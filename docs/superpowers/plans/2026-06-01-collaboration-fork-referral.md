# RootBridge Plan 6: Collaboration, Fork, and Referral

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add family tree collaboration (invite by link or email, viewer/editor roles), fork-a-person into a new tree, and a referral token system (25 tokens on signup, 150 on subscription).

**Architecture:** Three new modules (`collab_routes.py`, `fork_routes.py`, `mailer.py`) plus model additions. A shared `_can_access_tree()` helper in `tree_routes.py` gates all read/write access to trees and persons. Referral state lives on the `User` model; the Stripe webhook already handles subscriptions and is extended to award referral tokens.

**Tech Stack:** Flask, SQLAlchemy, PyJWT (already installed), smtplib (stdlib — no new packages), secrets (stdlib), existing Redis/Postgres on Railway.

**Spec:** `docs/superpowers/specs/2026-06-01-collaboration-referral-design.md`

---

## File Structure

```
app/
├── models.py            MODIFY — add TreeInvite, TreeCollaborator; add referral_code + referred_by_user_id to User
├── tree_routes.py       MODIFY — add _can_access_tree() helper; open GET routes to collaborators
├── collab_routes.py     CREATE — invite CRUD + accept + collaborator management + GET /invite/<token> HTML page
├── fork_routes.py       CREATE — POST /api/persons/<id>/fork
├── mailer.py            CREATE — send_invite_email() via Gmail SMTP
├── auth.py              MODIFY — generate referral_code on register; award 25 tokens on referral signup
├── stripe_routes.py     MODIFY — award 150 tokens to referrer on first subscription
├── __init__.py          MODIFY — register collab_bp and fork_bp
tests/
├── test_collab_routes.py   CREATE
├── test_fork_routes.py     CREATE
├── test_referral.py        CREATE
static/
├── js/person_card.js    MODIFY — add "Start New Tree From Here" button + runFork()
├── app.html             MODIFY — add referral panel and invite panel to settings UI
```

---

### Task 1: Models — TreeInvite, TreeCollaborator, User referral fields

**Files:**
- Modify: `app/models.py`
- Test: `tests/test_collab_routes.py` (partial — model instantiation only)

- [ ] **Step 1: Write failing test**

Create `tests/test_collab_routes.py`:

```python
import json
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
        u1 = User(email='u1@t.com', password_hash='x', referral_code='RB-U1')
        u2 = User(email='u2@t.com', password_hash='x', referral_code='RB-U2')
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
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/krish/familytree_app && source venv/bin/activate && pytest tests/test_collab_routes.py::test_tree_invite_model tests/test_collab_routes.py::test_tree_collaborator_unique tests/test_collab_routes.py::test_user_has_referral_code -v
```

Expected: FAIL with `ImportError: cannot import name 'TreeInvite'`

- [ ] **Step 3: Add models to `app/models.py`**

Add these two classes after the `AlfredMessage` class, and add two columns to `User`:

```python
# In User class, after stripe_subscription_id line (line 14), add:
    referral_code = db.Column(db.String(16), unique=True)
    referred_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
```

Add after the `AlfredMessage` class at the bottom of `app/models.py`:

```python
class TreeInvite(db.Model):
    __tablename__ = 'tree_invites'
    id = db.Column(db.Integer, primary_key=True)
    tree_id = db.Column(db.Integer, db.ForeignKey('trees.id'), nullable=False)
    role = db.Column(db.String(10), nullable=False)  # 'viewer' | 'editor'
    invite_token = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(255))
    claimed_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime, nullable=False)


class TreeCollaborator(db.Model):
    __tablename__ = 'tree_collaborators'
    id = db.Column(db.Integer, primary_key=True)
    tree_id = db.Column(db.Integer, db.ForeignKey('trees.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    role = db.Column(db.String(10), nullable=False)  # 'viewer' | 'editor'
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (db.UniqueConstraint('tree_id', 'user_id', name='uq_tree_collaborator'),)
```

- [ ] **Step 4: Run tests**

```bash
cd /home/krish/familytree_app && source venv/bin/activate && pytest tests/test_collab_routes.py::test_tree_invite_model tests/test_collab_routes.py::test_tree_collaborator_unique tests/test_collab_routes.py::test_user_has_referral_code -v
```

Expected: All 3 PASS

- [ ] **Step 5: Commit**

```bash
cd /home/krish/familytree_app && git add app/models.py tests/test_collab_routes.py && git commit -m "feat: add TreeInvite, TreeCollaborator models and User referral fields"
```

---

### Task 2: Access control helper + open tree routes to collaborators

**Files:**
- Modify: `app/tree_routes.py` — add `_can_access_tree()`, update GET routes

- [ ] **Step 1: Write failing tests**

Add to `tests/test_collab_routes.py`:

```python
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
    # Add collaborator directly in DB
    with app.app_context():
        from app.db import db
        from app.models import User, TreeCollaborator
        _register(client, 'collab@t.com')  # registers and logs in as collab
        collab = User.query.filter_by(email='collab@t.com').first()
        db.session.add(TreeCollaborator(tree_id=tree_id, user_id=collab.id, role='viewer'))
        db.session.commit()
    r = client.get(f'/api/trees/{tree_id}')
    assert r.status_code == 200

def test_non_collaborator_cannot_read_tree(client, app):
    _register(client, 'owner2@t.com')
    ids = _create_person_for_user(client)
    tree_id = ids['tree_id']
    _register(client, 'stranger@t.com')  # logs in as stranger
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
    assert r.status_code == 404  # viewer treated as non-owner for deletes
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/krish/familytree_app && source venv/bin/activate && pytest tests/test_collab_routes.py::test_collaborator_can_read_tree tests/test_collab_routes.py::test_non_collaborator_cannot_read_tree tests/test_collab_routes.py::test_viewer_cannot_delete_person -v
```

