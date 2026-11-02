"""Record the study live. Every command here except `summarise` costs money.

    python record.py canonical          60 calls, the run the notebook replays
    python record.py replication N      140 calls, one complete replication
    python record.py resample           16 calls, eight re-asks per arm
    python record.py summarise          no calls: rebuilds data/ from the above

The notebook needs none of this. It replays the canonical run from
`tests/fixtures/finrobot/liquidity-crisis.json` and reads the other three
bodies of evidence from the summaries in `data/`. This is how those were
made, so a reader can make them again.

Live mode needs the FinRobot extra, which installs on Python 3.11 only, and
an Anthropic key in `ANTHROPIC_API_KEY`:

    pip install "tradefloor[finrobot,arrow]"

Each recording is saved after every call to `<out>.partial.json`, and a run
started again resumes from it: the market is deterministic, so a resumed run
reaches the same prompts and the answers already paid for are reused.

A replication forks six arms from one shared history: the study's two
(`control` and `crisis`) and the four treated arms of the rate-and-regime
decomposition, which shares `control`. Every arm of a fork is bit-identical
at the fork whatever the arm count, so one shared history serves both
comparisons. The replication transcripts are about 1.7 MB each and are not
committed; `data/` holds their summaries.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import experiment as ex  # noqa: E402
from tradefloor.counterfactual import World, agree  # noqa: E402
from tradefloor.integrations.finrobot import (  # noqa: E402
    DecisionError, FinRobotAdapter, Transcript, parse)

#: The model every recording in this study was made with. A dated snapshot,
#: so the provenance names the model that answered.
MODEL = "claude-sonnet-4-5-20250929"

#: Where replication transcripts and raw resample answers go. Under
#: `artifacts/`, which git ignores.
DEFAULT_OUT = HERE / "artifacts" / "recordings"

#: The six arms of one replication.
REPLICATION_ARMS: dict[str, str | dict | None] = {
    "control": None,
    "crisis": ex.SCENARIO_NAME,
    "+200bps": ex.QUIET,
    "vix": ex.VIX_ONLY,
    "cycle": ex.CYCLE_ONLY,
    "rate+regime": ex.LOUD,
}
STUDY_ARMS = ["control", "crisis"]
DECOMPOSITION_ARMS = ["control", "+200bps", "vix", "cycle", "rate+regime"]

#: Re-asks per arm at the fork step.
RESAMPLE_N = 8


class _SavingTranscript(Transcript):
    """A transcript that writes itself to disk after every entry."""

    __slots__ = ("_path",)

    def __init__(self, path: Path):
        super().__init__()
        self._path = path

    def record(self, entry):
        super().record(entry)
        self.save(self._path)


def llm_config() -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("recording needs ANTHROPIC_API_KEY in the environment")
    return {"config_list": [{"model": MODEL, "api_key": key,
                             "api_type": "anthropic"}],
            "temperature": 0.0, "cache_seed": None}


def _agent(**kwargs) -> FinRobotAdapter:
    small = ex.subset(ex.load_snapshot())
    return FinRobotAdapter(mode="live", llm_config=llm_config(),
                           fundamentals=ex.fundamentals(small),
                           objective=ex.OBJECTIVE, every=ex.DECISION_EVERY,
                           **kwargs)


def run(arms: dict, out: Path) -> dict:
    """One complete live run: twenty shared days, a fork, twenty per arm.

    The same construction the notebook replays, call for call, so the
    recording it writes replays there.
    """
    partial = out.with_suffix(".partial.json")
    prior = Transcript.load(partial) if partial.exists() else None
    recorder = _SavingTranscript(partial)
    agent = _agent(recorder=recorder, prior=prior, arm="shared")
    small = ex.subset(ex.load_snapshot())
    world = World(seed=ex.SEED, universe=ex.universe(small), agent=agent,
                  pins=ex.BASE_PINS, cash=ex.CASH,
                  steps_per_day=ex.STEPS_PER_DAY,
                  ticks_per_step=ex.TICKS_PER_STEP,
                  model=ex.PRESET, label="shared", on_refusal="skip")
    world.run(days=ex.WARMUP_DAYS)
    world.checkpoint("before the interventions")

    names = list(arms)
    worlds = dict(zip(names, world.fork(*names)))
    for label, arm in worlds.items():
        arm.agent.arm = label
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            found = agree(worlds[left], worlds[right])
            assert found.identical, found.differences
    for label, treatment in arms.items():
        print(f"{label:>12}  {ex.treat(worlds[label], treatment)}")
    for label, arm in worlds.items():
        arm.engine.settle_depth_counterfactual(True)
        arm.run(days=ex.BRANCH_DAYS)
        arm.engine.record(arm.day - 1)
        print(f"{label:>12}  done, {len(recorder)} interactions recorded")

    recorder.meta.update({
        "provider": "anthropic", "model": MODEL, "temperature": 0.0,
        "mode": "live",
        "recorded_utc": _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0).isoformat()})
    recorder.save(out)
    return worlds


def summarise_run(worlds: dict, names: list[str], index: int,
                  transcript: str) -> dict:
    """One row of `data/replications.json` or `data/decomposition.json`."""
    sub = {n: worlds[n] for n in names}
    series = ex.exposure_series(sub)
    bands = ex.bands(series, names)
    row: dict = {
        "index": index, "transcript": transcript,
        "mean_exposure": {n: bands[n]["mean_exposure"] for n in names},
        "risk_language": {n: bands[n]["risk_language"] for n in names},
        "decisions": len(series)}
    if len(names) == 2:
        order = ex.ordering(series, names)
        row.update({"ordering_strict": order["strict"],
                    "ordering_days": order["days"],
                    "ordering_failures": order["failures"],
                    "holds_on_means": bands[names[0]]["mean_exposure"]
                    > bands[names[1]]["mean_exposure"]})
    row["agent_change"] = {n: bands[n]["agent_change_total"] for n in names}
    row["decision_counts"] = {n: {
        "scheduled": bands[n]["decisions"],
        "valid": bands[n]["decisions"] - bands[n]["refused"],
        "refused": bands[n]["refused"]} for n in names}
    row["refusals"] = sum(bands[n]["refused"] for n in names)
    return row


def cmd_canonical(out: Path) -> None:
    run(dict(ex.ARMS), ex.FIXTURE)
    print(f"wrote {ex.FIXTURE}")


def cmd_replication(out: Path, index: int) -> None:
    path = out / f"run-{index:02d}.json"
    worlds = run(REPLICATION_ARMS, path)
    summary = {"two": summarise_run(worlds, STUDY_ARMS, index, path.name),
               "five": summarise_run(worlds, DECOMPOSITION_ARMS, index,
                                     path.name)}
    (out / f"run-{index:02d}.summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")


def _fork_entries() -> dict:
    transcript = Transcript.load(ex.FIXTURE)
    step = ex.WARMUP_DAYS * ex.STEPS_PER_DAY
    return {e["arm"]: e for e in transcript.entries if e["step"] == step}


def cmd_resample(out: Path) -> None:
    """Eight more answers to each arm's fork-step prompt, byte for byte."""
    entries = _fork_entries()
    agent = _agent()
    raw_path = out / "resample-raw.json"
    raw = (json.loads(raw_path.read_text(encoding="utf-8"))
           if raw_path.exists() else {})
    for arm in STUDY_ARMS:
        raw.setdefault(arm, [])
        while len(raw[arm]) < RESAMPLE_N:
            raw[arm].append(agent.reask(entries[arm]))
            raw_path.write_text(json.dumps(raw, indent=1), encoding="utf-8")
            print(f"resample {arm} {len(raw[arm])}/{RESAMPLE_N}")


