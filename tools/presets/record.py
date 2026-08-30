"""Build the committed record for a preset, from a measured panel.

    python tools/presets/record.py --panel out/preset-panel.json
    python tools/presets/record.py --panel out/preset-panel.json --check

A preset's published figures were spread across `envelope.py`, prose in three
modules, a README, several test comments and the changelog, and every release
that moved the default re-typed them by hand. Two survived 0.6.0 unmeasured
because nobody could tell they were stale: `envelope.DECAY_252` and
`DECAY_SLOPE` still describe pt-v14.

A record is one JSON file per preset holding what was measured, what it was
measured with, and when. `python/tradefloor/presets/<name>.json`, generated
here, committed, and shipped in the wheel so the documentation site and
`tradefloor.preset_record` read the same bytes. `git log` on one of those
files is the audit trail for that preset.

The schema is versioned and additive. A reader that knows `schema: 1` keeps
working when a later release adds a field, so the docs build does not have to
move in lockstep with a measurement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "python" / "tradefloor" / "presets"

#: Bumped only when a reader that knows the previous number would be WRONG,
#: rather than merely incomplete. Adding a field does not bump it.
SCHEMA = 1

#: Which release made each preset the default. A record says what a preset
#: IS; this says what it was to the project, and it is the question a reader
#: holding an old result actually asks.
DEFAULT_SINCE = {
    "pt-v3": "0.1.0", "pt-v10": "0.2.0", "pt-v12": "0.3.0",
    "pt-v14": "0.4.0", "pt-v16": "0.6.0",
}


def coefficient_digest(values: dict[str, float]) -> str:
    """A citable identity for the coefficient vector itself.

    The fingerprint is a NAME and a preset could in principle be edited
    under it, which is the failure `the_three_presets_are_three_different_
    models` exists to catch on the Rust side. This is the same guard for a
    record: two files claiming one name and disagreeing here is a defect,
    not a matter of interpretation.
    """
    body = "\n".join(f"{k}={values[k]!r}" for k in sorted(values))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8")
    return out.stdout.strip() if out.returncode == 0 else ""


def build(name: str, panel: dict, values: dict[str, float]) -> dict:
    """One preset's record, from the panel that measured it."""
    p = panel["presets"][name]
    measured = {
        # The panel names the version and the box it ran on. A figure without
        # the build that produced it cannot be re-derived, which is the whole
        # reason `RunManifest` exists for runs.
        "tradefloor_version": panel["pretium_version"],
        "commit": git("rev-parse", "HEAD") or None,
        "workers": panel.get("workers"),
        "wall_seconds": round(panel.get("wall_s", 0.0), 1),
        "method": panel["method"],
    }
    return {
        "schema": SCHEMA,
        "preset": name,
        "fingerprint": name,
        "coefficient_digest": coefficient_digest(values),
        "coefficients": {k: values[k] for k in sorted(values)},
        "default_since": DEFAULT_SINCE.get(name),
        "measured": measured,
        "panel_252": p["panel_252"],
        "panel_504": p["panel_504"],
        "in_band": {
            "252": p["in_band_252"],
            "504": p["in_band_504"],
            "heldout_universe": p["in_band_heldout_universe"],
            "heldout_seeds": p["in_band_heldout_seeds"],
        },
        "misses": {
            "252": p["misses_252"],
            "504": p["misses_504"],
            "heldout_universe": p["misses_heldout_universe"],
            "heldout_seeds": p["misses_heldout_seeds"],
        },
        "crisis_lever": {
            "ratio": p["crisis_lever"],
            "vol_at_vix_5": p["vol_at_vix_5"],
            "vol_at_vix_65": p["vol_at_vix_65"],
            "real": panel["method"].get("real_crisis_lever"),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--panel", required=True,
                    help="a preset_panel.py artefact")
    ap.add_argument("--check", action="store_true",
                    help="compare against the committed records and report "
                         "every difference, writing nothing")
    args = ap.parse_args()

    import tradefloor

    panel = json.loads(pathlib.Path(args.panel).read_text(encoding="utf-8"))
    OUT.mkdir(exist_ok=True)

    drift = []
    for name in sorted(panel["presets"]):
        values = tradefloor.ModelParams.from_preset(name).to_dict()
        record = build(name, panel, values)
        text = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
        path = OUT / f"{name}.json"
        if args.check:
            if not path.exists():
                drift.append(f"{path.name} is missing")
            else:
                have = json.loads(path.read_text(encoding="utf-8"))
                # The measurement block carries a commit and a wall time, so
                # comparing it would report drift on every re-run. What has
                # to agree is the SCIENCE.
                for field in ("coefficient_digest", "panel_252", "panel_504",
                              "in_band", "misses", "crisis_lever"):
                    if have.get(field) != record[field]:
                        drift.append(f"{path.name}: {field} differs")
        else:
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"  wrote {path.relative_to(ROOT)}")

    if args.check:
        for d in drift:
            print(f"  {d}")
        print(f"{len(drift)} differences")
        return 1 if drift else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
