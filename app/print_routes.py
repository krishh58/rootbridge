"""
Family tree print-to-PDF generator.
Produces a print-ready PDF at the chosen poster size (vector, scalable to any DPI).
Cost: 150 tokens per download.
"""
import io
import math
from flask import Blueprint, request, send_file, jsonify, g

from reportlab.lib import colors
from reportlab.lib.pagesizes import inch
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.utils import simpleSplit

from .db import db
from .models import Tree, Person
from .auth import require_auth
from .token_middleware import require_tokens

print_bp = Blueprint('print', __name__)

# ── Page sizes (width × height in inches) ────────────────────────────────────
SIZES = {
    '11x14':  (11, 14),
    '16x20':  (16, 20),
    '18x24':  (18, 24),
    '24x36':  (24, 36),
}

# ── Style palettes ────────────────────────────────────────────────────────────
STYLES = {
    'classic': {
        'bg':        colors.HexColor('#0f172a'),
        'box_fill':  colors.HexColor('#1e293b'),
        'box_stroke':colors.HexColor('#3b82f6'),
        'line':      colors.HexColor('#334155'),
        'name_color':colors.HexColor('#f1f5f9'),
        'date_color':colors.HexColor('#94a3b8'),
        'title_color':colors.HexColor('#f1f5f9'),
        'sub_color': colors.HexColor('#94a3b8'),
        'border':    colors.HexColor('#1e40af'),
        'footer':    colors.HexColor('#475569'),
        'accent':    colors.HexColor('#3b82f6'),
    },
    'heritage': {
        'bg':        colors.HexColor('#1c1408'),
        'box_fill':  colors.HexColor('#2a1f0e'),
        'box_stroke':colors.HexColor('#b5681e'),
        'line':      colors.HexColor('#5c3d11'),
        'name_color':colors.HexColor('#e8dcc8'),
        'date_color':colors.HexColor('#a08060'),
        'title_color':colors.HexColor('#e8dcc8'),
        'sub_color': colors.HexColor('#a08060'),
        'border':    colors.HexColor('#8b4513'),
        'footer':    colors.HexColor('#6b4c2a'),
        'accent':    colors.HexColor('#b5681e'),
    },
    'modern': {
        'bg':        colors.HexColor('#f8fafc'),
        'box_fill':  colors.HexColor('#ffffff'),
        'box_stroke':colors.HexColor('#3b82f6'),
        'line':      colors.HexColor('#cbd5e1'),
        'name_color':colors.HexColor('#0f172a'),
        'date_color':colors.HexColor('#64748b'),
        'title_color':colors.HexColor('#0f172a'),
        'sub_color': colors.HexColor('#64748b'),
        'border':    colors.HexColor('#dde2ea'),
        'footer':    colors.HexColor('#94a3b8'),
        'accent':    colors.HexColor('#3b82f6'),
    },
}


def _build_hierarchy(persons):
    """Return (roots, children_map, person_by_id). persons is a list of Person rows."""
    by_id = {p.id: p for p in persons}
    id_set = set(by_id)

    # edges: parent_id → child_id
    children = {p.id: [] for p in persons}
    has_parent = set()
    for p in persons:
        for pid in (p.parent_ids or []):
            if pid in id_set:
                children[pid].append(p.id)
                has_parent.add(p.id)

    roots = [p.id for p in persons if p.id not in has_parent]
    if not roots:
        roots = [persons[0].id]

    return roots, children, by_id


def _assign_positions(roots, children, by_id):
    """
    BFS level assignment + horizontal positioning.
    Returns dict: person_id → (col, row) where row=0 is roots.
    """
    level = {}  # id → row
    queue = [(r, 0) for r in roots]
    visited = set()
    while queue:
        nid, lvl = queue.pop(0)
        if nid in visited:
            continue
        visited.add(nid)
        level[nid] = max(level.get(nid, 0), lvl)
        for child in children.get(nid, []):
            queue.append((child, lvl + 1))

    # Group by level
    by_level = {}
    for pid, lvl in level.items():
        by_level.setdefault(lvl, []).append(pid)
    max_level = max(by_level) if by_level else 0

    pos = {}
    for lvl, ids in sorted(by_level.items()):
        for col, pid in enumerate(ids):
            pos[pid] = (col, lvl)

    return pos, max_level, {lvl: len(ids) for lvl, ids in by_level.items()}


