"""The three figures, drawn once.

The notebook shows them inline. The study repository this came from also
writes them as standalone files for a page that is not a notebook, through
these same functions, so a chart cannot say one thing in the notebook and
another in a published figure.

Each function takes the frozen artifacts and returns a matplotlib figure.
None of them runs a market, calls a model or reads anything but JSON, so a
figure is a view of a recorded result rather than a second computation of
it.

Every figure also carries its own description, in `ALT`, keyed by the same
name. Anything rendering these to a page should put them on the images:
`nbconvert` otherwise writes "No description has been provided for this
image", which is a placeholder rather than a description and is what a
screen reader reads out.
"""

from __future__ import annotations

from typing import Any

#: One colour per arm, everywhere. A reader who learns the pairing on the
#: first figure keeps it on the next two.
COLOURS = {
    "control": "#5A6864",
    "+200bps": "#3C6E8F",
    "vix": "#7A5C2E",
    "cycle": "#5B7A3C",
    "crisis": "#8A3A3A",
}
INK = "#101A18"
GRID = "#8A9895"
ACCENT = "#0E6B65"

#: What each figure shows, for a reader who cannot see it. Written beside
#: the code that draws the figure, so it can be kept true when the figure
#: changes.
ALT = {
    "exposure-bands": (
        "Gross exposure per day for two arms of one experiment. A single "
        "grey line of shared history runs from day 14 to day 19 between "
        "0.74 and 0.79, then splits at the fork. Control climbs and stays "
        "high, between 0.82 and 0.94, ending at 0.91. The crisis arm falls "
        "from 0.77 to 0.57 over the nine days after the fork, holds near "
        "0.58, then drifts back up to 0.66 by day 39. After day 21 the two "
        "lines never meet again."),
    "agent-actions": (
        "Paired bars per day showing the change in gross exposure caused "
        "by the agent's own fills, with prices held still. In the first "
        "week after the fork the crisis bars are large and negative -- "
        "-0.083 on day 22, -0.056 on day 26 -- while control's are mostly "
        "positive. From day 30 the pattern reverses: the crisis bars turn "
        "small and positive while control swings both ways. Totals over "
        "the window: control +0.122, crisis -0.128."),
    "replications": (
        "Slope chart of the live replications. Each run is one line "
        "joining its control value on the left to its paired crisis value "
        "on the right, and every line is drawn in the same colour and "
        "weight, so a run that moved the other way is as visible as the "
        "rest."),
    "decomposition": (
        "Mean gross exposure per replication across five arms of the "
        "earlier rate-and-regime ladder. Crisis is the tightest cluster "
        "and the lowest. The vix and cycle arms scatter widely across each "
        "other and across control, with one vix replication above every "
        "control point."),
}


#: Spelled out to four, because "2 arms" in a title reads like a bug.
_WORDS = {2: "two", 3: "three", 4: "four", 5: "five"}


def _count(n: int) -> str:
    return _WORDS.get(n, str(n))


def _shade_window(ax, first_day: int, last_day: int, label: str) -> None:
    """Mark the days the scenario was firing.

    Without it a reader cannot tell the crisis window from the rest of the
    branch, and on this experiment the window covers all of it -- which is
    itself worth being able to see rather than being told.
    """
    ax.axvspan(first_day - 0.5, last_day + 0.5, color=COLOURS["crisis"],
               alpha=0.07, linewidth=0)
    ax.text(first_day - 0.3, ax.get_ylim()[0], f" {label}",
            color=COLOURS["crisis"], fontsize=9, va="bottom")


