"""The mechanism language (noise, phase 5).

The checker's three rules, the hoisting rewrite, the emitter's contract
and the committed Rust are each stated here. The known-answer digest,
run by the gate, is what proves the emitted jump mechanism is the shipped
one; this file proves the tooling says what it claims.
"""

import dataclasses
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "mechanism"))
sys.path.insert(0, os.path.join(ROOT, "tools", "mechanism", "mechanisms"))

import check as checker  # noqa: E402
import emit  # noqa: E402
from spec import (Add, Bin, Call, Const, Dial, DialSpec, Draw, Extern,  # noqa: E402
                  ForCompanies, If, IfSome, Let, Mechanism, Neg, Set, State,
                  StateSpec, Var, When, bits)
from jumps import JUMPS  # noqa: E402
from vix_jump import VIX_JUMP_AS_WRITTEN  # noqa: E402


def toy(body, dials=(DialSpec("gain", 0.0),), state=(), stream="jumps"):
    return Mechanism(name="toy", stream=stream, dials=dials,
                     state=state or (StateSpec("x", scope="engine", rust="self.x"),),
                     externs=(), body=body, target={"params": "p", "rng": "self.rng"})


# -- the jump mechanism ------------------------------------------------------

def test_the_jump_specification_checks_and_is_inert_at_the_defaults():
    found = checker.check(JUMPS)
    assert found["effect"] == checker.Effect(1, 1, 1, 1)
    assert found["effect"].describe() == (
        "1 uniform, 1 normal, 1 uniform per company, 1 normal per company")
    assert found["proof"].inert, found["proof"].failures
    assert len(found["digest"]) == 64


def test_the_committed_rust_is_the_generated_rust():
    have = emit.committed(JUMPS)
    assert have is not None, "engine.rs carries no generated jump body"
    assert have == emit.Emitter(JUMPS).function_body()
    assert emit.main(["--check"]) == 0


def test_the_generated_rust_keeps_the_contract():
    text = emit.Emitter(JUMPS).function_body()
    assert "mul_add" not in text
    assert "self.jump_rng.site(Site::JumpMarketU, 0);" in text
    assert "let u_market = self.jump_rng.next_f64();" in text
    assert "self.jump_rng.site(Site::JumpCompanyU, index as u32);" in text
    # source order and no reassociation: fully parenthesised, as written
    assert "(1.0 - p.jump_vix_coupling) + ((p.jump_vix_coupling * ratio) * ratio)" in text
    assert "crate::market::tick::clamp_s(&self.params, s + total)" in text
    assert "let ratio = self.economy.vix / p.market_vol_vix_anchor;" in text
    assert text.index("next_f64") < text.index("next_normal")
    assert "if let Some(acc) = self.attribution.get_mut(index)" in text


# -- the checker's rules -----------------------------------------------------

def test_a_draw_inside_a_branch_on_a_dial_is_a_type_error_and_hoists():
    body = (Set("x", If(Bin("==", Dial("gain"), Const(0.0)), Const(0.0),
                        Bin("*", Dial("gain"), Draw("normal", "External", 0)))),)
    with pytest.raises(checker.SpecError, match="hoist"):
        checker.check(toy(body))
    hoisted = checker.hoist(body)
    assert isinstance(hoisted[0], Let) and isinstance(hoisted[0].expr, Draw)
    assert isinstance(hoisted[1], Set)
    found = checker.check(toy(hoisted))
    assert found["effect"] == checker.Effect(normals=1)
    # the value on the branch taken is unchanged: gain times the same draw
    assert hoisted[1].expr.otherwise == Bin("*", Dial("gain"), Var("hoisted_normal_1"))


def test_a_draw_under_a_when_on_state_is_a_type_error():
    body = (When(Bin("<", State("x"), Const(1.0)),
                 (Let("z", Draw("normal", "External", 0)), Add("x", Var("z")))),)
    with pytest.raises(checker.SpecError, match="hoist"):
        checker.check(toy(body))


