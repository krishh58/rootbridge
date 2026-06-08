from flask import Blueprint, jsonify, g, send_from_directory, current_app, request
import os, json
from pathlib import Path
from .auth import require_auth
from .db import db
from .models import User, Person, Tree

routes_bp = Blueprint('routes', __name__)

@routes_bp.get('/health')
def health():
    return jsonify({'status': 'ok'})

@routes_bp.post('/api/admin/rescan')
def trigger_rescan():
    secret = os.environ.get('ADMIN_SECRET', '')
    if not secret or request.headers.get('X-Admin-Secret') != secret:
        return jsonify({'error': 'Forbidden'}), 403
    from .rescan import run_monthly_rescan
    run_monthly_rescan()
    return jsonify({'ok': True})

@routes_bp.post('/api/admin/set-tier')
def set_tier():
    secret = os.environ.get('ADMIN_SECRET', '')
    if not secret or request.headers.get('X-Admin-Secret') != secret:
        return jsonify({'error': 'Forbidden'}), 403
    data = request.get_json() or {}
    email = data.get('email', '').strip()
    tier = data.get('tier', 'admin').strip()
    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({'error': f'User {email} not found'}), 404
    user.tier = tier
    if tier == 'admin':
        user.token_balance = 999999
    db.session.commit()
    return jsonify({'ok': True, 'email': user.email, 'tier': user.tier, 'tokens': user.token_balance})

@routes_bp.post('/api/admin/reset-user')
def reset_user():
    secret = os.environ.get('ADMIN_SECRET', '')
    if not secret or request.headers.get('X-Admin-Secret') != secret:
        return jsonify({'error': 'Forbidden'}), 403
    data = request.get_json() or {}
    email = data.get('email', '').strip()
    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({'error': f'User {email} not found'}), 404
    from sqlalchemy import text as _t
    uid = user.id
    # Delete in dependency order to avoid FK violations
    for stmt in [
        'DELETE FROM alfred_messages WHERE tree_id IN (SELECT id FROM trees WHERE user_id=:u)',
        'DELETE FROM gaps WHERE person_id IN (SELECT id FROM persons WHERE tree_id IN (SELECT id FROM trees WHERE user_id=:u))',
        'DELETE FROM search_results WHERE person_id IN (SELECT id FROM persons WHERE tree_id IN (SELECT id FROM trees WHERE user_id=:u))',
        'DELETE FROM research_findings WHERE person_id IN (SELECT id FROM persons WHERE tree_id IN (SELECT id FROM trees WHERE user_id=:u))',
        'DELETE FROM persons WHERE tree_id IN (SELECT id FROM trees WHERE user_id=:u)',
        'DELETE FROM trees WHERE user_id=:u',
    ]:
        try:
            db.session.execute(_t(stmt), {'u': uid})
        except Exception:
            db.session.rollback()
    db.session.commit()
    return jsonify({'ok': True, 'email': email})

@routes_bp.get('/api/me')
@require_auth
def me():
    user = db.session.get(User, g.user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    return jsonify({
        'id': user.id,
        'email': user.email,
        'tier': user.tier,
        'tokens': user.total_tokens(),
        'discovery_enabled': user.discovery_enabled,
    })

@routes_bp.get('/')
def index():
    return send_from_directory(os.path.join(current_app.root_path, '..', 'static'), 'index.html')

@routes_bp.get('/pricing')
def pricing():
    return send_from_directory(os.path.join(current_app.root_path, '..', 'static'), 'pricing.html')

@routes_bp.get('/app')
def app_shell():
    return send_from_directory(os.path.join(current_app.root_path, '..', 'static'), 'app.html')

@routes_bp.get('/account')
def account():
    return send_from_directory(os.path.join(current_app.root_path, '..', 'static'), 'account.html')

@routes_bp.get('/reset-password')
def reset_password_page():
    return send_from_directory(os.path.join(current_app.root_path, '..', 'static'), 'reset-password.html')

@routes_bp.get('/verify-email')
def verify_email_page():
    return send_from_directory(os.path.join(current_app.root_path, '..', 'static'), 'verify-email.html')

@routes_bp.get('/terms')
def terms():
    return send_from_directory(os.path.join(current_app.root_path, '..', 'static'), 'terms.html')

@routes_bp.get('/privacy')
def privacy():
    return send_from_directory(os.path.join(current_app.root_path, '..', 'static'), 'privacy.html')

@routes_bp.get('/api/seed/stats')
@require_auth
def seed_stats():
    from .match_index import index_stats
    index_path = Path(current_app.root_path).parent / 'data' / 'ged_index.json'
    index_count = 0
    if index_path.exists():
        try:
            index_count = len(json.loads(index_path.read_text()))
        except Exception:
            pass
    seed_tree = Tree.query.filter_by(name='GEDCOM Seed Data').first()
    seed_persons = Person.query.filter_by(tree_id=seed_tree.id).count() if seed_tree else 0
    return jsonify({
        'index_count': index_count,
        'seed_persons': seed_persons,
        'match_index': index_stats(),
    })


@routes_bp.post('/api/admin/rebuild-index')
@require_auth
def rebuild_index():
    if g.user_id != 1:
        return jsonify({'error': 'Admin only'}), 403
    from .match_index import build_hot_index
    count = build_hot_index(current_app._get_current_object())
    from .match_index import index_stats
    return jsonify({'rebuilt': True, 'indexed': count, 'stats': index_stats()})
