"""The draw stream as an addressable, editable object.

Every random number the engine consumes has an address: the stream it
came from, whether it was a uniform or a normal, and how many draws of
that kind the stream had taken since the engine was built. This module
names those addresses, installs substitutions at them, reads the log of
what a stream delivered, and runs one day under a set of substitutions
in a fork so the parent is untouched.

Nothing here changes a trajectory on its own. An engine with an empty
overlay produces the known-answer digest; a patched draw costs the same
generator step as an unpatched one, so every address after it keeps its
value and ``draws_by_stream`` reads the same with and without the
overlay. The Rust side (``rust/src/rng.rs``) states the contract; this
module is the Python surface over it.

## Addresses

``DrawAddress(stream, kind, index)``. Streams are the seven the engine
derives from its seed: ``market``, ``economy``, ``external``, ``jumps``,
``volume``, ``news`` and ``volume_idio``. ``kind`` is ``"uniform"`` or
``"normal"``. Normals index by normal count and uniforms by uniform
count, counted apart: Box-Muller takes two uniforms from the generator
for every two normals it returns and caches the second as a spare, so
counting the underlying uniforms would leave every second normal without
an address of its own. The two uniforms Box-Muller consumes are not on
the uniform index.

## What an address means across days

The index runs from engine construction, so the same address names the
same draw in every fork of the same run and in any restore of a snapshot
that carries the counts. A snapshot written before draw addressing
restores with counts of zero: its addresses restart at the restore, and a
patch written against the original run does not land where it did.

## Sites

The engine tags each group of draws with the call site that takes them.
:data:`SITES` lists them in schedule order per stream; the test
``test_the_site_sequence_is_the_schedule`` walks one day and asserts the
sequence, so the list is the draw schedule made legible rather than a
description of it.
"""
from __future__ import annotations

import struct
from typing import Any, NamedTuple, Sequence

from ._core import Engine

STREAMS = ("market", "economy", "external", "jumps", "volume", "news",
           "volume_idio")
KINDS = ("uniform", "normal")

#: The call sites, in the order each stream meets them. The market
#: stream's order is per open tick; the others are per day. The economy
#: chain's sites are groups: the daily chain, the cycle roll and the
#: central bank each take a state-dependent number of draws, so their
#: draws are addressed by index and named by group.
SITES = {
    "market": ("market_factor_z", "sector_z", "factor_idio_z", "stash_u",
               "settle_u"),
    "jumps": ("jump_market_u", "jump_market_z", "jump_company_u",
              "jump_company_z"),
    "volume": ("volume_z",),
    "volume_idio": ("volume_idio_z",),
    "news": ("news_u", "news_z"),
    "economy": ("economy_daily", "economy_cycle", "central_bank"),
    "external": ("external",),
}


class DrawAddress(NamedTuple):
    """One draw: the stream, the kind and the index of that kind."""

    stream: str
    kind: str
    index: int

    def check(self) -> "DrawAddress":
        if self.stream not in STREAMS:
            raise ValueError(
                f"unknown stream {self.stream!r}; one of {', '.join(STREAMS)}")
        if self.kind not in KINDS:
            raise ValueError(
                f"unknown draw kind {self.kind!r}; uniform or normal")
        if self.index < 0:
            raise ValueError(f"a draw index is non-negative, got {self.index}")
        return self


class Patch(NamedTuple):
    """One substitution: the address and the value the consumer receives.

    A uniform must lie in ``[0, 1)`` to be a uniform the consumer can
    read; a normal takes any finite value. Neither is enforced here, so a
    caller that wants an event to fire can set a uniform to ``0.0`` and one
    that wants it unfired can set ``1.0``, and the comparison the consumer
    makes is what decides (``World.unfire`` documents the jump site's).
    """

    address: DrawAddress
    value: float


class LoggedDraw(NamedTuple):
    """One recorded draw: what the consumer received, when and where."""

    address: DrawAddress
    value: float
    day: int
    site: str
    tag: int


def patch_draws(engine: Engine, patches: Sequence[Patch]) -> None:
    """Install ``patches`` on ``engine``.

    Each generator still advances at every address; only the value the
    consumer receives changes, so the draw counts and every unpatched
    address are identical with and without the overlay. A patch at an
    address the engine has already passed is kept and never lands; the
    caller reads ``engine.stream_positions()`` to see where each stream is.
    """
    engine.patch_draws([(p.address.check().stream, p.address.kind,
                         int(p.address.index), float(p.value))
                        for p in patches])


def draw_log(engine: Engine, stream: str, from_day: int,
             to_day: int) -> list[LoggedDraw]:
    """The draws ``stream`` delivered on days ``from_day..to_day``.

    Logging is opt-in: ``engine.trace_draws(stream, from_day, to_day)``
    must have been called before those days ran, or the log is empty.
    ``run_day_with`` traces the day it runs. The value logged is the one
    the consumer received, so a patched draw shows its patched value.
    """
    return [LoggedDraw(DrawAddress(*addr), value, day, site, tag)
            for addr, value, day, site, tag
            in engine.draw_log(stream, from_day, to_day)]


