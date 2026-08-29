# Contributing

Thanks for looking. A few things about this codebase are unusual, and
knowing them first will save you a wasted afternoon.

## The one rule everything else follows

**A change to the simulated trajectory is a breaking change**, whatever its
size.

Given a seed, a roster and a macro state, `tradefloor` produces one market and
the same market on every platform. Published results cite a seed and a model
fingerprint; if the same fingerprint starts producing a different market,
every one of those results silently becomes unreproducible. There is no
deprecation window for that.

In practice:

- Improving a coefficient is **not** an edit to an existing preset. It is a
  new preset. `pt-v1` still runs today exactly as it did, and will.
- The known-answer digest in `tests/known_answer.json` must not move without
  a `KAT_VERSION` bump. If it moves without one, two builds have disagreed,
  which is the thing the gate exists to catch.

## Setup

```bash
git clone https://github.com/simoncoombes/tradefloor
cd tradefloor
uv venv && source .venv/bin/activate
uv pip install maturin pytest pyarrow numpy pyyaml
maturin develop --release
```

`--release` is not optional for anything you intend to measure: a debug
build is roughly thirty times slower and you will conclude the simulator is
unusable.

`pyyaml` is a DEVELOPMENT dependency only, and the library never imports it.
Scenario files are read by `tradefloor.yaml_subset`, a strict reader for the
block-style subset the schema uses, because the package depends on nothing
and a configuration file should not change that. pyyaml is here so
`tests/test_yaml_subset.py` can check that reader against a real parser on
every fragment it accepts and every scenario the repository ships. Without
it those tests skip, and a skip and a pass are the same colour.

## Tests

```bash
pytest tests/                                   # Python, ~1350 tests
cargo test --manifest-path rust/Cargo.toml      # Rust, including parity
python tests/known_answer.py                    # the determinism digest
```

The count is there so you can tell a finished run from one that quietly
collected half of itself. At `ca4fa6a` the suite collected 1,358 tests and a
full run was 1321 passed and 37 skipped in 204s. Two things move that number
without anything being wrong. `tests/test_tool_help.py` parametrises over
`tools/calibration/*.py`, so adding a tool adds a test: one landed after
`ca4fa6a` and the tree now collects 1,359. And `tests/test_mcp.py` and
`tests/test_mcp_integration.py` call `pytest.importorskip("mcp")` at module
level, so without the `mcp` extra their 93 tests never enter the count at all
and collection reports 1,266 -- a missing extra shows up as a smaller total,
not as a column of skips.

Some suites are opt-in and skip cleanly rather than failing. Three different
reasons put a test in the skip column and it is worth knowing which you are
looking at: an optional import the default install does not have (`pyarrow`,
`gymnasium`, `numpy`, `nbformat` and `nbclient`, the `mcp` extra), an
artifact that is not in your tree (the Rust golden corpus, stored sweep
results), or `TRADEFLOOR_SLOW_TESTS` being unset. Only the last is a choice you
made.

`TRADEFLOOR_SLOW_TESTS=1` adds two things: the example notebooks, which are
executed rather than read (`tests/test_examples.py` walks `examples/` and
parametrises over what it finds, so its count moves when an example is added
-- the script syntax checks run on every pass, because a rename that missed a
reference should fail whether or not you remembered the flag), and the one
MCP test that runs a full 252-day evaluation. That test costs what it costs because
the only way to show a result AT the certified horizon is not a slice of a
shorter one is to reach the horizon.

The Rust parity suites compare against golden vectors generated from the
reference implementation. They are the evidence for the port being
bit-identical, so if you change anything in `rust/src/market/` or
`rust/src/economy/`, run them.

## Branches, and how work reaches `main`

`main` is what ships. It is protected: nothing lands on it except through a
pull request that has passed the determinism gate on all five targets and the
documentation build, and only the owner merges. `dev` carries the same
requirement.

**Every piece of work gets its own branch.** Not `dev` directly, not `main`.

```
feature branch  ->  dev  ->  main  ->  tag
```

| branch | for |
|---|---|
| `preset/pt-vNN` | one preset, and nothing else. A preset is an era boundary; it needs its own history so it can be reviewed, measured and reverted as one thing. |
| `feat/<name>` | a mechanism or an engine change |
| `fix/<name>` | a reported defect |
| `docs/<name>` | prose, the site, the notebooks |
| `chore/<name>` | tooling, CI, release plumbing |

**One preset per branch, always.** Two presets on one branch cannot be
measured against each other, and the losing one cannot be dropped without
rewriting the history of the winner. The measurement is the deliverable, so
the branch is the unit that carries it.

