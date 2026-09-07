"""One estimator, one window, one debias -- and the same FUNCTION on both sides.

The model's `vix_ar1_debiased` row is a per-seed 252-day reading. It was
compared for months against **0.976**, the whole-span autocorrelation of
^VIX: one series, 8,960 bars, computed by different code over a different
length. On a like-for-like ruler -- the same estimator on the same tape, cut
into 252-session windows -- the tape reads 0.9299 debiased, so every identity
arm on the record is MORE persistent than the real VIX by 0.04 to 0.05 rather
than short of it, and `id-v18-d100`'s 0.9763 "MET" compared two estimators.

The repair is not a better number. It is that the tape's ruler and the
model's row go through ONE function object, at the horizon each is taken at,
so a second implementation cannot drift away from the first and a 504-day run
cannot be read against the 252-day figure.

Numeric agreement is not the property. The whole-span figure agreed with
itself perfectly and was still the wrong ruler, so
`test_a_numerically_identical_twin_is_rejected` builds a private copy that
agrees with `facts.level_ar1` to the last bit and watches the guard reject
it. No test here pins the ruler's value: they assert the derivation, so a
tape refetch that moves it fails honestly rather than failing this file.
"""

import math
import os
import random
import statistics
import sys
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "tools" / "calibration"))

import tradefloor as pt  # noqa: E402
import tradefloor.facts as facts  # noqa: E402
import vix_ar1_ruler as ruler  # noqa: E402
from tradefloor import ValidationError  # noqa: E402

#: The number on the record, and what it is: the WHOLE-SPAN reading. Carried
#: here as the defect's own value so the tests can show it is a different
#: quantity rather than a stale one.
WHOLE_SPAN_ON_THE_RECORD = 0.976

#: A deterministic stand-in for a VIX path: mean-reverting, positive, and
#: persistent enough that the lag-one reading is nowhere near a boundary. Used
#: where the property under test is which function ran, not what it returned.
def mean_reverting(n, seed, level=18.0, phi=0.93, sigma=1.4):
    rng = random.Random(seed)
    x, out = level, []
    for _ in range(n):
        x = level + phi * (x - level) + rng.gauss(0.0, sigma)
        out.append(abs(x))
    return out


def runs_at(length, count, seed=0):
    return [mean_reverting(length, seed + i) for i in range(count)]


def test_the_tape_path_and_the_model_path_resolve_one_estimator(monkeypatch):
    """The whole defect, asserted: same function, different data.

    The tape side is `vix_ar1_ruler`'s own per-window step; the model side is
    a real run's VIX level series through `facts.vix_levels`. Both are watched
    through one patched attribute, so a path carrying its own copy of the
    estimator records nothing and fails here.
    """
    seen = []
    original = facts.level_ar1

    def recording(series):
        seen.append(len(series))
        return original(series)

    monkeypatch.setattr(facts, "level_ar1", recording)

    # THE TAPE SIDE. `blocks` is the cut the ruler is derived on; the tape
    # itself is vendor data, so the shape is supplied and the code path is
    # the shipped one.
    tape = mean_reverting(120, seed=101)
    blocks = ruler.blocks(tape, 40)
    assert len(blocks) == 3
    tape_ruler = facts.median_level_ar1(blocks, length=40)
    assert seen == [40, 40, 40]

    # THE MODEL SIDE. A real engine, its own macro table, no reimplementation
    # of anything on the way.
    engine = pt.Engine(seed=11, universe=pt.Universe.random(12, seed=7),
                       model="pt-v16")
    engine.run_days(60)
    levels = facts.vix_levels(engine.macro_table())
    assert len(levels) == 60
    model_row = facts.median_level_ar1([levels], length=60)
    assert seen == [40, 40, 40, 60]

    # Both are readings of the same quantity, which is the whole point of
    # them sharing a function.
    assert 0.0 < tape_ruler < 1.2 and 0.0 < model_row < 1.2


