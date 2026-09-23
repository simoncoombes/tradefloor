# Headlines: the engine's news as text, with the leak guard

`tradefloor.headlines` turns the engine's news events into `Headline` text for
an agent (contract 0.1, section 8). A headline says which company has news
and whether it is good or bad. It never says how big the news is, because in
this engine the size of a news event is the answer key: it is exactly the
move the engine will add to the price.

Module: `python/tradefloor/headlines.py` (standard library only).
Tests: `tests/test_headlines.py`, `tests/test_headlines_leak.py` (17 tests,
about 2 seconds).

## What news the engine has

One kind. At every `open_market` the engine draws, for each company, a
uniform and a normal `z` on its NEWS stream. If the uniform is below
`endogenous_news_intensity`, the company has news that day:

    {company_id, sector, price_impact = endogenous_news_sigma * z}

(`rust/src/engine.rs`, `open_market`). On pt-v19 the intensity is 0.05 per
name per day and sigma is 0.0175, so a 40-name session sees about two events
a day, each worth 1.4% on average in either direction. Presets before pt-v11
have news switched off and get no headlines.

How the event reaches the price: every tick of that day adds
`price_impact / 390` to the announcer's price state, and 5% of that to each
sector peer (more in a crisis: `news_peer_vix_coupling`). So the move lands
in a straight line across the session. None of it is in the price at the
open, all of it is in by the close, and it does not reverse on later days.
Measured on pt-v19, 1,966 events, market-adjusted:

| clock     | share of `price_impact` in the price |
|-----------|--------------------------------------|
| tick 30   | 5%                                   |
| tick 60   | 13%                                  |
| tick 210  | 51%                                  |
| close     | 92%                                  |
| next days | no reversal (about +6bp the next day) |

The engine's `company_news` attribution is exactly zero at the open and
exactly `price_impact / 390` after the first tick.

What Python can read today: `Engine.state_snapshot()["session_news"]`, a list
of `{"ticker", "sector", "price_impact"}`. The key called `"ticker"` holds the
engine's company id (`"AAS-18"`), not the ticker. The list holds one day: it
is regenerated at `open_market` and kept through `close_market`.
`day_count` counts completed closes, so the list belongs to day `day_count`
while the market is open and to `day_count - 1` after the close.

What the engine does not model: earnings dates, guidance, analysts,
takeovers, macro releases. An event has a company, a sector, a sign and a
size, and nothing else. The event type can also carry sector-wide and
market-wide news (no company), but the engine never generates those itself.

## API

```python
from tradefloor.headlines import headlines_for

headlines_for(engine_or_snapshot, *, day, tick, tickers=None, release_tick=1)
    -> list[Headline]
```

- `engine_or_snapshot`: an `Engine`, or its `state_snapshot()` dict.
- `day`, `tick`: the session clock (`Clock.day`, `Clock.tick`), in the
  engine's `day_count` frame, which is the frame the core's clock uses when
  it opens day 0 at session start.
- `tickers`: the roster in the engine's order (`Engine.tickers`, which is
  `SessionInfo.tickers`). Required for a snapshot, since the snapshot names
  companies by id; defaults to `Engine.tickers` for an engine. A roster of
  the wrong length, or an id not in it, raises `ValueError`.
- `release_tick`: when on its day a headline appears. Default and minimum 1,
  the first tick that prices the event. A later value is allowed; 0 or less
  raises.

Returns the current news day's headlines if `(news_day, release_tick)` is at
or before `(day, tick)`, else `[]`. Each is a `Headline(day=news_day,
tick=release_tick, tickers=[the announcer], text=..., category=...)`, in
roster order. `category` is the engine's news arm: `"company"`, or
`"sector"` / `"market"` for the two kinds the engine can carry but does not
generate (those name no ticker). Same state and clock give the same list in
any process: text is seeded by SHA-256 of the event's identity, not by
`hash()` or a global RNG.

Also public: `news_facts(snapshot, tickers=)` (the events with the size
removed), `headline_text(fact)`, `news_day(snapshot)`, `RELEASE_TICK`,
`TEXT_VERSION`.

## Examples

pt-v19, seed 7, 40 names, the first days:

    day 0   AAQ delays retail distribution rollout
    day 1   Utility AAJ announces share repurchase program
    day 1   Ashgrove Capital turns cautious on AAQ
    day 2   Food maker ABD secures new private-label supply agreement
    day 3   ABL Q4 profit comes in above consensus
    day 5   Freight carrier AAX cuts full-year outlook
    day 6   AAQ hit with lawsuit over product claims
    day 10  Drugmaker AAO guides next quarter above consensus

