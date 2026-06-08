"""
RootBridge Research Citation Generator.

POST /api/citation/generate
  Body: { record: {...search result dict...} }
  Returns: PDF file (application/pdf)

Generates a printable source citation card for any record returned by the
search cascade. Each card includes a RootBridge Record ID, timestamp, and
a properly-formatted genealogical citation the researcher can attach to
their family history file.
"""

import hashlib
import io
from datetime import datetime, timezone

from flask import Blueprint, request, send_file, jsonify, g
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.utils import simpleSplit

from .auth import require_auth

citation_bp = Blueprint('citation', __name__)

# ── Colour palette (heritage dark — serious / archival feel) ─────────────────
PAL = {
    'bg':        colors.HexColor('#0f0d0a'),
    'card':      colors.HexColor('#1c1610'),
    'border':    colors.HexColor('#8b6914'),
    'accent':    colors.HexColor('#c9922a'),
    'heading':   colors.HexColor('#e8dcc8'),
    'body':      colors.HexColor('#c8b89a'),
    'muted':     colors.HexColor('#7a6a52'),
    'badge_bg':  colors.HexColor('#2a1f0e'),
    'white':     colors.HexColor('#f1ede6'),
    'green':     colors.HexColor('#4ade80'),
    'red':       colors.HexColor('#f87171'),
}

# ── Source metadata: display name, badge colour, citation template ───────────
SOURCE_META = {
    'ssdi': {
        'label':    'U.S. Social Security Death Index',
        'badge':    colors.HexColor('#1e3a5f'),
        'badge_txt':colors.HexColor('#7eb8f7'),
        'tag':      'SSDI',
        'cite':     'U.S. Social Security Death Index, 1935–2014. Social Security Administration. Accessed via RootBridge Vault.',
    },
    'ship_manifest': {
        'label':    'Historic Ship Passenger List',
        'badge':    colors.HexColor('#1a3320'),
        'badge_txt':colors.HexColor('#4ade80'),
        'tag':      'SHIP',
        'cite':     'Strassburger, Ralph B., and William J. Hinke. Pennsylvania German Pioneers (1934). '
                    'Original passenger lists, Philadelphia Port of Entry.',
    },
    'ship_manifest_db': {
        'label':    'Pennsylvania German Passenger List (1727–1808)',
        'badge':    colors.HexColor('#1a3320'),
        'badge_txt':colors.HexColor('#4ade80'),
        'tag':      'SHIP',
        'cite':     'Rupp, Israel Daniel. A Collection of Upwards of Thirty Thousand Names of German, '
                    'Swiss, Dutch, French, and Other Immigrants in Pennsylvania (1876). '
                    'Indexed by RootBridge from Strassburger-Hinke Pennsylvania German Pioneers (1934).',
    },
    'ged_vault': {
        'label':    'Private GEDCOM Submission',
        'badge':    colors.HexColor('#2d1a3a'),
        'badge_txt':colors.HexColor('#c084fc'),
        'tag':      'GED',
        'cite':     'Private family history submission. GEDCOM file indexed by RootBridge Vault. '
                    'Verify independently against primary sources.',
    },
    'rootbridge_vault': {
        'label':    'RootBridge Genealogy Vault',
        'badge':    colors.HexColor('#1a2a3a'),
        'badge_txt':colors.HexColor('#60a5fa'),
        'tag':      'VAULT',
        'cite':     'RootBridge Genealogy Vault. Compiled from WikiTree community data and '
                    'digitized public-domain genealogy records.',
    },
    'ellis_island': {
        'label':    'Ellis Island Passenger Record',
        'badge':    colors.HexColor('#1e3a5f'),
        'badge_txt':colors.HexColor('#7eb8f7'),
        'tag':      'ELLIS',
        'cite':     'Ellis Island Foundation. Passenger Search Database, 1892–1957. '
                    'Original manifests held by National Archives (NARA).',
    },
    'castle_garden': {
        'label':    'Castle Garden Immigrant Record',
        'badge':    colors.HexColor('#1e3a5f'),
        'badge_txt':colors.HexColor('#7eb8f7'),
        'tag':      'CASTLE',
        'cite':     'Battery Conservancy. Castle Garden Immigration Database, 1820–1892. '
                    'Original manifests held by National Archives (NARA).',
    },
    'revwar_pension': {
        'label':    'Revolutionary War Pension Record',
        'badge':    colors.HexColor('#3a1a1a'),
        'badge_txt':colors.HexColor('#f87171'),
        'tag':      'REVWAR',
        'cite':     'U.S. National Archives and Records Administration (NARA). '
                    'Revolutionary War Pension and Bounty-Land Warrant Application Files, 1800–1900.',
    },
    'nara': {
        'label':    'National Archives Record',
        'badge':    colors.HexColor('#3a1a1a'),
        'badge_txt':colors.HexColor('#f87171'),
        'tag':      'NARA',
        'cite':     'U.S. National Archives and Records Administration (NARA). '
                    'Federal records collection.',
    },
    'wikitree': {
        'label':    'WikiTree Community Profile',
        'badge':    colors.HexColor('#1a3320'),
        'badge_txt':colors.HexColor('#4ade80'),
        'tag':      'WIKI',
        'cite':     'WikiTree.com. Collaborative genealogy database. '
                    'Profile sourced from community contributions — verify against primary sources.',
    },
    'hamburg_emigrant': {
        'label':    'Hamburg Emigration Record',
        'badge':    colors.HexColor('#2a1a0e'),
        'badge_txt':colors.HexColor('#fb923c'),
        'tag':      'HAMBURG',
        'cite':     'Hamburg State Archives. Hamburger Auswandererlisten (Hamburg Emigration Lists). '
                    'Compiled by Ballin Institut, Hamburg.',
    },
    'chronicling_america': {
        'label':    'Historic Newspaper Record',
        'badge':    colors.HexColor('#2a2a0e'),
        'badge_txt':colors.HexColor('#facc15'),
        'tag':      'NEWS',
        'cite':     'Library of Congress. Chronicling America: Historic American Newspapers. '
                    'chroniclingamerica.loc.gov',
    },
}

