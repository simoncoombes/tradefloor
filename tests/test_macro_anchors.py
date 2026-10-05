"""Unemployment's anchor and oil's interior (issues #170, #171, #172).

Five switches, each 0.0 on every shipped preset and a branch at zero:

- `unemployment_natural_pull`: the monthly share of unemployment's gap to
  the natural rate closed (0.0 keeps the shipped 0.06).
- `unemployment_okun_coefficient`: Okun's law as its annual coefficient,
  with no recovery term (0.0 keeps the shipped monthly 0.20 and the
  recovery term).
- `unemployment_natural_rate`: the natural rate with no long-term
  unemployment (0.0 keeps the shipped 4.0).
- `oil_inventory_reversion`: the daily share of oil inventory's gap to 50
  closed (0.0 keeps the pure integrator).
- `oil_inflation_passthrough`: inflation's response to oil as a multiple
  of the shipped 0.01 a dollar, the same either side of 81 (0.0 keeps the shipped three-way branch).

These tests hold: every preset carries them at 0.0 and its digest does not
see them there; on pt-v20 unemployment leaves its floor under Okun's law
read as annual; inventory reversion keeps oil off its clamp on a seed where
the integrator pins it; the pass-through pays a rise and a fall alike; and
a run with every switch on, snapshotted and continued in a fresh process,
matches the run that never stopped.
"""

import json
import pathlib
import subprocess
import sys
import textwrap

import pytest

import tradefloor as tf

import snapshot_codec

UNIVERSE = list(tf.Universe.random(4, seed=1))
SWITCHES = {
    "unemployment_natural_pull": 0.1,
    "unemployment_okun_coefficient": 0.5,
    "unemployment_natural_rate": 4.5,
    "oil_inventory_reversion": 0.002,
    "oil_inflation_passthrough": 1.0,
}


def model(**dials):
    return tf.ModelParams.from_preset("pt-v20", **dials)


def path(seed, days, **dials):
    """The economy after every close, in percent and dollars."""
    e = tf.Engine(seed=seed, universe=UNIVERSE, model=model(**dials))
    out = []
    for _ in range(days):
        e.run_days(1, record=False)
        out.append(e.economy())
    return out


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset_and_silent_in_its_digest(preset):
    params = tf.ModelParams.from_preset(preset)
    values = params.to_dict()
    silent = set(tf.ModelParams.digest_silent_at_zero())
    for name in SWITCHES:
        assert values[name] == 0.0
        assert name in silent
        assert tf.ModelParams.from_preset(preset, **{name: -0.0}).fingerprint == preset


@pytest.mark.parametrize("name", sorted(SWITCHES))
def test_off_zero_each_switch_is_a_custom_model(name):
    assert model(**{name: SWITCHES[name]}).fingerprint.startswith("custom-")


@pytest.mark.parametrize("name, bad", [
    ("unemployment_natural_pull", 1.5),
    ("unemployment_okun_coefficient", 3.0),
    ("unemployment_natural_rate", 9.0),
    ("oil_inventory_reversion", -0.1),
    ("oil_inflation_passthrough", 3.5),
])
def test_out_of_range_values_are_refused(name, bad):
    with pytest.raises(Exception, match=name):
        model(**{name: bad})


def test_unemployment_leaves_its_floor_under_an_annual_okun_law():
    """Issue #172. On pt-v20 at seed 102 the shipped release holds the rate
    at its 2.5 floor on every one of 504 sessions; with Okun's law read as
    the annual relation it states, it never touches the floor."""
    off = [d["unemployment_rate"] for d in path(102, 504)]
    on = [d["unemployment_rate"] for d in path(102, 504, unemployment_okun_coefficient=0.5)]
    assert sum(u <= 2.5 for u in off) / len(off) > 0.9
    assert min(on) > 2.8


def test_the_natural_pull_and_rate_move_where_unemployment_sits():
    base = dict(unemployment_okun_coefficient=0.5)
    weak = path(103, 504, **base)
    strong = path(103, 504, unemployment_natural_pull=0.2, **base)
    higher = path(103, 504, unemployment_natural_rate=5.0, **base)

    def gap(p):
        return sum(d["unemployment_rate"] - d["structural_unemployment"] for d in p) / len(p)

    # A stronger pull closes more of the gap to the natural rate.
    assert abs(gap(strong)) < abs(gap(weak))
    # A higher natural rate moves the NAIRU and the rate together.
    assert min(d["structural_unemployment"] for d in higher) >= 5.0 + 0.3 * 0.5 - 1e-9
    assert (sum(d["unemployment_rate"] for d in higher)
            > sum(d["unemployment_rate"] for d in weak) + 0.5 * len(weak))


