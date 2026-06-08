import json
import hashlib
import os
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
import requests as req_lib
from .gap_classifier import classify_gaps, confidence_score
from .db import get_redis
from .research_context import ResearchContext

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
        params = {'action': 'searchPerson', 'FirstName': first,
                  'LastName': last, 'format': 'json'}
        resp = req_lib.get('https://api.wikitree.com/api.php',
                           params=params, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        matches = []
        if isinstance(data, list) and data:
            matches = data[0].get('matches', [])
        elif isinstance(data, dict):
            matches = data.get('matches', [])
        results = []
        for item in matches[:10]:
            # BirthDate is "YYYY-MM-DD" — extract year
            birth_date_str = item.get('BirthDate', '') or ''
            item_birth = None
            if birth_date_str and len(birth_date_str) >= 4:
                try:
                    item_birth = int(birth_date_str[:4])
                    if item_birth < 1000:
                        item_birth = None
                except (ValueError, TypeError):
                    pass

            if birth_year and item_birth and abs(item_birth - birth_year) > 20:
                continue
            fn = item.get('FirstName') or ''
            ln = item.get('LastNameAtBirth') or ''
            # Skip if first name requested but missing (low-quality record)
            if first and not fn:
                continue
            name = f"{fn} {ln}".strip()
            if not name:
                continue
            results.append({
                'name': name,
                'title': name,
                'birth_year': item_birth,
                'birth_place': item.get('BirthLocation', ''),
                'death_year': int(item.get('DeathDate', '')[:4]) if (item.get('DeathDate', '') or '')[:4].isdigit() else None,
                'death_place': item.get('DeathLocation', ''),
                'gender': item.get('Gender', ''),
                'date': birth_date_str[:4] if birth_date_str else '',
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

_LOC_HEADERS = {'User-Agent': 'RootBridge/1.0 (genealogy research; contact@rootbridge.app)'}
_LOC_BASE    = 'https://www.loc.gov/collections/chronicling-america/'

def search_chronicling(first: str, last: str, birth_year: int = None) -> list:
    """
    Search Chronicling America via the LOC collections API (1770-1963 newspapers).
    Runs targeted queries for obituaries and vital notices scoped to the person's lifespan.
    """
    results = []
    name = f'{first} {last}'.strip() if first else last

    def _query(q, count=6):
        try:
            params = {'fo': 'json', 'q': q, 'c': count}
            r = req_lib.get(_LOC_BASE, params=params, headers=_LOC_HEADERS, timeout=10)
            r.raise_for_status()
            return r.json().get('results', [])
        except Exception:
            return []

    def _item_year(item):
        d = item.get('date') or ''
        try:
            return int(str(d)[:4])
        except (ValueError, TypeError):
            return None

    def _fmt(item, record_type):
        url = (item.get('aka') or [''])[0]
        return {
            'source':      'chronicling_america',
            'record_type': record_type,
            'title':       (item.get('title') or '')[:100],
            'date':        item.get('date', ''),
            'url':         url,
        }

    # Obituary search — filter results to likely death window client-side
    obit_items = _query(f'{name} obituary death', count=8)
    for item in obit_items:
        yr = _item_year(item)
        if birth_year and yr:
            if not (birth_year + 40 <= yr <= min(birth_year + 105, 1963)):
                continue
        results.append(_fmt(item, 'obituary'))

    # Birth/marriage search — filter to birth era
    vital_items = _query(f'{name} born married marriage', count=6)
    for item in vital_items:
        yr = _item_year(item)
        if birth_year and yr:
            if not (birth_year - 10 <= yr <= birth_year + 35):
                continue
        results.append(_fmt(item, 'vital_notice'))

    return results[:8]


# ---------------------------------------------------------------------------
# Brave Search API — free tier 2,000/mo, real web results, no Playwright needed
# Set BRAVE_API_KEY env var to enable
# ---------------------------------------------------------------------------

_OBIT_DOMAINS = {'legacy.com', 'findagrave.com', 'dignitymemorial.com', 'tributearchive.com',
                 'tributes.com', 'obits.com', 'obituaries.com', 'funeralhome', 'funeral',
                 'nola.com', 'theadvocate.com', 'legacy', 'tribute', 'memorial'}
_OBIT_SIGNALS = {'obituary', 'obituaries', 'passed away', 'in loving memory', 'burial',
                 'interment', 'survived by', 'memorial', 'graveside', 'funeral home'}

def search_bing_lite_obits(first: str, last: str, birth_year: int = None,
                           search_place: str = '') -> list:
    """
    Search Bing Lite (lite.bing.com) for obituaries — free, no API key,
    returns clean HTML that parses without Playwright.
    """
    try:
        import urllib.parse, re as _re
        name_q = f'"{first} {last}"' if first else f'"{last}"'
        parts = [name_q, 'obituary']
        if search_place:
            parts.append(search_place)
        query = ' '.join(parts)

        resp = req_lib.get(
            'https://lite.bing.com/search',
            params={'q': query},
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html',
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return []

        html = resp.text
        # Bing Lite returns simple <li> result blocks with <a> and <p> tags
        blocks = _re.findall(r'<li[^>]*class="[^"]*b_algo[^"]*"[^>]*>(.*?)</li>', html, _re.DOTALL)
        if not blocks:
            # Fallback: grab all anchor tags with URLs
            blocks = _re.findall(r'<a[^>]+href="(https?://[^"]+)"[^>]*>([^<]+)</a>', html)

        results = []
        # Parse anchor + description pairs from each block
        for block in blocks[:10]:
            links = _re.findall(r'href="(https?://[^"]+)"', block)
            titles = _re.findall(r'<a[^>]*>([^<]+)</a>', block)
            descs = _re.findall(r'<p[^>]*>([^<]+)</p>', block)
            url = links[0] if links else ''
            title = titles[0].strip() if titles else ''
            desc = descs[0].strip() if descs else ''
            combined = (title + ' ' + desc + ' ' + url).lower()

            if not url or last.lower() not in combined:
                continue
            has_signal = any(s in combined for s in _OBIT_SIGNALS)
            has_domain = any(d in url.lower() for d in _OBIT_DOMAINS)
            if not (has_signal or has_domain):
                continue

            results.append({
                'source': 'obituary_web',
                'record_type': 'obituary',
                'title': title or f'{first} {last} obituary',
                'snippet': desc[:200],
                'url': url,
                'name': f'{first} {last}',
            })

        return results[:4]
    except Exception as e:
        logger.debug('Bing Lite obituary search failed: %s', e)
        return []


# ---------------------------------------------------------------------------
# Legacy.com obituary search — HTTP-based, no Playwright needed
# ---------------------------------------------------------------------------

_LEGACY_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*',
}

_US_STATE_ABBR = {
    'alabama':'al','alaska':'ak','arizona':'az','arkansas':'ar','california':'ca',
    'colorado':'co','connecticut':'ct','delaware':'de','florida':'fl','georgia':'ga',
    'hawaii':'hi','idaho':'id','illinois':'il','indiana':'in','iowa':'ia',
    'kansas':'ks','kentucky':'ky','louisiana':'la','maine':'me','maryland':'md',
    'massachusetts':'ma','michigan':'mi','minnesota':'mn','mississippi':'ms',
    'missouri':'mo','montana':'mt','nebraska':'ne','nevada':'nv',
    'new hampshire':'nh','new jersey':'nj','new mexico':'nm','new york':'ny',
    'north carolina':'nc','north dakota':'nd','ohio':'oh','oklahoma':'ok',
    'oregon':'or','pennsylvania':'pa','rhode island':'ri','south carolina':'sc',
    'south dakota':'sd','tennessee':'tn','texas':'tx','utah':'ut','vermont':'vt',
    'virginia':'va','washington':'wa','west virginia':'wv','wisconsin':'wi','wyoming':'wy',
}

def search_legacy_obits(first: str, last: str, birth_year: int = None,
                         birth_place: str = '') -> list:
    """
    Search Legacy.com obituaries via their JSON API.
    Effective for deaths from the 1990s onward.
    """
    try:
        import urllib.parse
        name_slug = urllib.parse.quote(f'{first}-{last}'.lower().replace(' ', '-'))
        url = f'https://www.legacy.com/obituaries/name/search?name={urllib.parse.quote(f"{first} {last}")}'

        # Extract state abbreviation from birth_place for filtering
        state_hint = ''
        bp_lower = birth_place.lower()
        for state_name, abbr in _US_STATE_ABBR.items():
            if state_name in bp_lower or abbr == bp_lower.strip():
                state_hint = abbr
                break

        # Use Legacy.com's public search JSON endpoint
        search_url = 'https://www.legacy.com/obituaries/search'
        params = {'keyword': f'{first} {last}', 'countryid': 1}
        if state_hint:
            # Legacy uses numeric state IDs; use city/keyword search instead
            params['keyword'] = f'{first} {last} {birth_place}'.strip()

        resp = req_lib.get(search_url, params=params, headers=_LEGACY_HEADERS, timeout=10)
        if resp.status_code != 200:
            return []

        # Parse HTML response for obituary cards
        html = resp.text
        results = []
        import re as _re
        # Extract names and dates from legacy.com result cards
        cards = _re.findall(
            r'href="([^"]+/obituaries/[^"]+)"[^>]*>.*?<h\d[^>]*>([^<]+)</h\d>',
            html, _re.DOTALL
        )
        for href, title in cards[:6]:
            title = title.strip()
            if last.lower() not in title.lower():
                continue
            full_url = href if href.startswith('http') else f'https://www.legacy.com{href}'
            results.append({
                'source': 'legacy_obituary',
                'record_type': 'obituary',
                'title': title,
                'url': full_url,
                'birth_place': birth_place,
            })

        return results[:4]
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
# Ship manifest search — colonial/immigrant era (pre-1850)
# ---------------------------------------------------------------------------

# Philadelphia arrival records 1727–1808 (Pennsylvania German immigrants)
# Internet Archive full-text search over digitized ship lists
_IA_SEARCH_URL = 'https://archive.org/advancedsearch.php'


def search_ship_manifests(first: str, last: str, birth_year: int = None,
                           country_hint: str = '') -> list:
    """
    Search colonial-era ship arrival records for immigrant ancestors.
    Covers: Pennsylvania German arrivals 1727-1808, Philadelphia port lists,
    Hamburg emigrant lists 1850-1934 (for later immigrants).
    Best for ancestors born 1700-1820 in Germany, UK, Ireland, Netherlands.
    """
    results = []

    # Estimate arrival year: if birth_year known, parents arrived ~25yr before
    # birth, so arrival ~birth_year-25 to birth_year+5
    arrive_min = (birth_year - 30) if birth_year else 1700
    arrive_max = (birth_year + 10) if birth_year else 1820
    arrive_min = max(arrive_min, 1700)
    arrive_max = min(arrive_max, 1900)

    # Internet Archive: Pennsylvania German ship lists (Rupp/Strassburg lists)
    try:
        q = f'"{last}" ship manifest passenger'
        if country_hint:
            q += f' {country_hint}'
        params = {
            'q': f'({q}) AND mediatype:texts AND subject:(genealogy OR "ship manifest" OR "passenger list" OR "emigrant")',
            'fl[]': ['identifier', 'title', 'date', 'subject', 'description'],
            'rows': 8,
            'page': 1,
            'output': 'json',
        }
        resp = req_lib.get(_IA_SEARCH_URL, params=params, timeout=8)
        resp.raise_for_status()
        docs = resp.json().get('response', {}).get('docs', [])
        last_lower = last.lower()
        first_lower = first.lower() if first else ''
        for doc in docs:
            title = doc.get('title', '')
            desc  = doc.get('description', '')
            combined = (title + ' ' + (desc if isinstance(desc, str) else ' '.join(desc or []))).lower()
            if last_lower not in combined and first_lower not in combined:
                continue
            ident = doc.get('identifier', '')
            results.append({
                'source': 'ship_manifest',
                'record_type': 'immigration',
                'title': title,
                'date': str(doc.get('date', '')),
                'url': f'https://archive.org/details/{ident}' if ident else '',
                'birth_place': country_hint or '',
            })
    except Exception:
        pass

    # NARA immigration/naturalization catalog (specific to ship records)
    try:
        q_parts = [last]
        if first:
            q_parts.append(first)
        if country_hint:
            q_parts.append(country_hint)
        q_parts.append('passenger')
        nara_q = ' '.join(q_parts)
        resp = req_lib.get(
            NARA_URL,
            params={'q': nara_q, 'rows': 4, 'typeOfMaterials': 'Microfilm+Publications'},
            timeout=8,
        )
        resp.raise_for_status()
        hits = resp.json().get('body', {}).get('hits', {}).get('hits', [])
        last_lower = last.lower()
        for h in hits:
            rec = h['_source'].get('record', {})
            title = rec.get('title', '')
            if last_lower not in title.lower() and 'passenger' not in title.lower() and 'arrival' not in title.lower():
                continue
            resources = rec.get('onlineResources', [])
            url = (resources[0].get('url', '') if resources
                   else f'https://catalog.archives.gov/id/{h["_id"]}')
            results.append({
                'source': 'ship_manifest',
                'record_type': 'immigration',
                'title': title,
                'date': '',
                'url': url,
            })
    except Exception:
        pass

    return results[:6]


# ---------------------------------------------------------------------------
# Ship arrivals — local DB search (fast, no network)
# ---------------------------------------------------------------------------

def _search_ship_arrivals_db_safe(last: str, first: str = '',
                                   birth_year: int = None) -> list:
    """
    Query the local ship_arrivals table built by ship_manifest_scraper.py.
    Returns results immediately with no network I/O. Returns [] if table
    doesn't exist yet (scraper hasn't run).
    """
    try:
        import sqlite3 as _sqlite3, os as _os
        _db = _os.path.join(_os.path.dirname(__file__), '..', 'data', 'ged_vault_index.db')
        _db = _os.path.normpath(_db)
        if not _os.path.exists(_db):
            return []
        conn = _sqlite3.connect(_db)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        if 'ship_arrivals' not in tables:
            conn.close()
            return []

        last_clean = last.upper().strip()
        prefix4    = last_clean[:4]
        results, seen = [], set()

        for pat in [last_clean, f'{prefix4}%']:
            args = [pat]
            sql  = "SELECT * FROM ship_arrivals WHERE last_name LIKE ?"
            if first:
                sql += " AND (first_name LIKE ? OR first_name LIKE ?)"
                args += [f'{first[:3]}%', first.strip().title()]
            if birth_year:
                # Parents arrived 20-35 years before child's birth
                sql += " AND arrival_year BETWEEN ? AND ?"
                args += [birth_year - 40, birth_year + 15]
            sql += " LIMIT 15"

            for row in conn.execute(sql, args).fetchall():
                key = (row[1], row[2], row[5], row[4])  # last, first, year, ship
                if key in seen:
                    continue
                seen.add(key)
                name = f"{row[2] or ''} {row[1] or ''}".strip()
                ship = row[4] or ''
                yr   = row[5] or ''
                ident = row[12] or ''
                url  = f'https://archive.org/details/{ident}' if ident else ''
                score = 88 if pat == last_clean else 55
                results.append({
                    'source':      'ship_manifest_db',
                    'record_type': 'immigration',
                    'title':       f'Ship arrival record: {name} — {ship} ({yr})',
                    'name':        name,
                    'date':        str(yr),
                    'birth_place': row[9] or '',
                    'url':         url,
                    'score':       score,
                })
        conn.close()
        return results
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Revolutionary War pension files (1775-1840, NARA via archive.org)
# ---------------------------------------------------------------------------

def search_revwar_pensions(first: str, last: str, birth_year: int = None,
                            birth_place: str = '') -> list:
    """
    Search NARA Revolutionary War pension files (~80K files) on archive.org.
    These contain sworn family testimony with names, birth dates, places,
    children, and service details — highest-value records for pre-1800 US lines.

    Pension series:
      S = survivors (veteran applied while living)
      W = widow (widow applied after veteran died)
      R = rejected (application denied, but file still has data)
    """
    results = []
    last_lower  = last.lower()
    first_lower = first.lower() if first else ''

    # 1. Internet Archive: NARA revolutionary war pension file collection
    try:
        q = f'"{last}"'
        if first:
            q += f' "{first}"'
        params = {
            'q': (f'({q}) AND (subject:("revolutionary war pension") OR '
                  f'subject:("pension file") OR subject:("revolutionary war"))'
                  f' AND mediatype:texts'),
            'fl[]': ['identifier', 'title', 'description', 'subject', 'date'],
            'rows': 8,
            'output': 'json',
        }
        resp = req_lib.get(_IA_SEARCH_URL, params=params, timeout=10)
        resp.raise_for_status()
        docs = resp.json().get('response', {}).get('docs', [])
        for doc in docs:
            title = doc.get('title', '')
            desc  = doc.get('description', '')
            subj  = doc.get('subject', [])
            if isinstance(subj, list):
                subj = ' '.join(subj)
            combined = (title + ' ' + (desc if isinstance(desc, str) else '') + ' ' + subj).lower()
            if last_lower not in combined and (not first_lower or first_lower not in combined):
                continue
            ident = doc.get('identifier', '')
            results.append({
                'source':      'revwar_pension',
                'record_type': 'military',
                'title':       title or f'Rev War pension file: {first} {last}',
                'date':        str(doc.get('date', '')),
                'url':         f'https://archive.org/details/{ident}' if ident else '',
                'birth_place': birth_place,
                'score':       75,
            })
    except Exception:
        pass

    # 2. NARA catalog: series M804 (Rev War pension applications, 2,670 microfilm reels)
    try:
        nara_q = f'{last} {first} "revolutionary war" pension'.strip()
        resp   = req_lib.get(
            NARA_URL,
            params={'q': nara_q, 'rows': 4,
                    'description': 'pension',
                    'typeOfMaterials': 'Microfilm+Publications'},
            timeout=10,
        )
        resp.raise_for_status()
        hits = resp.json().get('body', {}).get('hits', {}).get('hits', [])
        for h in hits:
            rec   = h['_source'].get('record', {})
            title = rec.get('title', '')
            if last_lower not in title.lower():
                continue
            resources = rec.get('onlineResources', [])
            url = (resources[0].get('url', '') if resources
                   else f'https://catalog.archives.gov/id/{h["_id"]}')
            results.append({
                'source':      'revwar_pension',
                'record_type': 'military',
                'title':       title,
                'date':        '',
                'url':         url,
                'birth_place': birth_place,
                'score':       70,
            })
    except Exception:
        pass

    return results[:6]


# ---------------------------------------------------------------------------
# Multi-port immigration search (Castle Garden, Ellis Island, Hamburg, Baltimore)
# ---------------------------------------------------------------------------
#
# US immigrant port eras (for routing logic):
#   Philadelphia  1682–1820  German/Swiss/Dutch colonial arrivals
#   Baltimore     1820–1910  German/Irish direct route (Hamburg → Baltimore)
#   New Orleans   1820–1870  Irish/German route → Mississippi → Midwest
#   Castle Garden 1820–1892  New York; 11M records
#   Galveston     1845–1945  German → Texas (Adelsverein settlers)
#   Ellis Island  1892–1957  Southern/Eastern Europe; 12M records
#   Angel Island  1910–1940  Pacific arrivals (Chinese, Japanese, European)
#
# These are live per-query searches. For bulk-indexed local data, use
# _search_ship_arrivals_db_safe() which covers Philadelphia 1727-1808.


def search_castle_garden(first: str, last: str, birth_year: int = None) -> list:
    """
    Search Castle Garden (New York arrivals 1820-1892), 11 million records.
    Volunteer-transcribed; free; no API key needed.
    """
    results = []
    try:
        params = {
            'given': first or '',
            'surname': last,
            'year': birth_year or '',  # arrival year field — approximate via birth
            'country': '',
            'ship': '',
        }
        resp = req_lib.get(
            'https://www.castlegarden.org/search.php',
            params=params,
            headers={'User-Agent': 'Mozilla/5.0 (compatible; genealogy research)'},
            timeout=10,
        )
        resp.raise_for_status()
        # Parse results table
        import re as _re
        rows = _re.findall(r'<tr[^>]*class=["\'](?:odd|even)["\'][^>]*>(.*?)</tr>',
                           resp.text, _re.DOTALL | _re.IGNORECASE)
        last_lower = last.lower()
        for row in rows[:10]:
            cells = _re.findall(r'<td[^>]*>(.*?)</td>', row, _re.DOTALL)
            cells = [_re.sub(r'<[^>]+>', '', c).strip() for c in cells]
            if not cells or last_lower not in ' '.join(cells).lower():
                continue
            # Typical columns: Name, Age, Sex, Country, Arrival Date, Ship
            name_cell = cells[0] if cells else ''
            date_cell = cells[4] if len(cells) > 4 else ''
            ship_cell = cells[5] if len(cells) > 5 else ''
            results.append({
                'source':      'castle_garden',
                'record_type': 'immigration',
                'title':       f'Castle Garden arrival: {name_cell} — {ship_cell} ({date_cell})',
                'date':        date_cell,
                'url':         'https://www.castlegarden.org/',
                'birth_place': cells[3] if len(cells) > 3 else '',
                'score':       80,
            })
    except Exception:
        pass
    return results[:6]


def search_ellis_island(first: str, last: str, birth_year: int = None) -> list:
    """
    Search Ellis Island Foundation (New York arrivals 1892-1957), 65 million records.
    Live query against heritage.statueofliberty.org.
    """
    results = []
    try:
        # The Foundation's search page — we POST a search and parse the results table
        params = {
            'last_name': last,
            'first_name': first or '',
            'year_of_arrival': '',
            'ship_name': '',
            'page': 1,
        }
        resp = req_lib.get(
            'https://heritage.statueofliberty.org/passenger-results',
            params=params,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                'Accept': 'text/html',
            },
            timeout=12,
        )
        resp.raise_for_status()
        import re as _re
        # Parse results — look for passenger name rows
        rows = _re.findall(
            r'<tr[^>]*class=["\']passenger["\'][^>]*>(.*?)</tr>',
            resp.text, _re.DOTALL | _re.IGNORECASE
        )
        if not rows:
            # Alternative: find any table rows with the surname
            rows = _re.findall(r'<tr[^>]*>(.*?)</tr>', resp.text, _re.DOTALL)

        last_lower = last.lower()
        for row in rows[:15]:
            cells = _re.findall(r'<td[^>]*>(.*?)</td>', row, _re.DOTALL)
            cells = [_re.sub(r'<[^>]+>', '', c).strip() for c in cells]
            text = ' '.join(cells)
            if last_lower not in text.lower():
                continue
            if len(cells) >= 3:
                results.append({
                    'source':      'ellis_island',
                    'record_type': 'immigration',
                    'title':       f'Ellis Island: {text[:80]}',
                    'date':        cells[2] if len(cells) > 2 else '',
                    'url':         'https://heritage.statueofliberty.org/passenger-details',
                    'birth_place': cells[3] if len(cells) > 3 else '',
                    'score':       80,
                })
    except Exception:
        pass
    return results[:6]


def search_hamburg_emigrant(first: str, last: str, birth_year: int = None,
                             origin_place: str = '') -> list:
    """
    Search the German Emigration Database (Deutsche Auswanderer-Datenbank).
    Covers Hamburg + Bremen departures 1820-1934 — the DEPARTURE side of German emigration.
    All ships leaving Germany are listed here even if they arrived at NY/Baltimore/New Orleans.
    """
    results = []
    try:
        # The DAD site search via NARA catalog alternative
        # Also search archive.org for Hamburg emigrant lists
        q = f'"{last}"'
        if first:
            q += f' "{first}"'
        params = {
            'q': f'({q}) AND (subject:(Hamburg) OR subject:(emigrant) OR title:(emigrants)) AND mediatype:texts',
            'fl[]': ['identifier', 'title', 'description', 'date'],
            'rows': 5,
            'output': 'json',
        }
        resp = req_lib.get(_IA_SEARCH_URL, params=params, timeout=10)
        resp.raise_for_status()
        docs = resp.json().get('response', {}).get('docs', [])
        last_lower = last.lower()
        for doc in docs:
            title = doc.get('title', '')
            desc  = doc.get('description', '')
            combined = (title + ' ' + (desc if isinstance(desc, str) else '')).lower()
            if last_lower not in combined:
                continue
            ident = doc.get('identifier', '')
            results.append({
                'source':      'hamburg_emigrant',
                'record_type': 'immigration',
                'title':       f'Hamburg emigrant record: {title}',
                'date':        str(doc.get('date', '')),
                'url':         f'https://archive.org/details/{ident}' if ident else
                               'https://www.deutsche-auswanderer-datenbank.de',
                'birth_place': origin_place or 'Germany',
                'score':       72,
            })
    except Exception:
        pass

    # Also point Alfred to the German Emigration Database for manual lookup
    if not results:
        results.append({
            'source':      'hamburg_emigrant',
            'record_type': 'immigration',
            'title':       f'German Emigration Database — search for {first} {last}'.strip(),
            'date':        '',
            'url':         f'https://www.deutsche-auswanderer-datenbank.de/index.php?directory=passengers',
            'birth_place': origin_place or 'Germany',
            'score':       55,
            'note':        'Manual search required — JavaScript-based database',
        })
    return results[:4]


# ---------------------------------------------------------------------------
# Free census / genealogy sources
# ---------------------------------------------------------------------------

_STATE_ABBR = {
    'alabama':'al','alaska':'ak','arizona':'az','arkansas':'ar','california':'ca',
    'colorado':'co','connecticut':'ct','delaware':'de','florida':'fl','georgia':'ga',
    'hawaii':'hi','idaho':'id','illinois':'il','indiana':'in','iowa':'ia',
    'kansas':'ks','kentucky':'ky','louisiana':'la','maine':'me','maryland':'md',
    'massachusetts':'ma','michigan':'mi','minnesota':'mn','mississippi':'ms',
    'missouri':'mo','montana':'mt','nebraska':'ne','nevada':'nv','new hampshire':'nh',
    'new jersey':'nj','new mexico':'nm','new york':'ny','north carolina':'nc',
    'north dakota':'nd','ohio':'oh','oklahoma':'ok','oregon':'or','pennsylvania':'pa',
    'rhode island':'ri','south carolina':'sc','south dakota':'sd','tennessee':'tn',
    'texas':'tx','utah':'ut','vermont':'vt','virginia':'va','washington':'wa',
    'west virginia':'wv','wisconsin':'wi','wyoming':'wy',
}


def _state_code(birth_place: str) -> str:
    """Return 2-letter state code from a place string, or empty string."""
    bp = birth_place.lower()
    for name, code in _STATE_ABBR.items():
        if name in bp or bp.strip() == code:
            return code
    return ''


def search_usgenweb(first: str, last: str, birth_year: int = None,
                    birth_place: str = '') -> list:
    """
    Search USGenWeb — volunteer-transcribed county census, vital, and land records.
    Uses the Internet Archive's full-text search over the usgenweb.org collection,
    which covers all years including 1930 Census transcriptions.
    """
    try:
        state_code = _state_code(birth_place)
        # IA full-text search over USGenWeb collection
        q_parts = [f'"{last}"']
        if first:
            q_parts.append(f'"{first}"')
        if birth_year:
            q_parts.append(f'({birth_year} OR {birth_year - 1} OR {birth_year + 1})')
        q = ' AND '.join(q_parts)

        params = {
            'q': q,
            'fl[]': 'identifier,title,description,subject,date',
            'rows': 8,
            'output': 'json',
            'collection': 'usgenweb-archives',
        }
        resp = req_lib.get('https://archive.org/advancedsearch.php',
                           params=params, timeout=10)
        resp.raise_for_status()
        docs = resp.json().get('response', {}).get('docs', [])

        results = []
        last_lower = last.lower()
        for doc in docs:
            title = doc.get('title', '')
            desc  = doc.get('description', '') or ''
            if last_lower not in title.lower() and last_lower not in desc.lower():
                continue
            # State filter — skip if state code present and doesn't match
            if state_code and title:
                title_lower = title.lower()
                # Only filter if another state is explicitly named
                other_states = [c for c in _STATE_ABBR.values()
                                if c != state_code and f'/{c}/' in title_lower]
                if other_states:
                    continue
            results.append({
                'source': 'usgenweb',
                'record_type': 'census_transcription',
                'title': title,
                'date': str(doc.get('date', '')),
                'birth_place': birth_place,
                'url': f"https://archive.org/details/{doc.get('identifier','')}",
            })
        return results[:5]
    except Exception:
        return []


def search_rootsweb(first: str, last: str, birth_year: int = None,
                    birth_place: str = '') -> list:
    """
    Search RootsWeb WorldConnect via Internet Archive — huge collection of
    user-submitted family trees, many with pre-paywall census citations.
    Also searches the RootsWeb mailing list archives which contain transcribed
    census data posted by researchers.
    """
    try:
        q_parts = [f'"{last}"']
        if first:
            q_parts.append(f'"{first}"')
        if birth_place:
            state_code = _state_code(birth_place)
            if state_code:
                q_parts.append(state_code.upper())
        q = ' '.join(q_parts)

        params = {
            'q': q,
            'fl[]': 'identifier,title,description,date',
            'rows': 8,
            'output': 'json',
            'collection': 'rootsweb',
        }
        resp = req_lib.get('https://archive.org/advancedsearch.php',
                           params=params, timeout=10)
        resp.raise_for_status()
        docs = resp.json().get('response', {}).get('docs', [])

        results = []
        last_lower = last.lower()
        for doc in docs:
            title = doc.get('title', '') or ''
            desc  = doc.get('description', '') or ''
            if last_lower not in title.lower() and last_lower not in desc.lower():
                continue
            results.append({
                'source': 'rootsweb',
                'record_type': 'family_tree',
                'title': title,
                'date': str(doc.get('date', '')),
                'url': f"https://archive.org/details/{doc.get('identifier','')}",
            })
        return results[:5]
    except Exception:
        return []


def search_open_library_genealogy(first: str, last: str,
                                   birth_year: int = None,
                                   birth_place: str = '') -> list:
    """
    Search OpenLibrary for digitised genealogy books — county histories,
    family surname books, state genealogical society publications.
    These often contain census abstracts and vital records.
    """
    try:
        q_parts = [last]
        if first:
            q_parts.append(first)
        if birth_place:
            state_code = _state_code(birth_place)
            if state_code:
                q_parts.append(birth_place.split(',')[0].strip())
        q = ' '.join(q_parts) + ' genealogy'

        resp = req_lib.get(
            'https://openlibrary.org/search.json',
            params={'q': q, 'limit': 6, 'fields': 'title,author_name,first_publish_year,key'},
            timeout=8,
        )
        resp.raise_for_status()
        docs = resp.json().get('docs', [])

        results = []
        last_lower = last.lower()
        for doc in docs:
            title = doc.get('title', '')
            if last_lower not in title.lower():
                continue
            authors = doc.get('author_name', [])
            results.append({
                'source': 'open_library',
                'record_type': 'genealogy_book',
                'title': title,
                'date': str(doc.get('first_publish_year', '')),
                'url': f"https://openlibrary.org{doc.get('key','')}",
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


def cross_reference_with_context(results: list,
                                  ctx: ResearchContext = None) -> list:
    """
    Pre-filters results using ResearchContext constraints before scoring.
    Results that clearly violate known facts are dropped.
    """
    if not results:
        return results

    if ctx is None:
        return results

    results = ctx.filter_results(results, min_score=25)
    if not results:
        return results

    return cross_reference(results, ctx.first, ctx.last, ctx.birth_year, ctx.birth_place)


_GENERATION_LABELS = {
    1: 'target',
    2: 'parent',
    3: 'grandparent',
    4: 'great-grandparent',
    5: 'great-great-grandparent',
}


def build_parent_context(child_ctx,
                          parent_first: str = '',
                          parent_last: str = '',
                          parent_birth_year: int = None,
                          parent_birth_place: str = '') -> 'ResearchContext':
    gen = (child_ctx.generation or 1) + 1
    label = _GENERATION_LABELS.get(gen, f'generation-{gen}')
    return ResearchContext(
        first=parent_first,
        last=parent_last or child_ctx.last,
        birth_year=parent_birth_year,
        birth_place=parent_birth_place,
        generation=gen,
        generation_label=label,
    )


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
    # For name-exact database searches, use only the given name (first word).
    # Middle names appended to first cause zero matches in indexed archives.
    # Keep the full "first middle" form only for free-text/keyword searches.
    _first_only = first.split()[0] if first else first

    tasks = {
        'wikitree':    lambda: search_wikitree(_first_only, last, birth_year),
        'chronicling': lambda: search_chronicling(first, last, birth_year),
        'nara':        lambda: search_nara(_first_only, last, birth_year, birth_place),
        'usgenweb':    lambda: search_usgenweb(first, last, birth_year, birth_place),
        'rootsweb':    lambda: search_rootsweb(first, last, birth_year, birth_place),
        'open_library':lambda: search_open_library_genealogy(first, last, birth_year, birth_place),
    }

    # SSDI Oracle — our own 87M death records (instant, free, no rate limit)
    try:
        from .ssdi_oracle import search_ssdi, ssdi_available
        if ssdi_available():
            tasks['ssdi'] = lambda: search_ssdi(
                _first_only, last, birth_year, birth_place, limit=15
            )
    except Exception:
        pass

    # Geni.com world family tree (requires GENI_ACCESS_TOKEN in .env)
    try:
        from .geni_search import search_geni, geni_available
        if geni_available():
            tasks['geni'] = lambda: search_geni(
                _first_only, last, birth_year, birth_place, limit=10
            )
    except Exception:
        pass

    if _playwright_available():
        from .playwright_scrapers import (
            search_findagrave, search_obituaries, search_va_gravesite,
            search_freebmd, search_irish_genealogy, search_antenati,
            search_geneteka, search_digitalarkivet, search_archion,
            search_matricula, _guess_origins,
        )
        # Always-on US sources
        tasks['findagrave']   = lambda: search_findagrave(_first_only, last, birth_year)
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
            # two-letter abbreviations
            'al','ak','az','ar','ca','co','ct','de','fl','ga','hi','id','il','in',
            'ia','ks','ky','la','me','md','ma','mi','mn','ms','mo','mt','ne','nv',
            'nh','nj','nm','ny','nc','nd','oh','ok','or','pa','ri','sc','sd','tn',
            'tx','ut','vt','va','wa','wv','wi','wy',
        }
        import re as _re
        _bp_lower = birth_place.lower()
        _bp_tokens = set(_re.split(r'[\s,]+', _bp_lower))
        _is_us = bool(_bp_tokens & _us_states)

        # Deep ancestry mode: for persons born before 1830, even in a US state,
        # their parents were likely immigrants or colonial-era — fire European sources.
        # birth_year=None is treated as modern, so no override.
        _deep_ancestry = _is_us and birth_year is not None and birth_year < 1830

        if _deep_ancestry:
            # Local DB search first (instant, no network) then internet fallback
            tasks['ship_manifest_db'] = lambda: _search_ship_arrivals_db_safe(
                last, _first_only, birth_year
            )
            # Internet-based fallback for names not yet in local DB
            tasks['ship_manifest'] = lambda: search_ship_manifests(
                _first_only, last, birth_year, country_hint
            )
            # Rev War pension files (1775-1840 US veterans + family testimony)
            if birth_year is not None and 1730 <= birth_year <= 1800:
                tasks['revwar_pension'] = lambda: search_revwar_pensions(
                    _first_only, last, birth_year, birth_place
                )
            # Port-specific searches based on arrival era
            if birth_year is not None:
                # Castle Garden (New York 1820-1892): parents of persons born ~1820-1920
                if 1840 <= birth_year <= 1950:
                    tasks['castle_garden'] = lambda: search_castle_garden(
                        _first_only, last, birth_year
                    )
                # Ellis Island (New York 1892-1957): persons born ~1850-1940
                if 1870 <= birth_year <= 1970:
                    tasks['ellis_island'] = lambda: search_ellis_island(
                        _first_only, last, birth_year
                    )
                # Hamburg/German departure records — for any German-origin line
                _is_german = ('german' in (country_hint or '').lower() or
                              'germany' in (country_hint or '').lower() or
                              'german' in origins)
                if _is_german and 1800 <= birth_year <= 1950:
                    tasks['hamburg_emigrant'] = lambda: search_hamburg_emigrant(
                        _first_only, last, birth_year, country_hint
                    )

        origins = [] if (_is_us and not _deep_ancestry) else ([country_hint.lower()] if country_hint else _guess_origins(last))

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

def _run_phase(tasks: dict, all_results: list, timeout: float):
    """Run a set of tasks in parallel, extend all_results, yield SSE strings."""
    with ThreadPoolExecutor(max_workers=min(len(tasks), 12)) as executor:
        futures = {executor.submit(fn): name for name, fn in tasks.items()}
        try:
            for future in as_completed(futures, timeout=timeout):
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
                    else:
                        yield 'data: ' + json.dumps({'source': name, 'count': 0}) + '\n\n'
                except Exception as e:
                    logger.debug('Source %s failed: %s', name, e)
                    yield 'data: ' + json.dumps({'source': name, 'count': 0}) + '\n\n'
        except FuturesTimeout:
            pass


def _build_phase2_tasks(picks: list, first: str, last: str,
                        birth_year: int, birth_place: str) -> dict:
    """Turn AI-chosen source keys into callables."""
    _first_only = first.split()[0] if first else first
    _has_playwright = _playwright_available()
    tasks = {}

    mapping = {
        'chronicling': lambda: search_chronicling(first, last, birth_year),
        'nara':        lambda: search_nara(_first_only, last, birth_year, birth_place),
    }

    if _has_playwright:
        from .playwright_scrapers import (
            search_obituaries, search_va_gravesite, search_freebmd,
            search_irish_genealogy, search_antenati, search_geneteka,
            search_digitalarkivet, search_archion, search_matricula,
        )
        mapping.update({
            'obituaries':     lambda: search_obituaries(first, last, birth_year, birth_place),
            'va_gravesite':   lambda: search_va_gravesite(_first_only, last, birth_year),
            'freebmd':        lambda: search_freebmd(first, last, birth_year, 'All'),
            'irish_birth':    lambda: search_irish_genealogy(first, last, birth_year, 'B'),
            'irish_death':    lambda: search_irish_genealogy(first, last, birth_year, 'D'),
            'antenati':       lambda: search_antenati(first, last, birth_year),
            'geneteka':       lambda: search_geneteka(first, last, birth_year),
            'digitalarkivet': lambda: search_digitalarkivet(first, last, birth_year),
            'archion':        lambda: search_archion(first, last, birth_year),
            'matricula':      lambda: search_matricula(first, last, birth_year, 'DE'),
        })

    for key in picks:
        if key in mapping:
            tasks[key] = mapping[key]
    return tasks


# ---------------------------------------------------------------------------
# Fallback layer — name variants + broader search when confidence < 35
# ---------------------------------------------------------------------------

_COMMON_VARIANTS = {
    'henderson': ['hendersen', 'hendenson', 'henderso'],
    'johnson':   ['jonson', 'johnston', 'johnsen'],
    'smith':     ['smyth', 'smythe'],
    'brown':     ['browne', 'broun'],
    'wilson':    ['willson', 'wilsen'],
    'miller':    ['miler', 'millor'],
    'davis':     ['davies', 'daviss'],
    'thomas':    ['tomas', 'thomass'],
    'taylor':    ['tailor', 'tayler'],
    'anderson':  ['andersen', 'anderso'],
    'jackson':   ['jakson', 'jacson'],
    'white':     ['whyte', 'wight'],
    'harris':    ['hariss', 'harriss'],
    'martin':    ['marten', 'martyn'],
    'thompson':  ['thomson', 'tompson'],
    'garcia':    ['garsia', 'garcea'],
    'martinez':  ['martines', 'martinex'],
    'robinson':  ['robison', 'robenson'],
    'clark':     ['clarke', 'clerke'],
    'rodriguez': ['rodrigues', 'rodrigvez'],
    'lewis':     ['louis', 'luis'],
    'lee':       ['lea', 'li'],
    'walker':    ['waller', 'walkor'],
    'hall':      ['hale', 'haul'],
    'allen':     ['alan', 'allin'],
    'young':     ['yong', 'younge'],
    'hernandez': ['hernandes', 'hernandex'],
    'king':      ['kinge', 'kyng'],
    'wright':    ['wwright', 'rite'],
    'scott':     ['skott', 'scot'],
    'green':     ['greene', 'grean'],
    'baker':     ['backer', 'baiker'],
    'adams':     ['addams', 'adames'],
    'nelson':    ['nelsen', 'nelso'],
    'hill':      ['hil', 'hille'],
    'campbell':  ['cambell', 'cambel'],
    'mitchell':  ['michell', 'mitchel'],
    'roberts':   ['robert', 'robarts'],
    'carter':    ['korter', 'cater'],
    'phillips':  ['philips', 'phillipps'],
    'evans':     ['evens', 'evins'],
    'turner':    ['torner', 'terner'],
    'torres':    ['torrez', 'torez'],
    'parker':    ['parkor', 'parkur'],
    'collins':   ['colins', 'colling'],
    'edwards':   ['edwords', 'edards'],
    'stewart':   ['stuart', 'steward'],
    'morris':    ['moris', 'moriss'],
    'sanchez':   ['sanches', 'sanchex'],
    'rogers':    ['roggers', 'roger'],
    'reed':      ['reid', 'read'],
    'cook':      ['cooke', 'coke'],
    'morgan':    ['morgen', 'morgon'],
    'bell':      ['bel', 'belle'],
    'murphy':    ['murphey', 'murfy'],
    'bailey':    ['bailee', 'baily'],
    'rivera':    ['revera', 'riviera'],
    'cooper':    ['couper', 'kooper'],
    'richardson': ['richrdson', 'ricardson'],
    'cox':       ['cocks', 'kox'],
    'howard':    ['howord', 'howerd'],
    'ward':      ['waard', 'wrd'],
    'torres':    ['torrez', 'torre'],
    'peterson':  ['petersen', 'peterso'],
    'gray':      ['grey', 'grei'],
    'ramirez':   ['ramires', 'ramirex'],
    'james':     ['jaymes', 'jemes'],
    'watson':    ['wotson', 'watsen'],
    'brooks':    ['brook', 'broks'],
    'kelly':     ['kely', 'kelley'],
    'sanders':   ['saunders', 'sandors'],
    'price':     ['prise', 'pryce'],
    'bennett':   ['bennet', 'benet'],
    'wood':      ['woods', 'wod'],
    'barnes':    ['barns', 'barness'],
    'ross':      ['rosse', 'rouse'],
    'henderson': ['hendersen', 'henderso', 'hendrson'],
    'coleman':   ['colman', 'colemen'],
    'jenkins':   ['jenkin', 'jenckins'],
    'perry':     ['perri', 'pery'],
    'butler':    ['buttler', 'buler'],
    'simmons':   ['simons', 'simmon'],
    'foster':    ['fostar', 'fester'],
    'gonzales':  ['gonzalez', 'gonsales'],
    'bryant':    ['briant', 'bryent'],
    'alexander': ['alexandr', 'alexandar'],
    'russell':   ['russel', 'rssel'],
    'griffin':   ['grifin', 'griffon'],
    'diaz':      ['dias', 'deas'],
    'hayes':     ['hays', 'haies'],
    'myers':     ['miers', 'meyers'],
    'ford':      ['forde', 'frd'],
    'hamilton':  ['hamilten', 'hamiltun'],
    'graham':    ['grahem', 'graam'],
    'sullivan':  ['sulliven', 'sulivan'],
    'wallace':   ['walace', 'wallis'],
    'woods':     ['wuds', 'woodes'],
    'cole':      ['kole', 'coel'],
    'west':      ['wuest', 'wset'],
    'jordan':    ['jordon', 'gordan'],
    'owens':     ['owen', 'owenes'],
    'reynolds':  ['renolds', 'reynold'],
    'fisher':    ['fissher', 'fishar'],
    'ellis':     ['elis', 'elliss'],
    'harrison':  ['harison', 'harisson'],
    'gibson':    ['gipson', 'gibsen'],
    'mcdonald':  ['macdonald', 'mcdonnal'],
    'cruz':      ['cruse', 'crus'],
    'marshall':  ['marshal', 'marshel'],
    'ortiz':     ['ortis', 'ortez'],
    'gomez':     ['gomes', 'gomze'],
    'murray':    ['murrey', 'murry'],
    'freeman':   ['freman', 'freemen'],
    'wells':     ['welles', 'wels'],
    'webb':      ['web', 'webbe'],
    'simpson':   ['simpsen', 'simson'],
    'stevens':   ['stephen', 'stevins'],
    'tucker':    ['tuker', 'tuckor'],
    'porter':    ['portor', 'partor'],
    'hunter':    ['huntar', 'huntor'],
    'hicks':     ['hick', 'hickes'],
    'crawford':  ['craford', 'crawferd'],
    'henry':     ['henery', 'henrey'],
    'boyd':      ['boid', 'boyde'],
    'mason':     ['masen', 'mesen'],
    'morales':   ['moralis', 'morrales'],
    'kennedy':   ['kenedy', 'kennidy'],
    'warren':    ['woren', 'warrn'],
    'dixon':     ['dickson', 'dixen'],
    'ramos':     ['ramoz', 'ramas'],
    'reyes':     ['reis', 'reies'],
    'burns':     ['burnes', 'byrns'],
    'gordon':    ['gordan', 'gordin'],
    'shaw':      ['shawe', 'shw'],
    'holmes':    ['holms', 'holmess'],
    'rice':      ['ryce', 'ricce'],
    'robertson': ['roberson', 'robertsen'],
    'hunt':      ['hunte', 'hant'],
    'black':     ['blak', 'blache'],
    'daniels':   ['daniel', 'daniells'],
    'palmer':    ['palmar', 'palmor'],
    'mills':     ['mils', 'millss'],
    'nichols':   ['nicols', 'nicolls'],
    'grant':     ['grante', 'grent'],
    'knight':    ['kight', 'nite'],
    'ferguson':  ['fergson', 'fergusen'],
    'rose':      ['roze', 'roes'],
    'stone':     ['ston', 'stoan'],
    'hawkins':   ['hawkens', 'haukins'],
    'dunn':      ['dun', 'dunne'],
    'perkins':   ['perkin', 'purkens'],
    'hudson':    ['hutson', 'hudsen'],
    'spencer':   ['spenser', 'spencor'],
    'gardner':   ['garner', 'gardener'],
    'payne':     ['pain', 'paine'],
    'pierce':    ['pierse', 'perce'],
    'berry':     ['bery', 'berrie'],
    'matthews':  ['mathews', 'mathues'],
    'arnold':    ['arnald', 'arneld'],
    'wagner':    ['wagoner', 'vagner'],
    'willis':    ['wilis', 'williss'],
    'ray':       ['rae', 'rei'],
    'watkins':   ['watkin', 'watkens'],
    'olson':     ['olsen', 'olsen'],
    'carr':      ['care', 'karr'],
    'luna':      ['lona', 'luuna'],
    'lawson':    ['lausen', 'lawsen'],
    'little':    ['litle', 'littel'],
    'boston':    ['beston', 'bosten'],
    'horton':    ['horten', 'hortn'],
    'ryan':      ['ryen', 'rian'],
    'armstrong': ['armstong', 'armsrong'],
    'obrien':    ['obrion', 'obrian'],
    'harvey':    ['harvie', 'harvy'],
    'hanson':    ['hansen', 'hanson'],
    'medina':    ['medena', 'medyna'],
    'rodriguez': ['rodrigez', 'rodriquez'],
    'steele':    ['steel', 'stelle'],
    'adkins':    ['atkins', 'adkens'],
    'patel':     ['patal', 'patil'],
}


def _name_variants(last: str) -> list[str]:
    """Return common spelling variants for a surname."""
    key = last.lower().strip()
    variants = list(_COMMON_VARIANTS.get(key, []))

    # Phonetic transforms: vowel swaps, double→single consonants, silent endings
    transforms = []
    # e/a vowel swap in middle
    if len(key) > 4:
        for i in range(1, len(key) - 1):
            if key[i] == 'e':
                transforms.append(key[:i] + 'a' + key[i+1:])
            elif key[i] == 'a':
                transforms.append(key[:i] + 'e' + key[i+1:])
    # Drop trailing 's' or 'e'
    if key.endswith('s') and len(key) > 4:
        transforms.append(key[:-1])
    if key.endswith('e') and len(key) > 4:
        transforms.append(key[:-1])
    # Add trailing 's'
    if not key.endswith('s') and len(key) > 3:
        transforms.append(key + 's')
    # son → sen, sen → son
    if key.endswith('son'):
        transforms.append(key[:-3] + 'sen')
    elif key.endswith('sen'):
        transforms.append(key[:-3] + 'son')

    seen = {key}
    result = []
    for v in variants + transforms:
        v = v.strip()
        if v and v not in seen and len(v) >= 3:
            seen.add(v)
            result.append(v.title())
    return result[:6]


def _run_fallback_layer(first: str, last: str, middle: str,
                        birth_year, birth_place: str,
                        all_results: list, _ctx) -> list:
    """
    Expand the search when confidence < 35.  Returns new results list (deduplicated).
    Tries: middle-name-as-surname, name variants, ±20yr birth range, state archives.
    Does NOT yield SSE — caller yields status before calling.
    """
    fallback_hits = []
    seen_urls = {r.get('url', '') for r in all_results}

    def _add(results, tag):
        for r in results:
            r = dict(r)
            r['fallback_strategy'] = tag
            url = r.get('url', '')
            if url not in seen_urls:
                seen_urls.add(url)
                fallback_hits.append(r)

    # Strategy 1: middle name as surname (e.g. "Orville Cleckner Henderson" → search "Cleckner")
    if middle:
        _add(search_wikitree(first, middle, birth_year), 'middle_as_surname')
        _add(search_chronicling(first, middle, birth_year), 'middle_as_surname')

    # Strategy 2: name spelling variants
    variants = _name_variants(last)
    for variant in variants[:3]:
        _add(search_wikitree(first, variant, birth_year), f'variant_{variant.lower()}')
        _add(search_nara(first, variant, birth_year, birth_place), f'variant_{variant.lower()}')

    # Strategy 3: broader birth year window (±20 instead of ±5)
    if birth_year:
        broader_ctx = ResearchContext(
            first=first, last=last,
            birth_year=birth_year, birth_place=birth_place,
        )
        # Directly query chronicling with loose year window
        _add(search_chronicling(first, last, None), 'broader_year_range')

    # Strategy 4: NARA (always worth a fallback hit regardless of prior picks)
    _add(search_nara(first, last, birth_year, birth_place), 'nara_fallback')
    if middle:
        _add(search_nara(first, middle, birth_year, birth_place), 'nara_middle_surname')

    return fallback_hits


def run_us_cascade_stream(first: str = '', last: str = '', birth_year: int = None,
                          birth_place: str = '', country_hint: str = '',
                          community_results: list = None, skip_vault: bool = False,
                          middle: str = '', death_year: int = None, death_place: str = ''):
    """
    Agentic two-phase search generator yielding SSE strings.

    Phase 1: fast core sources (WikiTree, FindAGrave, DPLA census, NARA)
    Agent:   OpenRouter reads Phase 1 clues, picks best Phase 2 sources
    Phase 2: only the AI-chosen sources run
    Final:   score, gap analysis, AI summary

    skip_vault: set True when the caller already ran vault search (avoids double-hit)
    """
    from .ai_synthesis import synthesize_gaps, agentic_pick_sources

    all_results = []
    _first_only = first.split()[0] if first else first

    _ctx = ResearchContext(
        first=first, last=last,
        birth_year=birth_year,
        birth_place=birth_place,
        generation=1,
        generation_label='target',
    )

    yield 'data: ' + json.dumps({
        'agent_status': 'context',
        'generation': _ctx.generation,
        'generation_label': _ctx.generation_label,
        'target': f'{first} {last}'.strip(),
        'constraints': _ctx.constraints,
    }) + '\n\n'

    # ── Phase 0: vault search (internal, instant) ────────────────────────────
    vault_hits = []
    if skip_vault:
        yield 'data: ' + json.dumps({'agent_status': 'vault', 'message': 'Vault already searched — checking external sources…'}) + '\n\n'
    else:
        yield 'data: ' + json.dumps({'agent_status': 'vault', 'message': 'Searching RootBridge vault…'}) + '\n\n'

    _vault_t0 = time.time()
    try:
        if not skip_vault:
            from .match_index import search_vault, search_ged_vault
            vault_hits = search_vault(last, first, birth_year, limit=20)
        vault_ms = round((time.time() - _vault_t0) * 1000)
        if vault_hits:
            vault_results = [{
                'source': 'rootbridge_vault',
                'record_type': 'vault',
                'title': f"{h.get('first_name', '')} {h.get('last_name', '')}".strip(),
                'date': str(h.get('birth_year', '')) if h.get('birth_year') else '',
                'location': f"{h.get('birth_state', '')} {h.get('birth_country', '')}".strip(),
                'url': '',
                'vault_score': h.get('score', 0),
            } for h in vault_hits]
            all_results.extend(vault_results)
            yield 'data: ' + json.dumps({
                'source': 'rootbridge_vault',
                'results': vault_results,
                'count': len(vault_results),
                'total': len(all_results),
                'vault_ms': vault_ms,
            }) + '\n\n'
        else:
            yield 'data: ' + json.dumps({'source': 'rootbridge_vault', 'count': 0, 'vault_ms': vault_ms}) + '\n\n'
    except Exception as e:
        vault_ms = round((time.time() - _vault_t0) * 1000)
        logger.debug('Vault search failed: %s', e)
        yield 'data: ' + json.dumps({'source': 'rootbridge_vault', 'count': 0, 'vault_ms': vault_ms}) + '\n\n'

    # ── Phase 0b: GED vault index (857K persons from 7,278 GEDCOM files) ────────
    yield 'data: ' + json.dumps({'agent_status': 'ged_vault', 'message': 'Searching 857,000-person GED archive…'}) + '\n\n'
    _ged_t0 = time.time()
    try:
        from .match_index import search_ged_vault
        ged_hits = search_ged_vault(last, first, birth_year, birth_place, limit=20)
        ged_ms   = round((time.time() - _ged_t0) * 1000)
        if ged_hits:
            all_results.extend(ged_hits)
            yield 'data: ' + json.dumps({
                'source': 'ged_vault',
                'results': ged_hits,
                'count': len(ged_hits),
                'total': len(all_results),
                'ged_ms': ged_ms,
            }) + '\n\n'
        else:
            yield 'data: ' + json.dumps({'source': 'ged_vault', 'count': 0, 'ged_ms': ged_ms}) + '\n\n'
    except Exception as e:
        logger.debug('GED vault search failed: %s', e)
        yield 'data: ' + json.dumps({'source': 'ged_vault', 'count': 0}) + '\n\n'

    if community_results:
        _cr = list(community_results)
        all_results.extend(_cr)
        yield 'data: ' + json.dumps({
            'source': 'rootbridge_community',
            'results': _cr,
            'count': len(_cr),
            'total': len(all_results),
        }) + '\n\n'

    # ── Phase 1: reliable external sources (no Playwright — gets blocked) ────────
    # SSDI Oracle chunk search — only included if Oracle Storage is configured
    from .oracle_storage import search_ssdi as _search_ssdi, chunk_is_available as _oracle_ok
    _ssdi_task = {'ssdi': lambda: _search_ssdi(first, last, birth_year, birth_place)} \
        if _oracle_ok() else {}

    # Detect US location and era to skip irrelevant sources
    import re as _re2
    _all_places = f'{birth_place} {death_place}'.lower()
    _place_tokens = set(_re2.split(r'[\s,]+', _all_places))
    _us_state_set = {
        'alabama','alaska','arizona','arkansas','california','colorado','connecticut',
        'delaware','florida','georgia','hawaii','idaho','illinois','indiana','iowa',
        'kansas','kentucky','louisiana','maine','maryland','massachusetts','michigan',
        'minnesota','mississippi','missouri','montana','nebraska','nevada',
        'new hampshire','new jersey','new mexico','new york','north carolina',
        'north dakota','ohio','oklahoma','oregon','pennsylvania','rhode island',
        'south carolina','south dakota','tennessee','texas','utah','vermont',
        'virginia','washington','west virginia','wisconsin','wyoming',
        'usa','united states',
        'al','ak','az','ar','ca','co','ct','de','fl','ga','hi','id','il','in',
        'ia','ks','ky','la','me','md','ma','mi','mn','ms','mo','mt','ne','nv',
        'nh','nj','nm','ny','nc','nd','oh','ok','or','pa','ri','sc','sd','tn',
        'tx','ut','vt','va','wa','wv','wi','wy',
    }
    _is_us = bool(_place_tokens & _us_state_set)
    _is_modern = (birth_year and birth_year > 1920) or (death_year and death_year > 2000)
    # Chronicling America only covers 1770-1963 — useless for modern persons
    _chron_useful = not _is_modern

    # Use death_place for obituary/burial searches — more accurate than birth_place
    _search_place = death_place or birth_place

    _obit_task = {}
    if _is_modern:
        # Bing Lite HTTP fallback (no API key needed)
        if not os.environ.get('BRAVE_API_KEY'):
            _obit_task['bing_lite_obituary'] = lambda: search_bing_lite_obits(first, last, birth_year, _search_place)
        _obit_task['legacy_obituary'] = lambda: search_legacy_obits(first, last, birth_year, _search_place)
        if _playwright_available():
            from .playwright_scrapers import search_obituaries as _search_obits, search_findagrave as _search_fg
            _obit_task['obituaries'] = lambda: _search_obits(first, last, birth_year, _search_place)
            _obit_task['findagrave'] = lambda: _search_fg(_first_only, last, birth_year, death_year)

    phase1_tasks = {
        **_ssdi_task,
        **_obit_task,
    }
    # WikiTree and Chronicling America only useful for older US records
    if not _is_modern:
        phase1_tasks['wikitree']    = lambda: search_wikitree(_first_only, last, birth_year)
    if _chron_useful:
        phase1_tasks['chronicling'] = lambda: search_chronicling(first, last, birth_year)
    # NARA useful for all eras (military, immigration, naturalization)
    if not _is_us or (birth_year and birth_year < 1960):
        phase1_tasks['nara'] = lambda: search_nara(_first_only, last, birth_year, birth_place)

    yield 'data: ' + json.dumps({'agent_status': 'phase1', 'message': 'Searching external archives…'}) + '\n\n'

    yield from _run_phase(phase1_tasks, all_results, timeout=14)

    # ── Agent decision ───────────────────────────────────────────────────────
    yield 'data: ' + json.dumps({'agent_status': 'thinking', 'message': 'Alfred is choosing the best next sources…'}) + '\n\n'

    picks = agentic_pick_sources(
        person={'first_name': first, 'last_name': last,
                'birth_year': birth_year, 'birth_place': birth_place,
                'death_year': death_year, 'death_place': death_place,
                'is_us': _is_us, 'is_modern': _is_modern},
        phase1_results=all_results,
        ctx=_ctx,
    )
    logger.info('Agentic picks for %s %s: %s', first, last, picks)

    yield 'data: ' + json.dumps({
        'agent_status': 'phase2',
        'picks': picks,
        'message': f'Following {len(picks)} leads…',
    }) + '\n\n'

    # ── Phase 2: AI-chosen sources ───────────────────────────────────────────
    phase2_tasks = _build_phase2_tasks(picks, first, last, birth_year or 0, birth_place)
    if phase2_tasks:
        yield from _run_phase(phase2_tasks, all_results, timeout=16)

    # ── Final scoring + AI summary ───────────────────────────────────────────
    scored   = cross_reference_with_context(all_results, _ctx)
    snapshot = _build_person_snapshot(first, last, birth_year, birth_place, scored)
    gaps     = classify_gaps(snapshot, scored)
    score    = confidence_score(snapshot)
    corr     = sum(1 for r in scored if r.get('corroboration_score', 0) > 0)
    score    = min(score + (15 if corr >= 2 else 7 if corr == 1 else 0), 95)

    # ── Fallback layer — expand search when confidence is too low ────────────
    roadmap = None
    if score < 35:
        yield 'data: ' + json.dumps({
            'agent_status': 'fallback',
            'message': 'No strong match found — trying name variants and broader search…',
        }) + '\n\n'

        fallback_hits = _run_fallback_layer(
            first, last, middle, birth_year, birth_place, all_results, _ctx
        )

        if fallback_hits:
            all_results.extend(fallback_hits)
            # Re-score with context filter
            scored   = cross_reference_with_context(all_results, _ctx)
            snapshot = _build_person_snapshot(first, last, birth_year, birth_place, scored)
            gaps     = classify_gaps(snapshot, scored)
            score    = confidence_score(snapshot)
            corr     = sum(1 for r in scored if r.get('corroboration_score', 0) > 0)
            score    = min(score + (15 if corr >= 2 else 7 if corr == 1 else 0), 95)

        # If still low confidence after fallback, generate a research roadmap
        if score < 35:
            from .ai_synthesis import generate_roadmap
            roadmap = generate_roadmap(
                first=first, last=last, middle=middle,
                birth_year=birth_year, birth_place=birth_place,
                results=scored, gaps=gaps,
            )

    yield 'data: ' + json.dumps({'agent_status': 'synthesizing', 'message': 'Reasoning about results…'}) + '\n\n'

    synthesis = synthesize_gaps(
        person={'first_name': first, 'last_name': last,
                'birth_year': birth_year, 'birth_state': birth_place},
        results=scored, gaps=gaps,
        ctx=_ctx,
    )

    done_payload = {
        'done': True,
        'results': scored,
        'gaps': gaps,
        'confidence': score,
        'summary': synthesis['summary'],
        'corroborated_count': corr,
        'agentic_picks': picks,
    }
    if roadmap:
        done_payload['roadmap'] = roadmap

    yield 'data: ' + json.dumps(done_payload) + '\n\n'


# ---------------------------------------------------------------------------
# Non-streaming entry point (kept for guest search + cache fallback)
# ---------------------------------------------------------------------------

def extend_lineage(person: dict, max_generations: int = 3) -> dict:
    """
    Starting from a known person, search backward one generation at a time
    until max_generations is reached or no candidates are found.

    Returns a dict keyed by generation number:
      {
        2: {'father': {...results...}, 'mother': {...results...}},
        3: {'father': {...}, 'mother': {...}},
        ...
      }

    Each entry is the best-scoring candidate found, or None if no match.
    """
    from .ai_synthesis import synthesize_gaps

    tree = {}
    current_gen = [person]  # list of persons at the current generation

    for gen in range(2, max_generations + 2):
        next_gen = []
        for child in current_gen:
            child_ctx = ResearchContext.from_person(child, generation=gen - 1)
            child_birth = child.get('birth_year')
            child_place = child.get('birth_state') or child.get('birth_place') or ''
            child_last  = child.get('last_name', '')

            # Estimate parent birth year: 25-35 years before child
            parent_birth_est = (child_birth - 28) if child_birth else None
            # If child is in early US location, parents may have been immigrants
            _country = child.get('country_hint', '')

            # Father: same surname, born ~28yr earlier, same region
            father_ctx = build_parent_context(
                child_ctx,
                parent_last=child_last,
                parent_birth_year=parent_birth_est,
                parent_birth_place=child_place,
            )
            father_results = run_us_cascade(
                first='', last=child_last,
                birth_year=parent_birth_est,
                birth_place=child_place,
                country_hint=_country,
            )
            father_candidates = father_results.get('results', [])
            # Score against father context
            father_candidates = father_ctx.filter_results(father_candidates, min_score=35)
            father_best = father_candidates[0] if father_candidates else None

            # Mother: maiden name unknown — search by birth place + year only
            # (birth_place same region, year ±5 of father estimate)
            mother_ctx = build_parent_context(
                child_ctx,
                parent_last='',
                parent_birth_year=parent_birth_est,
                parent_birth_place=child_place,
            )
            # Only search mother if we have place context — otherwise too noisy
            mother_best = None
            if child_place and parent_birth_est:
                mother_results = run_us_cascade(
                    first='', last='',
                    birth_year=parent_birth_est,
                    birth_place=child_place,
                    country_hint=_country,
                )
                mother_candidates = mother_results.get('results', [])
                mother_candidates = mother_ctx.filter_results(mother_candidates, min_score=35)
                mother_best = mother_candidates[0] if mother_candidates else None

            gen_label = _GENERATION_LABELS.get(gen, f'generation-{gen}')
            tree[gen] = tree.get(gen, [])
            tree[gen].append({
                'child_name': f"{child.get('first_name', '')} {child.get('last_name', '')}".strip(),
                'generation': gen,
                'generation_label': gen_label,
                'father': father_best,
                'mother': mother_best,
                'father_candidates': father_candidates[:3],
                'mother_candidates': mother_candidates[:3] if child_place and parent_birth_est else [],
            })

            # Queue confirmed candidates for next generation
            if father_best:
                father_as_person = {
                    'first_name': father_best.get('first_name', ''),
                    'last_name': father_best.get('last_name', child_last),
                    'birth_year': father_best.get('birth_year', parent_birth_est),
                    'birth_state': father_best.get('birth_place', child_place),
                    'country_hint': _country,
                }
                next_gen.append(father_as_person)

        if not next_gen:
            break
        current_gen = next_gen

    return tree


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
