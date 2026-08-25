---
title: RNG streams
nav_order: 18
rack: reference
---

# RNG streams

One seed drives the whole simulation, but not through one generator. The
engine derives **three independent substreams** from the root seed — market,
economy, external — so that changing what one consumer draws cannot shift
any other consumer's sequence. This page is the derivation contract: what a
reader needs to reproduce every stream from the seed, and the argument for
why the streams are independent.

## Why streams exist

With a single shared stream, every draw is coupled to every other. Ask "what
would have happened if I changed only this?" and the honest answer was
"nothing comparable": perturbing one consumer's draw count shifted the
sequence for everyone after it. Three consumers hit exactly this wall:

- **Transaction cost analysis** re-runs the market without the trader's
  orders and prices every fill against it. The subtraction is only exact if
  the untraded world sees the identical noise sequence.
- **Pinned-versus-baseline macro comparison** replays a macro path (a
  historical series, a scenario) against an endogenous baseline. The macro
  chain's draw count genuinely varies with macro state, so under a shared
  stream a pinned run's market noise had nothing to do with its baseline's.
- **An embedder** (the game this library was extracted from, or any host
  application) needs randomness of its own. Under a shared stream, adding
  one event roll on the host side invalidated every seeded market
  trajectory.

The split landed inside the 2026-08 era boundary, regenerating the
known-answer baseline — `KAT_VERSION = 5` at the split; the era's later
model changes have since taken it to 8: every trajectory changed, once, and
these three questions became answerable.

## The three streams

| stream | id | serves |
|---|---|---|
| `market` | 0 | every draw in the tick: the market factor, sector factors, per-company noise, volume, book settlement |
| `economy` | 1 | the daily macro chain: economy update, cycle transition, central bank |
| `external` | 2 | the embedder, through `Engine.draw_uniform()` / `draw_normal()` |

The **market stream's schedule is a pure function of (market status, active
roster, sector count)**. Settlement historically drew four uniforms *or
zero*, decided by guards that read the trajectory; since the split the four
are drawn unconditionally per active company per open tick, whether or not
the settle uses them. No price, no macro value and no order flow can move
the market stream's position. The economy stream's count still varies with
macro state — a chain in contraction draws a shock the expansion never
rolls — which is precisely why it has its own stream.

`Engine.draws_by_stream()` reports the per-stream counts:
`{"market": n, "economy": n, "external": n}`. Equal `market` counts between
two runs of the same tick schedule mean the two markets consumed — and
therefore saw — an identical noise sequence.

## The derivation contract

For root seed `s` (a 32-bit unsigned integer) and stream id `k` from the
table above:

```text
mixed      = splitmix64_mix((s << 32) | k)      # on 64 bits
seed_k     = mixed >> 32                        # top 32 bits
sequence_k = 256 + k
stream_k   = GameRng(seed_k, sequence_k)        # PCG-XSH-RR 64/32
```

`splitmix64_mix` is the SplitMix64 output finalizer (Stafford's Mix13, as
published in Vigna's reference `splitmix64.c`), all on 64-bit unsigned
integers with wrapping arithmetic:

```text
z  = input + 0x9E3779B97F4A7C15
z ^= z >> 30;  z *= 0xBF58476D1CE4E5B9
z ^= z >> 27;  z *= 0x94D049BB133111EB
z ^= z >> 31
```

This is a **contract, not an implementation detail**. It is pinned by a
golden test against hand-computed values
(`rng.rs::substream_derivation_is_the_documented_formula`), and changing it
is an era boundary in its own right: every seeded trajectory would change.

Everything above is integer arithmetic, exactly specified on every
platform — no floats, no platform-dependent hashing — so the derivation
adds nothing for the cross-OS bit-identity claim to trip on.

## Why the streams are independent

Two properties carry the argument, and each half of the derivation supplies
one:

1. **Distinct sequences, structurally.** Each stream gets its own PCG
   `sequence`, hence its own odd LCG increment. Two PCG32 generators with
   different increments traverse *different orbits*: one can never be a
   time-shifted copy of the other. This holds by construction, not
   probabilistically.

2. **Decorrelated states, by mixing.** The lazy derivation —
   `GameRng(s, k)` with the root seed used raw — is refused deliberately.
   Two PCG streams seeded with the same state and different increments
   `c, c′` have states related by the affine identity
   `state′ₙ − stateₙ = (c′ − c)(aⁿ⁻¹ + ⋯ + 1)`: a deterministic
   cross-stream structure that the output permutation only obscures.
   Passing `(s, k)` through an avalanche finalizer first gives every
   stream an unrelated starting state — a one-bit change in either input
   flips each output bit with probability ≈ ½ — so no such relation exists
   between any two substreams.

The sequence base 256 keeps derived sequences visibly clear of every
sequence historically used with raw seeds (0 and 1 from the two `GameRng`
constructors, 21 for the universe, 99 for the reference MAIN stream), so a
recorded sequence number identifies its era at a glance.

## What is deliberately not derived this way

- **Universe generation** keeps its original `GameRng(seed, 21)`. Its seed
  is a *universe* seed — a different input domain from the simulation
  seed — so it shares no root with the engine streams, and re-deriving it
  would churn every published universe fingerprint for no independence
  gain.
- **`GameRng(seed, sequence)`** stays public and raw: it is the Layer-1
  API, and the replay surface for pre-split recordings. An engine replaying
  a recorded reference stream (`tick_with`, the golden-parity harnesses)
  reconstructs `GameRng(seed, 99)` and consumes it in the reference's
  shared-stream order, settlement's four-or-zero included.

## What lands in a checkpoint

`state_snapshot()["rng"]` is nine numbers: `(state, increment, spare)` for
the market, economy and external streams, in that order. The u64 halves
ride as f64 bit patterns so they round-trip exactly. Each stream carries
its own Box-Muller spare — the parity of normal draws is per-stream state
and never crosses domains. A pre-split snapshot (three numbers) is refused
on restore with its era named: it froze a single-stream market that this
version cannot continue bit-exactly.
