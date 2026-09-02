"""The `prints` table, and the depth counterfactual behind two of its columns.

`truth` says what moved fair value. `prints` says what happened between fair
value and the tape: the shock that arrived, the depth that absorbed it, and
-- when the counterfactual is asked for -- what the same tick would have
printed against every resting level.

The claims here are about the ARITHMETIC and the schedule, not about a
measured level. A test that pinned "liquidity moved 4.7% of the average
print" would fail on the next preset for a reason that is not a defect, so
the numbers below are all identities the engine has to satisfy on any market
it produces.

The one number this file does state is the one the example reports, and it
is stated where it was measured rather than asserted here: see
`examples/experiments/liquidity-crisis/README.md`.
"""

import math

import pytest

import tradefloor

pa = pytest.importorskip("pyarrow", reason="pyarrow is a test-only dependency")


# The roster and the seeds the design note fixed for this surface, so a
# reader can reproduce a row from the docstring.
ROSTER_SEED = 111
NAMES = 12
RUN_SEED = 42
DAYS = 3
TICKS = 390


def run(*, counterfactual, days=DAYS, ticks=TICKS, seed=RUN_SEED):
    """A recorded run of `days` whole sessions on the fixed roster."""
    universe = tradefloor.Universe.random(NAMES, seed=ROSTER_SEED)
    engine = tradefloor.Engine(seed=seed, universe=universe)
    if counterfactual:
        engine.settle_depth_counterfactual(True)
    for day in range(days):
        engine.open_market()
        engine.run_session(9, 30, 3, ticks)
        engine.close_market()
        engine.record(day)
    return engine


# ── The table ─────────────────────────────────────────────────────────────


def test_the_table_has_a_row_per_instrument_per_tick():
    table = pa.table(run(counterfactual=False, days=1, ticks=30).prints())
    assert table.num_rows == 30 * NAMES
    assert table.column_names == [
        "day",
        "tick",
        "instrument_id",
        "print",
        "model_price",
        "shock",
        "absorbed",
    ]


def test_the_counterfactual_columns_arrive_only_when_asked_for():
    """Presence is the signal, so an absent arm cannot read as a zero one.

    A column of zeros would say liquidity moved nothing, which is a
    measurement. Nobody having asked is not.
    """
    off = pa.table(run(counterfactual=False, days=1, ticks=30).prints())
    on = pa.table(run(counterfactual=True, days=1, ticks=30).prints())
    assert "unbounded_print" not in off.column_names
    assert "liquidity_share" not in off.column_names
    assert on.column_names[-2:] == ["unbounded_print", "liquidity_share"]


def test_the_schema_says_which_of_the_two_shapes_it_is():
    """The metadata is derived from the columns rather than retyped."""
    for asked in (True, False):
        table = pa.table(run(counterfactual=asked, days=1, ticks=30).prints())
        meta = {k.decode(): v.decode() for k, v in table.schema.metadata.items()}
        assert meta["depth_counterfactual"] == ("on" if asked else "off")
        assert meta["columns"].split(",") == table.column_names
        assert "unbounded_print" in meta["caveat"]


def test_every_numeric_column_is_f64_and_every_identifier_an_integer():
    table = pa.table(run(counterfactual=True, days=1, ticks=30).prints())
    for field in table.schema:
        if field.name in ("day", "tick", "instrument_id"):
            assert pa.types.is_unsigned_integer(field.type), field.name
        else:
            assert field.type == pa.float64(), field.name


def test_it_joins_to_bars_and_truth_on_the_same_key():
    engine = run(counterfactual=False, days=1, ticks=30)
    prints = pa.table(engine.prints()).to_pydict()
    bars = pa.table(engine.bars()).to_pydict()
    truth = pa.table(engine.truth()).to_pydict()
    for key in ("day", "tick", "instrument_id"):
        assert prints[key] == bars[key] == truth[key]
    # `print` is the same number `bars` calls `close`, and `model_price` is
    # the same number `truth` calls `anchor_price`. Two names for one value
    # is a join, not a duplicate: this table is about the distance between
    # them.
    assert prints["print"] == bars["close"]
    assert prints["model_price"] == truth["anchor_price"]


def test_asking_for_one_day_returns_that_day():
    engine = run(counterfactual=False, days=2, ticks=20)
    table = pa.table(engine.prints(day=1)).to_pydict()
    assert set(table["day"]) == {1}
    assert len(table["day"]) == 20 * NAMES


def test_a_run_that_changed_its_mind_mid_way_is_refused():
    """Two kinds of day in one table would mislabel one of them."""
    universe = tradefloor.Universe.random(NAMES, seed=ROSTER_SEED)
    engine = tradefloor.Engine(seed=RUN_SEED, universe=universe)
    engine.open_market()
    engine.run_session(9, 30, 3, 20)
    engine.close_market()
    engine.record(0)
    engine.settle_depth_counterfactual(True)
    engine.open_market()
    engine.run_session(9, 30, 3, 20)
    engine.close_market()
    engine.record(1)
    with pytest.raises(tradefloor.ValidationError, match="depth counterfactual"):
        engine.prints()
    # Each day on its own still reads, and says what it is.
    assert "unbounded_print" not in pa.table(engine.prints(day=0)).column_names
    assert "unbounded_print" in pa.table(engine.prints(day=1)).column_names


