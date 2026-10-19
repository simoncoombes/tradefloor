"""The flow a preset was fitted at, and the check on a host's own flow.

A host that hands the engine its own news and macro shocks adds them to the
flow the preset generates for itself, and every realism statement describes
that flow alone. `envelope.CALIBRATED_FLOW` states it as data and
`envelope.external_flow` (with `check(external_flow=...)`) says whether a
host's flow stays inside it. The arithmetic is the Rust crate's
`flow::ExternalFlow::assess`, so these tests also pin the binding.
"""

from __future__ import annotations

import pytest

import tradefloor as tf
from tradefloor import envelope as env


def test_the_calibrated_flow_is_read_from_each_preset() -> None:
    for name in ("pt-v14", "pt-v16", "pt-v18", "pt-v19", "pt-v20"):
        fit = env.CALIBRATED_FLOW[name]
        d = tf.ModelParams.from_preset(name).to_dict()
        assert fit["company_news_rate"] == d["endogenous_news_intensity"]
        assert fit["company_news_sigma"] == d["endogenous_news_sigma"]
        assert fit["idio_jump_rate"] == d["jump_intensity_idio"]
        assert fit["market_jump_rate"] == d["jump_intensity_market"]
        assert fit["market_factor_sigma"] == d["market_factor_sigma"]
        # The engine draws no news of either common scope and steps the
        # economy once a session with no economic shocks.
        assert fit["sector_news_rate"] == 0.0
        assert fit["market_news_rate"] == 0.0
        assert fit["macro_shock_share"] == 0.0
        assert fit["macro_steps_per_session"] == 1.0


def test_every_preset_has_a_row() -> None:
    assert sorted(env.CALIBRATED_FLOW) == sorted(tf.preset_names())


def test_no_external_flow_is_inside() -> None:
    v = env.external_flow(sessions=252, names=40)
    assert v
    assert not v.gaps


def test_company_news_at_the_fitted_size_is_inside_and_past_it_is_not() -> None:
    fit = env.CALIBRATED_FLOW["pt-v20"]
    names, sessions = 100, 500
    events = int(fit["company_news_rate"] * names * sessions)
    sigma = fit["company_news_sigma"]
    # Half up and half down, as the preset's own draws are on average.
    signs = [1.0 if k % 2 == 0 else -1.0 for k in range(events)]
    assert env.external_flow(sessions=sessions, names=names,
                             company_news=[sigma * x for x in signs])
    v = env.external_flow(sessions=sessions, names=names,
                          company_news=[1.5 * sigma * x for x in signs])
    assert not v
    assert [g.id for g in v.gaps] == ["external-flow"]
    assert "company news" in v.reasons[0]


def test_news_that_leans_one_way_is_outside_as_drift() -> None:
    # The fitted size and rate, every event down: inside on variance, but it
    # moves fair value by 0.05 x 0.0175 x 252, about 22% a year.
    fit = env.CALIBRATED_FLOW["pt-v21"]
    assert fit["news_drift"] == 0.0
    names, sessions = 100, 500
    events = int(fit["company_news_rate"] * names * sessions)
    sigma = fit["company_news_sigma"]
    v = env.external_flow(sessions=sessions, names=names, preset="pt-v21",
                          company_news=[-sigma] * events)
    assert not v
    assert len(v.reasons) == 2, v.reasons
    assert "-22.1% a year" in v.reasons[0]


def test_a_split_written_as_an_earnings_cut_is_outside_as_drift() -> None:
    # One 2:1 split a name over two years, the engine's price left whole:
    # fair value falls by ln 2 over the two years.
    import math
    v = env.external_flow(sessions=504, names=108, preset="pt-v21",
                          fundamental_moves=[-math.log(2.0)] * 108)
    assert not v
    text = " ".join(v.reasons)
    assert "-34.7% a year" in text and "-ln(ratio)" in text


def test_ticks_outside_the_session_are_outside() -> None:
    fit = env.CALIBRATED_FLOW["pt-v21"]
    assert fit["off_session_ticks_per_session"] == 0.0
    assert env.external_flow(sessions=100, names=40, preset="pt-v21",
                             off_session_ticks=300)
    # A host that ticks from 07:00 to 20:00: 150 pre-market and 240
    # after-hours ticks a session.
    v = env.external_flow(sessions=100, names=40, preset="pt-v21",
                          off_session_ticks=390 * 100)
    assert not v
    assert "390 ticks a session outside 09:30 to 16:00" in v.reasons[0]
    with pytest.raises(tf.ValidationError):
        env.external_flow(sessions=100, names=40, off_session_ticks=-1)


