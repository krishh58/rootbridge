from app.models import PersonMatch, ResearchMessage

def test_person_match_model_exists():
    assert PersonMatch.__tablename__ == 'person_matches'

def test_research_message_model_exists():
    assert ResearchMessage.__tablename__ == 'research_messages'
