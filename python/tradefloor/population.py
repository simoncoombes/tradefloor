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
the name's daily volume. Five kinds:

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
- ``crowd`` trades a ranked signal, the one
  :class:`tradefloor.baselines.Momentum` and
  :class:`tradefloor.baselines.MeanReversion` trade: the simple return over
  ``lookback`` open ticks (390 is one session), ``"momentum"`` or
  ``"reversal"``, long the first ``top_k`` names and short the last
  ``top_k``, each at ``size``. A held name stays until it slips ``buffer``
  ranks past the edge. It decides where the session's tick count, modulo
  ``interval``, equals ``offset``: a crowd that looks every five ticks
  reaches a new loser before a rule that decides every sixty-five, and one
  that decides near the close is in before a rule that rebalances at the
  next open. So it takes the mispricing first and your rule trades at the
  price it left (signal competition, the decay of a published anomaly).
  With ``stop`` above zero it sells out when its positions' price P&L has
  fallen ``stop`` of its full book's gross from its best, then takes back
  ``recover`` of its book each session; several crowd participants with
  different limits unwind one after another (a crowded exit).

Every participant decides every ``interval`` ticks (staggered, or at a
crowd's ``offset``), leaves gaps
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
    "crowd": (("lookback", True), ("offset", True), ("top_k", True), ("buffer", True),
              ("stop", False), ("recover", False)),
}

#: The signals a crowd can trade, as the ranked trend rules name them.
CROWD_SIGNALS = ("momentum", "reversal")

_COMMON = ("size", "rate", "interval", "band")

#: The shipped populations, by the name :meth:`Population.named` takes.
SHIPPED = ("standard", "crowded")

