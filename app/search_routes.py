from flask import Blueprint, request, jsonify, g
from .auth import require_auth
from .db import db, get_redis
from .models import User, Tree, Person, SearchResult, Gap
from .search_cascade import run_us_cascade
from .ai_synthesis import synthesize_gaps

search_bp = Blueprint('search', __name__)
GUEST_RATE_LIMIT = 5


def _check_guest_rate_limit(ip: str) -> bool:
    try:
        r = get_redis()
        key = f'guest_rate:{ip}'
        count = r.get(key)
        if count and int(count) >= GUEST_RATE_LIMIT:
            return False
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, 86400)
        pipe.execute()
        return True
    except Exception:
        # Redis unavailable — allow the request rather than blocking guests
        return True


def _save_search_to_db(user_id: int, tree_name: str, first: str, last: str,
                       birth_year: int, birth_place: str, cascade: dict,
                       summary: str) -> dict:
    tree = Tree.query.filter_by(user_id=user_id, name=tree_name).first()
    if not tree:
        tree = Tree(user_id=user_id, name=tree_name or f'{first} {last} Family')
        db.session.add(tree)
        db.session.flush()

    person = Person(
        tree_id=tree.id, first_name=first, last_name=last,
        birth_year=birth_year, birth_state=birth_place,
        confidence=cascade['confidence'],
    )
    db.session.add(person)
    db.session.flush()

    for r in cascade['results']:
        db.session.add(SearchResult(
            person_id=person.id, source=r.get('source', ''),
            record_type=r.get('record_type', ''), url=r.get('url', ''),
            raw_data=r,
        ))

    for gap in cascade['gaps']:
        db.session.add(Gap(
            person_id=person.id, gap_type=gap['gap_type'],
            suggested_source=gap.get('suggested_source', ''),
            suggested_query=gap.get('suggested_query', ''),
        ))

    db.session.commit()
    return {'person_id': person.id, 'tree_id': tree.id}


@search_bp.post('/search')
def guest_search():
    data = request.get_json() or {}
    if not data.get('last'):
        return jsonify({'error': 'Last name is required'}), 400
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if not _check_guest_rate_limit(ip):
        return jsonify({'error': 'Daily search limit reached. Create a free account to continue.'}), 429
    first = data.get('first', '')
    last = data['last']
    birth_year = data.get('birth_year')
    birth_place = data.get('birth_place', '')
    cascade = run_us_cascade(first=first, last=last, birth_year=birth_year, birth_place=birth_place)
    synthesis = synthesize_gaps(
        person={'first_name': first, 'last_name': last, 'birth_year': birth_year, 'birth_state': birth_place},
        results=cascade['results'], gaps=cascade['gaps']
    )
    return jsonify({**cascade, 'summary': synthesis['summary']})


@search_bp.post('/api/search')
@require_auth
def authenticated_search():
    data = request.get_json() or {}
    if not data.get('last'):
        return jsonify({'error': 'Last name is required'}), 400
    first = data.get('first', '')
    last = data['last']
    birth_year = data.get('birth_year')
    birth_place = data.get('birth_place', '')
    tree_name = data.get('tree_name', '')
    cascade = run_us_cascade(first=first, last=last, birth_year=birth_year, birth_place=birth_place)
    synthesis = synthesize_gaps(
        person={'first_name': first, 'last_name': last, 'birth_year': birth_year, 'birth_state': birth_place},
        results=cascade['results'], gaps=cascade['gaps']
    )
    ids = _save_search_to_db(g.user_id, tree_name, first, last, birth_year, birth_place, cascade, synthesis['summary'])
    return jsonify({**cascade, 'summary': synthesis['summary'], **ids})
