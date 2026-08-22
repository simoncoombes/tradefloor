"""Identifiability, measured instead of argued: the SVD of the scaled Jacobian.

CALIBRATION.md §4.3, built. Reads one or more `jacobian.py` result files
(chunked runs of one Jacobian are concatenated after verifying they
share method, seeds and bit-identical base panels — the determinism
contract makes that concatenation sound, and the check makes it
checked), assembles the dimensionless sensitivity matrix

    M[k, j] = (mean secant of statistic k per deviation unit of
               parameter j) / s_k

with s_k the across-seed sd of statistic k at the base vector, measured
from the same run's base panels — and takes its SVD.

What the pieces mean, mechanically:

- **Column norms** are each parameter's total visibility: how many
  seed-sds of panel movement one unit of regularised deviation buys,
  across all eight statistics. A parameter with an exactly-zero column
  produced bit-identical panels under perturbation — invisible is then
  a theorem about the run, not an estimate.
- **Singular values** price the best- and worst-seen directions in
  parameter space. With eight statistics against P parameters the rank
  is at most eight, so at least P − 8 directions are unseen by
  dimension counting alone; the measured content is how the seen
  directions decay, and hence how many EFFECTIVE parameters the panel
  constrains.
- **The searched-set submatrix** is §3.9's freeze-list hypothesis under
  test: its spectrum says whether the nine searched parameters are
  themselves mutually identifiable, and the full matrix says whether
  any frozen parameter is strongly visible (a candidate to unfreeze).

Caveats carried from the design (§4.3): J is local — a different base
has a different Jacobian — and identifiability is evaluated on the
moment map, not on the band loss (whose flat interior would hide
exactly the structure this measures).

Usage:

    .venv/bin/python tools/calibration/identifiability.py \
        --jacobians results/jacobian-pt-v1-*.json \
        --out results/identifiability-pt-v1-$(date +%F).json
"""

from __future__ import annotations

import argparse
import json

import numpy as np

import instrumentlib as lib


def load_chunks(paths: list[str]) -> tuple[dict, dict[str, dict]]:
    """Merge chunked Jacobian files into (shared header, columns by param)."""
    header: dict | None = None
    columns: dict[str, dict] = {}
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        method = {k: data["method"][k] for k in
                  ("base", "base_overrides", "h", "seeds", "days", "universe")}
        if header is None:
            header = {"method": method, "base_panels": data["base_panels"],
                      "seed_sd": data["seed_sd"], "files": [path],
                      "wall_seconds": data["wall_seconds"],
                      "evaluations": data["evaluations"]}
        else:
            if method != header["method"]:
                raise SystemExit(f"{path}: method differs from {paths[0]}; "
                                 "these are not chunks of one Jacobian")
            if data["base_panels"] != header["base_panels"]:
                raise SystemExit(f"{path}: base panels differ bit-for-bit "
                                 "from the first chunk — determinism broken "
                                 "or different builds; refusing to merge")
            header["files"].append(path)
            header["wall_seconds"] += data["wall_seconds"]
            header["evaluations"] += data["evaluations"]
        for name, col in data["columns"].items():
            if name in columns:
                raise SystemExit(f"{name} appears in two chunks")
            columns[name] = col
    assert header is not None
    return header, columns


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--jacobians", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--aggregate", default="mean",
                        choices=["mean", "median"],
                        help="which across-seed secant aggregate fills M")
    args = parser.parse_args()

    header, columns = load_chunks(args.jacobians)
    params = [name for name in lib.PARAM_SPECS if name in columns]
    extra = [name for name in columns if name not in lib.PARAM_SPECS]
    if extra:
        raise SystemExit(f"unknown parameters in Jacobian: {extra}")
    seed_sd = header["seed_sd"]
    stats = list(lib.PANEL_STATS)

    matrix = np.array([
        [columns[name]["aggregate"][stat][args.aggregate] / seed_sd[stat]
         for name in params]
        for stat in stats
    ])

    # Across-seed uncertainty of each entry, same scaling: sd of the
    # per-seed secants over sqrt(n_seeds), divided by s_k. Entries whose
    # magnitude is below ~2x this are not distinguishable from zero even
    # under CRN (the derivative's own seed-dependence, not comparison
    # noise).
    n_seeds = len(header["method"]["seeds"])
    entry_se = np.array([
        [(columns[name]["aggregate"][stat]["sd"] or 0.0)
         / (n_seeds ** 0.5) / seed_sd[stat]
         for name in params]
        for stat in stats
    ])

    column_norms = np.linalg.norm(matrix, axis=0)
    order = np.argsort(column_norms)[::-1]

    print(f"base {header['method']['base']}, h={header['method']['h']}, "
          f"{n_seeds} seeds, aggregate={args.aggregate}")
    print(f"\n{'parameter':<28}{'|column| (sd/dev-unit)':>24}   class")
    tiers = []
    for j in order:
        name = params[j]
        norm = column_norms[j]
        tier = ("exact zero" if norm == 0.0
                else "invisible (<0.1)" if norm < 0.1
                else "weak (0.1-1)" if norm < 1.0
                else "visible")
        searched = "searched" if name in lib.SEARCHED_9 else "frozen"
        tiers.append({"parameter": name, "column_norm": float(norm),
                      "tier": tier, "hypothesis_class": searched})
        print(f"{name:<28}{norm:>24.4f}   {tier:<18} {searched}")

    def spectrum(sub_params: list[str]) -> dict:
        idx = [params.index(p) for p in sub_params]
        sub = matrix[:, idx]
        u, s, vt = np.linalg.svd(sub, full_matrices=False)
        dirs = []
        for i in range(len(s)):
            loadings = sorted(
                zip(sub_params, vt[i]), key=lambda t: -abs(t[1]))
            dirs.append({
                "singular_value": float(s[i]),
                "moment_side": {stat: float(u[k, i])
                                for k, stat in enumerate(stats)},
                "loadings": [(n, float(w)) for n, w in loadings
                             if abs(w) > 0.05],
            })
        return {
            "params": sub_params,
            "singular_values": [float(x) for x in s],
            "directions": dirs,
            "rank_bound": min(len(stats), len(sub_params)),
        }

    full = spectrum(params)
    searched = spectrum([p for p in lib.SEARCHED_9 if p in columns]) \
        if all(p in columns for p in lib.SEARCHED_9) else None

    print("\nsingular values, full surface: "
          + ", ".join(f"{x:.3f}" for x in full["singular_values"]))
    if searched:
        print("singular values, searched-9 submatrix: "
              + ", ".join(f"{x:.3f}" for x in searched["singular_values"]))
    print("\nbest-seen directions (right singular vectors, |loading| > 0.05):")
    for i, d in enumerate(full["directions"][:8]):
        names = ", ".join(f"{n} {w:+.2f}" for n, w in d["loadings"][:5])
        print(f"  sigma_{i + 1} = {d['singular_value']:8.3f}: {names}")

    lib.write_json(args.out, {
        "provenance": lib.provenance(),
        "method": {
            **header["method"],
            "aggregate": args.aggregate,
            "jacobian_files": header["files"],
            "row_scale": "seed sd s_k measured from the base panels of "
                         "the same run",
            "column_scale": "deviation units (log for scale parameters, "
                            "raw for bounded; CALIBRATION.md section 6.3)",
        },
        "seed_sd": seed_sd,
        "matrix": {
            "stats": stats,
            "params": params,
            "values": matrix.tolist(),
            "entry_standard_error": entry_se.tolist(),
        },
        "column_norms": tiers,
        "spectrum_full": full,
        "spectrum_searched9": searched,
    })


if __name__ == "__main__":
    main()
