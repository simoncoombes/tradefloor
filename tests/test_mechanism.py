"""The mechanism language (noise, phase 5).

The checker's three rules, the hoisting rewrite, the emitter's contract
and the committed Rust are each stated here. The known-answer digest,
run by the gate, is what proves the emitted jump mechanism is the shipped
one; this file proves the tooling says what it claims.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "mechanism"))
sys.path.insert(0, os.path.join(ROOT, "tools", "mechanism", "mechanisms"))

import check as checker  # noqa: E402
import emit  # noqa: E402
from spec import (Add, Bin, Const, Dial, DialSpec, Draw, If, Let, Mechanism,  # noqa: E402
                  Set, State, StateSpec, Var, When, bits)
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
    inert = toy((Let("z", Draw("normal", "External", 0)),
                 Add("x", Bin("*", Dial("gain"), Var("z")))))
    proof = checker.prove_inert(inert)
    assert proof.inert and proof.writes == ["x: adds a decided zero"]
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
    assert found["proof"].inert
    assert emit.committed(VIX_JUMP_AS_WRITTEN) is None


# -- the emitter ---------------------------------------------------------------

def test_constants_are_pinned_to_their_bits():
    e = emit.Emitter(toy((Set("x", Bin("*", Const(0.15), Dial("gain"))),)))
    text = "\n".join(e.body(e.mech.body, 2))
    assert f"f64::from_bits({bits(0.15)}) /* 0.15 */" in text
    assert bits(0.15) == "0x3FC3333333333333"
    assert emit.literal(0.0) == "0.0" and emit.literal(1.0) == "1.0"


def test_declared_state_generates_the_four_texts():
    mech = toy((Set("fear", Const(0.0)),),
               state=(StateSpec("fear", scope="engine", rust="self.economy.fear",
                                declared=True, default=0.0, doc="the fear level"),))
    texts = emit.state_generation(mech)
    assert "pub fear: f64," in texts["struct_fields"]
    assert "fear: 0.0," in texts["defaults"]
    assert 'set_item("fear", self.inner.self.economy.fear)' in texts["snapshot"] or \
        'set_item("fear"' in texts["snapshot"]
    assert 'get_item("fear")' in texts["restore"]
    assert "def fear(self) -> float" in texts["python"]


def test_the_spec_digest_moves_with_a_default():
    other = Mechanism(**{**JUMPS.__dict__,
                         "dials": tuple(DialSpec(d.name, 0.5 if d.name == "jump_sigma_idio" else d.default, d.doc)
                                        for d in JUMPS.dials)})
    assert other.digest() != JUMPS.digest()
    assert JUMPS.digest() == JUMPS.digest()


# -- the record ------------------------------------------------------------------

def test_the_record_carries_the_mechanism_set_beside_the_fingerprint():
    import tradefloor as tf
    for name in tf.preset_records():
        record = tf.preset_record(name)
        assert record["fingerprint"] == name
        assert record["schema"] == 1
        jumps = record["mechanisms"]["jumps"]
        assert jumps["spec"] == JUMPS.digest()
        assert jumps["stream"] == "jumps"
        coefficients = tf.ModelParams.from_preset(name).to_dict()
        for dial in JUMPS.dials:
            assert jumps["doses"][dial.name] == coefficients.get(dial.name, dial.default)
