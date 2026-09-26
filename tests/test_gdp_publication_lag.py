"""The published GDP growth figure (`gdp_publication_lag`).

The growth the engine reports -- `macro_fields["gdp_growth"]`, the recorded
`macro_table()` and what reads them -- is, under the dial, a QUARTERLY
figure as the BEA publishes one: the mean of the true daily growth over the
macro calendar's quarter (63 sessions on the 252-session calendar, 90 on
the shipped one; day 0, the opening, is the first day of quarter 0),
released on the close `gdp_publication_lag` sessions after the quarter's
last day. Before the first release the opening growth is published. The
true daily growth stays internal: output and earnings compound it, a pin
sets it at once, and `state_snapshot()` carries it.

These tests hold: the dial is inert at 0.0 on every preset; under it the
published figure is each quarter's mean, released exactly `lag` sessions
after the quarter ends and at no other close, so the step a turn of the
cycle puts on the daily growth never reaches it on the day; the lag moves
no price and no draw; and the snapshot, the state hash, a fork and a
replayed checkpoint carry the figure's state only while the dial is set.
"""

import struct

import pytest

import tradefloor as tf
from tradefloor.interventions import TARGETS
from tradefloor.manifest import state_hash

UNIVERSE = list(tf.Universe.random(4, seed=1))
LAG = 21


def lagged(lag=LAG, preset="pt-v20"):
    return tf.ModelParams.from_preset(preset, gdp_publication_lag=float(lag))


def quarter_length(preset):
    days = tf.ModelParams.from_preset(preset).to_dict()["macro_calendar_days_per_year"]
    return 90 if days == 365.0 else 3 * round(days / 12.0)


def true_growth(engine):
    """The economy's own growth, in percent."""
    return engine.state_snapshot()["economy"]["gdp_growth"]


def prices(engine):
    raw = engine.prices()
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def walk(engine, days, pins=None):
    """Close `days` sessions, pinning `pins[day]` (fractional growth) before
    that day's session. Returns the true growth (percent) and the published
    figure (fractional) after construction and after every close, so both
    `[d]` are read after `d` closes."""
    pins = pins or {}
    true, published = [true_growth(engine)], [engine.macro_fields["gdp_growth"]]
    for d in range(1, days + 1):
        if d in pins:
            engine.pin_macro(gdp_growth=pins[d])
        engine.run_days(1)
        true.append(true_growth(engine))
        published.append(engine.macro_fields["gdp_growth"])
    return true, published


