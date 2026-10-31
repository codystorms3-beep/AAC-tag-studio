AAC Tag Studio v14.8

This build updates the existing AAC Tag Studio app rather than creating a separate PDF utility.

Highlights:
- Maker Studio / Tag Design: turn on Multiple tags, choose how many (All / 1–12),
  then click any tag in the preview to edit just that one (shape, style, colours…).
  Click empty space to edit all tags together again.
- Brighter colour scheme tuned for special-needs / AAC use (coral, teal, sunny accents).
- Words show instantly with a loading banner; AI (Local) is a full-screen view;
  Word Lists has a 📚 Library of all words & pictures with right-click send to the 3D tag / PDF Studio.
- PDF Studio: set Cards per page AND Number of pages (1–12). The word list auto-fills
  to match; preview defaults to “All pages” so you see every sheet before export.
- Tag Design tab: size / thickness / design depth update a live
  preview that includes a side-view strip showing thickness and design-depth layers.
- Log & diagnostics “✨ Explain” uses the same local GGUF AI (local_ai/ folder or one-click
  download + llama-cpp-python). No separate model required.
- Existing 3D printing, word management, board tools, and offline icon cache retained.

How to run (Windows)
--------------------
0. One-time dependency install (if Launch complains about missing packages):
     Double-click  Install_Deps.bat
   This runs pip install -r requirements.txt for you.

1. From source (recommended while testing):
     Double-click  Launch_AAC_Tag_Studio.bat
   The first run will also offer to install any missing packages listed in requirements.txt.

2. Diagnostics (if something fails):
     Double-click  Run_Diagnostics.bat
   Then send logs\diagnostics.txt and logs\aac_tag.log.

3. Build a standalone EXE:
     Double-click  Build_EXE.bat
   Requirements:
     - Python 3.10+ from python.org (tick “Add to PATH”)
     - Turn OFF the Microsoft Store python.exe / python3.exe app-execution aliases
   The bat installs requirements.txt + PyInstaller, builds dist\AACTagStudio.exe,
   and copies the icons folder next to it. First launch of the EXE can take 20–40 s.

Optional packages (auto-installed when available):
  keyring   – stores Anthropic API key in the OS credential store
  resvg-py  – rasterizes Mulberry SVG symbols to PNG

Note: the page preview is rendered in-app from the same layout parameters and local
icon lookup used by the PDF exporter. Fonts and image rasterization can vary slightly
by system/PDF viewer; verify the first exported page before printing a large batch.
