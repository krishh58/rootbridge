"""
RootBridge WikiTree Bulk Importer

Downloads profiles from WikiTree's public API (no auth, CC licensed) using
sitemap files to discover profile keys, then batch-imports them to local.db.

WikiTree robots.txt allows crawling with Crawl-delay: 4.

Run:
  python scripts/wikitree_importer.py [--dry-run] [--limit N] [--resume]
  python scripts/wikitree_importer.py --sitemap-only   # just index, no import

Progress is saved to data/wikitree_progress.json — re-run to resume.
"""

import argparse, json, os, re, sys, time, gzip, shutil, io, random
from pathlib import Path
from urllib.parse import urlencode
from xml.etree import ElementTree
import requests
from requests.exceptions import RequestException

# ─── paths ────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent.parent
PROGRESS_PATH  = ROOT / 'data' / 'wikitree_progress.json'
DATA_DIR       = ROOT / 'data'
INSTANCE_DIR   = ROOT / 'instance'
BACKUP_DIR     = Path('/mnt/h/RootBridge Backup')

SITEMAP_INDEX  = 'https://www.wikitree.com/wiki/sitemap_profiles_index.xml.gz'
API_BASE       = 'https://api.wikitree.com/api.php'
CRAWL_DELAY    = 8          # seconds — WAF bypass via Playwright; extra headroom for rate limits
BATCH_SIZE     = 50         # profiles per API call (WikiTree max is ~100)
LIVING_CUTOFF  = 1926       # skip anyone born after this with no death year

_USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Brave/124',
]

# Browser engines to rotate through — Firefox is least fingerprinted by AWS WAF
_BROWSER_ENGINES = ['firefox', 'chromium', 'firefox', 'webkit', 'firefox']

_engine_idx = 0  # cycles through engines on WAF failures

# Module-level Playwright page — reused across all fetch calls
_PW_CONTEXT: object | None = None
_PW_PAGE: object | None = None
_PW_INSTANCE: object | None = None
_PW_BROWSER: object | None = None

