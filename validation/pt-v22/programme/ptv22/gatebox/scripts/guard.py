"""guard.py check|write DIR [OUT.txt]: the gate box's inputs against MANIFEST.sha256 (committed beside this file).

check: every file the manifest lists must be in DIR with its sha256; every other file in DIR must be one the run
supplies (arm.txt, parts.txt) or Python's own cache; and the files registration 19 pins by hash (REGISTERED below,
the draft's items 8 and 9) must carry those hashes. Any miss stops the box before it reads anything. It prints every
input's hash, the arm's included, and writes them to OUT.txt when given.
write: lay the manifest down from DIR (after an intended change of input); it refuses when a REGISTERED file differs.

Standard library only: it runs before the engine is used, on the box (jobs.sh) and on the desk (launch.sh)."""
import hashlib
import pathlib
import sys

MANIFEST = "MANIFEST.sha256"
SUPPLIED = {"arm.txt", "parts.txt"}     # per run, not inputs of the job; their hashes are printed
# ptv22-registration-19-DRAFT.md, "Amendments" items 8 and 9 (design ptv22/s2-fix d8bf1ad1); driven2020.py as items 24
# and 25(a) read L1 (amendments A and E: the index's quarterly reported EPS with v, the trough quarter stamped at its
# end; item 9's file was 6155b708, item 24's 23a04ef0, amendment B's 2e79e484)
REGISTERED = {
    "graded-scenarios/recession.yml": "ad3c51d5c949bba3c49f181e8a6f4ef23cd120bd900abdfc8a22c2f844e710be",
    "driven2020.py": "ae1aee40cb39ec3622b824a2bb4221f90d46ae075c7c9abe936091b6c0197dc9",
    "earnings-2020.json": "9b87fa8e058010608027c6f4fc83a33147ab727c5341fb7c8dc5b940550a931e",
}


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def files(d):
    return sorted(str(p.relative_to(d)) for p in d.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store")


def check(d, out=None):
    want = {}
    for line in (d / MANIFEST).read_text().splitlines():
        if line.strip():
            h, f = line.split(None, 1)
            want[f.strip()] = h
    bad, lines = [], []
    for f, h in sorted(want.items()):
        p = d / f
        got = sha(p) if p.is_file() else "missing"
        lines.append(f"{got[:16]}  {f}")
        if got != h:
            bad.append(f"{f}: manifest {h[:16]}, here {got[:16]}")
    for f, h in REGISTERED.items():
        if want.get(f) != h:
            bad.append(f"{f}: registered {h[:16]}, manifest {str(want.get(f))[:16]}")
    for f in files(d):
        if f in want or f == MANIFEST:
            continue
        if f in SUPPLIED:
            lines.append(f"{sha(d / f)[:16]}  {f} (supplied)")
        else:
            bad.append(f"{f}: not in the manifest")
    text = "\n".join(lines + [f"guard: {'STOP, ' + str(len(bad)) + ' mismatches' if bad else 'OK'} "
                              f"({len(want)} inputs)"] + [f"  {b}" for b in bad]) + "\n"
    if out:
        pathlib.Path(out).write_text(text)
    sys.stdout.write(text)
    return 1 if bad else 0


def write(d):
    fs = [f for f in files(d) if f != MANIFEST and f not in SUPPLIED]
    hs = {f: sha(d / f) for f in fs}
    wrong = [f for f, h in REGISTERED.items() if hs.get(f) != h]
    if wrong:
        sys.stderr.write(f"REFUSED: not the registered file: {', '.join(wrong)}\n")
        return 1
    (d / MANIFEST).write_text("".join(f"{hs[f]}  {f}\n" for f in fs))
    print(f"wrote {MANIFEST}: {len(fs)} inputs")
    return 0


if __name__ == "__main__":
    mode, d = sys.argv[1], pathlib.Path(sys.argv[2]).resolve()
    sys.exit(check(d, sys.argv[3] if len(sys.argv) > 3 else None) if mode == "check" else write(d))
