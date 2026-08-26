---
title: Reproducing a run
nav_order: 11
rack: experiment
---

# Reproducing a run

A seed alone does not identify a run. The market an agent trades depends on
that agent's own orders, so one seed with different order flow is a different
market. Reproducing means reproducing every input.

This page is the one to point a methods section at. It lists exactly what
identifies a run, how to serialise each part, and what the library does and
does not guarantee about the result.

## The five things that identify a run

| what | where it lives | in the order log? |
|---|---|---|
| package version and model preset | `pt.version()`, `pt.model_preset()["name"]` | no |
| simulation seed | you chose it | **no, pass it explicitly** |
| universe | `universe.fingerprint`, plus how it was built | **no, pass it explicitly** |
| macro initial conditions | the `Macro` you constructed the engine with | **no, pass it explicitly** |
| every input to the run | `engine.order_log` | yes |

Four of the five are not in the log, and that is deliberate. `replay()` takes
the seed, the universe and the macro as arguments so that replaying a log
against the wrong starting conditions is a thing you have to do on purpose.

### 1. Version and preset

```python
pt.version()                      # '0.2.0'
pt.model_preset()["name"]         # 'pt-v10', the shipped default
```

Coefficients ship as a named preset rather than as constructor keywords, so
two published results can be compared. Quote both: the version pins the build,
the preset name pins the coefficient set.

Read [Conventions](conventions.html) for what the preset dictionary contains
and, just as importantly, what it does not.

### 2. The seed

The simulation seed is one integer and it is yours. It is separate from the
universe seed, and holding the universe fixed while varying the simulation
seed is the standard setup for variance estimation.

### 3. The universe

Every result object carries the universe fingerprint alongside the seed -
`Scorecard.universe_fingerprint`, `facts.measure()["universe_fingerprint"]`,
`Execution.universe_fingerprint` - because the same seed over a different
roster is a different market.

```python
universe = pt.Universe.random(108, seed=7)
universe.fingerprint      # 64 hex characters
```

The fingerprint **verifies** a universe; it does not reconstruct one. Publish
whichever of these built yours, and the fingerprint so a reader can check they
rebuilt the same thing:

- `pt.Universe.random(n, seed=k)` - the two integers are the whole recipe.
- An explicit roster of `Instrument`s - publish the roster.
- `pt.Universe.from_edgar(snapshot)` - publish the snapshot, not the query.
  See [SEC EDGAR](real-fundamentals-from-sec-edgar.html).

Roster **order** is contractual. A re-sorted universe is a different market,
and the fingerprint covers order as well as content, so a reversed roster
hashes differently.

### 4. Macro initial conditions

The macro chain evolves endogenously from the initial conditions you
construct the engine with: the day close advances the economy, and a
`pin_macro` overrides a field from the day it lands. What identifies the run
is the *initial* state, exactly as the seed identifies the noise - the
realised path is reproduced by the replay, not published by hand. The seven
fields are small enough to publish verbatim:

```python
macro = pt.Macro(vix=15.0, federal_funds_rate=0.025,
                 corporate_bond_yield=None, inflation_rate=0.02,
                 qe_pe_boost=0.0, fear_greed_index=50.0, cycle="expansion")
```

`corporate_bond_yield=None` is not the same as `0.0` - see
[Conventions](conventions.html). Record the `None`.

If you drove a macro path with a [Scenario](scenarios.html), serialise the
realised path rather than the constructor call:

```python
shock.to_json(days=60)    # the PATH, not the recipe
```

A recipe is only citable while the constructor that built it keeps behaving
the same way. The realised values are the scenario regardless of what any
later version does.

### 5. The order log

```python
log = engine.order_log                    # plain dicts, JSON-serialisable
same = pt.replay(log, seed=42, universe=universe, macro=macro)
```

The log holds every input the engine consumed: `open_market`, `run_session`
with its hour, minute, day of week, tick count, news and order flow,
`close_market`, `record`, `pin_macro`, listings and delistings, and any
explicit RNG draws. `pin_macro` is logged, so a scenario run replays from its
own log with no special handling.

An unknown operation raises on replay rather than being skipped, because a
replay that silently ignored an entry would produce a market that is not the
one the log describes, and it would look like a success.

## A complete archive

Everything above, in one JSON-serialisable dictionary:

```python
import json
import pretium as pt

universe = pt.Universe.random(20, seed=11)
macro = pt.Macro()
engine = pt.Engine(seed=42, universe=universe, macro_state=macro)
engine.run_days(20)

archive = {
    "pretium_version": pt.version(),
    "model_preset": pt.model_preset()["name"],
    "seed": 42,
    "universe": {"constructor": "random", "n": 20, "seed": 11,
                 "fingerprint": universe.fingerprint},
    "macro": {"vix": macro.vix,
              "federal_funds_rate": macro.federal_funds_rate,
              "corporate_bond_yield": macro.corporate_bond_yield,
              "inflation_rate": macro.inflation_rate,
              "qe_pe_boost": macro.qe_pe_boost,
              "fear_greed_index": macro.fear_greed_index,
              "cycle": macro.cycle},
    "order_log": engine.order_log,
}
json.dumps(archive)                       # archivable as data
```

And the check a reviewer runs:

```python
rebuilt = pt.Universe.random(archive["universe"]["n"],
                             seed=archive["universe"]["seed"])
assert rebuilt.fingerprint == archive["universe"]["fingerprint"]

replayed = pt.replay(archive["order_log"], seed=archive["seed"],
                     universe=rebuilt, macro=pt.Macro(**archive["macro"]))
assert replayed.prices() == engine.prices()
assert replayed.draws_consumed == engine.draws_consumed
```

That makes a published result replayable without the code that produced it, a
divergence bisectable - `replay(log, ..., until=n)` stops after `n` entries -
and an experiment archivable as data. A script may not run next year; a list
of dicts will.

## The archive as one object

Everything on this page, constructed and checked by the library rather than
by hand:

```python
manifest = pt.RunManifest.of(engine, seed=42, universe=universe, macro=macro)
same = pt.RunManifest.from_json(manifest.to_json()).reproduce()
```

`RunManifest` embeds every component above, fingerprints each one, carries
the expected result digest so the reviewer's check runs itself, and refuses -
loudly, naming the culprit - rather than replaying against a changed
component or a build whose trajectories have moved. See
[Sharing a run](sharing-a-run.html).

## What the reproduction guarantees

**Across machines -- measured on all five targets at one commit, one
platform since.** The library carries its own `exp`, `log`, `sin` and `cos`
rather than calling the platform's, specifically so builds on different
operating systems can't drift apart in the low bits. Every release builds
wheels for five targets, runs one fixed simulation inside each, and compares
digests (`.github/workflows/determinism.yml`); any disagreement fails the
release.

That gate has run. At `ad91026` (known-answer v5, the RNG stream split), all
five targets -- Linux x86_64 and aarch64, macOS arm64 and x86_64, Windows
x86_64 -- produced the identical digest, `76983e65...3180eeb`. It has not yet
run against a tagged release. An earlier two-platform record also stands: at
`a5afd1c` (v3), independent Windows x86_64 and macOS arm64 builds agreed on
`112fd73e...6eff337`.

The baseline has moved since the five-target run. The era's model changes
took the known-answer version from v5 to v6 (the GJR asymmetry term), v7
(the market factor's conditional volatility) and v8 (that volatility's VIX
coupling); the current digest, `1ee64998...fe3581c` at v8, was regenerated on
macOS arm64 and has one platform's confirmation behind it until the gate runs
again. Treat "the same seed gives the same market on any platform" as
measured for all five targets at `ad91026`, and as engineering intent --
backed by a test that no platform-varying transcendental reaches the source
(`rust/tests/mathx_parity.rs`) -- for the current baseline.

**Across versions, not at all.** A change to a coefficient, to the universe
generator, or to the engine moves every seed's trajectory. This is why the
version and the preset name belong in the archive alongside the seed. If you
need a number to survive a version change, re-measure it rather than inherit
it - `facts.measure()` takes the same arguments the rest of the library does
for exactly that reason.

## Citing the software

`CITATION.cff` at the repository root carries the citation metadata in
Citation File Format, which GitHub renders as a "Cite this repository" button
and which reference managers read directly. There is no DOI yet, and one has
deliberately not been invented; a Zenodo deposit is the next step.

A citation identifies the software. It does not identify a run. A methods
section needs both: cite the package, then give the seed, the universe
fingerprint and its recipe, the macro initial conditions, the preset name and
the archived order log.
