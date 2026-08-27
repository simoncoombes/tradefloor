# Contributing

Thanks for looking. A few things about this codebase are unusual, and
knowing them first will save you a wasted afternoon.

## The one rule everything else follows

**A change to the simulated trajectory is a breaking change**, whatever its
size.

Given a seed, a roster and a macro state, `pretium` produces one market and
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
git clone https://github.com/simoncoombes/pretium
cd pretium
uv venv && source .venv/bin/activate
uv pip install maturin pytest pyarrow numpy
maturin develop --release
```

`--release` is not optional for anything you intend to measure: a debug
build is roughly thirty times slower and you will conclude the simulator is
unusable.

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
results), or `PRETIUM_SLOW_TESTS` being unset. Only the last is a choice you
made.

`PRETIUM_SLOW_TESTS=1` adds two things: the example notebooks, which are
executed rather than read (`tests/test_examples.py` holds 21 tests over eight
notebooks and two scripts, 19 of them behind the flag -- the two script
syntax checks run on every pass, because a rename that missed a reference
should fail whether or not you remembered the flag), and the one MCP test
that runs a full 252-day evaluation. That test costs what it costs because
the only way to show a result AT the certified horizon is not a slice of a
shorter one is to reach the horizon.

The Rust parity suites compare against golden vectors generated from the
reference implementation. They are the evidence for the port being
bit-identical, so if you change anything in `rust/src/market/` or
`rust/src/economy/`, run them.

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
it back out of `pretium.envelope.CERTIFIED` and `pretium.facts.REAL_MARKETS`
rather than taking this file's word for it.

Punctuation is ASCII. No em dashes, en dashes, typographic minus signs or
arrows, in prose or in a docstring: use ` -- ` and `-`. The minus sign is the
one that bites, because it looks right and is a different character from the
one in the number beside it.

## Reporting a determinism failure

This is the most valuable bug you can find. If two platforms disagree on
`python tests/known_answer.py`, please open an issue with both digests, both
platforms, and the output of `pretium.version()`. That is enough to start;
we can reproduce the rest.
