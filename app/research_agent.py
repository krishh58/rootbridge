"""
AI-driven genealogy research agent.

Uses Claude via OpenRouter (OpenAI-compatible tool_calls format).
Streams findings back as SSE events as they are discovered.

For living people (no death year, born >= 1930) a separate lightweight
path scrapes Spokeo + Radaris people-search directories instead of
FindAGrave/BillionGraves which only cover the deceased.
"""
import json
import logging
import re

import requests as req_lib

logger = logging.getLogger(__name__)

OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'
AGENT_MODEL    = 'anthropic/claude-3.5-haiku'
MAX_TURNS      = 14
MAX_PAGE_CHARS = 4000
LIVING_BIRTH_YEAR_THRESHOLD = 1930  # born after this → try living-person path first


SYSTEM_PROMPT = """You are a genealogy research agent. You have tools to browse the web.
Given a person's details, research them thoroughly.

IMPORTANT — always use full URLs, never plain text queries.
DO NOT use Google — it blocks bots. Use DuckDuckGo instead.
CRITICAL — birth year validation: If the target person has a known birth year, SKIP any memorial or record where the birth year differs by more than 20 years. Report ONLY the person who matches. A birth year of 1879 is NOT a match for a target born in 1865.

URL formats to use:
- DuckDuckGo search: https://duckduckgo.com/html/?q=Orville+Cleckner+Henderson+genealogy
- DuckDuckGo obituary: https://duckduckgo.com/html/?q=%22Orville+Cleckner+Henderson%22+obituary
- FindAGrave with middle name: https://www.findagrave.com/memorial/search?firstname=Orville+Cleckner&lastname=Henderson
- FindAGrave first name only: https://www.findagrave.com/memorial/search?firstname=Orville&lastname=Henderson
- FindAGrave individual memorial: use the real URL extracted from [MEMORIAL LINKS ON THIS PAGE]
- BillionGraves: https://billiongraves.com/search/results?firstname=Orville&lastname=Henderson
- Ancestry public: https://www.ancestry.com/search/?name=Orville_Henderson

Strategy:
1. Search FindAGrave with full name including middle name
   - The results page shows a list. Read it carefully for memorial URLs like /memorial/123456/name
   - At the bottom of the page text you will see [MEMORIAL LINKS ON THIS PAGE] — pick the URL matching the right person and browse it to get birth year, parents, spouse
2. DuckDuckGo search with full name
3. DuckDuckGo obituary search with name in quotes
4. BillionGraves search
5. Follow any memorial or obituary pages that look like a match

Rules:
- ALWAYS use real https:// URLs — never plain text
- Include middle name in searches — it dramatically narrows results
- CRITICAL: FindAGrave search pages only show partial data. You MUST follow individual /memorial/ links to get birth year and parents
- Look for patterns like "findagrave.com/memorial/123456" in the text and browse those URLs
- Use report_finding for each confirmed fact with the source URL
- Only report facts you actually read on a page — do not guess
- Stop after 12 sources or when you have birth year + death year + parents
- End by calling research_complete"""


def _browse(url: str, page) -> str:
    try:
        page.goto(url, timeout=18000, wait_until='domcontentloaded')
        text = page.inner_text('body')
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
        # For FindAGrave pages, inject full memorial URLs so the agent can follow them
        # Links are appended AFTER the body trim so they're never cut off
        links_section = ''
        if 'findagrave.com' in url:
            links = page.query_selector_all('a[href*="/memorial/"]')
            if links:
                hrefs = []
                for a in links[:20]:
                    href = a.get_attribute('href') or ''
                    if '/memorial/' in href and '/memorial/search' not in href and href not in hrefs:
                        hrefs.append(href)
                if hrefs:
                    full_urls = ['https://www.findagrave.com' + h if h.startswith('/') else h for h in hrefs]
                    links_section = '\n\n[MEMORIAL LINKS ON THIS PAGE]\n' + '\n'.join(full_urls)
        return text[:MAX_PAGE_CHARS] + links_section
    except Exception as e:
        return f'[Failed to load: {e}]'


