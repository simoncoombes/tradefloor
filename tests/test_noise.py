"""Draw addressing and the patching layer (noise, phase 1).

Every claim the module makes is stated here as a test. Inertness is the
known-answer digest's to prove (`test_known_answer.py`); what this file
proves is that the overlay substitutes without moving the stream, that
the log records what the consumer received, that a patch on a fork stays
on the fork, that a snapshot carries the overlay, and that the site
sequence the log records is the schedule the module documents.
"""

import struct

import pytest

import tradefloor as tf
from tradefloor import noise
from tradefloor.noise import DrawAddress, Patch

SEED = 42
UNIVERSE = tf.Universe.random(12, seed=111)
TICKS = 78


def fresh(*, seed=SEED, model=None):
    kwargs = {} if model is None else {"model": model}
    return tf.Engine(seed=seed, universe=UNIVERSE, **kwargs)


def run(engine, days, *, first=0, trace=()):
    for stream in trace:
        engine.trace_draws(stream, first, first + days - 1)
    engine.run_days(days, hour=9, minute=30, day_of_week=3,
                    ticks_per_day=TICKS, volatility=1.0, record=True,
                    first_day=first)
    return engine


def prices(engine):
    raw = engine.prices()
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def columns(engine):
    out = state(engine)
    out.pop("draws")
    out.pop("draws_by_stream")
    return out


def state(engine):
    n = len(engine.tickers)
    out = {}
    for field in ("price", "previous_close", "volume", "mispricing_s",
                  "garch_variance"):
        out[field] = struct.unpack("<%dd" % n, engine.column(field))
    out["draws"] = engine.draws_consumed
    out["draws_by_stream"] = engine.draws_by_stream()
    return out


# -- the empty overlay -------------------------------------------------------

def test_an_empty_overlay_and_a_trace_leave_the_market_bit_identical():
    """The known-answer digest proves the code change is inert; this
    proves the instruments are: patching nothing and tracing every stream
    reproduce the untouched run bit for bit, draw counts included."""
    plain = run(fresh(), 3)
    instrumented = fresh()
    noise.patch_draws(instrumented, [])
    run(instrumented, 3, trace=noise.STREAMS)
    assert state(instrumented) == state(plain)
    assert instrumented.stream_positions() == plain.stream_positions()


# -- one patched uniform -----------------------------------------------------

def test_one_patched_uniform_moves_one_log_entry_and_no_draw_count():
    """Substitute, never skip. The draws by stream are identical, every
    other logged value is identical, and exactly one entry differs: the
    patched address, at the patched value."""
    day = 1
    plain = run(fresh(), 3, trace=("jumps",))
    patched = fresh()
    address = DrawAddress("jumps", "uniform", 5)
    noise.patch_draws(patched, [Patch(address, 0.999)])
    run(patched, 3, trace=("jumps",))

    assert patched.draws_by_stream() == plain.draws_by_stream()
    assert patched.stream_positions() == plain.stream_positions()
    a = noise.draw_log(plain, "jumps", 0, 2)
    b = noise.draw_log(patched, "jumps", 0, 2)
    assert len(a) == len(b) > 0
    changed = [(x, y) for x, y in zip(a, b) if x != y]
    assert len(changed) == 1, changed
    before, after = changed[0]
    assert before.address == address == after.address
    assert after.value == 0.999
    assert before.value != 0.999
    assert (before.day, before.site, before.tag) == (after.day, after.site,
                                                     after.tag)


def test_a_patched_normal_leaves_the_next_normal_untouched():
    """A patched normal substitutes the value returned and nothing else:
    the Box-Muller spare is computed from the raw uniforms, so the normal
    after the patched one is the generator's own."""
    plain = run(fresh(), 2, trace=("volume_idio",))
    patched = fresh()
    address = DrawAddress("volume_idio", "normal", 2)
    noise.patch_draws(patched, [Patch(address, 3.5)])
    run(patched, 2, trace=("volume_idio",))
    a = noise.draw_log(plain, "volume_idio", 0, 1)
    b = noise.draw_log(patched, "volume_idio", 0, 1)
    assert [x.address for x in a] == [x.address for x in b]
    for x, y in zip(a, b):
        if x.address == address:
            assert y.value == 3.5
        else:
            assert x.value == y.value


# -- forks -------------------------------------------------------------------

def test_a_patch_applied_through_a_fork_does_not_reach_the_parent():
    parent = run(fresh(), 2)
    parent.trace_draws("news", 2, 2)
    child, = parent.fork(1)
    noise.patch_draws(child, [Patch(DrawAddress("news", "uniform", 0), 0.0)])
    assert child.draw_patches() != []
    assert parent.draw_patches() == []
    run(child, 1, first=2)
    run(parent, 1, first=2)
    assert parent.draw_patches() == []


