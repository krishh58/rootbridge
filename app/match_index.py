"""
Obsidian-style match index for fast ancestor matching.

Hot path: in-memory dict keyed by (soundex_key, birth_decade) → list of row tuples.
Cold path: DB query when index not built or bucket missing (free-tier fallback).

Row tuple layout: (id, first_name, last_name, birth_year, birth_state, birth_country, tree_id, user_id)
"""
import logging
import threading
from collections import defaultdict
from sqlalchemy import text

logger = logging.getLogger(__name__)

# ── In-memory index ──────────────────────────────────────────────────────────

_INDEX: dict = {}          # (soundex_key, birth_decade) → list[tuple]
_LOCK  = threading.Lock()
_BUILT = False

MAX_INDEX_ROWS = 800_000   # ~200MB at ~250 bytes/row (Python tuple overhead)

# ── Soundex ──────────────────────────────────────────────────────────────────

_DIGIT = {
    'B':'1','F':'1','P':'1','V':'1',
    'C':'2','G':'2','J':'2','K':'2','Q':'2','S':'2','X':'2','Z':'2',
    'D':'3','T':'3',
    'L':'4',
    'M':'5','N':'5',
    'R':'6',
}

def soundex(name: str) -> str:
    """American Soundex — 4-char code, e.g. 'Smith' → 'S530'."""
    name = (name or '').upper()
    name = ''.join(c for c in name if c.isalpha())
    if not name:
        return ''
    result = [name[0]]
    prev = _DIGIT.get(name[0], '0')
    for ch in name[1:]:
        d = _DIGIT.get(ch, '0')
        if d != '0' and d != prev:
            result.append(d)
            if len(result) == 4:
                break
        # H, W, vowels don't reset the previous consonant code
        if d != '0':
            prev = d
    return ''.join(result).ljust(4, '0')[:4]


