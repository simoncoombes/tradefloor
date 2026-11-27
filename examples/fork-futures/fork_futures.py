"""One market forked into a control and three packaged scenarios.

Run it:

    python examples/fork-futures/fork_futures.py

It runs one seeded market for forty days with a buy-and-hold portfolio in
it, forks that world into four branches, applies a packaged scenario to
three of them on the first day after the fork, runs all four for eighty
more days, and draws the four equity curves as an SVG.

The four branches share the first forty days to the bit, which
`tf.agree` checks before any scenario is applied. So the gap between two
curves after the fork is the scenario and nothing else.

The figure is written twice, once for a light page and once for a dark
one, by this file and the standard library alone. The same seed gives
the same two files byte for byte. `figure/` holds the committed copy the
project README shows. A run writes to `artifacts/`, which is git-ignored,
and `--out figure` regenerates the committed copy.

It takes seven to twelve seconds of CPU and needs no keys and no network.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from xml.sax.saxutils import escape

import tradefloor as tf

SEED = 7
UNIVERSE_SIZE = 20
UNIVERSE_SEED = 11
SHARED_DAYS = 40
BRANCH_DAYS = 80

#: The three packaged scenarios, by the names `tf.Scenario.available()`
#: lists. The control branch gets none.
SCENARIOS = ("rate_shock", "recession", "liquidity_crisis")
BRANCHES = ("control", *SCENARIOS)

DEFAULT_OUT = Path(__file__).resolve().parent / "artifacts"

#: Two palettes, one per page background, on a transparent canvas. The
#: three scenario hues keep an OKLab distance of 12 or more from each
#: other under simulated deuteranopia and protanopia in both themes. On
#: the light page two of them are under 3:1 against white, so every line
#: also ends in a text label and the script prints the table it plots.
THEMES = {
    "light": {
        "text": "#1f2328", "muted": "#59636e", "grid": "#d1d9e0",
        "control": "#59636e", "rate_shock": "#257de0",
        "recession": "#f47230", "liquidity_crisis": "#00b894",
    },
    "dark": {
        "text": "#f0f6fc", "muted": "#9198a1", "grid": "#3d444d",
        "control": "#9198a1", "rate_shock": "#338ced",
        "recession": "#e76624", "liquidity_crisis": "#00a888",
    },
}

WIDTH, HEIGHT = 900, 440
LEFT, RIGHT, TOP, BOTTOM = 56, 196, 88, 92
FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,"
        "sans-serif")


# ---------------------------------------------------------------------------
# The experiment
# ---------------------------------------------------------------------------

def run() -> dict:
    """The shared history and the four branches, as index levels.

    A level is the portfolio's net worth at a day's close, with the
    starting capital as 100.
    """
    universe = tf.Universe.random(UNIVERSE_SIZE, seed=UNIVERSE_SEED)
    world = tf.World(seed=SEED, universe=list(universe),
                     agent=tf.baselines.BuyAndHold(),
                     label="shared history")
    capital = world.net_worth()

    shared = [100.0]
    for _ in range(SHARED_DAYS):
        world.run(days=1)
        shared.append(world.net_worth() / capital * 100.0)

    arms = dict(zip(BRANCHES, world.fork(*BRANCHES), strict=True))
    control = arms["control"]

    # Taken before any scenario is applied: each branch against the
    # control, nine checks a pair.
    agreements = {name: tf.agree(control, arms[name]) for name in SCENARIOS}

    # `at=0` moves a file's first event to the first day after the fork
    # and keeps the gaps between its events. The packaged files first
    # fire on day 50, which would leave fifty flat days on this chart.
    fingerprints = {}
    for name in SCENARIOS:
        scenario = tf.Scenario.load(name)
        fingerprints[name] = scenario.fingerprint
        arms[name].apply(scenario, at=0)

    curves: dict[str, list[float]] = {name: [] for name in BRANCHES}
    for _ in range(BRANCH_DAYS):
        for name in BRANCHES:
            arms[name].run(days=1)
            curves[name].append(arms[name].net_worth() / capital * 100.0)

    return {
        "version": tf.__version__,
        "preset": tf.ModelParams.from_preset().fingerprint,
        "seed": SEED,
        "universe_fingerprint": universe.fingerprint,
        "capital": capital,
        "shared": shared,
        "branches": curves,
        "identical_at_fork": {name: bool(agreement.identical)
                              for name, agreement in agreements.items()},
        "scenario_fingerprints": fingerprints,
    }


# ---------------------------------------------------------------------------
# The figure. Nothing below decides anything about the market.
# ---------------------------------------------------------------------------

def _bounds(result: dict) -> tuple[int, int]:
    """The y axis, widened to multiples of ten."""
    levels = list(result["shared"])
    for curve in result["branches"].values():
        levels.extend(curve)
    low = int(min(levels) // 10) * 10
    high = int(-(-max(levels) // 10)) * 10
    return low, high


def _spread(targets: list[float], gap: float) -> list[float]:
    """Label positions at least `gap` apart, in the order given.

    `targets` is sorted from the top of the chart down. A label that
    would overlap the one above it moves down.
    """
    placed: list[float] = []
    for target in targets:
        if placed and target < placed[-1] + gap:
            target = placed[-1] + gap
        placed.append(target)
    return placed


def svg(result: dict, theme: str) -> str:
    """The figure as SVG text, for a `light` or a `dark` page."""
    ink = THEMES[theme]
    days = SHARED_DAYS + BRANCH_DAYS
    low, high = _bounds(result)
    plot_w = WIDTH - LEFT - RIGHT
    plot_h = HEIGHT - TOP - BOTTOM

    def x(day: float) -> float:
        return LEFT + plot_w * day / days

    def y(level: float) -> float:
        return TOP + plot_h * (high - level) / (high - low)

    def path(points: list[tuple[float, float]]) -> str:
        return " ".join(f"{'M' if i == 0 else 'L'}{x(d):.1f},{y(v):.1f}"
                        for i, (d, v) in enumerate(points))

    title = ("A buy-and-hold portfolio in one simulated market, forked "
             "into four futures")
    described = (
        f"Forty days of shared history, then four branches run for "
        f"eighty more days. Final index levels, starting capital = 100: "
        + ", ".join(f"{name} {result['branches'][name][-1]:.1f}"
                    for name in BRANCHES) + ".")
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" '
        f'height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" '
        f'aria-labelledby="t d" font-family="{FONT}">',
        f'<title id="t">{escape(title)}</title>',
        f'<desc id="d">{escape(described)}</desc>',
        f'<text x="{LEFT}" y="26" font-size="16" font-weight="600" '
        f'fill="{ink["text"]}">{escape(title)}</text>',
        f'<text x="{LEFT}" y="48" font-size="12" fill="{ink["muted"]}">'
        f'Portfolio value at each close, starting capital = 100</text>',
    ]

    # Grid and y axis.
    for level in range(low, high + 1, 10):
        out.append(
            f'<line x1="{LEFT}" y1="{y(level):.1f}" x2="{LEFT + plot_w}" '
            f'y2="{y(level):.1f}" stroke="{ink["grid"]}" '
            f'stroke-width="1"/>')
        out.append(
            f'<text x="{LEFT - 8}" y="{y(level) + 4:.1f}" font-size="12" '
            f'text-anchor="end" fill="{ink["muted"]}">{level}</text>')

    # X axis.
    for day in range(0, days + 1, 20):
        out.append(
            f'<text x="{x(day):.1f}" y="{TOP + plot_h + 18}" '
            f'font-size="12" text-anchor="middle" fill="{ink["muted"]}">'
            f'{day}</text>')
    out.append(
        f'<text x="{LEFT + plot_w / 2:.1f}" y="{TOP + plot_h + 38}" '
        f'font-size="12" text-anchor="middle" fill="{ink["muted"]}">'
        f'Trading day</text>')

    # The fork.
    out.append(
        f'<line x1="{x(SHARED_DAYS):.1f}" y1="{TOP - 22}" '
        f'x2="{x(SHARED_DAYS):.1f}" y2="{TOP + plot_h}" '
        f'stroke="{ink["muted"]}" stroke-width="1" '
        f'stroke-dasharray="4 4"/>')
    out.append(
        f'<text x="{x(SHARED_DAYS) - 8:.1f}" y="{TOP - 10}" font-size="12" '
        f'text-anchor="end" fill="{ink["muted"]}">shared history</text>')
    out.append(
        f'<text x="{x(SHARED_DAYS) + 8:.1f}" y="{TOP - 10}" font-size="12" '
        f'fill="{ink["muted"]}">fork on day {SHARED_DAYS}</text>')

    # The lines. Scenarios first, so the control is drawn over them.
    fork_point = (SHARED_DAYS, result["shared"][-1])
    for name in (*SCENARIOS, "control"):
        points = [fork_point] + [
            (SHARED_DAYS + 1 + i, level)
            for i, level in enumerate(result["branches"][name])]
        out.append(
            f'<path d="{path(points)}" fill="none" stroke="{ink[name]}" '
            f'stroke-width="2" stroke-linejoin="round" '
            f'stroke-linecap="round"/>')
    out.append(
        f'<path d="{path(list(enumerate(result["shared"])))}" fill="none" '
        f'stroke="{ink["control"]}" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>')

    # One label per line, at its right-hand end.
    ends = sorted(BRANCHES, key=lambda n: -result["branches"][n][-1])
    spots = _spread([y(result["branches"][n][-1]) for n in ends], 20.0)
    label_x = LEFT + plot_w + 10
    for name, spot in zip(ends, spots, strict=True):
        level = result["branches"][name][-1]
        out.append(
            f'<line x1="{label_x}" y1="{spot:.1f}" x2="{label_x + 16}" '
            f'y2="{spot:.1f}" stroke="{ink[name]}" stroke-width="3" '
            f'stroke-linecap="round"/>')
        out.append(
            f'<text x="{label_x + 24}" y="{spot + 4:.1f}" font-size="13" '
            f'fill="{ink["text"]}">{name}'
            f'<tspan fill="{ink["muted"]}" dx="6">{level:.1f}</tspan>'
            f'</text>')

    caption = (
        f"tradefloor {result['version']}, preset {result['preset']}, "
        f"seed {result['seed']}, {UNIVERSE_SIZE} companies from "
        f"Universe.random({UNIVERSE_SIZE}, seed={UNIVERSE_SEED}).")
    caption_2 = (
        "Each scenario is a packaged file applied to one branch on the "
        "first day after the fork. Simulated prices, example "
        "assumptions.")
    out.append(
        f'<text x="{LEFT}" y="{HEIGHT - 30}" font-size="12" '
        f'fill="{ink["muted"]}">{escape(caption)}</text>')
    out.append(
        f'<text x="{LEFT}" y="{HEIGHT - 12}" font-size="12" '
        f'fill="{ink["muted"]}">{escape(caption_2)}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def table(result: dict) -> str:
    """The plotted levels, every tenth day and the last."""
    lines = [f"  {'day':>4}" + "".join(f"{name:>18}" for name in BRANCHES)]
    for day in range(0, SHARED_DAYS + 1, 10):
        level = result["shared"][day]
        lines.append(f"  {day:>4}" + f"{level:>18.2f}" * len(BRANCHES))
    for step in range(9, BRANCH_DAYS, 10):
        day = SHARED_DAYS + 1 + step
        lines.append(f"  {day:>4}" + "".join(
            f"{result['branches'][name][step]:>18.2f}" for name in BRANCHES))
    return "\n".join(lines)


def main(out: Path = DEFAULT_OUT) -> dict:
    started = time.time()
    result = run()

    print(f"  library   {result['version']}")
    print(f"  preset    {result['preset']}")
    print(f"  seed      {result['seed']}")
    print(f"  universe  {result['universe_fingerprint']}")
    print(f"  capital   ${result['capital']:,.0f}, buy-and-hold")
    print()
    for name in SCENARIOS:
        same = "yes" if result["identical_at_fork"][name] else "NO"
        print(f"  {name:<18}identical to control at the fork: {same}")
        print(f"  {'':<18}{result['scenario_fingerprints'][name]}")
    print()
    print("  INDEX LEVEL AT THE CLOSE, STARTING CAPITAL = 100")
    print(table(result))
    print()
    print("  CHANGE SINCE THE FORK")
    at_fork = result["shared"][-1]
    for name in BRANCHES:
        final = result["branches"][name][-1]
        print(f"  {name:<18}{(final / at_fork - 1.0) * 100.0:>+8.2f}%")

    out.mkdir(parents=True, exist_ok=True)
    written = []
    for theme in THEMES:
        target = out / f"fork-futures-{theme}.svg"
        target.write_text(svg(result, theme), encoding="ascii",
                          newline="\n")
        written.append(target)
    print()
    for target in written:
        print(f"  wrote {target.name}")
    print(f"\nfinished in {time.time() - started:.1f}s")

    result["artifacts"] = [str(target) for target in written]
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="directory the two SVG files are written to")
    main(out=parser.parse_args().out)
