"""
ancestry_chain.py — Auto-tree builder

Given a starting Person, Alfred:
  1. Scans their search results for family name mentions
  2. Extracts parents, spouse, and other relatives
  3. Creates Person nodes for each and links them via PersonEdge
  4. Queues each new person for their own search cascade
  5. Repeats up to MAX_DEPTH generations back

SSE endpoint streams progress back to the UI in real time.
"""

import re
import json
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MAX_DEPTH = 4          # how many generations back to chase
MAX_NODES = 40         # safety cap — stop before runaway tree growth
SEARCH_DELAY = 1.2     # seconds between cascade calls (be polite to external APIs)

# ---------------------------------------------------------------------------
# Name extraction from obituary / result text
# ---------------------------------------------------------------------------

# Patterns that reveal family relationships in obituary text
# Each pattern yields one or two captured name strings
_PARENT_PATTERNS = [
    # "son/daughter of Donald King and Dorothy Hunter King"
    # "born to Donald King and Dorothy (Hunter) King"
    # "in Ponchatoula to Leslie and Lauranie Henderson Anderson"
    r'(?:son|daughter)\s+of\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)(?:\s+and\s+([A-Z][a-z]+(?:\s+(?:nee\s+)?[A-Z][a-z]+)*\s+[A-Z][a-z]+))?',
    r'born\s+(?:\w+\s+)*?to\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)(?:\s+and\s+([A-Z][a-z]+(?:\s+(?:\([^)]+\)\s+)?)[A-Z][a-z]+))?',
    # "parents: Donald King, Dorothy King"
    r'parents?:\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)(?:,\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+))?',
    # "father, Donald King" or "mother, Dorothy King"
    r'(?:his|her)\s+father,?\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)',
    r'(?:his|her)\s+mother,?\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)',
]


def _extract_parents_shared_surname(text: str) -> list[tuple[str, str]]:
    """
    Handle patterns where two parents share one last name:
      "parents, Donald and Dorothy King"
      "daughter of Edward and Pauline Donavan"
      "born to William and Helen Crawford"
      "son of Charles and Mary Ellen Smith"
    Returns [(first1,last),(first2,last)].
    """
    parents = []
    seen = set()

    # All trigger phrases that introduce two-parent shared-surname pattern
    triggers = r'(?:parents?,|(?:son|daughter)\s+of|born\s+(?:\w+\s+)*?to)'
    pattern = re.compile(
        triggers +
        r'\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)'   # first parent (may have middle name)
        r'\s+and\s+'
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)'        # second parent (may have middle name)
        r'(?:\s+\([^)]+\))?'                        # optional (nee X)
        r'\s+([A-Z][a-z]+)',                        # shared last name
        re.IGNORECASE
    )
    for m in pattern.finditer(text):
        raw1, raw2, last = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        if last.lower() in _NAME_STOPWORDS or len(last) < 2:
            continue
        # If either parent already has 2+ tokens they have their own last names
        # e.g. "daughter of Ernest Anderson and Mary Johnson" — skip, let _PARENT_PATTERNS handle
        if len(raw1.split()) >= 2 or len(raw2.split()) >= 2:
            continue
        # Reject common prepositions / stop words as "last name"
        if last.lower() in {'of', 'in', 'at', 'the', 'and', 'or', 'home', 'city',
                             'kansas', 'kentucky', 'louisiana', 'texas', 'ohio',
                             'florida', 'georgia', 'california', 'virginia'}:
            continue
        # raw1/raw2 may be "Mary Ellen" — take just the first token as first name
        f1 = raw1.split()[0]
        f2 = raw2.split()[0]
        for fn in (f1, f2):
            key = f'{fn.lower()}_{last.lower()}'
            if key not in seen and fn[0].isupper():
                seen.add(key)
                parents.append((fn, last))

    return parents

_SPOUSE_PATTERNS = [
    # "his loving wife of 61 years, Sandra J. King (nee Mautz)"
    r'(?:his|her)\s+(?:loving\s+)?(?:wife|husband)(?:\s+of\s+\d+\s+years?)?,?\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)',
    # "survived by his wife, Sandra"
    r'survived\s+by\s+(?:his|her)\s+wife,?\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)',
    r'survived\s+by\s+(?:his|her)\s+husband,?\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)',
    # "married to Sandra Mautz"
    r'married\s+to\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)',
]

