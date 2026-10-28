"""The measured record for a shipped preset.

A record is what a preset was MEASURED to be: its panel at both horizons, its
band membership on four axes, its crisis lever, and the build and method that
produced them. `tools/presets/record.py` writes them and they ship in the
wheel, so a reader can cite a preset's figures without a clone and without
re-running a 96-core measurement.

    >>> import tradefloor as tf
    >>> rec = tf.preset_record()            # pt-v21, the shipped default
    >>> rec["in_band"]["252"]
    15

The point is that a figure and the preset it describes travel together. The
alternative, which this replaces, was a published number in one file and the
preset it was measured under in another, agreeing only as long as somebody
remembered to re-type both at an era boundary.

`available()` lists what has a record. Not every shipped preset does: a
record costs a measurement, so they exist for the presets the project
publishes figures about, and `available()` is the honest answer to which.

A record can be committed before its grade has run, with each figure the
grade measures left as a SLOT: `{"slot": NAME, "filled_by": HOW}` in place of
the block. Such a record is not a measurement yet. `preset_record` refuses it
and names the empty slots, `available()` leaves it out and `pending()` lists
it; `tools/presets/record.py`'s writers fill the slots.

A record's `long_run` rows each carry `pass`, whether the row sits inside its
mark. A row the registration reads and reports without gating also carries
`gated: false` and `reported_because`; a gated row with an accepted exception
carries `exception`. The block's `passed` and `of` count gated rows, and
`all_rows` counts every row. A record written that way, or one whose verdict
says "pass", is refused when a gated row fails.
"""

from __future__ import annotations

import json
from typing import Any


def _dir():
    """The directory the records live in, installed or in a clone.

    `importlib.resources` rather than `__file__`, for the reason
    `scenario._pack` gives: the two differ exactly where it matters.
    """
    from importlib import resources

    return resources.files(__package__) / "presets"


def is_slot(value: Any) -> bool:
    """Whether a record's value is a slot the grade has not filled yet."""
    return isinstance(value, dict) and set(value) == {"slot", "filled_by"}


def open_slots(record: dict[str, Any]) -> list[str]:
    """The slots a record still holds, by name, in the record's order."""
    return [v["slot"] for v in record.values() if is_slot(v)]


def gated_failures(record: dict[str, Any]) -> list[str]:
    """The `long_run` rows a record GATES that sit outside their mark.

    A row is gated unless it carries `gated: false` (a row the registration
    reads and reports without gating; `tools/presets/record.py`'s
    `REPORTED_ROWS`).
    """
    block = record.get("long_run")
    if not isinstance(block, dict) or is_slot(block):
        return []
    return [str(r.get("id")) for r in block.get("rows") or []
            if r.get("gated", True) and r.get("pass") is not True]


def refused_rows(record: dict[str, Any]) -> list[str]:
    """The gated failing rows that stop `preset_record` loading a record.

    Two cases. A block that says "pass" while a gated row fails contradicts
    itself. A block written with the gating marks (`all_rows`, which
    `record.py` writes for every record since pt-v22's) is a release's grade,
    and a release cannot ship a gated row outside its mark. An older block
    without the marks loads as it always did: pt-v19's record, for one,
    carries the grade it failed as pt-v20's control.
    """
    block = record.get("long_run")
    failing = gated_failures(record)
    if failing and isinstance(block, dict) and (
            block.get("verdict") == "pass" or "all_rows" in block):
        return failing
    return []


def _records() -> dict[str, dict[str, Any]]:
    try:
        paths = sorted(p for p in _dir().iterdir() if p.name.endswith(".json"))
    except (FileNotFoundError, NotADirectoryError):
        return {}
    return {p.name[:-5]: json.loads(p.read_text(encoding="utf-8")) for p in paths}


def available() -> list[str]:
    """The presets that carry a measured record, sorted."""
    return sorted(n for n, rec in _records().items() if not open_slots(rec))


def pending() -> list[str]:
    """The presets whose record is committed with slots still empty, sorted."""
    return sorted(n for n, rec in _records().items() if open_slots(rec))


def preset_record(name: str | None = None) -> dict[str, Any]:
    """One preset's record. `None` means the shipped default.

    Raises `LookupError` naming what does exist, rather than returning an
    empty dict: a caller that got `{}` would publish nothing and say nothing,
    which is the failure mode this whole file exists to remove.
    """
    if name is None:
        from . import _core

        name = _core.model_preset()["name"]
    path = _dir() / f"{name}.json"
    if not path.is_file():
        raise LookupError(
            f"no measured record for {name!r}. Records exist for "
            f"{', '.join(available()) or 'no preset'}; write one with "
            "tools/presets/record.py after measuring the panel."
        )
    record = json.loads(path.read_text(encoding="utf-8"))
    empty = open_slots(record)
    if empty:
        raise LookupError(
            f"{name!r}'s record is committed but not measured: its grade has "
            f"not filled {len(empty)} slot(s) ({', '.join(empty)}). "
            "tools/presets/record.py's writers fill them."
        )
    failing = refused_rows(record)
    if failing:
        raise LookupError(
            f"{name!r}'s record gates row(s) outside their mark "
            f"({', '.join(failing)}): it describes a grade the preset did not pass."
        )
    return record
