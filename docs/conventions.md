---
title: Conventions
nav_order: 16
rack: reference
---

# Conventions

**Rates are fractional.** `0.052` means 5.2%. Passing `5.2` raises, with an
error that says so.

**Absence differs from zero.** `corporate_bond_yield=None` falls through to the
policy rate; `0.0` is a real observation used as given. In columnar reads, where
a column cannot carry `None`, absence is `NaN`, never zero -- zero is a real
maker inventory, a real mispricing and a real return.

**Invalid input raises.** `ValidationError` for a malformed scenario,
`OrderError` for a rejected order. Nothing is silently clamped, because a
simulator that repairs your inputs gives you a market you did not specify.

**Negative EPS is legal.** Loss-makers are valued off book value, and a universe
without them is not realistic.

**Short interest is a share count, not a fraction.** The squeeze rule divides it
by the float, so 3% of a hundred million shares is `short_interest=3_000_000`.
Passing `0.03` does not quietly mean three hundredths of a share: values
strictly between 0 and 1 raise `ValidationError` for a company with a real
share count, because three hundredths of one share against that float is a
squeeze ratio of 3e-10 and a squeeze that can never fire, and that mistake
would otherwise be silent.
`Universe.random` generates a realistic spread: the draw is log-uniform between
0.4% and 30% of shares outstanding, so the median is the geometric mean of
those bounds, 3.46%, and about one name in eleven clears the 20% squeeze
threshold. Measured across the whole three-letter ticker space, 17,576 names
at seed 7: median 3.45%, 9.54% of names above 0.20. A roster the size these
docs use runs lower and noisier -- 2.53% on `Universe.random(108, seed=7)` --
because a hundred names is a small sample of a draw whose top and bottom
differ by a factor of seventy-five.

**Roster order is contractual.** A re-sorted universe is a different market.
`universe.fingerprint` covers order as well as content.

**Coefficients ship as a preset.** They are named and versioned rather than
passed as constructor keywords, so two published results can be compared.
`pt-v14` is the current default, as of the 2026-08-28 era boundary; `pt-v12`
was the default before it, and every earlier name from `pt-v1` on is still
selectable and still reproduces bit for bit.
`model_preset()` returns the set in force, and the returned values are folded
into the known-answer digest and parity-tested against the reference
implementation.

What the dictionary carries is the mispricing and crowd model: the half-life,
`mispricing_phi`, `momentum_theta`, the mispricing and daily-shock caps, and
the three crowd terms. Eight numbers, and every one of them live.

**It is not a complete listing of the model's coefficients.** The GARCH
parameters, the market and sector factor sigmas, and the order-flow
coefficient are all live and none of them appear. Nor is there anything that
forces the name `pt-v1` to change if one of the absent constants moves. How
much can hide in one of them is not hypothetical: `volume_move_cap` was a
literal `4.0` in `tick.rs` through `pt-v11`, saturating a name's volume
response at a 4% daily move, so every crisis day traded like a bad Tuesday.
Raising it to 12.0 is the whole of `pt-v12`, and every earlier preset still
runs the 4.0, which is why they replay unchanged. It is a real parameter now,
one of the 87 `ModelParams.settable()` lists, and readable as
`ModelParams.from_preset("pt-v12").volume_move_cap`. `model_preset()` still
shows neither number. So quote the preset name AND `tf.version()` when you
publish -- the version is what actually pins the build. See
[Reproducing a run](reproducing-a-run.md).
