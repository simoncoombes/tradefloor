# Release 0.8.5 checklist

What is left between `release/0.8.5` as pushed and 0.8.5 on PyPI, in order.
Everything above step 1 is done: the branch merges `fix/agent-flow-once`,
`feature/order-book-depth`, `feature/bonds` and `docs/model-spec`, the
Rust and Python suites pass on it, and the four known-answer digests are
unchanged. `RELEASING.md` is the runbook, and each step below names the
section of it that applies. The last step is the owner's.

The branch also carries two commits cherry-picked from `dev` (c618089 and
d1cb9a6), because step 4 cannot read the docs register without them. The
rest of `dev` is not in this release: `a46575e` (`envelope.check` accepts
four measured roster mixes), `fbdcac1` (the `forced_flow_threshold`
summary), `d445d9c` (`decay-curve-504.json` moves into `measurements/`) and
`1c653e7` (an openai-agents re-record that `fix/agent-flow-once` supersedes).
An LTS patch takes no new features (`docs/SUPPORT.md`), so if `a46575e` is
wanted in the 0.8 line it has to be merged before the tag.

## Digests on the branch today

| digest | value | moves when pt-v20 lands |
|---|---|---|
| `simulationSha256` | `1e683b96...` | yes, the default preset moves |
| `sha256` (known answer) | `c22d4a02...` | yes |
| `metadataSha256` | `8804ef0e...` | yes |
| `bondsSha256` | `522aeb76...` | yes, its session runs the default preset |
| book `sha256` | `b83323a6...` | yes: `preset/pt-v20` re-bases it to `c765d6a6...` with `BOOK_KAT_VERSION` still 1 |

## 1. Merge the preset

- [ ] Wait for E3 to push `preset/pt-v20` with every criteria row passing.
- [ ] `git merge --no-ff origin/preset/pt-v20` on `release/0.8.5`. A trial
      merge of 9fce931 conflicted in three files, all on comments and blank
      lines: `python/tradefloor/manifest.py`, `rust/src/engine.rs` and
      `tests/test_known_answer.py`. The code on both sides is the same
      (rates hashed after the equities and before the book). Keep the
      release branch's text. `CHANGELOG.md` and `README.md` merged cleanly.
- [ ] Walk `RELEASING.md` section 5b, since the default moves: the envelope
      (`PRESET`, `CERTIFIED`, `MEASURED_504`), the preset record and its
      level and crisis block from a paired run, `KAT_VERSION` and
      `tests/known_answer.json` regenerated on two architectures, test
      expectations pinned to pt-v19, and anything recorded against the
      market. The five LLM fixtures were re-recorded on pt-v19; a replay
      under a different preset raises `ReplayMiss`, so re-record them or
      name `pt-v19` where the notebooks replay them.
- [ ] Prose that names the default: the README (the realism section and
      "`pt-v19` became the default in 0.8.0"), the BibTeX `note` and the
      citing example, `rust/README.md`, `docs/SUPPORT.md`, and the
      `python/` sweep in 5b item 6. `tools/release/check.py` catches the
      README's two lines.
- [ ] `docs/MODEL.md` describes pt-v19 and has a "Coming in pt-v20"
      section. Either bring it to pt-v20 or say at the top that it
      describes pt-v19. `library_docs.py` in the docs repository rewrites
      the name of the preset after the default, so check its output.
- [ ] `docs/SUPPORT.md`, "Before the first LTS tag": it asks for one
      known-answer digest per shipped preset before the LTS tag. The
      owner decides whether 0.8.5 waits for that.
- [ ] Optional: the liquidity-crisis FinRobot study is skipped by the slow
      notebook test until `tests/fixtures/finrobot/liquidity-crisis.json`
      (60 calls) and its four replications are re-recorded.

## 2. Fill the placeholders

- [ ] Engine: `grep -n "PLACEHOLDER pt-v20" CHANGELOG.md` finds five, two of
      them in the release note. The note is 248 words with the placeholder
      sentences in it and the budget is 250, so pt-v20's numbers have to
      fit in about the 45 words those two sentences hold.
      `python tools/release/check.py --version 0.8.5` counts it.
- [ ] Docs: `grep -rn "\[PLACEHOLDER\|\[REMEASURE" tools/docs/learn` in
      `tradefloor-docs`. The build lists them on a preview and refuses them
      on a live build. The release notes page has three
      `[PLACEHOLDER new default]` markers. The presets page shows a
      placeholder row for any default it has no entry for: add a pt-v20
      entry to `PRESETS` in `handoff/Presets.dc.html` and pt-v19 drops to
      reproduction only with its `retired` text. The same page's meta
      description and "The era boundary" paragraph name pt-v19 as the
      default. The docs pages cannot name pt-v20 until the mirrored
      `params.rs` registers it, because `build.check_presets` refuses a
      preset the package does not ship.
- [ ] Rebuild and re-run everything:

      maturin develop --release
      python -m pytest tests/ -q
      TRADEFLOOR_SLOW_TESTS=1 python -m pytest tests/test_examples.py -q
      cd rust && cargo test --offline --release
      python tools/release/check.py --version 0.8.5

      On this machine the one Python failure at f19e254 was
      `test_the_sdks_client_survives_consecutive_decisions`, whose control
      stops failing on openai 3.14.0 and openai-agents 0.22.2. It passes
      on openai 3.19.2 and openai-agents 0.22.3, the versions the fixture
      was recorded with.
- [ ] Push `release/0.8.5`. The AWS box clones by branch name.

## 3. The docs branch against the merged engine

