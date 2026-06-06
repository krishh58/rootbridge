"""One-time vault seeding endpoint — POST chunks of persons from local SQLite."""
import os
from flask import Blueprint, request, jsonify
from .db import db
from .models import Person

vault_import_bp = Blueprint('vault_import', __name__)

SEED_SECRET = os.environ.get('SEED_SECRET', '')


@vault_import_bp.post('/api/admin/vault-import')
def vault_import():
    if not SEED_SECRET or request.headers.get('X-Seed-Secret') != SEED_SECRET:
        return jsonify(error='forbidden'), 403

    rows = request.get_json(force=True)
    if not rows or not isinstance(rows, list):
        return jsonify(error='expected JSON array'), 400

    inserted = 0
    for r in rows:
        p = Person(
            first_name=r.get('first_name'),
            last_name=r.get('last_name'),
            middle_name=r.get('middle_name'),
            birth_year=r.get('birth_year'),
            birth_state=r.get('birth_state'),
            birth_country=r.get('birth_country'),
            death_year=r.get('death_year'),
            death_place=r.get('death_place'),
            notes=r.get('notes'),
            confidence=r.get('confidence'),
            soundex_key=r.get('soundex_key'),
            birth_decade=r.get('birth_decade'),
            tree_id=None,
            user_id=None,
        )
        db.session.add(p)
        inserted += 1

    db.session.commit()
    return jsonify(inserted=inserted)
