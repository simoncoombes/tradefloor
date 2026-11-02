# pt-v22's grade

This folder holds the grade pt-v22 ships on: the twenty-third registration's,
run on 2026-11-01. The boxes ran the engine at e132a66a (package 0.10.2) with
the arm R22-fixd2RWPD, 209 dials on pt-v20. The grade ran the arm as the
custom model `custom-9ac114fd`, and pt-v22 is that arm value for value
(`tools/presets/arms/pt-v22.txt`, `@status graded`). The layout follows
`../pt-v21/`:

- `scripts-as-run.txt` lists every file the five grading boxes unpacked
  before they ran (g23gen-e1a, g23b1-e1a, g23b2-e1a, g23traded-e7a and
  g23reads-e1a): its sha256, the box and its path in that box's archive, and
  its path here.
- `programme/` holds those files at their paths in the design repository at
  the registration's commit.
  - `programme/results/ptv22/BOX/` holds each box's engine commit, archive
    sha256, known answer, wheel and digests.
  - `programme/results/ptv22/grade/` holds the desk's grade: the outputs of
    the nine steps, less the exploit search's per-rule dump.
- `registration-files.txt` lists the registration's own files: its
  document, the arm, the exam seeds and the input manifest.
- `verdict-pt-v22.json` is the verdict the preset record carries
  (`python/tradefloor/presets/pt-v22.json`, `long_run`).
- `level-pool/pt-v22.json` is the record's pooled level read, 360 seeds.
