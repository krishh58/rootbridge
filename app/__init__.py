import os
from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from .config import Config
from .db import db

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri='memory://',
)

def create_app(config=None):
    app = Flask(__name__, static_folder='../static', static_url_path='/static')
    app.config.from_object(config or Config)
    limiter.init_app(app)

    if not app.config.get('SECRET_KEY') and not app.config.get('TESTING'):
        raise RuntimeError("SECRET_KEY environment variable is not set")

    db.init_app(app)
    from flask_migrate import Migrate
    Migrate(app, db)

    from .auth import auth_bp
    from .routes import routes_bp
    from .search_routes import search_bp
    from .tree_routes import tree_bp
    from .alfred_routes import alfred_bp
    from .stripe_routes import stripe_bp
    from .gdpr_routes import gdpr_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(routes_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(tree_bp)
    app.register_blueprint(alfred_bp)
    app.register_blueprint(stripe_bp)
    app.register_blueprint(gdpr_bp)
    from .heritage_routes import heritage_bp
    app.register_blueprint(heritage_bp)
    from .collab_routes import collab_bp
    app.register_blueprint(collab_bp)
    from .fork_routes import fork_bp
    app.register_blueprint(fork_bp)
    from .match_routes import match_bp
    app.register_blueprint(match_bp)
    from .message_routes import message_bp
    app.register_blueprint(message_bp)
    from .document_routes import document_bp
    app.register_blueprint(document_bp)
    from .rootcommons_routes import rootcommons_bp
    app.register_blueprint(rootcommons_bp)
    from .vault_import_routes import vault_import_bp
    app.register_blueprint(vault_import_bp)
    from .ged_export_routes import ged_export_bp
    app.register_blueprint(ged_export_bp)
    from .print_routes import print_bp
    app.register_blueprint(print_bp)
    from .citation_routes import citation_bp
    app.register_blueprint(citation_bp)

    with app.app_context():
        from . import models  # noqa: register models with SQLAlchemy
        db.create_all()

    # Index builder disabled — was locking persons table on startup and blocking inserts
    # if not app.config.get('TESTING'):
    #     import threading
    #     from .match_index import build_hot_index
    #     def _build_index():
    #         build_hot_index(app)
    #     threading.Thread(target=_build_index, daemon=True, name='index-builder').start()

    if not app.config.get('TESTING') and not os.environ.get('SKIP_VAULT_SEED'):
        import threading
        def _seed_vault():
            import time, gzip, json, urllib.request
            time.sleep(5)
            with app.app_context():
                from .models import Person
                from .db import db
                VAULT_EXPECTED_MIN = 750_000
                count = db.session.execute(db.text('SELECT COUNT(*) FROM persons')).scalar()
                if count and count >= VAULT_EXPECTED_MIN:
                    print(f'Vault already seeded ({count:,} persons), skipping.')
                    return
                if count and count > 0:
                    print(f'Vault incomplete ({count:,} persons, expected {VAULT_EXPECTED_MIN:,}+). Clearing and re-seeding...')
                else:
                    print('Vault empty — downloading seed data...')
                try:
                    from .models import Tree, User
                    # Create system vault user + tree if needed
                    vault_user = User.query.filter_by(email='vault@rootcommons.internal').first()
                    if not vault_user:
                        import secrets, hashlib
                        vault_user = User(
                            email='vault@rootcommons.internal',
                            password_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                            tier='free', token_balance=0,
                        )
                        db.session.add(vault_user)
                        db.session.flush()
                    vault_tree = Tree.query.filter_by(name='RootCommons Vault').first()
                    if not vault_tree:
                        vault_tree = Tree(name='RootCommons Vault', user_id=vault_user.id)
                        db.session.add(vault_tree)
                        db.session.flush()
                    tree_id = vault_tree.id
                    # Clear any partial seed before re-importing
                    if count and count > 0:
                        db.session.execute(db.text('DELETE FROM persons WHERE tree_id = :tid'), {'tid': tree_id})
                        db.session.commit()
                        print(f'Cleared {count:,} partial records. Starting fresh import...')
                    import urllib.request as _req
                    opener = _req.build_opener(_req.HTTPRedirectHandler())
                    req = _req.Request(
                        'https://github.com/krishh58/rootbridge/releases/download/vault-seed-v1/vault_export.jsonl.gz',
                        headers={'User-Agent': 'Mozilla/5.0'}
                    )
                    with opener.open(req, timeout=120) as r:
                        data = r.read()
                    print(f'Downloaded {len(data):,} bytes. Importing...')
                    import datetime as _dt
                    LIVING_CUTOFF = _dt.datetime.now().year - 100
                    batch, total, skipped = [], 0, 0
                    for line in gzip.decompress(data).decode().splitlines():
                        row = json.loads(line)
                        by = row.get('birth_year')
                        dy = row.get('death_year')
                        if not dy and by and by > LIVING_CUTOFF:
                            skipped += 1
                            continue
                        batch.append(Person(
                            tree_id=tree_id,
                            first_name=row.get('first_name'), last_name=row.get('last_name'),
                            middle_name=row.get('middle_name'), birth_year=row.get('birth_year'),
                            birth_state=row.get('birth_state'), birth_country=row.get('birth_country'),
                            death_year=row.get('death_year'), death_place=row.get('death_place'),
                            notes=row.get('notes'), confidence=row.get('confidence'),
                            soundex_key=row.get('soundex_key'), birth_decade=row.get('birth_decade'),
                        ))
                        if len(batch) >= 1000:
                            db.session.bulk_save_objects(batch)
                            db.session.commit()
                            total += len(batch)
                            batch = []
                            print(f'Vault seed: {total:,} inserted')
                    if batch:
                        db.session.bulk_save_objects(batch)
                        db.session.commit()
                        total += len(batch)
                    print(f'Vault seed complete: {total:,} persons imported, {skipped:,} likely-living skipped.')
                except Exception as e:
                    print("ERROR:", f'Vault seed failed: {e}')
        threading.Thread(target=_seed_vault, daemon=True, name='vault-seeder').start()

    if not app.config.get('TESTING'):
        from apscheduler.schedulers.background import BackgroundScheduler
        from .matcher import run_matcher
        from .rescan import run_monthly_rescan
        scheduler = BackgroundScheduler(daemon=True)
        def _nightly_match():
            with app.app_context():
                run_matcher()
        def _monthly_rescan():
            with app.app_context():
                run_monthly_rescan()
        scheduler.add_job(_nightly_match, 'cron', hour=3, max_instances=1)
        scheduler.add_job(_monthly_rescan, 'cron', day=1, hour=7, max_instances=1)
        if not scheduler.running:
            scheduler.start()

    return app
