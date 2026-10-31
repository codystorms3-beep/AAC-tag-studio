"""Board import (v14.9): read a communication board from a PDF or a picture.

A "board" here is a grid of cells, each with a picture and (usually) a word under it -
the layout most AAC boards use. This module finds the grid, cuts out every cell's
picture, reads the word, and notes the cell's tint, so the app can put the words,
pictures and layout back together (Word list, Library, PDF Studio cards).

How the pieces are found
  * PDF with a text layer (PyMuPDF): the words come straight from the text, with exact
    positions, and the page is rendered to a picture at 200 dpi for the cut-outs.
  * Picture / scanned PDF: the grid is found from the cell borders; the word is read with
    OCR when an OCR engine is installed (rapidocr-onnxruntime or Tesseract), and otherwise
    left blank for you to type in the review window.
  * If no grid lines can be found you give the number of columns and rows and the page is
    cut evenly.

Pure Python + numpy + Pillow (+ OpenCV is not required). PyMuPDF is needed only for PDFs.
"""
from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

log = logging.getLogger("aac")

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tif", ".tiff")
PDF_DPI = 200
MAX_PAGES = 40


class BoardError(Exception):
    """A problem with plain-English text that can be shown to the user as is."""


@dataclass
class Cell:
    page: int
    row: int
    col: int
    box: tuple                      # (x0, y0, x1, y1) on the page image
    word: str = ""
    image: Image.Image | None = None    # the picture cut out of the cell (None = no picture)
    tint: str | None = None         # "#rrggbb" when the cell has a coloured background
    word_source: str = ""           # "text" (PDF text layer), "ocr", or "" (you type it)

    @property
    def blank(self):
        return not self.word.strip() and self.image is None


@dataclass
class BoardPage:
    index: int
    cols: int
    rows: int
    size: tuple
    cells: list = field(default_factory=list)
    grid_found: bool = True


# ------------------------------------------------------------------ reading files
def _import_fitz():
    try:
        import pymupdf as fitz          # PyMuPDF >= 1.24
        return fitz
    except Exception:
        pass
    try:
        import fitz                     # older PyMuPDF
        return fitz
    except Exception:
        return None


def pdf_supported():
    return _import_fitz() is not None


def read_pdf_pages(path, dpi=PDF_DPI, max_pages=MAX_PAGES, progress=None):
    """-> [(PIL.Image RGB, [(x0, y0, x1, y1, text), ...] in image pixels), ...]"""
    fitz = _import_fitz()
    if fitz is None:
        raise BoardError(
            "Reading PDFs needs the PyMuPDF package, which isn't installed.\n\n"
            "Install it once (Command Prompt):   pip install pymupdf\n"
            "then restart the app.  (You can also import a picture of the board - "
            "a screenshot or a photo - without it.)")
    out = []
    try:
        doc = fitz.open(str(path))
    except Exception as e:
        raise BoardError(f"Couldn't open that PDF: {e}") from e
    try:
        if getattr(doc, "needs_pass", False):
            raise BoardError("That PDF is password-protected.")
        n = min(len(doc), max_pages)
        zoom = dpi / 72.0
        for i in range(n):
            if progress:
                progress(f"Reading PDF page {i + 1} of {n}…")
            page = doc[i]
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            words = []
            try:
                for w in page.get_text("words"):
                    x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], str(w[4])
                    if text.strip():
                        words.append((x0 * zoom, y0 * zoom, x1 * zoom, y1 * zoom, text.strip()))
            except Exception:
                words = []
            out.append((img, words))
    finally:
        try:
            doc.close()
        except Exception:
            pass
    if not out:
        raise BoardError("That PDF has no pages.")
    return out


def read_image_page(path):
    try:
        img = Image.open(str(path))
        img.load()
    except Exception as e:
        raise BoardError(f"Couldn't open that picture: {e}") from e
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        bg.alpha_composite(img)
        img = bg
    return [(img.convert("RGB"), [])]


# ------------------------------------------------------------------ grid detection
def _clusters(idx, gap=2):
    """Group consecutive indexes -> list of (centre, size)."""
    out, cur = [], []
    for i in idx:
        if cur and i - cur[-1] > gap:
            out.append((float(np.mean(cur)), len(cur)))
            cur = []
        cur.append(int(i))
    if cur:
        out.append((float(np.mean(cur)), len(cur)))
    return out


