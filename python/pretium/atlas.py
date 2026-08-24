"""Atlas: a map of how the model responds, instead of a search that guesses.

A calibration search walks the parameter space from wherever it starts,
minimising one number. That works when you already know which parameters
matter and what you are trading away. When you do not, it fails in a
specific and expensive way: the optimiser sells whatever the objective
cannot see, and you discover which thing that was only after the run, by
noticing a gate you never put in the loss. Six consecutive searches were
rejected that way in two days, each rejection revealing a trade the scalar
objective had made silently.

This module surveys the space instead. Sample it broadly, measure
everything you care about at every point, and then ask the resulting table
the questions a search cannot answer:

- **Which parameters move which outputs?** (`Survey.sensitivity`)
- **What SHAPE does the effect have** -- monotone, saturating, an interior
  optimum, nothing? (`Survey.profile`)
- **Which parameters move nothing**, and are wasting a search's budget?
  (`Survey.unidentified`)
- **What trades are available**, all at once, rather than one rejection at
  a time? (`Survey.pareto`, `Survey.report_front`)
- **Why does B beat A**, decomposed per parameter rather than asserted?
  (`Survey.attribution`)
- **What drives this output**, as a sentence a reader can check?
  (`Survey.explain`)
- **Does a screening finding survive fresh paths at full resolution?**
  (`Survey.confirm`)

The frontier is the point. A scalar objective picks a trade FOR you,
silently, and the only way to see which one is to check a gate afterwards.
A frontier shows every trade at the same time and lets a human choose.

# It is not a training loop

Nothing here fits parameters to a target. There is no gradient, no
objective, and no "best" vector: `survey` measures, and the analysis
methods describe. That is deliberate. The failure this module exists to
prevent is trusting an optimiser over a landscape nobody has looked at, and
a second optimiser layered on the first would not fix it.

# Explanations state their basis, or they are worse than numbers

Half of this module renders words: `explain`, `report_front`, and the
`summary` inside `attribution`. The register those follow is fixed by a
recent, concrete failure: the best known candidate was described in a
design document as delivering crisis severity "through correlation", which
was right for one of its two parameters and backwards for the other --
every number involved was correct, and the connective sentence was
invented. So every rendered explanation here carries the row counts, the
ranges, and the dropped-row tally it stands on, and states its own limits
inline: a rank correlation is not an elasticity, a binned marginal is an
estimate with the other fifty parameters varying, an attribution assumes
additivity. A number invites scepticism; a sentence does not, so the
sentence has to carry its own.

# Cost, stated plainly

A survey costs whatever `measure` costs, times the sample count. It is
meant to be run once at a screening resolution and reused via `save`/`load`
for every later question, not re-run per question. A screening resolution
is exactly that: on the shipped preset, eight of the ten panel statistics
have their across-seed p10-p90 range crossing a band edge, so any point
this map recommends still needs a full-seed (thirty, not six) confirmation
before it is believed. `unidentified` and the shapes in `explain` rank and
describe; they do not certify.

# Considered and rejected, so the next reader does not re-litigate blind

- **Partial rank correlation (PRCC) beside Spearman.** On a Latin
  hypercube the columns are independent by construction, so partialling
  out the other parameters moves each coefficient by O(1/n) while adding
  machinery that implies a precision gain it does not deliver. If a survey
  is ever built on a correlated design, revisit.
- **An automatic all-pairs interaction scan.** Fifty-four parameters is
  1,431 pairs, each tested on a halved sample at screening resolution:
  noise dressed as findings. The targeted question -- "does this parameter
  act differently when that one is high?" -- is asked with the `where=`
  filter on `sensitivity` and `profile`, deliberately one hypothesis at a
  time.
- **A feasibility gate inside `survey`.** Which vectors a model is
  stationary over is the caller's knowledge (for pretium it lives in
  `tools/calibration/instrumentlib.feasibility_violation`, not in the
  type). `plan` exists so a caller can check the vectors before spending
  anything, and a `measure` that raises is recorded rather than fatal.
"""

from __future__ import annotations

import json
import math
import statistics
import warnings
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from ._core import ValidationError

#: The default box around a shipped value when a caller names a parameter
#: without a range: a quarter to four times it, matching the calibration
#: convention. A CONVENTION, not knowledge -- the one time it decided
#: something it was wrong: the best known `crisis_blend_ramp` (6.0, four
#: point three times ship) sat outside this box's ceiling of 5.6, and a
#: 96-core search inside the box concluded there was nothing to find.
#: Override any range you have actual information about.
DEFAULT_BOX = (0.25, 4.0)

#: Default sampling seed, so an undirected survey is still reproducible.
DEFAULT_SEED = 20260824


def latin_hypercube(n: int, dims: int, seed: int) -> list[list[float]]:
    """`n` points in the unit cube, one per stratum on every axis.

    A plain uniform sample leaves gaps and clumps that read as structure
    when anything is fitted to them. Latin hypercube sampling stratifies
    each axis independently, so every parameter is sampled evenly across
    its range no matter how many other parameters there are -- the property
    that makes a few thousand points informative in fifty dimensions rather
    than merely scattered.

    Uses the library's own `GameRng` so a survey is reproducible from its
    seed like everything else here, on any platform.
    """
    from ._core import GameRng

    rng = GameRng(int(seed), 7717)
    columns: list[list[float]] = []
    for _ in range(dims):
        strata = [(i + rng.next_float()) / n for i in range(n)]
        # Fisher-Yates against the same stream, so the permutation is part
        # of the reproducible draw rather than a separate source.
        # (GameRng.next_int is inclusive of both bounds.)
        for i in range(n - 1, 0, -1):
            j = rng.next_int(0, i)
            strata[i], strata[j] = strata[j], strata[i]
        columns.append(strata)
    return [[columns[d][i] for d in range(dims)] for i in range(n)]


