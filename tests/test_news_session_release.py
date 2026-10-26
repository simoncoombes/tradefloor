"""Company news released through the session (`news_session_release_share`)."""
import numpy as np
import pytest

import tradefloor as tf
from tradefloor.sandbox import MarketView


def engine(seed=7, **dials):
    u = tf.Universe.random(12, seed=111)
    return tf.Engine(seed=seed, universe=u, model=tf.ModelParams.from_preset("pt-v20", **dials))


def first_ticks_and_closes(e, days):
    """The price after each day's first tick and at its close."""
    f, c = [], []
    for _ in range(days):
        e.open_market()
        e.run_session(9, 30, 3, 1, close_at_end=False)
        f.append(np.frombuffer(e.prices(), "<f8").copy())
        e.run_session(9, 31, 3, 389, close_at_end=True)
        e.close_market()
        c.append(np.frombuffer(e.prices(), "<f8").copy())
    return np.array(f), np.array(c)


@pytest.mark.parametrize("preset", ["pt-v20", "pt-v21"])
def test_off_on_every_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["news_session_release_share"] == 0.0


@pytest.mark.parametrize("value", [-0.1, 1.5])
def test_range(value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", news_session_release_share=value)


def test_zero_is_the_engine_as_it_stood():
    a, b = engine(), engine(news_session_release_share=0.0)
    assert a.model.fingerprint == b.model.fingerprint
    fa, ca = first_ticks_and_closes(a, 20)
    fb, cb = first_ticks_and_closes(b, 20)
    assert np.array_equal(fa, fb) and np.array_equal(ca, cb)
    assert all("release_minute" not in e for e in b.session_news())


def test_the_news_leaves_the_first_tick():
    base, live = engine(), engine(news_session_release_share=1.0)
    assert base.model.fingerprint != live.model.fingerprint
    fb, cb = first_ticks_and_closes(base, 120)
    fl, cl = first_ticks_and_closes(live, 120)
    # The first tick's move from the previous close: smaller once the news
    # is released through the day, not at the first tick.
    mb = np.log(fb[1:] / cb[:-1]); ml = np.log(fl[1:] / cl[:-1])
    assert (ml ** 2).sum() < (mb ** 2).sum()


def test_release_minutes_are_carried_and_held_back():
    e = engine(news_session_release_share=1.0)
    seen = False
    for _ in range(40):
        e.open_market()
        news = e.session_news()
        if news:
            seen = True
            assert all(0.0 <= n["release_minute"] < 390.0 for n in news)
            view = MarketView(e)
            # Nothing is out before its release; everything is by the close.
            assert len(view.news()) == sum(n["release_minute"] <= 0 for n in news)
            e.run_session(9, 30, 3, 390, close_at_end=True)
            assert len(view.news()) == len(news)
        else:
            e.run_session(9, 30, 3, 390, close_at_end=True)
        e.close_market()
    assert seen


def test_snapshot_round_trip_mid_day():
    a = engine(news_session_release_share=1.0)
    for _ in range(5):
        a.open_market(); a.run_session(9, 30, 3, 390, close_at_end=True); a.close_market()
    a.open_market(); a.run_session(9, 30, 3, 120, close_at_end=False)
    b = engine(news_session_release_share=1.0)
    b.restore_state(a.state_snapshot())
    a.run_session(11, 30, 3, 270, close_at_end=True)
    b.run_session(11, 30, 3, 270, close_at_end=True)
    assert np.array_equal(np.frombuffer(a.prices(), "<f8"), np.frombuffer(b.prices(), "<f8"))
