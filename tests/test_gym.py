"""The Gymnasium environment.

Both gymnasium and numpy are optional dependencies of the package, so these
skip cleanly rather than pretending the surface is untested.
"""

import pytest

import tradefloor

np = pytest.importorskip("numpy", reason="numpy is optional")

UNIVERSE = tradefloor.Universe.random(5, seed=5)


def make(**kw):
    from tradefloor.gym import TradingEnv

    params = dict(universe=UNIVERSE, seed=42, days=2, steps_per_day=3,
                  ticks_per_step=40, cash=2_000_000)
    params.update(kw)
    return TradingEnv(**params)


# --------------------------------------------------------------------------
# Conformance
# --------------------------------------------------------------------------

def test_it_passes_the_gymnasium_api_checker():
    """The library's own conformance test, not a hand-rolled substitute.

    It caught a genuine violation: reset must call ``super().reset(seed=seed)``
    so gymnasium's generator is seeded. The market draws nothing from that
    generator, because all of its randomness lives in the engine's PCG32
    stream. Since 0.8.5 a reset() given no seed draws the episode's seed from
    it.
    """
    gym = pytest.importorskip("gymnasium")
    import warnings
    from gymnasium.utils.env_checker import check_env

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        check_env(make(), skip_render_check=True)


def test_the_spaces_describe_the_arrays_actually_returned():
    gym = pytest.importorskip("gymnasium")
    env = make()
    obs, _ = env.reset(seed=1)
    assert env.observation_space.contains(obs)
    assert obs.shape == (2 * len(UNIVERSE) + 1,)
    assert env.action_space.shape == (len(UNIVERSE),)


# --------------------------------------------------------------------------
# Observations
# --------------------------------------------------------------------------

def test_observations_are_f64_and_c_contiguous():
    # What a Box space wants, and what the dtype rule requires everywhere.
    env = make()
    obs, _ = env.reset(seed=1)
    assert obs.dtype == np.float64
    assert obs.flags["C_CONTIGUOUS"]


def test_prices_are_returns_not_levels():
    """A level says nothing without its history.

    The range across a generated roster spans two orders of magnitude, so a
    policy fed levels would have to learn each instrument's price scale before
    it could learn anything about trading. Returns are stationary and
    comparable across names.
    """
    env = make()
    obs, _ = env.reset(seed=1)
    returns = obs[: len(UNIVERSE)]
    # First observation has no prior step, so returns are zero rather than NaN.
    assert np.all(returns == 0.0)

    obs, *_ = env.step(np.zeros(len(UNIVERSE)))
    returns = obs[: len(UNIVERSE)]
    assert np.all(np.isfinite(returns))
    assert np.all(np.abs(returns) < 1.0), "a log return of |1| is a 172% move"


def test_holdings_are_reported_as_fractions_of_net_worth():
    env = make()
    env.reset(seed=1)
    n = len(UNIVERSE)
    obs, *_ = env.step(np.full(n, 0.1))
    holdings = obs[n : 2 * n]
    assert np.all(np.isfinite(holdings))
    assert np.any(holdings > 0), "a positive target should produce a position"


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------

def test_actions_are_target_weights_so_a_bigger_weight_is_a_bigger_position():
    env = make()
    env.reset(seed=1)
    n = len(UNIVERSE)
    env.step(np.full(n, 0.05))
    small = abs(env.portfolio.positions[env.engine.tickers[0]].quantity)

    env.reset(seed=1)
    env.step(np.full(n, 0.30))
    large = abs(env.portfolio.positions[env.engine.tickers[0]].quantity)
    assert large > small


def test_negative_weights_go_short():
    env = make()
    env.reset(seed=1)
    env.step(np.full(len(UNIVERSE), -0.1))
    assert env.portfolio.positions[env.engine.tickers[0]].quantity < 0


def test_out_of_range_actions_are_clipped_not_rejected():
    # A policy emitting 1.3 early in training is normal. Killing the episode
    # would make the environment teach optimiser hygiene instead of trading.
    env = make()
    env.reset(seed=1)
    obs, reward, term, trunc, info = env.step(np.full(len(UNIVERSE), 5.0))
    assert np.all(np.isfinite(obs))
    assert not term


