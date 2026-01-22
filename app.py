import io
import json
import os
import re
from datetime import datetime

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
from reportlab.pdfgen import canvas as pdf_canvas
from werkzeug.utils import secure_filename

# ============================================================
# Hard requirements for Render + Playwright
# ============================================================
# IMPORTANT: must be set before importing Playwright anywhere.
# We keep it here so gunicorn workers always inherit it.
# This makes Playwright use its bundled .local-browsers folder (what your build downloaded).
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

# ============================================================
# Paths / Config
# ============================================================
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# If you attached a Render persistent disk, mount it at /var/data (Render convention).
# You can also override with DATA_DIR env var.
DATA_DIR = os.environ.get("DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)

UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAIN_DB_PATH = os.path.join(DATA_DIR, "database.db")  # existing DB
CERT_DB_PATH = os.path.join(DATA_DIR, "certificate_templates.db")  # Unlayer template DB

ALLOWED_ARTWORK_EXTENSIONS = {"jpg", "jpeg", "png"}

# Set your real artist name here
ARTIST_NAME = os.environ.get("ARTIST_NAME", "Miet Warlop")


def public_base_url() -> str:
    """
    Production: set PUBLIC_BASE_URL to your Render URL (no trailing slash), e.g.
      PUBLIC_BASE_URL=https://artist-database.onrender.com

    Local dev: falls back to request.url_root
    """
    return os.environ.get("PUBLIC_BASE_URL", request.url_root.rstrip("/"))


# ============================================================
# Flask + SQLAlchemy (binds)
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


# Legacy table (keeps old DB compatible; not used by the new certificate pipeline)
class CertificateTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    settings_json = db.Column(db.Text, nullable=False, default="{}")
    logo_filename = db.Column(db.String(255), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ============================================================
# Model (CERT DB) — Unlayer template storage
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
# Helpers
# ============================================================


def allowed_ext(filename: str, allowed: set[str]) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed


def save_upload(file_storage) -> str:
    filename = secure_filename(file_storage.filename)
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    final = f"{ts}_{filename}"
    file_storage.save(os.path.join(app.config["UPLOAD_FOLDER"], final))
    return final


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
    """
    Print-safe wrapper:
    - No link styling (prevents blue underlines / "broken link" look)
    - Stable fonts (avoid remote font loads that can hang PDF render)
    """
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Certificate</title>
  <style>
    @page {{ size: A4; margin: 20mm; }}
    html, body {{ margin:0; padding:0; }}
    * {{
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
      font-family: Helvetica, Arial, sans-serif;
    }}
    img {{ max-width: 100%; height: auto; }}

    /* Museum-grade: never show "link look" in PDF */
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


def strip_artwork_image_module_from_unlayer_html(template_html: str) -> str:
    """
    If an artwork has NO image, remove any Unlayer image block that still contains
    the artwork image placeholder. This prevents broken-image icons in the PDF.
    """
    if not template_html:
        return template_html

    token_pattern = r"(%%\s*artwork_image_url\s*%%|\[\[\s*artwork_image_url\s*\]\])"

    html = template_html

    # Remove img tags that still contain the token
    html = re.sub(
        rf"<img\b[^>]*\bsrc=(['\"]).*?{token_pattern}.*?\1[^>]*>",
        "",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Remove enclosing tables containing the token anywhere inside (Unlayer uses lots of tables)
    html = re.sub(
        rf"<table\b[^>]*>.*?{token_pattern}.*?</table>",
        "",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    html = re.sub(r"\n{3,}", "\n\n", html)
    return html


def merge_unlayer_html(template_html: str, artwork: Artwork, artwork_image_url: str) -> str:
    """
    Supported placeholders in saved Unlayer HTML:
      %%tag%%  (preferred)
      [[tag]]  (allowed)
      {{tag}} + HTML-escaped variants (legacy compatibility)
    """
    def safe(v):
        if v is None:
            return "—"
        s = str(v).strip()
        return str(html_escape(s)) if s else "—"

    values = {
        "artist_name": safe(ARTIST_NAME),
        "artwork_title": safe(artwork.title),
        "year": safe(artwork.year),
        "medium": safe(artwork.medium),
        "dimensions": safe(artwork.dimensions),
        "edition_info": safe(artwork.edition_info or "Unique"),
        "artwork_id": safe(artwork.id),
        "certificate_date": safe(datetime.utcnow().strftime("%Y-%m-%d")),
        "artwork_image_url": str(html_escape(artwork_image_url or "")),
        "signature_line": (
            '<span style="display:inline-block;'
            'border-bottom:1px solid #111;'
            'min-width:260px;height:1.2em;vertical-align:baseline;"></span>'
        ),
    }

    html = template_html or ""

    for key, val in values.items():
        patterns = [
            re.compile(r"%%\s*" + re.escape(key) + r"\s*%%"),
            re.compile(r"\[\[\s*" + re.escape(key) + r"\s*\]\]"),
            re.compile(r"\{\{\s*" + re.escape(key) + r"\s*\}\}"),
            re.compile(r"&#91;&#91;\s*" + re.escape(key) + r"\s*&#93;&#93;"),
            re.compile(r"&#123;&#123;\s*" + re.escape(key) + r"\s*&#125;&#125;"),
        ]
        for pat in patterns:
            html = pat.sub(val, html)

    return html


def pdf_from_url_with_playwright(url: str) -> bytes:
    """
    Render HTML route to PDF using Playwright, optimized for Render stability.

    Key choices:
    - wait_until="domcontentloaded" (more reliable than load/networkidle on hosted envs)
    - longer timeout
    - block slow/irrelevant resources (fonts, media) to avoid hanging
    """
    from playwright.sync_api import sync_playwright

    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

    def should_allow_request(req_url: str) -> bool:
        # Allow same-origin requests; block most external to reduce hang risk.
        if not base:
            return True
        return req_url.startswith(base) or req_url.startswith("data:") or req_url.startswith("blob:")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ]
        )
        page = browser.new_page()

        # Intercept requests to prevent remote font/CDN hangs
        def route_handler(route, req):
            rtype = req.resource_type
            req_url = req.url

            # Block heavy/irrelevant types and most external traffic
            if rtype in ("font", "media"):
                return route.abort()

            if not should_allow_request(req_url):
                # Allow images if you really need them from external sources.
                # But for stability, block external by default.
                return route.abort()

            return route.continue_()

        page.route("**/*", route_handler)

        # Fast reliable navigation
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)

        # Ensure content exists
        page.wait_for_selector("body", timeout=30_000)

        # Settle layout
        page.wait_for_timeout(800)

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


@app.route("/favicon.ico")
def favicon():
    return Response(status=204)


# ============================================================
# Pages
# ============================================================


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


# ============================================================
# Create / Edit artworks
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

    base = public_base_url()

    artwork_image_url = ""
    if artwork.image_filename:
        # IMPORTANT: absolute URL so headless chrome can fetch it
        artwork_image_url = f"{base}{url_for('uploaded_file', filename=artwork.image_filename)}"

    template_html = tpl.html
    if not artwork_image_url:
        template_html = strip_artwork_image_module_from_unlayer_html(template_html)

    merged = merge_unlayer_html(template_html, artwork, artwork_image_url)
    return Response(wrap_full_html(merged), mimetype="text/html")


@app.route("/artworks/<int:artwork_id>/certificate")
def certificate_pdf(artwork_id):
    render_url = public_base_url() + url_for("certificate_render", artwork_id=artwork_id)

    try:
        pdf_bytes = pdf_from_url_with_playwright(render_url)
    except Exception as e:
        app.logger.exception("Certificate PDF generation failed")
        return Response(
            "Certificate PDF generation failed.\n\n"
            f"Render URL: {render_url}\n\n"
            "Checklist:\n"
            "- Render env var: PUBLIC_BASE_URL=https://artist-database.onrender.com\n"
            "- Start command: gunicorn app:app --timeout 180 --workers 1\n"
            "- Build command: pip install -r requirements.txt && python -m playwright install chromium\n\n"
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
            can_update = data.get("artwork_id") == artwork_id
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
    c = pdf_canvas.Canvas(buf, pagesize=A4)

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
    # Local dev only. On Render, use gunicorn start command.
    app.run(host="127.0.0.1", port=5000, debug=True)
