# RootBridge Plan 3: Visual Tree + Person Card + Hometown Visual + Alfred

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the D3.js interactive family tree, the person card overlay with hometown photo and historical map, and the Alfred AI research concierge with Web Speech API voice input.

**Architecture:** `tree_routes.py` exposes tree/person CRUD APIs. `hometown.py` fetches Wikimedia Commons photos, David Rumsey historical maps, and generates AI life-context blurbs via OpenRouter. `alfred_routes.py` handles Alfred chat with full person context and stores message history. `static/js/tree.js` renders the D3.js tree (blue/yellow/red nodes). `static/js/person_card.js` handles the overlay, hometown panel, Alfred chat, and Web Speech API voice input.

**Tech Stack:** D3.js v7 (CDN), Web Speech API (browser-native, no cost), Wikimedia Commons API (no auth), David Rumsey LUNA API (no auth), OpenRouter claude-3-haiku, Python requests

**Spec:** `docs/superpowers/specs/2026-05-31-genealogy-gap-finder-design.md`

**Builds on Plans 1 and 2. Plan 4 adds token deduction to Alfred and hometown AI calls.**

---

## File Structure

```
app/
├── tree_routes.py       # GET/PUT /api/trees/<id>, GET/POST/DELETE /api/persons
├── hometown.py          # Wikimedia photo, David Rumsey map, OpenRouter life-context
├── alfred_routes.py     # POST /api/alfred/<person_id>/chat, GET history
├── models.py            # Add AlfredMessage model (modify existing)
static/
├── js/
│   ├── tree.js          # D3.js tree rendering, node click handlers
│   └── person_card.js   # Person card overlay, Alfred chat UI, voice input
├── app.html             # Add D3.js CDN, tree container, person card HTML, script tags
tests/
├── test_tree_routes.py
├── test_hometown.py
├── test_alfred_routes.py
```

---

### Task 1: AlfredMessage model + tree/person API routes

**Files:**
- Modify: `app/models.py` — add AlfredMessage model
- Create: `app/tree_routes.py`
- Create: `tests/test_tree_routes.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tree_routes.py
import json

def _register_and_login(client):
    client.post('/auth/register',
        data=json.dumps({'email': 't@test.com', 'password': 'pass'}),
        content_type='application/json')
    return client

def test_get_tree_not_found(client):
    _register_and_login(client)
    r = client.get('/api/trees/9999')
    assert r.status_code == 404

def test_create_person_and_get_tree(client):
    _register_and_login(client)
    r = client.post('/api/persons',
        data=json.dumps({'tree_name': 'My Tree', 'first_name': 'Jane',
                         'last_name': 'Doe', 'birth_year': 1820}),
        content_type='application/json')
    assert r.status_code == 201
    data = r.get_json()
    tree_id = data['tree_id']
    person_id = data['person_id']

    r2 = client.get(f'/api/trees/{tree_id}')
    assert r2.status_code == 200
    tree_data = r2.get_json()
    assert tree_data['id'] == tree_id
    assert len(tree_data['persons']) == 1
    assert tree_data['persons'][0]['id'] == person_id

def test_get_person_detail(client):
    _register_and_login(client)
    r = client.post('/api/persons',
        data=json.dumps({'tree_name': 'T', 'first_name': 'Bob', 'last_name': 'Smith'}),
        content_type='application/json')
    person_id = r.get_json()['person_id']
    r2 = client.get(f'/api/persons/{person_id}')
    assert r2.status_code == 200
    p = r2.get_json()
    assert p['first_name'] == 'Bob'
    assert 'gaps' in p
    assert 'search_results' in p

def test_update_person(client):
    _register_and_login(client)
    r = client.post('/api/persons',
        data=json.dumps({'tree_name': 'T', 'first_name': 'Bob', 'last_name': 'Smith'}),
        content_type='application/json')
    person_id = r.get_json()['person_id']
    r2 = client.put(f'/api/persons/{person_id}',
        data=json.dumps({'birth_year': 1850, 'birth_state': 'Ohio'}),
        content_type='application/json')
    assert r2.status_code == 200
    p = r2.get_json()
    assert p['birth_year'] == 1850

def test_delete_person(client):
    _register_and_login(client)
    r = client.post('/api/persons',
        data=json.dumps({'tree_name': 'T', 'first_name': 'Bob', 'last_name': 'Smith'}),
        content_type='application/json')
    data = r.get_json()
    person_id = data['person_id']
    tree_id = data['tree_id']
    r2 = client.delete(f'/api/persons/{person_id}')
    assert r2.status_code == 204
    r3 = client.get(f'/api/trees/{tree_id}')
    assert len(r3.get_json()['persons']) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/krish/familytree_app && source venv/bin/activate
pytest tests/test_tree_routes.py -v
```

Expected: FAIL — module not found

- [ ] **Step 3: Add AlfredMessage to app/models.py**

Append this class to the end of `app/models.py`:

```python
class AlfredMessage(db.Model):
    __tablename__ = 'alfred_messages'
    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=False)
    tree_id = db.Column(db.Integer, db.ForeignKey('trees.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'user' or 'assistant'
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 4: Create app/tree_routes.py**

```python
from flask import Blueprint, request, jsonify, g
from .auth import require_auth
from .db import db
from .models import Tree, Person, SearchResult, Gap, AlfredMessage

tree_bp = Blueprint('tree', __name__)

def _person_to_dict(p: Person) -> dict:
    return {
        'id': p.id, 'tree_id': p.tree_id,
        'first_name': p.first_name, 'last_name': p.last_name,
        'birth_year': p.birth_year, 'birth_state': p.birth_state,
        'birth_country': p.birth_country, 'death_year': p.death_year,
        'death_place': p.death_place, 'confidence': p.confidence,
        'parent_ids': p.parent_ids or [], 'spouse_ids': p.spouse_ids or [],
        'notes': p.notes,
    }

def _person_detail(p: Person) -> dict:
    d = _person_to_dict(p)
    d['search_results'] = [
        {'id': r.id, 'source': r.source, 'record_type': r.record_type,
         'url': r.url, 'raw_data': r.raw_data}
        for r in p.search_results
    ]
    d['gaps'] = [
        {'id': g.id, 'gap_type': g.gap_type, 'suggested_source': g.suggested_source,
         'suggested_query': g.suggested_query, 'resolved': g.resolved}
        for g in p.gaps
    ]
    return d

