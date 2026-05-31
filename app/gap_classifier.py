FIELD_WEIGHTS = {
    'birth_year': 20,
    'birth_state': 10,
    'death_year': 15,
    'death_place': 10,
    'parent_ids': 25,
    'spouse_ids': 10,
    'first_name': 5,
    'last_name': 5,
}


def confidence_score(person: dict) -> int:
    total = 0
    for field, weight in FIELD_WEIGHTS.items():
        val = person.get(field)
        if val is not None and val != [] and val != '':
            total += weight
    return min(total, 100)


def classify_gaps(person: dict) -> list:
    gaps = []
    name = f"{person.get('first_name', '')} {person.get('last_name', '')}".strip()
    birth_year = person.get('birth_year')
    birth_state = person.get('birth_state', '')

    if not person.get('parent_ids'):
        gaps.append({
            'gap_type': 'missing_parents',
            'suggested_source': 'FamilySearch',
            'suggested_query': f'{name} {birth_year or ""} {birth_state or ""} parents'.strip(),
        })

    if not birth_year or not person.get('birth_state'):
        gaps.append({
            'gap_type': 'missing_birth',
            'suggested_source': 'FamilySearch Vital Records',
            'suggested_query': f'{name} birth record {birth_state or ""}'.strip(),
        })

    if not person.get('death_year') or not person.get('death_place'):
        gaps.append({
            'gap_type': 'missing_death',
            'suggested_source': 'Find A Grave / FamilySearch',
            'suggested_query': f'{name} death {birth_year or ""}',
        })

    if not person.get('spouse_ids'):
        gaps.append({
            'gap_type': 'missing_spouse',
            'suggested_source': 'FamilySearch Marriage Records',
            'suggested_query': f'{name} marriage record',
        })

    return gaps
