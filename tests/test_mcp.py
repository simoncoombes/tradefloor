"""The MCP server: what it exposes, what it refuses, and what it admits.

Skipped whole when the optional `mcp` dependency is absent, like the other
opt-in surfaces. The core package depends on nothing and this must not
change that.

The tests that matter here are not the plumbing ones. A tool that returns
the right number under a sentence that inverts it is the failure this
server was built to prevent, so most of what follows asserts on the
CAVEATS and the provenance rather than on the simulation results.
"""

import asyncio
import json

import pytest

pytest.importorskip("mcp", reason="the MCP server is an opt-in extra")

import pretium as pt  # noqa: E402
from pretium import envelope, mcp  # noqa: E402
from pretium.facts import REAL_MARKETS  # noqa: E402

MOMENTUM = {"signal": {"kind": "momentum", "lookback_days": 1.0},
            "portfolio": {"top_k": 5}}


# -- registration ----------------------------------------------------------


def test_every_tool_registers_with_a_description():
    # `asyncio.run` rather than a pytest-asyncio marker: the server is an
    # opt-in extra already, and a second opt-in test plugin to await two
    # calls is not worth the dependency.
    tools = asyncio.run(mcp.server.list_tools())
    assert len(tools) == 8
    for t in tools:
        assert t.description and len(t.description) > 30, t.name
        assert t.input_schema is not None, t.name


def test_the_protocol_layer_returns_structured_content():
    res = asyncio.run(
        mcp.server.call_tool("validate_strategy", {"spec": MOMENTUM}))
    assert res.is_error is False
    assert isinstance(res.structured_content, dict)
    assert res.structured_content["ok"] is True


# -- the honesty machinery -------------------------------------------------


def test_no_measured_number_is_hardcoded_in_the_module():
    """The drift guard, and the reason this server computes its caveats.

    `PRODUCT.md` and `README.md` both still assert a return autocorrelation
    of +0.219 and +0.249 from a superseded preset, where the shipped one
    measures well under half that and is IN band. Those are prose. A caveat
    that hardcoded the same figure would be a lie told by the software, to
    a model, which would then repeat it to a person.

    So: no statistic value may appear as a literal in this module. They are
    read from `envelope.CERTIFIED` at call time or they are not stated.
    """
    source = (pt.__file__.replace("__init__.py", "mcp.py"))
    text = open(source, encoding="utf-8").read()
    # Split off the docstring: it QUOTES the stale figures deliberately, as
    # the worked example for why this rule exists.
    body = text.split('"""', 2)[2]
    for stale in ("0.219", "0.249", "88.7"):
        assert stale not in body, (
            f"{stale} appears as a literal in mcp.py. Measured values must "
            f"come from envelope.CERTIFIED, not from a typed constant."
        )


def test_the_statistic_line_reports_what_the_envelope_measures():
    line = mcp._statistic_line("return_acf1")
    measured = envelope.CERTIFIED["return_acf1"]
    lo, hi = REAL_MARKETS["return_acf1"]
    assert f"{measured:.4g}" in line
    assert f"{lo:g}" in line and f"{hi:g}" in line
    assert "in band" in line or "OUT OF BAND" in line


def test_a_single_seed_result_says_so_in_capitals():
    r = mcp.evaluate_strategies({"mine": MOMENTUM}, days=1)
    assert r["ok"]
    assert any("ONE SEED" in c for c in r["caveats"]), (
        "a single-seed ordering is the most misreportable thing this server "
        "produces and must announce itself"
    )


def test_a_momentum_strategy_is_told_what_its_edge_rests_on():
    r = mcp.evaluate_strategies({"mine": MOMENTUM}, days=1)
    assert any("return_acf1" in c for c in r["caveats"])


def test_an_oracle_hidden_inside_a_blend_still_triggers_the_warning():
    """The nested case. A blend containing `oracle` is exactly as privileged
    as a bare oracle, and a caveat engine that only looked at the top-level
    `kind` would miss it -- producing a result that looks like a strategy
    and is actually perfect foresight."""
    blend = {
        "signal": {"kind": "blend", "components": [
            {"kind": "momentum", "lookback_days": 1.0, "weight": 0.5},
            {"kind": "oracle", "weight": 0.5},
        ]},
        "portfolio": {"top_k": 3},
    }
    specs, _ = mcp._specs_from({"sneaky": blend})
    assert "oracle" in mcp._signals_in(specs)

    r = mcp.evaluate_strategies({"sneaky": blend}, days=1)
    assert r["ok"], r.get("error")
    assert any("PRIVILEGED" in c for c in r["caveats"])


def test_unbounded_leverage_is_called_out():
    r = mcp.evaluate_strategies({"mine": MOMENTUM}, days=1, max_leverage=None)
    assert any("Leverage is unbounded" in c for c in r["caveats"])


