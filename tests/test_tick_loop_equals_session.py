"""A tick loop and a session are two spellings of the same day.

Every preset, not just the default. The bug this pins (§117) shipped inside
pt-v11 and was invisible for the whole of its calibration because pt-v11 was
never the default: `test_a_batched_tick_loop_matches_a_batched_session` runs
on whatever the default is, so it went green through the entire crisis
thread and sprang the moment pt-v12 was promoted.

What it was. Endogenous news is generated once for a day. It used to be
generated in `run_session` and chained onto the caller's events there, which
is wrong in two independent ways:

  * a session is a CALL, and `EngineBatch.tick` is a call with `ticks=1`, so
    a tick-driven day re-rolled the day's news on every one of its 390
    minutes;
  * `Engine.tick` does not go through `run_session` at all -- it builds its
    own request with no news -- so a tick-driven caller saw no endogenous
    news whatever.

Generation now happens in `open_market`, which every spelling of a day
passes through exactly once, and the chaining happens in `tick_inner`, which
every spelling of a minute passes through exactly once.
"""
import numpy as np
import pytest

import tradefloor


UNIVERSE = tradefloor.Universe.random(8, seed=7)
PRESETS = ["pt-v%d" % i for i in range(1, 13)]


def _prices(engine):
    return np.frombuffer(engine.column("price"), dtype="<f8").copy()


@pytest.mark.parametrize("preset", PRESETS)
@pytest.mark.parametrize("seed", [5, 6, 7])
def test_a_tick_loop_is_the_same_day_as_one_session(preset, seed):
    model = tradefloor.ModelParams.from_preset(preset)

    session = tradefloor.Engine(seed=seed, universe=UNIVERSE, model=model)
    session.open_market()
    session.run_session(9, 30, 3, 60)

    stepped = tradefloor.Engine(seed=seed, universe=UNIVERSE, model=model)
    stepped.open_market()
    for i in range(60):
        stepped.tick(9 + (30 + i) // 60, (30 + i) % 60, 3)

    a, b = _prices(session), _prices(stepped)
    worst = float(np.abs(a - b).max())
    assert worst == 0.0, (
        f"{preset} seed {seed}: a 60-tick session and 60 ticks printed "
        f"different prices, worst {worst}. Something a day owns is being "
        f"rebuilt or skipped per CALL -- see §117."
    )


@pytest.mark.parametrize("preset", PRESETS)
def test_a_tick_driven_day_gets_the_endogenous_news_a_session_gets(preset):
    """The half of §117 that a price comparison alone would not catch.

    If the tick path silently dropped endogenous news AND the session path
    were changed to drop it too, the test above would pass on two identical
    wrong markets. This one asserts the mechanism is actually live wherever
    the preset turns it on.
    """
    model = tradefloor.ModelParams.from_preset(preset)
    if model.endogenous_news_intensity == 0.0:
        pytest.skip(f"{preset} ships the mechanism switched off")

    # Enough names that at least one event lands: 40 at intensity 0.05.
    universe = tradefloor.Universe.random(40, seed=3)
    off = tradefloor.ModelParams.from_preset(preset, endogenous_news_intensity=0.0,
                                          endogenous_news_sigma=0.0)

    def stepped(m):
        e = tradefloor.Engine(seed=11, universe=universe, model=m)
        e.open_market()
        for i in range(60):
            e.tick(9 + (30 + i) // 60, (30 + i) % 60, 3)
        return _prices(e)

    assert float(np.abs(stepped(model) - stepped(off)).max()) > 0.0, (
        f"{preset}: turning endogenous news off changed nothing on a "
        "tick-driven day, so the tick path is not seeing it"
    )


@pytest.mark.parametrize("preset", ["pt-v19", "pt-v20"])
@pytest.mark.parametrize("split", [(2, 195), (6, 65), (78, 5)])
def test_a_day_of_sessions_on_an_advancing_clock_is_one_session(preset, split):
    """A third spelling of a day: several `run_session` calls, each starting
    where the last ended. It is the same day to the bit, and it raises no
    clock warning, which is what a harness stepping an agent needs.

    Starting every session at 9:30 is not the same day. It replays the
    opening minutes, and `run_session` warns.
    """
    import warnings

    from tradefloor.harness import session_clock

    model = tradefloor.ModelParams.from_preset(preset)
    steps, ticks = split

    whole = tradefloor.Engine(seed=5, universe=UNIVERSE, model=model)
    whole.open_market()
    whole.run_session(9, 30, 3, 390)

    stepped = tradefloor.Engine(seed=5, universe=UNIVERSE, model=model)
    stepped.open_market()
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        for step in range(steps):
            stepped.run_session(*session_clock((9, 30, 3), step, ticks), ticks)
    assert whole.prices() == stepped.prices()
    assert whole.draws_consumed == stepped.draws_consumed

    repeated = tradefloor.Engine(seed=5, universe=UNIVERSE, model=model)
    repeated.open_market()
    with pytest.warns(RuntimeWarning, match="harness.session_clock"):
        for _ in range(steps):
            repeated.run_session(9, 30, 3, ticks)
    assert whole.prices() != repeated.prices()
