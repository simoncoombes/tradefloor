"""Sweep the market-factor variance process, measure the whole panel at each point.

The 2026-08 sigma sweep (`sweep_market_factor_sigma.py`) proved the
cross-sectional correlation band unreachable by a constant-sigma Gaussian
factor: correlation is bought with kurtosis at a fixed rate (finding 14).
This sweep calibrates the escape named there -- the factor's own GARCH(1,1)
conditional volatility (`rust/src/market/factor_vol.rs`), funded by
`IDIO_SIGMA_SCALE` reallocating variance out of the per-name idiosyncratic
noise so total volatility holds still.

A point here is a VECTOR, not a scalar:

    sigma  MARKET_FACTOR_SIGMA          tick.rs      baseline factor sigma
    idio   IDIO_SIGMA_SCALE             factor_vol   the funding side
    alpha  MARKET_VOL_ALPHA             factor_vol   surprise weight
    beta   MARKET_VOL_BETA              factor_vol   persistence weight
    vix    MARKET_VOL_VIX_COUPLING      factor_vol   0 autonomous, 1 VIX-driven
    ceil   MARKET_VOL_CEILING_MULTIPLE  factor_vol   variance ceiling

Omitted keys keep the committed value, so a one-axis sweep reads as one.
Points are given as JSON: `--points '[{"sigma":0.014,"idio":0.85}, ...]'`.

Everything else is inherited from the sigma sweep deliberately, because its
guards exist for measured reasons -- with ONE amendment this sweep's first
run forced:

  - **The stale-build guard moved from trajectories to binaries.** The
    sigma sweep's guard -- every point's 2-day trajectory fingerprint must
    be distinct -- assumed every constant is visible in any trajectory.
    The variance ceiling broke the assumption on this sweep's first run:
    `ceil=5` and `ceil=8` are bit-identical over the fingerprint's two days
    because the factor variance cannot REACH either ceiling that fast, and
    the guard aborted a healthy sweep. Trajectory equality was always a
    proxy for "the installed binary is a rebuild of the patched source",
    so this sweep checks that directly instead: (a) after patching, each
    file is re-read and must contain the requested value; (b) the compiled
    core in the built wheel must hash-equal the installed one; (c) two
    points' installed cores must never hash-equal each other -- distinct
    f64 immediates cannot compile to identical binaries, so a collision
    means the rebuild did not take.
  - **The measurement-consistency fingerprint stays.** The panel
    measurement must reproduce the trajectory fingerprint taken just
    before it, so the wheel cannot change between the two.
  - **Persist after every point**, so an aborted sweep still reports what it
    measured. **Restore in `finally:`** and rebuild, so the tree and the
    installed wheel match again whatever happened.
  - The measurement is `measure_panel.py` unchanged -- the library's own
    `facts.measure` in a fresh interpreter, the published method, with the
    per-instrument vol median and pinned-VIX crisis columns riding along.

Run from the repository root, venv per the release recipe:

    .venv/bin/python tools/calibration/sweep_market_factor_vol.py \
        --points '[{"sigma":0.0075,"idio":1.0},{"sigma":0.014,"idio":0.85}]' \
        --out tools/calibration/results/market-factor-vol-<date>-<stage>.json

Budget roughly 90s per point: ~30s rebuild, ~60s panel at six seeds.
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
FACTOR_VOL_RS = REPO / "rust" / "src" / "market" / "factor_vol.rs"

# Every patchable constant: which file, and the exact declaration pattern.
# One regex per constant, each required to match exactly once, for the same
# reason the sigma sweep insists: zero matches means the constant moved and
# the sweep would measure nothing; two means the pattern no longer knows
# what it is patching.
CONSTANTS = {
    "sigma": (TICK_RS, re.compile(
        r"(pub const MARKET_FACTOR_SIGMA: f64 = )([0-9eE.+-]+)(;)")),
    "idio": (FACTOR_VOL_RS, re.compile(
        r"(pub const IDIO_SIGMA_SCALE: f64 = )([0-9eE.+-]+)(;)")),
    "alpha": (FACTOR_VOL_RS, re.compile(
        r"(pub const MARKET_VOL_ALPHA: f64 = )([0-9eE.+-]+)(;)")),
    "beta": (FACTOR_VOL_RS, re.compile(
        r"(pub const MARKET_VOL_BETA: f64 = )([0-9eE.+-]+)(;)")),
    "vix": (FACTOR_VOL_RS, re.compile(
        r"(pub const MARKET_VOL_VIX_COUPLING: f64 = )([0-9eE.+-]+)(;)")),
    "ceil": (FACTOR_VOL_RS, re.compile(
        r"(pub const MARKET_VOL_CEILING_MULTIPLE: f64 = )([0-9eE.+-]+)(;)")),
    "floor": (FACTOR_VOL_RS, re.compile(
        r"(pub const MARKET_VOL_FLOOR_MULTIPLE: f64 = )([0-9eE.+-]+)(;)")),
}

TABLE = [
    ("cross_sectional_corr", "corr"),
    ("annualised_vol_pct", "vol%(pool)"),
    ("per_instrument_vol_median_pct", "vol%(med)"),
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


def point_label(point: dict) -> str:
    return ",".join(f"{k}={point[k]!r}" for k in sorted(point))


def patch_point(originals: dict[Path, str], point: dict) -> None:
    """Write every file with the point's overrides applied to the ORIGINALS.

    Always patches from the pristine text, so omitted keys mean "the
    committed value" rather than "whatever the previous point left".
    """
    unknown = set(point) - set(CONSTANTS)
    if unknown:
        raise SystemExit(f"unknown constants in point: {sorted(unknown)}")
    texts = {path: text for path, text in originals.items()}
    for key, value in point.items():
        path, pattern = CONSTANTS[key]
        matches = pattern.findall(texts[path])
        if len(matches) != 1:
            raise SystemExit(
                f"expected exactly one declaration for {key} in {path}, "
                f"found {len(matches)}"
            )
        texts[path] = pattern.sub(
            lambda m: f"{m.group(1)}{float(value)!r}{m.group(3)}", texts[path]
        )
    for path, text in texts.items():
        path.write_text(text, encoding="utf-8")
    # Guard (a): the patch took. Re-read from disk and require every
    # requested value to be present in its declaration.
    for key, value in point.items():
        path, pattern = CONSTANTS[key]
        on_disk = pattern.findall(path.read_text(encoding="utf-8"))
        if len(on_disk) != 1 or float(on_disk[0][1]) != float(value):
            raise SystemExit(
                f"patch for {key}={value} did not take in {path}: found {on_disk}"
            )


def wheel_core_hash(wheel: Path) -> str:
    """SHA-256 of the compiled core inside a built wheel."""
    import zipfile

    with zipfile.ZipFile(wheel) as archive:
        cores = [n for n in archive.namelist()
                 if re.search(r"pretium/_core.*\.(so|pyd)$", n)]
        if len(cores) != 1:
            raise SystemExit(f"expected one compiled core in {wheel}, found {cores}")
        import hashlib

        return hashlib.sha256(archive.read(cores[0])).hexdigest()[:16]


def installed_core_hash(venv: Path) -> str:
    """SHA-256 of the compiled core actually importable from the venv."""
    import hashlib

    cores = sorted(venv.glob("lib/python*/site-packages/pretium/_core*.so")) + sorted(
        venv.glob("lib/python*/site-packages/pretium/_core*.pyd")
    )
    if len(cores) != 1:
        raise SystemExit(f"expected one installed pretium core in {venv}, found {cores}")
    return hashlib.sha256(cores[0].read_bytes()).hexdigest()[:16]


def build_and_install(venv: Path, work: Path, tag: str) -> str:
    """Build, install, and return the installed core's hash.

    Guard (b): the hash of the core inside the wheel just built must equal
    the hash of the core now importable -- the install cannot have been a
    silent no-op against some other build.
    """
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
    built = wheel_core_hash(wheels[0])
    installed = installed_core_hash(venv)
    if built != installed:
        raise SystemExit(
            f"STALE INSTALL: wheel core {built} != installed core {installed}; "
            f"the reinstall did not take."
        )
    return installed


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
    header = f"{'point':>42}" + "".join(f"{label:>11}" for _, label in TABLE)
    lines.append(header)
    for label, entry in sweep.items():
        row = f"{label:>42}"
        for key, _ in TABLE:
            value = median_over_seeds(entry["panels"], key)
            row += f"{value:>11.3f}" if value is not None else f"{'n/a':>11}"
        lines.append(row)
    lines.append("")
    lines.append("cross-sectional correlation under pinned VIX (median over seeds):")
    any_entry = next(iter(sweep.values()))
    vix_levels = sorted(any_entry["pinned_vix"], key=float)
    lines.append(
        f"{'point':>42}" + "".join(f"{'vix ' + v:>11}" for v in vix_levels)
    )
    for label, entry in sweep.items():
        row = f"{label:>42}"
        for vix in vix_levels:
            rows = entry["pinned_vix"][vix]
            corr = statistics.median(
                r["cross_sectional_corr"] for r in rows.values()
            )
            row += f"{corr:>11.3f}"
        lines.append(row)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--points",
        required=True,
        help="JSON list of {constant: value} overrides, one dict per point",
    )
    parser.add_argument("--seeds", default="1,2,3,4,5,6")
    parser.add_argument("--days", type=int, default=252)
    parser.add_argument("--universe-n", type=int, default=40)
    parser.add_argument("--universe-seed", type=int, default=111)
    parser.add_argument("--pin-vix", default="15,45,65")
    parser.add_argument("--pin-days", type=int, default=120)
    parser.add_argument("--out", default=None)
    parser.add_argument("--work-dir", default=None)
    args = parser.parse_args()

    points = json.loads(args.points)
    if not isinstance(points, list) or not all(isinstance(p, dict) for p in points):
        raise SystemExit("--points must be a JSON list of objects")

    venv = REPO / ".venv"
    python = venv / "bin" / "python"
    if not python.exists():
        raise SystemExit(f"{python} not found; create the venv first")
    work = Path(args.work_dir) if args.work_dir else Path(
        tempfile.mkdtemp(prefix="factor-vol-sweep-")
    )
    work.mkdir(parents=True, exist_ok=True)

    out_path = Path(args.out) if args.out else (
        HERE / "results" /
        f"market-factor-vol-{datetime.date.today().isoformat()}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    git_rev = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=REPO, capture_output=True, text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO, capture_output=True, text=True,
    ).stdout.strip()
    if dirty:
        git_rev += "-dirty"

    originals = {
        TICK_RS: TICK_RS.read_text(encoding="utf-8"),
        FACTOR_VOL_RS: FACTOR_VOL_RS.read_text(encoding="utf-8"),
    }
    seen: dict[str, str] = {}
    sweep: dict[str, dict] = {}
    try:
        for index, point in enumerate(points):
            label = point_label(point)
            tag = f"p{index:02d}"
            print(f"\n=== point {index}: {label} ===", flush=True)
            patch_point(originals, point)
            core = build_and_install(venv, work, tag)

            # Guard (c): distinct constant vectors compile to distinct
            # binaries, so an installed core seen before means the rebuild
            # silently reused a previous build.
            if core in seen and seen[core] != label:
                raise SystemExit(
                    f"STALE BUILD: point {label} installed core {core}, "
                    f"already seen for {seen[core]}. The rebuild did not "
                    f"take; refusing to record one build under two labels."
                )
            seen[core] = label
            print_ = fingerprint(python)
            print(f"installed core {core}, trajectory fingerprint {print_}",
                  flush=True)

            entry = measure(python, args, work, tag)
            if entry["trajectory_fingerprint"] != print_:
                raise SystemExit(
                    f"wheel changed between fingerprint and measurement at "
                    f"{label}: {print_} != {entry['trajectory_fingerprint']}"
                )
            entry["point"] = point
            entry["installed_core"] = core
            sweep[label] = entry

            with open(out_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "constants": {
                            key: str(path.relative_to(REPO))
                            for key, (path, _) in CONSTANTS.items()
                        },
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
        for path, text in originals.items():
            path.write_text(text, encoding="utf-8")
        print("\nrestored sources; rebuilding the wheel to match the tree",
              flush=True)
        build_and_install(venv, work, "restored")

    print(f"\nresults written to {out_path}\n")
    print(summarise(sweep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
