# pretium

A deterministic market simulator with a real limit order book.

**This is a placeholder release.** It reserves the name and contains no
functional code. Do not depend on it yet.

## What this will be

A market simulator that takes a seed and a starting state and runs a market
forward: prices, an order book with depth, fills, and macro state. The
intended guarantees are:

- **Deterministic across platforms.** The same seed and the same inputs
  produce bit-identical output on Linux, macOS and Windows, because the
  library ships its own transcendental maths rather than relying on the
  platform's libm.
- **Emergent market impact.** Orders match against a real book with
  price-time priority, so a large order pays worse prices because it consumed
  levels, rather than because a slippage coefficient said so.
- **Ground truth.** The simulator knows the fair value it computed and the
  regime it is in, so both are readable. No real market dataset has labels.

Rust core, with Python bindings planned.

## Licence

Apache-2.0
