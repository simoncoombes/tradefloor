---
title: Model presets
nav_order: 8.5
rack: experiment
---

# Model presets

The model's coefficients ship as a named, versioned preset. A preset names
the complete set, frozen and documented: the variance processes, the factor
structure, the mispricing dynamics and the guards. **`"pt-v3"` is the
current default**; most users never touch it. `"pt-v1"` and `"pt-v2"` remain
selectable and bit-reproducing forever.

```python
eng = pt.Engine(seed=42, universe=u, model="pt-v3")   # the default, spelled out
eng = pt.Engine(seed=42, universe=u, model="pt-v1")   # an earlier era, still exact
```

The reason is comparability. `(package version, model="pt-v3", universe
fingerprint, seed)` is a complete, minimal, citable specification of a
market. If every user ran a bespoke coefficient set, no two published
results would be comparable, and "tested on the pretium simulator" would
mean nothing.

## Which preset to use

Ten presets ship. Five are recommendations; the rest exist so that
results already published on them keep reproducing bit for bit, and a
new preset never moves an old one.

| preset | use it for | status |
|---|---|---|
| `pt-v3` | anything: the default, and the one the realism envelope certifies | recommended |
| `pt-v7` | studies whose thesis is a sector, or a crisis: the first preset with industries that survive a crisis, twelve of thirteen realism statistics in band at both horizons | recommended, opt in by name |
| `pt-v8` | crisis studies and anything that measures how correlation moves through time: the factor's variance has a memory, the crisis lever is 4.34x, thirteen of fourteen in band at 504 days | recommended, opt in by name |
| `pt-v9` | anything measuring volatility regimes, clustering or crises the market makes itself: thirteen of fourteen statistics in band at both horizons, and the first preset whose VIX responds to the day's move rather than to the closing minute | recommended, opt in by name |
| `pt-v10` | the most realistic market this project measures: all fourteen statistics in band at 252 days, on training seeds and a held-out universe | recommended, opt in by name |
| `pt-v1`, `pt-v2`, `pt-v4`, `pt-v5`, `pt-v6` | reproducing a run that names them | reproduction only |

`pt-v7` is not the default because the envelope certifies one preset by
policy, and re-certifying moves every published number at once. On a
single real name replayed through a real crisis (the pandemic notebook in
`examples/`) `pt-v3` and `pt-v6` fit the path a touch better than `pt-v7`;
`pt-v7` earns its keep in the cross-section, which a one-name path does not
measure. The per-preset record of what moved and what it measured is in the
[changelog](https://github.com/simoncoombes/pretium/blob/main/CHANGELOG.md)
and the calibration record it cites.

## Changing the model

The escape hatch exists and is deliberately ceremonial:

```python
custom = pt.ModelParams.from_preset("pt-v1", garch_alpha=0.12)
eng = pt.Engine(seed=42, universe=u, model=custom)
eng.model_fingerprint        # "custom-0c04c4ba", never "pt-v1"
```

`ModelParams` is immutable once built. The fingerprint is the first 8 hex
characters of a sha256 over the canonical serialisation (names sorted,
values as raw IEEE-754 bit patterns), and a coefficient set bit-identical
to a shipped preset fingerprints as that preset's *name*. Those two rules
together are the honesty mechanism: there is no way to construct a modified
model that presents as the benchmark one, and no way to mutate one after
its fingerprint has been quoted. The rule is not "you may not change the
model"; it is "a changed model has a different name."

The fingerprint travels everywhere a result does:

- `Scorecard.model_fingerprint`, beside the seed, the universe fingerprint
  and the strategy fingerprint, via `pt.evaluate(..., model=custom)`.
- `RunManifest` embeds the **full coefficient dictionary** of the model the
  run actually ran, and `reproduce()` replays under it, so a custom-model
  manifest rebuilds the custom market bit for bit or refuses by name.
- `Checkpoint` and `pt.branch` resume and fork under the parent's model.
- `pt.replay(log, ..., model=custom)` for driving it by hand.

## What is settable, and what is not

`pt.ModelParams.settable()` lists the runtime-settable surface: the two
variance processes (per-name GJR-GARCH and the market factor's), the factor
sigmas and the idiosyncratic scale, the mispricing dynamics, the news and
flow coefficients, and the guards that live in the tick chain (the
mispricing cap, the crowd lean cap, the session breaker, the price cap).
Guards are settable but are worst-case guarantees rather than tuning knobs,
and a calibration search excludes them.

Two coefficients are *derived*: `mispricing_phi` and `s_phi_tick` are
carried as recorded bit patterns and cannot be set directly. Overriding
`mispricing_half_life_days` recomputes both, deterministically on a given
build but not bit-identically to any recorded constant.

The rest of the preset surface, meaning the fair-value coefficients, the
macro chain's constants, the book geometry and the sector sigma table, is *visible* in
`ModelParams.to_dict()` and covered by the fingerprint, but overriding it
is refused by name: those constants are compile-time in this build, and
accepting an override the engine would ignore would make the fingerprint a
lie.

One rule governs membership: **nothing settable may change how many draws
are taken or in what order.** Market hours, the 390-tick day, the calendar,
the sector key order: those are the draw schedule, and a preset changes
what the draws are multiplied into, never the schedule. That is what keeps
any two presets comparable under common random numbers: run the same seed
under two models and `draws_consumed` is identical, so every difference in
the outcome is a parameter effect, not reshuffled noise.

## Sweeping parameters

A parameter sweep is ordinary `itertools` work. No rebuilds, and the
default model is untouched:

```python
for alpha in (0.02, 0.04, 0.08):
    params = pt.ModelParams.from_preset("pt-v1", garch_alpha=alpha)
    eng = pt.Engine(seed=1, universe=u, model=params)
    eng.run_days(252)
    print(params.fingerprint, summarise(eng.bars(grain="day")))
```

`tools/calibration/eval_model_params.py` in the repository is the worked
version: the realism panel at several vectors in parallel worker processes,
with the two invariants asserted on every run: distinct vectors carry
distinct fingerprints, and draw counts match across vectors per seed.

## Presets are consumed here, produced elsewhere

A calibrated preset arrives as a new named entry in the shipped table,
produced by the calibration tooling with its provenance committed; every
earlier preset stays selectable and bit-reproducing forever. That has now
happened twice. `"pt-v2"` and `"pt-v3"` were both produced this way, and
`"pt-v3"` is the current default. The library consumes presets; it does not
ship an optimiser.

What the shipped default is certified to reproduce, and where it is not, is
[the realism envelope](realism-envelope.md).

For the compact coefficient table the known-answer test hashes, see
`pt.model_preset()`, which returns the mispricing coefficient dictionary and
keeps its exact historical shape, because the cross-platform determinism
gate digests every value in it. The full surface lives on
`ModelParams.to_dict()`.
