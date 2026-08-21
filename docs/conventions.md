---
title: Conventions
nav_order: 15
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
`model_preset()` returns it.
