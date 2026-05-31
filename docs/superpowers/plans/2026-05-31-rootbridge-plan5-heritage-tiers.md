# RootBridge Plan 5: Heritage Tiers

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the European Roots search cascade (Ellis Island ship manifests → origin village → FamilySearch EU church records, Riksarkivet, Matricula Online) and the African American Heritage cascade (Freedmen's Bureau, slave schedules 1850–1860, WPA narratives, 1870 wall detection and guidance), plus AI document translation for non-English records.

**Architecture:** `heritage_cascade.py` contains `run_european_cascade()` and `run_aa_cascade()`. `translation.py` handles OpenRouter AI translation for German/Irish/Italian/Polish/French documents. `heritage_routes.py` exposes tier-gated endpoints that check `user.tier` before running the appropriate cascade. The 1870 wall detector is a function in `heritage_cascade.py` that triggers a specific guidance alert when AA heritage persons are born before 1870 with no parents found.

**Tech Stack:** Python requests, FamilySearch REST API (same OAuth2 client from Plan 2), Riksarkivet API, Chronicling America API (WPA narratives), NARA/Ellis Island SOAP/REST API, OpenRouter claude-3-haiku for translation

**Spec:** `docs/superpowers/specs/2026-05-31-genealogy-gap-finder-design.md`

**Builds on Plans 1–4. This is the final plan — completes the MVP.**

---

## File Structure

```
app/
├── heritage_cascade.py   # run_european_cascade(), run_aa_cascade(), detect_1870_wall()
├── translation.py        # translate_document() via OpenRouter
├── heritage_routes.py    # POST /api/heritage/<person_id>/european, /aa
tests/
├── test_heritage_cascade.py
├── test_translation.py
├── test_heritage_routes.py
```

---

### Task 1: Ellis Island ship manifest + European entry point

**Files:**
- Create: `app/heritage_cascade.py` (partial — Ellis Island + entry detection)
- Create: `tests/test_heritage_cascade.py` (partial)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_heritage_cascade.py
from unittest.mock import patch, MagicMock
from app.heritage_cascade import search_ellis_island, extract_origin_village

def test_ellis_island_returns_manifests(app):
    with app.app_context():
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'entries': [{
                'content': {'gedcomx': {'persons': [{
                    'id': 'M123',
                    'names': [{'nameForms': [{'fullText': 'Johann Mueller'}]}],
                    'facts': [
                        {'type': 'http://gedcomx.org/Arrival', 'date': {'original': '1882'},
                         'place': {'original': 'New York'}},
                        {'type': 'http://gedcomx.org/Birth',
                         'place': {'original': 'Bavaria, Germany'}},
                    ]
                }]}}
            }]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch('app.heritage_cascade.FamilySearchClient') as MockFS:
            MockFS.return_value.get_token.return_value = 'tok'
            MockFS.return_value.search_records.return_value = [{
                'source': 'familysearch_records',
                'raw': mock_resp.json.return_value['entries'][0]['content']['gedcomx'],
                'url': 'https://familysearch.org/ark:/...',
            }]
            results = search_ellis_island(first='Johann', last='Mueller', birth_year=1860)
    assert isinstance(results, list)

def test_extract_origin_village_from_manifest():
    manifest_raw = {
        'persons': [{
            'facts': [
                {'type': 'http://gedcomx.org/Birth', 'place': {'original': 'Dachau, Bavaria, Germany'}},
            ]
        }]
    }
    village = extract_origin_village(manifest_raw)
    assert 'Bavaria' in village or 'Germany' in village

def test_extract_origin_village_returns_empty_on_missing():
    village = extract_origin_village({})
    assert village == ''
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/krish/familytree_app && source venv/bin/activate
pytest tests/test_heritage_cascade.py -v
```

- [ ] **Step 3: Create app/heritage_cascade.py**

```python
import requests as req_lib
from .familysearch import FamilySearchClient
from .gap_classifier import classify_gaps, confidence_score
from .db import get_redis
import hashlib, json

CACHE_TTL = 86400

def _cache_key(prefix, first, last, birth_year):
    raw = f'{prefix}|{first}|{last}|{birth_year}'.lower()
    return f'heritage:{hashlib.md5(raw.encode()).hexdigest()}'

# --- ELLIS ISLAND / NARA ---

def search_ellis_island(first: str, last: str, birth_year: int = None) -> list:
    fs = FamilySearchClient()
    # NARA immigration collections on FamilySearch: passenger lists 1820-1957
    collection_ids = ['1849782', '1923067']  # Immigration Index, Passenger Lists
    results = []
    for cid in collection_ids:
        try:
            records = fs.search_records(cid, first=first, last=last, birth_year=birth_year)
            results.extend(records)
        except Exception:
            continue
    return results

def extract_origin_village(manifest_raw: dict) -> str:
    for person in manifest_raw.get('persons', []):
        for fact in person.get('facts', []):
            fact_type = fact.get('type', '')
            if 'Birth' in fact_type or 'Birthplace' in fact_type or 'NativePlace' in fact_type:
                place = fact.get('place', {}).get('original', '')
                if place:
                    return place
    return ''

# --- EUROPEAN CHURCH RECORDS ---

def search_riksarkivet(first: str, last: str, birth_year: int = None) -> list:
    try:
        params = {
            'firstname': first, 'lastname': last,
            'recordtype': 'birth',
        }
        if birth_year:
            params['fromyear'] = birth_year - 5
            params['toyear'] = birth_year + 5
        resp = req_lib.get(
            'https://sok.riksarkivet.se/api/person',
            params=params, timeout=5
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get('records', []):
            results.append({
                'source': 'riksarkivet',
                'name': item.get('name', ''),
                'birth_year': item.get('birth_year'),
                'parish': item.get('parish', ''),
                'url': item.get('url', 'https://sok.riksarkivet.se/'),
                'record_type': 'church_record',
                'language': 'sv',
            })
        return results
    except Exception:
        return []

def search_familysearch_eu(first: str, last: str, birth_year: int = None,
                            origin_country: str = '') -> list:
    fs = FamilySearchClient()
    # EU record collections on FamilySearch
    eu_collections = {
        'Germany': ['2177832', '2178574'],   # German church books, civil registration
        'Ireland': ['1408347', '1923321'],   # Irish civil registration, church records
        'Italy': ['1401494'],                # Italian civil registration
        'Poland': ['2178993'],               # Polish metrical books
        'Sweden': ['1554443'],               # Swedish household examination records
    }
    results = []
    collections = eu_collections.get(origin_country, list(eu_collections.values())[0])
    for cid in collections[:2]:  # limit to 2 collections per call
        try:
            records = fs.search_records(cid, first=first, last=last, birth_year=birth_year)
            for r in records:
                r['source'] = f'familysearch_eu_{origin_country.lower()}'
            results.extend(records)
        except Exception:
            continue
    return results

def run_european_cascade(first: str = '', last: str = '', birth_year: int = None,
                          birth_place: str = '', origin_country: str = '') -> dict:
    r = get_redis()
    key = _cache_key('eu', first, last, birth_year)
    cached = r.get(key)
    if cached:
        return json.loads(cached)

    results = []

    # Step 1: Ellis Island manifest → origin village
    manifests = search_ellis_island(first, last, birth_year)
    results.extend(manifests)
    detected_country = origin_country
    if manifests and not detected_country:
        for m in manifests:
            village = extract_origin_village(m.get('raw', {}))
            if village:
                # Simple country detection from village string
                for country in ('Germany', 'Ireland', 'Italy', 'Poland', 'Sweden',
                                'England', 'Scotland', 'France', 'Austria'):
                    if country.lower() in village.lower():
                        detected_country = country
                        break
                break

    # Step 2: EU church records via FamilySearch
    eu_records = search_familysearch_eu(first, last, birth_year, detected_country)
    results.extend(eu_records)

    # Step 3: Riksarkivet for Swedish heritage
    if detected_country in ('Sweden', '') or 'sweden' in birth_place.lower():
        riksarkivet_records = search_riksarkivet(first, last, birth_year)
        results.extend(riksarkivet_records)

    person_snapshot = {
        'first_name': first, 'last_name': last,
        'birth_year': birth_year, 'birth_state': birth_place,
        'birth_country': detected_country,
        'death_year': None, 'death_place': None,
        'parent_ids': [], 'spouse_ids': [],
    }
    gaps = classify_gaps(person_snapshot)
    score = confidence_score(person_snapshot)

    # Note detected origin country in results metadata
    output = {
        'results': results, 'gaps': gaps, 'confidence': score,
        'detected_country': detected_country,
        'cascade_type': 'european',
    }
    r.setex(key, CACHE_TTL, json.dumps(output))
    return output

# --- AFRICAN AMERICAN HERITAGE ---

def detect_1870_wall(birth_year: int = None, parent_ids: list = None) -> dict | None:
    if not birth_year:
        return None
    if birth_year < 1870 and not parent_ids:
        return {
            'type': '1870_wall',
            'message': (
                'This person was born before 1870. '
                'The 1870 US Census is the first federal census to record African Americans by name. '
                'Tracing ancestry beyond this point requires specialized records.'
            ),
            'guidance': [
                'Search Freedmen\'s Bureau records (1865–1872) on FamilySearch — '
                'labor contracts and ration records often name formerly enslaved people and their families.',
                'Check the 1850 and 1860 Slave Schedules — they list enslaved people by age/sex under '
                'the slaveholder\'s name. Search for the slaveholder in the 1870 census to find '
                'freedpeople who stayed nearby.',
                'WPA Slave Narratives (Library of Congress) contain first-person accounts — '
                'search for the county of residence.',
                'Plantation records and estate inventories in state archives sometimes name '
                'enslaved individuals. Search the slaveholder\'s county courthouse records.',
            ],
            'sources': [
                {'name': 'Freedmen\'s Bureau Records (FamilySearch)', 'url': 'https://www.familysearch.org/search/collection/1596973'},
                {'name': 'Slave Schedules 1850 (FamilySearch)', 'url': 'https://www.familysearch.org/search/collection/1420440'},
                {'name': 'Slave Schedules 1860 (FamilySearch)', 'url': 'https://www.familysearch.org/search/collection/1420440'},
                {'name': 'WPA Slave Narratives (Library of Congress)', 'url': 'https://www.loc.gov/collections/slave-narratives-from-the-federal-writers-project-1936-to-1938/'},
            ],
        }
    return None

def search_freedmens_bureau(first: str, last: str, birth_year: int = None) -> list:
    fs = FamilySearchClient()
    # Freedmen's Bureau records collections on FamilySearch
    bureau_collections = ['1596973', '1989505']  # Labor contracts, ration records
    results = []
    for cid in bureau_collections:
        try:
            records = fs.search_records(cid, first=first, last=last, birth_year=birth_year)
            for r in records:
                r['source'] = 'freedmens_bureau'
                r['record_type'] = 'freedmens_bureau'
            results.extend(records)
        except Exception:
            continue
    return results

def search_slave_schedules(last: str, birth_year: int = None) -> list:
    fs = FamilySearchClient()
    # Slave schedules 1850 and 1860 — search by probable slaveholder surname
    schedule_collections = ['1420440', '1420441']
    results = []
    for cid in schedule_collections:
        try:
            records = fs.search_records(cid, first='', last=last, birth_year=birth_year)
            for r in records:
                r['source'] = 'slave_schedule'
                r['record_type'] = 'slave_schedule'
                r['note'] = ('Slave schedules list age/sex only, not names. '
                             'This record is for the slaveholder with this surname.')
            results.extend(records)
        except Exception:
            continue
    return results

def search_wpa_narratives(first: str, last: str, birth_state: str = '') -> list:
    try:
        query = f'"{first} {last}"'
        if birth_state:
            query += f' {birth_state}'
        params = {
            'q': query,
            'fo': 'json',
            'c': 'wpa',  # WPA collection filter
        }
        resp = req_lib.get(
            'https://www.loc.gov/search/',
            params=params, timeout=5
        )
        resp.raise_for_status()
        items = resp.json().get('results', [])
        return [{
            'source': 'wpa_narratives',
            'record_type': 'slave_narrative',
            'title': i.get('title', ''),
            'url': f"https://www.loc.gov{i.get('id', '')}",
        } for i in items[:3]]
    except Exception:
        return []

def run_aa_cascade(first: str = '', last: str = '', birth_year: int = None,
                   birth_place: str = '') -> dict:
    r = get_redis()
    key = _cache_key('aa', first, last, birth_year)
    cached = r.get(key)
    if cached:
        return json.loads(cached)

    results = []

    # Freedmen's Bureau
    results.extend(search_freedmens_bureau(first, last, birth_year))

    # Slave schedules (search by surname as probable slaveholder name)
    results.extend(search_slave_schedules(last, birth_year))

    # WPA Narratives
    results.extend(search_wpa_narratives(first, last, birth_place))

    person_snapshot = {
        'first_name': first, 'last_name': last,
        'birth_year': birth_year, 'birth_state': birth_place,
        'death_year': None, 'death_place': None,
        'parent_ids': [], 'spouse_ids': [],
    }
    gaps = classify_gaps(person_snapshot)
    score = confidence_score(person_snapshot)
    wall = detect_1870_wall(birth_year, [])

    output = {
        'results': results, 'gaps': gaps, 'confidence': score,
        'wall_1870': wall, 'cascade_type': 'african_american',
    }
    r.setex(key, CACHE_TTL, json.dumps(output))
    return output
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_heritage_cascade.py -v
```

Expected: All 3 PASS

- [ ] **Step 5: Commit**

```bash
git add app/heritage_cascade.py tests/test_heritage_cascade.py
git commit -m "feat: heritage cascade — Ellis Island, EU church records, Riksarkivet, Freedmen's Bureau, slave schedules, WPA narratives"
```

---

### Task 2: 1870 wall detection tests

**Files:**
- Modify: `tests/test_heritage_cascade.py` — add 1870 wall tests

- [ ] **Step 1: Write additional tests for 1870 wall**

Add these tests to `tests/test_heritage_cascade.py`:

```python
from app.heritage_cascade import detect_1870_wall, run_aa_cascade

def test_1870_wall_detected_for_pre_1870_no_parents():
    wall = detect_1870_wall(birth_year=1845, parent_ids=[])
    assert wall is not None
    assert wall['type'] == '1870_wall'
    assert len(wall['guidance']) >= 4
    assert len(wall['sources']) >= 4

def test_1870_wall_not_triggered_with_parents():
    wall = detect_1870_wall(birth_year=1845, parent_ids=[1, 2])
    assert wall is None

def test_1870_wall_not_triggered_post_1870():
    wall = detect_1870_wall(birth_year=1875, parent_ids=[])
    assert wall is None

def test_1870_wall_not_triggered_no_birth_year():
    wall = detect_1870_wall(birth_year=None, parent_ids=[])
    assert wall is None

def test_aa_cascade_includes_wall_alert(app):
    with app.app_context():
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        with patch('app.heritage_cascade.FamilySearchClient') as MockFS, \
             patch('app.heritage_cascade.get_redis', return_value=mock_redis), \
             patch('app.heritage_cascade.search_wpa_narratives', return_value=[]):
            MockFS.return_value.search_records.return_value = []
            result = run_aa_cascade(
                first='Moses', last='Johnson', birth_year=1840, birth_place='Georgia'
            )
    assert result['wall_1870'] is not None
    assert result['cascade_type'] == 'african_american'
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/test_heritage_cascade.py -v
```

Expected: All tests PASS (including 3 from Task 1 + 5 new)

- [ ] **Step 3: Commit**

```bash
git add tests/test_heritage_cascade.py
git commit -m "test: 1870 wall detection coverage — pre/post 1870, with/without parents"
```

---

### Task 3: AI document translation

**Files:**
- Create: `app/translation.py`
- Create: `tests/test_translation.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_translation.py
from unittest.mock import patch, MagicMock
from app.translation import translate_document, detect_language

def test_detect_language_german():
    text = 'Geburtsname Johann Mueller geboren am 15 März 1842 in Bayern'
    lang = detect_language(text)
    assert lang == 'de'

def test_detect_language_english():
    text = 'Born in the county of Cork Ireland on the third day of May'
    lang = detect_language(text)
    assert lang == 'en'

def test_detect_language_unknown():
    lang = detect_language('')
    assert lang == 'unknown'

def test_translate_document_returns_translation(app):
    with app.app_context():
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'choices': [{'message': {'content': '{"translation": "Birth record for Johann Mueller, born March 15, 1842 in Bavaria.", "key_fields": {"name": "Johann Mueller", "birth_date": "March 15, 1842", "birth_place": "Bavaria"}}'}}]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch('app.translation.requests.post', return_value=mock_resp):
            result = translate_document(
                text='Geburtsname Johann Mueller geboren am 15 März 1842 in Bayern',
                source_language='de'
            )
    assert 'translation' in result
    assert 'key_fields' in result

def test_translate_document_returns_empty_on_error(app):
    with app.app_context():
        with patch('app.translation.requests.post', side_effect=Exception('API error')):
            result = translate_document(text='text', source_language='de')
    assert result == {}
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_translation.py -v
```

- [ ] **Step 3: Create app/translation.py**

```python
import re
import json as jsonlib
import requests
from flask import current_app

OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'

LANGUAGE_PATTERNS = {
    'de': r'\b(geboren|gestorben|Geburtsname|verheiratet|Taufe|Pfarrei|Bayern|Preußen)\b',
    'fr': r'\b(né|née|mort|décédé|mariage|paroisse|département)\b',
    'sv': r'\b(född|döpt|begraven|kyrkbok|socken|husförhör)\b',
    'it': r'\b(nato|nata|morto|battesimo|matrimonio|comune|provincia)\b',
    'pl': r'\b(urodzony|urodzona|chrzciny|ślub|parafia|gmina)\b',
    'ga': r'\b(baistí|pósadh|bás|paróiste|contae)\b',
}

def detect_language(text: str) -> str:
    if not text:
        return 'unknown'
    for lang, pattern in LANGUAGE_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE):
            return lang
    ascii_ratio = sum(1 for c in text if ord(c) < 128) / max(len(text), 1)
    if ascii_ratio > 0.95:
        return 'en'
    return 'unknown'

def translate_document(text: str, source_language: str = 'de') -> dict:
    lang_names = {
        'de': 'German', 'fr': 'French', 'sv': 'Swedish',
        'it': 'Italian', 'pl': 'Polish', 'ga': 'Irish Gaelic',
    }
    lang_name = lang_names.get(source_language, source_language)
    prompt = f"""Translate this {lang_name} genealogical record to English. Extract key genealogical fields.

TEXT:
{text[:2000]}

Respond with ONLY valid JSON in this exact format:
{{"translation": "full English translation here", "key_fields": {{"name": "...", "birth_date": "...", "birth_place": "...", "death_date": "...", "death_place": "...", "father": "...", "mother": "...", "spouse": "..."}}}}

Omit any key_fields that are not mentioned in the document."""
    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                'Authorization': f"Bearer {current_app.config['OPENROUTER_API_KEY']}",
                'Content-Type': 'application/json',
            },
            json={
                'model': 'anthropic/claude-3-haiku',
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 400,
            },
            timeout=15,
        )
        resp.raise_for_status()
        content = resp.json()['choices'][0]['message']['content'].strip()
        # Strip markdown code fences if present
        content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content, flags=re.MULTILINE).strip()
        return jsonlib.loads(content)
    except Exception:
        return {}
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_translation.py -v
```

Expected: All 5 PASS

- [ ] **Step 5: Commit**

```bash
git add app/translation.py tests/test_translation.py
git commit -m "feat: AI document translation — language detection, German/French/Swedish/Italian/Polish/Irish, key field extraction"
```

---

### Task 4: Heritage routes — tier-gated endpoints

**Files:**
- Create: `app/heritage_routes.py`
- Modify: `app/__init__.py` — register blueprint
- Create: `tests/test_heritage_routes.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_heritage_routes.py
import json
from unittest.mock import patch, MagicMock
from app.models import User

