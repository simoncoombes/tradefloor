"""ao5.py BOX ARM [--engine DIR] [--seed S] [--json FILE]: AO5, "the 40 registered rows are unchanged by the
arrival shuffle switch" (book_arrival_shuffle), graded as a construction check on the box's own engine and scripts.

AO5 is not a statistic of one run: "unchanged" compares the arm with the switch on (R16A carries 1.0) and off.
Running every protocol behind the 40 rows a second time with the switch off would double the grade box. The
switch can only act through one code path, so AO5 checks that path and that none of the 40 rows' scripts reaches
it. Five checks; AO5 is the number that fail (band: 0).

  1. engine   The engine checkout (TF_ENGINE or --engine) holds the box's engine code: under rust/src,
              python/tradefloor and tools/calibration it differs from the commit in BOX/commit.txt (committed or
              not) in Rust comments and Rust unit tests (`mod tests`) at most.
  2. readers  In that code the switch is read in exactly two places: Rust `Engine::arrival_order` (engine.rs)
              and Python `World.run` (counterfactual.py), where it is read only for a cohort (`not self._single`).
              Engine::arrival_order is called only through its Python binding (python_engine.rs), and the
              binding only from World.run. Anything else that names the switch outside params.rs's plumbing,
              the presets, the type stub and provenance.py's text fails the check until it is reviewed.
  3. scripts  The scripts behind the 40 rows, as the box ran them (BOX/scripts-as-run.tgz: longrun.py,
              certrun_box.py, crisisext_box.py, crisisext_box_hr.py, edge.py, c4.py, xsec.py, grade_xsec.py,
              desk.py, driven2020.py, driven2022.py, c10.py, tape.py, estimators.py, r7_event.py, r7_eval.py,
              r7_event_pre.py, recession_rows.py (R7a, R7b, R7a-pre, S1a, S1b, S2); and the engine's
              tools/calibration/impact_curve.py for C9) use only Engine, ModelParams, Universe, Scenario, facts,
              evaluate, StrategySpec and version from tradefloor. None of those builds a World: an untraded
              Engine never asks for an arrival order, and evaluate runs each agent on its own engine. A script
              that names anything else (World, counterfactual, externality, noise, baselines, ...) fails.
  4. paired   The same entry points run twice with the arm's dials, the switch on and off, on one held-out seed
              (default 3001, the arrival-order rows' held-out block), with World.__init__ wrapped to record
              every cohort of two or more agents: an untraded Engine (40 sessions, 20 names), facts.measure
              (60 sessions, 10 names), evaluate with C4b's agents (hold, oracle, the six price rules; 10 days),
              and evaluate under the packaged recession (60 days, as C4b runs it; three of the agents). Every
              output is identical bit for bit, leaving out the model's fingerprint (which names the switch), and
              no cohort of two was built.
  5. live     The switch is live in this build, so 4 is not vacuous: with it on, Engine.arrival_order puts two
              labels out of label order on some of 60 steps and a two-buyer World (e7_arm.py's q1 cohort)
              records a cohort of two; with it off, label order on every step.

Prints one line `AO5JSON {...}` (and writes --json FILE): {"AO5": failed checks, "checks": {name: {"pass": ...,
"detail": ...}}, "engine_commit": ..., "arm": ...}. grade_all.py reads BOX/ao/ao5_ARM.json. Runs on the engine's
python (it imports tradefloor); the box's collected directory needs commit.txt, arms.txt and scripts-as-run.tgz."""
import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile

SCRIPTS_40 = ["longrun.py", "certrun_box.py", "crisisext_box.py", "crisisext_box_hr.py", "edge.py", "c4.py",
              "xsec.py", "grade_xsec.py", "desk.py", "driven2020.py", "driven2022.py", "c10.py", "tape.py",
              "estimators.py", "r7_event.py", "r7_eval.py", "r7_event_pre.py", "recession_rows.py"]
ENGINE_SCRIPTS_40 = ["tools/calibration/impact_curve.py"]
ALLOWED_API = {"Engine", "ModelParams", "Universe", "Scenario", "facts", "evaluate", "StrategySpec", "version",
               "__version__", "__file__"}
CODE_PATHS = ["rust/src", "python/tradefloor", "tools/calibration"]
SWITCH = "book_arrival_shuffle"


# ------------------------------------------------------------------------------------------------ 1. engine

