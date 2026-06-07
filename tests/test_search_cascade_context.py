from app.research_context import ResearchContext
from app.search_cascade import cross_reference_with_context


def test_filter_drops_wrong_era():
    ctx = ResearchContext(first='James', last='Henderson', birth_year=1929,
                         birth_place='Michigan')
    results = [
        {'title': 'James Henderson', 'birth_year': 1929, 'source': 'wikitree',
         'birth_place': 'Michigan'},
        {'title': 'James Henderson', 'birth_year': 1875, 'source': 'nara',
         'birth_place': 'North Carolina'},
        {'title': 'James Henderson', 'birth_year': 1930, 'source': 'chronicling',
         'birth_place': 'Michigan'},
    ]
    filtered = cross_reference_with_context(results, ctx)
    years = [r.get('birth_year') for r in filtered]
    assert 1875 not in years
    assert 1929 in years or 1930 in years


def test_filter_adds_context_score():
    ctx = ResearchContext(first='James', last='Henderson', birth_year=1929)
    results = [{'title': 'James Henderson', 'birth_year': 1929, 'source': 'wikitree'}]
    filtered = cross_reference_with_context(results, ctx)
    assert 'context_score' in filtered[0]


def test_filter_no_context_passthrough():
    """Without a context, all results pass through unchanged."""
    results = [
        {'title': 'James Henderson', 'birth_year': 1875, 'source': 'nara'},
        {'title': 'James Henderson', 'birth_year': 1929, 'source': 'wikitree'},
    ]
    filtered = cross_reference_with_context(results, ctx=None)
    assert len(filtered) == 2


def test_filter_preserves_results_when_no_constraints_match():
    """If constraints can't be evaluated (no dates on results), don't drop them."""
    ctx = ResearchContext(first='James', last='Henderson', birth_year=1929)
    results = [
        {'title': 'James Henderson', 'source': 'wikitree'},
        {'title': 'James Henderson', 'source': 'nara'},
    ]
    filtered = cross_reference_with_context(results, ctx)
    assert len(filtered) == 2