MOCK_EU_CASCADE = {
    'results': [{'source': 'familysearch_eu_germany', 'url': 'https://fs.org/1'}],
    'gaps': [], 'confidence': 60, 'detected_country': 'Germany', 'cascade_type': 'european'
}
MOCK_AA_CASCADE = {
    'results': [{'source': 'freedmens_bureau', 'url': 'https://fs.org/2'}],
    'gaps': [], 'confidence': 40,
    'wall_1870': None, 'cascade_type': 'african_american'
}

def _setup_user_with_tier(client, app, tier='european'):
    client.post('/auth/register',
        data=json.dumps({'email': f'{tier}@test.com', 'password': 'pass'}),
        content_type='application/json')
    with app.app_context():
        user = User.query.filter_by(email=f'{tier}@test.com').first()
        user.tier = tier
        user.token_balance = 500
        from app.db import db
        db.session.commit()

def _create_person(client, tier):
    r = client.post('/api/persons',
        data=json.dumps({'tree_name': 'T', 'first_name': 'J', 'last_name': 'D',
                         'birth_year': 1850, 'birth_state': 'Ohio'}),
        content_type='application/json')
    return r.get_json()['person_id']

def test_european_cascade_requires_european_tier(client, app):
    client.post('/auth/register',
        data=json.dumps({'email': 'free_eu@test.com', 'password': 'pass'}),
        content_type='application/json')
    r = client.post('/api/persons',
        data=json.dumps({'tree_name': 'T', 'first_name': 'J', 'last_name': 'D'}),
        content_type='application/json')
    person_id = r.get_json()['person_id']
    r2 = client.post(f'/api/heritage/{person_id}/european',
        data=json.dumps({}), content_type='application/json')
    assert r2.status_code == 403

