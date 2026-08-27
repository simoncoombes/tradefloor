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
a column cannot carry `None`, absence is `NaN`, never zero - zero is a real
maker inventory, a real mispricing and a real return.

**Invalid input raises.** `ValidationError` for a malformed scenario,
`OrderError` for a rejected order. Nothing is silently clamped, because a
simulator that repairs your inputs gives you a market you did not specify.

**Negative EPS is legal.** Loss-makers are valued off book value, and a universe
without them is not realistic.

**Short interest is a share count, not a fraction.** The squeeze rule divides it
by the float, so `short_interest=0.03` means three hundredths of one share.
`Universe.random` generates a realistic spread - median about 3.7% of shares
outstanding, with roughly one name in eleven above the 20% squeeze threshold.

<!-- CHECK the 3.7%: the generator draws short interest log-uniform between
     0.4% and 30% of shares outstanding, and float equals shares outstanding
     for a random universe (rust/src/universe.rs), which puts the median of
     that draw below the 3.7% quoted here. The one-in-eleven figure matches
     the generator's own comment and the 20% threshold matches the squeeze
     rule. No measured replacement for the median was supplied, so the
     sentence is left as written rather than guessed at. -->

**Roster order is contractual.** A re-sorted universe is a different market.
`universe.fingerprint` covers order as well as content.

**Coefficients ship as a preset.** They are named and versioned rather than
passed as constructor keywords, so two published results can be compared.
`pt-v12` is the current default, as of the 2026-08-26 era boundary; `pt-v10`
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
runs the 4.0, which is why they replay unchanged. `model_preset()` shows
neither number. So quote the preset name AND `pt.version()` when you
publish - the version is what actually pins the build. See
[Reproducing a run](reproducing-a-run.html).
