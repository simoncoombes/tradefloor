"""Order-flow impact divides out a name's depth once under `order_flow_depth_law`.

`order_imbalance` returns participation: a tick's flow over the name's average
minute volume, times the participation multiplier. The shipped
`calculate_live_factors` then multiplies that by `liquidity_factor`, one over
a minute volume again. So at equal participation the shipped impact falls as
depth, and at a fixed share count as depth squared (issue #182).

The cited law is linear in order-flow imbalance over depth (Cont, Kukanov and
Stoikov, Journal of Financial Econometrics 12(1) 47-88, 2014), so impact in
return terms at equal participation does not depend on depth. Under the
switch the depth is divided out once, in the participation term, and the
coefficient is restated at the daily volume `order_imbalance` already
assumes for a name that reports none, one million shares. A name at that
volume is charged exactly what the shipped law charges it.

Everything here reads `Engine.attribution("order_flow_impact")`, the law's own
output before the cent grid, the session breaker and the price path. A cost
in basis points read off prices is quantised at a cent, which is 20 bps on a
five-dollar name, and would make an exponent a reading of the grid.
"""

from __future__ import annotations

import math
import struct

import pytest

import tradefloor as tf

#: Thin, medium and mega-cap, built by hand. Average minute volumes of 154,
#: 6,154 and 230,769 shares span three and a half orders of magnitude, all
#: above the 100-share floor. Half a per cent of the shares outstanding is
#: half the average volume, so the shipped law's two depth definitions agree
#: and its exponents are its own. Prices run against depth (the thinnest name
#: is the dearest) so a price term would show.
ROSTER = [
    tf.Instrument("THIN", "technology", initial_price=180.0,
                  shares_outstanding=6.0e6, avg_volume=60_000.0),
    tf.Instrument("MID", "industrials", initial_price=12.0,
                  shares_outstanding=2.4e8, avg_volume=2_400_000.0),
    tf.Instrument("MEGA", "financial_services", initial_price=45.0,
                  shares_outstanding=9.0e9, avg_volume=90_000_000.0),
]
TICKERS = [i.ticker for i in ROSTER]
MINUTE = {i.ticker: max(i.avg_volume / 390.0, 100.0) for i in ROSTER}

#: The daily volume the corrected law is restated at.
REFERENCE_DAILY_VOLUME = 1_000_000.0


def model(base: str = "pt-v20", **dials) -> tf.ModelParams:
    return tf.ModelParams.from_preset(base, **dials)


