# tests/test_translation.py
from unittest.mock import patch, MagicMock
from app.translation import translate_document, detect_language

def test_detect_language_german():
    text = 'Geburtsname Johann Mueller geboren am 15 März 1842 in Bayern'
    lang = detect_language(text)
    assert lang == 'de'

def test_detect_language_english():
    text = 'Born in the county of Cork Ireland on the third day of May'
    lang = detect_language(text)
    assert lang == 'en'

def test_detect_language_unknown():
    lang = detect_language('')
    assert lang == 'unknown'

def test_translate_document_returns_translation(app):
    with app.app_context():
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'choices': [{'message': {'content': '{"translation": "Birth record for Johann Mueller, born March 15, 1842 in Bavaria.", "key_fields": {"name": "Johann Mueller", "birth_date": "March 15, 1842", "birth_place": "Bavaria"}}'}}]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch('app.translation.requests.post', return_value=mock_resp):
            result = translate_document(
                text='Geburtsname Johann Mueller geboren am 15 März 1842 in Bayern',
                source_language='de'
            )
    assert 'translation' in result
    assert 'key_fields' in result

def test_translate_document_returns_empty_on_error(app):
    with app.app_context():
        with patch('app.translation.requests.post', side_effect=Exception('API error')):
            result = translate_document(text='text', source_language='de')
    assert result == {}
