"""Which measurements a dial can move: the engine side of a row-reach map.

A screen that re-measures every row for every candidate pays for rows the
candidate's changed dials cannot touch. This module says, for each dial,
through which CHANNEL it acts, and for each measurement protocol, which
channels it opens. A dial reaches a protocol when the protocol opens the
dial's channel. A dial with no entry here acts on every simulated market,
which is the safe default: leaving a dial out costs measurement, never a
wrong reading.

Channels
--------

``market``
    Every simulated market. Every protocol opens it, so a ``market`` dial
    reaches everything.
``order_flow``
    Order volumes in a tick: flow sent with ``flow_per_tick`` or
    ``tick(order_flow=...)``, and agents' fills where fill impact routes
    through it. An untraded run leaves the volumes empty, and at zero
    volume the imbalance these dials scale is the literal 0.0.
``agent_book``
    The agent-facing book: depth, refill and resting orders that only an
    agent's order reads.
``news_events``
    News events delivered to the engine. The weights here multiply an event
    impact; with no event the accumulator is untouched. The endogenous news
    variance on pt-v20 is a variance term, not an event, and does not read
    them.

Every entry is a claim about the code, with its reason. `probe` checks the
part of the claim a short run can falsify: a dial in a narrow channel must
leave an untraded, newsless run identical to the last bit. A probe that
passes is not proof, because a dial could wait on a condition the probe
never reaches; the full screen's verify mode is the check that covers the
rest (`result_cache.resolve(..., verify=True)`).

New switches register here when they are added, with the channel their
zero branch leaves untouched.
"""
from __future__ import annotations

import hashlib
import json
from typing import Iterable, Mapping

CHANNELS = ("market", "order_flow", "agent_book", "news_events")

#: dial -> (channel, reason). Absent means "market".
DIALS: dict[str, tuple[str, str]] = {
    "order_flow_coefficient": (
        "order_flow", "scales the order imbalance, which is 0.0 without order volumes"),
    "order_flow_impact_law": (
        "order_flow", "switches the imbalance law; at zero volume both laws give 0.0"),
    "order_flow_depth_law": (
        "order_flow", "scales the imbalance by reference depth; zero volume stays 0.0"),
    "informed_flow_fraction": (
        "order_flow", "splits order volume into informed and noise parts"),
    "fill_impact_coefficient": (
        "order_flow", "prices an agent's fills into the order imbalance"),
    "book_depth_coefficient": (
        "agent_book", "depth of the agent-facing book, read when an agent order meets it"),
    "book_depth_exponent": (
        "agent_book", "price-for-size law of the agent-facing book"),
    "book_depth_reach": (
        "agent_book", "how far the agent-facing book's depth reaches"),
    "book_shared": (
        "agent_book", "whether agents share consumed depth"),
    "book_refill_half_life": (
        "agent_book", "refill of depth agents consumed"),
    "book_resting": (
        "agent_book", "whether an agent's limit order rests in the book"),
    "news_market_weight": (
        "news_events", "weight of a market-wide news event's impact"),
    "news_sector_weight": (
        "news_events", "weight of a sector news event's impact"),
    "news_peer_weight": (
        "news_events", "transfer of a peer company's good news"),
    "news_peer_weight_down": (
        "news_events", "transfer of a peer company's bad news"),
    "news_peer_vix_coupling": (
        "news_events", "crisis scaling of the peer transfer"),
}

#: The gate tools' measurement kinds (`gate_pick.one`). Each is an untraded
#: run with no news events, so each opens `market` alone. The held VIX and
#: the driven window are scenarios, which are inputs to the market and open
#: no other channel.
GATE_PROTOCOLS: dict[str, frozenset[str]] = {
    kind: frozenset({"market"}) for kind in
    ("p252", "p504", "vix5", "vix45", "vix65", "driven", "ho_seeds", "ho_universe")
}


def channel_of(dial: str) -> str:
    return DIALS.get(dial, ("market", ""))[0]


def reason_of(dial: str) -> str:
    return DIALS.get(dial, ("market", "no entry: assumed to act on every market"))[1]


def changed_dials(candidate: Mapping[str, float], baseline: Mapping[str, float]) -> list[str]:
    """Dials whose values differ, from two `ModelParams.to_dict()` mappings.

    A dial present in one only counts as changed unless the side that has it
    holds 0.0, which is how a new silent switch at zero appears beside an
    older build's dictionary.
    """
    out = []
    for k in sorted(set(candidate) | set(baseline)):
        if k == "name":
            continue
        a, b = candidate.get(k), baseline.get(k)
        if a is None or b is None:
            if (a if a is not None else b) != 0.0:
                out.append(k)
        elif a != b:
            out.append(k)
    return out


def reachable(dials: Iterable[str], protocols: Mapping[str, Iterable[str]]) -> set[str]:
    """The protocols any of `dials` can move."""
    chans = {channel_of(d) for d in dials}
    out = set()
    for name, opens in protocols.items():
        opened = set(opens) | {"market"}
        if chans & opened:
            out.add(name)
    return out


def probe(dial: str, value: float, *, base: str = "pt-v20", days: int = 120,
          names: int = 6, seeds: Iterable[int] = (1, 2)) -> bool:
    """True when the dial at `value` leaves an untraded run's panel identical.

    Untraded and newsless: `facts.measure` on a small universe. Compares every
    numeric reading of the panel exactly. For a dial in a narrow channel this
    must be True; for a `market` dial it says nothing (a dial may wait on a
    crisis the run never reaches).
    """
    import tradefloor as tf  # noqa: PLC0415

    universe = tf.Universe.random(names, seed=111)

    def digest(model) -> str:
        out = []
        for s in seeds:
            f = tf.facts.measure(seed=s, universe=universe, days=days, model=model)
            out.append({k: v for k, v in sorted(f.items())
                        if isinstance(v, (int, float)) or v is None})
        return hashlib.sha256(json.dumps(out, default=repr).encode()).hexdigest()

    ref = digest(tf.ModelParams.from_preset(base))
    return digest(tf.ModelParams.from_preset_unchecked(base, **{dial: value})) == ref
