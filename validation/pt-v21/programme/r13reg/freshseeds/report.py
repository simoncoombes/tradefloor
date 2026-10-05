"""report.py DIR -- the eighteenth registration's proof report (proof-18.txt; the seventeenth's is proof-17.txt, the sixteenth's proof-16.txt, the fifteenth's proof.txt, the fourteenth's proof-14.txt) from
increment.py's four outputs in DIR (index.jsonl, local.jsonl, s3.jsonl, git.jsonl, and their .log files).

Every record that touches an exam seed of ../grade-seeds.json is listed, per source and kind, with the rule below that
explains it. A seed-context record (a JSON key or argument naming a seed, a SEED or FIRST variable, range(a, b), "seed
N", a file or key name, a commit line beside a seed word) must be one of the listed reservations or guards of this
range, never a run. A bare number (idx-scratch's integer tokens, a textual a-b range) must be a value that is not a
seed. The exam set is clean when every record is explained. Exit 1 otherwise."""
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
R13 = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(R13, "box"))
import seedplan as S  # noqa: E402

SEED_KINDS = {"json-seed", "json-seeds-list", "arg-seeds", "kw-seed", "env-seed", "range-call", "range-call-plus",
              "seed-word", "name", "seed-context", "int"}
# (kinds, a regex on the file or key, a regex on the evidence or "", why it is not a run of an exam seed)
KNOWN = [
    # the eighteenth registration's set (260201-264400)
    ({"range-any"}, r"/scratchpad/reg18doc2?\.py$", r"26[0-4]\d\d\d",
     "the script that wrote the eighteenth registration's text (its seed section), not a run"),
    # the sixteenth registration's set (220201-224400)
    ({"range-any"}, r"/tfd-wt-reg16/programme/r13reg/README\.md$", r"220201-224400",
     "the sixteenth registration's own README naming its exam set, not a run"),
    # the fifteenth registration's set (85201-89400); the fourteenth's rules are in git at registration/fourteenth
    ({"range-any"}, r"/tfd-wt-reg1[45]/programme/ptv20-registration-14\.md$|/tfd-wt-reg14/programme/r13reg/freshseeds/layout\.py$",
     r"83201-93830", "the fourteenth registration's text: where its pools would have landed at +33000 (83201-93830), not a run"),
    ({"name"}, r"/pkgtarget/debug/(incremental|deps)/tradefloor-[^/]*/?.*\.o$|/pkgtarget/debug/deps/tradefloor-[0-9a-f]+\.[0-9a-z]+\.[0-9a-z]+\.rcgu\.o$",
     r"89317", "a Rust build object's hashed file name (5512iyueo89317hf2y9o32dd1.o), not a run"),
    # idx-scratch.jsonl's integer tokens (every integer 9000-99999 in a text file, no seed context)
    ({"tok"}, r"/realism/data/edgar-sub/", r"", "SEC EDGAR submission files: accession numbers, file sizes, CIKs"),
    ({"tok"}, r"/edgar-2024h1\.json$|/sec_tickers\.json$|liquidity-crisis/data/edgar-2026-08-31\.json$|/rchk/x\.json$"
              r"|/fr/snap-copy\.json$", r"", "EDGAR fundamentals: CIK numbers and share counts"),
    ({"tok"}, r"/\.tradefloor/sessions/\.idempotency/|/w9/rawlog\.json$", r"",
     "the local app server's stored responses (session d843ed45, since deleted): prices and quantities"),
    ({"tok"}, r"/scratchpad/tour/run-after/server\.log$", r"",
     "the local app server's log (session d843ed45, since deleted): port and process numbers"),
    ({"tok"}, r"/realism/data/zarinelli2015\.txt$", r"", "the text of Zarinelli et al. 2015 (a paper): numbers in its body"),
    ({"tok"}, r"/gymlib/numpy/", r"", "numpy's own source, vendored into a scratch environment"),
    ({"tok"}, r"/scratchpad/m19\.txt$", r"",
     "a scratch file of 25 scattered integers (since deleted), none beside a seed word: no run's seed list"),
]


def explain(r):
    for kinds, fpat, epat, why in KNOWN:
        if r["kind"] in kinds and re.search(fpat, r["f"]) and (not epat or re.search(epat, r["ev"])):
            return why
    return None


def main(argv):
    d = argv[1]
    gs = json.load(open(os.path.join(R13, "grade-seeds.json")))
    E = sorted({s for p in gs["protocols"].values() for s in S.parse(p["seeds"])})
    out, bad = [], 0
    logs = {}
    for part in ("index", "local", "s3", "git"):
        logs[part] = open(os.path.join(d, f"{part}.log")).read().strip().splitlines()
    out.append(f"exam set {S.fmt(E)}: {len(E)} distinct seeds, span {gs['span']}")
    out.append("")
    out.append("sources (increment.py's logs):")
    for part, lines in logs.items():
        for x in lines:
            if not x.startswith(("s3 read ", "exam set")):
                out.append(f"  [{part}] {x}")
        out.append(f"  [{part}] {[x for x in lines if x.startswith('exam set')][-1].split(': ', 1)[1]}")
    for part in ("index", "local", "s3", "git"):
        recs = [json.loads(x) for x in open(os.path.join(d, f"{part}.jsonl"))]
        out.append("")
        out.append(f"== {part}: {len(recs)} records touch an exam seed")
        groups = collections.OrderedDict()
        for r in recs:
            why = explain(r)
            key = (r["kind"] in SEED_KINDS, why or "UNEXPLAINED")
            groups.setdefault(key, []).append(r)
        for (seedctx, why), rs in sorted(groups.items(), key=lambda kv: (kv[0][1] != "UNEXPLAINED", -len(kv[1]))):
            if why == "UNEXPLAINED":
                bad += len(rs)
            tag = "seed context" if seedctx else "no seed context"
            out.append(f"  {len(rs):5d}  [{tag}] {why}")
            seen = set()
            for r in rs:
                k = (r["kind"], r["a"], r["b"], os.path.dirname(r["f"]) if r["kind"] == "tok" else r["f"])
                if k in seen and why != "UNEXPLAINED":
                    continue
                seen.add(k)
                if len(seen) > (40 if why == "UNEXPLAINED" else 3):
                    break
                out.append(f"         {r['kind']:12s} {r['a']}-{r['b']}  {r['f'][-100:]}")
                if r["ev"]:
                    out.append(f"             {r['ev'][:130]!r}")
    out.append("")
    out.append("verdict: " + ("CLEAN: no record names an exam seed as run; every record touching the set is explained"
                              if not bad else f"NOT CLEAN: {bad} unexplained records"))
    print("\n".join(out))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
