"""guard19.py: the nineteenth registration's grade inputs against MANIFEST19.json (committed beside this file).

    python guard19.py launch --ref JOBS_REF --pin PIN (--plan PLAN.json | --arm ARM_FILE)   desk, before any box
    python guard19.py box DIR [--engine REPO]                                              the box, before any run
    python guard19.py grade --ref JOBS_REF --engine TF_ENGINE                              grade19.sh, before step one
                                                                     (TF_ENGINE at the pin and clean)
    python guard19.py write --ref REF                                                      lay the manifest down
    python guard19.py joined --ref REF                                                     joined19.py, before a read

The manifest holds, by sha256:
  box      every file a grade box's scripts/longrun/ gets: assemble.py's files at the ref (files.txt, the graded
           scenarios, the screen's own files) and box.sh's screen-jobs.sh and wheel.sh;
  desk     the readers grade19.sh runs on the desk, with what they import (r13reg's grade_all, exploits,
           exploits_summary, verdict16, box/seedplan.py, box/digests.py, deps/, audit085/, grade19/ but its dryrun/; longrun's
           ptv22 readers; ptv22/futures's; the real values) and the ptv21 reader at grade 18's pin;
  gatebox  the gate box's own MANIFEST.sha256 (its guard.py checks each file);
  launch   the files a launch runs or sends from the desk: screen/box.sh and the user data it sends, the gate box's
           launch.sh and its user data (checked at launch both at JOBS_REF and in this checkout, which runs them);
  joined   the joined gate's script (ptv22/joined/joined19.py), its registered readers spec and the readers it runs at
           the ref (registration 20): the pre-grade chance, checked with no slot, since the arm is read before it is
           registered;
  slots    arm (sha256 of the arm line), engine_pin (the engine commit), grade_seeds (sha256 of grade-seeds.json, or
           "absent" for a dry run on held-out seeds): empty until the registration fills them, and every check
           refuses while any is empty.
REGISTERED below pins the brief's two files by hash, so a manifest written from the wrong ref still stops."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]                                  # the design repo
MANIFEST = "programme/r13reg/grade19/MANIFEST19.json"
SLOTS = ("arm", "engine_pin", "grade_seeds")
REGISTERED = {   # box-side names (scripts/longrun/)
    "seedplan.py": "84c66dd779dee9a184968442811f9d61690b8bf2dc824badb32cb93133d7c100",
    "r15-grade-jobs.sh": "a6189d7a616637088a113705b7a92a34339101a73e7091fd66c39779a1a58ffd",
}
DESK_FILES = ["r13reg/grade_all.py", "r13reg/exploits.py", "r13reg/exploits_summary.py", "r13reg/verdict16.py",
              "r13reg/box/seedplan.py", "r13reg/box/digests.py", "longrun/ptv22_switch.py", "longrun/ptv22.py",
              "longrun/ptv22traded.py", "longrun/ptv22_pieces.py", "longrun/data/ptv22/real.json",
              "ptv22/futures/futrows.py", "ptv22/futures/passprob_futures.py", "ptv22/futures/tpieces.py",
              # what grade_all runs on the desk besides: lr270.py and the long run's report and criteria over it, v1.py,
              # the bands criteria.py reads, the certification grader
              "r13reg/lr270.py", "r13reg/margins.py", "r13reg/box/certgrade_box_hr.py", "longrun/criteria.py",
              "longrun/longrun.py", "longrun/estimators.py", "longrun/tape.py", "results/ptv20/v1.py",
              "results/ptv20/bands.json"]
DESK_DIRS = ["r13reg/grade19", "r13reg/deps", "r13reg/audit085"]
P21 = ("fbfb1f88f1fbfa2b1475c9a37d50a509d8dc1d79", ["longrun/ptv21.py", "longrun/data/ptv21/real.json"])
NOT_INPUTS = {"plan.json", "grade-seeds.json", "guard19.py", "MANIFEST19.json"}   # per run, a slot, or the guard
# the joined gate's script and the readers it runs at the registration's ref (ptv22/joined/joined19.py with
# readers-registered.json; registration 20): the pre-grade chance, not a grade step, pinned so the arms' reads name them
JOINED_FILES = ["ptv22/joined/joined19.py", "ptv22/joined/readers-registered.json", "ptv22/joined/joined_gate.py",
                "ptv22/joined/build_under.py", "ptv22/results/r22o5czN-B/gate/passprob.py",
                "ptv22/results/r22o5czN-B/gate/resize.py", "ptv22/results/r22o5czN-B/gate/passprob_rest.py",
                "ptv22/futures/pp_dropped.py", "longrun/ptv22_passprob.py", "longrun/xd5pp.py", "longrun/mg3pp.py", "ptv22/joined/reads/b9grade.py",
                "ptv22/joined/reads/dv1c20.py", "ptv22/joined/reads/r7read20.py", "ptv22/joined/reads/on1read20.py"]
LAUNCH_FILES = ["programme/screen/box.sh", "programme/scripts/user-data-screen.sh", "programme/ptv22/gatebox/launch.sh",
                "programme/ptv22/gatebox/user-data-jobbox.sh", "programme/ptv22/gatebox/LAUNCH.sha256"]


def git(*a, text=False):
    return subprocess.run(["git", "-C", str(ROOT), *a], check=True, capture_output=True, text=text).stdout


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def commit(ref):
    return git("rev-parse", "--verify", f"{ref}^{{commit}}", text=True).strip()


def manifest_at(ref):
    return json.loads(git("show", f"{ref}:{MANIFEST}"))


def box_files(ref) -> dict[str, str]:
    """scripts/longrun/ as screen/box.sh lays it out at REF, hashed (plan, grade seeds and the guard aside)."""
    sys.path.insert(0, str(ROOT / "programme" / "screen" / "box"))
    import assemble
    with tempfile.TemporaryDirectory() as t:
        L = assemble.assemble(pathlib.Path(t), ref)
        for f in ("screen-jobs.sh", "wheel.sh"):
            (L / f).write_bytes(git("show", f"{ref}:programme/screen/box/{f}"))
        return {str(p.relative_to(L)): sha(p.read_bytes()) for p in sorted(L.rglob("*"))
                if p.is_file() and "__pycache__" not in p.parts and p.name not in NOT_INPUTS}


def desk_names(ref):
    names = list(DESK_FILES)
    for d in DESK_DIRS:
        names += [n[len("programme/"):] for n in git("ls-tree", "-r", "--name-only", ref, f"programme/{d}", text=True).split()
                  if not n.endswith("MANIFEST19.json") and "__pycache__" not in n and "/grade19/dryrun/" not in n]
    return sorted(set(names))


def desk_files(ref) -> dict[str, str]:
    out = {n: sha(git("show", f"{ref}:programme/{n}")) for n in desk_names(ref)}
    for n in P21[1]:
        out[f"{n} @ {P21[0][:8]}"] = sha(git("show", f"{P21[0]}:programme/{n}"))
    return out


def compare(kind, want, got):
    bad = [f"{kind} {f}: manifest {h[:12]}, here {got.get(f, 'missing')[:12]}" for f, h in sorted(want.items()) if got.get(f) != h]
    bad += [f"{kind} {f}: not in the manifest" for f in sorted(set(got) - set(want))]
    return bad


def check_slots(m, dry=False):
    bad = [f"slot {s} is empty: the registration names it" for s in SLOTS if not m["slots"].get(s)]
    if m["slots"].get("grade_seeds") == "absent" and not dry:
        bad.append("slot grade_seeds is 'absent' (held-out seeds only) outside a dry run (DRY_RUN=1)")
    return bad


def check_registered(box):
    return [f"registered {f}: {h[:12]} pinned, {box.get(f, 'missing')[:12]} in the manifest"
            for f, h in REGISTERED.items() if box.get(f) != h]


def arm_lines(plan=None, arm=None):
    if arm:
        return {pathlib.Path(arm).read_text().strip()}
    return {s["line"].strip() for s in json.loads(pathlib.Path(plan).read_text())["shards"]}


def check_arm(m, lines):
    want = m["slots"].get("arm")
    return [f"arm: a line with sha256 {sha(l.encode())[:12]} ({l.split('@')[0]}), the slot names {str(want)[:12]}"
            for l in sorted(lines) if want and sha(l.encode()) != want]


def report(bad, what):
    for b in bad:
        print(f"  {b}")
    print(f"guard19 {what}: {'STOP, ' + str(len(bad)) + ' mismatches' if bad else 'OK'}")
    return 2 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("launch", "box", "grade", "write", "joined"))
    ap.add_argument("dir", nargs="?")
    ap.add_argument("--ref"); ap.add_argument("--pin"); ap.add_argument("--plan"); ap.add_argument("--arm")
    ap.add_argument("--engine")
    a = ap.parse_args()
    dry = bool(os.environ.get("DRY_RUN"))
    if a.mode == "write":
        ref = commit(a.ref)
        try:
            slots = manifest_at(ref)["slots"]
        except subprocess.CalledProcessError:
            slots = {s: "" for s in SLOTS}
        box = box_files(ref)
        bad = check_registered(box)
        if bad:
            return report(bad, "write (REFUSED)")
        m = {"what": "registration 19's grade inputs (guard19.py)", "written_from": ref, "slots": slots,
             "box": box, "desk": desk_files(ref),
             "gatebox": {"programme/ptv22/gatebox/scripts/MANIFEST.sha256":
                         sha(git("show", f"{ref}:programme/ptv22/gatebox/scripts/MANIFEST.sha256"))},
             "launch": {k: sha(git("show", f"{ref}:{k}")) for k in LAUNCH_FILES},
             "joined": {k: sha(git("show", f"{ref}:programme/{k}")) for k in JOINED_FILES}}
        (ROOT / MANIFEST).write_text(json.dumps(m, indent=1) + "\n")
        print(f"wrote {MANIFEST}: {len(m['box'])} box files, {len(m['desk'])} desk files, {len(m['launch'])} launch files; slots "
              + ", ".join(f"{k}={'set' if v else 'empty'}" for k, v in slots.items()))
        return 0
    if a.mode == "box":
        L = pathlib.Path(a.dir)
        m = json.loads((L / "MANIFEST19.json").read_text())
        got = {str(p.relative_to(L)): sha(p.read_bytes()) for p in sorted(L.rglob("*"))
               if p.is_file() and "__pycache__" not in p.parts and p.name not in NOT_INPUTS}
        bad = check_slots(m, dry) + check_registered(m["box"]) + compare("box", m["box"], got)
        gs = L / "grade-seeds.json"
        want = m["slots"].get("grade_seeds")
        if want and want != "absent" and (not gs.is_file() or sha(gs.read_bytes()) != want):
            bad.append(f"grade-seeds.json: slot {want[:12]}, here {sha(gs.read_bytes())[:12] if gs.is_file() else 'missing'}")
        if want == "absent" and gs.is_file():
            bad.append("grade-seeds.json is here, but the slot says absent")
        bad += check_arm(m, arm_lines(plan=L / "plan.json"))
        if a.engine:
            head = subprocess.run(["git", "-C", a.engine, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            if m["slots"].get("engine_pin") and head != m["slots"]["engine_pin"]:
                bad.append(f"engine: checkout at {head[:12]}, the slot names {m['slots']['engine_pin'][:12]}")
        return report(bad, "box")
    try:
        ref = commit(a.ref)
        m = manifest_at(ref)
    except subprocess.CalledProcessError:
        return report([f"ref {a.ref}: not a commit here, or no {MANIFEST} at it (not this registration's grade job)"],
                      a.mode)
    if a.mode == "joined":     # the joined gate's readers at REF against the manifest at REF (no slot: it is pre-grade)
        want = m.get("joined") or {}
        bad = [] if want else ["the manifest has no joined section (written before registration 20)"]
        bad += compare("joined", want, {k: sha(git("show", f"{ref}:programme/{k}")) for k in want})
        return report(bad, "joined")
    bad = check_slots(m, dry)
    if a.mode == "launch":
        if a.pin and m["slots"].get("engine_pin") and a.pin != m["slots"]["engine_pin"]:
            bad.append(f"engine: PIN {a.pin[:12]}, the slot names {m['slots']['engine_pin'][:12]} (give the full commit)")
        bad += check_arm(m, arm_lines(a.plan, a.arm))
        if a.plan:
            box = box_files(ref)
            bad += check_registered(m["box"]) + compare("box", m["box"], box)
        else:
            bad += compare("gatebox", m["gatebox"], {k: sha(git("show", f"{ref}:{k}")) for k in m["gatebox"]})
        want = m.get("launch") or {}
        if not want:
            bad.append("the manifest has no launch files (written before round 2)")
        bad += compare("launch", want, {k: sha(git("show", f"{ref}:{k}")) for k in want})
        bad += [b.replace("launch ", "launch (this checkout) ", 1) for b in
                compare("launch", want, {k: sha((ROOT / k).read_bytes()) if (ROOT / k).is_file() else "missing"
                                         for k in want})]
        return report(bad, "launch")
    # grade: the readers this checkout runs, against the manifest at JOBS_REF
    got = {n: sha((ROOT / "programme" / n).read_bytes()) if (ROOT / "programme" / n).is_file() else "missing"
           for n in desk_names("HEAD")}
    for n in P21[1]:
        got[f"{n} @ {P21[0][:8]}"] = sha(git("show", f"{P21[0]}:programme/{n}"))
    bad += compare("desk", m["desk"], got)
    if a.engine:
        head = subprocess.run(["git", "-C", a.engine, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        if m["slots"].get("engine_pin") and head != m["slots"]["engine_pin"]:
            bad.append(f"engine: TF_ENGINE at {head[:12]}, the slot names {m['slots']['engine_pin'][:12]}")
        # the pin's files, not an edited checkout of it (session 6's dry run, 2026-10-27): anything git reports
        # modified, staged or untracked (not ignored) stops the grade
        st = subprocess.run(["git", "-C", a.engine, "status", "--porcelain"], capture_output=True, text=True)
        dirty = st.stdout.splitlines() if st.returncode == 0 else ["(git status failed)"]
        if dirty:
            bad.append(f"engine: TF_ENGINE is dirty, {len(dirty)} paths ({', '.join(x.strip() for x in dirty[:3])}"
                       f"{', ...' if len(dirty) > 3 else ''}): grade on a clean checkout of the pin")
    return report(bad, "grade")


if __name__ == "__main__":
    sys.exit(main())
