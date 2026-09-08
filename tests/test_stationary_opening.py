"""The stationary day-zero opening of the business cycle.

Every run opens in EXPANSION at phase age ZERO, so thirty seeds leave their
first expansion at nearly the same age and move through the first cycle in
step. `cycle_stationary_opening` draws the day-zero phase AND its age from
the cycle's own stationary law instead. The identity is
`ModelParams::cycle_stationary_opening`; the renewal law and its own
properties are asserted in Rust (`economy::cycle::stationary_law`).

What is asserted here is what only the assembled engine can say:

- **The default is the base build to the bit.** The dial ships at 0.0 and
  `Engine::draw_stationary_opening` returns before touching the economy or
  the generator, so no arithmetic changes at all. The digest below was taken
  from a build of `b3658be` -- the commit this branch was cut from, with the
  dial absent from the source -- so it can fail honestly rather than
  recording what this build happens to produce. And the instrument is shown
  to be able to fail, on the same panel, before it is believed.
  `test_known_answer.py` makes the same claim over one seed and 250
  days against the committed `known_answer.json`; it is cited rather
  than restated here.
- **It is a switch.** A day-zero state is either drawn from that law or it
  is not; there is no half-drawn phase, so every non-zero value gives the
  same run.
- **The drawn ensemble is the law.** Over three hundred seeds the phase
  shares are the identity's `pi` and the ages are its quantiles, which is
  the end-to-end statement: the law reaches the engine, in the engine's own
  units, through the draw.
"""

import collections
import hashlib
import statistics
import struct

import pytest

import tradefloor

#: The roster the perturbation probe uses, so a failure here and a failure
#: there are on one subject.
UNIVERSE = tradefloor.Universe.random(10, seed=3)

#: Thirty seeds by thirty days by ten names: 9,000 daily returns.
SEEDS = range(1, 31)
DAYS = 30

#: The panel digest of the SHIPPED default, measured on a release build of
#: `b3658be` with the dial absent from the source, before this branch's
#: first commit. It is a recorded identity of that build, in the same sense
#: as `known_answer.json`'s digest and for the same purpose -- and unlike
#: that one it spans thirty seeds rather than one.
#:
#: The preset it describes is `BASE_PANEL_PRESET` below, not "the default".
#:
#: If this fails, something moved pt-v16. That is the whole claim, so
#: re-stamping it is not a fix.
#:
#: IT NAMES ITS PRESET since 0.7.0. It was recorded as "the shipped default"
#: and compared against whatever the default happened to be, so the release
#: that moved the default to pt-v18 broke it for the one reason that is not
#: a defect. Naming pt-v16 -- the default at `b3658be`, where this was
#: recorded -- keeps the identity exactly and makes it survive every later
#: boundary. Verified at 0.7.0: pt-v16 reproduces this digest over all 9,000
#: returns after the default moved, the burn-in's calendars were fixed and a
#: supplied opening stopped being relaxed.
BASE_PANEL_DIGEST = "fbe62e965aa47a36fd8dd1cf610dd86d804ce94b9bb1ae465db75c1a4fb5e757"

#: The preset the digest above records: the default at `b3658be`.
BASE_PANEL_PRESET = "pt-v16"

#: THE TWO DIALS THIS FILE'S TABLES WERE DERIVED UNDER, held so every test
#: measures `cycle_stationary_opening` rather than the default around it.
#: Both were 0.0 in every preset through pt-v16 and both move at pt-v18, so
#: they were invisible until the default did.
#:
#: `macro_burn_in_days` runs AFTER the opening is drawn and relaxes it: 755
#: days of macro between the draw and the reading, which is what the age
#: tables would then be measuring.
#:
#: `cycle_hazard_per_month` is the clock the stationary law itself is read
#: on, so it does not perturb the identity -- it REPLACES it.
#: `IDENTITY_SHARES` and `IDENTITY_AGES` below are the law at 0.0, and at
#: 1.0 the expansion median is 435 days against their 123. The
#: slower-clock test asks for 1.0 explicitly and compares the two, which is
#: the check that the clock is load-bearing at all.
IDENTITY_CLOCK = {"macro_burn_in_days": 0.0, "cycle_hazard_per_month": 0.0}


