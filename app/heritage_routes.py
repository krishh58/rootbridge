from functools import wraps
from flask import Blueprint, request, jsonify, g
from .auth import require_auth
from .token_middleware import require_tokens
from .db import db
from .models import User, Person, Tree, SearchResult, Gap
from .heritage_cascade import run_european_cascade, run_aa_cascade
from .ai_synthesis import synthesize_gaps

heritage_bp = Blueprint('heritage', __name__)

EUROPEAN_TIERS = {'european', 'all'}
AA_TIERS = {'aa', 'all'}


def _require_tier(allowed_tiers: set):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user = User.query.get(g.user_id)
            if not user or user.tier not in allowed_tiers:
                return jsonify({
                    'error': 'This heritage cascade requires an upgraded subscription.',
                    'required_tiers': list(allowed_tiers),
                    'current_tier': user.tier if user else 'unknown',
                    'upgrade_url': '/pricing',
                }), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator


def _save_heritage_results(person_id: int, cascade: dict):
    for r in cascade.get('results', []):
        if r.get('url') and not SearchResult.query.filter_by(
            person_id=person_id, url=r['url']
        ).first():
            db.session.add(SearchResult(
                person_id=person_id,
                source=r.get('source', ''),
                record_type=r.get('record_type', ''),
                url=r['url'],
                raw_data=r,
            ))
    for gap in cascade.get('gaps', []):
        if not Gap.query.filter_by(person_id=person_id, gap_type=gap['gap_type']).first():
            db.session.add(Gap(
                person_id=person_id,
                gap_type=gap['gap_type'],
                suggested_source=gap.get('suggested_source', ''),
                suggested_query=gap.get('suggested_query', ''),
            ))
    person = Person.query.get(person_id)
    if person and cascade.get('confidence', 0) > (person.confidence or 0):
        person.confidence = cascade['confidence']
    db.session.commit()


@heritage_bp.post('/api/heritage/<int:person_id>/european')
@require_auth
@_require_tier(EUROPEAN_TIERS)
@require_tokens(40)
def european_cascade(person_id):
    person = Person.query.join(Tree).filter(
        Person.id == person_id, Tree.user_id == g.user_id
    ).first_or_404()
    data = request.get_json() or {}
    cascade = run_european_cascade(
        first=person.first_name or '',
        last=person.last_name or '',
        birth_year=person.birth_year,
        birth_place=person.birth_state or '',
        origin_country=data.get('origin_country', ''),
    )
    _save_heritage_results(person_id, cascade)
    synthesis = synthesize_gaps(
        person={'first_name': person.first_name, 'last_name': person.last_name,
                'birth_year': person.birth_year, 'birth_state': person.birth_state},
        results=cascade['results'],
        gaps=cascade['gaps'],
    )
    return jsonify({**cascade, 'summary': synthesis['summary']})


@heritage_bp.post('/api/heritage/<int:person_id>/aa')
@require_auth
@_require_tier(AA_TIERS)
@require_tokens(40)
def aa_cascade(person_id):
    person = Person.query.join(Tree).filter(
        Person.id == person_id, Tree.user_id == g.user_id
    ).first_or_404()
    cascade = run_aa_cascade(
        first=person.first_name or '',
        last=person.last_name or '',
        birth_year=person.birth_year,
        birth_place=person.birth_state or '',
    )
    _save_heritage_results(person_id, cascade)
    synthesis = synthesize_gaps(
        person={'first_name': person.first_name, 'last_name': person.last_name,
                'birth_year': person.birth_year, 'birth_state': person.birth_state},
        results=cascade['results'],
        gaps=cascade['gaps'],
    )
    return jsonify({**cascade, 'summary': synthesis['summary']})
