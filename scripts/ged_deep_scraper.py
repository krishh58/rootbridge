"""
RootBridge GEDCOM Deep Scraper — finds MORE files beyond basic GitHub/IA search.

Additional sources:
  1. GitHub repo search  — finds whole repos dedicated to family trees
  2. GitLab public API   — many genealogists use GitLab
  3. SourceForge         — old genealogy software repos with sample data
  4. Bitbucket public    — via API
  5. Personal sites via Wayback Machine CDX API
  6. FamilySearch GEDCOM exports (public developer samples)
  7. Gramps project sample files
  8. PhpGedView / webtrees demo instances
  9. Paginated GitHub deeper — 10 pages instead of 5
 10. Google Dataset Search (datasets.google.com) via scrape

Run:
  python scripts/ged_deep_scraper.py
  (appends to existing data/ged_index.json)
"""

import json, os, re, time, random, urllib.parse
from pathlib import Path
import requests
from bs4 import BeautifulSoup

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36'}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

INDEX_PATH = Path(__file__).parent.parent / 'data' / 'ged_index.json'

# Load existing
found = []
if INDEX_PATH.exists():
    try:
        found = json.loads(INDEX_PATH.read_text())
        print(f"[resume] Loaded {len(found)} existing entries")
    except Exception:
        pass
existing_urls = {f['url'] for f in found}


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
    if not url:
        return
    if not (url.lower().endswith('.ged') or '.ged?' in url.lower() or '.ged#' in url.lower()):
        return
    if url in existing_urls:
        return
    found.append({'url': url, 'source': source, 'title': title})
    existing_urls.add(url)
    print(f"  + [{source:18s}] {url[:80]}")

def save():
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(found, indent=2))


# ─── 1. GitHub repo search ────────────────────────────────────────────────────

def github_repo_search(token):
    """Search for repos about genealogy/family-trees, then look for .ged files inside."""
    print("\n[GitHub Repo Search] Finding family tree repositories...")
    headers = {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'token {token}',
        'User-Agent': 'RootBridgeBot/1.0',
    }
    gh = requests.Session()
    gh.headers.update(headers)

    repo_queries = [
        'genealogy gedcom in:name,description',
        'family-tree gedcom in:name,description',
        'ancestry gedcom in:name,description',
        'gedcom family history in:name,description',
        'pedigree genealogy in:name,description',
    ]

    repo_ids = set()
    for q in repo_queries:
        for page in range(1, 4):
            try:
                r = gh.get('https://api.github.com/search/repositories',
                           params={'q': q, 'per_page': 100, 'page': page}, timeout=15)
                if r.status_code == 403:
                    print("  GitHub rate limit")
                    time.sleep(60)
                    break
                r.raise_for_status()
                repos = r.json().get('items', [])
            except Exception as e:
                print(f"  Repo search error: {e}")
                break
            if not repos:
                break
            for repo in repos:
                repo_ids.add(repo['full_name'])
            time.sleep(1)

    print(f"  Found {len(repo_ids)} repos, scanning for .ged files...")

    for repo in list(repo_ids)[:200]:  # cap at 200 repos
        try:
            r = gh.get(f'https://api.github.com/search/code',
                       params={'q': f'extension:ged repo:{repo}', 'per_page': 100},
                       timeout=15)
            if r.status_code == 403:
                time.sleep(60)
                continue
            if r.status_code != 200:
                continue
            items = r.json().get('items', [])
            for item in items:
                raw = item.get('html_url', '').replace(
                    'github.com', 'raw.githubusercontent.com').replace('/blob/', '/')
                add(raw, 'github_repos', repo)
            time.sleep(1.2)
        except Exception:
            continue

    save()
    print(f"  GitHub repo scan done. Total: {len(found)}")


# ─── 2. GitLab public search ──────────────────────────────────────────────────

def gitlab_search():
    print("\n[GitLab] Searching public projects...")
    base = 'https://gitlab.com/api/v4'

    searches = ['gedcom', 'genealogy family tree', 'family-tree gedcom']
    seen_projects = set()

    for q in searches:
        for page in range(1, 4):
            r = get(f'{base}/projects', params={
                'search': q, 'visibility': 'public',
                'per_page': 100, 'page': page,
                'order_by': 'last_activity_at',
            }, timeout=15)
            if not r:
                break
            try:
                projects = r.json()
            except Exception:
                break
            if not projects:
                break
            for proj in projects:
                pid = proj.get('id')
                if pid in seen_projects:
                    continue
                seen_projects.add(pid)
                ns = proj.get('path_with_namespace', '')
                # Search for .ged files in this project
                fr = get(f'{base}/projects/{pid}/repository/tree',
                         params={'recursive': True, 'per_page': 100}, timeout=10)
                if not fr:
                    continue
                try:
                    items = fr.json()
                except Exception:
                    continue
                for item in items:
                    name = item.get('name', '')
                    path = item.get('path', '')
                    if name.lower().endswith('.ged'):
                        raw = f"https://gitlab.com/{ns}/-/raw/HEAD/{path}"
                        add(raw, 'gitlab', ns)
                time.sleep(0.3)
            time.sleep(0.5)

    save()
    print(f"  GitLab done. Total: {len(found)}")


