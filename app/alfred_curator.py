"""
Alfred GEDCOM Curator — AI-driven genealogy data source finder.

Strategy (hallucination mitigation):
  1. Model never invents URLs from memory — it calls web_search/archive_search tools
  2. Two-model cross-check: Claude + Mistral independently find sources; intersection wins
  3. URLs assembled from structured {site, identifier} parts, not free-form strings
  4. Every URL HEAD-validated before it enters the index
"""

import json, re, time, urllib.parse
from pathlib import Path
import requests

OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'

# Primary curation model — strong tool-use capability
CURATOR_MODEL_A = 'anthropic/claude-3.5-sonnet'
# Cross-check model — independent, different provider
CURATOR_MODEL_B = 'mistralai/mistral-large'

INDEX_PATH = Path(__file__).parent.parent / 'data' / 'ged_index.json'

HEADERS_BROWSER = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36'
}

# ─── tool implementations ─────────────────────────────────────────────────────

def _web_search(query: str, max_results: int = 10) -> list[dict]:
    """DuckDuckGo HTML search — returns [{title, url, snippet}]."""
    try:
        from bs4 import BeautifulSoup
        params = {'q': query, 'kl': 'us-en'}
        r = requests.get(
            'https://html.duckduckgo.com/html/',
            params=params,
            headers=HEADERS_BROWSER,
            timeout=15,
        )
        soup = BeautifulSoup(r.text, 'lxml')
        results = []
        for a in soup.select('a.result__a')[:max_results]:
            href = a.get('href', '')
            # DDG wraps links — extract real URL
            if 'uddg=' in href:
                href = urllib.parse.unquote(re.search(r'uddg=([^&]+)', href).group(1))
            snippet_el = a.find_parent('div', class_='result')
            snippet = snippet_el.select_one('.result__snippet')
            results.append({
                'title': a.get_text(strip=True),
                'url': href,
                'snippet': snippet.get_text(strip=True) if snippet else '',
            })
        return results
    except Exception as e:
        return [{'error': str(e)}]


def _archive_search(query: str, max_results: int = 20) -> list[dict]:
    """Internet Archive scrape API — returns items with .ged files."""
    try:
        r = requests.get(
            'https://archive.org/services/search/v1/scrape',
            params={'q': query, 'fields': 'identifier,title', 'count': max_results},
            timeout=20,
        )
        items = r.json().get('items', [])
        results = []
        for item in items:
            ident = item.get('identifier', '')
            # Check files in this item
            fr = requests.get(
                f'https://archive.org/metadata/{ident}/files', timeout=10
            )
            files = fr.json().get('result', [])
            ged_files = [f['name'] for f in files if f.get('name', '').lower().endswith('.ged')]
            if ged_files:
                results.append({
                    'identifier': ident,
                    'title': item.get('title', ident),
                    'ged_files': ged_files,
                    'urls': [
                        f"https://archive.org/download/{ident}/{urllib.parse.quote(name)}"
                        for name in ged_files
                    ],
                })
            time.sleep(0.1)
        return results
    except Exception as e:
        return [{'error': str(e)}]


def _validate_url(url: str) -> dict:
    """HEAD request to confirm URL exists and returns content."""
    try:
        r = requests.head(url, headers=HEADERS_BROWSER, timeout=10, allow_redirects=True)
        return {
            'url': url,
            'valid': r.status_code == 200,
            'status': r.status_code,
            'content_type': r.headers.get('content-type', ''),
            'size_bytes': int(r.headers.get('content-length', 0)),
        }
    except Exception as e:
        return {'url': url, 'valid': False, 'error': str(e)}


# ─── tool dispatch ────────────────────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        'type': 'function',
        'function': {
            'name': 'web_search',
            'description': 'Search the web for genealogy GEDCOM file sources. Returns real search results — only use URLs from these results, never from memory.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {'type': 'string', 'description': 'Search query'},
                    'max_results': {'type': 'integer', 'default': 10},
                },
                'required': ['query'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'archive_search',
            'description': 'Search the Internet Archive for GEDCOM genealogy files. Returns verified download URLs.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {'type': 'string', 'description': 'Search terms'},
                    'max_results': {'type': 'integer', 'default': 20},
                },
                'required': ['query'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'validate_url',
            'description': 'Verify a URL is accessible before adding it to the index.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'url': {'type': 'string'},
                },
                'required': ['url'],
            },
        },
    },
]

TOOL_MAP = {
    'web_search': lambda args: _web_search(args['query'], args.get('max_results', 10)),
    'archive_search': lambda args: _archive_search(args['query'], args.get('max_results', 20)),
    'validate_url': lambda args: _validate_url(args['url']),
}

SYSTEM_PROMPT = """You are Alfred, a genealogy data curator for RootBridge. Your mission: find high-quality, publicly available GEDCOM (.ged) family history files to seed our database.

CRITICAL RULES — follow these exactly:
1. NEVER invent or recall URLs from memory. Only use URLs returned by web_search or archive_search tools.
2. Call validate_url on every URL before including it in your final list.
3. Prefer files with 500+ persons. Skip obvious test/fake data.
4. Focus on: genealogy society exports, county historical records, university archives, USGenWeb, FamilySearch public datasets, Rootsweb archives.
5. Use archive_search for Internet Archive sources — it returns verified download URLs directly.
6. Use web_search for finding genealogy organization pages, then extract .ged download links from the results.

When done, return a JSON array (no markdown, raw JSON only):
[{"url": "...", "source": "...", "description": "...", "estimated_persons": 0}]

Only include URLs that passed validate_url with valid=true."""


