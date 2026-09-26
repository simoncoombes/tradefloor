"""The README ships to PyPI, where a relative link is a dead link.

`readme = "README.md"` in pyproject means this file becomes the project page
body on PyPI. That page lives at pypi.org, so a relative target like
`examples/01-first-simulation.ipynb` resolves to
pypi.org/project/tradefloor/examples/01-first-simulation.ipynb and 404s. On
GitHub the identical markup works, so the defect survived
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


# --------------------------------------------------------------------------
# Commands that need a clone
# --------------------------------------------------------------------------

CLONE = "git clone https://github.com/simoncoombes/tradefloor"


def _sections() -> list[tuple[str, list[str]]]:
    """Each `## ` section's heading and the lines inside its code fences."""
    out: list[tuple[str, list[str]]] = []
    fenced = False
    for line in README.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            out.append((line[3:].strip(), []))
            fenced = False
        elif line.startswith("```"):
            fenced = not fenced
        elif fenced and out:
            out[-1][1].append(line.strip())
    return out


def test_every_example_the_readme_runs_follows_a_clone() -> None:
    """`examples/` is in neither the wheel nor the sdist.

    So on the PyPI page, where this file is the project description,
    `python examples/rate-shock/counterfactual.py` after `pip install
    tradefloor` fails with "No such file or directory". 0.8.5 shipped that
    way, and the FinRobot section had the same commands. Checked per
    section, because a reader who jumps to one does not see the clone
    another section asked for.
    """
    missing = []
    for heading, lines in _sections():
        runs = [i for i, line in enumerate(lines)
                if line.startswith("python examples/")]
        if not runs:
            continue
        clones = [i for i, line in enumerate(lines) if line.startswith(CLONE)]
        if not clones or clones[0] > runs[0]:
            missing.append(f"{heading}: {lines[runs[0]]}")
    assert not missing, (
        "these README sections run a file from examples/ without cloning "
        f"the repository first, and pip does not install examples/. Add "
        f"`{CLONE}` and `cd tradefloor` before the command:\n  "
        + "\n  ".join(missing))


def test_the_finrobot_extra_is_introduced_with_the_python_it_needs() -> None:
    """FinRobot allows Python 3.10 and 3.11 and tradefloor needs 3.11, so
    the extra installs only on 3.11. On 3.12 pip fails with "Could not find
    a version that satisfies the requirement finrobot>=0.1.5".

    The replay needs no extra. So the section shows the replay first, and
    the install line comes after a sentence that names 3.11, where a
    reader on 3.12 is told before they run it.
    """
    lines = README.read_text(encoding="utf-8").splitlines()
    start = lines.index("## FinRobot integration")
    end = next(i for i in range(start + 1, len(lines))
               if lines[i].startswith("## "))
    section = lines[start:end]
    install = next(i for i, line in enumerate(section)
                   if line.startswith('pip install "tradefloor[finrobot]"'))
    replay = next(i for i, line in enumerate(section)
                  if line.startswith("python examples/integrations/finrobot/")
                  and "--live" not in line)
    assert replay < install, (
        "the FinRobot section asks for the finrobot extra before it shows "
        "the replay, which needs no extra and runs on every Python")
    assert "Python 3.11" in "\n".join(section[:install]), (
        "the FinRobot section gives the finrobot extra's install command "
        "without saying first that it installs only on Python 3.11")


def test_the_finrobot_extra_carries_no_python_marker() -> None:
    """Deliberately unconditional. With `; python_version < "3.12"` on its
    requirements, `pip install "tradefloor[finrobot]"` on 3.12 would
    succeed and install nothing, and `--live` would fail later with less to
    go on than the resolver's error gives now."""
    import tomllib

    extras = tomllib.loads(
        (REPO / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"]["optional-dependencies"]
    marked = [r for r in extras["finrobot"] if ";" in r]
    assert not marked, marked