def test_european_cascade_allowed_for_european_tier(client, app):
    _setup_user_with_tier(client, app, 'european')
    person_id = _create_person(client, 'european')
    with patch('app.heritage_routes.run_european_cascade', return_value=MOCK_EU_CASCADE), \
         patch('app.heritage_routes.synthesize_gaps', return_value={'summary': 'Found German records.'}):
        r = client.post(f'/api/heritage/{person_id}/european',
            data=json.dumps({'origin_country': 'Germany'}),
            content_type='application/json')
    assert r.status_code == 200
    data = r.get_json()
    assert data['cascade_type'] == 'european'
    assert 'summary' in data

def test_european_cascade_allowed_for_all_tier(client, app):
    _setup_user_with_tier(client, app, 'all')
    person_id = _create_person(client, 'all')
    with patch('app.heritage_routes.run_european_cascade', return_value=MOCK_EU_CASCADE), \
         patch('app.heritage_routes.synthesize_gaps', return_value={'summary': 'Found records.'}):
        r = client.post(f'/api/heritage/{person_id}/european',
            data=json.dumps({}), content_type='application/json')
    assert r.status_code == 200

def test_aa_cascade_requires_aa_tier(client, app):
    client.post('/auth/register',
        data=json.dumps({'email': 'free_aa@test.com', 'password': 'pass'}),
        content_type='application/json')
    r = client.post('/api/persons',
        data=json.dumps({'tree_name': 'T', 'first_name': 'J', 'last_name': 'D'}),
        content_type='application/json')
    person_id = r.get_json()['person_id']
    r2 = client.post(f'/api/heritage/{person_id}/aa',
        data=json.dumps({}), content_type='application/json')
    assert r2.status_code == 403