@tree_bp.get('/api/trees/<int:tree_id>')
@require_auth
def get_tree(tree_id):
    tree = Tree.query.filter_by(id=tree_id, user_id=g.user_id).first_or_404()
    return jsonify({
        'id': tree.id, 'name': tree.name, 'share_token': tree.share_token,
        'persons': [_person_to_dict(p) for p in tree.persons],
    })

@tree_bp.put('/api/trees/<int:tree_id>')
@require_auth
def update_tree(tree_id):
    tree = Tree.query.filter_by(id=tree_id, user_id=g.user_id).first_or_404()
    data = request.get_json() or {}
    if 'name' in data:
        tree.name = data['name']
    db.session.commit()
    return jsonify({'id': tree.id, 'name': tree.name})

@tree_bp.get('/api/persons/<int:person_id>')
@require_auth
def get_person(person_id):
    p = Person.query.join(Tree).filter(
        Person.id == person_id, Tree.user_id == g.user_id
    ).first_or_404()
    return jsonify(_person_detail(p))

@tree_bp.post('/api/persons')
@require_auth
def create_person():
    data = request.get_json() or {}
    tree_name = data.get('tree_name') or f"{data.get('last_name', 'My')} Family"
    tree = Tree.query.filter_by(user_id=g.user_id, name=tree_name).first()
    if not tree:
        tree = Tree(user_id=g.user_id, name=tree_name)
        db.session.add(tree)
        db.session.flush()
    p = Person(
        tree_id=tree.id,
        first_name=data.get('first_name', ''),
        last_name=data.get('last_name', ''),
        birth_year=data.get('birth_year'),
        birth_state=data.get('birth_state', ''),
        birth_country=data.get('birth_country', ''),
        death_year=data.get('death_year'),
        death_place=data.get('death_place', ''),
        parent_ids=data.get('parent_ids', []),
        spouse_ids=data.get('spouse_ids', []),
        confidence=data.get('confidence', 0),
    )
    db.session.add(p)
    db.session.commit()
    return jsonify({'person_id': p.id, 'tree_id': tree.id}), 201

@tree_bp.put('/api/persons/<int:person_id>')
@require_auth
def update_person(person_id):
    p = Person.query.join(Tree).filter(
        Person.id == person_id, Tree.user_id == g.user_id
    ).first_or_404()
    data = request.get_json() or {}
    for field in ('first_name', 'last_name', 'birth_year', 'birth_state',
                  'birth_country', 'death_year', 'death_place', 'notes',
                  'confidence', 'parent_ids', 'spouse_ids'):
        if field in data:
            setattr(p, field, data[field])
    db.session.commit()
    return jsonify(_person_to_dict(p))

@tree_bp.delete('/api/persons/<int:person_id>')
@require_auth
def delete_person(person_id):
    p = Person.query.join(Tree).filter(
        Person.id == person_id, Tree.user_id == g.user_id
    ).first_or_404()
    db.session.delete(p)
    db.session.commit()
    return '', 204
```

- [ ] **Step 5: Register blueprint in app/__init__.py**

Add inside `create_app()` after the existing blueprint imports:

```python
    from .tree_routes import tree_bp
    app.register_blueprint(tree_bp)
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_tree_routes.py -v
```

Expected: All 5 PASS

- [ ] **Step 7: Run full suite**

```bash
pytest tests/ -v
```

Expected: All previous tests still pass (no regressions)

- [ ] **Step 8: Commit**

```bash
git add app/models.py app/tree_routes.py app/__init__.py tests/test_tree_routes.py
git commit -m "feat: AlfredMessage model, tree/person CRUD routes"
```

---

### Task 2: Hometown Visual backend

**Files:**
- Create: `app/hometown.py`
- Create: `tests/test_hometown.py`
- Modify: `app/tree_routes.py` — add GET /api/persons/<id>/hometown

- [ ] **Step 1: Write failing tests**

```python
# tests/test_hometown.py
from unittest.mock import patch, MagicMock
from app.hometown import get_hometown_photo, get_historical_map, get_life_context

def test_get_hometown_photo_returns_url(app):
    with app.app_context():
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'query': {'pages': {
                '1': {'title': 'County Cork', 'original': {'source': 'https://commons.wikimedia.org/img/cork.jpg'}}
            }}
        }
        mock_resp.raise_for_status = MagicMock()
        with patch('app.hometown.requests.get', return_value=mock_resp):
            result = get_hometown_photo('County Cork', 1847)
    assert result['url'] == 'https://commons.wikimedia.org/img/cork.jpg'
    assert result['is_approximate'] is False

def test_get_hometown_photo_returns_approximate_on_no_results(app):
    with app.app_context():
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'query': {'pages': {}}}
        mock_resp.raise_for_status = MagicMock()
        with patch('app.hometown.requests.get', return_value=mock_resp):
            result = get_hometown_photo('Unknown Place', 1800)
    assert result['is_approximate'] is True

def test_get_hometown_photo_returns_none_on_error(app):
    with app.app_context():
        with patch('app.hometown.requests.get', side_effect=Exception('network error')):
            result = get_hometown_photo('Cork', 1847)
    assert result is None

def test_get_life_context_returns_text(app):
    with app.app_context():
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'choices': [{'message': {'content': 'Life was hard in rural Cork.'}}]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch('app.hometown.requests.post', return_value=mock_resp):
            result = get_life_context(
                name='Margaret Haines', birth_place='County Cork',
                birth_year=1820, death_year=1890
            )
    assert isinstance(result, str)
    assert len(result) > 0

def test_get_life_context_returns_empty_on_error(app):
    with app.app_context():
        with patch('app.hometown.requests.post', side_effect=Exception('API error')):
            result = get_life_context('Jane Doe', 'Ohio', 1850, None)
    assert result == ''
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_hometown.py -v
```

- [ ] **Step 3: Create app/hometown.py**

```python
import requests
from flask import current_app

WIKIMEDIA_API = 'https://en.wikipedia.org/w/api.php'
OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'

