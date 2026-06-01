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
    collection_ids = ['1849782', '1923067']
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
    eu_collections = {
        'Germany': ['2177832', '2178574'],
        'Ireland': ['1408347', '1923321'],
        'Italy': ['1401494'],
        'Poland': ['2178993'],
        'Sweden': ['1554443'],
        'England': ['1526539', '2285338'],
        'Scotland': ['1551520', '1771076'],
        'France': ['1403145'],
        'Austria': ['1915777'],
    }
    results = []
    collections = eu_collections.get(origin_country, [])
    if not collections:
        return results
    for cid in collections[:2]:
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
    try:
        r = get_redis()
        key = _cache_key('eu', first, last, birth_year)
        cached = r.get(key)
        if cached:
            return json.loads(cached)
    except Exception:
        r = None

    results = []

    manifests = search_ellis_island(first, last, birth_year)
    results.extend(manifests)
    detected_country = origin_country
    if manifests and not detected_country:
        for m in manifests:
            village = extract_origin_village(m.get('raw', {}))
            if village:
                for country in ('Germany', 'Ireland', 'Italy', 'Poland', 'Sweden',
                                'England', 'Scotland', 'France', 'Austria'):
                    if country.lower() in village.lower():
                        detected_country = country
                        break
                if detected_country:
                    break

    eu_records = search_familysearch_eu(first, last, birth_year, detected_country)
    results.extend(eu_records)

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

    output = {
        'results': results, 'gaps': gaps, 'confidence': score,
        'detected_country': detected_country,
        'cascade_type': 'european',
    }
    if r is not None:
        try:
            r.setex(key, CACHE_TTL, json.dumps(output))
        except Exception:
            pass
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
                "Search Freedmen's Bureau records (1865–1872) on FamilySearch — "
                "labor contracts and ration records often name formerly enslaved people and their families.",
                "Check the 1850 and 1860 Slave Schedules — they list enslaved people by age/sex under "
                "the slaveholder's name. Search for the slaveholder in the 1870 census to find "
                "freedpeople who stayed nearby.",
                "WPA Slave Narratives (Library of Congress) contain first-person accounts — "
                "search for the county of residence.",
                "Plantation records and estate inventories in state archives sometimes name "
                "enslaved individuals. Search the slaveholder's county courthouse records.",
            ],
            'sources': [
                {'name': "Freedmen's Bureau Records (FamilySearch)", 'url': 'https://www.familysearch.org/search/collection/1596973'},
                {'name': 'Slave Schedules 1850 (FamilySearch)', 'url': 'https://www.familysearch.org/search/collection/1420440'},
                {'name': 'Slave Schedules 1860 (FamilySearch)', 'url': 'https://www.familysearch.org/search/collection/1420440'},
                {'name': 'WPA Slave Narratives (Library of Congress)', 'url': 'https://www.loc.gov/collections/slave-narratives-from-the-federal-writers-project-1936-to-1938/'},
            ],
        }
    return None

def search_freedmens_bureau(first: str, last: str, birth_year: int = None) -> list:
    fs = FamilySearchClient()
    bureau_collections = ['1596973', '1989505']
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
            'c': 'wpa',
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
    try:
        r = get_redis()
        key = _cache_key('aa', first, last, birth_year)
        cached = r.get(key)
        if cached:
            return json.loads(cached)
    except Exception:
        r = None

    results = []
    results.extend(search_freedmens_bureau(first, last, birth_year))
    results.extend(search_slave_schedules(last, birth_year))
    results.extend(search_wpa_narratives(first, last, birth_place))

    person_snapshot = {
        'first_name': first, 'last_name': last,
        'birth_year': birth_year, 'birth_state': birth_place,
        'death_year': None, 'death_place': None,
        'parent_ids': [], 'spouse_ids': [],
    }
    gaps = classify_gaps(person_snapshot)
    score = confidence_score(person_snapshot)
    wall = detect_1870_wall(birth_year, None)

    output = {
        'results': results, 'gaps': gaps, 'confidence': score,
        'wall_1870': wall, 'cascade_type': 'african_american',
    }
    if r is not None:
        try:
            r.setex(key, CACHE_TTL, json.dumps(output))
        except Exception:
            pass
    return output