def run_day_with(engine: Engine, day: int, patches: Sequence[Patch],
                 *, hour: int = 9, minute: int = 30, day_of_week: int = 3,
                 ticks_per_day: int = 390, volatility: float = 1.0,
                 streams: Sequence[str] = STREAMS) -> "DayResult":
    """Fork ``engine``, install ``patches``, run ``day`` once, and return
    the day's closes and its ground-truth attribution.

    The primitive every other instrument in this module uses. The fork is
    a copy, so the parent is untouched whatever the patches do; the fork's
    draw log is traced on every stream in ``streams`` for that day, so the
    result carries what each stream delivered.

    ``day`` numbers the day the way ``Engine.run_days(first_day=...)``
    does. The fork opens the market at that day, runs ``ticks_per_day``
    ticks, records the day, and closes it, which advances the macro chain.

    What this cannot tell a caller: whether the closes it returns are
    reachable by any draw vector at all. A circuit breaker or the order
    book clamps the price, and a patched draw large enough to cross a
    clamp produces the clamped close, not a proportional one. Phase 4's
    solver reports the binding clamp; this function reports the close.
    """
    forks = engine.fork(1)
    fork = forks[0]
    patch_draws(fork, patches)
    for stream in streams:
        fork.trace_draws(stream, day, day)
    fork.run_days(1, hour=hour, minute=minute, day_of_week=day_of_week,
                  ticks_per_day=ticks_per_day, volatility=volatility,
                  record=True, first_day=day)
    raw = fork.prices()
    closes = dict(zip(fork.tickers,
                      struct.unpack(f"<{len(raw) // 8}d", raw)))
    truth = fork.truth(day=day)
    return DayResult(day=day, engine=fork, closes=closes, truth=truth,
                     patches=tuple(patches))


class DayResult(NamedTuple):
    """One day run under a set of patches, in a fork."""

    day: int
    engine: Engine
    closes: dict[str, float]
    truth: Any
    patches: tuple[Patch, ...]

    def log(self, stream: str) -> list[LoggedDraw]:
        return draw_log(self.engine, stream, self.day, self.day)


def market_day_layout(engine: Engine, day: int) -> dict[int, tuple[int, int, int]]:
    """Where each active company's market-stream normals sit on ``day``.

    Returns ``{company_index: (first, stride, ticks)}``: the company's
    normal at tick ``t`` of that day has index ``first + t * stride``.
    Derived from the day mark the engine records at the open (the stream
    positions, the active roster and the sector count) and the per-tick
    schedule the site sequence pins: one normal for the market factor, one
    per sector, then one per active company. The draw log is the check on
    the arithmetic and ``test_the_layout_matches_the_log`` makes it.
    """
    layout = engine.market_day_layout(day)
    if layout is None:
        raise ValueError(f"day {day} was never opened on this engine")
    return {company: (first, stride, ticks)
            for company, first, stride, ticks in layout}


#: The uniform that keeps a jump from firing. ``Engine::apply_jumps``
#: fires the market jump when its uniform is below the day's intensity,
#: and a uniform lies in ``[0, 1)``, so ``1.0`` is never below an
#: intensity of at most one. ``World.unfire`` documents the comparison.
NO_FIRE = 1.0


def surgery_patches(seed: int, stream: str, surgery_seed: int,
                    addresses: Sequence[DrawAddress]) -> list[Patch]:
    """Patches that re-randomise ``addresses`` of ``stream``.

    One value per address, in order, from a generator derived from the
    root ``seed``, the stream and ``surgery_seed`` per the surgery
    derivation contract in ``rust/src/rng.rs`` (``GameRng::surgery``):
    the stream's own mix, mixed again with the surgery seed and a tag,
    on a sequence no stream uses. Uniforms and normals are drawn from it
    in the order the addresses ask, so the same addresses under the same
    seeds give the same patches, and a different order gives different
    ones.

    The addresses are the caller's to know. ``World.window`` finds them
    by running the days once in a fork with the stream traced; this
    function only turns them into values.
    """
    checked = [DrawAddress(*a).check() for a in addresses]
    for a in checked:
        if a.stream != stream:
            raise ValueError(
                f"a surgery of {stream} cannot patch {a.stream}; one "
                f"stream per surgery, so the record says what moved")
    values = Engine.surgery_draws(int(seed), stream, int(surgery_seed),
                                  [a.kind for a in checked])
    return [Patch(a, v) for a, v in zip(checked, values)]


__all__ = ["DrawAddress", "Patch", "LoggedDraw", "DayResult", "SITES",
           "STREAMS", "KINDS", "NO_FIRE", "patch_draws", "draw_log",
           "run_day_with", "market_day_layout", "surgery_patches"]
