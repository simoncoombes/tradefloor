"""`tradefloor.headlines`: the API, the timing rule, determinism, the templates.

The leak guard's quantitative test is `test_headlines_leak.py`. This file pins
the rest of the contract (docs/serve/CONTRACT.md section 8): a headline names
the right ticker, says the right direction, is the same text in every
process, and is never visible before the first tick that prices its event.
"""

from __future__ import annotations

import re

import numpy as np
import pytest
import tradefloor as tf
from tradefloor import headlines as hl
from tradefloor.headlines import NewsFact, headline_text, headlines_for, news_facts
from tradefloor.serve.types import Headline

TICKS = 390
STEP = 30


def _engine(n: int = 20, seed: int = 1) -> tf.Engine:
    return tf.Engine(seed=seed, universe=tf.Universe.random(n, seed=111), model="pt-v19")


def _news_attribution(e: tf.Engine) -> np.ndarray:
    return np.frombuffer(e.attribution("company_news"), dtype="<f8")


# -- timing ------------------------------------------------------------------------


def test_no_headline_is_visible_before_the_first_tick_that_prices_it() -> None:
    """Walk 15 pt-v19 days in 30-tick steps, the way the session server does,
    and at every clock check what is visible against what the engine has
    priced. The `company_news` attribution is the engine's own record of the
    news reaching a price: zero at the open, non-zero from the first tick."""
    e = _engine()
    idx = {t: i for i, t in enumerate(e.tickers)}
    seen_events = 0
    for d in range(15):
        if d > 0:
            # Closed, between days: only the finished day's news, never the
            # next day's (the engine has not drawn it yet).
            for clock in ((d - 1, TICKS), (d, 0)):
                assert all(h.day == d - 1 for h in headlines_for(e, day=clock[0], tick=clock[1]))
        e.open_market()
        snap = e.state_snapshot()
        live = [ev for ev in snap["session_news"] if ev["price_impact"]]
        seen_events += len(live)
        # The open: the day's news exists inside the engine, no price has it.
        assert not _news_attribution(e).any()
        assert headlines_for(e, day=d, tick=0) == []
        for k in range(1, TICKS // STEP + 1):
            h, m, dw = tf.harness.session_clock((9, 30, 1), k - 1, STEP)
            e.run_session(h, m, dw, STEP)
            tick = k * STEP
            got = headlines_for(e, day=d, tick=tick)
            assert len(got) == len(live)  # complete once released
            attr = _news_attribution(e)
            for headline in got:
                assert headline.day == d and headline.tick == hl.RELEASE_TICK <= tick
                assert attr[idx[headline.tickers[0]]] != 0.0  # the price has it
            # A clock before the release point sees nothing, even now.
            assert headlines_for(e, day=d, tick=0) == []
            assert headlines_for(e, day=d - 1, tick=TICKS) == []
        e.close_market()
        closed = headlines_for(e, day=d, tick=TICKS)
        assert [x.day for x in closed] == [d] * len(live)
    assert seen_events >= 8, "the walk must actually meet news to test anything"


def test_a_later_release_tick_is_allowed_and_an_earlier_one_is_refused() -> None:
    e = _engine()
    for _ in range(6):  # seed 1 has news on day 5
        e.open_market()
        day = e.state_snapshot()["day_count"]
        if e.state_snapshot()["session_news"]:
            break
        e.close_market()
    e.run_session(9, 30, 1, 200)
    assert headlines_for(e, day=day, tick=180, release_tick=195) == []
    assert headlines_for(e, day=day, tick=195, release_tick=195)
    for bad in (0, -1, TICKS + 1):
        with pytest.raises(ValueError):
            headlines_for(e, day=day, tick=200, release_tick=bad)


def test_the_news_day_follows_the_engine_across_open_and_close() -> None:
    assert hl.news_day({"day_count": 0, "market_open": False}) is None
    assert hl.news_day({"day_count": 0, "market_open": True}) == 0
    assert hl.news_day({"day_count": 6, "market_open": False}) == 5
    assert hl.news_day({"day_count": 6, "market_open": True}) == 6


# -- what a headline names ----------------------------------------------------------------


def _snapshot(events, *, day=3, open_=True, ids=("AAA-0", "AAB-1", "AAC-2")) -> dict:
    return {"day_count": day, "market_open": open_, "tickers": list(ids),
            "session_news": events}


ROSTER = ["AAA", "AAB", "AAC"]


def test_it_names_the_ticker_not_the_engine_id_and_the_right_direction() -> None:
    snap = _snapshot([
        {"ticker": "AAB-1", "sector": "healthcare", "price_impact": 0.02},
        {"ticker": "AAC-2", "sector": "energy", "price_impact": -0.03},
    ])
    got = headlines_for(snap, day=3, tick=30, tickers=ROSTER)
    assert [h.tickers for h in got] == [["AAB"], ["AAC"]]
    assert [h.category for h in got] == ["company", "company"]
    facts = news_facts(snap, tickers=ROSTER)
    assert [f.direction for f in facts] == [1, -1]
    assert got[0].text == headline_text(facts[0])
    assert all(isinstance(h, Headline) and "AAB-1" not in h.text for h in got)


def test_events_the_engine_ignores_are_not_news() -> None:
    """The engine reads impacts truthy-or: None, 0.0 and NaN move nothing."""
    snap = _snapshot([
        {"ticker": "AAA-0", "sector": "energy", "price_impact": None},
        {"ticker": "AAB-1", "sector": "energy", "price_impact": 0.0},
        {"ticker": "AAC-2", "sector": "energy", "price_impact": float("nan")},
    ])
    assert headlines_for(snap, day=3, tick=30, tickers=ROSTER) == []


def test_sector_and_market_events_are_headlined_under_their_own_arm() -> None:
    """The engine never generates these endogenously today, but the event type
    carries them (a caller-supplied or restored event), so they have text."""
    snap = _snapshot([
        {"ticker": None, "sector": "utilities", "price_impact": -0.01},
        {"ticker": None, "sector": None, "price_impact": 0.01},
    ])
    got = headlines_for(snap, day=3, tick=30, tickers=ROSTER)
    assert [(h.category, h.tickers) for h in got] == [("sector", []), ("market", [])]
    assert "utilities" in got[0].text.lower()


def test_a_snapshot_needs_the_roster_and_a_wrong_roster_is_refused() -> None:
    snap = _snapshot([{"ticker": "AAB-1", "sector": "energy", "price_impact": 0.02}])
    with pytest.raises(ValueError):
        headlines_for(snap, day=3, tick=30)
    with pytest.raises(ValueError):
        headlines_for(snap, day=3, tick=30, tickers=ROSTER[:2])
    with pytest.raises(TypeError):
        headlines_for(42, day=3, tick=30)


# -- determinism ------------------------------------------------------------------------


GOLDEN = [
    (NewsFact(5, "company", 1, "AAS", "industrials"), "Whitmore Partners upgrades AAS to outperform"),
    (NewsFact(5, "company", -1, "AAS", "industrials"), "Whitmore Partners downgrades AAS to underperform"),
    (NewsFact(120, "company", 1, "ABC", "healthcare"), "Drugmaker ABC tops Q1 profit estimates"),
    (NewsFact(120, "company", -1, "ABC", "healthcare"), "Drugmaker ABC misses Q1 profit estimates"),
    (NewsFact(33, "company", 1, "AAQ", "technology"), "AAQ wins regulatory approval for new product"),
    (NewsFact(33, "sector", -1, None, "energy"), "Energy outlook worsens on new industry survey"),
    (NewsFact(33, "market", 1, None, None), "Economic data beat forecasts across the board"),
]


def test_the_text_is_pinned_across_processes() -> None:
    """SHA-256 seeding, not `hash()` or a global RNG, so these strings are the
    same in every process and Python version. Changing a template or the
    choice logic must bump TEXT_VERSION and this table together. The pairs
    also show the design: the same event reads the same storyline either
    way, and only the direction words change."""
    assert hl.TEXT_VERSION == "tradefloor.headlines/1"
    for fact, text in GOLDEN:
        assert headline_text(fact) == text


def test_an_engine_its_snapshot_and_a_restored_copy_give_the_same_headlines() -> None:
    e = _engine()
    for d in range(6):
        e.open_market()
        e.run_session(9, 30, 1, STEP)
        from_engine = headlines_for(e, day=d, tick=STEP)
        snap = e.state_snapshot()
        assert headlines_for(snap, day=d, tick=STEP, tickers=e.tickers) == from_engine
        copy = _engine()
        copy.restore_state(snap)
        assert headlines_for(copy, day=d, tick=STEP) == from_engine
        e.run_session(10, 0, 1, TICKS - STEP)
        e.close_market()


# -- the templates -------------------------------------------------------------------------

# Words that grade a move rather than name it. A real headline's "soars" or
# "narrowly" carries size; here the size is hidden, so the words would either
# leak it (if chosen by size) or lie about it (if not). Neither is allowed.
INTENSITY = re.compile(
    r"\b(soar|surg|plung|plummet|tumbl|crash|skyrocket|rocket|slump|sink|jump|spik|"
    r"rall|record|massive|huge|big|sharp|steep|slight|modest|small|marginal|narrow|"
    r"wide|blowout|crush|smash|stun|shock|dramatic|strong|significant|mild|minor|"
    r"major|substantial|hefty|whopping|tiny|deep|severe|bumper|stellar|dismal|"
    r"disastrous|sweeping|vast|much|far|well|biggest|largest|worst|best|key)\w*",
    re.IGNORECASE)
# Claims about the price, which at release has not moved.
PRICE_CLAIM = re.compile(r"\b(shares|stock|stocks|rises|falls|gains|drops|slides|climbs)\b",
                         re.IGNORECASE)
UP_CUES = {"tops", "beat", "beats", "ahead", "above", "raises", "lifts", "boosts",
           "upgrades", "upgraded", "positive", "wins", "secures", "expands",
           "approval", "clear", "settles", "favourable", "repurchase", "buyback",
           "meets", "firmer", "improves", "upbeat", "lift"}
DOWN_CUES = {"misses", "miss", "short", "below", "cuts", "lowers", "warns",
             "downgrades", "downgraded", "cautious", "loses", "delays", "disruption",
             "probe", "lawsuit", "reject", "departs", "pauses", "softer", "worsens",
             "disappoint", "clouding"}


def _all_templates():
    for story in hl._COMPANY_STORIES.values():
        yield from ((1, t) for t in story[1])
        yield from ((-1, t) for t in story[-1])
    for stories in hl._SECTOR_STORIES.values():
        for story in stories.values():
            yield from ((1, t) for t in story[1])
            yield from ((-1, t) for t in story[-1])
    for table in (hl._SECTOR_ARM, hl._MARKET_ARM):
        yield from ((1, t) for t in table[1])
        yield from ((-1, t) for t in table[-1])


def test_templates_name_the_direction_and_never_grade_it() -> None:
    templates = list(_all_templates())
    assert len(templates) >= 40
    for sign, template in templates:
        assert not INTENSITY.search(template), template
        assert not PRICE_CLAIM.search(template), template
        assert not re.search(r"\d|%", template), template
        words = set(re.findall(r"[a-z]+", template.lower()))
        want, avoid = (UP_CUES, DOWN_CUES) if sign == 1 else (DOWN_CUES, UP_CUES)
        assert words & want, f"no {'up' if sign == 1 else 'down'} cue: {template}"
        assert not words & avoid, f"mixed cues: {template}"


def test_every_storyline_has_as_many_bad_templates_as_good() -> None:
    tables = [*hl._COMPANY_STORIES.values(), hl._SECTOR_ARM, hl._MARKET_ARM,
              *(s for stories in hl._SECTOR_STORIES.values() for s in stories.values())]
    for table in tables:
        assert len(table[1]) == len(table[-1])


def test_rendered_text_carries_no_number_but_the_quarter() -> None:
    texts = [headline_text(NewsFact(d, "company", s, t, sec))
             for d in range(0, 400, 7) for s in (1, -1)
             for t, sec in (("AAA", "technology"), ("ABQ", "healthcare"), ("ACX", "utilities"))]
    assert len(set(texts)) > 40  # varied
    for text in texts:
        assert not re.search(r"\d", re.sub(r"\bQ[1-4]\b", "", text)), text
        assert text[0].isupper()
