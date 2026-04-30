# artistdb/services/certificates.py
"""
Certificate logic:
- storing & loading Unlayer templates
- merging merge-tags into HTML
- rendering PDF using Playwright (no network)

This is the "hard" part of your app, so it belongs in services/.
"""

import base64
import io
import json
import os
import re
from datetime import datetime
from typing import Any, Dict

from markupsafe import escape as html_escape
from playwright.sync_api import sync_playwright

from ..extensions import db
from ..models import Artwork, UnlayerCertificateTemplate


def get_or_create_unlayer_template() -> UnlayerCertificateTemplate:
    """
    Your app uses a single template row with id=1.
    If it doesn't exist yet, create it.
    """
    tpl = UnlayerCertificateTemplate.query.get(1)
    if not tpl:
        tpl = UnlayerCertificateTemplate(id=1, design_json=None, html=None)
        db.session.add(tpl)
        db.session.commit()
    return tpl


def wrap_full_html(inner_html: str) -> str:
    """
    Wrap Unlayer's HTML inside a full document with print-friendly settings.
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
    """Convert value to safe HTML text, defaulting to a dash."""
    if v is None:
        return "—"
    s = str(v).strip()
    return str(html_escape(s)) if s else "—"


def artwork_image_data_uri(artwork: Artwork, upload_folder: str) -> str:
    """
    Returns a data: URI for the artwork image.
    Why? Because Playwright can render HTML without fetching external URLs.
    """
    filename = artwork.certificate_image_filename or artwork.image_filename
    if not filename and hasattr(artwork, "images"):
        images = artwork.images
        filename = images[0] if images else None

    if not filename:
        return ""

    path = os.path.join(upload_folder, filename)
    if not os.path.isfile(path):
        return ""

    ext = filename.rsplit(".", 1)[1].lower()
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"

    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")

    return f"data:{mime};base64,{b64}"


def strip_empty_image_tags(html: str) -> str:
    """Remove <img src=""> tags to avoid broken image icons in PDFs."""
    if not html:
        return html
    return re.sub(
        r"<img\b[^>]*\bsrc=(['\"])\s*\1[^>]*>",
        "",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )


def merge_unlayer_html(template_html: str, artwork: Artwork, *, artist_name: str, upload_folder: str) -> str:
    """
    Replace placeholders in Unlayer exported HTML.

    Supported placeholder styles:
      - %%tag%%
      - [[tag]]
      - {{tag}}
      - encoded variants (Unlayer sometimes converts brackets)
    """
    img_uri = artwork_image_data_uri(artwork, upload_folder)

    values: Dict[str, str] = {
        "artist_name": _safe_text(artist_name),
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


def render_multiple_artworks_html(artworks: list[Artwork], *, artist_name: str, upload_folder: str) -> str:
    """
    Render a multi-artwork info document for PDF generation.
    """
    cards = []
    for artwork in artworks:
        image_uri = artwork_image_data_uri(artwork, upload_folder)
        image_html = (
            f'<img src="{image_uri}" alt="{html_escape(artwork.title or "Artwork image")}">'
            if image_uri
            else '<div class="no-image">No image</div>'
        )
        cards.append(f"""
        <section class="artwork-card">
          <div class="headline">
            <div>
              <h1>{html_escape(artwork.title or '')}</h1>
              <div class="subtitle">ID {html_escape(artwork.id)} · {html_escape(artwork.year or '—')} · {html_escape(artwork.medium or '—')}</div>
            </div>
            <div class="meta">{html_escape(artist_name)}</div>
          </div>

          <div class="body">
            <div class="image-frame">
              {image_html}
            </div>
            <div class="details">
              <p><strong>Series / Project:</strong> {html_escape(artwork.series or '—')}</p>
              <p><strong>Dimensions:</strong> {html_escape(artwork.dimensions or '—')}</p>
              <p><strong>Edition:</strong> {html_escape(artwork.edition_type or '—')} {html_escape(artwork.edition_info or '')}</p>
              <p><strong>Status:</strong> {html_escape(artwork.status or '—')}</p>
              <p><strong>Price:</strong> {html_escape(artwork.price or '—')}</p>
              <p><strong>Notes:</strong> {html_escape(artwork.notes or '—')}</p>
            </div>
          </div>
        </section>
        """)

    header = f"""
    <header class="document-header">
      <h1>Artwork selection</h1>
      <div class="document-meta">{html_escape(artist_name)} · {html_escape(datetime.utcnow().strftime('%Y-%m-%d'))}</div>
    </header>
    """

    return f"""
    <div class="multi-artwork-sheet">
      {header}
      {''.join(cards)}
    </div>
    <style>
      body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #111; line-height: 1.5; padding: 0; margin: 0; }}
      .document-header {{ padding-bottom: 20px; border-bottom: 1px solid #ddd; margin-bottom: 20px; }}
      .document-header h1 {{ margin: 0 0 4px; font-size: 28px; }}
      .document-meta {{ color: #555; font-size: 14px; }}
      .artwork-card {{ page-break-inside: avoid; margin-bottom: 32px; }}
      .headline {{ display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; margin-bottom: 18px; }}
      .headline h1 {{ margin: 0; font-size: 22px; }}
      .subtitle {{ color: #555; font-size: 14px; margin-top: 6px; }}
      .body {{ display: grid; grid-template-columns: 270px 1fr; gap: 18px; align-items: start; }}
      .image-frame {{ min-height: 200px; border: 1px solid #ddd; border-radius: 12px; padding: 10px; display: flex; align-items: center; justify-content: center; background: #fafafa; }}
      .image-frame img {{ max-width: 100%; max-height: 100%; border-radius: 10px; }}
      .no-image {{ color: #777; font-size: 14px; }}
      .details p {{ margin: 0 0 10px; font-size: 14px; }}
      .details p strong {{ color: #111; }}
      .artwork-card + .artwork-card {{ padding-top: 0; }}
    </style>
    """


def pdf_from_html_with_playwright(html: str) -> bytes:
    """
    Render HTML to PDF using Playwright WITHOUT navigating to your own URL.
    This avoids Render network timeouts and is fast.
    """
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


def template_json_for_editor(tpl: UnlayerCertificateTemplate):
    """Return template JSON (dict) for the editor endpoint."""
    if not tpl.design_json:
        return None
    try:
        return json.loads(tpl.design_json)
    except Exception:
        return None
