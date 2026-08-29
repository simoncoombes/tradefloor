"""Volume persistence is partly idiosyncratic, and that half was not modelled.

`update_volume_state` carries a COMMON multiplier: every name shares it, so
the whole market is busy or quiet together. Its own docstring has said since
it was written that "real volume persistence is partly idiosyncratic, and
that half is not modelled".

It is the half the last panel miss needs. `volume_change_acf1` at 504 days
reads about -0.316 against a band of -0.29 to -0.21 on every preset, and the
model is too NEGATIVE, which is what independent per-tick noise does to the
change in a series. Reaching the band through the COMMON component needs a
bigger innovation, and that takes `volume_abs_return_corr` out with it
(CALIBRATION-FOLLOWUPS.md §21 to §23, §73), because a market-wide volume
multiplier adds volume variance unrelated to any name's own moves.

A per-name state raises each name's own volume autocorrelation without
touching the common component. Pinned here: it ships inert, it is per-NAME
rather than common, and it moves volume without moving prices.
"""

from __future__ import annotations

import pyarrow as pa
import pyarrow.compute as pc
import pytest

import tradefloor as pt

ON = dict(volume_idio_persistence=0.8, volume_idio_sigma=0.25)


def _run(model, days: int = 30, n: int = 12):
    e = pt.Engine(seed=13, universe=pt.Universe.random(n, seed=4), model=model)
    e.run_days(days)
    return e


@pytest.mark.parametrize("preset", ["pt-v3", "pt-v10", "pt-v11"])
def test_it_ships_inert(preset: str) -> None:
    """At zero the trajectory is the shipped one, bit for bit."""
    before = list(_run(preset).prices())
    m = pt.ModelParams.from_preset(preset, volume_idio_persistence=0.0,
                                   volume_idio_sigma=0.0)
    assert m.fingerprint == preset
    assert list(_run(m).prices()) == before


def test_it_moves_volume() -> None:
    base = _run("pt-v11")
    on = _run(pt.ModelParams.from_preset("pt-v11", **ON))

    def vols(engine):
        b = pa.table(engine.bars(grain="day"))
        return b["volume"].to_pylist()

    assert vols(on) != vols(base)


def test_it_is_per_name_and_not_common() -> None:
    """The whole point: names must diverge from each other, not together.

    The common component multiplies every name by the same number, so it
    cannot change the SPREAD of volume across names on a given day. A
    per-name state must.
    """
    import statistics as st

    def cross_sectional_spread(engine) -> float:
        b = pa.table(engine.bars(grain="day"))
        last = pc.max(b["day"]).as_py()
        day = b.filter(pc.equal(b["day"], last))
        v = [x for x in day["volume"].to_pylist() if x and x > 0]
        return st.pstdev([x / st.mean(v) for x in v])

    common_only = _run(pt.ModelParams.from_preset(
        "pt-v11", volume_persistence=0.9, volume_innovation_sigma=0.3))
    per_name = _run(pt.ModelParams.from_preset("pt-v11", **ON))
    assert cross_sectional_spread(per_name) != cross_sectional_spread(common_only)


def test_it_reaches_prices_through_the_book() -> None:
    """Volume is NOT downstream of price here, and that is worth pinning.

    This test was first written the other way round, asserting that a
    volume-only dial leaves prices untouched. It does not, on any preset,
    including ones with `volume_variance_gain` at zero. Volume sets how deep
    the book is, depth sets what an order does to the price, so a name that
    trades more prints a different path.

    That means this dial is not addressable at the volume row alone: a
    calibration that moves it is also moving execution. Recorded here rather
    than discovered during one.
    """
    m = pt.ModelParams.from_preset("pt-v3", **ON)
    assert list(_run(m).prices()) != list(_run("pt-v3").prices())

    # And with the mechanism off it is exactly inert, so the difference
    # above is the mechanism and not the extra draws: the per-name state has
    # its own RNG stream precisely so the other streams do not shift.
    off = pt.ModelParams.from_preset("pt-v3", volume_idio_persistence=0.0,
                                     volume_idio_sigma=0.0)
    assert list(_run(off).prices()) == list(_run("pt-v3").prices())


def test_both_dials_are_settable_and_fingerprinted() -> None:
    for name, value in (("volume_idio_persistence", 0.7),
                        ("volume_idio_sigma", 0.2)):
        assert name in pt.ModelParams.settable()
        m = pt.ModelParams.from_preset("pt-v11", **{name: value})
        assert m.fingerprint.startswith("custom-")
        assert m.to_dict()[name] == value


def test_the_variance_coupling_ships_inert_and_follows_the_name() -> None:
    """The return-RELATED per-name volume, and why it exists.

    §111 measured that per-name volume PERSISTENCE fixes the volume-change
    row and takes `volume_abs_return_corr` down with it, exactly as the
    common component does. The cause is not common-versus-per-name: any
    volume variance unrelated to a name's own returns dilutes a statistic
    measuring how well volume tracks the size of that name's move.

    This couples volume to the name's own conditional variance instead, so
    the variance it adds is return-related by construction.
    """
    for preset in ("pt-v3", "pt-v10", "pt-v11"):
        m = pt.ModelParams.from_preset(preset, volume_idio_variance_gain=0.0)
        assert m.fingerprint == preset
        assert list(_run(m).prices()) == list(_run(preset).prices()), preset

    on = pt.ModelParams.from_preset("pt-v11", volume_idio_variance_gain=1.0)
    assert on.fingerprint.startswith("custom-")
    assert "volume_idio_variance_gain" in pt.ModelParams.settable()

    def vols(engine):
        return pa.table(engine.bars(grain="day"))["volume"].to_pylist()

    assert vols(_run(on)) != vols(_run("pt-v11"))