def test_a_short_run_says_it_is_a_slice_of_an_annual_measurement():
    """MAX_DAYS sits well below the certified horizon because a 252-day
    evaluation takes ~95 seconds. So every result this server produces is a
    short window on a market certified annually, and the envelope will not
    say so -- its gaps are about running LONGER. This caveat covers the
    direction the envelope does not."""
    assert mcp.MAX_DAYS < envelope.CERTIFIED_HORIZON_DAYS, (
        "if the cap ever reaches the certified horizon, revisit this caveat"
    )
    # Every reachable horizon is short: the cap is below the threshold, so
    # this caveat fires on EVERY scored result. That is not boilerplate --
    # it is a permanent property of a server that cannot afford to run a
    # year, and a result that omitted it would be quietly overclaiming.
    for days in (1, 5, mcp.MAX_DAYS):
        r = mcp.evaluate_strategies({"mine": MOMENTUM}, days=days)
        assert any("SHORT WINDOW" in c for c in r["caveats"]), days


def test_the_envelope_tool_still_reaches_the_horizon_gap():
    # `evaluate_strategies` cannot run past the certified horizon, but
    # `check_envelope` is the tool for asking about one, and it must.
    r = mcp.check_envelope(
        horizon_days=envelope.CERTIFIED_HORIZON_DAYS + 1)
    assert r["ok"] and r["inside"] is False
    assert any(g["id"] == "horizon" for g in r["gaps"])


def test_a_caveat_that_does_not_apply_is_not_emitted():
    # Caveats are only worth reading if they are earned. A multi-seed run
    # must not carry the single-seed warning.
    r = mcp.rank_strategies({"mine": MOMENTUM}, seeds=[1, 2, 3], days=1)
    assert not any("ONE SEED" in c for c in r["caveats"])


# -- provenance ------------------------------------------------------------


@pytest.mark.parametrize("call", [
    lambda: mcp.describe_simulator(),
    lambda: mcp.check_envelope(horizon_days=252),
    lambda: mcp.validate_strategy(MOMENTUM),
    lambda: mcp.evaluate_strategies({"m": MOMENTUM}, days=1),
    lambda: mcp.describe_universe(size=8),
    lambda: mcp.explain_price_move(universe_size=8, day=1, top_n=2),
])
def test_every_successful_result_carries_its_provenance(call):
    r = call()
    assert r["ok"], r.get("error")
    prov = r["provenance"]
    assert prov["model_preset"] == pt.model_preset()["name"]
    assert prov["pretium_version"] == pt.__version__


def test_a_scored_result_names_the_seed_and_the_universe():
    r = mcp.evaluate_strategies({"m": MOMENTUM}, seed=11, days=1)
    prov = r["provenance"]
    assert prov["seed"] == 11
    assert prov["universe"] == {"size": 40, "seed": 111}
    assert len(prov["universe_fingerprint"]) > 8


# -- the strategy surface --------------------------------------------------


def test_a_missing_spec_version_is_supplied_and_reported():
    r = mcp.validate_strategy(MOMENTUM)
    assert r["ok"]
    assert r[mcp._ASSUMED] is True
    assert r["canonical"]["spec_version"] == pt.SPEC_VERSION


def test_a_version_the_caller_gave_is_not_overwritten():
    # The newer-than-understood refusal has to keep working, or the
    # convenience above would have disabled a real safety property.
    doc = {"spec_version": pt.SPEC_VERSION + 1, **MOMENTUM}
    r = mcp.validate_strategy(doc)
    assert r["ok"] is False
    assert "newer" in r["error"]


def test_python_source_is_not_a_strategy():
    """There is no code path from a tool argument to execution, and this is
    the test that says so out loud."""
    for hostile in ("lambda obs: {}", "__import__('os').system('id')",
                    {"signal": {"kind": "eval", "code": "1+1"}}):
        r = mcp.validate_strategy(hostile)
        assert r["ok"] is False, hostile


def test_a_grammar_error_comes_back_with_the_grammar():
    r = mcp.validate_strategy({"signal": {"kind": "telepathy"}})
    assert r["ok"] is False
    assert "telepathy" in r["error"]
    # A model that gets an error listing the valid kinds can fix itself in
    # one more call. One that gets "invalid spec" cannot.
    assert "momentum" in r["error"]


# -- limits ----------------------------------------------------------------


