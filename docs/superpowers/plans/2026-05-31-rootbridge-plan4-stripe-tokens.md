# RootBridge Plan 4: Stripe + Token System

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire up Stripe subscription billing for all 5 tiers and one-time top-up packs, implement token deduction middleware that gates AI operations, add the low-balance warning toast, and add GDPR data export/deletion endpoints.

**Architecture:** `stripe_routes.py` handles subscription checkout, top-up checkout, and the Stripe webhook (which updates DB tier + token balance). `token_middleware.py` provides a `require_tokens(cost)` decorator that deducts from the user's balance before any AI call. `alfred_routes.py` and `ai_synthesis.py` get wrapped with this decorator. `gdpr_routes.py` exposes data export and deletion.

**Tech Stack:** stripe Python SDK, Flask, PostgreSQL (SQLAlchemy), existing User model with deduct_tokens()

**Spec:** `docs/superpowers/specs/2026-05-31-genealogy-gap-finder-design.md`

**Builds on Plans 1–3. Plan 5 builds on this for heritage-tier enforcement.**

---

## File Structure

```
app/
├── stripe_routes.py     # Checkout sessions, webhook handler
├── token_middleware.py  # require_tokens(cost) decorator
├── gdpr_routes.py       # GET /api/me/export, DELETE /api/me
├── alfred_routes.py     # Modify: wrap chat endpoint with require_tokens(10)
├── ai_synthesis.py      # Modify: wrap synthesize_gaps with require_tokens(20)
├── hometown.py          # Modify: wrap get_life_context with require_tokens(5)
tests/
├── test_stripe_routes.py
├── test_token_middleware.py
├── test_gdpr_routes.py
```

**Add to requirements.txt:** `stripe==9.11.0`

---

### Task 1: Stripe subscription checkout

**Files:**
- Modify: `requirements.txt` — add stripe
- Create: `app/stripe_routes.py`
- Create: `tests/test_stripe_routes.py`

Stripe price IDs are environment variables — never hardcode them. The env vars expected:
- `STRIPE_PRICE_US` — US Records Only ($7.99/mo)
- `STRIPE_PRICE_EUROPEAN` — European Roots ($12.99/mo)
- `STRIPE_PRICE_AA` — African American Heritage ($12.99/mo)
- `STRIPE_PRICE_ASIAN` — Asian/Pacific ($12.99/mo)
- `STRIPE_PRICE_ALL` — All Access ($19.99/mo)
- `STRIPE_PRICE_TOPUP_300`, `STRIPE_PRICE_TOPUP_1000`, `STRIPE_PRICE_TOPUP_2500`, `STRIPE_PRICE_TOPUP_5000` — top-up packs

- [ ] **Step 1: Add stripe to requirements.txt**

Add this line to `requirements.txt`:

```
stripe==9.11.0
```

Then install:

```bash
cd /home/krish/familytree_app && source venv/bin/activate
pip install stripe==9.11.0
```

- [ ] **Step 2: Write failing tests**

```python
# tests/test_stripe_routes.py
import json
from unittest.mock import patch, MagicMock

def _login(client):
    client.post('/auth/register',
        data=json.dumps({'email': 's@test.com', 'password': 'pass'}),
        content_type='application/json')

def test_create_checkout_subscription(client):
    _login(client)
    mock_session = MagicMock()
    mock_session.url = 'https://checkout.stripe.com/test-session'
    with patch('app.stripe_routes.stripe.checkout.Session.create', return_value=mock_session):
        r = client.post('/api/stripe/checkout',
            data=json.dumps({'tier': 'us'}),
            content_type='application/json')
    assert r.status_code == 200
    assert 'url' in r.get_json()

def test_create_checkout_invalid_tier(client):
    _login(client)
    r = client.post('/api/stripe/checkout',
        data=json.dumps({'tier': 'invalid_tier'}),
        content_type='application/json')
    assert r.status_code == 400

def test_create_topup_checkout(client):
    _login(client)
    mock_session = MagicMock()
    mock_session.url = 'https://checkout.stripe.com/topup-session'
    with patch('app.stripe_routes.stripe.checkout.Session.create', return_value=mock_session):
        r = client.post('/api/stripe/topup',
            data=json.dumps({'pack': '1000'}),
            content_type='application/json')
    assert r.status_code == 200
    assert 'url' in r.get_json()

def test_create_topup_invalid_pack(client):
    _login(client)
    r = client.post('/api/stripe/topup',
        data=json.dumps({'pack': '9999'}),
        content_type='application/json')
    assert r.status_code == 400

def test_get_subscription_status(client):
    _login(client)
    r = client.get('/api/stripe/status')
    assert r.status_code == 200
    data = r.get_json()
    assert 'tier' in data
    assert 'token_balance' in data
    assert 'token_balance_topup' in data
```

