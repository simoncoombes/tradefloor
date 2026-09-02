"""The market and company jump mechanism, as a specification.

The mechanism ``Engine::apply_jumps`` runs at every close: one uniform
and one normal on the jumps stream for the market, then one of each per
company, taken whatever the dials say, so the stream position never
depends on a parameter. A jump adds to a company's mispricing, clamped,
records the clamped difference in the eighth attribution slot, and moves
the momentum reference by the share herding must not see.

At the defaults every intensity is zero. ``u < 0.0`` is false for every
uniform in ``[0, 1)``, so both jumps are zero, their sum is a decided
zero, and the guarded writes are unreachable: the checker proves the
body inert from that, and the known-answer digest proves the emitted
Rust is the shipped mechanism.
"""
from spec import (Add, Bin, Const, Dial, DialSpec, Draw, Extern, ExternSpec,
                  ForCompanies, If, IfSome, Let, Mechanism, Set, State,
                  StateSpec, Var, When)


def mul(a, b):
    return Bin("*", a, b)


def add(a, b):
    return Bin("+", a, b)


def sub(a, b):
    return Bin("-", a, b)


def div(a, b):
    return Bin("/", a, b)


JUMPS = Mechanism(
    name="jumps",
    stream="jumps",
    doc=__doc__,
    dials=(
        DialSpec("jump_intensity_market", 0.0,
                 "daily probability that the market jump fires"),
        DialSpec("jump_mean_market", 0.0,
                 "mean of the market jump in log-return units"),
        DialSpec("jump_sigma_market", 0.0,
                 "standard deviation of the market jump"),
        DialSpec("jump_intensity_idio", 0.0,
                 "daily probability that a company jump fires"),
        DialSpec("jump_sigma_idio", 0.0,
                 "standard deviation of the company jump"),
        DialSpec("jump_vix_coupling", 0.0,
                 "how much the arrival rate follows the VIX"),
        DialSpec("market_vol_vix_anchor", 15.0,
                 "the VIX at which the rate scale is one; read, not owned. "
                 "This is factor_vol::MARKET_VOL_VIX_ANCHOR, which "
                 "params.rs sets every preset from, and it is declared here "
                 "because prove_inert runs at these defaults: declared at "
                 "20.0 the proof was taken at a dose vector no build has. "
                 "It was harmless, because the ratio it feeds is discarded "
                 "while jump_vix_coupling is zero, and "
                 "test_every_declared_default_is_the_builds pins the whole "
                 "vector so the next one is not."),
        DialSpec("jump_momentum_share", 1.0,
                 "the share of a jump herding is allowed to see"),
    ),
    state=(
        StateSpec("economy.vix", scope="engine", doc="the VIX today",
                  rust="self.economy.vix"),
        StateSpec("stock.mispricing_s", scope="company", optional=True,
                  doc="log deviation from fair value",
                  rust="company.stock.mispricing_s"),
        StateSpec("stock.mispricing_s_prev_close", scope="company",
                  optional=True, doc="the momentum reference",
                  rust="company.stock.mispricing_s_prev_close"),
        StateSpec("attribution[8]", scope="company",
                  doc="the jump slot of the attribution accumulator",
                  rust="self.attribution",
                  add_rust="if let Some(acc) = self.attribution.get_mut({index}) {{\n{indent}    acc[8] += {value};\n{indent}}}"),
    ),
    externs=(
        ExternSpec("clamp_s", "crate::market::tick::clamp_s(&self.params, {0})",
                   "the mispricing cap, min/max spelling"),
    ),
    body=(
        # The regime's effect on the arrival rate. The ratio is a pure
        # value; at coupling zero the branch that uses it is not taken.
        Let("ratio", div(State("economy.vix"), Dial("market_vol_vix_anchor"))),
        Let("rate_scale", If(Bin("==", Dial("jump_vix_coupling"), Const(0.0)),
                             Const(1.0),
                             add(sub(Const(1.0), Dial("jump_vix_coupling")),
                                 mul(mul(Dial("jump_vix_coupling"), Var("ratio")),
                                     Var("ratio"))))),
        Let("intensity_market", If(Bin("==", Dial("jump_vix_coupling"), Const(0.0)),
                                   Dial("jump_intensity_market"),
                                   mul(Dial("jump_intensity_market"), Var("rate_scale")))),
        Let("intensity_idio", If(Bin("==", Dial("jump_vix_coupling"), Const(0.0)),
                                 Dial("jump_intensity_idio"),
                                 mul(Dial("jump_intensity_idio"), Var("rate_scale")))),
        Let("u_market", Draw("uniform", "JumpMarketU", 0)),
        Let("z_market", Draw("normal", "JumpMarketZ", 0)),
        Let("market", If(Bin("<", Var("u_market"), Var("intensity_market")),
                         add(Dial("jump_mean_market"),
                             mul(Dial("jump_sigma_market"), Var("z_market"))),
                         Const(0.0))),
        ForCompanies("index", (
            Let("u", Draw("uniform", "JumpCompanyU", Var("index"))),
            Let("z", Draw("normal", "JumpCompanyZ", Var("index"))),
            Let("idio", If(Bin("<", Var("u"), Var("intensity_idio")),
                           mul(Dial("jump_sigma_idio"), Var("z")),
                           Const(0.0))),
            Let("total", add(Var("market"), Var("idio"))),
            # Guarded rather than added: s + 0.0 is not a no-op on a
            # negative zero, and an inert mechanism leaves state it does
            # not touch bit-identical.
            When(Bin("!=", Var("total"), Const(0.0)), (
                IfSome("stock.mispricing_s", "s", (
                    Let("after", Extern("clamp_s", (add(Var("s"), Var("total")),))),
                    Add("attribution[8]", sub(Var("after"), Var("s"))),
                    Set("stock.mispricing_s", Var("after")),
                    Let("carried", mul(sub(Const(1.0), Dial("jump_momentum_share")),
                                       sub(Var("after"), Var("s")))),
                    When(Bin("!=", Var("carried"), Const(0.0)), (
                        IfSome("stock.mispricing_s_prev_close", "prev", (
                            Set("stock.mispricing_s_prev_close",
                                add(Var("prev"), Var("carried"))),
                        )),
                    )),
                )),
            )),
        )),
    ),
    target={"file": "rust/src/engine.rs", "function": "apply_jumps",
            "rng": "self.jump_rng", "params": "p",
            # The market intensity is read outside the mechanism too, by a
            # caller asking whether a jump can fire. Generated from this
            # specification rather than restated in Rust beside it, so the
            # threshold has one definition and one place to change.
            "accessors": ("intensity_market",)},
)
