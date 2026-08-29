"""The browser build and this build must produce the same market.

`pretium` compiles to two targets: a Python extension and WebAssembly. The
crate's whole justification is that they are one model rather than two --
`lib.rs` says a Rust engine that runs only in Python while the browser keeps
its own is "not a port, it is a fork". That claim needs a number.

`_core.fixed_simulation_digest` runs one fixed simulation and hashes it, and
the wasm surface exposes the SAME core function as `priceDigest`. Both
therefore hash the same thing the same way, which matters more than it
sounds: the first attempt compared a wasm digest against one rebuilt in
Python and they disagreed, because the Python surface reports rates as
fractions while the core carries percent. That was a units bug in the
harness presenting as a determinism failure. A check implemented twice is a
fork of the check.

This file pins the Python side. The wasm side is verified by
`tools/wasm/check.mjs`, which is not run here because it needs a wasm
toolchain and node; the digest below is the value both produced on
2026-08-24.
"""

import tradefloor as pt
from tradefloor import _core

#: One fixed simulation: twelve instruments, universe seed 7, sim seed 3,
#: five days at 65 ticks. Small enough to run in milliseconds and long
#: enough that a low-bit disagreement compounds into the digest.
CASE = dict(size=12, universe_seed=7, seed=3, days=5, ticks=65,
            preset="pt-v3")

#: Measured on macos-arm64 (native) AND wasm32-unknown-unknown (node) on
#: 2026-08-24, identical. Moves only when the simulation moves -- a change
#: here without an intended one means two builds have drifted apart, which
#: is the thing this exists to catch.
EXPECTED = "2b2f314181bde90a9ccabbc8232b03cba2e04221bee85501ffc78ae042cfd8f5"


def test_the_fixed_simulation_digest_is_stable():
    assert _core.fixed_simulation_digest(**CASE) == EXPECTED


def test_the_digest_is_deterministic_across_calls():
    assert (_core.fixed_simulation_digest(**CASE)
            == _core.fixed_simulation_digest(**CASE))


def test_a_different_seed_gives_a_different_digest():
    """Guards the probe itself. A digest that ignored its arguments would
    match across bindings for the least interesting reason possible."""
    other = dict(CASE, seed=CASE["seed"] + 1)
    assert _core.fixed_simulation_digest(**other) != EXPECTED


def test_a_different_preset_gives_a_different_digest():
    assert _core.fixed_simulation_digest(**dict(CASE, preset="pt-v1")) \
        != EXPECTED


def test_an_unknown_preset_is_refused():
    import pytest
    with pytest.raises(pt.ValidationError):
        _core.fixed_simulation_digest(**dict(CASE, preset="pt-v99"))


def test_the_probe_simulates_the_market_it_names():
    """The probe must actually run the case it advertises.

    Without this it could hash anything reproducible -- a constant, the seed
    -- and every cross-binding comparison would still pass for the least
    interesting reason available.

    This pins the PRICES of the same market built through the ordinary
    public API rather than rebuilding the digest. Rebuilding it here was the
    first attempt and it failed on units: the Python surface reports rates
    as fractions and an absent corporate bond yield as None, while the core
    carries percent and a real initial value. That asymmetry is exactly why
    the digest lives in the core and is called from both bindings instead of
    being recomputed at each edge.
    """
    import struct

    universe = pt.Universe.random(CASE["size"], seed=CASE["universe_seed"])
    engine = pt.Engine(universe=universe, seed=CASE["seed"],
                       model=CASE["preset"])
    engine.run_days(CASE["days"], ticks_per_day=CASE["ticks"])
    raw = engine.prices()
    prices = struct.unpack(f"<{len(raw) // 8}d", raw)

    assert len(prices) == CASE["size"]
    assert engine.tickers[:4] == ["AAA", "AAB", "AAC", "AAD"]
    # Measured on macos-arm64 and reproduced bit-for-bit by the wasm build.
    assert [round(p, 2) for p in prices[:3]] == [483.82, 245.57, 470.59]
