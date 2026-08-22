#!/usr/bin/env python3
"""Verify the golden vectors, or (historically) sync them from the reference.

The goldens are produced by the reference implementation's generators, which
import that implementation directly and therefore lived with it rather than
here. Since dd718e7 the corpus is committed at `goldens/` (D-G2), so the
live use of this script is `--verify`; the sync direction remains for the
machine that can still reach a reference checkout.

WHAT THE GOLDENS GATE -- narrowed 2026-08-21 (D-P1)
---------------------------------------------------

The goldens were once the whole parity contract: proof that this crate
reproduces the reference bit-for-bit. That claim is now deliberately
narrower, because this crate diverged from the reference on modelling
grounds and is the model of record (D-P2): the fork is not a defect and is
not ported back. Goldens gate the subsystems that remain faithful ports,
and that set is enumerable:

Still gated, bit-for-bit (measured on this corpus, 2026-08-21):
  - mathx-v8.json ............ V8 comparison report + determinism pin
  - fairvalue.json ........... fair value / target PE
  - marketmaker.json ......... quotes, ladders, inventory fills
  - orderbook.json ........... the matching program replay
  - microstructure.json ...... spreads, books, settlement + draw counts
  - mispricing-*.json (12) ... constants, step, apply, roots, trajectories
  - market-islands.json ...... sessions, curves, index maths (the GARCH
    cases and chains retired -- the GJR fork, below)
  - market-daily.json ........ close bookkeeping minus garchVariance: the
    momentum roll, lastDailyReturn, sPrevClose, the resets and the
    ReferenceEma avgVolume arm, through the chains' full 500 days (the
    garchVariance assertions retired -- the GJR fork, below)
  - economy-*.json ........... tier-1 islands; the stagflation and
    no-central-bank trajectories in full (their recorded VIX never
    exceeds CRISIS_VIX_THRESHOLD); the other three trajectories up to
    their crisis-fork day, via the fork gate in economy_parity.rs
  - sector anchors ........... the lib-level sectors gate
  - market-tick-closed-weekend.json, plus the draw-schedule arithmetic
    asserted across all market-tick-*.json (corpus self-checks)

Forked -- parity RETIRED, tests kept as runnable divergence records:
  - market-tick-*.json, all eight non-closed scenarios, and
    thirty-day-{calm,eventful}.json: the market-factor path.
    MARKET_FACTOR_SIGMA moved 0.003 -> 0.0075 (with its crash-amplifier
    normaliser denominated in it), so replaying reference draws through the
    new constant diverges at the first open tick. Retired under #[ignore]
    in market_tick_parity.rs / thirty_day_parity.rs; `cargo test --
    --ignored` reproduces the measured divergence, and those tests are
    EXPECTED to fail there.
  - divergence-reference.json / divergence-multiday.json: same fork, same
    cause (the examples and divergence_statistics measure it at mean 1.24%
    over a session); their disposition belongs to the examples' owners.
  - economy-trajectory-{active-shocks,calm-expansion,
    volatile-contraction}.json: the crisis-trigger fork, landed
    2026-08-21. CRISIS_VIX_THRESHOLD re-sited the gold/USD crisis gates
    from the reference's vix > 30 -- which no recorded trajectory ever
    crosses (hardest: 29.09; active-shocks OPENS at exactly 30.00 and
    the strict gate does not fire) -- to 25.5, where endogenous VIX
    actually goes. These three cross 25.5 and diverge on their first
    crossing day (0 / 306 / 16). Retired under #[ignore] in
    economy_parity.rs; `cargo test --test economy_parity -- --ignored`
    reproduces each divergence. Coverage replaced by the fork gate
    there: bit-parity strictly before the fork day, the draw schedule
    intact through it, and the divergence equal to the gate terms in
    exactly the two gated fields.
  - the GARCH cases and garchChains in market-islands.json, and the
    garchVariance assertions (cases and chains) in market-daily.json:
    the GJR asymmetry fork, landed 2026-08-21. garch.rs moved from the
    reference's symmetric GARCH(1,1) (alpha 0.09, beta 0.90) to GJR
    with gamma 0.34, alpha 0.02, beta 0.80, persistence held at 0.99.
    Everything failed, chains included -- the retune signature the
    market_islands_parity.rs forward-note's discriminator predicted (a
    pure asymmetry term would have spared the all-non-negative chains;
    that header carries the resolved measurement). Retired whole in
    market_islands_parity.rs (every assertion routes through the
    update) and as split-off #[ignore] tests in market_daily_parity.rs
    (the rest of the close stays live); `cargo test -- --ignored`
    reproduces the divergence: 84 mismatches in the islands cases, day
    0 of every chain except alternating-extremes at day 1 (its 0.9
    return clamps every parameterisation to the same ceiling), and
    19,980 of 43,200 daily cases (the rest pin to the clamp bounds).
    Coverage replaced by tests/garch_regression.rs and garch.rs's own
    property tests; correctness argued by the gamma sweep in
    tools/calibration/.

Expected to fork next (in-flight engine streams, noted so their failures
are read as the fork landing, not as port regressions): the market-factor
variance process in tick.rs lands on an already-retired surface; the
crisis CORRELATION trigger in tick.rs (vix > 40, still at the reference
level, expected to adopt CRISIS_VIX_THRESHOLD when its stream lands) also
lands on the already-retired market-tick surface. When a suite goes red,
retire the affected cases the same way -- reasoned header, #[ignore] with
the cause, coverage replaced by a gate that says what it actually gates --
rather than deleting or widening.

Regression coverage for retired surfaces lives in
tests/tick_regression.rs and tests/garch_regression.rs, each labelled for
what it is: self-anchored, gating regression and reproducibility, never
correctness.

Two rules this script enforces, both learned the hard way:

1. **Verify against index.json's SHA-256 manifest, always.** A partially
   copied or truncated vector set is otherwise a silent pass -- the tests read
   fewer cases and report success. Copying 135 MB by hand and trusting it is
   exactly how that happens.

2. **Never sync in the other direction.** The goldens come FROM the reference
   implementation. Regenerating them from this crate would make the surviving
   parity gates self-confirming -- asserting the crate agrees with itself,
   passing forever regardless of correctness -- and would destroy the retired
   suites' value as records of the measured divergence. D-P1 also forecloses
   regenerating from a constants-patched reference: the fork is not ported
   back, in either direction.

Usage:
    python sync-goldens.py --verify   # the live use: verify what is here
    PRETIUM_GOLDENS_SRC=/path/to/reference/goldens python sync-goldens.py
    python sync-goldens.py --from /path/to/goldens
"""

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEST = HERE / "goldens"