def _fit_lattice(cands, length, min_step):
    """Find evenly spaced lines among the candidates (cell borders are evenly spaced; the
    dark parts of pictures that look like lines are not). Returns the list of line
    positions or None."""
    cands = sorted(cands)
    if len(cands) < 3:
        return None
    best = None
    for i, a in enumerate(cands):
        for b in cands[i + 1:]:
            d = b - a
            for k in range(1, 13):
                p = d / k
                if p < min_step or p > length * 0.75:
                    continue
                tol = max(2.5, p * 0.05)
                matched = []
                j_lo = int(np.floor(-a / p)) - 1
                j_hi = int(np.ceil((length - a) / p)) + 1
                pts = []
                for j in range(j_lo, j_hi + 1):
                    x = a + j * p
                    if -tol <= x <= length + tol:
                        pts.append((j, x))
                for j, x in pts:
                    if any(abs(x - c) <= tol for c in cands):
                        matched.append(j)
                if len(matched) < 3:
                    continue
                span = matched[-1] - matched[0] + 1
                if len(matched) / span < 0.8:
                    continue
                score = (len(matched), p)
                if best is None or score > best[0]:
                    best = (score, a, p, matched[0], matched[-1])
    if best is None:
        return None
    _score, a, p, j0, j1 = best
    # least-squares polish using the candidates that sit on the lattice
    js, xs = [], []
    for j in range(j0, j1 + 1):
        x = a + j * p
        near = [c for c in cands if abs(c - x) <= max(2.5, p * 0.05)]
        if near:
            js.append(j)
            xs.append(min(near, key=lambda c: abs(c - x)))
    if len(js) >= 2:
        slope, icpt = np.polyfit(js, xs, 1)
        return [float(icpt + slope * j) for j in range(j0, j1 + 1)]
    return [float(a + j * p) for j in range(j0, j1 + 1)]


def detect_grid(img):
    """Find the cell borders. Returns (xs, ys) - the x positions of the vertical lines and
    the y positions of the horizontal lines - or None when there is no regular grid."""
    g = np.asarray(img.convert("L"))
    h, w = g.shape
    best = None
    # Cell borders run the whole length of the grid, so they are the strongest lines; the
    # dark parts of pictures only cover part of it. Try strict first, then looser.
    for thr, frac in ((200, 0.8), (225, 0.8), (170, 0.8), (200, 0.65), (225, 0.65), (240, 0.65)):
        mask = g < thr
        col = mask.mean(axis=0)
        row = mask.mean(axis=1)
        cx = [c for c, n in _clusters(np.where(col > frac)[0]) if n <= max(8, int(w * 0.03))]
        cy = [c for c, n in _clusters(np.where(row > frac)[0]) if n <= max(8, int(h * 0.03))]
        xs = _fit_lattice(cx, w, min_step=max(12, w / 40))
        ys = _fit_lattice(cy, h, min_step=max(12, h / 40))
        if xs and ys and len(xs) >= 2 and len(ys) >= 2:
            score = (len(xs) - 1) * (len(ys) - 1)
            if best is None or score > best[0]:
                best = (score, xs, ys)
            if score >= 4:
                break
    if best is None:
        return None
    return best[1], best[2]


def even_grid(img, cols, rows):
    """Cut the page's content area evenly into cols x rows (when no borders were found)."""
    g = np.asarray(img.convert("L"))
    ink = g < 235
    ys, xs = np.where(ink)
    if len(xs) == 0:
        raise BoardError("The page looks empty.")
    x0, x1 = int(np.percentile(xs, 0.2)), int(np.percentile(xs, 99.8)) + 1
    y0, y1 = int(np.percentile(ys, 0.2)), int(np.percentile(ys, 99.8)) + 1
    xs_l = [x0 + (x1 - x0) * i / cols for i in range(cols + 1)]
    ys_l = [y0 + (y1 - y0) * j / rows for j in range(rows + 1)]
    return xs_l, ys_l


# ------------------------------------------------------------------ OCR (optional)
_OCR = {"engine": None, "tried": False}


def ocr_available():
    _init_ocr()
    return _OCR["engine"] is not None


def _init_ocr():
    if _OCR["tried"]:
        return
    _OCR["tried"] = True
    try:
        from rapidocr_onnxruntime import RapidOCR      # pip install rapidocr-onnxruntime
        eng = RapidOCR()

        def run(img):
            res, _ = eng(np.asarray(img.convert("RGB"))[:, :, ::-1])
            if not res:
                return ""
            res = sorted(res, key=lambda r: (round(r[0][0][1] / 12), r[0][0][0]))
            return " ".join(str(r[1]) for r in res)
        _OCR["engine"] = run
        log.info("Board import: OCR via rapidocr")
        return
    except Exception:
        pass
    try:
        import pytesseract
        if shutil.which("tesseract") or Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe").exists():
            if not shutil.which("tesseract"):
                pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

            def run(img):
                return pytesseract.image_to_string(img, config="--psm 7")
            _OCR["engine"] = run
            log.info("Board import: OCR via Tesseract")
    except Exception:
        pass


