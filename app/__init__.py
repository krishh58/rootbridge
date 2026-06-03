from flask import Flask
from .config import Config
from .db import db

def create_app(config=None):
    app = Flask(__name__, static_folder='../static', static_url_path='/static')
    app.config.from_object(config or Config)

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

    with app.app_context():
        from . import models  # noqa: register models with SQLAlchemy
        db.create_all()

    if not app.config.get('TESTING'):
        from apscheduler.schedulers.background import BackgroundScheduler
        from .matcher import run_matcher
        scheduler = BackgroundScheduler(daemon=True)
        def _nightly_match():
            with app.app_context():
                run_matcher()
        scheduler.add_job(_nightly_match, 'cron', hour=3, max_instances=1)
        if not scheduler.running:
            scheduler.start()

    return app
