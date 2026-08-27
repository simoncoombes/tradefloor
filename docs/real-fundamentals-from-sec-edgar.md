---
title: Real fundamentals from SEC EDGAR
nav_order: 12
rack: connect
short: SEC EDGAR
---

# Real fundamentals from SEC EDGAR

```python
snap = pt.edgar.fetch(as_of="2024-06-30", limit=100,
                      user_agent="Jane Roe jane@example.org")
snap.save("edgar-2024h1.json")            # the artifact, hashed and citable

universe = pt.Universe.from_edgar(snap, federal_funds_rate=0.03)
```

Seeding from filings gives you a cross-section that is real - true valuation
dispersion, actual sector weights, loss-makers in realistic proportion - while
every price path stays synthetic.

Save the snapshot and cite that, not the query. EDGAR is not append-only, so
the same request returns different numbers next year. Snapshots are hashed and
serialisable.

The fetch uses the XBRL frames API: ten market-wide requests plus one per
company kept, rather than one per filer. The ten are diluted EPS,
shareholders' equity, two share-count tags (`us-gaap` and `dei`, because
filers use one or the other), and three revenue tags at two periods each so
growth can be derived. `rank_by="public_float"` adds an eleventh,
`dei:EntityPublicFloat`. Counted through the test suite's recording transport
on a four-filer market: 10 frame calls plus 4 submissions calls by default, 11
frame calls under `public_float`. Requests are rate limited to eight a second.
`fetch` takes a `transport`, so the derivation is testable without a socket.

`user_agent` is required and must carry a contact address. The SEC's
fair-access policy asks for one, and this library will not send a fabricated
one on your behalf.

Ranking is by shareholders' equity **by default**, because EDGAR carries no
market capitalisation, so the default universe skews toward balance-sheet-heavy
names. Measured on the live SEC for CY2025, the top 150 by equity came back 27%
financial services and 17% technology, against roughly 13% and 30% for the S&P
500, with five banks in the top ten.

`rank_by="public_float"` ranks by `dei:EntityPublicFloat` instead -- the
aggregate market value of stock held by non-affiliates, filed on the 10-K cover
page. It is the one market-derived number in EDGAR, and it produces a roster
whose composition resembles a real index. Its two costs are stated rather than
hidden. It is stale, as of the last business day of the most recently completed
second fiscal quarter, so six to eighteen months old depending on the filer.
And it is float, not capitalisation, so it understates founder-controlled
companies specifically. Mis-tagged filings are rejected by implied price per
share (`public_float / shares`) having to land inside
`pt.edgar.PLAUSIBLE_IMPLIED_PRICE`, which is `(0.5, 10000.0)` -- a filing that
implies a share price of eight million dollars has a units error, and saying
that beats saying "too big".

Neither ranking is a market-cap ranking, because EDGAR has no prices. For that,
set `initial_price` yourself from a market data source.

This loads fundamentals, not behaviour. A loaded ticker gives you a stock with
that company's fundamentals under this model's assumptions - not that company,
not its volatility, not its microstructure.
