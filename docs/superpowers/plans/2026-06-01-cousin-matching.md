# Cousin Matching & Research Messaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface other RootBridge users researching the same ancestors, show them each other's records side-by-side, and let them message directly — creating the network-effect retention loop.

**Architecture:** A background matching engine compares persons across all trees using name + birth year + location, writing confirmed matches (score ≥ 60) to a `PersonMatch` table. Match routes expose those matches to the frontend. Message routes handle threaded conversations anchored to a specific ancestor pair. The person card gets a badge showing researcher count; a new Connections tab provides the inbox.

**Tech Stack:** Flask/SQLAlchemy (existing), APScheduler 3.x (new), Gmail SMTP (existing mailer.py), D3.js person card (existing), vanilla JS inbox in app.html.

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `app/models.py` | Modify | Add PersonMatch, ResearchMessage models; add 3 fields to User |
| `app/matcher.py` | Create | score_match(), run_matcher() — pure matching logic |
| `app/match_routes.py` | Create | GET /api/persons/<id>/matches, GET /api/matches, POST /api/matches/<id>/seen |
| `app/message_routes.py` | Create | GET/POST /api/matches/<id>/messages, POST /api/matches/<id>/block |
| `app/mailer.py` | Modify | Add send_match_email() |
| `app/__init__.py` | Modify | Register 2 new blueprints, start APScheduler |
| `app/tree_routes.py` | Modify | Trigger matcher in background thread after person create/update |
| `static/js/person_card.js` | Modify | Fetch matches, render badge, match modal, shared records panel |
| `static/app.html` | Modify | Add Connections tab, conversation list, conversation view, discovery toggle |
| `requirements.txt` | Modify | Add APScheduler==3.10.4 |
| `tests/test_matcher.py` | Create | Unit tests for scoring, duplicate prevention, discovery gating |
| `tests/test_match_routes.py` | Create | API tests for match list, seen, block |
| `tests/test_message_routes.py` | Create | API tests for send (tier gate), read marking, email trigger, block cleanup |

---

## Task 1: Data Models

**Files:**
- Modify: `app/models.py`
- Test: `tests/test_matcher.py` (model imports only in this task)

- [ ] **Step 1: Write the failing import test**

```python
# tests/test_matcher.py
from app.models import PersonMatch, ResearchMessage

def test_person_match_model_exists():
    assert PersonMatch.__tablename__ == 'person_matches'

def test_research_message_model_exists():
    assert ResearchMessage.__tablename__ == 'research_messages'
```

- [ ] **Step 2: Run to confirm it fails**

```bash
python -m pytest tests/test_matcher.py -v
```
Expected: `ImportError: cannot import name 'PersonMatch'`

- [ ] **Step 3: Add PersonMatch, ResearchMessage, and User fields to models.py**

Add these three new User columns inside the `User` class, after `referral_reward_paid`:

```python
    discovery_enabled = db.Column(db.Boolean, default=True, nullable=False)
    blocked_user_ids  = db.Column(db.JSON, default=list)
    display_name      = db.Column(db.String(100))
```

Add a helper method to `User` (after `deduct_tokens`):

```python
    def get_display_name(self):
        if self.display_name:
            return self.display_name
        prefix = self.email.split('@')[0]
        return prefix[:20]
```

Append the two new models at the bottom of `app/models.py`:

```python
class PersonMatch(db.Model):
    __tablename__ = 'person_matches'
    id          = db.Column(db.Integer, primary_key=True)
    person_a_id = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=False)
    person_b_id = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=False)
    user_a_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    user_b_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    score       = db.Column(db.Integer, nullable=False)
    notified_a  = db.Column(db.Boolean, default=False, nullable=False)
    notified_b  = db.Column(db.Boolean, default=False, nullable=False)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    messages    = db.relationship('ResearchMessage', backref='match', lazy=True,
                                  cascade='all, delete-orphan')
    __table_args__ = (db.UniqueConstraint('person_a_id', 'person_b_id',
                                          name='uq_person_match_pair'),)


class ResearchMessage(db.Model):
    __tablename__ = 'research_messages'
    id         = db.Column(db.Integer, primary_key=True)
    match_id   = db.Column(db.Integer, db.ForeignKey('person_matches.id'), nullable=False)
    sender_id  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    body       = db.Column(db.Text, nullable=False)
    read       = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_matcher.py -v
```
Expected: `2 passed`

- [ ] **Step 5: Run the full suite to confirm nothing broke**

```bash
python -m pytest tests/ -q
```
Expected: all previously passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add app/models.py tests/test_matcher.py
git commit -m "feat: add PersonMatch, ResearchMessage models and User discovery fields"
```

---

## Task 2: Matching Engine

**Files:**
- Create: `app/matcher.py`
- Modify: `tests/test_matcher.py`

- [ ] **Step 1: Write failing tests for the matching engine**

Replace `tests/test_matcher.py` with:

```python
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
```

- [ ] **Step 2: Run to confirm they fail**

```bash
python -m pytest tests/test_matcher.py -v
```
Expected: `ImportError: cannot import name 'score_match' from 'app.matcher'`

- [ ] **Step 3: Create app/matcher.py**

```python
from .db import db
from .models import Person, Tree, User, PersonMatch


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1,
                            prev[j - 1] + (0 if ca == cb else 1)))
        prev = curr
    return prev[-1]


