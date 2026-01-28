# artistdb/routes/artworks.py
"""
Artwork CRUD + listing.
Kept clean by calling services for uploads and "box" location.
"""

import os
from flask import Blueprint, current_app, jsonify, redirect, render_template, request, send_from_directory, url_for

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
    """List artworks with optional status filter (working / for_sale / sold)."""
    status = (request.args.get("status") or "").strip().lower()

    q = Artwork.query.order_by(Artwork.created_at.desc())
    if status in ["working", "for_sale", "sold"]:
        q = q.filter(Artwork.status == status)

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
            status=request.form.get("status", "working"),
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
        artwork.status = request.form.get("status", "working")
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


@bp.route("/artworks/bulk-update", methods=["POST"])
def bulk_update_artworks():
    """
    Bulk update multiple artworks with the same setting.
    Expects JSON: { artwork_ids: [1, 2, 3], field: "status", value: "for_sale" }
    For sold artworks: only colorcode can be changed.
    """
    data = request.get_json()
    
    if not data or not data.get("artwork_ids") or not data.get("field") or data.get("value") is None:
        return jsonify({"error": "Missing artwork_ids, field, or value"}), 400
    
    artwork_ids = data["artwork_ids"]
    field = data["field"]
    value = data["value"]
    
    # Whitelist of allowed fields to update (prevent injection)
    allowed_fields = ["status", "for_sale", "price", "notes", "series", "year"]
    
    if field not in allowed_fields:
        return jsonify({"error": f"Field '{field}' is not allowed for bulk update"}), 400
    
    try:
        artworks = Artwork.query.filter(Artwork.id.in_(artwork_ids)).all()
        
        # Filter out sold artworks - they cannot be edited
        sold_ids = [a.id for a in artworks if a.status == "sold"]
        editable_artworks = [a for a in artworks if a.status != "sold"]
        
        for artwork in editable_artworks:
            if field == "for_sale":
                # Convert string to boolean
                setattr(artwork, field, value in [True, "true", "True", "yes", "1"])
            else:
                setattr(artwork, field, value)
        
        db.session.commit()
        
        message = f"Updated {len(editable_artworks)} artwork(s)"
        if sold_ids:
            message += f". {len(sold_ids)} sold artwork(s) were skipped (cannot edit sold items)."
        
        return jsonify({"success": True, "message": message}), 200
    
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
