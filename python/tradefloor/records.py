"""The measured record for a shipped preset.

A record is what a preset was MEASURED to be: its panel at both horizons, its
band membership on four axes, its crisis lever, and the build and method that
produced them. `tools/presets/record.py` writes them and they ship in the
wheel, so a reader can cite a preset's figures without a clone and without
re-running a 96-core measurement.

    >>> import tradefloor as tf
    >>> rec = tf.preset_record()            # the shipped default
    >>> rec["in_band"]["252"]
    14

The point is that a figure and the preset it describes travel together. The
alternative, which this replaces, was a published number in one file and the
preset it was measured under in another, agreeing only as long as somebody
remembered to re-type both at an era boundary.

`available()` lists what has a record. Not every shipped preset does: a
record costs a measurement, so they exist for the presets the project
publishes figures about, and `available()` is the honest answer to which.
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


def available() -> list[str]:
    """The presets that carry a measured record, sorted."""
    try:
        return sorted(p.name[:-5] for p in _dir().iterdir()
                      if p.name.endswith(".json"))
    except (FileNotFoundError, NotADirectoryError):
        return []


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
    return json.loads(path.read_text(encoding="utf-8"))
