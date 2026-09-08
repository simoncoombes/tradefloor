"""Universe generation and serialisation, and macro paths.

Neither needs the golden corpus: generation ranges are editorial and pinned by
nothing, and the properties worth asserting are determinism and shape.
"""

import json

import pytest

import tradefloor


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

def test_the_same_seed_gives_a_citable_universe():
    # The property that makes `random(108, seed=7)` nameable in a paper.
    a = tradefloor.Universe.random(108, seed=7)
    b = tradefloor.Universe.random(108, seed=7)
    assert a.tickers() == b.tickers()
    assert all(x.initial_price == y.initial_price for x, y in zip(a, b))
    assert all(x.eps == y.eps for x, y in zip(a, b))


def test_a_different_universe_seed_gives_a_different_universe():
    a = tradefloor.Universe.random(50, seed=1)
    b = tradefloor.Universe.random(50, seed=2)
    assert [i.initial_price for i in a] != [i.initial_price for i in b]


def test_the_universe_seed_is_independent_of_the_simulation_seed():
    """"Same universe, different market draws" must be expressible.

    It is the standard design for variance estimation, and it only works if
    the two seeds are independent. If generation shared the engine's stream,
    changing the universe seed would change the market and the design would be
    a lie.
    """
    universe = tradefloor.Universe.random(8, seed=99)
    a = tradefloor.Engine(seed=1, universe=universe)
    b = tradefloor.Engine(seed=2, universe=universe)
    for e in (a, b):
        e.open_market()
        e.run_session(9, 30, 3, 40)
    assert a.prices() != b.prices(), "different sim seeds must differ"

    c = tradefloor.Engine(seed=1, universe=tradefloor.Universe.random(8, seed=99))
    c.open_market()
    c.run_session(9, 30, 3, 40)
    assert a.prices() == c.prices(), "same universe seed + same sim seed must match"


def test_every_sector_appears_in_a_large_enough_universe():
    # Round-robin, not sampled: a sampled universe could omit a whole sector
    # by chance and the user would have no way to know their study had no
    # utilities in it.
    u = tradefloor.Universe.random(12, seed=1)
    assert len({i.sector for i in u}) == 12


def test_the_cross_section_is_plausible_not_uniform():
    u = tradefloor.Universe.random(300, seed=5)
    caps = [i.market_cap for i in u]
    # Spread tiers sit at $1B / $10B / $50B. A universe inside one tier would
    # have uniform liquidity, which is the opposite of useful.
    assert any(c < 1e9 for c in caps), "no small caps"
    assert any(c > 5e10 for c in caps), "no mega caps"
    # Loss-makers exercise the book-value path, which is otherwise dead code
    # in any generated study.
    losers = [i for i in u if i.eps < 0]
    assert 10 < len(losers) < 90, f"{len(losers)} loss-makers in 300"


def test_generation_rejects_degenerate_sizes():
    with pytest.raises(tradefloor.ValidationError):
        tradefloor.Universe.random(0)
    with pytest.raises(tradefloor.ValidationError, match="three letters"):
        tradefloor.Universe.random(26 ** 3 + 1)


def test_a_universe_is_an_ordered_sequence():
    # Order is contractual: the engine iterates in index order and draws as it
    # goes, so a reordered universe is a different market. A mapping would
    # invite exactly the dict that has no stable order.
    u = tradefloor.Universe.random(5, seed=1)
    assert isinstance(u, list)
    assert u.tickers() == ["AAA", "AAB", "AAC", "AAD", "AAE"]


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------

def test_json_round_trips_exactly():
    u = tradefloor.Universe.random(40, seed=11)
    back = tradefloor.Universe.from_json(u.to_json())
    assert back.tickers() == u.tickers()
    for a, b in zip(u, back):
        assert a.sector == b.sector
        assert a.initial_price == b.initial_price
        assert a.shares_outstanding == b.shares_outstanding
        assert a.eps == b.eps
        assert a.beta == b.beta


def test_a_serialised_universe_produces_the_same_market():
    # The point of serialising: a citable specification pins the exact
    # instruments regardless of what a later generator would produce.
    u = tradefloor.Universe.random(6, seed=4)
    a = tradefloor.Engine(seed=3, universe=u)
    b = tradefloor.Engine(seed=3, universe=tradefloor.Universe.from_json(u.to_json()))
    for e in (a, b):
        e.open_market()
        e.run_session(9, 30, 3, 60)
    assert a.prices() == b.prices()


def test_a_newer_schema_is_refused_rather_than_partially_read():
    # A field this version does not know would silently take a default and
    # produce a universe nobody specified -- the same class of failure as a
    # truncated golden file passing quietly.
    doc = json.loads(tradefloor.Universe.random(2, seed=1).to_json())
    doc["schema"] = 99
    with pytest.raises(tradefloor.ValidationError, match="newer"):
        tradefloor.Universe.from_json(json.dumps(doc))


def test_unknown_fields_are_refused():
    doc = json.loads(tradefloor.Universe.random(2, seed=1).to_json())
    doc["instruments"][0]["mystery"] = 1
    with pytest.raises(tradefloor.ValidationError, match="mystery"):
        tradefloor.Universe.from_json(json.dumps(doc))


def test_a_missing_required_field_names_itself():
    doc = json.loads(tradefloor.Universe.random(2, seed=1).to_json())
    del doc["instruments"][1]["ticker"]
    with pytest.raises(tradefloor.ValidationError, match="ticker"):
        tradefloor.Universe.from_json(json.dumps(doc))


def test_nonsense_input_is_refused():
    with pytest.raises(tradefloor.ValidationError, match="not a tradefloor universe"):
        tradefloor.Universe.from_json('{"hello": 1}')


