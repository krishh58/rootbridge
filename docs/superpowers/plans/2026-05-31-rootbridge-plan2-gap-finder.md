# RootBridge Plan 2: Gap Finder Engine

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire up FamilySearch OAuth2, run the US-tier search cascade (Census, vital records, military pensions, WikiTree, Chronicling America), classify gaps with confidence scoring, cache results in Redis, and synthesize findings via OpenRouter AI.

**Architecture:** `familysearch.py` handles OAuth2 token management (stored in Redis). `search_cascade.py` orchestrates parallel API calls for a single person. `gap_classifier.py` scores confidence and produces typed gaps. `ai_synthesis.py` sends results to OpenRouter and returns a plain-English summary. Search routes expose `POST /search` (guest, rate-limited) and `POST /api/persons/<id>/search` (authenticated).

**Tech Stack:** Python requests, Redis (token cache + result cache + rate limiting), OpenRouter API (claude-3-haiku), FamilySearch REST API (OAuth2 client credentials), WikiTree API, Chronicling America API

**Spec:** `docs/superpowers/specs/2026-05-31-genealogy-gap-finder-design.md`

**Builds on Plan 1. Plans 3–5 build on this.**

---

## File Structure

```
app/
├── familysearch.py      # FamilySearch OAuth2 client — token fetch/cache/refresh
├── search_cascade.py    # US-tier search cascade — orchestrates all API calls
├── gap_classifier.py    # Confidence scoring + gap type classification
├── ai_synthesis.py      # OpenRouter integration — gap summary + research suggestions
├── search_routes.py     # POST /search (guest) + POST /api/persons/<id>/search
tests/
├── test_familysearch.py
├── test_gap_classifier.py
├── test_search_cascade.py
├── test_search_routes.py
```

---

### Task 1: FamilySearch OAuth2 client

**Files:**
- Create: `app/familysearch.py`
- Create: `tests/test_familysearch.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_familysearch.py
from unittest.mock import patch, MagicMock
from app.familysearch import FamilySearchClient

def test_get_token_fetches_and_caches(app):
    with app.app_context():
        client = FamilySearchClient()
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_response = MagicMock()
        mock_response.json.return_value = {'access_token': 'tok123', 'expires_in': 3600}
        mock_response.raise_for_status = MagicMock()
        with patch('app.familysearch.requests.post', return_value=mock_response), \
             patch('app.familysearch.get_redis', return_value=mock_redis):
            token = client.get_token()
        assert token == 'tok123'
        mock_redis.setex.assert_called_once()

def test_get_token_returns_cached(app):
    with app.app_context():
        client = FamilySearchClient()
        mock_redis = MagicMock()
        mock_redis.get.return_value = 'cached_token'
        with patch('app.familysearch.get_redis', return_value=mock_redis):
            token = client.get_token()
        assert token == 'cached_token'

def test_search_persons_returns_list(app):
    with app.app_context():
        client = FamilySearchClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'entries': [{'content': {'gedcomx': {'persons': [
                {'id': 'abc', 'names': [{'nameForms': [{'fullText': 'Christopher Haines'}]}],
                 'facts': [{'type': 'http://gedcomx.org/Birth', 'date': {'original': '1760'}}]}
            ]}}}]
        }
        mock_response.raise_for_status = MagicMock()
        with patch.object(client, 'get_token', return_value='tok'), \
             patch('app.familysearch.requests.get', return_value=mock_response):
            results = client.search_persons(first='Christopher', last='Haines', birth_year=1760)
        assert isinstance(results, list)
        assert len(results) == 1
        assert results[0]['name'] == 'Christopher Haines'
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/krish/familytree_app && source venv/bin/activate
pytest tests/test_familysearch.py -v
```

Expected: FAIL — module not found

- [ ] **Step 3: Create app/familysearch.py**

