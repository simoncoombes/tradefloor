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
import os
import time

import pytest

pytest.importorskip("mcp", reason="the MCP server is an opt-in extra")

import tradefloor as pt  # noqa: E402
from tradefloor import envelope, mcp  # noqa: E402

MOMENTUM = {"signal": {"kind": "momentum", "lookback_days": 1.0},
            "portfolio": {"top_k": 5}}


# -- registration ----------------------------------------------------------


def test_every_tool_registers_with_a_description():
    # `asyncio.run` rather than a pytest-asyncio marker: the server is an
    # opt-in extra already, and a second opt-in test plugin to await two
    # calls is not worth the dependency.
    tools = asyncio.run(mcp.server.list_tools())
    # Against the module rather than a literal. A hard-coded count here is a
    # second place to remember the tool list, and it fails as a stale number
    # rather than as the thing it is checking -- which is that every
    # registered tool carries a usable description.
    registered = {name for name, obj in vars(mcp).items()
                  if getattr(obj, "__wrapped__", None) is not None
                  or name in {t.name for t in tools}}
    assert {t.name for t in tools} <= registered
    assert len(tools) >= 11, [t.name for t in tools]
    for t in tools:
        assert t.description and len(t.description) > 30, t.name
        assert t.input_schema is not None, t.name


def test_the_readme_counts_the_tools_the_server_registers():
    """The README's Contents table said twelve at 0.8.5, when the server
    registered thirteen. Both counts, every tool and the read-only ones,
    are read off that row and compared with the server's annotations, not
    with a literal here. The row called all thirteen read-only until the
    sessions arrived, and start_job was not."""
    import pathlib
    import re
    readme = (pathlib.Path(__file__).resolve().parent.parent
              / "README.md").read_text(encoding="utf-8")
    row = re.search(r"^\| MCP server \| (\w+) tools for a coding agent, "
                    r"(\w+) of them read-only", readme, re.M)
    assert row, "README.md's Contents table has no MCP server row"
    words = ["zero", "one", "two", "three", "four", "five", "six", "seven",
             "eight", "nine", "ten", "eleven", "twelve", "thirteen",
             "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
             "nineteen", "twenty", "twenty-one", "twenty-two"]

    def count(word):
        word = word.lower()
        return int(word) if word.isdigit() else words.index(word)

    tools = asyncio.run(mcp.server.list_tools())
    read_only = [t.name for t in tools if t.annotations.read_only_hint]
    assert count(row.group(1)) == len(tools), (
        f"README.md says {row.group(1)} tools and the server registers "
        f"{len(tools)}: {sorted(t.name for t in tools)}")
    assert count(row.group(2)) == len(read_only), (
        f"README.md says {row.group(2)} are read-only and the server marks "
        f"{len(read_only)}: {sorted(read_only)}")


def test_the_catalogue_lists_the_pack_the_constructors_and_the_registry():
    out = mcp.list_scenarios()
    assert out["ok"] is True
    assert {e["name"] for e in out["shipped"]} == set(pt.Scenario.available())
    for entry in out["shipped"]:
        assert entry["fingerprint"].startswith("sha256:")
        assert entry["shocks"], entry["name"]
    assert set(out["targets"]) == set(pt.TARGETS)
    assert set(out["not_supported"]) == set(pt.UNSUPPORTED_TARGETS)
    assert out["operations"] == list(pt.interventions.OPERATIONS)
    assert out["shapes"] == list(pt.interventions.SHAPES)


def test_build_scenario_takes_either_grammar_but_not_both():
    path = mcp.build_scenario(steps=[{"kind": "hold", "fields": {"vix": 30.0}}],
                              days=5)
    assert path["ok"] is True and path["fields_pinned"] == ["vix"]

    shocks = mcp.build_scenario(
        shocks=[{"target": "macro.vix", "operation": "multiply", "value": 2.0,
                 "at": 2, "duration": 4}],
        transmission=[{"target": "macro.corporate_yield", "operation": "add",
                       "value": 0.005, "at": 2}])
    assert shocks["ok"] is True
    assert shocks["shocks"][0]["shape"] == "hold"
    assert shocks["transmission"][0]["target"] == "macro.corporate_yield"

    both = mcp.build_scenario(
        steps=[{"kind": "hold", "fields": {"vix": 30.0}}],
        shocks=[{"target": "macro.vix", "operation": "multiply", "value": 2.0,
                 "at": 1}])
    assert both.get("ok") is not True
    assert "one at a time" in both["error"]


def test_an_authored_document_is_runnable_as_it_stands():
    """The split between authoring and running is only a saving if the thing
    one returns is the thing the other takes."""
    built = mcp.build_scenario(
        shocks=[{"target": "market.liquidity", "operation": "multiply",
                 "value": 0.4, "at": 1, "duration": 3}])
    ran = mcp.run_stress_scenario(built["scenario"], seed=7, universe_size=10,
                                  days=6)
    assert ran["ok"] is True, ran


def test_a_shipped_scenario_runs_by_name():
    """By name, and long enough to reach the document's first event.

    `policy_regime_shift` because its first event is the earliest in the
    pack (day 30), so this is the cheapest run in which a shipped document
    does something. Until 0.8.5 this test ran `liquidity_crisis` for six
    days, which is 44 days before its first shock, and passed on a result
    that was 0.0 for every entrant.
    """
    first = min(item.at for item in
                pt.Scenario.load("policy_regime_shift").interventions)
    out = mcp.run_stress_scenario("policy_regime_shift", seed=7,
                                  universe_size=4, days=first + 1)
    assert out["ok"] is True, out
    assert any(row["difference"] != 0.0 for row in out["comparison"]), (
        "the scenario reached the market, so some entrant must move")
    unknown = mcp.run_stress_scenario("no_such_scenario", universe_size=8,
                                      days=2)
    assert unknown.get("ok") is not True
    assert "liquidity_crisis" in unknown["error"]


def test_an_unsupported_target_is_refused_with_the_real_lever():
    out = mcp.build_scenario(
        shocks=[{"target": "market.volatility", "operation": "multiply",
                 "value": 2.0, "at": 1}])
    assert out.get("ok") is not True
    assert "macro.vix" in out["error"]


def test_the_protocol_layer_returns_structured_content():
    res = asyncio.run(
        mcp.server.call_tool("validate_strategy", {"spec": MOMENTUM}))
    assert res.is_error is False
    assert isinstance(res.structured_content, dict)
    assert res.structured_content["ok"] is True


# -- the honesty machinery -------------------------------------------------


def test_no_measured_number_is_hardcoded_in_the_module():
    """The drift guard, and the reason this server computes its caveats.

    The product brief and `README.md` both still asserted a return autocorrelation
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
    """The band quoted is the one the envelope grades on, not the decade
    table beside it. The two differ on most rows since the default basis
    moved to `ruled`, and until 0.8.5 the line quoted the decade band next
    to the ruled verdict."""
    row = envelope.certified()["statistics"]["return_acf1"]
    line = mcp._statistic_line("return_acf1")
    lo, hi = row["band"]
    assert f"{row['measured']:.4g}" in line
    assert f"{lo:g}" in line and f"{hi:g}" in line
    assert ("in band" if row["in_band"] else "OUT OF BAND") in line
    assert envelope.certified()["band_basis"] in line


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
    evaluation takes ~95 seconds, so every DIRECT call is a short window on
    a market certified annually. The envelope will not say so -- its gaps
    are about running LONGER -- and this caveat covers the direction the
    envelope does not.

    A background job reaches the certified horizon and correctly does NOT
    carry this caveat; that is what `start_job` is for.
    """
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
    lambda: mcp.build_universe(size=8),
    lambda: mcp.explain_price_move(universe_size=8, day=1, top_n=2),
])
def test_every_successful_result_carries_its_provenance(call):
    r = call()
    assert r["ok"], r.get("error")
    prov = r["provenance"]
    assert prov["model_preset"] == pt.model_preset()["name"]
    assert prov["tradefloor_version"] == pt.__version__
    # The package's name before 0.5.0, carried beside it until 0.9.1 (#237).
    assert "pretium_version" not in prov
    assert prov["model_fingerprint"], "an empty fingerprint cites nothing"


