"""The same seed gives the same run on every Python the package supports.

The wheel is abi3 and serves 3.11, 3.12 and 3.13 from one build, and the
known-answer digests cover the engine, which is Rust and cannot tell which
interpreter loaded it. What they do not cover is the Python between the
engine and the orders: net worth, gross exposure, rebalancing weights and
the scores. Python 3.12 changed built-in ``sum()`` over floats to
compensated summation, so in 0.8.5 an agent-driven market split in the last
bits between 3.11 and 3.12. With ``Universe.random(40, seed=111)``, seed 7
and ten days, the random baseline's order for AAD at step 29 was
``-4200.348357221354`` on 3.11 and ``-4200.348357221355`` on 3.12, 557 of
2,428 logged orders differed from there on, and every P&L on the scorecard
moved. The engine digests stayed put, which is why nothing noticed.

This file runs in the ``python 3.12`` and ``python 3.13`` jobs of
``.github/workflows/suite.yml`` as well as with the rest of the suite on
3.11. The digests below were taken on 3.11, so they fail anywhere the
arithmetic differs from it.
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import random
import sys

import pytest

import tradefloor as tf
from tradefloor._arith import ordered_sum

PACKAGE = pathlib.Path(tf.__file__).resolve().parent

#: What the run in :func:`_agent_driven_run` produces on Python 3.11. A
#: deliberate change to a reference agent or to the scoring moves these, and
#: then they are regenerated on 3.11 with ``python
#: tests/test_python_versions.py`` and the commit says why. A change on 3.12
#: or later alone is the bug this file exists for.
ORDERS_SHA256 = "537488a29c52a5d0"
SCORECARDS_SHA256 = "3f21f6c09822c650"


# --------------------------------------------------------------------------
# The helper
# --------------------------------------------------------------------------

def _left_to_right(values, start=0):
    """The loop Python 3.11's ``sum()`` ran, written out."""
    acc = start
    for value in values:
        acc = acc + value
    return acc


def test_ordered_sum_gives_the_python_311_answer():
    """Where 3.11 and 3.12 disagree, ordered_sum gives 3.11's answer.

    ``[1e16, 1.0, -1e16]`` is the textbook case: 3.11 loses the 1.0 to
    rounding and returns 0.0, and 3.12's compensation recovers it. Ten 0.1s
    is the everyday one. Neither result is wrong. They are different, and
    that is enough to split a run.
    """
    assert ordered_sum([1e16, 1.0, -1e16]) == 0.0
    assert ordered_sum([0.1] * 10) == 0.9999999999999999
    assert ordered_sum([0.1] * 10, 0.5) == _left_to_right([0.1] * 10, 0.5)


def test_ordered_sum_matches_a_left_to_right_loop_bit_for_bit():
    """Across magnitudes and signs, the same bits as the loop 3.11 ran."""
    draw = random.Random(2024)
    for _ in range(500):
        scale = 10.0 ** draw.uniform(-8, 12)
        values = [draw.gauss(0.0, scale) for _ in range(draw.randint(1, 60))]
        got = ordered_sum(values)
        want = _left_to_right(values)
        assert got.hex() == want.hex(), (values, got, want)


def test_ordered_sum_of_nothing_is_the_integer_zero():
    """``sum([])`` is ``0``, an int. Returning ``0.0`` would change what a
    JSON document or a prompt prints for an empty book."""
    result = ordered_sum([])
    assert result == 0 and type(result) is int


# --------------------------------------------------------------------------
# The guard
# --------------------------------------------------------------------------

def _counting(call: ast.Call) -> bool:
    """``sum(1 for ...)`` or ``sum(len(x) for ...)``: integers, which every
    Python adds exactly, so the built-in is safe."""
    if len(call.args) != 1 or call.keywords:
        return False
    arg = call.args[0]
    if not isinstance(arg, (ast.GeneratorExp, ast.ListComp)):
        return False
    elt = arg.elt
    if isinstance(elt, ast.Constant) and type(elt.value) is int:
        return True
    return (isinstance(elt, ast.Call) and isinstance(elt.func, ast.Name)
            and elt.func.id == "len")


def test_the_package_adds_floats_only_through_ordered_sum():
    """Every built-in ``sum()`` in the package is a count.

    Read from source, so a module added later is covered without anyone
    remembering this file. A syntactic check cannot see types, so the rule
    is strict: anything that is not visibly a count goes through
    ``ordered_sum``, which gives the same answer as ``sum()`` for integers
    too.
    """
    offenders = []
    for module in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "sum" and not _counting(node)):
                offenders.append(
                    f"{module.relative_to(PACKAGE)}:{node.lineno}")
    assert not offenders, (
        "built-in sum() over floats gives different last bits on Python "
        "3.11 and 3.12+, and a last bit is enough to change an agent's "
        "order. Use tradefloor._arith.ordered_sum here:\n  "
        + "\n  ".join(offenders))


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------

def _agent_driven_run() -> tuple[list[str], list[str]]:
    """Two reference agents in an eight-name market for two days.

    Small, and still enough: before the fix the random baseline's orders
    and both scorecards already differed between 3.11 and 3.12 at this
    size. The log records every quantity with ``repr``, so one ulp shows.
    """
    universe = tf.Universe.random(8, seed=111)
    orders: list[str] = []

    class Logged:
        def __init__(self, agent, name):
            self.agent, self.name = agent, name

        def act(self, obs):
            out = self.agent.act(obs)
            orders.extend(f"{self.name} {obs.step} {ticker} {quantity!r}"
                          for ticker, quantity in sorted(out.items()))
            return out

    refs = tf.baselines.reference_agents()
    scores = tf.evaluate(
        {name: Logged(refs[name], name) for name in ("random", "buy_and_hold")},
        seed=7, universe=universe, days=2)
    cards = [f"{name} {field} {value!r}"
             for name in sorted(scores)
             for field, value in scores[name].as_dict().items()]
    return orders, cards


def _digest(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]


@pytest.fixture(scope="module")
def run():
    return _agent_driven_run()


def test_agent_orders_match_the_python_311_run(run):
    orders, _ = run
    assert len(orders) == 104
    assert _digest(orders) == ORDERS_SHA256, (
        f"the orders two reference agents sent differ from the Python 3.11 "
        f"run on Python {sys.version.split()[0]}. Something between the "
        "engine and the agent now adds floats differently. Write both logs "
        "with `python tests/test_python_versions.py log.txt` under 3.11 and "
        "here, and diff them: the first line that differs names the step "
        "and the ticker.")


def test_scorecards_match_the_python_311_run(run):
    _, cards = run
    assert _digest(cards) == SCORECARDS_SHA256, (
        f"the scorecards differ from the Python 3.11 run on Python "
        f"{sys.version.split()[0]}:\n  " + "\n  ".join(cards))


if __name__ == "__main__":
    orders, cards = _agent_driven_run()
    print(f"ORDERS_SHA256 = {_digest(orders)!r}")
    print(f"SCORECARDS_SHA256 = {_digest(cards)!r}")
    if len(sys.argv) > 1:
        pathlib.Path(sys.argv[1]).write_bytes(
            "\n".join(orders + cards).encode("utf-8") + b"\n")
