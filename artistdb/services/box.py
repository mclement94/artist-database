# artistdb/services/box.py
"""
"Box" feature logic:
- create/verify QR token
- get artwork current location (latest log)

This logic is used by routes/box.py and routes/artworks.py.
"""

import os
from flask import request
from itsdangerous import URLSafeTimedSerializer

from ..models import LocationLog


def public_base_url() -> str:
    """
    Used for QR links.
    - On Render you can set PUBLIC_BASE_URL in env
    - Locally it uses request.url_root
    """
    return os.environ.get("PUBLIC_BASE_URL", request.url_root.rstrip("/"))


def serializer(secret_key: str) -> URLSafeTimedSerializer:
    """Serializer for signed tokens (prevents random people forging access)."""
    return URLSafeTimedSerializer(secret_key, salt="box-token")


def make_box_token(secret_key: str, artwork_id: int) -> str:
    """Generate a signed token that encodes artwork_id."""
    return serializer(secret_key).dumps({"artwork_id": artwork_id})


def verify_box_token(secret_key: str, token: str, max_age_seconds: int = 60 * 60 * 24 * 365 * 5):
    """
    Verify token signature + age.
    Default max age = 5 years.
    """
    return serializer(secret_key).loads(token, max_age=max_age_seconds)


def current_location(artwork_id: int):
    """Return the latest LocationLog row (or None)."""
    return (
        LocationLog.query.filter_by(artwork_id=artwork_id)
        .order_by(LocationLog.changed_at.desc())
        .first()
    )
