from unittest.mock import patch
import json

def _run_stream(first, last, birth_year=None, birth_place=''):
    from app.search_cascade import run_us_cascade_stream
    events = []
    with patch('app.search_cascade.search_wikitree', return_value=[]):
        with patch('app.search_cascade.search_chronicling', return_value=[]):
            with patch('app.search_cascade.search_nara', return_value=[]):
                with patch('app.ai_synthesis.agentic_pick_sources', return_value=[]):
                    with patch('app.ai_synthesis.synthesize_gaps',
                               return_value={'summary': 'test summary'}):
                        for chunk in run_us_cascade_stream(
                                first=first, last=last,
                                birth_year=birth_year, birth_place=birth_place,
                                skip_vault=True):
                            if chunk.startswith('data: '):
                                try:
                                    events.append(json.loads(chunk[6:]))
                                except Exception:
                                    pass
    return events

def test_stream_emits_generation_event():
    events = _run_stream('James', 'Henderson', birth_year=1929,
                         birth_place='Michigan')
    gen_events = [e for e in events if e.get('generation') is not None]
    assert len(gen_events) >= 1
    assert gen_events[0]['generation'] == 1

def test_stream_emits_context_constraints():
    events = _run_stream('James', 'Henderson', birth_year=1929,
                         birth_place='Michigan')
    ctx_events = [e for e in events if 'constraints' in e]
    assert len(ctx_events) >= 1
    constraints = ctx_events[0]['constraints']
    assert constraints['birth_year_min'] == 1924
    assert constraints['birth_year_max'] == 1934
