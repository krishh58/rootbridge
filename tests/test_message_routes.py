# tests/test_message_routes.py
import json
from unittest.mock import patch
from app.models import User, Person, Tree, PersonMatch, ResearchMessage
from app.db import db


def _register(client, email, password='pass'):
    client.post('/auth/register',
        data=json.dumps({'email': email, 'password': password}),
        content_type='application/json')


def _login(client, email, password='pass'):
    client.post('/auth/login',
        data=json.dumps({'email': email, 'password': password}),
        content_type='application/json')


def _get_user_id(app, email):
    with app.app_context():
        return User.query.filter_by(email=email).first().id


def _set_tier(app, user_id, tier):
    with app.app_context():
        u = User.query.get(user_id)
        u.tier = tier
        db.session.commit()


def _make_match(app, uid_a, uid_b):
    with app.app_context():
        tree_a = Tree(user_id=uid_a, name='A')
        tree_b = Tree(user_id=uid_b, name='B')
        db.session.add_all([tree_a, tree_b])
        db.session.flush()
        pa = Person(tree_id=tree_a.id, first_name='James', last_name='Henderson',
                    birth_year=1851, birth_state='Georgia')
        pb = Person(tree_id=tree_b.id, first_name='James', last_name='Henderson',
                    birth_year=1851, birth_state='Georgia')
        db.session.add_all([pa, pb])
        db.session.flush()
        lo, hi = (pa.id, pb.id) if pa.id < pb.id else (pb.id, pa.id)
        ua, ub = (uid_a, uid_b) if pa.id < pb.id else (uid_b, uid_a)
        m = PersonMatch(person_a_id=lo, person_b_id=hi,
                        user_a_id=ua, user_b_id=ub, score=90)
        db.session.add(m)
        db.session.commit()
        return m.id


def test_send_message_requires_explorer_tier(client, app):
    _register(client, 'free_msg@test.com')
    _register(client, 'other_msg@test.com')
    uid_a = _get_user_id(app, 'free_msg@test.com')
    uid_b = _get_user_id(app, 'other_msg@test.com')
    mid = _make_match(app, uid_a, uid_b)
    _login(client, 'free_msg@test.com')
    r = client.post(f'/api/matches/{mid}/messages',
        data=json.dumps({'body': 'Hello!'}),
        content_type='application/json')
    assert r.status_code == 403
    assert 'upgrade_url' in r.get_json()


def test_send_message_explorer_succeeds(client, app):
    _register(client, 'paid_msg@test.com')
    _register(client, 'paid_other@test.com')
    uid_a = _get_user_id(app, 'paid_msg@test.com')
    uid_b = _get_user_id(app, 'paid_other@test.com')
    _set_tier(app, uid_a, 'us')
    mid = _make_match(app, uid_a, uid_b)
    _login(client, 'paid_msg@test.com')
    with patch('app.message_routes.send_match_email', return_value=True):
        r = client.post(f'/api/matches/{mid}/messages',
            data=json.dumps({'body': 'Hi from paid user'}),
            content_type='application/json')
    assert r.status_code == 201
    assert r.get_json()['body'] == 'Hi from paid user'


def test_get_messages_marks_read(client, app):
    _register(client, 'reader_a@test.com')
    _register(client, 'reader_b@test.com')
    uid_a = _get_user_id(app, 'reader_a@test.com')
    uid_b = _get_user_id(app, 'reader_b@test.com')
    _set_tier(app, uid_a, 'us')
    mid = _make_match(app, uid_a, uid_b)
    # b sends a message (inject directly)
    with app.app_context():
        msg = ResearchMessage(match_id=mid, sender_id=uid_b, body='Hello A', read=False)
        db.session.add(msg)
        db.session.commit()
    _login(client, 'reader_a@test.com')
    r = client.get(f'/api/matches/{mid}/messages')
    assert r.status_code == 200
    msgs = r.get_json()
    assert len(msgs) == 1
    with app.app_context():
        assert ResearchMessage.query.filter_by(match_id=mid, read=False).count() == 0


def test_block_deletes_match_and_messages(client, app):
    _register(client, 'block_a@test.com')
    _register(client, 'block_b@test.com')
    uid_a = _get_user_id(app, 'block_a@test.com')
    uid_b = _get_user_id(app, 'block_b@test.com')
    mid = _make_match(app, uid_a, uid_b)
    with app.app_context():
        db.session.add(ResearchMessage(match_id=mid, sender_id=uid_b, body='Hi'))
        db.session.commit()
    _login(client, 'block_a@test.com')
    r = client.post(f'/api/matches/{mid}/block')
    assert r.status_code == 200
    with app.app_context():
        assert PersonMatch.query.get(mid) is None
        assert ResearchMessage.query.filter_by(match_id=mid).count() == 0
        u = User.query.get(uid_a)
        assert uid_b in (u.blocked_user_ids or [])


def test_send_email_on_first_message(client, app):
    _register(client, 'email_a@test.com')
    _register(client, 'email_b@test.com')
    uid_a = _get_user_id(app, 'email_a@test.com')
    uid_b = _get_user_id(app, 'email_b@test.com')
    _set_tier(app, uid_a, 'us')
    mid = _make_match(app, uid_a, uid_b)
    _login(client, 'email_a@test.com')
    with patch('app.message_routes.send_match_email') as mock_email:
        mock_email.return_value = True
        client.post(f'/api/matches/{mid}/messages',
            data=json.dumps({'body': 'First message'}),
            content_type='application/json')
        assert mock_email.called


def test_no_duplicate_email_on_second_message(client, app):
    _register(client, 'dup_a@test.com')
    _register(client, 'dup_b@test.com')
    uid_a = _get_user_id(app, 'dup_a@test.com')
    uid_b = _get_user_id(app, 'dup_b@test.com')
    _set_tier(app, uid_a, 'us')
    mid = _make_match(app, uid_a, uid_b)
    # inject existing unread message from a to b
    with app.app_context():
        db.session.add(ResearchMessage(match_id=mid, sender_id=uid_a, body='prev', read=False))
        db.session.commit()
    _login(client, 'dup_a@test.com')
    with patch('app.message_routes.send_match_email') as mock_email:
        mock_email.return_value = True
        client.post(f'/api/matches/{mid}/messages',
            data=json.dumps({'body': 'Second message'}),
            content_type='application/json')
        mock_email.assert_not_called()