def test_a_scored_result_names_the_seed_and_the_universe():
    r = mcp.evaluate_strategies({"m": MOMENTUM}, seed=11, days=1)
    prov = r["provenance"]
    assert prov["seed"] == 11
    assert prov["universe"] == {"size": 40, "seed": 111,
                                "sectors": None}
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
    """No code path runs from a tool argument to execution. This is
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
    """The tool claims the factors sum to `total_log_move`. It reports a
    per-row `residual` so the claim is checkable on the figures actually
    returned, rather than on unrounded ones the caller never sees."""
    r = mcp.explain_price_move(universe_size=12, day=1, top_n=5)
    assert r["ok"]
    for row in r["rows"]:
        assert set(row["factors"]) == set(pt.Engine.FACTORS)
        assert row["residual"] < 1e-9
        assert abs(sum(row["factors"].values())
                   - row["total_log_move"]) == pytest.approx(row["residual"])


def test_explain_price_move_says_what_its_factors_sum_to():
    """The description said "the seven factor contributions that SUM to the
    move". There are eleven, and they sum to the day's change in the
    mispricing. On pt-v20 that is a small part of the price move, because
    most of the day's news and noise moves fair value and
    `fair_value_shift` takes it back out."""
    tools = {t.name: t for t in asyncio.run(mcp.server.list_tools())}
    text = tools["explain_price_move"].description
    assert "seven" not in text and "SUM to the move" not in text
    assert f"{len(pt.Engine.FACTORS)} factor contributions" in text
    assert "mispricing" in text and "fair_value_shift" in text

    r = mcp.explain_price_move(universe_size=8, day=1, top_n=2)
    assert r["ok"]
    note = r["reading_note"]
    assert f"The {len(pt.Engine.FACTORS)} factors" in note
    assert "not the log change in the price" in note


def test_a_stress_test_always_carries_its_control():
    r = mcp.run_stress_scenario("vix_shock", {"mine": MOMENTUM}, days=5,
                                peak_day=2, universe_size=12)
    assert r["ok"], r.get("error")
    for row in r["comparison"]:
        assert row["return_pct_control"] is not None
        assert row["difference"] == pytest.approx(
            row["return_pct_shocked"] - row["return_pct_control"], abs=1e-6)
    assert any("MAGNITUDE" in c for c in r["caveats"])


#: A preset on which the Oracle is a ceiling. The tools pinned the library
#: calls to it until they took a preset of their own; they run it by name
#: now, which checks the capture path through the argument a client uses.
CEILING_PRESET = "pt-v19"


def test_the_ranking_is_ordered_and_points_at_the_number_to_quote():
    assert pt.baselines.oracle_is_ceiling(CEILING_PRESET)
    r = mcp.rank_strategies({"mine": MOMENTUM}, seeds=[1, 2, 3], days=1,
                            preset=CEILING_PRESET)
    assert r["ok"]
    assert "capture_withheld" not in r
    caps = [x["pooled_capture"] for x in r["records"]
            if x["pooled_capture"] is not None]
    assert caps == sorted(caps, reverse=True), "table() order must survive"
    assert "pooled_capture" in r["reading_note"]
    # `seeds_first` is a league position, not a head-to-head record, and
    # saying so is the difference between a number and a misreading.
    assert "not a head-to-head" in r["reading_note"]


def test_on_pt_v20_the_ranking_quotes_buy_and_hold_and_no_capture():
    """The default's Oracle is not a ceiling. The records carry no capture
    field at all, the headline is the mean P&L over buy-and-hold's, the
    table is ordered on it, and the reason is sent."""
    r = mcp.rank_strategies({"mine": MOMENTUM}, seeds=[1, 2, 3], days=1)
    assert r["ok"]
    # The default, pt-v21 from 0.10.0 (these read "pt-v20" until then).
    assert r["provenance"]["model_preset"] == "pt-v21"
    assert r["capture_withheld"] == pt.baselines.ORACLE_NOT_A_CEILING["pt-v21"]
    assert "unmeasurable" not in r
    for record in r["records"]:
        assert not {"pooled_capture", "median_capture",
                    "seeds_measured"} & set(record)
    excess = [x["mean_excess_over_buy_and_hold"] for x in r["records"]]
    assert excess == sorted(excess, reverse=True)
    assert "mean_excess_over_buy_and_hold" in r["reading_note"]
    assert "not a head-to-head" in r["reading_note"]


def test_on_pt_v20_an_evaluation_quotes_buy_and_hold_and_no_capture():
    r = mcp.evaluate_strategies({"mine": MOMENTUM}, days=1)
    assert r["ok"]
    assert "capture_ratio" not in r and "capture_note" not in r
    assert r["capture_ratio_withheld"] == (
        pt.baselines.ORACLE_NOT_A_CEILING["pt-v21"])  # the default; was pt-v20
    pnl = {row["name"]: row["pnl"] for row in r["scores"]}
    assert set(r["versus_buy_and_hold"]) == set(pnl) - {"buy_and_hold"}
    for name, value in r["versus_buy_and_hold"].items():
        assert value == pytest.approx(pnl[name] - pnl["buy_and_hold"],
                                      abs=0.02)


def test_where_the_oracle_is_a_ceiling_an_evaluation_quotes_capture():
    assert pt.baselines.oracle_is_ceiling(CEILING_PRESET)
    r = mcp.evaluate_strategies({"mine": MOMENTUM}, days=1,
                                preset=CEILING_PRESET)
    assert r["ok"]
    assert "capture_ratio_withheld" not in r
    assert "versus_buy_and_hold" not in r
    assert "capture_note" in r and isinstance(r["capture_ratio"], dict)


def test_the_oracle_caveat_says_whether_it_is_a_ceiling(monkeypatch):
    blend = {"signal": {"kind": "oracle"}, "portfolio": {"top_k": 3}}
    specs, _ = mcp._specs_from({"o": blend})
    kwargs = dict(days=5, n_seeds=1, signals=mcp._signals_in(specs),
                  max_leverage=2.0, universe_size=40,
                  sector_concentrated=False)
    line = next(c for c in mcp._caveats(**kwargs) if "PRIVILEGED" in c)
    assert "not a ceiling" in line
    monkeypatch.setattr(pt.baselines, "oracle_is_ceiling",
                        lambda model=None: True)
    line = next(c for c in mcp._caveats(**kwargs) if "PRIVILEGED" in c)
    assert "ceiling for measuring capture" in line


def test_the_paired_sign_test_reports_an_identical_strategy_as_all_ties():
    """A submitted momentum spec and the momentum baseline are the same
    strategy, and the honest answer is 'indistinguishable' rather than a
    coin-flip winner."""
    r = mcp.rank_strategies({"mine": MOMENTUM}, seeds=[1, 2, 3], days=1)
    same = [t for t in r["paired_sign_tests"] if t["b"] == "momentum"]
    assert same, "the momentum baseline should have been compared"
    assert same[0]["ties"] == 3 and same[0]["paired_seeds"] == 0


# -- universes and scenarios as data ---------------------------------------


def test_a_concentrated_roster_is_buildable_and_declares_the_gap():
    """Sector concentration is one of the SIX NAMED envelope gaps, and
    `envelope.check` already takes it as an argument. A server that could
    not build a concentrated roster could not ask about a gap its own
    product documents."""
    u = mcp.build_universe(size=20, seed=111,
                           sectors=["technology", "financial_services"])
    assert u["ok"]
    assert set(u["sector_counts"]) == {"technology", "financial_services"}
    assert any("CONCENTRATED" in c for c in u["caveats"])

    r = mcp.evaluate_strategies({"m": MOMENTUM}, universe=u["universe"],
                                days=1)
    assert any("CONCENTRATED" in c for c in r["caveats"]), (
        "the gap must follow the roster into the result that used it"
    )


def test_a_balanced_roster_says_how_to_ask_the_other_question():
    u = mcp.build_universe(size=12, seed=111)
    assert any("BALANCED" in c and "sectors" in c for c in u["caveats"])


def test_an_unknown_sector_is_refused_with_the_known_ones():
    u = mcp.build_universe(size=10, sectors=["crypto"])
    assert u["ok"] is False
    assert "technology" in u["error"]


def test_a_hand_authored_roster_runs():
    rows = [{"ticker": "ZZA", "sector": "technology", "initial_price": 100.0,
             "shares_outstanding": 1e8, "eps": 4.0},
            {"ticker": "ZZB", "sector": "energy", "initial_price": 50.0,
             "shares_outstanding": 2e8, "beta": 1.4,
             "book_value_per_share": 30.0}]
    u = mcp.build_universe(instruments=rows)
    assert u["ok"], u.get("error")
    assert [i["ticker"] for i in u["instruments"]] == ["ZZA", "ZZB"]
    x = mcp.explain_price_move(universe=u["universe"], day=1, top_n=2)
    assert x["ok"]
    assert {r["ticker"] for r in x["rows"]} == {"ZZA", "ZZB"}


def test_an_instrument_field_that_does_not_exist_is_refused_by_name():
    u = mcp.build_universe(instruments=[
        {"ticker": "A", "sector": "technology", "initial_price": 1.0,
         "shares_outstanding": 1e8, "dividend_yield": 0.02},
        {"ticker": "B", "sector": "energy", "initial_price": 1.0,
         "shares_outstanding": 1e8}])
    assert u["ok"] is False
    assert "dividend_yield" in u["error"]


def test_a_scenario_can_be_authored_and_handed_straight_back():
    """The first thing anyone does is pass a tool its own output."""
    b = mcp.build_scenario(steps=[
        {"kind": "ramp", "field": "vix", "start": 15.0, "end": 55.0,
         "over": 8},
        {"kind": "step", "field": "federal_funds_rate", "before": 0.025,
         "after": 0.06, "at": 4}], label="fear and rates", days=10)
    assert b["ok"], b.get("error")
    assert set(b["fields_pinned"]) == {"vix", "federal_funds_rate"}
    assert b["table"][0]["vix"] == 15.0

    r = mcp.run_stress_scenario(b["scenario"], days=6)
    assert r["ok"], r.get("error")
    assert r["scenario_authored"] is True
    assert r["scenario"] == "fear and rates"


def test_a_preset_scenario_still_works_and_is_marked_as_one():
    r = mcp.run_stress_scenario("vix_shock", days=3, peak_day=1,
                                universe_size=8)
    assert r["ok"] and r["scenario_authored"] is False


def test_an_unknown_macro_field_is_refused_with_the_valid_list():
    b = mcp.build_scenario(steps=[{"kind": "ramp", "field": "unemployment",
                                   "start": 1, "end": 2, "over": 3}])
    assert b["ok"] is False
    assert "vix" in b["error"] and "unemployment" in b["error"]


def test_the_macro_field_list_is_read_from_the_engine():
    # Typed here, it would drift from what `Scenario` accepts -- which is
    # exactly how this module came to claim the surface was "40-odd fields"
    # when it was seven. Asserted against `FIELDS` rather than against a
    # count, because a count is a second place to remember: the list grew to
    # eleven when the intervention framework exposed four macro fields the
    # economy already carried, and a number here would have been a test
    # failure standing in for a documentation update.
    from tradefloor.scenario import FIELDS

    fields = mcp._macro_fields()
    assert "vix" in fields and "cycle" in fields
    assert tuple(fields) == FIELDS, fields


def test_an_unknown_step_kind_names_the_three_that_exist():
    b = mcp.build_scenario(steps=[{"kind": "teleport", "field": "vix"}])
    assert b["ok"] is False
    assert "hold" in b["error"] and "ramp" in b["error"]


# -- background jobs -------------------------------------------------------


def test_a_direct_call_past_the_cap_is_refused_and_points_at_start_job():
    """Named for what it checks.

    An earlier version of this test was called "a job reaches the certified
    horizon that a direct call cannot" and ran no job at all -- it asserted
    the REFUSAL and never the reach. The assertions were correct; the name
    claimed a property nothing here established, which is the exact failure
    this project keeps hitting in its prose. The reach is tested below.
    """
    assert mcp.MAX_DAYS < mcp.MAX_DAYS_ASYNC == envelope.CERTIFIED_HORIZON_DAYS
    r = mcp.evaluate_strategies({"m": MOMENTUM},
                                days=envelope.CERTIFIED_HORIZON_DAYS)
    assert r["ok"] is False
    assert "start_job" in r["error"], "the refusal must say where to go"


def test_the_day_cap_is_lifted_only_on_a_job_worker_thread():
    """The mechanism the horizon claim actually rests on.

    `_day_cap` reads a thread-local that `_run_job` sets, because
    `ThreadPoolExecutor` does not propagate a context and a cap that
    silently failed to apply would be worse than no cap. Asserted directly
    so the property is covered without a 95-second simulation.
    """
    import threading

    assert mcp._day_cap() == mcp.MAX_DAYS, "the calling thread stays capped"

    seen = {}

    def worker():
        mcp._local.async_job = True
        seen["inside"] = mcp._day_cap()

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert seen["inside"] == mcp.MAX_DAYS_ASYNC
    assert mcp._day_cap() == mcp.MAX_DAYS, (
        "the worker's flag must not leak back to the calling thread"
    )


@pytest.mark.skipif(not (os.environ.get("TRADEFLOOR_SLOW_TESTS")
                         or os.environ.get("PRETIUM_SLOW_TESTS")),
                    reason="a 252-day evaluation is ~95s; "
                           "set TRADEFLOOR_SLOW_TESTS=1 to run it")
def test_a_job_really_does_reach_the_certified_horizon():
    """The claim, measured rather than asserted.

    Opt-in because it costs what it costs: this is the run the SHORT WINDOW
    caveat exists to describe, and the only way to prove the caveat is
    absent at the certified horizon is to reach it.
    """
    j = mcp.start_job("evaluate_strategies",
                      {"strategies": {"m": MOMENTUM},
                       "days": envelope.CERTIFIED_HORIZON_DAYS,
                       "universe_size": 20})
    assert j["ok"], j.get("error")
    for _ in range(3600):
        c = mcp.check_job(j["job_id"])
        if c["status"] != "running":
            break
        time.sleep(1.0)
    assert c["status"] == "done", c
    result = c["result"]
    assert result["ok"]
    assert not any("SHORT WINDOW" in x for x in result["caveats"]), (
        "a result AT the certified horizon is not a slice of one"
    )


def test_a_job_runs_and_its_result_is_collectable():
    j = mcp.start_job("evaluate_strategies",
                      {"strategies": {"m": MOMENTUM}, "days": 1,
                       "universe_size": 8})
    assert j["ok"], j.get("error")
    for _ in range(600):
        c = mcp.check_job(j["job_id"])
        if c["status"] != "running":
            break
        time.sleep(0.1)
    assert c["status"] == "done", c
    assert c["result"]["ok"]
    assert c["result"]["caveats"]


def test_a_cheap_tool_is_not_jobbable():
    r = mcp.start_job("describe_simulator", {})
    assert r["ok"] is False
    assert "evaluate_strategies" in r["error"]


def test_an_unknown_job_says_the_three_reasons_it_might_be_missing():
    r = mcp.check_job("job-does-not-exist")
    assert r["ok"] is False
    assert "restart" in r["error"]


def test_listing_jobs_needs_no_id():
    r = mcp.check_job()
    assert r["ok"] and isinstance(r["jobs"], list)


def test_describe_simulator_serves_the_envelopes_verdicts_by_group():
    """Two statistics were out until the 2026-08-26 era boundary made pt-v10
    the default, and every shape row has been in band since. The level and
    crisis rows joined on 2026-09-03 and the default preset holds two of
    them red, so an agent reading this surface is told which rows are out
    and which group each belongs to, rather than a stale pair or a bare
    total.

    The served verdicts are derived from the envelope here rather than
    typed: the rows expected red at the default are pinned once, in
    `tests/test_envelope.py`, and this surface has to mirror whatever that
    pin holds. A version of this test that listed every level and crisis
    row as out of band contradicted the registered prediction that the -1
    percent fear row passes, and failed the day it was measured.
    """
    d = mcp.describe_simulator()
    from tradefloor import envelope
    from tradefloor.facts import SHAPE, LEVEL, CRISIS, DISPERSION
    cert = envelope.certified()
    served_out = set(d["certified"]["statistics_out_of_band"])
    served_in = set(d["certified"]["statistics_in_band"])
    # `is False` / `is True`, NOT truthiness. Since the default basis moved
    # to `ruled` a row can be UNREADABLE -- `in_band` None, because the
    # basis adopts no band for it -- and `not None` is True, so the old
    # truthiness form counted an ungraded row as out of band and asserted
    # the surface should serve it that way. Three states, tested as three.
    served_unreadable = set(d["certified"]["statistics_unreadable"])
    assert served_out == {k for k, v in cert["statistics"].items()
                          if v["in_band"] is False}
    assert served_in == {k for k, v in cert["statistics"].items()
                         if v["in_band"] is True}
    assert served_unreadable == {k for k, v in cert["statistics"].items()
                                 if v["in_band"] is None}
    # The three sets partition the served statistics; nothing is served
    # twice and nothing served is unaccounted for.
    assert not (served_in & served_out) and not (served_in & served_unreadable)
    assert d["certified"]["band_basis"] == cert["band_basis"]
    # Every shape row is in band, and the shape group is served whole so a
    # reader can tell a crisis row in band from the count a gate reads.
    assert set(SHAPE) <= served_in
    assert d["certified"]["groups"]["shape"] == list(SHAPE)
    # DISPERSION since the fourth composition of 2026-09-22: a graded row
    # measured on a crisis window and carried in its own group, so it is
    # served like the others and belongs in this partition. The row named
    # here rather than the group left open, because a row that reaches the
    # served set without a group is the wiring fault this line catches.
    assert all(k in LEVEL + CRISIS + DISPERSION
               for k in served_in - set(SHAPE))
    # The level and crisis rows are served with their OWN verdicts, which is
    # the property that matters and the one that survives an era boundary.
    # This read `assert "index_drift_pct" in served_out` and pinned the
    # verdict rather than the wiring: it was red at every default through
    # pt-v16 and is green at pt-v18, and a test that pins today's verdict
    # fails on the release that improves the model. What must hold is that
    # each row is served under the verdict the envelope computes for it.
    for row in LEVEL + CRISIS + DISPERSION:
        if row not in cert["statistics"]:
            continue                       # unmeasured; asserted just below
        # Three states again: an UNREADABLE row is served as neither in nor
        # out, and the truthiness form sent it to `served_out`.
        _verdict = cert["statistics"][row]["in_band"]
        expected = (served_unreadable if _verdict is None
                    else served_in if _verdict else served_out)
        assert row in expected, (
            f"{row} is served under the wrong verdict: the envelope reads "
            f"in_band={cert['statistics'][row]['in_band']}")
    assert set(d["certified"]["statistics_unmeasured"]) == set(cert["unmeasured"])
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
    lambda: mcp.build_universe(size=10),
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
    lambda: mcp.run_stress_scenario("rate_ramp", days=3, peak_day=2,
                                    universe_size=8),
    lambda: mcp.explain_price_move(universe_size=8, day=1, top_n=2),
    lambda: mcp.build_universe(size=8),
    lambda: mcp.build_scenario(steps=[{'kind': 'hold', 'fields': {'vix': 30.0}}]),
    lambda: mcp.check_envelope(horizon_days=100),
    lambda: mcp.validate_strategy(MOMENTUM),
])
def test_every_result_serialises(call):
    # A tool result that cannot be JSON-encoded reaches the model as a
    # transport error. `Ranking.separation` was a bound method on the first
    # draft of this server and would have done exactly that.
    json.dumps(call())


# -- compute and argument limits -------------------------------------------
#
# A security review of the 0.8.5 release branch found that no run tool
# bounded `steps_per_day`. A run costs time in proportion to days times
# steps, so MAX_DAYS and the job cap bounded nothing: one direct call with
# steps_per_day=10**9 was still running after 20 seconds, and two such jobs
# took both job workers until the server restarted, so a third, ordinary
# one-day job was refused. The same review found malformed arguments that
# escaped as exceptions, where the module promises refusals as results.

TINY = {"days": 1, "universe_size": 2, "include_baselines": False}


@pytest.mark.parametrize("call,expect", [
    (lambda: mcp.evaluate_strategies(
        {"m": MOMENTUM}, steps_per_day=mcp.MAX_STEPS_PER_DAY + 1, **TINY),
     "steps_per_day must be 1..22"),
    (lambda: mcp.evaluate_strategies({"m": MOMENTUM}, steps_per_day=0, **TINY),
     "steps_per_day must be 1..22"),
    (lambda: mcp.rank_strategies(
        {"m": MOMENTUM}, seeds=[1, 2], days=1, universe_size=2,
        steps_per_day=mcp.MAX_STEPS_PER_DAY + 1),
     "steps_per_day must be 1..22"),
    # Within the per-day cap, but more steps in all than the day cap allows
    # at six a day.
    (lambda: mcp.evaluate_strategies(
        {"m": MOMENTUM}, days=mcp.MAX_DAYS, steps_per_day=7,
        universe_size=2, include_baselines=False),
     "days x steps_per_day must be at most 360"),
])
def test_steps_per_day_is_bounded_like_days(call, expect):
    r = call()
    assert r["ok"] is False
    assert expect in r["error"]


def test_a_huge_steps_per_day_is_refused_at_once():
    """The review's call, on a thread so that a regression fails here
    rather than hanging the suite."""
    import threading

    out = {}
    t = threading.Thread(
        target=lambda: out.update(r=mcp.evaluate_strategies(
            {"m": MOMENTUM}, steps_per_day=10**9, **TINY)),
        daemon=True)
    t.start()
    t.join(10.0)
    assert not t.is_alive(), "evaluate_strategies ran steps_per_day=10**9"
    assert out["r"]["ok"] is False
    assert "steps_per_day" in out["r"]["error"]


def test_the_step_budget_still_admits_what_the_day_cap_admitted():
    assert mcp._steps_refusal(mcp.MAX_DAYS, 6, mcp.MAX_DAYS) is None
    assert mcp._steps_refusal(mcp.MAX_DAYS_ASYNC, 6,
                              mcp.MAX_DAYS_ASYNC) is None
    # The same budget, spent on longer days.
    assert mcp._steps_refusal(mcp.MAX_DAYS // 2, 12, mcp.MAX_DAYS) is None
    refused = mcp._steps_refusal(mcp.MAX_DAYS, 12, mcp.MAX_DAYS)
    assert "start_job" in refused["error"]


@pytest.mark.parametrize("tool,arguments,expect", [
    ("rank_strategies",
     {"strategies": {"m": MOMENTUM}, "days": 60, "steps_per_day": 10**9,
      "universe_size": 120, "seeds": list(range(12))},
     "steps_per_day must be 1..22"),
    ("evaluate_strategies",
     {"strategies": {"m": MOMENTUM}, "days": 60, "steps_per_day": 10**9,
      "universe_size": 120},
     "steps_per_day must be 1..22"),
    ("evaluate_strategies",
     {"strategies": {"m": MOMENTUM}, "days": mcp.MAX_DAYS_ASYNC,
      "steps_per_day": 7},
     "days x steps_per_day must be at most 1512"),
    # A job's arguments go through the tool's own annotations, as a direct
    # call's do (qa085/mcp-tools), so the wording is pydantic's.
    ("evaluate_strategies", {"strategies": {"m": MOMENTUM}, "days": "abc"},
     "days: Input should be a valid integer"),
    ("evaluate_strategies", {"strategies": {"m": MOMENTUM}, "days": 2.5},
     "days: Input should be a valid integer"),
    ("rank_strategies", {"strategies": {"m": MOMENTUM},
                         "steps_per_day": [6]},
     "steps_per_day: Input should be a valid integer"),
    ("run_stress_scenario", {"scenario": "rate_shock", "steps_per_day": 6},
     "takes no argument named ['steps_per_day']"),
    ("evaluate_strategies", ["days", 1], "arguments must be an object"),
])
def test_a_job_that_would_run_unbounded_is_refused_before_it_starts(
        tool, arguments, expect):
    before = len(mcp._jobs)
    r = mcp.start_job(tool, arguments)
    assert r["ok"] is False, r
    assert expect in r["error"]
    assert len(mcp._jobs) == before, "a refused job must not take a worker"


def test_the_estimate_counts_steps_and_survives_odd_arguments():
    base = mcp._estimate_seconds("evaluate_strategies", {"days": 10})
    start_up = mcp._estimate_seconds("evaluate_strategies", {"days": 0})
    # Twice the steps is twice the per-day cost; building the engines is not
    # a per-step cost.
    assert mcp._estimate_seconds(
        "evaluate_strategies", {"days": 10, "steps_per_day": 12}) == \
        pytest.approx(base + (base - start_up))
    # A universe sent as a JSON string, or garbage, counts as the default
    # roster rather than raising after the job has been submitted.
    for universe in ('{"size": 40}', 7, ["x"]):
        assert mcp._estimate_seconds(
            "evaluate_strategies", {"days": 10, "universe": universe}) == \
            pytest.approx(base)


def _row(**over):
    row = {"ticker": "ZZA", "sector": "technology", "initial_price": 50.0,
           "shares_outstanding": 1e8}
    row.update(over)
    return row


@pytest.mark.parametrize("universe,expect", [
    ({"instruments": [_row(ticker=5), _row(ticker="ZZB")]}, "instrument 0"),
    ({"instruments": [_row(initial_price="cheap"), _row(ticker="ZZB")]},
     "instrument 0"),
    ({"instruments": [7, _row(ticker="ZZB")]},
     "instrument 0 must be an object"),
    ({"instruments": 5}, "instruments must be a list"),
    ({"size": [4]}, "universe size must be a whole number"),
    ({"size": 4, "sectors": 5}, "sectors must be a list"),
])
def test_a_malformed_universe_is_refused_as_a_result(universe, expect):
    for r in (mcp.evaluate_strategies({"m": MOMENTUM}, universe=universe,
                                      **TINY),
              mcp.explain_price_move(universe=universe, day=1)):
        assert r["ok"] is False, r
        assert expect in r["error"]


def test_infinite_cash_is_refused_like_nan_cash():
    """cash=nan was refused and cash=inf returned ok: True, because the
    portfolio's check was `x != x or x <= 0`."""
    for cash in (float("inf"), float("nan")):
        r = mcp.evaluate_strategies({"m": MOMENTUM}, cash=cash, **TINY)
        assert r["ok"] is False
        assert "cash must be finite and positive" in r["error"]