def _tests_start(text):
    """The first line of a Rust file's `mod tests` (unit tests, not compiled into the engine), or None."""
    for i, line in enumerate(text.splitlines(), 1):
        if re.match(r"\s*mod\s+tests\b", line):
            return i
    return None


def inert_rust_diff(diff, new_text, old_text):
    """True when every changed line of one .rs file's -U0 diff is a comment, blank, or inside `mod tests`."""
    t_new, t_old = _tests_start(new_text), _tests_start(old_text)
    for h in re.finditer(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@.*?\n((?:[-+ \\].*\n?)*)", diff, re.M):
        o, n = int(h.group(1)), int(h.group(2))
        for line in h.group(3).splitlines():
            if line.startswith("\\"):
                continue
            body = line[1:].strip()
            comment = not body or body.startswith("//")
            if line.startswith("+"):
                if not comment and not (t_new and n >= t_new):
                    return False
                n += 1
            elif line.startswith("-"):
                if not comment and not (t_old and o >= t_old):
                    return False
                o += 1
    return True


def check_engine(eng, commit):
    """The checkout's engine code is the box's: files under CODE_PATHS differ from the box's commit (committed or
    not) only in Rust comments and Rust unit tests (`mod tests`), which the engine does not run."""
    def git(*a):
        return subprocess.run(["git", "-C", eng, *a], capture_output=True, text=True)
    if not commit:
        return False, "no engine commit (BOX/commit.txt)"
    if git("cat-file", "-e", commit + "^{commit}").returncode:
        return False, f"engine checkout {eng} has no commit {commit[:12]}"
    d = git("diff", "--name-only", commit, "--", *CODE_PATHS)          # working tree against the box's commit
    u = git("ls-files", "--others", "--exclude-standard", "--", *CODE_PATHS)
    if d.returncode:
        return False, d.stderr.strip()
    untracked = [x for x in u.stdout.split() if x.endswith((".py", ".rs"))]
    changed = [x for x in d.stdout.split() if x]
    live = list(untracked)
    for f in changed:
        if not f.endswith(".rs"):
            live.append(f); continue
        diff = git("diff", "-U0", commit, "--", f).stdout
        path = os.path.join(eng, f)
        new_text = open(path, errors="replace").read() if os.path.exists(path) else ""
        old_text = git("show", f"{commit}:{f}").stdout
        if not inert_rust_diff(diff, new_text, old_text):
            live.append(f)
    head = git("rev-parse", "HEAD").stdout.strip()
    if live:
        return False, f"engine code differs from {commit[:12]} (checkout HEAD {head[:12]}): {live[:8]}"
    inert = f"; {len(changed)} files differ in Rust comments or unit tests only: {changed[:6]}" if changed else ""
    return True, f"code under {', '.join(CODE_PATHS)} is {commit[:12]}'s (checkout HEAD {head[:12]}){inert}"


# ------------------------------------------------------------------------------------------------ 2. readers

def _rust_code_lines(text):
    """(line number, code with // comments removed) for a Rust source."""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        code = line.split("//", 1)[0]
        if code.strip():
            out.append((i, code))
    return out


def _fn_spans(text):
    """{fn name: (first line, last line)} for Rust fns, by brace depth from the `fn` line."""
    lines = text.splitlines(); spans = {}
    for i, line in enumerate(lines):
        m = re.match(r"\s*(?:pub(?:\([^)]*\))?\s+)?fn\s+(\w+)", line.split("//", 1)[0])
        if not m:
            continue
        depth, started = 0, False
        for j in range(i, len(lines)):
            code = lines[j].split("//", 1)[0]
            depth += code.count("{") - code.count("}")
            started = started or "{" in code
            if started and depth <= 0:
                spans.setdefault(m.group(1), []).append((i + 1, j + 1)); break
    return spans


def rust_readers(src_dir):
    """[(file, line, where)] for every code line naming the switch or calling arrival_order, outside params.rs."""
    hits = []
    for root, _, files in os.walk(src_dir):
        for f in sorted(files):
            if not f.endswith(".rs"):
                continue
            p = os.path.join(root, f); rel = os.path.relpath(p, os.path.dirname(src_dir))
            text = open(p, errors="replace").read()
            spans = _fn_spans(text)
            def where(n):
                best = None
                for name, ss in spans.items():
                    for a, b in ss:
                        if a <= n <= b and (best is None or b - a < best[1] - best[0]):
                            best = (a, b, name)
                return best[2] if best else "<module>"
            for n, code in _rust_code_lines(text):
                if f == "params.rs":
                    continue
                if re.search(rf"\b{SWITCH}\b", code):
                    hits.append((rel, n, where(n), "reads"))
                if re.search(r"\barrival_order\s*\(", code) and not re.search(r"\bfn\s+arrival_order\b", code):
                    hits.append((rel, n, where(n), "calls arrival_order"))
    return hits


def py_readers(pkg_dir):
    """[(file, line, where, kind)]: every Python expression naming the switch (a string equal to it, an attribute
    of that name) or calling .arrival_order(, with the enclosing Class.function."""
    hits = []
    for root, _, files in os.walk(pkg_dir):
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            p = os.path.join(root, f); rel = os.path.relpath(p, os.path.dirname(pkg_dir))
            try:
                tree = ast.parse(open(p, errors="replace").read())
            except SyntaxError as e:
                hits.append((rel, 0, "unparsed", str(e))); continue
            stack = []

            def visit(node):
                named = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                if named:
                    stack.append(node.name)
                if isinstance(node, ast.Constant) and node.value == SWITCH:
                    hits.append((rel, node.lineno, ".".join(stack) or "<module>", "reads"))
                if isinstance(node, ast.Attribute) and node.attr == SWITCH:
                    hits.append((rel, node.lineno, ".".join(stack) or "<module>", "reads"))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "arrival_order":
                    hits.append((rel, node.lineno, ".".join(stack) or "<module>", "calls arrival_order"))
                for c in ast.iter_child_nodes(node):
                    visit(c)
                if named:
                    stack.pop()
            visit(tree)
    return hits


EXPECTED_RUST = {("src/engine.rs", "arrival_order", "reads"), ("src/python_engine.rs", "arrival_order", "calls arrival_order")}
EXPECTED_PY = {("tradefloor/counterfactual.py", "World.run", "reads"),
               ("tradefloor/counterfactual.py", "World.run", "calls arrival_order"),
               ("tradefloor/provenance.py", "<module>", "reads")}      # the dial's provenance text, a dict key


def check_readers(eng):
    rs = rust_readers(os.path.join(eng, "rust", "src"))
    ps = py_readers(os.path.join(eng, "python", "tradefloor"))
    bad = [h for h in rs if (h[0], h[2], h[3]) not in EXPECTED_RUST]
    bad += [h for h in ps if (h[0], h[2], h[3]) not in EXPECTED_PY]
    seen = {(h[0], h[2], h[3]) for h in rs} | {(h[0], h[2], h[3]) for h in ps}
    missing = sorted((EXPECTED_RUST | EXPECTED_PY) - seen)
    # the Python read is a cohort's only
    cf = os.path.join(eng, "python", "tradefloor", "counterfactual.py")
    cohort_only = False
    if os.path.exists(cf):
        text = open(cf, errors="replace").read()
        m = re.search(r"shuffled\s*=\s*\(\s*not\s+self\._single\s+and\s+bool\(\s*self\.engine\.model_params\.get\(\s*\"" + SWITCH, text)
        cohort_only = bool(m)
    ok = not bad and not missing and cohort_only
    detail = (f"{len(rs)} Rust and {len(ps)} Python sites, all expected; World.run reads it only for a cohort"
              if ok else f"unexpected {bad[:6]}; missing {missing}; cohort-only read {'found' if cohort_only else 'NOT found'}")
    return ok, detail


# ------------------------------------------------------------------------------------------------ 3. scripts

def tradefloor_names(source):
    """The tradefloor names a script uses: attributes of a name bound to the package or a submodule, and names
    imported from it; submodules imported count as their last component."""
    tree = ast.parse(source)
    alias, names = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] == "tradefloor":
                    alias.add(a.asname or "tradefloor")
                    if "." in a.name:
                        names.add(a.name.split(".")[-1])
        elif isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "tradefloor":
            if "." in n.module:
                names.add(n.module.split(".")[-1])
            for a in n.names:
                names.add(a.name)
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in alias:
            names.add(n.attr)
    return names


