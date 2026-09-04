"""Shapley shares of a preset change, held to their identities.

`tools/calibration/shapley.py` divides the change from one preset to
another among named groups of dials by exact enumeration of every subset at
the same seeds. Four things about it can be wrong quietly: the shares can
fail to add up to the change they divide, a group carrying nothing can be
handed a share, the guard that makes a difference a parameter effect can
pass a subset that saw different noise, and the hand declaration of the
groups can drift from the preset table it describes. Each is stated here.

The simulation-backed tests run a two-group toy at four names, 32 days and
two seeds: eight panels, a few seconds. The toy proves identities rather
than effects, and nothing here is a measurement of pt-v16.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "tools" / "calibration"))

import tradefloor  # noqa: E402

pytest.importorskip("pyarrow", reason="facts.measure reads bars through Arrow")
shapley = pytest.importorskip("shapley",
                              reason="calibration tooling is not packaged")

#: Two dials that both move the panel at four names and 32 days. The QE
#: gain was the first choice and moved nothing at that horizon, which is a
#: fact about the macro chain rather than a defect; a toy whose groups are
#: inert proves the sum identity as 0 = 0.
TOY_GROUPS = {
    "trim": {"market_factor_sigma": 0.007593024924589399},
    "volume": {"volume_move_response": 1.0},
}
TOY_SEEDS = [1, 2]
TOY_DAYS = 32
TOY_UNIVERSE = (4, 1)


@pytest.fixture(scope="module")
def toy():
    rows = shapley.evaluate("pt-v14", TOY_GROUPS, TOY_SEEDS, TOY_DAYS,
                            TOY_UNIVERSE, workers=1, progress=lambda *_: None)
    crn = shapley.guard(shapley.distinct_rows(rows))
    summary = shapley.summarise(rows, TOY_GROUPS, base="pt-v14", target=None,
                                seeds=TOY_SEEDS, days=TOY_DAYS,
                                universe=TOY_UNIVERSE, crn=crn)
    return rows, crn, summary


def test_shares_sum_to_the_change_on_every_statistic(toy):
    """Efficiency, on the medians and on every seed, to 1e-9.

    Exact enumeration leaves rounding only, so the tolerance is a check on
    the arithmetic rather than on an estimate.
    """
    rows, _, summary = toy
    stats = summary["statistics"]
    assert stats, "the toy measured no statistic; the identity would be vacuous"
    for key in stats:
        total = sum(summary["shares"][name][key] for name in TOY_GROUPS)
        assert abs(total - summary["change"][key]) <= 1e-9, key
    per_seed = shapley.seed_values(rows, stats)
    for seed, table in per_seed.items():
        shares = shapley.shapley(table, list(TOY_GROUPS))
        for key in stats:
            change = (table[frozenset(TOY_GROUPS)][key]
                      - table[frozenset()][key])
            total = sum(shares[name][key] for name in TOY_GROUPS)
            assert abs(total - change) <= 1e-9, (seed, key)


def test_the_toy_groups_both_move_the_panel(toy):
    """The identity above is stated on a change, not on zero."""
    _, _, summary = toy
    for name in TOY_GROUPS:
        moved = [key for key in summary["statistics"]
                 if summary["shares"][name][key] != 0.0]
        assert moved, f"{name} moved nothing; the toy is vacuous"


def test_a_group_with_no_dials_contributes_zero():
    """An empty group's share is 0.0 on every statistic, to the bit.

    Two subsets whose unions are the same vector share one measurement, so
    the marginal of an empty group is a difference of identical floats
    rather than of two runs held equal by determinism.
    """
    groups = {"volume": TOY_GROUPS["volume"], "empty": {}}
    rows = shapley.evaluate("pt-v14", groups, TOY_SEEDS, TOY_DAYS,
                            TOY_UNIVERSE, workers=1, progress=lambda *_: None)
    stats, _ = shapley.measured_statistics(rows)
    values = shapley.subset_values(rows, stats)
    shares = shapley.shapley(values, list(groups))
    for key in stats:
        assert shares["empty"][key] == 0.0, key
        change = values[frozenset(groups)][key] - values[frozenset()][key]
        assert shares["volume"][key] == pytest.approx(change, abs=1e-12)
    # And the report says so, computed from the shares rather than typed:
    # a group at 0.0 to the bit on every seed changed no panel.
    crn = shapley.guard(shapley.distinct_rows(rows))
    summary = shapley.summarise(rows, groups, base="pt-v14", target=None,
                                seeds=TOY_SEEDS, days=TOY_DAYS,
                                universe=TOY_UNIVERSE, crn=crn)
    assert summary["inert"] == ["empty"]
    assert any("inert at this roster and horizon" in text
               and "empty" in text for text in summary["caveats"])


def test_every_subset_saw_the_same_market_draws(toy):
    """The premise of the pairing, measured rather than assumed."""
    _, crn, _ = toy
    assert crn["guarded_stream"] == "market"
    assert set(crn["market"]) == set(TOY_SEEDS)


def test_a_summary_built_without_the_guard_runs_it(toy):
    """No path to a share skips the guard."""
    rows, crn, _ = toy
    summary = shapley.summarise(rows, TOY_GROUPS, base="pt-v14", target=None,
                                seeds=TOY_SEEDS, days=TOY_DAYS,
                                universe=TOY_UNIVERSE)
    assert summary["crn"]["market"] == crn["market"]


def test_repeated_seeds_are_refused_before_any_panel():
    with pytest.raises(ValueError, match="seeds repeat"):
        shapley.evaluate("pt-v14", TOY_GROUPS, [1, 1], TOY_DAYS,
                         TOY_UNIVERSE, workers=1, progress=lambda *_: None)


def _row(overrides: dict, seed: int, market: int, economy: int) -> dict:
    return {
        "base": "pt-v14", "fingerprint": "custom-toy", "overrides": overrides,
        "seed": seed, "seconds": 0.0, "draws_consumed": market + economy,
        "draws_by_stream": {"market": market, "economy": economy,
                            "external": 0},
        "panel": {}, "unmeasured": [],
    }


def test_the_crn_guard_refuses_a_doctored_market_draw_count():
    """A subset whose market draws differ stops the run before any share.

    The fixture is two vectors at two seeds with equal market counts; one
    economy count differs, which the guard reports as data, since the
    macro chain may branch under a parameter change. Doctoring one market
    count is the failure the guard exists for, and it names the seed.
    """
    rows = [
        _row({}, 1, 1000, 20),
        _row({"qe_pe_gain": 0.0}, 1, 1000, 21),
        _row({}, 2, 1000, 20),
        _row({"qe_pe_gain": 0.0}, 2, 1000, 20),
    ]
    clean = shapley.guard(rows)
    assert clean["market"] == {1: 1000, 2: 1000}
    assert [d["seed"] for d in clean["economy_deviations"]] == [1]

    rows[1]["draws_by_stream"]["market"] = 1001
    with pytest.raises(AssertionError, match="seed 1: market"):
        shapley.guard(rows)


def test_the_declaration_holds_to_the_preset_table():
    """Every dial is a settable field, in one group, and the union is pt-v16.

    The union check is the fingerprint the library assigns: a coefficient
    set equal to a shipped preset carries that preset's name, so a
    declaration that reproduces pt-v16 from pt-v14 dial for dial is
    verified by the library rather than by this file's own comparison.
    """
    settable = set(tradefloor.ModelParams.settable())
    seen: dict[str, str] = {}
    for name, dials in shapley.GROUPS.items():
        for dial in dials:
            assert dial in settable, f"{name}: {dial} is not settable"
            assert dial not in seen, f"{dial} in {seen[dial]} and {name}"
            seen[dial] = name
    verified = shapley.verify_declaration(shapley.GROUPS, "pt-v14", "pt-v16")
    assert verified["fingerprint"] == "pt-v16"
    assert len(verified["union"]) == 19
    assert verified["inert"] == []


@pytest.mark.parametrize("groups, complaint", [
    ({"a": {"qe_pe_gain": 0.0}, "b": {"qe_pe_gain": 0.5}},
     "declared in both a and b"),
    ({"a": {"no_such_dial": 1.0}}, "not a settable"),
    ({"a": {"qe_pe_gain": "off"}}, "not a number"),
    ({"base": {"qe_pe_gain": 0.0}}, "never 'base'"),
    ({"a+b": {"qe_pe_gain": 0.0}}, "carries no"),
])
def test_a_wrong_declaration_is_refused(groups, complaint):
    with pytest.raises(ValueError, match=complaint):
        shapley.verify_declaration(groups, "pt-v14")


def test_a_union_that_misses_the_target_is_refused():
    """Dropping a group names the dials the target moves without it."""
    groups = {name: dials for name, dials in shapley.GROUPS.items()
              if name != "qe"}
    with pytest.raises(ValueError, match="moves qe_pe_gain and no group"):
        shapley.verify_declaration(groups, "pt-v14", "pt-v16")
    groups = dict(shapley.GROUPS)
    groups["extra"] = {"garch_alpha": 0.1}
    with pytest.raises(ValueError, match="leaves it at the pt-v14 value"):
        shapley.verify_declaration(groups, "pt-v14", "pt-v16")


def test_the_lattice_stops_at_nine_groups():
    ten = {f"g{i}": {} for i in range(10)}
    with pytest.raises(ValueError, match="budget of 9"):
        shapley.subsets(ten)
    nine = {f"g{i}": {} for i in range(9)}
    assert len(shapley.subsets(nine)) == 512
    with pytest.raises(ValueError, match="needs all 4"):
        shapley.shapley({frozenset(): {"s": 0.0},
                         frozenset("a"): {"s": 1.0},
                         frozenset("ab"): {"s": 3.0}})


def test_shapley_on_a_known_game():
    """Two players with an interaction, and three symmetric ones."""
    values = {
        frozenset(): {"s": 0.0},
        frozenset("a"): {"s": 1.0},
        frozenset("b"): {"s": 2.0},
        frozenset("ab"): {"s": 5.0},
    }
    shares = shapley.shapley(values, ["a", "b"])
    assert shares["a"]["s"] == pytest.approx(2.0)
    assert shares["b"]["s"] == pytest.approx(3.0)

    symmetric = {frozenset(s): {"s": float(len(s) ** 2)}
                 for s in ["", "a", "b", "c", "ab", "ac", "bc", "abc"]}
    shares = shapley.shapley(symmetric)
    assert all(shares[g]["s"] == pytest.approx(3.0) for g in "abc")


def test_the_report_prints_the_certified_value_the_band_and_the_caveats(toy):
    _, _, summary = toy
    text = shapley.report(summary)
    assert "certified" in text and "band" in text
    for name in TOY_GROUPS:
        assert f"\n  {name:16s}" in text
    assert "sum of shares" in text
    assert f"{len(TOY_SEEDS)} seed(s) against the 30" in text
    assert f"{TOY_DAYS} days against the certified horizon" in text
    assert "Universe.random(4, seed=1) against the certified" in text


def test_the_level_row_carries_a_share_and_no_band_verdict(toy):
    """The graded rows outside the shape set, walked by their own rules.

    The level row is paired per seed like a shape row, so it carries a
    share, and its value per subset is the MEAN the row is graded by
    rather than the median. Its certified value comes from the level
    table and was measured with the roster varying, which this tool never
    does, so the band verdict is withheld and the report says so instead
    of grading one protocol with the other's ruler. The pooled fear row
    has no per-seed value and carries no share at all. Before this, the
    tool raised KeyError on the level row's certified value, on every
    subset, which is the enumeration defect the split makes visible.
    """
    import statistics

    from tradefloor import envelope, facts

    rows, _, summary = toy
    assert "index_drift_pct" in summary["statistics"]
    for name in TOY_GROUPS:
        assert "index_drift_pct" in summary["shares"][name]
    per_seed = shapley.seed_values(rows, ["index_drift_pct"])
    base = [table[frozenset()]["index_drift_pct"] for table in per_seed.values()]
    assert summary["values"]["base"]["index_drift_pct"] == pytest.approx(
        statistics.fmean(base))
    assert facts.AGGREGATE["index_drift_pct"] == "mean"

    certified = summary["certified"]
    assert certified["protocol"]["index_drift_pct"] == "level"
    assert (certified["values"]["index_drift_pct"]
            == envelope.CERTIFIED_LEVEL["index_drift_pct"])
    assert certified["level_protocol"] == facts.LEVEL_PROTOCOL["roster"]
    assert all(certified["protocol"][key] == "held"
               for key in summary["statistics"] if key in facts.SHAPE)
    assert "index_drift_pct" in summary["withheld"]
    assert all(key not in facts.SHAPE for key in summary["withheld"])
    for name in TOY_GROUPS:
        assert summary["band_moves"][name]["index_drift_pct"] == {
            "alone": None, "last": None}
    assert any("band verdict withheld" in text and "index_drift_pct" in text
               for text in summary["caveats"])

    assert summary["pooled"] == ["fear_gauge_dn3"]
    assert "fear_gauge_dn3" not in summary["statistics"]
    assert "fear_gauge_dn3" not in summary["unmeasured"]
    assert "fear_gauge_dn3" not in summary["bands"]

    text = shapley.report(summary)
    assert "verdict withheld" in text
    assert "(level protocol)" in text
    assert "pooled over every seed's sessions" in text
    assert "fear_gauge_dn3" in text

    # And at the doubled horizon the level table has no measured value, so
    # the column reads None rather than the 252-day number.
    far = shapley.certified_column(2 * envelope.CERTIFIED_HORIZON_DAYS,
                                  ["annualised_vol_pct", "index_drift_pct"])
    assert far["values"]["annualised_vol_pct"] == envelope.MEASURED_504[
        "annualised_vol_pct"]
    assert far["values"]["index_drift_pct"] is None
    assert far["protocol"] == {"annualised_vol_pct": "held",
                               "index_drift_pct": "level"}


def test_seed_ranges_parse():
    assert shapley.parse_seeds("1-30") == list(range(1, 31))
    assert shapley.parse_seeds("5,1,3") == [1, 3, 5]
    assert shapley.parse_seeds("7") == [7]
    with pytest.raises(ValueError):
        shapley.parse_seeds(",")