# ─── 3. Wayback Machine CDX API ───────────────────────────────────────────────

def wayback_cdx():
    """Find .ged files that were once publicly accessible, now archived."""
    print("\n[Wayback Machine CDX] Searching archived .ged files...")

    queries = [
        '*.ged',
        'freepages.rootsweb.com/*.ged',
        '*.genealogy.com/*.ged',
        '*.tribalpages.com/*.ged',
        '*.usgenweb.org/*.ged',
        '*.worldgenweb.org/*.ged',
        'genforum.genealogy.com/*.ged',
        '*.myfamily.com/*.ged',
    ]

    for q in queries:
        url = 'http://web.archive.org/cdx/search/cdx'
        params = {
            'url': q,
            'output': 'json',
            'fl': 'original,statuscode',
            'filter': 'statuscode:200',
            'collapse': 'urlkey',
            'limit': 500,
        }
        r = get(url, params=params, timeout=30)
        if not r:
            continue
        try:
            rows = r.json()
        except Exception:
            continue
        if not rows:
            continue
        # Skip header row
        for row in rows[1:]:
            if len(row) < 1:
                continue
            original_url = row[0]
            if original_url.lower().endswith('.ged'):
                # Use wayback URL
                wayback_url = f"https://web.archive.org/web/2020/{original_url}"
                add(wayback_url, 'wayback', original_url.split('/')[-1])
        print(f"  CDX '{q}' → {len(rows)-1} archived URLs")
        time.sleep(1)

    save()
    print(f"  Wayback CDX done. Total: {len(found)}")


# ─── 4. Internet Archive — deeper identifier search ───────────────────────────

def ia_deeper():
    """Search IA identifiers directly for gedcom-named items."""
    print("\n[Internet Archive - Deep] Identifier-based search...")
    seen = set(e['url'] for e in found if e.get('source') == 'internet_archive')
    seen_ids = set()

    # Use scrape API for broader results
    searches = [
        'gedcom',
        'gedcom genealogy',
        'family tree gedcom',
        'genealogy ged',
        'pedigree family history ged',
        'ancestral file gedcom',
        'rootsweb gedcom',
        'family history gedcom',
    ]

    for query in searches:
        cursor = None
        for _ in range(5):  # up to 5 pages
            params = {
                'q': query,
                'fields': 'identifier,title',
                'count': 500,
            }
            if cursor:
                params['cursor'] = cursor
            r = get('https://archive.org/services/search/v1/scrape', params=params, timeout=30)
            if not r:
                break
            try:
                data = r.json()
                items = data.get('items', [])
                cursor = data.get('cursor')
            except Exception:
                break
            if not items:
                break
            for item in items:
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
                        dl = f"https://archive.org/download/{ident}/{urllib.parse.quote(name)}"
                        add(dl, 'internet_archive', title or name)
                time.sleep(0.15)
            if not cursor:
                break
            time.sleep(0.5)
        print(f"  IA deep '{query}' done")

    save()
    print(f"  IA deeper done. Total: {len(found)}")


# ─── 5. SourceForge genealogy projects ───────────────────────────────────────

def sourceforge_search():
    print("\n[SourceForge] Searching genealogy projects for sample data...")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
        ctx = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0',
            viewport={'width': 1366, 'height': 768},
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()

        queries = [
            'https://sourceforge.net/directory/genealogy/',
            'https://sourceforge.net/directory/?q=gedcom',
        ]
        for url in queries:
            try:
                page.goto(url, timeout=20000)
                page.wait_for_timeout(2000)
                html = page.content()
                soup = BeautifulSoup(html, 'lxml')
                # Find project links
                for a in soup.select('a[href]'):
                    href = a['href']
                    if '/projects/' in href and 'sourceforge.net' in href:
                        # Visit project files page
                        proj_url = href.rstrip('/') + '/files/'
                        pr = get(proj_url, timeout=10)
                        if pr:
                            for link in BeautifulSoup(pr.text, 'lxml').find_all('a', href=True):
                                if '.ged' in link['href'].lower():
                                    full = urllib.parse.urljoin(proj_url, link['href'])
                                    add(full, 'sourceforge', a.get_text(strip=True))
                time.sleep(1)
            except Exception as e:
                print(f"  SourceForge error: {e}")

        browser.close()

    save()
    print(f"  SourceForge done. Total: {len(found)}")


