"""The rate indices' timing (`rate_close_remark`, `rate_intraday_live`) and the
central bank's stress cut (`fed_stress_cut`, `fed_stress_vix`,
`fed_stress_inflation_gap`): the thirteenth registration's bond timing (r13
audit).

With all five at their defaults, which every preset ships, the rate indices
(UST2Y, UST10Y, IGCORP) take each close's curve at the NEXT open, so each
prices the close's curve one session late: an index's close-to-close return
is the repricing formula on the previous close's move. The close re-mark puts
the curve on the index at the close that publishes it; the live mark prints
the index, during the session, at the curve the session so far implies for
tonight, so the close's move is not readable from the session. The stress cut
has the bank cut at a meeting after a VIX at or over a start, on the highest
VIX published since the last meeting.

These tests hold that the five are off everywhere and inert at their
defaults (the fingerprint included), that the engine refuses values outside
their domains, the same-close identity, a pin's re-mark, the live mark's
zero drift and unreadable close, the tape's decomposition for rate rows, the
stress level's recursion and a cut fired on it, and that the stress level and
the live mark are carried in the snapshot and both state hashes only while
set. The projection's arithmetic (bit for bit the close's own step at its
means) and the cut's branch are held by the engine's unit tests
(`live_projection`, `stress_cut`, and `rates::tests`).
"""

import math
import statistics
import struct

import pytest

import tradefloor as tf
from tradefloor import manifest

DEFAULTS = {"rate_close_remark": 0.0, "rate_intraday_live": 0.0,
            "fed_stress_cut": 0.0, "fed_stress_vix": 30.0,
            "fed_stress_inflation_gap": 1.0}
REMARK = {"rate_close_remark": 1.0}
LIVE = {"rate_close_remark": 1.0, "rate_intraday_live": 1.0}
RATES = ("UST2Y", "UST10Y", "IGCORP")
SPECS = {s["ticker"]: s for s in tf.rate_specs()}
UNIVERSE = tf.Universe.random(12, seed=111, bonds=True)


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=7, universe=UNIVERSE, **dials):
    return tf.Engine(seed=seed, universe=universe,
                     model=tf.ModelParams.from_preset("pt-v20", **dials))


def day(e, ticks=390):
    e.open_market()
    e.run_session(9, 30, 3, ticks)
    e.close_market()


def prices(e):
    return dict(zip(e.tickers, floats(e.prices())))


def curve(e):
    m = e.macro_fields
    return {"UST2Y": m["treasury_yield_2y"], "UST10Y": m["treasury_yield_10y"],
            "IGCORP": m["corporate_bond_yield"]}


def formula(ticker, y_before, y_after, one_step):
    """The documented repricing as a return. `one_step`: the night's carry
    and the move in the open's one step, `y / 252 - D dy + C dy^2 / 2`, as
    shipped; otherwise the move at the close and the carry at the open,
    `(1 + y / 252)(1 - D dy + C dy^2 / 2) - 1`."""
    spec = SPECS[ticker]
    dy = min(y_after - y_before, spec["duration"] / spec["convexity"])
    move = -spec["duration"] * dy + 0.5 * spec["convexity"] * dy * dy
    if one_step:
        return y_before / 252.0 + move
    return (1.0 + y_before / 252.0) * (1.0 + move) - 1.0


# -- off, and inert at the default -------------------------------------------

# pt-v21, the default from 0.10.0, sets four of them; the test after this
# one holds its values.
@pytest.mark.parametrize("preset", [p for p in tf.preset_names() if p != "pt-v21"])
def test_off_on_every_shipped_preset(preset):
    d = tf.ModelParams.from_preset(preset).to_dict()
    for name, value in DEFAULTS.items():
        assert d[name] == value, (preset, name)


def test_pt_v21_ships_them_on():
    d = tf.ModelParams.from_preset("pt-v21").to_dict()
    assert {name: d[name] for name in DEFAULTS} == {
        "rate_close_remark": 1.0, "rate_intraday_live": 1.0,
        "fed_stress_cut": 0.1, "fed_stress_vix": 30.0,
        "fed_stress_inflation_gap": 2.0}


