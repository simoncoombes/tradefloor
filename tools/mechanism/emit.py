"""The emitter: a checked mechanism as Rust, and a check that the
committed Rust is the generated Rust.

    python tools/mechanism/emit.py --check      # exit 1 on any difference
    python tools/mechanism/emit.py --write      # regenerate in place

The emitter uses only the ``mathx`` surface, keeps source evaluation
order, parenthesises every nested binary operation so nothing is
reassociated, never emits ``mul_add``, and pins every recorded constant
to its hex bits (``f64::from_bits``), with zero and one as themselves. Every draw is a
statement: the site call, then the draw, in the order the specification
lists them, so the schedule the library pins is the schedule the body
takes.

The generated body sits between two marker comments inside the target
function. ``--check`` extracts what is between them and compares it to
what the specification generates, the way ``record.py --check`` compares
a record; ``--write`` replaces it. The known-answer digest is what proves
the generated body is the shipped mechanism, and it is the gate's to run.

Declared state (``StateSpec(declared=True)``) generates the struct field,
its default, the snapshot and restore entries and the Python getter, as
text for the four places they go. No shipped mechanism declares state in
this phase; the toy in ``tests/test_mechanism.py`` exercises the
generation.
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "mechanisms"))

from spec import (Add, Bin, Call, Const, Dial, Draw, Extern, ForCompanies,  # noqa: E402
                  If, IfSome, Let, Mechanism, Neg, Set, State, Var, When, bits)
from check import SpecError, check, effect_of  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(HERE))
MATHX_RUST = {"exp": "mathx::exp", "log": "mathx::log", "pow": "mathx::pow",
              "sin": "mathx::sin", "cos": "mathx::cos", "sqrt": "mathx::sqrt",
              "max": "mathx::max", "min": "mathx::min", "clamp": "mathx::clamp"}


class EmitError(Exception):
    pass


def literal(value: float) -> str:
    """A constant as Rust: zero, one and minus one as themselves, anything
    else pinned to its bits with the decimal beside it.

    The three that pass through are exactly representable and parse to the
    same bits from any reader, so pinning them buys nothing. Negative zero
    is pinned, because ``-0.0`` and ``0.0`` read alike and are different
    values.
    """
    if value in (0.0, 1.0, -1.0) and not (value == 0.0 and str(value).startswith("-")):
        return f"{value!r}"
    return f"f64::from_bits({bits(value)}) /* {value!r} */"


class Emitter:
    def __init__(self, mech: Mechanism) -> None:
        self.mech = mech
        self.params = mech.target.get("params", "p")
        self.rng = mech.target.get("rng", "self.rng")
        self.state = {s.path: s for s in mech.state}
        self.externs = {e.name: e for e in mech.externs}

    # -- expressions --

    def expr(self, e, index: str = "index") -> str:
        if isinstance(e, Const):
            return literal(float(e.value))
        if isinstance(e, Dial):
            return f"{self.params}.{e.name}"
        if isinstance(e, State):
            spec = self.state.get(e.path)
            if spec is None:
                raise EmitError(f"state {e.path!r} is not declared on {self.mech.name}")
            return spec.rust or e.path
        if isinstance(e, Var):
            return e.name
        if isinstance(e, Draw):
            raise EmitError("a draw is a statement: bind it with Let (the "
                            "checker's hoist() produces that form)")
        if isinstance(e, Neg):
            return f"-{self.operand(e.expr, index)}"
        if isinstance(e, Bin):
            return f"{self.operand(e.left, index)} {e.op} {self.operand(e.right, index)}"
        if isinstance(e, Call):
            if e.fn not in MATHX_RUST:
                raise EmitError(f"{e.fn!r} is not on the mathx surface")
            return f"{MATHX_RUST[e.fn]}({', '.join(self.expr(a, index) for a in e.args)})"
        if isinstance(e, Extern):
            spec = self.externs.get(e.name)
            if spec is None:
                raise EmitError(f"extern {e.name!r} is not declared")
            return spec.rust.format(*[self.expr(a, index) for a in e.args])
        if isinstance(e, If):
            return (f"if {self.cond(e.cond, index)} {{ {self.expr(e.then, index)} }} "
                    f"else {{ {self.expr(e.otherwise, index)} }}")
        raise EmitError(f"not an expression: {e!r}")

    #: The expression forms that are not self-delimiting in Rust, so an
    #: operand of one of them carries parentheses. A Call and an Extern
    #: bring their own brackets and a leaf is one token.
    COMPOUND = (Bin, Neg, If)

    def operand(self, e, index: str) -> str:
        """An operand of a binary operation or of a negation, parenthesised
        whatever its precedence, so the emitted tree is the specification's
        tree and nothing is reassociated.

        Every compound form needs this, not only ``Bin``. ``Neg`` over
        ``Bin`` emitted ``-gain + offset``, which Rust reads as
        ``(-gain) + offset`` while the spec node is ``-(gain + offset)``;
        at gain 1.0 and offset 3.0 that is 2.0 against the specified -4.0.
        ``If`` as an operand emitted a bare ``if`` block beside an
        operator, and ``Neg`` over ``Neg`` emitted ``--x``, neither of
        which Rust parses.
        """
        text = self.expr(e, index)
        return f"({text})" if isinstance(e, self.COMPOUND) else text

    def cond(self, e, index: str) -> str:
        if isinstance(e, Bin) and e.op in ("<", "<=", ">", ">=", "==", "!="):
            return f"{self.expr(e.left, index)} {e.op} {self.expr(e.right, index)}"
        return self.expr(e, index)

    # -- statements --

    def body(self, stmts: tuple, depth: int, index: str = "index") -> list[str]:
        out: list[str] = []
        pad = "    " * depth
        for s in stmts:
            if isinstance(s, Let) and isinstance(s.expr, Draw):
                tag = self.expr(s.expr.tag, index) if not isinstance(s.expr.tag, int) else str(s.expr.tag)
                if not isinstance(s.expr.tag, int):
                    tag = f"{tag} as u32"
                draw = "next_f64" if s.expr.kind == "uniform" else "next_normal"
                out.append(f"{pad}{self.rng}.site(Site::{s.expr.site}, {tag});")
                out.append(f"{pad}let {s.name} = {self.rng}.{draw}();")
            elif isinstance(s, Let):
                out.append(f"{pad}let {s.name} = {self.expr(s.expr, index)};")
            elif isinstance(s, Set):
                spec = self.state[s.path]
                value = self.expr(s.expr, index)
                if spec.optional:
                    out.append(f"{pad}{spec.rust} = Some({value});")
                else:
                    out.append(f"{pad}{spec.rust} = {value};")
            elif isinstance(s, Add):
                spec = self.state[s.path]
                value = self.expr(s.expr, index)
                if spec.add_rust:
                    text = spec.add_rust.format(index=index, value=value, indent=pad)
                    out.extend(pad + line if i == 0 else line
                               for i, line in enumerate(text.split("\n")))
                else:
                    out.append(f"{pad}{spec.rust} += {value};")
            elif isinstance(s, When):
                out.append(f"{pad}if {self.cond(s.cond, index)} {{")
                out.extend(self.body(s.body, depth + 1, index))
                out.append(f"{pad}}}")
            elif isinstance(s, IfSome):
                spec = self.state[s.path]
                out.append(f"{pad}if let Some({s.name}) = {spec.rust} {{")
                out.extend(self.body(s.body, depth + 1, index))
                out.append(f"{pad}}}")
            elif isinstance(s, ForCompanies):
                out.append(f"{pad}for ({s.name}, company) in self.companies.iter_mut().enumerate() {{")
                out.extend(self.body(s.body, depth + 1, s.name))
                out.append(f"{pad}}}")
            else:
                raise EmitError(f"not a statement: {s!r}")
        return out

    def function_body(self) -> str:
        """The generated body between its markers, at the target's depth."""
        found = check(self.mech)
        if not found["proof"].inert:
            raise EmitError("the mechanism is not proven inert at its defaults: "
                            + "; ".join(found["proof"].failures))
        digest = found["digest"][:12]
        depth = 2
        pad = "    " * depth
        lines = [f"{pad}// mechanism:{self.mech.name} begin -- generated by "
                 f"tools/mechanism/emit.py from",
                 f"{pad}// tools/mechanism/mechanisms/{self.mech.name}.py "
                 f"(spec {digest}); do not edit by hand.",
                 f"{pad}// Draws: {found['effect'].describe()} on the "
                 f"{self.mech.stream} stream, unconditionally.",
                 f"{pad}let {self.params} = &self.params;"]
        lines.extend(self.body(self.mech.body, depth))
        lines.append(f"{pad}// mechanism:{self.mech.name} end")
        return "\n".join(lines) + "\n"


