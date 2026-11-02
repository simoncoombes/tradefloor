# pt-v22, twenty-third registration: DRAFT, not registered

**DRAFT. This is not a registration.** Nothing here binds, no exam seed below
may be run, and no grade may be launched from it. The owner approves the
registration and the grade separately (the standing instruction in
`PLAN-pt-v22.md`, "Registration, grade and release"). It was prepared on
2026-11-01 from the twenty-second registration as registered (`ptv22/regprep`
27be6536, manifest 30c81802, `programme/ptv22-registration-22-DRAFT.md`), after
grade 22 failed on one row (`~/Dev/handovers/v22-grade22-report.md`). The arm and
the pin are registrations 21's and 22's, unchanged: R22-fixd2RWPD (R22-fixd2RWP
plus session 6's deficit-liquidation dial) on `ptv22/candidate` e132a66a, its
stage B R22-fixd2RWP's, reused whole. Only the exam seeds are new (session 4's);
their table is the slot the seed table, filled at the registration commit from
`freshseeds/layout.py`, so that no file carries the new set's numbers before it. What is done and what
needs an owner decision is listed at the end, under "Open before registering".

When registered, this file, `r13reg/grade-seeds.json` and the files it
names will be the registration: the arm, the engine pin, the known answers,
the fresh exam seeds and the proof they are unused, the rows and their bands
and estimators, the grade commands and the pass rule. It follows the
twenty-second registration's text, which follows the twenty-first's, the
twentieth's and the nineteenth's, `ptv20-registration-13.md` and
`ptv21-registration-18.md`. Everything below that this draft does not change is
the twenty-second's, word for word; the sections on registrations 19 to 22 are
kept as the record.

## Why a twenty-third registration

Grade 22 (registration 22 at `ptv22/regprep` 27be6536, manifest 30c81802; arm R22-fixd2RWPD, custom-9ac114fd, on
`ptv22/candidate` e132a66a; 1,650 fresh exam seeds at a seven-digit OFFSET, grade-seeds.json 81ff88f5) ran on
2026-10-31. `grade19.sh` ran all 9 steps with no crash and ended **FAIL, rows fail in: verdict16**. Nothing was
re-run, re-read or re-tuned.

1. **C4a (the 65-minute tape, registered) failed both its lines.** ACF1 read -0.0586 (se 0.0086) against the floor
   -0.05, and the Roll spread 2.05x the quoted spread (21.76 bp against 10.21 bp) against the ceiling 2x; the row
   passes if either holds. It was read by the registered reader on the exam seeds: the row was read, not a reader
   fault. Grades 19 to 21 read 1.62x, 1.56x and 1.92x (pass, pass, pass at usage 0.96).
   - **The cause** (session 3, `v22-c4a-report.md`; rule first, design `v23/sensmap` 6318a9f1): the draw, not the
     deficit dial. C4a reads 8 bare engines of 252 days with no Portfolio and no accounts, and the dial is read only by
     the Portfolio's margin call. The registered arm at the dial's 1 and 0 on 64 screen seeds gives 64 of 64
     bit-identical histories. Session 6 (32 of 32, `c4a_seed` itself) and session 5 (stage-B seed 201) read the same.
   - **How often:** on those 64 histories the arm sits on C4a's ACF line (seed mean -0.052, se 0.004) and its Roll
     leg is a mean of per-history ratios with a heavy tail (4 of 64 read 5.1-6.5x; grade 22's set held a 4.8x and a
     2.8x). An 8-history C4a fails this arm about 0.32 of grades, so it is about a 0.68 row; grades 19 to 22 lie inside
     its spread (median 1.71x, 90th percentile 2.33x).

Everything else passed: verdict16's other 151 gated rows (the registered rows 47 of 48), the pt-v21 rows (ON1 0.342,
G1 0.412, G1c 0.476), the exploit gate (0 exploits in 509 rules), PH2, `amend19.py`'s rows (R3 held -0.139, SF1
1.115, DV1c 6.011, E1 -0.076, PH4 0.669, PH4b 0.607) and `ptv22grade.py`'s 33 gated rows, none unread (MG2 0, NS1
0.473). **Grade 21's failure passed at grade 22:** RF5 read 0.562 (band 0 to 2). PH5 passed (the return clause d
-0.0046, se 0.0149; the vol clause's usage 0.33). Reported, not gated: OF4 1.769, R6 -2.32. The thinnest passing
rows: F-bear (usage 1.00), DV6 0.914, Q4 0.893, R4m 0.883, H5 0.878, Q2 0.872, H4 0.870. Four grades have failed on
four different rows (grade 19 R4m and the unread RF5, grade 20 PH5 and MG2, grade 21 RF5, grade 22 C4a), each at its
edge on a fresh draw.

**What this registration changes for it:** nothing but the exam seeds. The arm, the pin, the rows, the bands and the
estimators are registration 22's; C4a is read as registered (a mean of 8 histories' Roll ratios), and so are RF5,
PH5, R4m, H5, F-bear and Q4. Session 3's option of the Roll leg as a median over histories goes to the v23 gate
proposal (dev-47, 2026-11-01), not to this registration. Grade 22 cost $3.43 (six spot boxes, none reclaimed) and
proof-22's S3 part $0.55.

## Why a twenty-second registration

Grade 21 (registration 21 at `ptv22/regprep` aba0c1ff, manifest d2546f60; arm R22-fixd2RWPD, custom-9ac114fd, on
`ptv22/candidate` e132a66a; 1,650 fresh exam seeds at a seven-digit OFFSET, grade-seeds.json 31357e63) ran on
2026-10-30. `grade19.sh` ran all 9 steps with no crash and ended **FAIL, rows fail in: ptv22grade**. Nothing was
re-run, re-read or re-tuned.

1. **RF5 (a construction row, item 15) read 2.037 against its band 0 to 2.** It was read at the registered horizons,
   63 and 126 sessions, by the registered reader: the row was read, not a reader fault. t was -1.60 at 63 sessions
   and -2.04 at 126 (a mean forecast error of -0.88 bp at 126, se 0.43). It read 0.648 at grade 19 (offline) and
   0.554 at grade 20.
   - **The cause** (session 3, `v22-rf5-report.md`): the draw, on a small real bias. The forecast reverts to
     `forecast_policy_neutral` 2.84%, a little above the model's own mean policy rate (2.63% over 810 histories), which
     predicts an average error of -0.39 bp, exactly what the three grades pooled measure (-0.39 bp at 126 sessions, se
     0.26, t -1.53). Each history's error follows its rate level, and grade 21's set carried slightly more zero-bound
     time (12.7% against 11.6% at grade 20), so it landed at -0.88 bp.
   - **Not the deficit dial:** the futures stage runs no accounts, and a grade-21 history re-run with the dial at 1 and
     at 0 is bit-identical, every recorded field and its RF5 pieces. **Not an outlier:** no history dominates, and
     dropping the largest deviations makes t more negative.
   - **How often:** RF5 is a 2-se test. On a fresh 270 drawn from the pooled 810 it fails a perfectly unbiased
     forecaster with this error structure 5.4% of grades, and this arm about 19% (its mean's uncertainty included).
     Grades 19 to 21 read 0.65, 0.55 and 2.04: one fail in three, as such a row gives.

Everything else passed: verdict16's 152 gated rows (the registered rows 48 of 48), the pt-v21 rows (ON1 0.343, G1
0.408, G1c 0.475), the exploit gate (0 exploits in 509 rules), PH2, `amend19.py`'s rows (R3 held -0.135, SF1 1.050,
DV1c 5.769, E1 -0.095, PH4 0.663, PH4b 0.598) and `ptv22grade.py`'s other 32 gated rows (NS1 0.471). **Grade 20's
two failures passed at grade 21:** PH5 (the return clause d +0.0006, se 0.0134; the vol clause's usage 0.947) and
MG2, which read 0 with the deficit-liquidation dial: no margin call left risk past the next open. Reported, not
gated: OF4 1.625, R6 -2.83. The thinnest passing rows: F-bear (usage 1.00), C4a 0.96, PH5 0.947, Q4 0.935, DV6
0.928, C2 0.889 (8 of 270). Three grades have failed on three different rows (grade 19 R4m and the unread RF5,
grade 20 PH5 and MG2, grade 21 RF5); every other row passed all three times.

**What this registration changes for it:** nothing but the exam seeds. The arm, the pin, the rows, the bands and the
estimators are registration 21's; RF5 is read as registered, and so are PH5, R4m, H5, F-bear and Q4. Grade 21 cost
$5.39 (nine boxes: three spot reclaims, b1 relaunched on demand) and proof-21's S3 part $0.32.

## Why a twenty-first registration

Grade 20 (registration 20 at `ptv22/regprep` 634db5ed, manifest c1310efa; arm
R22-fixd2RWP, custom-ee6aba24, on `ptv22/candidate` 0baefaf4; 1,650 exam seeds
in 290201-299270, grade-seeds.json 6473ef9a) ran on 2026-10-29. `grade19.sh`
ran all 9 steps with no crash and ended **FAIL, rows fail in: verdict16
ptv22grade** (with registration 20's fix both failing steps are named).
Nothing was re-run, re-read or re-tuned.

1. **PH5 (a proposed row) failed on its return clause:** the paired index log
   return, year 1 less year 0, read -0.0293 with se 0.0140, **2.09 se** against
   "under 2 se". The vol clause passed (usage 0.27). At grade 19 the same
   clause read 1.95 se (-0.0256, se 0.0131) and passed; the 90 long-run
   histories alone pass it (PH5_90 1.0), and the registered row reads all 270.
   Nothing in this registration changes PH5: it is read as registered. A fresh
   seed set moves where an arm at the edge reads.
2. **MG2 (a construction row, item 23) read 1 against its band 0:** one margin
   call, in one traded history (exam seed 299215, `stress_IDX_long`, long the
   front index future at about 60 times its equity). The future fell 1.78% on
   day 1109, taking the account below zero (-34,931) at the close, which made a
   call; it rose 2.93% overnight, so at the next open equity (about 0.92M) was
   above the initial requirement (about 0.56M), the call was met by the
   engine's rule (`Portfolio.settle_open`) and nothing was liquidated. The
   registered clause, "no account goes below zero on a derivative loss without
   a liquidation", asks for a liquidation. Not an engine fault (the engine did
   what its margin rule says) and not a reader fault (the reader implements the
   clause word for word): a gap between the two (`v22-optmargin-report.md`,
   "MG2 at grade 20"). Grade 19 read 0: it needs a one-day move larger than the
   initial margin and a night big enough to restore it, one account-day in 270
   histories of 20 traded years.

Everything else passed: the registered rows 37 of 37, the pt-v21 rows 11 of 11
(ON1 0.346, G1 0.411, G1c 0.480), the leak rows 44 of 44, the proposed rows 58
of 59 (PH5), the exploit gate (0 exploits in 509 rules), PH2, `amend19.py`'s
rows (R3 held -0.152, SF1 1.021, DV1c 5.844, E1 -0.090, PH4 0.665, PH4b 0.606)
and `ptv22grade.py`'s other 32 gated rows. **Grade 19's two failures passed at
grade 20:** R4m 0.2129 (usage 0.943) and RF5, read at its registered horizons
this time (0.554, band 0 to 2; item 26). Reported, not gated: R6 -2.57, OF4
1.158. The thinnest passing rows: F-bear (usage 1.00), R4m 0.943, H5 0.934, Q4
0.932, R7a 0.911. So the arm sits at the edge on several rows, and a fresh seed
set moves which ones fail.

**What this registration changes for it:** MG2's gap is closed in the engine,
as the registered clause says it should be: session 6's deficit-liquidation
dial (item 31: a margin call made with the account's equity below zero is not
met by a recovery, and the next open liquidates), on a new pin (session 1's).
Nothing else changes: PH5, R4m, H5, F-bear and Q4 are read as registered.
Grade 20 cost $3.47 (six spot boxes) and proof-20's S3 part $0.29.

## Why a twentieth registration

Grade 19 (registration 19 at `ptv22/regprep` af83eb11, manifest be501fcc; arm
R22-fixd2RWP, custom-ee6aba24, on `ptv22/candidate` 0baefaf4; 1,650 exam seeds
in 280201-289270) ran on 2026-10-28. `grade19.sh`'s first attempt stopped at
`grade_all` (rc 3) on a usage error, the arm file's path given for ARM, before
reading any row; it is kept (`grade-attempt1-armpath/`). The second attempt ran
all 9 steps with no crash and ended **FAIL**, on two rows. Nothing was re-run,
re-read or re-tuned.

1. **R4m (a leak row, gated) read 0.1952 against its band 0.20 to 0.65**,
   0.0048 under the floor (usage 1.021). Before the grade, fallback F's joined
   gate gave R4m a chance of **0.931**, its only row under 0.95 (stage B's
   decide: "R4m's 30 under its floor"); the free gate passed it, and the grade
   failed it. The registered arm's R4m chance is the number this registration turns on.
2. **RF5 was not read: a reader fault.** `ptv22grade.py --rf5 63,126` (item 15)
   rewrote only the horizon check in `futrows.rows`. The statistic,
   `passprob_futures._unbiased_stat`, still looped over every horizon in the
   record, and the 252-session error is NaN in all 270 histories, so RF5 read
   NaN and an unread gated row fails. Read at 63 and 126 on the same 270
   pieces, RF5 is 0.648 (band 0 to 2). It was not patched after the
   registration (a reader changed after the registration commit is a new
   registration). The dry run missed it: its synthetic pieces carried a 252
   value. Registration 20 adds the case (pieces NaN at 252).
3. **The grade's summary undercounted.** `verdict16.py` exited 0 on R4m's
   failure, so `grade19.sh`'s FAIL line named only ptv22grade; the verdict file
   said fail. No reading was affected.

Everything else passed: the registered rows 37 of 37, the pt-v21 rows 11 of
11, the proposed rows 59 of 59, the leak rows 43 of 44 (R4m), the exploit gate
(0 exploits in 509 gated rules over 25 outputs), `amend19.py`'s rows,
`ptv22grade.py`'s other 32 gated rows and PH2's three known answers. The
four thinnest passing rows: F-bear (usage 1.20), H5 0.957, C3 0.91, Q4 0.904.

The rows of items 20-25, as grade 19 read them:

| row | reading | band | | note |
|---|---|---|---|---|
| R3 (item 4, the held close) | -0.162 | -0.36 to 0.03 | pass | the last print read -0.370, superseded and not counted |
| SF1 (item 3, ratio of medians) | 1.017 | at least 0.75 | pass | median of per-seed ratios 1.025, superseded |
| DV1c (item 7) | 6.486 | 3.7 to 7.7 | pass | DV1 6.172, superseded |
| PH4 / PH4b (item 20) | 0.662 / 0.601 | 0.574-0.790 / 0.514-0.781 | pass | offset 0: 0.661 / 0.600, superseded |
| E1 (item 25) | -0.083 | -0.40 to -0.046 | pass | registered E1 -0.111, superseded |
| ON1 (item 25(h)) | 0.336 | 0.245 to 0.415 | pass | |
| NS1 (item 25) | 0.470 | 0.3 to 0.6 | pass | |
| G1 / G1c | 0.410 / 0.476 | 0.30-0.48 / 0.30-0.60 | pass | |
| R6 (item 25(i)) | -2.52 | -10.86 to -2.72 | outside | reported, not gated |
| OF4 (item 21) | 1.392 | 0 to 2 | inside | reported, not gated |
| R4m (leak) | 0.1952 | 0.20 to 0.65 | **fail** | |
| RF5 (item 15) | not read | 0 to 2 | **unread** | 0.648 at 63 and 126 |

### What was tried after grade 19, and why it was not taken

Session 3 read four arms on R22-fixd2RWP against R4m's risk (design `ptv22/r4m20-v22`, rule 78790568 and
amendment A c1ccab01, committed before any box or read; `v22-r4m20-report.md`). Each adds dials already measured or
tuned and recorded, no new value: R4mA the credit spread's lagged leverage term (measured); R4mB A plus H5G (tuned,
recorded); R4mC B plus the 2-year; R4mD A plus the 2-year. On the gate box's 270 (8701-8970) and the scenarios
(9001-9200), against v21 and the base:

