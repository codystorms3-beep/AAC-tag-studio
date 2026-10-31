# AAC Tag Studio — architecture note (v3.6.0-exp)

This note exists so a future iOS port only has to replace the UI layer,
not the product.

## Engine (portable, GUI-free, iOS-reusable)
These modules import nothing from tkinter/customtkinter and have no GUI
code. They are the product logic and can travel to any platform:

- `aac_core.py`  — tag geometry: bodies, ink, shapes, braille dots,
  snap connectors, magnet recess, keyring loop, STL + 3MF export, plate packing
- `aac_import.py` / `aac_import_ui.py` — board import: reads a PDF/picture of a communication board (grid detection,
  cell pictures, labels via PDF text or OCR) and the review/apply screens.
- `aac_boards.py` — printable PDFs: PECS cards, choice boards, schedules,
  sentence strips, core boards, about-me sheets, token boards, offline packs
- `aac_3mf.py`   — 3MF container writer
- `aac_ai.py`    — AI helpers (word suggestions, picture picking, icon drawing,
  quality review, plain-English commands); cloud + local backends
- `aac_common.py`— constants, paths, logging, settings helpers
- `aac_diag.py`  — diagnostics bundle

## Shell (platform-specific, replace per platform)
- `aac_gui.py` — the Windows/macOS/Linux desktop UI (customtkinter).
  **Do not put product logic here** — it calls into the engine modules above.
- `aac_tag.py` — CLI entry point (already headless; works anywhere Python runs)

## iOS plan (later)
customtkinter cannot run on iOS. The realistic paths are:
1. Keep the engine in Python and ship it via BeeWare Briefcase / Kivy,
   writing a new touch UI on top of the same engine calls, or
2. Re-implement a thin native (SwiftUI) UI that shells out to the engine
   as a library.

Either way, the rule is: **new features go in the engine modules**,
the GUI only collects input and displays output. The v3.6.0-exp Boards
features (strip/core/about-me/token) were built this way on purpose —
every one of them is a plain engine function with a thin GUI wrapper.
