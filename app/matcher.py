import logging
from .db import db
from .models import Person, Tree, User, PersonMatch
from .match_index import lookup_candidates, soundex, _decade

logger = logging.getLogger(__name__)

THRESHOLD = 60


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
        return 0
    diff = abs(year_a - year_b)
    if diff > 5:
        return 0
    if diff == 0:
        score += 30
    elif diff <= 2:
        score += 20
    else:
        score += 10

    state_a_n   = (state_a   or '').strip().lower()
    state_b_n   = (state_b   or '').strip().lower()
    country_a_n = (country_a or '').strip().lower()
    country_b_n = (country_b or '').strip().lower()

    if state_a_n and state_b_n and state_a_n == state_b_n:
        score += 20
    elif country_a_n and country_b_n and country_a_n == country_b_n:
        score += 10

    return score


def _load_user_map():
    return {u.id: u for u in User.query.all()}


def _existing_pairs():
    pairs = set()
    for m in PersonMatch.query.with_entities(
            PersonMatch.person_a_id, PersonMatch.person_b_id).all():
        pairs.add((m.person_a_id, m.person_b_id))
    return pairs


def _try_commit_match(pa_id, pb_id, ua_id, ub_id, score, existing_pairs):
    match = PersonMatch(
        person_a_id=pa_id, person_b_id=pb_id,
        user_a_id=ua_id,   user_b_id=ub_id,
        score=score,
    )
    db.session.add(match)
    try:
        db.session.commit()
        existing_pairs.add((pa_id, pb_id))
    except Exception as exc:
        logger.error('Failed to commit match (%s, %s): %s', pa_id, pb_id, exc)
        db.session.rollback()


def run_matcher(person_id: int = None):
    """
    Compare persons across trees to find ancestor matches.

    Single-person mode  (person_id given):
        Uses the soundex+decade index to pull a small candidate set — O(bucket_size)
        instead of O(n). Typical bucket: tens to a few hundred rows.

    Full-scan mode (no person_id):
        Groups persons by (soundex_key, birth_decade) and compares only within each
        bucket, reducing O(n²) to O(k * b²) where k=buckets, b=avg bucket size.
        Falls back to the old full-table approach if the index isn't built.
    """
    user_map      = _load_user_map()
    existing_pairs = _existing_pairs()

    def _check_users(uid_a, uid_b):
        u_a = user_map.get(uid_a)
        u_b = user_map.get(uid_b)
        if not u_a or not u_b:
            return False
        if not u_a.discovery_enabled or not u_b.discovery_enabled:
            return False
        if uid_b in (u_a.blocked_user_ids or []) or uid_a in (u_b.blocked_user_ids or []):
            return False
        return True

    def _process_pair(pa_id, pa_tuple, pa_uid, pb_id, pb_tuple, pb_uid):
        lo, hi = (pa_id, pb_id) if pa_id < pb_id else (pb_id, pa_id)
        if (lo, hi) in existing_pairs:
            return
        if pa_uid == pb_uid:
            return
        if not _check_users(pa_uid, pb_uid):
            return
        s = score_match(pa_tuple, pb_tuple)
        if s < THRESHOLD:
            return
        ua = pa_uid if pa_id < pb_id else pb_uid
        ub = pb_uid if pa_id < pb_id else pa_uid
        _try_commit_match(lo, hi, ua, ub, s, existing_pairs)

    # ── Single-person mode ────────────────────────────────────────────────────
    if person_id is not None:
        target = Person.query.filter_by(id=person_id).first()
        if not target:
            return

        t_tuple = (target.first_name, target.last_name,
                   target.birth_year, target.birth_state, target.birth_country)
        t_uid   = target.tree.user_id

        candidates = lookup_candidates(target.last_name, target.birth_year, target.birth_country)

        for row in candidates:
            cid, cfn, cln, cby, cbs, cbc, _tree_id, cuid = row
            if cid == person_id:
                continue
            c_tuple = (cfn, cln, cby, cbs, cbc)
            _process_pair(person_id, t_tuple, t_uid, cid, c_tuple, cuid)
        return

    # ── Full-scan mode — bucket-by-bucket ─────────────────────────────────────
    from .match_index import _INDEX, _BUILT, _LOCK

    with _LOCK:
        built = _BUILT
        if built:
            # snapshot bucket keys so we release the lock before iterating
            buckets = {k: list(v) for k, v in _INDEX.items()}

    if built:
        for bucket_rows in buckets.values():
            for i, row_a in enumerate(bucket_rows):
                for row_b in bucket_rows[i + 1:]:
                    pa_id, afn, aln, aby, abs_, abc, _, auid = row_a
                    pb_id, bfn, bln, bby, bbs, bbc, _, buid = row_b
                    _process_pair(
                        pa_id, (afn, aln, aby, abs_, abc), auid,
                        pb_id, (bfn, bln, bby, bbs, bbc), buid,
                    )
        return

    # ── Fallback: full table scan (index not built) ───────────────────────────
    logger.warning('Match index not built — falling back to full table scan')
    persons = Person.query.all()
    for i, pa in enumerate(persons):
        for pb in persons[i + 1:]:
            uid_a = pa.tree.user_id
            uid_b = pb.tree.user_id
            _process_pair(
                pa.id, (pa.first_name, pa.last_name, pa.birth_year, pa.birth_state, pa.birth_country), uid_a,
                pb.id, (pb.first_name, pb.last_name, pb.birth_year, pb.birth_state, pb.birth_country), uid_b,
            )
