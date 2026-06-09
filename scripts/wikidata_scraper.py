#!/usr/bin/env python3
"""
Scrape pre-1800 European persons from Wikidata SPARQL.

Wikidata has ~60,000+ Europeans born before 1800 with structured
name/birth/death/place data going back to the 1400s. Free, no auth.

Strategy: query by decade (1400–1799) to avoid SPARQL timeouts.
Each decade gets paginated with LIMIT/OFFSET until exhausted.

Output: /mnt/h/RootBridge/european/wikidata/DECADE.jsonl
Also inserts into data/ged_vault_index.db → european_persons table.

Usage:
    python3 scripts/wikidata_scraper.py [--resume]
"""

import json
import logging
import sqlite3
import time
import random
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/tmp/wikidata_scraper.log'),
    ],
)
logger = logging.getLogger(__name__)

ROOT      = Path(__file__).parent.parent
DB_PATH   = ROOT / 'data' / 'ged_vault_index.db'
HDIR      = Path('/mnt/h/RootBridge/european/wikidata')
CKPT_FILE = ROOT / 'data' / 'wikidata_checkpoint.json'
HDIR.mkdir(parents=True, exist_ok=True)

SPARQL_URL = 'https://query.wikidata.org/sparql'
PAGE_SIZE  = 500
DELAY_MIN  = 4.0
DELAY_MAX  = 8.0
TIMEOUT    = 45
MAX_RETRY  = 3

SPARQL_TEMPLATE = """
SELECT ?person ?personLabel ?birthDate ?deathDate
       ?birthPlaceLabel ?deathPlaceLabel ?genderLabel ?countryLabel WHERE {{
  ?person wdt:P31 wd:Q5 .
  ?person wdt:P569 ?birthDate .
  ?person wdt:P19 ?birthPlace .
  ?birthPlace wdt:P30 wd:Q46 .
  FILTER(YEAR(?birthDate) >= {year_from} && YEAR(?birthDate) < {year_to})
  OPTIONAL {{ ?person wdt:P570 ?deathDate }}
  OPTIONAL {{ ?person wdt:P21 ?gender }}
  OPTIONAL {{ ?person wdt:P20 ?deathPlace }}
  OPTIONAL {{ ?person wdt:P27 ?country }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" }}
}}
ORDER BY ?birthDate
LIMIT {limit}
OFFSET {offset}
"""


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


def split_name(label: str):
    parts = label.strip().split()
    if len(parts) >= 2:
        return ' '.join(parts[:-1]), parts[-1].upper()
    return label.strip(), ''


def parse_year(val: str):
    if not val:
        return None
    import re
    m = re.search(r'(-?\d{1,4})-\d{2}-\d{2}', val)
    if m:
        y = int(m.group(1))
        return y if y > 0 else None
    m2 = re.search(r'\b(\d{3,4})\b', val)
    return int(m2.group(1)) if m2 else None


def insert_record(db: sqlite3.Connection, rec: dict) -> bool:
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
            'wikidata', rec.get('source_id',''),
        ))
        db.commit()
        return db.execute('SELECT changes()').fetchone()[0] == 1
    except Exception as e:
        logger.warning('DB insert error: %s', e)
        return False


def query_decade(session: requests.Session, year_from: int, year_to: int, offset: int):
    sparql = SPARQL_TEMPLATE.format(
        year_from=year_from, year_to=year_to,
        limit=PAGE_SIZE, offset=offset,
    )
    for attempt in range(MAX_RETRY):
        try:
            r = session.post(SPARQL_URL,
                data={'query': sparql},
                headers={'Accept': 'application/sparql-results+json'},
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                return r.json()['results']['bindings']
            if r.status_code == 429:
                logger.warning('Rate limited — sleeping 30s')
                time.sleep(30)
            else:
                logger.warning('SPARQL %d for %d-%d offset %d', r.status_code, year_from, year_to, offset)
        except Exception as e:
            logger.warning('SPARQL error %d-%d offset %d: %s', year_from, year_to, offset, e)
        time.sleep(DELAY_MAX * (attempt + 1))
    return None


def load_checkpoint() -> dict:
    if CKPT_FILE.exists():
        return json.loads(CKPT_FILE.read_text())
    return {'decades_done': [], 'total_inserted': 0}


def save_checkpoint(ckpt: dict):
    CKPT_FILE.write_text(json.dumps(ckpt, indent=2))


def run():
    db = sqlite3.connect(str(DB_PATH))
    init_db(db)

    session = requests.Session()
    session.headers['User-Agent'] = 'RootBridge/1.0 genealogy research (educational)'

    ckpt         = load_checkpoint()
    decades_done = set(ckpt.get('decades_done', []))
    total_ins    = ckpt.get('total_inserted', 0)

    # Decades from 1400 to 1800 (exclusive upper bound)
    decades = [(y, y + 10) for y in range(1400, 1800, 10)]
    logger.info('Wikidata scraper: %d decades, %d already done', len(decades), len(decades_done))

    for year_from, year_to in decades:
        decade_key = str(year_from)
        if decade_key in decades_done:
            continue

        out_file = HDIR / f'{year_from}s.jsonl'
        written  = 0
        offset   = 0

        logger.info('Decade %d–%d', year_from, year_to)

        while True:
            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
            bindings = query_decade(session, year_from, year_to, offset)

            if bindings is None:
                logger.warning('  Failed after retries — skipping offset %d', offset)
                break
            if not bindings:
                break  # exhausted

            with out_file.open('a', encoding='utf-8') as f:
                for b in bindings:
                    label     = b.get('personLabel', {}).get('value', '')
                    source_id = b.get('person', {}).get('value', '').split('/')[-1]
                    first, last = split_name(label)

                    rec = {
                        'first_name':  first,
                        'last_name':   last,
                        'birth_year':  parse_year(b.get('birthDate',{}).get('value','')),
                        'death_year':  parse_year(b.get('deathDate',{}).get('value','')),
                        'birth_place': b.get('birthPlaceLabel',{}).get('value','')[:100],
                        'death_place': b.get('deathPlaceLabel',{}).get('value','')[:100],
                        'gender':      b.get('genderLabel',{}).get('value',''),
                        'country':     b.get('countryLabel',{}).get('value',''),
                        'source_id':   source_id,
                    }
                    f.write(json.dumps(rec, ensure_ascii=False) + '\n')
                    if insert_record(db, rec):
                        total_ins += 1
                    written += 1

            logger.info('  offset=%d got=%d written=%d total_db=%d',
                        offset, len(bindings), written, total_ins)
            offset += PAGE_SIZE

            if len(bindings) < PAGE_SIZE:
                break  # last page

        decades_done.add(decade_key)
        ckpt['decades_done']    = list(decades_done)
        ckpt['total_inserted']  = total_ins
        save_checkpoint(ckpt)
        logger.info('  Decade %d done — %d persons this decade, %d total', year_from, written, total_ins)

    db.close()
    logger.info('DONE: %d European persons from Wikidata', total_ins)


if __name__ == '__main__':
    run()
