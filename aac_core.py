"""Geometry + export engine (no GUI)."""
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace as _dc_replace
from pathlib import Path

import cv2
import numpy as np
import trimesh
from matplotlib.font_manager import FontProperties
from matplotlib.textpath import TextPath
from PIL import Image
from shapely.affinity import scale as sh_scale, translate as sh_translate
from shapely.geometry import Point, Polygon, box

from aac_3mf import write_3mf
from aac_common import APP_DIR, DATA_DIR, ICON_DIR, log

# --- AI hooks (installed by the GUI when an API key is set; all optional) -------------------------
AI_HOOKS = {"pick": None,          # pick(word, term, candidates) -> index of best ARASAAC picture
            "draw": None,          # draw(word) -> shapely geometry (AI-drawn icon)
            "auto_draw": False,    # draw an icon with AI when no picture is found
            "force_draw": set()}   # lowercase words the user asked AI to draw

# --- live status reporting (v13.14) -----------------------------------------------------------------
# The GUI installs a per-thread callback before building a tag so the preview can show
# what is happening ("Downloading picture…", "Local AI is creating an image…").
_TL = threading.local()


def set_status_callback(fn):
    """Register (or clear, with None) the status callback for the CURRENT thread."""
    _TL.cb = fn


def report_status(msg):
    """Tell the current thread's status callback what is going on. Never raises."""
    cb = getattr(_TL, "cb", None)
    if cb:
        try:
            cb(msg)
        except Exception:
            pass


# Permanent offline index: maps normalized word -> relative icon filename
ICON_INDEX_FILE = DATA_DIR / "icon_index.json"

SHAPES = ["Circle", "Rounded square", "Square", "Hexagon", "Star", "Heart", "Shield", "Oval"]
STYLES = ["Flat inlay", "Raised", "Sunken", "Outline"]
HOLES = ["Top loop", "Left loop", "Through (top)", "None"]
LAYOUTS = ["Icon + word", "Icon only", "Word only"]
SOURCES = ["Auto (local, then online)", "Local icons only"]
CASES = ["lowercase", "Title Case", "UPPERCASE"]
FONT_FILES = {
    "Verdana Bold": "verdanab.ttf", "Arial Bold": "arialbd.ttf", "Segoe UI Bold": "segoeuib.ttf",
    "Tahoma Bold": "tahomabd.ttf", "Trebuchet Bold": "trebucbd.ttf", "Calibri Bold": "calibrib.ttf",
    "Comic Sans Bold": "comicbd.ttf",
}
DEFAULT_FONT = "DejaVu Sans Bold"
# Map everyday / AAC words to better ARASAAC search terms so typing almost
# anything (including custom phrases) finds a close pictogram.
ALIASES = {
    # food / drink
    "eat": "eat", "food": "food", "lunch": "lunch", "breakfast": "breakfast",
    "dinner": "dinner", "snack": "cookie", "drink": "drink", "thirsty": "thirsty",
    "hungry": "hungry", "apple": "apple", "banana": "banana", "cookie": "cookie",
    "juice": "juice", "milk": "milk", "pizza": "pizza", "sandwich": "sandwich",
    "fruit": "fruit", "bread": "bread", "cheese": "cheese", "egg": "egg",
    "chicken": "chicken", "meat": "meat", "soup": "soup", "salad": "salad",
    "cake": "cake", "ice cream": "ice cream", "candy": "candy", "chips": "chips",
    "coffee": "coffee", "tea": "tea", "soda": "soda", "water": "water",
    # people / places
    "house": "home", "home": "home", "school": "school", "bathroom": "toilet",
    "toilet": "toilet", "mom": "mother", "mum": "mother", "mother": "mother",
    "dad": "father", "father": "father", "teacher": "teacher", "friend": "friend",
    "doctor": "doctor", "nurse": "nurse", "brother": "brother", "sister": "sister",
    "grandma": "grandmother", "grandpa": "grandfather", "baby": "baby",
    "boy": "boy", "girl": "girl", "man": "man", "woman": "woman",
    "park": "park", "store": "shop", "shop": "shop", "hospital": "hospital",
    "library": "library", "church": "church", "beach": "beach", "pool": "swimming pool",
    "kitchen": "kitchen", "bedroom": "bedroom", "outside": "outside", "inside": "inside",
    # actions / core
    "wait": "wait", "next": "next", "hear": "listen", "look": "look", "see": "see",
    "go": "go", "come": "come", "stop": "stop", "help": "help", "want": "want",
    "need": "need", "like": "like", "love": "love", "play": "play", "work": "work",
    "sleep": "sleep", "wash": "wash", "open": "open", "close": "close",
    "give": "give", "take": "take", "put": "put", "get": "get", "make": "make",
    "read": "read", "write": "write", "draw": "draw", "sing": "sing",
    "dance": "dance", "run": "run", "walk": "walk", "sit": "sit", "stand": "stand",
    "jump": "jump", "climb": "climb", "swim": "swim", "ride": "ride",
    "push": "push", "pull": "pull", "turn": "turn", "break": "break",
    "fix": "fix", "share": "share", "choose": "choose", "find": "find",
    "show": "show", "tell": "tell", "ask": "ask", "answer": "answer",
    "think": "think", "know": "know", "remember": "remember", "forget": "forget",
    "start": "start", "begin": "start", "finish": "finished", "end": "end",
    # feelings / body
    "happy": "happy", "sad": "sad", "angry": "angry", "scared": "scared",
    "tired": "tired", "sick": "sick", "hurt": "hurt", "pain": "pain",
    "hot": "hot", "cold": "cold", "good": "good", "bad": "bad",
    "excited": "excited", "bored": "bored", "worried": "worried", "proud": "proud",
    "confused": "confused", "surprised": "surprised", "calm": "calm",
    "head": "head", "hand": "hand", "foot": "foot", "eye": "eye", "ear": "ear",
    "mouth": "mouth", "nose": "nose", "arm": "arm", "leg": "leg", "hair": "hair",
    # time / sequence / social
    "now": "now", "later": "later", "again": "again", "more": "more",
    "finished": "finished", "done": "finished", "all done": "finished",
    "yes": "yes", "no": "no", "please": "please", "thank you": "thank you",
    "thanks": "thank you", "sorry": "sorry", "hello": "hello", "hi": "hello",
    "goodbye": "goodbye", "bye": "goodbye", "morning": "morning", "night": "night",
    "today": "today", "tomorrow": "tomorrow", "yesterday": "yesterday",
    "first": "first", "last": "last", "before": "before", "after": "after",
    # objects / activities / misc
    "book": "book", "toy": "toy", "ball": "ball", "car": "car", "bus": "bus",
    "phone": "telephone", "computer": "computer", "tv": "television",
    "music": "music", "picture": "picture", "color": "colour", "colour": "colour",
    "movie": "film", "game": "game", "puzzle": "puzzle", "doll": "doll",
    "bike": "bicycle", "train": "train", "plane": "airplane", "boat": "boat",
    "dog": "dog", "cat": "cat", "bird": "bird", "fish": "fish", "horse": "horse",
    "rain": "rain", "sun": "sun", "snow": "snow", "wind": "wind", "cloud": "cloud",
    "money": "money", "bag": "bag", "key": "key", "door": "door", "window": "window",
    "chair": "chair", "table": "table", "bed": "bed", "clothes": "clothes",
    "shirt": "shirt", "shoes": "shoes", "hat": "hat", "coat": "coat",
    "toothbrush": "toothbrush", "soap": "soap", "towel": "towel",
    "i": "I", "me": "me", "my": "my", "you": "you", "he": "he", "she": "she",
    "we": "we", "they": "they", "it": "it", "this": "this", "that": "that",
    "what": "what", "where": "where", "when": "when", "who": "who", "why": "why",
    "how": "how", "which": "which", "don't": "do not", "cant": "cannot",
    "can't": "cannot", "won't": "will not",
}

# Core AAC vocabulary ready for one-click load (larger set)
COMMON_WORDS = [
    "I", "you", "me", "my", "he", "she", "we", "they",
    "want", "need", "like", "love", "don't", "help", "more", "finished", "all done",
    "yes", "no", "please", "thank you", "sorry", "hello", "goodbye",
    "go", "stop", "come", "wait", "look", "listen", "see", "play", "work",
    "eat", "drink", "sleep", "wash", "open", "close", "give", "take", "put", "get",
    "make", "read", "write", "draw", "sing", "dance", "run", "walk", "sit", "stand",
    "happy", "sad", "angry", "scared", "tired", "sick", "hurt", "hot", "cold",
    "good", "bad", "excited", "bored", "calm",
    "home", "school", "bathroom", "outside", "inside", "kitchen", "bedroom",
    "park", "store", "hospital", "car", "bus", "bike",
    "mom", "dad", "friend", "teacher", "brother", "sister", "baby",
    "food", "water", "juice", "milk", "cookie", "apple", "banana", "pizza",
    "book", "toy", "ball", "music", "game", "phone", "computer", "tv",
    "dog", "cat", "bird", "sun", "rain", "now", "later", "again",
    "what", "where", "when", "who", "why", "how", "this", "that",
]


