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
    send_from_directory,
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import qrcode

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader

# --------------------------------------------------
# Basic config
# --------------------------------------------------

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "database.db")

UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {"jpg", "jpeg"}
ARTIST_NAME = "Miet Warlop"

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-change-me"),
    SQLALCHEMY_DATABASE_URI="sqlite:///" + DB_PATH,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    UPLOAD_FOLDER=UPLOAD_DIR,
)

print("DATA_DIR =", DATA_DIR)
print("DB_PATH  =", DB_PATH)
print("UPLOAD_DIR =", UPLOAD_DIR)

db = SQLAlchemy(app)



# --------------------------------------------------
# Helpers
# --------------------------------------------------


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def save_image(file):
    filename = secure_filename(file.filename)
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    final = f"{ts}_{filename}"
    file.save(os.path.join(app.config["UPLOAD_FOLDER"], final))
    return final


def serializer():
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="box-token")


def make_box_token(artwork_id):
    return serializer().dumps({"artwork_id": artwork_id})


def verify_box_token(token, max_age=60 * 60 * 24 * 365 * 5):
    return serializer().loads(token, max_age=max_age)


def current_location(artwork_id):
    return (
        LocationLog.query.filter_by(artwork_id=artwork_id)
        .order_by(LocationLog.changed_at.desc())
        .first()
    )


# --------------------------------------------------
# Models
# --------------------------------------------------


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

    for_sale = db.Column(db.Boolean, default=False)
    price = db.Column(db.String(50))

    notes = db.Column(db.Text)

    image_filename = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class LocationLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    artwork_id = db.Column(db.Integer, db.ForeignKey("artwork.id"), nullable=False)

    location = db.Column(db.String(200), nullable=False)
    note = db.Column(db.Text)
    changed_at = db.Column(db.DateTime, default=datetime.utcnow)

    artwork = db.relationship("Artwork", backref="location_logs")


# --------------------------------------------------
# Init DB (important for Render)
# --------------------------------------------------

with app.app_context():
    db.create_all()

# --------------------------------------------------
# Static serving for uploads
# --------------------------------------------------


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


# --------------------------------------------------
# Pages
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
# Create / Edit
# --------------------------------------------------


@app.route("/add-artwork", methods=["GET", "POST"])
def add_artwork():
    if request.method == "POST":
        image = request.files.get("image")
        image_filename = None

        if image and image.filename:
            if not allowed_file(image.filename):
                return "Only JPG/JPEG allowed", 400
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
        if image and image.filename:
            if not allowed_file(image.filename):
                return "Only JPG/JPEG allowed", 400
            artwork.image_filename = save_image(image)

        db.session.commit()
        return redirect(url_for("artwork_detail", artwork_id=artwork.id))

    return render_template("edit_artwork.html", artwork=artwork)


# --------------------------------------------------
# Box page (QR target)
# --------------------------------------------------


@app.route("/artworks/<int:artwork_id>/box", methods=["GET", "POST"])
def box_page(artwork_id):
    artwork = Artwork.query.get_or_404(artwork_id)

    token = request.args.get("token", "")
    can_update = False
    token_error = None

    if token:
        try:
            data = verify_box_token(token)
            can_update = data.get("artwork_id") == artwork_id
        except (BadSignature, SignatureExpired):
            token_error = "Invalid or expired QR code."

    success = False
    error = None

    if request.method == "POST":
        if not can_update:
            error = "Not authorised."
        else:
            location = request.form.get("location", "").strip()
            note = request.form.get("note", "").strip()
            if not location:
                error = "Location required."
            else:
                db.session.add(
                    LocationLog(
                        artwork_id=artwork.id,
                        location=location,
                        note=note,
                    )
                )
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
# PDFs
# --------------------------------------------------


@app.route("/artworks/<int:artwork_id>/certificate")
def certificate_pdf(artwork_id):
    artwork = Artwork.query.get_or_404(artwork_id)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 40 * mm

    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(w / 2, y, "Certificate of Authenticity")
    y -= 20 * mm

    if artwork.image_filename:
        img_path = os.path.join(app.config["UPLOAD_FOLDER"], artwork.image_filename)
        if os.path.exists(img_path):
            img = ImageReader(img_path)
            iw, ih = img.getSize()
            scale = min((w - 60 * mm) / iw, 70 * mm / ih)
            dw, dh = iw * scale, ih * scale
            c.drawImage(img, (w - dw) / 2, y - dh, dw, dh)
            y -= dh + 10 * mm

    c.setFont("Helvetica", 11)
    for label, value in [
        ("Artist", ARTIST_NAME),
        ("Title", artwork.title),
        ("Year", artwork.year or "—"),
        ("Medium", artwork.medium),
        ("Dimensions", artwork.dimensions or "—"),
        ("Edition", artwork.edition_info or "Unique"),
    ]:
        c.drawString(30 * mm, y, f"{label}: {value}")
        y -= 12

    c.drawString(30 * mm, y - 10, f"Artwork ID: {artwork.id}")
    c.showPage()
    c.save()

    buf.seek(0)
    return send_file(buf, mimetype="application/pdf", as_attachment=False)


@app.route("/artworks/<int:artwork_id>/box-label")
def box_label_pdf(artwork_id):
    artwork = Artwork.query.get_or_404(artwork_id)

    base = os.environ.get("PUBLIC_BASE_URL", request.url_root.rstrip("/"))
    token = make_box_token(artwork.id)
    box_url = f"{base}{url_for('box_page', artwork_id=artwork.id, token=token)}"

    qr = qrcode.make(box_url)
    qr_buf = io.BytesIO()
    qr.save(qr_buf)
    qr_buf.seek(0)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawImage(ImageReader(qr_buf), 120 * mm, 160 * mm, 60 * mm, 60 * mm)
    c.drawString(30 * mm, 180 * mm, artwork.title)
    c.drawString(30 * mm, 170 * mm, f"ID: {artwork.id}")
    c.showPage()
    c.save()

    buf.seek(0)
    return send_file(buf, mimetype="application/pdf", as_attachment=False)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)

