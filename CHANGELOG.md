## v15.74 — Previews look exactly like 15.67 again (Body/Ink colours, fill kept)

- **Changed (Cody's call):** all four previews (Maker 3D, browser 3D views, live view) colour tags with the Body/Ink colours again, exactly as 15.67 did — they no longer follow the Category colours switch. This undoes the 15.72 preview-colour change: with the switch on, previews showed Fitzgerald category colours, and Cody wants the 15.67 look back. The 15.73 automatic face fill stays, so the middle still cannot look empty. Exports are unchanged and still use category colours when the switch is on. Note: the preview now shows YOUR Body/Ink settings — if those are the lavender/gray from the 15.71 photo rather than the cream/black of the 15.67 screenshot, set Body = cream and Ink = black under Tag Design → Advanced options → "Colours (when category colours are off)" to match that screenshot exactly.

## v15.73 — Maker preview fills the tag face automatically (it can never look empty inside)

- **Fixed:** the Maker 3D preview now fills each tag's face automatically. The preview builds a fill layer from the tag's own top-face triangles, in the tag's base colour, lifted a hair above the face so it wins the depth test, and in both windings so backface culling can never hide it. When the mesh face already draws, the fill is the same colour in the same place and changes nothing visible; when the face fails to draw on a given machine, the fill is the face — so the middle of a tag can no longer look hollow or see-through in the preview. Preview only; exports are untouched.

## v15.72 — Previews now show category colours (they were on but invisible)

- **Fixed:** the Category colours switch was on by default and the exported files used it, but every preview (Maker 3D view, browser 3D views, live view) coloured the base with the Body colour anyway, so the switch looked dead and the preview showed the Body colour instead of the Fitzgerald category colours. All four preview paths now use the same colour logic as the export: high contrast wins, else the category colour for the base when the switch is on, else Body/Ink colours. With the switch on (the default), the preview now shows what the export produces.

## v15.71 — Export base looks solid again (keeps the fragment fix)

- **Fixed:** 15.68 dropped both interface caps to kill the slicer fragments, but that left the base an open shell (1,102 open edges on the test tag), which a slicer preview shows as hollow/empty in the middle. 15.71 keeps the bottom slab's top cap — it covers the seam, so the base looks solid again — and drops only the top slab's bottom cap, so no coincident-cap pair remains to fragment. Same printed volume, zero single-triangle fragments, open edges down to the top slab's bottom rim only. The app preview itself was identical on 15.67 and 15.68 (verified with screenshots); this fix is for how the exported mesh presents in a slicer.

## v15.70 — Generate picture no longer acts on a word you never picked

- **Fixed:** entering Maker silently filled the current word with the first word of the preview set (so "afraid" kept loading up), and rail Generate picture trusted that value — so with nothing selected it started generating a picture for that first word. Generate picture now acts only on a word you actually picked: a highlighted Maker tag first, else a word you picked via a tile, put, create, replace, apply, picture, or library send. With nothing picked it says "Pick a word first" instead of generating. No other action changes: the preview still auto-fills its own target for View in 3D and export, which already fall back to the first word themselves.

## v15.69 — AI tab: readable text + Generate down to two actions

- **The faint text is fixed:** the right rail's buttons used a transparent background with the theme's default white text, which is invisible on the pale rail — every rail button now has an explicit dark-on-light (and light-on-dark) text color. The same fix covers the banner clock (was white on light gray) and the gray helper labels across the AI tab, which are now a darker gray.
- **Generate is now two actions:** "✨ Generate picture" (picture for the current word) and "📝 Create wordlist" (suggest words for a situation). The old "Both" button is gone — ticking the composer's "AI-draw pictures for them" box makes "Create wordlist" also draw pictures for the new words, which is what Both did. "Explain log" moved up with Settings and Help; nothing else on the rail changed.

## v15.68 — Maker exports no longer shatter into fragments in the slicer

- **Root cause found and fixed:** Flat inlay tags are built from two stacked slabs (bottom with the design cut out, solid top), and the two caps where they meet at the 0.6 mm inlay boundary were triangulated differently. When a slicer loaded the file, those coincident caps shattered into dozens of single-triangle pieces per tag (all 70 stray pieces in the test plate sat exactly at z = 0.6) — that is the pile of arcs, shards, and thin bars Creality Print showed around the real tags. The export now drops both interface caps, so the slabs join along their shared outline: same printed volume, no junk pieces. The ink layers were already clean and are unchanged.
- **Measured on the 8-tag test plate:** base file went from 638 separate pieces (588 of them under 1 mm³, 560 of them single triangles) to 70 real pieces with zero single-triangle fragments; total volume unchanged. The "not watertight" log warning is silenced for Flat inlay / Outline, since the open slab seam is intentional now, not a defect.

## v15.67 — EXE no longer dies silently + console debug build

- **A failed EXE launch now leaves evidence instead of nothing:** the frozen build is windowed (no console), so any startup crash used to exit without a word — the traceback went to print/logging, which nobody sees. A fatal startup error now writes STARTUP_ERROR.txt next to the program (and in the data folder) and shows a dialog with the log locations. If the rebuilt EXE still fails to open, that file (or a screenshot of the dialog) says exactly why, instead of another silent nothing.
- **New Build_EXE_Debug.bat:** builds AACTagStudio_Debug.exe with a console window, so running it from a Command Prompt shows the startup log live. Use it only for diagnosing a launch failure; the normal windowed build is still the one to distribute.
- **Possible culprits this will unmask (not yet proven):** a missing DLL for a top-level import in the frozen build (cv2 is imported at the top of the core module), a missing bundled module, or antivirus quarantining the one-file extraction. The visible error will say which one — no more guessing.

## v15.66 — Maker selection cleanup + single-tag zoom

- **Selected tag is clean now:** the purple circle around the highlighted tag stays — that's the ground ring, and it's the good part. What's gone is the purple scribble across the tag's face. Cause: selection used to re-draw the whole selected tag's mesh inflated 6% in violet, on the theory that only a rim would show. From the tilted view that theory fails — inflating sideways changes each fragment's depth, so the raised picture and word painted violet halos over the tag face (the "stringing"). Selection is now just the ground ring, and the tag face stays exactly as designed.
- **Single tag no longer fills the whole view:** viewing one tag used to frame the camera on that tag's own tiny bounds, so it blew up to fill the screen. The camera now never frames tighter than a 120 mm scene, so a single tag sits at about a third of the view with room around it, like the multi-tag view. Zooming in close by hand still works — the wheel sets the distance directly and isn't clamped.

## v15.65 — All pages row view

- **All pages in one row:** PDF Studio's new "All pages" button (on the Pages ribbon tab and the bottom bar, next to "Two pages") shows every page in one row, left to right in order, at the same size pages one and two have today — so a third, fourth or tenth page just shows up alongside them instead of hiding behind the pair. The two views are exclusive: turning All pages on turns Two pages off, and back again.
- **Working in the row:** the focused page keeps its purple border; click any page to work on it (objects, snips, shapes and text all land on the page you clicked); drag an object across pages in one gesture and drop it on another page; ◀ ▶ walks the pages in order and the row slides the newly walked-to page into view; the page strip still jumps to any page. Slide the row sideways with a right-drag on empty space, the same pan as everywhere else. Zooming still anchors on the focused page.
- **One behaviour to know:** if you click a page that is only half in view, the row slides it fully into view when you let go of the mouse — never while you are pressing, so a click or a snip drag always lands exactly where you pressed.

## v15.64 — Snip over pictures fixed, Animals theme, recategorization

- **Snip over pictures fixed (and saved PDFs too):** snipping over a picture on a page used to come back wrong — the picture was rendered small and centred in its rectangle instead of filling it, so the snip captured the wrong piece (your board screenshot: the fragment was an enlarged crop of that shrunken board). Cause: the picture renderer never scaled a picture UP to its rectangle, only down. Pictures now fill their rectangle at any size, in the editor, in snips, and in saved PDFs. This one is older than the stamp fix — the stamp fix didn't cause it.
- **Animals theme + recategorization:** the Library and Maker picture box now have an Animals group (warm brown), and the word table grew from 437 to 688 words — about 150 animals (pets, farm, wild, sea, bugs, baby animals) plus everyday nouns that were sitting in Uncategorized (train, truck, boat, plane, bicycle, arm, leg, hand, foot, tooth, shoe, blanket, pillow, sky, leaf, rock, mountain, lake, ocean, and more). Ten animals that were filed under Nouns (dog, cat, bird, fish, horse, cow, pig, chicken, duck, animal) move to Animals. Plurals work too (dogs, foxes, puppies), and phrases like sea turtle, polar bear, tropical fish and guinea pig land in Animals. Words the app remembered under a wrong old guess (tiger as People, duckling as Actions) are re-sorted on upgrade; categories you set yourself on purpose are kept.
- **New words and pictures auto-categorize:** every genuinely new word already got its best category when it was added — now that best guess covers animals, plurals and phrases, so new words and their pictures land in the right group on their own.

## v15.63 — PDF zoom fix, Combine, Open pictures, Maker swap, save projects

- **PDF Studio zoom fixed:** scrolling to zoom no longer pushes the two pages over each other. The pair is now one row — focused page, a fixed gap, other page — so the pages can never overlap, however far you zoom. And zooming always works on the page you have selected (the purple-bordered one), even if your cursor is sitting over the other page.
- **Combine into one picture:** select two or more stacked objects, right-click, "Combine into one picture". It renders just those objects — like a snip cut around the outside of the largest one — into a single picture you can drag around as one object. The page underneath shows through again once the originals are gone, and it undoes in one step. (Grouping still exists for when you want to keep the pieces separate.)
- **Open takes pictures now:** Open in PDF Studio accepts PNG, JPG, GIF, WebP and BMP as well as PDFs. A picture lands on the current page as an object, fitted inside the page and centred — the same thing "Replace picture…" already did for a single object, but straight from Open.
- **Maker right-click:** "Bring forward" / "Send backward" are replaced by "Switch spots with" — a list of the other tags in preview; click one and the two tags flop spots on the viewer.
- **Save project / Open project:** two new buttons on the PDF Studio bottom bar. Save project keeps the whole session — every page, every object, groups, card pages, the focused page and the zoom — in one file, so you can open it back up later exactly where you left off. Re-saving goes straight to the same file. If a background PDF has moved since you saved, that page keeps your objects on a blank page and tells you which file is missing.
- **Snip on uploaded PDFs fixed (two causes):** snipping on the second page while a first-page object sat underneath that spot used to capture the first page's content instead — shifted from what you dragged. Draw tools (snip, harvest, shapes) now always work on the page you can see. And on very large pages the snip used to crop with the wrong scale, squashing the result — it now crops with the scale the page really rendered at.

## v15.62 — General tidy-up: dead code removed, nothing else changed

- **Removed about 2,000 lines of code the app no longer reaches**, verified by scanning every file for callers (including the test suites) before deleting anything. No feature, button, or behaviour changed — the app does exactly what 15.61 did, with less in the way.
- **The old embedded-browser (WebView2) corpse is finally out:** its module, its loader DLL (~190 KB), the embedded-window machinery, and the embedded preview page are deleted. The in-app preview is the native GL view, and the browser 3D pages are unchanged. What stays, shrunk to a few lines, is only the visibility helper that shows the GL frame on Maker/Tag Design tabs.
- **Also out:** four orphaned old AI-tab handlers (Draw this icon, quality check, prepare-all, better search term — their buttons are gone; the current "Make an AI image" flow is untouched), four old board-export functions, the old Maker word picker and Edit path, old PDF preview/picture-choice helpers, the old Ollama install helpers (the Ollama engine choice itself is untouched), and three unused imports.
- **Tiny safe fixes:** three `f"..."` strings with no placeholders, and four word-category entries that were shadowed by later ones (`love`/`first`/`clean`/`light` keep the category they already had).
- **Deliberately kept, not deleted:** the browser 3D viewers (single, multi, live) — no button opens them right now, but removing them would remove the feature itself, so they stay until you say otherwise; the small framework hooks Tkinter and the web server call on their own; and one PDF helper the test suites use.
- Nothing else changed.

## v15.61 — Reorder tags from the Maker right-click menu

- **Right-click a tag in Maker Studio and you can now move it around the viewer.** **Bring forward** moves the tag one step earlier in the preview order, **Send backward** moves it one step later — the grid shifts around it. The tag itself (word, picture, look) does not change, and the entries grey out at the ends of the order so you can't move past them. This is the Maker equivalent of PDF Studio's Bring forward / Send backward, which already worked that way.
- Dragging a tag on the viewer itself still orbits the camera (left-drag) and pans (right-drag), so the menu is the way to rearrange for now.
- Nothing else changed.

## v15.60 — Replace only picture / only word, save objects to the Library, stamps fixed + more of them

- **Right-click a tag in Maker Studio and you can now replace only the picture, or only the word.** Highlight a tag, right-click it, and the menu offers **Replace only picture…** (picks a picture from your Library; the word on the tag does not change) and **Replace only word…** (type the new word; the tag keeps its picture and its look). Each does exactly that one thing — unlike sending, which replaces the whole tag.
- **Right-click an object or picture in PDF Studio and you can save it to your Library.** Picture objects and cards with pictures now have **Save picture to Library** on their right-click menu. A card saves under its own word; a plain picture asks which word to file it under. It lands in the Maker strip and Library tab, and the document itself is untouched.
- **Stamps are fixed and there are more of them.** “★ Great job!” was only showing about half its words because the stamp's box was measured with a font that doesn't even have the ★ character, so the box came out ~40% too narrow and the picture got cut off at the edge — making it bigger never helped, because the box was always wrong by the same amount. Boxes are now measured with the font that actually draws them, so every stamp shows whole. Stamps already on a page repair themselves the next time you change their size, text, bold, or alignment. And the Stamp menu gains **Super!**, **Excellent!**, **Awesome!**, **Well done!**, **Nice work!**, **You did it!**, and **Keep it up!**.
- Nothing else changed.

## v15.59 — Sending anything to a highlighted tag replaces the whole tag

- **A highlighted tag is now replaced whole — word and picture together.** Sending a picture to a highlighted tag used to keep the old word and just swap the picture, so the tag showed the old word with the new word's picture. Now the tag becomes the sent word with the sent picture. The same rule covers putting a word on the highlighted tag (word tile, or typing a word and tapping **Put on current highlighted tag**): the new word arrives with its own picture — the picture you saved for that word, if it has one — instead of the tag keeping whatever picture it had.
- Sending a picture with no tag highlighted works exactly as before (the picture goes on the current word, the word stays), and typing a word and pressing Enter still adds a new tag rather than replacing the highlighted one — **Put on current highlighted tag** is the button that replaces.
- Nothing else changed.

## v15.58 — The 3D viewer looks the part, and selection is obvious now

- **The viewer looks more like a product shot now.** The flat background is a soft lavender gradient (pale violet up top, fading into the floor colour), the floor carries a faint grid like a slicer's build plate, and the tags themselves catch the light better — a soft sheen on the flat faces and a light rim along the edges, so the round tag reads as glossy instead of flat. The contact shadows under the tags are unchanged.
- **You can finally tell what you've selected.** Clicking a tag in the viewer used to show only a butter-coloured glow underneath it — which the tag itself hid. Now the selected tag gets a thick violet ring on the floor around it *and* a violet outline hugging its silhouette, so the highlight is visible from any angle. Clicking empty space still deselects, and the ring and outline go away with it.
- **A little info chip sits bottom-right inside the viewer**, like the reference: Triangles and Dimensions for the selected tag (or totals across all tags when nothing is selected), plus the tag's name.
- The thumbnail strip from the reference is *not* in this build — each thumbnail needs its own little 3D render, which is a bigger piece than the look changes, so it's first on tomorrow's list with the other things.
- Nothing else changed.

## v15.57 — Uploaded PDFs lay on top of your page, card adds name their page

- **Uploading a PDF no longer adds its pages to your document.** Opening a PDF while you already have pages open used to append the PDF's pages after yours — so it "turned itself into the page". Now the upload does the right-click "Place this page as a picture" step from the start: each uploaded page lands on the page you're working on as a picture object at its natural size, even over the edges, and nothing new appears in the page list. A multi-page upload lays one picture per uploaded page, staggered so each one stays grabbable, and one Undo takes the whole upload back off. The uploaded file itself never joins the document. Opening a PDF into an empty document still opens it as pages, since there's no page to lay it on.
- **Words: every add path was re-tested with page 2 highlighted, and they all land on page 2** — row tap, ＋ button, drag onto the page, typed Add, and the card-grid add. The one real drag bug (drops landing on page 1 when zoomed) was already fixed in 15.56. New in 15.57: adding to a card grid now says which page it went to in the status line ("Added 1 word(s) to the card on page 2"), so if it ever lands on the wrong page on your machine, that message will show it — tell me which button you tapped and I'll chase that exact path.
- Nothing else changed.

## v15.56 — PDF pages land on the page you're on, words add on any page

- **Page as picture no longer makes a new page.** Placing an uploaded PDF page as a picture used to open a fresh blank page sized to match the PDF's orientation — so with two pages open, the PDF showed up as a third page. Now the picture lands right on the page you're working on, at its natural size, even if it runs over the sides of the paper; drag it anywhere and drag its corners to resize, like any picture. The uploaded page itself is untouched.
- **Words from the word list add on any page now.** Dragging a word row onto the second page of the pair used to fail — the drop was measured against the first page only, so it was refused as "not on the page". A drop now lands on whichever page it was dropped on. And typing words and tapping **＋ Add to card/page** on a page that isn't a card page used to park the words in the Stamp box where they never visibly arrived; they now land as cards on that page, staggered at the centre so several at once don't stack. On a card page, adding to the card grid works exactly as before.
- Nothing else changed.

## v15.55 — Two pages, reworked: equal size, focus border, no more page-switch on click

- **Two pages is the default now.** Opening a document with more than one page shows the first two side by side right away, and adding a page makes it show up in the pair on its own — no switch to flip. (The **Two pages** buttons on the bar and in the Pages tab still turn it off if you ever want a single page.)
- **Both pages are the same size now.** The pair shares one scale, so the two pages match exactly, and zooming zooms both together — zoom out to see the pair, zoom into the next page and it takes the highlight.
- **The page you're working on wears a purple border.** The other page keeps its plain edge, so it's always clear which page the tools are working on. Clicking a page — empty space or an object on it — just moves the highlight there; the pair stays put, and editing one page never pushes you into a single page or changes the other one.
- **Drags go both ways, seamlessly.** An object on either page can be dragged straight onto the other one — press it, drag across, drop it there. The moved objects get the purple outline on the page they landed on, and the page you're dragging from stays where it was.
- **◀ ▶ walk the pages in order.** Next steps to the next page and highlights it; when the highlight reaches the right-hand page, the pair slides along so every page gets its turn, one by one, without jumps. Strip thumbnails of a page already in the pair just highlight it; any other thumbnail jumps the pair there.
- Nothing else changed: the strip drag, the single-page tools, and Save all work exactly as before.

## v15.54 — Save PDF fixed, Two pages on the bar, right-click Create new tag

- **Save PDF works on any document now.** The green Save PDF button in the bottom bar was wired to the old word-list export, so on an edited document it popped up "Type at least one word first" and refused to save. It now saves the document you're editing, exactly like the ribbon's Save — no words required, and your original file is still never changed.
- **Two pages is on the bottom bar too.** You asked for the second page beside this one so you can drag between pages — the switch was only in the Pages ribbon tab, easy to miss. There's now a **Two pages** button right on the bottom bar next to Add page (it stays in step with the Pages switch), so the next page shows up beside the current one in one tap and you can drag objects across.
- **Right-click a word for a new tag, in Maker Studio and in the Word Bank.** Right-clicking a Maker word tile now has **Create new tag** in its menu (right after Put on tag) — the word becomes a brand-new tag on the preview page. Right-clicking a word row in the PDF Studio Word Bank has the same entry: it jumps to Maker Studio and puts that word on a brand-new tag there.
- **The page size label keeps up after a rotate.** Rotating a page left the Properties panel showing the old size and orientation (11.0 × 8.5 in · Landscape on a page that was now portrait). The label now follows the page's current rotation.
- About the sideways page in your screenshot: the app shows an uploaded page exactly as the file itself says — a page whose file carries a 90° rotation comes in sideways in every viewer, not just here. The fix is one tap: Pages tab → **Rotate ⟲** (or ⟳ — whichever makes it read upright) turns the page, and it turns the placed picture with it. A sideways picture page straightens the same way. Nothing about the upload itself changed, because straightening it automatically would break files whose rotation is correct.

## v15.53 — Cut/snip on Home, two pages side by side

- **Cut and snip are on the Home tab now too.** ✂ Save picture, ✂ Save words and ✂ Snip sit in Home's second row, so they're one tap away instead of a tab switch. The Images and Tools copies are untouched, and the active-tool highlight follows whichever copy you tapped.
- **Two pages on one screen.** The Pages tab has a new **Two pages** switch: the next page appears beside the current one, live. Drag an object off the left page and drop it on the right one and it moves there (same as dragging to a strip thumbnail, but you can see where it's going); the moved objects get a purple outline on the new page, and the page bar reads "Pages 2–3 of …". Clicking an object on the right page switches to that page and selects it, so you can drag it back. Tools still only work on the left page, and on the last page the switch just shows the one page until there's a next one.
- **An uploaded PDF page can become a picture.** **Page as picture** in the Pages tab (and "Place this page as a picture" on the right-click page menu) puts the page you're looking at onto a fresh blank page the same size, as a picture object — then you move it and drag its corners to resize it like any picture. From any other page it offers a menu of the document's uploaded pages to pick from. The source page itself is untouched, and the picture saves into the PDF like any other image.
- Nothing else changed: same editor, same single-page behavior when the switch is off.

## v15.52 — Multi always on, a simpler Edit box, preview-page words, printer beds, Library-order pictures

- **Multiple tags are just always on now.** The switch is gone, and so is the "How many tags" menu right below it — the preview always shows every word on the preview page. (Your old saved switch setting is ignored; the preview behaves exactly like multi-on always did, including in Tag Design.)
- **A preview-page space, right where the word tiles are.** Under the tiles there's now an "On the preview page" row showing only the words currently on tags, in page order — tap one to highlight its tag. And a **Delete all tags** button that clears the whole page (the words stay in their lists, and "Load into preview" puts a list back).
- **The Edit box is stripped to what you asked for:** the typing box, **Put on current highlighted tag** (the old Put on tag, renamed — the typed word replaces the highlighted tag; with nothing highlighted it joins the page as a new tag), **✨ Suggest**, and a new **Create new tag** (the typed word becomes a brand-new tag). Gone from the Edit box: Edit Current Tag, the three browser-view buttons (the in-app 3D preview is the viewer now), and the Pick-a-word row (tiles and the preview-page chips are how words get picked). The "Editing ..." line stays, since it shows which tag the Put button will change. Enter in the typing box still adds a new tag, as before.
- **Printer bed sizes, in Settings → 3D printing (advanced).** Your bed is 220 × 220 × 220, and there's now 180, 256, 300 and 350 too. The preview arranges the tags inside the chosen bed: if the natural grid is too big it first tries other column counts and a tighter spacing, and any tag that still doesn't fit gets a **red frame** plus a note under the preview page ("6 tag(s) outside the bed — the slicer can split them"). Moving tags around stays a slicer job, as you said — this only shows you the fit. The bed plate itself is drawn under the tags, and the camera frames the whole bed. Exports are completely unchanged.
- **The picture box now sorts exactly like the Library tab.** The Maker picture list (and the Select-from-library popup) used flat A→Z; the Library tab groups by theme (People, Actions, …) with A→Z inside each. The picture box now follows the Library's order, including honouring an explicit A→Z / Z→A Library sort.
- Fixed while testing this build: with multi always on, the first preview call could land during startup before the word lists load, and the preview set locked itself to empty forever (blank preview). An empty list no longer locks the set — it seeds once the words are there. Your explicit empty page (Delete all) still stays empty, as it should.

## v15.51 — Readable preview words, a sidebar that stays put, right-drag pan, right-click on any tag

- **Words read correctly in the preview now (PRIORITY for the eyes, not the files).** The exported STL text is laid out right-to-left, so the preview showed every word backwards ("won" for "now") — and no orbit angle can fix a mirror, only a rotation can. The preview now mirrors each tag about its own center, so words read properly (orbit to the south side and they read straight across); the STL files and the exports are completely untouched, and the tag positions, cells and picking bounds are unchanged.
- **The left bar stays a single size now (PRIORITY).** It resized on every tag click because the tabview is packed into the sidebar, so pack propagation kept following the tabview's requested width — and the long "Editing ..." label widened the whole bar by ~110px on every edit click (502 → 612 in the rig) and back on deselect. The bar is now frozen at its natural width once built (it only ever grows, never shrinks, if a tab needs more room), and the "Editing ..." label wraps instead of driving the width — 502 before, during and after editing here.
- **Tag Design's top bar is empty now.** The Picture button above the preview is gone too (the word dropdown went in 15.50); the bar holds just the settings and Log buttons on the right.
- **Right-drag pans the preview.** Drag with the right button and the tag under the cursor follows it, like the PDF canvas; the view re-aims from memory, no re-export.
- **Right-click options work on any tag you hover, not just the highlighted one.** The old click bounds were computed at refresh time and went stale on every orbit/zoom/pan, so menus (and click-select) only landed on the tag that hadn't moved — usually the highlighted center one. Bounds are now re-projected from the stored tag footprints with the current camera on every click, and the menu opens on right-click release (so a right-drag pans instead of popping a menu). Verified after orbiting: right-clicking "dinner" at its new position opens dinner's menu.
- One note: the separate browser 3D page still shows words mirrored (it reads the raw files) — say the word if you want that flipped too; the in-app preview is what this build fixes.

## v15.50 — Tag Design shows Maker words only (the stray word dropdown is gone)

- **Removed the word dropdown from above the preview in Tag Design.** It listed the full vocabulary, so any outside word could load into the preview and the tag editing — Tag Design adds no words, and only Maker Studio words belong there. The Picture button stays (Tag Design has no picture picker of its own), and the dropdown widget itself still exists for the background list updates — it is just never packed anymore.
- **A preview word that never came from Maker now snaps to the first Maker word** when you enter Tag Design, so a stale pick (like the startup "afraid") can't linger in the editing preview. If the word is already a Maker word, nothing changes; multi-tag mode already showed the Maker set and is untouched.
- Nothing else changed: same slicer-style preview, same Maker Studio behavior.

## v15.49 — The preview looks like a 3D slicer now (no more cones)

- **Fixed the "giant cones aiming at the center": the preview matrix was uploaded transposed.** The code passed a NumPy row-major matrix with GL_FALSE, which made OpenGL read it transposed — the view translation leaked into the perspective divide, so close-up flat tags stretched radially into cones (and even the normal view was subtly sheared). The upload now uses GL_TRUE, so the rendered pixels match the picking math exactly — click-to-select bounds now agree with the tags you see, too.
- **The preview is orthographic, like a slicer's top view.** No perspective divide at all, so a flat tag stays a flat disc and every word stays undistorted no matter how far you zoom in or out. Same orbit (drag), zoom (wheel), and double-click reset as before; the framing and fit distance are unchanged.
- Nothing else changed: same stacked ink from 15.48, same offline wheels install.

## v15.48 — The preview shows the actual tag now (the word was invisible)

- **Fixed the preview showing a blank disc: the word and ring were buried inside the tag.** Every part STL is exported grounded at z=0 (base 0–1.6, ink 0–0.6), and the preview laid them all at z=0 — so the dark ring and letters sat *inside* the base slab, and the real depth buffer hid them completely. The black wedges in 15.47 were just the misaligned ink ring peeking past the disc's edge. The preview now stacks each part on the base top (ink at 1.6–2.2), and centers each word as a whole so the ring and letters sit exactly where the printed tag puts them. What you see now is the real tag: cream disc, dark ring, dark word, keychain tab.
- Nothing else changed: same OpenGL preview, same gestures, same offline wheels install.

## v15.47 — The preview packages install offline, and never block startup

- **If you have no internet, the program now installs the OpenGL preview packages from wheel files in the app folder.** On first run it checks the `wheels` folder inside the app folder first and installs from there with no internet at all; only if no wheels are found does it try the internet download. The wheels zip below contains exactly those two files (PyOpenGL 3.1.10 + pyopengltk 0.0.4) — extract it once into the app folder and you're done. The packages still land in the same place as every other pip package, and later runs install nothing.
- **Fixed a real flaw 15.46 exposed: a missing preview package used to stop the whole program from opening.** Because the preview packages were on the required list, a failed install made the program exit before the window ever appeared. They are off that list now — the worst case is the preview cell showing its message while the rest of the program works normally.
- Nothing else changed: same OpenGL preview, same gestures, same Live 3D links.

## v15.46 — The preview is OpenGL now (real GPU 3D, in the program)

- **The Python picture preview is gone, replaced by a real OpenGL canvas in the same preview cell.** It draws the same exported tags with the graphics card: smooth lighting, a ground plane, soft shadows under each tag, and the butter selection glow. Drag to orbit, wheel to zoom, double-click to reset, click a tag to select it — all the same gestures as before, but now the picture is drawn by the GPU instead of painted pixel by pixel, so orbiting stays smooth.
- **The two packages it needs download themselves on first run.** When the program starts it checks whether PyOpenGL and pyopengltk are already there; only if one is missing does it download it, into the same place all the other pip packages live (your Python's site-packages on C:). Nothing is re-downloaded on later runs. If a download fails (offline, for example), the program still opens and the preview cell says plainly that 3D needs OpenGL, instead of going blank — and Install_Deps.bat installs both as well.
- **Nothing else about the preview changed:** same words, same layout, same camera, same click-to-select. The external "Live 3D viewer" link and the "View all tags in 3D" / "View this tag in 3D" links are untouched in this build.
- Note for update-zip installs: the old `aac_3drender.py` file is left behind in your folder, unused — the program no longer reads it. The full zip removes it. Delete it by hand if you like; leaving it does nothing.

## v15.45 — The automatic browser preview is OFF (the app works again)

- **Plain truth first: 15.44's timing theory was wrong, and your new log proved it.** The browser creation was deferred until after the window appeared, and the program still died with the same -1073740791 — with the browser creation started but never finished, while the built-in preview and its exports were all healthy around it. So the crash isn't about WHEN the browser starts. On your machine, creating the in-program browser kills the process — the same pattern as the 15.38 crash, different code.
- **So the automatic browser start is off, not fixed a third time.** The app opens with the built-in 3D preview (which your log shows working fine) and the external "Live 3D viewer" link still works. No more open-then-close. The browser code is still in the program, untouched, for a deliberate diagnostic run later — not another blind timing change.
- **The 'afraid' re-pushing fix from 15.44 stands** (your 15.44 log shows exactly one embed push and no live-push spam). If you want the browser inside the program back, the honest next step is a test build that logs each browser step to a file, so the last line before the crash names the exact fatal call — but that build will crash once when you run it. Your call, no pressure.
- Nothing else changed.

## v15.44 — No more startup crash, no more re-pushing the same tag

- **Fixed the startup crash (exit code -1073740791 / 0xC0000409, a native stack overrun).** Found it by diffing: 15.41 started fine, and the only functional change in 15.42 was creating the browser preview during startup — before the window has ever appeared and before the app's message loop is running. The browser's creation needs a shown window and a running loop, so it now starts on the first window-appearing moment instead (with a 5-second fallback). Nothing else about the embed changed: same live multi-grid page, same built-in fallback if the browser can't start. A native kill like this leaves no traceback and no usable log lines — that itself was the clue pointing at the browser creation, not at your Python files.
- **Got rid of the re-pushing, for real this time.** 15.43 stopped the spinner from redrawing, but any repaint still scheduled a fresh live/embed push — so an unchanged word like 'afraid' at startup kept getting re-exported and re-pushed, and the log kept filling with "pushing 'afraid'" lines. Now a push only happens when the content actually changed (word, design, picture, or selection). Edits, picture changes, and tile selection still push immediately; an unchanged tag pushes once and then stays quiet. The 15.43 spinner timeout is unchanged.

## v15.43 — Stops the nonstop exporting (the 'afraid' loop)

- **Found it in your log:** the loading spinner for 'afraid' never finished, and every 70ms heartbeat redrew — which, since the Maker canvas went headless, painted nothing but scheduled a fresh export each time. That's the tons of activity: a native refresh every ~5s, and with the live page open a live push every ~0.65s, for minutes, plus the stuck "Loading 'afraid'..." label. The spinner no longer draws, and a build stuck over 60s is cleared (if it does finish late, the tag still appears).
- This also covers the old session's live-push loop — same spinner, same fuel.
- Note: this log was from 15.41, before the 15.42 cold-start fix, so the missing embedded browser here is already explained — 15.42 starts the embed at launch. This build includes that fix too.

## v15.42 — The embedded preview now starts on a cold start

- **Found why the preview showed the Python renderer instead of the browser page:** the embed only started when you SWITCHED to Maker — but opening the app already sitting on Maker never fires the tab callback, so the embed never started at all (no error, no message, just the fallback). It now kicks off once at startup when opening on Maker/Tag Design, and the host frame is gridded before the browser is parented to it so its window handle is real.
- Nothing else changed: same live multi-grid page, same built-in fallback if the browser can't start. If the browser still doesn't appear, watch for the "Embedded browser isn't available" note at the bottom — that tells us it tried and failed, which is a different problem with a different fix.

## v15.41 — The embedded preview shows ALL the tags, live

- **The in-program browser preview is now the multi-tag grid, not one tag.** 15.40's embedded page showed only the selected tag; the preview you mocked up with the browser window showed all nine. The embedded page now serves the whole preview set on its own loopback server (separate from the single-tag "Live 3D viewer" link, so the two never fight over one state) and rebuilds the grid in place as you edit, add, remove, or re-select tags — camera and orbit untouched after the first load.
- The built-in 3D view from 15.39 is still the fallback if the browser can't start, and still shows while the browser is starting.
- **Honest limit, unchanged:** the real WebView2 part still can't be tested on my machine — the page, the server, and the multi-push were all proven here, but the browser window inside the program itself is proven only on Windows. If it misbehaves, the timing (open? tab switch? resize?) + exit code is still the diagnosis.

## v15.40 — The preview is the browser live viewer, inside the program

- **The Maker/Tag Design preview now hosts a real browser window showing the live 3D page** — the same page the "Live 3D viewer" link opens, updating live as you edit, with the same orbit/zoom. This is the embed done differently from 15.28–15.33: one controller for the app's lifetime, created on the main thread only, parented to a permanent preview frame that is never destroyed (the old popup window could die before the delayed handshake finished, and the old design used the controller across threads — the most likely cause of the native crashes). If the browser can't start, the built-in 3D view from 15.39 shows instead — the program never has a blank preview.
- **When the browser preview is showing, the built-in render skips its export** (the live push already feeds the page), so edits don't pay for two exports.
- **Honest limit: the browser part can't be tested on my machine** (no Windows/WebView2 here), so the in-app browser itself is unproven until it runs on Windows. What was proven here: the whole lifecycle with a stand-in browser (startup, live-page address, resize, tab switches, graceful failure falling back to the built-in view), plus the full test suites. If the embedded window crashes on open, on tab switch, or on resize, tell me exactly when and the exit code — that timing is the diagnosis.

## v15.39 — Maker preview is 3D now (the canvas goes headless)

- **The Maker/Tag Design preview is a native 3D view, not the canvas.** It renders the same exported tag geometry with a built-in renderer (pure Python + PIL — no WebView2, no browser, no new dependency): drag to orbit, scroll to zoom, double-click to reset the view. It renders only when something changes (a finished edit, a click, a drag, a resize) — never in a loop.
- **The old canvas is headless on Maker tabs.** It still exists for PDF Studio, but on Maker/Tag Design it is covered by the 3D view and does no paint work at all — no redraw-everything cost anymore. Word Lists keeps its 2D tag view, and PDF Studio is untouched.
- **Clicking a tag in 3D selects it**, just like clicking a tile did: the tag glows butter-yellow and the edit card follows it. Right-click a tag for Remove-from-preview / Generate, right-click empty space for the Generate menu. Design controls, the word entry, and the browser 3D views all work exactly as before.
- **Why native, not the browser page:** the WebView2 embed killed the program (0xC0000005, removed in 15.38), so the in-app view had to be built from scratch. It shows the same tags, the same colors, the same raised picture and word — rendered by the app itself.

## v15.38 — Experimental embedded window REMOVED (it could kill the whole program)

- **The "Embedded 3D window (experimental)" link is gone, and the embedded path behind it can no longer run.** It died on Windows with exit code -1073741819, which is 0xC0000005 — a native access violation inside the WebView2 embedding. A native kill like that has no Python traceback and no log lines, which is exactly why every crash looked empty. No Python-side guard can make native code safe once it touches a bad window handle, so this is a removal, not another fix attempt.
- **The delayed popup is explained, not just removed.** The embed's setup is asynchronous: the click starts a handshake that can stall, and switching tabs let the stalled handshake proceed — straight into the access violation. With the button and the path gone, there is no stalled handshake left to fire later.
- **Nothing else 3D is lost.** "View this tag in 3D", "View all tags in 3D", and the live viewer all run in the browser and are untouched. This build also contains everything from 15.37 (live scheduling rebuild, stable local address, dead-server notice, push-start logging) — if you haven't installed 15.37 yet, skip it and install this one.

## v15.37 — Live viewer scheduling rebuilt (no more starvation, no orphaned tabs)

- **Fixed: the live push can no longer be postponed forever.** 15.35/15.36 cancelled and re-scheduled the push on every repaint, so a stream of repaints could keep pushing it out indefinitely. Now the first call schedules one push 400ms out and later calls only update the wanted word — the push always fires, and a push already running is never doubled up (its completion runs the next one if anything changed meanwhile).
- **The live page now lives on a stable local address** (same port every run, with an ephemeral fallback if a second copy of the app is running). An old tab from a previous run now reconnects to the new run's server instead of polling a dead address forever — and if the server really is gone, the page says so after a few seconds instead of freezing silently.
- **The log now proves which stage fails.** Every push logs "Live 3D: pushing …" when it starts, plus the existing pushed/failed lines, so the next log shows whether pushes start, fail, or complete.
- **No canvas changes in this build, as asked.** The canvas viewer is being replaced, not fixed — this build only touches the live viewer's scheduling, address, and logging.

## v15.36 — Live 3D viewer now follows the multi-tag grid

- **Fixed: the live viewer froze on the first tag.** 15.35 only hooked the single-tag preview path, but the multi-tag grid — the everyday setup — never runs that path: multi edits and tile clicks go through the multi build/repaint path instead. So the page opened with the right tag and then never moved, exactly as reported.
- **The live viewer now follows multi mode too.** Every multi repaint and every finished multi build reschedules a live push (debounced, so a nine-tag rebuild pushes once at the end, not nine times). Clicking a different tile moves the live view to that tag; editing a tag's shape, size, or colors pushes the new version.
- **A stale selection can no longer pin the live view.** If the selected word leaves the preview set, the live view falls back to the current word instead of showing a tag that no longer exists.
- **The live page now shows its state version** next to the update time ("Updated 14:32:10 · v3"), so a future "it doesn't update" report can say whether the page is stuck on an old version or the tag itself isn't changing.

## v15.35 — Live 3D viewer in the browser (the embed is parked)

- **New: "Live 3D viewer" link in Maker Studio's Edit card.** It opens the current tag in your browser, and the page updates itself as you edit — change the word, shape, size, or colors and the new tag swaps in within about a second, without reloading the page or moving your camera. That is the live viewer: edit, and it shows up directly.
- **Why the browser, not inside the app:** the embedded window has now crashed the whole program three times (15.28, 15.29, 15.33) with nothing in the log to learn from, so that path is parked instead of guessed at again. The browser path is the one proven on Windows — single view and multi view both work — so the live viewer runs on it. The app serves the page from your own machine only (127.0.0.1, a temporary port, nothing leaves this PC), and the server stops when the app closes.
- **Also in this build: log lines are now forced to disk** after each 3D milestone (gate, flow, export done), so a native crash cannot take the newest lines with it. If the experimental link ever acts up again, send the newest file in the app folder's logs/ folder (aac_tag.log) — older log files will not show the new run.

## v15.34 — Rail Picture/Both buttons now use the Picture style menu (AAC pastel)

- **Fixed (approved 2026-10-25): the AI tab rail ✨ Picture and ✨📝 Both buttons no longer force the legacy "Line drawing" style.** They used to overwrite the Picture style menu with the first simple-sounding legacy style before generating — which bypassed the AAC pastel builder and its subject dictionary, so mom came out as the old text-model invention ("child pointing at woman in dress walking") instead of "a kind mother smiling". Worse, the overwrite stuck: the menu stayed on Line drawing until changed back by hand.
- **The menu is now the single control for every button.** Rail Picture, Both, right-click → Generate images, and chat picture requests all use whatever the menu says. Default is AAC pastel.
- **One check after installing:** if a previous rail click switched your menu to Line drawing, switch it back to AAC pastel once — the saved menu value is respected, not reset, and from now on no button will switch it behind your back. Help text updated to match (the old "simple icon" wording is gone).

## v15.33 — Experimental embed can no longer fail silently

- **Every experimental-link click now produces visible feedback.** If the embed cannot run, the status line says so and the browser page opens instead; if the window opens, the status line says that too. A click that did nothing gave us nothing to diagnose — that outcome is gone.
- **A hidden window is no longer "reused".** If a previous embedded window exists but is not viewable (minimized, withdrawn, never mapped), it is destroyed and a fresh one is created instead of lifting an invisible window.
- **A failed window creation falls back instead of raising.** The browser page opens with a note, never an error dialog.
- **The embed gate decision is now logged** (Windows? runtime available?) so the next log answers why the embed did or did not run.
- No change to the live-viewer plan itself: the Maker preview becoming a live 3D view (edits re-export and swap meshes in place, debounced, 2D canvas kept behind a toggle until it proves out) is the stage-3 direction and needs this embed path reliable first.

## v15.32 — Multi-tag 3D view (the pre-conversion test) + stale loading label fixed

- **New: "View all tags in 3D" in Maker Studio's Edit card.** The whole locked preview set on one table in the browser — one cell per tag, floating word label above each, orbit/zoom/pan, Reset view. One tag proving out does not prove nine, so this is the test before the canvas is replaced.
- **Correction: the picture and word DO show in the viewer.** They survive as raised geometry (the ink shapes and the word letters, extruded) — the old page note claiming they "still live in the Maker preview" was wrong, and it is fixed on both viewer pages. No face decal is needed before conversion.
- **Browser page only for the multi view** — no embed path. The embed is still experimental; this test is about the 3D content and layout, not the embed.
- **Fixed: the stale `Loading "afraid"…` label at startup.** The restored single word flashed that line and the multi grid never cleared it. Multi-tag completion now sets the label to the tag count instead.
- **Same no-auto-draw rule as v15.31:** the multi export uses local pictures only, so a picture-less word cannot stall the page.

## v15.31 — AI auto-draw knocked off the automatic paths (startup, preview, 3D view)

- **Root cause of the 3D "crashes": a picture-less word silently fired a full local-AI draw.** 'afraid' has no picture, so every build of it loaded the text model (~100s, proven in the user's own log) plus an image attempt. That ran on the Maker preview, on exports, and on both 3D links — so the app froze into looking crashed, diagnostics passed (they never load the models), and the status sat on "loading 'afraid'".
- **The 3D viewer never auto-draws now** (both links): local pictures only, otherwise the tag shows word-only. A viewer click is back to a fraction of a second.
- **Maker previews never auto-draw now either** — single and multi-tag. Previews rebuild on every word/size/style change and at startup (Multiple tags on), which was the "tons of AI loading on startup". Local pictures still resolve automatically.
- **Fresh installs no longer default the auto-draw switch on** (was True). Existing saved settings are untouched: the switch "AI draws an icon if none is found" in AI Settings still controls deliberate exports — turn it off there if you want export auto-draw gone too.
- **Explicit AI drawing is untouched:** AI tab buttons, chat picture requests, and "Draw this icon" still draw on purpose.

## v15.30 — The browser 3D page is the default again; the embedded window is opt-in only

- **"View this tag in 3D" opens the browser page again.** That page worked well in v15.27 and is the safe default. The embedded window crashed on v15.28 and again on v15.29 with no new log to diagnose from, so it no longer sits on the main path.
- **The embedded window is still there, as "Embedded 3D window (experimental)"** underneath the main link in Maker Studio's Edit card. Use it only when you want to retry the embed — if it fails again, the browser link is right above it and unaffected.
- **Fixed: a closed embedded window no longer leaves "loading" stuck on the main window.** Closing the 3D window now resets the main status bar to Ready, so the "3D view is loading for …" line cannot linger after the window is gone.
- **Please check one thing on this build:** the title bar should say 15.30. If it still says 15.28, the update did not land (wrong folder, partial extract) and everything since is the old build — re-extract over the folder you actually run from.

## v15.29 — Fix: the embedded 3D window no longer hangs or crashes on Windows

- **Fixed: clicking "View this tag in 3D" could leave the window stuck on loading, then crash.** Your log showed the export finishing fine (0.2s, 3 files) and then silence — no Python error at all, which points at the browser-engine handshake, not the tag build. The engine's callbacks could reach tkinter from a thread other than the UI thread, and touching tkinter from the wrong thread can kill the process without a traceback. The engine's callbacks now only drop a plain message on a thread-safe queue; the app drains that queue on the main thread, and the controller is only ever used there. A same-thread check refuses a controller that arrives on a background thread instead of risking it. Silence still cannot strand the window: the 15-second watchdog now lives in the same main-thread pump and falls back to the browser page.
- **The next log will pinpoint any remaining failure.** The 3D flow now logs its milestones (embedded vs browser flow, controller ready, export done, navigate, fallback and why), so if anything still goes wrong on Windows, the log shows exactly where it stopped.
- **Nothing else changed.** Same embedded window, same browser fallback, same page.

## v15.28 — 3D view, part two: embedded in the app, plus calmer controls and a table to rest on

- **The 3D view now opens inside the app.** "View this tag in 3D" in Maker Studio opens an app-owned 3D window (Microsoft Edge WebView2, the system browser engine — no new install, no browser chrome, no separate browser app) instead of a system-browser tab. It opens at once with a "Building your tag…" note and shows the tag when the export lands; clicking again while it is open reuses the same window for the new tag, and "Open in browser" in its top bar still sends the current page to your default browser. If embedding is unavailable on a machine (no Windows, no WebView2 runtime, or any embed failure), it falls back to the browser page automatically with a note — you always get the view.
- **Controls are no longer floaty.** The orbit controls' glide (damping) is off, so the tag stops when your mouse stops instead of coasting on.
- **The tag now rests on something.** The viewer page has a visible light table under the tag (with its shadow on it) instead of a shadow floating in empty space.
- **About the slow load you reported:** the export itself measures about 1.7 seconds here for a dinner tag, so most of the wait you felt was almost certainly the full browser launching cold — the embedded window removes that from the path, and the loading note makes the remaining wait visible. If it is still slow on your machine after this, tell me and the next step is profiling the mesh building itself.
- **One honest caveat:** the embedded window is Windows-only code I cannot run on my Linux test rig, so the embed path itself is tested by you on Windows — the browser fallback path is tested here, and any embed failure falls back to it rather than crashing. If the viewer does not fill its window exactly on your display scaling, or focus behaves oddly, report it — those are the known first-run things to check.

## v15.27 — 3D view, part one: a link in Maker Studio opens the tag in your browser

- **New: "View this tag in 3D" link in Maker Studio's Edit card.** It opens the current tag (the selected tag, else the preview word, else the first preview word) as a standalone Three.js page in your default browser — drag to rotate, scroll to zoom, right-drag to pan, with shadows and a Reset view button. This is part one only: nothing is embedded in the app window yet. Try it and report back before any embedding work.
- **What it shows:** the tag's real print shape — base plus raised ink parts, in your tag's body and ink colours — built by the same export path as a real STL export, with the same per-tag options as the Maker preview. One honest limit for this first test: it shows the geometry only, not the picture and word on the tag face (an STL carries no picture); the page says so. The face still lives in the Maker preview.
- **How it works:** the page is one self-contained file (Three.js r147 inlined, STL parts embedded) written to your system temp folder as aac_tag_3d_viewer.html and overwritten on every click, so refreshing the browser tab shows the newest tag. No internet, no server, no new install — Three.js is bundled in the app's viewer3d/ folder.

## v15.26 — A picture request in the AI chat can no longer rearrange your settings

- **Fixed: a descriptive picture prompt typed into the AI chat changed settings and moved the preview around.** The chat assistant is designed to turn plain English into settings and words, so a long image description ("soft pastel ... move the subject to the center ...") was parsed as settings instructions, and the returned options were applied to your tag design — changing shapes, sizes, and colours and redrawing the preview around them. A picture request now never reaches that assistant at all: if your message asks for a picture ("draw a picture of a dog", "picture of dinner", "an illustration of a happy child jumping"), the chat draws just that picture — no settings changed, no words added, no tab switched. The picture uses your current Picture style (AAC pastel if you picked it). If you ask for a picture without naming a subject ("make a picture"), it asks what the picture should be of instead of guessing.
- **Ordinary chat commands are untouched.** Asking for words ("6 bathroom words, rounded squares, high contrast") or settings ("change the shape to round") still goes through the assistant exactly as before — only messages that ask for a picture take the new picture-only path.

## v15.25 — Style prompts distilled to fit SD Turbo's text window

- **The prompts now fit the model instead of overflowing it.** SD Turbo's text encoder only reads about the first 77 tokens of a prompt, and the 15.24 style, composition, and negative together ran well past twice that — so only their openings ever reached the model. Everything is distilled to fit, keeping the highest-impact cues: the positive is now the subject, then "Soft pastel 3D cartoon AAC educational symbol, friendly, child-appropriate, instantly recognizable, rounded simplified forms, gentle soft shading, thin dark brown outlines, pastel palette of sage, butter yellow, peach, sky blue and lavender", then "Single centered subject, plain warm ivory background, generous empty space, square composition, no text." The negative keeps the bans that matter, front-loaded (photorealistic, scary, harsh shadows, busy background, circular frame, decorative border, button interface, text, letters, words, watermark, cropped subject, blurry, distorted anatomy, inconsistent style, flat monochrome silhouette, and the rest), with near-duplicates folded together — realistic photography into photorealistic, captions into text/letters/words, cut-off objects into cropped subject, and so on. Every one of the 96 dictionary prompts and the negative now estimate at or under 77 tokens (worst dictionary prompt: 71), checked by a token estimator in the module. The subject still comes first, so it always survives even if a very long custom subject pushes the tail over.
- **If you saved 15.24 without editing the style, you get the distilled version automatically.** A saved style identical to the 15.24 default was never a personal choice, so loading it upgrades it to the distilled default (the 15.24 originals are kept in the module as a record). Anything you actually customized is left exactly as you wrote it.
- **Nothing else changed.** Same dictionary, same order, same negative-prompt separation, same Advanced editing and restore. The full 15.24 wording is preserved in the module (LEGACY_STYLE_PROMPT_1524 / LEGACY_NEGATIVE_PROMPT_1524) if you ever want it back — paste it into the Advanced boxes and save.

## v15.24 — Automatic AAC illustration style prompt builder

- **One style, everywhere, built automatically.** When an SD Turbo picture is made with the new "AAC pastel" picture style (now the default for new pictures, and the first choice in the Picture style menu), the app builds the prompt for you in a fixed order: a subject description, then the permanent soft-pastel 3D cartoon style, then the composition and background instructions — with the negative prompt sent separately to the model. The picture itself is only the illustration: no word label, no circular border, no interface, no card layout — the app still adds those around it afterwards, exactly as before.
- **Subject descriptions come from a built-in dictionary first** (96 common AAC words and phrases: dinner, listen, alligator, home, "I made a mistake", food, actions, animals, emotions, objects, places, people, and abstract words like now, next, more, and all done). A word the dictionary does not know can use the existing on-device text model for one short description, or stay dictionary-only if you prefer — that choice is in Advanced, and nothing here needs the internet. If the model is unavailable, the word itself is the fallback.
- **Everything lives in one place.** The style prompt, negative prompt, and subject behaviour are defined once in the new aac_style module — no copies scattered through the app — and edited in the AI tab under Settings, in the new "Illustration style prompts" section, with a Restore default style button. The normal AI tab is unchanged and stays simple: enter a word, generate, preview, and the "saved to Library" confirmation only appears once the picture is actually saved (that ordering was checked, not changed — saves already raise on failure, so a failed save can never be reported as saved).
- **Nothing working was replaced.** The SD Turbo model, the inference call, the picture preview, the Library saving, and the older picture styles (Flat clipart, Cartoon sticker, Line drawing, Bold icon, Picture book) are all untouched — picking one of those still builds its prompt exactly as before, with no negative prompt added. Empty words are refused before anything generates ("Type a word first"), instead of silently drawing a placeholder.

## v15.23 — Warm Maker Studio, and tile clicks stop rebuilding every tag

- **Warm Maker Studio, from the preview you picked.** The Maker panel is lavender now, with a peach header band carrying a heart, "Your words. Your way. We're here with you.", and a Theme selector (Soft pastel / Dark — it works like the light/dark setting you already have). The controls sit in three white cards: "Your words" (word list, tiles, Multiple tags, How many tags, with the note that words can be added, edited, or removed anytime), "Edit" ("Make changes with kindness", a new Edit Current Tag button, the editing note, Pick a word, search, Put on tag, Suggest), and "Picture" (heart accent, "Choose pictures that feel right.", Find pictures, Select from library, Upload, the library rows, My library / Put on this tag / Create new tag). Under the cards: "Every tag is a step toward being understood." and "You're building your voice. We're here for every step." Nothing was dropped — every Maker control is still there, only grouped into cards. Edit Current Tag does exactly what clicking a tag does (makes it the tag you are editing); it is a visible button for that, not a new feature. Two honest limits of the widget kit: the background is a peach band over lavender, not a true painted gradient, and the two closing lines sit under the Picture card instead of under the preview, because the preview canvas is redrawn on every change and would wipe them.
- **Tags look warmer too.** The edited tag now glows soft yellow with "You're editing this one." underneath instead of the pink rectangle, tags carry a thicker warm-brown rim, and near-black icon ink leans warm brown. That is preview paint only — exported PNGs and 3D prints are unchanged.
- **Tile clicks are fast now — this was a real bug.** Every click in multi-tag mode threw away the whole tag cache and rebuilt every tag's geometry (picture lookup + 3D shape) from scratch, so adding one word re-paid the cost of all nine. The cache now tells a design change apart from a selection change: adding a word rebuilds only that word, replacing a word rebuilds only the replacement, and just selecting a tag rebuilds nothing it does not have to. The three click paths that forced the full wipe no longer do.
- **Tags stop ballooning.** The grid always picked the layout giving every tag the most room, so with one or two tags they filled the whole preview and jumped size on every click. Grid tags are now capped at 360 px — still large, just not window-filling — and the grid stays centred in its cells.

## v15.22 — Rail labels you can read, editor fills the window, tabs and ribbon centred

- **Tool rail labels fixed.** The labels were small (9 pt) and muted grey on lavender, which is what read as fuzzy. They are now 11 pt bold in dark plum (light mode) / near-white (dark mode), and the rail is a little wider (76 px) so "Rectangle" fits.
- **The editor area expands upward.** In editor mode the ribbon's row was still carrying the old layout's stretch weight, so the window's spare height split into blank bands above the ribbon and above the canvas — the empty space in the 15.21 screenshot. Only the canvas row stretches now: tabs, ribbon, then the page area fills the rest, about 200 px taller on a full-height window.
- **Centred, as asked.** The workspace tabs (Maker Studio / Word Lists / Tag Design / PDF Studio / AI) are centred in their row, and Home's two ribbon rows are centred in the ribbon. Other ribbon tabs keep their left-aligned rows.
- **A real drag bug this work flushed out, also fixed.** With the taller canvas the zoom rose, and a cut-out piece 11 px tall on screen had only ~1 px of grabbable body between its top and bottom resize handles — a click 1 px off centre grabbed the north handle and aspect-scaled the piece instead of moving it. Edge handles now switch off until there is a full handle-width of body left (3x tolerance instead of 2x); thin strips still resize from their corners. Nothing else changed.

## v15.21 — The pastel look actually shows (light theme)

- **The real cause of the 15.20 report:** the preview was drawn in light mode, but the app saves dark mode, so 15.20 stayed dark on a real machine and the pastel hid behind dark grey. This build switches the app to light once, the first time it runs — that is the preview's look: light lavender-grey canvas, white ribbon buttons, pastel panels. If you switch back to dark in Settings, that choice sticks from then on (dark is still supported, it just is not the preview look).
- **Home ribbon is two rows again.** Log and Settings were wrapping onto a third row on real windows; they now sit in row 2 with the markup buttons.
- **Page size menu is white** in light mode instead of the default blue blob, and ribbon buttons are white in light mode (still grey in dark mode).
- Nothing else changed from 15.20 — same tools, same behaviour, same pastel colours.

## v15.20 — Pastel PDF Studio, built fresh on your 15.14 (preview 2, approved)

- **A clean reset, as you directed.** This is built on the 15.14 zip you re-uploaded — nothing else. The 15.16, 15.17, 15.18 and 15.19 builds are withdrawn and none of their files are in here; the interface file differs from your 15.14 only in the colour palette, and the editor file is the 15.14 editor plus the layout and pastel work below, written here. No Grok code, no Grok files. If you installed 15.18, extracting this update over it replaces its interface file with the clean one.
- **Pastel colours throughout the program**, the look from preview 2 that you approved: blush, mint, butter and lilac replace the coral, teal, sun and purple everywhere they appeared — Maker Studio, Word Lists, Tag Design, AI, the workspace tabs, the ribbon, the rail, the Properties panel and the page strip — with the soft lavender-grey canvas backdrop in light mode. The right-click colour swatches are Butter, Blush, Mint, Lilac, White and Soft black.
- **The preview-2 layout, with everything kept:** icon-over-label Home ribbon buttons (symbol on top, name underneath), the labeled tool rail, the Properties panel with its Page section and selected-item colours, the Word Bank with search and ＋ rows, the Pages panel with its header and ＋ / 🗑 buttons, and the bottom bar with Save PDF and the zoom slider. Every tool and behaviour from 15.14 is still there — draw, select, move, resize, rotate, snip, drag between pages, sends as movable objects, undo/redo, right-click menus — only the look changed.
- **Two things the preview showed that the build keeps honest:** the rail stays labeled (clearer than icon-only, and the preview's rail was labeled too), and the icons are symbol glyphs, not colour emoji pictures, because this kind of app can't draw colour emoji reliably on Windows.

## v15.14 — Sends become movable objects, Maker tags paint right, a tidier editor, Log in the ribbon

- **Sending to PDF Studio now places a movable object.** Right-clicking a word in Word Lists, or a word or picture in the Library, and sending it to PDF Studio used to write to the old card list — whose tiles are hidden once the editor opens, so in normal use the send simply vanished (it only surfaced if the document was still pristine when you next opened PDF Studio). Now the word lands as a free card object in the middle of the current editor page, already selected, with its sent picture if one was sent — drag it anywhere at once. It no longer makes a tile, and the Library menu no longer asks current-card / new-card.
- **Maker tags paint correctly again.** The tag preview painted each ink polygon's outline only, so the border ring — whose outline is the whole tag disc — painted as a solid disc in ink colour, hiding the body, the picture and the word and leaving only a category-coloured edge, with the midtone (Detail) colour smeared over the middle. Ink polygons now paint with their holes in the body colour, largest first, so the ring is a ring, the body disc shows, and the picture and word sit on top of it. The same fix applies to the exported tag PNG preview. The 3D export meshes were already correct (they use the geometry, not the paint order) and are unchanged.
- **A tidier PDF Studio.** The top bar above the page was empty in the editor (every control in it was packed away) and took a whole blank row — it is gone, and the page, bar, info and save rows move up one. The Edit tab lost its "Cards & pages" heading row and its own selected-card row; the selected-card note now sits inline with its buttons. Log and Settings, which lived in that top bar, now sit at the end of the ribbon's Home tab, so the log file is reachable from inside PDF Studio too (Log & diagnostics: Refresh, Copy all, Open log folder).
- **About the log you sent.** The two errors in it are a CustomTkinter scrollbar quirk (dragging a scroll bar before its drag state is set) — logged, harmless, and the app kept running and closed normally. Nothing in the log points at the send problem; that was the hidden-tile routing above.

# AAC Tag Studio - Changelog

## v15.13 — Tab bar back, dead sliders out, tile text keeps its picture, drag between pages, permanent cut-out blanks, right-drag pan, tools that toggle off, Home absorbs Comment
- **Tab switching is direct again.** In the full-window editor the sidebar
  (and its tab buttons) is hidden, which left only the ▦ menu. The five
  workspace tabs now have their own bar above the ribbon — one tap to
  switch, the way it used to be. The current tab reads purple and can't be
  re-tapped; the ▦ button is gone.
- **Dead card sliders removed.** Word size, picture size, picture position
  and word position are gone from the Edit tab (and the old sidebar). They
  were left over from the old preview — and a deeper fix underneath: the
  card menus and checkboxes never synced to the page you were on, so they
  showed and applied stale values from the last page touched. They now
  mirror the current page's spec, and a page's saved sizes survive
  menu changes. The remaining card options (layout, paper, show
  pictures/words, category colours) are also on the right-click menu, under
  Card page, when you right-click a card page.
- **Editing a tile's text keeps its picture.** Renaming a card on a card
  page, or editing a free picture card's word, now replaces only the text.
  The picture stays exactly as it was — a card-page tile remembers the
  picture it had (or that it had none), and that memory moves with the tile
  when tiles swap or words leave the page. Picking a new word from the word
  box still gives you that word's own picture.
- **Drag between pages.** The page strip on the right shows every page.
  Drag an object onto another page's thumbnail and it moves there — the
  view follows it, with the object still selected. The thumbnail lights up
  purple while you hover it with the drag.
- **Cut-out blanks are permanent now.** Cutting out no longer leaves a
  white tile object behind: the blank is part of the page itself. It can't
  be selected, moved or outlined, and it bakes into the saved PDF under
  everything else. One undo still takes the cut-out away.
- **Right-click empty space pans.** With nothing selected, right-dragging
  on empty space pans the page around. A plain right-click (no drag) still
  opens the page menu, and right-clicking an object still opens its menu.
- **Tools toggle off.** Tapping the active tool again — Snip, Freehand, or
  any rail or ribbon tool, or its shortcut key — drops back to Select
  instead of staying stuck on.
- **Home absorbs Comment.** The ribbon's Comment tab is gone; Highlight,
  Underline, Strikethrough, Freehand, Text box, Note and the Stamp menu
  all live in Home now, under the one Home label.
## v15.12 — Snip Cut-out, lines that resize and recolour, a roomier panel, right-click colours, tactile snap, steady zoom
- **Snip Cut-out.** The Snip menu has a new option: ✂ Cut out — move it
  around. It cuts the dragged region out of the page — a white patch stays
  where it was — and the cut piece becomes a picture object you can drag
  anywhere. It is a real cut, not a copy: move the piece away and the hole
  stays behind. Works on PDF pages and on pages you made in the app.
- **Lines and arrows finally resize and recolour.** Dragging a resize handle
  used to move the selection box while the line itself stayed put (and then
  the line wasn't clickable where it looked). The handles now stretch the
  line and arrow themselves, and their colour changes through the panel,
  the Shapes tab, or the new right-click Line colour menu.
- **The panel breathes.** The card and page settings that sat below the word
  list now live in the ribbon's Edit tab (Cards & pages: Fill cards, the
  selected card's Move/Delete, the picture-card stamp, Add page, and the
  cards-per-page, paper, size and position options — the last group still
  only shows on card pages). The word list is taller and the objects list
  is taller to use the freed space.
- **Right-click does a lot more, kept tidy.** Objects now get Fill and Line
  colour submenus with quick swatches, a custom colour, and No fill where
  it fits; text gets a Text colour submenu, cards a Card colour submenu
  (including Category colour), pictures get Replace picture. Arrange is now
  its own submenu (forward, backward, front, back), plus Copy and Cut
  alongside Duplicate and Delete, and a Snap on/off toggle on the
  empty-space menu.
- **Snap feels like snapping.** The guides are thicker and carry a label
  saying what caught ("Snapped · Page centre", "Snapped · Object edge"),
  so it is obvious the moment a snap engages. The Snap button itself turns
  purple while snapping is on.
- **Wheel zoom keeps the page.** Two fixes: the zoom now anchors on the
  cursor clamped onto the page (zooming with the cursor over the rail or
  panel no longer pushes the page away), and the pan math now accounts for
  the rail and panel widths — before, every zoom step shifted the page
  sideways, so a flick of the wheel marched it off-screen.

## v15.11 — Free placing at once, Open adds pages, Snip, and a full-window editor
- **Free placing is immediate now.** Drag any word tile from the word box
  straight onto the page and it lands there as a free card at once — no
  click-to-arm step, no detour through the card grid. Clicking a tile still
  works the old way (adds to a card page, or arms click-to-place).
- **Opening a PDF adds pages instead of replacing your document.** Open PDF
  now puts the new PDF's pages right after the current page and jumps to the
  first new one, so a PDF you open to snip from sits alongside your own
  pages. A fresh, empty document is still replaced outright, and Close doc
  still gives you a clean slate first when a real replacement is what you want.
- **✂ Snip tool** (rail and ribbon, shortcut S). Drag around anything on any
  page and a menu offers Save picture to Library, Save words to Word Lists,
  or both at once. On pages from an opened PDF it reads the PDF itself; on
  pages you made in the app it takes the words from the objects and card
  tiles in the rectangle, and renders the picture from the finished page.
  The older ✂ Save picture / ✂ Save words tools are still there too.
- **The editor fills the whole window.** The sidebar column is hidden while
  the editor is open — no more blank bar where the old sidebar was — and the
  page, rail and panel stretch across. Switching tabs from the editor goes
  through the new ▦ button at the bottom of the rail; leaving the editor
  brings the sidebar back exactly as it was.

## v15.10 — PDF Studio fixes: right-click menus, free tiles, no more old sidebar, tooltips
- **Right-click menus.** Right-click an object (text, shape, picture, card) for
  Edit text, Duplicate, Delete, Rotate 90°, forward/backward, Group/Ungroup,
  Lock/Unlock and Hide. Right-click a card on a card page for Rename, Delete
  word and Move word to page. Right-click empty space for Paste, Select all,
  Add blank page (Letter/A4), Add card page, Fit page and Zoom to selection.
- **Tiles place freely.** Drag a card off the card grid and it becomes a free
  picture card you can put anywhere on the page. Dragging it onto another card
  still swaps the two words, as before.
- **Adding pages is obvious now.** A purple **＋ Add page** button sits next
  to the page controls at the bottom of the editor, and in the Cards & pages
  panel — blank pages (Letter, Legal, A4), a new card page, or pages from a PDF.
- **Clicking empty space deselects.** Drawing tools are now one-shot: draw one
  shape and the Select tool comes back, so clicking empty space afterwards
  clears the selection instead of starting another shape. Tiny mouse wobbles
  no longer count as a selection box, and Escape clears the selection too.
- **The old sidebar is gone from the editor.** Everything it held — the word
  entry, the word box, card-page options (cards per page, paper, sizes,
  positions, picture/word toggles), the selected-card controls and the picture
  card stamp — now lives in the **Cards & pages** section of the Properties
  panel on the right. The tab switcher on the far left is untouched.
- **Hover tips everywhere.** Hold the mouse over any rail, ribbon, panel or
  page-control button and a short tip says what it does.
- **Fill colour works for new shapes.** The shape fill colour (and No fill)
  now applies to shapes you draw, not just to shapes already selected — and
  while a drawing tool is armed, the shape controls show in the Properties
  panel so the colour is right there before you draw.

## v15.9 — PDF Studio becomes a real canvas editor (Inkscape-inspired) + speed pass
- **Edit right on the page.** The Text tool now lets you click the page and type
  directly on it — no pop-up boxes. Click existing text (or double-click it with
  the Select tool) to edit it in place; picture-card words and card-page words
  edit the same way. Click away or press Ctrl+Enter to finish, Esc to cancel.
- **A proper tool rail** on the left of the page: Select, Text, Rectangle,
  Ellipse, Line, Arrow, Freehand, Insert picture, and a Hand tool for panning
  (holding Space pans with any tool). Letter shortcuts too: V, T, R, E, L, A, F, H.
- **Real object selection.** Shift-click selects several objects; dragging on
  empty space rubber-band selects; grouped objects move together (Ctrl+G /
  Ctrl+Shift+G). Corner and edge handles resize, and the round handle above a
  selection rotates it (snapping to 15° steps; the ⟳ 90° button still exists).
  Arrow keys nudge (Shift = bigger steps). Objects can turn to any angle now,
  and the saved PDF matches what you see.
- **Properties + Objects panel** on the right: position, size and rotation
  fields; text, shape and picture controls that follow your selection; align,
  distribute, duplicate, forward/backward and Zoom-to-selection; and an object
  list for every object on the page — click to select, ▲▼ to restack, Hide
  (won't print), Lock (can't be grabbed), and rename.
- **Snapping guides** (Tools → Snap): dragged objects snap to page edges, the
  page centre, margins and other objects, with purple guide lines while you drag.
- **Speed pass.** Panning and dragging now move the objects directly instead of
  repainting the whole page on every mouse move, mouse-wheel zoom is smoothed,
  page geometry is cached, and the Maker picture library builds in small
  batches instead of freezing the window. Same features, much less lag.
- One behaviour change to know: dragging on empty page space now rubber-band
  selects (Inkscape-style) instead of panning — pan with the Hand tool or by
  holding Space.


## v15.8 — Harvest from PDFs, a permanent Maker library, 3-colour tags
- **PDF harvesting (PDF Studio):** open any PDF, pick **Images → ✂ Save picture**,
  drag around a picture, and it's saved into your Library under a word you choose —
  reusable on cards and tags like any other picture. **Tools → ✂ Save words** does
  the same for text: drag around words and they land in your Word Lists and the
  editor's word box. Your original PDF is never touched.
- **Maker Studio's bottom strip is your permanent picture library now.** It no
  longer reloads from the internet when you change a word — it shows the pictures
  saved on this computer. Click one, then **Put on this tag** or **Create new tag**.
  Find pictures still works when you want new ones, and whatever you pick joins
  the library for good.
- **Tags can use up to 3 colours** (Tag Design → Colours → Tag colours): the
  picture's midtones print in their own **Detail** colour, so an outline no longer
  merges with a dark fill into one blob. Exports carry the third colour too
  (3MF materials + an extra _ink2 STL). "Up to 2" is exactly the old behaviour.


## v15.7 — Selectable cards, natural placement
- **Cards on a card page are clickable now.** Click a card to select it (yellow
  outline): the word box then replaces THAT card's word, Delete removes the
  word, and "Move word to page…" sends it to another page — onto a card page
  it joins the words; onto a blank or PDF page it lands as a movable card.
- **Drag cards to reorder them** on their page — drag a card onto another slot
  to swap the two words.
- **Click-to-place from the word box:** on blank and PDF pages, clicking a word
  in the word box arms it — the next click on the page drops the picture card
  right there (Escape cancels).
- Everything still works with undo, and the saved PDF is unchanged in format.

## v15.6 — PDF Studio IS the editor (one workspace)
- **The separate card view is gone.** PDF Studio now opens straight into the
  editor — ribbon, page, one sidebar — and card pages live inside it as
  first-class pages alongside opened-PDF pages and blank pages.
- **Card pages are real pages now:** make them from the new **Cards** sidebar
  section (type words, use the word box, or Fill cards from Word Lists), flip
  between them in the page strip, stamp pictures/text/shapes on top of them,
  and Save PDF prints everything together.
- **Page & cards options moved into the editor sidebar** (cards per page,
  paper, word/picture size and position, show pictures/words, category
  colours) and apply to the card page you're looking at.
- **Duplicated controls removed:** the old editor sidebar's page buttons,
  page nav and zoom row are gone — the ribbon and the bottom page bar are
  the one place for those now.
- Closing a document (Home → Close doc) starts a fresh set of card pages.
  A card document you haven't edited yet keeps following your word lists,
  so Library / AI "send to PDF Studio" still lands on the first card page.

## v15.5 — PDF Editor: full ribbon editor
- **The PDF Viewer + Editor is now a full editor with a ribbon** above the page
  (Home / Comment / Edit / Images / Shapes / Pages / Tools) and a page +
  zoom bar below it. The sidebar keeps the page list, the picture-card stamp
  tools and the selected-item properties.
- **Comment:** drag to highlight, underline or strike through; freehand drawing;
  text boxes; sticky notes; one-tap stamps (★ Great job!, ✓ Done, Name:/Date:).
- **Edit:** duplicate, copy/paste (Ctrl+C / Ctrl+V), rotate, bring forward /
  send backward, align to the page — plus a real **Redo** (Ctrl+Y) next to Undo.
- **Images:** import PNG/JPG/etc. straight from your computer, replace a
  picture, resize with corner AND edge handles (aspect kept), and a **Crop**
  mode — drag the picture's edges to trim what shows.
- **Shapes:** rectangles, ovals, circles, lines, arrows and polygons with
  fill colour, line colour and line width controls.
- **Text boxes** get real formatting: font family (Helvetica / Times /
  Courier), bold, italic, size and alignment — and text stays as real,
  selectable text in the saved PDF (not flattened into a picture).
- **Pages:** insert blank pages (Letter / Legal / A4 / custom size) alongside
  the existing insert-PDF, delete, duplicate, rotate, reorder and extract.
- **Tools:** live page-size readout, a Measure tool (inches), snap-to-grid
  and a light page grid.
- **Print…** sends the edited document to the printer. As always, your
  original file is never changed — saving writes a new PDF, and objects stay
  editable for the whole session (nothing is flattened until you save).

## v15.4 — PDF Studio: one layout brain (better preview canvas)
- **Preview and print now share ONE layout.** The page geometry (card positions,
  picture size/position, labels, footer) is computed once in a single layout
  function that both the preview canvas and the Save PDF export draw from —
  what you see is what prints, and the whole preview≠print bug class is gone.
- **Fixed: printed pictures sat mirrored vs the preview.** The export read the
  picture-position value from the bottom of the card while the preview and the
  drag handles read it from the top, so printed icons landed low — low enough
  to overlap the word label. Print now matches the preview exactly.
- **Fixed: A4 previews showed Letter proportions.** The preview always drew a
  US Letter page shape even when A4 was selected; it now uses the real paper size.
- **Snappier selection:** selecting/deselecting cards now repaints only a thin
  overlay layer (outlines + handles) instead of redrawing every card.
- Picture drag handles now ride on the picture's actual drawn rectangle, and a
  picture that finishes downloading slots into its exact layout position
  (before, it briefly used the global size/position instead of the card's own).


## v15.3.1 — PDF Studio fixes (checking pass over v15.3)
- **Print now matches the preview:** dragging a card's picture-size / position handles in
  PDF Studio only changed the preview — Save PDF still used the global sliders, so the
  printed cards came out different. The export now uses each card's own size/position.
- **Backspace no longer eats cards:** with a card selected, pressing Backspace (or Delete)
  while typing in a sidebar text box deleted the selected card(s) off the page. Typing is
  now safe — Delete only removes cards when you're not editing text.
- Version number catches up: the app window now shows the 15.3 line (v15.3 itself never
  bumped it past 15.1.1).

## v15.3 — PDF Studio: multi-select, per-card picture handles, in-place edit
- **Ctrl+click** selects several cards at once; **Delete** removes all selected cards.
- **Per-card picture handles:** select a card and drag the square handle to resize its
  picture, or the round handle to move it up/down — saved per word. The sliders still
  set the default for cards without their own setting.
- **Double-click a card** to rename it right on the page.
- Library right-click can place a picture into a filled card or a blank slot.

## v15.2 — AI rail tidy-up
- Clear **Picture / Word / Both** buttons on the AI rail; "Both" suggests words for a
  situation, then generates simple pictures for them.


## v15.1.1 — PDF Editor verified; AI safety checks restored
- **PDF Viewer + Editor (v15.1.0) checked end-to-end** in the running app: open, page
  navigation + thumbnails, rotate / duplicate / move / delete, stamping picture cards and
  text labels, dragging + corner-resizing items, saving (the saved PDF re-opens with the
  right pages, rotation and stamps), saving over the opened file is refused, and the
  original file is never touched. The card builder still works as before.
- **SD Turbo damaged-model check is back** (it was dropped in the v15.0 rewrite): a
  half-downloaded SD Turbo model is no longer accepted as "installed" — the app spots the
  wrong file size, says so plainly, and Download fetches a fresh copy. A half-extracted SD
  engine (exe present, libraries missing) is likewise detected and reinstalled.
- **Qwen 3B size check restored:** a cut-short 3B model download is flagged as incomplete
  instead of being treated as a working model.
- Removed the Verify_PDF_Editor.py test script from the app folder (developer tool, not
  part of the app).


## v15.1.0 — PDF Studio: PDF Viewer + Editor
- **Open PDF…** (top of PDF Studio) opens ANY PDF in a viewer/editor; **← Back to cards**
  returns to the card builder, which is unchanged and still the default. The mode is
  remembered for the session only.
- **Viewer:** pages are drawn with PyMuPDF at the current zoom (in the background, so big
  PDFs don't freeze the window), with Page X of N, a thumbnail strip of every page, zoom
  in/out/fit, scroll-wheel zoom and drag-to-pan. Password-protected PDFs get a plain message.
- **Page tools:** rotate left/right, delete, duplicate, move up/down, insert another PDF's
  pages, extract this page / all pages to a new PDF, and Undo.
- **Stamp on a page:** a movable **picture card** (same pictures, saved picture choices and
  category colours as the card view), a **text label** and a **highlight box**. Click to
  select, drag to move, drag a corner to resize, Delete to remove. Items are kept in PDF
  points per page, so zooming never moves them.
- **Saving** uses the usual name/folder/overwrite dialog and writes a brand-new PDF through a
  temp file that is checked (it must re-open with the right page count) before it replaces
  anything. Your original file is never changed - saving over it is refused.
- **PyMuPDF is now a required package** (it was optional). New file: aac_pdfedit.py.

## v15.0.2 — Library send: picture-only / word-only; PDF pages grow
- **Picture from Library** → only changes the picture on the highlighted tag/card
  (word is left alone).
- **Word from Library** → only changes the word on the highlighted tag/card
  (picture is left alone).
- **Blank card/tag** is the only case that fills both word and picture together.
- **PDF Studio** raises Number of pages when new cards need another sheet (up to 12).

## v15.0.1 — Library right-click: current tag/card or create new
- **Library pictures and words** right-click menus now offer:
  - **Send to 3D tag (Maker Studio)** → Current highlighted tag · Create new tag
  - **Send to PDF Studio** → Current highlighted card · Create new card
- If nothing is highlighted, “Current …” is disabled and you can still create new.
- Double-click still uses auto mode (current when something is selected, else new).

## v15.0 — Qwen 3B only + SD Turbo; ready alerts on each download
- **Text model is Qwen2.5-3B-Instruct** (~2.1 GB Q4_K_M from the official
  Qwen GGUF repo). The old 0.5B and Coder 1.5B options are removed.
- **Images stay on SD Turbo** (model + engine under local_ai/sd_turbo/).
- **Ready alerts.** When Qwen 3B finishes downloading, a dialog tells you it’s
  ready (and to download SD Turbo next). When the SD Turbo model or engine
  finishes, another dialog confirms that step.
- AI Settings buttons are labeled **1 · Download Qwen 3B** and
  **2 · Download SD Turbo model** so the order is obvious on a fresh install.

## v14.9.1 — “Words similar to …” actually adds words; PDF fill-from-Word-Lists button
- **Word requests no longer return empty.** Asking “build me words similar to
  jump and add them to my word list” used to end with “no new words or
  settings this time” because the small local model treated it as a settings
  question. Word-intent is detected now; a default of 8 words is used when
  you don’t name a number; a focused word-only prompt (no SPEC dump) generates
  the list and tops up until it has enough unique new words.
- **PDF Studio → Fill cards from Word Lists.** One button puts every word from
  your current Word Lists bank onto PDF cards (skips ones already there). It
  never runs automatically when you change layout or page count.

## v14.9 — Stuck AI banner, stable studio size, one recommended text model, Animals pack
- **GENERATING TEXT banner no longer sticks.** When the local AI finished a
  chat reply with no new words and no settings changes, the blue loading bar
  kept spinning forever. The done path always clears the busy banner now
  (plus a safety net if any handler forgets).
- **Studio size stays put.** Canvas redraw ignores tiny size jitter and
  debounces longer, and the sidebar width is locked so clicking around
  tabs/tiles does not keep resizing the layout.
- **One recommended text model.** Qwen2.5-Coder-1.5B is the recommended
  on-device text model (help, word lists, reviewing). The 0.5B is optional
  for low-RAM machines. Real pictures stay on SD Turbo.
- **Animals & phrases board.** 25 new icons (I want, animals, monkey,
  alligator, … goat) ship in icons/ and the matching words appear under
  Library → Words via the new “Animals & phrases” topic pack.

## v14.8 — Name your files before exporting; PDF / 3D / plate export fixes
- **Name the file first.** Every export now opens a small dialog: type the
  file name, pick the folder (Browse…), and see the exact files that will be
  created. It remembers the last name per export type, asks before replacing
  an existing file (Yes = replace, No = keep both as _2, Cancel = go back),
  and works with Auto-save too. Applies to PDF Studio, single 3D tags
  (3MF / STL / Both), "separate file for each tag" (named sub-folder, or
  blank for none) and "one plate".
- **Plates no longer overwrite each other.** The plate was always saved as
  plate.3mf / plate_base.stl — a second plate silently replaced the first.
  It now uses the name you give it. The plate also warns when the layout is
  deeper than the (assumed 220 mm) bed.
- **Two words, one file name — fixed.** "Mom" and "mom!" (or any words that
  clean up to the same name) overwrote each other in separate-file and plate
  exports; they now get _2, _3… and unique part names inside the plate.
- **STL single export** no longer pretends to save "name.stl" while writing
  name_base.stl + name_ink.stl: the dialog lists the real files, and checks
  those for overwrites.
- **PDF export is safer.** PDFs are written to a temporary file and swapped in
  only when every page is done, so a failed export can't leave a corrupt PDF
  or destroy a good one. PDFs are built with matplotlib's non-GUI backend
  (the old default could error or hang when drawn on the worker thread).
  A PDF that's open in a viewer now gives a plain "close it and retry" message.
- **No double exports.** The PDF export now locks the Export button like the
  3D exports do, and clicking while busy tells you instead of doing nothing.
- **AI can now add words straight to PDF Studio cards or Maker Studio tags.**
  In the AI tab, pick **New words go to:** Word list only / PDF Studio cards /
  Maker Studio tags (or just say it: "put them in PDF Studio", "…on tags").
  Say any number — "five similar words for excitement", "12 feelings words",
  "a dozen food words" — and you get that many NEW words (the AI is told what
  you already have and is asked again if it repeats or comes up short).
  It only ever ADDS: PDF cards are appended after your existing cards (blank
  cards stay blank), Maker tags after your existing tags (their per-tag designs
  untouched). Maker also switches Multiple tags on and widens "How many tags"
  to All if a smaller number would hide the new ones. New words also go in your
  Word list, as before.
- **Pictures for the new words:** by default they get library / ARASAAC
  pictures like any new word. Tick **AI-draw pictures for them** to have the AI
  draw a picture for each new word instead (uses the AI image backend; needs it
  set up, same as right-click → Generate an image).

### v14.8 — Local AI: install freezes/crashes, words and images (stability pass)
- **The window no longer freezes during install.** Every download tick used to add a
  new chat bubble (thousands of widgets for one 1 GB model). Progress now updates one
  status line (max ~2x/second) and only real steps appear in the chat.
- **The app can't stop hearing from background jobs any more.** One background result
  whose handler raised used to kill the message loop for the rest of the session
  (previews, progress bars and busy buttons froze). A bad message is now logged and
  skipped. Worker threads also no longer touch Tk directly (safe `ui_call`).
- **No surprise Ollama installer.** If the engine couldn't be set up the app silently
  downloaded and installed the whole Ollama program (+ a model). It now only uses Ollama
  if it is already running, and otherwise tells you the plain fix.
- **Engine setup fixed.** It downloads the llama.cpp program first (no pip, no compiling),
  never uses pip inside the packaged EXE (that relaunched the app), and pip is
  wheel-only (never tries to compile). "Engine not found" is no longer remembered
  forever, so a fresh install / a file you drop into local_ai/ is seen immediately.
- **Downloads resume.** The 5 GB SD Turbo model (and the others) keep their .part file
  and automatically continue after a dropped connection instead of restarting at 0%.
- **Text AI no longer hangs or breaks on odd characters.** helper programs now run with
  input closed (llama-cli's chat mode waited for typing forever), no flashing console
  windows, UTF-8 output (a curly quote used to crash the job), and chat mode switched off
  (uses llama-completion when the release includes it).
- **Words:** if the small model cuts off or garbles its JSON the app now repairs it
  (keeps the complete words) or asks once more; word suggestions get a bigger budget.
- **Images:** AI icon drawing is limited to 12 simple shapes / 1200 tokens (was 30 / 3000 —
  up to ~15 minutes per icon on a CPU). Each picture appears as soon as it is made, SD
  Turbo is skipped for the rest of a batch after it fails once, and out-of-memory is
  explained in plain words.
- **AI pictures now match the word.** SD Turbo used to get the bare word (plus "AAC
  symbol / pictogram" wording it drew literally), so feelings, verbs and abstract words
  came out unrelated. The AI now first works out ONE concrete thing to draw ("excitement"
  -> "a child jumping with both arms up and a big smile"), puts it first in a short clean
  prompt, runs 4 steps instead of 2, and centres the result on a clean square. The chat
  shows what it drew each word as. New right-click option **Draw "word" my way…** lets you
  describe the picture yourself (leave the word unchanged to let the AI decide).
  Note: 4 steps takes about twice as long per picture as before.
- **Pictures are looked up online automatically for words that have none.** Adding or
  AI-generating words used to check only your computer (online only on export or "Find
  pictures…"). Now, with Icon source = "Auto (local, then online)", every new word with
  NO picture on this computer is looked up on ARASAAC in the background. Words that
  already have a picture (saved, custom, or AI) are never searched online, words sent
  from the Library without one are left alone, and a word ARASAAC has no match for isn't
  asked again that session. "Local icons only" still means no internet.
- **Picture style for AI pictures** (AI tab, next to "New words go to"): Flat clipart,
  Cartoon sticker, Line drawing, Bold icon, Picture book - so you can find the look SD
  Turbo does best on your words.
- **Import a board from a PDF or a picture** (Word Lists tab and PDF Studio panel: "Import a
  board (PDF / picture)…"). The app finds the grid of cells, cuts out each cell's picture,
  reads the word (straight from the PDF's text when it has any; with OCR if the optional
  reader is installed; otherwise you type it) and shows everything in a review window to
  correct before anything is imported. Then it can add the words to your Word list (and save
  them as a named list), save the pictures to your Library so those words use them (never
  replacing a picture you chose yourself unless you tick it), and put the cards in PDF Studio
  in the SAME layout - same grid (any size, e.g. 8×6), same positions, blank cells kept blank,
  cell colours kept. Grids are found from the cell borders; if there are none, type the
  columns x rows and it cuts evenly. Reading PDFs uses PyMuPDF (the app offers to install it;
  it's in requirements.txt). Optional OCR for pictures: pip install rapidocr-onnxruntime.
- **AI pictures that looked like arrows and blocks - fixed at the source.** When SD Turbo
  isn't installed (or failed) the app quietly fell back to the text model "drawing" with
  basic shapes, which is exactly those arrows and blocks. It no longer does that silently:
  if SD Turbo isn't installed you're told why and offered a one-click **install SD Turbo**
  (engine + model, resumable) that then makes the picture; the basic shapes are used only
  if you explicitly choose them. If SD Turbo fails mid-run you now see its real error
  instead of shapes. The chat labels each picture with the engine that made it.
- **Maker Studio tiles no longer vanish when you click around.** The word tiles are
  rebuilt by destroying every button; doing that from inside a tile's own click (or several
  times in a row while clicking fast) could leave the area empty. Rebuilds are now merged
  and run after the click finishes, the new tiles are built BEFORE the old ones are
  removed (if anything fails the tiles you had stay), and a stuck drag ghost is cleared.
  Same protection for the PDF Studio word box and the Word Lists tiles.

## v14.6 — Presets are back, and they only load the word box
- **The presets dropdown is back** in PDF Studio (it was taken out in
  v14.5 — sorry, that wasn't the ask). Picking **Load words from…**
  now changes **only the words in the word box** (the tiles in the
  sidebar). It never touches the cards on your preview pages.
- **The word box is a palette now**, like Maker Studio: the tiles show
  the loaded list, and **clicking a word puts it on a card** — onto the
  highlighted blank if one is lit, onto the selected card (replacing
  its word), or as a new card at the end if neither is picked.
  Right-click a tile for the same choices, an AI image for the word,
  or removing it from the box.
- Everything else from v14.5 stays: PDF Studio starts with a blank
  page, cards-per-page / page count never refill your cards, and
  there's no option bar above the preview in PDF Studio or Maker
  Studio.

## v14.5 — PDF Studio starts blank; the bar above the preview is gone
- **No more presets swapping your pages.** The **Load words from…**
  dropdown in PDF Studio is gone. PDF Studio now **starts with a blank
  page** every time, and you fill it in card by card — the word box,
  clicking a blank card, right-click picture options, the AI word +
  image option and the Library all work exactly as before. Changing
  **cards per page** or **number of pages** now changes only the page
  grid — it no longer pulls words in from Word Lists or cuts cards off.
- **The little option bar above the preview is gone** in PDF Studio and
  Maker Studio. Everything it held already lives in the sidebar: PDF's
  **Selected card** panel (picture picker, Find pictures, My image,
  label, tile colour) and Maker's list dropdown, tiles, word pick and
  picture buttons. Tag Design keeps its bar — it has no word picker of
  its own.

## v14.4 — PDF Studio sidebar reorganised: one word area, presets, more right-click AI
- **One place to type a word.** The PDF Studio sidebar now starts with an
  **Add a word** area: type a word, then **＋ New card** makes a fresh
  card from it (Enter does the same), **Put on highlighted card** puts
  the typed word on the card you highlighted (a word card or a blank),
  and **Remove selected card** sits right under it. No more hunting
  between separate Add/Remove rows.
- **Presets dropdown.** Under the word box, **Load words from…** lists
  Current words, your saved lists, and every preset (Feelings, Actions,
  Describing words, Everyday words…) — picking one loads its words as
  the cards. It stays in step when you edit lists in Word Lists.
- **The rest is grouped, not scattered:** your word tiles, the Selected
  card panel, then a **Page & print** section (cards per page, pages,
  sliders, card content). Everything from before is still there.
- **Blank cards: click works too.** Clicking a blank card now highlights
  it (teal), same as right-clicking — dragging from it still pans the
  page. Right-click options on blanks gained **✨ Create AI word +
  image here…**: tell the AI what the card is for and it invents the
  word, puts it on that exact card, and creates its picture.
- **Right-click a filled card** in the preview now also offers
  **✨ Generate / create an image for its word** (it was only on the
  word tiles before), and when an AI image finishes while you're in
  PDF Studio, the card's picture updates on screen straight away.


## v14.3 — Printed words fixed, blank PDF cards fillable, faster tab switching
- **3D tags printed with the word backwards and upside down — fixed.**
  The print-orientation step flipped the tag both ways (a 180° turn),
  which can never correct a design that prints face-down: on the finished
  tag the word stayed mirror-backwards and flipped. Now the face-down
  styles (Flat inlay, Outline) are mirrored left-right only — turn the
  finished tag over and the word and picture read exactly like the
  preview, the right way up, hanging loop at the top. Face-up styles
  (Raised, Sunken) are no longer flipped at all. Verified from the
  exported geometry for every style; tags stay watertight and seated
  on the bed.
- **PDF Studio: blank cards can be filled.** Right-click any blank tile
  in the preview (or a "(blank)" tile in the word list) and it lights
  up teal: **Add a picture here… (my image)** puts any picture file on
  that exact card (the card takes the picture's name), and **Find
  pictures for a word here…** asks for a word and opens the usual
  picture gallery. Cards after the filled one keep their places —
  filling a later blank pads the gap with blank cards that print as
  blank cards, and a blank can be removed again from the same menu.
  Right-clicking a filled card now offers its picture actions too.
- **Tab switching streamlined.** Switching tabs churned the layout and
  repainted the whole preview on every layout twitch (often 4–5 full
  repaints per switch), the Library rescanned every picture file and
  rebuilt itself on every visit to Word Lists even when nothing had
  changed, PDF Studio stacked a new 90-second connection check on
  every visit, and the Log badge reconfigured itself 8 times a second
  forever. Now: repaints are coalesced (at most one per layout settle,
  skipped when the canvas didn't change size), the Library reloads
  only when its words/pictures actually changed, there is only ever
  one connection-check chain, and the badge updates only when it
  changes. Nothing was removed — everything works as before, it just
  doesn't redo work it already did.


## v14.2 — PDF Studio words are tiles now
- **The old plain list is gone.** The **Words / card labels** section in
  PDF Studio is now word **tiles**, the same design as Maker Studio and
  Word Lists: click a tile to select its card (the tile lights up teal,
  in step with the ring on the card in the preview).
- **Right-click a tile** (like Maker's tiles): **Add to current
  highlighted tile** puts that word on the highlighted card, replacing
  its word — the picture/label choices follow the word, same as a
  rename. The menu also has Select this card, generate-an-image, and
  Remove word.
- Everything else about the section works as before: type + Add,
  Remove (now removes the selected tile's word), cards-per-page refill,
  drag cards in the preview to reorder, and your words are saved and
  restored exactly as they were.


## v14.1 — Fix: picking a preset still swapped the preview tags
- **The bug:** if the app started with **Multiple tags** already on (from
  saved settings), the preview had never actually been locked — it was
  quietly following the **Current word list** dropdown. Picking a preset
  like **Feelings** replaced every tag in the preview with feelings words.
- **The fix:** the first words the preview shows are now locked in as
  your working set, from the first moment they appear. From then on the
  preview only changes when YOU change it (put a word on, Load into
  preview, right-click → Remove). Picking any list or preset now only
  fills the word tiles below the dropdown, which is all it should do.
- Verified by replaying the exact report: app starts in Multiple tags,
  pick **Preset: Feelings** → preview keeps your tags, tiles show the
  feelings words.


## v14.0 — Maker preview permanence, finished
Built on Cody's own v13.32 (the "preview stays put" build). His locked
working set was the right idea — this release closes the four ways the
preview could still change behind your back, so the tags in the Maker
Studio preview area are truly permanent until YOU change them:

- **Tag count no longer resets the preview.** Changing "How many tags"
  used to reload the preview from the current word list. Now it only
  changes how many of your preview tags are shown/exported.
- **Flipping Multiple tags off and on no longer resets it either.** The
  working set is only seeded from a list the very first time (or when
  you click **Load into preview**).
- **The preview set is saved.** Close the app, reopen it — your tags are
  still there (stored in settings with your per-tag designs).
- **Putting a word on now adds it.** In Multiple tags mode, picking a
  word (tile, quick-pick, 📚 All words…, or typing it) adds it to
  the preview as a new tag when nothing is highlighted; with a tag
  highlighted it still replaces just that tag, as before.
- **Taking a tag out:** right-click any tag in the preview →
  **Remove from preview** (the generate-an-image option is on the same
  menu).


## v13.32 — Maker preview stays put; all-words picker like the Library
- **Preview no longer changes when you flip the word list.** The Maker Studio
  tag preview is what you are working on: it only updates when you put a word
  on the tag, clear it, or click **Load into preview**. Switching “Current word
  list” only refreshes the tiles and the quick-pick menu.
- **📚 All words…** button next to the quick-pick dropdown. Opens a Library-style
  picker of every word (lists, presets, common AAC, boards, AI) with **Search**
  and sort (**By theme**, A→Z, Z→A, By source). Click a word to put it on the tag.
- **Load into preview** loads the selected list into the multi-tag working set
  on demand. Turning Multiple tags on also locks that list as the working set.
- Top-bar word menu keeps all available words so the current preview word is
  never forced off the menu when you change lists.

## v13.31 — Multi-tag edit actually works (one tag only)
- **Shape / style / colour changes only affect the highlighted tag.** While a
  tag is selected, the other tags keep their previous design. Click empty
  space to clear the highlight — then changes apply to every tag again.
- **Double-click a word tile** (or right-click → “Put on highlighted tag”) to
  put that word on the highlighted tag only, without touching the others.
- Fixed: multi preview rebuilds correctly in both Maker Studio and Tag Design
  when you edit one tag.

## v13.30 — AI helpers where you need them
- **✨ Suggest words** moved out of the AI tab and placed next to the word-entry
  boxes in **Maker Studio** and **Word Lists** (purple “✨ Suggest” button). Click
  it, type a situation (e.g. bedtime, dentist), and the AI fills your list.
- **Removed from AI tab:** Prepare all words, Draw this icon, Quality-check this tag
  (they were rarely used and cluttered the page).
- **AI tab keeps:** Create picture (SD Turbo) and Explain log, plus setup and the
  automatic picture-picking switches.

## v13.29 — Clickable multi-tags, tag count selector, per-tag design, brighter AAC palette
- **Click any tag in the multi-tag preview** (Maker Studio or Tag Design) to
  edit just that one. Change shape, style, colours, etc. — only the selected
  tag updates. Click empty space to go back to editing all tags together.
  A coral “✎ editing” ring shows which tag is selected.
- **How many tags selector** next to the Multiple tags switch: All / 1 / 2 / 3 /
  4 / 6 / 8 / 12. Preview and export use only that many from the current list.
- **Tag Design tab now shares the multi-tag preview** when Multiple tags is on
  — same tiled real-tag view as Maker Studio.
- **Per-tag design overrides** are saved with your settings (shape, style,
  body/ink colour, and any other design field you change while a tag is selected).
- **Colour scheme refreshed for special-needs / AAC vibe**: brighter coral
  primary actions, teal export/success, sunny highlights, richer purple for
  AI and selected tiles. Preview backgrounds are a touch warmer and clearer.

## v13.28 — Honest Reset + Maker multi shows real tags
- **Reset local AI now visibly switches everything OFF.** The status dot
  drops to "○ Local AI off" the moment Reset starts (13.27 left the old
  green dot up for the whole run and never refreshed it at the end), the
  off state is shown again before the reinstall begins, and the final
  status is refreshed when it finishes.
- **The wipe is verified.** The in-process model is unloaded before
  deleting (on Windows a model file the app has loaded can't be
  deleted), locked files get a retry, and anything that still can't be
  deleted is named in the report — a leftover model can no longer keep
  the AI secretly "on".
- **Maker multiple-tags preview reworked** (Cody's spec): it now looks
  exactly like the single-tag preview — the real tag shapes with their
  pictures and words — just tiled side by side, each tag scaled down so
  the whole list fits the screen. Tags build in the background; a word
  shows as plain text until its tag is ready. The old rectangle-card
  grid is gone.

## v13.27 — Automatic picture saving, Maker multi-tags, PDF word position, AI reset
- **Settings cleaned up.** The one-off printable PDF buttons (PECS cards /
  choice board / First→Then / visual schedule) are gone — PDF Studio is
  the one place for printable cards now. The dead code behind them went
  with them.
- **Pictures save themselves.** The "Download icons for offline use"
  button is gone because it's automatic now: every new word the app sees
  gets its picture fetched and stored in the background, and pictures
  (uploads, AI images, search picks) keep saving to the Library as before.
- **PDF Studio: Word position slider**, next to Word size and mirroring
  Picture size/position — moves the word label up or down in each card,
  in the preview and the exported PDF. Saved with your other PDF settings.
- **Maker Studio: Multiple tags mode.** A switch under the word tiles
  turns the preview into a grid of tags for the whole current list.
  Export then asks: a separate file for each tag, or **one plate** with
  every tag laid out together (single 3MF/STL pair, 220 mm plate width).
- **AI tab → Advanced: Reset local AI.** One button removes everything
  in local_ai/ (llama.cpp engine, both models, SD Turbo engine + model,
  broken-model marks) and reinstalls the whole stack fresh, reporting
  anything that failed. Words, lists and pictures are not touched.
- **SD Turbo install audited** (Cody asked for a double-check): the SD
  engine download was run end-to-end — real zip, unpacks, sd-cli.exe
  found. The model URL answers with exactly 5,214,561,328 bytes. One bug
  found + fixed: a model download that dropped mid-stream was renamed
  into place as if finished — it now stops with a "download stopped
  early" error and no fake model is left behind. SD download failures
  also use the plain-English "no internet" message now.

## v13.26 — Local AI: one job at a time + automatic smaller-model switch
From Cody's log (2026-10-18): several llama-cli processes were running at
once — an AI task, a second task, and the setup probe — all sharing one
CPU, so each ran at a fraction of full speed until the biggest job hit
its timeout and reported "let the smaller fast model handle this one".
- **One inference at a time.** Every on-device engine run — word helpers,
  chat, icon drawing, the setup model probe, and SD Turbo — now takes a
  single lock. Other jobs queue instead of competing, so each runs at
  full speed. (This contention, not the models, caused most timeouts.)
- **Timeouts switch models automatically.** If a model times out, the
  app now retries the job with the next model in local_ai/, smallest
  first — no message telling you to do it by hand. When the smaller model
  finishes the job, the AI output notes that it switched and why.
- **Timeouts have a bit more headroom** (the 13.25 caps cut real jobs
  off on a slower laptop: now a 60-second base for loading plus ~0.28 s
  per token, up to 15 minutes for the biggest jobs).
- **Download failures speak English.** With no internet, downloads used
  to report raw "getaddrinfo failed" errors; they now say there's no
  internet connection right now and that anything already in local_ai/
  still works offline.

## v13.25 — Interactive PDF preview + honest local AI status
- **PDF Studio preview is interactive.** Scroll the mouse wheel over the
  page to **zoom** (40–300%), drag empty space to **pan**, and use the
  **+/−/Reset** buttons under Preview zoom. Zoom keeps the point under
  the cursor fixed so you can inspect individual cards closely.
- **Picture size slider** (50–140%). Scales the icons on every card in
  the live preview and in the exported PDF. Remembered with your other
  PDF settings.
- **Picture position slider** (high / mid / low). Moves the icon band up
  or down inside each card so you can favour bigger labels or bigger
  pictures without leaving the page.
- Word size slider still works the same (50–160%, auto-fit per card).
- Tips under Card content explain: drag a card to reorder · drag empty
  space to pan · scroll wheel to zoom · double-click a card for pictures.
- **Local AI tells the truth about what works.** Set up local AI now
  **probes** each GGUF with llama-cli. A file that looks complete but fails
  to load is marked broken, skipped on later calls, and listed so you can
  re-download it. Status shows which models work and whether you have
  **text**, **draw**, and/or **images** (SD Turbo).
- **Image generation is gated.** Real pictures need SD Turbo. Simple icon
  drawing needs a working text model. If the only model is damaged, the app
  no longer pretends AI is on or loops “image generation failed”.
- **Broken models are remembered** in `local_ai/.broken_models.json` until
  you re-download them (download clears the mark).

## v13.24 — Maker tiles panel, PDF word-size slider, AI audit
- **Maker Studio: the Word Lists tile design is now at the top.** The
  type-a-word box moved down; in its place: the **Current word list**
  dropdown and a grid of **word tiles** (same design as Word Lists — the
  word on the tag stays lit purple). **Click** a tile to put it on the tag,
  **drag** a tile onto the tag, or **right-click** it for options (Put on
  tag / Find pictures / ✨ Generate an image / Send to PDF Studio).
  Switching the list re-deals the tiles. The old plain word list under the
  gallery is gone — the tiles replace it.
- **Gallery pictures can be dragged too.** Click a picture to use it as
  before, **drag it onto the tag**, or **right-click** it for "Put this
  picture on the tag".
- **PDF Studio: Word size slider** (50–160%, next to Cards per page /
  Number of pages). It scales the words on the cards, and every word still
  **auto-fits its card**: short words fill up to the slider size, long
  words shrink just enough to fit instead of running off the edge. The
  preview and the saved PDF use the same sizing, and your setting is
  remembered.
- **Local AI audit (text engine).** Your 13.23 model-fallback work checks
  out (verified: a stale or missing model path now falls back to the
  models actually in local_ai/, with the note saying which one answered).
  Two bugs fixed: (1) the fallback candidate list could re-add a damaged
  model file that the folder scan had already excluded, hiding the
  "model is damaged — download it again" message behind a generic error;
  (2) if no model was usable and Ollama was the fallback, it could be
  handed a .gguf file path as if it were an Ollama model name, producing
  a confusing "model not found" — it now gets the proper model name.

## v13.23 — Everything the AI makes goes into the Library, labelled AI
- **AI pictures are labelled AI in the Library.** Every picture the AI creates (SD Turbo or
  the local AI's drawn icons) is saved as `<word>_ai.png` and shows under
  **Library → Pictures** with a purple frame and an "✨ AI" label. Regenerating never
  overwrites: each try is kept as its own picture (`_ai_2`, `_ai_3` …) and the newest one
  becomes the word's picture. SD Turbo now uses a fresh random seed when regenerating, so
  you actually get a different picture (before, the seed was fixed and it repeated itself).
- **Right-click any word → "✨ Generate an image for this word".** Works on word tiles
  (Word Lists), Library words and pictures, the live tag preview, and the PDF Studio card
  list. The wording adapts: *Generate* (no picture yet), *Make an AI image* (has a normal
  picture), *Regenerate AI image* (already has an AI one). Select several Library words
  and it generates for all of them in one go. Uses SD Turbo if installed, otherwise the
  local AI's icon drawing, otherwise it points you to the setup buttons.
- **AI tab: a big "what is the AI doing" banner.** Purple = 🎨 GENERATING / REGENERATING
  IMAGE, blue = ✍️ GENERATING / REGENERATING TEXT, green = ✔ SAVED TO LIBRARY — AI LABEL,
  red = failed. Shows the word, the method, a running timer and a progress bar, and has
  shortcut buttons to the AI pictures / AI words in the Library. Errors appear in the
  banner instead of a pop-up. The automatic "AI draws an icon if none is found" switch
  reports to the same banner.
- **AI-generated text goes to Library → Words under AI.** Words from "Suggest words" and
  from the AI command box are recorded (ai_library.json) and show as purple "✨ AI" tiles.
  The Library sort menu has a new **✨ AI only** view, and "By source" lists AI first.
- **Uses the models you actually have.** If the Coder 1.5B (or the 0.5B) is missing, has a
  stale path from an older folder, or fails to load, the AI now automatically tries every
  other real .gguf in local_ai/ and says which one it used. If the models and engine are
  already in local_ai/, the AI turns itself on — no need to click "Set up local AI" first.
- **Broken model downloads are caught and repaired.** The log showed the Coder 1.5B failing to
  load instantly on every AI call: an earlier download was cut short (app closed mid-download),
  and the half-file stayed in local_ai/ looking like a real model. Now: downloads go to a
  `.part` file and are only renamed once the full size is verified; model files that are
  truncated or not real GGUF files are ignored (and shown in red in the AI tab); clicking the
  model's Download button replaces a broken file automatically; and a load failure now says
  "incomplete or damaged download — click Download again" instead of a llama.cpp dump.
- **Images fall back too.** If SD Turbo isn't installed or fails, "Generate an image" uses the
  Local AI's icon drawing instead (and the AI tab banner says so). With no models at all you
  get one plain message saying what to put in local_ai/ — no stacked llama.cpp/Ollama errors.
- Fixed: re-building a tag whose icon was force-drawn no longer re-saves a copy each time.

## v13.22 — Local AI engine fix (the "CPP / Ollama errors")
- **Found it:** the program search that locates the llama.cpp engine was
  matching files loosely, and the engine folder also contains
  `llama-bench.exe` / `llama-batched-bench.exe`, which sort *before*
  `llama-cli.exe`. The app was running the **benchmark tool** as the chat
  engine — every AI helper failed with a llama.cpp error, and then the
  Ollama fallback added its own "not running" error on top. The engine is
  now matched by its exact name (`llama-cli.exe`), with the old `main.exe`
  kept as a legacy fallback only.
- **Proper chat format:** prompts are now sent in the Qwen ChatML format
  the models were trained on, so the model stops at the end of its answer
  instead of rambling to the token limit — faster, cleaner answers, and
  far fewer timeouts. Anything the model adds past its answer is trimmed.
- **Timeouts scale with the job:** the old fixed 3-minute limit (which
  big jobs like icon drawing could hit on a slower PC) now grows with the
  size of the request, up to 25 minutes, and a timeout now says so in
  plain English.
- **Quieter fallback:** Ollama is only tried when it is actually running,
  so a llama.cpp problem is reported as itself — one clear message
  instead of two stacked errors.
- **ONNX note:** the words-side ONNX swap was checked again — there is
  still no trustworthy ready-made ONNX build of the two Qwen models
  (only 0-download uploads from unknown accounts), so the GGUF stack
  stays until official builds exist or the models are converted in-house.

## v13.21 — Library pictures in Maker, everything saved to the Library, SD Turbo
- **Maker Studio: "Select from library…"** — a third picture button next to Find
  pictures and Upload my image. It browses every picture in your current
  database (the Library) as thumbnails with a word filter; click one and it
  goes on the tag.
- **Every image lands in the Library, and the Library is searched first** —
  pictures from online search were already saved and found first; now uploaded
  images and AI-created images are copied into the Library (icons/) and
  remembered as the word's picture too, instead of living outside it.
- **SD Turbo — image AI in the local AI tab** — words and images are different
  stacks: the GGUF models do words; **SD Turbo does images**. New "Image AI —
  SD Turbo" section with one-click downloads using the same method as
  llama.cpp: the stable-diffusion.cpp engine (~18 MB) and the SD Turbo model
  (~5.2 GB) into local_ai/sd_turbo/. The new helper **"✨ Create picture
  (SD Turbo)"** generates a pictogram-style picture for the current word on
  this PC and saves it into your Library. (SD Turbo is Stability AI's model,
  free for non-commercial use — this app is non-commercial.)
- **Preview word dropdown matches Maker Studio** — the word dropdown in the
  top bar now lists the same words as Maker's "Current word list" (switch the
  list in Maker and the preview dropdown follows).

## v13.20 — Word tiles, PDF auto-fill, no blank cards
- **Word Lists: words are tiles now** — the old text box (with its blinking cursor
  when you clicked a word) is gone. Each word is a tile: it glows when you hover,
  the word you're working on stays lit purple, and **right-clicking a tile** gives
  options — send the word to the 3D tag (Maker Studio), send it to PDF Studio, or
  remove it from the list. Quick-add still works (it now also accepts several
  words at once, separated by commas or lines).
- **PDF Studio fills itself from your words** — changing Cards per page or Number
  of pages now fills the card slots with the words from your current word list
  (Word Lists tab), each with its picture. No more "card 8 / card 15 / card 13"
  placeholders — slots beyond your words simply stay empty, and old placeholder
  entries saved from earlier versions are cleaned out of the list automatically.
- **No blank picture cards** — when no picture exists for a word, the card shows
  the word itself, big and centered, in the preview and in the saved PDF.
- **Page controls under Number of pages fixed** — the ◀ ▶ arrows now switch to
  One-page view so they visibly move between pages (in All-pages view every page
  is already on screen, so they looked dead), and your One page / All pages choice
  is no longer reset every time the page count or layout changes.

## v13.19 — Picture boldness slider on Tag Design
- **Picture boldness** slider is now on the main Tag Design panel (not only under Advanced).
  Drag right for a bolder picture, left for finer lines. 0 keeps the solid printable default.

## v13.18 — Solid printable icons in the slicer
## v13.18 — Solid printable icons in the slicer
- **Icons no longer export as faint red speckles** — line-art pictograms are dilated
  when traced and given a small minimum wall thickness (~0.2 mm) so the ink mesh
  is solid and visible in the slicer / on the print.
- Palette PNG icons (ARASAAC style) convert correctly before tracing.
- Text still appears mirrored in the slicer on purpose: the model is oriented for
  face-down dual-colour printing so the finished top face reads correctly.

## v13.17 — Icons back to v13.14 auto behaviour + Pictures sort
## v13.17 — Icons back to v13.14 auto behaviour + Pictures sort
- **Automatic icons restored** — preview again finds pictures the same way as v13.14
  (local, then online). A stuck "remembered path" no longer blocks lookup.
- Hole values from older saves (Top / Left / Through…) are normalized so the
  dropdown and geometry stay in sync.
- **Through (top)** hole sits tighter to the top edge so it does not cut the icon.
- **Library → Pictures** uses the same Sort menu as Words:
  By theme (default), A→Z, Z→A, By source, with section headers.

## v13.16 — Icon stickiness + Library sort
## v13.16 — Icon stickiness + Library sort
- **Icons no longer disappear** when you change size, hole style, thickness, or other tag options.
  The last picture found for a word is remembered and reused offline so slider tweaks only
  rebuild geometry, not re-download the picture.
- **Through (top) hole** sits near the top edge of the tag (inside the body) so it no longer
  punches out the icon in the centre.
- **Library → Words: Sort menu**
  - **By theme** (default) — groups by AAC category (People, Actions, Feelings, Places, …)
    with coloured section headers
  - **A → Z** / **Z → A** — alphabetical
  - **By source** — groups by where the word came from (Current words, presets, topic packs, …)

## v13.15 — Tag Design simplified
## v13.15 — Tag Design simplified
- **Tag Design cleaned up** — only ~5 main controls by default (Shape, Size, Key-ring/hole, Layout, Colour style) plus one **Design preset** dropdown.
- **Advanced options** checkbox reveals thickness, design depth, hole diameter, border ring, icon/font/text settings, AAC switches, and colours.
- **Key-ring / hole** options:
  - Top loop / Left loop — protruding keychain loop (same as before)
  - **Through (top)** — hole punched through the tag body, no protruding loop
  - None — solid tag (add a hole or scale in your slicer)
- **Design presets**: Standard keyring, Small keyring, Badge — through hole, Solid badge, Raised icon keyring, Magnet tile.
- Size is still in mm; you can leave a standard size and do final resizing in the slicer.
- Old saves with hole "Top" / "Left" are mapped to the new names automatically.

## v13.14 — GUI update (loading, full-screen AI, Library)
- **Words appear instantly** — type a word in Maker Studio and press Enter / "Put on tag":
  the word shows on the tag right away (large text first, then the real tag shape), with a
  spinning banner on top: "Finding a picture…", "Downloading a picture…", and when the
  offline AI is working "✨ Local AI is loading & creating an AI image…". The finished tag
  (picture / AI image) replaces it when ready. Slider tweaks keep the old tag on screen.
- **AI (Local) is now full screen** — opening the tab hides the sidebar and the tag preview
  completely; the AI status, folder/downloads, command box, replies, helpers and switches get
  the whole window. A tab bar at the top switches back to any other tab. (Fixed: places that
  jumped to the AI tab — e.g. "Set up local AI" prompts and Log → Explain — left the preview on screen.)
- **📚 Library (Word Lists tab)** — new button. *Words* shows every word in one searchable list
  (current words, your saved lists, Common AAC words, topic packs, presets, saved pictures).
  *Pictures* is a scrollable thumbnail library of every saved picture (loads in the background).
  Words are shown as tiles (click, Ctrl-click, Shift-click to select). The Library opens by itself
  whenever you click into Word Lists (✕ Close hides it until you leave the tab).
  **Right-click** a word or picture → send it to the 3D tag or PDF Studio (the one you were last
  working in is the first choice; double-click does that too). A word sends ONLY the word — no
  picture is looked up or attached (a picture you already have stays); a picture sends ONLY the
  picture, onto the word already on the tag / the selected PDF card. Words can also be added to your list.

## v13.14 — 2026-10-14 (polish update)
Cody's polish round after confirming 13.14 works on Windows (local AI
installer + models + online picture fetching all verified live):
- **AI tab spread out** — the AI (Local) controls no longer squeeze into
  the sidebar: they take over the wide right side (status + Set up on top,
  local_ai folder/downloads on the left, command box + replies on the
  right, helpers in one row, Automatic switches in one row). The sidebar
  tab just points there.
- **Word Lists preview area removed** — the right side no longer shows the
  "current words / saved lists" info board; it shows the tag for the word
  you click, like the other tabs.
- **Enter acts as a click** — Enter in the AI command box runs "✨ Do it";
  Enter in the word list selects the word on that line; Enter in the
  Maker and PDF Studio word lists picks the selected word (on top of the
  places Enter already worked: quick-add, Maker word entry, PDF add/rename).
- **PDF Studio drag & drop** — drag a card in the preview to another slot
  to reorder the page; tile colours stay with their slots.
- **Bigger printed words** — card labels in the PECS PDF went from ~10pt to
  ~14pt (8-per-page) and up to 17pt on big cards; choice board, schedule,
  sentence strip, core board, About Me and token board labels bumped too.
  The on-screen preview labels grew to match.

## v13.14-exp (experimental) — 2026-10-13
The local AI stack, exactly as specced:
- **Two models, two jobs** — Qwen2.5-0.5B-Instruct Q4_K_M is the FAST model
  (quick helpers: suggest words, categories, picture picking) and
  Qwen2.5-Coder-1.5B-Instruct Q4_K_M is the SMART model (drawing icons,
  explaining problems, quality checks). Helpers now pick by role instead of
  whatever file sorts first. The old "Instruct 1.5B" download is gone.
- **llama.cpp b11575 is the engine** — new "Download llama.cpp engine
  (b11575)" button fetches llama-b11575-bin-win-cpu-x64.zip from the
  llama.cpp releases into local_ai/ and unpacks it. No pip, no compiling.
- **Engine order** — a llama-cli binary in local_ai/ is now used FIRST
  (before llama-cpp-python), in both setup checks and chat. The python
  wheel and Ollama remain as fallbacks.
- **"Set up local AI" installs the whole stack** — on an empty folder it
  downloads the engine + both models, then wires fast/smart automatically.
  The AI tab folder line now reports "engine: llama.cpp b11575" when found.

## v13.12-exp (experimental) — 2026-10-14
- **PDF All-pages grid** — multi-page preview lays pages out in a grid (not a single
  vertical stack) so the canvas is used fully.
- **Per-card tile colour** — colour is keyed by card *slot index*, not word text.
  Changing one “stop” card no longer recolours every other “stop” on the sheet.
- **Board Studio** — preview is strip-only and large; core word board stays on the left.
- **Strip 3D tab** — new tab for the snap-together 3D plate preview + export shortcut.
- **Word Lists preview** — blank area now shows current words (chips) and all saved lists.
- **Local AI discovery** — scans local_ai/ (and subfolders) for llama-b11575 / llama-b*
  builds and qwen2.5-coder GGUFs; ranks coder/instruct models first.
- **Downloads** — “Download Coder 1.5B” (preferred) and “Download Instruct 1.5B”
  (recommended general). Falls back to 0.5B if the 1.5B download fails. Same
  auto-install of llama-cpp-python as before.

## v13.11-exp (experimental) — 2026-10-14
- **PDF Studio: Number of pages** — under Cards per page you can now set how many
  pages (1–12). The word list auto-fills to match (cards × pages) with editable
  placeholders; switch layout or page count and the list resizes. Preview defaults
  to “All pages” so every sheet is visible at once.
- **Tag Design tab** (was “Design”) — clearer name, live note that size / thickness /
  design depth update the right-hand preview. The preview now includes a side-view
  strip that shows thickness and design-depth layers, so you see exactly what will
  print before you export.
- **Local AI for Log & diagnostics** — “✨ Explain” and every other AI helper now
  use the same GGUF model from local_ai/ (or the one-click download). The call path
  correctly routes .gguf paths through llama-cpp-python instead of only Ollama.
  Model is cached in memory so explain/log calls stay fast.
- General bug check: GGUF backend wiring, page-count sync with word list, settings
  persistence for pdf_pages / pdf_preview_mode.

## v13.10-exp (experimental) — 2026-10-13
Your list again:
- **Maker Studio: two word dropdowns** — "Current word list" picks where words come
  from (your current words, any saved list, or a preset list); "Current words" picks
  a word from that list and puts it on the tag. The clickable word list is still there.
- **Preset word lists grouped by synonyms** — Feelings, Actions, Describing words,
  Everyday words. Synonyms sit together (happy · glad · joyful) so you can pick the
  exact word. Load them in Word Lists (blank line between groups) or straight from
  Maker Studio's "Current word list" dropdown.
- **Settings: Exports section is gone** — the one-off PDF buttons moved under
  PDF Studio where they belong. In **Advanced → Auto-save exports** you can now set
  a separate folder per studio (PDF Studio / Maker Studio / Board Studio) and turn
  on auto-save so exports skip the Save dialog and land in the right folder.
- **3D printing settings simplified** — the "This word only" scope question is gone.
  The only choice is the export file format (3MF / STL / Both). Export saves the
  word on the tag in that format.
- **Board Studio preview is real now** — the preview area shows your sentence
  strip (exactly what Strip → PDF and Strip → 3D plate print) and your core word
  board, instead of the Maker tag left over from Maker Studio.
- **Strip: type your own word** — a new entry in the strip builder adds any word
  straight to the strip, even if it's not in a word list. Repeats allowed.
- Fix: saved word lists stored as lists are now read correctly everywhere
  (Maker's list switcher could silently show nothing for them).

## v13.0-exp (experimental) — 2026-10-13
Your full list, done:
- **Per-card tile colours** — pick a colour for the selected card only (Selected card
  panel). Your pick wins over category colours. Reset button to clear it.
- **Card content** — the backwards "Show word labels" is gone. Two clear checkboxes:
  "Show pictures" and "Show words". Pictures-only, words-only, or both.
- **Thumbnail dropdowns everywhere** — every picture picker in the app shows mini
  previews (PDF Studio toolbar + sidebar, main toolbar).
- **Word Lists tab** — picture section removed. It's words and lists only now, with a
  clear note that lists feed PDF Studio, Maker Studio, and Board Studio.
- **Maker Studio** — its own word list bar (click a word to put it on the tag),
  distinct from Word Lists. Tabs renamed: Maker Studio, Word Lists, Board Studio.
- **AI is local-only** — no cloud, no API keys, ever. New local_ai/ folder: drop in
  any .gguf model (+ llama.cpp if you have it) and the app finds it, or hit
  "Download model" for a one-click small model. "Set up local AI" checks your
  folder first.
- **Fix (2026-10-13): exe kept re-downloading the AI model** — in the built exe,
  the local_ai folder pointed inside PyInstaller's temp extraction dir, which is
  wiped on exit, so the ~400MB model would download again every launch. The exe
  now keeps it in the persistent per-user data folder (next to the exe when
  writable, otherwise %LOCALAPPDATA%\AACTagStudio\local_ai). No re-downloads.


## v3.11.0-exp (experimental) — 2026-10-13
PDF Studio goes pro, Words tab cleanup, Settings rebuilt:
- **Multi-page preview** — pages beyond the first finally show up. Flip with
  ◀ Page 1 of 3 ▶, or switch to "All pages" to see every page at once (smaller).
- **Tile colour** — pick the card background yourself when category colours are
  off; the button shows your current colour. Applies to preview and PDF export.
- **Picture picker with thumbnails** — the Picture dropdown is now a popup
  showing a mini preview next to Picture 1-8. Replaces the ◀ ▶ arrows in the
  main toolbar too.
- **Double-click fixed** — opens the picture gallery in-tab (was jumping to the
  Words tab on older builds).
- **Words tab** — word list uses a normal arrow cursor; no more I-beam.
- **More button gone** — its exports and offline tools moved into Settings.
- **Settings rebuilt** — Appearance, PDF Studio (paper size, cut guides),
  Exports, Offline tools, and 3D printing tucked under Advanced.


## v3.10.0-exp (experimental) — 2026-10-12
PDF Studio, rebuilt for humans ("Studio Pro"):
- **Connection status** — a green/red indicator at the top of the sidebar shows
  whether you're online; when offline it says pictures won't download, so you
  always know why a card is empty. Rechecks itself every 90 seconds.
- **Selected-card panel** — every picture option for the selected card now lives
  in the sidebar too: picture dropdown, Find pictures…, My image…, and the
  Label checkbox with a plain-English explanation. Nothing to hunt for.
- **No more tab-hopping** — double-clicking a card opens its picture gallery
  right here; the "Choose pictures in Words tab" detour is gone.
- **Smarter export button** — normal-sized now, and it saves what you're
  looking at: "Save PDF…" in PDF Studio, "Export file" (3D) everywhere else.


## v3.9.2-exp (experimental) — 2026-10-12
OpenSymbols refinements:
- **Expiry dialog, finished** — paste the fresh token right in the dialog and
  hit "Save & retry search": it saves to the credential store and re-runs your
  search on its own. Also handles HTTP 403 rejections, not just 400/401.
- **Faster repeat searches** — the icons cache is now checked before any
  download, so searching the same word again is nearly instant.
- **Exe-safe SVG rendering** — resvg-py pinned and added to the PyInstaller
  build so the shipped exe keeps Mulberry support.
- **Attribution** — PDF exports credit OpenSymbols/Mulberry/Sclera/Tawasol in
  the footer when those pictures are used (their licenses require it).


## v3.9.1-exp (experimental) — 2026-10-12
OpenSymbols, finished:
- **Temporary tokens work** — paste a token from opensymbols.org/api and it is
  used directly (no shared secret needed). When it expires, the app tells you,
  links you to the renewal page, and a Retry button re-runs your search once
  you've saved the fresh token.
- **Mulberry pictures work** — SVGs are rasterized to PNG on download
  (resvg-py wheel, no system libraries), so Mulberry joins Sclera and Tawasol
  in every picture search, the PDF maker, and the keychain maker.
- **Faster** — per-library download caps + round-robin merge: no single library
  hogs the results, and searches no longer download hundreds of images.


## v3.9.0-exp (experimental) — 2026-10-12
Pictures + labels ("Sources Edition"):
- **OpenSymbols support** — unlocks Mulberry, Sclera, Tawasol and more free
  libraries in every picture search (Maker gallery, Find pictures… popup).
  Needs a free API secret: Maker tab → "More pictures: OpenSymbols API secret"
  → get one at opensymbols.org → API page → request access. Kept in the system
  credential store. Without a key, everything works exactly as before.
- **"Word on picture" switch** — some pictures already have the word printed on
  them (STOP signs, labels), so our own word underneath is redundant. Select a
  card and uncheck **Label** in the toolbar: the card shows the picture bigger
  with no caption. Saved per word, honored in the preview and in exported PDFs.


## v3.8.1-exp (experimental) — 2026-10-12
Picture-source health fix:
- The Mulberry/Sclera/Tawasol feed (via the SymboTalk API) is dead — its server
  returns empty replies — and every "Find pictures" search was stalling up to
  ~24 seconds waiting on it. Removed the dead path; picture search now uses the
  healthy sources only (local cache + ARASAAC) and returns much faster.
- ARASAAC verified live: ~9–10 pictures per word.


## v3.8.0-exp (experimental) — 2026-10-12
PDF Studio interface rework ("Editor Edition"):
- Removed the speaker/sound button from the top bar.
- The picture ◀/▶ buttons are now a **Picture dropdown** (Picture 1–8) in the
  card toolbar — picking one applies it immediately.
- **Find pictures…** — Maker-style gallery popup for the selected card: searches
  the free libraries, shows thumbnails with a Use button per picture, applies it
  straight to the card. Plus **My image…** for your own uploads.
- The sidebar word list is now a real clickable list: click a word to highlight it
  and select its card in the document (no more fiddly text cursor). Type or paste
  words into the entry and press Enter to add them; Remove deletes the selected.
- The 3D-preview ◀/▶ navigation hides while the document tab is active, giving
  the card toolbar room.


## v3.7.0-exp (experimental) — 2026-10-12
PDF Studio rebuilt as a document editor ("Document Edition"):
- **Pictures now load automatically** — the old preview never touched the network,
  so fresh installs showed empty coloured squares. Cards now fetch their pictures
  in the background and pop in as they arrive; the UI never freezes while waiting.
- **Click a card to select its word** — a contextual toolbar appears (word name,
  picture ◀/▶ cycling, Picture… button) so word/picture selection is driven by the
  document itself, like a word processor.
- **Double-click a card** to jump to the Words tab with that word ready in the
  picture picker; "Choose pictures in Words tab" now carries the selected card's
  word over too.
- Failed/missing pictures show a subtle "?" instead of a mystery blank; loading
  cards show "…".
- Fetched pictures are cached for the session, so resizing or retyping never
  re-downloads.
- Root-cause bug fixes found while rebuilding:
  - Preview pictures could never render in any version: `photo.width` was missing
    its parentheses, so every icon draw died with a silent TypeError. Fixed.
  - The 3D tag preview could paint itself over the document (the giant shapes
    behind the page): `draw()`, the preview animation and the background queue now
    stand down while the document tab is active.
  - Card labels and the page caption used pixel values as point sizes, rendering
    hundreds of points tall. Fixed to real point sizes.


## v3.6.0-exp (experimental) — 2026-10-12
New boards & workflow features (experimental build):
- New **Boards** tab:
  - **Sentence strip builder** — order words, move up/down, export a printable
    PDF strip, or a one-row 3D print plate with snap connectors auto-enabled
  - **Core word board** — 24/36/60-cell Fitzgerald-key boards from the Core 40
    list or your current words, PDF export
  - **About me sheet** — photo + name + grouped key words ("I want / I feel /
    I need / I like"), one-page PDF
  - **Token board** — "I am working for" reward + choice row + 1–10 token
    circles, PDF export
- **Saved word lists** — name, save, reload and delete your own lists
  (persisted in settings)
- **Load words from a text file** (one per line, or comma-separated)
- **Speak button** (top bar) — hears the preview word via system speech
  (Windows SAPI / macOS say / spd-say / espeak); no new dependencies
- **Smarter plate packing** — biggest tags placed first, tighter print plates
- Strip words and saved lists persist between sessions
- Added ARCHITECTURE.md documenting the engine/GUI split for a future iOS port
  (engine modules are GUI-free and portable; only aac_gui.py is desktop-bound)


## v3.5.1  (current)
### Fixed
- **PDF Studio preview icons now render at the right size** — a pixels-vs-inches mixup drew every icon ~3.5× too big, overflowing its card. The preview now matches the exported PDF.
- **PDF Studio honours your picture choices** — custom uploads, picture variants (◀/▶) and "search as" phrases from the Words tab now carry through to the PDF Studio preview and all PDF exports (PECS cards, choice board, First→Then, schedule, offline pack).
- **Typing in the PDF Studio word box no longer freezes the app** — the preview redraw is debounced and uses local icons only (no network on the UI thread).
- **AI quality check requires a vision-capable backend** — it no longer invents scores when the local text-only model can't see the tag.
- **Maker gallery race fixed** — rapid successive searches can't show stale results for the wrong word anymore.
- **Icon variant index no longer clobbered** — gallery searches across multiple terms kept overwriting each other's variant numbers; variants are now numbered uniquely. Cycling past the last online picture clamps instead of silently repeating.
- **Print-plate 3MF keeps per-tag colours** — each part gets its own material instead of every tag inheriting the last tag's colours.
- **Word dropdown keeps its position** after visiting the PDF Studio tab.
- **Cloud model names restored** when switching from local to cloud AI with local model names still in the fields.
- **API key stored securely** — uses the OS credential store when available (Windows Credential Manager etc.), falling back to `ai_config.json` only where secure storage is unavailable.

### Added
- **PDF Studio remembers its word list and options** between sessions.
- Optional `keyring` dependency for secure API key storage.

### Changed
- Icon geometry cache and icon index are now bounded/cached in memory (less disk churn on slider drags).
- Offline pack manifest stamps the real app version; PDFs are skipped for an empty word list.
- SymboTalk candidates that aren't real images are skipped instead of being cached as broken files.

## 3.3.0-exp
### Added — Maker tab + more free picture libraries
- **Maker tab**: type a word → places it on the keychain → **picture gallery sidebar**
- Click **Use** on any gallery image (or **Upload my image…**) — applied automatically
- Gallery searches multiple free AAC libraries and **saves every chosen/found image forever**
- Extra free sources via SymboTalk API: **Mulberry** (CC BY-SA), **Sclera**, **Tawasol**, plus **ARASAAC**

## 3.2.0-exp
### Fixed — face-down dual-colour printing
- **Flat inlay / Outline**: design (word + icon) only on the **first layers** (bed side)
- **Top of the tag is solid / blank** — no letter holes through the whole thickness
- Mesh is oriented **upside-down + backwards on the bed** so the finished face reads correctly
- **Preview unchanged**: still shows the final right-reading look
- New slider: **Design depth (first layers)** default 0.6 mm

## 3.1.0-exp
### Added / changed
- **Local AI by default** via Ollama (`qwen2.5:3b-instruct`) — private, no API key
- **Enable local AI** button: checks for Ollama + model; downloads and installs automatically if missing
- Turning on AI helper switches also triggers local setup when needed
- Cloud Claude still available under **Cloud key…**
- **Click a word in the left list** to select it for preview (not only the dropdown)
- Icons saved permanently in data `icons/` and mirrored to the app `icons/` folder

## 3.0.0-exp  (current)
**Theme: AI everywhere it helps, and a much simpler window. No new pip packages.**

### New: AI (Claude) - optional, off until you add an API key
All AI calls use the standard library (`urllib`), so nothing extra to install. Without a key the app behaves like 2.1.
- **Tell the AI what you want** - plain English changes the settings *and* the word list
  ("12 mealtime words, big hexagons, extra-bold icons"). Everything the AI sends back is validated against the
  allowed options and ranges before it touches your settings.
- **Suggest words for a situation** - "going to the dentist" gives an ordered word list with category colours.
- **Prepare all words** - one call finds the best picture search term (bike ride -> bicycle) and the category for every word.
- **AI picks the best picture** - among the ARASAAC results it chooses the most literal AAC meaning
  (play = children playing, not a theatre play). Cached, so it costs nothing the second time.
- **AI draws icons** - when no picture exists (or you click *Draw this icon with AI*) Claude designs a bold pictogram from
  simple shapes (circles, polygons, thick lines, arcs, cut-outs); the app turns that into printable geometry.
  Saved in `ai_icons/` so it works offline afterwards.
- **Quality check** - sends a picture of the tag to Claude, which scores it, lists problems (thin lines, unclear picture,
  crowded text) and offers one-click fixes.
- **Explain what went wrong** - plain-English help reading `aac_tag.log` and the diagnostics (also in the Log window).
- **Set up AI** dialog with Test button; model names are editable so you are never stuck on an old model.
- Privacy note shown in setup: words and tag previews you send go to Anthropic; don't type children's names.

### Changed: the window
- **One big "Export file" button.** No more STL / 3MF checkboxes, folder box, "generate this/all", PDF rows or progress
  clutter under the preview.
  - It opens a normal Save dialog. Pick **.3mf** (both colours in one file) or **.stl** (base + ink) *in the dialog*.
  - After exporting, a small "Show in folder" appears.
- Left side is now three tabs: **Words**, **Design**, **AI**. The preview gets the whole right side.
- Everything that used to be crowding the bottom moved to tidy places:
  - Boards, PECS, schedule, offline pack, icon download: **More** menu.
  - Export scope (this word / all words / one plate), batch format, light mode: **gear** menu.
- Preview now shows category colours / high contrast exactly as they will print.

### Fixed / hardened
- Category colours can now come from the AI (and are saved with your settings).
- AI failures never block the app: errors show a friendly message and are logged; failed AI drawing is not retried for 2 minutes.

### Known limitations of this experimental build
- Written and checked without a live API key or a screen: the AI request/response handling was tested against a mock
  server, and the window code was statically checked, but **please run Run_Diagnostics.bat first** and tell me what the log says.
- AI-drawn icons appear on 3D tags and previews, but PECS / PDF boards only use picture files (not yet AI drawings).
- Model names `claude-haiku-5-5` / `claude-sonnet-5-5` are defaults you can change in *Set up AI*.

## 2.1.0-exp
### Added
- **PECS printable cards**: same words + pictures as 3D tags, laid out on letter paper
- Layouts: **4 / 6 / 8 / 9 / 12 / 16 cards per page** with cut guides
- Option for word label under picture; uses category colours when enabled
- Included in offline AAC pack

## 2.0.0-exp
Experimental AAC / autism-focused release. **No new pip packages required.**

### Added
- **Category colours** (Fitzgerald-style): People yellow, Actions green, Nouns orange, etc.
- **Size presets**: Keyring 30 mm → Large 60 mm
- **Snap connectors** for building sentence strips
- **Magnet recess** on the back of tags
- **High-contrast** black/white mode
- **Topic packs**: Core 40, Feelings, Classroom, Mealtime, Playground, Body & pain, Safety & break
- **PDF boards**: Choice board, First→Then, Visual schedule (matplotlib, already installed)
- **Build offline AAC pack**: icons + 3D tags + PDFs + vocabulary.csv + manifest
- **aac_boards.py** module for packs and boards

### Notes
- Experimental — test prints recommended for connectors and magnet recess
- PDF boards need icons cached (use Download icons / offline pack while online)

## 1.4.0  (current)
Focus: permanent offline icon cache + reliable print orientation for text.

### Added
- **Download icons for offline use** button: while online, fetches a pictogram for every word in the list and saves it permanently.
- **Icon index** (`icon_index.json`) maps each word to its cached file so offline lookup always works — even for custom words and "search as" phrases.
- Icons are saved under the **word you typed** (not only the search term), so they survive later offline sessions.

### Fixed
- Offline: "I have the words but the icon is not there anymore" — cache is now durable and looked up by word name + index.
- Print orientation: models rotate 180° around Z only (no letter mirroring) and sit on Z=0 so tops/bottoms of words stay correct and readable on the build plate.

## 1.3.0
Focus: smoother UI, more tag styles, one-word generation, custom-word picture search.

### Added
- **More shapes**: Square, Star, Heart, Shield, Oval (plus Circle, Rounded square, Hexagon).
- **More styles**: Sunken and Outline in addition to Flat inlay and Raised.
- **Generate this word** (default) vs **Generate all words** — no longer builds every word at once unless you ask.
- **Find picture for this word as…** field: type any custom word/phrase and map it to an AAC search term (e.g. bike ride → bicycle).
- **Live preview motion**: scale-in animation + soft shadow when options or icons change.
- **Status flashes** and clearer hover colours on buttons.
- Expanded alias table and ~90 common AAC words.
- **requests** package for more reliable online icon downloads (still works without it).

### Changed
- Version 1.3.0.

## 1.2.0
Focus: better automatic icon matching, one-click batch of common AAC words, and correct print orientation.

### Added
- **Smarter icon search**: typing a word now tries several close matches (aliases, singular/plural, verb stems) against ARASAAC `bestsearch` and `search`, so far more words get a suitable picture automatically.
- **Large alias table** for everyday AAC vocabulary (food, feelings, actions, places, etc.).
- **"Load common AAC words" button**: fills the words box with ~50 core vocabulary items ready for batch generation.
- **Print orientation fix**: models are mirrored and flipped so they sit correctly on the build plate. You no longer need to rotate them backwards and upside-down in the slicer.

### Changed
- Version bumped to 1.2.0.

## 1.1.0
Focus: reliable start-up on a computer that already has Python and pip packages, plus logging.

### Why the v1.0 window opened and closed
I could not see your screen, so these are the causes ranked by likelihood. All are fixed in 1.1.
1. **Windows Store "python.exe" stub.** `where python` succeeds, but the stub just opens the Store and exits, so the window flashed and closed.
   v1.1 finds Python with the `py -3` launcher first and *runs* the candidate to prove it works.
2. **LF line endings in the .bat files.** The v1.0 batch files were created with Unix line endings. cmd.exe can misread multi-line `( ... )` blocks and `if` statements that way, which makes the script end early. v1.1 .bat files use Windows (CRLF) endings and avoid fragile constructs.
3. **A private `venv` that was half-built.** If the first pip install failed (network, or no wheel for a brand-new Python), the venv still existed on the next run, so the install step was skipped and the app crashed on import. v1.1 uses *your* Python and its existing packages. No venv.
4. **Errors vanishing with the window.** The launcher only paused on some failures. v1.1 always pauses on any non-zero exit and tells you where the log is.

### Changed
- No more virtual environment: uses the Python you already have and only installs packages that are truly missing (checks first, asks first, falls back to `--user` installs).
- Four small files instead of one big one (`aac_common`, `aac_diag`, `aac_core`, `aac_gui`, `aac_tag`) so a failure points at one place.
- 3MF files are now written by a built-in writer: real per-part colours, no `lxml` / `networkx` needed (those removed from requirements).
- Logs, settings and output go to a writable folder automatically (falls back to `%LOCALAPPDATA%\AACTagStudio` if the program is in a protected folder).

### Added
- **Logging**: `logs/aac_tag.log` (rotating, 3 backups) records every start, icon lookup, export, warning and crash with a full traceback. `logs/launcher.log` records which Python was used and the exit code.
- **Issue monitor**: warnings and errors are counted live; the button in the bottom-right turns amber ("⚠ 2 issues · Log"). Click it to read the log, copy it, open the folder or run the self-test.
- **Diagnostics**: `Run_Diagnostics.bat` (or `--diagnose`) checks Python version, tkinter, every package + version, folder permissions, internet, and builds a test tag. Result saved to `logs/diagnostics.txt`.
- Crash handlers: errors in the window, in background threads, and at start-up are logged and shown in a message box instead of closing silently.

### Bugs found in the 1.0 code review and fixed
| # | Bug | Effect | Fix |
|---|-----|--------|-----|
| 1 | "Next icon" choice was one global number | Generate used the same icon variant for every word | Variant is now stored per word |
| 2 | Settings variable types came from the saved JSON | A hand-edited or old `settings.json` (e.g. `40` instead of `40.0`) could crash the window | Types come from the field defaults |
| 3 | Offline: every slider move retried the online lookup | Preview froze up to 10 s per change | Failed lookups are remembered for 90 s |
| 4 | Icon re-traced from the image on every slider move | Slow preview | Traced shapes are cached |
| 5 | One failing word aborted the whole batch | Lost work | Each word is isolated; failures are listed at the end |
| 6 | Custom image deleted later | Silent fallback | Warning logged |
| 7 | Fonts only searched in `C:\Windows\Fonts` | Per-user installed fonts missed | Also searches the per-user fonts folder |
| 8 | Missing `mapbox-earcut` gave a vague error | Hard to diagnose | Clear message in log + diagnostics |
| 9 | Empty/blank icon image crashed or produced nothing | Confusing result | Detected, warned, falls back to word only |
| 10 | Non-watertight meshes passed silently | Possible slicer errors | Warned in log |

### Known limitations (not bugs, but good to know)
- Online icons need internet and are traced from pictures, so very detailed pictograms may lose fine detail. Use *Icon boldness* and *Use my image...*.
- ARASAAC pictograms are CC BY-NC-SA (personal / non-commercial; credit ARASAAC).
- The 3MF/STL files could not be test-printed in my environment; open them in your slicer and check the first one.
- This version was syntax-checked and its dependency-free parts (logging, diagnostics, 3MF writer) were run, but the geometry and window code could not be run in my sandbox (no internet to install shapely/trimesh/customtkinter). Please run Run_Diagnostics.bat first.

## 1.0.0
- First release: type a word, auto icon + text, flat tag, STL/3MF export, modern window, options.


## 3.4.0-exp — PDF Studio workspace
- Added a dedicated PDF Studio tab with its own editable word/card list.
- Added print-page preview for the first Letter page, with layout, category-colour, and label options.
- Added a direct Save printable PDF action using the existing multi-page PECS PDF exporter.
- Added a shortcut to the Words tab for choosing or changing picture overrides.
- The on-screen preview is a layout mock-up; the exported PDF uses the app's existing icon lookup and print renderer.

## v3.5.0 — Integrated PDF Studio Preview
- PDF Studio now takes over the main preview canvas instead of opening a separate preview window.
- Returning to another workspace restores the existing 3D tag preview.
- Updated PDF page preview to use locally available AAC icons and match the PDF exporter's Letter-page margins, card spacing, card sizing, label setting, category colors, and multi-page count.
