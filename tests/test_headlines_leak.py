"""The leak guard, tested: no headline gives away its event's price impact.

`price_impact` is the answer key: the engine adds it to the announcer's price
over the session. A headline may say the direction (real headlines do), so
the sign is allowed through. The size is not. Three tests:

1. Invariance, the exact one. Rescale every event's impact by an independent
   positive factor (so sizes AND their ranks change, signs do not): the
   headlines are byte-identical, order included. So the text is a function of
   the sign and the event's identity alone.
2. Prediction, the one a skeptic would run. Over ~4,000 events drawn by the
   engine itself (pt-v19, 40 names, 2,000 days), fit the best predictors of
   |price_impact| we can build from the headline -- template skeleton group
   means, a ridge regression on every word, text length -- on half and score
   them on the other half. None may beat the sign-and-category baseline by
   more than 0.005 of R^2. (The sign alone is worth ~0: |z| and sign(z) are
   independent.)
3. Power. The same test must catch a leak: a generator that adds an
   intensity word by size, and a subtle one that tilts one template choice
   65/35 by whether the move is above the median. If test 2 could not see
   those, passing it would mean nothing. Measured on six engine seeds: the
   fair generator's best gain is at most +0.0001; the 65/35 tilt's is 0.022
   to 0.057 (a 60/40 tilt, 0.005 to 0.025, is at the edge of what 4,000
   events can see); the intensity word's is about 0.75.
"""

from __future__ import annotations

import copy
import hashlib
import random
import re
from dataclasses import fields

import numpy as np
import pytest
import tradefloor as tf
from tradefloor.headlines import NewsFact, headlines_for

TOLERANCE = 0.005  # R^2 a text predictor may gain over sign + category


@pytest.fixture(scope="module")
def engine_events():
    """(impact, headline) for every event pt-v19 draws in 2,000 days on 40
    names. Only `open_market` / `close_market`: the news is drawn at the open
    on its own stream, so skipping the ticks changes no event, and the whole
    draw takes under a second."""
    e = tf.Engine(seed=7, universe=tf.Universe.random(40, seed=111), model="pt-v19")
    rows = []
    for d in range(2000):
        e.open_market()
        snap = e.state_snapshot()
        got = headlines_for(snap, day=d, tick=390, tickers=e.tickers)
        impacts = [ev["price_impact"] for ev in snap["session_news"] if ev["price_impact"]]
        assert len(got) == len(impacts)
        rows.extend(zip(impacts, got))
        e.close_market()
    assert len(rows) > 3000
    return rows


# -- 1. invariance -----------------------------------------------------------------


def test_nothing_about_a_headline_changes_when_only_the_sizes_do() -> None:
    e = tf.Engine(seed=7, universe=tf.Universe.random(40, seed=111), model="pt-v19")
    rng = random.Random(0)
    checked = 0
    for d in range(120):
        e.open_market()
        snap = e.state_snapshot()
        if snap["session_news"]:
            want = headlines_for(snap, day=d, tick=390, tickers=e.tickers)
            for _ in range(5):
                other = copy.deepcopy(snap)
                for ev in other["session_news"]:
                    ev["price_impact"] *= 10 ** rng.uniform(-6, 6)
                assert headlines_for(other, day=d, tick=390, tickers=e.tickers) == want
            flipped = copy.deepcopy(snap)
            for ev in flipped["session_news"]:
                ev["price_impact"] = -ev["price_impact"]
            assert [h.text for h in headlines_for(flipped, day=d, tick=390,
                                                  tickers=e.tickers)] != [h.text for h in want]
            checked += len(want)
        e.close_market()
    assert checked > 150


def test_the_text_layer_cannot_see_a_size() -> None:
    """The structural half of the guard: what the text is written from has no
    field a magnitude could ride in."""
    assert {f.name for f in fields(NewsFact)} == {"day", "arm", "direction", "ticker", "sector"}


# -- 2. prediction ---------------------------------------------------------------------


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9&]+", text.lower())


def _r2(y: np.ndarray, pred: np.ndarray, const: float) -> float:
    return float(1.0 - np.sum((y - pred) ** 2) / np.sum((y - const) ** 2))