# Words that look like names but aren't
_NAME_STOPWORDS = {
    'the', 'and', 'with', 'his', 'her', 'their', 'our', 'was', 'born',
    'died', 'passed', 'away', 'also', 'survived', 'preceded', 'death',
    'funeral', 'memorial', 'service', 'cemetery', 'church', 'loving',
    'devoted', 'beloved', 'dear', 'years', 'home', 'hospital', 'family',
    'obituary', 'burial', 'visitation', 'interment', 'graveside',
    'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday',
    'january', 'february', 'march', 'april', 'may', 'june', 'july',
    'august', 'september', 'october', 'november', 'december',
}


def _clean_name(raw: str) -> tuple[str, str] | None:
    """
    Split a raw name string into (first, last).
    Returns None if it looks like a non-name.
    """
    if not raw:
        return None
    raw = raw.strip().strip(',').strip()
    # Strip maiden name in parens: "Dorothy (Hunter) King" → "Dorothy King"
    raw = re.sub(r'\s*\([^)]+\)', '', raw).strip()
    # Strip "nee X" suffix
    raw = re.sub(r'\s+nee\s+\w+', '', raw, flags=re.I).strip()
    parts = raw.split()
    if len(parts) < 2:
        return None
    # Skip middle initials — take first and last
    first = parts[0]
    last  = parts[-1]
    if first.lower() in _NAME_STOPWORDS or last.lower() in _NAME_STOPWORDS:
        return None
    if len(first) < 2 or len(last) < 2:
        return None
    # Must start with capital
    if not first[0].isupper() or not last[0].isupper():
        return None
    return first, last


def extract_family_from_text(text: str) -> dict:
    """
    Parse obituary/result text for family member names.
    Returns {'parents': [(first,last), ...], 'spouses': [(first,last), ...]}
    """
    parents = []
    spouses = []
    seen    = set()

    def _add(lst, name_tuple):
        key = f'{name_tuple[0].lower()}_{name_tuple[1].lower()}'
        if key not in seen:
            seen.add(key)
            lst.append(name_tuple)

    # Shared-surname pattern first: "parents, Donald and Dorothy King"
    for name in _extract_parents_shared_surname(text):
        _add(parents, name)

    for pat in _PARENT_PATTERNS:
        for match in re.finditer(pat, text, re.IGNORECASE):
            for grp in match.groups():
                if grp:
                    cleaned = _clean_name(grp)
                    if cleaned:
                        _add(parents, cleaned)

    for pat in _SPOUSE_PATTERNS:
        for match in re.finditer(pat, text, re.IGNORECASE):
            for grp in match.groups():
                if grp:
                    cleaned = _clean_name(grp)
                    if cleaned:
                        _add(spouses, cleaned)

    return {'parents': parents[:2], 'spouses': spouses[:1]}


def extract_family_from_results(results: list) -> dict:
    """
    Scan all search results for a person and extract family members.
    Prefers full_text if available, falls back to snippet/title.
    """
    all_text = []
    for r in results:
        if r.get('full_text'):
            all_text.append(r['full_text'])
        elif r.get('snippet'):
            all_text.append(r['snippet'])
        if r.get('title'):
            all_text.append(r['title'])
    combined = ' '.join(all_text)
    return extract_family_from_text(combined)


# ---------------------------------------------------------------------------
# Birth year estimation
# ---------------------------------------------------------------------------

def _estimate_birth_year(generation_depth: int, anchor_birth_year: int | None) -> tuple[int | None, int | None]:
    """
    Estimate birth year range for an ancestor at a given depth.
    generation_depth: 1=target, 2=parents, 3=grandparents, etc.
    """
    if not anchor_birth_year:
        return None, None
    # Average ~27 years per generation
    estimated = anchor_birth_year - (27 * (generation_depth - 1))
    return estimated - 10, estimated + 10


# ---------------------------------------------------------------------------
# Core chaining engine
# ---------------------------------------------------------------------------

