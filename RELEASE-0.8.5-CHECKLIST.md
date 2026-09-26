# Release 0.8.5 checklist

What is left between `release/0.8.5` and 0.8.5 on PyPI, in order.
`RELEASING.md` is the runbook, and each step names the section of it that
applies. The last step is the owner's.

## The branch today

`integration/0.8.5` (2026-09-26) is `release/0.8.5` (6a808c0) with
`fix/ptv20-final` (1b21118, pt-v20's graded arm), `feature/seed64`
(d589c04) and `fix/harness-sandbox` (238571b) merged, one merge commit
each, and RELEASING 5b run on the final vector. It is pushed and not yet
merged into `release/0.8.5`. `release/0.8.5` before it merged
`fix/agent-flow-once`, `feature/order-book-depth`, `feature/bonds`,
`docs/model-spec`, `preset/pt-v20`, `fixtures/pt-v20`, `fix/ptv20-core`
(the Oracle, `fair_value_shift`, the corporate-yield fixes) and
`docs/model-spec-v20`, plus five commits cherry-picked from `dev` (c618089,
d1cb9a6, a46575e, fbdcac1, d445d9c; 1c653e7 is superseded). pt-v20 is the
default, at the vector the grade box ptv20g6 passed 40 of 40 on.

On the integration head the full Python suite (`pytest -n 4`) fails only
the 17 tests that replay the five recorded LLM fixtures (see step 1), and
`cargo test` passes 564 with none failing.

| digest | value |
|---|---|
| `simulationSha256` (KAT 28) | `72485a9f...` |
| `sha256` (known answer) | `ac004fea...` |
| `metadataSha256` | `8804ef0e...` |
| `bondsSha256` | `cac3ff44...` |
| book `sha256` | `81aceb27...` (BOOK_KAT_VERSION 1; `d075094c...` without the state hash) |
| presets, 19 rows | combined `87f0b185...`; pt-v20's row `07ab6e0c...` |
| 64-bit seed line | `cef62229...` (seed 2**63 + 12345, pt-v19) |

These are pt-v20's graded arm, produced on macOS arm64. The eighteen
per-preset rows before pt-v20 are `release/0.8.5`'s and match the published
0.8.1 wheel with the two treasury yields left out; seed64 and the sandbox
moved no digest.

## 1. The last engine changes

- [x] **E3's `fix/ptv20-final` is merged** (1b21118): the graded arm,
      `garch_beta` back at 0.7905, the known answers re-based in place
      (KAT 28 has never shipped).
- [x] **E7's `feature/seed64` is merged** (d589c04), and every digest
      checked again on the merged build.
- [x] **`fix/harness-sandbox` is merged** (238571b).
- [ ] **The independent adversarial audit of pt-v20** re-runs on the final
      commit, after RELEASING 5b and before the PR, and the bar is no open
      blockers or majors. Any finding goes back to E3 for a fix and a
      regrade, and every step from here is re-run on the fixed vector
      before the PR.
- [ ] **The five LLM fixtures are re-recorded once**, with API keys. Until
      then the 17 tests that replay them fail on this branch.
- [x] RELEASING 5b on the final vector: pt-v20's record from the grade
      box's preset panel (ptv20g6), its level block from a paired run on
      this build (`tools/presets/results/level-rows-pt-v20-2026-09-26.json`,
      the control pt-v19 reproducing its four constants), its long-run
      block from `verdict-pt-v20-g6.json`, `envelope_tables.py --write`,
      and the known answers as re-based on `fix/ptv20-final`.
- [ ] Re-run the envelope gap measurements on the final vector
      (`tools/calibration/aws/user-data-envgaps.sh`, one box, about $0.15)
      and fold them into `envelope.py` and `loss.py`. The runs on branches
      `envgaps/pt-v20` (folded) and `envgaps/pt-v20-final` (6fa7462, not
      folded, measured at `garch_beta` 0.85) are superseded by that run.
- [ ] The CHANGELOG's release note (it quotes the ptv20g3 figures), the
      README's realism section, notebooks 00 to 06 and 09 and the pt-v19
      figures left in the docs glossary and core-concepts pages follow the
      final vector. MODEL.md's values follow it on `integration/0.8.5`.

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
- [x] Zenodo's GitHub integration is on for `simoncoombes/tradefloor`
      (the owner, 2026-09-25), so the 0.8.5 GitHub release gets a DOI.
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
      owner, then `curl -sI https://tradefloor.dev/`,
      `curl -sI https://docs.tradefloor.dev/` and `indexnow.py`.
- [ ] Merge `main` into `dev`.
- [ ] Delete the working branches and worktrees: `integration/0.8.5`,
      `archive/integration-0.8.5-677ca51` (the earlier local integration
      branch, renamed when this one was made),
      `flip/a`, `flip/b`, `flip/c`, `envgaps/pt-v20`, `remeasure/pt-v20`,
      and in the docs repo `figures/pt-v20`.

## 8. Zenodo

- [x] The owner switched Zenodo's GitHub integration on for
      `simoncoombes/tradefloor` on 2026-09-25 (`RELEASING.md`, "DOI
      (Zenodo)").
- [ ] After the 0.8.5 GitHub release: open the new Zenodo record, check its
      title, author and licence against `.zenodo.json`, and read the concept
      DOI from it.
- [ ] Put the concept DOI in `CITATION.cff` (`doi:`), in the README's
      "Citing tradefloor" BibTeX entry in place of `10.5281/zenodo.XXXXXXX`,
      and on the docs Install page. That is a documentation change, which an
      LTS patch allows, and the docs change goes through a PR into the docs
      repo's `main`.
