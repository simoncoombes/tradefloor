# pt-v21, eighteenth registration

Written 2026-10-05, before any exam-seed run. This file, `r13reg/grade-seeds.json`
and the files it names are the registration: the arm, the engine pin, the known
answers, the fresh exam seeds and the proof they are unused, the rows and their
bands, the grade commands and the pass rule. The registration commit is pushed
before the first exam-seed box launches, and nothing here changes after it.

## Why an eighteenth registration

The seventeenth grade (R21E1, `registration/seventeenth` 881e282c, results
ae782593) passed 158 of 159 rows and failed C1 alone: the crash rate in
years 3-21 over years 1-2 read 1.59x against a ceiling of 1.5x. Years 3-21
read 1.265% of sessions under -3%, as on set A (1.258%); years 1-2 read
0.796% (361 sessions over the 90 long-run histories) against 1.387% on set
A. PH5 passed in the form the seventeenth registered (use 0.72; 0.97 in the
per-year 2.0 form).

A check of the seeding after grade 17 found no defect, and the miss a chance
draw. Every engine stream mixes the seed through splitmix64 before seeding
its generator; neighbouring seeds, seed blocks and the five seed sets read
so far (A, B, D and the sixteenth and seventeenth exam sets) show no shared
skew in their openings or early years. R21E1's years 1-2 crash rate over
1,350 histories on those sets is 1.24%, with a standard error of about 0.20
points for 90 histories, because crashes cluster within histories; grade
17's 0.796% sits about 2.2 standard errors under it, and the same set's
pool of 180 histories reads 1.35%. On a fresh set of 90 histories C1 fails
about 3% of the time for this arm.

The owner's standing instruction after a failed grade is a fresh round: the
same arm on fresh exam seeds. This registration is that round. R21E1 is
unchanged. PH5 keeps the Bonferroni form the owner adopted on 2026-10-05
after grade 16 (R21D1, 158 of 159, PH5 at use 1.31 in the per-year 2.0 form
and 0.97 in this one; "Pre-history", below).

The owner's decision on 2026-10-05, made after grade 17: rows move to more
histories, with every band and rule unchanged.

1. Every row read on the long run's 90 histories that can be computed from
   all 270 of the grade's long-run histories (the long run's 90 and the
   pool's 180, which the pool stage records with the same instrument) is
   read on all 270: A1-A3, B1-B8, C1, C2, V1 and B9's annual spread
   (criteria.py, from a long-run report over the 270); CV1, CV3, V2, V3,
   H1-H5, VC4a-f, PH4, PH4b, C10d and the `lever:` rows (grade_all.py); and
   the exploit gate's index-level screens, rule_cut on the long run and the
   long run's seven levered rules (exploits.py). `r13reg/lr270.py` builds
   the 270 from the box's two sets after the seed checks C10c and PH5
   already make.
2. The other noisy rows the seed-set check named run on three times the
   seeds: the true-phase histories 90 to 270 (B10-B12, DV1-DV8, G1-G10,
   C10e-cal, VC1, VC2, T1-T3, C10e, I-rate, R3 held, R3m, R4 on the held
   close, R4m, R4-lag, F-stress and F-bear), the recession rows 30 to 90
   (S1a, S1b, S2), the SF desk's six scenario files 12 to 36 (SF2-SF5 on
   lc, oil, prs, rate, curve and geo), the L-rate probe 12 to 36, and the
   pt-v21 stage 30 to 90 (ON1, U1-U4, O1-O4, SK1, SK2).

Grade 17 read on these terms: C1 on all 270 of its long-run histories
reads 1.08x, which passes; with the long-run rows on the 270 and every
other row as graded, grade 17 reads 159 of 159. Grade 17 stands as graded,
a fail on C1. The change applies from this grade on.

## The candidate

`r13reg/arms/arm-R21E1.txt`: R21E1 on base pt-v20, 104 dials, fingerprint
`custom-2dc32068`, on the engine `ptv21/macro` at
2024f633101be2668b4fd48c14a19a9ba31ef9e6 (dev v0.9.1 with sim/r21 merged,
`ptv21/base` a28a8f79, and the macro anchors). It is the fifteenth
registration's R20M with these sixteen dials changed:

| dial | R20M | R21E1 | mechanism |
|---|---|---|---|
| `cycle_equity_hazard_opening` | 0.007 | 0.011 | PH5's year-0 lift (0.016 in R21D1, grade 16) |
| `earnings_cycle_half_life` | preset | 150 | S2 |
| `treasury_2y_noise` | preset | 0.008 | R1 |
| `policy_anticipation` | 2.0 | 1.8 | R1, the rate rows |
| `overnight_idio_share` | 0.25 | 0.10 | overnight gaps (ON1) |
| `overnight_market_share` | 0.60 | 0.55 | overnight gaps |
| `jump_intensity_market` | preset | 0.005 | jump skew (SK1) |
| `jump_mean_market` | preset | -0.03 | jump skew |
| `jump_sigma_market` | preset | 0.01 | jump skew |
| `unemployment_okun_coefficient` | preset | 0.75 | unemployment anchor (U1) |
| `oil_inventory_reversion` | preset | 0.002 | oil interior |
| `oil_inflation_passthrough` | preset | 1.0 | oil pass-through |
| `order_flow_depth_law` | preset | 1.0 | order-flow laws |
| `order_flow_impact_law` | preset | 1.0 | order-flow laws |
| `order_flow_coefficient` | preset | 800 | order-flow laws |
| `volume_idio_variance_gain` | preset | 0.65 | D1's volume row |

### Known answers the grade box checks

Measured on the desk on 2026-10-04 at 2024f633 (`~/Dev/tf-v21-screen1`, built with
`maturin develop --release`, worktree clean); R21E1's fingerprint and arm line
checked again on the same build on 2026-10-05:

    sim digest (tests/known_answer.py)                 72485a9fb16ba12d633fbc587dc63fb7293852b08219c8a84a8ed2cb40b7634e
    known-answer sha256                                ac004feaa3fecaa325073882a82eb2b5870b6c3ce0d86976a1f91146b1758fb7
    presets, combined                                  87f0b185fb8a84a7038b38065c91034c94e26caaf7e820d1959f292b21c43cc0
    pt-v20's row (tests/known_answer_presets.py)       07ab6e0c1cea8fe42eb5e801afa0c725f7c6e8e6e1e56ba622eaa1efa9577520
    book known answer (tests/known_answer_book.py)     b14d1f50d7d7798554b761a1dc3bc020b18b420ad6d973f6872c2985517d8476
    R21E1's dial set, engine fingerprint               custom-2dc32068
    arm line, sha256 of the text after "R21E1@pt-v20:" c83581c4131455e5b38160ac1797e04bfd80ecba96d7f0281390054eb2216e27

The five known-answer scripts print exactly what origin/dev (v0.9.1) prints on
this build: every pt-v21 switch is silent at zero. Each grade box refuses to
start when its sim digest is not the first line (`wheel.sh`, `SKIP_GATE_IF_KAT`;
the wheel for 2024f633 was built and gated once, `out/cache/wheels/`), and
every shard stops before its grading job when the presets digest is not
87f0b185 or R21E1's fingerprint is not `custom-2dc32068` (`r13reg/box/digests.py`
with `REGISTERED_GRADE` set). The grade is void if any shard's
`known-answer.txt` or `digests.txt` differs.

## What this registration changes, and what it does not

The graders are the seventeenth registration's (`registration/seventeenth`
881e282c, its grade results ae782593), with these changes:

