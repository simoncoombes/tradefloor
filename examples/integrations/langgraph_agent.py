"""A LangGraph graph trading a Tradefloor market, through the adapter.

Two things live here, and they share one definition of the market so they
cannot drift into describing different experiments:

1. **A runnable example.** ``python examples/integrations/langgraph_agent.py``
   runs a real compiled two-node graph against a five-day market. No
   network, no API key, seconds. The decision node is a plain Python rule.
2. **The rate-shock experiment** that ``langgraph_agent.ipynb`` narrates,
   where the decision node is a language model and the run is forked at a
   checkpoint to ask what the same agent does when one number changes. The
   notebook replays a recorded run by default; ``--record`` is what
   produced the recording, and it is the only path that spends money.

## The same graph, one node swapped

Both paths build the SAME two-node graph -- ``analyse`` then ``decide`` --
and differ only in which ``decide`` they wire in. That is the point worth
seeing: a LangGraph node is a function, so the difference between a rule
and a model is one edge, and everything around it -- the state schema, the
reducer, the adapter, the validation, the market -- is unchanged.

``analyse`` is deliberately NOT a model call. It reads the payload and
writes a short factual note: the policy rate, the funding headroom, which
names moved. Three reasons. It is one LLM call per decision instead of two.
It is deterministic, so it costs nothing to re-run. And because it is a
pure function of the payload, the notebook can recompute and DISPLAY its
output on the replay path, where the graph never runs at all -- an example
that cannot show its own middle step offline teaches half of itself.

## Sizing against the funding cap, not the participation cap

``decide`` applies BOTH limits the observation states, and the lesson is
that you cannot know in advance which one binds.

``max_order_shares`` is what the MARKET can absorb in one order. It is
fixed by average daily volume, so it does not move when the book does.
``buying_power`` is what the BOOK can fund before the leverage cap refuses
the trade. It scales with equity, so it shrinks as the portfolio loses and
grows as it gains.

Which is smaller is therefore a property of the run, not a rule. On the
$50m book this experiment uses, the participation cap across all four names
is worth about 0.6x equity and IT binds. On the $1m book an earlier draft
of this file used, the same cap was worth over thirty times equity, every
order was refused at the leverage limit, and the run scored zero trades
while looking like it had traded. Four independent agents made that
mistake, which is why ``buying_power`` is in the payload at all.

An agent that reads one limit is right until the book changes size. The
crossover is computable and the notebook computes it.

## The file is not called langgraph.py, and must not be renamed to it

A script's own directory is ``sys.path[0]``, so a file named
``langgraph.py`` here would shadow the installed ``langgraph`` package for
this very process, and the example could not import the framework it
demonstrates. The ``_agent`` suffix is load-bearing, not decoration.
Verified by building such a file and watching the local module win the
import. The library module ``tradefloor/integrations/langgraph.py`` is safe
from this, because it is only ever imported as part of a package and never
sits on ``sys.path[0]``.

Run it:

    python examples/integrations/langgraph_agent.py
    TRADEFLOOR_LIVE_EXAMPLES=1 python examples/integrations/langgraph_agent.py --record

The second one CALLS A MODEL AND SPENDS MONEY, sixty times. It needs the
opt-in above as well as ``ANTHROPIC_API_KEY``, because a credential sitting
in the environment is not consent to spend it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Annotated, Any, TypedDict

import tradefloor as tf
from tradefloor.counterfactual import World
from tradefloor.integrations import common as ci
from tradefloor.integrations.langgraph import INSTRUCTIONS, LangGraphAdapter

# -- the experiment ----------------------------------------------------------

#: The simulation seed. Fixed, and the whole market descends from it.
SEED = 4242

#: The flat example's length. The forked experiment uses the two below.
DAYS = 5

#: Days before the fork, and days each arm runs after it. Twenty is not
#: arbitrary: ``return_5d`` is None until five days have been observed and
#: the adapter's price memory holds thirty steps, so a short warm-up asks
#: the agent to decide with no history and measures the warm-up rather than
#: the agent.
WARMUP_DAYS = 20
BRANCH_DAYS = 20
STEPS_PER_DAY = 6

#: How often the graph is asked, in steps. Six is once a simulated day. The
#: market still advances every step; a portfolio manager does not re-decide
#: six times a day. Identical in both arms, which ``fork`` guarantees.
DECISION_EVERY = STEPS_PER_DAY

#: The book the agent runs. Large enough that its orders are a real
#: fraction of daily volume, so execution costs something.
CASH = 50_000_000.0

#: The macro regime both worlds share until the intervention.
POLICY_RATE = 0.04
DISCOUNT_RATE = 0.055
BASE_PINS = {"federal_funds_rate": POLICY_RATE,
             "corporate_bond_yield": DISCOUNT_RATE}

#: The intervention. A parallel 200bp shift: the policy rate the agent
#: reads and the corporate bond yield equities are discounted off, moving
#: together. Both, and not the policy rate alone, because of how this model
#: transmits -- ``federal_funds_rate`` reaches a valuation only by steering
#: the corporate bond yield, which is recomputed at central-bank meetings,
#: the first scheduled past the end of this run.
SHOCK_BPS = 200
SHOCKED_POLICY_RATE = POLICY_RATE + SHOCK_BPS / 10_000
SHOCKED_DISCOUNT_RATE = DISCOUNT_RATE + SHOCK_BPS / 10_000

#: Four companies, written down rather than drawn, and deliberately the
#: same roster the FinRobot study uses
#: (``examples/integrations/finrobot/rate_shock.py``). Same seed, same
#: names, same shock: the two notebooks then differ in the framework and
#: nothing else, which is what makes them worth reading side by side.
#:
#: They differ in the one property that decides rate sensitivity in this
#: model: revenue growth is the duration term in
#: ``1 - (discount - neutral) * 1.5 * (1 + growth * 2)``.
#:
#: ticker, sector, what it is, price, shares, eps, book value, revenue
#: growth, average daily volume, beta, short interest.
ROSTER = [
    ("NOVA", "technology", "long-duration growth", 112.0, 4.0e8, 3.60, 12.0,
     0.35, 6.0e6, 1.35, 9.0e6),
    ("HELX", "healthcare", "mid-duration growth", 88.0, 5.0e8, 3.80, 22.0,
     0.18, 4.5e6, 1.05, 5.0e6),
    ("BRDG", "industrials", "short-duration cyclical", 70.0, 7.0e8, 4.20,
     31.0, 0.06, 5.0e6, 0.95, 4.0e6),
    ("STAP", "consumer_staples", "minimal-duration defensive", 51.0, 9.0e8,
     2.60, 17.0, 0.01, 4.0e6, 0.60, 3.0e6),
]

#: Public company facts handed to the graph beside the market data.
#: Supplied here instead of read off the engine, so the adapter stays
#: auditable about what it exposes. An analyst reads all five off a filing.
#: The valuation they feed stays out: that is the simulator's answer key.
FUNDAMENTALS = {
    row[0]: {"sector": row[1], "eps": row[5], "book_value_per_share": row[6],
             "revenue_growth": row[7], "beta": row[9]}
    for row in ROSTER
}

#: Longest duration first. The order every table uses, because the claim is
#: that the response is monotone in it and an alphabetical table hides it.
BY_DURATION = sorted(ROSTER, key=lambda row: row[7], reverse=True)

#: The standing objective, appended to every observation in both arms. It
#: says what the agent is FOR and nothing about what is about to happen.
OBJECTIVE = ("Manage the portfolio for attractive risk-adjusted returns "
             "while controlling downside risk.")


def _repo_root() -> Path:
    """The repository root, found by marker rather than by counting parents.

    ``parents[2]`` was correct at this file's previous depth and would have
    broken silently when ``examples/`` gained a level -- the exact defect
    the FinRobot example carried through the same move. A marker survives
    any future rearrangement or fails loudly, and those are the only two
    acceptable outcomes.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("no pyproject.toml above this file; is the "
                       "repository layout intact?")


