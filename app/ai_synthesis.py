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

    prompt = f"""You are a genealogy research assistant for RootBridge, a service that searches archives on behalf of users.

We searched for {name} (born ~{birth}) across these archives: {', '.join(sources) or 'none'}.

Results: {found_summary}. {corroboration_note}

Research gaps we identified after searching:
{gap_list}

Write 2-3 plain English sentences summarizing: what we found, what is still missing, and what that means for the research.
Do NOT tell the user to go search somewhere themselves — RootBridge does the searching. Be specific and helpful."""

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
