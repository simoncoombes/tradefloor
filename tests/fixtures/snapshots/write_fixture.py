"""Write a state-snapshot fixture under whichever tradefloor is installed.

    python tests/fixtures/snapshots/write_fixture.py PRESET STOP OUT.json

STOP is ``close`` (three closed days) or ``midday`` (three closed days and
forty ticks of a fourth). The file carries the snapshot in the lossless form
``tests/snapshot_codec.py`` defines, the inputs that rebuild the engine, and
what the writing build reached when it ran on from the snapshot: the state
hash and the price column after the rest of the fourth day and one more.

Run once per release under a virtualenv holding that release from PyPI; the
fixtures in this directory were written that way.
"""

import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import tradefloor as tf  # noqa: E402
from snapshot_codec import encode  # noqa: E402

SEED = 11
TICKS = 78


def universe():
    sectors = ["technology", "financial_services", "energy", "healthcare"]
    return tf.Universe([
        tf.Instrument(f"FX{i}", sectors[i], initial_price=20.0 + 7.0 * i,
                      shares_outstanding=1.5e8 + 2.0e7 * i,
                      eps=1.1 + 0.4 * i, book_value_per_share=8.0 + i,
                      revenue_growth=0.01 + 0.02 * i,
                      avg_volume=300_000 + 100_000 * i, beta=0.8 + 0.15 * i)
        for i in range(4)
    ])


def main(preset: str, stop: str, out: str) -> None:
    roster = universe()
    engine = tf.Engine(seed=SEED, universe=roster,
                       model=tf.ModelParams.from_preset(preset))
    for day in range(3):
        engine.open_market()
        engine.run_session(9, 30, day % 5, TICKS)
        engine.close_market()
    if stop == "midday":
        engine.open_market()
        engine.run_session(9, 30, 3, 40)
    snapshot = engine.state_snapshot()
    if stop == "midday":
        engine.run_session(12, 50, 3, TICKS - 40)
        engine.close_market()
    engine.open_market()
    engine.run_session(9, 30, 4, TICKS)
    engine.close_market()
    doc = {
        "written_by": tf.__version__,
        "preset": preset,
        "seed": SEED,
        "stop": stop,
        "universe": json.loads(roster.to_json()),
        "snapshot": encode(snapshot),
        "continued": {
            "state_hash": engine.state_hash() if hasattr(engine, "state_hash") else None,
            "price_sha256": hashlib.sha256(engine.column("price")).hexdigest(),
        },
    }
    pathlib.Path(out).write_text(json.dumps(doc, sort_keys=True, indent=1) + "\n")


if __name__ == "__main__":
    main(*sys.argv[1:4])
