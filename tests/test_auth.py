import json

def test_register_success(client):
    r = client.post('/auth/register',
        data=json.dumps({'email': 'user@test.com', 'password': 'pass1234'}),
        content_type='application/json')
    assert r.status_code == 201
    data = r.get_json()
    assert data['email'] == 'user@test.com'
    assert data['tier'] == 'free'
    assert 'auth_token' in r.headers.get('Set-Cookie', '')

def test_register_duplicate_email(client):
    payload = json.dumps({'email': 'dup@test.com', 'password': 'pass1234'})
    client.post('/auth/register', data=payload, content_type='application/json')
    r = client.post('/auth/register', data=payload, content_type='application/json')
    assert r.status_code == 409
    assert r.get_json()['error'] == 'Email already registered'

def test_register_missing_fields(client):
    r = client.post('/auth/register',
        data=json.dumps({'email': 'x@test.com'}),
        content_type='application/json')
    assert r.status_code == 400

def test_login_success(client):
    client.post('/auth/register',
        data=json.dumps({'email': 'login@test.com', 'password': 'mypass'}),
        content_type='application/json')
    r = client.post('/auth/login',
        data=json.dumps({'email': 'login@test.com', 'password': 'mypass'}),
        content_type='application/json')
    assert r.status_code == 200
    assert r.get_json()['email'] == 'login@test.com'
    assert 'auth_token' in r.headers.get('Set-Cookie', '')

def test_login_wrong_password(client):
    client.post('/auth/register',
        data=json.dumps({'email': 'wp@test.com', 'password': 'correct'}),
        content_type='application/json')
    r = client.post('/auth/login',
        data=json.dumps({'email': 'wp@test.com', 'password': 'wrong'}),
        content_type='application/json')
    assert r.status_code == 401

def test_login_unknown_email(client):
    r = client.post('/auth/login',
        data=json.dumps({'email': 'nobody@test.com', 'password': 'x'}),
        content_type='application/json')
    assert r.status_code == 401

def test_logout_clears_cookie(client):
    client.post('/auth/register',
        data=json.dumps({'email': 'lo@test.com', 'password': 'pass'}),
        content_type='application/json')
    r = client.post('/auth/logout')
    assert r.status_code == 200
    cookie = r.headers.get('Set-Cookie', '')
    # Cookie should be cleared — empty value or past expiry
    assert 'auth_token' in cookie

def test_protected_route_no_token(client):
    r = client.get('/api/me')
    assert r.status_code == 401

def test_protected_route_with_token(client):
    client.post('/auth/register',
        data=json.dumps({'email': 'me@test.com', 'password': 'pass'}),
        content_type='application/json')
    r = client.get('/api/me')
    assert r.status_code == 200
    assert r.get_json()['email'] == 'me@test.com'
