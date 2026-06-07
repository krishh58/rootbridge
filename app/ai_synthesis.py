import requests
from flask import current_app

OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'
MODEL = 'anthropic/claude-3-haiku'


def build_synthesis_prompt(ctx, results: list, gaps: list) -> str:
    if ctx is not None:
        name = f'{ctx.first} {ctx.last}'.strip()
        birth = f'~{ctx.birth_year}' if ctx.birth_year else 'unknown year'
        if ctx.birth_place:
            birth += f', {ctx.birth_place}'
        context_block = ctx.to_prompt_block()
        generation_note = (f'(Generation {ctx.generation}: {ctx.generation_label})'
                           if ctx.generation > 1 else '')
    else:
        name = 'Unknown'
        birth = 'unknown'
        context_block = ''
        generation_note = ''

    sources = list({r.get('source', '') for r in results if r.get('source')})
    high_confidence = [r for r in results if r.get('context_score', 50) >= 70]
    low_confidence  = [r for r in results if r.get('context_score', 50) < 40]

    result_lines = []
    for r in results[:15]:
        cs = r.get('context_score', '?')
        corr = r.get('corroboration_score', 0)
        line = (f"  [{r.get('source','?')}] {r.get('title','')} | "
                f"born:{r.get('birth_year','?')} {r.get('birth_place','')} | "
                f"context_score:{cs} corroborated_by:{corr}")
        result_lines.append(line)

    gap_list = '\n'.join(
        f'  - {g.get("label", g.get("gap_type", "gap"))}: {g.get("detail", g.get("suggested_query", ""))}'
        for g in gaps
    ) or '  None'

    prompt = f"""You are a genealogy research assistant for RootBridge. {generation_note}

=== RESEARCH CONTEXT ===
{context_block if context_block else f'Target: {name}, born {birth}'}

=== SEARCH RESULTS ({len(results)} records) ===
{chr(10).join(result_lines) if result_lines else '  No records found.'}

High-confidence matches (context_score >= 70): {len(high_confidence)}
Low-confidence/suspicious results (context_score < 40): {len(low_confidence)}

=== RESEARCH GAPS ===
{gap_list}

=== YOUR TASK ===
Reason step by step before writing your summary:

STEP 1 — CONSTRAINT CHECK: Do any results violate the confirmed facts or constraints listed above? Name them explicitly. If a result has context_score < 40, explain why it is suspect.

STEP 2 — WHAT WAS FOUND: Which results are credible matches? Cite the source and what it confirms.

STEP 3 — WHAT IS MISSING: What gaps remain? Be specific about what record type would fill each gap.

STEP 4 — SUMMARY: Write 2–3 plain English sentences for the user. Reference only credible results. Do NOT mention context_score numbers. Do NOT tell the user to search elsewhere — RootBridge does the searching.

Output only the STEP 4 summary to the user."""

    return prompt


def synthesize_gaps(person: dict, results: list, gaps: list, ctx=None) -> dict:
    if ctx is None:
        try:
            from app.research_context import ResearchContext
            ctx = ResearchContext.from_person(person)
        except Exception:
            pass

    prompt = build_synthesis_prompt(ctx, results, gaps)

    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                'Authorization': f"Bearer {current_app.config['OPENROUTER_API_KEY']}",
                'Content-Type': 'application/json',
            },
            json={'model': MODEL, 'messages': [{'role': 'user', 'content': prompt}],
                  'max_tokens': 180},
            timeout=10,
        )
        resp.raise_for_status()
        summary = resp.json()['choices'][0]['message']['content'].strip()
        return {'summary': summary}
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return {'summary': ''}


# Available sources the agent can choose from in Phase 2
AGENTIC_SOURCES = {
    'obituaries':     'US obituary databases — confirms death date, survivors, hometown',
    'va_gravesite':   'VA National Cemetery — confirms military service and burial',
    'chronicling':    'US newspapers 1770–1963 — birth announcements, marriage notices, obits',
    'dpla_military':  'Military records via DPLA — service records, pension files',
    'dpla_obituary':  'Obituary collections via DPLA — digitized death notices',
    'dpla_marriage':  'Marriage records via DPLA — marriage certificates and registers',
    'nara':           'National Archives — immigration, naturalization, federal records',
    'freebmd':        'FreeBMD — all UK births, marriages, deaths 1837–2006',
    'irish_birth':    'Irish civil registration births from 1864',
    'irish_death':    'Irish civil registration deaths from 1864',
    'antenati':       'Italian National Archives — vital records 1800–1940',
    'geneteka':       'Polish Catholic parish records — baptisms, marriages, burials',
    'digitalarkivet': 'Norwegian census and church books',
    'archion':        'German Protestant church records',
    'matricula':      'German/Austrian/Polish Catholic parish registers',
}


