"""Every dial-gated snapshot key, one mechanism at a time, in a fresh process.

`test_state_schema_r21.py` turns every r21 dial on at once. This file turns
on one mechanism at a time (its dials at R20M's values, on pt-v20), so a key
whose state is lost, or read back in the wrong order, cannot be covered by
another mechanism's state moving the same prices. For each case:

* the run is cut three times: at a close, after the next open and before
  its first tick, and in the middle of that session; each snapshot goes
  through the lossless JSON form into a fresh Python process,
  is restored onto an engine built from another seed, and runs on to the
  end; every later position must match an uninterrupted run bit for bit
  (the state hash and the price column);
* the case's keys must be carried at the cut that holds them, so the round
  trip is known to exercise them;
* a snapshot lacking a key the model needs is refused, and the refusal names
  the key; a key carried only while it holds something is refused by name on
  a model without its dial.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import textwrap

import pytest

import tradefloor as tf
from tradefloor.population import Population

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import snapshot_codec  # noqa: E402

UNIVERSE = tf.Universe.random(6, seed=5).with_bonds()
SEED = 3
TICKS = 78
FIRST = 40          # ticks before the mid-session cut
DAYS = 12
#: A VIX spike on day 1 for every case, so the stress mechanisms (the Fed
#: put, the stress cut and hold, the drawdown window) hold something at the
#: cuts rather than their calm defaults.
SPIKE = {1: dict(vix=45.0, vix_sets_variance=True)}
#: Three positions a day: the open, the first ticks, the rest and the close.
#: The cuts fall on day 3: after day 2's close, after day 3's open (before
#: any tick has read what the open drew) and in the middle of day 3.
STEPS = 3
CUT_DAY = 3

#: The dials of each mechanism, at R20M's values where R20M sets them.
LEVERAGE = dict(market_vol_leverage=2.5, market_vol_leverage_half_life=15.0,
                market_vol_leverage_standardise=1.0)
CYCLE_VOL = dict(market_vol_cycle_ratio=2.4705882352941178,
                 market_vol_cycle_expansion=0.82, market_vol_cycle_half_life=10.0,
                 market_vol_cycle_relative=0.75, market_vol_cycle_cap_relative=1.0,
                 market_vol_cycle_pin_neutral=1.0, market_vol_cycle_pin_phase=1.0)
STRESS = dict(fed_stress_cut=0.1, fed_stress_inflation_gap=2.0)
FED_PUT = dict(fed_put_gain=3.0, fed_put_half_life=126.0, fed_put_carry=1.0,
               fed_put_emergency_vix=50.0)
OVERNIGHT = dict(overnight_market_share=0.6, overnight_idio_share=0.25,
                 overnight_idio_df=4.0)
#: A surprise is realised at the opening print, which only a night split
#: prices, so the earnings dials need R20M's split too.
EARNINGS = dict(OVERNIGHT, earnings_surprise_sigma=3.5, earnings_session_sigma=1.9,
                earnings_followthrough_sigma=1.1, earnings_volume_multiple=1.2)
IDIO = dict(idio_vol_alpha=0.25, idio_vol_beta=0.5, idio_vol_jump_bump=1.0,
            jump_intensity_idio=0.009, jump_sigma_idio=0.0318)
MEMORY = dict(impact_memory_coefficient=0.65, impact_memory_half_life=12.0,
              impact_memory_slow_half_life=780.0, impact_memory_slow_weight=0.1,
              impact_memory_crossover=0.001, impact_memory_refill=1.0,
              fill_impact_coefficient=0.15, book_arrival_shuffle=1.0,
              book_depth_nesting=1.0, book_cross_at_limit=1.0)
MACRO = dict(unemployment_okun_coefficient=0.75, unemployment_natural_pull=0.1,
             oil_inventory_reversion=0.002, oil_inflation_passthrough=1.0)

from test_state_schema_r21 import EVERY_R21_DIAL  # noqa: E402

#: name -> (overrides on pt-v20, top-level keys, economy keys, held keys,
#: options). A held key is carried only while it holds something; its
#: absence is a value, so it is checked for presence at the mid-session cut
#: and refused on pt-v20 rather than refused when missing.
CASES = {
    "pt-v20": ({}, ("vix_anchor_slow", "fair_value_offset", "opening_z"),
               ("vix_feedback", "cycle_history", "unemployment_impulse",
                "gdp_publication", "earnings_cycle"), (), {}),
    "garch_cascade": (dict(garch_cascade_components=3.0), ("garch_cascade",), (), (), {}),
    "market_vol_leverage": (LEVERAGE, ("market_vol_leverage_memory",), (), (), {}),
    "market_vol_cycle": (CYCLE_VOL, (), (), ("market_vol_cycle_log",), {}),
    "vix_stress_premium": (dict(vix_stress_premium=3.0, vix_stress_premium_knee=0.6,
                                vix_stress_premium_cap=0.35),
                           ("vix_stress_memory",), (), (), {}),
    "cycle_nowcast": (dict(cycle_nowcast_accuracy=0.4), ("cycle_nowcast_rng",),
                      ("cycle_nowcast",), (), {}),
    "fed_stress_cut": (STRESS, ("fed_stress_vix_max",), (), (), {}),
    "fed_stress_hold": (dict(STRESS, fed_stress_hold=42.0),
                        ("fed_stress_vix_max", "fed_stress_hold_age"), (), (), {}),
    "treasury_path": (dict(treasury_path_pricing=1.0, treasury_path_half_life=63.0,
                           treasury_policy_damping=0.5),
                      ("treasury_policy_path",), (), (), {}),
    "fed_drawdown_hold": (dict(FED_PUT, fed_drawdown_hold=0.12),
                          ("fed_drawdown_returns", "fed_drawdown_mcap_prev"),
                          ("intermeeting_return", "fed_put", "fed_put_owed",
                           "fed_put_mcap_prev"), (), {}),
    "cycle_recovery_release": (dict(CYCLE_VOL, market_vol_cycle_recovery_release=0.45,
                                    market_vol_cycle_recovery_scale=0.1),
                               ("fed_drawdown_returns", "fed_drawdown_mcap_prev"), (),
                               ("market_vol_cycle_log",), {}),
    "policy_anticipation": (dict(policy_anticipation=2.0),
                            ("policy_anticipation_priced",), (), (), {}),
    "rate_intraday_live": (dict(rate_intraday_live=1.0, rate_close_remark=1.0),
                           (), (), ("rate_live_marks",), {}),
    "pinned_vix": (dict(pinned_vix_variance_share=0.7, pinned_vix_feedback=0.8),
                   (), (), ("pinned_vix_jump", "macro_pins_today"),
                   {"pins": {2: dict(vix=38.0), 3: dict(vix=42.0)}}),
    "corporate_spread_pin": ({}, (), (), ("pinned_corporate_spread", "macro_pins_today"),
                             {"pins": {2: dict(corporate_spread=0.025, cycle="contraction"),
                                       3: dict(corporate_spread=0.03)}}),
    "market_day_tail": (dict(market_day_tail_df=7.0, market_day_tail_state_share=1.0),
                        (), (), ("market_day_scale",), {}),
    "buyback": (dict(buyback_accrual=1.0, buyback_payout_share=0.9),
                ("buyback_log_shares",), (), (), {}),
    "prehistory_valuation": (dict(market_prehistory_valuation=1.0,
                                  market_prehistory_sessions=5.0),
                             ("opening_carry",), (), (), {}),
    # The first ex-date on this universe is day 13's open.
    "dividends": (dict(dividend_payout_share=1.2, dividend_buyback_substitution=1.0),
                  ("dividend",), (), ("pending_dividend",), {"cut_day": 13}),
    "earnings": (EARNINGS, ("earnings_key",), (), (), {}),
    "earnings_withheld": (dict(EARNINGS, earnings_cycle_report_share=0.5,
                               earnings_cycle_depth=0.05),
                          ("earnings_key", "earnings_withheld"), ("earnings_cycle",), (), {}),
    "night_market_factor": (OVERNIGHT,
                            ("night_market_factor",), (), (), {}),
    "idio_vol": (IDIO, ("idio_variance", "idio_jump_pending", "idio_jump_var_pending"),
                 (), (), {}),
    "anticipation_drift": (dict(earnings_anticipation_drift_share=0.9,
                                earnings_anticipation_drift_half_life=252.0),
                           (), ("anticipation_drift", "anticipation_raw"), (), {}),
    "fed_put": (FED_PUT, (), ("intermeeting_return", "fed_put", "fed_put_owed",
                              "fed_put_mcap_prev"), (), {}),
    "spread_equity_gain": (dict(corporate_spread_equity_gain=1.8,
                                corporate_spread_equity_half_life=126.0),
                           (), ("spread_equity_gap",), (), {}),
    "cycle_equity_hazard": (dict(cycle_equity_hazard=5.0, cycle_equity_hazard_knee=0.1,
                                 cycle_equity_hazard_opening=0.007,
                                 corporate_spread_equity_half_life=126.0),
                            (), ("spread_equity_gap",), (), {}),
    "cycle_publication_draw": (dict(cycle_publication_lag_draw=1.0), (),
                               ("cycle_publication",), (), {}),
    "qe_pe_stock_gain": (dict(qe_pe_stock_gain=0.5), (), ("qe_assets_ratio",), (), {}),
    "impact_memory": (MEMORY, (), (), ("book",), {"trade": True}),
    "population": ({}, (), (), (), {"population": "crowded"}),
    "macro_anchors": (MACRO, (), (), (), {}),
    "every_r21_dial": (dict(EVERY_R21_DIAL, **MACRO),
                       ("market_vol_leverage_memory", "vix_stress_memory",
                        "cycle_nowcast_rng", "fed_stress_vix_max", "fed_stress_hold_age",
                        "treasury_policy_path", "fed_drawdown_returns",
                        "fed_drawdown_mcap_prev", "policy_anticipation_priced",
                        "buyback_log_shares", "opening_carry", "dividend",
                        "earnings_key", "earnings_withheld", "idio_variance",
                        "idio_jump_pending", "idio_jump_var_pending"),
                       ("cycle_nowcast", "cycle_publication", "anticipation_drift",
                        "anticipation_raw", "intermeeting_return", "fed_put",
                        "fed_put_owed", "fed_put_mcap_prev", "spread_equity_gap"),
                       ("book", "rate_live_marks", "market_day_scale", "pinned_vix_jump",
                        "macro_pins_today", "pinned_corporate_spread"),
                       {"trade": True, "population": "crowded",
                        "pins": {2: dict(vix=38.0), 3: dict(vix=60.0, corporate_spread=0.03)}}),
}


def _model(name):
    return tf.ModelParams.from_preset("pt-v20", **CASES[name][0])


def _engine(name, seed=SEED):
    options = CASES[name][4]
    population = Population.crowded() if options.get("population") == "crowded" else None
    return tf.Engine(seed=seed, universe=UNIVERSE, model=_model(name),
                     population=population)


def _trade(engine, day, half):
    """Agents' market and resting orders, so the book carries fills, resting
    orders and the meta-order memory across the cut."""
    for k, inst in enumerate(list(UNIVERSE)[:6]):
        sign = 1.0 if (k + day + half) % 2 == 0 else -1.0
        engine.submit("a", inst.ticker, sign * 0.004 * inst.avg_volume)
        book = engine.book(inst.ticker)
        if book.best_bid is not None:
            engine.submit("b", inst.ticker, 100.0, limit_price=round(book.best_bid * 0.999, 2))


def _step(engine, name, position):
    """A third of a day: the open, the first ticks, or the rest and the close."""
    options = CASES[name][4]
    day, part = divmod(position, STEPS)
    if part == 0:
        pins = {**SPIKE, **options.get("pins", {})}.get(day)
        if pins:
            engine.pin_macro(**pins)
        engine.open_market()
    elif part == 1:
        engine.run_session(9, 30, day % 5, FIRST)
        if options.get("trade"):
            _trade(engine, day, 0)
    else:
        if options.get("trade"):
            _trade(engine, day, 1)
        minute = 30 + FIRST
        engine.run_session(9 + minute // 60, minute % 60, day % 5, TICKS - FIRST)
        engine.close_market()


def _mark(engine):
    return [engine.state_hash(), engine.column("price").hex()]


def play(engine, name, start, stop):
    """Run positions `start` to `stop` and mark the engine after each."""
    marks = []
    for position in range(start, stop):
        _step(engine, name, position)
        marks.append(_mark(engine))
    return marks


def _end(name):
    return STEPS * (_cut_day(name) + DAYS - CUT_DAY)


def _cut_day(name):
    return CASES[name][4].get("cut_day", CUT_DAY)


def _cuts(name):
    day = _cut_day(name)
    return [STEPS * day, STEPS * day + 1, STEPS * day + 2]


def resume(name, snapshot, start):
    """In a fresh process: restore onto an engine from another seed, run on."""
    engine = _engine(name, seed=SEED + 6)
    engine.restore_state(snapshot)
    return play(engine, name, start, _end(name))


_RESUME = textwrap.dedent("""
    import json, sys
    sys.path.insert(0, {tests!r})
    import snapshot_codec
    import test_snapshot_gated_keys as T
    doc = json.load(open({path!r}))
    out = [T.resume(doc["name"], snapshot_codec.loads(s), start)
           for start, s in doc["cuts"]]
    print(json.dumps(out))
