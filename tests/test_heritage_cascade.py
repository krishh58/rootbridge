# tests/test_heritage_cascade.py
from unittest.mock import patch, MagicMock
from app.heritage_cascade import search_ellis_island, extract_origin_village

def test_ellis_island_returns_manifests(app):
    with app.app_context():
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'entries': [{
                'content': {'gedcomx': {'persons': [{
                    'id': 'M123',
                    'names': [{'nameForms': [{'fullText': 'Johann Mueller'}]}],
                    'facts': [
                        {'type': 'http://gedcomx.org/Arrival', 'date': {'original': '1882'},
                         'place': {'original': 'New York'}},
                        {'type': 'http://gedcomx.org/Birth',
                         'place': {'original': 'Bavaria, Germany'}},
                    ]
                }]}}
            }]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch('app.heritage_cascade.FamilySearchClient') as MockFS:
            MockFS.return_value.get_token.return_value = 'tok'
            MockFS.return_value.search_records.return_value = [{
                'source': 'familysearch_records',
                'raw': mock_resp.json.return_value['entries'][0]['content']['gedcomx'],
                'url': 'https://familysearch.org/ark:/...',
            }]
            results = search_ellis_island(first='Johann', last='Mueller', birth_year=1860)
    assert isinstance(results, list)

def test_extract_origin_village_from_manifest():
    manifest_raw = {
        'persons': [{
            'facts': [
                {'type': 'http://gedcomx.org/Birth', 'place': {'original': 'Dachau, Bavaria, Germany'}},
            ]
        }]
    }
    village = extract_origin_village(manifest_raw)
    assert 'Bavaria' in village or 'Germany' in village

def test_extract_origin_village_returns_empty_on_missing():
    village = extract_origin_village({})
    assert village == ''
