#!/usr/bin/env python3
"""
Scrape Open Archives NL (openarch.nl) — Dutch civil records going back centuries.

API requires a name search. Strategy: iterate all 676 two-letter substring combos
(AA through ZZ), paginate each, deduplicate by `identifier` field.

Records include births, marriages, deaths, passenger lists from Dutch archives.
Covers Rotterdam, Amsterdam, and all major Dutch cities/provinces.

Output: /mnt/h/RootBridge/european/openarch/{PREFIX}.jsonl
Also inserts into data/ged_vault_index.db → european_persons table.

Usage:
    python3 scripts/openarch_scraper.py [--resume]
"""

import json
import logging
import sqlite3
import string
import time
import random
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/tmp/openarch_scraper.log'),
    ],
)
logger = logging.getLogger(__name__)

ROOT      = Path(__file__).parent.parent
DB_PATH   = ROOT / 'data' / 'ged_vault_index.db'
HDIR      = Path('/mnt/h/RootBridge/european/openarch')
CKPT_FILE = ROOT / 'data' / 'openarch_checkpoint.json'
SEEN_FILE = ROOT / 'data' / 'openarch_seen.json'   # deduplicate by identifier
HDIR.mkdir(parents=True, exist_ok=True)

API_URL   = 'https://api.openarch.nl/1.1/records/search.json'
PAGE_SIZE = 10       # API max
DELAY_MIN = 2.5
DELAY_MAX = 5.0
TIMEOUT   = 20
MAX_RETRY = 3

LETTERS = string.ascii_uppercase  # A-Z


def init_db(db: sqlite3.Connection):
    db.execute("""
        CREATE TABLE IF NOT EXISTS european_persons (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name  TEXT,
            last_name   TEXT,
            birth_year  INTEGER,
            death_year  INTEGER,
            birth_place TEXT,
            death_place TEXT,
            gender      TEXT,
            country     TEXT,
            source      TEXT,
            source_id   TEXT UNIQUE
        )
    """)
    db.execute('CREATE INDEX IF NOT EXISTS idx_eu_last  ON european_persons(last_name)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_eu_birth ON european_persons(birth_year)')
    db.commit()


def parse_name(raw: str):
    """Split 'FirstName LastName' or '@.C. Henderson' into (first, last)."""
    s = raw.strip().lstrip('@').strip()
    parts = s.split()
    if len(parts) >= 2:
        return ' '.join(parts[:-1]), parts[-1].upper()
    return s, ''


def record_to_db(rec: dict) -> dict:
    name = rec.get('personname', '')
    first, last = parse_name(name)
    event = rec.get('_eventtype', rec.get('eventtype', ''))
    date  = rec.get('eventdate', {}) or {}
    year  = date.get('year') if isinstance(date, dict) else None
    place = rec.get('eventplace', [])
    place_str = ', '.join(place) if isinstance(place, list) else str(place or '')

    birth_year = death_year = None
    birth_place = death_place = ''

    ev = event.lower() if event else ''
    if 'geboor' in ev or 'birth' in ev or 'doop' in ev or 'bapti' in ev:
        birth_year  = year
        birth_place = place_str[:100]
    elif 'overl' in ev or 'death' in ev or 'begraf' in ev or 'burial' in ev:
        death_year  = year
        death_place = place_str[:100]

    return {
        'first_name':  first,
        'last_name':   last,
        'birth_year':  birth_year,
        'death_year':  death_year,
        'birth_place': birth_place,
        'death_place': death_place,
        'gender':      '',
        'country':     'Netherlands',
        'source':      'openarch',
        'source_id':   rec.get('identifier', ''),
    }


