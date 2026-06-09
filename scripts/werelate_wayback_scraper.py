#!/usr/bin/env python3
"""
Scrape WeRelate Person pages from the Wayback Machine.

WeRelate is Cloudflare-blocked on the live site. The Wayback Machine has ~325,000
archived Person pages (65 CDX pages × ~5,000 URLs) with no access restrictions.

Strategy:
  1. Paginate CDX API to collect all unique Person page URLs
  2. For each URL, fetch the most recent Wayback snapshot
  3. Parse birth/death year+place, first/last name from the HTML table
  4. Insert into ged_vault_index.db (werelate_persons table)
  5. Checkpoint after every batch to survive restarts

Usage:
  python3 scripts/werelate_wayback_scraper.py [--cdx-only] [--resume]

Resume: automatic — reads checkpoint from data/werelate_checkpoint.json
"""

import argparse
import json
import logging
import random
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import unquote

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/tmp/werelate_scraper.log'),
    ],
)
logger = logging.getLogger(__name__)

# ── paths ──────────────────────────────────────────────────────────────────
ROOT       = Path(__file__).parent.parent
DB_PATH    = ROOT / 'data' / 'ged_vault_index.db'
CKPT_FILE  = ROOT / 'data' / 'werelate_checkpoint.json'

# ── timing ─────────────────────────────────────────────────────────────────
DELAY_MIN  = 3.0
DELAY_MAX  = 6.0
CDX_DELAY  = 8.0   # longer delay between CDX pagination calls
TIMEOUT    = 30
MAX_RETRY  = 3

# ── user agents ────────────────────────────────────────────────────────────
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0',
]

CDX_URL = 'https://web.archive.org/cdx/search/cdx'

# Month name → number
MONTHS = {
    'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
    'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12,
}


# ── database ───────────────────────────────────────────────────────────────

def init_db(db: sqlite3.Connection):
    db.execute("""
        CREATE TABLE IF NOT EXISTS werelate_persons (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name  TEXT,
            last_name   TEXT,
            birth_year  INTEGER,
            death_year  INTEGER,
            birth_place TEXT,
            death_place TEXT,
            gender      TEXT,
            page_title  TEXT UNIQUE,
            wayback_url TEXT
        )
    """)
    db.execute('CREATE INDEX IF NOT EXISTS idx_wr_last ON werelate_persons(last_name)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_wr_birth ON werelate_persons(birth_year)')
    db.commit()


