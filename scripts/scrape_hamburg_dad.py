#!/usr/bin/env python3
"""
Bulk scraper for the Deutsche Auswanderer-Datenbank (DAD).
Source:  www.dad-recherche.de/hmb/search_engl.asp
Data:    ~5M Hamburg + Bremen departure records, 1820–1934 (name, age, year)
         Full record details (ship, destination) are behind a paywall — we capture
         the free list view: surname, first_name, age, departure_year.
Output:  /mnt/h/RootBridge/immigration/hamburg_dad/
         - {PREFIX}.jsonl
         - checkpoint.json

Strategy:
  - 676 two-letter surname prefix searches (AA→ZZ)
  - Site caps results at 100/query; if a query returns exactly 100 rows,
    subdivide to 26 three-letter sub-searches
  - Rate: 0.8 req/sec; ~5-10 hours for full run

Usage:
  python scripts/scrape_hamburg_dad.py
  python scripts/scrape_hamburg_dad.py --resume
  python scripts/scrape_hamburg_dad.py --prefix SC     # single prefix
"""

import argparse
import json
import logging
import re
import time
from itertools import product
from pathlib import Path
from string import ascii_uppercase
from threading import Lock

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

OUT_DIR    = Path('/mnt/h/RootBridge/immigration/hamburg_dad')
CHECKPOINT = OUT_DIR / 'checkpoint.json'
SEARCH_URL = 'https://www.dad-recherche.de/hmb/search_engl.asp'
RATE_LIMIT = 0.8
TRUNCATION = 100   # if result count == this, subdivide

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(OUT_DIR / 'scrape_hamburg.log', mode='a'),
    ]
)
log = logging.getLogger(__name__)

_ckpt_lock = Lock()


def load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        return json.loads(CHECKPOINT.read_text())
    return {'done': [], 'in_progress': {}}


def save_checkpoint(ckpt: dict):
    with _ckpt_lock:
        CHECKPOINT.write_text(json.dumps(ckpt, indent=2))


def make_session() -> requests.Session:
    s = requests.Session()
    s.mount('https://', HTTPAdapter(max_retries=Retry(
        total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504]
    )))
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://www.dad-recherche.de/hmb/index_engl.asp',
        'Accept': 'text/html,application/xhtml+xml,*/*;q=0.9',
        'Accept-Language': 'en-US,en;q=0.9,de;q=0.8',
    })
    return s


def parse_results(html: str) -> list[dict]:
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.I)
    records = []
    for row in rows:
        cells = [re.sub(r'<[^>]+>', '', c).strip()
                 for c in re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.I)]
        cells = [c for c in cells if c and c != '&raquo;']
        if len(cells) >= 3 and cells[0] and cells[0][0].isupper() and cells[0] != 'Surname':
            rec = {
                'last':  cells[0],
                'first': cells[1] if len(cells) > 1 else '',
                'age':   cells[2] if len(cells) > 2 else '',
                'year':  cells[3] if len(cells) > 3 else '',
                'source': 'hamburg_dad',
            }
            # Skip obviously corrupt entries
            if rec['last'] and not re.match(r'^[A-Z][A-Z -]{0,40}$', rec['last']):
                continue
            records.append(rec)
    return records


def search_prefix(session: requests.Session, surname_prefix: str) -> list[dict]:
    # Append % to match all surnames starting with this prefix (SQL LIKE wildcard)
    try:
        r = session.get(
            SEARCH_URL,
            params={'Nachname': surname_prefix + '%', 'Vorname': '', 'Geschlecht': '%', 'x1': '70080400'},
            timeout=20
        )
        r.raise_for_status()
        return parse_results(r.text)
    except Exception as e:
        log.warning('Search failed for %s: %s', surname_prefix, e)
        return []


def scrape_prefix(prefix: str, session: requests.Session, ckpt: dict) -> int:
    done_set = set(ckpt['done'])
    if prefix in done_set:
        return 0

    results = search_prefix(session, prefix)
    time.sleep(RATE_LIMIT)

    if len(results) == TRUNCATION:
        # Subdivide to 3-letter prefixes
        log.info('[%s] %d results (truncated) — subdividing to 3-letter', prefix, TRUNCATION)
        total = 0
        for c in ascii_uppercase:
            sub = prefix + c
            if sub in done_set:
                continue
            sub_results = search_prefix(session, sub)
            time.sleep(RATE_LIMIT)
            if sub_results:
                _write_jsonl(OUT_DIR / f'{sub}.jsonl', sub_results)
                total += len(sub_results)
                log.info('  [%s] %d records', sub, len(sub_results))
            ckpt['done'].append(sub)
            save_checkpoint(ckpt)
        ckpt['done'].append(prefix)
        save_checkpoint(ckpt)
        return total
    else:
        if results:
            _write_jsonl(OUT_DIR / f'{prefix}.jsonl', results)
        log.info('[%s] %d records', prefix, len(results))
        ckpt['done'].append(prefix)
        save_checkpoint(ckpt)
        return len(results)


def _write_jsonl(path: Path, records: list[dict]):
    with open(path, 'w', encoding='utf-8') as f:
        for rec in records:
            f.write(json.dumps(rec) + '\n')


def two_letter_prefixes():
    for a, b in product(ascii_uppercase, repeat=2):
        yield a + b


def main():
    ap = argparse.ArgumentParser(description='Hamburg DAD emigrant bulk scraper')
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--prefix', help='Single prefix (e.g. SC)')
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ckpt = load_checkpoint()
    session = make_session()

    if args.prefix:
        count = scrape_prefix(args.prefix.upper(), session, ckpt)
        log.info('Done: %d records for %s', count, args.prefix.upper())
        return

    all_prefixes = list(two_letter_prefixes())
    done_set = set(ckpt['done'])
    remaining = [p for p in all_prefixes if p not in done_set]
    log.info('Hamburg DAD scrape — %d/%d prefixes remaining', len(remaining), len(all_prefixes))

    total = 0
    for prefix in remaining:
        total += scrape_prefix(prefix, session, ckpt)

    log.info('Complete — %d total records', total)


if __name__ == '__main__':
    main()
