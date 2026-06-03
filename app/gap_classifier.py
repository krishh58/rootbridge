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

# Sources we actually search — shown to users when we explain what we covered
SOURCE_LABELS = {
    'wikitree': 'WikiTree collaborative tree',
    'chronicling_america': 'Chronicling America newspapers',
    'dpla_census': 'DPLA census records',
    'dpla_military': 'DPLA military records',
    'dpla': 'DPLA cultural heritage archives',
    'nara': 'National Archives (NARA)',
    'dpla_obituary': 'DPLA obituary records',
    'chronicling_obituary': 'Chronicling America obituaries',
    'dpla_marriage': 'DPLA marriage records',
    'dpla_birth': 'DPLA birth records',
}


def confidence_score(person: dict) -> int:
    total = 0
    for field, weight in FIELD_WEIGHTS.items():
        val = person.get(field)
        if val is not None and val != [] and val != '':
            total += weight
    return min(total, 100)


def classify_gaps(person: dict, results: list = None) -> list:
    """
    Report research gaps based on what we actually searched and what we found.
    Never tells the user to go search somewhere else — we do the searching.
    """
    gaps = []
    results = results or []
    name = f"{person.get('first_name', '')} {person.get('last_name', '')}".strip()
    birth_year = person.get('birth_year')
    birth_state = person.get('birth_state', '')

    sources_searched = list({SOURCE_LABELS.get(r.get('source', ''), r.get('source', ''))
                              for r in results})

    def _sources_with(record_types):
        return [SOURCE_LABELS.get(r.get('source', ''), r.get('source', ''))
                for r in results if r.get('record_type') in record_types]

    # --- Missing birth info ---
    if not birth_year or not birth_state:
        census_hits = _sources_with(['census', 'dpla_census'])
        gaps.append({
            'gap_type': 'missing_birth',
            'label': 'Birth information incomplete',
            'detail': (
                f"We found {len(census_hits)} census record(s) that may contain birth details."
                if census_hits else
                "No census records returned birth information. "
                "We searched DPLA census collections and NARA federal records."
            ),
            'we_searched': ['DPLA census records', 'National Archives (NARA)', 'WikiTree'],
            'found': bool(census_hits),
        })

    # --- Missing death info ---
    if not person.get('death_year') or not person.get('death_place'):
        obit_hits = _sources_with(['newspaper', 'obituary', 'chronicling_obituary', 'dpla_obituary'])
        gaps.append({
            'gap_type': 'missing_death',
            'label': 'Death record not found',
            'detail': (
                f"We found {len(obit_hits)} newspaper record(s) that may contain death or obituary information."
                if obit_hits else
                "No death records or obituaries surfaced. "
                "We searched Chronicling America historical newspapers and DPLA vital records."
            ),
            'we_searched': ['Chronicling America newspapers', 'DPLA cultural heritage archives',
                            'National Archives (NARA)'],
            'found': bool(obit_hits),
        })

    # --- Missing parents ---
    if not person.get('parent_ids'):
        tree_hits = _sources_with(['family_tree'])
        census_hits = _sources_with(['census', 'dpla_census'])
        gaps.append({
            'gap_type': 'missing_parents',
            'label': 'Parents not identified',
            'detail': (
                f"We found {len(tree_hits)} family tree record(s) and {len(census_hits)} census "
                f"record(s) that may list parents or household members."
                if tree_hits or census_hits else
                "No parent records found. Census records from this era list household members "
                "which can identify parents. We searched WikiTree and DPLA census collections."
            ),
            'we_searched': ['WikiTree collaborative tree', 'DPLA census records',
                            'National Archives (NARA)'],
            'found': bool(tree_hits or census_hits),
        })

    # --- Missing spouse ---
    if not person.get('spouse_ids'):
        marriage_hits = _sources_with(['marriage', 'dpla_marriage', 'family_tree'])
        gaps.append({
            'gap_type': 'missing_spouse',
            'label': 'Marriage record not found',
            'detail': (
                f"We found {len(marriage_hits)} record(s) that may reference a spouse or marriage."
                if marriage_hits else
                "No marriage records found. We searched Chronicling America for marriage "
                "announcements, DPLA vital records, and WikiTree family connections."
            ),
            'we_searched': ['WikiTree collaborative tree', 'Chronicling America newspapers',
                            'DPLA cultural heritage archives'],
            'found': bool(marriage_hits),
        })

    return gaps