def _names_match(n1: str, n2: str) -> bool:
    a = (n1 or '').strip().lower()
    b = (n2 or '').strip().lower()
    return bool(a and b and _levenshtein(a, b) <= 1)


def score_match(person_a: tuple, person_b: tuple) -> int:
    """
    person_a / person_b: (first_name, last_name, birth_year, birth_state, birth_country)
    Returns confidence score 0-100. Score < 60 means no match.
    """
    first_a, last_a, year_a, state_a, country_a = person_a
    first_b, last_b, year_b, state_b, country_b = person_b

    if not (_names_match(first_a, first_b) and _names_match(last_a, last_b)):
        return 0

    score = 40  # name match baseline

    if year_a is None or year_b is None:
        return 0  # birth_year required
    diff = abs(year_a - year_b)
    if diff > 5:
        return 0
    if diff == 0:
        score += 30
    elif diff <= 2:
        score += 20
    else:
        score += 10

    state_a_n = (state_a or '').strip().lower()
    state_b_n = (state_b or '').strip().lower()
    country_a_n = (country_a or '').strip().lower()
    country_b_n = (country_b or '').strip().lower()

    if state_a_n and state_b_n and state_a_n == state_b_n:
        score += 20
    elif country_a_n and country_b_n and country_a_n == country_b_n:
        score += 10

    return score


def run_matcher(person_id: int = None):
    """
    Compare persons across trees to find ancestor matches.
    If person_id given, compare only that person against all others.
    Otherwise full scan. Skips existing pairs. Writes matches with score >= 60.
    """
    THRESHOLD = 60

    def _person_tuple(p):
        return (p.first_name, p.last_name, p.birth_year, p.birth_state, p.birth_country)

    def _user_id_for_person(p):
        return p.tree.user_id

    if person_id:
        targets = Person.query.filter_by(id=person_id).all()
        candidates = Person.query.filter(Person.id != person_id).all()
    else:
        targets = Person.query.all()
        candidates = None  # will pair within targets

    existing_pairs = set()
    for m in PersonMatch.query.with_entities(PersonMatch.person_a_id, PersonMatch.person_b_id).all():
        existing_pairs.add((m.person_a_id, m.person_b_id))

    def _process_pair(pa, pb):
        pid_lo, pid_hi = (pa.id, pb.id) if pa.id < pb.id else (pb.id, pa.id)
        if (pid_lo, pid_hi) in existing_pairs:
            return
        uid_a = _user_id_for_person(pa)
        uid_b = _user_id_for_person(pb)
        if uid_a == uid_b:
            return
        user_a = User.query.get(uid_a)
        user_b = User.query.get(uid_b)
        if not user_a or not user_b:
            return
        if not user_a.discovery_enabled or not user_b.discovery_enabled:
            return
        blocked_a = user_a.blocked_user_ids or []
        blocked_b = user_b.blocked_user_ids or []
        if uid_b in blocked_a or uid_a in blocked_b:
            return
        s = score_match(_person_tuple(pa), _person_tuple(pb))
        if s < THRESHOLD:
            return
        pa_id = pa.id if pa.id < pb.id else pb.id
        pb_id = pb.id if pa.id < pb.id else pa.id
        ua_id = uid_a if pa.id < pb.id else uid_b
        ub_id = uid_b if pa.id < pb.id else uid_a
        match = PersonMatch(
            person_a_id=pa_id, person_b_id=pb_id,
            user_a_id=ua_id, user_b_id=ub_id,
            score=s,
        )
        db.session.add(match)
        existing_pairs.add((pa_id, pb_id))
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    if person_id:
        for target in targets:
            for cand in candidates:
                _process_pair(target, cand)
    else:
        persons = targets
        for i, pa in enumerate(persons):
            for pb in persons[i + 1:]:
                _process_pair(pa, pb)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_matcher.py -v
```
Expected: all 12 tests pass.

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -q
```
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/matcher.py tests/test_matcher.py
git commit -m "feat: add matching engine with Levenshtein name scoring"
```

---

## Task 3: Match Routes

**Files:**
- Create: `app/match_routes.py`
- Modify: `app/__init__.py`
- Create: `tests/test_match_routes.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_match_routes.py
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
```

- [ ] **Step 2: Run to confirm they fail**

```bash
python -m pytest tests/test_match_routes.py -v
```
Expected: `404` or connection errors — routes don't exist yet.

- [ ] **Step 3: Create app/match_routes.py**

```python
from flask import Blueprint, jsonify, g
from .auth import require_auth
from .db import db
from .models import User, Person, Tree, TreeCollaborator, PersonMatch, SearchResult, ResearchMessage

match_bp = Blueprint('match_routes', __name__)


def _can_access_person(person_id: int, user_id: int) -> bool:
    p = Person.query.get(person_id)
    if not p:
        return False
    if p.tree.user_id == user_id:
        return True
    return TreeCollaborator.query.filter_by(
        tree_id=p.tree_id, user_id=user_id
    ).first() is not None


