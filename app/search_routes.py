import re
import datetime
from flask import Blueprint, request, jsonify, g, Response, stream_with_context, current_app
from .auth import require_auth
from .token_middleware import require_tokens
from .db import db, get_redis
from .models import User, Tree, Person, SearchResult, Gap
from .search_cascade import run_us_cascade, run_us_cascade_stream
from .ai_synthesis import synthesize_gaps, extract_ancestor_from_obit
from . import limiter

search_bp = Blueprint('search', __name__)
FREE_SEARCH_LIMIT = 5


def _fetch_community_results(first: str, last: str, birth_year: int) -> list:
    """Return SearchResult records from other users who already found this person."""
    from sqlalchemy import func
    try:
        q = Person.query.filter(func.lower(Person.last_name) == last.lower())
        if first:
            q = q.filter(func.lower(Person.first_name).like(f'{first[:3].lower()}%'))
        if birth_year:
            q = q.filter(Person.birth_year.between(birth_year - 10, birth_year + 10))
        persons = q.limit(15).all()
        results = []
        for person in persons:
            for sr in person.search_results:
                if sr.raw_data:
                    r = dict(sr.raw_data)
                    r['source'] = 'rootbridge_community'
                    r['community_source'] = sr.source
                    results.append(r)
        return results[:40]
    except Exception:
        return []


def _check_search_limit(key: str) -> bool:
    """Return True if the caller is under the lifetime limit (5 searches, never resets)."""
    try:
        r = get_redis()
        count = r.get(key)
        if count and int(count) >= FREE_SEARCH_LIMIT:
            return False
        r.incr(key)  # no expiry — counter is permanent
        return True
    except Exception:
        return True


def _enrich_person_from_results(person, results: list) -> None:
    """Backfill birth/death year and place from scraped records into the Person row."""
    for r in results:
        if not person.birth_year:
            if r.get('birth_year'):
                try: person.birth_year = int(r['birth_year'])
                except (ValueError, TypeError): pass
        if not person.birth_state and not person.birth_country:
            if r.get('birth_place'):
                person.birth_state = r['birth_place']
        if not person.death_year:
            if r.get('death_year'):
                try: person.death_year = int(r['death_year'])
                except (ValueError, TypeError): pass
            elif r.get('death_date'):
                try: person.death_year = int(str(r['death_date']).split('/')[-1])
                except (ValueError, IndexError): pass
        if not person.death_place:
            if r.get('death_place'):
                person.death_place = r['death_place']
            elif r.get('cemetery'):
                person.death_place = r['cemetery']


def _save_search_to_db(user_id: int, tree_name: str, first: str, last: str,
                       birth_year: int, birth_place: str, cascade: dict,
                       summary: str, death_year: int = None, death_place: str = '') -> dict:
    tree = Tree.query.filter_by(user_id=user_id, name=tree_name).first()
    if not tree:
        tree = Tree(user_id=user_id, name=tree_name or f'{first} {last} Family')
        db.session.add(tree)
        db.session.flush()

    person = Person(
        tree_id=tree.id, first_name=first, last_name=last,
        birth_year=birth_year, birth_state=birth_place,
        death_year=death_year, death_place=death_place or '',
        confidence=cascade['confidence'],
    )
    db.session.add(person)
    db.session.flush()

    for r in cascade['results']:
        db.session.add(SearchResult(
            person_id=person.id, source=r.get('source', ''),
            record_type=r.get('record_type', ''), url=r.get('url', ''),
            raw_data=r,
        ))

    _enrich_person_from_results(person, cascade['results'])

    for gap in cascade['gaps']:
        db.session.add(Gap(
            person_id=person.id, gap_type=gap['gap_type'],
            suggested_source=gap.get('suggested_source', ''),
            suggested_query=gap.get('suggested_query', ''),
        ))

    db.session.commit()
    return {'person_id': person.id, 'tree_id': tree.id}