def test_at_the_default_the_fingerprint_is_the_one_before_they_existed():
    # `ModelParams::digest` leaves the five out at their defaults, so a
    # vector that names them there is the vector it was, and a custom one
    # keeps the fingerprint (and the book known answer its state hash).
    assert tf.ModelParams.from_preset("pt-v20", **DEFAULTS).fingerprint == "pt-v20"
    custom = tf.ModelParams.from_preset("pt-v20", treasury_10y_noise=0.03)
    same = tf.ModelParams.from_preset("pt-v20", treasury_10y_noise=0.03, **DEFAULTS)
    assert custom.fingerprint.startswith("custom-")
    assert same.fingerprint == custom.fingerprint
    for name in DEFAULTS:
        moved = tf.ModelParams.from_preset(
            "pt-v20", **({"rate_close_remark": 1.0} if name == "rate_intraday_live" else {}),
            **{name: {"rate_close_remark": 1.0, "rate_intraday_live": 1.0,
                      "fed_stress_cut": 0.25, "fed_stress_vix": 35.0,
                      "fed_stress_inflation_gap": 2.0}[name]})
        assert moved.fingerprint.startswith("custom-"), name


def test_at_the_default_nothing_moves_and_nothing_is_carried():
    a = engine()
    b = engine(**DEFAULTS)
    for e in (a, b):
        for _ in range(3):
            day(e, ticks=60)
    assert floats(a.prices()) == floats(b.prices())
    assert a.state_hash() == b.state_hash()
    b.open_market()
    b.run_session(9, 30, 3, 10)
    for snap in (a.state_snapshot(), b.state_snapshot()):
        assert "fed_stress_vix_max" not in snap
        assert "rate_live_marks" not in snap


@pytest.mark.parametrize("name,value", [
    ("rate_close_remark", 0.5), ("rate_close_remark", 2.0),
    ("rate_intraday_live", 0.5), ("fed_stress_cut", -0.1), ("fed_stress_cut", 1.5),
    ("fed_stress_vix", 5.0), ("fed_stress_vix", 250.0),
    ("fed_stress_inflation_gap", -1.0), ("fed_stress_inflation_gap", 11.0),
])
def test_the_domains(name, value):
    extra = REMARK if name == "rate_intraday_live" else {}
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", **extra, **{name: value})


def test_the_live_mark_needs_the_close_remark():
    with pytest.raises(Exception, match="rate_close_remark"):
        tf.ModelParams.from_preset("pt-v20", rate_intraday_live=1.0)


# -- the same close ----------------------------------------------------------

def closes(e, sessions):
    rows = [(prices(e), curve(e))]
    for _ in range(sessions):
        day(e)
        rows.append((prices(e), curve(e)))
    return rows


@pytest.fixture(scope="module")
def runs():
    return {name: closes(engine(**dials), 40)
            for name, dials in (("shipped", {}), ("remark", REMARK), ("live", LIVE))}


def worst(rows, lag):
    """The largest miss, in bp, of each index's close-to-close return against
    the formula on the curve move `lag` closes earlier (0 is the same close),
    from the second session on (the first open accrues no carry)."""
    out = {}
    for t in RATES:
        misses = []
        for d in range(2 + lag, len(rows)):
            (p0, _), (p1, _) = rows[d - 1], rows[d]
            (_, c0), (_, c1) = rows[d - 1 - lag], rows[d - lag]
            misses.append(abs(p1[t] / p0[t] - 1.0 - formula(t, c0[t], c1[t], lag == 1)) * 1e4)
        out[t] = max(misses)
    return out


def test_shipped_prices_each_close_one_session_late(runs):
    assert all(v < 0.01 for v in worst(runs["shipped"], 1).values())
    assert all(v > 1.0 for v in worst(runs["shipped"], 0).values())


@pytest.mark.parametrize("arm", ["remark", "live"])
def test_the_close_remark_prices_the_same_close(runs, arm):
    # The identity the registration's I-rate row reads: under 0.01 bp. The
    # live mark commits nothing, so it holds under it as under the re-mark.
    assert all(v < 0.01 for v in worst(runs[arm], 0).values()), worst(runs[arm], 0)


def test_the_live_mark_changes_no_close_and_no_equity(runs):
    # The live mark moves only a session's prints: every close, of the
    # equities and the indices, is the re-mark arm's to the bit.
    for (a, _), (b, _) in zip(runs["remark"], runs["live"]):
        assert a == b