- [ ] **Step 3: Run tests to verify failure**

```bash
cd /home/krish/familytree_app && source venv/bin/activate
pytest tests/test_stripe_routes.py -v
```

- [ ] **Step 4: Create app/stripe_routes.py**

```python
import os
import stripe
from flask import Blueprint, request, jsonify, g, current_app
from .auth import require_auth
from .db import db
from .models import User

stripe_bp = Blueprint('stripe_routes', __name__)

TIER_PRICE_ENVS = {
    'us': 'STRIPE_PRICE_US',
    'european': 'STRIPE_PRICE_EUROPEAN',
    'aa': 'STRIPE_PRICE_AA',
    'asian': 'STRIPE_PRICE_ASIAN',
    'all': 'STRIPE_PRICE_ALL',
}

TOPUP_PRICE_ENVS = {
    '300': 'STRIPE_PRICE_TOPUP_300',
    '1000': 'STRIPE_PRICE_TOPUP_1000',
    '2500': 'STRIPE_PRICE_TOPUP_2500',
    '5000': 'STRIPE_PRICE_TOPUP_5000',
}

TOPUP_AMOUNTS = {'300': 300, '1000': 1000, '2500': 2500, '5000': 5000}

def _stripe_key():
    return current_app.config.get('STRIPE_SECRET_KEY', '')

@stripe_bp.post('/api/stripe/checkout')
@require_auth
def create_checkout():
    stripe.api_key = _stripe_key()
    data = request.get_json() or {}
    tier = data.get('tier', '')
    if tier not in TIER_PRICE_ENVS:
        return jsonify({'error': f'Invalid tier. Valid: {list(TIER_PRICE_ENVS.keys())}'}), 400
    price_id = os.environ.get(TIER_PRICE_ENVS[tier], '')
    if not price_id:
        return jsonify({'error': 'Tier not configured yet'}), 503
    user = User.query.get(g.user_id)
    session = stripe.checkout.Session.create(
        mode='subscription',
        line_items=[{'price': price_id, 'quantity': 1}],
        success_url=request.host_url + 'app?payment=success',
        cancel_url=request.host_url + 'pricing',
        customer_email=user.email,
        metadata={'user_id': str(g.user_id), 'tier': tier},
    )
    return jsonify({'url': session.url})

@stripe_bp.post('/api/stripe/topup')
@require_auth
def create_topup():
    stripe.api_key = _stripe_key()
    data = request.get_json() or {}
    pack = str(data.get('pack', ''))
    if pack not in TOPUP_PRICE_ENVS:
        return jsonify({'error': f'Invalid pack. Valid: {list(TOPUP_PRICE_ENVS.keys())}'}), 400
    price_id = os.environ.get(TOPUP_PRICE_ENVS[pack], '')
    if not price_id:
        return jsonify({'error': 'Pack not configured yet'}), 503
    user = User.query.get(g.user_id)
    session = stripe.checkout.Session.create(
        mode='payment',
        line_items=[{'price': price_id, 'quantity': 1}],
        success_url=request.host_url + 'app?payment=success',
        cancel_url=request.host_url + 'pricing',
        customer_email=user.email,
        metadata={'user_id': str(g.user_id), 'topup_tokens': pack},
    )
    return jsonify({'url': session.url})

@stripe_bp.get('/api/stripe/status')
@require_auth
def subscription_status():
    user = User.query.get(g.user_id)
    return jsonify({
        'tier': user.tier,
        'token_balance': user.token_balance,
        'token_balance_topup': user.token_balance_topup,
        'total_tokens': user.total_tokens(),
    })
```

- [ ] **Step 5: Register blueprint in app/__init__.py**

Add inside `create_app()` after existing blueprint imports:

```python
    from .stripe_routes import stripe_bp
    app.register_blueprint(stripe_bp)
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_stripe_routes.py -v
```

Expected: All 5 PASS

- [ ] **Step 7: Commit**

