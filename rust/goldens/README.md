# Golden vectors

These files record what `src/lib/engine/prng.ts` and `src/lib/engine/mispricing.ts`
**do**, value by value, at full `f64` precision. They are the reference the Rust
port must reproduce.

**995,783 assertions across 20 files, 37.4 MB.**

---

## The one rule

> **These are generated from the TypeScript. They must never be regenerated
> from the Rust.**

The TypeScript is the only implementation that exists today, which makes it the
only independent oracle. The moment the Rust regenerates these files, the Rust
becomes its own oracle and any divergence is silently legitimised. The parity
test then proves nothing except that Rust agrees with Rust.

If the Rust disagrees with a vector, exactly two responses are legitimate:

1. Fix the Rust.
2. Decide the divergence is acceptable, record *why* in `docs/rust-port/PLAN.md`,
   and bump `daily_runs.formula_version`, because the daily challenge is the
   one surface where "same seed, same market" is a promise to users.

Regenerating the vector is not on the list. The only reason to re-run the
generator is that the **TypeScript itself** changed, and that is a formula-era
change whether or not anyone intended it to be.

Every file records the SHA-256 of both source files it was generated from, so a
stale vector set is detectable rather than merely suspected.

---

## Regenerating and verifying

```bash
npx tsx scripts/rust-port/generate-goldens.ts            # write the vectors
npx tsx scripts/rust-port/generate-goldens.ts --check    # verify them
```

`--check` re-reads every file from disk, decodes the recorded **inputs** from
their IEEE-754 bit patterns, re-runs the TypeScript, and compares every recorded
**output** bit for bit. It also verifies each file against the SHA-256 and byte
count in `index.json`. It exits non-zero on any mismatch.

Generation is deterministic: no `Date.now()`, no `Math.random()`, ASCII output
with LF endings. Two runs produce byte-identical files, which has been verified
by comparing SHA-256 sums across consecutive runs. The only field that legitimately
changes between runs is `meta.gitCommit`, and only when `HEAD` has moved.

Generation takes about 50 s; `--check` about 30 s. Most of that is the 60 million
PRNG draws behind `prng-checkpoints.json`.

---

## File format

Every file is a single JSON object:

```jsonc
{
  "kind": "mispricing.step",   // dispatch tag: one per generator/checker pair
  "meta": { ... },             // provenance, coverage, and porting notes
  ...                          // payload, shape depends on `kind`
}
```

`meta` carries the generator path, the git commit, the SHA-256 of both source
files, which functions the file covers, and a `notes` array. **Read the notes.**
They are where the JS-specific behaviour that a port will otherwise get wrong is
written down.

### Floats

Every `f64` is recorded as its **raw IEEE-754 bit pattern**: big-endian, 16
uppercase hex digits, no `0x` prefix. That is the contract. Nothing anywhere in
these files is rounded, truncated or `toFixed`-ed.

Two encodings appear:

| Form | Where | Example |
|---|---|---|
| `{ "dec": …, "bits": … }` | individual cases | `{ "dec": "0.9885140203528962", "bits": "3FEFA1E827A1B38C" }` |
| bare bits string | bulk arrays and row tables | `"3FEFA1E827A1B38C"` |

`dec` is the shortest round-trip decimal, **for humans only**. It is exact
(JS
`Number.prototype.toString()` round-trips `f64`) but `bits` is what a checker
should compare, because it also distinguishes `+0` from `-0` and needs no
correctly-rounded decimal parser. `dec` spells out `"NaN"`, `"Infinity"`,
`"-Infinity"` and `"-0"`, which JSON numbers cannot express.

Floats are **never** written as JSON numbers. JSON numbers appear only where the
value is an exact integer: u32 PRNG outputs, `nextInt` results, seeds, indices,
counts and byte sizes.

### NaN

Every NaN is recorded as `7FF8000000000000`, which is the bit pattern of Rust's
`f64::NAN`.

This is a deliberate normalisation, and it is there because the first run of the
verifier caught V8 emitting *both* `7FF8000000000000` and `FFF8000000000000` for
the same expression depending on whether the result had passed through the heap.
**The sign and payload of a NaN are not part of the parity contract; NaN-ness
is.** Compare NaN results with `is_nan()`, not with bits.

### Bulk arrays

Long float arrays are arrays of bare bits strings, packed 8 per line. When every
element of such an array is identical, it collapses to:

```jsonc
"shocks": { "constantBits": "0000000000000000", "length": 100000 }
```

A reader must handle both forms. `mispricing-step-cases.json` uses a third form,
a **row table**: `columns` names the six positions and `rows` is an array of
six-element bits arrays, purely because one object per case would have put that
file at 22 MB.

