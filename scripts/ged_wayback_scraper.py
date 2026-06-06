"""
RootBridge — Wayback Machine Pre-Lockdown GEDCOM Scraper

Targets the Ancestry/RootsWeb ecosystem sites that had publicly accessible
GEDCOM files before they locked down (roughly 1996–2017):

  - RootsWeb WorldConnect       wc.rootsweb.com, worldconnect.rootsweb.com
  - Genealogy.com               shut down 2014, huge GEDCOM library
  - FamilyTreeMaker community   familytreemaker.genealogy.com
  - RootsWeb free pages         freepages.*.rootsweb.com
  - USGenWeb                    *.usgenweb.org / *.usgenweb.net
  - WorldGenWeb                 *.worldgenweb.org
  - GenealogyMagazine           genealogymagazine.com
  - Rootsweb mailing list files lists.rootsweb.com
  - MyFamily.com                myfamily.com (Ancestry spin-off, defunct)
  - TribalPages                 tribalpages.com
  - GeneaNet early              geneanet.org (pre-paywall public exports)
  - GenForum                    genforum.genealogy.com
  - RootsPoint                  rootspoint.com
  - One Great Family            onegreatfamily.com (defunct 2013)
  - Family Tree Legends         familytreelegends.com
  - Kindred Konnections          kindredkonnections.com
  - Individual state GenWebs    (al,ak,az,...,wy).usgenweb.org
  - County GenWeb pages         county pages under each state genweb

Each query hits the Wayback CDX API with limit=2000 to get maximum coverage.
Wayback URLs are used so files are always accessible even if the original is gone.

Run:
  python scripts/ged_wayback_scraper.py [--limit 2000] [--dry-run]
  (appends to existing data/ged_index.json)
"""

import json, time, urllib.parse, argparse
from pathlib import Path
import requests

INDEX_PATH = Path(__file__).parent.parent / 'data' / 'ged_index.json'
CDX_URL    = 'http://web.archive.org/cdx/search/cdx'
HEADERS    = {'User-Agent': 'Mozilla/5.0 (compatible; RootBridgeBot/1.0)'}

# ─── load existing ────────────────────────────────────────────────────────────

found = []
if INDEX_PATH.exists():
    try:
        found = json.loads(INDEX_PATH.read_text())
        print(f"[resume] Loaded {len(found)} existing entries")
    except Exception:
        pass
existing_urls = {f['url'] for f in found}


def add(url, source, title=''):
    url = url.strip()
    if not url or url in existing_urls:
        return False
    if not (url.lower().endswith('.ged') or '.ged?' in url.lower()):
        return False
    found.append({'url': url, 'source': source, 'title': title})
    existing_urls.add(url)
    print(f"  + [{source}] {url[:90]}")
    return True


def save():
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(found, indent=2))


# ─── CDX query ────────────────────────────────────────────────────────────────

def cdx_query(url_pattern: str, source: str, limit: int = 2000, dry_run: bool = False) -> int:
    """Query Wayback CDX for a URL pattern. Returns number of new URLs added."""
    params = {
        'url':      url_pattern,
        'output':   'json',
        'fl':       'original,timestamp,statuscode',
        'filter':   'statuscode:200',
        'collapse': 'urlkey',
        'limit':    limit,
    }
    rows = None
    for attempt in range(3):
        try:
            r = requests.get(CDX_URL, params=params, headers=HEADERS, timeout=90)
            if r.status_code == 503:
                wait = 30 * (attempt + 1)
                print(f"  [503] {url_pattern} — waiting {wait}s before retry {attempt+1}/3")
                time.sleep(wait)
                continue
            r.raise_for_status()
            rows = r.json()
            break
        except requests.exceptions.Timeout:
            wait = 20 * (attempt + 1)
            print(f"  [timeout] {url_pattern} — waiting {wait}s before retry {attempt+1}/3")
            time.sleep(wait)
        except Exception as e:
            print(f"  [err] {url_pattern}: {e}")
            break
    if rows is None:
        print(f"  [skip] {url_pattern} — failed after 3 attempts")
        return 0

    if not rows or len(rows) <= 1:
        print(f"  CDX '{url_pattern}' → 0 results")
        return 0

    new = 0
    for row in rows[1:]:  # skip header
        if len(row) < 3:
            continue
        original_url = row[0]
        timestamp    = row[1]
        # strip query string before checking extension
        clean_url = original_url.split('?')[0].split('#')[0]
        if not clean_url.lower().endswith('.ged'):
            continue
        # Use the best available snapshot (prefer most recent pre-lockdown)
        wayback_url = f"https://web.archive.org/web/{timestamp}/{original_url}"
        title = original_url.split('/')[-1]
        if not dry_run:
            if add(wayback_url, source, title):
                new += 1
        else:
            new += 1

    print(f"  CDX '{url_pattern}' → {len(rows)-1} archived, {new} new .ged URLs")
    return new