def test_the_inertness_proof_distinguishes_a_write_from_a_decided_zero():
    # An Add of a decided zero is NOT proven inert: see
    # test_adding_a_decided_zero_is_not_proven_inert for why. The guarded
    # form below is what a mechanism uses instead.
    adds_zero = toy((Let("z", Draw("normal", "External", 0)),
                     Add("x", Bin("*", Dial("gain"), Var("z")))))
    proof = checker.prove_inert(adds_zero)
    assert not proof.inert
    assert "x adds a decided zero" in proof.failures[0]
    live = toy((Set("x", Bin("+", Dial("gain"), Const(1.0))),))
    proof = checker.prove_inert(live)
    assert not proof.inert
    assert "x is written 1.0" in proof.failures[0]
    # a write the defaults cannot reach: u < 0.0 is false on [0, 1)
    guarded = toy((Let("u", Draw("uniform", "External", 0)),
                   When(Bin("<", Var("u"), Dial("gain")), (Set("x", Const(2.0)),))))
    proof = checker.prove_inert(guarded)
    assert proof.inert and proof.writes == ["x: unreachable at the defaults"]
    # and not proven at a dose that opens the guard
    assert not checker.prove_inert(guarded, {"gain": 0.5}).inert
    # a comparison the intervals cannot decide is a failure to prove, not a pass
    undecided = toy((Let("z", Draw("normal", "External", 0)),
                     When(Bin("<", Var("z"), Const(0.0)), (Set("x", Const(2.0)),))))
    assert not checker.prove_inert(undecided).inert


def test_a_mathx_call_off_the_surface_is_refused():
    with pytest.raises(checker.SpecError, match="mathx"):
        checker.check(toy((Set("x", checker.Call("tanh", (Dial("gain"),))),)))


# -- the finding ---------------------------------------------------------------

def test_vix_jump_as_written_is_rejected_and_hoisting_would_move_the_stream():
    with pytest.raises(checker.SpecError, match="dial or on state"):
        checker.check(VIX_JUMP_AS_WRITTEN)
    hoisted = checker.hoist(VIX_JUMP_AS_WRITTEN.body)
    rewritten = Mechanism(**{**VIX_JUMP_AS_WRITTEN.__dict__, "body": hoisted})
    found = checker.check(rewritten)
    # two uniforms on the economy stream at every close, for every preset:
    # the rewrite the checker proposes and this work does not apply
    assert found["effect"] == checker.Effect(uniforms=2)
    # The rewrite ends in `vix += fear_jump`, and fear_jump is a decided
    # zero at the defaults. The prover does not decide that write, because
    # adding +0.0 to a field holding -0.0 changes its bits and no sign for
    # the field is available. The finding is the stream, measured above.
    assert not found["proof"].inert
    assert found["proof"].failures == [
        "economy.vix adds a decided zero on a path the defaults reach; "
        "that is the identity except on a field holding -0.0, which the "
        "prover cannot rule out"]
    assert emit.committed(VIX_JUMP_AS_WRITTEN) is None


# -- the emitter ---------------------------------------------------------------

def test_constants_are_pinned_to_their_bits():
    e = emit.Emitter(toy((Set("x", Bin("*", Const(0.15), Dial("gain"))),)))
    text = "\n".join(e.body(e.mech.body, 2))
    assert f"f64::from_bits({bits(0.15)}) /* 0.15 */" in text
    assert bits(0.15) == "0x3FC3333333333333"
    assert emit.literal(0.0) == "0.0" and emit.literal(1.0) == "1.0"


def test_declared_state_generates_the_five_texts():
    """Equality, not containment.

    These assertions read `in` on a disjunction whose second arm was a
    prefix of the first, so the whole assertion reduced to the prefix and
    passed on any value the generator put there. The snapshot line it
    passed on was `self.inner.self.economy.fear`, which compiles nowhere.
    """
    mech = toy((Set("fear", Const(0.0)),),
               state=(StateSpec("fear", scope="engine", rust="self.economy.fear",
                                declared=True, default=0.0, doc="the fear level"),))
    texts = emit.state_generation(mech)
    assert sorted(texts) == ["defaults", "python", "restore", "snapshot",
                             "struct_fields"]
    assert texts["struct_fields"] == (
        "    /// the fear level\n    pub fear: f64,")
    assert texts["defaults"] == "            fear: 0.0,"
    assert texts["snapshot"] == (
        '        out.set_item("fear", self.inner.economy.fear)?;')
    assert texts["restore"] == (
        '        if let Some(v) = state.get_item("fear")? {\n'
        '            self.inner.economy.fear = v.extract::<f64>()?;\n'
        '        }')
    assert texts["python"] == (
        "    @property\n    def fear(self) -> float: ...")


def test_a_declared_field_off_the_engine_root_is_refused():
    """The binding path is built by replacing the `self.` root, so a
    spelling that is not rooted there has no binding path to build."""
    mech = toy((Set("fear", Const(0.0)),),
               state=(StateSpec("fear", scope="company", rust="company.fear",
                                declared=True, default=0.0, doc="the fear"),))
    with pytest.raises(emit.EmitError, match="not rooted at the engine"):
        emit.state_generation(mech)