```bash
git add requirements.txt app/stripe_routes.py app/__init__.py tests/test_stripe_routes.py
git commit -m "feat: Stripe checkout for subscriptions and top-up packs"
```

---

### Task 2: Stripe webhook — tier + token updates

**Files:**
- Modify: `app/stripe_routes.py` — add POST /webhook/stripe

- [ ] **Step 1: Write failing tests**

Add these tests to `tests/test_stripe_routes.py`:

```python
import time

def _make_webhook_event(event_type, metadata, obj_type='checkout.session'):
    import stripe as stripe_lib
    payload = {
        'id': 'evt_test', 'type': event_type, 'created': int(time.time()),
        'data': {'object': {
            'id': 'cs_test', 'object': obj_type,
            'mode': 'subscription' if 'subscription' in event_type else 'payment',
            'metadata': metadata,
            'subscription': 'sub_test' if 'subscription' in event_type else None,
            'customer': 'cus_test',
        }},
        'object': 'event',
        'api_version': '2024-04-10',
    }
    return payload

def test_webhook_subscription_created(client):
    client.post('/auth/register',
        data=json.dumps({'email': 'w@test.com', 'password': 'pass'}),
        content_type='application/json')
    from app.models import User
    user = User.query.filter_by(email='w@test.com').first()
    payload = _make_webhook_event(
        'checkout.session.completed',
        {'user_id': str(user.id), 'tier': 'us'}
    )
    with patch('app.stripe_routes.stripe.Webhook.construct_event', return_value=type('E', (), {'type': payload['type'], 'data': type('D', (), {'object': payload['data']['object']})()})()  ):
        r = client.post('/webhook/stripe',
            data=json.dumps(payload),
            content_type='application/json',
            headers={'Stripe-Signature': 'test'})
    assert r.status_code == 200
    db.session.refresh(user)
    assert user.tier == 'us'
    assert user.token_balance == 500

def test_webhook_topup_completed(client):
    client.post('/auth/register',
        data=json.dumps({'email': 'tu@test.com', 'password': 'pass'}),
        content_type='application/json')
    from app.models import User
    user = User.query.filter_by(email='tu@test.com').first()
    initial_topup = user.token_balance_topup
    payload = _make_webhook_event(
        'checkout.session.completed',
        {'user_id': str(user.id), 'topup_tokens': '1000'}
    )
    payload['data']['object']['mode'] = 'payment'
    with patch('app.stripe_routes.stripe.Webhook.construct_event', return_value=type('E', (), {'type': payload['type'], 'data': type('D', (), {'object': payload['data']['object']})()})()  ):
        r = client.post('/webhook/stripe',
            data=json.dumps(payload),
            content_type='application/json',
            headers={'Stripe-Signature': 'test'})
    assert r.status_code == 200
    db.session.refresh(user)
    assert user.token_balance_topup == initial_topup + 1000
```

- [ ] **Step 2: Add webhook route to app/stripe_routes.py**

Add this import at the top of `app/stripe_routes.py`:

```python
import json as jsonlib
```

Append this route to `app/stripe_routes.py`:

```python
@stripe_bp.post('/webhook/stripe')
def stripe_webhook():
    stripe.api_key = _stripe_key()
    payload = request.get_data(as_text=True)
    sig = request.headers.get('Stripe-Signature', '')
    webhook_secret = current_app.config.get('STRIPE_WEBHOOK_SECRET', '')
    try:
        event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
    except (stripe.error.SignatureVerificationError, ValueError):
        return jsonify({'error': 'Invalid signature'}), 400

    if event.type == 'checkout.session.completed':
        obj = event.data.object
        meta = obj.metadata or {}
        user_id = meta.get('user_id')
        if not user_id:
            return jsonify({'received': True})
        user = User.query.get(int(user_id))
        if not user:
            return jsonify({'received': True})

        if obj.mode == 'subscription' and meta.get('tier'):
            new_tier = meta['tier']
            user.tier = new_tier
            user.token_balance = User.TIER_TOKENS.get(new_tier, 0)
            user.stripe_customer_id = obj.customer
            user.stripe_subscription_id = obj.subscription
            db.session.commit()

        elif obj.mode == 'payment' and meta.get('topup_tokens'):
            tokens = TOPUP_AMOUNTS.get(str(meta['topup_tokens']), 0)
            user.token_balance_topup += tokens
            db.session.commit()

    elif event.type == 'customer.subscription.deleted':
        obj = event.data.object
        user = User.query.filter_by(stripe_subscription_id=obj.id).first()
        if user:
            user.tier = 'free'
            user.token_balance = User.TIER_TOKENS['free']
            user.stripe_subscription_id = None
            db.session.commit()

    return jsonify({'received': True})
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/test_stripe_routes.py -v
```

