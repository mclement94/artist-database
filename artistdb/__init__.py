# artistdb/__init__.py
"""
This file creates the Flask app (application factory pattern).

Why do professionals do this?
- Easier testing
- Easier configuration (Render vs Raspberry Pi vs local)
- Keeps your code modular
"""

from flask import Flask

from .config import Config
from .extensions import db
from .routes.main import bp as main_bp
from .routes.artworks import bp as artworks_bp
from .routes.certificates import bp as certificates_bp
from .routes.box import bp as box_bp


def create_app() -> Flask:
    app = Flask(
        __name__,
        # We keep templates/ and static/ at project root (same as now)
        template_folder="../templates",
        static_folder="../static",
    )

    # Load config (paths, DB locations, secrets, etc.)
    app.config.from_object(Config)

    # Initialize extensions (SQLAlchemy etc.)
    db.init_app(app)

    # Create database tables the first time
    # (For bigger apps you'd use migrations, but this is fine for now.)
    with app.app_context():
        from . import models  # ensures models are registered before create_all()
        db.create_all()

    with app.app_context():
        from artistdb import models
        db.create_all()

    from .services.schema import ensure_artwork_status_column
    ensure_artwork_status_column()

    # Register route blueprints (each feature in its own file)
    app.register_blueprint(main_bp)
    app.register_blueprint(artworks_bp)
    app.register_blueprint(certificates_bp)
    app.register_blueprint(box_bp)

    return app
