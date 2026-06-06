from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from .config import Config
from .db import db

limiter = Limiter(key_func=get_remote_address, default_limits=[])

def create_app(config=None):
    app = Flask(__name__, static_folder='../static', static_url_path='/static')
    app.config.from_object(config or Config)
    limiter.init_app(app)

    if not app.config.get('SECRET_KEY') and not app.config.get('TESTING'):
        raise RuntimeError("SECRET_KEY environment variable is not set")

    db.init_app(app)

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

    with app.app_context():
        from . import models  # noqa: register models with SQLAlchemy
        db.create_all()

    if not app.config.get('TESTING'):
        import threading
        from .match_index import build_hot_index
        def _build_index():
            build_hot_index(app)
        threading.Thread(target=_build_index, daemon=True, name='index-builder').start()

    if not app.config.get('TESTING'):
        import threading
        def _seed_vault():
            import time, gzip, json, urllib.request
            time.sleep(5)
            with app.app_context():
                from .models import Person
                from .db import db
                count = db.session.execute(db.text('SELECT COUNT(*) FROM persons')).scalar()
                if count and count > 0:
                    app.logger.info(f'Vault already seeded ({count:,} persons), skipping.')
                    return
                app.logger.info('Vault empty — downloading seed data...')
                try:
                    req = urllib.request.Request(
                        'https://api.github.com/repos/krishh58/rootbridge/releases/assets/440261710',
                        headers={'Authorization': 'token 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',
                                 'Accept': 'application/octet-stream'}
                    )
                    with urllib.request.urlopen(req) as r:
                        data = r.read()
                    app.logger.info(f'Downloaded {len(data):,} bytes. Importing...')
                    batch, total = [], 0
                    for line in gzip.decompress(data).decode().splitlines():
                        row = json.loads(line)
                        batch.append(Person(
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
                            app.logger.info(f'Vault seed: {total:,} inserted')
                    if batch:
                        db.session.bulk_save_objects(batch)
                        db.session.commit()
                        total += len(batch)
                    app.logger.info(f'Vault seed complete: {total:,} persons.')
                except Exception as e:
                    app.logger.error(f'Vault seed failed: {e}')
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
