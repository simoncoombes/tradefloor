"""Does the agent actually react to macro conditions? Fork the world and find out.

Run it:

    python examples/rate-shock/counterfactual.py

One market. One agent. Twenty days of shared history. Then a checkpoint, a
fork into two identical worlds, and one changed variable: interest rates rise
200 basis points in one of them and not in the other. Both worlds run twenty
more days with the same agent, and the demo reports where they came apart.

    MARKET -> AGENT -> CHECKPOINT -> FORK -> +200bps -> COMPARE

The point is not the P&L. The point is that everything except the rate is
provably identical at the moment of the intervention, so everything that
happens afterwards descends from it. No historical dataset can offer that,
because history ran once.

`main` below is the experiment and nothing else; every `_show` is narration,
and deleting all of them would leave the design intact and readable. The agent
is in `agent.py`, and is a parameter -- replace that import and no other
line here changes. `examples/finrobot/` is that swap, made: the same
experiment with a real FinRobot agent in place of this one.

Everything reported is ground truth about THIS market. It is a controlled
synthetic experiment, not a prediction about how real securities would react
to a real rate rise.

Takes about two seconds. No API keys, no network, no data files.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import tradefloor as tf
from tradefloor.counterfactual import World, agree, compare

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import MacroAwareAgent  # noqa: E402

# ---------------------------------------------------------------------------
# The experiment, as constants. Everything that decides what runs is here.
# ---------------------------------------------------------------------------

#: The simulation seed. Fixed, and the whole market descends from it.
SEED = 4242

#: Days before the fork, and days each arm runs after it. Six decision steps a
#: day and 65 ticks a step, which is the library's own evaluation cadence.
WARMUP_DAYS = 20
BRANCH_DAYS = 20
STEPS_PER_DAY = 6
TICKS_PER_STEP = 65

#: The book the agent runs. Large enough that its orders are a real fraction
#: of daily volume, so execution costs something.
CASH = 50_000_000.0

#: The macro regime both worlds share until the intervention. Pinned rather
#: than left endogenous so that "the rate before the shock" is a number the
#: demo can state, and so that the control is genuinely a control: both arms
#: run under the same pinned regime and differ only in its level.
POLICY_RATE = 0.04
DISCOUNT_RATE = 0.055
BASE_PINS = {"federal_funds_rate": POLICY_RATE,
             "corporate_bond_yield": DISCOUNT_RATE}

#: The intervention. A parallel 200bp shift: the policy rate the agent watches
#: and the corporate bond yield equities are discounted off, moving together.
#:
#: Both, and not the policy rate alone, because of how this model transmits.
#: `federal_funds_rate` reaches a valuation ONLY by steering the corporate
#: bond yield, which is recomputed at central-bank meetings -- the first
#: scheduled 45 days out, past the end of this run. A hike to the policy rate
#: by itself would move the agent and not the market, and the demo would be
#: showing half a mechanism. `tests/test_macro_transmission.py` pins that map.
SHOCK_BPS = 200
SHOCKED_POLICY_RATE = POLICY_RATE + SHOCK_BPS / 10_000
SHOCKED_DISCOUNT_RATE = DISCOUNT_RATE + SHOCK_BPS / 10_000

#: Four companies, written down rather than drawn, so the roster is pinned
#: exactly and the story is legible. They differ in the one property that
#: decides rate sensitivity in this model: revenue growth is the duration term
#: in `1 - (discount - neutral) * 1.5 * (1 + growth * 2)`.
#:
#: Opening prices sit near each company's fair value at the starting discount
#: rate, so the market does not begin with a large mispricing correction that
#: would swamp the intervention.
#:
#: ticker, sector, what it is, price, shares, eps, book value, revenue growth,
#: average daily volume, beta, short interest.
ROSTER = [
    ("NOVA", "technology", "long-duration growth", 112.0, 4.0e8, 3.60, 12.0,
     0.35, 6.0e6, 1.35, 9.0e6),
    ("HELX", "healthcare", "mid-duration growth", 88.0, 5.0e8, 3.80, 22.0,
     0.18, 4.5e6, 1.05, 5.0e6),
    ("BRDG", "industrials", "short-duration cyclical", 70.0, 7.0e8, 4.20, 31.0,
     0.06, 5.0e6, 0.95, 4.0e6),
    ("STAP", "consumer_staples", "minimal-duration defensive", 51.0, 9.0e8,
     2.60, 17.0, 0.01, 4.0e6, 0.60, 3.0e6),
]

#: What the agent believes about each company's growth, which is the same
#: number the valuation uses as duration. Public information a real analyst
#: reads off a filing; it is handed to the agent because an Observation
#: carries prices and positions, not fundamentals.
DURATION = {row[0]: row[7] for row in ROSTER}

#: Longest duration first. The order every table below uses, because the claim
#: is that the response is monotone in it and an alphabetical table hides that.
BY_DURATION = sorted(ROSTER, key=lambda row: row[7], reverse=True)

DEFAULT_OUT = Path(__file__).resolve().parent / "artifacts"

AGENT_REFERENCE = "examples/rate-shock/agent.py::MacroAwareAgent"


def universe() -> tf.Universe:
    return tf.Universe([
        tf.Instrument(ticker, sector, initial_price=price,
                      shares_outstanding=shares, eps=eps,
                      book_value_per_share=book, revenue_growth=growth,
                      avg_volume=volume, beta=beta, short_interest=short)
        for (ticker, sector, _label, price, shares, eps, book, growth,
             volume, beta, short) in ROSTER
    ])


# ---------------------------------------------------------------------------
# The experiment
# ---------------------------------------------------------------------------

def main(out: Path = DEFAULT_OUT, chart: bool = True) -> dict:
    started = time.time()

    roster = universe()
    agent = MacroAwareAgent(duration=DURATION)
    _show_market(roster)
    _show_agent(agent)

    world = World(seed=SEED, universe=list(roster), agent=agent,
                  pins=BASE_PINS, cash=CASH, steps_per_day=STEPS_PER_DAY,
                  ticks_per_step=TICKS_PER_STEP, label="shared history")
    world.run(days=WARMUP_DAYS)
    _show_history(world)

    mark = world.checkpoint(label="before the rate shock")
    _show_checkpoint(mark)

    control, shock = world.fork("control", f"+{SHOCK_BPS}bps")
    identical = agree(control, shock)
    _show_fork(identical)

    shock.intervene(federal_funds_rate=SHOCKED_POLICY_RATE,
                    corporate_bond_yield=SHOCKED_DISCOUNT_RATE)
    _show_intervention(shock)

    control.run(days=BRANCH_DAYS)
    shock.run(days=BRANCH_DAYS)
    report = compare(control, shock, agreement=identical)

    _show_market_divergence(control, shock)
    _show_first_divergence(report, control, shock)
    _show_results(report)
    _show_conclusion(report)

    written = _save(out, world, mark, control, shock, report, identical,
                    roster, chart=chart)
    _show_artifacts(out, written)

    print(f"\nfinished in {time.time() - started:.1f}s")
    return {
        "comparison": report.as_dict(),
        "artifacts": [str(path) for path in written],
        "checkpoint_step": world.step,
        "seconds": time.time() - started,
    }


# ---------------------------------------------------------------------------
# Narration. Nothing below decides anything.
# ---------------------------------------------------------------------------

def rule(text: str) -> None:
    print(f"\n{text}\n" + "-" * 72)


def _show_market(roster: tf.Universe) -> None:
    rule("1  A MARKET")
    print(f"  seed {SEED}   {len(roster)} instruments   "
          f"{STEPS_PER_DAY} decisions a day   ${CASH:,.0f} of capital")
    print(f"  universe fingerprint  {roster.fingerprint}")
    print(f"  model preset          {tf.ModelParams.from_preset().fingerprint}"
          f"   (tradefloor {tf.__version__})")
    print()
    print(f"  {'':6}{'sector':<22}{'growth':>8}{'price':>9}"
          f"{'fair value':>12}   what it is")
    for (ticker, sector, label, price, _shares, eps, book, growth,
         *_rest) in BY_DURATION:
        value = tf.fair_value(eps=eps, sector=sector, revenue_growth=growth,
                              corporate_bond_yield=DISCOUNT_RATE,
                              book_value_per_share=book)
        print(f"  {ticker:<6}{sector:<22}{growth:>8.2f}{price:>9.2f}"
              f"{value.fair_value:>12.2f}   {label}")
    print()
    print("  Rate sensitivity here is 1 - (discount - neutral) x 1.5 x")
    print("  (1 + growth x 2), so revenue growth IS the duration term, and")
    print("  the same 200bp shift costs NOVA the most and STAP the least.")


def _show_agent(agent: MacroAwareAgent) -> None:
    rule("2  ONE AGENT, WHICH WILL EVENTUALLY MEET TWO VERSIONS OF THE WORLD")
    for line in agent.policy().splitlines():
        print(f"  {line}")
    print()
    print("  Deterministic, no RNG, no LLM. It reads the policy rate off the")
    print("  engine's macro state; it never reads fair value or attribution.")


def _show_history(world: World) -> None:
    rule(f"3  {WARMUP_DAYS} DAYS OF SHARED HISTORY")
    summary = world.summary()
    print(f"  market created                 {len(world.universe)} "
          "instruments")
    print(f"  agent ran                      {world.step} decision steps")
    print(f"  orders filled                  {summary['trades']}")
    print(f"  days completed                 {world.day}")
    print()
    print(f"  portfolio      ${world.net_worth():,.0f}")
    print(f"  cash           ${world.portfolio.cash:,.0f}")
    print(f"  gross exposure {summary['exposure']:.2f}x")
    print(f"  policy rate    {POLICY_RATE:.2%}")
    print(f"  discount rate  {DISCOUNT_RATE:.3%}")
    print(f"  market digest  {world.digest()}")


def _show_checkpoint(mark: tf.Checkpoint) -> None:
    rule("4  CHECKPOINT, IMMEDIATELY BEFORE THE INTERVENTION")
    print(f"  {mark!r}")
    print(f"  {len(mark.to_json()):,} bytes of JSON: the seed, the roster and")
    print("  every input that reached the engine. Replayable without this")
    print("  process, this script, or this machine.")


def _show_fork(identical) -> None:
    rule("5  FORK: TWO COPIES OF EXACTLY THE SAME WORLD")
    print(identical.render())
    print()
    if not identical.identical:
        raise SystemExit(
            f"the two branches did not start identical: "
            f"{identical.differences}. Everything after this would be "
            "measuring two different markets, so the demo stops here.")
    print(f"  Verified, not asserted: {len(identical.checks)} checks, every "
          "one read")
    print("  back off the two worlds. These are two copies of the same")
    print("  simulated world, and nothing below takes that on trust.")


def _show_intervention(shock: World) -> None:
    rule(f"6  THE INTERVENTION: +{SHOCK_BPS}BPS, IN ONE BRANCH ONLY")
    applied = shock.interventions[0]
    print(f"  {'':<22}{'control':>14}{'rate shock':>14}")
    print(f"  {'policy rate':<22}{POLICY_RATE:>13.2%}"
          f"{SHOCKED_POLICY_RATE:>14.2%}")
    print(f"  {'discount rate':<22}{DISCOUNT_RATE:>13.3%}"
          f"{SHOCKED_DISCOUNT_RATE:>14.3%}")
    print(f"  {'everything else':<22}{'identical':>14}{'identical':>14}")
    print()
    print(f"  Applied on day {applied['day']}, step {applied['step']}, "
          "through pin_macro, which")
    print("  the engine records in its own order log. The intervention")
    print("  travels inside the checkpoint and the manifest, not only here.")


def _show_market_divergence(control: World, shock: World) -> None:
    rule(f"7  BOTH WORLDS RUN {BRANCH_DAYS} MORE DAYS, SAME AGENT IN EACH")
    order = [control.engine.tickers.index(row[0]) for row in BY_DURATION]
    print("  " + " " * 22 + "".join(f"{row[0]:>11}" for row in BY_DURATION))
    for arm in (control, shock):
        prices = arm.trace[-1]["prices"]
        print(f"  {arm.label:<14}{arm.day:>3} days"
              + "".join(f"{prices[i]:>11.2f}" for i in order))
    a, b = control.trace[-1]["prices"], shock.trace[-1]["prices"]
    print(f"  {'difference':<22}"
          + "".join(f"{b[i] / a[i] - 1:>11.2%}" for i in order))
    print()
    print("  The market itself moved, most in the longest-duration name. Part")
    print("  of that is the repricing and part is the agent's own smaller")
    print("  footprint; tf.tca.analyse separates them and this demo does not.")


def _show_first_divergence(report, control: World, shock: World) -> None:
    rule("8  FIRST DIVERGENCE")
    print(report.divergence.render())
    print()
    step = report.divergence.decision
    if step is None:
        print("  The agent never changed a decision. For this policy that")
        print("  would be a bug, not a finding.")
        return
    before = control.trace[step]["decision"]
    after = shock.trace[step]["decision"]
    print(f"  At step {step} the agent's target gross exposure went "
          f"{before['gross']:.2f}x in")
    print(f"  the control and {after['gross']:.2f}x under the shock, and it "
          "did not cut evenly:")
    print()
    print(f"    {'':<6}{'growth':>8}{'control':>10}{'shock':>9}{'cut':>9}")
    for ticker, _s, _l, _p, _sh, _e, _b, growth, *_rest in BY_DURATION:
        was, now = before["weights"][ticker], after["weights"][ticker]
        print(f"    {ticker:<6}{growth:>8.2f}{was:>10.4f}{now:>9.4f}"
              f"{now / was - 1:>9.1%}")


def _show_results(report) -> None:
    rule("9  RESULTS")
    print(report.render())
    print()
    print(f"  {'final holdings':<24}{'control':>16}{'rate shock':>16}")
    for row in BY_DURATION:
        held = report.control["positions"].get(row[0], 0.0)
        shocked = report.treatment["positions"].get(row[0], 0.0)
        print(f"  {row[0]:<24}{held:>16,.0f}{shocked:>16,.0f}")


def _show_conclusion(report) -> None:
    d = report.divergence
    control, shock = report.control, report.treatment
    longest = BY_DURATION[0][0]
    held = control["positions"].get(longest, 0.0)
    gap = (1.0 - shock["positions"].get(longest, 0.0) / held) if held else 0.0

    rule("COUNTERFACTUAL RESULT")
    for line in (
        "Same market history.  Same agent.  Same portfolio.",
        "Same order book.  Same execution state.  Same seed.",
        "",
        f"One intervention: interest rates +{SHOCK_BPS}bps, at step "
        f"{d.intervention_step} of {control['steps']}.",
        "",
        f"The agent's target changed at step {d.decision}. It sent a "
        "different order at",
        f"step {d.orders}, and the two portfolios were worth different "
        "amounts from",
        f"step {d.portfolio} onward. It finished at {shock['exposure']:.2f}x "
        "gross against the",
        f"control's {control['exposure']:.2f}x, holding {gap:.0%} less of "
        f"{longest}, the longest-duration",
        f"name, and {shock['pnl_since'] - control['pnl_since']:+,.0f} dollars "
        "apart in P&L.",
        "",
        "Tradefloor isolated the effect of the intervention inside this",
        "simulated market. Nothing else could have caused it, because",
        "nothing else differed.",
        "",
        "Ground truth about this market, not about any real one.",
    ):
        print(f"  {line}" if line else "")


def _show_artifacts(out: Path, written: list[Path]) -> None:
    rule("ARTIFACTS")
    for path in written:
        print(f"  {path.name:<20}{path.stat().st_size:>12,} bytes")
    print(f"\n  written to {out}")


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

def _save(out: Path, world: World, mark, control: World, shock: World,
          report, agreement, roster, *, chart: bool) -> list[Path]:
    """Write the experiment where it can be inspected after the fact.

    Tradefloor's own artifact types where it has them -- a Checkpoint and a
    RunManifest per arm, all three self-verifying -- plus one experiment
    document that ties them together with the design, the intervention and
    the divergence.
    """
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def write(name: str, text: str) -> None:
        path = out / name
        path.write_text(text, encoding="utf-8")
        written.append(path)

    write("checkpoint.json", mark.to_json())
    write("control.json", control.manifest(strategy=AGENT_REFERENCE).to_json())
    write("rate_shock.json",
          shock.manifest(strategy=AGENT_REFERENCE).to_json())
    write("comparison.json", json.dumps(report.as_dict(), indent=2,
                                        sort_keys=True))
    write("manifest.json", json.dumps({
        "experiment": "rate shock counterfactual",
        "question": "How does the same trading agent behave when interest "
                    "rates unexpectedly rise by 200 basis points?",
        "written_by": {"tradefloor": tf.__version__,
                       "model": tf.ModelParams.from_preset().fingerprint},
        "design": {
            "seed": SEED,
            "universe_fingerprint": roster.fingerprint,
            "instruments": [
                {"ticker": t, "sector": s, "duration_label": label,
                 "revenue_growth": g}
                for (t, s, label, _p, _sh, _e, _b, g, *_r) in BY_DURATION],
            "cash": CASH,
            "warmup_days": WARMUP_DAYS,
            "branch_days": BRANCH_DAYS,
            "steps_per_day": STEPS_PER_DAY,
            "ticks_per_step": TICKS_PER_STEP,
            "base_pins": BASE_PINS,
            "agent": AGENT_REFERENCE,
        },
        "checkpoint": {"day": WARMUP_DAYS, "step": world.step,
                       "log_entries": len(mark),
                       "market_digest": world.digest()},
        "intervention": shock.interventions,
        "fork_agreement": agreement.as_dict(),
        "divergence": report.divergence.as_dict(),
        "arms": {"control": report.control, "rate_shock": report.treatment},
        "caveat": "A controlled synthetic experiment. Ground truth about this "
                  "simulated market, not a claim about real securities.",
    }, indent=2, sort_keys=True))

    if chart:
        path = _chart(out / "comparison.png", control, shock, report)
        if path is not None:
            written.append(path)
    return written


def _chart(path: Path, control: World, shock: World, report) -> Path | None:
    """Portfolio value and gross exposure, with the intervention marked.

    Optional. matplotlib is what the notebooks already chart with and is not
    a dependency of the library, so a missing one skips the picture and says
    so rather than failing a run whose whole result is in the terminal.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.ticker import FuncFormatter
    except ImportError:
        print("\n  (no chart: pip install matplotlib)")
        return None

    fork = report.divergence.intervention_step
    shared = slice(0, fork + 1)
    after = slice(fork, None)

    def series(world, field, window):
        rows = world.trace[window]
        return [row["step"] for row in rows], [row[field] for row in rows]

    fig, (top, bottom) = plt.subplots(
        2, 1, figsize=(9.5, 6.8), sharex=True,
        gridspec_kw={"height_ratios": [2, 1], "hspace": 0.12})

    SHARED, CONTROL, SHOCK = "#4a4e69", "#2a6f97", "#c1121f"

    for axis, field in ((top, "net_worth"), (bottom, "exposure")):
        # The shared history drawn ONCE, in one colour. Drawing both arms over
        # it would put one line exactly on top of the other and make the first
        # half of the picture look like it belonged to whichever was plotted
        # last -- the opposite of what it shows.
        axis.plot(*series(control, field, shared), color=SHARED,
                  linewidth=1.5, label="shared history",
                  solid_capstyle="round")
        axis.plot(*series(control, field, after), color=CONTROL,
                  linewidth=1.5, label=control.label)
        axis.plot(*series(shock, field, after), color=SHOCK,
                  linewidth=1.5, label=shock.label)
        axis.axvline(fork, color="#333333", linestyle="--", linewidth=1.0)
        axis.grid(alpha=0.2, linewidth=0.5)
        axis.spines[["top", "right"]].set_visible(False)
        axis.margins(x=0.01)

    top.annotate(f"checkpoint, fork, +{SHOCK_BPS}bps", xy=(fork, 1.0),
                 xycoords=("data", "axes fraction"),
                 xytext=(6, -11), textcoords="offset points",
                 fontsize=9, color="#333333", va="top")
    bottom.annotate("the agent de-risks here", xy=(fork, 0.0),
                    xycoords=("data", "axes fraction"),
                    xytext=(6, 8), textcoords="offset points",
                    fontsize=9, color="#333333", va="bottom")
    top.yaxis.set_major_formatter(
        FuncFormatter(lambda v, _pos: f"${v / 1e6:,.0f}m"))
    top.set_ylabel("portfolio value")
    top.set_title("Same market, same agent, one changed variable",
                  loc="left", fontsize=13, pad=34)
    # Above the axes, under the title. Inside them it sat on the shared
    # history, which is the one line the picture is built around.
    top.legend(frameon=False, fontsize=9, ncols=3, loc="lower left",
               bbox_to_anchor=(0.0, 1.01), borderaxespad=0.0)
    bottom.yaxis.set_major_formatter(
        FuncFormatter(lambda v, _pos: f"{v:.2f}x"))
    bottom.set_ylabel("gross exposure")
    bottom.set_xlabel("decision step  (6 a day)")
    bottom.set_ylim(0.0, 1.15)
    fig.text(0.012, 0.012,
             f"tradefloor {tf.__version__}   seed {SEED}   preset "
             f"{tf.ModelParams.from_preset().fingerprint}   "
             "a controlled synthetic experiment, not a claim about real "
             "markets", fontsize=7.5, color="#666666")
    # subplots_adjust rather than tight_layout: the legend sits OUTSIDE the
    # top axes, which tight_layout cannot measure and warns about.
    fig.subplots_adjust(left=0.105, right=0.985, top=0.865, bottom=0.115)
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="where to write the experiment artifacts")
    parser.add_argument("--no-chart", action="store_true",
                        help="skip the comparison chart")
    args = parser.parse_args()
    main(out=args.out, chart=not args.no_chart)