def test_a_measured_host_flow_is_outside_on_every_channel() -> None:
    # Seed 12345 of the host-driven run behind issues #238 and #239, 504
    # sessions on 108 names: company, sector and market-wide news at the
    # counts and rms sizes measured, earnings revised about four times a
    # name a year at an rms log change of 0.258, and shocks active on 74% of
    # 700 macro steps at a mean load of 2.25, and the VIX written 593 times.
    v = env.external_flow(
        sessions=504, names=108,
        company_news=[0.0315] * 1710,
        sector_news=[0.0210] * 372,
        market_news=[0.0283] * 122,
        fundamental_moves=[0.258] * 760,
        vix_writes=[0.5] * 593,
        macro_steps=700,
        macro_shock_loads=[2.25] * 518,
    )
    assert not v
    text = " ".join(v.reasons)
    for channel in ("company news", "market-wide news", "fundamental",
                    "wrote the VIX", "economic shock", "stepped"):
        assert channel in text, channel


def test_vix_writes_count_in_points_a_session() -> None:
    assert env.external_flow(sessions=100, names=40, vix_writes=[5.0])
    v = env.external_flow(sessions=100, names=40, vix_writes=[5.0, -10.0])
    assert not v
    assert "15.0" not in v.reasons[0] and "0.15" in v.reasons[0]


def test_check_carries_the_flow() -> None:
    inside = env.check(horizon_days=252, external_flow=dict(sessions=252,
                                                            names=40))
    assert inside
    assert any("inside the flow" in w for w in inside.warnings)
    outside = env.check(horizon_days=252, external_flow=dict(
        sessions=252, names=40, macro_shock_loads=[1.0] * 10))
    assert not outside
    assert "external-flow" in [g.id for g in outside.gaps]


def test_bad_input_is_refused() -> None:
    with pytest.raises(tf.ValidationError):
        env.external_flow(sessions=0, names=40)
    with pytest.raises(tf.ValidationError):
        env.external_flow(sessions=10, names=40, company_news=[float("nan")])
    with pytest.raises(tf.ValidationError):
        env.external_flow(sessions=10, names=40, macro_steps=1,
                          macro_shock_loads=[1.0, 1.0])
    with pytest.raises(tf.ValidationError):
        env.external_flow(sessions=10, names=40, preset="pt-v99")


PHASES = ("expansion", "peak", "contraction", "trough", "recovery")


def test_the_calibrated_flow_states_the_presets_own_cycle() -> None:
    fit = env.CALIBRATED_FLOW["pt-v21"]
    assert abs(sum(fit[f"{p}_share"] for p in PHASES) - 1.0) < 1e-12
    # The US table on a 21-session month: a trough of 3.5 months on average,
    # and 99 in 100 over by session 170.
    assert abs(fit["trough_mean_sessions"] - 73.5) < 0.05
    assert fit["trough_run_limit"] == 170
    assert fit["contraction_run_limit"] == 246
    assert fit["downturn_run_limit"] == 338
    assert abs(fit["downturn_share"] - (fit["contraction_share"]
                                        + fit["trough_share"])) < 1e-15


def test_a_phase_held_past_the_presets_own_sojourns_is_outside() -> None:
    own = (["contraction"] * 104 + ["trough"] * 66 + ["recovery"] * 252
           + ["expansion"] * 334)
    v = env.external_flow(sessions=756, names=108, cycle_phases=own)
    assert v, v.reasons
    assert "22% of its 756 sessions with a phase were in a contraction or " \
           "trough, against 8%" in v.warnings[0]

    held = (["expansion"] * 126 + ["contraction"] * 126 + ["trough"] * 454
            + ["recovery"] * 50)
    v = env.external_flow(sessions=756, names=108, cycle_phases=held)
    assert not v
    assert [g.id for g in v.gaps] == ["external-flow"]
    assert v.reasons[0].startswith("a trough ran 454 sessions unbroken")
    assert "together ran 580 sessions" in v.reasons[1]

    a = env.assess_external_flow({
        "names": 108, "sessions": 756, "company_events": 0,
        "company_sum_sq": 0.0, "sector_events": 0, "sector_sum_sq": 0.0,
        "market_events": 0, "market_sum_sq": 0.0, "fundamental_events": 0,
        "fundamental_sum_sq": 0.0, "vix_writes": 0, "vix_write_abs": 0.0,
        "vix_write_max": 0.0, "macro_steps": 756, "macro_shock_steps": 0,
        "macro_shock_load": 0.0, "cycle_phases": held}, "pt-v21")
    assert a["trough_longest"] == 454
    assert a["downturn_longest"] == 580
    assert abs(a["trough_share"] - 454 / 756) < 1e-12


def test_a_tally_without_phases_reads_as_before() -> None:
    v = env.external_flow(sessions=252, names=40)
    assert v
    assert "contraction or trough" not in v.warnings[0]
    with pytest.raises(tf.ValidationError):
        env.external_flow(sessions=10, names=40, cycle_phases=["slump"])
