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
#:
#: AN ERA BOUNDARY EDITS THIS. The release that moves the default adds the
#: new preset's name and its version here, and a record generated before
#: that edit carries `default_since: null` for a preset that IS the
#: default -- which reads as "never the default" rather than as "not
#: recorded yet". `check_default_since` refuses that combination, so the
#: line cannot be forgotten in the direction that publishes a wrong claim.
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


def mechanism_set(values: dict[str, float]) -> dict[str, dict]:
    """The mechanism set with doses, beside the coefficient vector.

    Additive (schema 1 stays 1): a reader that knows the coefficient
    vector keeps working, and one that knows this field can say which
    mechanisms a preset runs, at which specification and which doses.
    The specification digest comes from `tools/mechanism`, the doses are
    the preset's own coefficients for the mechanism's dials. The preset's
    `fingerprint` is untouched.
    """
    for sub in ("mechanism", "mechanism/mechanisms"):
        path = str(ROOT / "tools" / sub)
        if path not in sys.path:
            sys.path.insert(0, path)
    from emit import shipped

    out = {}
    for mech in shipped():
        out[mech.name] = {
            "spec": mech.digest(),
            "stream": mech.stream,
            "doses": {d.name: values.get(d.name, d.default) for d in mech.dials},
        }
    return out


#: The two artefact shapes `--level` accepts, keyed by what the run varied.
#: `archprobe-level-panel.py` writes `roster_per_seed: true` for the level
#: protocol and `false` for the held roster; both are needed, because a
#: level figure without its protocol is not a figure.
LEVEL_PROTOCOLS = {True: "roster_varying", False: "roster_held"}


def load_level(paths: list[str]) -> dict[str, dict[str, dict]]:
    """Group `archprobe-level-panel.py` artefacts by preset and protocol.

    Refuses an artefact that is not on the certification seeds and horizon,
    and refuses two artefacts claiming the same preset and protocol: the
    second would silently win, and a run measured twice on two builds is
    exactly the case where that matters.
    """
    from tradefloor.facts import LEVEL_PROTOCOL  # noqa: PLC0415

    out: dict[str, dict[str, dict]] = {}
    for path in paths:
        art = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        name = art["preset"]
        if tuple(art["seeds"]) != tuple(LEVEL_PROTOCOL["seeds"]):
            raise SystemExit(
                f"{path}: seeds {art['seeds'][0]}-{art['seeds'][-1]} "
                f"({len(art['seeds'])}) are not the certification seeds "
                f"{LEVEL_PROTOCOL['seeds'][0]}-{LEVEL_PROTOCOL['seeds'][-1]}"
            )
        if art["days"] != LEVEL_PROTOCOL["days"]:
            raise SystemExit(
                f"{path}: {art['days']} days, not the certified "
                f"{LEVEL_PROTOCOL['days']}"
            )
        key = LEVEL_PROTOCOLS[bool(art["roster_per_seed"])]
        if key in out.setdefault(name, {}):
            raise SystemExit(
                f"{path}: a second {key} artefact for {name}; one of the two "
                "would win silently"
            )
        out[name][key] = art
    return out


def level_reading(art: dict, *, graded: bool) -> dict:
    """One protocol's reading of the level and crisis rows.

    Every estimator comes from `facts` -- `aggregate_panels` reads each row
    the way the certification does, a mean for the level row and a pooled
    median for `fear_gauge_dn3` -- so this cannot grade a row by an
    estimator the envelope does not use.

    `graded` is False for the held-roster reading. The bands belong to the
    roster-varying protocol, so a held figure carries its value and no
    verdict; see `facts.LEVEL_PROTOCOL["comparison_rule"]`.
    """
    from tradefloor import facts  # noqa: PLC0415

    # The invariant `load_level` establishes, asserted where it is USED. A
    # held reading graded against a band is the one thing
    # `LEVEL_PROTOCOL["comparison_rule"]` forbids, and a caller that got
    # the pair the wrong way round would produce a well-formed block
    # saying it.
    if bool(art["roster_per_seed"]) is not graded:
        raise SystemExit(
            f"{art['preset']}: an artefact with roster_per_seed="
            f"{art['roster_per_seed']} cannot be read as graded={graded}; "
            "the bands belong to the roster-varying protocol"
        )

    rows = art["rows"]
    keys = tuple(facts.LEVEL) + tuple(facts.CRISIS)
    values = facts.aggregate_panels(rows, keys)
    out: dict[str, object] = {
        # The roster as the RUN recorded it, not as this function assumes
        # it: a held run on some other roster would otherwise be labelled
        # with seed 111.
        "protocol": ("facts.LEVEL_PROTOCOL: "
                     f"{art['universe']}, roster varying"
                     if graded else
                     f"{art['universe']} HELD, "
                     f"seeds {art['seeds'][0]}-{art['seeds'][-1]}"),
        "roster_per_seed": bool(art["roster_per_seed"]),
        "graded": graded,
        "seeds": [art["seeds"][0], art["seeds"][-1]],
        "n_seeds": len(art["seeds"]),
        "days": art["days"],
        "commit": art.get("commit"),
        "package_version": art.get("package_version"),
        "model_fingerprint": art.get("model_fingerprint"),
    }
    for key in keys:
        if key not in values:
            out[key] = None
            continue
        how = facts.AGGREGATE.get(key, "median")
        row: dict[str, object] = {"value": values[key], "estimator": how}
        if how == "pooled":
            row["pooled_sessions"] = facts.pooled_sessions(rows, key)
            row["reporting_seeds"] = sum(
                1 for r in rows if r.get(key) is not None)
        else:
            row["n_seeds"] = sum(1 for r in rows if r.get(key) is not None)
        if graded:
            low, high = facts.REAL_MARKETS[key]
            row["band"] = [low, high]
            row["distance"] = facts.band_distance(values[key], low, high)
            row["in_band"] = row["distance"] == 0
        out[key] = row
    return out


