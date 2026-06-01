import json
from app.models import User, Person, Tree, PersonMatch
from app.db import db


def _register(client, email='u@test.com', password='pass'):
    client.post('/auth/register',
        data=json.dumps({'email': email, 'password': password}),
        content_type='application/json')


def _make_person(app, user_id, first, last, year, state):
    with app.app_context():
        tree = Tree.query.filter_by(user_id=user_id).first()
        if not tree:
            tree = Tree(user_id=user_id, name='T')
            db.session.add(tree)
            db.session.flush()
        p = Person(tree_id=tree.id, first_name=first, last_name=last,
                   birth_year=year, birth_state=state)
        db.session.add(p)
        db.session.commit()
        return p.id


def _inject_match(app, pid_a, pid_b, uid_a, uid_b, score=90):
    with app.app_context():
        lo, hi = (pid_a, pid_b) if pid_a < pid_b else (pid_b, pid_a)
        ua, ub = (uid_a, uid_b) if pid_a < pid_b else (uid_b, uid_a)
        m = PersonMatch(person_a_id=lo, person_b_id=hi,
                        user_a_id=ua, user_b_id=ub, score=score)
        db.session.add(m)
        db.session.commit()
        return m.id


def _get_user_id(app, email):
    with app.app_context():
        return User.query.filter_by(email=email).first().id


def test_get_person_matches_returns_list(client, app):
    _register(client, 'ma@test.com')
    uid_a = _get_user_id(app, 'ma@test.com')
    pid_a = _make_person(app, uid_a, 'James', 'Henderson', 1851, 'Georgia')

    _register(client, 'mb@test.com')
    uid_b = _get_user_id(app, 'mb@test.com')
    pid_b = _make_person(app, uid_b, 'James', 'Henderson', 1851, 'Georgia')
    _inject_match(app, pid_a, pid_b, uid_a, uid_b)

    client.post('/auth/login',
        data=json.dumps({'email': 'ma@test.com', 'password': 'pass'}),
        content_type='application/json')
    r = client.get(f'/api/persons/{pid_a}/matches')
    assert r.status_code == 200
    data = r.get_json()
    assert len(data) == 1
    assert data[0]['match_id'] is not None
    assert data[0]['score'] == 90


def test_get_person_matches_requires_auth(client, app):
    r = client.get('/api/persons/999/matches')
    assert r.status_code == 401


def test_get_all_matches_returns_unread_count(client, app):
    _register(client, 'allm_a@test.com')
    uid_a = _get_user_id(app, 'allm_a@test.com')
    pid_a = _make_person(app, uid_a, 'Eliza', 'Mae', 1870, 'Ohio')

    _register(client, 'allm_b@test.com')
    uid_b = _get_user_id(app, 'allm_b@test.com')
    pid_b = _make_person(app, uid_b, 'Eliza', 'Mae', 1870, 'Ohio')
    _inject_match(app, pid_a, pid_b, uid_a, uid_b)

    client.post('/auth/login',
        data=json.dumps({'email': 'allm_a@test.com', 'password': 'pass'}),
        content_type='application/json')
    r = client.get('/api/matches')
    assert r.status_code == 200
    body = r.get_json()
    assert 'matches' in body
    assert 'total_unread' in body
    assert len(body['matches']) == 1


def test_post_seen_marks_notified(client, app):
    _register(client, 'seen_a@test.com')
    uid_a = _get_user_id(app, 'seen_a@test.com')
    pid_a = _make_person(app, uid_a, 'Tom', 'Jones', 1860, 'Alabama')

    _register(client, 'seen_b@test.com')
    uid_b = _get_user_id(app, 'seen_b@test.com')
    pid_b = _make_person(app, uid_b, 'Tom', 'Jones', 1860, 'Alabama')
    mid = _inject_match(app, pid_a, pid_b, uid_a, uid_b)

    client.post('/auth/login',
        data=json.dumps({'email': 'seen_a@test.com', 'password': 'pass'}),
        content_type='application/json')
    r = client.post(f'/api/matches/{mid}/seen')
    assert r.status_code == 200
    with app.app_context():
        m = PersonMatch.query.get(mid)
        assert m.notified_a is True
        assert m.notified_b is False  # only the caller's side marked
