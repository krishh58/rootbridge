import secrets
from datetime import datetime, timezone, timedelta
from flask import Blueprint, request, jsonify, g, render_template_string
from .auth import require_auth
from .db import db
from .models import User, Tree, TreeInvite, TreeCollaborator
from .mailer import send_invite_email

collab_bp = Blueprint('collab', __name__)

INVITE_TTL_DAYS = 7


def _assert_owner(tree_id: int):
    tree = Tree.query.get_or_404(tree_id)
    if tree.user_id != g.user_id:
        from flask import abort
        abort(403)
    return tree


@collab_bp.get('/api/me/referral')
@require_auth
def get_referral_info():
    user = User.query.get(g.user_id)
    referred_count = User.query.filter_by(referred_by_user_id=g.user_id).count()
    paid_subs = User.query.filter_by(referred_by_user_id=g.user_id, referral_reward_paid=True).count()
    tokens_earned = (referred_count * 25) + (paid_subs * 150)
    return jsonify({
        'referral_code': user.referral_code,
        'referral_url': f'/register?ref={user.referral_code}',
        'tokens_earned': tokens_earned,
    })


@collab_bp.post('/api/trees/<int:tree_id>/invite')
@require_auth
def create_invite(tree_id):
    tree = _assert_owner(tree_id)
    data = request.get_json() or {}
    role = data.get('role', 'viewer')
    if role not in ('viewer', 'editor'):
        return jsonify({'error': 'role must be viewer or editor'}), 400
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS)
    invite = TreeInvite(
        tree_id=tree_id, role=role,
        invite_token=token,
        email=data.get('email'),
        expires_at=expires_at,
    )
    db.session.add(invite)
    db.session.commit()

    invite_url = f'/invite/{token}'
    email = data.get('email')
    if email:
        owner = User.query.get(g.user_id)
        send_invite_email(
            to_email=email,
            inviter_name=owner.email,
            tree_name=tree.name,
            role=role,
            invite_url=invite_url,
        )

    return jsonify({'invite_url': invite_url, 'token': token,
                    'expires_at': expires_at.isoformat()}), 201


@collab_bp.get('/api/invite/<token>')
def preview_invite(token):
    invite = TreeInvite.query.filter_by(invite_token=token).first_or_404()
    if invite.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return jsonify({'error': 'Invite has expired'}), 410
    tree = Tree.query.get(invite.tree_id)
    owner = User.query.get(tree.user_id)
    return jsonify({
        'tree_name': tree.name,
        'inviter_name': owner.email,
        'role': invite.role,
        'expires_at': invite.expires_at.isoformat(),
    })


@collab_bp.post('/api/invite/<token>/accept')
@require_auth
def accept_invite(token):
    invite = TreeInvite.query.filter_by(invite_token=token).first_or_404()
    if invite.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return jsonify({'error': 'Invite has expired'}), 410
    if invite.claimed_by_user_id:
        return jsonify({'error': 'Invite already claimed'}), 400
    existing = TreeCollaborator.query.filter_by(
        tree_id=invite.tree_id, user_id=g.user_id
    ).first()
    if existing:
        return jsonify({'tree_id': invite.tree_id, 'role': existing.role})
    collab = TreeCollaborator(tree_id=invite.tree_id, user_id=g.user_id, role=invite.role)
    invite.claimed_by_user_id = g.user_id
    db.session.add(collab)
    db.session.commit()
    return jsonify({'tree_id': invite.tree_id, 'role': invite.role})


@collab_bp.get('/api/trees/<int:tree_id>/collaborators')
@require_auth
def list_collaborators(tree_id):
    _assert_owner(tree_id)
    collabs = TreeCollaborator.query.filter_by(tree_id=tree_id).all()
    result = []
    for c in collabs:
        u = User.query.get(c.user_id)
        result.append({
            'user_id': c.user_id,
            'email': u.email if u else '',
            'role': c.role,
            'joined_at': c.created_at.isoformat() if c.created_at else None,
        })
    return jsonify({'collaborators': result})


@collab_bp.delete('/api/trees/<int:tree_id>/collaborators/<int:user_id>')
@require_auth
def remove_collaborator(tree_id, user_id):
    _assert_owner(tree_id)
    collab = TreeCollaborator.query.filter_by(
        tree_id=tree_id, user_id=user_id
    ).first_or_404()
    db.session.delete(collab)
    db.session.commit()
    return '', 204


@collab_bp.get('/invite/<token>')
def invite_landing(token):
    invite = TreeInvite.query.filter_by(invite_token=token).first_or_404()
    expired = invite.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc)
    tree = Tree.query.get(invite.tree_id)
    owner = User.query.get(tree.user_id) if tree else None
    return render_template_string("""<!DOCTYPE html>
<html><head><title>RootBridge Invitation</title>
<link rel="stylesheet" href="/static/style.css">
</head><body>
<nav class="nav"><a href="/" class="nav-brand">RootBridge</a></nav>
<div style="max-width:480px;margin:4rem auto;padding:2rem;background:#1e293b;border-radius:12px;text-align:center">
  {% if expired %}
  <h2 style="color:#f87171">This invite has expired</h2>
  <p style="color:#94a3b8">Ask the tree owner to send a new invite link.</p>
  {% else %}
  <h2 style="color:#f8fafc">You've been invited!</h2>
  <p style="color:#94a3b8"><strong style="color:#f8fafc">{{ inviter }}</strong>
    invited you to collaborate on <strong style="color:#f8fafc">{{ tree_name }}</strong>
    as a <strong style="color:#60a5fa">{{ role }}</strong>.</p>
  <a href="/app" style="display:inline-block;margin-top:1rem;padding:.75rem 2rem;
    background:#2563eb;color:#fff;border-radius:8px;text-decoration:none;font-weight:600">
    Sign in to Accept
  </a>
  <p style="color:#64748b;font-size:.8rem;margin-top:1rem">
    After signing in, use <code>/api/invite/{{ token }}/accept</code> to claim your invite.
  </p>
  {% endif %}
</div>
</body></html>""",
        expired=expired,
        tree_name=tree.name if tree else 'Unknown Tree',
        inviter=owner.email if owner else 'Someone',
        role=invite.role,
        token=token,
    )
