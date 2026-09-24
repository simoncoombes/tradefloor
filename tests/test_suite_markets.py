"""Every market of the published suite runs on both candidate presets.

tf-suite-2026.1 is twenty markets (tradefloor-serve, suites/definitions.py):
eight with no scenario and two each under six packaged scenarios, market seed
92000 + i on `Universe.random(20, seed=93000 + i)`, sixty days of six steps.
The hosted service grades agents on it, so a market that refuses to run is a
suite that cannot be scored.

Under pt-v20 the first recession market (92019) used to refuse: the packaged
recession holds growth three points lower, the economy it met on day 50 was
already contracting at 2.3 per cent, and -5.3 per cent was under the rates'
-5 per cent floor. Growth now has its own floor, -10 per cent (US real GDP
fell 7.4 per cent year on year to 2020Q2, and 10.0 per cent at an annualised
quarterly rate in 1958Q1; FRED GDPC1), and this test holds every market to
running on both presets.
"""

import pytest

import tradefloor as tf

SUITE = dict(days=60, steps_per_day=6, ticks_per_step=65, cash=1_000_000.0,
             max_leverage=2.0)

SCENARIOS = ([None] * 8 + ["geopolitical_conflict"] * 2
             + ["liquidity_crisis"] * 2 + ["oil_price_spike"] * 2
             + ["policy_regime_shift"] * 2 + ["rate_shock"] * 2
             + ["recession"] * 2)

MARKETS = [(92000 + i, 93000 + i, sc) for i, sc in enumerate(SCENARIOS, start=1)]


@pytest.mark.parametrize("preset", ["pt-v19", "pt-v20"])
@pytest.mark.parametrize("seed,universe_seed,scenario", MARKETS,
                         ids=[f"{s}-{sc or 'plain'}" for s, _, sc in MARKETS])
def test_every_suite_market_runs(preset, seed, universe_seed, scenario):
    cards = tf.evaluate(
        {"buy_and_hold": tf.StrategySpec.hold()},
        seed=seed,
        universe=tf.Universe.random(20, seed=universe_seed),
        scenario=tf.Scenario.load(scenario) if scenario else None,
        model=tf.ModelParams.from_preset(preset),
        **SUITE,
    )
    assert cards["buy_and_hold"].return_pct == cards["buy_and_hold"].return_pct


def test_growth_alone_may_fall_below_the_rates_floor():
    """The floor is growth's alone: a rate under -5 per cent is still refused,
    and growth under -10 per cent is too."""
    assert tf.check_rate("gdp_growth", -0.08) == -0.08
    with pytest.raises(ValueError):
        tf.check_rate("gdp_growth", -0.11)
    with pytest.raises(ValueError):
        tf.check_rate("federal_funds_rate", -0.08)
