"""increment.py PART OUT.jsonl -- the fifteenth registration's proof (and the fourteenth's before it) that no exam seed
of ../grade-seeds.json has been run: every seed record, in every source, that touches an exam seed. PART is one of

  index   the 2026-09-28 seed-use index (seeduse.py over every source then; scratchpad fresh/*.jsonl, 4.3 million
          records), the thirteenth registration's proof
  local   every file modified since 2026-09-28 11:00 local under the session scratchpads (/private/tmp/claude-501) and
          /Users/simoncoombes/Dev (the design repo and every worktree, with the fleet manifests; the engine checkouts),
          read by seeduse.one: every integer in the last three path components, seed contexts and a-b ranges in text
  s3      every key under s3://BUCKET/pretium-calib/ modified since 2026-09-28 15:00 UTC (11:00 EDT): its name, and its
          body when it is text under 20 MB (seeduse.one); a text key of 20 MB or more is streamed and every integer in
          it that is an exam seed is recorded with its context. Plus every run folder's jobs.log and run.log, whatever
          its date (the task's list of what a run records)
  git     every commit on every branch of the design repo, the engine, tradefloor-serve and tradefloor-docs: git log
          -G for an exam-span number beside a seed or first-seed word

A record touches the exam set when its interval [a, b] holds an exam seed. Ranges wider than 20000 are counted and
not listed (dates, accession numbers: the thirteenth proof's rule). Writes one JSON line per touching record."""
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
R13 = os.path.dirname(HERE)
sys.path[:0] = [HERE, os.path.join(R13, "box")]
import seeduse  # noqa: E402
import seedplan as S  # noqa: E402

SC = "/private/tmp/claude-501/-Users-simoncoombes-Dev/1c44b7cb-8427-4fe5-bd5c-53a9ae2ea1f3/scratchpad"
GS = json.load(open(os.path.join(R13, "grade-seeds.json")))
E = {s for d in GS["protocols"].values() for s in S.parse(d["seeds"])}
LO, HI = min(E), max(E)
CUT_LOCAL = time.mktime(datetime.datetime(2026, 9, 28, 11, 0).timetuple())
CUT_S3 = datetime.datetime(2026, 9, 28, 15, 0, tzinfo=datetime.timezone.utc)
WIDE = 20000


def touches(a, b):
    if b < LO or a > HI:
        return False
    return any(s in E for s in range(max(a, LO), min(b, HI) + 1))


class Out:
    def __init__(self, path):
        self.f = open(path, "w"); self.n = 0; self.wide = 0

    def add(self, src, kind, a, b, where, ev):
        if not touches(a, b):
            return
        if b - a > WIDE:
            self.wide += 1; return
        self.n += 1
        self.f.write(json.dumps({"src": src, "kind": kind, "a": a, "b": b, "f": where, "ev": ev[:160]}) + "\n")


def part_index(o):
    for fn in sorted(glob.glob(f"{SC}/fresh/*.jsonl")):
        n = 0
        for line in open(fn):
            n += 1
            r = json.loads(line)
            if "a" in r:
                o.add("index:" + os.path.basename(fn), r["kind"], r["a"], r["b"], r["f"], r.get("ev", ""))
            else:
                for t in r.get("tok", {}):
                    if t.isdigit():
                        o.add("index:" + os.path.basename(fn), "tok", int(t), int(t), r["f"], "")
                for rg in r.get("rng", []):
                    o.add("index:" + os.path.basename(fn), "rng", rg[0], rg[1], r["f"], "")
        print("index", os.path.basename(fn), n, "records", flush=True)


def part_local(o, skip=()):
    nf = 0
    for root in ("/private/tmp/claude-501", "/Users/simoncoombes/Dev"):
        for f in seeduse.files([root]):
            try:
                st = os.lstat(f)
            except OSError:
                continue
            if st.st_mtime < CUT_LOCAL or "/fresh/" in f or any(s in f for s in skip):
                continue
            nf += 1
            _, recs = seeduse.one(f)
            for k, a, b, ev in recs:
                o.add("local", k, a, b, f, ev)
    print("local files modified since 2026-09-28 11:00:", nf, flush=True)


