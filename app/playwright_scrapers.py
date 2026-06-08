"""
Playwright-based scrapers for genealogy sources that block plain HTTP requests.
Each scraper returns a list of result dicts compatible with search_cascade.py.

Sources covered:
  US:       FindAGrave (200M+ memorials, worldwide burials)
  UK:       FreeBMD (all births/marriages/deaths 1837-2006)
  Ireland:  IrishGenealogy.ie (civil registration + church records)
  Italy:    Antenati - Italian National Archives (19th century vital records)
  Poland:   Geneteka (66M+ parish records: Poland, Lithuania, Belarus, Ukraine)
  Norway:   Digitalarkivet (census + church records)
  Germany:  Archion (Protestant church records)
  Germany:  Matricula Online (Catholic church records, Germany/Austria/Poland)

Fallback: if Playwright is unavailable (Railway without chromium, etc.)
  each function returns [] silently — the HTTP cascade in search_cascade.py
  continues as normal.
"""

import time
import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Browser factory — shared stealth context
# ---------------------------------------------------------------------------

_STEALTH_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.0.0 Safari/537.36'
)
_STEALTH_SCRIPT = (
    'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
)


def _make_browser():
    """Launch a stealth Chromium instance. Returns (playwright, browser) or raises."""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=True,
        args=[
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
            '--disable-dev-shm-usage',
        ],
    )
    return pw, browser


def _make_page(browser):
    context = browser.new_context(
        user_agent=_STEALTH_UA,
        viewport={'width': 1280, 'height': 800},
        locale='en-US',
    )
    context.add_init_script(_STEALTH_SCRIPT)
    return context.new_page()


def _safe_text(page) -> str:
    try:
        return page.inner_text('body')
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# FindAGrave — US + international burial records (200M+ memorials)
# ---------------------------------------------------------------------------

def search_findagrave(first: str, last: str, birth_year: int = None,
                      death_year: int = None, country_id: str = None) -> list:
    """
    Search FindAGrave for burial memorials.
    country_id examples: '1'=USA, '75'=Germany, '97'=Ireland, '104'=Italy,
                         '166'=Norway, '225'=UK, '178'=Poland
    """
    try:
        pw, browser = _make_browser()
        try:
            page = _make_page(browser)
            params = f'firstname={first}&lastname={last}'
            if birth_year:
                params += f'&birthyear={birth_year}&birthyearfilter=2'
            if death_year:
                params += f'&deathyear={death_year}&deathyearfilter=2'
            if country_id:
                params += f'&locationId=country_{country_id}'
            url = f'https://www.findagrave.com/memorial/search?{params}'
            page.goto(url, timeout=30000, wait_until='domcontentloaded')
            time.sleep(4)
            text = _safe_text(page)

            results = []
            # Parse memorial blocks — each has name, dates, cemetery, location
            blocks = re.findall(
                r'([\w\s\.\-\']+)\n(?:VETERAN\n)?(?:Flowers[^\n]*\n)?'
                r'(\d{1,2} \w+ \d{4}|\d{4}) – (\d{1,2} \w+ \d{4}|\d{4})\n'
                r'([^\n]+)\n([^\n]+)',
                text
            )
            for b in blocks[:5]:
                name, born, died, cemetery, location = b
                name = name.strip()
                if last.lower() not in name.lower():
                    continue
                results.append({
                    'source': 'findagrave',
                    'record_type': 'burial',
                    'name': name,
                    'birth_date': born.strip(),
                    'death_date': died.strip(),
                    'cemetery': cemetery.strip(),
                    'location': location.strip(),
                    'title': f'{name} — {cemetery.strip()}, {location.strip()}',
                    'url': url,
                })

            # If regex didn't parse, do simple presence check
            if not results and f'{last.lower()}' in text.lower():
                count_match = re.search(r'(\d+) matching record', text)
                if count_match:
                    results.append({
                        'source': 'findagrave',
                        'record_type': 'burial',
                        'title': f'{first} {last} — {count_match.group(1)} memorial(s) found on FindAGrave',
                        'url': url,
                    })
            return results
        finally:
            browser.close()
            pw.stop()
    except Exception as e:
        logger.debug('FindAGrave scrape failed: %s', e)
        return []


