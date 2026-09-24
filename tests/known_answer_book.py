"""The known-answer test for the agent-facing book, with its dials on.

`known_answer.py` is the determinism gate for the market nobody trades, and
its digest does not move with the book's dials: at any setting of them an
untraded run is the same market to the bit. So that gate cannot see the
book, and this one exists to. It runs a fixed market with every book dial on
and several agents in it, and hashes what they did: every fill, every
waiting order, every impact row, the prices, and the engine's state hash.

Its own file and its own baseline, so the historical gate's three digests
and the script contract `test_known_answer.py` pins stay exactly as they
were. `test_known_answer.py` checks this baseline too, which is what puts it
in the cross-platform determinism workflow beside the other.

What it exercises, in one chain: the latent depth at every size up to a whole
day's volume; consumption, the maker's re-quote and the latent refill; a
queue of resting orders at one price; the model's flow filling them in parts;
agents crossing each other; cancels; the traded-range fallback is not here
(it is the dial off); the linear permanent law and its attribution; and
`submit_many`'s ordering.

Canonical form as in `known_answer.py`: big-endian f64 throughout, one NaN
pattern, strings length-prefixed, no decimal formatting anywhere.
"""

import hashlib
import struct

import tradefloor

#: Bumped only when the book's behaviour is meant to change.
BOOK_KAT_VERSION = 1

SEED = 20260924
DAYS = 3
STEPS = 6
TICKS = 65

#: The seven dials, at the values suggested for pt-v20.
DIALS = dict(book_depth_coefficient=0.75, book_depth_exponent=0.5,
             book_depth_reach=1.0, book_shared=1.0,
             book_refill_half_life=27.0, book_resting=1.0,
             fill_impact_coefficient=0.314)


def _f64(buf: bytearray, value) -> None:
    if value is None:
        buf.extend(b"\xff\xff\xff\xff\xff\xff\xff\xff")
    elif value != value:
        buf.extend(b"\x7f\xf8\x00\x00\x00\x00\x00\x00")
    else:
        buf.extend(struct.pack(">d", float(value)))


def _text(buf: bytearray, value: str) -> None:
    encoded = str(value).encode("utf-8")
    buf.extend(struct.pack(">I", len(encoded)))
    buf.extend(encoded)


def _fill(buf: bytearray, f: dict) -> None:
    for key in ("agent", "order_id", "ticker", "side", "liquidity",
                "counterparty"):
        _text(buf, f[key])
    for key in ("quantity", "price", "reference", "day", "tick", "sequence"):
        _f64(buf, f[key])


def _instruments():
    sector_names = tradefloor.sectors()
    return [
        tradefloor.Instrument(
            f"BOOK{i}",
            sector_names[i % 12],
            initial_price=12.0 + i * 9.25,
            shares_outstanding=2.0e8 + i * 1.5e7,
            eps=(-0.8 if i in (4, 9) else 0.9 + i * 0.55),
            book_value_per_share=9.0 + i * 2.5,
            revenue_growth=-0.03 + i * 0.025,
            avg_volume=40_000 + i * 180_000,
            beta=0.65 + i * 0.1,
        )
        for i in range(12)
    ]


def _orders(engine, day: int, step: int) -> list[dict]:
    """The step's orders, a fixed function of the day, the step and the
    book as it stands, so they reach into the queue and the latent depth
    whatever the prices happen to be."""
    tickers = engine.tickers
    out = []
    for k, ticker in enumerate(tickers):
        if (k + day + step) % 3:
            continue
        book = engine.book(ticker)
        volume = 40_000 + k * 180_000
        # A taker, sized from a tenth of a percent to a whole day's volume.
        size = round(volume * (0.001, 0.02, 0.1, 0.3, 1.0)[(k + step) % 5])
        side = 1 if (k + day) % 2 == 0 else -1
        out.append({"agent": "taker", "ticker": ticker, "quantity": side * size})
        # Two makers queue at the same touch, one step apart in arrival.
        if book.best_bid is not None:
            out.append({"agent": "maker1", "ticker": ticker,
                        "quantity": round(volume * 0.004),
                        "limit_price": book.best_bid})
            out.append({"agent": "maker2", "ticker": ticker,
                        "quantity": round(volume * 0.004),
                        "limit_price": book.best_bid})
        # And an order a cent inside the spread when there is room.
        if (book.best_bid is not None and book.best_ask is not None
                and book.best_ask - book.best_bid >= 0.03):
            out.append({"agent": "inside", "ticker": ticker,
                        "quantity": -round(volume * 0.002),
                        "limit_price": round(book.best_ask - 0.01, 2)})
    return out


def book_buffer() -> bytes:
    buf = bytearray()
    model = tradefloor.ModelParams.from_preset("pt-v19", **DIALS)
    engine = tradefloor.Engine(seed=SEED, universe=_instruments(), model=model)
    agents = ("taker", "maker1", "maker2", "inside")

    for day in range(DAYS):
        engine.open_market()
        for step in range(STEPS):
            reports = engine.submit_many(_orders(engine, day, step))
            for r in reports:
                _text(buf, r["order_id"])
                for key in ("filled", "average_price", "worst_price",
                            "reference", "resting", "unfilled"):
                    _f64(buf, r[key])
            engine.run_session(9 + (30 + step * TICKS) // 60,
                               (30 + step * TICKS) % 60, 3, TICKS)
            for agent in agents:
                for f in engine.take_fills(agent):
                    _fill(buf, f)
            for r in engine.take_impacts():
                _text(buf, r["agent"])
                _text(buf, r["ticker"])
                for key in ("bought", "sold", "permanent"):
                    _f64(buf, r[key])
            # The second maker withdraws on odd steps: the cancel path.
            if step % 2:
                for o in engine.open_orders("maker2"):
                    engine.cancel(o["order_id"], agent="maker2")
        for o in engine.open_orders():
            _text(buf, o["order_id"])
            _f64(buf, o["remaining"])
        engine.close_market()
        for value in struct.unpack("<%dd" % len(engine.tickers), engine.prices()):
            _f64(buf, value)

    buf.extend(bytes.fromhex(engine.state_hash()))
    _f64(buf, float(engine.draws_consumed))
    return bytes(buf)


def book_digest() -> str:
    return hashlib.sha256(book_buffer()).hexdigest()


if __name__ == "__main__":
    data = book_buffer()
    print(f"pretium book known-answer test v{BOOK_KAT_VERSION}")
    print(f"  package  {tradefloor.version()}")
    print(f"  bytes    {len(data)}")
    print(f"  sha256   {hashlib.sha256(data).hexdigest()}")