def test_the_spec_digest_moves_with_a_default():
    other = Mechanism(**{**JUMPS.__dict__,
                         "dials": tuple(DialSpec(d.name, 0.5 if d.name == "jump_sigma_idio" else d.default, d.doc)
                                        for d in JUMPS.dials)})
    assert other.digest() != JUMPS.digest()
    assert JUMPS.digest() == JUMPS.digest()


# -- the record ------------------------------------------------------------------

def test_the_record_carries_the_mechanism_set_beside_the_fingerprint():
    """Read from the repository's record files, which are what the wheel
    ships; the installed package on a developer box may be an older wheel
    whose records predate the field."""
    import glob
    import json

    import tradefloor as tf
    paths = sorted(glob.glob(os.path.join(ROOT, "python", "tradefloor",
                                          "presets", "*.json")))
    assert paths
    for path in paths:
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
        name = record["preset"]
        assert record["fingerprint"] == name
        assert record["schema"] == 1
        jumps = record["mechanisms"]["jumps"]
        assert jumps["spec"] == JUMPS.digest()
        assert jumps["stream"] == "jumps"
        coefficients = tf.ModelParams.from_preset(name).to_dict()
        for dial in JUMPS.dials:
            assert jumps["doses"][dial.name] == coefficients.get(dial.name, dial.default)


# -- the rules each have a test ----------------------------------------------

def test_an_ifsome_body_that_draws_is_a_type_error():
    """The rule that stops a mechanism drawing inside `if let Some(...)`.

    Whether the field is present is state, so a draw under it puts the
    stream position under state. The jump mechanism writes inside two
    IfSome blocks, so this is the rule holding its schedule fixed.
    """
    body = (IfSome("x", "held", (Let("z", Draw("normal", "External", 0)),
                                 Add("x", Var("z")))),)
    with pytest.raises(checker.SpecError, match="IfSome"):
        checker.check(toy(body))
    # the same body with the draw above the IfSome is fine
    ok = (Let("z", Draw("normal", "External", 0)),
          IfSome("x", "held", (Add("x", Const(0.0)),)))
    assert checker.check(toy(ok))["effect"] == checker.Effect(normals=1)


def test_a_forcompanies_inside_a_forcompanies_is_a_type_error():
    """A draw per company per company is not a schedule quantity."""
    inner = ForCompanies("j", (Let("z", Draw("normal", "External", 0)),
                               Add("x", Var("z"))))
    with pytest.raises(checker.SpecError, match="per company squared"):
        checker.check(toy((ForCompanies("i", (inner,)),)))
    # one level is a schedule quantity
    one = (ForCompanies("i", (Let("z", Draw("normal", "External", 0)),
                              Add("x", Var("z")))),)
    assert checker.check(toy(one))["effect"] == checker.Effect(
        normals_per_company=1)


def test_adding_a_live_value_is_not_proven_inert():
    """The Add rule. A write reached at the defaults is a failure to
    prove whatever its value, so a live addend cannot pass as inert."""
    live = toy((Add("x", Bin("+", Dial("gain"), Const(1.0))),))
    proof = checker.prove_inert(live)
    assert not proof.inert
    assert proof.failures == ["x adds 1.0 on a path the defaults reach"]


def test_adding_a_decided_zero_is_not_proven_inert():
    """Adding zero is the identity everywhere except on negative zero.

    For a field holding -0.0, `x += 0.0` leaves +0.0, a different bit
    pattern, and `x = 0.0 + x` does the same. The prover has no sign for
    a symbolic field, so it cannot decide either and reports both rather
    than guessing. `mechanisms/jumps.py` guards its write for this
    reason, and its comment says so.
    """
    assert math.copysign(1.0, -0.0 + 0.0) == 1.0
    assert math.copysign(1.0, -0.0) == -1.0
    adds = toy((Add("x", Const(0.0)),))
    assert not checker.prove_inert(adds).inert
    writes_sum = toy((Set("x", Bin("+", Const(0.0), State("x"))),))
    assert not checker.prove_inert(writes_sum).inert
    # the guarded form a mechanism uses instead is proven
    guarded = toy((Let("u", Draw("uniform", "External", 0)),
                   When(Bin("<", Var("u"), Dial("gain")),
                        (Add("x", Const(2.0)),))))
    proof = checker.prove_inert(guarded)
    assert proof.inert and proof.writes == ["x: unreachable at the defaults"]
    # a decided zero still absorbs a multiply, which is the one identity
    # the prover assumes and the guards above rest on
    assert checker._arith("*", 0.0, checker.Sym("z")) == 0.0


