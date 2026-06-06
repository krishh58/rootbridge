"""GEDCOM export routes — Heritage tier feature."""
from datetime import datetime, timezone
from functools import wraps
from flask import Blueprint, Response, jsonify, g
from .auth import require_auth
from .db import db
from .models import Person, Tree, User

def require_tier(min_tier):
    TIERS = {'free': 0, 'us': 1, 'heritage': 2}
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            user = User.query.get(g.user_id)
            if not user or TIERS.get(user.tier, 0) < TIERS.get(min_tier, 0):
                return jsonify(error=f'{min_tier} tier required'), 403
            return f(*args, **kwargs)
        return wrapped
    return decorator

ged_export_bp = Blueprint('ged_export', __name__)

CURRENT_YEAR = datetime.now(timezone.utc).year


def _build_ged(persons):
    now = datetime.now(timezone.utc)
    lines = [
        '0 HEAD',
        '1 SOUR RootBridge',
        '2 NAME RootBridge Genealogy Platform',
        '2 VERS 1.0',
        f'1 DATE {now.strftime("%d %b %Y").upper()}',
        f'2 TIME {now.strftime("%H:%M:%S")}',
        '1 GEDC',
        '2 VERS 5.5.1',
        '2 FORM LINEAGE-LINKED',
        '1 CHAR UTF-8',
        '',
    ]

    for p in persons:
        name_parts = ' '.join(filter(None, [p.first_name, p.middle_name]))
        full_name = f'{name_parts} /{p.last_name}/' if p.last_name else name_parts
        lines.append(f'0 @I{p.id}@ INDI')
        lines.append(f'1 NAME {full_name}')
        if p.first_name:
            lines.append(f'2 GIVN {p.first_name}')
        if p.last_name:
            lines.append(f'2 SURN {p.last_name}')
        if p.birth_year:
            lines.append('1 BIRT')
            lines.append(f'2 DATE {p.birth_year}')
            place = ', '.join(filter(None, [p.birth_state, p.birth_country]))
            if place:
                lines.append(f'2 PLAC {place}')
        if p.death_year:
            lines.append('1 DEAT')
            lines.append(f'2 DATE {p.death_year}')
            if p.death_place:
                lines.append(f'2 PLAC {p.death_place}')
        lines.append('')

    lines.append('0 TRLR')
    return '\n'.join(lines)


@ged_export_bp.get('/api/trees/<int:tree_id>/export.ged')
@require_auth
@require_tier('us')
def export_tree_ged(tree_id):
    tree = Tree.query.filter_by(id=tree_id, user_id=g.user_id).first_or_404()
    persons = Person.query.filter_by(tree_id=tree_id).all()
    ged_content = _build_ged(persons)
    slug = tree.name.lower().replace(' ', '_')[:30]
    filename = f'rootbridge_{slug}_{datetime.now(timezone.utc).strftime("%Y%m%d")}.ged'
    return Response(
        ged_content,
        mimetype='text/plain',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@ged_export_bp.get('/api/vault/export.ged')
@require_auth
@require_tier('heritage')
def export_vault_matches_ged():
    """Export all vault persons matched to this user's trees as a GED file."""
    from .models import PersonMatch
    user_tree_ids = [t.id for t in Tree.query.filter_by(user_id=g.user_id).all()]
    if not user_tree_ids:
        return Response('0 HEAD\n1 GEDC\n2 VERS 5.5.1\n0 TRLR\n', mimetype='text/plain')

    matched_ids = db.session.execute(
        db.text('SELECT DISTINCT vault_person_id FROM person_matches WHERE user_tree_id = ANY(:ids)'),
        {'ids': user_tree_ids}
    ).scalars().all()

    persons = Person.query.filter(Person.id.in_(matched_ids)).all() if matched_ids else []
    ged_content = _build_ged(persons)
    filename = f'rootbridge_matches_{datetime.now(timezone.utc).strftime("%Y%m%d")}.ged'
    return Response(
        ged_content,
        mimetype='text/plain',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )
