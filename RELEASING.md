# Releasing

One tag drives everything. `git push origin vX.Y.Z` starts the workflow that
builds five wheels, builds the sdist, proves they agree, publishes to PyPI
and writes the GitHub release. The steps below are the parts a tag cannot do
for you, and the checks that exist because something once went wrong.

Every item here is either automated and named so you know not to do it by
hand, or manual and ordered so the irreversible steps come last.

## Before you tag

### 1. Move the version in four places

`pt.version()` is a published fact and these must agree:

| file | what reads it |
|---|---|
| `pyproject.toml` | the wheel, the sdist, PyPI |
| `rust/Cargo.toml` | the crate, crates.io |
| `CITATION.cff` | anyone citing a result |
| `docs/reproducing-a-run.md` | the worked example that prints it |

`CITATION.cff` carries two fields, not one: `version:` and `date-released:`,
the day the version was tagged. A version without the date it shipped is half
a citation.

`date-released` is the field that goes stale silently. At 0.3.0 it still read
`2026-08-26`, which is when v0.2.0 was tagged, sitting under `version: 0.3.0`.
Nothing tests it, so it reaches a citation field pointing at the previous
release. Set it to the day you intend to tag, and check it again at the tag.

Then `cargo update -p pretium` so `Cargo.lock` follows, and rebuild
(`maturin develop --release`) so the installed package reports the new
number rather than the old one.

### 2. Write the changelog section

`CHANGELOG.md`, newest first, heading `## X.Y.Z`. The release workflow cuts
the GitHub release notes from this section, matching `## X.Y.Z` or
`## [X.Y.Z]`. If it finds nothing it does not ship empty notes: the script
calls `sys.exit("CHANGELOG.md has no section for ...")`, the step runs under
`set -euo pipefail`, and the whole `github_release` job fails before
`gh release create` runs. That job `needs: [setup, verify, publish]`, so by
the time you see the failure the wheels are already on PyPI, which is the one
step that cannot be undone. Write the section before you tag, not after.

Prose, not a bulleted catalogue. No em dashes or en dashes anywhere.
`tests/test_brand_commitments.py` enforces that on `CHANGELOG.md`.

**Lead the section, then mark where the lead ends.** The whole section is what
`release.yml` publishes as the GitHub release note and what the release-notes
page renders, and this project writes changelog sections as essays: 0.3.0 ran
to 2,370 words across eight subsections, which is the right permanent record
and an unreadable release page.

```markdown
## X.Y.Z

<what changed, what to pin, what got worse: a few hundred words>

<!-- release-note-ends -->

### the detail, and how it was measured
```

Everything above `<!-- release-note-ends -->` becomes the release note, and
the page folds the rest behind a disclosure. A section without the marker
publishes whole, which is what every version before 0.3.0 does.

### 3. Run the suite, including the slow half AND the Rust one

```
python -m pytest tests/ -q
PRETIUM_SLOW_TESTS=1 python -m pytest tests/test_examples.py -q
cd rust && cargo test --offline
```

The third line is not optional and was learned the hard way at 0.2.0. The
Python suite does not run the crate's own unit tests, so `cargo test` sat
red for a whole afternoon of engine work: a unit test still asserted the old
default preset, and the parity tests would not compile against a struct that
had gained a field. Neither is visible from pytest, and the first one only
surfaced from the packaged-crate check in the crate section below.

The second executes all eight notebooks and the research workflow end to end
(`tests/test_examples.py` globs `0*.ipynb`, which matches 00 through 06 and
09), 21 tests in all. It is skipped by default because it runs for a minute
or more, and because it needs `nbformat` and `nbclient`, which the library
does not depend on. Check the summary line rather than the exit code: the
eight tests that actually EXECUTE a notebook are the ones that need
`nbclient`, so with `nbformat` alone those eight skip, the other thirteen
report, and the step is green having executed no notebook at all. Both are
exactly why it goes stale.

The last timed run of the whole file was 96s (`--durations=15` on an idle
machine), with `09-a-pandemic-shaped-market.ipynb` alone at 40s against 21s
for the next slowest. That figure is machine-bound: the same file under load
has measured more than twice that, so read it as an order of magnitude
rather than a budget. Time it on a quiet machine or not at all.

### 4. Re-measure the published figures

```
python tools/remeasure/remeasure.py
```

