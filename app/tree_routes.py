from flask import Blueprint, request, jsonify, g
from .auth import require_auth
from .db import db
from .models import Tree, Person, SearchResult, Gap, AlfredMessage

tree_bp = Blueprint('tree', __name__)

def _person_to_dict(p):
    return {
        'id': p.id, 'tree_id': p.tree_id,
        'first_name': p.first_name, 'last_name': p.last_name,
        'birth_year': p.birth_year, 'birth_state': p.birth_state,
        'birth_country': p.birth_country, 'death_year': p.death_year,
        'death_place': p.death_place, 'confidence': p.confidence,
        'parent_ids': p.parent_ids or [], 'spouse_ids': p.spouse_ids or [],
        'notes': p.notes,
    }

def _person_detail(p):
    d = _person_to_dict(p)
    d['search_results'] = [
        {'id': r.id, 'source': r.source, 'record_type': r.record_type,
         'url': r.url, 'raw_data': r.raw_data}
        for r in p.search_results
    ]
    d['gaps'] = [
        {'id': gap.id, 'gap_type': gap.gap_type, 'suggested_source': gap.suggested_source,
         'suggested_query': gap.suggested_query, 'resolved': gap.resolved}
        for gap in p.gaps
    ]
    return d

@tree_bp.get('/api/trees/<int:tree_id>')
@require_auth
def get_tree(tree_id):
    tree = Tree.query.filter_by(id=tree_id, user_id=g.user_id).first_or_404()
    return jsonify({
        'id': tree.id, 'name': tree.name, 'share_token': tree.share_token,
        'persons': [_person_to_dict(p) for p in tree.persons],
    })

@tree_bp.put('/api/trees/<int:tree_id>')
@require_auth
def update_tree(tree_id):
    tree = Tree.query.filter_by(id=tree_id, user_id=g.user_id).first_or_404()
    data = request.get_json() or {}
    if 'name' in data:
        tree.name = data['name']
    db.session.commit()
    return jsonify({'id': tree.id, 'name': tree.name})

@tree_bp.get('/api/persons/<int:person_id>')
@require_auth
def get_person(person_id):
    p = Person.query.join(Tree).filter(
        Person.id == person_id, Tree.user_id == g.user_id
    ).first_or_404()
    return jsonify(_person_detail(p))

@tree_bp.post('/api/persons')
@require_auth
def create_person():
    data = request.get_json() or {}
    tree_name = data.get('tree_name') or f"{data.get('last_name', 'My')} Family"
    tree = Tree.query.filter_by(user_id=g.user_id, name=tree_name).first()
    if not tree:
        tree = Tree(user_id=g.user_id, name=tree_name)
        db.session.add(tree)
    p = Person(
        first_name=data.get('first_name', ''),
        last_name=data.get('last_name', ''),
        birth_year=data.get('birth_year'),
        birth_state=data.get('birth_state', ''),
        birth_country=data.get('birth_country', ''),
        death_year=data.get('death_year'),
        death_place=data.get('death_place', ''),
        parent_ids=data.get('parent_ids', []),
        spouse_ids=data.get('spouse_ids', []),
        confidence=data.get('confidence', 0),
    )
    p.tree = tree
    db.session.add(p)
    db.session.commit()
    return jsonify({'person_id': p.id, 'tree_id': tree.id}), 201

@tree_bp.put('/api/persons/<int:person_id>')
@require_auth
def update_person(person_id):
    p = Person.query.join(Tree).filter(
        Person.id == person_id, Tree.user_id == g.user_id
    ).first_or_404()
    data = request.get_json() or {}
    int_fields = {'birth_year', 'death_year', 'confidence'}
    list_fields = {'parent_ids', 'spouse_ids'}
    str_fields = {'first_name', 'last_name', 'birth_state', 'birth_country', 'death_place', 'notes'}
    for field in int_fields | list_fields | str_fields:
        if field not in data:
            continue
        val = data[field]
        if field in int_fields:
            if val is not None and not isinstance(val, int):
                return jsonify({'error': f'{field} must be an integer'}), 400
        elif field in list_fields:
            if not isinstance(val, list):
                return jsonify({'error': f'{field} must be a list'}), 400
        setattr(p, field, val)
    db.session.commit()
    return jsonify(_person_to_dict(p))

@tree_bp.delete('/api/persons/<int:person_id>')
@require_auth
def delete_person(person_id):
    p = Person.query.join(Tree).filter(
        Person.id == person_id, Tree.user_id == g.user_id
    ).first_or_404()
    db.session.delete(p)
    db.session.commit()
    return '', 204
