"""AI features for AAC Tag Studio — LOCAL ONLY (v3.12.0-exp).

No cloud, no API keys, no accounts. Everything runs on this PC.

Backends (in order of preference):
  1. local_ai/ folder: GGUF models run by the llama.cpp binary package
     (llama-cli) dropped into local_ai/. One-click downloads in the AI tab.
  2. llama-cpp-python, when installed.
  3. Ollama (fallback, only used when it is actually running).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from aac_common import DATA_DIR, log

# v13.26: ONE local inference at a time. Cody's log showed two or three
# llama-cli processes (an AI task, another task, the setup probe) running
# at once on the same CPU — each starved the others until the biggest job
# hit its timeout. Every on-device engine run (text calls, model probes,
# SD Turbo) now takes this lock; the rest queue up and run at full speed.
_INFER_LOCK = threading.RLock()

API_URL = os.environ.get("AAC_AI_URL", "https://api.anthropic.com/v1/messages")
API_VERSION = "2023-06-01"
# Cloud defaults (optional)
FAST_MODEL = "claude-haiku-4-5"
SMART_MODEL = "claude-sonnet-4-5"
# Local defaults — small, JSON-friendly, runs on most PCs with 8GB+ RAM
LOCAL_MODEL = os.environ.get("AAC_LOCAL_MODEL", "qwen2.5:3b-instruct")
LOCAL_SMART_MODEL = os.environ.get("AAC_LOCAL_SMART_MODEL", "qwen2.5:3b-instruct")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
CONFIG_FILE = DATA_DIR / "ai_config.json"
CACHE_FILE = DATA_DIR / "ai_cache.json"
AI_ICON_DIR = DATA_DIR / "ai_icons"
# v3.12.0-exp: user-managed local AI folder (ships with the program)
def _default_local_ai_dir():
    # In a PyInstaller one-file exe, __file__ points inside the extraction
    # temp dir (sys._MEIPASS), which is deleted when the app exits — a model
    # downloaded there would vanish and re-download every launch. Keep it in
    # the persistent per-user data dir instead. From source, keep the folder
    # next to the program so developers can drop files in by hand.
    if getattr(sys, "frozen", False):
        return DATA_DIR / "local_ai"
    return Path(__file__).resolve().parent / "local_ai"


LOCAL_AI_DIR = _default_local_ai_dir()
# The local AI stack (v15.0 — one text model + SD Turbo for pictures):
#   TEXT  — Qwen2.5-3B-Instruct Q4_K_M (~2.1 GB): word lists, help, review.
#   Images — SD Turbo (separate stack under local_ai/sd_turbo/).
# Engine: llama.cpp b11575 Windows CPU x64 binaries (see download_llama_cpp).
# Official Qwen GGUF: Qwen/Qwen2.5-3B-Instruct-GGUF → qwen2.5-3b-instruct-q4_k_m.gguf
TEXT_GGUF_REPO = "Qwen/Qwen2.5-3B-Instruct-GGUF"
TEXT_GGUF_FILE = "qwen2.5-3b-instruct-q4_k_m.gguf"
# Back-compat aliases (older callers used SMART/FAST/RECOMMENDED names).
SMART_GGUF_REPO, SMART_GGUF_FILE = TEXT_GGUF_REPO, TEXT_GGUF_FILE
FAST_GGUF_REPO, FAST_GGUF_FILE = TEXT_GGUF_REPO, TEXT_GGUF_FILE
DEFAULT_GGUF_REPO, DEFAULT_GGUF_FILE = TEXT_GGUF_REPO, TEXT_GGUF_FILE
FALLBACK_GGUF_REPO, FALLBACK_GGUF_FILE = TEXT_GGUF_REPO, TEXT_GGUF_FILE
RECOMMENDED_GGUF_REPO, RECOMMENDED_GGUF_FILE = TEXT_GGUF_REPO, TEXT_GGUF_FILE
# llama.cpp release that powers the local engine ("the wheel" — a self-contained
# binary package dropped into local_ai/, no pip compiling).
LLAMA_CPP_TAG = "b11575"
LLAMA_CPP_ZIP = f"llama-{LLAMA_CPP_TAG}-bin-win-cpu-x64.zip"
LLAMA_CPP_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_CPP_TAG}/{LLAMA_CPP_ZIP}"
LLAMA_CPP_DIR_NAME = f"llama-{LLAMA_CPP_TAG}-bin-win-cpu-x64"


class AIError(Exception):
    """A problem the user can understand (no model, offline, bad reply...)."""


# ============================================================ secure key storage
_KEYRING_SERVICE = "AAC Tag Studio"
_KEYRING_ACCOUNT = "anthropic_api_key"


def _keyring_get():
    """Read the API key from the OS credential store (None if unavailable)."""
    try:
        import keyring
        return keyring.get_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT) or ""
    except Exception:
        return ""


def _keyring_set(value):
    """Store the API key in the OS credential store. Returns True on success."""
    try:
        import keyring
        if value:
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT, value)
        else:
            try:
                keyring.delete_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT)
            except Exception:
                pass
        return True
    except Exception as e:
        log.debug("Keyring unavailable: %s", e)
        return False


# ============================================================ config
class Config:
    def __init__(self):
        self.backend = "local"  # "local" | "cloud"
        self.key = ""
        self.fast_model = LOCAL_MODEL
        self.smart_model = LOCAL_SMART_MODEL
        self.cloud_fast = FAST_MODEL
        self.cloud_smart = SMART_MODEL
        self.pick_best = True
        self.auto_draw = False  # v15.31: was True; automatic draws loaded the models on every preview/export
        self.local_ready = False  # model confirmed present
        self.load()

    def load(self):
        try:
            d = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            d = {}
        except Exception as e:
            log.warning("AI config unreadable (%s) - using defaults", e)
            d = {}
        self.backend = d.get("backend") or "local"
        if self.backend not in ("local", "cloud"):
            self.backend = "local"
        # API key: OS credential store first, then the config file (legacy),
        # then the environment. A file-stored key is migrated on next save().
        self.key = _keyring_get() or d.get("key", "") or os.environ.get("ANTHROPIC_API_KEY", "")
        self.fast_model = d.get("fast_model") or (LOCAL_MODEL if self.backend == "local" else FAST_MODEL)
        self.smart_model = d.get("smart_model") or (LOCAL_SMART_MODEL if self.backend == "local" else SMART_MODEL)
        self.cloud_fast = d.get("cloud_fast") or FAST_MODEL
        self.cloud_smart = d.get("cloud_smart") or SMART_MODEL
        self.pick_best = bool(d.get("pick_best", True))
        self.auto_draw = bool(d.get("auto_draw", False))  # v15.31: was True
        self.local_ready = bool(d.get("local_ready", False))

    def save(self):
        # Prefer the OS credential store; only fall back to the plain file
        # when secure storage is unavailable on this machine.
        key_in_file = "" if _keyring_set(self.key) else self.key
        try:
            CONFIG_FILE.write_text(json.dumps({
                "backend": self.backend,
                "key": key_in_file,
                "fast_model": self.fast_model,
                "smart_model": self.smart_model,
                "cloud_fast": self.cloud_fast,
                "cloud_smart": self.cloud_smart,
                "pick_best": self.pick_best,
                "auto_draw": self.auto_draw,
                "local_ready": self.local_ready,
            }, indent=2), encoding="utf-8")
        except Exception as e:
            log.warning("Could not save AI config: %s", e)

    @property
    def ready(self):
        if self.backend == "local":
            return bool(self.local_ready)
        return bool(self.key.strip())


cfg = Config()


def get_config():
    return cfg


# ============================================================ cache + failure back-off
_cache = None


def _cache_load():
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            _cache = {}
    return _cache


def cache_get(key):
    return _cache_load().get(key)


def cache_set(key, value):
    c = _cache_load()
    c[key] = value
    if len(c) > 3000:
        for k in list(c)[:500]:
            c.pop(k, None)
    try:
        CACHE_FILE.write_text(json.dumps(c), encoding="utf-8")
    except Exception as e:
        log.debug("AI cache not saved: %s", e)


def _hash(*parts):
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()


_FAIL = {}


def _recently_failed(key, secs=120):
    return time.time() - _FAIL.get(key, 0) < secs


# ============================================================ Ollama local runtime
# ============================================================ v14.8 robustness helpers
class _Run:
    """Result of _run_quiet (decoded text, like subprocess.CompletedProcess)."""
    def __init__(self, returncode, stdout, stderr):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _run_quiet(cmd, timeout, cwd=None):
    """Run a helper program the safe way (v14.8).

    - stdin is closed: a program that waits for typed input (llama-cli's chat
      mode does) ends instead of hanging until the timeout.
    - no console window flashing up on Windows.
    - output decoded as UTF-8 with replacement: the old text=True used the
      Windows code page, and one curly quote / emoji from the model raised an
      uncaught UnicodeDecodeError that failed the whole AI job.
    Raises subprocess.TimeoutExpired / OSError like subprocess.run does."""
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
              timeout=timeout)
    if cwd:
        kw["cwd"] = str(cwd)
    if sys.platform.startswith("win"):
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    r = subprocess.run(cmd, **kw)
    return _Run(r.returncode,
                (r.stdout or b"").decode("utf-8", "replace"),
                (r.stderr or b"").decode("utf-8", "replace"))


def _report_progress(progress, label, done, total, state):
    """Throttled progress line: at most ~2 a second, and only when the percent moved."""
    if not progress:
        return
    now = time.time()
    pct = (done * 100 // total) if total else -1
    if total and pct == state.get("pct"):
        return
    if now - state.get("t", 0) < 0.5 and pct < 100:
        return
    state["pct"], state["t"] = pct, now
    progress(f"{label}… {pct}%" if total else f"{label}… {done // 1_000_000} MB")


def _download_resumable(url, part, label, progress=None, expected=0, retries=5, timeout=60):
    """Download url -> part (a .part file), resuming after a dropped connection.

    v14.8: a failed or interrupted download used to throw everything away (the 5 GB
    SD Turbo model started again from zero). Now the .part file is kept and the
    next attempt continues from where it stopped, with a few automatic retries.
    Returns the number of bytes in the finished file; raises the last error."""
    part = Path(part)
    part.parent.mkdir(parents=True, exist_ok=True)
    last = None
    state = {}
    for attempt in range(retries):
        have = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": "AAC-Tag-Studio"}
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                code = getattr(r, "status", 200)
                clen = int(r.headers.get("Content-Length") or 0)
                if have and code == 206:
                    total, mode = have + clen, "ab"
                else:                                  # fresh, or the server ignored Range
                    have, total, mode = 0, (clen or expected), "wb"
                done = have
                with open(part, mode) as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        _report_progress(progress, label, done, total, state)
            if total and done < total:
                raise OSError(f"connection dropped at {done // 1_000_000} of {total // 1_000_000} MB")
            return done
        except urllib.error.HTTPError as e:
            if e.code == 416 and have:                 # we already have the whole file
                return have
            if e.code in (401, 403, 404):
                raise
            last = e
        except Exception as e:
            last = e
        log.warning("download attempt %d/%d for %s failed: %s", attempt + 1, retries, label, last)
        if progress and attempt + 1 < retries:
            progress(f"{label}: connection problem — retrying ({attempt + 2}/{retries})…")
        time.sleep(min(2 ** attempt, 10))
    raise last if last else OSError("download failed")


def _run_llama(cli, model_path, prompt, n_predict, temp, timeout, extra=()):
    """Run one non-interactive completion and return the _Run result (v14.8).

    Newer llama.cpp releases moved one-shot text completion to llama-completion and
    made llama-cli an interactive chat; prefer llama-completion when it sits next to
    llama-cli, otherwise run llama-cli with chat mode switched off (-no-cnv), and
    retry without that flag for builds that don't know it."""
    cli = Path(cli)
    comp = cli.with_name("llama-completion" + cli.suffix)
    exe = comp if comp.exists() else cli
    base = [str(exe), "-m", str(model_path), "-n", str(n_predict), "-c", "4096",
            "--temp", str(temp), "-p", prompt, "--no-display-prompt", *extra]
    cmds = [base] if exe is comp else [base + ["-no-cnv"], base]
    r = None
    for cmd in cmds:
        r = _run_quiet(cmd, timeout, cwd=exe.parent)
        both = (r.stderr or "") + (r.stdout or "")
        if r.returncode != 0 and any(h in both.lower() for h in
                                     ("unknown argument", "invalid argument", "unrecognized")):
            continue
        break
    return r