# ---------------------------------------------------------------------------
# FreeBMD — UK births, marriages, deaths 1837-2006
# ---------------------------------------------------------------------------

def search_freebmd(first: str, last: str, birth_year: int = None,
                   record_type: str = 'All') -> list:
    """
    Search FreeBMD for UK civil registration records.
    record_type: 'Births', 'Deaths', 'Marriages', or 'All'
    """
    try:
        pw, browser = _make_browser()
        try:
            page = _make_page(browser)
            start = max(1837, (birth_year or 1837) - 5)
            end = (birth_year or 1900) + 10
            url = (
                f'https://www.freebmd.org.uk/cgi/search.pl'
                f'?sur={last}&fnam={first}&type={record_type}'
                f'&start={start}&end={end}&submit=Find'
            )
            page.goto(url, timeout=30000, wait_until='domcontentloaded')
            time.sleep(4)
            text = _safe_text(page)

            results = []
            # FreeBMD results are in a table: Surname | First | District | Vol | Page | Year | Quarter
            rows = re.findall(
                r'([A-Z][a-z]+(?:\s[A-Z][a-z]+)*)\s+'
                r'([A-Z][a-z]+(?:\s[A-Z][a-z]+)*)\s+'
                r'(\d{4})\s+(Jan|Mar|Jun|Sep)',
                text
            )
            for row in rows[:5]:
                surname, firstname, year, quarter = row
                if last.lower() not in surname.lower():
                    continue
                results.append({
                    'source': 'freebmd',
                    'record_type': record_type.lower() if record_type != 'All' else 'vital_record',
                    'name': f'{firstname} {surname}',
                    'year': year,
                    'quarter': quarter,
                    'title': f'{firstname} {surname} — {record_type} {quarter} {year} (FreeBMD UK)',
                    'url': 'https://www.freebmd.org.uk',
                })

            if not results and last.lower() in text.lower() and 'No entries' not in text:
                results.append({
                    'source': 'freebmd',
                    'record_type': 'vital_record',
                    'title': f'{first} {last} — record found in FreeBMD UK civil registration',
                    'url': 'https://www.freebmd.org.uk',
                })
            return results
        finally:
            browser.close()
            pw.stop()
    except Exception as e:
        logger.debug('FreeBMD scrape failed: %s', e)
        return []


# ---------------------------------------------------------------------------
# IrishGenealogy.ie — Ireland civil registration + church records
# ---------------------------------------------------------------------------

def search_irish_genealogy(first: str, last: str, birth_year: int = None,
                           record_type: str = 'B') -> list:
    """
    Search IrishGenealogy.ie.
    record_type: 'B'=Births, 'D'=Deaths, 'M'=Marriages
    """
    try:
        pw, browser = _make_browser()
        try:
            page = _make_page(browser)
            start = max(1845, (birth_year or 1870) - 5)
            end = (birth_year or 1900) + 5
            url = (
                f'https://www.irishgenealogy.ie/en/search-records/civil-registration/'
                f'?surname={last}&firstname={first}&event={record_type}'
                f'&start_year={start}&end_year={end}'
            )
            page.goto(url, timeout=30000, wait_until='domcontentloaded')
            time.sleep(5)
            text = _safe_text(page)

            results = []
            if last.lower() in text.lower() and 'Not Found' not in text:
                count_match = re.search(r'(\d+)\s+result', text, re.IGNORECASE)
                rtype_map = {'B': 'birth', 'D': 'death', 'M': 'marriage'}
                results.append({
                    'source': 'irish_genealogy',
                    'record_type': rtype_map.get(record_type, 'vital_record'),
                    'title': (
                        f'{first} {last} — '
                        f'{count_match.group(1) if count_match else "record(s)"} found '
                        f'in Irish civil registration'
                    ),
                    'url': url,
                })
            return results
        finally:
            browser.close()
            pw.stop()
    except Exception as e:
        logger.debug('IrishGenealogy scrape failed: %s', e)
        return []


