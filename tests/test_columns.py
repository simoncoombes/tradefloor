"""The columnar read surface, and what absence means in it.

A column cannot carry `None`, so every optional field needs a spelling for
"not set yet". This library uses NaN for that everywhere — and did not, in one
place, which is what these tests exist to hold.
"""

import math
import struct

import pytest

import pretium


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

UNIVERSE = pretium.Universe.random(5, seed=2)


def column(engine, field):
    raw = engine.column(field)
    return list(struct.unpack("<%dd" % (len(raw) // 8), raw))


def test_every_declared_field_can_be_read():
    engine = pretium.Engine(seed=3, universe=UNIVERSE)
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
    engine = pretium.Engine(seed=3, universe=UNIVERSE)
    for field in UNSET_BEFORE_FIRST_TICK:
        values = column(engine, field)
        assert all(math.isnan(v) for v in values), (field, values)


def test_the_unset_fields_become_real_numbers_once_they_exist():
    # The other half. A column that were NaN forever would satisfy the test
    # above and be useless.
    engine = pretium.Engine(seed=3, universe=UNIVERSE)
    engine.run_days(2)
    for field in UNSET_BEFORE_FIRST_TICK:
        values = column(engine, field)
        assert all(math.isfinite(v) for v in values), (field, values)


def test_fields_that_are_genuinely_zero_stay_zero():
    # volume before any trading is a real zero, not an absence, and must not
    # be turned into NaN by an over-eager application of the rule above.
    engine = pretium.Engine(seed=3, universe=UNIVERSE)
    assert all(v == 0.0 for v in column(engine, "volume"))
    assert all(math.isfinite(v) for v in column(engine, "garch_variance"))


def test_an_unknown_field_lists_every_valid_one():
    # The list used to be a hand-written string beside the match arms, so it
    # could omit a name the function accepted. A list of valid names that is
    # missing one is worse than no list: it sends the reader hunting for a
    # different mistake.
    engine = pretium.Engine(seed=1, universe=UNIVERSE)
    with pytest.raises(pretium.ValidationError) as caught:
        engine.column("nonsense")
    message = str(caught.value)
    for field in FIELDS:
        assert field in message, field


def test_the_characteristics_are_constant_through_a_run():
    engine = pretium.Engine(seed=3, universe=UNIVERSE)
    before = {f: column(engine, f) for f in ("beta", "short_interest", "float_shares")}
    engine.run_days(3)
    for field, values in before.items():
        assert column(engine, field) == values, field


def test_the_characteristics_match_the_universe_they_came_from():
    engine = pretium.Engine(seed=3, universe=UNIVERSE)
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
    engine = pretium.Engine(seed=3, universe=UNIVERSE)
    assert column(engine, "float_shares") == pytest.approx(
        [i.shares_outstanding for i in UNIVERSE])


def test_reading_a_column_does_not_disturb_the_market():
    # Columns are observations. Reading one must not consume a draw or change
    # a price, or every diagnostic would alter the thing it measured.
    quiet = pretium.Engine(seed=9, universe=UNIVERSE)
    quiet.run_days(2)
    watched = pretium.Engine(seed=9, universe=UNIVERSE)
    watched.run_days(2)
    for field in FIELDS:
        watched.column(field)
    assert watched.prices() == quiet.prices()
    assert watched.draws_consumed == quiet.draws_consumed
