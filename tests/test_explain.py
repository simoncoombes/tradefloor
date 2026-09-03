"""One name's day, explained: what the tree claims and what checks it.

The claim these tests defend is not "the simulator reports where a move
came from". It is that the report RECONSTRUCTS the move and that every
node of it is runnable: the eleven contributions sum to the day's log
move, and each node replays from the copy taken before the day opened to
the contribution it sits under. Both are checkable, and both are checked
here rather than asserted in a docstring.

The mechanism table is the other half. It names Rust functions, dials,
engine columns, macro fields and draw sites, and a name in it that Rust
does not read is a false claim with a plausible number beside it. Every
name is checked against the source of the function it is declared on.
"""

import ast
import json
import math
import re
import struct
import subprocess
from pathlib import Path

import pytest

import tradefloor as tf
from tradefloor import explain as ex
from tradefloor import noise
from tradefloor._core import ValidationError

pa = pytest.importorskip("pyarrow")

ROOT = Path(__file__).resolve().parent.parent
RUST = ROOT / "rust" / "src"

#: The roster, seed and preset every measured claim below is made on.
ROSTER_SIZE, ROSTER_SEED, ENGINE_SEED, PRESET = 12, 111, 42, "pt-v16"


def engine(days=4, keep=(1, 2), record=True, preset=PRESET):
    roster = tf.Universe.random(ROSTER_SIZE, seed=ROSTER_SEED)
    e = tf.Engine(seed=ENGINE_SEED, universe=roster, model=preset)
    if keep is not None:
        e.keep_explanations(*keep)
    e.run_days(days, record=record)
    return e


def one(day=1, **kwargs):
    e = engine(**kwargs)
    return e, e.explain(e.tickers[0], day)


def _f64(buf):
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def rows_for(table, index):
    ids = table["instrument_id"]
    return [k for k in range(len(ids)) if ids[k] == index]


# --------------------------------------------------------------------------
# The decomposition reconstructs the move
# --------------------------------------------------------------------------


def test_the_contributions_are_the_declared_eleven_and_nothing_else():
    # Membership as an exact list, in order. A tree that grew a twelfth
    # contribution, or lost one, would still sum to the move if the
    # arithmetic were rearranged around it.
    _, result = one()
    names = [child.name for child in result.root.children]
    assert names == list(ex.CONTRIBUTIONS)
    assert list(ex.CONTRIBUTIONS)[:9] == list(tf.Engine.FACTORS)
    assert list(ex.CONTRIBUTIONS)[9:] == ["fair_value", "book"]
    assert all(child.kind == "factor" for child in result.root.children)
    assert result.root.kind == "move"


def test_the_contributions_sum_to_the_move():
    """The property that makes this a decomposition rather than a list.

    Measured on ``Universe.random(12, seed=111)`` at engine seed 42, day
    1, ``pt-v16``: the eleven sum to the move with a residual of 1.4e-16,
    which is the two logs the move is taken through and the order of the
    additions.
    """
    _, result = one()
    total = math.fsum(child.value for child in result.root.children)
    assert abs(result.move - total) < ex.TOLERANCE
    assert result.move != 0.0


def test_the_nine_truth_columns_sum_to_the_change_in_mispricing():
    # The nine are the tape's own columns, so they sum to the day's change
    # in `s` and NOT to the printed move: the gap is the book and the
    # valuation, which the other two contributions carry. Measured here
    # rather than assumed, because a tree that put the printed move on the
    # nine would look right and be wrong by 2.2e-3 on this very day.
    e, result = one()
    nine = {child.name: child.value for child in result.root.children
            if child.name in tf.Engine.FACTORS}
    table = pa.table(e.truth(day=1)).to_pydict()
    rows = rows_for(table, 0)
    for name, value in nine.items():
        assert value == pytest.approx(
            math.fsum(table[name][k] for k in rows), abs=1e-15), name
    mispricing = math.fsum(nine.values())
    assert abs(result.move - mispricing) > 1e-6, (
        "the nine columns reproduced the printed move exactly, so either "
        "the book absorbed nothing on this day or the nine are not the "
        "tape's columns")


def test_a_measured_contribution_leaves_a_residual_a_remainder_cannot():
    """The guard that separates a measurement from a remainder.

    Both contributions are computed from the day before's closing levels
    rather than as what the mispricing leaves over. A remainder would make
    the eleven sum to the move whatever the engine had done, and the sum
    test could not tell the two apart, because a remainder and the direct
    computation agree to 1e-15.

    What tells them apart is the residual itself. Measured, it is float
    rounding and never exactly zero; as a remainder it is exactly zero by
    construction. The fallback branch, where the day before is not on the
    tape, IS a remainder, so the two branches are measured here side by
    side on the same engine and day.
    """
    e = engine()
    residuals = []
    for day in (1, 2):
        for name in e.tickers[:6]:
            result = e.explain(name, day)
            total = math.fsum(child.value for child in result.root.children)
            residuals.append(result.move - total)
    assert all(r != 0.0 for r in residuals), residuals
    assert all(abs(r) < ex.TOLERANCE for r in residuals), max(residuals)

    # The same day on a run with no tape behind it, where `book` IS the
    # remainder and the residual therefore vanishes.
    remainder = engine(record=False).explain(e.tickers[0], 1)
    total = math.fsum(child.value for child in remainder.root.children)
    assert remainder.move - total == 0.0
    assert remainder._previous is None


def test_the_book_share_equals_the_distance_from_the_anchor_to_the_print():
    """``book`` is stated as a remainder, so it is checked as a measurement.

    ``log(close)`` is ``log(fundamental_value) + mispricing_s`` plus the
    log distance from the anchor to the print, so the day's change in
    that distance is what the valuation and the mispricing do not account
    for. Computed both ways here: as the remainder the tree reports, and
    directly off the two days' anchor prices and closes.
    """
    e, result = one()
    book = {child.name: child.value
            for child in result.root.children}["book"]
    today = pa.table(e.truth(day=1)).to_pydict()
    before = pa.table(e.truth(day=0)).to_pydict()
    bars = pa.table(e.bars(grain="day")).to_pydict()
    close = {(bars["day"][k], bars["instrument_id"][k]): bars["close"][k]
             for k in range(len(bars["day"]))}
    end = rows_for(today, 0)[-1]
    prev = rows_for(before, 0)[-1]
    direct = (math.log(close[(1, 0)] / today["anchor_price"][end])
              - math.log(close[(0, 0)] / before["anchor_price"][prev]))
    assert book == pytest.approx(direct, abs=1e-12)
    assert book != 0.0


# --------------------------------------------------------------------------
# Every node is runnable
# --------------------------------------------------------------------------


def test_every_node_replays_to_the_contribution_it_sits_under():
    # check() reports four families of miss at once: the sum, every
    # node's replay, the nine columns against the tape the run recorded,
    # and the printed close against the same tape.
    _, result = one()
    assert result.check() == []


def test_the_replay_reproduces_the_close_the_run_printed():
    """The comparison the per-node replays cannot make.

    Every node's replay runs the same day the same way, so a replay that
    rebuilt the WRONG day agrees with itself at every node and reports
    nothing. The tape is the only thing outside that loop, and the close
    is what the nine columns do not cover: they say the mispricing path
    was rebuilt, and the print is settled through the book after it.
    """
    e, result = one()
    bars = pa.table(e.bars(grain="day")).to_pydict()
    close = {(bars["day"][k], bars["instrument_id"][k]): bars["close"][k]
             for k in range(len(bars["day"]))}
    assert result._recorded_close == close[(1, 0)]
    # To the bit, not to a tolerance: the same day from the same state
    # under the same draws prints the same price.
    assert result._base.levels["close"] == result._recorded_close
    assert result.root.inputs["close"] == result._recorded_close


def test_the_recorded_columns_are_read_and_matched():
    """check()'s third claim, pinned positively.

    Making `_recorded` return None unconditionally switches the claim off
    and every other test still passes, so the claim needs a test that
    fails when it stops being made.
    """
    e, result = one()
    assert result._recorded is not None
    assert sorted(result._recorded) == sorted(tf.Engine.FACTORS)
    table = pa.table(e.truth(day=1)).to_pydict()
    rows = rows_for(table, 0)
    for name, value in result._recorded.items():
        assert value == pytest.approx(
            math.fsum(table[name][k] for k in rows), abs=1e-15), name
        assert result._base.factors[name] == pytest.approx(
            value, abs=ex.TOLERANCE), name


def test_an_unrecorded_run_has_no_close_to_compare_against():
    e = engine(record=False)
    result = e.explain(e.tickers[0], 1)
    assert result._recorded_close is None
    assert result.check() == []


def test_a_check_costs_one_day_run_per_distinct_overlay():
    """What a check() costs, as a count rather than as a clock.

    A replay is a function of its patch set, so nodes whose draws are the
    same share one run: the eleven contributions and their mechanisms
    collapse onto the overlays their draw nodes make, and every node with
    no draw under it shares the empty one. Fifteen distinct overlays,
    and the count does not move with the roster, which is what makes the
    cost readable without a stopwatch on a shared machine. The tree is 55
    nodes where the print table splits the book contribution and 53 where
    the build has no print table.
    """
    for size in (8, 12):
        roster = tf.Universe.random(size, seed=ROSTER_SEED)
        e = tf.Engine(seed=ENGINE_SEED, universe=roster, model=PRESET)
        e.keep_explanations(1, 1)
        e.run_days(3, record=True)
        result = e.explain(e.tickers[0], 1)
        assert result.check() == []
        assert len(result._runs) == 15, size
        assert len(result._walk) == (55 if HAS_PRINTS else 53), size
        assert len(ex._addresses(result.root)) == 2736, size


