"""Credit's VIX slope and leverage term (`corporate_spread_vix_cut`,
`corporate_spread_equity_gain`, `corporate_spread_equity_half_life`).

Every preset ships the three at 0.0 and every digest is the one it was
(tests/test_known_answer.py). These hold the mechanism on pt-v20, which moves
the corporate yield every close (`corporate_yield_daily`): the gap steps on
the session's index return; with the slope cut whole the corporate yield's
daily move off a meeting is the 10-year's plus the gain times the cycle
multiplier times the gap's change, and a meeting re-anchors the spread to
the formula with the gap in its base; the snapshot and both state hashes
carry the gap only while the gain is set, and a restore reproduces the run;
and no dial takes a draw.
"""

import math
import struct

import pytest

import tradefloor as tf
from tradefloor import manifest

UNIVERSE = list(tf.Universe.random(12, seed=3))
DIALS = ("corporate_spread_vix_cut", "corporate_spread_equity_gain",
         "corporate_spread_equity_half_life")
GAIN = {"corporate_spread_equity_gain": 1.5, "corporate_spread_equity_half_life": 126.0}
# The meeting formula's cycle multipliers (`economy::central_bank::spread_multiplier_of`).
MULT = {"contraction": 2.8, "trough": 3.5, "recovery": 1.4, "peak": 1.1, "expansion": 1.0}


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=7, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", **dials))


# pt-v21, the default from 0.10.0, sets these; the test after this one
# holds its values.
@pytest.mark.parametrize("preset", [p for p in tf.preset_names() if p != "pt-v21"])
def test_off_on_every_shipped_preset(preset):
    values = tf.ModelParams.from_preset(preset).to_dict()
    assert all(values[name] == 0.0 for name in DIALS)


def test_pt_v21_ships_them_on():
    """pt-v21, the default from 0.10.0, ships them at the values its grade
    read."""
    d = tf.ModelParams.from_preset("pt-v21").to_dict()
    assert {n: d[n] for n in (
        "corporate_spread_vix_cut",
        "corporate_spread_equity_gain",
        "corporate_spread_equity_half_life",
    )} == {
        "corporate_spread_vix_cut": 1.0,
        "corporate_spread_equity_gain": 1.8,
        "corporate_spread_equity_half_life": 126.0,
    }


@pytest.mark.parametrize("dials", [
    {"corporate_spread_vix_cut": -0.1},
    {"corporate_spread_vix_cut": 1.1},
    {"corporate_spread_equity_gain": -0.5, "corporate_spread_equity_half_life": 126.0},
    {"corporate_spread_equity_gain": 10.5, "corporate_spread_equity_half_life": 126.0},
    # The gap is averaged at the half-life, so a gain needs one.
    {"corporate_spread_equity_gain": 1.5},
    {"corporate_spread_equity_half_life": 1300.0},
])
def test_out_of_range_is_refused(dials):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", **dials)


def test_the_gap_is_carried_only_while_the_gain_is_set():
    assert "spread_equity_gap" not in engine().state_snapshot()["economy"]
    alone = engine(corporate_spread_vix_cut=0.8, corporate_spread_equity_half_life=126.0)
    assert "spread_equity_gap" not in alone.state_snapshot()["economy"]
    assert "spread_equity_gap" in engine(**GAIN).state_snapshot()["economy"]


def session(e):
    """One session by hand: the opens, the last print's cap-weighted return
    from the open in per cent (pt-v20 has no night split, so the close's
    step reads the day from the open), then the close."""
    e.open_market()
    opens = floats(e.prices())
    e.run_session(9, 30, 3, 390)
    last = floats(e.prices())
    cap = floats(e.column("market_cap"))
    r = sum((p - o) / o * 100.0 * c for p, o, c in zip(last, opens, cap)) / sum(cap)
    e.close_market()
    return r