def _cache_to_vault(first: str, last: str, birth_year: int, birth_place: str,
                    death_year: int, final_event: dict) -> None:
    """
    Save a high-confidence external search result back to the vault seed tree.
    This means the next person who searches the same ancestor gets a free vault hit.
    Only caches when confidence >= 60 to avoid polluting vault with noise.
    """
    try:
        confidence = final_event.get('confidence', 0)
        if confidence < 60:
            return
        from .match_index import search_vault, add_to_index
        # Don't cache if already in vault
        existing = search_vault(last, first_name=first, birth_year=birth_year, limit=3)
        top = max((h.get('score', 0) for h in existing), default=0)
        if top >= 70:
            return  # already well-represented

        seed_tree = Tree.query.filter_by(name='__vault_seed__').first()
        if not seed_tree:
            return

        parts = [x.strip() for x in (birth_place or '').split(',')]
        birth_state   = parts[-2][:50] if len(parts) >= 2 else None
        birth_country = parts[-1][:50] if parts else None

        person = Person(
            tree_id      = seed_tree.id,
            first_name   = (first or '')[:100],
            last_name    = (last  or '')[:100],
            birth_year   = birth_year,
            birth_state  = birth_state,
            birth_country= birth_country,
            death_year   = death_year,
            confidence   = confidence,
        )
        db.session.add(person)
        db.session.flush()
        from .match_index import update_person_in_index
        update_person_in_index(person.id, person.last_name, person.birth_year,
                               person.first_name, person.birth_state, person.birth_country,
                               seed_tree.id, seed_tree.user_id)
        db.session.commit()
    except Exception:
        pass


@search_bp.post('/search')
@limiter.limit('30 per hour')
def guest_search():
    data = request.get_json() or {}
    if not data.get('last'):
        return jsonify({'error': 'Last name is required'}), 400
    forwarded = request.headers.get('X-Forwarded-For', '')
    ip = forwarded.split(',')[0].strip() if forwarded else request.remote_addr
    if not _check_search_limit(f'guest_searches:{ip}'):
        return jsonify({'error': 'Free search limit reached. Subscribe to continue researching.'}), 429
    first = data.get('first', '')
    middle = data.get('middle', '')
    full_first = f'{first} {middle}'.strip() if middle else first
    last = data['last']
    birth_year = int(data['birth_year']) if data.get('birth_year') else None
    birth_place = data.get('birth_place', '')
    community = _fetch_community_results(full_first, last, birth_year)
    cascade = run_us_cascade(first=full_first, last=last, birth_year=birth_year,
                             birth_place=birth_place, community_results=community)
    synthesis = synthesize_gaps(
        person={'first_name': full_first, 'last_name': last, 'birth_year': birth_year, 'birth_state': birth_place},
        results=cascade['results'], gaps=cascade['gaps']
    )
    return jsonify({**cascade, 'summary': synthesis['summary']})


@search_bp.get('/api/vault/search')
@limiter.limit('120 per hour')
@require_auth
def vault_quick_search():
    """
    Free vault-only search — no token cost, instant results from local vault.
    Used for auto-populate as users type names in the tree builder.
    Returns up to 10 vault matches. Does NOT call any external sources.
    """
    last  = request.args.get('last', '').strip()
    first = request.args.get('first', '').strip()
    year  = request.args.get('year', '').strip()
    if not last:
        return jsonify({'results': [], 'vault_count': 0})
    birth_year = int(year) if year.isdigit() else None
    from .match_index import search_vault
    hits = search_vault(last, first_name=first, birth_year=birth_year, limit=10)
    return jsonify({
        'results': hits,
        'vault_count': len(hits),
        'source': 'vault',
    })


@search_bp.post('/api/search')
@limiter.limit('60 per hour')
@require_auth
@require_tokens(20)
def authenticated_search():
    data = request.get_json() or {}
    if not data.get('last'):
        return jsonify({'error': 'Last name is required'}), 400
    user = User.query.get(g.user_id)
    if user and user.tier == 'free':
        if not _check_search_limit(f'free_user_searches:{g.user_id}'):
            return jsonify({'error': 'Free search limit reached. Subscribe to continue researching.'}), 429
    first = data.get('first', '')
    middle = data.get('middle', '')
    full_first = f'{first} {middle}'.strip() if middle else first
    last = data['last']
    birth_year = int(data['birth_year']) if data.get('birth_year') else None
    birth_place = data.get('birth_place', '')
    tree_name = data.get('tree_name', '')
    community = _fetch_community_results(full_first, last, birth_year)
    cascade = run_us_cascade(first=full_first, last=last, birth_year=birth_year,
                             birth_place=birth_place, community_results=community)
    synthesis = synthesize_gaps(
        person={'first_name': full_first, 'last_name': last, 'birth_year': birth_year, 'birth_state': birth_place},
        results=cascade['results'], gaps=cascade['gaps']
    )
    ids = _save_search_to_db(g.user_id, tree_name, full_first, last, birth_year, birth_place, cascade, synthesis['summary'])
    return jsonify({**cascade, 'summary': synthesis['summary'], **ids})