# -- installing it -----------------------------------------------------------


def test_explain_without_pyarrow_is_a_refusal_naming_the_extra(monkeypatch):
    """explain needs pyarrow, which is why the `mcp` extra carries it
    (tests/test_packaging.py). Where it is missing anyway, the tool refuses
    with the install line rather than raising.

    Raising was worse than it looks: MCP wraps a tool's exception, so a
    client saw "Error executing tool explain" and nothing of the message
    naming pyarrow and the command that installs it.
    """
    import sys

    monkeypatch.setitem(sys.modules, "pyarrow", None)
    out = mcp.explain(universe_size=8, day=1, depth=1)
    assert out["ok"] is False
    assert "pyarrow" in out["error"]
    assert 'pip install "tradefloor[mcp]"' in out["error"]


def test_the_console_script_hands_over_to_the_server(monkeypatch):
    """`tradefloor-mcp` runs `tradefloor.__main__:mcp_main`, which checks
    for the `mcp` package and then calls `tradefloor.mcp.main`. With the
    package present it must reach the server."""
    from tradefloor.__main__ import mcp_main

    called = []
    monkeypatch.setattr(mcp, "main", lambda: called.append(True))
    mcp_main()
    assert called == [True]

# -- 0.8.5 QA findings -------------------------------------------------------
#
# One test (or a few) per finding from the 0.8.5 review of this server, each
# failing on 5b56d0b and passing after. Most refuse before any engine is
# built, so they cost a parse, not a simulation.