def _weights_traded(env, prices, worth):
    """Each name's traded quantity as a weight of the pre-step net worth."""
    asked = {f["ticker"]: f["requested"] for f in env.portfolio.fills}
    return [asked.get(t, 0.0) * p / worth
            for t, p in zip(env.engine.tickers, prices)]


@pytest.mark.parametrize("weight", [1.0, 0.4])
def test_an_over_cap_action_is_scaled_down_as_a_whole(weight):
    """Five weights of 1.0 ask for 5x under a 2x cap, and five of 0.4 for
    exactly 2x, which the cost of the fills pushes over.

    Before 0.8.5 the env traded names in roster order until the cap refused
    one: all ones bought the first name at 1x and refused the other four,
    and 0.4 each lost the fifth name. So the position depended on which
    name came first. Scaled as one vector, every name gets the same weight,
    just under the cap, and nothing is refused.
    """
    env = make()
    env.reset(seed=5)
    n = len(UNIVERSE)
    prices = np.frombuffer(env.engine.prices(), dtype="<f8")
    _, _, _, _, info = env.step(np.full(n, weight))
    assert info["scaled"] is True
    assert info["rejected"] == 0
    room = 2.0 / (1 + 0.01 * 2.0)
    assert _weights_traded(env, prices, 2_000_000) == pytest.approx(
        [room / n] * n, rel=1e-9)


def test_an_action_under_the_cap_is_traded_as_given():
    env = make()
    env.reset(seed=5)
    n = len(UNIVERSE)
    prices = np.frombuffer(env.engine.prices(), dtype="<f8")
    action = np.array([0.5, -0.4, 0.3, 0.2, -0.5])   # 1.9x gross
    _, _, _, _, info = env.step(action)
    assert info["scaled"] is False
    assert info["rejected"] == 0
    assert _weights_traded(env, prices, 2_000_000) == pytest.approx(
        list(action), rel=1e-9)


def test_without_a_cap_nothing_is_scaled():
    env = make(max_leverage=None)
    env.reset(seed=5)
    _, _, _, _, info = env.step(np.ones(len(UNIVERSE)))
    assert info["scaled"] is False
    assert info["rejected"] == 0
    assert info["leverage"] > 4.5


def test_a_rotation_sells_before_it_buys():
    """Moving 1.9x from the last two names into the first two.

    In roster order the purchases came first and would have taken the book
    to about 3.8x, so the cap refused them, the sales went through, and the
    step ended flat. Shrinking positions are traded before growing ones.
    """
    env = make()
    env.reset(seed=5)
    n = len(UNIVERSE)
    tickers = env.engine.tickers
    last_two = np.zeros(n)
    last_two[-2:] = 0.95
    env.step(last_two)
    first_two = np.zeros(n)
    first_two[:2] = 0.95
    _, _, _, _, info = env.step(first_two)
    assert info["scaled"] is False
    assert info["rejected"] == 0
    held = env.portfolio.positions
    assert all(held[t].quantity > 0 for t in tickers[:2])
    assert all(held[t].quantity == 0 for t in tickers[-2:])


def test_a_malformed_action_is_refused():
    env = make()
    env.reset(seed=1)
    with pytest.raises(tradefloor.ValidationError, match="expected"):
        env.step(np.zeros(len(UNIVERSE) + 3))
    with pytest.raises(tradefloor.ValidationError, match="non-finite"):
        env.step(np.full(len(UNIVERSE), np.nan))


def test_stepping_before_reset_is_refused():
    env = make()
    with pytest.raises(tradefloor.ValidationError, match="reset"):
        env.step(np.zeros(len(UNIVERSE)))


# --------------------------------------------------------------------------
# Reward
# --------------------------------------------------------------------------

def test_rewards_sum_to_the_change_in_net_worth():
    """Per-step P&L, not cumulative.

    Cumulative reward double-counts every earlier step and makes the return
    depend on episode length rather than on skill.
    """
    env = make()
    env.reset(seed=7)
    rng = np.random.default_rng(0)
    total = 0.0
    info = {}
    while True:
        _, reward, term, trunc, info = env.step(rng.uniform(-0.2, 0.2, len(UNIVERSE)))
        total += reward
        if term or trunc:
            break
    assert total == pytest.approx(info["net_worth"] - 2_000_000, rel=1e-9)


