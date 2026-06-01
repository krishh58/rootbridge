import json
from unittest.mock import patch

MOCK_CASCADE = {
    'results': [{'name': 'John Doe', 'source': 'familysearch', 'url': 'http://fs.org/1'}],
    'gaps': [{'gap_type': 'missing_parents', 'suggested_source': 'FamilySearch',
              'suggested_query': 'John Doe parents'}],
    'confidence': 45,
}
MOCK_SYNTHESIS = {'summary': 'Found one record. Parents unknown.'}


def test_guest_search_success(client):
    with patch('app.search_routes.run_us_cascade', return_value=MOCK_CASCADE), \
         patch('app.search_routes.synthesize_gaps', return_value=MOCK_SYNTHESIS):
        r = client.post('/search',
            data=json.dumps({'last': 'Doe', 'first': 'John', 'birth_year': 1800}),
            content_type='application/json')
    assert r.status_code == 200
    data = r.get_json()
    assert 'results' in data
    assert 'gaps' in data
    assert 'summary' in data
    assert data['confidence'] == 45


def test_guest_search_requires_last_name(client):
    r = client.post('/search',
        data=json.dumps({'first': 'John'}),
        content_type='application/json')
    assert r.status_code == 400


def test_guest_search_rate_limited(client):
    with patch('app.search_routes.run_us_cascade', return_value=MOCK_CASCADE), \
         patch('app.search_routes.synthesize_gaps', return_value=MOCK_SYNTHESIS), \
         patch('app.search_routes._check_guest_rate_limit', return_value=False):
        r = client.post('/search',
            data=json.dumps({'last': 'Doe'}),
            content_type='application/json')
    assert r.status_code == 429


def test_authenticated_search_saves_to_db(client):
    client.post('/auth/register',
        data=json.dumps({'email': 's@test.com', 'password': 'pass'}),
        content_type='application/json')
    with patch('app.search_routes.run_us_cascade', return_value=MOCK_CASCADE), \
         patch('app.search_routes.synthesize_gaps', return_value=MOCK_SYNTHESIS):
        r = client.post('/api/search',
            data=json.dumps({'last': 'Doe', 'first': 'John', 'birth_year': 1800,
                             'tree_name': 'My Tree'}),
            content_type='application/json')
    assert r.status_code == 200
    data = r.get_json()
    assert 'person_id' in data
    assert 'tree_id' in data
