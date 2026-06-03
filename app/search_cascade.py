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


def _dpla_date(sr: dict) -> str:
    date = sr.get('date')
    if isinstance(date, dict):
        return date.get('displayDate', '')
    if isinstance(date, list) and date:
        d = date[0]
        return d.get('displayDate', '') if isinstance(d, dict) else ''
    return ''


def _dpla_title(sr: dict) -> str:
    titles = sr.get('title', [])
    if isinstance(titles, list):
        return titles[0] if titles else ''
    return str(titles)


def search_dpla(first: str, last: str, birth_year: int = None,
                subject: str = '', page_size: int = 5) -> list:
    if not DPLA_KEY:
        return []
    try:
        q = f'{first} {last}'.strip() if first else last
        params = {'q': q, 'api_key': DPLA_KEY, 'page_size': page_size}
        if subject:
            params['sourceResource.subject.name'] = subject
        resp = req_lib.get(DPLA_URL, params=params, timeout=8)
        resp.raise_for_status()
        docs = resp.json().get('docs', [])
        results = []
        for doc in docs:
            sr = doc.get('sourceResource', {})
            dp = doc.get('dataProvider', '')
            if isinstance(dp, dict):
                dp = dp.get('name', '')
            results.append({
                'source': 'dpla',
                'record_type': subject or 'general',
                'title': _dpla_title(sr),
                'date': _dpla_date(sr),
                'provider': doc.get('provider', {}).get('name', ''),
                'data_provider': dp,
                'url': doc.get('isShownAt', ''),
            })
        return results
    except Exception:
        return []


def _dpla_search(q: str, record_type: str, page_size: int = 6) -> list:
    """Core DPLA fetch — q already includes record type keywords."""
    if not DPLA_KEY:
        return []
    try:
        params = {'q': q, 'api_key': DPLA_KEY, 'page_size': page_size}
        resp = req_lib.get(DPLA_URL, params=params, timeout=8)
        resp.raise_for_status()
        docs = resp.json().get('docs', [])
        results = []
        for doc in docs:
            sr = doc.get('sourceResource', {})
            dp = doc.get('dataProvider', '')
            if isinstance(dp, dict):
                dp = dp.get('name', '')
            results.append({
                'source': f'dpla_{record_type}',
                'record_type': record_type,
                'title': _dpla_title(sr),
                'date': _dpla_date(sr),
                'provider': doc.get('provider', {}).get('name', ''),
                'data_provider': dp,
                'url': doc.get('isShownAt', ''),
            })
        return results
    except Exception:
        return []


def search_dpla_census(first: str, last: str, birth_year: int = None,
                       birth_place: str = '') -> list:
    name = f'{first} {last}'.strip() if first else last
    q = f'{name} born {birth_place}' if birth_place else f'{name} born'
    return _dpla_search(q, 'census', page_size=6)


def search_dpla_military(first: str, last: str, birth_year: int = None) -> list:
    name = f'{first} {last}'.strip() if first else last
    return _dpla_search(f'{name} military service soldier', 'military', page_size=5)


def search_wikitree(first: str, last: str, birth_year: int = None) -> list:
    try:
        params = {'action': 'searchPerson', 'firstName': first,
                  'lastName': last, 'format': 'json'}
        resp = req_lib.get('https://api.wikitree.com/api.php',
                           params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        matches = []
        if isinstance(data, list) and data:
            matches = data[0].get('matches', [])
        elif isinstance(data, dict):
            matches = data.get('0', {}).get('matches', [])
        results = []
        for item in matches[:5]:
            item_birth = item.get('BirthYear')
            if birth_year and item_birth and abs(int(item_birth) - birth_year) > 15:
                continue
            results.append({
                'name': item.get('LongName', ''),
                'birth_year': item_birth,
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


def _name_tokens(s: str) -> set:
    return set(s.lower().split()) if s else set()


def _year_close(a, b, window: int = 10) -> bool:
    try:
        return abs(int(a) - int(b)) <= window
    except (TypeError, ValueError):
        return False


def cross_reference(results: list, first: str, last: str,
                    birth_year: int = None, birth_place: str = '') -> list:
    """
    Score each result by how many other sources corroborate it.
    A result is corroborated when another result from a different source
    contains the search name tokens AND is within the birth year window.
    Adds 'corroborated_by' (list of sources) and 'corroboration_score' to each result.
    Sorts corroborated results to the top.
    """
    target_tokens = _name_tokens(f'{first} {last}')
    state_token = birth_place.lower() if birth_place else ''

    def _relevance(r: dict) -> dict:
        title = (r.get('title') or r.get('name') or '').lower()
        title_tokens = set(title.split())
        name_match = len(target_tokens & title_tokens) / max(len(target_tokens), 1)
        year_match = _year_close(r.get('birth_year'), birth_year) if birth_year else False
        place_match = state_token and state_token in title
        return {
            'name_match': name_match,
            'year_match': year_match,
            'place_match': place_match,
            'relevant': name_match >= 0.5,
        }

    scored = []
    for i, r in enumerate(results):
        rel = _relevance(r)
        corroborated_by = []
        for j, other in enumerate(results):
            if i == j:
                continue
            if other.get('source') == r.get('source'):
                continue
            other_rel = _relevance(other)
            if other_rel['relevant'] and rel['relevant']:
                corroborated_by.append(other['source'])
        r = dict(r)
        r['corroborated_by'] = list(set(corroborated_by))
        r['corroboration_score'] = len(r['corroborated_by'])
        r['name_match_score'] = round(rel['name_match'], 2)
        r['place_match'] = rel['place_match']
        scored.append(r)

    # Sort: corroborated first, then by name match score
    scored.sort(key=lambda x: (x['corroboration_score'], x['name_match_score']), reverse=True)
    return scored


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

    raw_results = []
    raw_results += search_wikitree(first, last, birth_year)
    raw_results += search_chronicling(first, last, birth_year)
    raw_results += search_dpla_census(first, last, birth_year, birth_place)
    raw_results += search_dpla_military(first, last, birth_year)
    raw_results += search_dpla(first, last, birth_year, page_size=5)

    results = cross_reference(raw_results, first, last, birth_year, birth_place)

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

    # Boost confidence when multiple sources corroborate
    corroborated_count = sum(1 for r in results if r.get('corroboration_score', 0) > 0)
    gaps = classify_gaps(person_snapshot)
    score = confidence_score(person_snapshot)
    if corroborated_count >= 2:
        score = min(score + 15, 95)
    elif corroborated_count == 1:
        score = min(score + 7, 95)

    output = {
        'results': results,
        'gaps': gaps,
        'confidence': score,
        'corroborated_count': corroborated_count,
    }
    if r is not None:
        try:
            r.setex(key, CACHE_TTL, json.dumps(output))
        except Exception:
            pass
    return output
