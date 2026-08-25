"""A checkpoint resumes the market it froze, whichever preset that was.

Through 0.1.4 it did not: `Checkpoint.of` decided whether to carry the model
by comparing the engine's fingerprint against `ModelParams.from_preset()`,
whose no-arg form returned pt-v1 while `Engine` defaults to pt-v3. A pt-v1
run therefore checkpointed with `model=None` and resumed as pt-v3, silently
replaying a different market. The same mistake had already been made and
fixed once for `model_preset()`.

These tests pin the property rather than the fix: every shipped preset must
survive a round trip, and the two defaults must agree.
"""

from __future__ import annotations

import pretium as pt
from pretium.checkpoint import Checkpoint

PRESETS = ("pt-v1", "pt-v2", "pt-v3", "pt-v4", "pt-v5", "pt-v6", "pt-v7", "pt-v8")


def test_the_two_defaults_agree() -> None:
    """`from_preset()` and `Engine(...)` must name the same model."""
    engine = pt.Engine(seed=1, universe=pt.Universe.random(3, seed=1))
    assert pt.ModelParams.from_preset().fingerprint == engine.model_fingerprint
    assert pt.model_preset()["name"] == engine.model_fingerprint


def test_every_shipped_preset_survives_a_round_trip() -> None:
    universe = list(pt.Universe.random(6, seed=1))
    for name in PRESETS:
        engine = pt.Engine(seed=3, universe=pt.Universe(universe), model=name)
        engine.run_days(3)
        resumed = Checkpoint.of(engine, universe=universe, seed=3).resume()
        assert resumed.model_fingerprint == name, (
            f"a {name} run resumed as {resumed.model_fingerprint}: the "
            "checkpoint replayed a market it did not freeze"
        )


def test_a_resumed_run_continues_the_same_trajectory() -> None:
    """Fingerprints agreeing is necessary; identical prices is the point."""
    universe = list(pt.Universe.random(6, seed=1))
    for name in ("pt-v1", "pt-v3", "pt-v8"):
        original = pt.Engine(seed=3, universe=pt.Universe(universe), model=name)
        original.run_days(3)
        resumed = Checkpoint.of(original, universe=universe, seed=3).resume()
        assert list(resumed.prices()) == list(original.prices())
        original.run_days(2)
        resumed.run_days(2)
        assert list(resumed.prices()) == list(original.prices()), name


def test_a_custom_model_is_carried() -> None:
    universe = list(pt.Universe.random(4, seed=2))
    custom = pt.ModelParams.from_preset("pt-v3", garch_alpha=0.12)
    engine = pt.Engine(seed=5, universe=pt.Universe(universe), model=custom)
    engine.run_days(2)
    check = Checkpoint.of(engine, universe=universe, seed=5)
    assert check.model is not None
    assert check.resume().model_fingerprint == custom.fingerprint