def test_a_session_that_was_never_recorded_still_reads():
    """The fallback `bars` and `truth` both have, and the same rule for it.

    Nothing recorded means there is no day to select, so `day` labels the
    rows instead of choosing them.
    """
    universe = tradefloor.Universe.random(NAMES, seed=ROSTER_SEED)
    engine = tradefloor.Engine(seed=RUN_SEED, universe=universe)
    engine.settle_depth_counterfactual(True)
    engine.open_market()
    engine.run_session(9, 30, 3, 40)
    table = pa.table(engine.prints(day=7)).to_pydict()
    assert len(table["print"]) == 40 * NAMES
    assert set(table["day"]) == {7}
    assert len(table["unbounded_print"]) == 40 * NAMES


# ── The decomposition ─────────────────────────────────────────────────────


def test_the_shock_and_the_absorption_sum_to_the_print_move():
    """The identity the table exists to let a reader check.

    Roster `Universe.random(12, seed=111)`, seed 42, three days, the shipped
    preset. Asserted as a derivation rather than a pinned tolerance-free
    number: `shock` and `absorbed` are two logs and the move is a third, so
    they agree to rounding rather than to the bit.
    """
    table = pa.table(run(counterfactual=False).prints()).to_pydict()
    prints = table["print"]
    shock = table["shock"]
    absorbed = table["absorbed"]
    rows = len(prints)
    checked = 0
    # From the second tick of each day, where the previous row on the same
    # tape is in the table. The first tick of a day differences against
    # yesterday's close, which is a row this table does not carry.
    for row in range(rows):
        tick = table["tick"][row]
        if tick == 0:
            continue
        previous = prints[row - NAMES]
        moved = math.log(prints[row] / previous)
        assert abs(shock[row] + absorbed[row] - moved) < 1e-12, (
            f"row {row}: shock {shock[row]} + absorbed {absorbed[row]} "
            f"is not the move {moved}"
        )
        checked += 1
    assert checked == (TICKS - 1) * NAMES * DAYS


def test_the_model_price_is_where_the_shock_lands():
    """`shock` is measured to `model_price`, and the table carries both."""
    table = pa.table(run(counterfactual=False, days=1, ticks=60).prints()).to_pydict()
    for row in range(NAMES, len(table["print"])):
        previous = table["print"][row - NAMES]
        want = math.log(table["model_price"][row] / previous)
        assert abs(table["shock"][row] - want) < 1e-15


def test_a_tick_that_printed_nothing_new_absorbed_nothing():
    """No settlement means the print IS the model price.

    A closed or empty tick has an absorption of exactly zero rather than a
    small number, because nothing stood between the model and the tape.
    """
    table = pa.table(run(counterfactual=False, days=1, ticks=60).prints()).to_pydict()
    for row, (printed, model) in enumerate(
        zip(table["print"], table["model_price"])
    ):
        if printed == model:
            assert table["absorbed"][row] == 0.0, row


# ── The counterfactual ────────────────────────────────────────────────────


def test_the_counterfactual_moves_neither_the_market_nor_the_draws():
    """The arm is an observation, so the two runs are one market.

    This is the same claim `tests/known_answer.py` makes over a digest, made
    here over every price of a three-day run so a failure names the tick.
    """
    off = run(counterfactual=False)
    on = run(counterfactual=True)
    assert off.draws_by_stream() == on.draws_by_stream()
    a = pa.table(off.prints()).to_pydict()
    b = pa.table(on.prints()).to_pydict()
    for column in ("print", "model_price", "shock", "absorbed"):
        assert a[column] == b[column], column


def test_the_share_is_zero_wherever_the_two_prints_coincide():
    """Including every tick that never settled.

    Depth cannot move a print that no flow reached, so the share on those
    rows is zero by construction rather than by rounding.
    """
    table = pa.table(run(counterfactual=True).prints()).to_pydict()
    for row, (printed, unbounded) in enumerate(
        zip(table["print"], table["unbounded_print"])
    ):
        if printed == unbounded:
            assert table["liquidity_share"][row] == 0.0, row


def test_the_share_is_never_an_infinity():
    """A print that did not move has no move to apportion, and says NaN.

    Dividing anyway would put an infinity in the column, and one infinity
    turns every mean taken over it into an infinity too.
    """
    table = pa.table(run(counterfactual=True).prints()).to_pydict()
    prints = table["print"]
    for row, share in enumerate(table["liquidity_share"]):
        assert not math.isinf(share), row
        if math.isnan(share):
            assert table["tick"][row] > 0
            assert prints[row] == prints[row - NAMES], row


def test_the_share_reconstructs_from_the_two_prints():
    """The column is the ratio it says it is, on every row that has one."""
    table = pa.table(run(counterfactual=True).prints()).to_pydict()
    prints = table["print"]
    checked = 0
    for row, share in enumerate(table["liquidity_share"]):
        if table["tick"][row] == 0 or share == 0.0 or math.isnan(share):
            continue
        previous = prints[row - NAMES]
        want = math.log(prints[row] / table["unbounded_print"][row]) / math.log(
            prints[row] / previous
        )
        assert abs(share - want) < 1e-9, row
        checked += 1
    assert checked > 0, "no row carried a share to check"


def test_the_arm_reports_something_on_this_roster():
    """A surface that measured zero everywhere would pass every test above.

    So this one asserts the arm is not vacuous on the roster the docstrings
    name: some print on `Universe.random(12, seed=111)` at seed 42 differs
    from what unbounded depth would have printed.
    """
    table = pa.table(run(counterfactual=True).prints()).to_pydict()
    differing = sum(
        1
        for printed, unbounded in zip(table["print"], table["unbounded_print"])
        if printed != unbounded
    )
    assert differing > 0
    assert differing < len(table["print"]), (
        "every print differing would mean the bound binds on every tick, "
        "which the book is built not to do"
    )