@search_bp.get('/api/search/stream')
@limiter.limit('60 per hour')
@require_auth
def search_stream():
    """
    SSE endpoint — streams results as each source completes.
    Events: {source, results, count, total}  then final {done:true, results, gaps, summary, ...}

    Token model:
      - Vault hit only (score >= 60, >= 3 results): FREE — 0 tokens
      - External sources needed: 20 tokens charged before firing external calls
    """
    data = request.args
    if not data.get('last'):
        return jsonify({'error': 'Last name is required'}), 400

    user = User.query.get(g.user_id)
    if user and user.tier == 'free':
        if not _check_search_limit(f'free_user_searches:{g.user_id}'):
            return jsonify({'error': 'Free search limit reached. Subscribe to continue.'}), 429

    first      = data.get('first', '')
    middle     = data.get('middle', '')
    full_first = f'{first} {middle}'.strip() if middle else first
    last       = data['last']
    birth_year = int(data['birth_year']) if data.get('birth_year') else None
    death_year = int(data['death_year']) if data.get('death_year') else None
    birth_place= data.get('birth_place', '')
    death_place= data.get('death_place', '')
    tree_name  = data.get('tree_name', '')
    user_id    = g.user_id
    community  = _fetch_community_results(full_first, last, birth_year)

    def generate():
        import json as _json
        all_results = []
        final_event = None

        # ── Phase 0: vault search — always free ──────────────────────────────
        from .match_index import search_vault
        vault_hits = search_vault(last, first_name=full_first, birth_year=birth_year, limit=20)
        vault_results = [{
            'source': 'rootbridge_vault',
            'record_type': 'vault',
            'title': f"{h.get('first_name','')} {h.get('last_name','')}".strip(),
            'date': str(h.get('birth_year','')) if h.get('birth_year') else '',
            'location': f"{h.get('birth_state','')} {h.get('birth_country','')}".strip(),
            'url': '',
            'vault_score': h.get('score', 0),
        } for h in vault_hits]

        if vault_results:
            all_results.extend(vault_results)
            yield 'data: ' + _json.dumps({
                'source': 'rootbridge_vault',
                'results': vault_results,
                'count': len(vault_results),
                'total': len(all_results),
            }) + '\n\n'

        # Strong vault hit → return immediately, no token charge, no external calls
        top_score = max((h.get('score', 0) for h in vault_hits), default=0)
        if len(vault_hits) >= 3 and top_score >= 60:
            yield 'data: ' + _json.dumps({
                'done': True,
                'results': vault_results,
                'gaps': [],
                'summary': f'Found {len(vault_hits)} matching records in the RootBridge vault.',
                'confidence': min(top_score, 95),
                'vault_only': True,
                'tokens_charged': 0,
            }) + '\n\n'
            return

        # ── External sources needed — charge tokens now ───────────────────────
        current_user = User.query.get(user_id)
        if not current_user:
            yield 'data: ' + _json.dumps({'error': 'User not found'}), + '\n\n'
            return

        if current_user.tier != 'admin':
            if current_user.total_tokens() < 20:
                yield 'data: ' + _json.dumps({
                    'done': True,
                    'results': vault_results,
                    'gaps': [],
                    'summary': 'Vault search complete. Subscribe or top up tokens to search external records.',
                    'confidence': top_score,
                    'vault_only': True,
                    'tokens_charged': 0,
                    'token_gate': True,
                }) + '\n\n'
                return

            if not current_user.deduct_tokens(20):
                yield 'data: ' + _json.dumps({'error': 'Token deduction failed'}), + '\n\n'
                return
            db.session.commit()

        yield 'data: ' + _json.dumps({'tokens_charged': 0 if current_user.tier == 'admin' else 20}) + '\n\n'

        for event_str in run_us_cascade_stream(
            full_first, last, birth_year, birth_place,
            community_results=community,
            skip_vault=True,  # vault already searched above
            middle=middle,
            death_year=death_year,
        ):
            yield event_str
            try:
                ev = _json.loads(event_str.replace('data: ', '', 1).strip())
                if ev.get('done'):
                    final_event = ev
                elif ev.get('results'):
                    all_results.extend(ev['results'])
            except Exception:
                pass

        # Save to DB after stream completes
        if final_event:
            try:
                ids = _save_search_to_db(
                    user_id, tree_name, full_first, last, birth_year, birth_place,
                    {'results': final_event.get('results', []),
                     'gaps':    final_event.get('gaps', []),
                     'confidence': final_event.get('confidence', 0)},
                    final_event.get('summary', ''),
                    death_year=death_year, death_place=death_place,
                )
                yield 'data: ' + _json.dumps({'saved': True, **ids}) + '\n\n'

                # Cache high-confidence finds back to vault so future searches are free
                _cache_to_vault(full_first, last, birth_year, birth_place,
                                death_year, final_event)
            except Exception:
                pass

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'X-Accel-Buffering': 'no',
            'Cache-Control':     'no-cache',
            'Connection':        'keep-alive',
        },
    )


