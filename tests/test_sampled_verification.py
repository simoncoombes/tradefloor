"""Sampled verification: a Merkle root over per-day state, and a verifier.

Four properties, each with a test that fails if it is lost. The per-day hash
covers every field a snapshot carries and has one implementation in Rust and
one in Python that agree. Checking k days costs k days of simulation. A
tampered day fails on its own leaf and a tampered state fails on the day that
follows it. And nothing here moves an existing trajectory: `reproduce()`
behaves the same with the ledger block and without it.
"""

import json
import struct

import pytest

import tradefloor as tf
from tradefloor import manifest as mf
from tradefloor.manifest import _day_spans, _proof_holds, state_hash

#: The roster, seed and length the design note fixes the cost measurement on.
UNIVERSE = list(tf.Universe.random(8, seed=99))
SEED = 42
DAYS = 12

#: Short days, so the whole file runs in seconds. The cost claim is a ratio,
#: so it holds at any tick count and this one keeps the suite quick.
TICKS = 30


def run(days=DAYS, ticks=TICKS, snapshots=True, seed=SEED, record=False):
    """A finished run, its ledger and its manifest."""
    engine = tf.Engine(seed=seed, universe=UNIVERSE)
    ledger = tf.DayLedger(snapshots=snapshots)
    engine.run_days(days, record=record, ticks_per_day=ticks, ledger=ledger)
    manifest = tf.RunManifest.of(engine, seed=seed, universe=UNIVERSE,
                                 ledger=ledger)
    return engine, ledger, manifest


# --------------------------------------------------------------------------
# 1. The per-day hash
# --------------------------------------------------------------------------


def test_the_rust_hash_and_the_python_twin_agree():
    """The contract the whole package rests on, asserted directly.

    `Engine.state_hash` walks the engine's own fields in Rust and
    `manifest.state_hash` walks the dict `state_snapshot` returns in Python.
    They are two implementations of one encoding, and a ledger is only
    checkable by a reader holding a snapshot if they produce the same bytes.
    """
    engine = tf.Engine(seed=SEED, universe=UNIVERSE)
    for _ in range(3):
        engine.run_days(1, record=False, ticks_per_day=TICKS)
        assert engine.state_hash() == state_hash(engine.state_snapshot())

    # And mid-day, where the day accumulators and the market-open flag are
    # the halves of the state a close-boundary leaf never exercises.
    engine.open_market()
    engine.run_session(9, 30, 3, 10)
    assert engine.state_hash() == state_hash(engine.state_snapshot())


def test_the_two_hashes_agree_on_a_day_that_carries_news():
    """The endogenous news path, which eight names rarely reach.

    `session_news` is per-day state generated at `open_market` and cleared at
    the next one, so a close-boundary leaf carries the day's events. At the
    shipped intensity of 0.05 a roster of eight fires on few days, and the
    roster the cost measurement uses would leave the list empty in most of
    them. Forty names reach it, so the encoding of an optional ticker, an
    optional sector and an optional impact is measured rather than assumed.
    """
    roster = list(tf.Universe.random(40, seed=7))
    engine = tf.Engine(seed=SEED, universe=roster)
    with_news = 0
    for _ in range(20):
        engine.open_market()
        engine.run_session(9, 30, 3, 20)
        engine.close_market()
        snapshot = engine.state_snapshot()
        if snapshot["session_news"]:
            with_news += 1
            assert engine.state_hash() == state_hash(snapshot)
    assert with_news >= 5, (
        f"only {with_news} of twenty days carried news, so this test barely "
        "reached the path it is named for"
    )


def _nudge(value):
    """The same float, changed, by flipping the low bit of its pattern.

    Adding one is the obvious perturbation and it is a no-op on much of what
    a snapshot carries. Four of the twenty-one generator words reinterpret to
    doubles at or above 2 ** 53, where `value + 1.0 == value`, so a walk built
    on addition would report four covered fields as uncovered.

    A NaN is replaced rather than flipped, because flipping one gives another
    NaN and every NaN writes the same canonical pattern wherever `_f64` is
    the encoder. `tick_fundamental` is NaN until the first tick.
    """
    if value != value:
        return 1.0
    bits = struct.unpack("<Q", struct.pack("<d", value))[0] ^ 1
    return struct.unpack("<d", struct.pack("<Q", bits))[0]


def _slot(buffer, index):
    """The same transport buffer with one f64 slot changed."""
    at = index * 8
    head = struct.unpack("<d", buffer[at:at + 8])[0]
    return buffer[:at] + struct.pack("<d", _nudge(head)) + buffer[at + 8:]


