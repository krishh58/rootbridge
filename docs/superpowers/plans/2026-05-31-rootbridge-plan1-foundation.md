# RootBridge Plan 1: Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Flask app skeleton, PostgreSQL data models, JWT user auth, Redis connection, landing/pricing pages, and Railway deployment — the foundation all other plans build on.

**Architecture:** Flask app factory pattern with SQLAlchemy models, bcrypt password hashing, JWT tokens in httpOnly cookies, and a `@require_auth` decorator. Static HTML/CSS pages served by Flask. All config via environment variables.

**Tech Stack:** Python 3.11, Flask 3.x, SQLAlchemy 2.x, psycopg2-binary, PyJWT, bcrypt, Redis (redis-py), gunicorn, pytest, Railway

**Spec:** `docs/superpowers/specs/2026-05-31-genealogy-gap-finder-design.md`

**This is Plan 1 of 5. Plans 2–5 build on this foundation.**

---

## File Structure

```
familytree_app/
├── app/
│   ├── __init__.py        # Flask app factory
│   ├── config.py          # Config from env vars
│   ├── models.py          # SQLAlchemy: User, Tree, Person, SearchResult, Gap
│   ├── db.py              # SQLAlchemy instance + init_db()
│   ├── auth.py            # Register/login/logout routes + require_auth decorator
│   └── routes.py          # Landing, pricing, health check, API stubs
├── static/
│   ├── index.html         # Landing page
│   ├── pricing.html       # Pricing page
│   ├── app.html           # Main SPA shell (tree loads here — Plan 3 fills this in)
│   └── style.css          # Shared dark theme styles
├── tests/
│   ├── conftest.py        # pytest fixtures: app, client, db
│   ├── test_models.py     # Model creation and field tests
│   └── test_auth.py       # Register, login, logout, protected route tests
├── requirements.txt
├── Procfile               # Railway: web: gunicorn "app:create_app()"
└── .env.example           # Document required env vars
```

---

### Task 1: Project scaffold and dependencies

**Files:**
- Create: `requirements.txt`
- Create: `Procfile`
- Create: `.env.example`
- Create: `app/__init__.py`
- Create: `app/config.py`

- [ ] **Step 1: Create requirements.txt**

```
Flask==3.0.3
Flask-SQLAlchemy==3.1.1
psycopg2-binary==2.9.9
PyJWT==2.8.0
bcrypt==4.1.3
redis==5.0.4
gunicorn==22.0.0
python-dotenv==1.0.1
pytest==8.2.0
pytest-flask==1.3.0
```

- [ ] **Step 2: Create Procfile**

```
web: gunicorn "app:create_app()"
```

- [ ] **Step 3: Create .env.example**

```
DATABASE_URL=postgresql://user:password@localhost:5432/rootbridge
REDIS_URL=redis://localhost:6379/0
SECRET_KEY=change-me-to-a-long-random-string
OPENROUTER_API_KEY=sk-or-v1-...
FAMILYSEARCH_CLIENT_ID=your-familysearch-app-id
FAMILYSEARCH_CLIENT_SECRET=your-familysearch-secret
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
FLASK_ENV=development
```

- [ ] **Step 4: Create app/config.py**

```python
import os

class Config:
    SECRET_KEY = os.environ['SECRET_KEY']
    SQLALCHEMY_DATABASE_URI = os.environ['DATABASE_URL']
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
    FAMILYSEARCH_CLIENT_ID = os.environ.get('FAMILYSEARCH_CLIENT_ID', '')
    FAMILYSEARCH_CLIENT_SECRET = os.environ.get('FAMILYSEARCH_CLIENT_SECRET', '')
    STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
    STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')

class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SECRET_KEY = 'test-secret-key'
    REDIS_URL = 'redis://localhost:6379/1'
    WTF_CSRF_ENABLED = False
```

- [ ] **Step 5: Create app/__init__.py**

```python
from flask import Flask
from .config import Config
from .db import db

def create_app(config=None):
    app = Flask(__name__, static_folder='../static', static_url_path='/static')
    app.config.from_object(config or Config)

    db.init_app(app)

    from .auth import auth_bp
    from .routes import routes_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(routes_bp)

    return app
```

- [ ] **Step 6: Install dependencies and verify import**