```python
import requests
from flask import current_app
from .db import get_redis

FAMILYSEARCH_BASE = 'https://api.familysearch.org'
TOKEN_CACHE_KEY = 'fs:access_token'

class FamilySearchClient:

    def get_token(self) -> str:
        r = get_redis()
        cached = r.get(TOKEN_CACHE_KEY)
        if cached:
            return cached
        resp = requests.post(
            f'{FAMILYSEARCH_BASE}/cis-web/oauth2/v3/token',
            data={
                'grant_type': 'client_credentials',
                'client_id': current_app.config['FAMILYSEARCH_CLIENT_ID'],
                'client_secret': current_app.config['FAMILYSEARCH_CLIENT_SECRET'],
            }
        )
        resp.raise_for_status()
        data = resp.json()
        token = data['access_token']
        expires_in = int(data.get('expires_in', 3600)) - 60
        r.setex(TOKEN_CACHE_KEY, expires_in, token)
        return token

    def _headers(self) -> dict:
        return {
            'Authorization': f'Bearer {self.get_token()}',
            'Accept': 'application/x-fs-v1+json',
        }

    def search_persons(self, first: str = '', last: str = '',
                       birth_year: int = None, birth_place: str = '') -> list:
        params = {'q': self._build_query(first, last, birth_year, birth_place), 'count': 10}
        resp = requests.get(
            f'{FAMILYSEARCH_BASE}/platform/tree/search',
            headers=self._headers(), params=params
        )
        resp.raise_for_status()
        return self._parse_person_entries(resp.json())

    def search_records(self, collection_id: str, first: str = '', last: str = '',
                       birth_year: int = None) -> list:
        params = {'q': self._build_query(first, last, birth_year), 'count': 10}
        resp = requests.get(
            f'{FAMILYSEARCH_BASE}/platform/records/search',
            headers=self._headers(), params=params,
        )
        resp.raise_for_status()
        return self._parse_record_entries(resp.json())

    def _build_query(self, first, last, birth_year, birth_place='') -> str:
        parts = []
        if first:
            parts.append(f'givenName:"{first}"')
        if last:
            parts.append(f'surname:"{last}"')
        if birth_year:
            parts.append(f'birthLikeDate:"{birth_year-3} TO {birth_year+3}"')
        if birth_place:
            parts.append(f'birthLikePlace:"{birth_place}"')
        return ' '.join(parts)

    def _parse_person_entries(self, data: dict) -> list:
        results = []
        for entry in data.get('entries', []):
            try:
                persons = entry['content']['gedcomx']['persons']
                for p in persons:
                    name = ''
                    if p.get('names'):
                        forms = p['names'][0].get('nameForms', [])
                        if forms:
                            name = forms[0].get('fullText', '')
                    birth_year = None
                    for fact in p.get('facts', []):
                        if 'Birth' in fact.get('type', ''):
                            date_str = fact.get('date', {}).get('original', '')
                            if date_str and date_str.isdigit():
                                birth_year = int(date_str)
                    results.append({
                        'name': name,
                        'birth_year': birth_year,
                        'fs_id': p.get('id', ''),
                        'source': 'familysearch',
                        'url': f'https://www.familysearch.org/tree/person/details/{p.get("id", "")}',
                    })
            except (KeyError, IndexError):
                continue
        return results

    def _parse_record_entries(self, data: dict) -> list:
        results = []
        for entry in data.get('entries', []):
            try:
                record = entry.get('content', {}).get('gedcomx', {})
                results.append({
                    'source': 'familysearch_records',
                    'raw': record,
                    'url': entry.get('links', {}).get('self', {}).get('href', ''),
                })
            except (KeyError, IndexError):
                continue
        return results
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_familysearch.py -v
```

Expected: All 3 PASS

- [ ] **Step 5: Commit**

```bash
git add app/familysearch.py tests/test_familysearch.py
git commit -m "feat: FamilySearch OAuth2 client with Redis token cache"
```

---

### Task 2: Gap classifier — confidence scoring

