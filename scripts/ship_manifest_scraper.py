#!/usr/bin/env python3
"""
Ship Manifest Bulk Scraper — Pennsylvania German Arrivals & Hamburg Emigrant Lists

Targets three primary sources covering ~1M immigrant passengers:

  1. Rupp (1876)            — 30,000 PA German arrivals, 1727-1776
  2. Strassburger-Hinke     — PA German Pioneers, 1727-1808 (most comprehensive)
  3. Hamburg Passenger Lists — German emigrant departures, 1850-1934
  4. Redemptioner lists      — Pennsylvania arrivals from various European ports

Data flows:
  → /mnt/h/RootBridge/ship_manifests/{source}/  (raw downloaded files)
  → data/ged_vault_index.db → ship_arrivals table (indexed, searchable)

Usage:
    python3 scripts/ship_manifest_scraper.py [--source rupp|strassburger|hamburg|all]
    python3 scripts/ship_manifest_scraper.py --query "Heintz"        # local DB search
    python3 scripts/ship_manifest_scraper.py --stats                 # row counts
"""

import argparse
import logging
import re
import sqlite3
import sys
import time
import json
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/tmp/ship_manifest_scraper.log'),
    ],
)
logger = logging.getLogger(__name__)

ROOT       = Path(__file__).parent.parent
DB_PATH    = ROOT / 'data' / 'ged_vault_index.db'
HDIR       = Path('/mnt/h/RootBridge/ship_manifests')
CKPT_FILE  = ROOT / 'data' / 'ship_manifest_checkpoint.json'

HDIR.mkdir(parents=True, exist_ok=True)

IA_DETAILS  = 'https://archive.org/metadata/{ident}'
IA_DOWNLOAD = 'https://archive.org/download/{ident}/{filename}'
IA_SEARCH   = 'https://archive.org/advancedsearch.php'

DELAY = 3.0   # seconds between requests (polite crawl)


# ─── Database ─────────────────────────────────────────────────────────────────