def check_scripts(box, eng):
    tgz = os.path.join(box, "scripts-as-run.tgz")
    if not os.path.exists(tgz):
        return False, "no BOX/scripts-as-run.tgz"
    found, bad = {}, {}
    with tarfile.open(tgz) as t:
        members = {os.path.basename(m.name): m for m in t.getmembers() if m.isfile() and m.name.endswith(".py")}
        for s in SCRIPTS_40:
            if s not in members:
                continue          # a script the box did not ship (crisisext_box_hr.py on older boxes) runs nothing
            found[s] = tradefloor_names(t.extractfile(members[s]).read().decode("utf-8", "replace"))
    for s in ENGINE_SCRIPTS_40:
        p = os.path.join(eng, s)
        if os.path.exists(p):
            found[os.path.basename(s)] = tradefloor_names(open(p, errors="replace").read())
    need = {"longrun.py", "certrun_box.py", "edge.py", "c4.py", "grade_xsec.py", "driven2020.py", "driven2022.py",
            "impact_curve.py", "r7_event.py", "r7_eval.py", "r7_event_pre.py", "recession_rows.py"}
    absent = sorted(need - set(found))
    for s, names in found.items():
        extra = sorted(names - ALLOWED_API)
        if extra:
            bad[s] = extra
    ok = not bad and not absent
    used = sorted(set().union(*found.values()) & ALLOWED_API - {"__file__", "__version__"}) if found else []
    detail = (f"{len(found)} scripts use only {used}" if ok else f"outside the allowed API: {bad}; scripts absent: {absent}")
    return ok, detail


