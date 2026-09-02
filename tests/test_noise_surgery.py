"""Draw surgery in World (noise, phase 2).

Each claim the surgery methods make, stated as a test: unfiring a jump
moves nothing before its day and no draw count; a window on one stream
leaves every other stream's log bit-identical and is the documented
derivation; a transplant reproduces the source at its addresses and
nothing outside them; the check after a surgery's day names a schedule
that moved.
"""

import pytest

import tradefloor as tf
from tradefloor import noise
from tradefloor.counterfactual import World, agree, compare

SEED = 42
UNIVERSE = tf.Universe.random(10, seed=99)
STEPS, TICKS = 2, 20
# The market jump fires on every day: an intensity of one with the VIX
# coupling off, so the comparison `u < 1.0` holds for every uniform the
# stream can draw and only the surgery's 1.0 escapes it.
JUMPY = tf.ModelParams.from_preset("pt-v16", jump_intensity_market=1.0,
                                   jump_vix_coupling=0.0)


class Buyer:
    """Buys a fixed lot of the first name on the first step of every day."""

    def act(self, obs):
        if obs.step % obs.steps_per_day == 0:
            return {obs.tickers[0]: 50}
        return {}


def world(model=None):
    return World(seed=SEED, universe=UNIVERSE, agent=Buyer(),
                 steps_per_day=STEPS, ticks_per_step=TICKS, model=model)


def logs(w, streams, first, last):
    return {s: [tuple(e) for e in w.draws(s, first, last)] for s in streams}


def trace_all(*worlds, first, last):
    for w in worlds:
        for s in noise.STREAMS:
            w.trace(s, first, last)


# -- unfire ------------------------------------------------------------------

def test_unfiring_a_jump_moves_nothing_before_its_day():
    root = world(model=JUMPY).run(3)
    control, unfired = root.fork("control", "unfired")
    agreement = agree(control, unfired)
    assert agreement.identical
    unfired.unfire(4)
    control.run(4)
    unfired.run(4)

    assert (unfired.engine.draws_by_stream()
            == control.engine.draws_by_stream())
    assert unfired.engine.stream_positions() == control.engine.stream_positions()
    rows = list(zip(control.trace, unfired.trace))
    assert all(a["prices"] == b["prices"] for a, b in rows if a["day"] <= 4)
    assert any(a["prices"] != b["prices"] for a, b in rows if a["day"] > 4)
    market = [e for e in unfired.draws("jumps", 4, 4)
              if e.site == "jump_market_u"]
    assert [e.value for e in market] == [noise.NO_FIRE]
    assert unfired.surgeries == [{
        "kind": "unfire", "day": 4, "step": 4 * STEPS, "stream": "jumps",
        "address": market[0].address, "value": noise.NO_FIRE}]

    report = compare(control, unfired, agreement=agreement)
    assert report.divergence.intervention_day == 4
    assert report.divergence.intervention_step == 4 * STEPS
    assert report.divergence.prices == 5 * STEPS
    assert report.as_dict()["fork_agreement"]["identical"]


def test_unfire_refuses_a_day_that_has_run():
    w = world().run(2)
    with pytest.raises(tf.ValidationError, match="has run"):
        w.unfire(1)
    with pytest.raises(tf.ValidationError, match="market open"):
        w.engine.open_market()
        w.unfire(3)


# -- window ------------------------------------------------------------------

def test_a_window_surgery_leaves_every_other_stream_bit_identical():
    root = world().run(2)
    control, windowed, again, other = root.fork(
        "control", "windowed", "again", "other")
    trace_all(control, windowed, again, other, first=2, last=4)
    windowed.window("news", (3, 4), surgery_seed=7)
    again.window("news", (3, 4), surgery_seed=7)
    other.window("news", (3, 4), surgery_seed=8)
    for w in (control, windowed, again, other):
        w.run(3)

    others = [s for s in noise.STREAMS if s != "news"]
    assert logs(windowed, others, 2, 4) == logs(control, others, 2, 4)
    assert logs(windowed, ["news"], 2, 2) == logs(control, ["news"], 2, 2)
    assert logs(windowed, ["news"], 3, 4) != logs(control, ["news"], 3, 4)
    assert logs(again, ["news"], 3, 4) == logs(windowed, ["news"], 3, 4)
    assert logs(other, ["news"], 3, 4) != logs(windowed, ["news"], 3, 4)
    # the same addresses as the control: the schedule did not move
    entries = windowed.draws("news", 3, 4)
    assert ([e.address for e in entries]
            == [e.address for e in control.draws("news", 3, 4)])
    # the values are the documented derivation, in address order
    expected = tf.Engine.surgery_draws(
        SEED, "news", 7, [e.address.kind for e in entries])
    assert [e.value for e in entries] == expected
    assert windowed.surgeries == [{
        "kind": "window", "day": 3, "step": 3 * STEPS, "stream": "news",
        "days": (3, 4), "surgery_seed": 7, "draws": len(entries)}]
    assert (windowed.engine.draws_by_stream()
            == control.engine.draws_by_stream())