```bash
cd /home/krish/familytree_app
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 -c "from app import create_app; print('ok')"
```

Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add requirements.txt Procfile .env.example app/__init__.py app/config.py
git commit -m "feat: Flask app factory scaffold with config"
```

---

### Task 2: Database layer — SQLAlchemy instance and models

**Files:**
- Create: `app/db.py`
- Create: `app/models.py`
- Create: `tests/conftest.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Create app/db.py**

```python
from flask_sqlalchemy import SQLAlchemy
import redis as redis_lib
from flask import current_app

db = SQLAlchemy()

def get_redis():
    return redis_lib.from_url(current_app.config['REDIS_URL'], decode_responses=True)
```

- [ ] **Step 2: Create app/models.py**

```python
from datetime import datetime, timezone
from .db import db

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    tier = db.Column(db.String(20), default='free', nullable=False)
    token_balance = db.Column(db.Integer, default=100, nullable=False)
    token_balance_topup = db.Column(db.Integer, default=0, nullable=False)
    token_reset_date = db.Column(db.DateTime)
    stripe_customer_id = db.Column(db.String(255))
    stripe_subscription_id = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    trees = db.relationship('Tree', backref='owner', lazy=True, cascade='all, delete-orphan')

    TIER_TOKENS = {
        'free': 100,
        'us': 500,
        'european': 1000,
        'aa': 1000,
        'asian': 1000,
        'all': 2500,
    }

    def total_tokens(self):
        return self.token_balance + self.token_balance_topup

    def deduct_tokens(self, amount):
        if self.total_tokens() < amount:
            return False
        if self.token_balance >= amount:
            self.token_balance -= amount
        else:
            remainder = amount - self.token_balance
            self.token_balance = 0
            self.token_balance_topup -= remainder
        return True

class Tree(db.Model):
    __tablename__ = 'trees'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    share_token = db.Column(db.String(64), unique=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))
    persons = db.relationship('Person', backref='tree', lazy=True, cascade='all, delete-orphan')

class Person(db.Model):
    __tablename__ = 'persons'
    id = db.Column(db.Integer, primary_key=True)
    tree_id = db.Column(db.Integer, db.ForeignKey('trees.id'), nullable=False)
    first_name = db.Column(db.String(255))
    last_name = db.Column(db.String(255))
    birth_year = db.Column(db.Integer)
    birth_state = db.Column(db.String(100))
    birth_country = db.Column(db.String(100))
    death_year = db.Column(db.Integer)
    death_place = db.Column(db.String(255))
    notes = db.Column(db.Text)
    confidence = db.Column(db.Integer, default=0)
    parent_ids = db.Column(db.JSON, default=list)
    spouse_ids = db.Column(db.JSON, default=list)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    search_results = db.relationship('SearchResult', backref='person', lazy=True, cascade='all, delete-orphan')
    gaps = db.relationship('Gap', backref='person', lazy=True, cascade='all, delete-orphan')

class SearchResult(db.Model):
    __tablename__ = 'search_results'
    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=False)
    source = db.Column(db.String(50), nullable=False)
    record_type = db.Column(db.String(100))
    url = db.Column(db.Text)
    raw_data = db.Column(db.JSON)
    found_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class Gap(db.Model):
    __tablename__ = 'gaps'
    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=False)
    gap_type = db.Column(db.String(100), nullable=False)
    suggested_source = db.Column(db.String(255))
    suggested_query = db.Column(db.Text)
    resolved = db.Column(db.Boolean, default=False)
```

- [ ] **Step 3: Create tests/conftest.py**

```python
import pytest
from app import create_app
from app.config import TestConfig
from app.db import db as _db

@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        _db.create_all()
        yield app
        _db.drop_all()

@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture
def db(app):
    return _db
```

- [ ] **Step 4: Write failing tests in tests/test_models.py**

```python
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
```

- [ ] **Step 5: Run tests to verify they fail**

```bash
cd /home/krish/familytree_app
source venv/bin/activate
pytest tests/test_models.py -v
```

Expected: FAIL — `app.auth` and `app.routes` modules not found yet (blueprints missing)

- [ ] **Step 6: Create placeholder blueprints so app factory works**

Create `app/auth.py`:
```python
from flask import Blueprint
auth_bp = Blueprint('auth', __name__)
```

Create `app/routes.py`:
```python
from flask import Blueprint
routes_bp = Blueprint('routes', __name__)
```

- [ ] **Step 7: Run tests again**

```bash
pytest tests/test_models.py -v
```

Expected: All 7 tests PASS

- [ ] **Step 8: Commit**

