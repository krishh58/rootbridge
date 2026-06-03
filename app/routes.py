from flask import Blueprint, jsonify, g, send_from_directory, current_app, request
import os
from .auth import require_auth
from .db import db
from .models import User

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
