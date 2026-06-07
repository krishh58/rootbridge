from app.research_context import ResearchContext
from app.search_cascade import build_parent_context


def test_parent_context_increments_generation():
    child_ctx = ResearchContext(first='James', last='Henderson',
                               birth_year=1929, birth_place='Michigan',
                               generation=1, generation_label='target')
    child_ctx.confirm('father_name', 'Orville Henderson', source='1930 Census')
    child_ctx.confirm('father_birth_place', 'Oklahoma', source='1930 Census')

    parent_ctx = build_parent_context(
        child_ctx=child_ctx,
        parent_first='Orville',
        parent_last='Henderson',
        parent_birth_year=1906,
        parent_birth_place='Oklahoma',
    )
    assert parent_ctx.generation == 2
    assert parent_ctx.generation_label == 'parent'
    assert parent_ctx.first == 'Orville'
    assert parent_ctx.birth_year == 1906
    assert parent_ctx.birth_place == 'Oklahoma'


def test_parent_context_inherits_ruled_out():
    child_ctx = ResearchContext(first='James', last='Henderson', birth_year=1929)
    child_ctx.rule_out('father born in Ohio', reason='Census says Kansas')
    parent_ctx = build_parent_context(
        child_ctx=child_ctx,
        parent_first='George',
        parent_last='Henderson',
    )
    # Parent context starts fresh — does not inherit child's ruled-out list
    assert parent_ctx.ruled_out == []


def test_parent_context_generation_label():
    child_ctx = ResearchContext(generation=2, generation_label='parent')
    parent_ctx = build_parent_context(child_ctx=child_ctx,
                                       parent_first='George',
                                       parent_last='Henderson')
    assert parent_ctx.generation == 3
    assert parent_ctx.generation_label == 'grandparent'
