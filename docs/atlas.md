---
title: Atlas — mapping what the model responds to
nav_order: 17
rack: reference
short: Atlas
---

# Atlas

**What does my result actually depend on?**

A backtest cannot answer that, because a backtest has one market. Atlas
answers it by measuring many: it samples the simulator's parameter space,
measures whatever *you* care about at every point, and then describes the
response surface — which parameters move your outcome, what shape the
effect has, and what trades are available between the things you want.

It is deliberately **not** an optimiser. `survey` measures; the analysis
methods describe. There is no target, no gradient and no "best" vector.

## When you need it, and when you do not

You do not need Atlas to run a simulation or test a strategy. That is
[Running a simulation](running-a-simulation.md) and
[Agents and evaluation](agents-and-evaluation.md), and most users never go
further.

You need Atlas for the second-order question — *if the market were
somewhat different, would my edge survive?* — and for calibration work,
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
from pretium import atlas
import pretium as pt

universe = pt.Universe.random(40, seed=111)

axes = atlas.axes_for(
    ["market_factor_sigma", "garch_alpha", "momentum_theta"],
    ranges={"momentum_theta": (0.0, 0.4)},   # override any default box
)

def measure(vector):
    model = pt.ModelParams.from_preset("pt-v3", **vector)
    scores = pt.evaluate({"mine": MyAgent()}, seed=7,
                         universe=universe, model=model)
    return {"sharpe": scores["mine"].sharpe,
            "drawdown": scores["mine"].max_drawdown}

s = atlas.survey(axes, measure, samples=500)
s.save("survey.json")           # measure once, ask many questions later
```

`measure` is yours, and that is the point: Atlas has no opinion about
whether the outputs are realism statistics, a Sharpe ratio, a fill rate or
an agent's score. They are all the same shape of question.

Then interrogate it:

```python
s.sensitivity("sharpe")            # which parameters move it, ranked
s.profile("momentum_theta", "sharpe")   # what SHAPE — monotone? an optimum?
s.unidentified(["sharpe", "drawdown"])  # which move nothing at all
s.pareto({"sharpe": "max", "drawdown": "min"})
print(s.explain("sharpe"))
print(s.report_front({"sharpe": "max", "drawdown": "min"}))
```

### Ranges are the most consequential thing you choose

`axes_for` defaults to roughly a quarter to four times a preset's shipped
value. **That is a convention, not knowledge.** It has been wrong here: a
96-core search once concluded there was nothing to find because the best
known value of a parameter sat just outside the default ceiling.

Override any range you have real information about. A parameter that ships
at exactly `0.0` has no multiplicative box at all and Atlas refuses to guess
one — you must give it an explicit range, because guessing would silently
decide what the map can see.

### Sampling

Latin hypercube: each axis is stratified independently, so every parameter
is sampled evenly across its range regardless of how many other parameters
vary. That is what makes a few thousand points informative in fifty
dimensions rather than merely scattered. Sampling is seeded and
reproducible.

`plan(axes, samples)` returns the vectors *without* measuring them, so you
can check them before spending anything — a sampled vector can easily be
outside the region your model is stationary over.

## Explanations, and why they carry their own limits

`explain`, `report_front` and `attribution`'s summary render prose. Every
one states the row counts, ranges and dropped-row tally it stands on, and
its own caveats inline.

That register was fixed by a specific failure. A candidate model was
described in a design document as delivering crisis severity "through
correlation" — correct for one of its two parameters and *backwards* for the
other. Every number involved was right; the connecting sentence was
invented. **A number invites scepticism and a sentence does not**, so the
sentence has to carry its own.

### `attribution` — why does B beat A?

```python
s.attribution(vector_a, vector_b, "sharpe", measured=(y_a, y_b))
```

Decomposes the difference across the parameters that differ. It **assumes
approximate additivity**, and strong interactions break it — on a purely
multiplicative surface it can overstate a contribution several-fold.

Pass `measured=` whenever you can. It supplies the true endpoint values, and
the residual between them and the decomposition is the only signal that the
additivity assumption failed. Without it, the summary says so explicitly
rather than quietly presenting an unchecked number.

## Confirmation: the step that is not optional

A survey runs at **screening resolution** — few seeds per point, chosen so
the map is affordable. That is enough to rank and describe. It is not enough
to believe.

```python
s.confirm(candidate, baseline, measure_with_seed,
          seed_blocks=[range(201, 231), range(301, 331)])
```

`confirm` re-measures at full resolution on seed blocks **disjoint** from
the survey's, and reports the paired difference in each block. It refuses to
run on overlapping seeds.

That refusal exists because of a real loss. A candidate here was declared
shippable on a 13% improvement — then measured on fresh seeds it read
+0.1297 where it was found, and −0.0315, +0.0209, +0.0233 elsewhere,
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

  1. *Below the noise floor* — real but smaller than the seeds can see.
  2. *Only past a threshold* your sample never crossed.
  3. **Coupled to another parameter.** A sensitivity is one axis at a time,
     so a mechanism that needs two parameters is structurally invisible to
     it. In this simulator `universe_stress_weight` scales *remembered*
     crisis stress and `universe_stress_decay` is what makes stress survive
     the night; at the shipped decay of `0.0` the weight multiplies zero, so
     sweeping it alone measures nothing at **any** value — verified
     identical at 1, 3, 5, 7 and 10. Set together, they move the crisis
     transient by +0.021. A calibration note once recorded that gain against
     the weight alone, and a later replication of the weight alone then
     declared the mechanism dead. Both were reading a two-parameter
     mechanism one parameter at a time.
  4. **Saturating.** If the effect reaches its full size early in the range
     and then flattens — often because something downstream clamps it — most
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
  and skipped rather than fatal — a region that breaks the model is a fact
  about the model. But infrastructure failures cluster in expensive corners,
  so check *where* the drops are, not just how many.

## Related

- [The realism envelope](realism-envelope.md) — what this simulator is
  certified to reproduce, and where it is not
- [The realism metrics](realism-metrics.md) — what each measured statistic
  means
