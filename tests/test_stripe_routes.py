import json
from unittest.mock import patch, MagicMock

def _login(client):
    client.post('/auth/register',
        data=json.dumps({'email': 's@test.com', 'password': 'pass'}),
        content_type='application/json')

def test_create_checkout_subscription(client):
    _login(client)
    mock_session = MagicMock()
    mock_session.url = 'https://checkout.stripe.com/test-session'
    with patch('app.stripe_routes.stripe.checkout.Session.create', return_value=mock_session):
        r = client.post('/api/stripe/checkout',
            data=json.dumps({'tier': 'us'}),
            content_type='application/json')
    assert r.status_code == 200
    assert 'url' in r.get_json()

def test_create_checkout_invalid_tier(client):
    _login(client)
    r = client.post('/api/stripe/checkout',
        data=json.dumps({'tier': 'invalid_tier'}),
        content_type='application/json')
    assert r.status_code == 400

def test_create_topup_checkout(client):
    _login(client)
    mock_session = MagicMock()
    mock_session.url = 'https://checkout.stripe.com/topup-session'
    with patch('app.stripe_routes.stripe.checkout.Session.create', return_value=mock_session):
        r = client.post('/api/stripe/topup',
            data=json.dumps({'pack': '1000'}),
            content_type='application/json')
    assert r.status_code == 200
    assert 'url' in r.get_json()

def test_create_topup_invalid_pack(client):
    _login(client)
    r = client.post('/api/stripe/topup',
        data=json.dumps({'pack': '9999'}),
        content_type='application/json')
    assert r.status_code == 400

def test_get_subscription_status(client):
    _login(client)
    r = client.get('/api/stripe/status')
    assert r.status_code == 200
    data = r.get_json()
    assert 'tier' in data
    assert 'token_balance' in data
    assert 'token_balance_topup' in data

import time

def _make_webhook_event(event_type, metadata, mode='subscription'):
    payload = {
        'id': 'evt_test', 'type': event_type, 'created': int(time.time()),
        'data': {'object': {
            'id': 'cs_test', 'object': 'checkout.session',
            'mode': mode,
            'metadata': metadata,
            'subscription': 'sub_test' if mode == 'subscription' else None,
            'customer': 'cus_test',
        }},
        'object': 'event',
        'api_version': '2024-04-10',
    }
    return payload

def test_webhook_subscription_created(client, app):
    client.post('/auth/register',
        data=json.dumps({'email': 'w@test.com', 'password': 'pass'}),
        content_type='application/json')
    from app.models import User
    from app.db import db
    with app.app_context():
        user = User.query.filter_by(email='w@test.com').first()
        user_id = user.id

    payload = _make_webhook_event(
        'checkout.session.completed',
        {'user_id': str(user_id), 'tier': 'us'},
        mode='subscription'
    )

    class FakeObj:
        def __init__(self, d):
            self.__dict__.update(d)
        def __getattr__(self, k):
            return self.__dict__.get(k)

    fake_event = FakeObj({
        'type': payload['type'],
        'data': FakeObj({'object': FakeObj({**payload['data']['object'], 'metadata': payload['data']['object']['metadata']})})
    })

    with patch('app.stripe_routes.stripe.Webhook.construct_event', return_value=fake_event):
        r = client.post('/webhook/stripe',
            data=json.dumps(payload),
            content_type='application/json',
            headers={'Stripe-Signature': 'test'})
    assert r.status_code == 200
    with app.app_context():
        user = User.query.filter_by(email='w@test.com').first()
        assert user.tier == 'us'
        assert user.token_balance == 500

def test_webhook_topup_completed(client, app):
    client.post('/auth/register',
        data=json.dumps({'email': 'tu@test.com', 'password': 'pass'}),
        content_type='application/json')
    from app.models import User
    with app.app_context():
        user = User.query.filter_by(email='tu@test.com').first()
        user_id = user.id
        initial_topup = user.token_balance_topup

    payload = _make_webhook_event(
        'checkout.session.completed',
        {'user_id': str(user_id), 'topup_tokens': '1000'},
        mode='payment'
    )

    class FakeObj:
        def __init__(self, d):
            self.__dict__.update(d)
        def __getattr__(self, k):
            return self.__dict__.get(k)

    fake_event = FakeObj({
        'type': payload['type'],
        'data': FakeObj({'object': FakeObj({**payload['data']['object'], 'metadata': payload['data']['object']['metadata']})})
    })

    with patch('app.stripe_routes.stripe.Webhook.construct_event', return_value=fake_event):
        r = client.post('/webhook/stripe',
            data=json.dumps(payload),
            content_type='application/json',
            headers={'Stripe-Signature': 'test'})
    assert r.status_code == 200
    with app.app_context():
        user = User.query.filter_by(email='tu@test.com').first()
        assert user.token_balance_topup == initial_topup + 1000
