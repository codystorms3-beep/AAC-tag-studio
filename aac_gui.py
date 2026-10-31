"""AAC Tag Studio 3.0 - GUI: three tabs on the left, big preview, ONE "Export file" button, AI tab."""
import io
import json
import os
import queue
import shutil
import subprocess
import sys
import re
import tempfile
import threading
import time
import tkinter as tk
from collections import OrderedDict
from dataclasses import asdict, fields, replace as dc_replace
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox

import customtkinter as ctk

import aac_ai as ai
import aac_style
import aac_3dview
import aac_glview
import aac_live3d
import aac_boards
import aac_core as core
import aac_diag
import aac_import_ui
import aac_pdfedit
from aac_common import (APP_NAME, APP_VERSION, DATA_DIR, ISSUES, LOG_DIR, LOG_FILE, OUT_DIR, SETTINGS_FILE, log,
                        tail_log)

BATCH_FORMATS = ["3MF (colours)", "STL (base + ink)", "Both"]
# v15.52: printer bed presets (mm, cubic X × Y × Z) for the Maker
# preview. Cody's bed is 220³; the preview arranges tags inside the
# chosen bed and flags any tag that doesn't fit.
BED_SIZES = ["180 × 180 × 180", "220 × 220 × 220", "256 × 256 × 256",
             "300 × 300 × 300", "350 × 350 × 350"]
AI_TARGETS = ["Word list only", "PDF Studio cards", "Maker Studio tags"]
_AI_TARGET_KEY = {AI_TARGETS[0]: "list", AI_TARGETS[1]: "pdf", AI_TARGETS[2]: "maker"}
# v13.29: brighter, friendlier palette for special-needs / AAC vibe —
# higher saturation, clear contrast, playful but still readable.
ACCENT = "#E88CA0"          # pastel blush — primary actions, selection
ACCENT_HOVER = "#F0A8B8"
TEAL = "#6ECFAE"            # pastel mint — secondary / success-ish
TEAL_HOVER = "#8ADDC0"
SUN = "#F5D67A"             # pastel butter — sunny highlight
AI_COLOR = "#A98BD6"        # pastel lilac for AI
AI_HOVER = "#BFA3E2"
AI_TAB = "AI (Local)"
# Soft backgrounds that still pop
PREVIEW_BG_DARK = "#1a1d24"
PREVIEW_BG_LIGHT = "#f0f4f8"

# One-click design presets for the Tag Design tab (simple mode).
# Keys are shown in the dropdown; values are partial Opts overrides + optional size_preset name.
DESIGN_PRESETS = {
    "Custom…": {},
    "Standard keyring (40 mm)": {
        "shape": "Circle", "size": 40.0, "hole": "Top loop", "style": "Flat inlay",
        "layout": "Icon + word", "ring": True, "thickness": 1.6, "design_depth": 0.6,
        "_size_preset": "Standard (40 mm)",
    },
    "Small keyring (30 mm)": {
        "shape": "Circle", "size": 30.0, "hole": "Top loop", "style": "Flat inlay",
        "layout": "Icon + word", "ring": True, "thickness": 1.6, "design_depth": 0.6,
        "_size_preset": "Keyring (30 mm)",
    },
    "Badge — through hole": {
        "shape": "Circle", "size": 40.0, "hole": "Through (top)", "style": "Flat inlay",
        "layout": "Icon + word", "ring": True, "thickness": 1.6, "design_depth": 0.6,
        "_size_preset": "Standard (40 mm)",
    },
    "Solid badge (no hole)": {
        "shape": "Circle", "size": 40.0, "hole": "None", "style": "Flat inlay",
        "layout": "Icon + word", "ring": True, "thickness": 1.6, "design_depth": 0.6,
        "_size_preset": "Standard (40 mm)",
    },
    "Raised icon keyring": {
        "shape": "Circle", "size": 40.0, "hole": "Top loop", "style": "Raised",
        "layout": "Icon + word", "ring": True, "raise_h": 0.8, "thickness": 1.8,
        "_size_preset": "Standard (40 mm)",
    },
    "Magnet tile (no loop)": {
        "shape": "Rounded square", "size": 40.0, "hole": "None", "style": "Flat inlay",
        "layout": "Icon + word", "ring": False, "magnet_recess": True, "thickness": 2.0,
        "_size_preset": "Standard (40 mm)",
    },
}


# ============================================================ helpers
def open_folder(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        log.warning("Could not open folder %s: %s", path, e)


def reveal(path):
    """Open the folder with the exported file selected (Windows), else just open the folder."""
    p = Path(path)
    try:
        if sys.platform.startswith("win") and p.exists() and p.is_file():
            subprocess.Popen(f'explorer /select,"{p}"')
            return
    except Exception as e:
        log.warning("Could not reveal %s: %s", p, e)
    open_folder(p if p.is_dir() else p.parent)


def load_settings():
    try:
        d = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        d = {}
    except Exception as e:
        log.warning("Settings file unreadable (%s) - using defaults", e)
        d = {}
    names = {f.name for f in fields(core.Opts)}
    o = core.Opts(**{k: v for k, v in d.get("opts", {}).items() if k in names})
    return o, d


def build_spec(fonts):
    """What the AI is allowed to change (also used to validate everything the AI sends back)."""
    return {
        "shape": ("choice", core.SHAPES), "size": ("float", 25, 70), "thickness": ("float", 0.8, 4),
        "style": ("choice", core.STYLES), "raise_h": ("float", 0.2, 2), "hole": ("choice", core.HOLES),
        "hole_d": ("float", 2, 8), "ring": ("bool",), "ring_w": ("float", 0.4, 2),
        "layout": ("choice", core.LAYOUTS), "icon_scale": ("float", 0.5, 1.3), "icon_bold": ("float", -0.3, 1.0),
        "font": ("choice", fonts), "text_case": ("choice", core.CASES), "text_scale": ("float", 0.5, 1.3),
        "body_color": ("color",), "ink_color": ("color",), "ink2_color": ("color",),
        "ink_layers": ("float", 1, 2), "connectors": ("bool",),
        "magnet_recess": ("bool",), "high_contrast": ("bool",), "category_colors": ("bool",),
    }


# ============================================================ app
class App(aac_pdfedit.PdfEditMixin, aac_import_ui.BoardImportMixin, ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        # Fixed preferred size — don't let tab/content switches keep
        # expanding the window. User can still drag-resize freely.
        self.geometry("1240x800")
        self.minsize(1040, 700)
        self._preferred_geom = "1240x800"
        o, saved = load_settings()
        self.fonts = core.available_fonts()
        if o.font not in self.fonts:
            o.font = self.fonts[0]
        self.vars = {}
        for f in fields(core.Opts):  # variable type comes from the field default, never from saved JSON
            d = f.default
            cls = tk.BooleanVar if isinstance(d, bool) else tk.DoubleVar if isinstance(d, float) else tk.StringVar
            try:
                val = getattr(o, f.name)
            except Exception:
                val = d
            # Back-compat: old hole values "Top" / "Left" → "Top loop" / "Left loop"
            if f.name == "hole" and val in ("Top", "Left"):
                val = f"{val} loop"
            try:
                var = cls(value=val)
            except Exception:
                var = cls(value=d)
            self.vars[f.name] = var
            var.trace_add("write", lambda *_: self.changed())
        self.spec = build_spec(self.fonts)
        self.cat_colors = tk.BooleanVar(value=saved.get("cat_colors", True))
        self.cat_colors.trace_add("write", lambda *_: self.changed())
        self.batch_fmt = tk.StringVar(value=saved.get("batch_fmt", BATCH_FORMATS[0]))
        # v15.52: multiple tags are always on now (the switch is gone) —
        # an old saved False is ignored, the preview always shows the
        # whole preview-page set.
        self.maker_multi = tk.BooleanVar(value=True)
        # v15.52: the "How many tags" menu is gone too — the preview
        # always shows every word on the preview page.
        self.maker_count_var = tk.StringVar(value="All")
        # v15.52: printer bed size for the Maker preview (Settings →
        # 3D printing). Cubic presets in mm; the preview arranges the
        # tags inside this bed and flags any that don't fit.
        _bed = str(saved.get("bed_size", "220 × 220 × 220"))
        if _bed not in BED_SIZES:
            _bed = "220 × 220 × 220"
        self.bed_size = tk.StringVar(value=_bed)
        # v14.8: where the AI puts NEW words (it only ever adds - never replaces)
        self.ai_target_var = tk.StringVar(value=saved.get("ai_target", AI_TARGETS[0]))
        self.ai_pics_var = tk.BooleanVar(value=bool(saved.get("ai_pics", False)))
        _sty = saved.get("ai_style", ai.AAC_STYLE_DEFAULT)
        _sty_values = list(ai.SD_STYLES) + [ai.AAC_STYLE_NAME]
        self.ai_style_var = tk.StringVar(value=_sty if _sty in _sty_values else ai.AAC_STYLE_DEFAULT)
        # v15.24: the illustration style prompts live in aac_style.py and are
        # persisted here; the Advanced section of the AI Settings panel edits them.
        try:
            aac_style.set_config(aac_style.StyleConfig.from_dict({
                "style_prompt": saved.get("aac_style_prompt"),
                "negative_prompt": saved.get("aac_negative_prompt"),
                "subject_mode": saved.get("aac_subject_mode")}))
        except Exception:
            pass
        # v13.29: per-tag design overrides (shape/style/etc for one word only)
        # and which multi-tag is currently selected for editing
        self.design_overrides = dict(saved.get("design_overrides") or {})
        self._selected_multi = None   # word string or None
        self._multi_hit = []          # [(word, x0, y0, x1, y1), ...] for click detection
        self._opts_snapshot = None    # last collect() for detecting which field changed
        # v13.32: locked working set for the Maker preview — list dropdown
        # only changes the tiles you pick FROM; the preview stays until you
        # put a word on / take one away yourself.
        # v14: restore the locked Maker preview set saved last session —
        # permanent means it survives restarts too, not just list flips.
        _saved_ww = saved.get("maker_work_words")
        self._maker_work_words = ([str(w) for w in _saved_ww]
                                  if isinstance(_saved_ww, list) else None)
        # v13.10-exp: auto-save export locations (Settings → Advanced)
        self.auto_export = tk.BooleanVar(value=saved.get("auto_export", False))
        self.export_dir_pdf = tk.StringVar(value=saved.get("export_dir_pdf", ""))
        self.export_dir_maker = tk.StringVar(value=saved.get("export_dir_maker", ""))
        self.export_dir_board = tk.StringVar(value=saved.get("export_dir_board", ""))
        self.size_preset = tk.StringVar(value="Standard (40 mm)")
        self.preview_word = tk.StringVar(value="")
        self._preview_word_explicit = False  # v15.70: True only when the USER picks a word (never the Maker auto-fill)
        self.search_as_var = tk.StringVar(value="")
        self.sel_label = tk.StringVar(value="")
        self.pecs_layout = tk.StringVar(value="8 per page (2×4)")
        self.pecs_labels = tk.BooleanVar(value=True)
        self.last_dir = saved.get("last_dir", str(OUT_DIR))
        self.overrides, self.variants = {}, {}
        self._icon_paths = {}  # word -> last successful local icon path (keeps pictures when size/opts change)
        self.search_as = dict(saved.get("search_as", {}))
        # v3.6.0-exp: boards + saved lists
        self.strip_words = list(saved.get("strip_words", []))
        self.word_lists = dict(saved.get("word_lists", {}))
        self.core_size_var = tk.StringVar(value="36 core words (6\u00d76)")
        self.core_source_var = tk.StringVar(value="Core 40 list")
        self.token_count_var = tk.StringVar(value="5")
        self.token_reward_var = tk.StringVar(value="")
        self.about_name_var = tk.StringVar(value="")
        self.about_photo_var = tk.StringVar(value="")
        self.about_groups = {h: tk.StringVar() for h in ("I want", "I feel", "I need", "I like")}
        self.my_lists_var = tk.StringVar(value="My saved lists\u2026")
        self._about_photo_path = ""
        aac_boards.AI_CATEGORIES.update(saved.get("ai_categories", {}))
        # v15.64: drop remembered categories that only ever came from the
        # old heuristic guess and are now contradicted by the built-in
        # table (tiger as People, duckling as Actions…); user overrides
        # that differ from the heuristic are kept untouched.
        try:
            aac_boards.prune_stale_ai_categories()
        except Exception:
            pass
        self.q = queue.Queue()
        self.gen = 0
        self.pending = None
        self.geo = None
        self.busy = False
        self.logwin = self.logbox = None
        self.last_export = None
        self._anim_scale, self._anim_after, self._flash_after = 1.0, None, None
        self._pdf_preview_after = None
        # v3.7.0-exp: document-view state (async pictures + click selection)
        self._pdf_selected = None
        self._pdf_selected_set = set()   # v15.3: multi-select (Ctrl+click)
        self._pdf_card_geom = []
        # v14.3: blank card slots in the PDF preview (right-click to fill)
        self._pdf_blank_geom = []        # [(slot_index, x, y, side)]
        self._pdf_blank_selected = None  # slot index of the highlighted blank
        self._canvas_redraw_after = None  # v14.3: coalesced <Configure> redraw
        self._canvas_last_size = None
        self._lib_fp = None              # v14.3: library data fingerprint
        self._pdf_recheck_armed = False
        self._badge_n = None
        self._pdf_icon_cache = {}
        self._pdf_icon_gen = 0
        self._pdf_wanted = set()
        self._pdf_fetch_q = queue.Queue()
        self._pdf_fetch_thread = None
        self._pdf_gallery_gen = 0
        # v13.25: interactive PDF preview — zoom + pan
        self._pdf_zoom = 1.0
        self._pdf_pan = [0.0, 0.0]   # pixel offset after zoom
        self._pdf_pan_drag = None    # {x0, y0, pan0} while panning empty space
        # v15.3: per-card picture scale/position + canvas handles / in-place edit
        self.pdf_pic_scale_map = {}      # word -> float (overrides global slider)
        self.pdf_pic_y_map = {}          # word -> float
        self._pdf_handle_geom = []       # [{word, kind, x, y, r, ...}] for hit-testing
        self._pdf_handle_drag = None     # active handle drag state
        self._pdf_inline_edit = None     # {word, entry, x, y, side} while editing label
        self._maker_gen = 0
        # v13.14: instant word + loading state for the 3D tag preview
        self._loading = None        # {word, gen, msg, t0, now, ang} while a tag is being built
        self._load_after = None
        self._tag_stale = False     # tag preview needs a rebuild when we come back to a tag tab
        # v13.14: library + "which studio am I working in"
        self._last_studio = "3d"    # "3d" (Maker / Tag Design) or "pdf"
        self._lib_open = False
        self._lib_words = []        # [(word, "sources")] after filtering (flat, for selection)
        self._lib_all_words = []
        self._lib_rows = []         # display rows: ('header', title) | ('word', word, sources)
        self._lib_row_geom = []     # layout cache from _lib_row_layout
        self._lib_pics = []         # all scanned pictures
        self._lib_pics_f = []       # filtered pictures (flat, for selection)
        self._lib_pic_rows = []     # display rows for pictures (headers + pics)
        self._lib_pic_geom = []     # layout cache for picture grid
        self._lib_sel = None
        self._lib_thumbs = OrderedDict()
        self._lib_requested = set()
        self._lib_visible = set()
        self._lib_req_q = queue.LifoQueue()
        self._lib_worker = None
        self._lib_render_after = None
        self._lib_cols = 1
        self._lib_wcols = 1
        self._lib_wsel = set()
        self._lib_wanchor = None
        self._lib_user_closed = False
        self._no_auto_pic = set()   # words sent from the Library: no automatic picture lookup
        # v3.9.0-exp: words whose card shows the picture only (the picture already has the word on it)
        self.pdf_no_label = set(saved.get("pdf_no_label", []))
        self._opensymbols_secret_fb = saved.get("opensymbols_secret_fb", "")
        # v15.21: the pastel preview is a LIGHT look, but the app saved dark
        # mode, so 15.20 stayed dark and the pastel hid.  Flip to light
        # once, the first time 15.21 runs; after that the saved choice
        # rules, so toggling dark in Settings still sticks.
        ctk.set_appearance_mode("light" if (saved.get("light") or not saved.get("pastel_theme_applied")) else "dark")
        self.install_ai_hooks()
        self._build(saved)
        # v15.44: the embed used to start only on Maker ENTRY, but a cold
        # start ON Maker never fires the tab callback — so 15.42 kicked
        # it here, from __init__. That died at startup on Cody's machine
        # with 0xC0000409 (native stack overrun, no traceback, no usable
        # log): WebView2's async creation needs a mapped parent window
        # and a running STA message pump, and __init__ has neither — the
        # window has never been shown and mainloop hasn't started. So
        # the kick is deferred to the first <Map> (window actually
        # appearing) with a 5s after() fallback; the tab-entry path is
        # unchanged and still starts it on a later Maker entry.
        try:
            self.bind("<Map>", self._embed_on_first_map, add="+")
        except Exception:
            pass
        try:
            self.after(5000, self._embed_cold_start)
        except Exception:
            pass
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.poll)
        self.words_changed()
        log.info("GUI ready (AI %s)", "on" if ai.cfg.ready else "off")

    def get_opensymbols_secret(self):
        """OpenSymbols API secret: system keyring first, settings-file fallback."""
        try:
            import keyring
            v = keyring.get_password("aac-tag-studio", "opensymbols")
            if v:
                return v
        except Exception:
            pass
        return self._opensymbols_secret_fb or ""

    def set_opensymbols_secret(self, value):
        value = (value or "").strip()
        try:
            import keyring
            if value:
                keyring.set_password("aac-tag-studio", "opensymbols", value)
            else:
                try:
                    keyring.delete_password("aac-tag-studio", "opensymbols")
                except Exception:
                    pass
            self._opensymbols_secret_fb = ""
        except Exception:
            self._opensymbols_secret_fb = value

    def report_callback_exception(self, exc, val, tb):
        log.error("GUI callback error", exc_info=(exc, val, tb))
        messagebox.showerror(APP_NAME, f"Something went wrong:\n{val}\n\nDetails were saved to:\n{LOG_FILE}")

    # ============================================================ layout
    def _build(self, saved):
        words_text = saved.get("words", "now\ndinner\nstop\nlisten\nnext\nplay\nhome")
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        side = ctk.CTkFrame(self, width=380, corner_radius=0)
        self.side = side
        side.grid(row=0, column=0, sticky="nsew")
        side.grid_propagate(False)
        side.configure(width=380)  # keep sidebar width fixed across tab switches
        head = ctk.CTkFrame(side, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 0))
        ctk.CTkLabel(head, text=APP_NAME, font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
        ctk.CTkLabel(head, text=f"  {APP_VERSION}", text_color="gray").pack(side="left", pady=(5, 0))
        self.tabs = ctk.CTkTabview(side, command=self.on_workspace_change)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        for t in ("Maker Studio", "Word Lists", "Tag Design", "PDF Studio", AI_TAB):
            self.tabs.add(t)
        self._build_maker(self.tabs.tab("Maker Studio"))
        self._build_words(self.tabs.tab("Word Lists"), words_text)
        self._build_design(self.tabs.tab("Tag Design"))
        self._build_pdf_studio(self.tabs.tab("PDF Studio"), saved)
        # 13.14: the AI tab's content is built into a wide panel on the
        # right (below, once the main column exists) — see _build_ai.

        main = ctk.CTkFrame(self, fg_color="transparent")
        self.main = main
        main.grid(row=0, column=1, sticky="nsew", padx=16, pady=14)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(main, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew")
        self.word_menu = ctk.CTkOptionMenu(top, values=["-"], variable=self.preview_word, width=170, height=34,
                                           command=lambda _: self.on_preview_word())
        # v14.5: NOT packed at startup - the little bar above the preview
        # only appears in Tag Design (see on_workspace_change). Maker and
        # PDF Studio keep every control in the sidebar instead.
        # v15.50: the word dropdown is no longer packed even in Tag
        # Design (it loaded outside words into the editing preview).
        # v15.51: the Picture button is no longer packed there either
        # (Cody: get rid of it) — neither control is packed anywhere
        # now; both widgets still exist for background updates.
        # v3.11.0-exp: thumbnail dropdown replaces the picture-flip arrows
        self.pic_pick_btn = ctk.CTkButton(top, text="Picture ▾", width=110, height=34,
                                          fg_color="gray30", hover_color="#555",
                                          command=self.open_main_picture_picker)
        # v3.8.0-exp: contextual toolbar for the card selected in the document
        self.pdf_sel_bar = ctk.CTkFrame(top, fg_color="transparent")
        self.pdf_sel_label = ctk.CTkLabel(self.pdf_sel_bar, text="", text_color=ACCENT,
                                          font=ctk.CTkFont(weight="bold"))
        self.pdf_sel_label.pack(side="left", padx=(2, 4))
        self.pdf_pic_btn = ctk.CTkButton(self.pdf_sel_bar, text="Picture 1 \u25be", width=118, height=34,
                                           fg_color="gray30", hover_color="#555",
                                           command=self.open_pdf_picture_picker)
        self.pdf_pic_btn.pack(side="left", padx=2)
        ctk.CTkButton(self.pdf_sel_bar, text="Find pictures…", width=112, height=34, fg_color="#FF6B6B",
                      hover_color="#FF8E8E", command=self.pdf_find_pictures).pack(side="left", padx=(6, 0))
        ctk.CTkButton(self.pdf_sel_bar, text="My image…", width=92, height=34, fg_color="gray30", hover_color="#555",
                      command=self.pdf_choose_image).pack(side="left", padx=(6, 0))
        # v3.9.0-exp: uncheck when the picture already has the word on it
        self.pdf_label_var = tk.BooleanVar(value=True)
        self.pdf_label_check = ctk.CTkCheckBox(self.pdf_sel_bar, text="Label",
                                               variable=self.pdf_label_var,
                                               command=self.pdf_toggle_label)
        self.pdf_label_check.pack(side="left", padx=(8, 0))
        # v3.10.0-exp: sidebar "Selected card" panel mirrors the toolbar
        self.pdf_label_check2.configure(variable=self.pdf_label_var)
        self.log_btn = ctk.CTkButton(top, text="Log", width=70, height=34, fg_color="gray30", hover_color="#555",
                                     command=self.open_log)
        self.log_btn.pack(side="right")
        ctk.CTkButton(top, text="⚙", width=40, height=34, fg_color="gray30", hover_color="#555",
                      font=ctk.CTkFont(size=16), command=self.open_settings).pack(side="right", padx=(0, 8))

        self.canvas = tk.Canvas(main, highlightthickness=0, bd=0)
        self.canvas.grid(row=1, column=0, sticky="nsew", pady=12)
        self.canvas.bind("<Configure>", lambda e: self._schedule_canvas_redraw(e))
        for _ev in ("<Button-3>", "<Button-2>"):      # v13.23: right-click the tag → generate image
            self.canvas.bind(_ev, self._on_canvas_right_click)
        # v15.13: right-drag pans the editor page (armed on empty space)
        self.canvas.bind("<B3-Motion>", self._on_canvas_right_drag)
        self.canvas.bind("<ButtonRelease-3>", self._on_canvas_right_release)
        self.canvas.bind("<Button-1>", self._on_pdf_canvas_click)
        self.canvas.bind("<Double-Button-1>", self._on_pdf_canvas_dblclick)
        # 13.14: drag PDF Studio tiles around to reorder them
        # v13.25: also pan the page when dragging empty space; mouse-wheel zooms
        self.canvas.bind("<B1-Motion>", self._on_pdf_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_pdf_canvas_release)
        self.canvas.bind("<MouseWheel>", self._on_pdf_canvas_wheel)
        self.canvas.bind("<Button-4>", self._on_pdf_canvas_wheel)   # Linux scroll up
        # v15.46: OpenGL 3D preview — a GPU canvas in the same grid cell
        # as the canvas, raised above it on Maker/Tag Design tabs (see
        # on_workspace_change). If OpenGL/pyopengltk is missing, a plain
        # message label sits in the cell instead of a blank preview.
        self._p3d_frame = tk.Frame(main, highlightthickness=0, bd=0)
        self._p3d_frame.grid(row=1, column=0, sticky="nsew", pady=12)
        self._p3d_gl = aac_glview.create_gl_view(self._p3d_frame)
        if self._p3d_gl is not None:
            self._p3d_gl.pack(fill="both", expand=True)
            _p3d_target = self._p3d_gl
            self._p3d_label = None
        else:
            self._p3d_label = tk.Label(
                self._p3d_frame, bd=0, highlightthickness=0,
                text="3D preview needs OpenGL.\nAdd the wheels to the wheels folder,\nor run Install_Deps.bat.")
            self._p3d_label.pack(fill="both", expand=True)
            _p3d_target = self._p3d_label
        # v15.58: viewer info chip — Triangles / Dimensions / Tag for
        # the selected tag, a dark chip overlaid bottom-right inside
        # the preview cell. Placed, not packed, so it floats over the
        # GL widget; only shown once a scene has landed (see
        # _p3d_update_info_chip).
        self._p3d_info_label = None
        if self._p3d_gl is not None:
            self._p3d_info_label = tk.Label(
                self._p3d_frame, text="", bg="#2C2F3A", fg="#FFFFFF",
                font=("TkDefaultFont", 10), justify="left",
                padx=10, pady=8, bd=0, highlightthickness=0)
        # add="+" matters on the GL widget: pyopengltk already binds
        # <Configure> (viewport + initgl), and a plain bind would
        # replace it.
        _p3d_target.bind("<Button-1>", self._p3d_on_press, add="+")
        _p3d_target.bind("<B1-Motion>", self._p3d_on_drag, add="+")
        _p3d_target.bind("<ButtonRelease-1>", self._p3d_on_release, add="+")
        _p3d_target.bind("<MouseWheel>", self._p3d_on_wheel, add="+")
        _p3d_target.bind("<Button-4>", self._p3d_on_wheel, add="+")
        _p3d_target.bind("<Button-5>", self._p3d_on_wheel, add="+")
        _p3d_target.bind("<Button-3>", self._p3d_on_rpress, add="+")
        _p3d_target.bind("<B3-Motion>", self._p3d_on_rdrag, add="+")
        _p3d_target.bind("<ButtonRelease-3>", self._p3d_on_rrelease, add="+")
        _p3d_target.bind("<Double-Button-1>", self._p3d_on_dblclick, add="+")
        _p3d_target.bind("<Configure>", self._p3d_on_resize, add="+")
        self._p3d_cam = {"azim": 45.0, "elev": 44.0, "dist": None, "target": (0.0, 0.0, 0.0)}
        self._p3d_words = []
        self._p3d_layout = []
        self._p3d_tag_ranges = []
        self._p3d_bounds = []
        self._p3d_footprints = []
        self._p3d_after = None
        self._p3d_busy = False
        self._p3d_dirty = False
        self._p3d_gen = 0
        self._p3d_refit = False
        # v15.62: the embedded browser is gone. The host frame stays
        # created but permanently hidden (visibility helper only);
        # the built-in GL frame is the preview.
        self._embed_host = tk.Frame(main, highlightthickness=0, bd=0,
                                    bg=PREVIEW_BG_DARK if ctk.get_appearance_mode() == "Dark" else PREVIEW_BG_LIGHT)
        self._embed_host.grid(row=1, column=0, sticky="nsew", pady=12)
        self._embed_host.grid_remove()
        self._embed_ctrl = None
        self._embed_state = "off"  # v15.62: permanently off; the embed is gone
        self.canvas.bind("<Button-5>", self._on_pdf_canvas_wheel)   # Linux scroll down
        # v15.3: Delete removes selected PDF cards; Escape cancels inline edit
        self.bind("<Delete>", self._pdf_delete_selected)
        self.bind("<BackSpace>", self._pdf_delete_selected)
        self.bind("<Escape>", lambda e: self._pdf_end_inline_edit(commit=False))
        self.info = ctk.CTkLabel(main, text="", anchor="w", text_color="gray")
        self.info.grid(row=2, column=0, sticky="ew")

        self.export_btn = ctk.CTkButton(main, text="Export file", height=40, corner_radius=10,
                                        font=ctk.CTkFont(size=14, weight="bold"),
                                        fg_color="#4ECDC4", hover_color="#6EDDD6", command=self.export_file)
        self.export_btn.grid(row=3, column=0, sticky="ew", pady=(10, 6))
        self.progress = ctk.CTkProgressBar(main, height=6)
        self.progress.set(0)
        self.progress.grid(row=4, column=0, sticky="ew")
        self.progress.grid_remove()
        foot = ctk.CTkFrame(main, fg_color="transparent")
        foot.grid(row=5, column=0, sticky="ew")
        foot.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(foot, text="Ready", anchor="w", text_color="gray")
        self.status.grid(row=0, column=0, sticky="w")
        self.reveal_btn = ctk.CTkButton(foot, text="Show in folder", width=110, height=24, fg_color="gray30",
                                        hover_color="#555", command=self.reveal_last)
        self.reveal_btn.grid(row=0, column=1, sticky="e")
        self.reveal_btn.grid_remove()

        # v13.14: the AI (Local) tab takes over the WHOLE window (sidebar + preview are
        # hidden while it is open). A slim bar at the top switches to any other tab.
        self.ai_panel = ctk.CTkFrame(self, fg_color="transparent")
        self.ai_panel.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=16, pady=14)
        self.ai_panel.grid_remove()
        nav = ctk.CTkFrame(self.ai_panel, fg_color="transparent")
        nav.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(nav, text=f"{APP_NAME} {APP_VERSION}", font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        self.ai_nav = ctk.CTkSegmentedButton(
            nav, values=["Maker Studio", "Word Lists", "Tag Design", "PDF Studio", AI_TAB],
            command=self.goto_tab)
        self.ai_nav.set(AI_TAB)
        self.ai_nav.pack(side="left", padx=18)
        self.log_btn2 = ctk.CTkButton(nav, text="Log", width=70, height=30, fg_color="gray30",
                                      hover_color="#555", command=self.open_log)
        self.log_btn2.pack(side="right")
        ai_body = ctk.CTkFrame(self.ai_panel, fg_color="transparent")
        ai_body.pack(fill="both", expand=True)
        self._pdf_drag = None
        self._build_ai(self.tabs.tab(AI_TAB), ai_body)

        # v13.14: Library — replaces the preview area while it is open (Word Lists tab)
        self.lib_panel = ctk.CTkFrame(main, fg_color="transparent")
        self.lib_panel.grid(row=1, column=0, sticky="nsew", pady=12)
        self.lib_panel.grid_remove()
        self._build_library(self.lib_panel)

        # v15.51 (PRIORITY): freeze the sidebar width. grid_propagate
        # alone never held it — the tabview is PACKED into side, so
        # pack propagation still followed the tabview's requested
        # width, and the long "Editing ..." label widened the whole
        # bar by ~110px on every tag click (502 -> 612 in the rig)
        # and back on deselect. Frozen at the natural width once all
        # tabs are built; the edit label wraps (below) instead of
        # driving the width, and on_workspace_change only ever grows
        # the lock (never shrinks it) if a tab needs more room.
        try:
            side.update_idletasks()
            side.configure(width=max(380, side.winfo_reqwidth()))
            side.pack_propagate(False)
        except Exception:
            pass

    def goto_tab(self, name):
        """Switch tab AND swap the workspace (tabs.set alone doesn't fire the tab callback)."""
        try:
            self.tabs.set(name)
        except Exception:
            return
        self.on_workspace_change()

    def quick_add_word(self):
        """Type a word, Enter adds it to the list (v3.12.0-exp).
        v13.20: tiles — also accepts several words at once (commas / lines)."""
        try:
            raw = self.quick_add_entry.get().strip()
            if not raw:
                return
            words = self.get_words()
            have = {w.lower() for w in words}
            added = False
            for w in (x.strip() for x in raw.replace(",", "\n").split("\n")):
                w = w.strip()
                if w and w.lower() not in have:
                    words.append(w)
                    have.add(w.lower())
                    added = True
            if added:
                self.set_words(words)
            self.quick_add_entry.delete(0, "end")
        except Exception:
            pass

    def new_word_list(self):
        """Start a fresh list — clears the tiles so you can add new words."""
        try:
            if self.get_words() and not messagebox.askyesno(APP_NAME, "Start a new list? This clears the current words."):
                return
            self.set_words([])
            self.quick_add_entry.focus_set()
        except Exception:
            pass

    def _heart_image(self, size):
        """A small blush heart, drawn with PIL (no font-symbol dependency)."""
        from PIL import Image, ImageDraw
        s = size * 4
        im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse([0, s * 0.15, s * 0.5, s * 0.65], fill=(232, 140, 160, 255))
        d.ellipse([s * 0.5, s * 0.15, s, s * 0.65], fill=(232, 140, 160, 255))
        d.polygon([(0, s * 0.4), (s, s * 0.4), (s * 0.5, s)], fill=(232, 140, 160, 255))
        return ctk.CTkImage(light_image=im, dark_image=im, size=(size, size))


    def _log_flush(self):
        """Force every log handler to disk (v15.35: a native crash must
        not take the newest 3D lines with it)."""
        try:
            for h in log.handlers:
                try:
                    h.flush()
                except Exception:
                    pass
        except Exception:
            pass

    def _maker_open_3d_view(self, embedded=False):
        """3D view for the current Maker tag: the proven browser page.

        v15.62: the embedded WebView2 path is GONE entirely (the window
        machinery, the module, and the loader DLL are all deleted) — the
        `embedded` argument is ignored and this always opens the browser
        page. The in-app preview is the native GL view.
        """
        embedded = False
        try:
            words = self._maker_preview_words()
        except Exception:
            words = []
        target = getattr(self, "_selected_multi", None) or self.preview_word.get() \
            or (words[0] if words else None)
        if not target:
            return self.flash_status("Put a word on a tag first, then View in 3D")
        self._3d_counter = getattr(self, "_3d_counter", 0) + 1
        log.info("3D view: browser flow for %r", target)
        self._log_flush()
        # Shared export: tkinter values are read on the main thread (a
        # worker-thread read crashed in v15.27); only the mesh export runs
        # in the background. work() never raises — it returns a status
        # tuple so done() can close the embedded window on failure instead
        # of leaving it on "Building…".
        o = self._opts_for_word(target)
        _, ov, var, sa, cat = self._export_args()
        colors = self._display_colors(target, o, cat)

        def work():
            tmp = Path(tempfile.mkdtemp(prefix="aac_3dview_"))
            try:
                # v15.31: the viewer never auto-draws. A picture-less word
                # (e.g. 'afraid') used to fire a full local-AI draw here —
                # ~100s of model loading per click — which froze the app
                # into looking crashed. Local pictures only; otherwise
                # the tag shows word-only, exactly like a local-only build.
                rep = core.export_words([target], tmp, o, ov, var, stl=True, threemf=False,
                                        plate=False, search_as=sa, category_colors=cat,
                                        auto=False)
                if rep["failed"]:
                    return (False, rep["failed"][0][1])
                n = core.safe_name(target)
                parts = []
                for part, color in zip(("base", "ink", "ink2"), colors):
                    f = tmp / f"{n}_{part}.stl"
                    if f.exists():
                        parts.append((part, f.read_bytes(), color))
                if not parts:
                    return (False, "No STL was produced for this tag")
                page = aac_3dview.build_viewer_html(target, parts)
                if page is None:
                    return (False, "3D viewer files are missing (viewer3d/) — use the full zip")
                # One stable file, overwritten on every click, so the
                # browser fallback and refreshes always show the newest tag.
                out = Path(tempfile.gettempdir()) / "aac_tag_3d_viewer.html"
                out.write_text(page, encoding="utf-8")
                return (True, out.as_uri() + "?v=" + str(self._3d_counter))
            except Exception as e:
                return (False, f"{type(e).__name__}: {e}")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        def done(result):
            ok, payload = result
            log.info("3D view: export done for %r ok=%s", target, ok)
            self._log_flush()
            if not ok:
                return self.flash_status(f"3D view failed: {payload}", ok=False, hold=True)
            url = payload
            import webbrowser
            webbrowser.open(url)
            self.flash_status(f"3D view opened in your browser for \u201c{target}\u201d", ok=True)

        self.bg("3D VIEW", work, done)

    def _maker_open_3d_multi(self):
        """Multi-tag 3D view: the whole Maker preview set on one table.

        v15.32 pre-conversion test: one tag proving out does not prove
        nine, so this page lays the locked preview set out in a grid
        (one cell per tag, floating word label above each) before the
        canvas is replaced. Browser page only — no embed path (the embed was
        removed in v15.38) and this test is about the 3D
        content and layout, not the embed. Like the single view, it
        never auto-draws (v15.31): local pictures only, word-only
        otherwise, so a picture-less word cannot stall the page.
        """
        try:
            words = self._maker_preview_words()
        except Exception:
            words = []
        if not words:
            return self.flash_status("Put words on tags first, then View all in 3D")
        self._3d_multi_counter = getattr(self, "_3d_multi_counter", 0) + 1
        log.info("3D view: multi browser flow for %d tags", len(words))
        # Tkinter values are read on the main thread (v15.27 lesson);
        # only the mesh exports run in the background.
        per_word = []
        try:
            _, ov, var, sa, cat = self._export_args()
            for w in words:
                o = self._opts_for_word(w)
                per_word.append((w, o, self._display_colors(w, o, cat)))
        except Exception as e:
            return self.flash_status(f"3D view failed: {type(e).__name__}: {e}",
                                     ok=False, hold=True)

        def work():
            tags = []
            skipped = []
            for (w, o, colors) in per_word:
                tmp = Path(tempfile.mkdtemp(prefix="aac_3dview_"))
                try:
                    rep = core.export_words([w], tmp, o, ov, var, stl=True, threemf=False,
                                            plate=False, search_as=sa, category_colors=cat,
                                            auto=False)
                    if rep["failed"]:
                        skipped.append(w)
                        continue
                    n = core.safe_name(w)
                    parts = []
                    for part, color in zip(("base", "ink", "ink2"), colors):
                        f = tmp / f"{n}_{part}.stl"
                        if f.exists():
                            parts.append((part, f.read_bytes(), color))
                    if parts:
                        tags.append((w, parts))
                    else:
                        skipped.append(w)
                except Exception:
                    skipped.append(w)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
            if not tags:
                return (False, "No STL was produced for these tags", 0)
            page = aac_3dview.build_multi_viewer_html(tags)
            if page is None:
                return (False, "3D viewer files are missing (viewer3d/) — use the full zip", 0)
            out = Path(tempfile.gettempdir()) / "aac_tag_3d_multi_viewer.html"
            out.write_text(page, encoding="utf-8")
            return (True, out.as_uri() + "?v=" + str(self._3d_multi_counter), len(tags))

        def done(result):
            ok, payload, count = result
            log.info("3D view: multi export done ok=%s n=%s", ok, count)
            if not ok:
                return self.flash_status(f"3D view failed: {payload}", ok=False, hold=True)
            import webbrowser
            webbrowser.open(payload)
            self.flash_status(f"Multi 3D view opened in your browser ({count} tags)", ok=True)

        self.bg("3D VIEW (ALL TAGS)", work, done)

    # ---- Live 3D viewer (v15.35) ----
    def _live_target(self):
        """Which tag the live viewer should show (v15.36).

        The selected multi tag wins — but only while it is still in the
        preview set. A stale selection (word removed from the set) used
        to pin the live view to a tag that no longer exists.
        """
        try:
            words = self._maker_preview_words()
        except Exception:
            words = []
        sel = getattr(self, "_selected_multi", None)
        if sel and words and sel not in words:
            sel = None
        return sel or self.preview_word.get() or (words[0] if words else None)

    def _live_ensure_server(self):
        """Create the loopback live server once (or return the running one)."""
        try:
            if getattr(self, "_live_server", None) is None:
                live_dir = Path(tempfile.mkdtemp(prefix="aac_live3d_"))
                (live_dir / "stl").mkdir(parents=True, exist_ok=True)
                page = aac_3dview.build_live_viewer_html()
                if page is None:
                    return False
                (live_dir / "index.html").write_text(page, encoding="utf-8")
                srv = aac_live3d.LiveServer(live_dir)
                srv.start()
                self._live_server = srv
                self._live_dir = live_dir
                self._live_version = 0
                # v15.44: a fresh server has no state.json yet, so the
                # unchanged-content skip must not carry over from the
                # previous server instance.
                self._live_last_fp = None
            return True
        except Exception as e:
            log.info("Live 3D: server start failed: %s", e)
            return False

    def _maker_open_live_view(self):
        """Open the live viewer page and push the current tag to it.

        The page is served from this machine only (127.0.0.1, ephemeral
        port) and polls state.json, swapping meshes in place on each
        edit — camera and orbit are untouched. The server dies with the
        app; the page simply stops updating after that.
        """
        target = self._live_target()
        if not target:
            return self.flash_status("Put a word on a tag first, then Live 3D viewer")
        if not self._live_ensure_server():
            return self.flash_status("Live 3D view needs the viewer files (viewer3d/)", ok=False, hold=True)
        try:
            import webbrowser
            webbrowser.open(self._live_server.url())
        except Exception as e:
            return self.flash_status(f"Live 3D view failed: {type(e).__name__}: {e}", ok=False, hold=True)
        self.flash_status("Live 3D viewer opened — edit a tag and watch it update", ok=True)
        log.info("Live 3D: opened for %r on %s", target, self._live_server.url())
        self._live_push(target)

    def _live_push_soon(self, word=None):
        """Schedule a live push (v15.37: never cancels, never starves).

        The old design cancelled and re-scheduled on every repaint, so
        a stream of repaints could postpone the push forever. Now the
        first call schedules one push 400ms out; later calls only update
        the wanted word and mark dirty. A push already running is never
        doubled up — its completion reschedules when dirty.
        """
        if getattr(self, "_live_server", None) is None:
            return
        self._live_dirty = True
        self._live_want = word
        if getattr(self, "_live_after", None) or getattr(self, "_live_busy", False):
            return
        try:
            self._live_after = self.after(400, self._live_fire)
        except Exception:
            pass

    def _live_fire(self):
        self._live_after = None
        if getattr(self, "_live_busy", False):
            return  # the running push's completion reschedules (dirty kept)
        self._live_dirty = False
        self._live_push(getattr(self, "_live_want", None))

    def _live_push_finished(self):
        """A live push ended (success, failure, or early return)."""
        self._live_busy = False
        if getattr(self, "_live_dirty", False) and getattr(self, "_live_server", None) is not None \
                and not getattr(self, "_live_after", None):
            try:
                self._live_after = self.after(400, self._live_fire)
            except Exception:
                pass

    def _live_push(self, target=None):
        """Re-export the current tag (auto=False, never an AI draw) and
        write the loopback server's stl/ + state.json for the live page.
        Failures are silent by design: the page keeps the previous tag
        and the next edit retries."""
        srv = getattr(self, "_live_server", None)
        if srv is None:
            return
        target = target or self._live_target()
        if not target:
            return
        try:
            o = self._opts_for_word(target)
            _, ov, var, sa, cat = self._export_args()
            colors = self._display_colors(target, o, cat)
        except Exception:
            return
        # v15.37: push-start line, so the log proves whether pushes start,
        # fail, or complete (a stuck page with no start lines = scheduling).
        # v15.44: push only when the content actually changed. draw() on
        # Maker/Tag Design schedules a push on EVERY repaint, so an
        # unchanged word ('afraid' at startup) was re-exported and
        # re-pushed forever — the log full of "pushing 'afraid'" lines
        # Cody asked to be rid of. The STL is a deterministic function
        # of (word, opts, picture choice, search-as, icon path), so
        # that tuple is the fingerprint; edits, picture changes and
        # tile selection all change it and still push.
        try:
            fp = repr((target, asdict(o), ov, var, sa,
                       getattr(self, "_icon_paths", {}).get(target)))
        except Exception:
            fp = None
        if fp is not None and fp == getattr(self, "_live_last_fp", None):
            log.debug("Live 3D: push skipped (unchanged) for %r", target)
            try:
                self._live_push_finished()
            except Exception:
                pass
            return
        self._live_last_fp = fp
        log.info("Live 3D: pushing %r", target)
        self._live_busy = True
        live_dir = self._live_dir

        def work():
            tmp = Path(tempfile.mkdtemp(prefix="aac_live3d_"))
            try:
                rep = core.export_words([target], tmp, o, ov, var, stl=True,
                                        threemf=False, plate=False, search_as=sa,
                                        category_colors=cat, auto=False)
                if rep["failed"]:
                    return (False, rep["failed"][0][1])
                n = core.safe_name(target)
                meshes = []
                for part, color in zip(("base", "ink", "ink2"), colors):
                    f = tmp / f"{n}_{part}.stl"
                    if f.exists():
                        (live_dir / "stl" / f"{part}.stl").write_bytes(f.read_bytes())
                        meshes.append({"label": part, "color": color,
                                       "url": f"stl/{part}.stl"})
                if not meshes:
                    return (False, "No STL was produced")
                return (True, meshes)
            except Exception as e:
                return (False, f"{type(e).__name__}: {e}")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        def done(result):
            ok, payload = result
            if not ok:
                log.info("Live 3D: push failed for %r: %s", target, payload)
                try:
                    self._live_push_finished()
                except Exception:
                    pass
                return
            import json
            self._live_version = getattr(self, "_live_version", 0) + 1
            state = {"version": self._live_version, "word": target, "meshes": payload}
            try:
                (live_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
            except Exception as e:
                log.info("Live 3D: state write failed: %s", e)
                try:
                    self._live_push_finished()
                except Exception:
                    pass
                return
            log.info("Live 3D: pushed version %s for %r", self._live_version, target)
            try:
                self._live_push_finished()
            except Exception:
                pass

        try:
            self.bg("LIVE 3D", work, done)
        except Exception:
            self._live_push_finished()

    # ---- In-app 3D preview (v15.39; native renderer, no WebView2) ----
    def _preview3d_soon(self):
        """Debounced in-app 3D refresh (same never-starve pattern as live)."""
        if getattr(self, "_p3d_frame", None) is None:
            return
        self._p3d_dirty = True
        if getattr(self, "_p3d_after", None) or getattr(self, "_p3d_busy", False):
            return
        try:
            self._p3d_after = self.after(400, self._preview3d_fire)
        except Exception:
            pass

    def _preview3d_fire(self):
        self._p3d_after = None
        if getattr(self, "_p3d_busy", False):
            return
        self._p3d_dirty = False
        self._preview3d_refresh()

    def _preview3d_finished(self):
        self._p3d_busy = False
        if getattr(self, "_p3d_dirty", False) and getattr(self, "_p3d_frame", None) is not None \
                and not getattr(self, "_p3d_after", None):
            try:
                self._p3d_after = self.after(400, self._preview3d_fire)
            except Exception:
                pass

    def _preview3d_words(self):
        """Words the 3D preview should show (multi set, else the one word)."""
        try:
            if getattr(self, "maker_multi", None) is not None and self.maker_multi.get():
                return [w for w in self._maker_preview_words() if w]
        except Exception:
            pass
        try:
            w = self.preview_word.get()
        except Exception:
            w = None
        return [w] if w and w != "-" else []

    def _p3d_bg_ground(self, dark):
        bg = aac_glview.hex_to_rgb(PREVIEW_BG_DARK) if dark else aac_glview.hex_to_rgb(PREVIEW_BG_LIGHT)
        ground = (72, 68, 82) if dark else (220, 215, 230)
        return bg, ground

    def _p3d_union_bounds(self, ranges, gbounds):
        out = []
        for (w, s, e) in ranges:
            gs = gbounds[s:e]
            xs0 = [b[0] - b[2] for b in gs]; xs1 = [b[0] + b[2] for b in gs]
            ys0 = [b[1] - b[3] for b in gs]; ys1 = [b[1] + b[3] for b in gs]
            out.append((w, ((min(xs0) + max(xs1)) / 2.0, (min(ys0) + max(ys1)) / 2.0,
                            (max(xs1) - min(xs0)) / 2.0, (max(ys1) - min(ys0)) / 2.0)))
        return out

    def _preview3d_refresh(self):
        """Re-export the preview words (auto=False) and upload them to
        the OpenGL preview.

        Main-thread reads first (frame size, camera, options), then the
        export + parse + bounds run off-thread; only the scene upload
        runs on the main thread. A camera move during the export drops
        the frame and schedules a fresh one instead of jumping the view
        back.
        """
        words = self._preview3d_words()
        if not words:
            return
        try:
            frame = self._p3d_frame
            fw = max(frame.winfo_width(), 50)
            fh = max(frame.winfo_height(), 50)
            dark = ctk.get_appearance_mode() == "Dark"
            cam = dict(self._p3d_cam)
            prev_words = list(self._p3d_words)
            multi = bool(getattr(self, "maker_multi", None) is not None and self.maker_multi.get())
            # v15.52: printer bed for the multi grid fit (preview only).
            try:
                bed = self._bed_dims()
            except Exception:
                bed = (220.0, 220.0)
            sel = getattr(self, "_selected_multi", None) if multi else None
            per_opts = {}
            for w in words:
                try:
                    per_opts[w] = self._opts_for_word(w)
                except Exception:
                    per_opts[w] = None
            _, ov, var, sa, cat = self._export_args()
        except Exception:
            return
        gen = getattr(self, "_p3d_gen", 0)
        log.info("Preview 3D: refreshing %r", words)
        self._p3d_busy = True
        # Watchdog: if a worker thread ever dies silently (bg's done is
        # skipped on exception), busy must not stick forever and brick
        # the preview. Bumping the generation also invalidates a late
        # done() from the stuck job, so it can't overwrite newer work.
        _rid = getattr(self, "_p3d_refresh_id", 0) + 1
        self._p3d_refresh_id = _rid

        def _watchdog(rid=_rid):
            if getattr(self, "_p3d_busy", False) and getattr(self, "_p3d_refresh_id", 0) == rid:
                log.info("Preview 3D: refresh watchdog — clearing stuck refresh")
                self._p3d_gen = getattr(self, "_p3d_gen", 0) + 1
                self._p3d_busy = False
                try:
                    self._preview3d_finished()
                except Exception:
                    pass

        try:
            self.after(30000, _watchdog)
        except Exception:
            pass

        def work():
            import math
            aac_glview.gc_hold()
            tmp = Path(tempfile.mkdtemp(prefix="aac_p3d_"))
            try:
                raw = []  # [(word, [(tris, color), ...])]
                for w in words:
                    o = per_opts.get(w)
                    if o is None:
                        continue
                    rep = core.export_words([w], tmp, o, ov, var, stl=True,
                                            threemf=False, plate=False, search_as=sa,
                                            category_colors=cat, auto=False)
                    if rep["failed"]:
                        continue
                    n = core.safe_name(w)
                    parts = []
                    for part, color in zip(("base", "ink", "ink2"),
                                           self._display_colors(w, o, cat)):
                        f = tmp / f"{n}_{part}.stl"
                        if f.exists():
                            tris = aac_glview.parse_stl(f.read_bytes())
                            if tris:
                                parts.append((tris, color))
                    if parts:
                        raw.append((w, parts))
                if not raw:
                    return (False, "No STL was produced")
                # Grid layout in world units (mirror the multi browser page).
                foot = []
                for _w, parts in raw:
                    xs = [v[0] for tris, _ in parts for tri in tris for v in tri]
                    ys = [v[1] for tris, _ in parts for tri in tris for v in tri]
                    foot.append(max(max(xs) - min(xs), max(ys) - min(ys)))
                cell0 = max(foot) * 1.25 if foot else 50.0
                max_foot = max(foot) if foot else 50.0
                n = len(raw)
                cols = max(1, math.ceil(math.sqrt(n)))
                rows = (n + cols - 1) // cols
                cell = cell0
                # v15.52: fit the grid inside the printer bed (multi only,
                # 2+ tags). The natural grid stays exactly as before when
                # it already fits. Otherwise try every column count with
                # a shrunk cell (never tighter than tag + 4mm gap) and
                # keep the least-shrunk fit; if nothing fits, the natural
                # grid stays and the overflowing tags get red frames.
                bed_info = None
                if multi and n >= 2:
                    bx, by = bed
                    def _fits(c, r, cl):
                        return ((c - 1) * cl + max_foot <= bx + 1e-9
                                and (r - 1) * cl + max_foot <= by + 1e-9)
                    if not _fits(cols, rows, cell0):
                        best = None
                        for c in range(1, n + 1):
                            r = (n + c - 1) // c
                            lim = cell0
                            if c > 1:
                                lim = min(lim, (bx - max_foot) / (c - 1))
                            if r > 1:
                                lim = min(lim, (by - max_foot) / (r - 1))
                            if lim < max_foot + 4.0:
                                continue
                            key = (lim, -abs(c - math.sqrt(n)))
                            if best is None or key > best[0]:
                                best = (key, c, r, lim)
                        if best is not None:
                            _, cols, rows, cell = best
                layout = []
                ranges = []
                for idx, (w, parts) in enumerate(raw):
                    col, row = idx % cols, idx // cols
                    ox = (col - (cols - 1) / 2.0) * cell
                    oy = ((rows - 1) / 2.0 - row) * cell
                    start = len(layout)
                    # v15.48: ONE center + ground per WORD, across all its
                    # parts, AND stack the parts: every part STL is
                    # exported grounded at z=0 (base 0-1.6, ink 0-0.6),
                    # so without stacking the ink sits INSIDE the base
                    # slab and the GL depth buffer hides it — the 15.47
                    # blank-disc bug (the black wedges in 15.47 were
                    # just the misaligned ink ring peeking past the disc
                    # edge). Raise every non-base part by the base
                    # thickness so ink/ink2 rest on the face, like the
                    # actual tag. parts[0] is always the base (mats
                    # order in export_words: body, ink, ink2).
                    allx = [v[0] for tris, _ in parts for tri in tris for v in tri]
                    ally = [v[1] for tris, _ in parts for tri in tris for v in tri]
                    allz = [v[2] for tris, _ in parts for tri in tris for v in tri]
                    cx = (min(allx) + max(allx)) / 2.0
                    cy = (min(ally) + max(ally)) / 2.0
                    mz = min(allz)
                    base_top = max(v[2] for tri in parts[0][0] for v in tri) - mz if parts else 0.0
                    for j, (tris, color) in enumerate(parts):
                        dz = 0.0 if j == 0 else base_top
                        # v15.51: mirror each tag about its own center
                        # (preview only). The exported STL text is laid
                        # out right-to-left, so the preview showed every
                        # word backwards ("won" for "now") — and no orbit
                        # angle can fix a mirror, only a rotation can.
                        # Negating the centered x makes the words read
                        # correctly here while the STL files (and the
                        # exports) are untouched; the tag's bbox is
                        # unchanged, so cells, footprints and picking
                        # bounds all stay exactly where they were.
                        layout.append(([tuple((-(v[0] - cx) + ox, v[1] - cy + oy, v[2] - mz + dz) for v in tri) for tri in tris], color))
                    # v15.73: AUTOMATIC FACE FILL. The tag's middle must
                    # never look empty in the preview, whatever the mesh
                    # cap does on a given GPU: copy the base part's face
                    # triangles (the highest large flat level — the top
                    # face, not the bottom or the 0.6 interface cap, and
                    # not a small top-loop cap) into an extra group in
                    # the tag's base colour, lifted a hair (0.03 mm) so
                    # it wins the depth test over the cap itself, and in
                    # BOTH windings so backface culling can never hide
                    # it: one winding is always front-facing. When the
                    # cap already draws, the fill is the same colour in
                    # the same place and changes nothing visible; when
                    # the cap fails to draw, the fill IS the face.
                    # Preview only — exports are untouched.
                    try:
                        laid_base_tris, base_color = layout[start]
                        levels = {}
                        for tri in laid_base_tris:
                            zs = (tri[0][2], tri[1][2], tri[2][2])
                            if abs(zs[0] - zs[1]) < 1e-6 and abs(zs[1] - zs[2]) < 1e-6:
                                levels.setdefault(round(zs[0], 6), []).append(tri)
                        if levels:
                            def _area(tris):
                                a = 0.0
                                for (x1, y1, _), (x2, y2, _), (x3, y3, _) in tris:
                                    a += abs((x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)) * 0.5
                                return a
                            max_area = max(_area(t) for t in levels.values())
                            face_z = max(z for z, t in levels.items() if _area(t) >= 0.9 * max_area)
                            fill = []
                            for tri in levels[face_z]:
                                up = tuple((v[0], v[1], v[2] + 0.03) for v in tri)
                                fill.append(up)
                                fill.append((up[0], up[2], up[1]))
                            layout.append((fill, base_color))
                    except Exception:
                        pass
                    ranges.append((w, start, len(layout)))
                # v15.52: bed plate + overflow frames (multi, 2+ tags).
                # The plate is a flat rectangle the size of the bed at
                # z=0.05 (under the tags, over the ground plane) so the
                # fit is visible; a tag whose laid-out bbox sticks past
                # the bed rect gets a red frame at z=0.1. Both are extra
                # layout groups AFTER the word groups, so ranges,
                # footprints and picking never see them — but the camera
                # fit below (allv) does include them, so the whole bed
                # stays in view. Exports are untouched.
                if multi and n >= 2:
                    bx, by = bed
                    over = []
                    for (w, s, e) in ranges:
                        xs = [v[0] for tris, _ in layout[s:e] for tri in tris for v in tri]
                        ys = [v[1] for tris, _ in layout[s:e] for tri in tris for v in tri]
                        if not xs or not ys:
                            continue
                        cx = (min(xs) + max(xs)) / 2.0
                        cy = (min(ys) + max(ys)) / 2.0
                        rx = (max(xs) - min(xs)) / 2.0
                        ry = (max(ys) - min(ys)) / 2.0
                        if abs(cx) + rx > bx / 2.0 + 1e-9 or abs(cy) + ry > by / 2.0 + 1e-9:
                            over.append((w, cx, cy, rx, ry))
                    plate = "#605C6C" if dark else "#C8C1D6"
                    hx, hy, pz = bx / 2.0, by / 2.0, 0.05
                    layout.append(([[(-hx, -hy, pz), (hx, -hy, pz), (hx, hy, pz)],
                                    [(-hx, -hy, pz), (hx, hy, pz), (-hx, hy, pz)]], plate))
                    red = "#E5484D"
                    for (_w, cx, cy, rx, ry) in over:
                        m, t, fz = 3.0, 3.0, 0.1
                        x0, x1 = cx - rx - m - t, cx + rx + m + t
                        y0, y1 = cy - ry - m - t, cy + ry + m + t
                        ix0, ix1 = cx - rx - m, cx + rx + m
                        iy0, iy1 = cy - ry - m, cy + ry + m
                        strips = [((x0, y1 - t, fz), (x1, y1 - t, fz), (x1, y1, fz)),
                                  ((x0, y1 - t, fz), (x1, y1, fz), (x0, y1, fz)),
                                  ((x0, y0, fz), (x1, y0, fz), (x1, y0 + t, fz)),
                                  ((x0, y0, fz), (x1, y0 + t, fz), (x0, y0 + t, fz)),
                                  ((x0, iy0, fz), (ix0, iy0, fz), (ix0, iy1, fz)),
                                  ((x0, iy0, fz), (ix0, iy1, fz), (x0, iy1, fz)),
                                  ((ix1, iy0, fz), (x1, iy0, fz), (x1, iy1, fz)),
                                  ((ix1, iy0, fz), (x1, iy1, fz), (ix1, iy1, fz))]
                        layout.append(([[a, b, c] for (a, b, c) in strips], red))
                    bed_info = (bx, by, [w for (w, _cx, _cy, _rx, _ry) in over])
                allv = [v for tris, _ in layout for tri in tris for v in tri]
                minx = min(v[0] for v in allv); maxx = max(v[0] for v in allv)
                miny = min(v[1] for v in allv); maxy = max(v[1] for v in allv)
                minz = min(v[2] for v in allv); maxz = max(v[2] for v in allv)
                tcenter = ((minx + maxx) / 2.0, (miny + maxy) / 2.0, (minz + maxz) / 2.0)
                radius = 0.5 * max(maxx - minx, maxy - miny, maxz - minz)
                cam2 = dict(cam)
                if tuple(w for w, _ in raw) != tuple(prev_words) or cam2.get("dist") is None:
                    cam2["dist"] = aac_glview.fit_distance(max(radius, 1.0))
                    cam2["target"] = tcenter
                # Per-part screen bounds for picking (the GL widget draws
                # its own glow from the selection word, so no glow value
                # is computed here anymore).
                gbounds = [aac_glview.project_bounds(tris, cam2["azim"], cam2["elev"], cam2["dist"], cam2["target"], fw, fh)
                           for tris, _ in layout]
                tbounds = self._p3d_union_bounds(ranges, gbounds)
                # v15.51: per-word WORLD footprints (bbox + z range) so
                # picking can re-project from the CURRENT camera —
                # the refresh-time screen bounds went stale on every
                # orbit/zoom, which is why right-click options (and
                # click-select) only landed on the highlighted/center
                # tag after moving the view.
                footprints = []
                for (w, s, e) in ranges:
                    xs = [v[0] for tris, _ in layout[s:e] for tri in tris for v in tri]
                    ys = [v[1] for tris, _ in layout[s:e] for tri in tris for v in tri]
                    zs = [v[2] for tris, _ in layout[s:e] for tri in tris for v in tri]
                    footprints.append((w, ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0,
                                            (max(xs) - min(xs)) / 2.0, (max(ys) - min(ys)) / 2.0,
                                            min(zs), max(zs))))
                return (True, (layout, ranges, tbounds, cam2, [w for w, _ in raw], footprints, bed_info))
            except Exception as e:
                return (False, f"{type(e).__name__}: {e}")
            finally:
                aac_glview.gc_release()
                shutil.rmtree(tmp, ignore_errors=True)

        def done(result):
            ok, payload = result
            if not ok:
                log.info("Preview 3D: refresh failed: %s", payload)
                try:
                    self._preview3d_finished()
                except Exception:
                    pass
                return
            if gen != getattr(self, "_p3d_gen", 0):
                # Camera moved mid-export: drop this frame, refresh fresh.
                try:
                    self._preview3d_finished()
                except Exception:
                    pass
                try:
                    self._preview3d_soon()
                except Exception:
                    pass
                return
            layout, ranges, tbounds, cam2, rwords, footprints, bed_info = payload
            try:
                self._p3d_layout = layout
                self._p3d_tag_ranges = ranges
                self._p3d_bounds = tbounds
                self._p3d_footprints = footprints
                self._p3d_words = rwords
                self._p3d_cam = cam2
                # v15.52: bed fit note under the preview page.
                try:
                    lbl = getattr(self, "_bed_status_label", None)
                    if lbl is not None and lbl.winfo_exists() and bed_info:
                        bx, by, over = bed_info
                        if over:
                            lbl.configure(
                                text=f"Bed {bx:.0f} × {by:.0f}: {len(over)} tag(s) outside the bed (red) — the slicer can split them",
                                text_color="#E5484D")
                        else:
                            lbl.configure(
                                text=f"Bed {bx:.0f} × {by:.0f}: all {len(rwords)} tags fit",
                                text_color="gray")
                except Exception:
                    pass
                gl = getattr(self, "_p3d_gl", None)
                if gl is not None:
                    bg, ground = self._p3d_bg_ground(dark)
                    gl.set_scene(layout, ranges, cam2, bg, ground, sel)
                    self._p3d_update_info_chip()
            except Exception as e:
                log.info("Preview 3D: display failed: %s", e)
            try:
                self._preview3d_finished()
            except Exception:
                pass

        try:
            self.bg("PREVIEW 3D", work, done)
        except Exception:
            self._preview3d_finished()

    def _p3d_update_info_chip(self):
        """v15.58: refresh the viewer info chip from the last scene.

        Selected tag → that tag's triangles + dimensions + name.
        Nothing selected → totals across all tags + the tag count.
        Hidden until a scene has landed.
        """
        lbl = getattr(self, "_p3d_info_label", None)
        if lbl is None:
            return
        try:
            if not lbl.winfo_exists():
                return
            words = getattr(self, "_p3d_words", None) or []
            layout = getattr(self, "_p3d_layout", None) or []
            if not words or not layout:
                lbl.place_forget()
                return
            sel = getattr(self, "_selected_multi", None)
            ranges = getattr(self, "_p3d_tag_ranges", None) or []
            if sel and sel in words:
                tris = 0
                for (w, s, e) in ranges:
                    if w == sel:
                        tris = sum(len(t) for t, _c in layout[s:e])
                        break
                dims = None
                for (w, (cx, cy, rx, ry, z0, z1)) in \
                        (getattr(self, "_p3d_footprints", None) or []):
                    if w == sel:
                        dims = (2 * rx, 2 * ry, z1 - z0)
                        break
                dim_txt = (f"{dims[0]:.1f} × {dims[1]:.1f} × "
                           f"{dims[2]:.1f} mm") if dims else "—"
                lbl.configure(text=f"Triangles: {tris:,}\n"
                                   f"Dimensions: {dim_txt}\nTag: {sel}")
            else:
                tris = sum(len(t) for t, _c in layout)
                xs = []; ys = []; zs = []
                for t, _c in layout:
                    for tri in t:
                        for v in tri:
                            xs.append(v[0]); ys.append(v[1]); zs.append(v[2])
                if xs:
                    dim_txt = (f"{max(xs) - min(xs):.1f} × "
                               f"{max(ys) - min(ys):.1f} × "
                               f"{max(zs) - min(zs):.1f} mm")
                else:
                    dim_txt = "—"
                lbl.configure(text=f"Triangles: {tris:,}\n"
                                   f"Dimensions: {dim_txt}\n"
                                   f"Tags: {len(words)}")
            lbl.place(relx=1.0, rely=1.0, anchor="se", x=-12, y=-12)
            lbl.lift()
        except Exception:
            pass

    def _p3d_render_memory(self):
        """Re-aim the OpenGL view from memory (drag/zoom/select/resize).

        v15.46: no render thread anymore — the GPU redraws instantly
        from the last uploaded scene, so this just applies the camera
        (with the refit logic) and asks the widget to redraw. The
        generation bump is kept so an export in flight sees the camera
        moved and drops its stale frame, as before.
        """
        gl = getattr(self, "_p3d_gl", None)
        if gl is None:
            return
        layout = getattr(self, "_p3d_layout", None)
        if not layout:
            return
        try:
            cam = dict(self._p3d_cam)
            multi = bool(getattr(self, "maker_multi", None) is not None and self.maker_multi.get())
            sel = getattr(self, "_selected_multi", None) if multi else None
            if getattr(self, "_p3d_refit", False):
                self._p3d_refit = False
                allv = [v for tris, _ in layout for tri in tris for v in tri]
                minx = min(v[0] for v in allv); maxx = max(v[0] for v in allv)
                miny = min(v[1] for v in allv); maxy = max(v[1] for v in allv)
                minz = min(v[2] for v in allv); maxz = max(v[2] for v in allv)
                cam["target"] = ((minx + maxx) / 2.0, (miny + maxy) / 2.0, (minz + maxz) / 2.0)
                cam["dist"] = aac_glview.fit_distance(0.5 * max(maxx - minx, maxy - miny, maxz - minz, 1.0))
                self._p3d_cam = dict(cam)
        except Exception:
            return
        self._p3d_gen = getattr(self, "_p3d_gen", 0) + 1
        try:
            self._p3d_cam = dict(cam)
            gl.set_camera(cam, sel)
        except Exception:
            pass
        try:
            self._p3d_update_info_chip()
        except Exception:
            pass

    def _p3d_pick(self, x, y):
        """Tag word nearest a click point, or None.

        v15.51: bounds are re-projected from the stored world
        footprints with the CURRENT camera every time — the old
        refresh-time bounds went stale on orbit/zoom/pan, so clicks
        and right-click menus landed on the wrong tag (or nowhere)
        after moving the view.
        """
        bounds = None
        try:
            fps = getattr(self, "_p3d_footprints", None)
            cam = getattr(self, "_p3d_cam", None)
            if fps and cam and cam.get("dist"):
                fw = max(self._p3d_frame.winfo_width(), 50)
                fh = max(self._p3d_frame.winfo_height(), 50)
                basis = aac_glview._basis(cam["azim"], cam["elev"], cam["dist"], cam["target"], fw, fh, 38.0)
                bounds = []
                for (w, (cx, cy, rx, ry, z0, z1)) in fps:
                    pts = []
                    for sx in (cx - rx, cx + rx):
                        for sy in (cy - ry, cy + ry):
                            for sz in (z0, z1):
                                p = aac_glview._project(basis, (sx, sy, sz))
                                if p is not None:
                                    pts.append(p)
                    if pts:
                        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                        bounds.append((w, ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0,
                                            (max(xs) - min(xs)) / 2.0, (max(ys) - min(ys)) / 2.0)))
                self._p3d_bounds = bounds
        except Exception:
            bounds = None
        if not bounds:
            bounds = getattr(self, "_p3d_bounds", [])
        pick = None
        best = 1e18
        for (w, (bx, by, brx, bry)) in bounds:
            if abs(x - bx) <= brx + 24 and abs(y - by) <= bry + 24:
                d = (x - bx) ** 2 + (y - by) ** 2
                if d < best:
                    best, pick = d, w
        return pick

    def _p3d_on_press(self, event):
        self._p3d_press = (event.x, event.y)
        self._p3d_moved = False

    def _p3d_on_drag(self, event):
        if getattr(self, "_p3d_press", None) is None:
            return
        dx = event.x - self._p3d_press[0]
        dy = event.y - self._p3d_press[1]
        if abs(dx) + abs(dy) > 3:
            self._p3d_moved = True
        if not self._p3d_moved:
            return
        cam = self._p3d_cam
        cam["azim"] = (cam["azim"] + dx * 0.5) % 360.0
        cam["elev"] = max(8.0, min(82.0, cam["elev"] - dy * 0.4))
        self._p3d_press = (event.x, event.y)
        self._p3d_render_memory()

    def _p3d_on_release(self, event):
        if getattr(self, "_p3d_moved", False):
            self._p3d_moved = False
            self._p3d_render_memory()
            return
        if self.tabs.get() not in ("Maker Studio", "Tag Design"):
            return
        multi = bool(getattr(self, "maker_multi", None) is not None and self.maker_multi.get())
        if not multi:
            return
        pick = self._p3d_pick(event.x, event.y)
        try:
            self._select_multi_tag(pick)
        except Exception:
            pass
        try:
            self._live_push_soon(None)
        except Exception:
            pass
        self._p3d_render_memory()

    def _p3d_on_rpress(self, event):
        # v15.51: right button down records for pan-vs-menu (the menu
        # now opens on release, so a right-drag can pan instead).
        self._p3d_rpress = (event.x, event.y)
        self._p3d_rpanned = False

    def _p3d_on_rdrag(self, event):
        # v15.51: right-drag pans the preview — the tag under the
        # cursor follows it (grab-the-world), like the PDF canvas.
        if getattr(self, "_p3d_rpress", None) is None:
            return
        if self.tabs.get() not in ("Maker Studio", "Tag Design"):
            return
        gl = getattr(self, "_p3d_gl", None)
        cam = dict(self._p3d_cam) if getattr(self, "_p3d_cam", None) else None
        if gl is None or cam is None or not cam.get("dist"):
            return
        dx = event.x - self._p3d_rpress[0]
        dy = event.y - self._p3d_rpress[1]
        if abs(dx) + abs(dy) > 3:
            self._p3d_rpanned = True
        if not self._p3d_rpanned:
            return
        try:
            fw = max(self._p3d_frame.winfo_width(), 50)
            fh = max(self._p3d_frame.winfo_height(), 50)
            _eye, right, up, _fwd, scale, _cx, _cy = aac_glview._basis(
                cam["azim"], cam["elev"], cam["dist"], cam["target"], fw, fh, 38.0)
            upp = 1.0 / scale if scale else 0.0
            t = cam["target"]
            cam["target"] = (t[0] - right[0] * dx * upp + up[0] * dy * upp,
                             t[1] - right[1] * dx * upp + up[1] * dy * upp,
                             t[2] - right[2] * dx * upp + up[2] * dy * upp)
            self._p3d_cam = cam
            self._p3d_rpress = (event.x, event.y)
            self._p3d_render_memory()
        except Exception:
            pass

    def _p3d_on_rrelease(self, event):
        # v15.51: a right-drag panned — no menu. A plain right-click
        # opens the menu for the tag under the cursor (release coords).
        panned = getattr(self, "_p3d_rpanned", False)
        self._p3d_rpress = None
        self._p3d_rpanned = False
        if panned:
            try:
                self._p3d_render_memory()
            except Exception:
                pass
            return
        self._p3d_on_rclick(event)

    def _p3d_on_wheel(self, event):
        if self.tabs.get() not in ("Maker Studio", "Tag Design"):
            return
        d = getattr(event, "delta", 0)
        if event.num == 4 or d > 0:
            factor = 0.9
        elif event.num == 5 or d < 0:
            factor = 1.1
        else:
            return
        cam = self._p3d_cam
        if cam.get("dist"):
            cam["dist"] = max(20.0, min(5000.0, cam["dist"] * factor))
            self._p3d_render_memory()

    def _p3d_on_rclick(self, event):
        if self.tabs.get() not in ("Maker Studio", "Tag Design"):
            return
        multi = bool(getattr(self, "maker_multi", None) is not None and self.maker_multi.get())
        try:
            pick = self._p3d_pick(event.x, event.y) if multi else None
            if pick:
                m = tk.Menu(self, tearoff=0)
                m.add_command(label=f"Remove \u201c{pick}\u201d from preview",
                              command=lambda w=pick: self._maker_remove_from_preview(w))
                self._add_generate_entry(m, [pick])
                m.add_separator()
                m.add_command(label="Replace only picture…",
                              command=lambda w=pick: self.maker_library_pick(for_tag=w))
                m.add_command(label="Replace only word…",
                              command=lambda w=pick: self._maker_replace_word_only_prompt(w))
                _words = list(self._maker_preview_words())
                _others = [w for w in _words if w != pick]
                m.add_separator()
                if _others:
                    _sub = tk.Menu(m, tearoff=0)
                    for _w in _others:
                        _sub.add_command(label=_w,
                                         command=lambda w=_w, p=pick: self._maker_swap_tags(p, w))
                    m.add_cascade(label="Switch spots with", menu=_sub)
                else:
                    m.add_command(label="Switch spots with", state="disabled")
                try:
                    m.tk_popup(event.x_root, event.y_root)
                finally:
                    m.grab_release()
                return "break"
        except Exception:
            pass
        w = self.preview_word.get()
        if not w or w == "-":
            return "break"
        self._popup_generate_menu(event, w)
        return "break"

    def _p3d_on_dblclick(self, event):
        if self.tabs.get() not in ("Maker Studio", "Tag Design"):
            return
        cam = self._p3d_cam
        cam["azim"] = 45.0
        cam["elev"] = 44.0
        self._p3d_refit = True
        self._p3d_render_memory()

    def _p3d_on_resize(self, event):
        if getattr(self, "_p3d_resize_after", None) is not None:
            return
        try:
            self._p3d_resize_after = self.after(120, self._p3d_resize_fire)
        except Exception:
            pass

    def _p3d_resize_fire(self):
        self._p3d_resize_after = None
        if self.tabs.get() in ("Maker Studio", "Tag Design"):
            self._p3d_render_memory()

    # ---- Embedded browser preview (v15.40; the live page, in-program) ----
    def _embed_on_first_map(self, event):
        """v15.62: first window map → make sure the built-in 3D frame is
        showing (the old WebView2 controller creation lived here; the
        embed is gone, so this only refreshes visibility, one-shot).
        Bindtags deliver children's <Map> too, so only the root's own
        map counts."""
        try:
            if event.widget is not self:
                return
        except Exception:
            pass
        self._embed_cold_start()

    def _embed_cold_start(self):
        """v15.62: deferred cold-start visibility refresh (see __init__)."""
        if getattr(self, "_embed_cold_started", False):
            return
        self._embed_cold_started = True
        try:
            if self.tabs.get() in ("Maker Studio", "Tag Design"):
                self._embed_update_visibility()
        except Exception:
            pass

    def _embed_update_visibility(self):
        """v15.62: the embed is gone, so this only ever shows the
        built-in 3D frame on Maker/Tag Design and hides both preview
        frames on every other tab. The host frame stays created but
        permanently hidden."""
        try:
            tab = self.tabs.get()
            if tab in ("Maker Studio", "Tag Design"):
                self._embed_host.grid_remove()
                self._p3d_frame.grid()
                self._p3d_frame.tkraise()
            else:
                self._embed_host.grid_remove()
                self._p3d_frame.grid_remove()
        except Exception:
            pass


    def _build_maker(self, tab):
        """Maker Studio: 3D keychain tags. Words come from your Word Lists tab lists.

        v15.23 warm look: the panel is lavender, the header band is peach,
        and the controls sit in white cards — Your words / Edit / Picture —
        exactly as the approved warm preview. Every control is the same one
        as before, only reparented into cards; nothing was dropped."""
        try:
            tab.configure(fg_color=("#E9E4EE", "#23252F"))
        except Exception:
            pass
        self.header(tab, "Maker Studio — 3D keychain tags", first=True)
        ctk.CTkLabel(tab, text="Pick a word list for the tiles, or 📚 All words… for every word "
                               "(search + by theme, like the Library). Tiles and list flips never "
                               "change the tag preview — only putting a word on or loading the list does.",
                     text_color="gray", wraplength=320, anchor="w", justify="left").pack(fill="x", pady=(0, 6))
        # v15.23 warm header: peach band, heart, tagline, Theme selector.
        # (A true gradient is not something this widget kit can paint
        # behind controls, so the band is peach fading into the lavender
        # panel below — the warm look from the preview, honestly rendered.)
        _mhead = ctk.CTkFrame(tab, fg_color=("#FFE7D3", "#3A2E3A"), corner_radius=12)
        _mhead.pack(fill="x", pady=(0, 4))
        self._maker_heart = self._heart_image(26)
        ctk.CTkLabel(_mhead, image=self._maker_heart, text="").pack(side="left", padx=(10, 4), pady=8)
        ctk.CTkLabel(_mhead, text="Your words. Your way. We're here with you.",
                     font=ctk.CTkFont(size=13), text_color=("#7A5C8A", "#D8C6E8")).pack(side="left", pady=8)
        self._maker_theme_var = tk.StringVar(
            value="Dark" if ctk.get_appearance_mode() == "Dark" else "Soft pastel")
        ctk.CTkOptionMenu(_mhead, values=["Soft pastel", "Dark"], variable=self._maker_theme_var,
                          width=130, command=lambda v: self.set_light(v == "Soft pastel")).pack(
            side="right", padx=(0, 10), pady=8)
        ctk.CTkLabel(tab, text="This is your space", text_color=("#7A5C8A", "#C9B8DC"),
                     font=ctk.CTkFont(size=12)).pack(fill="x", pady=(0, 4))

        def _card(title, sub):
            f = ctk.CTkFrame(tab, fg_color=("#FFFFFF", "#2B2E3A"), corner_radius=14)
            f.pack(fill="x", pady=(0, 8))
            ctk.CTkLabel(f, text=title, anchor="w", font=ctk.CTkFont(size=14, weight="bold"),
                         text_color=("#8A6BC0", "#C9A8E8")).pack(fill="x", padx=12, pady=(10, 0))
            if sub:
                ctk.CTkLabel(f, text=sub, anchor="w", font=ctk.CTkFont(size=11),
                             text_color="gray").pack(fill="x", padx=12, pady=(0, 6))
            return f

        # ---- card 1: Your words ----
        _words_card = _card("Your words", "Add, edit, or remove words anytime — this list is yours.")
        # v13.24: the Word Lists tile design moves to the TOP of Maker —
        # pick a list, then tap a word tile (or drag it onto the tag, or
        # right-click it). The type-a-word row moved down below the tiles.
        _mrow = ctk.CTkFrame(_words_card, fg_color="transparent")
        _mrow.pack(fill="x", padx=12, pady=(2, 0))
        ctk.CTkLabel(_mrow, text="Current word list", anchor="w", width=110,
                     font=ctk.CTkFont(weight="bold", size=11), text_color="gray").pack(side="left")
        self.maker_list_var = tk.StringVar(value="Current words")
        self.maker_list_menu = ctk.CTkOptionMenu(_mrow, values=["Current words"], variable=self.maker_list_var,
                                                command=lambda _: self._maker_sync_words())
        self.maker_list_menu.pack(side="left", fill="x", expand=True)
        # v13.32: explicitly load this list into the multi-tag preview
        # (list flips alone never change the preview)
        ctk.CTkButton(_mrow, text="Load into preview", width=120, height=28,
                      fg_color="gray30", hover_color="#555",
                      command=self._maker_load_list_into_preview).pack(side="left", padx=(6, 0))
        ctk.CTkLabel(_words_card, text="Words — click a tile to put on tag · drag onto the tag · right-click for options",
                     anchor="w", font=ctk.CTkFont(weight="bold", size=11),
                     text_color="gray").pack(fill="x", padx=12, pady=(6, 0))
        # The tiles live in a fixed-height cage: a bare CTkScrollableFrame
        # asks the packer for more height than it was configured with,
        # which starved the widgets below it (the footer ended up 1px tall).
        _tiles_wrap = ctk.CTkFrame(_words_card, height=140, fg_color="transparent")
        _tiles_wrap.pack(fill="x", padx=12, pady=(2, 0))
        _tiles_wrap.pack_propagate(False)
        self.maker_tiles = ctk.CTkScrollableFrame(_tiles_wrap, fg_color="transparent")
        self.maker_tiles.pack(fill="both", expand=True)
        self._maker_tiles_inner = ctk.CTkFrame(self.maker_tiles, fg_color="transparent")
        self._maker_tiles_inner.pack(fill="x", anchor="n")
        self._maker_tile_btns = {}
        # v15.52: multiple tags are always on (the switch is gone) and
        # the tag count is always "all of the preview page" (the menu
        # is gone too). In their place: the preview page itself — the
        # words currently on tags, so you can see the set at a glance,
        # tap one to highlight it, or clear the whole page.
        ctk.CTkLabel(_words_card, text="On the preview page — tap a word to highlight its tag",
                     anchor="w", font=ctk.CTkFont(weight="bold", size=11),
                     text_color="gray").pack(fill="x", padx=12, pady=(6, 0))
        self._preview_chips = ctk.CTkScrollableFrame(_words_card, height=44, fg_color="transparent",
                                                     orientation="horizontal")
        self._preview_chips.pack(fill="x", padx=12, pady=(2, 0))
        self._preview_chip_btns = {}
        _prevrow = ctk.CTkFrame(_words_card, fg_color="transparent")
        _prevrow.pack(fill="x", padx=12, pady=(4, 6))
        ctk.CTkButton(_prevrow, text="🗑 Delete all tags", width=130, height=28, fg_color="#C0392B",
                      hover_color="#E74C3C", command=self._maker_clear_preview).pack(side="left")
        self._bed_status_label = ctk.CTkLabel(_prevrow, text="", anchor="w",
                                              font=ctk.CTkFont(size=11), text_color="gray")
        self._bed_status_label.pack(side="left", fill="x", expand=True, padx=(8, 0))

        # ---- card 2: Edit ----
        # v15.52: stripped to what Cody asked for — the typing box, the
        # renamed Put button, Suggest, and Create new tag. Gone: Edit
        # Current Tag, the three browser-view buttons (the in-app 3D
        # preview is the viewer now), and the Pick-a-word row (tiles
        # and the preview-page chips above are how words get picked).
        # The "Editing ..." line stays: it shows which tag is the
        # current highlighted one the Put button will change.
        _edit_card = _card("Edit", "Make changes with kindness.")
        self._multi_edit_label = ctk.CTkLabel(
            _edit_card, text="", anchor="w", text_color=ACCENT, wraplength=320,
            font=ctk.CTkFont(size=11, weight="bold"))
        self._multi_edit_label.pack(fill="x", padx=12, pady=(0, 2))
        # type-a-word — pushed down below the tiles (v13.24; it was at the very top)
        # v13.30: Suggest words lives here (was buried in the AI tab)
        row = ctk.CTkFrame(_edit_card, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(6, 6))
        self.maker_word = tk.StringVar(value="")
        self.maker_entry = ctk.CTkEntry(row, textvariable=self.maker_word,
                                        placeholder_text="Type your own word… e.g. see, bathroom, help")
        self.maker_entry.pack(side="left", fill="x", expand=True)
        self.maker_entry.bind("<Return>", lambda e: self.maker_apply_word())
        ctk.CTkButton(row, text="Put on current highlighted tag", width=150,
                      command=self.maker_put_on_highlighted_word).pack(side="left", padx=(6, 0))
        ctk.CTkButton(row, text="✨ Suggest", width=88, fg_color=AI_COLOR, hover_color=AI_HOVER,
                      command=self.ai_suggest_words).pack(side="left", padx=(6, 0))
        ctk.CTkButton(row, text="Create new tag", width=110, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self.maker_create_new_tag_word).pack(side="left", padx=(6, 0))

        # ---- card 3: Picture ----
        _pic_card = _card("Picture", "Choose pictures that feel right.")
        self._maker_heart_sm = self._heart_image(16)
        ctk.CTkLabel(_pic_card, image=self._maker_heart_sm, text="").place(
            relx=1.0, rely=0.0, anchor="ne", x=-12, y=10)
        row2 = ctk.CTkFrame(_pic_card, fg_color="transparent")
        row2.pack(fill="x", padx=12, pady=(8, 0))
        ctk.CTkButton(row2, text="Find pictures", fg_color="#FF6B6B", hover_color="#FF8E8E",
                      command=self.maker_find_pictures).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row2, text="Select from library…", fg_color="#9B59B6", hover_color="#B07CC6",
                      command=self.maker_library_pick).pack(side="left", fill="x", expand=True, padx=(6, 0))
        ctk.CTkButton(row2, text="Upload my image…", fg_color="gray30", hover_color="#555",
                      command=self.maker_upload).pack(side="left", fill="x", expand=True, padx=(6, 0))

        self.maker_status = ctk.CTkLabel(_pic_card, text="My picture library — saved on this computer",
                                         text_color="gray", anchor="w", font=ctk.CTkFont(size=11))
        self.maker_status.pack(fill="x", padx=12, pady=(6, 2))

        self.maker_gallery = ctk.CTkScrollableFrame(_pic_card, height=120, fg_color="transparent")
        self.maker_gallery.pack(fill="both", expand=True, padx=12, pady=(2, 0))
        self._maker_thumbs = []  # keep PhotoImage refs
        # v15.8: the strip is a PERMANENT local library now (it used to be filled
        # by an internet search every time a word changed). Search results only
        # appear here while you are looking at them; the library is home.
        self._maker_strip_mode = "library"
        self._maker_lib_sel = None        # (word, path) chosen in the library strip
        self._maker_lib_rows = {}
        _librow = ctk.CTkFrame(_pic_card, fg_color="transparent")
        _librow.pack(fill="x", padx=12, pady=(4, 6))
        ctk.CTkButton(_librow, text="↻ My library", width=104, height=28, fg_color="gray30",
                      hover_color="#555", command=self._maker_lib_show).pack(side="left")
        self._maker_lib_put_btn = ctk.CTkButton(
            _librow, text="Put on this tag", height=28, fg_color=TEAL, hover_color=TEAL_HOVER,
            text_color="white", command=self._maker_lib_put_on_tag, state="disabled")
        self._maker_lib_put_btn.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self._maker_lib_new_btn = ctk.CTkButton(
            _librow, text="Create new tag", height=28, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color="white", command=self._maker_lib_new_tag, state="disabled")
        self._maker_lib_new_btn.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self._board_thumbs = []  # board preview PhotoImage refs (v13.10-exp)
        self._pic_pickers = {}  # popup id -> (win, rows, thumbs)
        # (v13.24: the two dropdowns and the word tiles now live at the TOP
        # of this tab — see above; the old clickable listbox became tiles.)
        self._maker_sync_words()
        self._maker_refresh_preview_chips()
        self.after(400, self._maker_lib_refresh)

        ctk.CTkLabel(tab, text="Pictures in your library live in your icons folder and "
                               "work offline. Find pictures and uploads add new ones to it.",
                     text_color="gray", font=ctk.CTkFont(size=10), wraplength=320,
                     anchor="w", justify="left").pack(fill="x", pady=(6, 0))
        # v15.23 warm microcopy, under the Picture card.
        ctk.CTkLabel(tab, text="Every tag is a step toward being understood.",
                     text_color=("#7A5C8A", "#C9B8DC"), font=ctk.CTkFont(size=11)).pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(tab, text="You're building your voice. We're here for every step.",
                     text_color=("#7A5C8A", "#C9B8DC"), font=ctk.CTkFont(size=11)).pack(fill="x", pady=(0, 2))

    def _saved_list_words(self, name):
        """Words of a saved list — stored values may be a list or old-style text."""
        v = self.word_lists.get(name)
        if isinstance(v, (list, tuple)):
            return [str(w).strip() for w in v if str(w).strip()]
        if isinstance(v, str):
            return [w.strip() for w in v.replace(",", "\n").split("\n") if w.strip()]
        return []

    def _maker_list_choices(self):
        """Sources for Maker's 'Current word list' dropdown (v13.10-exp)."""
        choices = ["Current words"] + sorted(self.word_lists.keys())
        choices += [f"Preset: {n}" for n in core.preset_list_names()]
        return choices

    def _maker_words_for(self, choice):
        if choice == "Current words":
            return self.get_words()
        if choice.startswith("Preset: "):
            return core.preset_words(choice[len("Preset: "):])
        return self._saved_list_words(choice)

    def _maker_pick_word(self, word):
        """Dropdown / word tile: picking a word puts it on the tag."""
        if not word or word == "—":
            return
        # v14: multi mode — picking a word changes the locked preview
        # set by hand: replace the highlighted tag, or add a new tag.
        if getattr(self, "maker_multi", None) is not None and self.maker_multi.get():
            self.maker_word.set(word)
            sel = getattr(self, "_selected_multi", None)
            if sel and sel != word and sel in self._maker_preview_words():
                self._maker_put_on_highlighted(word)
            else:
                if sel and sel not in self._maker_preview_words():
                    self._select_multi_tag(None)
                self._maker_add_to_preview(word)
            self._maker_style_tiles()
            return
        self.maker_word.set(word)
        self.maker_apply_word()
        self._maker_style_tiles()

    def _maker_sync_words(self):
        """Maker's word bar: 'Current word list' chooses the source for the
        tiles and the quick-pick menu only (v13.10-exp).

        v13.32: changing the list does NOT touch the 3D preview. The preview
        stays on whatever you are working on until you put a word on the tag
        or clear it yourself. Top-bar word menu keeps all available words."""
        try:
            choices = self._maker_list_choices()
            if self.maker_list_var.get() not in choices:
                self.maker_list_var.set("Current words")
            self.maker_list_menu.configure(values=choices)
            words = self._maker_words_for(self.maker_list_var.get())
            self._maker_render_tiles(words)
            # Top-bar dropdown: all words (library sources), always keep the
            # current preview word in the list so the OptionMenu does not reset it.
            try:
                all_w = self._all_words_for_menu()
                cur = self.preview_word.get()
                if cur and cur != "-" and cur not in all_w:
                    all_w = [cur] + all_w
                self.word_menu.configure(values=all_w or ["-"])
                if cur and cur != "-":
                    self.word_menu.set(cur)
            except Exception:
                pass
        except Exception:
            pass

    def _all_words_for_menu(self):
        """Every word from lists, presets, common AAC, boards, AI — A→Z."""
        seen, out = set(), []
        def add(w):
            w = str(w).strip()
            if not w:
                return
            k = w.lower()
            if k in seen:
                return
            seen.add(k)
            out.append(w)
        try:
            for w, _kind, _when in core.ai_words():
                add(w)
            for w in self.get_words():
                add(w)
            for name in list(self.word_lists):
                for w in self._saved_list_words(name):
                    add(w)
            for w in core.COMMON_WORDS:
                add(w)
            for name, ws in aac_boards.TOPIC_PACKS.items():
                for w in ws:
                    add(w)
            for name in core.preset_list_names():
                for w in core.preset_words(name):
                    add(w)
        except Exception:
            pass
        out.sort(key=lambda x: x.lower())
        return out

    def _bed_dims(self):
        """Printer bed (X, Y) in mm for the Maker preview (v15.52).

        The presets are cubic, so X == Y == the first number; anything
        unparsable falls back to Cody's 220 bed.
        """
        try:
            v = float(str(self.bed_size.get()).split("×")[0].strip())
            if v > 0:
                return (v, v)
        except Exception:
            pass
        return (220.0, 220.0)

    def _maker_preview_words(self):
        """Words the Maker multi-preview is currently working on (locked set).

        v14.1: the FIRST time the preview needs words, whatever it would
        show becomes the locked set. Before this, a set that had never
        been seeded (e.g. the app starts with Multiple tags already on
        from saved settings, so nothing ever "toggles" it) stayed unset
        and silently followed the 'Current word list' dropdown — picking
        a preset swapped every tag in the preview. Now the tags on screen
        are the set, from the first moment they appear."""
        ww = getattr(self, "_maker_work_words", None)
        if ww is not None:
            return list(ww)
        try:
            words = list(self._maker_words_for(self.maker_list_var.get()))
        except Exception:
            words = []
        # v15.52: never lock an EMPTY seed. With multi always on, the
        # first preview call can land during startup before the word
        # lists are loaded — locking [] then would brick the preview
        # forever (the set would never reseed). An empty list just
        # means "not seeded yet"; an explicit empty set (Delete all,
        # Load into preview with an empty list) still assigns [] above
        # via the ww-is-not-None branch and stays empty.
        if words:
            self._maker_work_words = words
            return list(words)
        return []
    def _maker_load_list_into_preview(self):
        """v13.32: copy the selected word list into the locked preview set.
        Use this when you want the multi-tag preview to show a different list;
        flipping the dropdown alone never does that."""
        try:
            words = list(self._maker_words_for(self.maker_list_var.get()))
        except Exception:
            words = []
        self._maker_work_words = words
        self._multi_fp = None
        if getattr(self, "_multi_geo", None) is not None:
            self._multi_geo = {}
        self._select_multi_tag(None)
        if self.maker_multi.get():
            self.draw()
        elif words:
            # single-tag: put the first word on the tag if nothing is there yet
            cur = self.preview_word.get()
            if (not cur or cur == "-") and words:
                self._maker_pick_word(words[0])
            else:
                self.draw()
        self.flash_status(
            f"Loaded {len(words)} word(s) into the preview" if words else "That list is empty",
            ok=bool(words))


    def _defer_once(self, key, fn, delay=20):
        """Run fn once, shortly after the current event finishes; repeat requests merge into one.
        (v14.8) The word tiles are rebuilt by destroying every button - doing that INSIDE a
        tile's own click handler, or several times in a row while you click quickly, left the
        tile area empty."""
        tok = f"_defer_{key}"
        if getattr(self, tok, None):
            return

        def run():
            setattr(self, tok, None)
            try:
                fn()
            except Exception:
                log.exception("%s failed", key)
        try:
            setattr(self, tok, self.after(delay, run))
        except Exception:
            setattr(self, tok, None)
            run()

    def _drag_cancel(self):
        """Drop any half-finished drag (and its purple ghost label)."""
        self._drag_state = None
        g = getattr(self, "_drag_ghost", None)
        if g is not None:
            try:
                g.destroy()
            except Exception:
                pass
            self._drag_ghost = None

    # ---- v13.24: word tiles in Maker (the Word Lists design) + drag & drop
    def _maker_render_tiles(self, words=None):
        """Rebuild Maker's word tiles (v14.8: deferred + merged; see _defer_once)."""
        self._maker_tiles_pending = words
        self._defer_once("maker_tiles", self._maker_render_tiles_now)

    def _maker_render_tiles_now(self):
        """Build the NEW tiles first, then swap them in and drop the old ones - if anything
        goes wrong the tiles you already had stay on screen instead of an empty area."""
        old = getattr(self, "_maker_tiles_inner", None)
        parent = getattr(self, "maker_tiles", None)
        if old is None or parent is None:
            return
        words = getattr(self, "_maker_tiles_pending", None)
        if words is None:
            try:
                words = self._maker_words_for(self.maker_list_var.get())
            except Exception:
                words = []
        self._drag_cancel()
        new = ctk.CTkFrame(parent, fg_color="transparent")
        btns = {}
        try:
            if not words:
                ctk.CTkLabel(new, text="No words in this list yet.",
                             text_color="gray", font=ctk.CTkFont(size=11),
                             justify="left", anchor="w").grid(row=0, column=0, sticky="w", padx=2, pady=4)
            else:
                for c in range(3):
                    new.grid_columnconfigure(c, weight=1, uniform="mtile")
                for i, w in enumerate(words):
                    r, c = divmod(i, 3)
                    b = ctk.CTkButton(new, text=w, height=36, corner_radius=8)
                    b.grid(row=r, column=c, sticky="ew", padx=2, pady=2)
                    self._bind_drag(b, ("word", w), w,
                                    lambda word=w: self._maker_pick_word(word))
                    b.bind("<Button-3>", lambda e, word=w: self._maker_tile_menu(e, word))
                    b.bind("<Button-2>", lambda e, word=w: self._maker_tile_menu(e, word))
                    b.bind("<Double-Button-1>", lambda e, word=w: self._maker_put_on_highlighted(word))
                    btns[w] = b
            new.pack(fill="x", anchor="n")
        except Exception:
            log.exception("Maker tiles rebuild failed - keeping the old tiles")
            try:
                new.destroy()
            except Exception:
                pass
            return
        self._maker_tiles_inner = new
        self._maker_tile_btns = btns
        try:
            old.destroy()
        except Exception:
            pass
        self._maker_style_tiles()

    def _maker_style_tiles(self):
        """The word on the tag stays lit purple, like the Word Lists tiles.
        The multi-highlighted tag's word also lights up (coral)."""
        sel = self.preview_word.get()
        multi_sel = getattr(self, "_selected_multi", None)
        for w, b in getattr(self, "_maker_tile_btns", {}).items():
            try:
                if multi_sel and w == multi_sel:
                    b.configure(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white",
                                font=ctk.CTkFont(size=12, weight="bold"))
                elif w == sel:
                    b.configure(fg_color="#9B59B6", hover_color="#B07CC6", text_color="white",
                                font=ctk.CTkFont(size=12, weight="bold"))
                else:
                    b.configure(fg_color=("gray90", "gray25"), hover_color=("#ccd6e4", "#3d4b63"),
                                text_color=("gray10", "#DCE4EE"), font=ctk.CTkFont(size=12))
            except Exception:
                pass

    def _maker_add_to_preview(self, word, quiet=False):
        """v14: put a word on a NEW tag in the locked preview set (multi
        mode, nothing highlighted). The set only changes by your own hand:
        put a word on, load a list, or remove a tag."""
        if not word or word == "\u2014":
            return
        words = list(self._maker_preview_words())
        if word in words:
            self._select_multi_tag(word)
            self.draw()
            if not quiet:
                self.flash_status(f"\u201c{word}\u201d is already in the preview \u2014 highlighted it", ok=True)
            return
        words.append(word)
        self._maker_work_words = words
        if getattr(self, "_multi_geo", None) is not None:
            self._multi_geo.pop(word, None)
        self._select_multi_tag(word)
        self.save()
        self.draw()
        if not quiet:
            self.flash_status(f"Added \u201c{word}\u201d to the preview ({len(words)} tag(s))", ok=True)

    def _maker_remove_from_preview(self, word):
        """v14: take one tag out of the locked preview set (right-click a
        preview tag -> Remove from preview)."""
        words = list(self._maker_preview_words())
        if word not in words:
            return
        words = [w for w in words if w != word]
        self._maker_work_words = words
        if getattr(self, "_selected_multi", None) == word:
            self._select_multi_tag(None)
        if getattr(self, "_multi_geo", None) is not None:
            self._multi_geo.pop(word, None)
        self.save()
        self.draw()
        self._maker_refresh_preview_chips()
        self.flash_status(f"Removed \u201c{word}\u201d from the preview ({len(words)} left)", ok=True)

    def _maker_refresh_preview_chips(self):
        """v15.52: rebuild the 'On the preview page' chips from the locked
        preview set, in preview order. Tap a chip to highlight that tag
        (same as clicking the tag itself). Safe to call before the Maker
        card exists (early refreshes) — it just returns."""
        try:
            frame = getattr(self, "_preview_chips", None)
            if frame is None or not frame.winfo_exists():
                return
            for child in frame.winfo_children():
                try:
                    child.destroy()
                except Exception:
                    pass
            self._preview_chip_btns = {}
            sel = getattr(self, "_selected_multi", None)
            for w in self._maker_preview_words():
                if not w:
                    continue
                hl = bool(sel and w == sel)
                b = ctk.CTkButton(frame, text=w, width=0, height=26,
                                  fg_color=(ACCENT if hl else ("gray90", "gray25")),
                                  hover_color=(ACCENT_HOVER if hl else ("#ccd6e4", "#3d4b63")),
                                  text_color=("white" if hl else ("gray10", "#DCE4EE")),
                                  font=ctk.CTkFont(size=12, weight="bold" if hl else "normal"),
                                  command=lambda ww=w: self._select_multi_tag(ww))
                b.pack(side="left", padx=(0, 6), pady=4)
                self._preview_chip_btns[w] = b
        except Exception:
            pass

    def _maker_clear_preview(self):
        """v15.52: delete all current tags in the preview (the locked
        set). 'Load into preview' above puts a list back, so nothing is
        lost for good — the words stay in their lists."""
        try:
            self._maker_work_words = []
            self._multi_fp = None
            if getattr(self, "_multi_geo", None) is not None:
                self._multi_geo = {}
            self._select_multi_tag(None)
            self.save()
            self.draw()
            self._maker_refresh_preview_chips()
            self.flash_status("Preview cleared — no tags on the page", ok=True)
        except Exception:
            pass

    def _maker_ensure_list_word(self, w):
        """Make sure a typed word is on the Current words list (the way
        Put on tag always did), returning the list's own casing."""
        try:
            words = self.get_words()
            for x in words:
                if x.lower() == w.lower():
                    return x
            words.append(w)
            self.set_words(words)
        except Exception:
            pass
        return w

    def maker_put_on_highlighted_word(self):
        """v15.52: the Edit box's renamed Put button — the typed word
        goes onto the current highlighted tag (replacing it). With
        nothing highlighted it joins the page as a new tag instead."""
        w = self.maker_word.get().strip()
        if not w:
            return
        w = self._maker_ensure_list_word(w)
        self._no_auto_pic.discard(w)
        if getattr(self, "_selected_multi", None):
            self._maker_put_on_highlighted(w)
            try:
                self._maker_style_tiles()
            except Exception:
                pass
            self._maker_refresh_preview_chips()
            self.draw()
            self.flash_status(f"“{w}” is on the highlighted tag now", ok=True)
        else:
            self._maker_add_to_preview(w)
        try:
            self.preview_word.set(w)
            self._preview_word_explicit = True
        except Exception:
            pass

    def _maker_create_new_tag(self, word):
        """v15.54: the given word becomes a brand-new tag on the preview
        page (and gets highlighted). If it is already on the page, it just
        gets highlighted. Shared by the tile and Word Bank right-clicks."""
        w = (word or "").strip()
        if not w:
            return
        w = self._maker_ensure_list_word(w)
        self._no_auto_pic.discard(w)
        self._maker_add_to_preview(w)
        try:
            self.preview_word.set(w)
            self._preview_word_explicit = True
        except Exception:
            pass

    def maker_create_new_tag_word(self):
        """v15.52: the Edit box's new button — the typed word becomes a
        brand-new tag on the preview page (and gets highlighted). If it
        is already on the page, it just gets highlighted."""
        w = self.maker_word.get().strip()
        if not w:
            return
        w = self._maker_ensure_list_word(w)
        self._no_auto_pic.discard(w)
        self._maker_add_to_preview(w)
        try:
            self.preview_word.set(w)
            self._preview_word_explicit = True
        except Exception:
            pass

    def _maker_bind_new_word_picture(self, word, picture_path=None):
        """v15.59: the picture a freshly replaced highlighted tag shows.
        An explicitly sent picture wins; else the new word's own saved
        picture — its override while that file still exists, else its
        local library picture. Returns True when a picture is bound."""
        if picture_path:
            self.overrides[word] = str(picture_path)
            self._no_auto_pic.discard(word)
            return True
        cur = self.overrides.get(word)
        if cur and Path(cur).exists():
            self._no_auto_pic.discard(word)
            return True
        if cur:
            self.overrides.pop(word, None)
        try:
            o = self._opts_for_word(word)
            o_local = dc_replace(o, icon_source=core.SOURCES[1])
            icon_word = self.search_as.get(word) or word
            p, _info = core.find_icon(icon_word, o_local, None,
                                       self.variants.get(word, 0))
        except Exception:
            p = None
        if p:
            self.overrides[word] = str(p)
            self._no_auto_pic.discard(word)
            return True
        return False

    def _maker_replace_highlighted(self, word, picture_path=None):
        """v15.59: replace the highlighted tag WHOLE — word AND picture.
        The tag slot keeps its look (design overrides move old → new),
        but the word becomes `word` and the picture becomes `word`'s
        picture (or `picture_path` when one was sent with it). Returns
        the new word, or None when no tag is highlighted / the word is
        not on the preview page."""
        sel = getattr(self, "_selected_multi", None)
        if not sel or not (getattr(self, "maker_multi", None) and self.maker_multi.get()):
            return None
        if not word:
            return None
        if word == sel:
            # Same word: only the picture changes (if one was sent).
            if picture_path:
                self.overrides[sel] = str(picture_path)
                self._no_auto_pic.discard(sel)
                if getattr(self, "_multi_geo", None) is not None:
                    self._multi_geo.pop(sel, None)
                self._multi_fp = None
                self.draw()
            return sel
        words = list(self._maker_preview_words())
        try:
            idx = next(i for i, w in enumerate(words) if w == sel)
        except StopIteration:
            return None
        # Move design overrides from old word → new word (tag keeps its look)
        if sel in self.design_overrides:
            self.design_overrides[word] = self.design_overrides.pop(sel)
        words[idx] = word
        self._maker_work_words = words
        self._selected_multi = word
        self._no_auto_pic.discard(word)
        self._maker_bind_new_word_picture(word, picture_path)
        try:
            self._multi_edit_label.configure(
                text=f"Editing “{word}” only — change shape/style, then click empty space to finish")
        except Exception:
            pass
        if getattr(self, "_multi_geo", None) is not None:
            self._multi_geo.pop(sel, None)
            self._multi_geo.pop(word, None)
        self._multi_fp = None
        try:
            self.preview_word.set(word)
            self._preview_word_explicit = True
            self.maker_word.set(word)
            self.word_menu.set(word)
        except Exception:
            pass
        self.save()
        self.draw()
        self._maker_refresh_preview_chips()
        return word

    def _maker_swap_tags(self, a, b):
        """v15.63: right-click a tag → Switch spots with → pick another
        preview tag, and the two flop places on the viewer grid. The
        tags themselves (words, pictures, looks) do not change, and the
        selection does not move off its tag."""
        words = list(self._maker_preview_words())
        if a not in words or b not in words or a == b:
            return False
        ia, ib = words.index(a), words.index(b)
        words[ia], words[ib] = words[ib], words[ia]
        self._maker_work_words = words
        self._multi_fp = None
        self.save()
        self.draw()
        self._maker_refresh_preview_chips()
        self.flash_status(f"“{a}” and “{b}” switched spots", ok=True)
        return True

    def _maker_replace_word_only_prompt(self, word):
        """v15.60: right-click a tag → Replace only word: ask for the new
        word; the tag's picture stays exactly as it is."""
        if not word:
            return
        dlg = ctk.CTkInputDialog(text=f"New word for the “{word}” tag (picture stays the same):",
                                 title="Replace only word")
        try:
            val = dlg.get_input()
        except Exception:
            val = None
        if not val or not val.strip():
            return
        self._maker_replace_word_only(word, val.strip())

    def _maker_replace_word_only(self, old, new):
        """v15.60: swap the word on one preview tag and NOTHING else — the
        tag keeps its picture (pinned as the new word's override) and its
        look (design overrides move old → new)."""
        if not old or not new or new == old:
            return
        words = list(self._maker_preview_words())
        if new in words:
            # Already on the page — just highlight it instead of doubling it.
            self._select_multi_tag(new)
            self.flash_status(f"“{new}” is already on the page — highlighted it", ok=True)
            return
        try:
            idx = next(i for i, w in enumerate(words) if w == old)
        except StopIteration:
            return
        # The picture the tag shows right now, pinned to the new word.
        eff = None
        cur = self.overrides.get(old)
        if cur and Path(cur).exists():
            eff = str(cur)
        else:
            try:
                o = self._opts_for_word(old)
                o_local = dc_replace(o, icon_source=core.SOURCES[1])
                p, _info = core.find_icon(self.search_as.get(old) or old, o_local, None,
                                           self.variants.get(old, 0))
                if p:
                    eff = str(p)
            except Exception:
                eff = None
        if old in self.design_overrides:
            self.design_overrides[new] = self.design_overrides.pop(old)
        words[idx] = new
        self._maker_work_words = words
        self._selected_multi = new
        if eff:
            self.overrides[new] = eff
            self._no_auto_pic.discard(new)
        else:
            self.overrides.pop(new, None)
        try:
            self._multi_edit_label.configure(
                text=f"Editing “{new}” only — change shape/style, then click empty space to finish")
        except Exception:
            pass
        if getattr(self, "_multi_geo", None) is not None:
            self._multi_geo.pop(old, None)
            self._multi_geo.pop(new, None)
        self._multi_fp = None
        try:
            self.preview_word.set(new)
            self._preview_word_explicit = True
            self.maker_word.set(new)
            self.word_menu.set(new)
        except Exception:
            pass
        self.save()
        self.draw()
        self._maker_refresh_preview_chips()
        self.flash_status(f"“{new}” is on the tag — picture unchanged", ok=True)

    def _maker_put_on_highlighted(self, word):
        """v13.31: with a multi-tag highlighted, put `word` onto THAT tag only
        (replaces the highlighted word in the preview working set). Does not
        change any other tags. v13.32: edits the locked preview set, not the
        list dropdown source, so flipping lists does not undo your work.
        v15.59: the picture follows the new word too (see
        _maker_replace_highlighted) — the tag is replaced whole."""
        sel = getattr(self, "_selected_multi", None)
        if not sel or not (getattr(self, "maker_multi", None) and self.maker_multi.get()):
            # nothing highlighted → normal single-tag pick
            self._maker_pick_word(word)
            return
        if not word or word == sel:
            return
        got = self._maker_replace_highlighted(word)
        if got is None:
            self._maker_pick_word(word)
            return
        self.flash_status(f"“{word}” is now on the highlighted tag", ok=True)

    def _maker_tile_menu(self, event, word):
        """Right-click a Maker word tile."""
        m = tk.Menu(self, tearoff=0)
        sel = getattr(self, "_selected_multi", None)
        multi_on = getattr(self, "maker_multi", None) and self.maker_multi.get()
        if multi_on and sel and sel != word:
            m.add_command(label=f"Put on highlighted tag (“{sel}” → “{word}”)",
                          command=lambda: self._maker_put_on_highlighted(word))
            m.add_separator()
        m.add_command(label="Put on tag", command=lambda: self._maker_pick_word(word))
        m.add_command(label="Create new tag",
                      command=lambda: self._maker_create_new_tag(word))
        m.add_command(label="Find pictures for this word",
                      command=lambda: (self.maker_word.set(word), self.maker_apply_word()))
        self._add_generate_entry(m, [word])
        m.add_separator()
        m.add_command(label="Send word to PDF Studio", command=lambda: self._tile_send_pdf(word))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()
        return "break"

    def _maker_pic_menu(self, event, word, path):
        """Right-click a gallery picture in Maker."""
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="Put this picture on the tag",
                      command=lambda: self.maker_use_picture(word, path))
        self._add_generate_entry(m, [word])
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()
        return "break"

    # ---- drag & drop onto the canvas (word tiles + gallery pictures).
    # A press that barely moves is a click; a real drag shows a ghost that
    # follows the pointer, and releasing over the canvas applies the item.
    # The binds go on the widget AND its children (CTk buttons are frames
    # with inner widgets that receive the actual events). Handlers are
    # state-guarded, so duplicate deliveries are harmless.
    def _bind_drag(self, widget, payload, text, on_click, image=None):
        widget.bind("<ButtonPress-1>",
                    lambda e: self._drag_begin(e, payload, text, image))
        widget.bind("<B1-Motion>", self._drag_move)
        widget.bind("<ButtonRelease-1>",
                    lambda e: self._drag_end(e, on_click))
        for child in widget.winfo_children():
            self._bind_drag(child, payload, text, on_click, image)

    def _drag_begin(self, event, payload, text, image=None):
        self._drag_cancel()                # v14.8: never leave a stale ghost from a missed release
        self._drag_state = {"payload": payload, "x0": event.x_root, "y0": event.y_root,
                            "active": False, "text": text, "image": image}

    def _drag_move(self, event):
        st = getattr(self, "_drag_state", None)
        if not st:
            return
        if not st["active"]:
            if abs(event.x_root - st["x0"]) + abs(event.y_root - st["y0"]) < 8:
                return
            st["active"] = True
            g = tk.Label(self, text=st["text"], bg="#9B59B6", fg="white",
                         font=("Arial", 11, "bold"), padx=10, pady=5)
            if st.get("image") is not None:
                g.configure(image=st["image"], text="")
            self._drag_ghost = g
        g = self._drag_ghost
        g.place(x=event.x_root - self.winfo_rootx() + 12,
                y=event.y_root - self.winfo_rooty() + 10)
        g.lift()

    def _drag_end(self, event, on_click):
        st = getattr(self, "_drag_state", None)
        self._drag_state = None
        g = getattr(self, "_drag_ghost", None)
        if g is not None:
            try:
                g.destroy()
            except Exception:
                pass
            self._drag_ghost = None
        if not st:
            return
        if not st.get("active"):
            on_click()          # it was a plain click, not a drag
            return "break"
        if self._over_canvas(event.x_root, event.y_root):
            kind, data = st["payload"]
            if kind == "word":
                self._maker_pick_word(data)
            elif kind == "picture":
                word, path = data
                self.maker_use_picture(word, path)
        return "break"

    def _over_canvas(self, x_root, y_root):
        try:
            c = self.canvas
            return (c.winfo_rootx() <= x_root <= c.winfo_rootx() + c.winfo_width()
                    and c.winfo_rooty() <= y_root <= c.winfo_rooty() + c.winfo_height())
        except Exception:
            return False

    def save_opensymbols_secret(self):
        self.set_opensymbols_secret(self.os_secret_entry.get())
        if self.get_opensymbols_secret():
            self.flash_status("OpenSymbols secret saved \u2014 Mulberry / Sclera / Tawasol unlocked", ok=True)
        else:
            self.flash_status("OpenSymbols secret cleared", ok=True)

    def _opensymbols_expired(self, where):
        """Token expired: notify, link the renewal page, retry on demand."""
        if getattr(self, "_os_expired_open", False):
            return
        self._os_expired_open = True
        w = ctk.CTkToplevel(self)
        w.title("OpenSymbols token expired")
        w.geometry("470x400")
        try:
            w.transient(self)
            x = self.winfo_rootx() + (self.winfo_width() - 470) // 2
            y = self.winfo_rooty() + (self.winfo_height() - 400) // 2
            w.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        except Exception:
            pass
        w.after(200, w.lift)
        pad = {"padx": 20, "pady": (10, 0)}
        ctk.CTkLabel(w, text="Your OpenSymbols token has expired.",
                     font=ctk.CTkFont(weight="bold", size=14), anchor="w").pack(fill="x", **pad)
        ctk.CTkLabel(w, text="Picture search kept working with the other libraries. "
                             "Paste a fresh token below and it picks up on its own — "
                             "no need to go anywhere else.",
                     wraplength=420, justify="left", anchor="w").pack(fill="x", **pad)

        def open_api_page():
            import webbrowser
            webbrowser.open("https://www.opensymbols.org/api")

        msg = ctk.CTkLabel(w, text="", anchor="w", text_color="#6bcb77", wraplength=420, justify="left")
        token_entry = ctk.CTkEntry(w, show="\u2022", placeholder_text="paste new token here")
        token_entry.pack(fill="x", padx=20, pady=(12, 0))

        def save_and_retry():
            value = token_entry.get().strip()
            if not value:
                msg.configure(text="Paste the token first.", text_color="#d9a441")
                return
            self.set_opensymbols_secret(value)
            try:
                self.os_secret_entry.delete(0, "end")
                self.os_secret_entry.insert(0, value)
            except Exception:
                pass
            msg.configure(text="Saved — retrying the search…", text_color="#6bcb77")
            w.after(600, retry)

        def retry():
            self._os_expired_open = False
            w.destroy()
            if where == "maker":
                self.maker_find_pictures()
            else:
                self.pdf_find_pictures()

        def close():
            self._os_expired_open = False
            w.destroy()

        w.protocol("WM_DELETE_WINDOW", close)
        ctk.CTkButton(w, text="Open opensymbols.org/api to get a token", command=open_api_page).pack(fill="x", padx=20, pady=(12, 0))
        msg.pack(fill="x", padx=20, pady=(8, 0))
        brow = ctk.CTkFrame(w, fg_color="transparent")
        brow.pack(fill="x", padx=20, pady=(4, 0))
        ctk.CTkButton(brow, text="Save & retry search", fg_color="#4ECDC4", hover_color="#6EDDD6",
                      command=save_and_retry).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(brow, text="Later", fg_color="gray30", hover_color="#555", width=90,
                      command=close).pack(side="left", padx=(8, 0))

    def maker_apply_word(self):
        w = self.maker_word.get().strip()
        if not w:
            return
        self._no_auto_pic.discard(w)
        # ensure word is on the list
        words = self.get_words()
        if not any(x.lower() == w.lower() for x in words):
            words.append(w)
            self.set_words(words)
        else:
            for x in words:
                if x.lower() == w.lower():
                    w = x
                    break
        self.preview_word.set(w)
        self._preview_word_explicit = True
        try:
            self.word_menu.set(w)
        except Exception:
            pass
        self.on_preview_word()
        # v14: in multi mode the applied word also joins the locked preview set
        if getattr(self, "maker_multi", None) is not None and self.maker_multi.get():
            self._maker_add_to_preview(w, quiet=True)
        self.flash_status(f"“{w}” on the keychain — its saved picture is used; "
                          "pick another from your library below if you like", ok=True)
        self._maker_lib_refresh()

    def _save_upload_to_library(self, word, src):
        """Copy a user-picked image into the Library (icons/) as the word's
        picture and remember it — from then on it's part of the database
        and searched first, like any downloaded picture (v13.21)."""
        try:
            import io
            from PIL import Image
            im = Image.open(src).convert("RGBA")
            buf = io.BytesIO()
            im.save(buf, "PNG")
            out = core._persist_icon(buf.getvalue(), word, 0, term=word)
            core._remember_icon(word, 0, out, term=word)
            return str(out)
        except Exception as e:
            log.debug("Library copy of upload failed for '%s': %s", word, e)
            return str(src)

    def maker_upload(self):
        w = self.maker_word.get().strip() or self.preview_word.get()
        if not w or w == "-":
            return messagebox.showinfo(APP_NAME, "Type a word first.")
        path = filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.webp")])
        if not path:
            return
        # ensure word on list
        self.maker_word.set(w)
        self.maker_apply_word()
        w = self.preview_word.get()
        self.overrides[w] = self._save_upload_to_library(w, path)
        self.changed()
        self.flash_status(f"Your image applied to \u201c{w}\u201d and saved in your Library", ok=True)
        self._maker_lib_refresh()

    def _library_picture_items(self):
        """Every picture in the current database (the Library): the icon
        index plus any image files sitting in the icons folder."""
        items, seen = [], set()
        try:
            index = core._load_icon_index()
            for key, rec in index.items():
                if not isinstance(rec, dict):
                    continue
                fname = rec.get("file")
                if not fname:
                    continue
                path = core.ICON_DIR / fname
                if path.exists():
                    rk = str(path.resolve())
                    if rk not in seen:
                        seen.add(rk)
                        items.append({"word": rec.get("word") or key, "path": path})
        except Exception:
            pass
        try:
            for f in sorted(core.ICON_DIR.iterdir()):
                if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
                    rk = str(f.resolve())
                    if rk not in seen:
                        seen.add(rk)
                        items.append({"word": f.stem, "path": f})
        except Exception:
            pass
        items.sort(key=lambda it: str(it["word"]).lower())
        # v15.52: the Maker picture box shows these in exactly the
        # Library tab's order. The Library's default sort is By theme
        # (Fitzgerald categories, A→Z inside each), so group the same
        # way here; honour an explicit A→Z / Z→A Library sort, and keep
        # theme order for By source / AI only (those groupings need the
        # Library's source info, which this list doesn't carry — and
        # AI only must never hide pictures from a picker).
        try:
            sort = None
            if hasattr(self, "lib_sort"):
                sort = self.lib_sort.get()
            if sort == "Z → A":
                items.reverse()
            elif sort != "A → Z":
                order = list(aac_boards.CATEGORIES.keys())
                pos = {c: i for i, c in enumerate(order)}
                buckets = {}
                for it in items:
                    cat = aac_boards.category_for(str(it["word"]))
                    if cat not in pos:
                        cat = "Uncategorized"
                    buckets.setdefault(cat, []).append(it)
                out = []
                for cat in order:
                    out.extend(buckets.get(cat) or [])
                for cat, group in buckets.items():
                    if cat not in pos:
                        out.extend(group)
                items = out
        except Exception:
            pass
        return items

    def maker_library_pick(self, for_tag=None):
        """Select a picture from the current database (Library) for the
        word on the tag — sits alongside Find pictures / Upload my image.
        v15.60: for_tag pins the pick to one tag (right-click → Replace
        only picture): the word on that tag does not change."""
        w = for_tag or (self.maker_word.get().strip() or self.preview_word.get())
        if not w or w == "-":
            return messagebox.showinfo(APP_NAME, "Type a word first.")
        items = self._library_picture_items()
        win = ctk.CTkToplevel(self)
        win.title(f"Library pictures — pick one for \u201c{w}\u201d")
        win.geometry("640x560")
        try:
            win.transient(self)
        except Exception:
            pass
        win.after(150, win.lift)
        ctk.CTkLabel(win, text=f"Pictures in your Library — click one to put it on \u201c{w}\u201d",
                     font=ctk.CTkFont(size=13, weight="bold"), anchor="w").pack(fill="x", padx=14, pady=(12, 2))
        search = ctk.CTkEntry(win, placeholder_text="Filter by word…")
        search.pack(fill="x", padx=14, pady=(2, 8))
        grid = ctk.CTkScrollableFrame(win, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        thumbs = []

        def apply_pick(it):
            if for_tag:
                # Replace only picture: the tag's word does not change.
                self.overrides[for_tag] = str(it["path"])
                self._no_auto_pic.discard(for_tag)
                if getattr(self, "_multi_geo", None) is not None:
                    self._multi_geo.pop(for_tag, None)
                self._multi_fp = None
                self.draw()
                self.flash_status(f"Library picture is now on “{for_tag}”", ok=True)
                self.changed()
                try:
                    win.destroy()
                except Exception:
                    pass
                return
            self.maker_word.set(w)
            self.maker_apply_word()
            word_now = self.preview_word.get()
            self.overrides[word_now] = str(it["path"])
            self._no_auto_pic.discard(word_now)
            self.flash_status(f"Library picture \u201c{it['word']}\u201d applied to \u201c{word_now}\u201d", ok=True)
            self.changed()
            try:
                win.destroy()
            except Exception:
                pass

        def render(filter_text=""):
            for child in grid.winfo_children():
                child.destroy()
            thumbs.clear()
            ft = filter_text.strip().lower()
            shown = [it for it in items if not ft or ft in str(it["word"]).lower()]
            if not shown:
                ctk.CTkLabel(grid, text="No pictures in your Library match — find or upload one first.",
                             text_color="gray", wraplength=520, justify="left",
                             anchor="w").grid(row=0, column=0, sticky="w", padx=4, pady=6)
                return
            from PIL import Image
            for c in range(4):
                grid.grid_columnconfigure(c, weight=1, uniform="libpick")
            for i, it in enumerate(shown[:160]):
                r, c = divmod(i, 4)
                cell = ctk.CTkFrame(grid, fg_color="transparent")
                cell.grid(row=r, column=c, sticky="ew", padx=3, pady=3)
                try:
                    im = Image.open(it["path"]).convert("RGBA")
                    im.thumbnail((84, 84), Image.Resampling.LANCZOS)
                    photo = ctk.CTkImage(light_image=im, dark_image=im, size=im.size)
                except Exception:
                    photo = None
                b = ctk.CTkButton(cell, text="" if photo else "?", image=photo, width=92, height=92,
                                  fg_color=("gray90", "gray25"), hover_color="#9B59B6",
                                  command=lambda it=it: apply_pick(it))
                b.pack()
                ctk.CTkLabel(cell, text=str(it["word"]), font=ctk.CTkFont(size=10),
                             text_color="gray").pack()
                thumbs.append(b)

        search.bind("<KeyRelease>", lambda e: render(search.get()))
        render()

    def maker_find_pictures(self):
        w = self.maker_word.get().strip() or self.preview_word.get()
        if not w or w == "-":
            return messagebox.showinfo(APP_NAME, "Type a word first.")
        self._no_auto_pic.discard(w)
        self.maker_word.set(w)
        self._maker_gen += 1
        gen = self._maker_gen
        # v3.12.0-exp: show what we already have INSTANTLY, then search online
        try:
            cached = core.get_cached_pictures(w)
        except Exception:
            cached = []
        if cached:
            self._fill_maker_gallery(w, cached, append=False)
            self.maker_status.configure(
                text=f"{len(cached)} saved picture(s) for \u201c{w}\u201d — searching for more…",
                text_color=ACCENT)
        else:
            self.maker_status.configure(text=f"Searching free libraries for \u201c{w}\u201d…", text_color=ACCENT)

        def work():
            try:
                cands = core.search_icon_candidates(
                    w, limit=16, opensymbols_secret=self.get_opensymbols_secret(),
                    progress=lambda f, msg: self.q.put(("maker_prog", gen, msg)))
                # merge: keep cached ones first, add only genuinely new pictures
                if cached:
                    seen = {str(c["path"].resolve()) for c in cached if c.get("path")}
                    fresh = [c for c in cands if str(c.get("path", "") and Path(c["path"]).resolve()) not in seen]
                    merged = cached + fresh
                else:
                    merged = cands
                self.q.put(("maker_gallery", gen, w, merged))
            except core.OpenSymbolsTokenExpired:
                self.q.put(("maker_gallery", gen, w, cached))
                self.q.put(("opensymbols_expired", "maker", gen))
            except Exception as e:
                log.exception("maker gallery")
                self.q.put(("maker_gallery", gen, w, cached))
                self.q.put(("error", self.gen, str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _fill_maker_gallery(self, word, cands, append=False):
        self._maker_lib_gen = getattr(self, "_maker_lib_gen", 0) + 1   # cancel any library build
        self._maker_strip_mode = "search"
        self._maker_lib_sel = None
        self._maker_lib_sync_actions()
        for child in self.maker_gallery.winfo_children():
            child.destroy()
        self._maker_thumbs.clear()
        ctk.CTkButton(self.maker_gallery, text="← Back to my library", height=26,
                      fg_color="gray30", hover_color="#555",
                      command=self._maker_lib_show).pack(fill="x", pady=(0, 4))
        if not cands:
            ctk.CTkLabel(self.maker_gallery, text="No pictures found. Try another word or upload your own.",
                         text_color="gray").pack(pady=12)
            self.maker_status.configure(text="No pictures found", text_color="gray")
            return
        self.maker_status.configure(text=f"{len(cands)} picture(s) for “{word}” — click one to use it",
                                    text_color="#6bcb77")
        try:
            from PIL import Image, ImageTk
        except Exception:
            Image = ImageTk = None
        for i, item in enumerate(cands):
            path = item.get("path")
            frame = ctk.CTkFrame(self.maker_gallery, fg_color=("gray90", "gray25"), corner_radius=8)
            frame.pack(fill="x", pady=4, padx=2)
            inner = ctk.CTkFrame(frame, fg_color="transparent")
            inner.pack(fill="x", padx=6, pady=6)
            if Image and path and Path(path).exists():
                try:
                    im = Image.open(path).convert("RGBA")
                    im.thumbnail((72, 72))
                    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                    bg = Image.alpha_composite(bg, im).convert("RGB")
                    photo = ImageTk.PhotoImage(bg)
                    self._maker_thumbs.append(photo)
                    lbl = tk.Label(inner, image=photo, bg="#2b2b2b")
                    lbl.pack(side="left", padx=(0, 8))
                    # v13.24: click the picture to use it, drag it onto the
                    # tag, or right-click it for options
                    self._bind_drag(lbl, ("picture", (word, str(path))), word,
                                    lambda p=str(path), ww=word: self.maker_use_picture(ww, p),
                                    image=photo)
                    lbl.bind("<Button-3>", lambda e, p=str(path), ww=word: self._maker_pic_menu(e, ww, p))
                    lbl.bind("<Button-2>", lambda e, p=str(path), ww=word: self._maker_pic_menu(e, ww, p))
                except Exception:
                    ctk.CTkLabel(inner, text="🖼", width=40).pack(side="left")
            else:
                ctk.CTkLabel(inner, text="🖼", width=40).pack(side="left")
            txt = ctk.CTkFrame(inner, fg_color="transparent")
            txt.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(txt, text=item.get("label") or path.name, anchor="w",
                         font=ctk.CTkFont(size=12, weight="bold")).pack(fill="x")
            ctk.CTkLabel(txt, text=item.get("source") or "", anchor="w",
                         text_color="gray", font=ctk.CTkFont(size=11)).pack(fill="x")
            ctk.CTkButton(inner, text="Use", width=52, height=28,
                          command=lambda p=str(path), ww=word: self.maker_use_picture(ww, p)).pack(side="right")

    # ------------------------------------------------ v15.8 permanent Maker library
    def _maker_lib_show(self):
        """Fill the Maker strip with the permanent local picture library."""
        if not getattr(self, "maker_gallery", None):
            return
        self._maker_strip_mode = "library"
        self._maker_lib_sel = None
        self._maker_lib_rows = {}
        self._maker_lib_sync_actions()
        for child in self.maker_gallery.winfo_children():
            child.destroy()
        self._maker_thumbs.clear()
        try:
            items = self._library_picture_items()
        except Exception:
            items = []
        if not items:
            ctk.CTkLabel(self.maker_gallery,
                         text="Your library is empty.\nUpload an image, or use Find pictures — "
                              "whatever you pick is saved here for good.",
                         text_color="gray", justify="left").pack(pady=12, anchor="w")
            self.maker_status.configure(text="My picture library — empty for now", text_color="gray")
            return
        self.maker_status.configure(
            text=f"My picture library — {len(items)} saved picture(s). Click one, then "
                 "Put on this tag or Create new tag", text_color="#6bcb77")
        try:
            from PIL import Image, ImageTk
        except Exception:
            Image = ImageTk = None
        shown = items[:120]
        self._maker_lib_items = shown
        # v15.9 perf: build the rows in small batches on the event loop —
        # creating 120 framed rows with thumbnails in one go froze the app
        # for a visible beat every time the Maker tab was shown.  A
        # generation counter cancels a stale build when the strip changes.
        gen = getattr(self, "_maker_lib_gen", 0) + 1
        self._maker_lib_gen = gen
        total = len(items)

        def build_batch(start):
            if getattr(self, "_maker_lib_gen", 0) != gen:
                return
            if not getattr(self, "maker_gallery", None) or not self.maker_gallery.winfo_exists():
                return
            for i in range(start, min(start + 20, len(shown))):
                item = shown[i]
                word, path = str(item.get("word", "")), str(item.get("path", ""))
                frame = ctk.CTkFrame(self.maker_gallery, fg_color=("gray90", "gray25"),
                                     corner_radius=8, border_width=0)
                frame.pack(fill="x", pady=3, padx=2)
                self._maker_lib_rows[i] = frame
                inner = ctk.CTkFrame(frame, fg_color="transparent")
                inner.pack(fill="x", padx=6, pady=5)
                if Image and path and Path(path).exists():
                    try:
                        im = Image.open(path).convert("RGBA")
                        im.thumbnail((56, 56))
                        bgim = Image.new("RGBA", im.size, (255, 255, 255, 255))
                        bgim = Image.alpha_composite(bgim, im).convert("RGB")
                        photo = ImageTk.PhotoImage(bgim)
                        self._maker_thumbs.append(photo)
                        lbl = tk.Label(inner, image=photo, bg="#2b2b2b", cursor="hand2")
                        lbl.pack(side="left", padx=(0, 8))
                        lbl.bind("<Button-1>", lambda e, k=i: self._maker_lib_select(k))
                    except Exception:
                        ctk.CTkLabel(inner, text="🖼", width=40).pack(side="left")
                else:
                    ctk.CTkLabel(inner, text="🖼", width=40).pack(side="left")
                txt = ctk.CTkFrame(inner, fg_color="transparent")
                txt.pack(side="left", fill="x", expand=True)
                wl = ctk.CTkLabel(txt, text=word or Path(path).stem, anchor="w",
                                  font=ctk.CTkFont(size=12, weight="bold"), cursor="hand2")
                wl.pack(fill="x")
                wl.bind("<Button-1>", lambda e, k=i: self._maker_lib_select(k))
                sl = ctk.CTkLabel(txt, text="saved picture", anchor="w", text_color="gray",
                                  font=ctk.CTkFont(size=11), cursor="hand2")
                sl.pack(fill="x")
                sl.bind("<Button-1>", lambda e, k=i: self._maker_lib_select(k))
                frame.bind("<Button-1>", lambda e, k=i: self._maker_lib_select(k))
                inner.bind("<Button-1>", lambda e, k=i: self._maker_lib_select(k))
            nxt = start + 20
            if nxt < len(shown):
                self.after(1, lambda: build_batch(nxt))
            elif total > len(shown):
                ctk.CTkLabel(self.maker_gallery,
                             text=f"…and {total - len(shown)} more — browse them all "
                                  "in the Library (Word Lists tab).",
                             text_color="gray").pack(pady=6)

        build_batch(0)

    def _maker_lib_refresh(self):
        """Re-show the library if it is what the strip is displaying (called after
        uploads, harvests, and anything else that saves a picture)."""
        try:
            if getattr(self, "_maker_strip_mode", "library") == "library":
                self._maker_lib_show()
        except Exception:
            pass

    def _maker_lib_select(self, i):
        items = getattr(self, "_maker_lib_items", [])
        if not (0 <= i < len(items)):
            return
        self._maker_lib_sel = (str(items[i].get("word", "")), str(items[i].get("path", "")))
        for k, fr in getattr(self, "_maker_lib_rows", {}).items():
            try:
                fr.configure(border_width=2 if k == i else 0, border_color=ACCENT)
            except Exception:
                pass
        self._maker_lib_sync_actions()

    def _maker_lib_sync_actions(self):
        st = "normal" if getattr(self, "_maker_lib_sel", None) else "disabled"
        for b in (getattr(self, "_maker_lib_put_btn", None), getattr(self, "_maker_lib_new_btn", None)):
            if b is not None:
                try:
                    b.configure(state=st)
                except Exception:
                    pass

    def _maker_lib_put_on_tag(self):
        sel = getattr(self, "_maker_lib_sel", None)
        if not sel:
            return
        word, path = sel
        cur = self._lib_maker_current_label()
        if not cur:
            self.flash_status("No tag to put it on — type a word or pick a tag first", ok=False)
            return
        res, _how = self._lib_send_3d(word, path, mode="current")
        if res:
            if res != cur:
                self.flash_status(f"“{res}” is now on the highlighted tag with its picture")
            else:
                self.flash_status(f"Picture put on “{cur}”")

    def _maker_lib_new_tag(self):
        sel = getattr(self, "_maker_lib_sel", None)
        if not sel:
            return
        word, path = sel
        res, _how = self._lib_send_3d(word, path, mode="new")
        if res:
            self.flash_status(f"New tag “{res}” created with its saved picture")

    def maker_use_picture(self, word, path):
        if not word or not path:
            return
        words = self.get_words()
        if not any(x.lower() == word.lower() for x in words):
            words.append(word)
            self.set_words(words)
        for x in self.get_words():
            if x.lower() == word.lower():
                word = x
                break
        self.overrides[word] = path
        self._no_auto_pic.discard(word)
        self.preview_word.set(word)
        self._preview_word_explicit = True
        try:
            self.word_menu.set(word)
        except Exception:
            pass
        self.on_preview_word()
        self.changed()
        self.flash_status(f"Picture applied to “{word}”", ok=True)

    def _build_words(self, tab, words_text):
        """Word lists: create words and lists used by PDF Studio and Maker.
        (v3.12.0-exp: picture picking moved out — this tab is words only.)"""
        self.header(tab, "Word lists", first=True)
        ctk.CTkLabel(tab, text="Create words and word lists here. Your lists feed PDF Studio, "
                               "Maker Studio, and PDF Studio — pick pictures over there.",
                     text_color="gray", wraplength=320, justify="left", anchor="w").pack(fill="x", pady=(0, 8))
        # v13.14: Library — every word and every picture, right-click to send to a studio
        ctk.CTkButton(tab, text="\U0001F4DA  Library — all words & pictures", height=36,
                      fg_color="#9B59B6", hover_color="#B07CC6",
                      font=ctk.CTkFont(size=13, weight="bold"),
                      command=self.open_library).pack(fill="x", pady=(0, 8))
        # v3.12.0-exp: quick-add — type a word, Enter adds it to the list
        # v13.30: Suggest words lives here (was buried in the AI tab)
        _qarow = ctk.CTkFrame(tab, fg_color="transparent")
        _qarow.pack(fill="x", pady=(0, 6))
        self.quick_add_entry = ctk.CTkEntry(_qarow, placeholder_text="Type a word, Enter adds it…")
        self.quick_add_entry.pack(side="left", fill="x", expand=True)
        self.quick_add_entry.bind("<Return>", lambda e: self.quick_add_word())
        ctk.CTkButton(_qarow, text="Add", width=60, command=self.quick_add_word).pack(side="left", padx=(6, 0))
        ctk.CTkButton(_qarow, text="✨ Suggest", width=88, fg_color=AI_COLOR, hover_color=AI_HOVER,
                      command=self.ai_suggest_words).pack(side="left", padx=(6, 0))
        ctk.CTkButton(_qarow, text="New list", width=70, fg_color="gray30", hover_color="#555",
                      command=self.new_word_list).pack(side="left", padx=(6, 0))
        # v13.20: words are TILES now (was a textbox: clicking put a blinking
        # text cursor in it). Tiles highlight on hover; the selected word's
        # tile stays lit; right-click a tile for send options.
        self._words_model = [w.strip() for w in words_text.replace(",", "\n").split("\n") if w.strip()]
        self._word_tile_btns = {}
        self.words_tiles = ctk.CTkScrollableFrame(tab, height=200, fg_color="transparent")
        self.words_tiles.pack(fill="both", expand=True)
        self._tiles_inner = ctk.CTkFrame(self.words_tiles, fg_color="transparent")
        self._tiles_inner.pack(fill="both", expand=True)
        self._render_word_tiles()
        ctk.CTkLabel(tab, text="Tip: click a word tile to see its tag \u00b7 right-click a tile for options",
                     text_color="gray", font=ctk.CTkFont(size=11), anchor="w").pack(fill="x", pady=(2, 0))
        self.list_var = tk.StringVar(value="Load a ready-made list…")
        packs = ["Common AAC words"] + list(aac_boards.TOPIC_PACKS.keys())
        ctk.CTkOptionMenu(tab, values=packs, variable=self.list_var, command=self.load_list,
                          fg_color="#FF6B6B", button_color="#FF6B6B", button_hover_color="#FF8E8E").pack(fill="x", pady=(8, 0))
        # v13.10-exp: preset lists grouped by synonyms
        self.preset_var = tk.StringVar(value="Preset lists — grouped by synonyms…")
        ctk.CTkOptionMenu(tab, values=core.preset_list_names(), variable=self.preset_var,
                          command=self.load_preset_list,
                          fg_color="#9B59B6", button_color="#9B59B6", button_hover_color="#B07CC6").pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(tab, text="Presets keep synonyms together (happy · glad · joyful) so you can pick the exact word.",
                     text_color="gray", font=ctk.CTkFont(size=11), anchor="w", wraplength=320,
                     justify="left").pack(fill="x", pady=(2, 0))
        # v3.6.0-exp: my saved lists
        self.my_lists_menu = ctk.CTkOptionMenu(tab, values=self._my_list_names(), variable=self.my_lists_var,
                          command=self.load_word_list,
                          fg_color="#2d5a3a", button_color="#2d5a3a", button_hover_color="#3a7049")
        self.my_lists_menu.pack(fill="x", pady=(8, 0))
        rwl = ctk.CTkFrame(tab, fg_color="transparent")
        rwl.pack(fill="x", pady=(6, 0))
        ctk.CTkButton(rwl, text="Save list", width=80, command=self.save_word_list).pack(side="left")
        ctk.CTkButton(rwl, text="Delete", width=70, fg_color="gray30", hover_color="#555",
                      command=self.delete_word_list).pack(side="left", padx=(6, 0))
        ctk.CTkButton(rwl, text="Load from file\u2026", fg_color="gray30", hover_color="#555",
                      command=self.load_words_from_file).pack(side="left", fill="x", expand=True, padx=(6, 0))
        ctk.CTkButton(tab, text="Import a board (PDF / picture)\u2026", fg_color="#2d5a3a", hover_color="#3a7049",
                      command=self.import_board).pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(tab, text="Reads the words and pictures from a board and keeps its layout.",
                     text_color="gray", font=ctk.CTkFont(size=10), anchor="w").pack(fill="x")
    def _build_design(self, tab):
        sc = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        sc.pack(fill="both", expand=True)
        self.header(sc, "Tag Design", first=True)
        ctk.CTkLabel(sc, text="A few common choices below. Tick Advanced for the full list. "
                     "Final size can be scaled in your slicer — the model is in millimetres.",
                     text_color="gray", wraplength=310, justify="left",
                     font=ctk.CTkFont(size=11)).pack(fill="x", pady=(0, 6))

        # ---- Design presets (one click sets several options) ----
        self.design_preset = tk.StringVar(value="Custom…")
        dp = ctk.CTkFrame(sc, fg_color="transparent")
        dp.pack(fill="x", pady=3)
        ctk.CTkLabel(dp, text="Design preset", anchor="w").pack(side="left")
        ctk.CTkOptionMenu(dp, values=list(DESIGN_PRESETS.keys()), variable=self.design_preset,
                          width=170, command=self.apply_design_preset).pack(side="right")

        # ---- Simple controls (≈5) ----
        self.menu(sc, "Shape", "shape", core.SHAPES)
        sp = ctk.CTkFrame(sc, fg_color="transparent")
        sp.pack(fill="x", pady=3)
        ctk.CTkLabel(sp, text="Size", anchor="w").pack(side="left")
        ctk.CTkOptionMenu(sp, values=list(aac_boards.SIZE_PRESETS.keys()), variable=self.size_preset,
                          width=170, command=self.apply_size_preset).pack(side="right")
        self.menu(sc, "Key-ring / hole", "hole", core.HOLES)
        self.menu(sc, "Layout", "layout", core.LAYOUTS)
        self.menu(sc, "Colour style", "style", core.STYLES)
        # Picture boldness — thicken or lighten the icon for clearer prints
        self.slider(sc, "Picture boldness", "icon_bold", -0.3, 1.0, 0.05, " mm")
        ctk.CTkLabel(sc, text="Drag right for a bolder picture, left for finer lines.",
                     text_color="gray", wraplength=310, justify="left",
                     font=ctk.CTkFont(size=10)).pack(fill="x", pady=(0, 4))

        # ---- Advanced toggle ----
        self.advanced_design = tk.BooleanVar(value=False)
        adv_row = ctk.CTkFrame(sc, fg_color="transparent")
        adv_row.pack(fill="x", pady=(12, 4))
        ctk.CTkSwitch(adv_row, text="Advanced options", variable=self.advanced_design,
                      command=self._toggle_advanced_design).pack(side="left")

        self.adv_frame = ctk.CTkFrame(sc, fg_color="transparent")
        # not packed until Advanced is on

        # Thickness / depth / raise
        self.slider(self.adv_frame, "Thickness", "thickness", 0.8, 4, 0.1, " mm")
        self.slider(self.adv_frame, "Design depth (first layers)", "design_depth", 0.4, 1.6, 0.1, " mm")
        self.slider(self.adv_frame, "Raised height", "raise_h", 0.2, 2, 0.1, " mm")
        self.slider(self.adv_frame, "Size (mm) — or scale in slicer", "size", 25, 70, 1, " mm")
        self.slider(self.adv_frame, "Hole diameter", "hole_d", 2, 8, 0.5, " mm")
        self.switch(self.adv_frame, "Border ring", "ring")
        self.slider(self.adv_frame, "Ring width", "ring_w", 0.4, 2, 0.1, " mm")

        self.header(self.adv_frame, "Content")
        self.menu(self.adv_frame, "Icon source", "icon_source", core.SOURCES)
        self.slider(self.adv_frame, "Icon size", "icon_scale", 0.5, 1.3, 0.05, "x")
        self.menu(self.adv_frame, "Font", "font", self.fonts)
        self.menu(self.adv_frame, "Letter case", "text_case", core.CASES)
        self.slider(self.adv_frame, "Text size", "text_scale", 0.5, 1.3, 0.05, "x")

        self.header(self.adv_frame, "AAC & print")
        ctk.CTkSwitch(self.adv_frame, text="Category colours (Fitzgerald key)",
                      variable=self.cat_colors).pack(anchor="w", pady=4)
        self.switch(self.adv_frame, "High contrast (black / white)", "high_contrast")
        self.switch(self.adv_frame, "Snap connectors (sentence strip)", "connectors")
        self.switch(self.adv_frame, "Magnet recess on back", "magnet_recess")

        self.header(self.adv_frame, "Colours (when category colours are off)")
        row = ctk.CTkFrame(self.adv_frame, fg_color="transparent")
        row.pack(fill="x", pady=4)
        self.body_btn = ctk.CTkButton(row, text="Body", width=110, hover=False, text_color="black",
                                      fg_color=self.vars["body_color"].get(),
                                      command=lambda: self.pick("body_color", self.body_btn))
        self.body_btn.pack(side="left", padx=(0, 8))
        self.ink_btn = ctk.CTkButton(row, text="Ink", width=110, hover=False, text_color="white",
                                     fg_color=self.vars["ink_color"].get(),
                                     command=lambda: self.pick("ink_color", self.ink_btn))
        self.ink_btn.pack(side="left")
        # v15.8: up to 3 colours per tag — the picture's midtones get their own
        # colour so dark areas stop merging into one blob.
        row2c = ctk.CTkFrame(self.adv_frame, fg_color="transparent")
        row2c.pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(row2c, text="Tag colours", anchor="w").pack(side="left")
        self._ink_layers_var = tk.StringVar(
            value="Up to 3" if float(self.vars["ink_layers"].get() or 2) >= 2 else "Up to 2")
        ctk.CTkOptionMenu(row2c, values=["Up to 2", "Up to 3"], variable=self._ink_layers_var,
                          width=96, command=self._ink_layers_pick).pack(side="left", padx=(8, 0))
        self.ink2_btn = ctk.CTkButton(row2c, text="Detail", width=96, hover=False, text_color="white",
                                      fg_color=self.vars["ink2_color"].get(),
                                      command=lambda: self.pick("ink2_color", self.ink2_btn))
        self.ink2_btn.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(self.adv_frame,
                     text="“Up to 3” prints the picture's midtones in the Detail colour, "
                          "so outlines stay separate from fills.",
                     text_color="gray", wraplength=310, justify="left",
                     font=ctk.CTkFont(size=10)).pack(fill="x", pady=(2, 4))

        # Hint about hole types
        ctk.CTkLabel(sc, text="Key-ring / hole:\n"
                     "• Top / Left loop — protruding keychain loop\n"
                     "• Through (top) — hole in the tag, no loop\n"
                     "• None — solid tag (scale or add a hole in the slicer)",
                     text_color="gray", wraplength=310, justify="left",
                     font=ctk.CTkFont(size=10)).pack(fill="x", pady=(10, 4))

    def _ink_layers_pick(self, val):
        """v15.8: 'Up to 2' = body + ink (classic). 'Up to 3' adds the midtone layer."""
        try:
            self.vars["ink_layers"].set(2.0 if val == "Up to 3" else 1.0)
        except Exception:
            pass

    def _pdf_check_online(self):
        """Background internet check for the PDF Studio status indicator."""
        def work():
            ok = False
            try:
                import socket
                sock = socket.create_connection(("api.arasaac.org", 443), timeout=5)
                sock.close()
                ok = True
            except Exception:
                ok = False
            self.q.put(("pdf_conn", ok))
        threading.Thread(target=work, daemon=True).start()

    def _pdf_schedule_recheck(self):
        """Recheck every 90s while PDF Studio is open (stops when you leave).
        v14.3: arm only ONE chain — entering PDF Studio used to stack an
        extra 90s chain on every visit."""
        try:
            if getattr(self, "_pdf_recheck_armed", False):
                return
            if self.tabs.get() == "PDF Studio":
                self._pdf_recheck_armed = True
                self._pdf_check_online()
                self.after(90000, self._pdf_recheck_tick)
        except Exception:
            pass

    def _pdf_recheck_tick(self):
        self._pdf_recheck_armed = False
        self._pdf_schedule_recheck()

    def _pdf_desired_slots(self):
        """Total card slots from cards-per-page × number-of-pages."""
        cols, rows = aac_boards.PECS_LAYOUTS.get(self.pdf_layout_var.get(), (2, 4))
        try:
            n_pages = max(1, min(12, int(self.pdf_pages_var.get())))
        except (TypeError, ValueError):
            n_pages = 1
        return cols * rows * n_pages, n_pages

    def _pdf_on_layout_or_pages_change(self):
        """v14.5: changing the layout or page count changes ONLY the
        page grid - never the cards. (Before, this refilled the slots
        from the Word Lists words and cut extra cards off, so the whole
        page changed under your hands.) Slots beyond your cards stay
        blank; extra cards simply make extra pages in the preview."""
        _target, n_pages = self._pdf_desired_slots()
        self._pdf_blank_selected = None
        self._pdf_render_tiles()
        # Prefer showing every page when more than one - unless the
        # user picked a preview mode themselves (v13.20 behaviour).
        if n_pages > 1 and hasattr(self, "pdf_preview_mode") and not getattr(self, "_pdf_mode_user", False):
            self.pdf_preview_mode.set("All pages")
        self.pdf_page = 0
        self.draw_pdf_preview()

    def _pdf_mode_picked(self):
        """v13.20: remember the user chose the preview mode themselves."""
        self._pdf_mode_user = True
        self.draw_pdf_preview()

    def _pdf_leave_all_pages(self):
        """v13.20: the page arrows do nothing in All-pages view (every page
        is already on screen) — switch to One page so they visibly work."""
        try:
            pages = max(1, min(12, int(self.pdf_pages_var.get())))
        except (TypeError, ValueError, AttributeError):
            pages = 1
        if pages > 1 and self.pdf_preview_mode.get() == "All pages":
            self.pdf_preview_mode.set("One page")
            self._pdf_mode_user = True

    def _pdf_prev_page(self):
        self._pdf_leave_all_pages()
        self.pdf_page = max(0, self.pdf_page - 1)
        self.draw_pdf_preview()

    def _pdf_next_page(self):
        self._pdf_leave_all_pages()
        cols, rows = aac_boards.PECS_LAYOUTS.get(self.pdf_layout_var.get(), (2, 4))
        pages = max(1, (len(self._pdf_studio_words()) + cols * rows - 1) // (cols * rows))
        # Honour explicit page count when set
        try:
            pages = max(pages, int(self.pdf_pages_var.get()))
        except (TypeError, ValueError, AttributeError):
            pass
        self.pdf_page = min(pages - 1, self.pdf_page + 1)
        self.draw_pdf_preview()

    def _pdf_render_tiles(self):
        self._defer_once("pdf_tiles", self._pdf_render_tiles_now)

    def _pdf_render_tiles_now(self):
        """Rebuild the WORD BOX tiles (v14.6). The tiles are a palette
        of words - your lists and the presets dropdown load them, and
        clicking one puts the word on a card. They are NOT the cards:
        the preview pages only change when a word is put on a card,
        exactly like Maker Studio's tiles vs. the tag preview."""
        frame = getattr(self, "_pdf_tiles_inner", None)
        if frame is None:
            return
        for child in frame.winfo_children():
            child.destroy()
        self._pdf_word_btns = []
        words = list(getattr(self, "_pdf_box_words", []) or [])
        if not words:
            ctk.CTkLabel(frame, text="Word box is empty \u2014 load a list or preset above.",
                         text_color="gray", font=ctk.CTkFont(size=11),
                         justify="left", anchor="w").grid(row=0, column=0, sticky="w", padx=2, pady=4)
            return
        for c in range(3):
            frame.grid_columnconfigure(c, weight=1, uniform="ptile")
        for i, w in enumerate(words):
            r, c = divmod(i, 3)
            b = ctk.CTkButton(frame, text=w, height=36, corner_radius=8,
                              command=lambda word=w: self._pdf_box_put(word))
            b.grid(row=r, column=c, sticky="ew", padx=2, pady=2)
            b.bind("<Button-3>", lambda e, word=w: self._pdf_box_tile_menu(e, word))
            b.bind("<Button-2>", lambda e, word=w: self._pdf_box_tile_menu(e, word))
            self._pdf_word_btns.append(b)
        self._pdf_style_tiles()

    def _pdf_style_tiles(self):
        """The word last picked from the word box stays lit teal."""
        sel = getattr(self, "_pdf_box_selected", None)
        words = list(getattr(self, "_pdf_box_words", []) or [])
        for i, b in enumerate(getattr(self, "_pdf_word_btns", [])):
            try:
                if sel and i < len(words) and words[i] == sel:
                    b.configure(fg_color="#4ECDC4", hover_color="#6EDDD6", text_color="white",
                                font=ctk.CTkFont(size=12, weight="bold"))
                else:
                    b.configure(fg_color=("gray90", "gray25"), hover_color=("#ccd6e4", "#3d4b63"),
                                text_color=("gray10", "#DCE4EE"), font=ctk.CTkFont(size=12))
            except Exception:
                pass

    # ---- v14.6: the word box (palette) and its actions
    def _pdf_box_put(self, word):
        """Word box click: put the word on a card. A highlighted blank
        gets it first; then the selected card (its word is replaced);
        with neither, the word becomes a new card at the end. This -
        plus the typed word box - is the only way words move from the
        word box onto the preview pages."""
        if not word:
            return
        self._pdf_box_selected = word
        slot = getattr(self, "_pdf_blank_selected", None)
        if slot is not None:
            self._pdf_insert_card_at(slot, word)
            self._pdf_blank_finish(slot, word)
        elif getattr(self, "_pdf_selected", None) and word != self._pdf_selected:
            self._pdf_put_on_highlighted(word)
        elif not getattr(self, "_pdf_selected", None):
            self._pdf_words.append(word)
            self._pdf_select(word)
            self.flash_status(f"\u201c{word}\u201d added as a new card", ok=True)
        self._pdf_style_tiles()

    def _pdf_box_add_card(self, word):
        self._pdf_box_selected = word
        self._pdf_words.append(word)
        self._pdf_select(word)
        self._pdf_style_tiles()
        self.flash_status(f"\u201c{word}\u201d added as a new card", ok=True)

    def _pdf_box_remove(self, word):
        self._pdf_box_words = [w for w in getattr(self, "_pdf_box_words", []) if w != word]
        if getattr(self, "_pdf_box_selected", None) == word:
            self._pdf_box_selected = None
        self._pdf_render_tiles()

    def _pdf_box_tile_menu(self, event, word):
        """Right-click a word box tile."""
        m = tk.Menu(self, tearoff=0)
        slot = getattr(self, "_pdf_blank_selected", None)
        sel = getattr(self, "_pdf_selected", None)
        if slot is not None:
            m.add_command(label="Put on the highlighted blank card",
                          command=lambda: self._pdf_box_put(word))
        elif sel and sel != word:
            m.add_command(label=f"Put on highlighted card (\u201c{sel}\u201d \u2192 \u201c{word}\u201d)",
                          command=lambda: self._pdf_box_put(word))
        m.add_command(label="Add as a new card",
                      command=lambda: self._pdf_box_add_card(word))
        self._add_generate_entry(m, [word])
        m.add_separator()
        m.add_command(label="Remove from word box",
                      command=lambda: self._pdf_box_remove(word))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _pdf_box_preset_picked(self, choice):
        """Presets dropdown: load the list into the WORD BOX only.
        The preview pages NEVER change from this - Cody's rule."""
        try:
            if not choice or choice == "Load words from\u2026":
                return
            words = self._maker_words_for(choice)
            out, seen = [], set()
            for w in words:
                w = str(w).strip()
                if w and w.lower() not in seen:
                    seen.add(w.lower())
                    out.append(w)
            # Reset the dropdown so the same list can be picked again later
            self.pdf_preset_var.set("Load words from\u2026")
            if not out:
                self.flash_status(f"\u201c{choice}\u201d has no words in it", ok=False)
                return
            self._pdf_box_words = out
            self._pdf_box_source = choice
            self._pdf_box_selected = None
            self._pdf_render_tiles()
            self.flash_status(f"{len(out)} words loaded into the word box \u2014 your preview pages didn't change", ok=True)
        except Exception:
            log.exception("pdf word box load")

    def _pdf_refresh_preset_menu(self):
        """Keep the word box dropdown in step with Word Lists edits."""
        try:
            self.pdf_preset_menu.configure(
                values=["Load words from\u2026"] + self._maker_list_choices())
        except Exception:
            pass

    def _pdf_box_refresh_if_current(self):
        """While the word box source is 'Current words', keep it in step
        with the Word Lists words (Maker does the same with its tiles).
        Once a list/preset is loaded into the box, it stays as loaded."""
        try:
            if getattr(self, "_pdf_box_source", "Current words") == "Current words":
                words = list(self.get_words())
                if words != list(getattr(self, "_pdf_box_words", []) or []):
                    self._pdf_box_words = words
                    self._pdf_render_tiles()
        except Exception:
            pass

    def _pdf_put_on_highlighted(self, word):
        """v14.2: right-click a word tile -> put it on the highlighted
        card, replacing that card's word \u2014 the Maker Studio
        'put on highlighted tag' idea, for cards. Label/picture choices
        follow the word, exactly like a rename."""
        sel = getattr(self, "_pdf_selected", None)
        if not sel or not word or word == sel:
            return
        idx = self._pdf_selected_index()
        if idx is None:
            return
        words = list(self._pdf_words)
        words[idx] = word
        self._pdf_words = words
        if sel in self.pdf_no_label:
            self.pdf_no_label.discard(sel)
            self.pdf_no_label.add(word)
        if sel in self.overrides:
            self.overrides[word] = self.overrides.pop(sel)
        if sel in self.variants:
            self.variants[word] = self.variants.pop(sel)
        if sel in self.search_as:
            self.search_as[word] = self.search_as.pop(sel)
        self._pdf_selected = word
        self._pdf_render_tiles()
        self._pdf_sync_sel_panel()
        self._pdf_sync_sel_bar()
        self._pdf_sync_pic_buttons()
        self.draw_pdf_preview()
        self.flash_status(f"\u201c{word}\u201d is now on the highlighted card", ok=True)

    def _pdf_selected_index(self):
        """Card slot index (0-based) for the selected word, or None."""
        w = self._pdf_selected
        if not w:
            return None
        words = self._pdf_studio_words()
        try:
            return words.index(w)
        except ValueError:
            return None

    def _pdf_pick_card_color(self):
        """Pick a tile colour for the selected card *slot* only (not all matching words)."""
        idx = self._pdf_selected_index()
        if idx is None:
            return
        from tkinter import colorchooser
        key = str(idx)
        _c, hexv = colorchooser.askcolor(color=self.pdf_tile_colors.get(key, "#FFFFFF"),
                                        title=f"Tile colour for card {idx + 1}")
        if hexv:
            self.pdf_tile_colors[key] = hexv
            try:
                self.pdf_tile_btn2.configure(fg_color=hexv)
            except Exception:
                pass
            self.draw_pdf_preview()

    def _pdf_reset_card_color(self):
        idx = self._pdf_selected_index()
        if idx is None:
            return
        self.pdf_tile_colors.pop(str(idx), None)
        try:
            self.pdf_tile_btn2.configure(fg_color="#FFFFFF")
        except Exception:
            pass
        self.draw_pdf_preview()

    def _pdf_rename_selected_dialog(self):
        """Right-click → rename the selected card (preview editing)."""
        old = self._pdf_selected
        if not old:
            return
        import tkinter.simpledialog as sd
        new = sd.askstring(APP_NAME, f"Rename card “{old}” to:",
                           initialvalue=old, parent=self)
        if not new or not new.strip() or new.strip() == old:
            return
        try:
            self.pdf_rename_entry.delete(0, "end")
            self.pdf_rename_entry.insert(0, new.strip())
        except Exception:
            pass
        self._pdf_rename_selected()

    def _pdf_rename_selected(self):
        """Rename the currently selected card label (customizes auto-populated slots)."""
        old = self._pdf_selected
        if not old:
            return
        new = (self.pdf_rename_entry.get() or "").strip()
        if not new or new == old:
            return
        words = self._pdf_studio_words()
        for i, w in enumerate(words):
            if w == old:
                words[i] = new
                break
        else:
            return
        # Tile colours are keyed by slot index — they stay put when renaming.
        # Label-hide / picture overrides follow the word text.
        if old in self.pdf_no_label:
            self.pdf_no_label.discard(old)
            self.pdf_no_label.add(new)
        if old in self.overrides:
            self.overrides[new] = self.overrides.pop(old)
        if old in self.variants:
            self.variants[new] = self.variants.pop(old)
        if old in self.search_as:
            self.search_as[new] = self.search_as.pop(old)
        if old in getattr(self, "pdf_pic_scale_map", {}):
            self.pdf_pic_scale_map[new] = self.pdf_pic_scale_map.pop(old)
        if old in getattr(self, "pdf_pic_y_map", {}):
            self.pdf_pic_y_map[new] = self.pdf_pic_y_map.pop(old)
        self._pdf_words = list(words)
        self._pdf_render_tiles()
        self._pdf_selected = new
        s = getattr(self, "_pdf_selected_set", set()) or set()
        if old in s:
            s.discard(old)
            s.add(new)
        self._pdf_selected_set = s
        self.pdf_rename_entry.delete(0, "end")
        self._pdf_sync_sel_bar()
        self._pdf_sync_sel_panel()
        self.draw_pdf_preview()

    def _build_pdf_studio(self, tab, saved):
        """PDF-focused workspace with editable word list, print options and an on-screen page preview."""
        sc = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        sc.pack(fill="both", expand=True)
        self._pdf_cards_sc = sc          # v15.1: swapped out while the PDF Viewer + Editor is open
        self.header(sc, "PDF Studio", first=True)
        ctk.CTkLabel(sc, text="Build printable AAC picture cards. Preview the page before saving.",
                     text_color="gray", wraplength=310, justify="left").pack(fill="x", pady=(0, 8))
        # v15.1: open ANY existing PDF to view it, rearrange pages and stamp pictures/text on it
        ctk.CTkButton(sc, text="Open PDF or picture…  (viewer + editor)", height=30, fg_color=TEAL,
                      hover_color=TEAL_HOVER, text_color="white",
                      command=self._ped_open_dialog).pack(fill="x", pady=(0, 8))
        # v3.10.0-exp: visible internet status — pictures need a connection
        _conn = ctk.CTkFrame(sc, fg_color="transparent")
        _conn.pack(fill="x", pady=(0, 8))
        self.pdf_conn_dot = ctk.CTkLabel(_conn, text="\u25cf", font=ctk.CTkFont(size=14),
                                         text_color="gray", width=20)
        self.pdf_conn_dot.pack(side="left")
        self.pdf_conn_text = ctk.CTkLabel(_conn, text="Checking internet…", text_color="gray",
                                          font=ctk.CTkFont(size=11), anchor="w")
        self.pdf_conn_text.pack(side="left", padx=(2, 0), fill="x", expand=True)
        ctk.CTkButton(_conn, text="Retry", width=56, height=24, fg_color="gray30", hover_color="#555",
                      command=self._pdf_check_online).pack(side="right")
        # v14.4: ONE word area (Cody's reorganisation) \u2014 type a word here,
        # then say what to do with it. (v14.5: no presets dropdown -
        # the page starts blank and Cody fills it in card by card.)
        self.header(sc, "Add a word")
        self.pdf_word_entry = ctk.CTkEntry(sc, placeholder_text="Type a word\u2026  (Enter = new card)")
        self.pdf_word_entry.pack(fill="x", pady=(2, 0))
        self.pdf_word_entry.bind("<Return>", lambda e: self.pdf_add_words())
        _aw = ctk.CTkFrame(sc, fg_color="transparent")
        _aw.pack(fill="x", pady=(6, 0))
        ctk.CTkButton(_aw, text="\uff0b New card", fg_color=TEAL, hover_color=TEAL_HOVER,
                      text_color="white", command=self.pdf_add_words).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(_aw, text="Put on highlighted card", fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      text_color="white", command=self.pdf_put_entry_on_highlighted).pack(side="left", fill="x", expand=True, padx=(6, 0))
        _aw2 = ctk.CTkFrame(sc, fg_color="transparent")
        _aw2.pack(fill="x", pady=(6, 0))
        ctk.CTkButton(_aw2, text="Remove selected card", height=26, fg_color="gray30", hover_color="#555",
                      command=self.pdf_remove_words).pack(side="left")
        # v14.9: one-click fill of PDF cards from the current Word Lists bank
        # (manual only — never runs on its own when layout/pages change).
        ctk.CTkButton(sc, text="Fill cards from Word Lists", height=30,
                      fg_color="#2f6fb3", hover_color="#3d7fc7", text_color="white",
                      command=self.pdf_fill_cards_from_word_bank).pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(sc, text="Puts every word from your current Word Lists into PDF cards "
                              "(skips ones already on a card). Does not run automatically.",
                     text_color="gray", font=ctk.CTkFont(size=10), anchor="w",
                     wraplength=310, justify="left").pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(sc, text="Word box \u2014 load a list or preset into the word box below. It never changes your preview pages:", text_color="gray",
                     font=ctk.CTkFont(size=11), anchor="w", wraplength=310, justify="left").pack(fill="x", pady=(8, 0))
        self.pdf_preset_var = tk.StringVar(value="Load words from\u2026")
        self.pdf_preset_menu = ctk.CTkOptionMenu(
            sc, values=["Load words from\u2026"] + self._maker_list_choices(),
            variable=self.pdf_preset_var, command=self._pdf_box_preset_picked)
        self.pdf_preset_menu.pack(fill="x", pady=(4, 2))
        ctk.CTkLabel(sc, text="Word box  (click a word to put it on a card \u00b7 right-click for options)", anchor="w").pack(fill="x", pady=(6, 0))
        # v14.2: word TILES, the Maker Studio / Word Lists design \u2014 the
        # old plain listbox is gone. Words live in self._pdf_words.
        # v14.5: PDF Studio ALWAYS starts with a blank page (Cody's
        # call) - nothing is restored or auto-filled; he fills the cards
        # in himself with the word box, the blank-card menus and the
        # Library. Cards live for the session in self._pdf_words.
        self._pdf_words = []
        # v14.6: the WORD BOX (the tiles below) is separate from the
        # cards. It starts on your current words; the dropdown above
        # loads other lists/presets into it. Words only reach the
        # preview pages when you put them on a card.
        self._pdf_box_source = "Current words"
        self._pdf_box_selected = None
        try:
            self._pdf_box_words = list(self.get_words())
        except Exception:
            self._pdf_box_words = []
        _pdf_tiles_wrap = ctk.CTkFrame(sc, height=190, fg_color="transparent")
        _pdf_tiles_wrap.pack(fill="x", pady=(4, 6))
        _pdf_tiles_wrap.pack_propagate(False)
        self._pdf_tiles_frame = ctk.CTkScrollableFrame(_pdf_tiles_wrap, fg_color="transparent")
        self._pdf_tiles_frame.pack(fill="both", expand=True)
        self._pdf_tiles_inner = ctk.CTkFrame(self._pdf_tiles_frame, fg_color="transparent")
        self._pdf_tiles_inner.pack(fill="x", anchor="n")
        self._pdf_word_btns = []
        self._pdf_render_tiles()
        # v3.10.0-exp: everything about the selected card lives here — no tab-hopping
        self.pdf_sel_panel = ctk.CTkFrame(sc, fg_color=("gray90", "gray22"), corner_radius=8)
        self.pdf_sel_panel.pack(fill="x", pady=(2, 8))
        ctk.CTkLabel(self.pdf_sel_panel, text="Selected card", anchor="w",
                     font=ctk.CTkFont(weight="bold", size=12)).pack(fill="x", padx=10, pady=(8, 0))
        self.pdf_sel_word = ctk.CTkLabel(self.pdf_sel_panel, text="Click a card in the preview ↑", anchor="w",
                                         font=ctk.CTkFont(size=12), text_color="gray")
        self.pdf_sel_word.pack(fill="x", padx=10)
        _ren = ctk.CTkFrame(self.pdf_sel_panel, fg_color="transparent")
        _ren.pack(fill="x", padx=10, pady=(4, 0))
        self.pdf_rename_entry = ctk.CTkEntry(_ren, placeholder_text="Rename this card…")
        self.pdf_rename_entry.pack(side="left", fill="x", expand=True)
        self.pdf_rename_entry.bind("<Return>", lambda e: self._pdf_rename_selected())
        ctk.CTkButton(_ren, text="Rename", width=70, height=28, fg_color="#FF6B6B", hover_color="#FF8E8E",
                      command=self._pdf_rename_selected).pack(side="left", padx=(6, 0))
        ctk.CTkLabel(self.pdf_sel_panel, text="Picture", anchor="w", text_color="gray",
                     font=ctk.CTkFont(size=11)).pack(fill="x", padx=10, pady=(6, 0))
        self.pdf_pic_btn2 = ctk.CTkButton(self.pdf_sel_panel, text="Picture 1 \u25be",
                                            fg_color="gray30", hover_color="#555",
                                            command=self.open_pdf_picture_picker)
        self.pdf_pic_btn2.pack(fill="x", padx=10, pady=(2, 0))
        _pr = ctk.CTkFrame(self.pdf_sel_panel, fg_color="transparent")
        _pr.pack(fill="x", padx=10, pady=(6, 0))
        self.pdf_find_btn2 = ctk.CTkButton(_pr, text="Find pictures…", width=120, fg_color="#FF6B6B", hover_color="#FF8E8E",
                      command=self.pdf_find_pictures)
        self.pdf_find_btn2.pack(side="left")
        self.pdf_myimg_btn2 = ctk.CTkButton(_pr, text="My image…", width=90, fg_color="gray30", hover_color="#555",
                      command=self.pdf_choose_image)
        self.pdf_myimg_btn2.pack(side="left", padx=(6, 0))
        self.pdf_label_check2 = ctk.CTkCheckBox(self.pdf_sel_panel, text="Show word label",
                                                variable=None,  # wired below
                                                command=self.pdf_toggle_label)
        self.pdf_label_check2.pack(anchor="w", padx=10, pady=(6, 0))
        _crow = ctk.CTkFrame(self.pdf_sel_panel, fg_color="transparent")
        _crow.pack(fill="x", padx=10, pady=(4, 0))
        ctk.CTkLabel(_crow, text="Tile colour", text_color="gray",
                     font=ctk.CTkFont(size=11)).pack(side="left")
        self.pdf_tile_btn2 = ctk.CTkButton(_crow, text="", width=44, height=24, hover=False,
                                           fg_color="#FFFFFF", border_width=1, border_color="gray50",
                                           command=self._pdf_pick_card_color)
        self.pdf_tile_btn2.pack(side="left", padx=(8, 0))
        ctk.CTkButton(_crow, text="Reset", width=52, height=24, fg_color="gray30", hover_color="#555",
                      command=self._pdf_reset_card_color).pack(side="left", padx=(6, 0))
        ctk.CTkLabel(self.pdf_sel_panel,
                     text="Untick when the picture already has the word on it.",
                     text_color="gray", font=ctk.CTkFont(size=10), wraplength=280,
                     anchor="w", justify="left").pack(fill="x", padx=10, pady=(0, 8))
        self.header(sc, "Page & print")
        ctk.CTkLabel(sc, text="Cards per page", anchor="w").pack(fill="x")
        self.pdf_layout_var = tk.StringVar(value=saved.get("pdf_layout", "8 per page (2×4)"))
        aac_boards.register_layout_name(self.pdf_layout_var.get())     # an imported board's layout (v14.9)
        if self.pdf_layout_var.get() not in aac_boards.PECS_LAYOUTS:
            self.pdf_layout_var.set("8 per page (2×4)")
        self.pdf_layout_menu = ctk.CTkOptionMenu(
            sc, values=list(aac_boards.PECS_LAYOUTS.keys()), variable=self.pdf_layout_var,
            command=lambda _: self._pdf_on_layout_or_pages_change())
        self.pdf_layout_menu.pack(fill="x", pady=(4, 4))
        ctk.CTkButton(sc, text="Import a board (PDF / picture)\u2026", fg_color="#2d5a3a", hover_color="#3a7049",
                      command=self.import_board).pack(fill="x", pady=(0, 4))
        # Number of pages — sets total card slots; the list auto-fills from your words
        _np = ctk.CTkFrame(sc, fg_color="transparent")
        _np.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(_np, text="Number of pages", anchor="w").pack(side="left")
        self.pdf_pages_var = tk.StringVar(value=str(saved.get("pdf_pages", 1)))
        self.pdf_pages_menu = ctk.CTkOptionMenu(
            _np, values=[str(i) for i in range(1, 13)], variable=self.pdf_pages_var,
            width=70, command=lambda _: self._pdf_on_layout_or_pages_change())
        self.pdf_pages_menu.pack(side="right")
        # Multi-page preview — flip pages or see them all at once
        self.pdf_page = 0
        _prow = ctk.CTkFrame(sc, fg_color="transparent")
        _prow.pack(fill="x", pady=(0, 4))
        ctk.CTkButton(_prow, text="◀", width=36, height=28, fg_color="gray30", hover_color="#555",
                      command=self._pdf_prev_page).pack(side="left")
        self.pdf_page_label = ctk.CTkLabel(_prow, text="Page 1 of 1", width=90, font=ctk.CTkFont(size=11))
        self.pdf_page_label.pack(side="left", padx=4)
        ctk.CTkButton(_prow, text="▶", width=36, height=28, fg_color="gray30", hover_color="#555",
                      command=self._pdf_next_page).pack(side="left")
        # Default to All pages so multi-page layouts are immediately visible
        self.pdf_preview_mode = tk.StringVar(value=saved.get("pdf_preview_mode", "All pages"))
        self._pdf_mode_user = False  # v13.20: True once the user picks a mode
        ctk.CTkOptionMenu(_prow, values=["One page", "All pages"], variable=self.pdf_preview_mode,
                          width=105, height=28,
                          command=lambda _: self._pdf_mode_picked()).pack(side="right")
        # v13.24 / v13.25: size & layout sliders — words, pictures, and preview zoom.
        # Word size still auto-fits each card (long words shrink, short words fill).
        self.pdf_word_scale = tk.DoubleVar(value=float(saved.get("pdf_word_scale", 1.0)))
        _ws = ctk.CTkFrame(sc, fg_color="transparent")
        _ws.pack(fill="x", pady=(2, 2))
        ctk.CTkLabel(_ws, text="Word size", anchor="w").pack(side="left")
        self.pdf_word_scale_label = ctk.CTkLabel(
            _ws, text=f"{round(self.pdf_word_scale.get() * 100)}%", width=44, anchor="e")
        self.pdf_word_scale_label.pack(side="right")
        ctk.CTkSlider(_ws, from_=0.5, to=1.6, number_of_steps=22,
                      variable=self.pdf_word_scale,
                      command=self._pdf_word_scale_changed).pack(
                          side="left", fill="x", expand=True, padx=(8, 8))
        # v13.25: Picture size — scales icons inside each card (preview + PDF)
        self.pdf_pic_scale = tk.DoubleVar(value=float(saved.get("pdf_pic_scale", 1.0)))
        _ps = ctk.CTkFrame(sc, fg_color="transparent")
        _ps.pack(fill="x", pady=(2, 2))
        ctk.CTkLabel(_ps, text="Picture size", anchor="w").pack(side="left")
        self.pdf_pic_scale_label = ctk.CTkLabel(
            _ps, text=f"{round(self.pdf_pic_scale.get() * 100)}%", width=44, anchor="e")
        self.pdf_pic_scale_label.pack(side="right")
        ctk.CTkSlider(_ps, from_=0.5, to=1.4, number_of_steps=18,
                      variable=self.pdf_pic_scale,
                      command=self._pdf_pic_scale_changed).pack(
                          side="left", fill="x", expand=True, padx=(8, 8))
        # v13.25: Picture vertical position within the card (0 = higher, 1 = lower)
        self.pdf_pic_y = tk.DoubleVar(value=float(saved.get("pdf_pic_y", 0.35)))
        _py = ctk.CTkFrame(sc, fg_color="transparent")
        _py.pack(fill="x", pady=(2, 2))
        ctk.CTkLabel(_py, text="Picture position", anchor="w").pack(side="left")
        self.pdf_pic_y_label = ctk.CTkLabel(
            _py, text="mid", width=44, anchor="e")
        self.pdf_pic_y_label.pack(side="right")
        ctk.CTkSlider(_py, from_=0.15, to=0.55, number_of_steps=16,
                      variable=self.pdf_pic_y,
                      command=self._pdf_pic_y_changed).pack(
                          side="left", fill="x", expand=True, padx=(8, 8))
        self._pdf_pic_y_changed(self.pdf_pic_y.get())
        # v13.27: Word position — height of the word label in the card,
        # mirroring Picture position (preview + PDF). 0.12 = the classic
        # spot just above the card's bottom edge.
        self.pdf_word_y = tk.DoubleVar(value=float(saved.get("pdf_word_y", 0.12)))
        _wy = ctk.CTkFrame(sc, fg_color="transparent")
        _wy.pack(fill="x", pady=(2, 2))
        ctk.CTkLabel(_wy, text="Word position", anchor="w").pack(side="left")
        self.pdf_word_y_label = ctk.CTkLabel(
            _wy, text="mid", width=44, anchor="e")
        self.pdf_word_y_label.pack(side="right")
        ctk.CTkSlider(_wy, from_=0.05, to=0.30, number_of_steps=25,
                      variable=self.pdf_word_y,
                      command=self._pdf_word_y_changed).pack(
                          side="left", fill="x", expand=True, padx=(8, 8))
        self._pdf_word_y_changed(self.pdf_word_y.get())
        # v13.25: Preview zoom controls (also mouse-wheel on the canvas)
        _zr = ctk.CTkFrame(sc, fg_color="transparent")
        _zr.pack(fill="x", pady=(4, 2))
        ctk.CTkLabel(_zr, text="Preview zoom", anchor="w").pack(side="left")
        ctk.CTkButton(_zr, text="−", width=32, height=26, fg_color="gray30", hover_color="#555",
                      command=lambda: self._pdf_set_zoom(self._pdf_zoom / 1.2)).pack(side="right")
        self.pdf_zoom_label = ctk.CTkLabel(_zr, text="100%", width=48, anchor="e")
        self.pdf_zoom_label.pack(side="right", padx=4)
        ctk.CTkButton(_zr, text="+", width=32, height=26, fg_color="gray30", hover_color="#555",
                      command=lambda: self._pdf_set_zoom(self._pdf_zoom * 1.2)).pack(side="right")
        ctk.CTkButton(_zr, text="Reset", width=52, height=26, fg_color="gray30", hover_color="#555",
                      command=self._pdf_reset_view).pack(side="right", padx=(0, 6))
        self.pdf_labels_var = tk.BooleanVar(value=saved.get("pdf_labels", True))
        # v3.12.0-exp: explicit card-content toggles (replaces the confusing single checkbox)
        self.pdf_show_pics = tk.BooleanVar(value=saved.get("pdf_show_pics", True))
        self.pdf_show_words = tk.BooleanVar(value=saved.get("pdf_show_words", True))
        self.pdf_colors_var = tk.BooleanVar(value=saved.get("pdf_colors", True))
        self.pdf_tile_colors = {}  # word -> hex (per-card tile colours, v3.12.0-exp)
        try:
            self.pdf_tile_colors.update(saved.get("pdf_tile_colors", {}) or {})
        except Exception:
            pass
        try:
            self.pdf_pic_scale_map = dict(saved.get("pdf_pic_scale_map", {}) or {})
            self.pdf_pic_y_map = dict(saved.get("pdf_pic_y_map", {}) or {})
        except Exception:
            self.pdf_pic_scale_map = getattr(self, "pdf_pic_scale_map", {}) or {}
            self.pdf_pic_y_map = getattr(self, "pdf_pic_y_map", {}) or {}
        self.pdf_paper = tk.StringVar(value=saved.get("pdf_paper", "letter"))
        if self.pdf_paper.get() not in ("letter", "A4"):
            self.pdf_paper.set("letter")
        self.pdf_cut_guides = tk.BooleanVar(value=saved.get("pdf_cut_guides", True))
        ctk.CTkLabel(sc, text="Card content", anchor="w", text_color="gray",
                     font=ctk.CTkFont(size=11)).pack(fill="x", pady=(4, 0))
        ctk.CTkCheckBox(sc, text="Show pictures", variable=self.pdf_show_pics,
                        command=self.draw_pdf_preview).pack(anchor="w", pady=2)
        ctk.CTkCheckBox(sc, text="Show words", variable=self.pdf_show_words,
                        command=self.draw_pdf_preview).pack(anchor="w", pady=2)
        ctk.CTkCheckBox(sc, text="Category-colour card backgrounds", variable=self.pdf_colors_var,
                        command=self.draw_pdf_preview).pack(anchor="w", pady=2)
        ctk.CTkLabel(sc, text="Tips: Ctrl+click multi-select · Delete removes · "
                              "drag card to reorder · handles resize/move picture · "
                              "double-click word to rename · double-click picture to find · "
                              "right-click for Library / colour / AI.",
                     text_color="gray", font=ctk.CTkFont(size=10), wraplength=300,
                     anchor="w", justify="left").pack(fill="x", pady=(8, 0))

    def _pdf_word_scale_changed(self, value):
        try:
            self.pdf_word_scale_label.configure(text=f"{round(float(value) * 100)}%")
        except Exception:
            pass
        self._queue_pdf_preview()

    def _pdf_pic_scale_changed(self, value):
        try:
            self.pdf_pic_scale_label.configure(text=f"{round(float(value) * 100)}%")
        except Exception:
            pass
        self._queue_pdf_preview()

    def _pdf_pic_y_changed(self, value):
        try:
            v = float(value)
            if v < 0.28:
                label = "high"
            elif v > 0.42:
                label = "low"
            else:
                label = "mid"
            self.pdf_pic_y_label.configure(text=label)
        except Exception:
            pass
        self._queue_pdf_preview()

    def _pdf_word_y_changed(self, value):
        try:
            v = float(value)
            if v < 0.09:
                label = "low"
            elif v > 0.19:
                label = "high"
            else:
                label = "mid"
            self.pdf_word_y_label.configure(text=label)
        except Exception:
            pass
        self._queue_pdf_preview()

    def _pdf_set_zoom(self, z, focus=None):
        """Zoom the PDF preview. focus=(cx, cy) keeps that canvas point fixed."""
        z = max(0.4, min(3.0, float(z)))
        old = self._pdf_zoom
        if abs(z - old) < 0.001:
            return
        if focus is not None and old > 0:
            # Keep the point under the cursor stable while zooming
            fx, fy = focus
            self._pdf_pan[0] = fx - (fx - self._pdf_pan[0]) * (z / old)
            self._pdf_pan[1] = fy - (fy - self._pdf_pan[1]) * (z / old)
        self._pdf_zoom = z
        try:
            self.pdf_zoom_label.configure(text=f"{round(z * 100)}%")
        except Exception:
            pass
        self.draw_pdf_preview()

    def _pdf_reset_view(self):
        self._pdf_zoom = 1.0
        self._pdf_pan = [0.0, 0.0]
        try:
            self.pdf_zoom_label.configure(text="100%")
        except Exception:
            pass
        self.draw_pdf_preview()

    def _queue_pdf_preview(self):
        if self._pdf_preview_after:
            self.after_cancel(self._pdf_preview_after)
        self._pdf_preview_after = self.after(350, self.draw_pdf_preview)

    def _pdf_studio_words(self):
        # v14.2: the words live in self._pdf_words now; the tiles are a view.
        return list(getattr(self, "_pdf_words", []))

    def pdf_add_words(self):
        raw = self.pdf_word_entry.get().replace(",", "\n").split("\n")
        known = {w.lower() for w in self._pdf_studio_words()}
        added = 0
        for w in (x.strip() for x in raw):
            if w and w.lower() not in known:
                self._pdf_words.append(w)
                known.add(w.lower())
                added += 1
        self.pdf_word_entry.delete(0, "end")
        if added:
            self._pdf_render_tiles()
            self._queue_pdf_preview()
            self.flash_status(f"Added {added} new card{'s' if added != 1 else ''}", ok=True)

    def pdf_fill_cards_from_word_bank(self):
        """v14.9: put every word from the current Word Lists bank onto PDF cards.
        Append-only (words already on a card are skipped). Manual button only —
        never runs when layout or page count changes."""
        try:
            bank = [str(w).strip() for w in self.get_words() if str(w).strip()]
        except Exception:
            bank = []
        if not bank:
            self.flash_status("Word Lists is empty — add words there first", ok=False)
            return
        known = {w.lower() for w in self._pdf_studio_words()}
        added = []
        for w in bank:
            if w.lower() not in known:
                known.add(w.lower())
                self._pdf_words.append(w)
                added.append(w)
        if not added:
            self.flash_status("All Word Lists words are already on cards", ok=True)
            return
        self._pdf_render_tiles()
        self._queue_pdf_preview()
        self.flash_status(
            f"Filled {len(added)} card{'s' if len(added) != 1 else ''} from Word Lists "
            f"({len(self._pdf_words)} total)",
            ok=True)

    # ---- the Add-a-word area's actions
    def _pdf_entry_word(self):
        """First word/phrase typed in the Add-a-word box ('' if empty)."""
        try:
            raw = self.pdf_word_entry.get()
        except Exception:
            return ""
        for part in raw.replace(",", "\n").split("\n"):
            if part.strip():
                return part.strip()
        return ""

    def pdf_put_entry_on_highlighted(self):
        """'Put on highlighted card': the typed word replaces the
        highlighted card's word \u2014 or fills the highlighted blank."""
        word = self._pdf_entry_word()
        if not word:
            self.flash_status("Type a word first", ok=False)
            return
        slot = getattr(self, "_pdf_blank_selected", None)
        if slot is not None:
            self._pdf_insert_card_at(slot, word)
            self.pdf_word_entry.delete(0, "end")
            self._pdf_blank_finish(slot, word)
            return
        if not self._pdf_selected:
            self.flash_status("Click a card (or a blank card) to highlight it first", ok=False)
            return
        if word == self._pdf_selected:
            self.pdf_word_entry.delete(0, "end")
            return
        self._pdf_put_on_highlighted(word)
        self.pdf_word_entry.delete(0, "end")

    def pdf_remove_words(self):
        """v14.2: Remove deletes the selected card's word (the tiles are
        single-select, same as Maker's tiles). v14.3: also removes a
        highlighted blank card."""
        if getattr(self, "_pdf_blank_selected", None) is not None and not self._pdf_selected:
            self._pdf_remove_blank(self._pdf_blank_selected)
            return
        w = self._pdf_selected
        if not w:
            return
        self._pdf_words = [x for x in self._pdf_words if x != w]
        self._pdf_deselect()
        self._pdf_render_tiles()
        self._queue_pdf_preview()
    def _schedule_canvas_redraw(self, event=None):
        """v14.3 / v14.9: <Configure> fires in bursts — a tab switch can churn the
        layout through a dozen resizes, and every one used to trigger a
        FULL canvas repaint (the main tab-switch lag). Coalesce them:
        at most one repaint per ~100 ms, and none at all when the canvas
        ends up the same size as the last repaint (on_workspace_change
        already painted that exact frame). Also ignore tiny size jitter
        so clicking tiles / switching studios doesn't keep "resizing"."""
        if getattr(self, "_canvas_redraw_after", None) is not None:
            return
        def _fire():
            self._canvas_redraw_after = None
            try:
                size = (self.canvas.winfo_width(), self.canvas.winfo_height())
            except Exception:
                size = None
            last = getattr(self, "_canvas_last_size", None)
            if size is not None and last is not None:
                if abs(size[0] - last[0]) < 4 and abs(size[1] - last[1]) < 4:
                    return
            self._canvas_last_size = size
            self._canvas_redraw()
        self._canvas_redraw_after = self.after(100, _fire)

    def _canvas_redraw(self):
        """Route canvas repaints to the workspace that owns the canvas."""
        try:
            tab = self.tabs.get()
            if tab == AI_TAB:
                return
            if tab == "PDF Studio":
                self.draw_pdf_preview(self.canvas)
            else:
                self.draw()
        except Exception:
            pass

    def draw_word_lists_preview(self):
        """13.14: Word Lists no longer takes over the preview area with
        an info board — the canvas shows the selected word's tag (draw()).
        Kept as a delegate so existing refresh callers stay correct."""
        self.draw()

    def on_workspace_change(self):
        """Swap the main preview between the 3D tag and the real printable-page layout."""
        try:
            self._ped_on_tab_change()      # v15.1: the page strip only belongs to PDF Studio
            tab = self.tabs.get()
            # v15.51: grow-only sidebar guard — if a tab's content is
            # naturally wider than the frozen bar (e.g. starting on a
            # narrow tab), widen once to fit; never shrink, so clicks
            # can't resize the bar. Skipped while the bar is hidden
            # (PDF Studio), where width reads are meaningless.
            try:
                if self.side.winfo_ismapped():
                    _req = self.side.winfo_reqwidth()
                    if _req > self.side.winfo_width():
                        self.side.configure(width=_req)
            except Exception:
                pass
            # v15.40: Maker/Tag Design show the embedded browser when
            # it is ready, else the built-in 3D view; every other tab
            # shows neither (the canvas owns those tabs). The embed
            # starts once, lazily, on first Maker entry. Both preview
            # frames are grid_remove()d elsewhere (v15.39: a gridded
            # frame keeps a 600px requested height that starves the PDF
            # canvas row and leaves the canvas unmapped).
            try:
                if tab in ("Maker Studio", "Tag Design"):
                    self._embed_update_visibility()
                else:
                    self._embed_host.grid_remove()
                    self._p3d_frame.grid_remove()
            except Exception:
                pass
            if tab == "Maker Studio":
                self._maker_lib_refresh()
            if tab != "Word Lists":
                self._lib_open = False
                self._lib_user_closed = False
            elif not self._lib_open and not self._lib_user_closed:
                # v13.14: the Library opens by itself whenever you go into Word Lists
                self._lib_open = True
                # v14.3: skip the full rescan/rebuild when nothing changed
                # since the last load — the tiles are still built.
                if getattr(self, "_lib_fp", None) is None or self._lib_fp != self._lib_fingerprint():
                    self._lib_reload()
                self._lib_update_hint()
            if tab == "PDF Studio":
                self._last_studio = "pdf"
            elif tab in ("Maker Studio", "Tag Design"):
                self._last_studio = "3d"
            if tab == AI_TAB:
                # v13.14: AI takes the WHOLE window (sidebar + preview hidden)
                self._show_ai_panel(True)
                self.word_menu.pack_forget()
                self.pic_pick_btn.pack_forget()
                self.pdf_sel_bar.pack_forget()
                self.log_btn.pack(side="right")
                self.info.configure(text="AI (Local) · on-device only — your words never leave this PC")
                return
            self._show_ai_panel(False)
            if tab == "Word Lists":
                self.word_menu.pack_forget()
                self.pic_pick_btn.pack_forget()
                self.log_btn.pack_forget()
                self.pdf_sel_bar.pack_forget()
                self.export_btn.configure(text="Export file", command=self.export_file)
                if self._lib_open:
                    self.info.configure(text="Library · right-click a word or picture to send it to the 3D tag or PDF Studio")
                    return
                # 13.14: no separate info preview — the canvas shows the
                # tag for the word you click, like everywhere else.
                self.draw()
                self.info.configure(text="Word Lists · click a word in the list to see its tag")
                return
            if tab == "PDF Studio":
                # v15.6: the editor IS PDF Studio — no separate card view.
                self._ped_ensure_session()
                self._ped_show()
                return
            if tab == "PDF Studio":
                self.word_menu.pack_forget()
                self.pic_pick_btn.pack_forget()
                self.log_btn.pack_forget()
                self.export_btn.configure(text="Save PDF…", command=self.export_pdf_studio)
                self._pdf_refresh_preset_menu()
                self._pdf_box_refresh_if_current()
                self._pdf_check_online()
                self._pdf_schedule_recheck()
                self.draw_pdf_preview(self.canvas)
                self._pdf_sync_sel_bar()
                self.info.configure(text="PDF Studio · click a card to select · drag to reorder · double-click for picture · right-click a blank card to add a picture")
                return
            # Maker / Tag Design / AI — 3D tag preview
            if tab == "Maker Studio":
                # v14.5: no option bar above the preview in Maker - the
                # sidebar already has the list dropdown, the tiles, the
                # word pick and the picture buttons.
                self.word_menu.pack_forget()
                self.pic_pick_btn.pack_forget()
            else:
                # v15.51: Tag Design keeps NEITHER control above the
                # preview now — the word dropdown went in 15.50 and
                # the Picture button goes here (Cody: get rid of it).
                # The bar above the preview is just the settings/log
                # buttons on the right.
                self.pic_pick_btn.pack_forget()
                self.word_menu.pack_forget()
                try:
                    ws = [w for w in self._maker_preview_words() if w]
                    cur = self.preview_word.get()
                    if ws and cur not in ws:
                        self.preview_word.set(ws[0])
                        self._preview_word_explicit = False
                        try:
                            self.word_menu.set(ws[0])
                        except Exception:
                            pass
                        self._preview3d_soon()
                except Exception:
                    pass
            self.log_btn.pack(side="right")
            self.pdf_sel_bar.pack_forget()
            self.export_btn.configure(text="Export file", command=self.export_file)
            w_now = self.preview_word.get()
            if self._tag_stale or (w_now and w_now != "-" and (not self.geo or self.geo[2] != w_now)):
                self._show_word_now(w_now)   # word first, picture after
                self.changed()
            else:
                self.draw()
            self.info.configure(text="" if not self._loading else self._loading["msg"])
        except Exception:
            pass


    def draw_pdf_preview(self, canvas=None):
        """Document view: WYSIWYG pages. Pictures fetch in the background;
        click a card to select its word, double-click to pick its picture."""
        canvas = canvas or self.canvas
        if canvas is None or not canvas.winfo_exists():
            return
        if canvas is self.canvas and self.tabs.get() != "PDF Studio":
            return
        if self._ped_active and canvas is self.canvas:
            self._ped_draw()               # v15.1: the PDF editor owns the canvas
            return
        self._pdf_icon_gen += 1
        gen = self._pdf_icon_gen
        canvas.delete("all")
        words = self._pdf_studio_words()
        self._pdf_wanted = set(words)
        if self._pdf_selected not in words:
            self._pdf_selected = None
            self._pdf_sync_sel_bar()
            self._pdf_sync_sel_panel()
        # Keep multi-select set aligned with current words
        try:
            self._pdf_selected_set = {w for w in getattr(self, "_pdf_selected_set", set()) if w in words}
            if self._pdf_selected:
                self._pdf_selected_set.add(self._pdf_selected)
        except Exception:
            self._pdf_selected_set = set()
        self._pdf_card_geom = []
        self._pdf_blank_geom = []
        self._pdf_handle_geom = []
        self._pdf_card_draw = {}
        w, h = max(canvas.winfo_width(), 300), max(canvas.winfo_height(), 400)
        bg = "#2C2F3A" if ctk.get_appearance_mode() == "Dark" else "#F1EDF5"
        canvas.configure(bg=bg)
        layout_name_now = self.pdf_layout_var.get()
        paper_now = self.pdf_paper.get() if hasattr(self, "pdf_paper") else "letter"
        cols, rows = aac_boards.PECS_LAYOUTS.get(layout_name_now, (2, 4))
        per = cols * rows
        pages_from_words = max(1, (len(words) + per - 1) // per) if per else 1
        try:
            pages_explicit = max(1, min(12, int(self.pdf_pages_var.get())))
        except (TypeError, ValueError, AttributeError):
            pages_explicit = 1
        pages = max(pages_from_words, pages_explicit)
        # Keep the pages control in sync when words alone force more pages
        try:
            if pages_from_words > pages_explicit and hasattr(self, "pdf_pages_var"):
                self.pdf_pages_var.set(str(pages_from_words))
        except Exception:
            pass
        self.pdf_page = max(0, min(getattr(self, "pdf_page", 0), pages - 1))
        try:
            self.pdf_page_label.configure(text=f"Page {self.pdf_page + 1} of {pages}")
        except Exception:
            pass
        self._pdf_preview_images = []
        show_pics = self.pdf_show_pics.get() if hasattr(self, "pdf_show_pics") else True
        show_words = self.pdf_show_words.get() if hasattr(self, "pdf_show_words") else True
        wscale = float(self.pdf_word_scale.get()) if hasattr(self, "pdf_word_scale") else 1.0
        pscale = float(self.pdf_pic_scale.get()) if hasattr(self, "pdf_pic_scale") else 1.0
        pic_y = float(self.pdf_pic_y.get()) if hasattr(self, "pdf_pic_y") else 0.35
        word_y = float(self.pdf_word_y.get()) if hasattr(self, "pdf_word_y") else 0.12
        use_cat = self.pdf_colors_var.get()
        tile_colors = getattr(self, "pdf_tile_colors", {})
        # v13.25: interactive zoom + pan (mouse-wheel / buttons + drag empty space)
        z = float(getattr(self, "_pdf_zoom", 1.0) or 1.0)
        pan_x, pan_y = getattr(self, "_pdf_pan", [0.0, 0.0])
        try:
            self.pdf_zoom_label.configure(text=f"{round(z * 100)}%")
        except Exception:
            pass
        os_credit = aac_boards.uses_opensymbols_credit(words, self.overrides)

        def _T(x, y=None):
            """Map layout coords \u2192 canvas coords under current zoom/pan."""
            if y is None:
                return x * z + pan_x
            return x * z + pan_x, y * z + pan_y

        def page_layout(page_idx):
            """v15.4: the shared page layout (aac_boards.pecs_page_layout \u2014
            the same numbers the printed export draws from), cached per page
            so zoom/pan/selection repaints reuse it untouched."""
            page_words = words[page_idx * per:(page_idx + 1) * per]
            key = (page_idx, tuple(page_words), layout_name_now, paper_now, pages,
                   use_cat, show_words, show_pics, wscale, pscale, pic_y, word_y,
                   frozenset((self.pdf_pic_scale_map or {}).items()),
                   frozenset((self.pdf_pic_y_map or {}).items()),
                   frozenset((str(k), str(v)) for k, v in (tile_colors or {}).items()),
                   frozenset(self.pdf_no_label or ()), os_credit)
            cache = getattr(self, "_pdf_layout_cache", None)
            if cache is None:
                cache = self._pdf_layout_cache = {}
            lay = cache.get(key)
            if lay is None:
                if len(cache) > 24:
                    cache.clear()
                lay = aac_boards.pecs_page_layout(
                    page_words, layout_name=layout_name_now, paper=paper_now,
                    page_index=page_idx, page_count=pages,
                    first_index=page_idx * per,
                    use_category_colors=use_cat, tile_colors=tile_colors,
                    show_labels=show_words, show_pictures=show_pics,
                    word_scale=wscale, pic_scale=pscale, pic_y=pic_y,
                    pic_scale_map=self.pdf_pic_scale_map,
                    pic_y_map=self.pdf_pic_y_map,
                    word_y=word_y, hide_labels_for=self.pdf_no_label)
                if os_credit:
                    lay["footer"] += ("\nSome pictures via OpenSymbols (Mulberry CC BY-SA"
                                       " \u00b7 Sclera CC BY-NC \u00b7 Tawasol CC BY-SA)")
                cache[key] = lay
            return lay

        def draw_page(page_idx, px0, py0, ppw, pph):
            lay = page_layout(page_idx)
            pw_in = lay["page_w_in"]
            # Apply zoom/pan to the page rectangle
            x0, y0 = _T(px0, py0)
            x1, y1 = _T(px0 + ppw, py0 + pph)
            canvas.create_rectangle(x0 + 4 * z, y0 + 5 * z, x1 + 4 * z, y1 + 5 * z,
                                    fill="#111923", outline="", tags=("pdfcontent",))
            canvas.create_rectangle(x0, y0, x1, y1, fill="white", outline="#aab4c0",
                                    tags=("pdfcontent",))
            ppi = (ppw * z) / pw_in  # canvas px per inch, zoom included

            def X(xin):
                return x0 + xin * ppi

            def Y(yin):
                return y0 + yin * ppi

            side = lay["side_in"] * ppi
            if side <= 4:
                return
            for slot in lay["slots"]:
                sx, sy = X(slot["x_in"]), Y(slot["y_in"])
                gi = slot["index"]
                word = slot["word"]
                # dashed cut guide around every slot, as printed
                canvas.create_rectangle(sx - .02 * ppi, sy - .02 * ppi,
                                        sx + side + .02 * ppi, sy + side + .02 * ppi,
                                        outline="#9299a2", dash=(3, 3), width=1,
                                        tags=("pdfcontent",))
                if word is None:
                    self._pdf_blank_geom.append((gi, sx, sy, side))
                    continue
                canvas.create_rectangle(sx, sy, sx + side, sy + side,
                                        fill=slot["color"], outline="#222222", width=1,
                                        tags=("pdfcontent",))
                if slot["blank"]:
                    self._pdf_blank_geom.append((gi, sx, sy, side))
                    continue
                self._pdf_card_geom.append((word, sx, sy, side, gi))
                info = {"word": word, "x": sx, "y": sy, "side": side, "icon_rect": None}
                self._pdf_card_draw[gi] = info
                wl = slot["label"] is not None
                big = False
                if not show_pics:
                    self._pdf_draw_big_word(canvas, slot, sx, sy, side, ppi)
                    big = True
                else:
                    key = self._pdf_cache_key(word)
                    img = self._pdf_icon_cache.get(key, "missing")
                    if slot["icon_rect"] is not None:
                        ix, iy, iw, ih = slot["icon_rect"]
                        info["icon_rect"] = (X(ix), Y(iy), iw * ppi, ih * ppi)
                    if img == "missing":
                        canvas.create_text(sx + side / 2, sy + side * (.40 if wl else .5),
                                           text="\u2026", fill="#9aa2ad",
                                           font=("Arial", max(10, int(side * 0.22))),
                                           tags=("pdfcontent", f"pdfph-{gi}"))
                        self._pdf_queue_fetch(gen, word, max(96, int(side * 2)))
                    elif img is not None:
                        self._pdf_draw_icon_rect(canvas, info["icon_rect"], img,
                                                 tag_extra=f"pdficon-{gi}")
                    else:
                        # no picture exists for this word \u2014 the WORD is the card
                        self._pdf_draw_big_word(canvas, slot, sx, sy, side, ppi)
                        big = True
                if wl and not big:
                    lab = slot["label"]
                    fs = self._pdf_font_px(word, lab["fs_pt"], lab["max_w_in"], ppi)
                    canvas.create_text(X(lab["cx_in"]), Y(lab["cy_in"]), text=word,
                                       fill="#111", font=("Arial", fs, "bold"),
                                       width=lab["max_w_in"] * ppi,
                                       tags=("pdfcontent", f"pdflabel-{gi}"))
            canvas.create_text((x0 + x1) / 2, Y(lay["footer_y_in"]), text=lay["footer"],
                               fill="#555", font=("Arial", -max(7, int(8 * ppi / 72))),
                               tags=("pdfcontent",))

        lay0 = page_layout(self.pdf_page)
        aspect = lay0["page_w_in"] / lay0["page_h_in"]
        mode = self.pdf_preview_mode.get() if hasattr(self, "pdf_preview_mode") else "One page"
        if mode == "All pages" and pages > 1:
            # Grid layout: arrange pages in rows/cols so the preview fills the area
            gap = 12
            best = (1, pages)
            for try_cols in range(1, pages + 1):
                try_rows = (pages + try_cols - 1) // try_cols
                cell_w = (w - 20 - (try_cols - 1) * gap) / try_cols
                cell_h = (h - 20 - (try_rows - 1) * gap) / try_rows
                page_w = min(cell_w, cell_h * aspect)
                page_h = page_w / aspect
                if page_w >= 40 and page_h >= 50:
                    best = (try_cols, try_rows)
                    # Prefer grids that use more of the width when close
                    if try_cols >= 2 and page_w * try_cols >= w * 0.55:
                        break
            grid_cols, grid_rows = best
            cell_w = (w - 20 - (grid_cols - 1) * gap) / grid_cols
            cell_h = (h - 20 - (grid_rows - 1) * gap) / grid_rows
            pw = min(cell_w, cell_h * aspect)
            ph = pw / aspect
            total_w = grid_cols * pw + (grid_cols - 1) * gap
            total_h = grid_rows * ph + (grid_rows - 1) * gap
            ox = (w - total_w) / 2
            oy = max(8, (h - total_h) / 2)
            for pidx in range(pages):
                r, col = divmod(pidx, grid_cols)
                draw_page(pidx, ox + col * (pw + gap), oy + r * (ph + gap), pw, ph)
        else:
            margin = 22
            pw = min(w - margin * 2, (h - margin * 2) * aspect)
            ph = pw / aspect
            x0, y0 = (w - pw) / 2, max(10, (h - ph) / 2)
            draw_page(self.pdf_page, x0, y0, pw, ph)
        if canvas is self.canvas:
            self._pdf_paint_sig = self._pdf_content_sig()
            self._pdf_paint_overlay()

    # ---- v3.7.0-exp: async pictures + click selection for the document view
    def _pdf_cache_key(self, word):
        var = self.variants.get(word, 0)
        sa = (self.search_as.get(word) or word).strip() or word
        ov = self.overrides.get(word)
        return (word, var, sa, ov if ov and Path(ov).exists() else None)

    def _pdf_draw_icon_rect(self, canvas, rect, pil_img, tag_extra=None):
        """Draw a card icon filling its shared-layout rect \u2014 the exact rect
        the printed export uses (v15.4)."""
        from PIL import Image, ImageTk
        import numpy as np
        if rect is None:
            return
        try:
            rx, ry, rw, rh = rect
            rw, rh = max(1, int(rw)), max(1, int(rh))
            im = Image.fromarray(np.asarray(pil_img).astype('uint8'))
            if im.size != (rw, rh):
                im = im.resize((rw, rh), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(im)
            self._pdf_preview_images.append(photo)
            tags = ("pdfcontent",) + ((tag_extra,) if tag_extra else ())
            canvas.create_image(rx, ry, image=photo, anchor="nw", tags=tags)
        except Exception:
            pass

    def _pdf_font_px(self, text, fs_pt, max_w_in, ppi):
        """Tk font size (negative = exact pixels) for layout text: the
        printed point size converted at the canvas resolution, shrunk only
        if the word would overflow its printed width."""
        import tkinter.font as tkfont
        px = max(8, int(round(float(fs_pt) * ppi / 72.0)))
        max_w_px = max(10.0, float(max_w_in) * ppi)
        memo = getattr(self, "_pdf_font_memo", None)
        if memo is None:
            memo = self._pdf_font_memo = {}
        mkey = (str(text), px, int(max_w_px))
        if mkey in memo:
            return memo[mkey]
        cache = getattr(self, "_pdf_px_font_cache", None)
        if cache is None:
            cache = self._pdf_px_font_cache = {}
        size = px
        while size > 8:
            f = cache.get(size)
            if f is None:
                f = cache[size] = tkfont.Font(family="Arial", size=-size, weight="bold")
            if f.measure(str(text)) <= max_w_px:
                break
            size -= 1
        if len(memo) > 3000:
            memo.clear()
        memo[mkey] = -size
        return -size

    def _pdf_draw_big_word(self, canvas, slot, sx, sy, side, ppi):
        """Word-only card (no picture): the word big and centered, at the
        shared layout's size \u2014 the same size it prints."""
        word = slot["word"]
        fs = self._pdf_font_px(word, slot["big_word_fs_pt"] or 12,
                               slot["side_in"] * 0.9, ppi)
        canvas.create_text(sx + side / 2, sy + side / 2, text=word, fill="#111",
                           font=("Arial", fs, "bold"), width=side * .9,
                           tags=("pdfcontent", f"pdfph-{slot['index']}"))

    def _pdf_content_sig(self):
        """Signature of everything that shapes the painted cards (not the
        selection) \u2014 lets selection changes repaint only the overlay."""
        try:
            c = self.canvas
            return (
                tuple(self._pdf_studio_words()),
                self.pdf_layout_var.get(),
                self.pdf_paper.get() if hasattr(self, "pdf_paper") else "letter",
                self.pdf_pages_var.get() if hasattr(self, "pdf_pages_var") else "1",
                self.pdf_preview_mode.get() if hasattr(self, "pdf_preview_mode") else "One page",
                getattr(self, "pdf_page", 0),
                bool(self.pdf_show_pics.get()) if hasattr(self, "pdf_show_pics") else True,
                bool(self.pdf_show_words.get()) if hasattr(self, "pdf_show_words") else True,
                bool(self.pdf_colors_var.get()),
                float(self.pdf_word_scale.get()) if hasattr(self, "pdf_word_scale") else 1.0,
                float(self.pdf_pic_scale.get()) if hasattr(self, "pdf_pic_scale") else 1.0,
                float(self.pdf_pic_y.get()) if hasattr(self, "pdf_pic_y") else 0.35,
                float(self.pdf_word_y.get()) if hasattr(self, "pdf_word_y") else 0.12,
                frozenset((self.pdf_pic_scale_map or {}).items()),
                frozenset((self.pdf_pic_y_map or {}).items()),
                frozenset((str(k), str(v))
                          for k, v in (getattr(self, "pdf_tile_colors", {}) or {}).items()),
                frozenset(self.pdf_no_label or ()),
                float(getattr(self, "_pdf_zoom", 1.0) or 1.0),
                tuple(getattr(self, "_pdf_pan", [0.0, 0.0])),
                (max(c.winfo_width(), 300), max(c.winfo_height(), 400)) if c is not None else (0, 0),
            )
        except Exception:
            return None

    def _pdf_paint_overlay(self):
        """v15.4: selection outlines, the blank-card highlight and the drag
        handles live on their own canvas layer \u2014 changing the selection
        repaints only this, never the cards underneath."""
        canvas = self.canvas
        if canvas is None or not canvas.winfo_exists():
            return
        try:
            canvas.delete("pdfoverlay")
        except Exception:
            return
        self._pdf_handle_geom = []
        sel_set = getattr(self, "_pdf_selected_set", set()) or set()
        for word, x, y, side, i in getattr(self, "_pdf_card_geom", []):
            if word == self._pdf_selected or word in sel_set:
                canvas.create_rectangle(x - 2, y - 2, x + side + 2, y + side + 2,
                                        outline="#1f6feb", width=3, tags=("pdfoverlay",))
        bsel = getattr(self, "_pdf_blank_selected", None)
        if bsel is not None:
            for gi, x, y, side in getattr(self, "_pdf_blank_geom", []):
                if gi == bsel:
                    canvas.create_rectangle(x - 2, y - 2, x + side + 2, y + side + 2,
                                            outline="#4ECDC4", width=3, tags=("pdfoverlay",))
                    if side >= 60:
                        canvas.create_text(x + side / 2, y + side / 2,
                                           text="add a picture\u2026",
                                           fill="#4ECDC4",
                                           font=("Arial", max(9, int(side * 0.11)), "bold"),
                                           width=side * .9, tags=("pdfoverlay",))
                    break
        w = self._pdf_selected
        if w:
            for word2, x, y, side, i in getattr(self, "_pdf_card_geom", []):
                if word2 == w:
                    info = getattr(self, "_pdf_card_draw", {}).get(i)
                    if info is not None and info.get("icon_rect"):
                        self._pdf_draw_handles(canvas, w, x, y, side, info["icon_rect"])
                    break

    def _pdf_refresh_overlay(self):
        """Selection changed: repaint just the overlay when the painted
        content is unchanged; fall back to a full redraw otherwise."""
        try:
            if (not self._ped_active and self.tabs.get() == "PDF Studio"
                    and getattr(self, "_pdf_paint_sig", None) is not None
                    and self._pdf_paint_sig == self._pdf_content_sig()
                    and getattr(self, "_pdf_card_geom", None)):
                self._pdf_paint_overlay()
                return
        except Exception:
            pass
        self.draw_pdf_preview()

    def _pdf_draw_handles(self, canvas, word, x, y, side, icon_rect):
        """Drag handles on the selected card, riding on the picture's layout
        rect: square (bottom-right) = picture size, circle (mid-right) =
        vertical position."""
        hr = max(5, min(9, int(side * 0.07)))
        rx, ry, rw, rh = icon_rect
        sx = rx + rw - hr - 2
        sy = ry + rh - hr - 2
        mx = rx + rw - hr - 2
        my = ry + rh / 2
        # Scale handle (square)
        canvas.create_rectangle(sx - hr, sy - hr, sx + hr, sy + hr,
                                fill="#1f6feb", outline="white", width=1,
                                tags=("pdfoverlay", "pdfhandle"))
        # Vertical position handle (circle)
        canvas.create_oval(mx - hr, my - hr, mx + hr, my + hr,
                           fill="#4ECDC4", outline="white", width=1,
                           tags=("pdfoverlay", "pdfhandle"))
        self._pdf_handle_geom.append({"word": word, "kind": "scale",
                                      "x": sx, "y": sy, "r": hr + 4, "side": side,
                                      "cx": x, "cy": y})
        self._pdf_handle_geom.append({"word": word, "kind": "pos",
                                      "x": mx, "y": my, "r": hr + 4, "side": side,
                                      "cx": x, "cy": y})

    def _pdf_handle_hit(self, event):
        """Return the handle under the cursor, or None."""
        for h in reversed(getattr(self, "_pdf_handle_geom", []) or []):
            if abs(event.x - h["x"]) <= h["r"] and abs(event.y - h["y"]) <= h["r"]:
                return h
        return None

    def _pdf_queue_fetch(self, gen, word, size_px):
        self._pdf_ensure_fetcher()
        key = self._pdf_cache_key(word)
        ov, var, sa = dict(self.overrides), {word: self.variants.get(word, 0)}, dict(self.search_as)
        self._pdf_fetch_q.put((gen, word, size_px, key, ov, var, sa))

    def _pdf_ensure_fetcher(self):
        if self._pdf_fetch_thread and self._pdf_fetch_thread.is_alive():
            return
        def worker():
            while True:
                try:
                    gen, word, size_px, key, ov, var, sa = self._pdf_fetch_q.get(timeout=30)
                except queue.Empty:
                    return  # idle: die; restarted on demand
                try:
                    if gen != self._pdf_icon_gen or word not in self._pdf_wanted:
                        continue
                    img = aac_boards._load_icon_rgba(word, size_px=size_px, overrides=ov,
                                                     variants=var, search_as=sa, local_only=False)
                except Exception as e:
                    log.debug("PDF preview fetch failed for %s: %s", word, e)
                    img = None
                try:
                    self.ui_call(self._pdf_icon_arrived, gen, key, word, img)
                except Exception:
                    return
        self._pdf_fetch_thread = threading.Thread(target=worker, daemon=True)
        self._pdf_fetch_thread.start()

    def _pdf_icon_arrived(self, gen, key, word, img):
        try:
            if gen != self._pdf_icon_gen or self.tabs.get() != "PDF Studio":
                return
            if len(self._pdf_icon_cache) > 400:
                for k in list(self._pdf_icon_cache)[:100]:
                    del self._pdf_icon_cache[k]
            self._pdf_icon_cache[key] = img
            if img is None:
                # nothing found: redraw turns the "…" into a word-only card
                self._queue_pdf_preview()
                return
            for w, x, y, side, i in self._pdf_card_geom:
                if w == word and self._pdf_cache_key(w) == key:
                    self.canvas.delete(f"pdfph-{i}")
                    info = getattr(self, "_pdf_card_draw", {}).get(i)
                    if info is not None and info.get("icon_rect"):
                        # v15.4: draw into the card's layout rect (per-card
                        # size/position included), then refresh handles
                        self._pdf_draw_icon_rect(self.canvas, info["icon_rect"], img,
                                                 tag_extra=f"pdficon-{i}")
                        self._pdf_paint_overlay()
                    else:
                        self._queue_pdf_preview()
                    break
        except Exception:
            log.exception("PDF icon arrival failed for %s", word)

    def _on_pdf_canvas_click(self, event):
        tab = self.tabs.get()
        if self._ped_active and tab == "PDF Studio":
            return self._ped_on_click(event)
        # v13.29: click a tag in multi-preview to select it for per-tag editing
        if tab in ("Maker Studio", "Tag Design") and getattr(self, "maker_multi", None) \
                and self.maker_multi.get() and getattr(self, "_multi_hit", None):
            for w, x0, y0, x1, y1 in self._multi_hit:
                if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                    self._select_multi_tag(w)
                    return
            # click empty space → clear selection (edit all again)
            if self._selected_multi is not None:
                self._select_multi_tag(None)
            return
        if tab != "PDF Studio":
            return
        # v15.3: handle hits take priority (scale / vertical position)
        h = self._pdf_handle_hit(event)
        if h is not None:
            self._pdf_handle_drag = {
                "kind": h["kind"], "word": h["word"],
                "x0": event.x, "y0": event.y,
                "side": h["side"], "cx": h["cx"], "cy": h["cy"],
                "ps0": float(self.pdf_pic_scale_map.get(h["word"],
                              self.pdf_pic_scale.get() if hasattr(self, "pdf_pic_scale") else 1.0)),
                "py0": float(self.pdf_pic_y_map.get(h["word"],
                              self.pdf_pic_y.get() if hasattr(self, "pdf_pic_y") else 0.35)),
            }
            self._pdf_drag = None
            self._pdf_pan_drag = None
            return
        multi = bool(event.state & 0x4)  # Control held
        for w, x, y, side, i in self._pdf_card_geom:
            if x <= event.x <= x+side and y <= event.y <= y+side:
                self._pdf_select(w, multi=multi)
                if multi:
                    self._pdf_drag = None
                    self._pdf_pan_drag = None
                    return
                # 13.14: remember the press — a drag may follow (reorder)
                self._pdf_drag = {"i": i, "word": w, "side": side,
                                  "x0": event.x, "y0": event.y, "active": False}
                self._pdf_pan_drag = None
                return
        # v14.4: a click on a BLANK card highlights it too (a drag from it
        # still pans the page, exactly like empty space did)
        gi = self._pdf_blank_hit(event)
        if gi is not None:
            self._pdf_highlight_blank(gi)
            self._pdf_drag = None
            self._pdf_pan_drag = {
                "x0": event.x, "y0": event.y,
                "pan0": list(getattr(self, "_pdf_pan", [0.0, 0.0])),
            }
            return
        # v13.25: click on empty space → start a pan
        self._pdf_drag = None
        self._pdf_pan_drag = {
            "x0": event.x, "y0": event.y,
            "pan0": list(getattr(self, "_pdf_pan", [0.0, 0.0])),
        }
        self._pdf_deselect()

    def _pdf_canvas_hit(self, event):
        for w, x, y, side, i in self._pdf_card_geom:
            if x <= event.x <= x + side and y <= event.y <= y + side:
                return w, i
        return None, None

    def _on_pdf_canvas_drag(self, event):
        """Drag a PDF Studio tile (reorder), handle (scale/pos), or empty space (pan)."""
        if self.tabs.get() != "PDF Studio":
            return
        if self._ped_active:
            return self._ped_on_drag(event)
        # v15.3: picture handle drag (scale / vertical position)
        hd = getattr(self, "_pdf_handle_drag", None)
        if hd is not None:
            side = max(1.0, float(hd["side"]))
            if hd["kind"] == "scale":
                # Drag out from centre of card → bigger picture
                dx = event.x - (hd["cx"] + side / 2)
                dy = event.y - (hd["cy"] + side / 2)
                dist = (dx * dx + dy * dy) ** 0.5
                # Map distance relative to side into a scale around the start value
                delta = (event.x - hd["x0"] + event.y - hd["y0"]) / side
                new_ps = max(0.4, min(1.8, hd["ps0"] + delta * 0.9))
                self.pdf_pic_scale_map[hd["word"]] = new_ps
            else:
                # Vertical position: map drag to fraction of card height
                rel = (event.y - hd["cy"]) / side
                new_py = max(0.15, min(0.75, rel))
                self.pdf_pic_y_map[hd["word"]] = new_py
            self.draw_pdf_preview()
            return
        # Pan when the press started on empty space
        pd = getattr(self, "_pdf_pan_drag", None)
        if pd is not None:
            dx = event.x - pd["x0"]
            dy = event.y - pd["y0"]
            self._pdf_pan = [pd["pan0"][0] + dx, pd["pan0"][1] + dy]
            self.draw_pdf_preview()
            return
        d = getattr(self, "_pdf_drag", None)
        if not d:
            return
        if not d["active"] and (abs(event.x - d["x0"]) + abs(event.y - d["y0"]) < 8):
            return
        d["active"] = True
        c = self.canvas
        c.delete("pdfdrag")
        s = d["side"]
        c.create_rectangle(event.x - s / 2, event.y - s / 2, event.x + s / 2, event.y + s / 2,
                           outline="#1f6feb", width=2, fill="#1f6feb", stipple="gray50",
                           tags=("pdfdrag",))
        w, i = self._pdf_canvas_hit(event)
        if w is not None and i != d["i"]:
            for gw, gx, gy, gs, gi in self._pdf_card_geom:
                if gi == i:
                    c.create_rectangle(gx - 3, gy - 3, gx + gs + 3, gy + gs + 3,
                                       outline="#6bcb77", width=3, tags=("pdfdrag",))
                    break

    def _on_pdf_canvas_release(self, event):
        """End pan, handle drag, or drop a dragged card onto a new slot."""
        if self._ped_active and self.tabs.get() == "PDF Studio":
            return self._ped_on_release(event)
        hd = getattr(self, "_pdf_handle_drag", None)
        self._pdf_handle_drag = None
        if hd is not None:
            w = hd.get("word", "")
            if hd["kind"] == "scale":
                v = self.pdf_pic_scale_map.get(w)
                self.flash_status(f"Picture size for “{w}”: {round(float(v or 1) * 100)}%", ok=True)
            else:
                v = self.pdf_pic_y_map.get(w)
                self.flash_status(f"Picture position for “{w}” updated", ok=True)
            try:
                self.save()
            except Exception:
                pass
            return
        pd = getattr(self, "_pdf_pan_drag", None)
        self._pdf_pan_drag = None
        if pd is not None:
            return  # pan already applied live during drag
        d = getattr(self, "_pdf_drag", None)
        self._pdf_drag = None
        if not d or not d.get("active") or self.tabs.get() != "PDF Studio":
            return
        try:
            self.canvas.delete("pdfdrag")
        except Exception:
            pass
        w, dst = self._pdf_canvas_hit(event)
        src = d["i"]
        if w is None or dst is None or dst == src:
            return
        items = list(self._pdf_words)
        if not (0 <= src < len(items) and 0 <= dst < len(items)):
            return
        val = items.pop(src)
        items.insert(dst, val)
        self._pdf_words = items
        self._pdf_render_tiles()
        self._pdf_select(d["word"])
        self._queue_pdf_preview()
        self.flash_status(f"Moved “{d['word']}” to position {dst + 1}")

    def _on_pdf_canvas_dblclick(self, event):
        if self.tabs.get() != "PDF Studio":
            return
        if self._ped_active:
            return self._ped_on_dblclick(event)
        for w, x, y, side, i in self._pdf_card_geom:
            if x <= event.x <= x+side and y <= event.y <= y+side:
                self._pdf_select(w)
                # v15.3: double-click lower third (word label) → in-place rename
                # upper area → find pictures (previous behaviour)
                rel_y = (event.y - y) / max(side, 1)
                if rel_y >= 0.68:
                    self._pdf_start_inline_edit(w, x, y, side)
                else:
                    self.pdf_find_pictures()
                return

    def _pdf_start_inline_edit(self, word, x, y, side):
        """Overlay an Entry on the card label for in-place rename."""
        self._pdf_end_inline_edit(commit=False)
        try:
            word_y = float(self.pdf_word_y.get()) if hasattr(self, "pdf_word_y") else 0.12
        except Exception:
            word_y = 0.12
        entry = tk.Entry(self.canvas, font=("Arial", max(10, int(side * 0.12)), "bold"),
                         justify="center", bd=1, relief="solid",
                         bg="#fffef0", fg="#111")
        entry.insert(0, word)
        entry.select_range(0, "end")
        ew = max(40, int(side * 0.9))
        eh = max(18, int(side * 0.16))
        ex = x + (side - ew) / 2
        ey = y + side * (1 - word_y) - eh / 2
        win_id = self.canvas.create_window(ex, ey, window=entry, anchor="nw",
                                           width=ew, height=eh, tags=("pdfinline",))
        self._pdf_inline_edit = {"word": word, "entry": entry, "win": win_id}

        def commit(_e=None):
            self._pdf_end_inline_edit(commit=True)

        def cancel(_e=None):
            self._pdf_end_inline_edit(commit=False)

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>", cancel)
        entry.focus_set()

    def _pdf_end_inline_edit(self, commit=True):
        ed = getattr(self, "_pdf_inline_edit", None)
        if not ed:
            return
        self._pdf_inline_edit = None
        old = ed.get("word")
        entry = ed.get("entry")
        new = ""
        try:
            new = (entry.get() or "").strip()
        except Exception:
            pass
        try:
            entry.destroy()
        except Exception:
            pass
        try:
            self.canvas.delete("pdfinline")
        except Exception:
            pass
        if not commit or not old or not new or new == old:
            return
        # Reuse the existing rename path via the sidebar entry
        try:
            self.pdf_rename_entry.delete(0, "end")
            self.pdf_rename_entry.insert(0, new)
        except Exception:
            pass
        self._pdf_selected = old
        self._pdf_rename_selected()
        # Keep per-card picture maps under the new name
        if old in self.pdf_pic_scale_map:
            self.pdf_pic_scale_map[new] = self.pdf_pic_scale_map.pop(old)
        if old in self.pdf_pic_y_map:
            self.pdf_pic_y_map[new] = self.pdf_pic_y_map.pop(old)

    def _on_pdf_canvas_wheel(self, event):
        """Mouse-wheel zooms the PDF preview toward the cursor."""
        if self.tabs.get() != "PDF Studio":
            return
        if self._ped_active:
            return self._ped_on_wheel(event)
        # Windows/macOS: event.delta; Linux: Button-4/5
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            factor = 1.15
        elif getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0:
            factor = 1 / 1.15
        else:
            return
        self._pdf_set_zoom(self._pdf_zoom * factor, focus=(event.x, event.y))

    # ---- v14.3: blank PDF tiles — right-click, highlight, add a picture
    def _pdf_blank_hit(self, event):
        """Slot index of the blank tile under the cursor, or None."""
        for gi, x, y, side in getattr(self, "_pdf_blank_geom", []):
            if x <= event.x <= x + side and y <= event.y <= y + side:
                return gi
        return None

    def _on_pdf_canvas_right_click(self, event):
        """Right-click in the PDF preview (v14.3, Cody's ask): a blank tile
        gets highlighted and offers 'add a picture'; a filled card gets
        highlighted and offers its picture actions.
        v15.2: also place a picture straight from the Library into the tile."""
        if self.tabs.get() != "PDF Studio":
            return
        # Filled card first (same actions the sidebar offers)
        for w, x, y, side, i in self._pdf_card_geom:
            if x <= event.x <= x + side and y <= event.y <= y + side:
                self._pdf_select(w)
                m = tk.Menu(self, tearoff=0)
                m.add_command(label="📚  Place from Library…",
                              command=lambda word=w: self._pdf_library_pick(word=word))
                m.add_command(label="Find pictures…", command=self.pdf_find_pictures)
                m.add_command(label="My image…", command=self.pdf_choose_image)
                m.add_command(label="Picture…", command=self.open_pdf_picture_picker)
                # v14.4: create an image for this card's word, right here
                self._add_generate_entry(m, [w])
                m.add_separator()
                m.add_command(label="Rename this card…",
                              command=self._pdf_rename_selected_dialog)
                m.add_command(label="Card colour…",
                              command=self._pdf_pick_card_color)
                m.add_command(label="Reset card colour",
                              command=self._pdf_reset_card_color)
                try:
                    m.tk_popup(event.x_root, event.y_root)
                finally:
                    m.grab_release()
                return
        gi = self._pdf_blank_hit(event)
        if gi is None:
            return
        # Highlight the blank tile, then offer the picture choices
        self._pdf_highlight_blank(gi)
        self._pdf_blank_menu(gi, event.x_root, event.y_root)

    def _pdf_highlight_blank(self, slot):
        """Highlight one blank card slot (preview outline + word tile)."""
        self._pdf_selected = None
        self._pdf_blank_selected = slot
        self._pdf_sync_sel_panel()
        self._pdf_sync_sel_bar()
        self._pdf_sync_list_selection()
        self._pdf_refresh_overlay()

    def _pdf_blank_menu(self, slot, x_root, y_root):
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="📚  Place from Library…",
                      command=lambda slot=slot: self._pdf_library_pick(slot=slot))
        m.add_command(label="Add a picture here… (my image)",
                      command=lambda slot=slot: self._pdf_blank_choose_image(slot))
        m.add_command(label="Find pictures for a word here…",
                      command=lambda slot=slot: self._pdf_blank_find_pictures(slot))
        # v14.4: let the AI invent the word AND create its picture
        m.add_command(label="✨ Create AI word + image here…",
                      command=lambda slot=slot: self._pdf_ai_word_image(slot))
        if 0 <= slot < len(getattr(self, "_pdf_words", [])):
            m.add_separator()
            m.add_command(label="Remove blank card",
                          command=lambda slot=slot: self._pdf_remove_blank(slot))
        try:
            m.tk_popup(x_root, y_root)
        finally:
            m.grab_release()

    def _pdf_library_pick(self, word=None, slot=None):
        """Right-click → place a Library picture into a filled card or a blank tile.
        - word set  → replace that card's picture (keep the word)
        - slot set  → fill the blank slot with the Library item's word + picture
        """
        items = self._library_picture_items()
        if not items:
            return messagebox.showinfo(APP_NAME,
                "Your Library has no pictures yet.\n\n"
                "Find or upload pictures in Maker Studio / Word Lists first, "
                "or use “My image…” on this tile.")
        title = (f"Library — pick a picture for “{word}”"
                 if word else "Library — pick a picture for this blank card")
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.geometry("660x580")
        try:
            win.transient(self)
        except Exception:
            pass
        win.after(120, win.lift)
        ctk.CTkLabel(win, text=title, font=ctk.CTkFont(size=13, weight="bold"),
                     anchor="w").pack(fill="x", padx=14, pady=(12, 2))
        search = ctk.CTkEntry(win, placeholder_text="Filter by word…")
        search.pack(fill="x", padx=14, pady=(2, 8))
        grid = ctk.CTkScrollableFrame(win, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        thumbs = []

        def apply_pick(it):
            path = str(it["path"])
            lib_word = str(it.get("word") or Path(path).stem).strip() or "picture"
            if word:
                # Replace picture on an existing card
                self.overrides[word] = path
                self._no_auto_pic.discard(word)
                self._pdf_select(word)
                self.flash_status(f"Library picture “{lib_word}” placed on “{word}”", ok=True)
                self.changed()
                self.draw_pdf_preview()
            else:
                # Fill blank slot with the Library item's word + picture
                card_word = lib_word
                existing = {str(w).lower() for w in getattr(self, "_pdf_words", []) if w}
                if card_word.lower() in existing:
                    n = 2
                    while f"{lib_word} {n}".lower() in existing:
                        n += 1
                    card_word = f"{lib_word} {n}"
                self.overrides[card_word] = path
                self._no_auto_pic.add(card_word)
                self._pdf_insert_card_at(slot, card_word)
                self._pdf_blank_finish(slot, card_word)
                self.flash_status(f"Library picture “{lib_word}” placed on blank card", ok=True)
                self.changed()
            try:
                win.destroy()
            except Exception:
                pass

        def render(filter_text=""):
            for child in grid.winfo_children():
                child.destroy()
            thumbs.clear()
            ft = filter_text.strip().lower()
            shown = [it for it in items if not ft or ft in str(it["word"]).lower()]
            if not shown:
                ctk.CTkLabel(grid, text="No Library pictures match — try a different filter.",
                             text_color="gray", wraplength=520, justify="left",
                             anchor="w").grid(row=0, column=0, sticky="w", padx=4, pady=6)
                return
            from PIL import Image, ImageTk
            for c in range(4):
                grid.grid_columnconfigure(c, weight=1, uniform="pdflib")
            for i, it in enumerate(shown[:200]):
                r, c = divmod(i, 4)
                cell = ctk.CTkFrame(grid, fg_color=("gray90", "gray22"), corner_radius=8)
                cell.grid(row=r, column=c, padx=4, pady=4, sticky="nsew")
                try:
                    im = Image.open(it["path"]).convert("RGBA")
                    im.thumbnail((110, 110), Image.Resampling.LANCZOS)
                    ph = ImageTk.PhotoImage(im)
                    thumbs.append(ph)
                    lbl = ctk.CTkLabel(cell, image=ph, text="")
                    lbl.pack(padx=6, pady=(6, 2))
                except Exception:
                    ctk.CTkLabel(cell, text="?", width=110, height=80).pack(padx=6, pady=(6, 2))
                ctk.CTkLabel(cell, text=str(it["word"])[:18], font=ctk.CTkFont(size=11),
                             wraplength=120).pack(padx=4, pady=(0, 6))
                for wdg in (cell,):
                    wdg.bind("<Button-1>", lambda e, item=it: apply_pick(item))
                for child in cell.winfo_children():
                    child.bind("<Button-1>", lambda e, item=it: apply_pick(item))

        search.bind("<KeyRelease>", lambda e: render(search.get()))
        render()
        ctk.CTkButton(win, text="Cancel", width=90, height=28, fg_color="gray30",
                      hover_color="#555", command=win.destroy).pack(pady=(0, 12))

    def _pdf_remove_blank(self, slot):
        words = list(getattr(self, "_pdf_words", []))
        if 0 <= slot < len(words) and not str(words[slot]).strip():
            del words[slot]
            self._pdf_words = words
        self._pdf_blank_selected = None
        self._pdf_render_tiles()
        self.draw_pdf_preview()

    def _pdf_blank_label_for_image(self, path):
        """A card label from an image file name: 'my_dog-2.png' -> 'my dog 2'."""
        stem = Path(path).stem
        label = re.sub(r"[_-]+", " ", stem).strip()
        label = re.sub(r"\s+", " ", label)
        if not label:
            label = "picture"
        # keep card words unique (overrides/pictures are keyed by word)
        words = list(getattr(self, "_pdf_words", []))
        base, n = label, 2
        while label.lower() in {w.lower() for w in words if w}:
            label = f"{base} {n}"
            n += 1
        return label

    def _pdf_insert_card_at(self, slot, word):
        """Put `word` on the exact card slot, padding the gaps before it
        with blank placeholder cards (empty words). Placeholders keep the
        slot honest: they draw as blank tiles, print as blank cards, and
        can be filled or removed like any card."""
        words = list(getattr(self, "_pdf_words", []))
        while len(words) < slot:
            words.append("")
        if len(words) == slot:
            words.append(word)
        else:
            words[slot] = word
        self._pdf_words = words

    def _pdf_blank_finish(self, slot, word):
        self._pdf_blank_selected = None
        self._pdf_render_tiles()
        self._pdf_select(word)
        self.flash_status(f"\u201c{word}\u201d added to the blank card", ok=True)

    def _pdf_blank_choose_image(self, slot):
        """Blank tile -> 'my image': any picture file becomes the card."""
        p = filedialog.askopenfilename(
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.webp")])
        if not p:
            return
        label = self._pdf_blank_label_for_image(p)
        saved = self._save_upload_to_library(label, p)
        self._pdf_insert_card_at(slot, label)
        self.overrides[label] = saved
        self._pdf_blank_finish(slot, label)

    def _pdf_blank_find_pictures(self, slot):
        """Blank tile -> 'find pictures': ask the word, then the usual
        gallery; the picked picture lands on this exact slot."""
        import tkinter.simpledialog as sd
        word = sd.askstring(APP_NAME, "Which word should this card say?",
                            parent=self)
        if not word or not word.strip():
            return
        word = word.strip()
        # The gallery machinery works on the selected card's word, so park
        # the new word in its slot first, then open the gallery for it.
        self._pdf_insert_card_at(slot, word)
        self._pdf_blank_selected = None
        self._pdf_render_tiles()
        self._pdf_select(word)
        self.pdf_find_pictures()

    def _pdf_ai_word_image(self, slot):
        """v14.4: right-click a blank card \u2192 'Create AI word + image here'.
        The AI suggests a word for a situation you describe, the word goes
        on this exact card, and its picture is created right after."""
        if not self.need_ai():
            return
        topic = ctk.CTkInputDialog(
            text="What should the AI make a card for?\n(e.g. snack time, feeling sick, playground)",
            title="AI word + image").get_input()
        if not topic or not topic.strip():
            return
        topic = topic.strip()
        cats = list(aac_boards.CATEGORIES.keys())

        def fn():
            items = ai.suggest_words(topic, 6, cats)
            used = {w.lower() for w in getattr(self, "_pdf_words", []) if str(w).strip()}
            for it in items:
                w = (it.get("word") or "").strip()
                if w and w.lower() not in used:
                    return w, it.get("category") or ""
            if items:
                return (items[0].get("word") or "").strip(), items[0].get("category") or ""
            return "", ""

        def done(res):
            word, cat = res
            if not word:
                self._ai_banner("error", "text", "NO WORD CAME BACK",
                                "The AI didn't suggest a usable word \u2014 try again.")
                return
            if cat:
                try:
                    aac_boards.AI_CATEGORIES[word.lower()] = cat
                except Exception:
                    pass
            try:
                core.ai_register_words([word], kind="generated", topic=topic)
            except Exception:
                pass
            self._pdf_insert_card_at(slot, word)
            self._pdf_blank_selected = None
            self._pdf_render_tiles()
            self._pdf_select(word)
            self.flash_status(f"AI word \u201c{word}\u201d is on the card \u2014 creating its picture\u2026", ok=True)
            self.ai_generate_images([word])

        self._ai_task("text", "CREATING AI WORD + IMAGE",
                      f"\u201c{topic}\u201d  \u00b7  the word first, then its picture", fn, done)

    def _pdf_select(self, word, multi=False):
        """Select a card. multi=True (Ctrl) toggles membership in the selection set."""
        self._pdf_end_inline_edit(commit=True)
        self._pdf_blank_selected = None
        if multi:
            s = getattr(self, "_pdf_selected_set", set()) or set()
            if word in s:
                s.discard(word)
                if self._pdf_selected == word:
                    self._pdf_selected = next(iter(s), None)
            else:
                s.add(word)
                self._pdf_selected = word
            self._pdf_selected_set = s
        else:
            self._pdf_selected = word
            self._pdf_selected_set = {word} if word else set()
        self._pdf_sync_sel_panel()
        self._pdf_sync_sel_bar()
        self._pdf_sync_list_selection()
        self._pdf_sync_pic_buttons()
        self._pdf_refresh_overlay()

    def _pdf_deselect(self):
        self._pdf_end_inline_edit(commit=True)
        if (self._pdf_selected is None and getattr(self, "_pdf_blank_selected", None) is None
                and not getattr(self, "_pdf_selected_set", None)):
            self._pdf_sync_sel_panel()
            return
        self._pdf_selected = None
        self._pdf_selected_set = set()
        self._pdf_blank_selected = None
        self._pdf_sync_sel_bar()
        self._pdf_sync_sel_panel()
        self._pdf_sync_list_selection()
        self._pdf_refresh_overlay()

    def _pdf_delete_selected(self, event=None):
        """Delete key: remove selected PDF card(s) from the page list."""
        if self.tabs.get() != "PDF Studio" or self._ped_active:
            return
        if getattr(self, "_pdf_inline_edit", None):
            return  # let Backspace edit the entry text
        # v15.3.1: Delete/BackSpace are bound at app level — never treat
        # them as "delete card" while the user is typing in a text field.
        try:
            fw = self.focus_get()
            if fw is not None and fw.winfo_class() in (
                    "Entry", "Text", "TEntry", "Spinbox", "Combobox", "TCombobox"):
                return
        except Exception:
            pass
        sel = set(getattr(self, "_pdf_selected_set", set()) or set())
        if self._pdf_selected:
            sel.add(self._pdf_selected)
        if not sel:
            return
        words = list(getattr(self, "_pdf_words", []) or [])
        kept = [w for w in words if w not in sel]
        removed = len(words) - len(kept)
        if removed <= 0:
            return
        self._pdf_words = kept
        self._pdf_selected = None
        self._pdf_selected_set = set()
        self._pdf_render_tiles()
        self._pdf_sync_sel_panel()
        self._pdf_sync_sel_bar()
        self.draw_pdf_preview()
        self.flash_status(f"Removed {removed} card{'s' if removed != 1 else ''}", ok=True)
        return "break"

    def _pdf_sync_sel_panel(self):
        """Sidebar 'Selected card' panel mirrors the toolbar; disabled when nothing is selected."""
        try:
            w = self._pdf_selected
            if w:
                self.pdf_sel_word.configure(text=f"\u201c{w}\u201d",
                                            font=ctk.CTkFont(size=13, weight="bold"),
                                            text_color="#4a9eff")
                state = "normal"
            else:
                self.pdf_sel_word.configure(text="Click a card in the preview \u2191",
                                            font=ctk.CTkFont(size=12), text_color="gray")
                state = "disabled"
            for ctl in (self.pdf_pic_btn2, self.pdf_find_btn2, self.pdf_myimg_btn2,
                        self.pdf_label_check2, self.pdf_tile_btn2):
                ctl.configure(state=state)
            if w:
                idx = self._pdf_selected_index()
                key = str(idx) if idx is not None else ""
                self.pdf_tile_btn2.configure(fg_color=self.pdf_tile_colors.get(key, "#FFFFFF"))
        except Exception:
            pass

    def _pdf_sync_list_selection(self):
        """v14.2: selection is mirrored by the word tiles now."""
        self._pdf_style_tiles()
    def _pdf_sync_sel_bar(self):
        # v14.5: the little selected-card bar above the preview is gone
        # (Cody: keep everything in the sidebar). The sidebar's Selected
        # card panel does everything it did - this now only keeps the
        # shared Label checkbox variable in step with the selection
        # and makes sure the bar itself stays hidden.
        try:
            if self._pdf_selected:
                self.pdf_label_var.set(self._pdf_selected not in self.pdf_no_label)
            self.pdf_sel_bar.pack_forget()
        except Exception:
            pass

    def pdf_toggle_label(self):
        """Per-card switch: the picture already has the word on it, so skip our label."""
        w = self._pdf_selected
        if not w:
            return
        if self.pdf_label_var.get():
            self.pdf_no_label.discard(w)
        else:
            self.pdf_no_label.add(w)
        self.draw_pdf_preview()

    def _pdf_sync_pic_buttons(self):
        try:
            label = f"Picture {self.variants.get(self._pdf_selected, 0) + 1} \u25be"
            self.pdf_pic_btn.configure(text=label)
            self.pdf_pic_btn2.configure(text=label)
        except Exception:
            pass

    def open_pdf_picture_picker(self):
        w = self._pdf_selected
        if not w:
            return
        self.open_picture_picker(self.pdf_pic_btn2, w, self.variants.get(w, 0),
                                 lambda i: self.pdf_pick_index(i))

    def pdf_pick_index(self, i):
        w = self._pdf_selected
        if not w:
            return
        self.variants[w] = i
        self.overrides.pop(w, None)
        self._pdf_sync_pic_buttons()
        self.flash_status(f"Picture {i + 1} for \u201c{w}\u201d")
        self.draw_pdf_preview()

    def open_picture_picker(self, anchor, word, current_idx, on_pick):
        """Popup listing Picture 1-8 with a thumbnail next to each. on_pick(i) on click."""
        win = ctk.CTkToplevel(self)
        win.title(f"Picture for \u201c{word}\u201d")
        win.geometry("320x430")
        try:
            win.transient(self)
            x = anchor.winfo_rootx()
            y = anchor.winfo_rooty() + anchor.winfo_height() + 4
            win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        except Exception:
            pass
        win.after(200, win.lift)
        sc = ctk.CTkScrollableFrame(win, fg_color="transparent")
        sc.pack(fill="both", expand=True, padx=8, pady=8)
        rows = []
        for i in range(8):
            row = ctk.CTkFrame(sc, fg_color="transparent")
            row.pack(fill="x", pady=3)
            img_lbl = ctk.CTkLabel(row, text="\u2026", width=56, height=56,
                                   fg_color=("gray85", "gray25"), corner_radius=6)
            img_lbl.pack(side="left")
            btn = ctk.CTkButton(row, text=f"Picture {i + 1}", anchor="w", height=56,
                                fg_color="#2d5a3a" if i == current_idx else "gray30",
                                hover_color="#3a7049" if i == current_idx else "#555",
                                command=lambda i=i: (win.destroy(), on_pick(i)))
            btn.pack(side="left", fill="x", expand=True, padx=(8, 0))
            rows.append((img_lbl, btn))
        pid = id(win)
        self._pic_pickers[pid] = (win, rows, [])
        win.protocol("WM_DELETE_WINDOW", lambda: (self._pic_pickers.pop(pid, None), win.destroy()))

        def work():
            o = self.collect()
            for i in range(8):
                try:
                    path, _ = core.find_icon(word, o, None, i)
                    path = str(path) if path and Path(path).exists() else None
                except Exception:
                    path = None
                self.q.put(("pic_thumb", pid, i, path))
        threading.Thread(target=work, daemon=True).start()

    def _fill_pic_thumb(self, pid, idx, path):
        from PIL import Image
        try:
            win, rows, thumbs = self._pic_pickers[pid]
            if not win.winfo_exists():
                self._pic_pickers.pop(pid, None)
                return
            img_lbl, _btn = rows[idx]
            if path:
                im = Image.open(path).convert("RGBA")
                im.thumbnail((56, 56), Image.LANCZOS)
                photo = ctk.CTkImage(light_image=im, dark_image=im, size=(56, 56))
                thumbs.append(photo)
                img_lbl.configure(image=photo, text="")
            else:
                img_lbl.configure(text="\u2014", text_color="gray")
        except Exception:
            pass


    def pdf_find_pictures(self):
        """Maker-style picture gallery for the selected card's word."""
        w = self._pdf_selected
        if not w:
            return messagebox.showinfo(APP_NAME, "Click a card in the document first.")
        win = ctk.CTkToplevel(self)
        win.title(f"Pictures for \u201c{w}\u201d")
        win.geometry("440x540")
        try:
            win.transient(self)
        except Exception:
            pass
        status = ctk.CTkLabel(win, text=f"Searching free libraries for \u201c{w}\u201d...",
                              text_color="gray", anchor="w")
        status.pack(fill="x", padx=12, pady=(10, 4))
        gal = ctk.CTkScrollableFrame(win, fg_color="transparent")
        gal.pack(fill="both", expand=True, padx=8, pady=4)
        thumbs = []
        self._pdf_gallery_gen += 1
        gen = self._pdf_gallery_gen

        def work():
            try:
                cands = core.search_icon_candidates(w, limit=16,
                                                    opensymbols_secret=self.get_opensymbols_secret())
            except core.OpenSymbolsTokenExpired:
                cands = []
                self.q.put(("opensymbols_expired", "pdf", gen))
            except Exception as e:
                log.exception("pdf gallery")
                cands = []
            try:
                self.ui_call(lambda: self._pdf_fill_gallery(win, gal, status, thumbs, w, gen, cands))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _pdf_fill_gallery(self, win, gal, status, thumbs, word, gen, cands):
        try:
            if gen != self._pdf_gallery_gen or not win.winfo_exists():
                return
        except Exception:
            return
        for child in gal.winfo_children():
            child.destroy()
        thumbs.clear()
        if not cands:
            ctk.CTkLabel(gal, text="No pictures found. Try another word or use My image...",
                         text_color="gray").pack(pady=12)
            status.configure(text="No pictures found", text_color="gray")
            return
        status.configure(text=f"{len(cands)} picture(s) for \u201c{word}\u201d - click Use", text_color="#6bcb77")
        win._thumb_refs = thumbs  # keep PhotoImages alive as long as the popup
        from PIL import Image, ImageTk
        for item in cands:
            path = item.get("path")
            row = ctk.CTkFrame(gal, fg_color=("gray90", "gray25"), corner_radius=8)
            row.pack(fill="x", pady=4, padx=2)
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", padx=6, pady=6)
            try:
                if path and Path(path).exists():
                    im = Image.open(path).convert("RGBA")
                    im.thumbnail((64, 64))
                    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                    bg = Image.alpha_composite(bg, im).convert("RGB")
                    photo = ImageTk.PhotoImage(bg)
                    thumbs.append(photo)
                    tk.Label(inner, image=photo, bg="#2b2b2b").pack(side="left", padx=(0, 8))
            except Exception:
                pass
            txt = ctk.CTkFrame(inner, fg_color="transparent")
            txt.pack(side="left", fill="x", expand=True)
            label = item.get("label") or (Path(path).name if path else "?")
            ctk.CTkLabel(txt, text=label, anchor="w",
                         font=ctk.CTkFont(size=12, weight="bold")).pack(fill="x")
            ctk.CTkLabel(txt, text=item.get("source") or "", anchor="w",
                         text_color="gray", font=ctk.CTkFont(size=11)).pack(fill="x")
            ctk.CTkButton(inner, text="Use", width=56, height=30,
                          command=lambda p=str(path), ww=word, wn=win: self.pdf_use_picture(ww, p, wn)).pack(side="right")

    def pdf_use_picture(self, word, path, win):
        if not word or not path:
            return
        self.overrides[word] = path
        try:
            win.destroy()
        except Exception:
            pass
        self.draw_pdf_preview()
        self.flash_status(f"Picture applied to \u201c{word}\u201d")
    def pdf_choose_image(self):
        w = self._pdf_selected
        if not w:
            return
        p = filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp")])
        if p:
            self.overrides[w] = self._save_upload_to_library(w, p)
            self.flash_status(f"Custom picture set for \u201c{w}\u201d and saved in your Library")
            self.draw_pdf_preview()

    def export_pdf_studio(self):
        words = self._pdf_studio_words()
        if not words:
            return messagebox.showinfo(APP_NAME, "Type at least one word first.")
        out = self._ask_pdf("aac_picture_cards")
        if not out:
            return
        self.pdf_layout = self.pdf_layout_var.get()
        # snapshot the Words-tab picture choices so a mid-export change can't corrupt the run
        ov, var, sa = dict(self.overrides), dict(self.variants), dict(self.search_as)
        hide_labels = set(self.pdf_no_label)
        self._pdf_task("Creating PDF cards…", out, lambda: aac_boards.export_pecs_cards_pdf(
            words, Path(out), layout_name=self.pdf_layout, use_category_colors=self.pdf_colors_var.get(),
            tile_colors=dict(self.pdf_tile_colors), paper=self.pdf_paper.get(),
            show_labels=self.pdf_show_words.get(), show_pictures=self.pdf_show_pics.get(),
            word_scale=float(self.pdf_word_scale.get()) if hasattr(self, "pdf_word_scale") else 1.0,
            pic_scale=float(self.pdf_pic_scale.get()) if hasattr(self, "pdf_pic_scale") else 1.0,
            pic_y=float(self.pdf_pic_y.get()) if hasattr(self, "pdf_pic_y") else 0.35,
            pic_scale_map=dict(getattr(self, "pdf_pic_scale_map", {}) or {}),
            pic_y_map=dict(getattr(self, "pdf_pic_y_map", {}) or {}),
            word_y=float(self.pdf_word_y.get()) if hasattr(self, "pdf_word_y") else 0.12,
            cut_guides=self.pdf_cut_guides.get(),
            overrides=ov, variants=var, search_as=sa, hide_labels_for=hide_labels))

    # Board Studio / Strip 3D removed in v13.13-exp

    # ============================================================ widgets
    def header(self, parent, text, first=False):
        ctk.CTkLabel(parent, text=text.upper(), font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=ACCENT, anchor="w").pack(fill="x", pady=(0 if first else 16, 3))

    def menu(self, parent, text, key, values):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", pady=3)
        ctk.CTkLabel(f, text=text, anchor="w").pack(side="left")
        ctk.CTkOptionMenu(f, values=values, variable=self.vars[key], width=170).pack(side="right")

    def switch(self, parent, text, key):
        ctk.CTkSwitch(parent, text=text, variable=self.vars[key]).pack(anchor="w", pady=4)

    def slider(self, parent, text, key, lo, hi, step, unit=""):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", pady=(4, 0))
        top = ctk.CTkFrame(f, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text=text, anchor="w").pack(side="left")
        val = ctk.CTkLabel(top, text="", anchor="e")
        val.pack(side="right")
        var = self.vars[key]

        def upd(*_):
            try:
                val.configure(text=f"{var.get():g}{unit}")
            except Exception:
                pass
        var.trace_add("write", upd)
        upd()
        ctk.CTkSlider(f, from_=lo, to=hi, number_of_steps=int(round((hi - lo) / step)), variable=var).pack(fill="x")

    # ============================================================ status / background work
    def flash_status(self, text, ok=True, hold=False):
        self.status.configure(text=text, text_color="#6bcb77" if ok else "#e07a5f")
        if self._flash_after:
            self.after_cancel(self._flash_after)
            self._flash_after = None
        if not hold:
            self._flash_after = self.after(4000, lambda: self.status.configure(text="Ready", text_color="gray"))

    def busy_on(self, text, determinate=False):
        self.busy = True
        self.export_btn.configure(state="disabled")
        self.status.configure(text=text, text_color=ACCENT)
        self.progress.grid()
        if determinate:
            self.progress.configure(mode="determinate")
            self.progress.set(0)
        else:
            self.progress.configure(mode="indeterminate")
            self.progress.start()

    def busy_off(self):
        self.busy = False
        self.export_btn.configure(state="normal")
        try:
            self.progress.stop()
        except Exception:
            pass
        self.progress.grid_remove()

    def bg(self, label, fn, on_done=None, lock=False, determinate=False, kind="task"):
        """Run fn() in a thread; on_done(result) runs back on the main thread."""
        if lock:
            if self.busy:
                self.flash_status("Still working on the last task — one moment…", ok=False)
                return
            self.busy_on(label, determinate)
        else:
            self.status.configure(text=label, text_color=ACCENT)

        def work():
            try:
                res = fn()
                self.q.put(("bg_done", on_done, res, None, lock, kind))
            except ai.AIError as e:
                log.warning("AI task '%s' failed: %s", label, e)
                self.q.put(("bg_done", on_done, None, str(e), lock, kind))
            except Exception as e:
                log.exception("Task '%s' failed", label)
                self.q.put(("bg_done", on_done, None, f"{type(e).__name__}: {e}", lock, kind))
        threading.Thread(target=work, daemon=True).start()

    def progress_cb(self):
        return lambda f, t: self.q.put(("progress", f, t))

    # ============================================================ settings / dialogs
    def open_settings(self):
        """App settings: appearance, PDF Studio, exports, offline tools, 3D printing."""
        w = ctk.CTkToplevel(self)
        w.title("Settings")
        w.geometry("460x560")
        try:
            w.transient(self)
            x = self.winfo_rootx() + (self.winfo_width() - 460) // 2
            y = self.winfo_rooty() + (self.winfo_height() - 560) // 2
            w.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        except Exception:
            pass
        w.after(200, w.lift)
        sc = ctk.CTkScrollableFrame(w, fg_color="transparent")
        sc.pack(fill="both", expand=True, padx=6, pady=6)

        def section(title):
            ctk.CTkLabel(sc, text=title, anchor="w",
                         font=ctk.CTkFont(weight="bold", size=13)).pack(fill="x", padx=12, pady=(12, 4))

        def note(text):
            ctk.CTkLabel(sc, text=text, anchor="w", text_color="gray",
                         font=ctk.CTkFont(size=11), wraplength=400, justify="left").pack(fill="x", padx=12, pady=(0, 4))

        section("Appearance")
        sw = ctk.CTkSwitch(sc, text="Light interface", command=lambda: self.set_light(sw.get()))
        sw.pack(anchor="w", padx=12, pady=2)
        if ctk.get_appearance_mode() == "Light":
            sw.select()

        section("PDF Studio")
        prow = ctk.CTkFrame(sc, fg_color="transparent")
        prow.pack(fill="x", padx=12, pady=2)
        ctk.CTkLabel(prow, text="Paper size", width=120, anchor="w").pack(side="left")
        ctk.CTkOptionMenu(prow, values=["letter", "A4"], variable=self.pdf_paper, width=140).pack(side="left")
        ctk.CTkCheckBox(sc, text="Dashed cut guides", variable=self.pdf_cut_guides).pack(anchor="w", padx=12, pady=4)
        note("Paper and cut guides apply the next time you save a PDF.")
        note("Printable PDFs live in PDF Studio — that's the one place for cards now.")

        section("Offline")
        note("Pictures save themselves now: every new word or picture the app "
             "sees is stored in your icons folder automatically, so searches "
             "keep working without internet.")
        ctk.CTkButton(sc, text="Build offline AAC pack\u2026", fg_color="gray30", hover_color="#555",
                      command=self.build_offline_pack).pack(fill="x", padx=12, pady=2)

        section("Advanced")
        ctk.CTkLabel(sc, text="Auto-save exports", anchor="w",
                     font=ctk.CTkFont(weight="bold", size=12)).pack(fill="x", padx=12)
        ctk.CTkSwitch(sc, text="Auto-save — skip the Save dialog",
                      variable=self.auto_export).pack(anchor="w", padx=12, pady=2)
        ctk.CTkLabel(sc, text="When on, each studio saves straight into its own folder below — "
                               "PDF Studio, Maker Studio, and PDF Studio files stay separate. "
                               "When off, the app asks where to save, like before.",
                     text_color="gray", font=ctk.CTkFont(size=11), wraplength=400,
                     anchor="w", justify="left").pack(fill="x", padx=12)

        def dir_row(label, var):
            r = ctk.CTkFrame(sc, fg_color="transparent")
            r.pack(fill="x", padx=12, pady=2)
            ctk.CTkLabel(r, text=label, width=110, anchor="w").pack(side="left")
            ent = ctk.CTkEntry(r, textvariable=var, placeholder_text="folder for these exports")
            ent.pack(side="left", fill="x", expand=True)

            def choose(var=var):
                d = filedialog.askdirectory(title=f"Folder for {label} exports", parent=w)
                if d:
                    var.set(d)
            ctk.CTkButton(r, text="Choose…", width=76, fg_color="gray30", hover_color="#555",
                          command=choose).pack(side="left", padx=(6, 0))

        dir_row("PDF Studio", self.export_dir_pdf)
        dir_row("Maker Studio", self.export_dir_maker)
        ctk.CTkLabel(sc, text="More pictures: OpenSymbols", anchor="w",
                     font=ctk.CTkFont(weight="bold", size=12)).pack(fill="x", padx=12, pady=(10, 0))
        ctk.CTkLabel(sc, text="Unlocks Mulberry, Sclera, Tawasol + more in every picture search. "
                               "Paste your secret, or a temporary token, from opensymbols.org \u2192 API page. "
                               "Tokens expire — the app will say so and link you to a fresh one. "
                               "Kept in your system's secure credential store.",
                     text_color="gray", font=ctk.CTkFont(size=11), wraplength=400,
                     anchor="w", justify="left").pack(fill="x", padx=12)
        _osrow2 = ctk.CTkFrame(sc, fg_color="transparent")
        _osrow2.pack(fill="x", padx=12, pady=(4, 0))
        self.os_secret_entry = ctk.CTkEntry(_osrow2, show="\u2022", placeholder_text="paste secret or token here")
        self.os_secret_entry.pack(side="left", fill="x", expand=True)
        self.os_secret_entry.insert(0, self.get_opensymbols_secret())
        ctk.CTkButton(_osrow2, text="Save", width=70, command=self.save_opensymbols_secret).pack(side="left", padx=(6, 0))

        section("3D printing  (advanced)")
        note("Only matters for 3D tag exports from Maker Studio.")
        ctk.CTkLabel(sc, text="Export file format", anchor="w").pack(fill="x", padx=12)
        ctk.CTkOptionMenu(sc, values=BATCH_FORMATS, variable=self.batch_fmt, width=380).pack(fill="x", padx=12, pady=(2, 4))
        ctk.CTkLabel(sc, text="That's the only choice now — Export saves the word on the tag in this format.",
                     text_color="gray", anchor="w", font=ctk.CTkFont(size=11)).pack(fill="x", padx=12, pady=(0, 4))
        # v15.52: printer bed size — the Maker preview arranges the tags
        # inside this bed and flags any tag that doesn't fit, so you know
        # before the slicer does. Preview only; exports are unchanged.
        ctk.CTkLabel(sc, text="Printer bed size (Maker preview)", anchor="w").pack(fill="x", padx=12, pady=(6, 0))
        ctk.CTkOptionMenu(sc, values=BED_SIZES, variable=self.bed_size, width=380,
                          command=lambda _: (self.save(), self._preview3d_soon())).pack(fill="x", padx=12, pady=(2, 4))
        ctk.CTkLabel(sc, text="Tags arrange themselves inside this bed in the preview; a tag that "
                              "doesn't fit gets a red frame and a note under the preview page. "
                              "Moving tags around stays a slicer job.",
                     text_color="gray", anchor="w", font=ctk.CTkFont(size=11), wraplength=400,
                     justify="left").pack(fill="x", padx=12, pady=(0, 4))

        section("Data")
        ctk.CTkButton(sc, text="Open data folder (icons, logs, settings)", fg_color="gray30", hover_color="#555",
                      command=lambda: open_folder(DATA_DIR)).pack(fill="x", padx=12, pady=2)

        ctk.CTkButton(w, text="Done", fg_color="#4ECDC4", hover_color="#6EDDD6",
                      command=lambda: (self.save(), w.destroy())).pack(fill="x", padx=18, pady=(8, 12))
    def set_light(self, on):
        ctk.set_appearance_mode("light" if on else "dark")
        self.after(150, self.draw)

    def _my_list_names(self):
        return sorted(self.word_lists.keys()) or ["(no saved lists)"]

    def refresh_my_lists(self):
        names = self._my_list_names()
        self.my_lists_menu.configure(values=names)
        if self.my_lists_var.get() not in names:
            self.my_lists_var.set(names[0])

    def save_word_list(self):
        words = self.get_words()
        if not words:
            return messagebox.showinfo(APP_NAME, "Add some words first.")
        import tkinter.simpledialog as sd
        name = sd.askstring(APP_NAME, "Name this list:", parent=self)
        if not name:
            return
        name = name.strip()
        self.word_lists[name] = words
        self.refresh_my_lists()
        self.my_lists_var.set(name)
        self.save()
        self.flash_status(f"Saved list \u201c{name}\u201d")
        try:
            if self.tabs.get() == "Word Lists":
                self.draw_word_lists_preview()
        except Exception:
            pass

    def load_word_list(self, name):
        if name in self.word_lists:
            self.set_words(self._saved_list_words(name))
            self.flash_status(f"Loaded list \u201c{name}\u201d")

    def delete_word_list(self):
        name = self.my_lists_var.get()
        if name in self.word_lists:
            del self.word_lists[name]
            self.refresh_my_lists()
            self.save()
            self.flash_status(f"Deleted list \u201c{name}\u201d")
            try:
                if self.tabs.get() == "Word Lists":
                    self.draw_word_lists_preview()
            except Exception:
                pass

    def load_words_from_file(self):
        p = filedialog.askopenfilename(filetypes=[("Text", "*.txt"), ("All files", "*.*")])
        if not p:
            return
        try:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return messagebox.showerror(APP_NAME, f"Could not read file:\n{e}")
        words = [w.strip() for w in text.replace(",", "\n").split("\n") if w.strip()]
        if words:
            self.set_words(words)
            self.flash_status(f"Loaded {len(words)} words from {Path(p).name}")


    # ============================================================ words / pictures
    def get_words(self):
        seen, out = set(), []
        for w in getattr(self, "_words_model", []):
            w = str(w).strip()
            if w and w.lower() not in seen:
                seen.add(w.lower())
                out.append(w)
        return out

    def set_words(self, words):
        old = {w.lower() for w in getattr(self, "_words_model", [])}
        self._words_model = [str(w).strip() for w in words if str(w).strip()]
        self._render_word_tiles()
        self.words_changed()
        # v13.27: pictures save themselves — a genuinely new word gets its
        # picture fetched + stored for offline use in the background.
        new_words = [w for w in self._words_model if w.lower() not in old]
        if new_words:
            # Best-effort category for each new word (table + heuristic)
            for w in new_words:
                try:
                    aac_boards.assign_category(w)
                except Exception:
                    pass
            self._queue_auto_icons(new_words)

    # ---- v13.27: automatic picture saving (replaces the old manual
    # "Download icons for offline use" button in Settings)
    def _queue_auto_icons(self, words):
        q = getattr(self, "_auto_icon_queue", None)
        if q is None:
            q = self._auto_icon_queue = []
        have = {w.lower() for w in q}
        for w in words:
            if w.lower() not in have:
                q.append(w)
                have.add(w.lower())
        if getattr(self, "_auto_icon_running", False) or not q:
            return
        try:
            o, ov, var, sa, _ = self._export_args()
        except Exception:
            return  # app still starting up — words will save on next add
        self._auto_icon_running = True
        batch = list(q)
        q.clear()

        def work():
            try:
                # Local only — never hit the internet just because a word was typed.
                # Online search only when the user clicks Find pictures… or sets
                # Icon source to "Auto (local, then online)" and exports.
                from dataclasses import replace as _r
                o_local = _r(o, icon_source=core.SOURCES[1])
                rep = core.prefetch_icons(batch, o_local, ov, var, sa)
                log.info("Local picture check: %d found on disk, %d not found",
                         len(rep.get("ok", [])), len(rep.get("failed", [])))
                # v14.8: a word with NO picture on this computer is looked up online
                # (ARASAAC) automatically - but only when Icon source is "Auto", and never
                # for words that already have a picture, a custom image, or that were sent
                # from the Library without one. Words ARASAAC has no match for are not
                # asked again this session.
                if o.icon_source == core.SOURCES[0]:
                    miss = getattr(self, "_online_miss", None)
                    if miss is None:
                        miss = self._online_miss = set()
                    skip = set(getattr(self, "_no_auto_pic", set()))
                    todo = [w for w, _i in rep.get("failed", [])
                            if w.lower() not in miss and w not in skip and not ov.get(w)]
                    got, net_fail = [], 0
                    for w in todo[:150]:
                        orep = core.prefetch_icons([w], o, ov, var, sa)
                        if orep.get("ok"):
                            got.append(w)
                            net_fail = 0
                        elif orep.get("failed") and "no matches" in str(orep["failed"][0][1]):
                            miss.add(w.lower())
                            net_fail = 0
                        else:
                            net_fail += 1
                            if net_fail >= 3:      # offline / blocked: stop, don't wait on every word
                                log.info("Online picture lookup paused: connection problem")
                                break
                    if todo:
                        log.info("Online picture lookup: %d of %d word(s) with no local picture found one",
                                 len(got), len(todo[:150]))
                    if got:
                        self.ui_call(self._after_online_icons, got)
            except Exception as e:
                log.debug("auto icon save failed: %s", e)
            finally:
                self._auto_icon_running = False
                rest = getattr(self, "_auto_icon_queue", None)
                if rest:
                    nxt = list(rest)
                    rest.clear()
                    try:
                        self.after(300, lambda: self._queue_auto_icons(nxt))
                    except Exception:
                        pass
        threading.Thread(target=work, daemon=True).start()

    def _after_online_icons(self, words):
        """Pictures just arrived from the online lookup for words that had none (v14.8)."""
        try:
            self.flash_status(f"Found {len(words)} picture(s) online: " + ", ".join(words[:4])
                              + ("…" if len(words) > 4 else ""))
            self._lib_refresh_if_open()
            if self.tabs.get() == "PDF Studio":
                self.draw_pdf_preview()
            else:
                self.draw()
        except Exception:
            log.debug("_after_online_icons failed", exc_info=True)

    # ---- v13.20: word tiles (Word Lists tab) ----
    def _render_word_tiles(self):
        self._defer_once("word_tiles", self._render_word_tiles_now)

    def _render_word_tiles_now(self):
        """Rebuild the word tiles from the current word list."""
        frame = getattr(self, "_tiles_inner", None)
        if frame is None:
            return
        for child in frame.winfo_children():
            child.destroy()
        self._word_tile_btns = {}
        words = self.get_words()
        if not words:
            ctk.CTkLabel(frame, text="No words yet \u2014 type a word above and press Enter.",
                         text_color="gray", font=ctk.CTkFont(size=11), wraplength=300,
                         justify="left", anchor="w").grid(row=0, column=0, sticky="w", padx=2, pady=4)
            return
        for c in range(3):
            frame.grid_columnconfigure(c, weight=1, uniform="wtile")
        for i, w in enumerate(words):
            r, c = divmod(i, 3)
            b = ctk.CTkButton(frame, text=w, height=36, corner_radius=8,
                              command=lambda word=w: self._select_word_tile(word))
            b.grid(row=r, column=c, sticky="ew", padx=2, pady=2)
            b.bind("<Button-3>", lambda e, word=w: self._word_tile_menu(e, word))
            b.bind("<Button-2>", lambda e, word=w: self._word_tile_menu(e, word))
            self._word_tile_btns[w] = b
        self._style_word_tiles()

    def _style_word_tiles(self):
        """Selected word's tile stays lit purple; the rest get a hover glow."""
        sel = self.preview_word.get()
        for w, b in getattr(self, "_word_tile_btns", {}).items():
            try:
                if w == sel:
                    b.configure(fg_color="#9B59B6", hover_color="#B07CC6", text_color="white",
                                font=ctk.CTkFont(size=12, weight="bold"))
                else:
                    b.configure(fg_color=("gray90", "gray25"), hover_color=("#ccd6e4", "#3d4b63"),
                                text_color=("gray10", "#DCE4EE"), font=ctk.CTkFont(size=12))
            except Exception:
                pass

    def _select_word_tile(self, word):
        """Click a tile = select that word (same as picking it in the dropdown)."""
        for w in self.get_words():
            if w.lower() == word.lower():
                self.preview_word.set(w)
                self._preview_word_explicit = True
                try:
                    self.word_menu.set(w)
                except Exception:
                    pass
                self.on_preview_word()
                self._style_word_tiles()
                break

    def _word_tile_menu(self, event, word):
        """Right-click a tile: where to send this word + remove."""
        self._select_word_tile(word)
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="\u279c  Send word to 3D tag (Maker Studio)",
                      command=lambda: self._tile_send_3d(word))
        m.add_command(label="Send word to PDF Studio",
                      command=lambda: self._tile_send_pdf(word))
        self._add_generate_entry(m, [word])                             # v13.23
        m.add_separator()
        m.add_command(label="Remove from list", command=lambda: self._remove_word(word))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()
        return "break"

    def _tile_send_3d(self, word):
        got, _how = self._lib_send_3d(word, None, mode="auto")
        self.flash_status(f"\u2714 Sent word \u201c{got or word}\u201d to the 3D tag (Maker Studio)")

    def _tile_send_pdf(self, word):
        got, _how = self._lib_send_pdf(word, None, mode="auto")
        self.flash_status(f"\u2714 Sent word \u201c{word}\u201d to PDF Studio")

    def _remove_word(self, word):
        self.set_words([w for w in self.get_words() if w.lower() != word.lower()])
        self.flash_status(f"Removed \u201c{word}\u201d from the list")

    def words_changed(self):
        ws = self.get_words()
        # v13.32: top-bar menu shows all available words (not only current list)
        try:
            all_w = self._all_words_for_menu()
            cur = self.preview_word.get()
            if cur and cur != "-" and cur not in all_w:
                all_w = [cur] + all_w
            self.word_menu.configure(values=all_w or ["-"])
            if cur and cur != "-":
                self.word_menu.set(cur)
            elif all_w:
                self.preview_word.set(all_w[0])
                self._preview_word_explicit = False
            else:
                self.preview_word.set("-")
                self._preview_word_explicit = False
        except Exception:
            self.word_menu.configure(values=ws or ["-"])
            if self.preview_word.get() not in ws:
                self.preview_word.set(ws[0] if ws else "-")
                self._preview_word_explicit = False
        try:
            self._style_word_tiles()
        except Exception:
            pass
        self.on_preview_word()
        try:
            self._maker_sync_words()
        except Exception:
            pass
        try:
            if self._lib_open:
                if getattr(self, "_lib_live_after", None):
                    self.after_cancel(self._lib_live_after)
                self._lib_live_after = self.after(500, lambda: self._lib_reload(keep=True))
        except Exception:
            pass
        try:
            if self.tabs.get() == "Word Lists":
                self.draw_word_lists_preview()
        except Exception:
            pass

    def on_preview_word(self):
        w = self.preview_word.get()
        self.sel_label.set(f"“{w}”" if w and w != "-" else "")
        self.search_as_var.set(self.search_as.get(w, ""))
        self._show_word_now(w)
        self.changed()

    # ---- v13.14: the word appears at once; loading is shown on top of it
    def _tag_tab_active(self):
        try:
            return self.tabs.get() not in ("PDF Studio", "Word Lists", AI_TAB)
        except Exception:
            return False

    def _show_word_now(self, w):
        """Paint the new word immediately (before any picture lookup / local-AI work)
        and flag the preview as loading. Slider tweaks on the same word keep the old
        tag on screen instead of flashing."""
        if not w or w == "-" or not self._tag_tab_active():
            return
        if self.geo and self.geo[2] == w:
            return
        self.geo = None
        self._loading = {"word": w, "gen": -1, "msg": "Loading…", "t0": time.time(), "now": True, "ang": 0}
        self.info.configure(text=f"Loading “{w}”…")
        self.draw()
        if not self._load_after:
            self._load_tick()

    def _begin_loading(self, word, gen, msg, immediate):
        self._loading = {"word": word, "gen": gen, "msg": msg, "t0": time.time(),
                         "now": immediate, "ang": 0}
        self.info.configure(text=msg)
        if not self._load_after:
            self._load_tick()

    def _end_loading(self, gen=None):
        ld = self._loading
        if ld and (gen is None or ld["gen"] in (gen, -1)):
            self._loading = None

    def _load_tick(self):
        """Spinner watchdog while a tag is being built (v15.43).

        The tick used to draw() on every 70ms heartbeat. Since 15.39
        made Maker/Tag Design headless, that draw painted nothing — the
        loading pill lives on the canvas, which is not painted there —
        while scheduling a native refresh + live push each time. With a
        build that never delivered ('afraid': no local picture, lookup
        hung or stranded), the app exported nonstop: the log showed a
        native refresh every ~5s and, with the live page open, a live
        push every ~0.65s, for minutes. The tick no longer draws at
        all, and a build stuck over 60s is cleared (a late completion
        still displays via the preview consumer, which never needed
        _loading to be set).
        """
        ld = self._loading
        if not ld:
            self._load_after = None
            return
        if time.time() - ld.get("t0", 0) > 60:
            log.info("Loading timed out for %r — spinner stopped", ld.get("word"))
            self._loading = None
            self._load_after = None
            return
        ld["ang"] = (ld["ang"] - 28) % 360
        self._load_after = self.after(70, self._load_tick)

    def _draw_loading_overlay(self, c, w, h, dark):
        ld = self._loading
        if not ld:
            return
        if not ld.get("now") and time.time() - ld["t0"] < 0.5:
            return  # quick rebuilds (slider drags) don't flash a banner
        msg = ld.get("msg") or "Loading…"
        col = AI_COLOR if "AI" in msg else "#2f6fbd"
        t = c.create_text(w / 2 + 16, 34, text=msg, fill="#ffffff", font=("Segoe UI", 11, "bold"))
        x0, y0, x1, y1 = c.bbox(t)
        r = c.create_rectangle(x0 - 44, y0 - 9, x1 + 16, y1 + 9, fill=col, outline="")
        c.tag_lower(r, t)
        sx, sy = x0 - 22, (y0 + y1) / 2
        for off in (0, 180):
            c.create_arc(sx - 9, sy - 9, sx + 9, sy + 9, start=ld["ang"] + off, extent=110,
                         style="arc", outline="#ffffff", width=3)

    def load_list(self, name):
        self.list_var.set("Load a ready-made list…")
        words = core.COMMON_WORDS if name == "Common AAC words" else aac_boards.TOPIC_PACKS.get(name, [])
        if words:
            self.set_words(list(words))
            self.flash_status(f"Loaded “{name}” ({len(words)} words)")

    def load_preset_list(self, name):
        """Load a built-in preset list (v13.10-exp). Synonym groups stay
        together, separated by a blank line so the grouping is visible."""
        groups = core.preset_groups(name)
        if not groups:
            return
        self.preset_var.set("Preset lists — grouped by synonyms…")
        # v13.20: tiles — synonym groups stay together in the tile flow
        self.set_words([w for g in groups for w in g])
        self.flash_status(f"Loaded preset “{name}” — synonyms grouped together")

    def apply_size_preset(self, name=None):
        val = aac_boards.SIZE_PRESETS.get(name or self.size_preset.get())
        if val is not None:
            self.vars["size"].set(val)
        # Mark design preset as custom when user changes size manually via this menu
        if hasattr(self, "design_preset"):
            self.design_preset.set("Custom…")

    def apply_design_preset(self, name=None):
        """Apply a one-click design preset (shape, hole type, style, etc.)."""
        name = name or self.design_preset.get()
        preset = DESIGN_PRESETS.get(name) or {}
        if not preset:
            return
        for k, v in preset.items():
            if k.startswith("_"):
                continue
            if k in self.vars:
                try:
                    self.vars[k].set(v)
                except Exception:
                    pass
        sp = preset.get("_size_preset")
        if sp and sp in aac_boards.SIZE_PRESETS:
            self.size_preset.set(sp)
            self.vars["size"].set(aac_boards.SIZE_PRESETS[sp])
        self.flash_status(f"Design preset “{name}” applied")

    def _toggle_advanced_design(self):
        """Show or hide the Advanced options frame in Tag Design."""
        if self.advanced_design.get():
            self.adv_frame.pack(fill="x", pady=(4, 0))
        else:
            self.adv_frame.pack_forget()

    def pick(self, key, btn):
        c = colorchooser.askcolor(color=self.vars[key].get())[1]
        if c:
            self.vars[key].set(c)
            btn.configure(fg_color=c)


    def collect(self):
        d = {k: v.get() for k, v in self.vars.items()}
        # Normalize hole values so old saves / partial matches always work
        h = str(d.get("hole") or "Top loop")
        if h in ("Top", "top"):
            h = "Top loop"
        elif h in ("Left", "left"):
            h = "Left loop"
        elif h.lower().startswith("through"):
            h = "Through (top)"
        elif h not in core.HOLES:
            h = "Top loop"
        d["hole"] = h
        # Keep the UI variable in sync if we remapped
        if self.vars.get("hole") and self.vars["hole"].get() != h:
            try:
                self.vars["hole"].set(h)
            except Exception:
                pass
        return core.Opts(**d)

    def bump(self, d):
        w = self.preview_word.get()
        if w and w != "-":
            self.variants[w] = max(0, self.variants.get(w, 0) + d)
            self.flash_status(f"Picture choice #{self.variants[w] + 1}")
            self.changed()

    def open_main_picture_picker(self):
        w = self.preview_word.get()
        if not w or w == "-":
            return
        self.open_picture_picker(self.pic_pick_btn, w, self.variants.get(w, 0),
                                 self._main_pick_index)

    def _main_pick_index(self, i):
        w = self.preview_word.get()
        if w and w != "-":
            self.variants[w] = i
            self._no_auto_pic.discard(w)
            self.overrides.pop(w, None)
            self.flash_status(f"Picture {i + 1} for \u201c{w}\u201d")
            self.changed()


    # ============================================================ preview
    def colors_for(self, word, o):
        """(body, ink, detail ink) — the detail ink paints the picture's midtone
        layer on 3-colour tags (v15.8)."""
        ink2 = getattr(o, "ink2_color", "#8a8a8a")
        if o.high_contrast:
            return "#111111", "#FFFFFF", "#FFFFFF"
        if self.cat_colors.get():
            return aac_boards.category_color(word), o.ink_color, ink2
        return o.body_color, o.ink_color, ink2

    def changed(self):
        if getattr(self, "_opts_loading", False):
            return
        # v13.31: when a multi-tag is selected, design changes apply ONLY to that tag
        sel = getattr(self, "_selected_multi", None)
        multi_on = getattr(self, "maker_multi", None) is not None and self.maker_multi.get()
        if sel and multi_on:
            try:
                cur = asdict(self.collect())
                prev = getattr(self, "_opts_snapshot", None) or {}
                delta = {k: cur[k] for k in cur if prev.get(k) != cur[k]}
                if delta:
                    d = dict(self.design_overrides.get(sel) or {})
                    d.update(delta)
                    self.design_overrides[sel] = d
                    # invalidate ONLY that tag's cached geometry
                    if getattr(self, "_multi_geo", None) is not None:
                        self._multi_geo.pop(sel, None)
                    # force fingerprint change so missing rebuild kicks
                    self._multi_fp = None
                    self._opts_snapshot = cur
            except Exception:
                pass
            # multi mode: just redraw the grid (rebuilds the one missing tag)
            if self.pending:
                self.after_cancel(self.pending)
            self.pending = self.after(120, self._refresh_multi)
            return
        # no selection → global design change for every tag
        if multi_on:
            self._multi_fp = None
            if getattr(self, "_multi_geo", None) is not None:
                self._multi_geo = {}
            if self.pending:
                self.after_cancel(self.pending)
            self.pending = self.after(120, self._refresh_multi)
            return
        if self.pending:
            self.after_cancel(self.pending)
        self.pending = self.after(220, self.refresh)

    def _refresh_multi(self):
        """Redraw multi-tag preview and rebuild any missing geometry."""
        self.pending = None
        if not (getattr(self, "maker_multi", None) and self.maker_multi.get()):
            self.refresh()
            return
        self.draw()

    def refresh(self):
        """Rebuild the 3D tag preview. Same auto-icon behaviour as v13.14:
        always look up pictures (local then online) unless the word was sent
        from the Library as 'word only' and the user has not picked a picture."""
        self.pending = None
        tab = self.tabs.get()
        if tab in ("PDF Studio", "Word Lists", AI_TAB):
            self._tag_stale = True  # rebuilt when we come back to a tag tab
            return  # document view / word lists / AI: no tag rebuild needed
        # v13.31: multi mode uses its own rebuild path
        if tab in ("Maker Studio", "Tag Design") and getattr(self, "maker_multi", None) \
                and self.maker_multi.get():
            self.draw()
            return
        w = self.preview_word.get()
        if not w or w == "-":
            return
        self._tag_stale = False
        self.gen += 1
        gen, o = self.gen, self.collect()
        # v13.29: apply per-tag design overrides for this word if any
        if w in getattr(self, "design_overrides", {}):
            try:
                o = self._opts_for_word(w, o)
            except Exception:
                pass
        # Explicit user pick only (do not force a remembered path as override —
        # that blocked the normal auto lookup when the path was wrong/stale).
        ov = self.overrides.get(w)
        var, sa = self.variants.get(w, 0), self.search_as.get(w) or w
        word_changed = (not self.geo) or self.geo[2] != w
        c = ai.cfg
        # v15.31: previews never auto-draw. A preview rebuild happens on
        # every word/size/style change and at startup (Multiple tags on),
        # so an automatic draw there loaded the text + image models over
        # and over — the "tons of AI loading on startup". Local pictures
        # still resolve automatically; AI drawing stays available on
        # purpose (AI tab buttons, chat picture requests, deliberate
        # exports via the switch in AI Settings).
        no_auto = True
        if no_auto:
            msg = "Building tag…"
        elif o.layout == "Word only":
            msg = "Building tag…"
        elif c.ready and (c.pick_best or c.auto_draw):
            msg = "Finding a picture… (local AI may take a moment to load)"
        else:
            msg = "Finding a picture…"
        self._begin_loading(w, gen, msg, word_changed)

        def work():
            try:
                core.set_status_callback(lambda m: self.q.put(("load_msg", gen, m)))
                if word_changed:
                    # Stage 1 (instant): the word on the real tag shape, no picture lookup.
                    try:
                        quick = core.build_tag(w, dc_replace(o, layout="Word only"), None, 0)
                        self.q.put(("preview_quick", gen, quick, o, w))
                    except Exception as e0:
                        log.debug("quick tag skipped for '%s': %s", w, e0)
                # Stage 2: full tag with automatic picture lookup (same as v13.14)
                res = core.build_tag(w, o, ov, var, icon_word=sa, auto=not no_auto)
                # Remember local path so size/style changes can stay offline & fast
                try:
                    p, _ = core.find_icon(sa or w, dc_replace(o, icon_source=core.SOURCES[1]),
                                          ov, var)
                    if p and Path(p).exists():
                        self._icon_paths[w] = str(p)
                except Exception:
                    pass
                self.q.put(("preview", gen, res, o, var, w))
            except Exception as e:
                log.exception("Preview failed for '%s'", w)
                self.q.put(("error", gen, f"{type(e).__name__}: {e}"))
            finally:
                core.set_status_callback(None)
        threading.Thread(target=work, daemon=True).start()

    def update_badge(self):
        n = len(ISSUES.records)
        if n == getattr(self, "_badge_n", None):
            return  # v14.3: poll() calls this every 120 ms — don't reconfigure the buttons when nothing changed
        self._badge_n = n
        for b in (self.log_btn, getattr(self, "log_btn2", None),
                  getattr(self, "_ped_log_btn", None)):
            if b is None:
                continue
            if n:
                b.configure(text=f"⚠ {n}", fg_color="#b36b00")
            else:
                b.configure(text="Log", fg_color="gray30")

    def _poll_drain(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "call":
                    _fn, _args = msg[1], msg[2]
                    _fn(*_args)
                elif kind == "preview":
                    _, gen, res, o, var, w = msg
                    if gen == self.gen:
                        self.geo = (res, o, w)
                        self._end_loading(gen)
                        if self.tabs.get() != "PDF Studio":
                            self.info.configure(text=f"{res[3]}  ·  picture choice #{var + 1}")
                            if self._tag_tab_active():
                                self._start_preview_anim()
                        # v15.35: the live viewer follows the finished preview.
                        try:
                            self._live_push_soon(w)
                        except Exception:
                            pass
                elif kind == "preview_quick":
                    _, gen, res, o, w = msg
                    # only if the full tag hasn't already arrived for this build
                    if gen == self.gen and self._loading and self._loading["gen"] in (gen, -1):
                        self.geo = (res, o, w)
                        self._anim_scale = 1.0
                        if self._tag_tab_active():
                            self.draw()
                elif kind == "load_msg":
                    _, gen, text = msg
                    if gen == self.gen and self._loading and self._loading["gen"] in (gen, -1):
                        self._loading["msg"] = text
                        self._loading["now"] = True
                        self.info.configure(text=text)
                elif kind == "ai_banner":
                    _, _st, _kd, _title, _detail = msg
                    self._ai_banner(_st, _kd, _title, _detail)
                    if _st == "ok":
                        self._lib_refresh_if_open()
                elif kind == "lib_thumb":
                    self._lib_thumb_arrived(msg[1], msg[2])
                elif kind == "error":
                    if msg[1] == self.gen:
                        self._end_loading(msg[1])
                        self.info.configure(text=f"Error: {msg[2]}  (see Log)")
                elif kind == "progress":
                    if self.progress.cget("mode") == "determinate":
                        self.progress.set(msg[1])
                    self.status.configure(text=msg[2], text_color=ACCENT)
                elif kind == "bg_done":
                    _, on_done, res, err, lock, k = msg
                    if lock:
                        self.busy_off()
                    if err:
                        self.flash_status(err, ok=False, hold=True)
                        messagebox.showwarning(APP_NAME, err)
                    else:
                        self.status.configure(text="Ready", text_color="gray")
                        if on_done:
                            on_done(res)
                elif kind == "maker_prog":
                    if msg[1] == self._maker_gen:
                        self.maker_status.configure(text=msg[2], text_color=ACCENT)
                elif kind == "ai_refresh":
                    try:
                        self.refresh_ai_status()
                    except Exception:
                        pass
                    try:
                        self.refresh_ai_folder_label()
                    except Exception:
                        pass
                elif kind == "multi_geo":
                    _, mword, mres = msg
                    if getattr(self, "_multi_geo", None) is not None:
                        self._multi_geo[mword] = mres
                    try:
                        if self.tabs.get() in ("Maker Studio", "Tag Design") and self.maker_multi.get():
                            self.draw()
                            # v15.32: a stale single-word "Loading …" label
                            # (e.g. the restored word at startup) otherwise
                            # sits under the finished multi grid forever.
                            try:
                                n = len(self._maker_preview_words())
                            except Exception:
                                n = 0
                            if n:
                                self.info.configure(text=f"{n} tags")
                            # v15.36: multi builds move the live viewer too.
                            try:
                                self._live_push_soon(None)
                            except Exception:
                                pass
                    except Exception:
                        pass
                elif kind == "opensymbols_expired":
                    _, where, gen = msg
                    if where == "maker" and gen == self._maker_gen:
                        self._opensymbols_expired("maker")
                    elif where == "pdf" and gen == self._pdf_gallery_gen:
                        self._opensymbols_expired("pdf")
                elif kind == "pdf_conn":
                    _ok = msg[1]
                    try:
                        if _ok:
                            self.pdf_conn_dot.configure(text_color="#6bcb77")
                            self.pdf_conn_text.configure(
                                text="Online — pictures will download", text_color="#6bcb77")
                        else:
                            self.pdf_conn_dot.configure(text_color="#e06c5a")
                            self.pdf_conn_text.configure(
                                text="Offline — using saved pictures only", text_color="#e06c5a")
                    except Exception:
                        pass
                elif kind == "pic_thumb":
                    _, _pid, _idx, _path = msg
                    self._fill_pic_thumb(_pid, _idx, _path)
                elif kind == "maker_gallery":
                    _, gen, ww, cands = msg
                    if gen == self._maker_gen:  # ignore stale searches
                        self._fill_maker_gallery(ww, cands)
                elif kind == "diag":
                    if self.logbox is not None and self.logwin is not None and self.logwin.winfo_exists():
                        self.logbox.delete("1.0", "end")
                        self.logbox.insert("1.0", msg[1])
        except queue.Empty:
            pass

    def poll(self):
        """v14.8: the UI message loop can no longer die. Before, ONE background result whose
        handler raised (a closed window, a missing widget...) escaped this function BEFORE the
        next poll was scheduled - the app then stopped hearing from every background job:
        previews, progress bars and busy buttons froze for the rest of the session."""
        try:
            for _ in range(200):               # a bad message is skipped, the rest still run
                try:
                    self._poll_drain()
                    break
                except Exception:
                    log.exception("UI message handler failed (skipped)")
            try:
                self.update_badge()
            except Exception:
                log.debug("update_badge failed", exc_info=True)
        finally:
            self.after(120, self.poll)

    def ui_call(self, fn, *args):
        """Run fn(*args) on the UI thread. Safe from ANY thread (workers hand it to the
        message loop instead of touching Tk themselves - Tk calls from worker threads are
        a classic cause of random freezes and crashes)."""
        if threading.current_thread() is threading.main_thread():
            self.after(0, fn, *args)
        else:
            self.q.put(("call", fn, args))

    def _start_preview_anim(self):
        if self._anim_after:
            self.after_cancel(self._anim_after)
        self._anim_scale = 0.86
        self._anim_step()

    def _anim_step(self):
        if self.tabs.get() in ("PDF Studio", "Word Lists", AI_TAB):
            self._anim_after = None
            return
        self._anim_scale = min(1.0, self._anim_scale + 0.05)
        self.draw()
        self._anim_after = self.after(16, self._anim_step) if self._anim_scale < 1.0 else None

    def _maker_multi_toggled(self):
        if not self.maker_multi.get():
            self._select_multi_tag(None)
        else:
            # v13.32: lock the working set from the current list when multi
            # is turned on (or when the count changes). Later list flips
            # only change the tiles, not this set.
            # v14: seed ONLY when no working set exists yet. An existing
            # set is permanent — toggling the switch or changing the tag
            # count must never reseed it from the list; the count only
            # changes how many of the set are shown / exported.
            if getattr(self, "_maker_work_words", None) is None:
                try:
                    _seed = list(self._maker_words_for(self.maker_list_var.get()))
                except Exception:
                    _seed = []
                # v15.52: same no-empty-lock rule as _maker_preview_words.
                if _seed:
                    self._maker_work_words = _seed
                    self._multi_fp = None
                    if getattr(self, "_multi_geo", None) is not None:
                        self._multi_geo = {}
        self.save()
        self.draw()
        self._maker_refresh_preview_chips()

    def _select_multi_tag(self, word):
        """v13.29: click a tag in the multi preview → edit only that tag's design.
        Click empty space (word=None) returns to editing all tags together."""
        # Save the true global options the first time we dive into a per-tag edit
        if word and self._selected_multi is None:
            try:
                self._global_opts_backup = asdict(self.collect())
            except Exception:
                self._global_opts_backup = None
        self._selected_multi = word
        try:
            if word:
                self._multi_edit_label.configure(
                    text=f"Editing “{word}” only — shape/style change this tag · double-click a word tile to swap · click empty space to finish")
                # Load this word's effective options into the UI so the controls match what you see
                o = self._opts_for_word(word)
                self._load_opts_into_ui(o)
                self._maker_style_tiles()
            else:
                self._multi_edit_label.configure(text="")
                # Restore the global options that were active before per-tag editing
                backup = getattr(self, "_global_opts_backup", None)
                if backup:
                    try:
                        o = core.Opts(**{k: v for k, v in backup.items() if k in {f.name for f in fields(core.Opts)}})
                        self._load_opts_into_ui(o)
                    except Exception:
                        pass
                self._global_opts_backup = None
                try:
                    self._maker_style_tiles()
                except Exception:
                    pass
        except Exception:
            pass
        self.draw()
        self._maker_refresh_preview_chips()
        try:
            self._p3d_update_info_chip()
        except Exception:
            pass

    def _load_opts_into_ui(self, o):
        """Push an Opts instance into the Tag Design controls without firing changed() loops."""
        self._opts_loading = True
        try:
            for f in fields(core.Opts):
                if f.name in self.vars:
                    try:
                        self.vars[f.name].set(getattr(o, f.name))
                    except Exception:
                        pass
            # colour buttons
            if hasattr(self, "body_btn"):
                try:
                    self.body_btn.configure(fg_color=o.body_color)
                    self.ink_btn.configure(fg_color=o.ink_color)
                except Exception:
                    pass
        finally:
            self._opts_loading = False
            self._opts_snapshot = asdict(o)

    def _paint_tag(self, c, res, o, word, cx_px, cy_px, S, dark, bg, shadow=4.0):
        """Paint one tag — shadow, body, ink polygons — centred at
        (cx_px, cy_px) at scale S. This is the single-tag preview's exact
        rendering, shared with the multiple-tags view."""
        base, ink, body = res[0], res[1], res[2]
        if body is None or body.is_empty:
            return
        body_c, ink_c, ink2_c = self.colors_for(word, o)
        x0, y0, x1, y1 = body.bounds
        ccx, ccy = (x0 + x1) / 2, (y0 + y1) / 2

        def T(coords, dx=0.0, dy=0.0):
            out = []
            for x, y in coords:
                out += [cx_px + (x - ccx) * S + dx, cy_px - (y - ccy) * S + dy]
            return out

        def paint(geom, color, hole_color):
            for p in core.polys(geom):
                c.create_polygon(T(p.exterior.coords), fill=color, outline="")
                for r in p.interiors:
                    c.create_polygon(T(r.coords), fill=hole_color, outline="")

        for p in core.polys(body):
            c.create_polygon(T(p.exterior.coords, shadow, shadow),
                             fill="#121317" if dark else "#c5c8ce", outline="")
        paint(body, body_c, bg)
        # v15.14: ink polygons must be painted with their holes, largest
        # first.  Before, exteriors alone were painted (v15.8), so the border
        # ring — whose exterior is the whole tag disc — painted as a solid
        # disc in ink colour, hiding the body, the picture and the word
        # behind it and leaving only a category-coloured edge.  A polygon's
        # hole shows the body underneath, and a bigger polygon that contains
        # smaller ones in its hole (the ring contains the picture) goes
        # first, so the picture paints back on top of it afterwards.
        if isinstance(ink, (list, tuple)):
            layers = list(zip(ink, (ink_c, ink2_c)))
        else:
            layers = [(ink, ink_c)]
        flat = []
        for g, col in layers:
            for p in core.polys(g):
                bx0, by0, bx1, by1 = p.bounds
                flat.append(((bx1 - bx0) * (by1 - by0), p, col))
        flat.sort(key=lambda t: -t[0])
        def _warm(h):
            # v15.23 warm Maker look: ink leans toward warm brown (30%).
            # Preview only — render_png and the 3D exports paint elsewhere.
            try:
                h = h.lstrip("#")
                r, gg, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
                return "#%02x%02x%02x" % (int(r + (74 - r) * 0.3), int(gg + (52 - gg) * 0.3),
                                          int(b + (40 - b) * 0.3))
            except Exception:
                return h
        for _area, p, col in flat:
            c.create_polygon(T(p.exterior.coords), fill=_warm(col), outline="")
            for r in p.interiors:
                c.create_polygon(T(r.coords), fill=body_c, outline="")
        # v15.23: a thicker warm-brown rim around the painted tag.
        rim_w = max(3, int(round(S * 0.30)))
        for p in core.polys(body):
            c.create_polygon(T(p.exterior.coords), fill="", outline="#4A3428", width=rim_w)

    def _base_opts(self):
        """The design options that apply to tags that are NOT being edited alone.
        While a multi-tag is selected, UI controls show that tag's options — so
        we freeze the true global options in _global_opts_backup and use those
        for every other tag (v13.31)."""
        sel = getattr(self, "_selected_multi", None)
        backup = getattr(self, "_global_opts_backup", None)
        if sel and backup:
            try:
                names = {f.name for f in fields(core.Opts)}
                return core.Opts(**{k: v for k, v in backup.items() if k in names})
            except Exception:
                pass
        return self.collect()

    def _opts_for_word(self, word, base_o=None):
        """Global (or frozen global) options merged with per-tag overrides.
        The selected tag also picks up the live UI values so shape changes
        show immediately on only that tag (v13.31)."""
        if base_o is None:
            base_o = self._base_opts()
        o = base_o
        ov = dict(self.design_overrides.get(word) or {})
        # Live UI edits while this tag is selected
        if word and word == getattr(self, "_selected_multi", None):
            try:
                cur = asdict(self.collect())
                for k, v in cur.items():
                    if hasattr(o, k):
                        ov[k] = v
            except Exception:
                pass
        if ov:
            try:
                o = dc_replace(o, **{k: v for k, v in ov.items() if hasattr(o, k)})
            except Exception:
                pass
        return o

    def _draw_maker_tags(self):
        """Multiple-tags preview (v13.28 rework, Cody's spec; v13.29: count
        selector + click-to-edit one tag). Real tag shapes tiled side by side."""
        c = self.canvas
        dark = ctk.get_appearance_mode() == "Dark"
        bg = PREVIEW_BG_DARK if dark else PREVIEW_BG_LIGHT
        c.configure(bg=bg)
        c.delete("all")
        self._multi_hit = []
        W, H = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
        fg = "#e6e6e6" if dark else "#333333"
        # v13.32: use the locked working set so flipping the list dropdown
        # does not rebuild the preview tags the user is editing.
        try:
            words = self._maker_preview_words()
        except Exception:
            words = []
        # v13.29: respect "How many tags" selector
        cnt = getattr(self, "maker_count_var", None)
        if cnt is not None:
            v = cnt.get()
            if v and v != "All":
                try:
                    words = words[:max(1, int(v))]
                except Exception:
                    pass
        if not words:
            c.create_text(W / 2, H / 2, fill="gray", font=("Segoe UI", 14),
                          text="No tags in the preview yet — pick a word list and load it, or put words on tags.")
            return
        base_o = self._base_opts()
        # v15.23: the cache fingerprint is split in two. A tile click only
        # changes which tag is selected (or adds one word) — it must not
        # rebuild every tag's geometry. Only a real design change drops
        # cache entries, and then only the affected ones.
        try:
            import dataclasses
            sel = getattr(self, "_selected_multi", None)
            live = asdict(self.collect()) if sel else {}
            fp_design = repr((dataclasses.asdict(base_o), sorted(self.overrides.items()),
                              sorted(self.variants.items()), sorted(self.search_as.items()),
                              sorted((k, tuple(sorted(d.items()))) for k, d in self.design_overrides.items())))
            fp = repr((fp_design, sel, tuple(sorted(live.items())) if live else ()))
        except Exception:
            fp_design, fp = "", ""
        if getattr(self, "_multi_geo", None) is None:
            self._multi_geo = {}
        if getattr(self, "_multi_fp_design", None) != fp_design:
            # a real design change: the selected tag alone, or all tags
            self._multi_fp_design = fp_design
            if sel:
                self._multi_geo.pop(sel, None)
            else:
                self._multi_geo = {}
        elif getattr(self, "_multi_fp", None) != fp:
            # only the selection (or its live options) changed: the newly
            # selected tag's geometry may differ, the rest cannot
            if sel:
                self._multi_geo.pop(sel, None)
        self._multi_fp = fp
        cache = self._multi_geo
        missing = [w for w in words if w not in cache]
        if missing:
            self._multi_kick(missing, base_o)
        # The column count that gives every tag the most room
        n = len(words)
        best = None
        for cols in range(1, n + 1):
            rows = (n + cols - 1) // cols
            cell = min(W / cols, H / rows)
            if best is None or cell > best[0]:
                best = (cell, cols, rows)
        cell, cols, rows = best
        # v15.23: a small set no longer balloons to window size — tags stay
        # a comfortable size and the grid stays centred in its cells.
        cell = min(cell, 360.0)
        sel = getattr(self, "_selected_multi", None)
        for i, w in enumerate(words):
            r, col = divmod(i, cols)
            cxp = (col + 0.5) * (W / cols)
            cyp = (r + 0.5) * (H / rows)
            half = cell * 0.48
            self._multi_hit.append((w, cxp - half, cyp - half, cxp + half, cyp + half))
            v = cache.get(w)
            o = self._opts_for_word(w, base_o)
            if v is None or v is False:
                # Not built yet (or couldn't build): the word itself,
                # like the single-tag loading state
                size = max(12, min(30, int(cell / (len(w) + 3) * 0.9)))
                c.create_text(cxp, cyp, fill=fg if v is None else "gray",
                              text=w, font=("Segoe UI", size, "bold"))
                if sel == w:
                    c.create_rectangle(cxp - half, cyp - half, cxp + half, cyp + half,
                                       outline="#E8C85A", width=3)
                continue
            body = v[2]
            if body is None or body.is_empty:
                continue
            x0, y0, x1, y1 = body.bounds
            S = cell / (max(x1 - x0, y1 - y0) * 1.18)
            # v15.23 warm look: the edited tag glows soft yellow, with a
            # friendly label underneath instead of the pink rectangle.
            if sel == w:
                c.create_oval(cxp - half - 10, cyp - half - 10,
                              cxp + half + 10, cyp + half + 10,
                              fill="#F9E9A8", outline="")
            self._paint_tag(c, v, o, w, cxp, cyp, S, dark, bg,
                            shadow=max(1.5, S * 0.08))
            if sel == w:
                c.create_text(cxp, cyp + half + 4,
                              fill="#B8860B" if not dark else "#E8C85A",
                              text="You're editing this one.", font=("Segoe UI", 9, "bold"))
        # v15.36: every multi repaint reschedules the live push (debounced
        # in _live_push_soon, so a build storm pushes once at the end).
        # This is the hook the single-preview consumer cannot provide:
        # multi mode never runs the single preview path.
        try:
            self._live_push_soon(None)
        except Exception:
            pass

    def _multi_kick(self, missing, base_o):
        """Build the missing tags' geometry in the background, one at a
        time, redrawing as each lands (same build as the single preview).
        Uses per-word design overrides + live UI for the selected tag (v13.31)."""
        if getattr(self, "_multi_building", False):
            return
        self._multi_building = True
        ov, var, sa = dict(self.overrides), dict(self.variants), dict(self.search_as)
        no_auto_words = set(getattr(self, "_no_auto_pic", set()))
        # Snapshot per-word options on the GUI thread so the worker is pure
        per_opts = {}
        for w in missing:
            try:
                per_opts[w] = self._opts_for_word(w, base_o)
            except Exception:
                per_opts[w] = base_o

        def work():
            try:
                for w in missing:
                    # v15.31: previews never auto-draw (see single preview).
                    no_auto = True
                    o = per_opts.get(w, base_o)
                    try:
                        res = core.build_tag(w, o, ov.get(w), var.get(w, 0),
                                             icon_word=sa.get(w) or w, auto=not no_auto)
                    except Exception as e:
                        log.debug("multi tag build failed for '%s': %s", w, e)
                        res = False
                    # Deliver via the app queue (the poller owns the GUI);
                    # after() from a worker thread is not reliable.
                    self.q.put(("multi_geo", w, res))
            finally:
                self._multi_building = False

        threading.Thread(target=work, daemon=True).start()

    def draw(self):
        tab = self.tabs.get()
        if tab == "PDF Studio":
            return
        if tab in ("Maker Studio", "Tag Design"):
            # v15.39: the canvas is headless on these tabs — the native 3D
            # preview owns them now. NO canvas paint work happens here at
            # all (no delete, no polygons, no text); the live push and
            # the 3D refresh are scheduled instead, debounced. The 2D
            # build pipeline still runs (it triggers both), but its
            # painting is skipped. PDF Studio, Word Lists, and AI tabs
            # below are untouched.
            try:
                self._live_push_soon(None)
            except Exception:
                pass
            # v15.62: the embedded browser is gone, so the built-in 3D
            # render always runs (the old "skip when browser showing"
            # check below is permanently true).
            if getattr(self, "_embed_state", None) != "ready":
                try:
                    self._preview3d_soon()
                except Exception:
                    pass
            return
        # v13.29: multi-tag preview works in Maker Studio AND Tag Design
        if tab in ("Maker Studio", "Tag Design") and getattr(self, "maker_multi", None) is not None \
                and self.maker_multi.get():
            self._draw_maker_tags()
            return
        c = self.canvas
        dark = ctk.get_appearance_mode() == "Dark"
        bg = PREVIEW_BG_DARK if dark else PREVIEW_BG_LIGHT
        c.configure(bg=bg)
        c.delete("all")
        w, h = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
        if not self.geo:
            ld = self._loading
            if ld:
                # the word shows at once, even before the tag shape is built
                size = max(20, min(56, int(w / (len(ld["word"]) + 3) * 0.9)))
                c.create_text(w / 2, h / 2, fill="#e6e6e6" if dark else "#333", text=ld["word"],
                              font=("Segoe UI", size, "bold"))
                self._draw_loading_overlay(c, w, h, dark)
            else:
                c.create_text(w / 2, h / 2, fill="gray", font=("Segoe UI", 14), text="Type a word to preview")
            return
        (base, ink, body, _), o, word = self.geo
        if body.is_empty:
            return
        body_c, ink_c, ink2_c = self.colors_for(word, o)
        x0, y0, x1, y1 = body.bounds
        S = min(w, h) / (max(x1 - x0, y1 - y0) * 1.18) * self._anim_scale
        self._paint_tag(c, self.geo[0], o, word, w / 2, h / 2, S, dark, bg,
                        shadow=4 * self._anim_scale)
        # Dimensions line
        dim_txt = (f"{x1 - x0:.1f} × {y1 - y0:.1f} mm  ·  {o.thickness:g} mm thick  ·  "
                   f"depth {getattr(o, 'design_depth', 0):g} mm  ·  {o.shape}  ·  {o.style}")
        c.create_text(w / 2, h - 14, fill="#888" if dark else "#666", font=("Segoe UI", 10),
                      text=dim_txt)
        # Side-profile strip so thickness & design depth are visible at a glance
        try:
            th = float(getattr(o, "thickness", 2.0) or 2.0)
            depth = float(getattr(o, "design_depth", 0.8) or 0.8)
            raise_h = float(getattr(o, "raise_h", 0.4) or 0.4)
            style = (getattr(o, "style", "") or "").lower()
            # Scale: 1 mm → 10 px, keep under ~80 px tall
            px_per_mm = min(14.0, 70.0 / max(th + (raise_h if "raised" in style else 0), 1.0))
            bar_w = 90
            bar_x = w - bar_w - 18
            base_y = h - 40
            # Body block
            body_h = th * px_per_mm
            c.create_rectangle(bar_x, base_y - body_h, bar_x + bar_w, base_y,
                               fill=body_c, outline="#555")
            # Design-depth band (first layers) highlighted
            d_h = min(depth, th) * px_per_mm
            band_fill = ink_c if "raised" not in style else "#4a90d9"
            c.create_rectangle(bar_x, base_y - d_h, bar_x + bar_w, base_y,
                               fill=band_fill, outline="")
            c.create_rectangle(bar_x, base_y - body_h, bar_x + bar_w, base_y,
                               outline="#333" if not dark else "#aaa", width=1)
            # Raised layer above body when style is raised
            if "raised" in style:
                rh = raise_h * px_per_mm
                c.create_rectangle(bar_x + 8, base_y - body_h - rh, bar_x + bar_w - 8,
                                   base_y - body_h, fill=ink_c, outline="#333")
            # Labels
            lbl = "#aaa" if dark else "#555"
            c.create_text(bar_x + bar_w / 2, base_y + 10, fill=lbl, font=("Segoe UI", 8),
                          text=f"side view  {th:g} mm")
            c.create_text(bar_x - 4, base_y - d_h / 2, fill=lbl, font=("Segoe UI", 7),
                          text=f"{depth:g}", anchor="e")
            c.create_text(bar_x - 4, base_y - body_h - 2, fill=lbl, font=("Segoe UI", 7),
                          text="top", anchor="e")
        except Exception:
            pass
        self._draw_loading_overlay(c, w, h, dark)

    def render_png(self, px=640):
        (base, ink, body, _), o, word = self.geo
        from PIL import Image, ImageDraw
        body_c, ink_c, ink2_c = self.colors_for(word, o)
        x0, y0, x1, y1 = body.bounds
        S = px * 0.9 / max(x1 - x0, y1 - y0)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        im = Image.new("RGB", (px, px), "white")
        d = ImageDraw.Draw(im)

        def T(coords):
            return [(px / 2 + (x - cx) * S, px / 2 - (y - cy) * S) for x, y in coords]

        def paint(g, color, hole):
            for p in core.polys(g):
                d.polygon(T(p.exterior.coords), fill=color)
                for r in p.interiors:
                    d.polygon(T(r.coords), fill=hole)
        paint(body, body_c, "white")
        # v15.14: same fix as _paint_tag — ink holes are painted in the body
        # colour, largest polygons first, or the border ring paints as a
        # solid disc and hides the picture and word.
        if isinstance(ink, (list, tuple)):
            layers = list(zip(ink, (ink_c, ink2_c)))
        else:
            layers = [(ink, ink_c)]
        flat = []
        for g, col in layers:
            for p in core.polys(g):
                bx0, by0, bx1, by1 = p.bounds
                flat.append(((bx1 - bx0) * (by1 - by0), p, col))
        flat.sort(key=lambda t: -t[0])
        for _area, p, col in flat:
            d.polygon(T(p.exterior.coords), fill=col)
            for r in p.interiors:
                d.polygon(T(r.coords), fill=body_c)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue()

    # ============================================================ export (ONE button)
    def _fmt_flags(self):
        fmt = self.batch_fmt.get()
        return (fmt in (BATCH_FORMATS[0], BATCH_FORMATS[2]), fmt in (BATCH_FORMATS[1], BATCH_FORMATS[2]))

    @staticmethod
    def _files_for(n, want_3mf, want_stl):
        f = []
        if want_3mf:
            f.append(f"{n}.3mf")
        if want_stl:
            f += [f"{n}_base.stl", f"{n}_ink.stl"]
        return f

    def export_file(self):
        """Export the word on the tag in the Settings file format. v14.8: you now
        NAME the file first (a small dialog), for single files, separate tags and plates."""
        if self.busy:
            self.flash_status("Still working on the last export — one moment…", ok=False)
            return
        if getattr(self, "maker_multi", None) is not None and self.maker_multi.get():
            words = self._maker_preview_words()
            cnt = getattr(self, "maker_count_var", None)
            if cnt is not None:
                v = cnt.get()
                if v and v != "All":
                    try:
                        words = words[:max(1, int(v))]
                    except Exception:
                        pass
            if not words:
                messagebox.showinfo(APP_NAME, "No tags in the preview yet — put words on tags first.")
                return
            self._maker_export_dialog(words)
            return
        w = self.preview_word.get()
        if not w or w == "-":
            messagebox.showinfo(APP_NAME, "Put a word on the tag first.")
            return
        want_3mf, want_stl = self._fmt_flags()
        r = self._export_dialog("Export tag", core.safe_name(w), "maker",
                                lambda n: self._files_for(n, want_3mf, want_stl),
                                name_label="File name", note=f"Tag for \u201c{w}\u201d")
        if r:
            folder, name = r
            self.export_named(w, folder, name, want_3mf, want_stl)

    def _maker_export_dialog(self, words):
        """Multiple-tags export: a separate file for each tag, or one plate with
        every tag laid out together. v14.8: both ask for a name first."""
        win = ctk.CTkToplevel(self)
        win.title("Export tags")
        win.geometry("380x216")
        try:
            win.transient(self)
        except Exception:
            pass
        win.after(150, win.lift)
        ctk.CTkLabel(win, text=f"Export {len(words)} tags",
                     font=ctk.CTkFont(weight="bold", size=14)).pack(pady=(16, 4))
        ctk.CTkLabel(win, text="Files use the format from Settings (3MF / STL).",
                     text_color="gray").pack(pady=(0, 10))

        def go(plate):
            win.destroy()
            want_3mf, want_stl = self._fmt_flags()
            if plate:
                r = self._export_dialog(
                    "Export plate", "aac_plate", "maker",
                    lambda n: self._files_for(n, want_3mf, want_stl),
                    name_label="Plate file name",
                    note=f"All {len(words)} tags on one plate.")
                if r:
                    folder, name = r
                    self.export_many(words, folder, True, plate_name=name)
            else:
                r = self._export_dialog(
                    "Export separate tags", "aac_tags", "maker",
                    lambda n: [(n + "/") if n else "(each tag file, directly in the folder)"],
                    name_label="Folder name for the tag files (leave blank for none)",
                    ok_text="Export", folder_mode=True,
                    note=f"{len(words)} tags, one file per tag, named after its word.")
                if r:
                    folder, name = r
                    self.export_many(words, folder / name if name else folder, False)

        ctk.CTkButton(win, text="Separate file for each tag", height=36,
                      fg_color="#4ECDC4", hover_color="#6EDDD6",
                      command=lambda: go(False)).pack(fill="x", padx=20, pady=4)
        ctk.CTkButton(win, text="One plate — all tags together", height=36,
                      fg_color="#9B59B6", hover_color="#B07CC6",
                      command=lambda: go(True)).pack(fill="x", padx=20, pady=4)
        ctk.CTkButton(win, text="Cancel", height=30, fg_color="gray30", hover_color="#555",
                      command=win.destroy).pack(fill="x", padx=20, pady=(8, 12))

    def _display_colors(self, word, o, cat):
        """Colors a preview shows for one tag. v15.74: exactly the 15.67
        behavior — Body/Ink colors always, whatever the Category colours
        switch says. v15.72 made previews follow the switch (category
        colors), but Cody wants the preview to look exactly like 15.67,
        and 15.67's four preview paths all used (body, ink, ink2)
        unconditionally. The switch still drives the EXPORTED files via
        export_words; only the previews ignore it now. The 15.73
        automatic face fill is unaffected — it uses whatever base
        color this returns."""
        return (o.body_color, o.ink_color, o.ink2_color)

    def _export_args(self):
        return (self.collect(), dict(self.overrides), dict(self.variants), dict(self.search_as), self.cat_colors.get())

    def export_named(self, word, folder, name, want_3mf, want_stl):
        """Export ONE tag as {name}.3mf and/or {name}_base.stl + {name}_ink.stl in folder."""
        folder = Path(folder)
        self.last_dir = str(folder)
        o, ov, var, sa, cat = self._export_args()
        n = core.safe_name(word)
        self.save()
        log.info("Export file: '%s' -> %s / %s (3mf=%s stl=%s)", word, folder, name, want_3mf, want_stl)

        def fn():
            tmp = Path(tempfile.mkdtemp(prefix="aac_export_"))
            try:
                rep = core.export_words([word], tmp, o, ov, var, stl=want_stl, threemf=want_3mf,
                                        plate=False, progress=self.progress_cb(), search_as=sa, category_colors=cat)
                if rep["failed"]:
                    raise RuntimeError(rep["failed"][0][1])
                folder.mkdir(parents=True, exist_ok=True)
                outs = []
                try:
                    if want_3mf and (tmp / f"{n}.3mf").exists():
                        dst = folder / f"{name}.3mf"
                        shutil.copy2(tmp / f"{n}.3mf", dst)
                        outs.append(dst)
                    if want_stl:
                        for part in ("base", "ink", "ink2"):
                            src = tmp / f"{n}_{part}.stl"
                            if src.exists():
                                dst = folder / f"{name}_{part}.stl"
                                shutil.copy2(src, dst)
                                outs.append(dst)
                except PermissionError:
                    raise PermissionError("Can't save the file — it is open in another program "
                                          "(your slicer?). Close it or pick a different name, then try again.")
                return outs
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        def done(files):
            if files:
                self.last_export = files[0]
                self.flash_status("Exported " + " + ".join(f.name for f in files), hold=True)
                self.reveal_btn.grid()
            else:
                self.flash_status("Export finished with no files", ok=False, hold=True)
        self.bg("Exporting…", fn, done, lock=True, determinate=True)

    def export_many(self, words, folder, plate, plate_name="plate"):
        folder = Path(folder)
        o, ov, var, sa, cat = self._export_args()
        tmf, stl = self._fmt_flags()
        self.save()
        log.info("Export %d words -> %s (stl=%s 3mf=%s plate=%s name=%s)", len(words), folder, stl, tmf, plate, plate_name)

        def fn():
            try:
                return core.export_words(words, folder, o, ov, var, stl=stl, threemf=tmf, plate=plate,
                                         progress=self.progress_cb(), search_as=sa, category_colors=cat,
                                         plate_name=plate_name, plate_h=220.0)
            except PermissionError:
                raise PermissionError("Can't save into that folder/file — it may be open in another "
                                      "program (your slicer?). Close it and try again.")

        def done(rep):
            if rep["files"]:
                self.last_export = rep["files"][0]
                self.reveal_btn.grid()
            warns = [n for n in rep.get("notes", []) if n.startswith("Plate is")]
            if rep["failed"]:
                lines = "\n".join(f"  {w}: {e}" for w, e in rep["failed"])
                self.flash_status(f"Exported {len(rep['files'])} files, {len(rep['failed'])} failed", ok=False, hold=True)
                messagebox.showwarning(APP_NAME, f"Some words failed:\n{lines}\n\nSee Log for details.")
            else:
                what = (Path(rep["files"][0]).name if (plate and rep["files"]) else f"{len(rep['files'])} files to {folder.name}")
                self.flash_status(f"Exported {what}", hold=True)
            if warns:
                messagebox.showwarning(APP_NAME, "\n".join(warns))
        self.bg("Exporting…", fn, done, lock=True, determinate=True)

    def reveal_last(self):
        if self.last_export:
            reveal(self.last_export)

    # ============================================================ More menu: boards, PDFs, offline
    # ---- v13.10-exp: auto-save export locations (Settings → Advanced)
    def _auto_dir(self, studio):
        """Folder exports auto-save into for a studio, or None when auto-save
        is off or no folder is set (caller then shows the usual dialog)."""
        try:
            if not self.auto_export.get():
                return None
            var = {"pdf": self.export_dir_pdf, "maker": self.export_dir_maker,
                   "board": self.export_dir_board}.get(studio)
            d = (var.get() or "").strip() if var else ""
            return Path(d) if d else None
        except Exception:
            return None


    def _export_dialog(self, title, default_name, studio, files_fn, name_label="File name",
                       ok_text="Export", folder_mode=False, note=""):
        """v14.8: one small dialog to NAME the export before it happens.

        Shows the file name, the folder (Browse…), and a live preview of the exact
        file names that will be written. Asks before replacing anything that exists
        (Yes = replace, No = keep both with _2, Cancel = go back).
        files_fn(name) -> list of file/folder names (relative to the folder) the
        export will create. Returns (folder: Path, name: str) or None if cancelled.
        folder_mode: the name is a sub-folder to create (may be left blank)."""
        auto = self._auto_dir(studio)
        names = getattr(self, "_last_export_names", None)
        if names is None:
            names = self._last_export_names = {}
        key = f"{studio}:{title}"
        out = {}
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.geometry("480x300" if note else "480x270")
        win.resizable(False, False)
        try:
            win.transient(self)
            x = self.winfo_rootx() + (self.winfo_width() - 480) // 2
            y = self.winfo_rooty() + (self.winfo_height() - 300) // 3
            win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        except Exception:
            pass
        name_var = tk.StringVar(value=names.get(key, default_name))
        folder_var = tk.StringVar(value=str(auto) if auto else (self.last_dir or str(Path.home())))

        ctk.CTkLabel(win, text=title, font=ctk.CTkFont(weight="bold", size=14)).pack(pady=(14, 2))
        if note:
            ctk.CTkLabel(win, text=note, text_color="gray", font=ctk.CTkFont(size=11),
                         wraplength=440, justify="left").pack(padx=20, pady=(0, 2))
        ctk.CTkLabel(win, text=name_label, anchor="w").pack(fill="x", padx=20, pady=(6, 0))
        ent = ctk.CTkEntry(win, textvariable=name_var)
        ent.pack(fill="x", padx=20, pady=(2, 4))
        ctk.CTkLabel(win, text="Save in", anchor="w").pack(fill="x", padx=20)
        frow = ctk.CTkFrame(win, fg_color="transparent")
        frow.pack(fill="x", padx=20, pady=(2, 4))
        fent = ctk.CTkEntry(frow, textvariable=folder_var)
        fent.pack(side="left", fill="x", expand=True)

        def browse():
            d = filedialog.askdirectory(title="Save in…", initialdir=folder_var.get() or None, parent=win)
            if d:
                folder_var.set(d)
        ctk.CTkButton(frow, text="Browse…", width=80, fg_color="gray30", hover_color="#555",
                      command=browse).pack(side="left", padx=(6, 0))
        prev = ctk.CTkLabel(win, text="", anchor="w", justify="left", text_color="gray",
                            font=ctk.CTkFont(size=11), wraplength=440)
        prev.pack(fill="x", padx=20, pady=(2, 0))

        def clean():
            raw = name_var.get()
            if folder_mode and not raw.strip():
                return ""
            return core.clean_filename(raw, default_name)

        def refresh(*_):
            try:
                fl = files_fn(clean())
                prev.configure(text="Will create:  " + ",  ".join(fl))
            except Exception:
                prev.configure(text="")
        name_var.trace_add("write", refresh)
        refresh()

        def go(_e=None):
            nm = clean()
            folder = Path((folder_var.get() or "").strip() or ".")
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                return messagebox.showwarning(APP_NAME, f"Can't use that folder:\n{e}", parent=win)
            def clashes(n):
                return [f for f in files_fn(n) if (folder / f).exists()]
            hit = clashes(nm)
            if hit:
                ans = messagebox.askyesnocancel(
                    APP_NAME, f"{', '.join(hit)} already exists in that folder.\n\n"
                              "Yes = replace it\nNo = keep both (save as a new numbered name)\nCancel = go back",
                    parent=win)
                if ans is None:
                    return
                if ans is False:
                    base_n, k = (nm or default_name), 2
                    while clashes(f"{base_n}_{k}"):
                        k += 1
                    nm = f"{base_n}_{k}"
            names[key] = nm or default_name
            self.last_dir = str(folder)      # next dialog opens in the folder you chose
            out["r"] = (folder, nm)
            win.destroy()

        brow = ctk.CTkFrame(win, fg_color="transparent")
        brow.pack(fill="x", padx=20, pady=(10, 12), side="bottom")
        ctk.CTkButton(brow, text="Cancel", width=90, fg_color="gray30", hover_color="#555",
                      command=win.destroy).pack(side="right")
        ctk.CTkButton(brow, text=ok_text, fg_color="#4ECDC4", hover_color="#6EDDD6", text_color="#10262a",
                      command=go).pack(side="right", padx=(0, 8))
        win.bind("<Return>", go)
        win.bind("<Escape>", lambda _e: win.destroy())

        def _focus():
            try:
                win.lift()
                win.grab_set()
                ent.focus_set()
                ent.select_range(0, "end")
            except Exception:
                pass
        win.after(200, _focus)
        self.wait_window(win)
        return out.get("r")

    def _ask_pdf(self, name, studio="pdf"):
        """Name + folder for a PDF (v14.8). Returns the full path or None."""
        r = self._export_dialog("Save PDF", name, studio, lambda n: [f"{n}.pdf"],
                                name_label="PDF file name", ok_text="Save PDF")
        if not r:
            return None
        folder, nm = r
        return str(folder / f"{nm}.pdf")

    def _pdf_task(self, label, path, fn):
        if not path:
            return
        if self.busy:
            self.flash_status("Still working on the last export — one moment…", ok=False)
            return
        self.last_dir = str(Path(path).parent)

        def done(_):
            if Path(path).exists():
                self.last_export = Path(path)
                self.flash_status(f"Saved {Path(path).name}", hold=True)
                self.reveal_btn.grid()
            else:
                self.flash_status("PDF finished but the file wasn't found — see Log", ok=False, hold=True)
        self.bg(label, fn, done, lock=True)

    def build_offline_pack(self):
        words = self.get_words()
        if not words:
            return messagebox.showinfo(APP_NAME, "Add some words first.")
        d = filedialog.askdirectory(title="Build the offline AAC pack in…", initialdir=self.last_dir)
        if not d:
            return
        o, ov, var, sa, _ = self._export_args()
        out = Path(d) / "offline_aac_pack"

        def fn():
            return aac_boards.build_offline_pack(words, out, o, ov, var, sa, progress=self.progress_cb(), make_3d=True)

        def done(rep):
            self.last_export = Path(rep["dir"])
            self.flash_status(f"Offline pack ready: {rep['dir']}", hold=True)
            self.reveal_btn.grid()
        self.bg("Building offline pack…", fn, done, lock=True, determinate=True)

    def _build_ai(self, tab, panel=None):
        """AI (Local) — chat-style full-window UI (v14.6.1 look).

        Sidebar tab is a pointer; real content lives in the full-window panel.
        Layout mirrors a modern chat app: header + search + message bubbles +
        bottom composer, with a slim action rail on the right. All previous
        controls (models, SD Turbo, switches, banner) are kept under Settings.
        """
        self.header(tab, "AI (Local)", first=True)
        ctk.CTkLabel(tab, text="The AI tab opens full screen as a chat. Status, models, "
                               "and tools live under Settings on the right.",
                     text_color="gray", wraplength=320, anchor="w", justify="left").pack(fill="x", pady=(0, 6))
        ctk.CTkButton(tab, text="Open AI full screen", fg_color=AI_COLOR, hover_color=AI_HOVER,
                      command=lambda: self.goto_tab(AI_TAB)).pack(fill="x")
        if panel is None:
            panel = tab

        # ---- colour tokens for the chat look (dark-friendly)
        _CHAT_BG = ("#e8ecf2", "#1a1d27")
        _BUBBLE_AI = ("#dfe6f0", "#2a2f3d")
        _BUBBLE_USER = ("#c8daf5", "#3a4d6b")
        _RAIL_BG = ("#d5dbe6", "#151820")
        _INPUT_BG = ("#f4f6fa", "#222633")
        _ACCENT_BTN = "#4c8bf5"
        _ACCENT_HOV = "#6aa1ff"

        root = ctk.CTkFrame(panel, fg_color="transparent")
        root.pack(fill="both", expand=True)
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(0, weight=1)

        # ===================== MAIN CHAT COLUMN =====================
        chat = ctk.CTkFrame(root, fg_color=_CHAT_BG, corner_radius=16)
        chat.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        chat.grid_rowconfigure(3, weight=1)
        chat.grid_columnconfigure(0, weight=1)

        # -- header: avatar + title + live status
        hdr = ctk.CTkFrame(chat, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        av = ctk.CTkFrame(hdr, width=44, height=44, corner_radius=22,
                          fg_color=("#b8c4d8", "#3a4258"))
        av.pack(side="left")
        av.pack_propagate(False)
        ctk.CTkLabel(av, text="🤖", font=ctk.CTkFont(size=20)).place(relx=0.5, rely=0.5, anchor="center")
        titles = ctk.CTkFrame(hdr, fg_color="transparent")
        titles.pack(side="left", padx=(12, 0), fill="x", expand=True)
        ctk.CTkLabel(titles, text="Local AI", font=ctk.CTkFont(size=20, weight="bold"),
                     anchor="w").pack(anchor="w")
        self.ai_status = ctk.CTkLabel(titles, text="", anchor="w",
                                      font=ctk.CTkFont(size=12), text_color=("gray30", "gray75"))
        self.ai_status.pack(anchor="w")
        ctk.CTkButton(hdr, text="Set up local AI", width=140, height=32,
                      fg_color=AI_COLOR, hover_color=AI_HOVER,
                      command=self.setup_local_ai).pack(side="right")

        # -- search bar (filters the visible chat transcript)
        search_wrap = ctk.CTkFrame(chat, fg_color="transparent")
        search_wrap.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 8))
        self.ai_search = ctk.CTkEntry(search_wrap, placeholder_text="Search messages…",
                                      height=36, corner_radius=18,
                                      fg_color=_INPUT_BG, border_width=0)
        self.ai_search.pack(fill="x")
        self.ai_search.bind("<KeyRelease>", self._ai_filter_chat)

        # -- activity banner (kept, compact, under search)
        self.ai_banner_frame = ctk.CTkFrame(chat, corner_radius=12, fg_color=("gray85", "gray20"))
        self.ai_banner_frame.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 6))
        self.ai_banner_frame.grid_columnconfigure(0, weight=1)
        self.ai_banner_title = ctk.CTkLabel(self.ai_banner_frame, text="💤  AI is idle", anchor="w",
                                            font=ctk.CTkFont(size=14, weight="bold"))
        self.ai_banner_title.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 0))
        self.ai_banner_time = ctk.CTkLabel(self.ai_banner_frame, text="", anchor="e",
                                           font=ctk.CTkFont(size=12, weight="bold"), text_color=("gray30", "gray85"))
        self.ai_banner_time.grid(row=0, column=1, sticky="e", padx=12, pady=(8, 0))
        self.ai_banner_detail = ctk.CTkLabel(
            self.ai_banner_frame, anchor="w", justify="left", wraplength=700,
            text="Nothing is being generated. Type a request below or right-click any word → Generate an image.",
            text_color=("gray35", "gray70"), font=ctk.CTkFont(size=12))
        self.ai_banner_detail.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(2, 4))
        self.ai_banner_bar = ctk.CTkProgressBar(self.ai_banner_frame, mode="indeterminate", height=6)
        self.ai_banner_bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 4))
        self.ai_banner_bar.grid_remove()
        _brow = ctk.CTkFrame(self.ai_banner_frame, fg_color="transparent")
        _brow.grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8))
        ctk.CTkButton(_brow, text="📚 AI pictures", width=120, height=26, fg_color="#9B59B6",
                      hover_color="#B07CC6", command=lambda: self.open_library_ai("Pictures")).pack(side="left", padx=3)
        ctk.CTkButton(_brow, text="📚 AI words", width=110, height=26, fg_color="#2f6fb3",
                      hover_color="#3f7fc3", command=lambda: self.open_library_ai("Words")).pack(side="left", padx=3)

        # -- scrollable message area (chat bubbles)
        self.ai_chat = ctk.CTkScrollableFrame(chat, fg_color="transparent", corner_radius=0)
        self.ai_chat.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 4))
        self.ai_chat.grid_columnconfigure(0, weight=1)
        self._ai_bubbles = []          # list of (role, text, frame)
        self._ai_chat_history = []     # list of (role, text) for filter/rebuild

        # hidden legacy textbox so any old code that writes ai_out still works
        self.ai_out = ctk.CTkTextbox(chat, height=1)
        self.ai_out.grid_remove()

        # -- composer (bottom)
        _orow = ctk.CTkFrame(chat, fg_color="transparent")
        _orow.grid(row=4, column=0, sticky="ew", padx=16, pady=(4, 0))
        ctk.CTkLabel(_orow, text="New words go to:", anchor="w",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(2, 8))
        ctk.CTkSegmentedButton(_orow, values=AI_TARGETS, variable=self.ai_target_var,
                               command=lambda _v: self.save()).pack(side="left")
        ctk.CTkCheckBox(_orow, text="AI-draw pictures for them", variable=self.ai_pics_var,
                        command=self.save, checkbox_width=18, checkbox_height=18,
                        font=ctk.CTkFont(size=12)).pack(side="left", padx=(14, 0))
        ctk.CTkLabel(_orow, text="Picture style:", font=ctk.CTkFont(size=12)).pack(side="left", padx=(14, 4))
        ctk.CTkOptionMenu(_orow, values=[ai.AAC_STYLE_NAME] + list(ai.SD_STYLES),
                          variable=self.ai_style_var, width=140,
                          command=lambda _v: self.save()).pack(side="left")
        ctk.CTkLabel(_orow, text="Only adds new ones \u2014 never changes what you have. "
                                 "Say a number (\u201c7 words\u2026\u201d) and where (\u201cin PDF Studio\u201d) to override.",
                     text_color=("gray30", "gray75"), font=ctk.CTkFont(size=11), anchor="w").pack(side="left", padx=(14, 0))
        composer = ctk.CTkFrame(chat, fg_color=_INPUT_BG, corner_radius=22, height=52)
        composer.grid(row=5, column=0, sticky="ew", padx=16, pady=(6, 14))
        composer.grid_columnconfigure(0, weight=1)
        self.ai_cmd = ctk.CTkTextbox(composer, height=44, wrap="word",
                                     fg_color="transparent", border_width=0,
                                     font=ctk.CTkFont(size=14))
        self.ai_cmd.grid(row=0, column=0, sticky="ew", padx=(14, 6), pady=4)
        self.ai_cmd.insert("1.0", "Type your message…")
        self.ai_cmd.bind("<FocusIn>", self._clear_placeholder)
        self.ai_cmd.bind("<Return>", self._ai_cmd_enter)
        ctk.CTkButton(composer, text="📎", width=36, height=36, corner_radius=18,
                      fg_color="transparent", hover_color=("gray75", "gray30"),
                      command=self._ai_attach_hint).grid(row=0, column=1, padx=(0, 2))
        ctk.CTkButton(composer, text="➤", width=40, height=36, corner_radius=18,
                      fg_color=_ACCENT_BTN, hover_color=_ACCENT_HOV,
                      command=self.ai_run_command).grid(row=0, column=2, padx=(0, 8))

        # ===================== RIGHT ACTION RAIL =====================
        rail = ctk.CTkFrame(root, width=168, fg_color=_RAIL_BG, corner_radius=16)
        rail.grid(row=0, column=1, sticky="ns")
        rail.grid_propagate(False)
        rail_inner = ctk.CTkFrame(rail, fg_color="transparent")
        rail_inner.pack(fill="both", expand=True, padx=10, pady=14)

        ctk.CTkButton(rail_inner, text="💬  New Chat", height=40, corner_radius=12,
                      fg_color=_ACCENT_BTN, hover_color=_ACCENT_HOV,
                      command=self._ai_new_chat).pack(fill="x", pady=(0, 14))

        def _rail_btn(text, cmd):
            # v15.69: transparent buttons keep the theme's white text by default,
            # which is invisible on the light rail — set an explicit dark/light pair.
            ctk.CTkButton(rail_inner, text=text, height=38, corner_radius=10,
                          fg_color="transparent", hover_color=("gray70", "gray28"),
                          text_color=("#1c2436", "#e8ecf2"),
                          anchor="w", command=cmd).pack(fill="x", pady=3)

        _rail_btn("⚙   Settings", self._ai_toggle_settings)
        _rail_btn("❓   Help", self._ai_show_help)
        _rail_btn("📋   Explain log", self.ai_explain_log)

        ctk.CTkLabel(rail_inner, text="Generate", font=ctk.CTkFont(size=11),
                     text_color=("gray30", "gray75"), anchor="w").pack(fill="x", pady=(10, 2))
        # v15.69: Generate is two actions — picture, wordlist. The old "Both"
        # is covered by "Create wordlist" + the "AI-draw pictures" checkbox.
        _rail_btn("✨   Generate picture", self.ai_sd_picture)
        _rail_btn("📝   Create wordlist", self.ai_suggest_words)
        _rail_btn("📁   Open local_ai", self.open_local_ai_folder)

        ctk.CTkLabel(rail_inner, text="", height=8).pack()
        ctk.CTkFrame(rail_inner, height=1, fg_color=("gray70", "gray35")).pack(fill="x", pady=6)
        _rail_btn("↩   Back to app", lambda: self.goto_tab("Maker Studio"))

        # ===================== SETTINGS / MODELS PANEL (toggle) =====================
        self.ai_settings = ctk.CTkFrame(root, fg_color=_CHAT_BG, corner_radius=16)
        # not gridded until toggled
        self._ai_settings_visible = False
        sc = ctk.CTkScrollableFrame(self.ai_settings, fg_color="transparent")
        sc.pack(fill="both", expand=True, padx=8, pady=8)
        sc.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(sc, fg_color="transparent")
        top.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(top, text="Settings & models", font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")
        ctk.CTkButton(top, text="Close ✕", width=80, height=28, fg_color="gray30", hover_color="#555",
                      command=self._ai_toggle_settings).pack(side="right")

        # left-style content: local_ai folder / models (was left column)
        left = ctk.CTkFrame(sc, fg_color="transparent")
        left.pack(fill="x", pady=4)
        self.header(left, "Your local AI folder", first=True)
        ctk.CTkLabel(left, text="On-device only. Two downloads:\n"
                                "1) Qwen2.5-3B Instruct (~2.1 GB) + llama.cpp engine — for words & help.\n"
                                "2) SD Turbo (~5.2 GB model + engine) — for real pictures.\n"
                                "You’ll get a clear “ready” alert when each one finishes.",
                     text_color=("gray30", "gray75"), wraplength=640, justify="left", anchor="w").pack(fill="x", pady=(0, 6))
        self.ai_folder_label = ctk.CTkLabel(left, text="", anchor="w", text_color=("gray30", "gray75"),
                                            wraplength=640, justify="left",
                                            font=ctk.CTkFont(size=12))
        self.ai_folder_label.pack(fill="x", pady=(0, 8))
        _airow = ctk.CTkFrame(left, fg_color="transparent")
        _airow.pack(fill="x")
        ctk.CTkButton(_airow, text="1 · Download Qwen 3B (~2.1 GB)", height=34, fg_color="#FF6B6B",
                      hover_color="#FF8E8E", command=self.download_local_ai_model).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(_airow, text="llama.cpp engine", height=34, fg_color="#FF6B6B",
                      hover_color="#FF8E8E", command=self.download_llama_engine).pack(
                          side="left", fill="x", expand=True, padx=(8, 0))
        _airow2 = ctk.CTkFrame(left, fg_color="transparent")
        _airow2.pack(fill="x", pady=(8, 0))
        ctk.CTkButton(_airow2, text="Open local_ai folder", height=32, fg_color="gray30", hover_color="#555",
                      command=self.open_local_ai_folder).pack(side="left", fill="x", expand=True)

        self.header(left, "Image AI — SD Turbo", first=False)
        ctk.CTkLabel(left, text="Stable Diffusion Turbo creates brand-new pictures on this PC. "
                                "Pictures are saved into your Library. Model ~5.2 GB; engine ~18 MB. "
                                "Download both — you’ll get a “ready” alert when each finishes.",
                     text_color=("gray30", "gray75"), wraplength=640, justify="left", anchor="w").pack(fill="x", pady=(0, 6))
        _airow3 = ctk.CTkFrame(left, fg_color="transparent")
        _airow3.pack(fill="x")
        ctk.CTkButton(_airow3, text="2 · Download SD Turbo model (~5.2 GB)", height=34, fg_color="#9B59B6",
                      hover_color="#B07CC6", command=self.download_sd_model_gui).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(_airow3, text="SD engine", height=34, fg_color="#9B59B6",
                      hover_color="#B07CC6", command=self.download_sd_engine_gui).pack(
                          side="left", fill="x", expand=True, padx=(8, 0))

        self.header(left, "Illustration style prompts", first=False)
        ctk.CTkLabel(left, text="Used when the Picture style is \u201cAAC pastel\u201d (the default for new "
                                "pictures). The picture itself is drawn without any word, border or card "
                                "layout — the app adds those afterwards. Technical details live here so the "
                                "main tab stays simple: enter a word, generate, preview, and the Library "
                                "confirmation only appears once the picture is actually saved.",
                     text_color=("gray30", "gray75"), wraplength=640, justify="left", anchor="w").pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(left, text="Style prompt (permanent illustration style)",
                     font=ctk.CTkFont(size=12, weight="bold"), anchor="w").pack(fill="x")
        self.ai_style_text = ctk.CTkTextbox(left, height=110, wrap="word")
        self.ai_style_text.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(left, text="Negative prompt (sent separately to the image model)",
                     font=ctk.CTkFont(size=12, weight="bold"), anchor="w").pack(fill="x")
        self.ai_negative_text = ctk.CTkTextbox(left, height=110, wrap="word")
        self.ai_negative_text.pack(fill="x", pady=(0, 6))
        _srow = ctk.CTkFrame(left, fg_color="transparent")
        _srow.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(_srow, text="Subject descriptions:", anchor="w",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0, 8))
        self.ai_subject_mode_var = tk.StringVar(value="Dictionary first, then text model")
        ctk.CTkOptionMenu(_srow, values=["Dictionary first, then text model", "Dictionary only"],
                          variable=self.ai_subject_mode_var, width=240,
                          command=lambda _v: self._ai_style_save()).pack(side="left")
        _sbtn = ctk.CTkFrame(left, fg_color="transparent")
        _sbtn.pack(fill="x", pady=(0, 6))
        ctk.CTkButton(_sbtn, text="Save illustration style", height=32,
                      command=self._ai_style_save).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(_sbtn, text="Restore default style", height=32, fg_color="gray30", hover_color="#555",
                      command=self._ai_style_restore).pack(side="left", fill="x", expand=True, padx=(8, 0))
        self._ai_style_refresh()

        self.header(left, "Advanced", first=False)
        ctk.CTkLabel(left, text="Reset removes EVERYTHING in local_ai/ then reinstalls the stack. "
                                "Needs internet (~7 GB). Your words, lists and pictures are NOT touched.",
                     text_color=("gray30", "gray75"), wraplength=640, justify="left", anchor="w").pack(fill="x", pady=(0, 6))
        ctk.CTkButton(left, text="Reset local AI — remove everything & reinstall…", height=32,
                      fg_color="#8c3a3a", hover_color="#a34a4a",
                      command=self.ai_reset_all).pack(fill="x")

        auto = ctk.CTkFrame(sc, fg_color="transparent")
        auto.pack(fill="x", pady=(12, 6))
        self.header(auto, "Automatic", first=True)
        srow = ctk.CTkFrame(auto, fg_color="transparent")
        srow.pack(fill="x")
        self.sw_pick = ctk.CTkSwitch(srow, text="AI picks the best picture", command=self.on_ai_switch)
        self.sw_pick.pack(side="left", padx=(0, 28), pady=4)
        self.sw_draw = ctk.CTkSwitch(srow, text="AI draws an icon if none is found", command=self.on_ai_switch)
        self.sw_draw.pack(side="left", pady=4)
        if ai.cfg.pick_best:
            self.sw_pick.select()
        if ai.cfg.auto_draw:
            self.sw_draw.select()

        self.refresh_ai_status()
        self.refresh_ai_folder_label()
        # welcome bubbles
        self.ai_say("Hello, how can I help you today?")
        self.ai_say("I'm here to assist you with any questions you have. What would you like to know?\n\n"
                    "This is 100% on-device AI — your words never leave this PC.\n"
                    "Click “Set up local AI” if models aren't installed yet. No accounts, no keys.")

    def _ai_add_bubble(self, role, text):
        """Append a chat bubble. role is 'ai' or 'user'."""
        if not hasattr(self, "ai_chat") or self.ai_chat is None:
            return
        text = (text or "").strip()
        if not text:
            return
        self._ai_chat_history.append((role, text))
        _BUBBLE_AI = ("#dfe6f0", "#2a2f3d")
        _BUBBLE_USER = ("#c8daf5", "#3a4d6b")
        row = ctk.CTkFrame(self.ai_chat, fg_color="transparent")
        row.pack(fill="x", pady=4, padx=6)
        if role == "user":
            # spacer left, bubble right
            ctk.CTkFrame(row, fg_color="transparent", width=80).pack(side="left", fill="x", expand=True)
            bubble = ctk.CTkFrame(row, fg_color=_BUBBLE_USER, corner_radius=14)
            bubble.pack(side="right", padx=(8, 4))
        else:
            bubble = ctk.CTkFrame(row, fg_color=_BUBBLE_AI, corner_radius=14)
            bubble.pack(side="left", padx=(4, 8))
            ctk.CTkFrame(row, fg_color="transparent", width=80).pack(side="right", fill="x", expand=True)
        lbl = ctk.CTkLabel(bubble, text=text, wraplength=520, justify="left", anchor="w",
                           font=ctk.CTkFont(size=13))
        lbl.pack(padx=14, pady=10)
        self._ai_bubbles.append((role, text, row))
        try:
            self.ai_chat._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _ai_rebuild_chat(self, history=None, *, keep_master=True):
        """Clear bubbles and redraw from history (optionally filtered).

        When keep_master is True (default), self._ai_chat_history is not replaced
        by a filtered view — only the on-screen bubbles change.
        """
        if not hasattr(self, "ai_chat"):
            return
        for child in list(self.ai_chat.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        self._ai_bubbles = []
        hist = history if history is not None else list(getattr(self, "_ai_chat_history", []))
        master = list(getattr(self, "_ai_chat_history", []))
        # Draw without pushing into master history
        _BUBBLE_AI = ("#dfe6f0", "#2a2f3d")
        _BUBBLE_USER = ("#c8daf5", "#3a4d6b")
        for role, text in hist:
            text = (text or "").strip()
            if not text:
                continue
            row = ctk.CTkFrame(self.ai_chat, fg_color="transparent")
            row.pack(fill="x", pady=4, padx=6)
            if role == "user":
                ctk.CTkFrame(row, fg_color="transparent", width=80).pack(side="left", fill="x", expand=True)
                bubble = ctk.CTkFrame(row, fg_color=_BUBBLE_USER, corner_radius=14)
                bubble.pack(side="right", padx=(8, 4))
            else:
                bubble = ctk.CTkFrame(row, fg_color=_BUBBLE_AI, corner_radius=14)
                bubble.pack(side="left", padx=(4, 8))
                ctk.CTkFrame(row, fg_color="transparent", width=80).pack(side="right", fill="x", expand=True)
            ctk.CTkLabel(bubble, text=text, wraplength=520, justify="left", anchor="w",
                         font=ctk.CTkFont(size=13)).pack(padx=14, pady=10)
            self._ai_bubbles.append((role, text, row))
        if keep_master:
            self._ai_chat_history = master
        try:
            self.ai_chat._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _ai_filter_chat(self, _event=None):
        q = ""
        try:
            q = (self.ai_search.get() or "").strip().lower()
        except Exception:
            return
        hist = getattr(self, "_ai_chat_history", [])
        if not q:
            self._ai_rebuild_chat(hist)
            return
        filtered = [(r, t) for r, t in hist if q in t.lower()]
        self._ai_rebuild_chat(filtered, keep_master=True)

    def _ai_new_chat(self):
        """Clear the transcript and start fresh (like New Chat in the mock-up)."""
        self._ai_chat_history = []
        self._ai_rebuild_chat([])
        try:
            self.ai_cmd.delete("1.0", "end")
            self.ai_cmd.insert("1.0", "Type your message…")
        except Exception:
            pass
        self.ai_say("Hello, how can I help you today?")
        self.ai_say("New chat started. Ask for words, tag styles, or picture ideas — everything stays on this PC.")

    def _ai_toggle_settings(self):
        """Show / hide the Settings & models panel over the chat."""
        if not hasattr(self, "ai_settings"):
            return
        if self._ai_settings_visible:
            self.ai_settings.grid_remove()
            self._ai_settings_visible = False
        else:
            self.ai_settings.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
            self.ai_settings.tkraise()
            self._ai_settings_visible = True
            self._ai_style_refresh()

    def _ai_style_refresh(self):
        """Fill the illustration-style fields from the current configuration."""
        try:
            cfg = aac_style.get_config()
            for box, val in ((self.ai_style_text, cfg.style_prompt),
                             (self.ai_negative_text, cfg.negative_prompt)):
                box.delete("1.0", "end")
                box.insert("1.0", val)
            self.ai_subject_mode_var.set(
                "Dictionary only" if cfg.subject_mode == aac_style.SUBJECT_MODE_DICTIONARY
                else "Dictionary first, then text model")
        except Exception:
            pass

    def _ai_style_save(self):
        """Apply the Advanced illustration-style fields and persist them."""
        try:
            style = self.ai_style_text.get("1.0", "end").strip()
            negative = self.ai_negative_text.get("1.0", "end").strip()
            if not style:
                # an empty style prompt would leave generation style-less;
                # fall back to the default and say so plainly
                style = aac_style.DEFAULT_STYLE_PROMPT
                self.ai_style_text.delete("1.0", "end")
                self.ai_style_text.insert("1.0", style)
                self.flash_status("Style prompt was empty — restored the default style", ok=False)
            aac_style.set_config(aac_style.StyleConfig(
                style_prompt=style, negative_prompt=negative,
                subject_mode=(aac_style.SUBJECT_MODE_DICTIONARY
                              if self.ai_subject_mode_var.get() == "Dictionary only"
                              else aac_style.SUBJECT_MODE_MODEL)))
            self.save()
            self.flash_status("Illustration style saved — new \u201cAAC pastel\u201d pictures will use it", ok=True)
        except Exception as e:
            self.flash_status(f"Could not save the illustration style: {e}", ok=False)

    def _ai_style_restore(self):
        """Restore the default style prompts and subject behaviour."""
        try:
            aac_style.reset_config()
            self._ai_style_refresh()
            self.save()
            self.flash_status("Default illustration style restored", ok=True)
        except Exception as e:
            self.flash_status(f"Could not restore the default style: {e}", ok=False)

    def _ai_show_help(self):
        self.ai_say(
            "Local AI help\n\n"
            "• Type a request in the box at the bottom (e.g. “12 mealtime words as hexagon tags”) and press Enter or ➤.\n"
            "• Settings (right rail) — download models, SD Turbo, and auto-picture switches.\n"
            "• Generate on the rail:\n"
            "    ✨ Generate picture — picture for the current word in the Picture style menu (AAC pastel by default).\n"
            "    📝 Create wordlist — suggest words for a situation (tick “AI-draw pictures for them” to also make pictures, like the old Both).\n"
            "• Explain log — plain-English summary of recent log messages.\n"
            "• Everything runs on this PC. No cloud, no API keys for local models."
        )

    def _ai_attach_hint(self):
        self.ai_say("Attachments aren't needed here — drop PNG/JPG icons into the icons folder "
                    "or use Library → Pictures. For a new AI image, use ✨ Generate picture on the right rail.")

    def _ai_cmd_enter(self, event=None):

        """Enter in the AI command box = clicking '✨ Do it' (13.14)."""
        self.ai_run_command()
        return "break"

    def _show_ai_panel(self, show):
        """v13.14: AI (Local) is a FULL-WINDOW view — the sidebar and the preview area are
        hidden while it is open, so the AI inputs and info get the whole screen."""
        try:
            if show:
                self.side.grid_remove()
                self.main.grid_remove()
                self.ai_panel.grid()
                try:
                    self.ai_nav.set(AI_TAB)
                except Exception:
                    pass
            else:
                self.ai_panel.grid_remove()
                self.side.grid()
                self.main.grid()
                self._apply_main_view()
        except Exception:
            pass

    def _apply_main_view(self):
        """Right side shows either the preview canvas or the Library (Word Lists tab only)."""
        try:
            if self._lib_open and self.tabs.get() == "Word Lists":
                self.canvas.grid_remove()
                self.lib_panel.grid()
                self.export_btn.grid_remove()
            else:
                self.lib_panel.grid_remove()
                self.canvas.grid()
                self.export_btn.grid()
        except Exception:
            pass

    # ============================================================ Library (v13.14)
    LIB_CELL_W, LIB_CELL_H = 128, 150

    def _build_library(self, p):
        """Library: every word and every saved picture in one place. Right-click an
        item to send it to the 3D tag or to the PDF Studio you're working on."""
        dark = ctk.get_appearance_mode() == "Dark"
        bar = ctk.CTkFrame(p, fg_color="transparent")
        bar.pack(fill="x")
        ctk.CTkLabel(bar, text="\U0001F4DA Library", font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        self.lib_mode = ctk.CTkSegmentedButton(bar, values=["Words", "Pictures"], command=self._lib_set_mode)
        self.lib_mode.set("Words")
        self.lib_mode.pack(side="left", padx=14)
        self.lib_search = tk.StringVar(value="")
        self.lib_search.trace_add("write", lambda *_: self._lib_filter())
        ctk.CTkEntry(bar, textvariable=self.lib_search, placeholder_text="Search…", width=160).pack(side="left")
        # Sort / group for Words view
        self.lib_sort = tk.StringVar(value="By theme")
        ctk.CTkLabel(bar, text="  Sort", text_color="gray").pack(side="left", padx=(8, 2))
        ctk.CTkOptionMenu(bar, values=["By theme", "A → Z", "Z → A", "By source", "✨ AI only"],
                          variable=self.lib_sort, width=110,
                          command=lambda _: self._lib_filter()).pack(side="left")
        ctk.CTkButton(bar, text="✕ Close", width=80, fg_color="gray30", hover_color="#555",
                      command=self.close_library).pack(side="right")
        ctk.CTkButton(bar, text="↻", width=36, fg_color="gray30", hover_color="#555",
                      command=self._lib_reload).pack(side="right", padx=(0, 6))
        self.lib_count = ctk.CTkLabel(bar, text="", text_color="gray")
        self.lib_count.pack(side="right", padx=10)
        self.lib_hint = ctk.CTkLabel(p, anchor="w", text_color="gray", font=ctk.CTkFont(size=12), text="")
        self.lib_hint.pack(fill="x", pady=(6, 0))
        body = ctk.CTkFrame(p, fg_color="transparent")
        body.pack(fill="both", expand=True, pady=(6, 0))
        bg = "#2b2b2b" if dark else "#f0f0f0"
        fg = "white" if dark else "black"

        # ---- words view: tiles (virtual grid, same idea as the pictures view)
        self.lib_words_frame = ctk.CTkFrame(body, fg_color="transparent")
        self.lib_wcanvas = tk.Canvas(self.lib_words_frame, bg=bg, highlightthickness=0, bd=0,
                                     yscrollincrement=50)
        wsb = ctk.CTkScrollbar(self.lib_words_frame, command=self._lib_wyview)
        self.lib_wcanvas.configure(yscrollcommand=wsb.set)
        wsb.pack(side="right", fill="y")
        self.lib_wcanvas.pack(side="left", fill="both", expand=True)
        self.lib_wcanvas.bind("<Configure>", lambda e: self._lib_wlayout())
        self.lib_wcanvas.bind("<Button-1>", self._lib_word_click)
        self.lib_wcanvas.bind("<Control-Button-1>", lambda e: self._lib_word_click(e, "ctrl"))
        self.lib_wcanvas.bind("<Shift-Button-1>", lambda e: self._lib_word_click(e, "shift"))
        self.lib_wcanvas.bind("<Double-Button-1>", self._lib_word_dbl)
        for ev in ("<Button-3>", "<Button-2>"):
            self.lib_wcanvas.bind(ev, self._lib_word_context)
        self.lib_wcanvas.bind("<MouseWheel>", self._lib_wwheel)
        self.lib_wcanvas.bind("<Button-4>", lambda e: self._lib_wwheel(e, 1))
        self.lib_wcanvas.bind("<Button-5>", lambda e: self._lib_wwheel(e, -1))

        # ---- pictures view (virtual grid: only visible thumbnails are drawn)
        self.lib_pics_frame = ctk.CTkFrame(body, fg_color="transparent")
        self.lib_canvas = tk.Canvas(self.lib_pics_frame, bg=bg, highlightthickness=0, bd=0,
                                    yscrollincrement=50)
        psb = ctk.CTkScrollbar(self.lib_pics_frame, command=self._lib_yview)
        self.lib_canvas.configure(yscrollcommand=psb.set)
        psb.pack(side="right", fill="y")
        self.lib_canvas.pack(side="left", fill="both", expand=True)
        self.lib_canvas.bind("<Configure>", lambda e: self._lib_layout())
        self.lib_canvas.bind("<Button-1>", self._lib_pic_click)
        self.lib_canvas.bind("<Double-Button-1>", self._lib_pic_dbl)
        for ev in ("<Button-3>", "<Button-2>", "<Control-Button-1>"):
            self.lib_canvas.bind(ev, self._lib_pic_context)
        self.lib_canvas.bind("<MouseWheel>", self._lib_wheel)
        self.lib_canvas.bind("<Button-4>", lambda e: self._lib_wheel(e, 1))
        self.lib_canvas.bind("<Button-5>", lambda e: self._lib_wheel(e, -1))
        self.lib_words_frame.pack(fill="both", expand=True)

    # ---- open / close
    def open_library(self):
        try:
            if self.tabs.get() != "Word Lists":
                self.tabs.set("Word Lists")
            self._lib_open = True
            self._lib_user_closed = False
            self._lib_reload()
            self.on_workspace_change()
            self._apply_main_view()
            self._lib_update_hint()
        except Exception:
            log.exception("open library")

    def close_library(self):
        self._lib_open = False
        self._lib_user_closed = True
        self._apply_main_view()
        self.on_workspace_change()

    def _studio_name(self, which=None):
        return "PDF Studio" if (which or self._last_studio) == "pdf" else "3D tag"

    def _lib_update_hint(self):
        self.lib_hint.configure(
            text=f"Right-click a word or picture → send it to the 3D tag or PDF Studio.  "
                 f"Double-click sends to the studio you were last in: {self._studio_name()}.  "
                 f"✨ AI-made words and pictures are purple — use Sort → “✨ AI only”.")

    # ---- data
    def _lib_fingerprint(self):
        """v14.3: cheap snapshot of everything the Library shows (word
        lists + picture files on disk). Entering Word Lists used to rescan
        and rebuild the whole Library on EVERY tab switch; now it only
        reloads when this fingerprint says something actually changed.
        Directory scans use entry stats only — no per-file path resolving."""
        import os
        parts = []
        try:
            parts.append(tuple(self.get_words()))
        except Exception:
            parts.append(None)
        try:
            parts.append(tuple((name, tuple(self._saved_list_words(name)))
                               for name in sorted(self.word_lists)))
        except Exception:
            parts.append(None)
        try:
            f = core.AI_LIBRARY_FILE
            parts.append((f.stat().st_mtime_ns, f.stat().st_size) if f.exists() else None)
        except Exception:
            parts.append(None)
        try:
            dirs = []
            for d in core._icon_dirs():
                n, tot, mx = 0, 0, 0
                try:
                    with os.scandir(d) as it:
                        for e in it:
                            if e.name.lower().endswith((".png", ".jpg", ".jpeg")):
                                try:
                                    st = e.stat()
                                except Exception:
                                    continue
                                n += 1
                                tot += st.st_size
                                if st.st_mtime_ns > mx:
                                    mx = st.st_mtime_ns
                except Exception:
                    pass
                dirs.append((str(d), n, tot, mx))
            parts.append(tuple(dirs))
        except Exception:
            parts.append(None)
        return repr(parts)

    @staticmethod
    def _lib_word_from_file(path, safe_map):
        stem = Path(path).stem
        stem = re.sub(r"_arasaac_\d+$", "", stem, flags=re.I)
        stem = core.ai_picture_word_base(stem)          # v13.23: apple_ai_2 -> apple
        base = re.sub(r"_\d+$", "", stem) or stem
        return safe_map.get(base.lower()) or base.replace("_", " ")

    def _lib_reload(self, keep=False):
        """Collect every word (all lists + presets + saved pictures) and every saved picture."""
        words = {}  # lower -> [display, [sources]]

        def add(w, src):
            w = str(w).strip()
            if not w:
                return
            e = words.setdefault(w.lower(), [w, []])
            if src not in e[1]:
                e[1].append(src)
        try:
            for w, _kind, _when in core.ai_words():      # v13.23: AI-generated words, labelled AI
                add(w, "AI")
            for w in self.get_words():
                add(w, "Current words")
            for name in list(self.word_lists):
                for w in self._saved_list_words(name):
                    add(w, f"My list: {name}")
            for w in core.COMMON_WORDS:
                add(w, "Common AAC words")
            for name, ws in aac_boards.TOPIC_PACKS.items():
                for w in ws:
                    add(w, name)
            for name in core.preset_list_names():
                for w in core.preset_words(name):
                    add(w, f"Preset: {name}")
        except Exception:
            log.exception("library words")
        safe_map = {core.safe_name(v[0]): v[0] for v in words.values()}
        # pictures on disk
        pics, seen, seen_ai = [], set(), set()
        try:
            for d in core._icon_dirs():
                try:
                    files = sorted(Path(d).iterdir())
                except Exception:
                    continue
                for f in files:
                    if f.suffix.lower() not in (".png", ".jpg", ".jpeg") or not f.is_file():
                        continue
                    key = str(f.resolve()).lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    wd = self._lib_word_from_file(f, safe_map)
                    if core.is_ai_picture(f):            # v13.23: AI pictures are labelled AI
                        if f.name.lower() in seen_ai:    # (mirror copy of the same file)
                            continue
                        seen_ai.add(f.name.lower())
                        pics.append({"path": f, "word": wd, "sub": "AI"})
                        continue
                    m = re.search(r"_arasaac_(\d+)$", f.stem, flags=re.I)
                    pics.append({"path": f, "word": wd,
                                 "sub": f"ARASAAC {m.group(1)}" if m else ("saved" if Path(d) == core.ICON_DIR else "built-in")})
        except Exception:
            log.exception("library pictures")
        pics.sort(key=lambda it: (it["word"].lower(), it["path"].name.lower()))
        for it in pics:
            if it["sub"] != "AI":
                add(it["word"], "Saved pictures")
        self._lib_pics = pics
        # Store as (word, sources_str) — sorting/grouping happens in _lib_filter
        self._lib_all_words = [(v[0], ", ".join(v[1])) for v in words.values()]
        self._lib_sel = None
        self._lib_fp = self._lib_fingerprint()
        self._lib_filter(keep)

    def _lib_sort_words(self, items):
        """Sort / group word tiles. Returns a list of either
        ('header', title) or ('word', word, sources)."""
        sort = (self.lib_sort.get() if hasattr(self, "lib_sort") else "By theme") or "By theme"
        if sort == "✨ AI only":                      # v13.23
            items = sorted([t for t in items if "AI" in [x.strip() for x in t[1].split(",")]],
                           key=lambda t: t[0].lower())
            return [("word", w, s) for w, s in items]
        if sort == "A → Z":
            items = sorted(items, key=lambda t: t[0].lower())
            return [("word", w, s) for w, s in items]
        if sort == "Z → A":
            items = sorted(items, key=lambda t: t[0].lower(), reverse=True)
            return [("word", w, s) for w, s in items]
        if sort == "By source":
            # Group by first listed source
            buckets = {}
            for w, s in items:
                src = (s.split(",")[0].strip() if s else "Other") or "Other"
                buckets.setdefault(src, []).append((w, s))
            out = []
            for src in sorted(buckets.keys(), key=lambda k: (k != "AI", k.lower())):   # AI first
                out.append(("header", "✨ AI" if src == "AI" else src))
                for w, s in sorted(buckets[src], key=lambda t: t[0].lower()):
                    out.append(("word", w, s))
            return out
        # Default: By theme (Fitzgerald / AAC category)
        order = list(aac_boards.CATEGORIES.keys())  # People, Actions, … Uncategorized
        buckets = {c: [] for c in order}
        for w, s in items:
            cat = aac_boards.category_for(w)
            if cat not in buckets:
                cat = "Uncategorized"
            buckets[cat].append((w, s))
        out = []
        for cat in order:
            group = buckets.get(cat) or []
            if not group:
                continue
            out.append(("header", cat))
            for w, s in sorted(group, key=lambda t: t[0].lower()):
                out.append(("word", w, s))
        return out

    def _lib_filter(self, keep=False):
        q = (self.lib_search.get() or "").strip().lower()
        filtered = [t for t in self._lib_all_words if not q or q in t[0].lower() or q in t[1].lower()]
        # Flat list for selection index math; headers are separate in _lib_rows
        self._lib_rows = self._lib_sort_words(filtered)
        self._lib_words = [(w, s) for kind, *rest in self._lib_rows
                           if kind == "word" for w, s in [rest]]
        pics_filtered = [it for it in self._lib_pics if not q or q in it["word"].lower()]
        self._lib_pic_rows = self._lib_sort_pics(pics_filtered)
        # Flat list for selection index math
        self._lib_pics_f = [it for kind, *rest in self._lib_pic_rows
                            if kind == "pic" for it in [rest[0]]]
        if self.lib_mode.get() == "Words":
            frac = self.lib_wcanvas.yview()[0] if keep else 0
            self._lib_wsel = set()
            self._lib_wanchor = None
            self._lib_wlayout()
            self.lib_wcanvas.yview_moveto(frac)
            self._lib_wrender()
            self.lib_count.configure(text=f"{len(self._lib_words):,} words")
        else:
            self._lib_sel = None
            self.lib_canvas.yview_moveto(0)
            self._lib_layout()
            self.lib_count.configure(text=f"{len(self._lib_pics_f):,} pictures")

    def _lib_sort_pics(self, items):
        """Sort / group picture tiles the same way as words.
        Returns list of ('header', title) or ('pic', item_dict)."""
        sort = (self.lib_sort.get() if hasattr(self, "lib_sort") else "By theme") or "By theme"
        if sort == "✨ AI only":                      # v13.23
            items = sorted([it for it in items if it.get("sub") == "AI"], key=lambda it: it["word"].lower())
            return [("pic", it) for it in items]
        if sort == "A → Z":
            items = sorted(items, key=lambda it: it["word"].lower())
            return [("pic", it) for it in items]
        if sort == "Z → A":
            items = sorted(items, key=lambda it: it["word"].lower(), reverse=True)
            return [("pic", it) for it in items]
        if sort == "By source":
            buckets = {}
            for it in items:
                src = (it.get("sub") or "Other").strip() or "Other"
                buckets.setdefault(src, []).append(it)
            out = []
            for src in sorted(buckets.keys(), key=lambda k: (k != "AI", k.lower())):   # AI first
                out.append(("header", "✨ AI" if src == "AI" else src))
                for it in sorted(buckets[src], key=lambda x: x["word"].lower()):
                    out.append(("pic", it))
            return out
        # Default: By theme
        order = list(aac_boards.CATEGORIES.keys())
        buckets = {c: [] for c in order}
        for it in items:
            cat = aac_boards.category_for(it["word"])
            if cat not in buckets:
                cat = "Uncategorized"
            buckets[cat].append(it)
        out = []
        for cat in order:
            group = buckets.get(cat) or []
            if not group:
                continue
            out.append(("header", cat))
            for it in sorted(group, key=lambda x: x["word"].lower()):
                out.append(("pic", it))
        return out

    def _lib_set_mode(self, mode):
        if mode == "Words":
            self.lib_pics_frame.pack_forget()
            self.lib_words_frame.pack(fill="both", expand=True)
        else:
            self.lib_words_frame.pack_forget()
            self.lib_pics_frame.pack(fill="both", expand=True)
        self._lib_filter()

    # ---- words view (tiles)
    WT_W, WT_H = 176, 66
    WT_HDR_H = 28  # height of a theme/source section header

    def _lib_row_layout(self):
        """Build (y0, y1, kind, data…) for each visual row so headers span full width
        and word tiles sit in a multi-column grid under each section."""
        rows = getattr(self, "_lib_rows", None) or []
        cols = max(1, self._lib_wcols)
        layout, y, wi = [], 0, 0  # wi = index into flat _lib_words
        col = 0
        for item in rows:
            if item[0] == "header":
                if col:
                    y += self.WT_H  # finish partial row of tiles
                    col = 0
                layout.append((y, y + self.WT_HDR_H, "header", item[1]))
                y += self.WT_HDR_H
            else:
                # word tile
                if col == 0:
                    row_y = y
                layout.append((row_y if col else y, (row_y if col else y) + self.WT_H,
                               "word", wi, item[1], item[2], col))
                wi += 1
                col += 1
                if col >= cols:
                    y += self.WT_H
                    col = 0
        if col:
            y += self.WT_H
        self._lib_row_geom = layout
        return y

    def _lib_wlayout(self):
        c = self.lib_wcanvas
        w = max(c.winfo_width(), 200)
        self._lib_wcols = max(1, w // self.WT_W)
        total_h = self._lib_row_layout()
        c.configure(scrollregion=(0, 0, w, total_h + 10))
        self._lib_wrender()

    def _lib_wyview(self, *a):
        self.lib_wcanvas.yview(*a)
        self._lib_wrender()

    def _lib_wwheel(self, e, direction=None):
        step = direction if direction is not None else (1 if e.delta > 0 else -1)
        self.lib_wcanvas.yview_scroll(-step * 3, "units")
        self._lib_wrender()

    def _lib_wrender(self):
        c = self.lib_wcanvas
        c.delete("all")
        geom = getattr(self, "_lib_row_geom", None) or []
        if not geom:
            c.create_text(max(c.winfo_width(), 200) / 2, 60, fill="gray", font=("Segoe UI", 13),
                          text="No words match.")
            return
        tw, th = self.WT_W, self.WT_H
        top = c.canvasy(0)
        bot = top + max(c.winfo_height(), 50)
        dark = ctk.get_appearance_mode() == "Dark"
        tile, tile_o = ("#383c44", "#4a4f59") if dark else ("#e2e6ee", "#c3c9d6")
        txt, sub = ("#f1f1f1", "#9aa0ab") if dark else ("#1c1c1c", "#6b7280")
        hdr_bg = "#2a2e36" if dark else "#d8dce6"
        hdr_fg = "#9ec5ff" if dark else "#1a4a8a"
        canvas_w = max(c.winfo_width(), 200)
        for entry in geom:
            y0, y1 = entry[0], entry[1]
            if y1 < top - th or y0 > bot + th:
                continue
            if entry[2] == "header":
                title = entry[3]
                # category colour bar when sorting by theme
                cat_color = aac_boards.CATEGORIES.get(title)
                c.create_rectangle(4, y0 + 2, canvas_w - 8, y1 - 2,
                                   fill=hdr_bg, outline="")
                if cat_color:
                    c.create_rectangle(4, y0 + 2, 10, y1 - 2, fill=cat_color, outline="")
                c.create_text(18, (y0 + y1) / 2, text=title, fill=hdr_fg, anchor="w",
                              font=("Segoe UI", 11, "bold"))
            else:
                # word tile: entry = (y0, y1, "word", wi, word, srcs, col)
                wi, word, srcs, col = entry[3], entry[4], entry[5], entry[6]
                x = col * tw
                y = y0
                sel = wi in self._lib_wsel
                is_ai = "AI" in [x.strip() for x in srcs.split(",")]
                c.create_rectangle(x + 5, y + 5, x + tw - 5, y + th - 5,
                                   fill="#4ECDC4" if sel else tile,
                                   outline="#6bcb77" if sel else ("#9b6bdc" if is_ai else tile_o),
                                   width=2 if (sel or is_ai) else 1)
                label = word if len(word) <= 17 else word[:16] + "…"
                c.create_text(x + tw / 2, y + 27, text=label, fill="white" if sel else txt,
                              font=("Segoe UI", 13, "bold"))
                parts = srcs.split(", ")
                if parts[0] == "AI":
                    parts[0] = "✨ AI"
                note = parts[0] if len(parts) == 1 else f"{parts[0]} +{len(parts) - 1}"
                note = note if len(note) <= 24 else note[:23] + "…"
                c.create_text(x + tw / 2, y + 47, text=note, fill="#cfe8d5" if sel else sub,
                              font=("Segoe UI", 8))

    def _lib_word_index_at(self, event):
        c = self.lib_wcanvas
        x = c.canvasx(event.x)
        y = c.canvasy(event.y)
        for entry in getattr(self, "_lib_row_geom", []) or []:
            y0, y1 = entry[0], entry[1]
            if y < y0 or y >= y1:
                continue
            if entry[2] != "word":
                return None
            wi, col = entry[3], entry[6]
            x0, x1 = col * self.WT_W, (col + 1) * self.WT_W
            if x0 <= x < x1:
                return wi
        return None

    def _lib_word_click(self, event, mod=None):
        self.lib_wcanvas.focus_set()
        i = self._lib_word_index_at(event)
        if i is None:
            if not mod:
                self._lib_wsel = set()
        elif mod == "ctrl":
            self._lib_wsel ^= {i}
            self._lib_wanchor = i
        elif mod == "shift" and self._lib_wanchor is not None:
            a, b = sorted((self._lib_wanchor, i))
            self._lib_wsel = set(range(a, b + 1))
        else:
            self._lib_wsel = {i}
            self._lib_wanchor = i
        self._lib_wrender()

    def _lib_selected_words(self):
        return [{"word": self._lib_words[i][0], "path": None}
                for i in sorted(self._lib_wsel) if i < len(self._lib_words)]

    def _lib_word_context(self, event):
        i = self._lib_word_index_at(event)
        if i is None:
            return "break"
        if i not in self._lib_wsel:
            self._lib_wsel = {i}
            self._lib_wanchor = i
            self._lib_wrender()
        self._lib_popup(event, self._lib_selected_words())
        return "break"

    def _lib_word_dbl(self, event=None):
        i = self._lib_word_index_at(event)
        if i is None:
            return "break"
        self._lib_wsel = {i}
        self._lib_wanchor = i
        self._lib_wrender()
        self._lib_send(self._lib_selected_words(), self._last_studio)
        return "break"

    # ---- pictures view (virtual grid, same theme/sort headers as Words)
    def _lib_pic_row_layout(self):
        """Build geometry for picture rows (headers + multi-column tiles)."""
        rows = getattr(self, "_lib_pic_rows", None) or []
        cols = max(1, self._lib_cols)
        layout, y, pi = [], 0, 0
        col = 0
        for item in rows:
            if item[0] == "header":
                if col:
                    y += self.LIB_CELL_H
                    col = 0
                layout.append((y, y + self.WT_HDR_H, "header", item[1]))
                y += self.WT_HDR_H
            else:
                if col == 0:
                    row_y = y
                layout.append((row_y if col else y, (row_y if col else y) + self.LIB_CELL_H,
                               "pic", pi, item[1], col))
                pi += 1
                col += 1
                if col >= cols:
                    y += self.LIB_CELL_H
                    col = 0
        if col:
            y += self.LIB_CELL_H
        self._lib_pic_geom = layout
        return y

    def _lib_layout(self):
        c = self.lib_canvas
        w = max(c.winfo_width(), 200)
        self._lib_cols = max(1, w // self.LIB_CELL_W)
        total_h = self._lib_pic_row_layout()
        c.configure(scrollregion=(0, 0, w, total_h + 10))
        self._lib_render()

    def _lib_yview(self, *a):
        self.lib_canvas.yview(*a)
        self._lib_render()

    def _lib_wheel(self, e, direction=None):
        step = direction if direction is not None else (1 if e.delta > 0 else -1)
        self.lib_canvas.yview_scroll(-step * 3, "units")
        self._lib_render()

    def _lib_render(self):
        """Draw only the thumbnails that are on screen; load the rest in the background."""
        c = self.lib_canvas
        c.delete("all")
        geom = getattr(self, "_lib_pic_geom", None) or []
        if not geom:
            c.create_text(max(c.winfo_width(), 200) / 2, 60, fill="gray", font=("Segoe UI", 13),
                          text="No saved pictures yet — find some in Maker Studio or PDF Studio.")
            return
        cw, ch = self.LIB_CELL_W, self.LIB_CELL_H
        top = c.canvasy(0)
        bot = top + max(c.winfo_height(), 50)
        dark = ctk.get_appearance_mode() == "Dark"
        txt, sub = ("#e6e6e6", "#888") if dark else ("#222", "#777")
        hdr_bg = "#2a2e36" if dark else "#d8dce6"
        hdr_fg = "#9ec5ff" if dark else "#1a4a8a"
        canvas_w = max(c.winfo_width(), 200)
        visible = set()
        self._lib_visible = visible   # live set: the worker skips thumbnails that scrolled away
        for entry in geom:
            y0, y1 = entry[0], entry[1]
            if y1 < top - ch or y0 > bot + ch:
                continue
            if entry[2] == "header":
                title = entry[3]
                cat_color = aac_boards.CATEGORIES.get(title)
                c.create_rectangle(4, y0 + 2, canvas_w - 8, y1 - 2, fill=hdr_bg, outline="")
                if cat_color:
                    c.create_rectangle(4, y0 + 2, 10, y1 - 2, fill=cat_color, outline="")
                c.create_text(18, (y0 + y1) / 2, text=title, fill=hdr_fg, anchor="w",
                              font=("Segoe UI", 11, "bold"))
            else:
                # entry = (y0, y1, "pic", pi, it, col)
                pi, it, col = entry[3], entry[4], entry[5]
                x, y = col * cw, y0
                if pi == self._lib_sel:
                    c.create_rectangle(x + 3, y + 3, x + cw - 3, y + ch - 3,
                                       fill="#4ECDC4", outline="#6bcb77", width=2)
                elif it["sub"] == "AI":      # v13.23: AI pictures get a purple frame + label
                    c.create_rectangle(x + 3, y + 3, x + cw - 3, y + ch - 3,
                                       fill="", outline="#9b6bdc", width=2)
                key = str(it["path"])
                visible.add(key)
                photo = self._lib_thumbs.get(key)
                if photo is not None:
                    self._lib_thumbs.move_to_end(key)
                    c.create_image(x + cw / 2, y + 62, image=photo)
                else:
                    c.create_rectangle(x + 14, y + 12, x + cw - 14, y + 112, outline="#555", dash=(2, 3))
                    c.create_text(x + cw / 2, y + 62, text="…", fill="gray", font=("Segoe UI", 16))
                    self._lib_request_thumb(key)
                label = it["word"] if len(it["word"]) <= 16 else it["word"][:15] + "…"
                c.create_text(x + cw / 2, y + 126, text=label,
                              fill="white" if pi == self._lib_sel else txt,
                              font=("Segoe UI", 10, "bold"))
                c.create_text(x + cw / 2, y + 140, text="✨ AI" if it["sub"] == "AI" else it["sub"],
                              fill="#cfe8d5" if pi == self._lib_sel else sub,
                              font=("Segoe UI", 8))

    def _lib_request_thumb(self, key):
        if key in self._lib_requested:
            return
        self._lib_requested.add(key)
        self._lib_req_q.put(key)
        if not self._lib_worker or not self._lib_worker.is_alive():
            self._lib_worker = threading.Thread(target=self._lib_thumb_worker, daemon=True)
            self._lib_worker.start()

    def _lib_thumb_worker(self):
        """Loads thumbnails newest-request-first; skips ones scrolled out of view."""
        try:
            from PIL import Image
        except Exception:
            return
        while True:
            try:
                key = self._lib_req_q.get(timeout=30)
            except queue.Empty:
                return
            if key not in self._lib_visible:
                self._lib_requested.discard(key)
                continue
            try:
                im = Image.open(key).convert("RGBA")
                im.thumbnail((104, 104))
                bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                self.q.put(("lib_thumb", key, Image.alpha_composite(bg, im).convert("RGB")))
            except Exception:
                self.q.put(("lib_thumb", key, None))

    def _lib_thumb_arrived(self, key, pil):
        self._lib_requested.discard(key)
        try:
            from PIL import ImageTk
            if pil is not None:
                self._lib_thumbs[key] = ImageTk.PhotoImage(pil)
                while len(self._lib_thumbs) > 500:
                    self._lib_thumbs.popitem(last=False)
        except Exception:
            return
        if self._lib_open and self.lib_mode.get() == "Pictures" and not self._lib_render_after:
            self._lib_render_after = self.after(40, self._lib_render_now)

    def _lib_render_now(self):
        self._lib_render_after = None
        if self._lib_open and self.lib_mode.get() == "Pictures":
            self._lib_render()

    def _lib_index_at(self, event):
        c = self.lib_canvas
        x = c.canvasx(event.x)
        y = c.canvasy(event.y)
        for entry in getattr(self, "_lib_pic_geom", []) or []:
            y0, y1 = entry[0], entry[1]
            if y < y0 or y >= y1:
                continue
            if entry[2] != "pic":
                return None
            pi, col = entry[3], entry[5]
            x0, x1 = col * self.LIB_CELL_W, (col + 1) * self.LIB_CELL_W
            if x0 <= x < x1:
                return pi
        return None

    def _lib_pic_click(self, event):
        self.lib_canvas.focus_set()
        self._lib_sel = self._lib_index_at(event)
        self._lib_render()

    def _lib_pic_dbl(self, event):
        i = self._lib_index_at(event)
        if i is not None:
            self._lib_sel = i
            self._lib_send([self._lib_pic_item(i)], self._last_studio)

    def _lib_pic_context(self, event):
        i = self._lib_index_at(event)
        if i is None:
            return "break"
        self._lib_sel = i
        self._lib_render()
        self._lib_popup(event, [self._lib_pic_item(i)])
        return "break"

    def _lib_pic_item(self, i):
        it = self._lib_pics_f[i]
        return {"word": it["word"], "path": str(it["path"])}

    # ---- right-click menu + sending
    def _lib_maker_current_label(self):
        """What the highlighted Maker tag is called right now (or None)."""
        sel = getattr(self, "_selected_multi", None)
        if sel:
            return sel
        cur = (self.preview_word.get() or "").strip()
        if cur and cur != "-":
            return cur
        try:
            cur = (self.maker_word.get() or "").strip()
        except Exception:
            cur = ""
        return cur or None


    def _lib_popup(self, event, items):
        """v15.0.1: right-click offers Current highlighted tag/card OR Create new
        for both Maker Studio and PDF Studio (pictures and words)."""
        if not items:
            return
        pic = bool(items[0].get("path"))
        noun = "picture" if pic else ("words" if len(items) > 1 else "word")
        m = tk.Menu(self, tearoff=0)

        maker_cur = self._lib_maker_current_label()
        m_maker = tk.Menu(m, tearoff=0)
        if maker_cur:
            m_maker.add_command(
                label=f"Current highlighted tag  (\u201c{maker_cur}\u201d)",
                command=lambda: self._lib_send(items, "3d", mode="current"))
        else:
            m_maker.add_command(
                label="Current highlighted tag  (none selected)",
                state="disabled")
        m_maker.add_command(
            label="Create new tag",
            command=lambda: self._lib_send(items, "3d", mode="new"))
        m.add_cascade(label=f"Send {noun} to 3D tag (Maker Studio)", menu=m_maker)

        # v15.14: sending to PDF Studio places a movable object on the
        # page — it is not a card tile any more, so there is no
        # current-card / new-card choice to offer.
        m.add_command(
            label=f"Place {noun} as an object in PDF Studio",
            command=lambda: self._lib_send(items, "pdf", mode="auto"))

        m.add_separator()
        if pic:
            m.add_command(label="Show file in folder", command=lambda: reveal(items[0]["path"]))
        else:
            m.add_command(label="Add to my current word list", command=lambda: self._lib_add_to_list(items))
        self._add_generate_entry(m, [it["word"] for it in items])
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _lib_add_to_list(self, items):
        words = self.get_words()
        have = {w.lower() for w in words}
        n = 0
        for it in items:
            if it["word"].lower() not in have:
                words.append(it["word"])
                have.add(it["word"].lower())
                n += 1
        if n:
            self.set_words(words)
            self._lib_reload()
        self.flash_status(f"Added {n} word(s) to your current word list")

    def _lib_send(self, items, target, mode="auto"):
        """Send words / a picture to Maker (3d) or PDF Studio.

        mode: "current" | "new" | "auto" (prefer current when highlighted).
        """
        sent, actions = [], []
        for it in items:
            w, path = it["word"], it.get("path")
            try:
                if target == "pdf":
                    got, how = self._lib_send_pdf(w, path, mode=mode)
                else:
                    got, how = self._lib_send_3d(w, path, mode=mode)
                if got:
                    sent.append(got)
                if how:
                    actions.append(how)
            except Exception:
                log.exception("library send")
        if not sent:
            return
        names = ", ".join(f"\u201c{w}\u201d" for w in sent[:3]) + (
            f" +{len(sent) - 3} more" if len(sent) > 3 else "")
        how = actions[0] if actions else ""
        if items[0].get("path"):
            msg = f"\u2714 Picture \u2192 {names} in {self._studio_name(target)}" + (
                f" ({how})" if how else "")
        else:
            msg = f"\u2714 Word {names} \u2192 {self._studio_name(target)}" + (
                f" ({how})" if how else "")
        self.flash_status(msg)
        try:
            self.lib_hint.configure(text=msg, text_color="#6bcb77")
            self.after(3500, lambda: (self.lib_hint.configure(text_color="gray"),
                                      self._lib_update_hint()))
        except Exception:
            pass

    def _lib_ensure_word_in_list(self, word):
        """Add word to Word Lists if missing; return canonical form."""
        words = self.get_words()
        canon = next((x for x in words if x.lower() == word.lower()), None)
        if canon is None:
            words.append(word)
            self.set_words(words)
            canon = word
        return canon


    def _lib_send_3d(self, word, path, mode="auto"):
        """Send to Maker Studio. Returns (result_word, how_description).

        v15.59: a send to a HIGHLIGHTED tag replaces the tag whole —
        word and picture together. With no highlighted tag, the old
        rules hold: picture-only → change the image on the current
        word, leave the word alone; word-only → change the word,
        leave the picture alone. Blank/new → may fill word and
        picture together (new empty tag).
        """
        highlighted = self._lib_maker_current_label()
        if mode == "auto":
            mode = "current" if highlighted else "new"

        if mode == "current":
            cur = highlighted
            if not cur:
                self.flash_status(
                    "No tag is highlighted in Maker Studio \u2014 pick one, or choose Create new tag",
                    ok=False)
                return None, None
            # v15.59: a send to a HIGHLIGHTED tag replaces the tag whole —
            # word and picture together. (With no highlighted tag on the
            # preview page, the old picture-only / word-only behaviour
            # below is kept.)
            sel = getattr(self, "_selected_multi", None)
            if (sel and getattr(self, "_maker_work_words", None) is not None
                    and sel in self._maker_work_words):
                canon = self._lib_ensure_word_in_list(word) if word else sel
                got = self._maker_replace_highlighted(canon, picture_path=path)
                if got is not None:
                    self._tag_stale = True
                    self.changed()
                    try:
                        self.on_preview_word()
                    except Exception:
                        pass
                    if path and canon != sel:
                        return got, "word + picture on highlighted tag"
                    if path:
                        return got, "picture only on current tag"
                    return got, "highlighted tag replaced"
            if path:
                # Picture only — do not change the word on this tag
                self.overrides[cur] = str(path)
                self._no_auto_pic.discard(cur)
                if getattr(self, "_multi_geo", None) is not None:
                    self._multi_geo.pop(cur, None)
                self._tag_stale = True
                self.changed()
                try:
                    self.on_preview_word()
                except Exception:
                    pass
                return cur, "picture only on current tag"
            # Word only, no highlighted tag — do not attach or change the picture
            canon = self._lib_ensure_word_in_list(word)
            self._no_auto_pic.add(canon)  # keep existing picture policy; no auto lookup
            self.maker_word.set(canon)
            self.preview_word.set(canon)
            self._preview_word_explicit = True
            try:
                self.word_menu.set(canon)
            except Exception:
                pass
            self._tag_stale = True
            self.changed()
            try:
                self.on_preview_word()
            except Exception:
                pass
            return canon, "word only on current tag"

        # mode == "new" — empty new tag: word, and picture only if one was sent
        canon = self._lib_ensure_word_in_list(word)
        if path:
            self.overrides[canon] = str(path)
            self._no_auto_pic.discard(canon)
            how = "new tag (word + picture)"
        else:
            self._no_auto_pic.add(canon)
            how = "new tag (word only)"
        try:
            if getattr(self, "maker_multi", None) is not None and self.maker_multi.get():
                self._maker_append_tags([canon])
        except Exception:
            log.exception("library send new maker tag")
        self.maker_word.set(canon)
        self.preview_word.set(canon)
        self._preview_word_explicit = True
        try:
            self.word_menu.set(canon)
        except Exception:
            pass
        self._tag_stale = True
        try:
            self.on_preview_word()
        except Exception:
            pass
        self.changed()
        return canon, how

    def _lib_send_pdf(self, word, path, mode="auto"):
        """Send to PDF Studio. Returns (result_word, how_description).

        v15.14: a send is now a movable OBJECT on the editor page, never a
        tile.  Before, this wrote to the old _pdf_words card list, whose
        tiles are hidden once the editor opens (v15.6: the editor IS PDF
        Studio), and the new card only surfaced if the document was still
        pristine when you next opened PDF Studio — so in normal use a send
        simply vanished.  Now the word (and its picture, if one was sent)
        lands as a free card object in the middle of the current page,
        already selected so it can be dragged anywhere at once.
        mode is ignored: "current card" and "new card" both meant tiles.
        """
        word = (word or "").strip()
        if not word:
            return None, None
        canon = self._lib_ensure_word_in_list(word)
        if path:
            # The sent picture is this word's picture for the object.
            self.overrides[canon] = str(path)
            self._no_auto_pic.discard(canon)
        try:
            # _ped_active means "an editor session exists", not "we are on
            # the PDF tab" — check the tab itself, or the object lands on a
            # page the user is not looking at.
            if getattr(self, "tabs", None) is not None and self.tabs.get() != "PDF Studio":
                self.goto_tab("PDF Studio")
        except Exception:
            pass
        try:
            placed = self._ped_place_sent(canon)
        except Exception:
            placed = False
        if not placed:
            self.flash_status("Could not place that in PDF Studio \u2014 open PDF Studio and try again", ok=False)
            return None, None
        page_no = (getattr(self, "_ped_cur", 0) or 0) + 1
        how = f"object on page {page_no}" + (" (word + picture)" if path else " (word only)")
        return canon, how


    # ============================================================ widgets

    # ============================================================ AI

    def refresh_ai_folder_label(self):
        """Show what's in local_ai/ (models + llama.cpp / llama-b* builds)."""
        try:
            info = ai.scan_local_ai_dir()
            bits = []
            if info["models"]:
                bits.append(", ".join(f.name for f in info["models"][:2]))
            if info.get("llama_cli"):
                cli_path = str(info["llama_cli"])
                if "llama-b11575" in cli_path:
                    bits.append("engine: llama.cpp b11575")
                else:
                    bits.append(f"llama: {info['llama_cli'].name}")
            elif info.get("llama_build"):
                bits.append(f"build: {info['llama_build']}")
            try:
                sd = ai.scan_sd()
                if sd["cli"] and sd["model"]:
                    bits.append("SD Turbo: ready")
                elif sd["model"]:
                    bits.append("SD Turbo: model in, engine missing")
                elif sd["cli"]:
                    bits.append("SD Turbo: engine in, model missing")
            except Exception:
                pass
            bad = info.get("damaged") or []
            if bad:      # v13.23: say it loudly — this is what made every AI call fail
                warn = "  ⚠ BROKEN (incomplete download — click its Download button again): " + \
                       ", ".join(f"{f.name} ({f.stat().st_size // 1_000_000} MB)" for f, _w in bad)
                self.ai_folder_label.configure(
                    text="local_ai/: " + (" · ".join(bits) if bits else "no working model") + warn,
                    text_color="#e07a5f")
            elif bits:
                self.ai_folder_label.configure(text="local_ai/: " + " · ".join(bits),
                                               text_color="#6bcb77")
            else:
                self.ai_folder_label.configure(
                    text="local_ai/: empty — download a model or drop in .gguf / llama-b*",
                    text_color="gray")
        except Exception:
            pass

    def open_local_ai_folder(self):
        try:
            ai.LOCAL_AI_DIR.mkdir(parents=True, exist_ok=True)
            open_folder(ai.LOCAL_AI_DIR)
        except Exception as e:
            messagebox.showinfo(APP_NAME, f"Could not open the folder:\n{e}")

    def _finish_gguf_ready(self, dest, note=""):
        """Mark local GGUF ready and refresh UI (called on main thread)."""
        ai.cfg.backend = "local"
        # v15.0: single text model (Qwen 3B) for both roles
        m = ai.find_model("smart") or dest
        ai.cfg.fast_model = str(m)
        ai.cfg.smart_model = str(m)
        ai.cfg.local_ready = True
        ai.cfg.save()
        self.install_ai_hooks()
        self.refresh_ai_status()
        self.refresh_ai_folder_label()
        extra = f"\n{note}" if note else ""
        name = Path(dest).name
        self.ai_say(f"✔ Qwen 3B is ready.\nModel: {name}{extra}\n"
                    "Text AI runs on this PC. Next: download SD Turbo for pictures.")
        try:
            messagebox.showinfo(
                APP_NAME,
                f"Qwen 3B is ready.\n\n"
                f"Model: {name}\n\n"
                f"Text help and word lists will use this model on your PC.\n"
                f"Next step: download SD Turbo (Image AI) for real pictures.")
        except Exception:
            pass

    def _make_ai_progress(self, say_steps=False):
        """A thread-safe, THROTTLED progress callback for downloads/setup (v14.8).

        Before, every download tick created a new chat bubble (thousands of widgets for one
        1 GB model) - the window froze and crashed. Now: percent updates only change the
        small status line, at most ~2x a second; real step messages (no percent) become a
        chat bubble once when say_steps is on; repeats are dropped."""
        state = {"t": 0.0, "last": ""}
        pct = re.compile(r"\d+\s*%|\d+ MB")

        def progress(msg):
            m = str(msg)
            if m == state["last"]:
                return
            is_pct = bool(pct.search(m))
            now = time.time()
            if is_pct and now - state["t"] < 0.4:
                return
            state["t"], state["last"] = now, m

            def apply():
                try:
                    self.ai_status.configure(text=m[:70], text_color="#4c9aff")
                    if say_steps and not is_pct:
                        self.ai_say(m)
                except Exception:
                    pass
            self.ui_call(apply)
        return progress

    def download_local_ai_model(self):
        """One-click GGUF download: Qwen2.5-3B-Instruct (~2.1 GB)."""
        if getattr(self, "_ai_dl_running", False):
            return
        self._ai_dl_running = True
        self.ai_say("Downloading Qwen2.5-3B Instruct (~2.1 GB) into local_ai/…\n"
                    "You’ll get an alert when it’s ready.")
        self.ai_status.configure(text="⏳ Downloading Qwen 3B…", text_color="#4c9aff")

        progress = self._make_ai_progress()

        def work():
            try:
                dest = ai.download_gguf_model(progress=progress)
                engine = None
                try:
                    engine = ai.ensure_llama_cpp(progress=progress)
                except Exception as eng_err:
                    # v14.8: the model IS saved. Don't silently download + install the huge
                    # Ollama app as a "fix" (that was the long freeze / surprise installer) -
                    # use Ollama only if it's already running, otherwise explain the fix.
                    log.warning("ensure_llama_cpp after download: %s", eng_err)
                    if ai.ollama_alive():
                        engine = "ollama"
                    else:
                        raise eng_err
                note = ""
                if engine == "cli":
                    note = "Using llama-cli binary from local_ai/."
                elif engine == "ollama":
                    note = "GGUF saved; runtime is Ollama (python package unavailable)."
                def ok(d=dest, n=note):
                    self._ai_dl_running = False
                    self._finish_gguf_ready(d, n)
                self.ui_call(ok)
            except Exception as e:
                log.exception("download_local_ai_model")
                def bad(err=e):
                    self._ai_dl_running = False
                    self.ai_status.configure(text="○ Download failed", text_color="#e07a5f")
                    self.ai_say(f"Download / engine setup failed:\n{err}")
                self.ui_call(bad)
        threading.Thread(target=work, daemon=True).start()


    def download_llama_engine(self):
        """Download the llama.cpp b11575 engine package into local_ai/ (v13.14-exp)."""
        if getattr(self, "_ai_dl_running", False):
            return
        self._ai_dl_running = True
        self.ai_say(f"Downloading the llama.cpp engine ({ai.LLAMA_CPP_TAG}, Windows CPU x64) into local_ai/…")
        self.ai_status.configure(text="⏳ Downloading engine…", text_color="#4c9aff")

        progress = self._make_ai_progress()

        def work():
            try:
                cli = ai.download_llama_cpp(progress=progress)
                def ok(c=cli):
                    self._ai_dl_running = False
                    self.refresh_ai_folder_label()
                    self.ai_status.configure(text="● Engine ready", text_color="#6bcb77")
                    self.ai_say(f"Engine ready: {Path(c).name} (llama.cpp {ai.LLAMA_CPP_TAG}).\n"
                                "It runs the models in local_ai/ — no pip, no compiling.")
                self.ui_call(ok)
            except Exception as e:
                log.exception("download_llama_engine")
                def bad(err=e):
                    self._ai_dl_running = False
                    self.ai_status.configure(text="○ Engine download failed", text_color="#e07a5f")
                    self.ai_say(f"Engine download failed:\n{err}")
                self.ui_call(bad)
        threading.Thread(target=work, daemon=True).start()

    def ai_reset_all(self):
        """Advanced (v13.27): wipe local_ai/ completely, then download and
        reinstall the whole local AI stack — llama.cpp engine, both GGUF
        models, SD engine + SD Turbo model — from scratch."""
        if getattr(self, "_ai_setup_running", False) or self.busy:
            messagebox.showinfo(APP_NAME, "An AI task is already running — let it finish first.")
            return
        if not messagebox.askyesno(
                APP_NAME,
                "Reset local AI?\n\n"
                "This deletes everything in the local_ai folder (engine, both "
                "models, SD Turbo — about 7 GB) and downloads it all again.\n\n"
                "You need internet for the downloads. Your words, lists, "
                "pictures and settings are not touched."):
            return
        self._ai_setup_running = True
        # The status dot goes OFF the moment Reset starts (13.27 left the
        # old green "Local AI on" up for the whole run).
        try:
            self.ai_status.configure(text="○ Local AI off — resetting\u2026", text_color=("gray30", "gray75"))
        except Exception:
            pass

        progress = self._make_ai_progress(say_steps=True)

        def fn():
            res = ai.wipe_local_ai()
            removed, leftovers = res["removed"], res["leftovers"]
            progress(f"Removed {len(removed)} item(s) from local_ai/ — reinstalling everything…")
            if leftovers:
                progress("⚠ Couldn't delete: " + ", ".join(leftovers) +
                         " — a file may be open in another program.")
            # Show the off state NOW, before the reinstall begins (via
            # the app queue — after() from this worker thread can throw)
            self.q.put(("ai_refresh",))
            steps = [
                ("llama.cpp engine", lambda: ai.download_llama_cpp(progress=progress)),
                ("Qwen 3B (text)", lambda: ai.download_gguf_model(progress=progress)),
                ("SD engine", lambda: ai.download_sd_engine(progress=progress)),
                ("SD Turbo model", lambda: ai.download_sd_model(progress=progress)),
            ]
            failed = []
            for name, step in steps:
                try:
                    step()
                except Exception as e:
                    failed.append(f"{name}: {e}")
                    progress(f"⚠ {name} failed — {e}")
            caps = ai.capabilities(probe=True)
            ai.cfg.backend = "local"
            ai.cfg.local_ready = bool(caps["text"])
            fast_m, smart_m = ai.find_model("fast"), ai.find_model("smart")
            if fast_m:
                ai.cfg.fast_model = str(fast_m)
            if smart_m:
                ai.cfg.smart_model = str(smart_m)
            ai.cfg.save()
            return {"caps": caps, "failed": failed, "leftovers": leftovers}

        def done(res):
            self._ai_setup_running = False
            caps = res["caps"]
            lines = ["Local AI reset complete."]
            if res.get("leftovers"):
                lines.append("⚠ Still in local_ai/ (couldn't be deleted — close other "
                             "programs using them, then Reset again): "
                             + ", ".join(res["leftovers"]))
            lines.append("Text AI: " + ("ready (" + ", ".join(p.name for p in caps["models"]) + ")"
                                         if caps["text"] else "NOT ready"))
            lines.append("Images (SD Turbo): " + ("ready" if caps["image"] else "not installed"))
            if res["failed"]:
                lines.append("")
                lines.append("These downloads failed (check your internet, then use the Download buttons):")
                for f in res["failed"]:
                    lines.append("  • " + f)
            self.ai_say("\n".join(lines))
            try:
                self.refresh_ai_folder_label()
                self.refresh_ai_status()
            except Exception:
                pass
            bad = bool(res["failed"] or res.get("leftovers"))
            self.flash_status("Local AI reset finished" + (" — with problems, see above" if bad else ""),
                              ok=not bad, hold=True)

        self.bg("Resetting local AI…", fn, done, lock=True)

    def download_sd_engine_gui(self):
        """Download the stable-diffusion.cpp engine into local_ai/sd_turbo/ (v13.21)."""
        if getattr(self, "_ai_dl_running", False):
            return
        self._ai_dl_running = True
        self.ai_say("Downloading the SD engine (stable-diffusion.cpp, Windows CPU x64) into local_ai/sd_turbo/…")
        self.ai_status.configure(text="⏳ Downloading SD engine…", text_color="#4c9aff")

        progress = self._make_ai_progress()

        def work():
            try:
                cli = ai.download_sd_engine(progress=progress)
                def ok(c=cli):
                    self._ai_dl_running = False
                    self.refresh_ai_folder_label()
                    self.ai_status.configure(text="● SD engine ready", text_color="#6bcb77")
                    self.ai_say(f"✔ SD engine ready: {Path(c).name} (stable-diffusion.cpp).\n"
                                "It runs the SD Turbo model — no pip, no compiling.")
                    try:
                        messagebox.showinfo(
                            APP_NAME,
                            f"SD engine is ready.\n\n"
                            f"File: {Path(c).name}\n\n"
                            f"If you haven’t already, download the SD Turbo model (~5.2 GB) next.")
                    except Exception:
                        pass
                self.ui_call(ok)
            except Exception as e:
                log.exception("download_sd_engine_gui")
                def bad(err=e):
                    self._ai_dl_running = False
                    self.ai_status.configure(text="○ SD engine download failed", text_color="#e07a5f")
                    self.ai_say(f"SD engine download failed:\n{err}")
                self.ui_call(bad)
        threading.Thread(target=work, daemon=True).start()

    def download_sd_model_gui(self):
        """Download the SD Turbo model (~5.2 GB) into local_ai/sd_turbo/ (v13.21)."""
        if getattr(self, "_ai_dl_running", False):
            return
        self._ai_dl_running = True
        self.ai_say("Downloading the SD Turbo model (~5.2 GB) into local_ai/sd_turbo/… this is the big one — let it finish.")
        self.ai_status.configure(text="⏳ Downloading SD Turbo model…", text_color="#4c9aff")

        progress = self._make_ai_progress()

        def work():
            try:
                dest = ai.download_sd_model(progress=progress)
                def ok(d=dest):
                    self._ai_dl_running = False
                    self.refresh_ai_folder_label()
                    self.ai_status.configure(text="● SD Turbo model ready", text_color="#6bcb77")
                    has_engine = bool(ai.scan_sd().get("cli"))
                    note = ""
                    if not has_engine:
                        note = "\nThe SD engine is still needed — click “SD engine” too."
                    self.ai_say(f"✔ SD Turbo model ready: {Path(d).name}.{note}")
                    try:
                        if has_engine:
                            messagebox.showinfo(
                                APP_NAME,
                                f"SD Turbo is ready.\n\n"
                                f"Model: {Path(d).name}\n"
                                f"Engine: installed\n\n"
                                f"You can create real pictures from the AI tab.")
                        else:
                            messagebox.showinfo(
                                APP_NAME,
                                f"SD Turbo model is ready.\n\n"
                                f"Model: {Path(d).name}\n\n"
                                f"Still needed: click “SD engine” (small download), "
                                f"then you’ll be fully set for pictures.")
                    except Exception:
                        pass
                self.ui_call(ok)
            except Exception as e:
                log.exception("download_sd_model_gui")
                def bad(err=e):
                    self._ai_dl_running = False
                    self.ai_status.configure(text="○ SD model download failed", text_color="#e07a5f")
                    self.ai_say(f"SD Turbo model download failed:\n{err}")
                self.ui_call(bad)
        threading.Thread(target=work, daemon=True).start()

    def _ai_picture_target(self):
        """Word rail Generate picture should act on (v15.70): the highlighted
        Maker tag first, else a word the user explicitly picked (tile, put,
        create, replace, apply, picture, library send). A preview word the
        app filled in by itself (Maker-enter auto-fill, list-change fallback)
        does NOT count — that phantom pick is what used to generate pictures
        for the preview set's first word (e.g. 'afraid') with nothing selected."""
        sel = getattr(self, "_selected_multi", None)
        if sel:
            return sel
        if getattr(self, "_preview_word_explicit", False):
            w = self.preview_word.get()
            if w and w != "-":
                return w
        return None

    def ai_sd_picture(self):
        """'✨ Generate picture' on the rail — generate (or regenerate) the current word's
        picture; same path as right-click → Generate an image.
        v15.34: uses the Picture style menu as-is (AAC pastel by default).
        It used to overwrite the menu with a simple legacy style first,
        which bypassed the AAC pastel builder and its subject dictionary.
        v15.70: acts only on a word the user actually picked (see _ai_picture_target)."""
        w = self._ai_picture_target()
        if not w:
            return self.ai_say("Pick a word first (Maker Studio / Word Lists), then click this again.")
        info = ai.scan_sd()
        if not info["cli"] or not info["model"]:
            # Fall back to simple shape/icon drawing if SD isn't ready
            backend = self._ai_image_backend()
            if backend == "draw":
                self.ai_generate_images([w], allow_shapes=True)
                return
            self._ai_banner("error", "image", "SD TURBO ISN'T FULLY INSTALLED",
                            "Download BOTH the SD engine and the SD Turbo model (left side).")
            self.ai_say("SD Turbo isn't fully installed yet.\n"
                        "In “Image AI — SD Turbo” (this tab, left side) download BOTH the SD engine "
                        "and the SD Turbo model, then click this again. Everything stays on this PC.\n"
                        "Until then, a text model can still draw simple icons.")
            return
        self.ai_generate_images([w])


    def setup_local_ai(self):
        """v3.12.0-exp / v13.25: probe real loadability. Never claim working
        just because a .gguf file is sitting in the folder."""
        if getattr(self, "_ai_setup_running", False):
            return
        self._ai_setup_running = True
        self.ai_status.configure(text="⏳ Checking local_ai folder…", text_color="#4c9aff")

        progress = self._make_ai_progress(say_steps=True)

        def work():
            try:
                info = ai.scan_local_ai_dir()
                # Ensure engine first so we can actually probe models
                try:
                    ai.ensure_llama_cpp(progress=progress)
                except Exception as eng_err:
                    log.warning("ensure_llama_cpp: %s", eng_err)
                    if not info.get("llama_cli"):
                        try:
                            progress("No engine yet — downloading llama.cpp…")
                            ai.download_llama_cpp(progress=progress)
                        except Exception as e:
                            log.warning("engine download failed: %s", e)

                if not info.get("models"):
                    try:
                        progress("No model yet — downloading the local AI stack…")
                        try:
                            ai.download_gguf_model(progress=progress)
                        except Exception as e:
                            log.warning("smart model download failed: %s", e)
                        try:
                            ai.download_fast_gguf(progress=progress)
                        except Exception as e:
                            log.warning("fast model download failed: %s", e)
                        info = ai.scan_local_ai_dir()
                    except Exception as e:
                        log.warning("stack download failed: %s", e)

                # PROBE every candidate — a file that fails to load is not "ready"
                progress("Testing which models actually load…")
                caps = ai.capabilities(probe=True)
                working = caps["models"]
                damaged = caps["damaged"]

                if working:
                    engine_note = ""
                    if caps.get("engine"):
                        engine_note = f"\nEngine: {Path(caps['engine']).name}"
                    fast_m = ai.find_model("fast")
                    smart_m = ai.find_model("smart")
                    work_names = {p.name.lower() for p in working}
                    if fast_m and fast_m.name.lower() not in work_names:
                        fast_m = working[0]
                    if smart_m and smart_m.name.lower() not in work_names:
                        smart_m = working[0]
                    lines = [
                        "Local AI is ready on this PC.",
                        "",
                        "Working model(s):",
                    ]
                    for p in working:
                        role = []
                        n = p.name.lower()
                        if "0.5b" in n or "0_5b" in n:
                            role.append("fast text")
                        if "coder" in n or "1.5b" in n or "1_5b" in n:
                            role.append("smart text + simple icon drawing")
                        if not role:
                            role.append("text")
                        lines.append(f"  • {p.name}  ({', '.join(role)})")
                    lines.append("")
                    if caps["image"]:
                        lines.append("Images: SD Turbo is installed — real picture generation is available.")
                    else:
                        lines.append(
                            "Images: not available yet.\n"
                            "  Text helpers and simple icon drawing will work.\n"
                            "  For photo-style pictures, download SD Turbo in the AI tab\n"
                            "  (Image AI section). Until then, Generate image uses simple drawing only if a text model works.")
                    if damaged:
                        lines.append("")
                        lines.append("Broken (skipped — re-download to fix):")
                        for p, why in damaged:
                            lines.append(f"  • {Path(p).name} — {why}")
                    if engine_note:
                        lines.append(engine_note)

                    def ok(msg="\n".join(lines), fm=fast_m, sm=smart_m, wk=working):
                        self._ai_setup_running = False
                        ai.cfg.backend = "local"
                        ai.cfg.fast_model = str(fm or wk[0])
                        ai.cfg.smart_model = str(sm or wk[0])
                        ai.cfg.local_ready = True
                        ai.cfg.save()
                        self.install_ai_hooks()
                        self.refresh_ai_status()
                        self.refresh_ai_folder_label()
                        self.ai_say(msg)
                    self.ui_call(ok)
                    return

                # Nothing loads
                ai.cfg.local_ready = False
                ai.cfg.save()
                bits = ["No working model found."]
                if damaged:
                    bits.append("")
                    bits.append("These files are present but broken (re-download them):")
                    for p, why in damaged:
                        bits.append(f"  • {Path(p).name} — {why}")
                bits.append("")
                bits.append(
                    "Click “1 · Download Qwen 3B” in the AI tab, then Set up local AI again.\n"
                    "Need real pictures? Also install SD Turbo under Image AI.")

                def none_ok(msg="\n".join(bits)):
                    self._ai_setup_running = False
                    self.install_ai_hooks()
                    self.refresh_ai_status()
                    self.refresh_ai_folder_label()
                    self.ai_status.configure(text="○ Local AI off — no working model", text_color="#e07a5f")
                    self.ai_say(msg)
                self.ui_call(none_ok)
            except Exception as e:
                log.exception("setup_local_ai")
                def bad(err=e):
                    self._ai_setup_running = False
                    self.ai_status.configure(text="○ Setup failed", text_color="#e07a5f")
                    self.ai_say(f"Setup failed:\n{err}")
                self.ui_call(bad)
        threading.Thread(target=work, daemon=True).start()

    def install_ai_hooks(self):
        c = ai.cfg

        def _with_status(fn, msg):
            def inner(*a, **k):
                core.report_status(msg)   # shown on the tag preview while the model works
                return fn(*a, **k)
            return inner
        core.AI_HOOKS["pick"] = (_with_status(ai.pick_candidate, "✨ Local AI is loading — choosing the best picture…")
                                 if (c.ready and c.pick_best) else None)
        def _draw_and_save(word, *a, **k):
            if core.ai_images_for(word):      # already drawn + saved: reuse silently
                return ai.draw_icon_geometry(word, *a, **k)
            self.q.put(("ai_banner", "busy", "image", "GENERATING IMAGE",
                        f"“{word}”  ·  Local AI drawing  ·  saves to Library → Pictures → AI"))
            try:
                geom = ai.draw_icon_geometry(word, *a, **k)
            except Exception:
                self.q.put(("ai_banner", "error", "image", "IMAGE GENERATION FAILED", f"“{word}” — see Log"))
                raise
            saved = None
            try:
                saved = self._save_ai_drawing_to_library(word, geom)
            except Exception as e:
                log.debug("AI drawing library save skipped for '%s': %s", word, e)
            self.q.put(("ai_banner", "ok", "image", "✔ IMAGE SAVED TO LIBRARY — AI LABEL",
                        f"“{word}”  →  Library → Pictures → AI" + (f"   ({Path(saved).name})" if saved else "")))
            return geom

        core.AI_HOOKS["draw"] = (_with_status(_draw_and_save,
                                              "✨ Local AI is loading & creating an AI image… (first time can take a minute)")
                                 if c.ready else None)
        core.AI_HOOKS["auto_draw"] = bool(c.ready and c.auto_draw)

    def _save_ai_drawing_to_library(self, word, geom, force=False):
        """Save an AI-drawn icon into the Library, labelled AI (<word>_ai.png) as the
        word's picture — searched first afterwards, like downloaded pictures.
        Unless force=True, a word that already has an AI picture isn't saved again
        (the tag is rebuilt often; that must not pile up copies).
        The drawing space is 100x100, origin top-left, y down. Returns the saved Path."""
        if geom is None or getattr(geom, "is_empty", True):
            return None
        if not force:
            have = core.ai_images_for(word)
            if have:
                return have[-1]
        import io
        from matplotlib.figure import Figure
        from matplotlib.patches import PathPatch
        from matplotlib.path import Path as MplPath
        from shapely.geometry import Polygon as ShPolygon
        polys = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
        fig = Figure(figsize=(5, 5), dpi=100)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, 100)
        ax.set_ylim(100, 0)
        ax.set_aspect("equal")
        ax.axis("off")
        for poly in polys:
            if not isinstance(poly, ShPolygon) or poly.is_empty:
                continue
            verts, codes = [], []
            for ring in [poly.exterior] + list(poly.interiors):
                pts = list(ring.coords)
                if len(pts) < 3:
                    continue
                verts.extend(pts)
                codes.extend([MplPath.MOVETO] + [MplPath.LINETO] * (len(pts) - 2) + [MplPath.CLOSEPOLY])
            if verts:
                ax.add_patch(PathPatch(MplPath(verts, codes), facecolor="black", edgecolor="none"))
        buf = io.BytesIO()
        fig.savefig(buf, format="png", transparent=True)
        out = core.save_ai_image(buf.getvalue(), word, how="Local AI drawing")
        return out

    def on_ai_switch(self):
        ai.cfg.pick_best = bool(self.sw_pick.get())
        ai.cfg.auto_draw = bool(self.sw_draw.get())
        ai.cfg.save()
        if (ai.cfg.pick_best or ai.cfg.auto_draw) and not ai.cfg.ready:
            self.setup_local_ai()
            return
        self.install_ai_hooks()
        self.changed()

    def refresh_ai_status(self):
        caps = ai.capabilities(probe=False)
        if caps["text"] and ai.cfg.ready:
            names = ", ".join(p.name for p in (caps["models"] or [])[:2]) or "model"
            extra = []
            if caps["text"]:
                extra.append("text")
            if caps["draw"]:
                extra.append("draw")
            if caps["image"]:
                extra.append("images")
            what = "+".join(extra) if extra else "text"
            self.ai_status.configure(
                text=f"● Local AI on ({names}) · {what}", text_color="#6bcb77")
        elif caps["damaged"] and not caps["text"]:
            self.ai_status.configure(
                text="○ Local AI off — model file is damaged (re-download)", text_color="#e07a5f")
        else:
            self.ai_status.configure(text="○ Local AI off — click Set up local AI", text_color=("gray30", "gray75"))

    def ai_say(self, text):
        """Show text as an AI chat bubble (and keep legacy ai_out in sync)."""
        try:
            self.ai_out.delete("1.0", "end")
            self.ai_out.insert("1.0", text)
        except Exception:
            pass
        try:
            self._ai_add_bubble("ai", text)
        except Exception:
            log.debug("ai_say bubble failed", exc_info=True)

    def _clear_placeholder(self, _e=None):
        t = self.ai_cmd.get("1.0", "end").strip()
        if t.startswith("e.g. ") or t.startswith("Type your message"):
            self.ai_cmd.delete("1.0", "end")

    def need_ai(self):
        """Local AI only (v3.12.0-exp) — no cloud, no keys."""
        if ai.cfg.ready:
            return True
        self.goto_tab(AI_TAB)
        if messagebox.askyesno(
                APP_NAME,
                "Local AI is not set up yet.\n\n"
                "Click Yes to set it up automatically "
                "(checks your local_ai folder, then downloads a small model)."):
            self.setup_local_ai()
        return False


    def ai_run_command(self):
        if not self.need_ai():
            return
        cmd = self.ai_cmd.get("1.0", "end").strip()
        if not cmd or cmd.startswith("e.g. ") or cmd.startswith("Type your message"):
            return self.ai_say("Type what you want first, e.g. “6 bathroom words, rounded squares, high contrast”.")
        try:
            self._ai_add_bubble("user", cmd)
        except Exception:
            pass
        try:
            self.ai_cmd.delete("1.0", "end")
        except Exception:
            pass
        # v15.26: a picture request never reaches the settings/words assistant.
        # Asking for a picture makes just the picture — it must not change
        # settings, add words, or switch tabs, however descriptive the prompt is.
        pic = ai.wants_picture(cmd)
        if pic is not None:
            if not pic:
                return self.ai_say("What should the picture be of? Name a word or short phrase "
                                   "(e.g. \u201ca happy child jumping\u201d) and I'll draw just that — "
                                   "no settings will change.")
            self.ai_say(f"Drawing a picture of \u201c{pic}\u201d — just the picture, no settings changed.")
            self.ai_generate_images([pic])
            return
        state = {"words": self.get_words(),
                 "options": {**asdict(self.collect()), "category_colors": self.cat_colors.get()}}
        # v14.8 / v14.9: how many, where to, and what already exists (never repeated).
        # Word requests with no number default to 8 so "words similar to jump" works.
        count = ai.parse_count(cmd)
        if count is None and ai.wants_words(cmd):
            count = 8
        target = ai.detect_target(cmd) or _AI_TARGET_KEY.get(self.ai_target_var.get(), "list")
        self._ai_pending_target = target
        avoid = list(self.get_words()) + [w for w in getattr(self, "_pdf_words", []) if str(w).strip()]
        try:
            avoid += list(self._maker_preview_words())
        except Exception:
            pass
        self.ai_say("Thinking…")
        again = getattr(self, "_ai_last_text", None) == cmd
        self._ai_last_text = cmd
        where = {"pdf": "PDF Studio cards", "maker": "Maker Studio tags"}.get(target, "your word list")
        self._ai_task("text", "REGENERATING TEXT" if again else "GENERATING TEXT",
                      f"Your request: “{cmd[:90]}”  ·  new words go to {where}"
                      + (f"  ·  {count} new" if count else ""),
                      lambda: ai.assistant(cmd, state, self.spec, count=count, avoid=avoid),
                      self._on_assistant)

    def _merge_words_into_list(self, words, source_label="AI"):
        """Add words to the current Word Lists tab without wiping existing ones.
        Returns (added_list, already_had_list)."""
        words = [str(w).strip() for w in (words or []) if str(w).strip()]
        existing = self.get_words()
        known = {w.lower() for w in existing}
        added, had = [], []
        for w in words:
            if w.lower() in known:
                had.append(w)
            else:
                existing.append(w)
                known.add(w.lower())
                added.append(w)
        if added:
            self.set_words(existing)
            try:
                core.ai_register_words(added, source_label, getattr(self, "_ai_last_text", "") or "")
            except Exception:
                pass
            self._lib_refresh_if_open()
            self.changed()
        return added, had

    def _pdf_append_cards(self, words):
        """v14.8: add NEW cards to the END of PDF Studio's cards. Never replaces,
        moves or reorders an existing card; words already on a card are skipped.
        Returns the words actually added."""
        cur = list(getattr(self, "_pdf_words", []))
        known = {str(w).strip().lower() for w in cur if str(w).strip()}
        new = []
        for w in words or []:
            w = str(w).strip()
            if w and w.lower() not in known:
                known.add(w.lower())
                new.append(w)
        if new:
            self._pdf_words.extend(new)
            self._pdf_render_tiles()
            try:
                self._pdf_box_refresh_if_current()
            except Exception:
                pass
            self._queue_pdf_preview()
        return new

    def _maker_append_tags(self, words):
        """v14.8: add NEW tags after the ones already in Maker Studio's preview set.
        Existing tags (and their per-tag designs) are untouched. Turns Multiple tags
        on, and widens \u201cHow many tags\u201d to All if a smaller number would hide the new ones.
        Returns the words actually added."""
        cur = list(self._maker_preview_words())
        known = {str(w).strip().lower() for w in cur}
        new = []
        for w in words or []:
            w = str(w).strip()
            if w and w.lower() not in known:
                known.add(w.lower())
                new.append(w)
        if not new:
            return new
        self._maker_work_words = cur + new
        self._multi_fp = None
        try:
            if not self.maker_multi.get():
                self.maker_multi.set(True)
            v = self.maker_count_var.get()
            if v and v != "All" and int(v) < len(self._maker_work_words):
                self.maker_count_var.set("All")
        except Exception:
            pass
        try:
            self._maker_multi_toggled()      # saves + redraws
        except Exception:
            self.save()
            self.draw()
        return new

    def _on_assistant(self, r):
        for k, v in r["options"].items():
            if k == "category_colors":
                self.cat_colors.set(v)
            elif k in self.vars:
                try:
                    self.vars[k].set(v)
                except Exception as e:
                    log.warning("Could not apply AI option %s=%r: %s", k, v, e)
        if r["category_colors"] is not None:
            self.cat_colors.set(r["category_colors"])
        added, had = [], []
        target = getattr(self, "_ai_pending_target", "list")
        placed, where_tab = [], "Word Lists"
        if r["words"]:
            if target == "maker":
                self._maker_preview_words()   # lock the tags you have BEFORE the list grows
            added, had = self._merge_words_into_list(r["words"], "command")
            # v14.8: put the new words on PDF Studio cards / Maker Studio tags too.
            # Append-only: existing cards and tags are never replaced or moved.
            try:
                if target == "pdf":
                    placed, where_tab = self._pdf_append_cards(r["words"]), "PDF Studio"
                elif target == "maker":
                    placed, where_tab = self._maker_append_tags(r["words"]), "Maker Studio"
            except Exception:
                log.exception("AI: putting new words on %s failed", target)
            _dest = {"pdf": "PDF STUDIO CARDS", "maker": "MAKER STUDIO TAGS"}.get(target, "WORD LIST")
            _n = len(placed) if target in ("pdf", "maker") else len(added)
            self._ai_banner("ok", "text", f"✔ {_n} NEW WORD(S) ADDED TO {_dest}",
                            f"{len(added)} new in the word list  ·  {len(had)} already there  →  Library → Words → AI")
            try:
                self.goto_tab(where_tab)
            except Exception:
                pass
            if self.ai_pics_var.get():
                _pics = placed if target in ("pdf", "maker") else added
                if _pics:
                    self.after(300, lambda w=list(_pics): self.ai_generate_images(w))
        for k, btn in (("body_color", self.body_btn), ("ink_color", self.ink_btn)):
            try:
                btn.configure(fg_color=self.vars[k].get())
            except Exception:
                pass
        changes = ", ".join(f"{k} → {v}" for k, v in r["options"].items())
        lines = [r.get("message") or "Done."]
        if placed:
            lines.append("")
            _what = "PDF Studio cards (after your existing ones)" if target == "pdf" else "Maker Studio tags (after your existing ones)"
            lines.append(f"Added {len(placed)} new word(s) to {_what}:")
            lines.append("  " + ", ".join(placed))
            if self.ai_pics_var.get():
                lines.append("Drawing AI pictures for them now.")
            else:
                lines.append("Pictures come from your library / ARASAAC; tick \u201cAI-draw pictures\u201d for AI ones.")
        elif added:
            lines.append("")
            lines.append(f"Added {len(added)} word(s) to your Word Lists tab:")
            lines.append("  " + ", ".join(added))
        if r.get("short"):
            lines.append(f"(You asked for {r['requested']} but only {r['requested'] - r['short']} new ones came back \u2014 "
                         "ask again for the rest.)")
        if had:
            lines.append(f"(Already in your list: {', '.join(had)})")
        if changes:
            lines.append("")
            lines.append("Changed settings: " + changes)
        if not added and not changes:
            lines.append("")
            lines.append("(No new words or settings this time.)")
        self.ai_say("\n".join(lines))
        # Always clear the GENERATING TEXT banner (previously left spinning
        # when the reply had neither new words nor settings changes).
        if r.get("words"):
            pass  # already set to ok when words were added
        elif changes:
            self._ai_banner("ok", "text", "✔ DONE", "Settings changed — no new words this time.")
        else:
            self._ai_banner("ok", "text", "✔ DONE", "No new words or settings this time.")
        self.changed()

    # ---- 2. suggest words
    def ai_suggest_words(self):
        if not self.need_ai():
            return
        topic = ctk.CTkInputDialog(
            text="What situation are the words for?\n(e.g. going to the dentist, bedtime, art class)",
            title="Suggest words").get_input()
        if not topic or not topic.strip():
            return
        cats = list(aac_boards.CATEGORIES.keys())
        topic = topic.strip()
        again = getattr(self, "_ai_last_text", None) == ("suggest:" + topic.lower())
        self._ai_last_text = "suggest:" + topic.lower()
        self._ai_topic = topic
        self._ai_task("text", "REGENERATING TEXT" if again else "GENERATING TEXT",
                      f"Word ideas for “{topic}”  ·  saved to Library → Words → AI",
                      lambda: ai.suggest_words(topic, 12, cats), self._on_suggested)

    def _on_suggested(self, items):
        for it in items:
            try:
                aac_boards.AI_CATEGORIES[it["word"].lower()] = it["category"]
            except Exception:
                pass
        words = [it["word"] for it in items if it.get("word")]
        added, had = self._merge_words_into_list(words, "suggested")
        # v15.69: the composer's "AI-draw pictures for them" box now also covers
        # rail-suggested words — this is what replaces the old "Both" button.
        # (Box off by default, so suggesting words alone is unchanged.)
        if added and self.ai_pics_var.get():
            self.ai_generate_images(added, allow_shapes=True)
        topic = getattr(self, "_ai_topic", "") or "your request"
        self._ai_banner("ok", "text", "✔ WORDS ADDED TO WORD LIST",
                        f"{len(added)} new for “{topic}”  →  Word Lists + Library → Words → AI")
        try:
            self.goto_tab("Word Lists")
        except Exception:
            pass
        lines = [
            f"Suggested words for “{topic}”:",
            "",
            "  " + ", ".join(words),
            "",
        ]
        if added:
            lines.append(f"Added {len(added)} new word(s) to your Word Lists tab.")
        if had:
            lines.append(f"Already had: {', '.join(had)}")
        lines.append("Also saved under Library → Words → AI.")
        self.ai_say("\n".join(lines))

    # ---- 3. prepare all words (search term + category in one call)


    # ============================================================ AI images + AI words (v13.23)
    def _add_generate_entry(self, menu, words, separator=True):
        """Add '✨ Generate an image for this word' to a right-click menu. The wording
        changes to 'Regenerate' when the word already has an AI picture, and to 'Make
        another' when it only has a normal picture."""
        words = [w for w in dict.fromkeys(str(w).strip() for w in (words or [])) if w]
        if not words:
            return
        if separator:
            menu.add_separator()
        if len(words) == 1:
            w = words[0]
            if core.ai_images_for(w):
                label = f"✨  Regenerate AI image for “{w}”"
            elif core.word_has_picture(w):
                label = f"✨  Make an AI image for “{w}” (it already has a picture)"
            else:
                label = f"✨  Generate an image for “{w}”"
        else:
            label = f"✨  Generate images for these {len(words)} words"
        menu.add_command(label=label, command=lambda ws=list(words): self.ai_generate_images(ws))
        if len(words) == 1:
            menu.add_command(label=f"✎  Draw “{words[0]}” my way… (describe the picture)",
                             command=lambda w=words[0]: self._ai_draw_my_way(w))

    def _popup_generate_menu(self, event, word):
        m = tk.Menu(self, tearoff=0)
        self._add_generate_entry(m, [word], separator=False)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _on_canvas_right_drag(self, event):
        """v15.13: right-drag pans the editor page."""
        try:
            if self.tabs.get() == "PDF Studio" and self._ped_active:
                self._ped_on_rclick_motion(event)
                return "break"
        except Exception:
            pass

    def _on_canvas_right_release(self, event):
        """v15.13: end of a right-drag (a plain right-click still gets its menu)."""
        try:
            if self.tabs.get() == "PDF Studio" and self._ped_active:
                self._ped_on_rclick_release(event)
                return "break"
        except Exception:
            pass

    def _on_canvas_right_click(self, event):
        """Right-click the live tag preview → generate an image for the word on the tag."""
        try:
            if self.tabs.get() == "PDF Studio":
                if self._ped_active:
                    self._ped_on_rclick_press(event)   # v15.13: menu, or an armed pan
                    return "break"
                self._on_pdf_canvas_right_click(event)
                return "break"
            if self.tabs.get() == AI_TAB:
                return
        except Exception:
            pass
        # v14: multi preview — right-clicking a tag offers to take it
        # out of the locked preview set (and to generate an image for it).
        try:
            if self.tabs.get() in ("Maker Studio", "Tag Design") and \
                    getattr(self, "maker_multi", None) is not None and self.maker_multi.get():
                for _w, _x0, _y0, _x1, _y1 in getattr(self, "_multi_hit", []):
                    if _x0 <= event.x <= _x1 and _y0 <= event.y <= _y1:
                        m = tk.Menu(self, tearoff=0)
                        m.add_command(label=f"Remove \u201c{_w}\u201d from preview",
                                      command=lambda w=_w: self._maker_remove_from_preview(w))
                        self._add_generate_entry(m, [_w])
                        try:
                            m.tk_popup(event.x_root, event.y_root)
                        finally:
                            m.grab_release()
                        return "break"
        except Exception:
            pass
        w = self.preview_word.get()
        if not w or w == "-":
            return "break"
        self._popup_generate_menu(event, w)
        return "break"

    def _ai_image_backend(self):
        """'sd' (SD Turbo, real images) → 'draw' (working text model draws icon) → None.

        v13.25: never claim draw if every GGUF is broken — that was the
        'image generation failed' loop when only a damaged model was present.
        """
        caps = ai.capabilities(probe=False)
        if caps["image"]:
            return "sd"
        if caps["draw"] and ai.cfg.ready:
            return "draw"
        return None

    def setup_sd_turbo_gui(self, then_words=None, then_subjects=None):
        """Download the SD engine + SD Turbo model in one go (v14.8), then - if words were
        waiting - generate their pictures. Resumes if the connection drops."""
        if getattr(self, "_ai_dl_running", False):
            self.flash_status("A download is already running — let it finish", ok=False)
            return
        self._ai_dl_running = True
        self.ai_say("Setting up SD Turbo (real pictures): the engine, then the ~5.2 GB model. "
                    "It keeps going if the connection drops — just click Set up again to resume.")
        self.ai_status.configure(text="⏳ Setting up SD Turbo…", text_color="#4c9aff")
        progress = self._make_ai_progress(say_steps=True)

        def work():
            try:
                ai.download_sd_engine(progress=progress)
                ai.download_sd_model(progress=progress)
                def ok():
                    self._ai_dl_running = False
                    self.refresh_ai_folder_label()
                    self.ai_status.configure(text="● SD Turbo ready", text_color="#6bcb77")
                    self.ai_say("SD Turbo is ready — real pictures from now on.")
                    if then_words:
                        self.after(300, lambda: self.ai_generate_images(then_words, subjects=then_subjects))
                self.ui_call(ok)
            except Exception as e:
                log.exception("setup_sd_turbo_gui")
                def bad(err=e):
                    self._ai_dl_running = False
                    self.ai_status.configure(text="○ SD Turbo setup incomplete", text_color="#e07a5f")
                    self.ai_say(f"SD Turbo setup didn't finish:\n{err}\n\nClick the picture button again to resume.")
                self.ui_call(bad)
        threading.Thread(target=work, daemon=True).start()

    def ai_generate_images(self, words, subjects=None, allow_shapes=False):
        """Generate (or regenerate) a picture for each word. Every result is saved into the
        Library, labelled AI (Library → Pictures → AI), and becomes that word's picture."""
        words = [w for w in dict.fromkeys(str(w).strip() for w in (words or [])) if w]
        if not words:
            return
        if getattr(self, "_ai_busy", False):
            self.flash_status("The AI is still working on something — wait for it to finish", ok=False)
            return
        subjects = {str(k).strip(): str(v).strip() for k, v in (subjects or {}).items() if str(v).strip()}
        drew = {}                      # word -> what the AI was told to draw (v14.8)
        style_name = self.ai_style_var.get()
        backend = self._ai_image_backend()
        if backend is None:
            self.goto_tab(AI_TAB)
            caps = ai.capabilities(probe=False)
            if caps["damaged"] and not caps["text"]:
                detail = "Your text model file is damaged. Re-download it in the AI tab, then try again."
            else:
                detail = "Set up a working text model (simple icons) or SD Turbo (real pictures)."
            self._ai_banner("error", "image", "CAN'T GENERATE IMAGES YET", detail)
            self.ai_say(
                "Image generation needs one of these:\n"
                "  • A working text model in local_ai/ (Set up local AI) — draws simple icons\n"
                "  • SD Turbo engine + model (Image AI section) — real pictures\n\n"
                + ("Broken files to re-download:\n  " +
                   "\n  ".join(f"{Path(p).name} — {why}" for p, why in caps["damaged"])
                   if caps["damaged"] else
                   "Click Set up local AI, or download SD Turbo first."))
            return
        if backend == "draw" and not allow_shapes:
            # v14.8: the text model only knows how to place basic shapes - the "arrows and
            # blocks" pictures. Never do that silently: say so and offer the real thing.
            ans = messagebox.askyesnocancel(
                APP_NAME,
                "SD Turbo (the real picture AI) isn't installed on this computer.\n\n"
                "Without it the app can only place basic shapes (arrows, blocks) — "
                "that is why the pictures don't look like the word.\n\n"
                "Install SD Turbo now?  (about 5.5 GB download, one time)\n\n"
                "Yes = install SD Turbo, then make the picture\n"
                "No = use the basic shapes anyway\n"
                "Cancel = do nothing", parent=self)
            if ans is None:
                return
            if ans:
                self.goto_tab(AI_TAB)
                self.setup_sd_turbo_gui(then_words=list(words), then_subjects=subjects)
                return
            allow_shapes = True
        method = "SD Turbo" if backend == "sd" else "basic shape drawing (not SD Turbo)"
        again = any(core.ai_images_for(w) for w in words)
        head = ("REGENERATING IMAGE" if again else "GENERATING IMAGE") + ("S" if len(words) > 1 else "")
        import random

        notes = []

        sd_state = {"ok": True}      # v14.8: once SD Turbo fails in a batch, don't reload 5 GB per word

        def fn():
            made, failed = [], []
            for i, w in enumerate(words, 1):
                self.q.put(("ai_banner", "busy", "image", head,
                            f"“{w}”" + (f"  ({i} of {len(words)})" if len(words) > 1 else "") +
                            f"  ·  {method}  ·  saves to Library → Pictures → AI"))
                try:
                    out = None
                    if backend == "sd" and sd_state["ok"]:
                        try:
                            seed = random.randint(1, 2 ** 31 - 1) if core.ai_images_for(w) else None
                            png = ai.sd_generate_png(w, seed=seed, subject=subjects.get(w), style=style_name)
                            drew[w] = ai.last_subject(w) or w
                            out = core.save_ai_image(Path(png).read_bytes(), w, how="SD Turbo")
                        except Exception as e_sd:
                            log.warning("SD Turbo failed for '%s': %s", w, e_sd)
                            sd_state["ok"] = False
                            if not allow_shapes:
                                # v14.8: no silent fallback to the shape drawing (arrows and
                                # blocks) - show the REAL SD Turbo error instead.
                                failed.append((w, f"SD Turbo failed: {e_sd}"))
                                for rest in words[i:]:
                                    failed.append((rest, "skipped (SD Turbo failed earlier in this batch)"))
                                notes.append("Not using the basic shape drawing as a stand-in, because it "
                                             "makes arrows and blocks. Fix the SD Turbo problem above and try again.")
                                break
                            notes.append(f"SD Turbo didn't work for “{w}” ({str(e_sd)[:140]}), "
                                         "so basic shapes were used (as you chose).")
                    if out is None:
                        geom = ai.draw_icon_geometry(w, force_new=True)
                        out = self._save_ai_drawing_to_library(w, geom, force=True)
                        if out is None:
                            raise ai.AIError("The AI drew nothing usable this time — try again.")
                    made.append((w, out))
                    self.ui_call(self._ai_image_ready, w, out)   # show each picture as soon as it exists
                except Exception as e:
                    log.warning("AI image for '%s' failed: %s", w, e)
                    failed.append((w, str(e)))
            return made, failed

        def done(res):
            made, failed = res
            for w, out in made:
                self.overrides[w] = str(out)
                self._no_auto_pic.discard(w)
            if made:
                names = ", ".join(f"“{w}”" for w, _ in made[:3]) + (f" +{len(made) - 3} more" if len(made) > 3 else "")
                self._ai_banner("ok", "image", "✔ IMAGE SAVED TO LIBRARY — AI LABEL",
                                f"{names}  →  Library → Pictures → AI   ({Path(made[-1][1]).name})")
                self.flash_status(f"✔ AI image saved to the Library (AI) for {names}")
            lines = [f"✔ “{w}” → {Path(o).name}  (Library → Pictures → AI)  ·  "
                     + ("SD Turbo" if w in drew else "basic shape drawing")
                     + (f"\n    drawn as: {drew[w]}" if drew.get(w) and drew[w].lower() != w.lower() else "")
                     for w, o in made]
            lines += [f"✘ “{w}”: {err}" for w, err in failed]
            lines += notes
            if made:
                lines.append("\nIt's now this word's picture in Maker Studio, PDF Studio and the Library. "
                             "Right-click the word again to regenerate — each try is kept as its own AI picture.")
            if failed and not made:
                self._ai_banner("error", "image", "IMAGE GENERATION FAILED", failed[0][1][:160])
            self.ai_say("\n".join(lines))
            self.changed()
            self._lib_refresh_if_open()
            # v14.4: if the user is looking at PDF Studio, show the new
            # picture on its card straight away
            try:
                if self.tabs.get() == "PDF Studio":
                    self.draw_pdf_preview()
            except Exception:
                pass
        self._ai_task("image", head, f"“{words[0]}”  ·  {method}  ·  saves to Library → Pictures → AI", fn, done)

    def _ai_draw_my_way(self, word):
        """Let the user say exactly what the picture should show (v14.8) - the surest fix
        when the AI's own idea of a word doesn't match what you mean."""
        try:
            from tkinter import simpledialog
            last = ai.last_subject(word)
            txt = simpledialog.askstring(
                APP_NAME,
                f"What should the picture of “{word}” show?\n\n"
                "Describe ONE simple thing, e.g. “a child jumping with both arms up”.\n"
                "Leave it as the word to let the AI decide.",
                initialvalue=last or word, parent=self)
        except Exception:
            log.exception("draw my way dialog")
            return
        if txt is None or not txt.strip():
            return
        if txt.strip().lower() == word.lower():
            self.ai_generate_images([word])          # unchanged = let the AI decide what to draw
        else:
            self.ai_generate_images([word], subjects={word: txt.strip()})

    def _ai_image_ready(self, word, out):
        """One AI picture finished: make it the word's picture right away (v14.8) instead of
        waiting for the whole batch - a batch of SD pictures can take many minutes."""
        try:
            self.overrides[word] = str(out)
            self._no_auto_pic.discard(word)
            self.changed()
            self._lib_refresh_if_open()
            if self.tabs.get() == "PDF Studio":
                self.draw_pdf_preview()
        except Exception:
            log.debug("_ai_image_ready failed", exc_info=True)

    # ---- the big "what is the AI doing right now" banner at the top of the AI tab
    def _ai_banner(self, state, kind, title, detail=""):
        """state: 'busy' | 'ok' | 'error' | 'idle'.  kind: 'image' | 'text'."""
        try:
            colors = {"busy": "#9B59B6" if kind == "image" else "#2f6fb3",
                      "ok": "#4ECDC4", "error": "#8c3a3a", "idle": ("gray85", "gray20")}
            icon = {"busy": "🎨" if kind == "image" else "✍️", "ok": "", "error": "⚠", "idle": "💤"}[state]
            self.ai_banner_frame.configure(fg_color=colors[state])
            self.ai_banner_title.configure(text=(f"{icon}  {title}" if icon else title),
                                           text_color="white" if state != "idle" else ("gray20", "gray80"))
            self.ai_banner_detail.configure(text=detail,
                                            text_color="white" if state != "idle" else ("gray35", "gray70"))
            if state == "busy":
                self.ai_banner_bar.grid()
                self.ai_banner_bar.start()
                if not getattr(self, "_ai_t0", None):
                    self._ai_t0 = time.time()
                    if getattr(self, "_ai_tick_id", None):
                        self.after_cancel(self._ai_tick_id)
                    self._ai_tick()
            else:
                self.ai_banner_bar.stop()
                self.ai_banner_bar.grid_remove()
                self._ai_t0 = None
                if getattr(self, "_ai_tick_id", None):
                    try:
                        self.after_cancel(self._ai_tick_id)
                    except Exception:
                        pass
                    self._ai_tick_id = None
        except Exception:
            log.debug("ai banner update failed", exc_info=True)

    def _ai_tick(self):
        t0 = getattr(self, "_ai_t0", None)
        if not t0:
            return
        e = int(time.time() - t0)
        try:
            self.ai_banner_time.configure(text=f"⏱ {e // 60}:{e % 60:02d}")
        except Exception:
            return
        self._ai_tick_id = self.after(1000, self._ai_tick)

    def _ai_task(self, kind, head, detail, fn, done):
        """Run fn() in the background with the AI-tab banner showing exactly what is happening.
        Errors are shown in the banner + output box (no pop-up)."""
        if getattr(self, "_ai_busy", False):
            self.flash_status("The AI is still working on something — wait for it to finish", ok=False)
            return
        self._ai_busy = True
        self._ai_t0 = None
        self._ai_banner("busy", kind, head, detail)

        def work():
            try:
                return ("ok", fn())
            except Exception as e:
                if not isinstance(e, ai.AIError):
                    log.exception("AI task '%s' failed", head)
                return ("err", e)

        def finished(r):
            self._ai_busy = False
            if r[0] == "err":
                msg = str(r[1])
                self._ai_banner("error", kind, "AI TASK FAILED", msg[:160])
                self.ai_say(f"That didn't work:\n{msg}")
                return
            try:
                done(r[1])
                note = ai.pop_fallback_note()
                if note:
                    self.ai_out.insert("end", "\n\n" + note)
            except Exception:
                log.exception("AI task done-handler")
                self._ai_banner("error", kind, "AI TASK FINISHED BUT SAVING FAILED", "See Log for details.")
                return
            # Safety net: if the done-handler forgot to clear the busy banner
            # (e.g. no words / no settings path), stop the spinning bar.
            try:
                if getattr(self, "_ai_t0", None) is not None:
                    self._ai_banner("ok", kind, "✔ DONE", "")
            except Exception:
                pass
        self.bg(head.title() + "…", work, finished, kind="ai")

    def _lib_refresh_if_open(self):
        try:
            self._lib_reload(keep=True)
        except Exception:
            pass

    def open_library_ai(self, mode="Pictures"):
        """Jump to the Library showing only the AI items (pictures or words)."""
        self.open_library()
        try:
            self.lib_sort.set("✨ AI only")
            self.lib_mode.set(mode)
            self._lib_set_mode(mode)
        except Exception:
            log.exception("open_library_ai")

    # ---- 4. draw icon with AI

    # ---- 5. quality check


    # ---- 6. explain log
    def ai_explain_log(self):
        if not self.need_ai():
            return
        diag = ""
        try:
            diag = (LOG_DIR / "diagnostics.txt").read_text(encoding="utf-8")
        except Exception:
            pass
        text = tail_log(150)
        self.bg("AI is reading the log…", lambda: ai.explain_log(text, diag), self.ai_say, kind="ai")

    # ============================================================ log window
    def open_log(self):
        if self.logwin is not None and self.logwin.winfo_exists():
            self.logwin.lift()
            self.refresh_log()
            return
        w = ctk.CTkToplevel(self)
        w.title("Log & diagnostics")
        w.geometry("860x580")
        self.logwin = w
        bar = ctk.CTkFrame(w)
        bar.pack(fill="x", padx=10, pady=10)
        ctk.CTkButton(bar, text="Refresh", width=80, command=self.refresh_log).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="Copy all", width=80, command=self.copy_log).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="Open log folder", width=120, command=lambda: open_folder(LOG_DIR)).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="Run self-test", width=110, command=self.run_selftest).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="✨ Explain", width=90, fg_color=AI_COLOR, hover_color=AI_HOVER,
                      command=lambda: (w.destroy(), self.goto_tab(AI_TAB), self.ai_explain_log())).pack(side="left", padx=4)
        self.logbox = ctk.CTkTextbox(w, font=ctk.CTkFont(family="Consolas", size=12), wrap="none")
        self.logbox.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.refresh_log()
        w.after(200, w.lift)

    def refresh_log(self):
        if self.logbox is not None:
            self.logbox.delete("1.0", "end")
            self.logbox.insert("1.0", tail_log(300))
            self.logbox.see("end")

    def copy_log(self):
        self.clipboard_clear()
        self.clipboard_append(self.logbox.get("1.0", "end"))
        self.flash_status("Log copied to clipboard")

    def run_selftest(self):
        self.logbox.delete("1.0", "end")
        self.logbox.insert("1.0", "Running self-test … (about 10 seconds)")

        def work():
            text, _ = aac_diag.diagnose()
            self.q.put(("diag", text))
        threading.Thread(target=work, daemon=True).start()

    # ============================================================ persistence
    def save(self):
        try:
            SETTINGS_FILE.write_text(json.dumps({
                "opts": asdict(self.collect()), "words": "\n".join(self.get_words()),
                "search_as": self.search_as, "cat_colors": self.cat_colors.get(),
                "batch_fmt": self.batch_fmt.get(), "last_dir": self.last_dir,
                "bed_size": self.bed_size.get() if hasattr(self, "bed_size") else "220 × 220 × 220",
                "maker_multi": bool(self.maker_multi.get()) if hasattr(self, "maker_multi") else False,
                "maker_count": self.maker_count_var.get() if hasattr(self, "maker_count_var") else "All",
                "ai_target": self.ai_target_var.get() if hasattr(self, "ai_target_var") else AI_TARGETS[0],
                "ai_pics": bool(self.ai_pics_var.get()) if hasattr(self, "ai_pics_var") else False,
                "ai_style": self.ai_style_var.get() if hasattr(self, "ai_style_var") else ai.AAC_STYLE_DEFAULT,
                "aac_style_prompt": aac_style.get_config().style_prompt,
                "aac_negative_prompt": aac_style.get_config().negative_prompt,
                "aac_subject_mode": aac_style.get_config().subject_mode,
                "maker_work_words": (list(self._maker_work_words) if getattr(self, "_maker_work_words", None) is not None else None),
                "design_overrides": self.design_overrides if hasattr(self, "design_overrides") else {},
                "auto_export": self.auto_export.get(),
                "export_dir_pdf": self.export_dir_pdf.get(),
                "export_dir_maker": self.export_dir_maker.get(),
                "export_dir_board": self.export_dir_board.get(),
                "light": ctk.get_appearance_mode() == "Light",
                "pastel_theme_applied": True,
                "ai_categories": aac_boards.AI_CATEGORIES,
                "pdf_layout": self.pdf_layout_var.get(),
                "pdf_pages": int(self.pdf_pages_var.get()) if hasattr(self, "pdf_pages_var") else 1,
                "pdf_word_scale": float(self.pdf_word_scale.get()) if hasattr(self, "pdf_word_scale") else 1.0,
                "pdf_pic_scale": float(self.pdf_pic_scale.get()) if hasattr(self, "pdf_pic_scale") else 1.0,
                "pdf_pic_y": float(self.pdf_pic_y.get()) if hasattr(self, "pdf_pic_y") else 0.35,
                "pdf_word_y": float(self.pdf_word_y.get()) if hasattr(self, "pdf_word_y") else 0.12,
                "pdf_preview_mode": self.pdf_preview_mode.get() if hasattr(self, "pdf_preview_mode") else "All pages",
                "pdf_labels": self.pdf_labels_var.get(),
                "pdf_no_label": sorted(self.pdf_no_label),
                "opensymbols_secret_fb": self._opensymbols_secret_fb,
                "pdf_colors": self.pdf_colors_var.get(),
                "pdf_paper": self.pdf_paper.get(), "pdf_cut_guides": self.pdf_cut_guides.get(),
                "pdf_tile_colors": dict(getattr(self, "pdf_tile_colors", {})),
                "pdf_pic_scale_map": dict(getattr(self, "pdf_pic_scale_map", {})),
                "pdf_pic_y_map": dict(getattr(self, "pdf_pic_y_map", {})),
                "pdf_show_pics": self.pdf_show_pics.get(), "pdf_show_words": self.pdf_show_words.get(),
                "strip_words": self.strip_words, "word_lists": self.word_lists}, indent=2), encoding="utf-8")
        except Exception as e:
            log.warning("Could not save settings: %s", e)

    def on_close(self):
        self.save()
        log.info("Closed normally")
        self.destroy()


def run_gui():
    ctk.set_default_color_theme("blue")
    App().mainloop()