# ---------------------------------------------------------------------------
# Antenati — Italian National Archives vital records (19th century)
# ---------------------------------------------------------------------------

def search_antenati(first: str, last: str, birth_year: int = None) -> list:
    """
    Search Antenati (Italian National Archives) for Italian ancestors.
    Best for people born in Italy 1800-1940.
    """
    try:
        pw, browser = _make_browser()
        try:
            page = _make_page(browser)
            query = f'{first} {last}'.strip()
            url = f'https://antenati.cultura.gov.it/en/search/?q={query.replace(" ", "+")}'
            page.goto(url, timeout=30000, wait_until='domcontentloaded')
            time.sleep(5)
            text = _safe_text(page)

            results = []
            if last.lower() in text.lower() and len(text) > 500:
                count_match = re.search(r'(\d+)\s+result', text, re.IGNORECASE)
                results.append({
                    'source': 'antenati',
                    'record_type': 'italian_vital_record',
                    'title': (
                        f'{first} {last} — '
                        f'{count_match.group(1) if count_match else "record(s)"} found '
                        f'in Italian National Archives (Antenati)'
                    ),
                    'url': url,
                })
            return results
        finally:
            browser.close()
            pw.stop()
    except Exception as e:
        logger.debug('Antenati scrape failed: %s', e)
        return []


# ---------------------------------------------------------------------------
# Geneteka — Polish/Lithuanian/Belarusian/Ukrainian parish records (66M+)
# ---------------------------------------------------------------------------

# Region codes for Geneteka
GENETEKA_REGIONS = {
    'mazowieckie': '07ds',
    'wielkopolskie': '08ds',
    'malopolskie': '12ds',
    'slaskie': '14ds',
    'lodzkie': '10ds',
    'kujawsko-pomorskie': '04ds',
    'all': '',
}


def search_geneteka(first: str, last: str, birth_year: int = None,
                    region: str = '') -> list:
    """
    Search Geneteka for Polish, Lithuanian, Belarusian, or Ukrainian ancestors.
    Covers 66M+ parish records. Best for Catholic ancestors from Eastern Europe.
    """
    try:
        pw, browser = _make_browser()
        try:
            page = _make_page(browser)
            from_year = max(1600, (birth_year or 1850) - 10) if birth_year else 1600
            to_year = (birth_year or 1900) + 10
            url = (
                f'https://geneteka.genealodzy.pl/index.php'
                f'?search_type=person&lang=eng&bdm=B'
                f'&w={region}&rid='
                f'&search_lastname={last}&search_name={first}'
                f'&from_year={from_year}&to_year={to_year}&exac=1'
            )
            page.goto(url, timeout=30000, wait_until='domcontentloaded')
            time.sleep(5)
            text = _safe_text(page)

            results = []
            if last.lower() in text.lower() and 'Total' in text:
                count_match = re.search(r'Entries\s*\n([\d\s]+)', text)
                results.append({
                    'source': 'geneteka',
                    'record_type': 'parish_record',
                    'title': (
                        f'{first} {last} — record found in Geneteka '
                        f'(Polish/East European parish records)'
                    ),
                    'url': url,
                })
            return results
        finally:
            browser.close()
            pw.stop()
    except Exception as e:
        logger.debug('Geneteka scrape failed: %s', e)
        return []


# ---------------------------------------------------------------------------
# Digitalarkivet — Norwegian census + church records
# ---------------------------------------------------------------------------

