# artistdb/config.py
"""
Central configuration.

This keeps environment variables / file paths out of your route logic.

Render vs Raspberry Pi:
- You can keep DATA_DIR on /var/data (persistent disk)
- Or fall back to local folder during dev
"""

import os


class Config:
    # IMPORTANT: Playwright needs this set early (like in your original app.py)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

    BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    # Render persistent disk often is /var/data
    DATA_DIR = os.environ.get("DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else BASE_DIR)
    os.makedirs(DATA_DIR, exist_ok=True)

    UPLOAD_FOLDER = os.path.join(DATA_DIR, "uploads")
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    MAIN_DB_PATH = os.path.join(DATA_DIR, "database.db")
    CERT_DB_PATH = os.path.join(DATA_DIR, "certificate_templates.db")

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-change-me")

    # SQLAlchemy main DB + bind for certificate templates
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + MAIN_DB_PATH
    SQLALCHEMY_BINDS = {"cert": "sqlite:///" + CERT_DB_PATH}
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # App defaults
    ARTIST_NAME = os.environ.get("ARTIST_NAME", "Miet Warlop")
    ALLOWED_ARTWORK_EXTENSIONS = {"jpg", "jpeg", "png"}
