"""Where the claim register lives.

The register records, per published figure, the value a page states and where
it states it. That makes it a description of the documentation, not of this
package, and the documentation left this repository for `tradefloor-docs` at
0.5.0. What was left behind is a register whose every coordinate points at a
`docs/*.md` tree that no longer exists here: 257 of its 260 rows name a file
nothing holds, and the gate that reads it could not say so.

So the register follows the pages it describes. This resolves it from, in
order:

    --inventory PATH        an explicit path, which wins over everything
    TRADEFLOOR_DOCS         a checkout of the documentation repository
    this repository         the copy still committed here

The last is a transition fallback, not the destination. It is kept so that a
clone of this repository alone still runs the gate rather than failing to
start, and so the move can land in two repositories in either order.

Every caller prints which of the three it used. A gate that silently reads a
different register than its operator believes is the same defect as one that
reads no register at all, and this tool has already shipped that once: for
three releases `resync.py` defaulted to a figures file from a run with zero
MOVED rows and reported "0 / 0 / 0" however stale the register had become.
"""

from __future__ import annotations

import os
import pathlib

HERE = pathlib.Path(__file__).resolve().parent

#: Where the register sits inside a documentation checkout. Named once here
#: rather than spelled out at each call site, so moving it within that
#: repository is one edit.
IN_DOCS = pathlib.Path("tools") / "remeasure" / "inventory.json"


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
            "--inventory, or unset it to use the copy in this repository."
        )

    local = HERE / "inventory.json"
    if local.is_file():
        return local, "the copy in this repository"

    raise SystemExit(
        "no claim register found. Set TRADEFLOOR_DOCS to a documentation "
        f"checkout holding {IN_DOCS.as_posix()}, or pass --inventory."
    )


#: The code repository, which is two levels above this file.
CODE_ROOT = HERE.parent.parent


def page_roots(docs_root: str | None = None) -> list[pathlib.Path]:
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