def test_a_window_on_the_market_stream_covers_every_tick():
    root = world().run(1)
    control, windowed = root.fork("control", "windowed")
    trace_all(control, windowed, first=1, last=2)
    windowed.window("market", 2, surgery_seed=1)
    control.run(2)
    windowed.run(2)
    n, sectors = len(UNIVERSE), root.engine.day_marks()[0]["sectors"]
    per_tick = 1 + sectors + n + n + 4 * n
    assert windowed.surgeries[0]["draws"] == per_tick * STEPS * TICKS
    assert logs(windowed, ["market"], 1, 1) == logs(control, ["market"], 1, 1)
    a = windowed.draws("market", 2, 2)
    b = control.draws("market", 2, 2)
    assert [(e.address, e.site, e.tag) for e in a] == [
        (e.address, e.site, e.tag) for e in b]
    assert all(x.value != y.value for x, y in zip(a, b))
    others = [s for s in noise.STREAMS if s != "market"]
    assert logs(windowed, others, 1, 2) == logs(control, others, 1, 2)


def test_the_surgery_derivation_is_a_function_of_its_three_seeds():
    kinds = ["uniform", "normal", "normal", "uniform"] * 4
    a = tf.Engine.surgery_draws(42, "jumps", 7, kinds)
    assert a == tf.Engine.surgery_draws(42, "jumps", 7, kinds)
    assert a != tf.Engine.surgery_draws(42, "jumps", 8, kinds)
    assert a != tf.Engine.surgery_draws(42, "market", 7, kinds)
    assert a != tf.Engine.surgery_draws(43, "jumps", 7, kinds)
    assert all(0.0 <= u < 1.0 for u, k in zip(a, kinds) if k == "uniform")
    with pytest.raises(ValueError):
        tf.Engine.surgery_draws(42, "weather", 7, kinds)
    with pytest.raises(ValueError):
        noise.surgery_patches(42, "jumps", 7,
                              [noise.DrawAddress("market", "uniform", 0)])


# -- transplant --------------------------------------------------------------

def test_a_transplant_reproduces_the_source_exactly_and_nothing_outside():
    root = world().run(2)
    control, source, target = root.fork("control", "source", "target")
    trace_all(control, source, target, first=2, last=5)
    source.window("jumps", (3, 4), surgery_seed=3)
    source.run(4)
    target.transplant(source, "jumps", (3, 4))
    control.run(4)
    target.run(4)

    assert logs(target, ["jumps"], 3, 4) == logs(source, ["jumps"], 3, 4)
    assert logs(target, ["jumps"], 3, 4) != logs(control, ["jumps"], 3, 4)
    assert logs(target, ["jumps"], 2, 2) == logs(control, ["jumps"], 2, 2)
    assert logs(target, ["jumps"], 5, 5) == logs(control, ["jumps"], 5, 5)
    others = [s for s in noise.STREAMS if s != "jumps"]
    assert logs(target, others, 2, 5) == logs(control, others, 2, 5)
    # the same draws everywhere makes the same world
    assert target.trace == source.trace
    assert target.digest() == source.digest()
    assert target.surgeries == [{
        "kind": "transplant", "day": 3, "step": 3 * STEPS,
        "stream": "jumps", "days": (3, 4), "source": "source",
        "source_seed": SEED, "draws": len(source.draws("jumps", 3, 4))}]


def test_a_transplant_needs_a_traced_source():
    root = world().run(1)
    source, target = root.fork("source", "target")
    source.run(2)
    with pytest.raises(tf.ValidationError, match="Trace the source"):
        target.transplant(source, "jumps", (1, 2))


def test_a_transplant_refuses_a_roster_of_another_size():
    small = World(seed=SEED, universe=tf.Universe.random(4, seed=1),
                  agent=Buyer(), steps_per_day=STEPS, ticks_per_step=TICKS)
    small.trace("jumps", 0, 0).run(1)
    target = world()
    with pytest.raises(tf.ValidationError, match="instruments"):
        target.transplant(small, "jumps", 0)


# -- point -------------------------------------------------------------------

def test_point_replaces_one_future_draw_and_refuses_a_drawn_one():
    w = world().run(1)
    with pytest.raises(tf.ValidationError, match="has been drawn"):
        w.point(("market", "uniform", 0), 0.5)
    ahead = w.engine.stream_positions()["jumps"][0]
    w.point(("jumps", "uniform", ahead), 0.25)
    assert w.surgeries == [{
        "kind": "point", "day": 1, "step": STEPS, "stream": "jumps",
        "address": ("jumps", "uniform", ahead), "value": 0.25}]
    w.trace("jumps", 1, 1).run(1)
    hit = [e for e in w.draws("jumps", 1, 1) if e.address.index == ahead]
    assert [(e.value, e.site) for e in hit] == [(0.25, "jump_market_u")]


# -- the check after the day -------------------------------------------------

def test_the_check_after_the_day_names_a_moved_schedule():
    w = world().run(1)
    w.unfire(2)
    spec = UNIVERSE[0]
    w.engine.list_instrument(tf.Instrument(
        "ZZZZ", spec.sector, initial_price=spec.initial_price,
        shares_outstanding=spec.shares_outstanding, eps=spec.eps,
        book_value_per_share=spec.book_value_per_share,
        revenue_growth=spec.revenue_growth, avg_volume=spec.avg_volume,
        beta=spec.beta, short_interest=spec.short_interest))
    with pytest.raises(tf.ValidationError, match="did not land"):
        w.run(2)


# -- forks and agreement -----------------------------------------------------

def test_a_fork_carries_the_surgery_and_agree_reports_it_as_engine_state():
    root = world().run(1)
    a, b = root.fork("a", "b")
    assert agree(a, b).identical
    b.unfire(2)
    report = agree(a, b)
    assert not report.identical
    assert report.differences == ["whole engine state"]
    child, = b.fork("child")
    assert child.surgeries == b.surgeries
    assert child.engine.draw_patches() == b.engine.draw_patches()
    child.run(2)
    b.run(2)
    assert child.trace == b.trace
