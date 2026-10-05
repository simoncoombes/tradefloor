"""screen_box.py: run a screen plan's shards on one box, through the result cache.

    python screen_box.py --plan plan.json --scripts SCR --repo REPO --out OUT \
        --cache s3://BUCKET/pretium-calib/out/cache [--parallel 1] [--verify]

A shard is one stage of the grade job (stages.json) for one arm at one seed
plan. For each shard, in the plan's order:

1. its cache key is (engine build digest, the arm's model digest, stage,
   instrument digest, seed plan); `lib/result_cache.py` gives the first two,
   the instrument digest is sha256 over the stage's script and every file in
   the scripts folder, so any change to a tool starts a new entry;
2. if the cache holds the key, its tgz is fetched and unpacked, and nothing
   runs (this is also how a relaunch after a spot reclaim resumes: every
   finished shard is already in the cache);
3. otherwise the stage runs as the grade job runs it: the job's preamble
   (seed plan check, pt-v20 check, digest gate, arms), the stage's own lines
   unchanged, and the closing lines, with OUT and the arms file pointed at
   the shard; its output is packed and uploaded under the key at once.

When every shard is done, the shard outputs are merged into OUT in the grade
job's layout (merge_out.py), so the desk graders read OUT as they read a
grade box.

`--verify` runs every shard even when it is cached and records, in
OUT/screen-manifest.json, every output file whose content differs from the
cached copy (logs and timing files apart). That is a determinism check on
the build. The reach-map check (a candidate's unreachable stages against the
baseline's) is `screen.py verify` on the merged folder.

The box's role may GetObject anywhere under pretium-calib/ and PutObject
under pretium-calib/out/, and may not list (AWS-CALIBRATION.md section
12.1), so the cache lives under out/cache/ and every read is a `cp` of a
known key; a miss is a failed `cp`. `--local-store DIR` replaces S3 with a
folder, for desk tests.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import time

HERE = pathlib.Path(__file__).resolve().parent
for cand in (HERE.parent / "lib", HERE):
    if (cand / "result_cache.py").exists():
        sys.path.insert(0, str(cand))
import result_cache as RC  # noqa: E402
import merge_out  # noqa: E402

PREAMBLE = (1, 134)
CLOSING = (380, 384)
NEEDS = {"c10": "box", "xsec": "box"}   # both read the box stage's long run from OUT (xsec: B9's annual sd)
#: Files in the scripts folder that do not change what a stage measures: the
#: arm, the plan and the screen's own machinery. Left out of the instrument
#: digest so that, say, a new timing in stages.json keeps every cache entry.
SCREEN_OWN = ("arms.txt", "cert-arms.txt", "plan.json", "stages.json", "screen_box.py",
              "merge_out.py", "result_cache.py", "wheel.sh", "screen-jobs.sh")          # c10 reads the box stage's long run from OUT
VOLATILE = (".log", ".txt", ".err")
VOLATILE_NAMES = ("DONE-JOBS", "j-ptv20-grade.json", "j-box-jobs.json", "j-longrun.json",
                  "seedplan.json", "digests.json", "screen-shard.json")


class Store:
    """The cache's objects: S3 through the CLI on a box, a folder on the desk."""

    def __init__(self, url: str | None, local: str | None):
        self.url, self.local = (url or "").rstrip("/"), local

    def get(self, key: str, dest: pathlib.Path) -> bool:
        if self.local:
            src = pathlib.Path(self.local) / key
            if not src.exists():
                return False
            shutil.copy2(src, dest)
            return True
        r = subprocess.run(["aws", "s3", "cp", "--only-show-errors", f"{self.url}/{key}", str(dest)],
                           capture_output=True, text=True)
        return r.returncode == 0 and dest.exists()

    def exists(self, key: str) -> bool:
        if self.local:
            return (pathlib.Path(self.local) / key).exists()
        bucket, _, prefix = self.url.removeprefix("s3://").partition("/")
        r = subprocess.run(["aws", "s3api", "head-object", "--bucket", bucket,
                            "--key", f"{prefix}/{key}"], capture_output=True, text=True)
        return r.returncode == 0

    def put(self, src: pathlib.Path, key: str) -> bool:
        if self.local:
            to = pathlib.Path(self.local) / key
            to.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, to)
            return True
        r = subprocess.run(["aws", "s3", "cp", "--only-show-errors", str(src), f"{self.url}/{key}"],
                           capture_output=True, text=True)
        return r.returncode == 0


