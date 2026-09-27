"""The night as a share of the day (`overnight_market_share`, `overnight_idio_share`).

Off zero the day's variance is SPLIT between the night and the session rather
than added to: the open draws the market factor's night at `sqrt(wm)` of its
daily sigma and the sector and own draws at `sqrt(wi)`, and every tick of the
session draws at `sqrt(1 - w)` of its usual scale. The market GJR's
persistence is 0.8946 + 0.0844 k^2 at an innovation scale k, so the split has
to be exact: the prototype's 1.17 days of innovation doubled the index's
volatility. These tests hold that the dials are off on every preset, that the
split is exact on the draws that feed the GJR, that the night reaches the
GJRs and the fair-value level as the session's draws do, that the day is read
from the last close, and that the opening print is the tick's own model price
(the buyback term's fixed point included), so the first tick does not undo it.
"""

import math
import struct

import pytest

import tradefloor as tf

UNIVERSE = tf.Universe.random(6, seed=11)


def floats(raw):
    return list(struct.unpack("<%dd" % (len(raw) // 8), raw))


def model(**dials):
    return tf.ModelParams.from_preset("pt-v20", **dials)


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    d = tf.ModelParams.from_preset(preset).to_dict()
    for name in ("overnight_market_share", "overnight_idio_share", "overnight_idio_df"):
        assert d[name] == 0.0


@pytest.mark.parametrize("dials", [
    {"overnight_market_share": -0.1},
    {"overnight_market_share": 0.95},
    {"overnight_idio_share": 1.0},
    {"overnight_idio_df": 2.0},
    {"overnight_idio_df": 3.5},
    {"overnight_idio_df": 31.0},
    # The ratio ADDS a night and the shares SPLIT the day: one or the other.
    {"overnight_variance_ratio": 0.3, "overnight_idio_share": 0.2},
    {"overnight_variance_ratio": 0.3, "overnight_market_share": 0.5},
])
def test_the_ranges(dials):
    with pytest.raises(Exception):
        model(**dials)


def market_state(e):
    """(variance, day_factor) of the market factor's GJR."""
    v = e.state_snapshot()["market_variance"]
    return v[0], v[1]


def test_the_split_is_exact_on_the_market_factor():
    # Day 0 on the same seed: the variance has not moved and the overnight
    # stream's first normal is the same, so the night each engine adds to
    # the GJR's day innovation scales as sqrt(wm), and the session's
    # accumulated factor, on the same market draws at the same sigma, as
    # sqrt(1 - wm).
    nights, sessions = {}, {}
    for wm in (0.2, 0.5, 0.8):
        e = tf.Engine(seed=5, universe=UNIVERSE, model=model(overnight_market_share=wm))
        e.open_market()
        nights[wm] = market_state(e)[1]
        e.run_session(9, 30, 3, 390)
        sessions[wm] = market_state(e)[1] - nights[wm]
    base = tf.Engine(seed=5, universe=UNIVERSE, model="pt-v20")
    base.open_market()
    assert market_state(base)[1] == 0.0
    base.run_session(9, 30, 3, 390)
    whole = market_state(base)[1]
    for wm in (0.2, 0.5, 0.8):
        assert nights[wm] / nights[0.2] == pytest.approx(math.sqrt(wm / 0.2), rel=1e-12)
        assert sessions[wm] / whole == pytest.approx(math.sqrt(1.0 - wm), rel=1e-12)


def test_night_plus_session_is_one_day_of_variance():
    # Over many days the night carries wm of the day's market innovation
    # variance and the session the rest: the sum is one day, not 1.17.
    wm = 0.5
    e = tf.Engine(seed=9, universe=UNIVERSE, model=model(overnight_market_share=wm))
    night2 = sess2 = 0.0
    for day in range(160):
        e.open_market()
        var, night = market_state(e)
        e.run_session(9, 30, 3, 390)
        total = market_state(e)[1]
        night2 += night * night / var
        sess2 += (total - night) ** 2 / var
        e.close_market()
    n = 160
    assert night2 / n == pytest.approx(wm, abs=0.15)
    assert (night2 + sess2) / n == pytest.approx(1.0, abs=0.25)


def test_the_night_reaches_the_names_gjr_and_the_fair_value_level():
    e = tf.Engine(seed=3, universe=UNIVERSE,
                  model=model(overnight_market_share=0.5, overnight_idio_share=0.2))
    e.run_days(2, record=False)
    e.open_market()
    night = floats(e.attribution("overnight"))
    noise = floats(e.attribution("random_noise"))
    shift = floats(e.attribution("fair_value_shift"))
    assert all(x != 0.0 for x in night)
    # The whole night joins the name's `random_noise` slot, the innovation
    # its GJR steps on tonight, and the permanent share leaves `s` for the
    # fair-value level as the tick's does (pt-v20: news share 1.0, market
    # share 1.0 on the plain loading).
    for a, b in zip(night, noise):
        assert b == pytest.approx(a, abs=1e-12)
    assert all(x != 0.0 for x in shift)
    # Before the session the noise split's parts sum to the slot.
    parts = floats(e.state_snapshot()["noise_parts"])
    for i, total in enumerate(noise):
        assert sum(parts[3 * i:3 * i + 3]) == pytest.approx(total, abs=1e-12)


def test_the_day_runs_from_the_last_close():
    # Under a split `previous_close` keeps the last close, so the day's
    # return the VIX, the forced flow and the per-name close read is the
    # whole day's; off it, the open resets it, as it always did.
    for dials, from_close in (({}, False), ({"overnight_idio_share": 0.2}, True)):
        e = tf.Engine(seed=3, universe=UNIVERSE, model=model(**dials))
        e.run_days(2, record=False)
        closes = floats(e.prices())
        e.open_market()
        prev, opens = floats(e.column("previous_close")), floats(e.column("open"))
        if from_close:
            assert prev == closes
            assert all(o != c for o, c in zip(opens, closes))
        else:
            # No night: the open is the close and resets the mark to it.
            assert prev == opens == closes


def test_the_opening_print_is_the_models_price_with_the_buyback_term():
    # pt-v20 returns 0.75 of earnings as buybacks, compounded over the
    # elapsed years. The shipped overnight block read fair value without the
    # term, so its open sat below the tick's model price by the compounded
    # yield and the first tick gave it back. On a near-zero split, a year
    # in, the open's move from the last close is the night's own move on
    # `s` (the fair-value level it hands over prints unchanged), not a
    # buyback discount of several per cent.
    e = tf.Engine(seed=4, universe=UNIVERSE,
                  model=model(overnight_market_share=0.001, overnight_idio_share=0.001))
    e.run_days(252, record=False, ticks_per_day=30)
    residual = []
    for _ in range(5):
        # The close's jump lands on `s` and the open prints it too.
        closes, jumps = floats(e.prices()), floats(e.attribution("jump"))
        e.open_market()
        opens = floats(e.column("open"))
        moved = floats(e.attribution("overnight"))
        residual += [math.log(o / c) - m - j
                     for o, c, m, j in zip(opens, closes, moved, jumps)]
        e.run_session(9, 30, 3, 30)
        e.close_market()
    # What is left is the last tick's settlement against the model price, a
    # few basis points; the buyback discount would be about four per cent.
    assert max(abs(r) for r in residual) < 5e-3


def test_the_nights_student_t_draws_only_while_set():
    # The chi-square normals are taken on the overnight stream at a site of
    # their own, df per name per open, and only under a split with the dial
    # set: the stream's schedule moves on no other model.
    n = len(UNIVERSE)
    counts = {}
    for dials in ({}, {"overnight_idio_df": 4.0},
                  {"overnight_idio_share": 0.2},
                  {"overnight_idio_share": 0.2, "overnight_idio_df": 4.0}):
        e = tf.Engine(seed=3, universe=UNIVERSE, model=model(**dials))
        e.run_days(3, record=False)
        counts[tuple(dials.items())] = e.state_snapshot()["draw_counts"][15]
    plain = counts[()]
    assert counts[(("overnight_idio_df", 4.0),)] == plain
    assert counts[(("overnight_idio_share", 0.2),)] == plain
    assert counts[(("overnight_idio_share", 0.2), ("overnight_idio_df", 4.0))] == plain + 3 * 4 * n


def test_a_restore_mid_session_continues_the_same_market():
    m = model(overnight_market_share=0.5, overnight_idio_share=0.2, overnight_idio_df=3.0)
    e = tf.Engine(seed=8, universe=UNIVERSE, model=m)
    e.run_days(3, record=False)
    e.open_market()
    e.run_session(9, 30, 3, 100)
    twin = tf.Engine(seed=99, universe=UNIVERSE, model=m)
    twin.restore_state(e.state_snapshot())
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_session(11, 10, 3, 290)
        x.close_market()
        x.run_days(3, record=False, first_day=4)
    assert floats(e.prices()) == floats(twin.prices())


def _session_on_night_slope(extra, seeds=(201, 202, 203), days=250):
    """The equal-weight session return's slope on the equal-weight night."""
    nights, sessions = [], []
    for seed in seeds:
        e = tf.Engine(seed=seed, universe=UNIVERSE, model=model(
            overnight_market_share=0.6, overnight_idio_share=0.2, **extra))
        n = len(e.tickers)
        for day in range(days):
            closes = floats(e.prices())[:n]
            e.open_market()
            opens = floats(e.column("open"))[:n]
            e.run_session(9, 30, 3, 390)
            ends = floats(e.prices())[:n]
            e.close_market()
            if day:
                nights.append(sum(math.log(o / c) for o, c in zip(opens, closes)) / n)
                sessions.append(sum(math.log(p / o) for p, o in zip(ends, opens)) / n)
    mx, my = sum(nights) / len(nights), sum(sessions) / len(sessions)
    sxy = sum((x - mx) * (y - my) for x, y in zip(nights, sessions))
    sxx = sum((x - mx) ** 2 for x in nights)
    return sxy / sxx


def test_the_session_does_not_follow_the_nights_gap():
    # pt-v20's lagged down-wire is un-recentred (lag 0.46, lag recentre 0.0)
    # and read live: on the session's ticks it drifts the market down while
    # the day so far is down. The night must neither drift on that wire nor
    # set its condition, or the session follows the gap: the review of
    # 50dfeed measured +0.099 on the equal-weight market (the forty real
    # names 2015-2025: +0.022). Here the wire is exaggerated (lag 1.5) so a
    # short run shows it, and read against the same wire with no tilt, which
    # has no mean to carry: what the multiple does to the variance (the
    # part of the move that stays in `s` and reverts) is in both. Before the
    # fix the tilted arm read +0.26 (se 0.04); after it, -0.12 against the
    # untilted arm's -0.08.
    lag = {"market_beta_down_asym_lag": 1.5}
    tilted = _session_on_night_slope({"market_beta_down_asym": 0.3, **lag})
    untilted = _session_on_night_slope({"market_beta_down_asym": 0.0, **lag})
    assert abs(tilted - untilted) < 0.15


def test_the_nights_draw_is_carried_only_while_the_live_wire_reads_it():
    # pt-v20 reads the lagged wire live, so a split on the market carries
    # tonight's draw; a split on the names alone, or the wire read at the
    # open, carries nothing new.
    assert "night_market_factor" not in tf.Engine(
        seed=1, universe=UNIVERSE, model="pt-v20").state_snapshot()
    assert "night_market_factor" not in tf.Engine(
        seed=1, universe=UNIVERSE, model=model(overnight_idio_share=0.2)).state_snapshot()
    assert "night_market_factor" not in tf.Engine(
        seed=1, universe=UNIVERSE, model=model(
            overnight_market_share=0.5, market_beta_down_asym_lag_live=0.0)).state_snapshot()
    e = tf.Engine(seed=1, universe=UNIVERSE, model=model(overnight_market_share=0.5))
    e.run_days(2, record=False)
    e.open_market()
    snap = e.state_snapshot()
    # The night's draw is what the open added to the GJR's day factor.
    assert snap["night_market_factor"] == market_state(e)[1] != 0.0
    from tradefloor import manifest
    assert manifest.state_hash(snap) == e.state_hash()
    e.run_session(9, 30, 3, 390)
    e.close_market()
    assert e.state_snapshot()["night_market_factor"] == 0.0


def test_a_restore_mid_session_keys_the_live_wire_on_the_session():
    # Exaggerated so the condition's sign is often the night's: a twin
    # restored mid-session without the night's draw keys the wire on the
    # whole day and prices a different afternoon.
    m = model(overnight_market_share=0.8, market_beta_down_asym=0.3,
              market_beta_down_asym_lag=1.5)
    diverged = False
    for seed in (8, 9, 10, 11):
        e = tf.Engine(seed=seed, universe=UNIVERSE, model=m)
        e.run_days(3, record=False)
        e.open_market()
        e.run_session(9, 30, 3, 20)
        snap = e.state_snapshot()
        twin, blind = (tf.Engine(seed=99, universe=UNIVERSE, model=m) for _ in range(2))
        twin.restore_state(snap)
        stripped = dict(snap)
        del stripped["night_market_factor"]
        blind.restore_state(stripped)
        for x in (e, twin, blind):
            x.run_session(9, 50, 3, 370)
        assert floats(e.prices()) == floats(twin.prices())
        diverged |= floats(blind.prices()) != floats(e.prices())
    assert diverged


def test_the_split_is_exact_on_the_sector_and_own_draws():
    # The review of 50dfeed found no test of the idiosyncratic split: a
    # session that drew its own and sector noise at the full scale, or a
    # night kept out of the sector's day factor, passed every test. Day 0 on
    # the same seed: the session's own-noise parts scale as sqrt(1 - wi)
    # against the unsplit session's on the same draws, and the night's
    # sector draw joins the sector's day factor, scaling as sqrt(wi).
    parts, sector_nights = {}, {}
    for wi in (0.0, 0.2, 0.5):
        dials = {"sector_vol_alpha": 0.05, "sector_vol_beta": 0.9}
        if wi:
            dials["overnight_idio_share"] = wi
        e = tf.Engine(seed=5, universe=UNIVERSE, model=model(**dials))
        e.open_market()
        before = floats(e.state_snapshot()["noise_parts"])
        sector_nights[wi] = floats(e.state_snapshot()["sector_day_factor"])
        e.run_session(9, 30, 3, 390)
        after = floats(e.state_snapshot()["noise_parts"])
        parts[wi] = [a - b for a, b in zip(after, before)]
    assert all(x == 0.0 for x in sector_nights[0.0])
    for wi in (0.2, 0.5):
        assert any(x != 0.0 for x in sector_nights[wi])
        for x, y in zip(sector_nights[wi], sector_nights[0.2]):
            if y:
                assert x / y == pytest.approx(math.sqrt(wi / 0.2), rel=1e-9)
        # The session's own and sector parts (slots 1 and 2 of each name's
        # three), on the same draws, at sqrt(1 - wi) of the unsplit ones; to
        # a per cent, because the size multiplier reads a market cap the
        # night has moved.
        n = len(UNIVERSE)
        for i in range(n):
            for k in (1, 2):
                base = parts[0.0][3 * i + k]
                if abs(base) > 1e-9:
                    assert parts[wi][3 * i + k] / base == pytest.approx(
                        math.sqrt(1.0 - wi), rel=1e-2)