| | R22-fixd2RWP | R4mA | R4mB | R4mC | R4mD |
|---|---|---|---|---|---|
| R4m on the 270 (band 0.20-0.65) | 0.220, chance 0.938 | 0.300, 1.000 | 0.309, 1.000 | 0.310, 1.000 | 0.289, 1.000 |
| joint chance (amendment A's table, 189 rows) | 0.642 | 0.584 | 0.725 | 0.826 | 0.374 |
| rows under 0.95 | H5 0.782, R4m 0.938 | H5 0.665 | H3 0.865 | none | H5 0.453, OF1 0.937 |
| v21 test, with the base clause | passes | fails: H5 | passes | fails: V3 | fails: H5 |

The owner chose R4mB, the one arm that moves R4m without a row turning worse than v21. **Its stage B failed** (fresh,
every stage kind on its own line; session 3, $4.74 spot):
- **C2**, the VIX ceiling: 4 of 90 long runs reach it, against at most 1 of 30 (chance 0.000; RWP's stage B had one).
  H5G's ten-year noise pushes the VIX tail up (V3 1.09 on the 270, against RWP's 0.96).
- **C4a**, the 65-minute return: ACF1 -0.052 and Roll 2.1x, against ACF1 at least -0.05 or Roll at most 2x (chance
  0.000), both clauses missed narrowly.
- H3 0.855, under the gate's 0.9.

So R4mB could not be registered, and nothing was re-tuned to rescue it. The owner chose to regrade the registered arm
R22-fixd2RWP on the same pin: its R4m chance is about 0.94 (0.938 on the gate box's 270 under the item-25 readers,
0.931 in fallback F's joined gate), and its H5 reads 0.782 on the gate box's 270 (0.987 on stage B's own pool; the
grade read H5 as a thin pass, usage 0.957). A v23 change to the VIX tail and the credit term together is the other
road (`v23-plan-DRAFT.md`).

Grade 19's exam seeds are spent. Registration 20 is a new registration: the same arm on the same pin, regraded on
fresh exam seeds (item 28), with the grade job's fixes (items 26 and 27 below) and the free gate's readers pinned
(item 29). The rows, bands and estimators are the nineteenth's otherwise. Grade 19 cost $4.34 (six boxes, the gate on demand).

## Why a nineteenth registration

pt-v22 adds index, rate and oil futures, single-stock options, margin and the
core model changes (owner, 2026-10-15, "option a": VIX futures and index
options ship off and move to v23). A new preset is a trajectory change, so
the underlying's 159 rows are re-read on fresh exam seeds, not carried by
NS3 (plan, revised 2026-10-10), and the new rows are graded beside them.

The owner's rules for the candidate: no row further from its real value than
pt-v21 (R21E1), no band-fitting, every gate chance at least 0.9 and the
joint chance at least 0.5 on the free gate before registering.

## The candidate

    arm              R22-fixd2RWPD: R22-fixd2RWP (registrations 19 and 20's arm, 208 dials) plus session 6's
                     deficit-liquidation dial `margin_deficit_liquidates` 1.0 (item 31)
    base             pt-v20 of the same build, with 209 dials over it
    engine branch    ptv22/candidate (pushed, no PR)
    engine commit    e132a66acbdec409f8f0d48875bdec068adbdd68 (session 1's: 0baefaf4 fast-forwarded to session 6's `ptv22/deficit-liquidates`, nothing else in; full check v22pin3)
    arm line         programme/r13reg/arms/arm-R22-fixd2RWPD.txt (209 dials, the registered
                     line; none at its pt-v20 value)
    line sha256      52c92c912f8b1a9d983ff9ea1860251c283e588b0796002066479972ee4685c0 (the text after "R22-fixd2RWPD@pt-v20:")
    fingerprint      custom-9ac114fd (`digests.py compute` at the pin)

Registrations 19 and 20's candidate, as registered: R22-fixd2RWP (208 dials) on `ptv22/candidate` 0baefaf4, line
sha256 02e4cc0b, fingerprint custom-ee6aba24. The engine and dial sections below are its own; this arm is it plus
the one dial, on the new pin (e132a66a: 0baefaf4 plus session 6's one commit, the switch `margin_deficit_liquidates`, 0 on every preset and silent in the digest at 0; it adds no platform-maths call in Rust).

### The engine

`ptv22/candidate` 0baefaf4 is `ptv22/combined` 7c48a2da (every v22 engine
branch to 2026-10-26, `v22-combine-report.md`) with one merge commit each, in
dev-47's order (`v22-candidate-report.md`):

| # | merged | head | merge | carries |
|---|---|---|---|---|
| 1 | `ptv22/twoyear` | bfdf7a73 | d5191eee | `treasury_2y_expected_path` (0.0 on every preset; not set in the arm) |
| 2 | `ptv22/opt-margin` | e2806a32, then e92b9e00 and 3c3c8eed | 135f0b5c, f49e9420, df4f2e4b | `margin_option_scan_range`, `margin_liquidation_worked` (items 22-23); the range's preset test leaves pt-v22 to its arm |
| 3 | `ptv22/evtenor` | e821fff3 | e8f0da86 | `surface_name_term_slope` (0.0; not set: the measured slope stopped by its rule, `v22-evtenor-report.md`); carries `ptv22/evmove` 1bae0b58 (`surface_name_earnings_premium`, 0.0; not set) |
| 4 | `ptv22/preset` | 8429c455 | d496eacd | the pt-v22 preset row (`tools/presets/arm_preset.py`); one conflict in provenance's `OUT_OF_SCOPE`, both sides kept |
| 4 | `ptv22/release-wording2` | b7f77308 | 1e889181 | CHANGELOG and MODEL wording |
| 5 | `ptv22/replay-pin` | 262fcdbc | 0f471efd | `pinned_vix_return` (0.0 on every preset; 0.005375 in the arm) |
| 6 | `ptv22/recession-file` | 73656274, then 3c8339b1 | 2c69b815, 43f4ed9a | `recession_2008`, the graded 2008 recession shipped as its own scenario (`recession` and the fingerprint batteries unchanged), and test_bonds' case for a scenario that widens credit on the spread; Python, YAML and tests only |
| 7 | `ptv22/preset` | c9fd9d9c, then 821930f8 | c396e98c, 0baefaf4 | the record's reported rows (OF4, R6) and its exceptions (ON1, E1); Python and docs only |

Every merge's added Rust was checked by hand for platform maths calls (none);
each new dial is 0.0 on every preset and left out of the digest there.

Checks: on 0baefaf4, box v22pin2 (2026-10-28): cargo test 920 passed, 0 failed; pytest 8,121 passed, 0 failed; all five known answers unchanged; prices bit-identical to edec6abf (9001 23 of 23, 8701 46 of 46, the names 27 of 27). Before it, on 0f471efd, box v22cand3: cargo test 920 passed,
0 failed; pytest 8,110 passed, 0 failed (544 skipped, 1 xfailed); all five
known answers unchanged (below); prices bit-identical to edec6abf, R22-reg19n's
engine: seed 9001 over 21 years 23 of 23 checkpoints, 8701 46 of 46, the names
27 of 27, and the gate box's 8701. df4f2e4b adds one test-only commit, checked
on the desk (6 of 6).

At the registration the pin carries the arm either as `custom-...` or as the
swapped pt-v22 preset (`arm_preset.py --arm programme/r13reg/arms/arm-R22-fixd2RWP.txt --graded`; the
swap's commit needs provenance entries for `margin_option_scan_range` and
`margin_liquidation_worked`, `v22-regdryrun-report.md` blocker 2). The swap
moves no price: the presets' rows are unchanged for every earlier preset.

### The dials

208 dials: R22-reg19n's 194 (the two tables after this one) and these 14 since it, none
at its pt-v20 value and no dial set by two sources with different values:

| dial | value | source |
|---|---|---|
| `policy_guidance_signal_sd` | 6.4759 | fix round 2 (item 14; `v22-fixround-report.md`, design `ptv22/fixround` daf7d781): replaces the eve dial |
| `rate_premium_per_fixing` | 1.0 | fix round 1, engine b2cf72ff: the premium re-derived per fixing on its own tape (k 3.1679, p 1.3235, within 0.39 se) |
| `margin_option_minimum` | 0.375 | fix round 2: FINRA Rule 4210's minimum (round 1's 0.10 failed MG3) |
| `margin_pretrade_check` | 1.0 | fix round 1, engine 2d457b24: switch, FINRA Rule 4210(b), Reg T 12 CFR 220.4(c) |
| `option_dealer_spread` | 0.02 | fix round 1, arm only: Muravyev and Pearson (2020), 7.2% ATM effective half-spread (large caps 5.7%) |
| `option_position_limit` | 100.0 | fix round 1, arm only: under every Cboe Rule 8.30 tier |
| `option_name_inventory_slope` | 0.000138 | the name dealer, measured on the tape (`v22-namedealer-report.md`, rule 13baa7ff) |
| `option_name_inventory_half_life` | 11.4 | the name dealer, measured |
| `option_name_spread_otm` | 1.431 | the name dealer, measured |
| `option_name_spread_itm` | 0.724 | the name dealer, measured |
| `option_name_spread_tenor_exponent` | -0.167 | the name dealer, measured |
| `margin_option_scan_range` | 0.15 | item 22: FINRA Rule 4210(g)(2)(F)'s range for an equity, as written |
| `margin_liquidation_worked` | 1.0 | item 23: switch, the liquidation worked as a broker works a market order |
| `pinned_vix_return` | 0.005375 | measured: the S&P 500's same-day log return on the VIX's change, -0.5375% a point (se 0.0249, 1990-2025; `v22-replays-report.md`) |


R22-reg19n's 194:

| source | dials | what |
|---|---|---|
| R22O5czNU-R1FTNLT (design ptv22/nu-r1 4c3c59cb) | 166 | the underlying arm, as `nu-r1` holds it (its provenance: `results/r22o5czN-B/nur1/RULE.md` and `RESULT.md`) |
| ptv22traded.py PHASE1 listing switches (design ptv22/futures-rows 56358419, futrows --switches phase1) | 7 | the phase 1 listing switches and the basis (`ptv22traded.PHASE1` less the VIX futures) |
| R22O5czNUf5n futures settings (design ptv22/futures-rows 56358419) | 8 | the futures' own settings, as `futures/RULE-NU.md`, `RULE-IF4.md`, `RULE-BASIS.md` set them |
| R22O5czNUopt4np22 single-stock options settings (design ptv22/name-options-rows 5815ad61) | 16 | the single names' options and the surface they read, index options unlisted |
| the option scan's tail (design ptv22/reg19-fix2 2bf28b0f, `fix2/optscan/RULE-optscan.md`) | 1 | `margin_scan_option_tail`, derived as `margin_scan_tail` was |
| company news before the open (design ptv22/overnight-v22 cc20885a, `RULE.md`; engine 6a219eea) | 1 | `news_preopen_share`, measured on the tape |

The 33 dials beyond R22O5czNU-R1FTNLT's 166 (five of them at pt-v20's value, so the registered line has 194):

| dial | value | source |
|---|---|---|
| `futures_index_listed` | 1.0 | ptv22traded.py PHASE1 listing switches |
| `basis_sd` | 3.753 | ptv22traded.py PHASE1 listing switches |
| `basis_persistence` | 0.429 | ptv22traded.py PHASE1 listing switches |
| `night_session_steps` | 10.0 | ptv22traded.py PHASE1 listing switches |
| `futures_rates_listed` | 1.0 | ptv22traded.py PHASE1 listing switches |
| `futures_oil_listed` | 1.0 | ptv22traded.py PHASE1 listing switches |
| `margin_scan_coverage` | 0.99 | ptv22traded.py PHASE1 listing switches |
| `futures_impact_memory` | 1.4 | R22O5czNUf5n futures settings |
| `futures_impact_memory_half_life` | 780.0 | R22O5czNUf5n futures settings |
| `futures_vix_listed` | 0.0 (pt-v20's value; written in arm-R22-reg19.txt only) | R22O5czNUf5n futures settings |
| `futures_vix_live_fast_share` | 0.0 (pt-v20's value; written in arm-R22-reg19.txt only) | R22O5czNUf5n futures settings |
| `futures_vix_live_fast_half_life` | 0.0 (pt-v20's value; written in arm-R22-reg19.txt only) | R22O5czNUf5n futures settings |
| `futures_vix_live_slow_half_life` | 0.0 (pt-v20's value; written in arm-R22-reg19.txt only) | R22O5czNUf5n futures settings |
| `margin_scan_tail` | 1.258 | R22O5czNUf5n futures settings |
| `basis_night_share` | 0.761 | R22O5czNUf5n futures settings |
| `surface_ssvi` | 1.0 | R22O5czNUopt4np22 single-stock options settings |
| `surface_skew_physical` | -0.698 | R22O5czNUopt4np22 single-stock options settings |
| `surface_skew_physical_slope` | -0.51 | R22O5czNUopt4np22 single-stock options settings |
| `surface_curvature` | 1.0 | R22O5czNUopt4np22 single-stock options settings |
| `surface_curvature_exponent` | 0.5 | R22O5czNUopt4np22 single-stock options settings |
| `options_index_listed` | 0.0 (pt-v20's value; written in arm-R22-reg19.txt only) | R22O5czNUopt4np22 single-stock options settings |
| `options_names_listed` | 1.0 | R22O5czNUopt4np22 single-stock options settings |
| `surface_name_skew` | 0.5 | R22O5czNUopt4np22 single-stock options settings |
| `surface_name_premium` | 0.22 | R22O5czNUopt4np22 single-stock options settings |
| `surface_skew_premium_slope` | 1.12 | R22O5czNUopt4np22 single-stock options settings |
| `surface_skew_premium` | -1.15 | R22O5czNUopt4np22 single-stock options settings |
| `surface_term_premium_short` | -0.08 | R22O5czNUopt4np22 single-stock options settings |
| `surface_term_premium_long` | 0.35 | R22O5czNUopt4np22 single-stock options settings |
| `surface_earnings_weight` | 0.90 | R22O5czNUopt4np22 single-stock options settings |
| `surface_skew_noise_sd` | 1.155 | R22O5czNUopt4np22 single-stock options settings |
| `surface_skew_noise_half_life` | 346 | R22O5czNUopt4np22 single-stock options settings |
| `margin_scan_option_tail` | 0.800 | derived: held-out 8501-8524, 1,500 sessions, the 0.99 quantile of the short strangle's move over its scan margin (0.8003), adopted unchanged; owner ruling 2026-10-23 that this method counts as derived |
| `news_preopen_share` | 0.259 | measured: the night share of a name's own variance on news days on the tape (se 0.013), set directly, not tuned (`v22-overnight-report.md`) |

R22O5czNUf5n's eight forecast dials (`forecast_horizon_sessions` 252 and the policy and VIX forecast dials derived on
NU's law) are not taken. The futures branch's own rule says the NU-R1FT line keeps its VIX dials and its horizon 130
and takes the four policy dials derived on its own law (`futures/RULE-RF5.md`, "NU-R1FT's policy dials", box v22fut9:
0.20 / 0.70 / 0.015 / 2.84), which R1FTNLT carries. No dial is set by two sources with different values.

### Known answers the grade box checks

Measured on `ptv22/candidate` (0baefaf4; the five scripts on box v22cand3 at 0f471efd), unchanged since
017732cf: the arm's dials are 0.0 on every preset, so no preset's trajectory moves. Registration 21's pin
replaces them as session 1's check gives them: they are unchanged at e132a66a (session 1's v22pin3, a full spot check: cargo 920 passed 0 failed, pytest 8,125 passed 0 failed, the five known answers as below, prices bit-identical to edec6abf 23/23, 46/46 and 27/27; `digests.py compute` at the pin gives presets abdfc2bc again and the arm's fingerprint below).

    sim digest (tests/known_answer.py)                 3a063f0d207bc37b5ade3b23c60f5e358b275507550b7f75f172a8d7cafb075c
    known-answer sha256                                4ea1d56340a3357db730764c3e24c131c5e0883efa4cd6f7f56c83ea9674033c
    presets, combined                                  abdfc2bc7a60b74f9412152c5a70598f4b271af9911f3b9497595c9ef124e1e4
                                                       (`digests.py compute arm-R22-fixd2RWP.txt` at the candidate;
                                                       the pt-v22 row is re-recorded by the swap, item 2 below)
    pt-v20's row (tests/known_answer_presets.py)       07ab6e0c1cea8fe42eb5e801afa0c725f7c6e8e6e1e56ba622eaa1efa9577520
    pt-v21's row                                       e3cf44104024d8a43a472d046f9c5d836266d7b94045126936b4478f1490ee78
    pt-v22's row                                       c7b7f0f0027c0034409721a6f15822a4dd38cd6503be99b8c534839c80163b27
                                                       (the preset's stand-in arm today; re-recorded when the swap
                                                       writes R22-fixd2RWP into it)
    book known answer (tests/known_answer_book.py)     b14d1f50d7d7798554b761a1dc3bc020b18b420ad6d973f6872c2985517d8476
    traded known answer (tests/known_answer_traded.py) 64b896ef (the script's digest)
    seed-64 known answer (tests/known_answer_seed64.py) cef62229d22349e5fbd41dac6ccaba91555b68a86dd79bde1c0259bd8e3e6a8b
    the arm's dial set, engine fingerprint             custom-9ac114fd
    arm line, sha256 of the text after "R22-fixd2RWPD@pt-v20:"
                                                       52c92c912f8b1a9d983ff9ea1860251c283e588b0796002066479972ee4685c0
    the arm line whole, sha256 (MANIFEST19.json's arm slot)
                                                       c8fa2ab6920427d5d151d5ba91d379e443af2a48e327492b5372b798b65c9802
    (registrations 19 and 20's: custom-ee6aba24, 02e4cc0b, d1872d52)

(R22-reg19n's 194-dial line is custom-3c8eb63d, e8b16502; R22-reg19's 193-dial
line custom-a8938ccf, 61a4eb2f.)

`r13reg/box/digests.py` needs `PRESETS_DIGEST` and `ARM_FINGERPRINTS` set to the
values above at the registration commit (`v22-regdryrun-report.md` blocker 1).

## What this registration changes, and what it does not

The graders are the eighteenth registration's, with these amendments and
rulings, each approved before this draft:

1. **The free gate** (plan, 2026-10-10): no registration unless every row's
   pass chance is at least 0.9 and the joint at least 0.5.
2. **Construction rows** (PH5, and the forecast rows RF5 and OF4) pass at
   the stationary arm's mean chance, read the same way on the same draws
   (2026-10-14, amended again the same day). PH1 enters at 1.
3. **SF1 is a ratio of medians** (2026-10-14), band unchanged (at least
   0.75), on 216 SF seeds (72 on 2026-10-15; raised to 216 by the owner on
   2026-10-25, before any read on the exam seeds). Their held-out value is
   201-400,431-446: 401-430 are old exam seeds, which `seedplan.py` refuses
   always, so they are skipped as d1pool skips them (coordinator's ruling,
   2026-10-26).
4. **R3 is read on the held close** (2026-10-14), band -0.36 to +0.03, real
   -0.161; the last print is reported.
5. **S1a is read on 180 recession seeds** (2026-10-15), band unchanged.
6. **XD1's tape allowance** (2026-10-15): a futures rule over C10's bound
   that the real tape also breaks passes when its median excess and its
   share of histories ahead are no larger than the tape's.
7. **DV1c supersedes DV1** (2026-10-19), real 5.72, band 3.7 to 7.7
   (2026-10-21). DV1 is kept and marked superseded.
8. **The recession file's VIX leg** follows the real 2008-09 Cboe VIX
   (2026-10-21): `s2fix/vix/graded-vix/recession.yml` (ad3c51d5) replaces
   recession_I (9eac7e1a) for both arms.
9. **The 2020 replay's trailing earnings leg** (2026-10-22, with the
   earnings look-through): `s2fix/replay2020/graded-2020/` (driven2020.py
   with earnings-2020.json) replaces the replay with no earnings leg. The
   forward-EPS leg is not adopted.
10. **NS3's premise is withdrawn** (2026-10-10): the 159 underlying rows are
    re-read on the exam seeds.
11. **v22 scope** (2026-10-15, "option a"): VIX futures and index options
    are off. The VF rows, the SV and OB rows, XD4, the VIX pushes of XD3 and
    the two VIX rules of XD1 are not graded.
    On 2026-10-25 the owner asked for both back in v22. Six attempts followed:
    the VIX law, the index surface, the index's drift, the leverage effect,
    the VIX futures product, and one joint solve of the volatility loop on 28
    tape moments (reports `v22-vixlaw-`, `-indexsurface-`, `-indexdrift-`,
    `-volloop-`, `-vixfutures-` and `-voljoint-report.md`). None put the VF
    rows and OB5 in band without making rows worse than R21E1. A short index
    straddle still earns about +0.8% a week, against a real -1.5 to -6. So
    item 11 stands for v22 (owner's standing rule, 2026-10-25: whatever makes
    the better model). The work carries to v23: one volatility loop solved on
    tape moments that pin its level, crash rate and VIX tail, with a
    reversal that does not damp crashes.
12. **Owner rulings of 2026-10-25** (the weakest gate rows; reports
    `v22-weakrows-sample-report.md` and `v22-weakrows-model-report.md`):
    - **B9's gate chance** is read at the grade's own size (all 90 desk
      histories plus 20 openings), not the stage-B size. The grade's
      estimator is unchanged.
    - **SF1** is graded on 216 SF seeds (item 3).
    - **The credit spread's lagged leverage term is not adopted.**
      `corporate_spread_equity_lag_gain` 0.9 with `corporate_spread_equity_lag`
      8 (solved on LQD less IEF on SPY, 2015-2025, measured; engine
      `ptv22/spread-lag-gain` 04ed74dd) lifts R4m from 0.221 to 0.300 (chance
      0.940 to 1.000), but on the gate box's 270 r14gen histories it takes H5
      from a tie with R21E1 to worse (distance +0.024 against +0.021, se 0.011;
      design `ptv22/reg19s` 188f3470, `v22-r4m-stageB-report.md`). The
      no-regression rule holds, so the candidate stays R22-reg19 at 193 dials
      and the dials stay at 0. Owner, 2026-10-25.
    - **Recorded as known gaps** (MODEL.md, `ptv22/spread-lag-gain`
      0aa6e6ee, docs only): the corporate spread's missing build-up after a
      stock fall (R4m 0.22 against 0.47); PH4b's 60-session persistence
      (0.60 against 0.71 as first registered; 0.61 against 0.65 under item
      20), whose only lever is v21's tuned `vix_level_sigma`;
      it moves to v23. OF4 is not changed (no bias shown).

    - **The gate with these rulings,** on R22-reg19 with ON1 and MG3 re-read
      on held-out histories (`v22-weakrows-sample-report.md`): joint about
      0.72 (B9 0.984 at the grade's size, SF1 0.978 at 216), no row under 0.9.

    - **Recorded, not an exploit:** the front index future held close to
      open nets +2.2 bp a night at small size (se 0.27; +2.6 gross), on 100
      held-out histories (design `ptv22/nightedge-v22` 57e14e2d). SPY's real
      night, 2005-2025, is +2.4 to +3.1 bp gross (se 0.8), positive in 18 of 21
      years, and a real ES round trip costs about 0.5 bp. The model matches the
      market, so this hold is not an exploit row.

13. **The final candidate** (2026-10-26): R22-reg19n, R22-reg19 plus
    `news_preopen_share` 0.259 on engine `ptv22/news-preopen` 6a219eea. The
    engine prices all company news from the first tick, so the names' own night
    is too small (15 bp against the tape's 26). The dial releases that share of
    company news before the open, with no new draw; its value is measured on
    the tape, not tuned (`v22-overnight-report.md`; design
    `ptv22/overnight-v22` cc20885a). On the 270 it takes G1 and G7 from ties
    with R21E1 to better. Every other route tried for v22 is closed (items 11
    and 12).
14. **The pre-grade audit** (2026-10-26, `v22-audit-report.md`; engine
    `ptv22/engine-audit` 23ea6322, readers `ptv22/readeraudit`). It found a
    riskless profit on the candidate: once the eve holds the Fed's decision,
    the price the rate futures read carries 0.8 of it, and trading FF by the
    eve's sign won 36 of 36 meetings. It also found a static spread between a
    TR3 contract and its FF strip (4.5 to 8.2 bp), a name-option dealer that
    quotes one tick wide with no inventory response, and no minimum margin
    for a short option. R22-reg19n is not graded with these in it. A fix round
    (`v22-fixround-brief.md`) prices the eve's decision fully less a measured
    meeting-day surprise, makes TR3's premium what its FF months imply, sets
    the dealer's spread and position limit from the tape, adds the rulebook's
    minimum short-option charge and a pre-trade initial check, and adopts the
    audit's six account and night fixes. Stock prices must not move; the
    futures and names stages are re-read. The owner's rule of 2026-10-25,
    "whatever makes this the better model", covers these choices.
15. **RF5 is read at 63 and 126 sessions** (owner, 2026-10-26). The engine's
    forecast runs to 130 sessions and the longest rate contract is about 125
    sessions out, so the 252-session reading is dropped from CRITERIA.
16. **The construction rows' bar** (coordinator, 2026-10-26, under the owner's
    rule). A ratio of 1 fails a correct model about half the time, because
    the stationary arm's own chance (0.73 for OF4) is noisy. OF4 is re-read on
    a larger held-out sample, its size and seeds committed first. If the
    ratio's interval includes 1, construction rows pass at 0.9, like every
    other row. If it lies wholly below 1, the oil forecast is fixed with a
    measured value instead.

17. **The traded stage's index-option readings** (coordinator, 2026-10-26,
    under the owner's rule; `v22-xd4-report.md`, design `ptv22/xd4-v22`
    98cc6931). (a) The short weekly straddle is read against OB5's band
    negated, on both sides, as every OB band is read. (b) The delta-hedged
    risk reversal and the straddle against the VIX future take C10a's bound
    against cash; the frozen data hold no tape figure, and none is added.
    (c) `index_parity_arb` counts under XD4's "parity trades lose". Its
    +$401 a package (12 of 12 histories) is a model fault: the dealer prices
    index options off the fair carry while the listed future carries basis
    noise. The fix ties the dealer's forward to the traded future
    (`v22-xd4b-brief.md`), with the futures settlement speed fix in the
    engine's Portfolio.

18. **The fix round's four stops** (coordinator, 2026-10-26, under the owner's
    rule; `v22-fixround-report.md`, design `ptv22/fixround` c5c1555d).
    (a) IF1/IF2: the night keeps step 0 at the close's fair and takes the
    re-mark at the first night step; an order at step 0 fills after it. The
    stale after-close future quote (`v22-nightdrift2-report.md`, +21 bp a
    night) must lose on the same build.
    (b) MG3: the model's option margin is a risk scan, so its minimum is the
    portfolio-margin rule's per-contract floor (FINRA 4210(g)), not the
    strategy-based 10%. MG3's criterion is unchanged. The pre-trade check stays.
    (c) The Fed eve: the market sees the decision through a noisy signal and
    prices the posterior mean over the 25 bp grid; the signal's noise is set so
    the decision-day surprise sd is the tape's 2.55 bp. Rounding the eve to the
    grid must not win.
    (d) OF4: its 95% interval on 810 histories is 0.28-0.95, wholly below 1,
    so by item 16 the construction rows keep ruling 2's bar of 1 and the oil
    forecast's bias is found and fixed with a measured value.
    Round 2 (`ptv22/engine-audit` 59a887e4, design `ptv22/fixround` 87003905):
    (a), (b) and (c) hold, with no row lower. The after-close exploit reads
    -0.12 bp a night (se 0.14); its slope on the re-mark is 0.006 (se 0.008),
    against 1.02 before. The coordinator rules it "doesn't win": the residual
    is ordinary night drift picked up through a long tilt, not the re-mark.
    The short strangle under the portfolio-margin floor goes below zero in
    108 of 270 histories; the fix round finds where those losses happen
    before the scan is changed.

19. **Grade-job readings** (coordinator, 2026-10-26, under the owner's rule;
    `v22-regprep-report.md`). (a) MG3 ("MG1 for option portfolios"; MG1
    counts one-session moves past the initial margin) counts losses past the
    margin only: a gain can't breach a margin, and the 0.005-0.015 band is a
    one-sided 1% tail. MG1's own reader is checked for the same reading.
    (b) AX1 keeps its registered text, "dealer or an agent": a reader for the
    agents' side is added before the grade, since the engine's
    `exercise_violations()` checks the dealer only. (c) The grade job tells a
    crash apart from a failing row: a crash stops it; a failing row is
    recorded and the grade runs on to the full verdict. (d) The grade's engine
    pin carries the settlement speed fix (`ptv22/index-parity` 9c8a6b09,
    bit-identical); the digests are recomputed on the final pin.
    (e) AX1's agent clause counts exercises and quotes the engine should have
    refused (outside the no-arbitrage bounds, or an exercise the contract
    doesn't allow). A legal exercise that holding would have beaten is the
    agent's loss, not a model fault: it is reported, not counted. The
    dividend-capture probe's 16 such exercises on seed 9001 are of this kind.
20. **PH4 and PH4b's real values, re-measured** (owner, 2026-10-27; `v22-ph4real-report.md`, design
    `ptv22/ph4real`: rule f3e6e084, tape de028af8, read bd7db1cd). The statistic is unchanged (the
    correlation of log realised volatility over K sessions before and after seams 2K apart, demeaned within
    each history, pooled; K 20 for PH4, 60 for PH4b). It is now read at every one of the 2K seam offsets and
    averaged, on the model and on the tape alike, because one path's seams alone move PH4b on the tape by
    about 0.25. The real is read at the model's length: the mean over the tape's windows of 5,292 sessions
    (^GSPC 1990-2025, sixteen windows ending at the year ends 2010-2025). The band is the real plus or minus
    two bootstrap standard errors over whole calendar years (B 2,000, seed 20261007), the rule both rows
    were registered under. PH4: real 0.682 (se 0.054), band 0.574 to 0.790 (was 0.659, 0.575 to 0.743).
    PH4b: real 0.648 (se 0.067), band 0.514 to 0.781 (was 0.712, 0.586 to 0.838). The values and the
    estimator were committed from the tape before any model was read on them. On the gate's 270 histories,
    v21 reads 0.657 and 0.615 and R22-fixd2 0.665 and 0.607; both pass both rows at 1.000, and against v21
    both rows tie. A resample by whole years lowers a persistence statistic (the resamples' mean PH4b is
    0.497); with runs of three years the se of PH4b would be 0.091. This is recorded, and rule 2 is kept.

21. **OF4 is reported, not gated** (owner, 2026-10-27). Six attempts to remove the oil forecast's
    126-session bias failed (`v22-oilfix-report.md`; engine `ptv22/oil-forecast` 0481b65e, cde5db53;
    design `ptv22/oilfix-v22`). The best read on the 810 held-out histories is 0.903 (0.396-0.986),
    still wholly below 1. The bias is about 0.21 $ at 126 sessions, 0.3% of the price. OF1-OF3, XD1's oil
    rules and the macro rows oil feeds all pass. OF4 is read and reported at the grade as registered, and
    it leaves the joined gate and the gate's every-row rule. No forecast switch is set: each is 0 on the
    preset.

22. **Single-name option margin at FINRA's range, and MG3 restated** (owner, 2026-10-27;
    `v22-optmargin-report.md`; engine `ptv22/opt-margin` e2806a32; design `ptv22/optmargin`: rule 136cac85,
    read bdb8334b). The single-name scan moved about two one-session standard deviations (a clearing
    house's 99%, times a tail fitted on the model), so a short strangle could write many times what a broker
    allows. `margin_option_scan_range` 0.15 puts the scan at FINRA Rule 4210(g)(2)(F)'s range for an equity:
    ten equidistant valuation points over plus and minus 15% of the name's price, netted within a name and
    summed across names (4210(g)(8)), with the (g)(7)(B) minimum as before; stock held without the name's
    options asks 15% of its value. The index options' scan is unchanged. MG3 was written for the model's own
    scan; under the rule's fixed range a portfolio's loss passes its margin only when the name moves past the
    range, so MG3 is restated from the rule and the tape, before any model read: the share of single-name
    option portfolio sessions (one name's options, 4210(g)(8)(A)) whose one-session loss passes that
    portfolio's initial margin set at the close before, losses only. Its real is the certified forty's rate of
    one-session moves past plus or minus 15%, 2015-2025: 58 of 106,360 name-sessions, 0.000545 (se
    0.000288, rule 2), band 0 to 0.00112. On 64 held-out histories the short strangle goes below zero in 0
    (33 without the dial), and MG3 reads 0.000052, chance 1.000.

23. **MG2 on option accounts reads "no risk left"** (coordinator, 2026-10-27, under the owner's rule;
    `v22-optmargin-report.md` "MG2"; engine `ptv22/opt-margin` e92b9e00; design `ptv22/optmargin`: rule e4e71eed,
    read 1612828b). Every MG2 failure on R22-fixd2 was XD5's sized earnings-straddle account holding long options
    after an unmet call, because the liquidation swept the dealer's book once. `margin_liquidation_worked` 1 works
    the liquidation as a broker works a market order: at the same open, each sale is sent again at once while the
    dealer, quoting afresh, still buys.
    MG2 on option accounts reads a margin call as liquidated when nothing it leaves can lose more than its value:
    no future, no short option, and no long option its book would still buy. A long option no book bids is
    reported (`no_bid_left`), not counted. On 9001-9064 with the sized account on, MG2 reads 0 (23 without the
    switch); the registered count reads 47, all long options with no bid.

24. **L1 is read on its real's own convention** (coordinator, 2026-10-27, under the owner's rule;
    `v22-lookthrough2-report.md`; design `ptv22/volpersist-v22` 606f63d1, folder `programme/ptv22/lookthrough/`).
    L1's real (68) stamps the tape's earnings trough at the end of its quarter; the registered reader stamped
    the model's on its day. The reader now stamps the model's earnings trough at the end of its quarter, as
    the real does. The real and band are unchanged. On 24 seeds of the registered 2020 replay with its
    earnings leg, R22-fixd2 reads 69.0 and v21 31.5 (on the day stamp: 27.0 and -10.5). Its model side, the
    earnings cycle, is superseded by item 25(a): the index's reported earnings, with the same quarter-end stamp.

25. **The convention round** (owner, 2026-10-28; coordinator rulings under the owner's rule;
    `v22-convention-report.md` "Round 2"; design `ptv22/volpersist-v22`, `programme/ptv22/convention/`: rule
    d1ed2dc3, amendment A a77a33b7, amendment B f9d05ae6, amendment C 91c99fe1, amendment D 82c18915, amendment E 21c2732d, re-reads ed9db66e and 81cd9db2; `v22-l1risk-report.md`, design
    `ptv22/l1risk-v22` da328b14). A reader-convention audit checked about 190 rows against their reals' sources:
    time stamps, windows, units, pooling, demeaning, span and the history length a real is read at. Every row
    whose reader computed its statistic on a different convention from its real's source is read on the source's
    convention, on v21 and v22 alike, whichever way it moves. The rule and both amendments were committed before
    any re-read or run. Nothing is re-tuned, and no reading is dropped because it moves the score.

    (a) **L1 keeps its registered real, read on the model's reported earnings; this supersedes item 24's reader.**
    The registration fixes L1's real as a quantity: the sessions from the S&P 500's trough on 23 March 2020 to
    the end of Q2 2020, 68, band 1 to 136 (`ptv20-registration.md` 610-611). It gives as its reason "the trough
    quarter of S&P operating earnings". That reason is wrong: S&P's own operating series troughs in Q1 2020
    (19.50 against Q2's 26.79), on reserve builds and write-downs booked at the quarter's end, and the street's
    operating series (FactSet) troughs in Q2. The quantity stands. The model side reads the index's reported
    earnings ((b) below), the quarter with the lowest quarterly reported EPS within the first 250 sessions,
    stamped at its end as item 24 stamps it, less the index's trough day. The 2020 leg (item 9) has no Q1 knot by
    its rule, so the model's reported earnings trough in Q2. On 24 desk seeds (9001-9024; the published EPS
    alone, by day) R22-fixd2RWP reads 71 and v21 31.5. The wrong reason, and the reported-but-looked-through
    charge the model would need to read S&P's own series, go to the next registration.

    (b) **An earnings row's model side is reported earnings, as its real is.** A name's reported EPS at a close is
    `eps_t x (gdp_t cpi_t / base) x exp(earnings_cycle_t) x exp(v_t) x exp(buyback_log_shares_t)`, with `eps_t`
    the published figure (`fundamentals()[0]`, which `market.earnings` writes) and `v_t` the name's own earnings
    level, its surprises and permanent news (amendment E). The earnings anticipation, the market's expectation, is
    left out. The index's reported earnings are Σ sh_i x EPS_i with the index's own shares.
    A quarter's reported EPS is the index's earnings rate averaged over the quarter's sessions, divided by 4;
    trailing figures are the last four quarters' sum. The rows: L1 ((a)); R6, the level fit of 100 ln(Σ sh P /
    Σ sh E_TTM) on Baa, monthly averages, December 2021 to October 2022, the source's window (amendment D; the
    model's December point is its state before the replay's first pinned session, paired with DBAA's December
    2021 average, 3.296%); E1, the fall of nominal trailing reported
    earnings (as Shiller's TTM), real -0.17 as registered; DV1c, rho as the change in ln(index price / index TTM
    reported earnings) between year ends; DV5, payout over reported earnings.

    (c) **The published VIX** wherever the real is the published close: B1, B2 and B6 (`longrun.py` records
    `macro_fields["vix"]`), B11, H1 and H2 (`passprob.py` and `grade_all.py` read `m_vix`).

    (d) **Reader fixes, each to its source's convention** (the real and band unchanged):
    - F-stress hike carries the CPI < 4 gate the H rows carry (DFEDTAR/U and VIXCLS; the real reproduces only
      with it);
    - C6 reads the value IC from session 252, as `real_value_ic.py` does;
    - C4a drops the 65-minute returns that span a night (the real is intraday);
    - S1a reads the regain on the index's own fall from its prior high, 252 sessions after the low (the S&P 500,
      2009);
    - R7a is read on R7a-pre's like-for-like baseline;
    - SF3 is the single worst raw close-to-close move of the index (16 March 2020);
    - DV6 is Ken French's D/P quintiles: sorted each year on the prior year's paid yield, payers only,
      value-weighted, held a year (the ahead-of-time sort alone is reported only);
    - DV3 is trailing twelve months' paid dividends over the index level (Damodaran);
    - the G night rows (G1, G1c, G7, G7b, G8, G10) add each name's dividend back at its ex-date open;
    - ON1 reads the opening print, before the first tick, dividend-adjusted;
    - NS1 reads the forty's equal-weighted cash index at the opening prints (G1c's reading);
    - EV1-EV3 are the median over model years of the per-year statistic, as their bands are built;
    - SS1 reads the five largest roster names;
    - RF1_m1 is read on the month's first session (n = 20), RF2 leaves unscheduled moves out, and OF3 drops the
      changes around contract 1's expiry, as each real does;
    - AX1 reads the agents' exercise violations too (item 19);
    - Q4 and Q5 read the print, not the latent price;
    - NS2 and XD3 take the futures-rows copy of the readers, and XD5 the names copy (the corrected conversion).

    (e) **Reals re-measured at the model's convention** (the model's reading unchanged, the band by its registered
    rule): CV3, the median over the tape's 21-year windows of each window's mean over the 21 block offsets (as item
    20), -1.083 (was -0.80), band unchanged; B3 and B4 in 20-year windows of the tape, each a fresh peak as each
    history is, 1.156 (band 0.578 to 2.312) and 2.844 (band 1.422 to 5.688); O1-O4 on WTI deflated by CPI (the
    model's oil has no inflation trend), O1 1.132 (0.440 to 1.824), O2 0.067 (0.035 to 0.098), O3 0.141 (-0.433 to
    0.715), O4 0.259 (-0.018 to 0.536); R6 on the level fit over its source's window, -5.43 (band -10.86 to -2.72;
    the endpoint form's -4.91 and the registered midpoint -5.2 reported beside); XD1's tape allowance with the tape
    trading a session after the decision.

    (f) **Withdrawn from the round; each stays as registered.**
    - **V1.** Demeaning each history by its own mean adds a small-sample bias of about 0.1-0.15 at five years,
      larger than the one it would remove (the real's mean is over 78-152 years). Its readings, for the record:
      V1a v21 0.957 -> 0.930, v22 0.924 -> 0.893; V1b v21 0.862 -> 0.720, v22 0.833 -> 0.676.
    - **C10b** (amendment C; `v22-c10brisk-report.md`). The row's bounds are the tape's drift after the
      NBER's turns dated with hindsight, set as a ceiling on what a published, real-time phase could be worth. They
      are built for the published-entry reader, so reading the model on the true entry would leave them without
      their reason. Its readings on the true entry, for the record (the 270 check): contraction / recovery, v21
      -0.17 -> -5.35 / +0.60 -> +3.34, v22 +0.28 -> -5.80 / +0.64 -> +4.31.

    (g) **Unchanged** (the real's source or derivation isn't on disk, or the difference is the object, not a
    convention): R3m, R4, R4-lag, R4m, VC1-VC3, B10, B12, C5, C7, C8, C10d, C10e, C10e-cal, SF2, AO2, DV4, DV7sd,
    DV7cut, H3-H5, U1-U4, SS2, SS3, RF4, A1-A3, IF3 and RF3.

    (h) **ON1 is worse than v21 on its corrected reader, an exception the owner accepts** (owner, 2026-10-28;
    `v22-convention-report.md` "ON1 trade-off", design 36e1aa8c; `v22-finalread-report.md` "Convention runs"). Read
    on the opening print before the first tick, dividend-adjusted, ON1 is 0.345 against v21's 0.325 (real 0.330,
    band 0.245 to 0.415, both in band): +0.0104 further from the real, 2.3 se on the registered classifier (se
    0.0046) and 1.99 se paired (se 0.0051). On its registered form it ties. The difference is the measured pre-open
    news share (`news_preopen_share` 0.259); without it the arm reads 0.329. The same mechanism makes G1 (0.364
    against v21's 0.340, real 0.391; d -0.0245, se 0.0061) and G7 (31.1 against 29.4 bp, real 37.5; d -1.75, se
    0.85) better than v21, and moves G7b and G8 toward their reals; no other of the 109 rows compared changes
    class. The measured mechanism is kept, and ON1 is recorded as worse than v21: an explicit exception to the rule
    that no row is worse than v21, for this row and this reason only.

    (i) **R6 is reported, not gated** (owner, 2026-10-28). Read on the corrected form (amendments D and E), R6 is
    -2.48 on v22 (chance 0.428) and -1.73 on v21 (0.077), against a real of -5.43 (band -10.86 to -2.72): both above
    the band, v22 nearer the real than v21. The model's P/E falls too little against Baa in 2022; that goes to the
    next registration. R6 is read and reported at the grade, as OF4 is (item 21), and leaves the joined gate and the
    every-row rule.

    (j) **E1 is worse than v21 on its corrected reader, an exception the owner accepts** (owner, 2026-10-28;
    `v22-convention-report.md` "E1 (corrected)"; `v22-finalread-report.md` "L1, E1 and DV1c on amendment E"). Read
    on the index's nominal trailing reported earnings (amendment E), E1 is -0.086 on v22 and -0.117 on v21, against
    a real of -0.17: v22 is +0.031 further from the real, 2.7 se on the registered classifier. With the names'
    earnings level taken out of reported earnings the two arms read the same. v21's level drifts down by half its
    variance a year (about -0.9%), a Jensen artefact that deepens its contraction falls; v22's aggregate
    earnings-level fix removes it, and the tape has no such drift. The fix is kept, and E1 is recorded as worse
    than v21: an exception to the rule that no row is worse than v21, for this row and this reason only. Both arms
    fall short of the tape, an older gap that goes to the next registration.

    The readings, old and new (v21 = R21E1; v22 = R22-fixd2RWP; class v22 against v21 by the combined read's
    section 4; the gate chance as each row's gate reads it):

    | row | v21 old -> new | v22 old -> new | real | class, old -> new | v22 chance, old -> new |
    |---|---|---|---|---|---|
    | B1 | 0.0499 -> 0.0635 | 0.0522 -> 0.0671 | 0.0817 | tie -> better | 1.000 -> 1.000 |
    | B2 | 17.87 -> 23.08 | 19.19 -> 24.51 | 21.95 | better -> worse (+1.43, se 0.63) | 1.000 -> 1.000 |
    | B6 | 0.370 -> 0.370 | 0.316 -> 0.312 | 0.326 | better -> better | 1.000 -> 1.000 |
    | B11 | 1.463 -> 1.468 | 1.470 -> 1.485 | 1.62 | tie -> tie | 1.000 -> 1.000 |
    | H1 | -0.528 -> -0.514 | -0.534 -> -0.508 | -0.411 | tie -> tie | 1.000 -> 1.000 |
    | H2 | 0.000 -> 0.000 | 0.0064 -> 0.0064 | 0 | worse -> worse, unchanged | 1.000 -> 1.000 |
    | F-stress hike | 0.0003 -> 0.0003 | 0.023 -> 0.007 | 0.01 | tie -> better | 1.000 -> 1.000 |
    | CV3 (real) | -0.844 | -0.823 | -0.80 -> -1.083 | tie -> tie | 1.000 -> 1.000 |
    | B3 (real) | 1.50 | 1.67 | 1.124 -> 1.156 | unchanged | in band |
    | B4 (real) | 4.17 | 4.46 | 3.654 -> 2.844 | unchanged | in band |
    | O1-O4 (reals) | 0.714 / 0.05 / -0.021 / 0.207 | 0.946 / 0.05 / 0.047 / 0.282 | 1.132 / 0.067 / 0.141 / 0.259 | unchanged | all in band |
    | S1a | 0.597 -> 0.796 | 0.617 -> 0.876 | 0.62 | tie -> worse (+0.080, se 0.027) | 1.000 -> 0.970 |
    | DV6 (French) | 3.03 -> 3.39 | 3.02 -> 3.38 | 0.89 | tie -> tie | 1.000 -> 0.949 |
    | C6 | 0.0037 -> 0.0016 | 0.0034 -> 0.0052 | 0.0093 | tie -> tie | in band |
    | NS1 | n/a | 0.527 -> 0.479 | 0.461 | v22 only | in band |
    | L1 (reported earnings; 9001-9200) | 31 -> 33 | 72 -> 72 | 68 | better -> better (-31, se 2.3; paired se 1.7) | 1.000 -> 1.000 |
    | C4a (acf1; roll over quoted) | -0.0198 -> -0.0447; 1.29 -> 1.48 | -0.0205 -> -0.0462; 1.55 -> 1.65 | as registered | pass -> pass, both arms | passes |
    | R6 (level fit on reported earnings, December 2021-October 2022) | -7.47 -> -1.73 | -6.89 -> -2.48 | -5.43 | better -> better; reported, not gated ((i)) | 1.000 -> 0.428 (v21 0.077) |
    | R7a hike / cut; R7a-pre hike / cut (one form, on R7a-pre's baseline) | -0.28 / -1.15; 0.03 / -0.84 | 0.41 / -0.65; 0.73 / -0.32 | 0, bound 5 | tie -> tie | as registered |
    | SF3 lc / geo / rec_pkg / rate / curve / oil / prs | -4.04 / -1.65 / -6.70 / -7.74 / -7.74 / -1.90 / -1.89 -> -6.19 / -4.23 / -9.43 / -7.69 / -7.69 / -3.44 / -3.49 | -6.48 / -2.71 / -7.34 / -7.97 / -7.97 / -1.96 / -1.94 -> -8.41 / -4.95 / -9.36 / -7.89 / -7.89 / -3.39 / -3.50 | floor -13 | every file passes both ways | passes |
    | E1 (nominal trailing reported earnings) | -0.110 -> -0.117 | -0.112 -> -0.086 | -0.17 | tie -> **worse** (+0.031, se 0.012; paired se 0.008): the exception of (j) | as registered |
    | DV1c (rho on reported trailing earnings) | 6.49 -> 6.90 | 5.59 -> 5.82 | 5.72 | tie -> tie | in band (3.7 to 7.7) |
    | DV3 (trailing paid) | 1.949 -> 1.902 | 1.933 -> 1.880 | 1.81 | tie -> tie | in band |
    | DV5 (reported earnings, the registered E) | 0.864 -> 0.864 | 0.851 -> 0.851 | 0.80 | better -> better (-0.013, se 0.004) | in band |
    | G1 | 0.346 -> 0.343 | 0.367 -> 0.364 | 0.391 | better -> better | in band |
    | G1c | 0.474 -> 0.474 | 0.473 -> 0.472 | 0.461 | tie -> tie | in band |
    | G7 | 29.6 -> 29.3 | 30.7 -> 30.3 | 37.5 | better -> better | in band |
    | G7b | 1.75 -> 1.78 | 1.93 -> 1.96 | 1.96 | better -> better | in band |
    | G8 | 2.80 -> 2.67 | 3.01 -> 2.91 | 3.28 | better -> better | in band |
    | G10 | 0.0344 -> 0.0345 | 0.0270 -> 0.0271 | 0.022 | tie -> tie | in band |
    | ON1 | 0.393 -> 0.325 | 0.399 -> 0.345 | 0.330 | tie -> **worse** (+0.0104, se 0.0046, 2.3 se; paired 1.99 se): the exception of (h) | in band |
    | EV1 / EV2 / EV3 | n/a | -0.151 / -0.011 / 1.293 -> -0.151 / -0.011 / 1.297 | as registered | v22 only | in band |
    | SS1 | n/a | 1.392 -> 1.349 | 1.245 | v22 only | in band |
    | RF2 | n/a | 1.281 -> 1.386 (267 of 270 seeds) | as registered | v22 only | 1.000 -> 1.000 |
    | RF1_m1 (bp) | n/a | 1.607 -> 1.438 (267 of 270 histories) | as registered | v22 only | 1.000 -> 1.000 |
    | OF3 | n/a | 0.860 -> 0.853 | as registered | v22 only | 1.000 -> 1.000 |
    | AX1 | n/a | 0 -> 0 | 0 | v22 only | in band |
    | Q4 | 0.662 -> 0.561 | 0.662 -> 0.553 | 0.66 | tie -> tie | in band (at the edge, 0.55) |
    | Q5 | 0.571 -> 0.482 | 0.572 -> 0.477 | 0.55 | tie -> tie | in band |
    | XD1 (allowance: rule, ahead) | n/a | oil_long_backwardation +9.18, 1.00 -> +9.67, 1.00; oil_calendar +4.34, 1.00 -> +4.25, 0.95 | the allowance, re-measured | v22 only | as item 6 |

26. **RF5 is read at its registered horizons in the statistic too** (registration 20; `ptv22/regprep` c2aa6734).
    `ptv22grade.py --rf5 63,126` hands `passprob_futures._unbiased_stat` only the horizons it names, as item 15
    registered; grade 19's reader left the statistic looping over every horizon, so the 252-session NaN made RF5
    unread. On grade 19's 270 pieces RF5 reads 0.648. VF8 and OF4 are read at all their horizons, unchanged. The
    rewrite stops the grade if `futrows.rows` no longer holds either term it rewrites.
27. **The grade's summary lines** (registration 20; no reading changes):
    - `verdict16.py` exits 1 when a counted row fails (cff79924). `grade19.sh`'s FAIL line names every step that
      exits 1, so it now names verdict16 too; in grade 19 it named only ptv22grade.
    - `ptv22grade.py` marks an unread gated row "NOT READ: fails the grade" and ends "failing on their reading:
      ...; not read, so failing: ..." (0a71849a).
    - `grade19.sh` refuses an arm file's path for ARM (7708799f), which crashed grade 19's first attempt.
    - `screen/box.sh`'s FLEET_PY default is the main checkout's venv, refused if it can't import boto3 (f09d48ba).
28. **Fresh exam seeds** (below): 1,650 in 290201-299270. Registration 19's 280201-289270 are spent and refused.
29. **The free gate's readers, pinned** (item 1's condition of registering; `ptv22/regprep`): the joined gate is one
    script, `ptv22/joined/joined19.py` (a8c2987d), which runs fallback F's steps with every reader taken from git at a
    named commit. `readers-registered.json` runs them at the registration's ref, and MANIFEST19.json's `joined`
    section pins the script, that spec and its eleven readers by sha256 (`guard19.py joined`, checked first). With
    the readers F ran it reproduces F's 0.7410, every row's chance equal. The readers' changes since registration 19:
    - `passprob.py` reads R6 as NaN on a gate box from before R6's reader (8f941f8a; R6 is reported, item 25(i));
    - MG3 enters from `mg3pp.py` (item 22's band), which the registered `joined_gate.py` requires (3726483d);
    - `joined_gate.py` lists EV1-EV3 as not read when the names reader skips them (6e46d839).
    - the free gate fails on a gated row it can't read, as the grade does (bc64fd25), and says NOT A GATE;
    - its inputs must be written by the registered writers, byte for byte at the ref (281c75ce);
    - the four enlarged reads must be made by the registered producers (6d5f147e, 677877c4), pinned in
      `ptv22/joined/reads/`: B9's `b9grade.py`; DV1c's `dv1c20.py` (amendment E's reported earnings, 5.824 on
      R22-reg19n's gen 270), R7a's `r7read20.py` (on the pre-open baseline, +0.73 / -0.32 on R22-reg19n's 270) and
      ON1's `on1read20.py` (25(h), the opening print, 0.3449 on R22-fixd2RWP's records). A read made by another
      script, or naming none, is refused.
30. **Tooling, no reading changed:** the proof's known list gains registration 20's rules, scoped to 290201-299270
    (b3472c1d), and keeps registration 19's as history, scoped to its span (835b00a0); `criteria.py`'s B3/B4 note
    says means, settled (6b777aec).

31. **MG2's below-zero clause, in the engine** (session 6; the arm's one new dial, `margin_deficit_liquidates`): a margin call
    made with the account's equity below zero is not met by a recovery before the next open, and the next open
    liquidates, as MG2's registered text asks. 0 on every preset; 1.0 in the arm. Session 6's engine branch `ptv22/deficit-liquidates` (e132a66a): the switch is silent in the digest at 0, and `test_margin_deficit.py` covers a deficit then a recovering night; grade 20's failing history, rerun on a Linux box at the graded code, matches the grade exactly at 0 and reads MG2 0 at 1, the histories parting only on the day of that sale (`v22-optmargin-report.md`). On fallback F's 270 stored traded histories (267,495 calls, 2,393 closes below zero) every below-zero close was already liquidated at the next open, so no stored stage-B output moves (session 5).
    The reader and the row are unchanged.
32. **Fresh exam seeds** (below): 1,650, laid out at a seven-digit OFFSET (`freshseeds/layout.py`, `ptv22/regprep`
    c6f9a963 and c5e7cfa4), built in code so no commit carries them before this registration's. Registration 20's 290201-299270 are spent and refused.
33. **Tooling, no reading changed:** the proof's known list keeps registration 20's rules as history, scoped to its
    span (1203370f), and gains the general rule for a registration's own commits (febe14a1, design
    `ptv22/proof-rules-v23`): a git record from a commit on the registration's branches that changes only
    registration files is explained; any other stays flagged. The proof's stream of the big S3 keys matches the exam
    set's own width (4d1a5f48; it matched five digits only, so proofs 19 and 20 never read their sets there).

34. **Fresh exam seeds** (below): 1,650 again, laid out by session 3 at `ptv22/regprep` 9fa94f39 (a fresh seven-digit OFFSET, built in code; registration 21's OFFSET kept as OFFSET21, in USED and an assert), its span checked fresh before that commit against every commit, name and ref of the engine, design, serve and docs repos, every S3 key name and run log under pretium-calib (any date) and every text file on the desk: no record touched it. Registration 21's set (grade-seeds.json 31357e63),
    spent at grade 21, is refused, as every earlier set is.

35. **Fresh exam seeds** (below): 1,650 again, laid out by session 4 at `ptv22/regprep` 54423a5f (a fresh seven-digit OFFSET, built in code; registration 22's OFFSET kept as OFFSET22, in USED and an assert), its span checked before that commit against every commit, name and ref of the engine, design, serve and docs repos (fresh), every S3 key name and run log under pretium-calib as listed 2026-10-31 07:42 (any date), and every text file and name on the desk (fresh): no record touched it; proof-23's S3 part then read every key and run log fresh. Registration 22's set (grade-seeds.json 81ff88f5),
    spent at grade 22, is refused, as every earlier set is. The proof's known list keeps registration 22's draft rule
    as history, scoped to its span (681327b7), and rules this draft by path.

No band changes beyond items 20 to 25. No estimator changes beyond the list. R4m, H5, PH5, RF5 and C4a are graded as
registered.

## Seeds

### Fresh exam seeds (laid out, not run)

`freshseeds/layout.py` with the layout registrations 20 to 22 used, at a fresh OFFSET: laid out by session 4 at `ptv22/regprep` 54423a5f (a fresh seven-digit OFFSET, built in code; registration 22's OFFSET kept as OFFSET22, in USED and an assert), its span checked before that commit against every commit, name and ref of the engine, design, serve and docs repos (fresh), every S3 key name and run log under pretium-calib as listed 2026-10-31 07:42 (any date), and every text file and name on the desk (fresh): no record touched it; proof-23's S3 part then read every key and run log fresh. The pools
sit at OFFSET plus 1000, 3000 and 4000, as before. Registration 22's set (grade-seeds.json 81ff88f5), registration
21's (31357e63) and registration 20's 290201-299270 are spent and refused. No exam seed lies in an old exam span, a
spent exam set (thirteenth to twenty-second) or a used block. The numbers are built in the code, not written: this
table is filled at the registration commit from the layout's own print:

    longrun     LONGRUN_SEED_LIST      90  201-230,501-530,801-830    -> 9420201-9420230,9420501-9420530,9420801-9420830
    truephase   GEN_SEEDS             270  201-290,501-590,801-890    -> 9420201-9420290,9420501-9420590,9420801-9420890
    leakmacro   EXPLOITS_LEAK_FIRST    24  201-224                    -> 9420201-9420224
    cert        CERT_SEEDS             30  201-230                    -> 9420201-9420230
    heldseeds   HELDSEEDS_SEEDS        30  501-530                    -> 9420501-9420530
    heldu       HELDU_SEEDS            30  801-830                    -> 9420801-9420830
    crisisext   CRISISEXT_FIRST       270  2001-2270                  -> 9422001-9422270
    edge        EDGE_FIRST_SEED        10  201-210                    -> 9420201-9420210
    c4a         C4A_FIRST_SEED          8  201-208                    -> 9420201-9420208
    xsec        XSEC_SEEDS             90  201-230,501-530,801-830    -> 9420201-9420230,9420501-9420530,9420801-9420830
    r7          R7_SEEDS               90  201-230,501-530,801-830    -> 9420201-9420230,9420501-9420530,9420801-9420830
    r7pre       R7PRE_SEEDS            90  201-230,501-530,801-830    -> 9420201-9420230,9420501-9420530,9420801-9420830
    r7eval      R7EVAL_SEEDS           30  201-230                    -> 9420201-9420230
    recession   REC_SEEDS             180  201-380                    -> 9420201-9420380
    sfrec       SF_REC_SEEDS           30  201-230                    -> 9420201-9420230
    sf          SF_SEEDS              216  201-400,431-446            -> 9420201-9420400,9420431-9420446
    mcrun       MC_RUN_SEEDS           30  2501-2530                  -> 9422501-9422530
    mctrips     MC_TRIP_SEEDS           3  2531-2532,2534             -> 9422531-9422532,9422534
    mcmark      MC_MARK_SEED            1  2533                       -> 9422533
    ao          AO_FIRST_SEED          30  3001-3030                  -> 9423001-9423030
    ph1         PH1_SEEDS               5  2001-2005                  -> 9422001-9422005
    ph3         PH3_SEEDS              30  2001-2030                  -> 9422001-9422030
    edgegen     EDGEGEN_SEEDS          30  2001-2030                  -> 9422001-9422030
    edgeeval    EDGEEVAL_SEEDS         20  3001-3020                  -> 9423001-9423020
    regr_short  AUDIT_SEED_SHORT       30  2101-2130                  -> 9422101-9422130
    regr_var    AUDIT_SEED_VAR         30  2201-2230                  -> 9422201-9422230
    regr_long   AUDIT_SEED_LONG         8  2301-2308                  -> 9422301-9422308
    regr_probe  AUDIT_SEED_PROBE        8  2401-2408                  -> 9422401-9422408
    stab        STAB_FIRST_SEED        12  201-212                    -> 9420201-9420212
    probe       PROBE_SEEDS            36  201-236                    -> 9420201-9420236
    d1pool      D1POOL_SEEDS          360  201-400,431-590            -> 9420201-9420400,9420431-9420590
    aopool      AOPOOL_FIRST          400  43001-43400                -> 9424001-9424400
    longrunpool LONGRUN_POOL_SEED_LIST  180  50201-50230,50501-50530,50801-50830,60201-60230,60501-60530,60801-60830 -> 9421201-9421230,9421501-9421530,9421801-9421830,9423201-9423230,9423501-9423530,9423801-9423830
    ptv21       PTV21_SEEDS            90  201-290                    -> 9420201-9420290
    ptv22       PTV22_SEEDS           270  9001-9270                  -> 9429001-9429270
    ptv22mp     PTV22_MP_SEEDS         30  9001-9030                  -> 9429001-9429030
    1650 distinct exam seeds in 9420201-9429270

The two `ptv22` protocols are the nineteenth's; `box/seedplan.py` (84c66dd7, unchanged) carries them:
their held-out values are the gate's 9001-9270 (free and traded rows) and
9001-9030 (IF4 and XD3). The names' chain rows read 270 histories at the
grade (CRITERIA-pt-v22.md); in this layout they share PTV22_SEEDS.

### The exam seeds have not been run

Not yet proved. Before the registration commit: `freshseeds/increment.py`
and `r13reg/proof19/report.py` over the local, S3 and git records since the
twenty-second proof, written as `proof-23.txt`. `report.py`'s rules for any
registration stay in its KNOWN list (the exam set's own file, the proof's own
output in `proof19/` to `proof23/`, the gate box's copy, this
draft's file and patch); registrations 19's to 22's own rules are kept as
history and explain records only inside their own spans (1203370f), and a git
record from one of this registration's own commits is explained by the general
rule (item 33).

## The grade commands

### The plan

From the registration commit `<sha>` (pushed; JOBS_REF below), in `programme/screen`:

    python3 screen.py plan --arms EMPTY --baseline-arm "$(cat ../r13reg/arms/arm-R22-fixd2RWPD.txt)" \
        --stage B --full --registered ../r13reg/grade-seeds.json --registered-grade <sha> \
        --jobs-ref <sha> --out ../r13reg/grade19/plan-g23.json

and one plan per role with `--only-stages` (the role's stages below). `--jobs-ref` is required and recorded in the
plan; `screen/box.sh` refuses a plan made from another commit.

Grade 18's 22 shards (box, edge, c4, xsec, driven2020, c10, driven2022, r7,
recession, stab, gen, probe, sf, r7pre, mc, aoph, d1pool, aopool,
longrun_pool, edgeaudit, regr, ptv21) and the four pt-v22 stages in `screen/stages.json`:

- **ptv22fut**: `futrows.py run`, the free futures rows' pieces, on PTV22_SEEDS, 21 years;
- **ptv22chain**: `ptv22.py measure --parts chain` and its pieces, on PTV22_SEEDS;
- **ptv22names**: `ptv22.py measure --parts names`, on PTV22_SEEDS;
- **ptv22traded**: `--parts traded` on PTV22_SEEDS, then `--parts metaorder,push` on PTV22_MP_SEEDS, then `tpieces`.

Each reads the registered arm line as written, with no `--switches`. `ptv22idx` and `ptv22vix` are in the job,
switched off (each requires its listing dial). The ptv22 readers are one merged copy on `ptv22/regprep`, pinned in
`screen/box/files.txt` (`ptv22.py` and `ptv22traded.py` at items 22-23's commits).

### The box

Launched by `programme/r13reg/grade19/boxes19.sh ROLE PLAN.json RUN` (or `ROLE ARM_FILE RUN` for the gate), one
stage at a time on c8g.16xlarge in us-east-2, with JOBS_REF the registration commit (required; the fifteenth's job
refused), BRANCH and PIN the engine pin (no default). Each launch runs `guard19.py launch` against MANIFEST19.json
at JOBS_REF: the box files, the desk readers, the gate box's manifest, the launch files and the three slots (arm,
engine pin, grade seeds), refusing while any slot is empty; the box checks itself again before any run, and
`grade19.sh` checks the desk readers and a clean TF_ENGINE at the pin before step one. Every enabled stage is in
exactly one role:

| role | stages | dead-man, min | cap |
|---|---|---|---|
| b1 | edgeaudit, aopool, longrun_pool, mc, d1pool, c4, edge | 120 | $1.30 |
| b2 | stab, box, regr, r7, aoph, r7pre, ptv21, sf, xsec, recession, probe, driven2020, driven2022, c10 | 110 | $1.20 |
| gen | gen (r14gen.py on GEN_SEEDS, the names files kept for DV1c) | 30 | $0.33 |
| reads | ptv22chain, ptv22fut, ptv22names | 180 | $1.95 |
| traded | ptv22traded (or traded1 and traded2, 90 each) | 150 | $1.63 |
| gate | the gate box (`ptv22/gatebox/launch.sh`: scenario rows, B9, openings, the 270 r14gen), held-out seeds | 60 | $0.65 |

b2's dead-man is its plan's estimate (87.9 box-minutes) plus 25% (733f72de). The gate box is the nineteenth's,
reused (`ptv22/gatebox`, its guard and 30 inputs); the registration commit swaps its seed files for this
registration's. The launches were run end to end with no AWS (`grade19/dryrun/dryrun.sh`, 97 as expected, 0 unexpected:
each role on this registration's files, each fault refused). KAT stays 3a063f0d207bc37b5ade3b23c60f5e358b275507550b7f75f172a8d7cafb075c.

The arm line has to list no dial at its pt-v20 value: `longrun.py` refuses
such a line. The registered 208-dial line meets that.

### Grading, on the desk, after collect

`programme/r13reg/grade19/grade19.sh MERGED ARM OUT` (ARM the arm's name, R22-fixd2RWPD) with JOBS_REF, TF_PY and TF_ENGINE at the pin and the
exam-seed variables of the layout above with `REGISTERED_GRADE=<sha>`. Each step's exit status is recorded (item
19(c)); a failing row is recorded and the grade runs on, a crash stops it:

- grade 18's commands: `grade_all.py`, `exploits.py`, `exploits_summary.py`, `ptv21.py grade` (at grade 18's
  pin), `verdict16.py`; L1 among them, from the driven2020 stage's `driven2020.py` (the gate box's copy, one file
  for both), which stamps the earnings trough at its quarter's end (item 24);
- `amend19.py`: the amended rows, R3 held (item 4), SF1 as a ratio of medians (item 3), DV1c for DV1 (item 7),
  PH4 and PH4b at every seam offset (item 20);
- `ptv22_switch.py rows` and `ptv22grade.py`: the pt-v22 rows on the four ptv22 stages' outputs (OF4 read and
  reported, not gated, item 21; MG3 per name portfolio, item 22; MG2 on option accounts "no risk left", item 23);
- `ph2.py`: PH2's three known answers at the pin.

`verdict16.py` exits 1 on a failing counted row and `ptv22grade.py` reads RF5 at 63 and 126 in its statistic too
(items 26-27); the grade's last line names every step with a failing row.

## The pass rule

R22-fixd2RWPD passes this grade when every gated row passes on the exam seeds:
the forty registered rows, the eleven pt-v21 rows, every proposed and leak
row (`grade_all.py`), the exploit gate (zero exploits), the pt-v22 rows that
ship (below), and every known answer matches. Rows marked "reported" or
"not graded" do not gate. Nothing is re-tuned, re-seeded or re-read after
the grade. If any gated row fails, the grade is a fail.

## The rows, their bands and estimators

### The underlying's 159 rows

The forty registered rows, the eleven pt-v21 rows, the proposed rows and the
leak rows, each with the band and estimator `ptv21-registration-18.md`
registers, changed only by items 3 to 9, 20, 24 and 25 above. The table's readings and
chances are R22-reg19's; R22-reg19n's, row by row, are in
`R22-reg19n/joined/joined-R22-reg19n.json` and summarised in "The candidate
on held-out seeds".

| row | group | band | stage-B reading | gate chance | the gate's read |
|---|---|---|---|---|---|
| A1 | registered | as registration 18 | 88 / 80 | 1.000 | unmodelled |
| A2 | registered | as registration 18 | 0.44 / 0.30 | 1.000 | unmodelled |
| A3 | registered | as registration 18 | 0.79 / 0.80 | 1.000 | unmodelled |
| B1 | registered | 0.04084 to 0.1634 | 0.05519 | 1.000 | normal, readings se, grade/stage-B size 3; the published VIX (item 25) |
| B2 | registered | 10.98 to 43.9 | 19.97 | 1.000 | normal, readings se, grade/stage-B size 3; the published VIX (item 25) |
| B3 | registered | 0.578 to 2.312 (real 1.156, item 25) | 1.678 | 1.000 | normal, readings se, grade/stage-B size 3 |
| B4 | registered | 1.422 to 5.688 (real 2.844, item 25) | 4.484 | 1.000 | normal, readings se, grade/stage-B size 3 |
| B5 | registered | 3.092 to 12.37 | 7.724 | 1.000 | normal, readings se, grade/stage-B size 3 |
| B6 | registered | 0.1631 to 0.6524 | 0.3087 | 1.000 | normal, readings se, grade/stage-B size 3; the published VIX (item 25) |
| B7 | registered | 14.49 to 21.74 | 18.71 | 1.000 | normal, readings se, grade/stage-B size 3 |
| B8 | registered | 4.25 to 8.25 | 7.148 | 0.976 | normal, readings se, grade/stage-B size 3 |
| B9 | registered | as registration 18 | 16.80 / 1.45 | 0.949 | predictive desk 150 + openings 1000; annual 90 -> 90 |
| C1 | registered | as registration 18 | 1.04x | 0.994 | predictive 270 -> 270 |
| C10 | registered | as registration 18 | +0.08, 0.51 / +2.1, -0.1 | 0.999 | predictive 270 -> 90 |
| C2 | registered | as registration 18 | 1 of 90 | 1.000 | unmodelled |
| C3 | registered | as registration 18 | +16bp | 1.000 | unmodelled |
| C4a | registered | as registration 18 | -0.026 / 2.0x | 1.000 | unmodelled; no night-spanning returns (item 25) |
| C4b | registered | as registration 18 | -4.1 pts, 7/20 | 1.000 | unmodelled |
| C5 | registered | 0.8 to 1.05 | 0.9564 | 1.000 | normal, readings se, grade/stage-B size 3 |
| C6 | registered | as registration 18 | +0.003 / -0.000 | 1.000 | unmodelled; from session 252 (item 25) |
| C7 | registered | as registration 18 | -0.001 / -0.003 | 1.000 | unmodelled |
| C8 | registered | -6.4 to 2.9 | -0.7005 | 1.000 | normal, readings se, grade/stage-B size 3 |
| C9 | registered | as registration 18 | 0.459 / 0.474 | 1.000 | unmodelled |
| D1 | registered | as registration 18 | in | 0.988 | normal, margins.py se (pooled rows) |
| D2 | registered | as registration 18 | 0.335 / 82.0 | 0.997 | predictive 200 -> 90, gate box |
| E1 | registered | as registration 18 | -0.115 | 1.000 | unmodelled; nominal trailing reported earnings (item 25) |
| F1 | registered | as registration 18 | 0.311 / 34.5 | 1.000 | predictive 200 -> 90, gate box |
| L1 | registered | as registration 18 (real 68; item 25(a)) | +72 (items 24, 25(a); R22-fixd2RWP, 9001-9200; v21 +33) | 1.000 | predictive 200 -> 90, gate box; quarterly reported EPS with v, the trough quarter stamped at its end (items 24, 25(a), amendment E) |
| R1 | registered | 3.65 to 6.8 | 5.277 | 1.000 | normal, readings se, grade/stage-B size 3 |
| R2 | registered | 4.54 to 6.27 | 5.837 | 1.000 | normal, readings se, grade/stage-B size 3 |
| R3 | registered | as registration 18 | -0.158 | 1.000 | predictive 270 -> 270, gate box r14gen |
| R4 | registered | as registration 18 | +0.279 | 1.000 | predictive 270 -> 270, gate box r14gen |
| R5 | registered | as registration 18 | 0.2295 | 1.000 | predictive 200 -> 90, gate box |
| R6 | registered | -10.86 to -2.72 (real -5.43, item 25) | -5.994 | 1.000 | predictive 200 -> 90, gate box; level fit on reported earnings; reported, not gated (item 25(i)) |
| R7a | registered | as registration 18 | +1.2 / +2.6 | 0.858 | normal, margins.py se; R7a-pre's baseline (item 25) |
| R7b | registered | as registration 18 | -0.75, 3/30 | 1.000 | unmodelled |
| S1a | registered | as registration 18 | 0.5462 | 0.999 | predictive 200 -> 180 (registration 19), graded recession.yml; the index's own fall (item 25) |
| S1b | registered | as registration 18 | 30/30 | 1.000 | predictive 200 -> 180 (registration 19), graded recession.yml |
| S2 | registered | as registration 18 | 49.17 | 1.000 | predictive 200 -> 180 (registration 19), graded recession.yml |
| V1 | registered | as registration 18 | 0.91 / 0.87 | 1.000 | unmodelled |
| O1 | pt-v21 | 0.440 to 1.824 (deflated, item 25) | 0.9461 | 1.000 | normal, readings se, grade/stage-B size 3 |
| O2 | pt-v21 | 0.035 to 0.098 (deflated, item 25) | 0.05 | 1.000 | normal, readings se, grade/stage-B size 3 |
| O3 | pt-v21 | -0.433 to 0.715 (deflated, item 25) | 0.04704 | 1.000 | normal, readings se, grade/stage-B size 3 |
| O4 | pt-v21 | -0.018 to 0.536 (deflated, item 25) | 0.2822 | 1.000 | normal, readings se, grade/stage-B size 3 |
| ON1 | pt-v21 | 0.245 to 0.415 | 0.3882 | 0.952 | normal, readings se, grade/stage-B size 3; the opening print, dividend-adjusted (item 25) |
| SK1 | pt-v21 | -0.287 to -0.014 | -0.1865 | 1.000 | normal, readings se, grade/stage-B size 3 |
| SK2 | pt-v21 | 0.998 to 1.502 | 1.316 | 1.000 | normal, readings se, grade/stage-B size 3 |
| U1 | pt-v21 | -0.02 to 0.287 | 0.1833 | 1.000 | normal, readings se, grade/stage-B size 3 |
| U2 | pt-v21 | -0.212 to 6.012 | 2.338 | 1.000 | normal, readings se, grade/stage-B size 3 |
| U3 | pt-v21 | -0.147 to 0.682 | 0.1383 | 1.000 | normal, readings se, grade/stage-B size 3 |
| U4 | pt-v21 | -0.494 to 2.222 | 0.7434 | 1.000 | normal, readings se, grade/stage-B size 3 |
| AO1 | proposed | 0.498 to 0.502 | 0.4993 | 1.000 | unmodelled, no usage |
| AO2 | proposed | 10 to 40 | 15.42 | 1.000 | unmodelled |
| AO3 | proposed | -3 to 3 | -1.249 | 1.000 | unmodelled, no usage |
| AO4 | proposed | -3 to 3 | -0.4325 | 1.000 | unmodelled, no usage |
| AO5 | proposed | 0 to 0 | - | 1.000 | unmodelled, NOT READ |
| B10 | proposed | 1.45 to 2.3 | 1.868 | 1.000 | predictive 270 -> 270, gate box r14gen |
| B11 | proposed | 1.25 to 2 | 1.47 | 1.000 | predictive 270 -> 270, gate box r14gen; the published VIX (item 25) |
| B12 | proposed | 0.45 to 0.85 | 0.5825 | 1.000 | predictive 270 -> 270, gate box r14gen |
| C10e-cal | proposed | at most 1 | -0.2216 | 1.000 | unmodelled |
| CV1 | proposed | -1.75 to -0.8 | -0.9804 | 1.000 | predictive 270 -> 270 |
| CV3 | proposed | -1.3 to -0.35 (real -1.083, item 25) | -0.8431 | 1.000 | predictive 270 -> 270 |
| DV1, read as DV1c | proposed | 3.7 to 7.7 (DV1c; DV1's 4.6 to 8.6 superseded) | 5.621 (DV1c, on the 270; DV1 5.68) | 1.000 | DV1c on the 270 r14gen histories (nu-r1 Round T, the same histories) |
| DV3 | proposed | 1.3 to 2.5 | 1.9 | 1.000 | unmodelled; trailing paid dividends (item 25) |
| DV4 | proposed | 1.8 to 3.4 | 2.389 | 1.000 | unmodelled |
| DV5 | proposed | 0.56 to 1.04 | 0.8424 | 1.000 | unmodelled; over reported earnings (item 25) |
| DV6 | proposed | -2.1 to 3.9 | 2.969 | 1.000 | unmodelled; French's D/P quintiles (item 25) |
| DV7cut | proposed | 0.045 to 0.18 | 0.1088 | 1.000 | unmodelled |
| DV7sd | proposed | 3.6 to 14.2 | 5.361 | 1.000 | unmodelled |
| DV8 | proposed | 0.8 to 1.05 | 0.9669 | 1.000 | unmodelled |
| G-rt | proposed | at most 0 | -1.1 | 1.000 | normal, readings se, grade/stage-B size 1 |
| G1 | proposed | 0.3 to 0.48 | 0.3404 | 1.000 | unmodelled; dividend added back (item 25) |
| G10 | proposed | -0.04 to 0.09 | 0.03581 | 1.000 | unmodelled; dividend added back (item 25) |
| G1c | proposed | 0.3 to 0.6 | 0.4708 | 1.000 | unmodelled; dividend added back (item 25) |
| G2 | proposed | 6.5 to 14 | 10.6 | 1.000 | unmodelled |
| G2b | proposed | 0.09 to 0.2 | 0.1459 | 1.000 | unmodelled |
| G3 | proposed | 0.65 to 0.85 | 0.7213 | 1.000 | unmodelled |
| G3b | proposed | 1.2 to 2.4 | 1.712 | 1.000 | unmodelled |
| G4 | proposed | 4.7 to 14.1 | 9.746 | 1.000 | unmodelled |
| G7 | proposed | 26 to 50 | 30.68 | 1.000 | unmodelled; dividend added back (item 25) |
| G7b | proposed | 0.8 to 4 | 1.721 | 1.000 | unmodelled; dividend added back (item 25) |
| G8 | proposed | 2 to 5 | 2.852 | 1.000 | unmodelled; dividend added back (item 25) |
| G9 | proposed | 1.6 to 3 | 2.084 | 1.000 | unmodelled |
| H1 | proposed | -0.69 to -0.13 | -0.5618 | 1.000 | predictive 270 -> 270; the published VIX (item 25) |
| H2 | proposed | at most 0.2 | 0.003546 | 1.000 | predictive 270 -> 270; the published VIX (item 25) |
| H3 | proposed | -0.84 to -0.4 | -0.4677 | 1.000 | predictive 270 -> 270 |
| H4 | proposed | -0.94 to -0.24 | -0.2799 | 1.000 | predictive 270 -> 270 |
| H5 | proposed | -0.35 to -0.02 | -0.3367 | 0.989 | predictive 270 -> 270 |
| PH1 | proposed | 1 to 1 | 1 | 1.000 | unmodelled |
| PH3 | proposed | 1 to 1 | 1 | 1.000 | unmodelled |
| PH4 | proposed | 0.574 to 0.790 | 0.6654 | 1.000 | predictive 270 -> 270, every seam offset (item 20) |
| PH4b | proposed | 0.514 to 0.781 | 0.6068 | 1.000 | predictive 270 -> 270, every seam offset (item 20) |
| PH5 | proposed | 1 to 1 | 1 | 1.000 | predictive 270 -> 270 |
| Q1 | proposed | 0.4 to 0.7 | 0.626 | 1.000 | unmodelled |
| Q2 | proposed | 0.3 to 1 | 0.3582 | 1.000 | unmodelled |
| Q3 | proposed | 0.62 to 0.82 | 0.7091 | 1.000 | unmodelled |
| Q4 | proposed | 0.55 to 0.8 | 0.6618 | 1.000 | unmodelled; on the print (item 25) |
| Q5 | proposed | 0.4 to 0.7 | 0.5717 | 1.000 | unmodelled; on the print (item 25) |
| Q6 | proposed | 0.1 to 0.21 | 0.1272 | 1.000 | unmodelled |
| Q7 | proposed | 0.5 to 0.9 | 0.7797 | 1.000 | unmodelled |
| Q8 | proposed | 0.5 to 0.8 | 0.6313 | 1.000 | unmodelled |
| Q9 | proposed | 0.5 to 0.8 | 0.6688 | 1.000 | unmodelled |
| V2 | proposed | 0.74 to 0.92 | 0.7988 | 1.000 | predictive 270 -> 270 |
| V3 | proposed | 0.42 to 1.67 | 1.188 | 1.000 | predictive 270 -> 270 |
| VC1 | proposed | 0.065 to 0.11 | 0.09405 | 1.000 | unmodelled |
| VC2 | proposed | 1.16 to 1.42 | 1.263 | 1.000 | unmodelled |
| VC3 | proposed | 0.039 to 0.17 | 0.1134 | 1.000 | unmodelled |
| VC4a | proposed | 0.158 to 0.305 | 0.2816 | 1.000 | predictive 270 -> 270 |
| VC4b | proposed | 0.152 to 0.346 | 0.2594 | 1.000 | predictive 270 -> 270 |
| VC4c | proposed | 0.077 to 0.245 | 0.2109 | 1.000 | predictive 270 -> 270 |
| VC4d | proposed | 0.025 to 0.126 | 0.06791 | 1.000 | predictive 270 -> 270 |
| VC4e | proposed | -0.687 to -0.13 | -0.3795 | 1.000 | predictive 270 -> 270 |
| VC4f | proposed | 0.489 to 0.74 | 0.6736 | 1.000 | predictive 270 -> 270 |
| C10c | leak | as registration 18 | 0 | 1.000 | unmodelled, no usage |
| C10d | leak | as registration 18 | 0 | 1.000 | unmodelled, no usage |
| C10e | leak | -13.5 to 15.7 | 3.054 | 1.000 | unmodelled |
| F-bear | leak | at least 0.5 | 0.6091 | 1.000 | predictive 270 -> 270, gate box r14gen |
| F-stress cut (rate > 0.25) | leak | 0.5 to 1 | 0.9652 | 1.000 | predictive 270 -> 270, gate box r14gen |
| F-stress hike | leak | at most 0.1 | 0.01334 | 1.000 | predictive 270 -> 270, gate box r14gen; the CPI < 4 gate (item 25) |
| H1-100y | leak | at least 0 | 0.1714 | 1.000 | normal, readings se, grade/stage-B size 1 |
| I-rate | leak | as registration 18 | 4.441e-12 | 1.000 | unmodelled, no usage |
| L-rate:IGCORP-4-sign | leak | as registration 18 |  | 1.000 | unmodelled |
| L-rate:IGCORP-5-sign | leak | as registration 18 |  | 1.000 | unmodelled |
| L-rate:UST10Y-4-sign | leak | as registration 18 |  | 1.000 | unmodelled |
| L-rate:UST10Y-5-sign | leak | as registration 18 |  | 1.000 | unmodelled |
| R3 held | leak | -0.36 to 0.03 | -0.1579 | 1.000 | predictive 270 -> 270, gate box r14gen |
| R3m | leak | -0.4 to 0.1 | -0.2732 | 1.000 | predictive 270 -> 270, gate box r14gen |
| R4-lag | leak | -0.1 to 0.1 | -0.01537 | 1.000 | predictive 270 -> 270, gate box r14gen |
| R4m | leak | 0.2 to 0.65 | 0.1967 | 0.937 | predictive 270 -> 270, gate box r14gen |
| R7a-pre | leak | as registration 18 |  | 0.842 | normal, margins.py se |
| SF1 | leak | at least 0.75 | 0.8033 | 0.923 | predictive 200 -> 72 (registration 19), ratio of medians, graded files |
| SF2:curve | leak | as registration 18 |  | 1.000 | unmodelled |
| SF2:geo | leak | as registration 18 |  | 1.000 | unmodelled |
| SF2:lc | leak | as registration 18 |  | 0.996 | predictive per-seed |
| SF2:oil | leak | as registration 18 |  | 1.000 | unmodelled |
| SF2:prs | leak | as registration 18 |  | 1.000 | unmodelled |
| SF2:rate | leak | as registration 18 |  | 1.000 | unmodelled |
| SF2:rec_pkg | leak | as registration 18 |  | 1.000 | unmodelled |
| SF3:curve | leak | as registration 18 | -7.88 | 1.000 | normal, readings se, grade/stage-B size 3; the raw worst close-to-close day (item 25) |
| SF3:geo | leak | as registration 18 | -3.364 | 1.000 | normal, readings se, grade/stage-B size 3; the raw worst close-to-close day (item 25) |
| SF3:lc | leak | as registration 18 | -8.094 | 1.000 | predictive per-seed; the raw worst close-to-close day (item 25) |
| SF3:oil | leak | as registration 18 | -1.93 | 1.000 | normal, readings se, grade/stage-B size 3; the raw worst close-to-close day (item 25) |
| SF3:prs | leak | as registration 18 | -1.949 | 1.000 | normal, readings se, grade/stage-B size 3; the raw worst close-to-close day (item 25) |
| SF3:rate | leak | as registration 18 | -7.88 | 1.000 | normal, readings se, grade/stage-B size 3; the raw worst close-to-close day (item 25) |
| SF3:rec_pkg | leak | as registration 18 | -4.29 | 1.000 | normal, readings se, grade/stage-B size 1; the raw worst close-to-close day (item 25) |
| SF4:rec_pkg | leak | as registration 18 | 0 | 1.000 | unmodelled, no usage |
| SF5:curve | leak | 3 to 13 | 6.064 | 1.000 | normal, readings se, grade/stage-B size 3 |
| SF5:geo | leak | 3 to 13 | 8.292 | 1.000 | normal, readings se, grade/stage-B size 3 |
| SF5:lc | leak | as registration 18 | 8.105 | 1.000 | predictive per-seed |
| SF5:oil | leak | 3 to 13 | 5.964 | 1.000 | normal, readings se, grade/stage-B size 3 |
| SF5:prs | leak | 3 to 13 | 5.957 | 1.000 | normal, readings se, grade/stage-B size 3 |
| SF5:rate | leak | 3 to 13 | 6.064 | 1.000 | normal, readings se, grade/stage-B size 3 |
| SF5:rec_pkg | leak | 3 to 13 | 6.009 | 1.000 | normal, readings se, grade/stage-B size 1 |
| T1 median bp | leak | at most 50 | 19.8 | 1.000 | unmodelled |
| T1 share over 1.7% | leak | at most 0.1 | 0 | 1.000 | unmodelled |
| T2 days a decade over 50 bp | leak | at most 1 | 0.01667 | 1.000 | unmodelled |
| T2 sd bp | leak | 1.5 to 6.2 | 3.903 | 1.000 | unmodelled |
| T3 bp | leak | at most 25 | 1.674 | 1.000 | unmodelled |

### The pt-v22 rows

From `longrun/CRITERIA-pt-v22.md` (registered before any derivative was
built). Sizes are the grade's: 270 histories of 21 years unless stated.

| row | statistic | band | estimator | ships |
|---|---|---|---|---|
| IF1 | sd of the front index future's basis to carry fair value, bp | 1.209 to 6.267 | fixed | yes |
| IF2 | lag-1 autocorrelation of that basis | 0.127 to 0.716 | fixed | yes |
| IF3 | front future at expiry less the opening-print index | 0 to 1e-12 | construction | yes |
| IF4 | day TWAP shortfall at 10% of volume, daily sigma (30 seeds) | 0.100 to 0.210 | adopted Q6 | yes |
| NS1 | night share of the forty's cash index at the opening prints (item 25) | 0.300 to 0.600 | adopted G1c | yes |
| NS2 | front future at 09:29 less its fair value on the opening prints, before the first tick (owner, 2026-10-08), sd bp | 0 to 6.267 | construction | yes |
| NS3 | untraded, every switch on: known answers and rows equal | equality | construction | withdrawn (item 10) |
| RF1_m1 / m3 / m6 | policy-rate future premium at 1, 3, 6 months, bp; m1 on the month's first session (item 25) | -0.07 to 2.62 / 3.5 to 17.5 / 7.96 to 53.44 | literature, free data | yes; 12 months reported |
| RF2 | RMS of the front policy-rate future's change, meeting over other sessions, unscheduled moves left out (item 25) | 1.064 to 3.320 | 2se | yes |
| RF3 | settlement less the realised average policy rate | 0 to 1e-9 | construction | yes |
| RF4 | eight-quarter strip less the 2-year yield, bp | -160.3 to 70.6 | fixed | yes |
| RF5 | policy-rate forecast unbiased at 63, 126, 252 sessions | 0 to 2 | construction (stationary arm) | yes; see "Open" 2 |
| OF1 | share of sessions with oil contract 1 above contract 4 | 0.396 to 0.638 | 2se | yes |
| OF2 | correlation of ln(F4/F1) with inventory's deviation | 0.443 to 0.665 | 2se | yes |
| OF3 | elasticity of contract 4's daily change to contract 1's, contract 1's expiry left out (item 25) | 0.485 to 1.063 | fixed | yes |
| OF4 | oil forecast unbiased at 21, 63, 126 sessions | 0 to 2 | construction (stationary arm) | yes |
| MG1 | share of one-session moves past initial margin, front contracts | 0.005 to 0.015 | construction | yes |
| MG2 | margin calls not met by the next open; accounts below zero | 0 | construction, traded | yes |
| MG3 | the share of single-name option portfolio sessions whose one-session loss passes the portfolio's initial margin set at the close before (item 22) | 0 to 0.00112 | 2se (tape: the forty, 2015-2025) | yes |
| XD1 | futures rules on published data | C10a's bound, the tape allowance (item 6, re-measured in item 25) | traded | yes, VIX rules not graded |
| XD2 | the basket at the open on the night futures move | C10a's bound | traded | yes |
| XD3 | pushing the expiry prints at the position limit (30 seeds) | at most 0 | construction, traded | yes, VIX push not graded |
| SS1 | median name 30-day IV over next 21 sessions' realised vol, the five largest names (item 25) | 1.083 to 1.407 | fixed | yes |
| SS2 | median 3-month implied correlation, x 100 | 34.12 to 47.66 | 2se | yes |
| SS3 | median implied less realised correlation | -0.078 to 0.185 | fixed | yes |
| SS4 | name skew less steep than index skew | above 0 | directional | yes |
| EV1 | log change of 30-day IV across the report, median over years (item 25) | -0.390 to -0.137 | fixed | yes |
| EV2 | log change of 30-day IV over the 15 sessions before, median over years (item 25) | -0.062 to 0.164 | fixed | yes |
| EV3 | RMS implied event move over RMS realised move, per year, median over years (item 25) | 0.719 to 1.305 | fixed | yes |
| AX1 | exercise violations, the agents' included (item 25) | 0 | construction | yes |
| AX2 | delivery error, dollars | 0 to 0.005 | construction, traded | yes |
| XD5 | single-name option rules | as XD4 | traded | yes |
| VF1 to VF8 | the VIX futures curve | CRITERIA-pt-v22.md | | not graded (item 11) |
| SV1 to SV9, OB1 to OB6, XD4 | the index surface and index option strategies | CRITERIA-pt-v22.md | | not graded (item 11) |

## The candidate on held-out seeds

### R22-fixd2RWPD: stage B, R22-fixd2RWP's reused whole (2026-10-30)

Session 5's desk checks (`v22-finalread-report.md`, "Registration 21 stage B"; rule first, `reg21/RULE.md` 050572c3, results `reg21/DESKCHECK.md`), on the dial's commit e132a66a against R22-fixd2RWP's stored stage-B outputs: gen (8701, 5,292 sessions) equal, 26/26 and 45/45 arrays; futures (9001, 21 years) equal to the last digit (Mac against Linux, about 1e-16); traded (9001, 21 years, F's script and switches) equal to the last digit. With the dial alone each is equal. By code, only `ptv22traded.py` uses a Portfolio, the one place the dial is read; every other stage sees it only in the arm's digest. The dial's case (a call made below zero that an overnight recovery would meet) is in none of F's 270 stored traded histories: 267,495 calls and 2,393 closes below zero, each liquidated at the next open.

So nothing moves: this arm's stage B is R22-fixd2RWP's, reused whole, and so are its joined gate and v21 test (joint 0.7410 as fallback F read it), with no box.

Grades 21 and 22 are not held-out reads: their seeds were the exam seeds, read once (above, "Why a twenty-second
registration" and "Why a twenty-third registration"). This arm's held-out read is the stage B above, unchanged.

Grade 20 is not a held-out read either: its seeds were the exam seeds, read once (above, "Why a twenty-first
registration"). The rest of this section is registration 20's, kept as the record.

R22-fixd2RWP's held-out read is fallback F's stage B, below ("R22-fixd2RWP (2026-10-27)"): joint 0.7410 on the
readers F ran, which `ptv22/joined/joined19.py` reproduces row for row (item 29). Session 3's r4m20 base re-read
its gate box's 270 under the item-25 readers ("Why a twentieth registration": R4m 0.938, H5 0.782).

Grade 19 is not a held-out read: its seeds were the exam seeds, read once (above, "Why a twentieth
registration"). The sections below are registration 19's candidates, kept as the record.

### R22-fixd2RWP (2026-10-27): the gate passes, no row worse than v21

Stage B by reuse (session 5, design `ptv22/finalread-v22` e4aee9c0, `fallbackF/READ-F.md`; engine `ptv22/candidate`
f49e9420 and df4f2e4b): R22-reg19n's stored shards for the 16 stages the arm's added dials cannot move (desk checks,
addenda C-D and G), ten stages re-run on the candidate (box, c4, driven2020, recession, sf, regr, ptv22names on 96,
ptv22traded, ptv22fut, c10), R22-reg19n's gate box and enlarged reads, SS and EV from the fix round's chain on 96,
and the scenario part re-read on the fixed gate box (b5285eac; box v22fbRWP2, 9001-9200) with `pinned_vix_return`.
`pinned_vix_return` moves only pinned sessions, so the rest of R22-fixd2RW's stage B stands for the arm.

**The joined gate:** 191 rows, **joint 0.7410** (R22-reg19n 0.7069), pessimistic 0.4988; **no row under 0.9.**
The underlying's 158 rows 0.8148, the futures' 22 0.9140 (OF4's 0.914 is reported, not gated, item 21; every other
futures row 1.000), the names' 11 0.9950 (EV3 0.995; SS1-SS4, EV1, EV2, AX1, AX2, XD5 1.000).

**Items 20 to 24 on the arm:**

| row | item | reading | band | chance |
|---|---|---|---|---|
| PH4 | 20 | 0.6654 | 0.574 to 0.790 | 1.000 |
| PH4b | 20 | 0.6068 | 0.514 to 0.781 | 1.000 |
| OF4 | 21 | read and reported (0.914 as a construction row) | reported, not gated | reported |
| MG3 | 22 | 0.0000518 (9001-9064) | 0 to 0.00112 | 1.000 |
| MG2, option accounts | 23 | 0 in 0 histories (9001-9064; the registered count 47, all long options no book bids) | 0 | 1.000 |
| L1 | 24, 25(a) | +71.0 on the index's quarterly reported EPS with v (amendment E; 9001-9024; 70.0 on the earnings cycle, +72.0 on it at 9001-9200) | 1 to 136 (the registered real 68 stands, amendment B) | 24 of 24 seeds in band |

PH4 and PH4b are R22-fixd2's on the gate box's 270 (its free prices are R22-reg19n's, bit for bit). L1 is the gate
box's `driven2020.py` at 23a04ef0 on the arm (design `ptv22/regprep` fcdd4a17, `grade19/dryrun/L1CHECK.txt`), on the
candidate's build; its D2 and F1 equal session 5's read to the digit (D2a 0.388, D2b 152, F1 0.384 in 32.5).

**Against v21 at stage B: 15 better, 78 tie, 3 worse** (CV1, DV6 and V2, all gen rows; on the 270 they tie). R4m's
30-history read (0.196) is under its floor (0.2203 on the 270) and AO5 is not read at stage B, so `decide` reads
DEAD as for R22-reg19n. The scenario rows with the replay pin: no row worse than v21 (S1a 0.617, S2 67.5, R6 -6.89
better; D2a 0.388 and F1a 0.384 move away from the real but stay in band).

**Open:** with `joined_gate.py --push --traded` (XD1 and XD3 re-read without the VIX futures), XD1 fails on the arm
(0.100) and on R22-reg19n alike (0.110), on `oil_long_backwardation`: a property of v22's futures line once the VIX
rules are out. Session 2 is on it.

### R22-reg19n (2026-10-26): the gate passes, no row worse than v21

Design `ptv22/reg19n` (6d054106, b4073862, ffc04aae; results under
`programme/ptv22/results/r22o5czN-B/R22-reg19n/`), engine 88c69d05. Each
re-read's size, seeds and reader were committed before any read
(`reads/RULE.md`).

**Stage B:** R22-reg19's plans with the arm and engine swapped, all 22 shards on
the boxes (v22reg19n-b1 and -b2; the 194-dial line lists no dial at its pt-v20
value, so `longrun.py` took every shard), the gate box v22gateREG19N (its job
unchanged but the arm) and a gen box v22gen19N (the same 270 r14gen histories,
equal to the gate box's in every array, with the names files kept for DV1c).
The stage-B grade reads 145 of 147 gated rows and 40 of 40 registered, as
R22-reg19's did: AO5 is not read there, and R4m's 30-history read (0.196) is
under its floor (0.2203 on the 270, chance 0.931).

**The re-reads**, every enlarged sample at its committed size, seeds and reader
(histories measured on one box, v22reads19N; every reading step on the desk):

| rows | sample | reading | chance |
|---|---|---|---|
| ON1 | 270, 9001-9270 | 0.3992 (se 0.0029) | 0.997 |
| R7a, R7a-pre | 270, 9001-9270 | hike +0.41, cut -0.65 bp; pre +0.73, -0.32 | 0.992, 0.994 |
| SS1-SS4, EV1-EV3 | 96, 9001-9096 | EV3 1.2926, SS1 1.3919 | EV3 0.995, the rest 1.000 |
| MG3 | 64, 9001-9064 (item 22; R22-fixd2RW) | 0.000052 | 1.000 |
| XD5, AX1, AX2 | 96, 9001-9096 | 0 rules broken, 0, 3e-8 | 1.000 |
| the futures rows | 270 x 21 years | 22 rows, none failing | joint 0.914 |
| DV1 as DV1c | 270 r14gen | 5.588 (se 0.177) | 1.000 |

The futures rows are read, as before, on the line without the 16 single-stock
options dials. Its pieces equal the full line's on 9001-9008 in every piece but
the VIX futures' own: `--switches phase1` lists the VIX futures on the 194-dial
line, which no longer writes them off ("Open" 5 and 6).

**The joined gate** (`R22-reg19n/joined/joined-R22-reg19n.txt`), with B9 at the
grade's 90 + 20 and SF1 at 216: 191 rows, **joint 0.707**, pessimistic 0.474.
**No row under 0.9.** Underlying 0.786, futures 0.914, names 0.984. Between 0.9
and 0.95: **OF4 0.914** (construction; "Open" 3) and **R4m 0.931**. Next: PH4b
0.958, SF1 0.975 (1.223 on 200 seeds, read at 216), H5 0.984, B9 0.984, MG3
0.989. D2 0.997, S1a 0.999, F1, L1, S2, RF5 and PH5 pass.

**Against R21E1:** **no row is worse.** Stage B's pairing
(`R22-reg19n/R22-reg19n-vs-v21.txt`, R21E1's scenario rows on the same corrected
files): **15 better, 78 ties**, 13 with no se. The 15 are R22-reg19's 13 (S2,
L1, F1b, D2a, O1, O4, A1 2008, A3 2008, R4 held, H4, DV4, DV5, B2) with **G1
and G7**, which the news before the open moves from ties to better. CV1, DV6
and V2 read worse at stage B, as for R22-reg19; they are gen rows and on the 270
all three tie (CV1 +0.060, se 0.035; DV6 -0.010, se 0.045; V2 +0.008, se
0.007). The gen rows on the 270 (Round T's `test7.py`, 2,000 draws): none
worse; H5 +0.020 (se 0.012), a tie as for R22-reg19 (+0.021); R4 held, H3 and
H4 better. DV1c is better (-0.64, se 0.30). DV1 itself reads further from real
on the 270 (+0.76, se 0.19), as R22-reg19's history set did (+0.70); it is
superseded by DV1c (item 7).

### R22-reg19 after the fix rounds (2026-10-23 to 2026-10-25): the gate passes

Three local rounds, $0, no engine change but the option tail (design `ptv22/reg19-fix`, `ptv22/reg19-fix2`
ec87b63a; results under `R22-reg19/fix/`, `fix2/`, `fix3/`). Each sample size and rule was committed before its read.

- **MG3:** the first read (0.614) was a reader bug: the short-strangle account kept delivered shares, so the stock's
  moves counted against an option margin. Fixed, it read 0.0021, under the band. The option scan then got its own
  tail, `margin_scan_option_tail`, derived as `margin_scan_tail` was (0.800). On 96 held-out histories (9001-9096)
  MG3 reads **0.0139**, chance **0.940**.
- **R7a and R7a-pre** on the 270: 0.992 and 0.994.
- **EV3 and SS1** on 96 held-out histories: 1.2926 (0.995) and 1.3919 (1.000).

**The joined gate** (`fix3/joined/joined-R22-reg19-fix3.txt`): 191 rows, **joint 0.592**, pessimistic 0.382.
**No row under 0.9.** Underlying 0.688, futures 0.921, names 0.935. Lowest: OF4 0.921 (construction; see "Open" 3),
SF1 0.923, R4m 0.937, MG3 0.940, PH4b 0.949, B9 0.949, ON1 0.952. No price moved in the fix rounds, so the v21
pairing below stands: no row is worse than v21.

### R22-reg19 at stage B (2026-10-22, before the fix rounds)

Stage B on ptv22/reg19 95553bd0 (design `ptv22/oil-r22o5-screen` eeb350d1 and e9533045,
`programme/ptv22/results/r22o5czN-B/R22-reg19/`): NU-R1FTJ's plans with the arm swapped, boxes v22reg19-b1 and -b2,
the gate box v22gateREG19 (200 held-out seeds 9001-9200 for the scenario rows, on the adopted replay and the
corrected recession file; 150 desk histories and 1,000 openings for B9; OS1 and OS2 on 200 openings; 270 r14gen
histories). `longrun.py` refused the 197-dial line, so the box, xsec, c10 and longrun_pool shards ran on the desk
on the 192-dial line (the same fingerprint) and were merged with the boxes' shards as the box merges them.

**The free gate, joined** (`R22-reg19/joined/joined-R22-reg19.txt`): 190 rows, **joint 0.234**, pessimistic
0.151. The underlying 158 rows 0.504 (DV1 read as DV1c), the futures' 22 rows 0.921, the names' 10 rows 0.505.

| row | chance | reading |
|---|---|---|
| **MG3** | 0 (not in the joint) | 0.614 of sessions past margin, band 0.005 to 0.015; over 0.36 in each of the 12 histories |
| **EV3** | 0.594 | 1.303, band 0.719 to 1.305 (12 histories to 270) |
| **R7a-pre** | 0.842 | cut +2.79 bp, hike +1.33 (stage B's 90) |
| **SS1** | 0.850 | 1.396, band 1.083 to 1.407 |
| **R7a** | 0.858 | cut +2.64 bp, hike +1.19 (stage B's 90) |
| OF4 | 0.921 | ratio of its chance (0.672) to the stationary arm's (0.730); a construction row |
| SF1 | 0.923 | 1.224 on 200 seeds, read at 72 |
| R4m | 0.937 | 0.2207 on the 270 |
| PH4b | 0.949 | 0.603 |
| B9 | 0.949 | annual sd 16.80, start-up 1.013 |

D2 0.997 (D2b 109, D2a 0.329), F1 and L1 1.000, S1a 0.999 (0.711 at 180), S2 1.000 (62.0), PH5 passes (0.624
against the stationary arm's mean 0.444), R3 held -0.145 (1.000), RF5 1.033 of the stationary arm (read at 63 and
126 sessions), DV1c 5.621 (1.000). Unmodelled rows failing on their reading: none.

**Against R21E1** (`R22-reg19-vs-v21.txt`; R21E1's scenario rows on the same corrected files): 80 ties, 13 better
(S2, L1, F1b, D2a, O1, O4, A1 2008, A3 2008, R4 held, H4, DV4, DV5, B2), 13 with no se, 3 worse at stage B: CV1,
DV6 and V2. Those three are gen rows; on the 270 (the owner's ruling of 2026-10-19) they tie (nu-r1 Round T on
R1FTN, whose 270 histories equal these in every array but the state hash). R4m's 30-history stage-B read (0.197)
is under its floor; on the 270 it is 0.2207 against R21E1's 0.2189, both in band. **No row is worse than v21** by
the owner's rules. pt-v21 has none of the futures and options rows.

## What it costs

A grade runs six boxes through `r13reg/grade19/boxes19.sh` on c8g.16xlarge (roles b1, b2, gen, reads, gate and
traded, with their measured dead-man times), each launched through `guard19.py`'s checks:

- Stage B's five boxes cost $2.33 on R22-reg19n (b1 $0.58, b2 $0.43, gate $0.24, gen $0.13, reads $0.95).
- `ptv22traded` on the registered line has not been timed at full size: 36 to 72 box-minutes, about $0.35 to
  $0.75 more.
- In all about **$2.7 to $3.5** a grade. Session 5's stage B of the arm (by reuse of R22-reg19n's shards, ten
  stages re-run on the candidate) cost $7.46, which is not a grade's cost.
- **Grade 19 cost $4.34** (b1 $0.64, b2 $0.62, gen $0.18, reads $1.23, traded $0.57 on spot in us-east-1; the
  gate $1.10 on demand in us-east-2, with no spot capacity there), and proof-19's S3 part $0.20.
- **Grade 20 cost $3.47** (six spot boxes: gen $0.15, gate $0.28, b2 $0.57, traded $0.59, b1 $0.64, reads
  $1.24), and proof-20's S3 part $0.29.
- **Grade 21 cost $5.39** (gen $0.18, b1 $0.20 and $0.35 reclaimed then $1.62 on demand, b2 $0.62, traded $0.27
  reclaimed then $0.59, gate $0.29, reads $1.28), and proof-21's S3 part $0.32 on demand.
- **Grade 22 cost $3.43** (six spot boxes, none reclaimed: gen $0.15, gate $0.30, b2 $0.56, traded $0.63, b1 $0.61,
  reads $1.18), and proof-22's S3 part $0.55 on spot.
- **The ledger stood at $122.17 of the programme's $135** after grade 22 (`v22-grade22-report.md`): a grade at
  grade 22's cost fits, and at grade 21's ($5.39, with reclaims and an on-demand relaunch) too; $12.83 left.

## The release blockers already known

- Commit messages on `ptv22/candidate` not on `main` (`v22-regdryrun-report.md` blocker 6): a Co-Authored-By
  trailer on 3ae73ec0, 07669553, 284aa6ad, a362731d, aeb7bde6 and 28b46d95, and a private path in 0d73b1e4,
  e86c3b94 and 3114ddf8. Three are on `dev` (3ae73ec0, 0d73b1e4, e86c3b94), the rest come with the ptv22
  branches. The files are clean. Fixing any of them is a history rewrite (on `dev`) or a squash at release: the
  owner's call.
- `tests/test_no_private_paths.py` must exempt `validation/pt-v22/` once the grade is published, as
  `record.py`'s `PUBLISHED_GRADES` will (blocker 7).

## Open before registering

**Known now** (2026-11-01):
- Grade 22's result (above): FAIL on C4a alone (Roll 2.05x against 2x, ACF1 -0.059 against -0.05; the draw, not the
  dial, `v22-c4a-report.md`); grade 21's failure (RF5) passed at 0.562.
- Registrations 21's and 22's own preparation, kept: the grade job on `ptv22/regprep` (items 26-34), proof-21's and
  proof-22's rules.
- The grade job's preparation for this registration on `ptv22/regprep`: registration 22's spent grade-seeds.json
  removed (697bc5cb) and MANIFEST19.json's three slots emptied (34c4e59e), so every launch is refused until this
  registration fills them; the proof's known list (681327b7); the fresh exam seeds built in code (54423a5f, item 35);
  the dry run's LAYOUT23 case (eaad0ef6) and the manifest written again (5c8c5db4); dry run 97 as expected, 0 unexpected.

**Open:**
1. **The arm: R22-fixd2RWPD, registrations 21's and 22's, unchanged** (dev-47, 2026-11-01): its file
   `r13reg/arms/arm-R22-fixd2RWPD.txt` (committed at aba0c1ff, line sha c8fa2ab6), custom-9ac114fd at the pin. Its
   stage B and joined gate are R22-fixd2RWP's, reused whole.
2. **The pin: `ptv22/candidate` e132a66a, registrations 21's and 22's, unchanged** (session 1's v22pin3), the dial 0 on every
   preset; `digests.py` already holds `ARM_FINGERPRINTS = {"R22-fixd2RWPD": "custom-9ac114fd"}` and `PRESETS_DIGEST`
   abdfc2bc (aba0c1ff).
3. **The known risks, read as registered:** C4a (2.05x at grade 22; it fails this arm about 0.32 of grades,
   `v22-c4a-report.md`), RF5 (0.562 at grade 22, 2.037 at 21; about 19% of grades, `v22-rf5-report.md`), PH5's
   return clause (1.95 se at grade 19, 2.09 at grade 20, 0.04 at 21), F-bear (usage 1.00), DV6 (0.914), Q4 (0.893),
   R4m (0.883) and H5 (0.878). This draft changes nothing for any of them. Session 3's options for C4a (regrade as is,
   the draft's choice here; more histories; the Roll leg as a median over histories) and for RF5 (its bound in basis
   points; the calibrated |t|) are the owner's: any form change would be an amendment registered before any exam seed
   runs, not a reading after. dev-47 put C4a's median in the v23 gate proposal (2026-11-01).
4. **At the registration commit, in order:** merge `ptv22/plan-rev` into `ptv22/regprep`; `layout.py --write`
   (`r13reg/grade-seeds.json`) and its print into the seed table here, then the proof (`proof-23.txt`); fill
   MANIFEST19.json's three slots (the arm and pin slots as registrations 21's and 22's, the seeds slot new); swap the gate
   box's seed files and rewrite its manifest (`seedswap.py`); `digests.py` stays as registered at aba0c1ff; then
   `guard19.py write`, push, the dry run and the real-slot checks (runbook 2.6; `grade19/dryrun/realdry.sh
   --registered` on design `s4/v23-realdry` checks the slots against the arm file, the seeds file and the pin).
5. **The proof's known list** has, before proof-23 (681327b7): registration 22's own rule moved to history and
   scoped to its set (as 19's to 21's were), and this file's seed table and patch ruled by path, as the twenty-second's
   were. The general rule for the registration's own commits (febe14a1) stays. Other copies and notes the proof finds
   are ruled as registrations 19's to 22's were.
6. **The budget:** $122.17 of $135 after grade 22; a grade at grade 22's cost ($3.43) or grade 21's ($5.39) fits.
7. **Release blockers already known** (above): the commit messages and the published grade's private-path
   exemption.
8. **Reported, not blocking:** `verdict16.py --registration19` lists the rows the draft supersedes or reports (DV1,
   PH4 and PH4b at offset 0, the registered E1, R3's last print, SF1's per-seed median, and R6) and doesn't count
   them.