def parse_arm(line: str):
    line = line.split("#", 1)[0].strip()
    head, _, body = line.partition(":")
    name, _, base = head.partition("@")
    if not base:
        raise SystemExit(f"arm {name!r}: a screen arm names its base (NAME@BASE:dials)")
    dials = {k.strip(): float(v) for k, _, v in
             (kv.partition("=") for kv in body.split(",") if kv.strip())}
    return name, base, dials


def model_digest(line: str) -> str:
    import tradefloor as tf  # noqa: PLC0415
    _, base, dials = parse_arm(line)
    return RC.params_digest(tf.ModelParams.from_preset(base, **dials))


def stage_script(jobs: str, stages: dict, stage: str) -> str:
    lines = jobs.splitlines()

    def cut(a, b):
        return lines[a - 1:b]
    body = []
    st = stages["stages"][stage]
    for spec in st.get("lines", []):
        a, b = (int(x) for x in spec.split(","))
        body += cut(a, b)
    # A stage the grade job does not have (the pt-v21 checks) carries its own
    # lines, run after the same preamble.
    body += st.get("script", [])
    # A recorded change to the job's own lines: (old text, new text) pairs,
    # each of which must occur (the graded-scenario runs of sf, c4 and recession).
    for old, new in st.get("rewrite", []):
        if not any(old in line for line in body):
            raise ValueError(f"stage {stage}: rewrite target {old!r} is not in its lines")
        body = [line.replace(old, new) for line in body]
    return "\n".join(cut(*PREAMBLE) + ["", f"# screen shard: stage {stage}"] + body
                     + cut(*CLOSING)) + "\n"


