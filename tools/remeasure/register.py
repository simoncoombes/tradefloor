"""Where the claim register lives, and how a row's page and value are found.

The register records, per published figure, the value a page states and where
it states it. That makes it a description of the documentation, not of this
package, and the documentation left this repository for `tradefloor-docs` at
0.5.0. The register now lives there, at `tools/remeasure/inventory.json`,
beside the pages it describes. This repository keeps no copy.

It kept one until 0.8.1, as a transition fallback. That copy stayed behind
when the pages moved, so for four releases every coordinate in it named a
`docs/*.md` page nothing held, and a clone of this repository alone ran the
gate against it without a word. A register stays right only where the pages
are edited.

So the register is resolved from, in order:

    --inventory PATH        an explicit path, which wins over everything
    TRADEFLOOR_DOCS         a checkout of the documentation repository

and from nothing else. With neither, the tools stop and say where the
register is. Every caller prints which of the two it used. A gate that
silently reads a different register than its operator believes is the same
defect as one that reads no register at all.
"""

from __future__ import annotations

import json
import os
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent

#: Where the register sits inside a documentation checkout. Named once here
#: rather than spelled out at each call site, so moving it within that
#: repository is one edit.
IN_DOCS = pathlib.Path("tools") / "remeasure" / "inventory.json"

#: The pointer every refusal carries. Anyone who arrives here from a clone of
#: this repository needs to know where the register went, not only that it
#: is missing.
WHERE = (
    "The claim register lives in the documentation repository "
    "(simoncoombes/tradefloor-docs) at tools/remeasure/inventory.json. "
    "Set TRADEFLOOR_DOCS to a checkout of it, or pass --inventory PATH."
)


def resolve(explicit: str | None = None) -> tuple[pathlib.Path, str]:
    """The register's path, and a phrase naming how it was found.

    Raises SystemExit rather than returning a path that is not there. A
    missing register is not an empty one, and the difference decides whether
    "nothing moved" means the figures hold or that nothing was checked.
    """
    if explicit:
        path = pathlib.Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"--inventory {explicit}: no such file")
        return path, "--inventory"

    docs = os.environ.get("TRADEFLOOR_DOCS")
    if docs:
        path = pathlib.Path(docs).expanduser().resolve() / IN_DOCS
        if path.is_file():
            return path, "TRADEFLOOR_DOCS"
        raise SystemExit(
            f"TRADEFLOOR_DOCS={docs} holds no {IN_DOCS.as_posix()}. "
            "Point it at a checkout of the documentation repository, or pass "
            "--inventory."
        )

    raise SystemExit(f"no claim register given. {WHERE}")


def docs_root_of(register_path: pathlib.Path) -> pathlib.Path | None:
    """The documentation checkout a register sits in, when it sits in one.

    A register at `<root>/tools/remeasure/inventory.json` belongs to `<root>`,
    which is where its bound figures' data files and its pages are. One
    passed with --inventory from anywhere else has no such root.
    """
    path = pathlib.Path(register_path).resolve()
    parts = IN_DOCS.parts
    if tuple(path.parts[-len(parts):]) != parts:
        return None
    return path.parents[len(parts) - 1]


#: The code repository, which is two levels above this file.
CODE_ROOT = HERE.parent.parent


def page_roots(docs_root: str | pathlib.Path | None = None) -> list[pathlib.Path]:
    """Where a row's `file` may be found, in the order it must be tried.

    The code repository first, deliberately. A handful of rows cite a bare
    `README.md` and mean the code repository's, not the documentation site's:
    `readme.residual` is a claim about the factor decomposition that appears
    in the code README and nowhere on the site. Both repositories have a
    README.md, so the order decides which file those rows are checked
    against, and the wrong order reports them as figures that moved when all
    that happened is the wrong file was opened.
    """
    roots = [CODE_ROOT]
    docs = docs_root or os.environ.get("TRADEFLOOR_DOCS")
    if docs:
        roots.append(pathlib.Path(docs).expanduser().resolve())
    return roots


# ---------------------------------------------------------------------------
# bound figures
# ---------------------------------------------------------------------------
#
# Some figures are not typed on a page. The build writes them from a data file
# it generates each release (the twelve-market grid and the rebalance table
# from experiments.json, the default preset and its crisis lever from
# preset-records.json), so the page moves when the file is regenerated. A row
# describing one carries
#
#     "bound": {"file": "tools/docs/learn/experiments.json",
#               "path": "grid.agents_table[name=momentum].pooled_capture"}
#
# and its published value is read from that file when the gate runs, not from
# the copy typed into the row. The typed copy is what the page said when the
# row was last re-pointed, kept so the register reads on its own; the file is
# what the page says now. `path` may be a list, whose values are joined with
# "-" (a separation's "12-0" is two fields).

_SEGMENT = re.compile(r"^([^\[\]]+)((?:\[[^\]]+\])*)$")


def _walk(data, path: str):
    node = data
    for segment in path.split("."):
        m = _SEGMENT.match(segment)
        if not m:
            raise KeyError(f"cannot read path segment {segment!r}")
        node = node[m.group(1)]
        for selector in re.findall(r"\[([^\]]+)\]", m.group(2)):
            if "=" in selector:
                key, want = selector.split("=", 1)
                matches = [item for item in node
                           if isinstance(item, dict) and str(item.get(key)) == want]
                if len(matches) != 1:
                    raise KeyError(f"[{selector}] matches {len(matches)} items")
                node = matches[0]
            else:
                node = node[int(selector)]
    return node


def bound_value(row: dict, docs_root: pathlib.Path | None):
    """The value the page states for a bound row, read from its data file.

    Raises LookupError naming the row when the file or the path is not
    there: a bound figure whose source cannot be read has no published value
    at all, and judging the typed copy instead would grade a page nobody
    built.
    """
    bound = row["bound"]
    if docs_root is None:
        raise LookupError(
            f"{row['id']} is bound to {bound['file']}, and the register is "
            "not inside a documentation checkout to read it from")
    source = docs_root / bound["file"]
    if not source.is_file():
        raise LookupError(f"{row['id']}: no {bound['file']} under {docs_root}")
    data = json.loads(source.read_text(encoding="utf-8"))
    paths = bound["path"]
    try:
        if isinstance(paths, list):
            return "-".join(str(_walk(data, p)) for p in paths)
        return _walk(data, paths)
    except (KeyError, IndexError, TypeError) as err:
        raise LookupError(f"{row['id']}: {bound['file']} has no {paths}: {err}")
