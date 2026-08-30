# Releasing

One tag drives everything. `git push origin vX.Y.Z` starts the workflow that
builds five wheels, builds the sdist, proves they agree, publishes to PyPI
and writes the GitHub release. The steps below are the parts a tag cannot do
for you, and the checks that exist because something once went wrong.

Every item here is either automated and named so you know not to do it by
hand, or manual and ordered so the irreversible steps come last.

## Before you tag

Run this first. It is the mechanical half of everything below, and it takes a
second:

```
python tools/release/check.py --version X.Y.Z
```

It reports and changes nothing. Every row in it exists because that thing was
wrong once and nothing noticed. What it cannot check it prints at the end
under "Still yours", so a step that needs judgement is visible rather than
implied.

### 1. Version locations

`pt.version()` is a published fact and these must agree:

| file | what reads it |
|---|---|
| `pyproject.toml` | the wheel, the sdist, PyPI |
| `rust/Cargo.toml` | the crate, crates.io |
| `CITATION.cff` | anyone citing a result |
| `tradefloor-docs: docs/reproducing-a-run.md` | the worked example that prints it |

The fourth location moved with the docs at 0.5.0: it lives in the
PRIVATE `simoncoombes/tradefloor-docs` repo now, along with every
rendered page that prints the version. Bump it there, rebuild, and
push that repo as part of the same release pass.

`CITATION.cff` carries two fields, not one: `version:` and `date-released:`,
the day the version was tagged. A version without the date it shipped is half
a citation.

`date-released` is the field that goes stale silently. At 0.3.0 it still read
`2026-08-26`, which is when v0.2.0 was tagged, sitting under `version: 0.3.0`.
Nothing tests it, so it reaches a citation field pointing at the previous
release. Set it to the day you intend to tag, and check it again at the tag.

Then `cargo update -p tradefloor` so `Cargo.lock` follows, and rebuild
(`maturin develop --release`) so the installed package reports the new
number.

### 2. Changelog section

`CHANGELOG.md`, newest first, heading `## X.Y.Z`. The release workflow cuts
the GitHub release notes from this section, matching `## X.Y.Z` or
`## [X.Y.Z]`. If it finds nothing it does not ship empty notes: the script
calls `sys.exit("CHANGELOG.md has no section for ...")`, the step runs under
`set -euo pipefail`, and the whole `github_release` job fails before
`gh release create` runs. That job `needs: [setup, verify, publish]`, so by
the time you see the failure the wheels are already on PyPI, which is the one
step that cannot be undone. Write the section before you tag, not after.

Write prose, with no em dashes or en dashes anywhere;
`tests/test_brand_commitments.py` enforces that on `CHANGELOG.md`, and
`tools/prose/prose.py` checks the rest of the house style.

**250 words above the marker.** That is the budget, and
`tests/test_prose.py` fails over it. Everything above
`<!-- release-note-ends -->` is what `release.yml` publishes as the GitHub
release note and what the release-notes page renders; everything below it is
kept and not published.

The budget exists because this project used to write changelog sections as
essays and they grew: 0.3.0 ran to 375 words, 0.6.0 reached 1,257 against a
median of 139 across the twelve sections before it. A reader on a release
page wants what changed, what breaks, what to pin and what got worse. 250 is
not tight: 0.4.0 (233) and 0.2.0 (240) both moved the default preset and
explained themselves inside it.

The budget binds the newest section only. The ones below it were published
under their tags, and rewriting a release note afterwards edits a record
somebody may have read.

```markdown
## X.Y.Z

<what changed, what to pin, what got worse: a few hundred words>

<!-- release-note-ends -->

### the detail, and how it was measured

```

Everything above `<!-- release-note-ends -->` becomes the release note, and
the page folds the rest behind a disclosure. A section without the marker
publishes whole, as every version before 0.3.0 does.

### 3. Full suite including the slow half and Rust

```
maturin develop --release          # FIRST, and not optional either
python -m pytest tests/ -q
TRADEFLOOR_SLOW_TESTS=1 python -m pytest tests/test_examples.py -q
cd rust && cargo test --offline
```