def instrument_digest(script: str, scripts: pathlib.Path, others: tuple = ()) -> str:
    """sha256 over the stage's script and every tool in the scripts folder.

    `others`: tools another stage declares as its own (stages.json
    "own_files"), left out here so that adding a stage keeps every other
    stage's cache entries.
    """
    h = hashlib.sha256(script.encode())
    for f in sorted(p for p in scripts.rglob("*") if p.is_file()):
        rel = f.relative_to(scripts).as_posix()
        if rel in SCREEN_OWN or _owned(rel, others) or "__pycache__" in rel or _apple_double(f):
            continue
        h.update(rel.encode() + b"\0" + hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


def _apple_double(f: pathlib.Path) -> bool:
    """A macOS metadata file ("._name"), which a tgz made on a Mac unpacks into
    on Linux. Its bytes are the packing machine's extended attributes, not a
    tool, and before 2026-10-04 they entered every box's keys."""
    return f.name.startswith("._")


def others_of(stages: dict, stage: str) -> tuple:
    """Files only other stages own, which this stage's digest leaves out.

    An entry ending in "/" is a folder: every file under it.
    """
    mine = set(stages["stages"][stage].get("own_files", []))
    return tuple(sorted({f for name, st in stages["stages"].items() if name != stage
                         for f in st.get("own_files", []) if f not in mine}))


def _owned(rel: str, others: tuple) -> bool:
    return any(rel == o or (o.endswith("/") and rel.startswith(o)) for o in others)


def shard_key(build: str, params: str, stage: str, instr: str, env: dict) -> str:
    seeds = hashlib.sha256(json.dumps(env, sort_keys=True).encode()).hexdigest()
    return f"{build[:16]}/{params[:16]}/{stage}-{instr[:12]}-{seeds[:12]}.tgz"


def shard_scripts(scripts: pathlib.Path, dest: pathlib.Path, line: str) -> pathlib.Path:
    """scripts/longrun as the stage sees it: every tool, and this arm alone."""
    L = dest / "longrun"
    L.mkdir(parents=True, exist_ok=True)
    for f in scripts.iterdir():
        if f.name in ("arms.txt", "cert-arms.txt") or _apple_double(f):
            continue
        link = L / f.name
        if not link.exists():
            link.symlink_to(f.resolve())
    for name in ("arms.txt", "cert-arms.txt"):
        (L / name).write_text(line.strip() + "\n", encoding="utf-8")
    return L


def pack(src: pathlib.Path, tgz: pathlib.Path) -> None:
    with tarfile.open(tgz, "w:gz") as t:
        for f in sorted(p for p in src.rglob("*") if p.is_file()):
            t.add(f, arcname=f.relative_to(src).as_posix())


def unpack(tgz: pathlib.Path, dest: pathlib.Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tgz) as t:
        t.extractall(dest, filter="data")


def content(d: pathlib.Path) -> dict[str, str]:
    """sha256 per output file, leaving out logs and per-run stamps."""
    out = {}
    for f in sorted(p for p in d.rglob("*") if p.is_file()):
        if f.suffix in VOLATILE or f.name in VOLATILE_NAMES:
            continue
        out[f.relative_to(d).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def run(args) -> int:
    plan = json.loads(pathlib.Path(args.plan).read_text())
    stages = json.loads(pathlib.Path(args.stages).read_text())
    jobs = pathlib.Path(args.jobs).read_text()
    scripts, repo, out = (pathlib.Path(args.scripts).resolve(), pathlib.Path(args.repo).resolve(),
                          pathlib.Path(args.out).resolve())
    work = pathlib.Path(args.work).resolve() if args.work else out.parent / "screen-work"
    work.mkdir(parents=True, exist_ok=True)
    store = Store(args.cache, args.local_store)
    build = RC.build_digest(repo)
    digests = {}
    manifest = {"plan": args.plan, "build": build, "started": time.strftime("%H:%M:%S"),
                "shards": []}
    # Every tool the instrument digests read, by content: a key that differs
    # between two runs can then be traced to the file that moved.
    manifest["instrument_files"] = {
        f.relative_to(scripts).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()[:16]
        for f in sorted(p for p in scripts.rglob("*") if p.is_file())
        if f.relative_to(scripts).as_posix() not in SCREEN_OWN and "__pycache__" not in f.parts
        and not _apple_double(f)}
    print(f"screen box: build {build[:16]}, {len(plan['shards'])} shard(s), cache "
          f"{args.local_store or args.cache}", flush=True)

    def one(sh) -> dict:
        line, stage, env = sh["line"], sh["stage"], sh["env"]
        name = parse_arm(line)[0]
        params = digests.setdefault(line, model_digest(line))
        script = stage_script(jobs, stages, stage)
        instr = instrument_digest(script, scripts, others_of(stages, stage))
        key = shard_key(build, params, stage, instr, env)
        sdir = work / f"{name}-{stage}-{key.split('-')[-1][:12]}"
        sout = sdir / "out"
        rec = {"arm": name, "stage": stage, "key": key, "env": env}
        tgz = sdir / "shard.tgz"
        sdir.mkdir(parents=True, exist_ok=True)
        hit = store.get(key, tgz)
        if not hit and args.alias_build:
            old = shard_key(args.alias_build, params, stage, instr, env)
            hit = store.get(old, tgz)
            if hit:
                rec["alias"] = old
        if hit and not args.verify:
            if sout.exists():
                shutil.rmtree(sout)
            unpack(tgz, sout)
            rec["source"] = "cache"
            return rec | {"out": str(sout)}
        if sout.exists():
            shutil.rmtree(sout)
        sout.mkdir(parents=True)
        need = NEEDS.get(stage)
        if need:
            # The shard it reads, from this session (same arm), copied in first.
            for prev in sorted(work.glob(f"{name}-{need}-*/out")):
                shutil.copytree(prev, sout, dirs_exist_ok=True)
        L = shard_scripts(scripts, sdir / "scr", line)
        (sdir / "jobs.sh").write_text(script, encoding="utf-8")
        # `python` in the stage lines is this interpreter, the one with the engine.
        path = f"{pathlib.Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}"
        envr = {**os.environ, "PATH": path, "REPO": str(repo), "OUT": str(sout),
                "SCR": str(sdir / "scr"), "WORKERS": str(args.workers), "GRID": "1", **env}
        t0 = time.time()
        with (sdir / "shard.log").open("w") as log:
            rc = subprocess.run(["bash", str(sdir / "jobs.sh")], env=envr, cwd=str(repo),
                                stdout=log, stderr=subprocess.STDOUT).returncode
        rec.update(source="measured", minutes=round((time.time() - t0) / 60, 2), exit=rc)
        (sdir / "screen-shard.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
        missing = [g for g in stages["stages"][stage].get("outputs", [])
                   if not any(sout.glob(g.replace("{ARM}", name)))]
        if rc != 0 or missing or "REFUSED" in (sdir / "shard.log").read_text(errors="replace"):
            rec["source"] = "failed"
            if missing:
                rec["missing_outputs"] = missing
            return rec | {"out": str(sout)}
        if hit and args.verify:
            cached = sdir / "cached"
            unpack(tgz, cached)
            a, b = content(cached), content(sout)
            rec["verify"] = [k for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
        fresh = sdir / "fresh.tgz"
        pack(sout, fresh)
        if not hit:
            rec["uploaded"] = store.put(fresh, key)
        return rec | {"out": str(sout)}

    shards = plan["shards"]
    if args.expect_hits:
        # An all-cache relaunch: every shard must already be in the cache, so
        # a miss is a wrong key, not work to do. Check them all before any
        # runs, and stop at once rather than measure.
        missing = []
        for sh in shards:
            params = digests.setdefault(sh["line"], model_digest(sh["line"]))
            instr = instrument_digest(stage_script(jobs, stages, sh["stage"]), scripts,
                                      others_of(stages, sh["stage"]))
            keys = [shard_key(build, params, sh["stage"], instr, sh["env"])]
            if args.alias_build:
                keys.append(shard_key(args.alias_build, params, sh["stage"], instr, sh["env"]))
            if not any(store.exists(k) for k in keys):
                missing.append(keys[0])
        if missing:
            manifest["expect_hits_missing"] = missing
            out.mkdir(parents=True, exist_ok=True)
            (out / "screen-manifest.json").write_text(json.dumps(manifest, indent=1))
            print(f"REFUSED: --expect-hits and {len(missing)} of {len(shards)} shard(s) are not "
                  f"in the cache, first {missing[0]}", flush=True)
            return 4
        print(f"expect-hits: all {len(shards)} shard(s) are in the cache", flush=True)
    done: dict[int, dict] = {}
    # c10 waits for its arm's box shard; everything else may run at once.
    first = [i for i, s in enumerate(shards) if s["stage"] not in NEEDS]
    later = [i for i, s in enumerate(shards) if s["stage"] in NEEDS]
    with cf.ThreadPoolExecutor(max(1, args.parallel)) as ex:
        for batch in (first, later):
            futs = {ex.submit(one, shards[i]): i for i in batch}
            for f in cf.as_completed(futs):
                rec = f.result()
                done[futs[f]] = rec
                manifest["shards"].append({k: v for k, v in rec.items() if k != "out"})
                print(f"  {rec['source']:8s} {rec['arm']:12s} {rec['stage']:12s} "
                      f"{rec.get('minutes', '')}", flush=True)
                out.mkdir(parents=True, exist_ok=True)
                (out / "screen-manifest.json").write_text(json.dumps(manifest, indent=1))
    conflicts = merge_out.merge(out, [pathlib.Path(done[i]["out"]) for i in sorted(done)
                                      if done[i]["source"] != "failed"])
    manifest["merge_conflicts"] = len(conflicts)
    manifest["finished"] = time.strftime("%H:%M:%S")
    (out / "screen-manifest.json").write_text(json.dumps(manifest, indent=1))
    failed = [r for r in manifest["shards"] if r["source"] == "failed"]
    differs = [r for r in manifest["shards"] if r.get("verify")]
    print(f"screen box: {sum(r['source'] == 'cache' for r in manifest['shards'])} from cache, "
          f"{sum(r['source'] == 'measured' for r in manifest['shards'])} measured, "
          f"{len(failed)} failed, {len(differs)} differ from the cache, "
          f"{len(conflicts)} merge conflict(s)", flush=True)
    return 1 if failed or differs else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--stages", default=str(HERE.parent / "stages.json"))
    ap.add_argument("--jobs", required=True, help="r15-grade-jobs.sh as the box has it")
    ap.add_argument("--scripts", required=True, help="the scripts/longrun folder")
    ap.add_argument("--repo", required=True, help="the built engine checkout")
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", default=None)
    ap.add_argument("--cache", default=None, help="s3://.../pretium-calib/out/cache")
    ap.add_argument("--local-store", default=None, help="a folder in place of S3 (desk tests)")
    ap.add_argument("--workers", type=int, default=90)
    ap.add_argument("--parallel", type=int, default=1,
                    help="shards at once; 1 is safe on memory (trap 17)")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--alias-build", default=None,
                    help="an earlier build digest (16 hex are enough) whose entries any arm may "
                         "read when this build is the same engine: the same commit keyed by an "
                         "older digest rule, or a build that only adds switches at zero. "
                         "Verify mode checks the claim")
    ap.add_argument("--expect-hits", action="store_true",
                    help="every shard must be a cache hit: check them all first and exit 4 on "
                         "any miss, measuring nothing (an all-cache relaunch)")
    args = ap.parse_args(argv)
    if not (args.cache or args.local_store):
        ap.error("--cache or --local-store")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
