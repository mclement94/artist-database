# artistdb/routes/artworks.py
"""
Artwork CRUD + listing.
Kept clean by calling services for uploads and "box" location.
"""

import os
from flask import Blueprint, current_app, redirect, render_template, request, send_from_directory, url_for

from ..extensions import db
from ..models import Artwork, LocationLog
from ..services.storage import allowed_ext, save_upload
from ..services.box import current_location

bp = Blueprint("artworks", __name__)

@bp.route("/uploads/<path:filename>")
def uploaded_file(filename):
    """Serve uploaded images."""
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)


@bp.route("/artworks")
def artwork_list():
    """List artworks with optional status filter (for_sale / sold)."""
    status = (request.args.get("status") or "").strip().lower()

    q = Artwork.query.order_by(Artwork.created_at.desc())
    if status == "for_sale":
        q = q.filter(Artwork.for_sale.is_(True))
    elif status == "sold":
        q = q.filter(Artwork.for_sale.is_(False))

    artworks = q.all()
    return render_template("artwork_list.html", artworks=artworks, status=status)


@bp.route("/artworks/<int:artwork_id>")
def artwork_detail(artwork_id):
    artwork = Artwork.query.get_or_404(artwork_id)
    latest = current_location(artwork.id)
    return render_template("artwork_detail.html", artwork=artwork, latest=latest)


@bp.route("/add-artwork", methods=["GET", "POST"])
def add_artwork():
    if request.method == "POST":
        image = request.files.get("image")
        image_filename = None

        if image and image.filename:
            allowed = current_app.config["ALLOWED_ARTWORK_EXTENSIONS"]
            if not allowed_ext(image.filename, allowed):
                return "Only JPG/JPEG/PNG allowed", 400
            image_filename = save_upload(image, current_app.config["UPLOAD_FOLDER"])

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
        return redirect(url_for("artworks.artwork_detail", artwork_id=artwork.id))

    return render_template("add_artwork.html")


@bp.route("/artworks/<int:artwork_id>/edit", methods=["GET", "POST"])
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
            allowed = current_app.config["ALLOWED_ARTWORK_EXTENSIONS"]
            if not allowed_ext(image.filename, allowed):
                return "Only JPG/JPEG/PNG allowed", 400
            artwork.image_filename = save_upload(image, current_app.config["UPLOAD_FOLDER"])

        db.session.commit()
        return redirect(url_for("artworks.artwork_detail", artwork_id=artwork.id))

    return render_template("edit_artwork.html", artwork=artwork)


@bp.route("/artworks/<int:artwork_id>/delete", methods=["POST"])
def delete_artwork(artwork_id):
    artwork = Artwork.query.get_or_404(artwork_id)

    # delete related logs first (no cascade configured)
    LocationLog.query.filter_by(artwork_id=artwork.id).delete()

    # delete uploaded image file (optional)
    if artwork.image_filename:
        try:
            path = os.path.join(current_app.config["UPLOAD_FOLDER"], artwork.image_filename)
            if os.path.isfile(path):
                os.remove(path)
        except Exception:
            current_app.logger.exception("Failed to delete image file for artwork %s", artwork.id)

    db.session.delete(artwork)
    db.session.commit()
    return redirect(url_for("artworks.artwork_list"))
