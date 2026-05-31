import requests
from flask import current_app
from .db import get_redis

FAMILYSEARCH_BASE = 'https://api.familysearch.org'
TOKEN_CACHE_KEY = 'fs:access_token'

class FamilySearchClient:

    def get_token(self) -> str:
        r = get_redis()
        cached = r.get(TOKEN_CACHE_KEY)
        if cached:
            return cached
        resp = requests.post(
            f'{FAMILYSEARCH_BASE}/cis-web/oauth2/v3/token',
            data={
                'grant_type': 'client_credentials',
                'client_id': current_app.config['FAMILYSEARCH_CLIENT_ID'],
                'client_secret': current_app.config['FAMILYSEARCH_CLIENT_SECRET'],
            }
        )
        resp.raise_for_status()
        data = resp.json()
        token = data['access_token']
        expires_in = int(data.get('expires_in', 3600)) - 60
        r.setex(TOKEN_CACHE_KEY, expires_in, token)
        return token

    def _headers(self) -> dict:
        return {
            'Authorization': f'Bearer {self.get_token()}',
            'Accept': 'application/x-fs-v1+json',
        }

    def search_persons(self, first: str = '', last: str = '',
                       birth_year: int = None, birth_place: str = '') -> list:
        params = {'q': self._build_query(first, last, birth_year, birth_place), 'count': 10}
        resp = requests.get(
            f'{FAMILYSEARCH_BASE}/platform/tree/search',
            headers=self._headers(), params=params
        )
        resp.raise_for_status()
        return self._parse_person_entries(resp.json())

    def search_records(self, collection_id: str, first: str = '', last: str = '',
                       birth_year: int = None) -> list:
        params = {'q': self._build_query(first, last, birth_year), 'count': 10}
        resp = requests.get(
            f'{FAMILYSEARCH_BASE}/platform/records/search',
            headers=self._headers(), params=params,
        )
        resp.raise_for_status()
        return self._parse_record_entries(resp.json())

    def _build_query(self, first, last, birth_year, birth_place='') -> str:
        parts = []
        if first:
            parts.append(f'givenName:"{first}"')
        if last:
            parts.append(f'surname:"{last}"')
        if birth_year:
            parts.append(f'birthLikeDate:"{birth_year-3} TO {birth_year+3}"')
        if birth_place:
            parts.append(f'birthLikePlace:"{birth_place}"')
        return ' '.join(parts)

    def _parse_person_entries(self, data: dict) -> list:
        results = []
        for entry in data.get('entries', []):
            try:
                persons = entry['content']['gedcomx']['persons']
                for p in persons:
                    name = ''
                    if p.get('names'):
                        forms = p['names'][0].get('nameForms', [])
                        if forms:
                            name = forms[0].get('fullText', '')
                    birth_year = None
                    for fact in p.get('facts', []):
                        if 'Birth' in fact.get('type', ''):
                            date_str = fact.get('date', {}).get('original', '')
                            if date_str and date_str.isdigit():
                                birth_year = int(date_str)
                    results.append({
                        'name': name,
                        'birth_year': birth_year,
                        'fs_id': p.get('id', ''),
                        'source': 'familysearch',
                        'url': f'https://www.familysearch.org/tree/person/details/{p.get("id", "")}',
                    })
            except (KeyError, IndexError):
                continue
        return results

    def _parse_record_entries(self, data: dict) -> list:
        results = []
        for entry in data.get('entries', []):
            try:
                record = entry.get('content', {}).get('gedcomx', {})
                results.append({
                    'source': 'familysearch_records',
                    'raw': record,
                    'url': entry.get('links', {}).get('self', {}).get('href', ''),
                })
            except (KeyError, IndexError):
                continue
        return results