**Rebuild before you read any of it.** The suite compares a source tree
against a compiled extension, so a tree that has moved past its build reports
drift everywhere and none of it is real. On 2026-08-30 an extension exposing
101 settable parameters against a tree carrying 106 produced thirteen
failures across `test_atlas` and the survey files, all of them phantom, and
the first reading of them was that another branch had broken `main`.
`tools/release/check.py` reports the mismatch as its first row for the same
reason.

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
does not depend on. Check the summary line. The exit code is green whether or
not the eight tests that actually EXECUTE a notebook ran, and those are the
ones that need
`nbclient`, so with `nbformat` alone those eight skip, the other thirteen
report, and the step is green having executed no notebook at all. Both are
exactly why it goes stale.

The last timed run of the whole file was 96s (`--durations=15` on an idle
machine), with `09-a-pandemic-shaped-market.ipynb` alone at 40s against 21s
for the next slowest. That figure is machine-bound: the same file under load
has measured more than twice that, so read it as an order of magnitude. Time
it on a quiet machine or not at all.

### 4. Published figures

```
python tools/remeasure/remeasure.py
```

This is the step the site's own status paragraph promises.
`docs/releases.html` tells every reader that published figures "are
re-measured by `tools/remeasure`, which reports every number the stated
method no longer produces, and a figure it flags is a documentation defect
until someone corrects it." Nothing in CI runs it, so if it is not run here
the guarantee is only a sentence.

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
the ceiling is how much of the engine releases the GIL, not the core
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
106 reproduced against 165 MOVED and 5 `structural_fail`: the shape of a
release where the preset moved and the prose did not follow yet.

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

### 5. Documentation site

**The site is not in this repository.** It is `simoncoombes/tradefloor-docs`,
private, and Vercel serves its `docs/` directory verbatim from `main` with no
build step, so a push there is a deploy that is live in about a minute.
`docs/`, `tools/docs/learn/` and `build_site.py` all left with it. The four
commands this step used to give you have not existed here since that move,
which is the same failure this step already carries a warning about: a step
that names paths nobody has is worse than no step, because it reads as done.

Documentation does not follow a release on its own. In the docs repo, on a
branch:

**5.1 Re-vendor what the site checks itself against**, from this tag:
`pyproject.toml`, `rust/src/params.rs`, `python/tradefloor/_core.pyi`,
`measurements/`, `rust/goldens/*.json`, `examples/data/`,
`python/tradefloor/presets/*.json` and `tools/prose/prose.py`. The last two
are new: the preset records carry the measured panel the site publishes, and
`prose.py` is canonical here now rather than in the docs repo, so both trees
are checked by one copy of the rules. That repo's README
lists each one and why it is there. The site builds its parameter and API
reference out of these, so a stale copy is a page that describes the previous
release.

**5.2 Regenerate the two inventories against the RELEASED wheel.**

```
python -m venv /tmp/rel && /tmp/rel/bin/pip install tradefloor==X.Y.Z
python tools/docs/learn/params.py --python /tmp/rel/bin/python
python tools/docs/learn/api.py
```

`--python` is required. A local development build reports the same
version string as the release and can expose parameters the release does not:
on 2026-08-29 this machine's system interpreter carried a build calling itself
0.5.0 with 98 settable parameters against the published wheel's 93,
`qe_pe_stock_gain` among them. Generating from it would have published
unreleased dials as shipped, under a version that agreed. `params.py` digests
the settable list beside the version for exactly this reason, and `--check`
reports the difference in those terms.

**5.3 Build, and pass all five checks** before opening the PR:

```
python tools/docs/learn/build.py
python tools/docs/learn/build.py  --check
python tools/docs/learn/data.py   --check
python tools/docs/learn/params.py --check --python /tmp/rel/bin/python
python tools/docs/learn/api.py    --check
node   tools/docs/learn/verify.cjs docs
node   tools/docs/learn/wraps.cjs  docs
```

`build.py --check` needs two runs to settle: each page's `dateModified` comes
from the last commit touching its sources, so committing moves the date the
next build writes. A tree still dirty on the third run is a bug, not churn.

**5.4 Open a PR.** Main is production there; it is merged, never pushed to.

### 5b. If this release moves the default preset

The rarest release and the one that touches most. Everything below moved at
0.6.0, and the ones marked NEW were found by breaking rather than by being on
a list.

**What a default move changes, in order:**

