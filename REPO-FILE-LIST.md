# What goes into the GitHub repository

Drop these files at the repository root, plus the app tree below. This list
matches the v15.74 release zip (158 entries) plus the 6 new launch files.
Repository name: ACC Tag Studio (`acc-tag-studio`).

## New files (this package)

- `README.md` — the repository front page.
- `LICENSE` — MIT License for the app's own code (copyright
  Cody Waidelich).
- `NOTICE.md` — third-party notices, including the exact Stability AI
  attribution text the Community License requires.
- `LICENSE-STABILITY-AI-COMMUNITY.md` — full Stability AI Community
  License Agreement (last updated July 5, 2024).
- `.gitignore` — keeps private and generated files out of the repo.
- `REPO-FILE-LIST.md` — this file.

## App tree (from the v15.74 release, verbatim, except the two EXE build scripts — see below)

Python source (15 files):

- `aac_tag.py` (entry point), `aac_gui.py`, `aac_core.py`, `aac_ai.py`,
  `aac_boards.py`, `aac_pdfedit.py`, `aac_glview.py`, `aac_3dview.py`,
  `aac_live3d.py`, `aac_3mf.py`, `aac_common.py`, `aac_diag.py`,
  `aac_import.py`, `aac_import_ui.py`, `aac_style.py`

Launchers and docs:

- `Launch_AAC_Tag_Studio.bat`, `Install_Deps.bat`, `Run_Diagnostics.bat`
- `Build_EXE.bat` and `Build_EXE_Debug.bat` are NOT included: the Windows
  exe ships later (see `README.md`), so the exe build scripts stay out
  until then.
- `ARCHITECTURE.md`, `CHANGELOG.md`, `README.txt`, `requirements.txt`

Built-in pictures:

- `icons/` — 125 files (124 pictures + `README.txt`). Copy the folder whole.

3D browser viewer (legacy fallback):

- `viewer3d/three.min.js`, `viewer3d/STLLoader.js`,
  `viewer3d/OrbitControls.js`

Offline install wheels (about 3.2 MB total):

- `wheels/pyopengl-3.1.10-py3-none-any.whl`
- `wheels/pyopengltk-0.0.4-py2.py3-none-any.whl`

Local AI folder — README only:

- `local_ai/README.txt`

## Do NOT include (and why)

- `settings.json` — private: your saved options, and it can hold secrets
  (for example the OpenSymbols API secret fallback).
- `icon_index.json`, `ai_config.json` — runtime caches, rebuilt on launch.
- `logs/`, `output/`, `*.log`, `STARTUP_ERROR.txt` — local run output.
- `dist/`, `build/`, `*.spec` — PyInstaller build products, rebuilt by
  `Build_EXE.bat`.
- `local_ai/` model files (`.gguf`, checkpoints) and engine binaries —
  several GB, over GitHub's per-file limit, and licensed separately.
  Users download them separately per `local_ai/README.txt`.
- `__pycache__/` — Python bytecode, regenerated automatically.

## Decided

- Repository name: ACC Tag Studio (`acc-tag-studio`).
- Copyright holder in `LICENSE`: Cody Waidelich.

## Still to decide before publishing

1. License for the app's code: MIT is drafted in `LICENSE`. Say the word
   if you want a different one.

## Still to do at publish time (not in this package)

- Create the repository on GitHub (needs your GitHub account and an
  internet-connected machine) and push these files.
- Create the first Release and attach the full zip (not the update-only
  zip — new users have no existing folder).
- Finish the real Windows installer (an Inno Setup draft is parked from
  earlier work) instead of shipping zip + .bat.
- The GitHub Pages one-page site linking to the latest Release.
