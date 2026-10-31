"""Shared paths, logging and issue tracking. Standard library only, so it can never fail on a missing pip package."""
import collections
import logging
import logging.handlers
import os
import platform
import sys
import tempfile
import threading
from pathlib import Path

APP_NAME = "AAC Tag Studio"
APP_VERSION = "15.74"
FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent


def _writable(p):
    try:
        p.mkdir(parents=True, exist_ok=True)
        t = p / ".write_test"
        t.write_text("x")
        t.unlink()
        return True
    except Exception:
        return False


def _data_dir():
    cands = [APP_DIR]
    la = os.environ.get("LOCALAPPDATA")
    if la:
        cands.append(Path(la) / "AACTagStudio")
    cands.append(Path(tempfile.gettempdir()) / "AACTagStudio")
    for c in cands:
        if _writable(c):
            return c
    return APP_DIR


DATA_DIR = _data_dir()          # APP_DIR when writable, otherwise %LOCALAPPDATA%\AACTagStudio
LOG_DIR = DATA_DIR / "logs"
ICON_DIR = DATA_DIR / "icons"
OUT_DIR = DATA_DIR / "output"
SETTINGS_FILE = DATA_DIR / "settings.json"
LOG_FILE = LOG_DIR / "aac_tag.log"

log = logging.getLogger("aac")


class IssueTracker(logging.Handler):
    """Keeps every WARNING/ERROR/CRITICAL of this session so the GUI can show a badge."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records = collections.deque(maxlen=300)


ISSUES = IssueTracker()


def setup_logging():
    if log.handlers:
        return log
    log.setLevel(logging.DEBUG)
    log.propagate = False
    full = logging.Formatter("%(asctime)s [%(levelname)s] (%(threadName)s) %(funcName)s: %(message)s")
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=512_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(full)
        fh.setLevel(logging.DEBUG)
        log.addHandler(fh)
    except Exception as e:  # never let logging stop the app
        print(f"[warn] could not open log file: {e}")
    if sys.stderr is not None:
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        log.addHandler(ch)
    ISSUES.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s", "%H:%M:%S"))
    log.addHandler(ISSUES)
    log.info("==== %s %s starting ====", APP_NAME, APP_VERSION)
    log.info("Python %s | %s | frozen=%s", sys.version.split()[0], platform.platform(), FROZEN)
    log.info("Interpreter: %s", sys.executable)
    log.info("Data folder: %s", DATA_DIR)
    return log


def install_excepthooks():
    def hook(t, v, tb):
        if issubclass(t, KeyboardInterrupt):
            return
        log.critical("Unhandled exception", exc_info=(t, v, tb))

    sys.excepthook = hook
    if hasattr(threading, "excepthook"):
        def thook(a):
            log.critical("Unhandled exception in thread %s", getattr(a.thread, "name", "?"),
                         exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
        threading.excepthook = thook


def report_fatal_error(text):
    """Make a fatal startup error VISIBLE in the frozen (windowed) app.

    A windowed PyInstaller build has no console, so a startup crash used to
    exit silently ("double-click and nothing happens"): the traceback went
    to print/logging, which nobody sees. This writes it to a plain-text
    file next to the program (and in the data folder) and tries to show a
    dialog with the log locations, so a failed launch always leaves
    something the user can see and send back. Never raises.
    """
    body = ("AAC Tag Studio could not start.\n\n" + str(text).strip() +
            f"\n\nFull log: {LOG_FILE}\n")
    for folder in (APP_DIR, DATA_DIR):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "STARTUP_ERROR.txt").write_text(body, encoding="utf-8")
        except Exception:
            pass
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        try:
            messagebox.showerror(
                APP_NAME,
                "AAC Tag Studio could not start.\n\n"
                "The details were saved to STARTUP_ERROR.txt next to the program "
                f"and to:\n  {LOG_FILE}")
        finally:
            root.destroy()
    except Exception:
        pass


def tail_log(n=250):
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except Exception as e:
        return f"(no log yet: {e})"