def test_the_episode_truncates_after_the_configured_length():
    env = make(days=2, steps_per_day=3)
    env.reset(seed=1)
    for i in range(6):
        _, _, term, trunc, info = env.step(np.zeros(len(UNIVERSE)))
    assert trunc and not term
    assert info["step"] == 6


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------

def test_the_same_seed_and_actions_give_the_same_episode():
    def run():
        env = make()
        env.reset(seed=11)
        rng = np.random.default_rng(3)
        rewards = []
        for _ in range(6):
            _, r, *_ = env.step(rng.uniform(-0.2, 0.2, len(UNIVERSE)))
            rewards.append(r)
        return rewards

    assert run() == run()


def test_a_different_seed_gives_a_different_market():
    def run(seed):
        env = make()
        env.reset(seed=seed)
        rewards = []
        for _ in range(6):
            _, r, *_ = env.step(np.full(len(UNIVERSE), 0.1))
            rewards.append(r)
        return rewards

    assert run(1) != run(2)


def test_reset_builds_a_new_market_rather_than_rewinding():
    # A rewind either secretly reconstructs -- fine, but then it is a
    # constructor -- or tries to restore mutable state and eventually misses a
    # field: the maker inventory, the Box-Muller spare, the GARCH state.
    env = make()
    env.reset(seed=5)
    env.step(np.full(len(UNIVERSE), 0.2))
    first = env.engine

    env.reset(seed=5)
    assert env.engine is not first
    assert env.portfolio.cash == 2_000_000
    assert env.portfolio.positions == {}


# --------------------------------------------------------------------------
# Seeds across resets
# --------------------------------------------------------------------------

def _episode(env, **kw):
    """The seed a reset ran, and the rewards of one fixed action after it."""
    _, info = env.reset(**kw)
    rewards = [env.step(np.full(len(UNIVERSE), 0.1))[1] for _ in range(2)]
    return info["seed"], rewards


def test_a_reset_without_a_seed_draws_a_new_market_after_the_first():
    """Gymnasium's convention: reset() with no seed moves on.

    Before 0.8.5 every reset() without a seed ran the constructor's seed,
    even straight after reset(seed=99), so a training loop of plain
    reset() calls trained on one market.
    """
    env = make()
    first, second, third = _episode(env), _episode(env), _episode(env)
    assert first[0] == 42
    assert len({first[0], second[0], third[0]}) == 3
    assert first[1] != second[1] and second[1] != third[1]

    ninety_nine = _episode(env, seed=99)
    after = _episode(env)
    assert ninety_nine[0] == 99
    assert after[0] not in (42, 99)
    assert after[1] != first[1] and after[1] != ninety_nine[1]


def test_unseeded_resets_draw_the_same_markets_on_every_run():
    def seeds(constructor_seed):
        env = make(seed=constructor_seed)
        drawn = [env.reset()[1]["seed"] for _ in range(3)]
        env.reset(seed=99)
        return drawn, [env.reset()[1]["seed"] for _ in range(2)]

    assert seeds(42) == seeds(42)
    # What follows reset(seed=99) depends on 99 alone.
    assert seeds(42)[1] == seeds(7)[1]
    assert seeds(42)[0] != seeds(7)[0]


def test_the_seed_in_info_replays_a_drawn_market():
    env = make()
    env.reset()
    drawn, rewards = _episode(env)
    assert drawn != 42
    assert _episode(make(), seed=drawn) == (drawn, rewards)


def test_without_gymnasium_the_same_seeds_are_drawn(monkeypatch):
    """The env runs without gymnasium, on a generator built the way
    gymnasium builds its own, so a training loop meets the same markets
    either way."""
    import tradefloor.gym as tgym

    def seeds():
        env = make()
        drawn = [env.reset()[1]["seed"] for _ in range(3)]
        env.reset(seed=99)
        return drawn + [env.reset()[1]["seed"]]

    installed = seeds()
    monkeypatch.setattr(tgym, "_gym", None)
    assert seeds() == installed
    assert len(set(installed)) == 4
