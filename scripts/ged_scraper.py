"""
RootBridge GEDCOM Scraper — stealth multi-source

Sources:
  1. Internet Archive API  (most reliable — thousands of .ged files)
  2. GitHub API            (public repos containing .ged files)
  3. Playwright/DDG        (stealth browser search for filetype:ged)
  4. Direct known archives (freepages.rootsweb, webtrees demos, etc.)
  5. Bing search scrape    (via Playwright)

Run:
  python scripts/ged_scraper.py           # find + report
  python scripts/ged_scraper.py --download --limit 100
"""

import argparse, json, os, re, time, random, urllib.parse
from pathlib import Path
import requests

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

OUT_DIR    = Path(__file__).parent.parent / 'data' / 'ged_files'
INDEX_PATH = Path(__file__).parent.parent / 'data' / 'ged_index.json'

# Load existing index so runs merge rather than overwrite
found = []
if INDEX_PATH.exists():
    try:
        found = json.loads(INDEX_PATH.read_text())
        print(f"[resume] Loaded {len(found)} existing entries from index")
    except Exception:
        pass

def get(url, timeout=20, **kw):
    try:
        r = SESSION.get(url, timeout=timeout, **kw)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"  [skip] {url[:80]} — {e}")
        return None

def add(url, source, title=''):
    url = url.strip()
    if not url.lower().endswith('.ged') and '.ged?' not in url.lower():
        return
    if any(f['url'] == url for f in found):
        return
    found.append({'url': url, 'source': source, 'title': title})
    print(f"  + [{source:16s}] {url[:85]}")


# ─── 1. Internet Archive ──────────────────────────────────────────────────────

def scrape_internet_archive():
    print("\n[1] Internet Archive API...")

    # Search for items with GEDCOM format
    searches = [
        'title:gedcom',
        'title:GEDCOM',
        'subject:GEDCOM',
        'identifier:gedcom',
        'gedcom family history',
        'gedcom ancestry',
        'genealogy ged download',
        'family tree gedcom export',
        '.ged genealogy',
        'gedcom file family',
    ]
    seen_ids = set()

    for query in searches:
        for page in range(1, 4):  # up to 3 pages × 200 = 600 items per query
            url = 'https://archive.org/advancedsearch.php'
            params = {
                'q': query,
                'fl[]': 'identifier,title',
                'rows': 200,
                'page': page,
                'output': 'json',
            }
            r = get(url, params=params)
            if not r:
                break
            try:
                docs = r.json()['response']['docs']
            except Exception:
                break
            if not docs:
                break
            if page == 1:
                print(f"  Query '{query}' → {len(docs)} items (page {page})")
            else:
                print(f"    page {page}: {len(docs)} more items")

            for item in docs:
                ident = item.get('identifier', '')
                if ident in seen_ids:
                    continue
                seen_ids.add(ident)
                title = item.get('title', '')

                fr = get(f'https://archive.org/metadata/{ident}/files', timeout=10)
                if not fr:
                    continue
                try:
                    files = fr.json().get('result', [])
                except Exception:
                    continue
                for f in files:
                    name = f.get('name', '')
                    if name.lower().endswith('.ged'):
                        dl_url = f"https://archive.org/download/{ident}/{urllib.parse.quote(name)}"
                        add(dl_url, 'internet_archive', title or name)
                time.sleep(0.15)

            if len(docs) < 200:
                break  # no more pages

    print(f"  IA total so far: {len(found)}")


# ─── 2. GitHub API ────────────────────────────────────────────────────────────

