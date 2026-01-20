import os
import io
from datetime import datetime

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    send_file,
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

import qrcode

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph
from reportlab.lib.utils import ImageReader

# --------------------------------------------------
# Configuration
# --------------------------------------------------

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

ARTIST_NAME = "Your Name"  # <-- change this
ALLOWED_EXTENSIONS = {"jpg", "jpeg"}  # JPG only as you requested

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-change-this-to-a-random-long-string"),
    SQLALCHEMY_DATABASE_URI="sqlite:///" + os.path.join(BASE_DIR, "database.db"),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    UPLOAD_FOLDER=os.path.join(BASE_DIR, "static", "uploads"),
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,  # 10MB
)

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

db = SQLAlchemy(app)

# --------------------------------------------------
# Helpers
# --------------------------------------------------


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def save_image(file) -> str:
    safe_name = secure_filename(file.filename)
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    filename = f"{timestamp}_{safe_name}"
    path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(path)
    return filename


def serializer():
    # token signer for QR actions
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="box-token")


def make_box_token(artwork_id: int) -> str:
    return serializer().dumps({"artwork_id": artwork_id})


def verify_box_token(token: str, max_age_seconds: int = 60 * 60 * 24 * 365 * 5):
    # 5 years by default (you can reprint labels anytime)
    return serializer().loads(token, max_age=max_age_seconds)


def current_location(artwork_id: int):
    return (
        LocationLog.query.filter_by(artwork_id=artwork_id)
        .order_by(LocationLog.changed_at.desc())
        .first()
    )


# --------------------------------------------------
# Models
# --------------------------------------------------


class Artwork(db.Model):
    __tablename__ = "artworks"

    id = db.Column(db.Integer, primary_key=True)

    title = db.Column(db.String(200), nullable=False)
    year = db.Column(db.String(10))
    series = db.Column(db.String(200))
    medium = db.Column(db.String(200), nullable=False)
    dimensions = db.Column(db.String(200))
    description = db.Column(db.Text)

    edition_type = db.Column(db.String(50))
    edition_info = db.Column(db.String(50))

    for_sale = db.Column(db.Boolean, default=False)
    price = db.Column(db.String(50))

    notes = db.Column(db.Text)

    image_filename = db.Column(db.String(255))

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class LocationLog(db.Model):
    __tablename__ = "location_logs"

    id = db.Column(db.Integer, primary_key=True)
    artwork_id = db.Column(db.Integer, db.ForeignKey("artworks.id"), nullable=False)

    location = db.Column(db.String(200), nullable=False)
    note = db.Column(db.Text)
    changed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    artwork = db.relationship("Artwork", backref=db.backref("location_logs", lazy=True))


# --------------------------------------------------
# Routes – navigation
# --------------------------------------------------


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/artworks")
def artwork_list():
    artworks = Artwork.query.order_by(Artwork.created_at.desc()).all()
    return render_template("artwork_list.html", artworks=artworks)


@app.route("/artworks/<int:artwork_id>")
def artwork_detail(artwork_id):
    artwork = Artwork.query.get_or_404(artwork_id)
    latest = current_location(artwork.id)
    return render_template("artwork_detail.html", artwork=artwork, latest=latest)


# --------------------------------------------------
# Routes – create & edit
# --------------------------------------------------


@app.route("/add-artwork", methods=["GET", "POST"])
def add_artwork():
    if request.method == "POST":
        image = request.files.get("image")
        image_filename = None
        if image and image.filename and allowed_file(image.filename):
            image_filename = save_image(image)

        artwork = Artwork(
            title=request.form["title"],
            year=request.form.get("year"),
            series=request.form.get("series"),
            medium=request.form["medium"],
            dimensions=request.form.get("dimensions"),
            description=request.form.get("description"),
            edition_type=request.form.get("edition_type"),
            edition_info=request.form.get("edition_info"),
            for_sale=request.form.get("for_sale") == "yes",
            price=request.form.get("price"),
            notes=request.form.get("notes"),
            image_filename=image_filename,
        )
        db.session.add(artwork)
        db.session.commit()
        return redirect(url_for("artwork_detail", artwork_id=artwork.id))

    return render_template("add_artwork.html")


