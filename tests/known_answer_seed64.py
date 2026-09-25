"""The known-answer test for a seed above 2**32.

Seeds are 64-bit from 0.8.5. Every seed below 2**32 is the market it was,
and the other known-answer files prove that on every target by not moving:
`known_answer.json`, `known_answer_book.json` and the nineteen rows of
`known_answer_presets.json` all run 32-bit seeds, and none of them changed
when seeds widened. This file is the other half, that a wide seed is one
market on every target, through each derivation a seed takes: the raw
generator, the universe generator, the engine's substreams and a surgery.

Its own file and its own baseline, as the book's gate has, so the digests
the older files carry stay exactly as they were. `test_known_answer.py`
checks this baseline and `known_answer.py` prints it as its sixth line,
which is what puts it in the five-target determinism workflow.

Canonical form as in `known_answer.py`: big-endian f64 throughout, one NaN
pattern, an absent value as all ones, no decimal formatting anywhere.
"""

import hashlib
import struct

import tradefloor

#: Bumped only if this harness itself changes. 64-bit seeding is a
#: contract (rust/src/rng.rs), so the market this runs never moves.
SEED64_KAT_VERSION = 1

#: Above 2**32 on purpose, and with the top bit set, so a build that
#: truncated a seed to its low 32 bits would hash seed 12345 and a build that
#: lost the top bit would hash 2**32 + 12345.
HIGH_SEED = 2**63 + 12345

#: The widest surgery seed, for the surgery's own wide derivation.
SURGERY_SEED = 2**64 - 1

#: A frozen preset, by name, so this row moves only if 64-bit seeding moves
#: or a platform disagrees, never because the default did.
PRESET = "pt-v19"

SESSIONS = 5
TICKS = 78

COLUMNS = ("price", "previous_close", "open", "high", "low", "volume",
           "market_cap", "mispricing_s", "garch_variance")


def _f64(buf: bytearray, value) -> None:
    if value is None:
        buf.extend(b"\xff\xff\xff\xff\xff\xff\xff\xff")
    elif value != value:
        buf.extend(b"\x7f\xf8\x00\x00\x00\x00\x00\x00")
    else:
        buf.extend(struct.pack(">d", float(value)))


def high_seed_buffer() -> bytes:
    """Every place a seed enters, at `HIGH_SEED`, hashed.

    The generator's draws on sequence 99; the roster
    `Universe.random(12, seed=HIGH_SEED)` draws; that roster run for five
    sessions of 78 ticks on `PRESET`, the nine columns hashed after each
    close, then the last session's tick prices and the draw count; and eight
    draws of a surgery of the news stream under `SURGERY_SEED`.
    """
    buf = bytearray()
    rng = tradefloor.GameRng(HIGH_SEED, 99)
    for i in range(32):
        _f64(buf, rng.next_float())
        _f64(buf, rng.next_normal())
        if i % 3 == 0:
            _f64(buf, float(rng.next_int(-1000, 1000)))

    universe = tradefloor.Universe.random(12, seed=HIGH_SEED)
    for inst in universe:
        for value in (inst.initial_price, inst.shares_outstanding, inst.eps,
                      inst.book_value_per_share, inst.revenue_growth,
                      inst.avg_volume, inst.beta, inst.short_interest):
            _f64(buf, value)

    engine = tradefloor.Engine(seed=HIGH_SEED, universe=universe, model=PRESET)
    n = len(universe)
    for _ in range(SESSIONS):
        engine.open_market()
        engine.run_session(9, 30, 3, TICKS, volatility=1.0)
        engine.close_market()
        for field in COLUMNS:
            for value in struct.unpack("<%dd" % n, engine.column(field)):
                _f64(buf, value)
    for value in struct.unpack(
        "<%dd" % (engine.session_ticks_written * n), engine.session_prices()
    ):
        _f64(buf, value)
    _f64(buf, float(engine.draws_consumed))

    for value in tradefloor.Engine.surgery_draws(
            HIGH_SEED, "news", SURGERY_SEED, ["uniform", "normal"] * 4):
        _f64(buf, value)
    return bytes(buf)


def high_seed_digest() -> str:
    return hashlib.sha256(high_seed_buffer()).hexdigest()


if __name__ == "__main__":
    data = high_seed_buffer()
    print(f"pretium 64-bit seed known-answer test v{SEED64_KAT_VERSION}")
    print(f"  package  {tradefloor.version()}")
    print(f"  seed     {HIGH_SEED}")
    print(f"  bytes    {len(data)}")
    print(f"  sha256   {hashlib.sha256(data).hexdigest()}")
