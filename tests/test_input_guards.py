"""Inputs that used to reach the engine unchecked and turned prices into NaN.

The 0.8.5 audit found the gaps one by one: `pin_macro(vix=-10)` and
`pin_macro(vix=1e6)` made every price NaN; `run_days(volatility=nan)` did
the same, and an hour of 25 or a day_of_week of 9 ran, though `tick()`
refused all of them; `patch_draws` installed an infinite normal;
`set_fundamentals` took an infinite EPS; a negative `set_day` was taken;
`ModelParams` accepted a price cap of zero and a buyback share outside
[0, 1]; and a scenario could hold the VIX at 1000, which made the index NaN
on 9 of 30 seeds, while the model's own chain never goes past its
`vix_ceiling` of 181.33.

Every refusal here is a ValidationError, and every test checks that the
refused call left the engine as it was, which is what makes a refusal safe
to catch and carry on from.
"""

import math
import struct

import pytest

import tradefloor as tf

UNIVERSE = tf.Universe.random(4, seed=111)
CEILING = tf.ModelParams.from_preset().vix_ceiling


def _engine():
    engine = tf.Engine(seed=3, universe=UNIVERSE)
    engine.run_days(2, record=False)
    return engine


def _finite_prices(engine):
    raw = engine.prices()
    return all(math.isfinite(p) for p in struct.unpack(f"<{len(raw) // 8}d", raw))


@pytest.mark.parametrize("vix", [-10.0, 0.0, CEILING + 1.0, 1e6])
def test_pin_macro_refuses_a_vix_the_model_cannot_hold(vix):
    engine = _engine()
    before = engine.state_hash()
    with pytest.raises(tf.ValidationError, match="vix_ceiling"):
        engine.pin_macro(vix=vix)
    assert engine.state_hash() == before


def test_pin_macro_takes_a_real_level_above_an_old_presets_ceiling():
    """Presets before pt-v19 clamp the VIX at 80, and the real close on 16
    March 2020 was 82.69. The bound is the higher of the model's ceiling and
    the default preset's, so that pin still works there."""
    engine = tf.Engine(seed=3, universe=UNIVERSE, model="pt-v18")
    assert engine.model_params["vix_ceiling"] == 80.0
    engine.pin_macro(vix=82.69)
    assert engine.macro_fields["vix"] == 82.69
    with pytest.raises(tf.ValidationError, match="vix_ceiling"):
        engine.pin_macro(vix=CEILING + 1.0)


def test_a_session_may_start_at_a_minute_past_59():
    """`tick` takes one minute and wants it in range. A session takes the
    minute it starts at, and `run_session(9, 30 + i * 30, ...)` has always
    been a way to write 10:00; the start only has to fall inside the day."""
    engine = _engine()
    engine.open_market()
    engine.run_session(9, 60, 3, 10)
    with pytest.raises(tf.ValidationError):
        engine.tick(9, 60, 3)


def test_pin_macro_takes_the_ceiling_itself():
    engine = _engine()
    engine.pin_macro(vix=CEILING)
    engine.run_days(2, record=False)
    assert _finite_prices(engine)


@pytest.mark.parametrize("kwargs", [
    {"volatility": float("nan")},
    {"volatility": -1.0},
    {"volatility": float("inf")},
    {"hour": 25},
    {"hour": -1},
    {"hour": 23, "minute": 60},
    {"day_of_week": 9},
])
@pytest.mark.parametrize("call", ["run_days", "run_session"])
def test_the_clock_and_the_volatility_are_checked_as_tick_checks_them(call, kwargs):
    engine = _engine()
    before = engine.state_hash()
    log = len(engine.order_log)
    with pytest.raises(tf.ValidationError):
        if call == "run_days":
            engine.run_days(1, record=False, **kwargs)
        else:
            clock = {"hour": 9, "minute": 30, "day_of_week": 3}
            clock.update({k: v for k, v in kwargs.items() if k in clock})
            engine.run_session(clock["hour"], clock["minute"],
                               clock["day_of_week"], 10,
                               **{k: v for k, v in kwargs.items()
                                  if k == "volatility"})
    # Refused before a day opened, so nothing was logged or run.
    assert engine.state_hash() == before
    assert len(engine.order_log) == log