def test_a_leaf_replays_to_its_parent():
    _, result = one()
    leaves = [(path, node) for path, node, _ in result._walk
              if node.kind == "draw"]
    assert [name for _, name in
            ((n, n.name) for _, n in leaves)] == [
        "news_u", "news_z", "market_factor_z", "sector_z",
        "factor_idio_z", "jump_market_u", "jump_market_z",
        "jump_company_u", "jump_company_z", "settle_u"]
    for path, leaf in leaves:
        parent = result._by_path[path.rsplit(".", 1)[0]]
        assert parent.kind == "mechanism"
        # Its own parent, including where the print table splits the book
        # contribution and the settlement's leaf sits under the order
        # book's share rather than under the contribution.
        assert result.replay(leaf) == pytest.approx(parent.value,
                                                    abs=ex.TOLERANCE)


def test_a_changed_draw_moves_the_day():
    """The replay overlay bites, so the test above is not a tautology.

    Every patch a replay installs is the value the draw already
    delivered, so every replay reproduces the day and check() passes.
    That is only evidence if the overlay could have changed the day, so
    one address is installed at a different value here and the day is
    measured again.
    """
    _, result = one()
    idio = next(node for _, node, _ in result._walk
                if node.kind == "draw" and node.name == "factor_idio_z")
    address = idio.addresses[0]
    delivered = result._draw_values[id(idio)][0]
    base = result._run(())
    same = result._run((noise.Patch(address, delivered),))
    assert same.move == base.move
    moved = result._run((noise.Patch(address, delivered + 8.0),))
    assert moved.move != base.move
    assert abs(moved.factors["random_noise"]
               - base.factors["random_noise"]) > 1e-9


#: Which draw nodes a one-address slip is visible in, measured on
#: `Universe.random(12, seed=111)` at seed 42, day 1, `pt-v16`: the day's
#: close moves for these two and is bit-identical for the other eight.
#: A slip on the market factor moves every name and a slip on this name's
#: settlement uniforms moves its own prints, while a slip on its
#: idiosyncratic, sector, news or jump draws lands on a neighbour's slot
#: and does not reach this close inside the day.
SLIPS_SHOW = ("market_factor_z", "settle_u")


def test_a_mis_addressed_node_changes_the_day_where_it_can():
    """What makes check()'s node claim able to fail at all, and how far.

    A replay installs the values the log delivered against the node's own
    addresses, in log order. Read back at the address instead, a patch
    carries the value that address already delivers, so it is a no-op
    whatever the address is and a node aimed one draw late replayed
    clean. Paired positionally it does not, wherever the slipped draw
    reaches this name's day.

    Both halves are measured here, because a claim that every slip is
    caught would be false: the eight nodes below slip onto another name's
    slot and this name's close does not move inside the day.
    """
    _, result = one()
    base = result._run(())
    moved, unmoved = [], []
    for _path, node, _parent in result._walk:
        if node.kind != "draw":
            continue
        values = result._draw_values[id(node)]
        assert len(values) == len(node.addresses), node.name
        late = tuple(noise.Patch(a._replace(index=a.index + 1), v)
                     for a, v in zip(node.addresses, values))  # noqa: E501
        (moved if result._run(late).move != base.move
         else unmoved).append(node.name)
    assert tuple(moved) == SLIPS_SHOW, moved
    assert set(unmoved) == {"news_u", "news_z", "sector_z", "factor_idio_z",
                            "jump_market_u", "jump_market_z",
                            "jump_company_u", "jump_company_z"}, unmoved


def test_the_market_addresses_are_this_names_own_slots():
    """One half a replay cannot check: whose draws these are.

    A node built off another company's tag carries that company's
    addresses AND its values, so installing them is a no-op and check()
    stays clean. The day mark decides which slots belong to which name,
    so the addresses are checked against that arithmetic instead, which
    the draw log did not supply.
    """
    e, result = one()
    layout = noise.market_day_layout(e, 1)
    first, stride, ticks = layout[0]
    expected = tuple(noise.DrawAddress("market", "normal", first + t * stride)
                     for t in range(ticks))
    idio = next(node for _, node, _ in result._walk
                if node.kind == "draw" and node.name == "factor_idio_z")
    assert idio.addresses == expected
    assert layout[1][0] != first, "the neighbour shares this name's slots"
    assert idio.addresses[0].index != layout[1][0]


def test_the_sector_index_is_the_names_own_sector():
    """The sector tag, against the library's order rather than itself.

    The tag `_draws` filters `sector_z` on is the same number the node's
    addresses were selected by, so a test that reads the tag off the
    node's own draws passes on any sector. `tf.sectors()` is the
    canonical order the engine derives the tag from, and a roster of
    twelve at this seed spans twelve distinct sectors, so a name in
    technology cannot pass at index 0 by accident.
    """
    roster = tf.Universe.random(ROSTER_SIZE, seed=ROSTER_SEED)
    order = list(tf.sectors())
    e = tf.Engine(seed=ENGINE_SEED, universe=roster, model=PRESET)
    e.keep_explanations(1, 1)
    e.run_days(3, record=True)
    seen = set()
    for k, instrument in enumerate(roster):
        result = e.explain(e.tickers[k], 1)
        assert result._sector_key == instrument.sector, k
        assert result._sector == order.index(instrument.sector), k
        seen.add(result._sector)
    assert len(seen) == len(order) == 12, sorted(seen)


def test_every_draw_node_holds_this_names_own_draws():
    """The other half: every address belongs to the name being explained.

    Read straight off the engine's log rather than through the tree, so a
    node filtered on the wrong tag is caught by the tag the log records
    for the address it carries, whatever values came with it.
    """
    e, result = one()
    scope_of = {(stream, site): scope for m in ex.MECHANISMS
                for stream, site, scope in m.sites}
    wanted = {"company": 0, "sector": result._sector}
    seen = 0
    for _path, node, _parent in result._walk:
        if node.kind != "draw":
            continue
        stream = node.addresses[0].stream
        day = int(node.inputs["day"])
        logged = {entry.address: entry for entry
                  in noise.draw_log(e, stream, day, day)}
        scope = scope_of[(stream, node.name)]
        for address in node.addresses:
            entry = logged[address]
            assert entry.site == node.name, (node.name, entry.site)
            if scope in wanted:
                assert entry.tag == wanted[scope], (node.name, entry.tag)
            seen += 1
    assert seen == len(ex._addresses(result.root)) == 2736


def test_the_patches_a_node_builds_come_from_the_log_and_not_the_address():
    """The fix to the pairing, exercised through `_patches` itself.

    Reading each value back at the address it is installed at makes every
    overlay a no-op, whatever the address. The test above measures the
    engine by building its own patch list, so it would still pass with
    the pairing reverted; this one goes through `_patches`, on a node
    whose addresses have been slipped while its values have not.
    """
    _, result = one()
    market = next(node for _, node, _ in result._walk
                  if node.kind == "draw" and node.name == "market_factor_z")
    slipped = market._replace(
        addresses=tuple(a._replace(index=a.index + 1)
                        for a in market.addresses))
    result._draw_values[id(slipped)] = result._draw_values[id(market)]
    straight = result._run(result._patches(market))
    late = result._run(result._patches(slipped))
    assert straight.move == result._base.move
    assert late.move != result._base.move
    # And the values are the log's, in log order, at the node's own
    # addresses: the pair is what a patch carries.
    logged = {entry.address: entry.value
              for entries in result._logged.values() for entry in entries}
    for patch in result._patches(market):
        assert patch.value == logged[patch.address]
    assert [p.address for p in result._patches(slipped)] == \
        list(slipped.addresses)
    assert [p.value for p in result._patches(slipped)] == \
        [logged[a] for a in market.addresses]


def test_replay_refuses_a_node_from_another_explanation():
    e = engine()
    first = e.explain(e.tickers[0], 1)
    other = e.explain(e.tickers[1], 1)
    with pytest.raises(ValidationError, match=r"^that factor node is not"):
        first.replay(other.root.children[0])


def test_the_state_nodes_hold_the_values_the_day_opened_on():
    # `price` at the copy taken before the open IS the previous close the
    # day's breaker band is derived from, so it must equal the close on
    # the tape of the day before. Read from the snapshot rather than from
    # the `previous_close` column, which at that moment is a day older.
    e, result = one()
    breaker = next(child for child in result.root.children
                   if child.name == "circuit_breaker")
    price = next(node for node in breaker.children[0].children
                 if node.name == "price")
    bars = pa.table(e.bars(grain="day")).to_pydict()
    close = {(bars["day"][k], bars["instrument_id"][k]): bars["close"][k]
             for k in range(len(bars["day"]))}
    assert price.kind == "state"
    assert price.value == pytest.approx(close[(0, 0)], abs=1e-12)
    assert result.root.inputs["previous_close"] == price.value


# --------------------------------------------------------------------------
# The window
# --------------------------------------------------------------------------


