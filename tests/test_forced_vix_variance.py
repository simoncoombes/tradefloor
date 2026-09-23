"""A forced VIX that sets the market's volatility: `Scenario(vix_sets_variance=True)`.

Off, which is the default and every scenario written before the switch, a
forced VIX reaches the market factor's variance the way the model's own VIX
does, one GARCH step a close toward the level that VIX implies. On, every
session the scenario forces the VIX closes with the variance SET to that
level (`MarketVarianceState::close_day_forced`). These tests pin the three
things the switch promises: nothing moves when it is off, the level arrives
at once when it is on, and the free law picks up from it without a jump.
"""

import array
import json

import pytest

import tradefloor as tf

UNIVERSE = list(tf.Universe.random(12, seed=5))

# pt-v19 off the excursion, as the candidate LMN-Q25A375 is: the ratio's
# denominator is the anchor, so the law's level is a function of the VIX alone.
FLAT = dict(market_vol_vix_excursion=0.0, market_vol_vix_exponent=4.0,
            market_vol_vix_exponent_below=2.5)


def model(**dials):
    return tf.ModelParams.from_preset("pt-v19", **dials)


def engine(m, seed=7, burn=10):
    e = tf.Engine(seed=seed, universe=UNIVERSE, model=m)
    e.run_days(burn, record=False)
    return e


def prices(e):
    p = array.array("d")
    p.frombytes(e.prices())
    return list(p)


def variance(e):
    """(mixture, day_factor, fast, slow, prev_day_factor, smoothed_vix)."""
    return e.state_snapshot()["market_variance"]


def clamp(m, v):
    base = m.market_factor_sigma ** 2
    return max(min(v, base * m.market_vol_ceiling_multiple),
               base * m.market_vol_floor_multiple)


def law_level(m, targets):
    """What a forced close writes, from the targets it reports."""
    fast, slow = targets
    w = m.market_vol_slow_weight
    return clamp(m, (1.0 - w) * clamp(m, fast) + w * clamp(m, slow))


def drive(e, scenario, days, first=10):
    out = []
    for day in range(days):
        scenario.apply(e, day)
        e.run_days(1, record=False, first_day=first + day)
        out.append((variance(e), e.market_variance_target()))
    return out


# --------------------------------------------------------------------------
# Off: bit for bit what it was
# --------------------------------------------------------------------------

@pytest.mark.parametrize("dials", [{}, FLAT], ids=["pt-v19", "flat"])
def test_off_is_the_plain_pin_bit_for_bit(dials):
    """A scenario with the switch off, one that never heard of it and a
    hand-written pin loop are one market."""
    m = model(**dials)
    a, b, c = engine(m), engine(m), engine(m)
    explicit = tf.Scenario(vix_sets_variance=False).hold(vix=45.0)
    implicit = tf.Scenario().hold(vix=45.0)
    for day in range(8):
        explicit.apply(a, day)
        implicit.apply(b, day)
        c.pin_macro(vix=45.0)
        for e in (a, b, c):
            e.run_days(1, record=False, first_day=10 + day)
    assert prices(a) == prices(b) == prices(c)
    assert a.state_hash() == b.state_hash() == c.state_hash()
    assert not a.vix_sets_variance_pending


def test_off_serialises_and_fingerprints_as_before():
    """No key is written while off, so every recorded document and every
    fingerprint over one is unchanged."""
    plain = tf.Scenario("s").hold(vix=30.0)
    off = tf.Scenario("s", vix_sets_variance=False).hold(vix=30.0)
    assert plain.to_json(5) == off.to_json(5)
    assert "vix_sets_variance" not in plain.to_json(5)
    assert plain.fingerprint == off.fingerprint
    assert "vix_sets_variance" not in plain.document()


def test_a_log_without_the_mark_is_the_log_it_was():
    e = engine(model())
    e.pin_macro(vix=30.0)
    entry = [x for x in e.order_log if x["op"] == "pin_macro"][-1]
    assert entry["fields"] == {"vix": 30.0}


# --------------------------------------------------------------------------
# On: the law's own level, at once
# --------------------------------------------------------------------------

@pytest.mark.parametrize("dials", [{}, FLAT], ids=["pt-v19", "flat"])
def test_on_a_held_vix_gives_the_laws_level_on_the_first_close(dials):
    m = model(**dials)
    e = engine(m)
    for (mv, targets) in drive(e, tf.Scenario(vix_sets_variance=True).hold(vix=45.0), 5):
        mix, _, fast, slow = mv[0], mv[1], mv[2], mv[3]
        assert fast == clamp(m, targets[0])
        assert slow == clamp(m, targets[1])
        assert mix == pytest.approx(law_level(m, targets), rel=1e-15)


