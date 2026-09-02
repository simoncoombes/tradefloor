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

## Draw surgery (P11)

The second case below pins the same comparison for a PATCHED run:
`rust/src/wasm.rs`'s `patchDraws` against `noise.patch_draws` on this
side, over the same roster, seed, days and patch. Neither binding's
`Sim`/`Engine` exposes a digest of a patched run directly -- the wasm
surface is the five bindings `tradefloor-design`'s
`programme/P11-wasm-surgery.md` freezes, and none of them is a digest --
so this pins a smaller one, over `prices` alone (the PRICE half of
`fixed_simulation_digest`'s recipe, without the macro fields a `Sim`
does not expose), computed identically in `tools/wasm/check.mjs`.
"""

import struct

import tradefloor as pt
from tradefloor import _core
from tradefloor import noise as _noise
from tradefloor.counterfactual import World

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
    carries percent and a real initial value. That asymmetry is why
    the digest lives in the core and is called from both bindings instead of
    being recomputed at each edge.
    """
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


# -- Draw surgery (P11) -------------------------------------------------

#: The surgery page's own case. pt-v3 (CASE above) ships
#: jump_intensity_market at 0.0, like every preset before pt-v13, so a
#: market jump can never fire under it and unfiring one would compare a
#: run against itself. pt-v13 is the first shipped preset with a nonzero
#: one (about 0.057, VIX-coupled -- rust/src/params.rs), and day 13 was
#: found by tracing the jumps stream over days 1 to 19 and checking each
#: day's market uniform against the intensity: 0.0402 on day 13, against
#: 0.19 to 0.97 on every other day traced, is the only one below it.
SURGERY_CASE = dict(size=12, universe_seed=7, seed=3, days=15, ticks=65,
                    preset="pt-v13")
SURGERY_DAY = 13

#: The jumps stream's uniform index for SURGERY_DAY, on the same
#: (size, universe_seed, seed, preset) roster: uniform position 0 at
#: construction, plus 13 (1 + 12 companies) per day for 13 days.
SURGERY_ADDRESS = 169

#: Measured on this build: the control run (no patch) and the same case
#: with SURGERY_DAY's market jump unfired, each hashed by
#: `_prices_digest` below. `tools/wasm/check.mjs` pins the same two hex
#: strings and recomputes them through the wasm bindings; a mismatch
#: there means the two builds disagree about this simulation.
SURGERY_CONTROL_DIGEST = \
    "7b7cf4c6b14d7ff5bc84c65edbc88b6e3258a3f21cd76bdb9f04277c54c1cbc9"
SURGERY_SHOCK_DIGEST = \
    "764be68c99799b9ab636d3876ef721d26331db95c9e9b796214046d9557b6455"


def _prices_digest(prices) -> str:
    """sha256 over each price's big-endian bytes, in roster order.

    The PRICE half of `_core.fixed_simulation_digest`'s recipe (this
    file's own module docstring), used here because a wasm `Sim`
    exposes `prices` but not the macro fields that digest also hashes:
    the frozen wasm surface (`tradefloor-design`'s
    `programme/P11-wasm-surgery.md`) has no getter for them, so a
    patched Sim's digest can only be over the prices. `tools/wasm/
    check.mjs` implements the identical recipe in JavaScript over the
    same bytes (`Buffer.writeDoubleBE`); a change to either recipe
    without the other breaks the comparison this exists to make.
    """
    import hashlib
    h = hashlib.sha256()
    for p in prices:
        h.update(struct.pack(">d", p))
    return h.hexdigest()


