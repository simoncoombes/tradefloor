"""The checker: draw effects, the equal-effect rule, hoisting, inertness.

Three things are checked, each a claim the emitter relies on:

1. Every expression has a draw effect, and a draw count is a schedule
   quantity. A body's effect is the sum of its statements'; a
   ``ForCompanies`` multiplies its body's count by the company count. A
   count that depends on a dial or on state is a type error, because the
   stream position would then depend on a parameter and no preset could
   ship inert.

2. A conditional on a dial or on state has equal draw effect on both
   branches. When it does not, the fix is draw hoisting: :func:`hoist`
   takes every draw out of the branches, unconditionally and in source
   order, binds each to a name, and uses the name inside the branch. The
   hoisted body has the same value on the branch taken and the same
   effect on both, and the stream position no longer depends on the
   condition. Hoisting changes what the mechanism does to the stream, so
   the checker proposes it and never applies it silently.

3. At the default doses the body reduces to the identity on state and
   price. :func:`prove_inert` evaluates the body with the dials at their
   defaults, uniforms as symbols in ``[0, 1)``, normals and state as
   symbols, and requires every write to be either unreachable (its guard
   decidably false) or the identity (writing a field its own value, or
   adding a decided zero). Anything it cannot decide is a failure to
   prove, reported as such; the prover does not guess.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from spec import (Add, Bin, Call, Const, Dial, Draw, Extern, ForCompanies,
                  If, IfSome, Let, Mechanism, Neg, Set, State, Var, When)


class SpecError(Exception):
    """The specification breaks a rule the emitter relies on."""


# -- draw effects ------------------------------------------------------------

@dataclass(frozen=True)
class Effect:
    """A draw count on the mechanism's stream: ``constant + per_company *
    companies`` uniforms and normals. Pure is all zeros."""

    uniforms: int = 0
    normals: int = 0
    uniforms_per_company: int = 0
    normals_per_company: int = 0

    def __add__(self, other: "Effect") -> "Effect":
        return Effect(self.uniforms + other.uniforms,
                      self.normals + other.normals,
                      self.uniforms_per_company + other.uniforms_per_company,
                      self.normals_per_company + other.normals_per_company)

    @property
    def pure(self) -> bool:
        return self == Effect()

    def per_company(self) -> "Effect":
        if self.uniforms_per_company or self.normals_per_company:
            raise SpecError("a ForCompanies inside a ForCompanies would "
                            "count draws per company squared, which is not "
                            "a schedule quantity")
        return Effect(0, 0, self.uniforms, self.normals)

    def describe(self) -> str:
        parts = []
        if self.uniforms:
            parts.append(f"{self.uniforms} uniform")
        if self.normals:
            parts.append(f"{self.normals} normal")
        if self.uniforms_per_company:
            parts.append(f"{self.uniforms_per_company} uniform per company")
        if self.normals_per_company:
            parts.append(f"{self.normals_per_company} normal per company")
        return ", ".join(parts) or "pure"


def depends_on_dial_or_state(expr: Any) -> bool:
    if isinstance(expr, (Dial, State)):
        return True
    if isinstance(expr, (Const, Draw, Var)):
        return False
    if isinstance(expr, Bin):
        return depends_on_dial_or_state(expr.left) or depends_on_dial_or_state(expr.right)
    if isinstance(expr, Neg):
        return depends_on_dial_or_state(expr.expr)
    if isinstance(expr, (Call, Extern)):
        return any(depends_on_dial_or_state(a) for a in expr.args)
    if isinstance(expr, If):
        return any(depends_on_dial_or_state(e) for e in (expr.cond, expr.then, expr.otherwise))
    raise SpecError(f"not an expression: {expr!r}")


def effect_of(expr: Any, mech: Mechanism, env: dict | None = None) -> Effect:
    """The draw effect of an expression. ``env`` carries the effect of
    each ``Let``-bound name, which is pure: a draw bound by ``Let`` was
    taken at the ``Let``, and the name is a value."""
    if isinstance(expr, (Dial, State, Var, Const)):
        return Effect()
    if isinstance(expr, Draw):
        if expr.kind == "uniform":
            return Effect(uniforms=1)
        if expr.kind == "normal":
            return Effect(normals=1)
        raise SpecError(f"a draw is uniform or normal, not {expr.kind!r}")
    if isinstance(expr, Bin):
        return effect_of(expr.left, mech, env) + effect_of(expr.right, mech, env)
    if isinstance(expr, Neg):
        return effect_of(expr.expr, mech, env)
    if isinstance(expr, Call):
        if expr.fn not in MATHX:
            raise SpecError(f"{expr.fn!r} is not on the mathx surface; one of "
                            + ", ".join(sorted(MATHX)))
        return sum((effect_of(a, mech, env) for a in expr.args), Effect())
    if isinstance(expr, Extern):
        if expr.name not in {e.name for e in mech.externs}:
            raise SpecError(f"extern {expr.name!r} is not declared on {mech.name}")
        return sum((effect_of(a, mech, env) for a in expr.args), Effect())
    if isinstance(expr, If):
        cond = effect_of(expr.cond, mech, env)
        then = effect_of(expr.then, mech, env)
        other = effect_of(expr.otherwise, mech, env)
        if depends_on_dial_or_state(expr.cond) and then != other:
            raise SpecError(
                f"a conditional on a dial or on state draws {then.describe()} "
                f"on one branch and {other.describe()} on the other; the "
                "stream position would depend on the condition. Hoist the "
                "draws: check.hoist() proposes the rewrite.")
        if then != other:
            raise SpecError(
                f"a conditional draws {then.describe()} on one branch and "
                f"{other.describe()} on the other; hoist the draws.")
        return cond + then
    raise SpecError(f"not an expression: {expr!r}")


def effect_of_body(body: tuple, mech: Mechanism) -> Effect:
    total = Effect()
    for stmt in body:
        total = total + effect_of_statement(stmt, mech)
    return total


def effect_of_statement(stmt: Any, mech: Mechanism) -> Effect:
    if isinstance(stmt, Let):
        return effect_of(stmt.expr, mech)
    if isinstance(stmt, (Set, Add)):
        return effect_of(stmt.expr, mech)
    if isinstance(stmt, When):
        cond = effect_of(stmt.cond, mech)
        inner = effect_of_body(stmt.body, mech)
        if not inner.pure:
            raise SpecError(
                f"a When on {'a dial or state' if depends_on_dial_or_state(stmt.cond) else 'a value'} "
                f"draws {inner.describe()} inside its body and nothing "
                "otherwise; hoist the draws above the When.")
        return cond
    if isinstance(stmt, IfSome):
        inner = effect_of_body(stmt.body, mech)
        if not inner.pure:
            raise SpecError(
                f"an IfSome on {stmt.path} draws {inner.describe()} inside "
                "its body; whether the field is present is state, so hoist "
                "the draws above it.")
        return Effect()
    if isinstance(stmt, ForCompanies):
        return effect_of_body(stmt.body, mech).per_company()
    raise SpecError(f"not a statement: {stmt!r}")


MATHX = {"exp", "log", "pow", "sin", "cos", "sqrt", "max", "min", "clamp"}


# -- hoisting -----------------------------------------------------------------

def hoist(body: tuple) -> tuple:
    """The body with every draw inside a conditional taken before it.

    Each draw found inside an ``If`` expression, a ``When`` or an ``IfSome``
    is replaced by a ``Var`` and a ``Let`` of that draw is inserted before
    the statement, in the order the draws appear in the source. The value
    on the branch taken is unchanged; the effect is now the same on every
    branch. What changes is the stream: the draws are taken whether or not
    the branch is, so a hoisted mechanism consumes the stream differently
    from the unhoisted one, and applying this to a shipped mechanism moves
    every later draw on its stream for every preset. The checker returns
    the rewrite; it does not install it.
    """
    out = []
    counter = [0]
    for stmt in body:
        lets: list = []
        new = _hoist_statement(stmt, lets, counter, inside=False)
        out.extend(lets)
        out.append(new)
    return tuple(out)


def _hoist_expr(expr: Any, lets: list, counter: list, inside: bool) -> Any:
    if isinstance(expr, Draw):
        if inside:
            counter[0] += 1
            name = f"hoisted_{expr.kind}_{counter[0]}"
            lets.append(Let(name, expr))
            return Var(name)
        return expr
    if isinstance(expr, Bin):
        return Bin(expr.op, _hoist_expr(expr.left, lets, counter, inside),
                   _hoist_expr(expr.right, lets, counter, inside))
    if isinstance(expr, Neg):
        return Neg(_hoist_expr(expr.expr, lets, counter, inside))
    if isinstance(expr, Call):
        return Call(expr.fn, tuple(_hoist_expr(a, lets, counter, inside) for a in expr.args))
    if isinstance(expr, Extern):
        return Extern(expr.name, tuple(_hoist_expr(a, lets, counter, inside) for a in expr.args))
    if isinstance(expr, If):
        cond = _hoist_expr(expr.cond, lets, counter, inside)
        then = _hoist_expr(expr.then, lets, counter, True)
        other = _hoist_expr(expr.otherwise, lets, counter, True)
        return If(cond, then, other)
    return expr


def _hoist_statement(stmt: Any, lets: list, counter: list, inside: bool) -> Any:
    if isinstance(stmt, Let):
        return Let(stmt.name, _hoist_expr(stmt.expr, lets, counter, inside))
    if isinstance(stmt, Set):
        return Set(stmt.path, _hoist_expr(stmt.expr, lets, counter, inside))
    if isinstance(stmt, Add):
        return Add(stmt.path, _hoist_expr(stmt.expr, lets, counter, inside))
    if isinstance(stmt, When):
        cond = _hoist_expr(stmt.cond, lets, counter, inside)
        return When(cond, tuple(_hoist_statement(s, lets, counter, True) for s in stmt.body))
    if isinstance(stmt, IfSome):
        return IfSome(stmt.path, stmt.name,
                      tuple(_hoist_statement(s, lets, counter, True) for s in stmt.body))
    if isinstance(stmt, ForCompanies):
        inner_lets: list = []
        new_body = []
        for s in stmt.body:
            new = _hoist_statement(s, inner_lets, counter, inside)
            new_body.extend(inner_lets)
            inner_lets.clear()
            new_body.append(new)
        return ForCompanies(stmt.name, tuple(new_body))
    raise SpecError(f"not a statement: {stmt!r}")


# -- inertness ----------------------------------------------------------------

class Sym:
    """A symbolic value with an interval: a uniform is ``[0, 1)``, a normal
    or a state field is unbounded."""

    def __init__(self, name: str, lo: float | None = None,
                 hi: float | None = None, hi_open: bool = False) -> None:
        self.name, self.lo, self.hi, self.hi_open = name, lo, hi, hi_open

    def __repr__(self) -> str:
        return f"Sym({self.name})"


UNKNOWN = object()


def _decide(op: str, left: Any, right: Any) -> Any:
    """A comparison's truth from concrete values and intervals, or UNKNOWN."""
    if isinstance(left, float) and isinstance(right, float):
        return {"<": left < right, "<=": left <= right, ">": left > right,
                ">=": left >= right, "==": left == right, "!=": left != right}[op]
    if isinstance(left, Sym) and isinstance(right, float):
        lo, hi = left.lo, left.hi
        if op == "<":
            if lo is not None and lo >= right:
                return False
            if hi is not None and (hi < right or (hi == right and left.hi_open)):
                return True
        if op == ">=":
            r = _decide("<", left, right)
            return UNKNOWN if r is UNKNOWN else not r
        if op == "!=" and lo is not None and hi is not None and lo == hi == right:
            return False
        return UNKNOWN
    if isinstance(left, float) and isinstance(right, Sym):
        flipped = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "==": "==", "!=": "!="}[op]
        return _decide(flipped, right, left)
    return UNKNOWN


