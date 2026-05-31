from app.gap_classifier import classify_gaps, confidence_score

def test_confidence_full_data():
    person = {
        'first_name': 'Christopher', 'last_name': 'Haines',
        'birth_year': 1760, 'birth_state': 'Virginia',
        'death_year': 1846, 'death_place': 'Allen County KY',
        'parent_ids': [1, 2], 'spouse_ids': [3],
    }
    assert confidence_score(person) >= 90

def test_confidence_name_only():
    person = {
        'first_name': 'John', 'last_name': 'Doe',
        'birth_year': None, 'birth_state': None,
        'death_year': None, 'death_place': None,
        'parent_ids': [], 'spouse_ids': [],
    }
    assert confidence_score(person) <= 20

def test_gaps_missing_parents():
    person = {'parent_ids': [], 'birth_year': 1760, 'birth_state': 'VA',
              'death_year': 1846, 'death_place': 'KY', 'spouse_ids': [1]}
    gaps = classify_gaps(person)
    types = [g['gap_type'] for g in gaps]
    assert 'missing_parents' in types

def test_gaps_missing_birth():
    person = {'parent_ids': [1], 'birth_year': None, 'birth_state': None,
              'death_year': 1846, 'death_place': 'KY', 'spouse_ids': []}
    gaps = classify_gaps(person)
    types = [g['gap_type'] for g in gaps]
    assert 'missing_birth' in types

def test_gaps_none_when_complete():
    person = {'parent_ids': [1, 2], 'birth_year': 1760, 'birth_state': 'VA',
              'death_year': 1846, 'death_place': 'KY', 'spouse_ids': [3]}
    gaps = classify_gaps(person)
    assert len(gaps) == 0

def test_gap_has_suggested_source():
    person = {'parent_ids': [], 'birth_year': 1760, 'birth_state': 'VA',
              'death_year': None, 'death_place': None, 'spouse_ids': []}
    gaps = classify_gaps(person)
    for gap in gaps:
        assert 'suggested_source' in gap
        assert 'suggested_query' in gap