def _resample_summary(out: Path) -> dict:
    entries = _fork_entries()
    raw = json.loads((out / "resample-raw.json").read_text(encoding="utf-8"))
    base = entries[ex.CONTROL_ARM]["prompt"].splitlines()
    stats, runs = {}, {}
    for arm in STUDY_ARMS:
        lines = entries[arm]["prompt"].splitlines()
        rows, refusals = [], 0
        for text in raw[arm][:RESAMPLE_N]:
            try:
                decision = parse(text)
            except DecisionError:
                refusals += 1
                continue
            # Signed share counts per name. A buy counts +1 toward `net`
            # and a sell -1; `gross` is the shares traded.
            orders: dict[str, float] = {}
            for action in decision.as_dict()["actions"]:
                side = str(action.get("side", "")).upper()
                if side not in ("BUY", "SELL"):
                    continue
                quantity = abs(float(action.get("quantity") or 0))
                orders[action["symbol"]] = orders.get(action["symbol"], 0.0) \
                    + (quantity if side == "BUY" else -quantity)
            net = (sum(1 for q in orders.values() if q > 0)
                   - sum(1 for q in orders.values() if q < 0))
            rows.append({"orders": orders, "net": net,
                         "gross": sum(abs(q) for q in orders.values())})
        nets = [r["net"] for r in rows]
        grosses = [r["gross"] for r in rows]
        stats[arm] = {
            "calls": len(raw[arm][:RESAMPLE_N]), "refusals": refusals,
            "distinct": len({json.dumps(r["orders"], sort_keys=True)
                             for r in rows}),
            "mean_net": statistics.mean(nets),
            "stdev_net": statistics.stdev(nets),
            "mean_gross": statistics.mean(grosses),
            "stdev_gross": statistics.stdev(grosses),
            "prompt_lines": len(lines),
            "differing_lines": sum(1 for a, b in zip(base, lines) if a != b)}
        runs[arm] = rows
    return {"n": RESAMPLE_N, "step": ex.WARMUP_DAYS * ex.STEPS_PER_DAY,
            "stats": stats, "runs": runs, "model": MODEL,
            "temperature": 0.0}