def _ollama_http(path, method="GET", body=None, timeout=30):
    url = OLLAMA_HOST.rstrip("/") + path
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ollama_alive():
    try:
        _ollama_http("/api/tags", timeout=3)
        return True
    except Exception:
        return False


def ollama_has_model(name):
    try:
        data = _ollama_http("/api/tags", timeout=10)
        models = [m.get("name", "") for m in data.get("models", [])]
        # names may be "qwen2.5:3b-instruct" or with digest suffix
        base = name.split(":")[0]
        for m in models:
            if m == name or m.startswith(name + "/") or m.startswith(name + ":"):
                return True
            if name in m or (base in m and ":" in name and name.split(":")[-1] in m):
                return True
        return any(name in m or m.startswith(base) for m in models)
    except Exception as e:
        log.debug("ollama_has_model: %s", e)
        return False


# v13.23: a half-downloaded model (app closed mid-download) used to sit in local_ai/ looking
# like a real model, and every AI call then died with "failed to load model".
# v13.25: also quarantine models that pass the size/magic check but fail to *load*
# in llama-cli (corrupt internal data). They are skipped until the user re-downloads.
_WARNED_BAD = set()
_MIN_MODEL_MB = (("3b", 1500), ("1.5b", 900), ("0.5b", 350))     # real Q4_K_M files are ~986 MB and ~398 MB
_PROBE_CACHE = {}          # path str → True (loads) | False (broken)
_RUNTIME_BAD = set()       # basenames that failed to load this session
_BAD_MARK_FILE = LOCAL_AI_DIR / ".broken_models.json"


def _load_broken_marks():
    try:
        data = json.loads(_BAD_MARK_FILE.read_text(encoding="utf-8"))
        return set(data.get("names") or [])
    except Exception:
        return set()


def _save_broken_marks(names):
    try:
        LOCAL_AI_DIR.mkdir(parents=True, exist_ok=True)
        _BAD_MARK_FILE.write_text(json.dumps({"names": sorted(names)}, indent=2), encoding="utf-8")
    except Exception as e:
        log.debug("Could not save broken-model marks: %s", e)


def mark_model_broken(path, reason="failed to load"):
    """Remember that this GGUF is unusable so we stop retrying it every call."""
    try:
        name = Path(path).name
    except Exception:
        name = str(path)
    _RUNTIME_BAD.add(name.lower())
    _PROBE_CACHE[str(path)] = False
    marks = _load_broken_marks()
    marks.add(name)
    _save_broken_marks(marks)
    if name not in _WARNED_BAD:
        _WARNED_BAD.add(name)
        log.warning("Model %s marked broken (%s) — will skip until re-downloaded", name, reason)


def clear_model_broken(path):
    """Call after a successful re-download so the model is tried again."""
    try:
        name = Path(path).name
        key = str(path)
    except Exception:
        return
    _RUNTIME_BAD.discard(name.lower())
    _PROBE_CACHE.pop(key, None)
    marks = _load_broken_marks()
    if name in marks:
        marks.discard(name)
        _save_broken_marks(marks)


def rehabilitate_false_marks():
    """Drop persistent broken-marks for files that look complete on disk.

    An earlier probe timeout used to quarantine a working 0.5B model. Size-OK
    GGUFs get another chance; truly tiny / non-GGUF files stay marked.
    """
    marks = _load_broken_marks()
    if not marks:
        return
    kept = set()
    for name in list(marks):
        p = LOCAL_AI_DIR / name
        if not p.is_file():
            continue
        try:
            size = p.stat().st_size
            with open(p, "rb") as f:
                magic = f.read(4)
        except OSError:
            kept.add(name)
            continue
        if magic == b"GGUF" and size >= 50 * 1_000_000:
            _RUNTIME_BAD.discard(name.lower())
            _PROBE_CACHE.pop(str(p), None)
            log.info("Cleared false broken-mark for %s (file looks complete)", name)
            continue
        kept.add(name)
    _save_broken_marks(kept)


def gguf_problem(path):
    """None if the file looks like a complete GGUF model, else a plain-English reason."""
    try:
        path = Path(path)
        size = path.stat().st_size
        with open(path, "rb") as f:
            magic = f.read(4)
    except OSError as e:
        return f"can't be read ({e})"
    if magic != b"GGUF":
        return "isn't a valid model file (the download is incomplete or damaged)"
    n = path.name.lower().replace("_", ".")
    for key, mn in _MIN_MODEL_MB:
        if key in n and size < mn * 1_000_000:
            return (f"is only {size // 1_000_000} MB but should be about {mn}+ MB "
                    "— the download was interrupted")
    if size < 50 * 1_000_000:
        return f"is only {size // 1_000_000} MB — far too small to be a model"
    # Only session hard-fails hide a size-OK file. Persistent marks from an
    # old timed-out probe are NOT fatal here — a real call can clear them.
    if path.name.lower() in _RUNTIME_BAD:
        return "couldn't be loaded by the engine (damaged — re-download it)"
    return None


def probe_model(path, progress=None, timeout=180):
    """Try to load the GGUF with llama-cli. Returns True if it answers.

    IMPORTANT: a *timeout* is NOT permanent damage. First load on a slow CPU
    can take well over a minute, and the same model may already have answered
    real prompts. Only an explicit "failed to load model" marks the file broken.
    """
    path = Path(path)
    key = str(path)
    if key in _PROBE_CACHE:
        return _PROBE_CACHE[key]
    # Size / magic only — do not treat a prior timeout mark as fatal here;
    # those false marks are cleared below when we get a clean answer.
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            magic = f.read(4)
    except OSError as e:
        log.warning("probe_model cannot read %s: %s", path.name, e)
        _PROBE_CACHE[key] = False
        return False
    if magic != b"GGUF":
        mark_model_broken(path, "isn't a valid model file")
        _PROBE_CACHE[key] = False
        return False
    n = path.name.lower().replace("_", ".")
    for tag, mn in _MIN_MODEL_MB:
        if tag in n and size < mn * 1_000_000:
            mark_model_broken(path, f"only {size // 1_000_000} MB (need ~{mn}+)")
            _PROBE_CACHE[key] = False
            return False
    if size < 50 * 1_000_000:
        mark_model_broken(path, f"only {size // 1_000_000} MB")
        _PROBE_CACHE[key] = False
        return False
    # Runtime hard-fail only (explicit load error earlier this session)
    if path.name.lower() in _RUNTIME_BAD:
        _PROBE_CACHE[key] = False
        return False
    cli = None
    try:
        cli = _find_llama_cli()
    except Exception:
        pass
    if not cli:
        _PROBE_CACHE[key] = True
        return True
    if progress:
        progress(f"Testing {path.name} (first load can take a minute)…")
    prompt = "<|im_start|>user\nSay OK.<|im_end|>\n<|im_start|>assistant\n"
    # Serialized like every other engine run (v13.26): a probe competing
    # with a real AI call slows both down and can time both out.
    with _INFER_LOCK:
        try:
            r = _run_llama(cli, path, prompt, 3, 0, timeout)
        except subprocess.TimeoutExpired:
            # Slow PC — keep available; do NOT quarantine
            log.warning("probe_model timed out for %s (keeping available — not marking broken)",
                        path.name)
            _PROBE_CACHE[key] = True
            return True
        except OSError as e:
            log.warning("probe_model OS error for %s: %s", path.name, e)
            return True
    both = ((r.stderr or "") + "\n" + (r.stdout or "")).strip()
    if "failed to load model" in both or "error loading model" in both:
        log.warning("probe_model failed for %s: %s", path.name, both[-300:])
        mark_model_broken(path, "failed to load")
        _PROBE_CACHE[key] = False
        return False
    if r.returncode != 0:
        log.warning("probe_model exit %s for %s: %s", r.returncode, path.name, both[-200:])
        _PROBE_CACHE[key] = True
        return True
    _PROBE_CACHE[key] = True
    clear_model_broken(path)
    return True


def capabilities(probe=False):
    """What local AI can actually do right now.

    Returns a dict:
      text   – at least one GGUF loads (word helpers, chat, search terms)
      draw   – same as text (simple geometry icons via the language model)
      image  – SD Turbo engine + weights are present (real picture generation)
      models – list of Path for GGUFs that load
      damaged – list of (Path, reason)
      engine – llama-cli Path or None
      status – short human line for the UI
    """
    try:
        rehabilitate_false_marks()
    except Exception:
        pass
    info = scan_local_ai_dir()
    working = []
    damaged = list(info.get("damaged") or [])
    for m in info.get("models") or []:
        if probe:
            if probe_model(m):
                working.append(m)
            else:
                damaged.append((m, "couldn't be loaded by the engine (damaged — re-download it)"))
            continue
        # Use cache / marks only — no slow probe unless asked
        key = str(m)
        if key in _PROBE_CACHE:
            if _PROBE_CACHE[key]:
                working.append(m)
            else:
                damaged.append((m, "couldn't be loaded by the engine (damaged — re-download it)"))
        elif m.name.lower() in _RUNTIME_BAD or m.name in _load_broken_marks():
            damaged.append((m, "couldn't be loaded by the engine (damaged — re-download it)"))
        else:
            working.append(m)  # not probed yet — optimistic until first real call
    # de-dupe damaged
    seen_d = set()
    clean_d = []
    for p, why in damaged:
        k = Path(p).name.lower()
        if k not in seen_d:
            seen_d.add(k)
            clean_d.append((Path(p), why))
    sd_ok = False
    try:
        sd_ok = bool(sd_ready())
    except Exception:
        sd_ok = False
    text_ok = bool(working) and bool(info.get("llama_cli") or _find_llama_cli())
    bits = []
    if working:
        bits.append("text: " + ", ".join(p.name for p in working[:3]))
    else:
        bits.append("text: none")
    bits.append("images: SD Turbo" if sd_ok else "images: none (need SD Turbo)")
    return {
        "text": text_ok,
        "draw": text_ok,          # geometry icons use the text model
        "image": sd_ok,           # real pictures need SD Turbo
        "models": working,
        "damaged": clean_d,
        "engine": info.get("llama_cli"),
        "status": " · ".join(bits),
    }