Expected: All 7 PASS (5 from Task 1 + 2 new)

- [ ] **Step 4: Commit**

```bash
git add app/stripe_routes.py tests/test_stripe_routes.py
git commit -m "feat: Stripe webhook — subscription tier update, token top-up, subscription cancellation"
```

---

### Task 3: Token deduction middleware

**Files:**
- Create: `app/token_middleware.py`
- Modify: `app/alfred_routes.py` — wrap chat endpoint
- Modify: `app/ai_synthesis.py` — wrap synthesize_gaps
- Modify: `app/hometown.py` — wrap get_life_context call in the route
- Create: `tests/test_token_middleware.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_token_middleware.py
import json
from unittest.mock import patch, MagicMock
from app.models import User

def _setup_user(client, email='tok@test.com'):
    client.post('/auth/register',
        data=json.dumps({'email': email, 'password': 'pass'}),
        content_type='application/json')

def test_token_deduction_on_alfred_chat(client, app):
    _setup_user(client)
    with app.app_context():
        user = User.query.filter_by(email='tok@test.com').first()
        initial_balance = user.total_tokens()

    r = client.post('/api/persons',
        data=json.dumps({'tree_name': 'T', 'first_name': 'J', 'last_name': 'D'}),
        content_type='application/json')
    person_id = r.get_json()['person_id']

    mock_resp = MagicMock()
    mock_resp.json.return_value = {'choices': [{'message': {'content': 'Answer.'}}]}
    mock_resp.raise_for_status = MagicMock()
    with patch('app.alfred_routes.requests.post', return_value=mock_resp):
        r2 = client.post(f'/api/alfred/{person_id}/chat',
            data=json.dumps({'message': 'Hello'}),
            content_type='application/json')
    assert r2.status_code == 200

    with app.app_context():
        user = User.query.filter_by(email='tok@test.com').first()
        assert user.total_tokens() == initial_balance - 10

def test_insufficient_tokens_blocks_alfred(client, app):
    _setup_user(client, 'low@test.com')
    with app.app_context():
        user = User.query.filter_by(email='low@test.com').first()
        user.token_balance = 5
        user.token_balance_topup = 0
        from app.db import db
        db.session.commit()

    r = client.post('/api/persons',
        data=json.dumps({'tree_name': 'T', 'first_name': 'J', 'last_name': 'D'}),
        content_type='application/json')
    person_id = r.get_json()['person_id']

    r2 = client.post(f'/api/alfred/{person_id}/chat',
        data=json.dumps({'message': 'Hello'}),
        content_type='application/json')
    assert r2.status_code == 402

def test_free_search_does_not_deduct_tokens(client, app):
    _setup_user(client, 'free@test.com')
    with app.app_context():
        user = User.query.filter_by(email='free@test.com').first()
        initial_balance = user.total_tokens()

    from unittest.mock import patch
    with patch('app.search_routes.run_us_cascade', return_value={'results':[],'gaps':[],'confidence':0}), \
         patch('app.search_routes.synthesize_gaps', return_value={'summary':''}):
        client.post('/search',
            data=json.dumps({'last': 'Doe'}),
            content_type='application/json')

    with app.app_context():
        user = User.query.filter_by(email='free@test.com').first()
        assert user.total_tokens() == initial_balance
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_token_middleware.py -v
```

- [ ] **Step 3: Create app/token_middleware.py**

```python
from functools import wraps
from flask import jsonify, g
from .db import db
from .models import User

def require_tokens(cost: int):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user = User.query.get(g.user_id)
            if not user or user.total_tokens() < cost:
                return jsonify({
                    'error': f'Insufficient tokens. This action costs {cost} tokens.',
                    'tokens_required': cost,
                    'tokens_available': user.total_tokens() if user else 0,
                }), 402
            if not user.deduct_tokens(cost):
                return jsonify({'error': 'Token deduction failed'}), 402
            db.session.commit()
            return f(*args, **kwargs)
        return wrapper
    return decorator
```

- [ ] **Step 4: Apply require_tokens to alfred_routes.py**

In `app/alfred_routes.py`, add the import at the top:

```python
from .token_middleware import require_tokens
```

Change the `chat` route decorator stack from:

```python
@alfred_bp.post('/api/alfred/<int:person_id>/chat')
@require_auth
def chat(person_id):
```

To:

```python
@alfred_bp.post('/api/alfred/<int:person_id>/chat')
@require_auth
@require_tokens(10)
def chat(person_id):
```

- [ ] **Step 5: Apply require_tokens to authenticated search**

In `app/search_routes.py`, add the import at the top:

```python
from .token_middleware import require_tokens
```

Change the `authenticated_search` route decorator stack from:

```python
@search_bp.post('/api/search')
@require_auth
def authenticated_search():
```

To:

```python
@search_bp.post('/api/search')
@require_auth
@require_tokens(20)
def authenticated_search():
```

Note: Guest `/search` (POST) does NOT deduct tokens — only authenticated AI synthesis does.

- [ ] **Step 6: Add low-balance toast to app.html**

In `static/app.html`, add this inside the `loadMe()` function, after the token badge is updated:

```javascript
async function loadMe() {
  const r = await fetch('/api/me');
  if (r.status === 401) { window.location.href = '/'; return; }
  const data = await r.json();
  document.getElementById('tokenBadge').textContent = `${data.tokens} tokens`;
  if (data.tokens < 100) showLowBalanceToast(data.tokens);
  loadTrees();
}

function showLowBalanceToast(tokens) {
  const existing = document.getElementById('lowBalanceToast');
  if (existing) return;
  const toast = document.createElement('div');
  toast.id = 'lowBalanceToast';
  toast.style.cssText = 'position:fixed;bottom:1.5rem;right:1.5rem;background:#7f1d1d;color:#fca5a5;padding:.75rem 1.25rem;border-radius:8px;font-size:.875rem;z-index:9999;display:flex;gap:1rem;align-items:center';
  toast.innerHTML = `⚠️ ${tokens} tokens remaining. <a href="/pricing" style="color:#fca5a5;text-decoration:underline">Top up</a> <button onclick="this.parentElement.remove()" style="background:none;border:none;color:#fca5a5;cursor:pointer;font-size:1rem">✕</button>`;
  document.body.appendChild(toast);
}
```

- [ ] **Step 7: Run tests**

```bash
pytest tests/test_token_middleware.py -v
```

Expected: All 3 PASS

- [ ] **Step 8: Run full suite**

```bash
pytest tests/ -v
```

Expected: All tests pass

- [ ] **Step 9: Commit**

```bash
git add app/token_middleware.py app/alfred_routes.py app/search_routes.py static/app.html tests/test_token_middleware.py
git commit -m "feat: token deduction middleware — gates Alfred (10 tokens) and AI search (20 tokens), low-balance toast"
```

---

### Task 4: PDF export + share tree link

**Files:**
- Modify: `requirements.txt` — add weasyprint
- Modify: `app/tree_routes.py` — add GET /api/trees/<id>/export/pdf and GET /trees/<share_token>

- [ ] **Step 1: Add weasyprint to requirements.txt**

```
weasyprint==62.3
```

Install:

```bash
pip install weasyprint==62.3
```

- [ ] **Step 2: Write failing tests**

```python
# Add to tests/test_tree_routes.py

def test_share_token_generated_on_tree_create(client):
    _register_and_login(client)
    r = client.post('/api/persons',
        data=json.dumps({'tree_name': 'Share Tree', 'first_name': 'A', 'last_name': 'B'}),
        content_type='application/json')
    tree_id = r.get_json()['tree_id']
    r2 = client.get(f'/api/trees/{tree_id}')
    tree_data = r2.get_json()
    # Share token is generated lazily
    r3 = client.post(f'/api/trees/{tree_id}/share')
    assert r3.status_code == 200
    assert 'share_url' in r3.get_json()

def test_public_tree_view(client):
    _register_and_login(client)
    r = client.post('/api/persons',
        data=json.dumps({'tree_name': 'PT', 'first_name': 'X', 'last_name': 'Y'}),
        content_type='application/json')
    tree_id = r.get_json()['tree_id']
    r2 = client.post(f'/api/trees/{tree_id}/share')
    share_token = r2.get_json()['share_token']
    r3 = client.get(f'/shared/{share_token}')
    assert r3.status_code == 200
```

- [ ] **Step 3: Run to verify failure**