def test_on_the_targets_are_the_free_laws_and_the_state_is_already_there():
    """Off the excursion the targets depend on the VIX alone, so the forced
    run and the free one read the same targets on the same close; the forced
    one is AT them and the free one a step toward them."""
    m = model(**FLAT)
    on, off = engine(m), engine(m)
    (mv_on, t_on), = drive(on, tf.Scenario(vix_sets_variance=True).hold(vix=45.0), 1)
    (mv_off, t_off), = drive(off, tf.Scenario().hold(vix=45.0), 1)
    assert t_on == t_off
    assert mv_on[0] == law_level(m, t_on)
    assert mv_off[0] < 0.5 * mv_on[0], "the free close is only one step toward the target"


def test_the_level_follows_the_forced_vix_day_by_day():
    m = model(**FLAT)
    e = engine(m)
    path = tf.Scenario(vix_sets_variance=True).hold(vix=15.0).ramp(
        "vix", start=40.0, end=20.0, over=4, begin=2)
    rows = drive(e, path, 7)
    vols = [r[0][0] for r in rows]
    assert vols[1] < vols[2], "the jump to 40 is in the next session's variance"
    assert vols[2] > vols[3] > vols[4] > vols[5], "and it falls as the VIX does"
    for mv, targets in rows:
        assert mv[0] == law_level(m, targets)


def test_the_mark_is_consumed_by_the_close():
    e = engine(model(**FLAT))
    e.pin_macro(vix=45.0, vix_sets_variance=True)
    assert e.vix_sets_variance_pending
    # A later pin of another field the same day does not cancel it.
    e.pin_macro(federal_funds_rate=0.03)
    assert e.vix_sets_variance_pending
    e.run_days(1, record=False, first_day=10)
    assert not e.vix_sets_variance_pending


# --------------------------------------------------------------------------
# Release: the free law carries on from the forced level
# --------------------------------------------------------------------------

def test_release_is_one_ordinary_free_step_from_the_forced_level():
    """The first close after the force is the free GARCH step, taken from the
    level the force left: recomputed here from the reported target and the
    day's factor, it lands on the state to rounding. Nothing is reset and
    nothing jumps."""
    m = model(**FLAT)
    e = engine(m)
    s = tf.Scenario(vix_sets_variance=True).shock(
        "macro.vix", operation="set", value=45.0, at=0, duration=5)
    rows = drive(e, s, 7)
    forced, released = rows[4], rows[5]
    assert forced[0][0] == law_level(m, forced[1])
    a, b, g = m.market_vol_alpha, m.market_vol_beta, m.market_vol_gamma
    f = released[0][4]                       # the release day's own factor
    lev = g if f < 0.0 else 0.0
    want_fast = clamp(m, (1.0 - a - b - 0.5 * g) * released[1][0]
                      + (a + lev) * f * f + b * forced[0][2])
    rho, share = m.market_vol_slow_persistence, m.market_vol_slow_gain
    sa, sb = share * rho, (1.0 - share) * rho
    want_slow = clamp(m, (1.0 - sa - sb) * released[1][1] + sa * f * f + sb * forced[0][3])
    assert released[0][2] == pytest.approx(want_fast, rel=1e-12)
    assert released[0][3] == pytest.approx(want_slow, rel=1e-12)
    # and the release close was not itself forced
    assert released[0][0] != law_level(m, released[1])


# --------------------------------------------------------------------------
# What forces the VIX
# --------------------------------------------------------------------------

def test_an_intervention_on_the_vix_forces_it_too():
    e = engine(model(**FLAT))
    s = tf.Scenario(vix_sets_variance=True).shock(
        "macro.vix", operation="multiply", value=2.0, at=2, duration=2)
    marked = []
    for day in range(6):
        s.apply(e, day)
        marked.append(e.vix_sets_variance_pending)
        e.run_days(1, record=False, first_day=10 + day)
    assert marked == [False, False, True, True, False, False]


def test_the_seed_sweep_marks_the_same_days():
    s = tf.Scenario(vix_sets_variance=True).hold(federal_funds_rate=0.03).ramp(
        "vix", start=20.0, end=40.0, over=3)
    assert s._pin_kwargs(0) == {"federal_funds_rate": 0.03, "vix": 20.0,
                                "vix_sets_variance": True}
    assert "vix_sets_variance" not in tf.Scenario().hold(vix=20.0)._pin_kwargs(0)