def search_digitalarkivet(first: str, last: str, birth_year: int = None) -> list:
    """
    Search Digitalarkivet for Norwegian ancestors.
    Covers census records, church books (baptisms, marriages, burials).
    """
    try:
        pw, browser = _make_browser()
        try:
            page = _make_page(browser)
            url = (
                f'https://www.digitalarkivet.no/en/content/censuses/search'
                f'?fornavn={first}&etternavn={last}'
                + (f'&foedselsaar={birth_year}' if birth_year else '')
            )
            page.goto(url, timeout=30000, wait_until='domcontentloaded')
            time.sleep(5)
            text = _safe_text(page)

            results = []
            if last.lower() in text.lower() and len(text) > 300:
                count_match = re.search(r'(\d+)\s+(?:result|hit|person)', text, re.IGNORECASE)
                results.append({
                    'source': 'digitalarkivet',
                    'record_type': 'norwegian_record',
                    'title': (
                        f'{first} {last} — '
                        f'{count_match.group(1) if count_match else "record(s)"} found '
                        f'in Norwegian Digitalarkivet'
                    ),
                    'url': url,
                })
            return results
        finally:
            browser.close()
            pw.stop()
    except Exception as e:
        logger.debug('Digitalarkivet scrape failed: %s', e)
        return []


# ---------------------------------------------------------------------------
# Archion — German Protestant church records
# ---------------------------------------------------------------------------

def search_archion(first: str, last: str, birth_year: int = None) -> list:
    """
    Search Archion for German Protestant (Evangelical) church records.
    Covers baptisms, marriages, and burials from German Protestant parishes.
    """
    try:
        pw, browser = _make_browser()
        try:
            page = _make_page(browser)
            url = 'https://www.archion.de/en/browse/'
            page.goto(url, timeout=30000, wait_until='domcontentloaded')
            time.sleep(3)
            text = _safe_text(page)

            results = []
            # Archion requires navigation — just confirm it's accessible and return pointer
            if len(text) > 200 and 'archion' in text.lower():
                results.append({
                    'source': 'archion',
                    'record_type': 'german_protestant_church',
                    'title': (
                        f'{first} {last} — search German Protestant church records '
                        f'on Archion (baptisms, marriages, burials)'
                    ),
                    'url': (
                        f'https://www.archion.de/en/browse/?'
                        f'type=0&query={first}+{last}'
                    ),
                })
            return results
        finally:
            browser.close()
            pw.stop()
    except Exception as e:
        logger.debug('Archion scrape failed: %s', e)
        return []


# ---------------------------------------------------------------------------
# Matricula Online — Catholic church records Germany/Austria/Poland
# ---------------------------------------------------------------------------

def search_matricula(first: str, last: str, birth_year: int = None,
                     country: str = '') -> list:
    """
    Search Matricula Online for Catholic church records.
    Covers Germany, Austria, Poland, and parts of Eastern Europe.
    Country codes: 'AT'=Austria, 'DE'=Germany, 'PL'=Poland
    """
    try:
        pw, browser = _make_browser()
        try:
            page = _make_page(browser)
            url = 'https://matricula-online.eu/browse.php'
            if country:
                url += f'?archiv={country}'
            page.goto(url, timeout=30000, wait_until='domcontentloaded')
            time.sleep(3)
            text = _safe_text(page)

            results = []
            if len(text) > 200:
                country_name = {'AT': 'Austria', 'DE': 'Germany', 'PL': 'Poland'}.get(country, 'Germany/Austria/Poland')
                results.append({
                    'source': 'matricula',
                    'record_type': 'catholic_church_record',
                    'title': (
                        f'{first} {last} — search Catholic church records '
                        f'({country_name}) on Matricula Online'
                    ),
                    'url': url,
                })
            return results
        finally:
            browser.close()
            pw.stop()
    except Exception as e:
        logger.debug('Matricula scrape failed: %s', e)
        return []


# ---------------------------------------------------------------------------
# Obituary search via DuckDuckGo (Playwright browser — bypasses bot detection)
# ---------------------------------------------------------------------------