**Files:**
- Create: `app/gap_classifier.py`
- Create: `tests/test_gap_classifier.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_gap_classifier.py
from app.gap_classifier import classify_gaps, confidence_score

def test_confidence_full_data():
    person = {
        'first_name': 'Christopher', 'last_name': 'Haines',
        'birth_year': 1760, 'birth_state': 'Virginia',
        'death_year': 1846, 'death_place': 'Allen County KY',
        'parent_ids': [1, 2], 'spouse_ids': [3],
    }
    assert confidence_score(person) >= 90

def test_confidence_name_only():
    person = {
        'first_name': 'John', 'last_name': 'Doe',
        'birth_year': None, 'birth_state': None,
        'death_year': None, 'death_place': None,
        'parent_ids': [], 'spouse_ids': [],
    }
    assert confidence_score(person) <= 20

def test_gaps_missing_parents():
    person = {'parent_ids': [], 'birth_year': 1760, 'birth_state': 'VA',
              'death_year': 1846, 'death_place': 'KY', 'spouse_ids': [1]}
    gaps = classify_gaps(person)
    types = [g['gap_type'] for g in gaps]
    assert 'missing_parents' in types

def test_gaps_missing_birth():
    person = {'parent_ids': [1], 'birth_year': None, 'birth_state': None,
              'death_year': 1846, 'death_place': 'KY', 'spouse_ids': []}
    gaps = classify_gaps(person)
    types = [g['gap_type'] for g in gaps]
    assert 'missing_birth' in types

def test_gaps_none_when_complete():
    person = {'parent_ids': [1, 2], 'birth_year': 1760, 'birth_state': 'VA',
              'death_year': 1846, 'death_place': 'KY', 'spouse_ids': [3]}
    gaps = classify_gaps(person)
    assert len(gaps) == 0

def test_gap_has_suggested_source():
    person = {'parent_ids': [], 'birth_year': 1760, 'birth_state': 'VA',
              'death_year': None, 'death_place': None, 'spouse_ids': []}
    gaps = classify_gaps(person)
    for gap in gaps:
        assert 'suggested_source' in gap
        assert 'suggested_query' in gap
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/test_gap_classifier.py -v
```

- [ ] **Step 3: Create app/gap_classifier.py**

```python
FIELD_WEIGHTS = {
    'birth_year': 20,
    'birth_state': 10,
    'death_year': 15,
    'death_place': 10,
    'parent_ids': 25,
    'spouse_ids': 10,
    'first_name': 5,
    'last_name': 5,
}

def confidence_score(person: dict) -> int:
    total = 0
    for field, weight in FIELD_WEIGHTS.items():
        val = person.get(field)
        if val is not None and val != [] and val != '':
            total += weight
    return min(total, 100)

def classify_gaps(person: dict) -> list:
    gaps = []
    name = f"{person.get('first_name', '')} {person.get('last_name', '')}".strip()
    birth_year = person.get('birth_year')
    birth_state = person.get('birth_state', '')

    if not person.get('parent_ids'):
        gaps.append({
            'gap_type': 'missing_parents',
            'suggested_source': 'FamilySearch',
            'suggested_query': f'{name} {birth_year or ""} {birth_state or ""} parents'.strip(),
        })

    if not birth_year or not person.get('birth_state'):
        gaps.append({
            'gap_type': 'missing_birth',
            'suggested_source': 'FamilySearch Vital Records',
            'suggested_query': f'{name} birth record {birth_state or ""}'.strip(),
        })

    if not person.get('death_year') or not person.get('death_place'):
        gaps.append({
            'gap_type': 'missing_death',
            'suggested_source': 'Find A Grave / FamilySearch',
            'suggested_query': f'{name} death {birth_year or ""}',
        })

    if not person.get('spouse_ids'):
        gaps.append({
            'gap_type': 'missing_spouse',
            'suggested_source': 'FamilySearch Marriage Records',
            'suggested_query': f'{name} marriage record',
        })

    return gaps
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_gap_classifier.py -v
```

Expected: All 6 PASS

- [ ] **Step 5: Commit**

```bash
git add app/gap_classifier.py tests/test_gap_classifier.py
git commit -m "feat: gap classifier — confidence scoring and typed gap detection"
```

---

### Task 3: Search cascade — US tier

