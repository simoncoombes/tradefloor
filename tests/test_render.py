"""The renderer, and the invariance experiment built on it.

The P6 observation-invariance design note (the programme design
repository, outside this checkout) sets the question: how much of what
an agent decides is the market, and how much is how the market was
described to it. `tradefloor.render` is the seam -- one allowlisted
payload, rendered four ways -- and `tradefloor.counterfactual.invariance`
is the experiment built on it.

Three things this file has to prove, because nothing else checks them:

- `TextRenderer()`, the all-default construction, reproduces
  `integrations.finrobot.render` character for character. `render` is now
  a thin wrapper over it, so this is a delegation check rather than a
  cross-implementation one, and it stays here because it is what actually
  proves the shipped FinRobot fixtures and the published study at
  `examples/integrations/finrobot/README.md` -- both keyed on the digest
  of this exact text -- keep replaying.
- `JSONRenderer()` reproduces what LangGraph, PydanticAI and OpenAI
  Agents already send: `payload`, `json.dumps`-ed with sorted keys. Their
  own shipped fixtures are keyed on it the same way.
- `invariance` gives identical decisions when two renderers are actually
  identical, and separates a presentation effect from an agent's own
  noise when they are not.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import random
import struct
import sys

import pytest

import tradefloor as tf
from tradefloor.counterfactual import Invariance, World, agree, invariance
from tradefloor.integrations import common as ci
from tradefloor.integrations import finrobot as fr
from tradefloor.integrations.langgraph import LangGraphAdapter
from tradefloor.integrations.openai_agents import OpenAIAgentsAdapter
from tradefloor.integrations.pydantic_ai import PydanticAIAdapter
from tradefloor.render import (LANGUAGE, ORDER, UNITS, JSONRenderer,
                               Renderer, TextRenderer)

REPO = pathlib.Path(__file__).resolve().parent.parent


def _load(name: str, path: pathlib.Path):
    """An example script, loaded for its seed, roster and fork constants.

    `name` must be unique across the whole suite -- three integrations
    name their study `rate_shock.py`, and `sys.modules` caches by name --
    which is why every call site below prefixes it. Registered in
    `sys.modules` BEFORE execution: a `@dataclass` resolves its own
    fields' string annotations through `sys.modules[cls.__module__]`, and
    a module absent from it raises inside `dataclasses`, not here.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _f64(buf: bytes) -> list[float]:
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def _observation(world: World):
    """The `Observation` `World.run` would build for the step under way.

    Built by hand, the way the other integration test files do, so a
    payload can be inspected without running a whole day through an agent
    first.
    """
    adv = [i.avg_volume for i in world.universe]
    return tf.Observation(world.step, world.day, world.engine.tickers,
                          _f64(world.engine.prices()), world.portfolio,
                          world.engine, adv, world.steps_per_day)


def small_world(*, n: int = 4, seed: int = 7, days: int = 2,
                agent=None) -> World:
    universe = list(tf.Universe.random(n, seed=3))
    agent = agent or _Hold()
    world = World(seed=seed, universe=universe, agent=agent,
                 pins={"federal_funds_rate": 0.04,
                       "corporate_bond_yield": 0.055},
                 cash=1_000_000.0)
    if days:
        world.run(days=days)
    return world


class _Hold:
    def act(self, obs):
        return {}


# ---------------------------------------------------------------------------
# The Renderer protocol
# ---------------------------------------------------------------------------


def test_textrenderer_and_jsonrenderer_satisfy_the_protocol():
    assert isinstance(TextRenderer(), Renderer)
    assert isinstance(JSONRenderer(), Renderer)


