"""Seed sweeps.

Runs in a subprocess pool, so everything here must be importable at module
level and guarded — pytest already runs under ``__main__`` protection, but the
payload function has to be picklable, which is why it lives at module scope in
the package rather than being a closure.
"""

import struct

import pytest

import pretium


def arr(buf):
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


UNIVERSE = pretium.Universe.random(8, seed=3)


def test_results_are_ordered_by_input_not_by_completion():
    """The property the whole function turns on.

    A sweep whose output order depended on which worker finished first would be
    non-deterministic in exactly the way this library exists to avoid, and the
    bug would present as noise in someone's results rather than as a failure.
    """
    seeds = [11, 3, 7, 42, 5]
    swept = pretium.run_many(seeds, universe=UNIVERSE, ticks=80, workers=4)
    for i, seed in enumerate(seeds):
        alone = pretium.run_many([seed], universe=UNIVERSE, ticks=80)[0]
        assert arr(swept[i]) == arr(alone), f"position {i} is not seed {seed}"


def test_worker_count_does_not_change_the_answer():
    # Per-seed isolation is total: each worker builds its own engine from its
    # own seed and shares nothing. If that were not so, results would depend on
    # how the work happened to be divided.
    #
    # The workers are THREADS sharing an address space, so a mistake here is a
    # data race rather than a serialisation bug -- which is why several counts
    # are checked rather than one. A race that a 4-thread run hides can still
    # show at 8.
    seeds = list(range(10))
    one = pretium.run_many(seeds, universe=UNIVERSE, ticks=60, workers=1)
    for workers in (2, 4, 8):
        assert pretium.run_many(seeds, universe=UNIVERSE, ticks=60,
                                workers=workers) == one


def test_a_sweep_matches_running_each_seed_by_hand():
    seeds = [1, 2, 3]
    swept = pretium.run_many(seeds, universe=UNIVERSE, ticks=100, days=2, workers=2)
    for seed, result in zip(seeds, swept):
        e = pretium.Engine(seed=seed, universe=UNIVERSE)
        for _ in range(2):
            e.open_market()
            e.run_session(9, 30, 3, 100)
            e.close_market()
        assert arr(result) == arr(e.prices())


def test_different_seeds_give_different_markets():
    results = pretium.run_many(range(8), universe=UNIVERSE, ticks=60, workers=2)
    assert len({bytes(r) for r in results}) == 8


def test_the_universe_crosses_as_a_specification():
    # Workers rebuild from JSON rather than from a pickle, so a worker's
    # universe is the same universe by construction rather than by whatever a
    # pickle happened to preserve.
    swept = pretium.run_many([4], universe=UNIVERSE, ticks=50)[0]
    rebuilt = pretium.Universe.from_json(UNIVERSE.to_json())
    direct = pretium.run_many([4], universe=rebuilt, ticks=50)[0]
    assert arr(swept) == arr(direct)


def test_collect_modes():
    summary = pretium.run_many([1, 2], universe=UNIVERSE, ticks=50,
                               workers=2, collect="summary")
    assert [s["seed"] for s in summary] == [1, 2]
    assert summary[0]["draws_consumed"] > 0
    assert summary[0]["tickers"] == UNIVERSE.tickers()

    attribution = pretium.run_many([1], universe=UNIVERSE, ticks=50,
                                   collect="attribution")[0]
    assert sorted(attribution["columns"]) == sorted(pretium.Engine.FACTORS)
    # Provenance, like the summary rows. `prices` is the one mode without it:
    # it returns raw bytes and has nowhere to put it.
    assert attribution["universe_fingerprint"] == UNIVERSE.fingerprint
    assert attribution["seed"] == 1


def test_a_macro_state_reaches_every_worker():
    macro = pretium.Macro(vix=28.0, federal_funds_rate=0.05, cycle="contraction")
    swept = pretium.run_many([9], universe=UNIVERSE, ticks=80, macro=macro)[0]
    e = pretium.Engine(seed=9, universe=UNIVERSE, macro_state=macro)
    e.open_market()
    e.run_session(9, 30, 3, 80)
    e.close_market()
    assert arr(swept) == arr(e.prices())


def test_degenerate_inputs_are_refused():
    with pytest.raises(pretium.ValidationError, match="no seeds"):
        pretium.run_many([], universe=UNIVERSE)
    with pytest.raises(pretium.ValidationError, match="at least 1"):
        pretium.run_many([1], universe=UNIVERSE, ticks=0)
    with pytest.raises(pretium.ValidationError, match="unknown collect"):
        pretium.run_many([1], universe=UNIVERSE, ticks=10, collect="everything")


def test_a_sweep_runs_where_there_is_no_importable_main():
    """The regression that matters most, because the failure was a HANG.

    `run_many` used a process pool. Windows spawns rather than forks, and
    spawning re-imports ``__main__`` in every child — which a REPL, a Jupyter
    notebook and a piped script do not have. The children died on
    ``OSError: [Errno 22] Invalid argument: '<stdin>'`` and the parent waited
    for results that were never coming. Measured as a ten-minute hang on a
    twenty-seed sweep, with no error and no output.

    Jupyter is where a library like this is most likely to be used, so that
    was not an edge case; it was broken exactly where it mattered. Threads
    fixed it, and this runs the sweep through stdin — with no ``__main__``
    file — to hold the fix in place.

    The timeout is the assertion. A regression reintroduces a hang, not a
    failure, and a test without a deadline would hang the suite with it.
    """
    import subprocess
    import sys

    script = (
        "import pretium as pt\n"
        "u = pt.Universe.random(6, seed=1)\n"
        "r = pt.run_many(seeds=[1, 2, 3, 4], universe=u, days=1, ticks=20,"
        " workers=4, collect='prices')\n"
        "print('OK', len(r))\n"
    )
    result = subprocess.run(
        [sys.executable, "-"], input=script, capture_output=True, text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "OK 4" in result.stdout
