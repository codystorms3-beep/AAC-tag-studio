# AAC Tag Studio

Free, fully offline desktop app that turns words into pictograms, printable
PECS cards and boards, and 3D-printable AAC tags.

- AAC Tag Studio is a free desktop app that turns words into pictograms, printable PECS cards and boards, and 3D-printable AAC tags. It is built for AAC users, parents, teachers, and speech therapists.
- In Maker Studio, you pick words and the app builds 3D keychain tags with a live preview on your printer bed. Export writes STL and multi-material 3MF files with base, ink, and midtone layers.
- PDF Studio is a print editor for cards and boards, with pages side by side, drag-and-drop objects, and saved projects. It turns your words into finished, printable pages without needing any other software.
- Everything runs fully offline on your own computer, including AI pictures generated locally for words that have no library picture. There are no accounts, no subscriptions, and the built-in picture library comes from ARASAAC and OpenSymbols.

No accounts. No internet needed to run it (once the optional local AI models
are in place). Built for AAC users, parents, teachers, and SLPs.

## What it does

- **Maker Studio** — build 3D-printable keychain tags from words. A live 3D
  preview shows every tag on a printer bed (180–350 mm presets), and export
  writes STL and multi-material 3MF files: base, ink, and midtone layers.
- **Word Lists** — your word lists, categories, and saved lists.
- **Tag Design** — shape, size, style (flat inlay, raised, sunken, outline),
  hole, ring, layout, fonts, and colors for each tag.
- **PDF Studio** — a print editor for cards and boards: two pages side by
  side, an All-pages row view, drag objects across pages, combine pictures,
  save and open projects, and save finished PDFs.
- **AI (Local)** — generate pictures for words that have no library picture,
  entirely on your own computer. A dictionary description comes first, the
  local text model fills in words the dictionary misses, and the picture
  model draws in a flat PCS-style look (thick outlines, flat bright colors,
  plain white background). The style is direction only: no actual PCS
  artwork is included — PCS symbols are Tobii Dynavox property.

## Run it from source

Requires Python 3.10 or newer (developed on Python 3.13) on Windows.

1. Download the repository (or a Release zip) and extract it.
2. Run `Install_Deps.bat` once to install the Python packages.
3. Run `Launch_AAC_Tag_Studio.bat`.

The `wheels/` folder holds the two small packages the 3D preview needs, so
they install offline with no internet.

## Windows exe (coming later)

A one-click Windows exe is planned but not part of this release yet. For
now, run from source with `Launch_AAC_Tag_Studio.bat` (see above).

## Local AI models (separate download, not in this repo)

The models are too big for git, so they are downloaded separately and placed
in `local_ai/` (see `local_ai/README.txt` for where each file goes):

- **Text:** Qwen2.5-3B-Instruct (GGUF, about 2.1 GB, Apache 2.0).
- **Pictures:** Stable Diffusion Turbo (about 5 GB, Stability AI Community
  License — see `NOTICE.md` and `LICENSE-STABILITY-AI-COMMUNITY.md`).

Without the models, everything except AI picture/text generation still works:
the built-in picture library covers most everyday words.

## Picture credits

- Built-in picture library: ARASAAC and OpenSymbols pictures (credits also
  appear in the app's PDF footers).
- AI pictures: original images generated on your machine in a PCS-like
  style. They are yours to use.

## License

The app's own code is under the MIT License — see `LICENSE` (copyright
Cody Waidelich). Third-party model and picture terms are in `NOTICE.md`.
## How this started

I built this with AI — a lot of prompts on the free tools — after talking with the therapy department at our school. They needed a way to make 3D tags, and I couldn't find an easy offline way to just make a bunch of them. So I started building one, and it snowballed from there. I'm trying to be committed to making this the best program it can be and any issues you find or anything that you think is wrong because I basically know nothing please let me know this is my first time doing anything like this. I know it's not real coding but I did learn a lot

## Status

Windows is the supported platform today. The app is actively developed;
see `CHANGELOG.md` for what each version changed.
