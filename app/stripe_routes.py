import os
import stripe
from flask import Blueprint, request, jsonify, g, current_app
from .auth import require_auth
from .db import db
from .models import User

stripe_bp = Blueprint('stripe_routes', __name__)

TIER_PRICE_ENVS = {
    'us': 'STRIPE_PRICE_US',
    'european': 'STRIPE_PRICE_EUROPEAN',
    'aa': 'STRIPE_PRICE_AA',
    'asian': 'STRIPE_PRICE_ASIAN',
    'all': 'STRIPE_PRICE_ALL',
}

TOPUP_PRICE_ENVS = {
    '300': 'STRIPE_PRICE_TOPUP_300',
    '1000': 'STRIPE_PRICE_TOPUP_1000',
    '2500': 'STRIPE_PRICE_TOPUP_2500',
    '5000': 'STRIPE_PRICE_TOPUP_5000',
}

TOPUP_AMOUNTS = {'300': 300, '1000': 1000, '2500': 2500, '5000': 5000}


def _stripe_key():
    return current_app.config.get('STRIPE_SECRET_KEY', '')


@stripe_bp.post('/api/stripe/checkout')
@require_auth
def create_checkout():
    stripe.api_key = _stripe_key()
    data = request.get_json() or {}
    tier = data.get('tier', '')
    if tier not in TIER_PRICE_ENVS:
        return jsonify({'error': f'Invalid tier. Valid: {list(TIER_PRICE_ENVS.keys())}'}), 400
    price_id = os.environ.get(TIER_PRICE_ENVS[tier], 'price_test')
    user = User.query.get(g.user_id)
    session = stripe.checkout.Session.create(
        mode='subscription',
        line_items=[{'price': price_id, 'quantity': 1}],
        success_url=request.host_url + 'app?payment=success',
        cancel_url=request.host_url + 'pricing',
        customer_email=user.email,
        metadata={'user_id': str(g.user_id), 'tier': tier},
    )
    return jsonify({'url': session.url})


@stripe_bp.post('/api/stripe/topup')
@require_auth
def create_topup():
    stripe.api_key = _stripe_key()
    data = request.get_json() or {}
    pack = str(data.get('pack', ''))
    if pack not in TOPUP_PRICE_ENVS:
        return jsonify({'error': f'Invalid pack. Valid: {list(TOPUP_PRICE_ENVS.keys())}'}), 400
    price_id = os.environ.get(TOPUP_PRICE_ENVS[pack], 'price_test')
    user = User.query.get(g.user_id)
    session = stripe.checkout.Session.create(
        mode='payment',
        line_items=[{'price': price_id, 'quantity': 1}],
        success_url=request.host_url + 'app?payment=success',
        cancel_url=request.host_url + 'pricing',
        customer_email=user.email,
        metadata={'user_id': str(g.user_id), 'topup_tokens': pack},
    )
    return jsonify({'url': session.url})


@stripe_bp.get('/api/stripe/status')
@require_auth
def subscription_status():
    user = User.query.get(g.user_id)
    return jsonify({
        'tier': user.tier,
        'token_balance': user.token_balance,
        'token_balance_topup': user.token_balance_topup,
        'total_tokens': user.total_tokens(),
    })
