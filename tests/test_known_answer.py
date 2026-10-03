"""The cross-platform determinism gate.

Deliberately a SEPARATE file from the parity suite, and the reason matters:
`test_parity.py` carries a module-level skipif on the golden corpus being
present. A CI runner building a fresh wheel does not have that corpus -- by
design, it is 135 MB and lives with the reference implementation -- so had
these tests stayed in that file they would have SILENTLY SKIPPED on exactly
the machines the gate exists to check, and the release would have gone green
without ever running it.

This file needs nothing but the installed package.
"""

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import known_answer  # noqa: E402
import known_answer_book  # noqa: E402
import known_answer_presets  # noqa: E402
import known_answer_seed64  # noqa: E402
import known_answer_traded  # noqa: E402
import pytest  # noqa: E402
import tradefloor  # noqa: E402



def test_known_answer_digest_matches_the_committed_baseline():
    """The gate behind the library's headline claim.

    Every wheel target runs this and must produce the same digest. A mismatch
    without a katVersion bump means one platform's build disagrees with the
    others -- which is the failure the libm-only design exists to
    prevent, and which nothing else in the suite would notice, since every
    other test compares this build against itself or against a reference the
    release machine may not have.

    Unlike the parity corpus, this needs nothing but the installed package, so
    it can run inside a fresh wheel on a machine that has never seen the
    reference implementation.
    """
    baseline = json.loads(
        (HERE / "known_answer.json").read_text(encoding="utf-8")
    )
    assert baseline["katVersion"] == known_answer.KAT_VERSION, (
        "KAT_VERSION changed without regenerating known_answer.json. If the "
        "simulation was intentionally changed, regenerate the baseline; if not, "
        "this is the drift the gate exists to catch."
    )
    # The SIMULATION digest is the gate. It covers the trajectory and nothing
    # else, so a mismatch here means either an intended era boundary (which
    # bumps KAT_VERSION) or a platform that disagrees about arithmetic --
    # never a change in what the library reports about itself.
    assert known_answer.simulation_digest() == baseline["simulationSha256"], (
        "the SIMULATION digest moved. Either a trajectory changed and "
        "KAT_VERSION must bump, or a platform disagrees -- which is the "
        "failure this gate exists to catch."
    )
    # The METADATA digest covers `model_preset()`'s report, which runs
    # nothing. It is asserted so a silent change is still caught, but it can
    # be re-based on its own when a reporting bug is fixed, without anyone
    # having to claim the market changed. Before the split, those two cases
    # were indistinguishable and the choice was between two false statements.
    assert known_answer.metadata_digest() == baseline["metadataSha256"], (
        "the reported model preset changed. If that was a deliberate "
        "reporting fix, re-base metadataSha256 alone and leave KAT_VERSION "
        "and simulationSha256 untouched."
    )
    assert known_answer.known_answer_digest() == baseline["sha256"]


def test_a_session_with_the_rate_indices_matches_its_baseline():
    """The simulated rate indices' own digest, beside the three above.

    A separate digest so that adding UST2Y, UST10Y and IGCORP moved none of
    the others: a roster without them is the market it always was. This is
    the claim that a roster with them is one market on every platform too,
    which the determinism workflow checks on each wheel target through this
    file, and across targets through the line `known_answer.py` prints.
    """
    baseline = json.loads(
        (HERE / "known_answer.json").read_text(encoding="utf-8")
    )
    assert known_answer.bonds_digest() == baseline["bondsSha256"], (
        "the rate indices' digest moved. Either their pricing, books or tape "
        "changed and bondsSha256 must be regenerated with the change, or a "
        "platform disagrees."
    )


def test_the_book_known_answer_matches_its_baseline():
    """The agent-facing book's gate, beside the market's.

    `known_answer.py`'s digests are set by a market nobody trades, which the
    book's dials cannot move, so that gate is blind to the book. This one
    runs a fixed market with every book dial on and several agents in it
    (`known_answer_book.py` says what it covers) and must agree on every
    platform, for the same reason the other must. It lives in this file so
    the determinism workflow, which runs this file on every wheel target,
    runs it too.
    """
    baseline = json.loads(
        (HERE / "known_answer_book.json").read_text(encoding="utf-8")
    )
    assert baseline["bookKatVersion"] == known_answer_book.BOOK_KAT_VERSION, (
        "BOOK_KAT_VERSION changed without regenerating known_answer_book.json.")
    assert baseline["dials"] == known_answer_book.DIALS
    assert known_answer_book.book_digest() == baseline["sha256"], (
        "the book digest moved. Either the book's behaviour changed and "
        "BOOK_KAT_VERSION must bump, or a platform disagrees.")


