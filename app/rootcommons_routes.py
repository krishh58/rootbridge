"""
RootCommons — instant search of the pre-lockdown genealogy vault.
"""
from flask import Blueprint, jsonify, request, g
from sqlalchemy import text
from .auth import require_auth
from .db import db
from .match_index import search_vault, index_stats

rootcommons_bp = Blueprint('rootcommons', __name__)


@rootcommons_bp.get('/api/rootcommons/search')
@require_auth
def vault_search():
    """
    Query params:
      q        — full name or just last name (required)
      year     — birth year (optional, integer)
      limit    — max results, default 25, max 50
    Returns JSON: { results: [...], total: N, index_ready: bool }
    """
    q     = (request.args.get('q') or '').strip()
    year  = request.args.get('year', type=int)
    limit = min(request.args.get('limit', 25, type=int), 50)

    if not q:
        return jsonify({'error': 'q is required'}), 400

    # Split "John Smith" → first_name="John", last_name="Smith"
    parts = q.split()
    if len(parts) >= 2:
        first_name = ' '.join(parts[:-1])
        last_name  = parts[-1]
    else:
        first_name = ''
        last_name  = parts[0]

    results = search_vault(last_name, first_name=first_name, birth_year=year, limit=limit)
    stats   = index_stats()

    return jsonify({
        'results':     results,
        'total':       len(results),
        'index_ready': stats['built'],
        'vault_size':  stats['persons'],
    })


@rootcommons_bp.get('/api/rootcommons/person/<int:person_id>')
@require_auth
def vault_person(person_id):
    """Return a single vault person with their edges (parents, children, spouses)."""
    row = db.session.execute(text(
        'SELECT p.id, p.first_name, p.last_name, p.birth_year, p.birth_state, '
        '       p.birth_country, p.death_year, p.death_place, p.middle_name, p.notes '
        'FROM persons p WHERE p.id = :pid'
    ), {'pid': person_id}).fetchone()

    if not row:
        return jsonify({'error': 'Not found'}), 404

    pid, fn, ln, by, bs, bc, dy, dp, mn, notes = row

    # Fetch edges
    edges = db.session.execute(text(
        'SELECT e.related_id, e.rel_type, '
        '       r.first_name, r.last_name, r.birth_year '
        'FROM person_edges e '
        'JOIN persons r ON r.id = e.related_id '
        'WHERE e.person_id = :pid'
    ), {'pid': person_id}).fetchall()

    relations = [
        {
            'id':         rel_id,
            'rel_type':   rel_type,
            'first_name': rfn,
            'last_name':  rln,
            'birth_year': rby,
        }
        for rel_id, rel_type, rfn, rln, rby in edges
    ]

    return jsonify({
        'id':           pid,
        'first_name':   fn,
        'last_name':    ln,
        'middle_name':  mn,
        'birth_year':   by,
        'birth_state':  bs,
        'birth_country': bc,
        'death_year':   dy,
        'death_place':  dp,
        'notes':        notes,
        'relations':    relations,
    })


@rootcommons_bp.get('/api/rootcommons/stats')
@require_auth
def vault_stats():
    stats = index_stats()
    total_persons = db.session.execute(text('SELECT COUNT(*) FROM persons')).scalar()
    return jsonify({
        'vault_size':   stats['persons'],
        'total_persons': total_persons,
        'index_ready':  stats['built'],
        'buckets':      stats['buckets'],
    })
