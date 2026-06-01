from flask import Blueprint, jsonify, request, g
from .auth import require_auth
from .db import db
from .models import User

gdpr_bp = Blueprint('gdpr', __name__)

@gdpr_bp.get('/api/me/export')
@require_auth
def export_data():
    user = User.query.get(g.user_id)
    trees_data = []
    for tree in user.trees:
        persons_data = []
        for p in tree.persons:
            persons_data.append({
                'first_name': p.first_name, 'last_name': p.last_name,
                'birth_year': p.birth_year, 'birth_state': p.birth_state,
                'birth_country': p.birth_country, 'death_year': p.death_year,
                'death_place': p.death_place, 'confidence': p.confidence,
                'notes': p.notes, 'parent_ids': p.parent_ids,
                'spouse_ids': p.spouse_ids,
                'search_results': [
                    {'source': r.source, 'record_type': r.record_type, 'url': r.url}
                    for r in p.search_results
                ],
                'gaps': [
                    {'gap_type': gap.gap_type, 'suggested_source': gap.suggested_source,
                     'resolved': gap.resolved}
                    for gap in p.gaps
                ],
            })
        trees_data.append({'name': tree.name, 'persons': persons_data})
    return jsonify({
        'account': {
            'email': user.email,
            'tier': user.tier,
            'created_at': user.created_at.isoformat() if user.created_at else None,
        },
        'trees': trees_data,
    })

@gdpr_bp.delete('/api/me')
@require_auth
def delete_account():
    data = request.get_json() or {}
    if data.get('confirm') != 'DELETE':
        return jsonify({'error': 'Send {"confirm": "DELETE"} to confirm account deletion'}), 400
    user = User.query.get(g.user_id)
    db.session.delete(user)
    db.session.commit()
    response = jsonify({'message': 'Account and all data deleted.'})
    response.delete_cookie('auth_token')
    return response