def scan_local_ai_dir():
    """Look in local_ai/ (and one level of subfolders) for GGUF models and llama.cpp.

    Recognises:
      - any *.gguf (prefers qwen2.5-coder / instruct)
      - llama-cli, llama-server, main.exe
      - llama-b11575 / llama-b* folders or binaries from llama.cpp releases
    """
    result = {"dir": LOCAL_AI_DIR, "models": [], "damaged": [], "llama_cli": None, "readme": False,
              "llama_build": None}
    # The chat binary is matched by EXACT name only (v13.22 fix). The
    # llama.cpp release folder also ships llama-bench.exe and
    # llama-batched-bench.exe, which sort BEFORE llama-cli.exe; the old
    # loose prefix / "llama-b" matching picked the benchmark tool up as
    # the chat engine, so every AI call ran the wrong program — those
    # were the recurring "llama.cpp errors", and the Ollama fallback then
    # buried the real problem under its own "not running" error.
    _CLI_EXACT = {"llama-cli", "llama-cli.exe"}
    _CLI_LEGACY = {"main", "main.exe"}  # very old llama.cpp one-shot binary
    _found = {"exact": None, "legacy": None}
    try:
        LOCAL_AI_DIR.mkdir(parents=True, exist_ok=True)
        if getattr(sys, "frozen", False) and not (LOCAL_AI_DIR / "README.txt").exists():
            try:
                (LOCAL_AI_DIR / "README.txt").write_text(
                    "AAC Tag Studio - local AI folder\n\n"
                    "The local AI stack lives here (all on-device, no account):\n"
                    "  - qwen2.5-0.5b-instruct-q4_k_m.gguf   (fast helpers)\n"
                    "  - qwen2.5-coder-1.5b-instruct-q4_k_m.gguf  (smart: drawing + explaining)\n"
                    "  - llama-b11575-bin-win-cpu-x64/  (the llama.cpp engine)\n"
                    "  - sd_turbo/  (SD Turbo image AI: sd-cli engine + sd_turbo.safetensors)\n"
                    "Use the AI tab download buttons to fetch each piece automatically,\n"
                    "or drop the files in by hand. This folder is kept between launches.\n",
                    encoding="utf-8")
            except Exception:
                pass

        def _scan(folder, depth=0):
            try:
                entries = sorted(folder.iterdir())
            except Exception:
                return
            for f in entries:
                name_l = f.name.lower()
                if f.is_file():
                    if f.suffix.lower() == ".gguf" and f.stat().st_size > 1024 * 1024:
                        why = gguf_problem(f)
                        if why:
                            result["damaged"].append((f, why))
                            if f.name not in _WARNED_BAD:
                                _WARNED_BAD.add(f.name)
                                log.warning("Model file %s %s — ignoring it", f.name, why)
                        else:
                            result["models"].append(f)
                    elif name_l in _CLI_EXACT:
                        if _found["exact"] is None:
                            _found["exact"] = f
                    elif name_l in _CLI_LEGACY:
                        if _found["legacy"] is None:
                            _found["legacy"] = f
                    elif name_l == "readme.txt":
                        result["readme"] = True
                elif f.is_dir() and depth < 2:
                    if "llama-b" in name_l or name_l in ("bin", "llama.cpp", "llama"):
                        result["llama_build"] = result["llama_build"] or f.name
                    _scan(f, depth + 1)

        _scan(LOCAL_AI_DIR)

        def _rank(p):
            n = p.name.lower()
            score = 0
            if "coder" in n:
                score += 10
            if "instruct" in n:
                score += 5
            if "qwen2.5" in n or "qwen2_5" in n:
                score += 3
            if "1.5b" in n or "1_5b" in n:
                score += 2
            if "q4" in n:
                score += 1
            return -score

        result["models"] = sorted(result["models"], key=_rank)
        result["llama_cli"] = _found["exact"] or _found["legacy"]
    except Exception as e:
        log.warning("scan_local_ai_dir: %s", e)
    result["ready"] = bool(result["models"])
    return result


def find_model(role="fast"):
    """Pick the local text GGUF (v15.0: only Qwen2.5-3B Instruct).

    Prefers a 3B file; otherwise any usable GGUF in local_ai/. role is kept for
    call-site compatibility (fast/smart both resolve to the same model).
    """
    info = scan_local_ai_dir()
    models = info.get("models") or []
    if not models:
        return None
    for p in models:
        n = p.name.lower()
        if "3b" in n or "3_b" in n:
            return p
    # Prefer the official recommended filename if present under any casing
    for p in models:
        if "qwen" in p.name.lower() and "instruct" in p.name.lower():
            return p
    return models[0]


def _pip_install(args, progress=None, label=None):
    """Run pip install; return (ok, stderr_tail). Never raises.

    v14.8: inside the packaged EXE sys.executable is the app itself, so
    "python -m pip" used to start ANOTHER copy of the app (extra windows, a
    frozen-looking install). pip is simply not available there."""
    if getattr(sys, "frozen", False):
        return False, "pip isn't available inside the packaged app."
    cmd = [sys.executable, "-m", "pip", "install", "--no-input",
           "--disable-pip-version-check", *args]
    log.info("pip: %s", " ".join(cmd))
    if progress and label:
        progress(label)
    try:
        r = _run_quiet(cmd, 420)
        out = (r.stderr or "") + "\n" + (r.stdout or "")
        if r.returncode != 0:
            log.warning("pip failed (code %s): %s", r.returncode, out[-1500:])
            return False, out[-1200:]
        return True, ""
    except Exception as e:
        log.warning("pip exception: %s", e)
        return False, str(e)


def ensure_llama_cpp(progress=None):
    """Make sure there is a working local AI engine.

    Order (v14.8):
      1. Native llama-cli binary already in local_ai/            -> "cli"
      2. Windows: download the llama.cpp binary package (no pip, no compiling) -> "cli"
      3. llama-cpp-python already installed                      -> True
      4. Prebuilt wheel via pip (NEVER compiles; skipped inside the packaged app)
    Raises AIError with plain-English fixes when nothing works.
    """
    try:
        cli = _find_llama_cli()
    except Exception:
        cli = None
    if cli:
        log.info("Local AI engine: llama-cli binary %s", cli)
        if progress:
            progress(f"Engine: llama.cpp binary ({cli.name})")
        return "cli"

    dl_err = None
    if sys.platform.startswith("win"):
        try:
            download_llama_cpp(progress=progress)
            return "cli"
        except Exception as e:
            dl_err = e
            log.warning("llama.cpp binary download failed: %s", e)

    try:
        import llama_cpp  # noqa
        return True
    except Exception:
        pass

    ok, err = False, ""
    if not getattr(sys, "frozen", False):
        ok, err = _pip_install(
            ["--upgrade", "llama-cpp-python", "--only-binary=:all:",
             "--extra-index-url", "https://abetlen.github.io/llama-cpp-python/whl/cpu"],
            progress=progress, label="Installing prebuilt CPU engine…")
        if ok:
            try:
                import llama_cpp  # noqa
                return True
            except Exception:
                pass

    hint = "Could not set up the local AI engine.\n\n"
    if dl_err is not None:
        hint += f"{dl_err}\n\n"
    hint += (
        "Easiest fix: check your internet, then click “Download llama.cpp engine "
        f"({LLAMA_CPP_TAG})” in the AI tab and “Set up local AI” again.\n\n"
        "Other options:\n"
        "  • Put a Windows llama-cli.exe (not Linux) into the local_ai folder\n"
        "  • Install Ollama from https://ollama.com yourself, then set up again\n")
    if err:
        last = [ln for ln in err.splitlines() if ln.strip()][-3:]
        hint += "\nPip said:\n  " + "\n  ".join(last)
    log.error("ensure_llama_cpp failed: %s", (err or str(dl_err) or "unknown")[-500:])
    raise AIError(hint)


def _download_error(what, filename, e):
    """Plain-English download failure. Cody's log filled up with raw
    '<urlopen error [Errno 11001] getaddrinfo failed>' dumps while his
    internet/DNS was down — say what it actually means instead."""
    s = str(e)
    if ("getaddrinfo" in s or "NameResolution" in s or "Failed to resolve" in s
            or "urlopen error" in s or "Network is unreachable" in s):
        return (f"{what} ({filename}) couldn't be downloaded — there's no internet "
                "connection right now (the download address couldn't be reached). "
                "Anything already in local_ai/ still works offline; try the "
                "download again when you're back online.")
    return f"{what} ({filename}): {e}"


def _download_one_gguf(repo, filename, progress=None, label=None):
    """Download a single GGUF into local_ai/. Returns Path or raises AIError."""
    LOCAL_AI_DIR.mkdir(parents=True, exist_ok=True)
    dest = LOCAL_AI_DIR / filename
    if dest.exists():
        why = gguf_problem(dest)
        if not why:
            return dest
        log.warning("Existing %s %s — replacing it with a fresh download", filename, why)
        try:
            dest.unlink()
        except OSError as e:
            raise AIError(f"{filename} is damaged and can't be replaced ({e}). "
                          "Close anything using it, delete it from local_ai/, and try again.")
    url = f"https://huggingface.co/{repo}/resolve/main/{filename}"
    msg = label or f"Downloading {filename}…"
    log.info(msg)
    if progress:
        progress(msg)
    part = dest.with_name(filename + ".part")      # written here first; renamed only when complete
    try:
        done = _download_resumable(url, part, f"Downloading {filename}", progress)
        # Part files are never in the broken-marks list, so only check size/magic
        size = part.stat().st_size
        with open(part, "rb") as f:
            magic = f.read(4)
        if magic != b"GGUF":
            part.unlink(missing_ok=True)          # unusable: start fresh next time
            raise AIError("the downloaded file isn't a valid model (incomplete or damaged)")
        if size < 50 * 1_000_000:
            part.unlink(missing_ok=True)
            raise AIError(f"the downloaded file is only {size // 1_000_000} MB — far too small")
        part.replace(dest)
        clear_model_broken(dest)
        _PROBE_CACHE.pop(str(dest), None)
        return dest
    except Exception as e:
        # the .part file is KEPT on a dropped connection so the next click resumes it
        raise AIError(_download_error("Model download failed", filename, e))


def download_gguf_model(progress=None):
    """Download the text model (Qwen2.5-3B-Instruct Q4_K_M, ~2.1 GB) into local_ai/."""
    info = scan_local_ai_dir()
    existing = find_model("smart")
    if existing and ("3b" in existing.name.lower() or "3_b" in existing.name.lower()):
        return existing
    if existing and existing.name.lower() == TEXT_GGUF_FILE.lower():
        return existing
    return _download_one_gguf(
        TEXT_GGUF_REPO, TEXT_GGUF_FILE, progress=progress,
        label="Downloading Qwen2.5-3B Instruct (~2.1 GB, one time)…")


def download_fast_gguf(progress=None):
    """Back-compat: 0.5B is gone — downloads the same Qwen 3B text model."""
    return download_gguf_model(progress=progress)


def download_llama_cpp(progress=None):
    """Download the llama.cpp engine package into local_ai/ (v13.14-exp).

    Fetches llama-b11575-bin-win-cpu-x64.zip from the llama.cpp GitHub
    releases and extracts it to local_ai/llama-b11575-bin-win-cpu-x64/, so
    llama-cli.exe is there for _find_llama_cli(). This package is the
    engine — no pip, no compiling. Returns the llama-cli path.
    Raises AIError (plain English) on failure.
    """
    global _llama_cli_path
    existing = _find_llama_cli()
    if existing:
        return existing
    LOCAL_AI_DIR.mkdir(parents=True, exist_ok=True)
    dest_dir = LOCAL_AI_DIR / LLAMA_CPP_DIR_NAME
    zip_path = LOCAL_AI_DIR / LLAMA_CPP_ZIP
    if progress:
        progress(f"Downloading llama.cpp engine ({LLAMA_CPP_TAG}, Windows CPU)…")
    try:
        _download_resumable(LLAMA_CPP_URL, zip_path, "Downloading llama.cpp engine", progress)
        import zipfile
        if not zipfile.is_zipfile(zip_path):
            zip_path.unlink(missing_ok=True)
            raise OSError("the downloaded engine file is damaged - try again")
    except Exception as e:
        raise AIError(_download_error("Engine download failed", "llama.cpp", e)) from e
    # Extract safely (no path escapes), keeping the release folder layout.
    try:
        import zipfile
        dest_dir.mkdir(parents=True, exist_ok=True)
        base = dest_dir.resolve()
        with zipfile.ZipFile(zip_path) as z:
            for member in z.namelist():
                target = (dest_dir / member).resolve()
                if target != base and base not in target.parents:
                    raise AIError(f"Engine zip contains an unsafe path: {member}")
            z.extractall(dest_dir)
    except AIError:
        raise
    except Exception as e:
        raise AIError(f"Could not unpack the engine zip: {e}") from e
    finally:
        try:
            zip_path.unlink()
        except Exception:
            pass
    _llama_cli_path = None  # rescan so the new binary is found
    cli = _find_llama_cli()
    if not cli:
        raise AIError("Engine unpacked but no llama-cli.exe was found inside it.")
    if not sys.platform.startswith("win"):
        # zipfile drops the executable bit; Windows doesn't need it, POSIX does
        try:
            cli.chmod(0o755)
        except Exception:
            pass
    log.info("llama.cpp %s ready: %s", LLAMA_CPP_TAG, cli)
    if progress:
        progress(f"Engine ready: {cli.name}")
    return cli


_gguf_llm = None
_gguf_path = None
_llama_cli_path = None  # cached Path to llama-cli when python package is missing