The same event reads the same storyline whichever way it goes; only the
direction words change ("Whitmore Partners upgrades AAS to outperform" /
"... downgrades AAS to underperform"). 4,000 events give about 2,600
distinct headlines, and 537 distinct wordings once the ticker and quarter
are masked.

The storyline (earnings, guidance, analyst rating, contract, regulatory,
capital return, and trial results for healthcare names) is decoration. The
engine does not model it, so it is drawn from the hash of the event's
identity and tells an agent nothing. That is why it appears only in the text
and `category` stays with what the engine actually has. Takeover stories are
left out on purpose: a real takeover headline means a 20-30% move, and this
engine's news is a 1-2% move, so the word would mislead.

## The leak guard

**The rule.** A headline may say what happened and which way. It may not
carry information about `|price_impact|` beyond what the category and sign
already carry.

**Why the design meets it.** `price_impact` is read in one function,
`news_facts`, and cut down to its sign on the spot. Everything after that
works from a `NewsFact(day, arm, direction, ticker, sector)`, which has no
field a size could ride in. The storyline, template, subject wording and
analyst firm are picked from SHA-256 of `(version, arm, day, ticker,
sector)`. The engine draws `z` independently of which company and day have
news, so none of those inputs says anything about the size. What is left
that could leak:

- words that grade a move ("soars", "narrowly", "record"): none in any
  template, checked by a test against an intensity word list;
- numbers: none, except the quarter label, which comes from the day index;
- claims about the share price: none (at release the price has barely
  moved), checked by a test;
- how many headlines there are, their order, their timing: set by whether
  news occurred, the roster order and a fixed release tick, none of which
  depend on the size.

**The tests** (`tests/test_headlines_leak.py`):

1. Invariance. On real engine snapshots, multiply every event's impact by
   its own random factor between 1e-6 and 1e6 (sizes and their ranks change,
   signs do not). The headlines stay byte-identical, order included. Flip
   the signs and the text changes.
2. Prediction. Take 3,994 events that pt-v19 itself draws (40 names, 2,000
   days). Fit the best predictors of `|price_impact|` we could build from the
   headline on even rows, score them out of sample on odd rows, and compare
   with the sign-and-category baseline. The test fails if any text predictor
   gains more than 0.005 of R-squared.
3. Power. Run the same test on two deliberately leaky generators. It must
   catch both.

Results (seed 7, out-of-sample R-squared for `|price_impact|`):

| headlines                                 | sign + category | template skeleton | ridge on words | text length | best gain |
|-------------------------------------------|-----------------|-------------------|----------------|-------------|-----------|
| shipped                                   | 0.0004          | -0.021            | 0.000          | 0.0005      | **+0.0001** |
| leaky: "slightly"/"sharply" by size tercile | 0.0004        | 0.320             | 0.754          | 0.002       | +0.75     |
| leaky: a suffix added 65/35 by size       | 0.0004          | 0.000             | 0.023          | 0.011       | +0.022    |

Across six engine seeds, the shipped generator's best gain is never above
+0.0001. The 65/35 tilt is caught on all six (0.022 to 0.057). A 60/40 tilt
scores 0.005 to 0.025, which is about the smallest leak 4,000 events can
see. The sign alone explains nothing about the size (R-squared 0.0004),
because `|z|` and the sign of `z` are independent.

The timing test (`tests/test_headlines.py`) walks 15 pt-v19 days in 30-tick
steps. At every clock it checks that nothing is visible at tick 0, that every
visible headline's ticker already has non-zero `company_news` attribution
(the price has started to move), that a clock earlier than the release sees
nothing, and that the next day's news is never visible before its open.

## Direction is visible, and it is worth a lot here

Direction stays in. The contract allows it, real headlines carry it, and a
headline without it ("AAS: company news") would be neither realistic nor
useful.

What that gives an agent, measured on pt-v19 (1,966 events, market-adjusted,
before costs): read the headline at the first observation it appears in
(tick 30 with 30-tick steps), buy good news and sell bad, hold to the close.

| trade at | mean captured per event | hit rate |
|----------|-------------------------|----------|
| tick 30  | +120bp (SE 3)           | 82%      |
| tick 120 | +89bp                   | 79%      |
| tick 210 | +58bp                   | 74%      |
| tick 360 | +10bp                   | 57%      |

