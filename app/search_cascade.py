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
    key = _cache_key(first, last, birth_year, birth_place)

    # Attempt cache read — skip gracefully if Redis is unavailable
    try:
        r = get_redis()
        cached = r.get(key)
        if cached:
            return json.loads(cached)
    except Exception:
        r = None

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
    if r is not None:
        r.setex(key, CACHE_TTL, json.dumps(output))
    return output