_OBIT_SIGNALS = frozenset([
    'obituary', 'passed', 'died', 'death', 'funeral', 'memorial',
    'survived by', 'visitation', 'interment', 'graveside',
])
_OBIT_DOMAINS = frozenset([
    'legacy.com', 'findagrave.com', 'dignitymemorial.com',
    'harrymckneely.com', 'tributes.com', 'forevermissed.com',
    'obits.', 'obituaries.',
])


def search_obituaries(first: str, last: str, birth_year: int = None,
                      birth_place: str = '') -> list:
    """
    Search Bing for obituaries using a real Chromium browser.
    Uses Bing (not DuckDuckGo) — better results and less server-IP blocking.
    Especially effective for deaths in the last 10-20 years.
    """
    try:
        pw, browser = _make_browser()
        try:
            page = _make_page(browser)
            name_q = f'"{first} {last}"' if first else f'"{last}"'
            parts = [name_q, 'obituary']
            if birth_place:
                parts.append(birth_place)
            query = ' '.join(parts)

            import urllib.parse
            url = f'https://www.bing.com/search?q={urllib.parse.quote(query)}'
            page.goto(url, timeout=30000, wait_until='domcontentloaded')
            time.sleep(2)

            results = []
            # Bing result structure: li.b_algo contains h2 > a and p.b_lineclamp2
            result_els = page.query_selector_all('li.b_algo')
            for el in result_els[:10]:
                try:
                    title_el = el.query_selector('h2 a')
                    snip_el  = el.query_selector('p, .b_lineclamp2, .b_caption p')
                    if not title_el:
                        continue
                    title_text = title_el.inner_text().strip()
                    result_url = title_el.get_attribute('href') or ''
                    snippet = snip_el.inner_text().strip() if snip_el else ''
                    combined = (title_text + ' ' + snippet).lower()

                    if last.lower() not in combined:
                        continue
                    has_signal = any(s in combined for s in _OBIT_SIGNALS)
                    has_domain = any(d in result_url.lower() for d in _OBIT_DOMAINS)
                    if not (has_signal or has_domain):
                        continue

                    results.append({
                        'source': 'obituary_web',
                        'record_type': 'obituary',
                        'title': title_text,
                        'snippet': snippet,
                        'url': result_url,
                        'name': f'{first} {last}',
                    })
                except Exception:
                    continue

            return results[:4]
        finally:
            browser.close()
            pw.stop()
    except Exception as e:
        logger.debug('Obituary Bing search failed: %s', e)
        return []


# ---------------------------------------------------------------------------
# VA Nationwide Gravesite Locator — all US military burials (free federal data)
# ---------------------------------------------------------------------------