#: The recorded run. In ``tests/`` rather than beside this file because the
#: automated tests replay the same interactions, and two copies of a
#: recording are two recordings that can drift apart.
FIXTURE = _repo_root() / "tests" / "fixtures" / "langgraph" / "rate-shock.json"

LIVE_MODEL = os.environ.get("TRADEFLOOR_LANGGRAPH_MODEL", "claude-opus-5")
LIVE_KEY_VAR = "ANTHROPIC_API_KEY"
LIVE_MAX_TOKENS = 2000

#: The explicit opt-in for live model calls, spelled like
#: TRADEFLOOR_SLOW_TESTS and required IN ADDITION to the key and the
#: framework. See :func:`can_run_live` for why a key alone is not enough.
LIVE_OPT_IN_VAR = "TRADEFLOOR_LIVE_EXAMPLES"

#: The packages a live graph needs. Named once so the check and the message
#: cannot list different things.
LIVE_PACKAGES = ("langgraph", "langchain_anthropic")


def have_live_key() -> bool:
    return bool(os.environ.get(LIVE_KEY_VAR))


def have_live_packages() -> bool:
    """The imports a live graph needs, checked without instantiating one.

    ``find_spec`` is wrapped because it does not merely return None when an
    import is unavailable: it RAISES whatever a ``sys.meta_path`` finder
    raises. This repository's own suite installs such a finder --
    ``test_replay_needs_neither_the_framework_nor_a_key`` blocks these very
    names to prove the replay path never imports them -- so an unguarded
    check would turn "LangGraph is absent" into a crash in the one test
    written to confirm it can be absent.
    """
    import importlib.util as finder

    for name in LIVE_PACKAGES:
        try:
            if finder.find_spec(name) is None:
                return False
        except (ImportError, ValueError):
            return False
    return True


