# ptv21c1: pt-v21's by-name certification box

One box grades pt-v21 and pt-v20, both by name, on the protocol of box ptv20g6
(validation/pt-v20): the 40 rows of pt-v20's twelfth registration, with the
same seeds, the same scripts (byte-identical to the archive g6 unpacked) and
the same desk grading. The only changes are in ptv21-cert-jobs.sh: the arms,
the build check (pt-v21 by name, the default, cycle_equity_hazard_opening
0.011), C9's impact curve on pt-v21 as well as pt-v20, the scenario sizes on
pt-v20 and pt-v21, the preset panel for both presets (so the record and the
long run come from one commit), and the pooled long run below.

The long-run rows are read on 270 histories, as the eighteenth registration
reads them (owner decision after grade 17; r13reg/lr270.py): A1-A3, B1-B8,
C1, C2, V1 and B9's annual spread. The 270 are the long run's 90 (g6's seeds
101-130, 401-430, 701-730) and a pool of 180 recorded by the same instrument
(longrun.py measure, step 1b of the jobs): the long run's three blocks offset
by 50000 and by 60000, the offsets seedplan.py's longrunpool protocol puts on
its long run (50101-50130, 50401-50430, 50701-50730, 60101-60130,
60401-60430, 60701-60730; none used before). S1a, S1b and S2 read 90 seeds
(REC_SEEDS, the long run's three blocks), as the eighteenth registration
reads them. Every other row stays on g6's seeds. On the desk, cert_lr270.py (lr270.py's build and prepare; it checks
each set against the launched list, because seedplan.py refuses g6's seeds
always) links the 270 into box-c1/lr270/ and writes the report, V1 and the
xsec file with B9's annual spread over them. criteria.py is unchanged; C2's
allowance scales with the number of histories (n // 30, so 9 of 270).

Grade 18 passed (330da15a). Launch from the design repo root, with
rel/0.10-ptv21 pushed (or with ENGINE_PATCHES over the pushed
rel/0.10-integration, 62973726).

## Before launch

    R=programme/results/ptv21
    mkdir -p $R
    cp <these files> $R/          # ptv21-cert-jobs.sh arms-c1.txt cert-arms-c1.txt ptv21c1-command.md cert_lr270.py
    # make the archive identical to g6's: the B50 tape-side cache it carried
    cp ~/Dev/tf-rel010/validation/pt-v20/programme/longrun/data/tape-side-B50-s20260923.json programme/longrun/data/

## The command

    R=programme/results/ptv21; P=programme/results/ptv20
    BRANCH=rel/0.10-ptv21; PIN=$(git -C ~/Dev/tf-rel010 rev-parse $BRANCH)
    KAT=3a063f0d207bc37b5ade3b23c60f5e358b275507550b7f75f172a8d7cafb075c \
    EXTRA_FILES="$R/ptv21-cert-jobs.sh programme/longrun/c4.py programme/results/news-speed/edge.py $P/desk.py $P/xsec.py $P/grade_xsec.py $P/bands.json $P/scenario_size.py $P/scenarios/recession_proposed.yml $P/scenarios/liquidity_crisis_proposed.yml $P/driven2020.py $P/driven2022.py $P/c10.py $P/r7_event.py $P/r7_eval.py $P/recession_rows.py $P/data/covid-2020-2021.json $P/data/driven-2022.json $P/edgar/DBAA.csv" \
    JOBS=longrun/ptv21-cert-jobs.sh SKIP_GATE=1 DEADMAN_MIN=240 \
    EXTRA_ENV="LONGRUN_BASE=pt-v20 LONGRUN_SEED_LIST=101-130,401-430,701-730 XSEC_SEEDS=101-130,401-430,701-730 REC_SEEDS=101-130,401-430,701-730 LONGRUN_POOL_SEED_LIST=50101-50130,50401-50430,50701-50730,60101-60130,60401-60430,60701-60730" \
    bash programme/longrun/box.sh ptv21c1 $BRANCH $PIN $R/arms-c1.txt $R/cert-arms-c1.txt

SKIP_GATE=1 with KAT= is required: eight tests on the branch wait for the
pt-v21 record this box measures, so the pytest gate would refuse. KAT is the
branch's sim digest (KAT_VERSION 29).

## Grading after collect

    .venv/bin/python fleet.py collect --run ptv21c1 --out programme/results/ptv21/box-c1
    R=programme/results/ptv21; B=$R/box-c1; L=programme/longrun
    python $R/cert_lr270.py $B                      # B/lr270: the 270 (prints "270 histories" per arm, or exits)
    python programme/results/ptv20/v1.py $B --out $B/v1.json     # V1 on the 90, reported beside the 270
    (cd $L && python certgrade_box.py ../results/ptv21/box-c1 --engine ~/Dev/tf-rel010 \
       --out ../results/ptv21/certgrade-c1.txt --json ../results/ptv21/certgrade-c1.json)
    python $L/criteria.py --longrun $B/lr270/longrun-report.json --certgrade $R/certgrade-c1.json \
      --edge $B/edge.json --c4 $B/c4a.json --c4 $B/c4b.json --xsec $B/lr270/xsec.json \
      --driven $B/driven2020.json --driven2022 $B/driven2022.json --impact pt-v21=$B/impact-pt-v21.json \
      --c10 $B/c10.json --r7 $B/r7-event.json --r7 $B/r7-eval.json --recession $B/recession.json \
      --v1 $B/lr270/v1.json --arm pt-v20 --arm pt-v21 \
      --out $R/criteria-c1.txt --json $R/criteria-c1.json \
      --verdict $R/verdict-pt-v21-c1.json --verdict-arm pt-v21 --box ptv21c1 --date <date>

Run certgrade against an engine checkout at PIN (~/Dev/tf-rel010 at the
launched commit). criteria.py refuses to write a verdict with an unscored row.
cert_lr270.py refuses unless both sets are exactly the launched seeds, the
pool's arm, base, dials, years and fingerprint are the long run's, and both
ran on one engine commit.

To publish: box-c1/lr270/ without its longrun/ links (the raw histories are
not published, as for g6), plus box-c1/longrun-pool.log and the pool's
meta.json files; cert_lr270.py goes beside the other desk scripts.

## The eighteenth registration's R4 and D1 (supplement ptv21c1s)

ptv21c1 graded R4 and D1 on the twelfth registration's definitions (38 of 40:
R4 at the last print +0.112, D1's index_tail_dn3_pct 0.598 on the 30 cert
seeds). pt-v21 was registered and graded (grade 18) on the eighteenth's: R4
on the held close (the thirteenth registration on) and D1's two level rows
pooled over 360 seeds (owner decisions 11 and 12). criteria.py takes them as
an option, `--definitions reg18 --reg18 FILE`; the default (reg12) is
unchanged, byte for byte, and under reg18 the twelfth readings stay in the
rows and the text, reported. The verdict records the definitions, why, and the
registration's public path (validation/pt-v21/programme/ptv21-registration-18.md).