def _per_arm(rows: list[dict], names: list[str]) -> dict:
    out = {}
    for name in names:
        values = [r["mean_exposure"][name] for r in rows]
        out[name] = {"mean_of_means": statistics.mean(values),
                     "stdev_of_means": statistics.stdev(values),
                     "min": min(values), "max": max(values)}
    return out


def cmd_summarise(out: Path) -> None:
    """data/replications.json, data/decomposition.json and
    data/resample-fork.json, from what the other commands wrote."""
    summaries = sorted(out.glob("run-*.summary.json"))
    if not summaries:
        sys.exit(f"no replication summaries in {out}")
    loaded = [json.loads(p.read_text(encoding="utf-8")) for p in summaries]

    rows = [s["two"] for s in loaded]
    per = _per_arm(rows, STUDY_ARMS)
    gaps = [r["mean_exposure"]["control"] - r["mean_exposure"]["crisis"]
            for r in rows]
    spread = max(per[n]["stdev_of_means"] for n in STUDY_ARMS)
    replications = {
        "replications": len(rows),
        "ordering_holds_on_means": sum(r["holds_on_means"] for r in rows),
        "per_arm": per, "rows": rows,
        "gaps": {"control - crisis": {
            "mean_gap": statistics.mean(gaps), "min_gap": min(gaps),
            "spread": spread,
            "ratio": statistics.mean(gaps) / spread if spread else None,
            "positive_in": sum(1 for g in gaps if g > 0)}},
        "model": MODEL, "temperature": 0.0, "preset": ex.PRESET}

    rows5 = [s["five"] for s in loaded]
    per5 = _per_arm(rows5, DECOMPOSITION_ARMS)
    against = {}
    for name in DECOMPOSITION_ARMS[1:]:
        gap = per5["control"]["mean_of_means"] - per5[name]["mean_of_means"]
        noise = max(per5[name]["stdev_of_means"],
                    per5["control"]["stdev_of_means"])
        against[name] = {
            "gap": gap, "spread": noise,
            "ratio": gap / noise if noise else None,
            "lower_in": sum(1 for r in rows5 if r["mean_exposure"][name]
                            < r["mean_exposure"]["control"])}
    decomposition = {
        "replications": len(rows5),
        "arms": {n: REPLICATION_ARMS[n] or {} for n in DECOMPOSITION_ARMS},
        "per_arm": per5, "against_control": against, "rows": rows5,
        "model": MODEL, "temperature": 0.0, "preset": ex.PRESET}

    data = HERE / "data"
    for name, body in (("replications.json", replications),
                       ("decomposition.json", decomposition),
                       ("resample-fork.json", _resample_summary(out))):
        (data / name).write_text(json.dumps(body, indent=2) + "\n",
                                 encoding="utf-8")
        print(f"wrote {data / name}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["canonical", "replication",
                                            "resample", "summarise"])
    parser.add_argument("index", nargs="?", type=int)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.command == "replication":
        if args.index is None:
            parser.error("replication needs an index, such as 1")
        cmd_replication(args.out, args.index)
    else:
        {"canonical": cmd_canonical, "resample": cmd_resample,
         "summarise": cmd_summarise}[args.command](args.out)


if __name__ == "__main__":
    main()
