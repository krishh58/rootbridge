import re
import random
import string
from flask import Blueprint, request, jsonify, make_response, g, current_app
from functools import wraps
import bcrypt
import jwt
import secrets
from datetime import datetime, timedelta, timezone
from .db import db
from .models import User, PasswordResetToken, TwoFactorCode, EmailVerification
from . import limiter

auth_bp = Blueprint('auth', __name__)


def create_token(user_id: int) -> str:
    payload = {
        'user_id': user_id,
        'exp': datetime.now(timezone.utc) + timedelta(days=30)
    }
    return jwt.encode(payload, current_app.config['SECRET_KEY'], algorithm='HS256')


GUEST_SEARCH_LIMIT = 5
GUEST_COOKIE = 'guest_searches'


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.cookies.get('auth_token')
        if token:
            try:
                payload = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
                g.user_id = payload['user_id']
                g.is_guest = False
                return f(*args, **kwargs)
            except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
                pass
        # Guest mode — allow limited searches
        used = int(request.cookies.get(GUEST_COOKIE, '0'))
        if used < GUEST_SEARCH_LIMIT:
            g.user_id = None
            g.is_guest = True
            resp = f(*args, **kwargs)
            if hasattr(resp, 'set_cookie'):
                resp.set_cookie(GUEST_COOKIE, str(used + 1), max_age=7*24*3600, samesite='Lax')
            else:
                from flask import make_response
                resp = make_response(resp)
                resp.set_cookie(GUEST_COOKIE, str(used + 1), max_age=7*24*3600, samesite='Lax')
            return resp
        return jsonify({'error': 'Authentication required', 'guest_limit': True, 'searches_used': used}), 401
    return decorated


def _base_url():
    return current_app.config.get('BASE_URL', 'https://rootbridge.app')


@auth_bp.get('/dev-login')
def dev_login():
    """Backdoor login for debugging — only works with correct key."""
    import os
    key = request.args.get('key', '')
    dev_key = os.environ.get('DEV_KEY', '')
    if not dev_key or key != dev_key:
        return jsonify({'error': 'Not found'}), 404
    user = User.query.filter_by(email='krishndrsn@gmail.com').first()
    if not user:
        user = User(
            email='krishndrsn@gmail.com',
            password_hash=bcrypt.hashpw(secrets.token_bytes(32), bcrypt.gensalt()).decode(),
            tier='heritage',
            token_balance=9999,
        )
        user.referral_code = 'RB-ADMIN000000'
        db.session.add(user)
        db.session.commit()
    token = create_token(user.id)
    resp = make_response('<script>window.location="/"</script>', 200)
    resp.set_cookie('auth_token', token, httponly=True, samesite='Lax', max_age=30*24*3600)
    return resp


@auth_bp.post('/auth/register')
@limiter.limit('10 per hour')
def register():
    data = request.get_json()
    if not data or not data.get('email') or not data.get('password'):
        return jsonify({'error': 'Email and password required'}), 400
    email = data['email'].strip().lower()
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        return jsonify({'error': 'Enter a valid email address'}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'Email already registered'}), 409
    if len(data['password']) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    password_hash = bcrypt.hashpw(data['password'].encode(), bcrypt.gensalt()).decode()
    user = User(email=email, password_hash=password_hash)
    user.referral_code = 'RB-' + secrets.token_hex(6).upper()
    db.session.add(user)

    ref_code = (data.get('ref') or '').strip().upper()
    if ref_code and re.fullmatch(r'RB-[0-9A-F]{12}', ref_code):
        referrer = User.query.filter_by(referral_code=ref_code).first()
        if referrer and referrer.email != email:
            referrer.token_balance += 25
            db.session.flush()
            user.referred_by_user_id = referrer.id

    db.session.commit()

    # Send verification email (non-blocking — skip if Gmail not configured)
    _send_verification(user)

    token = create_token(user.id)
    resp = make_response(jsonify({'id': user.id, 'email': user.email, 'tier': user.tier}), 201)
    resp.set_cookie('auth_token', token, httponly=True, samesite='Lax', max_age=30*24*3600)
    return resp


