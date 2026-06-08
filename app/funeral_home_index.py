"""
funeral_home_index.py — Regional funeral home discovery + obituary scraping

Flow:
  death_place → county resolution → cached funeral home URLs → scrape each for person

Cache lives in data/funeral_homes.db (SQLite, persists across deploys via volume).
Discovery uses one Serper call per county (fires once, cached forever).
Scraper handles the major funeral home platforms plus generic HTML extraction.
"""

import os
import re
import time
import sqlite3
import logging
import hashlib
from pathlib import Path

import requests as req_lib
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache DB
# ---------------------------------------------------------------------------

_DB_PATH = Path(__file__).parent.parent / 'data' / 'funeral_homes.db'
SERPER_API_KEY   = os.environ.get('SERPER_API_KEY', '')
SERPER_SEARCH_URL = 'https://google.serper.dev/search'

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


def _get_conn():
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS funeral_homes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            county_key  TEXT NOT NULL,        -- "tangipahoa_la" normalized
            name        TEXT,
            url         TEXT NOT NULL,
            platform    TEXT,                 -- "funeralone","tributewall","generic"
            discovered  INTEGER DEFAULT 0,    -- unix timestamp
            UNIQUE(county_key, url)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS county_cache (
            county_key  TEXT PRIMARY KEY,
            county_name TEXT,
            state_abbr  TEXT,
            discovered  INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# City → County resolution
# ---------------------------------------------------------------------------

# Top ~500 US cities hardcoded for zero-latency resolution.
# Format: "city|state_abbr" → "county_name|state_abbr"
_CITY_COUNTY = {
    # Louisiana
    'hammond|la':         'tangipahoa|la',
    'ponchatoula|la':     'tangipahoa|la',
    'amite|la':           'tangipahoa|la',
    'springfield|la':     'livingston|la',
    'denham springs|la':  'livingston|la',
    'walker|la':          'livingston|la',
    'baton rouge|la':     'east baton rouge|la',
    'new orleans|la':     'orleans|la',
    'shreveport|la':      'caddo|la',
    'lafayette|la':       'lafayette|la',
    'lake charles|la':    'calcasieu|la',
    'monroe|la':          'ouachita|la',
    'alexandria|la':      'rapides|la',
    'slidell|la':         'st. tammany|la',
    'covington|la':       'st. tammany|la',
    'mandeville|la':      'st. tammany|la',
    'kenner|la':          'jefferson|la',
    'metairie|la':        'jefferson|la',
    'gretna|la':          'jefferson|la',
    'houma|la':           'terrebonne|la',
    'thibodaux|la':       'lafourche|la',
    'natchitoches|la':    'natchitoches|la',
    'morgan city|la':     'st. mary|la',
    'opelousas|la':       'st. landry|la',
    'sulphur|la':         'calcasieu|la',
    'ruston|la':          'lincoln|la',
    'bastrop|la':         'morehouse|la',
    # Mississippi
    'jackson|ms':         'hinds|ms',
    'gulfport|ms':        'harrison|ms',
    'biloxi|ms':          'harrison|ms',
    'hattiesburg|ms':     'forrest|ms',
    'meridian|ms':        'lauderdale|ms',
    'tupelo|ms':          'lee|ms',
    'southaven|ms':       'desoto|ms',
    'olive branch|ms':    'desoto|ms',
    # Alabama
    'birmingham|al':      'jefferson|al',
    'montgomery|al':      'montgomery|al',
    'huntsville|al':      'madison|al',
    'mobile|al':          'mobile|al',
    'tuscaloosa|al':      'tuscaloosa|al',
    'auburn|al':          'lee|al',
    'dothan|al':          'houston|al',
    'decatur|al':         'morgan|al',
    # Tennessee
    'nashville|tn':       'davidson|tn',
    'memphis|tn':         'shelby|tn',
    'knoxville|tn':       'knox|tn',
    'chattanooga|tn':     'hamilton|tn',
    'murfreesboro|tn':    'rutherford|tn',
    'jackson|tn':         'madison|tn',
    'franklin|tn':        'williamson|tn',
    'clarksville|tn':     'montgomery|tn',
    # Texas (top cities)
    'houston|tx':         'harris|tx',
    'san antonio|tx':     'bexar|tx',
    'dallas|tx':          'dallas|tx',
    'austin|tx':          'travis|tx',
    'fort worth|tx':      'tarrant|tx',
    'el paso|tx':         'el paso|tx',
    'arlington|tx':       'tarrant|tx',
    'corpus christi|tx':  'nueces|tx',
    'plano|tx':           'collin|tx',
    'lubbock|tx':         'lubbock|tx',
    'garland|tx':         'dallas|tx',
    'irving|tx':          'dallas|tx',
    'amarillo|tx':        'potter|tx',
    'grand prairie|tx':   'dallas|tx',
    'beaumont|tx':        'jefferson|tx',
    'waco|tx':            'mclennan|tx',
    'tyler|tx':           'smith|tx',
    # Florida
    'jacksonville|fl':    'duval|fl',
    'miami|fl':           'miami-dade|fl',
    'tampa|fl':           'hillsborough|fl',
    'orlando|fl':         'orange|fl',
    'st. petersburg|fl':  'pinellas|fl',
    'hialeah|fl':         'miami-dade|fl',
    'tallahassee|fl':     'leon|fl',
    'fort lauderdale|fl': 'broward|fl',
    'port st. lucie|fl':  'st. lucie|fl',
    'cape coral|fl':      'lee|fl',
    'pembroke pines|fl':  'broward|fl',
    'hollywood|fl':       'broward|fl',
    'gainesville|fl':     'alachua|fl',
    'miramar|fl':         'broward|fl',
    'coral springs|fl':   'broward|fl',
    'clearwater|fl':      'pinellas|fl',
    'palm bay|fl':        'brevard|fl',
    'pompano beach|fl':   'broward|fl',
    'west palm beach|fl': 'palm beach|fl',
    'lakeland|fl':        'polk|fl',
    'pensacola|fl':       'escambia|fl',
    # Georgia
    'atlanta|ga':         'fulton|ga',
    'augusta|ga':         'richmond|ga',
    'columbus|ga':        'muscogee|ga',
    'savannah|ga':        'chatham|ga',
    'athens|ga':          'clarke|ga',
    'macon|ga':           'bibb|ga',
    'roswell|ga':         'fulton|ga',
    'albany|ga':          'dougherty|ga',
    # South Carolina
    'columbia|sc':        'richland|sc',
    'charleston|sc':      'charleston|sc',
    'north charleston|sc':'charleston|sc',
    'greenville|sc':      'greenville|sc',
    'rock hill|sc':       'york|sc',
    'spartanburg|sc':     'spartanburg|sc',
    # North Carolina
    'charlotte|nc':       'mecklenburg|nc',
    'raleigh|nc':         'wake|nc',
    'greensboro|nc':      'guilford|nc',
    'durham|nc':          'durham|nc',
    'winston-salem|nc':   'forsyth|nc',
    'fayetteville|nc':    'cumberland|nc',
    'cary|nc':            'wake|nc',
    'wilmington|nc':      'new hanover|nc',
    # Virginia
    'virginia beach|va':  'virginia beach|va',
    'norfolk|va':         'norfolk|va',
    'chesapeake|va':      'chesapeake|va',
    'richmond|va':        'richmond city|va',
    'newport news|va':    'newport news|va',
    'alexandria|va':      'alexandria|va',
    'hampton|va':         'hampton|va',
    'roanoke|va':         'roanoke city|va',
    # Ohio
    'columbus|oh':        'franklin|oh',
    'cleveland|oh':       'cuyahoga|oh',
    'cincinnati|oh':      'hamilton|oh',
    'toledo|oh':          'lucas|oh',
    'akron|oh':           'summit|oh',
    'dayton|oh':          'montgomery|oh',
    'parma|oh':           'cuyahoga|oh',
    'canton|oh':          'stark|oh',
    'youngstown|oh':      'mahoning|oh',
    'lorain|oh':          'lorain|oh',
    # Pennsylvania
    'philadelphia|pa':    'philadelphia|pa',
    'pittsburgh|pa':      'allegheny|pa',
    'allentown|pa':       'lehigh|pa',
    'erie|pa':            'erie|pa',
    'reading|pa':         'berks|pa',
    'scranton|pa':        'lackawanna|pa',
    'bethlehem|pa':       'northampton|pa',
    'lancaster|pa':       'lancaster|pa',
    'harrisburg|pa':      'dauphin|pa',
    # New York
    'new york|ny':        'new york|ny',
    'buffalo|ny':         'erie|ny',
    'rochester|ny':       'monroe|ny',
    'yonkers|ny':         'westchester|ny',
    'syracuse|ny':        'onondaga|ny',
    'albany|ny':          'albany|ny',
    'new rochelle|ny':    'westchester|ny',
    'mount vernon|ny':    'westchester|ny',
    'utica|ny':           'oneida|ny',
    # Illinois
    'chicago|il':         'cook|il',
    'aurora|il':          'kane|il',
    'rockford|il':        'winnebago|il',
    'joliet|il':          'will|il',
    'naperville|il':      'dupage|il',
    'springfield|il':     'sangamon|il',
    'peoria|il':          'peoria|il',
    # Michigan
    'detroit|mi':         'wayne|mi',
    'grand rapids|mi':    'kent|mi',
    'warren|mi':          'macomb|mi',
    'sterling heights|mi':'macomb|mi',
    'ann arbor|mi':       'washtenaw|mi',
    'lansing|mi':         'ingham|mi',
    'flint|mi':           'genesee|mi',
    # Indiana
    'indianapolis|in':    'marion|in',
    'fort wayne|in':      'allen|in',
    'evansville|in':      'vanderburgh|in',
    'south bend|in':      'st. joseph|in',
    'carmel|in':          'hamilton|in',
    'hammond|in':         'lake|in',
    'muncie|in':          'delaware|in',
    # Missouri
    'kansas city|mo':     'jackson|mo',
    'st. louis|mo':       'st. louis city|mo',
    'springfield|mo':     'greene|mo',
    'columbia|mo':        'boone|mo',
    'independence|mo':    'jackson|mo',
    # Arkansas
    'little rock|ar':     'pulaski|ar',
    'fort smith|ar':      'sebastian|ar',
    'fayetteville|ar':    'washington|ar',
    'springdale|ar':      'washington|ar',
    'jonesboro|ar':       'craighead|ar',
    # Kentucky
    'louisville|ky':      'jefferson|ky',
    'lexington|ky':       'fayette|ky',
    'bowling green|ky':   'warren|ky',
    'owensboro|ky':       'daviess|ky',
    'covington|ky':       'kenton|ky',
    # West Virginia
    'charleston|wv':      'kanawha|wv',
    'huntington|wv':      'cabell|wv',
    'morgantown|wv':      'monongalia|wv',
    # Oklahoma
    'oklahoma city|ok':   'oklahoma|ok',
    'tulsa|ok':           'tulsa|ok',
    'norman|ok':          'cleveland|ok',
    'broken arrow|ok':    'tulsa|ok',
    # Kansas
    'wichita|ks':         'sedgwick|ks',
    'overland park|ks':   'johnson|ks',
    'kansas city|ks':     'wyandotte|ks',
    'topeka|ks':          'shawnee|ks',
    # Nebraska
    'omaha|ne':           'douglas|ne',
    'lincoln|ne':         'lancaster|ne',
    # Iowa
    'des moines|ia':      'polk|ia',
    'cedar rapids|ia':    'linn|ia',
    'davenport|ia':       'scott|ia',
    # Minnesota
    'minneapolis|mn':     'hennepin|mn',
    'st. paul|mn':        'ramsey|mn',
    'rochester|mn':       'olmsted|mn',
    'duluth|mn':          'st. louis|mn',
    # Wisconsin
    'milwaukee|wi':       'milwaukee|wi',
    'madison|wi':         'dane|wi',
    'green bay|wi':       'brown|wi',
    # California
    'los angeles|ca':     'los angeles|ca',
    'san diego|ca':       'san diego|ca',
    'san jose|ca':        'santa clara|ca',
    'san francisco|ca':   'san francisco|ca',
    'fresno|ca':          'fresno|ca',
    'sacramento|ca':      'sacramento|ca',
    'long beach|ca':      'los angeles|ca',
    'oakland|ca':         'alameda|ca',
    'bakersfield|ca':     'kern|ca',
    'anaheim|ca':         'orange|ca',
    'stockton|ca':        'san joaquin|ca',
    'riverside|ca':       'riverside|ca',
    'irvine|ca':          'orange|ca',
    'modesto|ca':         'stanislaus|ca',
    'san bernardino|ca':  'san bernardino|ca',
    # Arizona
    'phoenix|az':         'maricopa|az',
    'tucson|az':          'pima|az',
    'mesa|az':            'maricopa|az',
    'chandler|az':        'maricopa|az',
    'scottsdale|az':      'maricopa|az',
    'glendale|az':        'maricopa|az',
    'gilbert|az':         'maricopa|az',
    'tempe|az':           'maricopa|az',
    # Nevada
    'las vegas|nv':       'clark|nv',
    'henderson|nv':       'clark|nv',
    'reno|nv':            'washoe|nv',
    # Colorado
    'denver|co':          'denver|co',
    'colorado springs|co':'el paso|co',
    'aurora|co':          'arapahoe|co',
    'fort collins|co':    'larimer|co',
    'lakewood|co':        'jefferson|co',
    # Washington
    'seattle|wa':         'king|wa',
    'spokane|wa':         'spokane|wa',
    'tacoma|wa':          'pierce|wa',
    'vancouver|wa':       'clark|wa',
    'bellevue|wa':        'king|wa',
    # Oregon
    'portland|or':        'multnomah|or',
    'salem|or':           'marion|or',
    'eugene|or':          'lane|or',
    # Idaho
    'boise|id':           'ada|id',
    # Utah
    'salt lake city|ut':  'salt lake|ut',
    'west valley city|ut':'salt lake|ut',
    'provo|ut':           'utah|ut',
    # Montana
    'billings|mt':        'yellowstone|mt',
    'missoula|mt':        'missoula|mt',
    # Wyoming
    'cheyenne|wy':        'laramie|wy',
    # New Mexico
    'albuquerque|nm':     'bernalillo|nm',
    'santa fe|nm':        'santa fe|nm',
    # Maryland
    'baltimore|md':       'baltimore city|md',
    'columbia|md':        'howard|md',
    'silver spring|md':   'montgomery|md',
    # Delaware
    'wilmington|de':      'new castle|de',
    # Connecticut
    'bridgeport|ct':      'fairfield|ct',
    'hartford|ct':        'hartford|ct',
    'new haven|ct':       'new haven|ct',
    # Massachusetts
    'boston|ma':          'suffolk|ma',
    'worcester|ma':       'worcester|ma',
    'springfield|ma':     'hampden|ma',
    # New Jersey
    'newark|nj':          'essex|nj',
    'jersey city|nj':     'hudson|nj',
    'paterson|nj':        'passaic|nj',
    # New Hampshire
    'manchester|nh':      'hillsborough|nh',
    'nashua|nh':          'hillsborough|nh',
    # Vermont
    'burlington|vt':      'chittenden|vt',
    # Maine
    'portland|me':        'cumberland|me',
    # Rhode Island
    'providence|ri':      'providence|ri',
}

# US state name → abbreviation
_STATE_ABBR = {
    'alabama': 'al', 'alaska': 'ak', 'arizona': 'az', 'arkansas': 'ar',
    'california': 'ca', 'colorado': 'co', 'connecticut': 'ct', 'delaware': 'de',
    'florida': 'fl', 'georgia': 'ga', 'hawaii': 'hi', 'idaho': 'id',
    'illinois': 'il', 'indiana': 'in', 'iowa': 'ia', 'kansas': 'ks',
    'kentucky': 'ky', 'louisiana': 'la', 'maine': 'me', 'maryland': 'md',
    'massachusetts': 'ma', 'michigan': 'mi', 'minnesota': 'mn', 'mississippi': 'ms',
    'missouri': 'mo', 'montana': 'mt', 'nebraska': 'ne', 'nevada': 'nv',
    'new hampshire': 'nh', 'new jersey': 'nj', 'new mexico': 'nm', 'new york': 'ny',
    'north carolina': 'nc', 'north dakota': 'nd', 'ohio': 'oh', 'oklahoma': 'ok',
    'oregon': 'or', 'pennsylvania': 'pa', 'rhode island': 'ri', 'south carolina': 'sc',
    'south dakota': 'sd', 'tennessee': 'tn', 'texas': 'tx', 'utah': 'ut',
    'vermont': 'vt', 'virginia': 'va', 'washington': 'wa', 'west virginia': 'wv',
    'wisconsin': 'wi', 'wyoming': 'wy',
}


def _normalize_county_key(county: str, state_abbr: str) -> str:
    c = re.sub(r'\s+(county|parish|borough|census area)$', '', county.lower().strip())
    c = re.sub(r'[^a-z0-9]', '_', c)
    s = state_abbr.lower().strip()[:2]
    return f'{c}_{s}'


def resolve_death_place_to_county(death_place: str) -> tuple[str, str, str] | None:
    """
    Parse death_place string → (county_key, county_name, state_abbr).
    Returns None if unresolvable.

    Examples:
      "Hammond, LA"          → ("tangipahoa_la", "Tangipahoa", "la")
      "Tangipahoa Parish, LA"→ ("tangipahoa_la", "Tangipahoa", "la")
      "Hammond, Louisiana"   → ("tangipahoa_la", "Tangipahoa", "la")
    """
    if not death_place:
        return None
    raw = death_place.strip().lower()

    # Extract state from trailing ", LA" or ", Louisiana"
    state_abbr = None
    for full, abbr in _STATE_ABBR.items():
        if raw.endswith(full) or raw.endswith(', ' + full):
            state_abbr = abbr
            raw = re.sub(r',?\s*' + re.escape(full) + r'$', '', raw).strip()
            break
    if not state_abbr:
        # Try 2-letter abbr at the end
        m = re.search(r',?\s+([a-z]{2})$', raw)
        if m:
            candidate = m.group(1)
            if candidate in _STATE_ABBR.values():
                state_abbr = candidate
                raw = raw[:m.start()].strip()

    if not state_abbr:
        return None

    # raw is now just the city/county portion
    city = raw.strip().rstrip(',').strip()

    # Direct city lookup
    city_key = f'{city}|{state_abbr}'
    if city_key in _CITY_COUNTY:
        county_raw, _ = _CITY_COUNTY[city_key].split('|')
        county_name = county_raw.title()
        county_key = _normalize_county_key(county_name, state_abbr)
        return county_key, county_name, state_abbr

    # Already a county name? (e.g. "Tangipahoa Parish, LA")
    stripped = re.sub(r'\s+(county|parish|borough)$', '', city.lower().strip())
    county_key = _normalize_county_key(stripped, state_abbr)
    return county_key, stripped.title(), state_abbr


# ---------------------------------------------------------------------------
# Funeral home discovery via Serper
# ---------------------------------------------------------------------------

def _discover_funeral_homes_serper(county_name: str, state_abbr: str) -> list[dict]:
    """Query Serper for funeral homes in a county. Returns list of {name, url}."""
    if not SERPER_API_KEY:
        return []
    query = f'funeral home obituaries {county_name} county {state_abbr.upper()}'
    try:
        resp = req_lib.post(
            SERPER_SEARCH_URL,
            json={'q': query, 'num': 10, 'gl': 'us', 'hl': 'en'},
            headers={'X-API-KEY': SERPER_API_KEY, 'Content-Type': 'application/json'},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning('Serper funeral home discovery failed: %s', e)
        return []

    homes = []
    skip_domains = {'legacy.com', 'findagrave.com', 'ancestry.com', 'google.com',
                    'facebook.com', 'yelp.com', 'yellowpages.com', 'mapquest.com',
                    'whitepages.com', 'bbb.org', 'indeed.com', 'linkedin.com',
                    'wikipedia.org', 'dignitymemorial.com', 'tributearchive.com',
                    'tributewall.com', 'obits.com', 'legacy.com'}
    obit_signals = {'funeral', 'funeralhome', 'mortuary', 'cremation', 'obituar',
                    'memorial', 'chapel', 'burial'}

    seen = set()
    for item in data.get('organic', []):
        url = item.get('link', '')
        title = item.get('title', '')
        domain = re.sub(r'^https?://(www\.)?', '', url).split('/')[0].lower()

        if domain in skip_domains:
            continue
        if domain in seen:
            continue
        combined = (title + ' ' + url + ' ' + item.get('snippet', '')).lower()
        if not any(s in combined for s in obit_signals):
            continue

        seen.add(domain)
        homes.append({'name': title, 'url': f'https://{domain}', 'platform': _detect_platform(url)})

    # Also check knowledge graph sitelinks
    for sl in data.get('sitelinks', {}).get('sitelinks', []):
        u = sl.get('link', '')
        if u:
            domain = re.sub(r'^https?://(www\.)?', '', u).split('/')[0].lower()
            if domain not in seen and domain not in skip_domains:
                seen.add(domain)
                homes.append({'name': sl.get('title', ''), 'url': f'https://{domain}',
                               'platform': _detect_platform(u)})

    return homes[:8]


def _detect_platform(url: str) -> str:
    u = url.lower()
    if 'tributearchive.com' in u or 'tributewall.com' in u:
        return 'tributewall'
    if 'funeralhomepage.com' in u or 'fdlic.com' in u:
        return 'fdlic'
    if 'forevermissed.com' in u:
        return 'forevermissed'
    if 'obitmemorial.com' in u:
        return 'obitmemorial'
    return 'generic'


def get_or_discover_funeral_homes(county_key: str, county_name: str,
                                   state_abbr: str) -> list[dict]:
    """
    Return cached funeral homes for this county, or discover via Serper.
    Always returns a list of {name, url, platform}.
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            'SELECT name, url, platform FROM funeral_homes WHERE county_key = ?',
            (county_key,)
        ).fetchall()
        if rows:
            return [dict(r) for r in rows]

        # Not cached — discover now
        homes = _discover_funeral_homes_serper(county_name, state_abbr)
        if homes:
            now = int(time.time())
            conn.executemany(
                'INSERT OR IGNORE INTO funeral_homes (county_key, name, url, platform, discovered) '
                'VALUES (?, ?, ?, ?, ?)',
                [(county_key, h['name'], h['url'], h['platform'], now) for h in homes]
            )
            conn.execute(
                'INSERT OR REPLACE INTO county_cache (county_key, county_name, state_abbr, discovered) '
                'VALUES (?, ?, ?, ?)',
                (county_key, county_name, state_abbr, now)
            )
            conn.commit()
        return homes
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Generic obituary scraper
# ---------------------------------------------------------------------------

_OBIT_SELECTORS = [
    # Common CMS class names used by funeral home platforms
    '.obit-text', '.obituary-text', '.obit-bio', '.obituary-body',
    '.obit-content', '.obituary-content', '#obituary-text', '#obit-content',
    '.tribute-text', '.tribute-body', '.memorial-text',
    '.entry-content', '.post-content', '.article-body',
    # Broad fallbacks
    'article', 'main',
]


def _extract_obit_text(soup: BeautifulSoup, first: str, last: str) -> str | None:
    """
    Extract obituary body text from a parsed page.
    Returns text if person's name is found, else None.
    """
    name_lower = f'{first} {last}'.lower().strip()
    last_lower = last.lower().strip()

    for sel in _OBIT_SELECTORS:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(' ', strip=True)
            if last_lower in text.lower():
                return text[:2000]

    # Fall back: any <p> block containing the name
    for p in soup.find_all('p'):
        text = p.get_text(' ', strip=True)
        if last_lower in text.lower() and len(text) > 80:
            return text[:2000]

    return None


def _build_search_urls(base_url: str, first: str, last: str) -> list[str]:
    """
    Generate candidate obituary search URLs for a funeral home site.
    Covers the most common CMS URL patterns.
    """
    base = base_url.rstrip('/')
    slug_full = f'{first.lower()}-{last.lower()}'
    slug_last = last.lower()
    urls = [
        # Direct name slug (most common for single-person pages)
        f'{base}/obituaries/{slug_full}',
        f'{base}/obituary/{slug_full}',
        f'{base}/obits/{slug_full}',
        # Search endpoints
        f'{base}/obituaries?search={first}+{last}',
        f'{base}/obituaries?name={first}+{last}',
        f'{base}/obituaries?q={first}+{last}',
        f'{base}/obituaries?last={last}&first={first}',
        # Listing pages (we scan for the name)
        f'{base}/obituaries',
        f'{base}/obituaries/',
        f'{base}/recent-obituaries',
        f'{base}/current-obituaries',
    ]
    return urls


def _scrape_one_url(url: str, first: str, last: str,
                    birth_year: int = None) -> dict | None:
    """
    Fetch a single URL and try to extract an obituary for the given person.
    Returns result dict on success, None on failure.
    """
    try:
        resp = req_lib.get(url, headers=_HEADERS, timeout=10, allow_redirects=True)
        if resp.status_code not in (200, 301, 302):
            return None
        soup = BeautifulSoup(resp.text, 'html.parser')
        page_text = resp.text.lower()
        last_lower = last.lower()

        # Quick reject: page doesn't mention last name at all
        if last_lower not in page_text:
            return None

        text = _extract_obit_text(soup, first, last)
        if not text:
            return None

        # Year validation if we have one
        if birth_year and str(birth_year) not in text and str(birth_year) not in page_text:
            return None

        # Extract page title for confidence
        title_tag = soup.find('title')
        title = title_tag.get_text(strip=True) if title_tag else f'{first} {last}'

        # Check og:description for a clean snippet
        og_desc = soup.find('meta', property='og:description')
        snippet = og_desc['content'][:300] if og_desc and og_desc.get('content') else text[:300]

        return {
            'source': 'funeral_home',
            'record_type': 'obituary',
            'title': title,
            'snippet': snippet,
            'url': url,
            'name': f'{first} {last}',
            'full_text': text,
        }
    except Exception:
        return None


def scrape_funeral_home(home_url: str, first: str, last: str,
                         birth_year: int = None) -> list[dict]:
    """
    Try to find an obituary for first/last on a given funeral home site.
    Returns list of result dicts (usually 0 or 1).
    """
    results = []
    urls = _build_search_urls(home_url, first, last)
    seen = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        hit = _scrape_one_url(url, first, last, birth_year)
        if hit:
            results.append(hit)
            break  # found — no need to try more URL patterns
    return results


# ---------------------------------------------------------------------------
# Public entrypoint — called from search_cascade
# ---------------------------------------------------------------------------

def search_regional_funeral_homes(first: str, last: str, death_place: str,
                                    birth_year: int = None) -> list[dict]:
    """
    Main entry: resolve county from death_place, discover/fetch funeral homes,
    scrape each for the person. Returns merged results list.
    """
    if not death_place:
        return []

    resolved = resolve_death_place_to_county(death_place)
    if not resolved:
        logger.info('Could not resolve county from death_place: %r', death_place)
        return []

    county_key, county_name, state_abbr = resolved
    logger.info('Resolved %r → %s (%s)', death_place, county_key, county_name)

    homes = get_or_discover_funeral_homes(county_key, county_name, state_abbr)
    if not homes:
        logger.info('No funeral homes found for county %s', county_key)
        return []

    results = []
    for home in homes[:5]:  # cap at 5 homes per search
        try:
            hits = scrape_funeral_home(home['url'], first, last, birth_year)
            results.extend(hits)
            if results:
                break  # found the person — stop checking more homes
        except Exception as e:
            logger.debug('Funeral home scrape error %s: %s', home['url'], e)

    return results


def get_county_stats() -> dict:
    """Return counts for admin dashboard."""
    conn = _get_conn()
    try:
        counties = conn.execute('SELECT COUNT(*) FROM county_cache').fetchone()[0]
        homes    = conn.execute('SELECT COUNT(*) FROM funeral_homes').fetchone()[0]
        return {'counties_indexed': counties, 'funeral_homes_indexed': homes}
    finally:
        conn.close()
