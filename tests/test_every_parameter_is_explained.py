"""Every settable parameter must carry a docstring a reader can find.

This project's standing rule is that everything is explainable: every number
traceable to a measurement, every mechanism's rationale written where a
reader will find it. A settable parameter with no `///` above its field is
the exact opposite. It appears in `ModelParams.settable()`, in the API docs
as a bare name, and in a survey's axis list, with nothing anywhere saying
what it does.

Ten parameters were in that state when this test was written, including
`garch_alpha`, `garch_beta`, `market_vol_alpha` and `market_vol_beta` --
the two variance processes the whole model is built on. They were grouped
under `// ── Per-name GJR-GARCH ──` section comments, which rustdoc does not
attach to anything and a reader of the generated docs never sees.
"""

from __future__ import annotations

import re
from pathlib import Path

import tradefloor

PARAMS_RS = Path(__file__).resolve().parent.parent / "rust" / "src" / "params.rs"


def test_every_settable_parameter_has_a_doc_comment() -> None:
    lines = PARAMS_RS.read_text(encoding="utf-8").split("\n")
    at: dict[str, int] = {}
    for i, line in enumerate(lines):
        m = re.match(r"\s*pub (\w+): f64,\s*$", line)
        if m:
            at.setdefault(m.group(1), i)

    missing = []
    for name in tradefloor.ModelParams.settable():
        i = at.get(name)
        if i is None:
            missing.append(f"{name}: no `pub {name}: f64,` field in params.rs")
            continue
        if not lines[i - 1].strip().startswith("///"):
            missing.append(f"{name}: no /// doc comment above its field")

    assert not missing, (
        "settable parameters with nothing explaining them:\n  "
        + "\n  ".join(missing)
        + "\n\nA `// ── section ──` comment does not count: rustdoc does not "
          "attach it to the field and a reader of the API docs never sees it."
    )