# ─── target domains ──────────────────────────────────────────────────────────

def build_queries():
    """Return list of (url_pattern, source_label) tuples."""
    queries = []

    # ── RootsWeb WorldConnect — the biggest pre-lockdown GEDCOM host ──────────
    queries += [
        ('wc.rootsweb.com/*.ged',                        'rootsweb_worldconnect'),
        ('worldconnect.rootsweb.com/*.ged',              'rootsweb_worldconnect'),
        ('worldconnect.genealogy.rootsweb.com/*.ged',    'rootsweb_worldconnect'),
    ]

    # ── RootsWeb free personal pages ─────────────────────────────────────────
    queries += [
        ('freepages.genealogy.rootsweb.com/*.ged',       'rootsweb_freepages'),
        ('freepages.rootsweb.com/*.ged',                 'rootsweb_freepages'),
        ('freepages.rootsweb.ancestry.com/*.ged',        'rootsweb_freepages'),
        ('home.rootsweb.com/*.ged',                      'rootsweb_freepages'),
        ('homepages.rootsweb.com/*.ged',                 'rootsweb_freepages'),
        ('members.rootsweb.com/*.ged',                   'rootsweb_freepages'),
        ('www.rootsweb.com/*.ged',                       'rootsweb_main'),
        ('rootsweb.com/*.ged',                           'rootsweb_main'),
    ]

    # ── Genealogy.com (shut down 2014) ────────────────────────────────────────
    queries += [
        ('www.genealogy.com/*.ged',                      'genealogy_com'),
        ('genealogy.com/*.ged',                          'genealogy_com'),
        ('familytreemaker.genealogy.com/*.ged',          'ftm_community'),
        ('genforum.genealogy.com/*.ged',                 'genforum'),
        ('searches.rootsweb.com/*.ged',                  'rootsweb_search'),
    ]

    # ── USGenWeb state & county pages ─────────────────────────────────────────
    us_states = [
        'al','ak','az','ar','ca','co','ct','de','fl','ga',
        'hi','id','il','in','ia','ks','ky','la','me','md',
        'ma','mi','mn','ms','mo','mt','ne','nv','nh','nj',
        'nm','ny','nc','nd','oh','ok','or','pa','ri','sc',
        'sd','tn','tx','ut','vt','va','wa','wv','wi','wy',
    ]
    for st in us_states:
        queries.append((f'{st}.usgenweb.org/*.ged',      'usgenweb'))
        queries.append((f'www.{st}genweb.org/*.ged',     'usgenweb'))

    queries += [
        ('*.usgenweb.org/*.ged',                         'usgenweb'),
        ('*.usgenweb.net/*.ged',                         'usgenweb'),
        ('usgenweb.org/*.ged',                           'usgenweb'),
        ('files.usgenweb.org/*.ged',                     'usgenweb'),
    ]

    # ── WorldGenWeb (international) ───────────────────────────────────────────
    queries += [
        ('*.worldgenweb.org/*.ged',                      'worldgenweb'),
        ('worldgenweb.org/*.ged',                        'worldgenweb'),
        ('www.worldgenweb.org/*.ged',                    'worldgenweb'),
    ]

    # ── MyFamily.com (Ancestry spin-off, defunct ~2012) ───────────────────────
    queries += [
        ('*.myfamily.com/*.ged',                         'myfamily_com'),
        ('www.myfamily.com/*.ged',                       'myfamily_com'),
    ]

    # ── TribalPages ───────────────────────────────────────────────────────────
    queries += [
        ('*.tribalpages.com/*.ged',                      'tribalpages'),
        ('www.tribalpages.com/*.ged',                    'tribalpages'),
    ]

    # ── One Great Family (defunct 2013) ───────────────────────────────────────
    queries += [
        ('www.onegreatfamily.com/*.ged',                 'onegreatfamily'),
        ('onegreatfamily.com/*.ged',                     'onegreatfamily'),
    ]

    # ── GeneaNet early public exports ─────────────────────────────────────────
    queries += [
        ('www.geneanet.org/*.ged',                       'geneanet'),
        ('geneanet.org/*.ged',                           'geneanet'),
    ]

    # ── RootsPoint ────────────────────────────────────────────────────────────
    queries += [
        ('www.rootspoint.com/*.ged',                     'rootspoint'),
        ('rootspoint.com/*.ged',                         'rootspoint'),
    ]

    # ── Kindred Konnections ───────────────────────────────────────────────────
    queries += [
        ('*.kindredkonnections.com/*.ged',               'kindredkonnections'),
    ]

    # ── Family Tree Legends ───────────────────────────────────────────────────
    queries += [
        ('*.familytreelegends.com/*.ged',                'familytreelegends'),
    ]

    # ── RootsWeb mailing list archives ────────────────────────────────────────
    queries += [
        ('lists.rootsweb.com/*.ged',                     'rootsweb_lists'),
        ('listsearches.rootsweb.com/*.ged',              'rootsweb_lists'),
    ]

    # ── WeRelate (pre-lockdown) ───────────────────────────────────────────────
    queries += [
        ('www.werelate.org/*.ged',                       'werelate'),
    ]

    # ── County/surname specific GenWeb pages ──────────────────────────────────
    queries += [
        ('*.rootsweb.ancestry.com/*.ged',                'rootsweb_ancestry'),
        ('home.rootsweb.ancestry.com/*.ged',             'rootsweb_ancestry'),
    ]

    # ── FamilySearch pre-lockdown public GEDCOM files ────────────────────────
    # FamilySearch distributed Ancestral File + Pedigree Resource File as
    # public GEDCOM downloads for years — these are NOT API-restricted.
    queries += [
        ('familysearch.org/*.ged',                            'familysearch_old'),
        ('www.familysearch.org/*.ged',                        'familysearch_old'),
        ('familysearch.org/*/gedcom*',                        'familysearch_old'),
        # Ancestral File — distributed on CD, widely mirrored
        ('*.familysearch.org/*.ged',                          'familysearch_old'),
        # Old LDS/FamilySearch distribution servers
        ('www.lds.org/*.ged',                                 'lds_org'),
        ('lds.org/*.ged',                                     'lds_org'),
        ('www.familyhistory.byu.edu/*.ged',                   'byu_familyhistory'),
        ('familyhistory.byu.edu/*.ged',                       'byu_familyhistory'),
        # FamilySearch developer/sample files
        ('developers.familysearch.org/*.ged',                 'familysearch_dev'),
        ('integration.familysearch.org/*.ged',                'familysearch_dev'),
    ]

    # ── Internet Archive mirrors of FamilySearch/LDS GEDCOM distributions ────
    # These are handled separately via ia_familysearch() below, but Wayback
    # may also have direct links to .ged files that were served from FS servers.

    # ── University and library genealogy collections ──────────────────────────
    queries += [
        ('www.lib.utah.edu/*.ged',                       'university_library'),
        ('www.newenglandancestors.org/*.ged',            'newengland_ancestors'),
        ('www.nehgs.org/*.ged',                          'nehgs'),
        ('www.archives.gov/*.ged',                       'us_national_archives'),
        ('www.fold3.com/*.ged',                          'fold3'),
        ('www.footnote.com/*.ged',                       'footnote_com'),        # pre-Fold3
    ]

    # ── General wildcard — any .ged served from personal/hobby sites ──────────
    queries += [
        ('*.angelfire.com/*.ged',                        'angelfire'),
        ('*.tripod.com/*.ged',                           'tripod'),
        ('*.geocities.com/*.ged',                        'geocities'),       # archived by Wayback
        ('*.fortunecity.com/*.ged',                      'fortunecity'),
        ('*.homestead.com/*.ged',                        'homestead'),
        ('members.tripod.com/*.ged',                     'tripod'),
    ]

    return queries


