"""Every resting order in a tick's settlement book (`book_settle_every_order`).

The book the model's flow settles through inside a tick is the maker's
ladder with every agent's resting order posted into it, and it keeps 32
orders a side (`order_book::MAX_DEPTH_PER_SIDE`), trimming the worst-priced.
Past 32 orders at or inside the ladder's prices, the rest are out of that
tick's book, and a slice that would have filled them stops short. The switch
widens each settlement's cap by the number of resting orders.

Every preset ships it at 0.0, which is left out of the digest, and every
known-answer digest is the one it was. These tests hold that, that it is
inert on a market no agent trades, the gap on pt-v22 with it off, and its
closure with it on.
"""

import pytest

import tradefloor as tf

ROSTER = tf.Universe.random(20, seed=93001)
PRESET = "pt-v22"
DIAL = "book_settle_every_order"
#: Two names on this roster whose flow reaches forty one-share bids a cent
#: inside the spread within twelve ticks of 11:05 on the second day.
NAMES = ("AAT", "AAG")


def warmed(**dials) -> tf.Engine:
    """An engine one untraded day in, with the next day open and one step
    run, so the maker has quoted around a real print."""
    e = tf.Engine(seed=92001, universe=ROSTER,
                  model=tf.ModelParams.from_preset(PRESET, **dials))
    e.open_market()
    e.run_session(9, 30, 3, 390)
    e.close_market()
    e.open_market()
    e.run_session(9, 30, 3, 65)
    return e


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()[DIAL] == 0.0


@pytest.mark.parametrize("value", [-1.0, 0.5, 2.0])
def test_it_is_a_switch(value):
    with pytest.raises(tf.ValidationError, match=DIAL):
        tf.ModelParams.from_preset(PRESET, **{DIAL: value})


def test_on_is_a_different_model_and_zero_is_the_preset():
    on = tf.ModelParams.from_preset(PRESET, **{DIAL: 1.0})
    assert on.fingerprint.startswith("custom-")
    assert tf.ModelParams.from_preset(PRESET, **{DIAL: 0.0}).fingerprint == PRESET
    assert DIAL in tf.ModelParams.digest_silent_at_zero()


def test_inert_on_a_market_no_agent_trades():
    a, b = warmed(), warmed(**{DIAL: 1.0})
    assert a.prices() == b.prices()
    assert a.draws_by_stream() == b.draws_by_stream()


def _filled_per_tick(dials, ticker, sizes, ticks=12):
    """Bids of `sizes` from agents a00, a01, ... a cent inside the spread,
    then `ticks` one-tick steps from 11:05: the shares the flow filled on
    each."""
    e = warmed(**dials)
    price = round(e.book(ticker).best_bid + 0.01, 2)
    for k, q in enumerate(sizes):
        assert e.submit(f"a{k:02d}", ticker, q, limit_price=price)["resting"] == q
    out = []
    for k in range(ticks):
        m = 95 + k
        e.run_session(9 + m // 60, m % 60, 3, 1)
        out.append(sum(f["quantity"] for f in e.take_fills() if f["ticker"] == ticker))
    return out


FORTY = [1] * 40
#: The same forty shares at the same price as 32 orders: the last one of 9.
THIRTY_TWO = [1] * 31 + [9]


@pytest.mark.parametrize("ticker", NAMES)
def test_off_the_settlement_leaves_out_orders_past_32(ticker):
    """pt-v22 as shipped: forty orders fill 32 on the tick the flow reaches
    them, where the same shares as 32 orders fill on that tick in full."""
    split = _filled_per_tick({}, ticker, FORTY)
    whole = _filled_per_tick({}, ticker, THIRTY_TWO)
    first = next(k for k, q in enumerate(whole) if q)
    assert whole[first] == 40.0
    assert split[first] == 32.0


@pytest.mark.parametrize("ticker", NAMES)
def test_on_every_order_is_settled(ticker):
    on = {DIAL: 1.0}
    split = _filled_per_tick(on, ticker, FORTY)
    whole = _filled_per_tick(on, ticker, THIRTY_TWO)
    assert split == whole
    assert max(split) == 40.0