This is the step the site's own status paragraph promises.
`docs/releases.html` tells every reader that published figures "are
re-measured by `tools/remeasure`, which reports every number the stated
method no longer produces, and a figure it flags is a documentation defect
until someone corrects it." Nothing in CI runs it, so if it is not run here
the guarantee is a sentence rather than a process.

**Check what it actually ran before reading it.** The report's header used to
say "Full run" whatever `--only` was passed. The 0.3.0 report on disk covered
3 of 30 groups and 54 of 308 figures in 3 seconds, said "Full run", and showed
no `MOVED` rows at all, because the groups that move were never measured. A
partial run now labels itself `PARTIAL RUN: n of N`, and `meta.groups_run` in
`figures.json` is the field that settles it.

**Run it on AWS, not here.** A 504-day 40-name measurement holds about 1.6 GB
per worker, so eight workers is roughly 13 GB, and it has taken this machine
out once mid-run. `tools/calibration/aws/user-data-remeasure.sh` runs it on a
96-vCPU box: 285 figures in 301 seconds at 64 workers, about twenty cents.
Sixty-four rather than ninety-six because `remeasure` uses a thread pool, so
the ceiling is how much of the engine releases the GIL rather than the core
count.

**The `perf` group's figures are laptop-bound.** They are marked
`machine_bound` and never fail, and a cloud run reports its own hardware. Do
not let a cloud number rewrite a published laptop one.

It writes `tools/remeasure/out/REPORT.md`. Read the **Doc edits needed**
table: every row is a published number this build does not produce. `MOVED`
after an engine change is an edit list, not a failure -- a new default preset
moves figures by design -- but the edits have to land before the next step,
because step 5 is what publishes them. `structural_fail` rows are boolean
claims that stopped being true and are worse than a moved number.

The last stored run is the record of what was current at that release. The
0.2.0 run (`tools/remeasure/out-0.2.0/REPORT.md`, commit `e3396b9`) reported
106 reproduced against 165 MOVED and 5 `structural_fail`, which is what a
release looks like when the preset moved and the prose did not follow yet.

### 4b. Re-point the inventory before believing a MOVED row

Run this whenever the documentation has been rewritten since the last
release, which at an era boundary is always.

```
python tools/remeasure/resync.py --report      # says what it would do
python tools/remeasure/resync.py --apply
```

`inventory.json` records, per published figure, the value the page states and
the line it states it on. A docs rewrite moves both and nothing re-reads them,
so the gate ends up comparing today's engine against yesterday's prose. At
0.3.0 that produced **106 MOVED rows and three structural_fail rows, and not
one of them was a documentation defect.** Fifty described content the rewrite
had deleted, several were reading the wrong column of a table the page gets
right, and the rest recorded a value the page no longer prints.

`resync` re-points a row when the measured value appears exactly once on its
page and the surrounding lines mention what the row measures, and retires a
row only when neither the value nor its subject is there. Anything else it
leaves for a human, and that residue needs reading rather than clearing.

**Three of the 0.3.0 rows were the measurement tool, not the inventory.**
`measures.py` called `separation("momentum", "mean_reversion")` where the page
prints `separation("mean_reversion", "momentum")`, which reverses every win
count, and compared momentum to random where the page compares mean-reversion
to random -- a different test. The horizon bullet measured momentum's capture
where the page says "the same mean-reversion agent". When a row disagrees,
check what the tool measures against what the page claims before editing
either.

The gate is worth reading only once it comes back clean. 0.3.0 finished at 285
figures, 199 reproduced, zero MOVED.

### 5. Rebuild the documentation site

```
python tools/docs/build_site.py
```

Build it, commit, then build again. Each page's `dateModified` comes from the
last commit touching its sources, which include `CHANGELOG.md` and the files
under `tools/docs/`, so committing those moves the date the next build writes.
The second build is the no-op that proves it settled. A build that still
dirties the tree on the third run is a bug rather than churn.

Adding a page needs three things and asserts on all of them: a markdown file
in `docs/`, an entry in `newpages.NEW_PAGES`, and a description in
`seo.DESCRIPTIONS` of 120 to 165 characters. The build fails naming the
missing one.

It reads the version from `pyproject.toml`, so the nav badge and the BibTeX
block follow automatically. Two version strings in the bundle are HISTORY
and must not move: the release-notes headings and the "measured on pretium
0.1.0" provenance line. The build matches them by surrounding markup and
leaves them alone.