def clean_word(text):
    t = re.sub(r"[^A-Za-z0-9' \-]", " ", str(text or ""))
    t = re.sub(r"\s+", " ", t).strip(" -'")
    if not t:
        return ""
    return t if t == "I" else t.lower()


def _ocr_label(band):
    _init_ocr()
    if _OCR["engine"] is None or band.width < 6 or band.height < 6:
        return ""
    try:
        scale = max(1.0, 64.0 / band.height)
        big = band.resize((int(band.width * scale), int(band.height * scale)), Image.LANCZOS)
        pad = Image.new("RGB", (big.width + 40, big.height + 40), (255, 255, 255))
        pad.paste(big, (20, 20))
        return clean_word(_OCR["engine"](pad))
    except Exception as e:
        log.debug("OCR failed: %s", e)
        return ""


# ------------------------------------------------------------------ cell splitting
def _tint_of(arr):
    """Background colour of a cell from its four inner corners; '#rrggbb' when it is not
    (near-)white, else None."""
    h, w = arr.shape[:2]
    k = max(2, int(min(h, w) * 0.06))
    corners = np.concatenate([arr[:k, :k].reshape(-1, 3), arr[:k, -k:].reshape(-1, 3),
                              arr[-k:, :k].reshape(-1, 3), arr[-k:, -k:].reshape(-1, 3)])
    med = np.median(corners, axis=0)
    if med.min() >= 240:
        return None, med
    return "#%02x%02x%02x" % tuple(int(v) for v in med), med


def _label_band(arr, bg):
    """Find the text band at the bottom (or top) of a cell picture: returns (y0, y1, where)
    or None. The band is a short run of ink rows separated from the picture by a blank gap."""
    h, w = arr.shape[:2]
    ink = (np.abs(arr.astype(np.int16) - bg).sum(axis=2) > 90)
    prof = ink.sum(axis=1)
    thresh = max(1, int(w * 0.01))
    rows = prof > thresh

    def bands(mask):
        out, start = [], None
        for y, v in enumerate(mask):
            if v and start is None:
                start = y
            elif not v and start is not None:
                out.append([start, y - 1])
                start = None
        if start is not None:
            out.append([start, len(mask) - 1])
        # glue bands split by a 1-2 row gap (letters with dots/descenders)
        merged = []
        for b in out:
            if merged and b[0] - merged[-1][1] <= 2:
                merged[-1][1] = b[1]
            else:
                merged.append(b)
        return merged

    bl = bands(rows)
    if len(bl) < 2:
        return None
    last, prev = bl[-1], bl[-2]
    gap = last[0] - prev[1]
    height = last[1] - last[0] + 1
    if gap >= 1 and height <= h * 0.36 and last[0] > h * 0.45:
        # a label can wrap to two lines: take the previous band too if it is text-sized
        if len(bl) >= 3:
            p2 = bl[-3]
            h2 = prev[1] - prev[0] + 1
            if h2 <= height * 1.6 and (prev[0] - p2[1]) >= 1 and prev[0] > h * 0.45:
                return prev[0], last[1], "bottom"
        return last[0], last[1], "bottom"
    return None


def _crop_picture(arr, bg, tinted):
    """Trim blank margin around the picture and return a square RGB image, or None when
    there is (almost) nothing in the region."""
    diff = np.abs(arr.astype(np.int16) - bg).sum(axis=2)
    mask = diff > 60
    # a leftover piece of the cell border shows up as a full-height / full-width line at an edge
    hh, ww = mask.shape
    for _ in range(3):
        if ww > 8 and mask[:, 0].mean() > 0.8:
            mask[:, 0] = False
            arr = arr.copy(); arr[:, 0] = bg
        if ww > 8 and mask[:, -1].mean() > 0.8:
            mask[:, -1] = False
            arr = arr.copy(); arr[:, -1] = bg
        if hh > 8 and mask[0, :].mean() > 0.8:
            mask[0, :] = False
            arr = arr.copy(); arr[0, :] = bg
        if hh > 8 and mask[-1, :].mean() > 0.8:
            mask[-1, :] = False
            arr = arr.copy(); arr[-1, :] = bg
    if mask.mean() < 0.004:
        return None
    ys, xs = np.where(mask)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    crop = arr[y0:y1, x0:x1].copy()
    if tinted:                                   # the tile colour is kept separately
        m = np.abs(crop.astype(np.int16) - bg).sum(axis=2) <= 40
        crop[m] = 255
    side = int(max(crop.shape[:2]) * 1.12)
    side = max(side, 24)
    canvas = np.full((side, side, 3), 255, dtype=np.uint8)
    oy, ox = (side - crop.shape[0]) // 2, (side - crop.shape[1]) // 2
    canvas[oy:oy + crop.shape[0], ox:ox + crop.shape[1]] = crop
    return Image.fromarray(canvas)


