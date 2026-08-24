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
  a `KAT_VERSION` bump. If it moves without one, two builds have disagreed —
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
pytest tests/                                   # Python, ~1000 tests
cargo test --manifest-path rust/Cargo.toml      # Rust, including parity
python tests/known_answer.py                    # the determinism digest
```

Some suites are opt-in because they need something the default install does
not have — `pyarrow`, `gymnasium`, the `mcp` package. They skip cleanly
rather than failing. `PRETIUM_SLOW_TESTS=1` enables a handful that run a
full 252-day evaluation.

The Rust parity suites compare against golden vectors generated from the
reference implementation. They are the evidence for the port being
bit-identical, so if you change anything in `rust/src/market/` or
`rust/src/economy/`, run them.

## Where code goes

**If it decides something about the market, it belongs in `rust/src/`, not
in a binding.**

The engine is consumed twice — as a Python extension and as WebAssembly —
and a modelling decision implemented separately in each is a fork wearing
the costume of glue code. The divergence is invisible until a whole
simulated market has drifted apart. The day loop lived in the Python binding
until the browser build needed it too; moving it was a day's work that
should not have to happen twice.

Bindings own conversion, error translation and bookkeeping. Nothing else.

## Style

The comment density here is higher than most codebases and it is
deliberate. The convention is that a comment explains **why**, and
especially why the obvious alternative is wrong — usually because it was
tried. If you remove a guard that looks redundant, check whether its comment
says what happened last time.

Numbers in prose carry their measurement. "Roughly a quarter" is a
convention; "0.0375 against a band of −0.08 to 0.06, thirty seeds at 252
days" is a measurement. Only the second kind belongs in a docstring.

## Reporting a determinism failure

This is the most valuable bug you can find. If two platforms disagree on
`python tests/known_answer.py`, please open an issue with both digests, both
platforms, and the output of `pretium.version()`. That is enough to start;
we can reproduce the rest.
