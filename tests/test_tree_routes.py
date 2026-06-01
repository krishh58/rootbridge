import json

def _register(client):
    client.post('/auth/register',
        data=json.dumps({'email': 't@test.com', 'password': 'pass'}),
        content_type='application/json')
    return client

def test_get_tree_not_found(client):
    _register(client)
    r = client.get('/api/trees/9999')
    assert r.status_code == 404

def test_create_person_and_get_tree(client):
    _register(client)
    r = client.post('/api/persons',
        data=json.dumps({'tree_name': 'My Tree', 'first_name': 'Jane',
                         'last_name': 'Doe', 'birth_year': 1820}),
        content_type='application/json')
    assert r.status_code == 201
    data = r.get_json()
    tree_id = data['tree_id']
    person_id = data['person_id']

    r2 = client.get(f'/api/trees/{tree_id}')
    assert r2.status_code == 200
    tree_data = r2.get_json()
    assert tree_data['id'] == tree_id
    assert len(tree_data['persons']) == 1
    assert tree_data['persons'][0]['id'] == person_id

def test_get_person_detail(client):
    _register(client)
    r = client.post('/api/persons',
        data=json.dumps({'tree_name': 'T', 'first_name': 'Bob', 'last_name': 'Smith'}),
        content_type='application/json')
    person_id = r.get_json()['person_id']
    r2 = client.get(f'/api/persons/{person_id}')
    assert r2.status_code == 200
    p = r2.get_json()
    assert p['first_name'] == 'Bob'
    assert 'gaps' in p
    assert 'search_results' in p

def test_update_person(client):
    _register(client)
    r = client.post('/api/persons',
        data=json.dumps({'tree_name': 'T', 'first_name': 'Bob', 'last_name': 'Smith'}),
        content_type='application/json')
    person_id = r.get_json()['person_id']
    r2 = client.put(f'/api/persons/{person_id}',
        data=json.dumps({'birth_year': 1850, 'birth_state': 'Ohio'}),
        content_type='application/json')
    assert r2.status_code == 200
    p = r2.get_json()
    assert p['birth_year'] == 1850
    assert p['birth_state'] == 'Ohio'

def test_delete_person(client):
    _register(client)
    r = client.post('/api/persons',
        data=json.dumps({'tree_name': 'T', 'first_name': 'Bob', 'last_name': 'Smith'}),
        content_type='application/json')
    data = r.get_json()
    person_id = data['person_id']
    tree_id = data['tree_id']
    r2 = client.delete(f'/api/persons/{person_id}')
    assert r2.status_code == 204
    r3 = client.get(f'/api/trees/{tree_id}')
    assert len(r3.get_json()['persons']) == 0
