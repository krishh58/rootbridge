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

    with app.app_context():
        from . import models  # noqa: register models with SQLAlchemy
        db.create_all()

    return app