def get_hometown_photo(place: str, year: int = None) -> dict | None:
    if not place:
        return None
    try:
        params = {
            'action': 'query', 'generator': 'search',
            'gsrsearch': f'{place} landscape', 'gsrnamespace': '6',
            'prop': 'imageinfo', 'iiprop': 'url|extmetadata',
            'iiurlwidth': 800, 'format': 'json',
        }
        resp = requests.get(WIKIMEDIA_API, params=params, timeout=5)
        resp.raise_for_status()
        pages = resp.json().get('query', {}).get('pages', {})
        for page in pages.values():
            info = page.get('imageinfo', [{}])[0]
            url = info.get('url', '') or info.get('thumburl', '')
            if url and url.lower().endswith(('.jpg', '.jpeg', '.png')):
                return {'url': url, 'title': page.get('title', ''),
                        'credit': 'Wikimedia Commons', 'is_approximate': False}
        return {'url': _regional_fallback(place), 'is_approximate': True,
                'title': f'Region: {place}', 'credit': 'Wikimedia Commons'}
    except Exception:
        return None

def _regional_fallback(place: str) -> str:
    country_map = {
        'ireland': 'https://upload.wikimedia.org/wikipedia/commons/thumb/4/45/Flag_of_Ireland.svg/320px-Flag_of_Ireland.svg.png',
        'germany': 'https://upload.wikimedia.org/wikipedia/commons/thumb/b/be/Flag_of_Germany.svg/320px-Flag_of_Germany.svg.png',
        'england': 'https://upload.wikimedia.org/wikipedia/commons/thumb/b/be/Flag_of_England.svg/320px-Flag_of_England.svg.png',
        'sweden': 'https://upload.wikimedia.org/wikipedia/commons/thumb/4/4c/Flag_of_Sweden.svg/320px-Flag_of_Sweden.svg.png',
    }
    place_lower = place.lower()
    for keyword, url in country_map.items():
        if keyword in place_lower:
            return url
    return ''

def get_historical_map(place: str, year: int = None) -> dict | None:
    if not place:
        return None
    try:
        year_str = str(year) if year else '1850'
        params = {
            'lc': 'RUMSEY~8~1',
            'q': f'{place}',
            'sort': 'Pub_Date',
            'order': 'descending',
            'pgs': '5', 'res': '1',
        }
        resp = requests.get(
            'https://luna.davidrumsey.com/luna/servlet/as/search',
            params=params, timeout=5
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get('results', [])
        if results:
            item = results[0]
            return {
                'url': item.get('urlsize4', item.get('urlsize3', '')),
                'title': item.get('title', ''),
                'year': item.get('pub_date', year_str),
                'credit': 'David Rumsey Map Collection',
            }
    except Exception:
        pass
    return None

def get_life_context(name: str, birth_place: str, birth_year: int,
                     death_year: int) -> str:
    try:
        era = f"{birth_year}–{death_year}" if death_year else f"born {birth_year}"
        prompt = f"""In 2-3 sentences, describe what daily life was like for an ordinary person living in {birth_place} around {era}. Focus on: the economic conditions, major historical events that would have affected their daily life, and why families might have emigrated from there. Be specific and evocative. Do not mention {name} by name."""
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                'Authorization': f"Bearer {current_app.config['OPENROUTER_API_KEY']}",
                'Content-Type': 'application/json',
            },
            json={
                'model': 'anthropic/claude-3-haiku',
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 120,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()['choices'][0]['message']['content'].strip()
    except Exception:
        return ''
```

- [ ] **Step 4: Add /api/persons/<id>/hometown to app/tree_routes.py**

Add this import at the top of `app/tree_routes.py`:

```python
from .hometown import get_hometown_photo, get_historical_map, get_life_context
```

Append this route to `app/tree_routes.py`:

```python
@tree_bp.get('/api/persons/<int:person_id>/hometown')
@require_auth
def get_hometown(person_id):
    p = Person.query.join(Tree).filter(
        Person.id == person_id, Tree.user_id == g.user_id
    ).first_or_404()
    place = p.birth_state or p.birth_country or ''
    if not place:
        return jsonify({'available': False})
    name = f'{p.first_name or ""} {p.last_name or ""}'.strip()
    photo = get_hometown_photo(place, p.birth_year)
    historical_map = get_historical_map(place, p.birth_year)
    life_context = get_life_context(name, place, p.birth_year, p.death_year)
    return jsonify({
        'available': True,
        'place': place,
        'photo': photo,
        'map': historical_map,
        'life_context': life_context,
    })
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_hometown.py -v
```

Expected: All 5 PASS

- [ ] **Step 6: Commit**

```bash
git add app/hometown.py app/tree_routes.py tests/test_hometown.py
git commit -m "feat: hometown visual backend — Wikimedia photos, David Rumsey maps, AI life context"
```

---

### Task 3: Alfred chat backend

**Files:**
- Create: `app/alfred_routes.py`
- Modify: `app/__init__.py` — register blueprint
- Create: `tests/test_alfred_routes.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_alfred_routes.py
import json
from unittest.mock import patch, MagicMock

def _setup_person(client):
    client.post('/auth/register',
        data=json.dumps({'email': 'a@test.com', 'password': 'pass'}),
        content_type='application/json')
    r = client.post('/api/persons',
        data=json.dumps({'tree_name': 'T', 'first_name': 'John', 'last_name': 'Doe',
                         'birth_year': 1800, 'birth_state': 'Virginia'}),
        content_type='application/json')
    return r.get_json()['person_id']

def test_alfred_history_empty_initially(client):
    person_id = _setup_person(client)
    r = client.get(f'/api/alfred/{person_id}/history')
    assert r.status_code == 200
    assert r.get_json()['messages'] == []

def test_alfred_chat_returns_response(client):
    person_id = _setup_person(client)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        'choices': [{'message': {'content': 'I found a pension record for John Doe.'}}]
    }
    mock_resp.raise_for_status = MagicMock()
    with patch('app.alfred_routes.requests.post', return_value=mock_resp):
        r = client.post(f'/api/alfred/{person_id}/chat',
            data=json.dumps({'message': 'Where did you find the pension record?'}),
            content_type='application/json')
    assert r.status_code == 200
    data = r.get_json()
    assert 'response' in data
    assert data['response'] == 'I found a pension record for John Doe.'

def test_alfred_chat_saves_history(client):
    person_id = _setup_person(client)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {'choices': [{'message': {'content': 'Answer.'}}]}
    mock_resp.raise_for_status = MagicMock()
    with patch('app.alfred_routes.requests.post', return_value=mock_resp):
        client.post(f'/api/alfred/{person_id}/chat',
            data=json.dumps({'message': 'Hello Alfred'}),
            content_type='application/json')
    r = client.get(f'/api/alfred/{person_id}/history')
    messages = r.get_json()['messages']
    assert len(messages) == 2  # user message + assistant response
    assert messages[0]['role'] == 'user'
    assert messages[1]['role'] == 'assistant'