def test_a_day_outside_the_window_raises_with_the_days_kept():
    e = engine(keep=(1, 2))
    with pytest.raises(ValidationError) as caught:
        e.explain(e.tickers[0], 3)
    message = str(caught.value)
    assert message.startswith("day 3 has no explanation kept")
    assert "days 1 to 2" in message
    assert "the days kept are 1, 2" in message


def test_a_day_never_asked_for_says_what_to_call():
    e = engine(keep=None)
    with pytest.raises(ValidationError,
                       match=r"^day 1 has no explanation kept, and none"):
        e.explain(e.tickers[0], 1)


def test_a_window_opened_after_the_day_ran_cannot_reach_it():
    # The store fills at the open, so asking afterwards is refused rather
    # than answered from a copy that does not exist.
    e = engine(keep=None)
    e.keep_explanations(1, 2)
    with pytest.raises(ValidationError, match=r"the days kept are none"):
        e.explain(e.tickers[0], 1)


def test_an_inverted_window_is_refused():
    e = tf.Engine(seed=1, universe=tf.Universe.random(4, seed=1))
    with pytest.raises(ValidationError, match=r"^keep_explanations takes"):
        e.keep_explanations(5, 2)


def test_an_unknown_ticker_is_refused_with_the_roster_the_day_had():
    e = engine()
    with pytest.raises(ValidationError,
                       match=r"^'NOPE' was not on the roster when day 1"):
        e.explain("NOPE", 1)
    # And with the roster the ENGINE has, on a day that was never kept,
    # since there is no copy to read a roster off.
    with pytest.raises(ValidationError, match=r"^'NOPE' is not in this"):
        e.explain("NOPE", 3)


# --------------------------------------------------------------------------
# Nothing about the market moves
# --------------------------------------------------------------------------


def test_the_window_changes_nothing_about_the_market():
    """The store and the log read; they never write.

    The comparison is the state hash, the prices, every stream's position
    and the draw counts, which together cover the market state, the
    generators and the schedule. A copy taken at an open that changed the
    engine would move at least one of them.
    """
    plain = engine(keep=None)
    watched = engine(keep=(0, 3))
    assert watched.state_hash() == plain.state_hash()
    assert _f64(watched.prices()) == _f64(plain.prices())
    assert watched.stream_positions() == plain.stream_positions()
    assert watched.draws_by_stream() == plain.draws_by_stream()
    assert watched.draws_consumed == plain.draws_consumed


def test_the_shipped_digest_is_where_the_fixture_says_it_is():
    # The gate's own quantity, on this build, before the test below
    # compares anything to it. A digest that had already moved would make
    # every comparison here a comparison between two wrong markets.
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "p1_known_answer", ROOT / "tests" / "known_answer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture = json.loads((ROOT / "tests" / "known_answer.json")
                         .read_text(encoding="utf-8"))
    assert module.simulation_digest() == fixture["simulationSha256"]
    assert module.KAT_VERSION == fixture["katVersion"]


def test_the_gates_own_run_is_the_same_run_with_the_window_open():
    """The digest's seed and horizon, run twice, with a window and without.

    The gate does not ask for a window, so this is the case it cannot
    cover: the same seed and the same number of days, with every day kept
    and every stream logged, against a plain run. The comparison is the
    state hash and the market digest, which is what the gate hashes.
    """
    fixture = json.loads((ROOT / "tests" / "known_answer.json")
                         .read_text(encoding="utf-8"))
    seed, days = fixture["seed"], 12
    roster = tf.Universe.random(ROSTER_SIZE, seed=ROSTER_SEED)
    watched = tf.Engine(seed=seed, universe=roster)
    watched.keep_explanations(0, days)
    watched.run_days(days, record=True)
    plain = tf.Engine(seed=seed, universe=roster)
    plain.run_days(days, record=True)
    assert watched.state_hash() == plain.state_hash()
    assert (tf.manifest.market_digest(watched)
            == tf.manifest.market_digest(plain))
    assert watched.explain(watched.tickers[0], days - 1).check() == []


def test_the_store_stays_out_of_the_snapshot_and_the_hash():
    # Recording-only data must not enter the snapshot, because the hash
    # covers every field the snapshot carries and a ledger written with a
    # window open would then refuse a ledger written without one.
    plain = engine(keep=None)
    watched = engine(keep=(0, 3))
    assert set(watched.state_snapshot()) == set(plain.state_snapshot())
    from tradefloor.manifest import state_hash
    assert state_hash(watched.state_snapshot()) == watched.state_hash()
    assert state_hash(plain.state_snapshot()) == watched.state_hash()


def test_a_kept_copy_carries_no_draw_log_and_the_same_counters():
    """The copy drops what the log holds and keeps everything else.

    The log is a recording buffer and the copy is never asked what it
    recorded, so its entries are weight: carried, they made the store
    quadratic in the window, since copy k held k days of a log the size
    of the tape. What must NOT move is the state: the hash covers
    draw_counts, and a copy whose counters had shifted would replay a
    different day.

    Measured on a run that both delists and lists, since a roster change
    is where a counter would be most likely to slip.
    """
    e = swapped()
    result = e.explain("AAB", 2)
    copy = result._opened
    # Nothing logged on the copy, on any of the seven streams.
    assert {s: len(noise.draw_log(copy, s, -1, 99))
            for s in noise.STREAMS} == {s: 0 for s in noise.STREAMS}
    # And the source engine, rebuilt to the same point, is the same state.
    source = tf.Engine(seed=ENGINE_SEED,
                       universe=tf.Universe.random(6, seed=ROSTER_SEED),
                       model=PRESET)
    source.keep_explanations(0, 5)
    source.run_days(2, record=True)
    source.delist(0)
    source.list_instrument(tf.Instrument("ZZZ", "technology",
                                         initial_price=50.0,
                                         shares_outstanding=1e6))
    assert copy.state_hash() == source.state_hash()
    assert copy.stream_positions() == source.stream_positions()
    assert copy.draws_by_stream() == source.draws_by_stream()
    assert copy.draws_consumed == source.draws_consumed
    counts = source.state_snapshot()["draw_counts"]
    assert copy.state_snapshot()["draw_counts"] == counts


def test_a_fork_of_a_cleared_copy_runs_the_same_market():
    # The measurement that matters: a copy whose log was emptied is still
    # the same engine, so a fork of it run on to the end reproduces the
    # source's trajectory to the bit.
    e = swapped()
    copy = e.explain("AAB", 2)._opened
    source = tf.Engine(seed=ENGINE_SEED,
                       universe=tf.Universe.random(6, seed=ROSTER_SEED),
                       model=PRESET)
    source.run_days(2, record=True)
    source.delist(0)
    source.list_instrument(tf.Instrument("ZZZ", "technology",
                                         initial_price=50.0,
                                         shares_outstanding=1e6))
    a, = copy.fork(1)
    b, = source.fork(1)
    a.run_days(6, record=True, first_day=2)
    b.run_days(6, record=True, first_day=2)
    assert _f64(a.prices()) == _f64(b.prices())
    assert a.state_hash() == b.state_hash()
    assert a.stream_positions() == b.stream_positions()
    assert a.draws_by_stream() == b.draws_by_stream()


def test_the_source_engines_own_log_survives_every_explain():
    # The tree reads the source's log, so a copy that emptied its own
    # must leave that one alone, in entries and in order.
    e = engine()

    def logged():
        return {s: [(tuple(d.address), d.value, d.day, d.site, d.tag)
                    for d in noise.draw_log(e, s, -1, 99)]
                for s in noise.STREAMS}

    before = logged()
    # 99,668 entries at twelve names over the two kept days plus
    # the one before them, so the check is over a real log rather
    # than an empty one.
    assert sum(len(v) for v in before.values()) == 99_668
    for day in (1, 2):
        assert e.explain(e.tickers[0], day).check() == []
    assert logged() == before


def test_a_fork_carries_the_days_its_parent_kept():
    e = engine()
    fork, = e.fork(1)
    assert fork.explain(fork.tickers[0], 1).check() == []


# --------------------------------------------------------------------------
# The jump slot's day
# --------------------------------------------------------------------------


def test_the_jump_node_addresses_the_close_before_the_day():
    """``apply_jumps`` runs at a close, so its draws are the day before's.

    Stated by comparing the node's addresses against both days' logs: they
    are exactly the previous day's jump addresses and none of the day's
    own. A node built off the wrong day would carry four plausible
    addresses with four plausible values.
    """
    e, result = one(day=2)
    jump = next(child for child in result.root.children
                if child.name == "jump")
    addressed = set()
    for node in jump.children[0].children:
        addressed.update(node.addresses)
    assert len(addressed) == 4, sorted(addressed)
    before = {entry.address for entry in noise.draw_log(e, "jumps", 1, 1)
              if entry.tag in (0,)}
    today = {entry.address for entry in noise.draw_log(e, "jumps", 2, 2)}
    assert addressed <= before
    assert not addressed & today