```bash
git add app/db.py app/models.py app/auth.py app/routes.py tests/conftest.py tests/test_models.py
git commit -m "feat: SQLAlchemy models — User, Tree, Person, SearchResult, Gap"
```

---

### Task 3: User authentication — register, login, logout

**Files:**
- Modify: `app/auth.py`
- Create: `tests/test_auth.py`

- [ ] **Step 1: Write failing auth tests in tests/test_auth.py**

```python
import json

def test_register_success(client):
    r = client.post('/auth/register',
        data=json.dumps({'email': 'user@test.com', 'password': 'pass1234'}),
        content_type='application/json')
    assert r.status_code == 201
    data = r.get_json()
    assert data['email'] == 'user@test.com'
    assert data['tier'] == 'free'
    assert 'auth_token' in r.headers.get('Set-Cookie', '')

def test_register_duplicate_email(client):
    payload = json.dumps({'email': 'dup@test.com', 'password': 'pass1234'})
    client.post('/auth/register', data=payload, content_type='application/json')
    r = client.post('/auth/register', data=payload, content_type='application/json')
    assert r.status_code == 409
    assert r.get_json()['error'] == 'Email already registered'

def test_register_missing_fields(client):
    r = client.post('/auth/register',
        data=json.dumps({'email': 'x@test.com'}),
        content_type='application/json')
    assert r.status_code == 400

def test_login_success(client):
    client.post('/auth/register',
        data=json.dumps({'email': 'login@test.com', 'password': 'mypass'}),
        content_type='application/json')
    r = client.post('/auth/login',
        data=json.dumps({'email': 'login@test.com', 'password': 'mypass'}),
        content_type='application/json')
    assert r.status_code == 200
    assert r.get_json()['email'] == 'login@test.com'
    assert 'auth_token' in r.headers.get('Set-Cookie', '')

def test_login_wrong_password(client):
    client.post('/auth/register',
        data=json.dumps({'email': 'wp@test.com', 'password': 'correct'}),
        content_type='application/json')
    r = client.post('/auth/login',
        data=json.dumps({'email': 'wp@test.com', 'password': 'wrong'}),
        content_type='application/json')
    assert r.status_code == 401

def test_login_unknown_email(client):
    r = client.post('/auth/login',
        data=json.dumps({'email': 'nobody@test.com', 'password': 'x'}),
        content_type='application/json')
    assert r.status_code == 401

def test_logout_clears_cookie(client):
    client.post('/auth/register',
        data=json.dumps({'email': 'lo@test.com', 'password': 'pass'}),
        content_type='application/json')
    r = client.post('/auth/logout')
    assert r.status_code == 200
    cookie = r.headers.get('Set-Cookie', '')
    assert 'auth_token=;' in cookie or 'auth_token=""' in cookie or 'Expires=Thu, 01 Jan 1970' in cookie

def test_protected_route_no_token(client):
    r = client.get('/api/me')
    assert r.status_code == 401

def test_protected_route_with_token(client):
    client.post('/auth/register',
        data=json.dumps({'email': 'me@test.com', 'password': 'pass'}),
        content_type='application/json')
    r = client.get('/api/me')
    assert r.status_code == 200
    assert r.get_json()['email'] == 'me@test.com'
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_auth.py -v
```

Expected: FAIL — routes not implemented

- [ ] **Step 3: Implement app/auth.py**