_DEFAULT_META = {
    'label':    'Genealogy Record',
    'badge':    colors.HexColor('#1e293b'),
    'badge_txt':colors.HexColor('#94a3b8'),
    'tag':      'REC',
    'cite':     'Record sourced via RootBridge multi-archive search.',
}


def _record_id(record: dict) -> str:
    """Stable 12-char hex ID derived from the record content."""
    key = '|'.join(str(record.get(k, '')) for k in
                   ('source', 'title', 'birth_year', 'death_year', 'birth_place'))
    return hashlib.sha256(key.encode()).hexdigest()[:12].upper()


def _wrap(text: str, font: str, size: float, max_w: float, c) -> list[str]:
    c.setFont(font, size)
    return simpleSplit(text or '', font, size, max_w)


def _field_rows(record: dict, source: str) -> list[tuple[str, str]]:
    """Return (label, value) pairs relevant to this source type."""
    rows = []

    def add(label, key, fallback=''):
        val = str(record.get(key) or fallback).strip()
        if val and val not in ('-', 'None', '0'):
            rows.append((label, val))

    # Name always first
    name = record.get('title') or (
        f"{record.get('first_name','')} {record.get('last_name','')}".strip()
    )
    if name:
        rows.append(('Full Name', name))

    add('Birth Year',   'birth_year')
    add('Birth Place',  'birth_place')
    add('Death Year',   'death_year')
    add('Death Place',  'death_place')

    # Source-specific extras
    if source in ('ship_manifest', 'ship_manifest_db'):
        add('Vessel',       'ship_name')
        add('Arrival Date', 'arrival_date')
        add('Arrival Port', 'port')
        add('Origin',       'origin_place')
        add('Oath Signed',  'oath_signed')

    elif source == 'ssdi':
        add('Last Residence', 'death_place')
        add('SSN State',      'ssn_state')

    elif source in ('ged_vault',):
        add('Gender',       'gender')
        add('Marriage Year','marriage_year')
        add('Notes',        'notes')
        src_file = record.get('source_file', '')
        if src_file:
            import os
            rows.append(('Source File', os.path.basename(str(src_file))))

    elif source == 'revwar_pension':
        add('Pension No.',  'pension_number')
        add('State',        'birth_place')
        add('Service',      'notes')

    add('Confidence',   'confidence')
    add('URL',          'url')

    return rows