def test_an_after_close_pin_is_priced_at_once():
    for dials, moves in (({}, False), (REMARK, True)):
        e = engine(**dials)
        day(e)
        before = prices(e)["UST10Y"]
        y = e.macro_fields["treasury_yield_10y"]
        e.pin_macro(treasury_yield_10y=y + 0.005)
        after = prices(e)["UST10Y"]
        if moves:
            spec = SPECS["UST10Y"]
            expected = before * (1.0 - spec["duration"] * 0.005
                                 + 0.5 * spec["convexity"] * 0.005 ** 2)
            assert after == pytest.approx(expected, rel=1e-12)
        else:
            assert after == before


def test_the_tape_books_the_remark_and_the_open_for_rate_rows():
    pa = pytest.importorskip("pyarrow")
    e = engine(**REMARK)
    for d in range(3):
        day(e, ticks=30)
        e.record(d)
    rows = pa.table(e.prints()).to_pylist()
    first_rate = len(e.tickers) - 3
    for k in range(3):
        series = [r for r in rows if r["instrument_id"] == first_rate + k]
        booked = [r["repriced"] for r in series]
        assert any(v != 0.0 for v in booked)
        for prev, row in zip(series, series[1:]):
            move = math.log(row["print"] / prev["print"])
            parts = row["repriced"] + row["shock"] + row["absorbed"]
            assert parts == pytest.approx(move, abs=1e-12), row


# -- the live mark -----------------------------------------------------------

def live_history(sessions=100, **dials):
    """Per session: the equities' session return, each index's marked yield
    at the open and at the last tick, and the published curve at the open."""
    e = engine(**dials)
    rows = []
    n = len(e.tickers) - 3
    for _ in range(sessions):
        e.open_market()
        prev = floats(e.column("previous_close"))[:n]
        y_open = [r["yield"] for r in e.rate_instruments]
        published = curve(e)
        e.run_session(9, 30, 3, 390)
        now = floats(e.prices())[:n]
        y_last = [r["yield"] for r in e.rate_instruments]
        e.close_market()
        ret = statistics.fmean(math.log(a / b) for a, b in zip(now, prev))
        rows.append((ret, y_open, y_last, published))
    return rows


def test_the_live_mark_opens_on_the_curve_and_moves_with_the_session():
    rows = live_history(**LIVE)
    ret = [r[0] for r in rows]
    for k, t in enumerate(RATES):
        # At the open the session holds nothing yet, and the mark is the
        # published curve exactly: E[tonight | the open] less itself.
        for r in rows:
            assert r[1][k] == pytest.approx(r[3][t], abs=1e-15), t
        # Through the session it moves with the equities (the flight to
        # quality and the VIX term), and on average not at all.
        drift = [(r[2][k] - r[1][k]) * 1e4 for r in rows]
        assert abs(corr(ret, drift)) > 0.5, t
        assert abs(statistics.fmean(drift)) < 1.5, (t, statistics.fmean(drift))
    # With the close re-mark alone the indices sit on the published curve
    # all session.
    for r in live_history(sessions=10, **REMARK):
        assert r[1] == r[2]


def corr(x, y):
    return statistics.correlation(x, y)


# -- the stress cut ----------------------------------------------------------

STRESS = {"fed_stress_cut": 0.25, "fed_stress_vix": 40.0, "fed_stress_inflation_gap": 10.0}


def meeting_run(sessions, pin_after_first=False, **dials):
    e = tf.Engine(seed=3, universe=tf.Universe.random(6, seed=1),
                  model=tf.ModelParams.from_preset("pt-v20", **dials))
    last = e.state_snapshot()["central_bank"]["last_meeting_date"]
    rows, meetings = [], 0
    for _ in range(sessions):
        day(e, ticks=30)
        snap = e.state_snapshot()
        met = snap["central_bank"]["last_meeting_date"] != last
        last = snap["central_bank"]["last_meeting_date"]
        m = e.macro_fields
        rows.append((m["vix"] * 1.0, met, m["federal_funds_rate"],
                     snap.get("fed_stress_vix_max")))
        if met:
            meetings += 1
            if pin_after_first and meetings == 1:
                e.pin_macro(vix=80.0)
    return rows


def test_the_stress_level_is_the_highest_vix_since_the_last_meeting():
    rows = meeting_run(150, **STRESS)
    assert sum(1 for r in rows if r[1]) >= 3
    level = rows[0][3]
    for vix, met, _, carried in rows[1:]:
        level = 0.0 if met else max(level, vix)
        assert carried == level