def test_run_day_with_returns_the_day_and_leaves_the_parent_where_it_was():
    parent = run(fresh(), 2)
    before = state(parent)
    address = DrawAddress("jumps", "uniform", 2 * (len(UNIVERSE) + 1))
    result = noise.run_day_with(parent, 2, [Patch(address, 0.0)],
                                ticks_per_day=TICKS)
    assert state(parent) == before
    assert result.day == 2
    assert set(result.closes) == set(parent.tickers)
    logged = result.log("jumps")
    assert logged and all(entry.day == 2 for entry in logged)
    assert any(entry.address == address and entry.value == 0.0
               for entry in logged)
    # the truth table is the day's: one batch, rows for every name
    table = result.truth
    assert table is not None


# -- snapshots ---------------------------------------------------------------

def test_a_snapshot_taken_with_an_overlay_restores_with_the_overlay():
    """A restored engine continues under the same substitutions as the one
    it copied, and its addresses continue from where the copy's did."""
    source = fresh()
    address = DrawAddress("jumps", "uniform", 3 * (len(UNIVERSE) + 1) + 1)
    noise.patch_draws(source, [Patch(address, 0.0)])
    run(source, 2)
    snapshot = source.state_snapshot()
    assert "draw_overlay" in snapshot and "draw_counts" in snapshot

    restored = fresh()
    restored.restore_state(snapshot)
    assert restored.draw_patches() == source.draw_patches()
    assert restored.stream_positions() == source.stream_positions()

    run(source, 3, first=2)
    run(restored, 3, first=2)
    # The draw counters restart on a restored engine, which test_forking
    # pins; the market, the positions and the overlay do not.
    assert columns(restored) == columns(source)
    assert restored.stream_positions() == source.stream_positions()


def test_a_snapshot_without_the_new_keys_still_restores():
    """A snapshot written before draw addressing carries neither key; it
    restores with counts of zero and no overlay, and says nothing else."""
    source = run(fresh(), 2)
    snapshot = source.state_snapshot()
    snapshot.pop("draw_overlay")
    snapshot.pop("draw_counts")
    restored = fresh()
    restored.restore_state(snapshot)
    assert restored.draw_patches() == []
    assert all(pos == (0, 0) for pos in restored.stream_positions().values())
    run(source, 1, first=2)
    run(restored, 1, first=2)
    assert prices(restored) == prices(source)


# -- the schedule ------------------------------------------------------------

def test_the_site_sequence_is_the_schedule():
    """Walk one day and assert the sequence of sites per stream. This is
    the draw schedule made legible; a mechanism that adds or moves a draw
    changes it, which is the point of pinning it."""
    n = len(UNIVERSE)
    model = tf.ModelParams.from_preset(
        "pt-v16", jump_intensity_market=0.5, jump_intensity_idio=0.5,
        endogenous_news_intensity=0.5, volume_idio_sigma=0.2,
        volume_innovation_sigma=0.2)
    engine = fresh(model=model)
    run(engine, 1, trace=noise.STREAMS)
    sites = {s: [e.site for e in noise.draw_log(engine, s, 0, 0)]
             for s in noise.STREAMS}
    assert sites["news"] == ["news_u", "news_z"] * n
    assert sites["jumps"] == (["jump_market_u", "jump_market_z"]
                              + ["jump_company_u", "jump_company_z"] * n)
    assert sites["volume"] == ["volume_z"]
    assert sites["volume_idio"] == ["volume_idio_z"] * n
    assert sites["external"] == []
    economy = sites["economy"]
    assert economy and set(economy) <= {"economy_daily", "economy_cycle",
                                        "central_bank"}
    assert economy[0] == "economy_daily"
    market = sites["market"]
    sectors = engine.day_marks()[0]["sectors"]
    per_tick = (["market_factor_z"] + ["sector_z"] * sectors
                + ["factor_idio_z", "stash_u"] * n + ["settle_u"] * 4 * n)
    assert len(market) == len(per_tick) * TICKS
    assert market[:len(per_tick)] == per_tick
    assert market == per_tick * TICKS
    assert "unset" not in market
    kinds = [e.address.kind for e in noise.draw_log(engine, "market", 0, 0)]
    assert kinds[:len(per_tick)] == (["normal"] * (1 + sectors)
                                     + ["normal", "uniform"] * n
                                     + ["uniform"] * 4 * n)


def test_the_layout_matches_the_log():
    """The day mark's arithmetic names the same market normals the log
    records for each company."""
    engine = fresh()
    run(engine, 2, trace=("market",))
    layout = noise.market_day_layout(engine, 1)
    logged = {}
    for entry in noise.draw_log(engine, "market", 1, 1):
        if entry.site == "factor_idio_z":
            logged.setdefault(entry.tag, []).append(entry.address.index)
    assert set(layout) == set(logged)
    for company, (first, stride, ticks) in layout.items():
        assert ticks == TICKS
        assert logged[company] == [first + t * stride for t in range(ticks)]


