"""The engine's news as text an agent reads, with the leak guard.

What the engine has
-------------------
One kind of news. At each `open_market` the engine draws, for every company,
a uniform (does news occur today, probability `endogenous_news_intensity`)
and a normal `z`; an event is `{company, sector, price_impact = sigma * z}`
(`rust/src/engine.rs`, `open_market`). The day's events sit in
`state_snapshot()["session_news"]` until the next open. Each tick of that day
adds `price_impact / 390` to the announcer's price state and a small share
(`news_peer_weight`, 5% on pt-v19, more in a crisis) to its sector peers, so
the whole move lands linearly over the session and none of it before the
first tick. The engine models no earnings calendar, no guidance, no analysts
and no takeovers: an event has a company, a sector, a sign and a size, and
nothing else.

What this module does
---------------------
`headlines_for(engine_or_snapshot, day=, tick=, tickers=)` returns the
`Headline`s an agent may see at that clock. A headline says which company,
and whether the news is good or bad, in the words a wire headline would use
("AAS tops Q3 profit estimates", "Castell Research downgrades AAQ to
underperform"). The storyline (earnings, guidance, analyst, contract,
regulatory, capital return, and trial results for healthcare) is DECORATION:
the engine does not model it, so it is drawn from a hash of the event's
identity and says nothing the engine knows. That is why it lives only in the
text and `category` names the engine's own arm ("company", or "sector" /
"market" for events the engine can carry but does not generate today).

The leak guard
--------------
`price_impact` is the answer key. It is read in exactly one place,
`news_facts`, and reduced there to its sign; the text is written from a
`NewsFact` that has no magnitude field, so no template, word, number or
ordering can depend on the size of the move. The text is seeded by
`(version, arm, day, ticker, sector)` through SHA-256, never by a global RNG
and never by the impact. `tests/test_headlines_leak.py` checks it three ways:
the text is byte-identical under any same-sign change of every impact; over
thousands of engine events the best predictor of |price_impact| built from
the text does no better than the sign alone; and a deliberately leaky
generator fails that same test by a wide margin.

Timing
------
A headline is released at tick `RELEASE_TICK` (1) of its day: the first tick
that prices the event. At tick 0 after the open, the event exists but no
price reflects it yet, so nothing is shown. See docs/serve/HEADLINES.md for
what a visible direction is worth to an agent in this engine (a lot, because
the engine prices news slowly) and the engine change it argues for.

Standard library only, like `tradefloor.serve.types`.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tradefloor.serve.types import Headline

__all__ = [
    "RELEASE_TICK", "TEXT_VERSION", "NewsFact", "headline_text", "headlines_for",
    "news_day", "news_facts",
]

#: The tick of its own day at which a headline is released. Tick 1 is the
#: first tick whose price carries any of the event (measured: the
#: `company_news` attribution is exactly zero at the open and
#: `price_impact / 390` after one tick). Later is allowed; earlier is not.
RELEASE_TICK = 1

#: Part of every seed. Changing any template or the choice logic changes
#: text for the same event, so it bumps this and the golden test with it.
TEXT_VERSION = "tradefloor.headlines/1"

TICKS_PER_SESSION = 390


# -- the one place the answer key is read ----------------------------------------


@dataclass(frozen=True)
class NewsFact:
    """Everything a headline may be written from.

    There is deliberately no magnitude field. `direction` is the sign of the
    event's price impact, +1 or -1: what a real headline says ("beats",
    "misses"). The size is dropped in `news_facts` and never reaches the text.
    """

    day: int
    arm: str                 # "company" | "sector" | "market": the engine's news arms
    direction: int           # +1 good news, -1 bad news
    ticker: str | None       # the announcing company's ticker (company arm)
    sector: str | None       # the event's sector key, e.g. "industrials"


def news_day(snapshot: Mapping[str, Any]) -> int | None:
    """The trading day the snapshot's `session_news` belongs to.

    The engine clears and regenerates the list at `open_market` and leaves it
    in place through `close_market`, while `day_count` counts completed
    closes. So while the market is open the news is today's (`day_count`);
    once it has closed it is the day just finished (`day_count - 1`). Before
    the first open there is no news day.
    """
    count = int(snapshot["day_count"])
    if bool(snapshot["market_open"]):
        return count
    return count - 1 if count > 0 else None


def news_facts(snapshot: Mapping[str, Any], *, tickers: Sequence[str]) -> list[NewsFact]:
    """The snapshot's news, with the magnitude removed.

    `tickers` is the session's roster in the engine's order (`Engine.tickers`,
    `SessionInfo.tickers`). The snapshot names a company by its engine id
    (`"AAS-18"`, in a key the snapshot calls "ticker"), and the roster order
    is what maps an id to the ticker an agent trades.

    An event whose impact is None, zero or NaN is dropped: the engine reads
    impacts truthy-or (`rust/src/market/factors.rs`), so such an event moves
    no price and is not news.
    """
    day = news_day(snapshot)
    events = list(snapshot.get("session_news") or [])
    if day is None or not events:
        return []
    ids = list(snapshot["tickers"])
    if len(ids) != len(tickers):
        raise ValueError(
            f"the roster has {len(tickers)} tickers and the snapshot {len(ids)} "
            "companies; pass the session's tickers in the engine's order")
    by_id = dict(zip(ids, tickers))
    facts = []
    for event in events:
        impact = event.get("price_impact")
        if impact is None or math.isnan(impact) or impact == 0.0:
            continue
        direction = 1 if impact > 0 else -1
        # From here on the impact is gone. Nothing below this line may read it.
        company = event.get("ticker")
        sector = event.get("sector")
        if company is not None:
            if company not in by_id:
                raise ValueError(f"news names company {company!r}, which is not in the roster")
            facts.append(NewsFact(day, "company", direction, by_id[company], sector))
        elif sector is not None:
            facts.append(NewsFact(day, "sector", direction, None, sector))
        else:
            facts.append(NewsFact(day, "market", direction, None, None))
    return facts


# -- text ------------------------------------------------------------------------------
#
# Rules every template keeps, enforced by tests/test_headlines.py:
#   * it says what happened and which way, never how much: no intensity words
#     ("soars", "sharply", "record", "slightly"), no numbers but the quarter;
#   * it makes no claim about the share price, which at release has not moved;
#   * an up template carries an up cue word and no down cue, and vice versa;
#   * the storyline, template, subject and firm are all chosen by the hash of
#     the event's identity, never by the event's size.

_FIRMS = (
    "Halvard & Co", "Castell Research", "Whitmore Partners", "Ashgrove Capital",
    "Pemberton Brothers", "Lindqvist Markets", "Oakhurst Advisory", "Carrow Securities",
)

# What a company in each sector is called in a headline, and what it sells.
_SECTOR_WORDS: dict[str, tuple[str, str, str]] = {
    # key: (display name, company noun, business noun)
    "technology": ("Technology", "software maker", "cloud services"),
    "financial_services": ("Financial services", "lender", "payments processing"),
    "healthcare": ("Healthcare", "drugmaker", "hospital supply"),
    "energy": ("Energy", "oil producer", "pipeline capacity"),
    "consumer_discretionary": ("Consumer discretionary", "retailer", "retail distribution"),
    "consumer_staples": ("Consumer staples", "food maker", "private-label supply"),
    "industrials": ("Industrials", "machinery maker", "equipment supply"),
    "materials": ("Materials", "chemicals maker", "raw materials supply"),
    "real_estate": ("Real estate", "property group", "office leasing"),
    "utilities": ("Utilities", "utility", "power purchase"),
    "telecommunications": ("Telecommunications", "telecom operator", "network services"),
    "transportation": ("Transportation", "freight carrier", "freight haulage"),
}

# {S} subject (ticker, or "Drugmaker AAS"), {t} ticker, {q} quarter,
# {firm} analyst firm, {biz} the sector's business noun.
_COMPANY_STORIES: dict[str, dict[int, tuple[str, ...]]] = {
    "earnings": {
        1: ("{S} tops {q} profit estimates",
            "{S} {q} earnings beat analyst forecasts",
            "{S} reports {q} results ahead of expectations",
            "{S} beats {q} revenue and earnings estimates",
            "{S} {q} profit comes in above consensus"),
        -1: ("{S} misses {q} profit estimates",
             "{S} {q} earnings fall short of analyst forecasts",
             "{S} reports {q} results below expectations",
             "{S} misses {q} revenue and earnings estimates",
             "{S} {q} profit comes in below consensus"),
    },
    "guidance": {
        1: ("{S} raises full-year outlook",
            "{S} lifts annual sales guidance",
            "{S} guides next quarter above consensus",
            "{S} boosts profit forecast for the year"),
        -1: ("{S} cuts full-year outlook",
             "{S} lowers annual sales guidance",
             "{S} guides next quarter below consensus",
             "{S} warns on profit for the year"),
    },
    "analyst": {
        1: ("{firm} upgrades {t} to outperform",
            "{t} upgraded to buy at {firm}",
            "{firm} turns positive on {t}",
            "{firm} raises price target on {t}"),
        -1: ("{firm} downgrades {t} to underperform",
             "{t} downgraded to sell at {firm}",
             "{firm} turns cautious on {t}",
             "{firm} cuts price target on {t}"),
    },
    "business": {
        1: ("{S} wins multi-year {biz} contract",
            "{S} secures new {biz} agreement",
            "{S} expands {biz} partnership"),
        -1: ("{S} loses {biz} contract",
             "{S} delays {biz} rollout",
             "{S} flags disruption in {biz} business"),
    },
    "regulatory": {
        1: ("{S} wins regulatory approval for new product",
            "Regulators clear {t} expansion plan",
            "{S} settles patent dispute on favourable terms"),
        -1: ("{S} faces regulatory probe",
             "{S} hit with lawsuit over product claims",
             "Regulators reject {t} application"),
    },
    "capital": {
        1: ("{S} announces share repurchase program",
            "{S} raises quarterly dividend",
            "{S} board approves buyback"),
        -1: ("{S} chief financial officer departs",
             "{S} delays annual report filing",
             "{S} pauses dividend increase"),
    },
}

# Storylines only one sector gets. The sector is part of the event's identity
# and independent of its size, so a sector-specific pool leaks nothing.
_SECTOR_STORIES: dict[str, dict[str, dict[int, tuple[str, ...]]]] = {
    "healthcare": {
        "trial": {
            1: ("{S} drug trial meets main goal",
                "{S} late-stage study meets primary endpoint"),
            -1: ("{S} drug trial misses main goal",
                 "{S} late-stage study misses primary endpoint"),
        },
    },
}

_SECTOR_ARM: dict[int, tuple[str, ...]] = {
    1: ("Industry data point to firmer {sector} demand",
        "{Sector} outlook improves on new industry survey"),
    -1: ("Industry data point to softer {sector} demand",
         "{Sector} outlook worsens on new industry survey"),
}

_MARKET_ARM: dict[int, tuple[str, ...]] = {
    1: ("Upbeat economic data lift market outlook",
        "Economic data beat forecasts across the board"),
    -1: ("Economic data disappoint, clouding market outlook",
         "Economic data miss forecasts across the board"),
}


class _Picker:
    """Deterministic choices from the event's identity: SHA-256 of the key,
    four bytes per choice, extended by re-hashing if ever exhausted."""

    def __init__(self, key: str) -> None:
        self._key = key.encode()
        self._buf = hashlib.sha256(self._key).digest()
        self._pos = 0

    def index(self, n: int) -> int:
        if self._pos + 4 > len(self._buf):
            self._buf = hashlib.sha256(self._buf + self._key).digest()
            self._pos = 0
        v = int.from_bytes(self._buf[self._pos:self._pos + 4], "big")
        self._pos += 4
        return v % n


def _seed_key(fact: NewsFact) -> str:
    # The event's identity: at most one endogenous event per company per day.
    # NOT the direction (so both signs of an event share a storyline) and
    # never anything derived from the impact.
    return f"{TEXT_VERSION}|{fact.arm}|{fact.day}|{fact.ticker or ''}|{fact.sector or ''}"


def _quarter(day: int) -> str:
    # The quarter just ended, read off the trading-day index (63 a quarter).
    return f"Q{(day // 63 + 3) % 4 + 1}"


def _sector_words(sector: str | None) -> tuple[str, str, str]:
    if sector in _SECTOR_WORDS:
        return _SECTOR_WORDS[sector]  # type: ignore[index]
    name = (sector or "industry").replace("_", " ")
    return (name.capitalize(), "company", f"{name} services")


def _stories_for(sector: str | None) -> dict[str, dict[int, tuple[str, ...]]]:
    pool = dict(_COMPANY_STORIES)
    pool.update(_SECTOR_STORIES.get(sector or "", {}))
    return pool


def headline_text(fact: NewsFact) -> str:
    """The headline for one event. A pure function of the fact, which has no
    magnitude in it."""
    if fact.direction not in (1, -1):
        raise ValueError(f"direction must be +1 or -1, got {fact.direction!r}")
    pick = _Picker(_seed_key(fact))
    display, noun, biz = _sector_words(fact.sector)
    if fact.arm == "sector":
        template = _SECTOR_ARM[fact.direction][pick.index(len(_SECTOR_ARM[fact.direction]))]
        return template.format(sector=display.lower(), Sector=display)
    if fact.arm == "market":
        return _MARKET_ARM[fact.direction][pick.index(len(_MARKET_ARM[fact.direction]))]
    if fact.arm != "company" or not fact.ticker:
        raise ValueError(f"not a headline-able fact: {fact!r}")
    stories = _stories_for(fact.sector)
    names = sorted(stories)
    story = stories[names[pick.index(len(names))]][fact.direction]
    template = story[pick.index(len(story))]
    # Subject: the bare ticker two times in three, else "Drugmaker AAS".
    subject = fact.ticker if pick.index(3) else f"{noun[0].upper()}{noun[1:]} {fact.ticker}"
    firm = _FIRMS[pick.index(len(_FIRMS))]
    return template.format(S=subject, t=fact.ticker, q=_quarter(fact.day), firm=firm, biz=biz)


# -- the entry point ---------------------------------------------------------------------


def _read(source: Any, tickers: Sequence[str] | None) -> tuple[Mapping[str, Any], list[str]]:
    if hasattr(source, "state_snapshot"):
        snapshot = source.state_snapshot()
        roster = list(tickers) if tickers is not None else list(source.tickers)
    elif isinstance(source, Mapping):
        if tickers is None:
            raise ValueError("a snapshot does not carry tickers; pass the session's "
                             "tickers in the engine's order")
        snapshot, roster = source, list(tickers)
    else:
        raise TypeError(f"expected an Engine or a state_snapshot() dict, got {type(source).__name__}")
    return snapshot, roster


def headlines_for(
    source: Any,
    *,
    day: int,
    tick: int,
    tickers: Sequence[str] | None = None,
    release_tick: int = RELEASE_TICK,
) -> list[Headline]:
    """The headlines visible at clock `(day, tick)`.

    `source` is an `Engine` or its `state_snapshot()`. `day` and `tick` are
    the session clock (`Clock.day`, `Clock.tick`: the engine's `day_count`
    frame, ticks elapsed in the session). `tickers` is the roster in the
    engine's order, required for a snapshot, defaulting to `Engine.tickers`.

    Returns the engine's current news day's headlines if that day's release
    point `(news_day, release_tick)` is at or before `(day, tick)`, else
    nothing. The engine holds one day of news, so this never returns an
    earlier day's headlines once the next day has opened; a caller that
    wants a feed keeps what it has already been given (HEADLINES.md,
    "Integration"). Order is the engine's roster order. Deterministic: the
    same state and clock give the same list, in any process.
    """
    if not 1 <= release_tick <= TICKS_PER_SESSION:
        raise ValueError(f"release_tick must be in 1..{TICKS_PER_SESSION}: a headline "
                         "may not appear before the first tick that prices it")
    snapshot, roster = _read(source, tickers)
    released_day = news_day(snapshot)
    if released_day is None or (int(day), int(tick)) < (released_day, release_tick):
        return []
    return [
        Headline(
            day=fact.day,
            tick=release_tick,
            tickers=[fact.ticker] if fact.ticker else [],
            text=headline_text(fact),
            category=fact.arm,
        )
        for fact in news_facts(snapshot, tickers=roster)
    ]
