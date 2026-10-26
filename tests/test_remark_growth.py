"""The close's re-mark's growth part priced at the next open (`remark_growth_open_share`)."""
import numpy as np
import pytest

import tradefloor as tf

SPLIT = dict(overnight_market_share=0.55, overnight_idio_share=0.1)


def engine(seed=7, **dials):
    u = tf.Universe.random(12, seed=111)
    return tf.Engine(seed=seed, universe=u, model=tf.ModelParams.from_preset("pt-v20", **dials))


def prices(e):
    return np.frombuffer(e.prices(), "<f8")[:12].copy()


def opens_and_closes(e, days):
    o, c = [], []
    for _ in range(days):
        e.open_market()
        o.append(prices(e))
        e.run_session(9, 30, 3, 390)
        e.close_market()
        c.append(prices(e))
    return np.array(o), np.array(c)


@pytest.mark.parametrize("preset", ["pt-v20", "pt-v21"])
def test_off_on_every_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["remark_growth_open_share"] == 0.0


@pytest.mark.parametrize("value", [-0.1, 1.5])
def test_range(value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", remark_growth_open_share=value, **SPLIT)


def test_refused_without_a_split():
    with pytest.raises(Exception, match="night split"):
        tf.ModelParams.from_preset("pt-v20", remark_growth_open_share=0.5)


def test_zero_is_the_remark_as_it_stood():
    a, b = engine(**SPLIT), engine(remark_growth_open_share=0.0, **SPLIT)
    assert a.model.fingerprint == b.model.fingerprint
    oa, ca = opens_and_closes(a, 30)
    ob, cb = opens_and_closes(b, 30)
    assert np.array_equal(oa, ob) and np.array_equal(ca, cb)


def test_the_growth_waits_for_the_open():
    base, live = engine(**SPLIT), engine(remark_growth_open_share=1.0, **SPLIT)
    assert base.model.fingerprint != live.model.fingerprint
    for e in (base, live):
        e.open_market()
        e.run_session(9, 30, 3, 390)
    # The day's last print is the same on both.
    assert np.array_equal(prices(base), prices(live))
    held = []
    for day in range(20):
        base.close_market(); live.close_market()
        held.append(np.log(prices(live) / prices(base)))
        base.open_market(); live.open_market()
        # No draw is added and `s` stands, so the opening print is the
        # base's: the growth held back at the re-mark arrives on the night.
        # Exactly on the first night; after it the close's own steps that
        # read the price after the re-mark (the buybacks, the dividend's
        # price average) move the two apart slowly, parts in 1e5 to 1e4.
        if day == 0:
            assert np.array_equal(prices(live), prices(base))
        else:
            np.testing.assert_allclose(prices(live), prices(base), rtol=1e-3)
        base.run_session(9, 30, 3, 390)
        live.run_session(9, 30, 3, 390)
    # Growth is mostly up, so the price after the close sits below the
    # base's on average and the night carries it.
    assert np.mean(held) < 0
