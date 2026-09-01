"""What a save writes must not depend on which machine ran it.

Every writer here produces a file the library tells a reader to hash, diff
or commit, and Python's default text mode makes all three answer
differently on Windows. These tests pass trivially on Linux, which is the
point: the bug they guard was invisible to the CI that would have caught
any other regression.

The bug that prompted them: a snapshot saved on Windows hashed
`0b9f6bf9...` in its author's working tree and `959783ef...` in a fresh
clone of the same commit, because `.gitattributes` normalised 5,450 CRLF
pairs that the writer had put there and the checkout never restored. The
published digest described one machine. The validator compared the file
against itself and passed.
"""

import hashlib
import json

import pytest

from tradefloor.atlas import Axis, Survey
from tradefloor.edgar import Snapshot
from tradefloor.integrations.common import Transcript as CommonTranscript
from tradefloor.integrations.finrobot import Transcript as FinRobotTranscript

CR = chr(13).encode("ascii")   # bytes: read_text would hide it

ROWS = [
    dict(ticker="ALPHA", sector="technology", eps=6.10,
         book_value_per_share=18.0, revenue_growth=0.19,
         shares_outstanding=1.5e10),
    dict(ticker="BETA", sector="energy", eps=8.40,
         book_value_per_share=52.0, revenue_growth=0.02,
         shares_outstanding=4.2e9),
]


def a_snapshot():
    return Snapshot(as_of="2024-06-30", rows=ROWS,
                    notes={"selection": "exact_ciks"})


def a_transcript(cls):
    t = cls(meta={"framework": "test", "model": "none"})
    t.entries.append(dict(arm="control", step=0, day=0, digest="d0",
                          prompt="line one", response="line two"))
    return t


def a_survey():
    s = Survey(axes=[Axis("alpha", 0.0, 1.0)])
    s.record(0, {"alpha": 0.5}, {"score": 1.25})
    s.record(1, {"alpha": 0.75}, error="diverged")
    return s


# --------------------------------------------------------------------------
# What each save writes
# --------------------------------------------------------------------------

def test_a_saved_snapshot_carries_no_carriage_return(tmp_path):
    """Criterion 1: the same snapshot is the same file on either platform.

    A digest over these bytes is what a reader computes to check they hold
    the file that was published, and `Snapshot.hash` cannot serve that
    purpose because it is over the content rather than over the file.
    """
    path = tmp_path / "snap.json"
    a_snapshot().save(str(path))
    assert CR not in path.read_bytes()


@pytest.mark.parametrize("cls", [CommonTranscript, FinRobotTranscript],
                         ids=["common", "finrobot"])
def test_a_saved_transcript_carries_no_carriage_return(tmp_path, cls):
    """Criterion 2, for both copies of the class.

    Recordings are committed, so a Windows author and a Linux CI would
    otherwise disagree about the contents of a checked-in fixture.
    """
    path = tmp_path / "recording.json"
    a_transcript(cls).save(path)
    assert CR not in path.read_bytes()


def test_a_saved_survey_carries_no_carriage_return(tmp_path):
    """`Survey.save` writes the artifact an atlas measurement is cited from."""
    path = tmp_path / "survey.json"
    a_survey().save(str(path))
    assert CR not in path.read_bytes()


def test_no_writer_in_the_package_uses_text_mode():
    """Criterion 4, for writers that do not exist yet.

    Testing the four current savers one at a time leaves the next one
    unguarded, and the next one is where this bug came from. Scanning the
    source catches an addition anywhere in the package, including in a
    module renamed after this test was written.
    """
    import tradefloor

    root = __import__("pathlib").Path(tradefloor.__file__).parent
    offenders = []
    for module in sorted(root.rglob("*.py")):
        for number, line in enumerate(
                module.read_text(encoding="utf-8").splitlines(), start=1):
            code = line.split("#")[0]
            text_write = ('.write_text(' in code
                          or ('open(' in code and '"w"' in code)
                          or ("open(" in code and "'w'" in code))
            if text_write and "newline=" not in code:
                offenders.append(f"{module.name}:{number}: {line.strip()}")
    assert not offenders, (
        "text-mode writes emit CRLF on Windows, so the bytes on disk "
        "depend on the machine. Write bytes, or pass newline='':"
        + chr(10) + "  " + (chr(10) + "  ").join(offenders))


# --------------------------------------------------------------------------
# What each load accepts
# --------------------------------------------------------------------------

def test_a_snapshot_saved_before_this_change_still_loads(tmp_path):
    """Criterion 3. Every file a Windows user already has carries CRLF.

    A fix that made those unreadable would trade a portability bug for a
    data-loss one, so the reading half is deliberately untouched.
    """
    path = tmp_path / "legacy.json"
    text = json.dumps(a_snapshot().to_dict(), sort_keys=True, indent=2)
    path.write_bytes(text.encode("utf-8").replace(chr(10).encode("ascii"), CR + chr(10).encode("ascii")))
    assert CR in path.read_bytes()
    assert Snapshot.load(str(path)).hash == a_snapshot().hash


@pytest.mark.parametrize("cls", [CommonTranscript, FinRobotTranscript],
                         ids=["common", "finrobot"])
def test_a_transcript_saved_before_this_change_still_loads(tmp_path, cls):
    """Criterion 3 for recordings, which is where the real exposure is.

    A recording costs API calls to make. Refusing to read one because of
    its line endings would destroy something that cannot be regenerated
    for free.
    """
    path = tmp_path / "legacy.json"
    original = a_transcript(cls)
    path.write_bytes(
        original.to_json().encode("utf-8").replace(chr(10).encode("ascii"), CR + chr(10).encode("ascii")))
    assert CR in path.read_bytes()
    assert cls.load(path).entries == original.entries


# --------------------------------------------------------------------------
# What the change does not touch
# --------------------------------------------------------------------------

def test_the_content_hash_is_unchanged(tmp_path):
    """Criterion 5. `Snapshot.hash` was already portable and stays put.

    It is computed over canonical JSON rather than over the file, which is
    right, and is the reason the file digest and the content digest are
    two different answers. This pins the value so a future change to the
    writer cannot quietly move it.
    """
    snapshot = a_snapshot()
    assert snapshot.save(str(tmp_path / "s.json")) == snapshot.hash
    canonical = snapshot.to_json().encode("utf-8")
    assert snapshot.hash == hashlib.sha256(canonical).hexdigest()


def test_two_saves_of_the_same_snapshot_agree_byte_for_byte(tmp_path):
    """The property the digest depends on, stated directly."""
    one, two = tmp_path / "one.json", tmp_path / "two.json"
    a_snapshot().save(str(one))
    a_snapshot().save(str(two))
    assert one.read_bytes() == two.read_bytes()