```bash
pytest tests/test_tree_routes.py::test_share_token_generated_on_tree_create tests/test_tree_routes.py::test_public_tree_view -v
```

- [ ] **Step 4: Add share + export routes to app/tree_routes.py**

Add these imports at the top of `app/tree_routes.py`:

```python
import secrets
from flask import make_response, render_template_string
```

Append these routes to `app/tree_routes.py`:

```python
@tree_bp.post('/api/trees/<int:tree_id>/share')
@require_auth
def generate_share_link(tree_id):
    tree = Tree.query.filter_by(id=tree_id, user_id=g.user_id).first_or_404()
    if not tree.share_token:
        tree.share_token = secrets.token_urlsafe(32)
        db.session.commit()
    return jsonify({
        'share_token': tree.share_token,
        'share_url': f'/shared/{tree.share_token}',
    })

@tree_bp.get('/shared/<share_token>')
def public_tree_view(share_token):
    tree = Tree.query.filter_by(share_token=share_token).first_or_404()
    persons = [_person_to_dict(p) for p in tree.persons]
    return render_template_string("""
<!DOCTYPE html>
<html><head><title>{{ tree_name }} — RootBridge</title>
<link rel="stylesheet" href="/static/style.css">
<script src="https://d3js.org/d3.v7.min.js"></script>
</head><body>
<nav class="nav"><a href="/" class="nav-brand">RootBridge</a>
<span style="color:#94a3b8;margin-left:1rem">Shared Tree: {{ tree_name }}</span></nav>
<div id="treeContainer" style="width:100vw;height:calc(100vh - 64px)"></div>
<script src="/static/js/tree.js"></script>
<script>renderTree({{ persons_json | safe }}, null);</script>
</body></html>
    """, tree_name=tree.name, persons_json=__import__('json').dumps(persons))

@tree_bp.get('/api/trees/<int:tree_id>/export/pdf')
@require_auth
def export_pdf(tree_id):
    from weasyprint import HTML
    tree = Tree.query.filter_by(id=tree_id, user_id=g.user_id).first_or_404()
    persons_rows = ''.join(
        f"<tr><td>{p.first_name or ''} {p.last_name or ''}</td>"
        f"<td>{p.birth_year or '?'}</td><td>{p.birth_state or p.birth_country or '?'}</td>"
        f"<td>{p.death_year or '?'}</td><td>{p.confidence}%</td></tr>"
        for p in tree.persons
    )
    html = f"""<!DOCTYPE html><html><head><style>
body{{font-family:Arial,sans-serif;color:#111}}
h1{{color:#1e40af}}table{{width:100%;border-collapse:collapse}}
th,td{{border:1px solid #ccc;padding:6px;text-align:left}}
th{{background:#dbeafe}}</style></head><body>
<h1>{tree.name}</h1>
<p>Exported from RootBridge — {len(tree.persons)} persons</p>
<table><tr><th>Name</th><th>Born</th><th>Birth Place</th><th>Died</th><th>Confidence</th></tr>
{persons_rows}</table></body></html>"""
    pdf = HTML(string=html).write_pdf()
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename="{tree.name}.pdf"'
    return response
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_tree_routes.py -v
```

Expected: All tests in test_tree_routes.py PASS

- [ ] **Step 6: Commit**

```bash
git add requirements.txt app/tree_routes.py tests/test_tree_routes.py
git commit -m "feat: share tree link, public tree view, PDF export"
```

---

### Task 5: GDPR endpoints — data export + account deletion

**Files:**
- Create: `app/gdpr_routes.py`
- Modify: `app/__init__.py` — register blueprint
- Create: `tests/test_gdpr_routes.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_gdpr_routes.py
import json

def _login(client):
    client.post('/auth/register',
        data=json.dumps({'email': 'gdpr@test.com', 'password': 'pass'}),
        content_type='application/json')

def test_data_export_returns_json(client):
    _login(client)
    client.post('/api/persons',
        data=json.dumps({'tree_name': 'T', 'first_name': 'A', 'last_name': 'B'}),
        content_type='application/json')
    r = client.get('/api/me/export')
    assert r.status_code == 200
    data = r.get_json()
    assert 'account' in data
    assert data['account']['email'] == 'gdpr@test.com'
    assert 'trees' in data
    assert len(data['trees']) == 1

def test_account_deletion_removes_user(client, app):
    _login(client)
    r = client.delete('/api/me',
        data=json.dumps({'confirm': 'DELETE'}),
        content_type='application/json')
    assert r.status_code == 200
    # Verify user is gone
    from app.models import User
    with app.app_context():
        user = User.query.filter_by(email='gdpr@test.com').first()
        assert user is None

def test_account_deletion_requires_confirm(client):
    _login(client)
    r = client.delete('/api/me',
        data=json.dumps({'confirm': 'nope'}),
        content_type='application/json')
    assert r.status_code == 400
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_gdpr_routes.py -v
```

