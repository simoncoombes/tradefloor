"""The Fed put and the Treasury haven (`fed_put_gain`, `fed_put_threshold`,
`fed_put_half_life`, `fed_put_emergency_vix`, `treasury_put_pricing`,
`treasury_haven_gain`).

The meeting's arithmetic (the cut a fall asks for, the hike a stressed VIX
holds, what the calm meetings give back, the priced put's missing surprise,
the haven in the meeting's target) is held on a hand-built economy in
rust/tests/fed_put.rs. These hold the engine around it: every preset ships
the six at 0.0 and every digest is the one it was; the close keeps the
intermeeting return from the market cap; the snapshot and both state hashes
carry the put's four fields only while the gain is set, and a restore
reproduces the run; a VIX close at or above `fed_put_emergency_vix` calls a
meeting 21 sessions after the last, and that meeting takes a meeting's
economy draws; the priced put lowers the 10-year's anchor by its share of
the cut the put asks for; and the haven lowers the 10-year under a stressed
VIX.
"""

import math
import struct

import pytest

import tradefloor as tf
from tradefloor import manifest

UNIVERSE = list(tf.Universe.random(12, seed=3))
DIALS = ("fed_put_gain", "fed_put_threshold", "fed_put_half_life",
         "fed_put_emergency_vix", "treasury_put_pricing", "treasury_haven_gain")
FIELDS = ("intermeeting_return", "fed_put", "fed_put_owed", "fed_put_mcap_prev")
PUT = {"fed_put_gain": 5.0, "fed_put_half_life": 126.0}


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
        "fed_put_gain",
        "fed_put_threshold",
        "fed_put_half_life",
        "fed_put_emergency_vix",
        "treasury_put_pricing",
        "treasury_haven_gain",
    )} == {
        "fed_put_gain": 3.0,
        "fed_put_threshold": 0.0,
        "fed_put_half_life": 126.0,
        "fed_put_emergency_vix": 50.0,
        "treasury_put_pricing": 1.0,
        "treasury_haven_gain": 0.014,
    }


@pytest.mark.parametrize("dials", [
    {"fed_put_gain": -0.1},
    {"fed_put_gain": 10.5, "fed_put_half_life": 126.0},
    # A put whose stock never decays would never be given back.
    {"fed_put_gain": 5.0},
    {"fed_put_threshold": 0.25},
    {"fed_put_half_life": 600.0},
    {"fed_put_emergency_vix": -1.0},
    {"fed_put_emergency_vix": 95.0},
    {"treasury_put_pricing": 1.5},
    {"treasury_haven_gain": 0.06},
])
def test_out_of_range_is_refused(dials):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", **dials)


def test_the_state_is_carried_only_while_the_gain_is_set():
    assert not set(FIELDS) & set(engine().state_snapshot()["economy"])
    # The other five alone write nothing.
    alone = engine(fed_put_half_life=126.0, fed_put_emergency_vix=40.0,
                   treasury_put_pricing=1.0, treasury_haven_gain=0.015)
    assert not set(FIELDS) & set(alone.state_snapshot()["economy"])
    assert set(FIELDS) <= set(engine(**PUT).state_snapshot()["economy"])


def test_the_close_keeps_the_intermeeting_return_from_the_market_cap():
    # The close reads total public market cap and adds its log change; a
    # meeting restarts the sum. pt-v20 re-prices to the published macro
    # after the close, so the cap the close read is not the column's
    # overnight, but the sum telescopes: between meetings it is the log of
    # tonight's base over the last meeting's.
    e = engine(**PUT)
    snap = e.state_snapshot()
    assert snap["economy"]["fed_put_mcap_prev"] == pytest.approx(
        sum(floats(e.column("market_cap"))), rel=1e-12)
    last = snap["central_bank"]["last_meeting_date"]
    base = snap["economy"]["fed_put_mcap_prev"]
    checked = 0
    for day in range(30):
        e.run_days(1, record=False, first_day=day)
        snap = e.state_snapshot()
        cap = snap["economy"]["fed_put_mcap_prev"]
        assert cap == pytest.approx(sum(floats(e.column("market_cap"))), rel=0.05)
        if snap["central_bank"]["last_meeting_date"] != last:
            assert snap["economy"]["intermeeting_return"] == 0.0
            last, base = snap["central_bank"]["last_meeting_date"], cap
        else:
            assert snap["economy"]["intermeeting_return"] == pytest.approx(
                math.log(cap / base), abs=1e-9)
            checked += 1
    assert checked > 10