@auth_bp.post('/auth/login')
@limiter.limit('20 per hour')
def login():
    data = request.get_json()
    if not data or not data.get('email') or not data.get('password'):
        return jsonify({'error': 'Email and password required'}), 400
    user = User.query.filter_by(email=data['email'].strip().lower()).first()
    if not user or not bcrypt.checkpw(data['password'].encode(), user.password_hash.encode()):
        return jsonify({'error': 'Invalid credentials'}), 401

    if user.two_factor_enabled:
        code = ''.join(random.choices(string.digits, k=6))
        temp_token = secrets.token_hex(32)
        expires = datetime.now(timezone.utc) + timedelta(minutes=10)
        tfc = TwoFactorCode(user_id=user.id, code=code,
                            temp_token=temp_token, expires_at=expires)
        db.session.add(tfc)
        db.session.commit()
        from .mailer import send_2fa_email
        send_2fa_email(user.email, code)
        return jsonify({'requires_2fa': True, 'temp_token': temp_token}), 202

    token = create_token(user.id)
    resp = make_response(jsonify({'id': user.id, 'email': user.email, 'tier': user.tier}))
    resp.set_cookie('auth_token', token, httponly=True, samesite='Lax', max_age=30*24*3600)
    return resp


@auth_bp.get('/dev-login')
def dev_login():
    """Local dev only — bypasses password check. Never reachable on Railway (no DEV_LOGIN env var)."""
    import os
    if not os.environ.get('DEV_LOGIN'):
        from flask import abort
        abort(404)
    user = User.query.filter_by(email='krishndrsn@gmail.com').first()
    if not user:
        return 'No dev user found. Restart the server.', 404
    token = create_token(user.id)
    from flask import redirect
    from .models import Tree
    tree = Tree.query.filter_by(user_id=user.id).order_by(Tree.id.desc()).first()
    dest = f'/app?tree={tree.id}' if tree else '/app'
    resp = make_response(redirect(dest))
    resp.set_cookie('auth_token', token, httponly=True, samesite='Lax', max_age=30*24*3600)
    return resp


@auth_bp.post('/api/auth/verify-2fa')
def verify_2fa():
    data = request.get_json() or {}
    temp_token = data.get('temp_token', '')
    code = data.get('code', '').strip()
    if not temp_token or not code:
        return jsonify({'error': 'temp_token and code required'}), 400

    now = datetime.now(timezone.utc)
    tfc = TwoFactorCode.query.filter_by(temp_token=temp_token, used=False).first()
    if not tfc or tfc.expires_at.replace(tzinfo=timezone.utc) < now:
        return jsonify({'error': 'Code expired or invalid'}), 401
    if tfc.code != code:
        return jsonify({'error': 'Incorrect code'}), 401

    tfc.used = True
    db.session.commit()

    user = db.session.get(User, tfc.user_id)
    token = create_token(user.id)
    resp = make_response(jsonify({'id': user.id, 'email': user.email, 'tier': user.tier}))
    resp.set_cookie('auth_token', token, httponly=True, samesite='Lax', max_age=30*24*3600)
    return resp


@auth_bp.post('/auth/logout')
def logout():
    resp = make_response(jsonify({'ok': True}))
    resp.set_cookie('auth_token', '', httponly=True, expires=0)
    return resp


@auth_bp.post('/api/auth/forgot-password')
@limiter.limit('5 per hour')
def forgot_password():
    data = request.get_json() or {}
    email = data.get('email', '').strip().lower()
    # Always return 200 — don't reveal whether the email exists
    if not email:
        return jsonify({'ok': True})
    user = User.query.filter_by(email=email).first()
    if user:
        token = secrets.token_hex(32)
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        prt = PasswordResetToken(user_id=user.id, token=token, expires_at=expires)
        db.session.add(prt)
        db.session.commit()
        reset_url = f'{_base_url()}/reset-password?token={token}'
        from .mailer import send_password_reset_email
        send_password_reset_email(user.email, reset_url)
    return jsonify({'ok': True})