ptv21c1 recorded neither reading's input (no true-phase histories, no pooled
record), so one more box measures only those, pt-v21 by name, on the same
commit with the same build check: 270 true-phase histories (GEN_SEEDS, the
long run's blocks each extended to 90, as the registration extends its own)
and the pooled level rows on 360 seeds (D1POOL_SEEDS, 200 from the cert cell's
first seed and 160 from 230 past it, as the registration lays out its own).
cert_reg18_box.py runs the registration's r14gen.py and d1pool_box.py with
the launched seed lists in place of seedplan.py's rule and the preset by name.

    R=programme/results/ptv21; X=programme/r13reg/box
    BRANCH=rel/0.10-ptv21; PIN=931ed3d4fe659e4dec765673ee3e280a6b40ab2b
    KAT=3a063f0d207bc37b5ade3b23c60f5e358b275507550b7f75f172a8d7cafb075c \
    EXTRA_FILES="$R/ptv21c1s-jobs.sh $R/cert_reg18_box.py $X/r14gen.py $X/d1pool_box.py $X/seedplan.py" \
    JOBS=longrun/ptv21c1s-jobs.sh SKIP_GATE=1 DEADMAN_MIN=60 \
    EXTRA_ENV="GEN_SEEDS=101-190,401-490,701-790 D1POOL_SEEDS=101-300,331-490" \
    bash programme/longrun/box.sh ptv21c1s $BRANCH $PIN $R/arms-c1s.txt

Grading after collect (ENGINE: a detached worktree at PIN with its extension built):

    .venv/bin/python fleet.py collect --run ptv21c1s --out programme/results/ptv21/box-c1s
    R=programme/results/ptv21; B=$R/box-c1; L=programme/longrun
    python $R/cert_reg18.py $R/box-c1s --engine ENGINE --commit $(cat $B/commit.txt) \
      --gen-seeds 101-190,401-490,701-790 --d1pool-seeds 101-300,331-490 --out $R/reg18-c1.json
    python $L/criteria.py --longrun $B/lr270/longrun-report.json --certgrade $R/certgrade-c1.json \
      --edge $B/edge.json --c4 $B/c4a.json --c4 $B/c4b.json --xsec $B/lr270/xsec.json \
      --driven $B/driven2020.json --driven2022 $B/driven2022.json --impact pt-v21=$B/impact-pt-v21.json \
      --c10 $B/c10.json --r7 $B/r7-event.json --r7 $B/r7-eval.json --recession $B/recession.json \
      --v1 $B/lr270/v1.json --arm pt-v20 --arm pt-v21 --definitions reg18 --reg18 $R/reg18-c1.json \
      --out $R/criteria-c1-reg18.txt --json $R/criteria-c1-reg18.json \
      --verdict $R/verdict-pt-v21-c1-reg18.json --verdict-arm pt-v21 --box ptv21c1+ptv21c1s --date <date>

The supplement measures pt-v21 only, so pt-v20's R4 and D1 are unscored under
reg18 (its reg12 readings are reported).

## Equivalence: grade 18's arm is pt-v21

equiv/equiv.py ran pt-v21 by name on the release build (931ed3d4, 0.10.0) and
R21E1's dials (custom-2dc32068) on ptv21/macro 2024f633 (0.9.1, the build grade
18's screens used): five untraded seeds (201-205) and one traded seed (206,
1709 fills), 504 sessions each. All six trajectory digests are identical
(equiv/byname-931ed3d4.json, equiv/dials-2024f633.json).