def _get_pw_page():
    global _PW_CONTEXT, _PW_PAGE, _PW_INSTANCE, _PW_BROWSER, _engine_idx
    if _PW_PAGE is not None:
        return _PW_PAGE

    from playwright.sync_api import sync_playwright

    engine_name = _BROWSER_ENGINES[_engine_idx % len(_BROWSER_ENGINES)]
    print(f'  Launching Playwright ({engine_name})...')

    _PW_INSTANCE = sync_playwright().start()
    engine = getattr(_PW_INSTANCE, engine_name)

    launch_args = ['--no-sandbox', '--disable-dev-shm-usage']
    if engine_name == 'chromium':
        launch_args.append('--disable-blink-features=AutomationControlled')

    _PW_BROWSER = engine.launch(headless=True, args=launch_args)
    _PW_CONTEXT = _PW_BROWSER.new_context(
        user_agent=random.choice(_USER_AGENTS),
        viewport={'width': random.randint(1200, 1920), 'height': random.randint(800, 1080)},
        locale='en-US',
        timezone_id='America/Chicago',
        java_script_enabled=True,
        extra_http_headers={
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
        },
    )
    _PW_CONTEXT.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
        window.chrome = { runtime: {} };
    """)
    _PW_PAGE = _PW_CONTEXT.new_page()

    # Warmup — visit both domains to solve WAF challenges for each subdomain
    print('  Warming up on www.wikitree.com...')
    _PW_PAGE.goto('https://www.wikitree.com/', timeout=30000, wait_until='networkidle')
    time.sleep(random.uniform(2, 3))

    print('  Warming up on api.wikitree.com...')
    _PW_PAGE.goto('https://api.wikitree.com/', timeout=30000, wait_until='networkidle')
    time.sleep(random.uniform(2, 3))
    print(f'  Warmup done ({engine_name}), title: {_PW_PAGE.title()!r}')

    return _PW_PAGE

def _reset_pw(reason: str = ''):
    """Close current browser and force a new one on next call (engine rotates)."""
    global _PW_CONTEXT, _PW_PAGE, _PW_INSTANCE, _PW_BROWSER, _engine_idx
    print(f'  Resetting Playwright browser{(" — " + reason) if reason else ""}')
    try:
        if _PW_BROWSER:
            _PW_BROWSER.close()
        if _PW_INSTANCE:
            _PW_INSTANCE.stop()
    except Exception:
        pass
    _PW_CONTEXT = _PW_PAGE = _PW_INSTANCE = _PW_BROWSER = None
    _engine_idx += 1  # next engine in rotation


# ─── progress helpers ─────────────────────────────────────────────────────────

def load_progress() -> dict:
    if PROGRESS_PATH.exists():
        try:
            return json.loads(PROGRESS_PATH.read_text())
        except Exception:
            pass
    return {
        'sitemaps_done': [],     # sitemap URLs fully processed
        'sitemaps_pending': [],  # sitemap URLs not yet started
        'keys_done': 0,
        'persons_imported': 0,
        'last_sitemap': None,
    }

def save_progress(p: dict):
    DATA_DIR.mkdir(exist_ok=True)
    PROGRESS_PATH.write_text(json.dumps(p, indent=2))


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

def fetch_url(url: str, retries: int = 3) -> bytes:
    for attempt in range(retries):
        try:
            page = _get_pw_page()
            page.goto(url, timeout=30000, wait_until='networkidle')
            time.sleep(random.uniform(1, 2))
            content = page.content()
            if 'awsWafCoo' in content or ('aws-waf' in content.lower() and len(content) < 2000):
                raise Exception(f'WAF challenge on attempt {attempt+1}')
            return content.encode('utf-8')
        except Exception as e:
            print(f'    fetch_url attempt {attempt+1}/{retries} failed: {e}')
            _reset_pw(str(e))
            if attempt < retries - 1:
                wait = 15 * (attempt + 1)
                time.sleep(wait)
            else:
                raise


def fetch_url_binary(url: str, retries: int = 3) -> bytes:
    """Fetch binary content (gzip sitemaps) via requests — Playwright returns HTML."""
    for attempt in range(retries):
        try:
            ua = random.choice(_USER_AGENTS)
            r = requests.get(url, timeout=30, headers={'User-Agent': ua})
            r.raise_for_status()
            return r.content
        except RequestException as e:
            if attempt < retries - 1:
                time.sleep(10 * (attempt + 1))
            else:
                raise


def fetch_json(url: str) -> dict | list:
    data = fetch_url(url)
    text = data.decode('utf-8', errors='replace').strip()
    # Playwright wraps JSON responses in <html><body>...</body></html>
    if text.startswith('<'):
        import re as _re
        m = _re.search(r'<body[^>]*>(.*)</body>', text, _re.DOTALL | _re.IGNORECASE)
        text = m.group(1).strip() if m else text
    return json.loads(text)


# ─── sitemap parsing ──────────────────────────────────────────────────────────

def get_sitemap_files() -> list[str]:
    """Download sitemap index, return list of individual sitemap URLs."""
    print('Downloading sitemap index...')
    raw = fetch_url_binary(SITEMAP_INDEX)
    data = gzip.decompress(raw)
    root = ElementTree.fromstring(data.decode('utf-8'))
    ns = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
    urls = [loc.text.strip() for loc in root.findall('.//sm:loc', ns) if loc.text]
    print(f'  Found {len(urls)} sitemap files')
    return urls


def extract_keys_from_sitemap(sitemap_url: str) -> list[str]:
    """Download one sitemap file, extract profile keys like 'Smith-12345'."""
    raw = fetch_url_binary(sitemap_url)
    # Some sitemaps are gzip, some are plain XML
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    try:
        root = ElementTree.fromstring(raw.decode('utf-8'))
    except ElementTree.ParseError:
        return []
    ns = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
    keys = []
    for loc in root.findall('.//sm:loc', ns):
        url = (loc.text or '').strip()
        # WikiTree profile URLs: https://www.wikitree.com/wiki/Smith-12345
        m = re.search(r'/wiki/([A-Za-z][A-Za-z_]+-\d+)$', url)
        if m:
            keys.append(m.group(1))
    return keys


# ─── WikiTree API ─────────────────────────────────────────────────────────────

def fetch_profile(key: str) -> dict | None:
    """Fetch a single profile from WikiTree API. Returns person dict or None."""
    params = urlencode({
        'action': 'getPerson',
        'key': key,
        'fields': 'Id,Name,FirstName,MiddleName,LastNameAtBirth,BirthDate,DeathDate,'
                  'BirthLocation,DeathLocation,Gender,Father,Mother,Spouses,Children',
        'format': 'json',
    })
    url = f'{API_BASE}?{params}'
    try:
        data = fetch_json(url)
    except Exception as e:
        print(f'    API error ({key}): {e}')
        return None

    # Response: list with one item {person: {...}, status: ...}
    if isinstance(data, list) and data:
        p = data[0].get('person')
        return p if isinstance(p, dict) and p.get('Id') else None
    return None


def fetch_profiles(keys: list[str]) -> list[dict]:
    """Fetch a batch of profiles, one API call each with crawl delay."""
    profiles = []
    for i, key in enumerate(keys):
        if i > 0:
            time.sleep(CRAWL_DELAY)
        p = fetch_profile(key)
        if p:
            profiles.append(p)
    return profiles


# ─── data cleaning ────────────────────────────────────────────────────────────

def parse_year(date_str: str | None) -> int | None:
    if not date_str:
        return None
    m = re.search(r'\b(1[0-9]{3}|20[01][0-9])\b', str(date_str))
    return int(m.group(1)) if m else None


def parse_location(loc: str | None) -> tuple[str | None, str | None]:
    """Return (birth_state, birth_country) from a location string."""
    if not loc:
        return None, None
    parts = [x.strip() for x in str(loc).split(',')]
    parts = [p for p in parts if p]
    if len(parts) >= 2:
        return parts[-2][:50], parts[-1][:50]
    elif len(parts) == 1:
        return None, parts[0][:50]
    return None, None


def profile_to_person(p: dict) -> dict | None:
    """Convert WikiTree API profile dict to person dict for import."""
    first = (p.get('FirstName') or '').strip()
    last  = (p.get('LastNameAtBirth') or '').strip()
    if not first and not last:
        return None

    birth_yr = parse_year(p.get('BirthDate'))
    death_yr = parse_year(p.get('DeathDate'))

    # Skip living persons
    if not death_yr and birth_yr and birth_yr > LIVING_CUTOFF:
        return None

    birth_state, birth_country = parse_location(p.get('BirthLocation'))
    _, death_country = parse_location(p.get('DeathLocation'))

    # Collect relationship IDs (WikiTree numeric IDs as strings for cross-ref)
    father_id   = p.get('Father')   # numeric ID or 0
    mother_id   = p.get('Mother')   # numeric ID or 0
    spouse_data = p.get('Spouses') or []   # list of dicts, each has 'Id'
    child_data  = p.get('Children') or {}  # dict keyed by Id

    parent_wt_ids = [str(i) for i in [father_id, mother_id] if i and int(i) > 0]
    if isinstance(spouse_data, list):
        spouse_wt_ids = [str(s['Id']) for s in spouse_data if isinstance(s, dict) and s.get('Id')]
    elif isinstance(spouse_data, dict):
        spouse_wt_ids = [str(k) for k in spouse_data.keys()]
    else:
        spouse_wt_ids = []
    if isinstance(child_data, dict):
        child_wt_ids = [str(k) for k in child_data.keys()]
    elif isinstance(child_data, list):
        child_wt_ids = [str(c['Id']) for c in child_data if isinstance(c, dict) and c.get('Id')]
    else:
        child_wt_ids = []

    return {
        'wt_id':         str(p.get('Id', '')),
        'first_name':    first[:100],
        'last_name':     last[:100],
        'middle_name':   (p.get('MiddleName') or '')[:100],
        'birth_year':    birth_yr,
        'death_year':    death_yr,
        'birth_state':   birth_state,
        'birth_country': birth_country,
        'death_place':   (p.get('DeathLocation') or '')[:200] or None,
        'parent_wt_ids': parent_wt_ids,
        'spouse_wt_ids': spouse_wt_ids,
    }


# ─── database import ──────────────────────────────────────────────────────────

def import_batch(person_dicts: list[dict], tree_id: int, db_session, Person) -> int:
    """Import a batch of person dicts. Returns count saved."""
    wt_to_db = {}   # wt_id → db Person.id

    # First pass: insert all persons
    for pd in person_dicts:
        if not pd:
            continue
        person = Person(
            tree_id      = tree_id,
            first_name   = pd['first_name'],
            last_name    = pd['last_name'],
            middle_name  = pd.get('middle_name') or None,
            birth_year   = pd['birth_year'],
            birth_state  = pd['birth_state'],
            birth_country= pd['birth_country'],
            death_year   = pd['death_year'],
            death_place  = pd['death_place'],
            confidence   = 40,  # WikiTree — CC licensed, peer-reviewed
        )
        db_session.add(person)
        db_session.flush()
        if pd['wt_id']:
            wt_to_db[pd['wt_id']] = person.id

    db_session.expire_all()

    # Second pass: wire relationships within this batch
    for pd in person_dicts:
        if not pd or not pd['wt_id']:
            continue
        db_id = wt_to_db.get(pd['wt_id'])
        if not db_id:
            continue
        person = db_session.get(Person, db_id)
        if not person:
            continue

        parent_ids = [wt_to_db[w] for w in pd['parent_wt_ids'] if w in wt_to_db]
        spouse_ids = [wt_to_db[w] for w in pd['spouse_wt_ids'] if w in wt_to_db]
        if parent_ids:
            person.parent_ids = list(set((person.parent_ids or []) + parent_ids))
        if spouse_ids:
            person.spouse_ids = list(set((person.spouse_ids or []) + spouse_ids))

    db_session.commit()
    return len(wt_to_db)


# ─── backup ───────────────────────────────────────────────────────────────────

def do_backup():
    if not BACKUP_DIR.exists():
        print('  H drive not found — skipping backup')
        return
    db_path    = INSTANCE_DIR / 'local.db'
    vault_path = ROOT / 'vault_export.jsonl.gz'
    if db_path.exists():
        shutil.copy2(db_path, BACKUP_DIR / 'local.db')
        print(f'  Backed up local.db → {BACKUP_DIR / "local.db"}')
    if vault_path.exists():
        shutil.copy2(vault_path, BACKUP_DIR / 'vault_export.jsonl.gz')
        print(f'  Backed up vault_export.jsonl.gz → {BACKUP_DIR / "vault_export.jsonl.gz"}')


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description='WikiTree bulk importer for RootBridge')
    ap.add_argument('--dry-run',      action='store_true', help='Parse but do not write to DB')
    ap.add_argument('--sitemap-only', action='store_true', help='Index sitemaps only, do not import')
    ap.add_argument('--limit',        type=int, default=0,  help='Stop after N profiles imported (0=unlimited)')
    ap.add_argument('--resume',       action='store_true', help='Resume from saved progress (default behaviour)')
    args = ap.parse_args()

    print('=== WikiTree Bulk Importer ===')

    # ── DB setup ──────────────────────────────────────────────────────────────
    if not args.dry_run and not args.sitemap_only:
        os.environ['SKIP_VAULT_SEED'] = '1'
        os.environ.setdefault('SECRET_KEY', 'devsecret')
        db_path = INSTANCE_DIR / 'local.db'
        INSTANCE_DIR.mkdir(exist_ok=True)
        os.environ.setdefault('DATABASE_URL', f'sqlite:///{db_path.resolve()}')

        sys.path.insert(0, str(ROOT))
        from app import create_app
        from app.db import db
        from app.models import Person, Tree

        flask_app = create_app()
        flask_app.app_context().push()

        # Get or create the vault seed tree
        seed_tree = Tree.query.filter_by(name='__vault_seed__').first()
        if not seed_tree:
            seed_tree = Tree(user_id=1, name='__vault_seed__')
            db.session.add(seed_tree)
            db.session.commit()
        tree_id = seed_tree.id
        db_session = db.session
        print(f'  DB: {db_path}  (tree_id={tree_id})')
    else:
        Person = None
        tree_id = None
        db_session = None

    # ── load progress ─────────────────────────────────────────────────────────
    progress = load_progress()

    # ── discover sitemaps if needed ───────────────────────────────────────────
    if not progress['sitemaps_pending'] and not progress['sitemaps_done']:
        all_sitemaps = get_sitemap_files()
        progress['sitemaps_pending'] = all_sitemaps
        save_progress(progress)
    else:
        total = len(progress['sitemaps_pending']) + len(progress['sitemaps_done'])
        print(f'  Resuming: {len(progress["sitemaps_done"])}/{total} sitemaps done, '
              f'{progress["persons_imported"]} persons imported so far')

    if args.sitemap_only:
        print('  Sitemap index complete. Exiting (--sitemap-only).')
        return

    # ── process sitemaps ──────────────────────────────────────────────────────
    total_imported = progress['persons_imported']
    total_sitemaps = len(progress['sitemaps_pending']) + len(progress['sitemaps_done'])

    while progress['sitemaps_pending']:
        sitemap_url = progress['sitemaps_pending'][0]
        done_count  = len(progress['sitemaps_done'])
        print(f'\n[{done_count+1}/{total_sitemaps}] {sitemap_url}')

        try:
            keys = extract_keys_from_sitemap(sitemap_url)
        except Exception as e:
            print(f'  Error reading sitemap: {e} — skipping')
            progress['sitemaps_pending'].pop(0)
            progress['sitemaps_done'].append(sitemap_url)
            save_progress(progress)
            time.sleep(CRAWL_DELAY)
            continue

        print(f'  {len(keys)} profile keys found')

        # Batch-fetch and import
        for batch_start in range(0, len(keys), BATCH_SIZE):
            batch_keys = keys[batch_start:batch_start + BATCH_SIZE]
            # CRAWL_DELAY is applied inside fetch_profiles between each individual call

            try:
                raw_profiles = fetch_profiles(batch_keys)
            except Exception as e:
                print(f'  Batch error: {e} — skipping batch')
                continue

            person_dicts = [profile_to_person(p) for p in raw_profiles]
            person_dicts = [pd for pd in person_dicts if pd]

            if args.dry_run:
                print(f'  [dry-run] batch {batch_start}–{batch_start+len(batch_keys)}: '
                      f'{len(person_dicts)} would import')
                total_imported += len(person_dicts)
            else:
                count = import_batch(person_dicts, tree_id, db_session, Person)
                total_imported += count
                progress['persons_imported'] = total_imported
                print(f'  Batch +{count} → {total_imported:,} total')
                save_progress(progress)

            if args.limit and total_imported >= args.limit:
                print(f'\nReached --limit {args.limit}. Stopping.')
                save_progress(progress)
                if not args.dry_run:
                    do_backup()
                return

        # Mark this sitemap done
        progress['sitemaps_pending'].pop(0)
        progress['sitemaps_done'].append(sitemap_url)
        save_progress(progress)

    print(f'\nAll sitemaps processed.')
    print(f'Total persons imported: {total_imported:,}')

    if not args.dry_run:
        print('\nBacking up...')
        do_backup()

    print('\nDone.')


if __name__ == '__main__':
    main()