def markers(name: str) -> tuple[str, str]:
    return f"        // mechanism:{name} begin", f"        // mechanism:{name} end\n"


def accessor_body(mech: Mechanism, var: str) -> str:
    """The generated body of a function returning one of the body's bindings.

    A mechanism's dials and state are read by more than the mechanism. The
    jump intensity is the threshold `apply_jumps` compares its market
    uniform against, and a caller that wants to know whether a jump can
    fire wants the same number. Written by hand beside the generated body
    it was a second copy of an expression this language claims to own, and
    a copy is what the language exists to remove.

    So the binding is emitted: the top-level `Let` statements up to and
    including ``var``, then ``var`` as the return value. Every one of them
    has to be pure, which is checked rather than assumed; a binding that
    takes a draw is not a function of the state and cannot be one.
    """
    emitter = Emitter(mech)
    taken = []
    for stmt in mech.body:
        if not isinstance(stmt, Let):
            break
        effect = effect_of(stmt.expr, mech)
        if not effect.pure:
            raise EmitError(
                f"{var!r} cannot be a function of the state: {stmt.name!r} "
                f"above it takes {effect.describe()}")
        taken.append(stmt)
        if stmt.name == var:
            break
    else:
        taken = []
    if not taken or taken[-1].name != var:
        raise EmitError(
            f"{mech.name} has no pure binding {var!r} among the statements "
            "before its first draw")
    pad = "    " * 2
    lines = [f"{pad}// mechanism:{mech.name}.{var} begin -- generated by "
             f"tools/mechanism/emit.py from",
             f"{pad}// tools/mechanism/mechanisms/{mech.name}.py; do not "
             "edit by hand.",
             f"{pad}let {emitter.params} = &self.params;"]
    lines.extend(emitter.body(tuple(taken), 2))
    lines.append(f"{pad}{var}")
    lines.append(f"{pad}// mechanism:{mech.name}.{var} end")
    return "\n".join(lines) + "\n"


