import requests as requests_lib
from unittest.mock import patch, MagicMock
from app.ai_synthesis import synthesize_gaps

def test_synthesize_returns_summary(app):
    with app.app_context():
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'choices': [{'message': {'content': 'Found birth record. Missing parents.'}}]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch('app.ai_synthesis.requests.post', return_value=mock_resp):
            result = synthesize_gaps(
                person={'first_name': 'Christopher', 'last_name': 'Haines',
                        'birth_year': 1760, 'birth_state': 'Virginia'},
                results=[{'source': 'familysearch', 'name': 'Christopher Haines'}],
                gaps=[{'gap_type': 'missing_parents', 'suggested_source': 'FamilySearch',
                       'suggested_query': 'Haines parents Virginia'}]
            )
        assert 'summary' in result
        assert isinstance(result['summary'], str)
        assert len(result['summary']) > 0

def test_synthesize_returns_empty_on_api_error(app):
    with app.app_context():
        with patch('app.ai_synthesis.requests.post', side_effect=requests_lib.RequestException('API down')):
            result = synthesize_gaps(
                person={'first_name': 'Jane', 'last_name': 'Doe'},
                results=[], gaps=[]
            )
        assert result['summary'] == ''
