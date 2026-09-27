"""Cash dividends (`dividend_payout_share` and its four companions).

Off on every shipped preset, where nothing is read and nothing is stored.
On, a paying name goes ex every 63 sessions; its price drops by the declared
amount exactly at the ex-date open, fair value accrues the amount between
ex-dates, and a holder is credited the cash (a short pays it). These tests
hold the mechanism, the snapshot, and the harness's accounting identities.
"""

import struct

import pytest

pa = pytest.importorskip("pyarrow")

import tradefloor as tf

UNIVERSE = list(tf.Universe.random(12, seed=3))
SEED = 2001
TICKS = 20
DIALS = ("dividend_payout_share", "dividend_growth_cutoff",
         "dividend_adjustment_speed", "dividend_yield_ceiling",
         "dividend_buyback_substitution")
ON = dict(dividend_payout_share=1.2, dividend_buyback_substitution=1.0,
          buyback_payout_share=1.1)


def f64(raw):
    return list(struct.unpack("<%dd" % (len(raw) // 8), raw))


def table(stream):
    return pa.table(stream)


def model(**kw):
    return tf.ModelParams.from_preset("pt-v20", **kw)


def engine(m=None):
    return tf.Engine(seed=SEED, universe=UNIVERSE, model=m or model(**ON))


def day(e):
    e.open_market()
    e.run_session(9, 30, 3, TICKS)
    e.close_market()


def states(e):
    """Each name's dividend state from the snapshot, as dicts."""
    raw = e.state_snapshot().get("dividend")
    if raw is None:
        return None
    v = f64(raw)
    keys = ("payout", "target_yield", "price_ema", "amount", "declared",
            "accrual", "paid_today")
    return [dict(zip(keys, v[7 * i:7 * i + 7])) for i in range(len(v) // 7)]


# -- off -------------------------------------------------------------------


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    d = tf.ModelParams.from_preset(preset).to_dict()
    assert d["dividend_payout_share"] == 0.0
    assert d["dividend_buyback_substitution"] == 0.0


def test_the_companions_read_nothing_with_the_dial_at_zero():
    base = engine(model())
    other = engine(model(dividend_growth_cutoff=0.05,
                         dividend_adjustment_speed=0.9,
                         dividend_yield_ceiling=5.0,
                         dividend_buyback_substitution=1.0))
    for _ in range(5):
        day(base)
        day(other)
    assert base.prices() == other.prices()
    assert base.column("mispricing_s") == other.column("mispricing_s")
    snap = other.state_snapshot()
    assert "dividend" not in snap and "pending_dividend" not in snap
    assert set(f64(other.column("dividend"))) == {0.0}
    assert table(other.distributions()).num_rows == 0
    assert "distribution" not in table(other.prints()).column_names
    assert set(f64(other.attribution("dividend"))) <= {0.0}


# -- the mechanism -----------------------------------------------------------


@pytest.fixture(scope="module")
def run():
    """70 sessions with dividends on: every name passes an ex-date."""
    e = engine()
    rows = []
    for _ in range(70):
        prev = f64(e.prices())
        e.open_market()
        rows.append(dict(prev=prev, open=f64(e.column("open")),
                         paid=list(e.dividends_today()),
                         states=states(e), day=e.day_count))
        e.run_session(9, 30, 3, TICKS)
        e.close_market()
    return e, rows


def test_every_payer_goes_ex_and_the_drop_is_the_amount(run):
    e, rows = run
    n = len(UNIVERSE)
    payers = {i for i, s in enumerate(rows[-1]["states"]) if s["target_yield"] > 0}
    assert len(payers) >= n // 2
    went = set()
    for r in rows[1:]:
        for i, d in enumerate(r["paid"][:n]):
            if d > 0:
                went.add(i)
                # pt-v20 has no overnight move: the open is the close less
                # the amount, to the bit's rounding.
                assert r["prev"][i] - r["open"][i] == pytest.approx(d, rel=1e-9, abs=1e-12)
    assert went == payers


def test_non_payers_carry_a_zero_state(run):
    _, rows = run
    for i, s in enumerate(rows[-1]["states"]):
        inst = UNIVERSE[i]
        if inst.eps is not None and inst.eps <= 0:
            assert s["payout"] == 0.0 and s["target_yield"] == 0.0


def test_the_accrual_rises_by_a_63rd_of_the_declared_amount(run):
    _, rows = run
    n = len(UNIVERSE)
    for a, b in zip(rows, rows[1:]):
        for i in range(n):
            sa, sb = a["states"][i], b["states"][i]
            if sb["target_yield"] == 0.0:
                assert sb["accrual"] == 0.0
                continue
            if b["paid"][i] > 0:
                assert sb["accrual"] == 0.0
            elif sa["declared"] and sb["declared"] and sa["amount"] == sb["amount"]:
                assert sb["accrual"] - sa["accrual"] == pytest.approx(
                    sb["amount"] / 63.0, rel=1e-9, abs=1e-15)


def test_the_distributions_table_is_announced_ahead(run):
    e, rows = run
    t = table(e.distributions()).to_pydict()
    assert t["kind"] and set(t["kind"]) == {"cash"}
    assert t["day"] == t["ex_day"]
    for decl, ex, amt in zip(t["declared_day"], t["ex_day"], t["amount"]):
        assert decl <= ex and amt > 0
        assert ex - decl == 21 or decl == 0
    # Every payment was announced, at the amount paid.
    announced = {(i, x): a for i, x, a in zip(t["instrument_id"], t["ex_day"], t["amount"])}
    for r in rows:
        for i, d in enumerate(r["paid"][:len(UNIVERSE)]):
            if d > 0:
                assert announced[(i, r["day"])] == d


def test_the_tape_carries_the_distribution_column():
    e = engine()
    e.run_days(70, ticks_per_day=TICKS, record=True)
    prints = table(e.prints()).to_pydict()
    assert "distribution" in prints
    paid = {(d, i): a for d, i, a, x in zip(
        prints["day"], prints["instrument_id"], prints["distribution"],
        prints["tick"]) if a}
    assert paid
    t = table(e.distributions()).to_pydict()
    announced = {(x, i): a for x, i, a in zip(t["ex_day"], t["instrument_id"], t["amount"])}
    for key, amount in paid.items():
        assert announced[key] == amount


def test_a_snapshot_restores_and_continues_identically():
    parent = engine()
    for _ in range(30):
        day(parent)
    restored = engine()
    restored.restore_state(parent.state_snapshot())
    assert states(restored) == states(parent)
    assert restored.state_hash() == parent.state_hash()
    fork, = tf.branch(parent, 1)
    for _ in range(40):
        day(parent)
        day(restored)
        day(fork)
    assert restored.prices() == parent.prices() == fork.prices()
    assert restored.state_hash() == parent.state_hash()


def test_the_dial_changes_the_market_and_its_state_hash():
    a, b = engine(model()), engine()
    for _ in range(3):
        day(a)
        day(b)
    assert a.prices() != b.prices()


@pytest.mark.parametrize("name,value", [
    ("dividend_payout_share", -0.1), ("dividend_payout_share", 2.5),
    ("dividend_growth_cutoff", -1.0), ("dividend_adjustment_speed", 0.0),
    ("dividend_yield_ceiling", 0.5), ("dividend_buyback_substitution", 0.5),
])
def test_out_of_range_is_refused(name, value):
    with pytest.raises(Exception):
        model(**{name: value})


# -- the harness's accounting ------------------------------------------------


def _payer_and_ex_day():
    """A paying name and the session it first goes ex (after day 5)."""
    e = engine()
    for _ in range(70):
        e.open_market()
        paid = e.dividends_today()
        if e.day_count > 5:
            for i, d in enumerate(paid[:len(UNIVERSE)]):
                if d > 0:
                    return UNIVERSE[i].ticker, i, e.day_count
        e.run_session(9, 30, 3, TICKS)
        e.close_market()
    raise AssertionError("no payer went ex")


def test_buy_and_hold_earns_price_plus_the_dividends_exactly():
    e = engine()
    p = tf.Portfolio(cash=1e7, max_leverage=None)
    e.open_market()
    for inst in UNIVERSE:
        p.execute(e, inst.ticker, 1000)
    held = {t: pos.quantity for t, pos in p.positions.items()}
    cash0 = p.cash
    e.run_session(9, 30, 3, TICKS)
    e.close_market()
    owed = 0.0
    for _ in range(69):
        e.open_market()
        paid = e.dividends_today()
        owed += sum(held[inst.ticker] * paid[i] for i, inst in enumerate(UNIVERSE))
        p.collect_dividends(e)
        p.collect_dividends(e)  # a second call on one open credits nothing
        e.run_session(9, 30, 3, TICKS)
        e.close_market()
    prices = f64(e.prices())
    total = cash0 + sum(held[inst.ticker] * prices[i] for i, inst in enumerate(UNIVERSE)) + owed
    assert owed > 0
    assert p.dividends == pytest.approx(owed, rel=1e-12)
    assert p.net_worth(e) == pytest.approx(total, rel=1e-9)


def test_a_short_pays_and_a_pair_nets_zero():
    ticker, i, ex = _payer_and_ex_day()
    e = engine()
    long_, short = tf.Portfolio(cash=1e6), tf.Portfolio(cash=1e6)
    amount = None
    while True:
        e.open_market()
        long_.collect_dividends(e)
        short.collect_dividends(e)
        if e.day_count == ex:
            amount = e.dividends_today()[i]
            break
        if e.day_count == ex - 1:
            long_.execute(e, ticker, 500)
            short.execute(e, ticker, -500)
        e.run_session(9, 30, 3, TICKS)
        e.close_market()
    assert amount > 0
    assert short.dividends == pytest.approx(-500 * amount)
    assert long_.dividends + short.dividends == pytest.approx(0.0, abs=1e-9)
    assert short.distributions[-1]["cash"] == pytest.approx(-500 * amount)


def test_a_resting_buy_limit_is_lowered_by_the_amount():
    ticker, i, ex = _payer_and_ex_day()
    e = engine()
    while True:
        e.open_market()
        if e.day_count == ex - 1:
            e.run_session(9, 30, 3, TICKS)
            price = f64(e.prices())[i]
            e.submit("rester", ticker, 10, limit_price=round(price * 0.5, 2))
            e.submit("seller", ticker, -10, limit_price=round(price * 2.0, 2))
            before = {o["agent"]: o["limit_price"] for o in e.open_orders()}
            e.close_market()
            continue
        if e.day_count == ex:
            amount = e.dividends_today()[i]
            after = {o["agent"]: o["limit_price"] for o in e.open_orders()}
            break
        e.run_session(9, 30, 3, TICKS)
        e.close_market()
    assert amount > 0
    assert after["rester"] == pytest.approx(before["rester"] - amount)
    assert after["seller"] == before["seller"]


class _Holder:
    def __init__(self):
        self.done = False

    def act(self, obs):
        if self.done:
            return {}
        self.done = True
        return {t: 100 for t in obs.tickers}


def test_evaluate_reports_the_dividends():
    on = tf.evaluate({"hold": _Holder()}, seed=SEED, universe=UNIVERSE,
                     days=70, steps_per_day=1, ticks_per_step=TICKS,
                     model=model(**ON), max_leverage=None)
    off = tf.evaluate({"hold": _Holder()}, seed=SEED, universe=UNIVERSE,
                      days=5, steps_per_day=1, ticks_per_step=TICKS,
                      model=model(), max_leverage=None)
    assert on["hold"].dividends > 0
    assert off["hold"].dividends == 0.0


def test_the_gym_collects_them():
    np = pytest.importorskip("numpy")
    from tradefloor.gym import TradingEnv
    env = TradingEnv(universe=UNIVERSE, seed=SEED, days=70, steps_per_day=1,
                     ticks_per_step=TICKS, model=model(**ON), max_leverage=None)
    env.reset(seed=SEED)
    action = np.full(len(UNIVERSE), 0.05)
    for _ in range(69):
        env.step(action)
    assert env._portfolio.dividends > 0


def test_buy_and_hold_reinvests_its_dividends():
    """The baseline earns the total return: each dividend buys whole shares
    of the name that paid it, so its cash stays near what the first trade
    left and its share count grows; with `reinvest_dividends=False` the
    dividends stay as cash."""
    from tradefloor.baselines import BuyAndHold
    kw = dict(seed=SEED, universe=UNIVERSE, days=70, steps_per_day=2,
              ticks_per_step=TICKS, model=model(**ON), max_leverage=None)
    drip = tf.evaluate({"bh": BuyAndHold()}, **kw)["bh"]
    cash = tf.evaluate({"bh": BuyAndHold(reinvest_dividends=False)}, **kw)["bh"]
    assert drip.dividends > cash.dividends > 0
    assert drip.trades > cash.trades
