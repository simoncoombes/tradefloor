"""Train a Stable-Baselines3 PPO agent on TradingEnv and score it on held-out markets.

The study asks what PPO learns on `tradefloor.gym.TradingEnv` in a short,
fixed training budget, measured against three reference policies on the same
held-out seeds:

    cash                     every target weight 0
    equal weight             every target weight 1/n, rebalanced each step
    PPO before training      the same network at its initial weights
    PPO after training       the network after `timesteps` environment steps

Run it from a clone:

    pip install "tradefloor[rl]" stable-baselines3
    python examples/rl-ppo/ppo.py

The result table is printed and written to `artifacts/results.json` beside
this file. `README.md` holds the measured numbers and their limits.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import time
from typing import Any, Callable

import tradefloor as tf

#: The market. One roster for training and evaluation, so the held-out part
#: is the seed and therefore the path the market takes. Eight names and the
#: roster seed are the ones notebook 05 uses, so a reader of that notebook
#: meets the same companies here.
UNIVERSE_SIZE = 8
UNIVERSE_SEED = 7

#: Five days of six steps: 30 decisions an episode. Short episodes give PPO
#: more episodes per environment step, and each step already costs 65 ticks
#: of the engine.
DAYS = 5
CASH = 1_000_000.0

#: The training budget the README reports, in environment steps. PPO
#: collects `N_STEPS` steps, then updates the network, so a budget is
#: rounded up to a whole number of rollouts and `num_timesteps` in the
#: record is what ran. SB3's default rollout is 2,048 steps, which at about
#: 0.1 s a step here would make one update cost minutes. 512 steps is about
#: seventeen 30-step episodes per update, and `BATCH_SIZE` keeps eight
#: minibatches per epoch.
TIMESTEPS = 8_192
N_STEPS = 512
BATCH_SIZE = 64

#: The env's own generator draws every training episode's seed from
#: `TRAIN_SEED`. The evaluation seeds sit far from it, and `train` checks the
#: two sets share no seed, because an overlap would score the policy on the
#: markets it trained on.
TRAIN_SEED = 0
EVAL_SEEDS = tuple(range(10_000, 10_020))

#: The network's own seed. SB3 seeds torch, numpy and the action noise from
#: it, so two runs on one machine and one torch build give the same network.
POLICY_SEED = 0

HERE = pathlib.Path(__file__).resolve().parent


def _require_sb3() -> Any:
    try:
        import stable_baselines3
    except ImportError as exc:  # pragma: no cover - the message is the point
        raise SystemExit(
            "This study needs Stable-Baselines3 and the env's extra:\n"
            '    pip install "tradefloor[rl]" stable-baselines3') from exc
    return stable_baselines3


def universe() -> list:
    return list(tf.Universe.random(UNIVERSE_SIZE, seed=UNIVERSE_SEED))


def make_env(seed: int) -> Any:
    """One TradingEnv, with its reward in basis points of starting cash.

    The env pays P&L in dollars, so a step on a $1M account pays
    thousands. PPO's value loss is a squared error on that scale, and SB3
    clips the gradient norm to 0.5, so dollar rewards leave the value head
    learning far slower than the policy. Dividing by cash and multiplying by
    1e4 changes the units and nothing else: every comparison below is in the
    same units, and `evaluate` reports returns from the env's own net worth.
    """
    import gymnasium
    from tradefloor.gym import TradingEnv

    env = TradingEnv(universe=universe(), seed=seed, days=DAYS, cash=CASH)
    return gymnasium.wrappers.TransformReward(
        env, lambda r: float(r) / CASH * 1e4)


class _SeedLog:
    """Records the seed of every episode a wrapped env starts."""

    def __init__(self) -> None:
        self.seeds: list[int] = []

    def wrap(self, env: Any) -> Any:
        import gymnasium
        log = self

        class Logged(gymnasium.Wrapper):
            def reset(self, **kwargs):
                obs, info = self.env.reset(**kwargs)
                log.seeds.append(int(info["seed"]))
                return obs, info

        return Logged(env)


def new_model(env: Any, n_steps: int = N_STEPS) -> Any:
    sb3 = _require_sb3()
    import torch
    # One thread: the result then depends on the torch build and the
    # machine, and not on how many cores the run happened to get.
    torch.set_num_threads(1)
    return sb3.PPO("MlpPolicy", env, n_steps=n_steps,
                   batch_size=min(BATCH_SIZE, n_steps), seed=POLICY_SEED,
                   verbose=0, device="cpu")


def train(timesteps: int = TIMESTEPS, n_steps: int = N_STEPS
          ) -> tuple[Any, Any, list[int], float]:
    """Return the untrained model, the trained one, training seeds, seconds."""
    log = _SeedLog()
    env = log.wrap(make_env(TRAIN_SEED))
    untrained = new_model(make_env(TRAIN_SEED), n_steps)
    model = new_model(env, n_steps)
    start = time.perf_counter()
    model.learn(total_timesteps=timesteps)
    seconds = time.perf_counter() - start
    overlap = set(log.seeds) & set(EVAL_SEEDS)
    if overlap:
        raise RuntimeError(
            f"training met evaluation seeds {sorted(overlap)}; pick others")
    return untrained, model, sorted(set(log.seeds)), seconds


def run_episode(policy: Callable[[Any], Any], seed: int) -> dict[str, float]:
    """One held-out episode. Return its P&L and its mean gross exposure.

    The return comes from the env's net worth, not the summed reward, so it
    is the account's own number in percent of starting cash.
    """
    import numpy as np

    env = make_env(seed)
    obs, _ = env.reset(seed=seed)
    gross = []
    done = False
    while not done:
        obs, _, terminated, truncated, _ = env.step(policy(obs))
        done = terminated or truncated
        n = UNIVERSE_SIZE
        gross.append(float(np.abs(obs[n:2 * n]).sum()))
    inner = env.unwrapped
    worth = inner.portfolio.net_worth(inner.engine)
    return {"return_pct": (worth / CASH - 1.0) * 100.0,
            "gross": statistics.fmean(gross)}


def policies(untrained: Any, trained: Any) -> dict[str, Callable[[Any], Any]]:
    import numpy as np

    zero = np.zeros(UNIVERSE_SIZE)
    equal = np.full(UNIVERSE_SIZE, 1.0 / UNIVERSE_SIZE)
    return {
        "cash": lambda obs: zero,
        "equal weight": lambda obs: equal,
        "PPO before training":
            lambda obs: untrained.predict(obs, deterministic=True)[0],
        "PPO after training":
            lambda obs: trained.predict(obs, deterministic=True)[0],
    }


def evaluate(untrained: Any, trained: Any,
             seeds: tuple[int, ...] = EVAL_SEEDS) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, policy in policies(untrained, trained).items():
        runs = [run_episode(policy, seed) for seed in seeds]
        returns = [r["return_pct"] for r in runs]
        out[name] = {
            "returns_pct": returns,
            "mean_return_pct": statistics.fmean(returns),
            "stdev_return_pct": statistics.stdev(returns) if len(returns) > 1 else 0.0,
            "mean_gross": statistics.fmean(r["gross"] for r in runs),
        }
    trained_r = out["PPO after training"]["returns_pct"]
    out["PPO after training"]["seeds_above"] = {
        ref: sum(a > b for a, b in zip(trained_r, out[ref]["returns_pct"]))
        for ref in ("cash", "equal weight", "PPO before training")}
    return out


def table(results: dict[str, Any], n_seeds: int) -> str:
    lines = [f"{'policy':<22}{'mean %':>9}{'stdev %':>9}{'gross':>8}",
             "-" * 48]
    for name, r in results.items():
        lines.append(f"{name:<22}{r['mean_return_pct']:>9.3f}"
                     f"{r['stdev_return_pct']:>9.3f}{r['mean_gross']:>8.2f}")
    above = results["PPO after training"]["seeds_above"]
    lines.append("")
    lines.append(
        f"PPO after training beat cash on {above['cash']}, equal weight on "
        f"{above['equal weight']} and its untrained self on "
        f"{above['PPO before training']} of {n_seeds} held-out seeds.")
    return "\n".join(lines)


def main(timesteps: int = TIMESTEPS, seeds: tuple[int, ...] = EVAL_SEEDS,
         write: bool = True, n_steps: int = N_STEPS) -> dict[str, Any]:
    sb3 = _require_sb3()
    import gymnasium
    import torch

    untrained, trained, train_seeds, seconds = train(timesteps, n_steps)
    results = evaluate(untrained, trained, seeds)
    record = {
        "timesteps": timesteps,
        "timesteps_run": int(trained.num_timesteps),
        "n_steps": n_steps,
        "train_seconds": round(seconds, 1),
        "train_episodes": len(train_seeds),
        "eval_seeds": list(seeds),
        "days": DAYS,
        "universe": {"size": UNIVERSE_SIZE, "seed": UNIVERSE_SEED},
        "preset": tf.ModelParams.from_preset().fingerprint,
        "versions": {"tradefloor": tf.__version__,
                     "stable_baselines3": sb3.__version__,
                     "torch": torch.__version__,
                     "gymnasium": gymnasium.__version__},
        "results": results,
    }
    print(f"{trained.num_timesteps} training steps over {len(train_seeds)} episodes in "
          f"{seconds:.0f} s; {len(seeds)} held-out seeds, {DAYS} days each.\n")
    print(table(results, len(seeds)))
    if write:
        out = HERE / "artifacts" / "results.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {out.relative_to(HERE.parent.parent)}")
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timesteps", type=int, default=TIMESTEPS,
                        help=f"PPO training steps (default {TIMESTEPS})")
    main(parser.parse_args().timesteps)
