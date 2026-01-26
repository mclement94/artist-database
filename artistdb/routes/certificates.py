# artistdb/routes/certificates.py
"""
Certificate routes:
- Unlayer designer page
- API to save/load template
- Render merged HTML
- Generate PDF
"""

import io
import json

from flask import Blueprint, Response, current_app, jsonify, render_template, request, send_file
from markupsafe import escape as html_escape

from ..extensions import db
from ..models import Artwork
from ..services.certificates import (
    get_or_create_unlayer_template,
    merge_unlayer_html,
    pdf_from_html_with_playwright,
    template_json_for_editor,
    wrap_full_html,
)

bp = Blueprint("certificates", __name__)

@bp.route("/certificate-designer")
def certificate_designer():
    sample = Artwork.query.order_by(Artwork.created_at.desc()).first()
    return render_template(
        "certificate_designer.html",
        sample=sample,
        unlayer_project_id=current_app.config.get("UNLAYER_PROJECT_ID") or None,
    )


@bp.route("/api/certificate-template", methods=["GET"])
def api_certificate_template_get():
    tpl = get_or_create_unlayer_template()
    design = template_json_for_editor(tpl)

    return jsonify(
        {
            "id": tpl.id,
            "design_json": design,
            "updated_at": tpl.updated_at.isoformat() if tpl.updated_at else None,
        }
    )


@bp.route("/api/certificate-template", methods=["POST"])
def api_certificate_template_save():
    tpl = get_or_create_unlayer_template()
    payload = request.get_json(force=True, silent=False)

    if not payload or "design_json" not in payload or "html" not in payload:
        return "Missing design_json/html", 400

    tpl.design_json = json.dumps(payload["design_json"])
    tpl.html = payload["html"]
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/artworks/<int:artwork_id>/certificate-render")
def certificate_render(artwork_id):
    artwork = Artwork.query.get_or_404(artwork_id)
    tpl = get_or_create_unlayer_template()

    if not tpl.html:
        return Response(
            "No Unlayer template saved yet. Go to /certificate-designer and click Save template.",
            status=400,
        )

    merged = merge_unlayer_html(
        tpl.html,
        artwork,
        artist_name=current_app.config["ARTIST_NAME"],
        upload_folder=current_app.config["UPLOAD_FOLDER"],
    )
    return Response(wrap_full_html(merged), mimetype="text/html")


# Keep both URLs (same as your original)
@bp.route("/artworks/<int:artwork_id>/certificate")
@bp.route("/artworks/<int:artwork_id>/certificate-print")
def certificate_pdf(artwork_id):
    artwork = Artwork.query.get_or_404(artwork_id)
    tpl = get_or_create_unlayer_template()

    if not tpl.html:
        return Response(
            "No Unlayer template saved yet. Go to /certificate-designer and click Save template.",
            status=400,
        )

    try:
        merged = merge_unlayer_html(
            tpl.html,
            artwork,
            artist_name=current_app.config["ARTIST_NAME"],
            upload_folder=current_app.config["UPLOAD_FOLDER"],
        )
        full_html = wrap_full_html(merged)
        pdf_bytes = pdf_from_html_with_playwright(full_html)
    except Exception as e:
        current_app.logger.exception("Certificate PDF generation failed")
        return Response(
            "Certificate PDF generation failed.\n\n"
            "Check logs for the full traceback.\n\n"
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
