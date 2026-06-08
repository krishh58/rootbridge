import requests
from flask import Blueprint, request, jsonify, g, current_app
from .auth import require_auth
from .token_middleware import require_tokens
from .db import db
from .models import Person, Tree, AlfredMessage
from .alfred_curator import run_curation

alfred_bp = Blueprint('alfred', __name__)
OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'
ALFRED_MODEL = 'anthropic/claude-3-haiku'
HISTORY_LIMIT = 10

def _build_alfred_context(person: Person) -> str:
    name = f'{person.first_name or ""} {person.last_name or ""}'.strip()
    birth = f"{person.birth_year or 'unknown'}, {person.birth_state or person.birth_country or 'unknown location'}"
    death = f"{person.death_year or 'unknown'}, {person.death_place or 'unknown location'}"

    sources = []
    for r in person.search_results:
        raw = r.raw_data or {}
        # Build a rich record summary Alfred can actually reason about
        detail_parts = []
        for field in ('title', 'first_name', 'last_name', 'birth_year', 'birth_place',
                      'death_year', 'death_place', 'ship_name', 'arrival_date',
                      'port', 'notes', 'confidence', 'source_file'):
            val = raw.get(field)
            if val and str(val).strip() not in ('', 'None', '0'):
                detail_parts.append(f"{field}: {val}")
        detail = ' | '.join(detail_parts) if detail_parts else 'no detail available'

        # Confidence flag
        conf = raw.get('confidence') or raw.get('score', 0)
        try:
            conf_val = int(conf)
        except (TypeError, ValueError):
            conf_val = 0
        conf_flag = ' ⚠️ LOW CONFIDENCE' if conf_val and conf_val < 50 else ''

        source_line = f"- [{r.source}] {r.record_type or 'record'}{conf_flag}\n  {detail}"
        if r.url:
            source_line += f"\n  url: {r.url}"
        sources.append(source_line)

    gaps = []
    for gap in person.gaps:
        if not gap.resolved:
            gaps.append(f"- {gap.gap_type}: try {gap.suggested_source} — search: {gap.suggested_query}")

    tree = person.tree
    relatives = []
    if tree:
        for p in tree.persons:
            if p.id == person.id:
                continue
            rel = f"{p.first_name or ''} {p.last_name or ''} ({p.birth_year or '?'}–{p.death_year or '?'})"
            if person.id in (p.spouse_ids or []):
                relatives.append(f"Spouse: {rel}")
            elif p.id in (person.parent_ids or []):
                relatives.append(f"Parent: {rel}")
            elif person.id in (p.parent_ids or []):
                relatives.append(f"Child: {rel}")

    return f"""You are Alfred, a British genealogy research concierge — knowledgeable, precise, and direct. You guide researchers through the evidence rather than just presenting it.

SUBJECT: {name}
Born: {birth}
Died: {death}
Overall confidence: {person.confidence or 'unknown'}%

RECORDS FOUND ({len(person.search_results)}):
{chr(10).join(sources) or 'None yet.'}

OPEN RESEARCH GAPS:
{chr(10).join(gaps) or 'No open gaps.'}

FAMILY CONNECTIONS:
{chr(10).join(relatives) or 'No other persons in tree yet.'}

YOUR ROLE:
- Read the actual record details above — do not make up information not in the records
- When a record matches well, explain specifically WHY (matching birth year, place, name spelling)
- When a match has ⚠️ LOW CONFIDENCE or details that contradict the subject, say so clearly before the researcher acts on it
- If a GED source_file is listed, you have access to that file and can read it for deeper context
- Steer the researcher toward what the evidence supports; push back politely if they stray from the facts
- For ship manifest records, explain the historical context (what that ship/port/year means)
- For SSDI records, note what the state field tells us about where the person lived at end of life
- Suggest the logical next step based on what gaps remain
- Keep responses to 3-5 sentences unless a longer explanation is genuinely needed"""

@alfred_bp.get('/api/alfred/<int:person_id>/history')
@require_auth
def get_history(person_id):
    Person.query.join(Tree).filter(
        Person.id == person_id, Tree.user_id == g.user_id
    ).first_or_404()
    messages = AlfredMessage.query.filter_by(person_id=person_id).order_by(
        AlfredMessage.created_at.asc()
    ).limit(HISTORY_LIMIT * 2).all()
    return jsonify({'messages': [
        {'role': m.role, 'content': m.content, 'created_at': m.created_at.isoformat()}
        for m in messages
    ]})

