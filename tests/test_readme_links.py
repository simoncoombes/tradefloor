"""The README ships to PyPI, where a relative link is a dead link.

`readme = "README.md"` in pyproject means this file becomes the project page
body on PyPI. That page lives at pypi.org, so a relative target like
`examples/01-first-simulation.ipynb` resolves to
pypi.org/project/tradefloor/examples/01-first-simulation.ipynb and 404s. On
GitHub the identical markup works, which is exactly why the defect survived
a release: the file renders correctly everywhere the author looks at it.

So the rule is checked rather than remembered. Every link in the README must
be absolute or an in-page anchor.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
README = REPO / "README.md"

#: `[text](target)`, ignoring image embeds, which are handled below.
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)")


def _targets(pattern: re.Pattern[str]) -> list[str]:
    return pattern.findall(README.read_text(encoding="utf-8"))


def test_every_readme_link_survives_pypi() -> None:
    relative = [
        t
        for t in _targets(LINK)
        if not t.startswith(("http://", "https://", "#", "mailto:"))
    ]
    assert not relative, (
        "README.md carries relative links, which are dead on the PyPI project "
        f"page: {relative}. Use the full "
        "https://github.com/simoncoombes/tradefloor/blob/main/... form."
    )


def test_every_readme_image_survives_pypi() -> None:
    relative = [
        t for t in _targets(IMAGE) if not t.startswith(("http://", "https://"))
    ]
    assert not relative, (
        f"README.md embeds images by relative path: {relative}. PyPI cannot "
        "resolve them, so they render as broken images on the project page."
    )


@pytest.mark.parametrize(
    "target",
    sorted(
        {
            t
            for t in _targets(LINK)
            if t.startswith("https://github.com/simoncoombes/tradefloor/")
        }
    ),
)
def test_linked_repository_paths_exist(target: str) -> None:
    """An absolute link into this repository must still name a real file.

    Absolute links cannot be caught by a broken-link checker offline, and a
    renamed example would leave the PyPI page pointing at a GitHub 404. The
    path after blob/main or tree/main is checkable right here without a
    network call.
    """
    m = re.search(r"/(?:blob|tree)/main/(.+)$", target)
    if not m:
        pytest.skip(f"not a repository file link: {target}")
    assert (REPO / m.group(1)).exists(), (
        f"README links to {m.group(1)}, which does not exist in the "
        "repository. The PyPI page would point at a GitHub 404."
    )
