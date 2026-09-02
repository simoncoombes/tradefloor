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
    assert [child.name for child in result.root.children] == list(ex.FACTORS)
    assert list(ex.FACTORS)[:9] == list(tf.Engine.FACTORS)
    assert list(ex.FACTORS)[9:] == ["fair_value", "book"]
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
    # check() reports three families of miss at once: the sum, every
    # node's replay, and the replay against the tape the run recorded.
    _, result = one()
    assert result.check() == []


def test_a_leaf_replays_to_its_parent():
    _, result = one()
    leaves = [(path, node) for path, node, _ in result._walk
              if node.kind == "draw"]
    assert len(leaves) >= 7, [path for path, _ in leaves]
    for path, leaf in leaves:
        parent = result._by_path[path.rsplit(".", 1)[0]]
        assert parent.kind == "mechanism"
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
    base = result._run(())
    same = result._run((noise.Patch(address, result._values[address]),))
    assert same.move == base.move
    moved = result._run((noise.Patch(address, result._values[address] + 8.0),))
    assert moved.move != base.move
    assert abs(moved.factors["random_noise"]
               - base.factors["random_noise"]) > 1e-9


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


def test_an_unknown_ticker_is_refused_with_the_roster():
    e = engine()
    with pytest.raises(ValidationError, match=r"^'NOPE' is not in this"):
        e.explain("NOPE", 1)


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
    assert len(result._inputs) >= 8, [e["op"] for e in result._inputs]
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
    assert len(ex.MECHANISMS) == len(ex.FACTORS) == 11
    assert len({m.factor for m in ex.MECHANISMS}) == 11


def test_every_mechanism_names_a_rust_function_that_exists():
    for mech in ex.MECHANISMS:
        for name in (mech.function,) + tuple(mech.via):
            assert rust_file(name).exists(), name
            assert body(name), name


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
    # A count as an invariant, so a table that lost its dials cannot pass
    # by declaring none: eight of the eleven read at least one dial and
    # the table names thirty-one in all.
    assert declared >= 31
    assert sum(1 for m in ex.MECHANISMS if m.dials) >= 8


def test_every_declared_state_field_is_a_column_that_its_rust_reads():
    e = tf.Engine(seed=1, universe=tf.Universe.random(2, seed=1))
    declared = 0
    for mech in ex.MECHANISMS:
        text = sources(mech)
        for name in mech.state:
            e.column(name)
            assert re.search(r"\b" + name + r"\b", text), (mech.factor, name)
            declared += 1
    assert declared >= 13
    assert sum(1 for m in ex.MECHANISMS if m.state) >= 8


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
    assert len(kinds) == 1 + len(ex.FACTORS)


def test_render_says_the_book_is_not_split_without_the_print_table():
    _, result = one()
    text = result.render()
    if hasattr(tf.Engine, "prints"):
        assert "absorbed and clamp" in text
    else:
        assert "no Engine.prints()" in text


# --------------------------------------------------------------------------
# The caveats are computed
# --------------------------------------------------------------------------


def test_the_caveats_are_computed_from_the_call():
    _, result = one()
    assert any(str(len(result._inputs)) in c for c in result.caveats)
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
    assert out["checked"]["nodes"] > 30
    assert [c["name"] for c in out["tree"]["children"]] == list(ex.FACTORS)
    total = math.fsum(c["value"] for c in out["tree"]["children"])
    assert abs(out["move"] - total) < ex.TOLERANCE


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
