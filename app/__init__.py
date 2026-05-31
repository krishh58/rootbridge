from flask import Flask
from .config import Config
from .db import db

def create_app(config=None):
    app = Flask(__name__, static_folder='../static', static_url_path='/static')
    app.config.from_object(config or Config)

    db.init_app(app)

    from .auth import auth_bp
    from .routes import routes_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(routes_bp)

    with app.app_context():
        db.create_all()

    return app