def test_the_jump_contribution_is_the_one_the_tape_books_to_the_day():
    # A day whose jump fired, found by running until one does rather than
    # by naming a day a preset change could move.
    roster = tf.Universe.random(ROSTER_SIZE, seed=ROSTER_SEED)
    e = tf.Engine(seed=ENGINE_SEED, universe=roster, model=PRESET)
    e.keep_explanations(0, 40)
    e.run_days(40, record=True)
    fired = []
    for day in range(1, 40):
        table = pa.table(e.truth(day=day)).to_pydict()
        rows = rows_for(table, 0)
        total = math.fsum(table["jump"][k] for k in rows)
        if total != 0.0:
            fired.append((day, total))
    assert fired, "no jump fired in forty days, so this test proved nothing"
    day, total = fired[0]
    result = e.explain(e.tickers[0], day)
    jump = next(child for child in result.root.children
                if child.name == "jump")
    assert jump.value == pytest.approx(total, abs=1e-15)
    assert result.check() == []


# --------------------------------------------------------------------------
# Where the previous day is not on the tape
# --------------------------------------------------------------------------


def test_the_valuation_moves_when_the_fundamental_does():
    """The eleventh contribution, on a path where it is not zero.

    `fair_value` is zero on every shipped preset, because earnings and the
    sector anchor are fixed for a run and the QE channel is off, so a test
    that only ran the defaults would assert nothing about it. Here the QE
    valuation channel is turned on and the macro path moves it between the
    two days.
    """
    roster = tf.Universe.random(ROSTER_SIZE, seed=ROSTER_SEED)
    model = tf.ModelParams.from_preset(PRESET, qe_pe_gain=2.0,
                                       qe_pe_stock_gain=2.0)
    e = tf.Engine(seed=ENGINE_SEED, universe=roster, model=model)
    e.keep_explanations(2, 2)
    e.run_days(2, record=True)
    e.pin_macro(qe_pe_boost=0.4)
    e.run_days(1, record=True)
    result = e.explain(e.tickers[0], 2)
    contributions = {child.name: child.value
                     for child in result.root.children}
    assert contributions["fair_value"] != 0.0
    assert abs(contributions["fair_value"]) > 0.1
    assert math.fsum(contributions.values()) == pytest.approx(
        result.move, abs=ex.TOLERANCE)
    assert result.check() == []
    # And zero on the shipped default, which is what the docstring says.
    assert {c.name: c.value for c in one()[1].root.children}["fair_value"] \
        == 0.0


def test_a_preset_that_draws_no_news_says_which_stream_was_silent():
    """The case the two silence caveats exist for.

    ``pt-v1`` carries no endogenous news, and the engine skips the news
    draws entirely at zero intensity, so that stream takes nothing at
    all. The news contribution then names a draw site with no address
    behind it, and both caveats have to say so rather than the tree
    presenting a leafless mechanism as one that read nothing.
    """
    roster = tf.Universe.random(ROSTER_SIZE, seed=ROSTER_SEED)
    e = tf.Engine(seed=ENGINE_SEED, universe=roster, model="pt-v1")
    e.keep_explanations(1, 1)
    e.run_days(3, record=True)
    result = e.explain(e.tickers[0], 1)
    assert sorted(result._traced) == ["jumps", "market"]
    sites = {node.name for _, node, _ in result._walk
             if node.kind == "draw"}
    assert "news_u" not in sites and "news_z" not in sites
    blind = next(c for c in result.caveats if "no leaf here" in c)
    assert "1 contribution" in blind and "company_news" in blind
    silent = next(c for c in result.caveats if "took no logged draw" in c)
    assert "1 stream" in silent and "(news)" in silent
    assert result.check() == []


def test_the_first_day_has_no_jump_leaf_and_the_caveat_names_it():
    # The jump a day carries was drawn at the close before it, and day
    # zero has no close before it, so the jump node is leafless there.
    e = engine(keep=(0, 1))
    result = e.explain(e.tickers[0], 0)
    jump = next(child for child in result.root.children
                if child.name == "jump")
    assert not [node for node in jump.children[0].children
                if node.kind == "draw"]
    blind = next(c for c in result.caveats if "no leaf here" in c)
    assert "1 contribution" in blind and "jump" in blind
    later = e.explain(e.tickers[0], 1)
    assert [node.name for node in later.root.children[8].children[0].children
            if node.kind == "draw"] == [
        "jump_market_u", "jump_market_z", "jump_company_u",
        "jump_company_z"]


def test_without_the_previous_day_the_valuation_and_the_book_are_one():
    e = engine(record=False)
    result = e.explain(e.tickers[0], 1)
    contributions = {child.name: child.value
                     for child in result.root.children}
    assert contributions["fair_value"] == 0.0
    assert math.fsum(contributions.values()) == pytest.approx(
        result.move, abs=ex.TOLERANCE)
    assert any("is not on this engine's tape" in c and "valuation" in c
               for c in result.caveats), result.caveats
    assert result.check() == []


def test_an_unrecorded_day_says_check_compared_against_no_tape():
    e = engine(record=False)
    result = e.explain(e.tickers[0], 1)
    assert result._recorded is None
    assert any("compares the replay against nothing the run itself" in c
               for c in result.caveats), result.caveats


def test_the_first_day_of_a_run_explains_against_its_opening_price():
    # Day 0 has no day before it, so the previous close is the price the
    # engine was built with and there is no tape to separate the
    # valuation from the book. Both are stated rather than left to fail.
    e = engine(keep=(0, 1))
    result = e.explain(e.tickers[0], 0)
    roster = tf.Universe.random(ROSTER_SIZE, seed=ROSTER_SEED)
    assert result.root.inputs["previous_close"] == pytest.approx(
        roster[0].initial_price, abs=1e-12)
    contributions = {child.name: child.value
                     for child in result.root.children}
    assert contributions["fair_value"] == 0.0
    assert math.fsum(contributions.values()) == pytest.approx(
        result.move, abs=ex.TOLERANCE)
    assert result.check() == []


def test_a_roster_edit_inside_the_day_is_refused_by_name():
    # A replay of one day runs the day's inputs, and a listing moves the
    # slot every column is read at, so the day is refused rather than
    # rebuilt against a roster of a different width.
    roster = tf.Universe.random(6, seed=ROSTER_SEED)
    e = tf.Engine(seed=ENGINE_SEED, universe=roster, model=PRESET)
    e.keep_explanations(0, 0)
    e.open_market()
    e.run_session(9, 30, 3, 60)
    e.list_instrument(tf.Instrument("ZZZ", "technology",
                                    initial_price=50.0,
                                    shares_outstanding=1e6))
    e.record(0)
    e.close_market()
    with pytest.raises(ValidationError,
                       match=r"^a day of inputs carries 'list_instrument'"):
        e.explain(e.tickers[0], 0)


# --------------------------------------------------------------------------
# The depth reading under the book contribution
# --------------------------------------------------------------------------

HAS_PRINTS = hasattr(tf.Engine, "prints")
needs_prints = pytest.mark.skipif(
    not HAS_PRINTS, reason="Engine.prints() is not on this build")


def prints_for(e, day, index):
    table = pa.table(e.prints(day=day)).to_pydict()
    rows = rows_for(table, index)
    return {name: math.fsum(table[name][k] for k in rows)
            for name in ("shock", "absorbed", "clamp")}, table, rows


def test_the_book_splits_exactly_when_the_build_has_prints():
    # The branch is decided by the build, not by an edit here, so the
    # test states whichever one this build is and cannot pass vacuously.
    _, result = one()
    book = next(child for child in result.root.children
                if child.name == "book")
    names = [child.name for child in book.children]
    if HAS_PRINTS:
        assert names == [function for _, function in ex.DEPTH]
        assert all(child.kind == "mechanism" for child in book.children)
    else:
        assert names == ["microstructure::settle_price_through_book"]


@needs_prints
def test_the_three_readings_add_to_the_book_contribution():
    """The identity, measured across days, seeds, presets and rosters.

    Each reading is computed on its own: the order book's share is summed
    absorbed less summed clamp, the breaker's is summed clamp, and the
    third is summed shock measured against the anchor's own move rather
    than taken as what the other two leave over.

    This test and the telescoping one below are ONE claim seen twice. The
    identity reduces to summed shock plus summed absorbed equalling the
    move, so the two fail together; the other is kept because it stands
    on prints() alone with no reference to this tree.
    """
    worst = 0.0
    # The tight band is here because clamp is exactly 0.0 on every
    # shipped preset, so on those the order book's share and the whole
    # absorption are the same number and a reading that dropped the
    # breaker would add up anyway.
    tight = tf.ModelParams.from_preset(PRESET, price_breaker_fraction=0.0005)
    clamped = 0
    for preset in (PRESET, "pt-v8", tight):
        for seed in (1, ENGINE_SEED, 909):
            for size in (6, 12):
                roster = tf.Universe.random(size, seed=ROSTER_SEED)
                e = tf.Engine(seed=seed, universe=roster, model=preset)
                e.keep_explanations(1, 3)
                e.run_days(5, record=True)
                for day in (1, 2, 3):
                    result = e.explain(e.tickers[0], day)
                    book = {c.name: c.value
                            for c in result.root.children}["book"]
                    children = next(c for c in result.root.children
                                    if c.name == "book").children
                    three = math.fsum(child.value for child in children)
                    worst = max(worst, abs(book - three))
                    clamped += children[1].value != 0.0
    assert worst < ex.TOLERANCE, worst
    assert clamped, "the breaker never bound, so the clamp is untested here"
    # Float rounding rather than an identity that happens to hold: a
    # remainder would give exactly zero on every one of the 36.
    assert worst > 0.0