# ------------------------------------------------------------------------------------------------ 4, 5. paired, live

def arm_dials(box, arm):
    for line in open(os.path.join(box, "arms.txt")):
        line = line.strip()
        if line.startswith(arm + "@"):
            base, _, body = line.split("@", 1)[1].partition(":")
            return base, {k: float(v) for k, v in (x.split("=") for x in body.split(",") if x)}
    raise SystemExit(f"ao5: no arm {arm} in {box}/arms.txt")


IDENTITY_KEYS = {"model_fingerprint", "fingerprint"}   # the model's name, which the switch changes by definition


def _strip(obj):
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if k not in IDENTITY_KEYS}
    if isinstance(obj, (list, tuple)):
        return [_strip(v) for v in obj]
    return obj


def _digest(obj):
    """sha256 of an output with the model's identity (its fingerprint) left out, floats by repr: bit for bit."""
    return hashlib.sha256(json.dumps(_strip(obj), sort_keys=True, default=repr).encode()).hexdigest()


def run_paired(box, arm, seed):
    import tradefloor as tf
    from tradefloor import counterfactual as cf

    cohorts = []
    init = cf.World.__init__

    def recording_init(self, *a, **k):
        init(self, *a, **k)
        if not self._single and len(self._agents) >= 2:
            cohorts.append(sorted(self._agents))
    cf.World.__init__ = recording_init
    try:
        base, dials = arm_dials(box, arm)
        models = {v: tf.ModelParams.from_preset(base, **{**dials, SWITCH: v}) for v in (1.0, 0.0)}
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

        def untraded(m):
            e = tf.Engine(seed=seed, universe=tf.Universe.random(20, seed=111), model=m)
            out = []
            for _ in range(40):
                e.run_days(1)
                out.append([list(e.prices()), dict(e.macro_fields)])
            return out

        def measure(m):
            return tf.facts.measure(seed=seed, universe=list(tf.Universe.random(10, seed=111)), days=60, model=m)

        def agents():
            daily = lambda kind: tf.StrategySpec(signal={"kind": kind, "lookback_days": 5.0},  # noqa: E731
                                                 execution={"cadence": "daily"})
            return {"mean_reversion_1step": tf.StrategySpec.mean_reversion(lookback_days=1 / 6),
                    "mean_reversion_1day": tf.StrategySpec.mean_reversion(lookback_days=1.0),
                    "mean_reversion_5day_daily": daily("mean_reversion"),
                    "momentum_1step": tf.StrategySpec.momentum(lookback_days=1 / 6),
                    "momentum_1day": tf.StrategySpec.momentum(lookback_days=1.0),
                    "momentum_5day_daily": daily("momentum"),
                    "buy_and_hold": tf.StrategySpec.hold(), "oracle": tf.StrategySpec.oracle()}

        def evaluate(m, scenario=None, days=10, only=None):
            ag = {k: v for k, v in agents().items() if not only or k in only}
            cards = tf.evaluate(ag, seed=seed, universe=tf.Universe.random(20, seed=seed), model=m,
                                scenario=tf.Scenario.load(scenario) if scenario else None,
                                days=days, steps_per_day=6, ticks_per_step=65, cash=1_000_000.0, max_leverage=2.0)
            return {k: [v.return_pct, v.turnover] for k, v in cards.items()}

        # the packaged recession fires from day 50, so its run is C4b's 60 days (three of the agents)
        paths = {"untraded Engine": untraded, "facts.measure": measure, "evaluate": evaluate,
                 "evaluate, recession": lambda m: evaluate(m, "recession", 60,
                                                           ("buy_and_hold", "momentum_1day", "mean_reversion_1step"))}
        res, differ = {}, []
        for name, fn in paths.items():
            a, b = _digest(fn(models[1.0])), _digest(fn(models[0.0]))
            res[name] = a[:16] + ("" if a == b else " != " + b[:16])
            if a != b:
                differ.append(name)
        paired_cohorts = list(cohorts)

        # 5. live: the switch reorders a cohort, and the recorder sees a cohort of two
        e1 = tf.Engine(seed=seed, universe=tf.Universe.random(20, seed=111), model=models[1.0])
        e0 = tf.Engine(seed=seed, universe=tf.Universe.random(20, seed=111), model=models[0.0])
        steps = [(d, k) for d in range(10) for k in range(6)]
        out_of_order = sum(e1.arrival_order(d, k, ["a", "b"]) != ["a", "b"] for d, k in steps)
        off_sorted = all(e0.arrival_order(d, k, ["b", "a"]) == ["a", "b"] for d, k in steps)

        class Buyer:
            AT = 6 * 1 + 1

            def act(self, obs):
                return {"AAC": 0.10 * obs.avg_volume("AAC")} if obs.step == self.AT else {}
        w = cf.World(seed=seed, universe=tf.Universe.random(20, seed=111), model=models[1.0],
                     agents={"a": Buyer(), "b": Buyer()}, max_leverage=None, cash=1e12)
        w.run(days=2)
        live_cohort = len(cohorts) > len(paired_cohorts)
    finally:
        cf.World.__init__ = init
    paired = (not differ and not paired_cohorts,
              f"seed {seed}, switch 1 vs 0: {res}" + (f"; cohorts of two built: {paired_cohorts[:4]}" if paired_cohorts else "")
              + (f"; DIFFER: {differ}" if differ else ""))
    live = (out_of_order > 0 and off_sorted and live_cohort,
            f"switch on: {out_of_order} of {len(steps)} steps out of label order; switch off: label order on every step"
            f" {'yes' if off_sorted else 'NO'}; a two-buyer World recorded {'yes' if live_cohort else 'NO'}")
    return paired, live


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("box"); ap.add_argument("arm")
    ap.add_argument("--engine", default=os.environ.get("TF_ENGINE", ""))
    ap.add_argument("--seed", type=int, default=3001)
    ap.add_argument("--json", default=None)
    ap.add_argument("--no-run", action="store_true", help="static checks only (checks 4 and 5 fail)")
    a = ap.parse_args(argv)
    commit = open(os.path.join(a.box, "commit.txt")).read().strip() if os.path.exists(os.path.join(a.box, "commit.txt")) else ""
    checks = {}
    checks["engine"] = check_engine(a.engine, commit)
    checks["readers"] = check_readers(a.engine)
    checks["scripts"] = check_scripts(a.box, a.engine)
    if a.no_run:
        checks["paired"] = (False, "not run (--no-run)"); checks["live"] = (False, "not run (--no-run)")
    else:
        checks["paired"], checks["live"] = run_paired(a.box, a.arm, a.seed)
    rec = {"AO5": sum(not ok for ok, _ in checks.values()), "arm": a.arm, "engine_commit": commit,
           "checks": {k: {"pass": bool(ok), "detail": d} for k, (ok, d) in checks.items()}}
    for k, (ok, d) in checks.items():
        print(f"  {k:8s} {'pass' if ok else 'FAIL'}  {d}")
    print(f"{a.arm}: AO5 {'pass' if rec['AO5'] == 0 else 'FAIL'} ({rec['AO5']} of {len(checks)} checks fail)")
    print("AO5JSON " + json.dumps(rec))
    if a.json:
        json.dump(rec, open(a.json, "w"), indent=1)
    return 0 if rec["AO5"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