def test_addresses_and_kinds_are_checked():
    with pytest.raises(ValueError):
        DrawAddress("weather", "uniform", 0).check()
    with pytest.raises(ValueError):
        DrawAddress("market", "gamma", 0).check()
    with pytest.raises(ValueError):
        DrawAddress("market", "normal", -1).check()
    engine = fresh()
    with pytest.raises(ValueError):
        noise.market_day_layout(engine, 5)


# -- the day a draw carries --------------------------------------------------

def test_every_stream_and_the_marks_carry_the_day_run_days_was_given():
    """`run_days(first_day=K)` numbers one day one way.

    The open pushes the day mark and takes the day's endogenous news draws,
    so a day stamped after the open left the mark and the news on the
    engine's own counter while every other stream carried K. A run of three
    days from 100 logged news on 0, 1 and 2 and everything else on 100, 101
    and 102, and `market_day_layout(100)` found nothing.
    """
    engine = fresh()
    for stream in noise.STREAMS:
        engine.trace_draws(stream, -1000, 1000)
    engine.run_days(3, hour=9, minute=30, day_of_week=3, ticks_per_day=TICKS,
                    volatility=1.0, record=True, first_day=100)
    for stream in noise.STREAMS:
        log = engine.draw_log(stream, -1000, 1000)
        if stream == "external":
            assert log == []
            continue
        assert sorted({e[2] for e in log}) == [100, 101, 102], stream
    assert [m["day"] for m in engine.day_marks()] == [100, 101, 102]
    for day in (100, 101, 102):
        assert engine.market_day_layout(day) is not None
    assert engine.market_day_layout(0) is None
    # the range filter names the days the run used
    assert engine.draw_log("news", 100, 102) == \
        engine.draw_log("news", -1000, 1000)


def test_two_runs_in_a_row_leave_every_stream_on_the_same_days():
    """Reachable without `first_day`: the counter advanced across the two
    calls while `first_day` restarted, so the news stream numbered four days
    and the rest numbered two, twice."""
    engine = fresh()
    for stream in noise.STREAMS:
        engine.trace_draws(stream, -1000, 1000)
    for _ in range(2):
        engine.run_days(2, hour=9, minute=30, day_of_week=3,
                        ticks_per_day=TICKS, volatility=1.0, record=True)
    seen = {}
    for stream in noise.STREAMS:
        log = engine.draw_log(stream, -1000, 1000)
        if stream == "external":
            continue
        seen[stream] = sorted({e[2] for e in log})
    assert set(map(tuple, seen.values())) == {(0, 1)}, seen
    # the marks agree with the streams: two days numbered 0 and two
    # numbered 1, because both calls were given the same first_day
    assert [m["day"] for m in engine.day_marks()] == [0, 1, 0, 1]


def test_a_restore_drops_the_marks_of_the_run_it_replaced():
    """The marks name the days THIS engine opened. Kept across a restore
    they named days the restored engine never ran: two days, then a
    three-day snapshot, then two more reported 0, 1, 3 and 4."""
    source = fresh()
    source.run_days(3, hour=9, minute=30, day_of_week=3, ticks_per_day=TICKS,
                    volatility=1.0, record=False, first_day=0)
    snapshot = source.state_snapshot()
    other = fresh()
    other.run_days(2, hour=9, minute=30, day_of_week=3, ticks_per_day=TICKS,
                   volatility=1.0, record=False, first_day=0)
    assert [m["day"] for m in other.day_marks()] == [0, 1]
    other.restore_state(snapshot)
    assert other.day_marks() == []
    other.run_days(2, hour=9, minute=30, day_of_week=3, ticks_per_day=TICKS,
                   volatility=1.0, record=False, first_day=3)
    assert [m["day"] for m in other.day_marks()] == [3, 4]
    assert other.market_day_layout(0) is None


def test_the_market_log_length_is_the_schedule():
    """The log's memory is quoted per day in `rng.rs`, and the draw count it
    rests on is a schedule quantity rather than a benchmark."""
    engine = fresh()
    engine.trace_draws("market", -1000, 1000)
    engine.run_days(1, hour=9, minute=30, day_of_week=3, ticks_per_day=TICKS,
                    volatility=1.0, record=False, first_day=0)
    mark = engine.day_marks()[0]
    names = len(mark["active"])
    sectors = mark["sectors"]
    # per tick: one market factor normal, one normal per sector, one normal
    # and one uniform per name, and settlement's four uniforms per name
    per_tick = 1 + sectors + names + names + 4 * names
    assert len(engine.draw_log("market", -1000, 1000)) == TICKS * per_tick