Where a file uses a compact form it also carries a `decimalPreview` repeating the
first few dozen entries in readable object form. Previews are redundant with the
bulk data by construction; they are not counted in the assertion totals, which is
why `--check` verifies slightly more values than `index.json` declares.

### `index.json`

Manifest: every file with its `kind`, byte size, SHA-256 and assertion count,
plus `totalAssertions`. Use it to detect a partially-regenerated or truncated
vector set.

---

## What is covered

| File | `kind` | Assertions | Size |
|---|---|---:|---:|
| `prng-raw-u32.json` | `prng.rawU32` | 138,432 | 1.69 MB |
| `prng-floats.json` | `prng.floats` | 60,000 | 1.23 MB |
| `prng-normals.json` | `prng.normals` | 60,000 | 1.23 MB |
| `prng-nextint.json` | `prng.nextInt` | 32,256 | 0.66 MB |
| `prng-nextbool.json` | `prng.nextBool` | 33,822 | 0.14 MB |
| `prng-mixed-sequence.json` | `prng.mixed` | 32,008 | 1.01 MB |
| `prng-checkpoints.json` | `prng.checkpoints` | 48 | 0.01 MB |
| `prng-derived.json` | `prng.derived` | 7,032 | 0.16 MB |
| `mispricing-constants.json` | `mispricing.constants` | 13 | 0.00 MB |
| `mispricing-create-state.json` | `mispricing.createState` | 52 | 0.01 MB |
| `mispricing-step-cases.json` | `mispricing.step` | 116,952 | 7.05 MB |
| `mispricing-apply.json` | `mispricing.apply` | 375 | 0.09 MB |
| `mispricing-crowd-lean.json` | `mispricing.crowdLean` | 441 | 0.10 MB |
| `mispricing-roots.json` | `mispricing.roots` | 589 | 0.08 MB |
| `mispricing-impulse.json` | `mispricing.impulse` | 13,653 | 0.29 MB |
| `mispricing-trajectory-calm.json` | `mispricing.trajectory` | 100,022 | 3.95 MB |
| `mispricing-trajectory-garch-clustered.json` | `mispricing.trajectory` | 100,022 | 3.95 MB |
| `mispricing-trajectory-news-shocks.json` | `mispricing.trajectory` | 100,022 | 5.90 MB |
| `mispricing-trajectory-extreme-clamped.json` | `mispricing.trajectory` | 100,022 | 5.90 MB |
| `mispricing-trajectory-denormal-drift.json` | `mispricing.trajectory` | 100,022 | 3.94 MB |

Seeds used throughout: `0`, `1`, `42`, `2^31`, `0xDEADBEEF`, `2^32-1`.
Sequences: `0`, `1`, `2`, `3`, `4`, `2^31-1`, `2^31`, `2^32-1`.

### PRNG

- **`prng-raw-u32`**, first 10,000 raw 32-bit PCG-XSH-RR outputs for all six
  seeds at sequences 0 and 1, plus 512 each across six further sequences.
  `PCG32` is not exported, so these are recovered as `nextFloat() × 2^32`, which
  is exact because `nextFloat()` divides by a power of two.
- **`prng-floats`**, **`prng-normals`**, first 10,000 outputs of `nextFloat()`
  and `nextNormal()` per seed.
- **`prng-nextint`**, 18 ranges × 3 seeds × 512 draws, including non-power-of-two
  ranges where the modulo bias is real and must be reproduced, plus three
  `min > max` cases in a separate `pathological` block.
- **`prng-nextbool`**, 10 probabilities including 0, 1, subnormal and
  out-of-range, plus the zero-argument default.
- **`prng-mixed-sequence`**, four scripted 2,000-op interleavings of all four
  methods, run against four seeds, plus an explicit `spareProbe` walking the
  Box-Muller spare cache. This is the vector that catches a port whose methods
  are each correct but consume the underlying stream in the wrong order.
- **`prng-checkpoints`**, raw output at draw indices up to 10,000,000. The
  PCG32 state is private and cannot be inspected, so a deep output is the proxy:
  it is a function of the entire state, so agreement here pins the state.
- **`prng-derived`**, `createGameRng`, `createRngStreams` (including the four
  derived child seeds) and `forkRng` at three parent warm-up depths.

### Mispricing

- **`mispricing-constants`**, all eight exported constants, plus the four
  intermediate values `MISPRICING_PHI` is built from.
