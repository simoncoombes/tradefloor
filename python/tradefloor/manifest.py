"""One object a stranger can reproduce a run from, and know that they did.

`tradefloor-docs: docs/reproducing-a-run.md` lists the five things that
identify a run and shows a careful reader how to archive and check each one
by hand. This module is that page as a single artifact:

```python
manifest = tf.RunManifest.of(engine, seed=42, universe=u, macro=m)
open("run.json", "wb").write(manifest.to_json().encode("utf-8"))

# ...anywhere else, with nothing but the package and the file...
same = tf.RunManifest.from_json(open("run.json").read()).reproduce()
```

`reproduce()` replays the run and checks the result against the digest the
manifest carries, so the reader is TOLD whether they rebuilt the same market
rather than eyeballing numbers off a page. On success the returned engine is
the published market, bit for bit. On any mismatch it raises, and the error
names the component that disagreed, so every component carries its own
fingerprint rather than one hash over the whole file.

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

## Sampled verification, for a run too long to replay

`reproduce()` replays the whole run, so a 252-day manifest costs 252 days to
check. A reader who wants evidence for a fraction of that cost has
:class:`DayLedger`: the run takes a canonical hash of the engine's state at
every close, the manifest carries the Merkle root over those leaves, and
:func:`verify` recomputes k random days from their committed predecessors.
Checking k days costs k days of simulation, whatever the length of the run.

The two checks measure different things and both are here. The market digest
says the whole run rebuilds to the same market on this build. A sampled
verification says k named days recompute to the states committed for them,
and :attr:`Verification.caveats` names those days and states what the days
outside the sample rest on.

## What a successful reproduction proves about platforms

Cross-OS bit-identity is measured by commit. The five-target release gate
has run: at ``ad91026`` (known-answer v5, the RNG stream split), all five
targets (Linux x86_64 and aarch64, macOS arm64 and x86_64, and Windows
x86_64) produced the identical digest, ``76983e65...3180eeb``, each also
passing against the committed baseline. It has not yet run against a
tagged release, and the current digest, ``1ee64998...fe3581c`` at v8, was
regenerated on macOS arm64 and has one platform's confirmation behind it
until the gate runs again. ``tradefloor-docs: docs/reproducing-a-run.md``
keeps the full record. The manifest records the writer's platform and claims
nothing beyond that. What it offers instead is sharper: the manifest carries
the
expected output digest, so a successful ``reproduce()`` on a different
machine IS a cross-platform measurement for that run, made by the reader,
not promised by the library. A failure after every input verified is
reported as exactly that: an arithmetic divergence on an unmeasured pair,
with both platforms named.
"""

from __future__ import annotations

import base64
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
from .replay import apply_log, replay
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

#: Version of the per-day state hash a :class:`DayLedger` writes, recorded in
#: a manifest's ``days`` block. Bumped only when the HASH ITSELF changes,
#: since its leaves are then a new series and comparing across versions is
#: refused rather than reported as a tampered day it is not. Same discipline
#: as :data:`ERA_PROBE`.
STATE_HASH_VERSION = "state/1"

#: The eighteen engine columns, in the order `Engine::state_hash` walks them,
#: which is `python_engine::COLUMN_FIELDS` order. Declared here rather than
#: read off the snapshot, so a column added to the engine and not placed here
#: fails :func:`state_hash` by name instead of dropping out of every leaf.
_STATE_HASH_COLUMNS = (
    "price", "previous_close", "previous_tick_price", "open", "high", "low",
    "volume", "avg_volume", "market_cap", "mispricing_s",
    "mispricing_s_prev_close", "mispricing_momentum", "last_daily_return",
    "maker_inventory", "garch_variance", "beta", "short_interest",
    "float_shares",
)

#: The economy's scalar fields, in the order `state_snapshot` declares them.
#: Two of them are not f64: ``oil_last_opec_day`` is an integer day and
#: ``market_pe`` is optional, so each is hashed with its own encoding.
_ECONOMY_FIELDS = (
    "federal_funds_rate", "prime_rate", "corporate_bond_yield",
    "treasury_yield_10y", "treasury_yield_2y", "mortgage_rate_30y",
    "cpi", "inflation_rate", "core_inflation",
    "gdp_growth", "gdp",
    "unemployment_rate", "jobs_created", "labor_force_participation",
    "usd_index", "oil_price", "gold_price", "copper_price",
    "housing_index", "home_starts_monthly", "housing_transaction_volume",
    "long_term_unemployment_rate", "structural_unemployment",
    "consumer_confidence", "business_confidence", "fear_greed_index", "vix",
    "tariff_rate", "trade_balance",
    "oil_inventory_level", "oil_last_opec_day",
    "wage_growth",
    "previous_day_market_return", "rolling_market_return_30d",
    "market_pe", "qe_pe_boost",
    "fiscal_stimulus", "government_debt_to_gdp",
    "months_in_current_phase", "recession_probability",
)

#: Every key the economy sub-dict carries: the scalars above, the four-point
#: GDP trend and the cycle phase.
_ECONOMY_KEYS = _ECONOMY_FIELDS + ("gdp_trend", "cycle_phase")

#: The central bank's fields, in `state_snapshot` order.
_CENTRAL_BANK_FIELDS = (
    "last_meeting_date", "next_meeting_date", "target_inflation",
    "target_unemployment", "qe_active", "qe_monthly_purchases",
    "hawkish_dovish_score", "forward_guidance",
)

#: Every key `Engine.state_snapshot` carries. :func:`state_hash` checks a
#: snapshot against this before hashing, so a field added to the engine
#: raises here rather than being left silently out of every leaf a ledger
#: holds -- the failure `state_snapshot` itself has had six times.
_SNAPSHOT_KEYS = (
    "columns", "rng", "tickers", "model_fingerprint",
    "attribution", "tick_components", "tick_fundamental", "tick_anchor",
    "market_open", "market_variance", "forced_flow_spent", "volume_state",
    "universe_stress", "volume_idio", "session_news", "economy",
    "central_bank", "day_count",
    # Added by the draw-addressing layer, and hashed for the reason the
    # refusal above exists: an installed overlay decides what the engine
    # draws next, so two states alike in every column but one patched draw
    # are not the same state.
    "draw_counts", "draw_overlay",
)