def level_block(arts: dict[str, dict]) -> dict:
    """The `panel_level` block: both protocols, each labelled, and the rule.

    Additive under schema 1. It is a SEPARATE block from `panel_252`
    deliberately: `panel_252` is the fourteen shape rows the envelope
    certifies, and folding a level row into it would put a row held red on
    purpose into a count that means "green".
    """
    from tradefloor.facts import CRISIS, LEVEL, LEVEL_PROTOCOL  # noqa: PLC0415

    missing = [k for k in ("roster_varying", "roster_held") if k not in arts]
    if missing:
        raise SystemExit(
            f"--level needs both protocols; missing {missing}. A level "
            "figure without the other protocol beside it invites exactly "
            "the comparison the rule forbids"
        )
    return {
        "rows": list(LEVEL) + list(CRISIS),
        "comparison_rule": LEVEL_PROTOCOL["comparison_rule"],
        "roster_varying": level_reading(arts["roster_varying"], graded=True),
        "roster_held": level_reading(arts["roster_held"], graded=False),
    }


def check_default_since(name: str, record: dict) -> str | None:
    """A record for the DEFAULT preset must say which release made it so.

    Returns a refusal, or None. `DEFAULT_SINCE` is edited by hand at an era
    boundary and a record generated in the window between the default
    moving and that edit publishes `default_since: null`, which a reader
    takes as "this preset was never the default" rather than as "nobody
    filled it in".
    """
    import tradefloor  # noqa: PLC0415

    if name != tradefloor.model_preset()["name"]:
        return None
    if record.get("default_since"):
        return None
    return (f"{name} is the engine's default preset and DEFAULT_SINCE has no "
            f"entry for it, so the record would claim it was never the "
            f"default. Add {name!r} and its release to DEFAULT_SINCE")


def git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8")
    return out.stdout.strip() if out.returncode == 0 else ""


