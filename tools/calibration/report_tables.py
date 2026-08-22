"""Render a calibration certificate as the report's tables.

Every published figure in this repository names the method that produced
it, and the weakest link in that chain is a person copying a number out of
a JSON and into a markdown table. This script closes it: the tables in
`pretium-design/CALIBRATION-PTV2.md` are generated from
`results/calibrate-pt-v2-*.json` by running this, so re-running it against
the same certificate reproduces the report's numbers exactly and a reader
who disbelieves a row can regenerate it.

    .venv/bin/python tools/calibration/report_tables.py \
        --certificate results/calibrate-pt-v2-2026-08-22.json
"""

from __future__ import annotations

import argparse
import json

AXES = (("train_seeds", "train (30 seeds)"),
        ("holdout_seeds", "held-out seeds (1-6)"),
        ("holdout_universe", "held-out universe (60, seed 222)"),
        ("holdout_horizon", "held-out horizon (504 days)"))


def fmt(value: float | None, places: int = 4) -> str:
    if value is None:
        return "--"
    return f"{value:+.{places}f}"


def verdict(stat: dict) -> str:
    if stat["measured"] is None:
        return "unmeasured"
    low, high = stat["band"]
    if stat["distance"] == 0:
        return "in"
    return "out (high)" if stat["measured"] > high else "out (low)"


def room(stat: dict) -> str:
    """How far inside its band a statistic sits, in its own seed noise.

    The band loss is flat inside the band (§6.1) and therefore blind to
    this; it is the quantity phase 3's band-edge failure was about, and a
    table that prints only "in"/"out" hides it. Negative means outside.
    """
    value = stat.get("room_sd")
    return "--" if value is None else f"{value:+.2f}"


