---
title: Atlas, mapping what the model responds to
nav_order: 17
rack: reference
short: Atlas
---

# Atlas

**What does my result actually depend on?**

A backtest cannot answer that, because a backtest has one market. Atlas
answers it by measuring many: it samples the simulator's parameter space,
measures whatever *you* care about at every point, and then describes the
response surface: which parameters move your outcome, what shape the
effect has, and what trades are available between the things you want.

It is deliberately **not** an optimiser. `survey` measures; the analysis
methods describe. There is no target, no gradient and no "best" vector.

## When you need it, and when you do not

You do not need Atlas to run a simulation or test a strategy. That is
[Running a simulation](running-a-simulation.md) and
[Agents and evaluation](agents-and-evaluation.md), and most users never go
further.

You need Atlas for the second-order question, *if the market were somewhat
different, would my edge survive?*, and for calibration work,
where you need to know which knobs matter before you turn any.

## Why it exists

Six consecutive calibration searches on this simulator were rejected, each
for the same underlying reason: a scalar objective collapses everything you
care about into one number, and the optimiser then sells whatever is not in
that number. Fixing one blind spot moved the selling to the next one.

The pattern is structural, not bad luck. **Whatever sits outside the
objective is free.** A Pareto front is the antidote: it shows every
available trade at the same time and makes a human choose, instead of an
optimiser choosing silently.

The other half of the diagnosis was that nobody had ever *measured* how the
parameters move the outputs. Every search was a walk through a room no one
had surveyed, in a direction someone had guessed.

## The shape of a session

```python
import statistics

from tradefloor import atlas
import tradefloor as tf

universe = tf.Universe.random(40, seed=111)
SURVEY_SEEDS = [11, 12, 13]     # the paths this screening pass runs on

axes = atlas.axes_for(
    ["market_factor_sigma", "garch_alpha", "momentum_theta"],
    preset="pt-v12",                         # box around the shipped values
    ranges={"momentum_theta": (0.0, 0.4)},   # override any default box
)

def measure(vector):
    model = tf.ModelParams.from_preset("pt-v12", **vector)
    runs = [tf.evaluate({"mine": tf.reference_agents(seed=3)["momentum"]},
                        seed=s, universe=universe, days=3, model=model)["mine"]
            for s in SURVEY_SEEDS]     # your agent goes where momentum is
    return {"return_pct": statistics.median(r.return_pct for r in runs),
            "turnover": statistics.median(r.turnover for r in runs)}

s = atlas.survey(axes, measure, samples=500)
s.meta["seeds"] = SURVEY_SEEDS  # what confirm() checks its blocks against
s.save("survey.json")           # measure once, ask many questions later
```

Pass `preset=` rather than taking the default. `axes_for` still defaults to
`preset="pt-v3"`, three era boundaries behind the shipped `pt-v14` -- pt-v3
handed over to `pt-v10`, and `pt-v10` to `pt-v12` on 2026-08-26 -- and every
box is a multiple of whatever preset it reads: `market_factor_sigma` ships at
0.016 on pt-v3 and 0.0088291 on pt-v12, so the default box runs 0.004 to 0.064
where the pt-v12 box runs 0.0022073 to 0.035316. The pt-v3 ceiling is 7.2
times the value the shipped model actually uses. Surveying a pt-v12 model
through pt-v3 boxes is a map of the wrong room.

`measure` is yours, and that is the point: Atlas has no opinion about
whether the outputs are realism statistics, a Sharpe ratio, a fill rate or
an agent's score. They are all the same shape of question. It has to be
yours for a second reason: a `Scorecard` carries `pnl`, `return_pct`,
`turnover`, `impact_bps`, `trades`, `max_leverage`, `explanation_accuracy`
and the run's fingerprints, and it carries no Sharpe and no drawdown. If you
want a risk-adjusted number, `measure` is where you compute it, from the run
you just did.

Then interrogate it:

```python
s.sensitivity("return_pct")            # which parameters move it, ranked
s.profile("momentum_theta", "return_pct")  # what shape: monotone? optimum?
s.unidentified(["return_pct", "turnover"]) # which move nothing at all
s.pareto({"return_pct": "max", "turnover": "min"})
print(s.explain("return_pct"))
print(s.report_front({"return_pct": "max", "turnover": "min"}))
```

### Ranges are the most consequential thing you choose

`axes_for` defaults to roughly a quarter to four times a preset's shipped
value. **That is a convention, not knowledge.** It has been wrong here: a
96-core search once concluded there was nothing to find because the best
known value of a parameter sat just outside the default ceiling.

Override any range you have real information about. A parameter that ships
at exactly `0.0` has no multiplicative box at all and Atlas refuses to guess
one. You must give it an explicit range, because guessing would silently
decide what the map can see.

### Sampling

Latin hypercube: each axis is stratified independently, so every parameter
is sampled evenly across its range regardless of how many other parameters
vary. That is what makes a few thousand points informative in fifty
dimensions rather than merely scattered. Sampling is seeded and
reproducible.

`plan(axes, samples)` returns the vectors *without* measuring them, so you
can check them before spending anything, since a sampled vector can easily be
outside the region your model is stationary over.

## Explanations, and why they carry their own limits

`explain`, `report_front` and `attribution`'s summary render prose. Every
one states the row counts, ranges and dropped-row tally it stands on, and
its own caveats inline.