def shocks(m: tf.ModelParams, flow: dict[str, tuple[float, float]], *,
           universe=ROSTER, seed: int = 42, ticks: int = 390) -> dict[str, float]:
    """A session's accumulated ``order_flow_impact`` per name, in log units.

    ``flow`` is held on every tick. Each tick adds the factor over 390 to the
    name's log mispricing, so a 390-tick session adds the factor once: the
    column is the law's per-day rate.
    """
    e = tf.Engine(seed=seed, universe=universe, model=m)
    e.open_market()
    e.run_session(9, 30, 3, ticks, flow_per_tick=flow)
    e.close_market()
    raw = e.attribution("order_flow_impact")
    values = struct.unpack("<%dd" % (len(raw) // 8), raw)
    return dict(zip([i.ticker for i in universe], values))


def at_participation(phi: float) -> dict[str, tuple[float, float]]:
    return {t: (phi * MINUTE[t], 0.0) for t in TICKERS}


def log_slope(xs, ys) -> float:
    lx = [math.log(x) for x in xs]
    ly = [math.log(y) for y in ys]
    mx, my = sum(lx) / len(lx), sum(ly) / len(ly)
    return (sum((x - mx) * (y - my) for x, y in zip(lx, ly))
            / sum((x - mx) ** 2 for x in lx))


# -- The exponents ------------------------------------------------------------

#: The tolerance is rounding. The column is computed before any price, so the
#: three names' values at equal participation differ by at most the last bit
#: of ``phi * v / v``; over a log-depth range of 7.3 that is a slope of order
#: 1e-16.
EXPONENT_TOLERANCE = 1e-9


@pytest.mark.parametrize("phi", [0.5, 5.0, 50.0])
def test_at_equal_participation_the_corrected_impact_does_not_depend_on_depth(phi):
    """Exponent 0 on depth and 0 on price, at a participation under the
    shipped floor, one inside the band and one past the ceiling."""
    out = shocks(model(order_flow_depth_law=1.0), at_participation(phi))
    values = [out[t] for t in TICKERS]
    assert all(v > 0 for v in values), out
    assert log_slope([MINUTE[t] for t in TICKERS], values) == pytest.approx(
        0.0, abs=EXPONENT_TOLERANCE)
    assert log_slope([i.initial_price for i in ROSTER], values) == pytest.approx(
        0.0, abs=EXPONENT_TOLERANCE)


@pytest.mark.parametrize("phi", [0.5, 5.0, 50.0])
def test_at_equal_participation_the_shipped_impact_falls_as_depth(phi):
    """The legacy exponent, pinned so it cannot drift: -1 at equal
    participation, and the level is the formula's, to the bit."""
    out = shocks(model(), at_participation(phi))
    values = [out[t] for t in TICKERS]
    assert log_slope([MINUTE[t] for t in TICKERS], values) == pytest.approx(
        -1.0, abs=EXPONENT_TOLERANCE)
    multiplier = max(0.2, min(phi, 10.0) * 0.15)
    for t in TICKERS:
        assert out[t] == pytest.approx(multiplier / MINUTE[t] * 50.0 * 0.35,
                                       rel=1e-12), t


def test_at_a_fixed_share_count_the_exponent_is_minus_one_corrected_and_minus_two_shipped():
    """The same order in shares on every name. Under the square-root
    participation law all three are below its knee, where it is linear, so
    the exponent is the depth law's alone."""
    flow = {t: (1_000.0, 0.0) for t in TICKERS}
    depths = [MINUTE[t] for t in TICKERS]
    corrected = shocks(model(order_flow_impact_law=1.0, order_flow_depth_law=1.0), flow)
    shipped = shocks(model(order_flow_impact_law=1.0), flow)
    assert log_slope(depths, [corrected[t] for t in TICKERS]) == pytest.approx(
        -1.0, abs=EXPONENT_TOLERANCE)
    assert log_slope(depths, [shipped[t] for t in TICKERS]) == pytest.approx(
        -2.0, abs=EXPONENT_TOLERANCE)


def test_at_the_reference_volume_the_two_laws_charge_the_same():
    """The restatement leaves a name trading a million shares a day exactly
    where it was, and moves every other name by its depth over that one."""
    ref = [tf.Instrument("REF", "technology", initial_price=40.0,
                         shares_outstanding=1.0e8,
                         avg_volume=REFERENCE_DAILY_VOLUME)]
    minute = REFERENCE_DAILY_VOLUME / 390.0
    for phi in (0.5, 5.0, 50.0):
        flow = {"REF": (phi * minute, 0.0)}
        assert (shocks(model(order_flow_depth_law=1.0), flow, universe=ref)
                == shocks(model(), flow, universe=ref))
    on = shocks(model(order_flow_depth_law=1.0), at_participation(5.0))
    off = shocks(model(), at_participation(5.0))
    for t in TICKERS:
        assert on[t] / off[t] == pytest.approx(MINUTE[t] / minute, rel=1e-12), t


def test_the_corrected_law_is_monotone_in_size_and_symmetric_in_sign():
    m = model(order_flow_impact_law=1.0, order_flow_depth_law=1.0)
    for t in TICKERS:
        sizes = [MINUTE[t] * k for k in (0.01, 0.1, 1.0, 10.0, 100.0)]
        buys = [shocks(m, {t: (q, 0.0)})[t] for q in sizes]
        assert all(b > a for a, b in zip(buys, buys[1:])), (t, buys)
        sells = [shocks(m, {t: (0.0, q)})[t] for q in sizes]
        assert sells == [-b for b in buys], t


# -- Inert without flow ------------------------------------------------------


def _trajectory(m, seed, *, days=3):
    """Every close, every attribution column and the state hash."""
    e = tf.Engine(seed=seed, universe=tf.Universe.random(8, seed=11), model=m)
    out = []
    for _ in range(days):
        e.open_market()
        e.run_session(9, 30, 3, 390)
        out.append(e.prices())
        for factor in ("order_flow_impact", "random_noise", "company_news"):
            out.append(e.attribution(factor))
        e.close_market()
        out.append(e.prices())
    out.append(e.state_hash())
    return out


@pytest.mark.parametrize("preset", ["pt-v20", "pt-v19", "pt-v12"])
def test_an_untraded_run_is_bit_identical_with_the_switch_on(preset):
    """No injected flow means a zero imbalance, which either depth law
    multiplies to `+0.0`. The state hash covers the model's fingerprint, and
    a switch at zero leaves that where it was, so it is compared only for
    the run with the switch off against the plain preset."""
    for seed in (1, 7, 2026):
        off = _trajectory(model(preset), seed)
        on = _trajectory(model(preset, order_flow_depth_law=1.0), seed)
        assert off[:-1] == on[:-1], (preset, seed)
        assert _trajectory(tf.ModelParams.from_preset(preset), seed) == off


def test_evaluate_with_the_reference_agents_is_unchanged_on_pt_v20():
    """On pt-v20 an agent's equity fills are priced by the linear
    `fill_impact_coefficient` law and never reach the order-flow channel,
    so the switch changes nothing an agent sees."""
    from tradefloor.baselines import reference_agents

    universe = tf.Universe.random(6, seed=5)

    def cards(m):
        scores = tf.evaluate(reference_agents(seed=3), seed=17, universe=universe,
                             days=2, model=m)
        return {k: (c.pnl, c.final_net_worth, list(c.equity_curve), c.impact_bps,
                    c.trades) for k, c in scores.items()}

    assert cards(model("pt-v20")) == cards(model("pt-v20", order_flow_depth_law=1.0))


def test_evaluate_on_a_preset_without_the_linear_fill_law_does_move():
    """The scope of 'inert', stated by measurement. Before pt-v20,
    `fill_impact_coefficient` is 0.0 and an agent's fills reach the market
    through the order-flow channel on the next tick, so turning the switch
    on there changes what agents pay and earn."""
    from tradefloor.baselines import reference_agents

    universe = tf.Universe.random(6, seed=5)

    def pnl(m):
        scores = tf.evaluate(reference_agents(seed=3), seed=17, universe=universe,
                             days=2, model=m)
        return {k: c.pnl for k, c in scores.items()}

    assert pnl(model("pt-v19")) != pnl(model("pt-v19", order_flow_depth_law=1.0))


# -- Identity ----------------------------------------------------------------


def test_the_switch_at_zero_is_the_preset_and_on_is_a_custom_model():
    """A switch at zero is the model that existed before it, so it leaves
    every fingerprint, and every state hash and manifest that carries one,
    where it was. Turned on, the model is honestly custom."""
    for preset in ("pt-v20", "pt-v19", "pt-v1"):
        assert model(preset, order_flow_depth_law=0.0).fingerprint == preset
        assert model(preset, order_flow_depth_law=-0.0).fingerprint == preset
        assert model(preset, order_flow_depth_law=1.0).fingerprint.startswith("custom-")
    custom = model("pt-v19", book_resting=1.0)
    assert (model("pt-v19", book_resting=1.0, order_flow_depth_law=0.0).fingerprint
            == custom.fingerprint)
    assert (model("pt-v19", book_resting=1.0, order_flow_depth_law=1.0).fingerprint
            != custom.fingerprint)


def test_a_custom_model_written_without_the_switch_still_replays():
    """A manifest from a build that did not know the switch carries no value
    for it. `from_dict` reads that as 0.0, and the rebuilt model must hash
    to the name it was recorded under, or the replay is refused."""
    written = model("pt-v19", book_resting=1.0).to_dict()
    del written["order_flow_depth_law"]
    rebuilt = tf.ModelParams.from_dict(written)
    assert rebuilt.fingerprint == written["name"]