@needs_prints
def test_the_four_correspondences_the_identity_rests_on():
    """The claims that bind, since the sum is exact by construction.

    book is the move less the anchor's move, and shock plus absorbed
    telescopes to the move, so the three adding to book is arithmetic.
    What can actually fail is the alignment between P2's table and this
    tree, and each of the four is asserted on its own so a failure names
    which one broke.
    """
    e, result = one()
    day, i = result._label, result._index
    sums, table, rows = prints_for(e, day, i)
    truth = pa.table(e.truth(day=day)).to_pydict()
    trows = rows_for(truth, i)

    # 1. the print table's model price is the tape's anchor, per tick
    assert len(rows) == len(trows)
    assert max(abs(table["model_price"][a] - truth["anchor_price"][b])
               for a, b in zip(rows, trows)) == 0.0

    # 2. the print before the day's first tick is the previous close
    first = rows[0]
    implied = table["print"][first] / math.exp(
        table["shock"][first] + table["absorbed"][first])
    assert implied == pytest.approx(
        result.root.inputs["previous_close"], abs=1e-9)

    # 3. the anchor at that point is the previous day's closing anchor
    before = pa.table(e.truth(day=day - 1)).to_pydict()
    brows = rows_for(before, i)
    assert result._previous["anchor_price"] == \
        before["anchor_price"][brows[-1]]

    # 4. shock and absorbed telescope over the day to the printed move
    assert sums["shock"] + sums["absorbed"] == pytest.approx(
        result.move, abs=ex.TOLERANCE)


@needs_prints
def test_the_breakers_share_is_non_zero_exactly_where_it_binds():
    """clamp is zero on a day the second breaker left alone.

    The day is found by narrowing the band until one binds rather than by
    naming a day a preset change could move, and the same run at the
    shipped band is the control.
    """
    roster = tf.Universe.random(ROSTER_SIZE, seed=ROSTER_SEED)
    tight = tf.ModelParams.from_preset(PRESET, price_breaker_fraction=0.0005)
    bound, loose = [], []
    for model, into in ((tight, bound), (PRESET, loose)):
        e = tf.Engine(seed=ENGINE_SEED, universe=roster, model=model)
        e.keep_explanations(1, 3)
        e.run_days(5, record=True)
        for day in (1, 2, 3):
            result = e.explain(e.tickers[0], day)
            book = next(c for c in result.root.children if c.name == "book")
            share = {name: child.value for (name, _), child
                     in zip(ex.DEPTH, book.children)}
            into.append(share["circuit_breaker_two"])
            assert result.check() == [], (model, day)
    assert any(v != 0.0 for v in bound), bound
    assert all(v == 0.0 for v in loose), loose
    # And the caveat says so on the day it did not bind, and not on one
    # where it did.
    e = tf.Engine(seed=ENGINE_SEED, universe=roster, model=tight)
    e.keep_explanations(1, 1)
    e.run_days(3, record=True)
    tightest = e.explain(e.tickers[0], 1)
    quiet = one()[1]
    assert any("did not bind" in c for c in quiet.caveats)
    if next(c for c in tightest.root.children
            if c.name == "book").children[1].value != 0.0:
        assert not any("did not bind" in c for c in tightest.caveats)


@needs_prints
def test_the_replayed_print_table_matches_the_one_the_run_recorded():
    """The split's numbers against the run's own print table.

    It does not show WHICH table the split read, and cannot: the two
    agree, so swapping the source changes nothing. What it shows is the
    agreement, which is the same family of claim as check()'s comparison
    of the nine columns and the close, and check() reports a mismatch in
    the same place.
    """
    e, result = one()
    assert result._base.depth
    sums, _, _ = prints_for(e, result._label, result._index)
    assert result._base.depth["absorbed"] == pytest.approx(
        sums["absorbed"], abs=1e-15)
    assert result._base.depth["shock"] == pytest.approx(
        sums["shock"], abs=1e-15)
    assert result.check() == []


# --------------------------------------------------------------------------
# The roster and the label the day was recorded under
# --------------------------------------------------------------------------


def delisted(delist=True, size=6):
    """A run that delists its first name half way, and the same run
    without the delisting, so the two can be compared name by name."""
    roster = tf.Universe.random(size, seed=ROSTER_SEED)
    e = tf.Engine(seed=ENGINE_SEED, universe=roster, model=PRESET)
    e.keep_explanations(0, 3)
    e.run_days(2, record=True)
    if delist:
        e.delist(0)
    # Both arms run the same days, so the control can be asked about any
    # of them rather than only the two before the change.
    e.run_days(2, record=True, first_day=2)
    return e


def contributions(result):
    return {child.name: child.value for child in result.root.children}


def test_a_delisting_does_not_move_which_name_a_day_explains():
    """The slot a name sits at is the slot it sat at on the day.

    A delisting shifts every slot below it, and a name resolved against
    the roster as it is now addresses another company in a day kept
    before the change: the state columns, the tape's instrument_id
    filter, the previous close and the company tag on the draws all
    follow that one index. Every claim check() makes agrees with the
    others in that state, because all four are wrong the same way.

    Stated against the DELISTED name rather than only for the right one,
    since asserting the tree equals the right name's is the same test
    written so it cannot fail by accident.
    """
    moved, clean = delisted(), delisted(delist=False)
    for day in (0, 1):
        after = contributions(moved.explain("AAB", day))
        assert after == contributions(clean.explain("AAB", day)), day
        assert after != contributions(clean.explain("AAA", day)), day
        assert moved.explain("AAB", day).check() == [], day
    # And the name that WAS delisted still explains on a day it traded,
    # because the copy that day was kept from still carries it.
    assert "AAA" not in moved.tickers
    assert contributions(moved.explain("AAA", 0)) == \
        contributions(clean.explain("AAA", 0))
    assert moved.explain("AAA", 0).check() == []


def swapped(keep=(0, 5), size=6):
    """A run that delists one name and lists another between day 1 and
    day 2, so both days' tapes are the same width while every slot
    between the two has moved."""
    roster = tf.Universe.random(size, seed=ROSTER_SEED)
    e = tf.Engine(seed=ENGINE_SEED, universe=roster, model=PRESET)
    e.keep_explanations(*keep)
    e.run_days(2, record=True)
    e.delist(0)
    e.list_instrument(tf.Instrument("ZZZ", "technology", initial_price=50.0,
                                    shares_outstanding=1e6))
    e.run_days(2, record=True, first_day=2)
    return e


def test_a_listing_that_hides_a_delisting_reads_the_slot_it_held():
    """The case a width comparison cannot see, read at the right slot.

    A delisting and a listing in one day leave the tape the same width
    and move every slot between them, so the previous day's tape holds
    this name at a slot it no longer has. The day before's own copy
    carries the roster that names it, so the levels are read there
    rather than the valuation collapsing into the book.
    """
    e = swapped()
    result = e.explain("AAB", 2)
    assert result._compared == "rosters"
    assert (result._previous_index, result._index) == (1, 0)
    assert result._previous_moved is True
    # AAB's own levels on day 1, read off that day's tape at slot 1.
    before = pa.table(e.truth(day=1)).to_pydict()
    rows = rows_for(before, 1)
    theirs = before["anchor_price"][rows[-1]]
    assert result._previous["anchor_price"] == theirs
    assert result._previous["fundamental_value"] == \
        before["fundamental_value"][rows[-1]]
    # And AAA's, which is what slot 0 holds on that tape, is not it.
    wrong = rows_for(before, 0)
    mistaken = before["anchor_price"][wrong[-1]]
    assert result._previous["anchor_price"] != mistaken
    total = math.fsum(child.value for child in result.root.children)
    assert abs(result.move - total) < ex.TOLERANCE
    assert result.check() == []
    line = next(c for c in result.caveats if "sat at slot" in c)
    assert "slot 1 on day 1" in line and "slot 0 on day 2" in line
    # A run with no roster change at all says nothing of the sort.
    assert not any("sat at slot" in c
                   for c in delisted(delist=False).explain("AAB", 2).caveats)


def test_the_slot_a_name_is_read_at_has_to_hold_that_name():
    """The invariant that ends the class rather than testing an instance.

    Every claim this module makes resolves the slot the same way, so all
    four agree with a wrong one and a tree built at another company's
    slot is a self-consistent description of that company under this
    name. The invariant fires on every call instead of on the roster
    shape a test happens to build.
    """
    roster = [instrument.ticker
              for instrument in tf.Universe.random(6, seed=ROSTER_SEED)]
    ex._the_slot_names_the_name(roster, 1, roster[1], "a roster")
    with pytest.raises(ValidationError, match=r"^slot 0 of a roster holds"):
        ex._the_slot_names_the_name(roster, 0, roster[1], "a roster")
    with pytest.raises(ValidationError, match=r"holds 'nothing'"):
        ex._the_slot_names_the_name(roster, 99, roster[1], "a roster")
    # And it is wired into both places a slot is resolved: a call that
    # reaches either one on a real tree passes.
    e = swapped()
    result = e.explain("AAB", 2)
    assert result._roster[result._index] == "AAB"
    assert result._before[result._previous_index] == "AAB"


