"""layout.py -- the eighteenth grade's fresh exam seeds (pt-v21, R21E1), written as grade-seeds.json (owner decision 8,
2026-09-28, applied again: the thirteenth grade ran 13201-16030, the fourteenth 33201-37400, the fifteenth 85201-89400,
the sixteenth 220201-224400, the seventeenth 240201-244400). The fifteenth's rule with OFFSET 260000, the pools at OFFSET plus 1000, 3000 and 4000 as before, and one more protocol,
ptv21 (the pt-v21 rows, PTV21_SEEDS), whose held-out value 201-230 moves like the cert block's.

    python3 layout.py            print the layout
    python3 layout.py --write    write ../grade-seeds.json (the file seedplan.load_registered reads)

The rule: every exam seed is its held-out counterpart plus OFFSET (85000), except the long run's pool and the arrival
order pool, which take blocks of their own (POOLED), where the fourteenth registration put them. Each protocol keeps
the held-out layout the r15 to r21 screens ran (box/seedplan.py's defaults): the same block sizes, the same first
seeds relative to each other, and the same sharing between protocols (the long run, the true-phase histories, xsec,
R7a and R7a-pre on one 90; the cert cell, R7b, the recession rows and the SF desk on the first 30; D1's pooled
certification seeds on 200 + 160 from the first; the D1 extension's range holding the pre-history, edgegen and
regression-probe blocks; arrival order and the tf.evaluate eval on one 30). Only the numbers move. Since the
eighteenth registration five protocols run three times the seeds (box/seedplan.py): the true-phase histories on three
blocks of 90 (201-290, 501-590, 801-890, each extending the long run's block of 30), the recession rows and the
pt-v21 stage on the first 90, the SF desk's six files and the L-rate probe on the first 36. The held-out pools
(longrunpool +50000/+60000, aopool 43001) moved by 85000 would land on 135201-145830 and 128001-128400, far from the
rest and inside the lite blocks; they take 86201-86830 and 88201-88830 (the held-out layout's three 30-blocks plus
86000 and 88000, each 90 disjoint from every other protocol's seeds, as the screens' pools are) and 89001-89400
instead: OFFSET plus 1000, plus 3000 and plus 4000, the fourteenth registration's places.
Standard library only."""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
R13 = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(R13, "box"))
import seedplan as S  # noqa: E402

OFFSET = 260000
SPAN = (260201, 264400)          # the lowest and highest exam seed
# the pooled protocols whose exam blocks are not the held-out value plus OFFSET (the module docstring)
POOLED = {
    "longrunpool": "261201-261230,261501-261530,261801-261830,263201-263230,263501-263530,263801-263830",
    "aopool": "264001",
}
OUT = os.path.join(R13, "grade-seeds.json")

# the rows each protocol's seeds feed, and the readings taken from another protocol's recording (no seeds of their own)
ALSO = {
    "longrun": "also: rule_cut and the seven levered macro rules against the exposure-matched position (rulecut_lr.py, lever_lr.py), the index-level exploit screens",
    "truephase": "also: the leak macro rules (leakmacro, its first 24)",
    "longrunpool": "with the long run's 90 (LONGRUN_SEED_LIST): the 270 histories C10c, PH5 and edge-c10_mirror are graded on (owner decisions 14-16)",
    "d1pool": "D1's index_tail_dn3_pct (pooled rate) and index_drift_pct (mean) over 360 seeds (owner decisions 11 and 12)",
    "aopool": "e7_arm.py q2/q3 in blocks of 20 and ao_coin.py: AO1 (the book coin, 400 x 420 x 6 = 1,008,000 keys), AO3 and AO4 (owner decision 10)",
    "edgeeval": "eval and evalem on the same seeds: each agent and its exposure-matched constant agent em_AGENT",
    "regr_long": "also: vol_target_15 against the exposure-matched position (regr_em.py) on the same 8 markets",
    "regr_short": "also: vol_target_15 exposure-matched (regr_em.py)",
    "regr_var": "also: vol_target_15 exposure-matched (regr_em.py)",
}
FIXED = {
    "c4b": "92001-92020, universes 93001-93020: the published suite, as held out",
    "c9": "impact_curve.py's own seeds, as held out",
    "scenario_size": "301-330, skipped under GRID=1; not a graded row",
    "regr_fixed": "probe_bond_stale seed 17, probe_invariants seeds 7-17: construction checks",
    "ao5": "ao5.py's paired run on held-out seed 3001: a construction check, not a seed block",
    "rosters": "Universe.random(40, seed=111) certified roster, heldout_universe roster 909, e7_arm's Universe.random(20, seed=111), ph_rows' Universe.random(8, seed=111); a varying roster takes the market seed, so it moves with it",
}