def test_a_numerically_identical_twin_is_rejected(monkeypatch):
    """What this guard rejects, built and watched failing.

    Two twins, because writing them found something. `exact` takes the mean
    with `statistics.mean` and is bit-identical to `facts.level_ar1` on every
    run here. `as_shipped_in_the_design_scripts` is the copy the five
    programme scripts actually carry, which differs in one respect nobody
    would think to state: it takes `statistics.fmean`. It agrees to 1.1e-16
    and it is NOT bit-identical on every run.

    So "two functions that agree numerically" is not even true of the pair
    this project has been running, and it would not be the property to want
    if it were: the whole-span 0.976 agreed with itself perfectly and was
    still the wrong ruler. The property is that one object runs, and both
    twins are invisible to the check the shipped path passes.
    """
    def exact(series):
        values = [float(v) for v in series]
        mean = statistics.mean(values)
        den = sum((v - mean) ** 2 for v in values)
        return sum((values[i] - mean) * (values[i + 1] - mean)
                   for i in range(len(values) - 1)) / den

    def as_shipped_in_the_design_scripts(series):
        values = [float(v) for v in series]
        mean = statistics.fmean(values)
        den = sum((v - mean) ** 2 for v in values)
        return sum((values[i] - mean) * (values[i + 1] - mean)
                   for i in range(len(values) - 1)) / den

    series = runs_at(252, 6, seed=200)
    assert [exact(r) for r in series] == [facts.level_ar1(r) for r in series]
    bitwise = [as_shipped_in_the_design_scripts(r) == facts.level_ar1(r)
               for r in series]
    assert not all(bitwise), (
        "the design scripts' fmean copy agreed bit for bit on all six runs "
        "here; the claim in this docstring needs re-measuring, not the "
        "assertion below relaxing")
    for run in series:
        assert as_shipped_in_the_design_scripts(run) == pytest.approx(
            facts.level_ar1(run), abs=1e-15)

    seen = []
    original = facts.level_ar1
    monkeypatch.setattr(facts, "level_ar1",
                        lambda s: (seen.append(len(s)), original(s))[1])

    for twin in (exact, as_shipped_in_the_design_scripts):
        twin_ruler = facts.debias_ar1(
            statistics.median([twin(r) for r in series]), 252)
        assert seen == []                              # REJECTED: nothing ran
        assert twin_ruler == pytest.approx(
            facts.debias_ar1(statistics.median(
                [original(r) for r in series]), 252), abs=1e-15)

    shipped = facts.median_level_ar1(series, length=252)
    assert seen == [252] * 6                           # accepted
    assert 0.0 < shipped < 1.2


def test_the_estimator_is_the_panels_own_autocorrelation(monkeypatch):
    """`level_ar1` is `_autocorrelation` at lag one, called not copied.

    The panel's return rows and the VIX's level row read one autocorrelation
    between them. Asserted by patching the callee, because equal numbers are
    what a copy also produces.
    """
    run = mean_reverting(252, seed=7)
    assert facts.level_ar1(run) == facts._autocorrelation(run, 1)

    monkeypatch.setattr(facts, "_autocorrelation",
                        lambda series, lag: 0.5 if lag == 1 else 0.0)
    assert facts.level_ar1(run) == 0.5


def test_the_ruler_is_derived_from_the_recorded_windows():
    """Every horizon's ruler recomputes from its recorded tape readings.

    The ruler is not a value with a derivation written beside it; it IS the
    derivation, run at import. A recorded window edited without the ruler
    moving is impossible rather than unlikely.
    """
    assert set(facts.REAL_VIX_AR1) == set(facts.REAL_VIX_AR1_WINDOWS)
    for days, windows in facts.REAL_VIX_AR1_WINDOWS.items():
        assert len(windows) >= 4, days
        assert facts.REAL_VIX_AR1[days] == facts.median_ar1_debiased(
            windows, n=days)
        # The debias is at the WINDOW length, not at the window count.
        raw = statistics.median(windows)
        assert facts.REAL_VIX_AR1[days] == pytest.approx(
            raw + (1.0 + 3.0 * raw) / days, abs=1e-15)


def test_the_ruler_is_not_written_down_beside_its_derivation():
    """No literal spelling of the ruler anywhere in `facts`.

    A derived value with its own number in a comment two lines up has two
    spellings, and the stale one is the one a reader copies. This is the
    assertion that keeps `REAL_VIX_AR1` derived rather than documented.
    """
    source = Path(facts.__file__).read_text(encoding="utf-8")
    for days, value in facts.REAL_VIX_AR1.items():
        for places in (4, 5, 6, 7):
            spelling = f"%.{places}f" % value
            assert spelling not in source, (
                f"the {days}-day ruler is written out as {spelling} in "
                f"facts.py; it is derived from REAL_VIX_AR1_WINDOWS and has "
                f"no second spelling")


def test_the_whole_span_figure_is_a_different_quantity():
    """0.976 is not a stale value of this ruler. It is another statistic.

    Measured against the tape's own spread across windows, so the claim is
    that the two disagree by far more than the sampling error of either --
    which is what makes the substitution a wrong-ruler error rather than a
    rounding one.
    """
    windows = facts.REAL_VIX_AR1_WINDOWS[facts.CERTIFIED_HORIZON_DAYS]
    se = statistics.stdev(windows) / math.sqrt(len(windows))
    gap = WHOLE_SPAN_ON_THE_RECORD - facts.real_vix_ar1(
        facts.CERTIFIED_HORIZON_DAYS)
    assert gap > 0.0
    assert gap > 4.0 * se, (
        f"gap {gap:.4f} against a window standard error of {se:.4f}")


