"""Dependency checking, auto-install and the self-test / diagnostics report. Standard library only at import time."""
import importlib
import importlib.util
import platform
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from aac_common import APP_DIR, APP_NAME, APP_VERSION, DATA_DIR, ICON_DIR, LOG_DIR, OUT_DIR, SETTINGS_FILE, log

REQUIRED = [("numpy", "numpy"), ("cv2", "opencv-python-headless"), ("shapely", "shapely"),
            ("trimesh", "trimesh"), ("mapbox_earcut", "mapbox-earcut"), ("matplotlib", "matplotlib"),
            ("PIL", "Pillow"), ("customtkinter", "customtkinter")]


def _has(mod):
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def missing_packages(gui=True):
    """Looks in the Python you already have - nothing is installed if it is already there."""
    return [pip for mod, pip in REQUIRED if (gui or mod != "customtkinter") and not _has(mod)]


# v15.46: the OpenGL preview's packages. They are checked first
# (find_spec) exactly like every other package, and installed to the
# same site-packages with the same plain `pip install` — no --target,
# no new location. The only difference from the other packages is that
# the GUI startup path installs these WITHOUT asking (see
# ensure_gl_deps), because the preview needs them on first run.
# v15.47: they are deliberately NOT in REQUIRED anymore — a missing
# preview package must never stop the whole program from opening (it
# did, on an offline machine, in 15.46: ensure_deps returned False and
# main exited before the GUI). ensure_gl_deps owns them now, and a
# failed install just leaves the preview cell showing its message.
GL_REQUIRED = [("OpenGL", "PyOpenGL"), ("pyopengltk", "pyopengltk")]
WHEELS_DIR = "wheels"  # v15.47: app-folder subfolder holding .whl files


def _wheel_version(path):
    """Sortable version tuple from a wheel filename (pyopengl-3.1.10-...)."""
    try:
        parts = Path(path).name.split("-")
        return tuple(int(p) for p in parts[1].split(".") if p.isdigit())
    except Exception:
        return ()


def find_gl_wheels():
    """Local .whl files for the GL packages in the app folder's wheels/
    subfolder (highest version wins per package). Offline installs use
    these before ever touching the internet."""
    try:
        folder = Path(APP_DIR) / WHEELS_DIR
        if not folder.is_dir():
            return []
        found = {}
        for f in folder.iterdir():
            name = f.name.lower()
            if not name.endswith(".whl"):
                continue
            # Match on the DISTRIBUTION name (pip name), not the module
            # name: the wheel is pyopengl-*.whl but the module is OpenGL.
            for mod, pip in GL_REQUIRED:
                stem = pip.lower()
                if name.startswith(stem + "-") or name.startswith(stem.replace("-", "_") + "-"):
                    if mod not in found or _wheel_version(f) > _wheel_version(found[mod]):
                        found[mod] = f
        return [str(found[mod]) for mod, _ in GL_REQUIRED if mod in found]
    except Exception:
        return []


def ensure_gl_deps():
    """Get the OpenGL preview packages at startup when missing.

    Check-first: if both modules already import, nothing runs at all.
    Otherwise, in order: (1) local .whl files from the app folder's
    wheels/ subfolder, installed with --no-index (no internet needed);
    (2) the normal internet install. Never prompts. Returns True if
    both are importable afterwards. On failure the caller carries on
    anyway — the preview cell shows a message instead of a blank, and
    the program still opens.
    """
    if getattr(sys, "frozen", False):
        return _has("OpenGL") and _has("pyopengltk")
    miss = [pip for mod, pip in GL_REQUIRED if not _has(mod)]
    if not miss:
        return True
    wheels = find_gl_wheels()
    if wheels:
        log.info("OpenGL preview packages missing, installing from local wheels: %s", ", ".join(wheels))
        print("First run: installing the 3D preview packages from the wheels folder ...")
        # One wheel at a time: a single --no-index command fails whole
        # if any dependency is missing, so a partial folder would
        # otherwise install nothing at all. GL_REQUIRED order already
        # puts PyOpenGL (the dependency) first.
        for w in wheels:
            install([w], no_index=True)
        miss = [pip for mod, pip in GL_REQUIRED if not _has(mod)]
        if not miss:
            return True
    log.info("OpenGL preview packages missing, installing: %s", ", ".join(miss))
    print(f"First run: installing the 3D preview packages ({', '.join(miss)}) ...")
    if install(miss):
        return all(_has(mod) for mod, _ in GL_REQUIRED)
    log.warning("OpenGL preview packages still missing; the program will open with a message in the preview cell")
    return False


def have_tkinter():
    return _has("tkinter")


def install(pkgs, no_index=False):
    if getattr(sys, "frozen", False):
        # v14.8: in the packaged EXE sys.executable IS the app - "-m pip" would launch it again
        log.warning("Not running pip inside the packaged app (missing: %s)", pkgs)
        return False
    flag = ["--no-index"] if no_index else []
    cmds = [[sys.executable, "-m", "pip", "install", *flag, *pkgs],
            [sys.executable, "-m", "pip", "install", "--user", *flag, *pkgs]]
    for cmd in cmds:
        print("Running:", " ".join(cmd), "\n(this can take a minute) ...")
        log.info("Running %s", " ".join(cmd))
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               stdin=subprocess.DEVNULL, timeout=900)
        except Exception as e:
            log.error("Could not run pip: %s", e)
            continue
        log.debug("pip stdout tail: %s", r.stdout[-1500:])
        if r.returncode == 0:
            log.info("pip install succeeded")
            return True
        log.warning("pip failed (code %s): %s", r.returncode, r.stderr[-1500:])
        print(r.stderr[-800:])
    return False


