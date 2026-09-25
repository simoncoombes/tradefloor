# Release 0.8.5 checklist

What is left between `release/0.8.5` and 0.8.5 on PyPI, in order.
`RELEASING.md` is the runbook, and each step names the section of it that
applies. The last step is the owner's.

## The branch today

`release/0.8.5` merges `fix/agent-flow-once`, `feature/order-book-depth`,
`feature/bonds`, `docs/model-spec`, `preset/pt-v20`, `fixtures/pt-v20`,
`fix/ptv20-core` (the Oracle, `fair_value_shift`, the corporate-yield fixes)
and `docs/model-spec-v20`, plus five commits cherry-picked from `dev`
(c618089, d1cb9a6, a46575e, fbdcac1, d445d9c; 1c653e7 is superseded).
pt-v20 is the default. The full Python suite passes (4,084 passed, 63
skipped, 1 xfailed) and `cargo test` passes 552 of 552.

| digest | value |
|---|---|
| `simulationSha256` (KAT 28) | `b0ef10ef...` |
| `sha256` (known answer) | `92c9cb7c...` |
| `metadataSha256` | `8804ef0e...` |
| `bondsSha256` | `cd6d532d...` |
| book `sha256` | `1e7f1884...` (BOOK_KAT_VERSION 1) |
| presets, 19 rows | combined `33d4efcf...`; pt-v20's row `149d72de...` |

The eighteen per-preset rows before pt-v20 match the published 0.8.1 wheel
with the two treasury yields left out.

## 1. The last pt-v20 regrade

- [ ] E3 regrades pt-v20 on the pooled 90 histories: the 28 registered rows
      and the new D2 row for the driven 2020 path. It may change
      `volume_move_response` for the ten-year gap.
- [ ] If any dial moves: merge `fix/ptv20-core`, then re-run RELEASING 5b
      for the new vector. That means the record and its level block
      (`tools/presets/level_panel.py` on both presets, `level_rows.py`,
      `record.py --level-rows`, `envelope_tables.py --write`),
      `KAT_VERSION` 29 with `tests/known_answer.json` regenerated, and
      pt-v20's row in `tests/known_answer_presets.json` regenerated.
      pt-v20 has not shipped, so its row may still change; the other
      eighteen may not.
- [ ] The CHANGELOG's pt-v20 figures, the README's realism section,
      MODEL.md's values and notebooks 00 to 06 and 09 follow any change.

## 2. The docs branch against the final engine

In `tradefloor-docs` on `release/0.8.5`, with `TRADEFLOOR_PYTHON` naming a
venv that holds a build of the final engine commit:

- [ ] `python tools/docs/learn/regenerate.py --source <engine checkout> --ref origin/release/0.8.5`
      (mirrors, library pages, inventories, experiments, build, commits).
- [ ] Merge the figures branch (`figures/pt-v20`) once its gate is clean.
- [ ] `python tools/docs/check.py` passes all fifteen steps.
- [ ] `python tools/remeasure/resync.py --lines` from the engine checkout
      with `TRADEFLOOR_DOCS` set, and commit the register.
- [ ] The docs repo's CI installs `tradefloor==0.8.5` from PyPI, so it stays
      red until the tag.

## 3. The remeasure box (RELEASING step 4)

- [ ] From `tradefloor-design`, with `TRADEFLOOR_DOCS` at the docs checkout
      of step 2, run the commands in the header of
      `tools/calibration/aws/user-data-remeasure.sh`: upload the register
      tarball as `in/remeasure-0.8.5-register.tgz`, launch run
      `remeasure-0.8.5` on c8g.24xlarge with `BRANCH=release/0.8.5`, then
      `status`, `collect` into `tools/remeasure/out-0.8.5` and `reap`.
      About $0.45 at the spot floor, $1.37 at most.
- [ ] Read "Doc edits needed", fix each MOVED row in the docs repo, rebuild,
      and repeat until clean, then commit `tools/remeasure/out-0.8.5/` on
      the engine branch.

## 4. The determinism gate (RELEASING step 7)

- [ ] `gh workflow run determinism.yml --ref release/0.8.5 -f targets=all`
- [ ] Read the run you started and check its `headSha` is the branch head.
      This is the second architecture for KAT 28's digests, which were
      produced on macOS arm64.

## 5. The pull request to main

- [ ] Open the PR `release/0.8.5` into `main`. The required checks are
      `all targets agree` and `the suite is green`, and the protection is
      strict, so the branch has to be up to date with `main`.
- [ ] Before the tag, the owner switches Zenodo on (step 8), or 0.8.5 gets
      no DOI.
- [ ] The owner merges.

## 6. Tag and publish

- [ ] `git fetch && git rev-parse origin/main` against the merge commit.
- [ ] `CITATION.cff` `date-released:` is the day of the tag.
- [ ] `git tag -a v0.8.5 -m "..." origin/main` and `git push origin v0.8.5`.
      `release.yml` builds, verifies, publishes to PyPI and crates.io, and
      writes the GitHub release from the section above
      `<!-- release-note-ends -->`. A crates.io version cannot be replaced.

## 7. After the tag

- [ ] Install from outside the tree and ask it what it is:

      python -m venv /tmp/rel && /tmp/rel/bin/pip install tradefloor==0.8.5
      /tmp/rel/bin/python -c "import tradefloor as tf; print(tf.version(), tf.model_preset()['name'])"
      /tmp/rel/bin/pip install --no-binary :all: tradefloor==0.8.5

- [ ] Reproduce every known-answer digest inside the installed wheel against
      `tests/known_answer.json`, `known_answer_book.json` and
      `known_answer_presets.json` from the tag.
- [ ] docs.rs: `https://docs.rs/tradefloor/0.8.5`.
- [ ] The docs site: mirror from `v0.8.5`, regenerate against the released
      wheel (`params.py --check --python /tmp/rel/bin/python`),
      `build.py --target live` (it refuses any `[PLACEHOLDER` or
      `[REMEASURE` marker left), `check.py`, a PR into `main`, merged by the
      owner, then `curl -sI https://tradefloor.dev/` and `indexnow.py`.
- [ ] Merge `main` into `dev`.
- [ ] Delete the working branches and worktrees: `integration/0.8.5`,
      `flip/a`, `flip/b`, `flip/c`, `envgaps/pt-v20`, `remeasure/pt-v20`,
      and in the docs repo `figures/pt-v20`.

## 8. Zenodo (the owner's)

- [ ] Before the tag: sign in at https://zenodo.org with GitHub, open
      https://zenodo.org/account/settings/github/, press "Sync now" and
      switch `simoncoombes/tradefloor` on (`RELEASING.md`, "DOI (Zenodo)").
- [ ] After the release: check the record's title, author and licence, and
      copy the concept DOI into `CITATION.cff` (`doi:`), the README's BibTeX
      entry and the docs Install page.
