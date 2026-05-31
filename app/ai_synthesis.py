import requests
from flask import current_app

OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'
MODEL = 'anthropic/claude-3-haiku'


def synthesize_gaps(person: dict, results: list, gaps: list) -> dict:
    name = f"{person.get('first_name', '')} {person.get('last_name', '')}".strip()
    birth = f"{person.get('birth_year', 'unknown')} {person.get('birth_state', '')}".strip()

    found_summary = f"{len(results)} records found" if results else "No records found"
    gap_list = '\n'.join(
        f"- {g['gap_type']}: try {g['suggested_source']} — search: {g['suggested_query']}"
        for g in gaps
    ) or "No gaps — record appears complete."

    prompt = f"""You are a genealogy research assistant. Summarize findings for {name} (born ~{birth}).

Records found: {found_summary}
Sources: {', '.join(set(r.get('source', '') for r in results)) or 'none'}

Research gaps:
{gap_list}

Write 2-3 plain English sentences: what was found, what is missing, and the single most important next step. Be specific and helpful."""

    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                'Authorization': f"Bearer {current_app.config['OPENROUTER_API_KEY']}",
                'Content-Type': 'application/json',
            },
            json={'model': MODEL, 'messages': [{'role': 'user', 'content': prompt}],
                  'max_tokens': 150},
            timeout=10,
        )
        resp.raise_for_status()
        summary = resp.json()['choices'][0]['message']['content'].strip()
        return {'summary': summary}
    except Exception:
        return {'summary': ''}
