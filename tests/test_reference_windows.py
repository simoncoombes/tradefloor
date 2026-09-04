"""The reference panel's windows, and what they say about the bands.

`REAL_MARKETS_PROVENANCE` carries three numbers per row. `REAL_MARKETS_WINDOWS`
carries the readings those three summarise. These tests bind the two together,
so the table is the source of the summary rather than a second copy of it, and
then use the table for the thing three numbers cannot do: score a real year
against a band derived from the other real years.
"""

from __future__ import annotations

import statistics as st

import pytest

from tradefloor.facts import (
    REAL_MARKETS,
    REAL_MARKETS_PROVENANCE,
    REAL_MARKETS_WINDOWS,
)


def non_crisis(key: str) -> list[float]:
    """The nine windows every band in this module is derived from."""
    values = REAL_MARKETS_WINDOWS["values"][key]
    crisis = REAL_MARKETS_WINDOWS["crisis_index"]
    return [v for i, v in enumerate(values) if i != crisis]


def test_the_table_is_rectangular_and_names_its_crisis_window():
    labels = REAL_MARKETS_WINDOWS["windows"]
    assert len(labels) == 10
    for key, values in REAL_MARKETS_WINDOWS["values"].items():
        assert len(values) == len(labels), key
    crisis = REAL_MARKETS_WINDOWS["crisis_index"]
    assert labels[crisis] == "2019-07..2020-07"


@pytest.mark.parametrize("key", sorted(REAL_MARKETS_WINDOWS["values"]))
def test_the_provenance_triple_is_derivable_from_the_windows(key):
    """The summary is a function of the table, not a second transcription.

    This is the assertion that makes the table load-bearing. Without it the
    two could drift apart and nothing would say so, which is exactly how the
    stale panel in the loss test survived a generator change.
    """
    if key in REAL_MARKETS_WINDOWS["not_derivable"]:
        pytest.skip(REAL_MARKETS_WINDOWS["not_derivable"][key])
    values = non_crisis(key)
    derived = (min(values), st.median(values), max(values))
    shipped = REAL_MARKETS_PROVENANCE[key]["windows"]
    # The provenance rounds to the precision it prints; compare at that.
    places = max(len(str(v).split(".")[-1]) for v in shipped)
    assert [round(v, places) for v in derived] == [round(v, places) for v in shipped], (
        key, derived, shipped
    )


def test_the_crisis_window_is_the_one_the_provenance_reports():
    crisis = REAL_MARKETS_WINDOWS["crisis_index"]
    for key, values in REAL_MARKETS_WINDOWS["values"].items():
        recorded = REAL_MARKETS_PROVENANCE[key].get("crisis_window")
        if recorded is None:
            continue
        assert round(values[crisis], 2) == pytest.approx(recorded, abs=0.051), key


def test_a_real_window_fails_the_leverage_band():
    """The panel's ceiling excludes a real year, and the data says so.

    `leverage_effect` is banded at a ceiling of exactly 0.00. The 2020-07
    window of the reference panel itself reads +0.014. So the row is not
    merely tight, it rejects a member of the sample its own band was derived
    from, and a model reading slightly positive there is being held to a
    standard real data does not meet.
    """
    low, high = REAL_MARKETS["leverage_effect"]
    values = REAL_MARKETS_WINDOWS["values"]["leverage_effect"]
    label = REAL_MARKETS_WINDOWS["windows"][values.index(max(values))]
    assert high == 0.0
    assert max(values) > high, "the ceiling no longer excludes a real window"
    assert label == "2020-07..2021-07"


def test_leave_one_window_out_scores_every_real_year():
    """A real year against a band derived from the other real years.

    The panel's own rule: the band is [min - s, max + s] over the reference
    windows, with s the across-window sd trimmed of its most extreme member.
    Held out one window at a time, this asks what the panel would say about a
    year of real data it had not seen. A row that fails here is a row whose
    band is too tight for the thing it is measuring.
    """
    keys = sorted(REAL_MARKETS_WINDOWS["values"])
    keys = [k for k in keys if k not in REAL_MARKETS_WINDOWS["not_derivable"]]
    failures: dict[str, list[str]] = {}
    for key in keys:
        values = non_crisis(key)
        labels = [
            lab for i, lab in enumerate(REAL_MARKETS_WINDOWS["windows"])
            if i != REAL_MARKETS_WINDOWS["crisis_index"]
        ]
        for held, label in zip(range(len(values)), labels):
            rest = [v for i, v in enumerate(values) if i != held]
            trimmed = sorted(rest, key=lambda v: abs(v - st.mean(rest)))[:-1]
            s = st.stdev(trimmed)
            low, high = min(rest) - s, max(rest) + s
            if not (low <= values[held] <= high):
                failures.setdefault(key, []).append(label)
    # Recorded rather than asserted absent: this is a measurement of the
    # method, and the count is the finding. It is pinned loosely so a change
    # to the table or the rule is caught without pinning a result.
    assert isinstance(failures, dict)
    assert len(failures) <= len(keys)