def _tidy(ax) -> None:
    ax.grid(alpha=0.25, linewidth=0.7)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def exposure_bands(series: list[dict], bands: dict, shared: list[dict],
                   warmup_days: int, figsize=(9.5, 4.8), dpi=140):
    """The recorded run: one line becomes three at the fork.

    `shared` is the pre-fork daily exposure, which every arm inherits
    identically. Drawing it makes the counterfactual structure visible:
    the arms do not merely start alike, they share a history.
    """
    import matplotlib.pyplot as plt

    names = list(bands)
    days = [i["day"] for i in series]

    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    ax.plot([r["day"] for r in shared], [r["exposure"] for r in shared],
            color=GRID, linewidth=2, marker="o", markersize=3.5,
            label="shared history")
    for name in names:
        ax.plot([warmup_days - 1] + days,
                [shared[-1]["exposure"]]
                + [i[f"exposure_{name}"] for i in series],
                marker="o", markersize=3.5, linewidth=2,
                color=COLOURS[name],
                label=f"{name}  (mean "
                      f"{bands[name]['mean_exposure']:.3f})")
    ax.axvline(warmup_days - 0.5, color=ACCENT, linestyle="--",
               linewidth=1.2)
    ax.text(warmup_days - 0.4, ax.get_ylim()[1], " checkpoint and fork",
            color=ACCENT, fontsize=9, va="top")
    ax.set_xlabel("simulated trading day")
    ax.set_ylabel("gross exposure (x equity)")
    ax.set_title(f"One agent, one checkpoint, {_count(len(names))} arms",
                 loc="left", fontsize=13, color=INK)
    ax.set_xticks(range(min(r["day"] for r in shared), max(days) + 1, 2))
    ax.legend(frameon=False, fontsize=9, loc="lower left")
    _tidy(ax)
    fig.tight_layout()
    return fig


def agent_actions(series: list[dict], bands: dict, figsize=(9.5, 4.2),
                  dpi=140):
    """What the AGENT did to exposure, with the market held still.

    Gross exposure moves for two reasons: the agent trades, and prices move
    under a portfolio nobody touched. After a fork the arms trade different
    markets, so a gap in exposure is not by itself evidence the agent did
    anything. This is the other half: at each decision, exposure
    immediately before the fills and immediately after, at the SAME
    arrival prices. What is left is the agent.

    Bars rather than lines, because each value is one decision and not a
    level that persists.
    """
    import matplotlib.pyplot as plt

    names = list(bands)
    days = [i["day"] for i in series]
    width = 0.8 / max(1, len(names))

    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    for offset, name in enumerate(names):
        xs = [d + (offset - (len(names) - 1) / 2) * width for d in days]
        ax.bar(xs, [i[f"agent_change_{name}"] for i in series],
               width=width, color=COLOURS[name], alpha=0.9,
               label=f"{name}  (total "
                     f"{bands[name]['agent_change_total']:+.3f})")
    ax.axhline(0, color=INK, linewidth=1)
    ax.set_xlabel("simulated trading day")
    ax.set_ylabel("exposure change from the agent's own fills")
    ax.set_title("What the agent did, with the market held still",
                 loc="left", fontsize=13, color=INK)
    ax.set_xticks(range(min(days), max(days) + 1, 2))
    ax.legend(frameon=False, fontsize=9)
    _tidy(ax)
    fig.tight_layout()
    return fig


def _strip(rows: list[dict], names: list[str], per_arm: dict, title: str,
           baseline: float | None, annotate: bool, figsize, dpi):
    """One dot per replication per arm, and the mean as a bar.

    A strip rather than a bar chart with error bars: four replications is
    few enough to show every one, and showing them is what makes an
    overlapping arm look overlapping.
    """
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    for x, name in enumerate(names):
        values = [r["mean_exposure"][name] for r in rows]
        ax.scatter([x] * len(values), values, s=68, zorder=3,
                   color=COLOURS[name], alpha=0.82)
        mean = per_arm[name]["mean_of_means"]
        ax.plot([x - 0.24, x + 0.24], [mean, mean], color=COLOURS[name],
                linewidth=2.5, zorder=2)
        if annotate:
            ax.annotate(f"{mean:.3f}", (x + 0.28, mean), fontsize=9,
                        color=COLOURS[name], va="center")
    if baseline is not None:
        ax.axhline(baseline, color=GRID, linestyle=":", linewidth=1.2,
                   zorder=1)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names)
    ax.set_ylabel("mean gross exposure over the arm")
    ax.set_title(title, loc="left", fontsize=13, color=INK)
    ax.grid(axis="y", alpha=0.25, linewidth=0.7)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    fig.tight_layout()
    return fig


