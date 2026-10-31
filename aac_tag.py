"""
AAC Tag Studio 3.0 - entry point.

GUI:   python aac_tag.py
CLI:   python aac_tag.py now dinner stop [--file words.txt] [--out folder] [--size 40] [--offline]
Other: --diagnose (full health report)   --install-deps (install missing packages without asking)
"""
import argparse
import sys
import traceback

from aac_common import APP_NAME, APP_VERSION, FROZEN, LOG_FILE, OUT_DIR, install_excepthooks, log, report_fatal_error, setup_logging
import aac_diag


def parse(argv):
    ap = argparse.ArgumentParser(description=f"{APP_NAME} {APP_VERSION}")
    ap.add_argument("words", nargs="*")
    ap.add_argument("--file")
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--size", type=float, default=40.0)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--diagnose", action="store_true")
    ap.add_argument("--install-deps", action="store_true")
    return ap.parse_args(argv)


def ensure_deps(gui, auto):
    """Uses the packages already on this computer; only installs what is genuinely missing."""
    if FROZEN:
        return True
    if gui and not aac_diag.have_tkinter():
        msg = ("Python's window toolkit (tkinter) is not installed.\n"
               "Fix: run the Python installer again -> Modify -> tick 'tcl/tk and IDLE'.")
        log.critical(msg.replace("\n", " "))
        print(msg)
        return False
    miss = aac_diag.missing_packages(gui)
    if not miss:
        log.info("All required packages already installed")
        return True
    log.warning("Missing packages: %s", ", ".join(miss))
    print(f"\nThese packages are not installed yet: {', '.join(miss)}")
    do = auto
    if not do and sys.stdin is not None and sys.stdin.isatty():
        try:
            do = input("Install them now with pip? [Y/n] ").strip().lower() in ("", "y", "yes")
        except EOFError:
            do = False
    if do and aac_diag.install(miss):
        miss = aac_diag.missing_packages(gui)
        if not miss:
            return True
    print(f"\nStill missing: {', '.join(miss)}")
    print(f"Install by hand with:\n  {sys.executable} -m pip install {' '.join(miss)}")
    print(f"Then run Run_Diagnostics.bat. Details: {LOG_FILE}")
    return False


def main(argv=None):
    a = parse(argv)
    setup_logging()
    install_excepthooks()
    if a.diagnose:
        text, ok = aac_diag.diagnose()
        print(text)
        return 0 if ok else 1
    words = list(a.words)
    if a.file:
        with open(a.file, encoding="utf-8") as fh:
            words += [l.strip() for l in fh if l.strip()]
    gui = not words
    if gui:
        # v15.46: the OpenGL preview packages install themselves on
        # first run (check-first, same site-packages as everything
        # else). v15.47: local wheels/ files first (offline), internet
        # second. A failed install does not block startup — the preview
        # cell says so, and the program still opens.
        try:
            aac_diag.ensure_gl_deps()
        except Exception:
            pass
    if not ensure_deps(gui, a.install_deps):
        return 2
    print(f"Starting {APP_NAME} {APP_VERSION} (the first start can take 20-30 s while fonts are cached) ...")
    if gui:
        import aac_gui
        aac_gui.run_gui()
        return 0
    import aac_core as core
    o = core.Opts(size=a.size, icon_source=core.SOURCES[1] if a.offline else core.SOURCES[0])
    rep = core.export_words(words, a.out, o, progress=lambda f, t: print(t))
    for n in rep["notes"]:
        print(n)
    for w, e in rep["failed"]:
        print(f"FAILED {w}: {e}")
    return 1 if rep["failed"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        tb = traceback.format_exc()
        try:
            log.critical("Fatal error", exc_info=True)
        except Exception:
            pass
        traceback.print_exc()
        print(f"\nA fatal error occurred. Details were saved to:\n  {LOG_FILE}")
        # v15.67: in the windowed EXE there is no console, so also leave a
        # visible dialog + STARTUP_ERROR.txt instead of dying silently.
        try:
            report_fatal_error(tb)
        except Exception:
            pass
        sys.exit(1)
