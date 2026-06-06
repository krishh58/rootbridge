import requests
from flask import current_app

OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'
MODEL = 'anthropic/claude-3-haiku'


def synthesize_gaps(person: dict, results: list, gaps: list) -> dict:
    name = f"{person.get('first_name', '')} {person.get('last_name', '')}".strip()
    birth = f"{person.get('birth_year', 'unknown')} {person.get('birth_state', '')}".strip()

    sources = list({r.get('source', '') for r in results if r.get('source')})
    corroborated = [r for r in results if r.get('corroboration_score', 0) > 0]

    found_summary = (
        f"{len(results)} records found across {len(sources)} sources"
        if results else "No records found"
    )
    corroboration_note = (
        f"{len(corroborated)} of those records were corroborated by multiple independent sources."
        if corroborated else "No cross-source corroboration found."
    )

    gap_list = '\n'.join(
        f"- {g['label']}: {g['detail']}"
        for g in gaps
    ) or "All key fields are populated."

    prompt = f"""You are a genealogy research assistant for RootBridge. You searched real archives on the user's behalf.

SOURCES YOU SEARCHED AND WHAT THEY CONTAIN:
- findagrave: FindAGrave — 200M+ burial memorials worldwide. Returns name, birth/death dates, cemetery, plot. Best for confirming death and burial location.
- freebmd: FreeBMD — ALL UK births, marriages, deaths 1837–2006. Civil registration index. Covers England, Wales, Scotland.
- irish_genealogy: IrishGenealogy.ie — Irish civil registration records. Births from 1864, marriages from 1845, deaths from 1864. Catholic + Protestant church records.
- antenati: Antenati (Italian National Archives) — Italian vital records 1800–1940. Births, marriages, deaths from Italian communes.
- geneteka: Geneteka — 66 million+ Catholic parish records from Poland, Lithuania, Belarus, Ukraine. Baptisms, marriages, burials going back to 1600s.
- digitalarkivet: Digitalarkivet — Norwegian census records and church books. Baptisms, confirmations, marriages, burials.
- archion: Archion — German Protestant (Evangelical) church records. Baptisms, marriages, burials from German parishes.
- matricula: Matricula Online — Catholic church records from Germany, Austria, and Poland. Parish registers going back centuries.
- wikitree: WikiTree — collaborative genealogy tree. User-contributed profiles. Good for connecting family lines.
- chronicling_america: Chronicling America — digitized US newspapers 1770–1963. Birth announcements, obituaries, marriage notices.
- dpla: DPLA — Digital Public Library of America. Digitized books, photos, documents from US libraries and archives.
- nara: NARA — US National Archives catalog. Military records, immigration, naturalization, federal records.

SEARCH RESULTS FOR {name} (born ~{birth}):
Sources that returned data: {', '.join(sources) or 'none'}
{found_summary}. {corroboration_note}

Research gaps identified:
{gap_list}

Write 2-3 plain English sentences: what was found, what is still missing, and what it means for the research.
Be specific — name the sources and what they returned. Do NOT tell the user to search elsewhere — RootBridge does the searching."""

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


def agentic_pick_sources(person: dict, phase1_results: list) -> list:
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

    source_menu = '\n'.join(f'  {k}: {v}' for k, v in AGENTIC_SOURCES.items())

    prompt = f"""You are an agentic genealogy researcher. You just completed a Phase 1 search for:
  Name: {name}
  Birth year: {birth_year}
  Birth place: {birth_place}

Phase 1 sources already searched: {', '.join(found_sources) or 'none'}

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