SHOCK_AT_100 = [{"target": "macro.vix", "operation": "set", "value": 45,
                 "at": 100, "duration": 5, "shape": "hold"}]


@pytest.mark.parametrize("name", pt.Scenario.available())
def test_a_shipped_scenario_that_cannot_fire_in_the_run_is_refused(name):
    """Every shipped document starts on day 30 or later, and the default run
    is 20 days. Each one used to come back ok with a difference of 0.0 for
    every entrant and nothing saying the shock never fired."""
    first = min(item.at for item in pt.Scenario.load(name).interventions)
    assert first >= 20, "the premise: no shipped event inside the default"
    r = mcp.run_stress_scenario(name, universe_size=8)
    assert r["ok"] is False, r
    assert f"first event is on day {first}" in r["error"]
    assert f"at least {first + 1} days" in r["error"]


def test_an_authored_shock_after_the_run_is_refused_when_built_and_run():
    built = mcp.build_scenario(shocks=SHOCK_AT_100, days=20)
    assert built["ok"] is False
    assert "day 100" in built["error"] and "start_job" in built["error"]
    ran = mcp.run_stress_scenario({"shocks": SHOCK_AT_100}, days=10,
                                  universe_size=8)
    assert ran["ok"] is False
    assert "day 100" in ran["error"]