@dataclass(frozen=True)
class Axis:
    """One parameter and the range the survey moves it over."""

    name: str
    low: float
    high: float
    #: Sample the log of the value. Right for scale parameters spanning
    #: orders of magnitude, where a linear sample would put almost every
    #: point in the top decade. `profile` bins in the same geometry, so a
    #: log axis gets near-uniform bin counts instead of a crowded first bin.
    log: bool = False

    def __post_init__(self) -> None:
        if not (math.isfinite(self.low) and math.isfinite(self.high)):
            raise ValidationError(f"{self.name!r}: range must be finite, "
                                  f"got {(self.low, self.high)}")
        if not self.high > self.low:
            raise ValidationError(f"{self.name!r}: high must exceed low, "
                                  f"got {(self.low, self.high)}")
        if self.log and self.low <= 0.0:
            raise ValidationError(
                f"{self.name!r}: a log axis needs a positive low, got "
                f"{self.low}; zero is infinitely far away in log space"
            )

    def at(self, unit: float) -> float:
        """The parameter value at position `unit` in [0, 1]."""
        if self.log:
            return math.exp(math.log(self.low)
                            + unit * (math.log(self.high) - math.log(self.low)))
        return self.low + unit * (self.high - self.low)

    def unit(self, value: float) -> float:
        """`at` inverted: where a value sits in [0, 1]. Used for binning."""
        if self.log:
            return ((math.log(value) - math.log(self.low))
                    / (math.log(self.high) - math.log(self.low)))
        return (value - self.low) / (self.high - self.low)


def axes_for(names: Iterable[str], preset: str = "pt-v3",
             ranges: Mapping[str, tuple[float, float]] | None = None,
             log: Iterable[str] = ()) -> list[Axis]:
    """Axes for `names`, defaulting to a multiplicative box around `preset`.

    `ranges` overrides the default box per parameter, and overriding is not
    an afterthought: the most recent search failed precisely because a
    default box excluded the known optimum, so any range you have real
    information about should be passed explicitly.

    Three refusals, each closing a silent-failure path:

    - A parameter shipped at zero (or below) has no multiplicative box, so
      it is refused rather than given an invented range. An inert
      mechanism's scale is a modelling decision, and guessing it would put
      the map's most interesting region somewhere nobody chose.
    - A key in `ranges` or `log` that is not in `names` is refused. A
      typo'd override would otherwise leave the default box silently in
      place -- the caller believes the range was widened, and it was not,
      which is the crisis_blend_ramp failure with worse ergonomics.
    - A duplicated name is refused; two axes over one parameter would make
      the second silently win.

    And one warning: an explicit range that EXCLUDES the preset's shipped
    value is legal (surveying a far region deliberately is a real use) but
    suspicious enough to say out loud, because such a survey cannot anchor
    anything it finds to the model actually being run.
    """
    import pretium

    names = list(names)
    if len(set(names)) != len(names):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise ValidationError(f"duplicated parameter names: {dupes}")
    ranges = dict(ranges or {})
    logset = set(log)
    for label, keys in (("ranges", set(ranges)), ("log", logset)):
        unknown = sorted(keys - set(names))
        if unknown:
            raise ValidationError(
                f"{label} names parameters not being surveyed: {unknown}. "
                "Refused rather than ignored: a typo here would leave a "
                "default box silently in place, which is how a search "
                "excluded its own optimum."
            )
    shipped = pretium.ModelParams.from_preset(preset)
    out = []
    for name in names:
        value = getattr(shipped, name, None)
        if value is None:
            raise ValidationError(f"{name!r} is not a parameter of {preset}")
        if name in ranges:
            lo, hi = ranges[name]
            if not (lo <= value <= hi):
                warnings.warn(
                    f"{name!r}: range {(lo, hi)} excludes the {preset} "
                    f"value {value}; the survey will not see the model you "
                    "are running", stacklevel=2)
        else:
            if value <= 0.0:
                raise ValidationError(
                    f"{name!r} ships at {value}, so it has no multiplicative "
                    "box. Give it an explicit range: an inert mechanism's "
                    "scale is a modelling decision, not something to guess."
                )
            lo, hi = value * DEFAULT_BOX[0], value * DEFAULT_BOX[1]
        out.append(Axis(name, lo, hi, name in logset))
    return out


def plan(axes: Sequence[Axis], samples: int, seed: int = DEFAULT_SEED
         ) -> list[dict[str, float]]:
    """The parameter vectors a survey will measure. Pure and reproducible.

    Separated from `survey` so a caller can inspect, filter or
    feasibility-check the plan before spending anything on it.
    `ModelParams.from_preset` accepts any numbers -- the stationarity gate
    lives in the calibration tooling, not in the type -- and a sweep once
    ran two non-stationary vectors and reported the best as the day's
    result. Checking the plan first is how that stops happening.
    """
    if samples < 8:
        raise ValidationError(
            f"a survey of {samples} points describes the sample rather than "
            "the model; use at least 8, and hundreds for anything you intend "
            "to read a sensitivity from")
    unit = latin_hypercube(samples, len(axes), seed)
    return [{a.name: a.at(u[i]) for i, a in enumerate(axes)} for u in unit]


