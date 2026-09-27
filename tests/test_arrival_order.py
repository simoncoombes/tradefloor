"""`book_arrival_shuffle`: a cohort's arrival order at the shared book.

At 0.0, which every preset carries, a `World` cohort executes in sorted label
order on every step, so on a live book the same label always takes the levels
first and stands first in the queue. With the switch on, each step's order is
the labels sorted by a counter-based priority of the seed, the world's day,
the step within the day and the label (`rust/src/rng.rs`,
`arrival_priority`): a name buys no priority, and arriving second still costs
what it costs.

The engines here are built on a named opening (`macro=tf.Macro()`), which
skips pt-v20's burn-in; the book, not the macro path, is what is under test.
"""

from __future__ import annotations

import collections

import pytest

import tradefloor as tf
from tradefloor.counterfactual import World

M64 = (1 << 64) - 1
ARRIVAL = 12
SEED64_TAG = 0x5344_3634

U = tf.Universe.random(8, seed=99)
T = U[0].ticker
AT = 1  # the step the one-shot agents trade on


# -- the formula, stated independently of the Rust --------------------------------

def _mix(x: int) -> int:
    z = (x + 0x9E3779B97F4A7C15) & M64
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & M64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & M64
    return z ^ (z >> 31)


def _stream_mix(seed: int, k: int) -> int:
    if seed >> 32 == 0:
        return (seed << 32) | k
    return seed ^ _mix((SEED64_TAG << 32) | k)


def _fnv1a64(data: bytes) -> int:
    h = 0xCBF29CE484222325
    for b in data:
        h = ((h ^ b) * 0x100000001B3) & M64
    return h


def priority(seed: int, day: int, step: int, label: str) -> int:
    base = _mix(_stream_mix(seed, ARRIVAL))
    key = _mix(_mix(base ^ day) ^ step)
    return _mix(key ^ _fnv1a64(label.encode("utf-8")))


def order(seed: int, day: int, step: int, labels) -> list[str]:
    return sorted(labels, key=lambda lab: (priority(seed, day, step, lab), lab))


def on(**over) -> tf.ModelParams:
    return tf.ModelParams.from_preset(**{"book_arrival_shuffle": 1.0, **over})


def engine(seed: int, model=None) -> tf.Engine:
    return tf.Engine(seed=seed, universe=U, macro_state=tf.Macro(),
                     model=model if model is not None else on())


def world(seed: int, agents: dict, model=None, **kw) -> World:
    return World(seed=seed, universe=U, macro=tf.Macro(),
                 model=model if model is not None else on(),
                 agents=agents, max_leverage=None, cash=1e12, **kw)


class Buyer:
    """One market buy of `q` shares of T at step AT."""

    def __init__(self, q):
        self.q = q

    def act(self, obs):
        return {T: self.q} if obs.step == AT else {}

    def fork(self):
        return Buyer(self.q)


class Every:
    """A market buy of `q` shares of T on every step."""

    def __init__(self, q):
        self.q = q

    def act(self, obs):
        return {T: self.q}

    def fork(self):
        return Every(self.q)


class Rester:
    """A buy limit at the best bid at step AT, `frac` of the daily volume."""

    def __init__(self, frac):
        self.frac = frac

    def act(self, obs):
        if obs.step != AT:
            return {}
        return {T: tf.Limit(self.frac * obs.avg_volume(T),
                            obs.engine.book(T).best_bid)}

    def fork(self):
        return Rester(self.frac)


def vwap(w: World, label: str) -> float:
    fills = w.portfolios[label].fills
    return (sum(f["price"] * abs(f["quantity"]) for f in fills)
            / sum(abs(f["quantity"]) for f in fills))


def filled(w: World, label: str) -> float:
    return sum(abs(f["quantity"]) for f in w.portfolios[label].fills)


# -- 1. the formula ---------------------------------------------------------------

def test_the_priority_is_the_documented_formula():
    """The golden values `rng.rs` pins, from the Python statement of it."""
    assert priority(201, 0, 0, "a") == 0x09f69160e6ea96dc
    assert priority(201, 3, 1, "b") == 0x3bc1d0a995436b68
    assert priority(2**40 + 7, 5, 2, "claude") == 0x32c68be7c13ffe3b
    assert order(201, 3, 1, ["a", "b"]) == ["b", "a"]


def test_the_engine_orders_by_the_formula():
    labels = ["a", "b", "c", "d", "claude", "m0", "été"]
    for seed in (0, 7, 201, 2**32 - 1, 2**32, 2**40 + 7, 2**64 - 1):
        e = tf.Engine(seed=seed, universe=U, macro_state=tf.Macro(), model=on())
        for day in (0, 1, 3, 252, 10_000):
            for step in range(6):
                assert e.arrival_order(day, step, labels) == \
                    order(seed, day, step, labels)


