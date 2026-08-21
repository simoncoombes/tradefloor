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

**Roster order is contractual.** A re-sorted universe is a different market.
`universe.fingerprint` covers order as well as content.

**Coefficients ship as a preset.** `pt-v1`, named and versioned, rather than as
constructor keywords, so two published results can be compared.
`model_preset()` returns it, and the returned values are folded into the
known-answer digest and parity-tested against the reference implementation.

What the dictionary carries is the mispricing and crowd model: the half-life,
`phi`, `momentum_theta`, the mispricing and daily-shock caps, and the three
crowd terms. Eight numbers, and every one of them live.

**It is not a complete listing of the model's coefficients.** The GARCH
parameters, the market and sector factor sigmas, and the order-flow
coefficient are all live and none of them appear. Nor is there anything that
forces the name `pt-v1` to change if one of the absent constants moves. So
quote the preset name AND `pt.version()` when you publish - the version is
what actually pins the build. See
[Reproducing a run](reproducing-a-run.html).