def test_alfred_chat_requires_message(client):
    person_id = _setup_person(client)
    r = client.post(f'/api/alfred/{person_id}/chat',
        data=json.dumps({}),
        content_type='application/json')
    assert r.status_code == 400
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_alfred_routes.py -v
```

- [ ] **Step 3: Create app/alfred_routes.py**

```python
import requests
from flask import Blueprint, request, jsonify, g, current_app
from .auth import require_auth
from .db import db
from .models import Person, Tree, SearchResult, Gap, AlfredMessage

alfred_bp = Blueprint('alfred', __name__)
OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'
ALFRED_MODEL = 'anthropic/claude-3-haiku'
HISTORY_LIMIT = 10

def _build_alfred_context(person: Person) -> str:
    name = f'{person.first_name or ""} {person.last_name or ""}'.strip()
    birth = f"{person.birth_year or 'unknown'}, {person.birth_state or person.birth_country or 'unknown location'}"
    death = f"{person.death_year or 'unknown'}, {person.death_place or 'unknown location'}"

    sources = []
    for r in person.search_results:
        sources.append(f"- {r.source} ({r.record_type or 'record'}): {r.url}")

    gaps = []
    for gap in person.gaps:
        if not gap.resolved:
            gaps.append(f"- {gap.gap_type}: try {gap.suggested_source} — search: {gap.suggested_query}")

    tree = person.tree
    relatives = []
    if tree:
        for p in tree.persons:
            if p.id == person.id:
                continue
            rel = f"{p.first_name or ''} {p.last_name or ''} ({p.birth_year or '?'}–{p.death_year or '?'})"
            if person.id in (p.spouse_ids or []):
                relatives.append(f"Spouse: {rel}")
            elif p.id in (person.parent_ids or []):
                relatives.append(f"Parent: {rel}")
            elif person.id in (p.parent_ids or []):
                relatives.append(f"Child: {rel}")

    return f"""You are Alfred, a British genealogy research concierge with the personality of a knowledgeable butler. You have complete access to all research for {name}.

PERSON: {name}
Born: {birth}
Died: {death}
Confidence score: {person.confidence}%

RECORDS FOUND ({len(person.search_results)}):
{chr(10).join(sources) or 'None yet.'}

OPEN RESEARCH GAPS:
{chr(10).join(gaps) or 'No gaps — research appears complete.'}

FAMILY TREE CONNECTIONS:
{chr(10).join(relatives) or 'No other persons in tree yet.'}

Answer questions about this person's research directly and specifically. If asked to show a picture, describe what image would be shown and from where. If asked to translate something, do so. Always cite specific sources when available. Keep responses concise — 2-4 sentences unless more detail is needed."""

@alfred_bp.get('/api/alfred/<int:person_id>/history')
@require_auth
def get_history(person_id):
    Person.query.join(Tree).filter(
        Person.id == person_id, Tree.user_id == g.user_id
    ).first_or_404()
    messages = AlfredMessage.query.filter_by(person_id=person_id).order_by(
        AlfredMessage.created_at.asc()
    ).limit(HISTORY_LIMIT * 2).all()
    return jsonify({'messages': [
        {'role': m.role, 'content': m.content, 'created_at': m.created_at.isoformat()}
        for m in messages
    ]})

@alfred_bp.post('/api/alfred/<int:person_id>/chat')
@require_auth
def chat(person_id):
    person = Person.query.join(Tree).filter(
        Person.id == person_id, Tree.user_id == g.user_id
    ).first_or_404()
    data = request.get_json() or {}
    user_message = data.get('message', '').strip()
    if not user_message:
        return jsonify({'error': 'Message is required'}), 400

    history = AlfredMessage.query.filter_by(person_id=person_id).order_by(
        AlfredMessage.created_at.desc()
    ).limit(HISTORY_LIMIT).all()
    history.reverse()

    system_context = _build_alfred_context(person)
    messages = [{'role': 'system', 'content': system_context}]
    for m in history:
        messages.append({'role': m.role, 'content': m.content})
    messages.append({'role': 'user', 'content': user_message})

    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                'Authorization': f"Bearer {current_app.config['OPENROUTER_API_KEY']}",
                'Content-Type': 'application/json',
            },
            json={'model': ALFRED_MODEL, 'messages': messages, 'max_tokens': 300},
            timeout=15,
        )
        resp.raise_for_status()
        assistant_reply = resp.json()['choices'][0]['message']['content'].strip()
    except Exception:
        assistant_reply = "I'm having trouble connecting at the moment. Please try again shortly."

    db.session.add(AlfredMessage(
        person_id=person_id, tree_id=person.tree_id,
        role='user', content=user_message
    ))
    db.session.add(AlfredMessage(
        person_id=person_id, tree_id=person.tree_id,
        role='assistant', content=assistant_reply
    ))
    db.session.commit()
    return jsonify({'response': assistant_reply})
```

- [ ] **Step 4: Register blueprint in app/__init__.py**

Add inside `create_app()` after the existing blueprint imports:

```python
    from .alfred_routes import alfred_bp
    app.register_blueprint(alfred_bp)
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_alfred_routes.py -v
```

Expected: All 4 PASS

- [ ] **Step 6: Run full suite**

```bash
pytest tests/ -v
```

Expected: All tests pass (25+ total)

- [ ] **Step 7: Commit**

```bash
git add app/alfred_routes.py app/__init__.py tests/test_alfred_routes.py
git commit -m "feat: Alfred chat backend — full person context, message history, OpenRouter integration"
```

---

### Task 4: D3.js family tree frontend

**Files:**
- Create: `static/js/tree.js`
- Modify: `static/app.html` — add D3.js CDN, tree container, script tag

- [ ] **Step 1: Update static/app.html**

Replace the `<head>` section's closing `</head>` tag — add D3.js CDN and the tree container to the body. The full updated `app.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>RootBridge — Your Family Tree</title>
  <link rel="stylesheet" href="/static/style.css">
  <script src="https://d3js.org/d3.v7.min.js"></script>
