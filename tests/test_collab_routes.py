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
