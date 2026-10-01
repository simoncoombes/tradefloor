"""The known-answer test for every shipped preset, one digest each.

`known_answer.py` hashes one simulation on the DEFAULT preset, so its
digest moves whenever the default does, and the claim that every older
preset still replays exactly rested on the coefficient fingerprints and on
the test suite. `docs/SUPPORT.md` makes that claim part of the long-term
support line from 0.8.5, and a claim the determinism gate does not check on
every platform is a claim nobody checks there. This file is the check: the
same fixed market once per shipped preset, named, hashed on its own.

A preset's digest is fixed when the preset first ships in a tagged release,
and it never moves after that. A new preset adds a row to
`known_answer_presets.json`. A row that changes means a frozen preset's
market changed, which no release may do, or a platform disagrees. Neither is
fixed by regenerating the baseline.

What each run is, the same for every preset: the twelve KAT equities of
`known_answer.py`, its seed and its section-5 macro, for 60 sessions of 78
ticks. Sixty sessions cover at least one central-bank meeting (every 29 to
38 sessions), so the daily macro step, the meeting rule and the close
bookkeeping all run, and a dial that acts only at a close or a meeting is
covered as well as one that acts every tick. Nobody trades, so the
agent-facing book is not exercised here; `known_answer_book.py` covers it.

Hashed after every close: the nine columns `known_answer.py` hashes, and a
fixed list of macro fields read by name. Then the last session's tick
prices and the draw count. Nothing the library REPORTS about itself is in
the buffer (not the model fingerprint, not `state_hash`, not
`model_preset()`), because a release may add a field to those without any
market moving, and that must not read as a frozen preset changing.

Canonical form as in `known_answer.py`: big-endian f64 throughout, one NaN
pattern, no decimal formatting.
"""

import hashlib
import struct

import tradefloor

#: Bumped only if this harness itself changes: the roster, the macro, the
#: horizon or what is hashed. A frozen preset's market never bumps it,
#: because a frozen preset's market does not change.
PRESET_KAT_VERSION = 1

SEED = 20260820
SESSIONS = 60
TICKS = 78

#: The columns hashed after every close, the same nine `known_answer.py`
#: hashes for the default preset.
COLUMNS = ("price", "previous_close", "open", "high", "low", "volume",
           "market_cap", "mispricing_s", "garch_variance")

#: Macro fields read by name. A fixed list, so a field a later release adds
#: to `Engine.macro_fields` does not move a frozen preset's digest.
MACRO = ("vix", "federal_funds_rate", "corporate_bond_yield", "inflation_rate",
         "qe_pe_boost", "fear_greed_index", "gdp_growth", "unemployment_rate",
         "tariff_rate", "oil_price", "treasury_yield_2y", "treasury_yield_10y")

#: The cycle is a name. It is hashed as its place in this tuple, and a name
#: outside it stops the run rather than hashing as something else.
CYCLES = ("expansion", "peak", "contraction", "trough", "recovery")


def _f64(buf: bytearray, value: float) -> None:
    if value != value:  # NaN, normalised as in known_answer.py
        buf.extend(b"\x7f\xf8\x00\x00\x00\x00\x00\x00")
    else:
        buf.extend(struct.pack(">d", float(value)))


def _instruments() -> list:
    """The twelve KAT equities, as `known_answer.py` section 5 builds them."""
    sector_names = tradefloor.sectors()
    return [
        tradefloor.Instrument(
            f"KAT{i}",
            sector_names[i % 12],
            initial_price=20.0 + i * 7.5,
            shares_outstanding=2.5e8 + i * 1e7,
            eps=(-1.0 if i in (5, 11) else 1.0 + i * 0.6),
            book_value_per_share=10.0 + i * 2.0,
            revenue_growth=-0.02 + i * 0.03,
            avg_volume=250_000 + i * 100_000,
            beta=0.7 + i * 0.1,
        )
        for i in range(12)
    ]


def preset_buffer(name: str) -> bytes:
    """Run the fixed market on one named preset and return its buffer."""
    buf = bytearray()
    instruments = _instruments()
    engine = tradefloor.Engine(
        seed=SEED,
        universe=instruments,
        macro_state=tradefloor.Macro(
            vix=19.5, federal_funds_rate=0.0425, corporate_bond_yield=0.0610,
            inflation_rate=0.031, qe_pe_boost=0.0, fear_greed_index=38.0,
            cycle="contraction",
        ),
        model=name,
    )
    n = len(instruments)
    for _ in range(SESSIONS):
        engine.open_market()
        engine.run_session(9, 30, 3, TICKS, volatility=1.0)
        engine.close_market()
        for field in COLUMNS:
            for value in struct.unpack("<%dd" % n, engine.column(field)):
                _f64(buf, value)
        macro = engine.macro_fields
        for field in MACRO:
            _f64(buf, macro[field])
        if macro["cycle"] not in CYCLES:
            raise ValueError(f"unknown cycle {macro['cycle']!r}; the preset "
                             "known-answer harness has to learn it first")
        _f64(buf, float(CYCLES.index(macro["cycle"])))
    for value in struct.unpack(
        "<%dd" % (engine.session_ticks_written * n), engine.session_prices()
    ):
        _f64(buf, value)
    _f64(buf, float(engine.draws_consumed))
    return bytes(buf)


def preset_digest(name: str) -> str:
    return hashlib.sha256(preset_buffer(name)).hexdigest()


def preset_digests() -> dict:
    """Every preset the build ships, in the order `preset_names()` gives."""
    return {name: preset_digest(name) for name in tradefloor.preset_names()}


def combined_digest(digests: dict) -> str:
    """One digest over every preset's, for the line the gate compares.

    Over `name digest` lines in the order given, so a preset added, dropped
    or renamed moves it as surely as one whose market moved.
    """
    text = "".join(f"{name} {digest}\n" for name, digest in digests.items())
    return hashlib.sha256(text.encode("ascii")).hexdigest()


if __name__ == "__main__":
    digests = preset_digests()
    print(f"pretium preset known-answer test v{PRESET_KAT_VERSION}")
    print(f"  package  {tradefloor.version()}")
    for name, digest in digests.items():
        print(f"  {name:<8} {digest}")
    print(f"  all      {combined_digest(digests)}")