def test_an_event_past_the_job_cap_is_said_to_be_out_of_reach():
    r = mcp.build_scenario(shocks=[dict(SHOCK_AT_100[0], at=400)])
    assert r["ok"] is False
    assert f"{mcp.MAX_DAYS_ASYNC}-day cap on a job" in r["error"]


def test_events_that_fall_outside_the_run_are_named_in_a_caveat():
    r = mcp.build_scenario(
        shocks=[{"target": "macro.vix", "operation": "multiply",
                 "value": 2.0, "at": 2, "duration": 20},
                {"target": "macro.corporate_yield", "operation": "add",
                 "value": 0.01, "at": 30, "duration": 2}], days=10)
    assert r["ok"] is True, r
    text = " ".join(r["caveats"])
    assert "1 of the scenario's 2 events starts after this 10-day run" in text
    assert "day 30" in text
    assert "still under way" in text, "the day-2 shock runs to day 21"


def test_the_catalogue_gives_each_documents_first_and_last_event_day():
    for entry in mcp.list_scenarios()["shipped"]:
        starts = [item.at for item in
                  pt.Scenario.load(entry["name"]).interventions]
        assert entry["first_event_day"] == min(starts)
        assert entry["last_event_day"] == max(starts)
        assert f"at least {min(starts) + 1} days" in entry["reach"]
    recession = next(e for e in mcp.list_scenarios()["shipped"]
                     if e["name"] == "recession")
    assert "no run here reaches them" in recession["reach"]


#: The constructors this server exposes by name. Written out rather than read
#: from `mcp.CONSTRUCTORS`, so the test states the public surface it checks.
EXPOSED_CONSTRUCTORS = ["rate_ramp", "vix_shock"]


@pytest.mark.parametrize("name", EXPOSED_CONSTRUCTORS)
def test_every_constructor_takes_peak_day(name):
    """peak_day was passed to vix_shock under its own name, which vix_shock
    does not take, and `rate_shock` resolved to the shipped document before
    the constructor, so no constructor could be timed."""
    r = mcp.run_stress_scenario(name, peak_day=2, days=4, universe_size=4)
    assert r["ok"] is True, r
    assert r["scenario_authored"] is False
    table = r["scenario_table"]
    if name == "vix_shock":
        # The spike arrives on the peak day and not before.
        assert table[1]["vix"] < table[2]["vix"]
    else:
        # The ramp reaches its end level on the peak day and holds it.
        assert table[2] == {**table[3], "day": 2}


def test_no_constructor_is_shadowed_by_a_shipped_document():
    assert sorted(mcp.CONSTRUCTORS) == EXPOSED_CONSTRUCTORS
    assert not set(mcp.CONSTRUCTORS) & set(pt.Scenario.available())
    assert set(mcp.list_scenarios()["constructors"]) == set(mcp.CONSTRUCTORS)
    tools = {t.name: t for t in asyncio.run(mcp.server.list_tools())}
    described = tools["run_stress_scenario"].description
    assert "vol_shock" not in described, "a deprecated alias, not advertised"
    assert all(name in described for name in mcp.CONSTRUCTORS)


def test_peak_day_on_a_document_says_which_constructor_to_use():
    r = mcp.run_stress_scenario("rate_shock", peak_day=5, days=10,
                                universe_size=8)
    assert r["ok"] is False and "rate_ramp" in r["error"]
    r = mcp.run_stress_scenario({"shocks": SHOCK_AT_100}, peak_day=5,
                                days=10, universe_size=8)
    assert r["ok"] is False and "peak_day" in r["error"]


@pytest.mark.parametrize("call", [
    lambda: mcp.evaluate_strategies({"buy_and_hold": MOMENTUM}, days=1,
                                    universe_size=8),
    lambda: mcp.rank_strategies({"oracle": MOMENTUM}, seeds=[1, 2], days=1,
                                universe_size=8),
    lambda: mcp.run_stress_scenario("vix_shock", {"momentum": MOMENTUM},
                                    peak_day=1, days=2, universe_size=8),
])
def test_a_strategy_named_after_a_baseline_is_refused(call):
    """The baselines were added with setdefault, so a strategy called
    buy_and_hold replaced the real one and every versus_buy_and_hold figure
    was measured against the caller's own strategy."""
    r = call()
    assert r["ok"] is False, r
    assert "baseline" in r["error"] and "Rename" in r["error"]


