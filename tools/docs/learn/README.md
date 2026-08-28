# The learning-path site

This builds `docs/learn/`: twenty-five pages that take a reader from "what is
a limit order book" to the API reference.

**It is not the published site.** `docs/*.html` still is, and that is built by
`tools/docs/build_site.py` from `tools/docs/design-bundle.html`. The two
pipelines are independent and neither reads the other's files. Flipping the
published site over to this one is an open decision — see §3.1 of
[ISSUES.md](ISSUES.md).

## Building it

```
python tools/docs/learn/build.py            # write docs/learn/
python tools/docs/learn/build.py --check    # fail if docs/learn/ is stale
node   tools/docs/learn/verify.cjs docs/learn
node   tools/docs/learn/verify.cjs docs/learn --responsive
```

`build.py` needs only Python 3.9+ and `node`. `verify.cjs` needs Chrome; set
`CHROME` if it is not in the usual place.

## How it fits together

```
handoff/*.dc.html ──┐
                    ├──> prerender.cjs ──> static HTML per page ──┐
site/learn-runtime.js ┘         │                                 ├──> build.py ──> docs/learn/
                                └──> the page's template ─────────┤
rust/goldens/*.json ────> data.py ──> pt-data.js ─────────────────┤
shell.py ──> masthead, search, door index, prev/next ─────────────┘
```

**`handoff/`** is the design handoff, vendored unchanged. Each `.dc.html` is a
template in a small declarative dialect — dotted-path interpolation, one loop
form, one conditional, three event attributes — plus a component class holding
the page's data and interaction. The copy in them is final.

**`site/learn-runtime.js`** implements that dialect. One parser feeds two back
ends: an HTML string at build time, and a DOM diff in the browser. That is why
25 pages of final copy were not transcribed into another framework's syntax —
the templates stay the source, and what a crawler is served and what a reader
ends up with are produced from the same tree.

**`prerender.cjs`** evaluates each component in a `vm` with just enough of a
browser to get through `render()`, walks the template, and returns static
markup. It also lifts the shell out of each page, converts the theme-swapped
image pairs to CSS classes, and gives every table a scroll box.

**`shell.py`** generates the masthead, the search overlay, the "All pages"
index and the prev/next pair from the site's page order. Those are *not* taken
from the design files, because the 25 copies in them disagree — see §1.1–1.4 of
ISSUES.md.

**`data.py`** generates `pt-data.js` from `rust/goldens/*.json`,
`docs/envelope.json` and `examples/data/`. `--check` diffs it against the
vendored snapshot.

**`verify.cjs`** loads every page in headless Chrome and fails on a thrown
exception, a console error, a failed request, an unresolved binding, or any
difference between the built markup and the mounted markup. It found five real
faults during the rebuild and is the reason to keep it.

## Changing something

| To change | Edit |
|---|---|
| a page's copy or markup | `handoff/<Page>.dc.html` |
| which door a page is in, or the reading order | `DOORS` in `build.py` |
| the masthead, footer or search markup | `shell.py` |
| a colour, type scale or breakpoint | the `<style>` block in the design files; it is merged across pages |
| a rule that no design file carries (print, narrow, theme) | the CSS constants in `build.py` |
| a correction to a component's behaviour | `SCRIPT_FIXES` in `build.py` |
| what the charts plot | nothing here — change the golden |

Every seam that patches a design asserts. A redrawn page that no longer
contains the text being replaced fails the build instead of silently dropping
the fix, which is the same discipline `build_site.py` uses on prose and for the
same reason.

## Adding a page

Drop `<Name>.dc.html` in `handoff/`, add `("<Name>", "<slug>")` to the right
door in `DOORS`, and rebuild. It joins the menus, the door index, the prev/next
chain and the search index automatically.