@pytest.mark.parametrize("patch", [
    ("market", "normal", 0, float("inf")),
    ("market", "normal", 0, float("nan")),
    ("jumps", "uniform", 0, 1.5),
    ("jumps", "uniform", 0, -0.1),
])
def test_patch_draws_refuses_a_value_no_generator_gives(patch):
    engine = _engine()
    good = ("market", "normal", 1, 0.5)
    with pytest.raises(tf.ValidationError):
        engine.patch_draws([good, patch])
    # Checked before any is installed.
    assert engine.draw_patches() == []


def test_patch_draws_still_takes_a_uniform_of_one():
    """1.0 is the value that stops an event from firing, and is allowed."""
    engine = _engine()
    engine.patch_draws([("jumps", "uniform", 0, 1.0)])
    assert engine.draw_patches() == [("jumps", "uniform", 0, 1.0)]


@pytest.mark.parametrize("column", [0, 1, 2])
@pytest.mark.parametrize("bad", [float("inf"), float("-inf")])
def test_set_fundamentals_refuses_an_infinite_value(column, bad):
    engine = _engine()
    before = engine.fundamentals()
    columns = [list(c) for c in before]
    columns[column][0] = bad
    with pytest.raises(tf.ValidationError, match="finite"):
        engine.set_fundamentals(*columns)
    assert engine.fundamentals() == before


def test_a_negative_set_day_is_refused():
    with pytest.raises(tf.ValidationError):
        _engine().set_day(-5)


@pytest.mark.parametrize("dial, value", [
    ("price_hard_cap", 0.0),
    ("price_hard_cap", -1.0),
    ("price_hard_cap", float("nan")),
    ("buyback_payout_share", -0.1),
    ("buyback_payout_share", 1.5),
])
def test_model_params_refuses_a_dial_outside_its_domain(dial, value):
    with pytest.raises(tf.ValidationError, match=dial):
        tf.ModelParams.from_preset("pt-v20", **{dial: value})


def test_a_scenario_cannot_hold_the_vix_above_the_ceiling():
    with pytest.raises(tf.ValidationError, match="vix_ceiling"):
        tf.Scenario().hold(vix=1000.0)


def test_a_scenario_cannot_set_the_vix_above_the_ceiling():
    with pytest.raises(tf.ValidationError, match="vix_ceiling"):
        tf.Scenario().shock("macro.vix", operation="set", value=1000.0, at=1)


def test_a_relative_vix_shock_is_written_at_most_at_the_ceiling():
    """x3.5 on a VIX already near the top computes a level the chain cannot
    hold. The ceiling is written instead, and the firing records it."""
    engine = _engine()
    engine.pin_macro(vix=100.0)
    scenario = tf.Scenario().shock("macro.vix", operation="multiply",
                                   value=3.5, at=0, duration=2)
    scenario.apply(engine, 0)
    assert engine.macro_fields["vix"] == CEILING
    assert scenario.log[0].new == CEILING
    engine.run_days(2, record=False)
    assert _finite_prices(engine)


def test_fundamentals_and_set_avg_volume_each_carry_their_own_docstring():
    """`Engine.fundamentals` opened with the `avg_volume` text, which had
    slid onto it from `set_avg_volume`, and `set_avg_volume` had none."""
    fundamentals = tf.Engine.fundamentals.__doc__ or ""
    avg_volume = tf.Engine.set_avg_volume.__doc__ or ""
    assert "avg_volume" not in fundamentals.splitlines()[0]
    assert "earnings per share" in " ".join(fundamentals.split())
    assert avg_volume.startswith("Write the `avg_volume` column")


def test_pin_macro_says_it_writes_one_day():
    """Its docstring opened "Pin one or more macro series to given values",
    and a VIX pinned at 45 read 38.7 at the next open. It writes today's
    value; `Scenario.hold` is what holds one."""
    first = " ".join((tf.Engine.pin_macro.__doc__ or "").split("\n\n")[0].split())
    assert first.startswith("Write today's value")
    assert "Scenario.hold" in first