def test_every_shipped_preset_matches_its_own_baseline():
    """One digest per shipped preset, beside the default's.

    The default's digest moves whenever the default does, so on its own it
    says nothing about the presets a study pinned. `docs/SUPPORT.md` promises
    that a preset's market is frozen once it ships, and this is where that
    promise is checked, on every wheel target the determinism workflow builds.

    A preset the build ships with no row fails here: a new preset gets its
    digest when it lands, from `python tests/known_answer_presets.py`. A row
    that moved is never fixed by regenerating it. It means a frozen preset's
    market changed or a platform disagrees, and each moved preset is named.
    """
    baseline = json.loads(
        (HERE / "known_answer_presets.json").read_text(encoding="utf-8")
    )
    assert baseline["presetKatVersion"] == known_answer_presets.PRESET_KAT_VERSION
    assert (baseline["seed"], baseline["sessions"], baseline["ticks"]) == (
        known_answer_presets.SEED, known_answer_presets.SESSIONS,
        known_answer_presets.TICKS)
    shipped = list(tradefloor.preset_names())
    recorded = list(baseline["presets"])
    assert shipped == recorded, (
        f"the build ships {shipped} and the baseline records {recorded}. A new "
        "preset adds its row when it lands; a recorded one is never removed.")
    measured = known_answer_presets.preset_digests()
    moved = [name for name in shipped if measured[name] != baseline["presets"][name]]
    assert not moved, (
        f"the market moved for {moved}. A shipped preset is frozen: either a "
        "change reached a preset it must not, or a platform disagrees.")
    assert known_answer_presets.combined_digest(measured) == baseline["sha256"]


def test_a_seed_above_two_to_the_thirty_two_matches_its_baseline():
    """64-bit seeding's gate, beside the 32-bit seeds every other digest runs.

    Seeds are 64-bit from 0.8.5, and every seed below 2**32 kept its market:
    the digests above did not move when they widened. This is the other
    half, a wide seed through the raw generator, the universe, the engine's
    substreams and a surgery, which must be one market on every platform.
    A mismatch means 64-bit seeding changed, which its contract in
    `rust/src/rng.rs` forbids, or a platform disagrees. Neither is fixed by
    regenerating the baseline.
    """
    baseline = json.loads(
        (HERE / "known_answer_seed64.json").read_text(encoding="utf-8")
    )
    assert baseline["seed64KatVersion"] == known_answer_seed64.SEED64_KAT_VERSION
    assert (baseline["seed"], baseline["surgerySeed"], baseline["preset"],
            baseline["sessions"], baseline["ticks"]) == (
        known_answer_seed64.HIGH_SEED, known_answer_seed64.SURGERY_SEED,
        known_answer_seed64.PRESET, known_answer_seed64.SESSIONS,
        known_answer_seed64.TICKS)
    assert baseline["seed"] >= 2**32
    assert known_answer_seed64.high_seed_digest() == baseline["sha256"], (
        "the 64-bit seed's market moved. Either the wide derivation changed, "
        "which no release may do, or a platform disagrees.")


def _traded_baseline() -> dict:
    return json.loads(
        (HERE / "known_answer_traded.json").read_text(encoding="utf-8"))


