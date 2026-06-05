import json
import hashlib
import os
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
import requests as req_lib
from .gap_classifier import classify_gaps, confidence_score
from .db import get_redis

logger = logging.getLogger(__name__)

CACHE_TTL  = 86400 * 2   # 48 hours
SEARCH_TIMEOUT = 28      # hard cap — show whatever came back in 28 seconds

DPLA_KEY = os.environ.get('DPLA_API_KEY', '')
DPLA_URL = 'https://api.dp.la/v2/items'


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------

def _cache_key(first, last, birth_year, birth_place) -> str:
    raw = f'{first}|{last}|{birth_year}|{birth_place}'.lower()
    return f'search:{hashlib.md5(raw.encode()).hexdigest()}'


# ---------------------------------------------------------------------------
# DPLA helpers
# ---------------------------------------------------------------------------

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


def _dpla_search(q: str, record_type: str, page_size: int = 6) -> list:
    if not DPLA_KEY:
        return []
    try:
        params = {'q': q, 'api_key': DPLA_KEY, 'page_size': page_size}
        resp = req_lib.get(DPLA_URL, params=params, timeout=8)
        resp.raise_for_status()
        docs = resp.json().get('docs', [])
        results = []
        for doc in docs:
            sr  = doc.get('sourceResource', {})
            dp  = doc.get('dataProvider', '')
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


def search_dpla(first: str, last: str, birth_year: int = None, page_size: int = 5) -> list:
    q = f'{first} {last}'.strip() if first else last
    return _dpla_search(q, 'general', page_size)


def search_dpla_census(first: str, last: str, birth_year: int = None,
                       birth_place: str = '') -> list:
    name = f'{first} {last}'.strip() if first else last
    q = f'{name} born {birth_place}' if birth_place else f'{name} born'
    return _dpla_search(q, 'census', page_size=6)


def search_dpla_military(first: str, last: str, birth_year: int = None) -> list:
    name = f'{first} {last}'.strip() if first else last
    return _dpla_search(f'{name} military service soldier', 'military', page_size=5)


# ---------------------------------------------------------------------------
# WikiTree
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Chronicling America
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# NARA
# ---------------------------------------------------------------------------

NARA_URL = 'https://catalog.archives.gov/proxy/records/search'


def search_nara(first: str, last: str, birth_year: int = None,
                birth_place: str = '') -> list:
    try:
        parts = [first, last]
        if birth_year:
            parts.append(str(birth_year))
        if birth_place:
            parts.append(birth_place)
        q = ' '.join(p for p in parts if p)
        resp = req_lib.get(NARA_URL, params={'q': q, 'rows': 6}, timeout=8)
        resp.raise_for_status()
        hits = resp.json().get('body', {}).get('hits', {}).get('hits', [])
        results = []
        last_lower = last.lower()
        for h in hits:
            rec = h['_source'].get('record', {})
            title = rec.get('title', '')
            if last_lower not in title.lower():
                continue
            resources = rec.get('onlineResources', [])
            url = (resources[0].get('url', '') if resources
                   else f'https://catalog.archives.gov/id/{h["_id"]}')
            results.append({
                'source': 'nara',
                'record_type': 'federal_record',
                'title': title,
                'date': '',
                'url': url,
            })
        return results[:4]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Cross-reference scoring
# ---------------------------------------------------------------------------

def _name_tokens(s: str) -> set:
    return set(s.lower().split()) if s else set()


def _year_close(a, b, window: int = 10) -> bool:
    try:
        return abs(int(a) - int(b)) <= window
    except (TypeError, ValueError):
        return False


