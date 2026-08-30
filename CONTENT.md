# Content guidelines

What every written surface in this repository and in `tradefloor-docs` has to
meet. `PRODUCT.md` holds the register and the banned constructions;
`RELEASING.md` holds the order things happen in at a release. This holds the
budgets and the per-surface rules, and it is the document to work from while
writing.

Two tools check the mechanical half:

```
python tools/prose/prose.py     # the house style, over this repo's prose
python tools/release/check.py   # every budget below, plus release state
```

Neither can see register, so read a clean run as the mechanical half
passing and nothing more.

## Budgets

Measured across the twelve changelog sections written before 0.6.0, and set
where the good ones already sat rather than where an ideal would be.

| surface | budget | enforced by |
|---|---|---|
| release note, above the marker | **250 words** | `tests/test_prose.py` |
| changelog detail, below the marker | none | |
| line width, all markdown and Python | 79 columns | convention |
| commit subject | 60 characters | convention |
| commit body line | 72 columns | convention |
| PR title | 60 characters | convention |
| site page description | 120 to 165 characters | `build.py` |
| heading, any surface | 60 characters, noun phrase | `prose.py` |

**Why 250 for a release note.** It is what `release.yml` publishes to GitHub
and what the release-notes page renders. The median of the twelve sections
before 0.6.0 is 139 words. 0.4.0 (233) and 0.2.0 (240) both moved the default
preset, the largest kind of change this project makes, and explained
themselves inside the budget. 0.6.0 reached 1,257, which is an unreadable
release page.

The budget binds the newest section only. The ones below it were published
under their tags, and rewriting a release note afterwards edits a record
somebody may have read.

**What goes above the marker:** what changed, what breaks, what to pin, what
got worse. **What goes below:** how it was measured, why it was done that
way, and what nearly went wrong. Nothing is deleted to meet the budget; it
moves.

## Per surface

### CHANGELOG.md

Write prose in place of a bulleted catalogue, newest first, under a
`## X.Y.Z` heading, with ASCII punctuation only, which
`tests/test_brand_commitments.py` enforces.

State a regression in the same breath as the improvement it paid for. Every
section that made something worse says so above the marker, and that is the
convention worth keeping most.

### README.md

It is the PyPI project page, so every link must be absolute;
`tests/test_readme_links.py` fails on a relative one.

**It names the default preset twice**, in prose, and neither line is a
version location. `tools/release/check.py` checks both against the build.
A release that moves the default has to move these.

### Example notebooks and scripts

- A notebook's committed output is part of the content. A reader on GitHub
  sees it without running anything, so it should show a real run.
- **A recorded fixture is content with an expiry.** Anything keyed to the
  market, such as a replay keyed to the exact text an agent was sent, dies
  when the default preset moves and has to be re-recorded.
- A notebook that needs a key should run without one and say what it did
  instead, rather than failing closed.
- Prose in a notebook meets the same register as everywhere else. Headings
  are noun phrases, so "Live calls and replay" rather than "Replay, and why
  you do not need an API key".

### Docstrings and code comments

**Out of scope, deliberately.** The rules were written for prose a reader
meets, not for a comment beside the line it explains. `prose.py` does not
read them and no budget applies.

One convention does: where a comment carries a measured number, it carries
the value it replaced too. Those comments are the only record of how a figure
moved across eras, and they have caught more than one stale claim.

### The documentation site

Lives in `tradefloor-docs`. Page copy is
`tools/docs/learn/handoff/<Page>.dc.html`; `docs/*.html` is built output and
is never hand-edited. Site-wide rules that no design file carries live in
`build.py`.

Nothing on a page wraps. Headings, labels and table cells are checked at
1180, 760 and 390 pixels by `wraps.cjs`, and a wrapped heading fails the
check.

## Checking your own work

```
python tools/prose/prose.py                 # this repository's prose
python tools/prose/prose.py path [path ...] # one file, or the site's pages
python tools/release/check.py               # budgets and release state
```

`prose.py` reports file, line and the text it objected to, so a finding
names the thing to change. A passage that has to quote a banned
construction in order to ban it is wrapped in `<!-- prose: off -->` and
`<!-- prose: on -->`, which is why the voice section of `PRODUCT.md` reads
clean.