def _match_for_user(match: PersonMatch, user_id: int) -> dict:
    is_a = match.user_a_id == user_id
    other_user_id = match.user_b_id if is_a else match.user_a_id
    my_person_id = match.person_a_id if is_a else match.person_b_id
    their_person_id = match.person_b_id if is_a else match.person_a_id

    other_user = User.query.get(other_user_id)
    display = other_user.get_display_name() if other_user else 'Unknown'

    my_records = SearchResult.query.filter_by(person_id=my_person_id).all()
    their_records = SearchResult.query.filter_by(person_id=their_person_id).all()

    their_urls = {r.url for r in their_records}
    my_urls = {r.url for r in my_records}

    unread_count = ResearchMessage.query.filter_by(
        match_id=match.id, read=False
    ).filter(ResearchMessage.sender_id != user_id).count()

    their_person = Person.query.get(their_person_id)
    ancestor_name = ''
    if their_person:
        ancestor_name = f"{their_person.first_name or ''} {their_person.last_name or ''}".strip()
        if their_person.birth_year:
            ancestor_name += f" ~{their_person.birth_year}"

    return {
        'match_id': match.id,
        'score': match.score,
        'display_name': display,
        'ancestor_name': ancestor_name,
        'unread_count': unread_count,
        'your_records': [
            {'source': r.source, 'record_type': r.record_type, 'url': r.url}
            for r in my_records
        ],
        'their_records': [
            {'source': r.source, 'record_type': r.record_type, 'url': r.url,
             'you_dont_have': r.url not in my_urls}
            for r in their_records
        ],
        'your_records_they_dont_have': [
            r.url for r in my_records if r.url not in their_urls
        ],
    }


@match_bp.get('/api/persons/<int:person_id>/matches')
@require_auth
def get_person_matches(person_id):
    if not _can_access_person(person_id, g.user_id):
        return jsonify({'error': 'Not found'}), 404
    matches = PersonMatch.query.filter(
        db.or_(
            PersonMatch.person_a_id == person_id,
            PersonMatch.person_b_id == person_id,
        )
    ).all()
    user = User.query.get(g.user_id)
    blocked = user.blocked_user_ids or []
    result = []
    for m in matches:
        other_uid = m.user_b_id if m.user_a_id == g.user_id else m.user_a_id
        if other_uid in blocked:
            continue
        result.append(_match_for_user(m, g.user_id))
    return jsonify(result)


@match_bp.get('/api/matches')
@require_auth
def get_all_matches():
    matches = PersonMatch.query.filter(
        db.or_(
            PersonMatch.user_a_id == g.user_id,
            PersonMatch.user_b_id == g.user_id,
        )
    ).order_by(PersonMatch.created_at.desc()).all()
    user = User.query.get(g.user_id)
    blocked = user.blocked_user_ids or []
    result = []
    total_unread = 0
    for m in matches:
        other_uid = m.user_b_id if m.user_a_id == g.user_id else m.user_a_id
        if other_uid in blocked:
            continue
        d = _match_for_user(m, g.user_id)
        total_unread += d['unread_count']
        result.append(d)
    return jsonify({'matches': result, 'total_unread': total_unread})


@match_bp.post('/api/matches/<int:match_id>/seen')
@require_auth
def mark_seen(match_id):
    m = PersonMatch.query.get_or_404(match_id)
    if m.user_a_id != g.user_id and m.user_b_id != g.user_id:
        return jsonify({'error': 'Not found'}), 404
    if m.user_a_id == g.user_id:
        m.notified_a = True
    else:
        m.notified_b = True
    db.session.commit()
    return jsonify({'ok': True})
```

- [ ] **Step 4: Register match_bp in app/__init__.py**

Add after the existing fork_bp registration:

```python
    from .match_routes import match_bp
    app.register_blueprint(match_bp)
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_match_routes.py -v
```
Expected: all 4 tests pass.

- [ ] **Step 6: Run full suite**

```bash
python -m pytest tests/ -q
```
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/match_routes.py app/__init__.py tests/test_match_routes.py
git commit -m "feat: add match routes — GET /api/persons/<id>/matches, GET /api/matches, POST seen"
```

---

## Task 4: Message Routes + Mailer

**Files:**
- Create: `app/message_routes.py`
- Modify: `app/mailer.py`
- Modify: `app/__init__.py`
- Create: `tests/test_message_routes.py`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run to confirm they fail**

```bash
python -m pytest tests/test_message_routes.py -v
```
Expected: `ImportError` or 404 errors.

- [ ] **Step 3: Add send_match_email to app/mailer.py**

Append to the end of `app/mailer.py`:

```python

def send_match_email(to_email: str, sender_display: str, ancestor_name: str) -> bool:
    gmail_user = os.environ.get('GMAIL_USER', '')
    gmail_password = os.environ.get('GMAIL_APP_PASSWORD', '')
    if not gmail_user or not gmail_password:
        logger.warning('GMAIL credentials not set — skipping match email')
        return False

    safe_sender = html_escape(sender_display.replace('\r', '').replace('\n', ''))
    safe_ancestor = html_escape(ancestor_name)
    subject = f'{safe_sender} wants to collaborate on {safe_ancestor} — RootBridge'
    body_html = f"""
<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto">
  <h2 style="color:#1a3d2b">You have a research match on RootBridge</h2>
  <p><strong>{safe_sender}</strong> is also researching
     <strong>{safe_ancestor}</strong> and sent you a message.</p>
  <a href="https://rootbridge.app/app"
     style="display:inline-block;padding:.75rem 2rem;background:#4a7c59;
            color:#fff;border-radius:8px;text-decoration:none;font-weight:600">
    View Message
  </a>
  <p style="color:#64748b;font-size:.85rem;margin-top:1.5rem">
    You can turn off match notifications in your account settings.
  </p>
</div>"""

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = gmail_user
    msg['To'] = to_email
    msg.attach(MIMEText(body_html, 'html'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, to_email, msg.as_string())
        return True
    except Exception as exc:
        logger.error('Failed to send match email to %s: %s', to_email, exc)
        return False
```

- [ ] **Step 4: Create app/message_routes.py**

```python
from flask import Blueprint, request, jsonify, g
from .auth import require_auth
from .db import db
from .models import User, PersonMatch, ResearchMessage
from .mailer import send_match_email

message_bp = Blueprint('message_routes', __name__)


def _get_match_and_check_access(match_id: int, user_id: int):
    m = PersonMatch.query.get_or_404(match_id)
    if m.user_a_id != user_id and m.user_b_id != user_id:
        return None, None, None
    is_a = m.user_a_id == user_id
    other_user_id = m.user_b_id if is_a else m.user_a_id
    their_person_id = m.person_b_id if is_a else m.person_a_id
    return m, other_user_id, their_person_id


@message_bp.get('/api/matches/<int:match_id>/messages')
@require_auth
def get_messages(match_id):
    m, other_user_id, _ = _get_match_and_check_access(match_id, g.user_id)
    if m is None:
        return jsonify({'error': 'Not found'}), 404

    unread = ResearchMessage.query.filter_by(
        match_id=match_id, read=False
    ).filter(ResearchMessage.sender_id == other_user_id).all()
    for msg in unread:
        msg.read = True
    db.session.commit()

    messages = ResearchMessage.query.filter_by(match_id=match_id).order_by(
        ResearchMessage.created_at.asc()
    ).all()
    return jsonify([
        {
            'id': msg.id,
            'sender_id': msg.sender_id,
            'body': msg.body,
            'read': msg.read,
            'created_at': msg.created_at.isoformat(),
            'is_mine': msg.sender_id == g.user_id,
        }
        for msg in messages
    ])


@message_bp.post('/api/matches/<int:match_id>/messages')
@require_auth
def send_message(match_id):
    m, other_user_id, their_person_id = _get_match_and_check_access(match_id, g.user_id)
    if m is None:
        return jsonify({'error': 'Not found'}), 404

    user = User.query.get(g.user_id)
    if user.tier == 'free':
        return jsonify({
            'error': 'Explorer subscription required to send messages.',
            'upgrade_url': '/pricing',
        }), 403

    data = request.get_json() or {}
    body = (data.get('body') or '').strip()
    if not body:
        return jsonify({'error': 'body is required'}), 400

    is_first_unread = ResearchMessage.query.filter_by(
        match_id=match_id, read=False
    ).filter(ResearchMessage.sender_id == g.user_id).count() == 0

    msg = ResearchMessage(match_id=match_id, sender_id=g.user_id, body=body)
    db.session.add(msg)
    db.session.commit()

    if is_first_unread:
        other_user = User.query.get(other_user_id)
        from .models import Person
        their_person = Person.query.get(their_person_id)
        ancestor_name = ''
        if their_person:
            ancestor_name = f"{their_person.first_name or ''} {their_person.last_name or ''}".strip()
            if their_person.birth_year:
                ancestor_name += f" ~{their_person.birth_year}"
        send_match_email(
            to_email=other_user.email,
            sender_display=user.get_display_name(),
            ancestor_name=ancestor_name,
        )

    return jsonify({
        'id': msg.id,
        'sender_id': msg.sender_id,
        'body': msg.body,
        'created_at': msg.created_at.isoformat(),
        'is_mine': True,
    }), 201


@message_bp.post('/api/matches/<int:match_id>/block')
@require_auth
def block_user(match_id):
    m, other_user_id, _ = _get_match_and_check_access(match_id, g.user_id)
    if m is None:
        return jsonify({'error': 'Not found'}), 404

    user = User.query.get(g.user_id)
    blocked = list(user.blocked_user_ids or [])
    if other_user_id not in blocked:
        blocked.append(other_user_id)
    user.blocked_user_ids = blocked

    db.session.delete(m)
    db.session.commit()
    return jsonify({'ok': True})
```

- [ ] **Step 5: Register message_bp in app/__init__.py**

Add after the match_bp registration:

```python
    from .message_routes import message_bp
    app.register_blueprint(message_bp)
```

- [ ] **Step 6: Run tests**

```bash
python -m pytest tests/test_message_routes.py -v
```
Expected: all 6 tests pass.