@dataclass
class Survey:
    """A measured table of parameter vectors against outcomes.

    Rows are appended by `record` (or by `survey`), carry the plan index
    they came from, and are either measured -- parameters plus outputs --
    or errored: parameters plus the error string, kept because a parameter
    region that breaks the model is a fact about the model. Every analysis
    method reports how many rows it used and how many it dropped, because a
    statistic computed over a quietly shrunken sample carries the full
    sample's authority -- a bug this project has been bitten by more than
    once.
    """

    axes: list[Axis]
    rows: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    # -- recording ---------------------------------------------------------

    def record(self, index: int, parameters: Mapping[str, float],
               outputs: Mapping[str, Any] | None = None,
               error: str | None = None) -> None:
        """Append one measured (or failed) vector.

        Exactly one of `outputs` and `error`. Parameters must cover exactly
        the axes: a row missing an axis would silently fall out of every
        sensitivity, and an extra key would be dark data nothing analyses.
        """
        if (outputs is None) == (error is None):
            raise ValidationError(
                "record takes exactly one of outputs= (a measurement) or "
                "error= (why there is none)")
        expected = {a.name for a in self.axes}
        got = set(parameters)
        if got != expected:
            missing, extra = sorted(expected - got), sorted(got - expected)
            raise ValidationError(
                f"row parameters must match the axes exactly; "
                f"missing {missing}, unexpected {extra}")
        row: dict[str, Any] = {"index": int(index),
                               "parameters": dict(parameters),
                               "outputs": dict(outputs or {})}
        if error is not None:
            row["error"] = str(error)
        self.rows.append(row)

    # -- bookkeeping -------------------------------------------------------

    def axis(self, name: str) -> Axis:
        for a in self.axes:
            if a.name == name:
                return a
        raise ValidationError(
            f"{name!r} is not a surveyed parameter. Surveyed: "
            f"{sorted(a.name for a in self.axes)}")

    def outputs(self) -> list[str]:
        """Every output name any measured row carries, in first-seen order."""
        keys: list[str] = []
        for row in self.rows:
            for k in row["outputs"]:
                if k not in keys:
                    keys.append(k)
        return keys

    def errors(self) -> list[dict[str, Any]]:
        """The rows whose measurement failed, with their recorded reasons."""
        return [r for r in self.rows if "error" in r]

    def provenance(self) -> dict[str, Any]:
        """What this map stands on: counts, ranges, and the survey's meta.

        Attach this (or the counts every analysis method already returns)
        to anything quoted out of the survey. A number that has shed its
        row count and ranges is a number someone will treat as a property
        of the model rather than of this sample.
        """
        return {
            "rows": len(self.rows),
            "measured": len(self.rows) - len(self.errors()),
            "errors": len(self.errors()),
            "outputs": self.outputs(),
            "axes": {a.name: {"low": a.low, "high": a.high, "log": a.log}
                     for a in self.axes},
            "meta": dict(self.meta),
        }

    def _usable(self, output: str, where: Mapping[str, tuple[float, float]]
                | None) -> tuple[list[tuple[dict, float]], dict[str, int]]:
        """Rows with a finite value for `output`, plus the drop tally."""
        if where:
            for name in where:
                self.axis(name)  # refuses a typo'd filter parameter
        errored = nonfinite = filtered = 0
        seen_key = False
        kept: list[tuple[dict, float]] = []
        for row in self.rows:
            if "error" in row:
                errored += 1
                continue
            if output in row["outputs"]:
                seen_key = True
            value = row["outputs"].get(output)
            if not _finite(value):
                nonfinite += 1
                continue
            if where and not all(
                    lo <= row["parameters"][n] <= hi
                    for n, (lo, hi) in where.items()):
                filtered += 1
                continue
            kept.append((row["parameters"], value))
        if self.rows and not seen_key:
            raise ValidationError(
                f"no row measures an output named {output!r}. Measured "
                f"outputs: {self.outputs()}. Refused rather than returning "
                "an empty answer, because a typo here would read as a "
                "finding.")
        return kept, {"rows_total": len(self.rows), "rows_used": len(kept),
                      "rows_error": errored, "rows_nonfinite": nonfinite,
                      "rows_outside_where": filtered}

    # -- analysis ----------------------------------------------------------

    def sensitivity(self, output: str,
                    where: Mapping[str, tuple[float, float]] | None = None
                    ) -> dict[str, Any]:
        """Rank-correlation of each parameter against one output, ranked.

        Spearman rather than Pearson, because these responses saturate: a
        curved monotone relationship reads correctly under ranks and reads
        weak under a linear measure, which would bury exactly the strong
        saturating effects a calibration cares about.

        The magnitude ranks INFLUENCE OVER THE SAMPLED RANGE; it is not an
        elasticity and must not be quoted as one. A value near zero is
        evidence of no monotone relationship over this range at this
        resolution -- not proof of inertness. A parameter that acts only in
        combination, or only past a threshold the sample never crossed,
        reads zero here; `where=` restricts the rows to a region of OTHER
        parameters to ask that targeted question (at the cost of a smaller,
        noisier sample, which the returned counts make visible).

        Returns the correlations with their basis attached, following the
        same discipline as `pretium.loss`: the numbers ride inside the
        provenance rather than travelling bare::

            {"output", "correlations": {param: rho, ...},   # sorted by |rho|
             "rows_total", "rows_used", "rows_error",
             "rows_nonfinite", "rows_outside_where", "where"}
        """
        vals, counts = self._usable(output, where)
        if len(vals) < 8:
            raise ValidationError(
                f"only {len(vals)} usable rows for {output!r}; a rank "
                "correlation over this few points describes the sample, not "
                "the model")
        ys = _ranks([v for _, v in vals])
        rho = {}
        for axis in self.axes:
            xs = _ranks([p[axis.name] for p, _ in vals])
            rho[axis.name] = _pearson(xs, ys)
        return {
            "output": output,
            "correlations": dict(sorted(rho.items(), key=lambda kv: -abs(kv[1]))),
            "where": dict(where) if where else None,
            **counts,
        }

    def unidentified(self, outputs: Sequence[str],
                     threshold: float | None = None) -> list[str]:
        """Parameters with no monotone influence on ANY named output.

        These are where a search spends its budget for nothing. The default
        threshold is three standard deviations of a Spearman correlation
        under the null (3 / sqrt(n - 1), n the usable row count), so what
        counts as "no influence" tightens as the survey grows instead of
        being a constant that is generous at four thousand rows and inside
        the noise at forty.

        Same caveat as `sensitivity`, and it is the operative one: this is
        "no monotone effect over THESE ranges at THIS resolution". A
        parameter that acts only in combination, or only past a threshold
        the sample never crossed, appears here without being inert -- so
        this list justifies deprioritising a parameter in a search, never
        deleting a mechanism.
        """
        keep: set[str] = set()
        for name in outputs:
            result = self.sensitivity(name)
            cut = (threshold if threshold is not None
                   else 3.0 / math.sqrt(result["rows_used"] - 1))
            for param, r in result["correlations"].items():
                if abs(r) >= cut:
                    keep.add(param)
        return [a.name for a in self.axes if a.name not in keep]

    def profile(self, param: str, output: str, bins: int = 12,
                where: Mapping[str, tuple[float, float]] | None = None
                ) -> dict[str, Any]:
        """The binned marginal of one output against one parameter.

        `sensitivity` gives direction and strength; this gives SHAPE, which
        is what a decision usually turns on -- an interior optimum says
        "ship a tuned value" where a monotone slide to the range edge says
        "the mechanism is net harmful here", and a rank correlation cannot
        tell those apart.

        Rows are binned by the parameter's value IN THE AXIS'S OWN GEOMETRY
        (log axes bin in log space), so under Latin hypercube sampling the
        bin counts come out near-uniform. Read the counts: a lopsided one
        means rows were lost to measurement errors or a `where=` filter,
        and the centre of a thin bin is noise.

        Each bin reports a median and the p10-p90 spread, never a bare
        mean: every other surveyed parameter is VARYING inside a bin, so
        the within-bin spread is genuinely large, and a centre quoted
        without it would imply a precision the survey does not have. What
        the marginal does measure -- and a one-dimensional sweep at a fixed
        base point does not -- is whether the effect holds with everything
        else moving, rather than at one configuration.

        Returns::

            {"param", "output", "log",
             "bins": [{"low", "high", "n", "median", "p10", "p90"}, ...],
             "rows_total", "rows_used", ...drop tally..., "where"}
        """
        axis = self.axis(param)
        if bins < 2:
            raise ValidationError(f"bins must be at least 2, got {bins}")
        vals, counts = self._usable(output, where)
        if len(vals) < 3 * bins:
            raise ValidationError(
                f"{len(vals)} usable rows across {bins} bins is fewer than "
                "three rows a bin; a marginal that thin describes the "
                "sample, not the model. Use fewer bins or more rows.")
        buckets: list[list[float]] = [[] for _ in range(bins)]
        for params, value in vals:
            u = axis.unit(params[param])
            buckets[min(max(int(u * bins), 0), bins - 1)].append(value)
        rows = []
        for i, bucket in enumerate(buckets):
            ordered = sorted(bucket)
            rows.append({
                "low": axis.at(i / bins),
                "high": axis.at((i + 1) / bins),
                "n": len(ordered),
                "median": statistics.median(ordered) if ordered else None,
                "p10": _percentile(ordered, 0.10) if ordered else None,
                "p90": _percentile(ordered, 0.90) if ordered else None,
            })
        return {"param": param, "output": output, "log": axis.log,
                "bins": rows, "where": dict(where) if where else None,
                **counts}

    def pareto(self, objectives: Mapping[str, str]) -> dict[str, Any]:
        """The non-dominated rows over several objectives at once.

        `objectives` maps an output name to `"min"` or `"max"`. A row is on
        the frontier when no other row is at least as good on every
        objective and strictly better on one.

        This is the method the whole module exists for. A scalar objective
        picks a trade for you and hides it; a frontier lays every available
        trade side by side. Every rejected candidate in this project's
        history was a trade discovered after a forty-minute run --
        long-horizon realism sold for short-horizon fit, crisis severity
        sold for clustering -- and each would have been visible here in
        advance. `report_front` renders the same frontier with each point's
        trades in words.

        Rows missing any named objective (errored, or non-finite there) are
        excluded and counted in the result. Returns::

            {"objectives", "front": [rows...],
             "rows_total", "rows_considered", "dominated"}
        """
        for direction in objectives.values():
            if direction not in ("min", "max"):
                raise ValidationError(
                    f"objective direction must be 'min' or 'max', "
                    f"got {direction!r}")
        keys = list(objectives)
        for key in keys:
            self._usable(key, None)  # refuses an output no row measures
        usable = [r for r in self.rows if "error" not in r
                  and all(_finite(r["outputs"].get(k)) for k in keys)]

        def at_least_as_good(a, b):
            return all(
                (a["outputs"][k] <= b["outputs"][k]) if objectives[k] == "min"
                else (a["outputs"][k] >= b["outputs"][k]) for k in keys)

        def strictly_better(a, b):
            return any(
                (a["outputs"][k] < b["outputs"][k]) if objectives[k] == "min"
                else (a["outputs"][k] > b["outputs"][k]) for k in keys)

        front = [row for row in usable
                 if not any(at_least_as_good(o, row) and strictly_better(o, row)
                            for o in usable if o is not row)]
        return {"objectives": dict(objectives), "front": front,
                "rows_total": len(self.rows),
                "rows_considered": len(usable),
                "dominated": len(usable) - len(front)}

    def attribution(self, a: Mapping[str, float], b: Mapping[str, float],
                    output: str, bins: int = 12,
                    measured: tuple[float, float] | None = None
                    ) -> dict[str, Any]:
        """Why does B differ from A on this output, per parameter?

        For each parameter where the two vectors differ, reads the survey's
        binned marginal (`profile`) at both values and reports the
        difference as that parameter's estimated contribution, with a rough
        noise scale beside it. The failure this exists to prevent is
        specific and recent: a two-parameter candidate was explained with
        one confident sentence that was right about one parameter and
        BACKWARDS about the other, because nothing decomposed the effect --
        the numbers were correct and the explanation was invented.

        Honesty about what this is: an ESTIMATE under an additivity
        assumption. Each contribution is a main effect read off a marginal
        with every other parameter varying; strong interactions between the
        changed parameters are attributed to neither and land in the
        residual (reported when `measured` -- the actually-measured output
        at A and at B -- is supplied). Contributions smaller than twice
        their noise scale are listed in `within_noise` rather than in the
        ranking, because "0.002 from X" at screening resolution is a coin
        flip wearing four decimals.

        Both vectors must state a value for every parameter they set, must
        set only surveyed parameters, and must sit inside the surveyed
        ranges -- outside them the marginal would be extrapolation, which
        is refused rather than clamped.

        Returns the decomposition with a rendered `summary` sentence whose
        caveats travel with it::

            {"output", "predicted_delta",
             "contributions": {param: {"delta", "se", "from", "to"}, ...},
             "within_noise": [param, ...],
             "measured_delta", "residual",      # None without `measured`
             "assumes", "rows_used", "summary"}
        """
        changed = {}
        for name in sorted(set(a) | set(b)):
            if name not in (n.name for n in self.axes):
                raise ValidationError(
                    f"{name!r} is not a surveyed parameter, so the survey "
                    "cannot attribute anything to it")
            if (name in a) != (name in b):
                raise ValidationError(
                    f"{name!r} is set on one vector and not the other; "
                    "state both sides explicitly, because a default filled "
                    "in here would be this module inventing the comparison")
            if a[name] != b[name]:
                changed[name] = (a[name], b[name])
        contributions: dict[str, dict[str, float]] = {}
        within_noise: list[str] = []
        rows_used = None
        for name, (va, vb) in changed.items():
            axis = self.axis(name)
            for label, v in (("A", va), ("B", vb)):
                if not (axis.low <= v <= axis.high):
                    raise ValidationError(
                        f"{name!r}={v} on vector {label} is outside the "
                        f"surveyed range ({axis.low}, {axis.high}); the "
                        "marginal there would be extrapolation, so it is "
                        "refused rather than clamped")
            prof = self.profile(name, output, bins=bins)
            rows_used = prof["rows_used"]
            ya, ea = _marginal_at(prof, axis, va)
            yb, eb = _marginal_at(prof, axis, vb)
            delta = yb - ya
            se = math.sqrt(ea * ea + eb * eb)
            entry = {"delta": delta, "se": se, "from": va, "to": vb}
            if abs(delta) < 2.0 * se:
                within_noise.append(name)
            contributions[name] = entry
        ranked = dict(sorted(
            ((n, c) for n, c in contributions.items()
             if n not in within_noise),
            key=lambda kv: -abs(kv[1]["delta"])))
        predicted = sum(c["delta"] for c in contributions.values())
        measured_delta = residual = None
        if measured is not None:
            measured_delta = measured[1] - measured[0]
            residual = measured_delta - predicted
        assumes = (
            "additivity: each contribution is a main effect read off a "
            "binned marginal with every other parameter varying; an "
            "interaction between the changed parameters is attributed to "
            "neither and lands in the residual")
        return {
            "output": output,
            "predicted_delta": predicted,
            "contributions": {**ranked,
                              **{n: contributions[n] for n in within_noise}},
            "within_noise": within_noise,
            "measured_delta": measured_delta,
            "residual": residual,
            "assumes": assumes,
            "rows_used": rows_used,
            "summary": _attribution_summary(
                output, changed, ranked, within_noise, predicted,
                measured_delta, rows_used),
        }

    def confirm(self, candidate: Mapping[str, float],
                baseline: Mapping[str, float],
                measure: Callable[[Mapping[str, float], int],
                                  Mapping[str, Any]],
                seed_blocks: Sequence[Sequence[int]]) -> dict[str, Any]:
        """Does a screening finding survive fresh paths, at full resolution?

        The other half of the survey loop, and still not an optimiser: the
        survey PROPOSES at screening resolution, and this tests one
        proposal on paths it was not found on. The failure it exists to
        prevent was measured the day this was written: a candidate was
        declared shippable on a +0.1297 gap found on the discovery seed
        block, and on three fresh blocks the same gap read -0.0315,
        +0.0209 and +0.0233 -- reversing sign once. The discovery sweep
        and its "validation" had used the same seeds, so re-measuring
        reproduced the same fluctuation exactly and called it
        confirmation: it tested reproducibility of the MEASUREMENT, not
        of the EFFECT. Seven retractions in this project trace to that
        distinction.

        So the seed hygiene is structural, not advisory. The survey must
        carry the seeds that measured it in `meta["seeds"]` (the shipped
        driver records them; set them yourself when building surveys by
        hand), and any confirmation seed found in that list -- or shared
        between blocks -- is REFUSED, not warned about, because a warning
        is a thing people read past and this is the one mistake that has
        to be impossible.

        `measure(vector, seed)` returns the full-resolution outputs for
        one vector on one seed; both vectors are measured on the SAME
        seeds, so each block's gap (median over the block, candidate minus
        baseline) is paired and the path noise largely cancels. A seed
        whose measurement raises fails the whole call rather than being
        recorded: a confirmation with quietly missing paths would carry
        the full block's authority, and unlike a survey row, there is no
        analysis downstream to skip it honestly.

        Several DISJOINT blocks, not one: one block tells you a number,
        several tell you whether it is a property of the model, and the
        rendered summary says so in as many words when only one is given.
        The baseline is required rather than defaulted, for the reason
        `attribution` refuses one-sided vectors -- a default filled in
        here would be this module inventing the comparison.

        Returns, with a rendered `summary` in the register of `explain`::

            {"outputs": {name: {"gaps": [per block], "mean_gap",
                                "sd_gap",            # None for one block
                                "consistent_sign", "reverses_sign",
                                "per_block": [{"seeds", "candidate",
                                               "baseline", "gap"}, ...]}},
             "seed_blocks", "survey_seeds", "summary"}
        """
        if "seeds" not in self.meta:
            raise ValidationError(
                "this survey does not record which seeds measured it "
                "(meta['seeds']), so the seed-overlap check cannot run -- "
                "and it runs on records, not on trust. Set meta['seeds'] "
                "when building the survey.")
        survey_seeds = set(self.meta["seeds"])
        blocks = [list(b) for b in seed_blocks]
        if not blocks or any(not b for b in blocks):
            raise ValidationError("confirm needs at least one non-empty "
                                  "seed block")
        flat = [s for b in blocks for s in b]
        overlap = sorted(survey_seeds & set(flat))
        if overlap:
            raise ValidationError(
                f"confirmation seeds {overlap} were used by the survey "
                "itself; an effect found on those paths would confirm "
                "itself. Refused, not warned: this is the mistake that has "
                "to be impossible.")
        if len(set(flat)) != len(flat):
            raise ValidationError(
                "a seed appears in more than one confirmation block; "
                "re-used paths would fake across-block agreement")
        if dict(candidate) == dict(baseline):
            raise ValidationError(
                "candidate and baseline are identical; there is no effect "
                "to confirm")

        per_block: list[dict[str, Any]] = []
        keys: list[str] | None = None
        for block in blocks:
            cand = [dict(measure(candidate, s)) for s in block]
            base = [dict(measure(baseline, s)) for s in block]
            if keys is None:
                keys = sorted(k for k in cand[0] if _finite(cand[0][k]))
                if not keys:
                    raise ValidationError(
                        "measure returned no finite outputs")
            for rows, label in ((cand, "candidate"), (base, "baseline")):
                for row, seed in zip(rows, block):
                    missing = [k for k in keys if not _finite(row.get(k))]
                    if missing:
                        raise ValidationError(
                            f"{label} measurement at seed {seed} has no "
                            f"finite value for {missing}; a confirmation "
                            "with holes is not a confirmation")
            per_block.append({
                "seeds": list(block),
                "medians": {
                    k: (statistics.median(r[k] for r in cand),
                        statistics.median(r[k] for r in base))
                    for k in keys},
            })

        outputs: dict[str, Any] = {}
        for k in keys or ():
            gaps = [b["medians"][k][0] - b["medians"][k][1]
                    for b in per_block]
            outputs[k] = {
                "gaps": gaps,
                "mean_gap": statistics.fmean(gaps),
                "sd_gap": statistics.stdev(gaps) if len(gaps) > 1 else None,
                "consistent_sign": (all(g > 0 for g in gaps)
                                    or all(g < 0 for g in gaps)),
                "reverses_sign": (any(g > 0 for g in gaps)
                                  and any(g < 0 for g in gaps)),
                "per_block": [
                    {"seeds": len(b["seeds"]),
                     "candidate": b["medians"][k][0],
                     "baseline": b["medians"][k][1],
                     "gap": b["medians"][k][0] - b["medians"][k][1]}
                    for b in per_block],
            }
        return {
            "outputs": outputs,
            "seed_blocks": blocks,
            "survey_seeds": sorted(survey_seeds),
            "summary": _confirm_summary(outputs, blocks),
        }

    # -- rendered explanations --------------------------------------------

    def explain(self, output: str, top: int = 8,
                threshold: float | None = None) -> str:
        """What drives one output, as text a reader can check.

        The ranked drivers with their rank correlations, the SHAPE of each
        read off its marginal (rising, saturating, interior optimum, ...),
        and what showed no measurable effect -- with the row counts and the
        caveats printed in the same block, so the sentence cannot travel
        without its basis. Shape words are conservative: a reversal or a
        flattening is only named when it exceeds the marginal's own noise
        scale, because an explanation that overclaims is worse than a bare
        number.
        """
        sens = self.sensitivity(output)
        n = sens["rows_used"]
        cut = threshold if threshold is not None else 3.0 / math.sqrt(n - 1)
        drivers = [(name, r) for name, r in sens["correlations"].items()
                   if abs(r) >= cut]
        flat = [name for name, r in sens["correlations"].items()
                if abs(r) < cut]
        lines = [
            f"what moves {output!r} -- measured on {n} of "
            f"{sens['rows_total']} sampled vectors "
            f"({sens['rows_error']} errored, "
            f"{sens['rows_nonfinite']} without a finite value)",
            "",
        ]
        if drivers:
            lines.append("drivers, by rank correlation over the sampled "
                         "range:")
            for name, r in drivers[:top]:
                axis = self.axis(name)
                shape = _shape(self.profile(name, output), axis)
                span = (f"({_fmt(axis.low)} to {_fmt(axis.high)}"
                        f"{', log' if axis.log else ''})")
                lines.append(f"  {name:28s} rho {r:+.2f}  {shape}  {span}")
            if len(drivers) > top:
                rest = ", ".join(name for name, _ in drivers[top:])
                lines.append(f"  ...and above threshold but not shown: {rest}")
        else:
            lines.append(f"no parameter clears the |rho| >= {cut:.3f} "
                         "threshold: over these ranges, at this resolution, "
                         "nothing moves this output monotonically.")
        if flat:
            lines += ["",
                      "no monotone effect measured (over these ranges, at "
                      "this resolution -- deprioritise in a search, do not "
                      "call inert):",
                      "  " + ", ".join(flat)]
        lines += [
            "",
            "read with care: a rank correlation ranks influence over the "
            "sampled range and is not an elasticity; a near-zero value is "
            "evidence of no monotone effect over THIS range, not proof of "
            "inertness -- a parameter acting only in combination, or past "
            "a threshold the sample never crossed, reads zero here.",
        ]
        note = self.meta.get("resolution")
        if note:
            lines.append(f"resolution: {note}")
        return "\n".join(lines)

    def report_front(self, objectives: Mapping[str, str], limit: int = 40
                     ) -> str:
        """`pareto` rendered with each point's trades stated in words.

        Every one of the six rejected searches behind this module was "it
        traded X for Y", discovered after the run. This prints, for each
        frontier point, where it is the frontier's best and where its
        worst, so the trade each point embodies is on the page before
        anything is run. The wording is deliberately neutral -- best/worst
        on named outputs -- because which trade is worth taking is the
        human's call, and this module has no opinion about what the
        outputs mean.
        """
        result = self.pareto(objectives)
        front = result["front"]
        keys = list(objectives)
        head = (f"pareto front over {keys}: {len(front)} of "
                f"{result['rows_considered']} usable rows "
                f"({result['rows_total']} sampled, "
                f"{result['dominated']} dominated)")
        if not front:
            return head + "\nno usable rows reach every objective."
        best = {k: (min if objectives[k] == "min" else max)(
            r["outputs"][k] for r in front) for k in keys}
        worst = {k: (max if objectives[k] == "min" else min)(
            r["outputs"][k] for r in front) for k in keys}
        ordered = sorted(front, key=lambda r: r["outputs"][keys[0]])
        lines = [head, ""]
        for row in ordered[:limit]:
            cells = "  ".join(f"{k}={_fmt(row['outputs'][k])}" for k in keys)
            wins = [k for k in keys if row["outputs"][k] == best[k]]
            pays = [k for k in keys if row["outputs"][k] == worst[k]
                    and len(front) > 1]
            trade = ""
            if wins:
                trade += "front-best " + ", ".join(wins)
            if pays:
                trade += ("; " if trade else "") + \
                    "front-worst " + ", ".join(pays)
            lines.append(f"  vector {row['index']:>5d}  {cells}"
                         + (f"   [{trade}]" if trade else ""))
        if len(front) > limit:
            lines.append(f"  ...and {len(front) - limit} more frontier "
                         "points (raise limit=, or read pareto() directly)")
        lines += [
            "",
            "every frontier point is a trade nobody has to discover after "
            "a run; which one is worth taking is not this module's call. "
            "Frontier membership at screening resolution is itself noisy "
            "-- confirm a chosen point at full seeds before shipping it.",
        ]
        return "\n".join(lines)

    # -- persistence -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "axes": [{"name": a.name, "low": a.low, "high": a.high,
                      "log": a.log} for a in self.axes],
            "rows": self.rows,
            "meta": self.meta,
        }

    def save(self, path: str) -> str:
        """Write the survey to JSON, measurement once, questions forever."""
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=1)
            handle.write("\n")
        return path

    @classmethod
    def load(cls, path: str) -> "Survey":
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
        return cls(
            axes=[Axis(**a) for a in doc["axes"]],
            rows=doc["rows"], meta=doc.get("meta", {}),
        )