def can_run_live() -> bool:
    """True only when a live run was ASKED FOR and is possible.

    Three conditions, and the opt-in comes first:
    ``TRADEFLOOR_LIVE_EXAMPLES=1`` in the environment, then the API key,
    then the framework. A credential existing in the environment is not
    consent to spend it: without the opt-in, a developer with a key
    exported who ran the slow suite would re-execute every live notebook
    and spend real money, having asked for neither -- and the
    replay-identity cell would then honestly read False against the
    committed recording, so the surprise bill would arrive dressed as a
    test failure. Replay is the default even when live is possible, because
    a recorded run is the point of these examples, and spending money
    should be a thing somebody typed rather than a thing their shell had
    set.
    """
    return (bool(os.environ.get(LIVE_OPT_IN_VAR))
            and have_live_key()
            and have_live_packages())


def live_requirements() -> str:
    """What live is still missing, in the order the gate checks it.

    The notebook asks the module rather than spelling the list out, so that
    changing what live requires is one edit here and not a stale sentence
    in a committed cell nobody re-runs.
    """
    missing = []
    if not os.environ.get(LIVE_OPT_IN_VAR):
        missing.append(f"{LIVE_OPT_IN_VAR}=1")
    if not have_live_key():
        missing.append(LIVE_KEY_VAR)
    if not have_live_packages():
        missing.append(" and ".join(LIVE_PACKAGES) + " installed")
    return ", ".join(missing) if missing else "nothing; live is available"


def live_call_count() -> tuple[int, int, int]:
    """``(shared, per_arm, total)`` model calls a live run of this experiment
    makes.

    Stated because a reader looking at a FORK cannot guess the arithmetic.
    The shared history is decided once and both arms inherit it; each arm
    then decides for itself, so the total is the shared days plus the
    branch days TWICE, not the length of the run.

    One call per decision, not two, because ``analyse`` is a plain function
    -- see the module docstring.
    """
    shared = WARMUP_DAYS
    per_arm = BRANCH_DAYS
    return shared, per_arm, shared + 2 * per_arm


def universe() -> list:
    return list(tf.Universe([
        tf.Instrument(ticker, sector, initial_price=price,
                      shares_outstanding=shares, eps=eps,
                      book_value_per_share=book, revenue_growth=growth,
                      avg_volume=volume, beta=beta, short_interest=short)
        for (ticker, sector, _what, price, shares, eps, book, growth,
             volume, beta, short) in ROSTER
    ]))


# -- the graph ---------------------------------------------------------------