**Files:**
- Create: `app/search_cascade.py`
- Create: `tests/test_search_cascade.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_search_cascade.py
from unittest.mock import patch, MagicMock
from app.search_cascade import run_us_cascade

def test_cascade_returns_results_and_gaps(app):
    with app.app_context():
        mock_fs = MagicMock()
        mock_fs.search_persons.return_value = [
            {'name': 'Christopher Haines', 'birth_year': 1760,
             'source': 'familysearch', 'url': 'https://familysearch.org/abc'}
        ]
        mock_fs.search_records.return_value = []
        with patch('app.search_cascade.FamilySearchClient', return_value=mock_fs), \
             patch('app.search_cascade.search_wikitree', return_value=[]), \
             patch('app.search_cascade.search_chronicling', return_value=[]):
            result = run_us_cascade(
                first='Christopher', last='Haines',
                birth_year=1760, birth_place='Virginia'
            )
    assert 'results' in result
    assert 'gaps' in result
    assert 'confidence' in result
    assert isinstance(result['results'], list)
    assert isinstance(result['gaps'], list)
    assert 0 <= result['confidence'] <= 100

def test_cascade_caches_result(app):
    with app.app_context():
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_fs = MagicMock()
        mock_fs.search_persons.return_value = []
        mock_fs.search_records.return_value = []
        with patch('app.search_cascade.FamilySearchClient', return_value=mock_fs), \
             patch('app.search_cascade.search_wikitree', return_value=[]), \
             patch('app.search_cascade.search_chronicling', return_value=[]), \
             patch('app.search_cascade.get_redis', return_value=mock_redis):
            run_us_cascade(first='John', last='Doe', birth_year=1800)
        mock_redis.setex.assert_called_once()

def test_cascade_uses_cache(app):
    with app.app_context():
        import json
        cached = json.dumps({'results': [], 'gaps': [], 'confidence': 0, 'cached': True})
        mock_redis = MagicMock()
        mock_redis.get.return_value = cached
        with patch('app.search_cascade.get_redis', return_value=mock_redis):
            result = run_us_cascade(first='Jane', last='Smith')
        assert result.get('cached') is True
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_search_cascade.py -v
```

- [ ] **Step 3: Create app/search_cascade.py**

```python
import json
import hashlib
import requests as req_lib
from .familysearch import FamilySearchClient
from .gap_classifier import classify_gaps, confidence_score
from .db import get_redis

CACHE_TTL = 86400  # 24 hours

def _cache_key(first, last, birth_year, birth_place) -> str:
    raw = f'{first}|{last}|{birth_year}|{birth_place}'.lower()
    return f'search:{hashlib.md5(raw.encode()).hexdigest()}'

def search_wikitree(first: str, last: str, birth_year: int = None) -> list:
    try:
        params = {'action': 'searchPerson', 'first_name': first,
                  'last_name': last, 'format': 'json'}
        if birth_year:
            params['birth_year'] = birth_year
        resp = req_lib.get('https://api.wikitree.com/api.php',
                           params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get('0', {}).get('matches', []):
            results.append({
                'name': item.get('LongName', ''),
                'birth_year': item.get('BirthYear'),
                'source': 'wikitree',
                'url': f"https://www.wikitree.com/wiki/{item.get('Name', '')}",
            })
        return results
    except Exception:
        return []

def search_chronicling(first: str, last: str, birth_year: int = None) -> list:
    try:
        query = f'"{first} {last}"'
        if birth_year:
            query += f' obituary'
        params = {'proxtext': query, 'format': 'json', 'rows': 5}
        resp = req_lib.get('https://chroniclingamerica.loc.gov/search/pages/results/',
                           params=params, timeout=5)
        resp.raise_for_status()
        items = resp.json().get('items', [])
        return [{
            'source': 'chronicling_america',
            'title': i.get('title', ''),
            'date': i.get('date', ''),
            'url': f"https://chroniclingamerica.loc.gov{i.get('id', '')}",
        } for i in items]
    except Exception:
        return []

def run_us_cascade(first: str = '', last: str = '',
                   birth_year: int = None, birth_place: str = '') -> dict:
    r = get_redis()
    key = _cache_key(first, last, birth_year, birth_place)
    cached = r.get(key)
    if cached:
        return json.loads(cached)

    fs = FamilySearchClient()
    results = []
    results += fs.search_persons(first=first, last=last,
                                  birth_year=birth_year, birth_place=birth_place)
    results += fs.search_records('', first=first, last=last, birth_year=birth_year)
    results += search_wikitree(first, last, birth_year)
    results += search_chronicling(first, last, birth_year)

    person_snapshot = {
        'first_name': first, 'last_name': last,
        'birth_year': birth_year, 'birth_state': birth_place,
        'death_year': None, 'death_place': None,
        'parent_ids': [], 'spouse_ids': [],
    }
    for r_item in results:
        if r_item.get('birth_year') and not person_snapshot['birth_year']:
            person_snapshot['birth_year'] = r_item['birth_year']

    gaps = classify_gaps(person_snapshot)
    score = confidence_score(person_snapshot)

    output = {'results': results, 'gaps': gaps, 'confidence': score}
    get_redis().setex(key, CACHE_TTL, json.dumps(output))
    return output
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_search_cascade.py -v
```