In `tradefloor-docs` on `release/0.8.5`, with `TRADEFLOOR_PYTHON` naming an
interpreter that holds a build of the engine branch:

- [ ] `python tools/docs/learn/mirrors.py --source <engine checkout> --ref origin/release/0.8.5`
- [ ] `python tools/docs/learn/library_docs.py`
- [ ] Regenerate `params.py`, `api.py`, `records.py`, `envelope.py` and
      `experiments.py`, then document the API pt-v20 adds (5c), and
      re-read every traded figure on the pages against the new default.
- [ ] Commit the sources, `python tools/docs/learn/build.py` (preview),
      commit the build, run it once more so the dates settle, and
      `python tools/docs/check.py`.
- [ ] `python tools/remeasure/resync.py --lines` from the engine checkout
      with `TRADEFLOOR_DOCS` set, and commit the register in the docs repo.
- [ ] Push the docs branch.

## 4. The remeasure box (RELEASING step 4)

- [ ] From `tradefloor-design`, with `TRADEFLOOR_DOCS` at the docs checkout
      of step 3, run the commands in the header of
      `tools/calibration/aws/user-data-remeasure.sh`: upload the register
      tarball as `in/remeasure-0.8.5-register.tgz`, launch run
      `remeasure-0.8.5` on c8g.24xlarge with `BRANCH=release/0.8.5`, then
      `status`, `collect` into `tools/remeasure/out-0.8.5` and `reap`.
- [ ] Cost: spot c8g.24xlarge was $0.913 an hour in us-east-2c on
      2026-09-24. About 30 minutes of box time with the build, so about
      $0.45. The dead-man switch caps it at 90 minutes, $1.37.
- [ ] Check `meta.groups_run` says a full run, then read "Doc edits
      needed". Each MOVED row is an edit in the docs repo (and in the
      register, which `resync.py --report` and `--apply` help with).
      Rebuild the docs and repeat until the report is clean.
- [ ] Commit `tools/remeasure/out-0.8.5/` on the engine branch and push.

## 5. The determinism gate (RELEASING step 7)

- [ ] `gh workflow run determinism.yml --ref release/0.8.5 -f targets=all`
- [ ] Read the run you started, not the newest in the list, and check its
      `headSha` is the branch head.

## 6. The pull request to main

- [ ] Open the PR `release/0.8.5` into `main`. The required checks on
      `main` are `all targets agree` and `the suite is green` (the runbook
      says `build`; the branch protection names these two), and the
      protection is strict, so the branch has to be up to date with `main`.
- [ ] If the dispatch in step 5 ran before the PR's last commit, dispatch
      it again for the head.
- [ ] Before the tag, the owner switches Zenodo on (step 9), or 0.8.5 gets
      no DOI.
- [ ] The owner merges.

## 7. Tag and publish

- [ ] `git fetch && git rev-parse origin/main` against the merge commit.
- [ ] `CITATION.cff` `date-released:` is the day of the tag. It says
      2026-09-24 now.
- [ ] `git tag -a v0.8.5 -m "..." origin/main` and `git push origin v0.8.5`.
      That runs `release.yml`: five wheels and the sdist, the verify job,
      PyPI and crates.io in parallel by Trusted Publishing, then the
      GitHub release cut from the section above `<!-- release-note-ends -->`.
      Nothing to run by hand. A crates.io version cannot be replaced.
- [ ] Watch both registries publish.

## 8. After the tag

- [ ] Install from outside the tree and ask it what it is:

      python -m venv /tmp/rel && /tmp/rel/bin/pip install tradefloor==0.8.5
      /tmp/rel/bin/python -c "import tradefloor as tf; print(tf.version(), tf.model_preset()['name'])"
      /tmp/rel/bin/pip install --no-binary :all: tradefloor==0.8.5

- [ ] Reproduce the known-answer digests inside the installed wheel against
      `tests/known_answer.json` and `tests/known_answer_book.json` from the
      tag.
- [ ] docs.rs: `https://docs.rs/tradefloor/0.8.5`. A 404 in the first
      minutes is the queue; compare with an earlier version after ten.
- [ ] The docs site, RELEASING step 5: a venv with the released wheel,
      `mirrors.py --source <engine> --ref v0.8.5`, `params.py --check
      --python /tmp/rel/bin/python`, `api.py`, `build.py --target live`
      (it refuses any marker left from step 2), `check.py`, then a PR into
      `main` in `tradefloor-docs`. The owner merges, which deploys. Then
      `curl -sI https://tradefloor.dev/` and
      `python tools/docs/learn/indexnow.py`.
- [ ] Merge `main` into `dev`, as the runbook's last shipping step says.
      `dev` already holds c618089 and d1cb9a6, so those merge as no-ops.
- [ ] Delete the working branches: `integration/0.8.5` (superseded by this
      branch) and, in the docs repository, `release/0.8.5-api` and
      `release/0.8.5-figures`.

## 9. Zenodo (the owner's)

- [ ] Before the tag: sign in at https://zenodo.org with GitHub, open
      https://zenodo.org/account/settings/github/, press "Sync now" and
      switch `simoncoombes/tradefloor` on. Zenodo archives only GitHub
      releases published after the switch (`RELEASING.md`, "DOI (Zenodo)").
- [ ] After the release: open the new record, check the title, author and
      licence, and copy the concept DOI.
- [ ] Put the concept DOI in `CITATION.cff` (`doi:`), in the README's
      BibTeX entry in place of `10.5281/zenodo.XXXXXXX`, and on the docs
      Install page. That is a documentation change, which an LTS patch
      allows.