@dataclass
class Opts:
    shape: str = "Circle"
    size: float = 40.0
    thickness: float = 1.6
    style: str = "Flat inlay"
    raise_h: float = 0.6
    hole: str = "Top loop"
    hole_d: float = 4.0
    ring: bool = True
    ring_w: float = 0.8
    layout: str = "Icon + word"
    icon_source: str = SOURCES[1]  # local first; online only via Auto or Find pictures
    icon_scale: float = 1.0
    icon_bold: float = 0.0
    font: str = "Verdana Bold"
    text_case: str = "lowercase"
    text_scale: float = 1.0
    body_color: str = "#f3e6dc"
    ink_color: str = "#2b2b2b"
    ink2_color: str = "#8a8a8a"   # v15.8: the picture's midtone ink (3-colour tags)
    ink_layers: float = 2.0       # v15.8: picture ink layers — 1 = ink only (classic), 2 = ink + midtone
    # v2 experimental
    connectors: bool = False      # left socket + right tab to snap tags into sentences
    magnet_recess: bool = False   # circular recess on bottom for a disc magnet
    high_contrast: bool = False   # force black body / white ink (or inverse)
    soft_edges: bool = True       # slightly larger corner rounding where applicable
    design_depth: float = 0.6     # mm of design on the FIRST layers (face-down print); top is solid


# ------------------------------------------------------------ geometry helpers
def xor_rings(rings):
    """Even-odd combine of closed rings so holes (o, a, e ...) work."""
    geom = Polygon()
    for r in rings:
        if len(r) < 3:
            continue
        p = Polygon(r).buffer(0)
        if not p.is_empty and p.area > 1e-6:
            geom = geom.symmetric_difference(p)
    return geom


def polys(g):
    if g is None or g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    return [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon) and not p.is_empty]


def fit(geom, cx, cy, w, h):
    if geom is None or geom.is_empty:
        return Polygon()
    minx, miny, maxx, maxy = geom.bounds
    s = min(w / max(maxx - minx, 1e-6), h / max(maxy - miny, 1e-6))
    geom = sh_scale(geom, s, s, origin=(0, 0))
    minx, miny, maxx, maxy = geom.bounds
    return sh_translate(geom, cx - (minx + maxx) / 2, cy - (miny + maxy) / 2)


# ------------------------------------------------------------ text
def _font_dirs():
    d = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"]
    la = os.environ.get("LOCALAPPDATA")
    if la:
        d.append(Path(la) / "Microsoft" / "Windows" / "Fonts")
    return d


def _find_font_file(fname):
    for d in _font_dirs():
        if (d / fname).exists():
            return d / fname
    return None


def available_fonts():
    fonts = [n for n, f in FONT_FILES.items() if _find_font_file(f)]
    fonts.append(DEFAULT_FONT)
    return fonts


_WARNED = set()


def _font(name):
    f = FONT_FILES.get(name)
    if f:
        p = _find_font_file(f)
        if p:
            return FontProperties(fname=str(p))
        if name not in _WARNED:
            log.warning("Font '%s' not found - falling back to DejaVu Sans Bold", name)
            _WARNED.add(name)
    return FontProperties(family="DejaVu Sans", weight="bold")


def text_geometry(word, o):
    w = {"lowercase": word.lower(), "UPPERCASE": word.upper(), "Title Case": word.title()}[o.text_case]
    if not w.strip():
        return Polygon()
    return xor_rings(TextPath((0, 0), w, size=10, prop=_font(o.font)).to_polygons())


# ------------------------------------------------------------ icons
def _download(url):
    try:
        import requests as _req
        r = _req.get(url, headers={"User-Agent": "aac-tag-studio/1.3"}, timeout=12)
        r.raise_for_status()
        return r.content
    except ImportError:
        req = urllib.request.Request(url, headers={"User-Agent": "aac-tag-studio/1.3"})
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.read()


def _icon_dirs():
    """Folders to search for icons (writable cache first, then app folder)."""
    dirs = [ICON_DIR]
    app_icons = APP_DIR / "icons"
    if app_icons.resolve() != ICON_DIR.resolve():
        dirs.append(app_icons)
    return dirs


def _local_icon(name):
    """Find a saved icon by exact base name in any icon folder."""
    for d in _icon_dirs():
        for ext in (".png", ".jpg", ".jpeg"):
            p = d / (name + ext)
            if p.exists():
                return p
    return None


# Directory listing cache so local-first scans stay cheap during batch export
_DIR_LIST_CACHE = {}  # dir_str -> (mtime_ns, [filenames])


def _list_icon_files(d):
    """Cached list of image filenames in an icon directory."""
    d = Path(d)
    key = str(d)
    try:
        mt = d.stat().st_mtime_ns
    except OSError:
        return []
    hit = _DIR_LIST_CACHE.get(key)
    if hit and hit[0] == mt:
        return hit[1]
    names = []
    try:
        for p in d.iterdir():
            if p.suffix.lower() in (".png", ".jpg", ".jpeg") and p.is_file():
                names.append(p.name)
    except OSError:
        names = []
    _DIR_LIST_CACHE[key] = (mt, names)
    return names


def _local_icons_for_word(word, variant=0):
    """All local pictures that belong to this word — exact, numbered, and
    ARASAAC-style (word_arasaac_123.png) or any word_* prefix.

    Returns a list of Paths, best match first. Never touches the network.
    """
    word = (word or "").strip()
    if not word:
        return []
    terms = _search_terms(word)
    stems = []
    for t in [word] + list(terms):
        n = safe_name(t)
        if n and n not in stems:
            stems.append(n)
    if not stems:
        return []

    out, seen = [], set()
    v = int(variant)

    def add(p):
        try:
            key = str(p.resolve())
        except Exception:
            key = str(p)
        if key not in seen and p.exists():
            seen.add(key)
            out.append(p)

    # Pass 1 — exact / numbered names (fast path)
    for n in stems:
        if v == 0:
            p = _local_icon(n)
            if p:
                add(p)
        else:
            p = _local_icon(f"{n}_{v}")
            if p:
                add(p)

    # Pass 2 — prefix scan: word_1.png, word_arasaac_123.png, bathroom_*.png …
    # Prefer the requested variant when numbered files exist.
    for d in _icon_dirs():
        for fname in _list_icon_files(d):
            stem = Path(fname).stem.lower()
            for n in stems:
                if stem == n or stem.startswith(n + "_"):
                    add(d / fname)
                    break

    # If a specific variant was requested, try to surface that one first
    if v > 0 and out:
        prefer = []
        rest = []
        tag = f"_{v}"
        for p in out:
            s = p.stem.lower()
            if s.endswith(tag) or f"_{v}_" in s:
                prefer.append(p)
            else:
                rest.append(p)
        if prefer:
            out = prefer + rest
    return out


def _best_local_icon(word, variant=0):
    """Single best local icon path, or None."""
    hits = _local_icons_for_word(word, variant)
    return hits[0] if hits else None


_INDEX_CACHE = {"mtime": None, "data": {}}