def expected(true, q, lag):
    """What an observer should read after each close: the mean of each
    quarter's true growth, summed in order as the engine sums it, released
    on the close `lag` after the quarter's last day; the opening before."""
    out, current = [], true[0] / 100.0
    releases = {}
    for k in range(len(true) // q + 1):
        days = range(k * q, (k + 1) * q)
        if days[-1] < len(true):
            total = 0.0
            for d in days:
                total += true[d]
            releases[days[-1] + lag] = total / q / 100.0
    for d in range(len(true)):
        if d in releases:
            current = releases[d]
        out.append(current)
    return out, sorted(releases)


# Every preset through pt-v19. pt-v20 sets gdp_publication_lag to 21 since its graded
# arm (2026-09-26; design repository, programme/ptv20-registration.md),
# which the test below holds. Was parametrized over every preset.
@pytest.mark.parametrize("preset", [p for p in tf.preset_names() if p != "pt-v20"])
def test_off_on_every_shipped_preset(preset):
    """0.0 on every preset: the growth reported is the growth the economy
    runs at, a pin reads straight back, and the snapshot carries no state."""
    assert tf.ModelParams.from_preset(preset).to_dict()["gdp_publication_lag"] == 0.0
    e = tf.Engine(seed=1, universe=UNIVERSE, model=preset)
    e.run_days(2)
    assert e.macro_fields["gdp_growth"] == true_growth(e) / 100.0
    e.pin_macro(gdp_growth=-0.031)
    assert e.macro_fields["gdp_growth"] == -0.031
    assert "gdp_publication" not in e.state_snapshot()["economy"]


def test_pt_v20_sets_the_graded_arms_value():
    assert tf.ModelParams.from_preset("pt-v20").to_dict()["gdp_publication_lag"] == 21.0


@pytest.mark.parametrize("preset", ["pt-v20", "pt-v16"])
def test_the_published_figure_is_the_quarter_mean_lag_sessions_late(preset):
    """On the session calendar (63-session quarters) and the shipped one
    (90): after every close the published figure is the last quarter
    released, its mean true growth, and the opening growth before that."""
    q = quarter_length(preset)
    assert q == {"pt-v20": 63, "pt-v16": 90}[preset]
    e = tf.Engine(seed=2, universe=UNIVERSE, model=lagged(preset=preset))
    true, published = walk(e, 3 * q + LAG + 5)
    want, releases = expected(true, q, LAG)
    assert published == want
    assert releases[:3] == [q - 1 + LAG, 2 * q - 1 + LAG, 3 * q - 1 + LAG]
    # The figure moved at every release, or the walk proved nothing.
    assert len({published[r] for r in releases[:3]} | {published[0]}) == 4


def test_the_figure_changes_only_on_a_release_day():
    """The step a turn puts on the daily growth reaches no one on the day:
    the published figure moves on release days and on no other close, and
    the true growth steps on days it does not."""
    q = quarter_length("pt-v20")
    e = tf.Engine(seed=3, universe=UNIVERSE, model=lagged())
    # A pinned drop of 3 pp, the size of the shock entering a contraction.
    true, published = walk(e, 4 * q, pins={30: -0.01})
    moved = [d for d in range(1, len(published)) if published[d] != published[d - 1]]
    assert moved and set(moved) <= {(k + 1) * q - 1 + LAG for k in range(4)}
    stepped = [d for d in range(1, len(true)) if abs(true[d] - true[d - 1]) >= 1.5]
    assert 30 in stepped and not set(stepped) & set(moved)


def test_the_opening_growth_is_published_until_the_first_release():
    q = quarter_length("pt-v20")
    e = tf.Engine(seed=4, universe=UNIVERSE, model=lagged())
    opening = e.macro_fields["gdp_growth"]
    assert opening == true_growth(e) / 100.0
    e.pin_macro(gdp_growth=-0.02)
    # A pin is the true growth at once and is not reported.
    assert true_growth(e) == pytest.approx(-2.0)
    assert e.macro_fields["gdp_growth"] == opening
    _, published = walk(e, q - 1 + LAG)
    assert published[:-1] == [opening] * (q - 1 + LAG)
    assert published[-1] != opening


def test_a_growth_intervention_reads_the_true_growth():
    """A scenario's operation is anchored on the growth the economy runs at,
    the value `pin_macro` writes, not on a figure months stale."""
    e = tf.Engine(seed=5, universe=UNIVERSE, model=lagged())
    e.pin_macro(gdp_growth=-0.02)
    target = TARGETS["macro.growth"]
    assert target.read(e) == pytest.approx(-0.02)
    assert e.macro_fields["gdp_growth"] != pytest.approx(-0.02)


def test_the_lag_moves_no_price_and_no_draw():
    """Only what is reported moves: the same market under lag 0 and lag N
    trades the same prices, takes the same draws and runs the same true
    growth, through a pin and past a release."""
    runs = {}
    for lag in (0, LAG):
        e = tf.Engine(seed=6, universe=UNIVERSE, model=lagged(lag))
        true, _ = walk(e, 90, pins={20: -0.01})
        runs[lag] = (prices(e), e.draws_consumed, true)
    assert runs[0] == runs[LAG]


def test_the_macro_table_reports_the_published_figure():
    pyarrow = pytest.importorskip("pyarrow")
    q = quarter_length("pt-v20")
    e = tf.Engine(seed=7, universe=UNIVERSE, model=lagged())
    published = []
    for _ in range(q + LAG + 3):
        # A day's row is recorded before its close, so it carries the
        # figure published as that day traded.
        published.append(e.macro_fields["gdp_growth"])
        e.run_days(1)
    table = pyarrow.table(e.macro_table())
    assert table.column("gdp_growth").to_pylist() == published
    assert len(set(published)) == 2


def test_the_snapshot_and_the_hash_carry_the_state_only_while_set():
    # pt-v20 with the dial off: pt-v20 itself sets it since its graded arm (2026-09-26); was model="pt-v20"
    off = tf.Engine(seed=8, universe=UNIVERSE, model=lagged(0))
    off.run_days(3)
    assert "gdp_publication" not in off.state_snapshot()["economy"]
    assert state_hash(off.state_snapshot()) == off.state_hash()

    q = quarter_length("pt-v20")
    on = tf.Engine(seed=8, universe=UNIVERSE, model=lagged())
    on.run_days(q + 5)
    snap = on.state_snapshot()
    gdp = snap["economy"]["gdp_publication"]
    # Days q..q+5 of quarter 1 averaged; quarter 0 awaiting release.
    assert gdp["quarter"] == 1 and gdp["count"] == 6
    assert gdp["pending_days"] == [q - 1 + LAG] and len(gdp["pending_values"]) == 1
    # The Python twin agrees with the engine, and the state is in both: a
    # snapshot alike but for one pending figure hashes apart.
    assert state_hash(snap) == on.state_hash()
    other = on.state_snapshot()
    other["economy"]["gdp_publication"]["pending_values"] = [1.25]
    assert state_hash(other) != state_hash(snap)
    twin = tf.Engine(seed=8, universe=UNIVERSE, model=lagged())
    twin.restore_state(other)
    assert twin.state_hash() == state_hash(other) != on.state_hash()
    # ... and the restored figure is the one released.
    twin.run_days(LAG - 5)
    assert twin.macro_fields["gdp_growth"] == 1.25 / 100.0


def test_a_restored_engine_continues_as_the_one_it_copied():
    """Restored between a quarter's end and its release: the pending figure
    is released on the same close in both."""
    q = quarter_length("pt-v20")
    parent = tf.Engine(seed=9, universe=UNIVERSE, model=lagged())
    walk(parent, q + 3, pins={5: -0.01})
    child = tf.Engine(seed=9, universe=UNIVERSE, model=lagged())
    child.restore_state(parent.state_snapshot())
    assert child.state_hash() == parent.state_hash()
    assert child.macro_fields["gdp_growth"] == parent.macro_fields["gdp_growth"]
    assert walk(child, 40) == walk(parent, 40)
    assert child.state_hash() == parent.state_hash()


def test_a_fork_and_a_replayed_checkpoint_carry_the_state():
    q = quarter_length("pt-v20")
    parent = tf.Engine(seed=10, universe=UNIVERSE, model=lagged())
    walk(parent, q + 3, pins={5: -0.01})
    fork, = tf.branch(parent, 1)
    mark = tf.Checkpoint.of(parent, universe=UNIVERSE, seed=10)
    resumed = mark.resume()
    for engine in (fork, resumed):
        assert engine.state_hash() == parent.state_hash()
    expected_walk = walk(parent, 40)
    assert walk(fork, 40) == expected_walk
    assert walk(resumed, 40) == expected_walk


def _snap_with(**changes):
    on = tf.Engine(seed=11, universe=UNIVERSE, model=lagged())
    on.run_days(3)
    snap = on.state_snapshot()
    gdp = snap["economy"]["gdp_publication"]
    for key, value in changes.items():
        if value is None:
            del gdp[key]
        else:
            gdp[key] = value
    return snap


@pytest.mark.parametrize("changes", [
    {"sum": None},
    {"pending_days": [100], "pending_values": []},
    {"count": 0},
    {"pending_days": [90, 83], "pending_values": [1.0, 2.0]},
    {"published": float("nan")},
], ids=["missing-key", "unpaired-pending", "empty-quarter", "out-of-order", "nan"])
def test_a_restore_refuses_a_state_that_does_not_fit(changes):
    snap = _snap_with(**changes)
    with pytest.raises(tf.ValidationError, match="gdp_publication"):
        tf.Engine(seed=11, universe=UNIVERSE, model=lagged()).restore_state(snap)


def test_a_restore_refuses_the_state_where_the_dial_is_off():
    snap = _snap_with()
    # Past the model check, to the state itself.
    del snap["model_fingerprint"]
    with pytest.raises(tf.ValidationError, match="gdp_publication_lag is 0"):
        # pt-v20 with the dial off: pt-v20 itself sets it since its graded arm (2026-09-26); was model="pt-v20"
        tf.Engine(seed=11, universe=UNIVERSE, model=lagged(0)).restore_state(snap)


def test_a_snapshot_without_the_state_reseeds_from_its_growth():
    """A snapshot written without the state, under the dial, publishes the
    growth it restores until the next release."""
    on = tf.Engine(seed=12, universe=UNIVERSE, model=lagged())
    on.pin_macro(gdp_growth=-0.02)
    on.run_days(1)
    snap = on.state_snapshot()
    del snap["economy"]["gdp_publication"]
    fresh = tf.Engine(seed=12, universe=UNIVERSE, model=lagged())
    fresh.restore_state(snap)
    assert fresh.macro_fields["gdp_growth"] == snap["economy"]["gdp_growth"] / 100.0
    gdp = fresh.state_snapshot()["economy"]["gdp_publication"]
    assert gdp["quarter"] == 0 and gdp["count"] == 1 and gdp["pending_days"] == []


@pytest.mark.parametrize("value", [-1.0, 2.5, 2521.0, float("nan")])
def test_a_lag_that_is_not_a_whole_number_of_sessions_is_refused(value):
    with pytest.raises(tf.ValidationError, match="gdp_publication_lag"):
        tf.ModelParams.from_preset("pt-v20", gdp_publication_lag=value)