def regions(mech: Mechanism) -> list[tuple[str, str]]:
    """Every generated region of this mechanism: the body, then each
    accessor, as ``(marker name, generated text)``."""
    out = [(mech.name, Emitter(mech).function_body())]
    for var in mech.target.get("accessors", ()):
        out.append((f"{mech.name}.{var}", accessor_body(mech, var)))
    return out


def committed(mech: Mechanism, region: str | None = None) -> str | None:
    """What sits between the markers in the target file, or None."""
    path = os.path.join(ROOT, mech.target["file"])
    with open(path, "rb") as f:
        text = f.read().decode("utf-8").replace("\r\n", "\n")
    begin, end = markers(region or mech.name)
    i = text.find(begin)
    if i < 0:
        return None
    j = text.find(end, i)
    if j < 0:
        raise EmitError(f"{mech.target['file']} has a begin marker for "
                        f"{region or mech.name} and no end marker")
    return text[i:j + len(end)]


def write(mech: Mechanism) -> str:
    """Replace every generated region in the target with what it generates.

    On the first write of the body, the whole existing body of the target
    function is replaced; after that, only what lies between the markers.
    An accessor region is only ever replaced between its markers, because
    the function it sits in is written by hand around it.
    """
    path = os.path.join(ROOT, mech.target["file"])
    for name, generated in regions(mech)[1:]:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8").replace("\r\n", "\n")
        have = committed(mech, name)
        if have is None:
            raise EmitError(
                f"{mech.target['file']} has no markers for {name}; an "
                "accessor region is placed by hand once and generated "
                "thereafter")
        with open(path, "wb") as f:
            f.write(text.replace(have, generated).encode("utf-8"))
    with open(path, "rb") as f:
        text = f.read().decode("utf-8").replace("\r\n", "\n")
    generated = Emitter(mech).function_body()
    have = committed(mech)
    if have is not None:
        new = text.replace(have, generated)
    else:
        head = f"    fn {mech.target['function']}(&mut self) {{\n"
        i = text.find(head)
        if i < 0:
            raise EmitError(f"{mech.target['file']} has no fn {mech.target['function']}")
        start = i + len(head)
        # the function ends at the first line that is exactly "    }"
        j = text.find("\n    }\n", start)
        new = text[:start] + generated + text[j + 1:]
    with open(path, "wb") as f:
        f.write(new.encode("utf-8"))
    return generated