def test_a_stream_off_the_allowlist_is_refused():
    with pytest.raises(checker.SpecError, match="is not a stream"):
        checker.check(toy((Set("x", Const(0.0)),), stream="jumpz"))
    for name in ("market", "economy", "external", "jumps", "volume", "news",
                 "volume_idio"):
        assert checker.check(toy((Set("x", State("x")),), stream=name))


def test_an_undeclared_name_is_a_spec_error():
    """Every undeclared name reports the mechanism and the name.

    These escaped the checker: a State path and a Set path passed clean
    and failed later inside the emitter, and an undeclared Dial and an
    unbound Var surfaced as a bare KeyError naming neither.
    """
    with pytest.raises(checker.SpecError, match="state 'nope.here'"):
        checker.check(toy((Let("v", State("nope.here")),)))
    with pytest.raises(checker.SpecError, match="writes state 'no_such'"):
        checker.check(toy((Set("no_such", Const(0.0)),)))
    with pytest.raises(checker.SpecError, match="dial 'ghost'"):
        checker.check(toy((Set("x", Dial("ghost")),)))
    with pytest.raises(checker.SpecError, match="no Let, ForCompanies or"):
        checker.check(toy((Set("x", Var("ghost")),)))
    with pytest.raises(checker.SpecError, match="extern 'ghost'"):
        checker.check(toy((Set("x", Extern("ghost", ())),)))
    # a name each binder introduces resolves
    assert checker.check(toy((Let("v", Dial("gain")), Set("x", Var("v")))))
    assert checker.check(toy((ForCompanies("i", (Set("x", Var("i")),)),)))
    assert checker.check(toy((IfSome("x", "held", (Set("x", Var("held")),)),)))
    # and the shipped mechanism resolves
    checker.resolve_names(JUMPS)


# -- hoisting states its condition -------------------------------------------

def test_hoisting_moves_the_value_when_two_branches_draw():
    """Hoisting preserves the value on the branch taken while at most one
    branch draws. Where both draw, each draw takes its own hoisted name in
    source order, so the second branch reads the first branch's draws.
    """
    u = Draw("uniform", "JumpMarketU", 0)
    body = (Set("x", If(Bin("<", Dial("gain"), Const(0.5)),
                        Bin("+", u, u), u)),)
    hoisted = checker.hoist(body)
    assert [s.name for s in hoisted if isinstance(s, Let)] == [
        "hoisted_uniform_1", "hoisted_uniform_2", "hoisted_uniform_3"]
    # the else branch drew first before hoisting and reads the third name
    # after it, which is the value that moves
    assert hoisted[-1].expr.otherwise == Var("hoisted_uniform_3")
    assert hoisted[-1].expr.then == Bin("+", Var("hoisted_uniform_1"),
                                        Var("hoisted_uniform_2"))
    stream = [0.11, 0.22, 0.33]
    assert _interpret(body, stream, gain=0.9) == (0.11, 1)
    assert _interpret(hoisted, stream, gain=0.9) == (0.33, 3)
    # the then branch, which draws first, keeps its value
    assert _interpret(body, stream, gain=0.1) == (0.33, 2)
    assert _interpret(hoisted, stream, gain=0.1) == (0.33, 3)


def test_hoisting_keeps_the_value_when_one_branch_draws():
    """The shape both shipped mechanisms have, and the one the checker
    proposes the rewrite for."""
    body = (Set("x", If(Bin("<", Dial("gain"), Const(0.5)),
                        Draw("uniform", "JumpMarketU", 0), Const(7.0))),)
    hoisted = checker.hoist(body)
    stream = [0.11, 0.22, 0.33]
    for gain in (0.1, 0.9):
        assert (_interpret(body, stream, gain)[0]
                == _interpret(hoisted, stream, gain)[0])


def _interpret(body, stream, gain):
    """The value written to x and the draws taken, on a fixed stream."""
    pos = [0]
    env = {}

    def ev(e):
        if isinstance(e, Const):
            return e.value
        if isinstance(e, Dial):
            return gain
        if isinstance(e, Var):
            return env[e.name]
        if isinstance(e, Draw):
            pos[0] += 1
            return stream[pos[0] - 1]
        if isinstance(e, Bin):
            a, b = ev(e.left), ev(e.right)
            return {"+": a + b, "*": a * b, "<": a < b}[e.op]
        if isinstance(e, If):
            return ev(e.then) if ev(e.cond) else ev(e.otherwise)
        raise TypeError(e)

    written = None
    for s in body:
        if isinstance(s, Let):
            env[s.name] = ev(s.expr)
        elif isinstance(s, Set):
            written = ev(s.expr)
    return written, pos[0]


