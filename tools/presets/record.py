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
    "pt-v14": "0.4.0", "pt-v16": "0.6.0", "pt-v18": "0.7.0", "pt-v19": "0.8.0",
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
        # The panel's own commit first. `git rev-parse HEAD` here names the
        # checkout writing the file, which is the measuring one only when the
        # record is written on the box that measured it.
        "commit": panel.get("commit") or git("rev-parse", "HEAD") or None,
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
        # The mechanism certificate beside the band panel, because "14 of 14
        # in band" answers fidelity alone: on five rows the band contains the
        # reading of a model WITHOUT the mechanism the row is named for. See
        # `envelope.certify`. Additive, so schema 1 stays 1 and a record
        # written before this field existed keeps working.
        "mechanism_252": p["mechanism_252"],
        "mechanism_heldout_seeds": p["mechanism_heldout_seeds"],
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
    ap.add_argument("--check", action="store_true",
                    help="compare against the committed records and report "
                         "every difference, writing nothing")
    ap.add_argument("--mechanisms", action="store_true",
                    help="rewrite only the mechanism set of every committed "
                         "record from the build's coefficients; no panel needed")
    ap.add_argument("--mechanism-gate", metavar="PANEL",
                    help="write ONLY the mechanism certificate onto the "
                         "committed records the given panel names, leaving "
                         "every other field byte for byte as it is. For the "
                         "case this field was added in: the certificate is a "
                         "new measurement on a protocol whose per-seed panels "
                         "nobody kept before, and re-deriving `panel_252` "
                         "from a fresh run at the same time would silently "
                         "fold in every model and roster change since the "
                         "record was written")
    ap.add_argument("--level-rows", metavar="ROWS",
                    help="write ONLY the LEVEL_PROTOCOL block onto the record "
                         "the given ptv18-envelope-rows.py artefact names. "
                         "envelope.CERTIFIED_LEVEL and CERTIFIED_CRISIS are "
                         "certified on a protocol no panel measures, so they "
                         "reach a record through here or not at all")
    ap.add_argument("--default-since", action="store_true",
                    help="rewrite only `default_since` on every committed "
                         "record from the DEFAULT_SINCE table; no panel "
                         "needed. For the release that moves the default")
    ap.add_argument("--coefficients", action="store_true",
                    help="rewrite only the coefficient vector and its digest "
                         "on every committed record, from the build; no panel "
                         "needed. For an era that ADDS a dial, where every "
                         "preset gains the new name at its inert default and "
                         "no measurement has changed")
    args = ap.parse_args()
    if args.mechanism_gate:
        return write_mechanism_gate(args.mechanism_gate)
    if args.level_rows:
        return write_level_protocol(args.level_rows)
    if args.default_since:
        return write_default_since()
    if args.mechanisms:
        return write_mechanisms()
    if args.coefficients:
        return write_coefficients()

    import tradefloor

    if args.panel is None:
        ap.error("--panel is required unless --mechanisms is given")
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
                for field in ("coefficient_digest", "mechanisms", "panel_252",
                              "panel_504", "in_band", "misses", "crisis_lever",
                              "mechanism_252", "mechanism_heldout_seeds"):
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


def write_mechanism_gate(panel_path: str) -> int:
    """Set the mechanism certificate on every record the panel names.

    Two fields and nothing else. A preset's `panel_252` was measured on the
    build and the roster generator of its own day, and the certificate is
    measured today: writing both from one run would move the published band
    figures for a reason that has nothing to do with the mechanism question,
    which is the drift this whole file exists to stop. So the certificate
    carries its OWN provenance -- the commit, the version and the method that
    produced it -- and a reader can see that the two blocks were measured on
    different builds because each says which.
    """
    panel = json.loads(pathlib.Path(panel_path).read_text(encoding="utf-8"))
    measured = {
        "tradefloor_version": panel["pretium_version"],
        "commit": git("rev-parse", "HEAD") or None,
        "method": panel["method"],
    }
    written = 0
    for name in sorted(panel["presets"]):
        path = OUT / f"{name}.json"
        if not path.exists():
            print(f"  skipped {name}: no committed record to write onto")
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        p = panel["presets"][name]
        for field in ("mechanism_252", "mechanism_heldout_seeds"):
            block = dict(p[field])
            block["measured"] = measured
            record[field] = block
        ordered = {}
        for key, value in record.items():
            if key in ("mechanism_252", "mechanism_heldout_seeds"):
                continue
            ordered[key] = value
            if key == "panel_504":
                ordered["mechanism_252"] = record["mechanism_252"]
                ordered["mechanism_heldout_seeds"] = \
                    record["mechanism_heldout_seeds"]
        path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8", newline="\n")
        counts = record["mechanism_252"]["counts"]
        print(f"  wrote {path.relative_to(ROOT)}  mechanism shown "
              f"{counts['mechanism_shown']} of {counts['mechanism_of']}"
              + (f", REVERSED {record['mechanism_252']['reversed']}"
                 if record["mechanism_252"]["reversed"] else ""))
        written += 1
    return 0 if written else 1


