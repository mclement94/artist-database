import base64
import io
import json
import os
import re
from datetime import datetime
from typing import Any, Dict

import qrcode
from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from markupsafe import escape as html_escape
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from werkzeug.utils import secure_filename

# ============================================================
# Environment (Render + Playwright)
# ============================================================
# IMPORTANT: keep this at the very top so Playwright (imported later) uses local browsers.
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

# ============================================================
# Paths / Storage (Render-friendly)
# ============================================================

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# If you attached a Render persistent disk at /var/data, this will persist across deploys.
DATA_DIR = os.environ.get("DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)

UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAIN_DB_PATH = os.path.join(DATA_DIR, "database.db")
CERT_DB_PATH = os.path.join(DATA_DIR, "certificate_templates.db")

ALLOWED_ARTWORK_EXTENSIONS = {"jpg", "jpeg", "png"}

ARTIST_NAME = os.environ.get("ARTIST_NAME", "Miet Warlop")

# ============================================================
# Flask + SQLAlchemy
# ============================================================

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-change-me"),
    SQLALCHEMY_DATABASE_URI="sqlite:///" + MAIN_DB_PATH,
    SQLALCHEMY_BINDS={"cert": "sqlite:///" + CERT_DB_PATH},
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    UPLOAD_FOLDER=UPLOAD_DIR,
)

db = SQLAlchemy(app)

# ============================================================
# Models (MAIN DB)
# ============================================================


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


# Keep legacy table (safe to keep; not used by Unlayer pipeline)
class CertificateTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    settings_json = db.Column(db.Text, nullable=False, default="{}")
    logo_filename = db.Column(db.String(255), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ============================================================
# Models (CERT DB) — Unlayer template storage
# ============================================================


class UnlayerCertificateTemplate(db.Model):
    __bind_key__ = "cert"
    __tablename__ = "unlayer_certificate_templates"

    id = db.Column(db.Integer, primary_key=True)
    design_json = db.Column(db.Text, nullable=True)  # JSON string
    html = db.Column(db.Text, nullable=True)  # exported HTML
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


with app.app_context():
    db.create_all()

# ============================================================
# Utils
# ============================================================


def allowed_ext(filename: str, allowed: set[str]) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed


def save_upload(file_storage) -> str:
    filename = secure_filename(file_storage.filename)
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    final = f"{ts}_{filename}"
    file_storage.save(os.path.join(app.config["UPLOAD_FOLDER"], final))
    return final


def public_base_url() -> str:
    # Used for QR links etc.
    return os.environ.get("PUBLIC_BASE_URL", request.url_root.rstrip("/"))


def current_location(artwork_id: int):
    return (
        LocationLog.query.filter_by(artwork_id=artwork_id)
        .order_by(LocationLog.changed_at.desc())
        .first()
    )


def serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="box-token")


def make_box_token(artwork_id: int) -> str:
    return serializer().dumps({"artwork_id": artwork_id})


def verify_box_token(token: str, max_age_seconds: int = 60 * 60 * 24 * 365 * 5):
    return serializer().loads(token, max_age=max_age_seconds)


def get_or_create_unlayer_template() -> UnlayerCertificateTemplate:
    tpl = UnlayerCertificateTemplate.query.get(1)
    if not tpl:
        tpl = UnlayerCertificateTemplate(id=1, design_json=None, html=None)
        db.session.add(tpl)
        db.session.commit()
    return tpl