def test_the_roster_comparison_is_load_bearing():
    """The guard that decides the previous slot, made able to fail.

    Reading the previous day's tape at the slot the name holds NOW is
    the defect; the comparison is what stops it. A guard that answers
    the same way whatever the rosters are gives the wrong levels back
    with no miss, so this fixes the answer rather than the machinery and
    watches the numbers move.
    """
    e = swapped()
    right = e.explain("AAB", 2)
    before = pa.table(e.truth(day=1)).to_pydict()
    theirs = rows_for(before, right._index)
    # The levels the same call would have read without the comparison,
    # which are the ones AAA left on that tape.
    assert right._previous["anchor_price"] != \
        before["anchor_price"][theirs[-1]]
    # And the tree built on those wrong levels does not add up, which is
    # what the sum identity is for.
    wrong_book = right.move - math.fsum(
        child.value for child in right.root.children
        if child.name not in ("book", "fair_value"))
    assert abs(wrong_book) < 1.0
    assert right._previous_index != right._index


#: Every shape a roster can take across the pair of days an explanation
#: reads, with what each one does to the previous close's levels. The
#: two the run log settles are the ones no comparison of rosters can
#: reach, because the day before was not kept.
ROSTER_SHAPES = (
    ("none", (0, 5), 0, False, True),
    ("none", (2, 5), 0, False, True),
    ("delist", (0, 5), 1, True, True),
    ("delist", (2, 5), 1, True, False),
    ("swap", (0, 5), 2, True, True),
    ("swap", (2, 5), 2, True, False),
)


def roster_arm(kind, keep):
    """A run that leaves the roster alone, delists a name, or delists one
    and lists another, between day 1 and day 2."""
    roster = tf.Universe.random(6, seed=ROSTER_SEED)
    e = tf.Engine(seed=ENGINE_SEED, universe=roster, model=PRESET)
    e.keep_explanations(*keep)
    e.run_days(2, record=True)
    if kind in ("delist", "swap"):
        e.delist(0)
    if kind == "swap":
        e.list_instrument(tf.Instrument("ZZZ", "technology",
                                        initial_price=50.0,
                                        shares_outstanding=1e6))
    e.run_days(2, record=True, first_day=2)
    return e


@pytest.mark.parametrize("kind,keep,ops,moved,levels", ROSTER_SHAPES)
def test_every_roster_shape_reads_the_right_levels_or_none(
        kind, keep, ops, moved, levels):
    """The previous close's levels, over every shape the roster can take.

    Where the day before was kept its roster names the slot. Where it was
    not, the RUN LOG decides: it records every listing and delisting with
    its position, so an operation between the two opens is a fact, and
    the levels are refused rather than read at a slot that may name
    another company. A width comparison cannot see a listing paired with
    a delisting, which is the fifth and sixth rows here.

    The book contribution is the assertion that bites. Read at the wrong
    slot it comes back at 2.85 against 0.0055, so a test that only
    checked which comparison ran would pass on a refusal made for the
    wrong reason.
    """
    result = roster_arm(kind, keep).explain("AAB", 2)
    assert len(result._ops) == ops
    assert result._previous_moved is moved
    assert (result._previous is not None) is levels
    assert result._compared == ("rosters" if keep[0] == 0
                                else "the run log")
    book = {c.name: c.value for c in result.root.children}["book"]
    assert abs(book) < 0.1, (kind, keep, book)
    total = math.fsum(child.value for child in result.root.children)
    assert abs(result.move - total) < ex.TOLERANCE
    assert result.check() == []


def test_a_clean_gap_is_still_explained_rather_than_refused():
    """The path where the levels are read on the weakest evidence.

    With the day before outside the window there is no roster to compare
    against, and the run log holding no listing or delisting is the whole
    of what says the slots held still. It is the only path that reads the
    previous close's levels without a roster behind it.

    Not a corner: this file's default fixture keeps days 1 and 2 and
    explains day 1, so day 0 is outside the window and most of the file
    comes through here. Reading those levels at a neighbouring slot fails
    18 tests without this one and 19 with it, measured on the current
    head by deselecting this test alone, so what this adds is a direct
    assertion where the others catch it through a sum or a replay.

    The assertion is the book contribution against the value the same day
    takes with the day before kept, to the bit. Asserting which
    comparison ran, or that the levels are merely present, passes on a
    tree that read them at another company's slot.
    """
    result = roster_arm("none", (2, 5)).explain("AAB", 2)
    kept = roster_arm("none", (0, 5)).explain("AAB", 2)
    assert result._ops == ()
    assert result._previous is not None
    assert result._previous == kept._previous
    book = {c.name: c.value for c in result.root.children}["book"]
    assert book == {c.name: c.value for c in kept.root.children}["book"]
    # A literal for `book` sat here and has been REMOVED rather than
    # re-recorded. This test is about the two arms agreeing, and the line
    # above states that; a literal beside it adds no coverage, because the
    # only way to fail it while the comparison holds is for the market to
    # have moved, which is not what this test is asking. It read
    # 0.006782659113 and then 0.006364983764 when a change to the universe
    # generator re-drew this roster, and its whole behaviour across that
    # change was to manufacture a failure that said nothing. Do not add it
    # back: pin the comparison, not the value.
    assert result.check() == []
    line = next(c for c in result.caveats if "was not kept" in c)
    assert "holds no listing or delisting between the two opens" in line
    assert "the weakest evidence any path here reads them on" in line
    # And the same run with the day before kept says nothing about it.
    assert not any("was not kept" in c for c in kept.caveats)


def test_the_run_log_names_the_operation_that_refused_the_levels():
    result = roster_arm("swap", (2, 5)).explain("AAB", 2)
    line = next(c for c in result.caveats if "has no levels" in c)
    assert "2 roster operations" in line
    assert "delist 0" in line and "list_instrument ZZZ" in line
    single = roster_arm("delist", (2, 5)).explain("AAB", 2)
    assert "1 roster operation between" in next(
        c for c in single.caveats if "has no levels" in c)


def test_a_moved_roster_is_named_in_the_caveats():
    moved = delisted()
    result = moved.explain("AAB", 0)
    assert result._roster_moved is True
    assert result._index == 1
    line = next(c for c in result.caveats if "roster held" in c)
    assert "6 names" in line and "holds 5 now" in line
    # And says nothing of the sort where the roster held still.
    assert not any("roster held" in c
                   for c in delisted(delist=False).explain("AAB", 0).caveats)


def test_a_name_listed_after_the_day_is_refused_by_name():
    roster = tf.Universe.random(6, seed=ROSTER_SEED)
    e = tf.Engine(seed=ENGINE_SEED, universe=roster, model=PRESET)
    e.keep_explanations(0, 3)
    e.run_days(2, record=True)
    e.list_instrument(tf.Instrument("ZZZ", "technology", initial_price=50.0,
                                    shares_outstanding=1e6))
    e.run_days(2, record=True, first_day=2)
    assert "ZZZ" in e.tickers
    with pytest.raises(ValidationError,
                       match=r"^'ZZZ' was not on the roster when day 0"):
        e.explain("ZZZ", 0)
    # It explains on a day it was there for.
    assert e.explain("ZZZ", 2).check() == []


def labelled(labels, keep=(1, 1), size=6):
    """A hand loop that closes through the session and records after it,
    under labels of its own choosing rather than the engine's counter."""
    roster = tf.Universe.random(size, seed=ROSTER_SEED)
    e = tf.Engine(seed=ENGINE_SEED, universe=roster, model=PRESET)
    e.keep_explanations(*keep)
    for label in labels:
        e.open_market()
        e.run_session(9, 30, 3, 390, close_at_end=True)
        e.record(label)
    return e


def test_a_day_closed_through_the_session_keeps_its_record():
    # The day is open, session, record, and the close is inside the
    # session. Stopping the day's inputs at the close dropped the record
    # out of it, and the label the record carries is what the tape keys
    # this day's rows by.
    e = labelled([0, 1, 2])
    result = e.explain(e.tickers[0], 1)
    assert [entry["op"] for entry in result._inputs] == [
        "open_market", "run_session", "record"]
    assert result._label == 1
    assert result.check() == []


def test_a_tape_label_that_is_not_the_engines_day_is_read_and_named():
    """The tape is keyed by the label, and the store by the engine's day.

    Every path the library drives passes one number to both. A hand loop
    can pass anything, and reading the tape at the store's key then
    compares this day's replay against another day's rows: four column
    misses and a close miss, all blaming the replay.
    """
    e = labelled([1, 2, 3])
    result = e.explain(e.tickers[0], 1)
    assert result._label == 2 and result.day == 1
    assert result.check() == []
    line = next(c for c in result.caveats if "recorded this day under" in c)
    assert "day 2" in line and "day 1" in line
    # The same run under labels that agree says nothing about it.
    assert not any("recorded this day under" in c
                   for c in labelled([0, 1, 2]).explain(
                       e.tickers[0], 1).caveats)


# --------------------------------------------------------------------------
# A world, and a cohort, feed the store
# --------------------------------------------------------------------------


class Buyer:
    """Buys the first name every step, so the day's inputs carry flow."""

    def act(self, observation):
        return {observation.tickers[0]: 400.0}


def world(agents=None, agent=None, keep=(1, 1), days=3):
    roster = tf.Universe.random(8, seed=ROSTER_SEED)
    w = tf.World(seed=ENGINE_SEED, universe=roster, agent=agent,
                 agents=agents, model=PRESET)
    w.engine.keep_explanations(*keep)
    w.run(days, record=True)
    return w