Reading news fast is a real edge, but in a real market it pays for seconds or
minutes, and post-news drift is a few percent over months. Here the whole
move takes the whole session, so the headline is worth nearly the full
answer key's direction for most of a day. This is a fact about the engine's
news model, not about the headlines: the headline appears at the first tick
that prices the event, which is the earliest the contract allows. An agent that learns to trade headlines in
tradefloor is learning something that will not carry over to money at this
size. The session report should say so (see Integration), and engine change
request 3 below is the real fix.

`release_tick` can be set later to shrink the edge (release at tick 195 and
the move left is about half). That trades one unrealism for another: the
price would drift for half a day on news nobody had published yet. The
default stays at 1.

## Why there is no earnings-surprise number

Real earnings headlines carry numbers ("EPS $1.12 vs $1.05 expected"). The
engine has no surprise quantity of its own. The only number it has is
`price_impact`, and any figure computed from it would hand the agent the
answer key: a surprise of `k * z` recovers `z` exactly. A number drawn
independently of the impact would be noise that looks like information, and
teach an agent that surprises do not matter. So headlines carry no numbers.
If the engine ever models earnings, with a surprise that is a separate draw
only partly correlated with the price move (as in real markets), a
surprise number from that draw would be fair to print.

## Integration with the session server

For `python/tradefloor/serve/core.py` (the core agent's file):

- The engine keeps only the current day's news. The core keeps the feed.
  After every step of `advance` (and after `close_market`), call
  `headlines_for(engine, day=clock.day, tick=clock.tick, tickers=info.tickers)`
  and append any headline not already in the session's news log. Persist the
  log with the session (`Headline.to_dict` / `from_dict`); past days cannot
  be rebuilt from a snapshot.
- `Observation.news`: the log's headlines released since the agent's
  previous observation, or, if the core prefers stateless, today's headlines
  (`headlines_for` at the observe clock). The first loses nothing when one
  `advance` spans several days; the second does.
- At tick 0 after the open, `headlines_for` returns `[]` for the new day by
  design. Nothing is shown before the price starts to move.
- Never hand an agent `state_snapshot()`, `attribution("company_news")` or
  `truth()`: each contains the answer key.
- Caveat for `SessionReport`, computed from the preset while
  `endogenous_news_intensity > 0`: news is priced in a straight line over the
  session, so a headline's direction was worth about +120bp per event on
  pt-v19 to an agent that traded it at once; real markets price news in
  minutes.

## Engine change requests

None is needed for the module to work. In order of value:

1. **`Engine.session_news() -> list[dict]`** (read-only accessor). Returns
   the current day's events as `{"ticker": <trading ticker>, "sector": str |
   None, "price_impact": float | None, "day": int}`, from
   `self.inner.session_news()` with ids mapped by `ticker_for_id`. Why: the
   only read today is the full `state_snapshot()` (0.4 ms at 40 names), whose
   `"ticker"` key holds the company id, so the caller must map ids through
   roster order. It cannot change a trajectory: it reads a vector by
   reference, draws nothing, writes nothing and is not logged.
2. **`Engine.session_tick -> int`** (read-only property): ticks run in the
   current day. `day_marks()[-1]["ticks"]` has it but builds every day's mark.
   With it, `headlines_for` could check the release point against the engine
   instead of trusting the caller's clock. Same argument: a read of a counter
   the engine already keeps.
3. **Price news fast** (a model change, not an accessor; it changes every
   trajectory from pt-v11 on and would need a new preset and a new
   known-answer digest, so it is the owner's call). Land most of
   `price_impact` in the first minutes after the event, for example a decay
   with a half-life of a few ticks, or in the opening print the way the
   overnight jump lands. A headline released at tick 1 would then arrive with
   the move mostly priced, as on a real wire, and the edge in the table above
   would shrink to something like the real one.

## Not built

- Macro headlines. The engine's economy has central-bank meetings and a
  federal funds rate, so "Fed raises rates a quarter point" could be written
  from public state with no answer key in it. The observation already
  carries the macro numbers, and the other macro series move daily rather
  than on release dates, so this waits for a request.
- Headlines for overnight jumps. A jump is not a news event in the engine;
  its size is pending state (`pending_jump`) before the open, and a headline
  would need its own leak argument.

Dependencies: none for the module; the tests use numpy, already a test
dependency.