- [ ] **Step 7: Run full suite**

```bash
python -m pytest tests/ -q
```
Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add app/message_routes.py app/mailer.py app/__init__.py tests/test_message_routes.py
git commit -m "feat: add message routes — send, read, block, email notification"
```

---

## Task 5: APScheduler + Person Save Trigger

**Files:**
- Modify: `requirements.txt`
- Modify: `app/__init__.py`
- Modify: `app/tree_routes.py`

- [ ] **Step 1: Add APScheduler to requirements.txt**

Add this line to `requirements.txt`:

```
APScheduler==3.10.4
```

- [ ] **Step 2: Install it**

```bash
pip install APScheduler==3.10.4
```

- [ ] **Step 3: Wire scheduler into app/__init__.py**

Replace the current `create_app` function body's final section (before `return app`) with:

```python
    with app.app_context():
        from . import models  # noqa: register models with SQLAlchemy
        db.create_all()

    if not app.config.get('TESTING'):
        from apscheduler.schedulers.background import BackgroundScheduler
        from .matcher import run_matcher
        scheduler = BackgroundScheduler(daemon=True)
        def _nightly_match():
            with app.app_context():
                run_matcher()
        scheduler.add_job(_nightly_match, 'cron', hour=3, max_instances=1)
        if not scheduler.running:
            scheduler.start()

    return app
```

- [ ] **Step 4: Wire trigger into tree_routes.py after person create**

In `create_person()`, replace the final `return` line with:

```python
    db.session.commit()
    _trigger_matcher_async(app=current_app._get_current_object(), person_id=p.id)
    return jsonify({'person_id': p.id, 'tree_id': tree.id}), 201
```

In `update_person()`, replace `db.session.commit()` + return with:

```python
    db.session.commit()
    _trigger_matcher_async(app=current_app._get_current_object(), person_id=p.id)
    return jsonify(_person_to_dict(p))
```

Add `_trigger_matcher_async` helper and the missing `current_app` import at the top of `tree_routes.py`. The current imports line is:

```python
from flask import Blueprint, request, jsonify, g
```

Change to:

```python
import threading
from flask import Blueprint, request, jsonify, g, current_app
```

Add the helper function before any route definitions:

```python
def _trigger_matcher_async(app, person_id: int):
    def _run():
        with app.app_context():
            from .matcher import run_matcher
            run_matcher(person_id=person_id)
    threading.Thread(target=_run, daemon=True).start()
