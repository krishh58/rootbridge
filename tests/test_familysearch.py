from unittest.mock import patch, MagicMock
from app.familysearch import FamilySearchClient

def test_get_token_fetches_and_caches(app):
    with app.app_context():
        client = FamilySearchClient()
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_response = MagicMock()
        mock_response.json.return_value = {'access_token': 'tok123', 'expires_in': 3600}
        mock_response.raise_for_status = MagicMock()
        with patch('app.familysearch.requests.post', return_value=mock_response), \
             patch('app.familysearch.get_redis', return_value=mock_redis):
            token = client.get_token()
        assert token == 'tok123'
        mock_redis.setex.assert_called_once()

def test_get_token_returns_cached(app):
    with app.app_context():
        client = FamilySearchClient()
        mock_redis = MagicMock()
        mock_redis.get.return_value = 'cached_token'
        with patch('app.familysearch.get_redis', return_value=mock_redis):
            token = client.get_token()
        assert token == 'cached_token'

def test_search_persons_returns_list(app):
    with app.app_context():
        client = FamilySearchClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'entries': [{'content': {'gedcomx': {'persons': [
                {'id': 'abc', 'names': [{'nameForms': [{'fullText': 'Christopher Haines'}]}],
                 'facts': [{'type': 'http://gedcomx.org/Birth', 'date': {'original': '1760'}}]}
            ]}}}]
        }
        mock_response.raise_for_status = MagicMock()
        with patch.object(client, 'get_token', return_value='tok'), \
             patch('app.familysearch.requests.get', return_value=mock_response):
            results = client.search_persons(first='Christopher', last='Haines', birth_year=1760)
        assert isinstance(results, list)
        assert len(results) == 1
        assert results[0]['name'] == 'Christopher Haines'
