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


@stripe_bp.post('/webhook/stripe')
def stripe_webhook():
    stripe.api_key = _stripe_key()
    payload = request.get_data(as_text=True)
    sig = request.headers.get('Stripe-Signature', '')
    webhook_secret = current_app.config.get('STRIPE_WEBHOOK_SECRET', '')
    try:
        event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
    except Exception:
        return jsonify({'error': 'Invalid signature'}), 400

    if event.type == 'checkout.session.completed':
        obj = event.data.object
        meta = obj.metadata or {}
        user_id = meta.get('user_id')
        if not user_id:
            return jsonify({'received': True})
        user = User.query.get(int(user_id))
        if not user:
            return jsonify({'received': True})

        if obj.mode == 'subscription' and meta.get('tier'):
            new_tier = meta['tier']
            user.tier = new_tier
            user.token_balance = User.TIER_TOKENS.get(new_tier, 0)
            user.stripe_customer_id = obj.customer
            user.stripe_subscription_id = obj.subscription
            db.session.commit()

        elif obj.mode == 'payment' and meta.get('topup_tokens'):
            tokens = TOPUP_AMOUNTS.get(str(meta['topup_tokens']), 0)
            user.token_balance_topup += tokens
            db.session.commit()

    elif event.type == 'customer.subscription.created':
        obj = event.data.object
        user = User.query.filter_by(stripe_customer_id=obj.customer).first()
        if user and user.referred_by_user_id:
            referrer = User.query.get(user.referred_by_user_id)
            if referrer:
                referrer.token_balance += 150
                db.session.commit()

    elif event.type == 'customer.subscription.deleted':
        obj = event.data.object
        user = User.query.filter_by(stripe_subscription_id=obj.id).first()
        if user:
            user.tier = 'free'
            user.token_balance = User.TIER_TOKENS.get('free', 50)
            user.stripe_subscription_id = None
            db.session.commit()

    return jsonify({'received': True})