```python
from flask import Blueprint, request, jsonify, make_response, g
from functools import wraps
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from .db import db
from .models import User
from flask import current_app

auth_bp = Blueprint('auth', __name__)

def create_token(user_id: int) -> str:
    payload = {
        'user_id': user_id,
        'exp': datetime.now(timezone.utc) + timedelta(days=30)
    }
    return jwt.encode(payload, current_app.config['SECRET_KEY'], algorithm='HS256')

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.cookies.get('auth_token')
        if not token:
            return jsonify({'error': 'Authentication required'}), 401
        try:
            payload = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
            g.user_id = payload['user_id']
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401
        return f(*args, **kwargs)
    return decorated

@auth_bp.post('/auth/register')
def register():
    data = request.get_json()
    if not data or not data.get('email') or not data.get('password'):
        return jsonify({'error': 'Email and password required'}), 400
    if User.query.filter_by(email=data['email']).first():
        return jsonify({'error': 'Email already registered'}), 409
    password_hash = bcrypt.hashpw(data['password'].encode(), bcrypt.gensalt()).decode()
    user = User(email=data['email'], password_hash=password_hash)
    db.session.add(user)
    db.session.commit()
    token = create_token(user.id)
    resp = make_response(jsonify({'id': user.id, 'email': user.email, 'tier': user.tier}), 201)
    resp.set_cookie('auth_token', token, httponly=True, samesite='Lax', max_age=30*24*3600)
    return resp

@auth_bp.post('/auth/login')
def login():
    data = request.get_json()
    if not data or not data.get('email') or not data.get('password'):
        return jsonify({'error': 'Email and password required'}), 400
    user = User.query.filter_by(email=data['email']).first()
    if not user or not bcrypt.checkpw(data['password'].encode(), user.password_hash.encode()):
        return jsonify({'error': 'Invalid credentials'}), 401
    token = create_token(user.id)
    resp = make_response(jsonify({'id': user.id, 'email': user.email, 'tier': user.tier}))
    resp.set_cookie('auth_token', token, httponly=True, samesite='Lax', max_age=30*24*3600)
    return resp

@auth_bp.post('/auth/logout')
def logout():
    resp = make_response(jsonify({'ok': True}))
    resp.set_cookie('auth_token', '', httponly=True, expires=0)
    return resp
```

- [ ] **Step 4: Add /api/me route to app/routes.py**

```python
from flask import Blueprint, jsonify, g, send_from_directory, current_app
import os
from .auth import require_auth
from .db import db
from .models import User

routes_bp = Blueprint('routes', __name__)

@routes_bp.get('/health')
def health():
    return jsonify({'status': 'ok'})

@routes_bp.get('/api/me')
@require_auth
def me():
    user = db.session.get(User, g.user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    return jsonify({
        'id': user.id,
        'email': user.email,
        'tier': user.tier,
        'tokens': user.total_tokens(),
    })

@routes_bp.get('/')
def index():
    return send_from_directory(os.path.join(current_app.root_path, '..', 'static'), 'index.html')

@routes_bp.get('/pricing')
def pricing():
    return send_from_directory(os.path.join(current_app.root_path, '..', 'static'), 'pricing.html')

@routes_bp.get('/app')
@require_auth
def app_shell():
    return send_from_directory(os.path.join(current_app.root_path, '..', 'static'), 'app.html')
```

- [ ] **Step 5: Run auth tests**

```bash
pytest tests/test_auth.py -v
```

Expected: All 9 tests PASS

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/ -v
```

Expected: All 16 tests (7 model + 9 auth) PASS

- [ ] **Step 7: Commit**

```bash
git add app/auth.py app/routes.py tests/test_auth.py
git commit -m "feat: user auth — register, login, logout, require_auth decorator"
```

---

### Task 4: Static pages — landing, pricing, app shell

**Files:**
- Create: `static/style.css`
- Create: `static/index.html`
- Create: `static/pricing.html`
- Create: `static/app.html`

- [ ] **Step 1: Create static/style.css**

```css
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg: #0f172a;
  --surface: #1e293b;
  --border: #334155;
  --text: #f1f5f9;
  --muted: #94a3b8;
  --blue: #3b82f6;
  --green: #4ade80;
  --red: #f87171;
  --yellow: #fbbf24;
  --purple: #a855f7;
}

body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif; line-height: 1.6; }

nav {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 32px; border-bottom: 1px solid var(--border);
  position: sticky; top: 0; background: var(--bg); z-index: 100;
}
.logo { font-size: 1.2rem; font-weight: 700; color: var(--blue); text-decoration: none; }
.nav-links a { color: var(--muted); text-decoration: none; margin-left: 24px; font-size: 0.9rem; }
.nav-links a:hover { color: var(--text); }
.token-badge {
  background: var(--surface); border: 1px solid var(--border);
  padding: 4px 12px; border-radius: 99px; font-size: 0.8rem; color: var(--yellow);
}

.hero { text-align: center; padding: 80px 20px 60px; }
.hero h1 { font-size: 2.8rem; font-weight: 800; margin-bottom: 16px; }
.hero h1 span { color: var(--blue); }
.hero p { font-size: 1.1rem; color: var(--muted); max-width: 560px; margin: 0 auto 32px; }