def agentic_pick_sources(person: dict, phase1_results: list, ctx=None) -> list:
    """
    Ask the AI which Phase 2 sources to search based on what Phase 1 found.
    Returns a list of source keys from AGENTIC_SOURCES.
    """
    name = f"{person.get('first_name', '')} {person.get('last_name', '')}".strip()
    birth_year = person.get('birth_year', 'unknown')
    birth_place = person.get('birth_place', 'unknown')

    found_sources = list({r.get('source') for r in phase1_results if r.get('source')})
    clues = []
    for r in phase1_results[:12]:
        parts = [r.get('title', ''), r.get('date', ''), r.get('record_type', '')]
        line = ' | '.join(p for p in parts if p)
        if line:
            clues.append(f"  - [{r.get('source')}] {line}")

    clue_text = '\n'.join(clues) if clues else '  (no records found in Phase 1)'

    context_block = ctx.to_prompt_block() if ctx else ''

    source_menu = '\n'.join(f'  {k}: {v}' for k, v in AGENTIC_SOURCES.items())

    prompt = f"""You are an agentic genealogy researcher. You just completed a Phase 1 search for:
  Name: {name}
  Birth year: {birth_year}
  Birth place: {birth_place}

Phase 1 sources already searched: {', '.join(found_sources) or 'none'}"""

    if context_block:
        prompt = f"Research context:\n{context_block}\n\n" + prompt

    prompt += f"""

Phase 1 findings:
{clue_text}

Available Phase 2 sources:
{source_menu}

Based on the clues above, pick the 2-4 most promising Phase 2 sources to search next.
Rules:
- Do NOT re-pick sources already searched in Phase 1 (wikitree, findagrave, dpla_census)
- If birth place is clearly a US state, skip European sources unless Phase 1 shows immigration
- If Phase 1 found military hints, pick va_gravesite and dpla_military
- If surname or location suggests Irish/UK origin, pick freebmd and irish_birth/irish_death
- If no clues at all, pick obituaries, chronicling, nara

Reply with ONLY a JSON array of source keys, e.g.: ["obituaries", "chronicling", "nara"]
No explanation. Just the array."""

    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                'Authorization': f"Bearer {current_app.config['OPENROUTER_API_KEY']}",
                'Content-Type': 'application/json',
            },
            json={
                'model': MODEL,
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 60,
            },
            timeout=8,
        )
        resp.raise_for_status()
        import json as _json
        raw = resp.json()['choices'][0]['message']['content'].strip()
        picks = _json.loads(raw)
        # Validate — only return keys that actually exist
        return [p for p in picks if p in AGENTIC_SOURCES]
    except Exception:
        # Fallback: safe default set
        return ['obituaries', 'chronicling', 'nara']


def extract_ancestor_from_obit(obit_text: str, known_person: str,
                                relationship: str, source_url: str) -> dict:
    """
    Given obituary text, extract the ancestor (relationship to known_person).
    Returns structured dict: first_name, last_name, birth_year, death_year,
    death_place, burial_place, spouse, confidence, summary.
    """
    rel_label = {
        'father': 'father', 'mother': 'mother',
        'grandfather': 'paternal or maternal grandfather',
        'grandmother': 'paternal or maternal grandmother',
        'great-grandfather': 'great-grandfather',
        'great-grandmother': 'great-grandmother',
        'uncle': 'uncle', 'aunt': 'aunt',
    }.get(relationship, relationship)

    prompt = f"""You are a genealogy extraction assistant. Read the obituary text below and extract information about the {rel_label} of {known_person}.

OBITUARY TEXT:
{obit_text[:3000]}

Extract and return ONLY a JSON object with these fields (use null for unknown):
{{
  "first_name": "...",
  "last_name": "...",
  "nickname": "...",
  "birth_year": null,
  "death_year": null,
  "death_place": "...",
  "burial_place": "...",
  "spouse": "...",
  "children": ["..."],
  "siblings": ["..."],
  "confidence": "high|medium|low",
  "summary": "one sentence describing what was found"
}}

If this obituary is NOT about the {rel_label} of {known_person}, return {{"confidence": "none", "summary": "This obituary does not match."}}
Return ONLY the JSON, no other text."""

    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                'Authorization': f"Bearer {current_app.config['OPENROUTER_API_KEY']}",
                'Content-Type': 'application/json',
            },
            json={'model': MODEL, 'messages': [{'role': 'user', 'content': prompt}],
                  'max_tokens': 400},
            timeout=15,
        )
        resp.raise_for_status()
        import json as jsonlib
        raw = resp.json()['choices'][0]['message']['content'].strip()
        # Strip markdown code fences if present
        if raw.startswith('```'):
            raw = raw.split('```')[1]
            if raw.startswith('json'):
                raw = raw[4:]
        result = jsonlib.loads(raw)
        result['source_url'] = source_url
        return result
    except Exception:
        return {'confidence': 'none', 'summary': 'Could not extract information from this obituary.',
                'source_url': source_url}
