# Support and long-term support

**Status: a draft policy.** Nothing here is in force until the owner adopts
it and the first long-term support (LTS) release is tagged. Until then the
supported line is the one `SECURITY.md` names.

tradefloor moved fast in 2026: 0.3.0 on 2026-08-27, 0.8.1 on 2026-09-24,
and five default presets in that month. That is fine for exploring and bad
for a study that has to stay pinned to one model for the life of a grant.
This page says what a researcher can pin to, and what we promise about it.

## Guarantees on every release line

These hold today, on every release line, LTS or not:

- **Old releases stay installable.** Every version on PyPI and crates.io stays published. crates.io versions can be yanked but never replaced, and we do not yank a release to hide a model change.
- **A shipped preset never changes.** A preset's coefficients are fixed when it first ships in a tagged release. A better coefficient is a new preset with a new name. `pt-v1` still runs exactly as it did in 0.3.0.
- **The fingerprint cannot lie.** `ModelParams.fingerprint()` hashes every coefficient's bit pattern. A vector equal to a shipped preset reports that preset's name. Anything else reports `custom-XXXXXXXX`.
- **The same seed gives the same market on every platform.** Each release runs one fixed simulation on five platforms and stops if any digest differs (`tests/known_answer.json`).

A preset under development, on a branch and in no tagged release, can still
change. pt-v19 went through five compositions before it shipped in 0.8.0.
Only the vector a tagged release ships is frozen.

## The LTS line

**Proposal: the first release whose default preset is pt-v20 starts the
first LTS line.** If that release is 0.9.0, the LTS line is 0.9.x.

pt-v20 is the right place to start, not pt-v19. The release that makes it
the default carries the changes listed under "Coming in pt-v20" in
[MODEL.md](MODEL.md): the agent order-flow fix, quotes centred on the model
price, the herding retune, and the opening mispricing. A study pinned to pt-v19 would pin known defects in
how an agent's trades reach the price.

### Support period

- **24 months** of bug and security fixes from the day the LTS line's first version is tagged.
- After that, the line stays installable forever and gets no more fixes.
- The next LTS line is named at least 6 months before the current one ends, so the two overlap.

### Allowed changes in an LTS patch

- Bug fixes that leave every known-answer digest unchanged: the simulation digest and the combined digest in `tests/known_answer.json`, and the per-preset digests described below.
- Security fixes, under the same condition.
- Wheels for a new CPython version or platform, if they build from the same source and reproduce the same digests.
- Documentation and error messages.

### Fixed for the life of an LTS line

- **Preset coefficients**, for every preset the line ships. Each preset's `ModelParams.fingerprint()` stays the same.
- **Known-answer digests.** A digest that moves means the trajectory moved, and a trajectory change cannot ship in an LTS patch.
- **The default preset.**
- **The random draw schedule.** How many draws are taken, from which stream, in what order.
- **The public API**: nothing is removed or renamed, no signature changes in a way that breaks a call, and no new features.
- **Saved formats**: a checkpoint, `RunManifest` or recorded transcript written by one patch release loads in every other patch release of the same line.

### Errata for trajectory bugs

It does not go into the LTS line. The line gets an erratum instead: the
defect is written down in the release notes and in `tradefloor.envelope`,
where `check()` can refuse the question it affects. The fix ships in the
next minor release, as a new preset if it changes coefficients.

This is the trade the LTS line makes. A known defect that stays put is
better for a published result than a fix that silently changes it.

## Before the first LTS tag

Two things this policy relies on are not in place yet:

1. **One known-answer digest per shipped preset.** Today the known-answer test hashes one simulation on the default preset. The claim that every older preset replays exactly rests on the coefficient fingerprints and on the test suite, not on a digest per preset. The LTS release should add one short digest per preset, so the claim is checked on every platform at every release.
2. **A DOI per release.** `CITATION.cff` and `.zenodo.json` are ready, and the Zenodo integration has to be switched on by the owner, as the "DOI (Zenodo)" section of [RELEASING.md](https://github.com/simoncoombes/tradefloor/blob/main/RELEASING.md) describes.

## Freezing and retiring presets

**Frozen.** A preset is frozen when it first ships in a tagged release.
From then on its coefficient vector, its fingerprint and its known-answer
digest are fixed. A later release may re-measure a frozen preset and
publish new figures about it. It never changes what the preset computes.

**Default.** A new preset becomes the default only in a minor release
(0.x.0), never in a patch. The release notes say which preset moved and
what a user will notice. A run that names its preset replays exactly across
the move. A run that relied on the default does not, which is why every
published result should name its preset.

**Retired.** A preset is retired when it is no longer measured or
recommended: its record says retired and why, and the docs list it under
retired presets. A retired preset keeps running exactly, and it stays in
the package. Presets are not removed. Removing one would break every result
that cites it, and old results are the reason presets exist.

## Details for a paper

Name all of these, so a reader can rebuild your market:

- the tradefloor version, and the DOI of that release once one exists
- the preset name and its fingerprint (`tf.model_preset()`)
- the seeds
- the universe (`Universe.random(n, seed=...)` or the roster file) and its fingerprint
- any scenario file, and its digest

`RunManifest` records all of them, and `RunManifest.reproduce()` stops on
the first mismatch.