Expected: `test_collaborator_can_read_tree` FAIL (returns 404 before helper exists), others may pass or fail.

- [ ] **Step 3: Add `_can_access_tree()` to `app/tree_routes.py`**

Add this function after the imports, before `tree_bp = Blueprint(...)`:

```python
def _can_access_tree(tree_id: int, user_id: int, require_editor: bool = False) -> bool:
    from .models import TreeCollaborator
    tree = Tree.query.get(tree_id)
    if not tree:
        return False
    if tree.user_id == user_id:
        return True
    collab = TreeCollaborator.query.filter_by(tree_id=tree_id, user_id=user_id).first()
    if not collab:
        return False
    if require_editor:
        return collab.role == 'editor'
    return True
```

- [ ] **Step 4: Update `get_tree` and `get_person` and `get_hometown` in `app/tree_routes.py`**

Replace these three route bodies to use the helper:

```python
@tree_bp.get('/api/trees/<int:tree_id>')
@require_auth
def get_tree(tree_id):
    if not _can_access_tree(tree_id, g.user_id):
        from flask import abort
        abort(404)
    tree = Tree.query.get(tree_id)
    return jsonify({
        'id': tree.id, 'name': tree.name, 'share_token': tree.share_token,
        'persons': [_person_to_dict(p) for p in tree.persons],
    })

@tree_bp.get('/api/persons/<int:person_id>')
@require_auth
def get_person(person_id):
    p = Person.query.get_or_404(person_id)
    if not _can_access_tree(p.tree_id, g.user_id):
        from flask import abort
        abort(404)
    return jsonify(_person_detail(p))

@tree_bp.get('/api/persons/<int:person_id>/hometown')
@require_auth
def get_hometown(person_id):
    p = Person.query.get_or_404(person_id)
    if not _can_access_tree(p.tree_id, g.user_id):
        from flask import abort
        abort(404)
    place = p.birth_state or p.birth_country or ''
    if not place:
        return jsonify({'available': False})
    name = f'{p.first_name or ""} {p.last_name or ""}'.strip()
    photo = get_hometown_photo(place, p.birth_year)
    historical_map = get_historical_map(place, p.birth_year)
    life_context = get_life_context(name, place, p.birth_year, p.death_year)
    return jsonify({
        'available': True, 'place': place,
        'photo': photo, 'map': historical_map, 'life_context': life_context,
    })
```

- [ ] **Step 5: Run tests**

```bash
cd /home/krish/familytree_app && source venv/bin/activate && pytest tests/test_collab_routes.py::test_collaborator_can_read_tree tests/test_collab_routes.py::test_non_collaborator_cannot_read_tree tests/test_collab_routes.py::test_viewer_cannot_delete_person -v
```

Expected: All 3 PASS

- [ ] **Step 6: Run full suite to check no regressions**

```bash
cd /home/krish/familytree_app && source venv/bin/activate && pytest tests/ -v 2>&1 | tail -10
```

Expected: All 82 existing tests still pass.

- [ ] **Step 7: Commit**

```bash
cd /home/krish/familytree_app && git add app/tree_routes.py tests/test_collab_routes.py && git commit -m "feat: _can_access_tree() helper — open GET routes to collaborators"
```

---

### Task 3: Collaboration routes — invite create, preview, accept, manage

**Files:**
- Create: `app/collab_routes.py`
- Modify: `app/__init__.py`
- Test: `tests/test_collab_routes.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_collab_routes.py`:

