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

import hashlib
import io
import math
import subprocess
import sys

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
        "clamp",
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


def test_the_schema_carries_one_caveat_computed_from_the_state():
    """The caveat is derived from the state rather than retyped.

    One key, and the count is asserted, because `arrow::Schema::metadata` is
    a `HashMap` whose iteration order the IPC writer serialises. A second key
    makes the footer bytes depend on a per-map hash seed, which the test
    below measures.
    """
    for asked in (True, False):
        table = pa.table(run(counterfactual=asked, days=1, ticks=30).prints())
        meta = {k.decode(): v.decode() for k, v in table.schema.metadata.items()}
        assert list(meta) == ["caveat"], meta
        caveat = meta["caveat"]
        assert "circuit breaker" in caveat
        if asked:
            assert "unbounded_print is the same tick" in caveat
            assert "NEGATIVE" in caveat
        else:
            assert "did not run" in caveat


def test_two_writes_of_one_table_are_byte_identical():
    """A published artifact has to be the same bytes twice.

    The schema metadata reaches the IPC footer in `HashMap` iteration order,
    and `RandomState` reseeds per map, so three keys gave two different
    digests for one table inside a single process. One key cannot vary.
    """
    ipc = pytest.importorskip("pyarrow.ipc")

    def written(counterfactual):
        table = pa.table(run(counterfactual=counterfactual, days=1, ticks=20).prints())
        sink = io.BytesIO()
        with ipc.new_file(sink, table.schema) as writer:
            writer.write_table(table)
        return sink.getvalue(), table

    for counterfactual in (True, False):
        first, table_a = written(counterfactual)
        second, table_b = written(counterfactual)
        assert table_a.equals(table_b)
        assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest(), (
            f"counterfactual={counterfactual}: two writes of one table differ"
        )
        # And the key order a reader sees is the same order both times.
        assert [k for k in table_a.schema.metadata] == [
            k for k in table_b.schema.metadata
        ]


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


def test_the_bytes_are_the_same_from_a_fresh_process():
    """The same table, written by two processes, is the same file.

    The in-process case above is the surprising one, because each `HashMap`
    gets its own hasher state. This is the one a reader would think of first,
    and it costs a second interpreter.
    """
    ipc = pytest.importorskip("pyarrow.ipc")
    del ipc
    script = (
        "import hashlib, io, tradefloor, pyarrow as pa, pyarrow.ipc as ipc;"
        "u = tradefloor.Universe.random(3, seed=1);"
        "e = tradefloor.Engine(seed=1, universe=u);"
        "e.settle_depth_counterfactual(True);"
        "e.open_market();"
        "e.run_session(9, 30, 3, 5);"
        "e.close_market();"
        "e.record(0);"
        "t = pa.table(e.prints());"
        "b = io.BytesIO();"
        "w = ipc.new_file(b, t.schema);"
        "w.write_table(t);"
        "w.close();"
        "print(hashlib.sha256(b.getvalue()).hexdigest())"
    )
    digests = {
        subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for _ in range(3)
    }
    assert len(digests) == 1, f"three processes wrote {len(digests)} files: {digests}"


def test_a_day_that_switched_the_arm_mid_way_drops_both_columns():
    """The columns go, and the caveat says why rather than what to do.

    A day whose sessions disagree records fewer counterfactual values than
    it has rows. Serving that as a column would put a real value on the
    wrong row for the rest of the day, so both go. The off-branch caveat
    would tell this caller to set the arm before the run, which is what they
    just did, so the state has a caveat of its own.
    """
    universe = tradefloor.Universe.random(NAMES, seed=ROSTER_SEED)
    engine = tradefloor.Engine(seed=RUN_SEED, universe=universe)
    engine.open_market()
    engine.run_session(9, 30, 3, 5)
    engine.settle_depth_counterfactual(True)
    engine.run_session(9, 35, 3, 5)
    engine.close_market()
    engine.record(0)

    table = pa.table(engine.prints(day=0))
    assert table.num_rows == 10 * NAMES
    assert "unbounded_print" not in table.column_names
    assert "liquidity_share" not in table.column_names
    caveat = table.schema.metadata[b"caveat"].decode()
    assert "switched part way through a day" in caveat
    assert "before the first session of a day" in caveat


