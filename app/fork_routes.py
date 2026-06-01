from flask import Blueprint, jsonify, g
from .auth import require_auth
from .db import db
from .models import Tree, Person
from .tree_routes import _can_access_tree

fork_bp = Blueprint('fork', __name__)


@fork_bp.post('/api/persons/<int:person_id>/fork')
@require_auth
def fork_person(person_id):
    p = Person.query.get_or_404(person_id)

    if not _can_access_tree(p.tree_id, g.user_id):
        from flask import abort
        abort(404)

    # Build new tree name
    parts = [p.first_name or '', p.last_name or '']
    tree_name = ' '.join(part for part in parts if part).strip()
    tree_name = f"{tree_name} Family" if tree_name else 'Family'

    new_tree = Tree(user_id=g.user_id, name=tree_name)
    db.session.add(new_tree)
    db.session.flush()  # get new_tree.id

    new_person = Person(
        tree_id=new_tree.id,
        first_name=p.first_name,
        last_name=p.last_name,
        birth_year=p.birth_year,
        birth_state=p.birth_state,
        birth_country=p.birth_country,
        confidence=p.confidence,
    )
    db.session.add(new_person)
    db.session.commit()

    return jsonify({'tree_id': new_tree.id, 'person_id': new_person.id}), 201