def wrap_full_html(inner_html: str) -> str:
    # Print-safe + remove visible link styling / link artifacts in PDF viewers.
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Certificate</title>
  <style>
    @page {{ size: A4; margin: 20mm; }}
    html, body {{ margin:0; padding:0; }}
    * {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    img {{ max-width: 100%; height: auto; }}

    /* Never show "link look" in PDF */
    a {{
      color: inherit !important;
      text-decoration: none !important;
      pointer-events: none !important;
    }}
  </style>
</head>
<body>
{inner_html}
</body>
</html>"""


def _safe_text(v: Any) -> str:
    if v is None:
        return "—"
    s = str(v).strip()
    return str(html_escape(s)) if s else "—"


def artwork_image_data_uri(artwork: Artwork) -> str:
    """
    Returns a data: URI for the artwork image (so Playwright doesn't need to fetch anything).
    If no image, returns "".
    """
    if not artwork.image_filename:
        return ""

    path = os.path.join(app.config["UPLOAD_FOLDER"], artwork.image_filename)
    if not os.path.isfile(path):
        return ""

    ext = artwork.image_filename.rsplit(".", 1)[1].lower()
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"

    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")

    return f"data:{mime};base64,{b64}"


def strip_empty_image_tags(html: str) -> str:
    """
    Removes <img> tags with empty src to avoid broken icons in PDF.
    """
    if not html:
        return html
    return re.sub(
        r"<img\b[^>]*\bsrc=(['\"])\s*\1[^>]*>",
        "",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )


def merge_unlayer_html(template_html: str, artwork: Artwork) -> str:
    """
    Replaces placeholders in Unlayer exported HTML.
    Supported placeholder styles:
      - %%tag%%
      - [[tag]]
      - {{tag}}
      - encoded variants: &#91;&#91;tag&#93;&#93;  / &#123;&#123;tag&#125;&#125;
      - &lbrack;&lbrack;tag&rbrack;&rbrack; (sometimes seen)
    """
    img_uri = artwork_image_data_uri(artwork)

    values: Dict[str, str] = {
        "artist_name": _safe_text(ARTIST_NAME),
        "artwork_title": _safe_text(artwork.title),
        "year": _safe_text(artwork.year),
        "medium": _safe_text(artwork.medium),
        "dimensions": _safe_text(artwork.dimensions),
        "edition_info": _safe_text(artwork.edition_info or "Unique"),
        "artwork_id": _safe_text(artwork.id),
        "certificate_date": _safe_text(datetime.utcnow().strftime("%Y-%m-%d")),
        "artwork_image_url": str(html_escape(img_uri or "")),
        "signature_line": (
            '<span style="display:inline-block;'
            'border-bottom:1px solid #111;'
            'min-width:260px;height:1.2em;vertical-align:baseline;"></span>'
        ),
    }

    out = template_html or ""

    for key, val in values.items():
        patterns = [
            re.compile(r"%%\s*" + re.escape(key) + r"\s*%%"),
            re.compile(r"\[\[\s*" + re.escape(key) + r"\s*\]\]"),
            re.compile(r"\{\{\s*" + re.escape(key) + r"\s*\}\}"),
            re.compile(r"&#91;&#91;\s*" + re.escape(key) + r"\s*&#93;&#93;"),
            re.compile(r"&#123;&#123;\s*" + re.escape(key) + r"\s*&#125;&#125;"),
            re.compile(r"&lbrack;&lbrack;\s*" + re.escape(key) + r"\s*&rbrack;&rbrack;"),
        ]
        for pat in patterns:
            out = pat.sub(val, out)

    if not img_uri:
        out = strip_empty_image_tags(out)

    return out


def pdf_from_html_with_playwright(html: str) -> bytes:
    """
    Render HTML to PDF using Playwright WITHOUT navigating to your own URL.
    This avoids Render network timeouts and is much faster.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ]
        )
        page = browser.new_page()
        page.set_content(html, wait_until="load", timeout=60_000)
        page.wait_for_timeout(150)

        pdf_bytes = page.pdf(
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
        )
        browser.close()
        return pdf_bytes


# ============================================================
# Uploads
# ============================================================


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


# ============================================================
# Pages
# ============================================================


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/artworks")
def artwork_list():
    # Filter: ?status=for_sale | sold | (blank = all)
    status = (request.args.get("status") or "").strip().lower()

    q = Artwork.query.order_by(Artwork.created_at.desc())
    if status == "for_sale":
        q = q.filter(Artwork.for_sale.is_(True))
    elif status == "sold":
        q = q.filter(Artwork.for_sale.is_(False))

    artworks = q.all()
    return render_template("artwork_list.html", artworks=artworks, status=status)


@app.route("/artworks/<int:artwork_id>")
def artwork_detail(artwork_id):
    artwork = Artwork.query.get_or_404(artwork_id)
    latest = current_location(artwork.id)
    return render_template("artwork_detail.html", artwork=artwork, latest=latest)


# ============================================================
# Create / Edit / Delete artworks
# ============================================================