def test_the_cut_fires_on_the_level_since_the_meeting_not_the_days_vix():
    # A VIX of 80 written the evening after the first meeting: the next
    # close publishes about 67 and it has fallen back under 40 by the next
    # meeting, which still cuts, three steps (1 + floor(27 / 10)), where the
    # bank without the cut holds.
    on = meeting_run(40, pin_after_first=True, **STRESS)
    off = meeting_run(40, pin_after_first=True)
    meets = [d for d, r in enumerate(on) if r[1]]
    assert len(meets) >= 2
    d = meets[1]
    vix, _, rate, _ = on[d]
    assert vix < 40.0
    assert rate == pytest.approx(on[d - 1][2] - 0.0075)
    assert off[d][2] == pytest.approx(off[d - 1][2])
    # The level resets at the meeting that read it.
    assert on[d][3] == 0.0


def test_a_restore_without_the_stress_level_loses_the_cut():
    # The pin scenario above, forked the session after the spike: a restore
    # that keeps the level cuts at the next meeting as the parent does, one
    # that drops it (0.0, and the VIX back under the start by then) holds.
    def parent():
        e = tf.Engine(seed=3, universe=tf.Universe.random(6, seed=1),
                      model=tf.ModelParams.from_preset("pt-v20", **STRESS))
        last = e.state_snapshot()["central_bank"]["last_meeting_date"]
        while True:
            day(e, ticks=30)
            now = e.state_snapshot()["central_bank"]["last_meeting_date"]
            if now != last:
                break
        e.pin_macro(vix=80.0)
        day(e, ticks=30)
        return e
    snap = parent().state_snapshot()
    assert snap["fed_stress_vix_max"] > 50.0
    rates = []
    for drop in (False, True):
        e = tf.Engine(seed=3, universe=tf.Universe.random(6, seed=1),
                      model=tf.ModelParams.from_preset("pt-v20", **STRESS))
        s = dict(snap)
        if drop:
            # The level a fresh engine holds: a snapshot without the key is
            # refused under the state contract (below), so the level is
            # zeroed in place to show what carrying it is worth.
            s["fed_stress_vix_max"] = 0.0
        e.restore_state(s)
        for _ in range(60):
            day(e, ticks=30)
        rates.append(e.macro_fields["federal_funds_rate"])
    assert rates[0] < rates[1], rates
    e = tf.Engine(seed=3, universe=tf.Universe.random(6, seed=1),
                  model=tf.ModelParams.from_preset("pt-v20", **STRESS))
    with pytest.raises(tf.ValidationError, match="fed_stress_vix_max.*fed_stress_cut"):
        e.restore_state({k: v for k, v in snap.items() if k != "fed_stress_vix_max"})


# -- carried and hashed only while set ----------------------------------------

def test_the_stress_level_and_the_live_mark_are_carried_and_hashed_only_while_set():
    dials = {**LIVE, **STRESS}
    e = engine(**dials)
    day(e)
    e.open_market()
    e.run_session(9, 30, 3, 101)
    snap = e.state_snapshot()
    assert len(snap["rate_live_marks"]) == 6
    assert "fed_stress_vix_max" in snap
    assert manifest.state_hash(snap) == e.state_hash()
    for key, value in (("rate_live_marks", [0.0] * 6), ("fed_stress_vix_max", 99.0)):
        edited = e.state_snapshot()
        edited[key] = value
        assert manifest.state_hash(edited) != manifest.state_hash(snap), key

    # A restore mid-session, between two refreshes of the live mark,
    # reproduces the market and the indices' prints to the bit.
    twin = engine(**dials)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_session(11, 11, 3, 289)
        x.close_market()
        day(x)
    assert floats(e.prices()) == floats(twin.prices())

    # After the close the session's mark is gone; the level stays.
    after = e.state_snapshot()
    assert "rate_live_marks" not in after and "fed_stress_vix_max" in after

    # Refused where the dial is off.
    for key in ("rate_live_marks", "fed_stress_vix_max"):
        only = {"rate_live_marks": LIVE, "fed_stress_vix_max": STRESS}[key]
        other = {k: v for k, v in dials.items() if k not in only}
        if key == "rate_live_marks":
            other = {**other, **REMARK}
        # (The model's fingerprint refuses it first; the engine's own check
        # stands behind that for a caller that writes the state directly.)
        with pytest.raises(Exception):
            engine(**other).restore_state(snap)
