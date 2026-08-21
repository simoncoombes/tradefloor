"""Fork a simulation mid-flight and run both futures.

Run to day sixty, then ask two questions of the same market: what happens under
a rate shock, and what happens without one. Everything before the fork is
identical — not statistically similar, identical — because both branches are
the same seed replayed to the same point.

```python
mark = pt.Checkpoint.of(engine)          # after sixty days
calm, hiked = mark.branch(2)
run_scenario(shock, engine=hiked, ...)
```

That is a counterfactual real markets cannot offer, and it is a different one
from the two already here. `tca.analyse` asks what your trading cost against a
world where you did not trade; `scenario.compare` asks what a macro path did.
This asks what happens NEXT, from a state you have already reached and want to
keep.

## Two ways to fork, and they are for different things

:func:`branch` forks in memory using the engine's own state snapshot: every
column plus the generator position, restored in constant time. Measured on a
sixty-day, forty-instrument run: **under a millisecond**, against 2,740 ms to
replay the same history.

:class:`Checkpoint` forks by replaying the order log. Slower by three orders
of magnitude, and the one you want when the fork has to OUTLIVE the process.
A snapshot is a state; a log is a history. A published result cites the
history, because that is what another person can re-run.

Use `branch` for an experiment inside one script, and `Checkpoint` for
anything you save, send or cite.

## Restoring a Checkpoint is a replay, and costs what the original run cost

There is no O(1) state snapshot. A checkpoint is the seed, the universe and
the order log; resuming means running the log again. Measured on forty
instruments:

    5 days     run 0.22s    restore 0.23s
    20 days    run 0.89s    restore 0.82s
    60 days    run 2.63s    restore 2.74s

So restoring costs about what the original run cost, and branching to `n`
futures costs `n` times that. Stated plainly because the word "checkpoint"
usually implies cheap restoration and here it does not.

What you get for it is that the checkpoint is DATA — a few kilobytes of JSON,
about 200 bytes per day — rather than an opaque memory image. It survives the
process, the version and the machine, which an in-memory snapshot of a PCG32
state and a maker inventory would not.

## Why not snapshot the engine state directly

Because it is a second thing to keep correct, and it has already been wrong.

The engine holds the generator position, a cached Box-Muller spare,
per-company GARCH variance, maker inventories, the mispricing carry -- and,
beside all of those, per-DAY accumulators. The snapshot carried the columns
and the generator and missed the accumulators, so a fork taken BETWEEN two
sessions of the same day lost the day's attribution and the market-open flag.
It then re-opened the day on its next session, re-anchored `previous_close`,
and priced differently from the parent it was supposed to be a copy of. It
also closed on a different GARCH variance, which does not show up in today's
prices at all -- only in tomorrow's.

Every test forked on a day boundary, where there is no per-day state to lose,
so nothing caught it. That is the shape of the hazard: a snapshot that misses
one field restores a market that looks right and diverges later.

The order log has none of this exposure. It is already the reproduction
mechanism, already tested, and already the thing a published result cites --
which is why it, not the snapshot, is what :class:`Checkpoint` serialises.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from ._core import Engine, Instrument, Macro, ValidationError

CHECKPOINT_SCHEMA = 1


class Checkpoint:
    """A point in a simulation you can return to, as data."""

    __slots__ = ("seed", "universe", "log", "macro", "label")

    def __init__(self, *, seed: int, universe: Sequence[Instrument],
                 log: Sequence[dict], macro: Macro | None = None,
                 label: str = "") -> None:
        self.seed = int(seed)
        self.universe = list(universe)
        self.log = [dict(entry) for entry in log]
        self.macro = macro
        self.label = label

    @property
    def universe_fingerprint(self) -> str:
        """Identity of the roster this checkpoint belongs to."""
        from . import Universe

        return Universe(self.universe).fingerprint

    @classmethod
    def of(cls, engine: Engine, *, universe: Sequence[Instrument], seed: int,
           macro: Macro | None = None, label: str = "") -> "Checkpoint":
        """Capture an engine's history.

        ``universe`` and ``seed`` are passed rather than read off the engine
        because it does not carry them: an engine is built FROM a universe and
        a seed and keeps neither. Requiring them here is honest about that,
        and it means a checkpoint records the inputs it will need rather than
        discovering at resume time that it cannot reproduce anything.
        """
        return cls(seed=seed, universe=universe, log=engine.order_log,
                   macro=macro, label=label)

    def resume(self, *, universe: Sequence[Instrument] | None = None) -> Engine:
        """A fresh engine at this exact state.

        Replays the log, so it costs roughly what reaching the state cost the
        first time.

        Pass ``universe`` to resume onto a roster you already hold rather than
        the one carried in the checkpoint. It is checked against the recorded
        fingerprint and refused on a mismatch -- tickers are generated
        positionally, so two universes can share every name and no
        fundamentals, and comparing names would be checking almost nothing.
        """
        from . import Universe
        from .replay import replay

        roster = self.universe if universe is None else list(universe)
        if universe is not None:
            supplied = Universe(roster).fingerprint
            if supplied != self.universe_fingerprint:
                raise ValidationError(
                    f"universe fingerprint {supplied[:12]}... does not match "
                    f"this checkpoint's {self.universe_fingerprint[:12]}.... "
                    "Restoring onto a different roster gives right prices and "
                    "wrong fair values."
                )
        return replay(self.log, seed=self.seed, universe=roster,
                      macro=self.macro)

    def branch(self, count: int = 2) -> list[Engine]:
        """``count`` independent engines, all at this state.

        Independent in the strong sense: they share no memory, so diverging
        one cannot perturb another. That is what makes a fork a controlled
        experiment rather than two runs that happen to start similarly.
        """
        if count < 1:
            raise ValidationError(f"count must be at least 1, got {count}")
        return [self.resume() for _ in range(count)]

    # -- serialisation ----------------------------------------------------

    def to_json(self) -> str:
        """The whole checkpoint as JSON: seed, universe, log and macro.

        Everything needed to reproduce the state, so a published result can
        ship the point its analysis starts from rather than a description of
        how to get there.
        """
        from . import Universe

        payload: dict[str, Any] = {
            "schema": CHECKPOINT_SCHEMA,
            "label": self.label,
            "seed": self.seed,
            "universe": json.loads(Universe(self.universe).to_json()),
            "universe_fingerprint": self.universe_fingerprint,
            "log": self.log,
        }
        if self.macro is not None:
            payload["macro"] = {
                "vix": self.macro.vix,
                "federal_funds_rate": self.macro.federal_funds_rate,
                "corporate_bond_yield": self.macro.corporate_bond_yield,
                "inflation_rate": self.macro.inflation_rate,
                "qe_pe_boost": self.macro.qe_pe_boost,
                "fear_greed_index": self.macro.fear_greed_index,
                "cycle": self.macro.cycle,
            }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> "Checkpoint":
        from . import Universe

        payload = json.loads(text)
        schema = payload.get("schema", 0)
        if schema > CHECKPOINT_SCHEMA:
            raise ValidationError(
                f"checkpoint schema {schema} is newer than this version "
                "understands. Upgrade pretium rather than reading it partially."
            )
        universe = Universe.from_json(json.dumps(payload["universe"]))
        recorded = payload.get("universe_fingerprint")
        if recorded is not None and recorded != universe.fingerprint:
            # The universe travelled and arrived changed. Restoring anyway
            # would give a market with the right prices and the wrong fair
            # values -- plausible in every visible way, and wrong in the one
            # that drives everything.
            raise ValidationError(
                "the universe in this checkpoint does not match its recorded "
                f"fingerprint ({recorded[:12]}... vs "
                f"{universe.fingerprint[:12]}...). The roster was edited or "
                "rebuilt by a different generator version; the checkpoint no "
                "longer describes the market it came from."
            )
        macro = None
        if "macro" in payload:
            macro = Macro(**payload["macro"])
        return cls(
            seed=payload["seed"],
            universe=universe,
            log=payload["log"],
            macro=macro,
            label=payload.get("label", ""),
        )

    def __len__(self) -> int:
        """Entries in the log, not days. The two differ once anything is
        recorded per session rather than per day."""
        return len(self.log)

    def __repr__(self) -> str:
        label = f"{self.label!r}, " if self.label else ""
        return (f"Checkpoint({label}seed={self.seed}, "
                f"{len(self.universe)} instruments, {len(self.log)} entries)")


def branch(
    engine: Engine,
    count: int = 2,
    *,
    universe: Sequence[Instrument],
    seed: int,
    macro: Macro | None = None,
) -> list[Engine]:
    """Fork a running engine in constant time.

    Copies the engine's complete state -- every column plus the generator
    position -- into `count` fresh engines. Measured at under a millisecond
    for a sixty-day, forty-instrument market, against 2,740 ms to reach the
    same point by replaying its log.

    The branches share no memory, so driving one cannot perturb another. That
    is what makes a fork a controlled experiment rather than two runs that
    started similarly.

    ``universe`` and ``seed`` are required because an engine carries neither:
    it is built FROM them and keeps them nowhere. The seed barely matters here
    -- the generator position is restored over whatever it produced -- but the
    universe does, because the columns are positional and a mismatched roster
    would attach every value to the wrong instrument. The engine refuses that
    rather than doing it.

    For a fork that must survive the process, use :class:`Checkpoint`.
    """
    if count < 1:
        raise ValidationError(f"count must be at least 1, got {count}")
    snapshot = engine.state_snapshot()
    out: list[Engine] = []
    for _ in range(count):
        fresh = Engine(seed=seed, universe=universe, macro_state=macro)
        fresh.restore_state(snapshot)
        out.append(fresh)
    return out