# ============================================================ SD Turbo (image AI, v13.21)
# Images and words are DIFFERENT stacks (Cody's rule):
#   IMAGES — Stable Diffusion Turbo, run by stable-diffusion.cpp (sd-cli):
#            a self-contained engine zip + one model file, installed with
#            the same one-click method as llama.cpp, inside local_ai/sd_turbo/.
#   WORDS  — the GGUF text models above. (ONNX is Cody's noted future
#            direction for the word side; checked 2026-10-17: no trustworthy
#            ready-made ONNX build of the two Qwen models exists yet — only
#            0-download uploads from unknown accounts — so the GGUF stack
#            stays until official builds exist or are converted in-house.)
# SD-Turbo is Stability AI's distilled SD 2.1 — 1-4 steps per picture, which
# is what makes CPU generation practical. Non-commercial license (the app is
# free / non-commercial, which fits).
SD_RELEASE_TAG = "master-973-f0aac52"
SD_ZIP = "sd-master-f0aac52-bin-win-cpu-x64.zip"
SD_URL = f"https://github.com/leejet/stable-diffusion.cpp/releases/download/{SD_RELEASE_TAG}/{SD_ZIP}"
SD_DIR_NAME = "sd-master-f0aac52-bin-win-cpu-x64"
SD_MODEL_REPO = "stabilityai/sd-turbo"
SD_MODEL_FILE = "sd_turbo.safetensors"
SD_MODEL_URL = f"https://huggingface.co/{SD_MODEL_REPO}/resolve/main/{SD_MODEL_FILE}"
SD_MODEL_BYTES = 5_214_561_328  # exact size of sd_turbo.safetensors on HF
SD_DIR = LOCAL_AI_DIR / "sd_turbo"
_SD_CLI_NAMES = {"sd-cli", "sd-cli.exe", "sd", "sd.exe"}


def scan_sd():
    """Find the SD engine (sd-cli) and the SD Turbo model in local_ai/.

    Looks in local_ai/sd_turbo/ first, then anywhere under local_ai/
    (two levels deep) so hand-installed copies are found too.
    Returns {"dir": SD_DIR, "cli": Path|None, "model": Path|None}.
    """
    result = {"dir": SD_DIR, "cli": None, "model": None}
    roots = [SD_DIR, LOCAL_AI_DIR]
    seen = set()

    def _walk(folder, depth=0):
        try:
            entries = sorted(folder.iterdir())
        except Exception:
            return
        for f in entries:
            try:
                if f.is_file():
                    key = str(f.resolve())
                    if key in seen:
                        continue
                    seen.add(key)
                    name_l = f.name.lower()
                    if name_l in _SD_CLI_NAMES and f.stat().st_size > 100_000:
                        if result["cli"] is None:
                            result["cli"] = f
                    elif ("turbo" in name_l and f.suffix.lower() in (".safetensors", ".gguf", ".ckpt")
                          and f.stat().st_size > 1024 * 1024):
                        if result["model"] is None:
                            result["model"] = f
                elif f.is_dir() and depth < 2:
                    _walk(f, depth + 1)
            except Exception:
                continue

    for r in roots:
        if r.exists():
            _walk(r)
    return result


def _sd_model_problem(path):
    """For the canonical SD Turbo model file the exact byte size is
    known (SD_MODEL_BYTES) \u2014 anything else is a download that didn't
    finish. Older builds renamed partial downloads into place, and a
    freeze/force-close at the wrong moment can leave one too; such a
    file was then accepted as "installed" forever and sd-cli failed to
    load it on every generation (gate from v14.8.1, dropped again by
    the 15.0 rewrite, re-ported in v15.1.1). Returns a reason or None."""
    try:
        if path is not None and path.name == SD_MODEL_FILE \
                and path.stat().st_size != SD_MODEL_BYTES:
            return (f"the file is {path.stat().st_size // 1_000_000} MB but the real "
                    f"SD Turbo model is {SD_MODEL_BYTES // 1_000_000} MB \u2014 "
                    "the download didn't finish")
    except Exception:
        pass
    return None


def _sd_engine_incomplete(cli):
    """The SD engine is a FOLDER of files (sd-cli + the stable-diffusion
    library + ggml libraries). A freeze/force-close mid-extract can
    leave the exe present but its libraries missing \u2014 it then can't run
    at all, while looking "installed" (v14.8.1)."""
    try:
        names = {f.name.lower() for f in cli.parent.iterdir() if f.is_file()}
        dlls = {"stable-diffusion.dll", "ggml.dll", "ggml-base.dll"}
        sos = {"libstable-diffusion.so", "libggml.so", "libggml-base.so"}
        return not (dlls.issubset(names) or sos.issubset(names))
    except Exception:
        return False


def sd_ready():
    info = scan_sd()
    if not info["cli"] or not info["model"]:
        return False
    if _sd_model_problem(info["model"]):
        return False
    if _sd_engine_incomplete(info["cli"]):
        return False
    return True


def download_sd_engine(progress=None):
    """Download the stable-diffusion.cpp engine into local_ai/sd_turbo/.

    Same method as the llama.cpp engine: one release zip from GitHub,
    safe-extracted, no pip / no compiling. Idempotent. Returns sd-cli path.
    """
    existing = scan_sd()["cli"]
    if existing and not _sd_engine_incomplete(existing):
        return existing
    if existing:
        # Half-extracted engine \u2014 reinstall it (the extract below
        # overwrites/adds the missing pieces).
        log.warning("SD engine at %s is incomplete \u2014 reinstalling", existing)
        if progress:
            progress("The SD engine is incomplete \u2014 reinstalling it\u2026")
    SD_DIR.mkdir(parents=True, exist_ok=True)
    dest_dir = SD_DIR / SD_DIR_NAME
    zip_path = SD_DIR / SD_ZIP
    if progress:
        progress("Downloading SD engine (stable-diffusion.cpp, Windows CPU)…")
    try:
        _download_resumable(SD_URL, zip_path, "Downloading SD engine", progress)
        import zipfile
        if not zipfile.is_zipfile(zip_path):
            zip_path.unlink(missing_ok=True)
            raise OSError("the downloaded SD engine file is damaged - try again")
    except Exception as e:
        raise AIError(_download_error("SD engine download failed", "stable-diffusion.cpp", e)) from e
    try:
        import zipfile
        dest_dir.mkdir(parents=True, exist_ok=True)
        base = dest_dir.resolve()
        with zipfile.ZipFile(zip_path) as z:
            for member in z.namelist():
                target = (dest_dir / member).resolve()
                if target != base and base not in target.parents:
                    raise AIError(f"SD engine zip contains an unsafe path: {member}")
            z.extractall(dest_dir)
    except AIError:
        raise
    except Exception as e:
        raise AIError(f"Could not unpack the SD engine zip: {e}") from e
    finally:
        try:
            zip_path.unlink()
        except Exception:
            pass
    cli = scan_sd()["cli"]
    if not cli:
        raise AIError("SD engine unpacked but no sd-cli.exe was found inside it.")
    if not sys.platform.startswith("win"):
        try:
            cli.chmod(0o755)
        except Exception:
            pass
    log.info("stable-diffusion.cpp ready: %s", cli)
    if progress:
        progress(f"SD engine ready: {cli.name}")
    return cli


def download_sd_model(progress=None):
    """Download the SD Turbo model (~5.2 GB) into local_ai/sd_turbo/.

    Writes to a .part file and renames at the end, so an interrupted
    download never looks like a finished model. Idempotent.
    """
    existing = scan_sd()["model"]
    if existing:
        problem = _sd_model_problem(existing)
        if not problem:
            return existing
        # A partial download wearing the finished file's name \u2014 remove
        # it and fetch a fresh copy (a good .part file still resumes).
        log.warning("SD Turbo model is damaged (%s) \u2014 downloading a fresh copy", problem)
        if progress:
            progress("The SD Turbo model on disk is incomplete \u2014 downloading a fresh copy\u2026")
        try:
            existing.unlink()
        except OSError as e:
            raise AIError(f"The damaged SD Turbo model can't be deleted ({e}). "
                          "Close anything using it, delete sd_turbo/sd_turbo.safetensors "
                          "from local_ai/, and try again.") from e
    SD_DIR.mkdir(parents=True, exist_ok=True)
    dest = SD_DIR / SD_MODEL_FILE
    part = SD_DIR / (SD_MODEL_FILE + ".part")
    if progress:
        progress("Downloading SD Turbo model (~5.2 GB — the big one)…")
    try:
        done = _download_resumable(SD_MODEL_URL, part, "Downloading SD Turbo model", progress,
                                   expected=SD_MODEL_BYTES, timeout=120, retries=8)
        # v13.27 audit fix: a dropped connection used to rename a
        # truncated file into place as if it were the finished model.
        expected = SD_MODEL_BYTES
        if done < expected * 0.9:
            raise AIError(f"the download stopped early ({done // 1_000_000} of "
                          f"{expected // 1_000_000} MB) — click Download again to continue "
                          "where it stopped")
        part.replace(dest)
    except Exception as e:
        # .part is kept: the next click resumes instead of starting 5 GB over
        raise AIError(_download_error("SD Turbo model download failed", SD_MODEL_FILE, e)) from e
    found = scan_sd()["model"]
    if not found:
        raise AIError("SD Turbo model downloaded but not found afterwards — check local_ai/sd_turbo/.")
    log.info("SD Turbo model ready: %s", found)
    if progress:
        progress("SD Turbo model ready")
    return found


def wipe_local_ai():
    """Remove EVERYTHING in local_ai/ — llama.cpp engine, both GGUF
    models, SD Turbo engine + model, broken-model marks — and forget all
    cached engine/model state, so the app behaves like a fresh install.
    Used by the AI tab's Reset button, which re-downloads afterwards.

    v13.28: VERIFIED wipe. The in-process model is unloaded first (on
    Windows a GGUF the app has mapped can't be deleted), locked files
    get a second attempt, and anything that still won't delete is
    reported in "leftovers" instead of being silently left behind —
    a leftover model would otherwise keep the AI switched on.
    Returns {"removed": [names], "leftovers": [names]}."""
    global _llama_cli_path, _gguf_llm, _gguf_path
    try:
        if _gguf_llm is not None:
            try:
                _gguf_llm.close()
            except Exception:
                pass
    finally:
        _gguf_llm = None
        _gguf_path = None
    import gc
    gc.collect()

    def _remove(entry):
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()

    removed, leftovers = [], []
    try:
        LOCAL_AI_DIR.mkdir(parents=True, exist_ok=True)
        entries = sorted(LOCAL_AI_DIR.iterdir())
    except Exception as e:
        log.warning("wipe_local_ai: %s", e)
        entries = []
    pending = []
    for entry in entries:
        try:
            _remove(entry)
            removed.append(entry.name)
        except Exception:
            pending.append(entry)
    if pending:
        time.sleep(0.5)   # transient locks let go — retry once
        for entry in pending:
            try:
                _remove(entry)
                removed.append(entry.name)
            except Exception as e:
                log.warning("wipe_local_ai: could not remove %s: %s", entry.name, e)
                if entry.is_dir():
                    try:
                        leftovers += [str(s.relative_to(LOCAL_AI_DIR))
                                      for s in sorted(entry.rglob("*"))[:20] if s.is_file()]
                    except Exception:
                        leftovers.append(entry.name)
                else:
                    leftovers.append(entry.name)
    _PROBE_CACHE.clear()
    _RUNTIME_BAD.clear()
    _WARNED_BAD.clear()
    _llama_cli_path = None
    cfg.local_ready = False
    cfg.fast_model = str(LOCAL_AI_DIR / FAST_GGUF_FILE)
    cfg.smart_model = str(LOCAL_AI_DIR / SMART_GGUF_FILE)
    cfg.save()
    log.info("wipe_local_ai removed: %s | leftovers: %s", removed, leftovers)
    return {"removed": removed, "leftovers": leftovers}


_LAST_SUBJECT = {}

# v14.8: picture styles for SD Turbo. The subject always comes FIRST (it only reads the start
# of a prompt well); the style is a short suffix. Try each and keep the one you like.
SD_STYLES = {
    "Flat clipart": "simple flat clipart illustration, one single subject, thick dark outline, "
                    "bold solid colors, plain white background, centered, no text",
    "Cartoon sticker": "cute cartoon sticker, one single subject, thick white border, bold colors, "
                       "simple shapes, plain white background, centered, no text",
    "Line drawing": "simple black line drawing, coloring book page, clean thick outlines, "
                    "plain white background, centered, no text",
    "Bold icon": "bold flat vector icon, black silhouette with minimal detail, "
                 "plain white background, centered, no text",
    "Picture book": "children's picture book illustration, soft simple shapes, gentle colors, "
                    "plain white background, centered, no text",
}
SD_STYLE_DEFAULT = "Flat clipart"  # legacy short styles below keep working
AAC_STYLE_NAME = "AAC pastel"  # the style-builder style (see aac_style.py)
AAC_STYLE_DEFAULT = AAC_STYLE_NAME  # new pictures default to the style builder


