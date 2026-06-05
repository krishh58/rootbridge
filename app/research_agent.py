"""
AI-driven genealogy research agent.

Gives Claude a 'browse_url' tool and lets it navigate the web like a
human researcher — following leads, reading pages, extracting facts.
Streams findings back as SSE events as they are discovered.
"""
import json
import logging
import re
import os

import requests as req_lib
from flask import current_app

logger = logging.getLogger(__name__)

OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'
AGENT_MODEL    = 'anthropic/claude-3.5-sonnet'
MAX_TURNS      = 12   # cap to control cost
MAX_PAGE_CHARS = 4000 # truncate page text sent to Claude


SYSTEM_PROMPT = """You are a genealogy research agent. You have one tool: browse_url.
Use it to search for a person's birth year, death year, birth place, death place, parents, and spouse.

Research strategy:
1. Start with a Google search: site:findagrave.com OR site:ancestry.com "FULL NAME"
2. Visit the most promising result pages
3. Try obituary searches: search Google for "FULL NAME obituary"
4. Check BillionGraves: search billiongraves.com
5. Look at any census record previews on Ancestry or FamilySearch public results
6. Follow leads — if a page mentions parents or a spouse, check that too

Rules:
- Call report_finding each time you confirm a fact with a source URL
- Be skeptical — only report facts you actually read on the page, not guesses
- If a page requires login to see details, note what the preview showed and move on
- Stop after you've checked 6-8 sources or found birth year + death year + parents
- Always end with a call to research_complete summarizing what you found and what's still unknown"""


def _browse(url: str, page) -> str:
    """Load URL with Playwright and return truncated page text."""
    try:
        page.goto(url, timeout=18000, wait_until='domcontentloaded')
        text = page.inner_text('body')
        # collapse whitespace
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
        return text[:MAX_PAGE_CHARS]
    except Exception as e:
        return f'[Failed to load: {e}]'


def run_research_agent(first: str, last: str, birth_year, birth_place: str, stream_fn):
    """
    Run the AI research agent. stream_fn(dict) is called for each SSE event.
    Events:
      {'type': 'status', 'message': str}           — agent is doing something
      {'type': 'browsing', 'url': str, 'note': str} — opening a page
      {'type': 'finding', 'field': str, 'value': str, 'source': str, 'confidence': str}
      {'type': 'done', 'summary': str, 'findings': list}
      {'type': 'error', 'message': str}
    """
    from .playwright_scrapers import _playwright_available
    if not _playwright_available():
        stream_fn({'type': 'error', 'message': 'Playwright not available on this server.'})
        return

    api_key = current_app.config.get('OPENROUTER_API_KEY', '')
    if not api_key:
        stream_fn({'type': 'error', 'message': 'OpenRouter API key not configured.'})
        return

    tools = [
        {
            'name': 'browse_url',
            'description': 'Load a webpage and return its text content. Use for search results and record pages.',
            'input_schema': {
                'type': 'object',
                'properties': {
                    'url':  {'type': 'string', 'description': 'The URL to load'},
                    'note': {'type': 'string', 'description': 'What you are looking for on this page'},
                },
                'required': ['url'],
            },
        },
        {
            'name': 'report_finding',
            'description': 'Report a confirmed genealogy fact you found on a page.',
            'input_schema': {
                'type': 'object',
                'properties': {
                    'field':      {'type': 'string',
                                   'enum': ['birth_year','birth_place','death_year',
                                            'death_place','parent','spouse','other']},
                    'value':      {'type': 'string', 'description': 'The value found'},
                    'source_url': {'type': 'string', 'description': 'URL where this was found'},
                    'confidence': {'type': 'string', 'enum': ['high','medium','low']},
                },
                'required': ['field', 'value', 'source_url', 'confidence'],
            },
        },
        {
            'name': 'research_complete',
            'description': 'Call this when you are done researching. Provide a summary.',
            'input_schema': {
                'type': 'object',
                'properties': {
                    'summary':       {'type': 'string', 'description': 'What was found and what is still unknown'},
                    'still_missing': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['summary'],
            },
        },
    ]

    name      = f'{first} {last}'.strip()
    birth_str = f'born approximately {birth_year}' if birth_year else 'birth year unknown'
    place_str = f'from {birth_place}' if birth_place else ''

    messages = [{
        'role': 'user',
        'content': (
            f'Research this person and find as much as you can: {name}, {birth_str} {place_str}. '
            f'I need: birth year, birth place, death year, death place, parents names, spouse name. '
            f'Use your browse_url tool to search and follow leads. '
            f'Report each finding with report_finding as you discover it.'
        ),
    }]

    findings = []
    stream_fn({'type': 'status', 'message': f'Starting deep research on {name}…'})

    from playwright.sync_api import sync_playwright
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
                        'model':      AGENT_MODEL,
                        'max_tokens': 1024,
                        'tools':      tools,
                        'messages':   messages,
                        'system':     SYSTEM_PROMPT,
                    },
                    timeout=45,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                stream_fn({'type': 'error', 'message': f'API error: {e}'})
                break

            choice  = data.get('choices', [{}])[0]
            message = choice.get('message', {})
            content = message.get('content') or []
            if isinstance(content, str):
                content = [{'type': 'text', 'text': content}]

            messages.append({'role': 'assistant', 'content': content})

            stop_reason  = choice.get('finish_reason', '')
            tool_results = []
            done         = False

            for block in content:
                if block.get('type') != 'tool_use':
                    continue

                tool_name  = block.get('name')
                tool_input = block.get('input', {})
                tool_id    = block.get('id', '')

                if tool_name == 'browse_url':
                    url  = tool_input.get('url', '')
                    note = tool_input.get('note', '')
                    stream_fn({'type': 'browsing', 'url': url, 'note': note})
                    page_text = _browse(url, page)
                    tool_results.append({
                        'role': 'tool',
                        'tool_call_id': tool_id,
                        'content': page_text,
                    })

                elif tool_name == 'report_finding':
                    finding = {
                        'field':      tool_input.get('field'),
                        'value':      tool_input.get('value'),
                        'source_url': tool_input.get('source_url', ''),
                        'confidence': tool_input.get('confidence', 'medium'),
                    }
                    findings.append(finding)
                    stream_fn({'type': 'finding', **finding})
                    tool_results.append({
                        'role': 'tool',
                        'tool_call_id': tool_id,
                        'content': 'Finding recorded.',
                    })

                elif tool_name == 'research_complete':
                    summary = tool_input.get('summary', '')
                    stream_fn({'type': 'done', 'summary': summary, 'findings': findings})
                    done = True
                    tool_results.append({
                        'role': 'tool',
                        'tool_call_id': tool_id,
                        'content': 'Research complete.',
                    })

            if tool_results:
                messages.append({'role': 'user', 'content': tool_results})

            if done or stop_reason == 'stop' or not tool_results:
                if not done:
                    stream_fn({'type': 'done',
                               'summary': 'Research complete.',
                               'findings': findings})
                break

        browser.close()
