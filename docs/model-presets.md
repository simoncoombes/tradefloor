---
title: Model presets
nav_order: 8.5
rack: experiment
---

# Model presets

The model's coefficients ship as a named, versioned preset. A preset names
the complete set, frozen and documented: the variance processes, the factor
structure, the mispricing dynamics and the guards. **`"pt-v12"` is the
current default**; most users never touch it. Every earlier preset, `"pt-v1"`
through `"pt-v11"`, remains selectable and bit-reproducing forever.

```python
eng = pt.Engine(seed=42, universe=u, model="pt-v12")  # the default, spelled out
eng = pt.Engine(seed=42, universe=u, model="pt-v10")  # the previous default, still exact
eng = pt.Engine(seed=42, universe=u, model="pt-v1")   # an earlier era, still exact
```

The reason is comparability. `(package version, model="pt-v12", universe
fingerprint, seed)` is a complete, minimal, citable specification of a
market. If every user ran a bespoke coefficient set, no two published
results would be comparable, and "tested on the pretium simulator" would
mean nothing.

## Which preset to use

Twelve presets ship. Two are recommendations; the rest exist so that
results already published on them keep reproducing bit for bit, and a
new preset never moves an old one.

| preset | use it for | status |
|---|---|---|
| `pt-v12` | anything: the default, the one the realism envelope certifies, with all fourteen statistics in band at 252 days *and* all fourteen again at 504, on thirty training seeds, on a held-out sixty-name universe, and thirteen of fourteen on held-out seeds | recommended |
| `pt-v10` | reproducing work published before the 2026-08-26 era boundary, when it was the default: all fourteen statistics in band at 252 days, on thirty training seeds and on a held-out universe | reproduction only |
| `pt-v11` | reproducing a run that names it: `pt-v10` plus the crisis work (`crisis_blend_gain`, `sector_vix_coupling`, endogenous news and peer transfer) that `pt-v12` inherits unchanged | reproduction only |
| `pt-v3` | reproducing work published when it was the default, the era before `pt-v10` | reproduction only |
| `pt-v7` | reproducing a sector or crisis study that names it: the first preset with industries that survive a crisis, twelve of thirteen realism statistics in band at both horizons. It is no longer the preset to opt into for that thesis: at a held VIX 45 `pt-v12` reads a crisis sector excess of +0.109 against a real +0.103 | reproduction only |
| `pt-v8` | anything that measures how correlation moves through time: the factor's variance has a memory, thirteen of fourteen in band at 504 days. Its crisis lever of 4.34x is no longer a reason to prefer it, because `pt-v12`'s steady-state crisis lever is 6.04 against a real 6.16 | recommended, opt in by name |
| `pt-v9` | reproducing a run that names it: thirteen of fourteen statistics in band at both horizons, and the first preset whose VIX responds to the day's move rather than to the closing minute, which `pt-v12` inherits while holding fourteen of fourteen at both horizons | reproduction only |
| `pt-v1`, `pt-v2`, `pt-v4`, `pt-v5`, `pt-v6` | reproducing a run that names them | reproduction only |

`pt-v7`, `pt-v8` and `pt-v9` are steps on the way from `pt-v3` to `pt-v12`,
and all three stay selectable because a run that named one must keep
reproducing. Only `pt-v8` is still worth opting into, when the thesis is how
correlation moves through time. `pt-v7` and `pt-v9` were recommendations
while the default was weaker than they were on a sector thesis, a crisis or
a volatility regime, and `pt-v12` is not weaker: it holds all fourteen
statistics in band at both horizons, its crisis sector excess at a held VIX
45 reads +0.109 against a real +0.103, its steady-state crisis lever 6.04
against a real 6.16, and its crisis co-movement 0.696 against a real 0.664
to 0.727. The narrower question each was better at is one the default now
answers at least as well.

<!-- STATUS CHECK (pt-v12 boundary): `pt-v7` and `pt-v9` were moved from
     "recommended, opt in by name" to "reproduction only" because the
     pt-v12 measurements cited here dominate the axes they were
     recommended for. No supplied list of recommended presets states that
     directly, so the demotion is an inference from the measurements. -->

`pt-v12` is `pt-v11` plus one number. `volume_move_cap` was a hard-coded
literal 4.0 in `tick.rs`, which saturated a name's volume response at a
4 percent daily move: past that the tape stopped reacting, so every crisis
day traded like a bad Tuesday. Raising it to 12.0 is what brings
`volume_change_acf1` inside its band at both horizons, -0.2656 against
(-0.32, -0.20) at 252 days and -0.2572 against (-0.29, -0.21) at 504, and
retires the volume-change gap [the realism envelope](realism-envelope.md)
used to carry. `pt-v11` in turn is `pt-v10` plus the crisis work, so the
default carries both steps. The certified horizon is still 252 days: the
504-day panel is measured, not certified, and the envelope says why.

<!-- ERA CHECK (pt-v12 boundary): the paragraph below, and the pt-v3 row
     above, used to date pt-v3's handover to the 2026-08-26 era boundary.
     That boundary now names the pt-v10 -> pt-v12 move. No date for the
     pt-v3 -> pt-v10 move was supplied, so neither place states one. -->

The default moved from `pt-v10` to `pt-v12` on 2026-08-26. Re-certifying
moves every published number at once, which is why it happens rarely and
why the old default stays selectable by name. The per-preset record of
what moved and what it measured is in the
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
flow coefficients, the volume expression's coefficients (`volume_move_cap`
among them, the one `pt-v12` moved off its compiled literal), and the guards
that live in the tick chain (the mispricing cap, the crowd lean cap, the
session breaker, the price cap).
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
happened several times. `"pt-v2"`, `"pt-v3"` and everything through
`"pt-v12"` were produced this way, and `"pt-v12"` is the current default.
The library consumes presets; it does not ship an optimiser.

What the shipped default is certified to reproduce, and where it is not, is
[the realism envelope](realism-envelope.md).

For the compact coefficient table the known-answer test hashes, see
`pt.model_preset()`, which returns the mispricing coefficient dictionary and
keeps its exact historical shape, because the cross-platform determinism
gate digests every value in it. The full surface lives on
`ModelParams.to_dict()`.