def test_aa_cascade_returns_wall_alert(client, app):
    _setup_user_with_tier(client, app, 'aa')
    person_id = _create_person(client, 'aa')
    wall_cascade = {**MOCK_AA_CASCADE, 'wall_1870': {
        'type': '1870_wall', 'message': 'Born before 1870.', 'guidance': ['Check Freedmen\'s Bureau'], 'sources': []
    }}
    with patch('app.heritage_routes.run_aa_cascade', return_value=wall_cascade), \
         patch('app.heritage_routes.synthesize_gaps', return_value={'summary': 'Limited pre-1870 records.'}):
        r = client.post(f'/api/heritage/{person_id}/aa',
            data=json.dumps({}), content_type='application/json')
    assert r.status_code == 200
    data = r.get_json()
    assert data['wall_1870'] is not None
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_heritage_routes.py -v
```

- [ ] **Step 3: Create app/heritage_routes.py**

```python
from flask import Blueprint, request, jsonify, g
from .auth import require_auth
from .token_middleware import require_tokens
from .db import db
from .models import User, Person, Tree, SearchResult, Gap
from .heritage_cascade import run_european_cascade, run_aa_cascade
from .ai_synthesis import synthesize_gaps

heritage_bp = Blueprint('heritage', __name__)

EUROPEAN_TIERS = {'european', 'all'}
AA_TIERS = {'aa', 'all'}