@app.route("/artworks/<int:artwork_id>/edit", methods=["GET", "POST"])
def edit_artwork(artwork_id):
    artwork = Artwork.query.get_or_404(artwork_id)

    if request.method == "POST":
        artwork.title = request.form["title"]
        artwork.year = request.form.get("year")
        artwork.series = request.form.get("series")
        artwork.medium = request.form["medium"]
        artwork.dimensions = request.form.get("dimensions")
        artwork.description = request.form.get("description")
        artwork.edition_type = request.form.get("edition_type")
        artwork.edition_info = request.form.get("edition_info")
        artwork.for_sale = request.form.get("for_sale") == "yes"
        artwork.price = request.form.get("price")
        artwork.notes = request.form.get("notes")

        image = request.files.get("image")
        if image and image.filename and allowed_file(image.filename):
            artwork.image_filename = save_image(image)

        db.session.commit()
        return redirect(url_for("artwork_detail", artwork_id=artwork.id))

    return render_template("edit_artwork.html", artwork=artwork)


# --------------------------------------------------
# Box page – "What's in the box" (+ location history)
# QR code links here. Token unlocks update form.
# --------------------------------------------------


@app.route("/artworks/<int:artwork_id>/box", methods=["GET", "POST"])
def box_page(artwork_id):
    artwork = Artwork.query.get_or_404(artwork_id)

    token = request.args.get("token", "")
    can_update = False
    token_error = None

    # Validate token (required for updating location)
    if token:
        try:
            data = verify_box_token(token)
            can_update = (data.get("artwork_id") == artwork_id)
            if not can_update:
                token_error = "Invalid QR token for this artwork."
        except SignatureExpired:
            token_error = "This QR token has expired."
        except BadSignature:
            token_error = "Invalid QR token."

    # Handle location update (only if token is valid)
    success = False
    error = None
    if request.method == "POST":
        if not can_update:
            error = "Not authorized to update location (scan the QR code on the box)."
        else:
            location = (request.form.get("location") or "").strip()
            note = (request.form.get("note") or "").strip()
            if not location:
                error = "Location is required."
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


# --------------------------------------------------
# PDFs – Certificate (with image) & Box label (QR)
# Both displayed inline in browser
# --------------------------------------------------


@app.route("/artworks/<int:artwork_id>/certificate")
def certificate_pdf(artwork_id):
    artwork = Artwork.query.get_or_404(artwork_id)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    margin_x = 30 * mm
    y = height - 35 * mm

    # Title
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(width / 2, y, "Certificate of Authenticity")
    y -= 18 * mm

    # Artwork image (jpg/jpeg)
    if artwork.image_filename:
        img_path = os.path.join(app.config["UPLOAD_FOLDER"], artwork.image_filename)
        if os.path.exists(img_path):
            img = ImageReader(img_path)
            iw, ih = img.getSize()
            max_w = width - 2 * margin_x
            max_h = 70 * mm
            scale = min(max_w / iw, max_h / ih)
            dw, dh = iw * scale, ih * scale
            x = (width - dw) / 2
            y -= dh
            c.drawImage(img, x, y, width=dw, height=dh, preserveAspectRatio=True, mask="auto")
            y -= 10 * mm

    def line(label, value):
        nonlocal y
        c.setFont("Helvetica-Bold", 11)
        c.drawString(margin_x, y, f"{label}:")
        c.setFont("Helvetica", 11)
        c.drawString(margin_x + 75, y, value if value else "—")
        y -= 14

    line("Artist", ARTIST_NAME)
    line("Title", artwork.title)
    line("Year", artwork.year or "—")
    line("Medium", artwork.medium)
    line("Dimensions", artwork.dimensions or "—")
    line("Edition", artwork.edition_info or "Unique")
    y -= 10

    styles = getSampleStyleSheet()
    text = (
        "This document certifies that the artwork described above is an authentic work by the artist named, "
        "recorded in the artist’s archive."
    )
    p = Paragraph(text, styles["Normal"])
    p.wrapOn(c, width - 2 * margin_x, 100)
    p.drawOn(c, margin_x, y)
    y -= 55

    # Signature line
    c.setFont("Helvetica", 11)
    c.drawString(margin_x, y, "Signed:")
    c.line(margin_x + 55, y - 2, margin_x + 240, y - 2)
    y -= 22

    c.setFont("Helvetica", 11)
    c.drawString(margin_x, y, f"Date issued: {datetime.utcnow().strftime('%Y-%m-%d')}")
    y -= 14
    c.drawString(margin_x, y, f"Artwork ID: {artwork.id}")

    c.setFont("Helvetica-Oblique", 9)
    c.drawCentredString(width / 2, 18 * mm, "This certificate is valid only for the artwork referenced above.")

    c.showPage()
    c.save()
    buf.seek(0)

    return send_file(
        buf,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"certificate_{artwork.id}.pdf",
    )