def _best_text_gain(y, sign, category, texts, tickers) -> tuple[float, dict[str, float]]:
    """Out-of-sample R^2 of |impact| predictors, and the best text predictor's
    gain over the sign-and-category baseline. Train on even rows, score on
    odd; every R^2 is against the training mean, so a predictor that has
    learned nothing scores about zero and one that overfits scores below."""
    n = len(y)
    tr = np.arange(n) % 2 == 0
    te = ~tr
    const = float(y[tr].mean())
    keys = [f"{c}|{s}" for c, s in zip(category, sign)]
    base = {k: float(y[tr][np.array(keys)[tr] == k].mean()) for k in set(keys)}
    base_pred = np.array([base[k] for k in keys])
    scores = {"sign+category": _r2(y[te], base_pred[te], const)}

    # Template skeleton: the text with its ticker and quarter masked, group
    # means shrunk toward the baseline (10 pseudo-events) so a rare skeleton
    # is not scored on noise.
    skel = [re.sub(r"\bQ[1-4]\b", "<Q>", t.replace(tk, "<T>")) for t, tk in zip(texts, tickers)]
    groups: dict[str, list[float]] = {}
    for s, v in zip(np.array(skel)[tr], y[tr]):
        groups.setdefault(s, []).append(float(v))
    k = 10.0
    pred = np.array([(sum(groups.get(s, [])) + k * b) / (len(groups.get(s, [])) + k)
                     for s, b in zip(skel, base_pred)])
    scores["skeleton"] = _r2(y[te], pred[te], const)

    # Ridge on every word (ticker, firm, quarter and storyline included) plus
    # the sign, the penalty chosen by 5-fold cross-validation on train only.
    vocab = {w: i for i, w in enumerate(sorted({w for t in texts for w in _tokens(t)}))}
    X = np.zeros((n, len(vocab) + 1))
    for r, t in enumerate(texts):
        for w in set(_tokens(t)):
            X[r, vocab[w]] = 1.0
    X[:, -1] = sign

    def fit(Xa, ya, lam):
        mu, ym = Xa.mean(0), ya.mean()
        Xc = Xa - mu
        b = np.linalg.solve(Xc.T @ Xc + lam * np.eye(Xa.shape[1]), Xc.T @ (ya - ym))
        return lambda Xb: (Xb - mu) @ b + ym

    Xtr, ytr = X[tr], y[tr]
    fold = np.arange(len(ytr)) % 5
    best = min((sum(float(np.sum((ytr[fold == f] - fit(Xtr[fold != f], ytr[fold != f], lam)(
        Xtr[fold == f])) ** 2)) for f in range(5)), lam)
        for lam in (0.1, 1.0, 10.0, 100.0, 1e3, 1e4))
    scores["words (ridge)"] = _r2(y[te], fit(Xtr, ytr, best[1])(X[te]), const)

    # Text length, with the sign.
    A = np.c_[np.ones(n), [len(t) for t in texts], sign]
    coef = np.linalg.lstsq(A[tr], y[tr], rcond=None)[0]
    scores["length"] = _r2(y[te], A[te] @ coef, const)

    gain = max(v for k2, v in scores.items() if k2 != "sign+category") - scores["sign+category"]
    return gain, scores


def _arrays(rows, texts=None):
    y = np.array([abs(x) for x, _ in rows])
    sign = np.array([1.0 if x > 0 else -1.0 for x, _ in rows])
    category = [h.category for _, h in rows]
    tickers = [h.tickers[0] for _, h in rows]
    return y, sign, category, (texts or [h.text for _, h in rows]), tickers


def test_no_text_predictor_of_the_size_beats_the_sign(engine_events) -> None:
    gain, scores = _best_text_gain(*_arrays(engine_events))
    assert abs(scores["sign+category"]) < 0.005, scores  # |z| is independent of sign
    assert gain <= TOLERANCE, scores


# -- 3. power ---------------------------------------------------------------------------


SIGMA = tf.ModelParams.from_preset("pt-v19").endogenous_news_sigma


def test_the_test_catches_an_intensity_word_chosen_by_size(engine_events) -> None:
    def graded(x: float, text: str) -> str:
        z = abs(x) / SIGMA  # half-normal terciles
        return text + (" slightly" if z < 0.4307 else "" if z < 0.9674 else " sharply")

    texts = [graded(x, h.text) for x, h in engine_events]
    gain, scores = _best_text_gain(*_arrays(engine_events, texts))
    assert gain > 0.5, scores


def test_the_test_catches_a_template_choice_tilted_by_size(engine_events) -> None:
    """A leak one would never see by reading: a suffix appended 65% of the
    time when the move is above the median and 35% when below, by a coin
    from the event's identity."""
    y = np.array([abs(x) for x, _ in engine_events])
    median = float(np.median(y))

    def coin(h) -> float:
        digest = hashlib.sha256(f"{h.day}|{h.tickers}".encode()).digest()
        return int.from_bytes(digest[:4], "big") / 2**32

    texts = [h.text + (" (update)" if coin(h) < (0.65 if abs(x) > median else 0.35) else "")
             for x, h in engine_events]
    gain, scores = _best_text_gain(*_arrays(engine_events, texts))
    assert gain > 2 * TOLERANCE, scores
