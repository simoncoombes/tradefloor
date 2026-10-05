"""The MCP server's populated mode: `population` on the run tools.

A population of background traders makes a run answer a different question
(does an edge survive other traders?) and takes away the one property a
comparison between strategies rests on, that they meet identical markets.
So what matters here is that a populated result says so and names its
population, that it is the library's populated run and nothing else, that
`rank_strategies` stays isolated and says why, and that a call without a
population is the call it was.
"""

import json
import time

import pytest

pytest.importorskip("mcp", reason="the MCP server is an opt-in extra")

import tradefloor as tf  # noqa: E402
from tradefloor import mcp  # noqa: E402
from tradefloor import population as population_module  # noqa: E402

MOMENTUM = {"signal": {"kind": "momentum", "lookback_days": 1.0},
            "portfolio": {"top_k": 3}}

#: The provenance keys an isolated evaluate_strategies result carried
#: before the run tools took a population.
ISOLATED_PROVENANCE = {
    "tradefloor_version", "pretium_version", "model_preset",
    "model_fingerprint", "spec_version", "seed", "universe",
    "universe_fingerprint", "days", "steps_per_day",
}


@pytest.fixture(autouse=True)
def no_sessions_left_open():
    for sid in list(mcp._sessions):
        mcp.close_session(sid)
    yield
    for sid in list(mcp._sessions):
        mcp.close_session(sid)


def spec():
    return tf.StrategySpec.from_json(mcp._normalise(MOMENTUM)[0])


def population_caveats(result):
    return [c for c in result["caveats"] if c.startswith("POPULATION ")]


def wait(job_id):
    deadline = time.time() + 120
    while time.time() < deadline:
        done = mcp.check_job(job_id)
        if done["status"] != "running":
            return done
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} did not finish")


# -- a call without a population is the call it was ------------------------


@pytest.mark.parametrize("call", [
    lambda **kw: mcp.evaluate_strategies({"m": MOMENTUM}, days=1,
                                         universe_size=8, **kw),
    lambda **kw: mcp.run_stress_scenario("rate_ramp", days=3, peak_day=2,
                                         universe_size=8, **kw),
    lambda **kw: mcp.rank_strategies({"m": MOMENTUM}, seeds=[1, 2], days=1,
                                     universe_size=8, **kw),
])
def test_omitting_the_population_returns_the_bytes_none_returns(call):
    assert json.dumps(call(), sort_keys=True) == json.dumps(
        call(population=None), sort_keys=True)


def test_an_isolated_result_carries_nothing_of_populations():
    r = mcp.evaluate_strategies({"m": MOMENTUM}, days=1, universe_size=8)
    assert r["ok"], r.get("error")
    assert set(r["provenance"]) == ISOLATED_PROVENANCE
    assert not population_caveats(r)
    assert r["caveats"][-1] == (
        "The market is single-venue with zero latency and no strategic "
        "counterparties. See `describe_simulator` for the full list.")
    assert all("population" not in row for row in r["scores"])


def test_an_isolated_session_names_no_population():
    r = mcp.open_session(universe_size=8)
    assert "population" not in r["provenance"]
    assert not population_caveats(r)
    listed = mcp.session_state()["sessions"]
    assert listed and all("population" not in s for s in listed)


# -- a populated result -----------------------------------------------------


@pytest.mark.parametrize("name", population_module.SHIPPED)
def test_a_populated_evaluation_is_the_librarys_populated_run(name):
    r = mcp.evaluate_strategies({"m": MOMENTUM}, days=2, universe_size=8,
                                include_baselines=False, population=name)
    assert r["ok"], r.get("error")
    pop = tf.Population.named(name)
    cards = tf.evaluate({"m": spec()}, seed=7,
                        universe=tf.Universe.random(8, seed=111), days=2,
                        trusted_agents=False, population=pop)
    [row] = r["scores"]
    assert row["final_net_worth"] == round(cards["m"].final_net_worth, 2)
    assert row["trades"] == cards["m"].trades
    assert cards["m"].population_fingerprint == pop.fingerprint
    assert r["provenance"]["population"] == name
    assert r["provenance"]["population_fingerprint"] == pop.fingerprint


def test_a_population_changes_the_market():
    isolated = mcp.evaluate_strategies({"m": MOMENTUM}, days=2,
                                       universe_size=8,
                                       include_baselines=False)
    populated = mcp.evaluate_strategies({"m": MOMENTUM}, days=2,
                                        universe_size=8,
                                        include_baselines=False,
                                        population="standard")
    assert isolated["scores"] != populated["scores"]


def test_a_populated_result_is_reproducible():
    def call():
        return mcp.evaluate_strategies({"m": MOMENTUM}, days=1,
                                       universe_size=8, population="crowded")
    assert json.dumps(call(), sort_keys=True) == json.dumps(
        call(), sort_keys=True)