#: The generator sequence :func:`verify` draws its sample of days from.
#: The library's own PCG32 rather than `random`, because a verification is
#: reproducible only if the days it sampled are: the same seed must name the
#: same days on every platform and every Python version, and the standard
#: library promises that of neither.
_VERIFY_STREAM = 909

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


def _bits(buf: bytearray, value: float) -> None:
    """One f64 as its raw bit pattern, big-endian, NaN payload included.

    For a value that is a bit pattern wearing a float, which is how a
    generator state crosses into Python: a PCG32 state is a u64 and a u64
    does not survive a Python float. Roughly one u64 in a thousand reads as a
    NaN, so putting one of these through :func:`_f64` would map distinct
    generator positions onto one digest.
    """
    buf.extend(struct.pack(">d", value))


def _u32(buf: bytearray, value: int) -> None:
    buf.extend(struct.pack(">I", int(value)))


def _u64(buf: bytearray, value: int) -> None:
    buf.extend(struct.pack(">Q", int(value)))


def _i64(buf: bytearray, value: int) -> None:
    buf.extend(struct.pack(">q", int(value)))


def _flag(buf: bytearray, value: bool) -> None:
    buf.append(1 if value else 0)


def _text(buf: bytearray, value: str) -> None:
    """A string, length-prefixed, so two adjacent strings cannot be re-split.

    Without the prefix "AB" then "C" and "A" then "BC" write the same bytes,
    and a roster renamed across that boundary would hash unchanged.
    """
    encoded = str(value).encode("utf-8")
    _u32(buf, len(encoded))
    buf.extend(encoded)


def _maybe_f64(buf: bytearray, value: float | None) -> None:
    if value is None:
        _flag(buf, False)
    else:
        _flag(buf, True)
        _f64(buf, value)


def _maybe_text(buf: bytearray, value: str | None) -> None:
    if value is None:
        _flag(buf, False)
    else:
        _flag(buf, True)
        _text(buf, value)


def _column(buffer: bytes, count: int, name: str) -> tuple[float, ...]:
    """Decode one of a snapshot's transport buffers.

    The buffers cross the boundary as LITTLE-endian bytes, which is the
    machine's layout and not the digest's. Hashing them as they arrive would
    write a digest that agrees with itself on one endianness and with nothing
    else, so every value is decoded here and re-encoded big-endian by
    :func:`_f64`.
    """
    if len(buffer) != count * 8:
        raise ValidationError(
            f"snapshot column {name!r} carries {len(buffer)} bytes and this "
            f"roster needs {count * 8}. The snapshot was written for a "
            "different roster, or truncated in transit."
        )
    return struct.unpack("<%dd" % count, buffer)