# -- the emitter's tree is the specification's tree --------------------------

def test_a_negation_parenthesises_a_compound_operand():
    """`Neg` emitted its operand bare, so `-(gain + offset)` became
    `-gain + offset`, which Rust reads as `(-gain) + offset`. At gain 1.0
    and offset 3.0 the specification means -4.0 and that Rust computes
    2.0.
    """
    dials = (DialSpec("gain", 0.0), DialSpec("offset", 0.0))
    mech = toy((Set("x", Neg(Bin("+", Dial("gain"), Dial("offset")))),),
               dials=dials)
    e = emit.Emitter(mech)
    assert e.body(mech.body, 0) == ["self.x = -(p.gain + p.offset);"]
    # a leaf operand carries no parentheses
    plain = toy((Set("x", Neg(Dial("gain"))),), dials=dials)
    assert emit.Emitter(plain).body(plain.body, 0) == ["self.x = -p.gain;"]
    # nor does a call, which brings its own brackets
    called = toy((Set("x", Neg(Call("sqrt", (Dial("gain"),)))),), dials=dials)
    assert emit.Emitter(called).body(called.body, 0) == [
        "self.x = -mathx::sqrt(p.gain);"]


def test_every_compound_operand_is_parenthesised():
    """`--x` and a bare `if` block beside an operator are both Rust parse
    errors, and both were reachable."""
    mech = toy((Set("x", Neg(Neg(Dial("gain")))),))
    assert emit.Emitter(mech).body(mech.body, 0) == ["self.x = -(-p.gain);"]
    conditional = toy((Set("x", Bin("+", If(Bin("<", Dial("gain"),
                                                Const(0.5)),
                                            Const(1.0), Const(1.0)),
                                    Const(1.0))),))
    text = emit.Emitter(conditional).body(conditional.body, 0)[0]
    assert text.startswith("self.x = (if ")
    assert text.endswith("}) + 1.0;")


def test_the_emitter_refuses_a_mechanism_the_prover_rejected():
    """The gate joining the inertness proof to the Rust that ships.

    `function_body` calls the checker and raises rather than emitting a
    mechanism that is live at its defaults. Without this, a mechanism the
    prover rejected reaches engine.rs.
    """
    live = toy((Set("x", Bin("+", Dial("gain"), Const(1.0))),))
    assert not checker.prove_inert(live).inert
    with pytest.raises(emit.EmitError, match="not proven inert"):
        emit.Emitter(live).function_body()
    # the shipped mechanism passes the same gate
    assert emit.Emitter(JUMPS).function_body()


# -- the digest covers what the emitter reads --------------------------------

def test_the_spec_digest_holds_still_across_a_doc_string_edit():
    """The digest is stamped into the generated header and into the
    `spec` field of every preset record, where a reader reads a change as
    a changed mechanism. A dial's doc string reaches no generated line,
    so editing one used to move every preset record for nothing.
    """
    dials = list(JUMPS.dials)
    dials[0] = dataclasses.replace(dials[0], doc=dials[0].doc + " Edited.")
    edited = dataclasses.replace(JUMPS, dials=tuple(dials))
    assert edited.digest() == JUMPS.digest()
    assert (emit.Emitter(edited).function_body()
            == emit.Emitter(JUMPS).function_body())
    # the mechanism's own doc string is prose too
    assert dataclasses.replace(JUMPS, doc=JUMPS.doc + " Edited.").digest() \
        == JUMPS.digest()


def test_the_spec_digest_covers_a_declared_field_doc_string():
    """A declared field's doc string becomes the doc comment on the
    struct field the emitter generates, so it is covered."""
    state = (StateSpec("fear", scope="engine", rust="self.economy.fear",
                       declared=True, default=0.0, doc="the fear level"),)
    mech = toy((Set("fear", State("fear")),), state=state)
    edited = dataclasses.replace(
        mech, state=(dataclasses.replace(state[0], doc="a different note"),))
    assert edited.digest() != mech.digest()
    assert (emit.state_generation(edited)["struct_fields"]
            != emit.state_generation(mech)["struct_fields"])
    # an undeclared field's doc string reaches nothing
    plain = (StateSpec("x", scope="engine", rust="self.x", doc="a note"),)
    a = toy((Set("x", State("x")),), state=plain)
    b = toy((Set("x", State("x")),),
            state=(dataclasses.replace(plain[0], doc="another"),))
    assert a.digest() == b.digest()