def run_research_agent(first: str, last: str, birth_year, birth_place: str,
                       stream_fn, api_key: str, middle: str = '', death_place: str = ''):
    """
    api_key must be passed in — cannot use current_app inside a thread.

    stream_fn(dict) is called for each event:
      {'type': 'status',   'message': str}
      {'type': 'browsing', 'url': str, 'note': str}
      {'type': 'finding',  'field': str, 'value': str, 'source_url': str, 'confidence': str}
      {'type': 'done',     'summary': str, 'findings': list}
      {'type': 'error',    'message': str}
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        stream_fn({'type': 'error', 'message': 'Playwright not installed on this server.'})
        return

    tools = [
        {
            'type': 'function',
            'function': {
                'name': 'browse_url',
                'description': 'Load a webpage and return its text. Use for search results and record pages.',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'url':  {'type': 'string'},
                        'note': {'type': 'string', 'description': 'What you are looking for'},
                    },
                    'required': ['url'],
                },
            },
        },
        {
            'type': 'function',
            'function': {
                'name': 'report_finding',
                'description': 'Report a confirmed genealogy fact found on a page.',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'field':      {'type': 'string',
                                       'enum': ['birth_year','birth_place','death_year',
                                                'death_place','parent','spouse','other']},
                        'value':      {'type': 'string'},
                        'source_url': {'type': 'string'},
                        'confidence': {'type': 'string', 'enum': ['high','medium','low']},
                    },
                    'required': ['field', 'value', 'source_url', 'confidence'],
                },
            },
        },
        {
            'type': 'function',
            'function': {
                'name': 'research_complete',
                'description': 'Call when done researching.',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'summary': {'type': 'string'},
                    },
                    'required': ['summary'],
                },
            },
        },
    ]

    full_name = f'{first} {middle} {last}'.strip() if middle else f'{first} {last}'.strip()
    birth_str = f'born approximately {birth_year}' if birth_year else 'birth year unknown'
    place_str = f'from {birth_place}' if birth_place else ''
    death_str = f'died in {death_place}' if death_place else ''

    # Build location-aware FindAGrave URLs — search multiple pages when death_place is known
    import urllib.parse
    fn_enc = urllib.parse.quote_plus(f'{first} {middle}'.strip() if middle else first)
    ln_enc = urllib.parse.quote_plus(last)
    dp_enc = urllib.parse.quote_plus(death_place) if death_place else ''
    fg_base = f'https://www.findagrave.com/memorial/search?firstname={fn_enc}&lastname={ln_enc}'
    fg_p2   = f'https://www.findagrave.com/memorial/search?firstname={urllib.parse.quote_plus(first)}&lastname={ln_enc}&page=2'
    fg_p3   = f'https://www.findagrave.com/memorial/search?firstname={urllib.parse.quote_plus(first)}&lastname={ln_enc}&page=3'
    ddg_loc = (f'https://duckduckgo.com/html/?q=%22{urllib.parse.quote_plus(full_name)}%22+{dp_enc}' if death_place
               else f'https://duckduckgo.com/html/?q={urllib.parse.quote_plus(full_name)}+genealogy')
    ddg_obit= f'https://duckduckgo.com/html/?q=%22{urllib.parse.quote_plus(full_name)}%22+obituary' + (f'+{dp_enc}' if death_place else '')

    location_hint = ''
    if death_place:
        location_hint = (
            f'\nIMPORTANT: This person died in {death_place}. '
            f'SKIP any record with a death location that is NOT {death_place}.\n'
            f'Middle names are often abbreviated to initials in old records — '
            f'"Orville C. Henderson" could be "Orville Cleckner Henderson". '
            f'If the initial matches the first letter of the middle name AND the location is {death_place}, treat it as a likely match and browse that memorial page.\n'
            f'Browse these URLs in order:\n'
            f'1. {fg_base}\n'
            f'2. {fg_p2}\n'
            f'3. {fg_p3}\n'
            f'4. {ddg_loc}\n'
            f'5. {ddg_obit}\n'
            f'From [MEMORIAL LINKS ON THIS PAGE], pick any link whose cemetery is in {death_place} and browse it.'
        )

    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {
            'role': 'user',
            'content': (
                f'Research: {full_name}, {birth_str} {place_str}'
                + (f', {death_str}' if death_str else '') + '. '
                + (f'Middle name "{middle}" is distinctive — use it in searches to narrow results. ' if middle else '')
                + (f'IMPORTANT: Known birth year is {birth_year} — reject ANY record where birth year differs by more than 20 years. ' if birth_year else '')
                + location_hint
                + '\nFind birth year, birth place, death year, death place, parents, spouse. '
                f'Use browse_url with real https:// URLs only. '
                f'Call report_finding for each confirmed fact, research_complete when done.'
            ),
        },
    ]

    findings = []
    stream_fn({'type': 'status', 'message': f'Starting deep research on {full_name}…'})

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page    = browser.new_page()
        page.set_extra_http_headers({'User-Agent':
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'})

        # Pre-search: when death_place is known, find matching memorial links
        # using DOM structure so each card's location is checked in isolation
        targeted_links = []
        if death_place:
            stream_fn({'type': 'status', 'message': f'Pre-scanning FindAGrave for {last} in {death_place}…'})
            dp_lower = death_place.lower()
            for pg in range(1, 5):
                try:
                    scan_url = (f'https://www.findagrave.com/memorial/search?'
                                f'firstname={urllib.parse.quote_plus(first)}&lastname={urllib.parse.quote_plus(last)}&page={pg}')
                    page.goto(scan_url, timeout=18000, wait_until='domcontentloaded')
                    # Each result card wraps the link + location text
                    # Find all cards that contain both a memorial link AND the death_place
                    # Use evaluate() to get each anchor's card container text — most reliable
                    anchors = page.query_selector_all('a[href*="/memorial/"]')
                    for a in anchors:
                        href = a.get_attribute('href') or ''
                        if '/memorial/search' in href or '/memorial/' not in href:
                            continue
                        try:
                            container = a.evaluate(
                                'el => el.parentElement?.parentElement?.parentElement?.innerText || ""'
                            )
                            if dp_lower in container.lower():
                                # Birth year guard: skip cards where birth year is off by >20 years
                                if birth_year:
                                    yr_m = re.search(r'\b(1[5-9]\d\d|20[0-2]\d)\b', container)
                                    if yr_m and abs(int(yr_m.group()) - int(birth_year)) > 20:
                                        continue
                                full_url = ('https://www.findagrave.com' + href if href.startswith('/') else href)
                                if full_url not in targeted_links:
                                    targeted_links.append(full_url)
                                    logger.info('Pre-search hit: %s | %s', full_url, container[:80].replace(chr(10),' '))
                        except Exception:
                            pass
                except Exception as _e:
                    logger.warning('Pre-search page %d failed: %s', pg, _e)

            if targeted_links:
                links_str = '\n'.join(f'  - {u}' for u in targeted_links[:6])
                messages[1]['content'] += (
                    f'\n\nPre-scan found these {death_place} candidates on FindAGrave — browse each one:\n'
                    + links_str
                )

        for turn in range(MAX_TURNS):
            try:
                resp = req_lib.post(
                    OPENROUTER_URL,
                    headers={
                        'Authorization': f'Bearer {api_key}',
                        'Content-Type':  'application/json',
                    },
                    json={
                        'model':    AGENT_MODEL,
                        'messages': messages,
                        'tools':    tools,
                        'tool_choice': 'auto',
                    },
                    timeout=45,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                stream_fn({'type': 'error', 'message': f'API error on turn {turn}: {e}'})
                break

            choice  = data.get('choices', [{}])[0]
            message = choice.get('message', {})
            messages.append(message)

            tool_calls  = message.get('tool_calls') or []
            finish      = choice.get('finish_reason', '')
            done        = False

            if not tool_calls:
                # No tool calls — agent is done or gave a plain text response
                stream_fn({'type': 'done', 'summary': message.get('content') or 'Research complete.', 'findings': findings})
                break

            tool_results = []
            for tc in tool_calls:
                fn_name = tc.get('function', {}).get('name', '')
                try:
                    fn_args = json.loads(tc.get('function', {}).get('arguments', '{}'))
                except json.JSONDecodeError:
                    fn_args = {}
                tc_id = tc.get('id', '')

                if fn_name == 'browse_url':
                    url  = fn_args.get('url', '')
                    note = fn_args.get('note', '')
                    stream_fn({'type': 'browsing', 'url': url, 'note': note})
                    page_text = _browse(url, page)
                    tool_results.append({
                        'role':         'tool',
                        'tool_call_id': tc_id,
                        'content':      page_text,
                    })

                elif fn_name == 'report_finding':
                    finding = {
                        'field':      fn_args.get('field', 'other'),
                        'value':      fn_args.get('value', ''),
                        'source_url': fn_args.get('source_url', ''),
                        'confidence': fn_args.get('confidence', 'medium'),
                    }
                    # If we know the death_place, silently skip contradicting death_place findings
                    skip = (death_place and finding['field'] == 'death_place'
                            and death_place.lower() not in finding['value'].lower())
                    if not skip:
                        findings.append(finding)
                        stream_fn({'type': 'finding', **finding})
                    tool_results.append({
                        'role':         'tool',
                        'tool_call_id': tc_id,
                        'content':      'Finding recorded.',
                    })

                elif fn_name == 'research_complete':
                    summary = fn_args.get('summary', 'Research complete.')
                    stream_fn({'type': 'done', 'summary': summary, 'findings': findings})
                    done = True
                    tool_results.append({
                        'role':         'tool',
                        'tool_call_id': tc_id,
                        'content':      'Complete.',
                    })

            messages.extend(tool_results)

            if done:
                break

        browser.close()


# ---------------------------------------------------------------------------
# Living-person research path (Spokeo + Radaris)
# ---------------------------------------------------------------------------

def _parse_spokeo_relatives(text: str, birth_year) -> list:
    """
    Extract relative names and ages from a Spokeo search results page.
    Returns list of dicts: {name, age, location, relation_hint}
    """
    relatives = []
    # Pattern: "Related To Firstname Lastname, Age N" (Spokeo format)
    for m in re.finditer(r'Related To\s+(.*?)(?=\n|Includes|Also known)', text, re.IGNORECASE | re.DOTALL):
        block = m.group(1)
        for rel in re.finditer(r'([A-Z][a-z]+(?: [A-Z][a-z]+)+),?\s+(?:Age\s+)?(\d+)', block):
            name = rel.group(1).strip()
            age  = int(rel.group(2))
            relatives.append({'name': name, 'age': age})
    # Also known as / alternate names block
    also = re.search(r'Also known as\s+(.*?)(?=\n\n|Includes)', text, re.IGNORECASE | re.DOTALL)
    # Extract main subject's location
    loc_m = re.search(r'RESIDES IN\s+([A-Z][A-Z ,]+)', text)
    location = loc_m.group(1).strip().title() if loc_m else ''
    return relatives, location


def _parse_radaris_relatives(text: str) -> list:
    """Extract relatives and lived-in locations from a Radaris page."""
    relatives = []
    # Pattern: name then age on nearby line
    for m in re.finditer(
        r'RELATED TO\s*((?:[A-Z][a-z]+(?: [A-Z][a-z.]+)+,?\s+\d+\s*\n?)+)',
        text, re.IGNORECASE
    ):
        block = m.group(1)
        for rel in re.finditer(r'([A-Z][a-z]+(?: [A-Z][a-z.]+)+),?\s+(\d+)', block):
            name = rel.group(1).strip()
            age  = int(rel.group(2))
            relatives.append({'name': name, 'age': age})
    locations = re.findall(r'HAS LIVED IN\s*((?:[A-Z][a-z ,]+\n?)+)', text, re.IGNORECASE)
    lived_in = []
    if locations:
        lived_in = [l.strip() for l in re.split(r'\n', locations[0]) if l.strip()]
    return relatives, lived_in


def _likely_parent(rel_age: int, subject_birth_year: int) -> bool:
    """True if the relative's age is consistent with being a parent (22-55 yrs older)."""
    if not subject_birth_year:
        return False
    import datetime
    current_year = datetime.datetime.now().year
    rel_birth_approx = current_year - rel_age
    age_diff = rel_birth_approx - subject_birth_year
    return 22 <= age_diff <= 55


