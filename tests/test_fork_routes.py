import json
from app.models import Tree, Person, TreeCollaborator, User


def _register(client, email, password='pass'):
    return client.post('/auth/register',
        data=json.dumps({'email': email, 'password': password}),
        content_type='application/json')


def _login(client, email, password='pass'):
    return client.post('/auth/login',
        data=json.dumps({'email': email, 'password': password}),
        content_type='application/json')


def _create_person(client, tree_name='Test Tree', first_name='Jane', last_name='Doe'):
    r = client.post('/api/persons',
        data=json.dumps({'tree_name': tree_name, 'first_name': first_name, 'last_name': last_name}),
        content_type='application/json')
    return r.get_json()


def test_fork_creates_new_tree(client, app):
    _register(client, 'fork_owner@t.com')
    ids = _create_person(client)
    person_id = ids['person_id']

    r = client.post(f'/api/persons/{person_id}/fork')
    assert r.status_code == 201
    data = r.get_json()
    assert 'tree_id' in data
    assert 'person_id' in data

    with app.app_context():
        new_tree = Tree.query.get(data['tree_id'])
        assert new_tree is not None


def test_fork_copies_person_fields(client, app):
    _register(client, 'fork_fields@t.com')
    # Create a person with rich data
    r = client.post('/api/persons',
        data=json.dumps({
            'tree_name': 'Origin Tree',
            'first_name': 'Alice',
            'last_name': 'Smith',
            'birth_year': 1880,
            'birth_state': 'Ohio',
            'birth_country': 'USA',
            'confidence': 75,
            'death_year': 1950,
            'notes': 'Some notes',
        }),
        content_type='application/json')
    person_id = r.get_json()['person_id']

    fork_r = client.post(f'/api/persons/{person_id}/fork')
    assert fork_r.status_code == 201
    new_person_id = fork_r.get_json()['person_id']

    with app.app_context():
        new_p = Person.query.get(new_person_id)
        assert new_p.first_name == 'Alice'
        assert new_p.last_name == 'Smith'
        assert new_p.birth_year == 1880
        assert new_p.confidence == 75
        # NOT copied
        assert new_p.death_year is None
        assert new_p.notes is None


def test_fork_does_not_modify_source(client, app):
    _register(client, 'fork_source@t.com')
    ids = _create_person(client, 'Source Tree', 'Bob', 'Jones')
    tree_id = ids['tree_id']
    person_id = ids['person_id']

    client.post(f'/api/persons/{person_id}/fork')

    with app.app_context():
        # Source tree unchanged
        source_tree = Tree.query.get(tree_id)
        assert source_tree is not None
        assert any(p.id == person_id for p in source_tree.persons)

        # Source person unchanged
        source_p = Person.query.get(person_id)
        assert source_p.first_name == 'Bob'
        assert source_p.last_name == 'Jones'
        assert source_p.tree_id == tree_id


def test_fork_default_tree_name(client, app):
    _register(client, 'fork_name@t.com')
    r = client.post('/api/persons',
        data=json.dumps({
            'tree_name': 'Origin',
            'first_name': 'Clara',
            'last_name': 'Brown',
        }),
        content_type='application/json')
    person_id = r.get_json()['person_id']

    fork_r = client.post(f'/api/persons/{person_id}/fork')
    new_tree_id = fork_r.get_json()['tree_id']

    with app.app_context():
        new_tree = Tree.query.get(new_tree_id)
        assert new_tree.name == 'Clara Brown Family'


def test_fork_collaborator_can_fork(client, app):
    # Owner creates tree/person
    _register(client, 'fork_collab_owner@t.com')
    ids = _create_person(client, 'Collab Tree', 'Eve', 'White')
    tree_id = ids['tree_id']
    person_id = ids['person_id']

    # Add a viewer collaborator
    with app.app_context():
        from app.db import db
        _register(client, 'fork_viewer@t.com')
        viewer = User.query.filter_by(email='fork_viewer@t.com').first()
        db.session.add(TreeCollaborator(tree_id=tree_id, user_id=viewer.id, role='viewer'))
        db.session.commit()

    # Log in as viewer and fork
    client.post('/auth/logout')
    _login(client, 'fork_viewer@t.com')
    r = client.post(f'/api/persons/{person_id}/fork')
    assert r.status_code == 201

    data = r.get_json()
    assert 'tree_id' in data
    assert 'person_id' in data

    # New tree should be owned by the viewer
    with app.app_context():
        viewer = User.query.filter_by(email='fork_viewer@t.com').first()
        new_tree = Tree.query.get(data['tree_id'])
        assert new_tree.user_id == viewer.id


def test_fork_non_collaborator_gets_404(client, app):
    # Owner creates tree/person
    _register(client, 'fork_owner2@t.com')
    ids = _create_person(client, 'Private Tree', 'Frank', 'Green')
    person_id = ids['person_id']

    # Stranger registers and tries to fork
    client.post('/auth/logout')
    _register(client, 'fork_stranger@t.com')
    r = client.post(f'/api/persons/{person_id}/fork')
    assert r.status_code == 404