def _moved(value):
    """The same value, changed, whatever kind of value it is."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, bytes):
        return _slot(value, 0)
    if isinstance(value, str):
        return value + "x"
    if isinstance(value, float):
        return _nudge(value)
    if isinstance(value, int):
        return value + 1
    if isinstance(value, dict):
        # One endogenous news event. Flipping the optional ticker exercises
        # the presence byte as well as the string behind it.
        out = dict(value)
        out["ticker"] = None if out["ticker"] is not None else "ZZZ"
        return out
    if isinstance(value, tuple):
        # One overlay entry: (stream, kind, index, value). Moving the
        # value rather than the address keeps it a well-formed entry.
        return tuple(value[:-1]) + (_moved(value[-1]),)
    if isinstance(value, list):
        if not value:
            return [{"ticker": "ZZZ", "sector": "technology",
                     "price_impact": 0.25}]
        return [_moved(value[0])] + list(value[1:])
    if value is None:
        return 1.0
    raise AssertionError(f"no perturbation for {type(value).__name__}")


def _differs(left, right):
    """Whether two snapshot values differ, NaN and None included.

    `NaN != NaN`, so a plain comparison reports a difference where there is
    none. The generator array is the case that matters, since it carries u64
    bit patterns as floats and several of them reinterpret to NaN.
    """
    if left is None or right is None:
        return left is not right
    if isinstance(left, float) and isinstance(right, float):
        return struct.pack("<d", left) != struct.pack("<d", right)
    if isinstance(left, list) and isinstance(right, list):
        return (len(left) != len(right)
                or any(_differs(a, b) for a, b in zip(left, right)))
    if isinstance(left, dict) and isinstance(right, dict):
        return (set(left) != set(right)
                or any(_differs(left[k], right[k]) for k in left))
    return left != right


def _mutations(label, value):
    """Every one-field change worth making to one snapshot value.

    A list is walked element by element. Walking only the first left twenty
    of the twenty-one generator words, five of the six variance slots, three
    of the four GDP-trend points and every news event after the first
    untested, which is most of what a written list of fields can lose.

    A transport buffer is walked at its first and last slot rather than
    throughout. It is decoded in one `struct.unpack` against a length the
    hash computes from the roster, so a read that stopped short raises rather
    than hashing less, and two slots are the cheap second opinion.
    """
    if isinstance(value, bytes):
        yield f"{label}[0]", _slot(value, 0)
        last = len(value) // 8 - 1
        if last > 0:
            yield f"{label}[{last}]", _slot(value, last)
    elif label == "draw_overlay" and not value:
        # An empty overlay perturbs to one installed substitution. The
        # generic empty-list perturbation is a news event, which is the
        # only empty list this walk met before draw addressing added a
        # second one, and it is not this field's shape.
        yield label, [(0, 0, 0, 0.5)]
    elif isinstance(value, list) and value:
        for i, element in enumerate(value):
            yield (f"{label}[{i}]",
                   value[:i] + [_moved(element)] + value[i + 1:])
    else:
        yield label, _moved(value)


def _walk(snapshot):
    """One mutated snapshot per field, labelled. The walk `test_forking` makes.

    Nested dicts are walked field by field rather than replaced whole, so the
    economy's forty-two entries and the central bank's eight are each tested
    on their own instead of behind one mutation of the dict that holds them.
    """
    for key, value in snapshot.items():
        if isinstance(value, dict):
            for inner, inner_value in value.items():
                for label, moved in _mutations(f"{key}.{inner}", inner_value):
                    mutated = dict(snapshot)
                    mutated[key] = dict(value)
                    mutated[key][inner] = moved
                    yield label, mutated
        else:
            for label, moved in _mutations(key, value):
                mutated = dict(snapshot)
                mutated[key] = moved
                yield label, mutated


def test_the_hash_moves_when_any_snapshot_field_moves():
    """Every field a snapshot carries reaches the leaf.

    The drift guard for the hash, and it needs one for the reason
    `state_snapshot` needed one: that method is a written list of fields and
    the list has been wrong six times. A hash over a written list can go the
    same way, and a field that quietly stopped being covered would leave a
    ledger committing to a state it does not describe, with every day still
    verifying.

    Asserted one field at a time rather than by comparing two whole
    snapshots. A single change is what a tampered archive looks like, and a
    walk that moved everything at once would pass while most of the fields
    it names were uncovered.
    """
    engine = tf.Engine(seed=SEED, universe=UNIVERSE)
    engine.run_days(2, record=False, ticks_per_day=TICKS)
    engine.open_market()
    engine.run_session(9, 30, 3, 10)
    snapshot = engine.state_snapshot()
    base = state_hash(snapshot)

    fields = list(_walk(snapshot))
    labels = [label for label, _ in fields]
    named = {label.split("[")[0] for label in labels}
    assert {name.split(".")[0] for name in named} == set(mf._SNAPSHOT_KEYS)
    assert {name.split(".", 1)[1] for name in named
            if name.startswith("columns.")} == set(mf._STATE_HASH_COLUMNS)
    assert {name.split(".", 1)[1] for name in named
            if name.startswith("economy.")} == set(mf._ECONOMY_KEYS)
    assert {name.split(".", 1)[1] for name in named
            if name.startswith("central_bank.")} == set(
                mf._CENTRAL_BANK_FIELDS)

    # The lists, element by element, which is the half a walk loses first.
    def slots(prefix):
        return sum(1 for label in labels if label.startswith(prefix))

    assert slots("rng[") == 3 * len(tf.noise.STREAMS)
    assert slots("market_variance[") == 6
    assert slots("economy.gdp_trend[") == 4
    assert slots("tickers[") == len(UNIVERSE)
    assert slots("columns.price[") == 2

    unmoved = [label for label, mutated in fields
               if state_hash(mutated) == base]
    assert unmoved == [], (
        f"changing {unmoved} left the leaf unchanged, so the hash does not "
        "cover them. A ledger would commit to a state it does not describe."
    )


def test_every_mutation_the_walk_makes_is_a_real_change():
    """Guards the guard, and it needs guarding.

    A walk proves nothing unless each of its mutations changes the value it
    touches. Adding one to a float is the perturbation that looks right and
    is a no-op on four of the generator words, and a walk built that way
    would report those four as uncovered while the hash covered them.
    """
    engine = tf.Engine(seed=SEED, universe=UNIVERSE)
    engine.run_days(1, record=False, ticks_per_day=TICKS)
    snapshot = engine.state_snapshot()

    inert = []
    for label, mutated in _walk(snapshot):
        key = label.split("[")[0].split(".")[0]
        if not _differs(mutated[key], snapshot[key]):
            inert.append(label)
    assert inert == [], (
        f"the walk left {inert} unchanged, so those fields are named by the "
        "guard above and not tested by it"
    )


def test_the_hash_refuses_a_snapshot_it_does_not_recognise():
    """A field added to the engine fails here rather than dropping out.

    The complement of the walk above. That one proves every field the hash
    knows about reaches the leaf; this proves a field it does NOT know about
    stops the hash instead of being skipped, which is how the guard survives
    the next time the engine grows.
    """
    engine = tf.Engine(seed=SEED, universe=UNIVERSE)
    snapshot = engine.state_snapshot()

    grown = dict(snapshot)
    grown["something_new"] = 1.0
    with pytest.raises(tf.ValidationError, match="something_new"):
        state_hash(grown)

    shrunk = dict(snapshot)
    del shrunk["volume_state"]
    with pytest.raises(tf.ValidationError, match="volume_state"):
        state_hash(shrunk)

    economy = dict(snapshot)
    economy["economy"] = {k: v for k, v in snapshot["economy"].items()
                          if k != "vix"}
    with pytest.raises(tf.ValidationError, match="vix"):
        state_hash(economy)


def test_two_days_of_one_run_hash_apart():
    """Consecutive leaves are distinct, which is what a ledger needs.

    A hash that collapsed neighbouring days would let one be swapped for
    another and verify.
    """
    engine = tf.Engine(seed=SEED, universe=UNIVERSE)
    ledger = tf.DayLedger()
    engine.run_days(DAYS, record=False, ticks_per_day=TICKS, ledger=ledger)
    assert len(set(ledger.leaves)) == DAYS


# --------------------------------------------------------------------------
# 2. The tree
# --------------------------------------------------------------------------


@pytest.mark.parametrize("days", [1, 2, 3, 5, 8, 12])
def test_every_leaf_proves_against_the_root(days):
    """Every day's proof reaches the root, at every leaf count.

    Parametrised over odd and even counts because the padding rule is where a
    Merkle implementation goes wrong: an odd level pairs its last node with
    itself, and an off-by-one there produces proofs that verify for some days
    and not others.
    """
    _, ledger, _ = run(days=days)
    root = ledger.root()
    for day in range(days):
        proof = ledger.proof(day)
        assert _proof_holds(ledger.leaves[day], day, proof, root), (
            f"day {day} of {days} does not prove against the root"
        )


def test_a_proof_fails_for_the_wrong_day_and_the_wrong_leaf():
    """The companion assertion: the proof check can fail.

    A membership check that accepted everything would pass the test above and
    be worthless.
    """
    _, ledger, _ = run(days=8)
    root = ledger.root()
    assert not _proof_holds(ledger.leaves[3], 4, ledger.proof(3), root)
    assert not _proof_holds("0" * 64, 3, ledger.proof(3), root)
    assert not _proof_holds(ledger.leaves[3], 3, ledger.proof(4), root)


def test_a_truncated_ledger_has_a_different_root():
    """Duplicate-last padding, asserted as the property it exists for.

    Promoting a lone node to the next level instead would give a nine-day
    ledger and an eight-day one the same root whenever the last leaf repeated,
    so a run could be cut short and still verify.
    """
    _, ledger, _ = run(days=9)
    short = tf.DayLedger()
    short.leaves = list(ledger.leaves[:8])
    short.snapshots = list(ledger.snapshots[:8])
    assert short.root() != ledger.root()

    padded = tf.DayLedger()
    padded.leaves = list(ledger.leaves[:8]) + [ledger.leaves[7]]
    padded.snapshots = list(ledger.snapshots[:8]) + [ledger.snapshots[7]]
    assert padded.root() != short.root(), (
        "a nine-leaf ledger whose last leaf repeats must not root as the "
        "eight-leaf ledger it was padded from"
    )


def test_a_ledger_round_trips_through_json():
    """The archive form carries the states back bit for bit.

    The buffers travel base64-encoded rather than as JSON numbers, because
    `tick_fundamental` and the generator array carry NaN as a VALUE and JSON
    cannot round-trip one. Asserted by re-hashing the loaded snapshots: if a
    bit moved, the leaves would no longer match.
    """
    _, ledger, _ = run(days=5)
    loaded = tf.DayLedger.from_json(ledger.to_json())
    assert loaded.leaves == ledger.leaves
    assert loaded.root() == ledger.root()
    assert [state_hash(s) for s in loaded.snapshots] == ledger.leaves

    small = tf.DayLedger.from_json(ledger.to_json(with_snapshots=False))
    assert small.snapshots is None
    assert small.leaves == ledger.leaves
    assert len(ledger.to_json(with_snapshots=False)) < len(ledger.to_json())


def test_a_ledger_refuses_a_hash_version_it_does_not_compute():
    """A leaf from another version of the hash is a different measurement."""
    _, ledger, _ = run(days=3)
    payload = json.loads(ledger.to_json())
    payload["hash"] = "state/99"
    with pytest.raises(tf.ValidationError, match="state/99"):
        tf.DayLedger.from_json(json.dumps(payload))


# --------------------------------------------------------------------------
# 3. The cost
# --------------------------------------------------------------------------


def _ticks_in(entries):
    """Ticks in a segment, counted by this test rather than by the library.

    The cost claim is the point of the package, so the number it is checked
    against is recomputed here instead of being read back out of the code
    that produced it.
    """
    total = 0
    for entry in entries:
        if entry["op"] == "tick":
            total += 1
        elif entry["op"] == "run_session":
            total += entry["ticks"]
    return total


@pytest.mark.parametrize("k", [1, 3, 12])
def test_verifying_k_days_costs_k_day_runs(k):
    """The measurement the package exists for.

    Universe.random(8, seed=99), seed 42, twelve days: verifying k of them
    replays k days and no more, whatever k is and however long the run. The
    ticks are counted from the segments the verifier actually replayed, and
    compared against a day's ticks recomputed here from the manifest's own
    log.
    """
    engine, ledger, manifest = run()
    spans = _day_spans(manifest.order_log)
    one_day = _ticks_in(manifest.order_log[spans[0][0]:spans[0][1]])
    assert one_day == TICKS

    report = mf.verify(manifest, ledger, k, seed=5)
    assert report.ok, report.describe()
    assert report.k == k
    assert report.day_runs == k
    assert report.ticks == k * one_day

    whole_run = _ticks_in(manifest.order_log)
    assert whole_run == DAYS * TICKS, (
        "the run itself is twelve days, so a k-day verification is k twelfths "
        "of what reproduce() costs"
    )


def test_a_ledger_without_snapshots_costs_the_days_it_replays():
    """The documented alternative, priced rather than hidden.

    Without a committed predecessor the only way to reach day d - 1 is to run
    to it, so day d costs d + 1 days. The report says so, and the caveat says
    what a ledger with snapshots would have cost instead.
    """
    engine, ledger, manifest = run(snapshots=False)
    assert ledger.snapshots is None
    assert ledger.keeps_snapshots is False

    report = mf.verify(manifest, ledger, 3, seed=5)
    assert report.ok, report.describe()
    assert report.day_runs == sum(day + 1 for day in report.days)
    assert report.ticks == report.day_runs * TICKS
    assert any("no snapshots" in caveat for caveat in report.caveats)


def test_the_sample_is_reproducible_and_depends_on_the_seed():
    """A verification is repeatable only if its days can be named.

    The library's own PCG32 rather than `random`, so the same seed draws the
    same days on every platform and every Python version.
    """
    _, ledger, manifest = run()
    first = mf.verify(manifest, ledger, 4, seed=7).days
    assert mf.verify(manifest, ledger, 4, seed=7).days == first
    assert len(set(first)) == 4
    others = {mf.verify(manifest, ledger, 4, seed=s).days
              for s in range(1, 12)}
    assert len(others) > 1, "every seed drew the same four days"


def test_the_caveats_name_the_sample_and_are_computed():
    """Every result carries what it does and does not establish.

    The rule `mcp.py` sets: a caveat is computed from the call rather than
    retyped, so it cannot go on being printed after the thing it describes
    has changed. Here that means the day list, the count and the cost.
    """
    _, ledger, manifest = run()
    report = mf.verify(manifest, ledger, 4, seed=7)
    joined = " ".join(report.caveats)
    assert "4 of the 12 days" in joined
    assert f"{report.day_runs} day-runs" in joined
    assert "the recorded tape" in joined

    whole = mf.verify(manifest, ledger, DAYS, seed=7)
    assert any("rests on no sampling" in caveat for caveat in whole.caveats)
    assert not any("rests on no sampling" in caveat
                   for caveat in report.caveats)


def test_the_caveat_says_when_a_day_came_from_construction():
    """Day 0 has no predecessor, and the caveat has to know it.

    The cost caveat is computed from the sample rather than from whether the
    ledger carries states at all. Written the other way it said "each sampled
    day was replayed from its committed predecessor" for a verification of
    day 0 alone, in which none was.
    """
    _, ledger, manifest = run()

    alone = mf.verify(manifest, ledger, 1, seed=12)
    assert alone.days == (0,) and alone.restored == 0
    joined = " ".join(alone.caveats)
    assert "Day 0 has no committed predecessor" in joined
    assert "committed predecessor. The sample cost" not in joined

    mixed = mf.verify(manifest, ledger, 4, seed=4)
    assert 0 in mixed.days and mixed.restored == 3
    joined = " ".join(mixed.caveats)
    assert "Day 0 has no committed predecessor" in joined
    assert "The other 3 days started from a committed predecessor" in joined

    without = mf.verify(manifest, ledger, 4, seed=7)
    assert 0 not in without.days and without.restored == 4
    assert any("Each sampled day was replayed from its committed predecessor"
               in caveat for caveat in without.caveats)


def _says(text, phrase):
    """Whether `phrase` appears in `text` as whole words.

    Written on split words rather than a regex, and that is a scar. The
    first version of the guard below used a word boundary in a raw f-string
    and shipped two literal backspace bytes instead of the two characters
    that spell one, so the compiled pattern was the phrase wrapped in a
    control character no caveat can contain. The assertion was vacuously
    true for every input, it survived a local run and five CI targets, and
    it was weaker than the bare substring test it replaced. There is no
    escape sequence here to get wrong.
    """
    stripped = text
    for mark in ".,;:()":
        stripped = stripped.replace(mark, " ")
    words = stripped.split()
    target = phrase.split()
    return any(words[i:i + len(target)] == target
               for i in range(len(words) - len(target) + 1))


def test_a_caveat_never_puts_a_number_before_the_wrong_plural(monkeypatch):
    """These strings are read by a person, so "1 days" is a defect.

    Every count in a caveat goes through `_count`, which is the only reason
    the singular case reads. Checked on a one-day run verified at k of 1,
    which is where every number in the sentence lands on one.

    Then checked again against a subject that is broken on purpose, because
    the first version of this test could not fail. `_count` is replaced by
    one that always pluralises, and the same phrases that must be absent
    above must be present below. Without that half, a guard can report green
    over a defect it is named for, which is what happened here.
    """
    wrong = ("1 days", "1 day-runs")
    _, ledger, manifest = run(days=1)

    joined = " ".join(mf.verify(manifest, ledger, 1, seed=3).caveats)
    assert [w for w in wrong if _says(joined, w)] == [], joined
    assert _says(joined, "1 day") and _says(joined, "1 day-run")

    monkeypatch.setattr(mf, "_count", lambda n, noun: f"{n} {noun}s")
    broken = " ".join(mf.verify(manifest, ledger, 1, seed=3).caveats)
    assert [w for w in wrong if _says(broken, w)] == list(wrong), (
        "the guard cannot see a broken _count, so it is not a guard: "
        + broken
    )


def test_the_word_match_reads_whole_words():
    """The reason the guard is word-bounded at all.

    A bare substring test finds "1 days" inside "11 days", which is a
    correct sentence, and fails on it. That is the defect the first version
    tried to avoid and introduced a worse one doing.
    """
    assert not _says("The root covers the other 11 days.", "1 days")
    assert _says("recomputed 1 days on this build", "1 days")
    assert not _says("The sample cost 1 day-runs.", "1 day-run")
    assert _says("The sample cost 1 day-run.", "1 day-run")


def test_an_empty_ledger_has_no_root_and_no_proof():
    """A ledger that crossed no close boundary commits to nothing.

    Refused by name at both entry points. A root over zero leaves would
    otherwise come out of `_merkle_levels` as an index error, which tells a
    reader that a list was empty and nothing about what they are holding.
    """
    empty = tf.DayLedger()
    assert empty.count == 0 and len(empty) == 0
    with pytest.raises(tf.ValidationError, match="holds no days"):
        empty.root()
    with pytest.raises(tf.ValidationError, match="outside this ledger"):
        empty.proof(0)
    assert "empty" in repr(empty)


def test_a_leaf_that_is_not_a_hash_is_refused_on_load():
    """Every malformed ledger is refused by name, this one included.

    A four-character leaf still hashes into the tree and produces a root, so
    a ledger holding one verifies against itself and commits to nothing. A
    non-hex leaf reaches `bytes.fromhex` and raises a bare `ValueError` out
    of a public reader, which is the one exception shape this module does not
    otherwise produce.
    """
    _, ledger, _ = run(days=5)
    for bad in ("not-hex", "abababab", "A" * 64, 123, ledger.leaves[0][:63]):
        payload = json.loads(ledger.to_json(with_snapshots=False))
        payload["leaves"][2] = bad
        with pytest.raises(tf.ValidationError, match="leaf 2 of this ledger"):
            tf.DayLedger.from_json(json.dumps(payload))

    reloaded = tf.DayLedger.from_json(ledger.to_json(with_snapshots=False))
    assert reloaded.leaves == ledger.leaves


def test_a_recorded_run_verifies():
    """`record=True` is the `run_days` default, so it is the common path.

    The recorded tape is not in a snapshot, so a restored engine starts the
    day with an empty one and fills it from the day's own sessions. Asserted
    rather than assumed, because a leaf that depended on the tape would fail
    here and nowhere else in this file.
    """
    engine, ledger, manifest = run(record=True)
    assert engine.recorded_days == DAYS
    report = mf.verify(manifest, ledger, DAYS, seed=5)
    assert report.ok, report.describe()
    assert report.day_runs == DAYS


# --------------------------------------------------------------------------
# 4. Tampering
# --------------------------------------------------------------------------


def test_a_tampered_leaf_fails():
    """One edited leaf, and the report names both what and where.

    The root no longer matches the manifest's, which is the cheap check, and
    the day the leaf belongs to does not recompute to it, which is the one
    that says which day moved. The two are reported apart:
    `test_a_root_mismatch_is_not_counted_as_a_failed_day` says what happened
    when they were not.
    """
    _, ledger, manifest = run()
    tampered = tf.DayLedger.from_json(ledger.to_json())
    tampered.leaves[5] = "0" * 64

    report = mf.verify(manifest, tampered, DAYS, seed=5)
    assert not report.ok
    assert not report.root_ok
    assert "the ledger's root is" in report.root_note
    assert [f.split(":")[0] for f in report.replay_failures] == ["day 5"]
    assert report.replayed == DAYS - 1
    with pytest.raises(tf.ValidationError, match="did not replay"):
        report.check()


def test_a_tampered_snapshot_fails_on_the_day_that_follows_it():
    """A state edited in transit surfaces on the next day, not its own.

    A snapshot is what the NEXT day is replayed from, so editing day 4's
    state leaves day 4's leaf intact and makes day 5 replay from a market
    that never existed.
    """
    _, ledger, manifest = run()
    tampered = tf.DayLedger.from_json(ledger.to_json())
    snapshot = tampered.snapshots[4]
    columns = dict(snapshot["columns"])
    prices = list(struct.unpack("<8d", columns["price"]))
    prices[0] += 1.0
    columns["price"] = struct.pack("<8d", *prices)
    snapshot["columns"] = columns
    tampered.snapshots[4] = snapshot

    report = mf.verify(manifest, tampered, DAYS, seed=5)
    assert not report.ok
    assert [f.split(":")[0] for f in report.failures] == ["day 5"], (
        "an edited state must fail on the day replayed from it, and on that "
        "day alone"
    )


def _tamper_leaf(ledger):
    """Edit a leaf, which moves the root."""
    leaf = ledger.leaves[4]
    ledger.leaves[4] = ("1" if leaf[0] == "0" else "0") + leaf[1:]


def _tamper_snapshot(ledger):
    """Edit a predecessor state, which leaves the root alone."""
    snapshot = ledger.snapshots[4]
    columns = dict(snapshot["columns"])
    prices = list(struct.unpack("<8d", columns["price"]))
    prices[0] += 1.0
    columns["price"] = struct.pack("<8d", *prices)
    snapshot["columns"] = columns


@pytest.mark.parametrize("tamper", [None, _tamper_leaf, _tamper_snapshot],
                         ids=["clean", "edited-leaf", "edited-state"])
def test_the_failure_count_is_over_days_and_cannot_exceed_the_sample(tamper):
    """The count is a count of days, whatever else went wrong.

    Asserted as the derivation rather than as a number, because the defect
    this guards was a number that happened to be one larger than k. A root
    mismatch is one fact about the ledger and belongs to no day, so counting
    it among them produced "10 of 9 sampled days" and no assertion in this
    file bound the arithmetic.

    Runs over a clean ledger, an edited leaf and an edited predecessor state,
    so the invariant holds when the root moves and when it does not.
    """
    _, ledger, manifest = run()
    if tamper is not None:
        ledger = tf.DayLedger.from_json(ledger.to_json())
        tamper(ledger)

    report = mf.verify(manifest, ledger, DAYS, seed=5)

    assert len(report.replay_failures) <= report.k
    assert report.replayed + len(report.replay_failures) == report.k
    named = {int(entry.split(":")[0].split()[1])
             for entry in report.replay_failures}
    assert named <= set(report.days), (
        "a replay failure named a day this verification did not sample"
    )
    assert report.ok == (report.root_ok and not report.replay_failures
                         and not report.proof_failures)

    if report.ok:
        assert tamper is None
        return

    with pytest.raises(tf.ValidationError) as raised:
        report.check()
    message = str(raised.value)
    if report.replay_failures:
        assert f"{len(report.replay_failures)} of " in message
        for day in set(report.days) - named:
            assert f"day {day}:" not in message, message
    assert f"{report.k + 1} of " not in message


def test_a_root_mismatch_is_not_counted_as_a_failed_day():
    """One edited leaf is one edited day, however many proofs it breaks.

    A tampered leaf moves the ledger's root, and inside `verify` a proof is
    built from the leaves that produce that root, so it recomputes to the
    ledger's own root and reaches the manifest's exactly when the two agree.
    Every sampled day's proof therefore fails together, saying nothing the
    root comparison had not already said.

    Reported into one list beside the per-day results, those entries joined
    the single real finding and the count over the list read a nine-day
    sample as ten failed days, while eight days that replayed perfectly were
    listed as failures. The roster, seed and tamper below are the ones the
    defect was found on.
    """
    roster = list(tf.Universe.random(11, seed=222))
    engine = tf.Engine(seed=8675309, universe=roster)
    ledger = tf.DayLedger()
    engine.run_days(9, record=False, ticks_per_day=TICKS, ledger=ledger)
    manifest = tf.RunManifest.of(engine, seed=8675309, universe=roster,
                                 ledger=ledger)

    tampered = tf.DayLedger.from_json(ledger.to_json())
    leaf = tampered.leaves[4]
    tampered.leaves[4] = ("1" if leaf[0] == "0" else "0") + leaf[1:]

    report = mf.verify(manifest, tampered, 9, seed=1)
    assert not report.ok
    assert not report.root_ok
    assert report.replayed == 8
    assert [f.split(":")[0] for f in report.replay_failures] == ["day 4"]
    assert report.proof_failures == (), (
        "a proof failing under a root the ledger reproduces would be a "
        "defect in the tree, and the root here has moved"
    )

    with pytest.raises(tf.ValidationError) as raised:
        report.check()
    message = str(raised.value)
    assert "1 of 9 sampled days did not replay" in message
    assert "The remaining 8 replayed" in message
    assert "10 of 9" not in message
    assert message.count("day 4:") == 1
    for day in (0, 1, 2, 3, 5, 6, 7, 8):
        assert f"day {day}:" not in message, message

    rendered = report.describe()
    assert "root: MOVED" in rendered
    assert "replay: 8 of 9 sampled days reproduced" in rendered


def test_a_ledger_from_another_run_is_refused_rather_than_reported():
    """Two artifacts that do not describe one run are a caller error.

    Refused before any day is replayed, because a report saying "twelve of
    twelve days failed" would send a reader hunting a divergence when they
    have handed over the wrong file.
    """
    _, ledger, manifest = run()
    _, other, _ = run(days=8)
    with pytest.raises(tf.ValidationError, match="commits to 12 days"):
        mf.verify(manifest, other, 3, seed=5)

    with pytest.raises(tf.ValidationError, match="k must be between"):
        mf.verify(manifest, ledger, 0, seed=5)
    with pytest.raises(tf.ValidationError, match="k must be between"):
        mf.verify(manifest, ledger, DAYS + 1, seed=5)


def test_a_manifest_with_no_ledger_block_says_so():
    """The absent case is named rather than crashing on a missing key."""
    engine, ledger, _ = run()
    plain = tf.RunManifest.of(engine, seed=SEED, universe=UNIVERSE)
    assert plain.day_ledger is None
    with pytest.raises(tf.ValidationError, match="carries no day ledger"):
        mf.verify(plain, ledger, 3, seed=5)


def test_a_ledger_and_a_log_that_disagree_are_refused_at_write_time():
    """Caught where the pair is made, rather than where it is read."""
    engine = tf.Engine(seed=SEED, universe=UNIVERSE)
    ledger = tf.DayLedger()
    engine.run_days(4, record=False, ticks_per_day=TICKS, ledger=ledger)
    engine.run_days(2, record=False, ticks_per_day=TICKS)
    with pytest.raises(tf.ValidationError, match="crosses 6 day boundaries"):
        tf.RunManifest.of(engine, seed=SEED, universe=UNIVERSE, ledger=ledger)

    empty = tf.Engine(seed=SEED, universe=UNIVERSE)
    with pytest.raises(tf.ValidationError):
        tf.RunManifest.of(empty, seed=SEED, universe=UNIVERSE,
                          ledger=tf.DayLedger())


# --------------------------------------------------------------------------
# 5. Nothing else moved
# --------------------------------------------------------------------------


def test_reproduce_is_unchanged_with_the_ledger_block_and_without_it():
    """The additive-field rule, asserted rather than assumed.

    A manifest written with a ledger and one written without it describe the
    same run and rebuild the same market. The documents differ by the one
    `days` key and nothing else, and the schema stays 1, so a build that
    predates this package reads the newer manifest.
    """
    engine, ledger, with_ledger = run()
    without = tf.RunManifest.of(engine, seed=SEED, universe=UNIVERSE)

    left = json.loads(with_ledger.to_json())
    right = json.loads(without.to_json())
    assert set(left) - set(right) == {"day_ledger"}
    del left["day_ledger"]
    assert left == right
    assert left["schema"] == right["schema"] == mf.MANIFEST_SCHEMA

    rebuilt_with = with_ledger.reproduce()
    rebuilt_without = without.reproduce()
    assert (mf.market_digest(rebuilt_with)
            == mf.market_digest(rebuilt_without)
            == with_ledger.result["digest"])

    loaded = tf.RunManifest.from_json(with_ledger.to_json())
    assert loaded.day_ledger == with_ledger.day_ledger
    assert mf.market_digest(loaded.reproduce()) == with_ledger.result["digest"]


def test_an_edited_ledger_block_is_refused_on_load():
    """The block is presence-checked on the way in, as each part is."""
    _, _, manifest = run()
    payload = json.loads(manifest.to_json())
    del payload["day_ledger"]["count"]
    with pytest.raises(tf.ValidationError, match="day_ledger block"):
        tf.RunManifest.from_json(json.dumps(payload))


def test_a_ledger_costs_the_run_nothing_it_would_not_have_paid():
    """The trajectory is untouched: the same run, ledger or no ledger.

    A hash is a read, so this should be true by construction. It is asserted
    because "reads nothing" is exactly the kind of claim a future change
    breaks quietly, and the whole package would then be moving markets to
    measure them.
    """
    plain = tf.Engine(seed=SEED, universe=UNIVERSE)
    plain.run_days(DAYS, record=False, ticks_per_day=TICKS)

    ledgered = tf.Engine(seed=SEED, universe=UNIVERSE)
    ledgered.run_days(DAYS, record=False, ticks_per_day=TICKS,
                      ledger=tf.DayLedger())

    assert mf.market_digest(plain) == mf.market_digest(ledgered)
    assert plain.draws_consumed == ledgered.draws_consumed
    assert plain.state_hash() == ledgered.state_hash()


# --------------------------------------------------------------------------
# 6. Every loop that crosses a close boundary
# --------------------------------------------------------------------------


def test_the_replay_loop_rebuilds_the_same_ledger():
    """A replayed run commits to the leaves the original did.

    `replay` is the loop `reproduce()` runs, so this is what lets a reader
    rebuild a ledger from a manifest and compare it with the one they were
    sent.
    """
    engine, ledger, _ = run(record=True)
    rebuilt = tf.DayLedger()
    tf.replay(engine.order_log, seed=SEED, universe=UNIVERSE, ledger=rebuilt)
    assert rebuilt.leaves == ledger.leaves
    assert rebuilt.root() == ledger.root()


def test_a_session_closed_day_ledgers_like_an_explicit_close():
    """Both spellings of a close are day boundaries, and both take a leaf.

    `run_session(close_at_end=True)` and `run_session(); close_market()` roll
    one world, which `test_equivalence.py` holds for the columns. A ledger
    that knew only the second would leave a session-closed run with no leaves
    at all, so the boundary test is the one that matters here: the run
    ledgers, and replaying its log rebuilds the same leaves.

    The two leaves themselves differ, in one field that is not the market.
    `close_market` clears the binding's session flag and the `close_at_end`
    path leaves it set, so a snapshot taken at the two boundaries carries
    `market_open` False and True. Every other field the hash covers -- the
    columns, the generators, the macro chain, the central bank -- is
    identical, and the pinned equality below is what would fail if that
    stopped being true. The flag itself is the binding's to change, and
    changing it would move the trajectory of a run that opens no market
    between two sessions.
    """
    explicit = tf.Engine(seed=SEED, universe=UNIVERSE)
    a = tf.DayLedger()
    for _ in range(3):
        explicit.open_market()
        explicit.run_session(9, 30, 3, TICKS)
        explicit.close_market()
        a.close(explicit)

    session = tf.Engine(seed=SEED, universe=UNIVERSE)
    b = tf.DayLedger()
    for _ in range(3):
        session.open_market()
        session.run_session(9, 30, 3, TICKS, close_at_end=True)
        b.close(session)

    assert len(b.leaves) == 3
    rebuilt = tf.DayLedger()
    tf.replay(session.order_log, seed=SEED, universe=UNIVERSE, ledger=rebuilt)
    assert rebuilt.leaves == b.leaves, (
        "a session-closed run must rebuild its own leaves, or a reader "
        "cannot check the ledger they were sent"
    )

    assert mf.market_digest(explicit) == mf.market_digest(session)
    assert explicit.draws_consumed == session.draws_consumed
    left, right = explicit.state_snapshot(), session.state_snapshot()
    assert left["market_open"] is False and right["market_open"] is True
    aligned = dict(right)
    aligned["market_open"] = False
    assert state_hash(left) == state_hash(aligned), (
        "the two spellings of a close differ in the session flag alone; "
        "something else in the state has moved"
    )


#: Every per-slot array a snapshot carries, and the f64 slots each holds per
#: instrument. The columns are added at runtime, since the snapshot names
#: them itself.
_PER_SLOT = {"attribution": len(tf.Engine.FACTORS), "tick_components": 8, "tick_fundamental": 1,
             "tick_anchor": 1, "volume_idio": 1}


def _slots(buffer, per_instrument):
    return len(buffer) // 8 // per_instrument


def _widths(snapshot):
    """Each per-slot array in a snapshot, by the instruments it holds."""
    out = {name: _slots(snapshot[name], per)
           for name, per in _PER_SLOT.items()}
    for name, buffer in snapshot["columns"].items():
        out[f"columns.{name}"] = _slots(buffer, 1)
    return out


@pytest.mark.parametrize("change", ["none", "list", "delist"])
def test_the_twin_hashes_exactly_the_snapshots_whose_widths_agree(change):
    """What the Python twin can check follows from the widths it is handed.

    The twin decodes each per-slot array against a length it computes from
    the roster, so it can hash a snapshot when every array holds one slot per
    instrument and must refuse one when any array does not. This asserts that
    relationship rather than either outcome, so it states the correct thing
    whatever the engine does with the arrays.

    That matters right now. On a build before tradefloor issue #148,
    `volume_idio` is sized at construction and is the one per-slot array
    `add_company` and `remove_company` do not resize, so the `list` and
    `delist` cases take the refusing branch and `none` takes the hashing
    branch. When #148 lands they all take the hashing branch and this test
    goes on asserting the same thing. A test that pinned the refusal would
    fail on the day the defect was fixed, which is the failure this file
    keeps finding elsewhere.

    No price rides on it either way. Every shipped preset holds
    `volume_idio_sigma` and `volume_idio_persistence` at 0.0, so every value
    in that array is exactly 0.0 and a width that lags the roster shifts
    zeros past zeros.
    """
    engine = tf.Engine(seed=SEED, universe=UNIVERSE)
    engine.open_market()
    engine.run_session(9, 30, 3, TICKS)
    engine.close_market()

    if change == "list":
        engine.list_instrument(tf.Instrument(
            "NEWCO", "technology", initial_price=40.0,
            shares_outstanding=1e8, eps=2.0, book_value_per_share=10.0,
            revenue_growth=0.05, avg_volume=5e5, beta=1.1))
    elif change == "delist":
        engine.delist(0)

    snapshot = engine.state_snapshot()
    roster = len(snapshot["tickers"])
    widths = _widths(snapshot)
    lagging = sorted(name for name, width in widths.items()
                     if width != roster)

    if not lagging:
        assert engine.state_hash() == state_hash(snapshot), (
            "every per-slot array holds one slot per instrument, so the twin "
            "has everything it needs and must agree with the engine"
        )
    else:
        named = lagging[0].split(".")[-1]
        with pytest.raises(tf.ValidationError, match=named):
            state_hash(snapshot)

    # The dials behind the array the widths currently turn on, so the "no
    # price rides on it" claim above is measured rather than remembered.
    model = dict(engine.model_params)
    assert model["volume_idio_sigma"] == 0.0
    assert model["volume_idio_persistence"] == 0.0
    assert set(struct.unpack("<%dd" % widths["volume_idio"],
                             snapshot["volume_idio"])) == {0.0}


def test_a_run_that_lists_and_delists_still_verifies():
    """A roster that changes mid-run reaches its snapshot's shape.

    `restore_state` refuses a snapshot whose tickers are not the engine's,
    because the columns are positional. A verifier that built a fresh engine
    from the manifest's roster and restored straight onto it would refuse
    every day after a listing, and the message would name the roster rather
    than the listing that moved it. The listing and delisting entries are
    replayed first, which costs no ticks and carries the fundamentals no
    column holds.

    This checks the Rust leaf and nothing else, which is the whole of what
    `verify` computes. It cannot also check the Python twin, because on a
    build before tradefloor issue #148 the twin refuses every snapshot taken
    after a roster change:
    `test_the_twin_hashes_exactly_the_snapshots_whose_widths_agree` above
    says why, and derives it rather than pinning it. So a roster-changing run
    is covered here on one side today, and on both once #148 lands.
    """
    engine = tf.Engine(seed=SEED, universe=UNIVERSE)
    ledger = tf.DayLedger()
    for day in range(6):
        if day == 2:
            engine.list_instrument(tf.Instrument(
                "NEWCO", "technology", initial_price=40.0,
                shares_outstanding=1e8, eps=2.0, book_value_per_share=10.0,
                revenue_growth=0.05, avg_volume=5e5, beta=1.1))
        if day == 4:
            engine.delist(0)
        engine.open_market()
        engine.run_session(9, 30, 3, TICKS)
        engine.close_market()
        ledger.close(engine)

    assert engine.tickers[-1] == "NEWCO" and len(engine.tickers) == 8
    manifest = tf.RunManifest.of(engine, seed=SEED, universe=UNIVERSE,
                                 ledger=ledger)
    report = mf.verify(manifest, ledger, 6, seed=3)
    assert report.ok, report.describe()
    assert report.day_runs == 6


def test_a_world_ledgers_every_day_it_runs():
    """The counterfactual loop, including the entries before the open.

    A world with pins writes `pin_macro` before the market opens, so a day's
    replay segment starts at the previous close rather than at `open_market`.
    Verified with every day sampled, which is what would fail if the segment
    were cut at the open: day 1 onward would replay under day 0's macro path.
    """
    class Flat:
        def act(self, observation):
            return {}

    world = tf.World(seed=3, universe=UNIVERSE, agent=Flat(),
                     pins={"vix": 34.0}, steps_per_day=2, ticks_per_step=10)
    ledger = tf.DayLedger()
    world.run(days=3, ledger=ledger)
    world.intervene(federal_funds_rate=0.06)
    world.run(days=3, ledger=ledger)

    assert len(ledger.leaves) == 6
    assert ledger.leaves[-1] == world.engine.state_hash()

    log = world.engine.order_log
    assert [entry["op"] for entry in log[:2]] == ["pin_macro",
                                                  "open_market"], (
        "the world writes its macro path before the open, which is the case "
        "the day segment has to cover"
    )
    manifest = tf.RunManifest.of(world.engine, seed=3, universe=UNIVERSE,
                                 ledger=ledger)
    report = mf.verify(manifest, ledger, 6, seed=1)
    assert report.ok, report.describe()


def test_the_hash_reads_every_field_of_an_overlay_entry():
    """The overlay's content, not only its presence.

    A hash that counted the substitutions and ignored their stream, kind,
    index and value passed everything: two engines whose overlays differ
    only in value, only in address or only in stream came back with the
    same leaf, and the Rust side and the twin silently disagreed on any
    engine carrying one. Dropping every entry from the Rust hash while
    keeping the length prefix left the suite green, which is what this
    fixes.
    """
    from tradefloor import noise

    def with_overlay(*patches):
        engine = tf.Engine(seed=SEED, universe=UNIVERSE)
        engine.run_days(1, record=False, ticks_per_day=TICKS)
        if patches:
            noise.patch_draws(engine, [
                noise.Patch(noise.DrawAddress(s, k, i), v)
                for s, k, i, v in patches])
        return engine

    plain = with_overlay()
    one = with_overlay(("jumps", "uniform", 0, 0.5))
    other_value = with_overlay(("jumps", "uniform", 0, 0.25))
    other_index = with_overlay(("jumps", "uniform", 1, 0.5))
    other_stream = with_overlay(("news", "uniform", 0, 0.5))

    # every field of the entry reaches the leaf
    assert plain.state_hash() != one.state_hash()
    assert one.state_hash() != other_value.state_hash()
    assert one.state_hash() != other_index.state_hash()
    assert one.state_hash() != other_stream.state_hash()

    # and the two implementations agree on an engine that carries one,
    # which is the case the agreement test never reached
    for engine in (plain, one, other_value, other_index, other_stream):
        snapshot = engine.state_snapshot()
        assert engine.state_hash() == state_hash(snapshot)
    assert one.state_snapshot()["draw_overlay"] == [(3, 0, 0, 0.5)]

    # the prices are untouched, so this is the overlay and nothing else
    assert list(one.prices()) == list(plain.prices())