def test_inventory_reversion_keeps_oil_off_its_clamp():
    """Issue #170. On pt-v20 at seed 101 inventory wanders to its 100
    ceiling, the pressure saturates and oil sits at its 35 floor on most of
    1,008 sessions. With inventory reverting toward 50 it stays near the
    dead zone and oil touches a clamp on few sessions."""
    off = path(101, 1008)
    on = path(101, 1008, oil_inventory_reversion=0.002)

    def clamped(p):
        return sum(d["oil_price"] <= 35.0 or d["oil_price"] >= 150.0 for d in p) / len(p)

    assert max(d["oil_inventory_level"] for d in off) > 95.0
    assert clamped(off) > 0.5
    inventory = [d["oil_inventory_level"] for d in on]
    assert 25.0 < min(inventory) and max(inventory) < 75.0
    assert clamped(on) < 0.1


def _release_day(seed, preset_model):
    """The first session whose close publishes a monthly release, read off
    the inflation rate, which moves only then."""
    e = tf.Engine(seed=seed, universe=UNIVERSE, model=preset_model)
    last = e.economy()["inflation_rate"]
    for day in range(1, 60):
        e.run_days(1, record=False)
        now = e.economy()["inflation_rate"]
        if now != last:
            return day
        last = now
    raise AssertionError("no release in 60 sessions")


@pytest.mark.parametrize("passthrough", [0.0, 1.0])
def test_the_pass_through_is_symmetric_about_its_anchor_only_when_set(passthrough):
    """Issue #171. Oil pinned the day before a release at 66, 81 and 96:
    the shipped branch pays the rise from 81 and almost none of the fall; the switch
    pays them equal and opposite."""
    m = model(oil_inflation_passthrough=passthrough)
    day = _release_day(5, m)

    def inflation_at(oil):
        e = tf.Engine(seed=5, universe=UNIVERSE, model=m)
        e.run_days(day - 1, record=False)
        e.pin_macro(oil_price=oil)
        e.run_days(1, record=False)
        return e.economy()["inflation_rate"]

    mid = inflation_at(81.0)
    up = inflation_at(96.0) - mid
    down = inflation_at(66.0) - mid
    if passthrough == 0.0:
        # (96 - 80) * 0.01 less (81 - 80) * 0.01 up; 66 is in the dead
        # zone, so down only gives back the 0.01 that 81 pays.
        assert up == pytest.approx(0.15, abs=1e-9)
        assert down == pytest.approx(-0.01, abs=1e-9)
    else:
        assert up == pytest.approx(0.15, abs=1e-9)
        assert up + down == pytest.approx(0.0, abs=1e-9)


_RESUME = textwrap.dedent("""
    import json, sys
    sys.path.insert(0, {tests!r})
    import tradefloor as tf
    import snapshot_codec
    doc = json.loads(open({path!r}).read())
    engine = tf.Engine(seed=doc["seed"],
                       universe=tf.Universe.from_json(doc["universe"]),
                       model=tf.ModelParams.from_preset("pt-v20", **doc["dials"]))
    engine.restore_state(snapshot_codec.loads(doc["snapshot"]))
    out = []
    for _ in range(doc["more"]):
        engine.run_days(1, record=False)
        out.append(snapshot_codec.dumps(engine.state_snapshot()))
    print(json.dumps(out))
""")


def test_a_run_with_every_switch_on_resumes_in_a_fresh_process(tmp_path):
    """The switches add no state, so a snapshot taken under them restores
    in a new interpreter and runs on exactly as the run that never stopped,
    across monthly releases and an OPEC decision."""
    universe = tf.Universe.random(4, seed=1)
    m = model(**SWITCHES)
    control = tf.Engine(seed=9, universe=universe, model=m)
    paused = tf.Engine(seed=9, universe=universe, model=m)
    control.run_days(30, record=False)
    paused.run_days(30, record=False)
    more = 70
    doc = {"seed": 9, "dials": SWITCHES, "universe": universe.to_json(), "more": more,
           "snapshot": snapshot_codec.dumps(paused.state_snapshot())}
    p = tmp_path / "paused.json"
    p.write_text(json.dumps(doc))
    script = _RESUME.format(tests=str(pathlib.Path(__file__).resolve().parent), path=str(p))
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          check=True)
    resumed = json.loads(done.stdout)
    expected = []
    for _ in range(more):
        control.run_days(1, record=False)
        expected.append(snapshot_codec.dumps(control.state_snapshot()))
    assert resumed == expected
