"""The buyback factor on the default preset: accrued along the path, and a
mid-run listing starts with none.

MODEL.md's known gaps: through pt-v20 the buyback factor is re-evaluated
each tick at the current price over the engine's global session count, so a
name listed mid-run is credited with buybacks from before it existed.
pt-v21 and pt-v22 close both under `buyback_accrual`: each name carries its
own log share-count reduction `L`, which the close adds to and which does not
move within a session, and a listing starts at 0. `tests/test_buyback_accrual.py`
holds the mechanism on pt-v20 with the switch on; these tests hold it on the
default preset as shipped.
"""

import math
import struct

import tradefloor as tf

UNIVERSE = list(tf.Universe.random(12, seed=3))
#: A thirteenth name, listed after 251 sessions.
NEW = tf.Universe.random(13, seed=77)[12]
PRESET = "pt-v22"


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def log_shares(e):
    return floats(e.state_snapshot()["buyback_log_shares"])


def engine():
    e = tf.Engine(seed=7, universe=UNIVERSE, model=tf.ModelParams.from_preset(PRESET))
    e.run_days(250, record=False)
    return e


def test_the_default_accrues_the_buyback():
    d = tf.ModelParams.from_preset(PRESET).to_dict()
    assert d["buyback_accrual"] == 1.0
    assert d["buyback_payout_share"] != 0.0


def test_the_share_count_does_not_move_within_a_session():
    e = engine()
    before = log_shares(e)
    assert max(before) > 0.0
    e.open_market()
    e.run_session(9, 30, 3, 389)
    assert log_shares(e) == before
    e.run_session(15, 59, 3, 1)
    e.close_market()
    assert log_shares(e) != before


def test_a_listing_starts_with_no_buybacks_and_accrues_its_own():
    e = engine()
    e.run_days(1, record=False, first_day=250)
    slot = e.list_instrument(NEW)
    assert log_shares(e)[slot] == 0.0
    payout = tf.ModelParams.from_preset(PRESET).to_dict()["buyback_payout_share"]
    # One session's yield at the listing's own terms: what the term that
    # stood would have credited at once is the whole 251 sessions' worth.
    step = min(payout * NEW.eps / NEW.initial_price, 0.15) / 252.0
    stood = math.exp(step * 251) - 1.0
    seen = []
    for day in range(251, 256):
        e.run_days(1, record=False, first_day=day)
        seen.append(log_shares(e)[slot])
    assert all(b > a for a, b in zip([0.0] + seen, seen))
    # Each close adds about one step, never the history before the listing.
    assert 0.5 * step < seen[0] < 2.0 * step
    assert seen[-1] < 10.0 * step < stood
