# artistdb/services/schema.py
import json
from sqlalchemy import text
from .extensions import db

def ensure_artwork_status_column():
    """
    Adds artwork.status and image columns if they are missing.
    Safe to run on every startup.
    """
    # SQLite: PRAGMA table_info(table_name) gives columns
    cols = db.session.execute(text("PRAGMA table_info(artwork)")).fetchall()
    col_names = {c[1] for c in cols}  # column name is index 1

    if "status" not in col_names:
        db.session.execute(text("ALTER TABLE artwork ADD COLUMN status TEXT"))

    if "image_filenames" not in col_names:
        db.session.execute(text("ALTER TABLE artwork ADD COLUMN image_filenames TEXT"))

    if "certificate_image_filename" not in col_names:
        db.session.execute(text("ALTER TABLE artwork ADD COLUMN certificate_image_filename TEXT"))

    if "sort_order" not in col_names:
        db.session.execute(text("ALTER TABLE artwork ADD COLUMN sort_order INTEGER DEFAULT 0"))

    if "status" not in col_names:
        if "for_sale" in col_names:
            db.session.execute(text("""
                UPDATE artwork
                SET status = CASE
                    WHEN for_sale = 1 THEN 'for_sale'
                    ELSE 'sold'
                END
                WHERE status IS NULL
            """))
        else:
            db.session.execute(text("""
                UPDATE artwork
                SET status = 'working'
                WHERE status IS NULL
            """))

    if "image_filename" in col_names:
        rows = db.session.execute(text("SELECT id, image_filename FROM artwork WHERE image_filename IS NOT NULL")).fetchall()
        for artwork_id, filename in rows:
            if filename:
                image_list = json.dumps([filename])
                db.session.execute(text("""
                    UPDATE artwork
                    SET image_filenames = :images,
                        certificate_image_filename = COALESCE(certificate_image_filename, :selected)
                    WHERE id = :id
                """), {"images": image_list, "selected": filename, "id": artwork_id})

    db.session.commit()