@search_bp.get('/api/persons/<int:person_id>/research')
@limiter.limit('30 per hour')
@require_auth
@require_tokens(20)
def research_person(person_id):
    """Re-run the search cascade for an existing person, merging new results in."""
    from .models import Person as PersonModel
    person = PersonModel.query.get(person_id)
    if not person:
        return jsonify({'error': 'Person not found'}), 404

    first       = person.first_name or ''
    last        = person.last_name  or ''
    birth_year  = person.birth_year
    birth_place = person.birth_state or person.birth_country or ''
    tree_id     = person.tree_id
    user_id     = g.user_id
    community   = _fetch_community_results(first, last, birth_year)

    def generate():
        import json as _json
        final_event = None

        for event_str in run_us_cascade_stream(
            first, last, birth_year, birth_place,
            community_results=community,
            middle=person.middle_name or '',
        ):
            yield event_str
            try:
                ev = _json.loads(event_str.replace('data: ', '', 1).strip())
                if ev.get('done'):
                    final_event = ev
            except Exception:
                pass

        if final_event:
            try:
                # Merge new results into existing person rather than creating a new one
                existing_urls = {sr.url for sr in person.search_results}
                new_count = 0
                for r in final_event.get('results', []):
                    if r.get('url') not in existing_urls:
                        db.session.add(SearchResult(
                            person_id=person_id,
                            source=r.get('source', ''),
                            record_type=r.get('record_type', ''),
                            url=r.get('url', ''),
                            raw_data=r,
                        ))
                        new_count += 1
                # Backfill any fields that were missing
                _enrich_person_from_results(person, final_event.get('results', []))
                if final_event.get('confidence', 0) > (person.confidence or 0):
                    person.confidence = final_event['confidence']
                db.session.commit()
                yield 'data: ' + _json.dumps({'saved': True, 'person_id': person_id, 'tree_id': tree_id, 'new_results': new_count}) + '\n\n'
            except Exception:
                pass

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-cache', 'Connection': 'keep-alive'},
    )


@search_bp.post('/api/reverse-search')
@limiter.limit('20 per hour')
@require_auth
@require_tokens(25)
def reverse_search():
    """
    Find an ancestor by searching for a known family member.
    Searches obituaries for the known person as a survivor, then extracts
    the ancestor's details using AI.
    """
    data = request.get_json() or {}
    known_first = data.get('known_first', '').strip()
    known_last = data.get('known_last', '').strip()
    known_location = data.get('known_location', '').strip()
    relationship = data.get('relationship', 'father').strip()

    if not known_last:
        return jsonify({'error': 'Last name of the known family member is required.'}), 400

    try:
        from .playwright_scrapers import search_by_descendant
        results = search_by_descendant(known_first, known_last, known_location, relationship)
    except Exception:
        results = []

    if not results:
        return jsonify({
            'found': False,
            'message': f"We couldn't find an obituary mentioning {known_first} {known_last} as a survivor. Try adding a location or checking the spelling."
        })

    best = results[0]
    ancestor = extract_ancestor_from_obit(
        obit_text=best.get('full_text', best.get('snippet', '')),
        known_person=f'{known_first} {known_last}',
        relationship=relationship,
        source_url=best['url'],
    )

    if ancestor.get('confidence') == 'none':
        return jsonify({
            'found': False,
            'message': ancestor.get('summary', 'No matching ancestor found in this obituary.')
        })

    return jsonify({
        'found': True,
        'ancestor': ancestor,
        'source_title': best['title'],
        'source_url': best['url'],
        'relationship': relationship,
        'known_person': f'{known_first} {known_last}',
    })


