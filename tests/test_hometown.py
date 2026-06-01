from unittest.mock import patch, MagicMock
from app.hometown import get_hometown_photo, get_historical_map, get_life_context

def test_get_hometown_photo_returns_url(app):
    with app.app_context():
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'query': {'pages': {
                '1': {'title': 'County Cork', 'original': {'source': 'https://commons.wikimedia.org/img/cork.jpg'}}
            }}
        }
        mock_resp.raise_for_status = MagicMock()
        with patch('app.hometown.requests.get', return_value=mock_resp):
            result = get_hometown_photo('County Cork', 1847)
    assert result['url'] == 'https://commons.wikimedia.org/img/cork.jpg'
    assert result['is_approximate'] is False

def test_get_hometown_photo_returns_approximate_on_no_results(app):
    with app.app_context():
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'query': {'pages': {}}}
        mock_resp.raise_for_status = MagicMock()
        with patch('app.hometown.requests.get', return_value=mock_resp):
            result = get_hometown_photo('Unknown Place', 1800)
    assert result['is_approximate'] is True

def test_get_hometown_photo_returns_none_on_error(app):
    with app.app_context():
        with patch('app.hometown.requests.get', side_effect=Exception('network error')):
            result = get_hometown_photo('Cork', 1847)
    assert result is None

def test_get_life_context_returns_text(app):
    with app.app_context():
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'choices': [{'message': {'content': 'Life was hard in rural Cork.'}}]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch('app.hometown.requests.post', return_value=mock_resp):
            result = get_life_context(
                name='Margaret Haines', birth_place='County Cork',
                birth_year=1820, death_year=1890
            )
    assert isinstance(result, str)
    assert len(result) > 0

def test_get_life_context_returns_empty_on_error(app):
    with app.app_context():
        with patch('app.hometown.requests.post', side_effect=Exception('API error')):
            result = get_life_context('Jane Doe', 'Ohio', 1850, None)
    assert result == ''