@app.route("/artworks/<int:artwork_id>/box-label")
def box_label_pdf(artwork_id):
    artwork = Artwork.query.get_or_404(artwork_id)

    token = make_box_token(artwork.id)
    box_url = url_for("box_page", artwork_id=artwork.id, token=token, _external=True)

    # Create QR image (PNG in-memory)
    qr = qrcode.QRCode(border=1, box_size=10)
    qr.add_data(box_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")

    qr_bytes = io.BytesIO()
    qr_img.save(qr_bytes, format="PNG")
    qr_bytes.seek(0)
    qr_reader = ImageReader(qr_bytes)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    margin_x = 20 * mm
    y = height - 25 * mm

    c.setFont("Helvetica-Bold", 18)
    c.drawString(margin_x, y, "Box Label")
    y -= 12 * mm

    c.setFont("Helvetica", 12)
    c.drawString(margin_x, y, f"Artist: {ARTIST_NAME}")
    y -= 8 * mm
    c.drawString(margin_x, y, f"Title: {artwork.title}")
    y -= 8 * mm
    c.drawString(margin_x, y, f"Year: {artwork.year or '—'}")
    y -= 8 * mm
    c.drawString(margin_x, y, f"Medium: {artwork.medium}")
    y -= 8 * mm
    c.drawString(margin_x, y, f"Dimensions: {artwork.dimensions or '—'}")
    y -= 8 * mm
    c.drawString(margin_x, y, f"Edition: {artwork.edition_info or 'Unique'}")
    y -= 10 * mm

    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin_x, y, f"Artwork ID: {artwork.id}")
    y -= 8 * mm

    latest = current_location(artwork.id)
    c.setFont("Helvetica", 11)
    c.drawString(margin_x, y, f"Current location: {(latest.location if latest else '—')}")
    y -= 12 * mm

    # Optional thumbnail (jpg/jpeg)
    if artwork.image_filename:
        img_path = os.path.join(app.config["UPLOAD_FOLDER"], artwork.image_filename)
        if os.path.exists(img_path):
            img = ImageReader(img_path)
            iw, ih = img.getSize()
            max_w = 75 * mm
            max_h = 55 * mm
            scale = min(max_w / iw, max_h / ih)
            dw, dh = iw * scale, ih * scale
            c.drawImage(img, margin_x, y - dh, width=dw, height=dh, preserveAspectRatio=True, mask="auto")

    # QR on right
    qr_size = 60 * mm
    qr_x = width - margin_x - qr_size
    qr_y = height - 75 * mm - qr_size
    c.drawImage(qr_reader, qr_x, qr_y, width=qr_size, height=qr_size, mask="auto")

    c.setFont("Helvetica", 10)
    c.drawString(qr_x, qr_y - 7 * mm, "Scan: what's in the box")
    c.setFont("Helvetica-Oblique", 7)
    c.drawString(margin_x, 15 * mm, box_url)

    c.showPage()
    c.save()
    buf.seek(0)

    return send_file(
        buf,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"box_label_{artwork.id}.pdf",
    )


# --------------------------------------------------
# App entry point
# --------------------------------------------------

if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