def test_a_world_run_feeds_the_store_and_the_day_replays():
    # Six sessions and the agent's own flow, replayed from the run log, so
    # the day the tree reads is the day the agent traded rather than a
    # default one. A replay that dropped the flow would leave
    # order_flow_impact at zero and the sum would miss the move.
    w = world(agent=Buyer())
    result = w.engine.explain(w.engine.tickers[0], 1)
    assert [e["op"] for e in result._inputs] == (
        ["open_market"] + ["run_session"] * 6
        + ["record", "close_market"])
    flow = next(child for child in result.root.children
                if child.name == "order_flow_impact")
    assert flow.value != 0.0
    assert result.check() == []


def test_a_cohort_world_feeds_the_store_too():
    w = world(agents={"a": Buyer(), "b": Buyer()})
    assert w.is_cohort
    result = w.engine.explain(w.engine.tickers[0], 1)
    assert result.check() == []


# --------------------------------------------------------------------------
# The mechanism table names Rust that exists and reads what it says
# --------------------------------------------------------------------------


def rust_file(path: str) -> Path:
    """The file a mechanism's Rust path names.

    ``a::b::c`` is ``rust/src/a/b.rs`` and ``a::Type::c`` is
    ``rust/src/a.rs``, which is the layout every path in the table has.
    """
    parts = path.split("::")[:-1]
    if parts and parts[-1][:1].isupper():
        parts = parts[:-1]
    return RUST.joinpath(*parts[:-1], parts[-1] + ".rs")


def body(path: str) -> str:
    """One Rust function's body, by brace counting from its signature."""
    text = rust_file(path).read_text(encoding="utf-8")
    leaf = path.split("::")[-1]
    match = re.search(r"^\s*(?:pub(?:\(crate\))? )?fn " + leaf + r"\(",
                      text, re.MULTILINE)
    assert match, f"no fn {leaf} in {rust_file(path).name}"
    start, depth = text.index("{", match.start()), 0
    for k in range(start, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return text[match.start():k + 1]
    raise AssertionError(f"unbalanced braces after fn {leaf}")


def sources(mech) -> str:
    return "\n".join(body(name)
                     for name in (mech.function,) + tuple(mech.via))


def test_the_table_covers_every_contribution_once():
    assert len(ex.MECHANISMS) == len(ex.CONTRIBUTIONS) == 11
    assert len({m.factor for m in ex.MECHANISMS}) == 11


def test_every_mechanism_names_a_rust_function_that_exists():
    for mech in ex.MECHANISMS:
        for name in (mech.function,) + tuple(mech.via):
            assert rust_file(name).exists(), name
            assert body(name), name


#: The dials and the state each contribution declares, written out here
#: and hand-verified against the Rust named on the mechanism. A second
#: copy, deliberately: four of the eleven name the same 505-line tick
#: function, which reads 108 of the model params, so "the name appears in
#: the named function" accepts any dial for any of those four and cannot
#: see reversion and momentum swapping theirs. This table can, because it
#: is per contribution. It has to be edited with the module's own.
EXPECTED = {
    "reversion": (("s_phi_tick",), ("mispricing_s",)),
    "momentum": (("momentum_theta",), ("mispricing_momentum",)),
    "crowd_lean": (("crowd_valuation_gain", "crowd_momentum_gain",
                    "crowd_lean_cap", "forced_flow_gain",
                    "forced_flow_threshold", "forced_flow_beta_exponent"),
                   ("mispricing_s", "mispricing_momentum", "beta")),
    "company_news": (("news_sector_weight", "news_market_weight",
                      "news_peer_weight", "news_peer_weight_down",
                      "news_peer_vix_coupling", "endogenous_news_intensity",
                      "endogenous_news_sigma"), ()),
    "order_flow_impact": (("order_flow_coefficient",
                           "informed_flow_fraction"), ("avg_volume",)),
    "short_squeeze_effect": ((), ("short_interest", "last_daily_return")),
    "random_noise": (("idio_sigma_scale", "idio_sigma_beta_exponent",
                      "sector_loading", "sector_loading_beta_slope",
                      "market_factor_sigma", "market_beta_down_asym",
                      "crisis_blend_source", "crisis_blend_gain",
                      "crash_amplifier_slope", "crash_amplifier_threshold"),
                     ("garch_variance", "beta", "market_cap")),
    "circuit_breaker": (("price_breaker_fraction", "price_hard_cap"),
                        ("price",)),
    "jump": (("jump_intensity_market", "jump_intensity_idio",
              "jump_mean_market", "jump_sigma_market", "jump_sigma_idio",
              "jump_vix_coupling", "jump_momentum_share",
              "market_vol_vix_anchor"),
             ("mispricing_s", "mispricing_s_prev_close")),
    "fair_value": (("fair_value_book_floor", "qe_pe_gain",
                    "qe_pe_stock_gain"), ()),
    "book": ((), ("price",)),
}


def test_each_contribution_declares_the_dials_and_state_it_is_meant_to():
    # Exact lists, in order, so a dial moving from one contribution to
    # another fails here even though both name the same Rust function.
    assert sorted(EXPECTED) == sorted(m.factor for m in ex.MECHANISMS)
    for mech in ex.MECHANISMS:
        dials, state = EXPECTED[mech.factor]
        assert mech.dials == dials, mech.factor
        assert mech.state == state, mech.factor


def test_every_declared_dial_is_a_model_param_that_its_rust_reads():
    dials = set(tf.Engine(seed=1, universe=tf.Universe.random(2, seed=1),
                          model=PRESET).model_params)
    declared = 0
    for mech in ex.MECHANISMS:
        text = sources(mech)
        for name in mech.dials:
            assert name in dials, (mech.factor, name)
            # `out.` and `self.` because the params constructor is where
            # a derived band's own dial is read, and that is the honest
            # place to look for it.
            assert re.search(r"\b(?:params|p|out|self)\." + name + r"\b",
                             text), (mech.factor, name, mech.function)
            declared += 1
    # Exact, not a floor. A floor let nine of random_noise's ten dials be
    # dropped and still pass, because the table declares more than the
    # floor asked for.
    assert declared == 40
    assert sum(1 for m in ex.MECHANISMS if m.dials) == 9


def test_every_declared_state_field_is_a_column_that_its_rust_reads():
    e = tf.Engine(seed=1, universe=tf.Universe.random(2, seed=1))
    declared = 0
    for mech in ex.MECHANISMS:
        text = sources(mech)
        for name in mech.state:
            e.column(name)
            assert re.search(r"\b" + name + r"\b", text), (mech.factor, name)
            declared += 1
    assert declared == 15
    assert sum(1 for m in ex.MECHANISMS if m.state) == 9


def test_every_declared_macro_field_is_a_macro_field_that_its_rust_reads():
    fields = set(tf.Engine(seed=1, universe=tf.Universe.random(2, seed=1))
                 .macro_fields)
    declared = 0
    for mech in ex.MECHANISMS:
        text = sources(mech)
        for name in mech.macro:
            assert name in fields, (mech.factor, name)
            assert re.search(r"\b" + name + r"\b", text), (mech.factor, name)
            declared += 1
    # Five: the VIX under the crowd's forced flow, under the jump's
    # intensity and under the book's spread, and the QE boost under the
    # valuation. The news channel reads the crisis spike rather than the
    # VIX itself, so it declares none.
    assert declared == 5


def test_every_declared_site_is_in_the_draw_schedule():
    declared = 0
    for mech in ex.MECHANISMS:
        for stream, site, scope in mech.sites:
            assert stream in noise.STREAMS, (mech.factor, stream)
            assert site in noise.SITES[stream], (mech.factor, site)
            assert scope in ("company", "sector", "market"), scope
            declared += 1
    assert declared == 10
    assert {m.factor for m in ex.MECHANISMS if m.sites} == {
        "company_news", "random_noise", "jump", "book"}


def test_the_state_and_macro_names_do_not_collide():
    # A state node is keyed by name inside one mechanism, so a field that
    # is both a column and a macro field would give two nodes one path.
    e = tf.Engine(seed=1, universe=tf.Universe.random(2, seed=1))
    assert not set(ex._STATE_FIELDS) & set(e.macro_fields)


def test_the_kinds_are_the_declared_five():
    _, result = one()
    assert {node.kind for _, node, _ in result._walk} == set(ex.KINDS)


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def test_the_json_carries_the_whole_tree():
    _, result = one()
    doc = json.loads(result.to_json())
    assert sorted(doc) == ["caveats", "day", "move", "root", "ticker"]
    assert doc["ticker"] == result.ticker
    assert doc["day"] == result.day
    assert doc["move"] == result.move
    assert doc["caveats"] == result.caveats
    assert ex._node_from_json(doc["root"]) == result.root


def test_the_json_round_trips_through_a_second_pass():
    _, result = one()
    once = result.to_json()
    doc = json.loads(once)
    assert json.dumps(doc, sort_keys=True, indent=2) == once


#: The Arrow columns, written out here rather than read from the module.
#: Compared against `Explanation.COLUMNS`, a column dropped from the
#: declaration disappears from both sides of the comparison and the test
#: passes over a narrower table than the one it exists to pin.
ARROW_COLUMNS = [
    ("path", "string"), ("parent", "string"), ("depth", "int64"),
    ("kind", "string"), ("name", "string"), ("value", "float64"),
    ("stream", "string"), ("draws", "int64"), ("first_index", "int64"),
    ("inputs", "string"),
]


def test_the_arrow_table_has_the_declared_columns():
    _, result = one()
    table = result.to_arrow()
    assert list(ex.Explanation.COLUMNS) == [tuple(c) for c in ARROW_COLUMNS]
    assert table.schema.names == [name for name, _ in ARROW_COLUMNS]
    for name, kind in ARROW_COLUMNS:
        assert table.schema.field(name).type == getattr(pa, kind)()
    assert table.num_rows == len(result._walk)
    # One metadata entry, because arrow-schema serialises the map in
    # iteration order at the boundary and two keys give unstable bytes.
    assert list(table.schema.metadata) == [b"caveats"]


def test_the_arrow_rows_rebuild_the_tree():
    _, result = one()
    table = result.to_arrow().to_pydict()
    parents = dict(zip(table["path"], table["parent"]))
    roots = [p for p, parent in parents.items() if parent == ""]
    assert roots == [result.ticker]
    assert all(parent in parents for parent in parents.values()
               if parent != "")
    draws = {p: n for p, n in zip(table["path"], table["draws"])}
    total = sum(n for p, n in draws.items() if p.count(".") == 3)
    assert total == len(ex._addresses(result.root))


def test_render_names_the_counterfactual_it_cannot_do():
    _, result = one()
    text = result.render(depth=1)
    assert "tradefloor.noise.attribute" in text
    assert result.ticker in text.splitlines()[0]
    # depth=1 shows the move and its contributions and no mechanism.
    kinds = [line.split()[0] for line in text.splitlines()[1:]
             if not line.startswith("  caveat")]
    assert kinds[0] == "move"
    assert set(kinds[1:]) == {"factor"}
    assert len(kinds) == 1 + len(ex.CONTRIBUTIONS)


def test_render_says_the_book_is_not_split_without_the_print_table():
    _, result = one()
    text = result.render()
    if HAS_PRINTS:
        assert "splits three ways under it" in text
        assert "three readings that add to it" in text
    else:
        assert "no Engine.prints()" in text


# --------------------------------------------------------------------------
# The caveats are computed
# --------------------------------------------------------------------------


def test_the_caveats_are_computed_from_the_call():
    _, result = one()
    # The whole clause, not the bare digit: several caveats carry day
    # numbers, so `4 in any caveat` passes on almost any tree.
    assert any(f"the {len(result._inputs)} inputs the run log holds"
               in c for c in result.caveats), result.caveats
    assert any("day 0's" in c for c in result.caveats)
    other = one(day=2)[1]
    assert any("day 1's" in c for c in other.caveats)
    assert result.caveats != other.caveats


def test_no_measured_number_is_hardcoded_in_the_module():
    """A caveat with a number typed into it goes on printing that number
    after the thing it describes has moved.

    Two scans, because one is not enough. A float LITERAL anywhere in the
    module is a coefficient the module should be reading; and a decimal
    inside a string the caveats build is a measurement retyped, which the
    literal scan cannot see because it is text.
    """
    source = (ROOT / "python" / "tradefloor" / "explain.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    allowed = {1e-12, 0.0}
    literals = [node.value for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, float)
                and node.value not in allowed]
    assert not literals, literals
    caveats = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef)
                   and node.name == "_caveats")
    typed = [node.value for node in ast.walk(caveats)
             if isinstance(node, ast.Constant)
             and isinstance(node.value, str)
             and re.search(r"\d+\.\d", node.value)]
    assert not typed, typed