def last_subject(word):
    """What the AI was told to draw for this word last time (shown to the user)."""
    return _LAST_SUBJECT.get(str(word).strip().lower())


_SUBJECT_BAD = re.compile(r"\b(sorry|cannot|can't|as an ai|i am|i'm|unable|word)\b", re.I)


def visual_subject(word):
    """v14.8: turn an AAC word into ONE concrete thing to draw ('excitement' -> 'a happy
    child jumping with both arms up'). SD Turbo draws objects and scenes well but fumbles
    abstract words, feelings and verbs when it is handed the bare word - which is why
    pictures often had nothing to do with the word. Cached; falls back to the word itself
    when the text AI isn't available."""
    word = str(word).strip()
    key = _hash("subject-v2", word.lower())
    cached = cache_get(key)
    if cached:
        return cached
    try:
        if not (cfg.ready or local_stack_present()):
            return word
        system = ("You help make picture cards for children who use AAC. Given a word, reply with "
                  "ONE short description (under 12 words) of a single, concrete, easy-to-draw picture "
                  "that clearly shows its everyday meaning. Objects: name the object. Feelings: a "
                  "child's face showing it. Actions: a child doing it. Places/people: the simplest "
                  "typical example. Abstract words: one clear everyday scene. No text or letters in "
                  "the picture. Reply with ONLY the description, nothing else.\n"
                  "Examples: happy -> a smiling child's face | run -> a child running | "
                  "excitement -> a child jumping with both arms up and a big smile | "
                  "bathroom -> a toilet and a sink | more -> two apples next to one apple")
        txt = call(system, f"Word: {word}", cfg.fast_model, 48, timeout=90)
        txt = str(txt).strip().splitlines()[0].strip().strip("\"'`*- ").rstrip(".")
        txt = re.sub(r"^(description|picture)\s*:\s*", "", txt, flags=re.I)
        if 3 <= len(txt) <= 110 and not _SUBJECT_BAD.search(txt):
            cache_set(key, txt)
            return txt
    except Exception as e:
        log.info("visual_subject for '%s' unavailable (%s) - using the word", word, e)
    return word


