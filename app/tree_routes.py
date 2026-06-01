import secrets
import json as _json
from flask import Blueprint, request, jsonify, g, make_response, render_template_string
from .auth import require_auth
from .db import db
from .models import Tree, Person, SearchResult, Gap, AlfredMessage
from .hometown import get_hometown_photo, get_historical_map, get_life_context

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

@tree_bp.get('/api/persons/<int:person_id>/hometown')
@require_auth
def get_hometown(person_id):
    p = Person.query.join(Tree).filter(
        Person.id == person_id, Tree.user_id == g.user_id
    ).first_or_404()
    place = p.birth_state or p.birth_country or ''
    if not place:
        return jsonify({'available': False})
    name = f'{p.first_name or ""} {p.last_name or ""}'.strip()
    photo = get_hometown_photo(place, p.birth_year)
    historical_map = get_historical_map(place, p.birth_year)
    life_context = get_life_context(name, place, p.birth_year, p.death_year)
    return jsonify({
        'available': True,
        'place': place,
        'photo': photo,
        'map': historical_map,
        'life_context': life_context,
    })

@tree_bp.post('/api/trees/<int:tree_id>/share')
@require_auth
def generate_share_link(tree_id):
    tree = Tree.query.filter_by(id=tree_id, user_id=g.user_id).first_or_404()
    if not tree.share_token:
        tree.share_token = secrets.token_urlsafe(32)
        db.session.commit()
    return jsonify({
        'share_token': tree.share_token,
        'share_url': f'/shared/{tree.share_token}',
    })

@tree_bp.get('/shared/<share_token>')
def public_tree_view(share_token):
    tree = Tree.query.filter_by(share_token=share_token).first_or_404()
    persons = [_person_to_dict(p) for p in tree.persons]
    return render_template_string("""<!DOCTYPE html>
<html><head><title>{{ tree_name }} — RootBridge</title>
<link rel="stylesheet" href="/static/style.css">
<script src="https://d3js.org/d3.v7.min.js"></script>
</head><body>
<nav class="nav"><a href="/" class="nav-brand">RootBridge</a>
<span style="color:#94a3b8;margin-left:1rem">Shared Tree: {{ tree_name }}</span></nav>
<div id="treeContainer" style="width:100vw;height:calc(100vh - 64px)"></div>
<script src="/static/js/tree.js"></script>
<script>renderTree({{ persons_json | safe }}, null);</script>
</body></html>""", tree_name=tree.name, persons_json=_json.dumps(persons))

@tree_bp.get('/api/trees/<int:tree_id>/export/pdf')
@require_auth
def export_pdf(tree_id):
    from weasyprint import HTML as WPHtml
    tree = Tree.query.filter_by(id=tree_id, user_id=g.user_id).first_or_404()
    persons_rows = ''.join(
        f"<tr><td>{p.first_name or ''} {p.last_name or ''}</td>"
        f"<td>{p.birth_year or '?'}</td><td>{p.birth_state or p.birth_country or '?'}</td>"
        f"<td>{p.death_year or '?'}</td><td>{p.confidence}%</td></tr>"
        for p in tree.persons
    )
    html = f"""<!DOCTYPE html><html><head><style>
body{{font-family:Arial,sans-serif;color:#111}}
h1{{color:#1e40af}}table{{width:100%;border-collapse:collapse}}
th,td{{border:1px solid #ccc;padding:6px;text-align:left}}
th{{background:#dbeafe}}</style></head><body>
<h1>{tree.name}</h1>
<p>Exported from RootBridge — {len(tree.persons)} persons</p>
<table><tr><th>Name</th><th>Born</th><th>Birth Place</th><th>Died</th><th>Confidence</th></tr>
{persons_rows}</table></body></html>"""
    pdf = WPHtml(string=html).write_pdf()
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename="{tree.name}.pdf"'
    return response
