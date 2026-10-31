"""Board import screens (v14.9): pick a PDF/picture of a board, check what was found,
and put the words, pictures and layout into the app.

Mixed into App (aac_gui.py). All the reading is in aac_import.py (no GUI there).
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

import aac_boards
import aac_import
from aac_common import APP_NAME, log

_FILETYPES = [("Boards (PDF or picture)", "*.pdf *.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff"),
              ("PDF", "*.pdf"),
              ("Pictures", "*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff"),
              ("All files", "*.*")]


class BoardImportMixin:
    # ------------------------------------------------------------ 1. pick + read
    def import_board(self):
        """Import a communication board from a PDF or a picture."""
        if self.busy:
            self.flash_status("Still working on the last task — one moment…", ok=False)
            return
        p = filedialog.askopenfilename(title="Import a board (PDF or picture)", filetypes=_FILETYPES)
        if not p:
            return
        if Path(p).suffix.lower() == ".pdf" and not aac_import.pdf_supported():
            self._board_need_pdf_reader(p)
            return
        self._board_read(p)

    def _board_need_pdf_reader(self, path):
        """PDFs need the small PyMuPDF add-on: offer to install it, then carry on."""
        import sys
        if getattr(sys, "frozen", False):
            messagebox.showinfo(
                APP_NAME,
                "This copy of the app was built without the PDF reader.\n\n"
                "You can import a picture of the board (a screenshot or photo) instead, or rebuild "
                "with Build_EXE.bat (PyMuPDF is now in requirements.txt).")
            return
        if not messagebox.askyesno(
                APP_NAME,
                "Reading PDFs needs a small add-on (PyMuPDF, about 20 MB).\n\n"
                "Install it now?  (needs internet; under a minute)"):
            return

        def fn():
            import aac_diag
            return aac_diag.install(["pymupdf"])

        def done(ok):
            import importlib
            importlib.invalidate_caches()
            if ok and aac_import.pdf_supported():
                self._board_read(path)
            else:
                messagebox.showwarning(
                    APP_NAME,
                    "I couldn't install it automatically.\n\nOpen a Command Prompt and run:\n"
                    "    pip install pymupdf\nthen try again.")
        self.bg("Installing the PDF reader…", fn, done, lock=True)

    def _board_read(self, path, cols=None, rows=None):
        def prog(m):
            self.ui_call(lambda: self.flash_status(m, hold=True))

        def fn():
            try:
                return ("ok", aac_import.read_board(path, cols=cols, rows=rows, progress=prog))
            except aac_import.BoardError as e:
                return ("err", str(e))

        def done(res):
            kind, val = res
            if kind == "ok":
                self.flash_status(f"Board read: {sum(len(p.cells) for p in val)} cells", hold=False)
                self._board_review(val, Path(path))
                return
            if "grid of cells" in val and not (cols and rows):
                # no borders found - let the user say how many columns and rows there are
                import tkinter.simpledialog as sd
                ans = sd.askstring(
                    APP_NAME,
                    "I couldn't find cell borders on this board.\n\n"
                    "How many columns and rows does it have?  Type it like  8x6\n"
                    "(columns x rows), and I'll cut it evenly.", parent=self)
                if ans:
                    import re
                    m = re.match(r"^\s*(\d{1,2})\s*[xX×*,]\s*(\d{1,2})\s*$", ans)
                    if m and int(m.group(1)) * int(m.group(2)) >= 2:
                        self._board_read(path, cols=int(m.group(1)), rows=int(m.group(2)))
                        return
                    messagebox.showinfo(APP_NAME, "Please type it like 8x6 (columns x rows).")
                return
            messagebox.showwarning(APP_NAME, val)

        self.bg("Reading the board…", fn, done, lock=True)

    # ------------------------------------------------------------ 2. review window
    def _board_review(self, pages, src):
        win = ctk.CTkToplevel(self)
        win.title(f"Import board — {src.name}")
        win.geometry("1020x740")
        try:
            win.transient(self)
        except Exception:
            pass
        win.after(200, win.lift)

        n_cells = sum(len(p.cells) for p in pages)
        with_pic = sum(1 for p in pages for c in p.cells if c.image is not None)
        with_word = sum(1 for p in pages for c in p.cells if c.word)
        grid = " + ".join(f"{p.cols}×{p.rows}" for p in pages[:4]) + ("…" if len(pages) > 4 else "")
        ctk.CTkLabel(win, text=f"{len(pages)} page(s), {grid}  ·  {with_pic} pictures  ·  {with_word} words read",
                     font=ctk.CTkFont(weight="bold", size=14)).pack(pady=(12, 0))
        need = n_cells - with_word - sum(1 for p in pages for c in p.cells if c.blank)
        if need > 0:
            hint = (f"{need} cell(s) have a picture but no word yet — type them below. "
                    + ("" if aac_import.ocr_available() else
                       "(Words in a picture can't be read automatically unless the optional reader is "
                       "installed: pip install rapidocr-onnxruntime. PDFs with real text are read fine.)"))
        else:
            hint = "Check the words below, fix any that are wrong, and clear a word to skip that cell."
        ctk.CTkLabel(win, text=hint, text_color="gray", wraplength=960, justify="left",
                     font=ctk.CTkFont(size=11)).pack(padx=16, pady=(2, 6))

        # ---- options
        opt = ctk.CTkFrame(win, fg_color="transparent")
        opt.pack(fill="x", padx=16)
        v_words = ctk.BooleanVar(value=True)
        v_pics = ctk.BooleanVar(value=True)
        v_cards = ctk.BooleanVar(value=True)
        v_color = ctk.BooleanVar(value=any(c.tint for p in pages for c in p.cells))
        v_repl = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(opt, text="Add the words to my Word list", variable=v_words).grid(row=0, column=0, sticky="w", padx=(0, 18), pady=2)
        ctk.CTkCheckBox(opt, text="Save the pictures (these words will use them)", variable=v_pics).grid(row=0, column=1, sticky="w", padx=(0, 18), pady=2)
        ctk.CTkCheckBox(opt, text="Put them on PDF Studio cards in the same layout", variable=v_cards).grid(row=1, column=0, sticky="w", padx=(0, 18), pady=2)
        ctk.CTkCheckBox(opt, text="Keep the cell colours", variable=v_color).grid(row=1, column=1, sticky="w", padx=(0, 18), pady=2)
        ctk.CTkCheckBox(opt, text="Replace pictures I chose myself", variable=v_repl).grid(row=2, column=1, sticky="w", pady=2)
        nrow = ctk.CTkFrame(opt, fg_color="transparent")
        nrow.grid(row=2, column=0, sticky="w", pady=2)
        ctk.CTkLabel(nrow, text="Save as word list:").pack(side="left", padx=(0, 6))
        v_name = ctk.StringVar(value=src.stem[:40])
        ctk.CTkEntry(nrow, textvariable=v_name, width=190).pack(side="left")

        # ---- the cells
        sc = ctk.CTkScrollableFrame(win, fg_color="transparent")
        sc.pack(fill="both", expand=True, padx=12, pady=(8, 4))
        self._board_thumbs = []              # keep the images alive
        vars_ = {}
        for page in pages:
            if len(pages) > 1:
                ctk.CTkLabel(sc, text=f"Page {page.index + 1}", anchor="w",
                             font=ctk.CTkFont(weight="bold", size=12)).pack(fill="x", pady=(8, 2))
            g = ctk.CTkFrame(sc, fg_color="transparent")
            g.pack(fill="x")
            cw = max(78, min(122, 940 // max(1, page.cols) - 6))
            for c in range(page.cols):
                g.grid_columnconfigure(c, weight=1, uniform=f"p{page.index}")
            for cell in page.cells:
                tile = None
                if v_color.get() and cell.tint:
                    tile = aac_import.hex_to_tile(cell.tint)
                f = ctk.CTkFrame(g, corner_radius=8, fg_color=tile or ("gray88", "gray20"))
                f.grid(row=cell.row, column=cell.col, padx=2, pady=2, sticky="nsew")
                if cell.image is not None:
                    im = ctk.CTkImage(light_image=cell.image, dark_image=cell.image, size=(cw - 30, cw - 30))
                    self._board_thumbs.append(im)
                    ctk.CTkLabel(f, text="", image=im).pack(padx=4, pady=(4, 2))
                else:
                    ctk.CTkLabel(f, text="(no picture)", text_color="gray", height=cw - 30,
                                 font=ctk.CTkFont(size=10)).pack(padx=4, pady=(4, 2))
                var = ctk.StringVar(value=cell.word)
                vars_[(cell.page, cell.row, cell.col)] = var
                e = ctk.CTkEntry(f, textvariable=var, width=cw - 10, height=26, justify="center",
                                 placeholder_text="type word" if cell.image is not None else "")
                e.pack(padx=4, pady=(0, 5))

        ctk.CTkLabel(win, text="Only import boards you made or are licensed to use.",
                     text_color="gray", font=ctk.CTkFont(size=10)).pack()
        brow = ctk.CTkFrame(win, fg_color="transparent")
        brow.pack(fill="x", padx=16, pady=(4, 12))
        ctk.CTkButton(brow, text="Cancel", width=100, fg_color="gray30", hover_color="#555",
                      command=win.destroy).pack(side="right")

        def go():
            words_map = {k: v.get().strip() for k, v in vars_.items()}
            opts = dict(words=v_words.get(), pics=v_pics.get(), cards=v_cards.get(),
                        colors=v_color.get(), replace=v_repl.get(), name=v_name.get().strip())
            win.destroy()
            try:
                self._board_apply(pages, words_map, opts, src)
            except Exception as e:
                log.exception("board apply failed")
                messagebox.showerror(APP_NAME, f"The import stopped part-way:\n{e}\n\nSee Log for details.")
        ctk.CTkButton(brow, text="Import", width=140, fg_color="#4ECDC4", hover_color="#6EDDD6",
                      text_color="#10262a", command=go).pack(side="right", padx=(0, 8))

    # ------------------------------------------------------------ 3. apply
    def _board_apply(self, pages, words_map, opts, src):
        slots, tiles, pics = [], {}, {}
        for page in pages:
            for cell in page.cells:             # row by row, left to right
                w = words_map.get((cell.page, cell.row, cell.col), "").strip()
                idx = len(slots)
                slots.append(w)
                if not w:
                    continue
                if opts["colors"] and cell.tint:
                    tiles[str(idx)] = aac_import.hex_to_tile(cell.tint)
                if cell.image is not None and w.lower() not in {k.lower() for k in pics}:
                    pics[w] = cell.image
        words = []
        seen = set()
        for w in slots:
            if w and w.lower() not in seen:
                seen.add(w.lower())
                words.append(w)
        if not words:
            return messagebox.showinfo(APP_NAME, "No words to import — type a word in at least one cell.")

        # -- pictures (before the words, so the picture lookup sees them)
        n_pics = 0
        if opts["pics"] and pics:
            tmp = Path(tempfile.mkdtemp(prefix="aac_board_"))
            try:
                for w, im in pics.items():
                    if w in self.overrides and not opts["replace"]:
                        continue                    # never overwrite a picture you chose yourself
                    f = tmp / "pic.png"
                    im.save(f, "PNG")
                    self.overrides[w] = self._save_upload_to_library(w, str(f))
                    self._no_auto_pic.discard(w)
                    n_pics += 1
            finally:
                import shutil
                shutil.rmtree(tmp, ignore_errors=True)

        # -- words
        n_new = 0
        if opts["words"]:
            existing = self.get_words()
            known = {x.lower() for x in existing}
            add = [w for w in words if w.lower() not in known]
            if add:
                self.set_words(existing + add)
            n_new = len(add)
            name = opts["name"]
            if name:
                base, k = name, 2
                while name in self.word_lists and list(self.word_lists[name]) != words:
                    name = f"{base} ({k})"
                    k += 1
                self.word_lists[name] = list(words)
                try:
                    self.refresh_my_lists()
                except Exception:
                    pass

        # -- PDF Studio cards, in the board's own layout
        placed = False
        if opts["cards"]:
            cur = [w for w in getattr(self, "_pdf_words", []) if str(w).strip()]
            go_on = True
            if cur:
                go_on = messagebox.askyesno(
                    APP_NAME,
                    f"PDF Studio already has {len(cur)} card(s).\n\n"
                    "Replace them with this board?\n\n"
                    "Yes = replace (the board's layout is used)\n"
                    "No = keep my cards (the words and pictures are still added)")
            if go_on:
                cols, rows = pages[0].cols, pages[0].rows
                if any((p.cols, p.rows) != (cols, rows) for p in pages):
                    messagebox.showinfo(APP_NAME, "The pages have different grid sizes, so PDF Studio uses the "
                                                  f"first page's grid ({cols}×{rows}).")
                name = aac_boards.ensure_layout(cols, rows)
                try:
                    self.pdf_layout_menu.configure(values=list(aac_boards.PECS_LAYOUTS.keys()))
                except Exception:
                    pass
                self.pdf_layout_var.set(name)
                self._pdf_words = list(slots)
                self.pdf_tile_colors.clear()
                self.pdf_tile_colors.update(tiles)
                self._pdf_selected = None
                try:
                    self._pdf_blank_selected = None
                except Exception:
                    pass
                per = cols * rows
                self.pdf_pages_var.set(str(min(12, max(1, -(-len(slots) // per)))))
                self._pdf_on_layout_or_pages_change()
                placed = True

        self.changed()
        self.save()
        bits = []
        if opts["words"]:
            bits.append(f"{n_new} new word(s)")
        if n_pics:
            bits.append(f"{n_pics} picture(s)")
        if placed:
            bits.append("cards placed in PDF Studio")
        self.flash_status("Board imported: " + ", ".join(bits) if bits else "Board imported", hold=True)
        try:
            self._lib_refresh_if_open()
        except Exception:
            pass
        self.goto_tab("PDF Studio" if placed else "Word Lists")