```python
def test_create_invite_returns_url(client, app):
    _register(client, 'inv_owner@t.com')
    ids = _create_person_for_user(client)
    tree_id = ids['tree_id']
    r = client.post(f'/api/trees/{tree_id}/invite',
        data=json.dumps({'role': 'editor'}),
        content_type='application/json')
    assert r.status_code == 201
    data = r.get_json()
    assert 'invite_url' in data
    assert 'token' in data

def test_invite_preview(client, app):
    _register(client, 'prev_owner@t.com')
    ids = _create_person_for_user(client)
    tree_id = ids['tree_id']
    r = client.post(f'/api/trees/{tree_id}/invite',
        data=json.dumps({'role': 'viewer'}),
        content_type='application/json')
    token = r.get_json()['token']
    client.post('/auth/logout')
    r2 = client.get(f'/api/invite/{token}')
    assert r2.status_code == 200
    data = r2.get_json()
    assert data['role'] == 'viewer'
    assert 'tree_name' in data

def test_accept_invite_creates_collaborator(client, app):
    _register(client, 'acc_owner@t.com')
    ids = _create_person_for_user(client)
    tree_id = ids['tree_id']
    r = client.post(f'/api/trees/{tree_id}/invite',
        data=json.dumps({'role': 'editor'}),
        content_type='application/json')
    token = r.get_json()['token']
    client.post('/auth/logout')
    _register(client, 'accepter@t.com')
    r2 = client.post(f'/api/invite/{token}/accept')
    assert r2.status_code == 200
    assert r2.get_json()['role'] == 'editor'

def test_expired_invite_returns_410(client, app):
    _register(client, 'exp_owner@t.com')
    ids = _create_person_for_user(client)
    tree_id = ids['tree_id']
    r = client.post(f'/api/trees/{tree_id}/invite',
        data=json.dumps({'role': 'viewer'}),
        content_type='application/json')
    token = r.get_json()['token']
    with app.app_context():
        from app.db import db
        from app.models import TreeInvite
        from datetime import datetime, timezone, timedelta
        invite = TreeInvite.query.filter_by(invite_token=token).first()
        invite.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.session.commit()
    _register(client, 'late@t.com')
    r2 = client.post(f'/api/invite/{token}/accept')
    assert r2.status_code == 410

def test_list_and_remove_collaborator(client, app):
    _register(client, 'mgr_owner@t.com')
    ids = _create_person_for_user(client)
    tree_id = ids['tree_id']
    r = client.post(f'/api/trees/{tree_id}/invite',
        data=json.dumps({'role': 'viewer'}),
        content_type='application/json')
    token = r.get_json()['token']
    client.post('/auth/logout')
    _register(client, 'to_remove@t.com')
    client.post(f'/api/invite/{token}/accept')
    with app.app_context():
        from app.models import User
        collab_id = User.query.filter_by(email='to_remove@t.com').first().id
    client.post('/auth/logout')
    _register(client, 'mgr_owner@t.com', 'pass')
    client.post('/auth/login',
        data=json.dumps({'email': 'mgr_owner@t.com', 'password': 'pass'}),
        content_type='application/json')
    r2 = client.get(f'/api/trees/{tree_id}/collaborators')
    assert r2.status_code == 200
    assert any(c['user_id'] == collab_id for c in r2.get_json()['collaborators'])
    r3 = client.delete(f'/api/trees/{tree_id}/collaborators/{collab_id}')
    assert r3.status_code == 204
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/krish/familytree_app && source venv/bin/activate && pytest tests/test_collab_routes.py::test_create_invite_returns_url -v
```

Expected: FAIL with 404 (route not registered)

- [ ] **Step 3: Create `app/collab_routes.py`**

```python
import secrets
from datetime import datetime, timezone, timedelta
from flask import Blueprint, request, jsonify, g, render_template_string
from .auth import require_auth
from .db import db
from .models import User, Tree, TreeInvite, TreeCollaborator

collab_bp = Blueprint('collab', __name__)

INVITE_TTL_DAYS = 7


def _assert_owner(tree_id: int):
    tree = Tree.query.get_or_404(tree_id)
    if tree.user_id != g.user_id:
        from flask import abort
        abort(403)
    return tree


@collab_bp.post('/api/trees/<int:tree_id>/invite')
@require_auth
def create_invite(tree_id):
    tree = _assert_owner(tree_id)
    data = request.get_json() or {}
    role = data.get('role', 'viewer')
    if role not in ('viewer', 'editor'):
        return jsonify({'error': 'role must be viewer or editor'}), 400
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS)
    invite = TreeInvite(
        tree_id=tree_id, role=role,
        invite_token=token,
        email=data.get('email'),
        expires_at=expires_at,
    )
    db.session.add(invite)
    db.session.commit()

    invite_url = f'/invite/{token}'
    email = data.get('email')
    if email:
        from .mailer import send_invite_email
        owner = User.query.get(g.user_id)
        send_invite_email(
            to_email=email,
            inviter_name=owner.email,
            tree_name=tree.name,
            role=role,
            invite_url=invite_url,
        )

    return jsonify({'invite_url': invite_url, 'token': token, 'expires_at': expires_at.isoformat()}), 201


@collab_bp.get('/api/invite/<token>')
def preview_invite(token):
    invite = TreeInvite.query.filter_by(invite_token=token).first_or_404()
    if invite.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return jsonify({'error': 'Invite has expired'}), 410
    tree = Tree.query.get(invite.tree_id)
    owner = User.query.get(tree.user_id)
    return jsonify({
        'tree_name': tree.name,
        'inviter_name': owner.email,
        'role': invite.role,
        'expires_at': invite.expires_at.isoformat(),
    })


@collab_bp.post('/api/invite/<token>/accept')
@require_auth
def accept_invite(token):
    invite = TreeInvite.query.filter_by(invite_token=token).first_or_404()
    if invite.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return jsonify({'error': 'Invite has expired'}), 410
    if invite.claimed_by_user_id:
        return jsonify({'error': 'Invite already claimed'}), 400
    existing = TreeCollaborator.query.filter_by(
        tree_id=invite.tree_id, user_id=g.user_id
    ).first()
    if existing:
        return jsonify({'tree_id': invite.tree_id, 'role': existing.role})
    collab = TreeCollaborator(tree_id=invite.tree_id, user_id=g.user_id, role=invite.role)
    invite.claimed_by_user_id = g.user_id
    db.session.add(collab)
    db.session.commit()
    return jsonify({'tree_id': invite.tree_id, 'role': invite.role})


@collab_bp.get('/api/trees/<int:tree_id>/collaborators')
@require_auth
def list_collaborators(tree_id):
    _assert_owner(tree_id)
    collabs = TreeCollaborator.query.filter_by(tree_id=tree_id).all()
    result = []
    for c in collabs:
        u = User.query.get(c.user_id)
        result.append({
            'user_id': c.user_id,
            'email': u.email if u else '',
            'role': c.role,
            'joined_at': c.created_at.isoformat() if c.created_at else None,
        })
    return jsonify({'collaborators': result})


@collab_bp.delete('/api/trees/<int:tree_id>/collaborators/<int:user_id>')
@require_auth
def remove_collaborator(tree_id, user_id):
    _assert_owner(tree_id)
    collab = TreeCollaborator.query.filter_by(
        tree_id=tree_id, user_id=user_id
    ).first_or_404()
    db.session.delete(collab)
    db.session.commit()
    return '', 204


@collab_bp.get('/invite/<token>')
def invite_landing(token):
    invite = TreeInvite.query.filter_by(invite_token=token).first_or_404()
    expired = invite.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc)
    tree = Tree.query.get(invite.tree_id)
    owner = User.query.get(tree.user_id) if tree else None
    return render_template_string("""<!DOCTYPE html>
<html><head><title>RootBridge Invitation</title>
<link rel="stylesheet" href="/static/style.css">
</head><body>
<nav class="nav"><a href="/" class="nav-brand">RootBridge</a></nav>
<div style="max-width:480px;margin:4rem auto;padding:2rem;background:#1e293b;border-radius:12px;text-align:center">
  {% if expired %}
  <h2 style="color:#f87171">This invite has expired</h2>
  <p style="color:#94a3b8">Ask the tree owner to send a new invite link.</p>
  {% else %}
  <h2 style="color:#f8fafc">You've been invited!</h2>
  <p style="color:#94a3b8"><strong style="color:#f8fafc">{{ inviter }}</strong>
    invited you to collaborate on <strong style="color:#f8fafc">{{ tree_name }}</strong>
    as a <strong style="color:#60a5fa">{{ role }}</strong>.</p>
  <a href="/app" style="display:inline-block;margin-top:1rem;padding:.75rem 2rem;
    background:#2563eb;color:#fff;border-radius:8px;text-decoration:none;font-weight:600">
    Sign in to Accept
  </a>
  <p style="color:#64748b;font-size:.8rem;margin-top:1rem">
    After signing in, visit <code>/api/invite/{{ token }}/accept</code> to claim your invite.
  </p>
  {% endif %}
</div>
</body></html>""",
        expired=expired,
        tree_name=tree.name if tree else 'Unknown Tree',
        inviter=owner.email if owner else 'Someone',
        role=invite.role,
        token=token,
    )
```

