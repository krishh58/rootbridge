from app.research_context import ResearchContext, ConfirmedFact

def test_basic_construction():
    ctx = ResearchContext(first='James', last='Henderson',
                         birth_year=1929, birth_place='Dearborn, Michigan')
    assert ctx.first == 'James'
    assert ctx.last == 'Henderson'
    assert ctx.birth_year == 1929
    assert ctx.birth_place == 'Dearborn, Michigan'

def test_derived_constraints_birth_year():
    ctx = ResearchContext(first='James', last='Henderson', birth_year=1929)
    c = ctx.constraints
    assert c['birth_year_min'] == 1924
    assert c['birth_year_max'] == 1934

def test_derived_constraints_no_birth_year():
    ctx = ResearchContext(first='James', last='Henderson')
    c = ctx.constraints
    assert 'birth_year_min' not in c

def test_add_confirmed_fact():
    ctx = ResearchContext(first='James', last='Henderson', birth_year=1929)
    ctx.confirm('death_place', 'Baton Rouge, Louisiana', source='death_record')
    assert ctx.confirmed_facts['death_place'].value == 'Baton Rouge, Louisiana'
    assert ctx.confirmed_facts['death_place'].source == 'death_record'

def test_rule_out():
    ctx = ResearchContext(first='James', last='Henderson', birth_year=1929)
    ctx.rule_out('birth in Ohio', reason='1930 Census confirms Michigan')
    assert any('Ohio' in r for r in ctx.ruled_out)

def test_generation_label():
    ctx = ResearchContext(first='James', last='Henderson', birth_year=1929,
                         generation=1, generation_label='target')
    assert ctx.generation == 1
    assert ctx.generation_label == 'target'

def test_score_result_passes_constraint():
    ctx = ResearchContext(first='James', last='Henderson', birth_year=1929,
                         birth_place='Michigan')
    result = {'name': 'James Henderson', 'birth_year': 1930,
              'birth_place': 'Michigan', 'source': 'wikitree'}
    score = ctx.score_result(result)
    assert score >= 70

def test_score_result_fails_year():
    ctx = ResearchContext(first='James', last='Henderson', birth_year=1929)
    result = {'name': 'James Henderson', 'birth_year': 1875, 'source': 'wikitree'}
    score = ctx.score_result(result)
    assert score < 30

def test_score_result_fails_place():
    ctx = ResearchContext(first='James', last='Henderson', birth_year=1929,
                         birth_place='Michigan')
    result = {'name': 'James Henderson', 'birth_year': 1930,
              'birth_place': 'North Carolina', 'source': 'wikitree'}
    score = ctx.score_result(result)
    assert score < 50

def test_to_prompt_block():
    ctx = ResearchContext(first='James', last='Henderson', birth_year=1929,
                         birth_place='Dearborn, Michigan')
    ctx.confirm('father_name', 'Orville Henderson', source='1930 Census')
    ctx.rule_out('birth in Ohio', reason='Census confirms Michigan')
    block = ctx.to_prompt_block()
    assert 'CONFIRMED' in block
    assert 'Orville Henderson' in block
    assert 'RULED OUT' in block
    assert 'Ohio' in block
    assert 'CONSTRAINTS' in block