def compare(cert: dict, keys: list[str]) -> None:
    """An `evaluate_axes.py` certificate as a side-by-side panel.

    The question this phase exists to answer is comparative — is the
    margined vector better than `pt-v2` on the axes the search never saw
    — so the table is one column per vector rather than one table per
    vector, and every cell carries the band room that decides it.
    """
    names = list(cert["vectors"])
    for axis, label in AXES:
        print(f"\n**{label}** — L_real " + ", ".join(
            f"{n} {cert['vectors'][n]['axes'][axis]['loss_real']:.4f}"
            for n in names) + "\n")
        print("| statistic | band | role | "
              + " | ".join(f"{n} | room" for n in names) + " |")
        print("|---|---|---|" + "---|---|" * len(names))
        for key in keys:
            first = cert["vectors"][names[0]]["axes"][axis]["statistics"][key]
            band = f"{first['band'][0]:g} to {first['band'][1]:g}"
            places = 1 if key == "annualised_vol_pct" else (
                2 if key == "excess_kurtosis" else 4)
            cells = []
            for name in names:
                stat = cert["vectors"][name]["axes"][axis]["statistics"][key]
                mark = "" if stat["distance"] == 0 else " **out**"
                cells.append(f"{fmt(stat['measured'], places)}{mark} | "
                             f"{room(stat)} sd")
            print(f"| {key} | {band} | {first['role']} | "
                  + " | ".join(cells) + " |")

    print("\n### The two variance persistences, against the 252-day window\n")
    print("| vector | factor persistence | half-life | GJR persistence "
          "| half-life |")
    print("|---|---|---|---|---|")
    for name in names:
        d = cert["vectors"][name]["diagnostics"]
        f, g = d["market_factor_variance"], d["per_name_garch"]
        print(f"| {name} | {f['persistence']:.6f} | "
              f"{f['half_life_days']:.1f} d | {g['persistence']:.6f} | "
              f"{g['half_life_days']:.1f} d |")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--certificate", required=True)
    parser.add_argument("--label", default="candidate",
                        help="what to call the searched vector's column")
    args = parser.parse_args()
    with open(args.certificate, encoding="utf-8") as handle:
        cert = json.load(handle)

    keys = list(cert["method"]["bands"])
    if "vectors" in cert:
        compare(cert, keys)
        return
    label = args.label

    margin = cert["method"].get("search_margin")
    if margin:
        print("### The margin the search aimed at\n")
        print(f"The search scored candidates against bands shrunk by "
              f"**{margin['margin_sd']} seed-sds on each side**. Every "
              f"number in every other table here is the shipped "
              f"`band_distance_loss` against the TRUE bands.\n")
        print("| statistic | true band | width | searched band | margin |")
        print("|---|---|---|---|---|")
        for key, row in margin["bands"].items():
            t, s = row["true"], row["searched"]
            print(f"| {key} | {t[0]:g} to {t[1]:g} | "
                  f"{row['band_width_sd']:.2f} sd | "
                  f"{s[0]:.4g} to {s[1]:.4g} | "
                  f"{row['achieved_margin_sd']:.2f} sd"
                  + (" (degenerate)" if row["degenerate"] else "") + " |")

    cap = cert["method"].get("factor_persistence_cap")
    if cap:
        print(f"\n### The identifiability cap\n")
        print(f"- `market_vol_alpha + market_vol_beta` <= "
              f"**{cap['persistence']:.6f}**")
        print(f"- half-life {cap['half_life_days']:g} trading days against a "
              f"{cap['window_days']}-day window "
              f"({cap['half_lives_in_window']:g} half-lives inside it)\n")
    print()

    print("### What moved\n")
    print(f"| parameter | \u2016column\u2016 | pt-v1 | {label} | move "
          "| \u00a76.3 deviation |")
    print("|---|---|---|---|---|---|")
    for move in sorted(cert["moves"],
                       key=lambda m: -abs(m["deviation"])):
        norm = move["column_norm_seed_sds_per_dev_unit"]
        ratio = (f"x{move['ratio']:.3f}" if move["ratio"] else "--")
        moved = "" if move["deviation"] else " (unmoved)"
        print(f"| `{move['parameter']}`{moved} | {norm} | "
              f"{move['pt_v1']:.6g} | {move['candidate']:.6g} | {ratio} | "
              f"{move['deviation']:+.4f} |")

    print(f"\n### The panel, pt-v1 against {label}, on every axis\n")
    for axis, axis_label in AXES:
        before = cert["axes"]["pt-v1"][axis]
        after = cert["axes"]["candidate"][axis]
        print(f"\n**{axis_label}** — L_real "
              f"{before['loss_real']:.3f} -> {after['loss_real']:.3f}\n")
        print(f"| statistic | band | role | pt-v1 | {label} | "
              f"verdict pt-v1 | verdict {label} | room {label} |")
        print("|---|---|---|---|---|---|---|---|")
        for key in keys:
            b, a = before["statistics"][key], after["statistics"][key]
            band = f"{b['band'][0]:g} to {b['band'][1]:g}"
            places = 1 if key == "annualised_vol_pct" else (
                2 if key == "excess_kurtosis" else 4)
            print(f"| {key} | {band} | {a['role']} | "
                  f"{fmt(b['measured'], places)} | "
                  f"{fmt(a['measured'], places)} | "
                  f"{verdict(b)} | {verdict(a)} | {room(a)} sd |")

    print(f"\n### Band room at {label}, every axis (seed-sds inside the "
          "TRUE band; negative is outside)\n")
    print("| statistic | " + " | ".join(l for _, l in AXES) + " |")
    print("|---" * (len(AXES) + 1) + "|")
    for key in keys:
        cells = []
        for axis, _ in AXES:
            stat = cert["axes"]["candidate"][axis]["statistics"][key]
            cells.append(room(stat))
        print(f"| {key} | " + " | ".join(cells) + " |")

    print("\n### §8's overfitting test\n")
    over = cert["overfitting"]
    print(f"- train L_real: {over['train_loss_real']:.4f}")
    print(f"- bootstrap spread of train L_real across its seeds: "
          f"{over['train_bootstrap_spread']:.4f}")
    print(f"- rejection threshold (train + 2 spreads): "
          f"{over['threshold']:.4f}")
    for axis, row in over["axes"].items():
        print(f"- {axis}: L_real {row['loss_real']:.4f} — "
              + ("EXCEEDS" if row["exceeds_threshold"] else "within"))
    flips = over["statistics_in_band_on_train_out_on_validation"]
    print(f"- in band on train, out on a validation axis: "
          + (", ".join(f"{f['statistic']} ({f['axis']})" for f in flips)
             if flips else "none"))
    print(f"- **verdict: {over['verdict']}**")

    print("\n### The lambda frontier\n")
    print("| squared deviation | L_real | parameters moved |")
    print("|---|---|---|")
    ship = {m["parameter"]: m["pt_v1"] for m in cert["moves"]}
    for point in cert["lambda_frontier"]["pareto"]:
        movers = sum(1 for k, v in point["overrides"].items()
                     if v != ship.get(k))
        print(f"| {point['squared_deviation']:.4f} | "
              f"{point['loss_real']:.4f} | {movers} |")

    print("\n### Where each lambda would put the operating point\n")
    print("| lambda | squared deviation | L_real | seeds |")
    print("|---|---|---|---|")
    for lam, pick in cert["lambda_frontier"]["picks"].items():
        print(f"| {lam} | {pick['squared_deviation']:.4f} | "
              f"{pick['loss_real']:.4f} | {pick['seeds']} |")

    if cert.get("degeneracy_probe"):
        probe = cert["degeneracy_probe"]
        print("\n### The degenerate corner, re-measured here\n")
        print(f"- inside this search's box: "
              f"{probe['in_calibration_box']}")
        print(f"- L_real on the training seeds: "
              f"{probe['measured']['loss_real']:.3f}")
        print("\n| statistic | band | measured | verdict |")
        print("|---|---|---|---|")
        for key in keys:
            stat = probe["measured"]["statistics"][key]
            band = f"{stat['band'][0]:g} to {stat['band'][1]:g}"
            print(f"| {key} | {band} | {fmt(stat['measured'])} | "
                  f"{verdict(stat)} |")

    print("\n### Cost\n")
    print(f"- vector evaluations: {cert['vector_evaluations']}")
    print(f"- panel runs: {cert['panel_runs']} "
          f"({cert['six_seed_vector_equivalents']:.0f} six-seed-vector "
          f"equivalents)")
    print(f"- search budget: {cert['budget_panel_runs']} panel runs, "
          f"{cert['reserve_panel_runs']} reserved for the thirty-seed "
          f"stages")
    print(f"- wall clock: {cert['wall_seconds']:.0f} s")
    guard = cert.get("crn_guard", {})
    print(f"- CRN guard: asserted on the "
          f"`{guard.get('asserted_stream', 'total')}` stream, which held on "
          f"every evaluation")
    print(f"- economy-stream draw deviations (the documented macro-state "
          f"coupling, not a violation): "
          f"{len(guard.get('economy_deviations', []))}")


if __name__ == "__main__":
    main()
