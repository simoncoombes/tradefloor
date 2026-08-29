"""Fork a simulation mid-flight and run both futures.

Run to day sixty, then ask two questions of the same market: what happens under
a rate shock, and what happens without one. Everything before the fork is
identical, not statistically similar but identical, because both branches are
the same seed replayed to the same point.

```python
mark = tf.Checkpoint.of(engine)          # after sixty days
calm, hiked = mark.branch(2)
run_scenario(shock, engine=hiked, ...)
```

That is a counterfactual real markets cannot offer, and it is a different one
from the two already here. `tca.analyse` asks what your trading cost against a
world where you did not trade; `scenario.compare` asks what a macro path did.
This asks what happens NEXT, from a state you have already reached and want to
keep.

## Two ways to fork, and they are for different things

:func:`branch` forks in memory by COPYING the engine. Measured on a sixty-day,
forty-instrument run: **under a millisecond**, against 2,740 ms to replay the
same history. A copy carries everything the engine holds, including the run's
order log, so a fork can itself be checkpointed, forked again, or written to a
:class:`tradefloor.RunManifest`.

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

What you get for it is that the checkpoint is DATA, a few kilobytes of JSON
at about 200 bytes per day, rather than an opaque memory image. It survives the
process, the version and the machine, which an in-memory snapshot of a PCG32
state and a maker inventory would not.

## Why `branch` copies rather than rebuilding from a state snapshot

Because a rebuild needs a list of fields to carry, and that list was wrong
five separate times.

`branch` used to construct a fresh engine and write
:meth:`Engine.state_snapshot` into it. The engine holds the generator
position, a cached Box-Muller spare, per-company GARCH variance, maker
inventories, the mispricing carry -- and, beside all of those, per-DAY state.
The snapshot carried the columns and the generator and missed the
accumulators, so a fork taken BETWEEN two sessions of the same day lost the
day's attribution and the market-open flag; it re-opened the day on its next
session, re-anchored `previous_close`, and priced differently from the parent
it was supposed to be a copy of. That was found and the fields were added.
Then the market factor's variance was missing. Then the common log-volume
state. Then the day counter. Each was added after a fork diverged.

The fifth was the day's endogenous news, which is generated once at
`open_market` and read by every tick of that day. A mid-day fork ran the rest
of the day without it and priced differently from its parent, on the shipped
default preset, and every existing test forked either on a day boundary or on
a universe small enough that the day was usually newsless.

Three more went with it, all silent in the same way. The fork's order log was
empty, so a `Checkpoint` taken on a fork replayed a market beginning at day
zero and a `RunManifest` taken on one failed its digest check while blaming
the platform. A mid-day fork lost the day's already-recorded ticks and wrote a
day half as long. And it lost the previous close's pending jump, so the first
row of its next recorded day attributed nothing to a move that happened.

The lesson is not "the list was missing five things". It is that a list
maintained by hand beside a growing struct will be missing a sixth. So there
is no list: a fork is `engine.clone()`, and a field added tomorrow is carried
without anyone remembering to carry it.

`state_snapshot` remains, with a narrower and now accurate promise: it carries
the market state, and not the order log, the recorded tape or the pending
jump. Those are history and output rather than state, and the method says so.

## Why the SERIALISED form is still the log

A copy cannot leave the process. A checkpoint has to, so it is data: the seed,
the universe and the order log, which is already the reproduction mechanism,
already tested, and already the thing a published result cites. A JSON dump of
a PCG32 position and a maker inventory would be an opaque memory image that
survives neither the version nor the machine.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from ._core import Engine, Instrument, Macro, ModelParams, ValidationError

CHECKPOINT_SCHEMA = 1


class Checkpoint:
    """A point in a simulation you can return to, as data."""

    __slots__ = ("seed", "universe", "log", "macro", "label", "model",
                 "written_by", "era")

    def __init__(self, *, seed: int, universe: Sequence[Instrument],
                 log: Sequence[dict], macro: Macro | None = None,
                 label: str = "", model: dict | None = None,
                 written_by: str | None = None, era: str | None = None) -> None:
        self.seed = int(seed)
        self.universe = list(universe)
        self.log = [dict(entry) for entry in log]
        self.macro = macro
        self.label = label
        # The full coefficient dictionary of a NON-DEFAULT model, or None
        # for a run under the default preset. Carried because a checkpoint
        # of a custom-model run that resumed under the default would replay
        # a plausible market that is not the one it froze.
        self.model = dict(model) if model else None
        # The build that wrote it. A version STRING is quoted and not trusted;
        # the era digest is a fixed probe simulation, so two builds that agree
        # on it produce the same numbers for the arithmetic a replay does.
        # Both are recorded because the digest says a checkpoint cannot be
        # replayed and only the version can say what to install instead.
        self.written_by = written_by
        self.era = era

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
        # Compared against the ENGINE'S default preset, not against a
        # "custom-" prefix: `resume` passes None straight to Engine, so
        # None is only honest for the model Engine defaults to. Under the
        # prefix test, a run under some other shipped preset would
        # checkpoint as None and silently resume under the default -- the
        # exact substitution the fingerprint exists to make impossible.
        #
        # It happened: through 0.1.4 `ModelParams.from_preset()` with no
        # name returned pt-v1 while engines default to pt-v3, so a pt-v1 run
        # checkpointed with no model and resumed as pt-v3. The no-arg form
        # now follows the engine, and the test below round-trips every
        # shipped preset rather than trusting that.
        from . import __version__
        from .manifest import era_fingerprint

        fingerprint = engine.model_fingerprint
        default = ModelParams.from_preset().fingerprint
        return cls(seed=seed, universe=universe, log=engine.order_log,
                   macro=macro, label=label,
                   model=(dict(engine.model_params)
                          if fingerprint != default else None),
                   written_by=__version__, era=era_fingerprint())

    def resume(self, *, universe: Sequence[Instrument] | None = None) -> Engine:
        """A fresh engine at this exact state.

        Replays the log, so it costs roughly what reaching the state cost the
        first time.

        Refuses a checkpoint written by a build whose arithmetic differs from
        this one's. A replay is the ORIGINAL run re-executed, so a release that
        moved a trajectory does not make an old checkpoint resume wrongly in a
        visible way: it makes it resume into a market that never existed, at
        the right size, with the right shape, silently. `RunManifest` has
        checked this since 0.2; a checkpoint carried no version at all.

        Pass ``universe`` to resume onto a roster you already hold rather than
        the one carried in the checkpoint. It is checked against the recorded
        fingerprint and refused on a mismatch -- tickers are generated
        positionally, so two universes can share every name and no
        fundamentals, and comparing names would be checking almost nothing.
        """
        from . import Universe, __version__
        from .manifest import era_fingerprint
        from .replay import replay

        # Absent means "written before this was recorded", which is every
        # archive made before this release; refusing those would break them to
        # guard against a hazard nothing can measure for them.
        if self.era is not None and self.era != era_fingerprint():
            raise ValidationError(
                f"this checkpoint was written by tradefloor "
                f"{self.written_by or 'an unrecorded version'} and this is "
                f"{__version__}, whose arithmetic differs: the era digest is "
                f"{era_fingerprint()[:12]}... against the checkpoint's "
                f"{self.era[:12]}.... Replaying it here would produce a market "
                "that is the right size and shape and is not the one that was "
                "frozen. Install the version that wrote it, or re-run the "
                "experiment on this one and record a new checkpoint."
            )
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
                      macro=self.macro,
                      model=(ModelParams.from_dict(self.model)
                             if self.model else None))

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
        if self.written_by is not None:
            payload["tradefloor_version"] = self.written_by
        if self.era is not None:
            payload["era"] = self.era
        if self.model is not None:
            payload["model"] = self.model
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

        # A damaged archive must arrive as a damaged archive. Left to
        # themselves the reads below raise `KeyError: 'seed'` and
        # `TypeError: string indices must be integers`, which tell a
        # developer that a dictionary lacked a key and say nothing about the
        # thing they are actually holding.
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValidationError(
                f"this is not JSON, so it is not a checkpoint: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValidationError(
                "this is not a checkpoint: a checkpoint is a JSON object with "
                f"a seed, a universe and a log, and this is a "
                f"{type(payload).__name__}."
            )
        for field in ("seed", "universe", "log"):
            if field not in payload:
                raise ValidationError(
                    f"this checkpoint has no {field!r}, so it cannot be "
                    "resumed. A checkpoint carries the seed, the universe and "
                    "the order log; one of the three is missing, which means "
                    "the file was truncated or written by something that is "
                    "not tradefloor."
                )
        schema = payload.get("schema", 0)
        if schema > CHECKPOINT_SCHEMA:
            raise ValidationError(
                f"checkpoint schema {schema} is newer than this version "
                "understands. Upgrade tradefloor rather than reading it partially."
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
            model=payload.get("model"),
            written_by=payload.get("tradefloor_version"),
            era=payload.get("era"),
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
    universe: Sequence[Instrument] | None = None,
    seed: int | None = None,
    macro: Macro | None = None,
) -> list[Engine]:
    """Fork a running engine in constant time.

    Copies the engine, so each branch continues from EXACTLY the state the
    parent had reached: every column, the generator position, the day's
    accumulators, the day's endogenous news, the recorded tape and the order
    log. Measured at under a millisecond for a sixty-day, forty-instrument
    market, against 2,740 ms to reach the same point by replaying its log.

    The branches share no memory, so driving one cannot perturb another. That
    is what makes a fork a controlled experiment rather than two runs that
    started similarly.

    Because the fork carries the parent's order log, it can itself be
    checkpointed, forked again, or written to a :class:`tradefloor.RunManifest`.
    That was not true while a fork was rebuilt from a state snapshot: the new
    engine's log was empty, so a checkpoint taken on it replayed a market
    beginning at day zero, silently.

    ``universe`` and ``seed`` are accepted for compatibility and are no longer
    needed -- a copy cannot land on the wrong roster, which is what they were
    there to prevent. A ``universe`` that IS passed is checked against the
    engine's own tickers, so a caller who believes they are forking a
    different market is told rather than humoured.

    For a fork that must survive the process, use :class:`Checkpoint`.
    """
    if count < 1:
        raise ValidationError(f"count must be at least 1, got {count}")
    if universe is not None:
        supplied = [i.ticker for i in universe]
        if supplied != engine.tickers:
            raise ValidationError(
                f"the universe passed to branch() has {len(supplied)} "
                f"instruments and this engine has {len(engine.tickers)}; they "
                "are not the same roster. A fork copies the engine, so it "
                "cannot be moved onto another universe -- build an engine on "
                "that roster instead."
                if len(supplied) != len(engine.tickers) else
                "the universe passed to branch() is not this engine's roster: "
                "it is ordered differently. A fork copies the engine, so it "
                "cannot be reordered -- build an engine on that roster "
                "instead."
            )
    return engine.fork(count)