@print_bp.post('/api/trees/<int:tree_id>/print')
@require_auth
@require_tokens(150)
def generate_print_pdf(tree_id):
    tree = db.session.get(Tree, tree_id)
    if not tree or tree.user_id != g.current_user.id:
        return jsonify({'error': 'Not found'}), 404

    body = request.get_json(silent=True) or {}
    size_key  = body.get('size', '18x24')
    style_key = body.get('style', 'heritage')
    title     = (body.get('title', '') or '').strip()[:80]
    subtitle  = (body.get('subtitle', '') or '').strip()[:120]
    orient    = body.get('orientation', 'portrait')  # 'portrait' or 'landscape'

    if size_key not in SIZES:
        return jsonify({'error': 'Invalid size'}), 400
    if style_key not in STYLES:
        return jsonify({'error': 'Invalid style'}), 400

    w_in, h_in = SIZES[size_key]
    if orient == 'landscape':
        w_in, h_in = h_in, w_in

    W = w_in * inch
    H = h_in * inch
    pal = STYLES[style_key]

    persons = Person.query.filter_by(tree_id=tree_id).all()
    if not persons:
        return jsonify({'error': 'No persons in tree'}), 400

    roots, children, by_id = _build_hierarchy(persons)
    pos, max_level, level_widths = _assign_positions(roots, children, by_id)

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(W, H))

    # ── Background ────────────────────────────────────────────────────────────
    c.setFillColor(pal['bg'])
    c.rect(0, 0, W, H, fill=1, stroke=0)

    # ── Decorative border ─────────────────────────────────────────────────────
    MARGIN = 0.35 * inch
    c.setStrokeColor(pal['border'])
    c.setLineWidth(3)
    c.rect(MARGIN, MARGIN, W - 2*MARGIN, H - 2*MARGIN, fill=0, stroke=1)
    c.setLineWidth(1)
    INNER = MARGIN + 6
    c.rect(INNER, INNER, W - 2*INNER, H - 2*INNER, fill=0, stroke=1)

    # ── Title area ────────────────────────────────────────────────────────────
    TOP_RESERVED = 0
    BOTTOM_RESERVED = 0.6 * inch

    if title:
        c.setFillColor(pal['title_color'])
        font_size = max(24, min(48, int(W / (len(title) * 0.55 + 1))))
        c.setFont('Times-Bold', font_size)
        c.drawCentredString(W / 2, H - MARGIN - 0.55*inch, title)
        TOP_RESERVED = 0.75 * inch

    if subtitle:
        c.setFillColor(pal['sub_color'])
        c.setFont('Times-Italic', 16)
        c.drawCentredString(W / 2, H - MARGIN - TOP_RESERVED - 0.1*inch, subtitle)
        TOP_RESERVED += 0.35 * inch

    # Accent rule under title
    if title:
        rule_y = H - MARGIN - TOP_RESERVED - 0.05*inch
        c.setStrokeColor(pal['accent'])
        c.setLineWidth(1.5)
        c.line(MARGIN + 0.5*inch, rule_y, W - MARGIN - 0.5*inch, rule_y)
        TOP_RESERVED += 0.2 * inch

    # ── Layout area for tree nodes ────────────────────────────────────────────
    tree_top    = H - MARGIN - INNER - TOP_RESERVED - 0.15*inch
    tree_bottom = MARGIN + INNER + BOTTOM_RESERVED
    tree_h      = tree_top - tree_bottom
    tree_w      = W - 2*(MARGIN + INNER + 0.1*inch)
    tree_left   = MARGIN + INNER + 0.1*inch

    num_rows = max_level + 1
    row_h    = tree_h / num_rows if num_rows > 0 else tree_h

    # Node box sizing — fit within cell with generous padding
    BOX_H = min(row_h * 0.52, 0.65 * inch)
    max_cols = max(level_widths.values()) if level_widths else 1
    BOX_W = min(tree_w / max(max_cols, 1) * 0.82, 2.4 * inch)
    BOX_W = max(BOX_W, 1.1 * inch)

    def node_center(col, row, level_width):
        """Return (cx, cy) center of a node box in PDF coordinates."""
        cell_w = tree_w / level_width
        cx = tree_left + cell_w * col + cell_w / 2
        # row 0 = top of tree_top; increases downward
        cy = tree_top - row * row_h - row_h / 2
        return cx, cy

    node_centers = {}
    for pid, (col, row) in pos.items():
        lvl_width = level_widths.get(row, 1)
        node_centers[pid] = node_center(col, row, lvl_width)

    # ── Draw connector lines ───────────────────────────────────────────────────
    c.setStrokeColor(pal['line'])
    c.setLineWidth(0.8)
    for pid, child_ids in children.items():
        if pid not in node_centers:
            continue
        px, py = node_centers[pid]
        mid_y = py - BOX_H / 2 - (row_h - BOX_H) / 4  # midpoint between parent bottom and children top
        for cid in child_ids:
            if cid not in node_centers:
                continue
            cx, cy = node_centers[cid]
            # L-shaped connector: parent bottom → mid_y → child top
            p_bottom = py - BOX_H / 2
            c_top    = cy + BOX_H / 2
            connector_y = (p_bottom + c_top) / 2
            c.line(px, p_bottom, px, connector_y)
            c.line(px, connector_y, cx, connector_y)
            c.line(cx, connector_y, cx, c_top)

    # ── Draw nodes ────────────────────────────────────────────────────────────
    NAME_FONT_SIZE  = max(6, min(11, BOX_H * 0.28))
    DATE_FONT_SIZE  = max(5, min(8,  BOX_H * 0.18))
    CORNER_R        = 4

    for pid, (cx, cy) in node_centers.items():
        person = by_id[pid]
        x = cx - BOX_W / 2
        y = cy - BOX_H / 2

        # Box fill + stroke
        c.setFillColor(pal['box_fill'])
        c.setStrokeColor(pal['box_stroke'])
        c.setLineWidth(0.8)
        c.roundRect(x, y, BOX_W, BOX_H, CORNER_R, fill=1, stroke=1)

        # Name
        first = (person.first_name or '').strip()
        last  = (person.last_name  or '').strip()
        name  = f'{first} {last}'.strip() or '?'

        c.setFillColor(pal['name_color'])
        c.setFont('Helvetica-Bold', NAME_FONT_SIZE)
        # Truncate if needed
        max_chars = int(BOX_W / (NAME_FONT_SIZE * 0.55))
        if len(name) > max_chars:
            name = name[:max_chars - 1] + '…'
        c.drawCentredString(cx, cy + BOX_H * 0.1, name)

        # Dates
        birth = str(person.birth_year) if person.birth_year else '?'
        death = str(person.death_year) if person.death_year else ''
        dates = f'{birth} – {death}' if death else birth
        c.setFillColor(pal['date_color'])
        c.setFont('Helvetica', DATE_FONT_SIZE)
        c.drawCentredString(cx, cy - BOX_H * 0.2, dates)

    # ── Footer ────────────────────────────────────────────────────────────────
    c.setFillColor(pal['footer'])
    c.setFont('Helvetica', 8)
    c.drawCentredString(W / 2, MARGIN + 0.22*inch, 'Created with RootBridge · rootbridge.app')

    c.save()
    buf.seek(0)

    safe_title = ''.join(c2 for c2 in (title or 'FamilyTree') if c2.isalnum() or c2 in '_ -')[:40]
    filename   = f'{safe_title}_{size_key}_{style_key}.pdf'

    return send_file(
        buf,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename,
    )