def _get_or_create_person(tree_id: int, first: str, last: str,
                           birth_year: int | None, birth_place: str,
                           death_year: int | None, relationship: str,
                           related_person_id: int, db, Person, PersonEdge) -> tuple:
    """
    Find an existing Person in this tree or create a new one.
    Wire up the PersonEdge relationship.
    Returns (person, created: bool).
    """
    from jellyfish import soundex as _sdx
    try:
        sdx = _sdx(last)
    except Exception:
        sdx = last[:4].upper()

    # Check for existing match in tree
    existing = Person.query.filter_by(
        tree_id=tree_id,
        first_name=first,
        last_name=last,
    ).first()

    if not existing and birth_year:
        existing = Person.query.filter_by(
            tree_id=tree_id,
            last_name=last,
            birth_year=birth_year,
        ).filter(
            Person.first_name.ilike(f'{first[:3]}%')
        ).first()

    if existing:
        # Wire edge if not already there
        _wire_edge(existing.id, related_person_id, relationship, db, PersonEdge)
        return existing, False

    # Create new stub person
    decade = (birth_year // 10 * 10) if birth_year else None
    person = Person(
        tree_id     = tree_id,
        first_name  = first,
        last_name   = last,
        birth_year  = birth_year,
        birth_state = birth_place,
        death_year  = death_year,
        soundex_key = sdx,
        birth_decade= decade,
        confidence  = 0,
        notes       = f'Auto-discovered via ancestry chain from person #{related_person_id}',
    )
    db.session.add(person)
    db.session.flush()  # get the ID without committing

    _wire_edge(person.id, related_person_id, relationship, db, PersonEdge)
    return person, True


def _wire_edge(person_id: int, related_id: int, rel_type: str, db, PersonEdge):
    """Create a PersonEdge if it doesn't already exist."""
    exists = PersonEdge.query.filter_by(
        person_id=person_id, related_id=related_id, rel_type=rel_type
    ).first()
    if not exists:
        db.session.add(PersonEdge(
            person_id=person_id,
            related_id=related_id,
            rel_type=rel_type,
        ))


# ---------------------------------------------------------------------------
# SSE streaming auto-build
# ---------------------------------------------------------------------------

def auto_build_tree_stream(start_person_id: int, tree_id: int, user_id: int,
                            app, db, Person, PersonEdge, Tree):
    """
    Generator that yields SSE strings while building the ancestry tree.
    Designed to be wrapped in a Flask Response(stream_with_context(...)).

    Charges 20 tokens per generation searched beyond the starting person.
    """
    from .search_cascade import run_us_cascade_stream
    from .models import User, SearchResult

    def _emit(event: dict) -> str:
        return 'data: ' + json.dumps(event) + '\n\n'

    with app.app_context():
        # ── Validate ────────────────────────────────────────────────────────
        start = Person.query.get(start_person_id)
        if not start or start.tree_id != tree_id:
            yield _emit({'error': 'Person not found', 'done': True})
            return

        user = User.query.get(user_id)
        if not user:
            yield _emit({'error': 'User not found', 'done': True})
            return

        yield _emit({
            'status': 'starting',
            'message': f'Starting ancestry chain from {start.first_name} {start.last_name}…',
            'person_id': start_person_id,
        })

        # ── Queue: (person_id, generation_depth, birth_year_anchor) ────────
        queue   = [(start_person_id, 1, start.birth_year)]
        visited = {start_person_id}
        nodes_created = 0
        total_searched = 0

        while queue and nodes_created < MAX_NODES:
            person_id, depth, anchor_year = queue.pop(0)

            if depth > MAX_DEPTH:
                continue

            person = Person.query.get(person_id)
            if not person:
                continue

            yield _emit({
                'status': 'searching',
                'generation': depth,
                'person': f'{person.first_name} {person.last_name}',
                'person_id': person_id,
                'message': f'Generation {depth}: searching {person.first_name} {person.last_name}…',
            })

            # ── Token check for generations beyond the first ─────────────
            if depth > 1 and user.tier != 'admin':
                if user.total_tokens() < 20:
                    yield _emit({
                        'status': 'token_gate',
                        'message': 'Out of tokens — ancestry chain paused. Top up to continue.',
                        'done': True,
                    })
                    return
                if not user.deduct_tokens(20):
                    yield _emit({'status': 'token_gate', 'done': True})
                    return
                db.session.commit()

            # ── Run search cascade for this person ───────────────────────
            birth_place = person.birth_state or ''
            death_place = person.death_place or ''
            birth_year  = person.birth_year
            death_year  = person.death_year

            all_results = []
            try:
                for event_str in run_us_cascade_stream(
                    first       = person.first_name or '',
                    last        = person.last_name  or '',
                    birth_year  = birth_year,
                    birth_place = birth_place,
                    death_year  = death_year,
                    death_place = death_place,
                    skip_vault  = False,
                ):
                    # Forward search events to the client
                    yield event_str
                    try:
                        ev = json.loads(event_str.replace('data: ', '', 1).strip())
                        if ev.get('results'):
                            all_results.extend(ev['results'])
                    except Exception:
                        pass
            except Exception as e:
                logger.warning('Chain search error for person %s: %s', person_id, e)

            total_searched += 1

            # ── Save results to person ───────────────────────────────────
            for r in all_results[:10]:
                db.session.add(SearchResult(
                    person_id   = person_id,
                    source      = r.get('source', ''),
                    record_type = r.get('record_type', ''),
                    url         = (r.get('url') or '')[:500],
                    raw_data    = r,
                ))

            # ── Extract family from results ───────────────────────────────
            family = extract_family_from_results(all_results)
            parents_found = []
            spouses_found = []

            # Estimate birth years for parents
            est_min, est_max = _estimate_birth_year(depth + 1, anchor_year or birth_year)
            est_year = ((est_min or 0) + (est_max or 0)) // 2 or None

            # Create parent nodes
            for fname, lname in family['parents']:
                parent, created = _get_or_create_person(
                    tree_id          = tree_id,
                    first            = fname,
                    last             = lname,
                    birth_year       = est_year,
                    birth_place      = birth_place,
                    death_year       = None,
                    relationship     = 'parent',
                    related_person_id= person_id,
                    db               = db,
                    Person           = Person,
                    PersonEdge       = PersonEdge,
                )
                parents_found.append({
                    'name': f'{fname} {lname}',
                    'person_id': parent.id,
                    'created': created,
                })
                if created:
                    nodes_created += 1
                if parent.id not in visited:
                    visited.add(parent.id)
                    queue.append((parent.id, depth + 1, est_year))

            # Create spouse node (generation stays same — not an ancestor)
            for fname, lname in family['spouses']:
                spouse, created = _get_or_create_person(
                    tree_id          = tree_id,
                    first            = fname,
                    last             = lname,
                    birth_year       = birth_year,  # roughly same generation
                    birth_place      = birth_place,
                    death_year       = None,
                    relationship     = 'spouse',
                    related_person_id= person_id,
                    db               = db,
                    Person           = Person,
                    PersonEdge       = PersonEdge,
                )
                spouses_found.append({
                    'name': f'{fname} {lname}',
                    'person_id': spouse.id,
                    'created': created,
                })
                if created:
                    nodes_created += 1
                # Don't chase spouse's ancestry — only direct ancestors

            db.session.commit()

            yield _emit({
                'status':        'person_done',
                'person_id':     person_id,
                'person':        f'{person.first_name} {person.last_name}',
                'generation':    depth,
                'parents_found': parents_found,
                'spouses_found': spouses_found,
                'results_count': len(all_results),
                'nodes_created': nodes_created,
                'queue_depth':   len(queue),
            })

            # Brief pause between cascade calls
            if queue:
                time.sleep(SEARCH_DELAY)

        # ── Done ─────────────────────────────────────────────────────────
        db.session.commit()
        yield _emit({
            'status':         'done',
            'done':           True,
            'total_searched': total_searched,
            'nodes_created':  nodes_created,
            'max_generation': MAX_DEPTH,
            'message':        f'Ancestry chain complete — {nodes_created} new ancestors found across {total_searched} searches.',
        })