def _require_tier(allowed_tiers: set):
    def decorator(f):
        from functools import wraps
        @wraps(f)
        def wrapper(*args, **kwargs):
            user = User.query.get(g.user_id)
            if not user or user.tier not in allowed_tiers:
                return jsonify({
                    'error': 'This heritage cascade requires an upgraded subscription.',
                    'required_tiers': list(allowed_tiers),
                    'current_tier': user.tier if user else 'unknown',
                    'upgrade_url': '/pricing',
                }), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator

def _save_heritage_results(person_id: int, cascade: dict):
    for r in cascade.get('results', []):
        existing = SearchResult.query.filter_by(
            person_id=person_id, url=r.get('url', '')
        ).first()
        if not existing and r.get('url'):
            db.session.add(SearchResult(
                person_id=person_id,
                source=r.get('source', ''),
                record_type=r.get('record_type', ''),
                url=r.get('url', ''),
                raw_data=r,
            ))
    for gap in cascade.get('gaps', []):
        existing = Gap.query.filter_by(
            person_id=person_id, gap_type=gap['gap_type']
        ).first()
        if not existing:
            db.session.add(Gap(
                person_id=person_id,
                gap_type=gap['gap_type'],
                suggested_source=gap.get('suggested_source', ''),
                suggested_query=gap.get('suggested_query', ''),
            ))
    person = Person.query.get(person_id)
    if person and cascade.get('confidence', 0) > person.confidence:
        person.confidence = cascade['confidence']
    db.session.commit()