@app.route("/add-artwork", methods=["GET", "POST"])
def add_artwork():
    if request.method == "POST":
        image = request.files.get("image")
        image_filename = None

        if image and image.filename:
            if not allowed_ext(image.filename, ALLOWED_ARTWORK_EXTENSIONS):
                return "Only JPG/JPEG/PNG allowed", 400
            image_filename = save_upload(image)

        artwork = Artwork(
            title=request.form["title"],
            year=request.form.get("year"),
            series=request.form.get("series"),
            medium=request.form["medium"],
            dimensions=request.form.get("dimensions"),
            description=request.form.get("description"),
            edition_type=request.form.get("edition_type"),
            edition_info=request.form.get("edition_info"),
            for_sale=(request.form.get("for_sale") == "yes"),
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
        artwork.for_sale = (request.form.get("for_sale") == "yes")
        artwork.price = request.form.get("price")
        artwork.notes = request.form.get("notes")

        image = request.files.get("image")
        if image and image.filename:
            if not allowed_ext(image.filename, ALLOWED_ARTWORK_EXTENSIONS):
                return "Only JPG/JPEG/PNG allowed", 400
            artwork.image_filename = save_upload(image)

        db.session.commit()
        return redirect(url_for("artwork_detail", artwork_id=artwork.id))

    return render_template("edit_artwork.html", artwork=artwork)


@app.route("/artworks/<int:artwork_id>/delete", methods=["POST"])
def delete_artwork(artwork_id):
    artwork = Artwork.query.get_or_404(artwork_id)

    # delete related logs first (no cascade configured)
    LocationLog.query.filter_by(artwork_id=artwork.id).delete()

    # delete uploaded image file (optional)
    if artwork.image_filename:
        try:
            path = os.path.join(app.config["UPLOAD_FOLDER"], artwork.image_filename)
            if os.path.isfile(path):
                os.remove(path)
        except Exception:
            app.logger.exception("Failed to delete image file for artwork %s", artwork.id)

    db.session.delete(artwork)
    db.session.commit()
    return redirect(url_for("artwork_list"))


# ============================================================
# Certificate Designer (Unlayer)
# ============================================================


@app.route("/certificate-designer")
def certificate_designer():
    sample = Artwork.query.order_by(Artwork.created_at.desc()).first()
    return render_template(
        "certificate_designer.html",
        sample=sample,
        unlayer_project_id=os.environ.get("UNLAYER_PROJECT_ID"),
    )


@app.route("/api/certificate-template", methods=["GET"])
def api_certificate_template_get():
    tpl = get_or_create_unlayer_template()
    design = None
    if tpl.design_json:
        try:
            design = json.loads(tpl.design_json)
        except Exception:
            design = None

    return jsonify(
        {
            "id": tpl.id,
            "design_json": design,
            "updated_at": tpl.updated_at.isoformat() if tpl.updated_at else None,
        }
    )


@app.route("/api/certificate-template", methods=["POST"])
def api_certificate_template_save():
    tpl = get_or_create_unlayer_template()
    payload = request.get_json(force=True, silent=False)

    if not payload or "design_json" not in payload or "html" not in payload:
        return "Missing design_json/html", 400

    tpl.design_json = json.dumps(payload["design_json"])
    tpl.html = payload["html"]
    db.session.commit()
    return jsonify({"ok": True})


# ============================================================
# Certificate render (HTML) + PDF (Playwright)
# ============================================================


@app.route("/artworks/<int:artwork_id>/certificate-render")
def certificate_render(artwork_id):
    artwork = Artwork.query.get_or_404(artwork_id)
    tpl = get_or_create_unlayer_template()

    if not tpl.html:
        return Response(
            "No Unlayer template saved yet. Go to /certificate-designer and click Save template.",
            status=400,
        )

    merged = merge_unlayer_html(tpl.html, artwork)
    return Response(wrap_full_html(merged), mimetype="text/html")


# One real URL, plus one alias endpoint (different URL) so templates can call either name.
@app.route("/artworks/<int:artwork_id>/certificate", endpoint="certificate_pdf")
@app.route("/artworks/<int:artwork_id>/certificate-print", endpoint="certificate_print")
def certificate_pdf(artwork_id):
    """
    Fast + reliable:
    - generate merged HTML directly
    - render with page.set_content (no network)
    - embed image as base64
    """
    artwork = Artwork.query.get_or_404(artwork_id)
    tpl = get_or_create_unlayer_template()

    if not tpl.html:
        return Response(
            "No Unlayer template saved yet. Go to /certificate-designer and click Save template.",
            status=400,
        )

    try:
        merged = merge_unlayer_html(tpl.html, artwork)
        full_html = wrap_full_html(merged)
        pdf_bytes = pdf_from_html_with_playwright(full_html)
    except Exception as e:
        app.logger.exception("Certificate PDF generation failed")
        return Response(
            "Certificate PDF generation failed.\n\n"
            "Check Render logs for the full traceback.\n\n"
            f"Error: {html_escape(str(e))}\n",
            mimetype="text/plain",
            status=500,
        )

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"certificate_{artwork_id}.pdf",
    )


# ============================================================
# Box page (location logging)
# ============================================================


@app.route("/artworks/<int:artwork_id>/box", methods=["GET", "POST"])
def box_page(artwork_id):
    artwork = Artwork.query.get_or_404(artwork_id)

    token = request.args.get("token", "")
    can_update = False
    token_error = None

    if token:
        try:
            data = verify_box_token(token)
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


# ============================================================
# Box label PDF (QR)
# ============================================================


@app.route("/artworks/<int:artwork_id>/box-label")
def box_label_pdf(artwork_id):
    artwork = Artwork.query.get_or_404(artwork_id)

    base = public_base_url()
    token = make_box_token(artwork.id)
    box_url = f"{base}{url_for('box_page', artwork_id=artwork.id, token=token)}"

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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