def test_renderers_are_pure_stdlib_no_engine_no_observation():
    """`render.py` imports the standard library, `._core.ValidationError`,
    and -- inside `TextRenderer.render`, lazily, to avoid a load-time
    cycle with `counterfactual.py` -- `.counterfactual.MACRO_FIELDS`, a
    plain tuple of field names. Nothing else, and nothing engine-shaped:
    a renderer that could reach `Engine` or `Observation` would put the
    ground-truth boundary `serialize_observation` guards behind a
    formatting choice.

    Checked over the parsed import statements themselves, module-level
    and local both, rather than `dir(render_module)`: `dir()` only lists
    names bound in the module's OWN namespace, so it cannot see a local
    import inside a method body at all, which is exactly where
    `MACRO_FIELDS` is imported.
    """
    import ast
    import tradefloor.render as render_module

    tree = ast.parse(pathlib.Path(render_module.__file__).read_text(
        encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            # `_core` and `counterfactual` are only allowed as PACKAGE-
            # RELATIVE imports (`from ._core import ...`, `node.level >=
            # 1`) -- this module reaching for an absolute, unrelated
            # top-level package that happened to share one of these
            # names would be a different thing entirely.
            name = node.module
            if name in ("_core", "counterfactual"):
                assert node.level >= 1, (
                    f"{name!r} imported absolutely, not package-relative")
            imported.add(name)

    allowed = {"json", "typing", "__future__", "_core", "counterfactual"}
    assert imported <= allowed, (
        f"render.py imports {imported - allowed}, outside the allowed "
        f"{allowed}")
    assert "numpy" not in imported and "pyarrow" not in imported


# ---------------------------------------------------------------------------
# TextRenderer: byte parity with FinRobot's own render, on the default axes
# ---------------------------------------------------------------------------


def test_default_textrenderer_matches_finrobot_render_small_roster():
    world = small_world(n=4, days=2)
    obs = _observation(world)
    payload = fr.observe(obs, history=[], fundamentals={})
    assert TextRenderer().render(payload) == fr.render(payload)


def test_default_textrenderer_matches_finrobot_render_with_fundamentals():
    world = small_world(n=5, days=3)
    obs = _observation(world)
    history = [[10.0, 20.0, 30.0, 40.0, 50.0]] * 10
    facts = {t: {"sector": "technology", "eps": 3.0, "beta": 1.2}
            for t in obs.tickers}
    payload = fr.observe(obs, history=history, fundamentals=facts)
    assert TextRenderer().render(payload) == fr.render(payload)


def test_default_textrenderer_matches_finrobot_render_with_a_held_position():
    world = small_world(n=6, days=2)
    world.portfolio.execute(world.engine, world.engine.tickers[0], 100)
    obs = _observation(world)
    payload = fr.observe(obs, history=[], fundamentals={})
    assert TextRenderer().render(payload) == fr.render(payload)


def test_default_textrenderer_plus_objective_matches_finrobot_render():
    """The "Objective" section is concatenated by the ADAPTER, not
    rendered -- see `FinRobotAdapter.act`. This is the concatenation
    every caller of `render(payload, objective=...)` used to get for
    free, reproduced by hand the way `FinRobotAdapter` now does it."""
    world = small_world(n=4, days=2)
    world.portfolio.execute(world.engine, world.engine.tickers[1], 50)
    obs = _observation(world)
    payload = fr.observe(obs, history=[], fundamentals={})
    body = TextRenderer().render(payload)
    got = f"{body}\n\nObjective\n---------\nManage the book."
    assert got == fr.render(payload, objective="Manage the book.")


def test_textrenderer_union_held_matches_finrobots_adapter_level_union():
    """`observe(detail=X)` bakes X in as given; `FinRobotAdapter._detail`
    used to union X with whatever is held before calling it.
    `TextRenderer(detail=X, union_held=True)` does that union itself,
    from the payload alone -- this proves the two paths agree once the
    union is done the same way. `union_held` defaults to `False`; see
    `test_bare_render_does_not_union_detail_with_held` for that side."""
    world = small_world(n=12, days=3)
    world.portfolio.execute(world.engine, world.engine.tickers[9], 500)
    obs = _observation(world)
    facts = {t: {"sector": "technology" if i % 2 else "energy"}
            for i, t in enumerate(obs.tickers)}
    panel = [obs.tickers[0], obs.tickers[1]]
    held = [t for t in obs.tickers if obs.position(t)]
    full_detail = sorted(set(panel).union(held))

    payload_baked = fr.observe(obs, history=[], fundamentals=facts,
                               detail=full_detail)
    old = fr.render(payload_baked)

    payload_plain = fr.observe(obs, history=[], fundamentals=facts)
    new = TextRenderer(detail=panel, union_held=True).render(payload_plain)
    assert old == new


def test_bare_render_does_not_union_detail_with_held():
    """The reading the delegation is settled on: `TextRenderer`'s default
    (`union_held=False`) renders `detail` exactly, matching what
    `observe(detail=X)` and `render(payload)`, called directly with no
    adapter, have always published -- the union was `FinRobotAdapter`'s
    own behaviour, applied to the argument it built before calling
    `observe`, not a property `render` gave every caller.

    A held position outside the given `detail` gets no full block here,
    on the default. `union_held=True` is the opt in, checked above."""
    world = small_world(n=12, days=3)
    world.portfolio.execute(world.engine, world.engine.tickers[9], 500)
    obs = _observation(world)
    facts = {t: {"sector": "technology"} for t in obs.tickers}
    panel = [obs.tickers[0], obs.tickers[1]]
    held_ticker = obs.tickers[9]
    assert held_ticker not in panel

    payload = fr.observe(obs, history=[], fundamentals=facts, detail=panel)
    text = fr.render(payload)
    assert held_ticker not in _detail_section(text)

    plain = fr.observe(obs, history=[], fundamentals=facts)
    default_text = TextRenderer(detail=panel).render(plain)
    assert default_text == text
    assert held_ticker not in _detail_section(default_text)


def _detail_section(text: str) -> str:
    """The `Detail` section's own body, up to `Portfolio`.

    Not `text[idx:]` to the end of the string: `Portfolio`'s own
    `Positions:` block lists every held name regardless of whether it
    earned a full block above, so a naive suffix would find a held
    ticker there and report a detail block that was never rendered.
    """
    marker = "\nDetail\n------\n"
    idx = text.find(marker)
    if idx == -1:
        return ""
    body = text[idx + len(marker):]
    end = body.find("\n\nPortfolio\n---------\n")
    return body if end == -1 else body[:end]


def test_a_panelled_finrobot_adapters_recorded_payload_carries_no_detail_key():
    """A stated consequence, not a bug: `FinRobotAdapter.act` no longer
    calls `observe(detail=...)` -- the renderer does the union itself,
    from the payload, at render time -- so `payload["detail"]` and
    `payload["sectors"]`, which a PANELLED adapter's recorded payload
    always carried before this package, are gone from a NEW recording's
    `self.record[i]["payload"]`. The rendered TEXT this same entry's
    `"prompt"` carries is unchanged -- `TextRenderer(union_held=True)`
    reproduces it, proven above -- only the payload stored alongside it.
    No in-repo reader of either key exists outside `finrobot.render`'s
    own old body, which this package also removed, but a reader
    comparing an OLD panelled recording against a NEW one would see this
    shape change in `"payload"` and nowhere else."""
    world = small_world(n=6, days=1, agent=_ScriptedFinRobot(
        panel=["AAA"], fundamentals={}))
    entry = world.agent.record[0]
    assert "detail" not in entry["payload"]
    assert "sectors" not in entry["payload"]
    # The prompt is what a reader actually compares two recordings by,
    # and it still carries the panel's effect: a full block for "AAA".
    assert "AAA" in _detail_section(entry["prompt"])


def test_textrenderer_detail_drops_an_unlisted_symbol():
    world = small_world(n=6, days=1)
    obs = _observation(world)
    payload = fr.observe(obs, history=[], fundamentals={})
    text = TextRenderer(detail=[obs.tickers[0], "NOT_LISTED"]).render(payload)
    assert "NOT_LISTED" not in text
    assert obs.tickers[0] in text


def test_textrenderer_detail_is_sorted_and_deduplicated():
    world = small_world(n=6, days=1)
    obs = _observation(world)
    payload = fr.observe(obs, history=[], fundamentals={})
    tickers = list(obs.tickers[:3])
    a = TextRenderer(detail=tickers).render(payload)
    b = TextRenderer(detail=list(reversed(tickers)) + tickers).render(payload)
    assert a == b


# ---------------------------------------------------------------------------
# TextRenderer: the four axes
# ---------------------------------------------------------------------------


def test_units_bps_changes_the_text_and_never_the_payload():
    """The allowlist test, restated for `units`: rendering never adds a
    key `serialize_observation` did not put there."""
    world = small_world(n=5, days=2)
    obs = _observation(world)
    # A `return_1d` to render as bps needs a real price history: empty
    # history is what day zero looks like, and both units render "not
    # available" there -- see the dedicated test for that case.
    history = [list(obs.prices)] * (obs.steps_per_day + 2)
    payload = ci.serialize_observation(obs, history=history)
    before = json.loads(json.dumps(payload))

    usd = TextRenderer(units="usd").render(payload)
    bps = TextRenderer(units="bps").render(payload)
    assert usd != bps
    assert payload == before, "rendering mutated the payload"
    assert "bps" in bps and "bps" not in usd


def test_units_bps_is_not_available_before_a_return_exists():
    """Day zero: `return_1d` is `None`, and the bps line reads exactly
    what the dollar line reads for a figure the payload never carried."""
    world = small_world(n=3, days=0)
    world.engine.open_market()
    obs = _observation(world)
    payload = ci.serialize_observation(obs, history=[])
    text = TextRenderer(units="bps").render(payload)
    assert "not available" in text


def test_order_reorders_every_asset_listing():
    assets = [
        {"symbol": "ZEBRA", "position": 10, "price": 1.0, "return_1d": None,
         "return_5d": None, "volatility": None, "best_bid": 1.0,
         "best_ask": 1.0, "avg_daily_volume": 100, "max_order_shares": 10,
         "fundamentals": {}},
        {"symbol": "ALPHA", "position": -50, "price": 2.0, "return_1d": None,
         "return_5d": None, "volatility": None, "best_bid": 2.0,
         "best_ask": 2.0, "avg_daily_volume": 100, "max_order_shares": 10,
         "fundamentals": {}},
        {"symbol": "MID", "position": 0, "price": 3.0, "return_1d": None,
         "return_5d": None, "volatility": None, "best_bid": 3.0,
         "best_ask": 3.0, "avg_daily_volume": 100, "max_order_shares": 10,
         "fundamentals": {}},
    ]
    assert [a["symbol"] for a in TextRenderer(order="roster")._ordered(assets)] \
        == ["ZEBRA", "ALPHA", "MID"]
    assert [a["symbol"] for a in TextRenderer(order="alphabetical")._ordered(assets)] \
        == ["ALPHA", "MID", "ZEBRA"]
    assert [a["symbol"] for a in TextRenderer(order="by_position")._ordered(assets)] \
        == ["ALPHA", "ZEBRA", "MID"]


def test_order_roster_is_a_no_op_on_the_default_construction():
    world = small_world(n=6, days=2)
    obs = _observation(world)
    payload = fr.observe(obs, history=[], fundamentals={})
    assert TextRenderer(order="roster").render(payload) == fr.render(payload)


def test_language_fr_translates_labels_and_leaves_data_alone():
    world = small_world(n=3, days=1)
    obs = _observation(world)
    facts = {t: {"sector": "technology"} for t in obs.tickers}
    payload = fr.observe(obs, history=[], fundamentals=facts)
    en = TextRenderer(language="en").render(payload)
    frr = TextRenderer(language="fr").render(payload)
    assert en != frr
    for ticker in obs.tickers:
        assert ticker in frr, "a ticker is data, not a label"
    assert "technology" in frr, "a sector name is data, not a label"


@pytest.mark.parametrize("units", UNITS)
@pytest.mark.parametrize("order", ORDER)
@pytest.mark.parametrize("language", LANGUAGE)
def test_every_axis_combination_renders_without_error(units, order,
                                                       language):
    world = small_world(n=5, days=2)
    world.portfolio.execute(world.engine, world.engine.tickers[0], 25)
    obs = _observation(world)
    facts = {t: {"sector": "technology"} for t in obs.tickers}
    payload = fr.observe(obs, history=[], fundamentals=facts)
    text = TextRenderer(units=units, order=order, language=language,
                        detail=[obs.tickers[0]]).render(payload)
    assert text and isinstance(text, str)


@pytest.mark.parametrize("bad_kwarg,value", [
    ("units", "eur"), ("order", "size"), ("language", "de")])
def test_an_invalid_axis_value_is_refused_at_construction(bad_kwarg, value):
    with pytest.raises(ci.ValidationError):
        TextRenderer(**{bad_kwarg: value})


# ---------------------------------------------------------------------------
# key(): stable, distinguishing, and the frozen default
# ---------------------------------------------------------------------------


def test_the_default_textrenderer_key_is_frozen():
    """P7 pins its battery on this exact string. Changing it is a breaking
    change to a package that has not been written against this one yet."""
    assert TextRenderer().key() == "text/en/usd/roster/full"


def test_the_default_jsonrenderer_key_is_frozen():
    assert JSONRenderer().key() == "json"


def test_key_is_stable_and_order_independent_on_detail():
    a = TextRenderer(detail=["B", "A"])
    b = TextRenderer(detail=["A", "B"])
    assert a.key() == b.key()


def test_every_axis_changes_the_key():
    base = TextRenderer().key()
    assert TextRenderer(units="bps").key() != base
    assert TextRenderer(order="alphabetical").key() != base
    assert TextRenderer(language="fr").key() != base
    assert TextRenderer(detail=["A"]).key() != base
    assert TextRenderer(detail=[]).key() != base
    assert TextRenderer(detail=[]).key() != TextRenderer(detail=["A"]).key()


def test_two_renderers_built_the_same_way_are_interchangeable():
    a, b = TextRenderer(units="bps"), TextRenderer(units="bps")
    assert a is not b
    assert a.key() == b.key()
    assert a == b


# ---------------------------------------------------------------------------
# JSONRenderer: byte parity with LangGraph, PydanticAI and OpenAI Agents
# ---------------------------------------------------------------------------


def test_jsonrenderer_matches_the_historical_json_body():
    world = small_world(n=3, days=1)
    obs = _observation(world)
    payload = ci.serialize_observation(obs, history=[])
    expected = json.dumps(payload, indent=2, sort_keys=True)
    assert JSONRenderer().render(payload) == expected
    # And the three adapters' own historical constructions of it, which
    # differ only in keyword order and an unused `default=` hook:
    assert JSONRenderer().render(payload) == json.dumps(
        payload, sort_keys=True, indent=2, default=float)


def test_jsonrenderer_sorts_keys_so_dict_order_cannot_move_the_digest():
    assert JSONRenderer().render({"b": 2, "a": 1}) \
        == JSONRenderer().render({"a": 1, "b": 2})


def test_jsonrenderer_refuses_a_fundamentals_value_it_cannot_encode():
    """`fundamentals` is caller-supplied and passed through unconverted;
    a `Decimal` in one is not an allowlist defect, and `JSONRenderer`
    raises `json`'s own `TypeError` naming the type rather than silently
    coercing it -- the reading LangGraph's and PydanticAI's own
    historical `render` already took with no `default=` hook. OpenAI
    Agents' prior `default=float` would have coerced this silently; see
    the class docstring for why that reading lost."""
    import decimal

    world = small_world(n=2, days=1)
    obs = _observation(world)
    facts = {obs.tickers[0]: {"market_cap": decimal.Decimal("1e9")}}
    payload = ci.serialize_observation(obs, history=[], fundamentals=facts)
    with pytest.raises(TypeError):
        JSONRenderer().render(payload)


# ---------------------------------------------------------------------------
# Every adapter's default renderer reproduces its shipped fixture
# ---------------------------------------------------------------------------


def test_finrobot_default_renderer_replays_the_shipped_fixture():
    example = _load("test_render_finrobot_rate_shock",
                    REPO / "examples" / "integrations" / "finrobot"
                    / "rate_shock.py")
    fixture_path = REPO / "tests" / "fixtures" / "finrobot" / "rate-shock.json"
    if not fixture_path.exists():
        pytest.skip("no committed FinRobot fixture")
    transcript = fr.Transcript.load(fixture_path)

    agent = fr.FinRobotAdapter(mode="replay", transcript=transcript,
                               fundamentals=example.FUNDAMENTALS,
                               objective=example.OBJECTIVE,
                               every=example.DECISION_EVERY, arm="shared")
    assert agent.renderer.key() == "text/en/usd/roster/full", (
        "not the default any more; this test must exercise it")
    world = World(seed=example.SEED, universe=example.universe(),
                 agent=agent, pins=example.BASE_PINS, cash=example.CASH,
                 steps_per_day=example.STEPS_PER_DAY,
                 ticks_per_step=example.TICKS_PER_STEP)
    world.run(days=example.WARMUP_DAYS)

    recorded_digests = [e["digest"] for e in transcript.entries[
        :example.WARMUP_DAYS]]
    assert [e["digest"] for e in agent.record] == recorded_digests, (
        "the default renderer no longer reproduces the shipped fixture's "
        "prompts byte for byte")


def test_langgraph_default_renderer_replays_the_shipped_fixture():
    example = _load("test_render_langgraph_rate_shock",
                    REPO / "examples" / "integrations" / "langgraph"
                    / "rate_shock.py")
    fixture_path = REPO / "tests" / "fixtures" / "langgraph" / "rate-shock.json"
    if not fixture_path.exists():
        pytest.skip("no committed LangGraph fixture")
    transcript = ci.Transcript.load(fixture_path)

    agent = LangGraphAdapter(mode="replay", transcript=transcript,
                             fundamentals=example.FUNDAMENTALS,
                             every=example.DECISION_EVERY, arm="shared")
    assert agent.renderer.key() == "json", (
        "not the default any more; this test must exercise it")
    world = World(seed=example.SEED, universe=example.universe(),
                 agent=agent, cash=example.CASH, pins=example.BASE_PINS)
    world.run(days=example.WARMUP_DAYS)

    recorded_digests = [e["digest"] for e in transcript.entries[
        :example.WARMUP_DAYS]]
    assert [e["digest"] for e in agent.record] == recorded_digests


def test_pydantic_ai_default_renderer_replays_the_shipped_fixture():
    example = _load("test_render_pydantic_ai_rate_shock",
                    REPO / "examples" / "integrations" / "pydantic_ai"
                    / "rate_shock.py")
    fixture_path = REPO / "tests" / "fixtures" / "pydantic_ai" / "rate-shock.json"
    if not fixture_path.exists():
        pytest.skip("no committed PydanticAI fixture")
    transcript = ci.Transcript.load(fixture_path)

    agent = PydanticAIAdapter(mode="replay", transcript=transcript)
    assert agent.renderer.key() == "json", (
        "not the default any more; this test must exercise it")
    world = World(seed=example.SEED, universe=example.universe(),
                 agent=agent, cash=example.CASH, pins=example.PINS)
    world.run(days=example.SHARED_DAYS)

    recorded_digests = [e["digest"] for e in transcript.entries[
        :example.SHARED_DAYS]]
    assert [e["digest"] for e in agent.record] == recorded_digests


def test_openai_agents_default_renderer_replays_the_shipped_fixture():
    example = _load("test_render_openai_agents_five_days",
                    REPO / "examples" / "integrations" / "openai_agents"
                    / "five_days.py")
    fixture_path = (REPO / "tests" / "fixtures" / "openai_agents"
                    / "five-days.json")
    if not fixture_path.exists():
        pytest.skip("no committed OpenAI Agents fixture")
    transcript = ci.Transcript.load(fixture_path)

    agent = OpenAIAgentsAdapter(mode="replay", transcript=transcript)
    assert agent.renderer.key() == "json", (
        "not the default any more; this test must exercise it")
    card = tf.evaluate({"pm": agent}, seed=example.SEED,
                       universe=example.universe(), days=example.DAYS)["pm"]

    recorded_digests = [e["digest"] for e in transcript.entries[
        :example.DAYS]]
    assert [e["digest"] for e in agent.record] == recorded_digests
    assert card.trades >= 0  # the run completed rather than refusing


# ---------------------------------------------------------------------------
# The renderer key in transcript metadata
# ---------------------------------------------------------------------------


def test_finrobot_provenance_carries_the_renderer_key():
    agent = fr.FinRobotAdapter(mode="live",
                               llm_config={"config_list": [{"model": "m"}]},
                               renderer=TextRenderer(units="bps"))
    assert agent.provenance()["renderer"] == "text/en/bps/roster/full"


def test_pydantic_ai_provenance_carries_the_renderer_key():
    agent = PydanticAIAdapter(mode="replay", transcript=ci.Transcript(),
                              renderer=TextRenderer(language="fr"))
    assert agent.provenance()["renderer"] == "text/fr/usd/roster/full"


def test_langgraph_provenance_carries_the_renderer_key():
    from test_langgraph import DuckGraph, contract  # local test helpers
    agent = LangGraphAdapter(DuckGraph(contract.hold),
                             renderer=TextRenderer(order="alphabetical"))
    assert agent.provenance()["renderer"] == \
        "text/en/usd/alphabetical/full"


def test_openai_agents_provenance_carries_the_renderer_key():
    agent = OpenAIAgentsAdapter(mode="replay", transcript=ci.Transcript(),
                                renderer=JSONRenderer())
    assert agent.provenance()["renderer"] == "json"


# ---------------------------------------------------------------------------
# invariance(): identical renderers give identical decisions
# ---------------------------------------------------------------------------


def test_two_identical_renderers_give_identical_decisions_on_the_fixture():
    """`invariance` proper needs a `renderer` attribute and a live fork;
    this is the narrower claim it rests on -- two SEPARATELY CONSTRUCTED
    but identically configured renderers replaying the same fixture reach
    the same decisions and the same transcript keys, step for step."""
    example = _load("test_render_finrobot_rate_shock",
                    REPO / "examples" / "integrations" / "finrobot"
                    / "rate_shock.py")
    fixture_path = REPO / "tests" / "fixtures" / "finrobot" / "rate-shock.json"
    if not fixture_path.exists():
        pytest.skip("no committed FinRobot fixture")
    transcript = ci.Transcript.load(fixture_path)

    def run_one(renderer):
        agent = fr.FinRobotAdapter(mode="replay", transcript=transcript,
                                   fundamentals=example.FUNDAMENTALS,
                                   objective=example.OBJECTIVE,
                                   every=example.DECISION_EVERY,
                                   renderer=renderer, arm="shared")
        world = World(seed=example.SEED, universe=example.universe(),
                     agent=agent, pins=example.BASE_PINS, cash=example.CASH,
                     steps_per_day=example.STEPS_PER_DAY,
                     ticks_per_step=example.TICKS_PER_STEP)
        world.run(days=example.WARMUP_DAYS)
        return agent

    a = run_one(TextRenderer())
    b = run_one(TextRenderer())
    assert a is not b
    assert [e["digest"] for e in a.record] == [e["digest"] for e in b.record]
    assert [e["decision"] for e in a.record] == \
        [e["decision"] for e in b.record]
    assert [e["prompt"] for e in a.record] == [e["prompt"] for e in b.record]


# ---------------------------------------------------------------------------
# invariance(): the fork agreement, on a real FinRobotAdapter
# ---------------------------------------------------------------------------


class _ScriptedFinRobot(fr.FinRobotAdapter):
    """A live-mode `FinRobotAdapter` with `_ask` replaced by a fixed
    answer, so `invariance()` can run two REAL `FinRobotAdapter` forks
    -- not a hand-rolled double -- with no network call. Mirrors
    `tests/test_finrobot.py`'s own `Scripted`, which the docstring there
    explains: `_ask` is the one method that reaches outside the process,
    and everything below it -- the render, the parse, the validation --
    runs unmodified."""

    def __init__(self, **kwargs):
        kwargs.setdefault("mode", "live")
        kwargs.setdefault("llm_config", {"config_list": [{"model": "none"}]})
        super().__init__(**kwargs)

    def _ask(self, prompt, key, obs):
        return json.dumps({"actions": [], "rationale": "hold"})


def test_invariance_fork_agreement_holds_on_a_real_finrobot_adapter():
    """The regression `agree()`-before-the-swap fixes. `FinRobotAdapter`
    is the one adapter whose `state()` publishes `"renderer"`
    (`test_the_fork_agreement_covers_the_panel`'s neighbour); calling
    `agree()` after the swap instead of before would see that field
    differ between every pair and report `fork_agreed=False` on every
    FinRobot run, unconditionally, which is a comparison artefact and
    not a finding. A double with no `renderer` in its own `state()`
    (`_KnownFloorAgent`, used elsewhere in this file) cannot exercise
    this at all, which is why this test builds a real adapter."""
    agent = _ScriptedFinRobot(fundamentals={})
    world = small_world(n=4, days=1, agent=agent)
    report = invariance(world, [TextRenderer(), TextRenderer(units="bps")],
                        days=1, floor=False)
    assert len(report.presentation) == 1
    comparison = report.presentation[0]
    assert comparison.agreement is not None
    assert comparison.agreement.identical, (
        "the forks disagreed at the fork, before either renderer ran a "
        f"step: {comparison.agreement.differences}")

    pytest.importorskip("pyarrow")
    assert report.table().column("fork_agreed").to_pylist() == [True]


# ---------------------------------------------------------------------------
# invariance(): against a recorded agent, only the matching renderer replays
# ---------------------------------------------------------------------------


def test_invariance_reports_a_non_matching_renderer_as_unrecorded():
    """The design note's claim: "against a recorded agent only renderers
    with recordings replay; the others are reported as unrecorded."
    Against the real shipped fixture, recorded under FinRobotAdapter's
    default renderer, a second renderer with different text has no
    entry for its first post-fork prompt and must not take the call
    down."""
    example = _load("test_render_finrobot_unrecorded",
                    REPO / "examples" / "integrations" / "finrobot"
                    / "rate_shock.py")
    fixture_path = REPO / "tests" / "fixtures" / "finrobot" / "rate-shock.json"
    if not fixture_path.exists():
        pytest.skip("no committed FinRobot fixture")
    transcript = fr.Transcript.load(fixture_path)

    agent = fr.FinRobotAdapter(mode="replay", transcript=transcript,
                               fundamentals=example.FUNDAMENTALS,
                               objective=example.OBJECTIVE,
                               every=example.DECISION_EVERY, arm="shared")
    world = World(seed=example.SEED, universe=example.universe(),
                 agent=agent, pins=example.BASE_PINS, cash=example.CASH,
                 steps_per_day=example.STEPS_PER_DAY,
                 ticks_per_step=example.TICKS_PER_STEP)
    world.run(days=example.WARMUP_DAYS)

    matching = TextRenderer()
    other = TextRenderer(units="bps")
    report = invariance(world, [matching, other],
                        days=example.BRANCH_DAYS, floor=False)

    assert report.unrecorded == [other.key()]
    assert report.stopped_early == {}, (
        "the matching renderer ran exactly the days asked for; it did "
        "not stop early")
    assert matching.key() in report.decisions
    assert other.key() not in report.decisions
    assert report.presentation == [], (
        "a pair needs both sides measured; only one renderer replayed")
    # One entry per post-fork STEP, not per day -- see `Invariance.decisions`
    # -- and every one of them is the agent's then-STANDING decision, which
    # `World.run`'s trace repeats between actual decision points rather than
    # leaving `None`; only a refused step is `None`. At the default cadence
    # (one decision a day) the standing decision changes at each day
    # boundary and holds for `STEPS_PER_DAY` rows.
    decisions = report.decisions[matching.key()]
    assert len(decisions) == example.BRANCH_DAYS * example.STEPS_PER_DAY
    assert all(d is not None for d in decisions)
    changes = sum(1 for a, b in zip(decisions, decisions[1:]) if a != b)
    assert changes == example.BRANCH_DAYS - 1, (
        "the standing decision should change once per day boundary and "
        "hold in between")
    # The report says so in text, not only in the field.
    assert other.key() in report.render()


def test_invariance_asked_for_more_days_than_the_fixture_covers_stops_early():
    """Round 2, finding 1: asking for `days` more than the transcript
    covers used to catch the exception at the WHOLE `run()` call, so the
    renderer the recording was made under -- which replayed every day it
    could -- was reported exactly the same as one that never matched at
    all: `unrecorded`, with the days it genuinely measured discarded.
    `invariance()` now runs day by day and keeps what succeeded; a
    renderer that ran out partway is in `stopped_early`, not
    `unrecorded`, and its partial `decisions` are kept."""
    example = _load("test_render_finrobot_stopped_early",
                    REPO / "examples" / "integrations" / "finrobot"
                    / "rate_shock.py")
    fixture_path = REPO / "tests" / "fixtures" / "finrobot" / "rate-shock.json"
    if not fixture_path.exists():
        pytest.skip("no committed FinRobot fixture")
    transcript = fr.Transcript.load(fixture_path)

    agent = fr.FinRobotAdapter(mode="replay", transcript=transcript,
                               fundamentals=example.FUNDAMENTALS,
                               objective=example.OBJECTIVE,
                               every=example.DECISION_EVERY, arm="shared")
    world = World(seed=example.SEED, universe=example.universe(),
                 agent=agent, pins=example.BASE_PINS, cash=example.CASH,
                 steps_per_day=example.STEPS_PER_DAY,
                 ticks_per_step=example.TICKS_PER_STEP)
    world.run(days=example.WARMUP_DAYS)

    matching = TextRenderer()
    other = TextRenderer(units="bps")
    asked = example.BRANCH_DAYS + 5  # more than the fixture covers
    report = invariance(world, [matching, other], days=asked, floor=False)

    assert report.unrecorded == [other.key()], (
        "the non-matching renderer still never got a first reply")
    assert matching.key() not in report.unrecorded, (
        "the reference renderer replayed most of the run and must not "
        "be reported the same as one that never matched at all")
    assert set(report.stopped_early) == {matching.key()}
    stopped_step = report.stopped_early[matching.key()]
    assert stopped_step == \
        world.step + example.BRANCH_DAYS * example.STEPS_PER_DAY

    # Its measurement up to the stop is kept, not thrown away.
    assert matching.key() in report.decisions
    decisions = report.decisions[matching.key()]
    assert len(decisions) == example.BRANCH_DAYS * example.STEPS_PER_DAY
    assert all(d is not None for d in decisions)

    text = report.render()
    assert "stopped early" in text and matching.key() in text
    assert "unrecorded" in text and other.key() in text


# ---------------------------------------------------------------------------
# invariance(): a pair where one arm stops early, not at the first step
# ---------------------------------------------------------------------------


class _FlakyFinRobot(fr.FinRobotAdapter):
    """A live-mode `FinRobotAdapter` that answers normally for
    `good_for` decisions, then raises on every one after -- for the
    renderer whose `key()` equals `fail_key` only. The real fixture
    cannot exercise a pair where one arm stops PARTWAY through: a
    mismatched renderer's text never matches the recording, so it
    fails on the very first post-fork step, not partway. This is a
    live agent instead, so it can fail on a specific later call."""

    def __init__(self, *, fail_key: str | None = None, good_for: int = 3,
                **kwargs) -> None:
        kwargs.setdefault("mode", "live")
        kwargs.setdefault("llm_config", {"config_list": [{"model": "none"}]})
        super().__init__(**kwargs)
        self.fail_key = fail_key
        self.good_for = good_for
        self._calls = 0

    def _ask(self, prompt, key, obs):
        self._calls += 1
        if (self.fail_key is not None and self.renderer.key() == self.fail_key
                and self._calls > self.good_for):
            raise fr.DecisionError(
                "scripted failure after good_for decisions")
        return json.dumps({"actions": [], "rationale": "hold"})

    def fork(self):
        # `FinRobotAdapter.fork` is hand-written, not `fork_kwargs()`-based
        # (it does not subclass `common.FrameworkAdapter`), and its
        # constructor call knows nothing about this subclass's extra
        # arguments -- overriding `fork_kwargs()` here is silently never
        # called. `super().fork()` still does the real work (a fresh
        # `type(self)(...)`, with `history`/`record`/`_decision` copied
        # over), so this only has to reattach what it drops.
        twin = super().fork()
        twin.fail_key = self.fail_key
        twin.good_for = self.good_for
        return twin


def test_invariance_reports_the_actual_span_when_one_arm_stops_early():
    """Round 3, the one remaining finding: `compare()` diffs two traces
    with `zip()`, which silently stops at the shorter one, so a pair
    where one arm stopped early and the other ran the full span must
    not be reported as if both covered `days` -- a "never diverged" row
    is a claim about `days_compared`, not necessarily `days`."""
    bps = TextRenderer(units="bps")
    usd = TextRenderer()
    agent = _FlakyFinRobot(fail_key=bps.key(), good_for=3, fundamentals={})
    world = small_world(n=4, days=1, agent=agent)
    fork_step = world.step

    report = invariance(world, [usd, bps], days=8, floor=False)

    assert report.unrecorded == []
    assert set(report.stopped_early) == {bps.key()}
    # Stopped after 3 good decisions, not on the first post-fork step.
    assert report.stopped_early[bps.key()] == \
        fork_step + agent.good_for * world.steps_per_day
    assert report.stopped_early[bps.key()] > fork_step

    assert len(report.presentation) == 1
    assert report.days_compared[(usd.key(), bps.key())] == 3
    assert len(report.decisions[bps.key()]) == \
        3 * world.steps_per_day, "the 3 good days are kept, not discarded"

    text = report.render()
    assert "compared over 3 of 8 days asked for" in text

    pytest.importorskip("pyarrow")
    table = report.table()
    assert table.column("days_compared").to_pylist() == [3]


# ---------------------------------------------------------------------------
# invariance(): a scripted agent with a known floor
# ---------------------------------------------------------------------------


class _KnownFloorAgent(ci.FrameworkAdapter):
    """Decides from `self.renderer`'s text alone: a large, deterministic
    shift when the text carries "bps" (a presentation effect this test
    controls), plus small seeded noise on every call (a floor this test
    also controls). `reask` draws fresh noise from the same generator, the
    way a real model's repeated answer to one frozen prompt would vary."""

    def __init__(self, *, ticker: str, renderer=None, noise_seed: int = 0,
                base: int = 100, spread: int = 20, **kwargs) -> None:
        super().__init__(**kwargs)
        self.renderer = renderer if renderer is not None else JSONRenderer()
        self.ticker = ticker
        self.noise_seed = noise_seed
        self.base = base
        self.spread = spread
        self._rng = random.Random(noise_seed)

    def _decide(self, text: str) -> dict:
        qty = (2 * self.base if "bps" in text else self.base)
        qty += self._rng.choice([-self.spread, 0, self.spread])
        return {"actions": [{"symbol": self.ticker, "side": "BUY",
                            "quantity": qty}],
               "rationale": "scripted"}

    def ask(self, obs, payload):
        text = self.renderer.render(payload)
        self.record_exchange(text)
        return self._decide(text)

    def reask(self, entry):
        text = self.renderer.render(entry["payload"])
        return self._decide(text)

    def fork_kwargs(self):
        kwargs = super().fork_kwargs()
        kwargs.update(ticker=self.ticker, renderer=self.renderer,
                      noise_seed=self.noise_seed, base=self.base,
                      spread=self.spread)
        return kwargs


def _floor_world() -> World:
    universe = list(tf.Universe.random(3, seed=11))
    agent = _KnownFloorAgent(ticker=universe[0].ticker,
                             renderer=JSONRenderer(), every=1,
                             noise_seed=1)
    world = World(seed=5, universe=universe, agent=agent,
                 pins={"federal_funds_rate": 0.04,
                       "corporate_bond_yield": 0.055},
                 cash=1_000_000.0, steps_per_day=1, ticks_per_step=5)
    world.run(days=2)
    return world


def test_invariance_refuses_fewer_than_two_renderers():
    world = _floor_world()
    with pytest.raises(ci.ValidationError, match="at least 2"):
        invariance(world, [JSONRenderer()], days=1)


def test_invariance_refuses_renderers_with_the_same_key():
    world = _floor_world()
    with pytest.raises(ci.ValidationError, match="distinct keys"):
        invariance(world, [JSONRenderer(), JSONRenderer()], days=1)


def test_invariance_refuses_an_agent_with_no_renderer():
    universe = list(tf.Universe.random(3, seed=11))
    world = World(seed=5, universe=universe, agent=_Hold(),
                 pins={"federal_funds_rate": 0.04,
                       "corporate_bond_yield": 0.055})
    world.run(days=1)
    with pytest.raises(ci.ValidationError, match="renderer"):
        invariance(world, [JSONRenderer(), TextRenderer()], days=1)


def test_invariance_separates_a_presentation_effect_from_the_floor():
    world = _floor_world()
    fork_step = world.step  # the first post-fork decision step
    report = invariance(
        world, [TextRenderer(units="usd"), TextRenderer(units="bps")],
        days=2)

    assert report.renderers == ["text/en/usd/roster/full",
                                "text/en/bps/roster/full"]
    assert len(report.presentation) == 1
    comparison = report.presentation[0]
    assert comparison.agreement is not None and comparison.agreement.identical, (
        "the two forks must start identical, or the divergence below could "
        "be attributed to a bad fork rather than to the renderer")

    # The presentation effect: `units="bps"` doubles this agent's base
    # order size, and it does so from the FIRST post-fork decision.
    assert comparison.divergence.decision == fork_step
    assert comparison.divergence.orders == fork_step

    # The floor: two forks of the identical `usd` renderer, resampled.
    assert report.floor is not None
    assert report.floor.identical_inputs, (
        "the floor's two arms must see byte-identical input, or the gap "
        "measured is not pure agent noise")
    # `_net` counts BUY minus SELL actions, not shares -- see
    # `counterfactual._net`. This scripted agent only ever sends one BUY,
    # so `_net` is the constant 1.0 on every call and `stdev_net` is 0.0
    # by construction, which would make the assertion below pass whether
    # or not a real floor exists. `_gross` -- shares moved, which is
    # where `spread` actually shows up -- is the statistic this agent's
    # noise lives in, so it is the one the separation is measured
    # against.
    floor_gross = report.floor.noise[report.floor.control]["stdev_gross"]
    assert floor_gross > 0, (
        "the scripted agent's own noise (spread=20) must show up as a "
        "real, nonzero floor, or there is nothing to separate the "
        "presentation effect from")

    # The separation the report exists to show: the presentation gap (one
    # base-quantity's worth, ~2x the scripted agent's own base) dwarfs the
    # floor's within-arm spread (bounded by `spread=20`).
    usd_decisions = report.decisions["text/en/usd/roster/full"]
    bps_decisions = report.decisions["text/en/bps/roster/full"]
    first_usd_qty = usd_decisions[0]["actions"][0]["quantity"]
    first_bps_qty = bps_decisions[0]["actions"][0]["quantity"]
    presentation_gap = abs(first_bps_qty - first_usd_qty)
    assert presentation_gap >= 100 - 20, (
        "the presentation effect should be at least a base quantity's "
        "worth, net of one noise draw")
    assert presentation_gap > floor_gross, (
        "the presentation effect should exceed the agent's own noise")


def test_invariance_floor_false_skips_the_extra_fork():
    world = _floor_world()
    report = invariance(
        world, [TextRenderer(units="usd"), TextRenderer(units="bps")],
        days=1, floor=False)
    assert report.floor is None


def test_invariance_table_has_one_row_per_pair():
    pytest.importorskip("pyarrow")
    world = _floor_world()
    report = invariance(
        world, [TextRenderer(units="usd"), TextRenderer(units="bps"),
               JSONRenderer()],
        days=1, floor=False)
    table = report.table()
    assert table.num_rows == 3  # 3 renderers -> 3 pairs
    assert set(table.column_names) >= {
        "renderer_a", "renderer_b", "fork_agreed", "decision_diverges_at"}


def test_invariance_render_produces_readable_text():
    world = _floor_world()
    report = invariance(
        world, [TextRenderer(units="usd"), TextRenderer(units="bps")],
        days=1)
    text = report.render()
    assert "text/en/usd/roster/full" in text
    assert "text/en/bps/roster/full" in text
    assert "noise floor" in text


# ---------------------------------------------------------------------------
# Invariance.most_agreed(): no winner drawn from list order on a tie
# ---------------------------------------------------------------------------


def _bare_invariance(decisions: dict[str, list]) -> Invariance:
    """An `Invariance` built by hand, for `agreement_rate`/`most_agreed`
    alone -- no fork, no run, just the decisions dict those two methods
    read."""
    return Invariance(renderers=list(decisions), days=1,
                      decisions=decisions, presentation=[], floor=None)


def test_most_agreed_is_none_when_two_renderers_fully_agree():
    """Round 2, finding 3: two renderers whose decisions match at every
    step tie at a 1.0 agreement rate, and `most_agreed()` must not report
    one of them as the winner -- there is nothing to prefer between two
    renderings the agent read identically."""
    decisions = {"a": [{"actions": []}, {"actions": []}],
                "b": [{"actions": []}, {"actions": []}]}
    report = _bare_invariance(decisions)
    assert report.agreement_rate() == {"a": 1.0, "b": 1.0}
    assert report.most_agreed() is None
    assert "agreed with" not in report.render()


def test_most_agreed_is_none_on_a_three_way_tie():
    decisions = {"a": [{"actions": []}], "b": [{"actions": []}],
                "c": [{"actions": [{"symbol": "X"}]}]}
    report = _bare_invariance(decisions)
    # a and b agree with each other and with nothing else; c agrees with
    # neither. Every pairwise rate a renderer participates in is either
    # 1.0 (a-b) or 0.0 (a-c, b-c), so a and b each average to 0.5 and tie.
    rates = report.agreement_rate()
    assert rates["a"] == rates["b"] == 0.5
    assert report.most_agreed() is None


def test_most_agreed_names_a_strict_winner():
    decisions = {"a": [{"actions": []}, {"actions": []}, {"actions": []}],
                "b": [{"actions": []}, {"actions": []},
                     {"actions": [{"symbol": "X"}]}],
                "c": [{"actions": [{"symbol": "X"}]},
                     {"actions": [{"symbol": "X"}]},
                     {"actions": [{"symbol": "X"}]}]}
    report = _bare_invariance(decisions)
    # a matches b at 2 of 3 steps and c at 0 of 3: rate 1/3.
    # b matches a at 2 of 3 and c at 1 of 3: rate 1/2.
    # c matches a at 0 of 3 and b at 1 of 3: rate 1/6.
    rates = report.agreement_rate()
    assert rates["b"] > rates["a"] > rates["c"]
    assert report.most_agreed() == "b"
    assert "agreed with 'b' most often" in report.render()
