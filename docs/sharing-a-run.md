---
title: Sharing a run
nav_order: 11.5
rack: experiment
short: Sharing a run
---

# Sharing a run

[Reproducing a run](reproducing-a-run.md) lists the five things that
identify a run and shows how to archive each one by hand. `RunManifest` is
that procedure as one object: written by whoever ran the simulation, checked
by whoever wants to.

```python
manifest = tf.RunManifest.of(engine, seed=42, universe=universe,
                             macro=macro, scenario=shock,
                             strategy=tf.StrategySpec.momentum())
open("run.json", "w").write(manifest.to_json())
```

The reader needs the package and the file, nothing else:

```python
same = tf.RunManifest.from_json(open("run.json").read()).reproduce()
```

`reproduce()` replays the run and checks the rebuilt market against the
digest the manifest carries, so the reader is **told** they got the same
market rather than eyeballing numbers off a page. On success the returned
engine is the published market, bit for bit: prices, mispricing state, GARCH
variance, draw count. On any mismatch it raises, and the error names the
component that disagreed, which is why every component travels with its own
fingerprint rather than one hash over the file.

## Everything is carried, not described

A manifest reproduces if and only if every component is either shipped with
the library or embedded in the manifest. A fingerprint identifies; it cannot
reconstruct. So the manifest embeds the roster itself, the macro initial
conditions, the realised scenario path, the full order log, and the strategy
when it is a `StrategySpec`.

The roster travels as data rather than as a recipe for producing it. A
generator can change across versions, and an EDGAR query is not the data it
returned. Record the recipe or the snapshot hash in `universe_source` for
the methods section.

The one thing that cannot be embedded is a hand-written Python agent, and
the manifest says so rather than pretending. Pass a reference instead:

```python
tf.RunManifest.of(engine, seed=42, universe=universe,
                  strategy="github.com/you/strat at 58837b3")
```

Such a manifest is honestly incomplete. `manifest.complete` is `False` and
`manifest.gaps` says what a reader needs and where. The **market** still
replays in full, because the agent's orders are data in the log; what needs
the referenced code is re-running the strategy itself. This mirrors
`Scorecard.strategy_fingerprint` being deliberately empty for hand-written
agents: the escape hatch declares itself.

## It refuses across an era boundary

A run is only reproducible on a build whose arithmetic matches the one that
ran it, and [across versions, not at all](reproducing-a-run.md). The hazard
is not hypothetical: one calendar day brought three trajectory-changing
fixes (the macro-chain and volume fixes, then the market-factor-sigma
recalibration) while `tf.version()` stayed `0.1.0` and the preset stayed
`pt-v1`. The recalibrated constant is not even in the preset dictionary.
Every name the library could quote held still while the numbers moved.

So the manifest's era identity is behavioural, not nominal: it records the
digest of a small fixed probe simulation (`tf.manifest.era_fingerprint()`),
and `reproduce()` recomputes it **before replaying**. Two builds that agree
on the probe agree on the arithmetic it exercises; two that disagree will
not reproduce each other's runs, whatever their version strings say. The
manifest refuses, naming both builds, rather than replaying into a
plausible market that is not the recorded one. A manifest that silently
produced different numbers across an era boundary would manufacture false
confidence, which is worse than no manifest at all.

The package version, preset name and full coefficient dictionary ride along
as well. They are what a methods section quotes, and a moved coefficient
fails with its key named, but none of them is trusted as the era.

## What a successful reproduction says about platforms

Cross-OS bit-identity is measured by commit, and the five-target gate has
run against the baseline this release ships. At `f722ce3`, the 0.3.0 version
bump, all five targets (Linux x86_64 and aarch64, macOS arm64 and x86_64,
and Windows x86_64) reported the identical known-answer v11 digest,
`60d47572...de590`, which is the `sha256` committed in
`tests/known_answer.json`. It has run against tagged releases too: the
determinism run on each of v0.1.0 through v0.2.0 had all five targets agree.
See [Reproducing a run](reproducing-a-run.md) for the full record. The
manifest records the platform it was written on and claims nothing beyond
that. What it offers is sharper than a claim: a successful `reproduce()` on
a different machine **is** a cross-platform measurement for that run, made
by the reader rather than promised by the library. A failure after every
input and the era have verified is reported as exactly what it is, an
arithmetic divergence, with both platforms named and the draw counts to
start a bisection from (`tf.replay(log, ..., until=n)`).

## Manifest or checkpoint?

Both serialise a run's history; they answer different questions.
[`Checkpoint`](forking-a-simulation.md) is a point to **return to**: fork
it, drive the branches apart, ask what happens next. `RunManifest` is a
finished result to **hand over**: it adds the era guard, the per-component
fingerprints, the result digest and the completeness declaration, none of
which a fork needs and all of which a stranger does.