**1. The certified envelope.** `envelope.PRESET`, `CERTIFIED` and
`MEASURED_504` describe the shipped default by the module's own contract, and
`tests/test_envelope.py` asserts `model_preset()["name"] == envelope.PRESET`.
Measure the new preset beside the outgoing one in a single paired run, so the
comparison is not two runs on two days.

**2. The preset record**, generated rather than typed:

```
python tools/presets/record.py --panel <the preset_panel artefact>
python tools/presets/record.py --panel <the same artefact> --check
```

One JSON per preset under `python/tradefloor/presets/`, shipped in the wheel,
read by `tradefloor.preset_record` and by the site.
`tests/test_preset_records.py` binds the record for `envelope.PRESET` to
`CERTIFIED` and `MEASURED_504`, so the panel and the preset it describes
cannot disagree. That binding is what 0.6.0 lacked: the panel was re-typed by
hand and `DECAY_252` beside it was not, and nothing failed.

**3. The determinism baseline.** Every seeded trajectory changes, so
`KAT_VERSION` bumps and `tests/known_answer.json` is regenerated. Produce the
new digest on two architectures before committing it; the baseline note
records that it was, and at 0.6.0 a Windows build and a Graviton box agreed
before the five-target gate ever ran.

**4. Test expectations pinned to the old default.** NEW, and the largest
unplanned piece of 0.6.0, where six broke in three shapes:

- a dial that was inert becomes live, so its perturbation entry flips
- a scenario stops reaching a threshold because the new preset runs calmer,
  which a non-vacuity guard reports as "this proves nothing"
- a documented invariant stops holding

Re-measure each expectation. Do not relax one. Keep the replaced value in the
comment beside it: those comments are how the fifth momentum and
mean-reversion swap was recognised as the fifth.

**5. Anything recorded and keyed to the market.** NEW. A replay fixture keyed
to the exact text an agent was sent dies when the market moves.
`tests/fixtures/finrobot/rate-shock.json` had to be re-recorded live against
the model at 0.6.0. Grep `tests/fixtures/` for anything a market change
invalidates before assuming the suite covers it.

**6. Prose that names the preset.** NEW. `README.md` states the default twice
and neither line is a version location, so nothing in step 1 catches them.
`tools/release/check.py` now does. Also sweep `python/` for prose figures:
`scenario.py` and `interventions.py` both described a boundary that a preset
had moved.

**7. The published grid, and the site.** Almost every figure the site
publishes was measured under one preset and almost none say which. When the
default moved from `pt-v12` to `pt-v14`, nothing re-measured: six pages went
on quoting pt-v12 numbers until 2026-08-29, reporting a pooled capture of
+0.783 where the shipped default gives +0.878, and a sign test of 9-3 where
it is 11-1. Two pages also printed a `separation()` shape the function has
never returned.

```python
u = tf.Universe.random(30, seed=11)
r = tf.rank(lambda: tf.reference_agents(seed=3), seeds=range(12),
            universe=u, days=10, workers=4)
r.separation("mean_reversion", "momentum")
```

Then grep for the figures it supersedes. `data.py --check` catches a moved
chart because the charts are generated; nothing catches a moved sentence, so
this one is on the person doing the release. Where a figure is written down,
write the preset beside it.

`figures.py` in the docs repo cannot help here: it wants
`tools/remeasure/inventory.json`, which lives in this repository and was never
vendored, so the prose-figure check does not run there at all.

**What a default move does NOT change.** A figure measured under a preset
that is still selectable stays true; it just stops describing the default. Say
which preset it describes rather than deleting it. Where a constant could not
be re-measured in time, mark it in place: `DECAY_252` and `DECAY_SLOPE` carry
that mark today.

### 5c. Document the API this release makes public

The site describes the published wheel, not `main`. So API that merges
between releases is deliberately left undocumented until it ships, and the
release is when that debt comes due: read the changelog section written in
step 2 and document everything in it that is public surface.

This is not optional tidying. A reader who installs the new version and
finds the site describing the previous one has no way to tell which is
wrong, and the pages carry no version of their own to warn them.

Two things to check while doing it, because both have been wrong before:

- **An argument that changed meaning needs its own note.** Say so
  plainly. Code that already passes it is now doing something different,
  and its author will not think to re-read a page about a call they
  already use.
- **A signature that gained an optional argument still changes the docs**,
  because the API pages publish signatures from the vendored stub and a
  stale stub publishes the old one.

