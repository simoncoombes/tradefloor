# The learning-path rebuild: what was found, fixed, and left

A record of everything the rebuild turned up, so that none of it has to be
rediscovered. Three sections: defects found and fixed, decisions taken that
someone else might have taken differently, and work deliberately left.

Every claim here is checkable. `python tools/docs/learn/build.py` builds the
site, `node tools/docs/learn/verify.cjs docs` loads all 25 pages in a
real browser, and `--responsive` adds four viewport widths.

---

## 1. Defects found in the handoff, and fixed

### 1.1 Two links to pages that were deleted in review

`The nine factors` ended with a prev/next pair pointing at `Internals.dc.html`
("Under the hood") and `Atlas.dc.html`. Both were cut in the same review that
added the page. Shipped as-is, two links on that page would have 404ed.

The cause is structural rather than careless: each of the 25 pages carried its
own copy of the prev/next pair, so the ordering lived in 25 places and one of
them was left behind. **Fixed** by generating the prev/next pair, the door
menus and the "All pages" index from the site's page order in `shell.py`, and
stripping each page's copy in `prerender.cjs`. There is now one list of pages,
in `build.py`, and a link to a page that does not exist fails the build.

### 1.2 Release notes was reachable from nothing

No page linked to it and it was in no menu. **Fixed** by the same change: it
sits in the Reference door and appears in every menu and index.

### 1.3 The four late pages had no navigation

`The nine factors`, `Checkpoints and forking`, `Real companies from EDGAR` and
`Release notes` shipped with a lighter header — no door menus, no search — and
were linked only by their own prev/next chain. A reader who arrived on one had
no way to reach the other 21 except the back button. **Fixed**: all 25 pages
now carry the same generated shell.

### 1.4 The door menus and the door index disagreed

The masthead menus filed `The MCP server` under **API**. The "All pages" index
filed it under **API** too, but the handoff's own door table and its front-door
copy — "three ways to drive it: a model over MCP, a policy training, an
algorithm being scored" — put it under **Use it**.

**Resolved as Use it**, on the handoff's reasoning rather than its markup.
Reversing it is a one-line change in `DOORS` in `build.py`. Flagged here
because it is a judgement, not a bug fix.

### 1.5 The print stylesheet does not exist

The handoff specifies it precisely — `0.5cm` page margin, `break-inside: avoid`
on figures and tables, `print-color-adjust: exact`, `backdrop-filter: none` —
and **not one of the 25 design files contains an `@media print` block**. The
spec describes a stylesheet nobody wrote.

**Fixed**: written to the spec in `PRINT_CSS`, plus the two things the spec
does not mention and printing needs — the sticky masthead unstuck, and the
search and theme controls dropped, because neither works on paper.

### 1.6 A dark-system reader got the light site

The dark palette hangs off `html[data-theme="dark"]`, and that attribute is
only ever set by the theme toggle. The handoff says the theme "falls back to
`prefers-color-scheme` on first visit", and in the prototype a script did that
after the first paint. So the site was light for every reader who had never
touched the toggle, whatever their system said.

**Fixed**: `system_dark()` restates the dark tokens under
`@media (prefers-color-scheme: dark)`, guarded by `:not([data-theme="light"])`
so an explicit choice still wins in both directions. The tokens are lifted from
the rule that already exists, so the two palettes cannot drift.

### 1.7 Every page wrote a theme the reader never chose

Each page's `componentDidMount` read the system preference and then wrote it to
`localStorage`. That turns "follow my system" into a stored choice on first
visit, so a reader who later switched their system to dark stayed on light
forever. **Fixed**: the shell owns the theme and stores only an explicit
choice; the per-page `applyTheme` is neutralised at build time.

### 1.8 The theme-swapped images could not follow the theme

The mark and the Python and Rust logos ship as light/dark pairs whose
visibility was bound to a `dark` flag inside each page's component. Two
consequences: the wrong image is on screen until the script runs, and — once
the shell owns the toggle — the flag never hears about a change, so the logos
stop agreeing with the page around them. **Fixed**: `themeImages()` in
`prerender.cjs` converts the pairs to `pt-light`/`pt-dark` classes, and two
stylesheet rules do the work before the first paint.

### 1.9 A text reveal ignored `prefers-reduced-motion`

The front door's realism verdict is revealed a word at a time on a 34ms timer.
The same page's three other players check `prefers-reduced-motion` and stay
still; this one, written later, does not. A reader who has asked the platform
to stop moving things gets text that types itself anyway.

**Fixed** through `SCRIPT_FIXES` in `build.py`, which asserts on its anchor —
a redrawn design that no longer contains the code being patched fails the build
rather than quietly dropping the fix.

### 1.10 Responsive behaviour below 620px was wrong

The handoff lists this as unverified: "the breakpoints are specified and the
rules are in the files, but they have not been checked on a physical device."
Checked now, at 360, 414, 620 and 900px. **Every page scrolled sideways at
360px**, for three separate reasons:

- Each door's dropdown panel is positioned against its own `<details>`, so the
  panels belonging to the menus on the right of the bar started far enough
  across that a 14rem panel ran past the screen — and it counted toward page
  width even closed.
- The control rows above the charts are flex rows that never wrap. A label, a
  number and a slider is wider than a phone.
- A five-column truth table on the front door had no scroll box, so the page
  scrolled instead of the table.

**Fixed** in `NARROW_CSS` and `wrapTables()`. All 25 pages are clean at all
four widths. One caution: wrapping is applied to flex *rows* only, selected by
their inline `flex-direction`, because telling a column container to wrap
starts a second column and widens the very thing it was meant to narrow.

### 1.11 `pt-data.js` was a drift surface

The handoff named this itself: "if the goldens change, the charts silently
lie". **Fixed** in `data.py`, which reads `rust/goldens/*.json`,
`docs/envelope.json` and `examples/data/covid-2020-2021.json` at build time.

`python tools/docs/learn/data.py --check` diffs the generated data against the
vendored snapshot; it reproduces it value for value. Establishing that turned
up one thing worth knowing: `Number.prototype.toFixed` rounds half away from
zero and Python's `round` rounds half to even, which disagree on exactly three
of the five hundred S&P closes.

### 1.12 The search index found pages, not passages

Also flagged in the handoff. It was a hand-written keyword list per page.
**Fixed**: `write_search_index()` builds it from the rendered text, so the
snippet under a result is a sentence the reader will actually meet, and the
index cannot fall behind the prose because it is made from it.

---

## 2. Defects found in the port itself, and fixed

These are faults in the runtime written for this rebuild, not in the handoff.
They are recorded because each one was invisible on the page and was caught
only by `verify.cjs`, which is the argument for keeping that harness.

### 2.1 SVG elements were built in the wrong namespace

`document.createElement('polyline')` produces an `HTMLUnknownElement`. It
accepts every attribute, reports no error and draws nothing. Any chart that was
rebuilt rather than patched would simply have vanished — on a site whose
premise is that every page carries one real picture. **Fixed**: the namespace
is threaded through `build()` and `patch()`.

### 2.2 Tables lost most of their rows on the first frame

A browser parsing `<table><tr>` inserts a `<tbody>`; the templates do not write
one. So the built tree and the parsed tree differed, and the diff tried to
replace a `tbody` full of rows with a single row. **Fixed** by inserting the
implicit `tbody` at parse time, so both back ends and the browser agree.

### 2.3 The HTML parser destroyed the client-side template

The template was shipped in a `<template>` element. An HTML parser applies the
table content model to anything it parses, and `<sc-for>` is not in it — so a
loop written inside a table was foster-parented out of the table and its rows
left behind ungrouped. Three pages' tables were mangled. **Fixed**: the
template ships in a raw-text `<script type="text/x-pt-template">`, whose
contents are never parsed as markup. The build fails if a template contains a
closing script tag.

### 2.4 The static page and the live page disagreed about motion

The prerenderer assumed animations were running, so the still frame the build
emitted was a frame mid-animation. A page served without JavaScript cannot
animate, so the honest static render is the still one. **Fixed**: the
prerenderer's `matchMedia` answers `true` for `prefers-reduced-motion`. A
consequence worth having: the built page carries the *complete* text of the
front door's verdict, so a crawler and a reader without JavaScript get all of
it, and the reveal is what the live page adds.

---

## 3. Open — a decision, not a defect

### 3.1 The old site is still the published one — CLOSED

`docs/` now holds the learning path. `tools/docs/build_site.py`, which
produced the pages that used to live there, refuses to run without a flag
and refuses to write to `docs/` at all — it is kept rather than deleted
because it is the record of how the previous site was made, and
`design-bundle.html` is still the only copy of that design.

Every URL the old site published still answers. Twelve redirect to the page
that replaced them; four — `atlas`, `internals`, `interrogate`, `wasm` —
were cut in the design review, say so, and send the reader to the front
door. A page that is neither built nor redirected fails the build, so one
cannot be dropped by forgetting it.

The site also gained what the old one had and this one lacked: JSON-LD on
every page, a social card, a web manifest, `theme-color`, and its own icons
— traced from the design's mark, replacing the rust bar-chart glyph of the
previous identity. The front door's canonical is the directory rather than
`index.html`, so the two forms are not indexed as separate pages.

**One thing to know about the icons.** The mark ships hard-edged: two alpha
values and nothing between them. `make_icons.py` blurs it before tracing,
which is what turns a staircase back into an edge. If the mark is ever
redrawn at a decent resolution, that step can go.

### 3.2 Nine components carried their own list of the pages — CLOSED

Eight built the "All pages" section from a list of page names and links held
in the component. The builder generates that section, so those were dead.

