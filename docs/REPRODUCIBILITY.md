# Reproducibility

What replays exactly, and where the promise stops: across platforms, across
releases, and when a saved state is restored.

## Seeds and platforms

The same seed gives the same market on every platform. Each release builds
for five platforms, runs one fixed simulation on each, and stops if any result
differs. tradefloor ships its own `exp`, `log`, `pow`, `sin` and `cos`, so
the system's math library cannot change a result.

`pt-v20` became the default in 0.8.5, replacing `pt-v19`. Every preset from
`pt-v1` on can still be selected, and a market with no agent orders in it
replays exactly on its named preset in every later release. Each release
checks that with a digest per shipped preset.

A run with agent orders in it replays exactly on the same release. Across
releases the promise is narrower. 0.8.5 changed how an agent's fills reach
the market, on every preset, so a traded run recorded before 0.8.5 matches up
to its first trade and differs after it. One traded `evaluate` run has a
digest from 0.8.5, checked on all five platforms: the reference agents on
`pt-v20`, with their orders, fills and scorecards.
[docs/SUPPORT.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/SUPPORT.md)
lists what each digest covers.

```python
eng = tf.Engine(seed=42, universe=u, model="pt-v10")
```

## Publishing a result

To let a reader rerun a result, publish its `RunManifest`: it records the
version, preset, seed, universe, macro state and scenario, and `reproduce()` stops on a mismatch.
It checks the market and carries no score: its `result` block holds the
market's `digest`, the number of `days` and `draws_consumed`, and
`tf.evaluate` and `tf.rank` write no manifest. A published score has to be
rerun to be checked.
[docs/SUPPORT.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/SUPPORT.md)
says which release to pin for a long study.

## Showing a score was not tuned to its seeds

To show a score was not tuned to its seeds, publish `tf.commit(seeds, salt)`
before the run and the seeds and salt after it. Draw the seeds with
`secrets.randbits(64)`. `tf.reveal(commitment, seeds, salt)` checks the pair,
and `tf.sealed_battery(seeds, salt)` builds the fingerprint battery on those
seeds. `tf.fingerprint.fingerprint(agent)` hashes what an agent ordered
across that battery's fixed markets, so two versions of an agent, with a
changed prompt or in another framework, can be checked for whether they
ordered the same things. It says nothing about which of the two is better.
The default battery is version 2: seven markets, one per shipped scenario,
each run 120 days so a day-50 shock has seventy days after it.
`tf.battery(1)`, the six 60-day markets of earlier releases, still builds,
and a fingerprint compares only against one taken on the same version.
