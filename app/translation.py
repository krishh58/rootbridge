import re
import json as jsonlib
import requests
from flask import current_app

OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'

LANGUAGE_PATTERNS = {
    'de': r'\b(geboren|gestorben|Geburtsname|verheiratet|Taufe|Pfarrei|Bayern|Preußen)\b',
    'fr': r'\b(né|née|mort|décédé|mariage|paroisse|département)\b',
    'sv': r'\b(född|döpt|begraven|kyrkbok|socken|husförhör)\b',
    'it': r'\b(nato|nata|morto|battesimo|matrimonio|comune|provincia)\b',
    'pl': r'\b(urodzony|urodzona|chrzciny|ślub|parafia|gmina)\b',
    'ga': r'\b(baistí|pósadh|bás|paróiste|contae)\b',
}

def detect_language(text: str) -> str:
    if not text:
        return 'unknown'
    for lang, pattern in LANGUAGE_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE):
            return lang
    ascii_ratio = sum(1 for c in text if ord(c) < 128) / max(len(text), 1)
    if ascii_ratio > 0.95:
        return 'en'
    return 'unknown'

def translate_document(text: str, source_language: str = 'de') -> dict:
    lang_names = {
        'de': 'German', 'fr': 'French', 'sv': 'Swedish',
        'it': 'Italian', 'pl': 'Polish', 'ga': 'Irish Gaelic',
    }
    lang_name = lang_names.get(source_language, source_language)
    prompt = f"""Translate this {lang_name} genealogical record to English. Extract key genealogical fields.

TEXT:
{text[:2000]}

Respond with ONLY valid JSON in this exact format:
{{"translation": "full English translation here", "key_fields": {{"name": "...", "birth_date": "...", "birth_place": "...", "death_date": "...", "death_place": "...", "father": "...", "mother": "...", "spouse": "..."}}}}

Omit any key_fields that are not mentioned in the document."""
    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                'Authorization': f"Bearer {current_app.config['OPENROUTER_API_KEY']}",
                'Content-Type': 'application/json',
            },
            json={
                'model': 'anthropic/claude-3-haiku',
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 400,
            },
            timeout=15,
        )
        resp.raise_for_status()
        content = resp.json()['choices'][0]['message']['content'].strip()
        content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content, flags=re.MULTILINE).strip()
        return jsonlib.loads(content)
    except Exception:
        return {}
