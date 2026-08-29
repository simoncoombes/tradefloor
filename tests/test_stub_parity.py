"""The type stub, checked against the extension it describes.

`_core.pyi` is hand-written. Nothing makes it true. A checker reading a stub
that has drifted validates user code against a fiction and reports success —
which is worse than having no stub, because the failure is confident.

That is not hypothetical. In a sibling codebase a hand-declared interface
named a static factory the compiled module did not export; the test double was
written from the same declaration, so the two agreed with each other all the
way to a browser, where the real module had no such symbol. The declaration
and its double had never met the thing they described.

PyO3 gives us a way out that a `.d.ts` does not: `__text_signature__` carries
the real parameter names and defaults. So these tests compare the stub against
the RUNTIME, and the parameter-name check is the valuable one — every argument
here is a float, so a transposed pair is invisible to a checker and produces a
market seeded with volatility in the interest-rate slot.
"""

import ast
from pathlib import Path

import pytest

import tradefloor._core as core

STUB = Path(__file__).resolve().parent.parent / "python" / "tradefloor" / "_core.pyi"

# NOT a skipif. The path said "pretium" for a week after the package became
# `tradefloor`, so `STUB.exists()` was False, and all ninety-nine tests in
# this file skipped and reported green while the stub they exist to check
# went unread. A guard that disables itself when its subject moves is worse
# than no guard, because the suite keeps saying the thing is checked.
#
# A missing stub is also not a reason to pass: it means the package ships no
# types, which is the failure this file is about.
if not STUB.exists():  # pragma: no cover
    raise AssertionError(
        f"the type stub is not at {STUB}. Either it is missing, in which "
        "case the package ships no types and a checker silently falls back "
        "to Any, or the package directory moved and this path did not "
        "follow it."
    )


def parsed():
    return ast.parse(STUB.read_text(encoding="utf-8"))


