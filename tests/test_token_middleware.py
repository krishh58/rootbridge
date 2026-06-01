import json
from unittest.mock import patch, MagicMock
from app.models import User

def _setup_user(client, email='tok@test.com'):
    client.post('/auth/register',
        data=json.dumps({'email': email, 'password': 'pass'}),
        content_type='application/json')

def test_token_deduction_on_alfred_chat(client, app):
    _setup_user(client)
    with app.app_context():
        user = User.query.filter_by(email='tok@test.com').first()
        initial_balance = user.total_tokens()

    r = client.post('/api/persons',
        data=json.dumps({'tree_name': 'T', 'first_name': 'J', 'last_name': 'D'}),
        content_type='application/json')
    person_id = r.get_json()['person_id']

    mock_resp = MagicMock()
    mock_resp.json.return_value = {'choices': [{'message': {'content': 'Answer.'}}]}
    mock_resp.raise_for_status = MagicMock()
    with patch('app.alfred_routes.requests.post', return_value=mock_resp):
        r2 = client.post(f'/api/alfred/{person_id}/chat',
            data=json.dumps({'message': 'Hello'}),
            content_type='application/json')
    assert r2.status_code == 200

    with app.app_context():
        user = User.query.filter_by(email='tok@test.com').first()
        assert user.total_tokens() == initial_balance - 10

def test_insufficient_tokens_blocks_alfred(client, app):
    _setup_user(client, 'low@test.com')
    with app.app_context():
        user = User.query.filter_by(email='low@test.com').first()
        user.token_balance = 5
        user.token_balance_topup = 0
        from app.db import db
        db.session.commit()

    r = client.post('/api/persons',
        data=json.dumps({'tree_name': 'T', 'first_name': 'J', 'last_name': 'D'}),
        content_type='application/json')
    person_id = r.get_json()['person_id']

    r2 = client.post(f'/api/alfred/{person_id}/chat',
        data=json.dumps({'message': 'Hello'}),
        content_type='application/json')
    assert r2.status_code == 402

def test_free_search_does_not_deduct_tokens(client, app):
    _setup_user(client, 'free@test.com')
    with app.app_context():
        user = User.query.filter_by(email='free@test.com').first()
        initial_balance = user.total_tokens()

    with patch('app.search_routes.run_us_cascade', return_value={'results':[],'gaps':[],'confidence':0}), \
         patch('app.search_routes.synthesize_gaps', return_value={'summary':''}):
        client.post('/search',
            data=json.dumps({'last': 'Doe'}),
            content_type='application/json')

    with app.app_context():
        user = User.query.filter_by(email='free@test.com').first()
        assert user.total_tokens() == initial_balance