@alfred_bp.post('/api/alfred/<int:person_id>/chat')
@require_auth
@require_tokens(10)
def chat(person_id):
    person = Person.query.join(Tree).filter(
        Person.id == person_id, Tree.user_id == g.user_id
    ).first_or_404()
    data = request.get_json() or {}
    user_message = data.get('message', '').strip()
    if not user_message:
        return jsonify({'error': 'Message is required'}), 400

    history = AlfredMessage.query.filter_by(person_id=person_id).order_by(
        AlfredMessage.created_at.desc()
    ).limit(HISTORY_LIMIT).all()
    history.reverse()

    system_context = _build_alfred_context(person)
    messages = [{'role': 'system', 'content': system_context}]
    for m in history:
        messages.append({'role': m.role, 'content': m.content})
    messages.append({'role': 'user', 'content': user_message})

    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                'Authorization': f"Bearer {current_app.config['OPENROUTER_API_KEY']}",
                'Content-Type': 'application/json',
            },
            json={'model': ALFRED_MODEL, 'messages': messages, 'max_tokens': 300},
            timeout=15,
        )
        resp.raise_for_status()
        choices = resp.json().get('choices', [])
        assistant_reply = choices[0]['message']['content'].strip() if choices else ''
    except Exception:
        assistant_reply = "I'm having trouble connecting at the moment. Please try again shortly."

    db.session.add(AlfredMessage(
        person_id=person_id, tree_id=person.tree_id,
        role='user', content=user_message
    ))
    db.session.add(AlfredMessage(
        person_id=person_id, tree_id=person.tree_id,
        role='assistant', content=assistant_reply
    ))
    db.session.commit()
    return jsonify({'response': assistant_reply})


@alfred_bp.post('/api/alfred/<int:person_id>/extend')
@require_auth
@require_tokens(25)
def extend_lineage(person_id):
    """
    Starting from a confirmed person, search backward generation by generation
    as far as records allow. Returns parent/grandparent candidates.

    Body (optional):
      { "max_generations": 3, "country_hint": "germany" }
    """
    person = Person.query.join(Tree).filter(
        Person.id == person_id, Tree.user_id == g.user_id
    ).first_or_404()

    data = request.get_json() or {}
    max_gen = min(int(data.get('max_generations', 3)), 5)
    country_hint = data.get('country_hint', '')

    person_dict = {
        'first_name':  person.first_name or '',
        'last_name':   person.last_name or '',
        'birth_year':  person.birth_year,
        'birth_state': person.birth_state or person.birth_country or '',
        'birth_place': person.birth_state or person.birth_country or '',
        'country_hint': country_hint,
    }

    try:
        from .search_cascade import extend_lineage as _extend
        tree = _extend(person_dict, max_generations=max_gen)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # Flatten for the response
    generations = []
    for gen_num in sorted(tree.keys()):
        for entry in tree[gen_num]:
            generations.append(entry)

    return jsonify({
        'person': f"{person.first_name or ''} {person.last_name or ''}".strip(),
        'person_id': person_id,
        'generations_searched': max_gen,
        'results': generations,
        'total_found': sum(
            (1 if e.get('father') else 0) + (1 if e.get('mother') else 0)
            for g_entries in tree.values()
            for e in g_entries
        ),
    })


@alfred_bp.post('/api/alfred/curate-gedcom')
@require_auth
def curate_gedcom():
    """Admin-only: run Alfred's AI curation agent to find new GEDCOM sources."""
    # Only allow seed user or user id=1
    if g.user_id != 1:
        return jsonify({'error': 'Admin only'}), 403

    data = request.get_json() or {}
    skip_crosscheck = data.get('skip_crosscheck', False)

    api_key = current_app.config.get('OPENROUTER_API_KEY', '')
    if not api_key:
        return jsonify({'error': 'OPENROUTER_API_KEY not configured'}), 500

    try:
        result = run_curation(api_key, skip_crosscheck=skip_crosscheck)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
