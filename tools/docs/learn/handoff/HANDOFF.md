# Handoff: tradefloor documentation site

## Overview

A rebuild of the `tradefloor` documentation as a learning path rather than a defence
of the project. Twenty-one pages, arranged in five doors, taking a reader from
"what is a limit order book" to the API reference.

The problem it solves: the existing docs are 42,000 words, 25 pages and 3 diagrams
for a market simulator, with no picture of a market anywhere. Two product
principles ("state the limitation next to the capability", "measure rather than
assert") were applied without a counterweight, so every sentence carries a number
and a caveat. That is excellent for an expert deciding whether to trust the
library and poor for someone deciding whether to try it.

Three editorial rules drive the rebuild, and they must survive implementation:

1. **Demote the caveats without deleting them.** "Next to the capability" is
   satisfied by a callout or a collapsible on the same screen. It does not have to
   be the third sentence of every paragraph. Every limitation in the old prose is
   still present; most now sit inside a `<details>` element under a heading that
   names what the reader is about to be warned about.
2. **Cap page length.** No page exceeds ~1,900 words. The old
   `scenario-recipes.md` was 6,780. Total across all 21 pages is ~25,500 words,
   down from ~42,000.
3. **Every page carries one real picture.** Charts are built from data committed
   in the repo (see *Data provenance*), never invented.

## About the design files

The files in `design/` are **design references created in HTML**. They are
prototypes showing intended look and behaviour, not production code to lift
directly.

The task is to **recreate these designs in the target documentation environment**.
The current site is static HTML generated from Markdown in `docs/`, published to
GitHub Pages, plus a single-page app shell (`docs/index.html`). Either target is
fine; the designs assume a static build with a small amount of per-page
JavaScript. If a docs framework is introduced (Astro Starlight, VitePress,
Mintlify), use its conventions and treat these files as the visual and editorial
spec.

Each `.dc.html` file is self-contained: it opens directly in a browser. Its
markup is inline-styled, which is a constraint of the prototyping environment and
**not** a recommendation — extract the token set below into CSS custom properties
and use classes.

## Fidelity

**High-fidelity.** Final colours, typography, spacing, copy and interactions.
Every hex value, font size and piece of copy in these files is deliberate.
Recreate the UI to match. The one thing deliberately left open is the responsive
detail below 620px: the breakpoints are specified and the rules are in the files,
but they have not been checked on a physical device.

## Design tokens

Declared as CSS custom properties on `:root`, overridden under
`html[data-theme="dark"]`. Light is the default; dark is opt-in and persisted.

### Colour — light

| Token | Value | Used for |
|---|---|---|
| `--ground` | `#FAFBFA` | page background |
| `--surface` | `#F1F4F3` | code blocks, collapsibles, inset panels |
| `--card` | `#FFFFFF` | cards, tables, raised panels |
| `--line` | `#D2DBD9` | borders, dividers |
| `--line-soft` | `#E3E9E7` | table row rules, internal card dividers |
| `--ink` | `#101A18` | headings, primary text |
| `--ink-2` | `#3C4A47` | body text |
| `--ink-3` | `#5A6864` | captions, eyebrows, provenance lines |
| `--accent` | `#0E6B65` | links, active state, positive bars |
| `--accent-deep` | `#0A4F4A` | link hover, text on `--accent-soft` |
| `--accent-soft` | `#DDEEEC` | callout backgrounds, active chips |
| `--accent-line` | `#8FC2BD` | callout borders, `summary::marker` |
| `--chip-bg` | `#EDF3F2` | inline code chips |
| `--chip-line` | `#CBD9D6` | inline code chip borders |
| `--band` | `#C9DEDB` | in-band ranges on the realism chart |
| `--red` | `#8A3A3A` | negative values, out-of-band |
| `--green` | `#2F6B4A` | in-band verdicts |
| `--amber` | `#7A5C2E` | thin-margin verdicts |
| `--header-bg` | `rgba(250,251,250,0.93)` | sticky masthead, with `backdrop-filter: blur(8px)` |

### Colour — dark (`html[data-theme="dark"]`)

| Token | Value |
|---|---|
| `--ground` | `#0D1312` |
| `--surface` | `#151D1C` |
| `--card` | `#111917` |
| `--line` | `#2B3735` |
| `--line-soft` | `#202B2A` |
| `--ink` | `#E5ECEA` |
| `--ink-2` | `#B2C0BD` |
| `--ink-3` | `#8A9895` |
| `--accent` | `#5FC9BE` |
| `--accent-deep` | `#8ADDD4` |
| `--accent-soft` | `#10302E` |
| `--accent-line` | `#2E6B65` |
| `--chip-bg` | `#14201F` |
| `--chip-line` | `#2B3735` |
| `--band` | `#24413E` |
| `--red` | `#E2938E` |
| `--green` | `#7FD3A6` |
| `--amber` | `#D3AC63` |
| `--header-bg` | `rgba(13,19,18,0.92)` |

The hue is a desaturated teal, chosen to be neutral ground: it carries no
market or trading metaphor, per the standing brand commitment that the visual
identity stays decoupled from the subject matter. One accent, no costume.

### Typography

Three families, from Google Fonts:

| Role | Family | Weights | Applied to |
|---|---|---|---|
| Headings | `Source Serif 4` | 400, 600 | `h1`, `h2`, `h3`, card titles, definition terms |
| Body / UI | `Public Sans` | 400, 500, 600 | paragraphs, buttons, table headers, glosses |
| Mono | `Spline Sans Mono` | 400, 500 | code, identifiers, data readouts, eyebrows, numeric cells |

```
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&family=Public+Sans:wght@400;500;600&family=Spline+Sans+Mono:wght@400;500&display=swap">
```

Scale, in rem at a 16px root:

| Element | Size | Weight | Line height | Tracking |
|---|---|---|---|---|
| `h1` | `clamp(1.9rem, 4vw, 2.6rem)` | 600 serif | 1.15 | `-0.015em` |
| Page lede | `1.125rem` | 400 sans | 1.65 | — |
| `h2` | `1.5rem` | 600 serif | 1.15 | `-0.015em` |
| `h3` / card title | `1.0625rem` | 600 serif | 1.15 | `-0.015em` |
| Body | `1rem` | 400 sans | 1.65 | — |
| Card body, callout body | `0.875rem` | 400 sans | 1.6 | — |
| Code block | `0.8125rem` | 400 mono | 1.7 | — |
| Inline code chip | `0.8125rem` | 400 mono | — | — |
| Table cell | `0.8125rem` | 400 mono (numeric) / sans (prose) | — | — |
| Table header | `0.75rem` | 500 sans, `--ink-3` | 1.3 | — |
| Eyebrow | `0.625rem` | 500 mono, `--ink-3` | 1 | `0.07em`–`0.09em` |
| Caption, provenance | `0.6875rem` | 400 mono, `--ink-3` | 1.55 | — |

Rule for choosing sans versus mono in small labels: **mono for anything that is a
literal identifier or a data readout** (`mispricing_s`, `BEST BID`, `DAY 14`),
**sans for anything that is prose** (table headers like "Use it for", category
labels, plain-language glosses). This was a specific correction during review;
mono all-caps eyebrows had been overused as headings.

Body copy uses `text-wrap: pretty`; `h1` uses `text-wrap: balance`.

### Spacing, radius, motion

- Section rhythm: `2.8rem` top padding between major sections, `2.5rem` before
  the footer nav.
- Card padding: `1rem 1.1rem`. Code block padding: `1rem`. Collapsible summary
  padding: `0.7rem 0.9rem`.
- Grid gaps: `1.3rem` between columns, `0.7rem` between cards in a dense grid,
  `0.5rem`–`0.6rem` inside a card.
- Content column: `max-width: 1040px`, `padding: 0 1.5rem`.
- Prose measure: `max-width: 70ch` on paragraphs that sit alone. Paragraphs
  inside a grid column are unconstrained.
- Border radius: `6px` on cards, code blocks, collapsibles and tables; `5px` on
  buttons and chips; `4px` on inline code chips; `99px` on pills.
- No shadows anywhere except the search overlay and the dropdown menus
  (`0 12px 40px rgba(0,0,0,0.18)`).
- Transitions: `130ms` on tab and table swaps, `200ms` on the market player's
  frame advance. Nothing else animates.

## Structure: five doors

The front door (`Learn tradefloor`) carries a five-step spine and, at the bottom, an
index of all five doors. Every other page carries the same door index above its
prev/next pair, and a dropdown per door in the masthead.

| Door | Pages |
|---|---|
| **Start here** | Install · Core concepts · The two loops · Running a market |
| **Use it** | Agents · Scenarios · Execution cost · RL environment · The MCP server |
| **Trust it** | Realism envelope · The metrics · Principles · Citing a run |
| **Reference** | Glossary · Conventions · Schemas · Presets |
| **API** | Core types · Evaluate · Parameters |

### The front door's five steps

The spine a beginner follows, in order, on one page:

1. **What a market is** — a real order book, replayed step by step.
2. **What one run gives you** — universe, macro, seed; the five output tables.
3. **What you can ask it** — the questions a backtest cannot answer.
4. **Three ways to drive it** — a model over MCP, a policy training, an
   algorithm being scored. Plus "An agent arena" as a build-on-it pattern.
5. **Whether to believe it** — the realism envelope and the five named gaps.

## Pages

Every page follows one shell. Rather than repeat it 21 times, the shell is
specified once and per-page notes follow.

### The shell (all 21 pages)

**Masthead** — sticky, `z-index: 40`, `--header-bg` with `backdrop-filter:
blur(8px)`, `1px` bottom border in `--line`. Inner row is `max-width: 1040px`,
`padding: 0.7rem 1.5rem`, `display: flex`, `align-items: center`, `gap: 1.5rem`.
Contents, left to right:

- The tradefloor mark (`mark-tradefloor.png`, 15×22, swapped for
  `mark-tradefloor-dark.png` in dark mode) beside the wordmark in mono at
  `1.0625rem`/500, tracking `-0.01em`. Links to the front door.
- Five `<details class="ptmenu">` dropdowns, one per door, at `0.875rem`. Click
  to open. Opening one closes the others; a click outside or `Escape` closes all.
  Implemented as `<details>` so they work before JavaScript loads. The door
  containing the current page shows its summary in `--accent`; within a panel,
  the current page is `--accent` and not a link.
- A search input, `⌘K` / `Ctrl+K` focuses it. Results are a dropdown overlay.
- A theme toggle: a pill in `--surface` with a `--line` border, label "dark" or
  "light" in mono at `0.75rem`.

**Breadcrumb** — `padding: 2.5rem 0 0`, mono `0.6875rem`, tracking `0.1em`,
`--ink-3`. Reads `LEARN / <PAGE NAME>`, current page in `--accent`.

**Page head** — `h1` then a lede paragraph at `1.125rem` in `--ink-2`. The lede
says what the reader will be able to do, never what the library cannot do.

**Footer** — the all-pages door index, then a prev/next pair. Each is a
two-line link: mono `0.6875rem` `--ink-3` eyebrow ("BACK" / "NEXT") over the
page title at `0.875rem`.

**Caveat collapsibles** — `<details>` in `--surface` with a `--line` border and
`6px` radius. `summary` is `0.875rem`/500 in `--ink`, padding `0.7rem 0.9rem`,
cursor pointer, `summary::marker` in `--accent-line`. Open state adds a
`--line-soft` top border on the body. The summary **names the risk** rather than
teasing it: "Momentum can work here for a reason real markets do not supply", not
"A note on momentum". Where a caveat is severe enough to belong above the fold,
it becomes an inline callout instead: `--accent-soft` background,
`--accent-line` border, body in `--accent-deep`.

**Print** — an `@media print` block: `0.5cm` page margin, `break-inside: avoid`
on figures and tables, `print-color-adjust: exact`, `backdrop-filter: none`,
and `#dc-root { height: auto }`.

**Responsive** — three breakpoints. At `900px`, every multi-column grid inside
`main` collapses to `1fr` and the masthead nav wraps to its own row. At `620px`
and `460px`, type steps down. Not yet checked on a physical device.

### Per-page notes

Read each file for the copy, which is final. What follows is the interactive or
data-bearing element on each page — the thing a static rebuild would otherwise
lose.

| Page | Words | The picture, and its interaction |
|---|---|---|
| **Learn tradefloor** | 3,562 | (1) An order-book replay: 22 steps over a real golden ladder, driven by a range slider, with a play/pause. Each step names the operation and marks which of the four quote readouts moved and which held. (2) A 30-day market player: price line for five names, animated, looping, scrubable. (3) The realism envelope panel: fourteen statistics, each a band with the measured value plotted inside or outside it. (4) A sector-mix donut for a generated universe. |
| **Install** | 1,044 | Two install paths side by side (`pip` / `cargo`), each with the language logo. Five platform digest rows. |
| **Core concepts** | 917 | The universe/macro/seed triad, with the roster-order warning as an inline callout — it is the one thing that silently changes results. |
| **The two loops** | 791 | A two-column diagram: the research loop against the calibration loop, and where they touch. |
| **Running a market** | 1,600 | A tabbed preview of the five output tables — Bars, Truth, Macro, Fills, Book — with real rows under real column names. `130ms` fade on tab swap. Plus the rebalance-cadence bars (+37.55% / +9.79% / −27.46%). |
| **Agents** | 907 | The twelve-market tally: `pt.rank` output, twelve squares showing the 9–3 split, three readouts stating what the numbers do and do not support. |
| **Scenarios** | 883 | Three scenario shapes, each with the code that drives it and an animated chart of the resulting macro path. |
| **Execution cost** | 757 | The counterfactual: the same seed with and without your orders, and where the shortfall accrues. |
| **RL environment** | 868 | The Gymnasium loop, the observation and action shapes, and what size costs. |
| **The MCP server** | 830 | "Two places a model can sit": inside as a reader, outside as a trader, four points each, two code shapes, and the limit both share. |
| **Realism envelope** | 1,063 | The fourteen-statistic panel again, plus the five named gaps and what each forbids. |
| **The metrics** | 1,383 | Each of the fourteen statistics: what it measures, its real band, the measured value, the verdict. |
| **Principles** | 1,320 | Five principles, each with what it costs. |
| **Citing a run** | 829 | The seven manifest fields, `reproduce()` refusing on mismatch, and what to write in a paper. |
| **Glossary** | 1,964 | 43 terms with live text filtering and category tabs. Sorted alphabetically. Field names in mono, prose terms in serif. |
| **Conventions** | 976 | The conventions worth knowing before you hit them: fractional rates, contractual roster order, f64 columns. |
| **Schemas** | 1,650 | Every column of the five tables, filterable by table. |
| **Presets** | 1,078 | The twelve presets, filterable by "all twelve" / "for new work" / "reproduction only", with in-band counts at 252 and 504 days. |
| **Core types** | 1,157 | `Engine`, `Universe`, `Macro`, `Scenario`, `Checkpoint`, with signatures. |
| **Evaluate** | 964 | `evaluate`, `rank`, `Scorecard`, `StrategySpec`. |
| **Parameters** | 980 | The model parameters, grouped, with defaults. |

## Interactions & behaviour

| Behaviour | Detail |
|---|---|
| Theme toggle | Sets `data-theme="dark"` on `<html>`, persists to `localStorage` under `pt-learn-theme`, falls back to `prefers-color-scheme` on first visit. Wrapped in try/catch for private mode. Swaps three image pairs (the mark, the Python logo, the Rust logo). |
| Dropdown menus | `<details>`-based, click to toggle. One-open-at-a-time, outside click closes, `Escape` closes. Listeners removed on unmount. |
| Search | `⌘K`/`Ctrl+K` focuses. Substring match over a keyword index in `pt-search.js`. Results grouped by door, arrow keys to move, `Enter` to open, `Escape` to dismiss. **This index is hand-written page keywords, not extracted content** — it finds pages, not passages. Extract at build time instead. |
| Order-book replay | Range slider over 22 steps plus play/pause. Each step's ladder, quotes and operation come from the golden fixture. Quote readouts label themselves "moved" (in `--accent`) or "held". |
| Market player | Animates 30 days, loops, scrubable by slider. `200ms` per frame. |
| Tabbed tables and filters | Plain state swaps with a `130ms` opacity fade, since column shape changes between tabs. |
| Collapsibles | Native `<details>`. No JavaScript, so they work and print open-or-closed as authored. |

## State

Per page, all local: `dark` (boolean, persisted), plus whichever of `tab`,
`filter`, `query`, `step`, `playing`, `day` that page needs. No shared or global
state, no data fetching. Everything renders from `pt-data.js` and `pt-search.js`,
which are static.

## Data provenance — read this before implementing

Every number and chart in these pages traces to a file in the repo. Nothing is
invented. Two exceptions are marked in the UI itself with an `ILLUSTRATIVE`
badge, following the existing docs' convention: one example return value and one
strategy fingerprint.

| Source | Feeds |
|---|---|
| `rust/goldens/thirty-day-calm.json`, `thirty-day-eventful.json` | the market player, the truth/bars/macro table previews |
| `rust/goldens/orderbook.json` | the 22-step order-book replay |
| `measurements/real-panel.json`, `seed-sd-504.json` | the fourteen-statistic realism panel and bands |
| `docs/envelope.json` | the five gaps and the certified horizon |
| `examples/data/covid-2020-2021.json` | the scenario macro path |
| `README.md`, `PRODUCT.md`, `docs/*.md` | every measured figure quoted in prose |

**The one thing to fix in implementation.** These values are baked into
`design/pt-data.js` by hand — I decoded the goldens' hex-encoded IEEE-754 floats
into a JS module. That file is a drift surface: if the goldens change, the charts
silently lie. Two changes remove it:

1. Read `rust/goldens/*.json` at build time and generate the chart data, so a
   golden change either updates the chart or fails the build.
2. Add every prose figure on these pages to `tools/remeasure`'s inventory, so
   `remeasure.py` can check them the way it already checks the rebalance
   measurement.

Until (1) lands, treat `pt-data.js` as a fixture snapshot, not a source.

## Assets

In `design/`:

- `mark-tradefloor.png`, `mark-tradefloor-dark.png` — the tradefloor mark, 15×22 at 1×.
- `logo-python.png`, `logo-python-dark.png` — for the install page.
- `logo-rust.png`, `logo-rust-dark.png` — for the install page.

No icon set is used. No illustrations. Every graphic on the site is either a
chart drawn from data or a diagram built from divs and borders.

## Files

```
design/
  Learn tradefloor.dc.html          the front door, five-step spine
  Install.dc.html                Start here
  Core concepts.dc.html
  The two loops.dc.html
  Running a market.dc.html
  Agents.dc.html                 Use it
  Scenarios.dc.html
  Execution cost.dc.html
  RL environment.dc.html
  The MCP server.dc.html
  Realism envelope.dc.html       Trust it
  The metrics.dc.html
  Principles.dc.html
  Citing a run.dc.html
  Glossary.dc.html               Reference
  Conventions.dc.html
  Schemas.dc.html
  Presets.dc.html
  Core types.dc.html             API
  Evaluate.dc.html
  Parameters.dc.html
  pt-data.js                     decoded fixture data (see provenance)
  pt-search.js                   hand-written search index
  support.js                     prototyping runtime, do not port
  *.png                          logos and mark

Learn tradefloor (standalone, offline).html   the front door, fully inlined,
                                           994 KB, opens with no network
```

Open any `.dc.html` directly in a browser. `support.js` is the prototyping
runtime that renders them — it exists only so the files are viewable and should
not be ported.

## Known gaps

1. Responsive behaviour below 620px is specified and coded but unverified on a
   device.
2. `pt-search.js` is a keyword index, not extracted content.
3. `pt-data.js` is a hand-decoded snapshot rather than a build-time artefact.
4. Four more pages were added after review, scoped to what teaches use rather
   than proves the engine works: **The nine factors** (Reference — what the
   `truth` table's columns mean), **Checkpoints and forking** (Use it — branch
   a run to test two decisions from one point), **Real companies from EDGAR**
   (Use it — seed a universe from real filings), **Release notes** (Reference —
   what changed and what it means for a run you have, not a full changelog).
   They use the shell but a lighter header (mark, wordmark, "All pages" link,
   theme toggle — no dropdown menus or search box) and are linked only by their
   own prev/next chain, not yet wired into the front door's door menus or
   `pt-search.js`. Deliberately cut, on the same review, as proof-pages rather
   than use-pages: Internals/"Under the hood" (architecture credibility, not a
   workflow — its one useful fact, the four-phase tick order, is worth a
   footnote on Core concepts if wanted), Atlas and Testing your edge (a narrow
   power-user tool for interrogating whether an edge is real; worth at most one
   combined page, not two, if the target audience needs it), Running in the
   browser/wasm parity (a determinism proof, not a task a reader performs), and
   Custom models/presets-in-depth (its one practical fact, changing a
   coefficient via `ModelParams.from_preset`, fits as a callout on the existing
   Presets page rather than its own page).
