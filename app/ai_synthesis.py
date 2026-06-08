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

STEP 1 — CONSTRAINT CHECK: Compare every result against the CONSTRAINTS above. If a result shows a birth place that does not match the constraint (e.g. constraint says Oklahoma but result says Nebraska), it is WRONG and must be excluded from the summary. Do not report it as a match under any circumstances.

STEP 2 — WHAT WAS FOUND: List only results that pass ALL constraints. If nothing passes, say no credible match was found.

STEP 3 — WHAT IS MISSING: What gaps remain in the confirmed record?

STEP 4 — SUMMARY: Write 2–3 plain English sentences. Only reference results that passed STEP 1. If no results passed, say the search did not find a confirmed match. Do NOT mention context_score numbers. Do NOT suggest the user do anything — RootBridge handles all searching automatically.

Output only the STEP 4 summary. No step labels, no headers."""

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
        # Strip any leaked step markers or "more research needed" language
        for marker in ['STEP 1', 'STEP 2', 'STEP 3', 'STEP 4 —', 'STEP 4—']:
            if marker in summary:
                summary = summary.split(marker)[-1].strip(' —\n')
                break
        _bad_phrases = [
            'more research would be needed',
            'additional research would be needed',
            'additional searches',
            'further research',
            'you may want to',
            'consider searching',
            'suggest searching',
        ]
        for phrase in _bad_phrases:
            if phrase in summary.lower():
                # Trim from that sentence onward
                idx = summary.lower().find(phrase)
                summary = summary[:idx].strip().rstrip('.,;')
                break
        return {'summary': summary}
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return {'summary': ''}


# Available sources the agent can choose from in Phase 2
AGENTIC_SOURCES = {
    'ssdi':           'Social Security Death Index — 87M US death records, birth year, death year, state of issue',
    'geni':           'Geni.com world family tree — millions of linked profiles with birth, death, and relationship data',
    'obituaries':     'US obituary databases — confirms death date, survivors, hometown',
    'va_gravesite':   'VA National Cemetery — confirms military service and burial',
    'chronicling':    'US newspapers 1770–1963 — birth announcements, marriage notices, obits',
    'dpla_military':  'Military records via DPLA — service records, pension files',
    'dpla_obituary':  'Obituary collections via DPLA — digitized death notices',
    'dpla_marriage':  'Marriage records via DPLA — marriage certificates and registers',
    'nara':           'National Archives — immigration, naturalization, federal records',
    'usgenweb':       'USGenWeb — volunteer-transcribed county census, vital and land records',
    'rootsweb':       'RootsWeb WorldConnect — user family trees with census citations',
    'open_library':   'OpenLibrary — digitised county histories and surname genealogy books',
    'ship_manifest_db': 'Local indexed ship arrivals (Rupp 1727-1776, Strassburger 1727-1808, Hamburg 1850-1934) — instant search, no network. Best first stop for immigrant ancestors.',
    'ship_manifest':  'Colonial/immigrant ship arrival records — Pennsylvania German lists 1727-1808, Hamburg emigrant lists 1850-1934. Best for pre-1850 ancestors with German/Irish/UK origin.',
    'revwar_pension':   'Revolutionary War pension files (NARA M804, ~80K files) — sworn testimony with birth dates, birthplaces, marriage dates, children. Best for US veterans born 1730-1800.',
    'castle_garden':   'Castle Garden NYC (1820-1892) — 11 million New York arrivals. German, Irish, British, Scandinavian immigrants pre-Ellis Island.',
    'ellis_island':    'Ellis Island NYC (1892-1957) — 12 million arrivals. Southern/Eastern Europe: Italian, Polish, Russian, Greek, Jewish immigrants.',
    'hamburg_emigrant':'German Emigration Database — Hamburg + Bremen departures 1820-1934. The DEPARTURE side: names, hometown, destination port. Best for tracing German ancestors back to their village.',
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
    death_year = person.get('death_year', 'unknown')
    death_place = person.get('death_place', 'unknown')
    is_us = person.get('is_us', False)
    is_modern = person.get('is_modern', False)

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

    # Build location/era-aware rules
    location_rules = []
    if is_us:
        location_rules.append('- This person is AMERICAN. Do NOT pick any European sources (freebmd, irish_birth, irish_death, antenati, geneteka, digitalarkivet, archion, matricula) unless Phase 1 found clear evidence of recent immigration.')
    if is_modern:
        location_rules.append('- This person died recently. Do NOT pick chronicling (ends 1963) — it will find nothing. Focus on obituaries, findagrave, va_gravesite.')
    if death_place and death_place != 'unknown':
        location_rules.append(f'- Death place is {death_place} — prioritize sources that cover this region.')
    if not location_rules:
        location_rules.append('- If birth place is clearly a US state, skip European sources unless Phase 1 shows immigration.')

    location_block = '\n'.join(location_rules)

    # Default fallback depends on era
    if is_modern and is_us:
        fallback = '["obituaries", "findagrave", "va_gravesite"]'
    elif is_us:
        fallback = '["obituaries", "nara", "wikitree"]'
    else:
        fallback = '["obituaries", "chronicling", "nara"]'

    prompt = f"""You are an agentic genealogy researcher. You just completed a Phase 1 search for:
  Name: {name}
  Birth year: {birth_year}  |  Birth place: {birth_place}
  Death year: {death_year}  |  Death place: {death_place}

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
- Do NOT re-pick sources already searched in Phase 1
{location_block}
- If Phase 1 found military hints, pick va_gravesite and dpla_military
- If surname or location suggests Irish/UK origin AND person is not clearly American, pick freebmd