def build_page(img, xs, ys, words_px, index=0, use_ocr=True, grid_found=True):
    """Cut one page image into cells using the grid lines xs / ys."""
    arr = np.asarray(img.convert("RGB"))
    cols, rows = len(xs) - 1, len(ys) - 1
    page = BoardPage(index=index, cols=cols, rows=rows, size=img.size, grid_found=grid_found)
    for r in range(rows):
        for c in range(cols):
            x0, x1 = int(round(xs[c])), int(round(xs[c + 1]))
            y0, y1 = int(round(ys[r])), int(round(ys[r + 1]))
            cw, ch = x1 - x0, y1 - y0
            ins = max(3, int(min(cw, ch) * 0.07))
            ix0, ix1, iy0, iy1 = x0 + ins, x1 - ins, y0 + ins, y1 - ins
            cell = Cell(page=index, row=r, col=c, box=(x0, y0, x1, y1))
            if ix1 - ix0 < 8 or iy1 - iy0 < 8:
                page.cells.append(cell)
                continue
            sub = arr[iy0:iy1, ix0:ix1]
            tint, bg = _tint_of(sub)
            cell.tint = tint
            bgv = bg.astype(np.int16)
            # --- the word
            label_top = None
            tw = [w for w in words_px
                  if ix0 - 2 <= (w[0] + w[2]) / 2 <= ix1 + 2 and iy0 - 2 <= (w[1] + w[3]) / 2 <= iy1 + 2]
            pic_y0, pic_y1 = 0, sub.shape[0]
            if tw:
                lines = sorted(tw, key=lambda w: (round(((w[1] + w[3]) / 2) / max(6, (w[3] - w[1]) * 0.8)), w[0]))
                cell.word = clean_word(" ".join(w[4] for w in lines))
                cell.word_source = "text" if cell.word else ""
                ty0 = min(w[1] for w in tw) - iy0
                ty1 = max(w[3] for w in tw) - iy0
                if (ty0 + ty1) / 2 >= sub.shape[0] * 0.5:
                    pic_y1 = max(4, int(ty0) - 1)
                else:
                    pic_y0 = min(sub.shape[0] - 4, int(ty1) + 1)
            else:
                band = _label_band(sub, bgv)
                if band is not None:
                    by0, by1, _where = band
                    pic_y1 = max(4, by0 - 1)
                    if use_ocr:
                        pad = 2
                        lab = img.crop((ix0, iy0 + max(0, by0 - pad), ix1, iy0 + min(sub.shape[0], by1 + 1 + pad)))
                        cell.word = _ocr_label(lab)
                        cell.word_source = "ocr" if cell.word else ""
            # --- the picture
            region = sub[pic_y0:pic_y1]
            if region.shape[0] >= 6:
                cell.image = _crop_picture(region, bgv, bool(tint))
            page.cells.append(cell)
    return page


# ------------------------------------------------------------------ top level
def read_board(path, cols=None, rows=None, progress=None, use_ocr=True):
    """Read a board file (PDF or picture). Returns a list of BoardPage (one per PDF page).

    cols / rows: when given, the page is cut evenly into that many cells instead of looking
    for border lines."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        pages_in = read_pdf_pages(path, progress=progress)
    elif ext in IMAGE_EXTS:
        pages_in = read_image_page(path)
    else:
        raise BoardError("Choose a PDF or a picture (PNG, JPG, BMP, WEBP).")
    pages = []
    for i, (img, words) in enumerate(pages_in):
        if progress:
            progress(f"Finding the cells on page {i + 1}…")
        grid_found = True
        if cols and rows:
            xs, ys = even_grid(img, int(cols), int(rows))
            grid_found = False
        else:
            g = detect_grid(img)
            if g is None:
                # a page with no regular grid (title page, notes) is skipped when other pages work
                log.info("Board import: no grid on page %d", i + 1)
                continue
            xs, ys = g
        if progress:
            progress(f"Reading {(len(xs) - 1) * (len(ys) - 1)} cells on page {i + 1}…")
        pages.append(build_page(img, xs, ys, words, index=len(pages), use_ocr=use_ocr, grid_found=grid_found))
    if not pages:
        raise BoardError(
            "I couldn't find a grid of cells in that file.\n\n"
            "If the board has no cell borders, tell me how many columns and rows it has "
            "(Columns / Rows boxes in the import window) and I'll cut it evenly.")
    return pages


def hex_to_tile(tint):
    """The tint as the app stores tile colours ('#rrggbb') - lightened a little so printed
    cards stay readable."""
    if not tint:
        return None
    r, g, b = (int(tint[i:i + 2], 16) for i in (1, 3, 5))
    r, g, b = (int(v + (255 - v) * 0.35) for v in (r, g, b))
    return "#%02x%02x%02x" % (r, g, b)