@heritage_bp.post('/api/heritage/<int:person_id>/european')
@require_auth
@_require_tier(EUROPEAN_TIERS)
@require_tokens(40)
def european_cascade(person_id):
    person = Person.query.join(Tree).filter(
        Person.id == person_id, Tree.user_id == g.user_id
    ).first_or_404()
    data = request.get_json() or {}
    origin_country = data.get('origin_country', '')
    cascade = run_european_cascade(
        first=person.first_name or '',
        last=person.last_name or '',
        birth_year=person.birth_year,
        birth_place=person.birth_state or '',
        origin_country=origin_country,
    )
    _save_heritage_results(person_id, cascade)
    synthesis = synthesize_gaps(
        person={'first_name': person.first_name, 'last_name': person.last_name,
                'birth_year': person.birth_year, 'birth_state': person.birth_state},
        results=cascade['results'],
        gaps=cascade['gaps'],
    )
    return jsonify({**cascade, 'summary': synthesis['summary']})

@heritage_bp.post('/api/heritage/<int:person_id>/aa')
@require_auth
@_require_tier(AA_TIERS)
@require_tokens(40)
def aa_cascade(person_id):
    person = Person.query.join(Tree).filter(
        Person.id == person_id, Tree.user_id == g.user_id
    ).first_or_404()
    cascade = run_aa_cascade(
        first=person.first_name or '',
        last=person.last_name or '',
        birth_year=person.birth_year,
        birth_place=person.birth_state or '',
    )
    _save_heritage_results(person_id, cascade)
    synthesis = synthesize_gaps(
        person={'first_name': person.first_name, 'last_name': person.last_name,
                'birth_year': person.birth_year, 'birth_state': person.birth_state},
        results=cascade['results'],
        gaps=cascade['gaps'],
    )
    return jsonify({**cascade, 'summary': synthesis['summary']})
