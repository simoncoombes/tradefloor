---
title: Real fundamentals from SEC EDGAR
nav_order: 12
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

The fetch uses the XBRL frames API: five market-wide requests plus one per
company kept, rather than one per filer. It takes a `transport`, so the
derivation is testable without a socket.

`user_agent` is required and must carry a contact address. The SEC's
fair-access policy asks for one, and this library will not send a fabricated
one on your behalf.

Ranking is by shareholders' equity, because EDGAR carries no market
capitalisation, so the universe skews toward balance-sheet-heavy names.

This loads fundamentals, not behaviour. A loaded ticker gives you a stock with
that company's fundamentals under this model's assumptions - not that company,
not its volatility, not its microstructure.