def _build_pdf(record: dict) -> bytes:
    source  = record.get('source', 'unknown')
    meta    = SOURCE_META.get(source, _DEFAULT_META)
    rec_id  = _record_id(record)
    ts      = datetime.now(timezone.utc).strftime('%B %d, %Y  %H:%M UTC')
    rows    = _field_rows(record, source)

    buf = io.BytesIO()
    W, H = letter          # 612 x 792 pts
    c = rl_canvas.Canvas(buf, pagesize=letter)

    # ── Background ────────────────────────────────────────────────────────────
    c.setFillColor(PAL['bg'])
    c.rect(0, 0, W, H, fill=1, stroke=0)

    # ── Outer gold border ─────────────────────────────────────────────────────
    c.setStrokeColor(PAL['border'])
    c.setLineWidth(1.5)
    c.rect(18, 18, W - 36, H - 36, fill=0, stroke=1)

    # ── Header band ───────────────────────────────────────────────────────────
    header_h = 72
    c.setFillColor(PAL['card'])
    c.rect(18, H - 18 - header_h, W - 36, header_h, fill=1, stroke=0)
    c.setStrokeColor(PAL['border'])
    c.setLineWidth(0.8)
    c.line(18, H - 18 - header_h, W - 18, H - 18 - header_h)

    # Logo / wordmark
    c.setFillColor(PAL['accent'])
    c.setFont('Helvetica-Bold', 18)
    c.drawString(36, H - 50, 'RootBridge')
    c.setFillColor(PAL['muted'])
    c.setFont('Helvetica', 9)
    c.drawString(36, H - 63, 'Research Citation Card')

    # Source badge (top right)
    badge_w, badge_h = 100, 22
    bx = W - 36 - badge_w - 8
    by = H - 18 - header_h // 2 - badge_h // 2
    c.setFillColor(meta['badge'])
    c.roundRect(bx, by, badge_w, badge_h, 4, fill=1, stroke=0)
    c.setFillColor(meta['badge_txt'])
    c.setFont('Helvetica-Bold', 9)
    tag = meta['tag']
    c.drawCentredString(bx + badge_w / 2, by + 7, tag)

    # ── Record ID strip ───────────────────────────────────────────────────────
    strip_y = H - 18 - header_h - 28
    c.setFillColor(PAL['badge_bg'])
    c.rect(18, strip_y, W - 36, 28, fill=1, stroke=0)
    c.setFillColor(PAL['muted'])
    c.setFont('Helvetica', 7.5)
    c.drawString(30, strip_y + 9, f'RECORD ID: {rec_id}')
    c.drawRightString(W - 30, strip_y + 9, f'Generated: {ts}')

    # ── Source label ──────────────────────────────────────────────────────────
    c.setFillColor(PAL['accent'])
    c.setFont('Helvetica-Bold', 11)
    c.drawString(36, strip_y - 28, meta['label'])
    c.setStrokeColor(PAL['border'])
    c.setLineWidth(0.5)
    c.line(36, strip_y - 33, W - 36, strip_y - 33)

    # ── Field rows ────────────────────────────────────────────────────────────
    y = strip_y - 50
    label_x  = 36
    value_x  = 200
    row_h    = 22
    max_val_w = W - value_x - 36

    for label, value in rows:
        if y < 140:
            break
        c.setFillColor(PAL['muted'])
        c.setFont('Helvetica-Bold', 9)
        c.drawString(label_x, y, label.upper())

        # Wrap long values
        lines = _wrap(value, 'Helvetica', 9.5, max_val_w, c)
        c.setFillColor(PAL['heading'])
        c.setFont('Helvetica', 9.5)
        for i, line in enumerate(lines[:3]):
            c.drawString(value_x, y - i * 12, line)

        line_count = min(len(lines), 3)
        y -= max(row_h, 12 * line_count + 6)

    # ── Divider before citation ───────────────────────────────────────────────
    cite_block_h = 90
    cite_y = 80
    c.setStrokeColor(PAL['border'])
    c.setLineWidth(0.5)
    c.line(36, cite_y + cite_block_h, W - 36, cite_y + cite_block_h)

    # ── Citation block ────────────────────────────────────────────────────────
    c.setFillColor(PAL['badge_bg'])
    c.rect(18, cite_y - 10, W - 36, cite_block_h + 10, fill=1, stroke=0)

    c.setFillColor(PAL['accent'])
    c.setFont('Helvetica-Bold', 8)
    c.drawString(36, cite_y + cite_block_h - 14, 'SUGGESTED CITATION')

    cite_lines = _wrap(meta['cite'], 'Helvetica', 8, W - 80, c)
    c.setFillColor(PAL['body'])
    c.setFont('Helvetica', 8)
    for i, line in enumerate(cite_lines[:5]):
        c.drawString(36, cite_y + cite_block_h - 30 - i * 12, line)

    # ── Footer ────────────────────────────────────────────────────────────────
    c.setFillColor(PAL['muted'])
    c.setFont('Helvetica', 7)
    c.drawCentredString(W / 2, 28,
        'This citation card is provided for research documentation purposes. '
        'Always verify records against primary sources. rootbridge.app')

    c.save()
    return buf.getvalue()


@citation_bp.post('/api/citation/generate')
@require_auth
def generate_citation():
    """Generate a PDF citation card for a search result record."""
    data = request.get_json(silent=True) or {}
    record = data.get('record')
    if not record or not isinstance(record, dict):
        return jsonify(error='record is required'), 400

    source = record.get('source', '')
    if not source:
        return jsonify(error='record must include a source field'), 400

    pdf_bytes = _build_pdf(record)

    name_slug = (record.get('title') or 'record').replace(' ', '_')[:30]
    filename  = f'rootbridge_citation_{name_slug}_{_record_id(record)}.pdf'

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename,
    )


@citation_bp.get('/api/citation/preview/<source>')
def citation_sources(source):
    """Return metadata for a source type (badge label, citation text)."""
    meta = SOURCE_META.get(source, _DEFAULT_META)
    return jsonify(
        source=source,
        label=meta['label'],
        tag=meta['tag'],
        citation=meta['cite'],
    )