def _tidy_picture(path):
    """Centre the subject on a clean square white canvas (v14.8). SD Turbo often draws the
    subject small or off to one side with a grey/tinted background, which looks bad on
    a card and wastes space on a tag. Never raises - on any problem the image is left as is."""
    try:
        import numpy as np
        from PIL import Image
        im = Image.open(path).convert("RGB")
        a = np.asarray(im).astype(np.int16)
        border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
        bg = np.median(border, axis=0)
        mask = np.abs(a - bg).sum(axis=2) > 90
        frac = mask.mean()
        if frac < 0.01 or frac > 0.85:
            return
        ys, xs = np.where(mask)
        x0, x1 = np.percentile(xs, [0.5, 99.5]).astype(int)
        y0, y1 = np.percentile(ys, [0.5, 99.5]).astype(int)
        pad = int(0.08 * max(x1 - x0, y1 - y0))
        box = (max(0, x0 - pad), max(0, y0 - pad), min(im.width, x1 + pad + 1), min(im.height, y1 + pad + 1))
        crop = im.crop(box)
        side = max(crop.size)
        canvas = Image.new("RGB", (side, side), tuple(int(v) for v in bg))
        canvas.paste(crop, ((side - crop.width) // 2, (side - crop.height) // 2))
        canvas = canvas.resize((512, 512), Image.LANCZOS)
        canvas.save(path)
    except Exception as e:
        log.debug("tidy picture skipped: %s", e)


def sd_generate_png(word, progress=None, seed=None, subject=None, steps=4, style=None):
    """Create a picture for `word` with SD Turbo. Returns the PNG path.

    v14.8: the picture is made from a concrete description of the word (`subject`, or
    visual_subject(word) when not given), with the subject FIRST in a short clean prompt
    (SD Turbo reads only the start of a prompt well), 4 steps instead of 2, and no
    "AAC / symbol / pictogram" wording that it tends to draw literally (odd symbols and
    fake lettering). The result is centred on a clean square. The description used is
    available afterwards from last_subject(word).
    seed=None uses the fixed default (same picture every time); pass a random seed to
    REGENERATE a different picture for the same word.
    Raises AIError (plain English) when the stack isn't installed.
    """
    info = scan_sd()
    problem = _sd_model_problem(info["model"])
    if problem:
        raise AIError(
            "The SD Turbo model on disk is damaged \u2014 " + problem + ".\n"
            "Click \u201cDownload SD Turbo model\u201d in the AI tab: it will fetch "
            "a fresh copy, then try again.")
    if not info["cli"] or not info["model"]:
        missing = []
        if not info["cli"]:
            missing.append("the SD engine")
        if not info["model"]:
            missing.append("the SD Turbo model")
        raise AIError(
            "SD Turbo isn't installed yet — missing " + " and ".join(missing) + ".\n"
            "Use the download buttons in the AI tab (local_ai/sd_turbo/), then try again.")
    AI_ICON_DIR.mkdir(parents=True, exist_ok=True)
    word = str(word).strip()
    if not word:
        raise AIError("Type a word first — there is nothing to draw.")
    if progress:
        progress("Working out what to draw…")
    style_name = style or AAC_STYLE_DEFAULT
    negative = ""
    if style_name == AAC_STYLE_NAME:
        # v15.24: the style builder assembles the prompt (subject, permanent
        # style, composition) and supplies the negative prompt separately.
        # The text model is only consulted for words the dictionary misses,
        # and only when the saved subject mode allows it.
        import aac_style
        cfg = aac_style.get_config()
        describer = visual_subject if cfg.subject_mode == aac_style.SUBJECT_MODE_MODEL else None
        built = aac_style.build_prompt(word, subject=subject, config=cfg, text_describer=describer)
        what = built.subject
        prompt = built.positive
        negative = built.negative
    else:
        what = (str(subject).strip() if subject else "") or visual_subject(word)
        prompt = f"{what}, {SD_STYLES.get(style_name, SD_STYLES[SD_STYLE_DEFAULT])}"
    _LAST_SUBJECT[word.lower()] = what
    slug = re.sub(r"[^a-z0-9]+", "_", word.lower()).strip("_") or "word"
    seed = 42 if seed is None else int(seed)
    out = AI_ICON_DIR / (f"sd_{slug}.png" if seed == 42 else f"sd_{slug}_{seed}.png")
    cmd = [str(info["cli"]), "-m", str(info["model"]),
           "-p", prompt, "-o", str(out),
           "--steps", str(max(1, int(steps))), "--cfg-scale", "1.0",
           "-W", "512", "-H", "512", "--seed", str(seed)]
    if negative:
        cmd += ["--negative-prompt", negative]
    log.info("sd-cli: '%s' as \"%s\" style=%s (steps %s, negative %d chars)",
             word, what, style_name, steps, len(negative))
    if progress:
        progress(f"SD Turbo is drawing: {what}  (CPU can take a few minutes)")
    with _INFER_LOCK:   # v13.26: one inference at a time (see _INFER_LOCK)
        try:
            if out.exists():
                out.unlink()          # never mistake an old picture for this run's result
        except OSError:
            pass
        try:
            r = _run_quiet(cmd, 900, cwd=Path(info["cli"]).parent)
        except OSError as e:
            raise AIError(f"sd-cli failed to run: {e}") from e
        except subprocess.TimeoutExpired as e:
            raise AIError("SD Turbo took too long (over 15 minutes) and was stopped. "
                          "Close other programs and try again, or use the simple drawing instead.") from e
    if r.returncode != 0 or not out.exists() or out.stat().st_size < 1000:
        both = ((r.stderr or "") + "\n" + (r.stdout or "")).strip()
        low = both.lower()
        if (r.returncode in (-1073741819, 3221225477, -1073740791, 3221226505) or "bad_alloc" in low
                or "not enough memory" in low or "out of memory" in low or "alloc" in low and "fail" in low):
            raise AIError("SD Turbo ran out of memory. It needs about 6-8 GB of free RAM - close "
                          "other programs (browsers, games) and try again. If it keeps happening, "
                          "use the simple icon drawing instead.")
        tail = both[-400:]
        raise AIError(f"SD Turbo couldn't create the picture (exit {r.returncode}): {tail}")
    _tidy_picture(out)
    log.info("SD Turbo created a picture for '%s' → %s", word, out)
    return out


def _is_windows_pe(path):
    """True if *path* looks like a Windows PE executable (MZ header)."""
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"MZ"
    except Exception:
        return False


def _find_llama_cli():
    """Return path to a *runnable* native llama-cli binary, or None.

    On Windows, skips files that are not PE (avoids WinError 193 from Linux
    or macOS binaries dropped into local_ai/).
    v14.8: a "not found" answer is never cached - before, looking once before the
    engine existed (or before you dropped it into local_ai/) made the app act as if
    it was missing until restart, even right after a successful install.
    """
    global _llama_cli_path
    if _llama_cli_path:
        try:
            if Path(_llama_cli_path).exists():
                return _llama_cli_path
        except Exception:
            pass
        _llama_cli_path = None
    info = scan_local_ai_dir()
    p = info.get("llama_cli")
    if p and sys.platform.startswith("win") and not _is_windows_pe(p):
        log.warning("Ignoring non-Windows binary in local_ai/: %s (not a PE/MZ file)", p)
        p = None
    _llama_cli_path = p if p else None
    return p


def _llama_cli_chat(model_path, prompt, max_tokens=512, system=None, _timeout=None):
    """Run a one-shot chat via the native llama-cli binary (no python package).

    The prompt goes in Qwen ChatML format (<|im_start|>…<|im_end|>) — the
    format the instruct models were trained on, so the model stops at the
    end of its answer. The old plain "System:/User:/Assistant:" text made
    it ramble to the token limit, which kept hitting the fixed 180 s
    timeout. The timeout now scales with the size of the job.
    """
    cli = _find_llama_cli()
    if not cli:
        raise AIError(
            "No llama-cli engine found in local_ai/.\n\n"
            "Fix: in the AI tab click “Download llama.cpp engine "
            f"({LLAMA_CPP_TAG})”, then click “Set up local AI” again.")
    if system:
        full = (f"<|im_start|>system\n{system}<|im_end|>\n"
                f"<|im_start|>user\n{prompt}<|im_end|>\n"
                "<|im_start|>assistant\n")
    else:
        full = (f"<|im_start|>user\n{prompt}<|im_end|>\n"
                "<|im_start|>assistant\n")
    # Scale with the token budget. v13.26: 13.25's tighter caps (194 s for
    # 700 tokens) cut real jobs off on Cody's laptop — the log showed
    # ~4 tokens/sec once other work shared the CPU. Calls are serialized
    # now, but keep headroom anyway: a 60 s base (model load + prompt)
    # plus ~0.28 s/token, capped at 15 minutes. A timeout no longer ends
    # the job either — call() automatically retries the smaller model.
    if _timeout is not None:
        timeout = int(_timeout)
    else:
        timeout = int(min(900, max(120, 60 + max_tokens * 0.28)))
    log.info("llama-cli: %s -n %s (timeout %ss)", Path(str(model_path)).name, max_tokens, timeout)
    try:
        r = _run_llama(cli, model_path, full, max_tokens, 0.3, timeout)
    except subprocess.TimeoutExpired:
        raise AIError(
            "The local AI took too long and was stopped "
            f"(over {max(1, timeout // 60)} min). It may just be a slow PC — "
            "try again in a moment.") from None
    except OSError as e:
        if getattr(e, "winerror", None) == 193 or "193" in str(e):
            raise AIError(
                f"llama-cli failed to run: {e}\n\n"
                "That file is not a valid Windows program (WinError 193) — "
                "it is a Linux/macOS binary. Download the Windows engine "
                "from the AI tab instead.") from e
        raise AIError(f"llama-cli failed to run: {e}") from e
    text = (r.stdout or "").strip()
    # The model can continue past its answer with a fake next turn; keep
    # only the assistant's reply.
    for stop in ("<|im_end|>", "<|im_start|>"):
        i = text.find(stop)
        if i != -1:
            text = text[:i].strip()
    if r.returncode != 0:
        both = ((r.stderr or "") + "\n" + (r.stdout or "")).strip()
        if "failed to load model" in both or "error loading model" in both:
            log.warning("llama-cli could not load %s: %s", Path(str(model_path)).name, both[-400:])
            raise AIError(f"The model file {Path(str(model_path)).name} couldn't be loaded — it is most "
                          "likely an incomplete or damaged download. In the AI tab, click its Download "
                          "button again (it replaces the broken file).")
        tail = both[-300:]
        raise AIError(f"llama-cli exit {r.returncode}: {tail}" if tail
                      else f"llama-cli exit {r.returncode} (no message).")
    return text


def llama_chat(model_path, prompt, max_tokens=512, system=None):
    """Run one prompt through a GGUF — serialized (see _INFER_LOCK)."""
    with _INFER_LOCK:
        return _llama_chat_run(model_path, prompt, max_tokens=max_tokens, system=system)


def _llama_chat_run(model_path, prompt, max_tokens=512, system=None):
    """Run one prompt through a GGUF (v13.14-exp engine order).

    1. The llama.cpp binary in local_ai/ (the b11575 package — the engine).
    2. llama-cpp-python, when installed (model cached in memory = fast
       repeated calls).
    """
    global _gguf_llm, _gguf_path
    path = str(model_path)

    # 1) Bundled llama.cpp binary first — that's the chosen engine.
    try:
        cli = _find_llama_cli()
    except Exception:
        cli = None
    if cli:
        return _llama_cli_chat(path, prompt, max_tokens=max_tokens, system=system)

    # 2) Python package fallback.
    try:
        from llama_cpp import Llama
    except ImportError:
        raise AIError(
            "No local AI engine found.\n\n"
            f"Click “Download llama.cpp engine ({LLAMA_CPP_TAG})” in the AI tab "
            "(one download, no compiling), or install Ollama from https://ollama.com.")

    if _gguf_llm is None or _gguf_path != path:
        log.info("Loading GGUF model: %s", path)
        _gguf_llm = Llama(model_path=path, n_ctx=2048, verbose=False)
        _gguf_path = path
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    out = _gguf_llm.create_chat_completion(
        messages=messages, max_tokens=max_tokens, temperature=0.3)
    return out["choices"][0]["message"]["content"].strip()


def _is_gguf_path(model):
    """True when *model* is a path to a .gguf file (not an Ollama name)."""
    if not model:
        return False
    s = str(model)
    if s.lower().endswith(".gguf"):
        return True
    try:
        return Path(s).is_file() and Path(s).suffix.lower() == ".gguf"
    except Exception:
        return False


def _call_gguf(system, content, model, max_tokens, timeout):
    """Local GGUF via llama-cpp-python (from local_ai/ folder)."""
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif isinstance(b, str):
                parts.append(b)
        user_text = "\n".join(parts)
    else:
        user_text = str(content)
    t0 = time.time()
    try:
        text = llama_chat(model, user_text, max_tokens=max_tokens, system=system)
    except AIError:
        raise  # already a plain-English message — don't bury it in a wrapper
    except Exception as e:
        log.warning("GGUF call failed: %s: %s", type(e).__name__, e)
        raise AIError(f"Local GGUF model failed: {type(e).__name__}: {e}") from e
    log.debug("GGUF %s answered in %.1fs (%d chars)", model, time.time() - t0, len(text))
    if not str(text).strip():
        raise AIError("The local AI returned an empty answer.")
    return str(text)


# ============================================================ low-level call
def _friendly(code, detail):
    if code == 401:
        return "The API key was rejected. Check it in AI settings."
    if code == 403:
        return "This API key is not allowed to use that model."
    if code == 404:
        return "The model name was not found. Change it in AI settings."
    if code == 429:
        return "Rate limit or credit problem - wait a minute, or check your account."
    if code in (500, 502, 503, 529):
        return "The AI service is busy right now. Try again in a moment."
    return f"AI request failed (HTTP {code}): {detail[:200]}"


def _call_ollama(system, content, model, max_tokens, timeout):
    if isinstance(content, list):
        # vision-style blocks: flatten text parts
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif isinstance(b, str):
                parts.append(b)
        user_text = "\n".join(parts)
    else:
        user_text = str(content)
    body = {
        "model": model,
        "stream": False,
        "options": {"num_predict": max_tokens, "temperature": 0.2},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
    }
    url = OLLAMA_HOST.rstrip("/") + "/api/chat"
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise AIError("Local AI is not running. Open the AI tab and click “Enable local AI”.") from e
    except Exception as e:
        raise AIError(f"Local AI call failed: {type(e).__name__}: {e}") from e
    text = (data.get("message") or {}).get("content") or data.get("response") or ""
    log.debug("Local AI %s answered in %.1fs (%d chars)", model, time.time() - t0, len(text))
    if not str(text).strip():
        raise AIError("The local AI returned an empty answer.")
    return str(text)


def _call_anthropic(system, content, model, max_tokens, timeout):
    body = {"model": model, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": content}]}
    req = urllib.request.Request(
        API_URL, data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json", "x-api-key": cfg.key.strip(),
                 "anthropic-version": API_VERSION})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        log.warning("AI HTTP %s from %s: %s", e.code, body["model"], detail[:300])
        raise AIError(_friendly(e.code, detail))
    except urllib.error.URLError as e:
        log.warning("AI network error: %s", e)
        raise AIError("Can't reach the AI service - are you online?")
    except Exception as e:
        log.warning("AI call failed: %s: %s", type(e).__name__, e)
        raise AIError(f"AI call failed: {type(e).__name__}: {e}")
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    log.debug("AI %s answered in %.1fs (%d chars)", body["model"], time.time() - t0, len(text))
    if not text.strip():
        raise AIError("The AI returned an empty answer.")
    return text


# v13.23: "use the models we actually have". The configured model can be missing (stale path from
# an older folder, half-finished download, file renamed). Instead of failing, try every real GGUF.
_FALLBACK_NOTE = ""


def pop_fallback_note():
    """Plain-English note about a model fallback that just happened ('' if none). Clears it."""
    global _FALLBACK_NOTE
    n, _FALLBACK_NOTE = _FALLBACK_NOTE, ""
    return n


def local_stack_present():
    """True when local_ai/ holds an engine + at least one GGUF that isn't known-broken."""
    try:
        caps = capabilities(probe=False)
        return bool(caps["models"]) and bool(caps.get("engine") or _find_llama_cli())
    except Exception:
        return False


def _gguf_candidates(model, role):
    """Ordered list of GGUF files that really exist: the configured one first, then the one
    that fits the job (smart/fast), then every other model in local_ai/."""
    cands, names = [], set()

    def add(p):
        try:
            p = Path(str(p))
            if p.is_file() and p.stat().st_size > 1024 * 1024 and p.name.lower() not in names:
                # Skip damaged files here too — the scan excludes them, and
                # re-adding one would hide the "damaged model" message in
                # call() behind a generic engine/load error.
                if gguf_problem(p):
                    return
                names.add(p.name.lower())
                cands.append(p)
        except Exception:
            pass
    if model and _is_gguf_path(model):
        add(model)
    add(find_model(role))
    for m in scan_local_ai_dir().get("models") or []:
        add(m)
    return cands


# v13.26: "took too long" is NOT in this list any more — a timeout now
# falls through to the automatic smaller-model switch in call() below
# (Cody: "it tells me to use the smaller model — should auto do it").
_NO_RETRY_HINTS = ("No llama-cli", "WinError 193", "No local AI engine")


def call(system, content, model=None, max_tokens=1200, timeout=120):
    """content = text or a list of content blocks. Returns the reply text."""
    # v13.25: never auto-flip local_ready just because a .gguf sits on disk —
    # the file may be corrupt. Setup (or a successful call) is what marks it ready.
    if not cfg.ready:
        if cfg.backend == "local":
            # If a probe already found a working model this session, allow the call
            caps = capabilities(probe=False)
            if caps["text"]:
                cfg.local_ready = True
                cfg.save()
            else:
                raise AIError(
                    "Local AI is not set up yet, or every model file is damaged.\n\n"
                    "Open the AI tab → Set up local AI. If a model is listed as broken, "
                    "click its Download button again.")
        else:
            raise AIError("No API key yet. Open the AI tab > Set up AI.")
    model = model or cfg.fast_model
    if cfg.backend == "local":
        # Prefer GGUF from local_ai/ when the configured model is a .gguf path.
        # v15.0: single text model (Qwen 3B); role only affects candidate order.
        global _FALLBACK_NOTE
        role = "smart" if model == cfg.smart_model else "fast"
        cands = _gguf_candidates(model, role)
        if cands:
            last = None
            timeout_from = None  # name of a model that timed out (v13.26)
            for i, cand in enumerate(cands):
                try:
                    out = _call_gguf(system, content, str(cand), max_tokens, timeout)
                    # Real success → clear any false "broken" mark (e.g. timed-out probe)
                    clear_model_broken(cand)
                    _PROBE_CACHE[str(cand)] = True
                    if timeout_from is not None:
                        _FALLBACK_NOTE = (f"Note: {timeout_from} was taking too long, so the AI "
                                          f"automatically finished the job with {cand.name} instead.")
                        log.warning("Answered with %s after %s timed out", cand.name, timeout_from)
                    elif i > 0:
                        _FALLBACK_NOTE = (f"Note: the preferred model wasn't available (or failed), "
                                          f"so the AI used {cand.name} from your local_ai folder instead.")
                        log.warning("Preferred model unavailable — answered with %s", cand.name)
                    return out
                except AIError as e:
                    last = e
                    msg = str(e)
                    if ("couldn't be loaded" in msg or "incomplete or damaged" in msg
                            or "Failed to load model" in msg or "failed to load model" in msg):
                        mark_model_broken(cand, "failed to load during call")
                    if any(h in msg for h in _NO_RETRY_HINTS):
                        break
                    if "took too long" in msg and i + 1 < len(cands):
                        # Don't just TELL the user to use the smaller model —
                        # switch to it automatically, smallest first.
                        timeout_from = cand.name
                        rest = sorted(cands[i + 1:],
                                      key=lambda q: q.stat().st_size if q.exists() else 0)
                        cands[i + 1:] = rest
                        log.warning("Model %s timed out — automatically switching to %s",
                                    cand.name, rest[0].name)
                    log.warning("Model %s failed (%s) — trying the next model in local_ai/", cand.name, e)
            # GGUF engine failed. Only fall back to Ollama when it is actually running — otherwise
            # its "not running" error just buries the real problem.
            if ollama_alive():
                log.warning("GGUF runtime failed (%s); Ollama is up — trying it", last)
                try:
                    return _call_ollama(system, content, LOCAL_MODEL, max_tokens, timeout)
                except Exception:
                    pass
            raise last
        if ollama_alive():
            # `model` may be a stale .gguf path — never hand that to Ollama
            # as a model name (it answers with a confusing "not found").
            return _call_ollama(system, content,
                                model if not _is_gguf_path(model) else LOCAL_MODEL,
                                max_tokens, timeout)
        bad = scan_local_ai_dir().get("damaged") or []
        if bad:
            names = "; ".join(f"{f.name} {why}" for f, why in bad)
            raise AIError("The only model file(s) in local_ai/ are damaged: " + names + ".\n\n"
                          "Click “Download Qwen 3B” in the AI tab — "
                          "it replaces the broken file with a fresh, verified download.")
        raise AIError("No AI model found in the local_ai folder, and Ollama isn't running.\n\n"
                      "Click “Download Qwen 3B” in the AI tab (or put a .gguf in local_ai/), "
                      "then try again.")
    return _call_anthropic(system, content, model, max_tokens, timeout)


def _repair_json(t):
    """Best-effort fix for JSON the model cut off (token limit) or fumbled: drop a
    half-written last string/item, close the open brackets. Returns a value or None."""
    start = next((i for i, ch in enumerate(t) if ch in "{["), None)
    if start is None:
        return None
    t = t[start:]
    for _cut in range(80):
        stack, in_str, esc, str_start = [], False, False, 0
        for i, ch in enumerate(t):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str, str_start = True, i
            elif ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]" and stack:
                stack.pop()
        if in_str:                                   # a string was cut off: drop it entirely
            t = t[:str_start]
            continue
        cand = re.sub(r"[,:\s]+$", "", t) + "".join(reversed(stack))
        try:
            return json.loads(cand)
        except ValueError:
            pass
        i = max(t.rfind(","), t.rfind("{"), t.rfind("["))   # drop the last half-finished piece
        if i < 0:
            return None
        t = t[:i + 1] if t[i] in "{[" else t[:i]
    return None


def extract_json(text):
    """Pull the first JSON object/array out of a reply (tolerates code fences, chatter,
    and (v14.8) answers cut off mid-way by the token limit)."""
    t = re.sub(r"```(?:json)?", "", str(text))
    dec = json.JSONDecoder()
    tried_repair = False
    for i, ch in enumerate(t):
        if ch in "{[":
            try:
                obj, _ = dec.raw_decode(t[i:])
                return obj
            except ValueError:
                if not tried_repair:          # the OUTER value is broken/cut off: repair it
                    tried_repair = True       # before settling for some inner fragment
                    fixed = _repair_json(t[i:])
                    if fixed:
                        log.info("AI answer was cut off or malformed - repaired it")
                        return fixed
                continue
    raise AIError("The AI answer was not in the expected format. Try again.")


def ask_json(system, content, model=None, max_tokens=1200):
    """Ask for JSON; if the small local model garbles it, ask once more, firmly (v14.8)."""
    txt = call(system, content, model, max_tokens)
    try:
        return extract_json(txt)
    except AIError:
        log.warning("AI reply wasn't valid JSON - asking again: %r", str(txt)[:160])
    txt2 = call(system + "\n\nIMPORTANT: your previous reply was not valid JSON. Reply with ONLY "
                "one complete, valid JSON value - no explanation, no code fences.",
                content, model, max_tokens)
    return extract_json(txt2)


BASE = ("You are the AI engine inside AAC Tag Studio, a desktop app that makes 3D-printable AAC "
        "(augmentative and alternative communication) tags, PECS cards and boards for non-speaking or "
        "minimally speaking children and adults, many of them autistic. Pictures must be literal, simple and "
        "unambiguous; words short and concrete. Reply with ONLY valid JSON - no prose, no code fences.")


# ============================================================ option validation
def clean_options(opts, spec):
    """Keep only known keys with legal values. spec: key -> ('choice', [..]) | ('float', lo, hi) | ('bool',) | ('color',)"""
    out = {}
    for k, v in (opts or {}).items():
        sp = spec.get(k)
        if not sp:
            continue
        try:
            if sp[0] == "choice":
                m = {str(c).lower(): c for c in sp[1]}
                if str(v).lower() in m:
                    out[k] = m[str(v).lower()]
            elif sp[0] == "float":
                out[k] = round(float(min(max(float(v), sp[1]), sp[2])), 2)
            elif sp[0] == "bool":
                out[k] = v if isinstance(v, bool) else str(v).lower() in ("true", "1", "yes", "on")
            elif sp[0] == "color" and re.fullmatch(r"#[0-9a-fA-F]{6}", str(v)):
                out[k] = str(v)
        except (TypeError, ValueError):
            pass
    return out


def _words(lst, limit=80):
    out, seen = [], set()
    for w in lst or []:
        w = str(w).strip()
        if w and len(w) <= 40 and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out[:limit]


# ============================================================ 1. assistant: plain English -> settings
# v14.8: "make five more words like excitement and put them in PDF Studio".
_NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
         "fifteen": 15, "sixteen": 16, "eighteen": 18, "twenty": 20, "thirty": 30, "forty": 40,
         "fifty": 50, "sixty": 60, "dozen": 12, "couple": 2}