def _arith(op: str, a: Any, b: Any) -> Any:
    if isinstance(a, float) and isinstance(b, float):
        return {"+": a + b, "-": a - b, "*": a * b, "/": a / b if b else float("nan")}[op]
    # a decided zero absorbs a multiply, and adding a decided zero to a
    # symbol is the symbol: the two identities the inertness proof needs
    if op == "*" and (a == 0.0 or b == 0.0):
        return 0.0
    if op == "+" and a == 0.0:
        return b
    if op == "+" and b == 0.0:
        return a
    if op == "-" and b == 0.0:
        return a
    return Sym(f"({a!r} {op} {b!r})")


class Proof:
    def __init__(self) -> None:
        self.writes: list[str] = []
        self.failures: list[str] = []

    @property
    def inert(self) -> bool:
        return not self.failures


def prove_inert(mech: Mechanism, doses: dict[str, float] | None = None) -> Proof:
    """Evaluate the body at ``doses`` (the defaults when not given) and
    require every write to be unreachable or the identity."""
    doses = dict(mech.defaults(), **(doses or {}))
    proof = Proof()
    env: dict[str, Any] = {}
    counter = [0]
    _prove_body(mech.body, mech, doses, env, counter, proof, reachable=True)
    return proof


def _eval(expr: Any, mech: Mechanism, doses: dict, env: dict, counter: list) -> Any:
    if isinstance(expr, Const):
        return float(expr.value)
    if isinstance(expr, Dial):
        return float(doses[expr.name])
    if isinstance(expr, State):
        return Sym(f"state.{expr.path}")
    if isinstance(expr, Var):
        return env[expr.name]
    if isinstance(expr, Draw):
        counter[0] += 1
        if expr.kind == "uniform":
            return Sym(f"u{counter[0]}", 0.0, 1.0, hi_open=True)
        return Sym(f"z{counter[0]}")
    if isinstance(expr, Neg):
        v = _eval(expr.expr, mech, doses, env, counter)
        return -v if isinstance(v, float) else Sym(f"-{v!r}")
    if isinstance(expr, Bin):
        a = _eval(expr.left, mech, doses, env, counter)
        b = _eval(expr.right, mech, doses, env, counter)
        if expr.op in ("<", "<=", ">", ">=", "==", "!="):
            return _decide(expr.op, a, b)
        return _arith(expr.op, a, b)
    if isinstance(expr, Call):
        args = [_eval(a, mech, doses, env, counter) for a in expr.args]
        if all(isinstance(a, float) for a in args):
            import math
            f = {"exp": math.exp, "log": math.log, "pow": math.pow, "sin": math.sin,
                 "cos": math.cos, "sqrt": math.sqrt, "max": max, "min": min,
                 "clamp": lambda x, lo, hi: max(lo, min(hi, x))}[expr.fn]
            return float(f(*args))
        return Sym(f"{expr.fn}{tuple(args)!r}")
    if isinstance(expr, Extern):
        args = [_eval(a, mech, doses, env, counter) for a in expr.args]
        return Sym(f"{expr.name}{tuple(args)!r}")
    if isinstance(expr, If):
        c = _eval(expr.cond, mech, doses, env, counter)
        if c is True:
            return _eval(expr.then, mech, doses, env, counter)
        if c is False:
            return _eval(expr.otherwise, mech, doses, env, counter)
        t = _eval(expr.then, mech, doses, env, counter)
        o = _eval(expr.otherwise, mech, doses, env, counter)
        if isinstance(t, float) and isinstance(o, float) and t == o:
            return t
        return Sym(f"if({c!r}, {t!r}, {o!r})")
    raise SpecError(f"not an expression: {expr!r}")


