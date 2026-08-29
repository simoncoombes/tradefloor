"""One object a stranger can reproduce a run from, and know that they did.

`docs/reproducing-a-run.md` lists the five things that identify a run and
shows a careful reader how to archive and check each one by hand. This module
is that page as a single artifact:

```python
manifest = tf.RunManifest.of(engine, seed=42, universe=u, macro=m)
open("run.json", "w").write(manifest.to_json())

# ...anywhere else, with nothing but the package and the file...
same = tf.RunManifest.from_json(open("run.json").read()).reproduce()
```

`reproduce()` replays the run and checks the result against the digest the
manifest carries, so the reader is TOLD whether they rebuilt the same market
rather than eyeballing numbers off a page. On success the returned engine is
the published market, bit for bit. On any mismatch it raises, and the error
names the component that disagreed, which is why every component carries its
own fingerprint rather than one hash over the whole file.

## The completeness rule

A manifest reproduces if and only if every component is either shipped with
the library or embedded in the manifest. A fingerprint identifies; it cannot
reconstruct, because you cannot invert a hash. So the manifest EMBEDS
everything user-supplied: the roster itself (never a recipe for one, since generators change
across versions, and an EDGAR query is not the data it returned), the macro
initial conditions, the realised scenario path, the full order log, and the
strategy when it is a :class:`StrategySpec`.

The one component that cannot always be embedded is a hand-written Python
agent, and the manifest says so rather than pretending: pass a reference
string ("repo X at commit Y") and the manifest records the strategy as
referenced, not carried. Such a manifest is honestly incomplete, and its
:attr:`~RunManifest.complete` is False and :attr:`~RunManifest.gaps` says
why. The MARKET still reproduces, because the agent's orders are data in the
log; what the reader cannot do without the referenced code is re-run the
strategy itself on new inputs. That mirrors ``Scorecard.strategy_fingerprint``
being deliberately empty for hand-written agents: an escape hatch that
declares itself.

## The era, and why it is a measurement rather than a version number

A run is only reproducible on a build whose arithmetic matches the build that
ran it. "Across versions, not at all" is the documented guarantee, and the
hazard is live: one calendar day brought three trajectory-changing fixes
(the macro-chain and volume fixes, then the market-factor-sigma
recalibration) while ``tf.version()`` stayed 0.1.0 and the preset stayed
"pt-v1", and the recalibrated constant is not even in the preset dictionary,
so a preset-value comparison holds still with them. Every NAME the
library could quote held still while the numbers moved. A manifest that
trusted names would replay on the wrong build, produce a plausible market,
and manufacture exactly the false confidence it exists to prevent.

So the era identity here is behavioural: :func:`era_fingerprint` runs a
small fixed simulation: generator draws, fair value across every sector,
the daily mispricing step, and a coupled engine run through day closes, and
digests it, the same canonical-f64 discipline as ``tests/known_answer.py``.
The test suite's ``KAT_VERSION`` is the same idea kept by convention, but it
lives in the test tree, which an installed wheel does not have, and a
convention depends on a human remembering to bump it. A digest cannot forget.
Two builds that agree on the probe agree on the arithmetic the probe
exercises; two that disagree will not reproduce each other's runs, whatever
their version strings say. ``reproduce()`` checks the probe BEFORE replaying
and refuses on a mismatch, naming both builds, following ``Checkpoint``'s
precedent of refusing over quietly running against the wrong world.

The package version, the preset name and the full coefficient dictionary
still ride along, since they are what a methods section quotes, the coefficient
values give a mismatch a specific name when the model itself moved, and the
embedded values are what will let a future custom preset travel without a
format change, but none of them is trusted as the era. The probe is.

## What a successful reproduction proves about platforms

Cross-OS bit-identity is measured by commit. The five-target release gate
has run: at ``ad91026`` (known-answer v5, the RNG stream split), all five
targets (Linux x86_64 and aarch64, macOS arm64 and x86_64, and Windows
x86_64) produced the identical digest, ``76983e65...3180eeb``, each also
passing against the committed baseline. It has not yet run against a
tagged release, and the current digest, ``1ee64998...fe3581c`` at v8, was
regenerated on macOS arm64 and has one platform's confirmation behind it
until the gate runs again. ``docs/reproducing-a-run.md`` keeps the full
record. The manifest records the writer's platform and claims nothing
beyond that. What it offers instead is sharper: the manifest carries the
expected output digest, so a successful ``reproduce()`` on a different
machine IS a cross-platform measurement for that run, made by the reader,
not promised by the library. A failure after every input verified is
reported as exactly that: an arithmetic divergence on an unmeasured pair,
with both platforms named.
"""

from __future__ import annotations

import hashlib
import json
import platform as _platform
import struct
from typing import Any, Sequence

from ._core import (
    Engine,
    GameRng,
    Instrument,
    Macro,
    MispricingState,
    ModelParams,
    ValidationError,
    fair_value,
    model_preset,
    sectors,
    step_mispricing_daily,
    version,
)
from .replay import replay
from .scenario import Scenario
from .spec import StrategySpec