def _version(mod, pip):
    try:
        from importlib.metadata import version
        for name in {pip, mod, pip.replace("-headless", ""), "opencv-python" if mod == "cv2" else pip}:
            try:
                return version(name)
            except Exception:
                pass
    except Exception:
        pass
    try:
        return getattr(importlib.import_module(mod), "__version__", "installed")
    except Exception as e:
        return f"import FAILED: {type(e).__name__}: {e}"


def diagnose():
    """Returns (report_text, all_ok)."""
    out, ok = [], True

    def line(s=""):
        out.append(s)

    def check(label, good, detail=""):
        nonlocal ok
        if not good:
            ok = False
        line(f"  [{'OK' if good else 'FAIL'}] {label} {detail}".rstrip())

    line(f"{APP_NAME} {APP_VERSION} - diagnostics  ({time.strftime('%Y-%m-%d %H:%M:%S')})")
    line("=" * 64)
    line("System")
    line(f"  Windows/OS : {platform.platform()}")
    line(f"  Python     : {sys.version.split()[0]} ({platform.architecture()[0]})  {sys.executable}")
    check("Python 3.9 or newer", sys.version_info >= (3, 9), "(older versions are not supported)")
    if sys.version_info >= (3, 13):
        line("  [note] Very new Python: if a package fails to install, a matching wheel may not exist yet.")
        line("         Python 3.11 or 3.12 is the safest choice.")
    check("tkinter (window toolkit)", have_tkinter(), "" if have_tkinter() else
          "-> re-run the Python installer, Modify, and tick 'tcl/tk and IDLE'")
    line("")
    line("Packages (looked up in the Python above)")
    for mod, pip in REQUIRED:
        if _has(mod):
            v = _version(mod, pip)
            check(pip, "FAILED" not in v, v)
        else:
            check(pip, False, f"MISSING  -> python -m pip install {pip}")
    line("")
    line("OpenGL preview (informational only — a missing preview package never fails diagnostics)")
    for mod, pip in GL_REQUIRED:
        if _has(mod):
            line(f"  [OK] {pip} {_version(mod, pip)}")
        else:
            line(f"  [MISSING] {pip}  -> put its .whl in the wheels folder or run Install_Deps.bat")
    try:
        _w = find_gl_wheels()
        line(f"  wheels folder: {Path(APP_DIR) / WHEELS_DIR} ({len(_w)} wheel(s) found)")
    except Exception:
        pass
    line("")
    line("Folders")
    for name, p in [("data", DATA_DIR), ("logs", LOG_DIR), ("icons", ICON_DIR), ("output", OUT_DIR)]:
        try:
            Path(p).mkdir(parents=True, exist_ok=True)
            t = Path(p) / ".t"
            t.write_text("x")
            t.unlink()
            check(f"{name} folder writable", True, str(p))
        except Exception as e:
            check(f"{name} folder writable", False, f"{p} ({e})")
    line(f"  settings file: {SETTINGS_FILE}")
    line("")
    line("Network (optional - only needed for online icons)")
    try:
        req = urllib.request.Request("https://api.arasaac.org/v1/pictograms/en/bestsearch/home",
                                     headers={"User-Agent": "aac-tag-studio"})
        with urllib.request.urlopen(req, timeout=8) as r:
            line(f"  [OK] ARASAAC reachable (HTTP {r.status})")
    except Exception as e:
        line(f"  [warn] ARASAAC not reachable: {type(e).__name__}: {e}")
        line("         Online icons will not work, local icons in the icons folder still do.")
    line("")
    line("AI (optional - needs an Anthropic API key)")
    try:
        import aac_ai
        if not aac_ai.cfg.ready:
            line("  [info] No API key set. AI features are off; everything else works. (AI tab > Set up AI)")
        else:
            try:
                aac_ai.ping()
                line(f"  [OK] AI connected (fast: {aac_ai.cfg.fast_model}, smart: {aac_ai.cfg.smart_model})")
            except Exception as e:
                line(f"  [warn] AI key is set but the test failed: {e}")
    except Exception as e:
        line(f"  [warn] AI module problem: {type(e).__name__}: {e}")
    line("")
    line("Engine self-test (builds the tag 'now' offline and writes STL + 3MF to a temp folder)")
    if missing_packages(gui=False):
        check("engine self-test", False, "skipped - install the missing packages first")
    else:
        try:
            import zipfile
            import xml.etree.ElementTree as ET
            import aac_core as c
            t0 = time.time()
            tmp = Path(tempfile.mkdtemp(prefix="aac_selftest_"))
            o = c.Opts(icon_source=c.SOURCES[1])
            rep = c.export_words(["now"], tmp, o)
            names = {p.name for p in rep["files"]}
            check("tag geometry + STL export", {"now_base.stl", "now_ink.stl"} <= names, ", ".join(sorted(names)))
            if "now.3mf" in names:
                with zipfile.ZipFile(tmp / "now.3mf") as z:
                    ET.fromstring(z.read("3D/3dmodel.model"))
                check("3MF export is valid", True)
            else:
                check("3MF export is valid", False)
            check("no failures", not rep["failed"], str(rep["failed"]) if rep["failed"] else f"({time.time() - t0:.1f}s)")
        except Exception as e:
            log.exception("Self-test failed")
            check("engine self-test", False, f"{type(e).__name__}: {e}")
    line("")
    line("RESULT: " + ("everything looks good." if ok else "problems found - see [FAIL] lines above."))
    text = "\n".join(out)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        (LOG_DIR / "diagnostics.txt").write_text(text, encoding="utf-8")
    except Exception:
        pass
    log.info("Diagnostics finished: %s", "OK" if ok else "PROBLEMS FOUND")
    return text, ok