def insert_person(db: sqlite3.Connection, rec: dict) -> bool:
    try:
        db.execute("""
            INSERT OR IGNORE INTO werelate_persons
              (first_name, last_name, birth_year, death_year,
               birth_place, death_place, gender, page_title, wayback_url)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            rec.get('first_name',''), rec.get('last_name',''),
            rec.get('birth_year'), rec.get('death_year'),
            rec.get('birth_place',''), rec.get('death_place',''),
            rec.get('gender',''), rec.get('page_title',''), rec.get('wayback_url',''),
        ))
        db.commit()
        return db.execute('SELECT changes()').fetchone()[0] == 1
    except Exception as e:
        logger.warning('DB insert failed: %s', e)
        return False


# ── parsing ────────────────────────────────────────────────────────────────

def parse_year(s: str) -> int | None:
    """Extract 4-digit year from a date string like '9 Apr 1498' or '1550'."""
    if not s:
        return None
    m = re.search(r'\b(1[0-9]{3}|20[0-2][0-9])\b', s)
    return int(m.group(1)) if m else None


def parse_name_from_title(raw_title: str) -> tuple[str, str]:
    """
    WeRelate title format: 'Person:FirstName LastName (N)' or URL-encoded.
    Returns (first_name, last_name).
    """
    title = unquote(raw_title).replace('_', ' ')
    # Strip 'Person:' prefix
    title = re.sub(r'^Person:', '', title, flags=re.I).strip()
    # Strip disambiguator (1), (2) etc
    title = re.sub(r'\s*\(\d+\)\s*$', '', title).strip()
    # Strip ' - Genealogy' suffix if present
    title = re.sub(r'\s*-\s*Genealogy\s*$', '', title, flags=re.I).strip()

    # Split on space — last token = last name, rest = first name
    # For 'John, Cardinal of Lorraine' — use comma split
    if ',' in title:
        parts = title.split(',', 1)
        first = parts[0].strip()
        last  = ''  # title/nobility — no clean last name
    else:
        tokens = title.split()
        if len(tokens) >= 2:
            first = ' '.join(tokens[:-1])
            last  = tokens[-1]
        elif len(tokens) == 1:
            first = tokens[0]
            last  = ''
        else:
            first = last = ''

    return first.strip(), last.strip().upper()


def parse_person_page(html: str, page_title: str, wayback_url: str) -> dict | None:
    """
    Parse a WeRelate Person page HTML.
    Data lives in the first <table>: 'b. DD Mon YYYY in Place' and 'd. ...'
    """
    rec = {'page_title': page_title, 'wayback_url': wayback_url}

    # Extract title from <title> tag if available
    title_m = re.search(r'<title>([^<]+)</title>', html, re.I)
    display_title = title_m.group(1).strip() if title_m else page_title

    first, last = parse_name_from_title(display_title)
    rec['first_name'] = first
    rec['last_name']  = last

    if not first and not last:
        return None

    # Extract text from the first table (person facts table)
    tbl_m = re.search(r'<table[^>]*>(.*?)</table>', html, re.DOTALL | re.I)
    if not tbl_m:
        return rec

    table_text = re.sub(r'<[^>]+>', ' ', tbl_m.group(1))
    table_text = re.sub(r'\s+', ' ', table_text).strip()

    # Birth: 'b. 9 Apr 1498 in Nancy' or 'b. 1498'
    b_m = re.search(r'\bb\.?\s+([^.d]+?)(?:\s+d\.|\s+Family|\s+Parents|$)', table_text, re.I)
    if b_m:
        birth_str = b_m.group(1).strip()
        rec['birth_year'] = parse_year(birth_str)
        # Place: text after 'in '
        place_m = re.search(r'\bin\s+(.+)', birth_str, re.I)
        rec['birth_place'] = place_m.group(1).strip()[:100] if place_m else ''
    else:
        # Try standalone year patterns like 'born 1498'
        born_m = re.search(r'born\s+(\d{4})', table_text, re.I)
        if born_m:
            rec['birth_year'] = int(born_m.group(1))

    # Death: 'd. 18 May 1550 in ...'
    d_m = re.search(r'\bd\.?\s+([^.F]+?)(?:\s+Family|\s+Parents|$)', table_text, re.I)
    if d_m:
        death_str = d_m.group(1).strip()
        rec['death_year'] = parse_year(death_str)
        place_m = re.search(r'\bin\s+(.+)', death_str, re.I)
        rec['death_place'] = place_m.group(1).strip()[:100] if place_m else ''
    else:
        died_m = re.search(r'died\s+(\d{4})', table_text, re.I)
        if died_m:
            rec['death_year'] = int(died_m.group(1))

    # Gender heuristic from page content
    if re.search(r'\bwife\b|\bmother\b|\bdaughter\b', table_text, re.I):
        rec['gender'] = 'F'
    elif re.search(r'\bhusband\b|\bfather\b|\bson\b', table_text, re.I):
        rec['gender'] = 'M'

    return rec


# ── CDX pagination ─────────────────────────────────────────────────────────

def fetch_cdx_page(session: requests.Session, page: int) -> list[tuple[str, str]]:
    """Fetch one CDX page of WeRelate Person URLs. Returns [(timestamp, url), ...]."""
    for attempt in range(MAX_RETRY):
        try:
            r = session.get(CDX_URL, params={
                'url': 'werelate.org/wiki/Person:*',
                'output': 'json',
                'limit': 5000,
                'page': page,
                'filter': 'statuscode:200',
                'collapse': 'urlkey',
                'fl': 'timestamp,original',
            }, timeout=45)
            if r.status_code == 200:
                data = json.loads(r.text)
                rows = data[1:]  # skip header
                # Filter out placeholder/empty titles
                valid = [
                    (row[0], row[1]) for row in rows
                    if row[1] and 'Person:' in row[1]
                    and not row[1].endswith('Person:')
                    and '$' not in row[1]
                    and len(unquote(row[1]).split('Person:')[-1].strip()) > 2
                ]
                return valid
            logger.warning('CDX page %d HTTP %d, retry %d', page, r.status_code, attempt+1)
        except Exception as e:
            logger.warning('CDX page %d error: %s, retry %d', page, e, attempt+1)
        time.sleep(CDX_DELAY * (attempt + 1))
    return []


def count_cdx_pages(session: requests.Session) -> int:
    for attempt in range(MAX_RETRY):
        try:
            r = session.get(CDX_URL, params={
                'url': 'werelate.org/wiki/Person:*',
                'output': 'json',
                'filter': 'statuscode:200',
                'collapse': 'urlkey',
                'showNumPages': 'true',
            }, timeout=45)
            if r.status_code == 200:
                data = json.loads(r.text)
                return int(data[1][0])
        except Exception as e:
            logger.warning('CDX page count error: %s', e)
        time.sleep(CDX_DELAY)
    return 65  # fallback


# ── person page fetcher ────────────────────────────────────────────────────

def fetch_person_page(session: requests.Session, ts: str, url: str) -> str | None:
    wayback = f'https://web.archive.org/web/{ts}/{url}'
    for attempt in range(MAX_RETRY):
        try:
            r = session.get(wayback, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.text
            if r.status_code == 404:
                return None
            logger.debug('Person page HTTP %d for %s', r.status_code, url)
        except Exception as e:
            logger.debug('Person page error %s: %s', url, e)
        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
    return None


# ── checkpoint ────────────────────────────────────────────────────────────

def load_checkpoint() -> dict:
    if CKPT_FILE.exists():
        return json.loads(CKPT_FILE.read_text())
    return {'cdx_pages_done': [], 'total_inserted': 0, 'total_seen': 0}


def save_checkpoint(ckpt: dict):
    CKPT_FILE.write_text(json.dumps(ckpt, indent=2))


# ── main ──────────────────────────────────────────────────────────────────

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers['User-Agent'] = random.choice(USER_AGENTS)
    return s


def run(cdx_only: bool = False):
    db = sqlite3.connect(str(DB_PATH))
    init_db(db)

    session   = make_session()
    ckpt      = load_checkpoint()
    pages_done = set(ckpt.get('cdx_pages_done', []))
    inserted   = ckpt.get('total_inserted', 0)
    seen       = ckpt.get('total_seen', 0)

    total_pages = count_cdx_pages(session)
    logger.info('WeRelate CDX: %d total pages to process (%d already done)',
                total_pages, len(pages_done))

    for page in range(total_pages):
        if page in pages_done:
            continue

        logger.info('CDX page %d / %d', page, total_pages - 1)
        time.sleep(CDX_DELAY + random.uniform(0, 3))

        urls = fetch_cdx_page(session, page)
        logger.info('  Got %d valid URLs', len(urls))

        if cdx_only:
            pages_done.add(page)
            ckpt['cdx_pages_done'] = list(pages_done)
            save_checkpoint(ckpt)
            continue

        page_inserted = 0
        for ts, url in urls:
            seen += 1
            # Rotate UA every 50 requests
            if seen % 50 == 0:
                session.headers['User-Agent'] = random.choice(USER_AGENTS)

            html = fetch_person_page(session, ts, url)
            if not html:
                time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
                continue

            page_title = unquote(url.split('Person:')[-1]) if 'Person:' in url else url
            wayback_url = f'https://web.archive.org/web/{ts}/{url}'

            rec = parse_person_page(html, page_title, wayback_url)
            if rec and (rec.get('first_name') or rec.get('last_name')):
                if insert_person(db, rec):
                    inserted += 1
                    page_inserted += 1

            delay = random.uniform(DELAY_MIN, DELAY_MAX)
            # Jitter ±30%
            delay *= random.uniform(0.7, 1.3)
            time.sleep(delay)

        pages_done.add(page)
        ckpt['cdx_pages_done'] = list(pages_done)
        ckpt['total_inserted'] = inserted
        ckpt['total_seen']     = seen
        save_checkpoint(ckpt)

        logger.info('  Page %d done: +%d inserted (total %d / %d seen)',
                    page, page_inserted, inserted, seen)

    db.close()
    logger.info('DONE: %d persons inserted from WeRelate Wayback', inserted)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--cdx-only', action='store_true', help='Only enumerate CDX URLs, do not fetch pages')
    ap.add_argument('--resume',   action='store_true', help='(default) Resume from checkpoint')
    args = ap.parse_args()
    run(cdx_only=args.cdx_only)