def test_on_without_forcing_the_vix_is_refused():
    s = tf.Scenario("rates", vix_sets_variance=True).hold(federal_funds_rate=0.04)
    with pytest.raises(tf.ValidationError, match="never forces the VIX"):
        s.apply(engine(model()), 0)


def test_the_switch_is_a_bool():
    with pytest.raises(tf.ValidationError, match="true or false"):
        tf.Scenario(vix_sets_variance="false")


# --------------------------------------------------------------------------
# The switch travels with the scenario
# --------------------------------------------------------------------------

def test_on_round_trips_and_moves_the_fingerprint():
    on = tf.Scenario("crash", vix_sets_variance=True).hold(vix=15.0).ramp(
        "vix", start=80.0, end=30.0, over=10, begin=5)
    off = tf.Scenario("crash").hold(vix=15.0).ramp(
        "vix", start=80.0, end=30.0, over=10, begin=5)
    assert on.fingerprint != off.fingerprint
    doc = json.loads(on.to_json(20))
    assert doc["vix_sets_variance"] is True
    back = tf.Scenario.from_json(on.to_json(20))
    assert back.vix_sets_variance
    assert back.table(20) == on.table(20)
    assert on.copy().vix_sets_variance and on.without_interventions().vix_sets_variance
    assert "vix_sets_variance: on" in on.describe()


def test_a_recorded_path_document_turns_it_on():
    """The replay's form: a schema-1 path with the key beside it."""
    doc = {"schema": 1, "label": "covid", "days": 3, "vix_sets_variance": True,
           "path": [{"day": i, "vix": v} for i, v in enumerate([14.0, 40.0, 80.0])]}
    s = tf.Scenario.from_json(json.dumps(doc))
    assert s.vix_sets_variance
    with pytest.raises(tf.ValidationError, match="true or false"):
        tf.Scenario.from_json(json.dumps(dict(doc, vix_sets_variance=1)))


def test_a_schema_2_document_keeps_it():
    s = tf.Scenario("oil", vix_sets_variance=True).shock(
        "macro.vix", operation="set", value=50.0, at=1, duration=3)
    back = tf.Scenario.from_json(s.to_json())
    assert back.vix_sets_variance
    assert back.fingerprint == s.fingerprint


def test_the_yaml_block_takes_it():
    text = """version: 1
scenario:
  name: panic
  vix_sets_variance: true
  shocks:
    - target: macro.vix
      operation: set
      value: 60.0
      at: 2
      duration: 5
"""
    s = tf.Scenario.from_yaml(text)
    assert s.vix_sets_variance
    assert not tf.Scenario.from_yaml(text.replace("  vix_sets_variance: true\n", "")).vix_sets_variance


# --------------------------------------------------------------------------
# A fork, a restore and a replay carry the mark
# --------------------------------------------------------------------------

def test_a_snapshot_carries_a_pending_mark_and_only_then():
    m = model(**FLAT)
    e = engine(m)
    assert "vix_sets_variance_pending" not in e.state_snapshot()
    free_hash = e.state_hash()
    e.pin_macro(vix=45.0, vix_sets_variance=True)
    snap = e.state_snapshot()
    assert snap["vix_sets_variance_pending"] is True
    assert e.state_hash() != free_hash
    twin = tf.Engine(seed=7, universe=UNIVERSE, model=m)
    twin.restore_state(snap)
    assert twin.vix_sets_variance_pending
    for x in (e, twin):
        x.run_days(1, record=False, first_day=10)
    assert prices(e) == prices(twin)
    assert variance(e) == variance(twin)


def test_a_replayed_log_reproduces_a_forced_run():
    m = model(**FLAT)
    e = tf.Engine(seed=3, universe=UNIVERSE, model=m)
    for day in range(4):
        e.pin_macro(vix=20.0 + 15.0 * day, vix_sets_variance=day > 0)
        e.open_market()
        e.run_session(9, 30, 3, 60)
        e.close_market()
    marks = [x["fields"].get("vix_sets_variance") for x in e.order_log if x["op"] == "pin_macro"]
    assert marks == [None, True, True, True]
    again = tf.replay(e.order_log, seed=3, universe=UNIVERSE, model=m)
    assert prices(again) == prices(e)
    assert variance(again) == variance(e)