class TradeState(TypedDict):
    """The graph's own state schema, which the adapter does not dictate.

    ``observation`` is where the adapter's default input builder puts the
    serialized payload. ``notes`` carries a reducer, so the two nodes'
    partial updates are appended rather than overwriting each other.
    ``decision`` is where this graph writes its answer, and it is one of the
    keys the adapter's default output parser looks for.

    The default input also sends a ``messages`` key, for graphs built
    around a chat model that want the observation as text. This schema does
    not declare it, so LangGraph drops it before any node runs. That is not
    a workaround; it is what lets one default input serve both kinds of
    graph, and this file is the half that ignores it.
    """

    observation: dict
    notes: Annotated[list, lambda a, b: a + b]
    decision: Any


def analyse(state: TradeState) -> dict[str, Any]:
    """Read the observation and write down what is worth acting on.

    A plain function, no model. See the module docstring for why: it makes
    the graph one LLM call per decision instead of two, and it makes the
    middle step of the graph reproducible offline, so the notebook can show
    it while replaying a recording.
    """
    payload = state["observation"]
    macro = payload["macro"]
    book = payload["portfolio"]
    moved = sorted((a for a in payload["assets"] if a["return_5d"] is not None),
                   key=lambda a: a["return_5d"])
    lines = [
        f"policy rate {macro['federal_funds_rate']:.2%}, "
        f"corporate bond yield {macro['corporate_bond_yield']:.2%}",
        f"equity {book['net_worth']:,.0f}, gross {book['gross_exposure']:.2f}x "
        f"of {book['max_leverage']}x, buying power "
        f"{(book['buying_power'] or 0):,.0f}",
    ]
    if moved:
        lines.append("five-day moves: " + ", ".join(
            f"{a['symbol']} {a['return_5d']:+.1%}" for a in moved))
    return {"notes": lines}


def _affordable(asset: dict, payload: dict, fraction: float) -> float:
    """Shares of ``asset`` worth ``fraction`` of the funding headroom.

    Both caps applied, which is the point. ``max_order_shares`` is what the
    market absorbs; ``buying_power`` is what the book can fund; the smaller
    binds. Sizing on the first alone is the refused-every-order failure the
    module docstring describes.
    """
    power = payload["portfolio"]["buying_power"]
    if power is None:                       # no leverage cap configured
        return asset["max_order_shares"]
    return min(asset["max_order_shares"], fraction * power / asset["price"])


def decide_by_rule(state: TradeState) -> dict[str, Any]:
    """The offline decision node: buy what fell, trim what ran.

    The whole strategy, so the runnable example needs no key. Deliberately
    simple -- the example is about the wiring, not the alpha.
    """
    payload = state["observation"]
    actions = []
    for asset in payload["assets"]:
        move = asset["return_5d"]
        if move is None:                    # not yet observable; hold
            continue
        if move < -0.02:
            quantity = _affordable(asset, payload, 0.25)
            if quantity >= 1:
                actions.append({"symbol": asset["symbol"], "side": "BUY",
                                "quantity": quantity})
        elif move > 0.02 and asset["position"] > 0:
            actions.append({"symbol": asset["symbol"], "side": "SELL",
                            "quantity": asset["position"]})
    return {"decision": {"actions": actions,
                         "rationale": "five-day mean reversion, sized against "
                                      "buying power"},
            "notes": [f"{len(actions)} actions"]}


def build_graph(decide=decide_by_rule):
    """The compiled graph. LangGraph is imported here, not at module scope.

    ``StateGraph`` is a BUILDER and has no ``invoke``; ``compile()`` is what
    produces the runnable ``CompiledStateGraph`` the adapter takes. Passing
    the builder is the easy mistake, and the adapter refuses it by name.
    """
    # This import is why the file is called `langgraph_agent.py` and not
    # `langgraph.py`: a script's directory is `sys.path[0]`, so a sibling of
    # that name would shadow the installed package and this line would
    # import the example itself. See the module docstring before renaming.
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(TradeState)
    builder.add_node("analyse", analyse)
    builder.add_node("decide", decide)
    builder.add_edge(START, "analyse")
    builder.add_edge("analyse", "decide")
    builder.add_edge("decide", END)
    return builder.compile()


