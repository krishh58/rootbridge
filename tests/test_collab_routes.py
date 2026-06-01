import json
import unittest.mock
from datetime import datetime, timezone, timedelta
from app.models import TreeInvite, TreeCollaborator, User

def test_tree_invite_model(app):
    with app.app_context():
        from app.db import db
        user = User(email='owner@t.com', password_hash='x', referral_code='RB-ABC123')
        db.session.add(user)
        db.session.flush()
        from app.models import Tree
        tree = Tree(user_id=user.id, name='Test Tree')
        db.session.add(tree)
        db.session.flush()
        invite = TreeInvite(
            tree_id=tree.id,
            role='editor',
            invite_token='tok123',
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        db.session.add(invite)
        db.session.commit()
        fetched = TreeInvite.query.filter_by(invite_token='tok123').first()
        assert fetched is not None
        assert fetched.role == 'editor'

def test_tree_collaborator_unique(app):
    with app.app_context():
        from app.db import db
        import sqlalchemy.exc
        u1 = User(email='u1@t.com', password_hash='x', referral_code='RB-U1X')
        u2 = User(email='u2@t.com', password_hash='x', referral_code='RB-U2X')
        db.session.add_all([u1, u2])
        db.session.flush()
        from app.models import Tree
        tree = Tree(user_id=u1.id, name='T')
        db.session.add(tree)
        db.session.flush()
        c1 = TreeCollaborator(tree_id=tree.id, user_id=u2.id, role='viewer')
        db.session.add(c1)
        db.session.commit()
        c2 = TreeCollaborator(tree_id=tree.id, user_id=u2.id, role='editor')
        db.session.add(c2)
        try:
            db.session.commit()
            assert False, 'Should have raised'
        except sqlalchemy.exc.IntegrityError:
            db.session.rollback()

def test_user_has_referral_code(app):
    with app.app_context():
        from app.db import db
        u = User(email='ref@t.com', password_hash='x', referral_code='RB-XYZ789')
        db.session.add(u)
        db.session.commit()
        fetched = User.query.filter_by(email='ref@t.com').first()
        assert fetched.referral_code == 'RB-XYZ789'
        assert fetched.referred_by_user_id is None

def _register(client, email, password='pass'):
    return client.post('/auth/register',
        data=json.dumps({'email': email, 'password': password}),
        content_type='application/json')

def _create_person_for_user(client):
    r = client.post('/api/persons',
        data=json.dumps({'tree_name': 'Test Tree', 'first_name': 'A', 'last_name': 'B'}),
        content_type='application/json')
    return r.get_json()

def test_collaborator_can_read_tree(client, app):
    _register(client, 'owner@t.com')
    ids = _create_person_for_user(client)
    tree_id = ids['tree_id']
    with app.app_context():
        from app.db import db
        from app.models import User, TreeCollaborator
        _register(client, 'collab@t.com')
        collab = User.query.filter_by(email='collab@t.com').first()
        db.session.add(TreeCollaborator(tree_id=tree_id, user_id=collab.id, role='viewer'))
        db.session.commit()
    r = client.get(f'/api/trees/{tree_id}')
    assert r.status_code == 200

def test_non_collaborator_cannot_read_tree(client, app):
    _register(client, 'owner2@t.com')
    ids = _create_person_for_user(client)
    tree_id = ids['tree_id']
    _register(client, 'stranger@t.com')
    r = client.get(f'/api/trees/{tree_id}')
    assert r.status_code == 404

def test_viewer_cannot_delete_person(client, app):
    _register(client, 'owner3@t.com')
    ids = _create_person_for_user(client)
    tree_id, person_id = ids['tree_id'], ids['person_id']
    with app.app_context():
        from app.db import db
        from app.models import User, TreeCollaborator
        _register(client, 'viewer@t.com')
        viewer = User.query.filter_by(email='viewer@t.com').first()
        db.session.add(TreeCollaborator(tree_id=tree_id, user_id=viewer.id, role='viewer'))
        db.session.commit()
    r = client.delete(f'/api/persons/{person_id}')
    assert r.status_code == 404