def scrape_github():
    print("\n[2] GitHub code search for .ged files...")
    queries = [
        'extension:ged',
        'extension:ged genealogy',
        'extension:ged family',
        'extension:ged ancestry',
    ]
    token = os.environ.get('GITHUB_TOKEN', '')
    headers = {'Accept': 'application/vnd.github+json',
               'User-Agent': 'RootBridgeBot/1.0'}
    if token:
        headers['Authorization'] = f'token {token}'
    else:
        print("  (no GITHUB_TOKEN — unauthenticated, 10 req/min limit applies)")

    gh_session = requests.Session()
    gh_session.headers.update(headers)

    seen = set()
    for q in queries:
        for page in range(1, 6):
            url = 'https://api.github.com/search/code'
            params = {'q': q, 'per_page': 100, 'page': page}
            try:
                r = gh_session.get(url, params=params, timeout=15)
                if r.status_code == 403:
                    print(f"  GitHub rate limit hit")
                    break
                r.raise_for_status()
                items = r.json().get('items', [])
            except Exception as e:
                print(f"  GitHub error: {e}")
                break

            if not items:
                break

            for item in items:
                raw_url = item.get('html_url', '').replace(
                    'github.com', 'raw.githubusercontent.com'
                ).replace('/blob/', '/')
                if raw_url and raw_url not in seen:
                    seen.add(raw_url)
                    repo = item.get('repository', {}).get('full_name', '')
                    add(raw_url, 'github', repo)

            time.sleep(1.5)  # GitHub rate limit: 10 req/min unauthenticated

    print(f"  GitHub total so far: {len(found)}")


# ─── 3. Known direct archives ─────────────────────────────────────────────────

KNOWN_PAGES = [
    # RootsWeb WorldConnect — search results pages with .ged download links
    ('https://wc.rootsweb.com/cgi-bin/igm.cgi?op=SHOW&db=*&id=I0001', 'rootsweb'),
    # GenCircles / Geni public trees (they expose GEDCOM exports)
    ('https://www.geni.com/gedcom', 'geni'),
    # WeRelate public GEDCOM uploads
    ('https://www.werelate.org/wiki/Special:GedcomList', 'werelate'),
    # FamilyTreeCircles
    ('https://www.familytreecircles.com/', 'familytreecircles'),
    # Open Archives
    ('https://openarch.nl/', 'openarch'),
]

def scrape_known_pages():
    from bs4 import BeautifulSoup
    print("\n[3] Known archive pages...")

    for page_url, source in KNOWN_PAGES:
        r = get(page_url, timeout=12)
        if not r:
            continue
        soup = BeautifulSoup(r.text, 'lxml')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '.ged' in href.lower():
                full = urllib.parse.urljoin(page_url, href)
                add(full, source, a.get_text(strip=True)[:80])
        time.sleep(0.5)

    # WeRelate has a dedicated GEDCOM file list — parse it
    r = get('https://www.werelate.org/wiki/Special:GedcomList', timeout=15)
    if r:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, 'lxml')
        for a in soup.find_all('a', href=True):
            if 'ged' in a['href'].lower() or 'gedcom' in a['href'].lower():
                full = urllib.parse.urljoin('https://www.werelate.org', a['href'])
                add(full, 'werelate', a.get_text(strip=True)[:80])


# ─── 4. Playwright stealth browser ────────────────────────────────────────────

PLAYWRIGHT_QUERIES = [
    'genealogy "download" filetype:ged',
    'family tree gedcom file download site:freepages.rootsweb.com',
    'site:familyhistory.com gedcom download',
    '"family history" "gedcom" download -site:ancestry.com',
    'gedcom file download free genealogy 2020 2021 2022 2023',
]

BING_QUERIES = [
    'filetype:ged genealogy',
    'filetype:ged family history',
    'filetype:ged ancestry',
]