MAX_NEW_WORDS = 60
_NOT_A_COUNT = re.compile(r"^\s*(?:per\b|mm\b|cm\b|%|px\b|pt\b|x\b|\u00d7|pages?\b|columns?\b|rows?\b|inch|in\b)", re.I)


def parse_count(text):
    """How many NEW words the request asks for ('five', '12', 'a dozen'), or None.
    Skips numbers that are settings (e.g. '6 per page', '40 mm')."""
    t = str(text or "")
    pat = re.compile(r"\b(\d{1,3}|" + "|".join(_NUMS) + r")\b", re.I)
    for m in pat.finditer(t):
        if _NOT_A_COUNT.match(t[m.end():]):
            continue
        tok = m.group(1).lower()
        n = int(tok) if tok.isdigit() else _NUMS.get(tok)
        if n and 1 <= n <= 500:
            return min(n, MAX_NEW_WORDS)
    return None


def wants_words(text):
    """True when the user is asking for vocabulary (similar words, a list, etc.).

    v14.9: small local models often treat pure word requests as 'settings only'
    and return words:null. Detecting intent lets us force a word-generation path
    and a default count so 'words similar to jump' actually adds words."""
    t = str(text or "").lower().strip()
    if not t:
        return False
    if re.search(r"\b(similar to|words? like|related to|synonyms? for|like the word)\b", t):
        return True
    if re.search(r"\b(build|make|give|add|suggest|create|generate|find)\b.{0,50}\bwords?\b", t):
        return True
    if re.search(r"\bwords?\b.{0,40}\b(add|to my|word list|into my|for me)\b", t):
        return True
    if re.search(r"\b(more words|vocabulary|vocab for|word ideas|word list)\b", t):
        return True
    # "8 bathroom words", "twelve food words" — number + topic + words
    if re.search(r"\b\d{1,3}\b.{0,30}\bwords?\b", t) or re.search(
            r"\b(" + "|".join(_NUMS) + r")\b.{0,30}\bwords?\b", t):
        return True
    return False


def wants_picture(text):
    """Subject of a picture request in the AI chat, or None.

    v15.26: a picture request must never reach the settings/words assistant.
    A long descriptive prompt ("soft pastel ... move the subject to the
    center") was parsed as settings instructions, and the returned options
    rearranged the preview. Picture requests go straight to image generation
    instead: no options applied, no words added, no tab switches.
    Returns the subject phrase, or "" when the request names no subject.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    low = raw.lower()
    m = re.search(r"\b(?:draw|generate|create|make|paint)\s+(?:a\s+|an\s+|the\s+)?"
                  r"(?:picture|image|illustration|icon|drawing|photo)(?:\s+(?:of\s+|for\s+)?(.+))?", low)
    if not m:
        m = re.search(r"\b(?:picture|image|illustration|icon|drawing|photo)\s+of\s+(.+)", low)
    if not m:
        m = re.search(r"^draw\s+(.+)", low)
    if not m:
        return None
    subj = (m.group(1) or "").strip()
    # a subject is a word or short phrase: cut at the first style clause
    subj = re.split(r"[,.;]", subj)[0].strip()
    subj = re.sub(r"^(a|an|the)\s+", "", subj)
    return subj


def detect_target(text):
    """Where the request says the new words should go: 'pdf', 'maker', 'list' or None
    (None = use whatever the target selector in the AI tab says)."""
    t = str(text or "").lower()
    if re.search(r"\b(word ?list only|just the (word )?list|only the (word )?list)\b", t):
        return "list"
    if "pdf studio" in t:
        return "pdf"
    if "maker studio" in t:
        return "maker"
    pdf = re.search(r"\b(pdf|pecs|picture cards?|printable|cards?)\b", t)
    mk = re.search(r"\b(maker|3d|tags?|keyrings?|keychains?|plate)\b", t)
    if pdf and mk:
        return "pdf" if pdf.start() < mk.start() else "maker"
    return "pdf" if pdf else ("maker" if mk else None)


def _text_model():
    """Prefer smart (Coder) when set; otherwise the fast/default model."""
    return cfg.smart_model or cfg.fast_model


def _more_words(command, need, avoid):
    """Top-up call: ask for exactly `need` MORE new words, none of them in `avoid`."""
    system = BASE + f"""
Task: give exactly {need} MORE short AAC words that fit the user's request.
None of them may be in AVOID. Words are short, concrete and pictogram-friendly (1-2 words each).
Return {{"words": [..]}}"""
    user = json.dumps({"request": command, "AVOID": list(avoid)[:120]})
    r = ask_json(system, user, _text_model(), min(2500, 150 + 30 * need))
    lst = r.get("words") if isinstance(r, dict) else r
    return _words(lst if isinstance(lst, list) else [], 80)


def _generate_words_for_request(command, need, avoid):
    """Focused word-only call — no settings/SPEC payload that confuses small models.

    Used when the user asks for similar words / a vocabulary list. Returns a plain
    list of strings (may be shorter than need if the model under-delivers)."""
    need = max(1, min(int(need or 8), MAX_NEW_WORDS))
    avoid = [str(w).strip() for w in (avoid or []) if str(w).strip()]
    system = BASE + f"""
Task: the user wants NEW AAC vocabulary words.
Give exactly {need} short, concrete, pictogram-friendly words (1-2 words each) that fit the request.
None of them may appear in AVOID (case-insensitive).
Do NOT return settings, options, or null. Always return a non-empty words array.
Return ONLY JSON: {{"words": ["word1", "word2", ...]}}"""
    payload = {"request": command, "count": need, "AVOID": avoid[:120]}
    r = ask_json(system, json.dumps(payload), _text_model(), min(2500, 200 + 40 * need))
    lst = r.get("words") if isinstance(r, dict) else r
    return _words(lst if isinstance(lst, list) else [], need + 10)


def assistant(command, state, spec, count=None, avoid=None):
    """Plain English -> settings and/or words.

    count: how many NEW words were asked for (parse_count). avoid: words that already
    exist (word list, PDF cards, Maker tags) - they are never returned again, and the
    result is topped up so you get the number you asked for, not fewer.

    v14.9: word requests without a number default to 8 words, and use a dedicated
    word-generation path so 'words similar to jump' actually returns words."""
    avoid = [str(w).strip() for w in (avoid or []) if str(w).strip()]
    word_req = wants_words(command)
    if count is None and word_req:
        count = 8
    count = max(1, min(int(count), MAX_NEW_WORDS)) if count else None

    # ---- Word-focused path: skip the heavy SPEC/settings prompt
    if word_req and count:
        seen = {w.lower() for w in avoid}
        words = []
        try:
            raw = _generate_words_for_request(command, count, list(seen))
        except AIError:
            raw = []
        for w in raw:
            if w.lower() not in seen:
                seen.add(w.lower())
                words.append(w)
        for _ in range(2):
            if len(words) >= count:
                break
            try:
                extra = _more_words(command, count - len(words), list(seen))
            except AIError:
                break
            for w in extra:
                if w.lower() not in seen:
                    seen.add(w.lower())
                    words.append(w)
        words = words[:count]
        short = max(0, count - len(words))
        if words:
            msg = (f"Added {len(words)} new word(s) similar to what you asked for."
                   if short == 0 else
                   f"Added {len(words)} new word(s) (asked for {count}).")
        else:
            msg = ("I couldn't generate new words this time — try again, or name a "
                   "number (e.g. “8 words similar to jump”).")
        return {"message": msg,
                "words": words or None,
                "options": {},
                "category_colors": None,
                "requested": count, "short": short}

    # ---- Settings / mixed path (original assistant)
    system = BASE + """
Task: turn the user's request into changes to the app's settings and/or a list of AAC words.
Return {"message": "<1-2 friendly sentences describing what you did>", "words": [..] or null, "options": {..}, "category_colors": true|false|null}
Rules:
- "options" may ONLY use keys from SPEC, with allowed values or ranges. Include only what the request implies.
- "words": when the user asks for words, similar words, more vocabulary, or a list for a situation,
  return a JSON array of short AAC words (1-2 words each). These are ADDED to what they already have - nothing is replaced.
  Use null only when the request is purely about settings (colours, sizes, etc.).