def state_hash(snapshot: dict[str, Any]) -> str:
    """sha256 over an engine's state: the per-day ledger leaf, in Python.

    The twin of ``Engine.state_hash``, computed from
    ``Engine.state_snapshot()`` rather than from the engine, and a test holds
    the two equal. It exists so a reader can check a ledger's leaves against
    an archived snapshot with the package alone, and so the encoding has a
    second implementation that a divergence between the two would expose.

    ## What it covers

    Every field the snapshot carries, in one fixed order: the eighteen
    columns instrument by instrument, the seven generator states, the roster
    and the model fingerprint, the day accumulators and the market-open flag,
    the market factor's variance, the volume states, the universe stress, the
    forced-flow budget, the day's endogenous news, the economy in declared
    order, the central bank and the day counter.

    ``market_digest`` covers nine columns and the draw count, which is what a
    published result is checked against. This covers the macro chain and the
    generator positions on top, because a ledger commits to the state the
    NEXT day starts from, and two runs can print the same prices today while
    holding different state for tomorrow.

    ## What it cannot say

    It is a hash of state, so history is outside it: the order log, the
    recorded tape and the pending daily jump are not in a snapshot and are
    not in this. Two engines that reached one state by different routes hash
    the same, which is the property that lets a replayed day be checked
    against a recorded one.

    One difference is worth knowing before two runs are compared.
    ``run_session(close_at_end=True)`` leaves the binding's session flag set
    where ``close_market()`` clears it, so the two spellings of one close
    hash apart on a market that is otherwise identical to the bit. The flag
    is state rather than bookkeeping: it decides whether the next session
    re-opens the day and re-anchors ``previous_close``. A recorded run still
    verifies against itself either way, because a replay runs the spelling
    its own log holds.

    It hashes each per-slot array at the width the snapshot carries, and
    refuses a snapshot whose arrays disagree with the roster, since it
    decodes each one against a length it computes from the roster. That is
    the invariant, whatever the roster does.

    Every per-slot array follows the roster, ``volume_idio`` included since
    the resize that landed with this one, so a snapshot taken after a
    listing or a delisting is accepted and a run whose roster changed is
    checked here like any other.

    ## The encoding

    Every float is eight bytes big-endian with one canonical NaN pattern, the
    rule :func:`_f64` and ``tests/known_answer.py`` share. The generator
    states are raw bit patterns instead; :func:`_bits` says why. Strings are
    length-prefixed, a bool is one byte, and an optional value is a presence
    byte followed by the value when it is there.

    A snapshot carrying a key this function does not know, or missing one it
    does, is refused by name. The alternative is a leaf that silently stops
    covering a field the engine grew, which is the failure
    ``state_snapshot`` has had six times.
    """
    if not isinstance(snapshot, dict):
        raise ValidationError(
            "state_hash takes the dict Engine.state_snapshot() returns, not a "
            f"{type(snapshot).__name__}."
        )
    carried = set(snapshot)
    expected = set(_SNAPSHOT_KEYS)
    if carried != expected:
        missing = sorted(expected - carried)
        extra = sorted(carried - expected)
        raise ValidationError(
            "this snapshot does not match the fields the state hash covers: "
            f"missing {missing}, unexpected {extra}. A leaf that skipped a "
            "field the engine carries would commit to a state it does not "
            "describe, so the hash refuses rather than hashing what it "
            "recognises."
        )

    tickers = list(snapshot["tickers"])
    n = len(tickers)
    buf = bytearray()

    columns = snapshot["columns"]
    if set(columns) != set(_STATE_HASH_COLUMNS):
        raise ValidationError(
            "this snapshot's columns are not the eighteen the state hash "
            "covers: missing "
            f"{sorted(set(_STATE_HASH_COLUMNS) - set(columns))}, unexpected "
            f"{sorted(set(columns) - set(_STATE_HASH_COLUMNS))}."
        )
    decoded = [_column(columns[name], n, name) for name in _STATE_HASH_COLUMNS]
    for i in range(n):
        for column in decoded:
            _f64(buf, column[i])

    rng = list(snapshot["rng"])
    if len(rng) != 21:
        raise ValidationError(
            f"this snapshot carries {len(rng)} generator numbers and the "
            "state hash covers 21: seven streams of state, increment and "
            "Box-Muller spare. A snapshot from an earlier stream layout "
            "froze a market this hash cannot describe."
        )
    for value in rng:
        _bits(buf, value)

    _u32(buf, n)
    for ticker in tickers:
        _text(buf, ticker)
    _text(buf, snapshot["model_fingerprint"])

    for name, width in (("attribution", 9), ("tick_components", 8),
                        ("tick_fundamental", 1), ("tick_anchor", 1)):
        for value in _column(snapshot[name], n * width, name):
            _f64(buf, value)
    _flag(buf, bool(snapshot["market_open"]))

    variance = list(snapshot["market_variance"])
    if len(variance) != 6:
        raise ValidationError(
            f"market_variance carries {len(variance)} numbers and the state "
            "hash covers six."
        )
    for value in variance:
        _f64(buf, value)
    _f64(buf, snapshot["volume_state"])
    for value in _column(snapshot["volume_idio"], n, "volume_idio"):
        _f64(buf, value)
    _f64(buf, snapshot["universe_stress"])
    _f64(buf, snapshot["forced_flow_spent"])

    news = list(snapshot["session_news"])
    _u32(buf, len(news))
    for event in news:
        _maybe_text(buf, event["ticker"])
        _maybe_text(buf, event["sector"])
        _maybe_f64(buf, event["price_impact"])

    economy = snapshot["economy"]
    if set(economy) != set(_ECONOMY_KEYS):
        raise ValidationError(
            "this snapshot's economy is not the one the state hash covers: "
            f"missing {sorted(set(_ECONOMY_KEYS) - set(economy))}, unexpected "
            f"{sorted(set(economy) - set(_ECONOMY_KEYS))}."
        )
    for name in _ECONOMY_FIELDS:
        value = economy[name]
        if name == "oil_last_opec_day":
            _i64(buf, value)
        elif name == "market_pe":
            _maybe_f64(buf, value)
        else:
            _f64(buf, value)
    trend = list(economy["gdp_trend"])
    if len(trend) != 4:
        raise ValidationError(
            f"gdp_trend carries {len(trend)} points and the state hash "
            "covers four."
        )
    for value in trend:
        _f64(buf, value)
    _text(buf, economy["cycle_phase"])

    bank = snapshot["central_bank"]
    if set(bank) != set(_CENTRAL_BANK_FIELDS):
        raise ValidationError(
            "this snapshot's central bank is not the one the state hash "
            "covers: missing "
            f"{sorted(set(_CENTRAL_BANK_FIELDS) - set(bank))}, unexpected "
            f"{sorted(set(bank) - set(_CENTRAL_BANK_FIELDS))}."
        )
    _i64(buf, bank["last_meeting_date"])
    _i64(buf, bank["next_meeting_date"])
    _f64(buf, bank["target_inflation"])
    _f64(buf, bank["target_unemployment"])
    _flag(buf, bool(bank["qe_active"]))
    _f64(buf, bank["qe_monthly_purchases"])
    _f64(buf, bank["hawkish_dovish_score"])
    _text(buf, bank["forward_guidance"])

    _u32(buf, snapshot["day_count"])

    # The draw-addressing layer's two fields, in the order the snapshot
    # carries them: the seven streams' uniform and normal positions
    # flattened in pairs, then the substitutions installed on them. Both
    # decide what the engine draws next, so a leaf that skipped them would
    # call two states the same when one is patched and the other is not.
    counts = list(snapshot["draw_counts"])
    if len(counts) != 14:
        raise ValidationError(
            "draw_counts must be 14 numbers, a uniform and a normal count "
            f"for each of the seven streams, got {len(counts)}."
        )
    for value in counts:
        _f64(buf, value)
    overlay = list(snapshot["draw_overlay"])
    _u32(buf, len(overlay))
    for entry in overlay:
        if len(entry) != 4:
            raise ValidationError(
                "each draw_overlay entry is (stream, kind, index, value), "
                f"got {len(entry)} fields."
            )
        stream, kind, index, value = entry
        _u32(buf, stream)
        _u32(buf, kind)
        _u64(buf, index)
        _f64(buf, value)
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



#: Schema of the JSON a :class:`DayLedger` writes.
LEDGER_SCHEMA = 1

#: Snapshot keys whose value is a buffer of little-endian f64s. They travel
#: base64-encoded, because the exact bits have to survive: a NaN in
#: ``tick_fundamental`` or in the ``rng`` array is a value, and JSON's own
#: float syntax would round-trip it as some other NaN.
_LEDGER_BUFFERS = ("attribution", "tick_components", "tick_fundamental",
                   "tick_anchor", "volume_idio")


#: The characters a leaf may be built from. A state hash is lowercase hex,
#: which is what `hashlib.hexdigest` produces and what `bytes.fromhex` will
#: take without complaint on either case.
_HEX = frozenset("0123456789abcdef")


def _is_leaf(value: Any) -> bool:
    """Whether this is a state hash rather than something that resembles one.

    64 lowercase hex characters. Anything shorter still enters the tree and
    produces a root, so a ledger holding one would verify against itself and
    commit to nothing.
    """
    return (isinstance(value, str) and len(value) == 64
            and _HEX.issuperset(value))


