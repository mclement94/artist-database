# artistdb/services/storage.py
"""
Everything related to file uploads and storing images.

This keeps your routes clean and makes it easier later to switch storage
(local folder vs S3 etc.)
"""

from datetime import datetime
from werkzeug.utils import secure_filename


def allowed_ext(filename: str, allowed: set[str]) -> bool:
    """Return True if filename has an allowed extension."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed


def save_upload(file_storage, upload_folder: str) -> str:
    """
    Save an uploaded file to disk and return the final filename.

    We add a timestamp prefix so two images named 'image.jpg' won't overwrite.
    """
    filename = secure_filename(file_storage.filename)
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    final = f"{ts}_{filename}"
    file_storage.save(f"{upload_folder}/{final}")
    return final
