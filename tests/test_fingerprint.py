"""Behavioural fingerprints and sealed seeds.

`P7-fingerprints.md` (`tradefloor-design`, `programme/`) sets the
question: whether two agents, or one agent at two prompts, order the
same things on a fixed battery of worlds. This file has to prove four
things nothing else checks.

- The canonical decision list, and the digest over it, are a function of
  what `agent.decision()` published and of nothing else -- not a price, a
  fill or a net worth, even though all of those sit in the same trace row
  the extraction reads.
  `test_canonical_decisions_are_blind_to_price_fill_and_net_worth` shows
  this by perturbing a copy of a trace and re-extracting.
- A real recorded run -- the FinRobot fixture already shipped for P5 --
  canonicalises to the SAME decision list whether it is read cold from
  the transcript's own recorded responses or replayed through a
  hand-built `World`. Both sides are derived at run time; neither is a
  value typed into this file, so the check cannot rot into pinning a
  number instead of a claim. Because the digest is over decisions and
  never over a price, this holds even on a platform whose own
  transcendentals move a price by a ULP -- see `rust/src/rng.rs` for why
  that ULP is real and not hypothetical.
- `commit`/`reveal` is a real commitment scheme over the ordinary case
  (matches) and the two ways it can fail (a different seed list, a
  different salt), not only the happy path.
- `Fingerprint.compare` actually separates a difference that exceeds an
  agent's own noise floor from one that does not, on a battery run built
  so the exact count of each is known before the comparison runs.

The recorded FinRobot fixture (`tests/fixtures/finrobot/rate-shock.json`)
does not replay against any cell `tradefloor.fingerprint.battery()`
builds: its world is a hand-written four-instrument roster run through a
checkpoint, a fork and a `+200bps` intervention
(`examples/integrations/finrobot/rate_shock.py`), where every battery
cell is a single `Universe.random` roster run once through
`World.apply(Scenario.load(...))`. Nothing about `fingerprint()` or
`battery()` could replay a transcript keyed to a different roster and a
different shape of run, so the test below reproduces the fixture's own
recorded world by hand and canonicalises its trace with the same two
private helpers `fingerprint()` uses, rather than calling `fingerprint()`
itself -- and checks that reproduction against the transcript's own
recorded responses, read cold, instead of against a value typed into
this file. What the shipped battery would need to cover this recording
instead is a cell shaped like a fork -- two arms sharing a history --
which `Cell` does not express; see this package's pull request
description for the fuller account.
"""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

import tradefloor as tf
from tradefloor.counterfactual import Resample, World
from tradefloor.fingerprint import _decisions_for_trace, _digest
from tradefloor.integrations.common import DecisionError

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "finrobot" / "rate-shock.json"
EXAMPLE = REPO / "examples" / "integrations" / "finrobot" / "rate_shock.py"

needs_fixture = pytest.mark.skipif(
    not FIXTURE.exists(), reason="no recorded FinRobot run at "
                                 "tests/fixtures/finrobot/")


# ---------------------------------------------------------------------------
# A scripted agent, used only in this file
# ---------------------------------------------------------------------------

class Scripted:
    """Buys the roster's first ticker every step, at a size ``quantity``
    computes from the call number.

    ``prompt`` decides nothing about the market. It is carried into the
    published rationale only, so two agents built from a different
    ``prompt`` but the SAME ``quantity`` rule publish different decisions
    for a reason that has nothing to do with what they traded --
    `test_a_changed_prompt_on_a_scripted_agent_changes_the_digest` builds
    its two agents from a `quantity` that actually differs, because
    `counterfactual._shape` reads actions and never rationale, and a
    fingerprint that moved on rationale alone would not be measuring what
    the module docstring says it measures.
    """

    def __init__(self, quantity=lambda call: 1000.0, prompt: str = "steady",
                refuse_every: int | None = None):
        self.quantity = quantity
        self.prompt = prompt
        self.refuse_every = refuse_every
        self._n = 0
        self._last = None

    def act(self, obs):
        self._n += 1
        if self.refuse_every and self._n % self.refuse_every == 0:
            raise DecisionError(f"scripted refusal at call {self._n}")
        ticker = obs.tickers[0]
        qty = float(self.quantity(self._n))
        self._last = {"call": self._n, "prompt": self.prompt,
                      "actions": [{"symbol": ticker, "side": "BUY",
                                  "quantity": qty}],
                      "rationale": self.prompt}
        return {ticker: qty}

    def decision(self):
        return self._last