def survey(axes: Sequence[Axis],
           measure: Callable[[dict[str, float]], Mapping[str, Any]],
           samples: int, seed: int = DEFAULT_SEED,
           progress: Callable[[int, int], None] | None = None) -> Survey:
    """Measure `samples` planned vectors and return the map.

    `measure` takes one parameter vector and returns whatever outputs you
    care about, as a mapping. It is supplied by the caller rather than
    fixed here, because "what you care about" is the question the survey
    is asking and this module has no business answering it: a panel of
    realism statistics, a strategy's Sharpe ratio, a fill rate, an agent's
    score -- all are the same shape of question.

    A vector whose measurement raises is recorded with its error and
    skipped by the analysis methods rather than failing the survey. A
    parameter region that breaks the model is a fact about the model, and
    losing the other few thousand points to it would be the wrong trade.

    The vectors are exactly `plan(axes, samples, seed)`, in order -- so a
    caller who feasibility-checked the plan measured what was checked. For
    a survey too expensive to run in-process (a cluster, a worker pool),
    run `plan` yourself, measure however you like, and `Survey.record`
    each result; this function is the one-process convenience, not the
    contract.
    """
    vectors = plan(axes, samples, seed)
    out = Survey(axes=list(axes), meta={"samples": samples, "seed": seed})
    for i, vector in enumerate(vectors):
        # `measure` alone is inside the try: pretium's own refusals
        # (ValidationError included) are facts about the vector's region
        # and belong in the record, while a malformed row from `record`
        # itself is a bug here and must raise.
        try:
            outputs: Mapping[str, Any] | None = dict(measure(vector))
            error = None
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            outputs, error = None, f"{type(exc).__name__}: {exc}"
        out.record(i, vector, outputs=outputs, error=error)
        if progress is not None:
            progress(i + 1, len(vectors))
    return out


