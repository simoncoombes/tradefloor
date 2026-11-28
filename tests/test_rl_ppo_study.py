"""The PPO study under `examples/rl-ppo/` runs, and its numbers are its own.

Behind `TRADEFLOOR_SLOW_TESTS=1`, and skipped without Stable-Baselines3,
which no extra of this package installs. The slow job in
`.github/workflows/suite.yml` installs it, so in CI the study runs.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib

import pytest

STUDY = (pathlib.Path(__file__).resolve().parent.parent
         / "examples" / "rl-ppo" / "ppo.py")

SLOW = pytest.mark.skipif(
    not (os.environ.get("TRADEFLOOR_SLOW_TESTS")
         or os.environ.get("PRETIUM_SLOW_TESTS")),
    reason="trains a network; set TRADEFLOOR_SLOW_TESTS=1 to run")


def _study():
    spec = importlib.util.spec_from_file_location("rl_ppo_study", STUDY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@SLOW
def test_the_ppo_study_trains_and_scores_on_held_out_seeds():
    """A short run of ppo.py: four policies, held-out seeds, a repeatable result.

    Two rollouts of 128 steps, so the network updates twice. That checks
    the seed bookkeeping, the evaluation and the record without the
    study's full budget. The second run checks that the same seeds give the
    same numbers, which is the claim the README's table rests on.
    """
    pytest.importorskip("stable_baselines3")
    pytest.importorskip("gymnasium")
    study = _study()
    seeds = study.EVAL_SEEDS[:2]

    first = study.main(timesteps=256, seeds=seeds, write=False, n_steps=128)
    results = first["results"]
    assert set(results) == {"cash", "equal weight", "PPO before training",
                            "PPO after training"}
    for r in results.values():
        assert len(r["returns_pct"]) == len(seeds)
    assert all(abs(x) < 1e-9 for x in results["cash"]["returns_pct"])
    assert results["cash"]["mean_gross"] == 0.0
    assert results["equal weight"]["mean_gross"] > 0.9
    assert first["train_episodes"] >= 1
    assert first["eval_seeds"] == list(seeds)
    assert first["timesteps_run"] == 256

    again = study.main(timesteps=256, seeds=seeds, write=False, n_steps=128)
    assert again["results"] == results
