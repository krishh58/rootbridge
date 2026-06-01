from flask import Blueprint, request, jsonify, g
from .auth import require_auth
from .db import db
from .models import User, Person, PersonMatch, ResearchMessage
from .mailer import send_match_email

message_bp = Blueprint('message_routes', __name__)


def _get_match_and_check_access(match_id: int, user_id: int):
    m = PersonMatch.query.get_or_404(match_id)
    if m.user_a_id != user_id and m.user_b_id != user_id:
        return None, None, None
    is_a = m.user_a_id == user_id
    other_user_id = m.user_b_id if is_a else m.user_a_id
    their_person_id = m.person_b_id if is_a else m.person_a_id
    return m, other_user_id, their_person_id


@message_bp.get('/api/matches/<int:match_id>/messages')
@require_auth
def get_messages(match_id):
    m, other_user_id, _ = _get_match_and_check_access(match_id, g.user_id)
    if m is None:
        return jsonify({'error': 'Not found'}), 404

    unread = ResearchMessage.query.filter_by(
        match_id=match_id, read=False
    ).filter(ResearchMessage.sender_id == other_user_id).all()
    for msg in unread:
        msg.read = True
    db.session.commit()

    messages = ResearchMessage.query.filter_by(match_id=match_id).order_by(
        ResearchMessage.created_at.asc()
    ).all()
    return jsonify([
        {
            'id': msg.id,
            'sender_id': msg.sender_id,
            'body': msg.body,
            'read': msg.read,
            'created_at': msg.created_at.isoformat(),
            'is_mine': msg.sender_id == g.user_id,
        }
        for msg in messages
    ])


@message_bp.post('/api/matches/<int:match_id>/messages')
@require_auth
def send_message(match_id):
    m, other_user_id, their_person_id = _get_match_and_check_access(match_id, g.user_id)
    if m is None:
        return jsonify({'error': 'Not found'}), 404

    user = User.query.get(g.user_id)
    if user.tier == 'free':
        return jsonify({
            'error': 'Explorer subscription required to send messages.',
            'upgrade_url': '/pricing',
        }), 403

    data = request.get_json() or {}
    body = (data.get('body') or '').strip()
    if not body:
        return jsonify({'error': 'body is required'}), 400

    is_first_unread = ResearchMessage.query.filter_by(
        match_id=match_id, read=False
    ).filter(ResearchMessage.sender_id == g.user_id).count() == 0

    msg = ResearchMessage(match_id=match_id, sender_id=g.user_id, body=body)
    db.session.add(msg)
    db.session.commit()

    if is_first_unread:
        other_user = User.query.get(other_user_id)
        their_person = Person.query.get(their_person_id)
        ancestor_name = ''
        if their_person:
            ancestor_name = f"{their_person.first_name or ''} {their_person.last_name or ''}".strip()
            if their_person.birth_year:
                ancestor_name += f" ~{their_person.birth_year}"
        send_match_email(
            to_email=other_user.email,
            sender_display=user.get_display_name(),
            ancestor_name=ancestor_name,
        )

    return jsonify({
        'id': msg.id,
        'sender_id': msg.sender_id,
        'body': msg.body,
        'created_at': msg.created_at.isoformat(),
        'is_mine': True,
    }), 201


@message_bp.post('/api/matches/<int:match_id>/block')
@require_auth
def block_user(match_id):
    m, other_user_id, _ = _get_match_and_check_access(match_id, g.user_id)
    if m is None:
        return jsonify({'error': 'Not found'}), 404

    user = User.query.get(g.user_id)
    blocked = list(user.blocked_user_ids or [])
    if other_user_id not in blocked:
        blocked.append(other_user_id)
    user.blocked_user_ids = blocked

    db.session.delete(m)
    db.session.commit()
    return jsonify({'ok': True})
