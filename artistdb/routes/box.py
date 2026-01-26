# artistdb/routes/box.py
"""
Box page + box label (QR PDF).
"""

import io

import qrcode
from flask import Blueprint, Response, current_app, render_template, send_file, url_for
from itsdangerous import BadSignature, SignatureExpired
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from ..extensions import db
from ..models import Artwork, LocationLog
from ..services.box import current_location, make_box_token, public_base_url, verify_box_token

bp = Blueprint("box", __name__)

@bp.route("/artworks/<int:artwork_id>/box", methods=["GET", "POST"])
def box_page(artwork_id):
    artwork = Artwork.query.get_or_404(artwork_id)

    token = (current_app.request_class.args.get("token") if False else None)  # (ignore: placeholder)
    # Flask doesn't expose request via current_app; import request normally:
    from flask import request

    token = request.args.get("token", "")
    can_update = False
    token_error = None

    if token:
        try:
            data = verify_box_token(current_app.config["SECRET_KEY"], token)
            can_update = (data.get("artwork_id") == artwork_id)
        except (BadSignature, SignatureExpired):
            token_error = "Invalid or expired QR code."

    success = False
    error = None

    if request.method == "POST":
        if not can_update:
            error = "Not authorised."
        else:
            location = (request.form.get("location") or "").strip()
            note = (request.form.get("note") or "").strip()
            if not location:
                error = "Location required."
            else:
                db.session.add(LocationLog(artwork_id=artwork.id, location=location, note=note))
                db.session.commit()
                success = True

    latest = current_location(artwork.id)
    history = (
        LocationLog.query.filter_by(artwork_id=artwork.id)
        .order_by(LocationLog.changed_at.desc())
        .all()
    )

    return render_template(
        "box_page.html",
        artwork=artwork,
        latest=latest,
        history=history,
        can_update=can_update,
        token=token,
        token_error=token_error,
        success=success,
        error=error,
    )


@bp.route("/artworks/<int:artwork_id>/box-label")
def box_label_pdf(artwork_id):
    artwork = Artwork.query.get_or_404(artwork_id)

    base = public_base_url()
    token = make_box_token(current_app.config["SECRET_KEY"], artwork.id)
    box_url = f"{base}{url_for('box.box_page', artwork_id=artwork.id, token=token)}"

    qr = qrcode.make(box_url)
    qr_buf = io.BytesIO()
    qr.save(qr_buf)
    qr_buf.seek(0)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    qr_size = 60 * mm
    c.drawImage(ImageReader(qr_buf), 120 * mm, 160 * mm, qr_size, qr_size)

    c.setFont("Helvetica-Bold", 14)
    c.drawString(30 * mm, 190 * mm, artwork.title)

    c.setFont("Helvetica", 12)
    c.drawString(30 * mm, 180 * mm, f"ID: {artwork.id}")

    c.setFont("Helvetica", 9)
    c.drawString(30 * mm, 170 * mm, box_url)

    c.showPage()
    c.save()

    buf.seek(0)
    return send_file(buf, mimetype="application/pdf", as_attachment=False)