# -- the live decision node --------------------------------------------------


def live_prompt(payload: dict, notes: list) -> str:
    """Exactly what the model is asked, built from the payload and the notes.

    The decision contract comes from the adapter's own ``INSTRUCTIONS``
    rather than being restated here, so there is one statement of what a
    decision looks like and the example cannot drift from the library.

    Built from the PAYLOAD, never from the Observation. The Observation
    carries ``.engine``, which knows fair value and the factor attribution;
    the payload is the allowlisted view, and rendering from it is what keeps
    the ground-truth boundary checkable.
    """
    return "\n\n".join([
        INSTRUCTIONS,
        f"STANDING OBJECTIVE\n{OBJECTIVE}",
        "ANALYST NOTES\n" + "\n".join(f"- {line}" for line in notes),
        "OBSERVATION\n" + json.dumps(payload, indent=2, sort_keys=True),
    ])


def decide_by_model(model):
    """A decision node backed by a chat model.

    Returns the model's raw text under ``decision``. The node does no
    parsing at all: the adapter's output parser unwraps the key, and
    ``common.parse_decision`` validates the text, tolerating the code
    fences and closing sentences models add whatever the instructions say.
    One validator, and the example does not grow a second one.
    """
    def decide(state: TradeState) -> dict[str, Any]:
        prompt = live_prompt(state["observation"], state["notes"])
        reply = model.invoke(prompt)
        text = reply.content if isinstance(reply.content, str) else "\n".join(
            block.get("text", "") for block in reply.content
            if isinstance(block, dict))
        return {"decision": text, "notes": ["model answered"]}

    return decide


def live_model(model_name: str = LIVE_MODEL):
    """The chat model behind ``decide``. Imported here, never at module scope.

    No ``temperature``. Claude Opus 5 refuses the parameter outright --
    ``400 invalid_request_error: temperature is deprecated for this model``
    -- so passing the 0.0 that a reproducibility-minded caller reaches for
    fails the run rather than pinning it. The recording is what makes this
    experiment reproducible; a sampling parameter never was.
    """
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=model_name, max_tokens=LIVE_MAX_TOKENS)


def live_agent(recorder: ci.Transcript | None = None, arm: str = "shared",
               model_name: str = LIVE_MODEL) -> LangGraphAdapter:
    graph = build_graph(decide=decide_by_model(live_model(model_name)))
    return LangGraphAdapter(graph, mode="live", recorder=recorder,
                            fundamentals=FUNDAMENTALS,
                            instructions=INSTRUCTIONS,
                            every=DECISION_EVERY, arm=arm)


def replay_agent(transcript: ci.Transcript, arm: str = "shared",
                 ) -> LangGraphAdapter:
    """A replaying adapter, which needs no graph and no LangGraph install."""
    return LangGraphAdapter(mode="replay", transcript=transcript,
                            fundamentals=FUNDAMENTALS,
                            instructions=INSTRUCTIONS,
                            every=DECISION_EVERY, arm=arm)


# -- the forked experiment ---------------------------------------------------


def run_experiment(agent: LangGraphAdapter):
    """Shared history, checkpoint, fork, one intervention, both arms.

    Returns ``(world, mark, control, shock)``. The agent is forked with the
    world, so both arms run the same graph, the same cadence and the same
    instructions -- ``LangGraphAdapter.fork`` guarantees that rather than
    leaving it to the caller.
    """
    world = World(seed=SEED, universe=universe(), agent=agent, cash=CASH,
                  pins=BASE_PINS)
    world.run(days=WARMUP_DAYS)
    mark = world.checkpoint(f"before the +{SHOCK_BPS}bps shock")

    control, shock = world.fork("control", f"+{SHOCK_BPS}bps")
    # `fork` copies the agent, which copies `arm` -- it cannot know which
    # branch it became. Naming them here is what makes the recorded
    # transcript readable afterwards: without it every entry says "shared"
    # and the file cannot be filtered by branch.
    control.agent.arm, shock.agent.arm = "control", f"+{SHOCK_BPS}bps"
    shock.intervene(federal_funds_rate=SHOCKED_POLICY_RATE,
                    corporate_bond_yield=SHOCKED_DISCOUNT_RATE)
    control.run(days=BRANCH_DAYS)
    shock.run(days=BRANCH_DAYS)
    return world, mark, control, shock


