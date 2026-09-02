"""The VIX fear-jump mechanism, as written: a finding, not a fix.

``economy/daily.rs`` takes the fear jump's two uniforms only when the
dial ``vix_jump_intensity`` is not zero, and the second only when the
first fires. Both draws sit inside a branch on a dial, on the economy
stream every preset shares. The checker rejects this specification: the
draw effect is zero on one branch and one or two uniforms on the other,
so the economy stream's position depends on the dial.

The checker's fix, hoisting, would take both uniforms at every close for
every preset. That moves every later draw on the economy stream and with
it every shipped preset's trajectory, which nothing in this work may do.
So this mechanism is recorded as it is, the rejection is pinned by a
test, and the rewrite is not applied. Re-expressing it is an era
boundary, to be taken on purpose or not at all.
"""
from spec import (Add, Bin, Call, Const, Dial, DialSpec, Draw, If, Let,
                  Mechanism, Neg, StateSpec, Var)


VIX_JUMP_AS_WRITTEN = Mechanism(
    name="vix_jump",
    stream="economy",
    doc=__doc__,
    dials=(
        DialSpec("vix_jump_intensity", 0.0, "fear jumps per year; zero takes no draw"),
        DialSpec("vix_jump_scale", 0.0, "mean size of a fear jump, VIX points"),
    ),
    state=(
        StateSpec("economy.vix", scope="engine", doc="the VIX",
                  rust="new_state.vix"),
    ),
    externs=(),
    body=(
        # As written in daily.rs: the outer branch is on the dial and the
        # inner branch draws again only when the first uniform fires.
        Let("fear_jump", If(
            Bin("!=", Dial("vix_jump_intensity"), Const(0.0)),
            If(Bin("<", Draw("uniform", "EconomyDaily", 0),
                   Bin("/", Dial("vix_jump_intensity"), Const(252.0))),
               Bin("*", Dial("vix_jump_scale"),
                   Neg(Call("log", (Call("max", (Draw("uniform", "EconomyDaily", 0),
                                                 Const(1e-12))),)))),
               Const(0.0)),
            Const(0.0))),
        # The day adds the jump to the VIX inside its clamp; the clamp and
        # the rest of the chain are not part of this specification.
        Add("economy.vix", Var("fear_jump")),
    ),
    target={"file": "rust/src/economy/daily.rs", "function": "update_economy_daily"},
)