def test_a_baseline_name_is_free_when_the_baselines_are_left_out():
    r = mcp.evaluate_strategies({"buy_and_hold": MOMENTUM}, days=1,
                                universe_size=4, include_baselines=False)
    assert r["ok"] is True, r
    assert [row["name"] for row in r["scores"]] == ["buy_and_hold"]


def test_the_price_move_tool_states_no_factor_count():
    tools = {t.name: t for t in asyncio.run(mcp.server.list_tools())}
    text = tools["explain_price_move"].description.lower()
    for count in ("seven", "eight", "nine", "ten", "eleven", "twelve"):
        assert count not in text, (count, len(pt.Engine.FACTORS))


def _job_count():
    return len(mcp.check_job()["jobs"])


def _wait(job_id):
    for _ in range(3000):
        c = mcp.check_job(job_id)
        if c["status"] != "running":
            return c
        time.sleep(0.1)
    raise AssertionError(f"{job_id} still running")


def test_a_job_whose_estimate_cannot_read_the_universe_still_returns_its_id():
    """The estimate ran after the job was submitted and raised on a universe
    given as JSON text, so the job ran and the caller never saw its id."""
    before = _job_count()
    j = mcp.start_job("evaluate_strategies",
                      {"strategies": {"m": MOMENTUM}, "days": 1,
                       "universe": '{"size": 4}'})
    assert j["ok"] is True, j
    assert _job_count() == before + 1
    assert _wait(j["job_id"])["status"] == "done"


@pytest.mark.parametrize("arguments,named", [
    ({"strategies": {"m": MOMENTUM}, "days": "abc"}, "days"),
    ({"strategies": {"m": MOMENTUM}, "days": 1, "bogus": 1}, "bogus"),
    ({"days": 1}, "strategies"),
    ("not an object", "object"),
])
def test_a_job_with_bad_arguments_is_refused_before_it_starts(arguments,
                                                               named):
    before = _job_count()
    r = mcp.start_job("evaluate_strategies", arguments)
    assert r["ok"] is False, r
    assert named in r["error"]
    assert _job_count() == before, "a refused job must not be registered"


def test_the_estimate_reads_every_universe_form():
    base = {"strategies": {"m": MOMENTUM}, "days": 5}
    small = mcp._estimate_seconds("evaluate_strategies",
                                  {**base, "universe": {"size": 4}})
    assert mcp._estimate_seconds(
        "evaluate_strategies", {**base, "universe": '{"size": 4}'}) == small
    rows = [{"ticker": f"T{i}"} for i in range(4)]
    assert mcp._estimate_seconds(
        "evaluate_strategies", {**base, "universe": {"instruments": rows}}
    ) == small
    assert mcp._estimate_seconds(
        "evaluate_strategies", {**base, "universe": "not json"}) > small


def test_the_ranking_estimate_counts_the_default_seeds():
    assert mcp._estimate_seconds("rank_strategies", {"days": 5}) == \
        mcp._estimate_seconds("rank_strategies",
                              {"days": 5, "seeds": list(mcp.DEFAULT_SEEDS)})


def test_an_empty_seed_list_is_refused():
    r = mcp.rank_strategies({"m": MOMENTUM}, seeds=[], days=1,
                            universe_size=4)
    assert r["ok"] is False and "got 0" in r["error"]


@pytest.mark.parametrize("steps,named", [
    ([{"kind": "hold", "fields": [1, 2]}], "fields must be an object"),
    ([{"kind": "hold", "fields": {"vix": "high"}}], "must be a number"),
    ([{"kind": "ramp", "field": "vix", "start": "a", "end": 45, "over": 10}],
     "start for 'vix' must be a number"),
    ([{"kind": "step", "field": "vix", "before": 15, "after": 45,
       "at": "5"}], "at must be a whole number"),
    ("not a list", "steps must be a list"),
])
def test_a_malformed_step_is_refused_by_name(steps, named):
    r = mcp.build_scenario(steps=steps)
    assert r["ok"] is False, r
    assert named in r["error"]


def test_an_unexpected_exception_comes_back_with_its_message(monkeypatch):
    """The SDK sends a crash as 'Error executing tool X' and nothing else.
    Anything that escapes a tool now comes back as a refusal carrying the
    exception's type and message."""
    def boom():
        raise RuntimeError("the pack is unreadable")

    monkeypatch.setattr(mcp, "_packaged", boom)
    r = mcp.list_scenarios()
    assert r["ok"] is False
    assert "RuntimeError: the pack is unreadable" in r["error"]
    res = asyncio.run(mcp.server.call_tool("list_scenarios", {}))
    assert res.is_error is False
    assert "the pack is unreadable" in res.structured_content["error"]


def test_provenance_carries_the_fingerprint_a_run_records():
    engine = pt.Engine(universe=pt.Universe.random(2, seed=1), seed=1)
    prov = mcp.describe_simulator()["provenance"]
    assert prov["model_fingerprint"] == engine.model_fingerprint != ""
    assert prov["tradefloor_version"] == pt.__version__


def test_the_concentrated_caveat_points_at_a_tool_that_exists():
    """The envelope's refusal tells a library caller to pass the mix name
    as `sector_concentrated`, which no run tool here takes."""
    caveats = mcp._caveats(days=2, n_seeds=1, signals=set(),
                           max_leverage=2.0, universe_size=10,
                           sector_concentrated=True)
    text = " ".join(caveats)
    assert "roster is sector-concentrated" in text, "the gap still fires"
    assert "pass its name as `sector_concentrated`" not in text
    assert "call `check_envelope`" in text


def test_check_envelope_takes_a_mix_name_and_the_macro_question():
    tools = {t.name: t for t in asyncio.run(mcp.server.list_tools())}
    props = tools["check_envelope"].input_schema["properties"]
    kinds = {branch.get("type")
             for branch in props["sector_concentrated"].get("anyOf", [])}
    assert {"boolean", "string"} <= kinds
    assert "macro_regime" in props
    r = mcp.check_envelope(horizon_days=20, macro_regime=True)
    assert r["ok"] and r["inside"] is False
    assert any(g["id"] == "macro-range" for g in r["gaps"])
    named = mcp.check_envelope(horizon_days=20,
                               sector_concentrated="sp500_like")
    assert named["ok"] and "pt-v19" in " ".join(named["reasons"])


def test_a_scenario_driving_inflation_earns_the_macro_range_caveat():
    assert mcp._drives_regime(pt.Scenario.load("oil_price_spike"))
    assert mcp._drives_regime(pt.Scenario("x").hold(inflation_rate=0.06))
    assert not mcp._drives_regime(pt.Scenario.load("liquidity_crisis"))
    r = mcp.run_stress_scenario(
        {"shocks": [{"target": "macro.inflation", "operation": "add",
                     "value": 0.03, "at": 1, "shape": "permanent"}]},
        days=2, universe_size=4)
    assert r["ok"], r
    reason = envelope.check(horizon_days=2, macro_regime=True).reasons[0]
    assert any(reason[:60] in c for c in r["caveats"])


def test_every_tool_parameter_carries_a_description():
    tools = asyncio.run(mcp.server.list_tools())
    for t in tools:
        for name, prop in t.input_schema.get("properties", {}).items():
            assert prop.get("description"), f"{t.name}.{name}"
    by_name = {t.name: t for t in tools}
    scenario = by_name["run_stress_scenario"].input_schema["properties"][
        "scenario"]
    assert {b.get("type") for b in scenario["anyOf"]} == {"string", "object"}
    tool = by_name["start_job"].input_schema["properties"]["tool"]
    assert tool["enum"] == list(mcp.JOBBABLE)
    assert '"signal"' in by_name["evaluate_strategies"].description


def test_an_authored_row_with_nothing_to_value_is_refused():
    """eps and book value are both optional on Instrument, and a row with
    neither is valued at the one-cent floor, so its price falls toward one
    cent every day. Buy-and-hold lost 26% in a day on such a roster and the
    result read as a normal day."""
    bare = [{"ticker": "AAA", "sector": "technology", "initial_price": 50,
             "shares_outstanding": 1e8},
            {"ticker": "BBB", "sector": "energy", "initial_price": 30,
             "shares_outstanding": 2e8}]
    u = mcp.build_universe(instruments=bare)
    assert u["ok"] is False
    assert "AAA" in u["error"] and "eps" in u["error"]
    assert "book_value_per_share" in u["error"]
    r = mcp.evaluate_strategies({"m": MOMENTUM},
                                universe={"instruments": bare}, days=1)
    assert r["ok"] is False and "AAA" in r["error"]
    fixed = [dict(bare[0], eps=2.5), dict(bare[1], book_value_per_share=20)]
    assert mcp.build_universe(instruments=fixed)["ok"] is True