def test_off_is_sorted_label_order():
    e = tf.Engine(seed=201, universe=U, macro_state=tf.Macro(),
                  model=tf.ModelParams.from_preset())
    for day in range(5):
        for step in range(6):
            assert e.arrival_order(day, step, ["b", "c", "a"]) == ["a", "b", "c"]


def test_the_switch_is_a_switch():
    with pytest.raises(Exception, match="book_arrival_shuffle"):
        tf.ModelParams.from_preset(book_arrival_shuffle=0.5)
    assert tf.ModelParams.from_preset().fingerprint == "pt-v20"


# -- 2. determinism -------------------------------------------------------------------

def test_the_same_seed_and_any_mapping_order_give_the_same_market():
    q = round(0.02 * U[0].avg_volume)
    one = world(3, {"x": Every(q), "y": Every(q), "z": Every(q)})
    two = world(3, {"z": Every(q), "x": Every(q), "y": Every(q)})
    one.run(days=2)
    two.run(days=2)
    assert [r["arrival"] for r in one.trace] == [r["arrival"] for r in two.trace]
    assert one.digest() == two.digest()
    assert one.rejected == two.rejected


# -- 3. uniformity ---------------------------------------------------------------------

def test_each_label_is_first_equally_often_and_steps_are_independent():
    labels = ["a", "b", "c", "d"]
    first, a_before_b, same, n, prev = collections.Counter(), 0, 0, 0, None
    perms = collections.Counter()
    for seed in (2001, 2002, 2003, 2004, 2005):
        e = engine(seed)
        for day in range(252):
            for step in range(6):
                o = e.arrival_order(day, step, labels)
                first[o[0]] += 1
                perms[tuple(o)] += 1
                a_before_b += o.index("a") < o.index("b")
                if prev is not None:
                    same += o[0] == prev
                prev = o[0]
                n += 1
    # 7,560 steps: a share's se is 0.005, so these are 4-5 se bands.
    for label in labels:
        assert 0.225 < first[label] / n < 0.275
    assert 0.475 < a_before_b / n < 0.525
    assert 0.225 < same / (n - 1) < 0.275
    assert len(perms) == 24
    expected = n / 24
    chi2 = sum((c - expected) ** 2 / expected for c in perms.values())
    assert chi2 < 49.7  # the 0.001 point of chi-squared on 23 df


# -- 4. subsets ------------------------------------------------------------------------

def test_a_subset_keeps_the_relative_order():
    e = engine(207)
    full = ["w", "x", "y", "z"]
    for day in range(40):
        for step in range(6):
            o = e.arrival_order(day, step, full)
            for drop in full:
                rest = [lab for lab in full if lab != drop]
                assert e.arrival_order(day, step, rest) == \
                    [lab for lab in o if lab != drop]


def test_a_without_arm_keeps_the_order():
    q = round(0.02 * U[0].avg_volume)
    w = world(207, {"x": Every(q), "y": Every(q), "z": Every(q)})
    w.run(days=1)
    (arm,) = w.fork("arm")
    removed = w.without("y")
    arm.run(days=1)
    removed.run(days=1)
    tail = slice(len(w.trace), None)
    assert [r["arrival"] for r in arm.trace[tail]] == \
        [r["arrival"] for r in removed.trace[tail]]
    # y sent nothing in the removed arm, and the others met the book in the
    # same relative order they did with y trading.
    assert all(not r["agents"]["y"]["fills"] for r in removed.trace[tail])


# -- 5. forks, checkpoints, manifests --------------------------------------------------

def test_a_fork_keeps_the_order_and_the_market():
    q = round(0.02 * U[0].avg_volume)
    w = world(11, {"a": Every(q), "b": Every(q)})
    w.run(days=1)
    left, right = w.fork("left", "right")
    left.run(days=2)
    right.run(days=2)
    assert [r["arrival"] for r in left.trace] == [r["arrival"] for r in right.trace]
    assert left.digest() == right.digest()


def test_a_checkpoint_and_a_manifest_reproduce_the_market():
    q = round(0.03 * U[0].avg_volume)
    w = world(12, {"a": Every(q), "b": Every(q)})
    w.run(days=2)
    assert w.checkpoint().resume().prices() == w.engine.prices()
    assert w.manifest().reproduce().prices() == w.engine.prices()


# -- 6. one agent never reads it ----------------------------------------------------------