def _load_icon_index():
    """In-memory cache of icon_index.json (reloaded only when the file changes),
    so slider drags and batch exports don't re-read the disk on every lookup."""
    try:
        mt = ICON_INDEX_FILE.stat().st_mtime_ns
    except OSError:
        return {}
    if _INDEX_CACHE["mtime"] == mt:
        return _INDEX_CACHE["data"]
    try:
        data = json.loads(ICON_INDEX_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    _INDEX_CACHE["mtime"] = mt
    _INDEX_CACHE["data"] = data
    return data


def _save_icon_index(index):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ICON_INDEX_FILE.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")
        try:
            _INDEX_CACHE["mtime"] = ICON_INDEX_FILE.stat().st_mtime_ns
        except OSError:
            _INDEX_CACHE["mtime"] = None
        _INDEX_CACHE["data"] = index
    except Exception as e:
        log.warning("Could not save icon index: %s", e)


def _index_key(word, variant=0):
    return f"{word.lower().strip()}|{int(variant)}"


def _remember_icon(word, variant, path, term="", pid=None):
    """Record word→file so offline lookup always finds the picture later."""
    index = _load_icon_index()
    index[_index_key(word, variant)] = {
        "file": Path(path).name,
        "term": term,
        "pid": pid,
        "word": word,
    }
    # also index under the bare word for variant 0
    if int(variant) == 0:
        index[word.lower().strip()] = index[_index_key(word, variant)]
    _save_icon_index(index)


def _icon_from_index(word, variant=0):
    index = _load_icon_index()
    entry = index.get(_index_key(word, variant)) or (index.get(word.lower().strip()) if variant == 0 else None)
    if not entry:
        return None
    name = entry.get("file") or ""
    for d in _icon_dirs():
        p = d / name
        if p.exists():
            return p
    return None


def _persist_icon(img_bytes, word, variant, term=""):
    """Write icon under the user's word name (and search term) permanently for offline use.
    Saved in the data icons folder and mirrored into the app icons folder when possible.
    """
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    base = safe_name(word) or "icon"
    fname = f"{base}.png" if int(variant) == 0 else f"{base}_{int(variant)}.png"
    out = ICON_DIR / fname
    out.write_bytes(img_bytes)
    # also keep a copy under the search term so term-based lookup works
    tbase = safe_name(term) if term else ""
    targets = [out]
    if tbase and tbase != base:
        alt = ICON_DIR / (f"{tbase}.png" if int(variant) == 0 else f"{tbase}_{int(variant)}.png")
        try:
            alt.write_bytes(img_bytes)
            targets.append(alt)
        except Exception:
            pass
    # permanent mirror next to the app (survives data-dir changes / reinstalls of settings)
    try:
        app_icons = APP_DIR / "icons"
        if app_icons.resolve() != ICON_DIR.resolve():
            app_icons.mkdir(parents=True, exist_ok=True)
            for src in targets:
                dest = app_icons / src.name
                if not dest.exists() or dest.stat().st_size != src.stat().st_size:
                    dest.write_bytes(src.read_bytes())
    except Exception as e:
        log.debug("Could not mirror icon to app folder: %s", e)
    return out


# ============================================================ AI library (v13.23)
# Everything the AI creates is labelled "AI" in the Library:
#   * pictures are saved as  <word>_ai.png / <word>_ai_2.png …  (a regenerated picture
#     is a NEW file — nothing is overwritten) and are listed under  Library → Pictures → AI
#   * AI-generated words (suggested lists, words the AI command box made) are recorded in
#     ai_library.json and listed under  Library → Words → AI
AI_LIBRARY_FILE = DATA_DIR / "ai_library.json"
_AI_PIC_RE = re.compile(r"_ai(_\d+)?$", re.I)


def is_ai_picture(path_or_name):
    """True when a picture file was created by the AI (name ends in _ai or _ai_<n>)."""
    return bool(_AI_PIC_RE.search(Path(str(path_or_name)).stem))


def ai_picture_word_base(stem):
    """'apple_ai_2' -> 'apple' (used by the Library to map files back to words)."""
    return _AI_PIC_RE.sub("", stem) or stem


def _ai_lib_load():
    try:
        d = json.loads(AI_LIBRARY_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            d.setdefault("words", {})
            d.setdefault("images", {})
            return d
    except Exception:
        pass
    return {"words": {}, "images": {}}


def _ai_lib_save(d):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        AI_LIBRARY_FILE.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.warning("Could not save the AI library record: %s", e)


def ai_register_words(words, kind="generated", topic=""):
    """Record AI-generated words so the Library lists them under  Words → AI.
    kind: 'suggested' | 'command' | 'regenerated' | 'generated'. Returns how many were new."""
    d = _ai_lib_load()
    n = 0
    now = time.strftime("%Y-%m-%d %H:%M")
    for w in words or []:
        w = str(w).strip()
        if not w:
            continue
        key = w.lower()
        if key in d["words"]:      # already labelled AI: keep the original record
            continue
        n += 1
        d["words"][key] = {"word": w, "kind": kind, "topic": topic, "when": now}
    if n:
        _ai_lib_save(d)
    return n


def ai_words():
    """[(word, kind, when)] for every AI-generated word, newest first."""
    d = _ai_lib_load()
    items = [(v.get("word") or k, v.get("kind", "generated"), v.get("when", ""))
             for k, v in d["words"].items()]
    items.sort(key=lambda t: t[2], reverse=True)
    return items


def save_ai_image(img_bytes, word, how="AI", make_default=True):
    """Save an AI-created picture into the Library, labelled AI. Always a new file
    (<word>_ai.png, then _ai_2, _ai_3 …) so regenerating never destroys an earlier picture.
    The newest one becomes the word's picture when make_default is True. Returns the Path."""
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    base = safe_name(word) or "icon"
    n = 1
    while True:
        fname = f"{base}_ai.png" if n == 1 else f"{base}_ai_{n}.png"
        if not (ICON_DIR / fname).exists():
            break
        n += 1
    out = ICON_DIR / fname
    out.write_bytes(img_bytes)
    try:  # permanent mirror next to the app, like every other saved picture
        app_icons = APP_DIR / "icons"
        if app_icons.resolve() != ICON_DIR.resolve():
            app_icons.mkdir(parents=True, exist_ok=True)
            (app_icons / fname).write_bytes(img_bytes)
    except Exception as e:
        log.debug("Could not mirror AI picture to app folder: %s", e)
    if make_default:
        _remember_icon(word, 0, out, term=word)
    d = _ai_lib_load()
    d["images"][fname] = {"word": str(word), "how": how, "when": time.strftime("%Y-%m-%d %H:%M")}
    _ai_lib_save(d)
    log.info("AI picture for '%s' saved to the Library as %s (%s)", word, fname, how)
    return out


def ai_images_for(word):
    """AI pictures already saved for this word, oldest first ([] if none)."""
    base = safe_name(word) or "icon"
    found = []
    try:
        for d in _icon_dirs():
            for f in Path(d).glob(f"{base}_ai*.png"):
                if re.fullmatch(re.escape(base) + r"_ai(_\d+)?", f.stem, flags=re.I) and \
                        f.name.lower() not in {x.name.lower() for x in found}:
                    found.append(f)
    except Exception:
        pass
    found.sort(key=lambda f: (len(f.stem), f.stem))
    return found


def word_has_picture(word):
    """True if a picture for this word already exists locally (no network, no AI)."""
    try:
        if _icon_from_index(word, 0):
            return True
        if _local_icon(safe_name(word)):
            return True
        for t in _search_terms(word)[:3]:
            if _local_icon(safe_name(t)):
                return True
    except Exception:
        pass
    return False


_FAILED = {}  # (key, variant) -> time of last failed online lookup (avoid hammering a dead connection)


def _search_terms(word):
    """Ordered list of search strings that often match a close ARASAAC pictogram.
    Handles custom multi-word phrases by trying the whole phrase, aliases,
    individual tokens, and simple stems."""
    w = word.lower().strip()
    terms = []
    if w in ALIASES:
        terms.append(ALIASES[w])
    terms.append(w)
    # multi-word: try each significant token
    parts = [p for p in w.replace("-", " ").split() if len(p) > 1]
    if len(parts) > 1:
        for p in parts:
            if p in ALIASES:
                terms.append(ALIASES[p])
            terms.append(p)
    # simple plural / singular
    if w.endswith("s") and len(w) > 3 and not w.endswith("ss"):
        terms.append(w[:-1])
    elif not w.endswith("s"):
        terms.append(w + "s")
    # verb stems
    if w.endswith("ing") and len(w) > 5:
        stem = w[:-3]
        terms.append(stem)
        terms.append(stem + "e")
    if w.endswith("ed") and len(w) > 4:
        terms.append(w[:-2])
        terms.append(w[:-1])
    # unique, keep order
    seen, out = set(), []
    for t in terms:
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def find_icon(word, o, override=None, variant=0):
    """Returns (path or None, description).

    Offline-first (always):
      1) user override path
      2) permanent icon_index.json
      3) any local file for this word (exact, numbered, word_arasaac_*, aliases)
    Online ARASAAC is ONLY used when icon_source is Auto AND nothing local
    was found. Use "Find pictures…" in the UI when you want a new download.
    """
    if override and Path(override).exists():
        return Path(override), "custom image"
    if override:
        log.warning("Custom image for '%s' no longer exists: %s", word, override)
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    terms = _search_terms(word)
    key = terms[0] if terms else word.lower().strip()
    v = int(variant)

    # 1) permanent index from previous downloads / saves
    p = _icon_from_index(word, v)
    if p:
        return p, "cached offline icon" + (f" #{v}" if v else "")

    # 2) broad local search — exact, numbered, ARASAAC-style, aliases
    p = _best_local_icon(word, v)
    if p:
        _remember_icon(word, v, p)
        return p, "local icon" + (f" #{v}" if v else "")

    # Also try variant 0 if a higher variant was requested but only base exists
    if v != 0:
        p = _best_local_icon(word, 0)
        if p:
            _remember_icon(word, 0, p)
            return p, "local icon"

    if o.icon_source != SOURCES[0]:
        return None, "no local icon (online lookup is off)"
    if time.time() - _FAILED.get((key, v), 0) < 90:
        return None, "online lookup failed recently (retry in a minute)"

    # 3) ARASAAC online – free for non-commercial use, CC BY-NC-SA, credit ARASAAC
    report_status(f"Downloading a picture for \u201c{word}\u201d\u2026")
    last_err = None
    for term in terms:
        try:
            q = urllib.parse.quote(term)
            data = None
            for endpoint in (
                f"https://api.arasaac.org/v1/pictograms/en/bestsearch/{q}",
                f"https://api.arasaac.org/v1/pictograms/en/search/{q}",
            ):
                try:
                    data = json.loads(_download(endpoint))
                    if data:
                        break
                except Exception as e:
                    last_err = e
                    data = None
            if not data:
                continue
            idx = min(v, len(data) - 1)  # clamp: never silently repeat an earlier picture
            pk = AI_HOOKS.get("pick")
            if pk and v == 0:
                try:
                    idx = int(pk(word, term, data[:8]))
                except Exception as e_ai:
                    log.warning("AI picture pick skipped: %s", e_ai)
                    idx = 0
                if not 0 <= idx < min(len(data), 8):
                    idx = 0
            pid = data[idx]["_id"]
            try:
                img = _download(f"https://api.arasaac.org/v1/pictograms/{pid}?color=false&download=false")
            except Exception as e1:
                log.debug("Primary icon URL failed (%s), trying static URL", e1)
                img = _download(f"https://static.arasaac.org/pictograms/{pid}/{pid}_500.png")
            out = _persist_icon(img, word, v, term=term)
            _remember_icon(word, v, out, term=term, pid=pid)
            log.info("Downloaded + cached icon for '%s' via '%s' variant %d (ARASAAC id %s) → %s",
                     word, term, v, pid, out.name)
            return out, f"online icon #{v} (ARASAAC · {term}) · saved offline"
        except Exception as e:
            last_err = e
            log.debug("Search term '%s' failed: %s", term, e)
    _FAILED[(key, v)] = time.time()
    log.warning("Online icon lookup failed for '%s': %s", word, last_err or "no matches")
    return None, f"online lookup failed ({type(last_err).__name__ if last_err else 'no matches'})"


# ============================================================ preset word lists (v13.10-exp)
# Ready-made lists grouped by synonyms: each inner list is a set of words that
# mean (almost) the same thing, so related words sit together on a board and
# the user can pick the exact word they want to teach/use.
SYNONYM_PRESET_LISTS = {
    "Feelings": [
        ["happy", "glad", "joyful"],
        ["sad", "unhappy", "upset"],
        ["angry", "mad", "furious"],
        ["scared", "afraid", "frightened"],
        ["tired", "sleepy", "exhausted"],
        ["sick", "ill", "unwell"],
        ["hurt", "sore", "in pain"],
        ["calm", "relaxed", "peaceful"],
        ["excited", "thrilled"],
        ["worried", "nervous", "anxious"],
        ["proud"],
        ["bored"],
        ["surprised", "shocked"],
        ["hungry", "starving"],
        ["thirsty"],
        ["hot", "warm"],
        ["cold", "chilly"],
    ],
    "Actions": [
        ["go", "come"],
        ["stop", "wait"],
        ["run", "jog"],
        ["walk", "stroll"],
        ["jump", "leap", "hop"],
        ["eat", "munch", "snack"],
        ["drink", "sip"],
        ["sleep", "nap", "rest"],
        ["look", "see", "watch"],
        ["listen", "hear"],
        ["talk", "speak", "say"],
        ["ask", "question"],
        ["tell", "explain"],
        ["help", "assist"],
        ["get", "take", "grab"],
        ["give", "hand"],
        ["make", "build", "create"],
        ["fix", "repair", "mend"],
        ["open"],
        ["close", "shut"],
        ["push", "pull"],
        ["sit", "sit down"],
        ["stand", "stand up"],
        ["wash", "clean"],
        ["play"],
        ["read"],
        ["write", "draw"],
        ["cook", "bake"],
        ["throw", "catch"],
        ["sing", "dance"],
        ["work", "job"],
        ["finish", "done", "all done"],
    ],
    "Describing words": [
        ["big", "large", "huge"],
        ["little", "small", "tiny"],
        ["fast", "quick", "rapid"],
        ["slow"],
        ["hot", "warm"],
        ["cold", "chilly", "freezing"],
        ["wet"],
        ["dry"],
        ["clean"],
        ["dirty", "messy"],
        ["good", "great", "awesome"],
        ["bad", "terrible"],
        ["yummy", "tasty", "delicious"],
        ["yucky", "gross"],
        ["loud", "noisy"],
        ["quiet", "soft", "silent"],
        ["new"],
        ["old"],
        ["easy", "simple"],
        ["hard", "difficult"],
        ["full"],
        ["empty"],
        ["same"],
        ["different"],
        ["first"],
        ["last"],
    ],
    "Everyday words": [
        ["want", "need"],
        ["like", "love"],
        ["more", "again"],
        ["finished", "all done"],
        ["now"],
        ["later"],
        ["today"],
        ["tomorrow"],
        ["here"],
        ["there"],
        ["up"],
        ["down"],
        ["in"],
        ["out"],
        ["on"],
        ["off"],
        ["yes", "yeah", "ok"],
        ["no", "not"],
        ["please"],
        ["thank you", "thanks"],
        ["hello", "hi"],
        ["goodbye", "bye"],
        ["my turn"],
        ["your turn"],
        ["help", "help me"],
        ["bathroom", "toilet"],
        ["home", "house"],
        ["school"],
    ],
}


def preset_list_names():
    """Names of the built-in synonym-grouped preset lists (v13.10-exp)."""
    return list(SYNONYM_PRESET_LISTS.keys())


def preset_groups(name):
    """Synonym groups for a preset list: [[word, word, ...], ...]."""
    return [list(g) for g in SYNONYM_PRESET_LISTS.get(name, [])]


def preset_words(name):
    """Flat word list for a preset, synonyms kept together in group order."""
    out, seen = [], set()
    for group in SYNONYM_PRESET_LISTS.get(name, []):
        for w in group:
            if w.lower() not in seen:
                seen.add(w.lower())
                out.append(w)
    return out


def get_cached_pictures(word):
    """Fast: pictures already saved for this word (no internet)."""
    word = (word or "").strip()
    if not word:
        return []
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    out, seen = [], set()
    for p in _local_icons_for_word(word, 0):
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            out.append({"path": p, "source": "local", "label": f"Saved \u00b7 {p.name}", "term": word})
    return out


def search_icon_candidates(word, limit=16, progress=None, opensymbols_secret=None):
    """Search free symbol libraries and return a list of candidate pictures.

    Each item: {"path": Path, "source": str, "label": str, "term": str}
    Images are downloaded and saved permanently under icons/.

    Sources (all free for non-commercial / open AAC use):
      - Local cache
      - ARASAAC (CC BY-NC-SA)
      - OpenSymbols (Mulberry / Sclera / Tawasol / more) — needs a free API secret
    """
    word = (word or "").strip()
    if not word:
        return []
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    terms = _search_terms(word)
    seen_paths, out = set(), []

    def add(path, source, label, term=""):
        if path is None:
            return
        path = Path(path)
        if not path.exists():
            return
        key = str(path.resolve())
        if key in seen_paths:
            return
        seen_paths.add(key)
        out.append({"path": path, "source": source, "label": label, "term": term or word})

    # Local files for this word first (exact, numbered, ARASAAC-style, aliases)
    for p in _local_icons_for_word(word, 0):
        add(p, "local", f"Saved · {p.name}", word)

    if progress:
        progress(0.15, "Searching ARASAAC…")

    # ARASAAC — multiple variants.
    # vi is a running counter across search terms so variant numbers stay unique
    # (previously each term restarted at 0 and overwrote the earlier terms' entries).
    vi = 0
    for term in terms[:3]:
        try:
            q = urllib.parse.quote(term)
            data = None
            for endpoint in (
                f"https://api.arasaac.org/v1/pictograms/en/bestsearch/{q}",
                f"https://api.arasaac.org/v1/pictograms/en/search/{q}",
            ):
                try:
                    data = json.loads(_download(endpoint))
                    if data:
                        break
                except Exception:
                    data = None
            if not data:
                continue
            for i, item in enumerate(data[: max(4, limit // 2)]):
                if len(out) >= limit or vi >= 24:
                    break
                pid = item.get("_id") or item.get("id")
                if pid is None:
                    continue
                try:
                    try:
                        img = _download(f"https://api.arasaac.org/v1/pictograms/{pid}?color=false&download=false")
                    except Exception:
                        img = _download(f"https://static.arasaac.org/pictograms/{pid}/{pid}_500.png")
                    # unique variant name so candidates don't overwrite each other
                    fname = f"{safe_name(word)}_arasaac_{pid}.png"
                    dest = ICON_DIR / fname
                    if not dest.exists():
                        dest.write_bytes(img)
                    add(dest, "ARASAAC", f"ARASAAC · {term}", term)
                    _remember_icon(word, vi, dest, term=term, pid=pid)
                    vi += 1
                except Exception as e:
                    log.debug("ARASAAC candidate %s failed: %s", pid, e)
        except Exception as e:
            log.debug("ARASAAC search '%s': %s", term, e)

    if opensymbols_secret and len(out) < limit:
        if progress:
            progress(0.7, "Searching OpenSymbols (Mulberry / Sclera / Tawasol)\u2026")
        try:
            for item in search_opensymbols(word, opensymbols_secret,
                                           limit=limit - len(out),
                                           repos=("mulberry", "sclera", "tawasol")):
                if len(out) >= limit:
                    break
                add(item["path"], item["source"], item["label"], item.get("term", word))
        except OpenSymbolsTokenExpired:
            raise  # the GUI catches this and shows the renewal dialog
        except Exception as e:
            log.debug("OpenSymbols search '%s': %s", word, e)

    if progress:
        progress(1.0, f"{len(out)} picture(s) found")
    log.info("Icon candidates for '%s': %d", word, len(out))
    return out[:limit]


# ============================================================ OpenSymbols (v2 API)
# Free aggregate of open-licensed symbol libraries (Mulberry CC BY-SA, Sclera CC
# BY-NC, Tawasol CC BY-SA, ...). Accepts either a free shared secret from
# opensymbols.org/api (we exchange it for a short-lived token) or a temporary
# access token pasted directly (used as-is until the API rejects it).

class OpenSymbolsTokenExpired(Exception):
    """The OpenSymbols API rejected our access token (HTTP 400/401/403). The user
    needs to fetch a fresh token; the GUI catches this and shows the renewal dialog."""


_opensymbols_token = {"token": None, "exp": 0.0, "is_raw": False}

def _opensymbols_is_access_token(value):
    """Temporary access tokens from opensymbols.org/api start with 'temp::'."""
    return (value or "").startswith("temp::")

def _http_status(exc):
    """Best-effort HTTP status code from a requests or urllib exception."""
    resp = getattr(exc, "response", None)
    if resp is not None:
        return getattr(resp, "status_code", None)
    return getattr(exc, "code", None)  # urllib.error.HTTPError

def _opensymbols_token_get(secret):
    """Return a usable access token for the secret-or-token the user saved."""
    import time as _t
    secret = (secret or "").strip()
    if not secret:
        return None
    if _opensymbols_is_access_token(secret):
        # raw temporary token: use directly; rejections are raised to the GUI
        _opensymbols_token.update(token=secret, exp=0.0, is_raw=True)
        return secret
    now = _t.time()
    if (_opensymbols_token["token"] and not _opensymbols_token["is_raw"]
            and _opensymbols_token["exp"] > now + 300):
        return _opensymbols_token["token"]
    tok, exp = None, now + 20 * 3600
    url = "https://www.opensymbols.org/api/v2/token"
    # POST with JSON body (verified live by third-party integrators); the docs
    # also accept the secret as a query parameter — try both.
    try:
        import requests as _req
        r = _req.post(url, json={"secret": secret},
                      headers={"User-Agent": "aac-tag-studio/1.3"}, timeout=12)
        if r.status_code == 200:
            tok = r.json().get("access_token")
    except Exception as e:
        log.debug("OpenSymbols token POST: %s", e)
    if not tok:
        try:
            q = urllib.parse.quote(secret)
            data = json.loads(_download(f"{url}?secret={q}"))
            tok = (data or {}).get("access_token")
        except Exception as e:
            log.debug("OpenSymbols token GET: %s", e)
    if tok:
        _opensymbols_token.update(token=tok, exp=exp, is_raw=False)
        return tok
    return None


def _opensymbols_search_request(term, repo, token):
    """One API search call. Raises OpenSymbolsTokenExpired on HTTP 400/401/403."""
    q = urllib.parse.quote(f"{term} repo:{repo}")
    url = (f"https://www.opensymbols.org/api/v2/symbols?q={q}"
           f"&locale=en&safe=1&access_token={urllib.parse.quote(token)}")
    try:
        raw = _download(url)
    except Exception as e:
        status = _http_status(e)
        if status in (400, 401, 403):
            raise OpenSymbolsTokenExpired(
                f"OpenSymbols rejected the access token ({status})")
        raise
    data = json.loads(raw)
    if isinstance(data, dict) and data.get("token_expired"):
        raise OpenSymbolsTokenExpired("OpenSymbols access token expired")
    return data if isinstance(data, list) else []


def _opensymbols_query(term, repo, secret):
    """Search one term/repo pair, refreshing a secret-based token once on 401.
    Raises OpenSymbolsTokenExpired when a pasted raw token is rejected."""
    token = _opensymbols_token_get(secret)
    if not token:
        return []
    try:
        return _opensymbols_search_request(term, repo, token)
    except OpenSymbolsTokenExpired:
        if _opensymbols_is_access_token(secret):
            raise
        _opensymbols_token["token"] = None  # force a refresh
        token = _opensymbols_token_get(secret)
        if not token:
            return []
        try:
            return _opensymbols_search_request(term, repo, token)
        except OpenSymbolsTokenExpired:
            log.debug("OpenSymbols: still rejected after token refresh")
            return []


def _rasterize_svg(svg_bytes, size=512):
    """Render SVG bytes to PNG bytes via resvg-py (prebuilt wheel, no system libs).
    Returns None when resvg-py is not installed or rendering fails."""
    try:
        import resvg_py
    except ImportError:
        return None
    try:
        return resvg_py.svg_to_bytes(svg_string=svg_bytes.decode("utf-8", "replace"),
                                     width=size, height=size)
    except Exception as e:
        log.debug("SVG rasterize failed: %s", e)
        return None


def search_opensymbols(word, secret, limit=10, repos=("mulberry", "sclera", "tawasol")):
    """Search OpenSymbols libraries. Returns candidate dicts like search_icon_candidates.

    Each item: {"path": Path, "source": str, "label": str, "term": str}.
    Images are downloaded (SVGs rasterized to PNG) and saved under icons/.
    Results are round-robin merged so no single library hogs the quota.
    Raises OpenSymbolsTokenExpired when a pasted raw token is rejected.
    """
    word = (word or "").strip()
    if not word or not (secret or "").strip():
        return []
    secret = secret.strip()
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    seen = set()
    per_repo = {repo: [] for repo in repos}
    # cap downloads per library: enough for the round-robin merge, no more
    per_repo_cap = max(2, limit // max(len(repos), 1) + 1)
    terms = _search_terms(word)[:2] or [word.lower()]
    expired = False
    for repo in repos:
        for term in terms:
            try:
                data = _opensymbols_query(term, repo, secret)
            except OpenSymbolsTokenExpired:
                expired = True
                break
            for item in data:
                if len(per_repo[repo]) >= per_repo_cap:
                    break
                img_url = item.get("image_url")
                if not img_url:
                    continue
                name = item.get("name") or term
                rkey = (item.get("repo_key") or repo).lower()
                safe = safe_name(f"{word}_os_{rkey}_{name}")[:80] or safe_name(word)
                dest = ICON_DIR / f"{safe}.png"
                key = str(dest.resolve())
                if key in seen:
                    continue
                seen.add(key)
                if dest.exists():
                    # already cached from an earlier search — no download needed
                    per_repo[repo].append({"path": dest,
                                           "source": "OpenSymbols \u00b7 " + rkey.title(),
                                           "label": rkey.title() + " \u00b7 " + name,
                                           "term": term})
                    continue
                try:
                    img = _download(img_url)
                    # Mulberry serves SVG — rasterize to PNG (needs resvg-py wheel)
                    if img[:20].lstrip().startswith(b"<") or b"<svg" in img[:200].lower():
                        img = _rasterize_svg(img)
                        if not img:
                            continue
                    if not img[:8].startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM", b"RIFF")):
                        continue
                    dest.write_bytes(img)
                    per_repo[repo].append({"path": dest,
                                           "source": "OpenSymbols \u00b7 " + rkey.title(),
                                           "label": rkey.title() + " \u00b7 " + name,
                                           "term": term})
                except Exception as e:
                    log.debug("OpenSymbols candidate failed: %s", e)
        if expired:
            break
    # round-robin merge so no single library hogs the quota
    out = []
    idx = 0
    while len(out) < limit:
        added = False
        for repo in repos:
            if idx < len(per_repo[repo]) and len(out) < limit:
                out.append(per_repo[repo][idx])
                added = True
        idx += 1
        if not added:
            break
    if expired and not out:
        raise OpenSymbolsTokenExpired("OpenSymbols rejected the access token")
    return out


def prefetch_icons(words, o, overrides=None, variants=None, search_as=None, progress=None):
    """Download and permanently cache an icon for every word while online.
    Returns {'ok': [(word, info)], 'failed': [(word, err)], 'skipped': [word]}."""
    overrides, variants = overrides or {}, variants or {}
    search_as = search_as or {}
    rep = {"ok": [], "failed": [], "skipped": []}
    total = max(len(words), 1)
    for i, w in enumerate(words):
        if progress:
            progress(i / total, f"Caching icon for '{w}'…")
        if overrides.get(w):
            rep["skipped"].append(w)
            continue
        try:
            lookup = (search_as.get(w) or w).strip() or w
            var = variants.get(w, 0)
            path, info = find_icon(lookup, o, None, var)
            # also bind the display word to the same file in the index
            if path and lookup.lower() != w.lower():
                _remember_icon(w, var, path, term=lookup)
            if path:
                rep["ok"].append((w, info))
            else:
                rep["failed"].append((w, info))
        except Exception as e:
            log.exception("Prefetch failed for '%s'", w)
            rep["failed"].append((w, f"{type(e).__name__}: {e}"))
    if progress:
        progress(1.0, "Icon cache complete")
    log.info("Prefetch: %d cached, %d failed, %d skipped (custom images)",
             len(rep["ok"]), len(rep["failed"]), len(rep["skipped"]))
    return rep


_ICON_GEO = {}
_ICON_GEO_LAYERS = {}


def image_geometry(path):
    """Trace an icon image into solid 2D geometry suitable for 3D printing.

    Line-art AAC pictograms are dilated so thin strokes become printable walls
    instead of vanishing into a few red speckles in the slicer.
    """
    ck = (str(path), Path(path).stat().st_mtime_ns)
    if ck in _ICON_GEO:
        return _ICON_GEO[ck]
    gray = _icon_gray(path)
    # Dark drawing on light background → foreground white
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    g = _trace_mask(bw, 2)
    if g.is_empty:
        log.warning("Tracing %s produced no shapes (blank or very faint image?)", Path(path).name)
    if len(_ICON_GEO) >= 200:  # bounded cache: drop the oldest entries
        for k in list(_ICON_GEO)[:50]:
            del _ICON_GEO[k]
    _ICON_GEO[ck] = g
    return g


def _icon_gray(path):
    """The shared picture prep (white composite, greyscale, 900 px, blurred) that
    both the classic tracer and the layered tracer start from."""
    im = Image.open(path)
    # Palette images with transparency need explicit RGBA conversion
    if im.mode in ("P", "LA"):
        im = im.convert("RGBA")
    elif im.mode != "RGBA":
        im = im.convert("RGBA")
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
    gray = np.array(Image.alpha_composite(bg, im).convert("L"))
    h, w = gray.shape
    # Work at a consistent high resolution for clean contours
    target = 900
    s = target / max(h, w)
    gray = cv2.resize(gray, (max(1, int(w * s)), max(1, int(h * s))),
                      interpolation=cv2.INTER_AREA)
    gray = cv2.copyMakeBorder(gray, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)
    return cv2.GaussianBlur(gray, (3, 3), 0)


def _trace_mask(bw, dilate_iters):
    """Thicken + trace a binary foreground mask into printable geometry.
    dilate_iters=2 is the classic single-colour behaviour, kept byte-identical."""
    # Thicken thin line-art so extrusion produces real walls (not dust)
    # 5x5 ellipse ≈ solid enough for ~0.4 mm features after fit-to-tag
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    bw = cv2.dilate(bw, kernel, iterations=dilate_iters)
    # Close small gaps inside filled regions
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel, iterations=1)
    cnts, _ = cv2.findContours(bw, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    rings = []
    for c in cnts:
        if cv2.contourArea(c) < 20:
            continue
        pts = c[:, 0, :].astype(float)
        pts[:, 1] *= -1  # image Y down → geometry Y up
        rings.append(pts)
    g = xor_rings(rings)
    if not g.is_empty:
        g = g.simplify(0.6, preserve_topology=True)
    return g


def image_geometry_layers(path, n=2):
    """Trace a picture into tone layers, darkest first (v15.8).

    Layer 0 is EXACTLY the classic image_geometry() result (the Otsu-dark mask,
    traced the classic way), so a 1-layer build is unchanged. Layer 1 (when the
    picture has one) is the midtone band between the Otsu level and the paper —
    the fill that used to be background while its outline merged into the dark
    blob. Layers are disjoint by construction of the bands."""
    ck = (str(path), Path(path).stat().st_mtime_ns, int(n))
    if ck in _ICON_GEO_LAYERS:
        return _ICON_GEO_LAYERS[ck]
    gray = _icon_gray(path)
    t0, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    classic = _trace_mask(bw, 2)
    layers = [classic]
    if int(n) >= 2 and not classic.is_empty:
        # Regime A — the picture HAS near-black line art: split the outlines from
        # the fills, which is where the "one big blob" came from.
        t_dark = int(min(96, max(40, 0.5 * t0)))
        dark_a = _trace_mask(cv2.inRange(gray, 0, t_dark), 2) if t_dark < t0 else Polygon()
        frac = (dark_a.area / classic.area) if classic.area else 0.0
        done = False
        if not dark_a.is_empty and frac >= 0.02:
            mid = _trace_mask(cv2.inRange(gray, t_dark + 1, int(t0)), 1)
            if not mid.is_empty:
                mid = mid.difference(dark_a)
            if not mid.is_empty:
                layers = [dark_a, mid]
                done = True
        if not done:
            # Regime B — no line art to separate: the extra layer is the light
            # detail just above the classic foreground.
            t1 = t0 + 0.45 * (250.0 - t0)
            if t1 - t0 >= 12:
                mid = _trace_mask(cv2.inRange(gray, int(t0) + 1, int(t1)), 1)
                if not mid.is_empty:
                    mid = mid.difference(classic)
                    if not mid.is_empty:
                        layers = [classic, mid]
    if len(_ICON_GEO_LAYERS) >= 200:
        for k in list(_ICON_GEO_LAYERS)[:50]:
            del _ICON_GEO_LAYERS[k]
    _ICON_GEO_LAYERS[ck] = layers
    return layers


def _fit_like(geoms, ref, cx, cy, w, h):
    """fit() a whole stack of layers with the transform computed from ONE
    reference geometry, so the layers stay registered with each other."""
    if ref is None or ref.is_empty:
        return [Polygon() for _ in geoms]
    minx, miny, maxx, maxy = ref.bounds
    s = min(w / max(maxx - minx, 1e-6), h / max(maxy - miny, 1e-6))
    rs = sh_scale(ref, s, s, origin=(0, 0))
    rminx, rminy, rmaxx, rmaxy = rs.bounds
    dx, dy = cx - (rminx + rmaxx) / 2, cy - (rminy + rmaxy) / 2
    return [sh_translate(sh_scale(g, s, s, origin=(0, 0)), dx, dy) for g in geoms]


# ------------------------------------------------------------ tag construction
def make_shape(o):
    R = o.size / 2
    name = o.shape
    if name == "Rounded square":
        r = R * 0.28
        return box(-R + r, -R + r, R - r, R - r).buffer(r, 32)
    if name == "Square":
        return box(-R * 0.92, -R * 0.92, R * 0.92, R * 0.92)
    if name == "Hexagon":
        pts = [(R * 1.04 * np.cos(np.radians(60 * i)), R * 1.04 * np.sin(np.radians(60 * i))) for i in range(6)]
        return Polygon(pts).buffer(-2).buffer(2, 32)
    if name == "Star":
        pts = []
        for i in range(10):
            a = np.radians(-90 + i * 36)
            r = R if i % 2 == 0 else R * 0.42
            pts.append((r * np.cos(a), r * np.sin(a)))
        return Polygon(pts).buffer(0.6, 8).buffer(-0.6, 8)
    if name == "Heart":
        # parametric heart scaled to fit in circle of radius R
        t = np.linspace(0, 2 * np.pi, 120)
        x = 16 * np.sin(t) ** 3
        y = 13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t)
        s = R * 0.95 / max(x.max() - x.min(), y.max() - y.min()) * 2
        pts = list(zip(x * s, (y - y.mean()) * s))
        return Polygon(pts).buffer(0.4, 8).buffer(-0.4, 8)
    if name == "Shield":
        pts = [
            (-R * 0.75, R * 0.85), (R * 0.75, R * 0.85), (R * 0.75, 0),
            (0, -R * 0.95), (-R * 0.75, 0),
        ]
        return Polygon(pts).buffer(R * 0.12, 16)
    # Circle (default) or Oval
    g = Point(0, 0).buffer(R, 128)
    if name == "Oval":
        from shapely.affinity import scale as _sc
        g = _sc(g, 1.15, 0.78, origin=(0, 0))
    return g


def build_tag(word, o, override=None, variant=0, icon_word=None, auto=True):
    """Returns (base_geom, ink_geoms, body_geom, info_text). ink_geoms is a list,
    darkest first: one entry for a classic tag, two when the picture has a
    midtone layer too (v15.8, up to 3 colours per tag including the body).
    icon_word: optional alternate phrase used only for picture search
    (lets custom/unusual words still find a related AAC pictogram).
    auto=False (v13.14): no online lookup and no AI-drawn icon — only pictures you already
    have saved locally; otherwise the tag shows the word alone."""
    shape = make_shape(o)
    minx, miny, maxx, maxy = shape.bounds
    hr = o.hole_d / 2
    outline, hole = shape, Polygon()
    h = (o.hole or "None").strip()
    # Back-compat: older saves used "Top" / "Left"
    if h in ("Top", "Top loop"):
        c = (0, maxy + hr - 0.5)
        outline = shape.union(Point(c).buffer(hr + 2.5, 48))
        hole = Point(c).buffer(hr, 32)
    elif h in ("Left", "Left loop"):
        c = (minx - (hr - 0.5), 0)
        outline = shape.union(Point(c).buffer(hr + 2.5, 48))
        hole = Point(c).buffer(hr, 32)
    elif h.startswith("Through"):
        # Hole punched through the body near the TOP edge — no protruding loop.
        # Tight to the top so the icon/word area is not cut out.
        margin = max(0.8, hr * 0.35)
        top_y = maxy - (hr + margin)
        hole = Point(0, top_y).buffer(hr, 32)
        if not shape.contains(hole):
            hr2 = min(hr, o.size * 0.10)
            hole = Point(0, maxy - (hr2 + 0.8)).buffer(hr2, 32)
    # "None" → no hole at all
    body = outline.difference(hole)

    # Snap connectors: tab on the right, matching notch on the left (sentence strips)
    if getattr(o, "connectors", False):
        R = o.size / 2
        tab_w, tab_h = 3.2, max(6.0, o.size * 0.22)
        # right tab (protrudes)
        tab = box(maxx - 0.2, -tab_h / 2, maxx + tab_w, tab_h / 2)
        # left socket (cut out) — slightly larger for clearance
        sock = box(minx - 0.4, -tab_h / 2 - 0.25, minx + tab_w + 0.2, tab_h / 2 + 0.25)
        body = body.union(tab).difference(sock).buffer(0)
        outline = outline.union(tab)

    ink = Polygon()
    if o.ring and o.ring_w > 0:
        ink = shape.buffer(-1.6).difference(shape.buffer(-1.6 - o.ring_w))

    k = o.size / 40.0
    icon, info = None, ""
    icon_layers = None          # v15.8: tone layers of the raster icon (image coords)
    if o.layout != "Word only":
        lookup = (icon_word or word).strip() or word
        ai_draw = AI_HOOKS.get("draw")
        if ai_draw and not override and word.strip().lower() in AI_HOOKS.get("force_draw", ()):
            try:
                icon = ai_draw(word)
                info = "AI-drawn icon"
            except Exception as e:
                log.warning("AI drawing failed for '%s': %s", word, e)
                icon = None
        if icon is None:
            p, info = find_icon(lookup, o if auto else _dc_replace(o, icon_source=SOURCES[1]), override, variant)
            if p:
                try:
                    want2 = (int(round(float(getattr(o, "ink_layers", 1) or 1))) >= 2
                             and not getattr(o, "high_contrast", False))
                    if want2:
                        icon_layers = image_geometry_layers(p, 2)
                        icon = icon_layers[0]
                    else:
                        icon = image_geometry(p)
                except Exception as e:
                    log.warning("Could not read icon %s: %s", p, e)
                    info = f"icon unreadable ({type(e).__name__})"
        if auto and (icon is None or icon.is_empty) and ai_draw and AI_HOOKS.get("auto_draw") and not override:
            try:
                icon = ai_draw(word)
                info = (info + " - " if info else "") + "AI-drawn icon instead"
            except Exception as e:
                log.info("AI auto-draw not available for '%s': %s", word, e)
                icon = None
        if icon_word and icon_word.strip().lower() != word.strip().lower():
            info = (info + f" · searched as “{lookup}”").strip(" ·")
    layout = o.layout
    if icon is None or icon.is_empty:
        icon = None
        if layout == "Icon + word":
            layout = "Word only"
            info = (info + " - showing word only").strip(" -")
        elif layout == "Icon only":
            info = (info + " - nothing to show").strip(" -")

    ts, isc = o.text_scale, o.icon_scale
    parts = []
    if layout == "Icon + word":
        icon = fit(icon, 0, 7 * k, 20 * k * isc, 14 * k * isc)
        parts = [icon, fit(text_geometry(word, o), 0, -8.8 * k, 24 * k * ts, 7.5 * k * ts)]
    elif layout == "Icon only":
        if icon is not None:
            icon = fit(icon, 0, 0, 24 * k * isc, 24 * k * isc)
            parts = [icon]
    else:
        parts = [fit(text_geometry(word, o), 0, 0, 27 * k * ts, 12 * k * ts)]
    # v15.8: fit the tone layers with the icon's own transform so they stay registered
    lay_fit = None
    if icon_layers and len(icon_layers) > 1 and icon is not None:
        if layout == "Icon + word":
            lay_fit = _fit_like(icon_layers, icon_layers[0], 0, 7 * k, 20 * k * isc, 14 * k * isc)
        elif layout == "Icon only":
            lay_fit = _fit_like(icon_layers, icon_layers[0], 0, 0, 24 * k * isc, 24 * k * isc)
    # Picture boldness slider: 0 = solid printable default, right = bolder, left = finer.
    if icon is not None and parts:
        bold = float(o.icon_bold or 0.0)
        # Base thickness so line-art always extrudes as real walls (not dust)
        base = 0.18 * k
        use_bold = base + bold
        # Never thinner than a hairline (~0.05 mm scaled)
        use_bold = max(0.05 * k, use_bold)
        grown = icon.buffer(use_bold, join_style=1, resolution=16)
        if not grown.is_empty:
            parts[0] = grown
        if lay_fit:
            grown_layers = [g.buffer(use_bold, join_style=1, resolution=16) for g in lay_fit]
            if len(grown_layers) > 1:
                grown_layers[1] = grown_layers[1].difference(grown_layers[0])
            lay_fit = grown_layers
    for g in parts:
        if g is not None and not g.is_empty:
            ink = ink.union(g)
    # v15.8: the midtone layer is whatever the layers add beyond the classic ink
    mid_ink = Polygon()
    if lay_fit and len(lay_fit) > 1:
        mid_ink = lay_fit[1].difference(ink)

    ink = ink.intersection(body).buffer(0)
    inks = [ink]
    if not mid_ink.is_empty:
        mid_ink = mid_ink.intersection(body).buffer(0)
        if not mid_ink.is_empty:
            inks.append(mid_ink)
    if ink.is_empty:
        log.warning("'%s': nothing to draw (empty ink layer)", word)
    style = o.style
    all_ink = inks[0]
    for _g in inks[1:]:
        all_ink = all_ink.union(_g)
    if style == "Outline" and not ink.is_empty:
        # keep only a thin outline of the ink shapes (of ALL layers together)
        rim = max(0.35, min(1.2, o.size * 0.02))
        outlined = all_ink.buffer(rim).difference(all_ink.buffer(-rim * 0.15))
        if not outlined.is_empty:
            inks = [outlined.intersection(body).buffer(0)]
            all_ink = inks[0]
        base = body.difference(all_ink).buffer(0)
    elif style in ("Raised", "Sunken"):
        base = body  # full body; ink sits on top or is recessed in mesh stage
    else:
        # Flat inlay – every ink layer is cut out of the body
        base = body.difference(all_ink).buffer(0)
    return base, inks, body, info or "text only"


def extrude(geom, height):
    ms = []
    for p in polys(geom):
        try:
            ms.append(trimesh.creation.extrude_polygon(p.simplify(0.01), height))
        except Exception as e:
            log.error("Extrude failed for one shape: %s: %s (is 'mapbox-earcut' installed?)", type(e).__name__, e)
    return trimesh.util.concatenate(ms) if ms else None


def _fix_print_orientation(mesh, face_down=False):
    """Seat the part on the bed (Z=0) and orient it so the design reads correctly.

    v14.3 fix (Cody's prints came out backwards AND upside down): the old code
    flipped BOTH axes (-x,-y) — a 180° rotation. A rotation cannot correct a
    face-down design: seen from the design face it stays mirror-backwards, and
    the Y flip made it upside down too; it was also applied to face-up styles
    that needed no flip at all.

    Correct rules:
    * face_down (Flat inlay / Outline — design sits on the FIRST layers
      against the plate): mirror LEFT-RIGHT only, (x,y,z) → (-x,y,z). Turning
      the finished tag over mirrors it back, so word + picture read exactly
      as in the preview, upright, with the top loop still at the top.
      (trimesh keeps winding/volume correct under this reflection.)
    * face-up (Raised / Sunken / anything else — design on the top face):
      no XY change at all; it already reads correctly from above.
    The on-screen preview uses the original 2D geometry and is unaffected.
    """
    if mesh is None:
        return None
    if face_down:
        T = np.eye(4)
        T[0, 0] = -1.0
        mesh.apply_transform(T)
    zmin = float(mesh.bounds[0][2])
    if abs(zmin) > 1e-6:
        mesh.apply_translation([0.0, 0.0, -zmin])
    return mesh


def _design_depth(o):
    """Thickness of the design (ink) on the first layers. Top of the tag stays solid."""
    d = float(getattr(o, "design_depth", 0.6) or 0.6)
    # keep at least one typical layer, leave room for a solid top
    d = max(0.4, min(d, max(0.4, o.thickness - 0.4)))
    return d


def make_meshes(base, inks, o, full_body=None):
    """Build body + ink meshes for the slicer.

    Flat inlay / Outline (face-down dual colour):
      - Ink only on the BOTTOM (first layers), height = design_depth
      - Body has matching cut-outs only in that bottom slab
      - TOP of the tag is solid body colour (blank) — no letter holes through
      - XY is flipped for correct reading on the designed face
    Preview still uses unflipped 2D geometry from build_tag().
    full_body: outline with hole only (no ink cut) — needed for the solid top slab.
    """
    ink_list = list(inks) if isinstance(inks, (list, tuple)) else [inks]
    style = o.style
    depth = _design_depth(o)
    solid_top = full_body if full_body is not None else base

    if style in ("Flat inlay", "Outline"):
        # Bottom: body with design cut out + ink filling those cut-outs
        mb_bot = extrude(base, depth)
        mi_list = [extrude(g, depth) for g in ink_list]
        # Top: full solid body (no design cut-outs) so the very top layers are blank
        top_h = max(0.4, o.thickness - depth)
        mb_top = extrude(solid_top, top_h)
        if mb_top is not None:
            mb_top.apply_translation([0, 0, depth])
        if mb_bot is not None and mb_top is not None:
            mb = trimesh.util.concatenate([mb_bot, mb_top])
        else:
            mb = mb_bot or mb_top
    elif style == "Raised":
        # Raised stays face-up: full body base, ink on top
        mb = extrude(base, o.thickness)
        mi_list = []
        for g in ink_list:
            m1 = extrude(g, o.raise_h)
            if m1 is not None:
                m1.apply_translation([0, 0, o.thickness])
            mi_list.append(m1)
    elif style == "Sunken":
        mb = extrude(base, o.thickness)
        sunk = min(depth, max(0.3, o.thickness - 0.4))
        mi_list = [extrude(g, sunk) for g in ink_list]
    else:
        mb = extrude(base, o.thickness)
        mi_list = [extrude(g, depth) for g in ink_list]

    # v15.68 dropped BOTH interface caps to kill the slicer fragments, but
    # that left the base an open shell (1,102 open edges on the test tag),
    # which a slicer preview shows as hollow/empty in the middle. v15.71
    # keeps the bottom slab's top cap — it geometrically covers the seam,
    # so the base still looks solid — and drops ONLY the top slab's bottom
    # cap. No coincident-cap pair remains to fragment (singles stay 0),
    # open edges fall to the top slab's bottom rim only, volume unchanged.
    # Ink is a single slab and needs no change.
    if style in ("Flat inlay", "Outline") and mb is not None:
        try:
            if mb_bot is not None and mb_top is not None:
                z = mb_top.vertices[mb_top.faces][:, :, 2]
                keep = ~np.all(np.abs(z - depth) < 1e-9, axis=1)
                mb_top.update_faces(keep)
                mb_top.remove_unreferenced_vertices()
                mb = trimesh.util.concatenate([mb_bot, mb_top])
        except Exception as e:
            log.warning("Interface cap cleanup skipped: %s", e)

    # Magnet recess into the TOP solid (after flip this is still the non-design face)
    if getattr(o, "magnet_recess", False) and mb is not None:
        mag_d = min(8.0, max(4.0, o.size * 0.18))
        mag_h = min(2.2, max(1.0, o.thickness * 0.45))
        try:
            cyl = trimesh.creation.cylinder(radius=mag_d / 2, height=mag_h, sections=48)
            # recess from the top face downward
            zmax = float(mb.bounds[1][2])
            cyl.apply_translation([0, 0, zmax - mag_h / 2])
            mb = _safe_diff(mb, cyl)
        except Exception as e:
            log.warning("Magnet recess skipped: %s", e)

    # v14.3: only the face-down styles get the left-right mirror; face-up
    # styles (Raised, Sunken) must reach the bed exactly as designed.
    _face_down = style in ("Flat inlay", "Outline")
    mb = _fix_print_orientation(mb, face_down=_face_down)
    mi_list = [_fix_print_orientation(m1, face_down=_face_down) for m1 in mi_list]
    return mb, mi_list


def _safe_diff(a, b):
    """Boolean difference with fallback if manifold engine unavailable."""
    try:
        return a.difference(b)
    except Exception:
        try:
            return a.difference(b, engine="blender")
        except Exception as e:
            log.warning("Mesh difference failed (%s) — returning original body", e)
            return a


def safe_name(w):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in w.lower()) or "tag"


_WIN_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def clean_filename(name, default="export"):
    """v14.8: make a user-typed export name safe on Windows/macOS/Linux.
    Keeps spaces and normal punctuation; drops characters files can't have,
    trailing dots/spaces, and reserved device names. Never returns ''."""
    name = "".join(c for c in str(name or "") if c not in '<>:"/\\|?*' and ord(c) >= 32)
    name = name.strip().rstrip(". ")
    for ext in (".3mf", ".stl", ".pdf"):      # the app adds the extension itself
        if name.lower().endswith(ext):
            name = name[: -len(ext)].rstrip(". ")
    if not name:
        name = default
    elif name.lower() in _WIN_RESERVED:
        name += "_"
    return name[:120]


def _part(name, mesh, mat):
    return (name, mesh.vertices, mesh.faces, mat)


def export_words(words, out_dir, o, overrides=None, variants=None, stl=True, threemf=True,
                 plate=False, plate_w=220.0, progress=None, search_as=None,
                 category_colors=False, plate_name="plate", plate_h=None, auto=True):
    """Returns {'notes': [...], 'failed': [(word, error)], 'files': [paths]}

    v14.8: plate_name names the plate files ({plate_name}.3mf / _base.stl /
    _ink.stl) instead of always 'plate'; words whose file names collide
    (e.g. 'Mom' and 'mom!') get _2, _3... instead of overwriting each other;
    the plate warns (in rep['notes']) when it is taller than plate_h."""
    plate_name = clean_filename(plate_name, "plate")
    _used = set()

    def _unique(n):
        base_n, k = n, 2
        while n in _used:
            n = f"{base_n}_{k}"
            k += 1
        _used.add(n)
        return n
    overrides, variants = overrides or {}, variants or {}
    search_as = search_as or {}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # default materials; may be overridden per-word when category_colors is on
    try:
        from aac_boards import category_color, category_for
    except Exception:
        category_color = lambda w: o.body_color
        category_for = lambda w: ""
    rep = {"notes": [], "failed": [], "files": []}
    plate_items = []
    t0 = time.time()
    for i, w in enumerate(words):
        if progress:
            progress(i / max(len(words), 1), f"Building '{w}' ...")
        try:
            # per-word body colour
            body_c = o.body_color
            ink_c = o.ink_color
            if getattr(o, "high_contrast", False):
                body_c, ink_c = "#111111", "#FFFFFF"
            elif category_colors:
                body_c = category_color(w)
            base, inks, body_full, info = build_tag(
                w, o, overrides.get(w), variants.get(w, 0),
                icon_word=search_as.get(w), auto=auto)
            mb, mi_list = make_meshes(base, inks, o, full_body=body_full)
            mats = [("body", body_c), ("ink", ink_c)]
            if len(inks) > 1:
                mats.append(("ink2", getattr(o, "ink2_color", "#8a8a8a")))
            if mb is None:
                raise RuntimeError("could not build the tag body (see log)")
            # v15.68: Flat inlay / Outline bases are intentionally open at
            # the 0.6 mm slab seam (interface caps removed, see make_meshes),
            # so watertightness is not a useful check for those styles.
            if not mb.is_watertight and o.style not in ("Flat inlay", "Outline"):
                log.warning("'%s': body mesh is not watertight - check it in your slicer", w)
            cat = category_for(w) if category_colors else ""
            note = f"{w}: {info}" + (f" [{cat}]" if cat else "")
            rep["notes"].append(note)
            n = _unique(safe_name(w))
            if plate:
                plate_items.append((n, mb, mi_list, mats))
                continue
            if stl:
                mb.export(out_dir / f"{n}_base.stl")
                rep["files"].append(out_dir / f"{n}_base.stl")
                for li, m1 in enumerate(mi_list):
                    if m1 is not None:
                        nm = f"{n}_ink.stl" if li == 0 else f"{n}_ink{li + 1}.stl"
                        m1.export(out_dir / nm)
                        rep["files"].append(out_dir / nm)
            if threemf:
                parts = [_part(f"{n}_base", mb, 0)]
                for li, m1 in enumerate(mi_list):
                    if m1 is not None:
                        parts.append(_part(f"{n}_ink" if li == 0 else f"{n}_ink{li + 1}",
                                           m1, li + 1))
                write_3mf(out_dir / f"{n}.3mf", parts, mats)
                rep["files"].append(out_dir / f"{n}.3mf")
        except Exception as e:
            log.exception("Failed to build '%s'", w)
            rep["failed"].append((w, f"{type(e).__name__}: {e}"))

    if plate and plate_items:
        try:
            # v3.6.0-exp: biggest tags first so rows pack tighter
            def _area(item):
                (bx0, by0, _), (bx1, by1, _) = item[1].bounds
                return (bx1 - bx0) * (by1 - by0)
            plate_items.sort(key=_area, reverse=True)
            x = y = rowh = 0.0
            gap = 4.0
            parts, all_b, all_i = [], [], []
            for n, mb, mi_list, pmats in plate_items:
                (bx0, by0, _), (bx1, by1, _) = mb.bounds
                w, h = bx1 - bx0, by1 - by0
                if x > 0 and x + w > plate_w:
                    x, y, rowh = 0.0, y + rowh + gap, 0.0
                off = [x - bx0, y - by0, 0]
                mb.apply_translation(off)
                parts.append((f"{n}_base", mb, pmats[0][1]))
                all_b.append(mb)
                for li, m1 in enumerate(mi_list or []):
                    if m1 is not None:
                        m1.apply_translation(off)
                        parts.append((f"{n}_ink" if li == 0 else f"{n}_ink{li + 1}",
                                      m1, pmats[li + 1][1]))
                        all_i.append(m1)
                x += w + gap
                rowh = max(rowh, h)
            total_h = y + rowh
            if plate_h and total_h > plate_h:
                rep["notes"].append(
                    f"Plate is {total_h:.0f} mm deep - taller than the {plate_h:.0f} mm bed; "
                    "split the tags over two plates or lower the tag size.")
            if stl:
                trimesh.util.concatenate(all_b).export(out_dir / f"{plate_name}_base.stl")
                rep["files"].append(out_dir / f"{plate_name}_base.stl")
                if all_i:
                    trimesh.util.concatenate(all_i).export(out_dir / f"{plate_name}_ink.stl")
                    rep["files"].append(out_dir / f"{plate_name}_ink.stl")
            if threemf:
                # per-part materials so each tag keeps its own colours on the plate
                plate_mats, mat_idx = [], {}

                def _pmat(color):
                    if color not in mat_idx:
                        mat_idx[color] = len(plate_mats)
                        plate_mats.append((f"color{len(plate_mats)}", color))
                    return mat_idx[color]

                mparts = [(name, mesh.vertices, mesh.faces, _pmat(color))
                          for name, mesh, color in parts]
                write_3mf(out_dir / f"{plate_name}.3mf", mparts, plate_mats)
                rep["files"].append(out_dir / f"{plate_name}.3mf")
        except Exception as e:
            log.exception("Failed to build the print plate")
            rep["failed"].append(("(plate)", f"{type(e).__name__}: {e}"))
    if progress:
        progress(1.0, "Done")
    log.info("Exported %d word(s) in %.1fs: %d file(s), %d failure(s)",
             len(words), time.time() - t0, len(rep["files"]), len(rep["failed"]))
    return rep