`dev` is the integration branch and is reset to `main` after every release, so
it is never a long-lived fork. It requires the determinism gate but not the
documentation build: model work legitimately leaves `docs/` stale until the
release rebuilds it, and failing that check mid-experiment teaches nothing.
`main` requires both, because that is where a reader sees it.

**Neither branch allows a force push or a deletion.** A rewritten `dev` used
to be a normal way to tidy up; it is now blocked, because a calibration run
that cloned it cannot be reproduced afterwards if its commits are gone.

## Where code goes

**If it decides something about the market, it belongs in `rust/src/`, not
in a binding.**

The engine is consumed twice, as a Python extension and as WebAssembly, and
a modelling decision implemented separately in each is a fork dressed up as
glue code. The divergence is invisible until a whole
simulated market has drifted apart. The day loop lived in the Python binding
until the browser build needed it too; moving it was a day's work that
should not have to happen twice.

Bindings own conversion, error translation and bookkeeping. Nothing else.

## Where examples, integrations and fixtures go

`examples/` used to mean one thing: a numbered series read in order. It now
means two, and keeping them in one flat directory cost something real. The
example test globbed `0*`, so the first unnumbered example added to
`examples/` was invisible to CI on the day it landed -- it compiled nowhere,
executed nowhere, and nothing said so.

Three tiers, and the rule for each is about who owns it.

```
examples/
    00-...ipynb .. 09-...py     the curriculum. Reading order. Core library.
    README.md
    data/
    <study>/                    one self-contained study per directory
        <name>.py
        <name>.ipynb
        README.md
        artifacts/              output, git-ignored, regenerable

python/tradefloor/
    integrations/
        <framework>.py          one adapter per third-party framework

tests/
    test_<name>.py              flat, as everything else here is
    fixtures/<name>/            recorded input, committed, one copy
```

**The numbers are a curriculum, not an index.** `00` to `09` are steps a
reader takes in order, they use the core library and at most an extra the
project itself ships, and adding to them is adding a lesson. A study is not
a lesson. It has one question, it may need an optional extra, and there is no
sense in which it comes after `09`, so it gets a name and a directory instead
of the next number.

**A study is one directory.** The script and the notebook for one experiment
are the same experiment in two presentations; splitting them across
`examples/` and a top-level `notebooks/` puts two halves of one thing in two
places and makes `notebooks/` a second unordered pile on the day a second one
arrives. The notebook imports the module; the module is the source of truth.

**Artifacts are output, fixtures are input, and they do not live together.**
An artifact is written by a run, describes that run, and is regenerable by
running it again -- so it sits beside the run that wrote it, in
`examples/<study>/artifacts/`, and is git-ignored. A fixture is recorded once
and replayed forever; it is the thing under test, and the test suite and the
example both read the SAME one, because two copies of a recording are two
recordings that can drift. So fixtures are committed, under
`tests/fixtures/<name>/`, whoever reads them.

**An integration is a subpackage member, an optional extra, and a study.**
See `python/tradefloor/integrations/__init__.py` for the rules the adapter
itself follows -- lazy import, never reached by `import tradefloor`, one
extra named after the framework. The runnable half is a study like any other.

**Every example is checked.** `tests/test_examples.py` walks `examples/`
rather than globbing `0*`, so a new example cannot be added invisible again.
Scripts are syntax-checked on every run; notebooks are executed behind
`TRADEFLOOR_SLOW_TESTS=1`.

## Style

The comment density here is higher than most codebases and it is
deliberate. The convention is that a comment explains **why**, and
especially why the obvious alternative is wrong, which is usually because
it was tried. If you remove a guard that looks redundant, check whether its comment
says what happened last time.

Numbers in prose carry their measurement. "Roughly a quarter" is a
convention; "0.0239 against a band of -0.08 to 0.06, thirty seeds at 252
days" is a measurement. Only the second kind belongs in a docstring. That
one is `return_acf1` on the shipped preset, and you can read both halves of
it back out of `tradefloor.envelope.CERTIFIED` and `tradefloor.facts.REAL_MARKETS`
rather than taking this file's word for it.

Punctuation is ASCII. No em dashes, en dashes, typographic minus signs or
arrows, in prose or in a docstring: use ` -- ` and `-`. The minus sign is the
one that bites, because it looks right and is a different character from the
one in the number beside it.

## Reporting a determinism failure

This is the most valuable bug you can find. If two platforms disagree on
`python tests/known_answer.py`, please open an issue with both digests, both
platforms, and the output of `tradefloor.version()`. That is enough to start;
we can reproduce the rest.
