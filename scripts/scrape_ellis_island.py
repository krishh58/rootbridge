#!/usr/bin/env python3
"""
Bulk scraper for Ellis Island passenger arrival records.

Source: heritage.statueofliberty.org WordPress AJAX endpoint
        (passenger_search_api action, FamilySearch-backed)
Data:   ~12M records, 1892–1957, New York port arrivals
Output: /mnt/h/RootBridge/immigration/ellis_island/
        - {PREFIX}.jsonl (one record per line)
        - checkpoint.json (tracks completed prefixes + page progress)

Strategy:
  - Iterate 2-letter last-name prefixes AA→ZZ (676 total)
  - For prefixes with > MAX_PAGES pages, subdivide to 3-letter (sub-prefixes)
  - Rate: ~1 req/sec — ~5–15 days total for all 12M records
  - Resume: checkpoint.json tracks progress per prefix

Usage:
  python scripts/scrape_ellis_island.py
  python scripts/scrape_ellis_island.py --resume            # continue from checkpoint
  python scripts/scrape_ellis_island.py --prefix SM         # single prefix (debug)
  python scripts/scrape_ellis_island.py --workers 2         # parallel workers
"""

import argparse
import json
import os
import sys
import time
import logging
from itertools import product
from pathlib import Path
from string import ascii_uppercase
from threading import Lock

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── Config ───────────────────────────────────────────────────────────────────
OUT_DIR      = Path('/mnt/h/RootBridge/immigration/ellis_island')
CHECKPOINT   = OUT_DIR / 'checkpoint.json'
AJAX_URL     = 'https://www.statueofliberty.org/wp-admin/admin-ajax.php'
NONCE_URL    = 'https://www.statueofliberty.org/arrival-search/'
NONCE        = 'fb08f16f71'   # static WP nonce — refresh if API starts returning 403
MAX_PAGES    = 500            # if a prefix has more, split to 3-letter
RATE_LIMIT   = 1.1            # seconds between requests (per worker)
MAX_RETRIES  = 4
LOG_EVERY    = 25             # log a line every N pages

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(OUT_DIR / 'scrape_ellis.log', mode='a'),
    ]
)
log = logging.getLogger(__name__)

# ── Checkpoint ───────────────────────────────────────────────────────────────
_ckpt_lock = Lock()

def load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        return json.loads(CHECKPOINT.read_text())
    return {'done': [], 'in_progress': {}}

def save_checkpoint(ckpt: dict):
    with _ckpt_lock:
        CHECKPOINT.write_text(json.dumps(ckpt, indent=2))

# ── HTTP session with retry ───────────────────────────────────────────────────
def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=MAX_RETRIES,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=['POST'],
    )
    s.mount('https://', HTTPAdapter(max_retries=retry))
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://www.statueofliberty.org/arrival-search/',
        'Content-Type': 'application/x-www-form-urlencoded',
    })
    return s

# ── API calls ─────────────────────────────────────────────────────────────────
_nonce_cache = NONCE
_nonce_fetched_at = 0.0

def _refresh_nonce(session: requests.Session) -> str:
    global _nonce_cache, _nonce_fetched_at
    try:
        r = session.get(NONCE_URL, timeout=15)
        import re
        m = re.search(r'"nonce":"([a-f0-9]+)"', r.text)
        if m:
            _nonce_cache = m.group(1)
            _nonce_fetched_at = time.time()
            log.info('Refreshed nonce: %s', _nonce_cache)
    except Exception as e:
        log.warning('Nonce refresh failed: %s — using cached', e)
    return _nonce_cache


