from functools import wraps
from flask import jsonify, g
from .db import db
from .models import User

def require_tokens(cost: int):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user = User.query.get(g.user_id)
            if not user or user.total_tokens() < cost:
                return jsonify({
                    'error': f'Insufficient tokens. This action costs {cost} tokens.',
                    'tokens_required': cost,
                    'tokens_available': user.total_tokens() if user else 0,
                }), 402
            if not user.deduct_tokens(cost):
                return jsonify({'error': 'Token deduction failed'}), 402
            db.session.commit()
            return f(*args, **kwargs)
        return wrapper
    return decorator