def record(out: Path = FIXTURE, model_name: str = LIVE_MODEL) -> Path:
    """Run the experiment LIVE and write the transcript both arms share.

    One file, because ``fork`` shares the recorder between the arms: the
    shared history is recorded once and each branch adds its own decisions,
    keyed by the exact input that produced them. Replaying it reproduces
    all three phases with no graph and no network.
    """
    if not can_run_live():
        sys.exit(f"--record calls the model and needs {live_requirements()}.")

    recorder = ci.Transcript()
    agent = live_agent(recorder=recorder, model_name=model_name)
    recorder.meta.update(agent.provenance())
    recorder.meta.update({"provider": "anthropic", "model": model_name,
                          "mode": "live",
                          "entry_point": "langgraph.graph.StateGraph",
                          "framework_url":
                              "https://github.com/langchain-ai/langgraph",
                          "seed": SEED, "warmup_days": WARMUP_DAYS,
                          "branch_days": BRANCH_DAYS,
                          "shock_bps": SHOCK_BPS})

    shared, per_arm, total = live_call_count()
    print(f"recording live against {model_name}")
    print(f"  {shared} shared days + {per_arm} x 2 branch days "
          f"= {total} model calls")
    _world, _mark, control, shock = run_experiment(agent)
    print(f"  control {len(control.agent.record)} decisions, "
          f"shock {len(shock.agent.record)} decisions")
    print(f"  {len(recorder)} interactions recorded")

    out.parent.mkdir(parents=True, exist_ok=True)
    recorder.save(out)
    print(f"wrote {out}")
    return out


# -- what the notebook reads -------------------------------------------------
#
# Named without a leading underscore on purpose: a notebook is expected to
# call these, so marking them private would be saying the opposite of what
# is true. They are the reason the notebook holds narrative and not
# machinery -- one definition, so the prose and the script cannot describe
# two different experiments.


def label(row) -> str:
    """``NOVA (long-duration)``. The full descriptor in ROSTER is prose for
    the setup block; a table column needs one that fits."""
    return f"{row[0]} ({row[2].split()[0]})"


def analyst_notes(payload: dict) -> list[str]:
    """What the ``analyse`` node wrote for this payload.

    The reason that node is not a model call: this is a pure function of the
    payload, so the notebook can show the graph's middle step while
    REPLAYING, where the graph itself never runs.
    """
    return analyse({"observation": payload})["notes"]


def weights(world: World) -> dict[str, float]:
    """Final portfolio weight per name, as a fraction of net worth."""
    last = world.trace[-1]
    tickers = list(world.engine.tickers)
    return {t: (last["positions"].get(t, 0.0)
                * last["prices"][tickers.index(t)]) / last["net_worth"]
            for t in tickers}


def sides(world: World) -> dict[str, int]:
    """BUY, SELL and HOLD instructions issued AFTER the fork.

    The behavioural question in its most direct form: did the agent do
    different things, not did it say different words.
    """
    counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
    for entry in world.agent.record:
        if world.fork_step is not None and entry["step"] < world.fork_step:
            continue
        for action in entry["decision"]["actions"]:
            counts[action["side"]] += 1
    return counts


def action_divergence(control: World, shock: World) -> int | None:
    """The first step at which the ACTIONS differ, ignoring the wording.

    Separate from the library's ``Divergence.decision``, which compares the
    whole recorded decision including the rationale. Both numbers earn
    their place: a changed rationale says the agent read the world
    differently, changed actions say it traded on that reading, and
    reporting only the first lets a change of mind that never reached the
    market read as a change of behaviour.
    """
    by_step = {e["step"]: e for e in shock.agent.record}
    for entry in control.agent.record:
        other = by_step.get(entry["step"])
        if other is not None and (entry["decision"]["actions"]
                                  != other["decision"]["actions"]):
            return entry["step"]
    return None