- [ ] **Step 4: Register blueprint in `app/__init__.py`**

Add after the `heritage_bp` registration:

```python
    from .collab_routes import collab_bp
    app.register_blueprint(collab_bp)
```

- [ ] **Step 5: Run tests**

```bash
cd /home/krish/familytree_app && source venv/bin/activate && pytest tests/test_collab_routes.py -v
```

Expected: All 11 tests PASS

- [ ] **Step 6: Commit**

```bash
cd /home/krish/familytree_app && git add app/collab_routes.py app/__init__.py tests/test_collab_routes.py && git commit -m "feat: collaboration routes — invite create, preview, accept, list, remove"
```

---

### Task 4: Mailer — Gmail SMTP invite email

**Files:**
- Create: `app/mailer.py`
- Test: `tests/test_collab_routes.py` (one additional test)

- [ ] **Step 1: Write failing test**

Add to `tests/test_collab_routes.py`:

```python
def test_invite_with_email_calls_mailer(client, app):
    _register(client, 'mail_owner@t.com')
    ids = _create_person_for_user(client)
    tree_id = ids['tree_id']
    with unittest.mock.patch('app.collab_routes.send_invite_email') as mock_mail:
        r = client.post(f'/api/trees/{tree_id}/invite',
            data=json.dumps({'role': 'editor', 'email': 'cousin@family.com'}),
            content_type='application/json')
    assert r.status_code == 201
    mock_mail.assert_called_once()
    args = mock_mail.call_args
    assert args.kwargs['to_email'] == 'cousin@family.com' or args[0][0] == 'cousin@family.com'
```

Add `import unittest.mock` at the top of `tests/test_collab_routes.py`.

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/krish/familytree_app && source venv/bin/activate && pytest tests/test_collab_routes.py::test_invite_with_email_calls_mailer -v
```

Expected: FAIL with `ImportError: cannot import name 'send_invite_email'`

- [ ] **Step 3: Create `app/mailer.py`**

```python
import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)