.btn {
  display: inline-block; padding: 12px 28px; border-radius: 8px;
  font-weight: 600; text-decoration: none; cursor: pointer; border: none; font-size: 1rem;
}
.btn-primary { background: var(--blue); color: white; }
.btn-primary:hover { background: #2563eb; }
.btn-ghost { background: transparent; color: var(--text); border: 1px solid var(--border); }
.btn-ghost:hover { border-color: var(--blue); color: var(--blue); }

.features {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 20px; padding: 60px 32px; max-width: 1100px; margin: 0 auto;
}
.feature-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 24px;
}
.feature-card h3 { font-size: 1rem; margin-bottom: 8px; }
.feature-card p { font-size: 0.875rem; color: var(--muted); }

.section-title { text-align: center; font-size: 1.8rem; font-weight: 700; padding: 48px 20px 8px; }
.section-sub { text-align: center; color: var(--muted); padding-bottom: 40px; }

.pricing-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 16px; padding: 0 32px 60px; max-width: 1100px; margin: 0 auto;
}
.price-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 20px;
}
.price-card.featured { border-color: var(--blue); }
.price-card .price { font-size: 1.8rem; font-weight: 800; margin: 8px 0; }
.price-card .price span { font-size: 0.85rem; color: var(--muted); font-weight: 400; }
.price-card ul { list-style: none; margin-top: 12px; }
.price-card ul li { font-size: 0.85rem; color: var(--muted); padding: 3px 0; }
.price-card ul li::before { content: '✓ '; color: var(--green); }

footer { text-align: center; padding: 32px; color: var(--muted); font-size: 0.85rem; border-top: 1px solid var(--border); }
```

- [ ] **Step 2: Create static/index.html**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>RootBridge — Find Your Family Gaps</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<nav>
  <a href="/" class="logo">RootBridge</a>
  <div class="nav-links">
    <a href="/pricing">Pricing</a>
    <a href="#" id="loginLink">Sign In</a>
    <a href="#" class="btn btn-primary" style="padding:8px 18px;font-size:0.85rem" id="signupLink">Start Free</a>
  </div>
</nav>

<section class="hero">
  <h1>Find Every Gap in Your <span>Family History</span></h1>
  <p>RootBridge searches FamilySearch, ship manifests, Freedmen's Bureau records, and more — then tells you exactly what's missing and where to find it. Up to 87% cheaper than Ancestry.</p>
  <a href="#search" class="btn btn-primary" style="margin-right:12px">Search Your Family Free</a>
  <a href="/pricing" class="btn btn-ghost">See Pricing</a>
</section>

<section id="search" style="max-width:600px;margin:0 auto;padding:0 20px 60px">
  <div style="background:#1e293b;border:1px solid #334155;border-radius:12px;padding:28px">
    <h2 style="margin-bottom:20px;font-size:1.2rem">Start with what you know</h2>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
      <div>
        <label style="font-size:0.8rem;color:#94a3b8;display:block;margin-bottom:4px">First Name</label>
        <input id="firstName" type="text" placeholder="Christopher" style="width:100%;background:#0f172a;border:1px solid #334155;border-radius:6px;padding:8px 12px;color:#f1f5f9;font-size:0.9rem">
      </div>
      <div>
        <label style="font-size:0.8rem;color:#94a3b8;display:block;margin-bottom:4px">Last Name *</label>
        <input id="lastName" type="text" placeholder="Haines" style="width:100%;background:#0f172a;border:1px solid #334155;border-radius:6px;padding:8px 12px;color:#f1f5f9;font-size:0.9rem">
      </div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px">
      <div>
        <label style="font-size:0.8rem;color:#94a3b8;display:block;margin-bottom:4px">Birth Year (approx)</label>
        <input id="birthYear" type="number" placeholder="1760" style="width:100%;background:#0f172a;border:1px solid #334155;border-radius:6px;padding:8px 12px;color:#f1f5f9;font-size:0.9rem">
      </div>
      <div>
        <label style="font-size:0.8rem;color:#94a3b8;display:block;margin-bottom:4px">Birth State / Country</label>
        <input id="birthPlace" type="text" placeholder="Virginia" style="width:100%;background:#0f172a;border:1px solid #334155;border-radius:6px;padding:8px 12px;color:#f1f5f9;font-size:0.9rem">
      </div>
    </div>
    <button class="btn btn-primary" style="width:100%" onclick="guestSearch()">Find Gaps →</button>
    <p style="text-align:center;font-size:0.8rem;color:#64748b;margin-top:10px">Free · No account required · 5 searches/day</p>
  </div>
</section>

<div class="features">
  <div class="feature-card">
    <h3>🔍 Gap Finder</h3>
    <p>Searches FamilySearch, WikiTree, Census records, and more. Shows you exactly what's missing and where to look next.</p>
  </div>
  <div class="feature-card">
    <h3>🌍 Crosses the Atlantic</h3>
    <p>Ship manifests → origin village → European church records. German, Irish, Italian, Polish ancestry fully supported.</p>
  </div>
  <div class="feature-card">
    <h3>✊ The 1870 Wall</h3>
    <p>Specialized Freedmen's Bureau, slave schedule, and WPA narrative searches for African American genealogy.</p>
  </div>
  <div class="feature-card">
    <h3>🏘️ See Where They Lived</h3>
    <p>Photos of your ancestor's hometown, historical maps of their region, and AI context on what life was like when they were born.</p>
  </div>
</div>

<footer>
  © 2026 RootBridge &nbsp;·&nbsp; <a href="/pricing" style="color:#64748b">Pricing</a>
</footer>

<script>
function guestSearch() {
  const last = document.getElementById('lastName').value.trim();
  if (!last) { alert('Last name is required'); return; }
  const params = new URLSearchParams({
    first: document.getElementById('firstName').value.trim(),
    last,
    birth_year: document.getElementById('birthYear').value.trim(),
    birth_place: document.getElementById('birthPlace').value.trim(),
  });
  window.location.href = '/app?' + params.toString();
}
</script>
</body>
</html>
```

