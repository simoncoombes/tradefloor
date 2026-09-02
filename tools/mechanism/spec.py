"""The mechanism language: a declarative specification of one mechanism.

A mechanism declares its dials with defaults, the state it reads and
writes, the stream it draws from, and a body. The body is a tree of the
node classes below. Every expression carries a draw effect, which the
checker (``check.py``) computes: pure, or a count on the mechanism's
stream, where a count is a schedule quantity (a constant, or the company
count, or the tick count) and never a dial or a piece of state. The
emitter (``emit.py``) turns a checked mechanism into Rust that uses only
the ``mathx`` surface, keeps source evaluation order, never reassociates,
never emits ``mul_add``, and pins every recorded constant to its bits.

The language is small on purpose. It has what the shipped mechanisms
need and nothing they do not, so that a body a reader can hold in one
screen is also a body the checker can prove inert.

Expressions:

- ``Dial(name)``, ``State(path)``, ``Var(name)``, ``Const(value)``
- ``Draw(kind, site, tag)`` where kind is ``"uniform"`` or ``"normal"``
- ``Bin(op, left, right)`` for ``+ - * / < <= > >= == !=``
- ``Neg(expr)``
- ``Call(fn, *args)`` for a ``mathx`` function
- ``Extern(name, *args)`` for a named engine function the mechanism may
  call, declared on the mechanism with its draw effect (pure)
- ``If(cond, then, else)`` as an expression

Statements:

- ``Let(name, expr)``
- ``Set(path, expr)``: write a state field
- ``Add(path, expr)``: add to a state field
- ``When(cond, body)``: statements under a condition; the checker
  requires the body to be pure, which is the equal-effect rule with an
  empty else branch
- ``IfSome(path, name, body)``: statements under an optional state field
  being present, binding its value; pure body, same rule
- ``ForCompanies(name, body)``: the body once per company, with the
  index bound to ``name``; draws inside count once per company
"""
from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, field
from typing import Any


# -- expressions -----------------------------------------------------------

@dataclass(frozen=True)
class Dial:
    name: str


@dataclass(frozen=True)
class State:
    path: str


@dataclass(frozen=True)
class Var:
    name: str


@dataclass(frozen=True)
class Const:
    value: float


@dataclass(frozen=True)
class Draw:
    kind: str
    site: str
    tag: Any = 0


@dataclass(frozen=True)
class Bin:
    op: str
    left: Any
    right: Any


@dataclass(frozen=True)
class Neg:
    expr: Any


@dataclass(frozen=True)
class Call:
    fn: str
    args: tuple = ()


@dataclass(frozen=True)
class Extern:
    name: str
    args: tuple = ()


@dataclass(frozen=True)
class If:
    cond: Any
    then: Any
    otherwise: Any


# -- statements -------------------------------------------------------------

@dataclass(frozen=True)
class Let:
    name: str
    expr: Any


@dataclass(frozen=True)
class Set:
    path: str
    expr: Any


@dataclass(frozen=True)
class Add:
    path: str
    expr: Any


@dataclass(frozen=True)
class When:
    cond: Any
    body: tuple


@dataclass(frozen=True)
class IfSome:
    path: str
    name: str
    body: tuple


@dataclass(frozen=True)
class ForCompanies:
    name: str
    body: tuple


# -- the mechanism ----------------------------------------------------------

@dataclass(frozen=True)
class DialSpec:
    name: str
    default: float
    doc: str = ""


@dataclass(frozen=True)
class StateSpec:
    """One state field the mechanism reads or writes.

    ``scope`` is ``"engine"`` for a field of the engine or its economy and
    ``"company"`` for a per-company field. ``optional`` marks a field held
    in an ``Option``. ``declared`` marks a field this mechanism introduces,
    for which the emitter generates the struct field, the snapshot and
    restore entries and the Python binding; a field that already exists is
    referenced, not generated.
    """

    path: str
    scope: str = "company"
    optional: bool = False
    declared: bool = False
    default: float = 0.0
    doc: str = ""
    #: The Rust spelling of the field where the target function reads it.
    rust: str = ""
    #: A template for ``Add`` where a plain ``+=`` is not the idiom, with
    #: ``{index}``, ``{value}`` and ``{indent}`` filled by the emitter.
    add_rust: str = ""


@dataclass(frozen=True)
class ExternSpec:
    name: str
    rust: str
    doc: str = ""


@dataclass(frozen=True)
class Mechanism:
    name: str
    stream: str
    dials: tuple
    state: tuple
    externs: tuple
    body: tuple
    doc: str = ""
    #: The Rust the emitter targets: the function whose body is generated
    #: and the file it lives in.
    target: dict = field(default_factory=dict)

    def dial(self, name: str) -> DialSpec:
        for d in self.dials:
            if d.name == name:
                return d
        raise KeyError(name)

    def defaults(self) -> dict[str, float]:
        return {d.name: d.default for d in self.dials}

    def digest(self) -> str:
        """The identity of the specification: its dials, defaults, state,
        stream and body, serialised canonically. Two specs that emit the
        same Rust have the same digest; a changed default changes it."""
        body = json.dumps(to_json(self), sort_keys=True,
                          separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def to_json(node: Any) -> Any:
    """A canonical JSON form of a node tree, floats as their bits."""
    if isinstance(node, float):
        return {"f64": "0x%016X" % struct.unpack("<Q", struct.pack("<d", node))[0]}
    if isinstance(node, (int, str, bool)) or node is None:
        return node
    if isinstance(node, (tuple, list)):
        return [to_json(n) for n in node]
    if isinstance(node, dict):
        return {k: to_json(v) for k, v in sorted(node.items())}
    if hasattr(node, "__dataclass_fields__"):
        out = {"node": type(node).__name__}
        for name in node.__dataclass_fields__:
            out[name] = to_json(getattr(node, name))
        return out
    raise TypeError(f"not a spec node: {node!r}")


def bits(value: float) -> str:
    """The hex bits of an ``f64``, the form a recorded constant is pinned in."""
    return "0x%016X" % struct.unpack("<Q", struct.pack("<d", float(value)))[0]