def _run_surgery_case(patch_day):
    """Build SURGERY_CASE's roster and run it, unfiring `patch_day`'s
    market jump first if given (`None` runs the control).

    Mirrors what the page's five wasm bindings do exactly: `Sim::new`,
    then (if patching) the `jumpAddress` arithmetic and `patchDraws`,
    then `runDays`. No agent and no `World`, because the wasm `Sim`
    trades nothing either -- `rust/src/wasm.rs`'s `run_day` calls
    `run_session` with `order_volumes: &[]`.
    """
    universe = pt.Universe.random(SURGERY_CASE["size"],
                                  seed=SURGERY_CASE["universe_seed"])
    engine = pt.Engine(universe=universe, seed=SURGERY_CASE["seed"],
                       model=SURGERY_CASE["preset"])
    address = None
    if patch_day is not None:
        uniforms0, _ = engine.stream_positions()["jumps"]
        per_day = 1 + len(engine.tickers)
        address = uniforms0 + patch_day * per_day
        _noise.patch_draws(engine, [_noise.Patch(
            _noise.DrawAddress("jumps", "uniform", address), _noise.NO_FIRE)])
    engine.run_days(SURGERY_CASE["days"], ticks_per_day=SURGERY_CASE["ticks"])
    raw = engine.prices()
    prices = struct.unpack(f"<{len(raw) // 8}d", raw)
    return prices, address


def test_the_surgery_cases_address_is_the_pinned_one():
    """Guards SURGERY_ADDRESS itself, so a change to the roster size or
    the construction order above shows up here rather than silently
    shifting where every other test in this section reads."""
    _, address = _run_surgery_case(SURGERY_DAY)
    assert address == SURGERY_ADDRESS


def test_the_surgery_control_digest_is_stable():
    prices, _ = _run_surgery_case(None)
    assert _prices_digest(prices) == SURGERY_CONTROL_DIGEST


def test_the_surgery_patched_digest_is_stable():
    """Pinned for `tools/wasm/check.mjs`'s second check: the same
    roster, seed, days and patch, run through the wasm bindings, must
    reach the same digest."""
    prices, _ = _run_surgery_case(SURGERY_DAY)
    assert _prices_digest(prices) == SURGERY_SHOCK_DIGEST


def test_unfiring_day_13s_jump_moves_the_price():
    """Guards the case itself against becoming a vacuous comparison.

    pt-v13's jump intensity is low enough that most days do not fire
    (SURGERY_CASE's docstring), so a day chosen without checking could
    easily land on one where patching to NO_FIRE changes nothing because
    nothing was going to fire anyway -- the control and shock digests
    would then agree for the least interesting reason available, the way
    `test_a_different_seed_gives_a_different_digest` guards the case
    above. Day 13 was chosen because it does not agree.
    """
    control, _ = _run_surgery_case(None)
    shock, _ = _run_surgery_case(SURGERY_DAY)
    assert control != shock
    assert SURGERY_CONTROL_DIGEST != SURGERY_SHOCK_DIGEST


def test_jump_address_matches_world_unfire():
    """The acceptance criterion in `programme/P11-wasm-surgery.md`:
    "jumpAddress(day) equals the address World.unfire computes for the
    same roster and day". `jump_address` (`rust/src/wasm.rs`) is
    wasm-only and cannot be called from Python, so this checks two
    independent expressions of its formula instead: the one inlined in
    `_run_surgery_case` above (which mirrors the Rust arithmetic byte
    for byte -- see `Sim::jump_address`'s doc comment) against
    `World.unfire`'s own recorded address, on the same roster and day.
    """
    class _Idle:
        """Never asked to act: `unfire` runs no days and asks no agent,
        so any object would do; this one documents that at the call
        site instead of leaving a bare `object()` unexplained."""

        def act(self, obs):
            return {}

    universe = pt.Universe.random(SURGERY_CASE["size"],
                                  seed=SURGERY_CASE["universe_seed"])
    world = World(seed=SURGERY_CASE["seed"], universe=universe,
                  agent=_Idle(), model=SURGERY_CASE["preset"])
    world.unfire(SURGERY_DAY)

    stream, kind, index = world.surgeries[-1]["address"]
    assert (stream, kind) == ("jumps", "uniform")
    assert index == SURGERY_ADDRESS