def stub_module_symbols():
    """Top-level names the stub declares, excluding type aliases.

    A `Side = Literal[...]` is a name for a checker and nothing at runtime, so
    it must not be demanded of the module. Distinguished by shape — an
    assignment rather than a def or class — rather than by a hand-kept list
    that would itself drift.
    """
    functions, classes, aliases, variables = set(), set(), set(), set()
    for node in parsed().body:
        if isinstance(node, ast.FunctionDef):
            functions.add(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.add(node.name)
        elif isinstance(node, ast.Assign):
            # `Side = Literal[...]` -- a name for a checker, nothing at runtime.
            for target in node.targets:
                if isinstance(target, ast.Name):
                    aliases.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            # `__version__: str` -- a declared module VARIABLE, which must
            # exist. Distinguished from an alias by shape rather than by name,
            # so the rule does not need a list of exceptions to maintain.
            variables.add(node.target.id)
    return functions, classes, aliases, variables


def stub_class_members(name: str):
    for node in parsed().body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            methods, attributes = set(), set()
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    methods.add(item.name)
                elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    attributes.add(item.target.id)
            return methods, attributes
    raise AssertionError(f"{name} is not declared in the stub")


def stub_params(class_name: str | None, func_name: str) -> list[str]:
    """Parameter names the stub declares, in order, excluding self."""
    tree = parsed()
    body = tree.body
    if class_name is not None:
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                body = node.body
                break
        else:
            raise AssertionError(f"{class_name} is not declared in the stub")
    for node in body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            args = node.args
            names = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
            # `**kwargs` is a parameter too — `ModelParams.from_preset` takes
            # its overrides that way, and the runtime side reports the name.
            if args.kwarg is not None:
                names.append(args.kwarg.arg)
            return [n for n in names if n != "self"]
    raise AssertionError(f"{func_name} is not declared in the stub")


def runtime_params(signature: str) -> list[str]:
    """Parameter names out of a `__text_signature__`, excluding self.

    Parsed directly rather than through `inspect.signature`. The first version
    of this built a throwaway class carrying the signature and asked inspect
    for it; that returned an EMPTY list for everything, and an empty list
    compared against a stub's parameters fails loudly only because the
    assertion happens to run that way round. Had I written the comparison as
    "every runtime name appears in the stub", it would have passed vacuously
    for every callable in the library.
    """
    inner = signature[signature.index("(") + 1: signature.rindex(")")]
    names: list[str] = []
    depth = 0
    current = ""
    # Split on top-level commas only: a default like `cycle="a,b"` or a nested
    # tuple would otherwise be split through the middle.
    for char in inner:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "," and depth == 0:
            names.append(current)
            current = ""
        else:
            current += char
    names.append(current)

    out: list[str] = []
    for part in names:
        part = part.strip()
        if not part or part in {"*", "/"}:
            continue
        name = part.split("=")[0].split(":")[0].strip().lstrip("*")
        if name and name not in {"self", "$self"}:
            out.append(name)
    return out


# --------------------------------------------------------------------------
# The surface
# --------------------------------------------------------------------------


def test_every_stub_symbol_exists_at_runtime():
    functions, classes, _, variables = stub_module_symbols()
    missing = sorted(
        n for n in functions | classes | variables if not hasattr(core, n))
    assert missing == [], missing


def test_every_runtime_symbol_is_declared_in_the_stub():
    # The other direction, and the one that keeps the stub USEFUL rather than
    # merely true. An undeclared export is invisible to a checker, so calling
    # it is an error in correct code -- which is how `version()` and
    # `ArrowStream` were found missing from the package exports earlier.
    functions, classes, aliases, variables = stub_module_symbols()
    declared = functions | classes | aliases | variables
    runtime = {n for n in dir(core) if not n.startswith("_")}
    assert sorted(runtime - declared) == []


def test_type_aliases_do_not_shadow_real_exports():
    _, _, aliases, _ = stub_module_symbols()
    # A type alias is checker-only by design, so it is exempt from existing at
    # runtime -- but it must not share a name with a real export, which would
    # shadow the export's true type with a Literal.
    assert sorted(a for a in aliases if hasattr(core, a)) == []


def test_declared_module_variables_exist():
    _, _, _, variables = stub_module_symbols()
    assert variables, "the stub declares no module variables; check the parser"
    assert sorted(v for v in variables if not hasattr(core, v)) == []


#: Every class the extension exports. Derived from the runtime rather than
#: listed, because a hand-kept list of things-to-check is the same kind of
#: artifact as the stub itself: it drifts, and what falls off it stops being
#: checked without anything failing.
RUNTIME_CLASSES = sorted(
    name for name in dir(core)
    if not name.startswith("_") and isinstance(getattr(core, name), type)
)


@pytest.mark.parametrize("name", RUNTIME_CLASSES)
def test_class_members_exist_at_runtime(name):
    methods, attributes = stub_class_members(name)
    cls = getattr(core, name)
    missing = sorted(
        m for m in methods | attributes
        if not hasattr(cls, m) and not m.startswith("__")
    )
    assert missing == [], (name, missing)


# --------------------------------------------------------------------------
# Parameter names -- the check that catches a transposition
# --------------------------------------------------------------------------


def runtime_callables():
    """Every callable the extension exposes that carries a signature.

    Enumerated from the module, not curated. The previous version listed
    seventeen by hand and there are seventy-two; the fifty-five it missed were
    unchecked, and nothing said so. A list of what to verify has exactly the
    drift problem of the thing being verified.
    """
    out: list[tuple[str | None, str]] = []
    for name in sorted(n for n in dir(core) if not n.startswith("_")):
        obj = getattr(core, name)
        if isinstance(obj, type):
            # `vars(obj)`, not `dir(obj)`. dir() walks the bases, so the
            # exception classes contributed BaseException's own add_note and
            # with_traceback -- real callables with real signatures that the
            # stub has no business redeclaring. Only what the class itself
            # defines is part of this extension's surface.
            for member in sorted(m for m in vars(obj) if not m.startswith("_")):
                attribute = getattr(obj, member, None)
                if callable(attribute) and getattr(
                    attribute, "__text_signature__", None
                ):
                    out.append((name, member))
        elif callable(obj) and getattr(obj, "__text_signature__", None):
            out.append((None, name))
    return out


CALLABLES = runtime_callables()


def test_the_callable_sweep_is_not_empty():
    # If enumeration ever returns nothing -- a PyO3 change stops emitting
    # __text_signature__, say -- every parametrised test below silently
    # vanishes and the suite still passes. This is the guard against a whole
    # class of checks disappearing quietly.
    assert len(CALLABLES) > 50, len(CALLABLES)
    assert any(owner is None for owner, _ in CALLABLES)
    assert any(owner == "Engine" for owner, _ in CALLABLES)


@pytest.mark.parametrize("class_name,func_name", CALLABLES)
def test_parameter_names_match_the_extension(class_name, func_name):
    """Names, in order. This is the check worth having.

    Every one of these arguments is a float or an int, so a transposed pair
    type-checks perfectly and seeds the economy wrong. In a sibling codebase I
    wrote ten such parameter names from memory and got six of them wrong;
    nothing caught it until a test read the real signature.
    """
    owner = core if class_name is None else getattr(core, class_name)
    target = getattr(owner, func_name)
    signature = getattr(target, "__text_signature__", None)
    if signature is None:
        pytest.skip(f"{func_name} exposes no __text_signature__")
    assert stub_params(class_name, func_name) == runtime_params(signature)


@pytest.mark.parametrize("name", ["Instrument", "Macro", "News", "NewsImpact",
                                 "MispricingState", "OrderBook", "GameRng"])
def test_constructor_parameter_names_match_the_extension(name):
    # PyO3 puts a #[new] signature on the CLASS rather than on __init__, which
    # reports (*args, **kwargs). Reading the wrong one would make this test
    # vacuous while looking thorough.
    cls = getattr(core, name)
    signature = getattr(cls, "__text_signature__", None)
    if signature is None:
        pytest.skip(f"{name} exposes no __text_signature__")
    assert stub_params(name, "__init__") == runtime_params(signature)