MANIFEST_SCHEMA = 1

#: Version of the fixed probe simulation behind :func:`era_fingerprint`.
#: Bumped only when the PROBE ITSELF changes, since its digests are then a new
#: series, and comparing across probe versions is refused as meaningless
#: rather than reported as an era mismatch it is not.
ERA_PROBE = 1

#: The engine columns the result digest covers. The nine the known-answer
#: test hashes, for the same reason it hashes them: printed prices sit on a
#: cent grid that can absorb a low-bit divergence, while ``mispricing_s`` and
#: ``garch_variance`` carry the continuous state where such a divergence
#: actually lives. A digest over prices alone could pass while the market
#: state underneath had drifted, which is confidence it had not earned.
DIGEST_COLUMNS = (
    "price", "previous_close", "open", "high", "low",
    "volume", "market_cap", "mispricing_s", "garch_variance",
)

_MACRO_FIELDS = ("vix", "federal_funds_rate", "corporate_bond_yield",
                 "inflation_rate", "qe_pe_boost", "fear_greed_index", "cycle")


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _f64(buf: bytearray, value: float) -> None:
    """One f64 in canonical big-endian form, NaN normalised.

    The same rule as the known-answer test, for the same reason: no decimal
    formatting anywhere near a digest, and one quiet-NaN bit pattern, because
    IEEE-754 leaves NaN sign and payload to the platform.
    """
    if value != value:  # NaN
        buf.extend(b"\x7f\xf8\x00\x00\x00\x00\x00\x00")
    else:
        buf.extend(struct.pack(">d", value))


def _macro_payload(macro: Macro) -> dict[str, Any]:
    return {field: getattr(macro, field) for field in _MACRO_FIELDS}


def market_digest(engine: Engine) -> str:
    """sha256 over an engine's end-of-run market state.

    Covers :data:`DIGEST_COLUMNS` for every instrument plus the draw count.
    Two engines with equal digests ended on the same market to the bit,
    including the continuous internals that tomorrow's prices depend on, not
    only the prices a cent grid has already rounded.
    """
    n = len(engine.tickers)
    buf = bytearray()
    for field in DIGEST_COLUMNS:
        for value in struct.unpack("<%dd" % n, engine.column(field)):
            _f64(buf, value)
    _f64(buf, float(engine.draws_consumed))
    return hashlib.sha256(bytes(buf)).hexdigest()


def era_fingerprint() -> str:
    """Digest of a fixed probe simulation: the build's behavioural identity.

    Two builds that agree here produce the same numbers for the arithmetic
    the probe exercises: the generator, fair value across every sector and
    both valuation paths, the daily mispricing step, and a coupled engine run
    through day closes, where the macro chain advances. Version strings and
    preset names are quoted in a manifest but not trusted as the era, because
    both have already held still across a boundary that moved every
    trajectory; this digest moved. See the module docstring for the argument.

    Deliberately a smaller sibling of ``tests/known_answer.py``, living in
    the package because the test tree does not ship in a wheel and a reader
    checking a manifest has nothing else.
    """
    buf = bytearray()

    # The generator, both draw kinds interleaved, so the Box-Muller spare's
    # parity is covered as state rather than assumed.
    rng = GameRng(20260821, 99)
    for i in range(32):
        _f64(buf, rng.next_float())
        _f64(buf, rng.next_normal())
        if i % 5 == 0:
            _f64(buf, float(rng.next_int(-500, 500)))

    # Fair value across all twelve sectors, exercising the earnings path, the
    # book-value path, the bond-yield fallback and the QE adjustment.
    sector_names = sectors()
    for index, sector in enumerate(sector_names):
        value = fair_value(
            eps=(-1.2 if index % 5 == 3 else 0.8 + index * 0.6),
            sector=sector,
            revenue_growth=-0.04 + index * 0.03,
            federal_funds_rate=0.02 + index * 0.002,
            corporate_bond_yield=None if index % 3 == 0 else 0.03 + index * 0.002,
            qe_pe_boost=0.05 if index % 2 == 0 else 0.0,
            book_value_per_share=6.0 + index,
        )
        _f64(buf, value.fair_value)
        _f64(buf, value.target_pe)
        _f64(buf, value.rate_adjustment)
        _f64(buf, 1.0 if value.book_value_path else 0.0)

    # The daily mispricing step over sixty days, where a 1-ULP disagreement
    # compounds into something a digest can see.
    state = MispricingState(0.05)
    for day in range(60):
        state = step_mispricing_daily(
            state, innovation=rng.next_normal() * 0.01,
            shock=0.02 if day % 17 == 16 else 0.0,
        )
        if day % 6 == 0:
            _f64(buf, state.s)
            _f64(buf, state.s_prev)

    # The coupled system: eight instruments, three sessions, each through the
    # close, which is where the macro chain advances, where GARCH extracts
    # its innovation, and where the 2026-08 era boundary's changes all live.
    instruments = [
        Instrument(
            f"ERA{i}",
            sector_names[i % 12],
            initial_price=18.0 + i * 9.0,
            shares_outstanding=2.0e8 + i * 3.0e7,
            eps=(-0.8 if i == 5 else 0.9 + i * 0.5),
            book_value_per_share=9.0 + i * 1.5,
            revenue_growth=-0.02 + i * 0.025,
            avg_volume=200_000 + i * 120_000,
            beta=0.6 + i * 0.12,
        )
        for i in range(8)
    ]
    engine = Engine(
        seed=20260821,
        universe=instruments,
        macro_state=Macro(
            vix=22.0, federal_funds_rate=0.03, corporate_bond_yield=0.055,
            inflation_rate=0.028, qe_pe_boost=0.0, fear_greed_index=40.0,
            cycle="contraction",
        ),
    )
    for _ in range(3):
        engine.open_market()
        engine.run_session(9, 30, 3, 78)
        engine.close_market()
        for field in DIGEST_COLUMNS:
            for value in struct.unpack("<8d", engine.column(field)):
                _f64(buf, value)
    _f64(buf, float(engine.draws_consumed))

    # The preset values themselves, sorted by key, so a coefficient edit that
    # somehow escaped the run above still moves the digest.
    preset = model_preset()
    for key in sorted(k for k in preset if k != "name"):
        _f64(buf, float(preset[key]))

    return hashlib.sha256(bytes(buf)).hexdigest()