def test_a_traded_run_matches_its_baseline():
    """A run through `tradefloor.evaluate`, beside the engine's digests.

    Every other digest here covers the engine. This one covers what an
    agent benchmark reports: the reference agents and a scripted
    limit-order agent through `evaluate` on pt-v20, with each agent's
    order log, fills and scorecard hashed (`known_answer_traded.py` says
    what each covers). It must agree on every platform the determinism
    workflow builds.

    The failure names the agent and the part that moved. A deliberate
    change to pt-v20, a reference agent or the scoring is re-based with
    `python tests/known_answer_traded.py --write`, with a sentence in the
    baseline's note. A digest that moved on one platform only is never
    re-based.
    """
    baseline = _traded_baseline()
    k = known_answer_traded
    assert baseline["tradedKatVersion"] == k.TRADED_KAT_VERSION, (
        "TRADED_KAT_VERSION changed without regenerating "
        "known_answer_traded.json.")
    assert (baseline["seed"], baseline["agentSeed"], baseline["preset"],
            baseline["days"], baseline["stepsPerDay"], baseline["ticksPerStep"],
            baseline["cash"], baseline["maxLeverage"]) == (
        k.SEED, k.AGENT_SEED, k.PRESET, k.DAYS, k.STEPS_PER_DAY,
        k.TICKS_PER_STEP, k.CASH, k.MAX_LEVERAGE)
    presets = json.loads(
        (HERE / "known_answer_presets.json").read_text(encoding="utf-8"))
    assert baseline["presetRow"] == presets["presets"][k.PRESET], (
        f"{k.PRESET}'s row in known_answer_presets.json is not the one this "
        "baseline was recorded on, so the preset changed since. Re-base this "
        "baseline on the new preset with `python tests/known_answer_traded.py "
        "--write` and say so in its note.")
    assert list(baseline["agents"]) == list(k.AGENTS)

    buffers = k.part_buffers(*k.traded_run())
    measured = k.part_digests(buffers)
    moved = [f"{name} {part}" for name in k.AGENTS for part in k.PARTS
             if measured[name][part] != baseline["agents"][name][part]]
    assert not moved, (
        f"the traded run moved: {', '.join(moved)}. If pt-v20, a reference "
        "agent or the scoring changed on purpose, bump TRADED_KAT_VERSION, "
        "re-base with `python tests/known_answer_traded.py --write` and say "
        "what moved in the note. If only this platform disagrees, that is the failure the "
        "determinism workflow exists to catch.")
    data = k.combined_buffer(buffers)
    assert len(data) == baseline["bytes"]
    assert hashlib.sha256(data).hexdigest() == baseline["sha256"]


def test_the_traded_run_hashes_every_scorecard_field():
    """A field added to `Scorecard` has to be placed: hashed by value, by
    count like the message lines, or listed as read off fields that are
    hashed. Left out, the digest would pass while the new field differed
    between platforms."""
    k = known_answer_traded
    placed = (set(k.SCORECARD_FIELDS) | set(k.SCORECARD_COUNTED)
              | set(k.SCORECARD_DERIVED))
    assert placed == set(tradefloor.Scorecard.__slots__), (
        "Scorecard's fields and the ones known_answer_traded.py hashes "
        f"differ: {sorted(placed ^ set(tradefloor.Scorecard.__slots__))}. "
        "Add the new field to SCORECARD_FIELDS or SCORECARD_COUNTED, then "
        "re-base with `python tests/known_answer_traded.py --write`.")


def test_recording_the_agents_changes_nothing_they_do():
    """The recorder sits between `evaluate` and each agent. Unwrapped, the
    same agents must score the same to the bit, or the digest would pin a
    run nobody else can make."""
    k = known_answer_traded
    wrapped, _ = k.traded_run()
    plain = k.evaluate(k.agents())
    for name in k.AGENTS:
        assert wrapped[name].as_dict() == plain[name].as_dict(), name


def test_the_traded_run_covers_something():
    """Guard against a digest that passes because nothing traded.

    Every agent fills, the oracle explains its days, and the scripted agent
    reaches every part of `evaluate`'s limit path: a limit that rests and
    fills in a session, one that fills at once, a cancel, and the leverage
    limit refusing an order late in the run.
    """
    k = known_answer_traded
    scores, recorders = k.traded_run()
    for name in k.AGENTS:
        fills = recorders[name].portfolio.fills
        assert fills and scores[name].trades > 0, name
        assert not scores[name].tampered, name
    assert len(scores["oracle"].explanations) == k.DAYS
    resting = recorders["resting"].portfolio.fills
    assert any(f.get("liquidity") == "maker" for f in resting)
    assert any(f.get("limit") is True for f in resting)
    assert all(recorders["resting"].kinds[kind] > 0 for kind in ("L", "C"))
    assert recorders["momentum"].kinds["M"] > 0
    assert scores["resting"].leverage_refusals > 0
    buffers = k.part_buffers(scores, recorders)
    for name in k.AGENTS:
        for part in k.PARTS:
            assert len(buffers[name][part]) > 64, (name, part)