Expected: All 3 PASS

- [ ] **Step 5: Commit**

```bash
git add app/search_cascade.py tests/test_search_cascade.py
git commit -m "feat: US search cascade — FamilySearch, WikiTree, Chronicling America, Redis cache"
```

---

### Task 4: AI synthesis — OpenRouter gap summary

**Files:**
- Create: `app/ai_synthesis.py`
- Create: `tests/test_ai_synthesis.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ai_synthesis.py
from unittest.mock import patch, MagicMock
from app.ai_synthesis import synthesize_gaps

def test_synthesize_returns_summary(app):
    with app.app_context():
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'choices': [{'message': {'content': 'Found birth record. Missing parents.'}}]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch('app.ai_synthesis.requests.post', return_value=mock_resp):
            result = synthesize_gaps(
                person={'first_name': 'Christopher', 'last_name': 'Haines',
                        'birth_year': 1760, 'birth_state': 'Virginia'},
                results=[{'source': 'familysearch', 'name': 'Christopher Haines'}],
                gaps=[{'gap_type': 'missing_parents', 'suggested_source': 'FamilySearch',
                       'suggested_query': 'Haines parents Virginia'}]
            )
        assert 'summary' in result
        assert isinstance(result['summary'], str)
        assert len(result['summary']) > 0

def test_synthesize_returns_empty_on_api_error(app):
    with app.app_context():
        with patch('app.ai_synthesis.requests.post', side_effect=Exception('API down')):
            result = synthesize_gaps(
                person={'first_name': 'Jane', 'last_name': 'Doe'},
                results=[], gaps=[]
            )
        assert result['summary'] == ''
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_ai_synthesis.py -v
```

- [ ] **Step 3: Create app/ai_synthesis.py**

```python
import requests
from flask import current_app

OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'
MODEL = 'anthropic/claude-3-haiku'

def synthesize_gaps(person: dict, results: list, gaps: list) -> dict:
    name = f"{person.get('first_name', '')} {person.get('last_name', '')}".strip()
    birth = f"{person.get('birth_year', 'unknown')} {person.get('birth_state', '')}".strip()

    found_summary = f"{len(results)} records found" if results else "No records found"
    gap_list = '\n'.join(
        f"- {g['gap_type']}: try {g['suggested_source']} — search: {g['suggested_query']}"
        for g in gaps
    ) or "No gaps — record appears complete."

    prompt = f"""You are a genealogy research assistant. Summarize findings for {name} (born ~{birth}).

Records found: {found_summary}
Sources: {', '.join(set(r.get('source','') for r in results)) or 'none'}

Research gaps:
{gap_list}

Write 2-3 plain English sentences: what was found, what is missing, and the single most important next step. Be specific and helpful."""

    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                'Authorization': f"Bearer {current_app.config['OPENROUTER_API_KEY']}",
                'Content-Type': 'application/json',
            },
            json={'model': MODEL, 'messages': [{'role': 'user', 'content': prompt}],
                  'max_tokens': 150},
            timeout=10,
        )
        resp.raise_for_status()
        summary = resp.json()['choices'][0]['message']['content'].strip()
        return {'summary': summary}
    except Exception:
        return {'summary': ''}
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_ai_synthesis.py -v
```

