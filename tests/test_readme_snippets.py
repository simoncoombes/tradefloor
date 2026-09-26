"""What README.md tells a reader to type has to work when they type it.

The README is the PyPI page, and most readers meet it after `pip install`,
with no clone. At 0.8.5 its scenario example was
`tradefloor scenario show scenarios/oil_price_spike.yml`, a path that exists
nowhere a reader stands: not at the repository root, and not in an install,
where the files are inside the package. Run as written it printed
"Scenario invalid" and a missing-file error. The Bonds paragraph and the
comment in `pyproject.toml` named the same kind of bare path.

So the commands are run, and the output printed under each is checked
against what the command prints now.
"""

from __future__ import annotations

import pathlib
import re
import shlex

import tradefloor as tf
from tradefloor.__main__ import main

REPO = pathlib.Path(__file__).resolve().parent.parent
README = REPO / "README.md"


def _blocks(text: str) -> list[list[str]]:
    """Every fenced block, line by line, without its fences."""
    blocks, body = [], None
    for line in text.splitlines():
        if line.startswith("```"):
            if body is None:
                body = []
            else:
                blocks.append(body)
                body = None
        elif body is not None:
            body.append(line)
    return blocks


def _squash(line: str) -> str:
    return " ".join(line.split())


def test_every_scenario_command_in_the_readme_runs(capsys):
    """Each `tradefloor scenario ...` line runs, and the lines the README
    prints under it are lines it prints."""
    found = 0
    for block in _blocks(README.read_text(encoding="utf-8")):
        for index, line in enumerate(block):
            if not line.startswith("tradefloor scenario "):
                continue
            found += 1
            code = main(shlex.split(line)[1:])
            out = capsys.readouterr().out
            assert code == 0, f"`{line}` exited {code}:\n{out}"
            printed = {_squash(row) for row in out.splitlines()}
            shown = [_squash(row) for row in block[index + 1:]
                     if row.strip() and not row.startswith("tradefloor ")]
            stale = [row for row in shown if row not in printed]
            assert not stale, (
                f"README.md shows these lines under `{line}` and the "
                f"command does not print them: {stale}")
    assert found, "the README no longer shows a scenario command"


def test_every_scenario_the_readme_names_ships():
    """A scenario named by `Scenario.load("...")` is one the package carries,
    and a scenario named as a file names one that exists from the root of
    a checkout."""
    text = README.read_text(encoding="utf-8")
    for name in re.findall(r"""Scenario\.load\(["']([A-Za-z0-9_]+)["']\)""",
                           text):
        assert name in tf.Scenario.available(), name
    for path in re.findall(r"[\w./-]*scenarios/[\w*]+\.yml", text):
        assert list(REPO.glob(path)), (
            f"README.md names {path}, which does not exist from the "
            f"repository root. Name a shipped scenario instead, as "
            f"`Scenario.load(\"...\")` or `tradefloor scenario show ...`.")


#: The pages whose inline code a reader copies into Python.
PAGES = ("README.md", "CHANGELOG.md", "docs/MODEL.md")

#: An inline code span shaped like a call: `f(...)`, `tf.a.b(...)`.
CALL_SPAN = re.compile(r"`((?:tf\.)?[A-Za-z_][\w.]*\([^`\n]*\))`")

#: `observe(detail=)` names a parameter rather than making a call, and is
#: left alone.
PARAMETER = re.compile(r"\w+=\)$")


def _resolve(dotted: str):
    obj = tf
    for part in dotted.removeprefix("tf.").split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


def test_every_call_written_as_code_is_one_python_accepts():
    """A call in backticks parses, and fits the signature of the function it
    names, as far as the arguments it writes out.

    At 0.8.5 the CHANGELOG wrote three seeds as `Universe.random(20, seed=3,
    42, 11)`, which is a SyntaxError, and docs/MODEL.md wrote
    `Universe.random(n, seed)`, which a keyword-only seed refuses. A call
    that leaves arguments out (`compare()`) is how prose names a function,
    so only what is written is checked: too many positional arguments, or a
    keyword the function does not take.
    """
    import ast
    import inspect

    wrong = []
    for page in PAGES:
        text = (REPO / page).read_text(encoding="utf-8")
        for span in CALL_SPAN.findall(text):
            if PARAMETER.search(span):
                continue
            try:
                call = ast.parse(span, mode="eval").body
            except SyntaxError as err:
                wrong.append(f"{page}: `{span}` is not Python ({err.msg})")
                continue
            if not isinstance(call, ast.Call):
                continue
            target = _resolve(ast.unparse(call.func))
            if target is None:
                continue
            try:
                signature = inspect.signature(target)
            except (TypeError, ValueError):
                continue
            if (any(isinstance(a, ast.Starred) for a in call.args)
                    or any(k.arg is None for k in call.keywords)):
                continue
            try:
                signature.bind_partial(
                    *[None] * len(call.args),
                    **{k.arg: None for k in call.keywords})
            except TypeError as err:
                wrong.append(f"{page}: `{span}` does not fit "
                             f"{ast.unparse(call.func)}{signature}: {err}")
    assert not wrong, "\n".join(wrong)


def test_the_records_doctest_names_the_preset_it_reads():
    """records.py's doctest reads the shipped default's record, and its
    comment said pt-v19 after the default moved to pt-v20."""
    import tradefloor.records as records
    named = re.findall(r"#\s*(pt-v\d+), the shipped default", records.__doc__)
    assert named == [tf.preset_record()["preset"]], (
        f"records.py's doctest comment names {named}; "
        f"tf.preset_record() reads {tf.preset_record()['preset']}")