def part_s3(o):
    import concurrent.futures as cf
    import threading
    import boto3
    sess = boto3.Session(profile_name="<profile>")
    B, PFX = "<bucket>", "pretium-calib/"
    loc = threading.local()

    def cl():
        if not hasattr(loc, "c"):
            loc.c = sess.client("s3", region_name="us-east-2")
        return loc.c
    keys, runs = [], set()
    for page in cl().get_paginator("list_objects_v2").paginate(Bucket=B, Prefix=PFX):
        for x in page.get("Contents", []):
            keys.append(x)
            p = x["Key"].split("/")
            if len(p) > 3 and p[1] == "out":
                runs.add(p[2])
    new = [x for x in keys if x["LastModified"] >= CUT_S3]
    logs = [x for x in keys if x["LastModified"] < CUT_S3 and x["Key"].split("/")[-1] in ("jobs.log", "run.log")
            and x["Key"].split("/")[1] == "out"]
    print("s3 keys", len(keys), "run folders", len(runs), "modified since 2026-09-28 15:00 UTC", len(new),
          "older jobs.log/run.log", len(logs), flush=True)
    for x in new:
        for comp in x["Key"].split("/")[-3:]:
            for m in re.finditer(r"(?<![0-9a-fA-F])(\d{1,7})(?![0-9a-fA-F])", comp):
                v = int(m.group(1))
                o.add("s3name", "name", v, v, x["Key"], comp)
    tmpd = os.environ.get("PROOF_TMP", os.path.join(SC, "r21reg15", "s3tmp")); os.makedirs(tmpd, exist_ok=True)
    text = [x for x in new + logs if os.path.splitext(x["Key"])[1].lower() in seeduse.TXT]
    small = [x for x in text if x["Size"] < 20_000_000]
    big = [x for x in text if x["Size"] >= 20_000_000]

    def read(x):
        k = x["Key"]; ext = os.path.splitext(k)[1].lower()
        tmp = os.path.join(tmpd, f"{abs(hash(k))}{ext or '.txt'}")
        try:
            cl().download_file(B, k, tmp)
            _, recs = seeduse.one(tmp)
        except Exception as e:     # a read that fails is reported, not skipped silently
            return k, None, str(e)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return k, [(kk, a, b, ev) for kk, a, b, ev in recs if kk != "name"], None
    errs = []
    with cf.ThreadPoolExecutor(32) as ex:
        for i, (k, recs, err) in enumerate(ex.map(read, small)):
            if err:
                errs.append((k, err)); continue
            for kk, a, b, ev in recs:
                o.add("s3", kk, a, b, k, ev)
            if i % 5000 == 0:
                print("s3 read", i, "of", len(small), flush=True)
    num = re.compile(rb"(?<![\d.])(\d{5})(?![\d.])")
    for x in big:
        k = x["Key"]
        body = cl().get_object(Bucket=B, Key=k)["Body"]
        tail = b""
        while True:
            chunk = body.read(8 << 20)
            if not chunk:
                break
            buf = tail + chunk
            for m in num.finditer(buf):
                v = int(m.group(1))
                if v in E:
                    o.add("s3big", "int", v, v, k, buf[max(0, m.start() - 60):m.end() + 20].decode("utf8", "replace"))
            tail = buf[-80:]
    print("s3 text read", len(small), "big streamed", len(big), "errors", len(errs), flush=True)
    for k, e in errs[:50]:
        print("  s3 read error", k, e)


REPOS = ["/Users/simoncoombes/Dev/tradefloor-design", "/Users/simoncoombes/Dev/tradefloor",
         "/Users/simoncoombes/Dev/tradefloor-serve", "/Users/simoncoombes/Dev/tradefloor-docs"]


def part_git(o):
    span = "(8[5-8][0-9]{3}|89[0-3][0-9]{2}|89400)"      # 85000-89400, over the exam span 85201-89400
    pat = r"([Ss][Ee][Ee][Dd]|FIRST|[Ff]irst)[^0-9]{0,40}([0-9]+[-, ]+){0,12}" + span + r"([^0-9]|$)"
    for repo in REPOS:
        if not os.path.isdir(repo):
            print("git: no repo", repo); continue
        shas = subprocess.run(["git", "-C", repo, "log", "--all", "--format=%H", "-E", "-G", pat],
                              capture_output=True, text=True).stdout.split()
        nall = subprocess.run(["git", "-C", repo, "rev-list", "--all", "--count"], capture_output=True, text=True).stdout.strip()
        print("git", repo, "commits", nall, "matching", len(shas), flush=True)
        rx = re.compile(pat)
        for sha in shas:
            diff = subprocess.run(["git", "-C", repo, "show", "--format=%s", sha], capture_output=True, text=True,
                                  errors="replace").stdout
            for line in diff.splitlines():
                if line[:1] not in "+-" or line.startswith(("+++", "---")):
                    continue
                for m in rx.finditer(line):
                    v = int(m.group(3))
                    o.add("git", "seed-context", v, v, f"{os.path.basename(repo)}@{sha[:10]}", line.strip())


def main(argv):
    part, out = argv[1], argv[2]
    o = Out(out)
    skip = tuple(argv[3:])
    {"index": part_index, "local": lambda o: part_local(o, skip), "s3": part_s3, "git": part_git}[part](o)
    o.f.close()
    print(f"exam set {S.fmt(sorted(E))} ({len(E)} seeds): {o.n} records touch it; {o.wide} wider than {WIDE} not listed")


if __name__ == "__main__":
    main(sys.argv)