def _source() -> Path | None:
    """Where the reference goldens live. No default: this is deliberate.

    Guessing a sibling checkout would bake one machine's layout into the tool
    and, worse, would name the reference implementation in a repository that
    does not otherwise mention it.
    """
    if "--from" in sys.argv:
        return Path(sys.argv[sys.argv.index("--from") + 1]).expanduser().resolve()
    env = os.environ.get("PRETIUM_GOLDENS_SRC")
    return Path(env).expanduser().resolve() if env else None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(root: Path) -> int:
    index = root / "index.json"
    if not index.exists():
        print(f"  no index.json in {root} - cannot verify", file=sys.stderr)
        return 1

    manifest = json.loads(index.read_text(encoding="utf-8"))
    files = manifest.get("files", [])
    if not files:
        print("  index.json lists no files - refusing to report success",
              file=sys.stderr)
        return 1

    bad = 0
    for entry in files:
        target = root / entry["file"]
        if not target.exists():
            print(f"  MISSING  {entry['file']}")
            bad += 1
            continue
        actual = sha256(target)
        if actual != entry["sha256"]:
            print(f"  CORRUPT  {entry['file']}")
            print(f"           expected {entry['sha256'][:16]}...")
            print(f"           actual   {actual[:16]}...")
            bad += 1

    total = len(files)
    if bad:
        print(f"\n  {bad} of {total} golden files failed verification.")
        return 1
    print(f"  {total}/{total} golden files verified against index.json.")
    return 0


def main() -> int:
    if "--verify" in sys.argv:
        return verify(DEST)

    source = _source()
    if source is None:
        print("  no source given.", file=sys.stderr)
        print("  Set PRETIUM_GOLDENS_SRC, or pass --from <path>.", file=sys.stderr)
        return 1
    if not source.exists():
        print(f"  source not found: {source}", file=sys.stderr)
        return 1

    print(f"  copying goldens\n    from {source}\n    to   {DEST}")
    if DEST.exists():
        shutil.rmtree(DEST)
    shutil.copytree(source, DEST)
    return verify(DEST)


if __name__ == "__main__":
    raise SystemExit(main())