- [ ] **Step 3: Create static/pricing.html**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Pricing — RootBridge</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<nav>
  <a href="/" class="logo">RootBridge</a>
  <div class="nav-links">
    <a href="/">Home</a>
    <a href="#" id="loginLink">Sign In</a>
  </div>
</nav>

<h2 class="section-title">Every Heritage Has a Different Research Challenge</h2>
<p class="section-sub">Search cascade, databases, and difficulty vary by background. That drives our pricing.</p>

<div class="pricing-grid">
  <div class="price-card">
    <div style="color:#94a3b8;font-size:0.8rem;font-weight:600">FREE</div>
    <div class="price">$0</div>
    <div style="font-size:0.8rem;color:#64748b">100 tokens/mo</div>
    <ul>
      <li>5 searches per day</li>
      <li>View gap report</li>
      <li style="color:#f87171;text-decoration:line-through">Save tree</li>
      <li style="color:#f87171;text-decoration:line-through">PDF export</li>
    </ul>
    <a href="/" class="btn btn-ghost" style="width:100%;text-align:center;margin-top:16px">Start Free</a>
  </div>
  <div class="price-card featured">
    <div style="color:#60a5fa;font-size:0.8rem;font-weight:600">🇺🇸 US RECORDS</div>
    <div class="price">$7.99<span>/mo</span></div>
    <div style="font-size:0.8rem;color:#64748b">500 tokens/mo · $63.99/yr</div>
    <ul>
      <li>Unlimited searches</li>
      <li>Save & return to tree</li>
      <li>PDF export</li>
      <li>Census 1790–1950</li>
      <li>Military pensions</li>
      <li>AI gap analysis</li>
    </ul>
    <a href="#" class="btn btn-primary" style="width:100%;text-align:center;margin-top:16px">Subscribe</a>
  </div>
  <div class="price-card">
    <div style="color:#c084fc;font-size:0.8rem;font-weight:600">🌍 + EUROPEAN ROOTS</div>
    <div class="price">$12.99<span>/mo</span></div>
    <div style="font-size:0.8rem;color:#64748b">1,000 tokens/mo · $103.99/yr</div>
    <ul>
      <li>Everything in US tier</li>
      <li>Ship manifests → village</li>
      <li>German/Irish/Italian/Polish</li>
      <li>AI document translation</li>
      <li>Matricula, Riksarkivet</li>
    </ul>
    <a href="#" class="btn btn-primary" style="width:100%;text-align:center;margin-top:16px">Subscribe</a>
  </div>
  <div class="price-card">
    <div style="color:#fbbf24;font-size:0.8rem;font-weight:600">✊ AFRICAN AMERICAN</div>
    <div class="price">$12.99<span>/mo</span></div>
    <div style="font-size:0.8rem;color:#64748b">1,000 tokens/mo · $103.99/yr</div>
    <ul>
      <li>Everything in US tier</li>
      <li>Freedmen's Bureau records</li>
      <li>Slave schedules 1850–1860</li>
      <li>WPA Slave Narratives</li>
      <li>1870 wall guidance</li>
    </ul>
    <a href="#" class="btn btn-primary" style="width:100%;text-align:center;margin-top:16px">Subscribe</a>
  </div>
  <div class="price-card" style="border:2px solid #f1f5f9">
    <div style="color:#f1f5f9;font-size:0.8rem;font-weight:600">🌐 ALL ACCESS</div>
    <div class="price">$19.99<span>/mo</span></div>
    <div style="font-size:0.8rem;color:#4ade80">2,500 tokens/mo · $149/yr (save 38%)</div>
    <ul>
      <li>Every tier combined</li>
      <li>Asian/Pacific records</li>
      <li>All AI translation</li>
      <li>All databases</li>
      <li>67% cheaper than Ancestry</li>
    </ul>
    <a href="#" class="btn btn-primary" style="width:100%;text-align:center;margin-top:16px;background:#f1f5f9;color:#0f172a">Subscribe</a>
  </div>