@search_bp.get('/api/persons/<int:person_id>/deep-research')
@limiter.limit('10 per hour')
@require_auth
@require_tokens(50)
def deep_research(person_id):
    """AI agent research — browses the web like a human genealogist."""
    from .models import Person as PersonModel
    person = PersonModel.query.get(person_id)
    if not person:
        return jsonify({'error': 'Person not found'}), 404

    name_parts  = (person.first_name or '').split()
    first       = name_parts[0] if name_parts else ''
    # Use dedicated middle_name column if present, else fall back to splitting first_name
    middle      = person.middle_name or (' '.join(name_parts[1:]) if len(name_parts) > 1 else '')
    last        = person.last_name  or ''
    birth_year  = person.birth_year
    # Expand 2-letter state abbreviations to full names so the agent can search effectively
    _STATE_NAMES = {
        'AL':'Alabama','AK':'Alaska','AZ':'Arizona','AR':'Arkansas','CA':'California',
        'CO':'Colorado','CT':'Connecticut','DE':'Delaware','FL':'Florida','GA':'Georgia',
        'HI':'Hawaii','ID':'Idaho','IL':'Illinois','IN':'Indiana','IA':'Iowa',
        'KS':'Kansas','KY':'Kentucky','LA':'Louisiana','ME':'Maine','MD':'Maryland',
        'MA':'Massachusetts','MI':'Michigan','MN':'Minnesota','MS':'Mississippi',
        'MO':'Missouri','MT':'Montana','NE':'Nebraska','NV':'Nevada','NH':'New Hampshire',
        'NJ':'New Jersey','NM':'New Mexico','NY':'New York','NC':'North Carolina',
        'ND':'North Dakota','OH':'Ohio','OK':'Oklahoma','OR':'Oregon','PA':'Pennsylvania',
        'RI':'Rhode Island','SC':'South Carolina','SD':'South Dakota','TN':'Tennessee',
        'TX':'Texas','UT':'Utah','VT':'Vermont','VA':'Virginia','WA':'Washington',
        'WV':'West Virginia','WI':'Wisconsin','WY':'Wyoming',
    }
    raw_place   = person.birth_state or person.birth_country or ''
    birth_place = _STATE_NAMES.get(raw_place.upper().strip(), raw_place)
    death_place = person.death_place or ''
    user_id     = g.user_id
    # Capture API key here — current_app not available inside thread
    api_key     = current_app.config.get('OPENROUTER_API_KEY', '')

    import json as _json

    def generate():
        import queue, threading

        q = queue.Queue()

        def agent_thread():
            from .research_agent import run_research_agent, run_living_research, LIVING_BIRTH_YEAR_THRESHOLD
            def put(event):
                q.put(event)
            try:
                is_living = (not person.death_year
                             and birth_year
                             and int(birth_year) >= LIVING_BIRTH_YEAR_THRESHOLD)
                if is_living:
                    run_living_research(first, last, birth_year, birth_place, put, middle)
                else:
                    run_research_agent(first, last, birth_year, birth_place, put, api_key, middle, death_place)
            except Exception as e:
                q.put({'type': 'error', 'message': str(e)})
            finally:
                q.put(None)  # sentinel

        t = threading.Thread(target=agent_thread, daemon=True)
        t.start()

        findings = []
        while True:
            event = q.get()
            if event is None:
                break
            yield 'data: ' + _json.dumps(event) + '\n\n'
            if event.get('type') == 'finding':
                findings.append(event)
            if event.get('type') == 'done':
                # Re-fetch person — original object may be detached after long agent run
                try:
                    from .models import Person as _P
                    p = _P.query.get(person_id)
                    if p:
                        field_map = {
                            'birth_year':  'birth_year',
                            'birth_place': 'birth_state',
                            'death_year':  'death_year',
                            'death_place': 'death_place',
                        }
                        filled = 0
                        for f in findings:
                            col = field_map.get(f['field'])
                            if not col:
                                continue
                            val = f['value']
                            # Skip non-values
                            if not val or str(val).lower() in ('unknown', 'none', 'n/a', ''):
                                continue
                            if col in ('birth_year', 'death_year'):
                                m = re.search(r'\d{4}', str(val))
                                if m:
                                    val = int(m.group())
                                else:
                                    continue
                            # Only fill in missing fields
                            if not getattr(p, col):
                                setattr(p, col, val)
                                filled += 1
                        # Bump confidence for each newly filled field
                        if filled:
                            p.confidence = min(95, (p.confidence or 0) + filled * 10)
                        # Save all findings + a run summary for persistent display
                        run_summary = {
                            'type': 'run_summary',
                            'text': event.get('summary', ''),
                            'ran_at': datetime.datetime.utcnow().isoformat()
                        }
                        tagged_findings = [dict(f, type='finding') for f in findings]
                        p.research_findings = [run_summary] + tagged_findings
                        db.session.commit()
                    yield 'data: ' + _json.dumps({'type': 'saved', 'person_id': person_id}) + '\n\n'
                except Exception as _e:
                    logger.error('deep-research save failed: %s', _e)
                    yield 'data: ' + _json.dumps({'type': 'saved', 'person_id': person_id}) + '\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-cache', 'Connection': 'keep-alive'},
    )


