# Changelog

## 0.1.0

First release.

pretium simulates an equity market you can run a strategy against. Give it a
seed and a roster of companies and it runs prices, a limit order book, fills
and a macro economy forward. Orders match against real depth, so trading
moves the price.

### What's in it

The engine covers price formation, a limit order book with price-time
priority and partial fills, and an economy that advances daily under a
five-phase business cycle.

On top of that sit the things you need to get an answer out of it: agent
evaluation against reference baselines and an oracle, ranking across seeds
with paired sign tests, transaction cost analysis, parameter sweeps, replay,
and checkpointing with branching. There's a Gymnasium environment for RL
work, five Arrow tables for getting results into polars or pandas, and an
SEC EDGAR loader if you want real fundamentals instead of generated ones.

Two things are less usual. The simulator computed every price, so it can
tell you why one moved: seven factor contributions per instrument per tick
that add up to the move. And because the same seed reproduces the same
market, you can run it twice, once with your orders and once without, and
price every fill against the market where you never traded.

### Determinism

The same seed gives the same market on every platform we ship for. That's
checked on each release rather than asserted: five targets build a wheel,
run one fixed simulation inside it, and compare digests. A disagreement
fails the release.

Verified for this one on linux-x86_64, linux-aarch64, macos-arm64,
macos-x86_64 and windows-x86_64, all reproducing

```
5bd011be292f823ce1c360d1a12bf46de3362deee058a37283c74ab47069d0c1
```

A WebAssembly build (`--features wasm`) produces the same numbers, which
means the engine can run in a browser without becoming a second model that
quietly disagrees with this one.

### How realistic it is

Ten statistics are measured against real-market bands. At the certified
252-day horizon the shipped `pt-v3` preset holds nine of the ten in band,
and holds the same nine on seeds and a roster the calibration never saw.

The tenth is the autocorrelation of volume changes, which misses by 13.7
seed-standard-deviations. It's left out of the calibration objective on
purpose, because pointing an optimiser at a target it can't reach distorts
everything else it touches.

Six further gaps are written down with what each one stops you concluding,
and `envelope.check()` will refuse a question that falls outside them rather
than answering it anyway.

### Presets

`pt-v3` is the default and is what the envelope certifies.

`pt-v4` also ships, and is not the default. It halves the calibration
objective and is the first preset to bring 504-day kurtosis inside its band,
which no earlier one managed. It pays for that at one year, where it holds
eight of ten statistics in band instead of nine. Use `pt-v3` for horizons up
to a year and `pt-v4` for multi-year work.

`pt-v1` and `pt-v2` remain selectable and reproduce exactly what they always
did.

### Driving it from an agent

`pip install "pretium[mcp]"` adds an MCP server, so a coding agent can use
the simulator through eleven read-only tools. Strategies, universes and
scenarios are all composed as data, and there's no way to get from a tool
argument to running code. Results carry their caveats and provenance with
them, because a model summarising a result has the tool output and nothing
else to go on.

### A note on versioning

Anything that changes the simulated trajectory is a breaking change here,
however small it looks. A market that runs differently from the same seed
invalidates every published result that cited it. So coefficient changes
arrive as a new model preset rather than an edit to an existing one, and old
presets keep running exactly as they did.