def send_invite_email(to_email: str, inviter_name: str, tree_name: str,
                      role: str, invite_url: str) -> bool:
    gmail_user = os.environ.get('GMAIL_USER', '')
    gmail_password = os.environ.get('GMAIL_APP_PASSWORD', '')
    if not gmail_user or not gmail_password:
        logger.warning('GMAIL_USER or GMAIL_APP_PASSWORD not set — skipping invite email')
        return False

    subject = f'{inviter_name} invited you to collaborate on RootBridge'
    body_html = f"""
<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto">
  <h2 style="color:#1e40af">You have a RootBridge invitation</h2>
  <p><strong>{inviter_name}</strong> has invited you to collaborate on
     <strong>{tree_name}</strong> as a <strong>{role}</strong>.</p>
  <a href="https://rootbridge.app{invite_url}"
     style="display:inline-block;padding:.75rem 2rem;background:#2563eb;
            color:#fff;border-radius:8px;text-decoration:none;font-weight:600">
    Accept Invitation
  </a>
  <p style="color:#64748b;font-size:.85rem;margin-top:1.5rem">
    This link expires in 7 days. If you weren't expecting this, you can ignore it.
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
        logger.error('Failed to send invite email to %s: %s', to_email, exc)
        return False
```

- [ ] **Step 4: Run test**

```bash
cd /home/krish/familytree_app && source venv/bin/activate && pytest tests/test_collab_routes.py::test_invite_with_email_calls_mailer -v
```

Expected: PASS

- [ ] **Step 5: Run full collab suite**

```bash
cd /home/krish/familytree_app && source venv/bin/activate && pytest tests/test_collab_routes.py -v
```

Expected: All 12 tests PASS

- [ ] **Step 6: Commit**

```bash
cd /home/krish/familytree_app && git add app/mailer.py tests/test_collab_routes.py && git commit -m "feat: Gmail SMTP mailer for invite emails — graceful fallback when env vars absent"
```

---

### Task 5: Fork a person into a new tree

**Files:**
- Create: `app/fork_routes.py`
- Modify: `app/__init__.py`
- Create: `tests/test_fork_routes.py`
- Modify: `static/js/person_card.js`

- [ ] **Step 1: Write failing tests**

Create `tests/test_fork_routes.py`:

```python
import json
from app.models import Tree, Person, User

def _register(client, email):
    return client.post('/auth/register',
        data=json.dumps({'email': email, 'password': 'pass'}),
        content_type='application/json')

def _create_person(client, first='Alice', last='Smith', tree='Main Tree'):
    r = client.post('/api/persons',
        data=json.dumps({'tree_name': tree, 'first_name': first, 'last_name': last,
                         'birth_year': 1850, 'birth_state': 'Ohio', 'confidence': 75}),
        content_type='application/json')
    return r.get_json()

def test_fork_creates_new_tree(client, app):
    _register(client, 'fork1@t.com')
    ids = _create_person(client)
    person_id = ids['person_id']
    r = client.post(f'/api/persons/{person_id}/fork',
        data=json.dumps({'tree_name': "Mother's Side"}),
        content_type='application/json')
    assert r.status_code == 201
    data = r.get_json()
    assert 'tree_id' in data
    assert 'person_id' in data
    assert data['tree_name'] == "Mother's Side"
    assert data['tree_id'] != ids['tree_id']

def test_fork_copies_person_fields(client, app):
    _register(client, 'fork2@t.com')
    ids = _create_person(client, first='Robert', last='Johnson')
    person_id = ids['person_id']
    r = client.post(f'/api/persons/{person_id}/fork',
        data=json.dumps({}), content_type='application/json')
    assert r.status_code == 201
    new_person_id = r.get_json()['person_id']
    with app.app_context():
        p = Person.query.get(new_person_id)
        assert p.first_name == 'Robert'
        assert p.last_name == 'Johnson'
        assert p.birth_year == 1850
        assert p.confidence == 75

def test_fork_does_not_modify_source_tree(client, app):
    _register(client, 'fork3@t.com')
    ids = _create_person(client)
    orig_tree_id = ids['tree_id']
    client.post(f'/api/persons/{ids["person_id"]}/fork',
        data=json.dumps({}), content_type='application/json')
    with app.app_context():
        orig_tree = Tree.query.get(orig_tree_id)
        assert len(orig_tree.persons) == 1

def test_fork_default_tree_name(client, app):
    _register(client, 'fork4@t.com')
    ids = _create_person(client, first='Mary', last='Williams')
    r = client.post(f'/api/persons/{ids["person_id"]}/fork',
        data=json.dumps({}), content_type='application/json')
    assert r.get_json()['tree_name'] == 'Mary Williams Family'

def test_collaborator_viewer_can_fork(client, app):
    _register(client, 'fork_owner@t.com')
    ids = _create_person(client)
    tree_id, person_id = ids['tree_id'], ids['person_id']
    r_invite = client.post(f'/api/trees/{tree_id}/invite',
        data=json.dumps({'role': 'viewer'}), content_type='application/json')
    token = r_invite.get_json()['token']
    client.post('/auth/logout')
    _register(client, 'fork_viewer@t.com')
    client.post(f'/api/invite/{token}/accept')
    r = client.post(f'/api/persons/{person_id}/fork',
        data=json.dumps({}), content_type='application/json')
    assert r.status_code == 201

def test_non_collaborator_cannot_fork(client, app):
    _register(client, 'fork_owner2@t.com')
    ids = _create_person(client)
    client.post('/auth/logout')
    _register(client, 'stranger_fork@t.com')
    r = client.post(f'/api/persons/{ids["person_id"]}/fork',
        data=json.dumps({}), content_type='application/json')
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/krish/familytree_app && source venv/bin/activate && pytest tests/test_fork_routes.py::test_fork_creates_new_tree -v
```

Expected: FAIL with 404 (route not registered)

- [ ] **Step 3: Create `app/fork_routes.py`**

```python
from flask import Blueprint, request, jsonify, g
from .auth import require_auth
from .db import db
from .models import Tree, Person
from .tree_routes import _can_access_tree

fork_bp = Blueprint('fork', __name__)


@fork_bp.post('/api/persons/<int:person_id>/fork')
@require_auth
def fork_person(person_id):
    source = Person.query.get_or_404(person_id)
    if not _can_access_tree(source.tree_id, g.user_id):
        from flask import abort
        abort(404)

    data = request.get_json() or {}
    first = source.first_name or ''
    last = source.last_name or ''
    tree_name = data.get('tree_name') or f'{first} {last} Family'.strip()

    new_tree = Tree(user_id=g.user_id, name=tree_name)
    db.session.add(new_tree)
    db.session.flush()

    new_person = Person(
        tree_id=new_tree.id,
        first_name=source.first_name,
        last_name=source.last_name,
        birth_year=source.birth_year,
        birth_state=source.birth_state,
        birth_country=source.birth_country,
        confidence=source.confidence,
    )
    db.session.add(new_person)
    db.session.commit()

    return jsonify({
        'tree_id': new_tree.id,
        'person_id': new_person.id,
        'tree_name': new_tree.name,
    }), 201
```

- [ ] **Step 4: Register `fork_bp` in `app/__init__.py`**

Add after the `collab_bp` registration:

```python
    from .fork_routes import fork_bp
    app.register_blueprint(fork_bp)
```

- [ ] **Step 5: Run fork tests**

```bash
cd /home/krish/familytree_app && source venv/bin/activate && pytest tests/test_fork_routes.py -v
```

Expected: All 6 PASS

- [ ] **Step 6: Add "Start New Tree From Here" button to `static/js/person_card.js`**

In `buildCardHTML()`, find the last `<button class="btn-secondary search-btn"` line (the AA Records button) and add the fork button immediately after it:

```javascript
          <button class="btn-secondary search-btn" onclick="runFork(${person.id})" title="Create a new tree starting from this person">Start New Tree From Here</button>
```

Then append `runFork()` at the end of `static/js/person_card.js`:

```javascript
async function runFork(personId) {
  const treeName = prompt('Name for the new tree (leave blank for default):');
  if (treeName === null) return; // user cancelled

  const r = await fetch(`/api/persons/${personId}/fork`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(treeName ? {tree_name: treeName} : {}),
  });

  if (!r.ok) {
    alert('Could not create tree. Please try again.');
    return;
  }

  const data = await r.json();
  document.getElementById('personCardOverlay').style.display = 'none';

  // Show toast
  const toast = document.createElement('div');
  toast.textContent = `New tree created — ${data.tree_name}`;
  toast.style.cssText = 'position:fixed;bottom:2rem;left:50%;transform:translateX(-50%);background:#1e40af;color:#fff;padding:.75rem 1.5rem;border-radius:8px;font-weight:600;z-index:9999';
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 3000);

  loadTree(data.tree_id);
}
```

- [ ] **Step 7: Run full suite**

```bash
cd /home/krish/familytree_app && source venv/bin/activate && pytest tests/ 2>&1 | tail -5
```

Expected: All tests pass (88+ total)

- [ ] **Step 8: Commit**

```bash
cd /home/krish/familytree_app && git add app/fork_routes.py app/__init__.py tests/test_fork_routes.py static/js/person_card.js && git commit -m "feat: fork person into new tree — accessible to collaborators, toast notification"
```

---

### Task 6: Referral system — codes, signup reward, subscription reward, UI

**Files:**
- Modify: `app/auth.py` — generate referral_code on register; award +25 tokens to referrer
- Modify: `app/stripe_routes.py` — award +150 tokens to referrer on first subscription
- Modify: `app/collab_routes.py` — add `GET /api/me/referral` endpoint
- Modify: `static/app.html` — add referral panel + invite panel to settings UI
- Create: `tests/test_referral.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_referral.py`:

```python
import json
from unittest.mock import patch, MagicMock
from app.models import User

def _register(client, email, ref_code=None):
    headers = {}
    environ_overrides = {}
    if ref_code:
        environ_overrides['HTTP_COOKIE'] = f'ref_code={ref_code}'
    return client.post('/auth/register',
        data=json.dumps({'email': email, 'password': 'pass'}),
        content_type='application/json',
        environ_overrides=environ_overrides)

def test_register_generates_referral_code(client, app):
    _register(client, 'gen@t.com')
    with app.app_context():
        u = User.query.filter_by(email='gen@t.com').first()
        assert u.referral_code is not None
        assert u.referral_code.startswith('RB-')

def test_referral_code_is_unique(client, app):
    _register(client, 'uniq1@t.com')
    client.post('/auth/logout')
    _register(client, 'uniq2@t.com')
    with app.app_context():
        u1 = User.query.filter_by(email='uniq1@t.com').first()
        u2 = User.query.filter_by(email='uniq2@t.com').first()
        assert u1.referral_code != u2.referral_code

def test_referral_signup_awards_25_tokens(client, app):
    _register(client, 'referrer@t.com')
    with app.app_context():
        referrer = User.query.filter_by(email='referrer@t.com').first()
        ref_code = referrer.referral_code
        initial_tokens = referrer.token_balance
    client.post('/auth/logout')
    _register(client, 'newbie@t.com', ref_code=ref_code)
    with app.app_context():
        referrer = User.query.filter_by(email='referrer@t.com').first()
        assert referrer.token_balance == initial_tokens + 25

def test_referred_by_set_on_register(client, app):
    _register(client, 'ref2@t.com')
    with app.app_context():
        referrer = User.query.filter_by(email='ref2@t.com').first()
        ref_code = referrer.referral_code
    client.post('/auth/logout')
    _register(client, 'newbie2@t.com', ref_code=ref_code)
    with app.app_context():
        newbie = User.query.filter_by(email='newbie2@t.com').first()
        referrer = User.query.filter_by(email='ref2@t.com').first()
        assert newbie.referred_by_user_id == referrer.id

def test_me_referral_endpoint(client, app):
    _register(client, 'refme@t.com')
    r = client.get('/api/me/referral')
    assert r.status_code == 200
    data = r.get_json()
    assert 'code' in data
    assert 'referral_url' in data
    assert data['referred_count'] == 0

def test_stripe_webhook_awards_referral_tokens(client, app):
    _register(client, 'wh_referrer@t.com')
    with app.app_context():
        referrer = User.query.filter_by(email='wh_referrer@t.com').first()
        ref_code = referrer.referral_code
        initial_tokens = referrer.token_balance
    client.post('/auth/logout')
    _register(client, 'wh_newbie@t.com', ref_code=ref_code)
    with app.app_context():
        from app.db import db
        newbie = User.query.filter_by(email='wh_newbie@t.com').first()
        newbie_id = newbie.id

    class FakeObj:
        def __getattr__(self, name): return getattr(self, f'_{name}', None)
        mode = 'subscription'
        metadata = {'user_id': str(newbie_id), 'tier': 'us'}
        customer = 'cus_test'
        subscription = 'sub_test'

    class FakeEvent:
        type = 'checkout.session.completed'
        class data:
            object = FakeObj()

    with patch('app.stripe_routes.stripe.Webhook.construct_event', return_value=FakeEvent()):
        client.post('/webhook/stripe',
            data='{}',
            content_type='application/json',
            headers={'Stripe-Signature': 'test'})

    with app.app_context():
        referrer = User.query.filter_by(email='wh_referrer@t.com').first()
        assert referrer.token_balance == initial_tokens + 25 + 150
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/krish/familytree_app && source venv/bin/activate && pytest tests/test_referral.py::test_register_generates_referral_code -v
```

Expected: FAIL (referral_code not generated in register)

- [ ] **Step 3: Update `app/auth.py` register route**

Replace the `register()` function body (keep the imports and `auth_bp` definition):

```python
@auth_bp.post('/auth/register')
def register():
    data = request.get_json()
    if not data or not data.get('email') or not data.get('password'):
        return jsonify({'error': 'Email and password required'}), 400
    if User.query.filter_by(email=data['email']).first():
        return jsonify({'error': 'Email already registered'}), 409
    password_hash = bcrypt.hashpw(data['password'].encode(), bcrypt.gensalt()).decode()

    import secrets as _secrets
    referral_code = 'RB-' + _secrets.token_hex(3).upper()
    while User.query.filter_by(referral_code=referral_code).first():
        referral_code = 'RB-' + _secrets.token_hex(3).upper()

    user = User(email=data['email'], password_hash=password_hash, referral_code=referral_code)

    ref_code = request.cookies.get('ref_code', '').strip()
    if ref_code:
        referrer = User.query.filter_by(referral_code=ref_code).first()
        if referrer and referrer.email != data['email']:
            user.referred_by_user_id = referrer.id
            referrer.token_balance += 25

    db.session.add(user)
    db.session.commit()
    token = create_token(user.id)
    resp = make_response(jsonify({'id': user.id, 'email': user.email, 'tier': user.tier}), 201)
    resp.set_cookie('auth_token', token, httponly=True, samesite='Lax', max_age=30*24*3600)
    resp.set_cookie('ref_code', '', expires=0)  # clear ref cookie after use
    return resp
```

- [ ] **Step 4: Update Stripe webhook in `app/stripe_routes.py`**

In the `stripe_webhook()` function, find the `if obj.mode == 'subscription' and meta.get('tier'):` block and replace it with:

```python
        if obj.mode == 'subscription' and meta.get('tier'):
            new_tier = meta['tier']
            is_first_sub = not user.stripe_subscription_id
            user.tier = new_tier
            user.token_balance = User.TIER_TOKENS.get(new_tier, 0)
            user.stripe_customer_id = obj.customer
            user.stripe_subscription_id = obj.subscription
            if is_first_sub and user.referred_by_user_id:
                referrer = User.query.get(user.referred_by_user_id)
                if referrer:
                    referrer.token_balance += 150
            db.session.commit()
```

- [ ] **Step 5: Add `GET /api/me/referral` to `app/collab_routes.py`**

Append to `app/collab_routes.py`:

```python
@collab_bp.get('/api/me/referral')
@require_auth
def me_referral():
    user = User.query.get(g.user_id)
    from flask import request as freq
    base_url = freq.host_url.rstrip('/')
    referral_url = f'{base_url}/?ref={user.referral_code}'
    referred_users = User.query.filter_by(referred_by_user_id=g.user_id).all()
    referred_count = len(referred_users)
    subscribed_count = sum(1 for u in referred_users if u.tier != 'free')
    tokens_earned = (referred_count * 25) + (subscribed_count * 150)
    return jsonify({
        'code': user.referral_code,
        'referral_url': referral_url,
        'referred_count': referred_count,
        'tokens_earned': tokens_earned,
    })
```

- [ ] **Step 6: Run referral tests**

```bash
cd /home/krish/familytree_app && source venv/bin/activate && pytest tests/test_referral.py -v
```

Expected: All 6 PASS

- [ ] **Step 7: Add referral panel to `static/app.html`**

Find the settings/profile section in `static/app.html`. Locate the `<div id="settingsPanel"` (or similar) and add this referral card HTML inside it. If settings panel doesn't exist, add it to the sidebar:

Read `static/app.html` first to find the exact insertion point, then add this block inside the settings/profile area:

```html
<!-- Referral Panel -->
<div id="referralPanel" style="display:none;margin-top:1.5rem">
  <h3 style="color:#f8fafc;font-size:1rem;margin-bottom:.75rem">Invite Family &amp; Friends</h3>
  <p style="color:#94a3b8;font-size:.85rem;margin-bottom:.75rem">
    Earn <strong style="color:#60a5fa">25 tokens</strong> when someone signs up with your link,
    <strong style="color:#60a5fa">150 more</strong> when they subscribe.
  </p>
  <div style="display:flex;gap:.5rem;align-items:center">
    <input id="referralLinkInput" type="text" readonly
      style="flex:1;background:#0f172a;border:1px solid #334155;color:#94a3b8;
             padding:.5rem;border-radius:6px;font-size:.8rem" />
    <button onclick="copyReferralLink()"
      style="padding:.5rem 1rem;background:#2563eb;color:#fff;border:none;
             border-radius:6px;cursor:pointer;font-size:.85rem;white-space:nowrap">
      Copy Link
    </button>
  </div>
  <p id="referralStats" style="color:#64748b;font-size:.8rem;margin-top:.5rem"></p>
</div>
```

Then add this JavaScript function to `static/app.html` (in the existing `<script>` block):

```javascript
async function loadReferral() {
  const r = await fetch('/api/me/referral');
  if (!r.ok) return;
  const d = await r.json();
  document.getElementById('referralLinkInput').value = d.referral_url;
  document.getElementById('referralStats').textContent =
    `${d.referred_count} people joined · ${d.tokens_earned} tokens earned`;
  document.getElementById('referralPanel').style.display = 'block';
}

function copyReferralLink() {
  const input = document.getElementById('referralLinkInput');
  navigator.clipboard.writeText(input.value).then(() => {
    const btn = event.target;
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = 'Copy Link'; }, 2000);
  });
}
```

Call `loadReferral()` from within the existing `loadMe()` function (append it at the end of `loadMe`).

Also add ref code cookie capture to `static/app.html`'s `<script>` block (or the landing page `static/index.html`):

```javascript
// Capture referral code from URL on page load
(function() {
  const params = new URLSearchParams(window.location.search);
  const ref = params.get('ref');
  if (ref) {
    document.cookie = `ref_code=${ref};path=/;max-age=${7*24*3600};samesite=Lax`;
  }
})();
```

- [ ] **Step 8: Run full test suite**

```bash
cd /home/krish/familytree_app && source venv/bin/activate && pytest tests/ -v 2>&1 | tail -10
```

Expected: All tests pass (94+ total)

- [ ] **Step 9: Commit**

```bash
cd /home/krish/familytree_app && git add app/auth.py app/stripe_routes.py app/collab_routes.py static/app.html tests/test_referral.py && git commit -m "feat: referral system — code generation on register, 25 tokens on signup, 150 on subscription, referral panel UI"
```

---

## Self-Review

**Spec coverage:**
- ✅ TreeInvite + TreeCollaborator models — Task 1
- ✅ referral_code + referred_by_user_id on User — Task 1
- ✅ `_can_access_tree()` helper — Task 2
- ✅ GET tree/person/hometown open to collaborators — Task 2
- ✅ POST /api/trees/<id>/invite — Task 3
- ✅ GET /api/invite/<token> (JSON preview) — Task 3
- ✅ POST /api/invite/<token>/accept — Task 3
- ✅ GET /api/trees/<id>/collaborators — Task 3
- ✅ DELETE /api/trees/<id>/collaborators/<user_id> — Task 3
- ✅ GET /invite/<token> (HTML landing page) — Task 3
- ✅ Gmail SMTP mailer with graceful fallback — Task 4
- ✅ Email called when invite has email field — Task 4
- ✅ POST /api/persons/<id>/fork — Task 5
- ✅ Fork accessible to collaborators (viewer or editor) — Task 5
- ✅ "Start New Tree From Here" button + toast — Task 5
- ✅ referral_code generated on register — Task 6
- ✅ +25 tokens to referrer on signup — Task 6
- ✅ +150 tokens to referrer on first subscription (Stripe webhook) — Task 6
- ✅ GET /api/me/referral endpoint — Task 6
- ✅ Referral panel in UI with copy-link button — Task 6
- ✅ ref code captured from URL into cookie — Task 6

**Placeholder scan:** None found. All functions, routes, and test assertions contain complete code.

**Type consistency:**
- `_can_access_tree(tree_id, user_id, require_editor=False)` — defined Task 2, used in Task 5 via `from .tree_routes import _can_access_tree`. Consistent.
- `TreeInvite.invite_token` — defined Task 1, used in Task 3 as `invite_token=token`. Consistent.
- `User.referral_code` — defined Task 1, populated Task 6, read in `/api/me/referral` Task 6. Consistent.
- `User.referred_by_user_id` — set in auth.py Task 6, read in stripe webhook Task 6. Consistent.