Reply with ONLY a JSON array of source keys, e.g.: {fallback}
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
        if is_modern and is_us:
            return ['obituaries', 'findagrave', 'va_gravesite']
        elif is_us:
            return ['obituaries', 'nara', 'wikitree']
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


# ---------------------------------------------------------------------------
# Research roadmap — generated when all searches fail to find a match
# ---------------------------------------------------------------------------

_STATE_ARCHIVE_URLS = {
    'alabama': 'https://www.adah.alabama.gov/',
    'alaska': 'https://archives.alaska.gov/',
    'arizona': 'https://azlibrary.gov/az-state-library-archives-public-records',
    'arkansas': 'https://www.ark.org/sos/archives/',
    'california': 'https://www.sos.ca.gov/archives/',
    'colorado': 'https://www.colorado.gov/archives',
    'connecticut': 'https://ctstatelibrary.org/archives-special-collections/',
    'delaware': 'https://archives.delaware.gov/',
    'florida': 'https://dos.myflorida.com/state-library-and-archives/',
    'georgia': 'https://www.georgiaarchives.org/',
    'hawaii': 'https://ags.hawaii.gov/archives/',
    'idaho': 'https://history.idaho.gov/archives/',
    'illinois': 'https://www.illinois.gov/agencies/agency/illinois-state-archives',
    'indiana': 'https://www.in.gov/iara/',
    'iowa': 'https://iowaculture.gov/history/research/collections/state-archives',
    'kansas': 'https://www.kshs.org/archives',
    'kentucky': 'https://kdla.ky.gov/',
    'louisiana': 'https://www.sos.la.gov/HistoricalResources/Pages/default.aspx',
    'maine': 'https://www.maine.gov/sos/arc/',
    'maryland': 'https://msa.maryland.gov/',
    'massachusetts': 'https://www.sec.state.ma.us/ard/',
    'michigan': 'https://www.michigan.gov/libraryofmichigan/',
    'minnesota': 'https://www.mnhs.org/',
    'mississippi': 'https://www.mdah.ms.gov/',
    'missouri': 'https://www.sos.mo.gov/archives/',
    'montana': 'https://mhs.mt.gov/',
    'nebraska': 'https://www.nebraskahistory.org/',
    'nevada': 'https://nsla.nv.gov/',
    'new hampshire': 'https://www.sos.nh.gov/archives/',
    'new jersey': 'https://www.nj.gov/state/archives/',
    'new mexico': 'https://www.nmcpr.state.nm.us/',
    'new york': 'https://www.archives.nysed.gov/',
    'north carolina': 'https://www.dncr.nc.gov/about/state-agencies/archives',
    'north dakota': 'https://www.nd.gov/itd/stategov/state-agency/state-historical-society-north-dakota',
    'ohio': 'https://www.ohiohistory.org/',
    'oklahoma': 'https://www.okhistory.org/',
    'oregon': 'https://sos.oregon.gov/archives/',
    'pennsylvania': 'https://www.phmc.pa.gov/Archives/',
    'rhode island': 'https://www.sos.ri.gov/divisions/public-information/archives',
    'south carolina': 'https://scdah.sc.gov/',
    'south dakota': 'https://history.sd.gov/archives/',
    'tennessee': 'https://sos.tn.gov/tsla/',
    'texas': 'https://www.tsl.texas.gov/',
    'utah': 'https://archives.utah.gov/',
    'vermont': 'https://sos.vermont.gov/vsara/',
    'virginia': 'https://www.lva.virginia.gov/',
    'washington': 'https://www.sos.wa.gov/archives/',
    'west virginia': 'https://www.wvculture.org/history/wvarchives.aspx',
    'wisconsin': 'https://www.wisconsinhistory.org/',
    'wyoming': 'https://wyoarchives.wyo.gov/',
}


def _state_archive_url(birth_place: str) -> str | None:
    if not birth_place:
        return None
    bp = birth_place.lower().strip()
    for state, url in _STATE_ARCHIVE_URLS.items():
        if state in bp or bp in state:
            return url
    return None


