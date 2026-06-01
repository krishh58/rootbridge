import json
from unittest.mock import patch
from app.models import User
from app.db import db

MOCK_EU_CASCADE = {
    'results': [{'source': 'familysearch_eu_germany', 'url': 'https://fs.org/1'}],
    'gaps': [], 'confidence': 60, 'detected_country': 'Germany', 'cascade_type': 'european'
}
MOCK_AA_CASCADE = {
    'results': [{'source': 'freedmens_bureau', 'url': 'https://fs.org/2'}],
    'gaps': [], 'confidence': 40,
    'wall_1870': None, 'cascade_type': 'african_american'
}

def _register_with_tier(client, app, email, tier):
    client.post('/auth/register',
        data=json.dumps({'email': email, 'password': 'pass'}),
        content_type='application/json')
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        user.tier = tier
        user.token_balance = 500
        db.session.commit()

def _create_person(client):
    r = client.post('/api/persons',
        data=json.dumps({'tree_name': 'T', 'first_name': 'J', 'last_name': 'D',
                         'birth_year': 1850, 'birth_state': 'Ohio'}),
        content_type='application/json')
    return r.get_json()['person_id']

def test_european_cascade_requires_tier(client, app):
    client.post('/auth/register',
        data=json.dumps({'email': 'free_eu@test.com', 'password': 'pass'}),
        content_type='application/json')
    person_id = _create_person(client)
    r = client.post(f'/api/heritage/{person_id}/european',
        data=json.dumps({}), content_type='application/json')
    assert r.status_code == 403

def test_european_cascade_allowed_for_paid_tier(client, app):
    _register_with_tier(client, app, 'eu_tier@test.com', 'us')
    person_id = _create_person(client)
    with patch('app.heritage_routes.run_european_cascade', return_value=MOCK_EU_CASCADE), \
         patch('app.heritage_routes.synthesize_gaps', return_value={'summary': 'Found records.'}):
        r = client.post(f'/api/heritage/{person_id}/european',
            data=json.dumps({'origin_country': 'Germany'}),
            content_type='application/json')
    assert r.status_code == 200
    assert r.get_json()['cascade_type'] == 'european'
    assert 'summary' in r.get_json()

def test_aa_cascade_requires_tier(client, app):
    client.post('/auth/register',
        data=json.dumps({'email': 'free_aa@test.com', 'password': 'pass'}),
        content_type='application/json')
    person_id = _create_person(client)
    r = client.post(f'/api/heritage/{person_id}/aa',
        data=json.dumps({}), content_type='application/json')
    assert r.status_code == 403

def test_aa_cascade_returns_wall_alert(client, app):
    _register_with_tier(client, app, 'aa_tier@test.com', 'us')
    person_id = _create_person(client)
    wall_cascade = {**MOCK_AA_CASCADE, 'wall_1870': {
        'type': '1870_wall', 'message': 'Born before 1870.',
        'guidance': ["Check Freedmen's Bureau"], 'sources': []
    }}
    with patch('app.heritage_routes.run_aa_cascade', return_value=wall_cascade), \
         patch('app.heritage_routes.synthesize_gaps', return_value={'summary': 'Limited records.'}):
        r = client.post(f'/api/heritage/{person_id}/aa',
            data=json.dumps({}), content_type='application/json')
    assert r.status_code == 200
    assert r.get_json()['wall_1870'] is not None
