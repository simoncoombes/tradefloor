"""The curve's anticipation of the next meeting (`policy_anticipation`,
`policy_anticipation_cut_share`).

Every preset ships both at 0.0 and every digest is the one it was; out-of-range
values are refused; the snapshot and both state hashes carry what the curve
prices only while the dial is set, and a restore reproduces the run. With the
dial on, a run whose inflation is pinned high (so the ladder hikes) holds the
10-year and the 2-year above a twin without it by exactly what the curve
prices, that grows with the share of the meeting interval elapsed and is gone
at the meeting, where the twins' curves meet again: the meeting moves the curve
by the surprise alone.
"""

import struct

import pytest

import tradefloor as tf
from tradefloor import manifest

UNIVERSE = list(tf.Universe.random(12, seed=3))
DIALS = ("policy_anticipation", "policy_anticipation_cut_share")
ON = {"policy_anticipation": 1.0}
HOT = 0.045          # a pinned inflation rate the ladder hikes on


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=7, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", **dials))


def hot_run(days, **dials):
    e = engine(**dials)
    rows = []
    for day in range(days):
        e.pin_macro(inflation_rate=HOT)
        e.run_days(1, record=False, first_day=day)
        snap = e.state_snapshot()
        ec, cb = snap["economy"], snap["central_bank"]
        rows.append(dict(rate=ec["federal_funds_rate"], t10=ec["treasury_yield_10y"],
                         t2=ec["treasury_yield_2y"], corp=ec["corporate_bond_yield"],
                         priced=snap.get("policy_anticipation_priced"),
                         last=cb["last_meeting_date"], next=cb["next_meeting_date"]))
    return e, rows


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    values = tf.ModelParams.from_preset(preset).to_dict()
    assert all(values[name] == 0.0 for name in DIALS)


def test_the_fingerprint_does_not_carry_them_at_zero():
    base = tf.ModelParams.from_preset("pt-v20", fed_put_gain=5.0, fed_put_half_life=126.0)
    same = tf.ModelParams.from_preset("pt-v20", fed_put_gain=5.0, fed_put_half_life=126.0,
                                      policy_anticipation=0.0, policy_anticipation_cut_share=0.0)
    assert base.fingerprint == same.fingerprint
    assert tf.ModelParams.from_preset("pt-v20", **ON).fingerprint != \
        tf.ModelParams.from_preset("pt-v20").fingerprint


@pytest.mark.parametrize("dials", [
    {"policy_anticipation": -0.1},
    {"policy_anticipation": 3.5},
    {"policy_anticipation": 1.0, "policy_anticipation_cut_share": -0.1},
    {"policy_anticipation": 1.0, "policy_anticipation_cut_share": 1.5},
])
def test_out_of_range_is_refused(dials):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", **dials)


def test_the_state_is_carried_only_while_set():
    assert "policy_anticipation_priced" not in engine().state_snapshot()
    # The cut share alone writes nothing.
    assert "policy_anticipation_priced" not in \
        engine(policy_anticipation_cut_share=1.0).state_snapshot()
    assert "policy_anticipation_priced" in engine(**ON).state_snapshot()


def test_off_the_dial_changes_nothing():
    # The cut share is unread with the dial off: the run is the preset's.
    a, b = engine(), engine(policy_anticipation_cut_share=1.0)
    for x in (a, b):
        for day in range(30):
            x.pin_macro(inflation_rate=HOT)
            x.run_days(1, record=False, first_day=day)
    assert floats(a.prices()) == floats(b.prices())


def test_the_curve_prices_the_expected_hike_and_the_meeting_moves_it_by_the_surprise():
    _, off = hot_run(60)
    _, on = hot_run(60, **ON)
    meetings = [i for i in range(1, 60) if on[i]["last"] != on[i - 1]["last"]]
    assert meetings, "the pinned inflation should bring a meeting inside 60 sessions"
    first = meetings[0]
    # The bank reads no yield, so the rate path is the twins' own.
    assert [r["rate"] for r in on[:first + 1]] == [r["rate"] for r in off[:first + 1]]
    assert on[first]["rate"] > on[first - 1]["rate"]
    # Before the meeting the curve holds what it prices, which grows with the
    # share of the interval elapsed (pt-v20 has no damping, so the pass-through
    # is one on the 10-year, the 2-year and the corporate yield). To within the
    # flight to quality, which reads the twins' own index returns: 0.008 points
    # of yield per per cent of index move.
    for i in range(first):
        p = on[i]["priced"]
        assert p > 0.0
        for key in ("t10", "t2", "corp"):
            assert on[i][key] - off[i][key] == pytest.approx(p, abs=0.01), (i, key)
    assert on[first - 1]["priced"] > on[0]["priced"]
    # At the meeting nothing is priced any more, and the twins' curves meet:
    # the day's move is the surprise, smaller than the whole change by what
    # was priced.
    assert on[first]["priced"] == pytest.approx(0.0, abs=1e-12)
    for key in ("t10", "t2", "corp"):
        assert on[first][key] == pytest.approx(off[first][key], abs=0.01), key
    move_on = on[first]["t10"] - on[first - 1]["t10"]
    move_off = off[first]["t10"] - off[first - 1]["t10"]
    assert move_off - move_on == pytest.approx(on[first - 1]["priced"], abs=0.01)


def test_the_priced_share_follows_the_meeting_clock():
    _, on = hot_run(40, **ON)
    # Between meetings the shadow change is the same hike, so the priced value
    # over the elapsed share is constant.
    first = next(i for i in range(1, 40) if on[i]["last"] != on[i - 1]["last"])
    ratios = []
    for i in range(first + 2, 40):
        r = on[i]
        if r["last"] != on[first]["last"]:
            break
        # The session's timestamp is not in the snapshot; the ratio of priced to
        # sessions since the meeting is constant while the shadow change is.
        ratios.append(r["priced"] / (i - first))
    assert len(ratios) > 5
    assert max(ratios) == pytest.approx(min(ratios), rel=1e-6)


def test_the_hashes_agree_and_a_restore_reproduces_the_run():
    e, _ = hot_run(15, **ON)
    snap = e.state_snapshot()
    assert snap["policy_anticipation_priced"] != 0.0
    assert manifest.state_hash(snap) == e.state_hash()
    moved = {**snap, "policy_anticipation_priced": snap["policy_anticipation_priced"] + 0.1}
    assert manifest.state_hash(moved) != e.state_hash()
    twin = engine(**ON)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(20, record=False, first_day=15)
    assert floats(e.prices()) == floats(twin.prices())
    assert e.state_hash() == twin.state_hash()


def test_a_snapshot_with_it_is_refused_by_a_model_without_it():
    snap = engine(**ON).state_snapshot()
    with pytest.raises(Exception):
        engine().restore_state(snap)


def test_no_draw_is_taken():
    # Up to the first meeting the twins' economies agree, so any draw the
    # shadow meeting took would show as a difference in the economy stream.
    a, off = hot_run(15)
    b, on = hot_run(15, **ON)
    assert all(r["last"] == on[0]["last"] for r in on), "no meeting inside these sessions"
    assert a.draws_by_stream() == b.draws_by_stream()