def resolves(sha: str) -> bool:
    """Whether ``sha`` names a commit this checkout can reach.

    A checkout at depth one holds one commit, which is what CI does, so a
    citation that is a real ancestor does not resolve until the clone is
    deepened. Deepened here rather than skipped, because a provenance
    guard that stands down on the machine that runs it every day is a
    guard that never runs.
    """
    def cat() -> bool:
        # An ancestor of HEAD, not merely an object the repository still
        # holds. After a rebase the old commit is still present, reachable
        # from the reflog, and `cat-file -e` reports it as if the citation
        # were live.
        return subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, "HEAD"],
            cwd=ROOT, capture_output=True).returncode == 0

    if cat():
        return True
    shallow = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"], cwd=ROOT,
        capture_output=True, text=True).stdout.strip()
    if shallow != "true":
        return False
    subprocess.run(["git", "fetch", "--quiet", "--unshallow"], cwd=ROOT,
                   capture_output=True)
    return cat()


def test_every_commit_this_module_cites_is_reachable():
    """A measured number names the commit it was measured on, and a
    rebase makes that name point at nothing.

    The module's cost table cites a commit. This branch is rebased before
    it merges, so the citation has to be re-pointed at a commit that
    survived, and a citation nobody checks survives as a plausible hex
    string. Read from the repository rather than from a list here.
    """
    source = (ROOT / "python" / "tradefloor" / "explain.py").read_text(
        encoding="utf-8")
    cited = set(re.findall(r"\bat ([0-9a-f]{7,40})\b", source))
    assert cited, "the cost table cites no commit, so nothing is provenanced"
    dead = [sha for sha in sorted(cited) if not resolves(sha)]
    assert not dead, (
        f"{', '.join(dead)} name no commit this checkout can reach. A "
        "rebase moved them, and a measurement has to name a commit that "
        "survived it.")


def test_a_caveat_names_the_mechanisms_with_no_draw_of_their_own():
    _, result = one()
    silent = {m.factor for m in ex.MECHANISMS if not m.sites}
    line = next(c for c in result.caveats if "no draw leaf" in c)
    for name in silent:
        assert name in line, name
    for name in {m.factor for m in ex.MECHANISMS if m.sites}:
        assert name not in line, name


# --------------------------------------------------------------------------
# The MCP tool
# --------------------------------------------------------------------------


def mcp_module():
    pytest.importorskip("mcp", reason="the MCP server is an opt-in extra")
    from tradefloor import mcp

    return mcp


def test_the_mcp_tool_returns_a_checked_tree():
    mcp = mcp_module()
    out = mcp.explain(universe_size=8, day=1)
    assert out["ok"] is True
    assert out["checked"]["misses"] == []
    assert out["checked"]["nodes"] == (55 if HAS_PRINTS else 53)
    shown = [c["name"] for c in out["tree"]["children"]]
    assert shown == list(ex.CONTRIBUTIONS)
    total = math.fsum(c["value"] for c in out["tree"]["children"])
    assert abs(out["move"] - total) < ex.TOLERANCE


def test_the_mcp_tool_result_is_bounded_and_says_what_it_trimmed():
    """A tool result is read by a model, so its size is a design choice.

    The addresses are the bulk of the tree and they do not shrink with
    the roster: one name's day carries the same few thousand at every
    market size, which was 88 KB of a 97 KB result. The tool samples them
    and reports both counts, so the number of addresses is still readable
    where the addresses themselves are not.
    """
    mcp = mcp_module()
    small = mcp.explain(universe_size=8, day=1)
    big = mcp.explain(universe_size=40, day=1)
    for out in (small, big):
        assert len(json.dumps(out)) < 40_000
        assert out["checked"]["addresses"] == 2736
        assert out["checked"]["addresses_shown"] < 100
        assert str(out["checked"]["addresses_shown"]) not in ("0",)
        assert "at most" in out["reading_note"]
    # The count does not move with the roster; the tree it came from does
    # not either, which is the point the reading note makes.
    assert small["checked"]["addresses"] == big["checked"]["addresses"]
    deepest = small["tree"]["children"][6]["children"][0]["children"]
    drawn = [c for c in deepest if c["kind"] == "draw"]
    assert drawn, deepest
    for child in drawn:
        assert len(child["addresses"]) <= mcp.ADDRESS_SAMPLE
        assert child["inputs"]["count"] >= len(child["addresses"])


def test_the_mcp_tool_is_read_only_and_repeatable():
    mcp = mcp_module()
    first = json.dumps(mcp.explain(universe_size=8, day=1), sort_keys=True)
    second = json.dumps(mcp.explain(universe_size=8, day=1), sort_keys=True)
    assert first == second


def test_the_mcp_tool_exposes_no_replay():
    # A replay runs engines, and a tool call answers inside a
    # conversation. The result is data, so it carries no callable.
    mcp = mcp_module()
    out = mcp.explain(universe_size=8, day=1)
    assert "replay" not in out
    json.dumps(out)


def test_the_mcp_tool_caveats_are_computed_and_carry_the_explanation():
    mcp = mcp_module()
    out = mcp.explain(universe_size=8, day=1)
    assert any("noise.attribute" in c for c in out["caveats"])
    assert any("measured by running day 1 again" in c
               for c in out["caveats"])
    small = mcp.explain(universe_size=8, day=1)["caveats"]
    big = mcp.explain(universe_size=40, day=1)["caveats"]
    assert any("8-name roster" in c for c in small)
    assert not any("8-name roster" in c for c in big)


def test_the_mcp_tool_refuses_a_day_past_its_cap():
    mcp = mcp_module()
    out = mcp.explain(day=10_000)
    assert out["ok"] is False
    assert "day must be" in out["error"]


def test_the_mcp_tool_refuses_an_unknown_ticker_with_the_roster():
    mcp = mcp_module()
    out = mcp.explain(ticker="NOPE", universe_size=8, day=1)
    assert out["ok"] is False
    assert "unknown ticker" in out["error"]