# -- helpers ----------------------------------------------------------------

def _finite(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v)


def _ranks(values: Sequence[float]) -> list[float]:
    """Midranks (ties share the average), the Spearman convention."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return 0.0 if dx == 0 or dy == 0 else num / (dx * dy)


def _percentile(ordered: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile of an already-sorted sequence."""
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _bin_noise(b: Mapping[str, Any]) -> float:
    """A rough scale for a bin median's noise, from its own spread.

    Normal-theory arithmetic (p10-p90 span is 2.56 sd; a median's standard
    error is 1.25 sd/sqrt(n)) applied to data nobody claims is normal:
    an order-of-magnitude device for separating signal from coin flips,
    not an inference. Degenerate bins get an infinite scale, so nothing
    read from them ever clears a noise gate.
    """
    if not b["n"] or b["p90"] is None or b["p10"] is None:
        return math.inf
    sd = (b["p90"] - b["p10"]) / 2.563
    if sd == 0.0:
        return 0.0
    return 1.253 * sd / math.sqrt(b["n"])


def _marginal_at(prof: Mapping[str, Any], axis: Axis, value: float
                 ) -> tuple[float, float]:
    """The marginal's centre at `value`: interpolated median, noise scale.

    Linear interpolation between the centres of non-empty bins, in the
    axis's own geometry; beyond the outermost centres the nearest bin's
    median is used flat rather than extrapolated.
    """
    pts = []
    bins = prof["bins"]
    for i, b in enumerate(bins):
        if b["n"]:
            centre = axis.at((i + 0.5) / len(bins))
            pts.append((axis.unit(centre), b["median"], _bin_noise(b)))
    if not pts:
        raise ValidationError("every bin of the marginal is empty")
    u = axis.unit(value)
    if u <= pts[0][0]:
        return pts[0][1], pts[0][2]
    if u >= pts[-1][0]:
        return pts[-1][1], pts[-1][2]
    for (u0, y0, e0), (u1, y1, e1) in zip(pts, pts[1:]):
        if u0 <= u <= u1:
            t = (u - u0) / (u1 - u0)
            return y0 + t * (y1 - y0), max(e0, e1)
    raise AssertionError("unreachable: u inside the hull found no segment")