# --------------------------------------------------------------------------
# Macro paths
# --------------------------------------------------------------------------

def test_a_scenario_is_a_path_not_a_flag():
    """A rate shock is fed_funds stepping over N days, supplied by the user.

    Not a `rate_shock=True` argument. Every macro narrative worth expressing --
    QE, a hiking cycle, stagflation -- is a path over these fields, so the API
    gives you the fields and resists growing named scenarios that are paths in
    disguise.
    """
    e = tradefloor.Engine(seed=1, universe=tradefloor.Universe.random(6, seed=1),
                       macro_state=tradefloor.Macro(federal_funds_rate=0.025))
    path = [0.025, 0.030, 0.038, 0.045, 0.050]
    for rate in path:
        e.pin_macro(federal_funds_rate=rate)
        e.open_market()
        e.run_session(9, 30, 3, 78)
        e.close_market()
    assert e.macro_state.federal_funds_rate == pytest.approx(0.050)


def test_macro_reads_back_in_the_units_it_was_written_in():
    # Fractional in, fractional out. A value read here can be written straight
    # back without a conversion, the reason for one denomination
    # at the boundary.
    e = tradefloor.Engine(seed=1, universe=tradefloor.Universe.random(3, seed=1),
                       macro_state=tradefloor.Macro(federal_funds_rate=0.0425,
                                                 inflation_rate=0.031))
    assert e.macro_state.federal_funds_rate == pytest.approx(0.0425)
    assert e.macro_state.inflation_rate == pytest.approx(0.031)


def test_a_supplied_opening_survives_construction():
    """The macro you passed is the macro you get.

    `macro_burn_in_days` exists to relax the CONSTRUCTOR'S default macro,
    which otherwise opens every run in expansion at phase age zero. pt-v18
    ships it at 755 and the burn-in ran on a supplied opening too, so an
    engine asked for a VIX of 45.0 and a policy rate of 5 per cent opened at
    21.55 and 0.00 -- three releases' worth of scenario studies would have
    started somewhere other than where they asked to.

    Asserted across every shipped preset, so a preset that switches the
    burn-in on later cannot reintroduce it.
    """
    crisis = tradefloor.Macro(vix=45.0, federal_funds_rate=0.05,
                              inflation_rate=0.06, cycle="contraction")
    for name in tradefloor.preset_names():
        e = tradefloor.Engine(seed=1, universe=tradefloor.Universe.random(3, seed=1),
                              macro_state=crisis, model=name)
        got = e.macro_state
        assert got.vix == pytest.approx(45.0), name
        assert got.federal_funds_rate == pytest.approx(0.05), name
        assert got.inflation_rate == pytest.approx(0.06), name
        assert got.cycle == "contraction", name


def test_a_batch_opens_where_a_single_engine_does():
    """`EngineBatch([s])` and `Engine(s)` are the same market.

    The rule that decides whether an opening is settled lives at the Python
    boundary, and it is written twice -- once here, once in the batch. A
    batch that settled an opening the single engine kept would be a silently
    different market, which is the drift `economy_from`'s own comment names.
    """
    universe = tradefloor.Universe.random(3, seed=1)
    crisis = tradefloor.Macro(vix=45.0, federal_funds_rate=0.05)
    for macro in (None, crisis):
        kwargs = {} if macro is None else {"macro_state": macro}
        single = tradefloor.Engine(seed=7, universe=universe, **kwargs)
        batch = tradefloor.EngineBatch(seeds=[7], universe=universe, **kwargs)
        single.open_market()
        batch.open_market()
        single.run_session(9, 30, 3, 30)
        batch.run_session(9, 30, 3, 30)
        # Both return raw f64 bytes; the batch's are (seeds, names) and the
        # single engine's are one row, so one seed makes them the same bytes.
        assert batch.prices() == single.prices(), (
            "the batch and the single engine opened on different markets"
            + ("" if macro is None else " under a supplied macro state"))


def test_pinning_is_narrow():
    # "Narrow write surface, generous read surface": pinning the policy rate
    # must not also freeze inflation.
    e = tradefloor.Engine(seed=1, universe=tradefloor.Universe.random(3, seed=1))
    before = e.macro_state.inflation_rate
    e.pin_macro(federal_funds_rate=0.06)
    assert e.macro_state.inflation_rate == before
    assert e.macro_state.federal_funds_rate == pytest.approx(0.06)


def test_a_rejected_pin_writes_nothing_at_all():
    # Validate everything before writing anything. A pin that applied two
    # fields then rejected the third would leave the scenario half-applied and
    # the run would continue on a macro state nobody asked for.
    e = tradefloor.Engine(seed=1, universe=tradefloor.Universe.random(3, seed=1))
    before_vix = e.macro_state.vix
    with pytest.raises(tradefloor.ValidationError, match="percent"):
        e.pin_macro(vix=22.0, federal_funds_rate=4.5)
    assert e.macro_state.vix == before_vix


def test_pinning_rejects_an_unknown_cycle():
    e = tradefloor.Engine(seed=1, universe=tradefloor.Universe.random(3, seed=1))
    with pytest.raises(tradefloor.ValidationError, match="expansion"):
        e.pin_macro(cycle="boom")


def test_a_driven_path_is_reproducible():
    def run():
        e = tradefloor.Engine(seed=8, universe=tradefloor.Universe.random(5, seed=2))
        for rate in (0.02, 0.03, 0.04):
            e.pin_macro(federal_funds_rate=rate)
            e.open_market()
            e.run_session(9, 30, 3, 40)
            e.close_market()
        return e.prices(), e.draws_consumed

    assert run() == run()
