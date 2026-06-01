import logging
from .db import db
from .models import Person, Tree, User, PersonMatch

logger = logging.getLogger(__name__)


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1,
                            prev[j - 1] + (0 if ca == cb else 1)))
        prev = curr
    return prev[-1]


def _names_match(n1: str, n2: str) -> bool:
    a = (n1 or '').strip().lower()
    b = (n2 or '').strip().lower()
    return bool(a and b and _levenshtein(a, b) <= 1)


def score_match(person_a: tuple, person_b: tuple) -> int:
    """
    person_a / person_b: (first_name, last_name, birth_year, birth_state, birth_country)
    Returns confidence score 0-100. Score < 60 means no match.
    """
    first_a, last_a, year_a, state_a, country_a = person_a
    first_b, last_b, year_b, state_b, country_b = person_b

    if not (_names_match(first_a, first_b) and _names_match(last_a, last_b)):
        return 0

    score = 40  # name match baseline

    if year_a is None or year_b is None:
        return 0  # birth_year required
    diff = abs(year_a - year_b)
    if diff > 5:
        return 0
    if diff == 0:
        score += 30
    elif diff <= 2:
        score += 20
    else:
        score += 10

    state_a_n = (state_a or '').strip().lower()
    state_b_n = (state_b or '').strip().lower()
    country_a_n = (country_a or '').strip().lower()
    country_b_n = (country_b or '').strip().lower()

    if state_a_n and state_b_n and state_a_n == state_b_n:
        score += 20
    elif country_a_n and country_b_n and country_a_n == country_b_n:
        score += 10

    return score


def run_matcher(person_id: int = None):
    """
    Compare persons across trees to find ancestor matches.
    If person_id given, compare only that person against all others.
    Otherwise full scan. Skips existing pairs. Writes matches with score >= 60.
    """
    THRESHOLD = 60

    def _person_tuple(p):
        return (p.first_name, p.last_name, p.birth_year, p.birth_state, p.birth_country)

    def _user_id_for_person(p):
        return p.tree.user_id

    user_map = {u.id: u for u in User.query.all()}

    if person_id is not None:
        targets = Person.query.filter_by(id=person_id).all()
        candidates = Person.query.filter(Person.id != person_id).all()
    else:
        targets = Person.query.all()
        candidates = None  # will pair within targets

    existing_pairs = set()
    for m in PersonMatch.query.with_entities(PersonMatch.person_a_id, PersonMatch.person_b_id).all():
        existing_pairs.add((m.person_a_id, m.person_b_id))

    def _process_pair(pa, pb):
        pid_lo, pid_hi = (pa.id, pb.id) if pa.id < pb.id else (pb.id, pa.id)
        if (pid_lo, pid_hi) in existing_pairs:
            return
        uid_a = _user_id_for_person(pa)
        uid_b = _user_id_for_person(pb)
        if uid_a == uid_b:
            return
        user_a = user_map.get(uid_a)
        user_b = user_map.get(uid_b)
        if not user_a or not user_b:
            return
        if not user_a.discovery_enabled or not user_b.discovery_enabled:
            return
        blocked_a = user_a.blocked_user_ids or []
        blocked_b = user_b.blocked_user_ids or []
        if uid_b in blocked_a or uid_a in blocked_b:
            return
        s = score_match(_person_tuple(pa), _person_tuple(pb))
        if s < THRESHOLD:
            return
        pa_id = pa.id if pa.id < pb.id else pb.id
        pb_id = pb.id if pa.id < pb.id else pa.id
        ua_id = uid_a if pa.id < pb.id else uid_b
        ub_id = uid_b if pa.id < pb.id else uid_a
        match = PersonMatch(
            person_a_id=pa_id, person_b_id=pb_id,
            user_a_id=ua_id, user_b_id=ub_id,
            score=s,
        )
        db.session.add(match)
        existing_pairs.add((pa_id, pb_id))
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.error('Failed to write PersonMatch (%s, %s): %s', pa_id, pb_id, exc)

    if person_id:
        for target in targets:
            for cand in candidates:
                _process_pair(target, cand)
    else:
        persons = targets
        for i, pa in enumerate(persons):
            for pb in persons[i + 1:]:
                _process_pair(pa, pb)