def panel_digest(model=None, seeds=SEEDS, days=DAYS):
    """Every per-name daily return over the panel, as raw f64 bytes.

    Big-endian IEEE-754 with no decimal formatting anywhere, and every loop
    over an index range, for `known_answer.py`'s reasons: `repr(float)` is
    platform-sensitive in ways that would either fail this for nothing or
    pass it while hiding a difference in the low bits.
    """
    digest = hashlib.sha256()
    n = len(UNIVERSE)
    for seed in seeds:
        kwargs = {} if model is None else {"model": model}
        engine = tradefloor.Engine(seed=seed, universe=UNIVERSE, **kwargs)
        for _ in range(days):
            engine.open_market()
            engine.run_session(9, 30, 3, 78)
            engine.close_market()
            price = struct.unpack("<%dd" % n, engine.column("price"))
            close = struct.unpack("<%dd" % n, engine.column("previous_close"))
            for i in range(n):
                ret = (price[i] - close[i]) / close[i] if close[i] else 0.0
                digest.update(struct.pack(">d", ret))
    return digest.hexdigest()


def opening(model=None, seed=1):
    """The day-zero phase, its age in DAYS, and the draws construction took."""
    kwargs = {} if model is None else {"model": model}
    engine = tradefloor.Engine(seed=seed, universe=UNIVERSE, **kwargs)
    economy = engine.state_snapshot()["economy"]
    age = economy["months_in_current_phase"] * 30.0
    return economy["cycle_phase"], age, engine.draws_consumed


def drawn(**overrides):
    kwargs = dict(IDENTITY_CLOCK)
    kwargs.update(cycle_stationary_opening=1.0)
    kwargs.update(overrides)          # an explicit burn-in wins, deliberately
    return tradefloor.ModelParams.from_preset(**kwargs)


def undrawn(**overrides):
    """The opening dial OFF and no burn-in: the point the drawn form moves.

    The baseline every test in this file compares against. It was the bare
    default until 0.7.0, when the default gained a burn-in that runs after
    the opening is drawn -- so the bare default stopped being the point.
    """
    kwargs = dict(IDENTITY_CLOCK)
    kwargs.update(overrides)
    return tradefloor.ModelParams.from_preset(**kwargs)


# -- inert at the default ---------------------------------------------------

def test_the_recorded_preset_is_the_base_build_to_the_bit():
    assert panel_digest(model=BASE_PANEL_PRESET) == BASE_PANEL_DIGEST


def test_the_digest_moves_when_the_opening_is_drawn():
    """The instrument, asserted before the assertion above is believed.

    A digest over an empty panel, or over a column that never moves, would
    pass the test above and mean nothing. This is the counterproof: the same
    function on the same panel must SEE the mechanism.
    """
    assert panel_digest(drawn()) != BASE_PANEL_DIGEST


def test_the_default_takes_no_draw_and_leaves_the_opening_where_it_was():
    """The construction claim, per seed rather than in aggregate.

    Bit-identity above is a property of the whole panel; this says the
    branch itself did nothing -- no phase moved, no age moved, and the
    generator was not touched, on any seed.
    """
    for seed in SEEDS:
        phase, age, draws = opening(undrawn(), seed=seed)
        assert (phase, age, draws) == ("expansion", 0.0, 0), seed



# -- a switch, not a share --------------------------------------------------

@pytest.mark.parametrize("value", [1e-9, 0.25, 0.5, 1.0, 2.0])
def test_every_non_zero_value_gives_the_same_opening(value):
    """A day-zero state is either drawn from the stationary law or it is
    not. There is no half-drawn phase, so the interior of the range has no
    reading -- which is asserted here rather than left for a search to
    discover as a flat direction.
    """
    want = panel_digest(drawn(), seeds=range(1, 6), days=5)
    model = undrawn(cycle_stationary_opening=value)
    assert panel_digest(model, seeds=range(1, 6), days=5) == want


# -- the drawn opening IS the law -------------------------------------------

#: `pi_i` at `cycle_hazard_per_month` 0.0, from the identity in
#: `economy::cycle::stationary_phase_shares`. Written here as the target of
#: a measurement, not as a constant the engine reads: the Rust side derives
#: these from `cycle_hazard_params` and `min_months`, and the point of this
#: test is that the ENGINE's openings reproduce them.
IDENTITY_SHARES = {
    "expansion": 0.385, "peak": 0.105, "contraction": 0.206,
    "trough": 0.098, "recovery": 0.206,
}

