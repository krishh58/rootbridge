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
        sources.append(f"- {r.source} ({r.record_type or 'record'}): {r.url}")

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

    return f"""You are Alfred, a British genealogy research concierge with the personality of a knowledgeable butler. You have complete access to all research for {name}.

PERSON: {name}
Born: {birth}
Died: {death}
Confidence score: {person.confidence}%

RECORDS FOUND ({len(person.search_results)}):
{chr(10).join(sources) or 'None yet.'}

OPEN RESEARCH GAPS:
{chr(10).join(gaps) or 'No gaps — research appears complete.'}

FAMILY TREE CONNECTIONS:
{chr(10).join(relatives) or 'No other persons in tree yet.'}

Answer questions about this person's research directly and specifically. If asked to show a picture, describe what image would be shown and from where. If asked to translate something, do so. Always cite specific sources when available. Keep responses concise — 2-4 sentences unless more detail is needed."""

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
