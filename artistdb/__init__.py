# artistdb/__init__.py
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
        template_folder="../templates",
        static_folder="../static",
    )

    app.config.from_object(Config)
    db.init_app(app)

    # Everything that touches db.session must happen inside app_context()
    with app.app_context():
        from . import models
        db.create_all()

        from .schema import ensure_artwork_status_column
        ensure_artwork_status_column()

    app.register_blueprint(main_bp)
    app.register_blueprint(artworks_bp)
    app.register_blueprint(certificates_bp)
    app.register_blueprint(box_bp)

    return app