- **`mispricing-step-cases`**, 58,080 single steps: 22 finite boundary-adjacent
  `s` values × 22 `sPrev` × 10 innovations × 12 shocks, covering each cap **at**,
  **one ULP inside** and **one ULP beyond**, plus subnormals and `-0`. The ULP
  neighbours are computed by bit manipulation rather than typed as decimals,
  because `0.14999999999999999` rounds straight back onto `0.15` and would have
  tested nothing, the first draft did exactly that. A separate `nonFinite`
  block records the 396 NaN/Infinity combinations.
- **`mispricing-trajectory-*`**, five scenarios × 100,000 sequential steps.
  Inputs are recorded explicitly rather than regenerated, so a PRNG bug cannot
  masquerade as a mispricing bug. Every step's `s` is recorded so a harness can
  report the **index** at which divergence starts, not merely that it did:
  - `calm`, 1.5% innovations, no shocks. sd(s) = 11.1%, which is the ≈10% the
    module docstring predicts.
  - `garch-clustered`, GARCH(1,1)-scaled innovations, volatility clustering.
  - `news-shocks`, sparse shocks drawn wider than `DAILY_SHOCK_CAP`; the shock
    clamp fires 1,993 times, the `s` clamp 5 times.
  - `extreme-clamped`, 54,105 of 100,000 steps land exactly on
    `±MISPRICING_CAP`. The clamp is the hot path, not an edge case.
  - `denormal-drift`, starts at `5e-324` with innovations near the denormal
    floor. 49,018 distinct values out of 100,000 steps, because gradual
    underflow keeps collapsing them, and one exact zero.
- **`mispricing-apply`**, **`mispricing-crowd-lean`**, **`mispricing-roots`**,
  **`mispricing-impulse`**, full cross products over boundary inputs
  (15 fair values × 25 `s` values; 21 × 21 crowd-lean inputs; 15 φ × 13 θ).
  `mispricing-roots` covers both branches of the discriminant test (125 real,
  70 complex). `mispricing-impulse` includes a 5,000-step unit-root case.

---

## Notes for the port

The full list lives in each file's `meta.notes`. These are the ones that will
cost a day if missed.

1. **`MISPRICING_PHI = Math.pow(0.5, 1/60)` is a transcendental result computed
   by V8 at module load.** Rust `f64::powf` may differ in the last ULP, and a
   one-ULP φ compounds through the entire `s`-process. Hardcode the literal from
   `mispricing-constants.json` → `constants.MISPRICING_PHI.bits`
   (`3FEFA1E827A1B38C` = `0.9885140203528962`).

2. **`clamp` is `x < lo ? lo : x > hi ? hi : x`.** NaN fails both comparisons and
   **passes through unchanged**. Rust's `f64::clamp` panics on NaN and
   `min`/`max` would return the bound. Neither reproduces this. Use the ternary.
   `-0` also passes through as `-0`.

3. **`Math.max(0.01, NaN)` is `NaN`; Rust's `f64::max(0.01, NaN)` is `0.01`.**
   `applyMispricing` is the only place this matters.

4. **Order of operations in `stepMispricing` is contractual.**
   `((φ·s + momentum) + innovation) + shock`, strictly left to right, with
   `momentum` computed first from the *unclamped* `s` and `sPrev`. Do not
   reassociate and **do not use `mul_add`**, a fused multiply-add changes the
   last bit.

5. **`Math.exp` in `applyMispricing` and `Math.log`/`Math.sin`/`Math.cos` in
   `nextNormal` are the only libm calls in these two modules.** They are where
   V8 and Rust can differ. `exp`'s argument is bounded to `[-0.9, 0.9]`, which
   makes a shared correctly-rounded `exp` over that narrow range a realistic
   Phase 1 fix. `Math.sqrt` is IEEE-exact in both and is not a risk.

6. **`nextNormal`'s spare is instance state that nothing clears.** An odd number
   of calls leaves the instance holding a value that a later call returns
   *without touching the PCG stream*. `prng-mixed-sequence.json` pins this.

7. **`nextInt` modulo bias is intentional.** `min + (next() % range)` is
   non-uniform for ranges that are not powers of two. Reproduce the bias; do not
   fix it. Note also that `nextInt(0, -1)` gives `NaN` in JS while Rust would
   panic on `% 0`, the game never calls it that way, but a port should decide
   deliberately rather than by accident.

8. **`createGameRng(seed)` is not `new GameRng(seed)`.** The factory passes
   sequence 0; the constructor defaults to sequence 1. They are different streams.

9. **`forkRng` advances the parent by exactly one draw**, which is observable.
   `prng-derived.json` records the parent's next value after each fork.

10. **`sequence` and `seed` are coerced with `ToUint32` before use.** The
    vectors cover `2^31`, `2^31-1` and `2^32-1` for both, because that is where a
    naive `as u32` cast in Rust and JS's `>>> 0` could plausibly disagree.
