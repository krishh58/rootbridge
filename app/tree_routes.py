import secrets
import json as _json
from markupsafe import escape as html_escape
import threading
from flask import Blueprint, request, jsonify, g, make_response, render_template_string, current_app, Response, stream_with_context
from .auth import require_auth
from .db import db
from .models import Tree, Person, SearchResult, Gap, AlfredMessage, TreeCollaborator, PersonEdge
from .hometown import get_hometown_photo, get_historical_map, get_life_context

def _can_access_tree(tree_id: int, user_id: int, require_editor: bool = False) -> bool:
    from .models import TreeCollaborator
    tree = Tree.query.get(tree_id)
    if not tree:
        return False
    if tree.user_id == user_id:
        return True
    collab = TreeCollaborator.query.filter_by(tree_id=tree_id, user_id=user_id).first()
    if not collab:
        return False
    if require_editor:
        return collab.role == 'editor'
    return True

tree_bp = Blueprint('tree', __name__)

def _trigger_matcher_async(app, person_id: int):
    def _run():
        with app.app_context():
            from .matcher import run_matcher
            run_matcher(person_id=person_id)
    threading.Thread(target=_run, daemon=True).start()

def _person_to_dict(p):
    return {
        'id': p.id, 'tree_id': p.tree_id,
        'first_name': p.first_name, 'middle_name': p.middle_name,
        'last_name': p.last_name,
        'birth_year': p.birth_year, 'birth_state': p.birth_state,
        'birth_country': p.birth_country, 'death_year': p.death_year,
        'death_place': p.death_place, 'confidence': p.confidence,
        'parent_ids': p.parent_ids or [], 'spouse_ids': p.spouse_ids or [],
        'notes': p.notes,
        'research_findings': p.research_findings or [],
    }

def _person_detail(p):
    d = _person_to_dict(p)
    d['search_results'] = [
        {'id': r.id, 'source': r.source, 'record_type': r.record_type,
         'url': r.url, 'raw_data': r.raw_data}
        for r in p.search_results
    ]
    d['gaps'] = [
        {'id': gap.id, 'gap_type': gap.gap_type, 'label': gap.label,
         'detail': gap.detail, 'suggested_source': gap.suggested_source,
         'suggested_query': gap.suggested_query, 'resolved': gap.resolved}
        for gap in p.gaps
    ]
    return d

@tree_bp.get('/api/trees')
@require_auth
def list_trees():
    owned = Tree.query.filter_by(user_id=g.user_id).order_by(Tree.updated_at.desc()).all()
    collab_ids = [
        c.tree_id for c in TreeCollaborator.query.filter_by(user_id=g.user_id).all()
    ]
    collab = Tree.query.filter(Tree.id.in_(collab_ids)).order_by(Tree.updated_at.desc()).all() if collab_ids else []
    def _summary(t):
        return {
            'id': t.id, 'name': t.name,
            'person_count': len(t.persons),
            'updated_at': t.updated_at.isoformat() if t.updated_at else None,
            'owned': t.user_id == g.user_id,
        }
    return jsonify({
        'trees': [_summary(t) for t in owned],
        'shared': [_summary(t) for t in collab],
    })


@tree_bp.get('/api/trees/<int:tree_id>')
@require_auth
def get_tree(tree_id):
    if not _can_access_tree(tree_id, g.user_id):
        from flask import abort
        abort(404)
    tree = Tree.query.get(tree_id)
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
    p = Person.query.get_or_404(person_id)
    if not _can_access_tree(p.tree_id, g.user_id):
        from flask import abort
        abort(404)
    return jsonify(_person_detail(p))