def test_the_hashes_agree_and_a_restore_reproduces_the_run():
    dials = {**PUT, "fed_put_emergency_vix": 40.0, "treasury_put_pricing": 1.0,
             "treasury_haven_gain": 0.015}
    e = engine(**dials)
    for day in range(6):
        e.pin_macro(vix=45.0)
        e.run_days(1, record=False, first_day=day)
    snap = e.state_snapshot()
    assert snap["economy"]["intermeeting_return"] != 0.0
    assert manifest.state_hash(snap) == e.state_hash()
    # Every one of the four reaches the hash.
    for name in FIELDS:
        moved = {**snap, "economy": {**snap["economy"], name: snap["economy"][name] + 1.0}}
        assert manifest.state_hash(moved) != e.state_hash(), name
    twin = engine(**dials)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(4, record=False, first_day=6)
    assert floats(e.prices()) == floats(twin.prices())
    assert e.state_hash() == twin.state_hash()


def meeting_days(e, days, vix=None):
    """The days on which a meeting was held, from the bank's own clock."""
    held = []
    last = e.state_snapshot()["central_bank"]["last_meeting_date"]
    for day in range(days):
        if vix is not None:
            e.pin_macro(vix=vix)
        e.run_days(1, record=False, first_day=day)
        now = e.state_snapshot()["central_bank"]["last_meeting_date"]
        if now != last:
            held.append(now // (24 * 60))
            last = now
    return held


def test_a_stressed_vix_calls_a_meeting_after_21_sessions():
    # Inflation under 4 and a policy rate above zero at this seed's opening.
    e = engine(**PUT)
    assert e.state_snapshot()["economy"]["inflation_rate"] < 4.0
    calm = meeting_days(engine(**PUT), 60, vix=70.0)
    called = meeting_days(engine(**PUT, fed_put_emergency_vix=40.0), 60, vix=70.0)
    # Off, the calendar's six-to-eight-week cadence; on, a meeting every 21
    # sessions: the opening's last meeting was 7 days before day 0, so the
    # first falls on day 14.
    assert len(called) > len(calm)
    assert called[0] == 14
    gaps = [b - a for a, b in zip(called, called[1:])]
    assert gaps and all(g == 21 for g in gaps)
    # A VIX under the dial's level calls nothing.
    assert meeting_days(engine(**PUT, fed_put_emergency_vix=90.0), 60, vix=45.0) == \
        meeting_days(engine(**PUT), 60, vix=45.0)


def test_the_haven_lowers_the_ten_year_under_a_stressed_vix():
    ten = {}
    for gain in (0.0, 0.02):
        e = engine(treasury_haven_gain=gain)
        assert e.state_snapshot()["economy"]["inflation_rate"] < 4.0
        for day in range(20):
            e.pin_macro(vix=50.0)
            e.run_days(1, record=False, first_day=day)
        ten[gain] = e.state_snapshot()["economy"]["treasury_yield_10y"]
    # The anchor is 0.6 lower and the daily pull closes 5 per cent of the
    # gap a session: about 0.6 * (1 - 0.95^20) = 0.38 after 20 sessions.
    assert ten[0.0] - ten[0.02] > 0.2


def test_an_emergency_meeting_takes_a_meetings_draws():
    # The put's arithmetic takes no draw, but a meeting the VIX calls is an
    # ordinary meeting: it takes the announcement variant's and the next
    # date's draws from the economy stream on a session the calendar would
    # not. The two runs share the economy stream until the first called
    # meeting (the close of the 14th session, dated day 14 above) and part
    # there by a meeting's 2 or 3 draws.
    runs = {}
    for level in (0.0, 40.0):
        e = engine(**PUT, fed_put_emergency_vix=level)
        drawn = []
        for day in range(20):
            e.pin_macro(vix=70.0)
            e.run_days(1, record=False, first_day=day)
            drawn.append(dict(e.draws_by_stream())["economy"])
        runs[level] = drawn
    assert runs[0.0][:13] == runs[40.0][:13]
    assert runs[40.0][13] - runs[0.0][13] in (2, 3)


def test_the_priced_put_lowers_the_ten_year_anchor_by_its_share():
    # One close from the same state, the put priced in full or not at all:
    # the 10-year's anchor reads the policy rate less kappa min(E, rate), and
    # the daily pull closes 5 per cent of the gap, so the two 10-years part
    # by 0.05 kappa min(E, rate), with E read on the close's own
    # intermeeting return.
    ten = {}
    for kappa in (0.0, 1.0):
        e = engine(**PUT, treasury_put_pricing=kappa)
        snap = e.state_snapshot()
        snap["economy"]["intermeeting_return"] = -0.1
        e.restore_state(snap)
        last = snap["central_bank"]["last_meeting_date"]
        rate = snap["economy"]["federal_funds_rate"]
        e.run_days(1, record=False, first_day=0)
        after = e.state_snapshot()
        # No meeting that night, so the return the close summed is still
        # there to read.
        assert after["central_bank"]["last_meeting_date"] == last
        ten[kappa] = after["economy"]["treasury_yield_10y"]
        asked = 5.0 * max(0.0, -after["economy"]["intermeeting_return"])
    assert rate > 0.0 and asked > 0.0
    assert ten[0.0] - ten[1.0] == pytest.approx(0.05 * min(asked, rate), abs=1e-9)