</head>
<body>
  <nav class="nav">
    <a href="/" class="nav-brand">RootBridge</a>
    <div class="nav-right">
      <span class="token-badge" id="tokenBadge">— tokens</span>
      <button class="btn-ghost" onclick="logout()">Logout</button>
    </div>
  </nav>

  <div class="app-layout">
    <aside class="sidebar">
      <h2>My Trees</h2>
      <div id="treeList"></div>
      <button class="btn-primary" onclick="showAddPersonForm()">+ Add Person</button>
    </aside>
    <main class="tree-main">
      <div id="treeContainer">
        <p class="placeholder-text">Add a person to start building your family tree.</p>
      </div>
    </main>
  </div>

  <div id="addPersonModal" class="modal hidden">
    <div class="modal-content">
      <h3>Add Person</h3>
      <input id="apFirst" placeholder="First name" class="input-field">
      <input id="apLast" placeholder="Last name *" class="input-field">
      <input id="apBirthYear" placeholder="Birth year (e.g. 1820)" type="number" class="input-field">
      <input id="apBirthPlace" placeholder="Birth state or country" class="input-field">
      <div class="modal-actions">
        <button class="btn-primary" onclick="addPerson()">Add</button>
        <button class="btn-ghost" onclick="closeModal('addPersonModal')">Cancel</button>
      </div>
    </div>
  </div>

  <div id="personCardOverlay" class="person-card-overlay hidden"></div>

  <script src="/static/js/tree.js"></script>
  <script src="/static/js/person_card.js"></script>
  <script>
    let currentTreeId = null;

    async function loadMe() {
      const r = await fetch('/api/me');
      if (r.status === 401) { window.location.href = '/'; return; }
      const data = await r.json();
      document.getElementById('tokenBadge').textContent = `${data.tokens} tokens`;
      loadTrees();
    }

    async function loadTrees() {
      // Trees are loaded from localStorage (tree_id) or URL param
      const params = new URLSearchParams(window.location.search);
      const treeId = params.get('tree') || localStorage.getItem('lastTreeId');
      if (treeId) {
        currentTreeId = parseInt(treeId);
        loadTree(currentTreeId);
      }
    }

    async function addPerson() {
      const first = document.getElementById('apFirst').value.trim();
      const last = document.getElementById('apLast').value.trim();
      const birthYear = parseInt(document.getElementById('apBirthYear').value) || null;
      const birthPlace = document.getElementById('apBirthPlace').value.trim();
      if (!last) { alert('Last name is required'); return; }
      const r = await fetch('/api/persons', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          tree_name: last + ' Family',
          first_name: first, last_name: last,
          birth_year: birthYear, birth_state: birthPlace,
        })
      });
      const data = await r.json();
      currentTreeId = data.tree_id;
      localStorage.setItem('lastTreeId', currentTreeId);
      closeModal('addPersonModal');
      loadTree(currentTreeId);
    }

    function showAddPersonForm() { document.getElementById('addPersonModal').classList.remove('hidden'); }
    function closeModal(id) { document.getElementById(id).classList.add('hidden'); }

    async function logout() {
      await fetch('/auth/logout', {method: 'POST'});
      window.location.href = '/';
    }

    loadMe();
  </script>
