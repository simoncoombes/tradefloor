"""Sweep `MARKET_FACTOR_SIGMA`, measure the realism panel at each value.

The cross-sectional correlation gap (design findings 7-9) traces to one
constant, and the temptation is to bump it and move on. This script exists
so the value is a measurement instead: it patches the constant in
`rust/src/market/tick.rs`, rebuilds the release wheel, reinstalls it,
measures the full realism panel across seeds in a fresh interpreter, and
restores the source when done. The output is the sigma -> statistics
relationship as data, which is what makes the calibration re-runnable —
in particular after any change that re-orders RNG draws, which invalidates
every trajectory and therefore this calibration with it.

Two failure modes are guarded against structurally, because both fail
silently and produce plausible numbers:

  - **Measuring a stale wheel.** Every measurement process first prints a
    trajectory fingerprint (a hash over the closes of a small fixed run).
    Distinct sigmas must produce distinct trajectories, so a fingerprint
    seen before under a different sigma means the rebuild or reinstall
    did not take, and the sweep aborts instead of recording the previous
    constant under a new label.
  - **Leaving the tree patched.** The original `tick.rs` bytes are restored
    in a `finally:`, and the wheel is rebuilt from the restored source so
    the installed package matches the tree again.

Run from the repository root, with the venv built per the release recipe:

    .venv/bin/python tools/calibration/sweep_market_factor_sigma.py \
        --sigmas 0.003,0.005,0.0075,0.010,0.0125,0.015,0.018 \
        --out tools/calibration/results/market-factor-sigma-<date>.json

Budget roughly a minute per sigma value for the six-seed default: a rebuild
is ~20s and the panel ~40s. The table printed at the end is a summary;
the JSON holds every per-seed figure.
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
TICK_RS = REPO / "rust" / "src" / "market" / "tick.rs"
CONSTANT = re.compile(
    r"(pub const MARKET_FACTOR_SIGMA: f64 = )([0-9eE.+-]+)(;)"
)

# The statistics the summary table prints, in print order, with the medians
# taken across seeds. The JSON output carries the complete per-seed panels;
# this list only chooses what fits on a terminal.
TABLE = [
    ("cross_sectional_corr", "corr"),
    ("annualised_vol_pct", "vol%(pooled)"),
    ("per_instrument_vol_median_pct", "vol%(median)"),
    ("excess_kurtosis", "kurtosis"),
    ("abs_return_acf1", "|r| acf1"),
    ("return_acf1", "r acf1"),
    ("volume_abs_return_corr", "vol/|r|"),
    ("leverage_effect", "leverage"),
    ("volume_change_acf1", "dV acf1"),
]


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("::", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, check=True, **kwargs)


def patch_sigma(source: str, sigma: float) -> str:
    """Return `source` with the constant set to `sigma`, or die.

    Exactly one occurrence must match. Zero means the constant moved or was
    renamed and the sweep would silently measure nothing; two would mean the
    pattern is no longer specific enough to know what it changed.
    """
    matches = CONSTANT.findall(source)
    if len(matches) != 1:
        raise SystemExit(
            f"expected exactly one MARKET_FACTOR_SIGMA definition in "
            f"{TICK_RS}, found {len(matches)}; the sweep does not know what "
            f"it would be patching"
        )
    return CONSTANT.sub(lambda m: f"{m.group(1)}{sigma!r}{m.group(3)}", source)


def build_and_install(venv: Path, work: Path, tag: str) -> None:
    """maturin build into a fresh per-sigma directory, install by exact path."""
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
    header = f"{'sigma':>8}" + "".join(f"{label:>14}" for _, label in TABLE)
    lines.append(header)
    for sigma, entry in sweep.items():
        row = f"{float(sigma):>8.4f}"
        for key, _ in TABLE:
            value = median_over_seeds(entry["panels"], key)
            row += f"{value:>14.3f}" if value is not None else f"{'n/a':>14}"
        lines.append(row)
    lines.append("")
    lines.append("cross-sectional correlation under pinned VIX (median over seeds):")
    any_entry = next(iter(sweep.values()))
    vix_levels = sorted(any_entry["pinned_vix"], key=float)
    lines.append(
        f"{'sigma':>8}" + "".join(f"{'vix ' + v:>14}" for v in vix_levels)
    )
    for sigma, entry in sweep.items():
        row = f"{float(sigma):>8.4f}"
        for vix in vix_levels:
            rows = entry["pinned_vix"][vix]
            corr = statistics.median(
                r["cross_sectional_corr"] for r in rows.values()
            )
            row += f"{corr:>14.3f}"
        lines.append(row)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--sigmas",
        default="0.003,0.005,0.0075,0.010,0.0125,0.015,0.018",
        help="comma-separated MARKET_FACTOR_SIGMA values to measure",
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
        help="results JSON path; default tools/calibration/results/<date>.json",
    )
    parser.add_argument(
        "--work-dir",
        default=None,
        help="scratch space for per-sigma wheels and panels; default a temp dir",
    )
    args = parser.parse_args()

    sigmas = [float(s) for s in args.sigmas.split(",") if s]
    venv = REPO / ".venv"
    python = venv / "bin" / "python"
    if not python.exists():
        raise SystemExit(
            f"{python} not found; create the venv per the build recipe first"
        )
    work = Path(args.work_dir) if args.work_dir else Path(
        tempfile.mkdtemp(prefix="sigma-sweep-")
    )
    work.mkdir(parents=True, exist_ok=True)

    out_path = Path(args.out) if args.out else (
        HERE / "results" /
        f"market-factor-sigma-{datetime.date.today().isoformat()}.json"
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

    original = TICK_RS.read_text(encoding="utf-8")
    seen: dict[str, float] = {}
    sweep: dict[str, dict] = {}
    try:
        for sigma in sigmas:
            tag = f"{sigma:.4f}".replace(".", "p")
            print(f"\n=== MARKET_FACTOR_SIGMA = {sigma} ===", flush=True)
            TICK_RS.write_text(patch_sigma(original, sigma), encoding="utf-8")
            build_and_install(venv, work, tag)

            print_ = fingerprint(python)
            if print_ in seen and seen[print_] != sigma:
                raise SystemExit(
                    f"STALE WHEEL: sigma={sigma} produced trajectory "
                    f"fingerprint {print_}, already seen for "
                    f"sigma={seen[print_]}. The rebuild or reinstall did not "
                    f"take; refusing to record one constant under two labels."
                )
            seen[print_] = sigma
            print(f"trajectory fingerprint {print_}", flush=True)

            entry = measure(python, args, work, tag)
            if entry["trajectory_fingerprint"] != print_:
                raise SystemExit(
                    f"wheel changed between fingerprint and measurement for "
                    f"sigma={sigma}: {print_} != {entry['trajectory_fingerprint']}"
                )
            sweep[repr(sigma)] = entry

            # Persist after every point, so an aborted sweep still reports
            # every value it actually measured.
            with open(out_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "constant": "MARKET_FACTOR_SIGMA (rust/src/market/tick.rs)",
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
        TICK_RS.write_text(original, encoding="utf-8")
        print("\nrestored tick.rs; rebuilding the wheel to match the tree",
              flush=True)
        build_and_install(venv, work, "restored")

    print(f"\nresults written to {out_path}\n")
    print(summarise(sweep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