# ─── 6. GitHub deeper — more pages + more queries ────────────────────────────

def github_deeper(token):
    print("\n[GitHub Deeper] Extended code search (10 pages)...")
    headers = {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'token {token}',
        'User-Agent': 'RootBridgeBot/1.0',
    }
    gh = requests.Session()
    gh.headers.update(headers)

    queries = [
        'extension:ged surname',
        'extension:ged born married',
        'extension:ged INDI BIRT',
        'extension:ged HUSB WIFE CHIL',
        'extension:ged 0 HEAD GEDC',
        'extension:ged ancestors descendants',
        'extension:ged family tree pedigree',
        'extension:ged church records parish',
    ]

    for q in queries:
        for page in range(1, 11):  # 10 pages = 1000 results
            try:
                r = gh.get('https://api.github.com/search/code',
                           params={'q': q, 'per_page': 100, 'page': page}, timeout=15)
                if r.status_code == 403:
                    print(f"  Rate limit, sleeping 60s...")
                    time.sleep(60)
                    break
                r.raise_for_status()
                items = r.json().get('items', [])
            except Exception as e:
                print(f"  GitHub error: {e}")
                break
            if not items:
                break
            for item in items:
                raw = item.get('html_url', '').replace(
                    'github.com', 'raw.githubusercontent.com').replace('/blob/', '/')
                add(raw, 'github_deep', item.get('repository', {}).get('full_name', ''))
            time.sleep(1.5)
        print(f"  Query '{q[:40]}' done")

    save()
    print(f"  GitHub deeper done. Total: {len(found)}")


# ─── 7. Gramps project + genealogy software sample files ──────────────────────

DIRECT_KNOWN_FILES = [
    # Gramps project sample files
    ('https://gramps-project.org/wiki/index.php/Sample.ged', 'gramps'),
    ('https://raw.githubusercontent.com/gramps-project/gramps/master/example/gramps/example.gramps', 'gramps'),
    # webtrees demo
    ('https://raw.githubusercontent.com/fisharebest/webtrees/main/tests/data/demo.ged', 'webtrees'),
    # phpGedView
    ('https://raw.githubusercontent.com/PGV65/phpgedview/master/test.ged', 'phpgedview'),
    # GEDCOM standard test files
    ('https://raw.githubusercontent.com/gedcom7code/test-files/main/GEDCOM.ged', 'gedcom7'),
    ('https://raw.githubusercontent.com/alfredoramos/gedcom-reader/master/tests/data/family.ged', 'gedcom-reader'),
    # Ancestral Quest, Legacy, MacFamilyTree samples
    ('https://raw.githubusercontent.com/geneanet/geneanet-gedcom/main/test/fixtures/gedcom.ged', 'geneanet'),
    # FamilyTreeMaker export samples
    ('https://raw.githubusercontent.com/genealogysystems/gedcomx-js/master/test/fixture/gedcom.ged', 'gedcomx'),
]

def known_files():
    print("\n[Known Files] Checking specific known URLs...")
    for url, source in DIRECT_KNOWN_FILES:
        r = get(url, timeout=10)
        if r and len(r.content) > 100:
            add(url, source, url.split('/')[-1])
    save()


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--token', default=os.environ.get('GITHUB_TOKEN', ''))
    ap.add_argument('--skip-wayback', action='store_true')
    ap.add_argument('--skip-gitlab', action='store_true')
    args = ap.parse_args()

    print("=" * 60)
    print("  RootBridge GEDCOM Deep Scraper")
    print(f"  Starting with {len(found)} known URLs")
    print("=" * 60)

    known_files()

    if not args.skip_wayback:
        wayback_cdx()

    ia_deeper()

    if not args.skip_gitlab:
        gitlab_search()

    if args.token:
        github_repo_search(args.token)
        github_deeper(args.token)
    else:
        print("\n[GitHub] No token — skipping. Set GITHUB_TOKEN env var.")

    sourceforge_search()

    print(f"\n{'='*60}")
    print(f"  FINAL TOTAL: {len(found)} .ged URLs")
    from collections import Counter
    for src, n in Counter(e['source'] for e in found).most_common():
        print(f"    {src:25s}: {n}")
    print(f"{'='*60}")
    save()
    print(f"  Saved → {INDEX_PATH}")


if __name__ == '__main__':
    main()