def _merkle_levels(leaves: Sequence[str]) -> list[list[bytes]]:
    """Every level of the tree, leaves first, root last.

    Binary, with duplicate-last padding: an odd level pairs its final node
    with itself. The alternative, promoting a lone node to the next level,
    makes two different leaf counts produce one root, so a ledger could be
    truncated and still verify.
    """
    level = [bytes.fromhex(leaf) for leaf in leaves]
    levels = [level]
    while len(level) > 1:
        if len(level) % 2:
            level = level + [level[-1]]
        level = [hashlib.sha256(level[i] + level[i + 1]).digest()
                 for i in range(0, len(level), 2)]
        levels.append(level)
    return levels


def _proof_holds(leaf: str, day: int, proof: Sequence[str], root: str) -> bool:
    """Recompute the root from one leaf and its siblings.

    The direction at each level comes from the index rather than from the
    proof, so a proof is a list of hashes and cannot claim a position its
    day does not have.
    """
    try:
        node = bytes.fromhex(leaf)
        siblings = [bytes.fromhex(s) for s in proof]
    except ValueError:
        return False
    index = day
    for sibling in siblings:
        pair = (node + sibling) if index % 2 == 0 else (sibling + node)
        node = hashlib.sha256(pair).digest()
        index //= 2
    return node.hex() == root


class DayLedger:
    """The per-day state hashes of one run, and the tree over them.

    A leaf is ``Engine.state_hash()`` taken after a day's close, and the root
    of the binary tree over the leaves is what a :class:`RunManifest` carries.
    The manifest stays a document a person can read: a year of snapshots at
    forty names is several megabytes, so the states live here, beside the
    manifest rather than inside it.

    ```python
    ledger = tf.DayLedger()
    engine.run_days(60, ledger=ledger)
    manifest = tf.RunManifest.of(engine, seed=42, universe=roster,
                                 ledger=ledger)
    open("ledger.json", "wb").write(ledger.to_json().encode("utf-8"))
    ```

    ## What the snapshots buy

    With them, checking day d costs one day of simulation: the verifier
    restores day d - 1 and replays day d. Without them it costs d days,
    because the only way to reach day d - 1 is to run to it, and
    :class:`Verification` says which of the two it paid. ``snapshots=False``
    is for a ledger that has to stay small and whose days will be checked
    rarely.

    The size is what decides between them, and it is why the states sit
    here rather than inside the manifest. On ``Universe.random(40,
    seed=7)``, seed 42, 252 days at 30 ticks a day with ``record=False``,
    at ``fd7b6dc``: the ledger writes 4,880,447 bytes with the states and
    16,924 without them, beside a 61,781-byte manifest. The run shape
    belongs in that sentence, because ``record=True`` takes the manifest to
    68,223 bytes and leaves the ledger where it is. A manifest is meant to
    be read, so it carries the root alone.

    ## The leaf is taken after the close

    Not after ``record``, so a run that never recorded a tape still ledgers,
    and the state a leaf commits to is the one the next day starts from.
    That is what makes day d checkable from day d - 1.
    """

    __slots__ = ("leaves", "snapshots")

    def __init__(self, *, snapshots: bool = True) -> None:
        self.leaves: list[str] = []
        self.snapshots: list[dict[str, Any]] | None = [] if snapshots else None

    # -- writing -----------------------------------------------------------

    @property
    def keeps_snapshots(self) -> bool:
        """Whether this ledger stores the state behind each leaf.

        Read by ``Engine.run_days`` before its day loop, so the Rust side
        knows whether to build a snapshot it would otherwise discard.
        """
        return self.snapshots is not None

    @property
    def count(self) -> int:
        return len(self.leaves)

    def close(self, engine: Engine) -> None:
        """Take this engine's leaf. Called at every close boundary."""
        self._close(engine.state_hash(),
                    engine.state_snapshot() if self.keeps_snapshots else None)

    def _close(self, leaf: str, snapshot: dict[str, Any] | None) -> None:
        """The protocol ``Engine.run_days`` calls from its Rust day loop.

        Two arguments rather than the engine, because the day loop holds a
        `&mut PyEngine` and no Python handle to hand back.
        """
        if self.snapshots is not None and snapshot is None:
            raise ValidationError(
                "this ledger keeps snapshots and was handed a leaf without "
                "one. A ledger half of whose days carry state would cost one "
                "day to check for some days and the whole run for others, "
                "with nothing recording which."
            )
        self.leaves.append(str(leaf))
        if self.snapshots is not None:
            self.snapshots.append(snapshot)

    # -- the tree ----------------------------------------------------------

    def root(self) -> str:
        """The Merkle root over every leaf, as hex."""
        if not self.leaves:
            raise ValidationError(
                "this ledger holds no days, so it has no root. A ledger is "
                "filled at each close boundary; this run crossed none, or "
                "the ledger was not passed to the loop that ran it."
            )
        return _merkle_levels(self.leaves)[-1][0].hex()

    def proof(self, day: int) -> list[str]:
        """The sibling hashes that carry day ``day`` up to the root.

        Checked with the root and the leaf, this says the leaf was committed
        at that position. It says nothing about the day recomputing to it,
        which is what :func:`verify` measures.
        """
        if not 0 <= day < len(self.leaves):
            raise ValidationError(
                f"day {day} is outside this ledger, which holds "
                f"{len(self.leaves)} days numbered 0 to "
                f"{len(self.leaves) - 1}."
            )
        out: list[str] = []
        index = day
        for level in _merkle_levels(self.leaves)[:-1]:
            padded = level + [level[-1]] if len(level) % 2 else level
            out.append(padded[index ^ 1].hex())
            index //= 2
        return out

    # -- serialisation -----------------------------------------------------

    def to_json(self, *, with_snapshots: bool = True) -> str:
        """The ledger as JSON: the file that travels beside a manifest.

        ``with_snapshots=False`` writes the leaves alone, which is the small
        artifact. A ledger that never held snapshots writes none either way,
        and :func:`verify` reports the cost that follows from what it finds.

        The f64 buffers travel base64-encoded rather than as JSON numbers,
        because ``tick_fundamental`` and the generator array carry NaN as a
        VALUE and JSON's float syntax cannot round-trip one.
        """
        payload: dict[str, Any] = {
            "schema": LEDGER_SCHEMA,
            "hash": STATE_HASH_VERSION,
            "leaves": list(self.leaves),
        }
        if with_snapshots and self.snapshots is not None:
            payload["snapshots"] = [_snapshot_to_json(s)
                                    for s in self.snapshots]
        return _canonical(payload)

    @classmethod
    def from_json(cls, text: str) -> "DayLedger":
        """Load a ledger written by :meth:`to_json`.

        Refuses a hash version this build does not compute, by name: a leaf
        from another version of the state hash is a different measurement,
        and checking a day against one would report a tampered day that is
        not. Refuses a leaf that is not 64 lowercase hex characters, by
        position, for the reason :func:`_is_leaf` gives.
        """
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValidationError(
                f"this is not JSON, so it is not a day ledger: {exc}"
            ) from exc
        if not isinstance(payload, dict) or "leaves" not in payload:
            raise ValidationError(
                "this is not a day ledger: a ledger is a JSON object with a "
                "list of leaves and a hash version."
            )
        schema = payload.get("schema", 0)
        if schema > LEDGER_SCHEMA:
            raise ValidationError(
                f"ledger schema {schema} is newer than this version "
                "understands. Upgrade tradefloor rather than reading it "
                "partially."
            )
        version = payload.get("hash")
        if version != STATE_HASH_VERSION:
            raise ValidationError(
                f"this ledger's leaves are {version!r} hashes and this build "
                f"computes {STATE_HASH_VERSION!r}. The two are different "
                "measurements, so every day would report as tampered."
            )
        leaves = list(payload["leaves"])
        for position, leaf in enumerate(leaves):
            if not _is_leaf(leaf):
                raise ValidationError(
                    f"leaf {position} of this ledger is {leaf!r}, which is "
                    "not a state hash: a leaf is 64 lowercase hex "
                    "characters. A shorter one still hashes into the tree "
                    "and produces a root, so the file is refused here rather "
                    "than checked against."
                )
        raw = payload.get("snapshots")
        ledger = cls(snapshots=raw is not None)
        ledger.leaves = leaves
        if raw is not None:
            if len(raw) != len(ledger.leaves):
                raise ValidationError(
                    f"this ledger holds {len(ledger.leaves)} leaves and "
                    f"{len(raw)} snapshots. One of the two was truncated, and "
                    "a snapshot read against the wrong day would report a "
                    "tampered run."
                )
            ledger.snapshots = [_snapshot_from_json(s) for s in raw]
        return ledger

    def __len__(self) -> int:
        return len(self.leaves)

    def __repr__(self) -> str:
        held = "with snapshots" if self.keeps_snapshots else "leaves only"
        root = self.root()[:12] + "..." if self.leaves else "empty"
        return f"DayLedger({len(self.leaves)} days, {held}, root {root})"