def test_the_populated_caveat_says_what_changes_and_what_was_measured():
    r = mcp.evaluate_strategies({"m": MOMENTUM}, days=1, universe_size=8,
                                population="standard")
    [line] = population_caveats(r)
    pop = tf.Population.standard()
    m = population_module.MEASURED
    assert line.startswith(f"POPULATION standard ({pop.fingerprint}):")
    assert "no longer face identical markets" in line
    assert "rank_strategies, which runs isolated" in line
    assert f"{m['programme_cost_excess']:.1%}" in line
    assert f"{m['programme_cost_excess_reported']:.0%}" in line
    assert m["programme_cost_source"] in line
    assert "mostly transient" in line
    assert m["edge_decay"] in line and m["crowded_exit"] in line
    assert f"{m['return_acf1_shift']:+.3f}" in line
    assert f"{m['runtime_ratio']:.1f} times" in line
    # Right after the model caveat, and the closing caveat no longer says
    # the market has no counterparty that reacts.
    assert r["caveats"][1] == line
    assert "no strategic counterparties" not in r["caveats"][-1]
    assert "population's" in r["caveats"][-1]


def test_the_populated_caveat_is_read_from_the_measured_record(monkeypatch):
    monkeypatch.setitem(population_module.MEASURED, "runtime_ratio", 9.9)
    monkeypatch.setitem(population_module.MEASURED, "return_acf1_shift",
                        -0.042)
    [line] = population_caveats(mcp.evaluate_strategies(
        {"m": MOMENTUM}, days=1, universe_size=8, population="standard"))
    assert "9.9 times" in line and "-0.042" in line


def test_a_preset_and_a_population_each_say_so_in_order():
    other = next(n for n in tf.preset_names()
                 if n != tf.model_preset()["name"])
    r = mcp.evaluate_strategies({"m": MOMENTUM}, days=1, universe_size=8,
                                preset=other, population="standard")
    assert r["ok"], r.get("error")
    assert r["caveats"][1].startswith(f"PRESET {other}")
    assert r["caveats"][2].startswith("POPULATION standard")
    assert r["provenance"]["model_preset"] == other
    assert r["provenance"]["population"] == "standard"


@pytest.mark.parametrize("call", [
    lambda: mcp.evaluate_strategies({"m": MOMENTUM}, days=1, universe_size=4,
                                    population="crowd"),
    lambda: mcp.run_stress_scenario("rate_ramp", days=3, universe_size=4,
                                    population="crowd"),
    lambda: mcp.open_session(universe_size=4, population="crowd"),
    lambda: mcp.start_job("evaluate_strategies",
                          {"strategies": {"m": MOMENTUM}, "days": 1,
                           "universe_size": 4, "population": "crowd"}),
])
def test_an_unknown_population_is_refused_with_the_shipped_list(call):
    r = call()
    assert r["ok"] is False
    assert "unknown population 'crowd'" in r["error"]
    for name in population_module.SHIPPED:
        assert repr(name) in r["error"]
    assert not mcp._sessions


# -- a stress test ----------------------------------------------------------


def test_a_populated_stress_test_runs_both_markets_with_the_population():
    r = mcp.run_stress_scenario("rate_ramp", days=3, peak_day=2,
                                universe_size=8, population="standard")
    assert r["ok"], r.get("error")
    pop = tf.Population.standard()
    roster = tf.Universe.random(8, seed=111)
    built = tf.Scenario.rate_shock(over=2)

    def run(scenario):
        return tf.evaluate(tf.baselines.reference_agents(seed=7), seed=7,
                           universe=roster, days=3, scenario=scenario,
                           trusted_agents=False, population=pop)

    shocked, control = run(built), run(None)
    rows = {row["name"]: row for row in r["comparison"]}
    for name, card in shocked.items():
        assert rows[name]["return_pct_shocked"] == round(card.return_pct, 4)
        assert rows[name]["return_pct_control"] == round(
            control[name].return_pct, 4)
    # The population's caveat after the model's, then the magnitude one.
    assert r["caveats"][1].startswith("POPULATION standard")
    assert r["caveats"][2].startswith("Scenario MAGNITUDE")
    assert r["provenance"]["population_fingerprint"] == pop.fingerprint


# -- rank_strategies stays isolated ----------------------------------------


@pytest.mark.parametrize("call", [
    lambda: mcp.rank_strategies({"m": MOMENTUM}, seeds=[1, 2], days=1,
                                universe_size=4, population="standard"),
    lambda: mcp.start_job("rank_strategies",
                          {"strategies": {"m": MOMENTUM}, "seeds": [1, 2],
                           "days": 1, "universe_size": 4,
                           "population": "standard"}),
])
def test_a_ranking_refuses_a_population_and_says_why(call):
    r = call()
    assert r["ok"] is False
    assert r["error"] == mcp._RANK_ISOLATED
    assert "common random numbers" in r["error"]
    assert "evaluate_strategies" in r["error"]


