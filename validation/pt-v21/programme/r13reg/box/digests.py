"""digests.py verify ARMS_FILE [ARMS_FILE ...] [--repo DIR] [--json FILE] [--enforce]: the grade box's known answers
beyond the sim digest (fifteenth registration, "Known answers the grade box checks"; the fourteenth registered R19V).

Two checks on the engine this box built (run on the engine's python, from its checkout or with --repo):

  presets  the combined digest over every shipped preset's fixed market (the engine's
           tests/known_answer_presets.py, `combined_digest(preset_digests())`) is PRESETS_DIGEST.
  arms     every arm line of each ARMS_FILE ("NAME@BASE:dial=value,...") builds, through
           tf.ModelParams.from_preset(BASE, **dials), the fingerprint ARM_FINGERPRINTS gives that arm; every arm
           the registration names is in the file and the file names no other arm.

The expected values are the registration's and are not read from the environment. The sim digest is checked
before this, by the box's user-data (SKIP_GATE_IF_KAT). --enforce (the job passes it when REGISTERED_GRADE is
set, so on the grade) exits 1 on any mismatch, before the first grading job; without it the result is printed and
the exit is 0, so a screen of other arms is not stopped."""
import argparse
import importlib.util
import json
import os
import sys

PRESETS_DIGEST = "87f0b185fb8a84a7038b38065c91034c94e26caaf7e820d1959f292b21c43cc0"
ARM_FINGERPRINTS = {"R21E1": "custom-2dc32068"}   # R21E1 on 2024f633 (arms/arm-R21E1.txt), the seventeenth and eighteenth registrations


def parse_arms(path):
    """[(name, base, dials)] from an arms file: NAME@BASE:k=v,... per line, # comments and blank lines skipped."""
    out = []
    for line in open(path):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        head, _, body = line.partition(":")
        name, _, base = head.partition("@")
        dials = {k: float(v) for k, v in (x.split("=", 1) for x in body.split(",") if x.strip())}
        out.append((name.strip(), (base or "pt-v20").strip(), dials))
    return out


def presets_digest(repo):
    p = os.path.join(repo, "tests", "known_answer_presets.py")
    spec = importlib.util.spec_from_file_location("known_answer_presets", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.combined_digest(m.preset_digests())


def fingerprint(base, dials):
    import tradefloor as tf
    p = tf.ModelParams.from_preset(base, **dials)
    return getattr(p, "fingerprint", None) or p.to_dict().get("name")


def check(presets, arms, expect_presets=PRESETS_DIGEST, expect_arms=None):
    """(ok, lines): presets is the computed combined digest; arms [(name, fingerprint)] from the arms files."""
    expect_arms = ARM_FINGERPRINTS if expect_arms is None else expect_arms
    lines, ok = [], True
    good = presets == expect_presets
    ok &= good
    lines.append(f"presets  {presets}  expected {expect_presets}  {'ok' if good else 'MISMATCH'}")
    seen = set()
    for name, fp in arms:
        seen.add(name)
        want = expect_arms.get(name)
        good = want is not None and fp == want
        ok &= good
        lines.append(f"arm {name:8s} {fp}  expected {want or '(not a registered arm)'}  {'ok' if good else 'MISMATCH'}")
    for name in sorted(set(expect_arms) - seen):
        ok = False
        lines.append(f"arm {name:8s} absent from the arms file  MISMATCH")
    return ok, lines


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["verify"])
    ap.add_argument("arms", nargs="+")
    ap.add_argument("--repo", default=os.getcwd())
    ap.add_argument("--json")
    ap.add_argument("--enforce", action="store_true")
    a = ap.parse_args(argv)
    try:
        pd = presets_digest(a.repo)
        arms = []
        for f in a.arms:
            arms += [(name, fingerprint(base, dials)) for name, base, dials in parse_arms(f)]
        ok, lines = check(pd, arms)
    except Exception as e:     # a digest that cannot be computed does not match
        ok, lines = False, [f"could not compute the digests: {type(e).__name__}: {e}"]
    for x in lines:
        print(x)
    print("DIGESTS " + ("ok" if ok else "MISMATCH") + ("" if a.enforce else " (reported; --enforce not set)"))
    if a.json:
        json.dump({"ok": ok, "lines": lines, "enforced": a.enforce}, open(a.json, "w"), indent=1)
    return 0 if ok or not a.enforce else 1


if __name__ == "__main__":
    sys.exit(main())