@tree_bp.post('/api/persons')
@require_auth
def create_person():
    if not g.user_id:
        return jsonify({'error': 'Please register to save your family tree.', 'needs_auth': True}), 401
    data = request.get_json() or {}
    tree_name = data.get('tree_name') or f"{data.get('last_name', 'My')} Family"
    # Also accept tree_id to look up an existing tree directly
    tree = None
    if data.get('tree_id'):
        tree = Tree.query.filter_by(id=data['tree_id'], user_id=g.user_id).first()
    if not tree:
        tree = Tree.query.filter_by(user_id=g.user_id, name=tree_name).first()
    if not tree:
        tree = Tree(user_id=g.user_id, name=tree_name)
        db.session.add(tree)
    middle = data.get('middle_name', '')
    first_full = f"{data.get('first_name', '')} {middle}".strip() if middle else data.get('first_name', '')
    p = Person(
        first_name=first_full,
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
    _trigger_matcher_async(app=current_app._get_current_object(), person_id=p.id)
    return jsonify({'id': p.id, 'person_id': p.id, 'tree_id': tree.id}), 201

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
    _trigger_matcher_async(app=current_app._get_current_object(), person_id=p.id)
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
    p = Person.query.get_or_404(person_id)
    if not _can_access_tree(p.tree_id, g.user_id):
        from flask import abort
        abort(404)
    place = p.birth_state or p.birth_country or ''
    if not place:
        return jsonify({'available': False})
    name = f'{p.first_name or ""} {p.last_name or ""}'.strip()
    photo = get_hometown_photo(place, p.birth_year)
    historical_map = get_historical_map(place, p.birth_year)
    life_context = get_life_context(name, place, p.birth_year, p.death_year)
    return jsonify({
        'available': True, 'place': place,
        'photo': photo, 'map': historical_map, 'life_context': life_context,
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
<script>renderTree({{ persons | tojson }}, null);</script>
</body></html>""", tree_name=tree.name, persons=persons)

@tree_bp.get('/api/trees/<int:tree_id>/export/gedcom')
@require_auth
def export_gedcom(tree_id):
    from io import StringIO
    from datetime import datetime, timezone as tz
    tree = Tree.query.filter_by(id=tree_id, user_id=g.user_id).first_or_404()
    persons = list(tree.persons)

    id_map = {p.id: f'@I{i + 1}@' for i, p in enumerate(persons)}

    # Build family units from spouse_ids and parent_ids
    families = {}   # frozenset_key → {ged_id, husb, wife, children}
    fam_counter = 0

    def _get_fam(parent_ids):
        nonlocal fam_counter
        key = frozenset(parent_ids)
        if key not in families:
            fam_counter += 1
            sp = sorted(parent_ids)
            families[key] = {
                'ged_id': f'@F{fam_counter}@',
                'husb': sp[0] if sp else None,
                'wife': sp[1] if len(sp) > 1 else None,
                'children': [],
            }
        return families[key]

    processed_couples = set()
    for p in persons:
        for sp_id in (p.spouse_ids or []):
            couple = frozenset([p.id, sp_id])
            if couple not in processed_couples:
                processed_couples.add(couple)
                _get_fam([p.id, sp_id])

    for p in persons:
        if p.parent_ids:
            fam = _get_fam(p.parent_ids[:2])
            fam['children'].append(p.id)

    fams_of = {p.id: [] for p in persons}   # person → [fam_ids as spouse]
    famc_of = {p.id: [] for p in persons}   # person → [fam_ids as child]
    for fam in families.values():
        if fam['husb'] and fam['husb'] in fams_of:
            fams_of[fam['husb']].append(fam['ged_id'])
        if fam['wife'] and fam['wife'] in fams_of:
            fams_of[fam['wife']].append(fam['ged_id'])
        for cid in fam['children']:
            if cid in famc_of:
                famc_of[cid].append(fam['ged_id'])

    buf = StringIO()

    def w(line):
        buf.write(line + '\r\n')

    date_str = datetime.now(tz.utc).strftime('%d %b %Y').upper()
    safe_tree = ''.join(c for c in tree.name if c.isalnum() or c in ' _-')

    w('0 HEAD')
    w('1 SOUR RootBridge')
    w('2 VERS 1.0')
    w('2 NAME RootBridge')
    w('1 GEDC')
    w('2 VERS 5.5.1')
    w('2 FORM LINEAGE-LINKED')
    w('1 CHAR UTF-8')
    w(f'1 DATE {date_str}')
    w(f'1 FILE {safe_tree}.ged')
    w('1 NOTE Exported from RootBridge')

    for p in persons:
        ged_id = id_map[p.id]
        w(f'0 {ged_id} INDI')
        given = (p.first_name or '').strip()
        surname = (p.last_name or '').strip()
        w(f'1 NAME {given} /{surname}/'.strip())
        if given:
            w(f'2 GIVN {given}')
        if surname:
            w(f'2 SURN {surname}')

        if p.birth_year or p.birth_state or p.birth_country:
            w('1 BIRT')
            if p.birth_year:
                w(f'2 DATE {p.birth_year}')
            place = ', '.join(filter(None, [p.birth_state, p.birth_country]))
            if place:
                w(f'2 PLAC {place}')

        if p.death_year or p.death_place:
            w('1 DEAT')
            if p.death_year:
                w(f'2 DATE {p.death_year}')
            if p.death_place:
                w(f'2 PLAC {p.death_place}')

        for fam_id in fams_of[p.id]:
            w(f'1 FAMS {fam_id}')
        for fam_id in famc_of[p.id]:
            w(f'1 FAMC {fam_id}')

        if p.notes:
            w(f'1 NOTE {p.notes.replace(chr(10), " ")[:240]}')
        if p.confidence:
            w(f'1 NOTE RootBridge research coverage: {p.confidence}%')

    for fam in families.values():
        w(f'0 {fam["ged_id"]} FAM')
        if fam['husb'] and fam['husb'] in id_map:
            w(f'1 HUSB {id_map[fam["husb"]]}')
        if fam['wife'] and fam['wife'] in id_map:
            w(f'1 WIFE {id_map[fam["wife"]]}')
        for cid in fam['children']:
            if cid in id_map:
                w(f'1 CHIL {id_map[cid]}')

    w('0 TRLR')

    ged_bytes = buf.getvalue().encode('utf-8')
    response = make_response(ged_bytes)
    response.headers['Content-Type'] = 'application/x-gedcom; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename="{safe_tree}.ged"'
    return response


@tree_bp.get('/api/trees/<int:tree_id>/export/pdf')
@require_auth
def export_pdf(tree_id):
    from weasyprint import HTML as WPHtml
    tree = Tree.query.filter_by(id=tree_id, user_id=g.user_id).first_or_404()
    persons_rows = ''.join(
        f"<tr><td>{html_escape(f'{p.first_name or chr(32)}{p.last_name or chr(32)}'.strip())}</td>"
        f"<td>{p.birth_year or '?'}</td><td>{html_escape(str(p.birth_state or p.birth_country or '?'))}</td>"
        f"<td>{p.death_year or '?'}</td><td>{p.confidence}%</td></tr>"
        for p in tree.persons
    )
    html = f"""<!DOCTYPE html><html><head><style>
body{{font-family:Arial,sans-serif;color:#111}}
h1{{color:#1e40af}}table{{width:100%;border-collapse:collapse}}
th,td{{border:1px solid #ccc;padding:6px;text-align:left}}
th{{background:#dbeafe}}</style></head><body>
<h1>{html_escape(tree.name)}</h1>
<p>Exported from RootBridge — {len(tree.persons)} persons</p>
<table><tr><th>Name</th><th>Born</th><th>Birth Place</th><th>Died</th><th>Confidence</th></tr>
{persons_rows}</table></body></html>"""
    pdf = WPHtml(string=html).write_pdf()
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    safe_name = ''.join(c for c in tree.name if c.isalnum() or c in ' _-')
    response.headers['Content-Disposition'] = f'attachment; filename="{safe_name}.pdf"'
    return response


@tree_bp.post('/api/gedcom/import')
@require_auth
def import_gedcom():
    raw = request.get_data(as_text=True)
    if not raw:
        return jsonify({'error': 'No data received'}), 400

    # Parse INDI records from the GEDCOM stream
    individuals = {}   # tag_id → dict
    current_id = None
    current_level1 = None

    for line in raw.splitlines():
        parts = line.strip().split(' ', 2)
        if len(parts) < 2:
            continue
        level, tag = parts[0], parts[1]
        value = parts[2] if len(parts) > 2 else ''

        if level == '0':
            current_id = None
            current_level1 = None
            if tag.startswith('@') and len(parts) > 2 and parts[2] == 'INDI':
                current_id = tag
                individuals[current_id] = {
                    'first': '', 'last': '',
                    'birth_year': None, 'birth_place': '',
                    'death_year': None, 'death_place': '',
                }
        elif current_id and level == '1':
            current_level1 = tag
            if tag == 'NAME':
                # Format: Given /Surname/
                name = value.replace('/', ' ').strip()
                parts2 = name.split()
                if len(parts2) >= 2:
                    individuals[current_id]['last'] = parts2[-1]
                    individuals[current_id]['first'] = ' '.join(parts2[:-1])
                else:
                    individuals[current_id]['last'] = name
        elif current_id and level == '2':
            rec = individuals[current_id]
            if current_level1 == 'BIRT':
                if tag == 'DATE':
                    try:
                        rec['birth_year'] = int(value.split()[-1])
                    except (ValueError, IndexError):
                        pass
                elif tag == 'PLAC':
                    rec['birth_place'] = value[:100]
            elif current_level1 == 'DEAT':
                if tag == 'DATE':
                    try:
                        rec['death_year'] = int(value.split()[-1])
                    except (ValueError, IndexError):
                        pass
                elif tag == 'PLAC':
                    rec['death_place'] = value[:255]

    if not individuals:
        return jsonify({'error': 'No individuals found in GEDCOM file'}), 400

    # Create a new tree from the filename or use first person's surname
    surnames = [v['last'] for v in individuals.values() if v['last']]
    tree_name = (surnames[0] + ' Family') if surnames else 'Imported Tree'
    tree = Tree(user_id=g.user_id, name=tree_name)
    db.session.add(tree)
    db.session.flush()

    count = 0
    for rec in individuals.values():
        if not rec['last'] and not rec['first']:
            continue
        person = Person(
            tree_id=tree.id,
            first_name=rec['first'] or None,
            last_name=rec['last'] or None,
            birth_year=rec['birth_year'],
            birth_state=rec['birth_place'] or None,
            death_year=rec['death_year'],
            death_place=rec['death_place'] or None,
            confidence=0,
        )
        db.session.add(person)
        count += 1

    db.session.commit()
    return jsonify({'imported': count, 'tree_id': tree.id}), 201


@tree_bp.get('/api/persons/<int:person_id>/auto-build')
@require_auth
def auto_build_ancestry(person_id: int):
    """
    SSE endpoint — streams ancestry chain building in real time.
    Alfred searches each ancestor generation, extracts parent names,
    creates Person nodes, and queues the next generation automatically.
    """
    person = Person.query.get(person_id)
    if not person:
        return jsonify({'error': 'Person not found'}), 404
    if not _can_access_tree(person.tree_id, g.user_id):
        return jsonify({'error': 'Access denied'}), 403

    from .ancestry_chain import auto_build_tree_stream
    from .models import User, Tree

    app = current_app._get_current_object()
    user_id = g.user_id
    tree_id = person.tree_id

    def generate():
        yield from auto_build_tree_stream(
            start_person_id = person_id,
            tree_id         = tree_id,
            user_id         = user_id,
            app             = app,
            db              = db,
            Person          = Person,
            PersonEdge      = PersonEdge,
            Tree            = Tree,
        )

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )


@tree_bp.post('/api/persons/<int:person_id>/extract-family')
@require_auth
def extract_family_names(person_id: int):
    """
    One-shot endpoint — scans existing search results for this person
    and returns extracted family member names without running new searches.
    Useful for previewing what auto-build would find.
    """
    person = Person.query.get(person_id)
    if not person:
        return jsonify({'error': 'Person not found'}), 404
    if not _can_access_tree(person.tree_id, g.user_id):
        return jsonify({'error': 'Access denied'}), 403

    from .ancestry_chain import extract_family_from_results

    results = [
        {
            'source':    r.source,
            'snippet':   (r.raw_data if isinstance(r.raw_data, dict) else {}).get('snippet', ''),
            'full_text': (r.raw_data if isinstance(r.raw_data, dict) else {}).get('full_text', ''),
            'title':     (r.raw_data if isinstance(r.raw_data, dict) else {}).get('title', ''),
        }
        for r in person.search_results
    ]
    family = extract_family_from_results(results)
    return jsonify({
        'person': f'{person.first_name} {person.last_name}',
        'parents_extracted': [{'first': f, 'last': l} for f, l in family['parents']],
        'spouses_extracted': [{'first': f, 'last': l} for f, l in family['spouses']],
        'result_count': len(results),
    })