def run_living_research(first: str, last: str, birth_year, birth_state: str,
                        stream_fn, middle: str = ''):
    """
    Research a likely-living person using Spokeo + Radaris people-search directories.
    Streams the same event types as run_research_agent so the caller doesn't need
    to treat it differently.

    Emits:
      status   — progress messages
      browsing — URL being visited
      finding  — confirmed relative (parent/spouse) or location
      done     — summary + findings list
      error    — if Playwright unavailable
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        stream_fn({'type': 'error', 'message': 'Playwright not installed on this server.'})
        return

    full_name  = f'{first} {middle} {last}'.strip() if middle else f'{first} {last}'.strip()
    state_abbr = birth_state[:2].upper() if birth_state else ''
    findings   = []

    def _report(field, value, source_url, confidence='medium'):
        f = {'field': field, 'value': value, 'source_url': source_url, 'confidence': confidence}
        findings.append(f)
        stream_fn({'type': 'finding', **f})

    stream_fn({'type': 'status', 'message': f'Searching people directories for {full_name} (living person path)…'})

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page    = browser.new_page()
        page.set_extra_http_headers({'User-Agent':
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'})

        # --- Spokeo ---
        import urllib.parse
        spokeo_name = f'{first}-{last}'
        spokeo_url  = (f'https://www.spokeo.com/{spokeo_name}/{birth_state}'
                       if birth_state else f'https://www.spokeo.com/{spokeo_name}')
        stream_fn({'type': 'browsing', 'url': spokeo_url, 'note': 'Spokeo people search'})
        try:
            page.goto(spokeo_url, timeout=20000, wait_until='domcontentloaded')
            text = page.inner_text('body')
            text = re.sub(r'\n{3,}', '\n\n', text).strip()

            spokeo_rels, location = _parse_spokeo_relatives(text, birth_year)
            if location:
                _report('birth_place', location, spokeo_url, 'medium')

            for rel in spokeo_rels:
                field = 'parent' if _likely_parent(rel['age'], birth_year) else 'other'
                _report(field, rel['name'], spokeo_url, 'low')
                logger.info('Spokeo relative: %s age %d → field=%s', rel['name'], rel['age'], field)
        except Exception as e:
            logger.warning('Spokeo scrape failed: %s', e)

        # --- Radaris ---
        radaris_url = f'https://radaris.com/p/{urllib.parse.quote(first)}/{urllib.parse.quote(last)}/'
        stream_fn({'type': 'browsing', 'url': radaris_url, 'note': 'Radaris people search'})
        try:
            page.goto(radaris_url, timeout=20000, wait_until='domcontentloaded')
            text = page.inner_text('body')
            text = re.sub(r'\n{3,}', '\n\n', text).strip()

            radaris_rels, lived_in = _parse_radaris_relatives(text)

            # Confirm or add location from lived-in list
            if lived_in and not any(f['field'] == 'birth_place' for f in findings):
                # First lived-in entry is usually current/recent; look for birth state match
                for loc in lived_in:
                    if birth_state and birth_state[:2].lower() in loc.lower():
                        _report('birth_place', loc, radaris_url, 'medium')
                        break

            for rel in radaris_rels:
                already = any(f['value'].lower() == rel['name'].lower() for f in findings)
                if not already:
                    field = 'parent' if _likely_parent(rel['age'], birth_year) else 'other'
                    _report(field, rel['name'], radaris_url, 'low')
                    logger.info('Radaris relative: %s age %d → field=%s', rel['name'], rel['age'], field)
        except Exception as e:
            logger.warning('Radaris scrape failed: %s', e)

        browser.close()

    parent_count = sum(1 for f in findings if f['field'] == 'parent')
    summary = (
        f'Living-person search for {full_name} complete. '
        f'Found {len(findings)} data point(s) including {parent_count} likely parent(s). '
        f'Results are from public people-search directories — confirm details before adding to tree.'
    )
    stream_fn({'type': 'done', 'summary': summary, 'findings': findings})
