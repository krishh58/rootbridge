# tests/test_heritage_cascade.py
from unittest.mock import patch, MagicMock
from app.heritage_cascade import search_ellis_island, extract_origin_village, detect_1870_wall, run_aa_cascade

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

def test_1870_wall_detected_for_pre_1870_no_parents():
    wall = detect_1870_wall(birth_year=1845, parent_ids=[])
    assert wall is not None
    assert wall['type'] == '1870_wall'
    assert len(wall['guidance']) >= 4
    assert len(wall['sources']) >= 4

def test_1870_wall_not_triggered_with_parents():
    wall = detect_1870_wall(birth_year=1845, parent_ids=[1, 2])
    assert wall is None

def test_1870_wall_not_triggered_post_1870():
    wall = detect_1870_wall(birth_year=1875, parent_ids=[])
    assert wall is None

def test_1870_wall_not_triggered_no_birth_year():
    wall = detect_1870_wall(birth_year=None, parent_ids=[])
    assert wall is None

def test_aa_cascade_includes_wall_alert(app):
    with app.app_context():
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        with patch('app.heritage_cascade.FamilySearchClient') as MockFS, \
             patch('app.heritage_cascade.get_redis', return_value=mock_redis), \
             patch('app.heritage_cascade.search_wpa_narratives', return_value=[]):
            MockFS.return_value.search_records.return_value = []
            result = run_aa_cascade(
                first='Moses', last='Johnson', birth_year=1840, birth_place='Georgia'
            )
    assert result['wall_1870'] is not None
    assert result['cascade_type'] == 'african_american'
