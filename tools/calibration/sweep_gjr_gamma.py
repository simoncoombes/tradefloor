"""Sweep the GJR asymmetry GAMMA, rebalancing ALPHA/BETA, panel at each point.

The leverage effect is structurally absent from the symmetric GARCH (design
finding 8; CALIBRATION.md §3.5): the return enters the variance update
squared, so its sign is destroyed. The GJR term in `rust/src/market/garch.rs`
restores a sign channel, and this script chooses its coefficient by
measurement instead of taste. Each sweep point is a (GAMMA, ALPHA, BETA)
triple, because GJR stationarity needs ALPHA + BETA + GAMMA/2 < 1 and the
shipped ALPHA + BETA = 0.99 leaves room for GAMMA < 0.02 -- far too small to
matter -- so any meaningful GAMMA must be paid for out of ALPHA or BETA. The
default grid holds effective persistence at 0.99 and pays two ways: out of
ALPHA (holding the average news-impact ALPHA + GAMMA/2 at the shipped 0.09)
and out of BETA (holding the up-day response at the shipped 0.09).

The two hard constraints the choice is made under, both binding by a hair at
the current era (post-rng-split baseline, `results/market-factor-sigma-
2026-08-21-post-rng-split.json`): median excess kurtosis is 3.11 against a
band floor of 3 with one seed already below it, and |r| acf(1) clustering is
0.124 against a band floor of 0.15. A GAMMA that buys leverage by pushing
either further out is a worse model, not a better one, and the per-seed
panels in the JSON are what let a reader check that, not just the medians.

Structure, guards and discipline are inherited from
`sweep_market_factor_sigma.py`:

  - **Stale-wheel guard.** Every point's measurement process first prints a
    trajectory fingerprint; distinct coefficient triples must produce
    distinct trajectories, so a repeated fingerprint under a different
    triple aborts the sweep rather than recording one build twice. (The
    zero-GAMMA baseline point legitimately reproduces the committed build's
    fingerprint -- the GJR term at zero is bit-identical by construction,
    which is separately proven -- but two DIFFERENT triples must never
    collide.)
  - **Restore in `finally:`**, and rebuild from the restored source so the
    installed wheel matches the tree again.
  - **Persist after every point**, and RESUME: re-running with the same
    `--out` skips points already measured, so the sweep can be driven in
    foreground chunks without losing or re-paying finished work.

Run from the repository root, with the venv built per the release recipe:

    .venv/bin/python tools/calibration/sweep_gjr_gamma.py \
        --points 0.00:0.09:0.90,0.08:0.05:0.90,0.08:0.09:0.86 \
        --out tools/calibration/results/gjr-gamma-<date>.json

Budget roughly 80s per point for the six-seed default: a rebuild is ~30s and
the panel ~50s. The table printed at the end is a summary; the JSON holds
every per-seed figure.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
GARCH_RS = REPO / "rust" / "src" / "market" / "garch.rs"

CONSTANTS = {
    "alpha": re.compile(r"(pub const ALPHA: f64 = )([0-9eE.+-]+)(;)"),
    "beta": re.compile(r"(pub const BETA: f64 = )([0-9eE.+-]+)(;)"),
    "gamma": re.compile(r"(pub const GAMMA: f64 = )([0-9eE.+-]+)(;)"),
}

# The statistics the summary table prints, medians across seeds. The JSON
# carries the complete per-seed panels; this list only chooses what fits on
# a terminal. Kurtosis additionally prints its per-seed MINIMUM, because the
# band floor of 3 is the binding constraint and a median that clears it while
# seeds sit under it is not "in band" in any sense a reader would accept.
TABLE = [
    ("leverage_effect", "leverage"),
    ("excess_kurtosis", "kurtosis"),
    ("abs_return_acf1", "|r| acf1"),
    ("annualised_vol_pct", "vol%(pooled)"),
    ("per_instrument_vol_median_pct", "vol%(median)"),
    ("cross_sectional_corr", "corr"),
    ("return_acf1", "r acf1"),
    ("volume_abs_return_corr", "vol/|r|"),
    ("volume_change_acf1", "dV acf1"),
]


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("::", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, check=True, **kwargs)


def patch_constants(source: str, alpha: float, beta: float, gamma: float) -> str:
    """Return `source` with the three coefficients set, or die.

    Each pattern must match exactly once. Zero means a constant moved or was
    renamed and the sweep would silently measure nothing; two would mean the
    pattern no longer knows what it is patching.
    """
    for name, value in (("alpha", alpha), ("beta", beta), ("gamma", gamma)):
        pattern = CONSTANTS[name]
        matches = pattern.findall(source)
        if len(matches) != 1:
            raise SystemExit(
                f"expected exactly one {name.upper()} definition in {GARCH_RS}, "
                f"found {len(matches)}; the sweep does not know what it would "
                f"be patching"
            )
        source = pattern.sub(lambda m: f"{m.group(1)}{value!r}{m.group(3)}", source)
    return source


def build_and_install(venv: Path, work: Path, tag: str) -> None:
    """maturin build into a fresh per-point directory, install by exact path."""
    dist = work / f"dist-{tag}"
    dist.mkdir(parents=True, exist_ok=True)
    for stale in dist.glob("*.whl"):
        stale.unlink()
    env = dict(os.environ, VIRTUAL_ENV=str(venv))
    run(["maturin", "build", "--release", "--out", str(dist)], cwd=REPO, env=env)
    wheels = sorted(dist.glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected exactly one wheel in {dist}, found {wheels}")
    run(
        ["uv", "pip", "install", "--force-reinstall", "--no-deps", str(wheels[0])],
        env=env,
    )


def measure(python: Path, args, work: Path, tag: str) -> dict:
    out = work / f"panel-{tag}.json"
    run(
        [
            str(python),
            str(HERE / "measure_panel.py"),
            "--seeds", args.seeds,
            "--days", str(args.days),
            "--universe-n", str(args.universe_n),
            "--universe-seed", str(args.universe_seed),
            "--pin-vix", args.pin_vix,
            "--pin-days", str(args.pin_days),
            "--out", str(out),
        ],
        cwd=REPO,
    )
    with open(out, encoding="utf-8") as handle:
        return json.load(handle)


def fingerprint(python: Path) -> str:
    result = run(
        [str(python), str(HERE / "measure_panel.py"), "--fingerprint-only"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def median_over_seeds(panels: dict, key: str) -> float | None:
    values = [p[key] for p in panels.values() if p.get(key) is not None]
    return statistics.median(values) if values else None


def summarise(sweep: dict) -> str:
    lines = []
    header = (
        f"{'gamma':>7}{'alpha':>7}{'beta':>7}{'pers.':>7}"
        + "".join(f"{label:>13}" for _, label in TABLE)
        + f"{'kurt(min)':>13}"
    )
    lines.append(header)
    for entry in sweep.values():
        c = entry["coefficients"]
        row = (
            f"{c['gamma']:>7.3f}{c['alpha']:>7.3f}{c['beta']:>7.3f}"
            f"{c['alpha'] + c['beta'] + c['gamma'] / 2.0:>7.3f}"
        )
        for key, _ in TABLE:
            value = median_over_seeds(entry["panels"], key)
            row += f"{value:>13.3f}" if value is not None else f"{'n/a':>13}"
        kurt_min = min(p["excess_kurtosis"] for p in entry["panels"].values())
        row += f"{kurt_min:>13.3f}"
        lines.append(row)
    lines.append("")
    lines.append("leverage effect per seed (band -0.30 to -0.10):")
    for label, entry in sweep.items():
        values = [
            entry["panels"][s]["leverage_effect"]
            for s in sorted(entry["panels"], key=int)
        ]
        lines.append(
            f"  {label:<22}" + "".join(f"{v:>9.3f}" for v in values)
        )
    lines.append("excess kurtosis per seed (band floor 3):")
    for label, entry in sweep.items():
        values = [
            entry["panels"][s]["excess_kurtosis"]
            for s in sorted(entry["panels"], key=int)
        ]
        lines.append(
            f"  {label:<22}" + "".join(f"{v:>9.3f}" for v in values)
        )
    return "\n".join(lines)


def parse_points(text: str) -> list[tuple[float, float, float]]:
    """Parse `gamma:alpha:beta` triples, refusing non-stationary ones."""
    points = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(":")
        if len(parts) != 3:
            raise SystemExit(
                f"point {chunk!r} is not gamma:alpha:beta"
            )
        gamma, alpha, beta = (float(p) for p in parts)
        persistence = alpha + beta + gamma / 2.0
        if not (0.0 <= persistence < 1.0):
            raise SystemExit(
                f"point {chunk!r} has persistence {persistence}; "
                f"ALPHA + BETA + GAMMA/2 must be in [0, 1) or the variance "
                f"process is non-stationary and the panel measures a runaway"
            )
        if gamma < 0.0 or alpha < 0.0 or beta < 0.0:
            raise SystemExit(f"point {chunk!r} has a negative coefficient")
        points.append((gamma, alpha, beta))
    return points


# Default grid: effective persistence held at the shipped 0.99 throughout.
# The alpha-paid arm holds the average news impact ALPHA + GAMMA/2 at the
# shipped 0.09 (so up-day response falls as the asymmetry rises); the
# beta-paid arm holds ALPHA at 0.09 and spends persistence's smooth
# component instead. GAMMA = 0.18 with ALPHA = 0 is the boundary where the
# up-day surprise response vanishes entirely.
DEFAULT_POINTS = (
    "0.00:0.09:0.90,"
    "0.04:0.07:0.90,"
    "0.08:0.05:0.90,"
    "0.12:0.03:0.90,"
    "0.16:0.01:0.90,"
    "0.18:0.00:0.90,"
    "0.08:0.09:0.86,"
    "0.12:0.09:0.84"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--points",
        default=DEFAULT_POINTS,
        help="comma-separated gamma:alpha:beta triples to measure",
    )
    parser.add_argument("--seeds", default="1,2,3,4,5,6")
    parser.add_argument("--days", type=int, default=252)
    parser.add_argument("--universe-n", type=int, default=40)
    parser.add_argument("--universe-seed", type=int, default=111)
    parser.add_argument("--pin-vix", default="15,45,65")
    parser.add_argument("--pin-days", type=int, default=120)
    parser.add_argument(
        "--out",
        default=None,
        help="results JSON path; default tools/calibration/results/<date>.json. "
        "An existing file is RESUMED: points already in it are skipped.",
    )
    parser.add_argument(
        "--work-dir",
        default=None,
        help="scratch space for per-point wheels and panels; default a temp dir",
    )
    args = parser.parse_args()

    points = parse_points(args.points)
    venv = REPO / ".venv"
    python = venv / "bin" / "python"
    if not python.exists():
        raise SystemExit(
            f"{python} not found; create the venv per the build recipe first"
        )
    work = Path(args.work_dir) if args.work_dir else Path(
        tempfile.mkdtemp(prefix="gjr-sweep-")
    )
    work.mkdir(parents=True, exist_ok=True)

    out_path = Path(args.out) if args.out else (
        HERE / "results" /
        f"gjr-gamma-{datetime.date.today().isoformat()}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    git_rev = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=REPO, capture_output=True, text=True,
    ).stdout.strip()
    # A sweep on a dirty tree is reproducible only from the tree, not the
    # rev, and the results file must not claim otherwise.
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO, capture_output=True, text=True,
    ).stdout.strip()
    if dirty:
        git_rev += "-dirty"

    # Resume: keep finished points and their fingerprints, so a chunked run
    # neither re-pays nor double-records them, and the stale-wheel guard
    # still sees every fingerprint the sweep has ever produced.
    sweep: dict[str, dict] = {}
    seen: dict[str, str] = {}
    if out_path.exists():
        with open(out_path, encoding="utf-8") as handle:
            existing = json.load(handle)
        if existing.get("git_rev") != git_rev:
            raise SystemExit(
                f"{out_path} was measured at {existing.get('git_rev')}, the "
                f"tree is now at {git_rev}; refusing to blend two builds in "
                f"one results file -- use a fresh --out"
            )
        sweep = existing["sweep"]
        for label, entry in sweep.items():
            seen[entry["trajectory_fingerprint"]] = label
        print(f"resuming {out_path}: {sorted(sweep)} already measured",
              flush=True)

    original = GARCH_RS.read_text(encoding="utf-8")
    try:
        for gamma, alpha, beta in points:
            label = f"g{gamma:.2f}-a{alpha:.2f}-b{beta:.2f}"
            if label in sweep:
                print(f"skipping {label}: already measured", flush=True)
                continue
            tag = label.replace(".", "p")
            print(f"\n=== GAMMA={gamma} ALPHA={alpha} BETA={beta} ===",
                  flush=True)
            GARCH_RS.write_text(
                patch_constants(original, alpha, beta, gamma), encoding="utf-8"
            )
            build_and_install(venv, work, tag)

            print_ = fingerprint(python)
            if print_ in seen and seen[print_] != label:
                raise SystemExit(
                    f"STALE WHEEL: {label} produced trajectory fingerprint "
                    f"{print_}, already seen for {seen[print_]}. The rebuild "
                    f"or reinstall did not take; refusing to record one build "
                    f"under two labels."
                )
            seen[print_] = label
            print(f"trajectory fingerprint {print_}", flush=True)

            entry = measure(python, args, work, tag)
            if entry["trajectory_fingerprint"] != print_:
                raise SystemExit(
                    f"wheel changed between fingerprint and measurement for "
                    f"{label}: {print_} != {entry['trajectory_fingerprint']}"
                )
            entry["coefficients"] = {
                "gamma": gamma, "alpha": alpha, "beta": beta,
            }
            sweep[label] = entry

            # Persist after every point, so an aborted sweep still reports
            # every value it actually measured.
            with open(out_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "constants":
                            "GAMMA, ALPHA, BETA (rust/src/market/garch.rs)",
                        "git_rev": git_rev,
                        "date": datetime.date.today().isoformat(),
                        "sweep": sweep,
                    },
                    handle,
                    indent=1,
                    sort_keys=True,
                )
                handle.write("\n")
    finally:
        GARCH_RS.write_text(original, encoding="utf-8")
        print("\nrestored garch.rs; rebuilding the wheel to match the tree",
              flush=True)
        build_and_install(venv, work, "restored")

    print(f"\nresults written to {out_path}\n")
    print(summarise(sweep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