</div>

<div style="max-width:700px;margin:0 auto 60px;padding:0 32px;text-align:center">
  <h3 style="margin-bottom:12px">Need more AI power?</h3>
  <p style="color:#94a3b8;font-size:0.9rem;margin-bottom:20px">Top-up packs never expire and cover heavy research sessions — deep European cascades, batch document translation, or a weekend deep-dive into your family history.</p>
  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;font-size:0.85rem">
    <div style="background:#1e293b;border:1px solid #334155;border-radius:8px;padding:12px">
      <div style="font-weight:700">$2.99</div><div style="color:#94a3b8">300 tokens</div>
    </div>
    <div style="background:#1e293b;border:1px solid #334155;border-radius:8px;padding:12px">
      <div style="font-weight:700">$7.99</div><div style="color:#94a3b8">1,000 tokens</div>
    </div>
    <div style="background:#1e293b;border:1px solid #334155;border-radius:8px;padding:12px">
      <div style="font-weight:700">$14.99</div><div style="color:#94a3b8">2,500 tokens</div>
    </div>
    <div style="background:#1e293b;border:1px solid #334155;border-radius:8px;padding:12px">
      <div style="font-weight:700">$24.99</div><div style="color:#94a3b8">5,000 tokens</div>
    </div>
  </div>
</div>

<footer>© 2026 RootBridge &nbsp;·&nbsp; <a href="/" style="color:#64748b">Home</a></footer>
</body>
</html>
```

- [ ] **Step 4: Create static/app.html (SPA shell — tree and search UI goes here in Plan 3)**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>RootBridge</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<nav>
  <a href="/" class="logo">RootBridge</a>
  <div class="nav-links">
    <span class="token-badge" id="tokenBadge">⚡ — tokens</span>
    <a href="/pricing">Top Up</a>
    <a href="#" onclick="logout()">Sign Out</a>
  </div>
</nav>

<div id="app" style="padding:40px 32px;text-align:center;color:#94a3b8">
  <p>Loading your tree...</p>
</div>

<script>
async function logout() {
  await fetch('/auth/logout', { method: 'POST' });
  window.location.href = '/';
}

async function loadMe() {
  const r = await fetch('/api/me');
  if (!r.ok) { window.location.href = '/'; return; }
  const data = await r.json();
  document.getElementById('tokenBadge').textContent = `⚡ ${data.tokens} tokens`;
}

loadMe();
// Tree rendering and search UI added in Plan 3
</script>
</body>
</html>
```

- [ ] **Step 5: Verify pages load**

```bash
cd /home/krish/familytree_app
source venv/bin/activate
SECRET_KEY=test DATABASE_URL=sqlite:///test.db REDIS_URL=redis://localhost:6379/0 \
  flask --app "app:create_app()" run --port 5050 &
sleep 2
curl -s http://localhost:5050/ | grep -c "RootBridge"
curl -s http://localhost:5050/pricing | grep -c "pricing"
curl -s http://localhost:5050/health
kill %1
```

Expected: `1`, `1`, `{"status":"ok"}`

- [ ] **Step 6: Commit**

```bash
git add static/
git commit -m "feat: landing page, pricing page, app shell with dark theme"
```

---

### Task 5: Railway deployment

