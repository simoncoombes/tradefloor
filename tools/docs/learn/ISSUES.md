# The learning-path rebuild: what was found, fixed, and left

A record of everything the rebuild turned up, so that none of it has to be
rediscovered. Three sections: defects found and fixed, decisions taken that
someone else might have taken differently, and work deliberately left.

Every claim here is checkable. `python tools/docs/learn/build.py` builds the
site, `node tools/docs/learn/verify.cjs docs/learn` loads all 25 pages in a
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

### 3.1 The old site is still the published one

This builds to `docs/learn/`. `docs/*.html` — the 25-page site generated by
`tools/docs/build_site.py` from `design-bundle.html` — is untouched and is
still what GitHub Pages serves. Nothing about the relaunch has been published.

The flip is deliberately a separate, reviewable step, and it is a decision
about content rather than machinery: the new site is a different set of pages
saying different things, not a restyling of the old one. What it needs:

- a redirect from each old URL to its nearest new one, or a decision to let
  them 404;
- `llms.txt`, `llms-full.txt`, `sitemap.xml` and `robots.txt`, which
  `build_site.py` generates today and `build.py` does not;
- the analytics snippet (`G-1LBM3239ZF`), which the new pages do not carry;
- README and repository links repointed.

### 3.2 Eight components still carry a dead door table

`index`, `metrics`, `conventions`, `schemas`, `presets`, `core-types`,
`evaluate` and `parameters` build their "All pages" section from a list of page
names and links held in the component. That section is generated now, so the
list is dead — but it is dead data that still asserts which pages exist, and
dead data that disagrees with the site is how a stale menu finds its way back.

Its links are rewritten so nothing can 404, and the build names the pages every
time it runs. Deleting it means editing the vendored design sources, which is a
change to the handoff rather than to the build, so it is left for a decision
about whether those files stay a pristine reference.

### 3.3 The prose figures are inventoried, not measured

`python tools/docs/learn/figures.py` finds 52 figures stated in prose across
the 25 pages. Twenty repeat a value `tools/remeasure` already measures. None
contradict one. The remaining 32 are written to `figures-todo.json` with the
page, the sentence and the value, needing a `group` and a `key`.

Assigning a measurement to a claim is a judgement about which computation
reproduces it, which is why it stops here. The one worth reading first is on
`edgar.html`: a sector mix quoted as "measured on the live SEC for CY2025",
with nothing that re-measures it.

---

## 4. Deliberately left

### 4.1 Layout is still inline-styled

The handoff asks for the token set to become CSS custom properties, and it has:
every colour on every page is a `var(--token)`, and the tokens live in one
`learn.css`. It also asks for the inline styles to become classes. They have
not, beyond the handful the fixes above needed.

The reason is fidelity. The copy and the markup in those 25 files are final,
and rewriting the 3,729 style attributes in those files by hand is a large
opportunity to change one of them by accident. The inline styles already reference the tokens, so
they theme and print correctly; what is lost is page weight and the ability to
restyle without a rebuild. Worth doing as its own pass, against `verify.cjs`,
which will fail on any element that moves.

### 4.2 Page weight

A page carries its markup twice: once prerendered, once as the template the
runtime re-renders from. That is 13% of total page bytes — less than it sounds,
because the prerendered copy has every loop expanded. Typical page: 55KB raw,
9KB gzipped. The front door is the outlier at 326KB raw, 39KB gzipped, most of
it inline SVG for four charts.

First load now costs 28KB gzipped of shared CSS, runtime and chart data. The
search index (32KB gzipped) is fetched only when someone opens search.

### 4.3 `measurements/real-panel.json` and `seed-sd-504.json` are not read

The handoff's provenance table lists them as the source for the realism panel.
`docs/envelope.json` already carries each statistic's measured value, its band
and the verdict, and it is what `pretium.envelope` publishes — so reading the
measurement files separately would be a second copy that could disagree with
the first. The measurement files remain the source *of* envelope.json.

### 4.4 The search hint says CTRL K before the script runs

`⌘K` versus `Ctrl+K` depends on the reader's platform, which static HTML does
not know. The built page says `CTRL K` and the shell corrects it on load. The
alternative is a control that says nothing until JavaScript arrives.
