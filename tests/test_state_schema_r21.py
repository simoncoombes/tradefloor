"""The r21 mechanisms' state under the versioned state contract.

Every key the r21 dials add to a snapshot is dial-gated: required exactly
where the engine's model sets its dial, and refused where it does not. A
few are carried only while they hold something (a day's market t scale
between an open and the close, a session's live rate mark, a priced VIX
move, an ex-date's move waiting for its tape row), and those are refused
where the dial is off and optional where it is on.

The model is R20M's vector on pt-v20 (the arm the r21 screen graded), with
the prehistory cut to five sessions so it builds in a second, and the
three dials R20M leaves off that carry state of their own turned on.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import textwrap

import pytest

import tradefloor as tf
from tradefloor.manifest import state_hash

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import snapshot_codec  # noqa: E402

UNIVERSE = tf.Universe.random(6, seed=5).with_bonds()
TICKS = 78

#: R20M's 92 overrides on pt-v20, as the screen ran them.
R20M = dict(
    cycle_equity_hazard_opening=0.007, cycle_equity_hazard=5.0,
    cycle_equity_hazard_knee=0.1, cycle_nowcast_accuracy=0.4,
    corporate_spread_cycle=0.75, fed_growth_cut=2.0,
    cycle_publication_lag_draw=1.0,
    earnings_anticipation_drift_half_life=252.0,
    earnings_anticipation_drift_share=0.9, buyback_accrual=1.0,
    rate_close_remark=1.0, rate_intraday_live=1.0, fed_stress_cut=0.1,
    fed_stress_inflation_gap=2.0, macro_pins_hold=1.0,
    market_vol_vix_coupling=0.75, flight_to_quality_gain=0.013,
    buyback_payout_share=0.9, dividend_payout_share=1.2,
    dividend_buyback_substitution=1.0, impact_memory_coefficient=0.65,
    impact_memory_half_life=12.0, impact_memory_slow_half_life=780.0,
    impact_memory_slow_weight=0.1, impact_memory_crossover=0.001,
    fill_impact_coefficient=0.15, book_arrival_shuffle=1.0,
    overnight_market_share=0.6, overnight_idio_share=0.25,
    overnight_idio_df=4.0, earnings_surprise_sigma=3.5,
    earnings_session_sigma=1.9, earnings_followthrough_sigma=1.1,
    earnings_volume_multiple=1.2, jump_intensity_idio=0.009,
    jump_sigma_idio=0.0318, idio_sigma_scale=0.52, idio_vol_alpha=0.25,
    idio_vol_beta=0.5, idio_vol_jump_bump=1.0, market_vol_slow_gamma=0.05,
    vix_stress_premium=3.0, vix_stress_premium_knee=0.6,
    vix_stress_premium_cap=0.35, fed_put_gain=3.0, fed_put_half_life=126.0,
    treasury_put_pricing=1.0, treasury_haven_gain=0.014,
    fed_stress_hold=42.0, treasury_path_pricing=1.0,
    treasury_path_half_life=63.0, treasury_policy_damping=0.5,
    corporate_spread_vix_cut=1.0, corporate_spread_equity_gain=1.8,
    corporate_spread_equity_half_life=126.0, pinned_vix_feedback=0.8,
    pinned_vix_variance_share=0.7,
    market_vol_cycle_ratio=2.4705882352941178,
    market_vol_cycle_expansion=0.82, market_vol_cycle_half_life=10.0,
    market_vol_cycle_relative=0.75, market_vol_cycle_cap_relative=1.0,
    market_vol_cycle_pin_neutral=1.0, market_vol_cycle_pin_phase=1.0,
    market_vol_leverage=2.5, market_vol_leverage_half_life=15.0,
    market_vol_leverage_standardise=1.0, vix_level_sigma=0.009,
    market_vol_vix_smooth=3.0, market_vol_gamma=0.06,
    market_vol_beta=0.9446, price_hard_cap=1000000000.0,
    book_depth_nesting=1.0, fair_value_market_excess_share=0.5,
    fair_value_vix_release_half_life=504.0, book_cross_at_limit=1.0,
    impact_memory_refill=1.0, pinned_vix_calm_knee=17.6,
    pinned_vix_calm_share=0.2, pinned_vix_priced_cap=1.0,
    policy_anticipation=2.0, fair_value_relative_knee=4.0,
    fair_value_relative_half_life=63.0, market_beta_normalise=1.0,
    market_factor_sigma=0.007099478, market_prehistory_sessions=504.0,
    market_prehistory_valuation=1.0, fed_put_carry=1.0,
    fed_put_emergency_vix=50.0, fed_drawdown_hold=0.12,
    market_vol_cycle_recovery_release=0.45,
    market_vol_cycle_recovery_scale=0.1,
)

#: R20M with a short prehistory, plus the dials R20M leaves off whose state
#: a snapshot carries: the day's t scale and the withheld earnings cycle.
EVERY_R21_DIAL = dict(R20M, market_prehistory_sessions=5.0,
                      market_day_tail_df=7.0, market_day_tail_state_share=1.0,
                      earnings_cycle_depth=0.05,
                      earnings_cycle_report_share=0.5)

#: Keys required on such a model, at the top level and in the economy.
TOP = ("market_vol_leverage_memory", "vix_stress_memory", "cycle_nowcast_rng",
       "fed_stress_vix_max", "fed_stress_hold_age", "treasury_policy_path",
       "fed_drawdown_returns", "fed_drawdown_mcap_prev",
       "policy_anticipation_priced", "buyback_log_shares", "opening_carry",
       "dividend", "earnings_key", "earnings_withheld", "idio_variance",
       "idio_jump_pending", "idio_jump_var_pending")
ECONOMY = ("cycle_nowcast", "cycle_publication", "anticipation_drift",
           "anticipation_raw", "intermeeting_return", "fed_put",
           "fed_put_owed", "fed_put_mcap_prev", "spread_equity_gap")


def _engine(model, seed=3):
    return tf.Engine(seed=seed, universe=UNIVERSE, model=model)


def _run(engine, days, first=0):
    for day in range(first, first + days):
        engine.open_market()
        engine.run_session(9, 30, day % 5, TICKS)
        engine.close_market()


@pytest.fixture(scope="module")
def model():
    return tf.ModelParams.from_preset("pt-v20", **EVERY_R21_DIAL)


def test_r20m_builds_on_pt_v20_with_the_screen_fingerprint():
    """The arm the screen and the fifteenth grade ran: custom-d4197920 on
    sim/r21. The port's digest leaves every silent switch at zero out, so
    the arm's own fingerprint is a new one, and it is stable."""
    params = tf.ModelParams.from_preset("pt-v20", **R20M)
    assert params.fingerprint.startswith("custom-")
    assert params.fingerprint == tf.ModelParams.from_preset("pt-v20", **R20M).fingerprint
    assert len(R20M) == 92


