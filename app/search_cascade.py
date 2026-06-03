import json
import hashlib
import os
import requests as req_lib
from .gap_classifier import classify_gaps, confidence_score
from .db import get_redis

CACHE_TTL = 86400
DPLA_KEY = os.environ.get('DPLA_API_KEY', '')
DPLA_URL = 'https://api.dp.la/v2/items'


def _cache_key(first, last, birth_year, birth_place) -> str:
    raw = f'{first}|{last}|{birth_year}|{birth_place}'.lower()
    return f'search:{hashlib.md5(raw.encode()).hexdigest()}'


def search_dpla(first: str, last: str, birth_year: int = None,
                subject: str = '', page_size: int = 5) -> list:
    if not DPLA_KEY:
        return []
    try:
        q = f'"{first} {last}"' if first else f'"{last}"'
        params = {'q': q, 'api_key': DPLA_KEY, 'page_size': page_size}
        if subject:
            params['sourceResource.subject.name'] = subject
        if birth_year:
            params['sourceResource.date.begin'] = birth_year - 5
            params['sourceResource.date.end'] = birth_year + 5
        resp = req_lib.get(DPLA_URL, params=params, timeout=8)
        resp.raise_for_status()
        docs = resp.json().get('docs', [])
        results = []
        for doc in docs:
            sr = doc.get('sourceResource', {})
            titles = sr.get('title', [])
            title = titles[0] if titles else ''
            date = sr.get('date', {})
            display_date = date.get('displayDate', '') if isinstance(date, dict) else ''
            results.append({
                'source': 'dpla',
                'record_type': subject or 'general',
                'title': title,
                'date': display_date,
                'provider': doc.get('provider', {}).get('name', ''),
                'data_provider': doc.get('dataProvider', ''),
                'url': doc.get('isShownAt', ''),
            })
        return results
    except Exception:
        return []


def search_dpla_census(first: str, last: str, birth_year: int = None,
                       birth_place: str = '') -> list:
    if not DPLA_KEY:
        return []
    try:
        q = f'"{first} {last}"' if first else f'"{last}"'
        if birth_place:
            q += f' {birth_place}'
        params = {
            'q': q,
            'api_key': DPLA_KEY,
            'page_size': 8,
            'sourceResource.subject.name': 'census',
        }
        resp = req_lib.get(DPLA_URL, params=params, timeout=8)
        resp.raise_for_status()
        docs = resp.json().get('docs', [])
        results = []
        for doc in docs:
            sr = doc.get('sourceResource', {})
            titles = sr.get('title', [])
            results.append({
                'source': 'dpla_census',
                'record_type': 'census',
                'title': titles[0] if titles else '',
                'provider': doc.get('provider', {}).get('name', ''),
                'data_provider': doc.get('dataProvider', ''),
                'url': doc.get('isShownAt', ''),
            })
        return results
    except Exception:
        return []


def search_dpla_military(first: str, last: str, birth_year: int = None) -> list:
    if not DPLA_KEY:
        return []
    try:
        q = f'"{first} {last}"' if first else f'"{last}"'
        params = {
            'q': q,
            'api_key': DPLA_KEY,
            'page_size': 5,
            'sourceResource.subject.name': 'military records',
        }
        resp = req_lib.get(DPLA_URL, params=params, timeout=8)
        resp.raise_for_status()
        docs = resp.json().get('docs', [])
        results = []
        for doc in docs:
            sr = doc.get('sourceResource', {})
            titles = sr.get('title', [])
            results.append({
                'source': 'dpla_military',
                'record_type': 'military',
                'title': titles[0] if titles else '',
                'provider': doc.get('provider', {}).get('name', ''),
                'data_provider': doc.get('dataProvider', ''),
                'url': doc.get('isShownAt', ''),
            })
        return results
    except Exception:
        return []


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
                'record_type': 'family_tree',
                'url': f"https://www.wikitree.com/wiki/{item.get('Name', '')}",
            })
        return results
    except Exception:
        return []


def search_chronicling(first: str, last: str, birth_year: int = None) -> list:
    try:
        query = f'"{first} {last}"'
        if birth_year:
            query += ' obituary'
        params = {'proxtext': query, 'format': 'json', 'rows': 5}
        resp = req_lib.get('https://chroniclingamerica.loc.gov/search/pages/results/',
                           params=params, timeout=5)
        resp.raise_for_status()
        items = resp.json().get('items', [])
        return [{
            'source': 'chronicling_america',
            'record_type': 'newspaper',
            'title': i.get('title', ''),
            'date': i.get('date', ''),
            'url': f"https://chroniclingamerica.loc.gov{i.get('id', '')}",
        } for i in items]
    except Exception:
        return []


def run_us_cascade(first: str = '', last: str = '',
                   birth_year: int = None, birth_place: str = '') -> dict:
    key = _cache_key(first, last, birth_year, birth_place)

    try:
        r = get_redis()
        cached = r.get(key)
        if cached:
            return json.loads(cached)
    except Exception:
        r = None

    results = []
    results += search_wikitree(first, last, birth_year)
    results += search_chronicling(first, last, birth_year)
    results += search_dpla_census(first, last, birth_year, birth_place)
    results += search_dpla_military(first, last, birth_year)
    results += search_dpla(first, last, birth_year, page_size=5)

    person_snapshot = {
        'first_name': first, 'last_name': last,
        'birth_year': birth_year, 'birth_state': birth_place,
        'death_year': None, 'death_place': None,
        'parent_ids': [], 'spouse_ids': [],
    }
    for r_item in results:
        if r_item.get('birth_year') and not person_snapshot['birth_year']:
            person_snapshot['birth_year'] = r_item['birth_year']
        if r_item.get('death_year') and not person_snapshot['death_year']:
            person_snapshot['death_year'] = r_item['death_year']
        if r_item.get('death_place') and not person_snapshot['death_place']:
            person_snapshot['death_place'] = r_item['death_place']

    gaps = classify_gaps(person_snapshot)
    score = confidence_score(person_snapshot)

    output = {'results': results, 'gaps': gaps, 'confidence': score}
    if r is not None:
        try:
            r.setex(key, CACHE_TTL, json.dumps(output))
        except Exception:
            pass
    return output