@pytest.mark.parametrize("call,expect", [
    (lambda: mcp.evaluate_strategies({"m": MOMENTUM}, days=mcp.MAX_DAYS + 1),
     "days"),
    (lambda: mcp.evaluate_strategies({"m": MOMENTUM},
                                     universe_size=mcp.MAX_UNIVERSE + 1),
     "universe_size"),
    (lambda: mcp.rank_strategies({"m": MOMENTUM},
                                 seeds=list(range(mcp.MAX_SEEDS + 2))),
     "seeds"),
    (lambda: mcp.evaluate_strategies(
        {f"s{i}": MOMENTUM for i in range(mcp.MAX_STRATEGIES + 1)}), "8"),
    (lambda: mcp.run_stress_scenario("apocalypse"), "unknown scenario"),
])
def test_a_request_beyond_the_limits_is_refused_as_a_result(call, expect):
    r = call()
    assert r["ok"] is False
    assert expect in r["error"]


# -- results the tools promise ---------------------------------------------


def test_the_factors_sum_to_the_move_as_returned():
    """The tool claims the seven factors sum to the move. It reports a
    per-row `residual` so the claim is checkable on the figures actually
    returned, rather than on unrounded ones the caller never sees."""
    r = mcp.explain_price_move(universe_size=12, day=1, top_n=5)
    assert r["ok"]
    for row in r["rows"]:
        assert set(row["factors"]) == set(pt.Engine.FACTORS)
        assert row["residual"] < 1e-9
        assert abs(sum(row["factors"].values())
                   - row["total_log_move"]) == pytest.approx(row["residual"])


def test_a_stress_test_always_carries_its_control():
    r = mcp.run_stress_scenario("vix_shock", {"mine": MOMENTUM}, days=5)
    assert r["ok"], r.get("error")
    for row in r["comparison"]:
        assert row["return_pct_control"] is not None
        assert row["difference"] == pytest.approx(
            row["return_pct_shocked"] - row["return_pct_control"], abs=1e-6)
    assert any("MAGNITUDE" in c for c in r["caveats"])


def test_the_ranking_is_ordered_and_points_at_the_number_to_quote():
    r = mcp.rank_strategies({"mine": MOMENTUM}, seeds=[1, 2, 3], days=1)
    assert r["ok"]
    caps = [x["pooled_capture"] for x in r["records"]
            if x["pooled_capture"] is not None]
    assert caps == sorted(caps, reverse=True), "table() order must survive"
    assert "pooled_capture" in r["reading_note"]
    # `seeds_first` is a league position, not a head-to-head record, and
    # saying so is the difference between a number and a misreading.
    assert "not a head-to-head" in r["reading_note"]


def test_the_paired_sign_test_reports_an_identical_strategy_as_all_ties():
    """A submitted momentum spec and the momentum baseline are the same
    strategy, and the honest answer is 'indistinguishable' rather than a
    coin-flip winner."""
    r = mcp.rank_strategies({"mine": MOMENTUM}, seeds=[1, 2, 3], days=1)
    same = [t for t in r["paired_sign_tests"] if t["b"] == "momentum"]
    assert same, "the momentum baseline should have been compared"
    assert same[0]["ties"] == 3 and same[0]["paired_seeds"] == 0


def test_describe_simulator_reports_the_gap_that_is_out_of_band():
    d = mcp.describe_simulator()
    assert d["certified"]["statistics_out_of_band"] == ["volume_change_acf1"]
    assert len(d["certified"]["statistics_in_band"]) == 9
    assert d["structural_limitations"]
    assert "atlas" in d["not_exposed_here"]


def test_an_unknown_statistic_is_refused_with_the_known_ones():
    r = mcp.check_envelope(horizon_days=252, statistics=["sharpe_ratio"])
    assert r["ok"] is False
    assert "return_acf1" in r["error"]


# -- the properties the product claims -------------------------------------


@pytest.mark.parametrize("call", [
    lambda: mcp.evaluate_strategies({"m": MOMENTUM}, days=2),
    lambda: mcp.explain_price_move(universe_size=10, day=1),
    lambda: mcp.describe_universe(size=10),
])
def test_a_tool_called_twice_returns_the_same_bytes(call):
    """Determinism is the product's headline claim. A tool that drifted
    between identical calls would break it where it is most visible."""
    assert json.dumps(call(), sort_keys=True) == \
        json.dumps(call(), sort_keys=True)


@pytest.mark.parametrize("call", [
    lambda: mcp.describe_simulator(),
    lambda: mcp.evaluate_strategies({"m": MOMENTUM}, days=1),
    lambda: mcp.rank_strategies({"m": MOMENTUM}, seeds=[1, 2], days=1),
    lambda: mcp.run_stress_scenario("rate_shock", days=3),
    lambda: mcp.explain_price_move(universe_size=8, day=1, top_n=2),
    lambda: mcp.describe_universe(size=8),
    lambda: mcp.check_envelope(horizon_days=100),
    lambda: mcp.validate_strategy(MOMENTUM),
])
def test_every_result_serialises(call):
    # A tool result that cannot be JSON-encoded reaches the model as a
    # transport error. `Ranking.separation` was a bound method on the first
    # draft of this server and would have done exactly that.
    json.dumps(call())