If a release ships nothing public, say so in the docs PR rather than
skipping the step, so the next person can tell the difference between
"nothing to do" and "not done".

### 6. README on PyPI

```
python -m pytest tests/test_readme_links.py -q
```

`readme = "README.md"` means this file becomes the PyPI project page, which
lives at pypi.org, so a relative link like `examples/01-first-simulation.ipynb`
resolves to `pypi.org/project/tradefloor/examples/...` and 404s. It renders
correctly on GitHub, so it survived two releases. The test fails on
any relative link and on any absolute link naming a file that is not there.

### 7. Determinism gate

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
So the default is the narrow path to the gap.
Money has nothing to do with it: the repository is public, so standard runners
are free, and the workflow's own note records the five-target run of 2026-08-27
(run 33028345268) at 3m35s of wall clock and about ten minutes of runner
time across all seven jobs. Ask for `all` here anyway, because the point of
a release gate is that the whole artifact set was checked in one place,
together, on the commit being shipped.

It also runs on pushes touching `rust/**`, `python/**`, `pyproject.toml` or
the workflow itself. A tag push runs all five regardless of any input: a
release must not ship on a partial gate.

**`all targets agree` is now a REQUIRED check on `main` and on `dev`**, so
this step is no longer advisory. The five-target run is what admits the pull
request, which means it has to be dispatched against the release branch and
be green before the merge, not against `main` afterwards. The two-target push
gate does not satisfy it: the required context is the job named `all targets
agree`, and a run started with the default `unverified` never produces it.

## Shipping order

The seven steps above run on a release branch off `main`, not on `main` and
not on `dev`. `main` is protected: a pull request is the only way in, it must
pass the determinism gate on all five targets and the documentation build,
and only the owner merges. `dev` is protected the same way, minus the docs
build.

That changes the order this used to describe. The old sequence merged to
`main` and then dispatched the gate against it, which cannot happen now: the
gate has to be green BEFORE the merge, because it is what admits the merge.

1. **Branch from `main`.** `release/X.Y.Z`. The version bump, the changelog
   section and the rebuilt site all belong on it.
2. **Push the branch.** An AWS remeasure clones by branch name, so anything
   the release needs must be on the remote before the run is launched.
3. **Open the pull request into `main`.** Push fires the determinism gate at
   two targets; the required check is the five-target run, so dispatch it
   explicitly against the BRANCH:
   `gh workflow run determinism.yml --ref release/X.Y.Z -f targets=all`.
4. **Wait for both required checks**, `all targets agree` and `build`. Read
   the run you started; the newest in the list is often a different one. See
   step 7.
5. **Merge the pull request.** Pages serves from `main/docs`, so this is the
   step that publishes the site. Nothing before it is visible to a reader.
6. **Check the tag target is the merged commit.** `git rev-parse origin/main`
   against the merge commit, compared rather than assumed. A squash merge
   makes a NEW commit, so the SHA that passed the gate differs from the one
   you are
   about to tag. The gate ran on the same tree, which covers the code, but
   the tag must point at what is on `main`.
7. **Tag `origin/main` and push the tag.** That is the irreversible step: a
   PyPI version number cannot be reused.
8. **Watch both registries publish.** The tag drives PyPI and crates.io in
   parallel, and the GitHub release waits for both. Nothing to run by hand.
9. **Check docs.rs.** A 404 in the first minutes is the build queue, not a
   failure. Compare against an earlier version: if `0.2.0` returns 200 and the
   new one still 404s after ten minutes, the build failed and the crate page
   says why.
10. **Reset `dev` to `main`.** It is the integration branch, not a fork, and
    a stale `dev` is how the site and the model drift apart. `dev` blocks
    force pushes, so this is a merge, not a reset, unless it is a
    fast-forward.

**Check the branch after any step that moves branches.** A `git push origin
main` run from another branch reports `Everything up-to-date` and pushes
nothing, which reads exactly like a successful deploy. `git branch
--show-current` after every checkout, and compare `git rev-parse --short HEAD
origin/main` before believing a push.

**The owner can still push directly to `main`.** `enforce_admins` is off, so
protection is a workflow, and a hotfix is possible at three in the morning. A
release that skipped the pull request also skipped the required checks, which
are the only thing standing between a tag and an unreproducible wheel on PyPI.

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

