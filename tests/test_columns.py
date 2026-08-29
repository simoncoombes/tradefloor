"""The columnar read surface, and what absence means in it.

A column cannot carry `None`, so every optional field needs a spelling for
"not set yet". This library uses NaN for that everywhere — and did not, in one
place, which is what these tests exist to hold.
"""

import math
import struct

import pytest

import tradefloor


FIELDS = [
    "price", "previous_close", "previous_tick_price", "open", "high", "low",
    "volume", "avg_volume", "market_cap", "mispricing_s",
    "mispricing_s_prev_close", "mispricing_momentum", "last_daily_return",
    "maker_inventory", "garch_variance", "beta", "short_interest",
    "float_shares",
]

#: Fields that are genuinely unset before the engine has run. NaN, never zero.
UNSET_BEFORE_FIRST_TICK = [
    "previous_tick_price", "mispricing_s", "mispricing_s_prev_close",
    "mispricing_momentum", "last_daily_return", "maker_inventory",
]

UNIVERSE = tradefloor.Universe.random(5, seed=2)


def column(engine, field):
    raw = engine.column(field)
    return list(struct.unpack("<%dd" % (len(raw) // 8), raw))


def test_every_declared_field_can_be_read():
    engine = tradefloor.Engine(seed=3, universe=UNIVERSE)
    engine.run_days(2)
    for field in FIELDS:
        assert len(column(engine, field)) == len(UNIVERSE), field


def test_absence_is_nan_and_never_zero():
    """maker_inventory used to read 0.0 before the maker had ever quoted.

    Zero is a REAL maker inventory — a maker holding nothing — so that said
    "flat" about a book which did not exist yet. Every other optional column
    already used NaN; this one was the exception, and the inconsistency was
    invisible because after the first tick an inventory of exactly zero
    essentially never occurs.
    """
    engine = tradefloor.Engine(seed=3, universe=UNIVERSE)
    for field in UNSET_BEFORE_FIRST_TICK:
        values = column(engine, field)
        assert all(math.isnan(v) for v in values), (field, values)


def test_the_unset_fields_become_real_numbers_once_they_exist():
    # The other half. A column that were NaN forever would satisfy the test
    # above and be useless.
    engine = tradefloor.Engine(seed=3, universe=UNIVERSE)
    engine.run_days(2)
    for field in UNSET_BEFORE_FIRST_TICK:
        values = column(engine, field)
        assert all(math.isfinite(v) for v in values), (field, values)


def test_fields_that_are_genuinely_zero_stay_zero():
    # volume before any trading is a real zero, not an absence, and must not
    # be turned into NaN by an over-eager application of the rule above.
    engine = tradefloor.Engine(seed=3, universe=UNIVERSE)
    assert all(v == 0.0 for v in column(engine, "volume"))
    assert all(math.isfinite(v) for v in column(engine, "garch_variance"))


def test_an_unknown_field_lists_every_valid_one():
    # The list used to be a hand-written string beside the match arms, so it
    # could omit a name the function accepted. A list of valid names that is
    # missing one is worse than no list: it sends the reader hunting for a
    # different mistake.
    engine = tradefloor.Engine(seed=1, universe=UNIVERSE)
    with pytest.raises(tradefloor.ValidationError) as caught:
        engine.column("nonsense")
    message = str(caught.value)
    for field in FIELDS:
        assert field in message, field


def test_the_characteristics_are_constant_through_a_run():
    engine = tradefloor.Engine(seed=3, universe=UNIVERSE)
    before = {f: column(engine, f) for f in ("beta", "short_interest", "float_shares")}
    engine.run_days(3)
    for field, values in before.items():
        assert column(engine, field) == values, field


def test_the_characteristics_match_the_universe_they_came_from():
    engine = tradefloor.Engine(seed=3, universe=UNIVERSE)
    assert column(engine, "beta") == pytest.approx([i.beta for i in UNIVERSE])
    assert column(engine, "short_interest") == pytest.approx(
        [i.short_interest for i in UNIVERSE])


def test_float_equals_shares_outstanding_which_is_a_simplification():
    """No closely-held stock. Stated, because it makes one ratio degenerate.

    `Instrument` has no float parameter, so every generated company floats its
    entire share count. That matters in one place: the short-squeeze mechanism
    reads short interest as a fraction of FLOAT, and with float equal to shares
    outstanding that ratio is systematically smaller than a real one would be —
    a company with 20% of its float shorted and 60% of its stock closely held
    reads as 8% here.

    Squeezes are therefore rarer in this model than in a market with the same
    nominal short interest. Asserted rather than left to be discovered from a
    squeeze column that is mostly zero.
    """
    engine = tradefloor.Engine(seed=3, universe=UNIVERSE)
    assert column(engine, "float_shares") == pytest.approx(
        [i.shares_outstanding for i in UNIVERSE])


def test_reading_a_column_does_not_disturb_the_market():
    # Columns are observations. Reading one must not consume a draw or change
    # a price, or every diagnostic would alter the thing it measured.
    quiet = tradefloor.Engine(seed=9, universe=UNIVERSE)
    quiet.run_days(2)
    watched = tradefloor.Engine(seed=9, universe=UNIVERSE)
    watched.run_days(2)
    for field in FIELDS:
        watched.column(field)
    assert watched.prices() == quiet.prices()
    assert watched.draws_consumed == quiet.draws_consumed


# --------------------------------------------------------------------------
# short_interest: a share count, and the mistake that looks like one
# --------------------------------------------------------------------------


def test_a_fraction_shaped_short_interest_is_refused():
    """The silent failure this guard exists to stop.

    `short_interest` is a share COUNT — the squeeze rule computes
    `short_interest / float`. Someone writing 0.03 means three per cent; what
    they get is three hundredths of one share, a ratio of 3e-11, and a squeeze
    that can never fire. Nothing errors, the market runs, and one of the four
    shock factors is simply dead for that company.

    I made this exact mistake while testing the squeeze path: set 0.25,
    watched the output not move, and only then read the formula. Refusing the
    ambiguous range is the same treatment rates get.
    """
    with pytest.raises(tradefloor.ValidationError) as caught:
        tradefloor.Instrument("AAA", "technology", initial_price=50.0,
                           shares_outstanding=1e9, eps=3.0, short_interest=0.03)
    message = str(caught.value)
    assert "SHARE COUNT" in message
    # And it names the value they should have passed, rather than leaving them
    # to work out the conversion that just caught them out.
    assert "30000000" in message


def test_a_real_share_count_is_accepted():
    instrument = tradefloor.Instrument(
        "AAA", "technology", initial_price=50.0, shares_outstanding=1e9,
        eps=3.0, short_interest=3e7)
    assert instrument.short_interest == 3e7


def test_zero_short_interest_is_legal():
    # Zero is "nobody is short", which is a real and common state. Only the
    # ambiguous open interval is refused.
    instrument = tradefloor.Instrument(
        "AAA", "technology", initial_price=50.0, shares_outstanding=1e9,
        eps=3.0, short_interest=0.0)
    assert instrument.short_interest == 0.0


def test_a_fractional_share_is_legal_in_a_tiny_company():
    # The guard keys on the share count, not on the value alone. Half a share
    # of a hundred-share company is a coherent position; half a share of a
    # billion-share company is a typo.
    instrument = tradefloor.Instrument(
        "AAA", "technology", initial_price=50.0, shares_outstanding=100.0,
        eps=3.0, short_interest=0.5)
    assert instrument.short_interest == 0.5


def test_generated_universes_pass_their_own_guard():
    # The generator draws a fraction of shares outstanding, so it must never
    # produce a value the constructor would reject.
    for instrument in tradefloor.Universe.random(60, seed=5):
        assert instrument.short_interest == 0.0 or instrument.short_interest >= 1.0


# --------------------------------------------------------------------------
# The tables must actually join
# --------------------------------------------------------------------------


def test_fills_join_to_the_tape_on_day_tick_instrument():
    """The claim the fills docstring made and only half-delivered.

    `bars`, `truth` and `book` are keyed on a within-day tick. `step` is a
    GLOBAL counter, so before the `tick` column a fill read `day=1, step=6`
    under four steps a day -- which looks wrong and joins to nothing.
    Recovering the tick needed `steps_per_day` and `ticks_per_step`, neither
    of which appears in any table, so the join worked only for someone who
    still had the call that produced the data.

    This does the join for real rather than asserting the column exists.
    """
    pa = pytest.importorskip("pyarrow")

    universe = tradefloor.Universe.random(6, seed=1)
    engine = tradefloor.Engine(seed=1, universe=universe)
    engine.run_days(3, record=True, ticks_per_day=40)
    bars = pa.table(engine.bars())

    portfolio = tradefloor.Portfolio(cash=1_000_000.0)
    portfolio.stamp(1, 6, 20)
    fills = pa.table(tradefloor._core.fills_stream(
        [1], [6], [20], [0], [100.0], [10.0], [10.0], [1000.0]))

    joined = fills.join(bars, keys=["day", "tick", "instrument_id"])
    assert joined.num_rows == 1, "the fill matched no bar"
    assert "close" in joined.column_names
    # And the bar it matched is a real one, not a null-filled outer join.
    assert joined.column("close")[0].as_py() > 0


def test_the_join_key_is_the_tick_the_fill_preceded():
    """Agents act at the START of a step, so the arithmetic is exact.

    A fill at within-day step k carries k * ticks_per_step -- the index of the
    next tick to run. Checked against the harness rather than restated: every
    fill's tick must equal that, for the steps_per_day and ticks_per_step the
    run actually used.
    """
    universe = tradefloor.Universe.random(6, seed=1)
    steps_per_day, ticks_per_step = 4, 10
    execution = tradefloor.tca.analyse(
        _AlwaysBuys(), seed=1, universe=universe, days=3,
        steps_per_day=steps_per_day, ticks_per_step=ticks_per_step)
    assert execution.fills, "no fills, so this proves nothing"
    for fill in execution.fills:
        expected = (fill["step"] % steps_per_day) * ticks_per_step
        assert fill["tick"] == expected, fill
        # And it is a within-day index, unlike step.
        assert 0 <= fill["tick"] < steps_per_day * ticks_per_step


def test_stamping_without_a_tick_is_a_type_error():
    # Defaulting it would put every fill at tick zero: a table that joins
    # cleanly, to the wrong bar, with nothing to indicate it.
    portfolio = tradefloor.Portfolio(cash=1_000.0)
    with pytest.raises(TypeError):
        portfolio.stamp(1, 6)


class _AlwaysBuys:
    def act(self, obs):
        return {obs.tickers[0]: 0.001 * obs.avg_volume(obs.tickers[0])}
