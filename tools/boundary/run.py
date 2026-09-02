"""Map a FinRobot agent's decision boundaries on the rate-shock study.

    python tools/boundary/run.py                  # replays the recording
    python tools/boundary/run.py --live           # calls FinRobot
    python tools/boundary/run.py --live --record artifacts/boundary.json
    python tools/boundary/run.py --transcript artifacts/boundary.json

Drives :func:`tradefloor.boundary.map_boundaries` with a FinRobot agent on
the world of ``examples/integrations/finrobot/rate_shock.py``: the same
roster, seed, pins, cash and cadence, so the shipped recording replays
and a live run is comparable to it. The provider is imported inside the
adapter on the first live call; a replay imports nothing beyond
``tradefloor``.

``--record`` writes every probe's exchange into one transcript, which
``--transcript`` replays later without a provider. The floor is not in
that transcript: ``resample`` asks the agent again through ``reask``,
which records nothing by contract, so a replayed map closes its brackets
and reports every floor as unmeasurable. The floor is a live measurement.

Output goes to ``--out``: ``boundary-map.json`` with every search, probe,
floor and manifest, ``boundary-map.parquet`` when ``pyarrow`` is
installed, and the rendered map on stdout.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError("no pyproject.toml above this file; where is the root?")


ROOT = _repo_root()
STUDY = ROOT / "examples" / "integrations" / "finrobot" / "rate_shock.py"
DEFAULT_OUT = Path(__file__).resolve().parent / "artifacts"


def _study():
    """The rate-shock study module, loaded by path.

    The study owns the experiment's constants, and this runner reuses them
    rather than copying them, so the world built here is the world the
    recording was made on.
    """
    spec = importlib.util.spec_from_file_location("rate_shock_study", STUDY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Map where a FinRobot agent's decision flips, one "
                    "intervention target at a time, on the rate-shock "
                    "study's market.")
    parser.add_argument("--live", action="store_true",
                        help="call FinRobot through the provider named by "
                             "the study; without it the recording replays")
    parser.add_argument("--transcript", type=Path, default=None,
                        help="recording to replay (default: the shipped "
                             "fixture)")
    parser.add_argument("--record", type=Path, default=None,
                        help="with --live, write every exchange to this "
                             "transcript")
    parser.add_argument("--prior", type=Path, default=None,
                        help="with --live and --record, resume from this "
                             "transcript before calling the provider")
    parser.add_argument("--scenarios", nargs="*", default=None,
                        help="shipped scenario names, and 'none' for the "
                             "world as it stands (default: none, then every "
                             "shipped scenario)")
    parser.add_argument("--targets", nargs="*", default=None,
                        help="intervention targets (default: every numeric "
                             "target)")
    parser.add_argument("--operation", default="multiply",
                        choices=("set", "add", "multiply"))
    parser.add_argument("--bracket", nargs=2, type=float, default=(0.5, 2.0),
                        metavar=("LOW", "HIGH"),
                        help="bracket in the operation's units "
                             "(default: 0.5 2.0)")
    parser.add_argument("--steps", type=int, default=8,
                        help="halvings per search (default: 8)")
    parser.add_argument("--warmup-days", type=int, default=None,
                        help="days of shared history before the map "
                             "(default: the study's)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="directory for the artifacts")
    return parser


def build_agent(study, args):
    """The FinRobot adapter, replaying or live. Nothing from FinRobot is
    imported here; the adapter imports it on the first live call."""
    from tradefloor.integrations.finrobot import FinRobotAdapter, Transcript

    common = dict(fundamentals=study.FUNDAMENTALS, objective=study.OBJECTIVE,
                  every=study.DECISION_EVERY, arm="shared")
    if not args.live:
        path = args.transcript or study.FIXTURE
        if not path.exists():
            sys.exit(f"no recording at {path}. Record one with --live "
                     "--record PATH, or point --transcript at one.")
        transcript = Transcript.load(path)
        return FinRobotAdapter(mode="replay", transcript=transcript,
                               **common), f"replay of {path}"
    recorder = Transcript() if args.record else None
    prior = Transcript.load(args.prior) if args.prior else None
    agent = FinRobotAdapter(mode="live", llm_config=study.llm_config(),
                            recorder=recorder, prior=prior, **common)
    return agent, f"live: {study.LIVE_API_TYPE} / {study.LIVE_MODEL}"


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.record and not args.live:
        sys.exit("--record needs --live: a replay has nothing new to record.")
    if args.prior and not args.record:
        sys.exit("--prior needs --record to resume into.")

    import tradefloor as tf
    from tradefloor.boundary import map_boundaries

    study = _study()
    agent, source = build_agent(study, args)
    warmup = (study.WARMUP_DAYS if args.warmup_days is None
              else args.warmup_days)
    world = tf.World(seed=study.SEED, universe=list(study.universe()),
                     agent=agent, pins=study.BASE_PINS, cash=study.CASH,
                     steps_per_day=study.STEPS_PER_DAY,
                     ticks_per_step=study.TICKS_PER_STEP, label="shared")
    print(f"agent      {source}")
    print(f"world      seed {study.SEED}, {len(world.universe)} instruments, "
          f"{warmup} days of shared history")
    world.run(days=warmup)

    scenarios = (["none"] + list(tf.Scenario.available())
                 if args.scenarios is None else args.scenarios)
    scenarios = [None if name == "none" else name for name in scenarios]
    atlas = map_boundaries(world, targets=args.targets, scenarios=scenarios,
                           operation=args.operation,
                           bracket=tuple(args.bracket), steps=args.steps)
    print(atlas.render())

    args.out.mkdir(parents=True, exist_ok=True)
    written = []
    path = args.out / "boundary-map.json"
    path.write_text(json.dumps(atlas.as_dict(), indent=2) + "\n",
                    encoding="utf-8")
    written.append(path)
    try:
        import pyarrow.parquet as pq
    except ImportError:
        pq = None
    if pq is not None:
        path = args.out / "boundary-map.parquet"
        pq.write_table(atlas.table(), path)
        written.append(path)
    recorder = getattr(agent, "recorder", None)
    if recorder is not None:
        recorder.save(args.record)
        written.append(args.record)
    print("artifacts")
    for path in written:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
