from unittest.mock import patch, MagicMock
from app.search_cascade import run_us_cascade

def test_cascade_returns_results_and_gaps(app):
    with app.app_context():
        mock_fs = MagicMock()
        mock_fs.search_persons.return_value = [
            {'name': 'Christopher Haines', 'birth_year': 1760,
             'source': 'familysearch', 'url': 'https://familysearch.org/abc'}
        ]
        mock_fs.search_records.return_value = []
        with patch('app.search_cascade.FamilySearchClient', return_value=mock_fs), \
             patch('app.search_cascade.search_wikitree', return_value=[]), \
             patch('app.search_cascade.search_chronicling', return_value=[]):
            result = run_us_cascade(
                first='Christopher', last='Haines',
                birth_year=1760, birth_place='Virginia'
            )
    assert 'results' in result
    assert 'gaps' in result
    assert 'confidence' in result
    assert isinstance(result['results'], list)
    assert isinstance(result['gaps'], list)
    assert 0 <= result['confidence'] <= 100

def test_cascade_caches_result(app):
    with app.app_context():
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_fs = MagicMock()
        mock_fs.search_persons.return_value = []
        mock_fs.search_records.return_value = []
        with patch('app.search_cascade.FamilySearchClient', return_value=mock_fs), \
             patch('app.search_cascade.search_wikitree', return_value=[]), \
             patch('app.search_cascade.search_chronicling', return_value=[]), \
             patch('app.search_cascade.get_redis', return_value=mock_redis):
            run_us_cascade(first='John', last='Doe', birth_year=1800)
        mock_redis.setex.assert_called_once()

def test_cascade_uses_cache(app):
    with app.app_context():
        import json
        cached = json.dumps({'results': [], 'gaps': [], 'confidence': 0, 'cached': True})
        mock_redis = MagicMock()
        mock_redis.get.return_value = cached
        with patch('app.search_cascade.get_redis', return_value=mock_redis):
            result = run_us_cascade(first='Jane', last='Smith')
        assert result.get('cached') is True
