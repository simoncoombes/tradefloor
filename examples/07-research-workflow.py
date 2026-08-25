"""A complete research workflow, start to finish, in about ten seconds.

Run it:

    python examples/07-research-workflow.py

This exercises all four things the library exists to provide: a reproducible
market, ground truth about why prices moved, emergent impact through a real
book, and a counterfactual, in the order a researcher would actually reach for
them.

It is also run by the test suite, deliberately. Running the library the way a
user would, rather than only running its unit tests, is what caught the two
worst defects this project has had: a parallel sweep that hung forever in any
notebook, and an order log that did not compare equal to itself after a JSON
round trip. Both had green unit tests. Neither survived one honest end-to-end
run.
"""

import struct
import time

import pretium as pt
from pretium.baselines import Momentum, capture_ratio, reference_agents


def main() -> dict:
    started = time.time()
    report: dict = {}

    # 1. A universe. Pin it by serialising: `random(60, seed=11)` is only a
    #    citable specification while the generator is versioned with the model,
    #    and to_json() pins the exact roster regardless.
    universe = pt.Universe.random(60, seed=11)
    report["universe"] = len(universe)
    print(f"1. universe: {len(universe)} instruments")

    # 2. A sweep. Per seed is the only safe parallel boundary, because the engine's
    #    three RNG streams split by domain (market, economy, external), not by
    #    unit of work, and the market stream serves every draw in a tick in
    #    one fixed order across the roster, so there is still no decomposition
    #    WITHIN a run that preserves the draw schedule.
    mark = time.time()
    sweep = pt.run_many(seeds=list(range(20)), universe=universe, days=5,
                        workers=8, collect="summary")
    report["sweep"] = len(sweep)
    print(f"2. swept {len(sweep)} seeds x 5 days in {time.time() - mark:.1f}s")

    # 3. Rank agents on one market. Same seed for every agent, so the
    #    comparison is between strategies rather than between markets, so order
    #    flow consumes no RNG draws, which is what makes that exact.
    mark = time.time()
    scores = pt.evaluate(reference_agents(seed=3), seed=7, universe=universe,
                         days=10)
    print(f"3. evaluated {len(scores)} agents in {time.time() - mark:.1f}s")
    for card in pt.leaderboard(scores):
        print(f"     {card.name:16s} {card.return_pct:+7.2f}%  "
              f"impact {card.impact_bps:+8.2f} bps")

    # The number worth reporting. Raw P&L is not comparable across markets,
    # since a seed with more dispersion pays every strategy more, and dividing by
    # what a perfectly-informed reference earned in THAT market removes
    # exactly that.
    #
    # A ratio above 1.0 is legal and does happen, in roughly 9% of measured
    # agent-seed pairs. The Oracle is not an upper bound: it gets the same
    # gross exposure as everyone else and spends it on a naive equal-weight
    # rule, so an agent with a better portfolio under the same constraint
    # out-earns it. That is a finding about portfolio construction, not a
    # broken denominator, so nothing here clamps it.
    ratios = capture_ratio(scores)
    report["capture"] = ratios
    print(f"     capture vs the oracle: "
          f"{ {k: round(v, 3) for k, v in ratios.items()} }")

    # ...and that table ranks the SEED at least as much as the agents. Twelve
    # markets, and a single seed picks the top agent half the time. So the
    # honest verdict is taken across seeds, paired so that each agent meets an
    # identical market within every one of them.
    #
    # `rank` takes a factory rather than built agents: agents are stateful, and
    # reusing them would carry seed 0's history into seed 1 invisibly.
    mark = time.time()
    ranking = pt.rank(lambda: reference_agents(seed=3), seeds=range(8),
                      universe=universe, days=5, workers=8)
    report["ranking"] = {r.name: r.pooled_capture for r in ranking.table()}
    print(f"     ranked across 8 seeds in {time.time() - mark:.1f}s")
    for line in ranking.report().splitlines()[1:]:
        print(f"  {line}")

    # The table has a winner. The statistics may not, and reporting only the
    # first would be the same coin flip in nicer clothes. A paired sign test
    # says whether the ordering is real.
    first, second = ranking.table()[0], ranking.table()[1]
    verdict = ranking.separation(first.name, second.name)
    report["top_two_p"] = verdict["p_value"]
    print(f"     {first.name} over {second.name}: {verdict['wins_a']}-"
          f"{verdict['wins_b']} across seeds, p={verdict['p_value']:.3f}"
          f"{'' if verdict['decisive'] else '  (not separated)'}")

    # The spread of the leader across single seeds is wider than its whole
    # margin over the runner-up. That is the finding, and it is asserted so
    # that a change which quietly narrows the spread has to explain itself.
    span = first.capture_range[1] - first.capture_range[0]
    margin = first.pooled_capture - second.pooled_capture
    report["span_exceeds_margin"] = span > margin
    assert span > margin, (
        f"per-seed spread {span:.3f} no longer exceeds the {margin:.3f} "
        "margin between the top two -- if that is real, single-seed "
        "evaluation just became defensible and this warning should change"
    )
    print(f"     one seed swings the leader by {span:.3f}, against a "
          f"{margin:.3f} margin over second place")

    # 4. What did the winner's trading cost? Every fill priced against a market
    #    where it never traded, the benchmark real TCA cannot have.
    mark = time.time()
    execution = pt.tca.analyse(Momentum(), seed=7, universe=universe, days=10)
    report["shortfall_bps"] = execution.shortfall_bps()
    print(f"4. shortfall {execution.shortfall_bps():+.2f} bps over "
          f"{len(execution.fills)} fills ({time.time() - mark:.1f}s)")
    print(f"     partial fills: {len(execution.partial_fills())}  "
          f"(a cheap execution that did not happen is not a cheap execution)")

    # Nothing the trader did not touch should have moved through the MARKET:
    # order flow consumes no draws, so the untraded names see identical
    # noise. Since the 2026-08 VIX coupling one indirect channel is open on
    # purpose. The fear gauge reacts same-day to the cap-weighted market
    # return, and VIX now reprices the shared factor's variance, so a
    # trader whose flow moves the market return nudges every name's
    # volatility two closes later. Measured here: two untraded names move,
    # by -6.5 and +3.2 bps against a 13 bps median direct impact. That is
    # the market being afraid of your trading, which is a cost, not a leak
    # in the subtraction, and it must stay well under the direct impact it
    # rides beside, which is asserted.
    leaked = execution.untouched_moved()
    report["leaked"] = leaked
    traded_names = {fill["ticker"] for fill in execution.fills}
    direct = sorted(
        abs(bps) for name, bps in execution.moved().items()
        if name in traded_names
    )
    median_direct = direct[len(direct) // 2]
    for name in leaked:
        assert abs(execution.impact_bps(name)) < median_direct, (
            f"macro feedback on {name} ({execution.impact_bps(name):+.2f} bps) "
            f"rivals direct impact ({median_direct:.2f} bps median) -- "
            "that is no longer a fear ripple"
        )
    print(f"     untouched instruments moved: {len(leaked)}, all through the "
          "fear gauge, all small against direct impact")

    # And with VIX pinned the macro channel is closed, so the subtraction is
    # byte-exact, the guarantee the RNG stream split actually makes, now
    # demonstrated at the boundary where it holds.
    pinned = pt.tca.analyse(Momentum(), seed=7, universe=universe, days=10,
                            scenario=pt.Scenario().hold(vix=15.0))
    report["leaked_pinned"] = pinned.untouched_moved()
    assert not report["leaked_pinned"], (
        f"impact leaked into untraded names under a pinned VIX: "
        f"{report['leaked_pinned']}"
    )
    print("     under a pinned VIX: none, byte-exact, as they must be")

    # 4b. The book that made those costs. Impact here is not a formula applied
    #     to a size -- it is depth being consumed, so it can be watched
    #     happening. `sweep_cost` asks what an order WOULD pay before placing
    #     it, and it reads the same book the fill does.
    probe = pt.Engine(seed=7, universe=universe)
    probe.open_market()
    probe.run_session(9, 30, 3, 65)
    ticker = universe[0].ticker
    book = probe.book(ticker)
    offered = book.depth("sell")
    quoted = [(size, book.sweep_cost("buy", size)) for size in
              (1e3, 1e4, 1e5, 5e6)]
    report["book_walk"] = [q.average_price for _, q in quoted]
    print(f"4b. the book for {ticker}: bid {book.best_bid:.2f} / ask "
          f"{book.best_ask:.2f}, {offered:,.0f} shares offered")
    for size, quote in quoted:
        short = "" if quote.filled >= size else             f"  FILLED ONLY {quote.filled:,.0f}"
        print(f"     buying {size:>10,.0f}: average {quote.average_price:.4f}"
              f"  worst {quote.worst_price:.4f}{short}")

    # A bigger order pays more per share, because it reaches further up the
    # book. Asserted so that a change flattening the depth curve -- which would
    # quietly make size free -- has to explain itself.
    averages = [q.average_price for _, q in quoted]
    assert averages == sorted(averages), f"a larger order paid less: {averages}"
    assert averages[-1] > averages[0], (
        "the largest order paid the same as the smallest; depth is not binding"
    )

    # Past the displayed depth the average price STOPS moving, and reading that
    # as "size is free above here" is the trap. The order is not cheap, it is
    # unfilled: every request beyond the book returns the same fill, capped at
    # what was actually offered. A cost per share is meaningless without the
    # quantity beside it.
    beyond = book.sweep_cost("buy", 5e7)
    report["saturates_at"] = beyond.filled
    assert abs(beyond.filled - offered) < 1e-6, (
        f"a 50m-share request filled {beyond.filled:,.0f} against "
        f"{offered:,.0f} offered"
    )
    assert beyond.average_price == quoted[-1][1].average_price, (
        "past saturation the average should stop moving"
    )
    print(f"     past {offered:,.0f} shares the price stops moving because the "
          f"order stops filling")

    # And the quote is not a separate model from the fill: what the portfolio
    # actually pays is what the book said it would.
    wallet = pt.Portfolio(cash=1e12)
    wallet.stamp(0, 0, 0)
    paid = wallet.execute(probe, ticker, 5e6)
    report["quote_matches_fill"] = paid["price"] == quoted[-1][1].average_price
    assert report["quote_matches_fill"], (
        f"quoted {quoted[-1][1].average_price} and paid {paid['price']}"
    )
    print("     what the book quoted is what the portfolio paid, exactly")

    # 5. Ground truth for the same market. One row per instrument per tick,
    #    and the seven components sum to the change in mispricing, so the
    #    label can be checked rather than trusted.
    mark = time.time()
    engine = pt.Engine(seed=7, universe=universe)
    engine.run_days(10, record=True)
    truth = engine.truth()
    report["truth_rows"] = truth.num_rows
    print(f"5. ground truth: {truth.num_rows:,} rows x {len(truth.columns)} "
          f"columns in {time.time() - mark:.1f}s")

    # 6. What actually drove this market? The day-grain view of the same
    #    quantity, so it agrees with the table above by construction.
    import struct

    totals = {
        factor: sum(abs(x) for x in struct.unpack(
            "<%dd" % len(universe), engine.attribution(factor)))
        for factor in pt.Engine.FACTORS
    }
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])
    report["dominant"] = ranked[0][0]
    print("6. drivers, largest first: "
          + ", ".join(f"{k} {v:.1e}" for k, v in ranked[:3]))

    # 7. Fork the market and run two futures. Everything before the fork is
    #    identical -- not statistically similar, identical -- so the
    #    difference between the branches is the shock and nothing else. This
    #    is the counterfactual a real market cannot give you: you cannot
    #    re-run a year without its hiking cycle.
    from pretium.scenario import Scenario

    mark = time.time()
    calm, hiked = pt.branch(engine, 2, universe=universe, seed=7)
    flat = Scenario().hold(federal_funds_rate=0.025, corporate_bond_yield=0.045)
    shock = Scenario.rate_shock(start=0.025, end=0.06, over=5)
    for branch_engine, path in ((calm, flat), (hiked, shock)):
        for day in range(6):
            path.apply(branch_engine, day)
            branch_engine.open_market()
            branch_engine.run_session(9, 30, 3, 390)
            branch_engine.close_market()

    after = struct.unpack("<%dd" % len(universe), hiked.prices())
    before = struct.unpack("<%dd" % len(universe), calm.prices())
    moves = sorted(h / c - 1.0 for h, c in zip(after, before))
    median_move = moves[len(moves) // 2] * 100.0
    report["hike_median_pct"] = median_move
    print(f"7. forked and ran two futures in {time.time() - mark:.1f}s: "
          f"the hike moved the median name {median_move:+.2f}%")
    # A rate rise should price equities DOWN. If this ever flips, either the
    # scenario stopped reaching fair value or the fork is not isolating.
    assert median_move < 0, f"a hike priced the market up: {median_move:+.2f}%"

    # 8. What kind of market is this, statistically? The mismatches matter
    #    more than the matches -- they are where a conclusion drawn here stops
    #    transferring. At the documented method (40 names, 252 days, the
    #    median over six seeds -- docs/how-realistic-is-this-market.md), four
    #    of the eight sit in band, and how things move together is now the
    #    half that mostly matches; what fails is scale and memory: volatility
    #    runs high, returns trend where real ones do not, the leverage effect
    #    is too weak, and volume shocks do not persist. Read the in-band half
    #    with that page's disclosure attached: four of the eight statistics
    #    were calibration targets this era, so a match there is partly the
    #    tuning meeting its own target, and held out from the tuning point the
    #    margins are thin. This run is one seed over 60 days on a different
    #    universe, so its reads are noisier than the published medians --
    #    here correlation overshoots its band and the leverage effect loses
    #    the sign that is stable at the documented method.
    #
    #    `verdict` rather than `direction`, because the leverage effect has a
    #    NEGATIVE reference band: an absent one is numerically above that band,
    #    and printing "above" for a missing effect says the opposite of what
    #    was measured.
    mark = time.time()
    facts = pt.facts.measure(seed=7, universe=universe, days=60)
    verdicts = pt.facts.compare_to_real_markets(facts)
    report["realism"] = {k: v["verdict"] for k, v in verdicts.items()}
    print(f"8. stylised facts in {time.time() - mark:.1f}s: "
          + ", ".join(f"{k.replace('_', ' ')} {v['verdict']}"
                      for k, v in verdicts.items()))
    # Not all in range, and not none. If every statistic matched, the
    # comparison would be doing no work; if none did, the model would be
    # unusable and the report should say so loudly.
    assert any(not v["matches"] for v in verdicts.values())
    assert any(v["matches"] for v in verdicts.values())

    # 9. Every result names the market it came from. A seed does not identify
    #    a market -- the same seed over a different roster is a different one
    #    -- and tickers do not identify a roster, because they are generated
    #    positionally.
    stamps = {
        scores["momentum"].universe_fingerprint,
        execution.universe_fingerprint,
        facts["universe_fingerprint"],
    }
    report["provenance"] = stamps == {universe.fingerprint}
    assert report["provenance"], stamps
    print(f"9. provenance: every result stamped {universe.fingerprint[:12]}...")

    # 10. Archive it. The log plus the seed plus the universe reproduce the run
    #    without this script.
    import json

    archive = {
        "seed": 7,
        "universe": json.loads(universe.to_json()),
        "log": engine.order_log,
    }
    blob = json.dumps(archive)
    report["archive_bytes"] = len(blob)
    restored = json.loads(blob)
    replayed = pt.replay(
        restored["log"], seed=restored["seed"],
        universe=pt.Universe.from_json(json.dumps(restored["universe"])),
    )
    assert replayed.prices() == engine.prices(), "replay diverged"
    print(f"10. archived {len(blob):,} bytes and replayed it to identical prices")

    print(f"\ntotal {time.time() - started:.1f}s")
    return report


if __name__ == "__main__":
    main()