def search_va_gravesite(first: str, last: str, birth_year: int = None) -> list:
    """
    Search the VA Nationwide Gravesite Locator for US military burials.
    Returns name, branch, war period, birth/death dates, cemetery, address.
    Covers all national cemeteries and many state veterans cemeteries.
    """
    try:
        pw, browser = _make_browser()
        try:
            page = _make_page(browser)
            page.goto('https://gravelocator.cem.va.gov/', timeout=30000,
                      wait_until='domcontentloaded')
            time.sleep(3)

            page.fill('input#lname', last)
            page.fill('input#fname', first)
            if birth_year:
                page.fill('input#birth_yy', str(birth_year))
            time.sleep(0.5)

            page.click('button:has-text("Search")')
            time.sleep(5)
            text = _safe_text(page)

            if last.upper() not in text.upper():
                return []

            results = []
            # Parse one or more result blocks — each block has fixed label:value lines
            blocks = re.split(r'Result Number', text)
            for block in blocks[1:]:  # skip header before first result
                name_m   = re.search(r'Name:\s*([^\n]+)', block)
                branch_m = re.search(r'Rank[^\n]*:\s*([^\n]+)', block)
                war_m    = re.search(r'War Period:\s*([^\n]+)', block)
                dob_m    = re.search(r'Date of Birth:\s*([\d/]+)', block)
                dod_m    = re.search(r'Date of Death:\s*([\d/]+)', block)
                cem_m    = re.search(r'Cemetery:\s*([^\n]+)', block)
                addr_m   = re.search(r'Cemetery Address:\s*([^\n]+)', block)

                if not name_m:
                    continue
                name     = name_m.group(1).strip()
                if last.upper() not in name.upper():
                    continue

                branch  = branch_m.group(1).strip()  if branch_m  else ''
                war     = war_m.group(1).strip()      if war_m     else ''
                dob     = dob_m.group(1).strip()      if dob_m     else ''
                dod     = dod_m.group(1).strip()      if dod_m     else ''
                cemetery= cem_m.group(1).strip()      if cem_m     else 'US National Cemetery'
                address = addr_m.group(1).strip()     if addr_m    else ''

                results.append({
                    'source':      'va_gravesite',
                    'record_type': 'military_burial',
                    'name':        name,
                    'birth_date':  dob,
                    'death_date':  dod,
                    'cemetery':    cemetery,
                    'address':     address,
                    'branch':      branch,
                    'war':         war,
                    'title':       f'{name} — {cemetery} · {branch} · {war}',
                    'url':         'https://gravelocator.cem.va.gov/',
                })

            return results
        finally:
            browser.close()
            pw.stop()
    except Exception as e:
        logger.debug('VA Gravesite search failed: %s', e)
        return []


# ---------------------------------------------------------------------------
# Reverse search — find an ancestor by searching for a known family member
# ---------------------------------------------------------------------------

def search_by_descendant(known_first: str, known_last: str,
                         known_location: str = '', relationship: str = 'father') -> list:
    """
    Find an ancestor by searching for a known living/recent family member.
    Strategy: obituaries always list survivors — search for the known person
    as a survivor to find their parent's/grandparent's obituary.

    relationship: 'father', 'mother', 'grandfather', 'grandmother',
                  'great-grandfather', 'great-grandmother', 'uncle', 'aunt'
    """
    try:
        import urllib.parse
        pw, browser = _make_browser()
        try:
            page = _make_page(browser)

            # Build query — search for known person as a survivor in obituaries
            name_q = f'"{known_first} {known_last}"'
            parts = [name_q, 'obituary', 'survived']
            if known_location:
                parts.append(known_location)
            query = ' '.join(parts)

            url = f'https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}'
            page.goto(url, timeout=30000, wait_until='domcontentloaded')
            time.sleep(3)

            result_els = page.query_selector_all('.result')
            candidates = []

            for el in result_els[:6]:
                try:
                    title_el = el.query_selector('.result__title')
                    url_el = el.query_selector('.result__url')
                    snip_el = el.query_selector('.result__snippet')
                    if not (title_el and url_el):
                        continue
                    title = title_el.inner_text().strip()
                    result_url = 'https://' + url_el.inner_text().strip()
                    snippet = snip_el.inner_text().strip() if snip_el else ''
                    combined = (title + ' ' + snippet).lower()

                    # Must look like an obituary
                    has_signal = any(s in combined for s in _OBIT_SIGNALS)
                    has_domain = any(d in result_url.lower() for d in _OBIT_DOMAINS)
                    if not (has_signal or has_domain):
                        continue

                    # Known person's last name should appear
                    if known_last.lower() not in combined:
                        continue

                    candidates.append({
                        'title': title,
                        'url': result_url,
                        'snippet': snippet,
                    })
                except Exception:
                    continue

            if not candidates:
                return []

            # Scrape the top candidate obituary for full text
            best = candidates[0]
            try:
                page.goto(best['url'], timeout=30000, wait_until='domcontentloaded')
                time.sleep(3)
                full_text = _safe_text(page)[:4000]
            except Exception:
                full_text = best['snippet']

            return [{
                'source': 'reverse_search',
                'record_type': 'obituary',
                'title': best['title'],
                'url': best['url'],
                'snippet': best['snippet'],
                'full_text': full_text,
                'relationship': relationship,
                'known_person': f'{known_first} {known_last}',
            }]

        finally:
            browser.close()
            pw.stop()
    except Exception as e:
        logger.debug('Reverse search failed: %s', e)
        return []