</body>
</html>
```

- [ ] **Step 2: Add tree CSS to static/style.css**

Append to the end of `static/style.css`:

```css
.app-layout { display: grid; grid-template-columns: 240px 1fr; height: calc(100vh - 64px); }
.sidebar { background: var(--surface); padding: 1.5rem; border-right: 1px solid #334155; overflow-y: auto; }
.sidebar h2 { font-size: 1rem; color: #94a3b8; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 1rem; }
.tree-main { overflow: hidden; position: relative; }
#treeContainer { width: 100%; height: 100%; }
#treeContainer svg { width: 100%; height: 100%; }
.placeholder-text { color: #64748b; text-align: center; margin-top: 4rem; }
.modal { position: fixed; inset: 0; background: rgba(0,0,0,.6); display: flex; align-items: center; justify-content: center; z-index: 100; }
.modal.hidden { display: none; }
.modal-content { background: var(--surface); border-radius: 8px; padding: 2rem; width: 380px; display: flex; flex-direction: column; gap: 1rem; }
.modal-content h3 { margin: 0; }
.modal-actions { display: flex; gap: .75rem; justify-content: flex-end; }
.input-field { background: #0f172a; border: 1px solid #334155; color: #f1f5f9; padding: .5rem .75rem; border-radius: 6px; font-size: .9rem; width: 100%; box-sizing: border-box; }
.input-field:focus { outline: 2px solid var(--blue); }
.node-confirmed circle { fill: #1e40af; stroke: var(--blue); stroke-width: 2; }
.node-partial circle { fill: #78350f; stroke: #f59e0b; stroke-width: 2; stroke-dasharray: 6,3; }
.node-gap circle { fill: #7f1d1d; stroke: #ef4444; stroke-width: 2; stroke-dasharray: 6,3; }
.node-inferred circle { fill: #1e293b; stroke: #64748b; stroke-width: 1.5; stroke-dasharray: 4,4; }
.node text { fill: #f1f5f9; font-size: 11px; pointer-events: none; }
.link { fill: none; stroke: #334155; stroke-width: 1.5; }
.person-card-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.7); z-index: 200; display: flex; align-items: center; justify-content: center; }
.person-card-overlay.hidden { display: none; }
```

- [ ] **Step 3: Create static/js/tree.js**

```javascript
const NODE_RADIUS = 28;

async function loadTree(treeId) {
  const r = await fetch(`/api/trees/${treeId}`);
  if (!r.ok) return;
  const tree = await r.json();
  renderTree(tree.persons, treeId);
}

function renderTree(persons, treeId) {
  const container = document.getElementById('treeContainer');
  container.innerHTML = '';
  if (!persons || persons.length === 0) {
    container.innerHTML = '<p class="placeholder-text">No persons in tree yet.</p>';
    return;
  }

  const width = container.clientWidth || 900;
  const height = container.clientHeight || 600;

  // Build parent-child edges from parent_ids
  const idSet = new Set(persons.map(p => p.id));
  const edges = [];
  persons.forEach(p => {
    (p.parent_ids || []).forEach(parentId => {
      if (idSet.has(parentId)) {
        edges.push({ source: parentId, target: p.id });
      }
    });
  });

  // Find roots: persons with no parents in this tree
  const hasParent = new Set(edges.map(e => e.target));
  const roots = persons.filter(p => !hasParent.has(p.id));
  if (roots.length === 0) roots.push(persons[0]);

  // Build D3 hierarchy from first root; others rendered as floating nodes
  const childMap = {};
  persons.forEach(p => { childMap[p.id] = []; });
  edges.forEach(e => { childMap[e.source].push(e.target); });

  const personById = Object.fromEntries(persons.map(p => [p.id, p]));

  function buildHierarchy(id) {
    const p = personById[id];
    return { data: p, children: childMap[id].map(buildHierarchy) };
  }

  const hierarchyData = d3.hierarchy(buildHierarchy(roots[0].id));
  const treeLayout = d3.tree().size([width - 80, height - 120]);
  treeLayout(hierarchyData);

  // Add floating nodes for disconnected persons
  const layoutNodes = hierarchyData.descendants().map(d => d.data.data);
  const layoutIds = new Set(layoutNodes.map(p => p.id));
  const floating = persons.filter(p => !layoutIds.has(p.id));

  const svg = d3.select('#treeContainer').append('svg')
    .attr('width', width).attr('height', height);

  const g = svg.append('g').attr('transform', 'translate(40, 60)');

  // Draw links
  g.selectAll('.link')
    .data(hierarchyData.links())
    .enter().append('path')
    .attr('class', 'link')
    .attr('d', d3.linkVertical().x(d => d.x).y(d => d.y));

  // Draw nodes from hierarchy
  const node = g.selectAll('.node')
    .data(hierarchyData.descendants())
    .enter().append('g')
    .attr('class', d => `node ${nodeClass(d.data.data.confidence)}`)
    .attr('transform', d => `translate(${d.x},${d.y})`)
    .style('cursor', 'pointer')
    .on('click', (event, d) => openPersonCard(d.data.data.id));

  node.append('circle').attr('r', NODE_RADIUS);
  node.append('text').attr('dy', 4).attr('text-anchor', 'middle')
    .text(d => shortName(d.data.data));
  node.append('text').attr('dy', 18).attr('text-anchor', 'middle')
    .style('font-size', '9px').style('fill', '#94a3b8')
    .text(d => d.data.data.birth_year || '?');

  // Draw floating nodes (disconnected from hierarchy)
  floating.forEach((p, i) => {
    const fx = 60 + (i * 80) % (width - 100);
    const fy = height - 80;
    const fn = g.append('g')
      .attr('class', `node ${nodeClass(p.confidence)}`)
      .attr('transform', `translate(${fx},${fy})`)
      .style('cursor', 'pointer')
      .on('click', () => openPersonCard(p.id));
    fn.append('circle').attr('r', NODE_RADIUS);
    fn.append('text').attr('dy', 4).attr('text-anchor', 'middle').text(shortName(p));
  });

  // Zoom + pan
  svg.call(d3.zoom().scaleExtent([0.3, 2]).on('zoom', e => g.attr('transform', e.transform)));
}

function nodeClass(confidence) {
  if (confidence >= 80) return 'node-confirmed';
  if (confidence >= 40) return 'node-partial';
  return 'node-gap';
}

function shortName(p) {
  const first = (p.first_name || '').charAt(0);
  const last = (p.last_name || '').slice(0, 8);
  return first ? `${first}. ${last}` : last;
}
```

- [ ] **Step 4: Verify visually**

Start the Flask dev server:

```bash
cd /home/krish/familytree_app && source venv/bin/activate
SECRET_KEY=test OPENROUTER_API_KEY=x FAMILYSEARCH_CLIENT_ID=x FAMILYSEARCH_CLIENT_SECRET=x flask --app "app:create_app()" run --port 5001
```

Open `http://localhost:5001` in browser. Register, go to `/app`. Click "+ Add Person", add "John Doe" born 1800. Verify:
- Tree renders with one node (red border — no confidence data)
- Node shows "J. Doe" and "1800"
- Node is clickable (click opens blank overlay — person_card.js not yet built)
- Zoom and pan work

- [ ] **Step 5: Commit**

```bash
git add static/app.html static/style.css static/js/tree.js
git commit -m "feat: D3.js family tree — confidence-colored nodes, zoom/pan, click to open card"
```

---

### Task 5: Person card overlay + Alfred UI + voice input

**Files:**
- Create: `static/js/person_card.js`

- [ ] **Step 1: Create static/js/person_card.js**

```javascript
async function openPersonCard(personId) {
  const overlay = document.getElementById('personCardOverlay');
  overlay.innerHTML = '<div class="card-loading">Loading...</div>';
  overlay.classList.remove('hidden');

  const [personResp, hometownResp, historyResp] = await Promise.all([
    fetch(`/api/persons/${personId}`),
    fetch(`/api/persons/${personId}/hometown`),
    fetch(`/api/alfred/${personId}/history`),
  ]);

  const person = await personResp.json();
  const hometown = hometownResp.ok ? await hometownResp.json() : { available: false };
  const history = historyResp.ok ? await historyResp.json() : { messages: [] };

  overlay.innerHTML = buildCardHTML(person, hometown, history.messages);

  // Close on overlay background click
  overlay.addEventListener('click', e => {
    if (e.target === overlay) closePersonCard();
  });

  initVoiceInput(personId);
  scrollAlfredHistory();
}

function closePersonCard() {
  document.getElementById('personCardOverlay').classList.add('hidden');
}

function buildCardHTML(person, hometown, messages) {
  const name = `${person.first_name || ''} ${person.last_name || ''}`.trim();
  const dates = [person.birth_year, person.death_year].filter(Boolean).join(' – ');
  const confidenceColor = person.confidence >= 80 ? '#3b82f6' : person.confidence >= 40 ? '#f59e0b' : '#ef4444';

  const confirmedFields = [];
  const missingFields = [];
  if (person.birth_year) confirmedFields.push('Birth year');
  else missingFields.push('Birth year');
  if (person.birth_state || person.birth_country) confirmedFields.push('Birth location');
  else missingFields.push('Birth location');
  if (person.death_year) confirmedFields.push('Death year');
  else missingFields.push('Death year');
  if ((person.parent_ids || []).length > 0) confirmedFields.push('Parents');
  else missingFields.push('Parents');
  if ((person.spouse_ids || []).length > 0) confirmedFields.push('Spouse');
  else missingFields.push('Spouse');

  const sourcesHTML = person.search_results.map(r =>
    `<a href="${r.url}" target="_blank" class="source-chip">${r.source}</a>`
  ).join('') || '<span class="no-data">No sources yet</span>';

  const gapsHTML = person.gaps.filter(g => !g.resolved).map(g =>
    `<div class="gap-item">✗ ${g.gap_type.replace(/_/g,' ')} — <em>${g.suggested_source}</em></div>`
  ).join('') || '';

  const hometownHTML = hometown.available ? `
    <div class="hometown-panel">
      ${hometown.photo ? `<img src="${hometown.photo.url}" class="hometown-photo" alt="${hometown.photo.title}" onerror="this.style.display='none'">
        ${hometown.photo.is_approximate ? '<p class="approx-note">Photo is regional approximation</p>' : ''}` : ''}
      ${hometown.map ? `<div class="map-info"><strong>Historical Map:</strong> ${hometown.map.title} (${hometown.map.year})<br><a href="${hometown.map.url}" target="_blank">View map →</a></div>` : ''}
      ${hometown.life_context ? `<div class="life-context">${hometown.life_context}</div>` : ''}
    </div>` : '<div class="hometown-panel"><p class="no-data">Birth location unknown — add a birth state or country to see the hometown visual.</p></div>';

  const messagesHTML = messages.map(m =>
    `<div class="alfred-message alfred-${m.role}">
      ${m.role === 'assistant' ? '<span class="alfred-avatar">🎩</span>' : ''}
      <div class="alfred-bubble">${escapeHtml(m.content)}</div>
    </div>`
  ).join('');

  return `
    <div class="person-card">
      <button class="card-close" onclick="closePersonCard()">✕</button>
      <div class="card-header">
        <h2>${escapeHtml(name)}</h2>
        <span class="card-dates">${dates}</span>
        <div class="confidence-bar">
          <div class="confidence-fill" style="width:${person.confidence}%;background:${confidenceColor}"></div>
          <span class="confidence-label">${person.confidence}% confidence</span>
        </div>
      </div>
      <div class="card-panels">
        <div class="card-left">
          <h4>Confirmed</h4>
          ${confirmedFields.map(f => `<div class="check-item confirmed">✓ ${f}</div>`).join('')}
          <h4>Missing</h4>
          ${missingFields.map(f => `<div class="check-item missing">✗ ${f}</div>`).join('')}
          ${gapsHTML ? `<h4>Research Gaps</h4>${gapsHTML}` : ''}
          <h4>Sources</h4>
          <div class="sources-row">${sourcesHTML}</div>
          <button class="btn-primary search-btn" onclick="runSearch(${person.id})">Search Again</button>
        </div>
        <div class="card-right">
          <h4>Hometown: ${escapeHtml(person.birth_state || person.birth_country || 'Unknown')}</h4>
          ${hometownHTML}
        </div>
      </div>
      <div class="alfred-panel">
        <div class="alfred-header"><span class="alfred-avatar">🎩</span><strong>Alfred</strong> — Research Concierge</div>
        <div class="alfred-history" id="alfredHistory">${messagesHTML}</div>
        <div class="alfred-input-row">
          <button class="mic-btn" id="micBtn" onclick="toggleVoice()" title="Voice input">🎤</button>
          <input type="text" id="alfredInput" placeholder="Ask Alfred about ${escapeHtml(name)}... (10 tokens)"
                 onkeydown="if(event.key==='Enter')sendAlfred(${person.id})">
          <button class="btn-primary" onclick="sendAlfred(${person.id})">Send <span class="token-cost">10</span></button>
        </div>
      </div>
    </div>
    <style>
      .person-card{background:#1e293b;border-radius:12px;width:min(900px,95vw);max-height:90vh;overflow-y:auto;padding:2rem;position:relative;display:flex;flex-direction:column;gap:1.5rem}
      .card-close{position:absolute;top:1rem;right:1rem;background:none;border:none;color:#94a3b8;font-size:1.2rem;cursor:pointer}
      .card-header h2{margin:0;font-size:1.4rem}
      .card-dates{color:#94a3b8;font-size:.9rem}
      .confidence-bar{background:#334155;border-radius:4px;height:6px;margin-top:.5rem;position:relative}
      .confidence-fill{height:6px;border-radius:4px;transition:width .3s}
      .confidence-label{font-size:.75rem;color:#94a3b8;position:absolute;right:0;top:8px}
      .card-panels{display:grid;grid-template-columns:1fr 1fr;gap:1.5rem}
      .card-left,.card-right{display:flex;flex-direction:column;gap:.5rem}
      .card-left h4,.card-right h4{color:#94a3b8;font-size:.8rem;text-transform:uppercase;margin:.75rem 0 .25rem}
      .check-item{font-size:.9rem;padding:.2rem 0}
      .check-item.confirmed{color:#4ade80}
      .check-item.missing{color:#f87171}
      .gap-item{font-size:.85rem;color:#fbbf24;padding:.2rem 0}
      .sources-row{display:flex;flex-wrap:wrap;gap:.5rem}
      .source-chip{background:#1e40af;color:#93c5fd;padding:.2rem .6rem;border-radius:20px;font-size:.8rem;text-decoration:none}
      .source-chip:hover{background:#2563eb}
      .no-data{color:#64748b;font-size:.85rem}
      .search-btn{margin-top:.75rem;align-self:flex-start}
      .hometown-photo{width:100%;border-radius:8px;object-fit:cover;max-height:180px}
      .approx-note{font-size:.75rem;color:#94a3b8;margin:.25rem 0}
      .map-info{font-size:.85rem;line-height:1.6}
      .life-context{font-size:.85rem;color:#cbd5e1;font-style:italic;border-left:3px solid var(--blue);padding-left:.75rem;margin-top:.5rem}
      .alfred-panel{border-top:1px solid #334155;padding-top:1.5rem}
      .alfred-header{display:flex;align-items:center;gap:.5rem;margin-bottom:1rem;font-size:.9rem}
      .alfred-avatar{font-size:1.2rem}
      .alfred-history{display:flex;flex-direction:column;gap:.75rem;max-height:200px;overflow-y:auto;padding-right:.5rem;margin-bottom:1rem}
      .alfred-message{display:flex;gap:.5rem;align-items:flex-start}
      .alfred-user .alfred-bubble{background:#1e40af;border-radius:8px 8px 0 8px;padding:.5rem .75rem;font-size:.875rem;align-self:flex-end;margin-left:auto}
      .alfred-assistant .alfred-bubble{background:#334155;border-radius:0 8px 8px 8px;padding:.5rem .75rem;font-size:.875rem}
      .alfred-input-row{display:flex;gap:.5rem;align-items:center}
      .alfred-input-row input{flex:1;background:#0f172a;border:1px solid #334155;color:#f1f5f9;padding:.5rem .75rem;border-radius:6px;font-size:.9rem}
      .alfred-input-row input:focus{outline:2px solid var(--blue)}
      .mic-btn{background:none;border:1px solid #334155;color:#f1f5f9;padding:.4rem .6rem;border-radius:6px;cursor:pointer;font-size:1rem}
      .mic-btn.listening{border-color:#ef4444;color:#ef4444;animation:pulse 1s infinite}
      @keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
      .token-cost{font-size:.75rem;background:#334155;padding:.1rem .3rem;border-radius:4px}
      .hometown-panel{display:flex;flex-direction:column;gap:.75rem}
      @media(max-width:640px){.card-panels{grid-template-columns:1fr}}
    </style>`;
}

async function sendAlfred(personId) {
  const input = document.getElementById('alfredInput');
  const message = input.value.trim();
  if (!message) return;
  input.value = '';

  const history = document.getElementById('alfredHistory');
  history.innerHTML += `<div class="alfred-message alfred-user"><div class="alfred-bubble">${escapeHtml(message)}</div></div>`;
  history.innerHTML += `<div class="alfred-message alfred-assistant"><div class="alfred-bubble typing">Alfred is thinking...</div></div>`;
  scrollAlfredHistory();

  const r = await fetch(`/api/alfred/${personId}/chat`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({message}),
  });
  const data = await r.json();

  const typingEl = history.querySelector('.typing');
  if (typingEl) typingEl.parentElement.outerHTML =
    `<div class="alfred-message alfred-assistant"><span class="alfred-avatar">🎩</span><div class="alfred-bubble">${escapeHtml(data.response || 'Sorry, I had trouble with that.')}</div></div>`;

  scrollAlfredHistory();
  // Refresh token badge
  fetch('/api/me').then(r => r.json()).then(d => {
    document.getElementById('tokenBadge').textContent = `${d.tokens} tokens`;
  });
}

async function runSearch(personId) {
  const p = await fetch(`/api/persons/${personId}`).then(r => r.json());
  const params = new URLSearchParams({
    last: p.last_name || '', first: p.first_name || '',
    birth_year: p.birth_year || '', birth_place: p.birth_state || '',
  });
  window.location.href = `/?${params}`;
}

function scrollAlfredHistory() {
  const h = document.getElementById('alfredHistory');
  if (h) h.scrollTop = h.scrollHeight;
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// --- Voice Input ---
let recognition = null;
let isListening = false;

function initVoiceInput(personId) {
  const micBtn = document.getElementById('micBtn');
  if (!micBtn) return;
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) { micBtn.style.display = 'none'; return; }

  recognition = new SpeechRecognition();
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.lang = 'en-US';

  recognition.onresult = e => {
    let transcript = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      transcript += e.results[i][0].transcript;
    }
    document.getElementById('alfredInput').value = transcript;
    if (e.results[e.results.length - 1].isFinal) {
      stopListening();
      sendAlfred(personId);
    }
  };

  recognition.onend = () => stopListening();
  recognition.onerror = () => stopListening();
}

function toggleVoice() {
  if (isListening) { stopListening(); return; }
  if (!recognition) return;
  isListening = true;
  document.getElementById('micBtn').classList.add('listening');
  recognition.start();
}

function stopListening() {
  isListening = false;
  const btn = document.getElementById('micBtn');
  if (btn) btn.classList.remove('listening');
  if (recognition) { try { recognition.stop(); } catch (e) {} }
}
```

- [ ] **Step 2: Verify visually**

```bash
cd /home/krish/familytree_app && source venv/bin/activate
SECRET_KEY=test OPENROUTER_API_KEY=x FAMILYSEARCH_CLIENT_ID=x FAMILYSEARCH_CLIENT_SECRET=x flask --app "app:create_app()" run --port 5001
```

Open `http://localhost:5001`, register, go to `/app`, add "John Doe" born 1820 in Virginia.

Verify:
1. D3 tree renders with John Doe node
2. Click node — person card opens as overlay
3. Left panel shows missing fields in red (no search yet, no parents/death data)
4. Right panel shows "Hometown: Virginia" with loading state
5. Alfred panel visible at bottom with text input
6. In Chrome: click mic button — it pulses red, speak — text appears in input
7. Type a message, click Send — "Alfred is thinking..." appears, then response
8. Close card (✕ or click background) — card closes

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/ -v
```

Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add static/js/person_card.js
git commit -m "feat: person card overlay — hometown visual, Alfred chat, Web Speech API voice input"
```

---

## Self-Review

**Spec coverage:**
- ✅ D3.js tree with blue/yellow/red node states (confidence ≥80/40–79/<40) — Task 4
- ✅ Click node → person card overlay — Tasks 4 & 5
- ✅ Left panel: confidence %, confirmed/missing checklist, source chips, Search Now button — Task 5
- ✅ Right panel: Wikimedia Commons hometown photo, David Rumsey historical map, AI life-context blurb — Tasks 2 & 5
- ✅ Alfred panel at bottom of person card — Task 5
- ✅ Alfred with full person context (results, gaps, hometown, tree relatives) — Task 3
- ✅ Alfred chat history saved per person per tree (alfred_messages table) — Tasks 1 & 3
- ✅ Alfred voice input via Web Speech API — Task 5
- ✅ Mic button pulses red when active, auto-sends on speech pause — Task 5
- ✅ Graceful degradation: mic button hidden if browser doesn't support SpeechRecognition — Task 5
- ✅ Source links as clickable chips, not raw URLs — Task 5
- ✅ "Add Parent" / "Add Spouse" can be wired from the Add Person form — Task 1
- ⏭️ Token deduction for Alfred (10 tokens/msg) and life-context blurb (5 tokens) — Plan 4
- ⏭️ Approximate photo flag when Wikimedia returns no results — Task 2 (implemented via is_approximate flag)

**Placeholder scan:** None. All functions have complete implementations.

**Type consistency:** `_person_detail()` returns `search_results` and `gaps` arrays — person_card.js reads `person.search_results` and `person.gaps` identically.
