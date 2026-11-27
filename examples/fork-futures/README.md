# One market and four futures

```
git clone https://github.com/simoncoombes/tradefloor
cd tradefloor
pip install tradefloor
python examples/fork-futures/fork_futures.py
```

The script runs one seeded market for 40 days with a buy-and-hold portfolio
in it. It forks that world into four branches, applies a packaged scenario
to three of them, and runs all four for 80 more days. It takes seven to
twelve seconds of CPU, and it needs no keys, no network and no package beyond
tradefloor.

`fork_futures.ipynb` beside it imports the script and runs the same study,
with the table and the light figure in its committed output. The script is
the source of truth, and the notebook adds no computation of its own.

| branch | what changes after the fork |
|---|---|
| `control` | nothing |
| `rate_shock` | the packaged `rate_shock` scenario |
| `recession` | the packaged `recession` scenario |
| `liquidity_crisis` | the packaged `liquidity_crisis` scenario |

`tradefloor scenario show rate_shock` prints what a scenario file changes
and which of its entries are assumptions.

## Method

The market is `tf.World(seed=7, ...)` on 20 companies from
`tf.Universe.random(20, seed=11)`, on the default preset. The agent is
`tf.baselines.BuyAndHold()`, which buys an equal weight of every company
once and never rebalances. So the four curves differ by what the market did
to one fixed portfolio.

`world.fork(...)` returns four worlds that share the first 40 days. Before
any scenario is applied, `tf.agree` compares each scenario branch with the
control on nine checks, and the script prints the verdict for each pair.

Each scenario is applied with `world.apply(scenario, at=0)`. The packaged
files first fire on day 50 of a run, and `at=0` moves that first event to
the first day after the fork and keeps the gaps between the file's events.

## Output

This is the output on tradefloor 0.11.0, preset pt-v22. The last line, the
elapsed time, is left out.

```
  library   0.11.0
  preset    pt-v22
  seed      7
  universe  8fe0af0e183f9ad6f29c0bbbe6fb64d0c54b334dd333ae1099d1ce29e371c29e
  capital   $1,000,000, buy-and-hold

  rate_shock        identical to control at the fork: yes
                    sha256:2d4e440eb1af2d068c9742c8bf2cdbbb6c199d3d04e49abae9bd7ca662e5b567
  recession         identical to control at the fork: yes
                    sha256:c0cbfcb7c4d04c5fd3b0f87017a28878ffa83d48ea9c15d55d837f5d573ac7a5
  liquidity_crisis  identical to control at the fork: yes
                    sha256:7d8c8cc86e74bf02bcf800b3733a774b18fcada9b44ec47d69e84014489f8461

  INDEX LEVEL AT THE CLOSE, STARTING CAPITAL = 100
   day           control        rate_shock         recession  liquidity_crisis
     0            100.00            100.00            100.00            100.00
    10            101.15            101.15            101.15            101.15
    20            100.35            100.35            100.35            100.35
    30            103.71            103.71            103.71            103.71
    40            104.50            104.50            104.50            104.50
    50            103.53             95.86             75.40             78.47
    60            104.66             97.58             80.20             84.47
    70            106.70             98.83             82.37             86.55
    80            105.07             97.77             69.41             77.27
    90            106.71             99.44             72.75             80.32
   100            106.40             99.86             72.01             80.71
   110            108.00            100.85             70.80             80.83
   120            109.76            102.57             76.95             85.30

  CHANGE SINCE THE FORK
  control              +5.04%
  rate_shock           -1.85%
  recession           -26.36%
  liquidity_crisis    -18.37%

  wrote fork-futures-light.svg
  wrote fork-futures-dark.svg
```

The hash under each scenario is the scenario file's fingerprint.

## Figure

The script draws the four curves as SVG with the standard library, in a
light and a dark palette on a transparent background. A run writes both
files to `artifacts/`, which is git-ignored.

`figure/` holds a committed copy of the two files, and the project README
shows that copy. It is committed for the reason a notebook's output is
committed: a reader on GitHub sees it without running anything. Regenerate
it after a release with:

```
python examples/fork-futures/fork_futures.py --out examples/fork-futures/figure
```

`tests/test_fork_futures.py` runs the script twice and requires the same
bytes both times. It also requires the committed copy to match a new run
when the copy's caption names the installed version.

## Limits

The curves come from one seed, so they show one market and say little about
the average effect of a scenario. A scenario file states assumptions, and
the simulator runs the market those assumptions describe. The project
README, under "Method and limitations", gives the measured size of a driven
scenario against real markets: about half to four-fifths, in the right
direction. Read the direction of each curve and treat the size as an
example.