That register was fixed by a specific failure. A candidate model was
described in a design document as delivering crisis severity "through
correlation", correct for one of its two parameters and *backwards* for the
other. Every number involved was right; the connecting sentence was
invented. **A number invites scepticism and a sentence does not**, so the
sentence has to carry its own.

### `attribution`, or why does B beat A?

```python
s.attribution(vector_a, vector_b, "return_pct", measured=(y_a, y_b))
```

Decomposes the difference across the parameters that differ. It **assumes
approximate additivity**, and strong interactions break it. On a purely
multiplicative surface it can overstate a contribution several-fold.

Pass `measured=` whenever you can. It supplies the true endpoint values, and
the residual between them and the decomposition is the only signal that the
additivity assumption failed. Without it, the summary says so explicitly
rather than quietly presenting an unchecked number.

## Confirmation: the step that is not optional

A survey runs at **screening resolution**, few seeds per point, chosen so
the map is affordable. That is enough to rank and describe. It is not enough
to believe.

```python
shipped = tf.ModelParams.from_preset("pt-v12").to_dict()
baseline = {a.name: shipped[a.name] for a in s.axes}
candidate = s.pareto({"return_pct": "max",
                      "turnover": "min"})["front"][0]["parameters"]

def measure_with_seed(vector, seed):        # one vector, one seed, full res
    model = tf.ModelParams.from_preset("pt-v12", **vector)
    r = tf.evaluate({"mine": tf.reference_agents(seed=3)["momentum"]},
                    seed=seed, universe=universe, days=3, model=model)["mine"]
    return {"return_pct": r.return_pct, "turnover": r.turnover}

s.confirm(candidate, baseline, measure_with_seed,
          seed_blocks=[range(201, 231), range(301, 331)])
```

`confirm` re-measures at full resolution on seed blocks **disjoint** from
the survey's, and reports the paired difference in each block. It refuses to
run on overlapping seeds.

Note the two shapes. `measure(vector)` screens, and chooses its own seeds;
`measure(vector, seed)` confirms, and is handed one. The gate compares
`seed_blocks` against `meta["seeds"]`, which is why the session above sets
it. `atlas.survey` records only `samples` and its sampling `seed`, so a
survey built by hand and not told its seeds cannot reach this step at all:
`confirm` raises rather than assuming, with *this survey does not record
which seeds measured it (meta['seeds'] is missing or empty), so the
seed-overlap check cannot run -- and it runs on records, not on trust*. The
gate can only see the integers it is handed, so a `measure` that ignores its
`seed` argument still defeats it. That honesty stays with you.

That refusal exists because of a real loss. A candidate here was declared
shippable on a 13% improvement. Measured on fresh seeds it read
+0.1297 where it was found, and -0.0315, +0.0209, +0.0233 elsewhere,
reversing sign once. Discovery and validation had used the same thirty
seeds, so re-measuring reproduced the same fluctuation exactly and reported
it as confirmation. **It tested reproducibility of the measurement, not of
the effect.**

One block gives you a number. Several tell you whether it is a property of
the model or of the paths.

## What Atlas cannot tell you

- **A near-zero sensitivity is not proof of inertness.** It means no
  monotone relationship was found *over the sampled range, at this
  resolution*. There are at least four ways a real effect reads as flat,
  and only the first is fixed by sampling harder:

  1. *Below the noise floor*, real but smaller than the seeds can see.
  2. *Only past a threshold* your sample never crossed.
  3. **Coupled to another parameter.** A sensitivity is one axis at a time,
     so a mechanism that needs two parameters is structurally invisible to
     it. In this simulator `universe_stress_weight` scales *remembered*
     crisis stress and `universe_stress_decay` is what makes stress survive
     the night; at the shipped decay of `0.0` the weight multiplies zero, so
     sweeping it alone measures nothing at **any** value, verified
     identical at 1, 3, 5, 7 and 10. Set together, they move the crisis
     transient by +0.021. A calibration note once recorded that gain against
     the weight alone, and a later replication of the weight alone then
     declared the mechanism dead. Both were reading a two-parameter
     mechanism one parameter at a time.
  4. **Saturating.** If the effect reaches its full size early in the range
     and then flattens, often because something downstream clamps it, most
     of your samples sit on a plateau, and a rank correlation over a step
     followed by a plateau is near zero however real the step is.

  `where=` asks (2) and (3) directly, but only if the sample survives the
  filter: conditioning the 4,000-vector survey here on `decay > 0.9` left
  ~380 rows across twelve bins with seventeen other parameters still moving,
  and the bin medians swung three times wider than the effect. **A filter
  that leaves too few rows does not answer the question, it just answers a
  noisier one.** Check the counts.
- **A rank correlation is not an elasticity.** It ranks influence; it does
  not measure how much. A one-at-a-time sweep and a marginal correlation
  measure different things, and disagreeing does not make either wrong.
- **Errors are not neutral.** A vector whose measurement raises is recorded
  and skipped rather than fatal, because a region that breaks the model is a fact
  about the model. But infrastructure failures cluster in expensive corners,
  so check *where* the drops are, not just how many.

## Related

- [The realism envelope](realism-envelope.md), what this simulator is
  certified to reproduce and where it is not
- [The realism metrics](realism-metrics.md), what each measured statistic
  means
