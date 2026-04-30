import io
import math
import os
from typing import Iterable

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from ..models import Artwork

PAGE_MARGIN = 20 * mm
PAGE_WIDTH, PAGE_HEIGHT = A4
IMAGE_GUTTER = 8 * mm


def _safe_text(value) -> str:
    if value is None:
        return "—"
    text = str(value).strip()
    return text if text else "—"


def _load_image(path: str):
    if not os.path.isfile(path):
        return None
    try:
        return ImageReader(path)
    except Exception:
        return None


def _fit_image_box(img, max_width: float, max_height: float) -> tuple[float, float]:
    width, height = img.getSize()
    if width <= 0 or height <= 0:
        return max_width, max_height

    scale = min(max_width / width, max_height / height)
    return width * scale, height * scale


def generate_multi_artwork_pdf(
    artworks: Iterable[Artwork], *, artist_name: str, upload_folder: str
) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)

    for artwork in artworks:
        pdf.setFont("Helvetica-Bold", 20)
        pdf.drawString(PAGE_MARGIN, PAGE_HEIGHT - PAGE_MARGIN - 4 * mm, artwork.title or "Untitled")

        pdf.setFont("Helvetica", 10)
        meta_left = PAGE_MARGIN
        meta_top = PAGE_HEIGHT - PAGE_MARGIN - 12 * mm
        lines = [
            f"Artist: {artist_name}",
            f"Year: {_safe_text(artwork.year)}",
            f"Medium: {_safe_text(artwork.medium)}",
            f"Dimensions: {_safe_text(artwork.dimensions)}",
            f"Series / Project: {_safe_text(artwork.series)}",
            f"Edition: {_safe_text(artwork.edition_type)} {_safe_text(artwork.edition_info)}",
            f"Status: {_safe_text(artwork.status)}",
            f"Price: {_safe_text(artwork.price)}",
        ]

        for line in lines:
            pdf.drawString(meta_left, meta_top, line)
            meta_top -= 5.5 * mm

        info_text = _safe_text(artwork.description or artwork.notes)
        if info_text != "—":
            pdf.drawString(meta_left, meta_top - 2 * mm, "Notes:")
            text_obj = pdf.beginText(meta_left, meta_top - 8 * mm)
            text_obj.setFont("Helvetica", 9)
            text_width = PAGE_WIDTH - 2 * PAGE_MARGIN
            for chunk in info_text.splitlines():
                if text_obj.getY() < PAGE_MARGIN + 30 * mm:
                    break
                text_obj.textLine(chunk[:120])
            pdf.drawText(text_obj)

        images = artwork.images or []
        image_area_top = PAGE_HEIGHT - PAGE_MARGIN - 60 * mm
        image_area_height = image_area_top - PAGE_MARGIN
        image_area_width = PAGE_WIDTH - 2 * PAGE_MARGIN

        if images:
            image_readers = []
            for filename in images:
                image_path = os.path.join(upload_folder, filename)
                image_reader = _load_image(image_path)
                if image_reader:
                    image_readers.append((filename, image_reader))

            if image_readers:
                cols = min(3, max(1, len(image_readers)))
                rows = math.ceil(len(image_readers) / cols)
                cell_width = (image_area_width - IMAGE_GUTTER * (cols - 1)) / cols
                cell_height = (image_area_height - IMAGE_GUTTER * (rows - 1)) / rows

                for index, (_, image_reader) in enumerate(image_readers):
                    col = index % cols
                    row = index // cols
                    x = PAGE_MARGIN + col * (cell_width + IMAGE_GUTTER)
                    y = image_area_top - (row + 1) * cell_height - row * IMAGE_GUTTER

                    img_w, img_h = _fit_image_box(image_reader, cell_width, cell_height)
                    x_offset = x + (cell_width - img_w) / 2
                    y_offset = y + (cell_height - img_h) / 2
                    pdf.drawImage(
                        image_reader,
                        x_offset,
                        y_offset,
                        width=img_w,
                        height=img_h,
                        preserveAspectRatio=True,
                        anchor="sw",
                        mask="auto",
                    )
            else:
                pdf.setFont("Helvetica-Oblique", 12)
                pdf.drawString(PAGE_MARGIN, image_area_top - 12 * mm, "Artwork images are missing or cannot be loaded.")
        else:
            pdf.setFont("Helvetica-Oblique", 12)
            pdf.drawString(PAGE_MARGIN, image_area_top - 12 * mm, "No artwork images available.")

        pdf.showPage()

    pdf.save()
    buffer.seek(0)
    return buffer.read()
