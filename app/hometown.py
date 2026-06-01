import requests
from flask import current_app

WIKIMEDIA_API = 'https://en.wikipedia.org/w/api.php'
OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'

def get_hometown_photo(place: str, year: int = None) -> dict | None:
    if not place:
        return None
    try:
        params = {
            'action': 'query', 'titles': place,
            'prop': 'pageimages', 'piprop': 'original',
            'format': 'json',
        }
        resp = requests.get(WIKIMEDIA_API, params=params, timeout=5)
        resp.raise_for_status()
        pages = resp.json().get('query', {}).get('pages', {})
        for page in pages.values():
            url = page.get('original', {}).get('source', '')
            if url and url.lower().endswith(('.jpg', '.jpeg', '.png')):
                return {'url': url, 'title': page.get('title', ''),
                        'credit': 'Wikimedia Commons', 'is_approximate': False}
        return {'url': _regional_fallback(place), 'is_approximate': True,
                'title': f'Region: {place}', 'credit': 'Wikimedia Commons'}
    except Exception:
        return None

def _regional_fallback(place: str) -> str:
    country_map = {
        'ireland': 'https://upload.wikimedia.org/wikipedia/commons/thumb/4/45/Flag_of_Ireland.svg/320px-Flag_of_Ireland.svg.png',
        'germany': 'https://upload.wikimedia.org/wikipedia/commons/thumb/b/be/Flag_of_Germany.svg/320px-Flag_of_Germany.svg.png',
        'england': 'https://upload.wikimedia.org/wikipedia/commons/thumb/b/be/Flag_of_England.svg/320px-Flag_of_England.svg.png',
        'sweden': 'https://upload.wikimedia.org/wikipedia/commons/thumb/4/4c/Flag_of_Sweden.svg/320px-Flag_of_Sweden.svg.png',
    }
    place_lower = place.lower()
    for keyword, url in country_map.items():
        if keyword in place_lower:
            return url
    return ''

def get_historical_map(place: str, year: int = None) -> dict | None:
    if not place:
        return None
    try:
        year_str = str(year) if year else '1850'
        params = {
            'lc': 'RUMSEY~8~1',
            'q': place,
            'sort': 'Pub_Date',
            'order': 'descending',
            'pgs': '5', 'res': '1',
        }
        resp = requests.get(
            'https://luna.davidrumsey.com/luna/servlet/as/search',
            params=params, timeout=5
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get('results', [])
        if results:
            item = results[0]
            return {
                'url': item.get('urlsize4', item.get('urlsize3', '')),
                'title': item.get('title', ''),
                'year': item.get('pub_date', year_str),
                'credit': 'David Rumsey Map Collection',
            }
    except Exception:
        pass
    return None

def get_life_context(name: str, birth_place: str, birth_year: int,
                     death_year: int) -> str:
    try:
        era = f"{birth_year}–{death_year}" if death_year else f"born {birth_year}"
        prompt = f"""In 2-3 sentences, describe what daily life was like for an ordinary person living in {birth_place} around {era}. Focus on: the economic conditions, major historical events that would have affected their daily life, and why families might have emigrated from there. Be specific and evocative. Do not mention {name} by name."""
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                'Authorization': f"Bearer {current_app.config['OPENROUTER_API_KEY']}",
                'Content-Type': 'application/json',
            },
            json={
                'model': 'anthropic/claude-3-haiku',
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 120,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()['choices'][0]['message']['content'].strip()
    except Exception:
        return ''