def test_every_r21_key_is_carried_on_a_model_that_sets_its_dial(model):
    engine = _engine(model)
    _run(engine, 2)
    snapshot = engine.state_snapshot()
    for key in TOP:
        assert key in snapshot, key
    for key in ECONOMY:
        assert key in snapshot["economy"], key


def test_no_r21_key_is_carried_on_pt_v20():
    engine = _engine("pt-v20")
    _run(engine, 2)
    snapshot = engine.state_snapshot()
    for key in TOP + ("pending_dividend", "market_day_scale", "rate_live_marks",
                      "pinned_vix_jump", "market_vol_cycle_log",
                      "night_market_factor", "pinned_corporate_spread"):
        assert key not in snapshot, key
    for key in ECONOMY:
        assert key not in snapshot["economy"], key
    # The attribution rows are the eleven-wide rows a 0.8.8 snapshot holds.
    assert len(snapshot["attribution"]) == 8 * 11 * len(snapshot["columns"]["price"]) // 8


@pytest.mark.parametrize("key", TOP)
def test_a_top_level_key_the_model_needs_is_refused_by_name(model, key):
    engine = _engine(model)
    _run(engine, 1)
    snapshot = engine.state_snapshot()
    del snapshot[key]
    with pytest.raises(tf.ValidationError, match=key):
        _engine(model).restore_state(snapshot)


