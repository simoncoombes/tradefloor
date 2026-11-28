# Roadmap

Every item on this page is planned work. The page gives no dates and makes
no commitment, and an item may change or be dropped.
[CHANGELOG.md](CHANGELOG.md) records what has shipped.

## Tradefloor Bench

Tradefloor Bench is a benchmark for financial AI agents built on this engine.
It is in preparation, and so far only baseline strategies have been run on
it. A pilot on a small set of models is planned first. A public version 1
with a frozen spec is planned after the pilot.

## Browser playground

A page that runs a small market in the browser, with no install, is planned.
The engine has a WebAssembly build (`rust/src/wasm.rs`) that runs days, reads
prices and quotes, and forks a market. The playground needs orders and
scenarios added to that build.

## Research notes

Short write-ups of experiments run on the engine are planned. Each one will
carry the version, preset, seeds and code that reproduce its figures.

## Suggestions

A feature request issue is the place to propose an item for this page.
[CONTRIBUTING.md](CONTRIBUTING.md) describes how a change to a benchmark or to
the model is handled.
