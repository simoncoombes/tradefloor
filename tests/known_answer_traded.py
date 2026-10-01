"""The known-answer test for a traded run through `tradefloor.evaluate`.

The other known-answer files pin the engine. `known_answer.py`,
`known_answer_presets.py` and `known_answer_seed64.py` hash markets nobody
trades, and `known_answer_book.py` drives the engine's book directly. None
of them goes through the Python harness, so none covers what an agent
benchmark reports: the orders the agents sent, what filled, and the
scorecards. This file does. It runs the reference agents, plus one scripted
agent that sends limit orders and cancels them, through `evaluate` on a
fixed seed, roster and preset, and hashes three things per agent:

- the order log: what `act()` returned at each step, after the step and
  day it was sent on and the prices the agent was shown, and what
  `explain()` answered at each close;
- the fills: every row of the agent's `portfolio.fills`, the market
  orders, the part of a limit order that filled at once, and what a
  waiting order filled later in a session; then the closing prices of the
  agent's own market;
- the scorecard: every field of `Scorecard`, by name, except the text of
  `errors` and `partial_fills`, which are hashed as counts. Their lines
  are messages for a reader, and rewording one must not move a market's
  digest. What they report is in the fills.

The agents are wrapped to record, and the wrapper changes nothing about the
run: it passes `privileged` through, has `explain` only when the agent
does, and returns what the agent returned. `test_known_answer.py` checks
this by comparing the scorecards with an unwrapped run.

`test_known_answer.py` checks the baseline in `known_answer_traded.json`,
and `known_answer.py` prints the combined digest as its seventh line, which
puts this run in the five-target determinism workflow.

Re-basing. The run uses pt-v20 by name, as it stands on the branch, so the
row moves when pt-v20 moves. The baseline records pt-v20's row from
`known_answer_presets.json` as `presetRow`, and the test fails with that
reason when the two disagree. A change to pt-v20, to a reference agent or
to what `evaluate` scores moves this digest on purpose. Then run
``python tests/known_answer_traded.py --write`` on the changed tree, which
rewrites the digests and `presetRow`, and add a sentence to `note` saying
what moved and why. The per-agent digests show which agent and which part
moved, so a scoring change that leaves the orders and fills where they were
can say so. A change in this file's own harness bumps
`TRADED_KAT_VERSION`. A digest that moves on one platform and not on
another is never re-based: it is the failure the workflow exists to catch.

Canonical form as in `known_answer.py`: big-endian f64, one NaN pattern,
an absent value as all ones, integers as big-endian i64, strings
length-prefixed, no decimal formatting anywhere.
"""

import hashlib
import json
import struct
import sys
from pathlib import Path

import tradefloor
from tradefloor.baselines import reference_agents

HERE = Path(__file__).resolve().parent

#: Bumped only if this harness changes: the roster, the agents, the
#: horizon or what is hashed. A change to the preset, an agent or the
#: scoring re-bases the baseline without bumping it (see the module text).
TRADED_KAT_VERSION = 1

SEED = 20260930
#: Seeds the random baseline only (see `reference_agents`).
AGENT_SEED = 5
PRESET = "pt-v20"
DAYS = 10
STEPS_PER_DAY = 6
TICKS_PER_STEP = 65
CASH = 1_000_000.0
MAX_LEVERAGE = 2.0

#: The agents, in the order they are run and hashed: the reference set,
#: then the scripted limit-order agent.
AGENTS = ("buy_and_hold", "random", "momentum", "mean_reversion", "oracle",
          "resting")

#: The scorecard fields hashed by value, in this order.
SCORECARD_FIELDS = (
    "name", "pnl", "return_pct", "trades", "turnover", "impact_bps",
    "max_leverage", "rejected", "explanations", "explanation_accuracy",
    "final_net_worth", "seed", "universe_fingerprint", "strategy_fingerprint",
    "model_fingerprint", "trusted", "uses_hidden_state", "tampered",
    "equity_curve", "max_drawdown_pct", "ruined", "leverage_refusals",
    "explanation_baseline",
)

#: The scorecard fields hashed as a count of their lines.
SCORECARD_COUNTED = ("errors", "partial_fills")

#: Every key a fill row can carry, in the order hashed. A market order's
#: row has the first ten; a limit order's adds the rest, and an absent key
#: is hashed as absent.
FILL_KEYS = ("ticker", "quantity", "price", "worst_price", "notional",
             "requested", "partial", "day", "step", "tick", "order_id",
             "liquidity", "counterparty", "limit")

PARTS = ("orders", "fills", "scorecard")


def _f64(buf: bytearray, value) -> None:
    if value is None:
        buf.extend(b"\xff\xff\xff\xff\xff\xff\xff\xff")
    elif value != value:
        buf.extend(b"\x7f\xf8\x00\x00\x00\x00\x00\x00")
    else:
        buf.extend(struct.pack(">d", float(value)))


def _int(buf: bytearray, value) -> None:
    buf.extend(struct.pack(">q", int(value)))