def _shape(prof: Mapping[str, Any], axis: Axis) -> str:
    """A conservative word for a marginal's shape.

    Reads the non-empty bin medians against the marginal's own noise scale
    and names only what exceeds it: "flat at this resolution" when the
    whole range of medians is within noise, an interior extremum only when
    the rise and the fall EACH exceed twice the noise, "saturating" only
    when the second half of the move is under a quarter of the first.
    Everything fuzzier degrades to plain "rising"/"falling". The bias is
    deliberate: a shape word that overclaims gets quoted, and this project
    has already paid for one confidently wrong mechanism sentence.
    """
    pts = [(i, b["median"], _bin_noise(b))
           for i, b in enumerate(prof["bins"]) if b["n"]]
    if len(pts) < 4:
        return "too few populated bins to name a shape"
    medians = [m for _, m, _ in pts]
    noise = statistics.median(e for _, _, e in pts)
    lo, hi = min(medians), max(medians)
    if not math.isfinite(noise) or hi - lo < 2.0 * noise:
        return "flat at this resolution"
    peak = max(range(len(pts)), key=lambda i: medians[i])
    trough = min(range(len(pts)), key=lambda i: medians[i])
    for idx, kind in ((peak, "maximum"), (trough, "minimum")):
        if 0 < idx < len(pts) - 1:
            before = medians[idx] - medians[0]
            after = medians[idx] - medians[-1]
            sign = 1.0 if kind == "maximum" else -1.0
            if sign * before > 2.0 * noise and sign * after > 2.0 * noise:
                where = axis.at((pts[idx][0] + 0.5) / len(prof["bins"]))
                return f"interior {kind} near {_fmt(where)}"
    direction = "rising" if medians[-1] > medians[0] else "falling"
    half = len(medians) // 2
    first = medians[half] - medians[0]
    second = medians[-1] - medians[half]
    if abs(first) > 4.0 * abs(second) and abs(first) > 2.0 * noise:
        return f"{direction}, saturating"
    return direction