@auth_bp.post('/api/auth/reset-password')
def reset_password():
    data = request.get_json() or {}
    token = data.get('token', '').strip()
    new_password = data.get('password', '')
    if not token or not new_password:
        return jsonify({'error': 'token and password required'}), 400
    if len(new_password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    now = datetime.now(timezone.utc)
    prt = PasswordResetToken.query.filter_by(token=token, used=False).first()
    if not prt or prt.expires_at.replace(tzinfo=timezone.utc) < now:
        return jsonify({'error': 'Link expired or already used'}), 400

    user = db.session.get(User, prt.user_id)
    user.password_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    prt.used = True
    db.session.commit()
    return jsonify({'ok': True})


@auth_bp.get('/api/auth/verify-email')
def verify_email():
    token = request.args.get('token', '').strip()
    if not token:
        return jsonify({'error': 'Missing token'}), 400
    now = datetime.now(timezone.utc)
    ev = EmailVerification.query.filter_by(token=token).first()
    if not ev or ev.expires_at.replace(tzinfo=timezone.utc) < now:
        return jsonify({'error': 'Link expired or invalid'}), 400
    user = db.session.get(User, ev.user_id)
    user.email_verified = True
    db.session.delete(ev)
    db.session.commit()
    from flask import redirect
    return redirect('/verify-email?success=1')


@auth_bp.post('/api/auth/resend-verification')
@require_auth
def resend_verification():
    user = db.session.get(User, g.user_id)
    if user.email_verified:
        return jsonify({'ok': True, 'already_verified': True})
    _send_verification(user)
    return jsonify({'ok': True})


@auth_bp.post('/api/auth/2fa/enable')
@require_auth
def enable_2fa():
    user = db.session.get(User, g.user_id)
    user.two_factor_enabled = True
    db.session.commit()
    return jsonify({'ok': True, 'two_factor_enabled': True})


@auth_bp.post('/api/auth/2fa/disable')
@require_auth
def disable_2fa():
    data = request.get_json() or {}
    user = db.session.get(User, g.user_id)
    if not bcrypt.checkpw((data.get('password') or '').encode(), user.password_hash.encode()):
        return jsonify({'error': 'Incorrect password'}), 401
    user.two_factor_enabled = False
    db.session.commit()
    return jsonify({'ok': True, 'two_factor_enabled': False})


@auth_bp.post('/api/auth/discovery')
@require_auth
def set_discovery():
    data = request.get_json() or {}
    user = db.session.get(User, g.user_id)
    user.discovery_enabled = bool(data.get('discovery_enabled', True))
    db.session.commit()
    return jsonify({'discovery_enabled': user.discovery_enabled})


@auth_bp.get('/api/auth/me')
@require_auth
def get_me():
    user = db.session.get(User, g.user_id)
    return jsonify({
        'id': user.id,
        'email': user.email,
        'tier': user.tier,
        'token_balance': user.token_balance,
        'discovery_enabled': user.discovery_enabled,
        'referral_code': user.referral_code,
        'two_factor_enabled': user.two_factor_enabled,
        'email_verified': user.email_verified,
    })


@auth_bp.patch('/api/auth/me')
@require_auth
def update_me():
    data = request.get_json() or {}
    user = db.session.get(User, g.user_id)
    errors = {}

    new_email = data.get('email', '').strip().lower()
    if new_email and new_email != user.email:
        if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', new_email):
            errors['email'] = 'Enter a valid email address.'
        elif User.query.filter_by(email=new_email).first():
            errors['email'] = 'That email is already in use.'
        else:
            user.email = new_email
            user.email_verified = False
            _send_verification(user)

    new_password = data.get('password', '')
    if new_password:
        if len(new_password) < 8:
            errors['password'] = 'Password must be at least 8 characters.'
        else:
            current_password = data.get('current_password', '')
            if not current_password:
                errors['password'] = 'Enter your current password to set a new one.'
            elif not bcrypt.checkpw(current_password.encode(), user.password_hash.encode()):
                errors['current_password'] = 'Current password is incorrect.'
            else:
                user.password_hash = bcrypt.hashpw(
                    new_password.encode(), bcrypt.gensalt()).decode()

    if errors:
        return jsonify({'errors': errors}), 422

    db.session.commit()
    return jsonify({'ok': True, 'email': user.email})


def _send_verification(user):
    token = secrets.token_hex(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=24)
    ev = EmailVerification(user_id=user.id, token=token, expires_at=expires)
    db.session.add(ev)
    db.session.commit()
    verify_url = f'{_base_url()}/api/auth/verify-email?token={token}'
    from .mailer import send_verification_email
    send_verification_email(user.email, verify_url)