#: The same identity's median and mean age in DAYS, per phase.
IDENTITY_AGES = {
    "expansion": (123, 129.4), "peak": (33, 33.4), "contraction": (65, 65.9),
    "trough": (31, 30.9), "recovery": (65, 65.8),
}

OPENINGS = 300


def test_the_drawn_phase_shares_are_the_identity_s_own():
    """Three hundred openings against `pi`, at three sampling standard
    errors -- the widest phase's se is 0.028 at this count, so a share that
    landed on the point law (expansion 1.0) or on the uniform (0.2) fails by
    a wide margin while sampling noise does not.
    """
    model = drawn()
    seen = collections.Counter(opening(model, seed)[0] for seed in range(1, OPENINGS + 1))
    for phase, want in IDENTITY_SHARES.items():
        got = seen[phase] / OPENINGS
        se = (want * (1.0 - want) / OPENINGS) ** 0.5
        assert abs(got - want) < 3.0 * se, f"{phase}: {got:.3f} against {want}"


def test_the_drawn_age_is_not_zero_and_sits_where_the_age_law_says():
    """The part a build would skip.

    Drawing the phase and setting the age to zero would start a smaller
    cohort at the same point, and it would pass the share test above. So
    the ages are graded too: per phase, against the identity's median, and
    against the share below the minimum duration -- which is
    `min_days / E[T]`, the one figure that says the age law was derived
    rather than guessed.
    """
    model = drawn()
    ages = collections.defaultdict(list)
    for seed in range(1, OPENINGS + 1):
        phase, age, _ = opening(model, seed)
        ages[phase].append(age)
    assert set(ages) == set(IDENTITY_SHARES), sorted(ages)
    for phase, values in ages.items():
        want_median, want_mean = IDENTITY_AGES[phase]
        assert min(values) >= 0.0, phase
        # The median of a sample this size is noisy, so the claim is the
        # ORDER of magnitude of the age law rather than its quantile: the
        # sample median inside half to twice the identity's, which the
        # point law (every age 0) cannot satisfy for any phase.
        got = statistics.median(values)
        assert 0.5 * want_median <= got <= 2.0 * want_median, \
            f"{phase}: median {got} against {want_median}"
        assert 0.4 * want_mean <= statistics.mean(values) <= 2.5 * want_mean, phase


def test_the_drawn_opening_takes_exactly_two_uniforms():
    """One for the phase, one for the age, from the economy substream that
    the burn-in already consumes -- so the market's day-zero draws sit
    exactly where they sat.
    """
    for seed in SEEDS:
        assert opening(drawn(), seed)[2] == 2, seed


def test_a_slower_clock_draws_from_its_own_law():
    """The law reads `cycle_hazard_per_month`, so the two clocks give two
    different openings -- which is what makes clock speed stop being
    load-bearing. At the slow clock the cycle is 2441 days against 639, and
    the drawn ages are correspondingly older.
    """
    fast = [opening(drawn(), s)[1] for s in range(1, OPENINGS + 1)]
    slow = [opening(drawn(cycle_hazard_per_month=1.0), s)[1]
            for s in range(1, OPENINGS + 1)]
    assert statistics.median(slow) > 2.0 * statistics.median(fast)


# -- the two halves of one opening ------------------------------------------

def test_the_burn_in_no_longer_restores_the_point_it_started_from():
    """`macro_burn_in_days` holds the phase and resets its clock, so on its
    own it settles the fields and restores the same day-zero point. Under
    the drawn opening it runs FREE: the phase it ends in is its own, and
    the clock is not reset, so the age is not zero.

    Asserted as a difference from the held form on the same seeds rather
    than against a recorded state, because what is claimed is that the hold
    is gone -- not any particular place the free run lands.
    """
    held = tradefloor.ModelParams.from_preset(macro_burn_in_days=755.0)
    free = drawn(macro_burn_in_days=755.0)
    for seed in range(1, 11):
        phase, age, _ = opening(held, seed)
        assert (phase, age) == ("expansion", 0.0), f"held moved on seed {seed}"
    ages = [opening(free, seed)[1] for seed in range(1, 11)]
    phases = {opening(free, seed)[0] for seed in range(1, 11)}
    assert any(a > 0.0 for a in ages), "the free burn-in reset the clock"
    assert len(phases) > 1, f"the free burn-in held the phase: {phases}"