""")


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_cut_resumed_in_a_fresh_process_matches_one_run(name, tmp_path):
    overrides, top, econ, held, _ = CASES[name]
    control = _engine(name)
    expected = play(control, name, 0, _end(name))
    cuts, snaps, seen = [], [], set()
    for cut in _cuts(name):
        paused = _engine(name)
        play(paused, name, 0, cut)
        snapshot = paused.state_snapshot()
        assert tf.manifest.state_hash(snapshot) == paused.state_hash()
        assert paused.state_hash() == expected[cut - 1][0]
        seen |= set(snapshot) | set(snapshot["economy"])
        for key in top:
            assert key in snapshot, (name, cut, key)
        for key in econ:
            assert key in snapshot["economy"], (name, cut, key)
        cuts.append(cut)
        snaps.append(snapshot)
    for key in held:
        assert key in seen, f"{name}: no cut carries {key}, so the round trip never read it"
    path = tmp_path / "cuts.json"
    path.write_text(json.dumps({"name": name, "cuts": [
        [c, snapshot_codec.dumps(s)] for c, s in zip(cuts, snaps)]}))
    script = _RESUME.format(tests=str(HERE), path=str(path))
    done = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True)
    assert done.returncode == 0, done.stderr[-3000:]
    resumed = json.loads(done.stdout)
    for cut, marks in zip(cuts, resumed):
        assert len(marks) == _end(name) - cut
        for position, (got, want) in enumerate(zip(marks, expected[cut:]), start=cut):
            assert got[1] == want[1], f"{name}: prices diverge at position {position} after a cut at {cut}"
            assert got[0] == want[0], f"{name}: state hash diverges at position {position} after a cut at {cut}"


def _needed(name):
    _, top, econ, _, _ = CASES[name]
    return [(name, "top", k) for k in top] + [(name, "economy", k) for k in econ]


@pytest.mark.parametrize("name, where, key",
                         [row for name in sorted(CASES) for row in _needed(name)])
def test_a_snapshot_lacking_a_key_its_model_needs_is_refused_by_name(name, where, key):
    engine = _engine(name)
    play(engine, name, 0, _cuts(name)[2])
    snapshot = engine.state_snapshot()
    (snapshot if where == "top" else snapshot["economy"]).pop(key)
    with pytest.raises(tf.ValidationError, match=key):
        _engine(name, seed=SEED + 6).restore_state(snapshot)


def _held():
    return sorted({k for case in CASES.values() for k in case[3]}
                  - {"book", "macro_pins_today", "pinned_corporate_spread"})


@pytest.mark.parametrize("key", _held())
def test_a_held_key_is_refused_by_name_on_a_model_without_its_dial(key):
    """Carried from a model that holds it, onto pt-v20's snapshot."""
    source = next(n for n in sorted(CASES) if key in CASES[n][3] and n != "every_r21_dial")
    engine = _engine(source)
    position = 0
    for cut in _cuts(source):
        play(engine, source, position, cut)
        position = cut
        snapshot = engine.state_snapshot()
        if key in snapshot:
            break
    assert key in snapshot
    plain = tf.Engine(seed=SEED, universe=UNIVERSE, model="pt-v20")
    play(plain, "pt-v20", 0, STEPS * CUT_DAY + 2)
    target = plain.state_snapshot()
    target[key] = snapshot[key]
    with pytest.raises(tf.ValidationError, match=key):
        tf.Engine(seed=SEED, universe=UNIVERSE, model="pt-v20").restore_state(target)


def test_a_numpy_uint64_key_above_the_signed_range_restores():
    """A 64-bit key read back through numpy (a ``uint64`` above 2**63 - 1)
    restores as the Python int it stands for."""
    np = pytest.importorskip("numpy")
    for seed in range(3, 40):
        engine = _engine("earnings", seed=seed)
        play(engine, "earnings", 0, STEPS)
        snapshot = engine.state_snapshot()
        if snapshot["earnings_key"] >= 2**63:
            break
    else:
        pytest.fail("no seed in 3..40 draws an earnings key above 2**63 - 1")
    plain = _engine("earnings", seed=seed + 6)
    plain.restore_state(snapshot)
    snapshot["earnings_key"] = np.uint64(snapshot["earnings_key"])
    restored = _engine("earnings", seed=seed + 6)
    restored.restore_state(snapshot)
    assert restored.state_hash() == plain.state_hash() == engine.state_hash()