def _confirm_summary(outputs: Mapping[str, Mapping[str, Any]],
                     blocks: Sequence[Sequence[int]]) -> str:
    """The confirmation as text: what reproduced, what reversed, and the
    single-block caveat when it applies. Verdicts are about the gap's
    behaviour across blocks -- sign agreement, and the mean against the
    across-block spread -- never the word "true": a consistent gap on k
    fresh blocks is evidence the effect belongs to the model, not proof.
    """
    k = len(blocks)
    lines = [f"confirmation on {k} disjoint block{'s' if k != 1 else ''} of "
             f"{', '.join(str(len(b)) for b in blocks)} seeds "
             "(disjoint from the survey's: checked, not trusted)", ""]
    for name, row in outputs.items():
        gaps = "  ".join(f"{g:+.4g}" for g in row["gaps"])
        if k == 1:
            verdict = "one block: a number, not a confirmation"
        elif all(g == 0 for g in row["gaps"]):
            verdict = "no difference measured"
        elif row["reverses_sign"]:
            verdict = ("sign REVERSES across blocks -- indistinguishable "
                       "from path luck")
        else:
            sd = row["sd_gap"] or 0.0
            if abs(row["mean_gap"]) > 2.0 * sd / math.sqrt(k):
                verdict = (f"consistent sign in {k}/{k} blocks, mean "
                           "clears the across-block spread")
            else:
                verdict = (f"consistent sign in {k}/{k} blocks, but the "
                           "mean is within the across-block spread")
        sd_txt = f" sd {row['sd_gap']:.4g}" if row["sd_gap"] is not None else ""
        lines.append(f"  {name:24s} gaps {gaps}   "
                     f"mean {row['mean_gap']:+.4g}{sd_txt}   {verdict}")
    lines.append("")
    if k == 1:
        n = len(blocks[0])
        lines.append(
            f"a single block cannot distinguish an effect of the model "
            f"from a property of its {n} particular paths -- the last "
            "candidate confirmed that way reversed sign on the next "
            "block. Measure more disjoint blocks before deciding "
            "anything.")
    else:
        lines.append(
            "a consistent gap across disjoint blocks is evidence the "
            "effect belongs to the model rather than to the paths it was "
            "found on; a reversal means the screening finding was the "
            "paths. Stated as measured -- nothing here is proof.")
    return "\n".join(lines)


