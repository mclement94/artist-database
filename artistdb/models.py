# artistdb/models.py
"""
Database models (tables).

Rule of thumb:
- models.py should NOT contain Flask routes
- models.py should NOT contain PDF generation
- models.py just defines data structure + relationships
"""

import json
from datetime import datetime

from .extensions import db


class Artwork(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    title = db.Column(db.String(200), nullable=False)
    year = db.Column(db.String(10))
    series = db.Column(db.String(200))
    medium = db.Column(db.String(200), nullable=False)
    dimensions = db.Column(db.String(200))
    description = db.Column(db.Text)

    edition_type = db.Column(db.String(50))
    edition_info = db.Column(db.String(50))

    status = db.Column(db.String(50), default="working")  # working, for_sale, sold
    for_sale = db.Column(db.Boolean, default=False)
    price = db.Column(db.String(50))

    notes = db.Column(db.Text)

    image_filename = db.Column(db.String(255))
    image_filenames = db.Column(db.Text)
    certificate_image_filename = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sort_order = db.Column(db.Integer, default=0)

    @property
    def images(self):
        if self.image_filenames:
            try:
                value = json.loads(self.image_filenames)
            except ValueError:
                return []
            return [str(x) for x in value if x]

        if self.image_filename:
            return [self.image_filename]

        return []

    @images.setter
    def images(self, value):
        if value is None:
            self.image_filenames = None
            return

        self.image_filenames = json.dumps([str(x) for x in value if x])

    @property
    def certificate_image(self):
        if self.certificate_image_filename:
            return self.certificate_image_filename
        if self.image_filename:
            return self.image_filename
        images = self.images
        return images[0] if images else None

    @property
    def exc_vat(self):
        """Price excluding VAT (6%)"""
        if not self.price:
            return None
        try:
            return float(self.price.replace('€', '').replace(',', '').strip())
        except (ValueError, AttributeError):
            return None

    @property
    def inc_vat(self):
        """Price including VAT (6%)"""
        exc = self.exc_vat
        if exc is None:
            return None
        return exc * 1.06


class LocationLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    artwork_id = db.Column(db.Integer, db.ForeignKey("artwork.id"), nullable=False)

    location = db.Column(db.String(200), nullable=False)
    note = db.Column(db.Text)
    changed_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Backref: artwork.location_logs gives you the log list
    artwork = db.relationship("Artwork", backref="location_logs")


# Legacy table (you said it’s safe to keep; not used by Unlayer flow now)
class CertificateTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    settings_json = db.Column(db.Text, nullable=False, default="{}")
    logo_filename = db.Column(db.String(255), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UnlayerCertificateTemplate(db.Model):
    """
    Stored in the "cert" bind database.
    This keeps certificate templates separate from the artworks DB.
    """
    __bind_key__ = "cert"
    __tablename__ = "unlayer_certificate_templates"

    id = db.Column(db.Integer, primary_key=True)
    design_json = db.Column(db.Text, nullable=True)  # JSON string
    html = db.Column(db.Text, nullable=True)         # exported HTML
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UnlayerPrintTemplate(db.Model):
    """
    Stored in the "cert" bind database as well, but used for print page layouts.
    """
    __bind_key__ = "cert"
    __tablename__ = "unlayer_print_templates"

    id = db.Column(db.Integer, primary_key=True)
    design_json = db.Column(db.Text, nullable=True)
    html = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
