import pytest
from app.models import PersonMatch, ResearchMessage, User, Person, Tree
from app.db import db
from app.matcher import score_match, run_matcher


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_user(app, email, discovery=True):
    with app.app_context():
        u = User(email=email, password_hash='x', discovery_enabled=discovery)
        db.session.add(u)
        db.session.commit()
        return u.id


def _make_person(app, user_id, first, last, birth_year, birth_state):
    with app.app_context():
        tree = Tree.query.filter_by(user_id=user_id).first()
        if not tree:
            tree = Tree(user_id=user_id, name='Test')
            db.session.add(tree)
            db.session.flush()
        p = Person(tree_id=tree.id, first_name=first, last_name=last,
                   birth_year=birth_year, birth_state=birth_state)
        db.session.add(p)
        db.session.commit()
        return p.id


# ── score_match ───────────────────────────────────────────────────────────────

def test_score_exact_match():
    score = score_match(
        ('James', 'Henderson', 1851, 'Georgia', None),
        ('James', 'Henderson', 1851, 'Georgia', None),
    )
    assert score == 90  # 40 name + 30 exact year + 20 state


def test_score_year_within_two():
    score = score_match(
        ('James', 'Henderson', 1851, 'Georgia', None),
        ('James', 'Henderson', 1853, 'Georgia', None),
    )
    assert score == 80  # 40 + 20 (within 2) + 20 state


def test_score_year_within_five():
    score = score_match(
        ('James', 'Henderson', 1851, 'Georgia', None),
        ('James', 'Henderson', 1856, 'Georgia', None),
    )
    assert score == 70  # 40 + 10 (within 5) + 20 state


def test_score_below_threshold_no_state():
    score = score_match(
        ('James', 'Henderson', 1851, None, None),
        ('James', 'Henderson', 1851, None, None),
    )
    assert score == 70  # 40 + 30, no state — still ≥ 60


def test_score_fuzzy_first_name():
    score = score_match(
        ('Jams', 'Henderson', 1851, 'Georgia', None),
        ('James', 'Henderson', 1851, 'Georgia', None),
    )
    assert score == 90  # Levenshtein ≤ 1 still counts as name match


def test_score_name_mismatch_returns_zero():
    score = score_match(
        ('Robert', 'Smith', 1851, 'Georgia', None),
        ('James', 'Henderson', 1851, 'Georgia', None),
    )
    assert score == 0  # name mismatch — names must match


def test_score_year_gap_too_large():
    score = score_match(
        ('James', 'Henderson', 1851, 'Georgia', None),
        ('James', 'Henderson', 1870, 'Georgia', None),
    )
    assert score == 0  # year gap > 5 — no year points, insufficient


# ── run_matcher ───────────────────────────────────────────────────────────────

def test_run_matcher_creates_match(app):
    uid_a = _make_user(app, 'a@test.com')
    uid_b = _make_user(app, 'b@test.com')
    pid_a = _make_person(app, uid_a, 'James', 'Henderson', 1851, 'Georgia')
    pid_b = _make_person(app, uid_b, 'James', 'Henderson', 1851, 'Georgia')
    with app.app_context():
        run_matcher()
        m = PersonMatch.query.first()
        assert m is not None
        assert {m.person_a_id, m.person_b_id} == {pid_a, pid_b}
        assert m.score >= 60


def test_run_matcher_skips_same_user(app):
    uid = _make_user(app, 'self@test.com')
    _make_person(app, uid, 'James', 'Henderson', 1851, 'Georgia')
    _make_person(app, uid, 'James', 'Henderson', 1851, 'Georgia')
    with app.app_context():
        run_matcher()
        assert PersonMatch.query.count() == 0


def test_run_matcher_skips_discovery_disabled(app):
    uid_a = _make_user(app, 'nodisco@test.com', discovery=False)
    uid_b = _make_user(app, 'disco@test.com', discovery=True)
    _make_person(app, uid_a, 'James', 'Henderson', 1851, 'Georgia')
    _make_person(app, uid_b, 'James', 'Henderson', 1851, 'Georgia')
    with app.app_context():
        run_matcher()
        assert PersonMatch.query.count() == 0


def test_run_matcher_no_duplicate(app):
    uid_a = _make_user(app, 'dup_a@test.com')
    uid_b = _make_user(app, 'dup_b@test.com')
    _make_person(app, uid_a, 'James', 'Henderson', 1851, 'Georgia')
    _make_person(app, uid_b, 'James', 'Henderson', 1851, 'Georgia')
    with app.app_context():
        run_matcher()
        run_matcher()  # second run
        assert PersonMatch.query.count() == 1


def test_run_matcher_single_person(app):
    uid_a = _make_user(app, 'single_a@test.com')
    uid_b = _make_user(app, 'single_b@test.com')
    pid_a = _make_person(app, uid_a, 'James', 'Henderson', 1851, 'Georgia')
    _make_person(app, uid_b, 'James', 'Henderson', 1851, 'Georgia')
    with app.app_context():
        run_matcher(person_id=pid_a)
        assert PersonMatch.query.count() == 1