def insert_record(db: sqlite3.Connection, rec: dict) -> bool:
    if not rec.get('source_id'):
        return False
    try:
        db.execute("""
            INSERT OR IGNORE INTO european_persons
              (first_name, last_name, birth_year, death_year,
               birth_place, death_place, gender, country, source, source_id)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            rec['first_name'], rec['last_name'],
            rec.get('birth_year'), rec.get('death_year'),
            rec.get('birth_place',''), rec.get('death_place',''),
            rec.get('gender',''), rec.get('country',''),
            rec['source'], rec['source_id'],
        ))
        db.commit()
        return db.execute('SELECT changes()').fetchone()[0] == 1
    except Exception as e:
        logger.warning('DB insert error: %s', e)
        return False


def fetch_page(session: requests.Session, name: str, start: int):
    for attempt in range(MAX_RETRY):
        try:
            r = session.get(API_URL, params={
                'name': name, 'rows': PAGE_SIZE, 'start': start,
            }, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            logger.warning('API %d for name=%s start=%d', r.status_code, name, start)
        except Exception as e:
            logger.warning('API error name=%s start=%d: %s', name, start, e)
        time.sleep(DELAY_MAX * (attempt + 1))
    return None


def load_checkpoint() -> dict:
    if CKPT_FILE.exists():
        return json.loads(CKPT_FILE.read_text())
    return {'combos_done': [], 'total_inserted': 0, 'total_seen': 0}


def save_checkpoint(ckpt: dict):
    CKPT_FILE.write_text(json.dumps(ckpt, indent=2))


def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(seen: set):
    # Only save every 10K to avoid slow writes — caller handles this
    SEEN_FILE.write_text(json.dumps(list(seen)))


def run():
    db = sqlite3.connect(str(DB_PATH))
    init_db(db)

    session = requests.Session()
    session.headers['User-Agent'] = 'RootBridge/1.0 genealogy research (educational)'

    ckpt       = load_checkpoint()
    combos_done = set(ckpt.get('combos_done', []))
    total_ins  = ckpt.get('total_inserted', 0)
    total_seen = ckpt.get('total_seen', 0)

    seen = load_seen()

    # All 676 two-letter combos: AA, AB, ... ZZ
    combos = [a + b for a in LETTERS for b in LETTERS]
    logger.info('OpenArch scraper: %d combos, %d done', len(combos), len(combos_done))

    for combo in combos:
        if combo in combos_done:
            continue

        out_file      = HDIR / f'{combo}.jsonl'
        combo_written = 0
        start         = 0

        # First call to get total
        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
        first_page = fetch_page(session, combo, 0)
        if first_page is None:
            logger.warning('Skipping %s — API failed', combo)
            combos_done.add(combo)
            ckpt['combos_done'] = list(combos_done)
            save_checkpoint(ckpt)
            continue

        total = first_page.get('response', {}).get('number_found', 0)
        if total == 0:
            combos_done.add(combo)
            ckpt['combos_done'] = list(combos_done)
            save_checkpoint(ckpt)
            continue

        logger.info('Combo %s: %d records', combo, total)
        pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        # Cap at 10,000 pages per combo (100K records) to avoid infinite loops on very common substrings
        pages = min(pages, 10000)

        with out_file.open('a', encoding='utf-8') as f:
            for page_num in range(pages):
                start = page_num * PAGE_SIZE

                if page_num == 0:
                    page_data = first_page
                else:
                    time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
                    page_data = fetch_page(session, combo, start)
                    if page_data is None:
                        break

                docs = page_data.get('response', {}).get('docs', [])
                if not docs:
                    break

                for doc in docs:
                    uid = doc.get('identifier', '')
                    if uid and uid in seen:
                        continue
                    if uid:
                        seen.add(uid)

                    total_seen += 1
                    f.write(json.dumps(doc, ensure_ascii=False) + '\n')

                    db_rec = record_to_db(doc)
                    if insert_record(db, db_rec):
                        total_ins += 1
                    combo_written += 1

                if combo_written % 1000 == 0 and combo_written > 0:
                    logger.info('  %s: %d written so far', combo, combo_written)

        # Save seen set every combo
        if len(seen) % 50000 < PAGE_SIZE * 10:
            save_seen(seen)

        combos_done.add(combo)
        ckpt['combos_done']    = list(combos_done)
        ckpt['total_inserted'] = total_ins
        ckpt['total_seen']     = total_seen
        save_checkpoint(ckpt)

        logger.info('Combo %s done: +%d written, %d total inserted', combo, combo_written, total_ins)

    save_seen(seen)
    db.close()
    logger.info('DONE: %d persons from Open Archives NL', total_ins)


if __name__ == '__main__':
    run()