def search_page(session: requests.Session, last_name: str, page: int) -> dict:
    data = {
        'action': 'passenger_search_api',
        'api_key': 'familysearch-passenger-api',
        'nonce': _nonce_cache,
        'last_name': last_name,
        'page': page,
    }
    resp = session.post(AJAX_URL, data=data, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get('success'):
        raise ValueError(f'API returned success=false for {last_name} p{page}')
    return payload['data']


# ── Prefix generation ─────────────────────────────────────────────────────────
def two_letter_prefixes():
    for a, b in product(ascii_uppercase, repeat=2):
        yield a + b

def three_letter_prefixes(prefix2: str):
    for c in ascii_uppercase:
        yield prefix2 + c


# ── Per-prefix scraper ────────────────────────────────────────────────────────
def scrape_prefix(prefix: str, session: requests.Session, ckpt: dict, start_page: int = 1) -> int:
    """Scrape all pages for a given last-name prefix. Returns total records saved."""
    outfile = OUT_DIR / f'{prefix}.jsonl'
    mode = 'a' if start_page > 1 else 'w'
    total_saved = 0
    page = start_page

    with open(outfile, mode, encoding='utf-8') as fh:
        while True:
            try:
                data = search_page(session, prefix, page)
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 403:
                    log.warning('[%s] 403 — refreshing nonce', prefix)
                    _refresh_nonce(session)
                    time.sleep(5)
                    continue
                log.error('[%s] HTTP error p%d: %s — skipping', prefix, page, e)
                break
            except Exception as e:
                log.error('[%s] Error p%d: %s — skipping', prefix, page, e)
                break

            results = data.get('results', [])
            if not results:
                break

            for rec in results:
                fh.write(json.dumps(rec) + '\n')
            total_saved += len(results)

            pagination = data.get('pagination', {})
            total_pages = pagination.get('total_pages', 1)

            if page % LOG_EVERY == 0 or page == 1:
                log.info('[%s] page %d/%d — %d records so far',
                         prefix, page, total_pages, total_saved)

            # Save progress after every page
            ckpt['in_progress'][prefix] = {'page': page, 'total_pages': total_pages}
            if page % 10 == 0:
                save_checkpoint(ckpt)

            if page >= total_pages:
                break

            page += 1
            time.sleep(RATE_LIMIT)

    return total_saved


def scrape_with_subdivision(prefix2: str, session: requests.Session, ckpt: dict):
    """Scrape a 2-letter prefix; if too large, subdivide to 3-letter prefixes."""
    done_set = set(ckpt['done'])
    in_prog = ckpt.get('in_progress', {})

    if prefix2 in done_set:
        return

    # Quick probe to check total pages
    try:
        data = search_page(session, prefix2, 1)
        total_pages = data.get('pagination', {}).get('total_pages', 1)
        time.sleep(RATE_LIMIT)
    except Exception as e:
        log.error('[%s] probe failed: %s', prefix2, e)
        return

    if total_pages <= MAX_PAGES:
        start = in_prog.get(prefix2, {}).get('page', 1)
        count = scrape_prefix(prefix2, session, ckpt, start_page=start)
        log.info('[%s] DONE — %d records', prefix2, count)
        ckpt['done'].append(prefix2)
        ckpt['in_progress'].pop(prefix2, None)
        save_checkpoint(ckpt)
    else:
        log.info('[%s] %d pages — subdividing to 3-letter prefixes', prefix2, total_pages)
        for prefix3 in three_letter_prefixes(prefix2):
            key3 = prefix3
            if key3 in done_set:
                continue
            start = in_prog.get(key3, {}).get('page', 1)
            count = scrape_prefix(prefix3, session, ckpt, start_page=start)
            log.info('[%s] DONE — %d records', prefix3, count)
            ckpt['done'].append(key3)
            ckpt['in_progress'].pop(key3, None)
            save_checkpoint(ckpt)
        # Mark the 2-letter prefix done once all sub-prefixes complete
        ckpt['done'].append(prefix2)
        save_checkpoint(ckpt)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description='Bulk Ellis Island passenger scraper')
    ap.add_argument('--resume', action='store_true', help='Resume from checkpoint')
    ap.add_argument('--prefix', help='Scrape only this prefix (e.g. HE)')
    ap.add_argument('--workers', type=int, default=1, help='Parallel workers (be polite: ≤2)')
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ckpt = load_checkpoint()

    session = make_session()

    if args.prefix:
        p = args.prefix.upper()
        log.info('Single-prefix mode: %s', p)
        scrape_with_subdivision(p, session, ckpt)
        return

    all_prefixes = list(two_letter_prefixes())
    done_set = set(ckpt['done'])
    remaining = [p for p in all_prefixes if p not in done_set]

    log.info('Starting Ellis Island bulk scrape — %d/%d prefixes remaining',
             len(remaining), len(all_prefixes))

    if args.workers > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        # Each worker gets its own session to avoid sharing state
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {
                ex.submit(scrape_with_subdivision, p, make_session(), ckpt): p
                for p in remaining
            }
            for fut in as_completed(futures):
                p = futures[fut]
                try:
                    fut.result()
                except Exception as e:
                    log.error('Worker failed for %s: %s', p, e)
    else:
        for prefix in remaining:
            scrape_with_subdivision(prefix, session, ckpt)

    log.info('All prefixes complete.')


if __name__ == '__main__':
    main()