def _text(buf: bytearray, value) -> None:
    if value is None:
        buf.extend(b"\xff\xff\xff\xff")
        return
    encoded = str(value).encode("utf-8")
    buf.extend(struct.pack(">I", len(encoded)))
    buf.extend(encoded)


def _value(buf: bytearray, value) -> None:
    """One value of a fill row or a scorecard, tagged with its type."""
    if value is None:
        buf.extend(b"N")
    elif isinstance(value, bool):
        buf.extend(b"T" if value else b"F")
    elif isinstance(value, int):
        buf.extend(b"I")
        # A seed may be any 64-bit unsigned integer, wider than i64.
        buf.extend(struct.pack(">Q", value))
    elif isinstance(value, float):
        buf.extend(b"D")
        _f64(buf, value)
    elif isinstance(value, str):
        buf.extend(b"S")
        _text(buf, value)
    elif isinstance(value, (list, tuple)):
        buf.extend(b"L")
        _int(buf, len(value))
        for item in value:
            _value(buf, item)
    else:
        raise TypeError(f"no canonical form for {type(value).__name__}")


def instruments():
    """Twelve equities, one per sector, set out by hand so the run does not
    depend on the universe generator."""
    sector_names = tradefloor.sectors()
    return [
        tradefloor.Instrument(
            f"TRD{i}",
            sector_names[i % 12],
            initial_price=15.0 + i * 8.5,
            shares_outstanding=3.0e8 + i * 2.0e7,
            eps=(-0.7 if i in (3, 10) else 1.1 + i * 0.5),
            book_value_per_share=8.0 + i * 2.25,
            revenue_growth=-0.025 + i * 0.02,
            avg_volume=300_000 + i * 150_000,
            beta=0.6 + i * 0.1,
        )
        for i in range(12)
    ]


class Resting:
    """A scripted agent that trades through limit orders.

    The reference agents send market orders only, so without this the
    digest would not reach `evaluate`'s limit path: a `tf.Limit` that rests
    in the book, fills later in a session and is collected after it, a new
    limit replacing a waiting one, a `tf.Cancel`, and a limit priced
    through the touch that fills at once. Every order is a fixed function
    of the step, the day and the book as it stands. It buys more than it
    sells, so from the seventh day the leverage limit refuses some of its
    orders, and the digest covers the refusal path as well.
    """

    def act(self, obs):
        out = {}
        waiting = {o["ticker"] for o in obs.portfolio.open_orders()}
        for k, ticker in enumerate(obs.tickers):
            phase = (k + obs.step) % 6
            size = float(round(obs.avg_volume(ticker) * 0.001))
            if phase == 0:
                book = obs.book(ticker)
                if (k + obs.day) % 2 == 0 and book.best_bid is not None:
                    out[ticker] = tradefloor.Limit(size, book.best_bid)
                elif book.best_ask is not None:
                    out[ticker] = tradefloor.Limit(-size, book.best_ask)
            elif phase == 3 and ticker in waiting:
                out[ticker] = tradefloor.Cancel()
            elif phase == 5 and k % 4 == 1:
                book = obs.book(ticker)
                if book.best_ask is not None:
                    out[ticker] = tradefloor.Limit(
                        size, round(book.best_ask + 0.05, 2))
        return out


class _Recorder:
    """Wraps an agent and writes its order log, changing nothing it does."""

    def __init__(self, agent):
        self._agent = agent
        self.privileged = bool(getattr(agent, "privileged", False))
        self.log = bytearray()
        #: How many market orders, limits and cancels the agent sent.
        self.kinds = {"M": 0, "L": 0, "C": 0}
        self.portfolio = None
        self.market = None

    def act(self, obs):
        # The views the harness hands every step. They read the live
        # portfolio and engine, so after the run they give the agent's
        # whole fill list and its market's closing prices.
        self.portfolio, self.market = obs.portfolio, obs.engine
        orders = self._agent.act(obs)
        buf = self.log
        buf.extend(b"A")
        _int(buf, obs.step)
        _int(buf, obs.day)
        for price in obs.prices:
            _f64(buf, price)
        if orders is None:
            buf.extend(b"0")
            return orders
        items = list(orders.items())
        _int(buf, len(items))
        for ticker, order in items:
            _text(buf, ticker)
            if isinstance(order, tradefloor.Limit):
                kind = b"L"
                buf.extend(kind)
                _f64(buf, order.quantity)
                _f64(buf, order.price)
            elif isinstance(order, tradefloor.Cancel):
                kind = b"C"
                buf.extend(kind)
            else:
                kind = b"M"
                buf.extend(kind)
                _f64(buf, float(order))
            self.kinds[kind.decode()] += 1
        return orders


class _ExplainingRecorder(_Recorder):
    """The same, for an agent with `explain`. A wrapper that always had
    `explain` would add explained days to a card that has none."""

    def explain(self, day):
        answer = self._agent.explain(day)
        self.log.extend(b"E")
        _int(self.log, day)
        _text(self.log, answer)
        return answer