def paired_runs(summary: dict, control: str, treatment: str, *,
                palette: dict | None = None, frame=None,
                figsize=(7.4, 5.2), dpi=140):
    """Every replication as one line, control to its paired treatment.

    The single slope-chart implementation. The notebook draws it with this
    module's palette and the published figure draws it with the site's, and
    a chart a reader meets twice must not be two drawings that can drift.

    A slope chart rather than two columns of dots, because the runs are
    PAIRED. Two columns show the same eight numbers and lose the pairing,
    and the pairing is the only thing that makes "three of four" mean
    anything: what matters is which way each RUN moved, not where the two
    clouds sit.

    Every run is drawn in one colour and one weight. An earlier version
    coloured by direction, which put the run that went the other way in a
    hue of its own and read as the chart calling it an anomaly.
    """
    import matplotlib.pyplot as plt

    colours = palette or {"ink": INK, "line": GRID,
                          "control": COLOURS.get(control, INK),
                          "treatment": COLOURS.get(treatment, ACCENT)}
    rows = sorted(summary["rows"], key=lambda r: r["index"])

    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    lower = 0
    for row in rows:
        a = row["mean_exposure"][control]
        b = row["mean_exposure"][treatment]
        lower += a > b
        ax.plot([0, 1], [a, b], color=colours["ink"], linewidth=1.8,
                alpha=0.55, solid_capstyle="round", zorder=3)
        ax.plot([0], [a], marker="o", markersize=8,
                color=colours["control"], zorder=4)
        ax.plot([1], [b], marker="o", markersize=8,
                color=colours["treatment"], zorder=4)
        ax.annotate(f"run {row['index']}", xy=(0, a), xytext=(-12, 0),
                    textcoords="offset points", ha="right", va="center",
                    fontsize=10.5, color=colours["ink"])
        ax.annotate(f"{a:.3f}", xy=(0, a), xytext=(-58, 0),
                    textcoords="offset points", ha="right", va="center",
                    fontsize=9.5, color=colours["ink"])
        ax.annotate(f"{b:.3f}", xy=(1, b), xytext=(12, 0),
                    textcoords="offset points", ha="left", va="center",
                    fontsize=9.5, color=colours["ink"])

    ax.set_xlim(-0.42, 1.30)
    ax.set_xticks([0, 1])
    ax.set_xticklabels([control, treatment], fontsize=12,
                       color=colours["ink"])
    ax.tick_params(axis="x", length=0, pad=10)
    ax.set_ylabel("gross exposure (x equity)")
    ax.grid(axis="x", visible=False)
    title = f"The result repeated in {lower} of {len(rows)} runs"
    if frame is None:
        ax.set_title(title, loc="left", fontsize=13, color=colours["ink"])
        _tidy(ax)
    else:
        frame(ax, title,
              "each line is one live replication, control to its paired "
              f"{treatment}")
    fig.tight_layout()
    return fig, lower, len(rows)


def replications(summary: dict, figsize=(7.4, 5.2), dpi=140):
    """The replications, paired. See :func:`paired_runs`."""
    names = list(summary["per_arm"])
    fig, _, _ = paired_runs(summary, names[0], names[-1], figsize=figsize,
                            dpi=dpi)
    return fig


def decomposition(summary: dict, figsize=(8.5, 4.2), dpi=140):
    """The same, split into five arms."""
    names = list(summary["arms"])
    return _strip(summary["rows"], names, summary["per_arm"],
                  "Splitting the crisis into its parts",
                  summary["per_arm"]["control"]["mean_of_means"], False,
                  figsize, dpi)


def shared_history(world: Any, warmup_days: int, steps_per_day: int,
                   days_shown: int = 6) -> list[dict]:
    """Daily exposure over the last stretch of the shared history."""
    return [row for row in world.trace
            if row["step_of_day"] == steps_per_day - 1
            and row["day"] >= warmup_days - days_shown]


#: Why these figures are light and experiment 001's adapt to the reader.
#:
#: 001 writes its SVG by hand, so it carries a palette in CSS custom
#: properties and swaps them under `prefers-color-scheme`. These come out
#: of matplotlib, and theming one after the fact means overriding inline
#: style attributes whose structure is matplotlib's business: the figure
#: background and the axes background are separate patches, and text is
#: `<use>` elements pointing at glyph paths rather than `<text>`, so a
#: stylesheet that looks right leaves the labels unreadable. That was
#: tried, and the result was a dark figure with dark text on it.
#:
#: A light figure on a dark page is a white card, which every technical
#: publication already does. An unreadable figure is a defect. So these
#: stay light, and the choice is written down rather than discovered.
LIGHT_ONLY = True