def test_switching_the_arm_off_mid_day_drops_them_the_same_way():
    universe = tradefloor.Universe.random(NAMES, seed=ROSTER_SEED)
    engine = tradefloor.Engine(seed=RUN_SEED, universe=universe)
    engine.settle_depth_counterfactual(True)
    engine.open_market()
    engine.run_session(9, 30, 3, 5)
    engine.settle_depth_counterfactual(False)
    engine.run_session(9, 35, 3, 5)
    engine.close_market()
    engine.record(0)

    table = pa.table(engine.prints(day=0))
    assert "unbounded_print" not in table.column_names
    caveat = table.schema.metadata[b"caveat"].decode()
    assert "switched part way through a day" in caveat


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
    # Every row but the twelve the run opens on. The table is tick-major
    # over concatenated days, so `row - NAMES` is the same instrument's
    # previous print INCLUDING across a day boundary: the close does not
    # move a price, so the first tick of day 1 differences against the last
    # print of day 0 and the identity holds there too.
    for row in range(NAMES, rows):
        previous = prints[row - NAMES]
        moved = math.log(prints[row] / previous)
        assert abs(shock[row] + absorbed[row] - moved) < 1e-12, (
            f"row {row}: shock {shock[row]} + absorbed {absorbed[row]} "
            f"is not the move {moved}"
        )
        checked += 1
    assert checked == TICKS * NAMES * DAYS - NAMES


def test_the_model_price_is_where_the_shock_lands():
    """`shock` is measured to `model_price`, and the table carries both."""
    table = pa.table(run(counterfactual=False, days=1, ticks=60).prints()).to_pydict()
    for row in range(NAMES, len(table["print"])):
        previous = table["print"][row - NAMES]
        want = math.log(table["model_price"][row] / previous)
        assert abs(table["shock"][row] - want) < 1e-15


def test_a_tick_that_printed_nothing_new_absorbed_nothing():
    """No settlement means the print IS the model price.

    Such a tick has an absorption of exactly zero rather than a small
    number, because nothing stood between the model and the tape.

    Run in the PRE-MARKET session, from 07:00, where every tick prices and
    none settles. An open session at the shipped roster and seeds produced
    no such row at all, so the same assertion inside a regular run never
    executed its body: 0 rows of 14,040 over three days.
    """
    universe = tradefloor.Universe.random(NAMES, seed=ROSTER_SEED)
    engine = tradefloor.Engine(seed=RUN_SEED, universe=universe)
    engine.open_market()
    engine.run_session(7, 0, 3, 60)
    table = pa.table(engine.prints()).to_pydict()

    checked = 0
    for row, (printed, model) in enumerate(
        zip(table["print"], table["model_price"])
    ):
        assert printed == model, (
            f"row {row}: a pre-market tick prints the model price, "
            f"{printed} against {model}"
        )
        assert table["absorbed"][row] == 0.0, row
        checked += 1
    assert checked == 60 * NAMES, checked
    # And the shock is still measured, because fair value moved even though
    # no book touched it. A column of zeros here would mean the pre-market
    # rows carry no information at all.
    assert any(v != 0.0 for v in table["shock"])


# ── The breaker's share of the absorption ─────────────────────────────────


def halted_run(days=12, ticks=100):
    """A market stressed hard enough that the print breaker fires.

    The shipped roster at its shipped macro never halts a name, so the
    clamp column is a structural zero there and every assertion about it
    would pass over rows that could not fail. These pins are the ones a
    review measured 146 clamped prints on.
    """
    universe = tradefloor.Universe.random(8, seed=5)
    engine = tradefloor.Engine(seed=11, universe=universe)
    engine.pin_macro(
        vix=85.0,
        federal_funds_rate=0.06,
        corporate_bond_yield=0.14,
        inflation_rate=0.09,
        qe_pe_boost=0.0,
        fear_greed_index=3.0,
    )
    for day in range(days):
        engine.open_market()
        engine.run_session(9, 30, 3, ticks, volatility=12.0)
        engine.close_market()
        engine.record(day)
    return engine


def test_the_clamp_is_reported_apart_from_the_book():
    """The breaker's share of the absorption, and why it needs its own column.

    On a clamped print the book and the breaker pull opposite ways, and on
    most of them they cancel exactly, so `absorbed` alone reads 0.0 on a name
    the breaker had just moved hundreds of basis points. That is the same
    value `absorbed` takes on a tick that never settled, so without `clamp`
    the two rows are the same row.

    Both halves are asserted: the three terms still add up to the move, and
    the two cases are distinguishable.
    """
    table = pa.table(halted_run().prints()).to_pydict()
    names = 8
    prints = table["print"]

    clamped = 0
    cancelled = 0
    opposed = 0
    for row in range(names, len(prints)):
        clamp = table["clamp"][row]
        absorbed = table["absorbed"][row]
        book = absorbed - clamp
        previous = prints[row - names]
        moved = math.log(prints[row] / previous)
        assert abs(table["shock"][row] + book + clamp - moved) < 1e-12, (
            f"row {row}: shock + book + clamp is not the move"
        )
        if clamp == 0.0:
            continue
        clamped += 1
        # The measured shape: never the same direction as the book.
        if book != 0.0:
            assert book * clamp < 0, (
                f"row {row}: the book and the breaker should oppose, "
                f"book {book} clamp {clamp}"
            )
            opposed += 1
        if absorbed == 0.0:
            cancelled += 1

    assert clamped > 0, "no print was clamped, so this run cannot test the column"
    assert opposed > 0
    assert cancelled > 0, (
        "no clamped row cancelled to exactly zero absorption, which is the "
        "case the column exists to tell apart"
    )