- **The long-run rows on 270 histories** (the owner's decision after grade
  17, item 1 above). `lr270.py` links `longrun/ARM`'s 90 and
  `longrun-pool/ARM`'s 180 into `BOX/lr270/longrun/ARM` after the same
  checks `grade_all.load_pool` makes (each set passes `seedplan.py`'s rule;
  exactly 90 + 180 distinct seeds; the pool's dials are the long run's),
  then writes `longrun.py report`, `v1.py` and an `xsec.json` with B9's
  annual spread over the 270 into `BOX/lr270/`. `grade_all.py` hands those
  to criteria.py and reads its own long-run rows on `load_pool`'s 270;
  `exploits.py` points the index-level screens, `rulecut_lr.py` and
  `lever_lr.py` at the 270. Without a full 270 those rows are not read, a
  fail. PH5 and C10c read the 270 as before; their 90-history readings are
  still reported (`PH5_90`, `C10c_breach_90`).
- **Three times the seeds for the noisy rows** (item 2 above), as five
  protocols in `box/seedplan.py`: `truephase` (`GEN_SEEDS`) 270,
  `recession` (`REC_SEEDS`) 90, `sf` (`SF_SEEDS`) 36, `probe`
  (`PROBE_SEEDS`) 36 and `ptv21` (`PTV21_SEEDS`) 90. The job's default
  values for the five changed in place (no line moved), as did
  `box/setB.env`. The tools take seed lists, so nothing else in the job
  changed. Rules that count seeds read the same share: SF2 and L-rate's
  "ahead in at most 2/3", S1b's "every seed".
- **Fresh exam seeds** (`grade-seeds.json`, `freshseeds/layout.py` at OFFSET
  260000) with the seventeenth grade's exam set added to `USED`, and their
  proof (`freshseeds/proof-18.txt`).
- `rulecut_lr.py` names the number of histories it read in its rule's label.
- `tests/test_registration_18.py` replaces `test_registration_17.py`.

Kept from the seventeenth registration, which changed the sixteenth's
graders:

