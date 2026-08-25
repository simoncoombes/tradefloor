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

Then `cargo update -p pretium` so `Cargo.lock` follows, and rebuild
(`maturin develop --release`) so the installed package reports the new
number rather than the old one.

### 2. Write the changelog section

`CHANGELOG.md`, newest first, heading `## X.Y.Z`. The release workflow cuts
the GitHub release notes from this section, matching `## X.Y.Z` or
`## [X.Y.Z]`. If it finds nothing the release ships with empty notes.

Prose, not a bulleted catalogue. No em dashes or en dashes anywhere: a test
does not enforce this, a reader notices it.

### 3. Run the suite, including the slow half

```
python -m pytest tests/ -q
PRETIUM_SLOW_TESTS=1 python -m pytest tests/test_examples.py -q
```

The second executes all six notebooks and the research workflow end to end.
It is skipped by default because it takes half a minute, which is exactly
why it goes stale.

### 4. Rebuild the documentation site

```
python tools/docs/build_site.py
```

It reads the version from `pyproject.toml`, so the nav badge and the BibTeX
block follow automatically. Two version strings in the bundle are HISTORY
and must not move: the release-notes headings and the "measured on pretium
0.1.0" provenance line. The build matches them by surrounding markup and
leaves them alone.

If a new design bundle has landed, drop it at `tools/docs/design-bundle.html`
first. The build asserts on the release-status text it expects to find, so a
reworded bundle fails loudly rather than shipping a stale claim.

### 5. Check the README survives PyPI

```
python -m pytest tests/test_readme_links.py -q
```

`readme = "README.md"` means this file becomes the PyPI project page, which
lives at pypi.org, so a relative link like `examples/01-first-simulation.ipynb`
resolves to `pypi.org/project/pretium/examples/...` and 404s. It renders
correctly on GitHub, which is why it survived two releases. The test fails on
any relative link and on any absolute link naming a file that is not there.

### 6. Run the determinism gate on the branch

```
gh workflow run determinism.yml --ref <branch> -f targets=all
```

Free on a public repository. It also runs on pushes to `main` touching
`rust/**`, `python/**`, `pyproject.toml` or the workflow itself, so on a
release from `main` it has usually already run.

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
read the 135 MB parity corpus, which `exclude` deliberately keeps out, and
they panic when it is absent. They are excluded **by name**, so a new test is
not silently dropped: add one that reads `goldens/` and you must add it to
`exclude` too, or a consumer running `cargo test` concludes the crate is
broken.

crates.io versions are permanent and cannot be replaced, only yanked.

## After

- **Verify from the outside**, not from your working tree:
  ```
  pip install pretium                     # the wheel
  pip install --no-binary :all: pretium   # the sdist, compiled from source
  ```
  The second is the path that was broken in 0.1.0 and nobody noticed until
  the release had gone out.
- The GitHub Pages deploy runs on the push to `main`. Check the site actually
  serves: `curl -sI https://simoncoombes.github.io/pretium/`.
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

That is why `pt-v1` through `pt-v8` all still exist and reproduce, and why a
patch release can carry a new preset without being a breaking change.