def test_a_clamped_print_is_told_apart_from_one_that_never_settled():
    """The distinguishability the arithmetic alone does not give.

    Two rows both read `absorbed == 0.0`. One never settled and its print is
    the model price; the other was halted and its print is the band edge.
    `clamp` separates them, and nothing else in the table does.
    """
    table = pa.table(halted_run().prints()).to_pydict()
    halted = [
        row
        for row, (a, c) in enumerate(zip(table["absorbed"], table["clamp"]))
        if a == 0.0 and c != 0.0
    ]
    quiet = [
        row
        for row, (a, c) in enumerate(zip(table["absorbed"], table["clamp"]))
        if a == 0.0 and c == 0.0
    ]
    assert halted, "no halted row with zero net absorption"
    assert quiet, "no quiet row"
    # The point of the column, stated as the thing that is true: on these two
    # sets of rows `absorbed` carries the same value, so it cannot separate
    # them, and `clamp` carries different values, so it can.
    assert all(table["absorbed"][row] == 0.0 for row in halted + quiet)
    assert all(table["clamp"][row] != 0.0 for row in halted)
    assert all(table["clamp"][row] == 0.0 for row in quiet)
    # A row with no absorption and no clamp printed the model price, since
    # `absorbed` is the log distance between the two. A halted row need not:
    # the first breaker can clamp the model price to the same band edge the
    # second clamps the print to, and then the two coincide with a clamp of
    # several hundred basis points behind them.
    for row in quiet:
        assert table["print"][row] == table["model_price"][row]


def test_the_clamp_is_zero_wherever_the_breaker_did_not_fire():
    """On the shipped roster it is a column of zeros, and that is correct."""
    table = pa.table(run(counterfactual=False).prints()).to_pydict()
    assert all(v == 0.0 for v in table["clamp"])


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


def test_the_share_is_negative_where_the_bound_truncates_the_walk():
    """The sign is the finding, so it is asserted rather than described.

    The depth bound TRUNCATES a walk. An order that exhausts a shallow book
    stops there; against every resting level it keeps filling and prints
    further from where it started. So the real print lies between the last
    print and the unbounded print, the numerator opposes the denominator,
    and the share comes out below zero.

    Asserted as the derivation on every negative row, and as a count so the
    run cannot pass by carrying no such rows at all. A positive share is the
    other case, where the deeper book printed nearer the last price or past
    it on the far side; it is counted rather than characterised, because the
    identity in the next test already covers every row.
    """
    table = pa.table(run(counterfactual=True).prints()).to_pydict()
    prints = table["print"]
    negative = 0
    positive = 0
    for row, share in enumerate(table["liquidity_share"]):
        if table["tick"][row] == 0 or share == 0.0 or math.isnan(share):
            continue
        previous = prints[row - NAMES]
        printed_move = math.log(prints[row] / previous)
        unbounded_move = math.log(table["unbounded_print"][row] / previous)
        if share < 0:
            negative += 1
            # A negative share is the truncation case, and it says two
            # things at once: the deeper book moved the price the same way
            # and moved it further.
            assert unbounded_move * printed_move > 0, (
                f"row {row}: a negative share needs both moves to point the "
                f"same way, {unbounded_move} against {printed_move}"
            )
            assert abs(unbounded_move) > abs(printed_move), (
                f"row {row}: a negative share needs the deeper book to have "
                f"moved further, {unbounded_move} against {printed_move}"
            )
        else:
            positive += 1
    assert negative > 0, "no row had the bound truncate a walk"
    assert negative > positive, (
        f"the bound is expected to truncate far more often than it extends: "
        f"{negative} against {positive}"
    )


def test_the_unbounded_move_is_one_minus_the_share_times_the_printed_move():
    """The identity every surface documents the share through.

    A share of -1 means the deeper book would have moved the price twice as
    far. This is what makes the column readable without a sign convention to
    remember.
    """
    table = pa.table(run(counterfactual=True).prints()).to_pydict()
    prints = table["print"]
    checked = 0
    for row, share in enumerate(table["liquidity_share"]):
        if table["tick"][row] == 0 or math.isnan(share):
            continue
        previous = prints[row - NAMES]
        if prints[row] == previous:
            continue
        printed_move = math.log(prints[row] / previous)
        unbounded_move = math.log(table["unbounded_print"][row] / previous)
        assert abs(unbounded_move - printed_move * (1 - share)) < 1e-9, row
        checked += 1
    assert checked > 0


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