```

- [ ] **Step 5: Run full suite to confirm nothing broke**

```bash
python -m pytest tests/ -q
```
Expected: all tests pass (scheduler does not run during TESTING=True).

- [ ] **Step 6: Commit**

```bash
git add requirements.txt app/__init__.py app/tree_routes.py
git commit -m "feat: wire APScheduler nightly match job and per-save trigger"
```

---

## Task 6: Person Card Badge + Match Modal

**Files:**
- Modify: `static/js/person_card.js`

The person card currently shows details about a selected ancestor. When opened, it will also call `GET /api/persons/<id>/matches` and render a badge + modal if matches exist.

- [ ] **Step 1: Locate the card open function in person_card.js**

```bash
grep -n "function\|openCard\|showCard\|overlay" static/js/person_card.js | head -30
```

Note the function name that renders the card overlay (e.g. `showPersonCard` or `openCard`). You will add match-loading code at the end of it.

- [ ] **Step 2: Add match badge rendering**

Find the section where the person card HTML is assembled (look for `innerHTML` assignment or a function that builds the card HTML). Add a match badge container to the card HTML:

```javascript
// Add inside the card HTML, after the main person details:
<div id="match-badge-area" style="margin-top:12px"></div>
```

- [ ] **Step 3: Add loadPersonMatches function**

Add this function to `static/js/person_card.js`:

```javascript
async function loadPersonMatches(personId) {
  const area = document.getElementById('match-badge-area');
  if (!area) return;
  try {
    const r = await fetch(`/api/persons/${personId}/matches`, { credentials: 'include' });
    if (!r.ok) return;
    const matches = await r.json();
    if (!matches.length) return;
    area.innerHTML = `
      <div style="background:#1a3d2b;border:1px solid #4a7c59;border-radius:8px;padding:10px 14px;cursor:pointer"
           onclick="openMatchModal(${JSON.stringify(matches).replace(/"/g, '&quot;')})">
        <span style="color:#4ade80;font-weight:600">👥 ${matches.length} researcher${matches.length > 1 ? 's' : ''} found</span>
        <span style="color:#86efac;font-size:0.85em;margin-left:8px">Connect →</span>
      </div>`;
  } catch (e) { /* silently ignore */ }
}
```

- [ ] **Step 4: Call loadPersonMatches when the card opens**

At the end of the function that opens/renders the person card, add:

```javascript
loadPersonMatches(personId);
```

(Replace `personId` with whatever variable holds the current person's id in that function.)

- [ ] **Step 5: Add openMatchModal function**

Add this function to `static/js/person_card.js`:

```javascript
function openMatchModal(matches) {
  const existing = document.getElementById('match-modal');
  if (existing) existing.remove();

  const modal = document.createElement('div');
  modal.id = 'match-modal';
  modal.style.cssText = `
    position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:9999;
    display:flex;align-items:center;justify-content:center;padding:16px`;
  modal.onclick = (e) => { if (e.target === modal) modal.remove(); };

  const matchCards = matches.map(m => {
    const theirNew = m.their_records.filter(r => r.you_dont_have).length;
    const yourNew = m.your_records_they_dont_have.length;
    const theirRows = m.their_records.map(r => `
      <div style="padding:4px 0;border-bottom:1px solid #1e293b;${r.you_dont_have ? 'color:#4ade80' : 'color:#94a3b8'}">
        ${r.you_dont_have ? '★ ' : ''}${r.source} — ${r.record_type || 'Record'}
        ${r.url ? `<a href="${r.url}" target="_blank" style="color:#60a5fa;font-size:0.8em;margin-left:6px">view</a>` : ''}
      </div>`).join('');
    const yourRows = m.your_records.map(r => `
      <div style="padding:4px 0;border-bottom:1px solid #1e293b;${m.your_records_they_dont_have.includes(r.url) ? 'color:#60a5fa' : 'color:#94a3b8'}">
        ${m.your_records_they_dont_have.includes(r.url) ? '★ ' : ''}${r.source} — ${r.record_type || 'Record'}
      </div>`).join('');
    return `
      <div style="background:#1e293b;border-radius:10px;padding:16px;margin-bottom:12px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
          <div>
            <span style="color:#f8fafc;font-weight:600">${m.display_name}</span>
            <span style="color:#94a3b8;font-size:0.85em;margin-left:8px">${m.ancestor_name}</span>
          </div>
          <span style="color:#4ade80;font-size:0.8em">${theirNew} new records for you</span>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
          <div>
            <div style="color:#60a5fa;font-size:0.75em;font-weight:600;margin-bottom:6px">YOUR RECORDS</div>
            ${yourRows || '<div style="color:#64748b;font-size:0.85em">None yet</div>'}
          </div>
          <div>
            <div style="color:#4ade80;font-size:0.75em;font-weight:600;margin-bottom:6px">${m.display_name.toUpperCase()}'S RECORDS</div>
            ${theirRows || '<div style="color:#64748b;font-size:0.85em">None yet</div>'}
          </div>
        </div>
        <button onclick="startConversation(${m.match_id}); document.getElementById('match-modal').remove();"
                style="width:100%;background:#4a7c59;color:#fff;border:none;border-radius:6px;
                       padding:10px;font-size:0.9em;cursor:pointer">
          Start conversation with ${m.display_name} →
        </button>
      </div>`;
  }).join('');

  modal.innerHTML = `
    <div style="background:#0f172a;border-radius:12px;padding:20px;max-width:640px;width:100%;
                max-height:80vh;overflow-y:auto;border:1px solid #334155">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <h3 style="color:#f8fafc;margin:0">Researchers on this ancestor</h3>
        <button onclick="document.getElementById('match-modal').remove()"
                style="background:none;border:none;color:#94a3b8;font-size:1.2em;cursor:pointer">✕</button>
      </div>
      <p style="color:#94a3b8;font-size:0.85em;margin-bottom:16px">
        ★ = records the other researcher has that you don't (green) or you have that they don't (blue)
      </p>
      ${matchCards}
    </div>`;
  document.body.appendChild(modal);
}

function startConversation(matchId) {
  // Switch to Connections tab and open this thread
  const tab = document.querySelector('[data-tab="connections"]');
  if (tab) tab.click();
  setTimeout(() => openThread(matchId), 100);
}
```

- [ ] **Step 6: Verify the card still loads with no JS errors**

Start the dev server and open the app. Click on a person node in the tree. The card should open normally. Console should show no errors.

```bash
python -m flask run --port 5000
```

If there are no existing matches, the badge area will be empty — that is correct.

- [ ] **Step 7: Commit**

```bash
git add static/js/person_card.js
git commit -m "feat: person card match badge and shared records modal"
```

---

## Task 7: Connections Tab Inbox

**Files:**
- Modify: `static/app.html`

This task adds the Connections tab to the existing app shell. The tab shows the conversation list and, when a thread is clicked, the conversation view with records pinned above chat.

- [ ] **Step 1: Locate the nav tab structure in app.html**

```bash
grep -n "tab\|nav\|data-tab\|section" static/app.html | head -40
```

Note the pattern used for existing tabs (e.g. My Tree, Alfred). You will add a Connections tab following the same pattern.

- [ ] **Step 2: Add the Connections tab button to the nav**

Find the nav tab buttons and add:

```html
<button class="tab-btn" data-tab="connections" onclick="switchTab('connections')">
  Connections <span id="connections-badge" style="display:none;background:#ef4444;color:#fff;
    border-radius:10px;padding:0 6px;font-size:0.75em;margin-left:4px"></span>