# ---------------------------------------------------------------------------
# Smart router — picks sources based on name origin hints
# ---------------------------------------------------------------------------

# Surname suffixes that suggest geographic origin
_ORIGIN_HINTS = {
    'irish':   {'o\'', 'mc', 'mac', 'mur', 'kel', 'sul', 'bri', 'rya', 'nol'},
    'italian': {'ini', 'ino', 'elli', 'ello', 'etti', 'etto', 'one', 'oni',
                'ese', 'osi', 'osi', 'ano', 'ani'},
    'polish':  {'ski', 'ska', 'cki', 'cka', 'wski', 'wska', 'czyk', 'czak',
                'iak', 'iak', 'wicz', 'owicz'},
    'norwegian': {'sen', 'son', 'dahl', 'berg', 'fjord', 'vik', 'dal', 'ness'},
    'german':  {'mann', 'stein', 'berg', 'burg', 'feld', 'bauer', 'meyer',
                'müller', 'muller', 'schneider', 'fischer'},
    'british': {'smith', 'jones', 'brown', 'taylor', 'wilson', 'davies',
                'evans', 'thomas', 'roberts'},
}


def _guess_origins(last: str) -> list:
    last_lower = last.lower()
    origins = []
    for origin, hints in _ORIGIN_HINTS.items():
        if any(last_lower.endswith(h) or last_lower.startswith(h) for h in hints):
            origins.append(origin)
    return origins or ['us']  # default to US search


def search_all_playwright(first: str, last: str, birth_year: int = None,
                          birth_place: str = '', country_hint: str = '') -> list:
    """
    Run all applicable Playwright scrapers based on name origin and country hint.
    Always searches FindAGrave. Additional sources selected by surname analysis.

    country_hint: 'ireland', 'italy', 'poland', 'norway', 'germany',
                  'uk', 'us', or '' (auto-detect)
    """
    results = []

    # Always search FindAGrave — covers US and has international records
    results += search_findagrave(first, last, birth_year)

    # Always search for obituaries — best source for recent deaths (last 10-20 yrs)
    results += search_obituaries(first, last, birth_year, birth_place)

    origins = [country_hint.lower()] if country_hint else _guess_origins(last)

    if 'ireland' in origins or 'irish' in origins:
        results += search_irish_genealogy(first, last, birth_year, 'B')
        results += search_irish_genealogy(first, last, birth_year, 'D')

    if 'italy' in origins or 'italian' in origins:
        results += search_antenati(first, last, birth_year)
        results += search_findagrave(first, last, birth_year, country_id='104')

    if 'poland' in origins or 'polish' in origins:
        results += search_geneteka(first, last, birth_year)

    if 'norway' in origins or 'norwegian' in origins:
        results += search_digitalarkivet(first, last, birth_year)
        results += search_findagrave(first, last, birth_year, country_id='166')

    if 'germany' in origins or 'german' in origins:
        results += search_archion(first, last, birth_year)
        results += search_matricula(first, last, birth_year, 'DE')
        results += search_findagrave(first, last, birth_year, country_id='75')

    if 'uk' in origins or 'british' in origins or 'british' in origins:
        results += search_freebmd(first, last, birth_year, 'All')
        results += search_findagrave(first, last, birth_year, country_id='225')

    # UK search also for Irish surnames (many Irish records in FreeBMD)
    if 'irish' in origins:
        results += search_freebmd(first, last, birth_year, 'All')

    return results