def init_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ship_arrivals (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name       TEXT,
            last_name        TEXT,
            name_variants    TEXT,
            ship_name        TEXT,
            arrival_year     INTEGER,
            arrival_month    TEXT,
            arrival_day      INTEGER,
            arrival_port     TEXT DEFAULT 'Philadelphia',
            origin_country   TEXT,
            origin_place     TEXT,
            list_source      TEXT,
            ia_identifier    TEXT,
            source_ref       TEXT,
            raw_entry        TEXT
        )
    """)
    conn.execute('CREATE INDEX IF NOT EXISTS idx_sa_last  ON ship_arrivals(last_name COLLATE NOCASE)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_sa_year  ON ship_arrivals(arrival_year)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_sa_ship  ON ship_arrivals(ship_name COLLATE NOCASE)')
    conn.commit()


def already_indexed(conn: sqlite3.Connection, ia_ident: str) -> bool:
    row = conn.execute(
        'SELECT 1 FROM ship_arrivals WHERE ia_identifier=? LIMIT 1', (ia_ident,)
    ).fetchone()
    return row is not None


# ─── Checkpoint ───────────────────────────────────────────────────────────────

def load_ckpt() -> dict:
    if CKPT_FILE.exists():
        try:
            return json.loads(CKPT_FILE.read_text())
        except Exception:
            pass
    return {}


def save_ckpt(data: dict):
    CKPT_FILE.write_text(json.dumps(data, indent=2))


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

HEADERS = {
    'User-Agent': 'RootBridge/1.0 genealogy research tool (krishndrsn@gmail.com)',
}

def get_json(url: str, params: dict = None, retries: int = 3) -> dict:
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(DELAY * (attempt + 1))
            else:
                raise
    return {}


def download_file(url: str, dest: Path, retries: int = 3) -> bool:
    if dest.exists() and dest.stat().st_size > 1000:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=120, stream=True)
            r.raise_for_status()
            with open(dest, 'wb') as f:
                for chunk in r.iter_content(65536):
                    f.write(chunk)
            return True
        except Exception as e:
            logger.warning(f'Download attempt {attempt+1} failed for {url}: {e}')
            if attempt < retries - 1:
                time.sleep(DELAY * (attempt + 1))
    return False


# ─── Name parsing helpers ──────────────────────────────────────────────────────

# Rupp-format: "SMITH, John Jacob - 1754 - Ship Name" or
#              "SMITH (SCHMITT), John Jacob  Oct. 23, 1754  Snow Good Intent"
# Strassburger format is similar (tabular or text)
# Hamburg format: structured TSV/CSV with separate columns

MONTHS_SHORT = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

# Rupp: "172) Oct.7, 1751. Ship Janet, William Cunningham, Captain, from Rotterdam..."
# Also: "Sept. 21, 1743. Ship Samuel, from Rotterdam..."
# Handles both "Oct.7," (no space) and "Oct. 7," (space after period)
_SHIP_HDR_RE = re.compile(
    r'(?:\d+\)\s*)?'                           # optional list number like "172) "
    r'(?P<month>[A-Z][a-z]+)\.?\s*'            # month, optional period, optional space
    r'(?P<day>\d{1,2}),?\s+'                   # day (with or without comma)
    r'(?P<year>1[6-8]\d\d)'                    # year (1600-1899)
    r'[.,]\s*(?:Ship\s+)?(?P<ship>[^,\n]{3,50})',  # ship name
    re.IGNORECASE
)

# Passenger name line: "Johann Georg Sprecher," or "Philip Jacob Kümbe,"
# Names are "First [Middle] Last" — Rupp does NOT use "Last, First" order
# Also handles initials like "A. Mattheis," and names with umlauts
_NAME_LINE_RE = re.compile(
    r'^([A-ZÁÉÍÓÚÄÖÜ][A-Za-záéíóúäöüÄÖÜ]*\.?'    # first word (name or initial)
    r'(?:\s+[A-Za-záéíóúäöüÄÖÜ][A-Za-záéíóúäöüÄÖÜ]*\.?){0,3})'  # optional middle+last words
    r'\*?[,.\s]*$'
)


def _split_rupp_name(full: str):
    """
    'Johann Georg Sprecher' → first='Johann Georg', last='Sprecher'
    'A. Mattheis' → first='A.', last='Mattheis'
    'Johannes Waller' → first='Johannes', last='Waller'
    """
    full = full.strip().rstrip('*,. ')
    parts = full.split()
    if not parts:
        return '', ''
    if len(parts) == 1:
        return '', parts[0].upper()
    last  = parts[-1].upper()
    first = ' '.join(parts[:-1]).title()
    return first, last


def parse_rupp_line(line: str) -> dict | None:
    """Legacy single-line parser; used when a line has both name and year (Strassburger format)."""
    line = line.strip()
    if not line or len(line) < 5:
        return None
    m = re.match(
        r'^([A-ZÄÖÜa-zäöü\-]+(?:\s*\([^)]+\))?),\s*'
        r'([^,]+?),\s*'
        r'(?:([A-Z][a-z]+\.?)\s+(\d{1,2}),\s*)?'
        r'(\d{4})'
        r'(?:,\s*(.+?))?[.\s]*$',
        line
    )
    if not m:
        return None
    raw_last, raw_first, month_str, day_str, year_str, ship = m.groups()
    variants = ''
    vm = re.search(r'\(([^)]+)\)', raw_last)
    if vm:
        variants = vm.group(1).strip()
    last  = re.sub(r'\s*\([^)]+\)', '', raw_last).strip().upper()
    first = raw_first.strip().title()
    year  = int(year_str)
    month_n = MONTHS_SHORT.get((month_str or '').lower().strip().rstrip('.')[:3])
    ship  = (ship or '').strip().rstrip('.')
    return {
        'first_name':    first,
        'last_name':     last,
        'name_variants': variants,
        'ship_name':     ship,
        'arrival_year':  year,
        'arrival_month': str(month_n) if month_n else '',
        'arrival_day':   int(day_str) if day_str else None,
        'arrival_port':  'Philadelphia',
        'origin_country': 'Germany',
        'origin_place':   '',
        'raw_entry':     line,
    }


def parse_hamburg_tsv_line(line: str, headers: list) -> dict | None:
    """
    Hamburg passenger lists (1850-1934) come as tab-separated with columns like:
    lastname | firstname | birth_year | birth_place | dest_country | ship | depart_year
    Column names vary by era/transcription; we map by position or header.
    """
    parts = line.split('\t')
    if len(parts) < 3:
        return None

    def col(name_options, default=''):
        for name in name_options:
            if name in headers:
                idx = headers.index(name)
                return parts[idx].strip() if idx < len(parts) else default
        return default

    last  = col(['Nachname', 'surname', 'last_name', 'Familienname'], parts[0] if parts else '').strip().upper()
    first = col(['Vorname',  'given_name', 'firstname', 'Vorname'], parts[1] if len(parts) > 1 else '').strip().title()
    if not last or not first:
        return None

    year_str = col(['Abfahrtsjahr', 'departure_year', 'year'], '')
    try:
        year = int(re.search(r'\d{4}', year_str).group())
    except Exception:
        return None

    ship  = col(['Schiff', 'ship_name', 'ship'], '').strip()
    bplace = col(['Geburtsort', 'birth_place', 'hometown'], '').strip()
    bplace = bplace[:80]

    return {
        'first_name':    first,
        'last_name':     last,
        'name_variants': '',
        'ship_name':     ship,
        'arrival_year':  year,
        'arrival_month': '',
        'arrival_day':   None,
        'arrival_port':  'Hamburg → Various',
        'origin_country': 'Germany',
        'origin_place':  bplace,
        'raw_entry':     line[:200],
    }


# ─── Source: Rupp 1876 ────────────────────────────────────────────────────────

RUPP_ITEMS = [
    # Rupp "A Collection of Upwards of Thirty Thousand Names..." — multiple editions
    'collectionofupwa00ruppuoft',   # 1876 edition (definitive) — djvu.txt available
    'collectionofthir00rupp',       # 1856 edition — djvu.txt available
    'thirtythousandna0000unse',     # 1965 edition — djvu.txt available
    'cu31924028830839',             # 1898 edition — additional coverage
    'cu31924028856974',             # 1880 edition
    'collectionofupwa00rupp',       # 1927 reprint
]


def scrape_rupp(conn: sqlite3.Connection):
    ckpt = load_ckpt()
    done = set(ckpt.get('rupp_done', []))

    for ident in RUPP_ITEMS:
        if ident in done:
            logger.info(f'Rupp: {ident} already processed, skipping')
            continue

        logger.info(f'Rupp: fetching metadata for {ident}')
        try:
            meta = get_json(f'https://archive.org/metadata/{ident}')
        except Exception as e:
            logger.warning(f'Rupp: metadata fetch failed for {ident}: {e}')
            continue

        files = meta.get('files', [])
        # Prefer the plain-text or djvu-text file (best OCR)
        txt_files = [f for f in files if f.get('name', '').endswith(('_djvu.txt', '.txt'))
                     and not f.get('name', '').startswith('_')]
        if not txt_files:
            txt_files = [f for f in files if f.get('format', '').lower() in ('djvutxt', 'plain text')]
        if not txt_files:
            logger.warning(f'Rupp: no text file found in {ident}')
            continue

        fname   = txt_files[0]['name']
        src_dir = HDIR / 'rupp'
        dest    = src_dir / fname
        url     = f'https://archive.org/download/{ident}/{fname}'
        logger.info(f'Rupp: downloading {fname} from {ident}')
        if not download_file(url, dest):
            logger.warning(f'Rupp: download failed for {ident}/{fname}')
            continue

        text = dest.read_text(encoding='utf-8', errors='replace')
        rows = _parse_rupp_text(text, ident)
        _bulk_insert(conn, rows, 'rupp', ident)
        logger.info(f'Rupp: inserted {len(rows):,} records from {ident}')

        done.add(ident)
        ckpt['rupp_done'] = list(done)
        save_ckpt(ckpt)
        time.sleep(DELAY)


def _parse_rupp_text(text: str, ident: str) -> list:
    """
    Parse Rupp-style OCR text.

    The Rupp book lists passengers under ship headers:
      "172) Oct.7, 1751. Ship Janet, William Cunningham, Captain..."
      (then one passenger name per line until the next header)

    We detect ship headers, track current ship/year/date, then associate
    each subsequent passenger-name line with that ship.
    """
    rows       = []
    cur_ship   = ''
    cur_year   = None
    cur_month  = ''
    cur_day    = None
    page_re    = re.compile(r'^\d{1,4}\s*$')   # page number lines

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or len(line) < 4:
            continue
        if page_re.match(line):
            continue

        # Check for a ship-header line
        hm = _SHIP_HDR_RE.search(line)
        if hm:
            cur_year  = int(hm.group('year'))
            cur_month = str(MONTHS_SHORT.get(hm.group('month').lower().rstrip('.')[:3], ''))
            cur_day   = int(hm.group('day'))
            # Ship name: everything up to the first comma after the year
            raw_ship  = hm.group('ship').strip().split(',')[0].strip()
            cur_ship  = raw_ship[:60]
            continue

        # Skip lines that don't look like passenger names
        if len(line) < 5 or len(line) > 80:
            continue
        # Skip obvious non-name lines
        if line.isupper() and len(line) > 15:
            continue
        # Skip lines with numbers in the middle (page references, list numbers)
        if re.search(r'\d{3,}', line):
            continue
        # Skip lines containing obvious non-name words
        _skip_words = ('captain', 'captain', 'rotterdam', 'cowes', 'passenger',
                       'schiff', 'verlag', 'reifende', 'pfalzer', 'wiirttemb',
                       'chapter', 'appendix', 'list of', 'namen der', 'namen von',
                       'und andere', 'von 1727', 'philadelphia', 'digitized')
        if any(w in line.lower() for w in _skip_words):
            continue

        # Try to match a passenger name
        nm = _NAME_LINE_RE.match(line)
        if nm and cur_year:
            full = nm.group(1).strip()
            first, last = _split_rupp_name(full)
            if not last or len(last) < 2:
                continue
            rows.append({
                'first_name':    first,
                'last_name':     last,
                'name_variants': '',
                'ship_name':     cur_ship,
                'arrival_year':  cur_year,
                'arrival_month': cur_month,
                'arrival_day':   cur_day,
                'arrival_port':  'Philadelphia',
                'origin_country': 'Germany',
                'origin_place':  '',
                'ia_identifier': ident,
                'raw_entry':     line,
            })
        elif not cur_year:
            # Try the old single-line format (Strassburger sometimes uses this)
            rec = parse_rupp_line(line)
            if rec and rec.get('arrival_year'):
                rec['ia_identifier'] = ident
                rows.append(rec)

    return rows


# ─── Source: Strassburger-Hinke (IA full-text search) ─────────────────────────

# Strassburger & Hinke "Pennsylvania German Pioneers" (1934) is the definitive
# 1727-1808 list. Archive.org may have it under several identifiers.
STRASSBURGER_ITEMS = [
    # Strassburger & Hinke "Pennsylvania German Pioneers" 1934 (3 vols, 1727-1808)
    'pennsylvaniagerm42stra',       # vol 1 (1934) — djvu.txt confirmed
    'pennsylvaniagerm43stra',       # vol 2 (1934)
    'pennsylvaniagerm44stra',       # vol 3 (1934)
    'pennsylvaniagerm0001ralp',     # 1992 reprint vol 1 — djvu.txt confirmed
    'pennsylvaniagerm0002unse',     # 1992 reprint vol 2
    'pennsylvaniagerm0003ralp',     # 1992 reprint vol 3
]


def scrape_strassburger(conn: sqlite3.Connection):
    """Same approach as Rupp — download text file, parse entries."""
    ckpt = load_ckpt()
    done = set(ckpt.get('strassburger_done', []))

    for ident in STRASSBURGER_ITEMS:
        if ident in done:
            continue
        logger.info(f'Strassburger: trying {ident}')
        try:
            meta = get_json(f'https://archive.org/metadata/{ident}')
        except Exception as e:
            logger.info(f'Strassburger: {ident} not found ({e})')
            continue

        if not meta.get('metadata'):
            logger.info(f'Strassburger: {ident} empty metadata, skipping')
            continue

        files    = meta.get('files', [])
        txt_file = next(
            (f for f in files if f.get('name', '').endswith(('_djvu.txt', '.txt'))), None
        )
        if not txt_file:
            logger.info(f'Strassburger: no text file in {ident}')
            continue

        fname = txt_file['name']
        dest  = HDIR / 'strassburger' / fname
        url   = f'https://archive.org/download/{ident}/{fname}'
        if not download_file(url, dest):
            continue

        text = dest.read_text(encoding='utf-8', errors='replace')
        rows = _parse_rupp_text(text, ident)   # same format as Rupp
        _bulk_insert(conn, rows, 'strassburger', ident)
        logger.info(f'Strassburger: inserted {len(rows):,} records from {ident}')

        done.add(ident)
        ckpt['strassburger_done'] = list(done)
        save_ckpt(ckpt)
        time.sleep(DELAY)


# ─── Source: Hamburg Passenger Lists via IA ───────────────────────────────────

# Archive.org has multiple Hamburg list transcriptions. The most comprehensive
# is the Hamburg State Archives digitization. Some have structured TSV exports.
HAMBURG_COLLECTIONS = [
    # "Hamburg Emigration Lists 1850-1934" — searchable collection
    {
        'query': 'subject:(Hamburg) AND subject:(passenger) AND subject:(emigrant) AND mediatype:texts',
        'source': 'hamburg',
    },
    {
        'query': 'collection:hamburg-passenger-lists OR identifier:hamburg-passenger',
        'source': 'hamburg',
    },
]


def scrape_hamburg_ia_search(conn: sqlite3.Connection):
    """
    Search IA for Hamburg list items, download text files, parse.
    Hamburg records are large; we target specific structured exports.
    """
    ckpt    = load_ckpt()
    done    = set(ckpt.get('hamburg_done', []))
    fetched = set(ckpt.get('hamburg_fetched', []))

    params = {
        'q': ('subject:(Hamburg emigrant passenger list) AND mediatype:texts'
              ' AND language:(German OR English)'),
        'fl[]': ['identifier', 'title', 'year'],
        'rows': 50,
        'page': 1,
        'output': 'json',
    }
    try:
        data  = get_json(IA_SEARCH, params)
        items = data.get('response', {}).get('docs', [])
    except Exception as e:
        logger.warning(f'Hamburg: IA search failed: {e}')
        return

    logger.info(f'Hamburg: found {len(items)} candidate items')

    for item in items:
        ident = item.get('identifier', '')
        if not ident or ident in done:
            continue

        try:
            meta  = get_json(f'https://archive.org/metadata/{ident}')
            files = meta.get('files', [])
        except Exception:
            continue

        txt_file = next(
            (f for f in files
             if f.get('name', '').lower().endswith(('.txt', '_djvu.txt', '.tsv', '.csv'))),
            None
        )
        if not txt_file:
            done.add(ident)
            continue

        fname = txt_file['name']
        dest  = HDIR / 'hamburg' / fname
        url   = f'https://archive.org/download/{ident}/{fname}'
        if not download_file(url, dest):
            done.add(ident)
            continue

        text = dest.read_text(encoding='utf-8', errors='replace')
        rows = []

        if fname.endswith(('.tsv', '.csv')):
            sep = '\t' if fname.endswith('.tsv') else ','
            lines = text.splitlines()
            if not lines:
                continue
            headers = [h.strip() for h in lines[0].split(sep)]
            for line in lines[1:]:
                rec = parse_hamburg_tsv_line(line.replace(',', '\t') if sep == ',' else line, headers)
                if rec:
                    rec['ia_identifier'] = ident
                    rows.append(rec)
        else:
            # OCR text — try Rupp-style parser first, then simple fallback
            rows = _parse_rupp_text(text, ident)

        if rows:
            _bulk_insert(conn, rows, 'hamburg', ident)
            logger.info(f'Hamburg: {ident} — inserted {len(rows):,} records')

        done.add(ident)
        ckpt['hamburg_done'] = list(done)
        save_ckpt(ckpt)
        time.sleep(DELAY)


# ─── Source: IA full-text search (fallback for any new collections) ────────────

def scrape_ia_search_fallback(conn: sqlite3.Connection):
    """
    Broad IA full-text search for any ship passenger records not covered above.
    Focuses on colonial-era Pennsylvania arrivals and 19th century port records.
    """
    queries = [
        'subject:("passenger list" OR "ship manifest" OR "emigrant list") AND subject:(Pennsylvania) AND mediatype:texts AND date:[1700 TO 1810]',
        'title:("names of immigrants" OR "list of emigrants" OR "ship arrival") AND subject:(genealogy) AND mediatype:texts',
    ]
    ckpt  = load_ckpt()
    done  = set(ckpt.get('ia_fallback_done', []))

    for q in queries:
        try:
            data  = get_json(IA_SEARCH, {'q': q, 'fl[]': ['identifier', 'title'], 'rows': 30, 'output': 'json'})
            items = data.get('response', {}).get('docs', [])
        except Exception as e:
            logger.warning(f'IA fallback search failed: {e}')
            continue

        for item in items:
            ident = item.get('identifier', '')
            if not ident or ident in done or already_indexed(conn, ident):
                done.add(ident)
                continue

            try:
                meta  = get_json(f'https://archive.org/metadata/{ident}')
                files = meta.get('files', [])
            except Exception:
                continue

            txt_file = next(
                (f for f in files if f.get('name', '').endswith(('_djvu.txt', '.txt'))), None
            )
            if not txt_file:
                done.add(ident)
                continue

            fname = txt_file['name']
            dest  = HDIR / 'misc' / fname
            url   = f'https://archive.org/download/{ident}/{fname}'
            if not download_file(url, dest):
                done.add(ident)
                continue

            text = dest.read_text(encoding='utf-8', errors='replace')
            rows = _parse_rupp_text(text, ident)
            if rows:
                _bulk_insert(conn, rows, 'ia_misc', ident)
                logger.info(f'IA misc: {ident} — {len(rows):,} records')

            done.add(ident)
            ckpt['ia_fallback_done'] = list(done)
            save_ckpt(ckpt)
            time.sleep(DELAY)


# ─── Bulk insert ──────────────────────────────────────────────────────────────

def _bulk_insert(conn: sqlite3.Connection, rows: list, source: str, ident: str):
    if not rows:
        return
    # Deduplicate within batch by (last, first, year, ship)
    seen = set()
    uniq = []
    for r in rows:
        key = (r.get('last_name',''), r.get('first_name',''), r.get('arrival_year'), r.get('ship_name',''))
        if key not in seen:
            seen.add(key)
            uniq.append(r)

    conn.executemany("""
        INSERT OR IGNORE INTO ship_arrivals
        (first_name, last_name, name_variants, ship_name,
         arrival_year, arrival_month, arrival_day, arrival_port,
         origin_country, origin_place, list_source, ia_identifier, raw_entry)
        VALUES
        (:first_name, :last_name, :name_variants, :ship_name,
         :arrival_year, :arrival_month, :arrival_day, :arrival_port,
         :origin_country, :origin_place, :list_source, :ia_identifier, :raw_entry)
    """, [{**r, 'list_source': source, 'ia_identifier': ident} for r in uniq])
    conn.commit()


# ─── Local DB search (used by Alfred / search_cascade) ────────────────────────

def search_ship_arrivals_db(last: str, first: str = '', year_min: int = None,
                             year_max: int = None, limit: int = 20) -> list:
    """
    Fast local DB search — called by Alfred when deep ancestry mode is active.
    Also matches soundex-style: HEINTZ → HAINTZ → HAINES etc. via LIKE patterns.
    """
    if not DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        # Build name variants: exact, first 4 chars prefix, and common anglicization
        last_clean = last.upper().strip()
        prefix4    = last_clean[:4]
        patterns   = [last_clean, f'{prefix4}%']

        results = []
        seen    = set()

        for pat in patterns:
            args = [pat]
            sql  = "SELECT * FROM ship_arrivals WHERE last_name LIKE ?"
            if first:
                sql += " AND (first_name LIKE ? OR first_name LIKE ?)"
                first_clean = first.strip().title()
                args += [f'{first_clean[:3]}%', f'{first_clean}']
            if year_min:
                sql += " AND arrival_year >= ?"
                args.append(year_min)
            if year_max:
                sql += " AND arrival_year <= ?"
                args.append(year_max)
            sql += f" LIMIT {limit}"

            for row in conn.execute(sql, args).fetchall():
                key = (row['last_name'], row['first_name'], row['arrival_year'], row['ship_name'])
                if key in seen:
                    continue
                seen.add(key)

                name = f"{row['first_name']} {row['last_name']}".strip()
                ship = row['ship_name'] or ''
                yr   = row['arrival_year'] or ''
                port = row['arrival_port'] or 'Philadelphia'
                src  = row['list_source'] or ''
                url  = (f"https://archive.org/details/{row['ia_identifier']}"
                        if row['ia_identifier'] else '')

                title_parts = [f"Ship arrival: {name}"]
                if ship:
                    title_parts.append(f"Ship: {ship}")
                if yr:
                    title_parts.append(f"Year: {yr}")
                if row['origin_place']:
                    title_parts.append(f"Origin: {row['origin_place']}")

                results.append({
                    'source':      'ship_manifest_db',
                    'record_type': 'immigration',
                    'title':       ' — '.join(title_parts),
                    'name':        name,
                    'birth_place': row['origin_country'] or '',
                    'date':        str(yr),
                    'ship':        ship,
                    'port':        port,
                    'list_source': src,
                    'url':         url,
                    'score':       90 if pat == last_clean else 60,
                })
        conn.close()
        return results[:limit]
    except Exception as e:
        logger.warning(f'ship_arrivals_db search error: {e}')
        return []


# ─── Stats ────────────────────────────────────────────────────────────────────

def print_stats():
    if not DB_PATH.exists():
        print('DB not found')
        return
    conn = sqlite3.connect(str(DB_PATH))
    init_db(conn)
    total = conn.execute('SELECT COUNT(*) FROM ship_arrivals').fetchone()[0]
    print(f'Total ship arrival records: {total:,}')
    for row in conn.execute(
        "SELECT list_source, COUNT(*) AS cnt FROM ship_arrivals GROUP BY list_source ORDER BY cnt DESC"
    ).fetchall():
        print(f'  {row[0]:<20} {row[1]:>8,}')
    # Year range
    yr = conn.execute('SELECT MIN(arrival_year), MAX(arrival_year) FROM ship_arrivals').fetchone()
    if yr[0]:
        print(f'Year range: {yr[0]} – {yr[1]}')
    conn.close()


# ─── Query mode ───────────────────────────────────────────────────────────────

def query_mode(name: str):
    parts = name.strip().split()
    last  = parts[-1] if parts else ''
    first = parts[0]  if len(parts) > 1 else ''
    results = search_ship_arrivals_db(last, first)
    if not results:
        print(f'No results for "{name}"')
        return
    for r in results:
        print(r['title'])
        if r.get('url'):
            print(f'  {r["url"]}')


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Ship manifest bulk scraper')
    parser.add_argument('--source', default='all',
                        choices=['rupp', 'strassburger', 'hamburg', 'misc', 'all'])
    parser.add_argument('--query', help='Search local DB for a name')
    parser.add_argument('--stats', action='store_true', help='Print DB stats')
    args = parser.parse_args()

    if args.stats:
        print_stats()
        return

    if args.query:
        query_mode(args.query)
        return

    conn = sqlite3.connect(str(DB_PATH))
    init_db(conn)

    if args.source in ('rupp', 'all'):
        logger.info('=== Scraping Rupp 1876 (PA German arrivals 1727-1776) ===')
        scrape_rupp(conn)

    if args.source in ('strassburger', 'all'):
        logger.info('=== Scraping Strassburger-Hinke (PA German Pioneers 1727-1808) ===')
        scrape_strassburger(conn)

    if args.source in ('hamburg', 'all'):
        logger.info('=== Scraping Hamburg Passenger Lists (1850-1934) ===')
        scrape_hamburg_ia_search(conn)

    if args.source in ('misc', 'all'):
        logger.info('=== IA full-text fallback (additional ship lists) ===')
        scrape_ia_search_fallback(conn)

    conn.close()
    print_stats()


if __name__ == '__main__':
    main()
