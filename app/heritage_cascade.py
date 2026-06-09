import os
import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests as req_lib
from .gap_classifier import classify_gaps, confidence_score
from .db import get_redis
from .search_cascade import search_dpla

logger = logging.getLogger(__name__)

CACHE_TTL = 86400

_KNOWN_COUNTRIES = ('germany', 'ireland', 'italy', 'poland', 'sweden',
                    'england', 'scotland', 'france', 'austria', 'norway',
                    'netherlands', 'denmark', 'hungary', 'russia', 'ukraine')


def _cache_key(prefix, first, last, birth_year):
    raw = f'{prefix}|{first}|{last}|{birth_year}'.lower()
    return f'heritage:{hashlib.md5(raw.encode()).hexdigest()}'


def _detect_country(results: list, origin_country: str) -> str:
    """Try to detect origin country from result birth_place / origin fields."""
    if origin_country:
        return origin_country
    for r in results:
        origin = (r.get('birth_place') or r.get('origin') or '').lower()
        for country in _KNOWN_COUNTRIES:
            if country in origin:
                return country.capitalize()
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

def search_dpla_eu(first: str, last: str, birth_year: int = None,
                   origin_country: str = '') -> list:
    """Search DPLA for European immigration and vital records."""
    subject = 'immigration'
    if origin_country:
        results = search_dpla(first, last, birth_year,
                              subject=f'{origin_country.lower()} immigration', page_size=5)
        if not results:
            results = search_dpla(first, last, birth_year, subject='immigration', page_size=5)
    else:
        results = search_dpla(first, last, birth_year, subject='immigration', page_size=5)
    for r in results:
        r['source'] = f'dpla_eu_{origin_country.lower()}' if origin_country else 'dpla_eu'
        r['record_type'] = 'european_immigration'
    return results

def run_european_cascade(first: str = '', last: str = '', birth_year: int = None,
                          birth_place: str = '', origin_country: str = '') -> dict:
    try:
        redis = get_redis()
        key = _cache_key('eu', first, last, birth_year)
        cached = redis.get(key)
        if cached:
            return json.loads(cached)
    except Exception:
        redis = None

    # Import the production search functions built in search_cascade.py
    from .search_cascade import (
        _search_ship_arrivals_db_safe,
        search_ship_manifests,
        search_castle_garden,
        search_ellis_island   as _sc_ellis_island,
        search_hamburg_emigrant,
        search_revwar_pensions,
    )

    _first = first.split()[0] if first else first
    _country = (origin_country or '').lower()
    _is_german  = 'german' in _country or 'germany' in _country
    _is_swedish = 'sweden' in _country or 'sweden' in birth_place.lower()
    _is_irish   = 'ireland' in _country or 'irish' in _country

    tasks: dict = {}

    # ── Local ship arrivals DB — instant, no network ──────────────────────
    tasks['ship_arrivals_db'] = lambda: _search_ship_arrivals_db_safe(
        last, _first, birth_year
    )

    # ── Internet Archive ship manifests — colonial + Hamburg ──────────────
    tasks['ship_manifests'] = lambda: search_ship_manifests(
        _first, last, birth_year, origin_country
    )

    # ── Port-specific: Castle Garden 1820–1892 (NY arrivals) ─────────────
    if birth_year is None or 1820 <= birth_year <= 1950:
        tasks['castle_garden'] = lambda: search_castle_garden(_first, last, birth_year)

    # ── Port-specific: Ellis Island 1892–1957 ────────────────────────────
    if birth_year is None or 1850 <= birth_year <= 1970:
        tasks['ellis_island'] = lambda: _sc_ellis_island(_first, last, birth_year)

    # ── Hamburg / German departure records ───────────────────────────────
    if _is_german or not origin_country:
        if birth_year is None or 1800 <= birth_year <= 1950:
            tasks['hamburg_emigrant'] = lambda: search_hamburg_emigrant(
                _first, last, birth_year, origin_country
            )

    # ── Rev War pension files (US veterans with European origins, 1775-1840)
    if birth_year and 1730 <= birth_year <= 1800:
        tasks['revwar_pension'] = lambda: search_revwar_pensions(
            _first, last, birth_year, birth_place
        )

    # ── Swedish church records ────────────────────────────────────────────
    if _is_swedish:
        tasks['riksarkivet'] = lambda: search_riksarkivet(first, last, birth_year)

    # ── DPLA European immigration (catch-all) ─────────────────────────────
    tasks['dpla_eu'] = lambda: search_dpla_eu(first, last, birth_year, origin_country)

    # ── Run all sources in parallel ───────────────────────────────────────
    results: list = []
    with ThreadPoolExecutor(max_workers=min(len(tasks), 8)) as executor:
        futures = {executor.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(futures, timeout=25):
            name = futures[future]
            try:
                batch = future.result(timeout=3) or []
                results.extend(batch)
            except Exception as exc:
                logger.debug('European source %s failed: %s', name, exc)

    detected_country = _detect_country(results, origin_country)

    person_snapshot = {
        'first_name': first, 'last_name': last,
        'birth_year': birth_year, 'birth_state': birth_place,
        'birth_country': detected_country,
        'death_year': None, 'death_place': None,
        'parent_ids': [], 'spouse_ids': [],
    }
    gaps  = classify_gaps(person_snapshot, results)
    score = confidence_score(person_snapshot)

    output = {
        'results':          results,
        'gaps':             gaps,
        'confidence':       score,
        'detected_country': detected_country,
        'cascade_type':     'european',
    }
    if redis is not None:
        try:
            redis.setex(key, CACHE_TTL, json.dumps(output))
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
    """Search DPLA for Freedmen's Bureau records."""
    results = search_dpla(first, last, birth_year, subject="freedmen's bureau", page_size=6)
    for r in results:
        r['source'] = 'freedmens_bureau'
        r['record_type'] = 'freedmens_bureau'
    # Also search LOC directly
    try:
        params = {
            'q': f'"{first} {last}" freedmen',
            'fo': 'json',
            'at': 'results',
        }
        resp = req_lib.get('https://www.loc.gov/search/', params=params, timeout=5)
        if resp.ok:
            for item in resp.json().get('results', [])[:3]:
                results.append({
                    'source': 'freedmens_bureau',
                    'record_type': 'freedmens_bureau',
                    'title': item.get('title', ''),
                    'url': f"https://www.loc.gov{item.get('id', '')}",
                })
    except Exception:
        pass
    return results


def search_slave_schedules(last: str, birth_year: int = None) -> list:
    """Search DPLA for slave schedule records (by slaveholder surname)."""
    results = search_dpla('', last, birth_year, subject='slave schedules', page_size=5)
    for r in results:
        r['source'] = 'slave_schedule'
        r['record_type'] = 'slave_schedule'
        r['note'] = ('Slave schedules list age/sex only, not names. '
                     'This record is for the slaveholder with this surname.')
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
    gaps = classify_gaps(person_snapshot, results)
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