def cross_reference(results: list, first: str, last: str,
                    birth_year: int = None, birth_place: str = '') -> list:
    target_tokens = _name_tokens(f'{first} {last}')
    state_token   = birth_place.lower() if birth_place else ''

    def _relevance(r: dict) -> dict:
        title = (r.get('title') or r.get('name') or '').lower()
        title_tokens = set(title.split())
        name_match   = len(target_tokens & title_tokens) / max(len(target_tokens), 1)
        year_match   = _year_close(r.get('birth_year'), birth_year) if birth_year else False
        place_match  = state_token and state_token in title
        return {'name_match': name_match, 'year_match': year_match,
                'place_match': place_match, 'relevant': name_match >= 0.5}

    scored = []
    for i, r in enumerate(results):
        rel = _relevance(r)
        corroborated_by = [
            other['source'] for j, other in enumerate(results)
            if i != j and other.get('source') != r.get('source')
            and _relevance(other)['relevant'] and rel['relevant']
        ]
        r = dict(r)
        r['corroborated_by']      = list(set(corroborated_by))
        r['corroboration_score']  = len(r['corroborated_by'])
        r['name_match_score']     = round(rel['name_match'], 2)
        r['place_match']          = rel['place_match']
        scored.append(r)

    scored.sort(key=lambda x: (x['corroboration_score'], x['name_match_score']), reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Person snapshot helper
# ---------------------------------------------------------------------------

def _build_person_snapshot(first, last, birth_year, birth_place, results) -> dict:
    snap = {
        'first_name': first, 'last_name': last,
        'birth_year': birth_year, 'birth_state': birth_place,
        'death_year': None, 'death_place': None,
        'parent_ids': [], 'spouse_ids': [],
    }
    for r in results:
        if r.get('birth_year') and not snap['birth_year']:
            snap['birth_year'] = r['birth_year']
        if r.get('death_year') and not snap['death_year']:
            snap['death_year'] = r['death_year']
        # VA gravesite returns death_date as "MM/DD/YYYY" — extract year from it
        if r.get('death_date') and not snap['death_year']:
            try:
                snap['death_year'] = int(r['death_date'].split('/')[-1])
            except (ValueError, IndexError):
                pass
        if r.get('death_place') and not snap['death_place']:
            snap['death_place'] = r['death_place']
        if r.get('cemetery') and not snap['death_place']:
            snap['death_place'] = r['cemetery']
    return snap


# ---------------------------------------------------------------------------
# Task builder — every source as an independent callable
# ---------------------------------------------------------------------------

def _playwright_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa
        return True
    except ImportError:
        return False


def _build_tasks(first: str, last: str, birth_year: int,
                 birth_place: str, country_hint: str,
                 community_results: list = None) -> dict:
    """Return {label: callable} for every applicable source."""
    tasks = {
        'wikitree':      lambda: search_wikitree(first, last, birth_year),
        'chronicling':   lambda: search_chronicling(first, last, birth_year),
        'dpla_census':   lambda: search_dpla_census(first, last, birth_year, birth_place),
        'dpla_military': lambda: search_dpla_military(first, last, birth_year),
        'dpla_general':  lambda: search_dpla(first, last, birth_year),
        'dpla_obituary': lambda: _dpla_search(
            f'{first} {last} obituary death'.strip(), 'dpla_obituary', 3),
        'dpla_marriage': lambda: _dpla_search(
            f'{first} {last} marriage'.strip(), 'dpla_marriage', 3),
        'nara':          lambda: search_nara(first, last, birth_year, birth_place),
    }

    if _playwright_available():
        from .playwright_scrapers import (
            search_findagrave, search_obituaries, search_va_gravesite,
            search_freebmd, search_irish_genealogy, search_antenati,
            search_geneteka, search_digitalarkivet, search_archion,
            search_matricula, _guess_origins,
        )
        # Always-on US sources
        _first_only = first.split()[0] if first else first  # strip middle initial for name-exact sources
        tasks['findagrave']   = lambda: search_findagrave(first, last, birth_year)
        tasks['obituaries']   = lambda: search_obituaries(first, last, birth_year, birth_place)
        tasks['va_gravesite'] = lambda: search_va_gravesite(_first_only, last, birth_year)

        # Origin-specific European sources — skip entirely if birth place is clearly US
        _us_states = {
            'alabama','alaska','arizona','arkansas','california','colorado','connecticut',
            'delaware','florida','georgia','hawaii','idaho','illinois','indiana','iowa',
            'kansas','kentucky','louisiana','maine','maryland','massachusetts','michigan',
            'minnesota','mississippi','missouri','montana','nebraska','nevada',
            'new hampshire','new jersey','new mexico','new york','north carolina',
            'north dakota','ohio','oklahoma','oregon','pennsylvania','rhode island',
            'south carolina','south dakota','tennessee','texas','utah','vermont',
            'virginia','washington','west virginia','wisconsin','wyoming',
            'usa','united states','u.s.','u.s.a.',
        }
        _bp_lower = birth_place.lower()
        _is_us = any(s in _bp_lower for s in _us_states)
        origins = [] if _is_us else ([country_hint.lower()] if country_hint else _guess_origins(last))

        if 'ireland' in origins or 'irish' in origins:
            tasks['irish_birth']  = lambda: search_irish_genealogy(first, last, birth_year, 'B')
            tasks['irish_death']  = lambda: search_irish_genealogy(first, last, birth_year, 'D')
            tasks['freebmd']      = lambda: search_freebmd(first, last, birth_year, 'All')
        if 'italy' in origins or 'italian' in origins:
            tasks['antenati']     = lambda: search_antenati(first, last, birth_year)
        if 'poland' in origins or 'polish' in origins:
            tasks['geneteka']     = lambda: search_geneteka(first, last, birth_year)
        if 'norway' in origins or 'norwegian' in origins:
            tasks['digitalarkivet'] = lambda: search_digitalarkivet(first, last, birth_year)
        if 'germany' in origins or 'german' in origins:
            tasks['archion']      = lambda: search_archion(first, last, birth_year)
            tasks['matricula']    = lambda: search_matricula(first, last, birth_year, 'DE')
        if 'uk' in origins or 'british' in origins:
            tasks['freebmd']      = lambda: search_freebmd(first, last, birth_year, 'All')

    if community_results:
        _cr = list(community_results)
        tasks['rootbridge_community'] = lambda: _cr

    return tasks


# ---------------------------------------------------------------------------
# Parallel runner (non-streaming)
# ---------------------------------------------------------------------------

def _run_parallel(tasks: dict) -> list:
    all_results = []
    with ThreadPoolExecutor(max_workers=min(len(tasks), 16)) as executor:
        futures = {executor.submit(fn): name for name, fn in tasks.items()}
        try:
            for future in as_completed(futures, timeout=SEARCH_TIMEOUT):
                try:
                    all_results.extend(future.result(timeout=2) or [])
                except Exception as e:
                    logger.debug('Source %s failed: %s', futures[future], e)
        except FuturesTimeout:
            logger.info('Search timeout after %ds — returning partial results', SEARCH_TIMEOUT)
    return all_results


# ---------------------------------------------------------------------------
# Streaming generator (SSE)
# ---------------------------------------------------------------------------

def run_us_cascade_stream(first: str = '', last: str = '', birth_year: int = None,
                          birth_place: str = '', country_hint: str = '',
                          community_results: list = None):
    """
    Generator yielding SSE-formatted strings.
    Each source sends an event as it completes.
    Final event has done=True with full scored results + AI summary.
    """
    from .ai_synthesis import synthesize_gaps

    all_results = []
    tasks = _build_tasks(first, last, birth_year or 0, birth_place, country_hint,
                         community_results=community_results)

    with ThreadPoolExecutor(max_workers=min(len(tasks), 16)) as executor:
        futures = {executor.submit(fn): name for name, fn in tasks.items()}
        try:
            for future in as_completed(futures, timeout=SEARCH_TIMEOUT):
                name = futures[future]
                try:
                    results = future.result(timeout=2) or []
                    if results:
                        all_results.extend(results)
                        yield 'data: ' + json.dumps({
                            'source': name,
                            'results': results,
                            'count': len(results),
                            'total': len(all_results),
                        }) + '\n\n'
                except Exception as e:
                    logger.debug('Source %s failed: %s', name, e)
        except FuturesTimeout:
            pass

    # Final: score, gap analysis, AI synthesis
    scored   = cross_reference(all_results, first, last, birth_year, birth_place)
    snapshot = _build_person_snapshot(first, last, birth_year, birth_place, scored)
    gaps     = classify_gaps(snapshot, scored)
    score    = confidence_score(snapshot)
    corr     = sum(1 for r in scored if r.get('corroboration_score', 0) > 0)
    score    = min(score + (15 if corr >= 2 else 7 if corr == 1 else 0), 95)

    synthesis = synthesize_gaps(
        person={'first_name': first, 'last_name': last,
                'birth_year': birth_year, 'birth_state': birth_place},
        results=scored, gaps=gaps,
    )

    yield 'data: ' + json.dumps({
        'done': True,
        'results': scored,
        'gaps': gaps,
        'confidence': score,
        'summary': synthesis['summary'],
        'corroborated_count': corr,
    }) + '\n\n'


# ---------------------------------------------------------------------------
# Non-streaming entry point (kept for guest search + cache fallback)
# ---------------------------------------------------------------------------

def run_us_cascade(first: str = '', last: str = '', birth_year: int = None,
                   birth_place: str = '', country_hint: str = '',
                   community_results: list = None) -> dict:
    key = _cache_key(first, last, birth_year, birth_place)
    try:
        r = get_redis()
        cached = r.get(key)
        if cached:
            return json.loads(cached)
    except Exception:
        r = None

    tasks       = _build_tasks(first, last, birth_year or 0, birth_place, country_hint,
                               community_results=community_results)
    raw_results = _run_parallel(tasks)
    results     = cross_reference(raw_results, first, last, birth_year, birth_place)
    snapshot    = _build_person_snapshot(first, last, birth_year, birth_place, results)
    gaps        = classify_gaps(snapshot, results)
    score       = confidence_score(snapshot)
    corr        = sum(1 for r in results if r.get('corroboration_score', 0) > 0)
    score       = min(score + (15 if corr >= 2 else 7 if corr == 1 else 0), 95)

    output = {'results': results, 'gaps': gaps,
              'confidence': score, 'corroborated_count': corr}

    if r is not None:
        try:
            r.setex(key, CACHE_TTL, json.dumps(output))
        except Exception:
            pass
    return output
