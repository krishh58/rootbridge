from unittest.mock import patch, MagicMock
from app.ai_synthesis import build_synthesis_prompt
from app.research_context import ResearchContext

def test_prompt_includes_context_block():
    ctx = ResearchContext(first='James', last='Henderson', birth_year=1929,
                         birth_place='Dearborn, Michigan')
    ctx.confirm('father_name', 'Orville Henderson', source='1930 Census')
    ctx.rule_out('birth in Ohio', reason='Census confirms Michigan')
    results = [{'title': 'James Henderson', 'source': 'wikitree',
                'birth_year': 1929, 'context_score': 90}]
    prompt = build_synthesis_prompt(ctx, results, gaps=[])
    assert 'CONFIRMED' in prompt
    assert 'Orville Henderson' in prompt
    assert 'RULED OUT' in prompt
    assert 'Ohio' in prompt

def test_prompt_includes_chain_of_thought_instruction():
    ctx = ResearchContext(first='James', last='Henderson', birth_year=1929)
    prompt = build_synthesis_prompt(ctx, [], gaps=[])
    assert 'Step 1' in prompt or 'STEP 1' in prompt
    assert 'constraint' in prompt.lower() or 'confirm' in prompt.lower()

def test_prompt_includes_generation_label():
    ctx = ResearchContext(first='Orville', last='Henderson', birth_year=1906,
                         generation=2, generation_label='parent')
    prompt = build_synthesis_prompt(ctx, [], gaps=[])
    assert 'generation' in prompt.lower() or 'parent' in prompt.lower()

def test_prompt_without_context_still_works():
    """Legacy path: no context provided — prompt still generates cleanly."""
    prompt = build_synthesis_prompt(None, [], gaps=[])
    assert len(prompt) > 50