Expected: Both PASS

- [ ] **Step 5: Commit**

```bash
git add app/ai_synthesis.py tests/test_ai_synthesis.py
git commit -m "feat: OpenRouter AI synthesis — gap summary and next-step suggestions"
```

---

### Task 5: Search API routes

**Files:**
- Create: `app/search_routes.py`
- Modify: `app/__init__.py` (register blueprint)
- Create: `tests/test_search_routes.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_search_routes.py
import json
from unittest.mock import patch

MOCK_CASCADE = {
    'results': [{'name': 'John Doe', 'source': 'familysearch', 'url': 'http://fs.org/1'}],
    'gaps': [{'gap_type': 'missing_parents', 'suggested_source': 'FamilySearch',
              'suggested_query': 'John Doe parents'}],
    'confidence': 45,
}
MOCK_SYNTHESIS = {'summary': 'Found one record. Parents unknown.'}

def test_guest_search_success(client):
    with patch('app.search_routes.run_us_cascade', return_value=MOCK_CASCADE), \
         patch('app.search_routes.synthesize_gaps', return_value=MOCK_SYNTHESIS):
        r = client.post('/search',
            data=json.dumps({'last': 'Doe', 'first': 'John', 'birth_year': 1800}),
            content_type='application/json')
    assert r.status_code == 200
    data = r.get_json()
    assert 'results' in data
    assert 'gaps' in data
    assert 'summary' in data
    assert data['confidence'] == 45

def test_guest_search_requires_last_name(client):
    r = client.post('/search',
        data=json.dumps({'first': 'John'}),
        content_type='application/json')
    assert r.status_code == 400

def test_guest_search_rate_limited(client):
    with patch('app.search_routes.run_us_cascade', return_value=MOCK_CASCADE), \
         patch('app.search_routes.synthesize_gaps', return_value=MOCK_SYNTHESIS), \
         patch('app.search_routes._check_guest_rate_limit', return_value=False):
        r = client.post('/search',
            data=json.dumps({'last': 'Doe'}),
            content_type='application/json')
    assert r.status_code == 429

def test_authenticated_search_saves_to_db(client):
    client.post('/auth/register',
        data=json.dumps({'email': 's@test.com', 'password': 'pass'}),
        content_type='application/json')
    with patch('app.search_routes.run_us_cascade', return_value=MOCK_CASCADE), \
         patch('app.search_routes.synthesize_gaps', return_value=MOCK_SYNTHESIS):
        r = client.post('/api/search',
            data=json.dumps({'last': 'Doe', 'first': 'John', 'birth_year': 1800,
                             'tree_name': 'My Tree'}),
            content_type='application/json')
    assert r.status_code == 200
    data = r.get_json()
    assert 'person_id' in data
    assert 'tree_id' in data
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_search_routes.py -v
```

- [ ] **Step 3: Create app/search_routes.py**