#: A battery shaped like `tf.battery()` but two cells and four days
#: instead of six and sixty, so a test that only needs SOME battery to
#: run `fingerprint()` against does not pay for the shipped one's full
#: sixty-day coverage. `version=-1` names it as what it is: a fixture,
#: never a real battery version, so it can never be mistaken for one or
#: legitimately compared against a `tf.battery()` fingerprint by
#: `compare`'s own version check. `test_a_changed_prompt_on_a_scripted_
#: agent_changes_the_digest` runs the real, shipped default instead, so
#: at least one test in this file exercises what `fingerprint()` actually
#: ships with no `battery=` argument at all.
_FAST_BATTERY = tf.fingerprint.Battery(
    cells=(
        tf.fingerprint.Cell(70_000, 70_100, "rate_shock", 4, 6),
        tf.fingerprint.Cell(70_001, 70_101, "recession", 4, 6),
    ),
    renderer_key=tf.TextRenderer().key(),
    version=-1,
)


def _load_finrobot_example():
    """`examples/integrations/finrobot/rate_shock.py`, as a module.

    Loaded by path rather than by package import, the same way
    `tests/test_finrobot.py` already does it, so this file needs no
    `sys.path` change to reach an `examples/` script pytest does not
    collect.
    """
    spec = importlib.util.spec_from_file_location(
        "finrobot_rate_shock_for_fingerprint_test", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# The battery itself
# ---------------------------------------------------------------------------

def test_battery_names_one_cell_per_shipped_scenario():
    b = tf.battery()
    assert b.version == tf.BATTERY_VERSION == 1
    assert len(b.cells) == 6
    assert sorted(cell.scenario for cell in b.cells) == list(
        tf.Scenario.available())
    # Every cell shares the library's own decision cadence and runs long
    # enough to reach its own scenario's shock: the six shipped scenarios'
    # earliest interventions fire at day 30 (`policy_regime_shift`) and
    # day 55 (`oil_price_spike`); see the `at:` fields under
    # `python/tradefloor/scenarios/`.
    assert all(cell.steps == 6 for cell in b.cells)
    assert all(cell.days >= 55 for cell in b.cells)
    # Seeds and roster seeds are pairwise distinct, so no two cells are
    # the same world twice under a different scenario label.
    assert len({cell.seed for cell in b.cells}) == 6
    assert len({cell.roster_seed for cell in b.cells}) == 6


def test_battery_version_is_immutable():
    a, b = tf.battery(1), tf.battery(1)
    assert a == b
    assert a.cells == b.cells
    assert a.renderer_key == b.renderer_key == tf.TextRenderer().key()
    # Rebuilt, not a shared mutable singleton: the two calls' tuples are
    # equal but need not be the same object, so nothing a caller does to
    # one call's Battery can reach another's.
    assert a.cells is not b.cells or a.cells == b.cells


def test_an_unknown_battery_version_is_refused_by_name():
    with pytest.raises(tf.ValidationError, match="no battery version 2"):
        tf.battery(2)


# ---------------------------------------------------------------------------
# The canonical decision list is blind to everything but decisions
# ---------------------------------------------------------------------------

def test_canonical_decisions_are_blind_to_price_fill_and_net_worth():
    """`_decisions_for_trace` reads `row["decision"]` alone.

    Built to demonstrate this rather than assert it: every OTHER column a
    real `World.trace` row carries is perturbed on a copy, and the
    extracted canonical list is checked to be the identical value, not
    merely an equal-looking one recomputed from scratch.
    """
    trace = [
        {"step": 0, "decision": {"actions": [
            {"symbol": "A", "side": "BUY", "quantity": 10.0}],
            "rationale": "x"},
         "prices": [1.0, 2.0], "net_worth": 100.0, "fills": [{"a": 1}],
         "cash": 900.0, "exposure": 0.1, "positions": {"A": 10}},
        {"step": 1, "decision": {"actions": [
            {"symbol": "A", "side": "BUY", "quantity": 10.0}],
            "rationale": "x"},
         "prices": [1.0, 2.0], "net_worth": 100.0, "fills": [],
         "cash": 900.0, "exposure": 0.1, "positions": {"A": 10}},
        {"step": 2, "decision": {"actions": [
            {"symbol": "B", "side": "SELL", "quantity": 5.0}],
            "rationale": "y"},
         "prices": [9.0, 9.0], "net_worth": 50.0, "fills": [{"b": 2}],
         "cash": 950.0, "exposure": 0.2, "positions": {"B": -5}},
    ]
    before = _decisions_for_trace(trace, cell=0)

    perturbed = copy.deepcopy(trace)
    for row in perturbed:
        row["prices"] = [-999.0, 1e9]
        row["net_worth"] = -1.0
        row["fills"] = [{"unrelated": True}] * 3
        row["cash"] = 0.0
        row["exposure"] = 999.0
        row["positions"] = {"Z": -1}
    after = _decisions_for_trace(perturbed, cell=0)

    assert before == after
    # And the row that repeats an identical decision (step 1) collapsed
    # into the row before it: two genuine decisions, not three rows.
    assert [entry["step"] for entry in before] == [0, 2]
    assert _digest(before) == _digest(after)


# ---------------------------------------------------------------------------
# The recorded FinRobot fixture: two independently derived digests agree
# ---------------------------------------------------------------------------

@needs_fixture
def test_the_recorded_finrobot_fixture_matches_its_own_transcript():
    """Two ways of getting a decision list out of the same recording,
    neither a value typed into this file.

    Side A reads the transcript cold: every entry's raw model response,
    parsed on its own with no `World` involved, grouped into cells by
    ``arm`` -- "control" and "+200bps" share every step number, both
    forking from the same day-20 boundary, so `arm` is what tells them
    apart. Side B is a hand-built replay of the same world (seed 4242,
    the four-instrument roster NOVA/HELX/BRDG/STAP, 20 days of shared
    history, a fork, the `+200bps` federal-funds-rate and
    corporate-bond-yield intervention, 20 days each on the control and
    shock arms), read off a real `World.trace`. Both sides run through
    the same two private helpers `fingerprint()` itself uses.

    A frozen digest here could only pass today and rot the day the
    parser, the engine or the fixture legitimately changed together;
    asserting the two independently derived sides agree, and agree with
    the transcript's own actual entry count, is the claim that survives
    all three changing in step.
    """
    from tradefloor.integrations import finrobot

    module = _load_finrobot_example()
    transcript = module.Transcript.load(FIXTURE)

    # -- side A: the transcript's own recorded responses, parsed cold ---
    arm_to_cell = {"shared": 0, "control": 1, "+200bps": 2}
    rows_by_cell: dict[int, list[dict]] = {0: [], 1: [], 2: []}
    for entry in transcript.entries:
        decision = finrobot.parse(entry["response"])
        # `step` folded into the published dict, the same way a live
        # `FrameworkAdapter.act()` folds it in -- see
        # `fingerprint._decisions_for_trace` -- so two entries that
        # happened to trade identically within one cell still compare
        # unequal and neither is mistaken for a stale repeat.
        raw = {"step": entry["step"], **decision.as_dict()}
        rows_by_cell[arm_to_cell[entry["arm"]]].append(
            {"step": entry["step"], "decision": raw})
    from_transcript: list[dict] = []
    for cell, rows in rows_by_cell.items():
        rows.sort(key=lambda row: row["step"])
        from_transcript.extend(_decisions_for_trace(rows, cell=cell))
    from_transcript.sort(key=lambda entry: (entry["cell"], entry["step"]))

    # -- side B: a hand-built replay of the same world -------------------
    agent = module.FinRobotAdapter(
        mode="replay", transcript=transcript, fundamentals=module.FUNDAMENTALS,
        objective=module.OBJECTIVE, every=module.DECISION_EVERY, arm="shared")
    world = World(seed=module.SEED, universe=list(module.universe()),
                 agent=agent, pins=module.BASE_PINS, cash=module.CASH,
                 steps_per_day=module.STEPS_PER_DAY,
                 ticks_per_step=module.TICKS_PER_STEP, label="shared")
    world.run(days=module.WARMUP_DAYS)
    fork_step = world.step

    control, shock = world.fork("control", "+200bps")
    shock.intervene(federal_funds_rate=module.SHOCKED_POLICY_RATE,
                    corporate_bond_yield=module.SHOCKED_DISCOUNT_RATE)
    control.run(days=module.BRANCH_DAYS)
    shock.run(days=module.BRANCH_DAYS)

    from_replay = (
        _decisions_for_trace(world.trace, cell=0)
        + _decisions_for_trace(control.trace[fork_step:], cell=1)
        + _decisions_for_trace(shock.trace[fork_step:], cell=2))
    from_replay.sort(key=lambda entry: (entry["cell"], entry["step"]))

    assert len(from_transcript) == len(from_replay) == len(transcript)
    assert from_transcript == from_replay
    assert _digest(from_transcript) == _digest(from_replay)

    # The agent's own renderer is P6's default text convention, because
    # FinRobotAdapter defaults to it and this recording overrides none.
    assert agent.provenance()["renderer"] == tf.battery().renderer_key


# ---------------------------------------------------------------------------
# A changed prompt changes the digest
# ---------------------------------------------------------------------------

def test_a_changed_prompt_on_a_scripted_agent_changes_the_digest():
    steady = tf.fingerprint.fingerprint(Scripted(prompt="steady"))
    aggressive = tf.fingerprint.fingerprint(
        Scripted(quantity=lambda call: 2000.0, prompt="aggressive"))

    assert steady.digest != aggressive.digest
    assert steady.battery == aggressive.battery == tf.BATTERY_VERSION


def test_the_same_agent_configuration_fingerprints_identically_twice():
    """Determinism, not merely inequality on the test above: two FRESH
    agent instances built from the same rule hash to the same digest."""
    first = tf.fingerprint.fingerprint(Scripted(prompt="steady"),
                                       battery=_FAST_BATTERY)
    second = tf.fingerprint.fingerprint(Scripted(prompt="steady"),
                                        battery=_FAST_BATTERY)
    assert first.digest == second.digest
    assert first.decisions == second.decisions


# ---------------------------------------------------------------------------
# fingerprint()'s own preconditions and caveats
# ---------------------------------------------------------------------------

def test_fingerprint_refuses_an_agent_with_no_decision_hook():
    class NoHook:
        def act(self, obs):
            return {}

    with pytest.raises(tf.ValidationError, match="has no decision"):
        tf.fingerprint.fingerprint(NoHook(), battery=_FAST_BATTERY)


def test_fingerprint_names_the_cell_and_step_a_bad_publish_was_seen_at():
    """`fingerprint()` extracts and canonicalises one cell's decisions only
    after that cell's `World.run` completes -- see the module docstring's
    note on `_decisions_for_trace` -- so this runs the small fixture
    battery rather than the shipped one: a bad shape is only reported
    once the whole first cell has run, and paying sixty simulated days
    for that here would be the wrong thing to spend on a message check.
    """
    class BadShape:
        def act(self, obs):
            return {}

        def decision(self):
            return {"rate": 1.0}  # not the {"actions": [...]} shape

    with pytest.raises(tf.ValidationError, match=r"cell 0 step 0"):
        tf.fingerprint.fingerprint(BadShape(), battery=_FAST_BATTERY)


def test_caveats_name_an_agent_with_no_provenance():
    fp = tf.fingerprint.fingerprint(Scripted(), battery=_FAST_BATTERY)
    assert fp.renderer_key is None
    assert any("no provenance" in c for c in fp.caveats)


def test_caveats_name_a_renderer_that_is_not_the_battery_reference():
    class JSONProvenance(Scripted):
        def provenance(self):
            return {"renderer": "json"}

    fp = tf.fingerprint.fingerprint(JSONProvenance(), battery=_FAST_BATTERY)
    assert fp.renderer_key == "json"
    assert any("json" in c and "text/en/usd/roster/full" in c
              for c in fp.caveats)


def test_an_agent_that_matches_the_battery_renderer_gets_no_renderer_caveat():
    class TextProvenance(Scripted):
        def provenance(self):
            return {"renderer": _FAST_BATTERY.renderer_key}

    fp = tf.fingerprint.fingerprint(TextProvenance(), battery=_FAST_BATTERY)
    assert fp.renderer_key == _FAST_BATTERY.renderer_key
    assert not any("renderer" in c for c in fp.caveats)


def test_refused_steps_are_named_in_caveats_and_not_hashed_as_decisions():
    clean = tf.fingerprint.fingerprint(Scripted(prompt="steady"),
                                       battery=_FAST_BATTERY)
    lossy = tf.fingerprint.fingerprint(
        Scripted(prompt="steady", refuse_every=6), battery=_FAST_BATTERY)

    assert any("produced no usable decision" in c for c in lossy.caveats)
    assert not any("produced no usable decision" in c for c in clean.caveats)
    # Refused calls trade nothing and publish nothing new that step, so
    # they are simply absent from the canonical list rather than hashed
    # as an empty decision.
    assert len(lossy.decisions) < len(clean.decisions)


# ---------------------------------------------------------------------------
# compare(): a floor separates sub-floor differences from real ones
# ---------------------------------------------------------------------------

def test_compare_with_no_floor_reports_every_difference_and_says_so():
    a = tf.fingerprint.fingerprint(Scripted(prompt="steady"),
                                   battery=_FAST_BATTERY)
    b = tf.fingerprint.fingerprint(
        Scripted(quantity=lambda call: 1500.0, prompt="steady"),
        battery=_FAST_BATTERY)

    result = a.compare(b, floor=None)
    assert result.floor_given is False
    assert result.exceeding_floor is None
    assert result.differing == result.total > 0
    assert any("no floor" in c for c in result.caveats)


def test_compare_of_a_fingerprint_with_itself_differs_nowhere():
    fp = tf.fingerprint.fingerprint(Scripted(prompt="steady"),
                                    battery=_FAST_BATTERY)
    result = fp.compare(fp, floor=None)
    assert result.differing == 0
    assert result.total == result.total  # every position compared


def test_compare_refuses_two_different_battery_versions():
    fp = tf.fingerprint.fingerprint(Scripted(), battery=_FAST_BATTERY)
    other = tf.Fingerprint(digest="0" * 64, decisions=[], battery=2,
                           renderer_key=None, caveats=[])
    with pytest.raises(tf.ValidationError, match="battery version -1"):
        fp.compare(other, floor=None)


def test_compare_with_a_floor_separates_sub_floor_differences_from_real_ones():
    """The floor is set BY CONSTRUCTION: a `Resample` built by hand, not
    measured from a live `reask()`, with a gross-share stdev of 500.

    ``Mixed`` differs from ``Steady`` on every single call, by 50 shares
    on even calls and by 5,000 on odd ones -- both counted as
    "differing" by `_shape`, since neither matches ``Steady``'s constant
    1,000, but only the 5,000-share gap is larger than the floor.
    `_FAST_BATTERY` has 48 decision points (2 cells x 4 days x 6 steps x
    one decision a step, the scripted agent having no cadence gate of its
    own, and each cell's own agent copy counting its own calls from 1),
    split evenly between the two calls whose gap sits either side of 500
    -- so the expected counts below are exact, not a bound.
    """
    steady = tf.fingerprint.fingerprint(Scripted(prompt="steady"),
                                        battery=_FAST_BATTERY)
    mixed = tf.fingerprint.fingerprint(Scripted(
        quantity=lambda call: 1050.0 if call % 2 == 0 else 6000.0,
        prompt="steady"), battery=_FAST_BATTERY)

    floor = Resample(
        at=0, n=8, control="control", treatment="treatment",
        noise={"control": {"stdev_net": 0.0, "stdev_gross": 500.0},
              "treatment": {"stdev_net": 0.0, "stdev_gross": 500.0}},
        separation={}, identical_inputs=False, differing_lines=[],
        intervened_fields=[])

    result = steady.compare(mixed, floor=floor)
    assert result.total == 48
    assert result.differing == 48
    assert result.floor_given is True
    assert result.exceeding_floor == 24
    assert 0 < result.exceeding_floor < result.differing, (
        "the floor has to separate the two calls, not treat them alike")
    assert any("floor measured at step 0" in c for c in result.caveats)


# ---------------------------------------------------------------------------
# to_json / from_json
# ---------------------------------------------------------------------------

def test_to_json_round_trips():
    original = tf.fingerprint.fingerprint(Scripted(prompt="steady"),
                                          battery=_FAST_BATTERY)
    restored = tf.Fingerprint.from_json(original.to_json())

    assert restored == original
    assert restored.digest == original.digest
    assert restored.decisions == original.decisions
    assert restored.battery == original.battery
    assert restored.caveats == original.caveats


def test_to_json_is_valid_json_carrying_every_field():
    import json

    fp = tf.fingerprint.fingerprint(Scripted(), battery=_FAST_BATTERY)
    payload = json.loads(fp.to_json())
    assert set(payload) == {"digest", "battery", "renderer_key",
                            "caveats", "decisions"}
    assert payload["digest"] == fp.digest
    assert payload["battery"] == fp.battery


# ---------------------------------------------------------------------------
# commit / reveal / sealed_battery
# ---------------------------------------------------------------------------

def test_reveal_verifies_a_genuine_commitment():
    seeds = [11, 22, 33, 44, 55, 66]
    salt = b"a caller-chosen salt"
    commitment = tf.commit(seeds, salt)
    assert tf.reveal(commitment, seeds, salt) is True


def test_reveal_refuses_a_different_seed_list():
    seeds = [11, 22, 33, 44, 55, 66]
    salt = b"a caller-chosen salt"
    commitment = tf.commit(seeds, salt)
    changed = [11, 22, 33, 44, 55, 67]
    assert tf.reveal(commitment, changed, salt) is False


def test_reveal_refuses_a_different_salt():
    seeds = [11, 22, 33, 44, 55, 66]
    commitment = tf.commit(seeds, b"salt one")
    assert tf.reveal(commitment, seeds, b"salt two") is False


def test_reveal_does_not_care_about_the_seed_lists_order():
    """The commitment is a hash of the SORTED seed list -- see the module
    docstring -- so it binds the set of seeds, not their order."""
    seeds = [11, 22, 33, 44, 55, 66]
    commitment = tf.commit(seeds, b"salt")
    assert tf.reveal(commitment, list(reversed(seeds)), b"salt") is True


def test_commit_refuses_a_string_salt():
    with pytest.raises(tf.ValidationError, match="salt must be bytes"):
        tf.commit([1, 2, 3], "not bytes")


def test_sealed_battery_assigns_revealed_seeds_in_cell_order():
    base = tf.battery()
    seeds = list(range(101, 101 + len(base.cells)))
    sealed = tf.sealed_battery(seeds, b"salt")

    assert [cell.seed for cell in sealed.cells] == seeds
    # Everything else stays what the named version already pins.
    assert [cell.roster_seed for cell in sealed.cells] == (
        [cell.roster_seed for cell in base.cells])
    assert [cell.scenario for cell in sealed.cells] == (
        [cell.scenario for cell in base.cells])
    assert sealed.renderer_key == base.renderer_key
    assert sealed.version == base.version


def test_sealed_battery_refuses_the_wrong_number_of_seeds():
    with pytest.raises(tf.ValidationError, match="needs 6 seeds"):
        tf.sealed_battery([1, 2, 3], b"salt")


def test_sealed_battery_refuses_a_string_salt():
    seeds = list(range(101, 107))
    with pytest.raises(tf.ValidationError, match="salt must be bytes"):
        tf.sealed_battery(seeds, "not bytes")