def build(name: str, panel: dict, values: dict[str, float],
          level: dict[str, dict] | None = None) -> dict:
    """One preset's record, from the panel that measured it.

    `level` is the `--level` artefacts for this preset, both protocols, and
    adds the `panel_level` block. Absent, the record carries no level rows
    at all -- which is what every record written before 2026-09-05 does,
    and why `main` refuses to REWRITE a record that has one without them.
    """
    p = panel["presets"][name]
    measured = {
        # The panel names the version and the box it ran on. A figure without
        # the build that produced it cannot be re-derived, which is the whole
        # reason `RunManifest` exists for runs.
        "tradefloor_version": panel["pretium_version"],
        # THE COMMIT THE PANEL WAS MEASURED AT, not the checkout that wrote
        # the record. Those are routinely different -- the panel runs on a
        # box and the record is written here afterwards -- and stamping the
        # writer's HEAD as the measurement's provenance is a figure that
        # cannot be re-derived. `written_at` keeps the writer's, because
        # knowing which tree generated the file is still worth having.
        #
        # None means an older panel artefact that predates the field, and
        # it is reported as None rather than filled in from the writer.
        "commit": panel.get("commit"),
        "written_at": git("rev-parse", "HEAD") or None,
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
        "mechanisms": mechanism_set(values),
        "default_since": DEFAULT_SINCE.get(name),
        "measured": measured,
        "panel_252": p["panel_252"],
        "panel_504": p["panel_504"],
        **({"panel_level": level_block(level)} if level else {}),
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
    ap.add_argument("--panel", required=False,
                    help="a preset_panel.py artefact")
    ap.add_argument("--level", action="append", default=[], metavar="PATH",
                    help="an archprobe-level-panel.py artefact (design "
                         "repo), repeatable. TWO per preset are required: "
                         "one --roster-per-seed (facts.LEVEL_PROTOCOL, the "
                         "protocol the level and crisis rows are certified "
                         "on) and one with the roster held at seed 111. "
                         "They become the additive panel_level block, each "
                         "labelled with its protocol")
    ap.add_argument("--check", action="store_true",
                    help="compare against the committed records and report "
                         "every difference, writing nothing")
    ap.add_argument("--mechanisms", action="store_true",
                    help="rewrite only the mechanism set of every committed "
                         "record from the build's coefficients; no panel needed")
    ap.add_argument("--coefficients", action="store_true",
                    help="rewrite only the coefficient vector and its digest "
                         "on every committed record, from the build; no panel "
                         "needed. For an era that ADDS a dial, where every "
                         "preset gains the new name at its inert default and "
                         "no measurement has changed")
    args = ap.parse_args()
    if args.mechanisms:
        return write_mechanisms()
    if args.coefficients:
        return write_coefficients()

    import tradefloor

    if args.panel is None:
        ap.error("--panel is required unless --mechanisms is given")
    panel = json.loads(pathlib.Path(args.panel).read_text(encoding="utf-8"))
    OUT.mkdir(exist_ok=True)

    level = load_level(args.level)
    unknown = sorted(set(level) - set(panel["presets"]))
    if unknown:
        ap.error(f"--level names presets the panel does not measure: {unknown}")

    # PRE-FLIGHT, before anything is written. A record that carries a
    # `panel_level` and is regenerated from a panel alone comes back
    # WITHOUT one, and nothing downstream fails: the documentation site
    # simply stops publishing the level rows. That is the shape of the
    # stale `DECAY_252` this tool exists because of, so it is refused
    # rather than warned about, and refused for every preset at once so a
    # partial rewrite cannot happen.
    if not args.check:
        lost = []
        for name in sorted(panel["presets"]):
            path = OUT / f"{name}.json"
            if not path.exists() or name in level:
                continue
            if "panel_level" in json.loads(path.read_text(encoding="utf-8")):
                lost.append(name)
        if lost:
            print("  REFUSED: these records carry a panel_level and no "
                  "--level artefact was given for them, so rewriting would "
                  f"drop it: {lost}")
            return 1

    drift = []
    for name in sorted(panel["presets"]):
        values = tradefloor.ModelParams.from_preset(name).to_dict()
        record = build(name, panel, values, level.get(name))
        refusal = check_default_since(name, record)
        if refusal:
            print(f"  REFUSED {name}.json: {refusal}")
            return 1
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
                fields = ["coefficient_digest", "mechanisms", "panel_252",
                          "panel_504", "in_band", "misses", "crisis_lever"]
                # Only when this run BUILT one. A --check without --level
                # says nothing about the level rows rather than reporting
                # them as drift, and the count line below says which.
                if "panel_level" in record:
                    fields.append("panel_level")
                for field in fields:
                    if have.get(field) != record[field]:
                        drift.append(f"{path.name}: {field} differs")
        else:
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"  wrote {path.relative_to(ROOT)}")

    if args.check:
        for d in drift:
            print(f"  {d}")
        print(f"{len(drift)} differences"
              + ("" if level else "; panel_level NOT checked (no --level)"))
        return 1 if drift else 0
    return 0


def write_coefficients() -> int:
    """Rewrite the coefficient vector and its digest, and nothing else.

    Adding a settable parameter changes the SHAPE of every preset's
    coefficient vector, because the digest is taken over the whole of
    `settable_names()`. Every record therefore goes stale at once, on a
    field that has nothing to do with what the preset was measured to do,
    and `--panel` would demand a fresh measurement to fix a bookkeeping
    change.

    So this touches two fields and leaves the measured blocks byte for
    byte as they are. What it must NOT be used for is a preset whose
    coefficients actually moved: that is a new preset, and a new preset
    needs the panel.
    """
    import tradefloor

    for path in sorted(OUT.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        # `to_dict()` whole, exactly as `build` passes it, `name` included:
        # the committed digest was taken over that dict and filtering any
        # key here would move the digest for a second, invisible reason.
        values = tradefloor.ModelParams.from_preset(record["preset"]).to_dict()
        before = record["coefficients"]
        moved = sorted(k for k in set(before) & set(values)
                       if before[k] != values[k])
        if moved:
            print(f"  REFUSED {path.name}: {len(moved)} existing coefficient(s) "
                  f"moved, which is a new preset and needs --panel: {moved}")
            return 1
        record["coefficient_digest"] = coefficient_digest(values)
        record["coefficients"] = {k: values[k] for k in sorted(values)}
        text = json.dumps(record, indent=2, ensure_ascii=False) + chr(10)
        path.write_text(text, encoding="utf-8", newline=chr(10))
        added = sorted(set(values) - set(before))
        print(f"  wrote {path.relative_to(ROOT)}"
              + (f"  (+{', '.join(added)})" if added else ""))
    return 0


def write_mechanisms() -> int:
    """Set the mechanism field on every committed record, from the build."""
    import tradefloor

    for path in sorted(OUT.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        values = tradefloor.ModelParams.from_preset(record["preset"]).to_dict()
        record["mechanisms"] = mechanism_set(values)
        ordered = {}
        for key, value in record.items():
            ordered[key] = value
            if key == "coefficients":
                ordered["mechanisms"] = record["mechanisms"]
        path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8", newline="\n")
        print(f"  {path.name}: mechanisms {sorted(record['mechanisms'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