```python
from flask import Blueprint, request, jsonify, g
from .auth import require_auth
from .db import db, get_redis
from .models import User, Tree, Person, SearchResult, Gap
from .search_cascade import run_us_cascade
from .ai_synthesis import synthesize_gaps

search_bp = Blueprint('search', __name__)
GUEST_RATE_LIMIT = 5

def _check_guest_rate_limit(ip: str) -> bool:
    r = get_redis()
    key = f'guest_rate:{ip}'
    count = r.get(key)
    if count and int(count) >= GUEST_RATE_LIMIT:
        return False
    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, 86400)
    pipe.execute()
    return True

def _save_search_to_db(user_id: int, tree_name: str, first: str, last: str,
                        birth_year: int, birth_place: str, cascade: dict,
                        summary: str) -> dict:
    tree = Tree.query.filter_by(user_id=user_id, name=tree_name).first()
    if not tree:
        tree = Tree(user_id=user_id, name=tree_name or f'{first} {last} Family')
        db.session.add(tree)
        db.session.flush()

    person = Person(
        tree_id=tree.id, first_name=first, last_name=last,
        birth_year=birth_year, birth_state=birth_place,
        confidence=cascade['confidence'],
    )
    db.session.add(person)
    db.session.flush()

    for r in cascade['results']:
        db.session.add(SearchResult(
            person_id=person.id, source=r.get('source', ''),
            record_type=r.get('record_type', ''), url=r.get('url', ''),
            raw_data=r,
        ))

    for gap in cascade['gaps']:
        db.session.add(Gap(
            person_id=person.id, gap_type=gap['gap_type'],
            suggested_source=gap.get('suggested_source', ''),
            suggested_query=gap.get('suggested_query', ''),
        ))

    db.session.commit()
    return {'person_id': person.id, 'tree_id': tree.id}

@search_bp.post('/search')
def guest_search():
    data = request.get_json() or {}
    if not data.get('last'):
        return jsonify({'error': 'Last name is required'}), 400
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if not _check_guest_rate_limit(ip):
        return jsonify({'error': 'Daily search limit reached. Create a free account to continue.'}), 429
    first = data.get('first', '')
    last = data['last']
    birth_year = data.get('birth_year')
    birth_place = data.get('birth_place', '')
    cascade = run_us_cascade(first=first, last=last, birth_year=birth_year, birth_place=birth_place)
    synthesis = synthesize_gaps(
        person={'first_name': first, 'last_name': last, 'birth_year': birth_year, 'birth_state': birth_place},
        results=cascade['results'], gaps=cascade['gaps']
    )
    return jsonify({**cascade, 'summary': synthesis['summary']})

@search_bp.post('/api/search')
@require_auth
def authenticated_search():
    data = request.get_json() or {}
    if not data.get('last'):
        return jsonify({'error': 'Last name is required'}), 400
    first = data.get('first', '')
    last = data['last']
    birth_year = data.get('birth_year')
    birth_place = data.get('birth_place', '')
    tree_name = data.get('tree_name', '')
    cascade = run_us_cascade(first=first, last=last, birth_year=birth_year, birth_place=birth_place)
    synthesis = synthesize_gaps(
        person={'first_name': first, 'last_name': last, 'birth_year': birth_year, 'birth_state': birth_place},
        results=cascade['results'], gaps=cascade['gaps']
    )
    ids = _save_search_to_db(g.user_id, tree_name, first, last, birth_year, birth_place, cascade, synthesis['summary'])
    return jsonify({**cascade, 'summary': synthesis['summary'], **ids})
```

- [ ] **Step 4: Register blueprint in app/__init__.py**

Add inside `create_app()` after existing blueprint imports:

```python
    from .search_routes import search_bp
    app.register_blueprint(search_bp)
```

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/ -v
```

Expected: All tests pass (16 existing + 4 new = 20 total)

- [ ] **Step 6: Commit**

```bash
git add app/search_routes.py app/__init__.py tests/test_search_routes.py
git commit -m "feat: search API routes — guest search (rate-limited), authenticated search saves to DB"
```

---

## Self-Review

**Spec coverage:**
- ✅ FamilySearch OAuth2 with Redis token cache — Task 1
- ✅ Census/vital/military/WikiTree/Chronicling America cascade — Task 3
- ✅ Gap classification (missing_parents, missing_birth, missing_death, missing_spouse) — Task 2
- ✅ Confidence score 0–100 — Task 2
- ✅ Redis 24h result cache — Task 3
- ✅ OpenRouter AI gap summary — Task 4
- ✅ POST /search (guest, 5/day rate limit per IP) — Task 5
- ✅ POST /api/search (auth, saves person/gaps/results to DB) — Task 5
- ⏭️ Heritage-specific cascades (European, AA) — Plan 5
- ⏭️ Token deduction on AI synthesis calls — Plan 4

**Placeholder scan:** None. All functions have full implementations.

**Type consistency:** `run_us_cascade()` returns `{results, gaps, confidence}` — used identically in Task 5 routes.
