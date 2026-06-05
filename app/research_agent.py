"""
AI-driven genealogy research agent.

Uses Claude via OpenRouter (OpenAI-compatible tool_calls format).
Streams findings back as SSE events as they are discovered.
"""
import json
import logging
import re

import requests as req_lib

logger = logging.getLogger(__name__)

OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'
AGENT_MODEL    = 'anthropic/claude-3.5-sonnet'
MAX_TURNS      = 14
MAX_PAGE_CHARS = 4000


SYSTEM_PROMPT = """You are a genealogy research agent. You have tools to browse the web.
Given a person's details, research them thoroughly.

Strategy:
1. Google search: "FIRSTNAME LASTNAME" birth death genealogy
2. Check findagrave.com results
3. Search Google for "FIRSTNAME LASTNAME obituary"
4. Check ancestry.com public search results (no login needed for previews)
5. Try billiongraves.com
6. Follow any promising leads you find

Rules:
- Use report_finding each time you confirm a real fact from a page
- Only report what you actually read — no guessing
- If a page needs login, note the preview info and move on
- Stop after 8 sources or when you have birth year + death year + at least one parent
- End by calling research_complete"""


def _browse(url: str, page) -> str:
    try:
        page.goto(url, timeout=18000, wait_until='domcontentloaded')
        text = page.inner_text('body')
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
        return text[:MAX_PAGE_CHARS]
    except Exception as e:
        return f'[Failed to load: {e}]'


def run_research_agent(first: str, last: str, birth_year, birth_place: str,
                       stream_fn, api_key: str):
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

    name      = f'{first} {last}'.strip()
    birth_str = f'born approximately {birth_year}' if birth_year else 'birth year unknown'
    place_str = f'from {birth_place}' if birth_place else ''

    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {
            'role': 'user',
            'content': (
                f'Research: {name}, {birth_str} {place_str}. '
                f'Find birth year, birth place, death year, death place, parents, spouse. '
                f'Use browse_url to search, report_finding for each confirmed fact, '
                f'research_complete when done.'
            ),
        },
    ]

    findings = []
    stream_fn({'type': 'status', 'message': f'Starting deep research on {name}…'})

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page    = browser.new_page()
        page.set_extra_http_headers({'User-Agent':
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'})

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