def _run_model_with_tools(api_key: str, model: str, log: list) -> list[dict]:
    """Run one model through the tool-use loop. Returns list of validated GED URLs."""
    messages = [{'role': 'user', 'content': 'Find as many high-quality public GEDCOM files as you can. Use all available tools. Be thorough — run multiple searches.'}]

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }

    found_urls = []
    max_iterations = 20

    for iteration in range(max_iterations):
        payload = {
            'model': model,
            'messages': [{'role': 'system', 'content': SYSTEM_PROMPT}] + messages,
            'tools': TOOL_DEFINITIONS,
            'tool_choice': 'auto',
            'max_tokens': 4000,
        }

        try:
            resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.append(f'[{model}] API error: {e}')
            break

        choice = data.get('choices', [{}])[0]
        msg = choice.get('message', {})
        finish = choice.get('finish_reason', '')

        messages.append(msg)

        tool_calls = msg.get('tool_calls', [])
        if not tool_calls:
            # Model finished — parse final answer
            content = msg.get('content', '')
            if content:
                # Extract JSON from response
                json_match = re.search(r'\[.*\]', content, re.DOTALL)
                if json_match:
                    try:
                        found_urls = json.loads(json_match.group(0))
                        log.append(f'[{model}] Final list: {len(found_urls)} URLs')
                    except Exception:
                        log.append(f'[{model}] Could not parse final JSON')
            break

        # Execute tool calls
        for tc in tool_calls:
            fn_name = tc.get('function', {}).get('name', '')
            fn_args_raw = tc.get('function', {}).get('arguments', '{}')
            tc_id = tc.get('id', '')

            try:
                fn_args = json.loads(fn_args_raw)
            except Exception:
                fn_args = {}

            log.append(f'[{model}] Tool: {fn_name}({list(fn_args.keys())})')

            if fn_name in TOOL_MAP:
                result = TOOL_MAP[fn_name](fn_args)
            else:
                result = {'error': f'Unknown tool: {fn_name}'}

            messages.append({
                'role': 'tool',
                'tool_call_id': tc_id,
                'content': json.dumps(result),
            })

    return found_urls


# ─── cross-check + dedup ──────────────────────────────────────────────────────

def _extract_urls(results: list[dict]) -> set[str]:
    urls = set()
    for r in results:
        u = r.get('url', '').strip()
        if u and (u.lower().endswith('.ged') or '.ged?' in u.lower()):
            urls.add(u)
    return urls


def run_curation(api_key: str, skip_crosscheck: bool = False) -> dict:
    """
    Main entry point. Returns:
      {new_count, total_count, log, new_urls}
    """
    log = []

    # Load existing index
    existing = []
    if INDEX_PATH.exists():
        try:
            existing = json.loads(INDEX_PATH.read_text())
        except Exception:
            pass
    existing_urls = {e['url'] for e in existing}
    log.append(f'Existing index: {len(existing)} URLs')

    # Model A — primary curator
    log.append(f'Running primary curator ({CURATOR_MODEL_A})...')
    results_a = _run_model_with_tools(api_key, CURATOR_MODEL_A, log)
    urls_a = _extract_urls(results_a)
    log.append(f'Model A found: {len(urls_a)} candidate URLs')

    if not skip_crosscheck:
        # Model B — independent cross-check
        log.append(f'Running cross-check model ({CURATOR_MODEL_B})...')
        results_b = _run_model_with_tools(api_key, CURATOR_MODEL_B, log)
        urls_b = _extract_urls(results_b)
        log.append(f'Model B found: {len(urls_b)} candidate URLs')

        # Intersection = high-confidence URLs; union = full set
        intersection = urls_a & urls_b
        union = urls_a | urls_b
        log.append(f'Intersection (both models agreed): {len(intersection)}')
        log.append(f'Union (either model found): {len(union)}')

        # Use intersection as primary; add union items that passed model A's validate_url
        a_validated = {r['url'] for r in results_a if r.get('url') and r.get('validated')}
        candidates = intersection | (urls_a & a_validated)
    else:
        candidates = urls_a

    # Final HEAD validation pass on anything not yet validated
    new_entries = []
    for url in candidates:
        if url in existing_urls:
            continue
        validation = _validate_url(url)
        if not validation.get('valid'):
            log.append(f'  [skip] {url[:70]} — {validation.get("status") or validation.get("error")}')
            continue
        # Find metadata from results_a
        meta = next((r for r in results_a if r.get('url') == url), {})
        new_entries.append({
            'url': url,
            'source': meta.get('source', 'alfred_curator'),
            'title': meta.get('description', ''),
        })
        log.append(f'  + {url[:70]}')

    # Merge into index
    updated = existing + new_entries
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(updated, indent=2))

    log.append(f'Done. Added {len(new_entries)} new URLs. Index total: {len(updated)}')

    return {
        'new_count': len(new_entries),
        'total_count': len(updated),
        'new_urls': [e['url'] for e in new_entries],
        'log': log,
    }
