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

## Restoring is a replay, and costs what the original run cost

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

Because it would be a second thing to keep correct. The engine holds the
generator position, a cached Box-Muller spare, per-company GARCH variance,
maker inventories and the mispricing carry; a snapshot that missed one field
would restore a market that looked right and diverged later. The order log is
already the reproduction mechanism, already tested, and already the thing a
published result cites.
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

    def resume(self) -> Engine:
        """A fresh engine at this exact state.

        Replays the log, so it costs roughly what reaching the state cost the
        first time.
        """
        from .replay import replay

        return replay(self.log, seed=self.seed, universe=self.universe,
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
        macro = None
        if "macro" in payload:
            macro = Macro(**payload["macro"])
        return cls(
            seed=payload["seed"],
            universe=Universe.from_json(json.dumps(payload["universe"])),
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