def _prove_body(body: tuple, mech: Mechanism, doses: dict, env: dict,
                counter: list, proof: Proof, reachable: bool) -> None:
    for stmt in body:
        if isinstance(stmt, Let):
            env[stmt.name] = _eval(stmt.expr, mech, doses, env, counter)
        elif isinstance(stmt, (Set, Add)):
            value = _eval(stmt.expr, mech, doses, env, counter)
            if not reachable:
                proof.writes.append(f"{stmt.path}: unreachable at the defaults")
                continue
            if isinstance(stmt, Add) and value == 0.0:
                proof.writes.append(f"{stmt.path}: adds a decided zero")
                continue
            if isinstance(stmt, Set) and isinstance(value, Sym) and value.name == f"state.{stmt.path}":
                proof.writes.append(f"{stmt.path}: written its own value")
                continue
            proof.failures.append(
                f"{stmt.path} is written {value!r} on a path the defaults reach")
        elif isinstance(stmt, When):
            c = _eval(stmt.cond, mech, doses, env, counter)
            inner = reachable and c is not False
            if reachable and c is UNKNOWN:
                _prove_body(stmt.body, mech, doses, dict(env), counter, proof, True)
            else:
                _prove_body(stmt.body, mech, doses, dict(env), counter, proof, inner)
        elif isinstance(stmt, IfSome):
            local = dict(env)
            local[stmt.name] = Sym(f"state.{stmt.path}")
            _prove_body(stmt.body, mech, doses, local, counter, proof, reachable)
        elif isinstance(stmt, ForCompanies):
            local = dict(env)
            local[stmt.name] = Sym("company")
            _prove_body(stmt.body, mech, doses, local, counter, proof, reachable)
        else:
            raise SpecError(f"not a statement: {stmt!r}")


# -- the whole check ------------------------------------------------------------

def check(mech: Mechanism) -> dict:
    """Run every check and return what was found; raises on a type error."""
    if mech.stream not in ("market", "economy", "external", "jumps", "volume",
                           "news", "volume_idio"):
        raise SpecError(f"{mech.stream!r} is not a stream")
    effect = effect_of_body(mech.body, mech)
    proof = prove_inert(mech)
    return {"effect": effect, "proof": proof, "digest": mech.digest()}