def _snapshot_to_json(snapshot: dict[str, Any]) -> dict[str, Any]:
    """One state snapshot in a form JSON can carry losslessly."""
    out = dict(snapshot)
    out["columns"] = {name: base64.b64encode(buf).decode("ascii")
                      for name, buf in snapshot["columns"].items()}
    for name in _LEDGER_BUFFERS:
        out[name] = base64.b64encode(snapshot[name]).decode("ascii")
    values = list(snapshot["rng"])
    out["rng"] = base64.b64encode(
        struct.pack("<%dd" % len(values), *values)).decode("ascii")
    return out


def _snapshot_from_json(payload: dict[str, Any]) -> dict[str, Any]:
    """The inverse of :func:`_snapshot_to_json`, ready for a restore."""
    out = dict(payload)
    out["columns"] = {name: base64.b64decode(text)
                      for name, text in payload["columns"].items()}
    for name in _LEDGER_BUFFERS:
        out[name] = base64.b64decode(payload[name])
    raw = base64.b64decode(payload["rng"])
    out["rng"] = list(struct.unpack("<%dd" % (len(raw) // 8), raw))
    return out


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
           derived_from: Any = None,
           ledger: "DayLedger | None" = None) -> "RunManifest":
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

        ``ledger`` is the :class:`DayLedger` the run filled, and it adds one
        ``days`` block holding the Merkle root over the per-day state hashes,
        the day count and the hash version. The manifest carries the root
        alone; the leaves and the states stay in the ledger, because a year
        of snapshots at forty names is several megabytes and a manifest is
        meant to be read. :func:`verify` is what the block is for.
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
        if ledger is not None:
            # A ledger and a log that disagree about how many days the run
            # crossed describe two different runs, and pairing them would
            # check day d of one against day d of the other. Counted from the
            # log rather than from `result["days"]`, which counts opens: a run
            # that opened a day and stopped inside it has no leaf for it.
            boundaries = sum(1 for entry in log if _is_close(entry))
            if ledger.count != boundaries:
                raise ValidationError(
                    f"this ledger holds {ledger.count} days and the run's log "
                    f"crosses {boundaries} day boundaries. The ledger was "
                    "filled by a different run, or it was not passed to the "
                    "loop that ran this one."
                )
            if ledger.count == 0:
                raise ValidationError(
                    "this ledger holds no days, so it commits to nothing. A "
                    "leaf is taken at a close boundary and this run crossed "
                    "none."
                )
            doc["day_ledger"] = {
                "root": ledger.root(),
                "count": ledger.count,
                "hash": STATE_HASH_VERSION,
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
                    "about history, checked when it is made rather "
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

        block = payload.get("day_ledger")
        if block is not None:
            # Shape only. An unknown hash version is left for `verify` to
            # refuse by name, because a manifest whose leaves this build
            # cannot recompute still reproduces: the ledger is additive and
            # `reproduce()` never reads it.
            if not isinstance(block, dict) or set(block) != {"root", "count",
                                                             "hash"}:
                raise ValidationError(
                    "this manifest's day_ledger block is not the three "
                    "fields it should carry (root, count, hash). It was "
                    "edited in transit, or written by something that is not "
                    "tradefloor."
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
            "do this, so the release profile forbids them); a "
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
        checkpoint can, and this test covers it -- the fingerprint must match,
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
    def day_ledger(self) -> dict[str, Any] | None:
        """The run's per-day commitment, or ``None`` when it has none.

        ``{"root": <Merkle root>, "count": <days>, "hash": "state/1"}``,
        under the document's ``day_ledger`` key. Named apart from
        ``result["days"]``, which is the number of days the run traded: one
        is a count and the other is a commitment, and a document that
        answered to ``days`` twice at two levels would make a reader work
        out which one they had opened.

        :func:`verify` pairs this with a :class:`DayLedger` and recomputes a
        sample of the days it commits to.
        """
        recorded = self._doc.get("day_ledger")
        return dict(recorded) if recorded else None

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

        Empty for a complete manifest. A gap is no defect, since a
        hand-written agent is the escape hatch working as designed, but the
        reader needs the fact, so the manifest states it rather than leaving
        it to be discovered.
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

# -- sampled verification ---------------------------------------------------


def _is_close(entry: dict[str, Any]) -> bool:
    """Whether this entry is a day boundary.

    Two spellings of one close, which the engine keeps equivalent:
    ``close_market`` on its own, and ``run_session`` with ``close_at_end``.
    A verifier that knew only the first would read a session-closed run as
    one long day.
    """
    op = entry.get("op")
    return op == "close_market" or (op == "run_session"
                                    and bool(entry.get("close_at_end")))


def _day_spans(log: Sequence[dict[str, Any]]) -> list[tuple[int, int]]:
    """Half-open index ranges, one per closed day.

    A day runs from the entry after the previous close through its own
    close. That is wider than the open-to-close window, deliberately: a
    scenario writes ``pin_macro`` BEFORE the market opens and a listing can
    land there too, so a segment that started at ``open_market`` would replay
    the day under yesterday's macro path and diverge for a reason that has
    nothing to do with tampering.

    Entries after the last close belong to no day. A run stopped mid-day has
    no leaf for it, because a leaf is taken at a close.
    """
    spans: list[tuple[int, int]] = []
    start = 0
    for i, entry in enumerate(log):
        if _is_close(entry):
            spans.append((start, i + 1))
            start = i + 1
    return spans


def _tick_count(entries: Sequence[dict[str, Any]]) -> int:
    """Engine ticks in a segment of log entries.

    The unit the cost claim is made in: a session carries its tick count and
    a ``tick`` entry is one. Draw counts would say the same thing less
    directly, since a closed market draws nothing.
    """
    total = 0
    for entry in entries:
        op = entry.get("op")
        if op == "tick":
            total += 1
        elif op == "run_session":
            total += int(entry.get("ticks", 0))
    return total


def _sample_days(count: int, k: int, seed: int) -> list[int]:
    """``k`` distinct days from ``range(count)``, reproducibly.

    A partial Fisher-Yates shuffle over the library's own PCG32. The standard
    library's generator would do the arithmetic, and its ``sample`` is not a
    published sequence: a verification whose sampled days moved between
    Python versions could not be repeated by the reader it was reported to.
    """
    rng = GameRng(int(seed) & 0xFFFFFFFF, _VERIFY_STREAM)
    pool = list(range(count))
    for i in range(k):
        j = i + int(rng.next_int(0, count - 1 - i))
        pool[i], pool[j] = pool[j], pool[i]
    return sorted(pool[:k])


def _count(n: int, noun: str) -> str:
    """``1 day``, ``3 days``. A caveat is read by a person.

    Written out because these strings are UI copy under `CONTENT.md`, and
    "1 days cost 1 day-runs" is the shape a caveat takes when a number is
    interpolated in front of a hardcoded plural.
    """
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


class Verification:
    """What a sampled verification measured, and over what.

    Returned by :func:`verify`. It reports rather than raising because k and
    the days drawn are part of the answer on a pass: "this run verifies" is a
    different claim from "these four of sixty days recompute on this build",
    and only the second is true. :meth:`check` raises for a caller that wants
    the failure to end the program.
    """

    __slots__ = ("days", "k", "count", "ticks", "day_runs", "restored",
                 "root_ok", "root_note", "replay_failures", "proof_failures",
                 "from_snapshots", "root", "_wrote", "_here")

    def __init__(self, *, days: Sequence[int], count: int, ticks: int,
                 day_runs: int, restored: int, root_ok: bool,
                 root_note: str, replay_failures: Sequence[str],
                 proof_failures: Sequence[str],
                 from_snapshots: bool, root: str,
                 wrote: str, here: str) -> None:
        self.days = tuple(days)
        self.k = len(self.days)
        self.count = int(count)
        self.ticks = int(ticks)
        self.day_runs = int(day_runs)
        #: Sampled days that started from a committed predecessor. Day 0 has
        #: none and replays from construction, so a sample that drew it is
        #: short of k here even on a ledger that carries every state.
        self.restored = int(restored)
        #: Whether the ledger's own root is the one the manifest commits to.
        #: Kept apart from the per-day results, because it is one fact about
        #: the whole ledger rather than a verdict on any day: reported as a
        #: day it made a sample of nine read as ten failed days.
        self.root_ok = bool(root_ok)
        self.root_note = root_note
        #: Sampled days that did not replay to the state committed for them.
        #: This is the per-day evidence, and the only thing counted over k.
        self.replay_failures = tuple(replay_failures)
        #: Leaves that failed their Merkle proof under a root the ledger
        #: reproduces. Empty in every case the tree handles correctly; see
        #: :func:`verify` for why one here means the tree disagrees with
        #: itself rather than that a day was edited.
        self.proof_failures = tuple(proof_failures)
        self.from_snapshots = bool(from_snapshots)
        self.root = root
        self._wrote = wrote
        self._here = here

    @property
    def failures(self) -> tuple[str, ...]:
        """Everything wrong with this verification, in one list.

        The root first, then the days that did not replay, then any proof
        that failed under a matching root. Reading the length of this as a
        count of failed days is what produced "10 of 9 sampled days did not
        verify" on a nine-day ledger with one edited leaf, so the count in
        :meth:`check` runs over :attr:`replay_failures` alone.
        """
        root = () if self.root_ok else (self.root_note,)
        return root + self.replay_failures + self.proof_failures

    @property
    def replayed(self) -> int:
        """Sampled days that reproduced the state committed for them."""
        return self.k - len(self.replay_failures)

    @property
    def ok(self) -> bool:
        """True when the root matches and every sampled day recomputed."""
        return (self.root_ok and not self.replay_failures
                and not self.proof_failures)

    @property
    def caveats(self) -> list[str]:
        """What this particular verification does and does not establish.

        Computed from the call: the sample, the cost, the platforms and the
        span the hash covers. A caveat typed into this docstring would go on
        being printed after the thing it describes had changed.
        """
        if self.k == self.count:
            out = [
                "This verification recomputed every one of the "
                f"{_count(self.count, 'day')} in the ledger on this build, "
                "so the result rests on no sampling."
            ]
        else:
            out = [
                f"This verification recomputed {self.k} of the "
                f"{_count(self.count, 'day')} in the ledger on this build. "
                f"The root covers the other "
                f"{_count(self.count - self.k, 'day')}, recording the leaf "
                "committed at each position."
            ]
        cost = (f"The sample cost {_count(self.day_runs, 'day-run')} and "
                f"{_count(self.ticks, 'engine tick')}.")
        if not self.from_snapshots:
            out.append(
                "This ledger carries no snapshots, so each sampled day was "
                f"replayed from day 0. {cost} A ledger written with "
                "snapshots costs one day-run per sampled day."
            )
        elif self.restored == self.k:
            out.append("Each sampled day was replayed from its committed "
                       f"predecessor. {cost}")
        elif self.restored == 0:
            out.append(
                "Day 0 has no committed predecessor, so the sample was "
                f"replayed from construction. {cost}"
            )
        else:
            out.append(
                "Day 0 has no committed predecessor and was replayed from "
                f"construction. The other {_count(self.restored, 'day')} "
                f"started from a committed predecessor. {cost}"
            )
        out.append(
            "The leaf hashes engine state. The order log, the recorded tape "
            "and the pending daily jump sit outside it, so a day that "
            "reached the same state by another route verifies."
        )
        if self._wrote == self._here:
            out.append(
                f"The manifest was written on {self._wrote} and checked on "
                f"{self._here}, so this measures the build rather than a "
                "platform pair."
            )
        else:
            out.append(
                f"The manifest was written on {self._wrote} and checked on "
                f"{self._here}. Each day that recomputed here is a "
                "cross-platform measurement for that day, made by the reader."
            )
        return out

    def check(self) -> "Verification":
        """Raise when anything did not verify. Returns self otherwise.

        For a caller that wants the failure to end the program, in the shape
        :meth:`RunManifest.verify_lineage` uses. The message separates the
        one fact about the whole ledger from the verdict on each sampled day,
        because running them together counted a root mismatch as a tenth
        failed day on a sample of nine and read eight days that replayed
        perfectly as failures.
        """
        if self.ok:
            return self
        lines: list[str] = []
        if not self.root_ok:
            lines.append(self.root_note)
        if self.replay_failures:
            lines.append(
                f"{len(self.replay_failures)} of "
                f"{_count(self.k, 'sampled day')} did not replay to the "
                "state the ledger commits:")
            lines.extend("  " + entry for entry in self.replay_failures)
            if self.replayed:
                lines.append(
                    f"The remaining {self.replayed} replayed to the state "
                    "the ledger commits.")
        else:
            lines.append(
                f"Every one of the {_count(self.k, 'sampled day')} replayed "
                "to the state the ledger commits, so no day this sample drew "
                "was edited.")
        lines.extend(self.proof_failures)
        raise ValidationError("\n".join(lines))

    def describe(self) -> str:
        """A reader's summary: sample, cost, verdict and caveats."""
        verdict = "PASSED" if self.ok else "FAILED"
        lines = [
            f"sampled verification {verdict}: {self.k} of {self.count} days, "
            f"root {self.root[:12]}...",
            f"  days: {', '.join(str(d) for d in self.days)}",
            f"  cost: {_count(self.day_runs, 'day-run')}, "
            f"{_count(self.ticks, 'engine tick')}, "
            f"{self.restored} restored from a predecessor",
            f"  root: {'matches the manifest' if self.root_ok else 'MOVED'}",
            f"  replay: {self.replayed} of {self.k} sampled days reproduced "
            f"the committed state",
        ]
        if not self.root_ok:
            lines.append(f"  FAILED {self.root_note}")
        for failure in self.replay_failures:
            lines.append(f"  FAILED {failure}")
        for failure in self.proof_failures:
            lines.append(f"  FAILED {failure}")
        for caveat in self.caveats:
            lines.append(f"  caveat: {caveat}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (f"Verification({'ok' if self.ok else 'FAILED'}, "
                f"{self.replayed}/{self.k} days replayed, "
                f"root {'ok' if self.root_ok else 'MOVED'}, "
                f"{self.day_runs} day-runs)")


def verify(manifest: RunManifest, ledger: DayLedger, k: int, *,
           seed: int) -> Verification:
    """Recompute ``k`` random days of a recorded run and check them.

    ```python
    report = tf.manifest.verify(manifest, ledger, 4, seed=7)
    print(report.describe())
    ```

    ## What it measures

    For each sampled day d it restores the ledger's snapshot for day d - 1
    onto a fresh engine, replays day d's log entries, hashes the result and
    compares it with the ledger's leaf for day d. It then checks that leaf
    against the root the manifest carries, by its Merkle proof. Day 0
    replays from construction, which is its own predecessor.

    So a pass says two things about each sampled day: this build recomputes
    it to the same state, and that state was committed at that position when
    the manifest was written. A tampered day fails on its own leaf; a
    tampered predecessor state fails on the day that follows it.

    ## The cost

    With snapshots, checking k days costs k days of simulation, whatever the
    length of the run. A ledger without snapshots reaches day d - 1 by
    running to it, so day d costs d + 1 days, and
    :attr:`Verification.day_runs` reports which of the two was paid.
    :attr:`Verification.restored` counts the sampled days that started from a
    committed predecessor, which is every one of them except day 0.

    The unit is day-runs and engine ticks, and it is exact in those units.
    Wall time tracks it, and the ratio is the part worth quoting: seconds on
    one machine say as much about what else was running as about this
    function. On ``Universe.random(40, seed=7)``, seed 42, twenty days at
    390 ticks, at ``c40fd39``, with the three modes interleaved in one
    process and medians of seven, verifying every day costs 1.10 times what
    running those days live costs and ``reproduce()`` over the same log
    costs 1.01 times it. Two runs of that protocol on this machine an hour
    apart differed by a factor of two in seconds a day and by 0.03 in the
    first ratio, which is why the ratio is the number written down. An
    independent measurement on a second checkout of this branch gave 0.95
    for it, so read the figure as parity rather than to two decimals.

    ## What it cannot say

    Nothing about the days outside the sample beyond their membership in the
    root, and :attr:`Verification.caveats` says so with k and the day list
    filled in. ``reproduce()`` remains the whole-run check: it replays every
    day and compares the market digest at the end.

    ``seed`` chooses the sample and is required rather than defaulted, for
    the reason ``GameRng`` requires a sequence: a verification is repeatable
    only if the reader can name the days it drew, and a hidden default makes
    "four random days" a claim nobody can check.
    """
    manifest._check_era()

    recorded = manifest._doc.get("day_ledger")
    if recorded is None:
        raise ValidationError(
            "this manifest carries no day ledger, so there is nothing to "
            "verify against. Pass a DayLedger to RunManifest.of when the run "
            "is captured; reproduce() is the check for a manifest without "
            "one."
        )
    if recorded.get("hash") != STATE_HASH_VERSION:
        raise ValidationError(
            f"this manifest's leaves are {recorded.get('hash')!r} hashes and "
            f"this build computes {STATE_HASH_VERSION!r}. The two are "
            "different measurements, so every day would report as tampered."
        )
    if ledger.count != int(recorded["count"]):
        raise ValidationError(
            f"this manifest commits to {recorded['count']} days and the "
            f"ledger holds {ledger.count}. The two describe different runs, "
            "or one of them was truncated."
        )

    log = manifest.order_log
    spans = _day_spans(log)
    if len(spans) != ledger.count:
        raise ValidationError(
            f"this manifest's order log crosses {len(spans)} day boundaries "
            f"and the ledger holds {ledger.count} days. The log is not the "
            "one the ledger was written from."
        )
    if not 1 <= k <= ledger.count:
        raise ValidationError(
            f"k must be between 1 and the {ledger.count} days this ledger "
            f"holds, got {k}."
        )

    root = str(recorded["root"])
    # One fact about the whole ledger, kept out of the per-day list. Inside
    # this function it is also the membership answer for every day: a proof
    # is built from the leaves that produce the ledger's own root, so it
    # recomputes to that root and reaches the manifest's exactly when the two
    # agree. Appending a per-day proof failure beside it therefore added k
    # entries that repeated this one, and the count over them read a
    # nine-day sample as ten failed days.
    root_ok = ledger.root() == root
    root_note = "" if root_ok else (
        f"the ledger's root is {ledger.root()[:12]}... and the manifest "
        f"commits to {root[:12]}.... A leaf was edited after the manifest "
        "was written. Membership cannot be judged day by day against a root "
        "that does not match, so no proof is reported below and the replay "
        "verdicts are what say which day moved."
    )
    replay_failures: list[str] = []
    proof_failures: list[str] = []

    chosen = _sample_days(ledger.count, k, seed)
    universe = manifest.universe
    macro = manifest.macro
    model = manifest._model_for_replay()
    ticks = 0
    day_runs = 0
    restored = 0

    for day in chosen:
        engine = Engine(seed=manifest.seed, universe=universe,
                        macro_state=macro, model=model)
        if ledger.snapshots is not None and day > 0:
            start, end = spans[day]
            # The roster first, and only the roster. `restore_state` refuses a
            # snapshot whose tickers are not the engine's, because the columns
            # are positional -- so a run that listed or delisted a name before
            # this day has to reach the shape the snapshot was taken at. These
            # entries carry the fundamentals a column cannot (sector, earnings,
            # book value), and they take draws, which the restore below
            # overwrites along with the rest of the state.
            shape = [entry for entry in log[:start]
                     if entry.get("op") in ("list_instrument", "delist")]
            if shape:
                apply_log(engine, shape)
            engine.restore_state(ledger.snapshots[day - 1])
            day_runs += 1
            restored += 1
        else:
            start, end = 0, spans[day][1]
            day_runs += day + 1
        segment = log[start:end]
        ticks += _tick_count(segment)
        apply_log(engine, segment)

        rebuilt = engine.state_hash()
        leaf = ledger.leaves[day]
        if rebuilt != leaf:
            replay_failures.append(
                f"day {day}: replaying it produced state "
                f"{rebuilt[:12]}... and the ledger commits to "
                f"{leaf[:12]}.... Either this day's inputs changed, or the "
                f"state it started from did."
            )
            continue
        # Still checked, and reported only when it says something the root
        # comparison did not. A proof that fails under a root the ledger
        # reproduces means the tree disagrees with itself, which is a defect
        # in this module rather than evidence about the run.
        if root_ok and not _proof_holds(leaf, day, ledger.proof(day), root):
            proof_failures.append(
                f"day {day}: the leaf recomputes and its proof does not "
                f"reach the root {root[:12]}... that the ledger itself "
                "produces. The tree implementation disagrees with itself; "
                "this is a defect in tradefloor, not an edited run."
            )

    wrote = manifest.written_by.get("platform") or {}
    return Verification(
        days=chosen, count=ledger.count, ticks=ticks, day_runs=day_runs,
        restored=restored, root_ok=root_ok, root_note=root_note,
        replay_failures=replay_failures, proof_failures=proof_failures,
        from_snapshots=ledger.snapshots is not None,
        root=root,
        wrote=f"{wrote.get('os')}-{wrote.get('machine')}",
        here=f"{_platform.system()}-{_platform.machine()}",
    )