# ─── Internet Archive: FamilySearch/LDS GEDCOM distributions ─────────────────

def ia_familysearch(limit: int = 500, dry_run: bool = False) -> int:
    """
    Search Internet Archive specifically for Ancestral File, Pedigree Resource
    File, and other FamilySearch/LDS public GEDCOM distributions.
    These were freely distributed on CD and widely uploaded to IA.
    """
    print('\n[IA FamilySearch] Searching for Ancestral File + PRF GEDCOM mirrors...')

    searches = [
        'ancestral file gedcom lds',
        'pedigree resource file gedcom',
        'familysearch gedcom download',
        'IGI international genealogical index gedcom',
        'LDS church genealogy gedcom',
        'family history library gedcom',
        'ancestral file lds family history',
        'familysearch pedigree ged',
        'mormon genealogy gedcom',
        'salt lake genealogy gedcom',
        'familysearch extracted records gedcom',
        'vital records gedcom familysearch',
    ]

    new_total = 0
    for query in searches:
        try:
            r = requests.get(
                'https://archive.org/services/search/v1/scrape',
                params={'q': query, 'fields': 'identifier,title', 'count': limit},
                timeout=30,
                headers=HEADERS,
            )
            items = r.json().get('items', [])
        except Exception as e:
            print(f'  [err] {query}: {e}')
            continue

        for item in items:
            ident = item.get('identifier', '')
            title = item.get('title', ident)
            try:
                fr = requests.get(
                    f'https://archive.org/metadata/{ident}/files',
                    timeout=10, headers=HEADERS,
                )
                files = fr.json().get('result', [])
            except Exception:
                continue
            for f in files:
                name = f.get('name', '')
                if name.lower().endswith('.ged'):
                    import urllib.parse
                    url = f"https://archive.org/download/{ident}/{urllib.parse.quote(name)}"
                    if not dry_run:
                        if add(url, 'ia_familysearch', title):
                            new_total += 1
                    else:
                        new_total += 1
            time.sleep(0.1)

        print(f"  IA '{query}' → {len(items)} items checked")
        time.sleep(0.5)

    print(f'  IA FamilySearch done. {new_total} new URLs found.')
    return new_total


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit',    type=int, default=2000, help='CDX results per query')
    ap.add_argument('--dry-run',  action='store_true',    help='Count only, no writes')
    ap.add_argument('--source',   default='',             help='Filter to specific source label')
    args = ap.parse_args()

    queries = build_queries()
    if args.source:
        queries = [(p, s) for p, s in queries if args.source in s]

    print('=' * 65)
    print('  RootBridge Wayback Pre-Lockdown GEDCOM Scraper')
    print(f'  {len(queries)} CDX queries | limit={args.limit} per query')
    print(f'  Starting with {len(found)} known URLs')
    print('=' * 65)

    total_new = 0

    # Internet Archive FamilySearch/Ancestral File search first (no rate limit issues)
    if not args.source or 'familysearch' in args.source or 'ia' in args.source:
        n = ia_familysearch(limit=args.limit, dry_run=args.dry_run)
        total_new += n
        if n > 0 and not args.dry_run:
            save()

    # Wayback CDX queries
    for pattern, source in queries:
        n = cdx_query(pattern, source, limit=args.limit, dry_run=args.dry_run)
        total_new += n
        if n > 0 and not args.dry_run:
            save()
        time.sleep(0.8)  # be polite to Wayback

    if not args.dry_run:
        save()

    print()
    print('=' * 65)
    print(f'  NEW URLs added : {total_new}')
    print(f'  Index total    : {len(found)}')
    print('=' * 65)
    if not args.dry_run:
        print(f'  Saved → {INDEX_PATH}')


if __name__ == '__main__':
    main()