If a new design bundle has landed, drop it at `tools/docs/design-bundle.html`
first. The build asserts on the release-status text it expects to find, so a
reworded bundle fails loudly rather than shipping a stale claim.

### 6. Check the README survives PyPI

```
python -m pytest tests/test_readme_links.py -q
```

`readme = "README.md"` means this file becomes the PyPI project page, which
lives at pypi.org, so a relative link like `examples/01-first-simulation.ipynb`
resolves to `pypi.org/project/pretium/examples/...` and 404s. It renders
correctly on GitHub, which is why it survived two releases. The test fails on
any relative link and on any absolute link naming a file that is not there.

### 7. Run the determinism gate on the branch

```
gh workflow run determinism.yml --ref <branch> -f targets=all
```

**Read the run you just started, not the newest one in the list.**
`gh run list --workflow=determinism.yml --branch <b> --limit 1` returns the
previous run until the new one registers, and that previous run is green on an
older commit. Take the run id from the `gh workflow run` output or from
`gh run view <id>`, and check `headSha` matches the commit you mean to tag.
Reading the wrong row is a green tick on the wrong artefact.

`targets=all` is spelled out because the dispatch default is `unverified`,
which runs only `macos-x86_64` and `windows-x86_64`. Those two are the
default because nothing else in the project touches them: `macos-arm64` is
the machine the work is done on, and every AWS calibration run builds the
crate and executes `tests/known_answer.py` before its own work, which has
covered `linux-aarch64` repeatedly and `linux-x86_64` once, on 2026-08-24.
So the default is the narrow path to the gap, not the cheap one. Money is
not the reason for it: the repository is public, so standard runners are
free, and the workflow's own note records the five-target run of 2026-08-27
(run 33028345268) at 3m35s of wall clock and about ten minutes of runner
time across all seven jobs. Ask for `all` here anyway, because the point of
a release gate is that the whole artifact set was checked in one place,
together, on the commit being shipped.

It also runs on pushes to `main` touching `rust/**`, `python/**`,
`pyproject.toml` or the workflow itself, so on a release from `main` it has
usually already run. A tag push runs all five regardless of any input: a
release must not ship on a partial gate.

## Shipping it, in order

The seven steps above run on `dev`. This is what puts them out.

1. **Push `dev`.** An AWS run clones the branch, so anything the release
   needs has to be on the remote before it is launched.
2. **Merge to `main` and push.** Pages serves from `main/docs`, so this is
   the step that publishes the site. Nothing before it is visible to a reader.
3. **Dispatch the determinism gate on `main` with `targets=all`** and wait.
   The push itself fires the gate at two targets, not five.
4. **Check the tag target is the gated commit.** `git rev-parse HEAD` against
   the run's `headSha`, compared rather than assumed.
5. **Tag and push the tag.** That is the irreversible step: a PyPI version
   number cannot be reused.
6. **Publish the crate.** Separate registry, separate command, below.
7. **Check docs.rs.** A 404 in the first minutes is the build queue, not a
   failure. Compare against an earlier version: if `0.2.0` returns 200 and the
   new one still 404s after ten minutes, the build failed and the crate page
   says why.

**Check the branch after any step that moves branches.** A `git push origin
main` run from `dev` reports `Everything up-to-date` and pushes nothing, which
reads exactly like a successful deploy. `git branch --show-current` after
every checkout or merge, and compare `git rev-parse --short main origin/main`
before believing a push.

## Tagging

```
git tag -a vX.Y.Z -m "..."
git push origin vX.Y.Z
```

That fires `release.yml`: five wheel targets, the sdist, a verify job that
runs one fixed simulation inside every wheel and compares digests, then
publish, then the GitHub release. A disagreement between targets fails the
release rather than shipping.

Publishing uses **Trusted Publishing**. No API token exists anywhere; GitHub
mints a short-lived OIDC token for the `pypi` environment and PyPI exchanges
it. There is nothing to rotate and nothing to leak.

A `workflow_dispatch` sets `publish=false` and cannot upload. Only a tag
publishes, because that is the direction a mistake is recoverable in.

## Publishing the crate

Not automated. crates.io is a separate registry with its own auth, and the
Rust crate does not have to move on the same cadence as the Python package.

```
cd rust
cargo publish --dry-run          # builds and packages, uploads nothing
cargo package --list             # what would actually ship
cargo publish
```

Before the first publish, or after adding an integration test, check what
the packaged crate does on its own:

```
cargo package
cd target/package/pretium-X.Y.Z && cargo test --offline
```

This matters more than it sounds. Sixteen of the nineteen integration tests
read the 140 MB parity corpus in `rust/goldens/`, which `exclude` deliberately
keeps out, and they panic when it is absent. What is left to run on its own is
`circuit_breaker`, `roster_mutation` and `stream_alignment`, plus the unit
tests. They are excluded **by name**, so a new test is not silently dropped:
add one that reads `goldens/` and you must add it to `exclude` too, or a
consumer running `cargo test` concludes the crate is broken.

crates.io versions are permanent and cannot be replaced, only yanked.

## After

- **Verify from the outside**, not from your working tree:
  ```
  pip install pretium                     # the wheel
  pip install --no-binary :all: pretium   # the sdist, compiled from source
  ```
  The second is the path that was broken in 0.1.0 and nobody noticed until
  the release had gone out.
- **There is no Pages deploy workflow.** `.github/workflows/` holds exactly
  two files, `determinism.yml` and `release.yml`. The site is the committed
  `docs/` tree (56 tracked files, including `.nojekyll`), so it is only as
  current as the last hand-run of `build_site.py` in step 5, and it changes
  only when that rebuild is committed and pushed. Nothing fails if you skip
  it; the site just goes on describing the previous release. Check it serves:
  `curl -sI https://simoncoombes.github.io/pretium/`.
- Submit the sitemap in Search Console if the page set changed. Google
  removed the ping endpoint in 2024, so it is a manual step.

## What has gone wrong before, and what now catches it

| failure | what caught it | what stops it now |
|---|---|---|
| sdist rejected, licence files declared at the root and packaged under `rust/` | PyPI, at upload | `license-files` declared explicitly; the sdist is verified before tagging |
| README links dead on PyPI, fine on GitHub | a human reading the live page | `tests/test_readme_links.py` |
| `calibrate.py --help` crashed on a literal `%` in prose | trying to use it | `tests/test_tool_help.py`, every tool in the directory |
| a release job half-published: wheels up, sdist refused, no way to replace | the job failed after uploading | `skip-existing`, so a re-run fills the gap |
| the docs site claimed the package was unreleased | reading the built page | the build asserts on that text and fails |
| the re-measurement gate ran 3 of 30 groups and reported "Full run" | reading `meta.groups_run` after the report looked too clean | a partial run prints `PARTIAL RUN: n of N` |
| 106 figures reported as MOVED, none of them a documentation defect | checking three of them against the page by hand | `resync.py`, run before the gate is believed |
| two gate rows measured a different pair, and a different agent, from the ones the page names | the rows disagreeing with prose that was right | the measurement follows the call the page prints |
| `CITATION.cff` shipped the previous release's date | reading the field at the tag | it is named in step 1 as the field that goes stale |
| a push reported `Everything up-to-date` while the fix sat on another branch | comparing SHAs rather than reading the push output | the branch check in the shipping list |

The pattern in all five: **correct everywhere the author looks, wrong only in
the destination.** That is why the checks above run against the artifact
rather than the source, and why the last step is installing from PyPI rather
than trusting the tree it was built from.

## Version policy

A change to the simulated trajectory is a breaking change however small it
looks, because a market that runs differently from the same seed invalidates
every published result that cited it. Coefficient changes therefore arrive as
a **new model preset**, never as an edit to an existing one, and old presets
keep running exactly as they did.

That is why `pt-v1` through `pt-v12` all still exist and reproduce, and why a
patch release can carry a new preset without being a breaking change. Check
the range rather than trusting this sentence: the shipped list is what
`pt.ModelParams.from_preset("pt-v99")` names in its error, and the default is
whatever `DEFAULT_PRESET_NAME` in `rust/src/params.rs` says. At 0.3.0 that is
twelve presets with `pt-v12` as the default.

## The release-notes page

`docs/releases.html` renders `CHANGELOG.md`: `build_site.py` parses the file
and inserts one section per released version into the bundle's Release Notes
page, above the era-boundary notes. An `## Unreleased` section is skipped, so
work in flight does not appear on the site. Release dates come from the
annotated tags, so a version cannot be dated by hand.

That means the changelog is the single source for three destinations: the
GitHub release body (cut by the release workflow), the docs site, and the
file itself. It also means a release whose changelog section is thin ships a
thin page, which is the intended pressure.
