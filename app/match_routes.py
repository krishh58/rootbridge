from flask import Blueprint, jsonify, g
from .auth import require_auth
from .db import db
from .models import User, Person, Tree, TreeCollaborator, PersonMatch, SearchResult, ResearchMessage

match_bp = Blueprint('match_routes', __name__)


def _can_access_person(person_id: int, user_id: int) -> bool:
    p = Person.query.get(person_id)
    if not p:
        return False
    if p.tree.user_id == user_id:
        return True
    return TreeCollaborator.query.filter_by(
        tree_id=p.tree_id, user_id=user_id
    ).first() is not None


def _match_for_user(match: PersonMatch, user_id: int) -> dict:
    is_a = match.user_a_id == user_id
    other_user_id = match.user_b_id if is_a else match.user_a_id
    my_person_id = match.person_a_id if is_a else match.person_b_id
    their_person_id = match.person_b_id if is_a else match.person_a_id

    other_user = User.query.get(other_user_id)
    display = other_user.get_display_name() if other_user else 'Unknown'

    my_records = SearchResult.query.filter_by(person_id=my_person_id).all()
    their_records = SearchResult.query.filter_by(person_id=their_person_id).all()

    their_urls = {r.url for r in their_records}
    my_urls = {r.url for r in my_records}

    unread_count = ResearchMessage.query.filter_by(
        match_id=match.id, read=False
    ).filter(ResearchMessage.sender_id != user_id).count()

    their_person = Person.query.get(their_person_id)
    ancestor_name = ''
    if their_person:
        ancestor_name = f"{their_person.first_name or ''} {their_person.last_name or ''}".strip()
        if their_person.birth_year:
            ancestor_name += f" ~{their_person.birth_year}"

    last_msg = ResearchMessage.query.filter_by(match_id=match.id).order_by(
        ResearchMessage.created_at.desc()
    ).first()
    last_message = (last_msg.body[:60] + '…') if last_msg and len(last_msg.body) > 60 else (last_msg.body if last_msg else '')

    return {
        'match_id': match.id,
        'score': match.score,
        'display_name': display,
        'ancestor_name': ancestor_name,
        'unread_count': unread_count,
        'last_message': last_message,
        'your_records': [
            {'source': r.source, 'record_type': r.record_type, 'url': r.url}
            for r in my_records
        ],
        'their_records': [
            {'source': r.source, 'record_type': r.record_type, 'url': r.url,
             'you_dont_have': r.url not in my_urls}
            for r in their_records
        ],
        'your_records_they_dont_have': [
            r.url for r in my_records if r.url not in their_urls
        ],
    }


@match_bp.get('/api/persons/<int:person_id>/matches')
@require_auth
def get_person_matches(person_id):
    if not _can_access_person(person_id, g.user_id):
        return jsonify({'error': 'Not found'}), 404
    matches = PersonMatch.query.filter(
        db.or_(
            PersonMatch.person_a_id == person_id,
            PersonMatch.person_b_id == person_id,
        )
    ).filter(
        db.or_(
            PersonMatch.user_a_id == g.user_id,
            PersonMatch.user_b_id == g.user_id,
        )
    ).all()
    user = User.query.get(g.user_id)
    blocked = user.blocked_user_ids if user else []
    result = []
    for m in matches:
        other_uid = m.user_b_id if m.user_a_id == g.user_id else m.user_a_id
        if other_uid in blocked:
            continue
        result.append(_match_for_user(m, g.user_id))
    return jsonify(result)


@match_bp.get('/api/matches')
@require_auth
def get_all_matches():
    if not g.user_id:
        return jsonify({'matches': [], 'total_unread': 0})
    matches = PersonMatch.query.filter(
        db.or_(
            PersonMatch.user_a_id == g.user_id,
            PersonMatch.user_b_id == g.user_id,
        )
    ).order_by(PersonMatch.created_at.desc()).all()
    user = User.query.get(g.user_id)
    blocked = user.blocked_user_ids if user else []
    result = []
    total_unread = 0
    for m in matches:
        other_uid = m.user_b_id if m.user_a_id == g.user_id else m.user_a_id
        if other_uid in blocked:
            continue
        d = _match_for_user(m, g.user_id)
        total_unread += d['unread_count']
        result.append(d)
    return jsonify({'matches': result, 'total_unread': total_unread})


@match_bp.post('/api/matches/<int:match_id>/seen')
@require_auth
def mark_seen(match_id):
    m = PersonMatch.query.get_or_404(match_id)
    if m.user_a_id != g.user_id and m.user_b_id != g.user_id:
        return jsonify({'error': 'Not found'}), 404
    if m.user_a_id == g.user_id:
        m.notified_a = True
    else:
        m.notified_b = True
    db.session.commit()
    return jsonify({'ok': True})
