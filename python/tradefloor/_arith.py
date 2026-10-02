"""Float addition that gives the same bits on every supported Python.

Python 3.12 changed the built-in ``sum()``: over floats it now carries a
Neumaier compensation term, so ``sum([1e16, 1.0, -1e16])`` is ``1.0`` on
3.12 and 3.13 and ``0.0`` on 3.11. Most of the time the two agree; when they
do not, they differ in the last bits. That was enough to break the one
promise this package makes: the same seed gives the same run. Measured on
0.8.5 with ``Universe.random(40, seed=111)``, seed 7 and ten days, the
random baseline's order quantities split between 3.11 and 3.12 from step 29
(``-4200.348357221354`` against ``...355``), 557 of 2,428 logged orders
differed, and every scorecard's P&L moved in its last digits. The engine
digests did not move, because the engine is Rust. What moved was the Python
arithmetic between the engine and the orders: net worth, gross exposure,
rebalancing weights.

:func:`ordered_sum` adds left to right with plain IEEE additions, which is
what ``sum()`` did through 3.11. So on 3.11 it returns exactly what
``sum()`` returned, bit for bit, including the integer ``0`` for an empty
input, and every number already certified on 3.11 stands. On 3.12 and later
it returns the 3.11 answer too. ``math.fsum`` would also be version-stable,
but it would change the numbers on 3.11 as well.

``tests/test_python_versions.py`` refuses any other use of the built-in
``sum()`` in the package except counting (``sum(1 for ...)``), whose
integers are exact on every version.
"""

from __future__ import annotations

from functools import reduce
from operator import add
from typing import Iterable

__all__ = ["ordered_sum"]


def ordered_sum(values: Iterable[float], start: float = 0) -> float:
    """``sum(values, start)`` as Python 3.11 computed it, on any version.

    ``reduce(add, ...)`` performs the same additions in the same order as
    3.11's ``sum()``, and on the same types: a float plus a float is one
    IEEE addition, an int start of ``0`` plus a float is that float, and a
    numpy scalar goes through its own ``__radd__`` exactly as it did inside
    ``sum()``. It runs the loop in C, so it costs about what ``sum()`` did.
    """
    return reduce(add, values, start)
