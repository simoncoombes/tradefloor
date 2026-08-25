---
title: Sharing a run
nav_order: 11.5
rack: experiment
short: Sharing a run
---

# Sharing a run

[Reproducing a run](reproducing-a-run.html) lists the five things that
identify a run and shows how to archive each one by hand. `RunManifest` is
that procedure as one object: written by whoever ran the simulation, checked
by whoever wants to.

```python
manifest = pt.RunManifest.of(engine, seed=42, universe=universe,
                             macro=macro, scenario=shock,
                             strategy=pt.StrategySpec.momentum())
open("run.json", "w").write(manifest.to_json())
```

The reader needs the package and the file, nothing else:

```python
same = pt.RunManifest.from_json(open("run.json").read()).reproduce()
```

`reproduce()` replays the run and checks the rebuilt market against the
digest the manifest carries, so the reader is **told** they got the same
market rather than eyeballing numbers off a page. On success the returned
engine is the published market, bit for bit — prices, mispricing state, GARCH
variance, draw count. On any mismatch it raises, and the error names the
component that disagreed, which is why every component travels with its own
fingerprint rather than one hash over the file.

## Everything is carried, not described

A manifest reproduces if and only if every component is either shipped with
the library or embedded in the manifest. A fingerprint identifies; it cannot
reconstruct. So the manifest embeds the roster itself (never a recipe — a
generator can change across versions, and an EDGAR query is not the data it
returned; record the recipe or snapshot hash in `universe_source` for the
methods section), the macro initial conditions, the realised scenario path,
the full order log, and the strategy when it is a `StrategySpec`.

The one thing that cannot be embedded is a hand-written Python agent, and
the manifest says so rather than pretending. Pass a reference instead:

```python
pt.RunManifest.of(engine, seed=42, universe=universe,
                  strategy="github.com/you/strat at 58837b3")
```

Such a manifest is honestly incomplete — `manifest.complete` is `False` and
`manifest.gaps` says what a reader needs and where. The **market** still
replays in full, because the agent's orders are data in the log; what needs
the referenced code is re-running the strategy itself. This mirrors
`Scorecard.strategy_fingerprint` being deliberately empty for hand-written
agents: the escape hatch declares itself.

## It refuses across an era boundary

A run is only reproducible on a build whose arithmetic matches the one that
ran it — [across versions, not at all](reproducing-a-run.html). The hazard
is not hypothetical: one calendar day brought three trajectory-changing
fixes (the macro-chain and volume fixes, then the market-factor-sigma
recalibration) while `pt.version()` stayed `0.1.0` and the preset stayed
`pt-v1` — the recalibrated constant is not even in the preset dictionary.
Every name the library could quote held still while the numbers moved.

So the manifest's era identity is behavioural, not nominal: it records the
digest of a small fixed probe simulation (`pt.manifest.era_fingerprint()`),
and `reproduce()` recomputes it **before replaying**. Two builds that agree
on the probe agree on the arithmetic it exercises; two that disagree will
not reproduce each other's runs, whatever their version strings say — and
the manifest refuses, naming both builds, rather than replaying into a
plausible market that is not the recorded one. A manifest that silently
produced different numbers across an era boundary would manufacture false
confidence, which is worse than no manifest at all.

The package version, preset name and full coefficient dictionary ride along
as well — they are what a methods section quotes, and a moved coefficient
fails with its key named — but none of them is trusted as the era.

## What a successful reproduction says about platforms

Cross-OS bit-identity is measured by commit. The five-target release gate
has run: at `ad91026` (known-answer v5, the RNG stream split), all five
targets — Linux x86_64 and aarch64, macOS arm64 and x86_64, Windows
x86_64 — produced the identical digest, `76983e65...3180eeb`, each also
passing against the committed baseline. It has not yet run against a tagged
release, and the current digest, `1ee64998...fe3581c` at v8, was regenerated
on macOS arm64 and has one platform's confirmation behind it until the gate
runs again — see [Reproducing a run](reproducing-a-run.html) for the full
record. The manifest records the platform it was written on and claims
nothing beyond that. What it offers is sharper than a claim: a successful `reproduce()` on
a different machine **is** a cross-platform measurement for that run, made
by the reader rather than promised by the library. A failure after every
input and the era have verified is reported as exactly what it is — an
arithmetic divergence, with both platforms named and the draw counts to
start a bisection from (`pt.replay(log, ..., until=n)`).

## Manifest or checkpoint?

Both serialise a run's history; they answer different questions.
[`Checkpoint`](forking-a-simulation.html) is a point to **return to** — fork
it, drive the branches apart, ask what happens next. `RunManifest` is a
finished result to **hand over**: it adds the era guard, the per-component
fingerprints, the result digest and the completeness declaration, none of
which a fork needs and all of which a stranger does.