def test_one_agent_is_the_same_market_with_the_switch_on():
    q = round(0.05 * U[0].avg_volume)
    runs = {}
    for name, model in (("off", tf.ModelParams.from_preset()), ("on", on())):
        w = World(seed=21, universe=U, macro=tf.Macro(), model=model,
                  agent=Every(q), max_leverage=None, cash=1e12)
        w.run(days=2)
        runs[name] = w
    off, live = runs["off"], runs["on"]
    assert off.engine.prices() == live.engine.prices()
    assert off.trace == live.trace
    assert off.portfolio.fills == live.portfolio.fills
    assert "arrival" not in live.trace[0]


def test_evaluate_is_the_same_with_the_switch_on():
    """`evaluate` gives each agent its own market, so there is no order to
    shuffle: every score but the model's fingerprint is the same."""
    from tradefloor.baselines import Momentum

    def agents():
        return {"m": Momentum(lookback=2, top_k=2, gross=1.0),
                "n": Momentum(lookback=3, top_k=2, gross=1.0)}
    kw = dict(seed=5, universe=U, macro=tf.Macro(), days=2)
    off = tf.evaluate(agents(), model=tf.ModelParams.from_preset(), **kw)
    live = tf.evaluate(agents(), model=on(), **kw)
    for name in ("m", "n"):
        a, b = off[name].as_dict(), live[name].as_dict()
        assert a.pop("model_fingerprint") == "pt-v20"
        assert b.pop("model_fingerprint").startswith("custom-")
        assert a == b


# -- 7. the cost of arriving second, and who pays it ----------------------------------------

def test_the_second_arrival_pays_more_and_the_label_does_not():
    q = round(0.10 * U[0].avg_volume)
    b_dearer, second_dearer = [], []
    for seed in range(1, 9):
        w = world(seed, {"a": Buyer(q), "b": Buyer(q)})
        w.run(days=1)
        first = w.trace[AT]["arrival"][0]
        va, vb = vwap(w, "a"), vwap(w, "b")
        b_dearer.append(vb > va)
        second_dearer.append((vb > va) if first == "a" else (va > vb))
    assert all(second_dearer)
    assert 0 < sum(b_dearer) < len(b_dearer)


def test_under_label_order_the_later_label_always_pays_more():
    """The gap the switch closes: the same pair at 0.0."""
    q = round(0.10 * U[0].avg_volume)
    for seed in range(1, 5):
        w = world(seed, {"a": Buyer(q), "b": Buyer(q)},
                  model=tf.ModelParams.from_preset())
        w.run(days=1)
        assert vwap(w, "b") > vwap(w, "a")
        assert "arrival" not in w.trace[AT]


# -- 8. the queue ---------------------------------------------------------------------------

def test_resting_limits_at_one_price_fill_the_first_arrival_first():
    """Two bids at the best bid, each a quarter of the day's volume. Where
    the day's flow cannot fill both, the first to arrive fills first,
    whichever label it is. Seeds 1, 4, 17 and 19 bind on this roster, with
    b, b, b and a first."""
    firsts = set()
    for seed in (1, 4, 17, 19):
        w = world(seed, {"a": Rester(0.25), "b": Rester(0.25)})
        w.run(days=1)
        first = w.trace[AT]["arrival"][0]
        second = "b" if first == "a" else "a"
        assert filled(w, first) > filled(w, second)
        firsts.add(first)
    assert firsts == {"a", "b"}


# -- 9. the trace ---------------------------------------------------------------------------

def test_rows_carry_arrival_only_while_the_switch_is_on():
    q = round(0.02 * U[0].avg_volume)
    live = world(9, {"a": Every(q), "b": Every(q)})
    off = world(9, {"a": Every(q), "b": Every(q)},
                model=tf.ModelParams.from_preset())
    live.run(days=1)
    off.run(days=1)
    assert all(sorted(r["arrival"]) == ["a", "b"] for r in live.trace)
    assert all("arrival" not in r for r in off.trace)
    for r, (day, step) in zip(live.trace, [(0, s) for s in range(6)]):
        assert r["arrival"] == order(9, day, step, ["a", "b"])


# -- 10. off is the World it was ------------------------------------------------------------

def test_off_is_the_plain_cohort():
    """At 0.0 a cohort executes in label order: the model with the switch
    written as 0.0 and the default are one market, one trace."""
    q = round(0.03 * U[0].avg_volume)
    plain = World(seed=13, universe=U, macro=tf.Macro(),
                  agents={"a": Every(q), "b": Every(q)},
                  max_leverage=None, cash=1e12)
    zero = world(13, {"a": Every(q), "b": Every(q)},
                 model=tf.ModelParams.from_preset(book_arrival_shuffle=0.0))
    plain.run(days=1)
    zero.run(days=1)
    assert plain.digest() == zero.digest()
    assert plain.trace == zero.trace