def scrape_with_playwright():
    from playwright.sync_api import sync_playwright
    from bs4 import BeautifulSoup
    import random

    print("\n[4] Playwright stealth browser searches...")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--disable-web-security',
            ]
        )
        ctx = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            viewport={'width': 1366, 'height': 768},
            locale='en-US',
        )
        # Mask automation signals
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
        """)
        page = ctx.new_page()

        # ── Bing filetype search ──
        for query in BING_QUERIES:
            try:
                url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}&count=50"
                page.goto(url, timeout=20000)
                page.wait_for_timeout(random.randint(1500, 3000))
                html = page.content()
                soup = BeautifulSoup(html, 'lxml')
                # Bing result links
                for a in soup.find_all('a', href=True):
                    link = a.get('href', '')
                    if '.ged' in link.lower():
                        add(link, 'bing', a.get_text(strip=True)[:80])
                print(f"  Bing: '{query}' done")
                time.sleep(random.uniform(2, 4))
            except Exception as e:
                print(f"  Bing error for '{query}': {e}")

        # ── DuckDuckGo ──
        for query in PLAYWRIGHT_QUERIES:
            try:
                url = f"https://duckduckgo.com/?q={urllib.parse.quote(query)}&ia=web"
                page.goto(url, timeout=20000)
                page.wait_for_timeout(random.randint(2000, 4000))
                # Scroll to load more
                page.keyboard.press('End')
                page.wait_for_timeout(1000)
                html = page.content()
                soup = BeautifulSoup(html, 'lxml')
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if '.ged' in href.lower():
                        clean = href
                        if 'uddg=' in href:
                            try:
                                clean = urllib.parse.unquote(href.split('uddg=')[1].split('&')[0])
                            except Exception:
                                pass
                        add(clean, 'duckduckgo', a.get_text(strip=True)[:80])
                print(f"  DDG: '{query[:50]}' done")
                time.sleep(random.uniform(3, 6))
            except Exception as e:
                print(f"  DDG error: {e}")

        # ── Scrape WeRelate GEDCOM list directly ──
        try:
            page.goto('https://www.werelate.org/wiki/Special:GedcomList', timeout=20000)
            page.wait_for_timeout(2000)
            html = page.content()
            soup = BeautifulSoup(html, 'lxml')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if 'ged' in href.lower():
                    full = urllib.parse.urljoin('https://www.werelate.org', href)
                    add(full, 'werelate', a.get_text(strip=True)[:80])
        except Exception as e:
            print(f"  WeRelate error: {e}")

        # ── Freepages RootsWeb ──
        try:
            page.goto('https://freepages.rootsweb.com/', timeout=20000)
            page.wait_for_timeout(2000)
            html = page.content()
            soup = BeautifulSoup(html, 'lxml')
            for a in soup.find_all('a', href=True):
                if '.ged' in a['href'].lower():
                    full = urllib.parse.urljoin('https://freepages.rootsweb.com/', a['href'])
                    add(full, 'rootsweb', a.get_text(strip=True)[:80])
        except Exception as e:
            print(f"  RootsWeb error: {e}")

        browser.close()

    print(f"  Playwright total so far: {len(found)}")


# ─── 5. Downloader + parser ───────────────────────────────────────────────────

def count_individuals(text):
    return len(re.findall(r'^0 @[^@]+@ INDI', text, re.MULTILINE))

def download_and_parse(entries, limit=50):
    print(f"\n[Download] Fetching up to {limit} files...")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    done  = 0

    for entry in entries[:limit]:
        url   = entry['url']
        fname = re.sub(r'[^\w\-.]', '_', url.split('/')[-1].split('?')[0]) or f'file_{done}.ged'
        if not fname.endswith('.ged'):
            fname += '.ged'
        dest = OUT_DIR / fname

        if dest.exists():
            text = dest.read_text(errors='replace')
        else:
            r = get(url, timeout=45)
            if not r:
                continue
            dest.write_bytes(r.content)
            text = r.content.decode(errors='replace')

        n = count_individuals(text)
        entry['persons']    = n
        entry['local_path'] = str(dest)
        total += n
        done  += 1
        print(f"  {fname[:50]:50s}  {n:>6,} persons")

    print(f"\n  Files: {done} | Total persons: {total:,}")
    return total


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--download',     action='store_true')
    ap.add_argument('--limit',        type=int, default=50)
    ap.add_argument('--skip-ia',      action='store_true')
    ap.add_argument('--skip-github',  action='store_true')
    ap.add_argument('--skip-browser', action='store_true')
    args = ap.parse_args()

    print("=" * 60)
    print("  RootBridge GEDCOM Scraper  (stealth edition)")
    print("=" * 60)

    if not args.skip_ia:
        scrape_internet_archive()

    if not args.skip_github:
        scrape_github()

    scrape_known_pages()

    if not args.skip_browser:
        scrape_with_playwright()

    print(f"\n{'='*60}")
    print(f"  TOTAL .ged URLs found: {len(found)}")
    print(f"{'='*60}")

    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(found, indent=2))
    print(f"  Saved → {INDEX_PATH}")

    if args.download and found:
        total = download_and_parse(found, limit=args.limit)
        INDEX_PATH.write_text(json.dumps(found, indent=2))
        print(f"\n  Grand total persons: {total:,}")

    print("\nDone.")

if __name__ == '__main__':
    main()