def write_level_protocol(rows_path: str) -> int:
    """Set the LEVEL_PROTOCOL block on the record the measurement names.

    The panel this file is otherwise built from holds `Universe.random(40,
    seed=111)` and measures the fourteen SHAPE rows. `envelope.CERTIFIED_LEVEL`
    and `CERTIFIED_CRISIS` are certified on `facts.LEVEL_PROTOCOL`, where the
    roster varies with the seed, so no panel can produce them and nothing
    bound them to a record. That is how `envelope.DECAY_252` came to describe
    pt-v14 under a pt-v16 default while its neighbours moved: a published
    constant with nothing to compare it against goes stale in silence, and
    the module's own docstring promises it describes the shipped preset.

    Its own provenance, like the mechanism certificate's, because it is a
    different run on a different protocol and a reader must be able to see
    that rather than infer it.

    REFUSES on a measurement whose control did not reproduce. The control arm
    exists to separate a moved model from a moved instrument, and a block
    written over a failed control is a published number nobody can compare.
    The refusal belongs here, at the write, rather than at the measurement:
    the readings are worth keeping either way, and it is publishing them that
    the verdict has to gate.
    """
    doc = json.loads(pathlib.Path(rows_path).read_text(encoding="utf-8"))
    name = doc["target"]
    path = OUT / f"{name}.json"
    if not path.exists():
        print(f"REFUSED: no committed record at {path} to write the "
              f"level block onto; write the panel record first",
              file=sys.stderr)
        return 1
    if not doc.get("control_reproduced"):
        print(f"REFUSED: the control {doc['control']} did not reproduce "
              f"the constants this build publishes for it "
              f"({', '.join(doc.get('control_moved_rows') or [])}), so "
              f"{name}'s readings are not on the ruler they replace",
              file=sys.stderr)
        return 1

    record = json.loads(path.read_text(encoding="utf-8"))
    record["level_protocol"] = {
        "certified_level": doc["certified_level"],
        "certified_crisis": doc["certified_crisis"],
        "certification": doc["certification_record"],
        "control": {
            "preset": doc["control"],
            "reproduced": True,
            "values": doc["control_values"],
            "published": doc["published_values"],
        },
        "measured": {
            "tradefloor_version": doc.get("package_version"),
            "commit": doc.get("commit"),
            "protocol": doc.get("protocol"),
            "seeds": doc.get("seeds"),
            "days": doc.get("days"),
        },
    }
    ordered = {}
    for key, value in record.items():
        if key == "level_protocol":
            continue
        ordered[key] = value
        if key == "mechanism_heldout_seeds":
            ordered["level_protocol"] = record["level_protocol"]
    if "level_protocol" not in ordered:      # a record written before that field
        ordered["level_protocol"] = record["level_protocol"]
    path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8", newline="\n")
    rows = dict(doc["certified_level"])
    rows.update(doc["certified_crisis"])
    print(f"  wrote {path.relative_to(ROOT)}  level_protocol: "
          + ", ".join(f"{k}={v:.4f}" for k, v in rows.items()))
    return 0


def write_default_since() -> int:
    """Set `default_since` on every committed record, from the table above.

    Not a measurement: it says which release made a preset the default, which
    is a fact about the project and lives in `DEFAULT_SINCE`. It gets its own
    mode because the alternative at an era boundary is re-running a 96-core
    panel to change one string, or editing seventeen JSON files by hand --
    and hand-editing a generated record is how `envelope.DECAY_252` came to
    describe a preset nobody was running.
    """
    written = 0
    for path in sorted(OUT.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        was = record.get("default_since")
        record["default_since"] = DEFAULT_SINCE.get(record["preset"])
        if was != record["default_since"]:
            print(f"  {path.name}: default_since {was!r} -> "
                  f"{record['default_since']!r}")
            written += 1
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8", newline="\n")
    print(f"  {written} record(s) changed")
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
