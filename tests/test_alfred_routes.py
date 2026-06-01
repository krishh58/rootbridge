import json
from unittest.mock import patch, MagicMock

def _setup_person(client):
    client.post('/auth/register',
        data=json.dumps({'email': 'a@test.com', 'password': 'pass'}),
        content_type='application/json')
    r = client.post('/api/persons',
        data=json.dumps({'tree_name': 'T', 'first_name': 'John', 'last_name': 'Doe',
                         'birth_year': 1800, 'birth_state': 'Virginia'}),
        content_type='application/json')
    return r.get_json()['person_id']

def test_alfred_history_empty_initially(client):
    person_id = _setup_person(client)
    r = client.get(f'/api/alfred/{person_id}/history')
    assert r.status_code == 200
    assert r.get_json()['messages'] == []

def test_alfred_chat_returns_response(client):
    person_id = _setup_person(client)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        'choices': [{'message': {'content': 'I found a pension record for John Doe.'}}]
    }
    mock_resp.raise_for_status = MagicMock()
    with patch('app.alfred_routes.requests.post', return_value=mock_resp):
        r = client.post(f'/api/alfred/{person_id}/chat',
            data=json.dumps({'message': 'Where did you find the pension record?'}),
            content_type='application/json')
    assert r.status_code == 200
    data = r.get_json()
    assert 'response' in data
    assert data['response'] == 'I found a pension record for John Doe.'

def test_alfred_chat_saves_history(client):
    person_id = _setup_person(client)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {'choices': [{'message': {'content': 'Answer.'}}]}
    mock_resp.raise_for_status = MagicMock()
    with patch('app.alfred_routes.requests.post', return_value=mock_resp):
        client.post(f'/api/alfred/{person_id}/chat',
            data=json.dumps({'message': 'Hello Alfred'}),
            content_type='application/json')
    r = client.get(f'/api/alfred/{person_id}/history')
    messages = r.get_json()['messages']
    assert len(messages) == 2  # user message + assistant response
    assert messages[0]['role'] == 'user'
    assert messages[1]['role'] == 'assistant'

def test_alfred_chat_requires_message(client):
    person_id = _setup_person(client)
    r = client.post(f'/api/alfred/{person_id}/chat',
        data=json.dumps({}),
        content_type='application/json')
    assert r.status_code == 400