def generate_roadmap(first: str, last: str, middle: str,
                     birth_year, birth_place: str,
                     results: list, gaps: list) -> dict:
    """
    Generate a research roadmap when no match is found.
    Returns: { steps: [str], links: [{label, url, prefilled}] }
    """
    import urllib.parse
    import json as jsonlib

    name = ' '.join(filter(None, [first, middle, last])).strip()
    name_no_mid = f'{first} {last}'.strip()

    # Build pre-filled archive links
    links = []

    # FamilySearch (always)
    fs_q = urllib.parse.urlencode({'q.givenName': first, 'q.surname': last,
                                   'q.birthLikeDate.from': str((birth_year or 1800) - 5),
                                   'q.birthLikeDate.to': str((birth_year or 1900) + 5),
                                   'q.birthLikePlace': birth_place or ''})
    links.append({
        'label': 'Search FamilySearch',
        'url': f'https://www.familysearch.org/search/record/results?{fs_q}',
        'note': 'World\'s largest free genealogy database — census, vital records, immigration',
    })

    # Find A Grave
    fg_q = urllib.parse.urlencode({'firstname': first, 'lastname': last,
                                   'birthyear': birth_year or '', 'birthyearfilter': 5,
                                   'location': birth_place or ''})
    links.append({
        'label': 'Search Find A Grave',
        'url': f'https://www.findagrave.com/memorial/search?{fg_q}',
        'note': 'Over 260 million memorial records with photos',
    })

    # Fold3 (military)
    fold3_q = urllib.parse.urlencode({'query': name_no_mid})
    links.append({
        'label': 'Search Fold3 Military Records',
        'url': f'https://www.fold3.com/search#query={urllib.parse.quote(name_no_mid)}',
        'note': 'US military records — draft cards, pension files, service records',
    })

    # Chronicling America
    chron_q = urllib.parse.urlencode({'q': name_no_mid, 'dateFilterType': 'range',
                                      'date1': str((birth_year or 1800) - 5),
                                      'date2': str((birth_year or 1900) + 20)})
    links.append({
        'label': 'Search Chronicling America',
        'url': f'https://chroniclingamerica.loc.gov/search/pages/results/?{chron_q}',
        'note': 'Free US newspapers 1770–1963 — obituaries, birth notices, marriage announcements',
    })

    # State archive if birth place known
    state_url = _state_archive_url(birth_place)
    if state_url:
        state_label = birth_place.title() if birth_place else 'State'
        links.append({
            'label': f'{state_label} State Archives',
            'url': state_url,
            'note': f'Official {state_label} vital records — birth/death certificates, county records',
        })

    # Middle name as surname hint
    if middle:
        links.append({
            'label': f'Try searching "{first} {middle}" (middle as surname)',
            'url': f'https://www.familysearch.org/search/record/results?q.givenName={urllib.parse.quote(first)}&q.surname={urllib.parse.quote(middle)}',
            'note': 'Ancestors sometimes indexed under their middle name',
        })

    # Use AI for the narrative steps
    found_count = len(results)
    gap_labels = [g.get('label', g.get('gap_type', '')) for g in gaps[:5]]
    gap_text = ', '.join(gap_labels) if gap_labels else 'birth year, birth place, death records'

    prompt = f"""You are Alfred, a genealogy research assistant for RootBridge.

We searched every source we have for {name} (born ~{birth_year or 'unknown'}, {birth_place or 'unknown place'}) and found {found_count} records — but none were a strong match.

Known gaps in the record: {gap_text}

Write exactly 3 concrete research steps a user should try next. Each step:
- Starts with a bold action verb
- Is 1–2 sentences max
- References a specific record type, archive, or strategy
- Does NOT say "try our platform again" or "search RootBridge"

Format as a JSON array of strings. Example:
["**Check county death certificates** — ...", "**Request a SSDI transcript** — ...", "**Search newspaper obituaries** — ..."]

Reply with ONLY the JSON array."""

    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                'Authorization': f"Bearer {current_app.config['OPENROUTER_API_KEY']}",
                'Content-Type': 'application/json',
            },
            json={'model': MODEL, 'messages': [{'role': 'user', 'content': prompt}],
                  'max_tokens': 300},
            timeout=10,
        )
        resp.raise_for_status()
        raw = resp.json()['choices'][0]['message']['content'].strip()
        if raw.startswith('```'):
            raw = raw.split('```')[1]
            if raw.startswith('json'):
                raw = raw[4:]
        steps = jsonlib.loads(raw)
        if not isinstance(steps, list):
            steps = []
    except Exception:
        steps = [
            f'**Check county vital records** — {birth_place or "the county"} may have birth or death certificates predating federal registration.',
            f'**Search newspaper obituaries** — Chronicling America has digitized US newspapers 1770–1963 and often contains death notices for rural ancestors.',
            f'**Try name spelling variants** — Common misspellings of "{last}" in census records include phonetic alternatives — search FamilySearch with a soundex option.',
        ]

    return {'steps': steps, 'links': links, 'name': name, 'birth_year': birth_year, 'birth_place': birth_place}
