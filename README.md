# pretium

A deterministic market simulator with a real limit order book.

**Pre-release.** The module-level API below is implemented and tested. The
full engine — stepping a whole market through time — is not exposed yet.

## What it does

```python
import pretium as pt

fv = pt.fair_value(
    eps=4.10, sector="technology", revenue_growth=0.22,
    federal_funds_rate=0.025, corporate_bond_yield=0.052,
)

rng = pt.GameRng(seed=42, sequence=99)
s = pt.MispricingState(0.0)

for _ in range(250):
    s = pt.step_mispricing_daily(s, innovation=rng.next_normal() * 0.015)
    price = pt.apply_mispricing(fv.fair_value, s.s)
```

That is a complete daily price path, and at every step the simulator can tell
you the fair value it computed, the mispricing it applied, and the multiple it
came from. No historical dataset carries those labels.

## The two things it is for

**Determinism.** The same seed and the same inputs are designed to produce
bit-identical output on Linux, macOS and Windows, because the library ships
its own transcendental maths rather than calling the platform's libm. Every
release runs one fixed simulation on every wheel target and compares digests;
no wheel ships that disagrees with the others.

To be precise about the current state of that claim: it is enforced by a
source-level ban on `std` transcendentals and by the release gate described
above. It is a property the design guarantees and the pipeline checks, not a
folk claim about floating point.

**Emergent market impact.** Orders match against a real book with price-time
priority, so a large order pays worse prices because it *consumed levels* —
not because a slippage coefficient said large orders cost more.

```python
book = pt.OrderBook("ACME")
for i in range(10):
    book.post_limit("sell", 100.0 + i, 100, owner="mm")

small = book.submit("buy", 50)    # average 100.00 — inside the best level
large = book.submit("buy", 550)   # worse, having reached six levels deep
```

## Conventions worth knowing before you start

**Rates are fractional.** `0.052` means 5.2%. Passing `5.2` raises, with an
error that says so — the mistake is common enough to be worth naming rather
than silently simulating a 520% policy rate.

**Absence is not zero.** `corporate_bond_yield=None` falls through to the
policy rate; `corporate_bond_yield=0.0` is a real observation and is used as
given. Several arguments make that distinction and it is never guessed at.

**Invalid input raises.** `ValidationError` for a malformed scenario,
`OrderError` for a rejected order. Nothing is silently clamped or repaired: a
simulator that helpfully fixes your inputs produces a market nobody specified.

**Negative EPS is legal.** Loss-makers are valued off book value. A universe
without them is not a realistic universe.

## Model coefficients

Coefficients ship as a named, versioned preset (`pt-v1`) rather than as
constructor keywords, so two published results can be compared. `model_preset()`
returns it.

## Install

```
pip install pretium
```

Wheels are abi3 and cover CPython 3.11 and later.

## Licence

Apache-2.0. See `LICENSE` and `NOTICE`.
