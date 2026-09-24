"""The canonical demo has to keep being true, not merely keep running.

``examples/rate-shock/counterfactual.py`` is the project's headline claim in
executable form: same world, same agent, one changed variable. A demo that
still runs while quietly failing to demonstrate that is worse than one that
crashes, because nobody looks.

So these check the CLAIMS. That the experiment reruns identically. That the
arms were identical before the intervention and differ in exactly the two
fields it changed. That nothing diverged before it. That the agent's response
has the shape the demo's prose says it has -- less risk, and least of all in
the longest-duration name. And that the artifacts it writes can be read back
and reproduced by somebody who was not there.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import tradefloor as tf
from tradefloor.counterfactual import World, agree, compare

STUDY = Path(__file__).resolve().parent.parent / "examples" / "rate-shock"
sys.path.insert(0, str(STUDY))

demo = pytest.importorskip("counterfactual")
from agent import MacroAwareAgent  # noqa: E402


def build() -> World:
    roster = demo.universe()
    agent = MacroAwareAgent(
        duration={row[0]: row[7] for row in demo.ROSTER})
    return World(
        seed=demo.SEED, universe=list(roster), agent=agent,
        pins={"federal_funds_rate": demo.POLICY_RATE,
              "corporate_bond_yield": demo.DISCOUNT_RATE},
        cash=demo.CASH, steps_per_day=demo.STEPS_PER_DAY,
        ticks_per_step=demo.TICKS_PER_STEP, label="root")


def experiment():
    world = build()
    world.run(days=demo.WARMUP_DAYS)
    control, shock = world.fork("control", f"+{demo.SHOCK_BPS}bps")
    agreement = agree(control, shock)
    shock.intervene(federal_funds_rate=demo.SHOCKED_POLICY_RATE,
                    corporate_bond_yield=demo.SHOCKED_DISCOUNT_RATE)
    control.run(days=demo.BRANCH_DAYS)
    shock.run(days=demo.BRANCH_DAYS)
    return world, control, shock, agreement


@pytest.fixture(scope="module")
def run():
    return experiment()


# ---------------------------------------------------------------------------
# The experiment holds together
# ---------------------------------------------------------------------------

def test_the_arms_start_identical(run):
    _world, _control, _shock, agreement = run
    assert agreement.identical, agreement.differences
    assert len(agreement.checks) == 9


def test_the_intervention_is_the_only_difference(run):
    world, control, shock, _agreement = run
    fork = world.step
    before_a, before_b = control.trace[fork - 1], shock.trace[fork - 1]
    assert before_a == before_b

    after_a = control.trace[fork]["macro"]
    after_b = shock.trace[fork]["macro"]
    changed = {f for f in after_a if after_a[f] != after_b[f]}
    assert changed == {"federal_funds_rate", "corporate_bond_yield"}
    assert (after_b["federal_funds_rate"] - after_a["federal_funds_rate"]
            == pytest.approx(demo.SHOCK_BPS / 10_000))
    assert (after_b["corporate_bond_yield"] - after_a["corporate_bond_yield"]
            == pytest.approx(demo.SHOCK_BPS / 10_000))


def test_nothing_diverges_before_the_intervention(run):
    world, control, shock, agreement = run
    report = compare(control, shock, agreement=agreement)
    d = report.divergence
    assert d.intervention_step == world.step
    for field in ("macro", "decision", "orders", "prices", "portfolio"):
        step = getattr(d, field)
        assert step is not None, f"{field} never diverged"
        assert step >= d.intervention_step, f"{field} diverged at {step}"


def test_the_experiment_reruns_identically():
    """Twice, from scratch, to the bit. Both arms and the shared history."""
    _wa, ca, sa, _aa = experiment()
    _wb, cb, sb, _ab = experiment()
    assert ca.trace == cb.trace
    assert sa.trace == sb.trace
    assert ca.digest() == cb.digest()
    assert sa.digest() == sb.digest()
    assert ca.summary() == cb.summary()
    assert sa.summary() == sb.summary()


def test_the_shocked_arm_is_not_the_control(run):
    """The guard on every determinism test above: an intervention that did
    nothing would satisfy all of them."""
    _world, control, shock, _agreement = run
    assert control.trace != shock.trace
    assert control.digest() != shock.digest()
    assert control.engine.prices() != shock.engine.prices()


# ---------------------------------------------------------------------------
# The agent behaved the way the demo says it did
# ---------------------------------------------------------------------------

def test_the_agent_cuts_risk_on_the_step_the_rate_moves(run):
    """The attribution claim, asserted on the two channels rather than on a
    still control.

    This read `after_control == before` until 0.8.0, and that equality was a
    COINCIDENCE rather than the property it was standing in for. The agent
    has two ways to cut: the rate channel, which only the shocked arm sees,
    and the volatility channel, a ratio of a 12-step window to a 60-step one
    that both arms compute from the SAME price history up to the fork.
    Measured over the branch on both presets: the volatility channel is
    non-zero on 36 of the 179 steps with a full slow window -- 20 per cent,
    and the SAME 36-of-179 on pt-v18 and on pt-v19. The fork lands on step
    120. On pt-v18 that step fell in the quiet 80 per cent and the control
    read exactly 0.95; on pt-v19 it falls in the busy 20 per cent and the
    control reads 0.9416. Nothing about the agent, the demo or the
    attribution changed -- the old assertion was resting on which side of a
    one-in-five split one particular step happened to land.

    So assert the thing that is actually load-bearing. At the fork the two
    arms differ in the policy rate and in nothing else, so their volatility
    channels are equal and CANCEL: the whole difference in target gross is
    the intervention. That is a stronger statement than a still control, and
    it holds on every preset rather than on the ones whose step 120 was
    quiet.
    """
    world, control, shock, _agreement = run
    fork = world.step
    before = control.trace[fork - 1]["decision"]["gross"]
    at_control = control.trace[fork]["decision"]
    at_shock = shock.trace[fork]["decision"]
    after_control, after_shock = at_control["gross"], at_shock["gross"]

    assert at_control["tightening_bps"] == 0, (
        f"the control read {at_control['tightening_bps']}bp of tightening; "
        "its rate was not supposed to move at all")
    assert at_control["vol_excess"] == at_shock["vol_excess"], (
        f"the arms read different volatility excess at the fork "
        f"({at_control['vol_excess']} against {at_shock['vol_excess']}), so "
        "the second channel no longer cancels and the difference in gross "
        "cannot be attributed to the rate alone")
    # The control may move on the shared channel, and must not move MUCH: a
    # control that de-risked materially of its own accord would leave the
    # comparison measuring two things even with the channel equalised.
    assert after_control >= before * 0.98, (
        f"the control cut gross from {before:.3f} to {after_control:.3f} "
        "without a rate move, which is more than the volatility channel "
        "should carry across one step")
    assert after_shock < before * 0.75, (
        f"the shocked arm cut gross exposure from {before:.3f} to "
        f"{after_shock:.3f}, which is not the material de-risking the demo "
        "describes")


def test_the_cut_is_deepest_in_the_longest_duration_name(run):
    """The composition claim, which is the interesting half of the response.

    A policy that only scaled everything down would show the same headline
    exposure change and would say nothing about duration. The weights have to
    be monotone in revenue growth, which is the model's own rate-sensitivity
    term.
    """
    world, control, shock, _agreement = run
    fork = world.step
    control_weights = control.trace[fork]["decision"]["weights"]
    shock_weights = shock.trace[fork]["decision"]["weights"]

    assert len(set(round(w, 9) for w in control_weights.values())) == 1, (
        "the control's weights are not equal, so the tilt below is not "
        "measuring the intervention")

    by_duration = sorted(demo.ROSTER, key=lambda row: row[7], reverse=True)
    ordered = [shock_weights[row[0]] for row in by_duration]
    assert ordered == sorted(ordered), (
        "the shocked arm's target weights are not monotone in revenue "
        f"growth: {dict(zip([r[0] for r in by_duration], ordered))}")

    longest, shortest = by_duration[0][0], by_duration[-1][0]
    assert shock_weights[longest] < shock_weights[shortest] * 0.9


def test_the_shocked_arm_ends_holding_less_of_everything(run):
    _world, control, shock, _agreement = run
    for ticker in control.engine.tickers:
        held_control = control.portfolio.positions[ticker].quantity
        held_shock = shock.portfolio.positions[ticker].quantity
        assert 0 < held_shock < held_control, ticker


def test_the_agent_is_deterministic_given_the_same_observations(run):
    """Two arms, the same shared history, and the same decisions in it.

    Different objects, deep-copied at the fork. If the agent carried any
    hidden state -- a counter, an RNG, a cache keyed on identity -- the
    pre-fork decisions in the two traces would drift apart.
    """
    world, control, shock, _agreement = run
    fork = world.step
    for i in range(fork):
        assert control.trace[i]["decision"] == shock.trace[i]["decision"], i
    assert control.agent is not shock.agent


# ---------------------------------------------------------------------------
# The artifacts are what they claim to be
# ---------------------------------------------------------------------------

def test_the_demo_runs_end_to_end_and_writes_readable_artifacts(tmp_path,
                                                                capsys):
    report = demo.main(out=tmp_path, chart=False)
    capsys.readouterr()

    names = {Path(p).name for p in report["artifacts"]}
    assert names == {"checkpoint.json", "control.json", "rate_shock.json",
                     "comparison.json", "manifest.json"}

    manifest = json.loads((tmp_path / "manifest.json").read_text("utf-8"))
    assert manifest["design"]["seed"] == demo.SEED
    assert manifest["fork_agreement"]["identical"] is True
    assert manifest["intervention"][0]["fields"] == {
        "federal_funds_rate": demo.SHOCKED_POLICY_RATE,
        "corporate_bond_yield": demo.SHOCKED_DISCOUNT_RATE}
    assert manifest["divergence"]["decision"] >= \
        manifest["divergence"]["intervention_step"]

    mark = tf.Checkpoint.from_json(
        (tmp_path / "checkpoint.json").read_text("utf-8"))
    assert mark.seed == demo.SEED
    assert len(mark.universe) == len(demo.ROSTER)
    assert mark.resume().tickers == [row[0] for row in demo.ROSTER]


def test_each_arms_manifest_reproduces_its_market(tmp_path, capsys):
    """The claim a published result makes, checked rather than asserted.

    ``reproduce()`` replays the recorded log and refuses on a digest
    mismatch, so this passing means somebody with only the JSON rebuilds the
    same market -- including the intervention, which travels inside the log
    as a ``pin_macro`` entry.
    """
    demo.main(out=tmp_path, chart=False)
    capsys.readouterr()

    control = tf.RunManifest.from_json(
        (tmp_path / "control.json").read_text("utf-8"))
    shock = tf.RunManifest.from_json(
        (tmp_path / "rate_shock.json").read_text("utf-8"))

    rebuilt_control = control.reproduce()
    rebuilt_shock = shock.reproduce()
    assert rebuilt_control.prices() != rebuilt_shock.prices()

    def pinned(manifest):
        return {entry["fields"]["federal_funds_rate"]
                for entry in manifest.order_log
                if entry["op"] == "pin_macro"}

    assert pinned(control) == {demo.POLICY_RATE}
    assert pinned(shock) == {demo.POLICY_RATE, demo.SHOCKED_POLICY_RATE}


def test_the_demo_completes_quickly(tmp_path, capsys):
    """It is a five-minute demo because it is read in five minutes, not run
    in five. If it stops being seconds, it stops being the thing to open
    first."""
    report = demo.main(out=tmp_path, chart=False)
    capsys.readouterr()
    assert report["seconds"] < 30.0