</button>
```

- [ ] **Step 3: Add the Connections tab panel**

Add a new tab panel alongside the existing panels:

```html
<div id="tab-connections" class="tab-panel" style="display:none;height:100%;display:flex;flex-direction:column">

  <!-- Conversation list (shown when no thread is open) -->
  <div id="connections-list" style="flex:1;overflow-y:auto;padding:16px">
    <h3 style="color:#f8fafc;margin:0 0 16px">Research Connections</h3>
    <div id="connections-list-items">
      <p style="color:#64748b">Loading...</p>
    </div>
    <div style="margin-top:24px;padding-top:16px;border-top:1px solid #1e293b">
      <label style="display:flex;align-items:center;gap:10px;color:#94a3b8;font-size:0.9em;cursor:pointer">
        <input type="checkbox" id="discovery-toggle" onchange="toggleDiscovery(this.checked)"
               style="width:16px;height:16px">
        Show my ancestors to other researchers (discovery)
      </label>
    </div>
  </div>

  <!-- Conversation view (shown when a thread is open) -->
  <div id="connections-thread" style="display:none;flex:1;display:flex;flex-direction:column;height:100%">
    <div style="padding:12px 16px;border-bottom:1px solid #1e293b;display:flex;align-items:center;gap:12px">
      <button onclick="closeThread()"
              style="background:none;border:none;color:#94a3b8;cursor:pointer;font-size:1.1em">←</button>
      <div>
        <div id="thread-title" style="color:#f8fafc;font-weight:600"></div>
        <div id="thread-ancestor" style="color:#94a3b8;font-size:0.85em"></div>
      </div>
    </div>

    <!-- Shared records strip -->
    <div id="thread-records" style="padding:10px 16px;background:#0a1628;border-bottom:1px solid #1e293b;
         overflow-x:auto;white-space:nowrap;min-height:44px"></div>

    <!-- Chat messages -->
    <div id="thread-messages" style="flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:8px">
    </div>

    <!-- Input -->
    <div style="padding:12px 16px;border-top:1px solid #1e293b;display:flex;gap:8px">
      <textarea id="thread-input" rows="2"
        style="flex:1;background:#1e293b;border:1px solid #334155;border-radius:8px;color:#f8fafc;
               padding:8px 12px;font-size:0.9em;resize:none"
        placeholder="Type a message..."
        onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendThreadMessage();}">
      </textarea>
      <button onclick="sendThreadMessage()"
              style="background:#4a7c59;color:#fff;border:none;border-radius:8px;
                     padding:0 20px;font-size:0.9em;cursor:pointer">Send</button>
    </div>
  </div>

</div>
```

- [ ] **Step 4: Add Connections JS to app.html**

Add this `<script>` block inside the page's script section (before `</body>`):

```javascript
// ── Connections tab ────────────────────────────────────────────────────────

let _currentMatchId = null;

async function loadConnections() {
  const r = await fetch('/api/matches', { credentials: 'include' });
  if (!r.ok) return;
  const { matches, total_unread } = await r.json();

  const badge = document.getElementById('connections-badge');
  if (total_unread > 0) {
    badge.textContent = total_unread;
    badge.style.display = 'inline';
  } else {
    badge.style.display = 'none';
  }

  const list = document.getElementById('connections-list-items');
  if (!matches.length) {
    list.innerHTML = '<p style="color:#64748b">No research connections yet. As more people join RootBridge and build their trees, you\'ll see matches here.</p>';
    return;
  }

  list.innerHTML = matches.map(m => `
    <div onclick="openThread(${m.match_id})"
         style="background:#1e293b;border-radius:8px;padding:12px 14px;margin-bottom:8px;
                cursor:pointer;border-left:3px solid ${m.unread_count ? '#4a7c59' : '#334155'}">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span style="color:#f8fafc;font-weight:600">${m.display_name}</span>
        ${m.unread_count ? `<span style="background:#4a7c59;color:#fff;border-radius:10px;padding:0 8px;font-size:0.75em">${m.unread_count} new</span>` : ''}
      </div>
      <div style="color:#94a3b8;font-size:0.85em;margin-top:2px">${m.ancestor_name}</div>
    </div>`).join('');

  // Load discovery toggle state
  const meR = await fetch('/api/auth/me', { credentials: 'include' });
  if (meR.ok) {
    const me = await meR.json();
    const toggle = document.getElementById('discovery-toggle');
    if (toggle && me.discovery_enabled !== undefined) {
      toggle.checked = me.discovery_enabled;
    }
  }
}

async function openThread(matchId) {
  _currentMatchId = matchId;
  document.getElementById('connections-list').style.display = 'none';
  document.getElementById('connections-thread').style.display = 'flex';

  // Load match info
  const r = await fetch('/api/matches', { credentials: 'include' });
  const { matches } = await r.json();
  const match = matches.find(m => m.match_id === matchId);
  if (match) {
    document.getElementById('thread-title').textContent = match.display_name;
    document.getElementById('thread-ancestor').textContent = match.ancestor_name;

    const strip = document.getElementById('thread-records');
    const allRecords = [
      ...match.your_records.map(r => ({ ...r, mine: true })),
      ...match.their_records.map(r => ({ ...r, mine: false })),
    ];
    strip.innerHTML = allRecords.map(r => `
      <a href="${r.url || '#'}" target="_blank"
         style="display:inline-block;background:${r.mine ? '#1e3a5f' : '#1a3d2b'};
                border-radius:6px;padding:4px 10px;margin-right:6px;font-size:0.8em;
                color:${r.mine ? '#60a5fa' : '#4ade80'};text-decoration:none;white-space:nowrap">
        ${r.source} — ${r.record_type || 'Record'}
      </a>`).join('') || '<span style="color:#64748b;font-size:0.85em">No records shared yet</span>';
  }

  await fetchThreadMessages(matchId);
  await fetch(`/api/matches/${matchId}/seen`, { method: 'POST', credentials: 'include' });
  loadConnections(); // refresh badge
}

