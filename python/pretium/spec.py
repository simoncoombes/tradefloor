"""A strategy as data: declarative, versioned, hashable.

Everything else in a run serialises, hashes and round-trips — the seed, the
universe fingerprint, the model preset, the scenario, the order log. The
strategy did not. ``evaluate`` takes any object with an ``act`` method, so the
moment a result depends on an agent it depends on a Python callable that
cannot be written into a methods section. A reader could re-run your seed and
get your market, then had no way to get your strategy.

This module closes that gap for the strategies that are already data. The
shipped baselines share one grammar — a signal, a concentration (``top_k``),
an exposure (``gross``), and a participation cap — and a
:class:`StrategySpec` writes that grammar down:

```python
spec = pt.StrategySpec.momentum(lookback_days=1.0, top_k=5)
scores = pt.evaluate({"momentum": spec}, seed=7, universe=u)
scores["momentum"].strategy_fingerprint    # cite this
```

Three properties, each load-bearing:

- **It round-trips.** ``StrategySpec.from_json(s.to_json()) == s``, and
  ``s.build().spec == s``. Without both directions this would be
  documentation rather than a specification.
- **It hashes.** :attr:`StrategySpec.fingerprint` is a sha256 over the
  canonical form, so a result can carry its strategy's identity next to its
  seed and universe fingerprint. The canonical form is what makes the hash
  mean something — see the property's docstring.
- **It is versioned on semantics.** ``spec_version`` pins what the words
  mean, exactly as the model preset pins the coefficients. If ``momentum``
  ever changes what it ranks, that is ``spec_version: 2`` — a spec whose
  meaning drifts while its version holds would look reproducible while not
  being, which is worse than no spec at all.

## What it deliberately cannot express

Stated plainly, because the limit is the design rather than an omission:
path dependence (stop losses, drawdown limits, anything reading its own P&L
history), conditional logic ("momentum in calm markets, reversion in
stress"), custom signals, and anything reading engine internals except
``oracle`` — the one privileged signal, which declares itself as such.

The escape hatch stays open: write a Python agent, as now. The cost is that
the result is not citable as a spec, and the methods section has to cite
code at a commit instead. That is an honest trade, and stating it is what
stops this grammar from sprawling into a programming language.

## The decisions the design left open, decided here

**Cadence is in the spec.** Trading frequency moves results more than any
signal parameter — the measured rebalance table in ``baselines`` swings the
same one-day signal from +88.7% to -13.2% — so a strategy whose identity
excluded it would not be identified: two runs of the same fingerprint could
disagree in sign. But the thing the spec pins is the strategy's own decision
rule, not the harness's step granularity. ``cadence: "daily"`` re-decides
once per day whatever ``steps_per_day`` the harness runs; ``cadence:
"step"`` — the default, and what every shipped baseline does — delegates to
the harness explicitly, and a methods section quoting such a spec must quote
``steps_per_day`` beside ``days`` and the seed. ``steps_per_day`` itself
stays a harness parameter, because it also sets how often every agent is
observed, which is experimental apparatus rather than strategy.

**``evaluate`` accepts specs directly.** A spec in the agents mapping is
built fresh inside every call, which also closes a real trap: agents are
stateful, and a reused instance carries one market's history into the next
with no visible symptom. It is also what an MCP server needs — a tool
cannot accept a callable — and what stops callers inventing their own
serialisation on the way to one.

**Blend weights are normalised, canonically.** Selection ranks the blended
score and takes the top k, so the agent is invariant under any positive
scaling of the weight vector: weights of 1.2/0.8 and 0.6/0.4 build
bit-identical agents. Taking weights as given would therefore let two
textually different specs name the same strategy under different
fingerprints — identity finer than the thing identified, which defeats
comparability from the opposite direction to semantic drift. Weights are
divided by the sum of their absolute values at construction; signs and
ratios — everything behaviourally meaningful, including the net-short-signal
tilt a negative weight expresses — survive.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from ._core import GameRng, ValidationError

#: Bumped when the MEANING of a spec changes, never for additive growth. A new
#: signal kind or cadence value extends the grammar and old specs keep meaning
#: what they meant; changing what ``momentum`` ranks, how a blend combines, or
#: how the canonical form is computed changes what existing specs SAY, and
#: that is a new version — exactly as a coefficient change is a new model
#: preset rather than an edit to "pt-v1".
SPEC_VERSION = 1

#: Every signal kind the grammar knows. ``blend`` combines the ranked kinds;
#: ``oracle`` is privileged and declares itself as such — see this module's
#: docstring and :class:`pretium.baselines.Oracle`.
SIGNAL_KINDS = ("hold", "random", "momentum", "mean_reversion", "oracle",
                "blend")

#: How often the strategy re-decides. ``step`` is every decision step the
#: harness offers (what every shipped baseline does); ``daily`` is the first
#: step of each day, whatever ``steps_per_day`` the harness runs.
CADENCES = ("step", "daily")

#: The kinds that produce a ranking and therefore take ``top_k``. ``hold``
#: owns the whole roster and ``random`` weights every name by its draw, so a
#: concentration parameter on either would describe nothing.
_RANKED = ("momentum", "mean_reversion", "oracle", "blend")

#: The kinds a blend may combine. ``hold`` ranks nothing, so blending it is
#: meaningless; ``random`` as a component contributes a uniformly random
#: RANKING — noise dilution, a legitimate ablation — which is deliberately
#: not the same strategy as the bare ``random`` signal, whose weights carry
#: the draws' magnitudes as well as their order.
_BLENDABLE = ("random", "momentum", "mean_reversion", "oracle")

_TREND = ("momentum", "mean_reversion")

# Defaults mirror the shipped baselines, field for field, so the named
# constructors with no arguments build exactly the reference agents.
_DEFAULT_LOOKBACK_DAYS = 1.0
_DEFAULT_TOP_K = 5
_DEFAULT_GROSS = {"hold": 1.0, "random": 0.5}
_DEFAULT_PARTICIPATION = {"hold": 0.05}


def _number(name: str, value: Any, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a number, got {value!r}")
    out = float(value)
    if not math.isfinite(out):
        raise ValidationError(f"{name} must be finite, got {value!r}")
    if positive and out <= 0.0:
        raise ValidationError(f"{name} must be positive, got {value!r}")
    return out


def _integer(name: str, value: Any, *, minimum: int = 1) -> int:
    if isinstance(value, bool):
        raise ValidationError(f"{name} must be an integer, got {value!r}")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValidationError(f"{name} must be an integer, got {value!r}")
        value = int(value)
    if not isinstance(value, int):
        raise ValidationError(f"{name} must be an integer, got {value!r}")
    if value < minimum:
        raise ValidationError(f"{name} must be >= {minimum}, got {value}")
    return value


def _refuse_unknown(what: str, given: Mapping[str, Any],
                    allowed: Sequence[str]) -> None:
    unknown = set(given) - set(allowed)
    if unknown:
        raise ValidationError(
            f"{what} has unknown field(s): {sorted(unknown)}. "
            f"Valid: {', '.join(sorted(allowed))}"
        )


def _canonical_component(raw: Mapping[str, Any]) -> dict[str, Any]:
    """One blend component, validated, weight not yet normalised."""
    if not isinstance(raw, Mapping) or "kind" not in raw:
        raise ValidationError(
            f"a blend component must be a mapping with a 'kind', got {raw!r}"
        )
    kind = raw["kind"]
    if kind == "hold":
        raise ValidationError(
            "'hold' cannot appear in a blend: it ranks nothing, so a weight "
            "on it would describe nothing"
        )
    if kind not in _BLENDABLE:
        raise ValidationError(
            f"unknown blend component kind {kind!r}. "
            f"Valid: {', '.join(_BLENDABLE)}"
        )
    if "weight" not in raw:
        raise ValidationError(f"blend component {kind!r} is missing 'weight'")
    weight = _number(f"weight of {kind!r}", raw["weight"])
    if weight == 0.0:
        raise ValidationError(
            f"blend component {kind!r} has weight 0, which makes it not part "
            "of the strategy. Remove it: a spectator component would change "
            "the fingerprint without changing the strategy."
        )
    component: dict[str, Any] = {"kind": kind, "weight": weight}
    if kind in _TREND:
        _refuse_unknown(f"blend component {kind!r}", raw,
                        ("kind", "weight", "lookback_days"))
        component["lookback_days"] = _number(
            f"{kind}.lookback_days", raw.get("lookback_days",
                                             _DEFAULT_LOOKBACK_DAYS),
            positive=True)
    else:
        _refuse_unknown(f"blend component {kind!r}", raw, ("kind", "weight"))
    return component


def _identity_of(component: Mapping[str, Any]) -> str:
    """What a component IS, weight aside — the merge and sort key."""
    return json.dumps({k: v for k, v in component.items() if k != "weight"},
                      sort_keys=True, separators=(",", ":"))


def _canonical_signal(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or "kind" not in raw:
        raise ValidationError(
            f"signal must be a mapping with a 'kind', got {raw!r}"
        )
    kind = raw["kind"]
    if kind not in SIGNAL_KINDS:
        raise ValidationError(
            f"unknown signal kind {kind!r}. Valid: {', '.join(SIGNAL_KINDS)}"
        )

    if kind in _TREND:
        _refuse_unknown(f"signal {kind!r}", raw, ("kind", "lookback_days"))
        return {"kind": kind,
                "lookback_days": _number(
                    f"{kind}.lookback_days",
                    raw.get("lookback_days", _DEFAULT_LOOKBACK_DAYS),
                    positive=True)}

    if kind != "blend":
        _refuse_unknown(f"signal {kind!r}", raw, ("kind",))
        return {"kind": kind}

    _refuse_unknown("signal 'blend'", raw, ("kind", "components"))
    components = raw.get("components")
    if not isinstance(components, Sequence) or isinstance(components, str) \
            or not components:
        raise ValidationError(
            "a blend needs a non-empty list of components"
        )

    # Merge identical components. The same signal named twice IS one signal
    # with the summed weight, and leaving both would let two spellings of one
    # strategy fingerprint apart.
    merged: dict[str, dict[str, Any]] = {}
    for entry in components:
        component = _canonical_component(entry)
        identity = _identity_of(component)
        if identity in merged:
            merged[identity]["weight"] += component["weight"]
        else:
            merged[identity] = component
    for identity, component in merged.items():
        if component["weight"] == 0.0:
            raise ValidationError(
                f"blend components {component['kind']!r} cancel to weight 0. "
                "A component that nets to nothing is not part of the strategy."
            )

    # Normalise to unit absolute mass. Selection ranks the blended score and
    # takes the top k, so the agent is invariant under positive scaling of
    # the weight vector — 1.2/0.8 and 0.6/0.4 build bit-identical agents —
    # and an unnormalised form would hash equal strategies apart. Signs and
    # ratios survive, so a net-short-signal tilt is still expressible.
    total = sum(abs(c["weight"]) for c in merged.values())
    ordered = sorted(merged.values(), key=_identity_of)
    for component in ordered:
        component["weight"] = component["weight"] / total

    # A blend of one ranked signal at weight +1.0 IS that signal: the rank of
    # a score orders exactly as the score, ties broken the same way, so the
    # collapse is behaviourally exact and keeps the fingerprint honest. A
    # single 'random' does NOT collapse — the bare signal weights names by
    # draw magnitude, the component only by draw order — and a single
    # negative weight does not either, because the reversed ordering agrees
    # with the opposite signal only up to tie handling.
    if len(ordered) == 1 and ordered[0]["weight"] == 1.0 \
            and ordered[0]["kind"] in _TREND + ("oracle",):
        only = dict(ordered[0])
        del only["weight"]
        return only

    return {"kind": "blend", "components": ordered}


def _contains_random(signal: Mapping[str, Any]) -> bool:
    if signal["kind"] == "random":
        return True
    return signal["kind"] == "blend" and any(
        c["kind"] == "random" for c in signal["components"]
    )


class StrategySpec:
    """A declarative strategy: buildable, serialisable, hashable.

    Immutable once constructed, for the same reason ``ModelParams`` would be:
    a fingerprint of a mutable object is a lie waiting to be told. Construct
    through the named constructors — :meth:`hold`, :meth:`random`,
    :meth:`momentum`, :meth:`mean_reversion`, :meth:`oracle`, :meth:`blend` —
    or pass the parts directly; either way the spec is canonicalised and
    validated here, at construction, where a mistake is visible.

    Defaults mirror the shipped baselines field for field, so
    ``StrategySpec.momentum()`` names exactly the agent ``Momentum()`` is.
    """

    __slots__ = ("_doc", "_fingerprint")

    def __init__(self, signal: Mapping[str, Any], *,
                 portfolio: Mapping[str, Any] | None = None,
                 execution: Mapping[str, Any] | None = None,
                 seed: int | None = None) -> None:
        portfolio = dict(portfolio or {})
        execution = dict(execution or {})
        _refuse_unknown("portfolio", portfolio, ("gross", "top_k"))
        _refuse_unknown("execution", execution,
                        ("cadence", "max_participation"))

        canonical_signal = _canonical_signal(signal)
        kind = canonical_signal["kind"]

        gross = _number("portfolio.gross",
                        portfolio.get("gross", _DEFAULT_GROSS.get(kind, 1.0)),
                        positive=True)
        canonical_portfolio: dict[str, Any] = {"gross": gross}
        if kind in _RANKED:
            canonical_portfolio["top_k"] = _integer(
                "portfolio.top_k", portfolio.get("top_k", _DEFAULT_TOP_K))
        elif "top_k" in portfolio:
            raise ValidationError(
                f"portfolio.top_k does not apply to {kind!r}: 'hold' owns "
                "the whole roster and 'random' weights every name, so a "
                "concentration parameter would describe nothing"
            )

        cadence = execution.get("cadence", "step")
        if cadence not in CADENCES:
            raise ValidationError(
                f"unknown cadence {cadence!r}. Valid: {', '.join(CADENCES)}"
            )
        if kind == "hold" and cadence != "step":
            raise ValidationError(
                "'hold' trades once and never again, so a cadence on it "
                "describes nothing — and two spellings of the same strategy "
                "must not fingerprint apart"
            )
        participation = _number(
            "execution.max_participation",
            execution.get("max_participation",
                          _DEFAULT_PARTICIPATION.get(kind, 0.02)),
            positive=True)
        canonical_execution = {"cadence": cadence,
                               "max_participation": participation}

        if _contains_random(canonical_signal):
            if seed is None:
                raise ValidationError(
                    "a spec containing the 'random' signal needs a seed: "
                    "without one the strategy is not reproducible, which is "
                    "the one property a spec exists to provide"
                )
            seed = _integer("seed", seed, minimum=0)
            if seed > 0xFFFF_FFFF:
                # The RNG's seed is 32-bit. Caught here, where the spec is
                # written, rather than at build time inside an evaluation.
                raise ValidationError(
                    f"seed must fit in 32 bits (0..4294967295), got {seed}"
                )
        elif seed is not None:
            raise ValidationError(
                "seed applies only to specs containing the 'random' signal. "
                "On a deterministic strategy it would draw nothing, and two "
                "identical strategies would fingerprint apart."
            )

        object.__setattr__(self, "_doc", {
            "spec_version": SPEC_VERSION,
            "signal": canonical_signal,
            "portfolio": canonical_portfolio,
            "execution": canonical_execution,
            "seed": seed,
        })
        object.__setattr__(self, "_fingerprint", None)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            "StrategySpec is immutable: a fingerprint of a mutable spec "
            "would be a lie. Construct a new one."
        )

    # -- named constructors ----------------------------------------------

    @classmethod
    def hold(cls, *, gross: float = 1.0,
             max_participation: float = 0.05) -> "StrategySpec":
        """Equal weight across the roster, bought once and left alone.

        ``gross`` is what :class:`pretium.baselines.BuyAndHold` calls
        ``leverage``: an equal-weight long-only book at weight ``gross/n`` IS
        a gross exposure of ``gross``, and the spec uses one word for one
        dimension across every kind.
        """
        return cls({"kind": "hold"}, portfolio={"gross": gross},
                   execution={"max_participation": max_participation})

    @classmethod
    def random(cls, *, seed: int = 0, gross: float = 0.5,
               max_participation: float = 0.02,
               cadence: str = "step") -> "StrategySpec":
        """Uniformly random target weights — the noise floor.

        ``seed`` seeds the strategy's own draws, on its own stream, exactly
        as :class:`pretium.baselines.RandomTrader` does. It is deliberately
        separate from the market seed and it is recorded in the spec, because
        a noise floor that cannot be reproduced is not a floor.
        """
        return cls({"kind": "random"}, portfolio={"gross": gross},
                   execution={"max_participation": max_participation,
                              "cadence": cadence},
                   seed=seed)

    @classmethod
    def momentum(cls, *, lookback_days: float = 1.0, top_k: int = 5,
                 gross: float = 1.0, max_participation: float = 0.02,
                 cadence: str = "step") -> "StrategySpec":
        """Long the recent winners, short the recent losers.

        The lookback is in DAYS, only. The shipped class also accepts a
        lookback in steps, but a step is a harness artifact — the same number
        means a different horizon under a different ``steps_per_day`` — and a
        spec that changed meaning with harness configuration would not
        specify anything.
        """
        return cls({"kind": "momentum", "lookback_days": lookback_days},
                   portfolio={"top_k": top_k, "gross": gross},
                   execution={"max_participation": max_participation,
                              "cadence": cadence})

    @classmethod
    def mean_reversion(cls, *, lookback_days: float = 1.0, top_k: int = 5,
                       gross: float = 1.0, max_participation: float = 0.02,
                       cadence: str = "step") -> "StrategySpec":
        """Long the recent losers, short the recent winners."""
        return cls({"kind": "mean_reversion",
                    "lookback_days": lookback_days},
                   portfolio={"top_k": top_k, "gross": gross},
                   execution={"max_participation": max_participation,
                              "cadence": cadence})

    @classmethod
    def oracle(cls, *, top_k: int = 5, gross: float = 1.0,
               max_participation: float = 0.02,
               cadence: str = "step") -> "StrategySpec":
        """Trades the true mispricing. Privileged, and says so.

        A spec naming ``oracle`` declares access to state no real trader
        has, which is exactly what a reviewer needs to see — leaving it out
        of the grammar would push the one strategy most in need of
        disclosure into the uncitable escape hatch. Its ``top_k`` moves the
        denominator of every capture ratio the library quotes, so a ratio
        published without the oracle's spec fingerprint beside it is not a
        number anyone can compare. See :class:`pretium.baselines.Oracle` for
        the measurements.
        """
        return cls({"kind": "oracle"},
                   portfolio={"top_k": top_k, "gross": gross},
                   execution={"max_participation": max_participation,
                              "cadence": cadence})

    @classmethod
    def blend(cls, components: Sequence[Mapping[str, Any]], *,
              top_k: int = 5, gross: float = 1.0,
              max_participation: float = 0.02, cadence: str = "step",
              seed: int | None = None) -> "StrategySpec":
        """A weighted combination of ranked signals.

        Each component is a mapping with ``kind``, ``weight`` and the kind's
        own parameters:

        ```python
        pt.StrategySpec.blend([
            {"kind": "momentum",       "weight": 0.6, "lookback_days": 1.0},
            {"kind": "mean_reversion", "weight": 0.4, "lookback_days": 5.0},
        ], top_k=10)
        ```

        Weights are on the signal, not the portfolio: the components' ranks
        are combined FIRST and ``top_k`` selects from the blended ranking,
        which is a different strategy from selecting top-k from each and
        merging. Weights are normalised to unit absolute mass at
        construction — see this module's docstring for why taking them as
        given would hash equal strategies apart.

        ``seed`` is required exactly when a ``random`` component is present.
        """
        return cls({"kind": "blend", "components": list(components)},
                   portfolio={"top_k": top_k, "gross": gross},
                   execution={"max_participation": max_participation,
                              "cadence": cadence},
                   seed=seed)

    # -- the three properties --------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        """The canonical form, as a fresh mutable copy."""
        return json.loads(self.to_json(indent=None))

    def to_json(self, **kwargs: Any) -> str:
        """Serialise the canonical form.

        What is written is the CANONICAL spec — defaults materialised,
        weights normalised, components merged and sorted — not the keystrokes
        that built it. A reader of the JSON sees every parameter the strategy
        ran under, including the ones the author never typed.
        """
        kwargs.setdefault("indent", 2)
        if kwargs.get("indent") is None:
            kwargs.pop("indent")
        return json.dumps(self._doc, **kwargs)

    @classmethod
    def from_json(cls, text: str) -> "StrategySpec":
        """Rebuild a spec from :meth:`to_json` output.

        A newer ``spec_version`` is refused rather than read on a best-effort
        basis: a field this version does not understand would silently take a
        default, and the resulting strategy would be one nobody specified —
        while claiming, via its fingerprint, to be exactly what was written.
        """
        payload = json.loads(text)
        if not isinstance(payload, dict) or "signal" not in payload:
            raise ValidationError("not a pretium strategy spec document")
        _refuse_unknown("strategy spec", payload,
                        ("spec_version", "signal", "portfolio", "execution",
                         "seed"))
        version = payload.get("spec_version")
        if not isinstance(version, int) or isinstance(version, bool) \
                or version < 1:
            raise ValidationError(
                f"spec_version must be a positive integer, got {version!r}"
            )
        if version > SPEC_VERSION:
            raise ValidationError(
                f"spec_version {version} is newer than this version "
                f"understands ({SPEC_VERSION}). Upgrade pretium rather than "
                "reading it partially: the version pins what the words mean."
            )
        return cls(payload["signal"],
                   portfolio=payload.get("portfolio"),
                   execution=payload.get("execution"),
                   seed=payload.get("seed"))

    @property
    def fingerprint(self) -> str:
        """sha256 over the canonical serialisation.

        The hash is over CONTENT, not keystrokes: sorted keys, no
        whitespace, defaults materialised, blend weights normalised and
        components merged and sorted. Whitespace, key order, writing a
        default explicitly, scaling every weight by two, or listing
        components in a different order all leave it unchanged — a
        fingerprint that moved with formatting would be worse than none,
        because it would look stable while identifying nothing.

        ``spec_version`` is inside the hash deliberately. The same JSON
        under a later version means something different, so it must
        fingerprint differently.
        """
        cached = self._fingerprint
        if cached is None:
            canonical = json.dumps(self._doc, sort_keys=True,
                                   separators=(",", ":"))
            cached = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            object.__setattr__(self, "_fingerprint", cached)
        return cached

    # -- building ---------------------------------------------------------

    def build(self) -> Any:
        """Construct the agent this spec names. Fresh state every call.

        For a single ranked or held signal at step cadence this returns the
        shipped baseline class itself — the spec claims to name those agents,
        and returning the genuine article makes the claim true by
        construction rather than by parallel implementation. Blends and
        daily cadence return the grammar's own agents. Every returned agent
        carries this spec as ``.spec``, which is the second half of the
        round-trip.
        """
        from .baselines import (BuyAndHold, MeanReversion, Momentum, Oracle,
                                RandomTrader)

        signal = self._doc["signal"]
        portfolio = self._doc["portfolio"]
        execution = self._doc["execution"]
        kind = signal["kind"]

        if kind == "hold":
            agent: Any = BuyAndHold(
                leverage=portfolio["gross"],
                max_participation=execution["max_participation"])
        elif kind == "random":
            agent = RandomTrader(
                seed=self._doc["seed"], gross=portfolio["gross"],
                max_participation=execution["max_participation"])
        elif kind == "momentum":
            agent = Momentum(
                lookback_days=signal["lookback_days"],
                top_k=portfolio["top_k"], gross=portfolio["gross"],
                max_participation=execution["max_participation"])
        elif kind == "mean_reversion":
            agent = MeanReversion(
                lookback_days=signal["lookback_days"],
                top_k=portfolio["top_k"], gross=portfolio["gross"],
                max_participation=execution["max_participation"])
        elif kind == "oracle":
            agent = Oracle(
                top_k=portfolio["top_k"], gross=portfolio["gross"],
                max_participation=execution["max_participation"])
        else:
            agent = _BlendAgent(
                signal["components"], top_k=portfolio["top_k"],
                gross=portfolio["gross"],
                max_participation=execution["max_participation"],
                seed=self._doc["seed"])

        if execution["cadence"] == "daily":
            agent = _DailyCadence(agent)
        agent.spec = self
        return agent

    # -- identity ----------------------------------------------------------

    @property
    def spec_version(self) -> int:
        return self._doc["spec_version"]

    @property
    def signal(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._doc["signal"]))

    @property
    def portfolio(self) -> dict[str, Any]:
        return dict(self._doc["portfolio"])

    @property
    def execution(self) -> dict[str, Any]:
        return dict(self._doc["execution"])

    @property
    def seed(self) -> int | None:
        return self._doc["seed"]

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, StrategySpec):
            return NotImplemented
        return self._doc == other._doc

    def __hash__(self) -> int:
        return hash(self.fingerprint)

    def __repr__(self) -> str:
        kind = self._doc["signal"]["kind"]
        cadence = self._doc["execution"]["cadence"]
        return (f"StrategySpec({kind}, cadence={cadence}, "
                f"{self.fingerprint[:12]}...)")


class _BlendAgent:
    """The grammar's own agent: a weighted combination of ranked signals.

    Each component turns the roster into a ranking — most attractive to go
    long first, ties broken on ticker exactly as the shipped baselines break
    them — and the blended score is the weighted sum of ranks. Ranks rather
    than raw scores, because raw scores have incomparable units: a one-day
    return, a five-day return and a log mispricing would otherwise be
    weighted by their variances rather than by the weights.

    For a single ranked component this reproduces the shipped baseline
    trade for trade (there is a test), which is what licenses the canonical
    collapse of a one-component blend to its bare signal.
    """

    #: True when any component reads the truth column. Set per instance so a
    #: results table can label a blend that sees past the observation wall,
    #: exactly as it labels the bare Oracle.
    privileged = False

    def __init__(self, components: Sequence[Mapping[str, Any]], *,
                 top_k: int, gross: float, max_participation: float,
                 seed: int | None) -> None:
        from .baselines import RANDOM_AGENT_STREAM

        self._components = [dict(c) for c in components]
        self.top_k = int(top_k)
        self.gross = float(gross)
        self.max_participation = float(max_participation)
        self._history: list[list[float]] = []
        self._lookbacks: list[int | None] | None = None
        self._rng = (GameRng(int(seed), RANDOM_AGENT_STREAM)
                     if any(c["kind"] == "random" for c in self._components)
                     else None)
        if any(c["kind"] == "oracle" for c in self._components):
            self.privileged = True

    def _resolve_lookbacks(self, obs: Any) -> list[int | None]:
        # Resolved from the observation, as the shipped trend agents do: the
        # agent does not know the harness's cadence until it is handed one.
        resolved: list[int | None] = []
        for component in self._components:
            if component["kind"] in _TREND:
                steps = component["lookback_days"] * getattr(
                    obs, "steps_per_day", 1)
                resolved.append(max(1, int(round(steps))))
            else:
                resolved.append(None)
        return resolved

    def act(self, obs: Any) -> dict[str, float]:
        import struct

        from .baselines import _book, rebalance

        if self._lookbacks is None:
            self._lookbacks = self._resolve_lookbacks(obs)
        self._history.append(list(obs.prices))

        # Hold cash until EVERY component can speak. Guessing with a partial
        # blend would make the first days measure a different strategy, and
        # the warm-up check runs before any random draw so that the draw
        # schedule cannot depend on which component warms up last.
        needed = max((lb for lb in self._lookbacks if lb is not None),
                     default=0)
        if len(self._history) <= needed:
            return {}

        n = len(obs.tickers)
        k = min(self.top_k, n // 2)
        if k < 1:
            return {}

        blended = [0.0] * n
        for component, lookback in zip(self._components, self._lookbacks):
            kind = component["kind"]
            if kind in _TREND:
                past = self._history[-(lookback + 1)]
                now = self._history[-1]
                attractiveness = [
                    (now[i] / past[i] - 1.0) if past[i] > 0 else 0.0
                    for i in range(n)
                ]
                if kind == "mean_reversion":
                    attractiveness = [-a for a in attractiveness]
            elif kind == "oracle":
                s = struct.unpack(
                    "<%dd" % n, obs.engine.column("mispricing_s"))
                attractiveness = [-x for x in s]
            else:  # random: a uniformly random ranking, one draw per name
                attractiveness = [self._rng.next_float() for _ in range(n)]

            order = sorted(range(n),
                           key=lambda i: (-attractiveness[i],
                                          obs.tickers[i]))
            weight = component["weight"]
            for position, i in enumerate(order):
                blended[i] += weight * (n - 1 - position)

        final = sorted(range(n),
                       key=lambda i: (-blended[i], obs.tickers[i]))
        longs, shorts = final[:k], final[-k:]
        return rebalance(obs, _book(obs.tickers, longs, shorts,
                                    self.gross, k),
                         max_participation=self.max_participation)


class _DailyCadence:
    """Re-decide once per day, whatever the harness's step granularity.

    On the first step of each day the wrapped agent is handed a DAILY view —
    an observation whose ``steps_per_day`` is 1 and whose step counter is the
    day index — so a one-day lookback resolves to one daily observation
    rather than to however many steps the harness happens to run. On every
    other step the strategy holds: no decision, no history, and for a random
    signal no draw, so the strategy's randomness is a function of its
    decisions rather than of the harness's step count.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.privileged = bool(getattr(inner, "privileged", False))
        explain = getattr(inner, "explain", None)
        if callable(explain):
            # Forwarded so a wrapped Oracle still scores explanation
            # accuracy; the harness looks the attribute up on the object it
            # was handed.
            self.explain = explain

    def act(self, obs: Any) -> dict[str, float]:
        from .harness import Observation

        steps_per_day = getattr(obs, "steps_per_day", 1)
        if steps_per_day > 1 and obs.step % steps_per_day:
            return {}
        # The same observation, re-labelled as one decision per day: the step
        # counter becomes the day index and steps_per_day becomes 1, so a
        # wrapped agent's "one-day lookback" resolves to one daily
        # observation rather than to however many steps the harness runs.
        # (`_adv` is the harness's own field; this wrapper is part of the
        # same package and hands it on unchanged.)
        return self._inner.act(Observation(
            obs.day, obs.day, obs.tickers, obs.prices, obs.portfolio,
            obs.engine, obs._adv, 1))