@pytest.mark.parametrize("key", ECONOMY)
def test_an_economy_key_the_model_needs_is_refused_by_name(model, key):
    engine = _engine(model)
    _run(engine, 1)
    snapshot = engine.state_snapshot()
    del snapshot["economy"][key]
    with pytest.raises(tf.ValidationError, match=key):
        _engine(model).restore_state(snapshot)


@pytest.mark.parametrize("key, value", [
    ("fed_drawdown_returns", b""), ("market_vol_leverage_memory", 0.0),
    ("market_day_scale", 1.5), ("pinned_vix_jump", 0.01),
    ("pending_dividend", b"\0" * 8)])
def test_an_r21_key_on_a_model_without_its_dial_is_refused(key, value):
    engine = _engine("pt-v20")
    _run(engine, 1)
    snapshot = engine.state_snapshot()
    snapshot[key] = value
    with pytest.raises(tf.ValidationError, match=key):
        _engine("pt-v20").restore_state(snapshot)


def test_a_mid_day_snapshot_round_trips_and_runs_on_as_one_run(model):
    control, paused = _engine(model), _engine(model)
    for engine in (control, paused):
        _run(engine, 2)
        engine.open_market()
        engine.run_session(9, 30, 2, 40)
    snapshot = paused.state_snapshot()
    assert state_hash(snapshot) == paused.state_hash()
    restored = _engine(model, seed=9)
    restored.restore_state(snapshot)
    assert restored.state_hash() == control.state_hash()
    for engine in (control, restored):
        engine.run_session(10, 10, 2, TICKS - 40)
        engine.close_market()
        _run(engine, 3, first=3)
    assert restored.column("price") == control.column("price")
    assert restored.state_hash() == control.state_hash()


_RESUME = textwrap.dedent("""
    import json, sys
    sys.path.insert(0, {tests!r})
    import tradefloor as tf
    import snapshot_codec
    doc = json.load(open({path!r}))
    model = tf.ModelParams.from_preset("pt-v20", **doc["model"])
    universe = tf.Universe.from_json(doc["universe"])
    engine = tf.Engine(seed=doc["seed"], universe=universe, model=model)
    engine.restore_state(snapshot_codec.loads(doc["snapshot"]))
    out = []
    for day in range(3, 6):
        engine.open_market()
        engine.run_session(9, 30, day % 5, {ticks})
        engine.close_market()
        out.append([engine.state_hash(), engine.column("price").hex()])
    print(json.dumps(out))
""")


def test_a_closed_snapshot_resumed_in_a_fresh_process_matches_one_run(model, tmp_path):
    control, paused = _engine(model), _engine(model)
    _run(control, 3)
    _run(paused, 3)
    doc = {"seed": 3, "universe": UNIVERSE.to_json(), "model": EVERY_R21_DIAL,
           "snapshot": snapshot_codec.dumps(paused.state_snapshot())}
    path = tmp_path / "paused.json"
    path.write_text(json.dumps(doc))
    script = _RESUME.format(tests=str(pathlib.Path(__file__).resolve().parent),
                            path=str(path), ticks=TICKS)
    done = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, check=True)
    expected = []
    for day in range(3, 6):
        control.open_market()
        control.run_session(9, 30, day % 5, TICKS)
        control.close_market()
        expected.append([control.state_hash(), control.column("price").hex()])
    assert json.loads(done.stdout) == expected


def test_a_ledger_of_r21_snapshots_round_trips_through_json(model):
    engine = _engine(model)
    ledger = tf.manifest.DayLedger()
    for day in range(2):
        _run(engine, 1, first=day)
        ledger.close(engine)
    loaded = tf.manifest.DayLedger.from_json(ledger.to_json())
    assert [state_hash(s) for s in loaded.snapshots] == loaded.leaves == ledger.leaves
    restored = _engine(model, seed=9)
    restored.restore_state(loaded.snapshots[-1])
    assert restored.state_hash() == engine.state_hash()