```

- [ ] **Step 4: Register blueprint in app/__init__.py**

Add inside `create_app()` after existing blueprint imports:

```python
    from .heritage_routes import heritage_bp
    app.register_blueprint(heritage_bp)
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_heritage_routes.py -v
```

Expected: All 5 PASS

- [ ] **Step 6: Run full suite**

```bash
pytest tests/ -v
```

Expected: All tests pass (45+ total)

- [ ] **Step 7: Commit**

```bash
git add app/heritage_routes.py app/__init__.py tests/test_heritage_routes.py
git commit -m "feat: heritage tier routes — European and AA cascades, tier enforcement, 40-token gate"
```

---

### Task 5: Wire heritage buttons into the Person Card UI

**Files:**
- Modify: `static/js/person_card.js` — add "Search European Records" and "Search AA Records" buttons

- [ ] **Step 1: Add heritage search buttons to person_card.js**

In `static/js/person_card.js`, find the line in `buildCardHTML` that contains the `search-btn` button:

```javascript
          <button class="btn-primary search-btn" onclick="runSearch(${person.id})">Search Again</button>
```

Replace it with:

```javascript
          <button class="btn-primary search-btn" onclick="runSearch(${person.id})">Search US Records</button>
          <button class="btn-secondary search-btn" onclick="runHeritageSearch(${person.id}, 'european')" title="Requires European Roots tier">Search European Records (40 tokens)</button>
          <button class="btn-secondary search-btn" onclick="runHeritageSearch(${person.id}, 'aa')" title="Requires AA Heritage tier">Search AA Records (40 tokens)</button>
