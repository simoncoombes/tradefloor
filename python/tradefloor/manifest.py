"""Run manifests: one object anyone can reproduce a run from and check.

`tradefloor-docs: docs/reproducing-a-run.md` lists the five things that
identify a run and shows how to archive and check each one by hand. This
module does the same in one object:

```python
manifest = tf.RunManifest.of(engine, seed=42, universe=u, macro=m)
open("run.json", "wb").write(manifest.to_json().encode("utf-8"))

# ...anywhere else, with nothing but the package and the file...
same = tf.RunManifest.from_json(open("run.json").read()).reproduce()
```

`reproduce()` replays the run and checks the result against the digest the
manifest carries, so the reader is told whether they rebuilt the same market
and does not have to compare numbers by eye. On success the returned engine
is the published market, bit for bit. On any mismatch it raises, and the
error names the component that disagreed, because every component carries
its own fingerprint instead of one hash over the whole file.

## The completeness rule

A manifest reproduces if and only if every component is either shipped with
the library or embedded in the manifest. A fingerprint identifies but cannot
reconstruct, because you cannot invert a hash. So the manifest EMBEDS
everything user-supplied: the roster itself (never a recipe for one, since
generators change across versions, and an EDGAR query is not the data it
returned), the macro initial conditions, the realised scenario path, the
full order log, and the strategy when it is a :class:`StrategySpec`.

The one component that cannot always be embedded is a hand-written Python
agent. Pass a reference string ("repo X at commit Y") and the manifest
records the strategy as referenced, not carried. Such a manifest is
incomplete: its :attr:`~RunManifest.complete` is False and
:attr:`~RunManifest.gaps` says why. The market still reproduces, because the
agent's orders are data in the log. Without the referenced code the reader
cannot re-run the strategy itself on new inputs.
``Scorecard.strategy_fingerprint`` is empty for hand-written agents for the
same reason.

## The era, and why it is a measurement rather than a version number

A run is only reproducible on a build whose arithmetic matches the build that
ran it. "Across versions, not at all" is the documented guarantee, and it
has mattered. One calendar day brought three trajectory-changing fixes
(the macro-chain and volume fixes, then the market-factor-sigma
recalibration) while ``tf.version()`` stayed 0.1.0 and the preset stayed
"pt-v1". The recalibrated constant is not in the preset dictionary, so a
preset-value comparison did not change either. Every name the library could
quote held still while the numbers moved. A manifest that trusted names
would replay on the wrong build and produce a plausible but different
market.

So the era identity here is behavioural. :func:`era_fingerprint` runs a
small fixed simulation (generator draws, fair value across every sector,
the daily mispricing step, and a coupled engine run through day closes) and
digests it with the same canonical-f64 encoding as ``tests/known_answer.py``.
The test suite's ``KAT_VERSION`` is the same idea kept by convention, but it
lives in the test tree, which an installed wheel does not have, and a
convention depends on someone remembering to bump it. A digest does not.
Two builds that agree on the probe agree on the arithmetic the probe
exercises, and two that disagree will not reproduce each other's runs,
whatever their version strings say. ``reproduce()`` checks the probe before replaying
and refuses on a mismatch, naming both builds, as ``Checkpoint`` refuses to
run against the wrong build.

The manifest still records the package version, the preset name and the
full coefficient dictionary. A methods section quotes them, the coefficient
values name a mismatch when the model itself moved, and the embedded values
will let a future custom preset travel without a format change. None of
them is trusted as the era. Only the probe is.

## Sampled verification, for a run too long to replay

`reproduce()` replays the whole run, so a 252-day manifest costs 252 days to
check. For evidence at a fraction of that cost there is :class:`DayLedger`.
The run takes a canonical hash of the engine's state at every close, the
manifest carries the Merkle root over those leaves, and :func:`verify`
recomputes k random days from their committed predecessors.
Checking k days costs k days of simulation, whatever the length of the run.

The two checks measure different things and both are here. The market digest
says the whole run rebuilds to the same market on this build. A sampled
verification says k named days recompute to the states committed for them,
and :attr:`Verification.caveats` names those days and states what the days
outside the sample rest on.

## What a successful reproduction proves about platforms

Cross-OS bit-identity is measured by commit. The five-target release gate
has run. At ``ad91026`` (known-answer v5, the RNG stream split), all five
targets (Linux x86_64 and aarch64, macOS arm64 and x86_64, and Windows
x86_64) produced the identical digest, ``76983e65...3180eeb``, each also
passing against the committed baseline. It has not yet run against a
tagged release, and the current digest, ``1ee64998...fe3581c`` at v8, was
regenerated on macOS arm64 and has one platform's confirmation behind it
until the gate runs again. ``tradefloor-docs: docs/reproducing-a-run.md``
keeps the full record. The manifest records the writer's platform and claims
nothing beyond that. It does carry the expected output digest, so a
successful ``reproduce()`` on a different machine is a cross-platform
measurement for that run, made by the reader. A failure after every input
verified is reported as an arithmetic divergence on an unmeasured pair,
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
    check_seed,
    fair_value,
    model_preset,
    preset_names,
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
    "months_in_current_phase", "phase_gdp_target", "recession_probability",
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
    # The day's `random_noise` split, the scale its idiosyncratic part was
    # drawn at, and the jump waiting for the session that trades it in.
    # Per-day state that reaches a price: off zero on
    # `garch_innovation_commensurate` the close builds the per-name GJR
    # innovation out of the first two, and off 1.0 on
    # `volume_move_jump_share` the volume scale reads the third.
    "noise_parts", "noise_own_scale2", "jump_move",
    "market_open", "market_variance", "forced_flow_spent",
    "market_vol_log_level",
    "vix_log_level",
    # The crisis episode: whether one is running, how many consecutive
    # sessions it has spent under `crisis_vix_threshold`, the sector index
    # its epicentre was drawn at (-1 for `none`, a crisis with no
    # epicentre) and the pin a scenario set (-2 for no pin). All four are
    # the state a run with `crisis_epicentre_extra` off zero carries, and
    # all four read their defaults on every preset before pt-v19. pt-v19
    # and pt-v20 ship the dial at 1.93, so on the default they move
    # whenever the VIX crosses `crisis_vix_threshold`. This read "on every
    # shipped preset" until 2026-09-24.
    "crisis_in_episode", "crisis_sessions_under",
    "crisis_epicentre", "crisis_epicentre_pin",
    "nominal_output_base", "volume_state",
    "universe_stress", "volume_idio", "sector_variance", "jump_excitation",
    "sector_day_factor", "sector_target_day",
    "session_news", "economy",
    "central_bank", "day_count",
    # Added by the draw-addressing layer, and hashed for the reason the
    # refusal above exists: an installed overlay decides what the engine
    # draws next, so two states alike in every column but one patched draw
    # are not the same state.
    "draw_counts", "draw_overlay",
    # The day's jump and overnight move, waiting for the tape row that
    # carries them. They are applied at a day boundary and written onto the
    # FIRST TICK OF THE NEXT DAY, so between the close and that row they are
    # pending state -- and a snapshot that dropped them let a resumed run's
    # record lose a day's jump while the continuous run's kept it.
    #
    # Hashed for the same reason the overlay above is: they decide what the
    # NEXT day's tape says, so two states alike in every column and holding
    # different pending jumps are not the same state. A verification that
    # called them equal would be overclaiming.
    "pending_jump", "pending_overnight",
)

#: Snapshot keys the state hash accepts and does not cover. Three:
#: ``session_tick``, the ticks the day has run, which is the tick the book
#: stamps a fill with. It moves no price, and every snapshot of an open or a
#: closed day carries a count, so covering it would have moved every leaf
#: written before it was carried. A restore puts it back, which is what it
#: is carried for: a fill after a restore is stamped as the original's was.
#: And ``state_schema``, the snapshot's layout version, which describes the
#: dict rather than the market; snapshots written before it carried none and
#: hash as they did. And ``vix_anchor``, the VIX anchor `vix_level_identity`
#: derives from the roster the engine was built on: a constant of the run,
#: carried so an engine rebuilt on a later roster restores the run's own
#: (#268). Covering it would have moved every pt-v19 and later leaf written
#: before it was carried.
_UNHASHED_KEYS = ("session_tick", "state_schema", "vix_anchor")


def _default_day(day_count: int, market_open: bool) -> int:
    """The day an engine's label and valuation clock hold when nothing moved
    them off its counter: ``day_count`` while a session is open, the day just
    closed after a close, 0 before the first open. The engine's
    ``default_day``, which decides when a snapshot carries the two."""
    count = int(day_count)
    return count if market_open or count == 0 else count - 1


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


def _scenario_fingerprint(payload: dict[str, Any]) -> str:
    """The manifest's scenario fingerprint: the payload without `origins`.

    `origins` says where a scenario's days came from (the file as written,
    its own fingerprint and how far its days moved). That is provenance,
    not part of the experiment, so it travels in the scenario block and
    stays out of every fingerprint here. A run that applied a scenario
    therefore keeps the scenario and inputs fingerprints it had before the
    record was written, and a reader who strips the record changes neither.
    The days the interventions actually fired on are in the shocks and
    transmission, and those are fingerprinted.
    """
    return _sha(_canonical({key: value for key, value in payload.items()
                            if key != "origins"}))


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
    including the continuous internals that tomorrow's prices depend on as
    well as the prices a cent grid has already rounded.
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


#: The per-name idiosyncratic variance state's snapshot keys, in the order
#: the state hash covers them: carried together, and only while
#: `idio_vol_alpha`, `idio_vol_beta` or `idio_vol_jump_bump` is set.
_IDIO_VOL_KEYS = ("idio_variance", "idio_jump_pending", "idio_jump_var_pending")


def state_hash(snapshot: dict[str, Any]) -> str:
    """sha256 over an engine's state: the per-day ledger leaf, in Python.

    It matches ``Engine.state_hash`` but is computed from
    ``Engine.state_snapshot()`` instead of the engine, and a test holds the
    two equal. With it a reader can check a ledger's leaves against an
    archived snapshot with the package alone, and the encoding has a second
    implementation, so a divergence between the two shows up.

    ## What it covers

    Every field the snapshot carries, in one fixed order: the eighteen
    columns instrument by instrument, the eight generator states, the roster
    and the model fingerprint, the day accumulators and the market-open flag,
    the market factor's variance, the volume states, the universe stress, the
    forced-flow budget, the growth term's nominal base, the day's endogenous
    news, the economy in declared order, the central bank and the day
    counter. After the book come five fields a snapshot carries only when
    they have moved: the day's label and the valuation's clock where they
    are not the day the counter gives, the fair-value inputs once
    ``set_fundamentals`` has changed them, the share counts once
    ``set_shares_outstanding`` has changed them, and the variance cascade on
    a model that runs it.

    It accepts three keys and does not cover them: ``session_tick``, the
    ticks the day has run, ``state_schema``, the snapshot's layout version,
    and ``vix_anchor``, the derived VIX anchor (:data:`_UNHASHED_KEYS` says
    why). It accepts the economy's
    ``qe_assets_ratio``, carried on a model with ``qe_pe_stock_gain`` set,
    and does not cover it either, as the engine's own hash does not.

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

    Note one difference before comparing two runs.
    ``run_session(close_at_end=True)`` leaves the binding's session flag set
    where ``close_market()`` clears it, so the two spellings of one close
    hash apart on a market that is otherwise identical to the bit. The flag
    is state, because it decides whether the next session
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
    states are raw bit patterns instead, and :func:`_bits` says why. Strings
    are length-prefixed, a bool is one byte, and an optional value is a
    presence byte followed by the value when it is there.

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
    # The anchor's slow memory is carried, and hashed, only on a run with
    # `vix_anchor_memory` off zero; every other snapshot omits it, and the
    # published VIX's stress memory only with `vix_stress_premium` set. So is
    # the market factor's return memory, only with `market_vol_leverage` set,
    # and the cycle's volatility multiplier, only with
    # `market_vol_cycle_ratio` set and once a close has set it.
    # So are the rate instruments, only on an engine that holds them, and
    # the agent-facing book, only once an agent has used it.
    # From pt-v20 the fair-value levels and the unapplied opening draws are
    # carried, together, on a model that can move a level. The accrued
    # buyback share-count reductions are carried only with
    # `buyback_accrual` and `buyback_payout_share` both set. The derived VIX
    # anchor rides with `vix_level_identity`, and is not hashed: it is a
    # constant of the run.
    expected = set(_SNAPSHOT_KEYS) | (
        {"vix_anchor_slow", "vix_anchor", "vix_stress_memory", "market_vol_leverage_memory",
         "market_vol_cycle_log", "rates", "book", "fair_value_offset", "opening_z",
         "buyback_log_shares", "opening_carry",
         # Carried only while set: a forced close pending tonight, today's
         # macro pins the corporate yield reads, and a jump's fair-value
         # shift waiting for its tape row.
         "vix_sets_variance_pending", "macro_pins_today", "pending_fair_value",
         # Carried only where they are not the day the counter gives, once
         # `set_fundamentals` or `set_shares_outstanding` has moved them, and
         # on a model that runs the variance cascade. Hashed after the book,
         # each behind its name.
         "current_day", "elapsed_days", "fundamentals", "shares_outstanding",
         "garch_cascade",
         # Carried on an engine built with a population, and hashed last.
         "population",
         # The market's cycle nowcast's generator, only while
         # `cycle_nowcast_accuracy` is set; the belief rides in the economy.
         "cycle_nowcast_rng",
         # The central bank's stress level, only while `fed_stress_cut` is
         # set, and the rate indices' live mark, only while
         # `rate_intraday_live` is set and a session holds one.
         "fed_stress_vix_max", "rate_live_marks",
         # The stress hold's clock, only while `fed_stress_hold` is set, and
         # the market's forecast of the policy path, only while
         # `treasury_path_pricing` is set.
         "fed_stress_hold_age", "treasury_policy_path",
         # The drawdown hold's window and base, only while
         # `fed_drawdown_hold` is set.
         "fed_drawdown_returns", "fed_drawdown_mcap_prev",
         # What the curve prices of the next meeting, only while
         # `policy_anticipation` is set.
         "policy_anticipation_priced",
         # and the spread a spread pin holds tonight, only with its mark.
         "pinned_corporate_spread",
         # Today's priced VIX move, only while a pin has made one.
         "pinned_vix_jump",
         # The day's market t scale, only between an open that drew one
         # and the close (`market_day_tail_df`).
         "market_day_scale",
         # The price index's divisor and close level, only while
         # `index_level_listed` is set; the live VIX's projection, only
         # while `vix_intraday_live` is set and a session holds one; and
         # the forecast, only while `forecast_horizon_sessions` is set and a
         # close has computed one.
         "index_divisor", "vix_live", "forecast",
         # The index futures and the generator they draw on, only while
         # `futures_index_listed` is set; their book, once an agent has
         # traded a contract; and the night's path, only while
         # `night_session_steps` is set and a night is walked.
         "derivatives_rng", "futures", "futures_book", "night_bridge",
         # The VIX's fear memory, only while `vix_fear_uptake` is set.
         "vix_fear",
         # The VIX futures, only while `futures_vix_listed` is set, and
         # their book once an agent has traded one.
         "vix_futures", "vix_futures_book",
         # The rate futures, only while `futures_rates_listed` is set, and
         # their book once an agent has traded one.
         "rate_futures", "rate_futures_book",
         # The oil futures, only while `futures_oil_listed` is set, and their
         # book once an agent has traded one.
         "oil_futures", "oil_futures_book",
         # The contracts' margin, only while `margin_scan_coverage` is set.
         "margin",
         # Oil's long factor, only while `oil_target_drift_sd` is set.
         "oil_target_drift",
         # The dividend states, on a model that pays dividends, and an
         # ex-date's move in `s` waiting for its tape row.
         "dividend", "pending_dividend",
         # The earnings calendar's key, only with the calendar on, and what
         # names hold back of the cycle for their reports, only while that
         # share runs.
         "earnings_key", "earnings_withheld",
         # Tonight's market draw under a night split, only while the
         # session's live lagged wire reads it.
         "night_market_factor",
         # The day's GJR innovation under a night split, which the
         # attribution books in `overnight` alone.
         "innovation_day",
         # The per-name idiosyncratic variance state, its three vectors
         # together, only while `idio_vol_alpha`, `_beta` or `_jump_bump`
         # is set.
         *_IDIO_VOL_KEYS,
         # Carried by every snapshot since 0.8.5 and hashed by none: the
         # ticks the day has run, the tick the book stamps a fill with. See
         # `_UNHASHED_KEYS`.
         *_UNHASHED_KEYS}
        & carried)
    layout = snapshot.get("state_schema", Engine.STATE_SCHEMA)
    if (isinstance(layout, bool) or not isinstance(layout, int)
            or not 1 <= layout <= Engine.STATE_SCHEMA):
        raise ValidationError(
            f"this snapshot's state_schema is {layout!r}, and this build "
            f"hashes versions 1 to {Engine.STATE_SCHEMA}.")
    if carried & set(_IDIO_VOL_KEYS) and not set(_IDIO_VOL_KEYS) <= carried:
        raise ValidationError(
            "this snapshot carries part of the idiosyncratic variance state "
            f"({sorted(carried & set(_IDIO_VOL_KEYS))}). The engine writes "
            "all three vectors or none, so it was edited or assembled from "
            "two snapshots.")
    if ("fair_value_offset" in carried) != ("opening_z" in carried):
        raise ValidationError(
            "this snapshot carries one of fair_value_offset and opening_z "
            "without the other. The engine writes both or neither, so it was "
            "edited or assembled from two snapshots.")
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

    from .noise import STREAMS  # the stream count, derived rather than written
    rng = list(snapshot["rng"])
    if len(rng) != 3 * len(STREAMS):
        raise ValidationError(
            f"this snapshot carries {len(rng)} generator numbers and the "
            f"state hash covers {3 * len(STREAMS)}: {len(STREAMS)} streams "
            "of state, increment and Box-Muller spare. A snapshot from an "
            "earlier stream layout froze a market this hash cannot describe."
        )
    for value in rng:
        _bits(buf, value)

    _u32(buf, n)
    for ticker in tickers:
        _text(buf, ticker)
    _text(buf, snapshot["model_fingerprint"])

    # The fair-value shift is the last slot of an attribution row and of a
    # tick row. It is hashed after both, and only for an engine carrying
    # fair-value offsets (whose snapshot has the "fair_value_offset" key), as
    # `Engine::state_hash_with_pending` does, so every other engine hashes as
    # it did before the slot existed.
    # The dividend's slot follows it in an attribution row, and is hashed
    # after the fair-value shift's on the same rule: only on a model that
    # pays dividends (whose snapshot has the "dividend" key) or where it is
    # non-zero.
    # A snapshot of a model without dividends leaves the dividend's slot out
    # of its attribution rows, which are then the eleven-wide rows they were.
    fv_slot = len(Engine.FACTORS) - 2
    width_a = len(Engine.FACTORS) if "dividend" in snapshot else fv_slot + 1
    width_t = 9
    rows_a = _column(snapshot["attribution"], n * width_a, "attribution")
    rows_t = _column(snapshot["tick_components"], n * width_t, "tick_components")
    for i in range(n):
        for value in rows_a[i * width_a:i * width_a + fv_slot]:
            _f64(buf, value)
    for i in range(n):
        for value in rows_t[i * width_t:(i + 1) * width_t - 1]:
            _f64(buf, value)
    fv_a = [rows_a[i * width_a + fv_slot] for i in range(n)]
    fv_t = [rows_t[(i + 1) * width_t - 1] for i in range(n)]
    if ("fair_value_offset" in snapshot or any(fv_a) or any(fv_t)):
        for value in fv_a + fv_t:
            _f64(buf, value)
    if "dividend" in snapshot:
        for value in (rows_a[i * width_a + fv_slot + 1] for i in range(n)):
            _f64(buf, value)
    for name, width in (("tick_fundamental", 1), ("tick_anchor", 1),
                        # The day's noise split, its idiosyncratic scale and
                        # the pending jump move, hashed here because they sit
                        # beside the accumulators above in the snapshot and
                        # are lost the same way.
                        ("noise_parts", 3), ("noise_own_scale2", 1)):
        for value in _column(snapshot[name], n * width, name):
            _f64(buf, value)
    # The day's GJR innovation under a night split, length first, as the
    # engine hashes it.
    if "innovation_day" in snapshot:
        values = _column(snapshot["innovation_day"],
                         len(snapshot["innovation_day"]) // 8, "innovation_day")
        _u32(buf, len(values))
        for value in values:
            _f64(buf, value)
    for value in _column(snapshot["jump_move"], n, "jump_move"):
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
    # The two states the composed vector turned on, LENGTH-PREFIXED: the
    # sector one follows the sector table rather than the roster, and the
    # name one is empty on an engine built with no companies. Same reason
    # the pending buffers below carry their lengths, and the same spelling.
    for name in ("sector_variance", "jump_excitation", "sector_day_factor"):
        raw = snapshot[name]
        if len(raw) % 8:
            raise ValidationError(
                f"snapshot field {name!r} carries {len(raw)} bytes, which is "
                "not a whole number of f64s.")
        values = _column(raw, len(raw) // 8, name)
        _u32(buf, len(values))
        for value in values:
            _f64(buf, value)
    _f64(buf, snapshot["sector_target_day"])
    _f64(buf, snapshot["universe_stress"])
    _f64(buf, snapshot["forced_flow_spent"])
    # The market factor's slow variance level, in logs. Hashed beside the
    # line above and for the same reason: two engines alike in every column
    # and sitting on different levels revert to different targets tonight.
    _f64(buf, snapshot["market_vol_log_level"])
    # The VIX's own slow log-level, for the same reason.
    _f64(buf, snapshot.get("vix_log_level", 0.0))
    if "vix_anchor_slow" in snapshot:
        _f64(buf, snapshot["vix_anchor_slow"])
    # The market factor's return memory, only on a model with
    # `market_vol_leverage` set: `Engine::state_hash`'s order and rule.
    if "market_vol_leverage_memory" in snapshot:
        _f64(buf, snapshot["market_vol_leverage_memory"])
    # The cycle's volatility multiplier, only on a model with
    # `market_vol_cycle_ratio` set: `Engine::state_hash`'s order and rule.
    if "market_vol_cycle_log" in snapshot:
        _f64(buf, snapshot["market_vol_cycle_log"])
    # The published VIX's stress memory, only on a model with
    # `vix_stress_premium` set: `Engine::state_hash`'s order and rule.
    if "vix_stress_memory" in snapshot:
        _f64(buf, snapshot["vix_stress_memory"])
    # The aggregate earnings cycle, only on a model with the cycle on, and
    # then the fair-value levels and the unapplied opening draws, only on a
    # model that can move a level: `Engine::state_hash`'s order and rule.
    if "earnings_cycle" in snapshot["economy"]:
        _f64(buf, snapshot["economy"]["earnings_cycle"])
    # The volatility feedback's smoothed exposure, only on a model with both
    # `fair_value_vix_discount` and `fair_value_vix_half_life` set.
    if "vix_feedback" in snapshot["economy"]:
        _f64(buf, snapshot["economy"]["vix_feedback"])
    # The accrued buyback share-count reductions, only on a model with
    # `buyback_accrual` and `buyback_payout_share` both set, one per name in
    # roster order: `Engine::state_hash`'s order, before the levels.
    if "buyback_log_shares" in snapshot:
        raw = snapshot["buyback_log_shares"]
        for value in _column(raw, n, "buyback_log_shares"):
            _f64(buf, value)
    # The earnings calendar's key, only with `earnings_surprise_sigma` set.
    if "earnings_key" in snapshot:
        _u64(buf, snapshot["earnings_key"])
    if "earnings_withheld" in snapshot:
        raw = snapshot["earnings_withheld"]
        values = _column(raw, len(raw) // 8, "earnings_withheld")
        _u32(buf, len(values))
        for value in values:
            _f64(buf, value)
    # Tonight's market draw, only while the live lagged wire reads it.
    if "night_market_factor" in snapshot:
        _f64(buf, snapshot["night_market_factor"])
    # The Fed put's state, only on a model with `fed_put_gain` set.
    if "fed_put" in snapshot["economy"]:
        for name in ("intermeeting_return", "fed_put", "fed_put_owed",
                     "fed_put_mcap_prev"):
            _f64(buf, snapshot["economy"][name])
    # Credit's leverage gap, only on a model with `corporate_spread_equity_gain` set.
    if "spread_equity_gap" in snapshot["economy"]:
        _f64(buf, snapshot["economy"]["spread_equity_gap"])
    if "fair_value_offset" in snapshot:
        for name in ("fair_value_offset", "opening_z"):
            if len(snapshot[name]) % 8:
                raise ValidationError(
                    f"snapshot field {name!r} carries {len(snapshot[name])} "
                    "bytes, which is not a whole number of f64s.")
        raw = snapshot["fair_value_offset"]
        for value in _column(raw, len(raw) // 8, "fair_value_offset"):
            _f64(buf, value)
        raw = snapshot["opening_z"]
        values = _column(raw, len(raw) // 8, "opening_z")
        _u32(buf, len(values))
        for value in values:
            _f64(buf, value)
        # The prehistory's carried opening (`market_prehistory_valuation`),
        # only while one waits: empty after the first open.
        raw = snapshot.get("opening_carry", b"")
        if len(raw) % 8:
            raise ValidationError(
                f"snapshot field 'opening_carry' carries {len(raw)} bytes, "
                "which is not a whole number of f64s.")
        if raw:
            values = _column(raw, len(raw) // 8, "opening_carry")
            _u32(buf, len(values))
            for value in values:
                _f64(buf, value)
    # The dividend states, seven f64s a name, only on a model that pays
    # dividends: `Engine::state_hash`'s order and rule.
    if "dividend" in snapshot:
        raw = snapshot["dividend"]
        for value in _column(raw, len(raw) // 8, "dividend"):
            _f64(buf, value)
    # The per-name idiosyncratic variance state, only while it runs, each
    # vector length-prefixed: `Engine::state_hash`'s order and rule.
    if "idio_variance" in snapshot:
        for name in _IDIO_VOL_KEYS:
            raw = snapshot[name]
            if len(raw) % 8:
                raise ValidationError(
                    f"snapshot field {name!r} carries {len(raw)} bytes, which "
                    "is not a whole number of f64s.")
            values = _column(raw, len(raw) // 8, name)
            _u32(buf, len(values))
            for value in values:
                _f64(buf, value)
    # The crisis episode, hashed for the reason the levels above are: two
    # engines alike in every column, one three sessions into a
    # financial-services episode and the other outside one, price the
    # epicentre's names differently tomorrow. `.get` with the default a
    # snapshot from before the mechanism carries, which is every recorded
    # one.
    _flag(buf, bool(snapshot.get("crisis_in_episode", False)))
    _f64(buf, float(snapshot.get("crisis_sessions_under", 0)))
    _f64(buf, float(snapshot.get("crisis_epicentre", -1)))
    _f64(buf, float(snapshot.get("crisis_epicentre_pin", -2)))
    # A forced close pending tonight, and today's macro pins, each only
    # while set, as the engine hashes them.
    if snapshot.get("vix_sets_variance_pending"):
        _flag(buf, True)
    if snapshot.get("macro_pins_today"):
        _f64(buf, 7.0)
        _f64(buf, float(snapshot["macro_pins_today"]))
        # The pinned corporate spread, only while its mark (0x4000) stands.
        if int(snapshot["macro_pins_today"]) & 0x4000:
            _f64(buf, float(snapshot["pinned_corporate_spread"]))
    # The stress level and the live mark, each behind its own tag, only
    # while carried: `Engine::state_hash`'s order and rule.
    if "fed_stress_vix_max" in snapshot:
        _f64(buf, 8.0)
        _f64(buf, float(snapshot["fed_stress_vix_max"]))
    # The stress hold's clock and the priced path's forecast, each behind its
    # own tag, only while carried.
    if "fed_stress_hold_age" in snapshot:
        _f64(buf, 10.0)
        _f64(buf, float(snapshot["fed_stress_hold_age"]))
    if "treasury_policy_path" in snapshot:
        _f64(buf, 11.0)
        _f64(buf, float(snapshot["treasury_policy_path"]))
    # The drawdown hold's window and base, behind their tag, the window
    # length-prefixed, only while carried.
    if "fed_drawdown_returns" in snapshot:
        raw = snapshot["fed_drawdown_returns"]
        if len(raw) % 8:
            raise ValidationError(
                f"snapshot field 'fed_drawdown_returns' carries {len(raw)} "
                "bytes, which is not a whole number of f64s.")
        values = _column(raw, len(raw) // 8, "fed_drawdown_returns")
        _f64(buf, 32.0)
        _f64(buf, float(len(values)))
        for value in values:
            _f64(buf, value)
        _f64(buf, float(snapshot["fed_drawdown_mcap_prev"]))
    if "policy_anticipation_priced" in snapshot:
        _f64(buf, 31.0)
        _f64(buf, float(snapshot["policy_anticipation_priced"]))
    if "rate_live_marks" in snapshot:
        marks = list(snapshot["rate_live_marks"])
        if len(marks) != 6:
            raise ValidationError(
                f"this snapshot's rate_live_marks carries {len(marks)} values; "
                "the state hash covers 6.")
        _f64(buf, 9.0)
        for value in marks:
            _f64(buf, float(value))
    if snapshot.get("pinned_vix_jump"):
        _f64(buf, 10.0)
        _f64(buf, float(snapshot["pinned_vix_jump"]))
    if "market_day_scale" in snapshot and float(snapshot["market_day_scale"]) != 1.0:
        _f64(buf, 12.0)
        _f64(buf, float(snapshot["market_day_scale"]))
    # The price index, the live VIX and the forecast, each behind its own
    # tag, only while carried: `Engine::state_hash`'s order and rule.
    if "index_divisor" in snapshot:
        pair = list(snapshot["index_divisor"])
        if len(pair) != 2:
            raise ValidationError(
                f"this snapshot's index_divisor carries {len(pair)} values; the "
                "state hash covers 2, the divisor and the last close's level.")
        _f64(buf, 41.0)
        for value in pair:
            _f64(buf, float(value))
    if "vix_live" in snapshot:
        _f64(buf, 42.0)
        _f64(buf, float(snapshot["vix_live"]))
    if "forecast" in snapshot:
        raw = snapshot["forecast"]
        if len(raw) % 8:
            raise ValidationError(
                f"snapshot field 'forecast' carries {len(raw)} bytes, which is "
                "not a whole number of f64s.")
        values = _column(raw, len(raw) // 8, "forecast")
        _f64(buf, 43.0)
        _u32(buf, len(values))
        for value in values:
            _f64(buf, value)
    # The index futures: their generator's bit patterns and draw counts,
    # then their numbers, length-prefixed; their book behind its own tag;
    # the night's path behind a third. `Engine::state_hash`'s order and rule.
    if ("futures" in snapshot) != ("derivatives_rng" in snapshot):
        raise ValidationError(
            "this snapshot carries one of futures and derivatives_rng without "
            "the other. The engine writes both or neither, so it was edited or "
            "assembled from two snapshots.")
    if "futures" in snapshot:
        rng = list(snapshot["derivatives_rng"])
        if len(rng) != 5:
            raise ValidationError(
                f"this snapshot's derivatives_rng carries {len(rng)} numbers; "
                "the state hash covers 5.")
        raw = snapshot["futures"]
        if len(raw) % 8:
            raise ValidationError(
                f"snapshot field 'futures' carries {len(raw)} bytes, which is "
                "not a whole number of f64s.")
        values = _column(raw, len(raw) // 8, "futures")
        _f64(buf, 44.0)
        for value in rng[:3]:
            _bits(buf, value)
        for value in rng[3:]:
            _f64(buf, value)
        _u32(buf, len(values))
        for value in values:
            _f64(buf, value)
    if "futures_book" in snapshot:
        _f64(buf, 45.0)
        _book(buf, snapshot["futures_book"])
    if "night_bridge" in snapshot:
        raw = snapshot["night_bridge"]
        if len(raw) % 8:
            raise ValidationError(
                f"snapshot field 'night_bridge' carries {len(raw)} bytes, which "
                "is not a whole number of f64s.")
        values = _column(raw, len(raw) // 8, "night_bridge")
        _f64(buf, 46.0)
        _u32(buf, len(values))
        for value in values:
            _f64(buf, value)
    # The VIX's fear memory, only while `vix_fear_uptake` is set, behind its
    # own tag: `Engine::state_hash`'s order and rule.
    if "vix_fear" in snapshot:
        _f64(buf, 47.0)
        _f64(buf, float(snapshot["vix_fear"]))
    # The VIX futures, only while `futures_vix_listed` is set, behind their
    # own tag, length-prefixed; their book behind another once an agent has
    # traded one. `Engine::state_hash`'s order and rule.
    if "vix_futures" in snapshot:
        raw = snapshot["vix_futures"]
        if len(raw) % 8:
            raise ValidationError(
                f"snapshot field 'vix_futures' carries {len(raw)} bytes, which "
                "is not a whole number of f64s.")
        values = _column(raw, len(raw) // 8, "vix_futures")
        _f64(buf, 48.0)
        _u32(buf, len(values))
        for value in values:
            _f64(buf, value)
    if "vix_futures_book" in snapshot:
        _f64(buf, 49.0)
        _book(buf, snapshot["vix_futures_book"])
    # The rate futures, only while `futures_rates_listed` is set, behind their
    # own tag, length-prefixed; their book behind another once an agent has
    # traded one.
    if "rate_futures" in snapshot:
        raw = snapshot["rate_futures"]
        if len(raw) % 8:
            raise ValidationError(
                f"snapshot field 'rate_futures' carries {len(raw)} bytes, which "
                "is not a whole number of f64s.")
        values = _column(raw, len(raw) // 8, "rate_futures")
        _f64(buf, 50.0)
        _u32(buf, len(values))
        for value in values:
            _f64(buf, value)
    if "rate_futures_book" in snapshot:
        _f64(buf, 51.0)
        _book(buf, snapshot["rate_futures_book"])
    # The oil futures, only while `futures_oil_listed` is set, behind their
    # own tag, length-prefixed; their book behind another once an agent has
    # traded one.
    if "oil_futures" in snapshot:
        raw = snapshot["oil_futures"]
        if len(raw) % 8:
            raise ValidationError(
                f"snapshot field 'oil_futures' carries {len(raw)} bytes, which "
                "is not a whole number of f64s.")
        values = _column(raw, len(raw) // 8, "oil_futures")
        _f64(buf, 52.0)
        _u32(buf, len(values))
        for value in values:
            _f64(buf, value)
    if "oil_futures_book" in snapshot:
        _f64(buf, 53.0)
        _book(buf, snapshot["oil_futures_book"])
    # The contracts' margin, only while `margin_scan_coverage` is set, behind
    # its own tag, length-prefixed. `Engine::state_hash`'s order and rule.
    if "margin" in snapshot:
        raw = snapshot["margin"]
        if len(raw) % 8:
            raise ValidationError(
                f"snapshot field 'margin' carries {len(raw)} bytes, which is "
                "not a whole number of f64s.")
        values = _column(raw, len(raw) // 8, "margin")
        _f64(buf, 54.0)
        _u32(buf, len(values))
        for value in values:
            _f64(buf, value)
    # Oil's long factor, only while `oil_target_drift_sd` is set, behind its
    # own tag: its log level and its key's two words.
    if "oil_target_drift" in snapshot:
        _f64(buf, 55.0)
        for value in _column(snapshot["oil_target_drift"], 3, "oil_target_drift"):
            _f64(buf, value)
    # LENGTH-PREFIXED, because these two are empty between the tape row that
    # consumes them and the close that fills them again -- unlike every
    # per-slot array above, which always follows the roster. An empty buffer
    # and a roster-length one of zeros are different states and hash apart.
    for name in ("pending_jump", "pending_overnight"):
        raw = snapshot[name]
        if len(raw) % 8:
            raise ValidationError(
                f"snapshot field {name!r} carries {len(raw)} bytes, which is "
                "not a whole number of f64s.")
        values = _column(raw, len(raw) // 8, name)
        _u32(buf, len(values))
        for value in values:
            _f64(buf, value)
    # The jump's fair-value shift waiting for its tape row: a key only while
    # non-empty, and hashed only then.
    if snapshot.get("pending_fair_value"):
        raw = snapshot["pending_fair_value"]
        values = _column(raw, len(raw) // 8, "pending_fair_value")
        _u32(buf, len(values))
        for value in values:
            _f64(buf, value)
    # The ex-date's move in `s` waiting for its tape row, on the same rule.
    if snapshot.get("pending_dividend"):
        raw = snapshot["pending_dividend"]
        values = _column(raw, len(raw) // 8, "pending_dividend")
        _u32(buf, len(values))
        for value in values:
            _f64(buf, value)
    # Nominal output when the run opened, the base of the growth term's
    # ratio. A constant of the run, hashed for the reason the fields around
    # it are: two engines alike in every column and holding different bases
    # value the same earnings differently tomorrow.
    _f64(buf, snapshot["nominal_output_base"])

    news = list(snapshot["session_news"])
    _u32(buf, len(news))
    for event in news:
        _maybe_text(buf, event["ticker"])
        _maybe_text(buf, event["sector"])
        _maybe_f64(buf, event["price_impact"])

    economy = snapshot["economy"]
    # `earnings_cycle` only on a model with the cycle on; hashed above, beside
    # the other states a dial turns on. `cycle_history` only on a model with
    # `cycle_publication_lag` set; hashed after the phase, below.
    # `gdp_publication` only on a model with `gdp_publication_lag` set;
    # hashed after the history. `unemployment_impulse` only on a model with
    # `unemployment_adjustment_half_life` set; hashed before it.
    # `vix_feedback` only with the volatility feedback smoothed; hashed
    # after `earnings_cycle`. The Fed put's four fields only with
    # `fed_put_gain` set, together; hashed after the night's market draw.
    # `spread_equity_gap` only with `corporate_spread_equity_gain` set;
    # hashed after the Fed put's fields.
    # `cycle_nowcast` only on a model with `cycle_nowcast_accuracy` set,
    # together with the snapshot's `cycle_nowcast_rng`; hashed after the
    # phase, before the history.
    # `cycle_publication` only on a model with `cycle_publication_lag_draw`
    # set, and `anticipation_drift` with `anticipation_raw` only on a model
    # with `earnings_anticipation_drift_share` set; both hashed after
    # `gdp_publication`, in that order.
    # `qe_assets_ratio` only with `qe_pe_stock_gain` set, and not hashed:
    # the engine's state hash has never covered it, and covering it now would
    # move every leaf of such a run.
    economy_expected = set(_ECONOMY_KEYS) | (
        {"earnings_cycle", "cycle_history", "gdp_publication",
         "unemployment_impulse", "vix_feedback", "qe_assets_ratio",
         "cycle_nowcast", "cycle_publication", "anticipation_drift",
         "anticipation_raw", "spread_equity_gap"}
        & set(economy))
    if "fed_put" in economy:
        economy_expected |= {"intermeeting_return", "fed_put", "fed_put_owed",
                             "fed_put_mcap_prev"}
    if ("anticipation_drift" in economy) != ("anticipation_raw" in economy):
        raise ValidationError(
            "this snapshot carries one of the economy's anticipation_drift "
            "and anticipation_raw without the other. The engine writes both "
            "or neither.")
    if ("cycle_nowcast" in economy) != ("cycle_nowcast_rng" in snapshot):
        raise ValidationError(
            "this snapshot carries one of the economy's cycle_nowcast and "
            "cycle_nowcast_rng without the other. The engine writes both or "
            "neither, so it was edited or assembled from two snapshots.")
    if set(economy) != economy_expected:
        raise ValidationError(
            "this snapshot's economy is not the one the state hash covers: "
            f"missing {sorted(economy_expected - set(economy))}, unexpected "
            f"{sorted(set(economy) - economy_expected)}."
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
    # The market's cycle nowcast, only while `cycle_nowcast_accuracy` is set:
    # the five weights, then its generator's state, increment and spare as
    # bit patterns and its uniform and normal counts. `Engine::state_hash`'s
    # order and rule.
    if "cycle_nowcast" in economy:
        belief = list(economy["cycle_nowcast"])
        rng = list(snapshot["cycle_nowcast_rng"])
        if len(belief) != 5 or len(rng) != 5:
            raise ValidationError(
                f"this snapshot's cycle nowcast carries {len(belief)} weights "
                f"and {len(rng)} generator numbers; the state hash covers 5 "
                "and 5.")
        for value in belief:
            _f64(buf, value)
        for value in rng[:3]:
            _bits(buf, value)
        for value in rng[3:]:
            _f64(buf, value)
    # The published-phase history, oldest first, LENGTH-PREFIXED, only while
    # `cycle_publication_lag` keeps one: `Engine::state_hash`'s order and rule.
    if "cycle_history" in economy:
        history = list(economy["cycle_history"])
        _u32(buf, len(history))
        for phase in history:
            _text(buf, phase)
    # Unemployment's impulse, only while `unemployment_adjustment_half_life`
    # is set: `Engine::state_hash`'s order and rule.
    if "unemployment_impulse" in economy:
        _f64(buf, economy["unemployment_impulse"])
    # The published GDP growth figure's state, only while
    # `gdp_publication_lag` is set: `Engine::state_hash`'s order and rule,
    # the pending releases LENGTH-PREFIXED, each its day then its figure.
    if "gdp_publication" in economy:
        gdp = economy["gdp_publication"]
        keys = {"published", "quarter", "count", "sum",
                "pending_days", "pending_values"}
        if set(gdp) != keys:
            raise ValidationError(
                "this snapshot's gdp_publication is not the one the state "
                f"hash covers: missing {sorted(keys - set(gdp))}, "
                f"unexpected {sorted(set(gdp) - keys)}.")
        days, values = list(gdp["pending_days"]), list(gdp["pending_values"])
        if len(days) != len(values):
            raise ValidationError(
                f"this snapshot's gdp_publication has {len(days)} pending "
                f"release days and {len(values)} pending figures.")
        _f64(buf, gdp["published"])
        _i64(buf, gdp["quarter"])
        _u32(buf, gdp["count"])
        _f64(buf, gdp["sum"])
        _u32(buf, len(days))
        for day, value in zip(days, values):
            _i64(buf, day)
            _f64(buf, value)
    # The drawn publication schedule, only while `cycle_publication_lag_draw`
    # is set: `Engine::state_hash`'s order and rule, the pending turns
    # LENGTH-PREFIXED, each its close then its phase.
    if "cycle_publication" in economy:
        pub = economy["cycle_publication"]
        keys = {"key", "published", "last_true", "closes", "turns",
                "pending_closes", "pending_phases"}
        if set(pub) != keys:
            raise ValidationError(
                "this snapshot's cycle_publication is not the one the state "
                f"hash covers: missing {sorted(keys - set(pub))}, "
                f"unexpected {sorted(set(pub) - keys)}.")
        closes = list(pub["pending_closes"])
        phases = list(pub["pending_phases"])
        if len(closes) != len(phases):
            raise ValidationError(
                f"this snapshot's cycle_publication has {len(closes)} pending "
                f"closes and {len(phases)} pending phases.")
        _u64(buf, pub["key"])
        _text(buf, pub["published"])
        _text(buf, pub["last_true"])
        _i64(buf, pub["closes"])
        _u64(buf, pub["turns"])
        _u32(buf, len(closes))
        for close, phase in zip(closes, phases):
            _i64(buf, close)
            _text(buf, phase)
    # The anticipation's left-out drift and the last `A - e`, only while
    # `earnings_anticipation_drift_share` is set.
    if "anticipation_drift" in economy:
        _f64(buf, economy["anticipation_drift"])
        _f64(buf, economy["anticipation_raw"])

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
    # carries them: the eight streams' uniform and normal positions
    # flattened in pairs, then the substitutions installed on them. Both
    # decide what the engine draws next, so a leaf that skipped them would
    # call two states the same when one is patched and the other is not.
    counts = list(snapshot["draw_counts"])
    if len(counts) != 2 * len(STREAMS):
        raise ValidationError(
            f"draw_counts must be {2 * len(STREAMS)} numbers, a uniform and "
            f"a normal count for each of the {len(STREAMS)} streams, got "
            f"{len(counts)}."
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
    # The rate instruments, before the book and only when carried, so every
    # snapshot of an engine without them hashes as it did before they existed. Each
    # instrument's state in `_RATE_STATE_FIELDS` order, then the curve state
    # the book shares.
    rates = snapshot.get("rates")
    if rates is not None:
        _text(buf, "rates")
        items = list(rates["instruments"])
        _u32(buf, len(items))
        for item in items:
            if set(item) != {"ticker", *_RATE_STATE_FIELDS}:
                raise ValidationError(
                    "a rate instrument in this snapshot does not carry the "
                    f"fields the state hash covers: {sorted(item)}.")
            _text(buf, item["ticker"])
            for name in _RATE_STATE_FIELDS:
                _f64(buf, item[name])
        _f64(buf, rates["ig_spread"])
        _f64(buf, rates["last_corporate"])
        _flag(buf, bool(rates["closed_since_open"]))
    # The agent-facing book, after the rate instruments, and only when the
    # snapshot carries it, which is only once an agent has used it.
    if "book" in snapshot:
        _book(buf, snapshot["book"])
    # The day's label and the valuation's clock, each behind its name and
    # only where it is not the day the counter gives. The engine never
    # writes one equal to that day, so a snapshot that does was edited, and
    # hashing it would describe a state no engine holds.
    usual = _default_day(snapshot["day_count"], snapshot["market_open"])
    for name in ("current_day", "elapsed_days"):
        if name in snapshot:
            day = int(snapshot[name])
            if day == usual:
                raise ValidationError(
                    f"this snapshot carries {name}={day}, which is the day its "
                    f"day_count and market_open give. The engine writes the "
                    f"key only when the two differ, so the snapshot was "
                    f"edited.")
            _text(buf, name)
            _i64(buf, day)
    # The fair-value inputs, once `set_fundamentals` has moved them: every
    # equity's earnings, book value and revenue growth, NaN where absent.
    if "fundamentals" in snapshot:
        block = snapshot["fundamentals"]
        keys = ("eps", "book_value_per_share", "revenue_growth")
        if not isinstance(block, dict) or set(block) != set(keys):
            raise ValidationError(
                "this snapshot's fundamentals are not the three columns the "
                f"state hash covers: {sorted(block) if isinstance(block, dict) else block!r}.")
        columns = [_column(block[k], n, f"fundamentals.{k}") for k in keys]
        _text(buf, "fundamentals")
        _u32(buf, n)
        for i in range(n):
            for column in columns:
                _f64(buf, column[i])
    # The share counts, once `set_shares_outstanding` has moved them: every
    # tick's market cap is the price times the count.
    if "shares_outstanding" in snapshot:
        shares = _column(snapshot["shares_outstanding"], n, "shares_outstanding")
        _text(buf, "shares_outstanding")
        _u32(buf, n)
        for value in shares:
            _f64(buf, value)
    # The variance cascade's components, on a model that runs it,
    # LENGTH-PREFIXED as the engine writes them.
    if "garch_cascade" in snapshot:
        raw = snapshot["garch_cascade"]
        if len(raw) % 8:
            raise ValidationError(
                f"snapshot field 'garch_cascade' carries {len(raw)} bytes, "
                "which is not a whole number of f64s.")
        values = _column(raw, len(raw) // 8, "garch_cascade")
        _text(buf, "garch_cascade")
        _u32(buf, len(values))
        for value in values:
            _f64(buf, value)
    # The population, last, on an engine built with one: its fingerprint,
    # the roster its state follows and every number of its state.
    if "population" in snapshot:
        block = snapshot["population"]
        if not isinstance(block, dict) or set(block) != {"fingerprint", "tickers", "state"}:
            raise ValidationError(
                "this snapshot's population is not the block the state hash "
                "covers: fingerprint, tickers and state.")
        raw = block["state"]
        if len(raw) % 8:
            raise ValidationError(
                f"snapshot field 'population.state' carries {len(raw)} bytes, "
                "which is not a whole number of f64s.")
        _text(buf, "population")
        _text(buf, block["fingerprint"])
        tickers = list(block["tickers"])
        _u32(buf, len(tickers))
        for ticker in tickers:
            _text(buf, ticker)
        values = _column(raw, len(raw) // 8, "population.state")
        _u32(buf, len(values))
        for value in values:
            _f64(buf, value)
    return hashlib.sha256(bytes(buf)).hexdigest()


#: A rate instrument's state in a snapshot, in the order the state hash walks
#: it. The same list as `RATE_STATE_FIELDS` in `rust/src/python_engine.rs`.
_RATE_STATE_FIELDS = (
    "level", "marked_yield", "price", "previous_close", "open", "high", "low",
    "volume", "avg_volume", "units_outstanding", "maker_inventory",
    "day_carry", "day_duration", "day_convexity",
)


#: The fields of the snapshot's ``book`` entry, which ``Engine.state_hash``
#: covers in this order.
_BOOK_KEYS = ("sequence", "fill_sequence", "taken", "orders", "flow",
              "fills", "impacts")

#: The book's optional entries: the metaorder memory, present only while
#: ``impact_memory_coefficient`` is set and the memory holds something, and
#: hashed after everything else when it is.
_BOOK_OPTIONAL_KEYS = ("memory",)

#: Values per company in the book's ``memory`` buffer: the fast and slow
#: memories of agents' net flow against the house, the displacement booked
#: into ``s``, what the flow waiting for the next tick paid, and that flow
#: (signed shares the house took the other side of).
_MEMORY_WIDTH = 5

#: Values per company in the book's ``taken`` buffer: the maker's bid and
#: ask consumed, the latent depth's bid and ask consumed, and the maker's
#: inventory change waiting for its next quote.
_TAKEN_WIDTH = 5


def _book(buf: bytearray, book: dict[str, Any]) -> None:
    """The agent-facing book's entry, as the engine hashes it."""
    if not set(_BOOK_KEYS) <= set(book) <= set(_BOOK_KEYS + _BOOK_OPTIONAL_KEYS):
        raise ValidationError(
            "this snapshot's book is not the one the state hash covers: "
            f"missing {sorted(set(_BOOK_KEYS) - set(book))}, unexpected "
            f"{sorted(set(book) - set(_BOOK_KEYS + _BOOK_OPTIONAL_KEYS))}.")
    _text(buf, "book")
    _u64(buf, book["sequence"])
    _u64(buf, book["fill_sequence"])
    raw = book["taken"]
    if len(raw) % (8 * _TAKEN_WIDTH):
        raise ValidationError(
            f"the book's taken buffer carries {len(raw)} bytes, which is not "
            f"a whole number of {_TAKEN_WIDTH}-value rows.")
    rows = len(raw) // (8 * _TAKEN_WIDTH)
    _u32(buf, rows)
    for value in _column(raw, rows * _TAKEN_WIDTH, "book.taken"):
        _f64(buf, value)
    orders = list(book["orders"])
    _u32(buf, len(orders))
    for o in orders:
        _text(buf, o["order_id"])
        _text(buf, o["agent"])
        _text(buf, o["ticker"])
        _text(buf, o["side"])
        _f64(buf, o["limit_price"])
        _f64(buf, o["quantity"])
        _f64(buf, o["remaining"])
        _u64(buf, o["sequence"])
        _text(buf, o["mode"])
    flow = list(book["flow"])
    _u32(buf, len(flow))
    for agent, ticker, bought, sold in flow:
        _text(buf, agent)
        _text(buf, ticker)
        _f64(buf, bought)
        _f64(buf, sold)
    fills = list(book["fills"])
    _u32(buf, len(fills))
    for f in fills:
        _text(buf, f["agent"])
        _text(buf, f["order_id"])
        _text(buf, f["ticker"])
        _text(buf, f["side"])
        _f64(buf, f["quantity"])
        _f64(buf, f["price"])
        _text(buf, f["liquidity"])
        _text(buf, f["counterparty"])
        _f64(buf, f["reference"])
        _i64(buf, f["day"])
        # Not `tick`, which is a label counted from the engine's own open
        # and restarts at a restore; see `Engine::state_hash`.
        _u64(buf, f["sequence"])
    impacts = list(book["impacts"])
    _u32(buf, len(impacts))
    for r in impacts:
        _text(buf, r["agent"])
        _text(buf, r["ticker"])
        _f64(buf, r["bought"])
        _f64(buf, r["sold"])
        _f64(buf, r["permanent"])
        # Only while the metaorder memory is on; see `Engine::state_hash`.
        if "transient" in r:
            _text(buf, "transient")
            _f64(buf, r["transient"])
        _i64(buf, r["day"])
    if "memory" in book:
        raw = book["memory"]
        if len(raw) % (8 * _MEMORY_WIDTH):
            raise ValidationError(
                f"the book's memory buffer carries {len(raw)} bytes, which is "
                f"not a whole number of {_MEMORY_WIDTH}-value rows.")
        rows = len(raw) // (8 * _MEMORY_WIDTH)
        _text(buf, "memory")
        _u32(buf, rows)
        for value in _column(raw, rows * _MEMORY_WIDTH, "book.memory"):
            _f64(buf, value)



def _writer_version(written_by: dict[str, Any]) -> Any:
    """The tradefloor version a manifest's `written_by` names.

    0.10.0 and later write `tradefloor_version`; 0.9.1 and earlier wrote the
    same value as `pretium_version`, and those manifests still load.
    """
    return written_by.get("tradefloor_version", written_by.get("pretium_version"))


#: The default preset of each release line that changed it, for a manifest
#: written before 0.10.0, whose era block does not name the preset its probe
#: ran under. The same table as `tools/presets/record.py`'s DEFAULT_SINCE.
_DEFAULT_SINCE = (
    ("0.1.0", "pt-v3"), ("0.2.0", "pt-v10"), ("0.3.0", "pt-v12"),
    ("0.4.0", "pt-v14"), ("0.6.0", "pt-v16"), ("0.7.0", "pt-v18"),
    ("0.8.0", "pt-v19"), ("0.8.5", "pt-v20"), ("0.10.0", "pt-v21"),
)


def _era_preset(written_by: dict[str, Any]) -> str | None:
    """The preset a manifest's era probe ran under: the one its era block
    names, else its writer's default by version, else this build's."""
    era = written_by.get("era") or {}
    if era.get("preset"):
        return era["preset"]
    try:
        wrote = tuple(int(x) for x in str(_writer_version(written_by)).split(".")[:3])
    except ValueError:
        return None
    name = None
    for since, preset in _DEFAULT_SINCE:
        if wrote >= tuple(int(x) for x in since.split(".")):
            name = preset
    return name if name in preset_names() else None


def era_fingerprint(preset: str | None = None) -> str:
    """Digest of a fixed probe simulation, used as the build's identity.

    `preset` is the model the probe's coupled engine runs and whose values it
    hashes: the build's default when None, as every manifest records it. A
    manifest written under another default (0.9.1's pt-v20) is checked by
    running the probe under that one, so a default that moved does not read
    as an engine that did.

    Two builds that agree here produce the same numbers for the arithmetic
    the probe exercises: the generator, fair value across every sector and
    both valuation paths, the daily mispricing step, and a coupled engine run
    through day closes, where the macro chain advances. Version strings and
    preset names are quoted in a manifest but not trusted as the era, because
    both have held still across a boundary that moved every trajectory, and
    this digest moved. See the module docstring for the argument.

    It is a smaller version of ``tests/known_answer.py``, kept in the
    package because the test tree does not ship in a wheel and a reader
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
        **({} if preset is None else {"model": ModelParams.from_preset(preset)}),
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
    values = model_preset(preset)
    for key in sorted(k for k in values if k != "name"):
        _f64(buf, float(values[key]))

    return hashlib.sha256(bytes(buf)).hexdigest()



#: Schema of the JSON a :class:`DayLedger` writes.
LEDGER_SCHEMA = 1

#: Snapshot keys whose value is a buffer of little-endian f64s. They travel
#: base64-encoded, because the exact bits have to survive: a NaN in
#: ``tick_fundamental`` or in the ``rng`` array is a value, and JSON's own
#: float syntax would round-trip it as some other NaN.
_LEDGER_BUFFERS = ("attribution", "tick_components", "tick_fundamental",
                   "tick_anchor", "noise_parts", "noise_own_scale2",
                   "jump_move", "volume_idio",
                   "sector_variance", "jump_excitation", "sector_day_factor",
                   "pending_jump", "pending_overnight")

#: Byte buffers only some snapshots carry: the fair-value levels and the
#: unapplied opening draws on a model that can move a level (pt-v20 on), a
#: jump's fair-value shift waiting for its tape row, the variance cascade on
#: a model that runs it, and the dial-gated per-name state of the
#: mechanisms that are off on every shipped preset: the accrued buyback
#: share-count reductions, what names hold back of the earnings cycle for
#: their reports, the idiosyncratic variance state, the prehistory's carried
#: opening, the dividend states and an ex-date's move waiting for its tape
#: row, the drawdown hold's window and the day's GJR innovation under a
#: night split. Encoded where
#: present and left out where not. The ``fundamentals`` block's three
#: buffers and the book's consumed depth are encoded beside them.
_LEDGER_OPTIONAL_BUFFERS = ("fair_value_offset", "opening_z", "pending_fair_value",
                            "garch_cascade", "buyback_log_shares", "earnings_withheld",
                            "idio_variance", "idio_jump_pending",
                            "idio_jump_var_pending", "opening_carry", "dividend",
                            "pending_dividend", "fed_drawdown_returns",
                            "innovation_day", "forecast", "futures",
                            "night_bridge", "vix_futures", "rate_futures",
                            "oil_futures", "margin", "shares_outstanding",
                            "oil_target_drift")

#: The ``fundamentals`` block's buffers, one per company each.
_LEDGER_FUNDAMENTALS = ("eps", "book_value_per_share", "revenue_growth")


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
    A year of snapshots at forty names is several megabytes, so the states
    live here, beside the manifest, and the manifest stays small enough for
    a person to read.

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

    Size decides between them. On ``Universe.random(40, seed=7)``, seed 42,
    252 days at 30 ticks a day with ``record=False``, at ``fd7b6dc``, the
    ledger writes 4,880,447 bytes with the states and 16,924 without them,
    beside a 61,781-byte manifest. The run shape matters, because
    ``record=True`` takes the manifest to 68,223 bytes and leaves the ledger
    where it is. A manifest is meant to be read, so it carries the root
    alone.

    ## The leaf is taken after the close

    It is not taken after ``record``, so a run that never recorded a tape
    still ledgers, and the state a leaf commits to is the one the next day
    starts from.
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
        """The ledger as JSON, the file that travels beside a manifest.

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

        It refuses, by name, a hash version this build does not compute. A
        leaf from another version of the state hash is a different
        measurement, and checking a day against one would report a tampered
        day that is not. It refuses a leaf that is not 64 lowercase hex
        characters, by position, for the reason :func:`_is_leaf` gives.
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
    """One state snapshot in a form JSON can carry losslessly.

    Without its ``state_schema``, so a ledger written here reads, and
    verifies, on a release that predates the key. Ledger schema 1 holds
    version-1 snapshots, which ``Engine.restore_state`` reads without the key
    as it reads one written by 0.8.5 to 0.8.8. A new snapshot layout needs a
    new ledger schema.
    """
    out = dict(snapshot)
    out.pop("state_schema", None)
    out["columns"] = {name: base64.b64encode(buf).decode("ascii")
                      for name, buf in snapshot["columns"].items()}
    for name in _LEDGER_BUFFERS:
        out[name] = base64.b64encode(snapshot[name]).decode("ascii")
    for name in _LEDGER_OPTIONAL_BUFFERS:
        if name in snapshot:
            out[name] = base64.b64encode(snapshot[name]).decode("ascii")
    for name in ("book", "futures_book", "vix_futures_book", "rate_futures_book",
                 "oil_futures_book"):
        if name in snapshot:
            book = dict(snapshot[name])
            book["taken"] = base64.b64encode(book["taken"]).decode("ascii")
            if "memory" in book:
                book["memory"] = base64.b64encode(book["memory"]).decode("ascii")
            out[name] = book
    if "fundamentals" in snapshot:
        out["fundamentals"] = {
            name: base64.b64encode(snapshot["fundamentals"][name]).decode("ascii")
            for name in _LEDGER_FUNDAMENTALS}
    values = list(snapshot["rng"])
    out["rng"] = base64.b64encode(
        struct.pack("<%dd" % len(values), *values)).decode("ascii")
    # Bit patterns wearing floats, as `rng` is, so NaN payloads survive.
    for name in ("cycle_nowcast_rng", "derivatives_rng"):
        if name in snapshot:
            values = list(snapshot[name])
            out[name] = base64.b64encode(
                struct.pack("<%dd" % len(values), *values)).decode("ascii")
    return out


def _snapshot_from_json(payload: dict[str, Any]) -> dict[str, Any]:
    """The inverse of :func:`_snapshot_to_json`, ready for a restore."""
    out = dict(payload)
    out["columns"] = {name: base64.b64decode(text)
                      for name, text in payload["columns"].items()}
    for name in _LEDGER_BUFFERS:
        out[name] = base64.b64decode(payload[name])
    for name in _LEDGER_OPTIONAL_BUFFERS:
        if name in payload:
            out[name] = base64.b64decode(payload[name])
    for name in ("book", "futures_book", "vix_futures_book", "rate_futures_book",
                 "oil_futures_book"):
        if name in payload:
            book = dict(payload[name])
            book["taken"] = base64.b64decode(book["taken"])
            if "memory" in book:
                book["memory"] = base64.b64decode(book["memory"])
            out[name] = book
    if "fundamentals" in payload:
        out["fundamentals"] = {
            name: base64.b64decode(payload["fundamentals"][name])
            for name in _LEDGER_FUNDAMENTALS}
    raw = base64.b64decode(payload["rng"])
    out["rng"] = list(struct.unpack("<%dd" % (len(raw) // 8), raw))
    for name in ("cycle_nowcast_rng", "derivatives_rng"):
        if name in payload:
            raw = base64.b64decode(payload[name])
            out[name] = list(struct.unpack("<%dd" % (len(raw) // 8), raw))
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
           ledger: "DayLedger | None" = None,
           agent_access: dict[str, Any] | None = None) -> "RunManifest":
        """Capture a finished run.

        ``universe`` and ``seed`` are passed rather than read off the engine,
        as ``Checkpoint.of`` requires them, because an engine is built from
        them and keeps neither.

        ``strategy`` is a :class:`StrategySpec` (carried in full, cited by
        its fingerprint) or a reference string for a hand-written agent,
        "repo X at commit Y", which the manifest records as referenced, not
        carried, and declares in :attr:`gaps`. An agent object is refused,
        because the manifest cannot serialise code, and accepting one would
        embed a ``repr`` while implying it embedded a strategy.

        ``universe_source`` is optional provenance (the ``random(n, seed)``
        recipe, an EDGAR snapshot hash and as-of date) recorded for the
        methods section. The roster itself is always embedded, because a
        recipe reproduces only while the generator behaves the same
        and a query is not the data it returned.

        ``derived_from`` is the :class:`tradefloor.Checkpoint` this run
        branched from, for the arm of an experiment rather than a run that
        started at day zero. It records the checkpoint's fingerprint, its
        label and how many log entries it held, which is the fork point.

        Without it, lineage can only be derived. Two branches of one
        experiment share a log prefix and its length is where they parted,
        so a reader holding both manifests can recover the structure by
        comparing them. A reader holding one cannot, and nothing says a run
        is a branch of anything. ``derived_from`` records it.

        ``ledger`` is the :class:`DayLedger` the run filled, and it adds one
        ``days`` block holding the Merkle root over the per-day state hashes,
        the day count and the hash version. The manifest carries the root
        alone. The leaves and the states stay in the ledger, because a year
        of snapshots at forty names is several megabytes and a manifest is
        meant to be read. :func:`verify` is what the block is for.

        ``agent_access`` records how the run's agents were given the market,
        when that was not the default read-only view: ``trusted_agents``
        (handed the live engine), ``hidden_state`` (the labels that declared
        the capability), ``tampered`` (label to the steps on which agent
        code changed the market) and ``margin_interest`` (False when the
        world let its portfolios borrow for free). :meth:`World.manifest`
        fills it. When it is absent the key is not written, so every other
        document is unchanged. It sits outside ``fingerprints``, because it
        describes the agents and the market's replay does not depend on it.
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
            else _scenario_fingerprint(scenario_payload),
            "strategy": strategy_fp,
            # The model rides beside the strategy: the same honesty
            # mechanism, where a shipped preset is cited by name and a
            # custom one by custom-XXXXXXXX, never mistakable for a
            # standard model in a published result.
            "model": engine.model_fingerprint,
            "order_log": _sha(_canonical(log)),
        }
        # The population, only on a populated run, so every other
        # document's fingerprints are the ones they were.
        population_spec = engine.population_spec()
        if population_spec is not None:
            fingerprints["population"] = population_spec["fingerprint"]
        seed = check_seed(seed)
        fingerprints["inputs"] = _sha(_canonical(
            {"seed": seed, **fingerprints}))

        doc = {
            "schema": MANIFEST_SCHEMA,
            "label": label,
            "written_by": {
                # `pretium_version` until 0.9.1, the package's name before
                # 0.5.0; `_writer_version` reads either. `written_by` is in
                # no fingerprint, so the rename moves none.
                "tradefloor_version": version(),
                # The Python version as well, since 0.8.5. The engine does
                # not depend on it, so a replay of this log does not either;
                # an agent re-run to regenerate the log does, because the
                # agent is Python. Manifests written before it was recorded
                # load and replay the same way.
                "platform": {"os": _platform.system(),
                             "machine": _platform.machine(),
                             "python": _platform.python_version()},
                # The FULL preset surface of the model the engine actually
                # ran, not the build's default, with "name" as its
                # fingerprint. Embedding the values is what lets a custom
                # preset travel: a fingerprint identifies, it cannot
                # reconstruct.
                "model": dict(engine.model_params),
                # `preset` since 0.10.0: the default the probe ran under.
                "era": {"probe": ERA_PROBE, "digest": era_fingerprint(),
                        "preset": model_preset()["name"]},
            },
            "seed": seed,
            "universe": universe_payload,
            "universe_source": universe_source,
            "macro": macro_payload,
            "scenario": scenario_payload,
            "strategy": strategy_payload,
            "order_log": log,
            "fingerprints": fingerprints,
            **({} if population_spec is None else {"population": {
                "version": 1,
                "participants": list(population_spec["participants"])}}),
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
        if agent_access:
            try:
                doc["agent_access"] = json.loads(_canonical(agent_access))
            except TypeError:
                raise ValidationError(
                    "agent_access must be JSON-serialisable: it travels "
                    "inside the manifest.") from None
        return cls(doc)

    def to_json(self) -> str:
        """The whole manifest as JSON, the artifact you hand over."""
        return _canonical(self._doc)

    # -- reading -----------------------------------------------------------

    @classmethod
    def from_json(cls, text: str) -> "RunManifest":
        """Load a manifest, checking every carried component's fingerprint.

        A component that arrives not matching the fingerprint it was written
        with is refused by name before anything runs. A manifest that
        changed in transit no longer describes the run it came from, and
        replaying it would produce a market that fails the result check for
        a reason the error could no longer locate.
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
            if part is None:
                continue
            digest = (_scenario_fingerprint(part)
                      if name == "scenario" and isinstance(part, dict)
                      else _sha(_canonical(part)))
            if digest != expected:
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

        block = payload.get("population")
        if (block is None) != (recorded.get("population") is None):
            raise ValidationError(
                "this manifest carries a population fingerprint and no "
                "population, or the reverse. One of them was removed in "
                "transit.")
        if block is not None:
            from .population import Population
            rebuilt = Population.from_dict({"version": block.get("version"),
                                            "participants": block.get("participants", [])})
            if rebuilt.fingerprint != recorded.get("population"):
                raise ValidationError(
                    "the population in this manifest does not match its "
                    "recorded fingerprint. It was edited in transit, and a "
                    "replay under it would trade a different crowd.")

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
        """Replay the run, verify the result, and return the rebuilt market.

        It refuses before replaying if this build is a different era from
        the one that wrote the manifest, because a manifest that silently
        produced different numbers across an era boundary would be trusted
        when it should not be. On a result
        mismatch after every input and the era verified, the error reports
        both platforms and the draw counts, which is where a bisection
        starts.
        """
        self._check_era()
        self._check_lineage()

        engine = replay(self.order_log, seed=self.seed,
                        universe=self.universe, macro=self.macro,
                        model=self._model_for_replay(),
                        population=self.population)

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
        python_there = wrote.get("python")
        python_here = _platform.python_version()
        if python_there is not None and python_there != python_here:
            # Named so that it is ruled out rather than chased. Python 3.12
            # changed float sum(), which is a real cause of two runs of the
            # same AGENT disagreeing, and a reader who sees two versions here
            # will suspect it.
            head += (
                f"It was written under Python {python_there} and replayed "
                f"under {python_here}. That does not explain this: a replay "
                "hands the recorded orders to the engine, which is compiled "
                "Rust, and no Python arithmetic runs between them. A "
                "different Python explains a different order log when an "
                "agent is re-run, which is a different failure. "
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

    @property
    def population(self) -> Any:
        """The :class:`tradefloor.Population` the run was recorded with.

        Rebuilt from the carried participants, or None for an isolated run.
        """
        block = self._doc.get("population")
        if block is None:
            return None
        from .population import Population
        return Population.from_dict({"version": block.get("version"),
                                     "name": "recorded",
                                     "participants": block["participants"]})

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
        mine = era_fingerprint(_era_preset(wrote))
        if mine != era.get("digest"):
            platform_info = wrote.get("platform", {})
            raise ValidationError(
                "this build does not reproduce the manifest's era: the "
                f"fixed probe simulation digests {mine[:12]}... against the "
                f"recorded {str(era.get('digest'))[:12]}.... Written under "
                f"tradefloor {_writer_version(wrote)} on "
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
        """The checkpoint this run branched from, or ``None``.

        ``None`` means a run that started at day zero.

        ``{"checkpoint": <fingerprint>, "label": ..., "entries": <fork point>}``.
        The entry count is where this run's history stops being its parent's,
        so two manifests naming the same checkpoint describe two arms of one
        experiment and the number says where they parted.
        """
        recorded = self._doc.get("derived_from")
        return dict(recorded) if recorded else None

    def verify_lineage(self, checkpoint: Any) -> None:
        """Check that this manifest's declared parent is the given checkpoint.

        The declaration alone names a digest, and a reader holding only the
        manifest cannot test it. A reader holding the checkpoint can. The
        fingerprint must match, and the run's first entries must be the
        checkpoint's log.

        It raises instead of returning a bool, as :meth:`reproduce` does, so
        a caller who writes ``manifest.verify_lineage(cp)`` and reads nothing
        still gets the failure.
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
    def agent_access(self) -> dict[str, Any] | None:
        """How the run's agents were given the market, or ``None``.

        ``None`` means the default read-only view with no privileged agent
        and no tampering. See :meth:`of`.
        """
        recorded = self._doc.get("agent_access")
        return json.loads(_canonical(recorded)) if recorded else None

    @property
    def day_ledger(self) -> dict[str, Any] | None:
        """The run's per-day commitment, or ``None`` when it has none.

        ``{"root": <Merkle root>, "count": <days>, "hash": "state/1"}``,
        under the document's ``day_ledger`` key. It is named apart from
        ``result["days"]``, the number of days the run traded, because one
        is a count and the other is a commitment, and two ``days`` keys at
        two levels would leave a reader working out which one they had
        opened.

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
        """Provenance of the roster, if recorded.

        Informational only. The roster itself is embedded and authoritative.
        """
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
        """The carried spec, or None.

        None also for a strategy that is only referenced.
        :attr:`strategy_reference` holds the reference.
        """
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
        """The coefficient dictionary of the model the run ran under.

        ``"name"`` holds its fingerprint: a shipped preset's name, or
        ``custom-XXXXXXXX`` for a run that must never be mistaken for one.
        """
        return dict(self._doc["written_by"].get("model") or {})

    @property
    def model_fingerprint(self) -> str:
        """The model's fingerprint name, as recorded.

        Falls back to the model dict's own name for manifests written before
        the fingerprint joined :attr:`fingerprints`.
        """
        recorded = self._doc.get("fingerprints", {}).get("model")
        if recorded is not None:
            return recorded
        return str(self.model.get("name", ""))

    # -- honesty about what it does not carry ------------------------------

    @property
    def gaps(self) -> list[str]:
        """What a reader needs from outside this manifest.

        Empty for a complete manifest. A gap is not a defect (a hand-written
        agent is one by design), but the reader needs to know about it, so
        the manifest states it.
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
        """True when every component is embedded or ships with the library.

        That is the condition under which this manifest alone reproduces the
        run.
        """
        return not self.gaps

    def describe(self) -> str:
        """A reader's summary of the manifest.

        It lists what is carried, what is referenced, and what checking it
        here would compare against.
        """
        doc = self._doc
        wrote = doc["written_by"]
        python = wrote["platform"].get("python")
        lines = [
            f"run manifest{f' {self.label!r}' if self.label else ''}: "
            f"seed {doc['seed']}, "
            f"{len(doc['universe']['instruments'])} instruments, "
            f"{doc['result']['days']} days, "
            f"{len(doc['order_log'])} log entries",
            f"  written by tradefloor {_writer_version(wrote)} on "
            f"{wrote['platform']['os']}-{wrote['platform']['machine']}"
            f"{f' under Python {python}' if python else ''}, "
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

    ``seed`` is any integer from 0 to ``2**64 - 1``. Until 0.8.5 it was masked
    to its low 32 bits here, so ``2**32 + 5`` drew seed 5's days and ``-1``
    drew ``2**32 - 1``'s. Every seed below ``2**32`` draws the days it drew;
    one above now draws its own, and a negative one is refused.
    """
    rng = GameRng(check_seed(seed), _VERIFY_STREAM)
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

    Returned by :func:`verify`. It reports instead of raising because k and
    the days drawn are part of the answer on a pass. "This run verifies" is a
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
        that failed under a matching root. Its length is not a count of
        failed days. Reading it that way produced "10 of 9 sampled days did
        not verify" on a nine-day ledger with one edited leaf, so the count in
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
        span the hash covers. A fixed caveat could go on being printed after
        the thing it described had changed.
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
        """Raise when anything did not verify, and return self otherwise.

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
    the manifest was written. A tampered day fails on its own leaf, and a
    tampered predecessor state fails on the day that follows it.

    ## The cost

    With snapshots, checking k days costs k days of simulation, whatever the
    length of the run. A ledger without snapshots reaches day d - 1 by
    running to it, so day d costs d + 1 days, and
    :attr:`Verification.day_runs` reports which of the two was paid.
    :attr:`Verification.restored` counts the sampled days that started from a
    committed predecessor, which is every one of them except day 0.

    The unit is day-runs and engine ticks, and it is exact in those units.
    Wall time tracks it, and the ratio is the figure to quote, because
    seconds on one machine depend as much on what else was running as on
    this function. On ``Universe.random(40, seed=7)``, seed 42, twenty days at
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

    ``seed`` chooses the sample and has no default, for the reason
    ``GameRng`` requires a sequence. A verification is repeatable only if
    the reader can name the days it drew, and a hidden default would make
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
    population = manifest.population
    ticks = 0
    day_runs = 0
    restored = 0

    for day in chosen:
        engine = Engine(seed=manifest.seed, universe=universe,
                        macro_state=macro, model=model, population=population)
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