def _decade(birth_year) -> int | None:
    if birth_year is None:
        return None
    return (int(birth_year) // 10) * 10


# ── Schema migration (adds columns if missing) ────────────────────────────────

def _ensure_columns(engine) -> None:
    """Add soundex_key and birth_decade columns to persons if they don't exist."""
    with engine.connect() as conn:
        for col, typedef in [('soundex_key', 'VARCHAR(4)'), ('birth_decade', 'INTEGER')]:
            try:
                conn.execute(text(f'ALTER TABLE persons ADD COLUMN {col} {typedef}'))
                conn.commit()
                logger.info('Added column persons.%s', col)
            except Exception:
                pass  # already exists


def _populate_columns(engine) -> int:
    """Compute and store soundex_key + birth_decade for rows where they're NULL.
    Runs in small batches so the table lock is released between commits."""
    import time
    BATCH = 5000
    total = 0
    while True:
        with engine.connect() as conn:
            rows = conn.execute(
                text('SELECT id, last_name, birth_year FROM persons WHERE soundex_key IS NULL LIMIT :n'),
                {'n': BATCH},
            ).fetchall()
            if not rows:
                break
            updates = []
            for row_id, last_name, birth_year in rows:
                sk = soundex(last_name)
                bd = _decade(birth_year)
                if sk:
                    updates.append({'id': row_id, 'sk': sk, 'bd': bd})
            if updates:
                conn.execute(
                    text('UPDATE persons SET soundex_key = :sk, birth_decade = :bd WHERE id = :id'),
                    updates,
                )
                conn.commit()
            total += len(rows)
            logger.info('Populated soundex/decade: %d done so far', total)
        time.sleep(0.1)  # yield between batches so web requests can get DB connections
    return total


# ── Index build ───────────────────────────────────────────────────────────────

def build_hot_index(app) -> int:
    """
    Load persons into in-memory index. Safe to call at startup inside app context.
    Returns number of rows indexed.
    """
    global _BUILT
    with app.app_context():
        from .db import db
        engine = db.engine
        _ensure_columns(engine)
        _populate_columns(engine)

        with engine.connect() as conn:
            total = conn.execute(
                text('SELECT COUNT(*) FROM persons WHERE soundex_key IS NOT NULL AND birth_decade IS NOT NULL')
            ).scalar()

        if total > MAX_INDEX_ROWS:
            logger.warning(
                'persons table has %d rows — exceeds MAX_INDEX_ROWS (%d). '
                'Index not built; falling back to DB queries.',
                total, MAX_INDEX_ROWS,
            )
            return 0

        with engine.connect() as conn:
            rows = conn.execute(text(
                'SELECT p.id, p.first_name, p.last_name, p.birth_year, '
                '       p.birth_state, p.birth_country, p.tree_id, t.user_id '
                'FROM persons p '
                'JOIN trees t ON t.id = p.tree_id '
                'WHERE p.soundex_key IS NOT NULL AND p.birth_decade IS NOT NULL'
            )).fetchall()

        new_index: dict = defaultdict(list)
        for row in rows:
            pid, fn, ln, by, bs, bc, tree_id, user_id = row
            sk = soundex(ln)
            bd = _decade(by)
            if sk and bd is not None:
                new_index[(sk, bd)].append((pid, fn, ln, by, bs, bc, tree_id, user_id))

        with _LOCK:
            _INDEX.clear()
            _INDEX.update(new_index)
            _BUILT = True

        logger.info('Built match index: %d buckets, %d persons', len(_INDEX), len(rows))
        return len(rows)


# ── Lookup ────────────────────────────────────────────────────────────────────

def lookup_candidates(last_name: str, birth_year: int, birth_country: str = None) -> list:
    """
    Return candidate row tuples for a given surname + birth year (±5 year window).
    Hits in-memory index first; falls back to DB if index not built.

    Row tuple: (id, first_name, last_name, birth_year, birth_state, birth_country, tree_id, user_id)
    """
    sk = soundex(last_name)
    if not sk or birth_year is None:
        return []

    bd = _decade(birth_year)

    with _LOCK:
        if _BUILT:
            candidates = list(_INDEX.get((sk, bd), []))
            # ±5 year window can span an adjacent decade
            if birth_year % 10 <= 4:
                candidates += _INDEX.get((sk, bd - 10), [])
            else:
                candidates += _INDEX.get((sk, bd + 10), [])
            return candidates

    # ── DB fallback ────────────────────────────────────────────────────────
    from .db import db
    try:
        rows = db.session.execute(text(
            'SELECT p.id, p.first_name, p.last_name, p.birth_year, '
            '       p.birth_state, p.birth_country, p.tree_id, t.user_id '
            'FROM persons p '
            'JOIN trees t ON t.id = p.tree_id '
            'WHERE p.soundex_key = :sk '
            '  AND p.birth_year BETWEEN :lo AND :hi'
        ), {'sk': sk, 'lo': birth_year - 5, 'hi': birth_year + 5}).fetchall()
        return list(rows)
    except Exception as exc:
        logger.error('DB fallback lookup failed: %s', exc)
        return []


def update_person_in_index(person_id: int, last_name: str, birth_year,
                            first_name: str, birth_state: str,
                            birth_country: str, tree_id: int, user_id: int) -> None:
    """Update or insert a single person in the hot index (call after save/edit)."""
    sk = soundex(last_name)
    bd = _decade(birth_year)
    if not sk or bd is None:
        return
    row = (person_id, first_name, last_name, birth_year, birth_state, birth_country, tree_id, user_id)
    with _LOCK:
        if not _BUILT:
            return
        bucket = _INDEX.setdefault((sk, bd), [])
        # remove stale entry for this person_id if present
        _INDEX[(sk, bd)] = [r for r in bucket if r[0] != person_id]
        _INDEX[(sk, bd)].append(row)


def remove_person_from_index(person_id: int, last_name: str, birth_year) -> None:
    """Remove a person from the hot index (call after delete)."""
    sk = soundex(last_name)
    bd = _decade(birth_year)
    if not sk or bd is None:
        return
    with _LOCK:
        if (sk, bd) in _INDEX:
            _INDEX[(sk, bd)] = [r for r in _INDEX[(sk, bd)] if r[0] != person_id]


def search_vault(last_name: str, first_name: str = '',
                 birth_year: int = None, limit: int = 25) -> list[dict]:
    """
    Full-text search against the RootCommons vault.
    Returns up to `limit` results sorted by relevance score (descending).

    Scoring:
      - Last-name soundex match required (filters the bucket)
      - Exact last-name match:        +30
      - First-name match (lev ≤ 2):  +25
      - Exact first-name match:       +10 bonus
      - Birth year within  0 yr:      +20
      - Birth year within  5 yr:      +15
      - Birth year within 10 yr:       +8
      - Birth year within 20 yr:       +3

    Row tuple: (id, first_name, last_name, birth_year, birth_state, birth_country, tree_id, user_id)
    """
    sk = soundex(last_name)
    if not sk:
        return []

    ln_query = (last_name  or '').strip().lower()
    fn_query = (first_name or '').strip().lower()

    # Collect candidate buckets: cover ±20 yr range = up to 5 decades
    decade_keys = set()
    if birth_year is not None:
        for offset in range(-20, 25, 10):
            decade_keys.add((sk, _decade(birth_year + offset)))
    else:
        # no year — grab all buckets for this soundex
        with _LOCK:
            if _BUILT:
                for (bsk, _), _ in _INDEX.items():
                    if bsk == sk:
                        decade_keys.add((sk, _))
            else:
                decade_keys = None  # signal DB fallback

    # Gather raw candidates
    candidates: list = []
    with _LOCK:
        if _BUILT:
            for key in (decade_keys or []):
                candidates.extend(_INDEX.get(key, []))
        else:
            candidates = None

    if candidates is None:
        # DB fallback
        from .db import db
        try:
            q = (
                'SELECT p.id, p.first_name, p.last_name, p.birth_year, '
                '       p.birth_state, p.birth_country, p.tree_id, t.user_id '
                'FROM persons p JOIN trees t ON t.id = p.tree_id '
                'WHERE p.soundex_key = :sk'
            )
            params = {'sk': sk}
            if birth_year is not None:
                q += ' AND p.birth_year BETWEEN :lo AND :hi'
                params.update({'lo': birth_year - 20, 'hi': birth_year + 20})
            from sqlalchemy import text
            candidates = list(db.session.execute(text(q), params).fetchall())
        except Exception as exc:
            logger.error('search_vault DB fallback failed: %s', exc)
            return []

    # Score and filter
    scored = []
    seen_ids = set()
    for row in candidates:
        pid, fn, ln, by, bs, bc, tree_id, user_id = row
        if pid in seen_ids:
            continue
        seen_ids.add(pid)

        score = 0

        # Last-name scoring
        ln_row = (ln or '').strip().lower()
        if ln_row == ln_query:
            score += 30
        elif ln_row and ln_query and _levenshtein(ln_row, ln_query) <= 1:
            score += 20
        else:
            continue  # soundex matched but name too different — skip

        # First-name scoring
        if fn_query:
            fn_row = (fn or '').strip().lower()
            dist = _levenshtein(fn_row, fn_query) if fn_row else 99
            if dist == 0:
                score += 35
            elif dist <= 1:
                score += 25
            elif dist <= 2:
                score += 15
            # If first name provided but completely mismatched, still include (lower score)

        # Birth-year scoring
        if birth_year is not None and by is not None:
            diff = abs(int(by) - int(birth_year))
            if diff == 0:
                score += 20
            elif diff <= 5:
                score += 15
            elif diff <= 10:
                score += 8
            elif diff <= 20:
                score += 3

        scored.append({
            'id':           pid,
            'first_name':   fn,
            'last_name':    ln,
            'birth_year':   by,
            'birth_state':  bs,
            'birth_country': bc,
            'score':        score,
        })

    scored.sort(key=lambda x: -x['score'])
    return scored[:limit]


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


def index_stats() -> dict:
    with _LOCK:
        buckets = len(_INDEX)
        total   = sum(len(v) for v in _INDEX.values())
    return {'built': _BUILT, 'buckets': buckets, 'persons': total}