class RunManifest:
    """A finished run as one shareable, self-verifying document.

    Built by :meth:`of` from the engine that ran, serialised with
    :meth:`to_json`, and checked by whoever receives it with
    :meth:`reproduce`. See the module docstring for what it carries, what it
    refuses, and why the era check is a digest rather than a version number.
    """

    __slots__ = ("_doc",)

    def __init__(self, doc: dict[str, Any]) -> None:
        self._doc = doc

    # -- writing -----------------------------------------------------------

    @classmethod
    def of(cls, engine: Engine, *, seed: int,
           universe: Sequence[Instrument], macro: Macro | None = None,
           scenario: Scenario | None = None,
           strategy: StrategySpec | str | None = None,
           universe_source: Any = None, label: str = "",
           derived_from: Any = None) -> "RunManifest":
        """Capture a finished run.

        ``universe`` and ``seed`` are passed rather than read off the engine
        for the same reason ``Checkpoint.of`` requires them: an engine is
        built FROM them and keeps neither.

        ``strategy`` is a :class:`StrategySpec` (carried in full, cited by
        its fingerprint) or a reference string for a hand-written agent,
        "repo X at commit Y", which the manifest records as referenced, not
        carried, and declares in :attr:`gaps`. An agent OBJECT is refused:
        the manifest cannot serialise code, and accepting it would embed a
        ``repr`` while implying it embedded a strategy.

        ``universe_source`` is optional provenance (the ``random(n, seed)``
        recipe, an EDGAR snapshot hash and as-of date) recorded for the
        methods section. The roster itself is always embedded regardless,
        because a recipe reproduces only while the generator behaves the same
        and a query is not the data it returned.

        ``derived_from`` is the :class:`tradefloor.Checkpoint` this run
        branched from, for the arm of an experiment rather than a run that
        started at day zero. It records the checkpoint's fingerprint, its
        label and how many log entries it held, which is the fork point.

        Without it, lineage is only DERIVABLE: two branches of one experiment
        share a log prefix and its length is where they parted, so a reader
        holding both manifests can recover the structure by comparing them.
        A reader holding one cannot, and nothing says a run is a branch of
        anything. This is that sentence, written down.
        """
        from . import Universe

        if strategy is not None and not isinstance(strategy, (StrategySpec, str)):
            raise ValidationError(
                "strategy must be a StrategySpec or a reference string, got "
                f"{type(strategy).__name__}. A hand-written agent cannot be "
                "carried as data. Pass where its code lives (repo and "
                "commit) and the manifest will record it as referenced, not "
                "carried."
            )
        if isinstance(strategy, str) and not strategy.strip():
            raise ValidationError(
                "an empty strategy reference points a reader at nothing. "
                "Name where the code lives, or pass None for a run with no "
                "strategy."
            )

        log = [dict(entry) for entry in engine.order_log]
        days = sum(1 for entry in log if entry.get("op") == "open_market")

        roster = (universe if isinstance(universe, Universe)
                  else Universe(universe))
        universe_payload = json.loads(
            roster.to_json(sort_keys=True, separators=(",", ":"), indent=None)
        )
        if universe_source is not None:
            try:
                _canonical(universe_source)
            except TypeError:
                raise ValidationError(
                    "universe_source must be JSON-serialisable: it travels "
                    "inside the manifest."
                ) from None

        macro_payload = None if macro is None else _macro_payload(macro)

        scenario_payload = None
        if scenario is not None:
            if days == 0:
                raise ValidationError(
                    "the engine's log has no traded days, so the scenario "
                    "has no realised path to record. Run first, then capture."
                )
            scenario_payload = json.loads(scenario.to_json(days))

        if isinstance(strategy, StrategySpec):
            strategy_payload: dict[str, Any] | None = {
                "spec": strategy.as_dict()}
            strategy_fp: str | None = strategy.fingerprint
        elif isinstance(strategy, str):
            # The escape hatch working as designed: a hand-written agent has
            # no data form, so its fingerprint is deliberately absent, exactly
            # as Scorecard.strategy_fingerprint is empty for one.
            strategy_payload = {"reference": strategy}
            strategy_fp = None
        else:
            strategy_payload = None
            strategy_fp = None

        fingerprints = {
            "universe": roster.fingerprint,
            "macro": None if macro_payload is None
            else _sha(_canonical(macro_payload)),
            "scenario": None if scenario_payload is None
            else _sha(_canonical(scenario_payload)),
            "strategy": strategy_fp,
            # The model rides beside the strategy: the same honesty
            # mechanism, where a shipped preset is cited by name and a
            # custom one by custom-XXXXXXXX, never mistakable for a
            # standard model in a published result.
            "model": engine.model_fingerprint,
            "order_log": _sha(_canonical(log)),
        }
        fingerprints["inputs"] = _sha(_canonical(
            {"seed": int(seed), **fingerprints}))

        doc = {
            "schema": MANIFEST_SCHEMA,
            "label": label,
            "written_by": {
                "pretium_version": version(),
                "platform": {"os": _platform.system(),
                             "machine": _platform.machine()},
                # The FULL preset surface of the model the engine actually
                # ran, not the build's default, with "name" as its
                # fingerprint. Embedding the values is what lets a custom
                # preset travel: a fingerprint identifies, it cannot
                # reconstruct.
                "model": dict(engine.model_params),
                "era": {"probe": ERA_PROBE, "digest": era_fingerprint()},
            },
            "seed": int(seed),
            "universe": universe_payload,
            "universe_source": universe_source,
            "macro": macro_payload,
            "scenario": scenario_payload,
            "strategy": strategy_payload,
            "order_log": log,
            "fingerprints": fingerprints,
            "result": {
                "digest": market_digest(engine),
                "days": days,
                "draws_consumed": engine.draws_consumed,
            },
        }
        if derived_from is not None:
            # Identity before history. The log is a sequence of INPUTS, so two
            # runs that opened and ran the same sessions carry the same log
            # whatever seed drew their market: comparing prefixes alone would
            # accept a checkpoint of an entirely different world. The seed and
            # the roster are what separate them.
            if int(derived_from.seed) != int(seed):
                raise ValidationError(
                    f"this checkpoint was taken on seed {derived_from.seed} "
                    f"and this run is seed {seed}, so the run did not branch "
                    "from it. Their order logs can still match: a log records "
                    "inputs, and the same sessions on two seeds are the same "
                    "inputs on two different markets."
                )
            if derived_from.universe_fingerprint != fingerprints["universe"]:
                raise ValidationError(
                    "this checkpoint was taken on a different roster "
                    f"({derived_from.universe_fingerprint[:12]}... against "
                    f"{fingerprints['universe'][:12]}...), so the run did not "
                    "branch from it. Tickers are generated positionally, so "
                    "two rosters can share every name and no fundamentals."
                )
            entries = len(derived_from.log)
            if entries > len(log):
                raise ValidationError(
                    f"this checkpoint holds {entries} log entries and the run "
                    f"holds {len(log)}, so the run cannot have started from "
                    "it. A branch continues its parent's history, so its log "
                    "is at least as long."
                )
            if list(log[:entries]) != list(derived_from.log):
                raise ValidationError(
                    "this run's first "
                    f"{entries} log entries are not the checkpoint's, so it "
                    "did not start there. Passing derived_from is a claim "
                    "about history, and it is checked when it is made rather "
                    "than believed."
                )
            doc["derived_from"] = {
                "checkpoint": derived_from.fingerprint,
                "label": derived_from.label,
                "entries": entries,
            }
        return cls(doc)

    def to_json(self) -> str:
        """The whole manifest as JSON: the artifact you hand over."""
        return _canonical(self._doc)

    # -- reading -----------------------------------------------------------

    @classmethod
    def from_json(cls, text: str) -> "RunManifest":
        """Load a manifest, checking every carried component's fingerprint.

        A component that arrives not matching the fingerprint it was written
        with is refused BY NAME, before anything runs: a manifest that
        travelled and arrived changed no longer describes the run it came
        from, and replaying it anyway would produce a market that fails the
        result check for a reason the error could no longer locate.
        """
        payload = json.loads(text)
        if not isinstance(payload, dict) or "order_log" not in payload \
                or "fingerprints" not in payload or "result" not in payload:
            raise ValidationError("not a tradefloor run manifest document")
        schema = payload.get("schema", 0)
        if schema > MANIFEST_SCHEMA:
            raise ValidationError(
                f"manifest schema {schema} is newer than this version "
                "understands. Upgrade tradefloor rather than reading it "
                "partially."
            )

        from . import Universe

        recorded = payload["fingerprints"]

        universe = Universe.from_json(json.dumps(payload["universe"]))
        if universe.fingerprint != recorded.get("universe"):
            raise ValidationError(
                "the universe in this manifest does not match its recorded "
                f"fingerprint ({str(recorded.get('universe'))[:12]}... vs "
                f"{universe.fingerprint[:12]}...). The roster was edited in "
                "transit; the manifest no longer describes the market it "
                "came from."
            )

        for name, part in (("macro", payload.get("macro")),
                           ("scenario", payload.get("scenario"))):
            expected = recorded.get(name)
            if (part is None) != (expected is None):
                raise ValidationError(
                    f"this manifest carries a {name} fingerprint and no "
                    f"{name}, or the reverse. One of them was removed in "
                    "transit."
                )
            if part is not None and _sha(_canonical(part)) != expected:
                raise ValidationError(
                    f"the {name} in this manifest does not match its "
                    "recorded fingerprint. It was edited in transit, and "
                    "replaying under it would produce a market the manifest "
                    "does not describe."
                )

        if payload.get("scenario") is not None:
            # Constructing validates the path (contiguous days, fixed
            # fields, plausible rates) so a coherent-looking but malformed
            # scenario is caught here by what is wrong with it.
            Scenario.from_json(json.dumps(payload["scenario"]))

        strategy_payload = payload.get("strategy")
        if strategy_payload is not None and "spec" in strategy_payload:
            rebuilt = StrategySpec.from_json(
                json.dumps(strategy_payload["spec"]))
            if rebuilt.fingerprint != recorded.get("strategy"):
                raise ValidationError(
                    "the strategy spec in this manifest does not match its "
                    f"recorded fingerprint ({str(recorded.get('strategy'))[:12]}"
                    f"... vs {rebuilt.fingerprint[:12]}...). It was edited "
                    "in transit."
                )
        elif recorded.get("strategy") is not None:
            raise ValidationError(
                "this manifest records a strategy fingerprint but carries no "
                "spec for it. The carried spec was removed in transit; a "
                "fingerprint identifies, it cannot reconstruct."
            )

        recorded_model = recorded.get("model")
        if recorded_model is not None:
            carried = (payload.get("written_by") or {}).get("model") or {}
            if carried.get("name") != recorded_model:
                raise ValidationError(
                    "the model dictionary in this manifest is named "
                    f"{carried.get('name')!r} but its recorded fingerprint "
                    f"is {recorded_model!r}. One of them was edited in "
                    "transit; the values themselves are re-verified against "
                    "the fingerprint before any replay."
                )

        if _sha(_canonical(payload["order_log"])) != recorded.get("order_log"):
            raise ValidationError(
                "the order log does not match its recorded fingerprint. The "
                "log is the run's input sequence; an altered log replays a "
                "different run under the original's name."
            )

        check = dict(recorded)
        check.pop("inputs", None)
        if _sha(_canonical({"seed": payload.get("seed"), **check})) \
                != recorded.get("inputs"):
            # Every component just verified individually, so what is left to
            # disagree is the one bare input outside them.
            raise ValidationError(
                "the manifest's inputs do not match the fingerprint they "
                "were written with, and every carried component checks out "
                "individually: the seed was edited in transit."
            )

        return cls(payload)

    # -- checking ----------------------------------------------------------

    def reproduce(self) -> Engine:
        """Replay the run and verify the result. Returns the rebuilt market.

        Refuses BEFORE replaying if this build is a different era from the
        one that wrote the manifest, because a manifest that silently produced
        different numbers across an era boundary would manufacture false
        confidence, which is worse than no manifest at all. On a result
        mismatch after every input and the era verified, the error reports
        both platforms and the draw counts, which is where a bisection
        starts.
        """
        self._check_era()
        self._check_lineage()

        engine = replay(self.order_log, seed=self.seed,
                        universe=self.universe, macro=self.macro,
                        model=self._model_for_replay())

        recorded = self._doc["result"]
        digest = market_digest(engine)
        if digest != recorded["digest"]:
            raise ValidationError(self._divergence(engine, digest, recorded))
        return engine

    def _divergence(self, engine: Engine, digest: str,
                    recorded: dict[str, Any]) -> str:
        """Why the replay did not rebuild the market, ranked by the evidence
        already in hand.

        This used to lead with "an unmeasured platform pair" in every case,
        and print the pair -- which was often the SAME platform twice, so the
        sentence disproved itself while sending the reader to the Rust core.
        It happened for real: a manifest taken on a fork whose order log was
        empty reported a suspected Windows-versus-Windows arithmetic
        difference, and the cause was a truncated history.

        Two facts are free here and neither was used. The draw counts say
        whether the two runs executed the same sequence of operations at all,
        which separates an input problem from an arithmetic one; and the two
        platform strings say whether a platform difference is even available
        as an explanation.
        """
        wrote = self._doc["written_by"]["platform"]
        there = f"{wrote['os']}-{wrote['machine']}"
        here = f"{_platform.system()}-{_platform.machine()}"
        head = (
            "the replay ran but did not rebuild the recorded market: "
            f"digest {digest[:12]}... against the manifest's "
            f"{recorded['digest'][:12]}... (draws consumed "
            f"{engine.draws_consumed} against {recorded['draws_consumed']}). "
        )
        bisect = (" Bisect with tradefloor.replay(log, ..., until=n): replay "
                  "both to step n and compare, and the first n that differs "
                  "is the operation to look at.")

        if engine.draws_consumed != recorded["draws_consumed"]:
            return head + (
                "The DRAW COUNTS DIFFER, so the two runs did not execute the "
                "same sequence of operations. That is an input difference, "
                "not an arithmetic one, and no platform explains it: this log "
                "is not the log that produced the recorded result. It is "
                "shorter or longer than the history it claims. The usual "
                "cause is a manifest written on an engine whose order log "
                "did not cover how it reached its state."
            ) + bisect

        if there != here:
            return head + (
                "Every carried input matched its fingerprint, the era probe "
                "agreed, and the draw counts match, so the two runs executed "
                "the same operations and disagreed about the arithmetic. "
                f"They ran on different platforms ({there} wrote it, {here} "
                "replayed it), which is the leading suspect: this is the "
                "cross-platform bit-identity the release gate exists to "
                "measure, and a pair it has not measured can differ."
            ) + bisect

        return head + (
            "Every carried input matched its fingerprint, the era probe "
            "agreed, and the draw counts match. Both runs are on "
            f"{here}, so a platform difference is NOT the explanation. What "
            "is left, in order: a build with different flags (float "
            "reassociation, FMA contraction or target-cpu=native would each "
            "do this, which is why the release profile forbids them); a "
            "wheel that is not the one whose digest was recorded, despite "
            "reporting the same version; or arithmetic the era probe does "
            "not exercise."
        ) + bisect

    def _check_lineage(self) -> None:
        """The half of the lineage claim a manifest can check alone.

        Without the checkpoint there is no way to confirm the first entries
        ARE its log -- :meth:`verify_lineage` is for a reader who holds it.
        What is checkable here is that the claim is not self-contradictory: a
        run cannot have branched from a point later than its own history.
        """
        recorded = self.derived_from
        if recorded is None:
            return
        entries = int(recorded["entries"])
        if entries > len(self.order_log):
            raise ValidationError(
                f"this manifest says it branched from a checkpoint holding "
                f"{entries} log entries and carries {len(self.order_log)}. A "
                "branch continues its parent's history, so it cannot be "
                "shorter than the point it started from."
            )

    def _model_for_replay(self) -> ModelParams | None:
        """The model the run was recorded under, rebuilt for the replay.

        ``None`` only for the preset the engine defaults to, including
        every manifest written before the model dict carried the full
        surface. A ``custom-`` model is rebuilt from the embedded values,
        and a NAMED preset that is not the default is looked up by name;
        :meth:`_check_era` has already verified this build ships it, can
        run it, and that its values still match their recorded
        fingerprint.

        The second case is why this is not "not custom, therefore None".
        With more than one shipped preset in the table, returning ``None``
        for a name the engine does not default to would replay the run
        under a different model and report success, which is the exact
        substitution the model fingerprint exists to make impossible,
        reached by way of a shortcut that was correct only while the table
        had one row.
        """
        theirs = (self._doc["written_by"].get("model") or {})
        name = theirs.get("name")
        if name is None:
            return None
        if str(name).startswith("custom-"):
            return ModelParams.from_dict(theirs)
        if name == model_preset()["name"]:
            return None
        return ModelParams.from_preset(name)

    def _check_era(self) -> None:
        wrote = self._doc["written_by"]
        theirs = wrote.get("model") or {}
        name = theirs.get("name")

        if isinstance(name, str) and name.startswith("custom-"):
            # A custom preset: the embedded values ARE the model, so the
            # check is that this build can run them and that they still
            # hash to the name they were recorded under. from_dict refuses
            # by name any value this build cannot run (a read-only or
            # derived coefficient that moved, an era boundary for the
            # unthreaded surface).
            rebuilt = ModelParams.from_dict(theirs)
            if rebuilt.fingerprint != name:
                raise ValidationError(
                    "the model dictionary in this manifest no longer "
                    f"matches its own name: the values hash to "
                    f"{rebuilt.fingerprint!r} against the recorded "
                    f"{name!r}. Either the dictionary was edited in "
                    "transit, or this build derives different bits from "
                    "the same inputs; both mean the replay would run a "
                    "model the manifest does not describe."
                )
        else:
            # The named preset the manifest ran, NOT this build's default.
            # While the table had one row those were the same thing; with
            # two they are not, and comparing a pt-v2 manifest against
            # pt-v1's coefficients would refuse a run this build can
            # reproduce perfectly, for a reason that is not true.
            try:
                ours = dict(model_preset(name)) if name else {}
            except ValidationError:
                ours = {}
            if not ours or name != ours.get("name"):
                raise ValidationError(
                    f"this manifest ran model preset {name!r}, which this "
                    "build does not ship. The coefficients are the model, "
                    "so the run cannot be checked here. Reproduce it on a "
                    "build that ships the preset it ran."
                )
            # Compare where both sides carry a value. The intersection
            # rather than the union, deliberately: an older manifest
            # carries the legacy nine-coefficient dict and a newer one the
            # full surface, and a key only one side knows is a difference
            # of BOOKKEEPING, not of model, since the era probe above this
            # block is what catches a behavioural change the comparison
            # cannot see.
            full = ModelParams.from_preset(ours["name"]).to_dict()
            disagreeing = sorted(
                key for key in set(theirs) & (set(ours) | set(full))
                if key != "name"
                and theirs.get(key) != ours.get(key, full.get(key))
            )
            if disagreeing:
                detail = "; ".join(
                    f"{key}: manifest {theirs.get(key)!r}, "
                    f"build {ours.get(key, full.get(key))!r}"
                    for key in disagreeing
                )
                raise ValidationError(
                    f"model preset {ours.get('name')!r} disagrees between this "
                    f"manifest and this build on {detail}. Same name, different "
                    "model: an era boundary. Every seed's trajectory moves "
                    "across one, so replaying here would produce a plausible "
                    "market that is not the one the manifest describes."
                )

        era = wrote.get("era") or {}
        if era.get("probe") != ERA_PROBE:
            raise ValidationError(
                f"this manifest's era probe (v{era.get('probe')}) is not the "
                f"one this build runs (v{ERA_PROBE}), so their digests "
                "cannot be compared. Upgrade tradefloor rather than concluding "
                "anything from two different measurements."
            )
        mine = era_fingerprint()
        if mine != era.get("digest"):
            platform_info = wrote.get("platform", {})
            raise ValidationError(
                "this build does not reproduce the manifest's era: the "
                f"fixed probe simulation digests {mine[:12]}... against the "
                f"recorded {str(era.get('digest'))[:12]}.... Written under "
                f"tradefloor {wrote.get('pretium_version')} on "
                f"{platform_info.get('os')}-{platform_info.get('machine')}; "
                f"this is tradefloor {version()} on {_platform.system()}-"
                f"{_platform.machine()}. An engine, calibration or platform "
                "difference has moved the trajectories, so the run cannot "
                "be reproduced on this build, and running it anyway would "
                "produce a market that looks right and is not the one the "
                "manifest describes."
            )

    # -- what it carries ---------------------------------------------------

    @property
    def seed(self) -> int:
        return self._doc["seed"]

    @property
    def label(self) -> str:
        return self._doc.get("label", "")

    @property
    def derived_from(self) -> dict[str, Any] | None:
        """The checkpoint this run branched from, or ``None`` for a run that
        started at day zero.

        ``{"checkpoint": <fingerprint>, "label": ..., "entries": <fork point>}``.
        The entry count is where this run's history stops being its parent's,
        so two manifests naming the same checkpoint describe two arms of one
        experiment and the number says where they parted.
        """
        recorded = self._doc.get("derived_from")
        return dict(recorded) if recorded else None

    def verify_lineage(self, checkpoint: Any) -> None:
        """Check this manifest's declared parent IS the given checkpoint.

        The declaration alone is a claim: it names a digest, and a reader
        holding only the manifest cannot test it. A reader holding the
        checkpoint can, and this is that test -- the fingerprint must match,
        and the run's first entries must be the checkpoint's log.

        Raises rather than returning a bool, for the same reason
        :meth:`reproduce` does: a lineage check whose result can be ignored
        by writing ``manifest.verify_lineage(cp)`` and reading nothing is a
        check that will be.
        """
        recorded = self.derived_from
        if recorded is None:
            raise ValidationError(
                "this manifest declares no parent, so there is no lineage to "
                "verify. A run recorded with derived_from names the "
                "checkpoint it branched from; this one was not."
            )
        if checkpoint.fingerprint != recorded["checkpoint"]:
            raise ValidationError(
                f"this manifest branched from checkpoint "
                f"{recorded['checkpoint'][:12]}... and the one supplied is "
                f"{checkpoint.fingerprint[:12]}.... Two checkpoints of the "
                "same market taken at different points, or under different "
                "labels, are different starting states and the digests say so."
            )
        entries = int(recorded["entries"])
        if list(self.order_log[:entries]) != list(checkpoint.log):
            raise ValidationError(
                "this manifest's fingerprint matches the checkpoint but its "
                f"first {entries} log entries do not, so one of the two was "
                "edited after it was written."
            )

    @property
    def universe(self):
        """The embedded roster, as a :class:`tradefloor.Universe`."""
        from . import Universe

        return Universe.from_json(json.dumps(self._doc["universe"]))

    @property
    def universe_source(self) -> Any:
        """Provenance of the roster, if recorded. Informational: the roster
        itself is embedded and authoritative."""
        return self._doc.get("universe_source")

    @property
    def macro(self) -> Macro | None:
        payload = self._doc.get("macro")
        return None if payload is None else Macro(**payload)

    @property
    def scenario(self) -> Scenario | None:
        """The realised macro path, as a :class:`Scenario`, or None."""
        payload = self._doc.get("scenario")
        if payload is None:
            return None
        return Scenario.from_json(json.dumps(payload))

    @property
    def strategy(self) -> StrategySpec | None:
        """The carried spec, or None, including for a strategy that is only
        referenced. :attr:`strategy_reference` holds the reference."""
        payload = self._doc.get("strategy")
        if payload is None or "spec" not in payload:
            return None
        return StrategySpec.from_json(json.dumps(payload["spec"]))

    @property
    def strategy_reference(self) -> str | None:
        payload = self._doc.get("strategy")
        if payload is None:
            return None
        return payload.get("reference")

    @property
    def order_log(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self._doc["order_log"]]

    @property
    def fingerprints(self) -> dict[str, Any]:
        return dict(self._doc["fingerprints"])

    @property
    def result(self) -> dict[str, Any]:
        """What the run produced: the market digest, days, draw count."""
        return dict(self._doc["result"])

    @property
    def written_by(self) -> dict[str, Any]:
        """The writing build: package version, platform, model, era digest."""
        return json.loads(json.dumps(self._doc["written_by"]))

    @property
    def model(self) -> dict[str, Any]:
        """The coefficient dictionary of the model the run ran under, with
        ``"name"`` as its fingerprint: a shipped preset's name, or
        ``custom-XXXXXXXX`` for a run that must never be mistaken for one."""
        return dict(self._doc["written_by"].get("model") or {})

    @property
    def model_fingerprint(self) -> str:
        """The model's honest name, as recorded. Falls back to the model
        dict's own name for manifests written before the fingerprint joined
        :attr:`fingerprints`."""
        recorded = self._doc.get("fingerprints", {}).get("model")
        if recorded is not None:
            return recorded
        return str(self.model.get("name", ""))

    # -- honesty about what it does not carry ------------------------------

    @property
    def gaps(self) -> list[str]:
        """What a reader needs from OUTSIDE this manifest, spelled out.

        Empty for a complete manifest. A gap is not a defect, since a
        hand-written agent is the escape hatch working as designed, but it is a fact the
        reader needs, so the manifest states it rather than leaving it to be
        discovered.
        """
        out = []
        reference = self.strategy_reference
        if reference is not None:
            out.append(
                "strategy: referenced, not carried; a hand-written agent. "
                "The market replays in full (its orders are data in the "
                f"log), but re-running the strategy itself needs: {reference}"
            )
        return out

    @property
    def complete(self) -> bool:
        """True when every component is embedded or ships with the library,
        the condition under which this manifest alone reproduces the run."""
        return not self.gaps

    def describe(self) -> str:
        """A reader's summary: what is carried, what is referenced, and what
        checking it here would compare against."""
        doc = self._doc
        wrote = doc["written_by"]
        lines = [
            f"run manifest{f' {self.label!r}' if self.label else ''}: "
            f"seed {doc['seed']}, "
            f"{len(doc['universe']['instruments'])} instruments, "
            f"{doc['result']['days']} days, "
            f"{len(doc['order_log'])} log entries",
            f"  written by tradefloor {wrote['pretium_version']} on "
            f"{wrote['platform']['os']}-{wrote['platform']['machine']}, "
            f"model {wrote['model'].get('name')!r}, "
            f"era {wrote['era']['digest'][:12]}...",
            f"  universe: carried "
            f"({doc['fingerprints']['universe'][:12]}...)"
            + (f", built from {_canonical(doc['universe_source'])}"
               if doc.get("universe_source") is not None else ""),
            "  macro: " + ("carried" if doc.get("macro") is not None
                           else "engine defaults"),
            "  scenario: " + (
                "carried, realised path"
                if doc.get("scenario") is not None else "none"),
        ]
        parent = self.derived_from
        if parent is not None:
            named = f" {parent['label']!r}" if parent["label"] else ""
            lines.append(
                f"  branched from checkpoint{named} "
                f"({parent['checkpoint'][:12]}...) at entry "
                f"{parent['entries']}")
        if self.strategy is not None:
            lines.append(
                f"  strategy: carried spec "
                f"({doc['fingerprints']['strategy'][:12]}...)")
        elif self.strategy_reference is not None:
            lines.append(
                f"  strategy: REFERENCED, not carried: "
                f"{self.strategy_reference}")
        else:
            lines.append("  strategy: none")
        lines.append(
            f"  result: market digest {doc['result']['digest'][:12]}..., "
            f"{doc['result']['draws_consumed']} draws")
        for gap in self.gaps:
            lines.append(f"  incomplete: {gap}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        label = f"{self.label!r}, " if self.label else ""
        state = "complete" if self.complete else "INCOMPLETE"
        return (f"RunManifest({label}seed={self.seed}, "
                f"{len(self._doc['universe']['instruments'])} instruments, "
                f"{self._doc['result']['days']} days, {state})")
