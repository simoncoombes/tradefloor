"""Company news released before the open (`news_preopen_share`)."""
import numpy as np
import pytest

import tradefloor as tf

SPLIT = dict(overnight_market_share=0.55, overnight_idio_share=0.1)


def engine(seed=7, **dials):
    u = tf.Universe.random(12, seed=111)
    return tf.Engine(seed=seed, universe=u, model=tf.ModelParams.from_preset("pt-v20", **dials))


def opens_and_closes(e, days):
    o, c = [], []
    for _ in range(days):
        e.open_market()
        o.append(np.frombuffer(e.prices(), "<f8").copy())
        e.run_session(9, 30, 3, 390, close_at_end=True)
        e.close_market()
        c.append(np.frombuffer(e.prices(), "<f8").copy())
    return np.array(o), np.array(c)


@pytest.mark.parametrize("preset", ["pt-v20", "pt-v21"])
def test_off_on_every_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["news_preopen_share"] == 0.0


@pytest.mark.parametrize("value", [-0.1, 1.5])
def test_range(value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", news_preopen_share=value, **SPLIT)


def test_refused_without_a_split():
    with pytest.raises(Exception, match="night split"):
        tf.ModelParams.from_preset("pt-v20", news_preopen_share=0.3)


def test_zero_is_the_split_as_it_stood():
    a, b = engine(**SPLIT), engine(news_preopen_share=0.0, **SPLIT)
    assert a.model.fingerprint == b.model.fingerprint
    oa, ca = opens_and_closes(a, 30)
    ob, cb = opens_and_closes(b, 30)
    assert np.array_equal(oa, ob) and np.array_equal(ca, cb)


def test_the_news_moves_into_the_open():
    base, live = engine(**SPLIT), engine(news_preopen_share=1.0, **SPLIT)
    assert base.model.fingerprint != live.model.fingerprint
    ob, cb = opens_and_closes(base, 120)
    ol, cl = opens_and_closes(live, 120)
    nb = np.log(ob[1:] / cb[:-1]); nl = np.log(ol[1:] / cl[:-1])
    # Every event priced at the open: the nights are louder.
    assert (nl ** 2).sum() > (nb ** 2).sum()
