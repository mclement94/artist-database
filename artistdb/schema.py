# artistdb/services/schema.py
from sqlalchemy import text
from .extensions import db

def ensure_artwork_status_column():
    """
    Adds artwork.status column if it's missing (SQLite schema migration-lite).
    Safe to run on every startup.
    """
    # SQLite: PRAGMA table_info(table_name) gives columns
    cols = db.session.execute(text("PRAGMA table_info(artwork)")).fetchall()
    col_names = {c[1] for c in cols}  # column name is index 1

    if "status" not in col_names:
        db.session.execute(text("ALTER TABLE artwork ADD COLUMN status TEXT"))
        # Optional: backfill status based on for_sale if that exists
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
        db.session.commit()