def test_the_preset_digests_tell_the_presets_apart():
    """A harness that gave two presets one digest could not see a change that
    turned one into the other, so every shipped preset's must differ."""
    baseline = json.loads(
        (HERE / "known_answer_presets.json").read_text(encoding="utf-8")
    )
    digests = list(baseline["presets"].values())
    assert len(set(digests)) == len(digests)


def test_known_answer_is_stable_within_a_process():
    assert known_answer.known_answer_digest() == known_answer.known_answer_digest()


def test_known_answer_actually_covers_something():
    """Guard against a gate that passes because it measures nothing.

    A KAT that hashed an empty buffer would be perfectly stable across every
    platform and would prove nothing at all.
    """
    buf = known_answer.known_answer_buffer()
    assert len(buf) % 8 == 0, "buffer is not a whole number of f64 values"
    assert len(buf) > 4000, f"buffer only {len(buf)} bytes - coverage shrank"
    # Not all one value: a buffer of repeated zeros would also be stable.
    assert len(set(buf[i:i + 8] for i in range(0, len(buf), 8))) > 100


@pytest.mark.parametrize("script", ["known_answer_book.py",
                                    "known_answer_presets.py",
                                    "known_answer_seed64.py",
                                    "known_answer_traded.py"])
def test_each_known_answer_script_names_the_package(script, tmp_path):
    """The header each script prints is the line a developer reads when the
    gate runs. It named the crate by its name before the rename (#160)."""
    import subprocess

    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent / script)],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    first = result.stdout.splitlines()[0]
    assert first.startswith("tradefloor "), first


def test_the_script_runs_as_the_gate_runs_it(tmp_path):
    """Execute known_answer.py the way CI does: as a script, from elsewhere.

    Everything above imports the module and calls into it. That left the
    ``__main__`` path -- the only path the determinism gate ever takes --
    completely uncovered, and broken: it called ``tradefloor.version()``,
    which the package had never re-exported. Every test passed and the gate
    failed on all five platforms at once.

    Run from tmp_path rather than the repo root, for the same reason CI does:
    from the repo root an importable source tree can satisfy ``import
    pretium`` and the gate would be measuring the checkout instead of the
    wheel it is supposed to certify.
    """
    import re
    import subprocess

    script = Path(__file__).parent / "known_answer.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    # The first line a developer reads names the package by its name.
    assert result.stdout.startswith("tradefloor known-answer test v"), (
        result.stdout.splitlines()[0])
    digests = re.findall(r"\b[0-9a-f]{64}\b", result.stdout)
    # SEVEN digests, in a fixed order: combined, simulation, metadata, the
    # session with the simulated rate indices, every shipped preset's
    # digest combined, a seed above 2**32, and a traded run through
    # evaluate. The CI gate greps all of them and compares the SET across
    # platforms, so the count and the order are both contractual --
    # .github/workflows/determinism.yml hashes each target's file and
    # requires one unique hash, which holds for seven as it did for three.
    #
    # It used to be exactly one, and the count was asserted for the same
    # reason it is asserted now: a gate that greps an ambiguous number of
    # digests is comparing something nobody specified. When the digest split
    # landed, this test and that workflow had to move together -- leaving the
    # workflow alone would have made it count three digests as three
    # disagreements and fail every green run.
    assert len(digests) == 7, result.stdout
    assert digests[0] == known_answer.known_answer_digest()
    assert digests[1] == known_answer.simulation_digest()
    assert digests[2] == known_answer.metadata_digest()
    assert digests[3] == known_answer.bonds_digest()
    # FIVE since 0.8.5: every shipped preset's digest, combined.
    assert digests[4] == known_answer_presets.combined_digest(
        known_answer_presets.preset_digests())
    # SIX since 0.8.5, when seeds became 64-bit: a seed above 2**32.
    assert digests[5] == known_answer_seed64.high_seed_digest()
    # SEVEN since 0.8.5: a traded run through evaluate, the reference
    # agents' orders, fills and scorecards.
    assert digests[6] == _traded_baseline()["sha256"]