def test_the_ranking_refusal_comes_before_any_job_starts():
    before = set(mcp._jobs)
    mcp.start_job("rank_strategies",
                  {"strategies": {"m": MOMENTUM}, "seeds": [1, 2],
                   "days": 1, "universe_size": 4, "population": "crowded"})
    assert set(mcp._jobs) == before


# -- background jobs --------------------------------------------------------


def test_a_job_runs_the_population_its_arguments_name():
    args = {"strategies": {"m": MOMENTUM}, "days": 1, "universe_size": 6,
            "include_baselines": False}
    j = mcp.start_job("evaluate_strategies", {**args, "population": "crowded"})
    assert j["ok"], j
    fingerprint = tf.Population.crowded().fingerprint
    assert j["provenance"]["population_fingerprint"] == fingerprint
    done = wait(j["job_id"])
    assert done["status"] == "done", done
    assert done["result"]["provenance"]["population_fingerprint"] == (
        fingerprint)
    assert population_caveats(done["result"])


def test_the_estimate_counts_the_populated_run_time():
    args = {"strategies": {"m": MOMENTUM}, "days": 20}
    isolated = mcp._estimate_seconds("evaluate_strategies", args)
    populated = mcp._estimate_seconds("evaluate_strategies",
                                      {**args, "population": "standard"})
    assert populated == pytest.approx(
        isolated * population_module.MEASURED["runtime_ratio"])


# -- sessions ----------------------------------------------------------------


def test_a_populated_session_agent_scores_what_a_populated_evaluate_scores():
    """The session loop with a population is `tf.evaluate`'s with it."""
    r = mcp.open_session(agents={"m": MOMENTUM}, universe_size=10, seed=5,
                         population="standard")
    assert r["ok"], r.get("error")
    sid = r["session_id"]
    mcp.session_step(sid, days=2)
    final = mcp.session_step(sid, days=1)
    cards = tf.evaluate({"m": spec()}, seed=5,
                        universe=tf.Universe.random(10, seed=111), days=3,
                        trusted_agents=False,
                        population=tf.Population.standard())
    assert final["agents"]["m"]["net_worth"] == round(
        cards["m"].final_net_worth, 2)
    assert final["agents"]["m"]["trades"] == cards["m"].trades
    assert final["provenance"]["population"] == "standard"
    [line] = population_caveats(final)
    assert final["caveats"][1] == line
    assert final["caveats"][2].startswith("SESSION")


def test_a_populated_fork_and_rewind_continue_bit_for_bit():
    sid = mcp.open_session(universe_size=10, population="crowded",
                           steps_per_day=6)["session_id"]
    mcp.session_step(sid, steps=8, orders={"AAA": 400})
    twin = mcp.session_fork(sid)
    assert twin["ok"], twin
    twin = twin["session_id"]
    mark = mcp.session_state(sid)["clock"]["step"]
    hash_at_mark = mcp._sessions[sid].engine.state_hash()
    mcp.session_step(sid, days=2, orders={"AAB": 300})
    mcp.session_step(twin, days=2, orders={"AAB": 300})
    assert (mcp._sessions[sid].engine.state_hash()
            == mcp._sessions[twin].engine.state_hash())
    back = mcp.session_rewind(sid, mark)
    assert back["ok"], back
    assert mcp._sessions[sid].engine.state_hash() == hash_at_mark
    assert mcp._sessions[sid].engine.population_fingerprint == (
        tf.Population.crowded().fingerprint)


def test_the_session_list_names_a_sessions_population():
    mcp.open_session(universe_size=4, population="standard")
    [listed] = mcp.session_state()["sessions"]
    assert listed["population"] == "standard"


# -- what the server says about populations ---------------------------------


def test_describe_simulator_lists_the_populations():
    d = mcp.describe_simulator()["populations"]
    assert d["default"] is None
    assert [p["name"] for p in d["shipped"]] == list(
        population_module.SHIPPED)
    for entry in d["shipped"]:
        pop = tf.Population.named(entry["name"])
        assert entry["fingerprint"] == pop.fingerprint
        assert [p["name"] for p in entry["participants"]] == [
            p.name for p in pop.participants]
    assert d["measured"] == population_module.MEASURED
    assert d["rank_strategies"] == mcp._RANK_ISOLATED
    for tool in ("evaluate_strategies", "run_stress_scenario",
                 "open_session", "start_job"):
        assert tool in d["how"]


def test_the_run_tools_say_they_take_a_population():
    import asyncio
    tools = {t.name: t for t in asyncio.run(mcp.server.list_tools())}
    for name in ("evaluate_strategies", "run_stress_scenario",
                 "open_session", "start_job", "rank_strategies"):
        assert "population" in tools[name].description, name
    for name in ("evaluate_strategies", "run_stress_scenario",
                 "open_session", "rank_strategies"):
        schema = tools[name].input_schema["properties"]["population"]
        assert schema.get("description"), name
    assert "refuses `population`" in tools["rank_strategies"].description
    assert "populations" in tools["describe_simulator"].description