async function fetchThreadMessages(matchId) {
  const r = await fetch(`/api/matches/${matchId}/messages`, { credentials: 'include' });
  if (!r.ok) return;
  const messages = await r.json();
  const container = document.getElementById('thread-messages');
  container.innerHTML = messages.map(msg => `
    <div style="display:flex;flex-direction:column;align-items:${msg.is_mine ? 'flex-end' : 'flex-start'}">
      <div style="max-width:70%;background:${msg.is_mine ? '#1a3d2b' : '#1e293b'};
                  border-radius:${msg.is_mine ? '12px 12px 2px 12px' : '12px 12px 12px 2px'};
                  padding:10px 14px;color:${msg.is_mine ? '#d1fae5' : '#f8fafc'};font-size:0.9em">
        ${msg.body}
      </div>
      <div style="color:#475569;font-size:0.75em;margin-top:2px">
        ${new Date(msg.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
      </div>
    </div>`).join('');
  container.scrollTop = container.scrollHeight;
}

async function sendThreadMessage() {
  if (!_currentMatchId) return;
  const input = document.getElementById('thread-input');
  const body = input.value.trim();
  if (!body) return;
  input.value = '';
  const r = await fetch(`/api/matches/${_currentMatchId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ body }),
  });
  if (r.status === 403) {
    const d = await r.json();
    alert(d.error + '\n\nUpgrade at /pricing');
    return;
  }
  if (r.ok) {
    await fetchThreadMessages(_currentMatchId);
  }
}

function closeThread() {
  _currentMatchId = null;
  document.getElementById('connections-thread').style.display = 'none';
  document.getElementById('connections-list').style.display = 'block';
}

async function toggleDiscovery(enabled) {
  await fetch('/api/auth/discovery', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ discovery_enabled: enabled }),
  });
}

// Load connections badge on page load and when switching to tab
document.addEventListener('DOMContentLoaded', () => {
  loadConnections();
  setInterval(loadConnections, 60000); // poll every 60s
});
```

- [ ] **Step 5: Add the discovery toggle endpoint**

Add to `app/auth.py` (or a suitable existing auth route file):

```python
@auth_bp.post('/api/auth/discovery')
@require_auth
def set_discovery():
    data = request.get_json() or {}
    user = User.query.get(g.user_id)
    user.discovery_enabled = bool(data.get('discovery_enabled', True))
    db.session.commit()
    return jsonify({'discovery_enabled': user.discovery_enabled})
```

Also add `discovery_enabled` to the existing `/api/auth/me` response so the toggle loads its initial state. Find the `me()` route in `auth.py` and add `'discovery_enabled': user.discovery_enabled` to its response dict.

- [ ] **Step 6: Confirm the tab switches correctly**

Start the dev server and open the app. Click Connections tab. You should see the conversation list (empty or with matches). No console errors.

```bash
python -m flask run --port 5000
```

- [ ] **Step 7: Run full suite**

```bash
python -m pytest tests/ -q
```
Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add static/app.html app/auth.py
git commit -m "feat: Connections tab — conversation list, thread view, discovery toggle"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered by |
|-----------------|-----------|
| PersonMatch model | Task 1 |
| ResearchMessage model | Task 1 |
| User.discovery_enabled, blocked_user_ids, display_name | Task 1 |
| score_match() with Levenshtein | Task 2 |
| run_matcher(person_id=None) | Task 2 |
| Score ≥ 60 threshold | Task 2 |
| GET /api/persons/<id>/matches | Task 3 |
| GET /api/matches with unread_count | Task 3 |
| POST /api/matches/<id>/seen | Task 3 |
| GET /api/matches/<id>/messages + mark read | Task 4 |
| POST /api/matches/<id>/messages + Explorer gate | Task 4 |
| POST /api/matches/<id>/block + cleanup | Task 4 |
| send_match_email (first message only) | Task 4 |
| APScheduler nightly job at 3am | Task 5 |
| Trigger on person save | Task 5 |
| Person card badge "N researchers found" | Task 6 |
| Shared records modal, split panel | Task 6 |
| Start conversation button → Connections tab | Task 6 |
| Connections nav tab with unread badge | Task 7 |
| Conversation list | Task 7 |
| Records strip pinned above chat | Task 7 |
| Chat thread with send | Task 7 |
| Discovery toggle | Task 7 |
| Display name helper (first_name + last initial) | Task 1 (get_display_name) |
| Free users see matches, blocked from sending | Task 4 (403 + upgrade_url) |
| Block adds to blocked_user_ids, deletes match+messages | Task 4 |
