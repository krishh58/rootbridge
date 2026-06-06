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
