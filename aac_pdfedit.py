"""PDF Viewer + Editor for PDF Studio (v15.1).

Two halves in one file:
  * ENGINE  (top)   - PyMuPDF + Pillow only, no Tk.  Page list + operation list over a
                      working copy; the opened file is NEVER modified.  Overlay items are
                      stored in PDF points on each page and baked in only when saving.
  * GUI MIXIN (bottom) - PdfEditMixin, mixed into App (aac_gui.py) like BoardImportMixin.
                      Rendering runs in ONE worker thread; the worker never touches Tk,
                      it hands results back through App.ui_call(...).

PyMuPDF is the only PDF library (reuses aac_import._import_fitz).
"""
from __future__ import annotations

import copy
import dataclasses
import base64
import io
import itertools
import os
import queue
import subprocess
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import aac_import
from aac_common import log

try:                                    # the engine stays importable without a display
    import tkinter as tk
    from tkinter import colorchooser, filedialog, messagebox
    import customtkinter as ctk
except Exception:                       # pragma: no cover
    tk = ctk = colorchooser = filedialog = messagebox = None

CARD_PT = 110.0            # default picture-card side, in PDF points (about 1.5 in)
BAKE_PX_PER_PT = 4         # raster resolution for baked picture cards / rotated text
THUMB_BOX = (104, 132)     # thumbnail box, px
THUMB_CELL = 152           # thumbnail strip row height, px
MAX_RENDER_PX = 6000       # never ask PyMuPDF for a bigger side than this
_UID = itertools.count(1)


class PdfEditError(Exception):
    """Plain-language problem (shown to the person as-is, never a traceback)."""


def require_fitz():
    fitz = aac_import._import_fitz()
    if fitz is None:
        raise PdfEditError(
            "Opening and editing PDFs needs the PyMuPDF package, which isn't installed.\n\n"
            "Install it once (Command Prompt):   pip install pymupdf\n"
            "then restart the app.")
    return fitz


# =============================================================== overlay items
@dataclass(eq=False)          # identity equality: items hold PIL images
class Item:
    kind: str                       # "card" | "text" | "rect" | "image" | "ellipse" |
                                    # "line" | "arrow" | "freehand"
    x0: float
    y0: float
    x1: float
    y1: float                       # rectangle in the page's VISIBLE orientation, PDF points
    rot: int = 0                    # clockwise quarter-turns of the content, degrees (0/90/180/270)
    word: str = ""
    text: str = ""
    size: float = 14.0              # text font size (pt)
    color: str = "#FFFFFF"          # card tile / rect fill / text ink / shape line colour
    label: bool = True              # card: show the word under the picture
    opacity: float = 1.0            # rect only
    img: object = None              # PIL RGBA picture (cards + imported images)
    loading: bool = False
    # v15.5 full-editor fields (defaults keep v15.1 items behaving as before)
    fill: object = None             # shapes: fill colour hex, None = no fill (rect: None = legacy colour fill)
    lw: float = 1.5                 # shapes/freehand: stroke width, pt
    points: object = None           # freehand/polygon: [(x, y), ...] in page points
    closed: bool = False            # freehand: closed polygon
    border: bool = False            # shapes: colour is the outline (fill may be None)
    font: str = "Helvetica"         # text: Helvetica | Times | Courier
    bold: bool = False              # text
    italic: bool = False            # text
    align: str = "l"                # text: "l" | "c" | "r" (multi-line)
    crop: object = None             # image: (left, top, right, bottom) fractions of the source shown
    name: str = ""                  # v15.9: Objects-panel name (blank = automatic from kind/word/text)
    hidden: bool = False            # v15.9: hidden objects don't draw, hit-test or bake
    locked: bool = False            # v15.9: locked objects draw + bake but can't be grabbed on the page
    gid: int = 0                    # v15.9: group id (0 = ungrouped); selecting one member selects all
    uid: int = field(default_factory=lambda: next(_UID))

    @property
    def w(self):
        return self.x1 - self.x0

    @property
    def h(self):
        return self.y1 - self.y0

    def clone(self):
        return dataclasses.replace(self, uid=next(_UID))


def _hex_rgb(hexv, default=(0, 0, 0)):
    try:
        h = hexv.lstrip("#")
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except Exception:
        return default


def text_extent(text, size, fontname="helv"):
    """(width, height) in points of a (multi-line) label in the given font.
    v15.60: measured with the same PIL display font compose_text draws
    with. The old fitz Base-14 metrics under-measured badly — ★/✓ are
    missing from Base-14 (so “★ Great job!” measured NARROWER than
    “Great job!”) and DejaVu Bold runs wider than Helvetica — so stamp
    boxes came out ~40% too narrow and the raster clipped the text.
    Existing stamps repair themselves the next time their size, text,
    bold, italic or align changes (those paths all refit)."""
    lines = (text or " ").split("\n")
    try:
        bold = fontname in ("hebo", "hebi", "tibo", "tibi", "cobo", "cobi")
        italic = fontname in ("heit", "hebi", "tiit", "tibi", "coit", "cobi")
        f = _font(size, bold, italic)
        d = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        w = max(d.textlength(ln or " ", font=f) for ln in lines)
    except Exception:
        try:
            fitz = require_fitz()
            w = max(fitz.get_text_length(ln or " ", fontname=fontname, fontsize=size) for ln in lines)
        except Exception:
            w = max(len(ln) for ln in lines) * size * 0.52
    return max(w + 2.0, size * 0.6), len(lines) * size * 1.2


def new_card(word, cx, cy, color, side=CARD_PT):
    return Item("card", cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2,
                word=word, color=color, loading=True)


def new_text(text, cx, cy, size=18.0, color="#111111", font="Helvetica", bold=False,
             italic=False, align="l"):
    it = Item("text", 0, 0, 0, 0, text=text, size=size, color=color, font=font,
              bold=bold, italic=italic, align=align)
    w, h = text_extent(text, size, text_fontname(it))
    it.x0, it.y0, it.x1, it.y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    return it


def new_rect(cx, cy, color="#F5D67A", w=170.0, h=34.0, opacity=0.4):
    return Item("rect", cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, color=color, opacity=opacity)


def new_image(img, cx, cy, max_w=300.0, max_h=300.0):
    """An imported picture, centred at (cx, cy), fitted inside max_w x max_h points
    at its natural aspect ratio."""
    iw, ih = img.size if img is not None else (100, 100)
    f = min(max_w / max(iw, 1), max_h / max(ih, 1), 1.0)
    w, h = max(8.0, iw * f), max(8.0, ih * f)
    return Item("image", cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, img=img)


def new_shape(kind, x0, y0, x1, y1, color="#E88CA0", fill=None, lw=2.0):
    pts = [(x0, y0), (x1, y1)] if kind in ("line", "arrow") else None
    nx0, nx1 = sorted((x0, x1))
    ny0, ny1 = sorted((y0, y1))
    if nx1 - nx0 < 1:
        nx1 = nx0 + 1
    if ny1 - ny0 < 1:
        ny1 = ny0 + 1
    return Item(kind, nx0, ny0, nx1, ny1, color=color, fill=fill, lw=lw, points=pts,
                border=True)


def new_freehand(points, color="#111111", lw=2.5, closed=False, fill=None):
    xs = [q[0] for q in points]
    ys = [q[1] for q in points]
    return Item("freehand", min(xs), min(ys), max(xs), max(ys), color=color, lw=lw,
                points=[(float(a), float(b)) for a, b in points], closed=closed, fill=fill)


def text_fontname(it):
    """PyMuPDF Base-14 font name for a text item's family/weight/slant."""
    fam = {"Helvetica": ("helv", "hebo", "heit", "hebi"),
           "Times": ("tiro", "tibo", "tiit", "tibi"),
           "Courier": ("cour", "cobo", "coit", "cobi")}.get(it.font, ("helv", "hebo", "heit", "hebi"))
    return fam[(1 if it.bold else 0) + (2 if it.italic else 0)]


def cropped_image(it):
    """The item's picture with its crop fractions applied (images only)."""
    im = it.img
    if im is None:
        return None
    if not it.crop:
        return im
    l, t, r, b = it.crop
    W, H = im.size
    box = (int(l * W), int(t * H), max(int(r * W), int(l * W) + 1), max(int(b * H), int(t * H) + 1))
    return im.crop(box)


def refit_text(it):
    """Recompute a text item's rectangle from its text/size (keeps the top-left corner).
    v15.9: the rectangle is the item's UNROTATED box — `rot` turns the content
    around the box centre — so refitting never swaps the sides."""
    w, h = text_extent(it.text, it.size, text_fontname(it))
    it.x1, it.y1 = it.x0 + w, it.y0 + h


def resize_item(it, orig, handle, px, py):
    """Drag a corner ('nw','ne','sw','se') or edge ('n','s','e','w') handle to the
    page point (px, py).  orig = (x0, y0, x1, y1, size) captured when the drag began;
    orig[5] may carry the freehand points list.  The opposite side stays fixed.
    Rectangles/ellipses/lines stretch freely; cards, images and text keep proportion."""
    ox0, oy0, ox1, oy1, osize = orig[:5]
    free = it.kind in ("rect", "ellipse", "line", "arrow")
    if free or len(handle) == 1:
        nx0, ny0, nx1, ny1 = ox0, oy0, ox1, oy1
        if "w" in handle:
            nx0 = px
        if "e" in handle:
            nx1 = px
        if "n" in handle:
            ny0 = py
        if "s" in handle:
            ny1 = py
        if not free:                       # proportional kinds on an edge: scale by that axis
            ow, oh = max(ox1 - ox0, 1e-6), max(oy1 - oy0, 1e-6)
            if handle in ("e", "w"):
                f = max(abs(nx1 - nx0) / ow, 0.12)
                cy = (oy0 + oy1) / 2
                nh = oh * f
                ny0, ny1 = cy - nh / 2, cy + nh / 2
            else:
                f = max(abs(ny1 - ny0) / oh, 0.12)
                cx = (ox0 + ox1) / 2
                nw = ow * f
                nx0, nx1 = cx - nw / 2, cx + nw / 2
            if it.kind == "text":
                it.size = max(4.0, min(400.0, osize * f))
        nx0, nx1 = sorted((nx0, nx1))
        ny0, ny1 = sorted((ny0, ny1))
        if nx1 - nx0 < 4:
            nx1 = nx0 + 4
        if ny1 - ny0 < 4:
            ny1 = ny0 + 4
        it.x0, it.y0, it.x1, it.y1 = nx0, ny0, nx1, ny1
    else:
        ax = ox1 if "w" in handle else ox0
        ay = oy1 if "n" in handle else oy0
        ow, oh = max(ox1 - ox0, 1e-6), max(oy1 - oy0, 1e-6)
        f = max(abs(px - ax) / ow, abs(py - ay) / oh, 0.12)
        nw, nh = ow * f, oh * f
        it.x0 = ax - nw if "w" in handle else ax
        it.x1 = ax if "w" in handle else ax + nw
        it.y0 = ay - nh if "n" in handle else ay
        it.y1 = ay if "n" in handle else ay + nh
        if it.kind == "text":
            it.size = max(4.0, min(400.0, osize * f))
    # v15.12: lines and arrows render from their points, not their box, so
    # their points have to follow the box exactly like a freehand's do —
    # before this, dragging a resize handle moved the selection box while
    # the line itself stayed put (and then wasn't clickable where it looked).
    if it.kind in ("freehand", "line", "arrow") and it.points and len(orig) > 5 and orig[5]:
        ow, oh = max(ox1 - ox0, 1e-6), max(oy1 - oy0, 1e-6)
        fx = (it.x1 - it.x0) / ow
        fy = (it.y1 - it.y0) / oh
        it.points = [(it.x0 + (qx - ox0) * fx, it.y0 + (qy - oy0) * fy) for qx, qy in orig[5]]


# =============================================================== rasters (cards, rotated text)
_FONTS = {}


def _font(px, bold=False, italic=False):
    px = max(6, int(px))
    key = (px, bold, italic)
    if key in _FONTS:
        return _FONTS[key]
    if bold and italic:
        names = ["arialbi.ttf", "Arial Bold Italic.ttf", "DejaVuSans-BoldOblique.ttf",
                 "LiberationSans-BoldItalic.ttf"]
    elif italic:
        names = ["ariali.ttf", "Arial Italic.ttf", "DejaVuSans-Oblique.ttf",
                 "LiberationSans-Italic.ttf"]
    elif bold:
        names = ["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"]
    else:
        names = ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"]
    f = None
    for n in names:
        try:
            f = ImageFont.truetype(n, px)
            break
        except Exception:
            continue
    if f is None:
        try:
            f = ImageFont.load_default(size=px)
        except TypeError:
            f = ImageFont.load_default()
    _FONTS[key] = f
    return f


def _fit_label(draw, text, max_w, start_px, min_px):
    px = start_px
    while px > min_px:
        f = _font(px, True)
        if draw.textlength(text, font=f) <= max_w:
            return f
        px -= 1
    return _font(min_px, True)


def compose_card(word, w_px, h_px, tile_hex, icon, label_on=True, loading=False):
    """The picture card as it is stamped: category-colour tile, picture, word label.
    Same look as the card view (tile + thin dark outline, picture above, word near the bottom)."""
    w_px, h_px = max(8, int(w_px)), max(8, int(h_px))
    im = Image.new("RGBA", (w_px, h_px), tile_hex or "#FFFFFF")
    d = ImageDraw.Draw(im)
    side = min(w_px, h_px)
    d.rectangle([0, 0, w_px - 1, h_px - 1], outline="#222222", width=max(1, round(side / 110)))
    if loading:
        f = _font(side * 0.22)
        d.text((w_px / 2, h_px / 2), "\u2026", font=f, fill="#9aa2ad", anchor="mm")
        return im
    if icon is None:                      # no picture: the word big, like the card view does
        f = _fit_label(d, word, w_px * 0.9, side * 0.2, 8)
        d.text((w_px / 2, h_px / 2), word, font=f, fill="#111111", anchor="mm")
        return im
    top, bot = h_px * 0.06, h_px * (0.74 if (label_on and word) else 0.94)
    pic = icon.copy()
    pic.thumbnail((max(1, int(w_px * 0.8)), max(1, int(bot - top))), Image.Resampling.LANCZOS)
    im.alpha_composite(pic, (int((w_px - pic.width) / 2), int(top + ((bot - top) - pic.height) / 2)))
    if label_on and word:
        f = _fit_label(d, word, w_px * 0.9, side * 0.16, 7)
        d.text((w_px / 2, h_px * 0.88), word, font=f, fill="#111111", anchor="mm")
    return im


def compose_text(text, size_pt, color_hex, w_pt, h_pt, px_per_pt,
                 bold=False, italic=False, align="l"):
    W, H = max(4, int(w_pt * px_per_pt)), max(4, int(h_pt * px_per_pt))
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = _font(size_pt * px_per_pt, bold, italic)
    step = size_pt * px_per_pt * 1.2
    for i, ln in enumerate((text or " ").split("\n")):
        tw = d.textlength(ln, font=f)
        x = 0 if align == "l" else (W - tw) / 2 if align == "c" else W - tw
        d.text((x, i * step), ln, font=f, fill=color_hex)
    return im


def _hex_rgba255(hexv, alpha=1.0):
    """(r, g, b, a) 0–255 ints for a hex colour + opacity, for PIL drawing."""
    try:
        h = hexv.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        r, g, b = (0, 0, 0)
    return (r, g, b, max(0, min(255, int(alpha * 255))))


def combine_render(items, bbox):
    """v15.63: flatten visible items into ONE RGBA picture of their union
    box, exactly as they appear on the page (back-to-front = the order
    they sit in the page's item list). Cards, pictures and text reuse
    item_raster; shapes are drawn with PIL mirroring the PDF-bake rules
    (border vs fill, opacity on fills, rotated rect/oval as polygons,
    arrowheads, closed freehand as a polygon). Transparent outside the
    items, so the result moves around like a sticker, not a white box."""
    import math
    x0, y0, x1, y1 = bbox
    w_pt, h_pt = max(1.0, x1 - x0), max(1.0, y1 - y0)
    scale = min(4.0, 4000.0 / max(w_pt, h_pt))
    W, H = max(4, int(w_pt * scale)), max(4, int(h_pt * scale))
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)

    def X(px):
        return (px - x0) * scale

    def Y(py):
        return (py - y0) * scale

    for it in items:
        if it.hidden:
            continue
        if it.kind in ("card", "image", "text"):
            spr = item_raster(it, scale)
            aabb = item_aabb(it)
            cx, cy = (aabb[0] + aabb[2]) / 2, (aabb[1] + aabb[3]) / 2
            canvas.alpha_composite(spr, (int(X(cx) - spr.width / 2),
                                         int(Y(cy) - spr.height / 2)))
            continue
        op = max(0.0, min(1.0, it.opacity))
        lw = max(1, int(it.lw * scale))
        if it.kind == "rect":
            if it.rot:
                pts = [(X(a), Y(b)) for a, b in item_corners(it)]
                if it.border:
                    if it.fill:
                        d.polygon(pts, fill=_hex_rgba255(it.fill, op))
                    d.line(pts + [pts[0]], fill=_hex_rgba255(it.color)[:3],
                           width=lw, joint="curve")
                else:
                    d.polygon(pts, fill=_hex_rgba255(it.color, op))
            else:
                box = [X(it.x0), Y(it.y0), X(it.x1), Y(it.y1)]
                if it.border:
                    if it.fill:
                        d.rectangle(box, fill=_hex_rgba255(it.fill, op))
                    d.rectangle(box, outline=_hex_rgba255(it.color)[:3], width=lw)
                else:
                    d.rectangle(box, fill=_hex_rgba255(it.color, op))
        elif it.kind == "ellipse":
            if it.rot:
                cx, cy = item_center(it)
                rx, ry = it.w / 2.0, it.h / 2.0
                pts = []
                for k in range(48):
                    a = 2 * math.pi * k / 48
                    pts.append(_rot_xy(cx + rx * math.cos(a), cy + ry * math.sin(a),
                                       cx, cy, it.rot))
                pts = [(X(a), Y(b)) for a, b in pts]
                if it.fill:
                    d.polygon(pts, fill=_hex_rgba255(it.fill, op))
                d.line(pts + [pts[0]], fill=_hex_rgba255(it.color)[:3],
                       width=lw, joint="curve")
            else:
                box = [X(it.x0), Y(it.y0), X(it.x1), Y(it.y1)]
                d.ellipse(box, fill=_hex_rgba255(it.fill, op) if it.fill else None,
                          outline=_hex_rgba255(it.color)[:3], width=lw)
        elif it.kind in ("line", "arrow", "freehand"):
            pts = [(X(a), Y(b)) for a, b in
                   (it.points if it.points else [(it.x0, it.y0), (it.x1, it.y1)])]
            if it.kind == "freehand" and it.closed and len(pts) > 2:
                if it.fill:
                    d.polygon(pts, fill=_hex_rgba255(it.fill, op))
                d.line(pts + [pts[0]], fill=_hex_rgba255(it.color)[:3],
                       width=lw, joint="curve")
            else:
                d.line(pts, fill=_hex_rgba255(it.color)[:3], width=lw, joint="curve")
            if it.kind == "arrow" and len(pts) >= 2:
                (ax, ay), (bx, by) = pts[-2], pts[-1]
                ang = math.atan2(by - ay, bx - ax)
                hl = max(7.0 * scale, it.lw * scale * 4.5)
                for da in (2.74, -2.74):
                    hx, hy = bx + hl * math.cos(ang + da), by + hl * math.sin(ang + da)
                    d.line([(bx, by), (hx, hy)], fill=_hex_rgba255(it.color)[:3],
                           width=lw)
    return canvas


def item_raster(it, px_per_pt):
    """RGBA picture of a card/text/image item as it APPEARS on the page (rotation
    applied); its pixel size matches the item's visible (rotated) footprint.
    v15.9: the item rectangle is the UNROTATED box; the content is composed at
    that size and then rotated, exactly like a graphics editor."""
    w_pt, h_pt = it.w, it.h                                       # upright size
    if it.kind == "card":
        im = compose_card(it.word, w_pt * px_per_pt, h_pt * px_per_pt, it.color, it.img,
                          it.label, it.loading)
    elif it.kind == "image":
        src_im = cropped_image(it)
        tw, th = max(4, int(w_pt * px_per_pt)), max(4, int(h_pt * px_per_pt))
        if src_im is None:
            im = Image.new("RGBA", (tw, th), "#dfe5ec")
            ImageDraw.Draw(im).text((8, 8), "no picture", fill="#66707c")
        else:
            # v15.64: fit INSIDE (tw, th) at the largest size, upscaling
            # when the source is smaller — thumbnail() never upscales,
            # so a picture smaller than its baked size used to land
            # small and centred in its rectangle (saved PDFs and snips
            # over picture objects came back wrong), while the editor
            # at display scale showed it filling the rectangle.
            sw, sh = src_im.size
            sc = min(tw / sw, th / sh)
            nw, nh = max(1, round(sw * sc)), max(1, round(sh * sc))
            im = (src_im.resize((nw, nh), Image.Resampling.LANCZOS)
                  if (nw, nh) != (sw, sh) else src_im.copy())
            canvas = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
            canvas.alpha_composite(im, ((tw - nw) // 2, (th - nh) // 2))
            im = canvas
    else:
        im = compose_text(it.text, it.size, it.color, w_pt, h_pt, px_per_pt,
                          it.bold, it.italic, it.align)
    if it.rot:
        im = im.rotate(-it.rot, expand=True)                      # PIL angles are counter-clockwise
    return im


# ------------------------------------------------- v15.9 rotation geometry
def _rot_xy(x, y, cx, cy, deg):
    """Rotate (x, y) CLOCKWISE by deg degrees around (cx, cy), in page points
    (y grows downward, so the usual screen-clockwise formula applies)."""
    import math
    if not deg:
        return (x, y)
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    dx, dy = x - cx, y - cy
    return (cx + dx * ca - dy * sa, cy + dx * sa + dy * ca)


def item_center(it):
    return ((it.x0 + it.x1) / 2.0, (it.y0 + it.y1) / 2.0)


def item_corners(it):
    """The item's four visible corners (rotation applied), from its top-left."""
    cx, cy = item_center(it)
    return [_rot_xy(x, y, cx, cy, it.rot)
            for x, y in ((it.x0, it.y0), (it.x1, it.y0), (it.x1, it.y1), (it.x0, it.y1))]


def item_aabb(it):
    """Axis-aligned bounding box of the item as it APPEARS (rotation applied).
    For line/freehand kinds the points already carry the geometry."""
    if it.points:
        xs = [q[0] for q in it.points]
        ys = [q[1] for q in it.points]
        return (min(xs), min(ys), max(xs), max(ys))
    cs = item_corners(it)
    xs = [c[0] for c in cs]
    ys = [c[1] for c in cs]
    return (min(xs), min(ys), max(xs), max(ys))


def item_contains(it, px, py):
    """Hit test in page points, honouring rotation: the pointer is un-rotated
    into the item's own space and tested against its unrotated rectangle."""
    cx, cy = item_center(it)
    qx, qy = _rot_xy(px, py, cx, cy, -it.rot)
    return it.x0 <= qx <= it.x1 and it.y0 <= qy <= it.y1


def item_display_name(it):
    """Short human name for the Objects list / properties header."""
    if it.name:
        return it.name
    if it.kind == "card":
        return f"Picture card · {it.word}" if it.word else "Picture card"
    if it.kind == "text":
        t = " ".join((it.text or "").split())
        return f"Text · {t[:18]}" if t else "Text"
    if it.kind == "image":
        return "Picture"
    if it.kind == "ellipse":
        return "Oval"
    if it.kind == "line":
        return "Line"
    if it.kind == "arrow":
        return "Arrow"
    if it.kind == "freehand":
        return "Polygon" if it.closed else "Freehand drawing"
    if it.kind == "rect":
        return "Rectangle" if it.border else "Highlight box"
    return it.kind


# =============================================================== pages + session
CARD_SRC = -2                # PageEntry.src marker for a generated card page


@dataclass(eq=False)
class PageEntry:
    src: int                 # index into Session.sources (-1 = blank, -2 = card page)
    idx: int                 # page index inside that source (unique id for blank/card pages)
    w: float                 # visible size of the SOURCE page (its own /Rotate applied), points
    h: float
    rot: int = 0             # extra clockwise rotation added in the editor (0/90/180/270)
    items: list = field(default_factory=list)
    card: object = None      # v15.6: card-page spec dict (words/layout/paper/scales…)
    whiteouts: list = field(default_factory=list)   # v15.13: cut-out erasures —
                                     # (x0, y0, x1, y1) page rects blanked permanently:
                                     # not items, not selectable, not movable, no outline

    @property
    def size(self):
        return (self.h, self.w) if self.rot in (90, 270) else (self.w, self.h)

    @property
    def key(self):
        return (self.src, self.idx, self.rot)

    def clone(self, fresh_items=False):
        e = PageEntry(self.src, self.idx, self.w, self.h, self.rot,
                      [(i.clone() if fresh_items else dataclasses.replace(i)) for i in self.items],
                      copy.deepcopy(self.card) if self.card is not None else None)
        e.whiteouts = list(self.whiteouts)
        return e


def _rotate_items(entry, d):
    """Rotate a page (and everything stamped on it) clockwise by d degrees.
    v15.9: an item's rectangle is its unrotated box, so a page turn moves the
    box centre and adds a quarter-turn to the item's own rotation — the box
    size itself never swaps.  Point-based items rotate their points and take
    the points' bounding box, as before."""
    for _ in range((d % 360) // 90):
        W, H = entry.size
        for it in entry.items:
            cx, cy = (it.x0 + it.x1) / 2, (it.y0 + it.y1) / 2
            ncx, ncy = H - cy, cx
            w, h = it.w, it.h
            it.x0, it.y0, it.x1, it.y1 = ncx - w / 2, ncy - h / 2, ncx + w / 2, ncy + h / 2
            if it.points:
                it.points = [(H - qy, qx) for qx, qy in it.points]
                xs = [q[0] for q in it.points]
                ys = [q[1] for q in it.points]
                it.x0, it.y0, it.x1, it.y1 = min(xs), min(ys), max(xs), max(ys)
            it.rot = (it.rot + 90) % 360
        entry.rot = (entry.rot + 90) % 360


class Session:
    """An opened PDF (plus any inserted ones) as a list of pages and an operation list.
    Source files are opened read-only and never written."""

    def __init__(self):
        self.sources = []            # [{"path": Path, "doc": fitz.Document}]
        self.pages = []              # [PageEntry] - the working copy, in output order
        self.ops = []                # what was done, in order: ("rotate", i, d) etc.
        self.dirty = False
        self.lock = threading.RLock()    # PyMuPDF documents are not thread-safe
        self._undo = []
        self._redo = []              # v15.5: redo stack (cleared by any new change)
        self._blank_dims = {}        # v15.5: blank-page id -> (w, h) in points
        self._blank_seq = itertools.count(1)
        self.card_images = {}        # v15.6: card-page id -> PIL image (screen resolution)
        self.card_render_fn = None   # v15.6: mixin-supplied fn(entry, px_per_pt) -> PIL image

    # ---- opening
    @classmethod
    def open(cls, path):
        s = cls()
        _, entries = s.load_source(path)
        s.pages = entries
        return s

    @property
    def title(self):
        return self.sources[0]["path"].name if self.sources else ""

    def load_source(self, path):
        """Open a PDF read-only and return (source id, [PageEntry]).  Safe in a worker thread."""
        fitz = require_fitz()
        with self.lock:
            try:
                doc = fitz.open(str(path))
            except Exception as e:
                raise PdfEditError(f"Couldn't open that PDF: {e}") from e
            try:
                if getattr(doc, "needs_pass", False):
                    raise PdfEditError("That PDF is password-protected.")
                n = doc.page_count
                if n < 1:
                    raise PdfEditError("That PDF has no pages.")
                entries = []
                sid = len(self.sources)
                for i in range(n):
                    r = doc.load_page(i).rect
                    entries.append(PageEntry(sid, i, float(r.width), float(r.height)))
            except Exception:
                try:
                    doc.close()
                except Exception:
                    pass
                raise
            self.sources.append({"path": Path(path), "doc": doc})
        return sid, entries

    def close(self):
        with self.lock:
            for s in self.sources:
                try:
                    s["doc"].close()
                except Exception:
                    pass
            self.sources = []

    # ---- operations (the only way pages change)
    def push_undo(self):
        self._undo.append([e.clone() for e in self.pages])
        del self._undo[:-60]
        self._redo.clear()

    def undo(self):
        if not self._undo:
            return False
        self._redo.append([e.clone() for e in self.pages])
        del self._redo[:-60]
        self.pages = self._undo.pop()
        self.dirty = True
        return True

    def redo(self):
        if not self._redo:
            return False
        self._undo.append([e.clone() for e in self.pages])
        del self._undo[:-60]
        self.pages = self._redo.pop()
        self.dirty = True
        return True

    def apply(self, op):
        k = op[0]
        self.push_undo()
        if k == "rotate":
            _, i, d = op
            _rotate_items(self.pages[i], d)
        elif k == "delete":
            if len(self.pages) <= 1:
                self._undo.pop()
                raise PdfEditError("A PDF needs at least one page - add another page before deleting this one.")
            self.pages.pop(op[1])
        elif k == "duplicate":
            self.pages.insert(op[1] + 1, self.pages[op[1]].clone(fresh_items=True))
        elif k == "move":
            _, i, j = op
            self.pages.insert(j, self.pages.pop(i))
        elif k == "insert":
            _, at, entries = op
            self.pages[at:at] = entries
        elif k == "blank":
            _, at, w, h = op
            bid = -next(self._blank_seq)
            self._blank_dims[bid] = (float(w), float(h))
            self.pages.insert(at, PageEntry(-1, bid, float(w), float(h)))
        else:
            self._undo.pop()
            raise ValueError(k)
        self.ops.append(op)
        self.dirty = True

    # ---- rendering (worker thread; reads the source, never writes it)
    def render_page(self, src, idx, rot, scale):
        fitz = require_fitz()
        if src == CARD_SRC:                           # v15.6: a generated card page
            base = self.card_images.get(idx)
            e = next((p for p in self.pages if p.src == CARD_SRC and p.idx == idx), None)
            w, h = (e.size if e is not None else (612.0, 792.0))
            if base is None:
                img = Image.new("RGB", (max(2, int(w * scale)), max(2, int(h * scale))), "white")
            else:
                img = base.resize((max(2, int(w * scale)), max(2, int(h * scale))),
                                  Image.Resampling.LANCZOS)
            return img.rotate(-rot, expand=True) if rot else img
        if src < 0:                                   # v15.5: a blank page is just white
            w, h = self._blank_dims.get(idx, (612.0, 792.0))
            img = Image.new("RGB", (max(2, int(w * scale)), max(2, int(h * scale))), "white")
            return img.rotate(-rot, expand=True) if rot else img
        with self.lock:
            page = self.sources[src]["doc"].load_page(idx)
            r = page.rect
            s = max(0.02, min(scale, MAX_RENDER_PX / max(r.width, r.height, 1.0)))
            pix = page.get_pixmap(matrix=fitz.Matrix(s, s), alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return img.rotate(-rot, expand=True) if rot else img

    # ---- harvesting (v15.8): read text / pictures OUT of a source page.
    # Rects arrive in the page's VISIBLE orientation (before any editor rotation).
    def extract_text(self, src, idx, vis_rect):
        """(text, [word, ...]) of the words inside a visible-orientation rect."""
        fitz = require_fitz()
        with self.lock:
            page = self.sources[src]["doc"].load_page(idx)
            clip = (fitz.Rect(*vis_rect) * page.derotation_matrix).normalize()
            found = page.get_text("words", clip=clip, sort=True)
        words = [str(w[4]) for w in found if str(w[4]).strip()]
        return " ".join(words), words

    def render_region(self, src, idx, vis_rect, scale=4.0):
        """The page region as a PIL image, in the orientation the user sees.
        v15.63: crop with the scale PyMuPDF REALLY used — render_page
        clamps to MAX_RENDER_PX, so on a very large page the image comes
        back smaller than `scale` and cropping with `scale` grabbed a
        shifted, squashed region instead of the dragged one."""
        img = self.render_page(src, idx, 0, scale)
        if img is None:
            return None
        vw = 0
        if src >= 0:
            with self.lock:
                try:
                    vw = self.sources[src]["doc"].load_page(idx).rect.width
                except Exception:
                    vw = 0
        actual = (img.width / vw) if vw else scale
        x0, y0, x1, y1 = vis_rect
        box = (max(0, int(x0 * actual)), max(0, int(y0 * actual)),
               min(img.width, int(x1 * actual)), min(img.height, int(y1 * actual)))
        if box[2] - box[0] < 2 or box[3] - box[1] < 2:
            return None
        return img.crop(box)

    # ---- saving
    def snapshot(self):
        return [e.clone() for e in self.pages]

    def source_paths(self):
        return [s["path"] for s in self.sources]

    def build(self, pages, out_path, progress=None):
        """Write `pages` (a snapshot list of PageEntry) to a brand-new PDF at out_path.
        Temp file first, verified, then swapped in - a failure leaves nothing half-written.
        Returns {"path", "pages"}."""
        fitz = require_fitz()
        out_path = Path(out_path)
        try:
            target = out_path.resolve()
        except Exception:
            target = out_path
        for p in self.source_paths():
            try:
                same = p.resolve() == target
            except Exception:
                same = False
            if same:
                raise PdfEditError(f"“{out_path.name}” is the file you opened - the original is never "
                                   "changed. Save under a different name.")
        n = len(pages)
        if n < 1:
            raise PdfEditError("There are no pages to save.")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_name(f".{out_path.stem}.{uuid.uuid4().hex[:8]}.tmp")
        out = fitz.open()
        try:
            with self.lock:
                i = 0
                while i < n:                       # copy runs of consecutive source pages in one go
                    if pages[i].src == CARD_SRC:   # v15.6: generated card page
                        bw, bh = pages[i].size
                        pg0 = out.new_page(width=bw, height=bh)
                        if self.card_render_fn is not None:
                            cim = self.card_render_fn(pages[i], BAKE_PX_PER_PT)
                            if cim is not None:
                                buf = io.BytesIO()
                                cim.save(buf, "PNG")
                                pg0.insert_image(fitz.Rect(0, 0, bw, bh), stream=buf.getvalue(),
                                                 keep_proportion=False, overlay=False)
                        i += 1
                        continue
                    if pages[i].src < 0:           # v15.5: blank page
                        bw, bh = pages[i].size
                        out.new_page(width=bw, height=bh)
                        i += 1
                        continue
                    j = i
                    while (j + 1 < n and pages[j + 1].src == pages[i].src
                           and pages[j + 1].idx == pages[j].idx + 1):
                        j += 1
                    out.insert_pdf(self.sources[pages[i].src]["doc"],
                                   from_page=pages[i].idx, to_page=pages[j].idx)
                    i = j + 1
                for k, e in enumerate(pages):
                    if progress:
                        progress(k / n, f"Saving page {k + 1} of {n}\u2026")
                    pg = out[k]
                    if e.rot:
                        pg.set_rotation((pg.rotation + e.rot) % 360)
                    # v15.13: cut-out erasures bake under the items, with no outline.
                    # Item rects are in the visible orientation, so whiteouts go
                    # through the same derotation the item baker uses.
                    for (wx0, wy0, wx1, wy1) in getattr(e, "whiteouts", []):
                        try:
                            pg.draw_rect((fitz.Rect(wx0, wy0, wx1, wy1)
                                          * pg.derotation_matrix).normalize(),
                                         color=None, fill=(1, 1, 1), width=0)
                        except Exception:
                            pass
                    self._bake(fitz, pg, e.items)
                out.save(str(tmp), garbage=3, deflate=True)
            out.close()
            out = None
            self._verify(fitz, tmp, pages)
            os.replace(tmp, out_path)
        except PermissionError:
            raise PdfEditError(f"Can't save '{out_path.name}' - it is open in another program "
                               "(a PDF viewer?). Close it, or pick a different file name, and try again.")
        finally:
            if out is not None:
                try:
                    out.close()
                except Exception:
                    pass
            try:
                tmp.unlink()
            except OSError:
                pass
        self._verify(fitz, out_path, pages)            # the file the person will open
        if progress:
            progress(1.0, "Saved")
        return {"path": str(out_path), "pages": n}

    @staticmethod
    def _verify(fitz, path, pages):
        try:
            chk = fitz.open(str(path), filetype="pdf")
        except Exception as e:
            raise PdfEditError(f"The saved PDF could not be re-opened ({e}). Nothing was replaced.") from e
        try:
            if chk.page_count != len(pages):
                raise PdfEditError(f"The saved PDF has {chk.page_count} pages but {len(pages)} were expected. "
                                   "Nothing was replaced.")
        finally:
            chk.close()

    @staticmethod
    def _bake(fitz, page, items):
        """Draw the overlay items onto a page.  Item rectangles are in the VISIBLE page
        orientation; PyMuPDF draws in the unrotated page, so go through derotation_matrix."""
        R = page.rotation % 360
        dm = page.derotation_matrix

        def T(x, y):
            return fitz.Point(x, y) * dm

        for it in items:
            if getattr(it, "hidden", False):
                continue                       # v15.9: hidden objects never bake
            vis = fitz.Rect(it.x0, it.y0, it.x1, it.y1)
            op = max(0.0, min(1.0, it.opacity))
            if it.kind == "rect" and it.rot:
                # v15.9: a rotated rectangle bakes as its rotated polygon
                sh = page.new_shape()
                cs = item_corners(it)
                sh.draw_polyline([T(a, b) for a, b in cs] + [T(*cs[0])])
                sh.finish(color=_hex_rgb(it.color) if it.border else None,
                          fill=_hex_rgb(it.fill) if it.fill else
                          (None if it.border else _hex_rgb(it.color)),
                          fill_opacity=op, width=max(0.2, it.lw) if it.border else 0,
                          closePath=True)
                sh.commit(overlay=True)
                continue
            if it.kind == "rect":
                if it.border:      # v15.5 shape rectangle: outline colour + optional fill
                    page.draw_rect((vis * dm).normalize(), color=_hex_rgb(it.color),
                                   fill=_hex_rgb(it.fill) if it.fill else None,
                                   fill_opacity=op, width=max(0.2, it.lw))
                else:              # legacy highlight box
                    page.draw_rect((vis * dm).normalize(), color=None, fill=_hex_rgb(it.color),
                                   fill_opacity=op, width=0)
                continue
            if it.kind == "ellipse" and it.rot:
                # v15.9: a rotated oval bakes as a fine polygon approximation
                import math
                cx, cy = item_center(it)
                rx, ry = it.w / 2.0, it.h / 2.0
                pts = []
                for k in range(48):
                    a = 2 * math.pi * k / 48
                    pts.append(_rot_xy(cx + rx * math.cos(a), cy + ry * math.sin(a),
                                       cx, cy, it.rot))
                sh = page.new_shape()
                sh.draw_polyline([T(a, b) for a, b in pts] + [T(*pts[0])])
                sh.finish(color=_hex_rgb(it.color),
                          fill=_hex_rgb(it.fill) if it.fill else None,
                          fill_opacity=op, width=max(0.2, it.lw), closePath=True)
                sh.commit(overlay=True)
                continue
            if it.kind == "ellipse":
                page.draw_oval((vis * dm).normalize(), color=_hex_rgb(it.color),
                               fill=_hex_rgb(it.fill) if it.fill else None,
                               fill_opacity=op, width=max(0.2, it.lw))
                continue
            if it.kind in ("line", "arrow", "freehand"):
                pts = it.points if it.points else [(it.x0, it.y0), (it.x1, it.y1)]
                sh = page.new_shape()
                if it.kind == "freehand" and it.closed and len(pts) > 2:
                    sh.draw_polyline([T(a, b) for a, b in pts] + [T(*pts[0])])
                else:
                    sh.draw_polyline([T(a, b) for a, b in pts])
                if it.kind == "arrow" and len(pts) >= 2:
                    import math
                    (ax, ay), (bx, by) = pts[-2], pts[-1]
                    ang = math.atan2(by - ay, bx - ax)
                    hl = max(7.0, it.lw * 4.5)
                    for da in (2.74, -2.74):
                        hx, hy = bx + hl * math.cos(ang + da), by + hl * math.sin(ang + da)
                        sh.draw_polyline([T(bx, by), T(hx, hy)])
                sh.finish(color=_hex_rgb(it.color),
                          fill=_hex_rgb(it.fill) if (it.fill and it.closed) else None,
                          width=max(0.2, it.lw), closePath=bool(it.closed))
                sh.commit(overlay=True)
                continue
            latin = all(ord(c) < 256 for c in (it.text or ""))
            if it.kind == "text" and R == 0 and it.rot == 0 and latin:
                fn = text_fontname(it)
                ink = _hex_rgb(it.color)
                for li, ln in enumerate((it.text or " ").split("\n")):
                    tw = fitz.get_text_length(ln or " ", fontname=fn, fontsize=it.size)
                    lx = it.x0 if it.align == "l" else it.x0 + max(0.0, (it.w - tw) / 2) \
                        if it.align == "c" else it.x0 + max(0.0, it.w - tw)
                    page.insert_text(fitz.Point(lx, it.y0 + it.size * 0.92 + li * it.size * 1.2),
                                     ln, fontsize=it.size, fontname=fn, color=ink)
                continue
            im = item_raster(it, BAKE_PX_PER_PT)               # as seen on the page ...
            if R:
                im = im.rotate(R, expand=True)                 # ... then back to unrotated page space
            buf = io.BytesIO()
            im.save(buf, "PNG")
            # v15.9: the raster already carries the item's rotation, so it is
            # inserted into the item's visible footprint (its rotated AABB).
            rvis = fitz.Rect(*item_aabb(it)) if it.rot else vis
            page.insert_image((rvis * dm).normalize(), stream=buf.getvalue(), keep_proportion=False,
                              overlay=True)


# =============================================================== GUI mixin
def _pal():
    import aac_gui as G              # lazy: aac_gui imports this module
    return G


class PdfEditMixin:
    """PDF Viewer + Editor mode of the PDF Studio tab (mixed into App)."""

    _ped_active = False              # per session only - never saved to settings
    _ped = None                      # the Session
    _ped_tool = "select"             # v15.5: active ribbon tool
    _ped_rib_tab = "Home"            # v15.5: active ribbon tab
    _ped_two = True                  # v15.55: two-page view is the default now
    _ped_row = False                 # v15.65: all-pages row view (mutually exclusive with pair)
    _ped_two_base = 0                # v15.55: left page of the pair
    _ped_two_moved = None            # v15.53: (page_idx, [items]) just moved onto the other page
    _ped_two_hover = False           # v15.53: a move drag is hovering the other page
    _ped_draw_state = None           # v15.5: in-progress shape/markup drag
    _ped_ghost = None                # v15.5: (kind, x0, y0, x1, y1) or ("free", points)
    _ped_poly = None                 # v15.5: polygon points being clicked out
    _ped_crop = False                # v15.5: crop mode for the selected image
    _ped_clip = None                 # v15.5: copied item (editor clipboard)
    _ped_snap = False                # v15.5: snap moves to a 6 pt grid
    _ped_grid = False                # v15.5: show a light page grid
    _ped_shape_fill = "#F5D67A"      # v15.5: shape defaults (None = no fill)
    _ped_shape_line = "#111111"
    _ped_shape_lw = 2.0
    _ped_cur = 0
    _ped_zoom = 1.0                  # relative to "fit"
    _ped_sel = None                  # primary selected Item (the newest of the selection)
    _ped_drag = None
    _ped_ui_built = False
    # v15.9: Inkscape-style editing state
    _ped_selset = []                 # every selected Item (includes _ped_sel)
    _ped_editing = None              # on-canvas text editor state dict, or None
    _ped_space = False               # Space held: any drag pans
    _ped_guides = []                 # active snap guides [(orient, page_pos)] while dragging
    _ped_snap_info = []              # v15.12: what each active guide snapped to, for its label
    _ped_marquee = None              # (x0, y0, x1, y1) page points while rubber-banding
    _ped_item_cids = {}              # item uid -> [canvas ids] from the last overlay draw
    _ped_rail_on = True              # left tool rail visible
    _ped_panel_on = True             # right properties/objects panel visible
    _ped_layout_cache = None         # (key, layout) memo for _ped_layout()
    _ped_wheel_job = None            # pending coalesced wheel-zoom
    _ped_nudge_mark = (0.0, ())      # (time, uids) for arrow-key undo coalescing

    # ------------------------------------------------------------ entering / leaving
    def _ped_session_is_pristine(self, sess):
        """True when a session holds nothing worth keeping: no items, no card
        words, no pages from a file, and no unsaved edits.  Opening a PDF over
        such a session replaces it; anything else gets the new PDF appended."""
        try:
            if sess is None:
                return True
            if getattr(sess, "dirty", False):
                return False
            for p in sess.pages:
                if getattr(p, "items", None):
                    return False
                if p.card is not None and p.card.get("words"):
                    return False
                if getattr(p, "src", -1) is not None and p.src >= 0:
                    return False
            return True
        except Exception:
            return False

    def _ped_open_dialog(self):
        """'Open…' in the card view (and 'Open' inside the editor).

        v15.57: opening a PDF into a document you already have no longer
        adds its pages to the document — the uploaded pages are laid on
        top of the page you are working on as picture objects, exactly as
        right-click 'Place this page as a picture' does, so nothing new
        appears in the page list.  Only a fresh, empty document is
        replaced by the opened PDF's pages — and 'Close doc' still gives
        a clean slate first when a real replacement is what you want.
        v15.63: pictures open too — a picture file lands on the current
        page as a picture object (fitted inside the page, centred), the
        way 'Replace picture…' already worked for a single object.
        """
        if not aac_import.pdf_supported():
            try:
                require_fitz()
            except PdfEditError as e:
                messagebox.showinfo(_pal().APP_NAME, str(e))
            return
        if self.busy:
            self.flash_status("Still working on the last task — one moment…", ok=False)
            return
        path = filedialog.askopenfilename(
            title="Open a PDF or picture", initialdir=self.last_dir or None,
            filetypes=[("PDF files", "*.pdf"),
                       ("Picture files", "*.png *.jpg *.jpeg *.gif *.webp *.bmp"),
                       ("All files", "*.*")])
        if not path:
            return
        self.last_dir = str(Path(path).parent)
        if Path(path).suffix.lower() in (".png", ".jpg", ".jpeg", ".gif",
                                          ".webp", ".bmp"):
            # v15.63: a picture never replaces the document — it lands on
            # the current page as an object, one undo step, like a lay-on-top.
            try:
                img = Image.open(path).convert("RGBA")
            except Exception as ex:
                self.flash_status(f"Couldn't open that picture: {ex}", ok=False)
                return
            if self._ped_entry() is None:
                self._ped_ensure_session()
            e = self._ped_entry()
            if e is None:
                self.flash_status("Couldn't open that picture: no page to put it on",
                                  ok=False)
                return
            W, H = e.size
            iw, ih = img.size
            f = min(1.0, (W - 48) / max(iw, 1), (H - 48) / max(ih, 1))
            w, h = max(8.0, iw * f), max(8.0, ih * f)
            self._ped.push_undo()
            pic = Item("image", W / 2 - w / 2, H / 2 - h / 2,
                       W / 2 + w / 2, H / 2 + h / 2, img=img, name=Path(path).stem)
            e.items.append(pic)
            self._ped.dirty = True
            self._ped_set_selection([pic], primary=pic)
            self._ped_draw_overlay()
            self._ped_objs_refresh()
            self.flash_status(f"Placed “{Path(path).name}” on this page — drag it anywhere")
            return
        cur = self._ped
        append = cur is not None and not self._ped_session_is_pristine(cur)
        if not append and cur is not None and cur.dirty and not messagebox.askyesno(
                _pal().APP_NAME, "You have unsaved edits to the PDF that's open.\\n\\nOpen a different PDF "
                                 "and discard them?"):
            return
        if append:
            def work():
                try:
                    sid, entries = cur.load_source(path)
                    out = []
                    for e in entries:
                        W, H = e.size
                        scale = 2000.0 / max(W, H)
                        out.append((W, H, cur.render_page(e.key[0], e.key[1], e.key[2], scale)))
                    # The uploaded pages never join the document — only
                    # their pictures do — so drop the source again.  It
                    # was appended last, so no live page can reference it;
                    # if that check ever fails, leaving it registered is
                    # harmless (it closes with the session).
                    with cur.lock:
                        if len(cur.sources) == sid + 1 and cur.sources[sid]["path"] == Path(path):
                            try:
                                cur.sources[sid]["doc"].close()
                            except Exception:
                                pass
                            cur.sources.pop()
                    return out
                except PdfEditError as e:
                    return str(e)

            def done(res):
                if isinstance(res, str):
                    self.flash_status(res, ok=False, hold=True)
                    messagebox.showwarning(_pal().APP_NAME, res)
                    return
                if cur is not self._ped:
                    return
                e = self._ped_entry()
                if e is None:
                    return
                # One undo step for the whole upload, like _ped_put's
                # effects batched: the pictures land on the page being
                # worked on at natural size, staggered so a multi-page
                # upload does not stack invisibly.
                self._ped.push_undo()
                pics = []
                for k, (W, H, img) in enumerate(res):
                    pic = Item("image", 24.0 * k, 24.0 * k,
                               24.0 * k + float(W), 24.0 * k + float(H),
                               img=img.convert("RGBA"), name="PDF page")
                    e.items.append(pic)
                    pics.append(pic)
                self._ped.dirty = True
                if pics:
                    self._ped_sel = pics[-1]
                    self._ped_selset = [pics[-1]]
                self._ped_draw_overlay()
                self._ped_sync_props()
                self._ped_strip_draw()
                n = len(res)
                self.flash_status(f"Laid {n} page(s) from {Path(path).name} on this page "
                                  f"as pictures — drag them anywhere, drag corners to resize")
            self.bg("Opening PDF…", work, done)
            return

        def work():
            try:
                return Session.open(path)
            except PdfEditError as e:
                return str(e)

        def done(res):
            if isinstance(res, str):
                self.flash_status(res, ok=False, hold=True)
                messagebox.showwarning(_pal().APP_NAME, res)
                return
            self._ped_start(res)
        self.bg("Opening PDF…", work, done)

    def _ped_start(self, sess):
        old = self._ped
        self._ped = sess
        self._ped_project_path = None  # v15.63: a fresh/PDF document is not a project until saved/opened as one
        if old is not None and old is not sess:
            threading.Thread(target=old.close, daemon=True).start()
        self._ped_cur, self._ped_zoom, self._ped_pan, self._ped_sel = 0, 1.0, [0.0, 0.0], None
        self._ped_selset = []              # v15.9: multi-selection
        self._ped_editing = None           # v15.9: no on-canvas text edit in progress
        self._ped_card_sel = None          # v15.7: selected card slot on a card page
        self._ped_place_word = None        # v15.7: word-box placement armed for the next page click
        self._ped_harvest_seq = 0          # v15.8: auto-names for harvested pictures
        self._ped_page = None              # {"key", "scale", "img", "photo", "psize"}
        self._ped_pending = None
        self._ped_page2 = None             # v15.53: second-page render slot, same shape as _ped_page
        self._ped_pending2 = None
        self._ped_row_imgs = {}            # v15.65: row-view render cache, (page key, scale) -> slot
        self._ped_row_hover = None         # v15.65: row page idx hovered during a move drag
        self._ped_row_focused_this_press = False  # v15.65: click changed focus; scroll on release
        self._ped_token2 = 0
        self._ped_thumbs, self._ped_thumb_photos, self._ped_card_photos = {}, {}, {}
        self._ped_thumb_req = set()
        self._ped_token = getattr(self, "_ped_token", 0) + 1
        self._ped_active = True
        self._ped_ensure_ui()
        self._ped_show()
        for _e in sess.pages:
            if _e.card is not None:
                self._ped_refresh_card_page(_e)

    def _ped_close_doc(self):
        """v15.6: there is no separate card view to go back to — closing a document
        starts a fresh set of card pages."""
        if self._ped is not None and self._ped.dirty and not messagebox.askyesno(
                _pal().APP_NAME, "You have unsaved edits.\n\nClose this document and start "
                                 "a fresh set of card pages? The edits will be lost."):
            return
        sess, self._ped = self._ped, None
        self._ped_active = False
        self._ped_token = getattr(self, "_ped_token", 0) + 1
        self._ped_thumbs, self._ped_thumb_photos, self._ped_page = {}, {}, None
        self._ped_page2 = None
        self._ped_pending2 = None
        if sess is not None:
            threading.Thread(target=sess.close, daemon=True).start()
        self._ped_start(self._ped_new_card_session())
        self.flash_status("New card pages — add words from the Cards section")

    def _ped_ensure_session(self):
        """v15.6: PDF Studio always has a document. The first visit creates card
        pages from the current word list; a pristine (unedited) card document
        keeps following the app word list (Library / AI sends land there)."""
        if self._ped is None:
            self._ped_start(self._ped_new_card_session())
            return
        s = self._ped
        if not s.dirty and not s.sources and s.pages and s.pages[0].card is not None:
            words = [str(w) for w in getattr(self, "_pdf_words", [])]
            if s.pages[0].card.get("words") != words or len(s.pages) != len(self._ped_chunk_words(words, s.pages[0].card)):
                fresh = self._ped_new_card_session()
                s.pages = fresh.pages
                s._undo.clear()
                s._redo.clear()
                self._ped_cur, self._ped_sel = 0, None
                for e in s.pages:
                    if e.card is not None:
                        self._ped_refresh_card_page(e)
                self._ped_changed()

    def _ped_on_tab_change(self):
        """Called first thing from on_workspace_change: the page strip only belongs to PDF Studio."""
        if not self._ped_ui_built:
            return
        self._ped_apply_chrome()
        if self._ped_active and self.tabs.get() == "PDF Studio":
            self._ped_strip.grid()
        else:
            self._ped_strip.grid_remove()

    def _ped_apply_chrome(self):
        """v15.5: while the editor is open on the PDF Studio tab, the ribbon sits above
        the page and the page-nav/zoom bar below it (the main column's rows shift);
        everywhere else the app keeps its usual layout.

        v15.11: the editor also takes the WHOLE window — the sidebar column
        (whose old editor panel is gone, leaving a blank bar) is hidden and the
        main column spans both root columns.  Tab switching from the editor
        goes through the rail's ▦ button, and leaving the editor restores the
        sidebar exactly as it was.
        """
        if not self._ped_ui_built:
            return
        on = self._ped_active and self.tabs.get() == "PDF Studio"
        main = self.main
        top = self.word_menu.master
        foot = self.status.master
        self._ped_place_furniture(on)
        try:
            if on:
                self.side.grid_remove()
                main.grid(row=0, column=0, columnspan=2, sticky="nsew",
                          padx=16, pady=14)
            else:
                self.side.grid()
                main.grid(row=0, column=1, columnspan=1, sticky="nsew",
                          padx=16, pady=14)
        except Exception:
            pass
        try:
            if on:
                # v15.14: the old top bar (word menu / picture picker / Log)
                # is empty in the editor — every control in it was packed
                # away — so it no longer takes a whole blank row.  Log and
                # Settings live in the ribbon's Home tab instead.
                top.grid_remove()
                self._ped_tabs_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
                self._ped_ribbon.grid(row=1, column=0, sticky="ew", pady=(0, 6))
                self.canvas.grid(row=2, column=0, sticky="nsew", pady=0)
                self._ped_strip.grid(row=2, column=1, sticky="ns", padx=(8, 0), pady=12)
                self._ped_bar.grid(row=3, column=0, sticky="ew", pady=(6, 0))
                self.info.grid(row=4, column=0, sticky="ew")
                # v15.16: Save PDF lives in the bottom bar now, so the old
                # Save row is hidden in the editor (it comes back, gridded
                # below, on every other tab).
                self.export_btn.grid_remove()
                self.progress.grid(row=5, column=0, sticky="ew")
                foot.grid(row=6, column=0, sticky="ew")
                main.grid_rowconfigure(2, weight=1)
                main.grid_rowconfigure(1, weight=0)  # v15.22: ribbon is
                # fixed-height; only the canvas row takes the slack, so
                # the editor area expands up into the old blank bands.
                main.grid_rowconfigure(3, weight=0)
                try:
                    self.after(70, self._ped_draw)   # redraw once the rows have settled
                except Exception:
                    pass
            else:
                self._ped_tabs_row.grid_remove()
                self._ped_ribbon.grid_remove()
                self._ped_bar.grid_remove()
                top.grid(row=0, column=0, sticky="ew")
                self.canvas.grid(row=1, column=0, sticky="nsew", pady=12)
                self._ped_strip.grid(row=2, column=1, sticky="ns", padx=(8, 0), pady=12)
                self.info.grid(row=2, column=0, sticky="ew")
                self.export_btn.grid(row=3, column=0, sticky="ew", pady=(10, 6))
                self.progress.grid(row=4, column=0, sticky="ew")
                foot.grid(row=5, column=0, sticky="ew")
                main.grid_rowconfigure(2, weight=0)
                main.grid_rowconfigure(3, weight=0)
                main.grid_rowconfigure(1, weight=1)
        except Exception:
            pass
        self._ped_sync_tabs_row()

    def _ped_sync_tabs_row(self):
        """v15.13: the editor's tab bar mirrors the workspace tab — the current
        tab reads purple and can't be re-tapped, the rest are plain."""
        if not getattr(self, "_ped_ui_built", False):
            return
        try:
            cur = self.tabs.get()
        except Exception:
            return
        for name, b in getattr(self, "_ped_tabs_btns", {}).items():
            try:
                if name == cur:
                    b.configure(fg_color=self.PED_PURPLE, state="disabled")
                else:
                    b.configure(fg_color="transparent", state="normal")
            except Exception:
                pass

    def _ped_show(self):
        """Switch the PDF Studio tab to Viewer/Editor layout."""
        G = _pal()
        self._pdf_cards_sc.pack_forget()
        # v15.10: the old sidebar panel stays hidden in the editor — every
        # control it had now lives in the rail + Properties panel (Cody:
        # "get rid of it, everything should be moved to the new thing").
        # The widgets are still built (other code references them); they
        # are simply never shown.
        self._ped_panel.pack_forget()
        self._ped_strip.grid(row=2, column=1, sticky="ns", padx=(8, 0), pady=12)
        self._ped_apply_chrome()
        self.word_menu.pack_forget()
        self.pic_pick_btn.pack_forget()
        self.log_btn.pack_forget()
        self.pdf_sel_bar.pack_forget()
        self.export_btn.configure(text="Save PDF…", command=self._ped_save)
        self._ped_refresh_words()
        self._ped_file_lbl.configure(text=f"{self._ped.title}")
        self.info.configure(text="PDF Editor · pick a tool in the ribbon · drag on the page to draw · "
                                 "click an item to select it · drag corners/edges to resize · scroll to zoom")
        self._ped_changed()

    # ------------------------------------------------------------ sidebar + strip
    def _ped_ensure_ui(self):
        if self._ped_ui_built:
            return
        G = _pal()
        tab = self.tabs.tab("PDF Studio")
        P = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        self._ped_panel = P
        self.header(P, "PDF Studio", first=True)
        self._ped_file_lbl = ctk.CTkLabel(P, text="", anchor="w", text_color="gray", wraplength=330,
                                          justify="left")
        self._ped_file_lbl.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(P, text="Your original file is never changed — Save PDF writes a new one.",
                     anchor="w", text_color="gray", font=ctk.CTkFont(size=10), wraplength=330,
                     justify="left").pack(fill="x")

        self.header(P, "Cards")
        self._ped_cardword_entry = ctk.CTkEntry(P, placeholder_text="Type words…  (Enter adds them)")
        self._ped_cardword_entry.pack(fill="x", pady=(2, 0))
        self._ped_cardword_entry.bind("<Return>", lambda e: self._ped_card_add_words())
        ctk.CTkButton(P, text="＋ Add to card page", fg_color=G.TEAL, hover_color=G.TEAL_HOVER,
                      text_color="white", command=self._ped_card_add_words).pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(P, text="Word box — load a list or preset, then click a word to put it "
                             "on the card page.", anchor="w", text_color="gray",
                     font=ctk.CTkFont(size=10), wraplength=330, justify="left").pack(fill="x", pady=(4, 0))
        self._ped_box_menu = ctk.CTkOptionMenu(
            P, values=["Load words from…"] + self._maker_list_choices(),
            variable=self.pdf_preset_var, command=self._ped_box_preset_picked2)
        self._ped_box_menu.pack(fill="x", pady=(2, 2))
        _box_wrap = ctk.CTkFrame(P, height=148, fg_color="transparent")
        _box_wrap.pack(fill="x", pady=(2, 2))
        _box_wrap.pack_propagate(False)
        self._ped_box_frame = ctk.CTkScrollableFrame(_box_wrap, fg_color="transparent")
        self._ped_box_frame.pack(fill="both", expand=True)
        self._ped_box_inner = ctk.CTkFrame(self._ped_box_frame, fg_color="transparent")
        self._ped_box_inner.pack(fill="x", anchor="n")
        self._ped_render_box_tiles()
        ctk.CTkButton(P, text="Fill cards from Word Lists", height=28, fg_color="#2f6fb3",
                      hover_color="#3d7fc7", text_color="white",
                      command=self._ped_fill_from_word_bank).pack(fill="x", pady=(4, 0))
        self._ped_newpage_btn = ctk.CTkButton(P, text="＋ New card page", height=28,
                                              fg_color="gray30", hover_color="#555",
                                              command=self._ped_add_card_page)
        self._ped_newpage_btn.pack(fill="x", pady=(2, 0))
        self._ped_cardsel_lbl = ctk.CTkLabel(P, text="Selected card: click a card on the page",
                                             anchor="w", text_color="gray",
                                             font=ctk.CTkFont(size=11), wraplength=330,
                                             justify="left")
        self._ped_cardsel_lbl.pack(fill="x", pady=(6, 0))
        _csr = ctk.CTkFrame(P, fg_color="transparent")
        _csr.pack(fill="x", pady=(2, 0))
        self._ped_cardsel_send = ctk.CTkButton(_csr, text="Move word to page…", height=26,
                                               fg_color="gray30", hover_color="#555",
                                               command=self._ped_card_send_menu)
        self._ped_cardsel_send.pack(side="left", fill="x", expand=True)
        self._ped_cardsel_del = ctk.CTkButton(_csr, text="Delete word", height=26, width=96,
                                              fg_color="gray30", hover_color="#555",
                                              command=self._ped_card_delete)
        self._ped_cardsel_del.pack(side="left", padx=(6, 0))

        # Page & cards options — packed in place, shown only on card pages
        pg = ctk.CTkFrame(P, fg_color="transparent")
        self._ped_pgframe = pg
        self.header(pg, "Page & cards")
        ctk.CTkLabel(pg, text="Cards per page", anchor="w").pack(fill="x")
        ctk.CTkOptionMenu(pg, values=list(G.aac_boards.PECS_LAYOUTS.keys()),
                          variable=self.pdf_layout_var,
                          command=lambda _: self._ped_card_opt_now()).pack(fill="x", pady=(2, 4))
        ctk.CTkLabel(pg, text="Paper", anchor="w").pack(fill="x")
        ctk.CTkOptionMenu(pg, values=["letter", "A4"], variable=self.pdf_paper,
                          command=lambda _: self._ped_card_opt_now()).pack(fill="x", pady=(2, 4))
        # v15.13: the four size/position sliders are gone (Cody: left over
        # from the old preview, and they don't change anything visible) —
        # layout, paper and the toggles stay, and card options are also on
        # the right-click menu now.
        ctk.CTkCheckBox(pg, text="Show pictures", variable=self.pdf_show_pics,
                        command=self._ped_card_opt_now).pack(anchor="w", pady=(4, 0))
        ctk.CTkCheckBox(pg, text="Show words", variable=self.pdf_show_words,
                        command=self._ped_card_opt_now).pack(anchor="w", pady=2)
        ctk.CTkCheckBox(pg, text="Category-colour card backgrounds", variable=self.pdf_colors_var,
                        command=self._ped_card_opt_now).pack(anchor="w", pady=2)

        self.header(P, "Stamp on this page")
        self._ped_word_var = tk.StringVar(value="")
        self._ped_word_combo = ctk.CTkComboBox(P, variable=self._ped_word_var, values=[" "])
        self._ped_word_combo.pack(fill="x", pady=(2, 0))
        self._ped_word_combo.bind("<Return>", lambda e: self._ped_add_card())
        ctk.CTkButton(P, text="＋ Picture card", fg_color=G.ACCENT, hover_color=G.ACCENT_HOVER,
                      text_color="white", command=self._ped_add_card).pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(P, text="Pick or type a word — it uses the same pictures and colours as the card view.",
                     anchor="w", text_color="gray", font=ctk.CTkFont(size=10), wraplength=330,
                     justify="left").pack(fill="x")
        self._ped_text_entry = ctk.CTkEntry(P, placeholder_text="Type a text label…")
        self._ped_text_entry.pack(fill="x", pady=(8, 0))
        self._ped_text_entry.bind("<Return>", lambda e: self._ped_add_text())
        ctk.CTkButton(P, text="＋ Text label", fg_color=G.TEAL, hover_color=G.TEAL_HOVER,
                      text_color="white", command=self._ped_add_text).pack(fill="x", pady=(6, 0))
        ctk.CTkButton(P, text="＋ Highlight box", fg_color=G.TEAL, hover_color=G.TEAL_HOVER,
                      text_color="white", command=self._ped_add_rect).pack(fill="x", pady=(6, 0))

        self.header(P, "Selected item")
        self._ped_sel_lbl = ctk.CTkLabel(P, text="Nothing selected", anchor="w", text_color=G.ACCENT)
        self._ped_sel_lbl.pack(fill="x")
        sr = ctk.CTkFrame(P, fg_color="transparent")
        sr.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(sr, text="Text size", anchor="w").pack(side="left")
        self._ped_size_val = ctk.CTkLabel(sr, text="18 pt", width=48, anchor="e")
        self._ped_size_val.pack(side="right")
        self._ped_size_var = tk.DoubleVar(value=18.0)
        ctk.CTkSlider(sr, from_=6, to=96, number_of_steps=90, variable=self._ped_size_var,
                      command=self._ped_size_changed).pack(side="left", fill="x", expand=True, padx=8)
        self._ped_color_btn = ctk.CTkButton(P, text="Colour…", height=28, fg_color="gray30",
                                            hover_color="#555", command=self._ped_pick_color)
        self._ped_color_btn.pack(fill="x", pady=(4, 0))
        self._ped_label_var = tk.BooleanVar(value=True)
        self._ped_label_chk = ctk.CTkCheckBox(P, text="Show word under picture", variable=self._ped_label_var,
                                              command=self._ped_label_toggled)
        self._ped_label_chk.pack(anchor="w", pady=(6, 0))
        self._ped_see_var = tk.BooleanVar(value=True)
        self._ped_see_chk = ctk.CTkCheckBox(P, text="See-through (highlight)", variable=self._ped_see_var,
                                            command=self._ped_see_toggled)
        self._ped_see_chk.pack(anchor="w", pady=(6, 0))
        ctk.CTkButton(P, text="Delete item", height=28, fg_color="gray30", hover_color="#555",
                      command=self._ped_delete_item).pack(fill="x", pady=(8, 0))
        # v15.5: text formatting (also the defaults for the Text box tool)
        tf = ctk.CTkFrame(P, fg_color="transparent")
        self._ped_fmt_frame = tf
        self._ped_font_var = tk.StringVar(value="Helvetica")
        ctk.CTkOptionMenu(tf, variable=self._ped_font_var, values=["Helvetica", "Times", "Courier"],
                          width=150, height=26, command=lambda v: self._ped_fmt_changed("font", v)
                          ).pack(anchor="w", pady=(8, 0))
        fr = ctk.CTkFrame(tf, fg_color="transparent")
        fr.pack(fill="x", pady=(4, 0))
        self._ped_bold_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(fr, text="Bold", variable=self._ped_bold_var, width=64,
                        command=lambda: self._ped_fmt_changed("bold", self._ped_bold_var.get())
                        ).pack(side="left")
        self._ped_italic_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(fr, text="Italic", variable=self._ped_italic_var, width=64,
                        command=lambda: self._ped_fmt_changed("italic", self._ped_italic_var.get())
                        ).pack(side="left")
        self._ped_align_var = tk.StringVar(value="Left")
        ctk.CTkOptionMenu(tf, variable=self._ped_align_var, values=["Left", "Center", "Right"],
                          width=110, height=26,
                          command=lambda v: self._ped_fmt_changed("align", v)).pack(anchor="w", pady=(4, 0))
        # v15.5: shape colours (mirror the ribbon Shapes controls)
        sf = ctk.CTkFrame(P, fg_color="transparent")
        self._ped_shape_frame = sf
        sr2 = ctk.CTkFrame(sf, fg_color="transparent")
        sr2.pack(fill="x", pady=(8, 0))
        ctk.CTkButton(sr2, text="Fill colour…", height=26, fg_color="gray30", hover_color="#555",
                      command=lambda: self._ped_pick_shape_color(True)).pack(side="left", fill="x",
                                                                             expand=True, padx=(0, 3))
        ctk.CTkButton(sr2, text="Line colour…", height=26, fg_color="gray30", hover_color="#555",
                      command=lambda: self._ped_pick_shape_color(False)).pack(side="left", fill="x",
                                                                              expand=True)
        self._ped_nofill_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(sf, text="No fill (outline only)", variable=self._ped_nofill_var,
                        command=self._ped_nofill_toggled).pack(anchor="w", pady=(4, 0))
        # v15.5: picture actions
        imf = ctk.CTkFrame(P, fg_color="transparent")
        self._ped_img_frame = imf
        ir = ctk.CTkFrame(imf, fg_color="transparent")
        ir.pack(fill="x", pady=(8, 0))
        ctk.CTkButton(ir, text="Replace…", height=26, fg_color="gray30", hover_color="#555",
                      command=self._ped_replace_image).pack(side="left", fill="x", expand=True,
                                                            padx=(0, 3))
        ctk.CTkButton(ir, text="Crop on/off", height=26, fg_color="gray30", hover_color="#555",
                      command=self._ped_toggle_crop).pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(P, text="Double-click a text label to edit it, or a picture card to change its word.",
                     anchor="w", text_color="gray", font=ctk.CTkFont(size=10), wraplength=330,
                     justify="left").pack(fill="x", pady=(6, 0))


        # ---- v15.13 main-tab bar: in the full-window editor the sidebar (and
        # its tab buttons) is hidden, so the five workspace tabs get their own
        # row above the ribbon — one tap to switch, like it used to be.
        trow = ctk.CTkFrame(self.main, fg_color="transparent")
        self._ped_tabs_row = trow
        self._ped_tabs_btns = {}
        # v15.22: equal spacers either side center the tab buttons in the row.
        ctk.CTkFrame(trow, fg_color="transparent", height=1).pack(side="left", fill="x", expand=True)  # height=1: a default frame is 200 tall and would blow the row up
        self._ped_tabs_spacer_r = ctk.CTkFrame(trow, fg_color="transparent", height=1)
        for name in ("Maker Studio", "Word Lists", "Tag Design", "PDF Studio", "AI (Local)"):
            b = ctk.CTkButton(trow, text=name, width=104, height=24, fg_color="transparent",
                              hover_color=("#EAE2F2", "#3A3550"),
                              text_color=("#222222", "#e8ebf0"),
                              font=ctk.CTkFont(size=12, weight="bold"),
                              command=lambda n=name: self.goto_tab(n))
            b.pack(side="left", padx=1)
            self._ped_tabs_btns[name] = b
        self._ped_tabs_spacer_r.pack(side="left", fill="x", expand=True)
        # ---- v15.5 ribbon (above the page) + page bar (below the page), in the main column
        G2 = _pal()
        rib = ctk.CTkFrame(self.main, fg_color=("#EEEAF2", "#252830"), corner_radius=8)
        self._ped_ribbon = rib
        tabs_row = ctk.CTkFrame(rib, fg_color="transparent")
        tabs_row.pack(anchor="center", padx=6, pady=(4, 0))  # v15.22: centered
        self._ped_rib_btns = {}
        for name in ("Home", "Edit", "Images", "Shapes", "Pages", "Tools"):
            b = ctk.CTkButton(tabs_row, text=name, width=64, height=22, fg_color="transparent",
                              hover_color=("#EAE2F2", "#3A3550"), text_color=("#222222", "#e8ebf0"),
                              command=lambda n=name: self._ped_rib_show(n))
            b.pack(side="left", padx=1)
            self._ped_rib_btns[name] = b
        self._ped_rib_body = ctk.CTkFrame(rib, fg_color="transparent")
        self._ped_rib_body.pack(fill="x", padx=6, pady=(2, 5))
        self._ped_tool_btns = {}

        def grp():
            f = ctk.CTkFrame(self._ped_rib_body, fg_color="transparent")
            return f

        def btn(parent, text, cmd, w=86, tool=None, tip=None):
            # v15.21: white in light mode (like the preview), gray30 in
            # dark mode — the old solid gray30 hid the pastel in light.
            b = ctk.CTkButton(parent, text=text, width=w, height=24,
                              fg_color=("white", "gray30"),
                              hover_color=("#EAE2F2", "#555555"),
                              text_color=("#222222", "#FFFFFF"), command=cmd)
            b.pack(side="left", padx=2)
            if tool:
                self._ped_tool_btns[tool] = b
            self._ped_tip(b, tip or text)    # v15.10: every ribbon button explains itself on hover
            return b

        self._ped_rib_groups = {}
        g = grp(); self._ped_rib_groups["Home"] = g
        # v15.16: Home's first row is the everyday row — document, undo,
        # then the drawing tools in the same order as the left rail, so the
        # two agree.  Nothing is removed: markup tools and Print live in
        # the second row below, and every other tab is untouched.
        hrow = ctk.CTkFrame(g, fg_color="transparent")
        hrow.pack(anchor="center")  # v15.22: Home row 1 centered
        # v15.17: icon + label, like the target screenshot's ribbon, and a
        # touch taller than the old 24 px buttons so the row reads at a
        # glance instead of as a strip of small gray pills.
        btn(hrow, "＋\nNew", self._ped_close_doc, w=64, tip="Start a new document")
        btn(hrow, "▢\nOpen", self._ped_open_dialog, w=72, tip="Open a PDF or picture to edit")
        btn(hrow, "💾\nSave", lambda: self._ped_save(), w=72, tip="Save this document as a new PDF")
        btn(hrow, "↩\nUndo", self._ped_undo, w=68)
        btn(hrow, "↪\nRedo", self._ped_redo, w=68)
        btn(hrow, "➤\nSelect", lambda: self._ped_set_tool("select"), w=80, tool="select")
        btn(hrow, "T\nText", lambda: self._ped_toggle_tool("text"), w=64, tool="text")
        btn(hrow, "▣\nImage", self._ped_import_image, w=76, tip="Insert a picture from a file onto the page")
        btn(hrow, "◇\nShape", lambda: self._ped_toggle_tool("polygon"), w=76, tool="polygon",
            tip="Shape — click each corner, double-click to finish")
        btn(hrow, "▭\nRectangle", lambda: self._ped_toggle_tool("rect"), w=104, tool="rect")
        btn(hrow, "◯\nCircle", lambda: self._ped_toggle_tool("circle"), w=76, tool="circle")
        btn(hrow, "╱\nLine", lambda: self._ped_toggle_tool("line"), w=64, tool="line")
        btn(hrow, "⟳\nRotate", self._ped_rotate_item, w=80, tip="Rotate the selected object 90°")
        btn(hrow, "✥\nMove", lambda: self._ped_toggle_tool("pan"), w=68, tool="pan",
            tip="Move — drag to pan around the page (H)")
        btn(hrow, "⌫\nDelete", self._ped_delete_item, w=80)
        # v15.20: taller again — icon over label needs the second line.
        for _ch in hrow.winfo_children():
            try:
                _ch.configure(height=44)
            except Exception:
                pass
        # v15.13: Comment is folded into Home — one section, one label.  The
        # buttons below pack into Home's second row (no new group is started).
        hrow2 = ctk.CTkFrame(g, fg_color="transparent")
        hrow2.pack(anchor="center", pady=(4, 0))  # v15.22: Home row 2 centered
        btn(hrow2, "Print…", self._ped_print, w=64)
        btn(hrow2, "Highlight", lambda: self._ped_toggle_tool("highlight"), tool="highlight")
        btn(hrow2, "Underline", lambda: self._ped_toggle_tool("underline"), tool="underline")
        btn(hrow2, "Strikethrough", lambda: self._ped_toggle_tool("strike"), w=104, tool="strike")
        btn(hrow2, "Freehand", lambda: self._ped_toggle_tool("freehand"), tool="freehand")
        # (no tool= here: Home's first-row Text button already owns the
        # "text" highlight, and the registry holds one button per tool)
        btn(hrow2, "Text box", lambda: self._ped_toggle_tool("text"))
        btn(hrow2, "Note", self._ped_add_note, w=56)
        # v15.53: cut and snip also live on Home, so they are one tap away
        # instead of a tab switch (the Images/Tools copies are untouched).
        self._ped_home_tool_btns = {}
        _hb = btn(hrow2, "✂ Save picture", lambda: self._ped_toggle_tool("harvest_pic"), w=104,
                  tip="Save picture: drag around a picture on an opened PDF page to save it into your picture Library")
        self._ped_home_tool_btns["harvest_pic"] = _hb
        _hb = btn(hrow2, "✂ Save words", lambda: self._ped_toggle_tool("harvest_words"), w=96,
                  tip="Save words: drag around words on an opened PDF page to add them to your Word Lists")
        self._ped_home_tool_btns["harvest_words"] = _hb
        _hb = btn(hrow2, "✂ Snip", lambda: self._ped_toggle_tool("snip"), w=72,
                  tip="Snip: drag around anything on the page, then save it as a picture, as words, or both")
        self._ped_home_tool_btns["snip"] = _hb
        self._ped_stamp_menu = ctk.CTkOptionMenu(hrow2, values=["Stamp…", "★ Great job!", "✓ Done",
                                                            "★ Super!", "★ Excellent!", "★ Awesome!",
                                                            "★ Well done!", "★ Nice work!", "★ You did it!",
                                                            "★ Keep it up!",
                                                            "Name: ____________", "Date: ____________"],
                                                 width=104, height=24, fg_color="gray30",
                                                 button_color="gray30", command=self._ped_add_stamp)
        self._ped_stamp_menu.pack(side="left", padx=2)
        self._ped_stamp_menu.set("Stamp…")
        # v15.14: Log and Settings used to sit in the top bar, which the
        # editor now hides — they live at the end of Home instead, so the
        # log file is reachable from inside PDF Studio too.
                # v15.21: Log/Settings belong in row 2 — packed into the group
        # frame they wrapped onto a third ribbon row on real windows.
        self._ped_log_btn = btn(hrow2, "Log", self.open_log, w=56)
        self._ped_tip(self._ped_log_btn, "Log & diagnostics — the app's log file, with Copy all and Open log folder")
        btn(hrow2, "⚙", self.open_settings, w=36, tip="Settings")

        g = grp(); self._ped_rib_groups["Edit"] = g
        erow = ctk.CTkFrame(g, fg_color="transparent")
        erow.pack(fill="x")
        btn(erow, "Select", lambda: self._ped_set_tool("select"), w=64, tool="select")
        btn(erow, "Duplicate", self._ped_duplicate_item)
        btn(erow, "Copy", self._ped_copy, w=56)
        btn(erow, "Paste", self._ped_paste, w=56)
        btn(erow, "Delete", self._ped_delete_item, w=64)
        btn(erow, "Rotate ⟳", self._ped_rotate_item, w=78)
        btn(erow, "Forward", lambda: self._ped_layer(True), w=70)
        btn(erow, "Backward", lambda: self._ped_layer(False), w=76)
        self._ped_align_menu = ctk.CTkOptionMenu(erow, values=["Align…", "Left", "Center", "Right",
                                                               "Top", "Middle", "Bottom"],
                                                 width=84, height=24, fg_color="gray30",
                                                 button_color="gray30", command=self._ped_align_pick)
        self._ped_align_menu.pack(side="left", padx=2)
        self._ped_align_menu.set("Align…")
        # v15.12: Cards & pages — moved here from below the panel word box,
        # so the panel's word list and objects list have room to grow.
        # v15.14: no heading row and no label row of their own — the
        # selected-card note sits inline in the button row, so the Edit
        # tab is two rows shorter and the page stops jumping when tabs
        # change height.
        eb = ctk.CTkFrame(g, fg_color="transparent")
        eb.pack(fill="x", pady=(6, 0))
        ebrow = ctk.CTkFrame(eb, fg_color="transparent")
        ebrow.pack(fill="x", pady=(3, 0))
        self._ped_rib_cardsel_lbl = ctk.CTkLabel(
            ebrow, text="Selected card: click a card on the page", anchor="w",
            text_color="gray", font=ctk.CTkFont(size=11))
        self._ped_rib_cardsel_lbl.pack(side="left", padx=(0, 8))
        _rb = ctk.CTkButton(ebrow, text="Fill cards from Word Lists", height=24,
                            fg_color="#2f6fb3", hover_color="#3d7fc7", text_color="white",
                            command=self._ped_fill_from_word_bank)
        _rb.pack(side="left", padx=(0, 4))
        self._ped_tip(_rb, "Fill this card page with the words from your Word Lists tab")
        self._ped_rib_cardsel_send = ctk.CTkButton(
            ebrow, text="Move word to page…", height=24, fg_color="gray30", hover_color="#555",
            command=self._ped_card_send_menu)
        self._ped_rib_cardsel_send.pack(side="left", padx=(0, 4))
        self._ped_tip(self._ped_rib_cardsel_send, "Move the selected card's word to another page")
        self._ped_rib_cardsel_del = ctk.CTkButton(
            ebrow, text="Delete word", height=24, width=96, fg_color="gray30", hover_color="#555",
            command=self._ped_card_delete)
        self._ped_rib_cardsel_del.pack(side="left", padx=(0, 8))
        self._ped_tip(self._ped_rib_cardsel_del, "Remove the selected card's word from this page")
        self._ped_rib_word_combo = ctk.CTkComboBox(ebrow, variable=self._ped_word_var,
                                                  values=[" "], width=132, height=24)
        self._ped_rib_word_combo.pack(side="left", padx=(0, 4))
        for target in (self._ped_rib_word_combo, getattr(self._ped_rib_word_combo, "_entry", None)):
            if target is not None:
                target.bind("<Return>", lambda e: self._ped_add_card())
        _rpc = ctk.CTkButton(ebrow, text="＋ Picture card", height=24, width=104,
                             fg_color=self.PED_PURPLE_D, hover_color=self.PED_PURPLE,
                             text_color="white", command=self._ped_add_card)
        _rpc.pack(side="left", padx=(0, 8))
        self._ped_tip(_rpc, "Put a picture card for this word on the page — drag it anywhere")
        _rap = ctk.CTkButton(ebrow, text="＋ Add page", height=24, fg_color=self.PED_PURPLE_D,
                             hover_color=self.PED_PURPLE, text_color="white",
                             command=self._ped_add_page_menu)
        _rap.pack(side="left")
        self._ped_tip(_rap, "Add a blank page (Letter / Legal / A4) or a new card page")
        pgf = ctk.CTkFrame(eb, fg_color="transparent")
        self._ped_rib_pgframe = pgf
        mrow = ctk.CTkFrame(pgf, fg_color="transparent")
        mrow.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(mrow, text="Cards per page", font=ctk.CTkFont(size=10)).pack(side="left")
        _rlm = ctk.CTkOptionMenu(mrow, values=list(_pal().aac_boards.PECS_LAYOUTS.keys()),
                                 variable=self.pdf_layout_var, width=150, height=24,
                                 command=lambda _: self._ped_card_opt_now())
        _rlm.pack(side="left", padx=(4, 12))
        self._ped_tip(_rlm, "How many cards fit on a card page")
        ctk.CTkLabel(mrow, text="Paper", font=ctk.CTkFont(size=10)).pack(side="left")
        _rpm = ctk.CTkOptionMenu(mrow, values=["letter", "A4"], variable=self.pdf_paper,
                                 width=90, height=24,
                                 command=lambda _: self._ped_card_opt_now())
        _rpm.pack(side="left", padx=(4, 12))
        self._ped_tip(_rpm, "Paper size for card pages")
        ctk.CTkCheckBox(mrow, text="Show pictures", variable=self.pdf_show_pics,
                        command=self._ped_card_opt_now).pack(side="left", padx=(0, 8))
        ctk.CTkCheckBox(mrow, text="Show words", variable=self.pdf_show_words,
                        command=self._ped_card_opt_now).pack(side="left", padx=(0, 8))
        ctk.CTkCheckBox(mrow, text="Category-colour backgrounds", variable=self.pdf_colors_var,
                        command=self._ped_card_opt_now).pack(side="left")
        g = grp(); self._ped_rib_groups["Images"] = g
        btn(g, "Import image…", self._ped_import_image, w=104)
        btn(g, "✂ Save picture", lambda: self._ped_toggle_tool("harvest_pic"), w=104,
            tool="harvest_pic",
            tip="Save picture: drag around a picture on an opened PDF page to save it into your picture Library")
        btn(g, "Picture card", self._ped_add_card, w=96)
        btn(g, "Replace…", self._ped_replace_image, w=76)
        self._ped_crop_btn = btn(g, "Crop: off", self._ped_toggle_crop, w=76)
        btn(g, "Rotate ⟳", self._ped_rotate_item, w=78)

        g = grp(); self._ped_rib_groups["Shapes"] = g
        btn(g, "Rectangle", lambda: self._ped_toggle_tool("rect"), tool="rect")
        btn(g, "Oval", lambda: self._ped_toggle_tool("ellipse"), w=60, tool="ellipse")
        btn(g, "Circle", lambda: self._ped_toggle_tool("circle"), w=60, tool="circle")
        btn(g, "Line", lambda: self._ped_toggle_tool("line"), w=52, tool="line")
        btn(g, "Arrow", lambda: self._ped_toggle_tool("arrow"), w=60, tool="arrow")
        btn(g, "Polygon", lambda: self._ped_toggle_tool("polygon"), w=70, tool="polygon")
        btn(g, "Fill…", lambda: self._ped_pick_shape_color(True), w=56)
        btn(g, "Line…", lambda: self._ped_pick_shape_color(False), w=56)
        self._ped_lw_menu = ctk.CTkOptionMenu(g, values=["1 pt", "2 pt", "3 pt", "5 pt", "8 pt"],
                                              width=64, height=24, fg_color="gray30",
                                              button_color="gray30", command=self._ped_lw_pick)
        self._ped_lw_menu.pack(side="left", padx=2)
        self._ped_lw_menu.set("2 pt")

        g = grp(); self._ped_rib_groups["Pages"] = g
        btn(g, "＋ Card page", self._ped_add_card_page, w=96)
        self._ped_blank_menu = ctk.CTkOptionMenu(g, values=["Blank page…", "Letter", "Legal", "A4", "Custom…"],
                                                 width=104, height=24, fg_color="gray30",
                                                 button_color="gray30", command=self._ped_blank_pick)
        self._ped_blank_menu.pack(side="left", padx=2)
        self._ped_blank_menu.set("Blank page…")
        btn(g, "Insert PDF…", self._ped_insert_pdf, w=92)
        self._ped_page_pic_btn = btn(g, "Page as picture", self._ped_place_page_as_picture, w=104,
                                     tip="Place an uploaded PDF page as a picture on a fresh blank page — then move it and drag its corners to resize")
        btn(g, "Delete", lambda: self._ped_op("delete"), w=60)
        btn(g, "Duplicate", lambda: self._ped_op("duplicate"), w=80)
        btn(g, "Rotate ⟲", lambda: self._ped_op("rotate", -90), w=78)
        btn(g, "Rotate ⟳", lambda: self._ped_op("rotate", 90), w=78)
        btn(g, "Move ◀", lambda: self._ped_op("up"), w=66)
        btn(g, "Move ▶", lambda: self._ped_op("down"), w=66)
        btn(g, "Extract page…", lambda: self._ped_save(only_current=True), w=100)
        self._ped_two_btn = btn(g, "Two pages: on", self._ped_toggle_two, w=100,
                                tip="Two pages: show the next page beside this one — drag objects across to move them")
        self._ped_row_btn = btn(g, "All pages: off", self._ped_toggle_row, w=100,
                                tip="All pages: show every page in one row at the same size — click a page to work on it, right-drag to slide the row")

        g = grp(); self._ped_rib_groups["Tools"] = g
        self._ped_pagesize_lbl = ctk.CTkLabel(g, text="", anchor="w", width=210)
        self._ped_pagesize_lbl.pack(side="left", padx=(2, 8))
        btn(g, "✂ Save words", lambda: self._ped_toggle_tool("harvest_words"), w=96,
            tool="harvest_words",
            tip="Save words: drag around words on an opened PDF page to add them to your Word Lists")
        btn(g, "✂ Snip", lambda: self._ped_toggle_tool("snip"), w=72, tool="snip",
            tip="Snip: drag around anything on the page, then save it as a picture, as words, or both")
        btn(g, "Measure", lambda: self._ped_toggle_tool("measure"), w=72, tool="measure")
        self._ped_snap_btn = btn(g, "Snap: off", self._ped_toggle_snap, w=76)
        self._ped_grid_btn = btn(g, "Grid: off", self._ped_toggle_grid, w=76)
        btn(g, "Fit page", self._ped_fit, w=72)
        btn(g, "Zoom 100%", lambda: self._ped_set_zoom(1.0 / max(self._ped_layout()[3], 0.01)), w=84)

        bar = ctk.CTkFrame(self.main, fg_color="transparent")
        self._ped_bar = bar
        _bp = ctk.CTkButton(bar, text="◀", width=36, height=24, fg_color="gray30", hover_color="#555555",
                            command=lambda: self._ped_step(-1))
        _bp.pack(side="left")
        self._ped_tip(_bp, "Previous page")
        self._ped_bar_lbl = ctk.CTkLabel(bar, text="Page 1 of 1", width=110)
        self._ped_bar_lbl.pack(side="left")
        _bn = ctk.CTkButton(bar, text="▶", width=36, height=24, fg_color="gray30", hover_color="#555555",
                            command=lambda: self._ped_step(1))
        _bn.pack(side="left")
        self._ped_tip(_bn, "Next page")
        # v15.10: adding a page gets one clear button, right by the page controls
        self._ped_addpage_btn = ctk.CTkButton(
            bar, text="＋ Add page", width=104, height=24, fg_color=self.PED_PURPLE_D,
            hover_color=self.PED_PURPLE, text_color="white",
            command=self._ped_add_page_menu)
        self._ped_addpage_btn.pack(side="left", padx=(10, 0))
        self._ped_tip(self._ped_addpage_btn,
                      "Add a page: blank page (Letter / Legal / A4) or a new card page")
        # v15.54: Two pages also lives on the bar, so it is visible
        # without opening the Pages ribbon tab.
        self._ped_two_bar_btn = ctk.CTkButton(bar, text="Two pages: on", width=100, height=24,
                                              fg_color="gray30", hover_color="#555555",
                                              command=self._ped_toggle_two)
        self._ped_two_bar_btn.pack(side="left", padx=(10, 0))
        self._ped_tip(self._ped_two_bar_btn,
                      "Two pages: show the next page beside this one — drag objects across to move them")
        self._ped_row_bar_btn = ctk.CTkButton(bar, text="All pages: off", width=100, height=24,
                                              fg_color="gray30", hover_color="#555555",
                                              command=self._ped_toggle_row)
        self._ped_row_bar_btn.pack(side="left", padx=(10, 0))
        self._ped_tip(self._ped_row_bar_btn,
                      "All pages: show every page in one row at the same size — click a page to work on it, right-drag to slide the row")
        # v15.55: two pages is the default — sync both buttons to the toggle.
        self._ped_sync_view_buttons()
        self._ped_measure_lbl = ctk.CTkLabel(bar, text="", anchor="w", text_color="gray")
        self._ped_measure_lbl.pack(side="left", padx=(14, 0))
        # v15.16: the bottom bar is the screenshot's bottom bar — page
        # controls left, Save PDF in the middle, zoom right.  The Save
        # button saves the editor document, like the ribbon's Save —
        # v15.54: it used to call the old word-list export, which refused
        # with "Type at least one word first" on an editor document.
        # v15.20: mint face, dark text — white on mint would wash out.
        self._ped_bar_save = ctk.CTkButton(bar, text="💾  Save PDF", height=28,
                                           fg_color=_pal().TEAL, hover_color=_pal().TEAL_HOVER,
                                           text_color="#1E3A32",
                                           command=self._ped_save)
        self._ped_bar_save.pack(side="left", padx=(20, 0))
        self._ped_tip(self._ped_bar_save, "Save this document as a new PDF — your original file is never changed")
        _bproj = ctk.CTkButton(bar, text="💾  Save project", height=28,
                               fg_color="gray30", hover_color="#555555",
                               command=self._ped_save_project)
        _bproj.pack(side="left", padx=(8, 0))
        self._ped_tip(_bproj, "Save this PDF Studio work so you can open it back up later — pages, objects, and card pages")
        _bprojo = ctk.CTkButton(bar, text="📂  Open project", height=28,
                                fg_color="gray30", hover_color="#555555",
                                command=self._ped_open_project)
        _bprojo.pack(side="left", padx=(8, 0))
        self._ped_tip(_bprojo, "Open a saved PDF Studio project")
        _bf = ctk.CTkButton(bar, text="Fit", width=44, height=24, fg_color="gray30", hover_color="#555555",
                            command=self._ped_fit)
        _bf.pack(side="right")
        self._ped_tip(_bf, "Fit the whole page in the window")
        _bz = ctk.CTkButton(bar, text="+", width=30, height=24, fg_color="gray30", hover_color="#555555",
                            command=lambda: self._ped_set_zoom(self._ped_zoom * 1.2))
        _bz.pack(side="right", padx=(0, 4))
        self._ped_tip(_bz, "Zoom in")
        self._ped_bar_zoom_lbl = ctk.CTkLabel(bar, text="100%", width=52, anchor="e")
        self._ped_bar_zoom_lbl.pack(side="right", padx=4)
        self._ped_bar_zoom = ctk.CTkSlider(bar, from_=25, to=300, number_of_steps=275,
                                           width=150, height=18,
                                           command=self._ped_bar_zoom_changed)
        self._ped_bar_zoom.pack(side="right", padx=4)
        self._ped_tip(self._ped_bar_zoom, "Zoom — drag, or use the + / − buttons")
        _bzo = ctk.CTkButton(bar, text="−", width=30, height=24, fg_color="gray30", hover_color="#555555",
                             command=lambda: self._ped_set_zoom(self._ped_zoom / 1.2))
        _bzo.pack(side="right")
        self._ped_tip(_bzo, "Zoom out")
        self._ped_rib_show("Home")

        # thumbnail strip: right of the page, inside the main column.
        # v15.16: it reads as the screenshot's Pages panel — a header,
        # the thumbnails, and ＋ / 🗑 at the bottom.
        strip = ctk.CTkFrame(self.main, width=134, fg_color="transparent")
        self._ped_strip = strip
        ctk.CTkLabel(strip, text="Pages", font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=self.PED_PURPLE).pack(side="top", fill="x", padx=4, pady=(0, 4))
        self._ped_strip_cv = tk.Canvas(strip, width=124, highlightthickness=0, bd=0, yscrollincrement=38)
        sb = tk.Scrollbar(strip, orient="vertical", command=self._ped_strip_scroll)
        self._ped_strip_cv.configure(yscrollcommand=sb.set)
        self._ped_strip_cv.pack(side="left", fill="y", expand=True)
        sb.pack(side="right", fill="y")
        _sbot = ctk.CTkFrame(strip, fg_color="transparent")
        _sbot.pack(side="bottom", fill="x", pady=(4, 0))
        _sadd = ctk.CTkButton(_sbot, text="＋", width=52, height=26, fg_color=self.PED_PURPLE_D,
                              hover_color=self.PED_PURPLE, text_color="white",
                              command=self._ped_add_page_menu)
        _sadd.pack(side="left", padx=(0, 4))
        self._ped_tip(_sadd, "Add a page: blank page (Letter / Legal / A4) or a new card page")
        _sdel = ctk.CTkButton(_sbot, text="🗑", width=52, height=26, fg_color="gray30",
                              hover_color="#555", command=lambda: self._ped_op("delete"))
        _sdel.pack(side="left")
        self._ped_tip(_sdel, "Delete the current page")
        cv = self._ped_strip_cv
        cv.bind("<Button-1>", self._ped_strip_click)
        cv.bind("<Configure>", lambda e: self._ped_strip_draw())
        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            cv.bind(ev, self._ped_strip_wheel)
        self.canvas.bind("<Delete>", lambda e: self._ped_key(e, "delete"))
        self.canvas.bind("<BackSpace>", lambda e: self._ped_key(e, "delete"))
        self.canvas.bind("<Control-z>", lambda e: self._ped_key(e, "undo"))
        self.canvas.bind("<Control-y>", lambda e: self._ped_key(e, "redo"))
        self.canvas.bind("<Control-Shift-z>", lambda e: self._ped_key(e, "redo"))
        self.canvas.bind("<Control-c>", lambda e: self._ped_key(e, "copy"))
        self.canvas.bind("<Control-x>", lambda e: self._ped_key(e, "cut"))
        self.canvas.bind("<Control-v>", lambda e: self._ped_key(e, "paste"))
        self.canvas.bind("<Escape>", lambda e: self._ped_key(e, "escape"))
        self.canvas.bind("<Return>", lambda e: self._ped_key(e, "finish"))
        # v15.9: Inkscape-style keys — arrows nudge (Shift = 10 pt), Ctrl+A
        # selects the page's objects, Ctrl+D duplicates, Ctrl+G groups,
        # single letters switch tools, Space (held) turns any drag into a pan.
        self.canvas.bind("<Left>", lambda e: self._ped_key(e, ("nudge", -1, 0)))
        self.canvas.bind("<Right>", lambda e: self._ped_key(e, ("nudge", 1, 0)))
        self.canvas.bind("<Up>", lambda e: self._ped_key(e, ("nudge", 0, -1)))
        self.canvas.bind("<Down>", lambda e: self._ped_key(e, ("nudge", 0, 1)))
        self.canvas.bind("<Control-a>", lambda e: self._ped_key(e, "select_all"))
        self.canvas.bind("<Control-d>", lambda e: self._ped_key(e, "duplicate"))
        self.canvas.bind("<Control-g>", lambda e: self._ped_key(e, "group"))
        self.canvas.bind("<Control-Shift-g>", lambda e: self._ped_key(e, "ungroup"))
        self.canvas.bind("<Control-Shift-G>", lambda e: self._ped_key(e, "ungroup"))
        for key_name, tool_name in (("v", "select"), ("t", "text"), ("r", "rect"),
                                    ("e", "ellipse"), ("l", "line"), ("a", "arrow"),
                                    ("f", "freehand"), ("s", "snip"), ("h", "pan")):
            self.canvas.bind(f"<{key_name}>",
                             lambda ev, t=tool_name: self._ped_tool_key(t))
            self.canvas.bind(f"<{key_name.upper()}>",
                             lambda ev, t=tool_name: self._ped_tool_key(t))
        self.canvas.bind("<KeyPress-space>", self._ped_space_down)
        self.canvas.bind("<KeyRelease-space>", self._ped_space_up)
        self._ped_build_rail_panel()
        self._ped_ui_built = True

    # ------------------------------------------ v15.9 tool rail + properties panel
    def _ped_build_rail_panel(self):
        """The Inkscape-style furniture: a compact vertical tool rail on the
        left of the page and a contextual Properties + Objects panel on the
        right.  Both float over the canvas workspace margins; _ped_layout
        reserves their space so the page is never hidden underneath."""
        cv = self.canvas
        PUR, PURD = self.PED_PURPLE, self.PED_PURPLE_D
        # v15.16: the rail is labeled, like the screenshot's — every tool
        # reads at a glance, no hovering to learn an icon.  The order
        # matches Home's first row.  Snip, Freehand and Pan are not lost:
        # Snip lives in Home and the Tools tab, Freehand and Move in Home, and all
        # three keep their letter keys (S / F / H) and Space-pan.
        rail = ctk.CTkFrame(cv, width=76, corner_radius=0, fg_color=("#E9E4EE", "#252830"))  # v15.22: wider, for the larger labels
        rail.pack_propagate(False)        # the constructor width is the width
        self._ped_rail = rail
        self._ped_rail_btns = {}
        tools = [("select", "➤", "Select", "Select — click objects, drag to move, Shift-click adds (V)"),
                 ("text", "T", "Text", "Text — click the page and type; click text to edit it (T)"),
                 ("polygon", "◇", "Shape", "Shape — click each corner, double-click to finish"),
                 ("line", "╱", "Line", "Line — drag on the page to draw (L)"),
                 ("arrow", "➔", "Arrow", "Arrow — drag on the page to draw (A)"),
                 ("rect", "▭", "Rectangle", "Rectangle — drag on the page to draw (R)"),
                 ("circle", "◯", "Circle", "Circle — drag on the page to draw")]
        for name, glyph, label, hint in tools:
            cell = ctk.CTkFrame(rail, fg_color="transparent")
            cell.pack(pady=1, padx=2, fill="x")
            b = ctk.CTkButton(cell, text=glyph, width=40, height=28, corner_radius=8,
                              fg_color="transparent", hover_color=("#EAE2F2", "#3A3550"),
                              text_color=("#222222", "#f0f0f4"),
                              font=ctk.CTkFont(size=15, weight="bold"),
                              command=lambda t=name: self._ped_toggle_tool(t))
            b.pack()
            lbl = ctk.CTkLabel(cell, text=label, font=ctk.CTkFont(size=11, weight="bold"),
                               text_color=("#2E2440", "#e8e8ee"))
            lbl.pack()
            self._ped_rail_btns[name] = b
            for w in (b, lbl):
                w.bind("<Enter>", lambda ev, h=hint: self._ped_rail_hint(h))
                w.bind("<Leave>", lambda ev: self._ped_rail_hint(None))
            self._ped_tip(b, hint)          # v15.10: a real hover tooltip too
        # action cells (not tools): a picture, an eraser, and "more"
        for glyph, label, hint, cmd in (
                ("▣", "Image", "Insert a picture from a file onto the page", self._ped_import_image),
                ("⌫", "Eraser", "Erase the selected object (Delete)", self._ped_delete_item),
                ("···", "More", "Show / hide the Properties panel", self._ped_toggle_panel)):
            cell = ctk.CTkFrame(rail, fg_color="transparent")
            cell.pack(pady=1, padx=2, fill="x")
            b = ctk.CTkButton(cell, text=glyph, width=40, height=28, corner_radius=8,
                              fg_color="transparent", hover_color=("#EAE2F2", "#3A3550"),
                              text_color=("#222222", "#f0f0f4"),
                              font=ctk.CTkFont(size=15, weight="bold"),
                              command=cmd)
            b.pack()
            lbl = ctk.CTkLabel(cell, text=label, font=ctk.CTkFont(size=11, weight="bold"),
                               text_color=("#2E2440", "#e8e8ee"))
            lbl.pack()
            self._ped_tip(b, hint)
        self._ped_rail_hint_lbl = ctk.CTkLabel(rail, text="", text_color=PUR,
                                               font=ctk.CTkFont(size=10), wraplength=68)
        self._ped_rail_hint_lbl.pack(pady=(2, 0))

        panel = ctk.CTkFrame(cv, width=280, corner_radius=0, fg_color=("#F1EDF5", "#2A2D38"))
        panel.pack_propagate(False)       # the constructor width is the width
        self._ped_props_panel = panel
        hdr = ctk.CTkFrame(panel, fg_color="transparent")
        hdr.pack(fill="x", padx=8, pady=(6, 0))
        ctk.CTkLabel(hdr, text="Properties", font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=PUR).pack(side="left")
        ctk.CTkButton(hdr, text="✕", width=24, height=24, fg_color="transparent",
                      hover_color=("#EAE2F2", "#3A3550"), command=self._ped_toggle_panel
                      ).pack(side="right")
        self._ped_props_title = ctk.CTkLabel(panel, text="Nothing selected", anchor="w",
                                             text_color=("#333333", "#cfcfe0"), wraplength=264,
                                             justify="left")
        self._ped_props_title.pack(fill="x", padx=8, pady=(2, 4))
        body = ctk.CTkScrollableFrame(panel, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=2, pady=2)
        self._ped_props_body = body

        # v15.16: Page section, first in the panel like the screenshot's —
        # the page's measured size and orientation as facts, and, on a
        # card page, the working paper menu (the same paper control the
        # Edit tab already has; on a PDF page the size is the source
        # file's, so the menu is disabled rather than pretending).
        pf = ctk.CTkFrame(body, fg_color="transparent")
        pf.pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(pf, text="Page", text_color=PUR,
                     font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w")
        prow = ctk.CTkFrame(pf, fg_color="transparent")
        prow.pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(prow, text="Page size", width=64, anchor="w").pack(side="left")
        # v15.21: white face in light mode — the default blue blob in the
        # 15.20 screenshot matched nothing in the preview.
        self._ped_page_size_menu = ctk.CTkOptionMenu(
            prow, values=["letter", "A4"], variable=self.pdf_paper, width=110, height=24,
            fg_color=("white", "#332E45"), button_color=self.PED_PURPLE_D,
            text_color=("#222222", "#FFFFFF"),
            command=lambda _: self._ped_card_opt_now())
        self._ped_page_size_menu.pack(side="left")
        self._ped_tip(self._ped_page_size_menu, "Paper size for card pages (a PDF page keeps its source size)")
        self._ped_page_info_lbl = ctk.CTkLabel(pf, text="", anchor="w",
                                               text_color="gray", font=ctk.CTkFont(size=10))
        self._ped_page_info_lbl.pack(fill="x", pady=(2, 0))

        def ent(parent, var, w=64):
            e = ctk.CTkEntry(parent, textvariable=var, width=w, height=24)
            # CTkEntry is a composite: the keys land in its inner tk entry, so
            # bind there as well or Return/FocusOut never fire.
            for target in (e, getattr(e, "_entry", None)):
                if target is not None:
                    target.bind("<Return>", lambda ev: self._ped_props_apply_geo())
                    target.bind("<FocusOut>", lambda ev: self._ped_props_apply_geo())
            return e

        geo = ctk.CTkFrame(body, fg_color="transparent")
        self._ped_props_geo = geo
        geo.pack(fill="x", pady=(2, 0))
        self._ped_geo_vars = {k: tk.StringVar(value="") for k in ("x", "y", "w", "h", "rot")}
        for row, (a, b) in enumerate((("x", "y"), ("w", "h"))):
            r = ctk.CTkFrame(geo, fg_color="transparent")
            r.pack(fill="x", pady=1)
            ctk.CTkLabel(r, text=a.upper(), width=16).pack(side="left")
            ent(r, self._ped_geo_vars[a]).pack(side="left", padx=(0, 6))
            ctk.CTkLabel(r, text=b.upper(), width=16).pack(side="left")
            ent(r, self._ped_geo_vars[b]).pack(side="left")
        r = ctk.CTkFrame(geo, fg_color="transparent")
        r.pack(fill="x", pady=1)
        ctk.CTkLabel(r, text="Rotation", width=52, anchor="w").pack(side="left")
        ent(r, self._ped_geo_vars["rot"], w=52).pack(side="left", padx=(0, 6))
        _rotb = ctk.CTkButton(r, text="⟳ 90°", width=58, height=24, fg_color=PURD, hover_color=PUR,
                              text_color="white", command=self._ped_rotate_item)
        _rotb.pack(side="left")
        self._ped_tip(_rotb, "Rotate the selected object 90°")
        ctk.CTkLabel(geo, text="Position / size in points (72 pt = 1 inch)",
                     text_color="gray", font=ctk.CTkFont(size=9)).pack(anchor="w")

        # text section (shares the sidebar's variables + handlers, so the two
        # sets of controls can never disagree)
        tf = ctk.CTkFrame(body, fg_color="transparent")
        self._ped_props_text = tf
        ctk.CTkLabel(tf, text="Text", text_color=PUR,
                     font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", pady=(8, 2))
        ctk.CTkOptionMenu(tf, variable=self._ped_font_var,
                          values=["Helvetica", "Times", "Courier"], width=150, height=24,
                          command=lambda v: self._ped_fmt_changed("font", v)).pack(anchor="w")
        fr = ctk.CTkFrame(tf, fg_color="transparent")
        fr.pack(fill="x", pady=(4, 0))
        ctk.CTkCheckBox(fr, text="Bold", variable=self._ped_bold_var, width=64,
                        command=lambda: self._ped_fmt_changed("bold", self._ped_bold_var.get())
                        ).pack(side="left")
        ctk.CTkCheckBox(fr, text="Italic", variable=self._ped_italic_var, width=70,
                        command=lambda: self._ped_fmt_changed("italic", self._ped_italic_var.get())
                        ).pack(side="left")
        ctk.CTkOptionMenu(tf, variable=self._ped_align_var, values=["Left", "Center", "Right"],
                          width=110, height=24,
                          command=lambda v: self._ped_fmt_changed("align", v)).pack(anchor="w",
                                                                                   pady=(4, 0))
        sr = ctk.CTkFrame(tf, fg_color="transparent")
        sr.pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(sr, text="Size", width=34, anchor="w").pack(side="left")
        ctk.CTkSlider(sr, from_=6, to=96, number_of_steps=90, variable=self._ped_size_var,
                      command=self._ped_size_changed).pack(side="left", fill="x", expand=True)

        # shape section
        sf = ctk.CTkFrame(body, fg_color="transparent")
        self._ped_props_shape = sf
        ctk.CTkLabel(sf, text="Shape", text_color=PUR,
                     font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", pady=(8, 2))
        sr2 = ctk.CTkFrame(sf, fg_color="transparent")
        sr2.pack(fill="x")
        ctk.CTkButton(sr2, text="Fill…", height=24, fg_color="gray30", hover_color="#555",
                      command=lambda: self._ped_pick_shape_color(True)).pack(
                          side="left", fill="x", expand=True, padx=(0, 3))
        ctk.CTkButton(sr2, text="Line…", height=24, fg_color="gray30", hover_color="#555",
                      command=lambda: self._ped_pick_shape_color(False)).pack(
                          side="left", fill="x", expand=True)
        ctk.CTkCheckBox(sf, text="No fill (outline only)", variable=self._ped_nofill_var,
                        command=self._ped_nofill_toggled).pack(anchor="w", pady=(4, 0))

        # picture section
        imf = ctk.CTkFrame(body, fg_color="transparent")
        self._ped_props_img = imf
        ctk.CTkLabel(imf, text="Picture", text_color=PUR,
                     font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", pady=(8, 2))
        ir = ctk.CTkFrame(imf, fg_color="transparent")
        ir.pack(fill="x")
        ctk.CTkButton(ir, text="Replace…", height=24, fg_color="gray30", hover_color="#555",
                      command=self._ped_replace_image).pack(side="left", fill="x", expand=True,
                                                            padx=(0, 3))
        self._ped_props_crop_btn = ctk.CTkButton(ir, text="Crop on/off", height=24,
                                                 fg_color="gray30", hover_color="#555",
                                                 command=self._ped_toggle_crop)
        self._ped_props_crop_btn.pack(side="left", fill="x", expand=True)
        ctk.CTkCheckBox(imf, text="Show word under picture", variable=self._ped_label_var,
                        command=self._ped_label_toggled).pack(anchor="w", pady=(4, 0))

        # arrange section
        af = ctk.CTkFrame(body, fg_color="transparent")
        self._ped_props_arrange = af
        ctk.CTkLabel(af, text="Arrange", text_color=PUR,
                     font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", pady=(8, 2))
        rows = [(("Duplicate", self._ped_duplicate_item), ("Delete", self._ped_delete_item)),
                (("Forward", lambda: self._ped_layer(True)), ("Backward", lambda: self._ped_layer(False))),
                (("Group", self._ped_group), ("Ungroup", self._ped_ungroup))]
        for row in rows:
            rf = ctk.CTkFrame(af, fg_color="transparent")
            rf.pack(fill="x", pady=1)
            for txt, cmd in row:
                ctk.CTkButton(rf, text=txt, height=24, fg_color="gray30", hover_color="#555",
                              command=cmd).pack(side="left", fill="x", expand=True, padx=1)
        rf = ctk.CTkFrame(af, fg_color="transparent")
        rf.pack(fill="x", pady=(5, 1))
        for txt, m, tip in (("⇤ Left", "l", "Align the left edges"),
                            ("↔ Centre", "c", "Align the horizontal centres"),
                            ("⇥ Right", "r", "Align the right edges")):
            _ab = ctk.CTkButton(rf, text=txt, height=24, fg_color="gray30", hover_color="#555",
                                command=lambda mm=m: self._ped_align(mm))
            _ab.pack(side="left", fill="x", expand=True, padx=1)
            self._ped_tip(_ab, tip)
        rf = ctk.CTkFrame(af, fg_color="transparent")
        rf.pack(fill="x", pady=1)
        for txt, m, tip in (("⤒ Top", "t", "Align the top edges"),
                            ("↕ Middle", "m", "Align the vertical middles"),
                            ("⤓ Bottom", "b", "Align the bottom edges")):
            _ab = ctk.CTkButton(rf, text=txt, height=24, fg_color="gray30", hover_color="#555",
                                command=lambda mm=m: self._ped_align(mm))
            _ab.pack(side="left", fill="x", expand=True, padx=1)
            self._ped_tip(_ab, tip)
        rf = ctk.CTkFrame(af, fg_color="transparent")
        rf.pack(fill="x", pady=1)
        _dh = ctk.CTkButton(rf, text="Distribute ↔", height=24, fg_color="gray30", hover_color="#555",
                            command=lambda: self._ped_distribute("h"))
        _dh.pack(side="left", fill="x", expand=True, padx=1)
        self._ped_tip(_dh, "Even out the horizontal gaps between 3 or more selected objects")
        _dv = ctk.CTkButton(rf, text="Distribute ↕", height=24, fg_color="gray30", hover_color="#555",
                            command=lambda: self._ped_distribute("v"))
        _dv.pack(side="left", fill="x", expand=True, padx=1)
        self._ped_tip(_dv, "Even out the vertical gaps between 3 or more selected objects")
        ctk.CTkButton(af, text="Zoom to selection", height=24, fg_color=PURD, hover_color=PUR,
                      text_color="white", command=self._ped_zoom_selection).pack(fill="x", pady=(5, 0))

        # objects list
        of = ctk.CTkFrame(body, fg_color="transparent")
        of.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(of, text="Objects on this page", text_color=PUR,
                     font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w")
        self._ped_objs = tk.Listbox(of, height=12, activestyle="none",
                                    bg="#1d1d23" if ctk.get_appearance_mode() == "Dark" else "#ffffff",
                                    fg="#e8e8ee" if ctk.get_appearance_mode() == "Dark" else "#222222",
                                    selectbackground=PURD, selectforeground="#ffffff",
                                    highlightthickness=1, highlightbackground="#555560",
                                    font=("Helvetica", 9))
        self._ped_objs.pack(fill="x", pady=(3, 0))
        self._ped_objs.bind("<<ListboxSelect>>", lambda ev: self._ped_objs_picked())
        orow = ctk.CTkFrame(of, fg_color="transparent")
        orow.pack(fill="x", pady=(4, 0))
        for txt, cmd, tip in (("▲", lambda: self._ped_objs_move(True), "Bring forward (towards the front)"),
                              ("▼", lambda: self._ped_objs_move(False), "Send backward"),
                              ("Hide", self._ped_objs_toggle_hide, "Hide the object — it won't print"),
                              ("Lock", self._ped_objs_toggle_lock, "Lock the object so it can't be grabbed")):
            _ob = ctk.CTkButton(orow, text=txt, width=40, height=24, fg_color="gray30",
                                hover_color="#555", command=cmd)
            _ob.pack(side="left", fill="x", expand=True, padx=1)
            self._ped_tip(_ob, tip)
        self._ped_obj_name_var = tk.StringVar(value="")
        ne = ctk.CTkEntry(of, textvariable=self._ped_obj_name_var,
                          placeholder_text="Object name — Enter renames")
        ne.pack(fill="x", pady=(4, 0))
        for target in (ne, getattr(ne, "_entry", None)):
            if target is not None:
                target.bind("<Return>", lambda ev: self._ped_objs_rename())
        ctk.CTkLabel(of, text="▲▼ reorder · Hide · Lock. Locked objects can't be "
                              "grabbed on the page; hidden ones don't print.",
                     text_color="gray", font=ctk.CTkFont(size=9), wraplength=230,
                     justify="left").pack(anchor="w", pady=(3, 6))

        # ---- v15.12: the word box only — the card/page settings that used
        # to sit below it now live in the ribbon's Edit tab, so the word
        # list and the objects list above have room to breathe.
        cf = ctk.CTkFrame(body, fg_color="transparent")
        cf.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(cf, text="Word Bank", text_color=PUR,
                     font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w")
        # v15.17: search above the rows, like the target screenshot — it
        # filters the rows below as you type, without touching the list.
        self._ped_bank_search_var = tk.StringVar(value="")
        _se = ctk.CTkEntry(cf, textvariable=self._ped_bank_search_var,
                           placeholder_text="🔍 Search words…")
        _se.pack(fill="x", pady=(3, 0))
        for target in (_se, getattr(_se, "_entry", None)):
            if target is not None:
                target.bind("<KeyRelease>", lambda e: self._ped_render_box_tiles())
        self._ped_tip(_se, "Type to filter the word rows below")
        self._ped_cardword_entry2 = ctk.CTkEntry(cf, placeholder_text="Type words…  (Enter adds them)")
        self._ped_cardword_entry2.pack(fill="x", pady=(3, 0))
        for target in (self._ped_cardword_entry2,
                       getattr(self._ped_cardword_entry2, "_entry", None)):
            if target is not None:
                target.bind("<Return>",
                            lambda e: self._ped_card_add_words(self._ped_cardword_entry2))
        _add = ctk.CTkButton(cf, text="＋ Add to card/page", height=26, fg_color=PURD,
                             hover_color=PUR, text_color="white",
                             command=lambda: self._ped_card_add_words(self._ped_cardword_entry2))
        _add.pack(fill="x", pady=(4, 0))
        self._ped_tip(_add, "Add the typed words to this page")
        self._ped_box_menu2 = ctk.CTkOptionMenu(
            cf, values=["Load words from…"] + self._maker_list_choices(),
            variable=self.pdf_preset_var, command=self._ped_box_preset_picked2)
        self._ped_box_menu2.pack(fill="x", pady=(2, 2))
        self._ped_tip(self._ped_box_menu2, "Load a word list into the word box below")
        _box_wrap2 = ctk.CTkFrame(cf, height=220, fg_color="transparent")
        _box_wrap2.pack(fill="x", pady=(2, 2))
        _box_wrap2.pack_propagate(False)
        _box_sc2 = ctk.CTkScrollableFrame(_box_wrap2, fg_color="transparent")
        _box_sc2.pack(fill="both", expand=True)
        self._ped_box_inner2 = ctk.CTkFrame(_box_sc2, fg_color="transparent")
        self._ped_box_inner2.pack(fill="x", anchor="n")
        self._ped_render_box_tiles()
        self._ped_sync_card_section()
        self._ped_sync_card_sel()
        # ribbon additions (the Tools group already exists by now)
        tg = self._ped_rib_groups.get("Tools")
        if tg is not None:
            b = ctk.CTkButton(tg, text="Zoom to object", width=104, height=24, fg_color="gray30",
                              hover_color="#555555", command=self._ped_zoom_selection)
            b.pack(side="left", padx=2)
            self._ped_tip(b, "Zoom in on the selected object")
            b = ctk.CTkButton(tg, text="Panel: on", width=76, height=24, fg_color="gray30",
                              hover_color="#555555", command=self._ped_toggle_panel)
            b.pack(side="left", padx=2)
            self._ped_panel_btn = b
            self._ped_tip(b, "Show / hide the Properties panel on the right")

    def _ped_rail_hint(self, text):
        try:
            if text:
                self.info.configure(text="PDF Editor · " + text)
            elif getattr(self, "_ped_hint_text", None):
                self.info.configure(text=self._ped_hint_text)
        except Exception:
            pass

    def _ped_toggle_panel(self):
        self._ped_panel_on = not self._ped_panel_on
        try:
            self._ped_panel_btn.configure(text="Panel: on" if self._ped_panel_on else "Panel: off")
        except Exception:
            pass
        self._ped_apply_chrome()
        self._ped_draw()

    def _ped_place_furniture(self, on):
        """Show/hide the rail + panel over the canvas (editor chrome)."""
        # NB: CTk widgets take their width from the constructor — passing
        # width= to place() raises ValueError in CustomTkinter.
        for w, kw in ((getattr(self, "_ped_rail", None), {"x": 0, "y": 0, "relheight": 1.0}),
                      (getattr(self, "_ped_props_panel", None),
                       {"relx": 1.0, "x": 0, "y": 0, "anchor": "ne", "relheight": 1.0})):
            if w is None:
                continue
            try:
                if on and (w is not self._ped_props_panel or self._ped_panel_on):
                    w.place(**kw)
                else:
                    w.place_forget()
            except Exception:
                pass

    def _ped_props_refresh(self, force=False):
        """Refresh the Properties panel for the current selection.  Cheap when
        nothing changed (a signature check), so it is safe to call often."""
        panel = getattr(self, "_ped_props_panel", None)
        if panel is None or not self._ped_ui_built:
            return
        sel = self._ped_selection()
        e = self._ped_entry()
        sig = (self._ped_cur, tuple(it.uid for it in (e.items if e else [])),
               tuple(it.uid for it in sel),
               tuple(round(v, 1) for v in (self._ped_sel_bounds(sel) or (0, 0, 0, 0))),
               sel[0].rot if len(sel) == 1 else None,
               e.rot if e is not None else None,  # v15.54: a page rotate
               self._ped_tool)     # v15.10: sections also follow the active tool
        if not force and getattr(self, "_ped_props_sig", None) == sig:
            return
        self._ped_props_sig = sig
        try:
            if not sel:
                self._ped_props_title.configure(text="Nothing selected — click an object "
                                                     "on the page")
            elif len(sel) > 1:
                self._ped_props_title.configure(text=f"{len(sel)} objects selected")
            else:
                it = sel[0]
                t = item_display_name(it)
                if it.gid:
                    t += " · grouped"
                if it.locked:
                    t += " · locked"
                if it.hidden:
                    t += " · hidden"
                self._ped_props_title.configure(text=t)
            b = self._ped_sel_bounds(sel)
            vals = {"x": "", "y": "", "w": "", "h": "", "rot": ""}
            if b is not None:
                vals = {"x": f"{b[0]:.0f}", "y": f"{b[1]:.0f}", "w": f"{b[2] - b[0]:.0f}",
                        "h": f"{b[3] - b[1]:.0f}",
                        "rot": f"{sel[0].rot:.0f}" if len(sel) == 1 and not sel[0].points else ""}
            for k, v in vals.items():
                self._ped_geo_vars[k].set(v)
            one = sel[0] if len(sel) == 1 else None
            for frame, show in ((self._ped_props_geo, bool(sel)),
                                (self._ped_props_text, one is not None and one.kind == "text"),
                                (self._ped_props_shape, (one is not None and
                                 (one.kind in ("ellipse", "line", "arrow", "freehand")
                                  or (one.kind == "rect" and one.border)))
                                 # v15.10: also while a drawing tool is armed, so the
                                 # fill colour / No fill for the NEXT shape is right here
                                 or self._ped_tool in ("rect", "ellipse", "circle",
                                                       "line", "arrow", "freehand")),
                                (self._ped_props_img, one is not None
                                 and one.kind in ("image", "card")),
                                (self._ped_props_arrange, bool(sel))):
                try:
                    if show:
                        frame.pack(fill="x")
                    else:
                        frame.pack_forget()
                except Exception:
                    pass
            # v15.16: Page section facts for the current page.
            try:
                if e is not None:
                    W, H = e.size
                    orient = "Portrait" if H >= W else "Landscape"
                    self._ped_page_info_lbl.configure(
                        text=f"{W / 72:.1f} × {H / 72:.1f} in · {orient}")
                    is_card = e.card is not None
                    # Never set the menu's variable here for a PDF page:
                    # it shares pdf_paper with the card pages, and writing
                    # a measured size into it would corrupt the card paper
                    # setting.  The measured size lives in the label above.
                    self._ped_page_size_menu.configure(
                        state="normal" if is_card else "disabled")
            except Exception:
                pass
        except Exception:
            pass
        self._ped_objs_refresh()

    def _ped_props_apply_geo(self):
        sel = [it for it in self._ped_selection() if not it.locked]
        if not sel or self._ped_entry() is None:
            return
        gv = self._ped_geo_vars

        def num(k):
            try:
                return float(gv[k].get())
            except Exception:
                return None
        b = self._ped_sel_bounds(sel)
        if b is None:
            return
        nx, ny, nw, nh = num("x"), num("y"), num("w"), num("h")
        dx = (nx - b[0]) if nx is not None else 0.0
        dy = (ny - b[1]) if ny is not None else 0.0
        fx = (nw / max(b[2] - b[0], 1e-6)) if nw is not None and nw > 0 else 1.0
        fy = (nh / max(b[3] - b[1], 1e-6)) if nh is not None and nh > 0 else 1.0
        rotv = num("rot")
        changed = bool(dx or dy or fx != 1.0 or fy != 1.0)
        rot_changed = (rotv is not None and len(sel) == 1 and not sel[0].points
                       and abs((rotv % 360) - sel[0].rot) > 0.01)
        if not changed and not rot_changed:
            return                       # focus passed through: nothing to do
        self._ped.push_undo()
        if changed:
            for it in sel:
                a = item_aabb(it)
                ox0 = b[0] + (a[0] - b[0]) * fx + dx
                oy0 = b[1] + (a[1] - b[1]) * fy + dy
                ddx, ddy = ox0 - a[0], oy0 - a[1]
                # scale about the item's own top-left of its footprint
                cx, cy = item_center(it)
                it.x0 = cx + (it.x0 - cx) * (fx if not it.points else 1.0)
                it.x1 = cx + (it.x1 - cx) * (fx if not it.points else 1.0)
                it.y0 = cy + (it.y0 - cy) * (fy if not it.points else 1.0)
                it.y1 = cy + (it.y1 - cy) * (fy if not it.points else 1.0)
                if it.points:
                    it.points = [(cx + (qx - cx) * fx, cy + (qy - cy) * fy) for qx, qy in it.points]
                    xs = [q[0] for q in it.points]
                    ys = [q[1] for q in it.points]
                    it.x0, it.y0, it.x1, it.y1 = min(xs), min(ys), max(xs), max(ys)
                self._ped_move_item_by(it, ddx, ddy)
                if it.kind == "text" and fy != 1.0:
                    it.size = max(4.0, min(400.0, it.size * fy))
            self._ped.dirty = True
        if rot_changed:
            sel[0].rot = int(round(rotv)) % 360
            self._ped.dirty = True
        self._ped_draw_overlay()
        self._ped_props_refresh(force=True)

    def _ped_objs_items(self):
        e = self._ped_entry()
        return list(reversed(e.items)) if e is not None else []     # top-most first

    def _ped_objs_refresh(self):
        lb = getattr(self, "_ped_objs", None)
        if lb is None:
            return
        items = self._ped_objs_items()
        sel = self._ped_selection()
        sig = (self._ped_cur, tuple(it.uid for it in items),
               tuple((it.name, it.hidden, it.locked, it.gid) for it in items),
               tuple(it.uid for it in sel))
        if getattr(self, "_ped_objs_sig", None) == sig:
            return
        self._ped_objs_sig = sig
        try:
            lb.delete(0, "end")
            for it in items:
                t = item_display_name(it)
                marks = (" (hidden)" if it.hidden else "") + (" (locked)" if it.locked else "") \
                    + (" (group)" if it.gid else "")
                lb.insert("end", t + marks)
            # v15.17: the list grows with its contents (up to 12 rows) and
            # shrinks to a short strip when the page is empty — the big
            # empty rectangle on a blank page was the panel's largest
            # piece of dead space.
            lb.configure(height=max(4, min(12, len(items) if items else 4)))
            lb.selection_clear(0, "end")
            for i, it in enumerate(items):
                if it in sel:
                    lb.selection_set(i)
            one = sel[0] if len(sel) == 1 else None
            self._ped_obj_name_var.set(one.name if one is not None else "")
        except Exception:
            pass

    def _ped_objs_current(self):
        lb = getattr(self, "_ped_objs", None)
        if lb is None:
            return None
        cur = lb.curselection()
        items = self._ped_objs_items()
        if not cur or cur[0] >= len(items):
            return None
        return items[cur[0]]

    def _ped_objs_picked(self):
        it = self._ped_objs_current()
        if it is not None:
            self._ped_set_selection([it], primary=it)

    def _ped_objs_move(self, up):
        it = self._ped_objs_current()
        e = self._ped_entry()
        if it is None or e is None or it not in e.items:
            return
        i = e.items.index(it)
        j = i + 1 if up else i - 1          # list is top-first: ▲ = bring forward
        if not (0 <= j < len(e.items)):
            return
        self._ped.push_undo()
        e.items[i], e.items[j] = e.items[j], e.items[i]
        self._ped.dirty = True
        self._ped_objs_sig = None
        self._ped_draw_overlay()
        self._ped_objs_refresh()

    def _ped_objs_toggle_hide(self):
        it = self._ped_objs_current() or self._ped_sel
        e = self._ped_entry()
        if it is None or e is None or it not in e.items:
            return
        self._ped.push_undo()
        it.hidden = not it.hidden
        self._ped.dirty = True
        self._ped_objs_sig = None
        self._ped_draw_overlay()
        self._ped_objs_refresh()
        self._ped_props_refresh(force=True)

    def _ped_objs_toggle_lock(self):
        it = self._ped_objs_current() or self._ped_sel
        e = self._ped_entry()
        if it is None or e is None or it not in e.items:
            return
        self._ped.push_undo()
        it.locked = not it.locked
        self._ped.dirty = True
        self._ped_objs_sig = None
        self._ped_draw_selection()
        self._ped_objs_refresh()
        self._ped_props_refresh(force=True)

    def _ped_objs_rename(self):
        it = self._ped_objs_current() or self._ped_sel
        e = self._ped_entry()
        if it is None or e is None or it not in e.items:
            return
        name = self._ped_obj_name_var.get().strip()
        if name == it.name:
            return
        self._ped.push_undo()
        it.name = name
        self._ped.dirty = True
        self._ped_objs_sig = None
        self._ped_objs_refresh()
        self._ped_props_refresh(force=True)

    # ------------------------------------------------------------ v15.6 card pages
    _ped_card_seq = itertools.count(1000)

    def _ped_card_spec(self, words):
        return {"words": [str(w) for w in words], "layout": self.pdf_layout_var.get(),
                "paper": self.pdf_paper.get(), "word_scale": float(self.pdf_word_scale.get()),
                "pic_scale": float(self.pdf_pic_scale.get()), "pic_y": float(self.pdf_pic_y.get()),
                "word_y": float(self.pdf_word_y.get()), "show_pics": bool(self.pdf_show_pics.get()),
                "show_words": bool(self.pdf_show_words.get()),
                "colors": bool(self.pdf_colors_var.get())}

    def _ped_chunk_words(self, words, spec):
        boards = _pal().aac_boards
        per = boards.PECS_LAYOUTS.get(spec.get("layout", ""), (2, 4))
        n = per[0] * per[1]
        return [list(words[i:i + n]) for i in range(0, len(words), n)] or [[]]

    def _ped_new_card_entry(self, words, spec=None):
        spec = spec or self._ped_card_spec(words)
        w, h = (595.28, 841.89) if str(spec.get("paper", "letter")).lower() == "a4" else (612.0, 792.0)
        return PageEntry(CARD_SRC, -next(self._ped_card_seq), w, h, 0, [], spec)

    def _ped_new_card_session(self):
        sess = Session()
        sess.card_render_fn = self._ped_card_render_for_build
        spec = self._ped_card_spec(getattr(self, "_pdf_words", []))
        sess.pages = [self._ped_new_card_entry(chunk, dict(spec, words=chunk))
                      for chunk in self._ped_chunk_words(spec["words"], spec)]
        return sess

    def _ped_card_layout(self, entry):
        """The shared card-page layout for a card entry (render + slot hit-tests)."""
        spec = entry.card or {}
        return _pal().aac_boards.pecs_page_layout(
            spec.get("words", []), layout_name=spec.get("layout", "8 per page (2×4)"),
            paper=spec.get("paper", "letter"), page_index=0, page_count=1, first_index=0,
            use_category_colors=spec.get("colors", True), tile_color="#FFFFFF",
            tile_colors=self.pdf_tile_colors, show_labels=spec.get("show_words", True),
            show_pictures=spec.get("show_pics", True), word_scale=spec.get("word_scale", 1.0),
            pic_scale=spec.get("pic_scale", 1.0), pic_y=spec.get("pic_y", 0.35),
            pic_scale_map=self.pdf_pic_scale_map, pic_y_map=self.pdf_pic_y_map,
            word_y=spec.get("word_y", 0.12))

    def _ped_card_render_for_build(self, entry, px_per_pt):
        """Draw a card page with PIL from the ONE shared layout (aac_boards) — the
        same geometry the old preview/print used — at px_per_pt resolution."""
        boards = _pal().aac_boards
        lay = self._ped_card_layout(entry)
        ppp = px_per_pt * 72.0                       # pixels per inch
        W, H = int(lay["page_w_in"] * ppp), int(lay["page_h_in"] * ppp)
        img = Image.new("RGB", (W, H), "white")
        d = ImageDraw.Draw(img)
        ov, var, sa = dict(self.overrides), dict(self.variants), dict(self.search_as)
        for slot in lay["slots"]:
            word = slot["word"]
            if word is None:
                continue
            x, y, side = slot["x_in"] * ppp, slot["y_in"] * ppp, slot["side_in"] * ppp
            d.rectangle([x, y, x + side - 1, y + side - 1], fill=slot["color"],
                        outline="#222222", width=max(1, int(ppp / 72)))
            if not str(word).strip():
                continue
            icon = None
            if slot["icon_rect"] is not None:
                # v15.13: a slot pinned by a text edit keeps exactly the
                # picture it had — the lookup below never runs for it.  A
                # pin of "" means the slot had no picture and keeps none.
                pins = (entry.card or {}).get("slot_pics") or {}
                pin = pins.get(str(slot["slot"]))
                if pin is not None:
                    if pin and Path(pin).exists():
                        try:
                            icon = Image.open(pin).convert("RGBA")
                            icon.thumbnail((max(16, int(slot["icon_size_px"] * px_per_pt)),) * 2,
                                           Image.Resampling.LANCZOS)
                        except Exception:
                            icon = None
                else:
                    try:
                        icon = boards._load_icon_rgba(
                            word, size_px=max(16, int(slot["icon_size_px"] * px_per_pt)),
                            overrides=ov, variants=var, search_as=sa, local_only=False)
                    except Exception:
                        icon = None
            if icon is not None and slot["icon_rect"] is not None:
                ix, iy, iw, ih = [v * ppp for v in slot["icon_rect"]]
                pic = icon.copy()
                pic.thumbnail((max(1, int(iw)), max(1, int(ih))), Image.Resampling.LANCZOS)
                img.paste(pic, (int(ix + (iw - pic.width) / 2), int(iy + (ih - pic.height) / 2)), pic)
                if slot["label"] is not None:
                    lb = slot["label"]
                    f = _font(lb["fs_pt"] * px_per_pt, True)
                    d.text((lb["cx_in"] * ppp, lb["cy_in"] * ppp), word, font=f, fill="#111111",
                           anchor="mm")
            else:
                fs = slot["big_word_fs_pt"] or 14
                f = _font(fs * px_per_pt, True)
                d.text((x + side / 2, y + side / 2), word, font=f, fill="#111111", anchor="mm")
        f = _font(9 * px_per_pt)
        d.text((W / 2, lay["footer_y_in"] * ppp), lay["footer"], font=f, fill="#888888", anchor="mm")
        return img

    def _ped_slot_at(self, e, px, py):
        """The layout slot (dict) under a page-point on a card page, or None."""
        if e is None or e.card is None:
            return None
        for slot in self._ped_card_layout(e)["slots"]:
            x, y, side = slot["x_in"] * 72, slot["y_in"] * 72, slot["side_in"] * 72
            if x <= px <= x + side and y <= py <= y + side:
                return slot
        return None

    def _ped_card_sel_word(self):
        e, sel = self._ped_entry(), getattr(self, "_ped_card_sel", None)
        if e is None or e.card is None or sel is None or sel >= len(e.card["words"]):
            return None
        return e.card["words"][sel]

    def _ped_sync_card_sel(self):
        if not getattr(self, "_ped_cardsel_lbl", None):
            return
        e, sel = self._ped_entry(), getattr(self, "_ped_card_sel", None)
        word = self._ped_card_sel_word()
        try:
            if e is not None and e.card is not None and sel is not None:
                txt = f"Selected card: “{word}”" if word else "Selected card: (blank card)"
                st = "normal"
            else:
                txt = "Selected card: click a card on the page"
                st = "disabled"
            self._ped_cardsel_lbl.configure(text=txt)
            self._ped_cardsel_send.configure(state=st)
            self._ped_cardsel_del.configure(state=st)
            # v15.12: mirror into the ribbon Edit tab copy (the panel copy
            # moved there from below the word box)
            lblr = getattr(self, "_ped_rib_cardsel_lbl", None)
            if lblr is not None:
                lblr.configure(text=txt)
                self._ped_rib_cardsel_send.configure(state=st)
                self._ped_rib_cardsel_del.configure(state=st)
        except Exception:
            pass

    @staticmethod
    def _ped_slot_pics_pop(card, sel):
        """v15.13: pins after the word at slot sel leaves — drop sel's pin,
        shift higher slots down so pins stay on their tiles."""
        pins = {}
        for k, v in (card.get("slot_pics") or {}).items():
            try:
                i = int(k)
            except Exception:
                continue
            if i == sel:
                continue
            pins[str(i - 1 if i > sel else i)] = v
        return pins

    @staticmethod
    def _ped_slot_pics_swap(card, i, j):
        """v15.13: pins after slots i and j swap words — the pins swap too,
        so each picture still belongs to the word it was pinned with."""
        pins = dict(card.get("slot_pics") or {})
        a, b = pins.pop(str(i), None), pins.pop(str(j), None)
        if a is not None:
            pins[str(j)] = a
        if b is not None:
            pins[str(i)] = b
        return pins

    def _ped_card_delete(self):
        e, sel = self._ped_entry(), getattr(self, "_ped_card_sel", None)
        if e is None or e.card is None or sel is None or sel >= len(e.card["words"]):
            return
        word = e.card["words"][sel]
        self._ped.push_undo()
        words = list(e.card["words"])
        words.pop(sel)
        e.card = dict(e.card, words=words,
                      slot_pics=self._ped_slot_pics_pop(e.card, sel))
        self._ped.dirty = True
        self._ped_card_sel = None
        self._ped_refresh_card_page(e)
        self._ped_strip_draw()
        self._ped_sync_card_sel()
        self.flash_status(f"Removed “{word}” from this card page" if word else "Blank card removed")

    def _ped_card_send_menu(self):
        if self._ped_card_sel_word() is None or self._ped is None:
            return
        import tkinter as tk
        m = tk.Menu(self, tearoff=0)
        for k, pg in enumerate(self._ped.pages):
            if k == self._ped_cur:
                continue
            kind = "cards" if pg.card is not None else ("blank" if pg.src < 0 else "PDF page")
            m.add_command(label=f"Page {k + 1} ({kind})",
                          command=lambda kk=k: self._ped_card_send_to(kk))
        if len(self._ped.pages) < 2:
            m.add_command(label="No other pages yet — add one first", state="disabled")
        try:
            m.tk_popup(self._ped_cardsel_send.winfo_rootx(),
                       self._ped_cardsel_send.winfo_rooty() + 28)
        finally:
            m.grab_release()

    def _ped_card_send_to(self, target):
        sess, e = self._ped, self._ped_entry()
        sel = getattr(self, "_ped_card_sel", None)
        word = self._ped_card_sel_word()
        if sess is None or e is None or word is None:
            return
        sess.push_undo()
        words = list(e.card["words"])
        words.pop(sel)
        e.card = dict(e.card, words=words,
                      slot_pics=self._ped_slot_pics_pop(e.card, sel))
        self._ped_card_sel = None
        tgt = sess.pages[target]
        if tgt.card is not None:
            tgt.card = dict(tgt.card, words=list(tgt.card["words"]) + [word])
            self._ped_refresh_card_page(tgt)
        elif word:
            W, H = tgt.size
            use_cat = self.pdf_colors_var.get() if hasattr(self, "pdf_colors_var") else True
            color = _pal().aac_boards.category_color(word) if use_cat else "#FFFFFF"
            it = new_card(word, W / 2, H / 2, color)
            tgt.items.append(it)
            self._ped_fetch_picture(it)
        sess.dirty = True
        self._ped_refresh_card_page(e)
        self._ped_strip_draw()
        self._ped_sync_card_sel()
        self._ped_draw_overlay()
        self.flash_status(f"Moved “{word}” to page {target + 1}")

    def _ped_place_card_at(self, word, px, py):
        e = self._ped_entry()
        if e is None or not word:
            return
        use_cat = self.pdf_colors_var.get() if hasattr(self, "pdf_colors_var") else True
        color = _pal().aac_boards.category_color(word) if use_cat else "#FFFFFF"
        it = new_card(word, px, py, color)
        self._ped_put(it)
        self._ped_fetch_picture(it)

    def _ped_place_sent(self, word):
        """v15.14: place a sent word as a movable free-card object on the
        current page, selected at once.  Returns True when placed.  Sends
        are staggered a little per object already on the page so several
        sends in a row do not land in one invisible stack."""
        e = self._ped_entry()
        if e is None or not word or not self._ped_active:
            return False
        try:
            cx, cy = self._ped_center_pt()
        except Exception:
            W, H = e.size
            cx, cy = W / 2, H / 2
        n = len(e.items) % 5
        W, H = e.size
        cx = min(max(cx + n * 18.0, 30.0), max(30.0, W - 30.0))
        cy = min(max(cy + n * 18.0, 30.0), max(30.0, H - 30.0))
        self._ped_place_card_at(word, cx, cy)
        self.flash_status(f"“{word}” is on page {self._ped_cur + 1} — drag it anywhere")
        return True

    def _ped_finish_card_drag(self, d):
        e = self._ped_entry()
        if e is None or e.card is None:
            return
        over = d.get("over")
        i = d["slot"]
        words = list(e.card["words"])
        if i >= len(words):
            return
        if over is not None and over != i:
            j = over
            self._ped.push_undo()
            while len(words) <= j:
                words.append("")
            words[i], words[j] = words[j], words[i]
            e.card = dict(e.card, words=words,
                          slot_pics=self._ped_slot_pics_swap(e.card, i, j))
            self._ped.dirty = True
            self._ped_card_sel = j
            self._ped_refresh_card_page(e)
            self._ped_strip_draw()
            self._ped_sync_card_sel()
            return
        if over is None and d.get("moved") and words[i]:
            # v15.10: dragged OFF the grid — the tile leaves the card layout
            # and becomes a free card on this page, placed where it was
            # dropped.  It can be dragged, resized and rotated like any
            # other object from here on.
            word = words[i]
            self._ped.push_undo()
            words.pop(i)
            e.card = dict(e.card, words=words,
                          slot_pics=self._ped_slot_pics_pop(e.card, i))
            self._ped.dirty = True
            self._ped_card_sel = None
            use_cat = self.pdf_colors_var.get() if hasattr(self, "pdf_colors_var") else True
            color = _pal().aac_boards.category_color(word) if use_cat else "#FFFFFF"
            W, H = e.size
            cx = min(max(d.get("lx", W / 2), 0), W)
            cy = min(max(d.get("ly", H / 2), 0), H)
            it = new_card(word, cx, cy, color)
            e.items.append(it)
            self._ped_sel, self._ped_selset = it, [it]
            self._ped_fetch_picture(it)
            self._ped_refresh_card_page(e)
            self._ped_strip_draw()
            self._ped_sync_card_sel()
            self._ped_draw_overlay()
            self._ped_sync_props()
            self.flash_status(f"“{word}” is a free card now — drag it anywhere on the page")

    def _ped_refresh_card_page(self, entry):
        sess = self._ped
        if sess is None or entry.card is None:
            return

        def work():
            return self._ped_card_render_for_build(entry, 1.5)

        def done(img):
            if sess is not self._ped or entry not in sess.pages:
                return
            sess.card_images[entry.idx] = img
            if self._ped_entry() is entry:
                self._ped_page = None
            self._ped_draw()
            self._ped_strip_draw()
        self.bg("Drawing card page…", work, done)

    def _ped_sync_card_section(self):
        if not self._ped_ui_built:
            return
        e = self._ped_entry()
        # v15.13: the card controls mirror THIS page's spec — before, the
        # menus and checkboxes never synced, so they showed (and applied)
        # stale values from whatever page was touched last.
        if e is not None and e.card is not None:
            try:
                spec = e.card
                self.pdf_layout_var.set(spec.get("layout", "8 per page (2×4)"))
                self.pdf_paper.set(spec.get("paper", "letter"))
                self.pdf_show_pics.set(bool(spec.get("show_pics", True)))
                self.pdf_show_words.set(bool(spec.get("show_words", True)))
                self.pdf_colors_var.set(bool(spec.get("colors", True)))
                self.pdf_word_scale.set(float(spec.get("word_scale", 1.0)))
                self.pdf_pic_scale.set(float(spec.get("pic_scale", 1.0)))
                self.pdf_pic_y.set(float(spec.get("pic_y", 0.35)))
                self.pdf_word_y.set(float(spec.get("word_y", 0.12)))
            except Exception:
                pass
        try:
            if e is not None and e.card is not None:
                self._ped_pgframe.pack(fill="x", after=self._ped_newpage_btn)
            else:
                self._ped_pgframe.pack_forget()
        except Exception:
            pass
        pgf = getattr(self, "_ped_rib_pgframe", None)   # v15.12: the ribbon Edit copy
        if pgf is not None:
            try:
                if e is not None and e.card is not None:
                    pgf.pack(fill="x", pady=(4, 0))
                else:
                    pgf.pack_forget()
            except Exception:
                pass

    def _ped_box_preset_picked2(self, val):
        try:
            self._pdf_box_preset_picked(val)
        except Exception:
            pass
        self._ped_render_box_tiles()

    def _ped_render_box_tiles(self):
        # v15.10: the word box lives in the Properties panel now (and is
        # mirrored into the hidden old sidebar frame, which other code paths
        # still reference) — render the rows into both containers.
        # v15.17: rows, like the target screenshot — the word itself is
        # the button (click puts it on the page, drag it onto the page
        # and it lands as a free card, exactly as the old tiles did) and
        # a ＋ button on the right does the same in one tap.  The panel's
        # search entry filters the rows; the hidden sidebar has no search
        # box, so it shows the full list.
        q = ""
        try:
            q = (getattr(self, "_ped_bank_search_var", None).get() or "").strip().lower()
        except Exception:
            q = ""
        words = list(getattr(self, "_pdf_box_words", []))[:300]
        if q:
            words = [w for w in words if q in str(w).lower()]
        for inner in (getattr(self, "_ped_box_inner", None),
                      getattr(self, "_ped_box_inner2", None)):
            if not inner:
                continue
            for w in inner.winfo_children():
                w.destroy()
            for word in words:
                row = ctk.CTkFrame(inner, fg_color="transparent")
                row.pack(fill="x", pady=1)
                b = ctk.CTkButton(row, text=str(word), height=24, fg_color="gray30",
                                  hover_color="#555", anchor="w",
                                  command=lambda wd=str(word): self._ped_box_word(wd))
                b.pack(side="left", fill="x", expand=True)
                self._ped_tip(b, f"Put “{word}” on the page")
                # v15.54: right-click a Word Bank row for a Maker tag.
                b.bind("<Button-3>", lambda e, wd=str(word): self._ped_bank_menu(e, wd))
                # v15.11: drag the row straight onto the page and it lands
                # there as a free card at once — no click-to-arm, no detour.
                # The bindings use add="+" so a plain click still runs the
                # button's own command (the old behaviour) untouched.
                self._ped_bind_tile_drag(b, str(word))
                plus = ctk.CTkButton(row, text="＋", width=28, height=24,
                                     fg_color=self.PED_PURPLE_D, hover_color=self.PED_PURPLE,
                                     text_color="white",
                                     command=lambda wd=str(word): self._ped_box_word(wd))
                plus.pack(side="left", padx=(4, 0))
                self._ped_tip(plus, f"Put “{word}” on the page")

    def _ped_bank_menu(self, event, word):
        """v15.54: right-click a Word Bank row — one entry, Create new tag,
        which jumps to Maker Studio and puts the word on a brand-new tag."""
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="Create new tag",
                      command=lambda: self._ped_bank_create_tag(word))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()
        return "break"

    def _ped_bank_create_tag(self, word):
        self.goto_tab("Maker Studio")
        self._maker_create_new_tag(word)

    def _ped_bind_tile_drag(self, widget, word):
        """Drag-and-drop for a word-box tile, on the tile and its parts."""
        widget.bind("<ButtonPress-1>",
                    lambda e: self._ped_tile_drag_start(e, word), add="+")
        widget.bind("<B1-Motion>", self._ped_tile_drag_move, add="+")
        widget.bind("<ButtonRelease-1>",
                    lambda e: self._ped_tile_drag_end(e, word), add="+")
        for child in widget.winfo_children():
            self._ped_bind_tile_drag(child, word)

    def _ped_tile_drag_start(self, event, word):
        self._ped_tile_drag = {"word": word, "x0": event.x_root, "y0": event.y_root,
                               "active": False}
        self._ped_tile_ghost = None

    def _ped_tile_drag_move(self, event):
        d = getattr(self, "_ped_tile_drag", None)
        if not d:
            return
        if not d["active"]:
            if abs(event.x_root - d["x0"]) + abs(event.y_root - d["y0"]) < 8:
                return
            d["active"] = True
            g = tk.Label(self, text=d["word"], bg="#A98BD6", fg="white",
                         font=("Arial", 11, "bold"), padx=10, pady=5)
            self._ped_tile_ghost = g
        g = self._ped_tile_ghost
        if g is not None:
            g.place(x=event.x_root - self.winfo_rootx() + 12,
                    y=event.y_root - self.winfo_rooty() + 10)
            g.lift()

    def _ped_tile_drag_end(self, event, word):
        d = getattr(self, "_ped_tile_drag", None)
        self._ped_tile_drag = None
        g = getattr(self, "_ped_tile_ghost", None)
        if g is not None:
            try:
                g.destroy()
            except Exception:
                pass
            self._ped_tile_ghost = None
        if not d or not d["active"]:
            return          # a plain click: the button's own command runs
        if not self._ped_active or self.tabs.get() != "PDF Studio":
            return
        e = self._ped_entry()
        if e is None:
            return
        cx, cy = event.x_root - self.canvas.winfo_rootx(), event.y_root - self.canvas.winfo_rooty()
        if not (0 <= cx <= self.canvas.winfo_width()
                and 0 <= cy <= self.canvas.winfo_height()):
            return          # dropped outside the canvas: nothing happens
        # v15.56: the drop may be on the other page of the pair — focus
        # it first (same tie-break as clicks: the focused page wins the
        # overlap), so the word lands on the page it was dropped on.
        lay2 = self._ped_two_layout()
        if lay2 is not None:
            _i2, _s2, _ox2, _oy2, _pw2, _ph2 = lay2
            if _ox2 <= cx <= _ox2 + _pw2 and _oy2 <= cy <= _oy2 + _ph2 \
                    and not self._ped_focused_rect_contains(cx, cy):
                self._ped_focus_page(_i2)
                e = self._ped_entry()
                if e is None:
                    return
        s, ox, oy, _ = self._ped_layout()
        px, py = (cx - ox) / s, (cy - oy) / s
        W, H = e.size
        if not (0 <= px <= W and 0 <= py <= H):
            self.flash_status("Drop it on the page itself to place it", ok=False)
            return
        self._ped_place_card_at(word, px, py)
        self.flash_status(f"Placed “{word}” — drag it anywhere on the page")

    def _ped_box_word(self, word):
        e = self._ped_entry()
        if e is not None and e.card is not None:
            sel = getattr(self, "_ped_card_sel", None)
            if sel is not None and sel < len(e.card["words"]):
                self._ped.push_undo()
                words = list(e.card["words"])
                words[sel] = word
                # a tile picked from the word box is a NEW word: any pin the
                # slot had from an earlier text edit must not stick to it
                pins = dict(e.card.get("slot_pics") or {})
                pins.pop(str(sel), None)
                e.card = dict(e.card, words=words, slot_pics=pins)
                self._ped.dirty = True
                self._ped_refresh_card_page(e)
                self._ped_strip_draw()
                self._ped_sync_card_sel()
                self.flash_status(f"Card changed to “{word}”")
            else:
                self._ped_card_add_words_list([word])
        else:
            self._ped_place_word = word
            self._ped_word_var.set(word)
            self.flash_status(f"Click on the page to place “{word}”")

    def _ped_card_add_words(self, src=None):
        src = src if src is not None else self._ped_cardword_entry
        raw = src.get().replace(",", "\n").split("\n")
        words = [w.strip() for w in raw if w.strip()]
        if not words:
            return
        src.delete(0, "end")
        self._ped_card_add_words_list(words)

    def _ped_card_add_words_list(self, words):
        e = self._ped_entry()
        if e is None:
            return
        if e.card is None:
            # v15.56: on a page that isn't a card page the words land as
            # free cards on this page (staggered at the centre so several
            # at once don't stack invisibly) instead of staging into the
            # Stamp box, where they never visibly arrived.
            try:
                cx, cy = self._ped_center_pt()
            except Exception:
                W, H = e.size
                cx, cy = W / 2, H / 2
            W, H = e.size
            for k, w in enumerate(words):
                n = (len(e.items) + k) % 5
                px = min(max(cx + n * 18.0, 30.0), max(30.0, W - 30.0))
                py = min(max(cy + n * 18.0, 30.0), max(30.0, H - 30.0))
                self._ped_place_card_at(w, px, py)
            self.flash_status(f"Placed {len(words)} word(s) on page {self._ped_cur + 1} — drag them anywhere")
            return
        have = {w.lower() for w in e.card["words"]}
        add = [w for w in words if w.lower() not in have]
        if not add:
            self.flash_status("Those words are already on this card page")
            return
        self._ped.push_undo()
        e.card = dict(e.card, words=list(e.card["words"]) + add)
        self._ped.dirty = True
        self._ped_refresh_card_page(e)
        self._ped_strip_draw()
        self.flash_status(f"Added {len(add)} word(s) to the card on page {self._ped_cur + 1}")

    def _ped_add_card_page(self):
        sess = self._ped
        if sess is None:
            return
        e = self._ped_new_card_entry([], dict(self._ped_card_spec([]),
                                              layout=self.pdf_layout_var.get(),
                                              paper=self.pdf_paper.get()))
        sess.push_undo()
        sess.pages.insert(self._ped_cur + 1, e)
        sess.dirty = True
        self._ped_cur += 1
        self._ped_sel = None
        self._ped_refresh_card_page(e)
        self.flash_status("New card page — add words from the Cards section")
        self._ped_changed()

    def _ped_fill_from_word_bank(self):
        sess = self._ped
        if sess is None:
            return
        try:
            words = [str(w).strip() for w in self.get_words() if str(w).strip()]
        except Exception:
            words = []
        if not words:
            return messagebox.showinfo(_pal().APP_NAME, "Your Word Lists are empty.")
        have = set()
        for pg in sess.pages:
            if pg.card is not None:
                have.update(w.lower() for w in pg.card["words"])
        todo = [w for w in words if w.lower() not in have]
        if not todo:
            self.flash_status("Every Word List word is already on a card page")
            return
        sess.push_undo()
        touched = []
        pages = [pg for pg in sess.pages if pg.card is not None]
        if not pages:
            first = self._ped_new_card_entry([])
            sess.pages.insert(self._ped_cur + 1, first)
            pages = [first]
        pi = 0
        for w in todo:
            while pi < len(pages):
                spec = pages[pi].card
                per = _pal().aac_boards.PECS_LAYOUTS.get(spec["layout"], (2, 4))
                if len(spec["words"]) < per[0] * per[1]:
                    break
                pi += 1
            if pi >= len(pages):
                ne = self._ped_new_card_entry([])
                sess.pages.append(ne)
                pages.append(ne)
            pg = pages[pi]
            pg.card = dict(pg.card, words=list(pg.card["words"]) + [w])
            if pg not in touched:
                touched.append(pg)
        sess.dirty = True
        for pg in touched:
            self._ped_refresh_card_page(pg)
        self._ped_goto(sess.pages.index(touched[0]))
        self.flash_status(f"Filled {len(todo)} word(s) onto card pages")
        self._ped_changed()

    def _ped_card_opt_now(self):
        """Menus/checkboxes: apply the shared card options to this page at once."""
        self._ped_card_opt_apply()


    def _ped_card_opt_apply(self):
        self._ped_card_after = None
        e = self._ped_entry()
        if e is None or e.card is None or self._ped is None:
            return
        new = self._ped_card_spec(e.card["words"])
        new["words"] = e.card["words"]
        # v15.13: the size/position sliders are gone, so a page's saved scales
        # survive menu/checkbox changes instead of resetting to the defaults
        for k in ("word_scale", "pic_scale", "pic_y", "word_y", "slot_pics"):
            if k in e.card:
                new[k] = e.card[k]
        if all(e.card.get(k) == v for k, v in new.items()):
            return
        self._ped.push_undo()
        e.card = new
        w, h = (595.28, 841.89) if new["paper"].lower() == "a4" else (612.0, 792.0)
        e.w, e.h = w, h
        self._ped.dirty = True
        self._ped_refresh_card_page(e)
        self._ped_changed()

    # ------------------------------------------------------------ v15.5 ribbon state
    def _ped_rib_show(self, name):
        self._ped_rib_tab = name
        for n, f in self._ped_rib_groups.items():
            if n == name:
                f.pack(fill="x")
            else:
                f.pack_forget()
        for n, b in self._ped_rib_btns.items():
            try:
                b.configure(fg_color=_pal().ACCENT if n == name else "transparent",
                            text_color="white" if n == name else ("#222222", "#e8ebf0"))
            except Exception:
                pass

    def _ped_toggle_tool(self, tool):
        """v15.13: tapping the active tool again drops back to Select — tools
        no longer stay stuck on.  Programmatic resets keep calling
        _ped_set_tool directly, so one-shot returns are unaffected."""
        if tool != "select" and getattr(self, "_ped_tool", None) == tool:
            self._ped_set_tool("select")
        else:
            self._ped_set_tool(tool)

    def _ped_set_tool(self, tool):
        self._ped_tool = tool
        self._ped_poly = None
        self._ped_ghost = None
        hints = {"select": "Click an object to select it · Shift-click adds · drag empty space to "
                           "rubber-band select · drag the ⟳ handle to rotate · arrow keys nudge",
                 "highlight": "Drag across the page to highlight",
                 "underline": "Drag under the words to underline them",
                 "strike": "Drag across the words to strike through them",
                 "freehand": "Drag to draw freehand",
                 "text": "Click the page and type — click existing text to edit it right there",
                 "rect": "Drag to draw a rectangle", "ellipse": "Drag to draw an oval",
                 "circle": "Drag to draw a circle", "line": "Drag to draw a line",
                 "arrow": "Drag to draw an arrow",
                 "polygon": "Click each corner · double-click (or Enter) to finish",
                 "measure": "Drag on the page to measure (inches)",
                 "pan": "Drag to move around the page (or hold Space with any tool)",
                 "harvest_pic": "Drag around a picture on the page to save it into your Library",
                 "harvest_words": "Drag around words on the page to save them into your Word Lists",
                 "snip": "Drag around words or a picture — then pick Save picture, Save words, or both"}
        try:
            self._ped_hint_text = "PDF Editor · " + hints.get(tool, "")
            self.info.configure(text=self._ped_hint_text)
        except Exception:
            pass
        for t, b in getattr(self, "_ped_tool_btns", {}).items():
            try:
                b.configure(fg_color=_pal().ACCENT if t == tool and tool != "select" else ("white", "gray30"))
            except Exception:
                pass
        for t, b in getattr(self, "_ped_home_tool_btns", {}).items():
            try:
                b.configure(fg_color=_pal().ACCENT if t == tool and tool != "select" else ("white", "gray30"))
            except Exception:
                pass
        for t, b in getattr(self, "_ped_rail_btns", {}).items():
            try:
                # v15.16: labeled rail — inactive glyphs keep their dark /
                # light text (white on transparent would vanish in light
                # mode); only the active tool goes white on purple.
                active = (t == tool)
                b.configure(fg_color=self.PED_PURPLE_D if active else "transparent",
                            text_color="#FFFFFF" if active else ("#222222", "#f0f0f4"))
            except Exception:
                pass
        cursors = {"select": "arrow", "text": "xterm", "pan": "hand2"}
        try:
            self.canvas.configure(cursor=cursors.get(
                tool, "crosshair" if tool not in ("select",) else "arrow"))
        except Exception:
            pass
        self._ped_draw_overlay()
        self._ped_props_refresh()     # v15.10: tool change re-rolls the sections

    def _ped_toggle_snap(self):
        self._ped_snap = not self._ped_snap
        # v15.12: the button itself shows the state — purple when snapping
        # is on, so it reads at a glance, not just from the label text.
        self._ped_snap_btn.configure(
            text="Snap: on" if self._ped_snap else "Snap: off",
            fg_color=self.PED_PURPLE if self._ped_snap else "gray30")

    def _ped_toggle_grid(self):
        self._ped_grid = not self._ped_grid
        self._ped_grid_btn.configure(text="Grid: on" if self._ped_grid else "Grid: off")
        self._ped_draw_overlay()

    def _ped_lw_pick(self, val):
        self._ped_shape_lw = float(str(val).split()[0])
        it = self._ped_sel
        if it is not None and it.kind in ("rect", "ellipse", "line", "arrow", "freehand") \
                and self._ped_entry() and it in self._ped_entry().items:
            self._ped.push_undo()
            it.lw = self._ped_shape_lw
            self._ped.dirty = True
            self._ped_draw_overlay()
        self._ped_lw_menu.set(val)

    def _ped_pick_shape_color(self, fill):
        _, hexv = colorchooser.askcolor(color=self._ped_shape_fill if fill else self._ped_shape_line,
                                        title="Fill colour" if fill else "Line colour")
        if not hexv:
            return
        it = self._ped_sel
        live = it is not None and self._ped_entry() and it in self._ped_entry().items
        if fill:
            self._ped_shape_fill = hexv
            if live and it.kind in ("rect", "ellipse", "freehand"):
                self._ped.push_undo()
                it.fill = hexv
                if it.kind == "rect":
                    it.border = True
                self._ped.dirty = True
        else:
            self._ped_shape_line = hexv
            if live and it.kind in ("rect", "ellipse", "line", "arrow", "freehand", "text"):
                self._ped.push_undo()
                it.color = hexv
                self._ped.dirty = True
        self._ped_draw_overlay()

    def _ped_refresh_words(self):
        words = []
        for src in (getattr(self, "_pdf_box_words", []), getattr(self, "_pdf_words", [])):
            for w in src:
                w = str(w).strip()
                if w and w not in words:
                    words.append(w)
        try:
            for w in self.get_words():
                if w not in words:
                    words.append(w)
        except Exception:
            pass
        self._ped_word_combo.configure(values=words or [" "])
        combo2 = getattr(self, "_ped_rib_word_combo", None)   # v15.12: the ribbon Edit copy
        if combo2 is not None:
            try:
                combo2.configure(values=words or [" "])
            except Exception:
                pass

    # ------------------------------------------------------------ geometry
    def _ped_entry(self):
        s = self._ped
        if s is None or not s.pages:
            return None
        self._ped_cur = max(0, min(self._ped_cur, len(s.pages) - 1))
        return s.pages[self._ped_cur]

    def _ped_insets(self):
        """Horizontal canvas space reserved for the v15.9 tool rail (left) and
        the properties/objects panel (right) while the editor is showing."""
        if not (self._ped_active and getattr(self, "_ped_ui_built", False)
                and self.tabs.get() == "PDF Studio"):
            return (0, 0)
        left = 84 if self._ped_rail_on else 0  # v15.22: rail is 76 wide now
        right = 288 if self._ped_panel_on else 0
        return (left, right)

    def _ped_layout(self):
        # v15.9: memoised — this runs many times per mouse event and the Tk
        # winfo calls dominated drag time.  The key covers every input.
        e = self._ped_entry()
        if e is None:
            return 1.0, 0.0, 0.0, 1.0
        cw, ch = max(self.canvas.winfo_width(), 200), max(self.canvas.winfo_height(), 200)
        ins = self._ped_insets()
        two = self._ped_two_active()
        row = self._ped_row_active()
        base = self._ped_two_base_idx() if two else -1
        okey = None
        if two:
            _op = self._ped.pages[base if self._ped_cur == base + 1 else base + 1]
            okey = (_op.key, _op.w, _op.h)
        rsig = None
        if row:
            rsig = tuple((p.key, p.w, p.h) for p in self._ped.pages)
        key = (cw, ch, id(self._ped), e.key, e.w, e.h, self._ped_cur, base, okey,
               round(self._ped_zoom, 4),
               round(self._ped_pan[0], 1), round(self._ped_pan[1], 1), ins, two, row, rsig)
        cache = self._ped_layout_cache
        if cache is not None and cache[0] == key:
            return cache[1]
        W, H = e.size
        if row:
            # v15.65: the row shares one scale (the pair's fit) and lays
            # every page left to right with a 24pt gutter; pan slides it.
            fit, _gap, _ins = self._ped_row_geometry()
            s = max(0.05, min(8.0, fit * self._ped_zoom))
            ox = self._ped_row_origins(s)[self._ped_cur] + self._ped_pan[0]
            oy = (ch - H * s) / 2 + self._ped_pan[1]
        elif two:
            # v15.63: the pair shares one scale and one row — focused
            # page, gutter, other page — so the pages can never
            # overlap, however far you zoom.
            fit, _gap, _ins = self._ped_pair_geometry()
            s = max(0.05, min(8.0, fit * self._ped_zoom))
            ox, _ox2 = self._ped_pair_origins(s)
            ox = ox + self._ped_pan[0]
            oy = (ch - H * s) / 2 + self._ped_pan[1]
        else:
            avail = max(80, cw - ins[0] - ins[1])
            fit = max(0.01, min((avail - 24) / W, (ch - 24) / H))
            s = max(0.05, min(8.0, fit * self._ped_zoom))
            ox = ins[0] + (avail - W * s) / 2 + self._ped_pan[0]
            oy = (ch - H * s) / 2 + self._ped_pan[1]
        out = (s, ox, oy, fit)
        self._ped_layout_cache = (key, out)
        return out

    def _ped_bar_zoom_changed(self, pct):
        """v15.16: bottom-bar zoom slider.  The displayed percent is
        s*100 where s = _ped_zoom * fit, so convert back through fit."""
        try:
            fit = self._ped_layout()[3]
            self._ped_set_zoom((float(pct) / 100.0) / max(fit, 0.01))
        except Exception:
            pass

    def _ped_set_zoom(self, z, focus=None):
        e = self._ped_entry()
        if e is None:
            return
        s0, ox0, oy0, fit = self._ped_layout()
        z = max(0.05 / fit, min(8.0 / fit, z))
        if abs(z - self._ped_zoom) < 1e-4:
            return
        W, H = e.size
        cw, ch = max(self.canvas.winfo_width(), 200), max(self.canvas.winfo_height(), 200)
        fx, fy = focus if focus else (cw / 2, ch / 2)
        # v15.12: two fixes for "zooming loses the page".  (1) The anchor is
        # clamped onto the page itself — zooming with the cursor over the
        # rail/panel used to anchor an off-page point and push the page
        # away.  (2) The pan math must use the real insets (rail left, panel
        # right), not the full canvas width: with the 15.9 furniture the old
        # formula shifted the page sideways by half the inset gap on every
        # zoom step, so a flick of the wheel marched it off-screen.
        fx = min(max(fx, ox0), ox0 + W * s0)
        fy = min(max(fy, oy0), oy0 + H * s0)
        px, py = (fx - ox0) / s0, (fy - oy0) / s0                # page point under the focus
        self._ped_zoom = z
        s1 = fit * z
        ins0, ins1 = self._ped_insets()
        if self._ped_row_active():
            # v15.65: invert the row geometry, so zooming keeps the
            # focused page's point under the cursor fixed and the row
            # slides with it.
            oxf = self._ped_row_origins(s1)[self._ped_cur]
            self._ped_pan = [fx - px * s1 - oxf,
                             fy - py * s1 - (ch - H * s1) / 2]
        elif self._ped_two_active():
            # v15.63: invert the pair-row geometry, so zooming keeps
            # the focused page's point under the cursor fixed and the
            # other page exactly one gutter away — never overlapping.
            oxf, _ox2 = self._ped_pair_origins(s1)
            self._ped_pan = [fx - px * s1 - oxf,
                             fy - py * s1 - (ch - H * s1) / 2]
        else:
            avail = max(80, cw - ins0 - ins1)
            self._ped_pan = [fx - px * s1 - ins0 - (avail - W * s1) / 2,
                             fy - py * s1 - (ch - H * s1) / 2]
        self._ped_draw()

    def _ped_fit(self):
        self._ped_zoom, self._ped_pan = 1.0, [0.0, 0.0]
        self._ped_draw()

    # ------------------------------------------------------------ v15.53 two pages
    # v15.55: the pair is (base, base+1) and the FOCUSED page is either
    # of the two — clicking the other page moves the focus, never the
    # pair, so editing one page never yanks the view to a single page.
    def _ped_two_base_idx(self):
        """Left page of the pair, clamped to the document (0 when the
        document has fewer than two pages)."""
        s = self._ped
        if s is None or len(s.pages) < 2:
            return 0
        return max(0, min(getattr(self, "_ped_two_base", 0), len(s.pages) - 2))

    def _ped_two_active(self):
        """True when the toggle is on AND the pair has a second page."""
        s = self._ped
        return bool(getattr(self, "_ped_two", False)) and s is not None \
            and self._ped_two_base_idx() + 1 < len(s.pages)

    def _ped_pair_geometry(self):
        """(shared fit, gutter, insets) for the pair, or None.  One
        scale for both pages keeps them the same size; zoom multiplies
        it for both, and the pan moves both together.  v15.63: the pair
        is laid out as one row — focused page, a fixed gutter, other
        page — centred in the free canvas area, so zooming can never
        push the pages over each other (the old half-centring let a
        zoomed page spill out of its half onto its neighbour)."""
        if not self._ped_two_active():
            return None
        s = self._ped
        base = self._ped_two_base_idx()
        foc = s.pages[self._ped_cur]
        oth = s.pages[base if self._ped_cur == base + 1 else base + 1]
        cw, ch = max(self.canvas.winfo_width(), 200), max(self.canvas.winfo_height(), 200)
        ins = self._ped_insets()
        avail = max(80, cw - ins[0] - ins[1])
        W, H = foc.size
        W2, H2 = oth.size
        fit = max(0.01, min((avail - 24) / (W + W2), (ch - 24) / H,
                            (ch - 24) / H2))
        return (fit, 24, ins)

    def _ped_pair_origins(self, s):
        """(focused ox, other ox) for the pair at scale s, before pan.
        The pair row is centred in the free canvas area; the focused
        page is left or right depending on which side of the pair it
        is, and the other page sits exactly one gutter away — the two
        rects can never overlap at any zoom."""
        base = self._ped_two_base_idx()
        foc = self._ped.pages[self._ped_cur]
        oth = self._ped.pages[base if self._ped_cur == base + 1 else base + 1]
        cw = max(self.canvas.winfo_width(), 200)
        ins = self._ped_insets()
        avail = max(80, cw - ins[0] - ins[1])
        W, _H = foc.size
        W2, _H2 = oth.size
        pair_w = W * s + 24 + W2 * s
        x0 = ins[0] + (avail - pair_w) / 2
        if self._ped_cur == base:
            return x0, x0 + W * s + 24
        return x0 + W2 * s + 24, x0

    def _ped_two_layout(self):
        """(idx, scale, ox, oy, pw, ph) for the OTHER page of the pair,
        or None.  Same shared scale, zoom and pan as the focused page,
        one gutter away on its own side — the pair is one view, not a
        page plus a preview."""
        if self._ped_row_active():
            return None
        if not self._ped_two_active():
            return None
        base = self._ped_two_base_idx()
        idx = base if self._ped_cur == base + 1 else base + 1
        e2 = self._ped.pages[idx]
        fit, _gap, _ins = self._ped_pair_geometry()
        s2 = max(0.05, min(8.0, fit * self._ped_zoom))
        W2, H2 = e2.size
        ch = max(self.canvas.winfo_height(), 200)
        _oxf, ox2 = self._ped_pair_origins(s2)
        ox2 = ox2 + self._ped_pan[0]
        oy2 = (ch - H2 * s2) / 2 + self._ped_pan[1]
        return (idx, s2, ox2, oy2, max(1, int(W2 * s2)), max(1, int(H2 * s2)))

    # ------------------------------------------------------------ v15.65 all-pages row
    # One row, every page, left to right in document order, at the same
    # size the pair view uses — so a third, fourth, … page just shows
    # up alongside pages one and two.  The row is anchored at the left
    # (page 0 at the left inset) and the shared pan slides it: focus
    # changes never move the row by themselves, so a press always maps
    # onto the layout the user clicked; walking (◀ ▶), strip clicks and
    # entering the mode scroll the focused page into view instead.
    def _ped_row_active(self):
        """True when the row toggle is on AND there is more than one page."""
        s = self._ped
        return bool(getattr(self, "_ped_row", False)) and s is not None \
            and len(s.pages) >= 2

    def _ped_row_geometry(self):
        """(shared fit, gutter, insets) for the row: the same fit the pair
        view would use for the focused page and its neighbour, so row
        pages are exactly pair-sized."""
        s = self._ped
        foc = s.pages[self._ped_cur]
        nb = s.pages[self._ped_cur + 1] if self._ped_cur + 1 < len(s.pages) \
            else s.pages[self._ped_cur - 1]
        cw, ch = max(self.canvas.winfo_width(), 200), max(self.canvas.winfo_height(), 200)
        ins = self._ped_insets()
        avail = max(80, cw - ins[0] - ins[1])
        W, H = foc.size
        W2, H2 = nb.size
        fit = max(0.01, min((avail - 24) / (W + W2), (ch - 24) / H,
                            (ch - 24) / H2))
        return (fit, 24, ins)

    def _ped_row_origins(self, s):
        """Left edge of every page at scale s, before pan."""
        ins = self._ped_insets()
        out = []
        x = ins[0]
        for p in self._ped.pages:
            out.append(x)
            x += p.size[0] * s + 24
        return out

    def _ped_row_layout(self):
        """[(idx, scale, ox, oy, pw, ph)] for every page, or [] when the
        row is not active.  Same shared scale, zoom and pan for all."""
        if not self._ped_row_active():
            return []
        fit, _gap, _ins = self._ped_row_geometry()
        s = max(0.05, min(8.0, fit * self._ped_zoom))
        origins = self._ped_row_origins(s)
        ch = max(self.canvas.winfo_height(), 200)
        out = []
        for i, p in enumerate(self._ped.pages):
            W, H = p.size
            out.append((i, s, origins[i] + self._ped_pan[0],
                        (ch - H * s) / 2 + self._ped_pan[1],
                        max(1, int(W * s)), max(1, int(H * s))))
        return out

    def _ped_row_page_at(self, x, y):
        """Index of the row page whose rect contains a canvas point, else None."""
        for (i, _s, ox, oy, pw, ph) in self._ped_row_layout():
            if ox <= x <= ox + pw and oy <= y <= oy + ph:
                return i
        return None

    def _ped_row_ensure_visible(self, i):
        """Slide the row (pan only) so page i is fully inside the free
        canvas area; a page wider than the area aligns to its left edge."""
        lay = self._ped_row_layout()
        if not (0 <= i < len(lay)):
            return
        _idx, _s, ox, _oy, pw, _ph = lay[i]
        cw = max(self.canvas.winfo_width(), 200)
        ins = self._ped_insets()
        left, right = ins[0], cw - ins[1]
        if pw >= right - left:
            self._ped_pan[0] += left - ox
        elif ox < left:
            self._ped_pan[0] += left - ox
        elif ox + pw > right:
            self._ped_pan[0] -= (ox + pw) - right

    def _ped_row_center_focused(self):
        """Pan so the focused page sits centred in the free area (used
        once, when the row is switched on)."""
        if not self._ped_row_active():
            return
        fit, _gap, _ins = self._ped_row_geometry()
        s = max(0.05, min(8.0, fit * self._ped_zoom))
        origins = self._ped_row_origins(s)
        cw = max(self.canvas.winfo_width(), 200)
        ins = self._ped_insets()
        avail = max(80, cw - ins[0] - ins[1])
        W, _H = self._ped.pages[self._ped_cur].size
        self._ped_pan[0] = ins[0] + (avail - W * s) / 2 - origins[self._ped_cur]

    def _ped_row_img(self, e, s):
        """The row view's render of a page (synchronous, cached): the
        worker only has slots for the focused page and the pair's other
        page, so the row renders each page itself at display scale —
        the same Session.render_page the worker calls."""
        if e.card is not None and e.idx not in self._ped.card_images:
            self._ped_refresh_card_page(e)
        key = (e.key, round(min(s, MAX_RENDER_PX / max(e.w, e.h, 1.0)), 3))
        cache = getattr(self, "_ped_row_imgs", None)
        if cache is None:
            cache = self._ped_row_imgs = {}
        slot = cache.get(key)
        if slot is not None:
            return slot
        if len(cache) > 32:
            cache.clear()
        try:
            img = self._ped.render_page(e.src, e.idx, e.rot, s)
        except Exception:
            return None
        slot = {"key": e.key, "scale": s, "img": img, "photo": None, "psize": None}
        cache[key] = slot
        return slot

    def _ped_row_release_scroll(self):
        """After a gesture that changed the row focus, slide the newly
        focused page fully into view (never during the gesture itself,
        or the press would map onto a moved layout)."""
        if not getattr(self, "_ped_row_focused_this_press", False):
            return
        self._ped_row_focused_this_press = False
        if self._ped_row_active():
            self._ped_row_ensure_visible(self._ped_cur)
            self._ped_draw()

    def _ped_sync_view_buttons(self):
        """Sync the Two-pages and All-pages buttons (ribbon + bar)."""
        for _b, _on, _label in (
                (getattr(self, "_ped_two_btn", None), getattr(self, "_ped_two", False), "Two pages"),
                (getattr(self, "_ped_two_bar_btn", None), getattr(self, "_ped_two", False), "Two pages"),
                (getattr(self, "_ped_row_btn", None), getattr(self, "_ped_row", False), "All pages"),
                (getattr(self, "_ped_row_bar_btn", None), getattr(self, "_ped_row", False), "All pages")):
            if _b is None:
                continue
            try:
                _b.configure(text=f"{_label}: {'on' if _on else 'off'}",
                             fg_color=self.PED_PURPLE if _on else "gray30")
            except Exception:
                pass

    def _ped_toggle_row(self):
        self._ped_row = not getattr(self, "_ped_row", False)
        self._ped_two_moved = None
        self._ped_two_hover = False
        self._ped_row_hover = None
        if self._ped_row:
            self._ped_two = False
            if self._ped_row_active():
                self._ped_row_center_focused()
        else:
            self._ped_pan = [0.0, 0.0]
        self._ped_sync_view_buttons()
        self._ped_changed()
        self._ped_draw()

    def _ped_toggle_two(self):
        self._ped_two = not getattr(self, "_ped_two", False)
        self._ped_two_moved = None
        self._ped_two_hover = False
        self._ped_row_hover = None
        if self._ped_two and getattr(self, "_ped_row", False):
            # switching modes drops the row pan, which could shove the
            # pair view far off-screen; the pair re-centres instead.
            self._ped_row = False
            self._ped_pan = [0.0, 0.0]
        self._ped_sync_view_buttons()
        self._ped_changed()
        self._ped_draw()

    def _ped_request_page2(self, e, s):
        """Second-page render request: its own token slot, so it never
        cancels the active page's request (the worker only honours the
        newest 'page' token, and 'page2' jobs bypass that check)."""
        want = (e.key, round(min(s, MAX_RENDER_PX / max(e.w, e.h, 1.0)), 3))
        if self._ped_pending2 == want:
            return
        self._ped_pending2 = want
        self._ped_token2 += 1
        self._ped_submit(0, ("page2", self._ped, self._ped_token2, e.key, s))


    def _ped_focused_rect_contains(self, x, y):
        """True when a canvas point is on the focused page's rect — the
        tie-breaker for the overlap when zoomed pages overflow their
        halves: the focused page wins ties."""
        e = self._ped_entry()
        if e is None:
            return False
        s, ox, oy, _ = self._ped_layout()
        pw, ph = max(1, int(e.size[0] * s)), max(1, int(e.size[1] * s))
        return ox <= x <= ox + pw and oy <= y <= oy + ph

    def _ped_two_track(self, event):
        """During a move drag: light up the other page's frame while the
        pointer hovers it (redrawn only when the state changes, like the
        strip target).  v15.65: in row mode any other page in the row
        lights up the same way."""
        if self._ped_row_active():
            hov = self._ped_row_page_at(event.x, event.y)
            if hov == self._ped_cur:
                hov = None
            if hov != getattr(self, "_ped_row_hover", None):
                self._ped_row_hover = hov
                self._ped_draw_overlay()
            return
        lay = self._ped_two_layout()
        hov = False
        if lay is not None:
            _, _, ox, oy, pw, ph = lay
            hov = ox <= event.x <= ox + pw and oy <= event.y <= oy + ph \
                and not self._ped_focused_rect_contains(event.x, event.y)
        if hov != getattr(self, "_ped_two_hover", False):
            self._ped_two_hover = hov
            self._ped_draw_overlay()

    def _ped_two_drop(self, items, tgt):
        """Move the dragged items onto the other page of the pair.  Like
        _ped_strip_drop, but the view already shows both pages, so there is
        no goto: the selection clears and the moved items get a transient
        outline on the other page instead."""
        src = self._ped_entry()
        if src is None or tgt is None:
            return
        dst = self._ped.pages[tgt]
        TW, TH = dst.size
        moved = []
        for it in list(items):
            if it in src.items:
                src.items.remove(it)
                dx = min(0.0, TW - it.x1) if it.x1 > TW else max(0.0, -it.x0)
                dy = min(0.0, TH - it.y1) if it.y1 > TH else max(0.0, -it.y0)
                if it.points:
                    it.points = [(qx + dx, qy + dy) for qx, qy in it.points]
                it.x0, it.y0, it.x1, it.y1 = it.x0 + dx, it.y0 + dy, it.x1 + dx, it.y1 + dy
                dst.items.append(it)
                moved.append(it)
        self._ped.dirty = True
        self._ped_set_selection([])
        self._ped_two_moved = (tgt, moved)
        self._ped_draw_overlay()
        self.flash_status(f"Moved to page {tgt + 1}")

    # ------------------------------------------------------------ rendering worker
    def _ped_submit(self, pri, job):
        q = getattr(self, "_ped_q", None)
        if q is None:
            q = self._ped_q = queue.PriorityQueue()
            self._ped_seq = itertools.count()
        q.put((pri, next(self._ped_seq), job))
        t = getattr(self, "_ped_thread", None)
        if t is not None and t.is_alive():
            return

        def worker():
            while True:
                try:
                    _, _, job = q.get(timeout=30)
                except queue.Empty:
                    return                                    # idle: die, restarted on demand
                try:
                    kind, sess, token, key, scale = job
                    if sess is not self._ped or (kind == "page" and token != self._ped_token):
                        continue
                    if kind == "thumb" and key in self._ped_thumbs:
                        continue
                    img = sess.render_page(key[0], key[1], key[2], scale)
                    self.ui_call(self._ped_render_done, kind, sess, token, key, scale, img, None)
                except PdfEditError as e:
                    self.ui_call(self._ped_render_done, kind, sess, token, key, scale, None, str(e))
                except Exception as e:                        # a bad page must not kill the worker
                    log.exception("PDF render failed")
                    self.ui_call(self._ped_render_done, kind, sess, token, key, scale, None,
                                 f"Couldn't draw that page ({e}).")
        self._ped_thread = threading.Thread(target=worker, daemon=True)
        self._ped_thread.start()

    def _ped_render_done(self, kind, sess, token, key, scale, img, err):
        """Main thread."""
        if sess is not self._ped or not self._ped_active:
            return
        if kind == "thumb":
            self._ped_thumbs[key] = img if img is not None else "bad"
            self._ped_strip_draw()
            return
        if kind == "page2":
            self._ped_pending2 = None
            if err:
                return
            self._ped_page2 = {"key": key, "scale": scale, "img": img, "photo": None, "psize": None}
            self._ped_draw()
            return
        if token != self._ped_token:
            return
        self._ped_pending = None
        if err:
            self.flash_status(err, ok=False, hold=True)
            return
        self._ped_page = {"key": key, "scale": scale, "img": img, "photo": None, "psize": None}
        self.status.configure(text="Ready", text_color="gray")
        self._ped_draw()

    # ------------------------------------------------------------ drawing the page
    def _ped_changed(self):
        """Something about the page list changed: refresh labels, strip and the page."""
        s = self._ped
        if s is None:
            return
        n = len(s.pages)
        self._ped_cur = max(0, min(self._ped_cur, n - 1))
        # v15.55: keep the pair around the focused page — the pair is
        # (base, base+1) and the focused page must be one of the two;
        # anything that moved focus elsewhere (add/delete/undo/goto)
        # re-anchors it, so an added page just shows up in the pair.
        if n >= 2:
            base = max(0, min(getattr(self, "_ped_two_base", 0), n - 2))
            if self._ped_cur not in (base, base + 1):
                base = min(self._ped_cur, n - 2)
            self._ped_two_base = base
        else:
            self._ped_two_base = 0
        try:
            if self._ped_row_active():
                self._ped_bar_lbl.configure(text=f"Page {self._ped_cur + 1} of {n} · All pages")
            elif self._ped_two_active():
                _base = self._ped_two_base_idx()
                self._ped_bar_lbl.configure(text=f"Pages {_base + 1}–{_base + 2} of {n}")
            else:
                self._ped_bar_lbl.configure(text=f"Page {self._ped_cur + 1} of {n}")
            e0 = s.pages[self._ped_cur]
            W0, H0 = e0.size
            self._ped_pagesize_lbl.configure(
                text=f"Page size: {W0 / 72:.2f} × {H0 / 72:.2f} in  ({W0:.0f} × {H0:.0f} pt)")
            self._ped_file_lbl.configure(
                text=s.title if s.sources else "Card pages — a new document (not from a file)")
            if e0.card is not None and e0.idx not in s.card_images:
                self._ped_refresh_card_page(e0)
        except Exception:
            pass
        self._ped_sync_card_section()
        self._ped_sync_card_sel()
        self._ped_strip_see(self._ped_cur)
        self._ped_strip_draw()
        self._ped_draw()
        self._ped_sync_props()

    def _ped_goto(self, i):
        s = self._ped
        if s is None or not (0 <= i < len(s.pages)):
            return
        self._ped_card_sel = None
        self._ped_cur, self._ped_sel, self._ped_drag = i, None, None
        self._ped_two_moved = None
        self._ped_selset = []
        self._ped_edit_close(commit=True)
        if self._ped_row_active():
            # v15.65: no pan reset in row mode — that would yank the row
            # back to page 1; slide the target page into view instead.
            self._ped_row_ensure_visible(i)
        else:
            self._ped_pan = [0.0, 0.0]
        self._ped_changed()

    def _ped_two_swap_slots(self):
        """After an in-pair focus flip, the two render slots hold the pages
        crossed: swap them so neither page re-renders (each slot is keyed,
        and a stale in-flight render is self-healing — the draw checks the
        key before using a slot)."""
        a, b = getattr(self, "_ped_page", None), getattr(self, "_ped_page2", None)
        if a is None or b is None:
            return
        e = self._ped_entry()
        lay2 = self._ped_two_layout()
        if e is None or lay2 is None:
            return
        o = self._ped.pages[lay2[0]]
        if a["key"] == o.key and b["key"] == e.key:
            self._ped_page, self._ped_page2 = b, a

    def _ped_focus_page(self, i, scroll=False):
        """v15.55: work on page i WITHOUT moving the pair — the pair stays
        put, only the highlight (and the tools' target) moves to i.
        v15.65: with scroll=True (row mode walks and strip clicks) the
        row slides so the newly focused page is fully in view; a press
        never scrolls, or the press would map onto a moved layout."""
        s = self._ped
        if s is None or not (0 <= i < len(s.pages)) or i == self._ped_cur:
            return
        self._ped_card_sel = None
        self._ped_cur, self._ped_sel, self._ped_drag = i, None, None
        self._ped_selset = []
        self._ped_two_moved = None
        self._ped_edit_close(commit=True)
        self._ped_two_swap_slots()
        if scroll and self._ped_row_active():
            self._ped_row_ensure_visible(i)
        self._ped_changed()

    def _ped_step(self, d):
        """v15.55: ◀ ▶ walk the pages one by one, the highlight following
        and the pair sliding only when the highlight leaves it — so every
        page gets its turn highlighted, in order, without jumps.
        v15.65: in row mode the walk just moves the highlight and the
        row slides the newly focused page into view."""
        s = self._ped
        if s is None or not s.pages:
            return
        n = len(s.pages)
        if self._ped_row_active():
            nxt = self._ped_cur + d
            if 0 <= nxt < n:
                self._ped_focus_page(nxt, scroll=True)
            return
        if not self._ped_two_active():
            self._ped_goto(self._ped_cur + d)
            return
        base = self._ped_two_base_idx()
        cur = self._ped_cur
        if d > 0:
            if cur == base:
                nxt = base + 1
            elif cur + 1 < n:
                self._ped_two_base = cur
                nxt = cur + 1
            else:
                return
        else:
            if cur == base + 1:
                nxt = base
            elif base > 0:
                self._ped_two_base = base - 1
                nxt = base - 1
            else:
                return
        self._ped_focus_page(nxt)

    def _ped_sync_zoom_labels(self, s):
        """v15.16: _ped_zoom_lbl is never created anywhere (a 15.14
        leftover), so its configure raised and skipped the bar label
        too — the bar always read 100% no matter the zoom.  Configure
        each label on its own so one missing widget can't silence the
        others."""
        try:
            self._ped_zoom_lbl.configure(text=f"{round(s * 100)}%")
        except Exception:
            pass
        try:
            self._ped_bar_zoom_lbl.configure(text=f"{round(s * 100)}%")
            # Keep the bottom-bar slider in step with the page.
            # Slider.set() does not fire its command, so this can't loop.
            self._ped_bar_zoom.set(max(25, min(300, s * 100)))
        except Exception:
            pass

    def _ped_draw(self):
        c = self.canvas
        if not self._ped_active or self.tabs.get() != "PDF Studio" or self._ped is None:
            return
        e = self._ped_entry()
        c.delete("all")
        c.configure(bg="#2C2F3A" if ctk.get_appearance_mode() == "Dark" else "#F1EDF5")
        if e is None:
            return
        s, ox, oy, fit = self._ped_layout()
        W, H = e.size
        pw, ph = max(1, int(W * s)), max(1, int(H * s))
        if self._ped_row_active():
            # v15.65: every page in the row — shadow + sheet + its own
            # cached render, then the overlay (which paints every
            # page's items and the focused page's purple border).
            self._ped_sync_zoom_labels(s)
            from PIL import ImageTk
            for (idx2, s2, ox2, oy2, pw2, ph2) in self._ped_row_layout():
                e2 = self._ped.pages[idx2]
                c.create_rectangle(ox2 + 4, oy2 + 4, ox2 + pw2 + 4, oy2 + ph2 + 4,
                                   fill="#000000", stipple="gray25",
                                   outline="", tags=("ped-page",))
                c.create_rectangle(ox2, oy2, ox2 + pw2, oy2 + ph2,
                                   fill="#FFFFFF", outline="#888888", tags=("ped-page",))
                slot = self._ped_row_img(e2, s2)
                if slot is not None:
                    if slot["photo"] is None or slot["psize"] != (pw2, ph2):
                        im = slot["img"] if slot["img"].size == (pw2, ph2) \
                            else slot["img"].resize((pw2, ph2), Image.BILINEAR)
                        slot["photo"], slot["psize"] = ImageTk.PhotoImage(im), (pw2, ph2)
                    c.create_image(ox2, oy2, image=slot["photo"], anchor="nw",
                                   tags=("ped-img",))
            self._ped_draw_overlay()
            return
        c.create_rectangle(ox + 4, oy + 4, ox + pw + 4, oy + ph + 4, fill="#000000", stipple="gray25",
                           outline="", tags=("ped-shadow",))
        c.create_rectangle(ox, oy, ox + pw, oy + ph, fill="#FFFFFF", outline="#888888", tags=("ped-page",))
        # v15.16: _ped_zoom_lbl is never created anywhere (a 15.14
        # leftover), so its configure raised and skipped the bar label
        # too — the bar always read 100% no matter the zoom.  Configure
        # each label on its own so one missing widget can't silence the
        # others.
        self._ped_sync_zoom_labels(s)
        pg = self._ped_page
        if pg is not None and pg["key"] == e.key:
            from PIL import ImageTk
            if pg["photo"] is None or pg["psize"] != (pw, ph):
                im = pg["img"] if pg["img"].size == (pw, ph) else pg["img"].resize((pw, ph), Image.BILINEAR)
                pg["photo"], pg["psize"] = ImageTk.PhotoImage(im), (pw, ph)
            c.create_image(ox, oy, image=pg["photo"], anchor="nw", tags=("ped-img",))
            eff = min(s, MAX_RENDER_PX / max(e.w, e.h, 1.0))          # what PyMuPDF will really draw
            if abs(pg["img"].size[0] - W * eff) > max(2, W * eff * 0.02):   # blurry: ask for a crisp one
                self._ped_request_page(e, s)
        else:
            c.create_text(ox + pw / 2, oy + ph / 2, text="Rendering page…", fill="#888888",
                          font=("Arial", 14))
            self._ped_request_page(e, s)
        # v15.53: the other page of the pair — same shadow + sheet +
        # rendered image, drawn before the overlay so the overlay can
        # paint its items.
        lay2 = self._ped_two_layout()
        if lay2 is not None:
            idx2, s2, ox2, oy2, pw2, ph2 = lay2
            e2 = self._ped.pages[idx2]
            c.create_rectangle(ox2 + 4, oy2 + 4, ox2 + pw2 + 4, oy2 + ph2 + 4,
                               fill="#000000", stipple="gray25", outline="", tags=("ped-page2",))
            c.create_rectangle(ox2, oy2, ox2 + pw2, oy2 + ph2,
                               fill="#FFFFFF", outline="#888888", tags=("ped-page2",))
            if e2.card is not None and e2.idx not in self._ped.card_images:
                self._ped_refresh_card_page(e2)
            pg2 = self._ped_page2
            if pg2 is not None and pg2["key"] == e2.key:
                from PIL import ImageTk
                if pg2["photo"] is None or pg2["psize"] != (pw2, ph2):
                    im = pg2["img"] if pg2["img"].size == (pw2, ph2) else pg2["img"].resize((pw2, ph2), Image.BILINEAR)
                    pg2["photo"], pg2["psize"] = ImageTk.PhotoImage(im), (pw2, ph2)
                c.create_image(ox2, oy2, image=pg2["photo"], anchor="nw", tags=("ped-img2",))
            else:
                c.create_text(ox2 + pw2 / 2, oy2 + ph2 / 2, text="Rendering page…", fill="#888888",
                              font=("Arial", 14))
                self._ped_request_page2(e2, s2)
        self._ped_draw_overlay()

    def _ped_request_page(self, e, s):
        want = (e.key, round(min(s, MAX_RENDER_PX / max(e.w, e.h, 1.0)), 3))
        if self._ped_pending == want:
            return
        self._ped_pending = want
        self._ped_token += 1                      # only the newest page request is honoured
        self.status.configure(text=f"Drawing page {self._ped_cur + 1}…", text_color=_pal().ACCENT)
        self._ped_submit(0, ("page", self._ped, self._ped_token, e.key, s))

    # ------------------------------------------------------------ drawing the overlay items
    def _ped_pt(self, x, y):
        s, ox, oy, _ = self._ped_layout()
        return ox + x * s, oy + y * s

    def _ped_draw_items(self, c, e, s, ox, oy, cids_map):
        """v15.53: paint one page's items at the given layout.
        cids_map collects item->canvas ids for the active page, or
        None for a preview page whose items are never hit-tested."""
        from PIL import ImageTk
        for it in e.items:
            if it.hidden:
                continue                       # v15.9: hidden objects are not drawn
            x0, y0, x1, y1 = ox + it.x0 * s, oy + it.y0 * s, ox + it.x1 * s, oy + it.y1 * s
            lwpx = max(1, int(it.lw * s))
            cids = []
            if it.kind == "rect" and it.rot:
                cpts = []
                for qx, qy in item_corners(it):
                    cpts.extend((ox + qx * s, oy + qy * s))
                if it.border:
                    cids.append(c.create_polygon(cpts, fill=it.fill or "", outline=it.color,
                                                 width=lwpx, tags=("ped-ov",)))
                elif it.opacity >= 0.99:
                    cids.append(c.create_polygon(cpts, fill=it.color, outline="", tags=("ped-ov",)))
                else:
                    cids.append(c.create_polygon(cpts, fill=it.color, outline=it.color,
                                                 stipple="gray50", tags=("ped-ov",)))
            elif it.kind == "rect":
                if it.border:
                    cids.append(c.create_rectangle(x0, y0, x1, y1, fill=it.fill or "", outline=it.color,
                                                   width=lwpx, tags=("ped-ov",)))
                elif it.opacity >= 0.99:
                    cids.append(c.create_rectangle(x0, y0, x1, y1, fill=it.color, outline="",
                                                   tags=("ped-ov",)))
                else:
                    cids.append(c.create_rectangle(x0, y0, x1, y1, fill=it.color, outline=it.color,
                                                   stipple="gray50", tags=("ped-ov",)))
            elif it.kind == "ellipse" and it.rot:
                import math
                cx, cy = item_center(it)
                cpts = []
                for k in range(40):
                    a = 2 * math.pi * k / 40
                    qx, qy = _rot_xy(cx + it.w / 2 * math.cos(a), cy + it.h / 2 * math.sin(a),
                                     cx, cy, it.rot)
                    cpts.extend((ox + qx * s, oy + qy * s))
                cids.append(c.create_polygon(cpts, fill=it.fill or "", outline=it.color,
                                             width=lwpx, tags=("ped-ov",)))
            elif it.kind == "ellipse":
                cids.append(c.create_oval(x0, y0, x1, y1, fill=it.fill or "", outline=it.color,
                                          width=lwpx, tags=("ped-ov",)))
            elif it.kind in ("line", "arrow", "freehand"):
                pts = it.points if it.points else [(it.x0, it.y0), (it.x1, it.y1)]
                cpts = []
                for qx, qy in pts:
                    cpts.extend((ox + qx * s, oy + qy * s))
                if it.kind == "freehand" and it.closed and it.fill and len(pts) > 2:
                    cids.append(c.create_polygon(cpts, fill=it.fill, outline="", tags=("ped-ov",)))
                cids.append(c.create_line(cpts, fill=it.color, width=lwpx,
                                          smooth=(it.kind == "freehand"),
                                          arrow="last" if it.kind == "arrow" else "none",
                                          tags=("ped-ov",)))
            else:                      # card / text / image: the raster IS the appearance
                ax0, ay0, ax1, ay1 = item_aabb(it)     # v15.9: footprint of the rotated content
                key = (it.uid, int((ax1 - ax0) * s), int((ay1 - ay0) * s), it.color, it.label,
                       it.loading, it.rot, it.word, it.text, round(it.size, 1), it.font,
                       it.bold, it.italic, it.align, it.crop, id(it.img))
                ph = self._ped_card_photos.get(key)
                if ph is None:
                    if len(self._ped_card_photos) > 200:
                        self._ped_card_photos.clear()
                    ph = ImageTk.PhotoImage(item_raster(it, max(0.5, s)))
                    self._ped_card_photos[key] = ph
                cids.append(c.create_image(ox + ax0 * s, oy + ay0 * s, image=ph, anchor="nw",
                                           tags=("ped-ov",)))
            if cids and cids_map is not None:
                cids_map[it.uid] = cids

    def _ped_draw_overlay(self):
        c = self.canvas
        c.delete("ped-ov")
        e = self._ped_entry()
        if e is None:
            return
        from PIL import ImageTk
        s, ox, oy, _ = self._ped_layout()
        W, H = e.size
        if self._ped_grid:
            gx = ox + 36 * s
            while gx < ox + W * s - 1:
                c.create_line(gx, oy, gx, oy + H * s, fill="#d7dee7", tags=("ped-ov",))
                gx += 36 * s
            gy = oy + 36 * s
            while gy < oy + H * s - 1:
                c.create_line(ox, gy, ox + W * s, gy, fill="#d7dee7", tags=("ped-ov",))
                gy += 36 * s
        # v15.13: cut-out erasures paint under everything, with no outline
        for (wx0, wy0, wx1, wy1) in getattr(e, "whiteouts", []):
            c.create_rectangle(ox + wx0 * s, oy + wy0 * s, ox + wx1 * s, oy + wy1 * s,
                               fill="#FFFFFF", outline="", tags=("ped-ov",))
        self._ped_item_cids = {}
        self._ped_draw_items(c, e, s, ox, oy, self._ped_item_cids)
        csel = getattr(self, "_ped_card_sel", None)
        if csel is not None and e.card is not None:
            try:
                lay = self._ped_card_layout(e)
                for slot in lay["slots"]:
                    if slot["slot"] == csel and slot["word"] is not None:
                        x0 = ox + slot["x_in"] * 72 * s
                        y0 = oy + slot["y_in"] * 72 * s
                        sd = slot["side_in"] * 72 * s
                        c.create_rectangle(x0, y0, x0 + sd, y0 + sd, outline=_pal().SUN,
                                           width=3, tags=("ped-ov",))
                    dg = self._ped_drag
                    if (dg and dg.get("mode") == "card" and slot["slot"] == dg.get("over")
                            and dg.get("over") != dg.get("slot")):
                        x0 = ox + slot["x_in"] * 72 * s
                        y0 = oy + slot["y_in"] * 72 * s
                        sd = slot["side_in"] * 72 * s
                        c.create_rectangle(x0 + 3, y0 + 3, x0 + sd - 3, y0 + sd - 3,
                                           outline=_pal().ACCENT, width=2, dash=(5, 3),
                                           tags=("ped-ov",))
            except Exception:
                pass
        accent = _pal().ACCENT
        gh = self._ped_ghost
        if gh is not None:
            if gh[0] == "free":
                cpts = []
                for qx, qy in gh[1]:
                    cpts.extend((ox + qx * s, oy + qy * s))
                if len(cpts) >= 4:
                    c.create_line(cpts, fill=accent, width=2, smooth=True, tags=("ped-ov",))
            elif gh[0] in ("line", "arrow"):
                c.create_line(ox + gh[1] * s, oy + gh[2] * s, ox + gh[3] * s, oy + gh[4] * s,
                              fill=accent, width=2, dash=(4, 3),
                              arrow="last" if gh[0] == "arrow" else "none", tags=("ped-ov",))
            elif gh[0] == "ellipse":
                c.create_oval(ox + gh[1] * s, oy + gh[2] * s, ox + gh[3] * s, oy + gh[4] * s,
                              outline=accent, width=2, dash=(4, 3), tags=("ped-ov",))
            else:
                c.create_rectangle(ox + gh[1] * s, oy + gh[2] * s, ox + gh[3] * s, oy + gh[4] * s,
                                   outline=accent, width=2, dash=(4, 3), tags=("ped-ov",))
        if self._ped_poly:
            cpts = []
            for qx, qy in self._ped_poly:
                cpts.extend((ox + qx * s, oy + qy * s))
            if len(cpts) >= 4:
                c.create_line(cpts, fill=accent, width=2, tags=("ped-ov",))
            for qx, qy in self._ped_poly:
                cxp, cyp = ox + qx * s, oy + qy * s
                c.create_oval(cxp - 3, cyp - 3, cxp + 3, cyp + 3, fill=accent, outline="",
                              tags=("ped-ov",))
        self._ped_draw_selection()
        # v15.53: the other page — background items only here (its
        # selection, handles, grid, ghost and card-slot marks draw when
        # it becomes the focused page); it is also a drop target for
        # drags from this page.  v15.65: in row mode every other page
        # in the row gets the same treatment.
        if self._ped_row_active():
            others = [(i, s2, ox2, oy2, pw2, ph2)
                      for (i, s2, ox2, oy2, pw2, ph2) in self._ped_row_layout()
                      if i != self._ped_cur]
        else:
            lay2 = self._ped_two_layout()
            others = [lay2] if lay2 is not None else []
        for (idx2, s2, ox2, oy2, pw2, ph2) in others:
            e2 = self._ped.pages[idx2]
            for (wx0, wy0, wx1, wy1) in getattr(e2, "whiteouts", []):
                c.create_rectangle(ox2 + wx0 * s2, oy2 + wy0 * s2, ox2 + wx1 * s2, oy2 + wy1 * s2,
                                   fill="#FFFFFF", outline="", tags=("ped-ov",))
            self._ped_draw_items(c, e2, s2, ox2, oy2, None)
            mv = getattr(self, "_ped_two_moved", None)
            if mv is not None and mv[0] == idx2:
                for it in mv[1]:
                    ax0, ay0, ax1, ay1 = item_aabb(it)
                    c.create_rectangle(ox2 + ax0 * s2, oy2 + ay0 * s2, ox2 + ax1 * s2, oy2 + ay1 * s2,
                                       outline=_pal().ACCENT, width=3, tags=("ped-ov",))
            hov = (idx2 == getattr(self, "_ped_row_hover", None)) \
                if self._ped_row_active() else getattr(self, "_ped_two_hover", False)
            if hov:
                c.create_rectangle(ox2, oy2, ox2 + pw2, oy2 + ph2,
                                   outline=_pal().ACCENT, width=3, tags=("ped-ov",))
        # v15.55: the focused page of the pair wears a purple border;
        # the other page keeps its plain sheet edge, so it is always
        # clear which page the tools are working on.  v15.65: same in
        # row mode.
        if self._ped_two_active() or self._ped_row_active():
            c.create_rectangle(ox, oy, ox + max(1, int(W * s)), oy + max(1, int(H * s)),
                               outline=self.PED_PURPLE, width=3, tags=("ped-ov",))

    # ------------------------------------------------- v15.9 selection layer
    PED_PURPLE = "#A98BD6"
    PED_PURPLE_D = "#8A6BC0"

    def _ped_selection(self):
        """The live selection: items of the current page that are selected
        (hidden items may be selected from the Objects list; locked ones too)."""
        e = self._ped_entry()
        if e is None:
            return []
        out = []
        for it in self._ped_selset:
            if it in e.items and it not in out:
                out.append(it)
        if self._ped_sel is not None and self._ped_sel in e.items and self._ped_sel not in out:
            out.append(self._ped_sel)
        return out

    def _ped_set_selection(self, items, primary=None):
        self._ped_two_moved = None
        e = self._ped_entry()
        keep = []
        if e is not None:
            for it in items or []:
                if it in e.items and it not in keep:
                    keep.append(it)
            keep = self._ped_expand_groups(keep)
        self._ped_selset = keep
        if primary is not None and primary in keep:
            self._ped_sel = primary
        elif keep:
            self._ped_sel = keep[-1]
        else:
            self._ped_sel = None
        self._ped_draw_selection()
        self._ped_sync_props()

    def _ped_expand_groups(self, items):
        e = self._ped_entry()
        if e is None:
            return list(items)
        gids = {it.gid for it in items if getattr(it, "gid", 0)}
        if not gids:
            return list(items)
        out = list(items)
        for it in e.items:
            if it.gid in gids and it not in out:
                out.append(it)
        return out

    def _ped_sel_bounds(self, items=None):
        """Union of the visible footprints (AABBs) of the given/selected items."""
        items = self._ped_selection() if items is None else items
        if not items:
            return None
        boxes = [item_aabb(it) for it in items]
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def _ped_handle_points(self):
        """Selection handles in PAGE points: {name: (x, y)} for the 8 resize
        handles around the selection's visible bounds, plus 'rot' — the
        rotation handle, which sits off the top edge in CANVAS pixels and is
        computed by _ped_rot_handle_xy().  For a single selected item the
        bounds are its rotated corners, so handles hug the turned object."""
        sel = self._ped_selection()
        if not sel:
            return {}
        if len(sel) == 1:
            it = sel[0]
            cs = item_corners(it)
            (x0, y0), (x1, y1) = cs[0], cs[2]
            mx = [(cs[0][0] + cs[1][0]) / 2, (cs[0][1] + cs[1][1]) / 2]
            my_r = [(cs[1][0] + cs[2][0]) / 2, (cs[1][1] + cs[2][1]) / 2]
            my_b = [(cs[2][0] + cs[3][0]) / 2, (cs[2][1] + cs[3][1]) / 2]
            my_l = [(cs[3][0] + cs[0][0]) / 2, (cs[3][1] + cs[0][1]) / 2]
            return {"nw": cs[0], "n": tuple(mx), "ne": cs[1], "e": tuple(my_r),
                    "se": cs[2], "s": tuple(my_b), "sw": cs[3], "w": tuple(my_l)}
        b = self._ped_sel_bounds(sel)
        x0, y0, x1, y1 = b
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        return {"nw": (x0, y0), "n": (mx, y0), "ne": (x1, y0), "e": (x1, my),
                "se": (x1, y1), "s": (mx, y1), "sw": (x0, y1), "w": (x0, my)}

    def _ped_rot_handle_xy(self):
        """Canvas position of the rotation handle: above the top-middle handle,
        along the selection's own 'up' direction."""
        import math
        sel = self._ped_selection()
        if not sel:
            return None
        hp = self._ped_handle_points()
        if "n" not in hp:
            return None
        s, ox, oy, _ = self._ped_layout()
        tx, ty = ox + hp["n"][0] * s, oy + hp["n"][1] * s
        b = self._ped_sel_bounds(sel)
        cx, cy = ox + (b[0] + b[2]) / 2 * s, oy + (b[1] + b[3]) / 2 * s
        if len(sel) == 1:
            ccx, ccy = item_center(sel[0])
            cx, cy = ox + ccx * s, oy + ccy * s
        vx, vy = tx - cx, ty - cy
        ln = math.hypot(vx, vy) or 1.0
        return (tx + vx / ln * 26, ty + vy / ln * 26, tx, ty)

    def _ped_draw_selection(self):
        """The selection layer (tag 'ped-sel'): outlines, handles, the rotation
        handle, snap guides and the marquee — redrawn on its own during drags
        so the page and the other objects don't have to be repainted."""
        c = self.canvas
        try:
            c.delete("ped-sel")
        except Exception:
            return
        e = self._ped_entry()
        if e is None or not self._ped_active:
            return
        s, ox, oy, _ = self._ped_layout()
        P = self.PED_PURPLE
        # v15.12: snapping should FEEL like snapping — thick guides with a
        # label saying what caught, so it is obvious the moment it engages.
        info = getattr(self, "_ped_snap_info", [])
        for gi, (orient, pos) in enumerate(self._ped_guides):
            lab = f"Snapped · {info[gi]}" if gi < len(info) else "Snapped"
            if orient == "v":
                x = ox + pos * s
                c.create_line(x, oy, x, oy + e.size[1] * s, fill=P, width=3, tags=("ped-sel",))
                c.create_text(x + 6, oy + 12, text=lab, anchor="w", fill=P,
                              font=("Helvetica", 10, "bold"), tags=("ped-sel",))
            else:
                y = oy + pos * s
                c.create_line(ox, y, ox + e.size[0] * s, y, fill=P, width=3, tags=("ped-sel",))
                c.create_text(ox + 8, y - 10, text=lab, anchor="w", fill=P,
                              font=("Helvetica", 10, "bold"), tags=("ped-sel",))
        if self._ped_marquee is not None:
            x0, y0, x1, y1 = self._ped_marquee
            c.create_rectangle(ox + x0 * s, oy + y0 * s, ox + x1 * s, oy + y1 * s,
                               outline=P, width=1, dash=(4, 3),
                               fill="#A98BD6", stipple="gray12", tags=("ped-sel",))
        if self._ped_editing is not None:
            # re-place the on-canvas editor BEFORE the no-selection return:
            # a card-slot edit has no item selection, and its editor window
            # still has to survive every redraw
            self._ped_edit_place()
        sel = self._ped_selection()
        if not sel:
            return
        for it in sel:
            cs = item_corners(it)
            cpts = []
            for qx, qy in cs:
                cpts.extend((ox + qx * s, oy + qy * s))
            kw = {"dash": (3, 3)} if it.hidden else {}
            c.create_polygon(cpts, outline="#B07CD8" if it.hidden else P, width=1,
                             fill="", tags=("ped-sel",), **kw)
        it = self._ped_sel
        if (len(sel) == 1 and it is not None and self._ped_crop and it.kind == "image"
                and it.crop and it in e.items):
            cl, ct2, cr, cb = it.crop
            fw = it.w / max(cr - cl, 1e-6)
            fh = it.h / max(cb - ct2, 1e-6)
            fx0, fy0 = it.x0 - cl * fw, it.y0 - ct2 * fh
            c.create_rectangle(ox + fx0 * s, oy + fy0 * s, ox + (fx0 + fw) * s,
                               oy + (fy0 + fh) * s, outline="#888888", width=1, dash=(3, 3),
                               tags=("ped-sel",))
        hp = self._ped_handle_points()
        for name, (hx, hy) in hp.items():
            cxp, cyp = ox + hx * s, oy + hy * s
            c.create_rectangle(cxp - 5, cyp - 5, cxp + 5, cyp + 5, fill="#FFFFFF",
                               outline=self.PED_PURPLE_D, width=2, tags=("ped-sel",))
        rh = self._ped_rot_handle_xy()
        if rh is not None:
            hx, hy, tx, ty = rh
            c.create_line(tx, ty, hx, hy, fill=self.PED_PURPLE_D, width=1, tags=("ped-sel",))
            c.create_oval(hx - 7, hy - 7, hx + 7, hy + 7, fill="#FFFFFF",
                          outline=self.PED_PURPLE_D, width=2, tags=("ped-sel",))
            c.create_text(hx, hy, text="⟳", fill=self.PED_PURPLE_D,
                          font=("Arial", 9, "bold"), tags=("ped-sel",))

    # ------------------------------------------------------------ mouse on the page (real events)
    def _ped_item_at(self, x, y):
        e = self._ped_entry()
        s, ox, oy, _ = self._ped_layout()
        px, py = (x - ox) / s, (y - oy) / s
        import math

        def segd(ax, ay, bx, by):
            vx, vy = bx - ax, by - ay
            den = vx * vx + vy * vy
            t = 0.0 if den == 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / den))
            return math.hypot(px - (ax + t * vx), py - (ay + t * vy))

        for it in reversed(e.items):
            if it.hidden or it.locked:
                continue               # v15.9: hidden/locked objects can't be grabbed
            if it.kind in ("line", "arrow", "freehand") and it.points:
                pts = it.points
                if any(segd(pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1]) <= 6
                       for k in range(len(pts) - 1)):
                    return it
                continue
            if item_contains(it, px, py):
                return it
        return None

    def _ped_handle_at(self, it, x, y):
        """Resize handle under the canvas point (x, y) for a single item —
        rotation-aware: the pointer is un-rotated into the item's own space."""
        s, ox, oy, _ = self._ped_layout()
        px, py = (x - ox) / s, (y - oy) / s
        cx, cy = item_center(it)
        qx, qy = _rot_xy(px, py, cx, cy, -it.rot)
        mx, my = (it.x0 + it.x1) / 2, (it.y0 + it.y1) / 2
        tol = 5 / max(s, 0.01)      # ~5 screen px: the body of a small item
                                    # (a one-line text label is ~11 px tall)
                                    # must stay grabbable between the handles
        # v15.12: on a thin item the edge handles' zones overlap and swallow
        # the body — at small zoom a press in the middle of a 20 pt strip
        # grabbed "north" and proportionally exploded it instead of moving
        # it.  So an edge-mid handle only exists when the item is thick
        # enough in that axis for its two zones not to meet; corners stay,
        # so a thin strip still resizes from its ends.
        # v15.22: 2x left only ~1 px of grabbable body on an 11 px tall
        # strip (the taller canvas raised the zoom, and a 1 px click error
        # grabbed the north handle and aspect-scaled a cut-out piece
        # instead of moving it).  Edge handles now need a full tolerance
        # of body left between them — 3x — so a thin strip moves when
        # you grab its middle and still resizes from its corners.
        thin_v = (it.y1 - it.y0) <= 3 * tol
        thin_h = (it.x1 - it.x0) <= 3 * tol
        for name, hx, hy in (("nw", it.x0, it.y0), ("n", mx, it.y0), ("ne", it.x1, it.y0),
                             ("e", it.x1, my), ("se", it.x1, it.y1), ("s", mx, it.y1),
                             ("sw", it.x0, it.y1), ("w", it.x0, my)):
            if name in ("n", "s") and thin_v:
                continue
            if name in ("e", "w") and thin_h:
                continue
            if abs(qx - hx) <= tol and abs(qy - hy) <= tol:
                return name
        return None

    def _ped_sel_handle_at(self, x, y):
        """Any selection handle under the canvas point: 'rot' for the rotation
        handle, else a resize-handle name, else None.  Works for a single
        (possibly rotated) item and for a multi-selection's union bounds."""
        sel = self._ped_selection()
        if not sel or all(x.locked for x in sel):
            return None                # a locked selection has no live handles
        rh = self._ped_rot_handle_xy()
        if rh is not None and abs(x - rh[0]) <= 10 and abs(y - rh[1]) <= 10:
            return "rot"
        if len(sel) == 1:
            return self._ped_handle_at(sel[0], x, y)
        s, ox, oy, _ = self._ped_layout()
        for name, (hx, hy) in self._ped_handle_points().items():
            if abs(x - (ox + hx * s)) <= 5 and abs(y - (oy + hy * s)) <= 5:
                return name
        return None

    def _ped_press_pt(self, event):
        s, ox, oy, _ = self._ped_layout()
        return (event.x - ox) / s, (event.y - oy) / s

    def _ped_on_click(self, event):
        e = self._ped_entry()
        if e is None:
            return
        self.canvas.focus_set()
        # v15.55: the other page is a full editing surface — pressing
        # on it focuses it (the pair stays put) and the press then runs
        # its normal course on the newly focused page, so an object
        # there can be picked up and dragged straight across.  Zoomed
        # pages overflow their halves and overlap, so a press on this
        # page's own object wins the tie — working on the focused page
        # never focus-switches by accident.
        # v15.63: with a DRAW tool (snip, harvest, shapes…) the press
        # belongs to the page you can see — the other page draws on
        # top, so a press on it must focus it even when a focused-page
        # object sits underneath that canvas point (the old item gate
        # sent the rectangle to the focused page, and the snip came
        # back showing the wrong page's content, shifted). Select/move
        # keeps the gate: grabbing your focused page's object through
        # an overlap must not yank the focus away.
        _draw_tools = ("polygon", "highlight", "underline", "strike", "rect",
                       "ellipse", "circle", "line", "arrow", "freehand", "measure",
                       "harvest_pic", "harvest_words", "snip")
        # v15.65: row pages never overlap, so a press on another page's
        # rect unambiguously means that page — focus it for every tool
        # (no item gate: the gate only existed for the pair's overlap).
        # No scroll here: the press must map onto the layout as clicked;
        # the row slides the page fully into view on release instead.
        if self._ped_row_active():
            _tgt = self._ped_row_page_at(event.x, event.y)
            if _tgt is not None and _tgt != self._ped_cur:
                self._ped_focus_page(_tgt)
                self._ped_row_focused_this_press = True
                e = self._ped_entry()
                if e is None:
                    return
        lay2 = self._ped_two_layout()
        if lay2 is not None:
            _i2, _s2, _ox2, _oy2, _pw2, _ph2 = lay2
            if _ox2 <= event.x <= _ox2 + _pw2 and _oy2 <= event.y <= _oy2 + _ph2:
                if self._ped_tool in _draw_tools or self._ped_item_at(event.x, event.y) is None:
                    self._ped_focus_page(_i2)
                    e = self._ped_entry()
                    if e is None:
                        return
        tool = self._ped_tool
        px, py = self._ped_press_pt(event)
        if tool == "polygon":
            if self._ped_poly is None:
                self._ped_poly = []
            self._ped_poly.append((px, py))
            self._ped_draw_overlay()
            return
        if tool in ("highlight", "underline", "strike", "rect", "ellipse", "circle",
                    "line", "arrow", "freehand", "measure", "harvest_pic", "harvest_words",
                    "snip"):
            self._ped_draw_state = {"tool": tool, "x0": px, "y0": py, "x1": px, "y1": py,
                                    "points": [(px, py)] if tool == "freehand" else None}
            return
        was_editing = self._ped_editing is not None
        if was_editing:
            self._ped_edit_commit()          # v15.9: clicking away finishes on-canvas editing
        if tool == "text":
            if was_editing:
                # the click that finished an edit is spent — it doesn't also
                # place another box or open another editor
                hit = self._ped_item_at(event.x, event.y)
                if hit is not None and hit.kind in ("text", "card"):
                    self._ped_set_selection([hit], primary=hit)
                    self._ped_edit_open_item(hit)
                return
            it = self._ped_item_at(event.x, event.y)
            if it is not None and it.kind in ("text", "card"):
                self._ped_set_selection([it], primary=it)
                self._ped_edit_open_item(it)
            elif it is None:
                font, bold, italic, align = self._ped_text_defaults()
                nit = new_text("", px, py, size=float(self._ped_size_var.get()),
                               color=self._ped_shape_line, font=font, bold=bold,
                               italic=italic, align=align)
                self._ped_put(nit)
                self._ped_edit_open_item(nit, fresh=True)
            return
        if tool == "pan" or self._ped_space:
            self._ped_drag = {"mode": "pan", "x": event.x, "y": event.y,
                              "lx": event.x, "ly": event.y, "pan0": list(self._ped_pan)}
            return
        if getattr(self, "_ped_place_word", None):
            word, self._ped_place_word = self._ped_place_word, None
            self._ped_place_card_at(word, px, py)
            return
        h = self._ped_sel_handle_at(event.x, event.y)
        if h == "rot":
            import math
            sel = self._ped_selection()
            b = self._ped_sel_bounds(sel)
            ccx, ccy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            if len(sel) == 1:
                ccx, ccy = item_center(sel[0])
            self._ped_drag = {"mode": "rotate", "moved": False, "cx": ccx, "cy": ccy,
                              "a0": math.atan2(py - ccy, px - ccx), "items": list(sel),
                              "origs": {x.uid: (x.rot, list(x.points) if x.points else None,
                                                (x.x0, x.y0, x.x1, x.y1)) for x in sel}}
            return
        if h:
            sel = self._ped_selection()
            if len(sel) == 1:
                it0 = sel[0]
                mode = "crop" if (self._ped_crop and it0.kind == "image") else "resize"
                self._ped_drag = {"mode": mode, "item": it0, "handle": h, "moved": False,
                                  "orig": (it0.x0, it0.y0, it0.x1, it0.y1, it0.size,
                                           list(it0.points) if it0.points else None),
                                  "crop0": tuple(it0.crop) if it0.crop else (0.0, 0.0, 1.0, 1.0)}
            else:
                self._ped_drag = {"mode": "resize_multi", "handle": h, "moved": False,
                                  "bounds": self._ped_sel_bounds(sel), "items": list(sel),
                                  "origs": {x.uid: (x.x0, x.y0, x.x1, x.y1, x.size,
                                                    list(x.points) if x.points else None)
                                            for x in sel}}
            return
        it = self._ped_item_at(event.x, event.y)
        shift = bool(event.state & 0x1)
        if it is not None:
            cur = self._ped_selection()
            was_in_cur = it in cur
            if shift:
                grp = self._ped_expand_groups([it])
                if it in cur:
                    self._ped_set_selection([x for x in cur if x not in grp])
                    return               # a shift-click toggle never starts a drag
                self._ped_set_selection(cur + [it], primary=it)
                cur = self._ped_selection()
            elif it not in cur:
                self._ped_set_selection([it], primary=it)
                cur = self._ped_selection()
            elif self._ped_sel is not it:
                self._ped_sel = it
                self._ped_sync_props()
            drag_items = [x for x in cur if not x.locked]
            if drag_items:
                self._ped_drag = {"mode": "move", "item": it, "items": drag_items,
                                  "x": event.x, "y": event.y, "lx": event.x, "ly": event.y,
                                  "moved": False,
                                  # v15.13: hold the pointer so the drag keeps
                                  # reporting while it crosses onto the page strip
                                  "grab": self._ped_grab_canvas(),
                                  # a plain click (no drag) on an object that
                                  # was ALREADY in a multi-selection collapses
                                  # the selection to just that object (a fresh
                                  # group selection, by contrast, stays whole)
                                  "collapse": it if (was_in_cur and len(cur) > 1
                                                     and not shift) else None,
                                  "origs": {x.uid: (x.x0, x.y0, x.x1, x.y1, x.size,
                                                    list(x.points) if x.points else None)
                                            for x in drag_items}}
                self._ped_draw_selection()
            return
        if e.card is not None:
            slot = self._ped_slot_at(e, px, py)
            if slot is not None and slot["word"] is not None:
                self._ped_card_sel = slot["slot"]
                self._ped_drag = {"mode": "card", "slot": slot["slot"], "x": event.x,
                                  "y": event.y, "moved": False, "over": slot["slot"]}
                self._ped_draw_overlay()
                self._ped_sync_card_sel()
                return
            if getattr(self, "_ped_card_sel", None) is not None:
                self._ped_card_sel = None
                self._ped_sync_card_sel()
        # v15.9: empty space starts a rubber-band (marquee) selection; a plain
        # click without dragging still just clears the selection, as before.
        self._ped_marquee = (px, py, px, py)
        self._ped_drag = {"mode": "marquee", "x": event.x, "y": event.y, "moved": False,
                          "shift": shift, "x0": px, "y0": py}

    def _ped_text_defaults(self):
        try:
            return (self._ped_font_var.get(), bool(self._ped_bold_var.get()),
                    bool(self._ped_italic_var.get()),
                    {"Left": "l", "Center": "c", "Right": "r"}.get(self._ped_align_var.get(), "l"))
        except Exception:
            return ("Helvetica", False, False, "l")

    def _ped_snap_delta(self, d, dx, dy, s):
        """v15.9 guide snapping for a move drag: nudge (dx, dy) so the dragged
        selection's edges/centre land on page edges, the page centre, the
        24 pt margin, or another object's edges/centre — within ~6 screen px.
        Falls back to the old 6 pt grid rounding when no guide is close.
        Records the guides to draw in self._ped_guides."""
        e = self._ped_entry()
        items = d["items"]
        primary = d["item"]
        o = d["origs"][primary.uid]
        W, H = e.size
        # the selection's original footprint, so multi-drags snap as one block
        boxes = []
        for x in items:
            oo = d["origs"][x.uid]
            boxes.append((oo[0], oo[1], oo[2], oo[3]))
        bx0 = min(b[0] for b in boxes)
        by0 = min(b[1] for b in boxes)
        bx1 = max(b[2] for b in boxes)
        by1 = max(b[3] for b in boxes)
        other = [item_aabb(x) for x in e.items if x not in items and not x.hidden]
        vlines = [(0.0, "Page edge"), (W / 2, "Page centre"), (W, "Page edge"),
                  (24.0, "Margin"), (W - 24.0, "Margin")]
        hlines = [(0.0, "Page edge"), (H / 2, "Page centre"), (H, "Page edge"),
                  (24.0, "Margin"), (H - 24.0, "Margin")]
        for b in other:
            vlines += [(b[0], "Object edge"), ((b[0] + b[2]) / 2, "Object centre"),
                       (b[2], "Object edge")]
            hlines += [(b[1], "Object edge"), ((b[1] + b[3]) / 2, "Object centre"),
                       (b[3], "Object edge")]
        tol = 6.0 / max(s, 0.05)
        best = None
        for cand in (bx0 + dx, (bx0 + bx1) / 2 + dx, bx1 + dx):
            for ln, lab in vlines:
                adj = ln - cand
                if abs(adj) <= tol and (best is None or abs(adj) < abs(best[0])):
                    best = (adj, ln, lab)
        guides = []
        info = []
        if best is not None:
            dx += best[0]
            guides.append(("v", best[1]))
            info.append(best[2])
        best = None
        for cand in (by0 + dy, (by0 + by1) / 2 + dy, by1 + dy):
            for ln, lab in hlines:
                adj = ln - cand
                if abs(adj) <= tol and (best is None or abs(adj) < abs(best[0])):
                    best = (adj, ln, lab)
        if best is not None:
            dy += best[0]
            guides.append(("h", best[1]))
            info.append(best[2])
        if not guides:
            nx = round((o[0] + dx) / 6) * 6.0
            ny = round((o[1] + dy) / 6) * 6.0
            dx, dy = dx + (nx - (o[0] + dx)), dy + (ny - (o[1] + dy))
        self._ped_guides = guides
        self._ped_snap_info = info       # v15.12: what snapped, for the on-canvas label
        return dx, dy

    def _ped_scale_selection(self, d, px, py):
        """v15.9: resize a multi-selection by scaling every object between the
        selection's original bounds and the dragged bounds."""
        bx0, by0, bx1, by1 = d["bounds"]
        hd = d["handle"]
        nx0, ny0, nx1, ny1 = bx0, by0, bx1, by1
        if "w" in hd:
            nx0 = px
        if "e" in hd:
            nx1 = px
        if "n" in hd:
            ny0 = py
        if "s" in hd:
            ny1 = py
        ow, oh = max(bx1 - bx0, 1e-6), max(by1 - by0, 1e-6)
        fx = max(0.05, abs(nx1 - nx0) / ow)
        fy = max(0.05, abs(ny1 - ny0) / oh)
        ax0 = bx1 if "w" in hd else bx0           # the fixed side, original frame
        ay0 = by1 if "n" in hd else by0
        anx = nx1 if "w" in hd else nx0           # ... and where it sits now
        any_ = ny1 if "n" in hd else ny0
        for x in d["items"]:
            o = d["origs"][x.uid]
            x.x0 = anx + (o[0] - ax0) * fx
            x.x1 = anx + (o[2] - ax0) * fx
            x.y0 = any_ + (o[1] - ay0) * fy
            x.y1 = any_ + (o[3] - ay0) * fy
            if x.x1 < x.x0:
                x.x0, x.x1 = x.x1, x.x0
            if x.y1 < x.y0:
                x.y0, x.y1 = x.y1, x.y0
            if o[5]:
                x.points = [(anx + (qx - ax0) * fx, any_ + (qy - ay0) * fy) for qx, qy in o[5]]
            if x.kind == "text":
                f = fy if ("n" in hd or "s" in hd) else fx
                x.size = max(4.0, min(400.0, o[4] * f))

    def _ped_on_drag(self, event):
        ds = self._ped_draw_state
        if ds is not None:
            px, py = self._ped_press_pt(event)
            ds["x1"], ds["y1"] = px, py
            tool = ds["tool"]
            if tool == "freehand":
                lx, ly = ds["points"][-1]
                if abs(px - lx) + abs(py - ly) > 2.0:
                    ds["points"].append((px, py))
                self._ped_ghost = ("free", list(ds["points"]))
            elif tool == "measure":
                dx, dy = abs(px - ds["x0"]), abs(py - ds["y0"])
                import math
                self._ped_measure_lbl.configure(
                    text=f"{dx / 72:.2f} × {dy / 72:.2f} in · {math.hypot(dx, dy) / 72:.2f} in")
                self._ped_ghost = ("line", ds["x0"], ds["y0"], px, py)
            elif tool in ("line", "arrow"):
                self._ped_ghost = (tool, ds["x0"], ds["y0"], px, py)
            elif tool in ("ellipse", "circle"):
                self._ped_ghost = ("ellipse", min(ds["x0"], px), min(ds["y0"], py),
                                   max(ds["x0"], px), max(ds["y0"], py))
            else:
                self._ped_ghost = ("rect", min(ds["x0"], px), min(ds["y0"], py),
                                   max(ds["x0"], px), max(ds["y0"], py))
            self._ped_draw_overlay()
            return
        d = self._ped_drag
        if not d:
            return
        s, ox, oy, _ = self._ped_layout()
        if d["mode"] == "card":
            e = self._ped_entry()
            slot = self._ped_slot_at(e, (event.x - ox) / s, (event.y - oy) / s)
            d["over"] = slot["slot"] if slot is not None else None
            d["lx"], d["ly"] = (event.x - ox) / s, (event.y - oy) / s   # drop point, page pt
            if abs(event.x - d["x"]) + abs(event.y - d["y"]) >= 3:
                d["moved"] = True
            self._ped_draw_overlay()
            return
        if d["mode"] == "pan":
            # v15.9 perf: slide the existing canvas items instead of repainting
            # the whole page on every mouse move; one full draw on release.
            dxp, dyp = event.x - d["lx"], event.y - d["ly"]
            d["lx"], d["ly"] = event.x, event.y
            if dxp or dyp:
                self.canvas.move("all", dxp, dyp)
            self._ped_pan = [d["pan0"][0] + event.x - d["x"], d["pan0"][1] + event.y - d["y"]]
            return
        if d["mode"] == "marquee":
            px, py = (event.x - ox) / s, (event.y - oy) / s
            if abs(event.x - d["x"]) + abs(event.y - d["y"]) >= 6:
                d["moved"] = True     # v15.10: a few px of click jitter is still a click
            self._ped_marquee = (min(d["x0"], px), min(d["y0"], py),
                                 max(d["x0"], px), max(d["y0"], py))
            self._ped_draw_selection()
            return
        if not d["moved"]:
            if d["mode"] in ("move", "rotate", "resize", "resize_multi") \
                    and "x" in d \
                    and abs(event.x - d["x"]) + abs(event.y - d["y"]) < 3:
                return
            self._ped.push_undo()
            d["moved"] = True
        e = self._ped_entry()
        W, H = e.size
        if d["mode"] == "move":
            dx, dy = (event.x - d["x"]) / s, (event.y - d["y"]) / s
            items = d["items"]
            primary = d["item"]
            o0 = d["origs"][primary.uid]
            pw_, ph_ = o0[2] - o0[0], o0[3] - o0[1]
            nx = max(-pw_ * 0.8, min(W - pw_ * 0.2, o0[0] + dx))   # keep part of it on the page
            ny = max(-ph_ * 0.8, min(H - ph_ * 0.2, o0[1] + dy))
            dx, dy = nx - o0[0], ny - o0[1]
            self._ped_guides = []
            if self._ped_snap:
                dx, dy = self._ped_snap_delta(d, dx, dy, s)
            # fast path: shift the dragged objects' canvas pictures by the
            # model delta since the last motion; the model stays
            # authoritative and release repaints.
            dxp = (dx - d.get("mdx", 0.0)) * s
            dyp = (dy - d.get("mdy", 0.0)) * s
            d["mdx"], d["mdy"] = dx, dy
            fast = all(x.uid in self._ped_item_cids for x in items)
            if fast and (dxp or dyp):
                for x in items:
                    for cid in self._ped_item_cids.get(x.uid, ()):  # noqa: B007
                        self.canvas.move(cid, dxp, dyp)
            for x in items:
                o = d["origs"][x.uid]
                x.x0, x.y0, x.x1, x.y1 = o[0] + dx, o[1] + dy, o[2] + dx, o[3] + dy
                if o[5]:
                    x.points = [(qx + dx, qy + dy) for qx, qy in o[5]]
            if fast:
                self._ped_draw_selection()
            else:
                self._ped_draw_overlay()
            self._ped_strip_track(event)   # v15.13: dragging onto a page thumbnail
            self._ped_two_track(event)
            return
        if d["mode"] == "rotate":
            import math
            px, py = (event.x - ox) / s, (event.y - oy) / s
            ang = math.degrees(math.atan2(py - d["cy"], px - d["cx"]) - d["a0"])
            snapped = round(ang / 15.0) * 15.0
            if snapped != d.get("applied"):
                d["applied"] = snapped
                for x in d["items"]:
                    orot, opts, orect = d["origs"][x.uid]
                    if opts is not None:
                        ocx, ocy = (orect[0] + orect[2]) / 2, (orect[1] + orect[3]) / 2
                        x.points = [_rot_xy(qx, qy, ocx, ocy, snapped) for qx, qy in opts]
                        xs = [q[0] for q in x.points]
                        ys = [q[1] for q in x.points]
                        x.x0, x.y0, x.x1, x.y1 = min(xs), min(ys), max(xs), max(ys)
                    else:
                        x.rot = int(round(orot + snapped)) % 360
                self._ped_draw_overlay()
            return
        if d["mode"] == "resize_multi":
            px, py = (event.x - ox) / s, (event.y - oy) / s
            self._ped_scale_selection(d, px, py)
            self._ped_draw_overlay()
            return
        it = d["item"]
        if d["mode"] == "crop":
            px, py = (event.x - ox) / s, (event.y - oy) / s
            ox0, oy0, ox1, oy1 = d["orig"][:4]
            cl, ct2, cr, cb = d["crop0"]
            fw = (ox1 - ox0) / max(cr - cl, 1e-6)
            fh = (oy1 - oy0) / max(cb - ct2, 1e-6)
            fx0, fy0 = ox0 - cl * fw, oy0 - ct2 * fh
            nl, nt, nr, nb = cl, ct2, cr, cb
            hd = d["handle"]
            if "w" in hd:
                nl = max(0.0, min(cr - 0.02, (px - fx0) / fw))
                it.x0 = fx0 + nl * fw
            if "e" in hd:
                nr = max(cl + 0.02, min(1.0, (px - fx0) / fw))
                it.x1 = fx0 + nr * fw
            if "n" in hd:
                nt = max(0.0, min(cb - 0.02, (py - fy0) / fh))
                it.y0 = fy0 + nt * fh
            if "s" in hd:
                nb = max(ct2 + 0.02, min(1.0, (py - fy0) / fh))
                it.y1 = fy0 + nb * fh
            it.crop = (nl, nt, nr, nb)
        else:
            px, py = (event.x - ox) / s, (event.y - oy) / s
            if it.rot:               # v15.9: resize in the item's own (unrotated) frame
                ocx = (d["orig"][0] + d["orig"][2]) / 2
                ocy = (d["orig"][1] + d["orig"][3]) / 2
                px, py = _rot_xy(px, py, ocx, ocy, -it.rot)
            resize_item(it, d["orig"], d["handle"], px, py)
            if it.kind == "text":
                self._ped_size_var.set(it.size)
                self._ped_size_val.configure(text=f"{round(it.size)} pt")
        self._ped_draw_overlay()

    def _ped_on_release(self, event):
        self._ped_two_hover = False
        self._ped_row_hover = None
        ds, self._ped_draw_state = self._ped_draw_state, None
        if ds is not None:
            self._ped_ghost = None
            _e0 = self._ped_entry()
            _n0 = len(_e0.items) if _e0 is not None else 0
            self._ped_finish_draw(ds)
            self._ped_draw_overlay()
            # v15.10: draw tools are one-shot — once the shape is on the page
            # the Select tool comes back, so the next click selects/deselects
            # instead of starting another shape (Cody's workflow, and how the
            # app behaved before 15.9).
            _e1 = self._ped_entry()
            if ds.get("tool") in ("rect", "ellipse", "circle", "line", "arrow",
                                  "freehand", "highlight", "underline", "strike") \
                    and _e1 is not None and len(_e1.items) > _n0:
                self._ped_set_tool("select")
            self._ped_row_release_scroll()
            return
        d, self._ped_drag = self._ped_drag, None
        if d and d.get("grab"):
            try:
                self.canvas.grab_release()
            except Exception:
                pass
        if d and d.get("mode") == "card":
            self._ped_finish_card_drag(d)
            self._ped_draw_overlay()
            self._ped_row_release_scroll()
            return
        if d and d.get("mode") == "pan":
            self._ped_draw()               # one clean repaint at the end of a pan
            return
        if d and d.get("mode") == "marquee":
            mq, self._ped_marquee = self._ped_marquee, None
            self._ped_guides = []
            if d.get("moved") and mq is not None and (mq[2] - mq[0] > 3 or mq[3] - mq[1] > 3):
                e = self._ped_entry()
                hits = []
                if e is not None:
                    for it in e.items:
                        if it.hidden or it.locked:
                            continue
                        b = item_aabb(it)
                        if b[0] <= mq[2] and b[2] >= mq[0] and b[1] <= mq[3] and b[3] >= mq[1]:
                            hits.append(it)
                base = self._ped_selection() if d.get("shift") else []
                self._ped_set_selection(base + hits)
            elif not d.get("shift"):
                self._ped_set_selection([])          # a plain click on empty space
                if getattr(self, "_ped_card_sel", None) is not None:
                    self._ped_card_sel = None
                    self._ped_sync_card_sel()
            self._ped_draw_overlay()
            self._ped_row_release_scroll()
            return
        if d and d.get("mode") == "move" and not d.get("moved") and d.get("collapse") is not None:
            self._ped_set_selection([d["collapse"]], primary=d["collapse"])
            self._ped_draw_overlay()
            self._ped_row_release_scroll()
            return
        if d and d.get("moved") and self._ped is not None:
            # v15.13: dropped on a page thumbnail — the whole selection moves
            # to that page (same undo step as the drag itself), and the view
            # follows it there.  The release point is the drop point; the
            # tracked value covers a release with no final motion event.
            tgt = None
            if d.get("mode") == "move":
                tgt = self._ped_strip_target_page(event)
                if tgt is None:
                    tgt = getattr(self, "_ped_strip_target", None)
            self._ped_strip_target = None
            if tgt is not None and tgt != self._ped_cur:
                self._ped_strip_drop(d["items"], tgt)
                return
            # v15.53: dropped on the second page — same move, no view change.
            lay2 = self._ped_two_layout()
            if d.get("mode") == "move" and lay2 is not None:
                _, _, _ox2, _oy2, _pw2, _ph2 = lay2
                if _ox2 <= event.x <= _ox2 + _pw2 and _oy2 <= event.y <= _oy2 + _ph2 \
                        and not self._ped_focused_rect_contains(event.x, event.y):
                    self._ped_two_drop(d["items"], lay2[0])
                    return
            # v15.65: dropped on another row page — same move, no view change.
            if d.get("mode") == "move" and self._ped_row_active():
                _tgt = self._ped_row_page_at(event.x, event.y)
                if _tgt is not None and _tgt != self._ped_cur:
                    self._ped_two_drop(d["items"], _tgt)
                    return
            self._ped.dirty = True
            self._ped_guides = []
            self._ped_draw_overlay()
            self._ped_props_refresh()
            self._ped_row_release_scroll()
        # v15.65: a plain click that changed the row focus slides the
        # newly focused page fully into view now that no press is in flight.
        self._ped_row_release_scroll()

    def _ped_draw_fill(self):
        """The fill for a NEWLY drawn shape: the chosen colour, or None when
        'No fill (outline only)' is ticked.  (Before 15.10 the tick was only
        applied to the selected shape — drawn shapes always came out filled.)"""
        try:
            if self._ped_nofill_var.get():
                return None
        except Exception:
            pass
        return self._ped_shape_fill

    def _ped_finish_draw(self, ds):
        if self._ped_entry() is None:
            return
        tool = ds["tool"]
        if tool == "measure":
            return
        if tool == "freehand":
            pts = ds["points"] or []
            if len(pts) >= 2:
                self._ped_put(new_freehand(pts, color=self._ped_shape_line,
                                           lw=max(1.5, self._ped_shape_lw)))
            return
        x0, y0, x1, y1 = ds["x0"], ds["y0"], ds["x1"], ds["y1"]
        nx0, nx1 = sorted((x0, x1))
        ny0, ny1 = sorted((y0, y1))
        if tool in ("harvest_pic", "harvest_words"):
            if nx1 - nx0 < 8 or ny1 - ny0 < 8:
                self.flash_status("Drag a bigger area to harvest", ok=False)
                return
            self._ped_harvest_finish(tool, (nx0, ny0, nx1, ny1))
            return
        if tool == "snip":
            if nx1 - nx0 < 8 or ny1 - ny0 < 8:
                self.flash_status("Drag a bigger area to snip", ok=False)
                return
            self._ped_snip_finish((nx0, ny0, nx1, ny1))
            return
        if nx1 - nx0 < 4 and ny1 - ny0 < 4:
            return
        G = _pal()
        if tool == "highlight":
            it = Item("rect", nx0, ny0, nx1, ny1, color=G.SUN, opacity=0.4)
        elif tool == "underline":
            it = Item("rect", nx0, ny1 - 2.4, nx1, ny1, color="#111111", opacity=1.0)
        elif tool == "strike":
            my = (ny0 + ny1) / 2
            it = Item("rect", nx0, my - 1.2, nx1, my + 1.2, color="#111111", opacity=1.0)
        elif tool == "rect":
            it = new_shape("rect", nx0, ny0, nx1, ny1, color=self._ped_shape_line,
                           fill=self._ped_draw_fill(), lw=self._ped_shape_lw)
        elif tool in ("ellipse", "circle"):
            if tool == "circle":
                side = max(nx1 - nx0, ny1 - ny0)
                nx1, ny1 = nx0 + side, ny0 + side
            it = new_shape("ellipse", nx0, ny0, nx1, ny1, color=self._ped_shape_line,
                           fill=self._ped_draw_fill(), lw=self._ped_shape_lw)
        elif tool in ("line", "arrow"):
            it = new_shape(tool, x0, y0, x1, y1, color=self._ped_shape_line,
                           fill=None, lw=self._ped_shape_lw)
        else:
            return
        self._ped_put(it)

    def _ped_on_dblclick(self, event):
        if self._ped_tool == "polygon":
            self._ped_finish_polygon()
            return
        e = self._ped_entry()
        it = self._ped_item_at(event.x, event.y) if e else None
        if it is None:
            # v15.7: double-clicking a card on a card page renames its word —
            # v15.9: with the on-canvas editor, like every other text edit.
            if e is not None and e.card is not None:
                px, py = self._ped_press_pt(event)
                slot = self._ped_slot_at(e, px, py)
                if slot is not None and slot["word"]:
                    self._ped_card_sel = slot["slot"]
                    self._ped_sync_card_sel()
                    self._ped_draw_overlay()
                    self._ped_edit_open_slot(e, slot)
            return
        if it.kind in ("text", "card"):
            self._ped_drag = None
            self._ped_set_selection([it], primary=it)
            self._ped_edit_open_item(it)
            return
        self._ped_sel = it
        self._ped_draw_overlay()
        self._ped_sync_props()

    # ------------------------------------------------------------ v15.10: hover tooltips
    def _ped_tip(self, w, text):
        """Attach a hover tooltip to a widget (and the children it has now —
        CTk buttons are composites, so the tip has to live on the parts the
        mouse actually touches)."""
        if w is None or not text:
            return
        if getattr(self, "_ped_tips", None) is None:
            self._ped_tips = {}
        self._ped_tips[w] = text
        targets = [w]
        try:
            stack = list(w.winfo_children())
            while stack:
                ch = stack.pop()
                targets.append(ch)
                stack.extend(ch.winfo_children())
        except Exception:
            pass
        for t in targets:
            try:
                t.bind("<Enter>", lambda e, txt=text: self._ped_tip_arm(txt), add="+")
                t.bind("<Leave>", lambda e: self._ped_tip_hide(), add="+")
            except Exception:
                pass

    def _ped_tip_arm(self, text):
        self._ped_tip_hide()
        try:
            self._ped_tip_job = self.after(450, lambda: self._ped_tip_show(text))
        except Exception:
            pass

    def _ped_tip_show(self, text):
        self._ped_tip_job = None
        try:
            x, y = self.winfo_pointerxy()
            tip = tk.Toplevel(self)
            tip.wm_overrideredirect(True)
            tip.wm_geometry(f"+{x + 14}+{y + 18}")
            tk.Label(tip, text=text, bg="#17171c", fg="#f0f0f4", bd=0,
                     font=("Segoe UI", 9), padx=8, pady=4, wraplength=300,
                     justify="left").pack()
            self._ped_tip_win = tip
        except Exception:
            pass

    def _ped_tip_hide(self):
        job = getattr(self, "_ped_tip_job", None)
        if job is not None:
            self._ped_tip_job = None
            try:
                self.after_cancel(job)
            except Exception:
                pass
        win = getattr(self, "_ped_tip_win", None)
        if win is not None:
            self._ped_tip_win = None
            try:
                win.destroy()
            except Exception:
                pass

    # ------------------------------------------------------------ v15.10: right-click menu
    def _ped_menu_style(self, m):
        try:
            m.configure(bg="#26262e", fg="#f0f0f4", activebackground=self.PED_PURPLE,
                        activeforeground="white", bd=0, font=("Segoe UI", 10))
        except Exception:
            pass
        return m

    def _ped_on_rclick_press(self, event):
        """Right-click press: an object (or a selection) gets its menu at once;
        empty space with nothing selected arms a right-drag pan instead —
        drag to pan, release without dragging for the page menu."""
        if self._ped_editing is not None:
            self._ped_edit_commit()
        e = self._ped_entry()
        if e is None:
            return
        if getattr(event, "num", 3) == 3 and self._ped_item_at(event.x, event.y) is None \
                and not self._ped_selection():
            self._ped_rclick_pan = {"x": event.x, "y": event.y,
                                    "pan": list(self._ped_pan), "moved": False}
            return
        self._ped_show_rclick_menu(event)

    def _ped_on_rclick_motion(self, event):
        """Right-drag pan (armed by _ped_on_rclick_press on empty space)."""
        pend = getattr(self, "_ped_rclick_pan", None)
        if pend is None:
            return
        dx, dy = event.x - pend["x"], event.y - pend["y"]
        if not pend["moved"] and abs(dx) + abs(dy) > 4:
            pend["moved"] = True
        if pend["moved"]:
            self._ped_pan = [pend["pan"][0] + dx, pend["pan"][1] + dy]
            self._ped_draw()

    def _ped_on_rclick_release(self, event):
        """End of a right-drag: a plain right-click (no drag) still gets the
        page menu; a real pan ends quietly."""
        pend = getattr(self, "_ped_rclick_pan", None)
        self._ped_rclick_pan = None
        if pend is not None and not pend["moved"]:
            self._ped_show_rclick_menu(event)

    def _ped_show_rclick_menu(self, event):
        """Right-click on the page: a context menu for the object under the
        cursor (or for a card tile, or for the page itself)."""
        e = self._ped_entry()
        if e is None:
            return
        px, py = self._ped_press_pt(event)
        it = self._ped_item_at(event.x, event.y)
        m = self._ped_menu_style(tk.Menu(self, tearoff=0))
        if it is not None:
            if it not in self._ped_selection():
                grp = self._ped_expand_groups([it])
                self._ped_set_selection(grp, primary=it)
            sel = self._ped_selection()
            if it.kind in ("text", "card"):
                m.add_command(label="Edit text",
                              command=lambda it=it: self._ped_edit_open_item(it))
                m.add_separator()
            # ---- colour: the shape fill / line options from the panel,
            # as quick submenus (swatches + custom + No fill where it fits)
            if it.kind in ("rect", "ellipse", "freehand"):
                self._ped_menu_color_sub(m, "Fill", "fill", it.fill)
                self._ped_menu_color_sub(m, "Line colour", "line", it.color)
            elif it.kind in ("line", "arrow"):
                self._ped_menu_color_sub(m, "Line colour", "line", it.color)
            elif it.kind == "text":
                self._ped_menu_color_sub(m, "Text colour", "text", it.color)
            elif it.kind == "card":
                sub = self._ped_menu_style(tk.Menu(m, tearoff=0))
                sub.add_command(label="Choose colour…",
                                command=lambda: self._ped_menu_pick_color("fill", it.color))
                sub.add_command(label="Category colour",
                                command=lambda: self._ped_menu_set_color("category", None))
                sub.add_separator()
                for name, hexv in self._PED_SWATCHES:
                    sub.add_command(label=f"{name}  {hexv}",
                                    command=lambda h=hexv: self._ped_menu_set_color("fill", h))
                m.add_cascade(label="Card colour", menu=sub)
                if it.img is not None:
                    m.add_command(label="Save picture to Library",
                                  command=lambda it=it: self._ped_save_item_picture(it))
            elif it.kind == "image":
                m.add_command(label="Replace picture…", command=self._ped_replace_image)
                m.add_command(label="Save picture to Library",
                              command=lambda it=it: self._ped_save_item_picture(it))
            m.add_separator()
            # ---- arrange
            arr = self._ped_menu_style(tk.Menu(m, tearoff=0))
            arr.add_command(label="Bring forward", command=lambda: self._ped_layer(True))
            arr.add_command(label="Send backward", command=lambda: self._ped_layer(False))
            arr.add_command(label="Bring to front",
                            command=lambda: self._ped_menu_layer_edge(True))
            arr.add_command(label="Send to back",
                            command=lambda: self._ped_menu_layer_edge(False))
            m.add_cascade(label="Arrange", menu=arr)
            m.add_command(label="Rotate 90°", command=self._ped_rotate_item)
            m.add_separator()
            if len(sel) > 1:
                m.add_command(label="Group", command=self._ped_group)
                m.add_command(label="Combine into one picture",
                              command=self._ped_combine)
            if it.gid:
                m.add_command(label="Ungroup", command=self._ped_ungroup)
            m.add_command(label="Unlock" if it.locked else "Lock",
                          command=lambda it=it: self._ped_menu_lock(it))
            m.add_command(label="Hide", command=self._ped_menu_hide)
            m.add_separator()
            m.add_command(label="Duplicate", command=self._ped_duplicate_item)
            m.add_command(label="Copy", command=self._ped_copy)
            m.add_command(label="Cut", command=self._ped_menu_cut)
            m.add_command(label="Delete", command=self._ped_delete_item)
        elif e.card is not None and (slot := self._ped_slot_at(e, px, py)) is not None \
                and slot["word"]:
            self._ped_card_sel = slot["slot"]
            self._ped_sync_card_sel()
            self._ped_draw_overlay()
            m.add_command(label=f"Rename “{slot['word']}”…",
                          command=lambda s=slot: self._ped_edit_open_slot(e, s))
            m.add_command(label="Delete word", command=self._ped_card_delete)
            m.add_command(label="Move word to page…", command=self._ped_card_send_menu)
            m.add_separator()
            m.add_command(label="Drag it off the grid to place it freely",
                          state="disabled")
        else:
            if self._ped_clip:
                m.add_command(label="Paste", command=self._ped_paste)
            m.add_command(label="Select all", command=lambda: self._ped_key(event, "select_all"))
            m.add_command(label="Snap: on" if self._ped_snap else "Snap: off",
                          command=self._ped_toggle_snap)
            if e.card is not None:
                self._ped_menu_card_page(m)
            m.add_separator()
            m.add_command(label="Add blank page — Letter",
                          command=lambda: self._ped_blank_pick("Letter"))
            m.add_command(label="Add blank page — A4",
                          command=lambda: self._ped_blank_pick("A4"))
            m.add_command(label="Add card page", command=self._ped_add_card_page)
            if e.src >= 0:
                m.add_command(label="Place this page as a picture",
                              command=lambda: self._ped_place_page_as_picture_go(e))
            m.add_separator()
            m.add_command(label="Fit page", command=self._ped_fit)
            if self._ped_selection():
                m.add_command(label="Zoom to selection", command=self._ped_zoom_selection)
        self._ped_ctx_menu = m
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                m.grab_release()
            except Exception:
                pass

    def _ped_menu_card_page(self, m):
        """v15.13: the card-page options from the Edit tab, on the right-click
        menu — layout, paper, and the three toggles (the section sync keeps
        the shared vars on this page's spec, so the marks are true)."""
        sub = self._ped_menu_style(tk.Menu(m, tearoff=0))
        lay = self._ped_menu_style(tk.Menu(sub, tearoff=0))
        for name in list(_pal().aac_boards.PECS_LAYOUTS.keys()):
            lay.add_radiobutton(label=name, variable=self.pdf_layout_var, value=name,
                                command=self._ped_card_opt_now)
        sub.add_cascade(label="Cards per page", menu=lay)
        pap = self._ped_menu_style(tk.Menu(sub, tearoff=0))
        for name in ("letter", "A4"):
            pap.add_radiobutton(label=name, variable=self.pdf_paper, value=name,
                                command=self._ped_card_opt_now)
        sub.add_cascade(label="Paper", menu=pap)
        sub.add_separator()
        sub.add_checkbutton(label="Show pictures", variable=self.pdf_show_pics,
                            command=self._ped_card_opt_now)
        sub.add_checkbutton(label="Show words", variable=self.pdf_show_words,
                            command=self._ped_card_opt_now)
        sub.add_checkbutton(label="Category-colour backgrounds", variable=self.pdf_colors_var,
                            command=self._ped_card_opt_now)
        m.add_cascade(label="Card page", menu=sub)

    def _ped_menu_lock(self, it):
        e = self._ped_entry()
        if e is None or it not in e.items:
            return
        self._ped.push_undo()
        it.locked = not it.locked
        self._ped.dirty = True
        self._ped_objs_sig = None
        self._ped_draw_selection()
        self._ped_objs_refresh()
        self._ped_props_refresh(force=True)

    def _ped_menu_hide(self):
        sel = self._ped_selection()
        if not sel:
            return
        self._ped.push_undo()
        for it in sel:
            it.hidden = True
        self._ped.dirty = True
        self._ped_set_selection([])
        self._ped_objs_sig = None
        self._ped_draw_overlay()
        self._ped_objs_refresh()

    # ------------------------------------------------------------ v15.12 right-click colour + arrange
    _PED_SWATCHES = (("Butter", "#F5D67A"), ("Blush", "#E88CA0"), ("Mint", "#6ECFAE"),
                     ("Lilac", "#A98BD6"), ("White", "#FFFFFF"), ("Soft black", "#2C2F3A"))

    def _ped_menu_color_sub(self, parent, label, mode, initial):
        """A colour submenu: quick swatches, a custom colour, and (for fills)
        No fill.  Applies to the primary selection, like the panel buttons."""
        sub = self._ped_menu_style(tk.Menu(parent, tearoff=0))
        sub.add_command(label="Choose colour…",
                        command=lambda: self._ped_menu_pick_color(mode, initial))
        if mode == "fill":
            sub.add_command(label="No fill", command=lambda: self._ped_menu_set_color(mode, None))
            sub.add_separator()
        for name, hexv in self._PED_SWATCHES:
            sub.add_command(label=f"{name}  {hexv}",
                            command=lambda h=hexv: self._ped_menu_set_color(mode, h))
        parent.add_cascade(label=label, menu=sub)

    def _ped_menu_pick_color(self, mode, initial):
        _title, hexv = colorchooser.askcolor(color=initial or "#FFFFFF",
                                             title="Colour")
        if hexv:
            self._ped_menu_set_color(mode, hexv)

    def _ped_menu_set_color(self, mode, hexv):
        it = self._ped_sel
        e = self._ped_entry()
        if it is None or e is None or it not in e.items:
            return
        self._ped.push_undo()
        if mode == "fill":
            if it.kind == "card":
                it.color = hexv or "#FFFFFF"
            else:
                it.fill = hexv
                if it.kind == "rect":
                    it.border = True
            self._ped_shape_fill = hexv or self._ped_shape_fill
        elif mode == "line":
            it.color = hexv
            self._ped_shape_line = hexv
        elif mode == "text":
            it.color = hexv
        elif mode == "category":
            it.color = _pal().aac_boards.category_color(it.word) if it.word else "#FFFFFF"
        self._ped.dirty = True
        self._ped_draw_overlay()
        self._ped_sync_props()

    def _ped_menu_layer_edge(self, to_front):
        """Move the whole selection to the very front (or back), keeping
        its internal order — the one-step version of Forward / Backward."""
        e = self._ped_entry()
        sel = self._ped_selection()
        if e is None or not sel:
            return
        self._ped.push_undo()
        rest = [it for it in e.items if it not in sel]
        e.items = (rest + sel) if to_front else (sel + rest)
        self._ped.dirty = True
        self._ped_draw_overlay()
        self._ped_objs_refresh()

    def _ped_menu_cut(self):
        self._ped_copy()
        self._ped_delete_item()

    # ------------------------------------------------- v15.9 on-canvas text editing
    def _ped_edit_open_item(self, it, fresh=False):
        """Edit a text label (or a picture card's word) right on the page."""
        e = self._ped_entry()
        if e is None or it not in e.items:
            return
        self._ped_edit_close(commit=True)
        self._ped_drag = None
        single = it.kind == "card"
        self._ped_editing = {"kind": "item", "item": it, "entry": e, "single": single,
                             "fresh": fresh, "orig": it.word if single else it.text,
                             "pushed": bool(fresh), "widget": None}
        self._ped_edit_build(it.word if single else it.text, it)
        self._ped_draw_selection()

    def _ped_edit_open_slot(self, e, slot):
        """Edit a card-page slot's word right on the page."""
        lay = self._ped_card_layout(e)
        sl = next((x for x in lay["slots"] if x["slot"] == slot["slot"]), None)
        if sl is None:
            return
        self._ped_edit_close(commit=True)
        self._ped_drag = None
        rect = (sl["x_in"] * 72, sl["y_in"] * 72,
                (sl["x_in"] + sl["side_in"]) * 72, (sl["y_in"] + sl["side_in"]) * 72)
        self._ped_editing = {"kind": "slot", "entry": e, "slot": slot["slot"],
                             "single": True, "fresh": False, "orig": slot["word"],
                             "rect": rect, "pushed": False, "widget": None}
        self._ped_edit_build(slot["word"], None, rect_pt=rect, size_pt=15.0)
        self._ped_draw_selection()

    def _ped_edit_build(self, text, it, rect_pt=None, size_pt=None):
        st = self._ped_editing
        if st is None:
            return
        s, ox, oy, _ = self._ped_layout()
        fam = {"Helvetica": "Helvetica", "Times": "Times", "Courier": "Courier"}.get(
            it.font if it is not None else "Helvetica", "Helvetica")
        size = size_pt if size_pt is not None else (it.size if it is not None else 14.0)
        px = max(9, int(round(size * max(s, 0.3) * 1.3)))
        fnt = (fam, -px, "bold") if (it is not None and it.bold) else (fam, -px)
        if it is not None and it.italic:
            fnt = (fam, -px, "bold italic") if it.bold else (fam, -px, "italic")
        w = tk.Text(self.canvas, font=fnt, wrap="char", undo=False, bd=0,
                    relief="flat", highlightthickness=2,
                    highlightbackground=self.PED_PURPLE, highlightcolor=self.PED_PURPLE_D,
                    background="#ffffff",
                    foreground=(it.color if it is not None else "#111111"),
                    insertbackground="#111111")
        w.insert("1.0", text or "")
        st["widget"] = w
        self._ped_edit_place()
        try:
            w.focus_set()
            if st["single"]:
                w.tag_add("sel", "1.0", "end-1c")
                w.mark_set("insert", "end-1c")
            else:
                w.mark_set("insert", "end-1c")
        except Exception:
            pass
        w.bind("<Escape>", lambda ev: (self._ped_edit_close(commit=False), "break")[1])
        w.bind("<Control-Return>", lambda ev: (self._ped_edit_close(commit=True), "break")[1])
        w.bind("<FocusOut>", lambda ev: self._ped_edit_close(commit=True))
        if st["single"]:
            w.bind("<Return>", lambda ev: (self._ped_edit_close(commit=True), "break")[1])

    def _ped_edit_place(self):
        """(Re)position the editor window on the canvas — called after draws,
        because a full draw deletes and recreates the canvas window item."""
        st = self._ped_editing
        if st is None or st.get("widget") is None:
            return
        c = self.canvas
        try:
            c.delete("ped-edit")
        except Exception:
            return
        s, ox, oy, _ = self._ped_layout()
        if st["kind"] == "slot":
            x0, y0, x1, y1 = st["rect"]
        else:
            it = st["item"]
            ax0, ay0, _, _ = item_aabb(it)
            x0, y0 = ax0, ay0
            x1, y1 = x0 + it.w, y0 + it.h
        wpx = max(70, int((x1 - x0) * s))
        hpx = max(26, int((y1 - y0) * s))
        try:
            c.create_window(ox + x0 * s, oy + y0 * s, window=st["widget"], anchor="nw",
                            width=wpx, height=hpx, tags=("ped-edit",))
            c.tag_raise("ped-edit")
        except Exception:
            pass

    def _ped_edit_commit(self):
        self._ped_edit_close(commit=True)

    def _ped_edit_close(self, commit=True):
        st = self._ped_editing
        if st is None:
            return
        self._ped_editing = None
        w = st.get("widget")
        val = None
        if w is not None:
            try:
                val = w.get("1.0", "end-1c")
            except Exception:
                val = None
            try:
                self.canvas.delete("ped-edit")
            except Exception:
                pass
            try:
                w.destroy()
            except Exception:
                pass
        if commit and val is not None:
            self._ped_edit_apply(st, val)
        elif not commit and st.get("fresh") and st.get("item") is not None:
            e, it = st["entry"], st["item"]
            if it in e.items:
                e.items.remove(it)
            self._ped_sel, self._ped_selset = None, []
        self._ped_draw_overlay()
        self._ped_sync_props()
        try:
            self.canvas.focus_set()
        except Exception:
            pass
        if commit and self._ped_tool == "text":
            # the text tool is one-shot, as in earlier versions: once a label
            # is placed and committed, the next click selects/moves again
            self._ped_set_tool("select")

    def _ped_edit_apply(self, st, val):
        if st["kind"] == "slot":
            word = (val or "").strip().split("\n")[0].strip()
            e = st["entry"]
            if word and word != st["orig"] and e.card is not None:
                self._ped.push_undo()
                # v15.13: renaming a tile's text pins the picture it had
                # (or its absence) to the slot, so the picture never changes
                # or vanishes on a rename.  A slot pinned before keeps its pin.
                pins = dict((e.card.get("slot_pics") or {}))
                if str(st["slot"]) not in pins:
                    try:
                        old_path = _pal().aac_boards._board_icon_path(
                            st["orig"], overrides=dict(self.overrides),
                            variants=dict(self.variants), search_as=dict(self.search_as),
                            local_only=True)
                        pins[str(st["slot"])] = str(old_path) if old_path else ""
                    except Exception:
                        pass
                words = list(e.card["words"])
                words[st["slot"]] = word
                e.card = dict(e.card, words=words, slot_pics=pins)
                self._ped.dirty = True
                self._ped_refresh_card_page(e)
                self._ped_strip_draw()
                self._ped_sync_card_sel()
            return
        it = st["item"]
        if st["single"]:                       # a picture card's word
            word = (val or "").strip().split("\n")[0].strip()
            if word and word != st["orig"]:
                if not st["pushed"]:
                    self._ped.push_undo()
                # v15.13: editing the text only replaces the text — the
                # picture stays exactly as it was (no refetch).
                it.word = word
                self._ped.dirty = True
            return
        new = (val or "").rstrip("\n")
        if not new.strip():
            if st["fresh"]:                    # an empty brand-new label just goes away
                e = st["entry"]
                if it in e.items:
                    e.items.remove(it)
                self._ped_sel, self._ped_selset = None, []
            return
        if new != st["orig"]:
            if not st["pushed"]:
                self._ped.push_undo()
            it.text = new
            refit_text(it)
            self._ped.dirty = True

    def _ped_on_wheel(self, event):
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            f = 1.15
        elif getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0:
            f = 1 / 1.15
        else:
            return
        if self._ped_entry() is None:
            return
        # v15.9 perf: a wheel flick fires dozens of events; coalesce them into
        # one zoom + repaint per frame instead of a full redraw per tick.
        pend = getattr(self, "_ped_wheel_pend", None)
        if pend is None:
            pend = self._ped_wheel_pend = {"z": self._ped_zoom, "focus": (event.x, event.y)}
        pend["z"] *= f
        pend["focus"] = (event.x, event.y)
        if self._ped_wheel_job is None:
            try:
                self._ped_wheel_job = self.after(24, self._ped_wheel_apply)
            except Exception:
                self._ped_wheel_apply()

    def _ped_wheel_apply(self):
        self._ped_wheel_job = None
        pend = getattr(self, "_ped_wheel_pend", None)
        self._ped_wheel_pend = None
        if pend is not None and self._ped_entry() is not None:
            self._ped_set_zoom(pend["z"], focus=pend["focus"])

    def _ped_key(self, event, what):
        if not self._ped_active or self.tabs.get() != "PDF Studio":
            return
        if isinstance(what, tuple) and what and what[0] == "nudge":
            self._ped_nudge(what[1], what[2], big=bool(event.state & 0x1))
            return "break"
        if what == "select_all":
            e = self._ped_entry()
            if e is not None:
                self._ped_set_selection([it for it in e.items if not it.hidden])
            return "break"
        if what == "group":
            self._ped_group()
            return "break"
        if what == "ungroup":
            self._ped_ungroup()
            return "break"
        if what == "duplicate":
            self._ped_duplicate_item()
            return "break"
        if what == "delete":
            if self._ped_card_sel_word() is not None:
                self._ped_card_delete()
            else:
                self._ped_delete_item()
        elif what == "undo":
            self._ped_undo()
        elif what == "redo":
            self._ped_redo()
        elif what == "copy":
            self._ped_copy()
        elif what == "cut":
            self._ped_copy()
            self._ped_delete_item()
        elif what == "paste":
            self._ped_paste()
        elif what == "escape":
            self._ped_place_word = None
            self._ped_poly = None
            self._ped_ghost = None
            self._ped_draw_state = None
            if self._ped_crop:
                self._ped_toggle_crop()
            self._ped_set_tool("select")
            # v15.10: Escape also puts the current object down
            if self._ped_selection():
                self._ped_set_selection([])
            if getattr(self, "_ped_card_sel", None) is not None:
                self._ped_card_sel = None
                self._ped_sync_card_sel()
            self._ped_draw_overlay()
        elif what == "finish":
            self._ped_finish_polygon()
        return "break"

    # ------------------------------------------------------------ page operations
    def _ped_op(self, what, arg=None):
        s = self._ped
        e = self._ped_entry()
        if s is None or e is None:
            return
        i, n = self._ped_cur, len(s.pages)
        try:
            if what == "rotate":
                s.apply(("rotate", i, arg))
                self._ped_pan = [0.0, 0.0]
            elif what == "delete":
                s.apply(("delete", i))
                self._ped_cur = min(i, n - 2)
                self.flash_status(f"Deleted page {i + 1} (your original file is untouched)")
            elif what == "duplicate":
                s.apply(("duplicate", i))
                self._ped_cur = i + 1
            elif what in ("up", "down"):
                j = i - 1 if what == "up" else i + 1
                if not (0 <= j < n):
                    return
                s.apply(("move", i, j))
                self._ped_cur = j
        except PdfEditError as ex:
            return messagebox.showinfo(_pal().APP_NAME, str(ex))
        self._ped_sel = None
        self._ped_page = None if what == "rotate" else self._ped_page
        self._ped_changed()

    def _ped_undo(self):
        if self._ped is not None and self._ped.undo():
            self._ped_sel = None
            self._ped_selset = []
            self._ped_changed()
        else:
            self.flash_status("Nothing to undo", ok=False)

    def _ped_redo(self):
        if self._ped is not None and self._ped.redo():
            self._ped_sel = None
            self._ped_selset = []
            self._ped_changed()
        else:
            self.flash_status("Nothing to redo", ok=False)

    def _ped_insert_pdf(self):
        s = self._ped
        if s is None or self.busy:
            return
        path = filedialog.askopenfilename(title="Insert pages from another PDF",
                                          initialdir=self.last_dir or None,
                                          filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")])
        if not path:
            return
        at = self._ped_cur + 1

        def work():
            try:
                return s.load_source(path)[1]
            except PdfEditError as ex:
                return str(ex)

        def done(res):
            if isinstance(res, str):
                self.flash_status(res, ok=False, hold=True)
                messagebox.showwarning(_pal().APP_NAME, res)
                return
            if s is not self._ped:
                return
            s.apply(("insert", at, res))
            self._ped_cur = at
            self._ped_sel = None
            self.flash_status(f"Inserted {len(res)} page(s) from {Path(path).name}")
            self._ped_changed()
        self.bg("Reading PDF…", work, done)

    # ------------------------------------------------------------ v15.53 page as picture
    def _ped_place_page_as_picture(self, entry=None):
        """Place an uploaded PDF page as a picture object on the current
        page, where it moves and drag-resizes like any picture — no new
        page is made, and the picture lands at its natural size even when
        it runs over the sides of the page (drag corners to resize).  With
        no entry, the current page is placed when it is a file page;
        otherwise a menu of the document's file pages is offered to pick
        from."""
        s = self._ped
        if s is None or self.busy:
            return
        if entry is not None:
            self._ped_place_page_as_picture_go(entry)
            return
        e = self._ped_entry()
        if e is not None and e.src >= 0:
            self._ped_place_page_as_picture_go(e)
            return
        files = [(i, p) for i, p in enumerate(s.pages) if p.src >= 0]
        if not files:
            self.flash_status("No uploaded PDF pages in this document", ok=False)
            return
        if len(files) == 1:
            self._ped_place_page_as_picture_go(files[0][1])
            return
        m = self._ped_menu_style(tk.Menu(self.canvas, tearoff=0))
        for i, p in files:
            m.add_command(label=f"Page {i + 1}",
                          command=lambda p=p: self._ped_place_page_as_picture_go(p))
        try:
            m.tk_popup(self.canvas.winfo_pointerx(), self.canvas.winfo_pointery())
        finally:
            try:
                m.grab_release()
            except Exception:
                pass

    def _ped_place_page_as_picture_go(self, e):
        s = self._ped
        if s is None or self.busy:
            return
        key = e.key
        W, H = e.size
        # ~2000 px on the long side: crisp on screen and in print, without
        # embedding a multi-megapixel image for every placed page.
        scale = 2000.0 / max(W, H)

        def work():
            try:
                return s.render_page(key[0], key[1], key[2], scale)
            except PdfEditError as ex:
                return str(ex)

        def done(res):
            if isinstance(res, str):
                self.flash_status(res, ok=False, hold=True)
                messagebox.showwarning(_pal().APP_NAME, res)
                return
            if s is not self._ped:
                return
            # v15.56: the picture lands on the page being worked on, at
            # its natural size — no fresh page, no fitting to the page's
            # orientation, even if it runs over the sides (the user
            # resizes it).  The source page itself is untouched.
            pic = Item("image", 0.0, 0.0, float(W), float(H),
                       img=res.convert("RGBA"), name="PDF page")
            self._ped_put(pic)
            self._ped_draw()
            self.flash_status("PDF page placed as a picture on this page — drag it anywhere, drag corners to resize")

        self.bg("Rendering page…", work, done)

    # ------------------------------------------------------------ thumbnail strip
    def _ped_grab_canvas(self):
        """v15.13: hold the pointer for an item drag, so motions keep arriving
        while the drag crosses off the canvas onto the page strip."""
        try:
            self.canvas.grab_set()
            return True
        except Exception:
            return False

    def _ped_strip_target_page(self, event):
        """The page whose strip thumbnail the pointer is over (canvas coords),
        or None — the strip sits right of the canvas in the same row."""
        if self._ped is None or not getattr(self, "_ped_ui_built", False):
            return None
        try:
            cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
            if not (cw < event.x < cw + 200 and 0 <= event.y < ch):
                return None
            top = self._ped_strip.winfo_rooty() - self.canvas.winfo_rooty()
            cy = self._ped_strip_cv.canvasy(0) + event.y - top
            j = int(cy // THUMB_CELL)
        except Exception:
            return None
        if 0 <= j < len(self._ped.pages) and j != self._ped_cur:
            return j
        return None

    def _ped_strip_track(self, event):
        """During an item drag: light up the strip thumbnail under the pointer
        (redrawn only when the target changes)."""
        j = self._ped_strip_target_page(event)
        if j != getattr(self, "_ped_strip_target", None):
            self._ped_strip_target = j
            self._ped_strip_draw()

    def _ped_strip_drop(self, items, tgt):
        """Move the dragged items onto page tgt, clamped inside it, and follow
        them there with the selection kept.  No new undo step: the drag's own
        first-motion snapshot already covers the move."""
        src = self._ped_entry()
        if src is None or tgt is None:
            return
        dst = self._ped.pages[tgt]
        TW, TH = dst.size
        for it in list(items):
            if it in src.items:
                src.items.remove(it)
                w, h = it.x1 - it.x0, it.y1 - it.y0
                dx = min(0.0, TW - it.x1) if it.x1 > TW else max(0.0, -it.x0)
                dy = min(0.0, TH - it.y1) if it.y1 > TH else max(0.0, -it.y0)
                if it.points:
                    it.points = [(qx + dx, qy + dy) for qx, qy in it.points]
                it.x0, it.y0, it.x1, it.y1 = it.x0 + dx, it.y0 + dy, it.x1 + dx, it.y1 + dy
                dst.items.append(it)
        self._ped.dirty = True
        self._ped_goto(tgt)
        self._ped_set_selection([it for it in items if it in dst.items],
                                primary=items[0] if items else None)
        self._ped_draw_overlay()
        self._ped_sync_props()
        self.flash_status(f"Moved to page {tgt + 1}")

    def _ped_strip_draw(self):
        if not self._ped_ui_built or not self._ped_active or self._ped is None:
            return
        from PIL import ImageTk
        cv = self._ped_strip_cv
        cv.delete("all")
        cv.configure(bg="#2C2F3A" if ctk.get_appearance_mode() == "Dark" else "#F1EDF5")
        pages = self._ped.pages
        n = len(pages)
        cv.configure(scrollregion=(0, 0, 124, n * THUMB_CELL))
        top = cv.canvasy(0)
        bot = top + max(cv.winfo_height(), 200)
        lo = max(0, int(top // THUMB_CELL) - 2)
        hi = min(n, int(bot // THUMB_CELL) + 3)
        accent = _pal().ACCENT
        for i in range(lo, hi):
            e = pages[i]
            y = i * THUMB_CELL
            cur = i == self._ped_cur
            tgt = i == getattr(self, "_ped_strip_target", None)
            cv.create_rectangle(3, y + 3, 121, y + THUMB_CELL - 3,
                                outline=self.PED_PURPLE if tgt else (accent if cur else "#555555"),
                                width=3 if (cur or tgt) else 1, tags=("ped-cell", f"cell{i}"))
            th = self._ped_thumbs.get(e.key)
            if isinstance(th, Image.Image):
                ph = self._ped_thumb_photos.get(e.key)
                if ph is None:
                    ph = self._ped_thumb_photos[e.key] = ImageTk.PhotoImage(th)
                cv.create_image(62, y + 8 + (THUMB_BOX[1] - th.height) / 2 + th.height / 2, image=ph)
            else:
                cv.create_text(62, y + 8 + THUMB_BOX[1] / 2, text="…" if th is None else "?", fill="#888888")
                if th is None and e.key not in self._ped_thumb_req:
                    W, H = e.size
                    self._ped_thumb_req.add(e.key)
                    self._ped_submit(1, ("thumb", self._ped, 0, e.key, min(THUMB_BOX[0] / W, THUMB_BOX[1] / H)))
            if e.items:
                cv.create_text(112, y + 12, text="●", fill=accent, font=("Arial", 9))
            cv.create_text(62, y + THUMB_CELL - 12, text=str(i + 1), fill="#cccccc" if
                           ctk.get_appearance_mode() == "Dark" else "#333333", font=("Arial", 10, "bold"))

    def _ped_strip_scroll(self, *args):
        self._ped_strip_cv.yview(*args)
        self._ped_strip_draw()

    def _ped_strip_wheel(self, event):
        up = getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0
        self._ped_strip_cv.yview_scroll(-3 if up else 3, "units")
        self._ped_strip_draw()

    def _ped_strip_see(self, i):
        cv = self._ped_strip_cv
        n = len(self._ped.pages) if self._ped else 0
        if n == 0:
            return
        total = n * THUMB_CELL
        top, bot = cv.canvasy(0), cv.canvasy(0) + max(cv.winfo_height(), 200)
        if i * THUMB_CELL < top or (i + 1) * THUMB_CELL > bot:
            cv.yview_moveto(max(0.0, (i * THUMB_CELL - 20) / total))

    def _ped_strip_click(self, event):
        i = int(self._ped_strip_cv.canvasy(event.y) // THUMB_CELL)
        # v15.65: in row mode a thumbnail just focuses its page and the
        # row slides it into view — the row never re-anchors.
        if self._ped_row_active():
            self._ped_focus_page(i, scroll=True)
            return
        # v15.55: a thumbnail of a page already in the pair just focuses
        # it — the pair stays put; any other thumbnail re-anchors it.
        if self._ped_two_active():
            base = self._ped_two_base_idx()
            if i in (base, base + 1):
                self._ped_focus_page(i)
                return
        self._ped_goto(i)

    # ------------------------------------------------------------ stamping items
    def _ped_center_pt(self):
        e = self._ped_entry()
        s, ox, oy, _ = self._ped_layout()
        W, H = e.size
        cw, ch = max(self.canvas.winfo_width(), 200), max(self.canvas.winfo_height(), 200)
        return (max(30.0, min(W - 30.0, (cw / 2 - ox) / s)), max(30.0, min(H - 30.0, (ch / 2 - oy) / s)))

    def _ped_put(self, it):
        e = self._ped_entry()
        self._ped.push_undo()
        e.items.append(it)
        self._ped.dirty = True
        self._ped_sel = it
        self._ped_selset = [it]
        self._ped_draw_overlay()
        self._ped_sync_props()

    def _ped_add_card(self):
        e = self._ped_entry()
        word = (self._ped_word_var.get() or "").strip()
        if e is None:
            return
        if not word:
            return messagebox.showinfo(_pal().APP_NAME, "Pick or type a word first.")
        cx, cy = self._ped_center_pt()
        use_cat = self.pdf_colors_var.get() if hasattr(self, "pdf_colors_var") else True
        color = _pal().aac_boards.category_color(word) if use_cat else "#FFFFFF"
        it = new_card(word, cx, cy, color)
        self._ped_put(it)
        self._ped_fetch_picture(it)

    # ------------------------------------------------------------ v15.8 harvesting
    def _ped_rect_to_source(self, e, rect):
        """A rect in the page's current (editor-rotated) visible coords, mapped back
        to the source page's own visible coords (what the engine methods expect)."""
        x0, y0, x1, y1 = rect
        sw, sh = e.w, e.h
        rot = e.rot % 360

        def inv(px, py):
            if rot == 90:
                return (py, sh - px)
            if rot == 180:
                return (sw - px, sh - py)
            if rot == 270:
                return (sw - py, px)
            return (px, py)

        pts = [inv(a, b) for a, b in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
        xs = [q[0] for q in pts]
        ys = [q[1] for q in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    def _ped_harvest_finish(self, tool, rect):
        sess, e = self._ped, self._ped_entry()
        self._ped_set_tool("select")
        if sess is None or e is None:
            return
        if e.src < 0:
            self.flash_status("Harvesting works on pages from an opened PDF — "
                              "this page was made in the app", ok=False)
            return
        srect = self._ped_rect_to_source(e, rect)
        if tool == "harvest_words":
            try:
                _text, words = sess.extract_text(e.src, e.idx, srect)
            except Exception as ex:
                self.flash_status(f"Couldn't read words there: {ex}", ok=False)
                return
            words = [w for w in words if any(ch.isalnum() for ch in w)]
            if not words:
                self.flash_status("No words in that area — on a scanned page the words "
                                  "are part of the picture; use ✂ Save picture instead",
                                  ok=False)
                return
            self._ped_harvest_add_words(words)
            return
        # harvest_pic: render the region off-thread, then ask for its word
        def work():
            return sess.render_region(e.src, e.idx, srect, scale=4.0)

        def done(img):
            if img is None or img.width < 4 or img.height < 4:
                self.flash_status("Couldn't cut that picture out", ok=False)
                return
            self._ped_harvest_save_picture(img)

        self.bg("Cutting out the picture…", work, done)

    def _ped_harvest_add_words(self, words):
        have = {w.lower() for w in self.get_words()}
        new = []
        for w in words:
            if w.lower() not in have:
                have.add(w.lower())
                new.append(w)
        if new:
            try:
                self.set_words(self.get_words() + new)
            except Exception:
                pass
        for attr in ("_pdf_words", "_pdf_box_words"):
            cur = list(getattr(self, attr, []) or [])
            known = {str(w).lower() for w in cur}
            cur += [w for w in words if w.lower() not in known]
            setattr(self, attr, cur)
        self._ped_render_box_tiles()
        self._ped_refresh_words()
        shown = ", ".join(words[:8]) + ("…" if len(words) > 8 else "")
        if new:
            self.flash_status(f"Saved {len(new)} new word(s) to your Word Lists: {shown}")
        else:
            self.flash_status(f"Those words are already in your Word Lists: {shown}")

    def _ped_harvest_save_picture(self, img):
        core = _pal().core
        self._ped_harvest_seq += 1
        dlg = ctk.CTkInputDialog(
            text="Word for this picture (how you'll find it in the Library):",
            title="Save picture to Library")
        val = dlg.get_input()
        if val is None:
            self.flash_status("Picture not saved")
            return
        name = val.strip() or f"PDF picture {self._ped_harvest_seq}"
        buf = io.BytesIO()
        img.convert("RGBA").save(buf, "PNG")
        data = buf.getvalue()
        try:
            variant = 0
            while core._icon_from_index(name, variant) is not None:
                variant += 1
            out = core._persist_icon(data, name, variant, term=name)
            core._remember_icon(name, variant, out, term=name)
        except Exception as ex:
            self.flash_status(f"Couldn't save that picture: {ex}", ok=False)
            return
        self.overrides[name] = str(out)
        self._ped_refresh_words()
        for fn in ("_lib_reload", "_maker_lib_refresh"):
            f = getattr(self, fn, None)
            if callable(f):
                try:
                    f()
                except Exception:
                    pass
        extra = f" (saved as picture {variant + 1} for that word)" if variant else ""
        self.flash_status(f"Saved picture “{name}” to your Library{extra} — "
                          "it will show on cards and tags for that word")

    def _ped_save_item_picture(self, it):
        """v15.60: right-click an object/picture → save its picture into the
        Library, so it shows up in the Maker strip and Library tab. The
        document itself is untouched (no undo step, no dirty flag)."""
        if it is None:
            return
        if it.kind == "image":
            img = cropped_image(it)
            word = None
        elif it.kind == "card":
            img = it.img
            word = it.word
        else:
            return
        if img is None:
            self.flash_status("That object has no picture to save", ok=False)
            return
        core = _pal().core
        if not word:
            dlg = ctk.CTkInputDialog(
                text="Word for this picture (how you'll find it in the Library):",
                title="Save picture to Library")
            try:
                val = dlg.get_input()
            except Exception:
                val = None
            if val is None:
                self.flash_status("Picture not saved")
                return
            word = val.strip() or "PDF picture"
        buf = io.BytesIO()
        img.convert("RGBA").save(buf, "PNG")
        data = buf.getvalue()
        try:
            variant = 0
            while core._icon_from_index(word, variant) is not None:
                variant += 1
            out = core._persist_icon(data, word, variant, term=word)
            core._remember_icon(word, variant, out, term=word)
        except Exception as ex:
            self.flash_status(f"Couldn't save that picture: {ex}", ok=False)
            return
        self.overrides[word] = str(out)
        self._ped_refresh_words()
        for fn in ("_lib_reload", "_maker_lib_refresh"):
            f = getattr(self, fn, None)
            if callable(f):
                try:
                    f()
                except Exception:
                    pass
        extra = f" (saved as picture {variant + 1} for that word)" if variant else ""
        self.flash_status(f"Saved picture “{word}” to your Library{extra}")

    # ------------------------------------------------------------ v15.11 snip
    def _ped_snip_finish(self, rect):
        """One drag, both harvests: a menu at the cursor offers the picture,
        the words, or both from the dragged rectangle, on ANY page."""
        self._ped_set_tool("select")
        e = self._ped_entry()
        if e is None:
            return
        m = self._ped_menu_style(tk.Menu(self, tearoff=0))
        m.add_command(label="Save picture to Library",
                      command=lambda: self._ped_snip_picture(rect))
        m.add_command(label="Save words to Word Lists",
                      command=lambda: self._ped_snip_words(rect))
        m.add_command(label="Save picture + words",
                      command=lambda: (self._ped_snip_picture(rect),
                                       self._ped_snip_words(rect)))
        m.add_separator()
        m.add_command(label="✂ Cut out — move it around",
                      command=lambda: self._ped_snip_cutout(rect))
        m.add_separator()
        m.add_command(label="Cancel", command=m.destroy)
        self._ped_ctx_menu = m
        try:
            m.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            try:
                m.grab_release()
            except Exception:
                pass

    def _ped_snip_words(self, rect):
        sess, e = self._ped, self._ped_entry()
        if sess is None or e is None:
            return
        x0, y0, x1, y1 = rect
        if e.src is not None and e.src >= 0:
            try:
                srect = self._ped_rect_to_source(e, rect)
                _text, words = sess.extract_text(e.src, e.idx, srect)
            except Exception as ex:
                self.flash_status(f"Couldn't read words there: {ex}", ok=False)
                return
            words = [w for w in words if any(ch.isalnum() for ch in w)]
        else:
            # app-made pages have no text layer: take the words of the text
            # and card objects whose centres fall in the rectangle, plus the
            # card-page slot words whose tiles the rectangle overlaps.
            found = []
            for it in e.items:
                if it.hidden:
                    continue
                b = item_aabb(it)
                cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
                if not (x0 <= cx <= x1 and y0 <= cy <= y1):
                    continue
                if it.kind == "card" and it.word:
                    found.append((cy, cx, it.word))
                elif it.kind == "text" and it.text:
                    for w in it.text.split():
                        found.append((cy, cx, w))
            if e.card is not None:
                for slot in self._ped_card_layout(e)["slots"]:
                    sx, sy, sd = slot["x_in"] * 72, slot["y_in"] * 72, slot["side_in"] * 72
                    if slot["word"] and not (sx + sd < x0 or sx > x1 or sy + sd < y0 or sy > y1):
                        found.append((sy, sx, slot["word"]))
            found.sort()
            seen, words = set(), []
            for _y, _x, w in found:
                if w.lower() not in seen:
                    seen.add(w.lower())
                    words.append(w)
        if not words:
            self.flash_status("No words in that area", ok=False)
            return
        self._ped_harvest_add_words(words)

    def _ped_snip_picture(self, rect):
        """Save the snipped region into the Library (the 15.11 snip)."""
        self._ped_snip_render(rect, self._ped_harvest_save_picture)

    def _ped_snip_cutout(self, rect):
        """v15.12: CUT the region out of the page instead of copying it — the
        region becomes a picture object you can drag around, and a white
        patch stays behind where it was cut from."""
        self._ped_snip_render(rect, lambda img: self._ped_cutout_place(img, rect))

    def _ped_cutout_place(self, img, rect):
        e = self._ped_entry()
        if e is None:
            return
        W, H = e.size
        x0, y0, x1, y1 = (max(0.0, rect[0]), max(0.0, rect[1]),
                          min(float(W), rect[2]), min(float(H), rect[3]))
        if x1 - x0 < 4 or y1 - y0 < 4:
            self.flash_status("Nothing to cut out there", ok=False)
            return
        # v15.13: the blank is a permanent page erasure, not an object — it
        # is recorded on the page itself, so it can't be selected, moved
        # or outlined, and it bakes into the saved PDF under everything
        # else.  One undo step covers the erasure and the picture together.
        self._ped.push_undo()
        e.whiteouts.append((x0, y0, x1, y1))
        pic = Item("image", x0, y0, x1, y1, img=img.convert("RGBA"), name="Cut-out")
        e.items.append(pic)
        self._ped.dirty = True
        self._ped_set_selection([pic], primary=pic)
        self._ped_draw_overlay()
        self._ped_sync_props()
        self.flash_status("Cut out — drag it anywhere; the blank stays where it was")

    def _ped_snip_render(self, rect, on_image):
        """Render a page rectangle to a PIL image (4×) and hand it to
        on_image: straight off the source PDF for a file page, or via a
        throwaway build of the document for an app-made page (the built page
        shows exactly what the editor shows, so the editor rectangle maps
        straight onto it)."""
        sess, e = self._ped, self._ped_entry()
        if sess is None or e is None:
            return
        if e.src is not None and e.src >= 0:
            # a page from an opened PDF: render the region straight off it
            srect = self._ped_rect_to_source(e, rect)

            def work():
                return sess.render_region(e.src, e.idx, srect, scale=4.0)

            def done(img):
                if img is None or img.width < 4 or img.height < 4:
                    self.flash_status("Couldn't cut that picture out", ok=False)
                    return
                on_image(img)

            self.bg("Cutting out the picture…", work, done)
            return
        cur = self._ped_cur
        x0, y0, x1, y1 = rect

        def work():
            _fitz = require_fitz()
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            tmp.close()
            try:
                sess.build(sess.snapshot(), tmp.name)
                doc = _fitz.open(tmp.name)
                try:
                    page = doc[cur]
                    pix = page.get_pixmap(matrix=_fitz.Matrix(4.0, 4.0))
                    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    return img.crop((int(x0 * 4), int(y0 * 4),
                                     int(x1 * 4), int(y1 * 4)))
                finally:
                    doc.close()
            finally:
                try:
                    os.unlink(tmp.name)
                except Exception:
                    pass

        def done(img):
            if img is None or img.width < 4 or img.height < 4:
                self.flash_status("Couldn't cut that picture out", ok=False)
                return
            on_image(img)

        self.bg("Cutting out the picture…", work, done)

    def _ped_fetch_picture(self, it):
        """Find the picture with the app's EXISTING lookup (saved picture choices, library icons).
        Local only. Runs in a worker; the result is applied back on the main thread."""
        word = it.word
        it.loading = True
        ov, var, sa = dict(self.overrides), dict(self.variants), dict(self.search_as)
        boards = _pal().aac_boards

        def work():
            return boards._load_icon_rgba(word, size_px=512, overrides=ov, variants=var, search_as=sa,
                                          local_only=True)

        def done(img):
            if it.word != word:
                return
            it.img, it.loading = img, False
            if not self._ped_active:
                return
            if img is None:
                self.flash_status(f"No picture found for “{word}” — the card shows the word.", ok=False)
            self._ped_draw_overlay()
        self.bg(f"Finding the picture for “{word}”…", work, done)

    def _ped_add_text(self):
        e = self._ped_entry()
        if e is None:
            return
        txt = self._ped_text_entry.get().strip()
        if not txt:
            return messagebox.showinfo(_pal().APP_NAME, "Type the text for the label first.")
        cx, cy = self._ped_center_pt()
        self._ped_put(new_text(txt, cx, cy, size=float(self._ped_size_var.get())))
        self._ped_text_entry.delete(0, "end")

    def _ped_add_rect(self):
        if self._ped_entry() is None:
            return
        cx, cy = self._ped_center_pt()
        self._ped_put(new_rect(cx, cy, color=_pal().SUN))

    def _ped_delete_item(self):
        e = self._ped_entry()
        sel = self._ped_selection()
        if e is None or not sel:
            return
        self._ped.push_undo()
        for it in sel:
            if it in e.items:
                e.items.remove(it)
        self._ped.dirty = True
        self._ped_sel, self._ped_selset = None, []
        self._ped_draw_overlay()
        self._ped_sync_props()

    # ------------------------------------------------------------ v15.5 item actions
    def _ped_copy(self):
        sel = self._ped_selection()
        e = self._ped_entry()
        if sel and e is not None:
            self._ped_clip = [it.clone() for it in sel]
            n = len(sel)
            self.flash_status(f"Copied {n} object{'' if n == 1 else 's'} — Ctrl+V pastes on this page")

    def _ped_paste(self):
        e = self._ped_entry()
        if e is None or not self._ped_clip:
            return
        clips = self._ped_clip if isinstance(self._ped_clip, list) else [self._ped_clip]
        W, H = e.size
        news = []
        self._ped.push_undo()
        for c0 in clips:
            it = c0.clone()
            dx = min(18.0, max(0.0, W - it.x1 - 1))
            dy = min(18.0, max(0.0, H - it.y1 - 1))
            it.x0, it.y0, it.x1, it.y1 = it.x0 + dx, it.y0 + dy, it.x1 + dx, it.y1 + dy
            if it.points:
                it.points = [(qx + dx, qy + dy) for qx, qy in it.points]
            e.items.append(it)
            news.append(it)
        self._ped.dirty = True
        self._ped_set_selection(news, primary=news[-1] if news else None)
        self._ped_draw_overlay()

    def _ped_duplicate_item(self):
        sel = self._ped_selection()
        e = self._ped_entry()
        if not sel or e is None:
            return messagebox.showinfo(_pal().APP_NAME, "Click an item on the page first.")
        self._ped_clip = [it.clone() for it in sel]
        self._ped_paste()

    def _ped_layer(self, forward):
        """Bring forward / send backward — every selected object steps one
        place, keeping the selection's internal order."""
        e = self._ped_entry()
        sel = self._ped_selection()
        if e is None or not sel:
            return
        idxs = sorted((e.items.index(it) for it in sel if it in e.items), reverse=forward)
        can = any(0 <= (i + 1 if forward else i - 1) < len(e.items)
                  and e.items[i + 1 if forward else i - 1] not in sel for i in idxs)
        if not can:
            return
        self._ped.push_undo()
        for i in idxs:
            j = i + 1 if forward else i - 1
            if 0 <= j < len(e.items) and e.items[j] not in sel:
                e.items[i], e.items[j] = e.items[j], e.items[i]
        self._ped.dirty = True
        self._ped_draw_overlay()
        self._ped_objs_refresh()

    def _ped_align_pick(self, val):
        self._ped_align_menu.set("Align…")
        m = {"Left": "l", "Center": "c", "Right": "r", "Top": "t", "Middle": "m", "Bottom": "b"}.get(val)
        if m:
            self._ped_align(m)

    def _ped_move_item_by(self, it, dx, dy):
        it.x0, it.y0, it.x1, it.y1 = it.x0 + dx, it.y0 + dy, it.x1 + dx, it.y1 + dy
        if it.points:
            it.points = [(qx + dx, qy + dy) for qx, qy in it.points]

    def _ped_align(self, mode):
        """One selected object aligns to the page (the classic behaviour);
        with several selected they align to each other (the selection's own
        bounding box), the way Inkscape does it."""
        e = self._ped_entry()
        sel = [it for it in self._ped_selection() if not it.locked]
        if e is None or not sel:
            return
        if len(sel) == 1:
            it = sel[0]
            W, H = e.size
            b = item_aabb(it)
            w, h = b[2] - b[0], b[3] - b[1]
            nx, ny = b[0], b[1]
            if mode == "l":
                nx = 24.0
            elif mode == "c":
                nx = (W - w) / 2
            elif mode == "r":
                nx = W - 24.0 - w
            elif mode == "t":
                ny = 24.0
            elif mode == "m":
                ny = (H - h) / 2
            elif mode == "b":
                ny = H - 24.0 - h
            dx, dy = nx - b[0], ny - b[1]
            if dx == 0 and dy == 0:
                return
            self._ped.push_undo()
            self._ped_move_item_by(it, dx, dy)
        else:
            B = self._ped_sel_bounds(sel)
            self._ped.push_undo()
            for it in sel:
                b = item_aabb(it)
                dx = dy = 0.0
                if mode == "l":
                    dx = B[0] - b[0]
                elif mode == "c":
                    dx = (B[0] + B[2]) / 2 - (b[0] + b[2]) / 2
                elif mode == "r":
                    dx = B[2] - b[2]
                elif mode == "t":
                    dy = B[1] - b[1]
                elif mode == "m":
                    dy = (B[1] + B[3]) / 2 - (b[1] + b[3]) / 2
                elif mode == "b":
                    dy = B[3] - b[3]
                if dx or dy:
                    self._ped_move_item_by(it, dx, dy)
        self._ped.dirty = True
        self._ped_draw_overlay()
        self._ped_props_refresh()

    def _ped_distribute(self, axis):
        """Space 3+ selected objects evenly along an axis (equal gaps between
        their visible boxes); the two end objects stay put."""
        e = self._ped_entry()
        sel = [it for it in self._ped_selection() if not it.locked]
        if e is None or len(sel) < 3:
            return
        keyed = sorted(sel, key=lambda it: (item_aabb(it)[0] + item_aabb(it)[2]) / 2
                       if axis == "h" else (item_aabb(it)[1] + item_aabb(it)[3]) / 2)
        boxes = [item_aabb(it) for it in keyed]
        i0, i1 = (0, 2) if axis == "h" else (1, 3)
        total = boxes[-1][i1] - boxes[0][i0]
        sizes = sum(b[i1] - b[i0] for b in boxes)
        gap = (total - sizes) / (len(keyed) - 1)
        self._ped.push_undo()
        pos = boxes[0][i1] + gap
        for it, b in zip(keyed[1:-1], boxes[1:-1]):
            d = pos - b[i0]
            if axis == "h":
                self._ped_move_item_by(it, d, 0.0)
            else:
                self._ped_move_item_by(it, 0.0, d)
            pos += (b[i1] - b[i0]) + gap
        self._ped.dirty = True
        self._ped_draw_overlay()
        self._ped_props_refresh()

    def _ped_combine(self):
        """v15.63: flatten the selected visible objects into ONE picture
        object — the stacked-tiles case: a small card on a big card
        becomes a single object you can drag around, instead of two
        objects that only look like one. The picture is rendered from
        the objects themselves (like a snip cut around the outside of
        the largest one), so the page underneath shows through again
        once the originals are gone. One undo step."""
        e = self._ped_entry()
        sel = [it for it in self._ped_selection() if not it.hidden]
        if e is None or len(sel) < 2:
            return
        if any(it.kind == "card" and it.loading for it in sel):
            self.flash_status("Wait for the pictures to finish loading, then combine again",
                              ok=False)
            return
        items = [it for it in e.items if it in sel]
        if len(items) < 2:
            return
        x0 = min(item_aabb(it)[0] for it in items)
        y0 = min(item_aabb(it)[1] for it in items)
        x1 = max(item_aabb(it)[2] for it in items)
        y1 = max(item_aabb(it)[3] for it in items)
        if x1 - x0 < 4 or y1 - y0 < 4:
            self.flash_status("Nothing big enough to combine there", ok=False)
            return
        try:
            img = combine_render(items, (x0, y0, x1, y1))
        except Exception as ex:
            self.flash_status(f"Couldn't combine those: {ex}", ok=False)
            return
        self._ped.push_undo()
        for it in items:
            try:
                e.items.remove(it)
            except ValueError:
                pass
        pic = Item("image", x0, y0, x1, y1, img=img.convert("RGBA"), name="Combined")
        e.items.append(pic)
        self._ped.dirty = True
        self._ped_set_selection([pic], primary=pic)
        self._ped_draw_overlay()
        self._ped_objs_refresh()
        self.flash_status(f"Combined {len(items)} objects into one picture — drag it anywhere")

    def _ped_group(self):
        e = self._ped_entry()
        sel = self._ped_selection()
        if e is None or len(sel) < 2:
            return
        self._ped.push_undo()
        gid = max([it.gid for it in e.items] + [0]) + 1
        for it in sel:
            it.gid = gid
        self._ped.dirty = True
        self.flash_status(f"Grouped {len(sel)} objects — click one to move them all")
        self._ped_objs_refresh()

    def _ped_ungroup(self):
        e = self._ped_entry()
        sel = self._ped_selection()
        if e is None or not any(it.gid for it in sel):
            return
        self._ped.push_undo()
        for it in sel:
            it.gid = 0
        self._ped.dirty = True
        self.flash_status("Ungrouped")
        self._ped_objs_refresh()

    def _ped_nudge(self, dx, dy, big=False):
        import time
        sel = [it for it in self._ped_selection() if not it.locked]
        if not sel:
            return
        step = 10.0 if big else 1.0
        uids = tuple(sorted(it.uid for it in sel))
        now = time.monotonic()
        mark = self._ped_nudge_mark
        if not (mark[1] == uids and now - mark[0] < 0.8):
            self._ped.push_undo()
        self._ped_nudge_mark = (now, uids)
        for it in sel:
            self._ped_move_item_by(it, dx * step, dy * step)
        self._ped.dirty = True
        self._ped_draw_overlay()
        self._ped_props_refresh()

    def _ped_zoom_selection(self):
        """Zoom + centre on the selected objects (or fit the page if none)."""
        e = self._ped_entry()
        if e is None:
            return
        b = self._ped_sel_bounds()
        if b is None:
            return self._ped_fit()
        s, ox, oy, fit = self._ped_layout()
        cw = max(self.canvas.winfo_width(), 200)
        ch = max(self.canvas.winfo_height(), 200)
        ins = self._ped_insets()
        avail_w = max(80, cw - ins[0] - ins[1])
        bw, bh = max(b[2] - b[0], 8.0), max(b[3] - b[1], 8.0)
        W, H = e.size
        bcx, bcy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        if self._ped_two_active():
            # v15.63: centre the selection in the free canvas area. The
            # pair row keeps its gutter, so centring can no longer shove
            # the focused page over the other one (the old half-centring
            # needed that workaround).
            s2 = max(0.05, min(8.0, min((avail_w - 48) / bw, (ch - 48) / bh)))
            self._ped_zoom = s2 / fit
            oxf, _ox2 = self._ped_pair_origins(s2)
            tcx, tcy = ins[0] + avail_w / 2, ch / 2
            self._ped_pan = [tcx - bcx * s2 - oxf,
                             tcy - bcy * s2 - (ch - H * s2) / 2]
        else:
            s2 = max(0.05, min(8.0, min((avail_w - 48) / bw, (ch - 48) / bh)))
            self._ped_zoom = s2 / fit
            tcx, tcy = ins[0] + avail_w / 2, ch / 2      # centre of the free canvas area
            self._ped_pan = [tcx - bcx * s2 - ins[0] - (avail_w - W * s2) / 2,
                             tcy - bcy * s2 - (ch - H * s2) / 2]
        self._ped_draw()

    def _ped_tool_key(self, tool):
        if self._ped_editing is not None:
            return "break"
        if self._ped_active and self.tabs.get() == "PDF Studio":
            self._ped_toggle_tool(tool)
        return "break"

    def _ped_space_down(self, event):
        if self._ped_active and self.tabs.get() == "PDF Studio":
            self._ped_space = True
            try:
                self.canvas.configure(cursor="hand2")
            except Exception:
                pass
        return "break"

    def _ped_space_up(self, event):
        self._ped_space = False
        if self._ped_active and self.tabs.get() == "PDF Studio":
            self._ped_set_tool(self._ped_tool)     # restores the tool cursor
        return "break"

    def _ped_rotate_item(self):
        e = self._ped_entry()
        sel = self._ped_selection()
        if e is None or not sel:
            return messagebox.showinfo(_pal().APP_NAME, "Click an item on the page first.")
        self._ped.push_undo()
        for it in sel:
            cx, cy = item_center(it)
            if it.points:
                it.points = [(cx - (qy - cy), cy + (qx - cx)) for qx, qy in it.points]
                xs = [q[0] for q in it.points]
                ys = [q[1] for q in it.points]
                it.x0, it.y0, it.x1, it.y1 = min(xs), min(ys), max(xs), max(ys)
            else:
                # v15.9: the box stays put; the content turns inside it.
                it.rot = (it.rot + 90) % 360
        self._ped.dirty = True
        self._ped_draw_overlay()
        self._ped_sync_props()

    def _ped_import_image(self):
        if self._ped_entry() is None:
            return
        path = filedialog.askopenfilename(
            title="Import a picture", initialdir=self.last_dir or None,
            filetypes=[("Pictures", "*.png *.jpg *.jpeg *.gif *.bmp *.webp *.tif *.tiff"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            im = Image.open(path)
            im.load()
            im = im.convert("RGBA")
            if max(im.size) > 2000:
                im.thumbnail((2000, 2000), Image.Resampling.LANCZOS)
        except Exception as ex:
            return messagebox.showwarning(_pal().APP_NAME, f"Couldn't open that picture ({ex}).")
        self.last_dir = str(Path(path).parent)
        cx, cy = self._ped_center_pt()
        e = self._ped_entry()
        W, H = e.size
        self._ped_put(new_image(im, cx, cy, max_w=W * 0.6, max_h=H * 0.6))
        self._ped_set_tool("select")
        self.flash_status(f"Imported {Path(path).name} — drag to move, drag corners to resize")

    def _ped_replace_image(self):
        it = self._ped_sel
        e = self._ped_entry()
        if it is None or e is None or it not in e.items or it.kind not in ("image", "card"):
            return messagebox.showinfo(_pal().APP_NAME,
                                       "Select an imported picture (or a picture card) first.")
        path = filedialog.askopenfilename(
            title="Replace with picture", initialdir=self.last_dir or None,
            filetypes=[("Pictures", "*.png *.jpg *.jpeg *.gif *.bmp *.webp *.tif *.tiff"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            im = Image.open(path)
            im.load()
            im = im.convert("RGBA")
            if max(im.size) > 2000:
                im.thumbnail((2000, 2000), Image.Resampling.LANCZOS)
        except Exception as ex:
            return messagebox.showwarning(_pal().APP_NAME, f"Couldn't open that picture ({ex}).")
        self._ped.push_undo()
        it.img = im
        it.loading = False
        if it.kind == "image":
            it.crop = None
        self._ped.dirty = True
        self._ped_draw_overlay()

    def _ped_toggle_crop(self):
        it = self._ped_sel
        if not self._ped_crop:
            if it is None or it.kind != "image":
                return messagebox.showinfo(_pal().APP_NAME,
                                           "Crop works on imported pictures — select one first.")
            self._ped_crop = True
            self.flash_status("Crop mode: drag the picture's edges to trim it · Esc to finish")
        else:
            self._ped_crop = False
        try:
            self._ped_crop_btn.configure(text="Crop: on" if self._ped_crop else "Crop: off")
        except Exception:
            pass
        self._ped_draw_overlay()

    def _ped_add_note(self):
        if self._ped_entry() is None:
            return
        cx, cy = self._ped_center_pt()
        e = self._ped_entry()
        self._ped.push_undo()
        box = Item("rect", cx - 85, cy - 27, cx + 85, cy + 27, color=_pal().SUN, opacity=0.92)
        txt = new_text("Note", cx, cy, size=13.0, color="#111111")
        e.items.extend([box, txt])
        self._ped.dirty = True
        self._ped_sel = txt
        self._ped_draw_overlay()
        self._ped_strip_draw()
        self._ped_sync_props()
        self.flash_status("Note added — double-click its text to edit it")

    def _ped_add_stamp(self, val):
        self._ped_stamp_menu.set("Stamp…")
        if self._ped_entry() is None or val == "Stamp…":
            return
        cx, cy = self._ped_center_pt()
        if val.startswith("★"):
            it = new_text(val, cx, cy, size=26.0, color="#d43a3a", bold=True)
        elif val.startswith("✓"):
            it = new_text(val, cx, cy, size=26.0, color="#1f8a70", bold=True)
        else:
            it = new_text(val, cx, cy, size=15.0, color="#111111")
        self._ped_put(it)

    def _ped_add_page_menu(self):
        """v15.10: one clear 'Add page' menu — blank pages and card pages."""
        m = self._ped_menu_style(tk.Menu(self, tearoff=0))
        m.add_command(label="Blank page — Letter", command=lambda: self._ped_blank_pick("Letter"))
        m.add_command(label="Blank page — Legal", command=lambda: self._ped_blank_pick("Legal"))
        m.add_command(label="Blank page — A4", command=lambda: self._ped_blank_pick("A4"))
        m.add_separator()
        m.add_command(label="Card page (picture cards)", command=self._ped_add_card_page)
        m.add_command(label="Insert pages from a PDF…", command=self._ped_insert_pdf)
        self._ped_ctx_menu = m
        b = getattr(self, "_ped_addpage_btn", None)
        try:
            if b is not None:
                m.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())
            else:
                m.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            try:
                m.grab_release()
            except Exception:
                pass

    def _ped_blank_pick(self, val):
        self._ped_blank_menu.set("Blank page…")
        sizes = {"Letter": (612.0, 792.0), "Legal": (612.0, 1008.0), "A4": (595.28, 841.89)}
        if val in sizes:
            self._ped_insert_blank(*sizes[val])
        elif val == "Custom…":
            dlg = ctk.CTkInputDialog(text="Page width, in inches (e.g. 8.5):", title="Custom page")
            wv = dlg.get_input()
            if not wv:
                return
            dlg = ctk.CTkInputDialog(text="Page height, in inches (e.g. 11):", title="Custom page")
            hv = dlg.get_input()
            if not hv:
                return
            try:
                w, h = float(wv) * 72, float(hv) * 72
            except Exception:
                return messagebox.showwarning(_pal().APP_NAME, "Those sizes don't look like numbers.")
            if not (72 <= w <= 2400 and 72 <= h <= 2400):
                return messagebox.showwarning(_pal().APP_NAME, "Keep page sizes between 1 and 33 inches.")
            self._ped_insert_blank(w, h)

    def _ped_insert_blank(self, w, h):
        s = self._ped
        if s is None:
            return
        s.apply(("blank", self._ped_cur + 1, w, h))
        self._ped_cur += 1
        self._ped_sel = None
        self.flash_status(f"Blank page added ({w / 72:.2f} × {h / 72:.2f} in)")
        self._ped_changed()

    def _ped_print(self):
        s = self._ped
        if s is None or not s.pages:
            return
        if self.busy:
            self.flash_status("Still working on the last task — one moment…", ok=False)
            return
        pages = s.snapshot()
        out = Path(tempfile.gettempdir()) / f"aac_print_{uuid.uuid4().hex[:8]}.pdf"

        def work():
            try:
                return s.build(pages, out)
            except PdfEditError as ex:
                return str(ex)

        def done(res):
            if isinstance(res, str):
                self.flash_status(res, ok=False, hold=True)
                messagebox.showwarning(_pal().APP_NAME, res)
                return
            try:
                if sys.platform.startswith("win"):
                    os.startfile(str(out), "print")          # noqa: S606 - user's own print copy
                else:
                    subprocess.Popen(["lp", str(out)])
                self.flash_status("Sent to the printer")
            except Exception:
                self.flash_status(f"Printing isn't available here — a print-ready copy is at {out}",
                                  ok=False, hold=True)
        self.bg("Preparing to print…", work, done, lock=True)

    def _ped_finish_polygon(self):
        pts = self._ped_poly
        self._ped_poly = None
        if pts and len(pts) >= 3 and self._ped_entry() is not None:
            self._ped_put(new_freehand(pts, color=self._ped_shape_line,
                                       lw=max(1.0, self._ped_shape_lw), closed=True,
                                       fill=self._ped_draw_fill()))
            self._ped_set_tool("select")     # v15.10: one-shot, like the other draw tools
        self._ped_draw_overlay()

    # ------------------------------------------------------------ selected-item panel
    def _ped_sync_props(self):
        if not self._ped_ui_built:
            return
        it = self._ped_sel
        e = self._ped_entry()
        sel = self._ped_selection()
        if len(sel) > 1:
            self._ped_sel_lbl.configure(text=f"{len(sel)} objects selected")
            for attr in ("_ped_fmt_frame", "_ped_shape_frame", "_ped_img_frame"):
                try:
                    getattr(self, attr).pack_forget()
                except Exception:
                    pass
            self._ped_props_refresh()
            self._ped_objs_refresh()
            return
        if it is None or e is None or it not in e.items:
            self._ped_sel = None
            self._ped_sel_lbl.configure(text="Nothing selected")
            for attr in ("_ped_fmt_frame", "_ped_shape_frame", "_ped_img_frame"):
                try:
                    getattr(self, attr).pack_forget()
                except Exception:
                    pass
            self._ped_props_refresh()
            self._ped_objs_refresh()
            return
        names = {"card": f"Picture card · {it.word}", "text": "Text", "image": "Imported picture",
                 "ellipse": "Oval", "line": "Line", "arrow": "Arrow",
                 "freehand": "Polygon" if it.closed else "Freehand drawing"}
        self._ped_sel_lbl.configure(text=names.get(
            it.kind, "Rectangle" if it.kind == "rect" and it.border else "Highlight box"))
        try:
            if it.kind == "text":
                self._ped_fmt_frame.pack(fill="x")
            else:
                self._ped_fmt_frame.pack_forget()
            if it.kind in ("ellipse", "line", "arrow", "freehand") or (it.kind == "rect" and it.border):
                self._ped_shape_frame.pack(fill="x")
                self._ped_nofill_var.set(it.fill is None)
            else:
                self._ped_shape_frame.pack_forget()
            if it.kind in ("image", "card"):
                self._ped_img_frame.pack(fill="x")
            else:
                self._ped_img_frame.pack_forget()
        except Exception:
            pass
        if it.kind == "text":
            self._ped_size_var.set(it.size)
            self._ped_size_val.configure(text=f"{round(it.size)} pt")
            self._ped_font_var.set(it.font)
            self._ped_bold_var.set(it.bold)
            self._ped_italic_var.set(it.italic)
            self._ped_align_var.set({"l": "Left", "c": "Center", "r": "Right"}.get(it.align, "Left"))
        self._ped_label_var.set(it.label)
        self._ped_see_var.set(it.opacity < 0.99)
        self._ped_props_refresh()
        self._ped_objs_refresh()

    def _ped_fmt_changed(self, what, value):
        it = self._ped_sel
        if it is None or it.kind != "text" or self._ped_entry() is None \
                or it not in self._ped_entry().items:
            return                       # no selection: the controls are just the tool defaults
        self._ped.push_undo()
        if what == "font":
            it.font = value
        elif what == "bold":
            it.bold = bool(value)
        elif what == "italic":
            it.italic = bool(value)
        elif what == "align":
            it.align = {"Left": "l", "Center": "c", "Right": "r"}.get(value, "l")
        refit_text(it)
        self._ped.dirty = True
        self._ped_draw_overlay()

    def _ped_nofill_toggled(self):
        it = self._ped_sel
        if it is None or self._ped_entry() is None or it not in self._ped_entry().items:
            return
        if it.kind in ("rect", "ellipse", "freehand"):
            self._ped.push_undo()
            it.fill = None if self._ped_nofill_var.get() else (self._ped_shape_fill or "#F5D67A")
            if it.kind == "rect":
                it.border = True
            self._ped.dirty = True
            self._ped_draw_overlay()

    def _ped_size_changed(self, value):
        v = float(value)
        self._ped_size_val.configure(text=f"{round(v)} pt")
        it = self._ped_sel
        if it is not None and it.kind == "text" and self._ped_entry() and it in self._ped_entry().items:
            if abs(it.size - v) < 0.01:
                return
            if getattr(self, "_ped_slider_item", None) is not it:
                self._ped.push_undo()
                self._ped_slider_item = it
            it.size = v
            refit_text(it)
            self._ped.dirty = True
            self._ped_draw_overlay()

    def _ped_pick_color(self):
        it = self._ped_sel
        if it is None:
            return messagebox.showinfo(_pal().APP_NAME, "Click an item on the page first.")
        _, hexv = colorchooser.askcolor(color=it.color, title="Colour")
        if hexv:
            self._ped.push_undo()
            it.color = hexv
            self._ped.dirty = True
            self._ped_draw_overlay()

    def _ped_label_toggled(self):
        it = self._ped_sel
        if it is not None and it.kind == "card":
            self._ped.push_undo()
            it.label = bool(self._ped_label_var.get())
            self._ped.dirty = True
            self._ped_draw_overlay()

    def _ped_see_toggled(self):
        it = self._ped_sel
        if it is not None and it.kind == "rect":
            self._ped.push_undo()
            it.opacity = 0.4 if self._ped_see_var.get() else 1.0
            self._ped.dirty = True
            self._ped_draw_overlay()

    # ------------------------------------------------------------ saving / extracting
    # ------------------------------------------------------------ v15.63 projects
    def _ped_item_data(self, it):
        """One item as JSON-safe data (pictures as base64 PNG)."""
        d = {"kind": it.kind, "x0": it.x0, "y0": it.y0, "x1": it.x1, "y1": it.y1,
             "rot": it.rot, "word": it.word, "text": it.text, "size": it.size,
             "color": it.color, "label": it.label, "opacity": it.opacity,
             "fill": it.fill, "lw": it.lw,
             "points": [list(p) for p in it.points] if it.points else None,
             "closed": it.closed, "border": it.border, "font": it.font,
             "bold": it.bold, "italic": it.italic, "align": it.align,
             "crop": list(it.crop) if it.crop else None,
             "name": it.name, "hidden": it.hidden, "locked": it.locked,
             "gid": it.gid}
        if it.img is not None:
            buf = io.BytesIO()
            it.img.convert("RGBA").save(buf, "PNG")
            d["img"] = base64.b64encode(buf.getvalue()).decode("ascii")
        return d

    def _ped_project_data(self):
        """The whole session as JSON-safe data: every page (file pages
        by source path, blank and card pages whole), every object, the
        focused page and the zoom. Card renders are NOT stored — they
        rebuild from the card specs on open."""
        sess = self._ped
        src_paths = {}
        for e in sess.pages:
            if e.src is not None and e.src >= 0:
                src_paths[e.src] = str(sess.sources[e.src]["path"])
        pages = []
        for e in sess.pages:
            pages.append({"src": e.src, "idx": e.idx, "w": e.w, "h": e.h,
                          "rot": e.rot,
                          "card": copy.deepcopy(e.card) if e.card is not None else None,
                          "whiteouts": [list(w) for w in e.whiteouts],
                          "items": [self._ped_item_data(it) for it in e.items],
                          "src_path": src_paths.get(e.src) if e.src is not None and e.src >= 0 else None})
        return {"app": "AAC Tag Studio", "kind": "pdf-project", "version": 1,
                "cur": self._ped_cur, "zoom": self._ped_zoom, "pages": pages}

    def _ped_save_project(self):
        """Save this PDF Studio work so it opens back up as it was.
        Re-saving goes straight to the same file; a first save asks
        where to put it."""
        if self._ped is None or not self._ped.pages:
            self.flash_status("Nothing to save yet — open or make a document first",
                              ok=False)
            return
        path = getattr(self, "_ped_project_path", None)
        if not path:
            path = filedialog.asksaveasfilename(
                title="Save PDF Studio project", initialdir=self.last_dir or None,
                defaultextension=".aacproj",
                filetypes=[("AAC Tag Studio project", "*.aacproj"),
                           ("All files", "*.*")])
            if not path:
                return
            self.last_dir = str(Path(path).parent)
        try:
            data = self._ped_project_data()
            import json
            Path(path).write_text(json.dumps(data, indent=1), encoding="utf-8")
        except Exception as ex:
            self.flash_status(f"Couldn't save that project: {ex}", ok=False)
            return
        self._ped_project_path = path
        self.flash_status(f"Project saved — open it back up any time from {Path(path).name}")

    def _ped_open_project(self):
        """Open a saved PDF Studio project back up: pages, objects,
        card pages, groups, focus and zoom. File-page backgrounds come
        back from their source PDFs; if one moved, that page keeps your
        objects on a blank page and says so."""
        if self._ped is not None and self._ped.dirty and not messagebox.askyesno(
                _pal().APP_NAME, "You have unsaved edits to the document that's open.\\n\\n"
                                 "Open a project and discard them?"):
            return
        path = filedialog.askopenfilename(
            title="Open a PDF Studio project", initialdir=self.last_dir or None,
            filetypes=[("AAC Tag Studio project", "*.aacproj"),
                       ("All files", "*.*")])
        if not path:
            return
        self.last_dir = str(Path(path).parent)
        try:
            import json
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as ex:
            self.flash_status(f"Couldn't open that project: {ex}", ok=False)
            return
        if not isinstance(data, dict) or data.get("kind") != "pdf-project":
            self.flash_status("That file isn't a PDF Studio project", ok=False)
            return

        def work():
            sess = Session()
            sid_by_path = {}
            missing = []
            pages = []
            for pd in data.get("pages", []):
                src, idx = pd.get("src"), pd.get("idx")
                if src is not None and src >= 0:
                    sp = pd.get("src_path")
                    if sp and sp not in sid_by_path:
                        try:
                            sid_by_path[sp] = sess.load_source(sp)[0]
                        except Exception:
                            sid_by_path[sp] = None
                            missing.append(Path(sp).name if sp else "?")
                    sid = sid_by_path.get(sp)
                    if sid is None:
                        # source moved: keep the objects, lose the background
                        e = PageEntry(-1, -1, float(pd.get("w", 612.0)),
                                      float(pd.get("h", 792.0)), int(pd.get("rot", 0)))
                    else:
                        e = PageEntry(sid, int(idx), float(pd.get("w", 612.0)),
                                      float(pd.get("h", 792.0)), int(pd.get("rot", 0)))
                elif src == CARD_SRC:
                    e = PageEntry(CARD_SRC, int(idx), float(pd.get("w", 612.0)),
                                  float(pd.get("h", 792.0)), int(pd.get("rot", 0)),
                                  [], copy.deepcopy(pd.get("card")))
                else:
                    e = PageEntry(-1, int(idx) if isinstance(idx, int) else -1,
                                  float(pd.get("w", 612.0)), float(pd.get("h", 792.0)),
                                  int(pd.get("rot", 0)))
                e.whiteouts = [tuple(w) for w in (pd.get("whiteouts") or [])]
                for idd in (pd.get("items") or []):
                    img = None
                    if idd.get("img"):
                        try:
                            img = Image.open(io.BytesIO(
                                base64.b64decode(idd["img"]))).convert("RGBA")
                        except Exception:
                            img = None
                    e.items.append(Item(
                        idd.get("kind", "rect"), float(idd.get("x0", 0)),
                        float(idd.get("y0", 0)), float(idd.get("x1", 8)),
                        float(idd.get("y1", 8)), rot=int(idd.get("rot", 0)),
                        word=idd.get("word", ""), text=idd.get("text", ""),
                        size=float(idd.get("size", 14.0)), color=idd.get("color", "#FFFFFF"),
                        label=bool(idd.get("label", True)),
                        opacity=float(idd.get("opacity", 1.0)), img=img,
                        fill=idd.get("fill"), lw=float(idd.get("lw", 1.5)),
                        points=[tuple(p) for p in idd["points"]] if idd.get("points") else None,
                        closed=bool(idd.get("closed", False)),
                        border=bool(idd.get("border", False)),
                        font=idd.get("font", "Helvetica"),
                        bold=bool(idd.get("bold", False)),
                        italic=bool(idd.get("italic", False)),
                        align=idd.get("align", "l"),
                        crop=tuple(idd["crop"]) if idd.get("crop") else None,
                        name=idd.get("name", ""), hidden=bool(idd.get("hidden", False)),
                        locked=bool(idd.get("locked", False)),
                        gid=int(idd.get("gid", 0))))
                pages.append(e)
            sess.pages = pages
            return sess, missing

        def done(res):
            sess, missing = res
            if not sess.pages:
                self.flash_status("That project has no pages in it", ok=False)
                return
            # fresh ids for card + blank pages BEFORE the session starts
            # (the saved ones belong to the old run's counters and could
            # collide with new pages; the card renders key on the new ids)
            for e in sess.pages:
                if e.src == CARD_SRC:
                    e.idx = -next(self._ped_card_seq)
                elif e.src == -1:
                    bid = -next(sess._blank_seq)
                    sess._blank_dims[bid] = (e.w, e.h)
                    e.idx = bid
            self._ped_start(sess)
            self._ped_project_path = path
            try:
                self._ped_cur = max(0, min(int(data.get("cur", 0)), len(sess.pages) - 1))
                self._ped_zoom = float(data.get("zoom", 1.0))
            except Exception:
                pass
            self._ped_draw()
            self._ped_strip_draw()
            for e in sess.pages:
                for it in e.items:
                    if it.kind == "card" and it.word and it.img is None:
                        self._ped_fetch_picture(it)
            msg = f"Opened project — {len(sess.pages)} page(s)"
            if missing:
                msg += f" (backgrounds missing for: {', '.join(sorted(set(missing)))} — those pages kept your objects on blank pages)"
            self.flash_status(msg)
        self.bg("Opening project…", work, done)

    def _ped_save(self, only_current=False, extract_all=False):
        s = self._ped
        if s is None or not s.pages:
            return
        if self.busy:
            self.flash_status("Still working on the last export — one moment…", ok=False)
            return
        stem = Path(s.title).stem or "document"
        if only_current:
            title, default = "Extract page to PDF", f"{stem}_page{self._ped_cur + 1}"
            pages = [s.pages[self._ped_cur].clone()]
        elif extract_all:
            title, default, pages = "Extract all pages to PDF", f"{stem}_extract", s.snapshot()
        else:
            title, default, pages = "Save edited PDF", f"{stem}_edited", s.snapshot()
        r = self._export_dialog(title, default, "pdf", lambda n: [f"{n}.pdf"],
                                name_label="PDF file name", ok_text="Save PDF")
        if not r:
            return
        folder, nm = r
        out = folder / f"{nm}.pdf"
        prog = self.progress_cb()

        def work():
            try:
                return s.build(pages, out, progress=prog)
            except PdfEditError as ex:
                return str(ex)

        def done(res):
            if isinstance(res, str):
                self.flash_status(res, ok=False, hold=True)
                messagebox.showwarning(_pal().APP_NAME, res)
                return
            if not only_current and not extract_all and s is self._ped:
                s.dirty = False
            self.last_dir = str(Path(res["path"]).parent)
            self.last_export = Path(res["path"])
            self.flash_status(f"Saved {Path(res['path']).name} ({res['pages']} page"
                              f"{'' if res['pages'] == 1 else 's'})", hold=True)
            self.reveal_btn.grid()
        self.bg("Saving PDF…", work, done, lock=True, determinate=True)