def _attribution_summary(output: str, changed: Mapping[str, tuple],
                         ranked: Mapping[str, Mapping[str, float]],
                         within_noise: Sequence[str], predicted: float,
                         measured_delta: float | None,
                         rows_used: int | None) -> str:
    if not changed:
        return (f"the two vectors are identical on every surveyed "
                f"parameter, so the survey attributes no difference in "
                f"{output!r} to them")
    if measured_delta is not None:
        head = (f"{output!r} moves {measured_delta:+.4g} from A to B "
                f"(marginals predict {predicted:+.4g})")
    else:
        head = (f"the marginals predict {output!r} moves {predicted:+.4g} "
                "from A to B")
    parts = [f"{name} {c['delta']:+.4g} (noise ~{c['se']:.2g}; "
             f"{_fmt(c['from'])} to {_fmt(c['to'])})"
             for name, c in ranked.items()]
    body = "; ".join(parts) if parts else \
        "no single parameter's contribution clears its noise"
    tail = ""
    if within_noise:
        tail = ("; within noise: " + ", ".join(within_noise))
    basis = (f". Estimated from binned marginals over {rows_used} surveyed "
             "rows under an additivity assumption, at screening "
             "resolution -- confirm any decision-bearing number with a "
             "full-seed measurement.")
    return head + ": " + body + tail + basis


def _fmt(v: float) -> str:
    return f"{v:.4g}"
