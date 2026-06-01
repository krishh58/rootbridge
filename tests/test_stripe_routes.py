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