Automated, from 0.4.3. The tag drives both registries: `publish to PyPI` and
`publish to crates.io` run in parallel off the same verified artefacts, and
the GitHub release waits for both, so a half-published version fails the run
rather than passing quietly.

It was manual until 0.4.2, and the cost of that showed: PyPI reached 0.4.2
while crates.io sat at 0.3.0, three releases behind, because publishing the
crate was a step someone had to remember rather than something the tag did.
0.4.2 was pushed to crates.io by hand to close the gap.

Both use Trusted Publishing, so no token exists in either registry's path.
crates.io mints one through `rust-lang/crates-io-auth-action` against the
`crates-io` environment, the same shape as PyPI's `pypi` environment. **Both
environments must be constrained to a single environment name on the registry
side**, or any workflow in the repository with `id-token: write` can publish.

The crate job packages and runs the packaged crate's own tests before
uploading. That check matters more than it sounds: sixteen of nineteen
integration tests read the 140 MB parity corpus that `exclude` deliberately
keeps out, and they panic when it is absent. What remains is `circuit_breaker`,
`roster_mutation` and `stream_alignment` plus the unit tests. They are excluded
**by name**, so a new test is not silently dropped: add one that reads
`goldens/` and it must go in `exclude` too, or a consumer running `cargo test`
concludes the crate is broken.

Unlike PyPI, the crate upload is not idempotent. PyPI's `skip-existing` lets a
re-run finish a partial upload; crates.io refuses a version that already
exists, and that refusal is the right outcome for a re-run rather than
something to swallow. **crates.io versions are permanent and can only be
yanked, never replaced.**

To publish by hand, which should now only happen to close a gap:

```
cd rust
cargo publish --dry-run          # builds and packages, uploads nothing
cargo package --list             # what would actually ship
cargo publish
```

**Check docs.rs after.** A 404 in the first minutes is the build queue, not a
failure. Compare against an earlier version: if `0.3.0` returns 200 and the new
one still 404s after ten minutes, the build failed and the crate page says why.

## After

- **Verify from the outside**, not from your working tree:
  ```
  pip install tradefloor                     # the wheel
  pip install --no-binary :all: tradefloor   # the sdist, compiled from source
  ```
  The second is the path that was broken in 0.1.0 and nobody noticed until
  the release had gone out.
- **Verify what was published, not that publishing happened.** Install the
  wheel from PyPI and ask it what it is:
  ```
  python -c "import tradefloor as tf; print(tf.version(), tf.model_preset()['name'])"
  ```
  Then reproduce the determinism digest inside it against
  `tests/known_answer.json` from the tag. That pair is what catches a tag cut
  from the wrong commit, which no amount of local green will show you. At
  0.6.0 both agreed, which is how the published envelope was known to
  describe the preset the wheel actually runs.
- **The site deploys from its own repository.** `docs/`, `tools/docs/` and
  `build_site.py` left this repository at 0.5.0. There is no Pages workflow
  here and no committed `docs/` tree; `.github/workflows/` holds
  `determinism.yml`, `release.yml` and `suite.yml`. Step 5 is the whole of
  what the site needs. Check it serves: `curl -sI https://tradefloor.dev/`.
- Submit the sitemap in Search Console if the page set changed. Google
  removed the ping endpoint in 2024, so it is a manual step.

## Past failures and their checks

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
| the docs quoted the previous default preset's figures | reading a published number after a preset change | step 5b re-measures the grid and names the preset beside the figure |
| the docs inventory was generated from a development build | two builds reporting one version | `params.py --check` digests the settable list; step 5.2 requires `--python` |
| `CITATION.cff` shipped the previous release's date | reading the field at the tag | it is named in step 1 as the field that goes stale |
| a push reported `Everything up-to-date` while the fix sat on another branch | comparing SHAs rather than reading the push output | the branch check in the shipping list |

The pattern in all five: **correct everywhere the author looks, wrong only in
the destination.** The checks above therefore run against the artifact, and
the last step installs from PyPI.

## Version policy

A change to the simulated trajectory is a breaking change however small it
looks, because a market that runs differently from the same seed invalidates
every published result that cited it. Coefficient changes therefore arrive as
a **new model preset**, never as an edit to an existing one, and old presets
keep running exactly as they did.

This is why `pt-v1` through `pt-v12` all still exist and reproduce, and why a
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