def _wrap(agent):
    if callable(getattr(agent, "explain", None)):
        return _ExplainingRecorder(agent)
    return _Recorder(agent)


def agents() -> dict:
    """The agents this run trades, in `AGENTS` order, unwrapped."""
    out = dict(reference_agents(seed=AGENT_SEED))
    out["resting"] = Resting()
    assert tuple(out) == AGENTS, tuple(out)
    return out


def evaluate(entrants: dict) -> dict:
    """`tradefloor.evaluate` with this run's settings."""
    return tradefloor.evaluate(
        entrants, seed=SEED, universe=instruments(), days=DAYS,
        steps_per_day=STEPS_PER_DAY, ticks_per_step=TICKS_PER_STEP,
        cash=CASH, max_leverage=MAX_LEVERAGE, model=PRESET)


def traded_run() -> tuple[dict, dict]:
    """Run the agents. Returns the scorecards and each agent's recorder."""
    recorders = {name: _wrap(agent) for name, agent in agents().items()}
    return evaluate(recorders), recorders


def part_buffers(scores: dict, recorders: dict) -> dict:
    """Each agent's three canonical buffers, keyed by name, then part."""
    out = {}
    for name in AGENTS:
        rec, card = recorders[name], scores[name]
        fills = bytearray()
        rows = rec.portfolio.fills if rec.portfolio is not None else []
        _int(fills, len(rows))
        for row in rows:
            extra = sorted(set(row) - set(FILL_KEYS))
            if extra:
                raise KeyError(f"a fill row has keys this harness does not "
                               f"hash: {extra}. Add them to FILL_KEYS and "
                               f"bump TRADED_KAT_VERSION.")
            for key in FILL_KEYS:
                _value(fills, row.get(key))
        prices = rec.market.prices() if rec.market is not None else b""
        for (price,) in struct.iter_unpack("<d", prices):
            _f64(fills, price)

        scorecard = bytearray()
        values = card.as_dict()
        for field in SCORECARD_FIELDS:
            _text(scorecard, field)
            value = values[field]
            if field == "explanations":
                value = [list(pair) for pair in value]
            _value(scorecard, value)
        for field in SCORECARD_COUNTED:
            _text(scorecard, field)
            _int(scorecard, len(values[field]))

        out[name] = {"orders": bytes(rec.log), "fills": bytes(fills),
                     "scorecard": bytes(scorecard)}
    return out


def part_digests(buffers: dict) -> dict:
    return {name: {part: hashlib.sha256(buffers[name][part]).hexdigest()
                   for part in PARTS}
            for name in AGENTS}


def combined_buffer(buffers: dict) -> bytes:
    buf = bytearray()
    for name in AGENTS:
        _text(buf, name)
        for part in PARTS:
            _text(buf, part)
            data = buffers[name][part]
            _int(buf, len(data))
            buf.extend(data)
    return bytes(buf)


def traded_digest() -> str:
    """The combined digest `known_answer.py` prints as its seventh line."""
    return hashlib.sha256(combined_buffer(part_buffers(*traded_run()))).hexdigest()


def _write(baseline_path: Path) -> dict:
    """Rewrite the baseline's digests from this tree, keeping its note."""
    buffers = part_buffers(*traded_run())
    data = combined_buffer(buffers)
    presets = json.loads((HERE / "known_answer_presets.json")
                         .read_text(encoding="utf-8"))
    old = (json.loads(baseline_path.read_text(encoding="utf-8"))
           if baseline_path.exists() else {})
    doc = {
        "kind": "tradefloor.known-answer.traded",
        "tradedKatVersion": TRADED_KAT_VERSION,
        "seed": SEED,
        "agentSeed": AGENT_SEED,
        "preset": PRESET,
        "presetRow": presets["presets"][PRESET],
        "days": DAYS,
        "stepsPerDay": STEPS_PER_DAY,
        "ticksPerStep": TICKS_PER_STEP,
        "cash": CASH,
        "maxLeverage": MAX_LEVERAGE,
        "agents": part_digests(buffers),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "note": old.get("note", ""),
    }
    baseline_path.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    return doc


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        doc = _write(HERE / "known_answer_traded.json")
        print(f"wrote known_answer_traded.json: {doc['sha256']}")
        print("Now add a sentence to its note saying what moved and why.")
        sys.exit(0)
    buffers = part_buffers(*traded_run())
    data = combined_buffer(buffers)
    print(f"tradefloor traded known-answer test v{TRADED_KAT_VERSION}")
    print(f"  package  {tradefloor.version()}")
    print(f"  preset   {PRESET}")
    print(f"  bytes    {len(data)}")
    for name, parts in part_digests(buffers).items():
        for part, digest in parts.items():
            print(f"  {name:<15} {part:<10} {digest}")
    print(f"  sha256   {hashlib.sha256(data).hexdigest()}")
