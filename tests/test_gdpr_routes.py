import json

def _login(client):
    client.post('/auth/register',
        data=json.dumps({'email': 'gdpr@test.com', 'password': 'pass'}),
        content_type='application/json')

def test_data_export_returns_json(client):
    _login(client)
    client.post('/api/persons',
        data=json.dumps({'tree_name': 'T', 'first_name': 'A', 'last_name': 'B'}),
        content_type='application/json')
    r = client.get('/api/me/export')
    assert r.status_code == 200
    data = r.get_json()
    assert 'account' in data
    assert data['account']['email'] == 'gdpr@test.com'
    assert 'trees' in data
    assert len(data['trees']) == 1

def test_account_deletion_removes_user(client, app):
    _login(client)
    r = client.delete('/api/me',
        data=json.dumps({'confirm': 'DELETE'}),
        content_type='application/json')
    assert r.status_code == 200
    from app.models import User
    with app.app_context():
        user = User.query.filter_by(email='gdpr@test.com').first()
        assert user is None

def test_account_deletion_requires_confirm(client):
    _login(client)
    r = client.delete('/api/me',
        data=json.dumps({'confirm': 'nope'}),
        content_type='application/json')
    assert r.status_code == 400