def test_the_refusal_rests_on_what_the_engine_does_with_a_bare_row():
    """The measurement behind the refusal above, so that if the engine ever
    values a bare row some other way this test says the refusal can go.
    Two names, one day, no agents: the bare roster falls by more than a
    tenth and the same roster with earnings does not."""
    import struct

    def day_one(**extra):
        rows = [pt.Instrument("AAA", "technology", initial_price=50,
                              shares_outstanding=1e8, **extra),
                pt.Instrument("BBB", "energy", initial_price=30,
                              shares_outstanding=2e8, **extra)]
        engine = pt.Engine(universe=pt.Universe(rows), seed=7)
        engine.run_days(1)
        raw = engine.prices()
        return struct.unpack(f"<{len(raw) // 8}d", raw)

    bare, valued = day_one(), day_one(eps=2.0)
    assert bare[0] < 50 * 0.9 and bare[1] < 30 * 0.9, bare
    assert valued[0] > 50 * 0.9 and valued[1] > 30 * 0.9, valued


def test_a_horizon_error_does_not_carry_the_statistic_list():
    r = mcp.check_envelope(horizon_days=0)
    assert r["ok"] is False
    assert "'return_acf1'" not in r["error"]
    r = mcp.check_envelope(horizon_days=20, statistics=["nope"])
    assert r["ok"] is False
    assert r["error"].count("'return_acf1'") == 1, "listed once, not twice"


def test_every_served_statistic_has_a_detail_line():
    d = mcp.describe_simulator()["certified"]
    served = (d["statistics_in_band"] + d["statistics_out_of_band"]
              + d["statistics_unreadable"])
    assert len(d["detail"]) == len(served)
    for name in served:
        assert any(line.startswith(name + " ") for line in d["detail"]), name


def test_describe_simulator_does_not_call_every_roster_balanced():
    d = mcp.describe_simulator()
    text = " ".join(d["structural_limitations"])
    assert "The roster is sector-balanced" not in text
    assert "Generated rosters are sector-balanced" in text


def test_the_cost_figures_are_dated_and_match_the_job_estimate():
    cost = mcp.describe_simulator()["limits"]["measured_cost"]
    assert "2026-09-26" in cost
    at5 = mcp._estimate_seconds("evaluate_strategies",
                                {"strategies": {"m": {}}, "days": 5})
    assert f"{at5:.0f}s at 5 days" in cost


@pytest.mark.parametrize("top_n", [0, -3])
def test_a_top_n_below_one_is_refused(top_n):
    r = mcp.explain_price_move(universe_size=4, top_n=top_n)
    assert r["ok"] is False and "top_n" in r["error"]


#: What each tool that is not read-only does to the world, as
#: (read_only, destructive, idempotent). start_job, open_session and
#: session_fork add to the server's memory. session_step advances a session
#: and keeps the state it left. session_rewind and close_session drop state,
#: and doing either twice drops nothing more.
NOT_READ_ONLY = {
    "start_job": (False, False, False),
    "open_session": (False, False, False),
    "session_fork": (False, False, False),
    "session_step": (False, False, False),
    "session_rewind": (False, True, True),
    "close_session": (False, True, True),
}


def test_every_tool_says_what_it_does_to_the_world():
    """Directories and clients read these annotations, and Anthropic's
    connector directory requires a title and the read-only hint on every
    tool. Every other tool builds its own engine and only reads, or reads a
    session without changing it."""
    tools = asyncio.run(mcp.server.list_tools())
    assert len(tools) == 19
    assert set(NOT_READ_ONLY) <= {t.name for t in tools}
    for t in tools:
        a = t.annotations
        assert t.title, t.name
        assert a is not None, t.name
        assert a.open_world_hint is False, t.name
        expected = NOT_READ_ONLY.get(t.name, (True, False, True))
        assert (a.read_only_hint, a.destructive_hint,
                a.idempotent_hint) == expected, t.name


def test_explain_says_when_to_use_it_instead_of_explain_price_move():
    tools = {t.name: t for t in asyncio.run(mcp.server.list_tools())}
    text = tools["explain"].description
    assert text.startswith("Trace one name's price move")
    assert "explain_price_move" in text
    assert "explain" in tools["explain_price_move"].description


def test_the_bundle_manifest_lists_each_tool_by_its_first_sentence():
    """mcpb/manifest.json is what Claude Desktop and Smithery show before the
    server runs. It once still said "Which draws seeded one name's day?"
    after the server's own description had been rewritten."""
    import json
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    manifest = json.loads((root / "mcpb" / "manifest.json").read_text("utf-8"))
    served = {t.name: t.description
              for t in asyncio.run(mcp.server.list_tools())}
    assert sorted(t["name"] for t in manifest["tools"]) == sorted(served)
    for tool in manifest["tools"]:
        first = re.match(r"(.+?[.!?])(\s|$)", served[tool["name"]], re.S)
        assert tool["description"] == " ".join(first.group(1).split()), (
            tool["name"])


# -- fork_day ----------------------------------------------------------------

FORK_STRATEGY = {"m": {"signal": {"kind": "momentum", "lookback_days": 1.0},
                       "portfolio": {"top_k": 3, "gross": 1.0}}}


def test_a_fork_shares_every_day_before_it_bit_for_bit():
    """The arms are one market until the fork: run to the fork day, the
    shocked arm and the control score every entrant identically, and one
    day later the packaged crisis has moved them apart."""
    built = pt.Scenario.load("liquidity_crisis").starting_at(6)
    universe = pt.Universe.random(12, seed=111)

    def run(days, scenario):
        entrants = {**pt.baselines.reference_agents(seed=7)}
        cards = pt.evaluate(entrants, seed=7, universe=universe, days=days,
                            scenario=scenario, trusted_agents=False)
        return {k: (c.return_pct, c.final_net_worth) for k, c in cards.items()}

    assert run(6, built) == run(6, None)
    r = mcp.run_stress_scenario("liquidity_crisis", strategies=FORK_STRATEGY,
                                days=7, fork_day=6, universe_size=12)
    assert r["ok"], r
    assert any(row["difference"] != 0 for row in r["comparison"])


def test_a_fork_starts_the_packaged_scenario_and_records_where():
    r = mcp.run_stress_scenario("liquidity_crisis", strategies=FORK_STRATEGY,
                                days=30, fork_day=10, universe_size=12)
    assert r["ok"], r
    assert r["fork_day"] == 10 and r["provenance"]["fork_day"] == 10
    assert "split on day 10" in r["reading_note"]
    document = r["provenance"]["scenario_document"]
    [origin] = document["origins"]
    assert origin["first_day"] == 10
    assert origin["fingerprint"] == pt.Scenario.load("liquidity_crisis").fingerprint
    again = mcp.run_stress_scenario("liquidity_crisis", strategies=FORK_STRATEGY,
                                    days=30, fork_day=10, universe_size=12)
    assert again["comparison"] == r["comparison"]


def test_without_a_fork_the_result_is_what_it_was():
    r = mcp.run_stress_scenario("liquidity_crisis", days=55, universe_size=8)
    assert r["ok"], r
    assert "fork_day" not in r and "fork_day" not in r["provenance"]
    assert "split on day" not in r["reading_note"]


@pytest.mark.parametrize("fork_day", [-1, 20, 25, True])
def test_a_fork_outside_the_run_is_refused(fork_day):
    r = mcp.run_stress_scenario("liquidity_crisis", days=20,
                                fork_day=fork_day, universe_size=8)
    assert r["ok"] is False and "fork_day must be a day of the run" in r["error"]


@pytest.mark.parametrize("name", sorted(mcp.CONSTRUCTORS))
def test_a_constructor_has_no_fork_point(name):
    r = mcp.run_stress_scenario(name, days=20, fork_day=5, universe_size=8)
    assert r["ok"] is False and "pins the macro path from day 0" in r["error"]


def test_a_macro_path_has_no_fork_point():
    path = mcp.build_scenario(steps=[{"kind": "hold", "fields": {"vix": 30.0}}])
    assert path["ok"], path
    r = mcp.run_stress_scenario(path["scenario"], days=20, fork_day=5,
                                universe_size=8)
    assert r["ok"] is False and "interventions" in r["error"]


