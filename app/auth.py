from flask import Blueprint, request, jsonify, make_response, g
from functools import wraps
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from .db import db
from .models import User
from flask import current_app

auth_bp = Blueprint('auth', __name__)

def create_token(user_id: int) -> str:
    payload = {
        'user_id': user_id,
        'exp': datetime.now(timezone.utc) + timedelta(days=30)
    }
    return jwt.encode(payload, current_app.config['SECRET_KEY'], algorithm='HS256')

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.cookies.get('auth_token')
        if not token:
            return jsonify({'error': 'Authentication required'}), 401
        try:
            payload = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
            g.user_id = payload['user_id']
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401
        return f(*args, **kwargs)
    return decorated

@auth_bp.post('/auth/register')
def register():
    data = request.get_json()
    if not data or not data.get('email') or not data.get('password'):
        return jsonify({'error': 'Email and password required'}), 400
    if User.query.filter_by(email=data['email']).first():
        return jsonify({'error': 'Email already registered'}), 409
    password_hash = bcrypt.hashpw(data['password'].encode(), bcrypt.gensalt()).decode()
    user = User(email=data['email'], password_hash=password_hash)
    db.session.add(user)
    db.session.commit()
    token = create_token(user.id)
    resp = make_response(jsonify({'id': user.id, 'email': user.email, 'tier': user.tier}), 201)
    resp.set_cookie('auth_token', token, httponly=True, samesite='Lax', max_age=30*24*3600)
    return resp

@auth_bp.post('/auth/login')
def login():
    data = request.get_json()
    if not data or not data.get('email') or not data.get('password'):
        return jsonify({'error': 'Email and password required'}), 400
    user = User.query.filter_by(email=data['email']).first()
    if not user or not bcrypt.checkpw(data['password'].encode(), user.password_hash.encode()):
        return jsonify({'error': 'Invalid credentials'}), 401
    token = create_token(user.id)
    resp = make_response(jsonify({'id': user.id, 'email': user.email, 'tier': user.tier}))
    resp.set_cookie('auth_token', token, httponly=True, samesite='Lax', max_age=30*24*3600)
    return resp

@auth_bp.post('/auth/logout')
def logout():
    resp = make_response(jsonify({'ok': True}))
    resp.set_cookie('auth_token', '', httponly=True, expires=0)
    return resp
