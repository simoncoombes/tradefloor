"""The market and company jump mechanism, as a specification.

The mechanism ``Engine::apply_jumps`` runs at every close: one uniform
and one normal on the jumps stream for the market, then one of each per
company, taken whatever the dials say, so the stream position never
depends on a parameter. A jump adds to a company's mispricing, clamped,
records the clamped difference in the eighth attribution slot, and moves
the momentum reference by the share herding must not see.

The company rate is not simply the market's. ``jump_idio_vix_decoupled``
holds it off the VIX while the market's still follows it, and
``jump_idio_excitation`` lifts it with the name's own recent jumps, which
needs a per-company vector the loop carries across days. That vector is
taken out of the engine for the length of the loop and put back after
it, because the loop borrows the company list.

At the defaults every intensity is zero and so is the excitation.
``u < 0.0`` is false for every uniform in ``[0, 1)``, so both jumps are
zero, their sum is a decided zero, the vector is never taken, and the
guarded writes are unreachable: the checker proves the body inert from
that, and the known-answer digest proves the emitted Rust is the shipped
mechanism.
"""
from spec import (Add, Bin, Const, Dial, DialSpec, Draw, Extern, ExternSpec,
                  ForCompanies, If, IfSome, Let, Mechanism, Note, Set, State,
                  StateSpec, Taken, Var, When)


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
        DialSpec("jump_idio_vix_decoupled", 0.0,
                 "nonzero holds the company arrival rate off the VIX "
                 "while the market's still follows it. The tape does not "
                 "support a single-name rate that rises with the index's "
                 "implied vol the way the market-wide one does "
                 "(vix-dynamics.md 19.1), and the two rates shared a "
                 "scale only because they were written together"),
        DialSpec("jump_idio_excitation", 0.0,
                 "how much a company's own jump raises its arrival rate "
                 "for the days after. Zero is a Poisson name, whose jumps "
                 "arrive independently; a positive value is the Hawkes "
                 "self-exciting form, where a name that has just jumped "
                 "is likelier to jump again and the clustering the tape "
                 "shows survives into the simulated series"),
        DialSpec("jump_idio_excitation_decay", 0.0,
                 "the daily factor the excitation is multiplied by, so "
                 "the memory of a jump fades geometrically. It is read "
                 "only while jump_idio_excitation is nonzero"),
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
        DialSpec("jump_mean_compensated", 0.0,
                 "how much of the drift the jump mean carries is given "
                 "back. 0.0 leaves the process uncompensated, which is "
                 "every preset before pt-v18. 1.0 subtracts the "
                 "compensator, intensity times mean, so the jump is a "
                 "martingale: its expectation is zero and every central "
                 "moment, skew included, is untouched"),
    ),
    state=(
        StateSpec("economy.vix", scope="engine", doc="the VIX today",
                  rust="self.economy.vix"),
        StateSpec("vix_anchor", scope="engine",
                  doc="the VIX at which the rate scale is one",
                  rust="self.vix_anchor"),
        StateSpec("stock.mispricing_s", scope="company", optional=True,
                  doc="log deviation from fair value",
                  rust="company.stock.mispricing_s"),
        StateSpec("stock.mispricing_s_prev_close", scope="company",
                  optional=True, doc="the momentum reference",
                  rust="company.stock.mispricing_s_prev_close"),
        StateSpec("jump_excitation[index]", scope="company",
                  doc="the name's own excitation, one slot per company. "
                      "Read and written inside the loop through a local, "
                      "because the loop holds self.companies",
                  rust="excitation[{index}]",
                  take_rust="self.jump_excitation"),
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
        Let("ratio", div(State("economy.vix"), State("vix_anchor"))),
        Let("rate_scale", If(Bin("==", Dial("jump_vix_coupling"), Const(0.0)),
                             Const(1.0),
                             add(sub(Const(1.0), Dial("jump_vix_coupling")),
                                 mul(mul(Dial("jump_vix_coupling"), Var("ratio")),
                                     Var("ratio"))))),
        Let("intensity_market", If(Bin("==", Dial("jump_vix_coupling"), Const(0.0)),
                                   Dial("jump_intensity_market"),
                                   mul(Dial("jump_intensity_market"), Var("rate_scale")))),
        # Emitted into engine.rs, because the two branches below read as a
        # repetition until a reader knows that one of them is deliberate.
        Note("The idiosyncratic rate: scaled by the VIX like the market's unless\n"
             "`jump_idio_vix_decoupled` says the tape does not support that\n"
             "(vix-dynamics.md 19.1), and excited by the name's own recent jumps\n"
             "when `jump_idio_excitation` is set. Both are branches at 0.0."),
        # Three ways to the same dial, chained so each added behaviour is a
        # branch at its own zero: an uncoupled build takes the first, a
        # decoupled one the second, and only a build that asked for the
        # coupling reaches the multiply. Every preset before the dial is
        # bit-identical because it never leaves the first branch.
        Let("intensity_idio", If(Bin("==", Dial("jump_vix_coupling"), Const(0.0)),
                                 Dial("jump_intensity_idio"),
                                 If(Bin("!=", Dial("jump_idio_vix_decoupled"), Const(0.0)),
                                    Dial("jump_intensity_idio"),
                                    mul(Dial("jump_intensity_idio"), Var("rate_scale"))))),
        # Bound once and read three times below, so the switch is one dial
        # read and the guard on each use is a comparison of a local.
        Let("excite", Dial("jump_idio_excitation")),
        Let("excite_decay", Dial("jump_idio_excitation_decay")),
        # The excitation vector is taken for the length of the loop: the
        # loop borrows self.companies mutably and self.jump_excitation
        # cannot be reached through self while it does. Gated on the dial,
        # so a build that does not excite never moves the vector.
        Taken("jump_excitation[index]", "excitation",
              Bin("!=", Var("excite"), Const(0.0)), (
            Let("u_market", Draw("uniform", "JumpMarketU", 0)),
            Let("z_market", Draw("normal", "JumpMarketZ", 0)),
            Let("market", If(Bin("<", Var("u_market"), Var("intensity_market")),
                             add(Dial("jump_mean_market"),
                                 mul(Dial("jump_sigma_market"), Var("z_market"))),
                             Const(0.0))),
            # The compensator. A jump whose mean is negative injects a drift of
            # intensity times mean every day, whether or not it fires, and that
            # drift is a first moment nobody asked the mechanism for: the mean
            # is there for SKEW. Subtracting the compensator makes the jump a
            # martingale, which is the standard compensated-Poisson
            # construction and not a choice of degree. It is a deterministic
            # offset, so it moves the first moment and leaves every central
            # moment alone: the skew and the fat tail the jump exists to
            # produce survive exactly.
            #
            # The intensity is the CONDITIONAL one, already scaled by the VIX
            # coupling above, so the compensator tracks the arrival rate rather
            # than assuming its day-zero value. A branch, so 0.0 is a decided
            # zero and every preset that predates the dial is bit-identical.
            Let("compensator", If(Bin("==", Dial("jump_mean_compensated"), Const(0.0)),
                                  Const(0.0),
                                  mul(Dial("jump_mean_compensated"),
                                      mul(Var("intensity_market"),
                                          Dial("jump_mean_market"))))),
            ForCompanies("index", (
                Let("u", Draw("uniform", "JumpCompanyU", Var("index"))),
                Let("z", Draw("normal", "JumpCompanyZ", Var("index"))),
                # The name's own rate, lifted by its excitation. The uniform
                # is compared against it once and the answer is kept: the
                # jump's size and the excitation it feeds back both need to
                # know whether this name fired, and a second comparison is
                # a second thing to keep in step.
                Let("rate", If(Bin("!=", Var("excite"), Const(0.0)),
                               mul(Var("intensity_idio"),
                                   add(Const(1.0), State("jump_excitation[index]"))),
                               Var("intensity_idio"))),
                Let("jumped", Bin("<", Var("u"), Var("rate"))),
                Let("idio", If(Var("jumped"),
                               mul(Dial("jump_sigma_idio"), Var("z")),
                               Const(0.0))),
                # Guarded, so a build that does not excite leaves every
                # slot of the vector exactly as it found it rather than
                # multiplying it by a decayed zero.
                When(Bin("!=", Var("excite"), Const(0.0)), (
                    Set("jump_excitation[index]",
                        add(mul(Var("excite_decay"), State("jump_excitation[index]")),
                            If(Var("jumped"), Var("excite"), Const(0.0)))),
                )),
                Let("total", sub(add(Var("market"), Var("idio")), Var("compensator"))),
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
