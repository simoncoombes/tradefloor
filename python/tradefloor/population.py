"""Background traders that share the market with your agents.

An evaluated agent normally trades against a market maker, latent depth and
the model's own flow, none of which reacts to it. So nothing in the market
ever notices an edge: ten copies of a strategy each earn what one earns, and
a programme that buys the same slices at the same minutes every day pays
what a randomised one pays. A :class:`Population` is the opt-in way to ask
whether an edge survives other traders. It is a small set of deterministic
participants that trade in the same agent-facing book as your agents, see the
same prices and pay the same costs.

```python
pop = tf.Population.standard()
cards = tf.evaluate({"m": my_agent}, seed=7, universe=roster, population=pop)
world = tf.World(seed=7, universe=roster, agents={...}, population=pop)
engine = tf.Engine(seed=7, universe=roster, population=pop)
```

## Two modes

**Isolated** (no population, the default) is what :func:`tradefloor.evaluate`
and :func:`tradefloor.rank` have always run: every agent meets the same
market to the bit, so a comparison between agents is a comparison of
strategies. Nothing here changes it, and a run without a population is the
run it was before populations existed.

**Populated** (``population=`` given) answers a different question. The
population reacts to what each agent does, so the market an agent meets
depends on its own trading. A populated result is reproducible (the same
seed, universe, model and population give the same market, bit for bit),
but two strategies in it no longer face identical markets, and a ranking
across them measures strategy and reaction together. :func:`tradefloor.rank`
therefore takes no population. In :func:`tradefloor.evaluate` each agent
trades alone with the population, in its own copy of the market; in a
:class:`tradefloor.World` with several agents, the agents and the
population share one market.

## The participants

Each participant sets a target position per name, in shares, from what the
market shows, and trades toward it with market orders. Sizes are shares of
the name's daily volume. Four kinds:

- ``trend`` piles into moves: long a name that has risen over ``lookback``
  sessions (five: the five-day momentum signal), in proportion to the move
  over the name's daily sigma, up to ``size``.
- ``reversion`` supplies the other side: the same signal turned round.
  At a lookback of one it trades the one-day reversal.
- ``liquidity`` leans against short moves (its own moving average, half-life
  ``half_life`` ticks) while the VIX is calm, and withdraws as it rises:
  full size at or below ``vix_calm``, nothing at or above ``vix_stress``.
- ``detector`` learns the agents' flow. Per name and per ``bucket`` ticks of
  the session it remembers (half-life ``memory`` sessions) the agents' net
  taker flow: a mean ``m`` and a variance ``v``. It expects ``m r^2`` with
  ``r = m^2 / (m^2 + v)``, so flow that recurs counts in full and flow seen
  once or at scattered minutes counts for little. It buys ``lead`` ticks
  ahead of flow it expects and sells ``hold`` ticks after; the window wraps
  across the night, so a programme that runs for days is held through. It
  adds to a position only where the quoted spread is at most ``max_spread``
  of the name's daily sigma, since on a name whose spread is a large part
  of its daily move a round trip costs more than the flow can pay. A programme that trades
  the same minutes every day is what it learns best; flow at random minutes
  stays an unreliable, small prediction. It sees the agents' flow per name,
  not per label.

Every participant decides every ``interval`` ticks (staggered), leaves gaps
smaller than ``band`` of its size alone, and trades at most ``rate`` of daily
volume per decision. It acts at the start of a tick, before the market moves:
an order an agent sends between ticks is ahead of the population on that
tick. Its orders take the maker's levels and the latent depth, move the
maker's inventory, can fill an agent's resting order (that fill is the
agent's, with the participant's label ``population:<name>`` as
counterparty), and reach the market on the tick they are sent. It takes no
random draw. Its own fills are on its ledger, which
:meth:`tradefloor.Engine.population_report` returns, not in any agent's.

It is not a model of latency or queues: every participant acts at the same
instant, posts no resting orders, and supplies liquidity only by trading
against moves, paying the spread to do so.

## Identity

A population is declarative and hashable. :attr:`Population.fingerprint`
digests the participants (not the name), a run records it beside the model
and universe fingerprints (:class:`tradefloor.RunManifest`, a scorecard's
``population_fingerprint``), and an engine's snapshot carries the
population's state under the same fingerprint, so a snapshot restores only
into an engine built with the population it came from.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable, Mapping

from ._core import ValidationError

#: The layout of :meth:`Population.as_dict`, which the fingerprint covers.
POPULATION_VERSION = 1

#: Each kind's own parameters, in the order the fingerprint lists them, with
#: whether each is a whole number.
_KIND_PARAMS: dict[str, tuple[tuple[str, bool], ...]] = {
    "trend": (("lookback", True), ("scale", False)),
    "reversion": (("lookback", True), ("scale", False)),
    "liquidity": (("half_life", False), ("scale", False),
                  ("vix_calm", False), ("vix_stress", False)),
    "detector": (("memory", False), ("bucket", True), ("lead", True),
                 ("hold", True), ("max_spread", False)),
}

_COMMON = ("size", "rate", "interval", "band")


class Participant:
    """One background trader: a kind, its sizing and its own parameters.

    Built with :meth:`trend`, :meth:`reversion`, :meth:`liquidity` or
    :meth:`detector`. Immutable; equal participants compare and hash equal.
    ``size`` is the largest position per name and ``rate`` the largest trade
    per decision, both as shares of the name's daily volume; ``interval`` is
    the ticks between decisions and ``band`` the gap to target, as a share of
    ``size``, that it leaves alone.
    """

    __slots__ = ("_fields",)

    def __init__(self, kind: str, *, name: str, size: float, rate: float,
                 interval: int, band: float, **params: float) -> None:
        if kind not in _KIND_PARAMS:
            raise ValidationError(
                f"unknown participant kind {kind!r}: one of "
                f"{', '.join(_KIND_PARAMS)}")
        if not isinstance(name, str) or not name or any(c.isspace() for c in name):
            raise ValidationError(
                f"a participant's name is a non-empty string with no spaces, "
                f"got {name!r}")
        wanted = dict(_KIND_PARAMS[kind])
        if set(params) != set(wanted):
            raise ValidationError(
                f"a {kind} participant takes {', '.join(wanted)}; got "
                f"{', '.join(sorted(params)) or 'none'}")
        fields: dict[str, Any] = {"kind": kind, "name": name}
        for key, value, whole in (("size", size, False), ("rate", rate, False),
                                  ("interval", interval, True),
                                  ("band", band, False),
                                  *((k, params[k], w) for k, w in _KIND_PARAMS[kind])):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError(f"{name}: {key} must be a number, got {value!r}")
            value = float(value)
            if not math.isfinite(value) or value < 0:
                raise ValidationError(f"{name}: {key} must be finite and not negative")
            if whole and value != int(value):
                raise ValidationError(f"{name}: {key} must be a whole number, got {value}")
            fields[key] = int(value) if whole else value
        if fields["size"] <= 0 or fields["rate"] <= 0 or fields["interval"] < 1:
            raise ValidationError(f"{name}: size and rate must be above zero, interval at least 1")
        if fields["band"] >= 1:
            raise ValidationError(f"{name}: band is a share of size, below 1")
        if kind in ("trend", "reversion") and not 1 <= fields["lookback"] <= 60:
            raise ValidationError(f"{name}: lookback must be 1 to 60 sessions")
        if kind in ("trend", "reversion", "liquidity") and fields["scale"] <= 0:
            raise ValidationError(f"{name}: scale must be above zero")
        if kind == "liquidity" and (fields["half_life"] <= 0
                                    or fields["vix_calm"] >= fields["vix_stress"]):
            raise ValidationError(
                f"{name}: half_life must be above zero and vix_calm below vix_stress")
        if kind == "detector" and (
                fields["memory"] <= 0 or fields["max_spread"] <= 0 or not 1 <= fields["bucket"] <= 390
                or fields["lead"] + fields["hold"] >= 390 - fields["bucket"]):
            raise ValidationError(
                f"{name}: memory must be above zero, bucket 1 to 390 ticks, and "
                "lead and hold together shorter than a session less one bucket")
        if fields["interval"] > 390:
            raise ValidationError(f"{name}: interval is at most 390 ticks")
        object.__setattr__(self, "_fields", fields)

    def __setattr__(self, key: str, value: Any) -> None:
        raise AttributeError("a Participant is immutable")

    def __getattr__(self, key: str) -> Any:
        try:
            return object.__getattribute__(self, "_fields")[key]
        except KeyError:
            raise AttributeError(key) from None

    @classmethod
    def trend(cls, *, name: str = "trend", size: float = 0.01, rate: float = 0.003,
              interval: int = 30, band: float = 0.3, lookback: int = 5,
              scale: float = 1.0) -> "Participant":
        """Piles into moves over ``lookback`` sessions."""
        return cls("trend", name=name, size=size, rate=rate, interval=interval,
                   band=band, lookback=lookback, scale=scale)

    @classmethod
    def reversion(cls, *, name: str = "reversion", size: float = 0.01,
                  rate: float = 0.003, interval: int = 30, band: float = 0.3,
                  lookback: int = 1, scale: float = 1.0) -> "Participant":
        """Trades against moves over ``lookback`` sessions."""
        return cls("reversion", name=name, size=size, rate=rate,
                   interval=interval, band=band, lookback=lookback, scale=scale)

    @classmethod
    def liquidity(cls, *, name: str = "liquidity", size: float = 0.005,
                  rate: float = 0.0015, interval: int = 30, band: float = 0.3,
                  half_life: float = 60.0, scale: float = 1.0,
                  vix_calm: float = 15.0, vix_stress: float = 35.0) -> "Participant":
        """Leans against short moves while calm; withdraws as the VIX rises."""
        return cls("liquidity", name=name, size=size, rate=rate,
                   interval=interval, band=band, half_life=half_life,
                   scale=scale, vix_calm=vix_calm, vix_stress=vix_stress)

    @classmethod
    def detector(cls, *, name: str = "detector", size: float = 0.02,
                 rate: float = 0.002, interval: int = 5, band: float = 0.1,
                 memory: float = 1.0, bucket: int = 1, lead: int = 120,
                 hold: int = 60, max_spread: float = 0.05) -> "Participant":
        """Learns the agents' flow by minute of the session and trades ahead
        of it, adding to a position only where the quoted spread is at most
        ``max_spread`` of the name's daily sigma."""
        return cls("detector", name=name, size=size, rate=rate,
                   interval=interval, band=band, memory=memory, bucket=bucket,
                   lead=lead, hold=hold, max_spread=max_spread)

    def as_dict(self) -> dict[str, Any]:
        """Every field, in a fixed order: kind, name, the four common ones,
        then the kind's own."""
        f = self._fields
        out = {"kind": f["kind"], "name": f["name"]}
        out.update({k: f[k] for k in _COMMON})
        out.update({k: f[k] for k, _ in _KIND_PARAMS[f["kind"]]})
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Participant":
        d = dict(data)
        kind = d.pop("kind", None)
        return cls(kind, **d)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Participant) and self.as_dict() == other.as_dict()

    def __hash__(self) -> int:
        return hash(json.dumps(self.as_dict(), sort_keys=True))

    def __repr__(self) -> str:
        f = self.as_dict()
        rest = ", ".join(f"{k}={v!r}" for k, v in f.items() if k != "kind")
        return f"Participant.{f['kind']}({rest})"