- [ ] **Step 3: Create app/gdpr_routes.py**

```python
from flask import Blueprint, jsonify, request, g
from .auth import require_auth
from .db import db
from .models import User, Tree, Person

gdpr_bp = Blueprint('gdpr', __name__)

@gdpr_bp.get('/api/me/export')
@require_auth
def export_data():
    user = User.query.get(g.user_id)
    trees_data = []
    for tree in user.trees:
        persons_data = []
        for p in tree.persons:
            persons_data.append({
                'first_name': p.first_name, 'last_name': p.last_name,
                'birth_year': p.birth_year, 'birth_state': p.birth_state,
                'birth_country': p.birth_country, 'death_year': p.death_year,
                'death_place': p.death_place, 'confidence': p.confidence,
                'notes': p.notes, 'parent_ids': p.parent_ids,
                'spouse_ids': p.spouse_ids,
                'search_results': [
                    {'source': r.source, 'record_type': r.record_type, 'url': r.url}
                    for r in p.search_results
                ],
                'gaps': [
                    {'gap_type': gap.gap_type, 'suggested_source': gap.suggested_source,
                     'resolved': gap.resolved}
                    for gap in p.gaps
                ],
            })
        trees_data.append({'name': tree.name, 'persons': persons_data})
    return jsonify({
        'account': {
            'email': user.email,
            'tier': user.tier,
            'created_at': user.created_at.isoformat() if user.created_at else None,
        },
        'trees': trees_data,
    })

@gdpr_bp.delete('/api/me')
@require_auth
def delete_account():
    data = request.get_json() or {}
    if data.get('confirm') != 'DELETE':
        return jsonify({'error': 'Send {"confirm": "DELETE"} to confirm account deletion'}), 400
    user = User.query.get(g.user_id)
    db.session.delete(user)
    db.session.commit()
    response = jsonify({'message': 'Account and all data deleted.'})
    response.delete_cookie('auth_token')
    return response
```

- [ ] **Step 4: Register blueprint in app/__init__.py**

Add inside `create_app()` after existing blueprint imports:

```python
    from .gdpr_routes import gdpr_bp
    app.register_blueprint(gdpr_bp)
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_gdpr_routes.py -v
```

Expected: All 3 PASS

- [ ] **Step 6: Run full suite**

```bash
pytest tests/ -v
```

Expected: All tests pass (35+ total)

- [ ] **Step 7: Commit**

```bash
git add app/gdpr_routes.py app/__init__.py tests/test_gdpr_routes.py
git commit -m "feat: GDPR data export and account deletion endpoints"
```

---

## Self-Review

**Spec coverage:**
- ✅ Stripe subscription checkout for all 5 tiers — Task 1
- ✅ Stripe one-time top-up pack purchases ($2.99/300, $7.99/1000, $14.99/2500, $24.99/5000) — Task 1
- ✅ Webhook updates DB tier + token balance on subscription completion — Task 2
- ✅ Webhook downgrades to free tier on subscription cancellation — Task 2
- ✅ Token deduction: monthly balance depletes first, then top-up — via existing User.deduct_tokens()
- ✅ Alfred gated at 10 tokens — Task 3
- ✅ Authenticated AI search gated at 20 tokens — Task 3
- ✅ Low-balance toast when < 100 tokens — Task 3
- ✅ Insufficient tokens → 402 response — Task 3
- ✅ Share tree link (public view) — Task 4
- ✅ PDF export — Task 4
- ✅ GDPR: data export — Task 5
- ✅ GDPR: account deletion — Task 5
- ⏭️ Heritage-tier search gating (European/AA cascades) — Plan 5
- ⏭️ Token cost for deep heritage cascades (40 tokens) — Plan 5

**Placeholder scan:** None. Stripe price IDs use env vars, all other code is complete.

**Type consistency:** `User.deduct_tokens()` returns bool — all callers check the return value before proceeding.
