from app.models import User, Tree, Person, Gap, SearchResult

def test_user_default_tier(db):
    u = User(email='a@test.com', password_hash='x')
    db.session.add(u)
    db.session.commit()
    assert u.tier == 'free'
    assert u.token_balance == 100
    assert u.token_balance_topup == 0

def test_user_deduct_tokens_from_balance(db):
    u = User(email='b@test.com', password_hash='x', token_balance=50, token_balance_topup=0)
    db.session.add(u)
    db.session.commit()
    assert u.deduct_tokens(30) is True
    assert u.token_balance == 20

def test_user_deduct_tokens_spills_into_topup(db):
    u = User(email='c@test.com', password_hash='x', token_balance=10, token_balance_topup=100)
    db.session.add(u)
    db.session.commit()
    assert u.deduct_tokens(40) is True
    assert u.token_balance == 0
    assert u.token_balance_topup == 70

def test_user_deduct_tokens_insufficient(db):
    u = User(email='d@test.com', password_hash='x', token_balance=5, token_balance_topup=0)
    db.session.add(u)
    db.session.commit()
    assert u.deduct_tokens(10) is False
    assert u.token_balance == 5

def test_tree_belongs_to_user(db):
    u = User(email='e@test.com', password_hash='x')
    db.session.add(u)
    db.session.commit()
    t = Tree(user_id=u.id, name='Henderson Family')
    db.session.add(t)
    db.session.commit()
    assert t.owner.email == 'e@test.com'

def test_person_belongs_to_tree(db):
    u = User(email='f@test.com', password_hash='x')
    db.session.add(u)
    db.session.commit()
    t = Tree(user_id=u.id, name='Test Tree')
    db.session.add(t)
    db.session.commit()
    p = Person(tree_id=t.id, first_name='Christopher', last_name='Haines', birth_year=1760)
    db.session.add(p)
    db.session.commit()
    assert p.confidence == 0
    assert p.parent_ids == []

def test_gap_belongs_to_person(db):
    u = User(email='g@test.com', password_hash='x')
    db.session.add(u)
    db.session.commit()
    t = Tree(user_id=u.id, name='T')
    db.session.add(t)
    db.session.commit()
    p = Person(tree_id=t.id, first_name='John', last_name='Doe')
    db.session.add(p)
    db.session.commit()
    g = Gap(person_id=p.id, gap_type='missing_parents',
            suggested_source='FamilySearch', suggested_query='John Doe 1800 Virginia')
    db.session.add(g)
    db.session.commit()
    assert g.resolved is False
