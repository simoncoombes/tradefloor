# Content guidelines

What every written surface in this repository and in `tradefloor-docs` has to
meet: the register, the banned constructions, the budgets and the per-surface
rules. `RELEASING.md` holds the order things happen in at a release. This is
the document to work from while writing.

The register below moved here from the product brief when that document
left the repository, because it is the half of it that still binds.

Two tools check the mechanical half:

```
python tools/prose/prose.py     # the house style, over this repo's prose
python tools/release/check.py   # every budget below, plus release state
```

Neither can see register, so read a clean run as the mechanical half
passing and nothing more.

## Register

Name: `tradefloor`, lowercase. The former name, `pretium`, was Latin for
*price*; the current one carries no such derivation.

<!-- prose: off -->
Voice: reference documentation. Flat, declarative, unmemorable. The reader is
scanning for a fact, not being persuaded. This binds all prose in this
repository and in `tradefloor-docs`: documentation, code comments, commit
messages, pull request titles and bodies, UI copy, labels and error text.

Rules, set by the plan owner:

- Headings and labels are noun phrases. No commas, no trailing clauses, no
  verbs. "Excluded tables", not "What was left alone, and why".
- Every sentence has a subject and a finite verb. No verbless fragments.
- Never tell the reader that something is significant. Delete "and that is the
  point", "which is why this matters", "that is the whole idea".
- Do not define things by negation. No "X is not Y", "not X but Y", "never
  wrong, only incomplete". Say what it is.
- No three-sentence assert, negate, resolve sequences. If three consecutive
  sentences are each under ten words, merge them.
- No sentence under six words may end a paragraph or a section.
- Abstract subjects take plain verbs. "The check failed on 3 of 120 seeds", not
  "the identity held through a crisis".
- No em-dash asides, no rhetorical questions, no single-sentence paragraphs for
  emphasis.
- Prefer the specific number, filename, or field name over a characterisation
  of it.
- ASCII punctuation only. No em dashes, en dashes, typographic minus signs or
  arrows.

When a sentence could open a blog post, rewrite it.

`tools/prose/prose.py` checks the mechanical rules and reports file, line and
text. It is canonical in this repository and `tradefloor-docs` vendors it, so
one copy of the rules checks both trees. It cannot see register, so a clean
run is not a passing grade.

This section is the register: what the writing sounds like. `CONTENT.md` is
the other half, and holds the budgets and the per-surface rules, including
how long a release note may run and which surfaces name the default preset in
prose.
<!-- prose: on -->

Dual-licensed MIT OR Apache-2.0, at the user's option, which is the
Rust-ecosystem norm and what `pyproject.toml` and `rust/Cargo.toml` both
declare. The repository must not reference the commercial product the engine
was ported from.

**The visual identity is deliberately decoupled from the subject matter.** A
first attempt themed the documentation as trading-desk stationery, deriving
its world from markets. The plan owner rejected it: theming the library to
its domain reads as a game artifact and couples the library's identity to the
product it was extracted from. The standing commitment is the contemporary
enterprise documentation standard, executed straight, with the Anthropic and
Mintlify class of developer documentation as the craft bar. Neutral ground,
one accent, no costume, no subject-derived metaphor. Light and dark are both
supported.

## Budgets

Measured across the twelve changelog sections written before 0.6.0, and set
where the good ones already sat rather than where an ideal would be.

| surface | budget | enforced by |
|---|---|---|
| release note, above the marker | **250 words** | `tests/test_prose.py` |
| changelog detail, below the marker | **750 words** | `tests/test_prose.py` |
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

**Why 750 for the detail.** It is three times the note, which leaves room
for one measured paragraph per change in a five-change release, and the
0.6.1 detail (375 words) sits inside it. The detail had no budget until
0.6.2, whose first cut carried the five pull-request bodies verbatim and
reached 2,387 words under a 223-word note; the 0.6.0 detail is 1,259 words
for the same reason. The pull request is the record of how a change was
measured, and the detail carries the result, the number behind it and the
pull request's name.

Both budgets bind the newest section only. The ones below it were published
under their tags, and rewriting a release note afterwards edits a record
somebody may have read.

**What goes above the marker:** what changed, what breaks, what to pin, what
got worse. **What goes below:** how it was measured, why it was done that
way, and what nearly went wrong. What the note cannot carry moves below the
marker, and what the detail cannot carry stays in the pull request.

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

### Commit messages and pull requests

The register applies here in full, and this is the surface it is broken on
most. A title is a heading, so it is a noun phrase or a plain statement of
what changed, never a clever one. "A ban nobody checked, enforced" was a real
title and is the shape to avoid.

- **Titles say what changed.** No oblique headlines, no wordplay, no
  colon-and-flourish.
- **Commit messages stay abstract**, because both repositories are public.
  Say what changed and why at the level a reader outside the project needs.
  Leave out finding counts, tool and check names, internal process narrative,
  who asked for what, and the shape of the session that produced the work.
  This is the one place the "prefer the specific number" rule is reversed.
- **A pull request body takes the specifics** the commit message left out:
  the counts, the measured values, the checks that ran and what they said.
  It has a reviewer, and a reviewer needs the evidence.
- No definition by negation in either. "Point the runner at the current
  names", not "the runner was not using the right names".

`prose.py` cannot read a commit message, so this surface rests on the
author rather than on a check.

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
`<!-- prose: on -->`, which is why the register section above reads clean.
