"""
Geni.com API search layer.

Geni requires OAuth2. Set GENI_ACCESS_TOKEN in .env to enable.

Get credentials at: https://www.geni.com/platform/developers/apps
  1. Register an app → get client_id + client_secret
  2. Client credentials grant:
       POST https://www.geni.com/platform/oauth/request_token
         ?client_id=...&client_secret=...&grant_type=client_credentials
  3. Put the returned access_token in GENI_ACCESS_TOKEN in .env

Endpoint used:
  GET https://www.geni.com/api/profile/search
    ?q={first}+{last}&fields=id,name,first_name,last_name,birth,death,gender
    &access_token={token}
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

GENI_TOKEN    = os.environ.get('GENI_ACCESS_TOKEN', '')
GENI_BASE_URL = 'https://www.geni.com/api'
TIMEOUT       = 10


def _year_from_event(event: dict | None) -> int | None:
    if not event:
        return None
    date = event.get('date', {})
    if isinstance(date, dict):
        y = date.get('year')
        if y:
            return int(y)
    return None


def _place_from_event(event: dict | None) -> str:
    if not event:
        return ''
    loc = event.get('location', {})
    if isinstance(loc, dict):
        parts = [loc.get('city', ''), loc.get('state', ''), loc.get('country', '')]
        return ', '.join(p for p in parts if p)
    return ''


def search_geni(first: str, last: str, birth_year=None,
                birth_place: str = '', limit: int = 10) -> list[dict]:
    """
    Search Geni.com for a person. Returns list of result dicts compatible with
    search_cascade result format.
    """
    if not GENI_TOKEN or not last:
        return []

    q = f'{first} {last}'.strip() if first else last

    params = {
        'q': q,
        'fields': 'id,name,first_name,last_name,birth,death,gender,profile_url',
        'access_token': GENI_TOKEN,
    }

    try:
        resp = requests.get(
            f'{GENI_BASE_URL}/profile/search',
            params=params,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning('Geni search failed for %s %s: %s', first, last, e)
        return []

    if 'error' in data:
        logger.warning('Geni API error: %s', data['error'])
        return []

    profiles = data.get('results', [])
    results = []

    for profile in profiles[:limit]:
        rec_first = profile.get('first_name', '') or ''
        rec_last  = profile.get('last_name', '')  or ''

        # Loose last-name filter — Geni search isn't exact
        if rec_last.upper()[:3] != last.upper()[:3]:
            continue

        birth_evt = profile.get('birth')
        death_evt = profile.get('death')

        rec_birth_year = _year_from_event(birth_evt)
        rec_death_year = _year_from_event(death_evt)
        rec_birth_place = _place_from_event(birth_evt)

        # Year filter: ±10 years
        if birth_year and rec_birth_year:
            try:
                if abs(int(rec_birth_year) - int(birth_year)) > 10:
                    continue
            except (TypeError, ValueError):
                pass

        profile_url = profile.get('profile_url', '')
        if profile_url and not profile_url.startswith('http'):
            profile_url = f'https://www.geni.com{profile_url}'

        name = profile.get('name') or f'{rec_first} {rec_last}'.strip()

        snippet_parts = []
        if rec_birth_year:
            snippet_parts.append(f'born {rec_birth_year}')
        if rec_death_year:
            snippet_parts.append(f'died {rec_death_year}')
        if rec_birth_place:
            snippet_parts.append(rec_birth_place)

        results.append({
            'source':      'geni',
            'record_type': 'family_tree',
            'title':       name,
            'name':        name,
            'first_name':  rec_first,
            'last_name':   rec_last,
            'birth_year':  rec_birth_year,
            'death_year':  rec_death_year,
            'birth_place': rec_birth_place or birth_place,
            'location':    rec_birth_place,
            'gender':      profile.get('gender', ''),
            'url':         profile_url,
            'snippet':     'Geni — ' + ', '.join(snippet_parts) if snippet_parts else 'Geni world family tree',
        })

    return results


def geni_available() -> bool:
    """Return True if a Geni access token is configured."""
    return bool(GENI_TOKEN)