def test_a_background_job_takes_a_fork_day():
    j = mcp.start_job("run_stress_scenario",
                      {"scenario": "liquidity_crisis", "days": 12,
                       "fork_day": 4, "universe_size": 6})
    assert j["ok"], j


# -- presets ---------------------------------------------------------------
#
# The run tools take a shipped preset by name. The envelope certifies the
# default alone, so a result under another preset says so, and a result
# under the default is the result it always was.

OTHER = "pt-v19"


@pytest.mark.parametrize("call", [
    lambda **kw: mcp.evaluate_strategies({"m": MOMENTUM}, days=1,
                                         universe_size=8, **kw),
    lambda **kw: mcp.rank_strategies({"m": MOMENTUM}, seeds=[1, 2], days=1,
                                     universe_size=8, **kw),
    lambda **kw: mcp.run_stress_scenario("rate_ramp", days=3, peak_day=2,
                                         universe_size=8, **kw),
    lambda **kw: mcp.explain_price_move(universe_size=8, day=1, top_n=2,
                                        **kw),
    lambda **kw: mcp.explain(universe_size=8, day=1, **kw),
    lambda **kw: mcp.check_envelope(horizon_days=100, **kw),
])
def test_naming_the_default_preset_returns_the_bytes_omitting_it_does(call):
    assert json.dumps(call(), sort_keys=True) == json.dumps(
        call(preset=pt.model_preset()["name"]), sort_keys=True)


@pytest.mark.parametrize("call", [
    lambda **kw: mcp.evaluate_strategies({"m": MOMENTUM}, days=1,
                                         universe_size=8, **kw),
    lambda **kw: mcp.rank_strategies({"m": MOMENTUM}, seeds=[1, 2], days=1,
                                     universe_size=8, **kw),
    lambda **kw: mcp.run_stress_scenario("rate_ramp", days=3, peak_day=2,
                                         universe_size=8, **kw),
    lambda **kw: mcp.explain_price_move(universe_size=8, day=1, top_n=2,
                                        **kw),
    lambda **kw: mcp.explain(universe_size=8, day=1, **kw),
    lambda **kw: mcp.open_session(universe_size=8, **kw),
])
def test_another_preset_runs_and_says_it_is_outside_the_certification(call):
    r = call(preset=OTHER)
    assert r["ok"], r.get("error")
    prov = r["provenance"]
    assert prov["model_preset"] == OTHER
    assert prov["model_fingerprint"] == pt.ModelParams.from_preset(
        OTHER).fingerprint
    assert prov["certified_preset"] == envelope.PRESET
    preset_caveats = [c for c in r["caveats"] if c.startswith("PRESET ")]
    assert len(preset_caveats) == 1, r["caveats"]
    record = pt.preset_record(OTHER)
    held = record["in_band"]["252"]
    of = held + len(record["misses"]["252"])
    assert f"holds {held} of {of} panel rows" in preset_caveats[0]
    assert envelope.PRESET in preset_caveats[0]
    if "session_id" in r:
        mcp.close_session(r["session_id"])


def test_another_preset_runs_another_market():
    default = mcp.evaluate_strategies({"m": MOMENTUM}, days=2, universe_size=8)
    other = mcp.evaluate_strategies({"m": MOMENTUM}, days=2, universe_size=8,
                                    preset=OTHER)
    cards = pt.evaluate({"m": pt.StrategySpec.from_json(
        mcp._normalise(MOMENTUM)[0])}, seed=7,
        universe=pt.Universe.random(8, seed=111), days=2, model=OTHER)
    mine = {row["name"]: row for row in other["scores"]}["m"]
    assert mine["final_net_worth"] == round(cards["m"].final_net_worth, 2)
    assert default["scores"] != other["scores"]


def test_a_default_result_carries_no_preset_caveat_or_key():
    r = mcp.evaluate_strategies({"m": MOMENTUM}, days=1, universe_size=8)
    assert not any(c.startswith("PRESET ") for c in r["caveats"])
    assert "certified_preset" not in r["provenance"]


@pytest.mark.parametrize("call", [
    lambda: mcp.evaluate_strategies({"m": MOMENTUM}, days=1, universe_size=4,
                                    preset="pt-v99"),
    lambda: mcp.rank_strategies({"m": MOMENTUM}, seeds=[1, 2], days=1,
                                universe_size=4, preset="pt-v99"),
    lambda: mcp.run_stress_scenario("rate_ramp", days=3, universe_size=4,
                                    preset="pt-v99"),
    lambda: mcp.explain_price_move(universe_size=4, preset="pt-v99"),
    lambda: mcp.explain(universe_size=4, preset="pt-v99"),
    lambda: mcp.check_envelope(horizon_days=10, preset="pt-v99"),
    lambda: mcp.open_session(universe_size=4, preset="pt-v99"),
    lambda: mcp.start_job("evaluate_strategies",
                          {"strategies": {"m": MOMENTUM}, "days": 1,
                           "universe_size": 4, "preset": "pt-v99"}),
])
def test_an_unknown_preset_is_refused_with_the_shipped_list(call):
    r = call()
    assert r["ok"] is False
    assert "unknown preset 'pt-v99'" in r["error"]
    for name in pt.preset_names():
        assert repr(name) in r["error"]


def test_a_job_runs_the_preset_its_arguments_name():
    j = mcp.start_job("evaluate_strategies",
                      {"strategies": {"m": MOMENTUM}, "days": 1,
                       "universe_size": 4, "preset": OTHER})
    assert j["ok"], j
    assert j["provenance"]["model_preset"] == OTHER
    deadline = time.time() + 60
    while time.time() < deadline:
        done = mcp.check_job(j["job_id"])
        if done["status"] != "running":
            break
        time.sleep(0.2)
    assert done["status"] == "done", done
    assert done["result"]["provenance"]["model_preset"] == OTHER


def test_check_envelope_answers_outside_for_another_preset():
    inside = mcp.check_envelope(horizon_days=100)
    assert inside["inside"] is True and "preset" not in inside
    other = mcp.check_envelope(horizon_days=100, preset=OTHER)
    assert other["inside"] is False
    assert other["reasons"][0].startswith(f"PRESET {OTHER}")
    assert other["preset"]["certified"] is False
    assert other["preset"]["record"]["252"]["in_band"] == pt.preset_record(
        OTHER)["in_band"]["252"]
    # A gap the question meets is still reported beside the preset's.
    longer = mcp.check_envelope(horizon_days=400, preset=OTHER)
    assert [g["id"] for g in longer["gaps"]] == ["horizon"]
    assert len(longer["reasons"]) == 2


def test_describe_simulator_lists_every_preset_and_which_is_certified():
    d = mcp.describe_simulator()["presets"]
    assert d["certified"] == envelope.PRESET
    assert d["default"] == pt.model_preset()["name"]
    shipped = {p["name"]: p for p in d["shipped"]}
    assert list(shipped) == list(pt.preset_names())
    assert [n for n, p in shipped.items() if p["certified"]] == [
        envelope.PRESET]
    for name, entry in shipped.items():
        record = pt.preset_record(name)
        assert entry["record"]["252"]["in_band"] == record["in_band"]["252"]
        assert entry["record"]["252"]["misses"] == record["misses"]["252"]


def test_a_momentum_caveat_on_another_preset_quotes_that_presets_record():
    r = mcp.evaluate_strategies({"m": MOMENTUM}, days=1, universe_size=8,
                                preset=OTHER)
    [line] = [c for c in r["caveats"] if "return autocorrelation" in c]
    own = pt.preset_record(OTHER)["panel_252"]["return_acf1"]
    assert f"{own:.4g}" in line and OTHER in line
    assert mcp._statistic_line("return_acf1") in line


def test_the_oracle_caveat_reads_the_runs_preset():
    oracle = {"signal": {"kind": "oracle"}, "portfolio": {"top_k": 3}}
    default = mcp.evaluate_strategies({"o": oracle}, days=1, universe_size=8)
    other = mcp.evaluate_strategies({"o": oracle}, days=1, universe_size=8,
                                    preset=OTHER)

    def line(r):
        return next(c for c in r["caveats"] if "PRIVILEGED" in c)

    assert ("ceiling for measuring capture" in line(other)) is \
        pt.baselines.oracle_is_ceiling(OTHER)
    assert ("ceiling for measuring capture" in line(default)) is \
        pt.baselines.oracle_is_ceiling()
