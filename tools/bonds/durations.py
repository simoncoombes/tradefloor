"""Where the rate indices' durations and convexities come from.

Modified duration and convexity (years squared) of par bonds paying
semiannual coupons, and of the maturity mix `IGCORP` stands for, computed
directly from the cash flows. `rust/src/rates.rs` quotes these numbers.

    python tools/bonds/durations.py
"""

from __future__ import annotations

import argparse


def par_bond(maturity: float, y: float, freq: int = 2) -> tuple[float, float, float]:
    """(price, modified duration, convexity) of a par bond at yield ``y``."""
    n = int(round(maturity * freq))
    price = duration = convexity = 0.0
    for k in range(1, n + 1):
        t = k / freq
        cash = y / freq + (1.0 if k == n else 0.0)
        discount = (1 + y / freq) ** (-freq * t)
        price += cash * discount
        duration += t * cash * discount
        convexity += cash * t * (t + 1 / freq) * discount
    duration = duration / price / (1 + y / freq)
    convexity = convexity / price / (1 + y / freq) ** 2
    return price, duration, convexity


def main() -> None:
    argparse.ArgumentParser(description=__doc__.split("\n\n")[0]).parse_args()
    for maturity in (2, 8.5, 10):
        for y in (0.025, 0.032, 0.04):
            _, d, c = par_bond(maturity, y)
            print(f"{maturity:4}y par bond at {y:.1%}: D {d:.3f}  C {c:.2f}")
    mix = {3: 0.40, 7: 0.30, 20: 0.15, 30: 0.15}
    d = sum(w * par_bond(t, 0.05)[1] for t, w in mix.items())
    c = sum(w * par_bond(t, 0.05)[2] for t, w in mix.items())
    print(f"40/30/15/15 mix of 3, 7, 20 and 30-year par bonds at 5%: D {d:.2f}  C {c:.1f}")
    for name, (dur, conv) in {"UST2Y": (1.9, 4.6), "UST10Y": (8.5, 84.0),
                              "IGCORP": (7.0, 100.0)}.items():
        print(f"{name}: +200bp prices at {(-dur * 0.02 + 0.5 * conv * 0.02 ** 2):+.2%}")


if __name__ == "__main__":
    main()