def test_the_gap_steps_on_the_session_return():
    e = engine(**GAIN)
    decay = 0.5 ** (1.0 / 126.0)
    gap = e.state_snapshot()["economy"]["spread_equity_gap"]
    assert gap == 0.0
    for _ in range(25):
        r = session(e)
        want = decay * (gap - math.log(1.0 + r / 100.0))
        gap = e.state_snapshot()["economy"]["spread_equity_gap"]
        assert gap == pytest.approx(want, abs=1e-12)
    assert gap != 0.0


def test_the_spread_moves_by_the_gap_and_the_meeting_re_anchors_to_it():
    # The VIX slope cut whole: off a meeting the spread moves only by the
    # gain times the multiplier times the gap's change; at a meeting the
    # spread is the formula with the gap in its base and no VIX term.
    g = 1.5
    e = engine(corporate_spread_vix_cut=1.0, **GAIN)
    before = e.state_snapshot()
    off, on = 0, 0
    for _ in range(60):
        session(e)
        now = e.state_snapshot()
        eb, en = before["economy"], now["economy"]
        spread = en["corporate_bond_yield"] - en["treasury_yield_10y"]
        if now["central_bank"]["last_meeting_date"] != before["central_bank"]["last_meeting_date"]:
            want = min(max((1.0 + g * en["spread_equity_gap"]) * MULT[en["cycle_phase"]], 0.8), 6.0)
            assert spread == pytest.approx(want, abs=1e-12)
            on += 1
        elif spread > 0.8 + 1e-9:
            moved = (en["corporate_bond_yield"] - eb["corporate_bond_yield"]) \
                - (en["treasury_yield_10y"] - eb["treasury_yield_10y"])
            want = g * MULT[eb["cycle_phase"]] * (en["spread_equity_gap"] - eb["spread_equity_gap"])
            assert moved == pytest.approx(want, abs=1e-12)
            off += 1
        before = now
    assert on >= 1 and off > 30


def test_the_slope_cut_alone_leaves_the_corporate_yield_on_the_10_year():
    e = engine(corporate_spread_vix_cut=1.0)
    before = e.state_snapshot()
    checked = 0
    for _ in range(40):
        session(e)
        now = e.state_snapshot()
        eb, en = before["economy"], now["economy"]
        if now["central_bank"]["last_meeting_date"] == before["central_bank"]["last_meeting_date"] \
                and en["corporate_bond_yield"] - en["treasury_yield_10y"] > 0.8 + 1e-9:
            assert en["corporate_bond_yield"] - eb["corporate_bond_yield"] == pytest.approx(
                en["treasury_yield_10y"] - eb["treasury_yield_10y"], abs=1e-12)
            checked += 1
        before = now
    assert checked > 20


def test_the_hashes_agree_and_a_restore_reproduces_the_run():
    dials = {**GAIN, "corporate_spread_vix_cut": 0.8}
    e = engine(**dials)
    e.run_days(6, record=False)
    snap = e.state_snapshot()
    assert snap["economy"]["spread_equity_gap"] != 0.0
    assert manifest.state_hash(snap) == e.state_hash()
    moved = {**snap, "economy": {**snap["economy"],
                                 "spread_equity_gap": snap["economy"]["spread_equity_gap"] + 1.0}}
    assert manifest.state_hash(moved) != e.state_hash()
    twin = engine(**dials)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(4, record=False, first_day=6)
    assert floats(e.prices()) == floats(twin.prices())
    assert e.state_hash() == twin.state_hash()


def test_no_dial_takes_a_draw_and_the_market_moves():
    base, live = engine(), engine(corporate_spread_vix_cut=0.8, **GAIN)
    for x in (base, live):
        x.run_days(30, record=False)
    assert base.draws_consumed == live.draws_consumed
    assert floats(base.prices()) != floats(live.prices())
    assert base.state_snapshot()["economy"]["corporate_bond_yield"] != \
        live.state_snapshot()["economy"]["corporate_bond_yield"]