- **PH5's volatility clause** takes 2.69 combined standard errors per year
  (`grade_all.PH5_VOL_Z = 2.69`, the owner's decision of 2026-10-05). The
  return clause, the 270 pooled histories and every other part of the row
  are unchanged. The per-year 2.0 form is reported as `PH5_vol_alt`.
  `margins.py` reads the same constant.
- **`digests.py`** requires R21E1 at `custom-2dc32068` and no other arm.

Carried from the sixteenth registration, which changed the fifteenth's
graders (`r13reg/`, copied unchanged in 20ea390b) in these ways:

- **The pt-v21 rows.** Eleven rows registered in CRITERIA-pt-v21.md gate this
  grade beside the forty (below): ON1, U1-U4, O1-O4, SK1, SK2, measured by
  `ptv21.py measure --parts free` and graded by `ptv21.py grade` against
  `data/ptv21/real.json`, both at ptv21/criteria 3fdb518c. Their seeds are a
  new protocol, `ptv21` (`PTV21_SEEDS`, `box/seedplan.py`).
- **The graded scenario files.** sf, recession and c4 load the packaged
  scenarios of caaa4c6e, the files the SF, S and C4b rows were banded and
  graded on, not the build's own: the base ships dev's files, which hold the
  corporate yield's level and read SF5 at 0.00 (screen 1). `graded_scenarios.py`
  runs each of the three tools with them in place; `graded-scenarios/SOURCE.json`
  records each file's sha256 (recession.yml 9eac7e1a, as g15 graded).
- **PH1 and PH3 on the base's history API.** The registered `ph_rows.py`
  imports sim/r21's `tradefloor.history`, which 0.8.5+ replaced; the port
  `ph_rows_port.py` (ptv21/tooling 53597c44) runs the same rows on either API
  and gives the original's state hashes on sim/r21.
- **The job runs as shards.** `programme/screen/box/screen_box.py` runs each
  stage of `box/r15-grade-jobs.sh` (the job's preamble, the stage's own lines,
  its closing lines) as a shard with every exam variable and `REGISTERED_GRADE`
  set, so the seed-plan check runs on every shard and sees exactly
  `grade-seeds.json`. Line 78 of the job, a comment, became the
  `PTV21_SEEDS` export; no other line moved, so `screen/stages.json`'s line
  ranges are the fifteenth's.
- `verdict16.py` builds the verdict (the 148 rows and the eleven), unchanged
  here.

Not changed: every band, estimator and rule of the 148 rows (PH5's
volatility multiplier as the seventeenth registered it); the owner decisions
8 and 10-16; the 1-se screen margin is not part of the grade.

## Seeds

### Fresh exam seeds

`freshseeds/layout.py` (the fifteenth's rule, OFFSET 260000) wrote
`grade-seeds.json`: every exam seed is its held-out counterpart plus 260000,
except the long run's pool and the arrival-order pool, which take blocks of
their own (261201-261830, 263201-263830; 264001-264400). 1380 distinct seeds
in 260201-264400:

    longrun      LONGRUN_SEED_LIST          90  260201-260230,260501-260530,260801-260830
    truephase    GEN_SEEDS                 270  260201-260290,260501-260590,260801-260890
    leakmacro    EXPLOITS_LEAK_FIRST        24  260201-260224
    cert         CERT_SEEDS                 30  260201-260230
    heldseeds    HELDSEEDS_SEEDS            30  260501-260530
    heldu        HELDU_SEEDS                30  260801-260830
    crisisext    CRISISEXT_FIRST           270  262001-262270
    edge         EDGE_FIRST_SEED            10  260201-260210
    c4a          C4A_FIRST_SEED              8  260201-260208
    xsec         XSEC_SEEDS                 90  260201-260230,260501-260530,260801-260830
    r7           R7_SEEDS                   90  260201-260230,260501-260530,260801-260830
    r7pre        R7PRE_SEEDS                90  260201-260230,260501-260530,260801-260830
    r7eval       R7EVAL_SEEDS               30  260201-260230
    recession    REC_SEEDS                  90  260201-260290
    sfrec        SF_REC_SEEDS               30  260201-260230
    sf           SF_SEEDS                   36  260201-260236
    mcrun        MC_RUN_SEEDS               30  262501-262530
    mctrips      MC_TRIP_SEEDS               3  262531-262532,262534
    mcmark       MC_MARK_SEED                1  262533
    ao           AO_FIRST_SEED              30  263001-263030
    ph1          PH1_SEEDS                   5  262001-262005
    ph3          PH3_SEEDS                  30  262001-262030
    edgegen      EDGEGEN_SEEDS              30  262001-262030
    edgeeval     EDGEEVAL_SEEDS             20  263001-263020
    regr_short   AUDIT_SEED_SHORT           30  262101-262130
    regr_var     AUDIT_SEED_VAR             30  262201-262230
    regr_long    AUDIT_SEED_LONG             8  262301-262308
    regr_probe   AUDIT_SEED_PROBE            8  262401-262408
    stab         STAB_FIRST_SEED            12  260201-260212
    probe        PROBE_SEEDS                36  260201-260236
    d1pool       D1POOL_SEEDS              360  260201-260400,260431-260590
    aopool       AOPOOL_FIRST              400  264001-264400
    longrunpool  LONGRUN_POOL_SEED_LIST    180  261201-261230,261501-261530,261801-261830,263201-263230,263501-263530,263801-263830
    ptv21        PTV21_SEEDS                90  260201-260290

Seeds used before this registration are layout.py's `USED` list (the
thirteenth to seventeenth registrations' record of every block run) and the
held-out sets A, B, C, +60000, +70000 and +80000 of every protocol. Added to
`USED` for this registration: the seventeenth grade's exam set
240201-244400. Already there: the sixteenth's 220201-224400, set D
(230201-233830), and 200001-201500, the engines whose opening phase was read
to tune `cycle_equity_hazard_opening`. layout.py refuses any overlap.

One read touched this set before it was laid out, and is recorded here
rather than in `USED`. The seeding check after grade 17 built an engine for
each seed from 1 to 400000 and read its day-0 economy opening (phase and age
of the cycle) to test whether the opening depends on the seed in any
pattern. It ran no market history and read no row, and nothing from it
placed this set: OFFSET follows the registrations' sequence (220000, 240000,
260000). As a `USED` block it would leave no seed under the lite blocks
(310201 upward).

### The exam seeds have not been run

`freshseeds/proof-18.txt`: increment.py's local, s3 and git parts on the new
set (every file modified since 2026-09-28 11:00 local under the session
scratchpads and ~/Dev, every S3 key under pretium-calib/ modified since
2026-09-28 15:00 UTC with the text bodies, every commit of the design repo,
the engine, tradefloor-serve and tradefloor-docs), read by report.py. The
2026-09-28 index the earlier proofs read is gone from its scratchpad; before
that date the record is layout.py's `USED` list, which places no block
between 244401 and 300200.

## The grade commands

### The plan

From the registration commit `<sha>` (pushed), in `programme/screen`:

    python3 screen.py plan --arms EMPTY --baseline-arm "$(cat ../r13reg/arms/arm-R21E1.txt)" \
        --stage B --full --registered ../r13reg/grade-seeds.json --registered-grade <sha> \
        --jobs-ref <sha> --out ../r13reg/grade18/plan-g18.json
    python3 screen.py split ../r13reg/grade18/plan-g18.json --boxes 4

Every stage of the job runs once for R21E1 at the registered counts, the
exploit gate's (edgeaudit, regr) included, plus the ptv21 stage: 22 shards,
each with the variables below and `REGISTERED_GRADE=<sha>`. The four plan
files share them across four boxes (us-east-2's spot limit).

### The box

    for P in 1 2 3 4; do
      REGION=us-east-2 TYPE=c8g.16xlarge DEADMAN_MIN=150 CAP=1.5 JOBS_REF=<sha> \
      KAT=72485a9fb16ba12d633fbc587dc63fb7293852b08219c8a84a8ed2cb40b7634e \
      bash programme/screen/box.sh g18p$P ptv21/macro 2024f633101be2668b4fd48c14a19a9ba31ef9e6 \
          programme/r13reg/grade18/plan-g18-box$P.json
    done

A box reclaimed part way is relaunched with the same plan file; shards it
finished are read from the cache, as measured. A shard that fails is re-run
whole on the same seeds and commit.

### Grading, on the desk, after collect

Collect the four boxes, merge them (`programme/screen/box/merge_out.py OUT/box
OUT/g18p1 ... OUT/g18p4`), then, with the engine at 2024f633:

    export TF_PY=~/Dev/tf-v21-screen1/.venv/bin/python TF_ENGINE=~/Dev/tf-v21-screen1 TF_PROGRAMME=<this repo>/programme
    export LONGRUN_SEED_LIST=260201-260230,260501-260530,260801-260830 GEN_SEEDS=260201-260290,260501-260590,260801-260890 EXPLOITS_LEAK_FIRST=260201 CERT_SEEDS=260201-260230 HELDSEEDS_SEEDS=260501-260530 HELDU_SEEDS=260801-260830 CRISISEXT_FIRST=262001 EDGE_FIRST_SEED=260201 C4A_FIRST_SEED=260201 XSEC_SEEDS=260201-260230,260501-260530,260801-260830 R7_SEEDS=260201-260230,260501-260530,260801-260830 R7PRE_SEEDS=260201-260230,260501-260530,260801-260830 R7EVAL_SEEDS=260201-260230 REC_SEEDS=260201-260290 SF_REC_SEEDS=260201-260230 SF_SEEDS=260201-260236 MC_RUN_SEEDS=262501-262530 MC_TRIP_SEEDS=262531,262532,262534 MC_MARK_SEED=262533 AO_FIRST_SEED=263001 PH1_SEEDS=262001-262005 PH3_SEEDS=262001-262030 EDGEGEN_SEEDS=262001-262030 EDGEEVAL_SEEDS=263001-263020 AUDIT_SEED_SHORT=262101 AUDIT_SEED_VAR=262201 AUDIT_SEED_LONG=262301 AUDIT_SEED_PROBE=262401 STAB_FIRST_SEED=260201 PROBE_SEEDS=260201-260236 D1POOL_SEEDS=260201-260400,260431-260590 AOPOOL_FIRST=264001 LONGRUN_POOL_SEED_LIST=261201-261230,261501-261530,261801-261830,263201-263230,263501-263530,263801-263830 PTV21_SEEDS=260201-260290 REGISTERED_GRADE=<sha>
    cd programme/r13reg
    $TF_PY grade_all.py $B --arms R21E1 --out $OUT/ga-R21E1.txt --json $OUT/ga-R21E1.json
    $TF_PY exploits.py $B R21E1 $OUT/expl > $OUT/expl-R21E1.log 2>&1
    $TF_PY exploits_summary.py $OUT/expl R21E1 > $OUT/exploits-R21E1.txt
    $TF_PY <ptv21.py at 3fdb518c> grade $B/ptv21/R21E1 --real <real.json at 3fdb518c> --json $OUT/ptv21-R21E1.json
    $TF_PY verdict16.py $OUT $OUT/verdict-R21E1.json R21E1

`grade_all.py` and `exploits.py` build `$B/lr270` themselves (`lr270.py`);
the commands are the seventeenth's.

## The pass rule

R21E1 passes this grade when every gated row passes on the exam seeds: the
forty registered rows, the eleven pt-v21 rows, every proposed row registered
as gated and every leak row (`grade_all.py`, as for the fifteenth grade), and
the exploit gate (zero exploits over its outputs, `exploits_summary.py`); and
every known answer matches. That is 159 rows. Rows marked "reported" do not
gate.

Nothing is re-tuned, re-seeded or re-read after the grade. If any gated row
fails, the grade is a fail.

## R21E1 on held-out seeds

Screen 4, set A (boxes s4bE1p1 and s4bE1p2, 2026-10-05; `programme/ptv21/results/screen4-B/R21E1`
on ptv21/tooling eefa0d51): 147 of 147 gated rows with the exploit gate
deferred, registered 40 of 40, every noisy row at least 1 se inside its band
(PH5 pooled +1.01 se, B12 +1.07 se, F-bear's share form +2.77 se), the eleven
pt-v21 rows all in. R1 5.41 (90 histories), S2 +76.4 (30 seeds), S1a 0.58,
L1 +12, B9 15.95 / 1.31, D1's volume_abs_return_corr 0.593 / 0.589 / 0.592 /
0.584 and volume_change_acf1 -0.227 / -0.229 / -0.243 / -0.236 on the four
cells. PH5 on its 270 histories: d -0.0044 (se 0.0156); worst year 1, gap
0.0076, use 0.60 under the per-year 2.0 form and 0.45 under this
registration's 2.69. On held-out sets B and D (270 histories each,
`ptv21/ph5/readings-E.json`) the volatility clause read 1.02 and 0.44 under
2.0, 0.76 and 0.33 under 2.69.

Grade 17's exam seeds (240201-244400, spent; `results/ptv21/g17/`): 158 of
159, C1 failing at 1.59x. R1 5.76, S2 +65.2, S1a 0.53, L1 +15, B9 16.06 /
1.05, D1's volume_abs_return_corr 0.579 / 0.596 / 0.591 / 0.591 and
volume_change_acf1 -0.241 / -0.233 / -0.238 / -0.231 on the four cells. PH5
on its 270 histories: d +0.0033 (se 0.0136); worst year 5, gap 0.0112, use
0.72 under 2.69 and 0.97 under 2.0. F-bear's median -0.70. The exploit gate:
0 exploits over 25 outputs and 509 rules.

## The pt-v21 rows

Registered in `programme/longrun/CRITERIA-pt-v21.md` (ptv21/criteria 3fdb518c)
before any mechanism was tuned. Graded by `ptv21.py grade` from the ptv21
stage's 90 free histories of 21 years on the exam seeds (`PTV21_SEEDS`; 30
before this registration);
bands from `data/ptv21/real.json` at the same commit.

| row | statistic | band | R21E1, set A (screen 4) |
|---|---|---|---|
| ON1 | overnight share of name variance, year one, median name | 0.245 to 0.415 | 0.405 |
| U1 | unemployment: share of months within 0.2 pt of the 5-year low | at most 0.287 | 0.183 |
| U2 | unemployment: 5-year range, points | at most 6.012 | 2.281 |
| U3 | unemployment: lag-12 autocorrelation of the monthly level | -0.147 to 0.682 | 0.201 |
| U4 | unemployment: sd of the 12-month change, points | at most 2.222 | 0.787 |
| O1 | oil: 5-year log range | 0.469 to 1.781 | 0.714 |
| O2 | oil: share of months within 2% of the 5-year high or low | at most 0.137 | 0.050 |
| O3 | oil: lag-12 autocorrelation of the monthly log price | -0.578 to 0.751 | -0.021 |
| O4 | oil: sd of the 12-month log change | at most 0.557 | 0.207 |
| SK1 | median one-year skew of daily index log returns | -0.287 to -0.014 | -0.194 |
| SK2 | index sessions under -3% over over +3% (log) | 0.998 to 1.502 | 1.285 |

## The forty registered rows

Unchanged from the fourteenth registration, with its amendments: (a) R7a reads
its all-days baseline from the last close for any model with a night
(`box/r7_event_pre.py`); (b) S1a, S1b and S2 are graded on
`tools/calibration/scenario_candidates/recession_I.yml`, packaged as
`recession.yml`; C9 is read with the arm's own dials; R4 is graded once, on
the held close. Owner decisions 11 and 12 read D1's two level rows over 360
seeds, and decision 13 reads R7a over 90.

The bands, real values and sources are those of `ptv20-registration.md`,
`longrun/CRITERIA.md` and `results/ptv20/bands.json`. The last column is the fifteenth registration's arm, R20M, on held-out set A / set B,
kept as the record it was; R21E1's set-A readings are in "R21E1 on held-out seeds".

| row | statistic | band | real | source | R20M (fifteenth), A / B |
|---|---|---|---|---|---|
| A1 | worst month's volatility, 2008 / 2020 replays with the real VIX | within 30% | 84 / 95 | S&P 500 tape | 98 / 75; 95 / 72 |
| A2 | maximum drawdown, same replays | within 30% | 0.57 / 0.34 | S&P 500 tape | 0.47 / 0.35; 0.50 / 0.36 |
| A3 | peak stock correlation, same replays | within 0.15 | 0.75 / 0.87 | the certified forty | 0.83 / 0.80; 0.84 / 0.80 |
| B1 | time with the VIX (state) above 30 | 1/2x to 2x | 8.2% | VIXCLS 1990-2025 | 5.5% / 5.4% |
| B2 | fear-spell length above 30, sessions | 1/2x to 2x | 22 | same | 19 / 18 |
| B3 | 20% bear markets per decade | 1/2x to 2x | 1.12 | S&P 500 | 1.54 / 1.53 |
| B4 | 10% corrections per decade | 1/2x to 2x | 3.65 | S&P 500 | 4.36 / 4.43 |
| B5 | sessions under -5% per decade | 1/2x to 2x | 6.2 | S&P 500 | 6.8 / 6.3 |
| B6 | time with the VIX under 15 | 1/2x to 2x | 32.6% | VIXCLS | 38.5% / 37.2% |
| B7 | index volatility, % a year | within 20% | 18.1 | S&P 500 | 18.4 / 18.5 |
| B8 | long-run index return, % a year | within 2 points | 6.25 | earnings growth (honest target) | 6.7 / 6.7 |
| B9 | sd of annual returns / start-up drift ratio | 13.9-20.9 / 2/3x-1.5x | 17.4 / 1.0 | S&P 500 1990-2024 | 16.50 / 1.34; 15.97 / 0.83 |
| C1 | crash rate, years 3-21 over years 1-2 | 2/3x to 1.5x | 1.0x | construction | 1.13x / 1.04x |
| C2 | VIX ceiling hits | at most 3 of 90 | 0 | construction | 0 / 0 of 90 |
| C3 | edge from a headline read 5 ticks late | under 20 bp | about 0 | construction | +16.5 / +14.3 bp |
| C4a | 65-minute print return ACF1, or Roll spread | >= -0.05, or <= 2x quoted | about 0 | Roll 1984 | -0.027 / 2.4x; -0.019 / 1.3x |
| C4b | price-only rules on the published suite | <= +5 pts median, <= 14 of 20 | about 0 | construction | -0.4, 8/20 on both |
| C5 | stock-level variance ratio, 60 sessions | 0.80-1.05 | 0.924 | the certified forty 2015-2025 | 0.953 / 0.947 |
| C6 | value signal rank IC, whole / first 60 | -0.03 to +0.05 | +0.009 | SEC XBRL, FRED | +0.006 / -0.003; +0.002 / -0.005 |
| C7 | momentum rank IC, 12-1 / 6-1 | -0.04 to +0.095 / -0.02 to +0.10 | +0.027 / +0.041 | the certified forty | -0.001 / +0.001; +0.002 / +0.001 |
| C8 | Lo-MacKinlay loser-minus-winner, bp a day | -6.4 to +2.9 | -1.74 | the certified forty | -1.0 / -0.6 |
| C9 | cost of size: exponent / coefficient | 0.4-0.7 / 0.33-0.67 | 0.5 / 0.5 | Toth et al. 2011; Almgren et al. 2005 | 0.517 / 0.433 (fixed seeds, both sets) |
| C10 | no public macro signal: 384 rules, and drift after a published turn | median <= +1.0, ahead <= 2/3; >= -5.9 / <= +4.3 | -2.0 (UNRATE rule); NBER turns | FRED, NBER | +0.13, 0.56 / +0.8, -0.1; +0.05, 0.51 / +1.7, -0.4 |
| D1 | every ruled band in, all four cells (crisis dispersion needs 10 readings); index_tail_dn3_pct and index_drift_pct over 360 seeds (decisions 11, 12) | in | -- | facts.REAL_MARKETS | in; crisis dispersion 14, 13, 11, 13 / 11, 13, 13, 14 readings; tail 0.708 / 0.916, drift 7.56 / 6.50 (30-seed cert 1.58 and 0.30 / 0.65 and 7.00, reported) |
| D2 | driven 2020-21: drawdown / sessions back to the high | 0.237-0.441 / 63-252 | 0.339 / 126 | S&P 500 2020-21 | 0.351 / 95; 0.327 / 68.5 |
| E1 | aggregate earnings fall in a contraction | -0.40 to -0.046 | -0.17 | Shiller, NBER 1953-2020 | -0.173 / -0.174 |
| F1 | driven 2020: fall and sessions to it | 0.237-0.441 / 12-46 | 0.339 / 23 | S&P 500 2020 | 0.282 / 28; 0.278 / 30 |
| L1 | price trough leads the earnings trough, sessions | 1 to 136 | 68 | S&P 500, operating earnings | +17 / +29 |
| R1 | 2-year yield daily sd, bp | 3.65-6.80 | 5.23 | FRED DGS2 2015-2025 | 6.44 / 6.46 |
| R2 | 10-year yield daily sd, bp | 4.54-6.27 | 5.41 | FRED DGS10 2015-2025 | 5.52 / 5.58 |
| R3 | corr(index, minus the 10-year's change) | -0.36 to +0.03 | -0.161 | SPY / IEF 2015-2025 | -0.274 / -0.297 |
| R4 | corr(index, minus the IG yield's change), on the held close | +0.15 to +0.39 | +0.272 | SPY / LQD 2015-2025 | +0.366 / +0.353 |
| R5 | driven 2022: index drawdown | 0.178-0.330 | 0.254 | S&P 500 2022 | 0.225 / 0.197 |
| R6 | driven 2022: P/E change per 100 bp of Baa | -10.4 to -2.6 % | -5.2 % | S&P 500 trailing P/E, DBAA | -6.71 / -6.80 |
| R7a | first-bar move after a hike / a cut, less all days, 90 seeds (baseline from the last close) | within 5 bp, or 2 se | 0 / 0 | Gurkaynak, Sack and Swanson 2005 | -0.42 / +0.36; +0.48 / +1.54 on the last-close baseline (-0.59 / +0.19; +0.22 / +1.28 on criteria.py's) |
| R7b | the rate-news agent: pts a year over holding, histories ahead | <= 0, <= 20 of 30 | 0 | construction | -0.86, 2/30; -0.92, 1/30 |
| S1a | recession: share of the fall won back 252 sessions on | 45% to 100% | 62% (2009) | S&P 500 recessions | 0.56 / 0.58 |
| S1b | recession: seeds out of contraction within 24 months | 30 of 30 | all | NBER (longest 18 months) | 30/30 on both |
| S2 | recession: the index's rise in the 252 after its low | +25% to +80% | +69% (2009) | S&P 500 | +73.0 / +75.4 |
| V1 | variance ratio, 2y/1y and 5y/1y | 0.75-1.15 / 0.55-1.20 | 0.93 / 0.87 | Shiller 1946-2023 / 1871-2023 | 0.91 / 0.85; 0.93 / 0.82 |

## Proposed rows, registered as gated

As in the fourteenth registration: read on the long-run histories (years 2
to 21, the certified roster `Universe.random(40, seed=111)`), all 270 of
them since this registration (the long run's 90 and the pool's 180), unless
the row says otherwise; each row is the median over histories unless stated;
definitions are `r13reg/grade_all.py`'s `ROWS` and the estimators beside it.
CV1 at [-1.75, -0.80], Q9 with a 0.80 ceiling, H1 at [-0.69, -0.13], and
B10 to B12 gated, as the thirteenth registration registered them.
DV1-DV8, VC1 and VC2 read the true-phase histories' names (`dv_rows`,
`vc_rows`), 270 histories since this registration.

### Dividends

| row | statistic | band | real | source | R20M, A / B |
|---|---|---|---|---|---|
| DV1 | index total return less the policy rate, log % a year | 4.6 to 8.6 | 6.59 | Ken French Mkt-RF 1926-2025 (Damodaran 6.23) | 6.28 / 6.57 |
| DV3 | dividend yield, cap-weighted, % | 1.3 to 2.5 | 1.81 | Damodaran 2001-2025 (Shiller 1990-2023 2.02) | 1.92 / 1.91 |
| DV4 | delivered buyback yield, log share-count fall a year, % | 1.8 to 3.4 | 2.58 | Damodaran B/P 2001-2025 | 2.35 / 2.31 |
| DV5 | total payout (D+B)/E | 0.56 to 1.04 | 0.80 | Damodaran 2001-2025 | 0.86 / 0.85 |
| DV6 | top minus bottom D/P quintile total return, pts a year, mean over histories | -2.1 to +3.9 | +0.89 (se 1.48) | Ken French D/P portfolios 1927-2025 | +3.21 / +3.33 |
| DV7 | sd of annual aggregate dividend growth, % / share of years with a cut | 3.6 to 14.2 / 0.045 to 0.18 | 7.10 / 3 of 34 | Shiller 1990-2023 | 5.21 / 0.143; 5.50 / 0.136 |
| DV8 | ex-date drop per unit of dividend, median over events | 0.80 to 1.05 | 0.88 | the forty's tape 2015-2025, 1121 events | 0.97 / 0.97 |

Reported only: DV2, the premium over the 10-year bond (real 5.11; R20M 5.11 /
5.24), and DV9, the D/P rank IC (real +0.005; R20M +0.027 / +0.028).

### Crash and volatility state

| row | statistic | band | real | source | R20M, A / B |
|---|---|---|---|---|---|
| CV1 | leverage sum, sum over k = 1..20 of corr(r_t, abs r_t+k) | -1.75 to -0.80 | -1.35 | S&P 500 1990-2025 (CRSP 1926-2025 -1.25) | -1.027 / -0.968 |
| CV3 | skew of non-overlapping 21-session index returns | -1.30 to -0.35 | -0.80 | S&P 500 1990-2025 | -1.121 / -0.739 |

Reported only: CV2, the VIX memory of a fall (real 0.57).

### VIX peaks

| row | statistic | band | real | source | R20M, A / B |
|---|---|---|---|---|---|
| V2 | median published VIX over RV21, sessions with RV21 >= 40, pooled | 0.74 to 0.92 | 0.831 (se 0.045) | S&P 500 and ^VIX 1990-2025 | 0.817 / 0.808 |
| V3 | share of sessions with the published VIX above 50, % | 0.42 to 1.67 | 0.84 | same tape | 1.05 / 1.00 |

Reported beside B1 and B6, which read the VIX state: the same rows on the
published quote (R20M 7.0% and 38.5% on set A, 7.0% and 37.2% on set B).

### Bear dynamics

Read on the true-phase histories (r14gen, 270 x 5292 sessions since this
registration; 90 before).

| row | statistic | band | real | source | R20M, A / B |
|---|---|---|---|---|---|
| B10 | index sd on true contraction and trough sessions over the rest | 1.45 to 2.30 | 1.87 | S&P 500 1928-2025 by NBER USREC | 1.66 / 1.75 |
| B11 | median VIX on those sessions over the rest | 1.25 to 2.00 | 1.62 | VIXCLS 1990-2025 by USREC | 1.50 / 1.50 |
| B12 | share of 20% bears with a true contraction between peak and trough + 63 | 0.45 to 0.85 | 0.64 (7 of 11) | S&P 500 bears 1950-2025, NBER | 0.581 / 0.583 |

### Bond hedge and the Fed

Published rates and CPI, the VIX state.

| row | statistic | band | real | source | R20M, A / B |
|---|---|---|---|---|---|
| H1 | mean policy-rate change over 63 sessions after a VIX >= 30 close (rate >= 0.5, CPI < 4) | -0.69 to -0.13 | -0.411 (se 0.139) | FRED DFF with the VIX tape | -0.635 / -0.584 |
| H2 | share of rate changes at VIX >= 30 and CPI < 4 that are hikes | at most 0.20 | 0 of 9 | FRED DFEDTAR / DFEDTARU 1990-2025 | 0 / 0 |
| H3 | mean 10-year change over 63-session windows with the index down more than 10% | -0.84 to -0.40 | -0.624 (se 0.108) | FRED DGS10 | -0.485 / -0.485 |
| H4 | bond return over index return, same windows | -0.94 to -0.24 | -0.473 | same | -0.281 / -0.286 |
| H5 | monthly corr(index, minus the 10-year's change), blocks starting with CPI < 3 | -0.35 to -0.02 | -0.188 (se 0.082) | FRED DGS10, CPI | -0.308 / -0.307 |

Reported only: rate changes a year (real 3.0; R20M 4.1 / 4.0) and the share
of time at or below 0.25 (real 0.255; R20M 0.21 / 0.21).

### Volatility clustering

| row | statistic | band | real | source | R20M, A / B |
|---|---|---|---|---|---|
| VC1 | idiosyncratic abs-residual lag-1 ACF, 252-return windows | 0.065 to 0.110 | 0.088 (se 0.010) | the real forty 2015-2025 | 0.096 / 0.097 |
| VC2 | aftershock: mean abs residual after a top 2.5% session over the mean | 1.16 to 1.42 | 1.29 (se 0.062) | same | 1.26 / 1.26 |
| VC3 | certification abs_return_acf1, panel_252 median | 0.039 to 0.17 | 0.1025 | facts.REAL_MARKETS_WINDOWS | 0.120 / 0.100 |
| VC4a | cap-weighted index abs-return ACF, lag 1 | 0.158 to 0.305 | 20-year window p10-p90 | CRSP 1926-2026 | 0.284 / 0.266 |
| VC4b | lag 5 | 0.152 to 0.346 | same | same | 0.264 / 0.247 |
| VC4c | lag 20 | 0.077 to 0.245 | same | same | 0.209 / 0.201 |
| VC4d | lag 100 | 0.025 to 0.126 | same | same | 0.075 / 0.086 |
| VC4e | power-law slope, lags 1 to 100 | -0.687 to -0.130 | same | same | -0.350 / -0.324 |
| VC4f | ACF1 of log monthly realised volatility | 0.489 to 0.740 | same | same | 0.692 / 0.703 |

### Earnings and overnight gaps

The first 2660 sessions of each true-phase history's names. Real reference
for every row: the forty 2015-2025 with EDGAR 8-K item 2.02 dates.

| row | statistic | band | real | R20M, A / B |
|---|---|---|---|---|
| G1 | overnight share of name variance, median name | 0.30 to 0.48 | 0.391 | 0.399 / 0.403 |
| G1c | overnight share of the equal-weighted index variance | 0.30 to 0.60 | 0.461 | 0.484 / 0.490 |
| G2 | reaction-day idiosyncratic variance ratio | 6.5 to 14 | 10.2 | 10.7 / 10.6 |
| G2b | reaction days' share of idiosyncratic variance | 0.09 to 0.20 | 0.144 | 0.147 / 0.146 |
| G3 | night's share of reaction-day variance | 0.65 to 0.85 | 0.759 | 0.734 / 0.737 |
| G3b | day-after idiosyncratic variance ratio | 1.2 to 2.4 | 1.71 | 1.66 / 1.69 |
| G4 | idiosyncratic excess kurtosis, median name | 4.7 to 14.1 | 9.4 | 9.6 / 9.7 |
| G7 | median abs gap, bp | 26 to 50 | 37.5 | 36.7 / 37.1 |
| G7b | nights beyond 8x the name's median gap, % | 0.8 to 4.0 | 1.96 | 1.09 / 1.16 |
| G8 | nights opening through a 2x stop, % | 2 to 5 | 3.28 | 3.50 / 3.63 |
| G9 | reaction-day volume ratio | 1.6 to 3.0 | 2.16 | 2.06 / 2.07 |
| G10 | gap continuation, EW session return on EW night return | -0.04 to +0.09 | +0.022 | 0.037 / 0.041 |
| C10e-cal | out through each report: median pts a year, share ahead | at most +1.0, at most 2/3 | -1.48, 11 of 39 | -0.23, 0.46; -0.33, 0.44 |

### Pre-history

| row | statistic | band | real | R20M, A / B |
|---|---|---|---|---|
| PH1 | state after prehistory(N) + T equals an untraded N + T run; forks identical (15 cells, N in 0, 21, 252) | all 15 | construction | 15/15 on both |
| PH2 | pre-history off leaves the digests unchanged | sim 72485a9f, presets 87f0b185, pt-v20 07ab6e0c | construction | all three (desk, caaa4c6e) |
| PH3 | with history_days 252, SMA200 and TSMOM252 active on every scored day | 1.0 | construction | 1.0 / 1.0 |
| PH4 | within-seed corr of log RV20 across seams every 40 sessions | 0.575 to 0.743 | 0.659 (^GSPC 1990-2025) | 0.687 / 0.696 |
| PH4b | the same, RV60 | 0.586 to 0.838 | 0.712 | 0.634 / 0.653 |
| PH5 | stationarity at the start, over 270 pooled long-run histories (decision 15): return clause and volatility clause, below | both hold | construction | pass / pass |

PH5, as the owner restated it on 2026-09-28 (item 2) and pooled on
2026-09-29 (decision 15). Per history, d is the index log return in year 1
less year 0, and v_y is the annualised volatility of year y. The return
clause holds when |mean d| < 2 se(d). The volatility clause holds when, for
every year y from 1 to 7,

    |mean v_y - mean v_0| <= 2.69 sqrt(se_0^2 + se_y^2)

where se_y is the standard deviation of v_y across histories over the square
root of their number, over the 270 pooled histories (`grade_all.load_pool`:
the long run's 90 and the pool's 180; without exactly 270 distinct seeds
the row is not read, a fail). On R20M, set A, under the per-year 2.0 form: d
+0.0021 (se 0.0156); worst year 1, gap 0.0099 against a bound of 0.0124 (80% of it). Set B: d -0.0149
(se 0.0142); worst year 3, gap 0.0094 against 0.0138 (68%). The 90-history
reading is reported (`PH5_90`): pass on set A (0.58 of the bound), fail on
set B (1.12).

The volatility clause's multiplier is 2.69 for each year (`grade_all.PH5_VOL_Z = 2.69`): the clause tests seven
years at once, and 2.69 is the two-sided normal quantile at 0.05 / 7, so a stationary model fails the clause about
5% of the time over the seven years together, where the per-year 2.0 of the thirteenth to sixteenth registrations
fails it about 27% of the time (1 - 0.95^7, years treated as independent). The owner adopted it on 2026-10-05, after
grade 16, whose PH5 read a use of 1.31 under the per-year form (year 2, gap 0.0149 against 0.0114; a fail) and 0.97
under this one (a pass); the return clause held under both. The ruling is recorded with that fact. The per-year 2.0 form is computed and reported as
`PH5_vol_alt`; it does not gate.

### Square-root impact

`metaorder_curve.py`, 12 names a seed, each order forked against a no-trade
twin.

| row | statistic | band | real | source | R20M, A / B |
|---|---|---|---|---|---|
| Q1 | exponent of the print peak in f, half-day schedule, 1-30% ADV | 0.4 to 0.7 | 0.47-0.51 | Zarinelli et al. 2015; Toth et al. 2011 | 0.632 / 0.655 |
| Q2 | print peak at 10% ADV over sqrt(0.1), daily sigma | 0.3 to 1.0 | about 0.46 | Bucci et al. PRL 2019 | 0.381 / 0.393 |
| Q3 | share of the displacement reached halfway through a day TWAP at 10% | 0.62 to 0.82 | 0.71 | square-root shape | 0.709 / 0.709 |
| Q4 | close over peak, half-day orders, f >= 3% | 0.55 to 0.80 | 0.66 | Bucci et al. 1901.05332 | 0.662 / 0.662 |
| Q5 | next close over peak | 0.40 to 0.70 | about 0.55 | same | 0.571 / 0.570 |
| Q6 | implementation shortfall of a day TWAP at 10% ADV, daily sigma | 0.10 to 0.21 | 0.105-0.21 | Toth; Almgren | 0.132 / 0.128 |
| Q7 | day TWAP over one-hour TWAP shortfall at 10% | 0.5 to 0.9 | about 0.63 | Bacry et al. 2015 | 0.792 / 0.782 |
| Q8 | day TWAP over block shortfall at 10% | 0.5 to 0.8 | 0.55-0.64 | Almgren et al. 2005; Bacry et al. 2015 | 0.640 / 0.638 |
| Q9 | the same at 3% | 0.5 to 0.8 | 0.55-0.64 | same | 0.684 / 0.687 |
| G-rt | the best round trip (pump, two-name, block and slice, next-day, alternating, wash) against the twin, bp | at most 0 | every round trip loses | construction | -0.95 / -0.90 |

Reported only: the mark-the-close gain over cost (R20M 34x / 38x; marking the
close is illegal).

### Arrival order

Owner decision 10. Reference for AO1 and AO3: no identity priority (Nasdaq
Rule 4757, NYSE Pillar 7.36-7.37).

| row | statistic | band | real | R20M, A / B |
|---|---|---|---|---|
| AO1 | the book coin: share of (seed, day, step) keys where label a arrives first, over the aopool seeds (400 x 420 x 6 = 1,008,000 keys; read only with at least 10^6) | 0.498 to 0.502 | 0.5 | 0.49929 / 0.49947 |
| AO2 | second arrival's VWAP premium, mean bp, positive on at least 28 of 30 | 10 to 40 | about 24 (Toth 2011) | 15.8, 30/30; 17.5, 30/30 |
| AO3 | label a's advantage between two identical momentum agents, arrival luck removed: intercept over its se, 400 aopool seeds | -3 to +3 | 0 | -1.40 / -0.50 |
| AO4 | Spearman(label rank, execution cost), four identical agents, luck removed: intercept over its se, 400 aopool seeds | -3 to +3 | 0 | -1.00 / -1.01 |
| AO5 | the 40 registered rows are unchanged by the arrival shuffle switch: checks of `ao5.py` that fail, of five | 0 | construction | 0 of 5 on both |

AO1 and AO3/AO4 are read only when `ao-pool/`'s blocks make exactly the
aopool set of `grade-seeds.json` (264001-264400) and at least 400 seeds carry a
luck reading (`grade_all.ao_luck`, `ao_pool_rows`). The 30-seed counts are
reported: AO1n30 13 / 11, AO3n30 19 / 8, AO4n30 -0.02 / -0.13. AO5 is graded
by `ao5.py BOX ARM --engine $TF_ENGINE` exactly as the thirteenth registration
describes it (engine, readers, scripts, paired, live).

## Leak rows

The r13 screen's definitions with the owner's restatements. T1 to T3, C10e,
I-rate, R3 held, R3m, R4m, R4-lag, F-stress and F-bear read the true-phase
histories (270 since this registration); C10c and, since this registration,
C10d read the 270 long-run histories; the SF rows read the SF desk (`box/desk_sf.py`),
each scenario paired against the arm's own no-scenario twin. Every row here
gates.

| row | statistic | bound | real | R20M, A / B |
|---|---|---|---|---|
| T1 | abs re-mark at a true phase turn: median, and share over 1.7% | <= 50 bp, <= 0.10 | NBER announces late | 15.1 bp, 0.007; 16.8 bp, 0.004 |
| T2 | Baa less 10-year spread, daily change: sd, and days a decade over 50 bp | 1.5-6.2 bp, <= 1 | sd 3.1, max 46 (FRED BAA10Y 1990-2026) | 2.93, 0.02; 2.95, 0.02 |
| T3 | meeting step after a true turn less other meetings | <= 25 bp | -- | 1.7 / 2.0 bp |
| C10c | every C10 rule mirrored 2x, against the exposure-matched position, over 270 pooled histories | 0 of 384 breach (median <= +1, ahead <= 2/3) | -- | 0 / 0 of 384 (90 alone: 0 / 1, reported) |
| C10d | drift after a published entry, 63 / 126 sessions, every phase and first cut or hike | in [-5.9, +4.3] / [-10, +8] | Shiller post-war | 0 / 0 cells out |
| C10e | forward 252 excess from 84 sessions into a true contraction | -13.5 to +15.7 | +1.1 (sd 25.3) | +1.0 / +4.5 |
| I-rate | same-close bond index invariant, max error | < 0.01 bp | construction | 0.000 / 0.000 |
| R3 held | R3 on the held close | -0.36 to +0.03 | -0.161 | -0.071 / -0.097 |
| R3m | R3 on monthly returns | -0.40 to +0.10 | -0.13 | -0.264 / -0.278 |
| R4m | R4 on monthly returns | +0.20 to +0.65 | +0.47 | +0.251 / +0.238 |
| R4-lag | corr(index today, IG bond tomorrow) | abs at most 0.10 | +0.044 | -0.038 / -0.035 |
| L-rate | IGCORP and UST10Y sign probes, trading at step 5 and at step 4: mean, and seeds positive | each at most +3% a year and at most 2/3 | -- | A: IGCORP step 5 -0.7 (8/12), step 4 -1.5 (5/12); UST10Y step 5 -0.7 (7/12), step 4 -1.9 (4/12). B: IGCORP -4.9 (2/12) and -2.9 (3/12); UST10Y -5.2 (3/12) and -2.7 (4/12) |
| F-stress cut | P(cut within 42 sessions, VIX 40+, policy rate above 0.25) | 0.50 to 1.00 | 0.90 | 0.998 / 0.995 |
| F-stress hike | P(hike within 42 sessions, VIX 30+) | at most 0.10 | 0.01 | 0.004 / 0.003 |
| F-bear | median policy change from peak to trough of a 20% bear | at most -0.5 | -- | -0.60 / -0.75 |
| SF1 | a forced VIX priced the day it is published | at least 0.75 | -- | 1.04 / 1.05 |
| SF2 | VIX timer on each packaged scenario: edge over holding, share ahead | at most +8, at most 2/3 | 1990-2025 tape | recession +1.2 (4/30) / +0.5 (2/30); every file in, highest +1.6 (1/12) / +0.1 (1/12) |
| SF3 | median over seeds of each seed's worst paired session, each scenario | at least -13% log | -12.8% (2020-03-16) | recession -4.6 / -4.6, liquidity crisis -4.3 / -3.6, rate and curve -8.0 / -7.8 |
| SF4 | closes off the pinned phase inside a phase hold, and one-day troughs | 0 | construction | 0 / 0 |
| SF5 | corporate yield daily sd, onset + 5 to + 250 | 3 to 13 bp | DBAA 5.1-8.5 | 5.0 to 7.4 / 4.3 to 7.7 |
| H1-100y | 100-year stationarity: decade vol within 0.67x-1.5x of decade 0, jump share, floor, tick AC | every decade in | construction | pass / pass (floor hits 0; lowest close 0.062 / 0.171) |
| R7a-pre | R7a on the last-close baseline, hike and cut, 90 seeds | within 5 bp, or 2 se | 0 | -0.42 / +0.36; +0.48 / +1.54 |

H1-100y's price filter, L-rate's one-sided bound, F-stress cut's
conditioning (the unconditioned P(cut) is reported: R20M 0.57 / 0.57) and
SF3's median form are as the thirteenth registration states them. The SF3
minimum is reported (R20M recession -17.7 / -13.1, liquidity crisis -12.1 /
-12.0).

## The exploit gate

As the fourteenth registration states it: the audit's repros
(`r13reg/audit085/edge` and `leaks`, run by `exploits.py`), the regression
probes, and the tf.evaluate agents. A rule is an exploit when its median
annual excess is above +1.0 point AND it is ahead in more than 2/3 of
histories (C10a's bound). The long run's seven levered macro rules and the
regression probes' `vol_target_15` take C10c's bound, against the
exposure-matched position: a breach when the median is above +1.0 point OR
the rule is ahead in more than 2/3. analyse_macro's public timing rules
take C10c's bound against holding; its `true_out` is reported. Every output
`exploits.py` writes is required (25 per arm), with at least R16A's held-out
counts of rules and readings (`exploits_summary.EXPECTED`, `READINGS`). Zero
exploits and zero breaches pass the gate. Since this registration the
index-level screens (`TF_LONGRUN`), rule_cut on the long run and the long
run's seven levered rules read the 270 long-run histories `lr270.py`
builds; the table below is R20M's record on the long run's 90.

edge-c10_mirror (owner decision 16) reads the 270 pooled long-run histories
C10c reads: `exploits.py` links `longrun/R20M`'s 90 and `longrun-pool/R20M`'s
180 into `expl/R20M/c10pool` after checking each seed set against
`seedplan.py`'s rule and that there are exactly 270 distinct seeds, and runs
`c10_mirror.py` with `C10_MIRROR_HISTORIES=270`, which exits with an error
on any other count. Without a full pool the repro is not run and its output
is an error. `exploits_summary.py` counts edge-c10_mirror as an exploit
unless its output says it was read over 270 long-run histories. Its rule and
bound are unchanged: a breach against the exposure-matched position when the
median is above +1.0 or the rule is ahead in more than 2/3; one breach is
one exploit.

R20M on held-out seeds: 25 outputs, 509 rules gated, zero exploits on both
sets. Excess in points a year, median over histories, and the share ahead;
"hold" is reported, the exposure-matched reading grades.

| rule | histories | mean exposure, A / B | hold, A / B | exposure-matched, A / B | C10c |
|---|---|---|---|---|---|
| 2x while pub peak or contraction | 90 long-run | 1.21 / 1.19 | -0.04, 0.49 / +0.46, 0.61 | -0.75, 0.39 / +0.18, 0.53 | pass |
| 2x while pub contraction or trough | 90 long-run | 1.19 / 1.18 | +0.52, 0.62 / +0.99, 0.68 | -0.01, 0.50 / +0.51, 0.60 | pass |
| 2x 126 after a published peak | 90 long-run | 1.06 / 1.06 | -0.11, 0.46 / +0.16, 0.52 | -0.20, 0.40 / -0.05, 0.47 | pass |
| 2x 252 after a published contraction | 90 long-run | 1.14 / 1.14 | +0.60, 0.60 / +0.76, 0.69 | +0.08, 0.53 / +0.34, 0.58 | pass |
| 2x 252 after a first cut | 90 long-run | 1.56 / 1.55 | +0.16, 0.52 / +0.58, 0.57 | -0.75, 0.39 / -0.60, 0.37 | pass |
| 2x 63 after any cut | 90 long-run | 1.38 / 1.37 | -0.20, 0.47 / -0.29, 0.49 | -0.86, 0.37 / -0.87, 0.37 | pass |
| 2x while pub GDP growth < 1% | 90 long-run | 1.14 / 1.14 | -0.06, 0.49 / +0.62, 0.59 | -0.42, 0.41 / +0.30, 0.53 | pass |
| vol_target_15, short probe | 30 | 1.35 / 1.33 | +0.49, 0.50 / +0.44, 0.57 | -2.57, 0.10 / -3.51, 0.27 | pass |
| vol_target_15, var probe | 30 | 1.30 / 1.29 | -0.85, 0.43 / -3.03, 0.30 | -2.90, 0.27 / -6.09, 0.13 | pass |
| vol_target_15, long probe | 8 | 1.33 / 1.33 | +0.37, 0.63 / +0.32, 0.63 | -0.77, 0.38 / -0.49, 0.25 | pass |

rule_cut on the long run's 90: -0.26 exposure-matched, ahead 0.43, on set A
(+1.21, 0.68 against holding); -0.08, 0.48 on set B (+1.18, 0.78). The
tf.evaluate agents against their own exposure (mean, histories ahead), set
A / set B: fedcut63 -0.41, 10/20 / -0.37, 9/20; pubpeakcon +0.11, 12/20 /
+0.05, 9/20; fg40 +0.33, 16/20 / +0.21, 12/20; unemp126 -0.43, 11/20 /
-0.51, 8/20. analyse_macro (medians, histories that beat holding of 24):
`pub_out` -1.50, 6 / -0.94, 5; `unemp_out` -1.49, 5 / -1.14, 9;
`unemp_up_out` -1.44, 5 / -0.83, 8 (`true_out`, reported, +0.22, 17 / +1.20,
16). edge-c10_mirror over 270: 0 of 384 exposure-matched breaches on both
sets. The rule the fourteenth registration's r20 screen failed on,
out_contraction_trough, reads +0.24, ahead 0.589 on set A and +0.38, 0.611
on set B (R20A 0.678 on set A).

## Rows to watch on the grade seeds

Reported for the record; none of this changes the pass rule.

- A rough count over grade 17's 159 rows, with each row's spread taken from
  its standard error or from its readings on three seed sets (set A and the
  sixteenth and seventeenth exam sets) and shrunk by the square root of
  three for every row whose sample triples: the chance that a model like
  R21E1 passes every row on a fresh set rises from between 0.43 and 0.05
  (the reads of grade 17) to between 0.77 and 0.20. The upper figure takes
  the spreads as known; the lower allows for estimating them from three
  readings.
- PH5 is now the largest single risk: a fresh 270 fails it about 13% of the
  time under 2.69 for this arm. Its six 270-history readings so far ran
  from 0.33 to 0.97 of the bound under 2.69.
- C1 on 270 histories fails about 0.01 to 0.03% of the time for this arm (1.07x
  over 1,350 histories; grade 17 read 1.59x on its 90 and 1.08x on its
  270).
- L1 and D2 (about 2% each) and SF2 on the liquidity-crisis file (about 2%
  on 36 seeds) are next; L1 and D2 read the xsec stage's own 90 runs and
  are unchanged. SF2:lc read +7.8 points on set A against +8 and +0.4 on
  grade 17's seeds.
- H4, R4m and F-stress cut sit close to an edge on every set (H4 0.95-0.98
  of its band, R4m 0.92-0.98, F-stress cut 0.9997 of 0.5 to 1.0); on 270
  histories their spreads shrink, their centres do not move.