# Every seed block run before this registration (the fifteenth registration's list, "Seeds used before this
# registration"): the held-out layout (every protocol's default, the pools included) as set A and moved by each held-out
# set's offset, the surrogate and audit blocks, the lite and AO blocks, the fixed seeds, and every spent exam set.
HELD_OFFSETS = (0, 20000, 50000, 60000, 70000, 80000)       # sets A, B, C and +60000, +70000, +80000
USED = [(4001, 4980), (5001, 5630), (6001, 7030), (9001, 9012),                     # surrogate and audit
        (40201, 40830), (90201, 190830), (110201, 190830), (300201, 300400), (310201, 10 ** 7),  # lite blocks
        (43001, 43400), (63001, 63400),                                              # AO pools, sets A and B
        (101, 190), (401, 430), (701, 730), (13201, 16030), (33201, 37400),          # exam seeds, spent
        (85201, 89400),                                                              # the fifteenth grade's
        (200001, 201500),        # cycle_equity_hazard_opening's tuning (615624e3, boxes b12t*): the opening phase of
                                 # each seed's engine, read to tune the dial R21D1 sets (engine docs/MODEL.md)
        (220201, 224400),        # the sixteenth grade's exam seeds (R21D1)
        (240201, 244400),        # the seventeenth grade's exam seeds (R21E1)
        # Not a block here: after grade 17 the seed-set check built an engine for each seed 1-400000 and read its day-0
        # economy opening (phase, age; no market history, no row). The eighteenth registration records it; as a block it
        # would leave no seed below the lite blocks (310201 upward). OFFSET follows the sequence (220000, 240000, 260000).
        (230201, 233830),        # set D: PH5's validation histories after grade 16 (ptv21/ph5/readings-E.json),
                                 # the long-run layout at +230000; sets A and B are HELD_OFFSETS 0 and 20000
        (92001, 92020), (93001, 93020), (301, 330), (111, 111), (909, 909), (7, 17)]  # fixed seeds


def used_seeds():
    held = {s for p, v, default, n, what in S.PROTOCOLS for s in S.expand(default, n)}
    out = {s + o for s in held for o in HELD_OFFSETS}
    return out, USED


def layout():
    prot, var = {}, {}
    for p, v, default, n, what in S.PROTOCOLS:
        held = S.expand(default, n)
        if p in POOLED:
            value = POOLED[p]
            exam = S.expand(value, n)
            assert len(exam) == len(held), p
        else:
            exam = [s + OFFSET for s in held]
            value = re.sub(r"\d+", lambda m: str(int(m.group()) + OFFSET), default)   # the held-out value, moved
            assert S.expand(value, n) == exam, p
        prot[p] = {"var": v, "seeds": S.fmt(exam), "n": len(exam), "held_out": S.fmt(held), "reads": what}
        if p in ALSO:
            prot[p]["also"] = ALSO[p]
        var[v] = value
    return prot, var


def main(argv):
    prot, var = layout()
    allseeds = sorted({s for d in prot.values() for s in S.parse(d["seeds"])})
    assert (allseeds[0], allseeds[-1]) == SPAN, (allseeds[0], allseeds[-1])
    assert not S.old_exam(allseeds)
    assert not [s for s in allseeds if 13201 <= s <= 16030], "the thirteenth grade's exam seeds"
    assert not [s for s in allseeds if 33201 <= s <= 37400], "the fourteenth grade's exam seeds"
    assert not [s for s in allseeds if 85201 <= s <= 89400], "the fifteenth grade's exam seeds"
    assert not [s for s in allseeds if 220201 <= s <= 224400], "the sixteenth grade's exam seeds"
    assert not [s for s in allseeds if 240201 <= s <= 244400], "the seventeenth grade's exam seeds"
    # each pooled block is disjoint from every other protocol's seeds (as the screens' pools are)
    for p in POOLED:
        other = {s for q, d in prot.items() if q != p for s in S.parse(d["seeds"])}
        assert not other & set(S.parse(prot[p]["seeds"])), p
    held, blocks = used_seeds()
    assert not held & set(allseeds), "a held-out seed of sets A, B, C, +60000, +70000 or +80000"
    assert not [s for s in allseeds for lo, hi in blocks if lo <= s <= hi], "a seed of a used block"
    both = S.parse(prot["longrun"]["seeds"]) + S.parse(prot["longrunpool"]["seeds"])
    assert len(both) == len(set(both)) == 270
    assert len(S.parse(prot["d1pool"]["seeds"])) == 360 and len(S.parse(prot["aopool"]["seeds"])) == 400
    assert len(S.parse(prot["r7"]["seeds"])) == len(S.parse(prot["r7pre"]["seeds"])) == 90
    doc = {
        "note": ("The eighteenth registration's exam seeds (programme/ptv21-registration-18.md, 'Fresh exam seeds'; "
                 "owner decisions 8 and 10-16). Every exam seed is its held-out counterpart plus 260000, except the long "
                 "run's pool (261201-261830, 263201-263830) and the arrival-order pool (264001-264400), which take "
                 "blocks of their own. No seed of this file had been run by any recorded run when it was registered "
                 "(freshseeds/proof-18.txt). The old exam seeds 101-190, 401-430 and 701-730 are refused always; the "
                 "thirteenth to seventeenth grades' 13201-16030, 33201-37400, 85201-89400, 220201-224400 and 240201-244400 "
                 "are not used."),
        "rule": "exam seed = held-out seed + 260000, per protocol; longrunpool and aopool: blocks of their own",
        "offset": OFFSET,
        "pooled": POOLED,
        "span": f"{SPAN[0]}-{SPAN[1]}",
        "distinct_seeds": len(allseeds),
        "blocks": {"A": "260201-260400", "B": "260431-260590", "C": "260801-260890", "P1": "261201-261230,261501-261530,261801-261830", "X": "262001-262270", "Q": "262501-262534", "E": "263001-263030", "P2": "263201-263230,263501-263530,263801-263830", "AO": "264001-264400"},
        "protocols": prot,
        "variables": var,
        "fixed": FIXED,
        "old_exam_seeds": "101-190,401-430,701-730",
    }
    for p, d in prot.items():
        print(f"{p:11s} {d['var']:20s} {d['n']:4d}  {d['held_out']:26s} -> {d['seeds']}")
    print(len(allseeds), "distinct exam seeds in", doc["span"])
    if "--write" in argv:
        with open(OUT, "w") as f:
            f.write(json.dumps(doc, indent=1) + "\n")
        print("wrote", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