#: What populated mode was measured to do, with the checks in
#: ``tools/calibration/population_checks.py`` (``ac3``, ``ac4``, ``cx`` and
#: ``runtime``, each paired on the seed). The MCP server quotes it in every
#: populated result, so a figure moves here and nowhere else.
#:
#: The figures were read with :meth:`Population.crowded` on R20M, the
#: candidate vector of 92 dials moved off pt-v20 that
#: ``tests/test_state_schema_r21.py`` lists, run on the 0.9.1 engine, not on
#: a shipped preset. They are local runs: ac3 and cx on seeds 92001 to
#: 92030 (20 names, 60 sessions), ac4 on seeds 201 to 230, the meta-order
#: rows AC1 and AC2 on 30 seeds by 4 names, ``return_acf1_shift`` on 40
#: names, 3 seeds of 252 sessions. They are re-measured on the preset that
#: ships with populated mode.
#:
#: - ``model``, ``population``, ``seeds``: what the figures were read on.
#: - ``edge_decay``: the ac3 finding, that an edge decays as other traders
#:   trade its signal.
#: - ``programme_cost_excess``: ac4, what a predictable programme pays in
#:   populated mode over what it pays in isolated mode, less one.
#:   ``programme_cost_excess_reported`` is the same excess van Kervel and
#:   Menkveld (2019) report from real markets; the gap is that impact here
#:   is mostly transient, so there is less to trade ahead of.
#: - ``crowded_exit``: the cx finding on a crowd's loss limits.
#: - ``return_acf1_shift``: populated less isolated ``return_acf1``.
#: - ``runtime_ratio``: populated run time over isolated.
#: - ``meta_order_rows``: AC1 (a 12-day programme's per-share cost over a
#:   1-day one's) and AC2 (one programme split over four labels against one
#:   label), populated, with their bands.
MEASURED: dict[str, Any] = {
    "model": "R20M, a candidate vector of 92 dials moved off pt-v20",
    "population": "crowded",
    "seeds": {"ac3": "92001-92030", "cx": "92001-92030", "ac4": "201-230",
              "ac1_ac2": "30 seeds by 4 names"},
    "method": "tools/calibration/population_checks.py",
    "edge_decay": "an edge decays as other traders trade its signal",
    "programme_cost_excess": 0.019,
    "programme_cost_excess_reported": 1.69,
    "programme_cost_source": "van Kervel and Menkveld (2019)",
    "crowded_exit": (
        "a crowded exit costs holders of the same signal on the day the "
        "crowd sells out, and the loss comes back over the following week"),
    "return_acf1_shift": 0.006,
    "runtime_ratio": 1.4,
    "meta_order_rows": {"ac1": 2.180, "ac1_band": (1.8, 5.0),
                        "ac2": 1.000, "ac2_band": (0.8, 1.25)},
}


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
        signal = params.pop("signal", None) if kind == "crowd" else None
        if kind == "crowd" and signal not in CROWD_SIGNALS:
            raise ValidationError(
                f"{name}: a crowd's signal is one of {', '.join(CROWD_SIGNALS)}, "
                f"got {signal!r}")
        if set(params) != set(wanted):
            raise ValidationError(
                f"a {kind} participant takes {', '.join(wanted)}; got "
                f"{', '.join(sorted(params)) or 'none'}")
        fields: dict[str, Any] = {"kind": kind, "name": name}
        if signal is not None:
            fields["signal"] = signal
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
        if kind == "crowd":
            if 390 % fields["interval"]:
                raise ValidationError(
                    f"{name}: a crowd's interval must divide the session's 390 ticks")
            if (fields["lookback"] < 1 or fields["lookback"] % fields["interval"]
                    or fields["lookback"] > 60 * 390):
                raise ValidationError(
                    f"{name}: a crowd's lookback is a whole number of intervals, "
                    "at most 60 sessions of 390 ticks")
            if fields["top_k"] < 1:
                raise ValidationError(f"{name}: top_k must be at least 1")
            if fields["offset"] >= fields["interval"]:
                raise ValidationError(f"{name}: offset must be below the interval")
            if not 0 < fields["recover"] <= 1:
                raise ValidationError(f"{name}: recover must be above 0 and at most 1")
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

    @classmethod
    def crowd(cls, *, signal: str, name: str = "crowd", size: float = 0.02,
              rate: float = 0.004, interval: int = 5, band: float = 0.1,
              lookback: int = 390, offset: int = 0, top_k: int = 5, buffer: int = 2,
              stop: float = 0.0, recover: float = 0.2) -> "Participant":
        """Trades a ranked signal: long the ``top_k`` names with the best
        ``signal`` reading and short the ``top_k`` with the worst, each at
        ``size`` of the name's daily volume. ``signal`` is ``"momentum"``
        (long the names that rose most over ``lookback`` open ticks) or
        ``"reversal"`` (long those that fell most), ranked exactly as
        :class:`tradefloor.baselines.Momentum` and
        :class:`tradefloor.baselines.MeanReversion` rank. A held name stays
        until it slips ``buffer`` ranks past the edge. It decides at the
        ticks where the session's tick count, modulo ``interval``, equals
        ``offset``; ``interval`` divides the session's 390 ticks and
        ``lookback`` is a whole number of intervals. With ``stop`` above
        zero it sells out when its positions' price P&L has fallen ``stop``
        of its full book's gross from its best, and takes back ``recover``
        of its book each session after."""
        return cls("crowd", name=name, size=size, rate=rate, interval=interval,
                   band=band, signal=signal, lookback=lookback, offset=offset,
                   top_k=top_k, buffer=buffer, stop=stop, recover=recover)

    def as_dict(self) -> dict[str, Any]:
        """Every field, in a fixed order: kind, name, the four common ones,
        then the kind's own (a crowd's signal first)."""
        f = self._fields
        out = {"kind": f["kind"], "name": f["name"]}
        out.update({k: f[k] for k in _COMMON})
        if "signal" in f:
            out["signal"] = f["signal"]
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
    each of the first four kinds; ``Population.crowded()`` adds crowds that
    trade the ranked rules' own signals. A custom one is a list of
    :class:`Participant`:

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
    def crowded(cls, *, reversal: float = 0.006, momentum: float = 0.01,
                members: int = 3, stop: float = 0.03,
                recover: float = 0.2, detectors: int = 5) -> "Population":
        """The standard population plus two crowds trading the ranked rules'
        own signals, each the long and short five of the roster, with a
        buffer of two ranks.

        - The reversal crowd is one participant trading the one-day reversal
          (a lookback of 390 open ticks) as statistical arbitrage does: it
          looks every fifteen ticks, so it reaches a new loser before a rule
          that decides every sixty-five.
        - The momentum crowd trades the five-day momentum (1,950 ticks) once
          a session, in the last fifteen ticks before the close, so a rule
          that rebalances at the next open meets the market after it. It is
          ``members`` participants sharing its size.

        ``reversal`` and ``momentum`` are each crowd's total size per name,
        as a share of daily volume. With ``stop`` above zero each carries a
        loss limit: the reversal crowd at ``stop``, the momentum members
        spread from half of ``stop`` to one and a half times it, so a loss
        that stops the first out can carry the others after it.

        Its other participants are the standard population's, with two
        changes. The trend follower decides as often as its five-day signal
        moves (every 130 ticks). And in place of
        one flow detector it holds ``detectors`` of them, identical (with
        five-tick buckets) and deciding on different ticks, competing to
        trade ahead of the same flow: high-frequency trading is several
        firms, and five is about as many as the flow of a predictable
        programme keeps profitable together."""
        if not isinstance(members, int) or members < 1:
            raise ValidationError("members is a whole number, at least 1")
        if not isinstance(detectors, int) or detectors < 1:
            raise ValidationError("detectors is a whole number, at least 1")
        crowd = []
        if reversal > 0:
            crowd.append(Participant.crowd(
                signal="reversal", name="reversal_crowd", size=reversal,
                rate=2 * reversal, interval=15, band=0.3, lookback=390,
                stop=stop, recover=recover))
        if momentum > 0:
            size = momentum / members
            for j in range(members):
                at = 0.5 + j / (members - 1) if members > 1 else 1.0
                crowd.append(Participant.crowd(
                    signal="momentum", name=f"momentum_crowd{j + 1}", size=size,
                    rate=2 * size, interval=390, offset=389 - 5 * (j % 3),
                    lookback=1950, stop=stop * at, recover=recover))
        # The standard population's trend follower deciding as often as a
        # five-day signal moves, every 130 ticks; its mean reverter and
        # liquidity provider as they are.
        base = [Participant.trend(interval=130), Participant.reversion(),
                Participant.liquidity()]
        hfts = [Participant.detector(name=f"detector{j + 1}", bucket=5)
                for j in range(detectors)]
        return cls([*base, *hfts, *crowd], name="crowded")

    @classmethod
    def named(cls, name: str) -> "Population":
        """A shipped population by name: one of :data:`SHIPPED`,
        ``"standard"`` or ``"crowded"``."""
        if name == "standard":
            return cls.standard()
        if name == "crowded":
            return cls.crowded()
        raise ValidationError(
            f"no shipped population named {name!r}; there are "
            + " and ".join(repr(n) for n in SHIPPED))

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