**Files:**
- Create: `railway.toml`
- Modify: `app/config.py` (handle Railway's DATABASE_URL format)

- [ ] **Step 1: Railway rewrites postgres:// to postgresql:// — fix config.py**

```python
import os

class Config:
    _raw_db_url = os.environ.get('DATABASE_URL', '')
    SECRET_KEY = os.environ['SECRET_KEY']
    SQLALCHEMY_DATABASE_URI = _raw_db_url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
    FAMILYSEARCH_CLIENT_ID = os.environ.get('FAMILYSEARCH_CLIENT_ID', '')
    FAMILYSEARCH_CLIENT_SECRET = os.environ.get('FAMILYSEARCH_CLIENT_SECRET', '')
    STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
    STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')

class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SECRET_KEY = 'test-secret-key'
    REDIS_URL = 'redis://localhost:6379/1'
```

- [ ] **Step 2: Create railway.toml**

```toml
[build]
builder = "NIXPACKS"

[deploy]
startCommand = "gunicorn 'app:create_app()' --bind 0.0.0.0:$PORT --workers 2"
healthcheckPath = "/health"
healthcheckTimeout = 30
restartPolicyType = "ON_FAILURE"
```

- [ ] **Step 3: Add db init command to app/__init__.py**

The app needs to create tables on first deploy. Update `create_app()`:

```python
from flask import Flask
from .config import Config
from .db import db

def create_app(config=None):
    app = Flask(__name__, static_folder='../static', static_url_path='/static')
    app.config.from_object(config or Config)

    db.init_app(app)

    from .auth import auth_bp
    from .routes import routes_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(routes_bp)

    with app.app_context():
        db.create_all()

    return app
```

- [ ] **Step 4: Run full test suite one last time**

```bash
cd /home/krish/familytree_app
source venv/bin/activate
pytest tests/ -v
```

Expected: All 16 tests PASS

- [ ] **Step 5: Deploy to Railway**

```bash
# Install Railway CLI if not present
npm install -g @railway/cli

# Login and deploy
railway login
railway init  # name the project rootbridge
railway add   # add PostgreSQL plugin
railway add   # add Redis plugin
railway up
```

After deploy, set environment variables in Railway dashboard:
- `SECRET_KEY` — generate with `python3 -c "import secrets; print(secrets.token_hex(32))"`
- `OPENROUTER_API_KEY` — your OpenRouter key
- `FAMILYSEARCH_CLIENT_ID` — from FamilySearch developer portal (free signup)
- `FAMILYSEARCH_CLIENT_SECRET` — from FamilySearch developer portal
- `STRIPE_SECRET_KEY` — from Stripe dashboard (test key for now: `sk_test_...`)
- `STRIPE_WEBHOOK_SECRET` — from Stripe webhook setup (Plan 4)

DATABASE_URL and REDIS_URL are injected automatically by Railway plugins.

- [ ] **Step 6: Verify deployment**

```bash
railway status
# Get your deployment URL from Railway dashboard, then:
curl https://your-app.railway.app/health
```

Expected: `{"status":"ok"}`

- [ ] **Step 7: Final commit**

```bash
git add railway.toml app/__init__.py app/config.py
git commit -m "feat: Railway deployment config, db auto-create on startup"
```

---

## Self-Review

**Spec coverage check:**
- ✅ Flask backend — Task 1
- ✅ PostgreSQL models (User, Tree, Person, SearchResult, Gap) — Task 2
- ✅ User auth (register/login/logout, JWT httpOnly cookie) — Task 3
- ✅ `@require_auth` decorator — Task 3
- ✅ `/health`, `/api/me`, `/`, `/pricing`, `/app` routes — Tasks 3–4
- ✅ Token balance + deduction logic on User model — Task 2
- ✅ Redis connection via `get_redis()` — Task 2 (used in Plans 2 and 4)
- ✅ Landing page with search form — Task 4
- ✅ Pricing page with all tiers + top-up packs — Task 4
- ✅ App shell with token badge in nav — Task 4
- ✅ Railway deploy — Task 5
- ✅ Tests for all models and auth — Tasks 2–3
- ⏭️ FamilySearch OAuth2 — Plan 2
- ⏭️ Stripe webhooks — Plan 4
- ⏭️ D3.js tree — Plan 3
- ⏭️ Rate limiting (5/day for guests via Redis) — Plan 2

**Placeholder scan:** None found. All code blocks are complete.

**Type consistency:** `User.deduct_tokens()` defined in Task 2 models.py, referenced correctly in Plan 4 (not yet written — consistent with method signature).