class Population:
    """A declarative, fingerprinted set of background traders.

    Pass one as ``population=`` to :func:`tradefloor.evaluate`,
    :class:`tradefloor.World` or :class:`tradefloor.Engine` to run in
    POPULATED mode; leave it out for ISOLATED mode, which is unchanged.
    Populated results are reproducible, but strategies in them no longer
    face identical markets: see :mod:`tradefloor.population`.

    ``Population.standard()`` is the shipped population, one participant of
    each kind. A custom one is a list of :class:`Participant`:

    ```python
    from tradefloor.population import Participant
    crowd = tf.Population([Participant.trend(size=0.02),
                           Participant.detector()], name="crowd")
    ```

    ``name`` is a label for people; :attr:`fingerprint` covers the
    participants only, so two populations with the same participants are
    the same market whatever they are called.
    """

    __slots__ = ("_participants", "_name")

    def __init__(self, participants: Iterable[Participant], *, name: str = "custom") -> None:
        items = tuple(participants)
        if not items:
            raise ValidationError("a Population needs at least one participant")
        for p in items:
            if not isinstance(p, Participant):
                raise ValidationError(
                    f"a Population takes Participant objects, got {type(p).__name__}")
        names = [p.name for p in items]
        if len(set(names)) != len(names):
            raise ValidationError(f"participant names must be unique, got {names}")
        if not isinstance(name, str) or not name:
            raise ValidationError("a Population's name is a non-empty string")
        object.__setattr__(self, "_participants", items)
        object.__setattr__(self, "_name", name)

    def __setattr__(self, key: str, value: Any) -> None:
        raise AttributeError("a Population is immutable")

    @property
    def participants(self) -> tuple[Participant, ...]:
        return self._participants

    @property
    def name(self) -> str:
        return self._name

    @classmethod
    def standard(cls) -> "Population":
        """The shipped population: one trend follower (five-day lookback),
        one mean reverter (one-day), one liquidity provider and one flow
        detector, at the sizes :class:`Participant`'s constructors default
        to."""
        return cls([Participant.trend(), Participant.reversion(),
                    Participant.liquidity(), Participant.detector()],
                   name="standard")

    @classmethod
    def named(cls, name: str) -> "Population":
        """A shipped population by name. ``"standard"`` is the one there is."""
        if name == "standard":
            return cls.standard()
        raise ValidationError(f"no shipped population named {name!r}; there is 'standard'")

    def as_dict(self) -> dict[str, Any]:
        """The population as plain data: what a manifest carries."""
        return {"version": POPULATION_VERSION, "name": self._name,
                "participants": [p.as_dict() for p in self._participants]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Population":
        if data.get("version") != POPULATION_VERSION:
            raise ValidationError(
                f"this population was written at version {data.get('version')!r}, "
                f"and this build reads {POPULATION_VERSION}")
        return cls([Participant.from_dict(p) for p in data["participants"]],
                   name=data.get("name", "custom"))

    @property
    def fingerprint(self) -> str:
        """``pop-`` and twelve hex digits of a sha256 over the layout version
        and the participants in order, as canonical JSON."""
        doc = {"version": POPULATION_VERSION,
               "participants": [p.as_dict() for p in self._participants]}
        raw = json.dumps(doc, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode()
        return "pop-" + hashlib.sha256(raw).hexdigest()[:12]

    def _engine_spec(self) -> dict[str, Any]:
        """What :class:`tradefloor.Engine` reads from ``population=``."""
        return {"fingerprint": self.fingerprint,
                "participants": [p.as_dict() for p in self._participants]}

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Population) and other.fingerprint == self.fingerprint

    def __hash__(self) -> int:
        return hash(self.fingerprint)

    def __repr__(self) -> str:
        kinds = ", ".join(f"{p.name}:{p.kind}" for p in self._participants)
        return f"Population({self._name!r}, {self.fingerprint}, [{kinds}])"


def check(population: Any) -> "Population | None":
    """``None`` or a :class:`Population`, refusing anything else by name."""
    if population is None or isinstance(population, Population):
        return population
    raise ValidationError(
        "population= takes a tradefloor.Population (tf.Population.standard(), "
        f"say) or None, got {type(population).__name__}")