def where(step: int | None) -> str:
    return "never" if step is None else f"step {step:<5} (day {step // STEPS_PER_DAY})"


def first_after_fork(world: World) -> dict | None:
    fork = world.fork_step or 0
    return next((e for e in world.agent.record if e["step"] >= fork), None)


def divergence_story(control: World, shock: World) -> str:
    """The two decisions side by side at the first post-fork decision point.

    One agent, one book, two worlds that were byte-identical a step
    earlier, and what it said in each. The clearest single output of the
    run, and the one a reader should look at before any table.
    """
    a, b = first_after_fork(control), first_after_fork(shock)
    if a is None or b is None:
        return "  no decision was taken after the fork."

    def block(name: str, entry: dict, rate: float) -> list[str]:
        lines = [f"  {name}  (policy rate {rate:.2%})", ""]
        actions = entry["decision"]["actions"]
        if not actions:
            lines.append("    no change")
        for action in actions:
            lines.append(f"    HOLD {action['symbol']}"
                         if action["side"] == "HOLD" else
                         f"    {action['side']} {action['quantity']:,.0f} "
                         f"{action['symbol']}")
        rationale = entry["decision"]["rationale"]
        if rationale:
            lines += [""] + _wrap(rationale, "    ")
        return lines

    return "\n".join(block("CONTROL", a, POLICY_RATE) + ["", "-" * 68, ""]
                     + block(f"+{SHOCK_BPS}BPS", b, SHOCKED_POLICY_RATE))


def _wrap(text: str, indent: str, width: int = 64) -> list[str]:
    import textwrap

    return [indent + line for line in textwrap.wrap(text, width)]


def table(rows, columns):
    """A DataFrame when pandas is around, an aligned block when it is not.

    The library has no dataframe dependency and this notebook does not add
    one; a reader without pandas still sees the numbers.
    """
    try:
        import pandas as pd
    except ImportError:
        widths = [max(len(str(r[i])) for r in [columns] + rows)
                  for i in range(len(columns))]
        for row in [columns, ["-" * w for w in widths]] + rows:
            print("  ".join(str(c).ljust(w) if i == 0 else str(c).rjust(w)
                            for i, (c, w) in enumerate(zip(row, widths))))
        return None
    return pd.DataFrame(rows, columns=columns).set_index(columns[0])


# -- the runnable example ----------------------------------------------------


def main() -> dict:
    """The five-day offline example. No key, no network, seconds."""
    agent = LangGraphAdapter(build_graph(), fundamentals=FUNDAMENTALS)
    scores = tf.evaluate({"langgraph": agent}, seed=SEED,
                         universe=universe(), days=DAYS, cash=CASH)
    card = scores["langgraph"]

    print(f"seed {SEED}, {DAYS} days, {len(ROSTER)} instruments")
    print(f"framework          {agent.info.reference()}")
    print(f"decisions          {len(agent.record)}")
    print(f"trades             {card.trades}")
    print(f"turnover           {card.turnover:,.0f}")
    print(f"pnl                {card.pnl:+,.0f}")
    print(f"return             {card.return_pct:+.2f}%")
    print(f"impact             {card.impact_bps:+.2f} bps")
    for entry in agent.record:
        acted = ", ".join(f"{a['side']} {a['quantity']:,.0f} {a['symbol']}"
                          for a in entry["decision"]["actions"]) or "hold"
        print(f"  day {entry['day']}  {acted}")

    # The structural gates, asserted rather than eyeballed: the agent was
    # consulted once per day, nothing it sent was refused by the market, and
    # every error column is empty. A demo that can rot silently is a demo
    # that teaches whatever it has rotted into.
    assert len(agent.record) == DAYS, agent.record
    assert not card.errors, card.errors
    assert card.rejected == 0, card.rejected

    return {"scorecard": card.as_dict(), "record": agent.record}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--record", action="store_true",
                        help="run the forked experiment LIVE and write the "
                             "transcript fixture (needs an API key)")
    parser.add_argument("--out", type=Path, default=FIXTURE)
    parser.add_argument("--model", default=LIVE_MODEL)
    args = parser.parse_args()
    if args.record:
        record(out=args.out, model_name=args.model)
    else:
        main()