def _parse_full_name(raw: str):
    """Parse 'George L. Henderson' → (first, middle, last)."""
    raw = re.sub(r'^(father|mother|parent|spouse|wife|husband)\s*[:\-]\s*', '', raw, flags=re.IGNORECASE).strip()
    raw = re.sub(r'\(.*?\)', '', raw).strip()
    parts = raw.split()
    if not parts:
        return '', '', ''
    if len(parts) == 1:
        return parts[0], '', ''
    if len(parts) == 2:
        return parts[0], '', parts[1]
    return parts[0], ' '.join(parts[1:-1]), parts[-1]


@search_bp.post('/api/persons/<int:person_id>/expand')
@require_auth
def expand_tree(person_id):
    """Create parent/spouse persons from research_findings and link them to this person."""
    from .models import Person as _P

    child = _P.query.get(person_id)
    if not child:
        return jsonify({'error': 'Person not found'}), 404

    data = request.get_json(silent=True) or {}
    findings = data.get('findings') or child.research_findings or []

    created = []
    new_parent_ids = list(child.parent_ids or [])
    new_spouse_ids = list(child.spouse_ids or [])

    for f in findings:
        field = f.get('field', '')
        if field not in ('parent', 'spouse'):
            continue
        raw_name = (f.get('value') or '').strip()
        if not raw_name:
            continue

        first, middle, last = _parse_full_name(raw_name)
        if not last:
            continue

        already = any(p.get('first_name') == first and p.get('last_name') == last for p in created)
        if already:
            continue

        exists = _P.query.filter_by(tree_id=child.tree_id, first_name=first, last_name=last).first()
        if exists:
            new_id = exists.id
        else:
            new_person = _P(
                tree_id=child.tree_id,
                first_name=first,
                middle_name=middle or None,
                last_name=last,
                confidence=20,
            )
            db.session.add(new_person)
            db.session.flush()
            new_id = new_person.id

        if field == 'parent' and new_id not in new_parent_ids:
            new_parent_ids.append(new_id)
        elif field == 'spouse' and new_id not in new_spouse_ids:
            new_spouse_ids.append(new_id)

        created.append({'id': new_id, 'field': field,
                        'first_name': first, 'middle_name': middle, 'last_name': last})

    child.parent_ids = new_parent_ids
    child.spouse_ids = new_spouse_ids
    child.research_findings = []
    db.session.commit()

    return jsonify({'created': created, 'person_id': person_id})


# ── Dev/test search — no auth, no tokens, local use only ─────────────────────

@search_bp.get('/api/dev/search/stream')
def dev_search_stream():
    """
    Auth-free SSE search endpoint for local testing.
    Calls the full cascade including OpenRouter synthesis.
    NOT for production use — no auth, no token deduction.
    """
    import os
    if os.environ.get('RAILWAY_ENVIRONMENT'):
        return jsonify({'error': 'Dev endpoint disabled in production'}), 403

    data = request.args
    first      = data.get('first', '')
    last       = data.get('last', '')
    birth_year = int(data['birth_year']) if data.get('birth_year') else None
    birth_place= data.get('birth_place', '')
    death_year = int(data['death_year']) if data.get('death_year') else None
    death_place= data.get('death_place', '')

    if not last:
        return jsonify({'error': 'Last name required'}), 400

    def generate():
        for event_str in run_us_cascade_stream(
            first, last, birth_year, birth_place,
            skip_vault=False,
            middle=data.get('middle', ''),
        ):
            yield event_str

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-cache', 'Connection': 'keep-alive'},
    )