- If "count" is given, "words" must contain exactly that many NEW words. Never repeat anything in "AVOID".
- Words are short, concrete, pictogram-friendly. Lowercase unless a name or the word "I".
- If the request cannot be done with SPEC, say so in "message" and change nothing."""
    # Slim payload — full word lists confuse tiny models into returning null
    slim_state = {
        "options": (state or {}).get("options", {}),
        "word_count": len((state or {}).get("words") or []),
    }
    payload = {"request": command, "current": slim_state, "SPEC": spec}
    if count:
        payload["count"] = count
    if avoid:
        payload["AVOID"] = avoid[:120]
    user = json.dumps(payload, default=list)
    budget = 700 if not count else min(2500, 400 + 30 * count)
    r = ask_json(system, user, _text_model(), budget)
    if not isinstance(r, dict):
        raise AIError("Unexpected AI answer.")
    cc = r.get("category_colors")
    words = _words(r["words"], 120) if isinstance(r.get("words"), list) else None
    short = 0
    if words is not None:
        seen = {w.lower() for w in avoid}
        fresh = []
        for w in words:
            if w.lower() not in seen:
                seen.add(w.lower())
                fresh.append(w)
        words = fresh
        if count:
            for _ in range(2):
                if len(words) >= count:
                    break
                try:
                    extra = _more_words(command, count - len(words), list(seen))
                except AIError:
                    break
                for w in extra:
                    if w.lower() not in seen:
                        seen.add(w.lower())
                        words.append(w)
            words = words[:count]
            short = max(0, count - len(words))
    # If the model still returned no words but the request clearly asked for
    # them, fall through to the focused generator once.
    if (words is None or not words) and word_req:
        n = count or 8
        try:
            raw = _generate_words_for_request(command, n, avoid)
            seen = {w.lower() for w in avoid}
            words = [w for w in raw if w.lower() not in seen][:n]
            short = max(0, n - len(words))
            count = n
        except AIError:
            pass
    return {"message": str(r.get("message", "Done.")),
            "words": words,
            "options": clean_options(r.get("options"), spec),
            "category_colors": cc if isinstance(cc, bool) else None,
            "requested": count, "short": short}


# ============================================================ 2. suggest a vocabulary for a situation
def suggest_words(topic, count, categories):
    """Suggest AAC words for a situation or 'words similar to …'.

    Uses the fast model and a tight token budget so CPU inference finishes
    in about a minute instead of hanging for 10+.
    """
    count = max(4, min(int(count or 12), 20))
    cats = list(categories)
    system = BASE + f"""
Task: suggest exactly {count} AAC vocabulary words for the situation or seed phrase given.
Pick words a non-speaking person would actually need to say or choose. Each must be easy to show as a simple pictogram.
Return ONLY JSON: {{"words":[{{"word":"..","category":".."}}]}}
category must be exactly one of: {json.dumps(cats)}.
Keep each "word" to 1-2 plain English words."""
    key = _hash("suggest", topic, count)
    cached = cache_get(key)
    if cached:
        return cached
    # fast model + low token budget = less hang time on CPU
    r = ask_json(system, f"Situation or seed: {topic}", cfg.fast_model, 200 + 30 * count)
    items = r.get("words") if isinstance(r, dict) else r
    out = []
    for it in items or []:
        if isinstance(it, dict) and it.get("word"):
            cat = it.get("category") if it.get("category") in categories else "Uncategorized"
            out.append({"word": str(it["word"]).strip(), "category": cat})
        elif isinstance(it, str) and it.strip():
            out.append({"word": it.strip(), "category": "Uncategorized"})
    if not out:
        raise AIError("The AI did not return any words. Try a shorter situation, e.g. “bedtime” or “words like hungry”.")
    cache_set(key, out)
    return out


# ============================================================ 3. prepare words: best search term + category, one call


# ============================================================ 4. pick the best pictogram among ARASAAC results
def _describe(item):
    kws = []
    for k in item.get("keywords", [])[:4]:
        if isinstance(k, dict):
            kws.append((k.get("keyword") or "") + (f" ({k['meaning']})" if k.get("meaning") else ""))
        else:
            kws.append(str(k))
    tags = ", ".join(str(t) for t in (item.get("tags") or [])[:5])
    cats = ", ".join(str(t) for t in (item.get("categories") or [])[:4])
    return "; ".join(x for x in kws if x) + (f" | tags: {tags}" if tags else "") + (f" | categories: {cats}" if cats else "")


def pick_candidate(word, term, candidates):
    """candidates: ARASAAC result dicts. Returns the index of the best one (0 on any problem)."""
    if len(candidates) < 2 or not cfg.ready or not cfg.pick_best:
        return 0
    if _recently_failed(("pick", word)):
        return 0
    descs = [{"i": i, "id": c.get("_id"), "about": _describe(c)} for i, c in enumerate(candidates)]
    key = _hash("pick", word, term, [d["id"] for d in descs])
    cached = cache_get(key)
    if isinstance(cached, int) and 0 <= cached < len(candidates):
        return cached
    system = BASE + """
Task: choose which pictogram best represents the AAC word, from the numbered candidates (described by keywords).
Prefer the most literal, common, everyday meaning of the word as used in AAC (e.g. "play" = children playing,
not a theatre play; "stop" = stop sign/hand). Return {"best": <index>}."""
    try:
        r = ask_json(system, json.dumps({"word": word, "searched_as": term, "candidates": descs}), cfg.fast_model, 80)
        i = int(r.get("best", 0))
        i = i if 0 <= i < len(candidates) else 0
        cache_set(key, i)
        log.info("AI picked picture #%d for '%s'", i, word)
        return i
    except Exception as e:
        _FAIL[("pick", word)] = time.time()
        log.warning("AI picture pick failed for '%s': %s", word, e)
        return 0


# ============================================================ 5. AI-drawn pictograms (shape DSL -> shapely)
DRAW_SYSTEM = BASE + """
Task: design a bold, simple black-and-white pictogram of the given AAC word on a 100x100 canvas
(origin top-left, y points DOWN), in the style of AAC symbol sets: ONE clear subject, thick solid shapes, no text.
Build it from these primitives, drawn in order. Each may have "op":"add" (default) or "cut" (erases from what is drawn so far):
 {"t":"circle","cx":50,"cy":50,"r":20}
 {"t":"ellipse","cx":50,"cy":50,"rx":20,"ry":12,"rot":0}
 {"t":"rect","x":10,"y":10,"w":30,"h":20,"r":0,"rot":0}              (r = corner radius, rot in degrees)
 {"t":"poly","pts":[[x,y],[x,y],[x,y]]}                              (filled polygon, 3-24 points)
 {"t":"line","pts":[[x,y],[x,y]],"w":8}                              (thick polyline, rounded ends)
 {"t":"arc","cx":50,"cy":50,"r":25,"a0":0,"a1":180,"w":8}            (thick arc; degrees clockwise from +x on screen)
Rules: stay inside 6..94; every stroke and every gap at least 5 units (it is 3D printed about 2 cm wide); at most 12
primitives (fewer is better - keep it simple); people are simple solid silhouettes; abstract words (now, more, stop, finished) use one clear metaphor
(clock, plus signs, hand/stop bar, tick). Return {"shapes":[...]}.
Example for "home": {"shapes":[{"t":"poly","pts":[[50,10],[92,48],[8,48]]},{"t":"rect","x":18,"y":46,"w":64,"h":44},
{"t":"rect","x":42,"y":62,"w":16,"h":28,"op":"cut"}]}"""


def shapes_to_geometry(shapes):
    from shapely.affinity import rotate, scale, translate
    from shapely.geometry import LineString, Point, Polygon, box
    import math

    def f(v, lo=-20.0, hi=120.0):
        return min(max(float(v), lo), hi)

    geom = Polygon()
    for s in (shapes or [])[:40]:
        try:
            t, op = s.get("t"), s.get("op", "add")
            if t == "circle":
                g = Point(f(s["cx"]), f(s["cy"])).buffer(max(f(s["r"], 1, 80), 1), 48)
            elif t == "ellipse":
                g = scale(Point(0, 0).buffer(1, 48), max(f(s["rx"], 1, 80), 1), max(f(s["ry"], 1, 80), 1))
                g = translate(rotate(g, f(s.get("rot", 0), -360, 360), origin=(0, 0)), f(s["cx"]), f(s["cy"]))
            elif t == "rect":
                x, y, w, h = f(s["x"]), f(s["y"]), max(f(s["w"], 1, 140), 1), max(f(s["h"], 1, 140), 1)
                r = min(max(float(s.get("r", 0)), 0), w / 2, h / 2)
                g = box(x + r, y + r, x + w - r, y + h - r).buffer(r, 16) if r > 0.5 else box(x, y, x + w, y + h)
                if s.get("rot"):
                    g = rotate(g, f(s["rot"], -360, 360), origin=(x + w / 2, y + h / 2))
            elif t == "poly":
                g = Polygon([(f(a), f(b)) for a, b in s["pts"]]).buffer(0)
            elif t == "line":
                g = LineString([(f(a), f(b)) for a, b in s["pts"]]).buffer(max(f(s.get("w", 8), 1, 40), 4) / 2, cap_style=1, join_style=1)
            elif t == "arc":
                cx, cy, r = f(s["cx"]), f(s["cy"]), max(f(s["r"], 1, 80), 1)
                a0, a1 = float(s["a0"]), float(s["a1"])
                n = max(8, int(abs(a1 - a0) / 6))
                pts = [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
                        cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]
                g = LineString(pts).buffer(max(f(s.get("w", 8), 1, 40), 4) / 2, cap_style=1, join_style=1)
            else:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        if g.is_empty:
            continue
        geom = geom.difference(g) if op == "cut" else geom.union(g)
    geom = scale(geom, 1, -1, origin=(50, 50)).buffer(0)  # screen (y down) -> model (y up)
    if geom.is_empty or geom.area < 80:
        raise AIError("The AI drawing came out empty.")
    return geom


def _icon_file(word):
    name = re.sub(r"[^a-z0-9_-]+", "_", word.lower().strip()) or "icon"
    return AI_ICON_DIR / (name + ".json")


def draw_icon_geometry(word, force_new=False):
    """Returns a shapely geometry (100x100 units, y up) for the word. Cached on disk. Raises AIError."""
    AI_ICON_DIR.mkdir(parents=True, exist_ok=True)
    p = _icon_file(word)
    if p.exists() and not force_new:
        try:
            return shapes_to_geometry(json.loads(p.read_text(encoding="utf-8")))
        except Exception as e:
            log.warning("Saved AI icon for '%s' unusable (%s) - redrawing", word, e)
    if _recently_failed(("draw", word.lower())) and not force_new:
        raise AIError("AI drawing failed a moment ago; try again in a minute.")
    try:
        r = ask_json(DRAW_SYSTEM, f"Word: {word}", cfg.smart_model, 1200)   # v14.8: 3000 meant ~15 min per icon on a CPU
        shapes = r.get("shapes") if isinstance(r, dict) else r
        geom = shapes_to_geometry(shapes)
        p.write_text(json.dumps(shapes), encoding="utf-8")
        log.info("AI drew an icon for '%s' (%d shapes)", word, len(shapes))
        return geom
    except Exception:
        _FAIL[("draw", word.lower())] = time.time()
        raise


# ============================================================ 6. visual quality check of a rendered tag


# ============================================================ 7. explain problems in plain English
def explain_log(log_text, diag_text=""):
    system = ("You are a patient, friendly Windows tech helper for a non-technical person who makes AAC tags with "
              "a Python app called AAC Tag Studio. Read the log and diagnostics and explain in plain English: "
              "1) what is wrong (at most 3 problems, most important first), 2) the exact steps to fix each one "
              "(mention button names and file names), 3) what you are unsure about. If everything looks fine, say so. "
              "Keep it under 200 words. No JSON.")
    return call(system, f"=== diagnostics ===\n{diag_text[-3000:]}\n\n=== log (latest lines) ===\n{log_text[-6000:]}",
                cfg.smart_model, 900)


def ping():
    return call("Reply with the single word OK.", "ping", cfg.fast_model, 10, timeout=20)


_VISION_HINTS = ("vision", "llava", "qwen2-vl", "qwen-vl", "minicpm-v", "moondream",
                 "bakllava", "cogvlm", "llama3.2-vision", "gemma3")