```

- [ ] **Step 2: Add runHeritageSearch function to person_card.js**

Append this function to `static/js/person_card.js`:

```javascript
async function runHeritageSearch(personId, type) {
  const btn = event.target;
  btn.disabled = true;
  btn.textContent = 'Searching...';

  const payload = {};
  if (type === 'european') {
    const country = prompt('Enter origin country (e.g. Germany, Ireland, Italy, Poland, Sweden):');
    if (country) payload.origin_country = country;
  }

  const r = await fetch(`/api/heritage/${personId}/${type}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });

  btn.disabled = false;
  btn.textContent = type === 'european' ? 'Search European Records (40 tokens)' : 'Search AA Records (40 tokens)';

  if (r.status === 403) {
    const data = await r.json();
    alert(`This feature requires an upgrade. Current tier: ${data.current_tier}. Visit /pricing to upgrade.`);
    return;
  }
  if (r.status === 402) {
    alert('Insufficient tokens. Please top up to run a deep heritage search (40 tokens required).');
    return;
  }
  if (!r.ok) { alert('Search failed. Please try again.'); return; }

  const data = await r.json();

  // Show 1870 wall alert if present
  if (data.wall_1870) {
    const wall = data.wall_1870;
    const guidance = wall.guidance.map(g => `• ${g}`).join('\n');
    alert(`⚠️ 1870 Wall Detected\n\n${wall.message}\n\nNext steps:\n${guidance}`);
  }

  // Reload the person card with fresh data
  openPersonCard(personId);

  // Refresh token badge
  fetch('/api/me').then(r => r.json()).then(d => {
    document.getElementById('tokenBadge').textContent = `${d.tokens} tokens`;
  });
}
```

- [ ] **Step 3: Add btn-secondary style to static/style.css**

Append to the end of `static/style.css`:

```css
.btn-secondary { background: transparent; color: var(--blue); border: 1px solid var(--blue); padding: .5rem 1rem; border-radius: 6px; cursor: pointer; font-size: .875rem; }
.btn-secondary:hover { background: rgba(59,130,246,.1); }
```

- [ ] **Step 4: Verify visually**

```bash
cd /home/krish/familytree_app && source venv/bin/activate
SECRET_KEY=test OPENROUTER_API_KEY=x FAMILYSEARCH_CLIENT_ID=x FAMILYSEARCH_CLIENT_SECRET=x flask --app "app:create_app()" run --port 5001
```

Open `http://localhost:5001`, register, add a person, click the node, open person card. Verify:
1. Three search buttons appear in the left panel: "Search US Records", "Search European Records (40 tokens)", "Search AA Records (40 tokens)"
2. Click "Search European Records" on a free account — 403 alert: "requires an upgrade"
3. No JS errors in browser console

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/ -v
```

Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add static/js/person_card.js static/style.css
git commit -m "feat: heritage search buttons in person card — European and AA with tier/token gating, 1870 wall alert"
```

---

## Self-Review

**Spec coverage:**
- ✅ Ellis Island manifest search → origin village extraction — Task 1
- ✅ FamilySearch EU partition (German/Irish/Italian/Polish/Swedish church records) — Task 1
- ✅ Riksarkivet Swedish church records — Task 1
- ✅ Freedmen's Bureau records (1865–1872) via FamilySearch — Task 1
- ✅ US Slave Schedules 1850–1860 via FamilySearch — Task 1
- ✅ WPA Slave Narratives via Library of Congress API — Task 1
- ✅ 1870 wall detection: born pre-1870 + no parents → specific guidance + source links — Tasks 1 & 2
- ✅ AI translation: German/French/Swedish/Italian/Polish/Irish — Task 3
- ✅ Language detection from document text — Task 3
- ✅ European cascade gated to european/all tiers — Task 4
- ✅ AA cascade gated to aa/all tiers — Task 4
- ✅ Both cascades cost 40 tokens — Task 4
- ✅ Heritage results saved to DB (deduped) — Task 4
- ✅ Heritage search buttons in person card with tier/token error handling — Task 5
- ✅ 1870 wall shown as modal alert in UI — Task 5
- ⏭️ Matricula Online (German/Austrian/Czech Catholic parish records) — post-MVP (requires separate API evaluation)
- ⏭️ Asian/Pacific Heritage cascade — post-MVP Phase 2 (spec)
- ⏭️ Hispanic/Latino, Native American, Jewish heritage tiers — post-MVP Phase 2 (spec)

**Placeholder scan:** None. All functions have complete implementations including all external API calls.

**Type consistency:**
- `run_european_cascade()` and `run_aa_cascade()` both return `{results, gaps, confidence, cascade_type}` — heritage_routes.py uses these fields identically.
- `detect_1870_wall()` returns `dict | None` — caller checks `if data.wall_1870` before displaying.
- `translate_document()` returns `{}` on error — callers should check for empty dict before using.
