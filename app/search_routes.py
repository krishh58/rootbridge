from flask import Blueprint, request, jsonify, g, Response, stream_with_context
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


def _save_search_to_db(user_id: int, tree_name: str, first: str, last: str,
                       birth_year: int, birth_place: str, cascade: dict,
                       summary: str) -> dict:
    tree = Tree.query.filter_by(user_id=user_id, name=tree_name).first()
    if not tree:
        tree = Tree(user_id=user_id, name=tree_name or f'{first} {last} Family')
        db.session.add(tree)
        db.session.flush()

    person = Person(
        tree_id=tree.id, first_name=first, last_name=last,
        birth_year=birth_year, birth_state=birth_place,
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

    for gap in cascade['gaps']:
        db.session.add(Gap(
            person_id=person.id, gap_type=gap['gap_type'],
            suggested_source=gap.get('suggested_source', ''),
            suggested_query=gap.get('suggested_query', ''),
        ))

    db.session.commit()
    return {'person_id': person.id, 'tree_id': tree.id}


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
@require_tokens(20)
def search_stream():
    """
    SSE endpoint — streams results as each source completes.
    Events: {source, results, count, total}  then final {done:true, results, gaps, summary, ...}
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
    birth_place= data.get('birth_place', '')
    tree_name  = data.get('tree_name', '')
    user_id    = g.user_id
    community  = _fetch_community_results(full_first, last, birth_year)

    def generate():
        all_results = []
        final_event = None

        for event_str in run_us_cascade_stream(
            full_first, last, birth_year, birth_place,
            community_results=community
        ):
            yield event_str
            # Track the last event so we can save after stream ends
            import json as _json
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
                from .search_cascade import run_us_cascade  # noqa (for _save_search_to_db context)
                ids = _save_search_to_db(
                    user_id, tree_name, full_first, last, birth_year, birth_place,
                    {'results': final_event.get('results', []),
                     'gaps':    final_event.get('gaps', []),
                     'confidence': final_event.get('confidence', 0)},
                    final_event.get('summary', ''),
                )
                import json as _json
                yield 'data: ' + _json.dumps({'saved': True, **ids}) + '\n\n'
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
            community_results=community
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
                # Update confidence and death info if improved
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
