"""Vectorised engines."""

import struct

import pytest

import pretium

UNIVERSE = pretium.Universe.random(8, seed=5)


def grid(buf, rows, cols):
    values = struct.unpack("<%dd" % (rows * cols), buf)
    return [list(values[r * cols:(r + 1) * cols]) for r in range(rows)]


def flat(buf, n):
    return list(struct.unpack("<%dd" % n, buf))


def batch(seeds=range(8), ticks=200):
    b = pretium.EngineBatch(seeds=list(seeds), universe=UNIVERSE)
    b.open_market()
    b.run_session(9, 30, 3, ticks)
    return b


# --------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------

def test_every_member_equals_that_seed_run_alone():
    """Per-seed isolation, asserted rather than assumed.

    If a batch member differed from the same seed run on its own, batching
    would be a different simulation wearing the same name -- and the
    difference would surface as irreproducible results long after anyone
    remembered which path they took.
    """
    seeds = [0, 1, 2, 3, 4]
    b = batch(seeds)
    rows = grid(b.prices(), *b.shape)
    for i, seed in enumerate(seeds):
        alone = pretium.Engine(seed=seed, universe=UNIVERSE)
        alone.open_market()
        alone.run_session(9, 30, 3, 200)
        assert flat(alone.prices(), len(UNIVERSE)) == rows[i], f"seed {seed}"


def test_members_do_not_interact():
    # Each engine owns its own generator. There is no decomposition WITHIN a
    # run that preserves the draw schedule, which is why per-seed is the only
    # safe boundary.
    small = batch([0, 1])
    large = batch([0, 1, 2, 3, 4, 5])
    assert grid(small.prices(), *small.shape)[0] == grid(large.prices(), *large.shape)[0]


def test_members_are_ordered_by_seed_position():
    seeds = [7, 3, 11, 1]
    b = batch(seeds)
    assert b.seeds == seeds
    rows = grid(b.prices(), *b.shape)
    for i, seed in enumerate(seeds):
        alone = pretium.Engine(seed=seed, universe=UNIVERSE)
        alone.open_market()
        alone.run_session(9, 30, 3, 200)
        assert flat(alone.prices(), len(UNIVERSE)) == rows[i]


def test_different_seeds_give_different_markets():
    b = batch(range(8))
    rows = grid(b.prices(), *b.shape)
    assert len({tuple(r) for r in rows}) == 8


# --------------------------------------------------------------------------
# Shape and output
# --------------------------------------------------------------------------

def test_the_grid_is_members_by_instruments_row_major():
    # One member per row, so a vectorised policy reads a market's
    # cross-section contiguously -- the direction it actually consumes.
    b = batch(range(4))
    assert b.shape == (4, len(UNIVERSE))
    assert len(b) == 4
    assert len(b.prices()) == 4 * len(UNIVERSE) * 8


def test_columns_have_the_same_shape_as_prices():
    b = batch(range(3))
    for field in ("price", "volume", "mispricing_s", "market_cap"):
        assert len(b.column(field)) == len(b.prices())


def test_an_unknown_column_is_refused():
    b = batch(range(2))
    with pytest.raises(pretium.ValidationError, match="unknown field"):
        b.column("close")


def test_a_single_member_batch_matches_a_single_engine():
    # The default macro must be shared, or EngineBatch([s]) and Engine(s)
    # would be different markets -- silently, because both look reasonable.
    b = batch([42])
    alone = pretium.Engine(seed=42, universe=UNIVERSE)
    alone.open_market()
    alone.run_session(9, 30, 3, 200)
    assert grid(b.prices(), *b.shape)[0] == flat(alone.prices(), len(UNIVERSE))


# --------------------------------------------------------------------------
# Stepping
# --------------------------------------------------------------------------

def test_tick_advances_every_member():
    b = pretium.EngineBatch(seeds=[1, 2, 3], universe=UNIVERSE)
    b.open_market()
    before = grid(b.prices(), *b.shape)
    for i in range(30):
        b.tick(9, 30 + i, 3)
    after = grid(b.prices(), *b.shape)
    assert all(a != c for a, c in zip(before, after))


def test_a_batched_tick_loop_matches_a_batched_session():
    fast = pretium.EngineBatch(seeds=[5, 6], universe=UNIVERSE)
    fast.open_market()
    fast.run_session(9, 30, 3, 60)

    slow = pretium.EngineBatch(seeds=[5, 6], universe=UNIVERSE)
    slow.open_market()
    for i in range(60):
        slow.tick(9 + (30 + i) // 60, (30 + i) % 60, 3)

    assert grid(fast.prices(), *fast.shape) == grid(slow.prices(), *slow.shape)


def test_draws_are_reported_per_member():
    b = batch(range(4))
    draws = b.draws_consumed
    assert len(draws) == 4
    assert all(d > 0 for d in draws)


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------

def test_duplicate_seeds_are_refused():
    # Two members with the same seed are the same market twice: the results
    # read as two samples and are one, silently.
    with pytest.raises(pretium.ValidationError, match="distinct"):
        pretium.EngineBatch(seeds=[1, 1, 2], universe=UNIVERSE)


def test_degenerate_construction_is_refused():
    with pytest.raises(pretium.ValidationError, match="no seeds"):
        pretium.EngineBatch(seeds=[], universe=UNIVERSE)
    with pytest.raises(pretium.ValidationError, match="empty"):
        pretium.EngineBatch(seeds=[1], universe=[])


def test_invalid_stepping_is_refused():
    b = pretium.EngineBatch(seeds=[1], universe=UNIVERSE)
    with pytest.raises(pretium.ValidationError, match="invalid time"):
        b.tick(25, 0, 3)
    with pytest.raises(pretium.ValidationError, match="greater than zero"):
        b.run_session(9, 30, 3, 0)