def binding_path(spec, mech: Mechanism) -> str:
    """A declared field's spelling inside the Python binding.

    ``StateSpec.rust`` is how the target function reads the field, rooted
    at the engine itself: ``self.economy.fear``. The binding holds the
    engine as ``self.inner``, so the same field is
    ``self.inner.economy.fear`` there. Concatenating the two roots gave
    ``self.inner.self.economy.fear``, which compiles nowhere.
    """
    if not spec.rust.startswith("self."):
        raise EmitError(
            f"{mech.name} declares state {spec.path!r} with the Rust spelling "
            f"{spec.rust!r}, which is not rooted at the engine; the emitter "
            "cannot write a binding path for it")
    return "self.inner." + spec.rust[len("self."):]


def state_generation(mech: Mechanism) -> dict[str, str]:
    """The five texts a declared state field needs, keyed by where they go."""
    fields, defaults, snapshot, restore, python = [], [], [], [], []
    for s in mech.state:
        if not s.declared:
            continue
        name = s.path.split(".")[-1]
        path = binding_path(s, mech)
        fields.append(f"    /// {s.doc}\n    pub {name}: f64,")
        defaults.append(f"            {name}: {literal(float(s.default))},")
        snapshot.append(f'        out.set_item("{name}", {path})?;')
        restore.append(f'        if let Some(v) = state.get_item("{name}")? {{\n'
                       f'            {path} = v.extract::<f64>()?;\n'
                       f'        }}')
        python.append(f"    @property\n    def {name}(self) -> float: ...")
    return {"struct_fields": "\n".join(fields), "defaults": "\n".join(defaults),
            "snapshot": "\n".join(snapshot), "restore": "\n".join(restore),
            "python": "\n".join(python)}


def shipped() -> list[Mechanism]:
    from jumps import JUMPS
    return [JUMPS]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="compare the committed Rust to the generated Rust")
    ap.add_argument("--write", action="store_true",
                    help="regenerate the committed Rust in place")
    a = ap.parse_args(argv)
    drift = 0
    for mech in shipped():
        found = check(mech)
        print(f"  {mech.name}: {found['effect'].describe()}; inert at the "
              f"defaults: {found['proof'].inert}; spec {found['digest'][:12]}")
        if a.write:
            write(mech)
            print(f"  wrote {mech.target['file']}")
        if a.check:
            for name, want in regions(mech):
                have = committed(mech, name)
                if have != want:
                    drift += 1
                    print(f"  {mech.target['file']}: the committed {name} "
                          "differs from the generated one")
    if a.check:
        print(f"{drift} differences")
        return 1 if drift else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