The ninth was not dead, and finding it is the reason this was worth doing:
the **front door's "Start here" cards** are built from the same kind of
hardcoded list, and it was **listing twenty-one pages of twenty-five** and
filing the MCP page under a different door from the one the handoff's own
table gives it. The four pages added after the design review were missing
from the front door as well as from the menus.

All nine now read the list from `props.doors`, which `build.py` derives from
`DOORS`. A page added there appears in the front door's cards, the menus, the
footer index, the prev/next chain and the search index, with nothing to keep
in step by hand.

### 3.3 The prose figures are inventoried and sorted, not measured

`python tools/docs/learn/figures.py` finds 52 figures stated in prose across
the 25 pages. Twenty-six repeat a value that is already measured — by
`tools/remeasure/inventory.json`, or by `docs/envelope.json`, which the
package regenerates on every release and which carries each statistic's
measured value and both edges of its band. None contradict one.

The remaining 26 are written to `figures-todo.json`, sorted by what kind of
work each needs:

| kind | n | what it needs |
|---|---|---|
| `measured` | 14 | a measurement group and key in `tools/remeasure` |
| `unsorted` | 6 | reading, then one of the other three |
| `contract` | 3 | a test, not a measurement — "passing 5.2 raises" |
| `example` | 3 | nothing; a value chosen to illustrate, not measured |

Assigning a measurement to a claim is a judgement about which computation
reproduces it, which is why it stops here. Two are worth reading first:

- **`edgar.html`** quotes a sector mix — 27% financial services, 17%, 13%,
  30% — as "measured on the live SEC for CY2025". Nothing re-measures it,
  and unlike the rest it depends on an external service that moves.
- **`realism-envelope.html`** quotes the 504-day panel: annualised
  volatility at 33.89 against a band ending at 34.0. That is not the
  certified 252-day envelope, so `docs/envelope.json` does not cover it —
  the 504-day panel and its re-derived bands have no published artifact at
  all, and the page's argument for stopping the horizon at one year rests
  on them.

---

## 4. Deliberately left

### 4.1 Layout is still inline-styled — CLOSED

Done in two halves, both checked by screenshotting all 25 pages at three
viewports in both themes and comparing pixel by pixel
(`tools/docs/learn/shots.cjs`). **75 of 75 views identical, for each half.**

The shell — masthead, search overlay, door index, prev/next, repeated on
every page — was rewritten by hand against the design, into `SHELL_CSS`.
Every declaration is the design's, written once.

The page templates were done mechanically, because rewriting the 3,729 style
attributes in the design files by hand is a large chance to change one of
them by accident. `prerender.cjs` counts every literal style across the
whole site, weights the ones inside loops by eight — one attribute in a
template is many in a rendered page — and lifts anything at or above a
weight of six into a class. 191 classes now cover 1,006 attributes.

The rules are written `#pt-root .pt-sN`, not `.pt-sN`. The declarations
being moved were inline, and an inline style outranks every selector that is
not `!important`; the id prefix preserves that ranking, so a rule that used
to win still wins. The blocks that are *meant* to override — print, theme,
the narrow-screen rules — say `!important`, which also fixed a print
stylesheet that would otherwise have lost to the shell's new classes.

What is left inline is a style used once or twice, which is where a class
costs more than it saves.

### 4.2 Page weight

A page carries its markup twice: once prerendered, once as the template the
runtime re-renders from. The prerendered copy has every loop expanded, so
the template is about a seventh of the bytes.

Extracting the styles took the site's HTML from 1,679KB to 1,062KB for 27KB
more CSS. Typical page: 48KB raw, 8KB gzipped. The front door is the outlier
at 320KB raw, 37KB gzipped, most of it inline SVG for four charts.

First load costs the shared CSS, runtime and chart data. The search index —
the largest single file, because it holds the text of all 25 pages — is
fetched only when someone opens search.

### 4.3 `measurements/real-panel.json` and `seed-sd-504.json` are not read

The handoff's provenance table lists them as the source for the realism panel.
`docs/envelope.json` already carries each statistic's measured value, its band
and the verdict, and it is what `pretium.envelope` publishes — so reading the
measurement files separately would be a second copy that could disagree with
the first. The measurement files remain the source *of* envelope.json.

### 4.4 Eleven pages have an h3 before their first h2

The design uses `h3` for card titles, and on those pages the cards come
before the first `h2` section heading — so the outline skips a level. It is
a best-practice miss rather than a failure: no guideline forbids it and
search engines parse it, but a screen reader's heading list reads oddly.

Left alone deliberately. Promoting those headings changes their size, and
the fidelity of these pages is established by comparing screenshots — the
one thing worth not spending that on is a heading level. Fixing it properly
means deciding whether a card title is a section heading at all, which is a
question for the design rather than the build.

### 4.5 The search hint says CTRL K before the script runs

`⌘K` versus `Ctrl+K` depends on the reader's platform, which static HTML does
not know. The built page says `CTRL K` and the shell corrects it on load. The
alternative is a control that says nothing until JavaScript arrives.
