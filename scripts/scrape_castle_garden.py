#!/usr/bin/env python3
"""
Bulk scraper for Castle Garden passenger arrival records.
Source:  castlegarden.org/searcher.php  (Battery Conservancy, NYC)
Data:    ~11M immigrant arrivals, 1820–1892 (pre-Ellis Island New York port)
         Fields: name, birth_year, arrival_year, last_residence, occupation, ship
Output:  /mnt/h/RootBridge/immigration/castle_garden/
         - {PREFIX}.jsonl
         - checkpoint.json

NOTE: castlegarden.org was unreachable on 2026-06-09 (SSL/connection errors).
      This scraper is built and ready — run it when the site recovers.
      The form is at: http://www.castlegarden.org/searcher.php
      Params: surname={NAME}, function=name, given={FIRST} (optional)

Usage:
  python scripts/scrape_castle_garden.py
  python scripts/scrape_castle_garden.py --resume
  python scripts/scrape_castle_garden.py --prefix HE
  python scripts/scrape_castle_garden.py --test        # connectivity test only
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

OUT_DIR    = Path('/mnt/h/RootBridge/immigration/castle_garden')
CHECKPOINT = OUT_DIR / 'checkpoint.json'
SEARCH_URL = 'http://www.castlegarden.org/searcher.php'
RATE_LIMIT = 1.0
PAGE_SIZE  = 50     # CG returns ~50 results per page (update after first test)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(OUT_DIR / 'scrape_castlegarden.log', mode='a'),
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
    s.mount('http://', HTTPAdapter(max_retries=Retry(
        total=4, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504]
    )))
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'http://www.castlegarden.org/',
        'Accept': 'text/html,application/xhtml+xml,*/*;q=0.9',
    })
    return s


def test_connectivity(session: requests.Session) -> bool:
    try:
        r = session.get(SEARCH_URL, params={'surname': 'Smith', 'function': 'name'}, timeout=15)
        return r.status_code == 200 and len(r.content) > 5000
    except Exception as e:
        log.error('Castle Garden connectivity test failed: %s', e)
        return False


def parse_results(html: str) -> tuple[list[dict], int]:
    """Returns (records, total_count). total_count=0 means unknown/single page."""
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.I)

    # Find total result count
    total = 0
    total_match = re.search(
        r'(\d[\d,]*)\s+(?:results?|records?|matches?|immigrants?)',
        html, re.I
    )
    if total_match:
        total = int(total_match.group(1).replace(',', ''))

    records = []
    header_found = False
    col_map = {}  # col_index -> field_name

    for row in rows:
        cells_raw = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL | re.I)
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells_raw]
        cells = [c for c in cells if c]

        if not cells:
            continue

        # Detect header row
        if not header_found:
            joined = ' '.join(cells).lower()
            if any(w in joined for w in ('name', 'surname', 'year', 'age', 'arrival')):
                header_found = True
                for i, cell in enumerate(cells):
                    cl = cell.lower()
                    if 'last' in cl or 'surname' in cl:
                        col_map[i] = 'last'
                    elif 'first' in cl or 'given' in cl:
                        col_map[i] = 'first'
                    elif 'arrival' in cl or 'year' in cl:
                        col_map[i] = 'arrival_year'
                    elif 'birth' in cl:
                        col_map[i] = 'birth_year'
                    elif 'residence' in cl or 'origin' in cl or 'last res' in cl:
                        col_map[i] = 'last_residence'
                    elif 'occup' in cl:
                        col_map[i] = 'occupation'
                    elif 'ship' in cl or 'vessel' in cl:
                        col_map[i] = 'ship'
                continue

        if header_found and len(cells) >= 2:
            rec = {'source': 'castle_garden'}
            for i, val in enumerate(cells):
                field = col_map.get(i)
                if field:
                    rec[field] = val
            # Fallback: if no col_map matched but we have 2+ cells, use positional
            if len(rec) == 1:
                rec['last']         = cells[0] if len(cells) > 0 else ''
                rec['first']        = cells[1] if len(cells) > 1 else ''
                rec['arrival_year'] = cells[2] if len(cells) > 2 else ''
            if rec.get('last') or rec.get('first'):
                records.append(rec)

    return records, total


def search_surname(session: requests.Session, surname: str, page: int = 1) -> tuple[list[dict], int]:
    params = {'surname': surname, 'function': 'name'}
    if page > 1:
        params['offset'] = (page - 1) * PAGE_SIZE
    try:
        r = session.get(SEARCH_URL, params=params, timeout=20)
        r.raise_for_status()
        return parse_results(r.text)
    except Exception as e:
        log.warning('CG search failed for %s p%d: %s', surname, page, e)
        return [], 0


def scrape_prefix(prefix: str, session: requests.Session, ckpt: dict) -> int:
    done_set = set(ckpt['done'])
    if prefix in done_set:
        return 0

    in_prog = ckpt.get('in_progress', {})
    outfile = OUT_DIR / f'{prefix}.jsonl'
    total = 0
    page = in_prog.get(prefix, 1)

    with open(outfile, 'a' if page > 1 else 'w', encoding='utf-8') as fh:
        while True:
            records, count = search_surname(session, prefix, page)
            time.sleep(RATE_LIMIT)

            if not records:
                break

            for rec in records:
                fh.write(json.dumps(rec) + '\n')
            total += len(records)

            # Update checkpoint
            ckpt['in_progress'][prefix] = page
            if page % 5 == 0:
                save_checkpoint(ckpt)
                log.info('[%s] page %d — %d records so far', prefix, page, total)

            # Check if there are more pages
            if count > 0 and total < count:
                page += 1
            elif len(records) == PAGE_SIZE:
                # Might be more — keep going
                page += 1
            else:
                break

    if total > 0:
        log.info('[%s] DONE — %d records', prefix, total)
    ckpt['done'].append(prefix)
    ckpt['in_progress'].pop(prefix, None)
    save_checkpoint(ckpt)
    return total


def two_letter_prefixes():
    for a, b in product(ascii_uppercase, repeat=2):
        yield a + b


def main():
    ap = argparse.ArgumentParser(description='Castle Garden bulk scraper (pre-Ellis Island, 1820-1892)')
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--prefix', help='Single prefix (e.g. HE)')
    ap.add_argument('--test', action='store_true', help='Connectivity test only')
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    session = make_session()

    if args.test:
        ok = test_connectivity(session)
        print('Castle Garden reachable:', ok)
        if ok:
            records, total = search_surname(session, 'Henderson')
            print(f'Test search: {len(records)} records, total={total}')
            if records:
                print('Sample:', records[0])
        return

    if not test_connectivity(session):
        log.error('Castle Garden is not reachable. Run again when site recovers.')
        log.error('Test with: python scripts/scrape_castle_garden.py --test')
        return

    ckpt = load_checkpoint()

    if args.prefix:
        count = scrape_prefix(args.prefix.upper(), session, ckpt)
        log.info('Done: %d records for %s', count, args.prefix.upper())
        return

    all_prefixes = list(two_letter_prefixes())
    done_set = set(ckpt['done'])
    remaining = [p for p in all_prefixes if p not in done_set]
    log.info('Castle Garden scrape — %d/%d prefixes remaining', len(remaining), len(all_prefixes))

    total = 0
    for prefix in remaining:
        total += scrape_prefix(prefix, session, ckpt)

    log.info('Complete — %d total records', total)


if __name__ == '__main__':
    main()