def test_every_registered_horizon_has_a_persistence_ruler():
    """A horizon cannot be half-added.

    `RULERS_BY_HORIZON` already refuses a horizon with bands and no noise
    scale. A horizon with bands and no persistence ruler would send a 504-day
    VIX row back to the 252-day figure, which is this file's defect at the
    next length.
    """
    assert sorted(facts.REAL_VIX_AR1) == sorted(facts.RULERS_BY_HORIZON)
    for days, row in facts.RULERS_BY_HORIZON.items():
        assert row["vix_ar1"] == facts.real_vix_ar1(days)
        assert row["vix_ar1_name"] == f"facts.REAL_VIX_AR1[{days}]"
    # And the two are not interchangeable: the longer window reads more
    # persistent than the shorter one by more than either window's own se.
    short, long = facts.real_vix_ar1(252), facts.real_vix_ar1(504)
    se504 = (statistics.stdev(facts.REAL_VIX_AR1_WINDOWS[504])
             / math.sqrt(len(facts.REAL_VIX_AR1_WINDOWS[504])))
    assert long - short > 4.0 * se504


def test_a_horizon_without_a_ruler_is_refused_by_name():
    """Not the 252-day figure with a shrug. The horizons that have one, named."""
    for days in (60, 180, 1260, 2520):
        with pytest.raises(ValidationError) as excinfo:
            facts.real_vix_ar1(days, what="a shadow run")
        message = str(excinfo.value)
        assert "a shadow run" in message
        assert str(sorted(facts.REAL_VIX_AR1)) in message
        assert "vix_ar1_ruler.py" in message


def test_a_run_of_the_wrong_length_is_refused():
    """The debias divides by a length, so the length has to be the data's.

    Constructed both ways round, because a check that only refuses the short
    side passes a 504-day panel read at 252.
    """
    with pytest.raises(ValidationError, match="504"):
        facts.median_level_ar1(runs_at(504, 3, seed=300), length=252)
    with pytest.raises(ValidationError, match="252"):
        facts.median_level_ar1(runs_at(252, 3, seed=400), length=504)
    # The matched pairing computes, so the refusal is about the horizons and
    # not about the call being broken in general.
    assert 0.0 < facts.median_level_ar1(runs_at(252, 3, seed=400),
                                        length=252) < 1.2


def test_a_constant_run_is_refused_rather_than_read_as_no_persistence():
    """A pinned VIX has no lag-one reading, and 0.0 is not one.

    `_autocorrelation` returns 0.0 for a constant series, which is a correct
    answer to a different question. Inside a median across runs it is a
    reading that drags the ruler down without announcing itself, and the size
    of that is measured here rather than asserted.
    """
    assert facts._autocorrelation([5.0] * 10, 1) == 0.0
    with pytest.raises(ValidationError, match="constant"):
        facts.level_ar1([5.0] * 10)
    with pytest.raises(ValidationError, match="three observations"):
        facts.level_ar1([5.0, 6.0])

    honest = runs_at(252, 9, seed=500)
    with pytest.raises(ValidationError):
        facts.median_level_ar1(honest + [[7.0] * 252], length=252)
    silent = statistics.median([facts.level_ar1(r) for r in honest] + [0.0] * 3)
    assert silent < statistics.median([facts.level_ar1(r) for r in honest])


def test_the_debias_is_applied_at_the_window_length():
    """The correction is `(1 + 3 rho) / n` and n is the run length.

    Asserted as an identity rather than at a value, and checked to be the size
    the docstring claims: 0.015 at 252 and rho near 0.93, which is a third of
    the distance the wrong ruler put between the model and the tape.
    """
    for rho in (0.80, 0.9151, 0.95):
        for n in (252, 504):
            assert facts.debias_ar1(rho, n) == rho + (1.0 + 3.0 * rho) / n
    assert facts.debias_ar1(0.93, 252) - 0.93 == pytest.approx(0.0150, abs=5e-4)
    assert facts.debias_ar1(0.93, 504) - 0.93 == pytest.approx(0.0075, abs=5e-4)
    with pytest.raises(ValidationError):
        facts.debias_ar1(0.9, 1)


def test_the_recorded_windows_reproduce_from_the_tape():
    """The tool's cut against what `facts` records, when the tape is present.

    ^VIX is vendor data and is not committed, so this SKIPS on a clean
    checkout and on CI -- named here rather than left implicit, because a
    skipped test is not a passed one. `tools/calibration/vix_ar1_ruler.py`
    prints the same comparison on any box that has the cache.
    """
    cache = (Path(ruler.data.CACHE)
             / f"{ruler.SYMBOL.strip('^')}_{ruler.START}_{ruler.END}.json")
    if not cache.exists():
        pytest.skip(f"the ^VIX tape is not cached at {cache}; copy "
                    f"tools/shadow/data or refetch to run this")
    _, levels, _, _ = ruler.closes(ruler.SYMBOL, ruler.START, ruler.END)
    for days, recorded in facts.REAL_VIX_AR1_WINDOWS.items():
        derived = [facts.level_ar1(b) for b in ruler.blocks(levels, days)]
        assert len(derived) == len(recorded), days
        for got, want in zip(derived, recorded):
            assert got == pytest.approx(want, abs=5e-7)
        assert facts.median_level_ar1(ruler.blocks(levels, days),
                                      length=days) == pytest.approx(
            facts.real_vix_ar1(days), abs=5e-7)
