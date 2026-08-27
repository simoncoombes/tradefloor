"""Build the published documentation site from the design bundle.

The design ships as one self-contained HTML file: a React app with every
route's markup inlined, addressed by hash (`#/mcp`). That is a good reading
experience and a bad indexing one, because everything after the `#` never
reaches a server and a crawler sees a single page whose body says "This page
requires JavaScript to display."

So this script emits two things from the one bundle:

1. `index.html`, the app itself, with the head a crawler actually reads
   filled in (description, Open Graph, canonical, JSON-LD) and a `<noscript>`
   index linking every route.
2. One static page per route, carrying that route's real prose with no
   JavaScript required. These are the URLs that get indexed, and they are
   what the README and any external link should point at.

Both carry the analytics snippet when a measurement ID is configured.

Run it after dropping a new design revision at DESIGN_BUNDLE:

    python tools/docs/build_site.py

Everything under docs/ that this script owns is overwritten. Files it does
not own (envelope.json, the markdown prose) are left alone.
"""

from __future__ import annotations

import html
import json
import pathlib
import re
import sys

import newpages
import presets
import seo
import upgrades

ROOT = pathlib.Path(__file__).resolve().parents[2]
DESIGN_BUNDLE = ROOT / "tools" / "docs" / "design-bundle.html"
OUT = ROOT / "docs"

#: Absolute base for canonical links, Open Graph URLs and the sitemap. These
#: three all need an absolute URL to work at all, so this cannot be relative.
BASE_URL = "https://simoncoombes.github.io/pretium"

#: GA4 measurement ID, the "G-XXXXXXXXXX" from the property's web data
#: stream. Empty means no analytics: the snippet is omitted entirely rather
#: than emitted with a placeholder, because a gtag call with a bad ID fails
#: silently and looks like working analytics that reports nothing.
GA_MEASUREMENT_ID = "G-1LBM3239ZF"

#: Read from pyproject so the site cannot drift from the package. Only two
#: places in the bundle should follow the version: the nav badge and the
#: BibTeX block. The release-notes headings and the "measured on pretium
#: 0.1.0" provenance lines are history and must stay where they are.
def _project_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        sys.exit("no version in pyproject.toml")
    return m.group(1)


VERSION = _project_version()

SITE_NAME = "pretium docs"
TAGLINE = "repeatable market simulation"
REPO_URL = "https://github.com/simoncoombes/pretium"

#: Tab icon. The SVG is the real one; the .ico exists because browsers ask
#: for /favicon.ico by default whether or not a page links to it, and that
#: request 404s otherwise. Both live at the site root, which is also where
#: every static page sits, so these paths are relative and need no base.
FAVICON_LINKS = (
    '<link rel="icon" type="image/svg+xml" href="favicon.svg">\n'
    '<link rel="alternate icon" href="favicon.ico" sizes="16x16 32x32 48x48">\n'
    '<link rel="apple-touch-icon" href="apple-touch-icon.png">'
)

#: Google Fonts covering the two families the design system asks for. The
#: bundle inlines them as woff2 blobs that only its own loader can resolve,
#: so the static pages fetch them the ordinary way and fall back to system
#: stacks if the request fails.
FONT_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2'
    "?family=Public+Sans:ital,wght@0,400;0,600;0,650;1,400"
    "&family=Spline+Sans+Mono:wght@400;600"
    '&display=swap">'
)


#: House style is no em or en dashes in prose. The design bundle arrives with
#: a handful in visible text; they are corrected here rather than in the
#: bundle so a fresh design revision gets the same treatment automatically.
#: Dashes inside the bundler's own JavaScript comments are left alone.
#: The bundle's OUTER <head>, which `doc` does not contain: `doc` is the
#: template payload carried inside a script tag, and this <title> sits above
#: it at design-bundle.html line 5. The PROSE_FIXES pair below corrects the
#: in-app helmet title of the same name and never touched this one, so the
#: published home page kept an em dash in the only title a crawler reads.
#: Guarded, for the same reason PROSE_FIXES now is.
BUNDLE_HEAD_FIXES = [
    ("<title>pretium docs — repeatable market simulation</title>",
     "<title>pretium docs: repeatable market simulation</title>"),
]

PROSE_FIXES = [
    ("pretium docs — repeatable market simulation", "pretium docs: repeatable market simulation"),
    ("MIXED — SEE NOTE", "MIXED, SEE NOTE"),
]

#: The bundle was authored in the pt-v3 era, when the panel had ten
#: statistics and the tick decomposition had seven components. Both grew:
#: fourteen statistics since 2026-08-25, nine components since the jump and
#: circuit-breaker columns were added, and the default preset moved twice on
#: 2026-08-26: to pt-v10, and then to pt-v12, which is what 0.3.0 ships. The
#: second of those boundaries also took the gap list from six named gaps to
#: five, because pt-v12 holds the volume-change row at both horizons. The
#: published site is generated from this bundle rather than from the markdown
#: in docs/, so a stale claim here is the claim users actually read.
#:
#: These assert. A bundle revision that rewords any of them fails the build
#: rather than quietly restoring a number that is no longer true.
ERA_FIXES = [
    (
        "It measures ten statistics against real markets and gets nine of "
        "them right over a year. The ten results are published, along with "
        "six things it gets wrong and what each one rules out.",
        "It measures fourteen statistics against real markets and gets all "
        "fourteen right over a year. The fourteen results are published, "
        "along with five things it gets wrong and what each one rules out.",
    ),
    (
        "pretium publishes what it gets right and what it gets wrong: ten "
        "statistics measured against real markets, how long those results "
        "hold for, the roster they were measured on, and six known gaps.",
        "pretium publishes what it gets right and what it gets wrong: "
        "fourteen statistics measured against real markets, how long those "
        "results hold for, the roster they were measured on, and five known "
        "gaps.",
    ),
    (
        "Ten statistics measured against real markets, nine of them in band "
        "over a year, and six known gaps with what each one rules out.",
        "Fourteen statistics measured against real markets, all fourteen in "
        "band over a year, and five known gaps with what each one rules out.",
    ),
    (
        "Measured on the shipped preset, eight of the ten statistics have "
        "their 10th-to-90th percentile range across seeds crossing a band "
        "edge.",
        "Measured on pt-v10 over thirty seeds, nine of the fourteen "
        "statistics have their 10th-to-90th percentile range across seeds "
        "crossing a band edge.",
    ),
    (
        '<code style="font-size:13px">abs_return_acf1</code> reads a median '
        "of 0.141 against a ceiling of 0.22, with a p90 of 0.426 and an "
        "across-seed standard deviation of 0.170, larger than the median "
        "itself.",
        '<code style="font-size:13px">abs_return_acf1</code> read a median '
        "of 0.0994 against a ceiling of 0.22, with a p90 of 0.4063 and an "
        "across-seed standard deviation of 0.1467, larger than the median "
        "itself. The shipped pt-v12 moves that median to 0.1107 and the "
        "spread has not been re-measured, so read the dispersion figures as "
        "the previous era's: the shape of the warning is what carries over, "
        "not the third decimal.",
    ),
    (
        'So "9/10 in band" describes the middle of thirty runs. Your one run '
        "can easily look worse. Read the spread before relying on one seed:",
        'So "14/14 in band" describes the middle of thirty runs, and the word '
        "certified describes that median rather than your run. It does not "
        "promise a typical single seed is in band on all fourteen; the spread "
        "below says most are not. Read it before relying on one seed:",
    ),
    (
        "eight of the ten statistics have their middle-eighty-percent range "
        "crossing a band edge",
        "nine of the fourteen statistics had their middle-eighty-percent "
        "range crossing a band edge when that spread was last measured, on "
        "pt-v10",
    ),
    (
        'is a preset name or a ModelParams; the default is pt-v3.',
        'is a preset name or a ModelParams; the default is pt-v12.',
    ),
    # The same correction as the one below, on the Changing the Model page's
    # copy of the pt-v3 row. "The 2026-08-26 era boundary" is not usable as a
    # marker here at all: 0.2.0 (pt-v3 -> pt-v10) and 0.3.0 (pt-v10 -> pt-v12)
    # were both cut that day, so naming the release is the only unambiguous
    # form.
    (
        "The current default, and the one the realism numbers describe.",
        "The default until pt-v10 replaced it at 0.2.0, kept selectable so "
        "work published against it keeps reproducing.",
    ),
    # pt-v3 did NOT hand the default over at the 2026-08-26 boundary. That
    # boundary names the pt-v10 -> pt-v12 move; pt-v3 was replaced by pt-v10
    # an era earlier, at 0.2.0 (CHANGELOG.md, "the default moved from
    # `pt-v3` to `pt-v10`"). No date for that move is recorded, so this
    # names the release rather than inventing one, matching the wording
    # docs/model-presets.md settled on.
    (
        "The current default, produced the same way. This is the preset the "
        "realism numbers describe: nine of ten statistics in band at 252 "
        "days. Herding is turned well down (momentum_theta 0.0742).",
        "The default until pt-v10 replaced it at 0.2.0, produced the same "
        "way, and the preset the realism numbers described then: nine of the "
        "ten statistics measured at the time were in band at 252 days. "
        "Herding is turned well down (momentum_theta 0.0742).",
    ),
    # The presets page's own count of itself. "Three presets ship" was true
    # when the bundle was authored and the table beneath it now runs to ten,
    # so the sentence contradicted the rows immediately under it.
    (
        '<code style="font-size:13px">pt-v3</code> is the current default; '
        '<code style="font-size:13px">pt-v1</code> and '
        '<code style="font-size:13px">pt-v2</code> stay selectable and '
        "bit-reproducing forever.",
        '<code style="font-size:13px">pt-v12</code> is the current default, '
        'and every earlier preset from <code style="font-size:13px">pt-v1'
        '</code> to <code style="font-size:13px">pt-v11</code> stays '
        "selectable and reproduces bit for bit. What that guarantee covers is "
        "the PRESET: naming one pins the coefficient set and the market it "
        "produces on a given package version, and a run is only fully "
        "specified by (package version, model, universe fingerprint, seed). "
        'See <a href="#/install">Install and Versioning</a> for what a '
        "package-version change can and cannot move.",
    ),
    (
        "Three presets ship. All three stay selectable and bit-reproducing "
        "forever.",
        "Twelve presets ship. pt-v12 is the default; the other eleven are "
        "selectable by name and none of them has ever been un-shipped.",
    ),
    (
        "Built for long horizons. It halves the dual-horizon objective",
        "Built to INVESTIGATE long-horizon realism, which is not the same as "
        "being certified for long-horizon strategy studies: nothing in this "
        "project is certified past 252 days. It halves the dual-horizon "
        "objective",
    ),
    # The volume row is in band at one year since 0.2.0, so the calibration
    # example can no longer call it unreachable. Gap 1 on the realism page
    # withdrew that claim and this sentence had not caught up.
    (
        "The structurally unreachable statistic rides along in every result "
        "as a standing falsification verdict, and stays out of the loss, "
        "because an optimiser pointed at an unreachable target distorts every "
        "other parameter chasing it.",
        "The statistics outside the objective ride along in every result as a "
        "standing falsification verdict, and stay out of the loss, because an "
        "optimiser is only allowed to chase what the project decided it may "
        "chase. That is a policy about the search, not a claim that the rows "
        "are unreachable: the volume-change row was called unreachable until "
        "0.2.0, pt-v10 held it at one year, and pt-v12 holds it at two -- the "
        "gap that named it is retired.",
    ),
    (
        'eng = pt.Engine(seed=42, universe=u, model="pt-v3")  # the default, spelled out',
        'eng = pt.Engine(seed=42, universe=u, model="pt-v12")  # the default, spelled out',
    ),
    (
        'eng = pt.Engine(seed=42, universe=u)                  # pt-v3, the default\n'
        'eng = pt.Engine(seed=42, universe=u, model="pt-v3")   # the same, spelled out',
        'eng = pt.Engine(seed=42, universe=u)                  # pt-v12, the default\n'
        'eng = pt.Engine(seed=42, universe=u, model="pt-v12")  # the same, spelled out',
    ),
    (
        '<h2 style="font-size:21px;margin:46px 0 10px">Build Targets</h2>',
        '<h2 style="font-size:21px;margin:46px 0 10px">Using it from Rust</h2>\n'
        '        <p style="color:var(--mut);font-size:14px">The engine is a '
        'Rust crate and is published on crates.io as '
        '<code style="font-size:12.5px">pretium</code>, the same code the '
        'Python wheels are built from.</p>\n'
        '        <pre style="padding:13px 16px;overflow-x:auto"><code '
        'data-lang="sh" style="font:400 13px/1.7 var(--font-mono);'
        'color:var(--codefg)">cargo add pretium</code></pre>\n'
        '        <p style="color:var(--mut);font-size:14px">Docs at '
        '<a href="https://docs.rs/pretium">docs.rs/pretium</a>. The crate '
        'carries the engine, the presets and the determinism guarantees; the '
        'Python package adds the harness, the evaluation layer and the '
        'notebooks.</p>\n'
        '        <h2 style="font-size:21px;margin:46px 0 10px">Build Targets</h2>',
    ),
    # The count leaked into five more places than the heading. A page that
    # says nine components beside a schema row promising seven is a reader's
    # bug report, and the two extra columns are the ones that make the
    # decomposition reconstruct a jump day or a halted day at all.
    (
        "What moved a price, broken into seven contributions that sum to the "
        "move.",
        "What moved a price, broken into nine contributions that sum to the "
        "move.",
    ),
    (
        "The answer key. Three levels plus the seven factor contributions.",
        "The answer key. Three levels plus the nine factor contributions.",
    ),
    (
        "One row per instrument per tick: fundamental value, anchor price, "
        "mispricing, and seven factor contributions that sum to the move.",
        "One row per instrument per tick: fundamental value, anchor price, "
        "mispricing, and nine factor contributions that sum to the move.",
    ),
    (
        '        return "momentum"        # one of the seven factor names',
        '        return "momentum"        # one of the nine factor names',
    ),
    # The snapshot's RNG block grew with the engine's stream count and this
    # row did not. A reader counting the numbers in a real snapshot found
    # twenty-one under a sentence promising nine.
    #
    # The enumeration has to end where the engine ends, because this row's
    # whole job is teaching a reader to read the era off the length: stopping
    # at eighteen tells them eighteen is current. It is twenty-one.
    # `rust/src/python_engine.rs:1818` accepts `9 | 12 | 15 | 18 | 21` and
    # line 1850 reads `volume_idio` from offset 18, and a live
    # `Engine.state_snapshot()["rng"]` on 0.3.0 is 21 long.
    (
        "Nine numbers: state, increment and Box-Muller spare for each of the "
        "three streams. A pre-split snapshot carrying three is refused on "
        "restore with its era named, because it froze a single-stream market "
        "this version cannot continue bit-exactly.",
        "Three numbers per generator stream: state, increment and Box-Muller "
        "spare. The count grows as the engine gains streams, and restore "
        "reads the era off the LENGTH rather than a version field: nine for "
        "the original market, economy and external split, then twelve, "
        "fifteen, eighteen and twenty-one as jumps, common volume, news and "
        "per-name volume arrived. Twenty-one is where it stands on 0.3.0, "
        "seven streams of three. A shorter "
        "snapshot restores the streams it carries and leaves the rest where "
        "the fresh engine put them, so a checkpoint written before a "
        "mechanism existed replays as it did then. A pre-split snapshot "
        "carrying three is refused with its era named, because it froze a "
        "single-stream market this version cannot continue bit-exactly.",
    ),
    # The exactness of a resumed run was stated without its one exception,
    # which was live from pt-v4 to 0.2.0. Measured: under 0.1.4 a pt-v4
    # snapshot restored and re-run does NOT reproduce the uninterrupted run,
    # while pt-v1 and pt-v3 do; under 0.2.0 all three do.
    (
        'So: <code style="font-size:13px">branch</code> when the experiment '
        'happens now, <code style="font-size:13px">Checkpoint</code> when '
        "someone else needs to start where you started.</p>",
        'So: <code style="font-size:13px">branch</code> when the experiment '
        'happens now, <code style="font-size:13px">Checkpoint</code> when '
        "someone else needs to start where you started.</p>\n        "
        '<p style="color:var(--mut);font-size:14px">One historical exception '
        "to the exactness, because it was live for five releases. Snapshots "
        "did not carry the engine's log-volume state until 0.2.0, so a "
        "restored engine could trade different volume and print different "
        "prices. That state is inert on pt-v1 to pt-v3 and live from pt-v4, "
        "and the divergence is reproducible: on 0.1.4 a pt-v4 snapshot "
        "restored and re-run does not match the uninterrupted run, where "
        "pt-v1 and pt-v3 do. Resume anything checkpointed under 0.1.x on a "
        "jump-carrying preset with that in mind.",
    ),
    # The EDGAR page's worked example. The block is badged ILLUSTRATIVE, so
    # its composed panel is not a measurement claim, but it was composed
    # against pt-v3 and showed a volume row 13.6 sd out that pt-v10 holds in
    # band, and its step-4 verdict is a real function's output that has since
    # changed. A composed number may be arbitrary; it may not contradict the
    # model the page describes.
    (
        "abs_return_acf1             0.1688    0.0715    0.4102    0.1544 "
        "   (0.02, 0.22)  in band; p10-p90 crosses an edge\n...\n"
        "volume_change_acf1         -0.4571   -0.4688   -0.4402    0.0104 "
        " (-0.32, -0.20)  OUT 13.6 sd",
        "abs_return_acf1             0.1688    0.0715    0.4102    0.1544 "
        "   (0.02, 0.22)  in band; p10-p90 crosses an edge\n...\n"
        "volume_change_acf1         -0.3095   -0.3402   -0.2711    0.0268 "
        " (-0.32, -0.20)  in band; p10-p90 crosses an edge",
    ),
    (
        "# 4. the verdict for a concentrated roster\nOUTSIDE the envelope\n"
        "  - the roster is sector-concentrated, and certification was "
        "measured on\n    a perfectly balanced one. An S&amp;P-like mix "
        "holds 8/10 (abs_return_acf5\n    leaves band); an all-technology "
        "roster holds 7/10 at 32.8% volatility.\n    Re-measure on your own "
        "universe",
        "# 4. the verdict for a concentrated roster\nOUTSIDE the envelope\n"
        "  - the roster is sector-concentrated, and certification was "
        "measured on\n    a perfectly balanced one. An S&amp;P-like mix "
        "holds 8/10 (abs_return_acf5\n    leaves band); an all-technology "
        "roster holds 7/10 at 32.8% volatility.\n    Re-measure on your own "
        "universe\n  ? cross_sectional_corr is in band at the certified "
        "horizon (0.3063 in\n    (0.08, 0.56)) -- but that is a median "
        "across 30 seeds; check\n    `intervals` for the spread before "
        "relying on one seed",
    ),
    (
        "The sector counts in step 2 are usually why yours differ: 14 "
        "financials against a balanced roster's 5 changes what the market "
        "does.",
        "The sector counts in step 2 are usually why yours differ: 14 "
        "financials against a round-robin roster's 3 or 4 per sector changes "
        "what the market does. Step 4's two counts are out of the "
        "ten-statistic panel of the pt-v3 era and have not been re-measured "
        "since; what they establish is a property of the roster rather than "
        "of a preset.",
    ),
    # The worked methods paragraph is the citation a reader copies, so it
    # models the current shape or it teaches the wrong one. It named 0.1.0,
    # pt-v3 and a pre-era-boundary digest against a file that carries a
    # preset and no digest.
    (
        "Strategies were evaluated on pretium 0.1.0 (commit "
        '<code style="font-size:12px">&lt;sha&gt;</code>), model preset '
        '<code style="font-size:12px">pt-v3</code>,',
        "Strategies were evaluated on pretium 0.3.0 (commit "
        '<code style="font-size:12px">&lt;sha&gt;</code>), model preset '
        '<code style="font-size:12px">pt-v12</code>,',
    ),
    (
        "Realism of the shipped preset at this horizon is as published in "
        '<code style="font-size:12px">envelope.json</code> at digest '
        '<code style="font-size:12px">992ef95d\u2026dc185e3</code>; absolute '
        "returns are not claimed to forecast live results.",
        "Realism of the shipped preset at this horizon is as published in "
        '<code style="font-size:12px">envelope.json</code>, which names the '
        "preset the figures describe; absolute returns are not claimed to "
        "forecast live results.",
    ),
    # The sector table's own description of the generator. Round-robin over
    # twelve sectors is five names each only at n = 60; the certified roster
    # is 40 and gets four in four sectors and three in the other eight.
    (
        '<code style="font-size:12px">Universe.random</code> places exactly '
        "five names in each, and you pass the key rather than the display "
        "name,",
        '<code style="font-size:12px">Universe.random</code> assigns these '
        "round-robin, so a roster is as close to balanced as its size "
        "allows: 60 names give five each, and the certified 40 give four to "
        "four sectors and three to the other eight. You pass the key rather "
        "than the display name,",
    ),
    # The digest a reader was told to check. It is the pre-era-boundary
    # baseline, and the file named beside it carries the preset rather than a
    # digest, so the check as written could not be run.
    (
        "Both published measurement runs report known-answer digest "
        '<code style="font-size:13px">992ef95d…dc185e3</code>; if that does '
        "not match your installed wheel, the published page describes a "
        "different model than the one you are running.",
        "The determinism baseline for the current era is known-answer v11, "
        'digest <code style="font-size:13px">60d47572…0de590</code>, which '
        "the release workflow checks inside every wheel before it uploads; "
        "every era boundary carries a different one, so a digest is an era "
        "marker rather than a version number. For the check that matters "
        "to a citation, compare "
        '<code style="font-size:13px">pretium.model_preset()["name"]</code> '
        "against the preset named in envelope.json: if they differ, the "
        "published page describes a different model than the one you are "
        "running.",
    ),
    # The API preset reference stops at pt-v4 and recommends a superseded
    # default for multi-year work, which the realism page forbids outright.
    (
        "market_factor_sigma. Built for horizons past a year."
        "</sc-raw-td></sc-raw-tr>",
        "market_factor_sigma. Built to investigate horizons past a "
        "year, which is not the same as being certified for them: nothing "
        "in this project is certified past 252 days.</sc-raw-td></sc-raw-tr>",
    ),
    (
        '<h2 style="font-size:19px;margin:42px 0 8px">Choosing Between '
        "pt-v3 and pt-v4</h2>\n        <p>A real choice with a measured "
        "basis, and the two horizons disagree.</p>",
        '<h2 style="font-size:19px;margin:42px 0 8px">Choosing Between '
        "pt-v3 and pt-v4</h2>\n        <p>A historical choice, kept because "
        "work published against either preset still reproduces. Neither is "
        "the default: <code style=\"font-size:13px\">pt-v12</code> has been "
        "since 2026-08-26, and it holds all fourteen statistics at 252 days "
        "AND all fourteen at 504, so it dominates both rows below. The full "
        "table of the twelve shipped presets is on "
        '<a href="#/change">Presets and Custom Models</a>.</p>\n        '
        "<p>The two counts below are out of the ten-statistic panel of the "
        "pt-v3 era, and the two horizons disagree.</p>",
    ),
    (
        "<strong>pt-v3 for anything at or under a year, pt-v4 for "
        "multi-year questions.</strong>",
        "<strong>Between these two, and only these two: pt-v3 at or under a "
        "year, pt-v4 for INVESTIGATING longer horizons.</strong> Neither "
        "licenses a multi-year backtest, which the realism page forbids on "
        "every preset, and pt-v12 beats both at both horizons.",
    ),
    # The landing page's two preset cards still carried the 0.1.x count of
    # three. Twelve ship: rust/src/params.rs preset_names() returns pt-v1
    # through pt-v12 and every one of them builds and fingerprints as itself.
    (
        "Three shipped coefficient sets, how to change one, and the "
        "fingerprint that stops a modified model passing as the shipped one.",
        "Twelve shipped coefficient sets, how to change one, and the "
        "fingerprint that stops a modified model passing as the shipped one.",
    ),
    (
        "The three shipped coefficient sets, what each one does differently, "
        "and how to read a preset at runtime.",
        "The twelve shipped coefficient sets, what each one does "
        "differently, and how to read a preset at runtime.",
    ),
    # The worked fingerprint of the one mechanism that section is about. The
    # bundle's digest was never the digest this call produces, and a reader
    # checking their own build against it concludes their build differs.
    # Run in the repo venv on 0.3.0:
    #   pt.ModelParams.from_preset("pt-v1", garch_alpha=0.12).fingerprint
    #   -> 'custom-7f290e34'
    (
        'eng.model_fingerprint     # "custom-0c04c4ba" -- never "pt-v1"',
        'eng.model_fingerprint     # "custom-7f290e34" -- never "pt-v1"',
    ),
    ("The Seven Components", "The Nine Components"),
    ("seven components", "nine components"),
    ("seven factors", "nine factors"),
]

#: The bundle was authored before anything shipped, so its Release Notes page
#: still says 0.1.0 is unreleased and nothing has been tagged. Every word of
#: that is now false, and a documentation page asserting a package is
#: unpublished while it sits on PyPI is worse than a missing page.
#:
#: Corrected here rather than in the bundle, for the same reason as
#: PROSE_FIXES: a fresh design revision gets the treatment automatically. The
#: replacements assert, so if a later bundle rewords this section the build
#: fails loudly instead of quietly shipping a stale claim.
RELEASE_STATUS_FIXES = [
    (
        '<h2 style="font-size:19px;margin:40px 0 4px">0.1.0 '
        '<span style="font-weight:400;color:var(--faint);font-size:15px">'
        "- unreleased</span></h2>",
        '<h2 style="font-size:19px;margin:40px 0 4px">Status '
        '<span style="font-weight:400;color:var(--faint);font-size:15px">'
        "- {version}, current</span></h2>",
    ),
    (
        "Pre-release. Everything documented works and is tested. The API may "
        "move before 1.0. Nothing has been tagged, and there is no DOI yet.",
        "Published on PyPI as <code style=\"font-size:12.5px\">pretium</code> "
        "and on crates.io as the <code style=\"font-size:12.5px\">pretium</code> "
        "crate. The API is exercised by the test suite and the example "
        "notebooks are executed rather than written and hoped over, so the "
        "code on this site runs. Published FIGURES are a separate discipline "
        "and a weaker guarantee: they are re-measured by "
        "<code style=\"font-size:12.5px\">tools/remeasure</code>, which "
        "reports every number the stated method no longer produces, and a "
        "figure it flags is a documentation defect until someone corrects it. "
        "The API may move before 1.0. Each release is tagged and its wheels "
        "are built by the release workflow, which runs one fixed simulation "
        "inside every wheel and compares digests before anything is "
        "uploaded. There is no DOI yet.",
    ),
]


#: The release-notes page in the bundle carries release STATUS (which version
#: is current, what the era boundary means) and no release notes: a reader
#: clicking "Release notes" found nothing about what changed in 0.1.1 or
#: 0.1.3. CHANGELOG.md has all of it and is the file the release workflow
#: already cuts GitHub release notes from, so the site renders that rather
#: than keeping a second copy that can disagree with the first.
CHANGELOG = ROOT / "CHANGELOG.md"

#: The heading the rendered changelog is inserted before, so the page reads
#: status, then what changed in each release, then the era boundary note.
#: Asserted at build time: a reworded bundle fails loudly rather than
#: silently shipping a page with no release notes again.
CHANGELOG_ANCHOR = (
    '<h2 style="font-size:19px;margin:40px 0 4px">Known-answer v8 '
)

_MUT = 'style="color:var(--mut);font-size:14px'
_CODE = 'style="font-size:12.5px"'


def _inline(text: str) -> str:
    """Markdown inline markup to the design's inline-styled HTML."""
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", lambda m: f'<code {_CODE}>{m.group(1)}</code>', out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                 lambda m: f'<a href="{m.group(2)}" style="color:var(--accent)">{m.group(1)}</a>',
                 out)
    return out


def _table(rows: list[str]) -> str:
    """A markdown table, minus its separator row."""
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    body = [r for r in cells[1:] if not all(set(c) <= set("-: ") for c in r)]
    head = "".join(
        f'<th style="text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);'
        f'font:600 12px var(--font-ui)">{_inline(c)}</th>' for c in cells[0])
    trs = "".join(
        "<tr>" + "".join(
            f'<td style="padding:6px 10px;border-bottom:1px solid var(--linesoft);'
            f'font-size:13px;color:var(--mut)">{_inline(c)}</td>' for c in r) + "</tr>"
        for r in body)
    return ('<div style="overflow-x:auto"><table style="border-collapse:collapse;'
            f'margin:12px 0;min-width:100%">{"<tr>" + head + "</tr>"}{trs}</table></div>')


def changelog_html(version: str, dates: dict[str, str]) -> str:
    """CHANGELOG.md as the release-notes page's body.

    Handles exactly the markup the changelog uses: version and section
    headings, paragraphs with bold, code and links, tables, and fenced code
    blocks. An "Unreleased" section is skipped, because the site documents
    what a reader can install.
    """
    if not CHANGELOG.exists():
        sys.exit("CHANGELOG.md not found; the release-notes page needs it")
    lines = CHANGELOG.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    para: list[str] = []
    table: list[str] = []
    fence: list[str] | None = None
    skipping = False

    def flush() -> None:
        nonlocal para, table
        if table:
            out.append(_table(table)); table = []
        if para:
            out.append(f'<p {_MUT};margin:0 0 12px">{_inline(" ".join(para))}</p>')
            para = []

    for line in lines:
        if line.startswith("```"):
            if fence is None:
                flush(); fence = []
            else:
                if not skipping:
                    out.append(
                        '<pre style="background:var(--codebg);color:#e8e8e8;padding:12px 14px;'
                        'border-radius:6px;overflow-x:auto;font:12.5px/1.6 var(--font-mono)">'
                        + html.escape("\n".join(fence)) + "</pre>")
                fence = None
            continue
        if fence is not None:
            fence.append(line); continue
        if line.startswith("## "):
            flush()
            title = line[3:].strip()
            skipping = title.lower().startswith("unreleased")
            if skipping:
                continue
            when = dates.get(title, "")
            tag = " - current" if title == version else (f" - {when}" if when else "")
            out.append(
                f'<h2 style="font-size:19px;margin:44px 0 4px">{_inline(title)}'
                f'<span style="font-weight:400;color:var(--faint);font-size:15px">'
                f"{html.escape(tag)}</span></h2>")
            continue
        if skipping:
            continue
        if line.startswith("### "):
            flush()
            out.append(f'<h3 style="font-size:15px;margin:24px 0 6px">'
                       f"{_inline(line[4:].strip())}</h3>")
            continue
        if line.startswith("# "):
            continue
        if line.startswith("|"):
            if para:
                flush()
            table.append(line); continue
        if not line.strip():
            flush(); continue
        if table:
            flush()
        para.append(line.strip())
    flush()
    return "\n        ".join(out)


def tag_dates() -> dict[str, str]:
    """Release dates from the annotated tags, so the page cannot invent one."""
    try:
        import subprocess
        raw = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname:short) %(creatordate:short)",
             "refs/tags"], cwd=ROOT, capture_output=True, text=True, timeout=20).stdout
    except Exception:  # noqa: BLE001 - a missing git is not a build failure
        return {}
    out = {}
    for row in raw.splitlines():
        parts = row.split()
        if len(parts) == 2 and parts[0].startswith("v"):
            out[parts[0][1:]] = parts[1]
    return out


def read_bundle() -> str:
    """The bundle, with our corrections applied to the readable document.

    The document lives as a JSON string inside a `<script>` tag, so editing
    the raw file means knowing how the encoder escaped every character: a
    `/` arrives as `\\u002F`, which is why matching a literal `</span>`
    silently finds nothing. Decoding first, editing plain text, then
    re-encoding removes the guesswork.
    """
    if not DESIGN_BUNDLE.exists():
        sys.exit(f"design bundle not found: {DESIGN_BUNDLE}")
    raw = DESIGN_BUNDLE.read_text(encoding="utf-8")
    payload = script_payload(raw, "template")
    doc = json.loads(payload)

    for old, new in PROSE_FIXES:
        if old not in doc:
            sys.exit(
                "the design bundle no longer contains a phrase PROSE_FIXES "
                f"corrects, so the correction would be silently skipped: {old[:60]!r}"
            )
        doc = doc.replace(old, new)
    for old, new in ERA_FIXES:
        if old not in doc:
            sys.exit(
                "the design bundle no longer contains a phrase ERA_FIXES "
                f"corrects, so the correction would be silently skipped: {old[:60]!r}"
            )
        doc = doc.replace(old, new)
    doc = newpages.inject(doc)
    doc = upgrades.apply(doc)
    doc = presets.replace_table(doc, presets.load())
    doc = apply_trust_fixes(doc)
    doc = apply_scenarios_fixes(doc)
    doc = apply_internals_fixes(doc)
    doc = apply_factors_fixes(doc)
    doc = apply_guide_fixes(doc)
    doc = apply_api_fixes(doc)
    doc = apply_reference_fixes(doc)
    # Two spots track the package version; the rest are history. See VERSION.
    doc = doc.replace(">v0.1.0<", f">v{VERSION}<")
    doc = doc.replace("version = {0.1.0}", "version = {%s}" % VERSION)
    if CHANGELOG_ANCHOR not in doc:
        sys.exit("the release-notes era-boundary heading was reworded; "
                 "CHANGELOG_ANCHOR needs updating rather than silently "
                 "shipping a release-notes page with no release notes")
    doc = doc.replace(
        CHANGELOG_ANCHOR,
        changelog_html(VERSION, tag_dates())
        + "\n\n        " + CHANGELOG_ANCHOR,
        1,
    )

    for stale, current in RELEASE_STATUS_FIXES:
        if stale not in doc:
            sys.exit(
                "release-status text not found in the bundle. It was reworded "
                "upstream, so RELEASE_STATUS_FIXES needs updating rather than "
                "silently shipping a stale claim:\n  " + stale[:90]
            )
        doc = doc.replace(stale, current.format(version=VERSION))

    # Re-encode, then escape every forward slash the way the bundler does.
    # Without that, a literal "</script>" inside the document would close the
    # tag early and the page would never boot.
    encoded = json.dumps(doc).replace("/", "\\u002F")
    out = raw.replace(payload, encoded, 1)
    # The outer head, which is not part of the payload just re-encoded above.
    # json.dumps escapes non-ASCII, so a literal em dash exists in `out` only
    # outside the payload and this cannot reach into the document.
    for old, new in BUNDLE_HEAD_FIXES:
        if old not in out:
            sys.exit(
                "the design bundle's outer head no longer contains a phrase "
                f"BUNDLE_HEAD_FIXES corrects: {old[:60]!r}"
            )
        out = out.replace(old, new)
    json.loads(script_payload(out, "template"))  # prove it still parses
    return out


def script_payload(bundle: str, kind: str) -> str:
    """Return the text of one `__bundler/<kind>` script tag."""
    m = re.search(
        rf'<script[^>]*type="__bundler/{kind}"[^>]*>(.*?)</script>', bundle, re.S
    )
    if not m:
        sys.exit(f"bundle has no __bundler/{kind} block")
    return m.group(1).strip()


def inner_document(bundle: str) -> str:
    """The readable document, which the bundle carries as a JSON string."""
    doc = json.loads(script_payload(bundle, "template"))
    if not isinstance(doc, str):
        sys.exit("template block was not a JSON string")
    return doc


def design_css(doc: str) -> str:
    """The design system, minus the @font-face block bound to bundle blobs."""
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", doc, re.S)
    if not blocks:
        sys.exit("no <style> block in the design document")
    # The font block is by far the largest and is almost entirely @font-face
    # rules pointing at uuid blob URLs. The design system is the other one.
    system = min(blocks, key=lambda b: b.count("@font-face"))
    return re.sub(r"@font-face\s*\{.*?\}", "", system, flags=re.S).strip()


#: A trailing `</div>` or `</main>`, with the whitespace around it.
_TRAILING_CLOSER = re.compile(r"\s*</(?:div|main)>\s*$")


def trim_app_shell(raw: str) -> str:
    """Drop closing tags a page fragment never opened.

    Every page but the last is bounded by the next page's opening div, so
    what it carries is exactly its own markup. The last one runs to the end
    of the document instead, and so picks up the app shell that wraps every
    route: a `</div>` and a `</main>` that belong to the bundle, not to the
    page. Published, those became a second `</main>` and two stray `</div>`
    at the foot of api-presets.html, the only static page on the site whose
    tags did not balance.

    Counting is enough because neither `div` nor `main` can self-close, and
    the surplus is always a run of closers at the very end, so removing the
    last closing tag is removing a surplus one. Fragments that already
    balance leave the loop on the first test and are returned untouched.
    """
    while len(re.findall(r"</(?:div|main)>", raw)) > len(
        re.findall(r"<(?:div|main)\b", raw)
    ):
        trimmed = _TRAILING_CLOSER.sub("", raw, count=1)
        if trimmed == raw:
            return raw
        raw = trimmed
    return raw


def split_pages(doc: str) -> list[dict]:
    """Every `data-page` div, in document order, with its label and title."""
    marks = list(re.finditer(r'<div([^>]*?)data-page="([^"]+)"([^>]*)>', doc))
    if not marks:
        sys.exit("no data-page divs found")
    pages = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else doc.rindex("</x-dc>")
        raw = doc[m.end() : end]
        # Each page div is closed by the </div> that precedes the next one.
        raw = raw[: raw.rindex("</div>")] if "</div>" in raw else raw
        raw = trim_app_shell(raw)
        attrs = m.group(1) + m.group(3)
        label = re.search(r'data-screen-label="([^"]*)"', attrs)
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", raw, re.S)
        pages.append(
            {
                "slug": m.group(2),
                "label": label.group(1) if label else m.group(2),
                "h1": strip_tags(h1.group(1)) if h1 else (label.group(1) if label else m.group(2)),
                "html": raw,
            }
        )
    return pages


def strip_tags(fragment: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


def page_url(slug: str) -> str:
    return "./" if slug == "home" else f"{slug}.html"


def absolute(slug: str) -> str:
    return f"{BASE_URL}/" if slug == "home" else f"{BASE_URL}/{slug}.html"


def clean_content(fragment: str) -> str:
    """Turn a bundle page fragment into standalone, JS-free HTML."""
    out = fragment
    # Tables were escaped into custom elements to survive the bundler.
    out = re.sub(r"<(/?)sc-raw-([a-z]+)", r"<\1\2", out)
    # camelCase attributes were escaped the same way.
    out = re.sub(
        r"\bsc-camel-([a-z-]+)=",
        lambda m: m.group(1).replace("-", "") + "=",
        out,
    )
    # Copy-to-clipboard buttons do nothing without the app behind them.
    out = re.sub(
        r'<button[^>]*\{\{\s*copyCode\s*\}\}[^>]*>.*?</button>', "", out, flags=re.S
    )
    out = re.sub(r'<button[^>]*>\s*Copy\s*</button>', "", out, flags=re.S)
    # Hash routes become real page links; in-page anchors are left alone.
    out = re.sub(r'href="#/([a-z0-9-]+)"', lambda m: f'href="{page_url(m.group(1))}"', out)
    out = out.replace('href="#/"', 'href="./"')
    # Any template variable left over would render as literal braces.
    out = re.sub(r"\s*\w+=\"[^\"]*\{\{[^}]*\}\}[^\"]*\"", "", out)
    out = re.sub(r"\{\{[^}]*\}\}", "", out)
    return out.strip()


def description_for(page: dict) -> str:
    """The page's hand-written description.

    Falls back to the first real sentence, trimmed, which is what every page
    used to get. That heuristic produced thirteen descriptions ending
    mid-clause and eight under 120 characters, so `seo.check_descriptions`
    now fails the build before this fallback can be reached.
    """
    written = seo.DESCRIPTIONS.get(page["slug"])
    if written:
        return written
    body = re.sub(r"<h1[^>]*>.*?</h1>", " ", page["html"], flags=re.S)
    for para in re.findall(r"<p[^>]*>(.*?)</p>", body, re.S):
        text = strip_tags(para)
        if len(text) > 60:
            if len(text) > 155:
                cut = text[:155].rsplit(" ", 1)[0]
                return cut.rstrip(",;:") + "..."
            return text
    return f"{page['h1']}. Documentation for pretium, a deterministic market simulator."


def analytics() -> str:
    if not GA_MEASUREMENT_ID:
        return ""
    gid = GA_MEASUREMENT_ID
    return f"""<script async src="https://www.googletagmanager.com/gtag/js?id={gid}"></script>
<script>
window.dataLayer = window.dataLayer || [];
function gtag(){{dataLayer.push(arguments);}}
gtag('js', new Date());
gtag('config', '{gid}');
</script>"""


def spa_analytics() -> str:
    """Analytics for the app, which changes route without changing URL path.

    A hash change fires no navigation, so gtag would record one pageview for
    a whole reading session. This sends one per route instead.
    """
    if not GA_MEASUREMENT_ID:
        return ""
    gid = GA_MEASUREMENT_ID
    return f"""<script async src="https://www.googletagmanager.com/gtag/js?id={gid}"></script>
<script>
window.dataLayer = window.dataLayer || [];
function gtag(){{dataLayer.push(arguments);}}
gtag('js', new Date());
gtag('config', '{gid}', {{send_page_view: false}});
function pretiumPageview() {{
  var route = location.hash.replace(/^#\\/?/, '') || 'home';
  gtag('event', 'page_view', {{
    page_title: document.title,
    page_location: location.href,
    page_path: '/' + route
  }});
}}
window.addEventListener('hashchange', pretiumPageview);
pretiumPageview();
</script>"""


def json_ld(page: dict, desc: str) -> str:
    """Structured data for one page.

    Three things were missing and each cost the same kind of ambiguity. The
    article had no dates, so nothing could tell a two-day-old page from a
    two-month-old one. The software node had no `sameAs`, so the PyPI
    package, the crate and this site were four unrelated entities rather
    than one. And "pretium" is Latin for price, which is a crowded name to
    have no disambiguation at all.
    """
    published, modified = seo.page_dates(page["slug"])
    data = {
        "@context": "https://schema.org",
        "@type": "TechArticle",
        "headline": seo.title(page["slug"], page["h1"]),
        "description": desc,
        "url": absolute(page["slug"]),
        "inLanguage": "en",
        "isPartOf": {
            "@type": "WebSite",
            "name": SITE_NAME,
            "url": f"{BASE_URL}/",
        },
        "about": seo.software_node(BASE_URL, REPO_URL, VERSION),
    }
    if published:
        data["datePublished"] = published
    if modified:
        data["dateModified"] = modified

    blocks = [json.dumps(data, separators=(",", ":"))]
    faq = seo.faq_node(page["slug"])
    if faq:
        blocks.append(json.dumps(faq, separators=(",", ":")))
    if page["slug"] == "trust":
        blocks.append(
            json.dumps(dataset_node_for_page(), separators=(",", ":"))
        )
    return "".join(
        '<script type="application/ld+json">' + b + "</script>" for b in blocks
    )


def dataset_node_for_page() -> dict:
    return seo.dataset_node(BASE_URL, REPO_URL)



def _fmt(v: float) -> str:
    # The bundle's own convention: a real minus sign, not a hyphen.
    s = f"{v:.2f}" if abs(v) >= 1 else f"{v:+.4f}"
    return s.replace("-", "\u2212")


def _band(lo: float, hi: float) -> str:
    def n(x):
        s = f"{x:.4f}".rstrip("0").rstrip(".")
        return (s if s not in ("-0", "") else "0").replace("-", "\u2212")
    return f"{n(lo)} - {n(hi)}"


def certified_rows() -> str:
    """The certification table's body, generated from the shipped envelope.

    The bundle carries a pt-v3-era table: ten statistics, `volume_change_acf1`
    at -0.4598 and out by 13.7 sd. Every figure moved at the 2026-08-26 era
    boundary, and this is the page a reader consults to decide whether to
    trust a result. Generating it from `pretium.envelope` means the published
    table cannot drift from the module again.
    """
    from pretium import envelope as env, facts

    rows = []
    for name, value in env.CERTIFIED.items():
        if name not in facts.REAL_MARKETS:
            continue
        lo, hi = facts.REAL_MARKETS[name]
        ok = lo <= value <= hi
        verdict = "in band" if ok else "out"
        colour = "var(--mut)" if ok else "var(--warn, #b45309)"
        rows.append(
            f"<sc-raw-tr><sc-raw-td>{name}</sc-raw-td>"
            f'<sc-raw-td style="text-align:right">{_fmt(value)}</sc-raw-td>'
            f'<sc-raw-td style="text-align:right">{_band(lo, hi)}</sc-raw-td>'
            f'<sc-raw-td style="color:{colour}">{verdict}</sc-raw-td></sc-raw-tr>'
        )
    body = "\n            ".join(rows)
    return (
        '<sc-raw-tbody style="font-family:var(--font-mono);font-size:12.5px">\n'
        f"            {body}\n          </sc-raw-tbody>"
    )


def horizon_rows() -> str:
    """The 504-day comparison table, likewise generated."""
    from pretium import envelope as env, facts

    rows = []
    for name in facts.REAL_MARKETS:
        v252, v504 = env.CERTIFIED.get(name), env.MEASURED_504.get(name)
        if v252 is None or v504 is None:
            continue
        lo, hi = facts.REAL_MARKETS_504[name]
        ok = lo <= v504 <= hi
        rows.append(
            f"<sc-raw-tr><sc-raw-td>{name}</sc-raw-td>"
            f'<sc-raw-td style="text-align:right">{_fmt(v252)}</sc-raw-td>'
            f'<sc-raw-td style="text-align:right">{_fmt(v504)}</sc-raw-td>'
            f'<sc-raw-td style="text-align:right">{_band(lo, hi)}</sc-raw-td>'
            f'<sc-raw-td>{"in" if ok else "out"}</sc-raw-td></sc-raw-tr>'
        )
    return "\n            ".join(rows)


#: The held-out axes, re-measured on pt-v12 at the same thirty-seed
#: resolution as the certification itself (`docs/realism-envelope.md`, "The
#: claim survives the axes it was not fitted to"). The bundle carries the
#: pt-v3-era 9/10 version of this table.
#:
#: The held-out-seed row is 13/14 and says which row it misses. It read
#: 14/14 here while `docs/realism-envelope.md` read 13/14, which is the
#: contradiction this entry exists to end: `corr_persistence_acf1` is in
#: band on the certification's own seeds and out of band on thirty seeds
#: the calibration never drew. An L_real of 0.0000 follows from every row
#: being in band, so the two clean axes can carry one; the held-out-seed
#: axis cannot, and no run in the repository records its loss, so the cell
#: says so rather than carrying a number nobody measured.
#:
#: Not generated from `pretium.envelope` like the two tables above it,
#: because the module carries the certified panel and not the held-out runs.
#: A generated table cannot drift; this one can, so it names its provenance.
HELD_OUT_ROWS = (
    '<sc-raw-tbody style="font-family:var(--font-mono);font-size:12.5px">\n'
    "            <sc-raw-tr><sc-raw-td>training seeds (101-130), 40 names"
    '</sc-raw-td><sc-raw-td style="text-align:right">14/14 in band'
    '</sc-raw-td><sc-raw-td style="text-align:right">0.0000</sc-raw-td>'
    "</sc-raw-tr>\n"
    "            <sc-raw-tr><sc-raw-td>held-out seeds (1-30), 40 names"
    '</sc-raw-td><sc-raw-td style="text-align:right">13/14 in band, '
    "corr_persistence_acf1 out"
    '</sc-raw-td><sc-raw-td style="text-align:right">not recorded'
    "</sc-raw-td></sc-raw-tr>\n"
    "            <sc-raw-tr><sc-raw-td>held-out universe (60 names, seed 909)"
    '</sc-raw-td><sc-raw-td style="text-align:right">14/14 in band'
    '</sc-raw-td><sc-raw-td style="text-align:right">0.0000</sc-raw-td>'
    "</sc-raw-tr>\n          </sc-raw-tbody>"
)


def apply_trust_fixes(doc: str) -> str:
    """Bring the realism page's measured content up to the shipped envelope.

    The bundle's "What Is Certified" table, its 504-day comparison and its
    first two gaps are all pt-v3 era. This is the page the site points a
    reader to before they trust a result, so a stale number here is the worst
    stale number in the project. Each replacement asserts.
    """
    def cut(marker: str, start_tag: str, end_tag: str, replacement: str) -> None:
        nonlocal doc
        if marker not in doc:
            sys.exit(f"the design bundle no longer contains {marker[:60]!r}; "
                     "apply_trust_fixes needs updating rather than silently "
                     "shipping a pt-v3-era realism page")
        i = doc.index(marker)
        s = doc.index(start_tag, i)
        e = doc.index(end_tag, s) + len(end_tag)
        doc = doc[:s] + replacement + doc[e:]

    def _block(marker: str) -> tuple[int, int]:
        """The <details> element whose summary contains `marker`."""
        if marker not in doc:
            sys.exit(f"the design bundle no longer contains {marker[:60]!r}; "
                     "apply_trust_fixes needs updating rather than silently "
                     "shipping a stale gap list")
        i = doc.index(marker)
        s = doc.rindex("<details", 0, i)
        e = doc.index("</details>", i) + len("</details>")
        return s, e

    def drop(marker: str) -> None:
        """Remove one gap's <details> block, and the blank line before it."""
        nonlocal doc
        s, e = _block(marker)
        while s > 0 and doc[s - 1] in " \t":
            s -= 1
        if s > 0 and doc[s - 1] == "\n":
            s -= 1
        doc = doc[:s] + doc[e:]

    def reorder(first: str, second: str) -> None:
        """Put the `second` gap block ahead of the `first` one."""
        nonlocal doc
        s1, e1 = _block(first)
        s2, e2 = _block(second)
        if not s1 < e1 <= s2 < e2 or doc[e1:s2].strip():
            sys.exit("the two gap blocks apply_trust_fixes reorders are no "
                     "longer adjacent in that order; the numbering would "
                     "ship out of sequence")
        doc = (doc[:s1] + doc[s2:e2] + doc[e1:s2] + doc[s1:e1] + doc[e2:])

    swaps = [
        # Provenance. The three method rows at the top of the page name the
        # preset each figure came from, and two of them still said pt-v3
        # while the table below them was regenerated for pt-v10. A
        # provenance line that names the wrong preset is worse than none.
        (
            "certification panel</sc-raw-td><sc-raw-td>pt-v3, 30 seeds, 40 "
            "instruments, 252 days. The figures in the table below, and the "
            'only ones the word "certified" applies to.</sc-raw-td>',
            "certification panel</sc-raw-td><sc-raw-td>pt-v12, 30 seeds, 40 "
            "instruments, 252 days, every figure the MEDIAN across those "
            "seeds. The figures in the table below, and the only ones the "
            'word "certified" applies to. A median is not a typical run: '
            "read Seed-to-Seed Variation before quoting one.</sc-raw-td>",
        ),
        (
            "six-seed panel</sc-raw-td><sc-raw-td>pt-v3, sim seeds 1 to 6,",
            "six-seed panel</sc-raw-td><sc-raw-td>pt-v12, sim seeds 1 to 6,",
        ),
        (
            "One seed, a smaller roster, 120 days, a macro field held fixed. "
            'What the <a href="#/scenarios">scenario</a> figures come from. '
            "Good for comparing one pin against another, and not comparable "
            "to either panel.",
            "One seed, a smaller roster, 120 days, a macro field held fixed. "
            'What the <a href="#/scenarios">scenario</a> figures come from. '
            "Good for comparing one pin against another, and not comparable "
            "to either panel. In particular, the calm-to-crisis volatility "
            "ratio a pair of pinned runs implies is NOT the crisis lever in "
            "gap 3: that one is measured on the certified 40-name roster "
            "over 252 days at thirty seeds, from VIX 5 to VIX 65. Three "
            "numbers on this site describe how violent a crisis is, and they "
            "are three different measurements.",
        ),
        (
            "<p>At a 252-day horizon the shipped <code style=\"font-size:13px\">"
            "pt-v3</code> preset puts nine of the ten measured statistics in "
            "band. The tenth fails structurally and is named, in every result "
            "the library produces.</p>",
            "<p>At a 252-day horizon the shipped <code style=\"font-size:13px\">"
            "pt-v12</code> preset puts all fourteen measured statistics in "
            "band, on thirty calibration seeds and on a held-out 60-name "
            "universe measured at the same resolution. It also puts all "
            "fourteen in band at 504 days against bands re-derived at that "
            "window, which is the first two-year clean sheet in the project: "
            "pt-v3 held seven there and pt-v10 held thirteen. Every figure below "
            "is the median of those thirty seeds, and that median is what the "
            "word certified describes: it is not a promise about your one "
            "run, which the spread further down prices.</p>",
        ),
        (
            "Measured: preset pt-v3 · 30 seeds · 40 instruments · 252 trading "
            "days · band-distance loss L_real = 0.0000.",
            "Measured: preset pt-v12 · 30 seeds · 40 instruments · 252 trading "
            "days · band-distance loss L_real = 0.0000.",
        ),
        # The volume-change gap is RETIRED at pt-v12, which holds
        # `volume_change_acf1` at -0.2656 over 252 days and -0.2572 over 504,
        # inside both bands. Its block is dropped rather than reworded (see
        # `drop` below), because a retired gap left in slot 1 forbids what the
        # envelope now permits and pushes every later gap number out by one.
        # The retirement itself is recorded in the paragraph that introduces
        # the list, so the count changing is explained rather than silent.
        (
            "<p>Each of these was measured, and each one says what it rules "
            "out. Note that the horizon gaps only bite past a year.</p>",
            "<p>Each of these was measured, and each one says what it rules "
            "out. Note that the horizon gaps only bite past a year. There "
            "were six until the pt-v12 boundary on 2026-08-26: the "
            "volume-change gap retired when "
            "<code style=\"font-size:12.5px\">volume_change_acf1</code> came "
            "inside its band at one year, \u22120.2656 against \u22120.32 to "
            "\u22120.20, and at two, \u22120.2572 against \u22120.29 to \u22120.21. A "
            "retired gap is recorded in the release notes rather than kept as "
            "a numbered slot, so the five below are numbered as "
            "<code style=\"font-size:12.5px\">pretium.envelope.GAPS</code> "
            "lists them.</p>",
        ),
        (
            "Measured against bands re-derived at the matching 504-day window, "
            "the model holds 5 of 10.",
            "Measured against bands re-derived at the matching 504-day window, "
            "the model holds all fourteen.",
        ),
        (
            "Volatility clustering roughly doubles from 252 to 504 days where "
            "real markets move about 14%. The price level stays plausible, so "
            "long runs look fine and are not. The dynamics leave the envelope "
            "while the chart still looks like a market.",
            "That is the first two-year clean sheet in the project, pt-v3 "
            "having held seven there and pt-v10 thirteen, and it is still not "
            "a certification: CERTIFIED is measured at 252 days, and the "
            "504-day table is measured but not certified. Two rows deserve a "
            "reader's caution at this horizon. Annualised volatility reads "
            "33.89 against a ceiling of 34.0, which is a tenth of a point of "
            "room on a statistic whose seed spread is many times that, and "
            "excess kurtosis reads 7.75, 0.17 seed-sd above a floor of 7.1. "
            "Past 504 days the panel is measured and has no ruler of its own: "
            "at 2520 days it holds 10 of 14 against the 504-day bands, which "
            "are the wrong bands for a ten-year window and are quoted only "
            "because none have been derived at one, and annualised volatility "
            "is flat year by year across those ten years, so a long run does "
            "not drift or blow up. The missing bands are what keeps the "
            "certification at 252 days.",
        ),
    ]
    swaps.extend([
        (
            "4 · Tails are too thin over multi-year windows",
            "4 · The endogenous economy cannot reach its own MACRO crisis "
            "regimes",
        ),
        (
            "<p>Over 504-day windows real markets show excess kurtosis of 7.1 "
            "to 22. The model shows 5.2. The 252-day band's floor of 1.6 is "
            "wide enough that this reads as comfortably in band on every "
            "252-day certificate, which is why it went unnoticed: nothing was "
            "measuring kurtosis where it fails.</p>",
            "<p>Two kinds of crisis, and the answer differs. A VOLATILITY "
            "crisis is endogenous since 0.2.0: the shipped preset's own VIX "
            "crosses its crisis threshold on 10.2% of days against a real "
            "12.5%, where the previous default reached it on none, so a "
            "panic is something this market produces and not only something "
            "a scenario drives. A MACRO crisis is not. Left to itself the "
            "macro state stays in a moderate band: endogenous inflation "
            "peaks at 4.06% to 4.11% over five seeds and five years, against "
            "a clamp of 6.0% and a US CPI that reached 9.1% in June 2022, "
            "and the central bank's own crisis cadence hangs off an "
            "inflation rate the economy never gets to. The cause is "
            "dispersion rather than persistence. This slot used to carry a "
            "thin-tails gap, retired at 0.2.0: two-year excess kurtosis "
            "moved from 5.23, under its band, to 8.26 under pt-v10 and "
            "7.75 under pt-v12, inside it both times.</p>",
        ),
        (
            "<p style=\"color:var(--fg)\"><strong>Forbids:</strong> tail-risk or "
            "VaR calibration at multi-year horizons. At the certified 252-day "
            "horizon, kurtosis is in band.</p>",
            "<p style=\"color:var(--fg)\"><strong>Forbids:</strong> studying an "
            "inflation regime or a policy crisis from the endogenous economy "
            "alone. Drive one with a scenario instead. A volatility crisis "
            "does not need one.</p>",
        ),
        (
            '<p style="margin:14px 0 0">Five of the ten statistics were live '
            "calibration targets, so an in-band verdict on those is partly "
            "the tuning meeting its target. The held-out rows are what make "
            "the claim more than that.</p>",
            '<p style="margin:14px 0 0">Five of the fourteen statistics were '
            "live calibration targets, so an in-band verdict on those five is "
            "the tuning meeting its target rather than an independent "
            "success. The held-out rows test whether the fit generalises; "
            "they do not make those five independent.</p>\n        "
            '<p style="margin:14px 0 0">And read "held out" narrowly, because '
            "it is doing less work than the phrase suggests. It means "
            "simulation seeds the calibration never drew and a roster it "
            "never ran. It does not mean withheld market data: the bands come "
            "from real-market windows, and the same bands were used to tune "
            "against and to grade against, with no empirical train and test "
            "split behind them. The held-out universe is another "
            "<code style=\"font-size:12.5px\">Universe.random()</code> draw "
            "from the same generator, so it varies the names and not the "
            "shape of the roster. Gap 5 varies the shape, and on pt-v12 that "
            "costs the second year rather than the first.</p>\n        "
            '<p style="margin:14px 0 0">The held-out-seed row is the one to '
            "read twice. "
            "<code style=\"font-size:12.5px\">corr_persistence_acf1</code> is "
            "in band at +0.1525 on the thirty seeds the certification was "
            "taken on and out of it on thirty seeds the calibration never "
            "used, which is a statistic being hard to estimate over a "
            "one-year window rather than a model changing. Its 504-day band "
            "of 0.19 to 0.49 is the ruler that can settle it, and the row "
            "reads +0.2077 there. The band-distance loss for that axis was "
            "not recorded, so the table says so rather than carrying a "
            "number.</p>",
        ),
        # The two worked examples' output blocks. Both were captured before
        # the era boundary and both carry a badge saying they were measured,
        # which makes a stale block worse than an unlabelled one. Their text
        # is what the installed package prints: `envelope.check` reads the
        # shipped module rather than running a simulation, so these two blocks
        # can be re-captured on any machine in a second and were, on 0.3.0.
        (
            "OUTSIDE the envelope\n  - horizon 504d exceeds the certified "
            "252d. At 504 days the model holds\n    5 of 10 against "
            "horizon-matched bands: abs_return_acf1 0.289 against\n    "
            "(0.04, 0.22), abs_return_acf5 0.152 against (0.02, 0.10)\n  - "
            "excess_kurtosis reads 5.23 at 504 days against a horizon-matched "
            "band\n    of (7.1, 22.0) -- the tails are too thin where you are "
            "measuring\n  - abs_return_acf20 depends on the decay shape, "
            "which is a mechanism gap:\n    log-log slope -0.953 against real "
            "markets' -0.436, and the curve is\n    negative by lag 30 where "
            "real markets stay positive to lag 60",
            "OUTSIDE the envelope\n  - horizon 504d exceeds the certified "
            "252d. At 504 days the model holds\n    all 14 against "
            "horizon-matched bands. The thin one is\n    annualised_vol_pct "
            "at 33.89 against (16.0, 34.0). Beyond 504 days the\n    panel is "
            "measured but has no ruler of its own: at 2520 days it holds\n"
            "    10 of 14 against the 504-day bands\n"
            "    (tools/calibration/long_horizon.py), and annualised "
            "volatility is flat\n    year by year across those ten years\n"
            "    (tools/calibration/memory_vs_drift.py). No bands have been "
            "derived at\n    a five-year window, which is why the "
            "certification stops here\n  - abs_return_acf20 depends on the "
            "decay shape, which is a mechanism gap:\n    log-log slope -0.953 "
            "against real markets' -0.436, and the curve is\n    negative by "
            "lag 30 where real markets stay positive to lag 60\n  ? "
            "excess_kurtosis reads 7.75 at 504 days against (7.1, 22.0): "
            "inside it,\n    but 0.17 seed-sd above the floor, so a tail "
            "study at this horizon is\n    reading the low edge of the band",
        ),
        # The six-month worked example. Every figure in it is now a real
        # measurement, so the block's badge stops saying MIXED: the spread
        # columns and the strategy line were the composed part, and both were
        # re-run. Measured on pretium 0.2.0, pt-v10, the page's own code:
        # Universe.random(40, seed=111), sim seeds 1 to 30, 126 days.
        (
            '<span title="Medians and verdict text are quoted from the '
            "shipped envelope; the spread columns and strategy numbers were "
            'composed for the example." style="font:500 9.5px/1 '
            "var(--font-mono);letter-spacing:0.07em;border-radius:5px;"
            "padding:3px 6px;margin-left:8px;color:var(--codemut);border:1px "
            'solid var(--codeline)">MIXED, SEE NOTE</span></div>\n          '
            '<pre style="padding:13px 16px;overflow-x:auto"><code '
            'data-lang="txt" style="font:400 12.5px/1.75 var(--font-mono);'
            'color:var(--codemut)"># 1.',
            '<span title="Captured from the run named beside this block." '
            'style="font:500 9.5px/1 var(--font-mono);letter-spacing:0.07em;'
            "border-radius:5px;padding:3px 6px;margin-left:8px;color:"
            'var(--tks);border:1px solid var(--tks)">MEASURED</span></div>\n'
            '          <pre style="padding:13px 16px;overflow-x:auto"><code '
            'data-lang="txt" style="font:400 12.5px/1.75 var(--font-mono);'
            'color:var(--codemut)"># 1.',
        ),
        (
            "# 1.\nTrue\ninside the envelope\n  - horizon 126d is within the "
            "certified 252d, and no named statistic\n    meets a measured "
            "gap\n  ? return_acf1 is in band at the certified horizon (0.0375 "
            "in\n    (-0.08, 0.06)) -- but that is a median across 30 seeds; "
            "check\n    `intervals` for the spread before relying on one "
            "seed\n  ? abs_return_acf1 is in band at the certified horizon "
            "(0.1413 in\n    (0.02, 0.22)) -- but that is a median across 30 "
            "seeds; check\n    `intervals` for the spread before relying on "
            "one seed",
            "# 1.\nTrue\ninside the envelope for the statistics you named\n"
            "  - horizon 126d is within the certified 252d, and no named "
            "statistic\n    meets a measured gap\n  ? return_acf1 is in band "
            "at the certified horizon (0.0239 in\n    (-0.08, 0.06)) -- but "
            "that is a median across 30 seeds; check\n    `intervals` for the "
            "spread before relying on one seed\n  ? abs_return_acf1 is in "
            "band at the certified horizon (0.1107 in\n    (0.02, 0.22)) -- "
            "but that is a median across 30 seeds; check\n    `intervals` for "
            "the spread before relying on one seed",
        ),
        (
            "annualised_vol_pct         24.0972   19.8410   30.1174    3.9026"
            "    (15.0, 36.0)  in band\nexcess_kurtosis             2.5305    "
            "1.7118    5.4402    1.4771     (1.6, 41.0)  in band\n"
            "return_acf1                 0.0375    0.0198    0.0631    0.0164"
            "   (-0.08, 0.06)  in band on the median; p10-p90 crosses an edge"
            "\nabs_return_acf1             0.1413    0.0492    0.4260    "
            "0.1701     (0.02, 0.22)  in band on the median; p10-p90 crosses "
            "an edge\n...\nvolume_change_acf1         -0.4598   -0.4712   "
            "-0.4441    0.0098  (-0.32, -0.20)  OUT 13.7 sd\n\n# 3.\n"
            "4.87 48719.20",
            "annualised_vol_pct         30.1974   26.5765   37.8424    7.1275"
            "     (15.0, 36.0)  in band on the median; p10-p90 crosses an edge"
            "\nexcess_kurtosis             6.9315    4.5513   11.3102    "
            "2.9010      (1.6, 41.0)  in band\n"
            "return_acf1                 0.0057   -0.0299    0.1492    0.0704"
            "    (-0.08, 0.06)  in band on the median; p10-p90 crosses an edge"
            "\nabs_return_acf1             0.0713    0.0280    0.1793    "
            "0.0941     (0.02, 0.22)  in band (an extreme seed crosses)\n"
            "abs_return_acf5             0.0190   -0.0146    0.1264    0.0555"
            "     (0.02, 0.09)  OUT 0.0 sd\n...\n"
            "volume_change_acf1         -0.3160   -0.3441   -0.2688    0.0282"
            "    (-0.32, -0.2)  in band on the median; p10-p90 crosses an edge"
            "\ncorr_persistence_acf1    unmeasured\n\n# 3.\n-52.87 -11.89",
        ),
        (
            "In that block the medians, bands, verdicts and the check reasons "
            "come from the shipped envelope. The p10, p90 and sd columns and "
            "the step 3 numbers are composed to show the shape of the report.",
            "Every figure in that block is a measurement, and the block "
            "spans two presets because its parts cost different amounts to "
            "re-run. Step 1 is envelope.check reading the shipped module "
            "rather than simulating anything, so it is captured on 0.3.0 "
            "under pt-v12 and is what the code above prints today. Steps 2 "
            "and 3 are a thirty-seed measurement: pretium 0.2.0, pt-v10, the "
            "code above verbatim, sim seeds 1 to 30 over "
            "Universe.random(40, seed=111) at 126 days, left as measured "
            "rather than relabelled. Rows between abs_return_acf5 and "
            "volume_change_acf1 are elided for width. Run steps 2 and 3 again "
            "under pt-v12 and the numbers move, which is what naming a preset "
            "is for.",
        ),
        (
            "Step 1 says yes, with two warnings. Step 2 shows why they were "
            "worth printing: <code style=\"font-size:13px\">abs_return_acf1"
            "</code> has a median of 0.141 inside its band and a p90 of 0.426, "
            "well above the ceiling of 0.22. A single seed can easily land out "
            "of band on a statistic that looks comfortable in the summary. "
            "That is what decides how many seeds step 3 needs.",
            "Step 1 says yes for the two statistics it was asked about, which "
            "is the whole of what it says. Step 2 is why that scope matters: "
            "at this horizon "
            "<code style=\"font-size:13px\">abs_return_acf5</code> sits on its "
            "floor and reads out, and "
            "<code style=\"font-size:13px\">corr_persistence_acf1</code> "
            "cannot be measured at all in 126 days, because twelve 21-day "
            "windows are not enough to autocorrelate. Neither was named in "
            "step 1, so neither appears in its verdict. The named statistics "
            "carry their own spread besides: "
            "<code style=\"font-size:13px\">return_acf1</code> has a median of "
            "0.0057 inside its band and a p90 of 0.149, well above the "
            "ceiling of 0.06. A single seed can easily land out of band on a "
            "statistic that looks comfortable in the summary. That is what "
            "decides how many seeds step 3 needs.",
        ),
        # The machine-readable companion carries the preset, not a digest, so
        # the check the page told a reader to run could not be run. The digest
        # it quoted was the pt-v3-era baseline besides.
        (
            "Machine-readable companion: envelope.json carries the "
            "per-statistic detail, the gap list and the provenance. Both "
            "measurement runs report known-answer digest 992ef95d…dc185e3. If "
            "the digest in envelope.json does not match your installed wheel, "
            "this page describes a different model than the one you are "
            "running.",
            "Machine-readable companion: envelope.json carries the "
            "per-statistic detail, the gap list and the preset the figures "
            "describe. The cross-platform determinism baseline for this era "
            "is known-answer v11, digest 60d47572…0de590, which the release "
            "workflow checks inside every wheel before it uploads. If "
            "pretium.model_preset()[\"name\"] is not the preset named in "
            "envelope.json, this page describes a different model than the "
            "one you are running.",
        ),
        # What the panel does and does not certify. The book is a real
        # mechanism rather than a slippage coefficient, and none of that is
        # an empirical microstructure claim: the fourteen statistics are
        # daily price-process and volume properties.
        (
            "<li>Strategy evaluation up to about one year, where the edge "
            "depends on statistics the panel certifies</li>",
            "<li>Strategy evaluation up to about one year, where the edge "
            "depends on statistics the panel certifies, which are daily "
            "price-process, correlation and volume properties</li>",
        ),
        # Gap 4 stopped being the thin-tails gap at 0.2.0, so the bullet
        # pointing VaR at it pointed at the wrong gap. Multi-year VaR is
        # gap 2's horizon; gap 4 is the macro regime.
        (
            "<li>Long-horizon volatility memory (gap 3), or VaR at "
            "multi-year horizons (gap 4)</li>",
            "<li>Long-horizon volatility memory (gap 3), or VaR at "
            "multi-year horizons (gap 2)</li>\n              "
            "<li>Studying an inflation regime or a policy crisis the "
            "economy is left to reach on its own (gap 4)</li>",
        ),
        (
            "<li>Any claim that simulated performance forecasts live "
            "returns</li>",
            "<li>Any claim that simulated performance forecasts live "
            "returns</li>\n              "
            "<li>Execution-sensitive conclusions that rest on the order "
            "book's own realism, which nothing here certifies</li>",
        ),
        (
            "<h2 style=\"font-size:21px;margin:46px 0 10px\">Why There Is No "
            "Realism Score</h2>",
            "<h2 style=\"font-size:21px;margin:46px 0 10px\">What the Panel "
            "Does Not Cover</h2>\n        "
            "<p>All fourteen statistics are daily properties of prices, "
            "correlations and volume. Matching is a real limit order book "
            "with price-time priority rather than a slippage coefficient, and "
            "impact is emergent, which is a statement about the MECHANISM. It "
            "is not a validation claim: nothing on this page measures depth "
            "shape, cancellation intensity, queue dynamics, the intraday "
            "spread distribution, impact decay, resiliency or order-sign "
            "autocorrelation against real market data. So a strategy whose "
            "edge or cost lives inside the book is leaning on a part of this "
            "simulator the envelope does not reach, however comfortably its "
            "returns sit in band. Execution cost here is measured and "
            "self-consistent, and it is not certified realistic.</p>\n        "
            "<h2 style=\"font-size:21px;margin:46px 0 10px\">Why There Is No "
            "Realism Score</h2>",
        ),
        # Gap 5. The lever moved from 3.07x to 5.05x at the era boundary and
        # this paragraph did not, so the page said "roughly half" while the
        # release notes said four fifths of the same quantity.
        (
            "measured on the 40-name reference roster: about 3.1 times here "
            "against real markets' 6.16 (17.2% annualised below VIX 12 "
            "against 106.1% above VIX 45). Roughly half. That is not the "
            "same quantity as the 82% against 62% in a single pinned 120-day "
            "run elsewhere on this site, which is one seed on a smaller "
            "roster over a shorter window.",
            "measured from VIX 5 to VIX 65 on the 40-name reference roster "
            "over 252 days at thirty seeds: 6.04 times here against real "
            "markets' 6.16 (17.2% annualised below VIX 12 against 106.1% "
            "above VIX 45). Within about two percent, up from 5.05 times at "
            "pt-v10 and 3.07 at the default before that. That is not the "
            "same quantity as the pair of "
            "pinned 120-day runs elsewhere on this site, which is one seed "
            "on a smaller roster over a shorter window and reads about 2.8 "
            "times from VIX 15 to VIX 45. Three numbers on this site "
            "describe how violent a crisis is; check which one you are "
            "reading before quoting it.",
        ),
        (
            "<p>Two quantities, failing differently. The steady state is the "
            "ratio",
            "<p>Two quantities, and only one of them still fails. The steady "
            "state is the ratio",
        ),
        (
            "The transient: the shipped preset retains 27.6% of the previous "
            "preset's shock response, because it raised factor-variance "
            "persistence to 0.989 to buy volatility clustering, and a 63-day "
            "half-life cannot track a twenty-day spike.</p>",
            "This gap opened by calling that lever materially weak, when it "
            "read 3.07 times; pt-v10 took it to 5.05 and pt-v12 to 6.04, so "
            "what the gap is ABOUT has changed. The magnitude is no longer "
            "the defect. "
            "The dispersion around it is. Over a window driven by the real "
            "2020-21 macro path, the model's residual standard deviation is "
            "1.565 times real, down from 1.76 at pt-v10 and barely moved from "
            "1.555 at pt-v11, and that is the worst axis in this model. So "
            "the expected size of a scenario's response is calibrated and the "
            "spread around it is too wide: one run understates how much of "
            "its own move was the scenario.</p>",
        ),
        (
            "This was attacked directly and the attack failed, which is why "
            "it is a gap and no longer a task. A two-timescale variance "
            "mixture restored the transient",
            "The transient was attacked directly in the pt-v10 era and the "
            "attack failed, which is why it was left where it was; what "
            "eventually moved the lever was pt-v11's crisis work rather than "
            "a variance mixture. A two-timescale variance mixture restored "
            "the transient",
        ),
        (
            "<p style=\"color:var(--fg)\"><strong>Forbids:</strong> sizing a "
            "scenario's impact rather than detecting it. Use scenarios to ask "
            "whether a strategy breaks. A crisis here is about half as "
            "violent as a real one and arrives more slowly, so surviving one "
            "is a weaker test than the label suggests.</p>",
            "<p style=\"color:var(--fg)\"><strong>Forbids:</strong> sizing a "
            "scenario's impact rather than detecting it. Use scenarios to ask "
            "whether a strategy breaks. On average a crisis here is about as "
            "violent as a real one, within two percent on the steady-state "
            "lever, and the spread around that average is 1.565 times real, "
            "so one run is a weak reading of how hard the scenario hit.</p>",
        ),
        # The decay-shape gap. "No parameter setting turns one slope into
        # the other" is a statement about this model class. The volume-change
        # gap made the stronger claim and had to withdraw it, so the weaker
        # one says out loud which it is. That gap is named rather than
        # numbered, because it retired at pt-v12 and has no number now.
        (
            "The model fits −0.953, about 2.2 times steeper. This is a "
            "mechanism gap: no parameter setting turns one slope into the "
            "other. It was tried, and a two-component variance mixture lands "
            "lag 20 while getting lag 60 wrong in both directions at once.",
            "The model fits −0.953, about 2.2 times steeper. This is a "
            "mechanism gap: no setting of this model's parameters turns one "
            "slope into the other, because the process is built from "
            "exponentials and a sum of exponentials is not a power law. It "
            "was tried, and a two-component variance mixture lands lag 20 "
            "while getting lag 60 wrong in both directions at once. Read "
            "that as a limit of this model class rather than of the project: "
            "the retired volume-change gap carried the stronger claim, that "
            "its row was structurally unreachable, and a new mechanism "
            "reached it. A gap is closed by adding mechanism, not by tuning "
            "what is already here.",
        ),
        # Gap 6. Round-robin over twelve sectors is not five names each, and
        # the three counts below it are a pt-v3-era ten-statistic panel.
        (
            "<p><code style=\"font-size:12.5px\">Universe.random()</code> places "
            "exactly five names in each of twelve sectors. No real index is "
            "balanced that way. The S&amp;P is roughly a third technology, "
            "and the Nasdaq more so. Varying only sector composition:</p>",
            "<p><code style=\"font-size:12.5px\">Universe.random()</code> assigns "
            "sectors round-robin over the twelve in "
            "<code style=\"font-size:12.5px\">sectors.SECTORS</code>, so a "
            "roster is as close to balanced as its size allows: the certified "
            "40 names put four in each of four sectors and three in each of "
            "the other eight. No real index is balanced that way. The "
            "S&amp;P is roughly a third technology, and the Nasdaq more so. "
            "Varying only sector composition. Re-measured on pt-v12 across "
            "thirty seeds and the full fourteen-statistic panel: at the "
            "certified 252-day horizon EVERY mix tested holds all fourteen -- "
            "balanced, S&amp;P-like, technology-heavy and defensive, with an "
            "all-technology roster holding 13 of 13 because "
            "sector_excess_corr is undefined when there is only one sector. "
            "Concentration costs the SECOND year rather than the first, "
            "through cross-sectional correlation rising past its 504-day "
            "band as the roster narrows:</p>",
        ),
        # The roster table itself. The bundle carries the pt-v3-era counts,
        # 9/10, 8/10 and 7/10 out of the ten-statistic panel at six seeds,
        # under a paragraph that now says they were superseded -- so the
        # header changes with the rows: the interesting axis is no longer a
        # single in-band count but the two horizons side by side. Figures
        # from `pretium.envelope.GAPS["roster-concentration"]`, measured by
        # tools/calibration/roster_shapes.py.
        (
            '<sc-raw-thead><sc-raw-tr><sc-raw-th>Roster</sc-raw-th>'
            '<sc-raw-th style="text-align:right">In band</sc-raw-th>'
            '<sc-raw-th style="text-align:right">L_real</sc-raw-th>'
            '<sc-raw-th style="text-align:right">Vol %</sc-raw-th>'
            "</sc-raw-tr></sc-raw-thead>",
            '<sc-raw-thead><sc-raw-tr><sc-raw-th>Roster</sc-raw-th>'
            '<sc-raw-th style="text-align:right">252d</sc-raw-th>'
            '<sc-raw-th style="text-align:right">504d</sc-raw-th>'
            "<sc-raw-th>Out at 504d</sc-raw-th>"
            "</sc-raw-tr></sc-raw-thead>",
        ),
        (
            "<sc-raw-tr><sc-raw-td>balanced (the certified one)</sc-raw-td>"
            '<sc-raw-td style="text-align:right">9/10</sc-raw-td>'
            '<sc-raw-td style="text-align:right">0.0000</sc-raw-td>'
            '<sc-raw-td style="text-align:right">27.9</sc-raw-td></sc-raw-tr>'
            "\n                <sc-raw-tr><sc-raw-td>S&amp;P-like mix"
            '</sc-raw-td><sc-raw-td style="text-align:right">8/10</sc-raw-td>'
            '<sc-raw-td style="text-align:right">0.0176</sc-raw-td>'
            '<sc-raw-td style="text-align:right">27.4</sc-raw-td></sc-raw-tr>'
            "\n                <sc-raw-tr><sc-raw-td>all technology"
            '</sc-raw-td><sc-raw-td style="text-align:right">7/10</sc-raw-td>'
            '<sc-raw-td style="text-align:right">0.0043</sc-raw-td>'
            '<sc-raw-td style="text-align:right">32.8</sc-raw-td></sc-raw-tr>',
            "<sc-raw-tr><sc-raw-td>balanced (the certified one)</sc-raw-td>"
            '<sc-raw-td style="text-align:right">14/14</sc-raw-td>'
            '<sc-raw-td style="text-align:right">14/14</sc-raw-td>'
            "<sc-raw-td>none</sc-raw-td></sc-raw-tr>"
            "\n                <sc-raw-tr><sc-raw-td>S&amp;P-like mix"
            '</sc-raw-td><sc-raw-td style="text-align:right">14/14</sc-raw-td>'
            '<sc-raw-td style="text-align:right">13/14</sc-raw-td>'
            "<sc-raw-td>annualised_vol_pct</sc-raw-td></sc-raw-tr>"
            "\n                <sc-raw-tr><sc-raw-td>technology-heavy"
            '</sc-raw-td><sc-raw-td style="text-align:right">14/14</sc-raw-td>'
            '<sc-raw-td style="text-align:right">11/14</sc-raw-td>'
            "<sc-raw-td>vol, corr_persistence_acf1, cross_sectional_corr"
            "</sc-raw-td></sc-raw-tr>"
            "\n                <sc-raw-tr><sc-raw-td>all technology"
            '</sc-raw-td><sc-raw-td style="text-align:right">13/13</sc-raw-td>'
            '<sc-raw-td style="text-align:right">10/13</sc-raw-td>'
            "<sc-raw-td>vol, corr_persistence_acf1, cross_sectional_corr"
            "</sc-raw-td></sc-raw-tr>"
            "\n                <sc-raw-tr><sc-raw-td>defensive"
            '</sc-raw-td><sc-raw-td style="text-align:right">14/14</sc-raw-td>'
            '<sc-raw-td style="text-align:right">14/14</sc-raw-td>'
            "<sc-raw-td>none</sc-raw-td></sc-raw-tr>",
        ),
        (
            "<p style=\"color:var(--fg)\"><strong>Forbids:</strong> inheriting "
            "these numbers for a sector-concentrated roster. Re-measure the "
            "panel on your own universe: <code style=\"font-size:12.5px\">"
            "facts.measure()</code> takes it directly, and "
            "<code style=\"font-size:12.5px\">envelope.intervals()</code> "
            "reports the spread.</p>",
            '<p style="font:400 12px/1.6 var(--font-mono);color:var(--faint);'
            'margin:10px 0 14px">Measured: preset pt-v12 \u00b7 30 seeds \u00b7 40 '
            "instruments \u00b7 252 and 504 trading days \u00b7 "
            "tools/calibration/roster_shapes.py. The all-technology row "
            "counts thirteen because sector_excess_corr is undefined with one "
            "sector, not because it failed.</p>\n            "
            "<p>This is also the limit of the held-out universe above. That "
            "roster is another draw from the same generator, so it varies the "
            "names and not the shape. This table varies the shape, and on "
            "pt-v12 the count holds at the certified horizon and drops at "
            "504 days.</p>\n            "
            "<p style=\"color:var(--fg)\"><strong>Forbids:</strong> inheriting "
            "this envelope for a sector-concentrated roster BEYOND one year. "
            "At the certified horizon it now transfers, which it did not "
            "before pt-v12. Past that, re-measure the panel on your own "
            "universe: <code style=\"font-size:12.5px\">"
            "facts.measure()</code> takes it directly, and "
            "<code style=\"font-size:12.5px\">envelope.intervals()</code> "
            "reports the spread.</p>",
        ),
    ])

    # The gap list is renumbered to match `pretium.envelope.GAPS`, which is
    # the only list of gaps this project actually maintains. The bundle
    # numbers six in a different order; these run last so that no earlier
    # swap has to know which number a gap currently carries.
    swaps.extend([
        ("2 · The certified horizon is 252 days",
         "1 · The certified horizon is 252 days"),
        ("3 · Volatility memory has the wrong shape",
         "2 · Volatility memory has the wrong shape"),
        ("5 · Scenario response is directional, not calibrated",
         "3 · Scenario response is directional, not calibrated"),
        ("6 · Certification used a sector-balanced roster",
         "5 · Certification used a sector-balanced roster"),
        # The closing list names gaps by number, so it moves with them. The
        # volume-change entry goes rather than shifting: pt-v12 holds that
        # row at 252 days and at 504, so the site was forbidding a strategy
        # the envelope now permits.
        (
            "<li>Multi-year backtests (gap 2)</li>\n              "
            "<li>Sizing how badly a crisis hurts, as opposed to detecting "
            "that it does (gap 5)</li>\n              "
            "<li>Inheriting these numbers on a sector-concentrated roster "
            "(gap 6)</li>",
            "<li>Multi-year backtests (gap 1)</li>\n              "
            "<li>Sizing how badly a crisis hurts, as opposed to detecting "
            "that it does (gap 3)</li>\n              "
            "<li>Inheriting these numbers on a sector-concentrated roster "
            "beyond one year (gap 5)</li>",
        ),
        (
            "<li>Long-horizon volatility memory (gap 3), or VaR at "
            "multi-year horizons (gap 2)</li>\n              "
            "<li>Studying an inflation regime or a policy crisis the "
            "economy is left to reach on its own (gap 4)</li>\n              "
            "<li>Strategies trading the change in volume (gap 1)</li>",
            "<li>Long-horizon volatility memory (gap 2), or VaR at "
            "multi-year horizons (gap 1)</li>\n              "
            "<li>Studying an inflation regime or a policy crisis the "
            "economy is left to reach on its own (gap 4)</li>",
        ),
    ])

    for old, new in swaps:
        if old not in doc:
            sys.exit("the design bundle reworded a realism-page passage that "
                     f"apply_trust_fixes corrects: {old[:70]!r}")
        doc = doc.replace(old, new, 1)

    # The volume-change gap retired at pt-v12, which reads -0.2656 at 252
    # days and -0.2572 at 504, inside both bands. Dropping the block is the
    # point: reworded, it would still forbid a strategy the envelope permits,
    # and it would still hold slot 1 and push every later number out by one.
    drop("1 · The tenth statistic is structurally unreachable")
    # `GAPS` lists scenario-magnitude before macro-range and the bundle has
    # them the other way round, so the numbers above would count 1, 2, 4, 3,
    # 5 down the page. Move the block rather than the numbers: the numbering
    # a reader can check against the library is the one that has to win.
    reorder("4 · The endogenous economy cannot reach its own MACRO crisis "
            "regimes", "3 · Scenario response is directional, not calibrated")

    cut("At a 252-day horizon the shipped", "<sc-raw-tbody", "</sc-raw-tbody>",
        certified_rows())
    cut("1 · The certified horizon is 252 days", "<sc-raw-tbody",
        "</sc-raw-tbody>",
        '<sc-raw-tbody style="font-family:var(--font-mono);font-size:12px">\n'
        f"                {horizon_rows()}\n              </sc-raw-tbody>")
    # The held-out table. The certified table above it was regenerated for
    # pt-v10 and this one was not, so the page claimed fourteen of fourteen
    # in one paragraph and nine of ten three lines later. Both counts were
    # real; they were two generations of the panel printed side by side.
    cut("A model graded on the same thirty seeds", "<sc-raw-tbody",
        "</sc-raw-tbody>", HELD_OUT_ROWS)
    return doc


def apply_scenarios_fixes(doc: str) -> str:
    """Re-measure the scenarios page on the shipped preset.

    Every figure on this page came from a run, and every one of those runs
    predates the 2026-08-26 era boundary that changed what a VIX pin does.
    The page also carried a third crisis-intensity number, so a reader met
    a pinned 59%-to-107% ratio here, a 3.1x steady-state lever on the
    realism page and a 5.05x lever in the release notes, with nothing saying
    they are three different measurements. The lever has since moved again,
    to 6.04x on pt-v12, which is why the replacements below name the preset
    beside the figure instead of leaving it to be inferred.

    Re-measured on the installed 0.2.0 package by the recipes the page
    states: volatility and the crisis pair over `Universe.random(20,
    seed=11)`, spreads and correlation over `Universe.random(25, seed=11)`,
    120 days, sim seed 3, pinned through the scenario API; the rate-shock
    table over `Universe.random(20, seed=4)`, `reference_agents(seed=3)`,
    sim seed 7, 20 days. Each replacement asserts.
    """
    swaps = [
        # The rate-shock table.
        (
            "<sc-raw-tr><sc-raw-td>buy_and_hold</sc-raw-td>"
            '<sc-raw-td style="text-align:right">\u22127.16%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">\u221210.75%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">\u22123.59</sc-raw-td>'
            "</sc-raw-tr>\n            "
            "<sc-raw-tr><sc-raw-td>momentum</sc-raw-td>"
            '<sc-raw-td style="text-align:right">\u22121.17%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">\u22123.52%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">\u22122.36</sc-raw-td>'
            "</sc-raw-tr>\n            "
            "<sc-raw-tr><sc-raw-td>oracle</sc-raw-td>"
            '<sc-raw-td style="text-align:right">+11.11%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">+8.70%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">\u22122.41</sc-raw-td>'
            "</sc-raw-tr>",
            "<sc-raw-tr><sc-raw-td>buy_and_hold</sc-raw-td>"
            '<sc-raw-td style="text-align:right">\u22125.63%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">\u22128.76%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">\u22123.13</sc-raw-td>'
            "</sc-raw-tr>\n            "
            "<sc-raw-tr><sc-raw-td>momentum</sc-raw-td>"
            '<sc-raw-td style="text-align:right">\u22127.88%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">\u22129.74%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">\u22121.86</sc-raw-td>'
            "</sc-raw-tr>\n            "
            "<sc-raw-tr><sc-raw-td>oracle</sc-raw-td>"
            '<sc-raw-td style="text-align:right">+14.98%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">+12.99%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">\u22121.99</sc-raw-td>'
            "</sc-raw-tr>",
        ),
        (
            "Buy-and-hold is long only, holds through the repricing, and "
            "loses the most. Momentum and the Oracle trade around it and "
            "each give up about two and a half points. The sizes belong to "
            "the seed: across sim seeds 5 to 9 buy-and-hold gives up 3.4 to "
            "4.7 points every time, while momentum's give-up spans "
            "\u22125.1 to +0.5. Run more than one seed before you quote a "
            "size.",
            "Buy-and-hold is long only, holds through the repricing, and "
            "gives up the most. Momentum and the Oracle trade around it and "
            "each give up about two points. The sizes belong to the seed: "
            "across sim seeds 5 to 9 buy-and-hold gives up 3.0 to 5.0 points "
            "every time, while momentum's give-up spans \u22121.9 to +0.5. "
            "Run more than one seed before you quote a size.",
        ),
        # What a VIX pin actually does now.
        (
            "Realised annual volatility: 49% at VIX 5, 59% at 15, 107% at "
            "45, 124% at 65.",
            "Realised annual volatility: 31% at VIX 5, 37% at 15, 105% at "
            "45, 126% at 65.",
        ),
        (
            "Mean quoted spread goes from 11.5 bps at VIX 15 to 25.9 bps at "
            "VIX 65,",
            "Mean quoted spread goes from 11.7 bps at VIX 15 to 21.5 bps at "
            "VIX 65,",
        ),
        (
            "mean pairwise correlation reads +0.27 calm, +0.68 at VIX 45, "
            "+0.76 at 65.",
            "mean pairwise correlation reads +0.20 calm, +0.62 at VIX 45, "
            "+0.68 at 65.",
        ),
        (
            "pinned through the scenario API. What VIX never moves is a "
            "single name's own noise. These figures are much higher than the "
            '24.1% on <a href="#/trust">Realism and Limits</a> because a '
            "pinned VIX is not a normal market: the pin drives the factor "
            "variance directly, and these runs use a smaller roster over 120 "
            "days rather than the certified 40 names over 252. Compare pins "
            "against each other, and take the certified number from an "
            "unpinned run.",
            "pinned through the scenario API. Since the 2026-08-26 era "
            "boundary VIX also moves a name's own variance, through "
            "<code style=\"font-size:12px\">garch_vix_coupling</code>; before "
            "it, VIX sized only the shared factor. These figures straddle "
            'the 32.76% on <a href="#/trust">Realism and Limits</a> rather '
            "than sitting near it, the calm pins bracketing it and the "
            "crisis pins running three to four times higher, because a "
            "pinned VIX is "
            "not a normal market: the pin drives the factor "
            "variance directly, and these runs use a smaller roster over 120 "
            "days rather than the certified 40 names over 252. Compare pins "
            "against each other, and take the certified number from an "
            "unpinned run. The ratio between two pins is NOT the crisis "
            "lever the realism page and the release notes quote: that one "
            "runs VIX 5 to VIX 65 on the certified roster over 252 days at "
            "thirty seeds and reads 6.04x on pt-v12. Three numbers, three "
            "measurements.",
        ),
        # The liquidity-crisis pair, in the comment the example prints.
        (
            "# 61.76% calm, 82.16% crisis",
            "# 39.98% calm, 67.68% crisis",
        ),
        # The whole-crisis-study block. Every line in it is now a real run,
        # including the two the note called composed, so the badge moves.
        (
            '<span title="Medians and verdict text are quoted from the '
            "shipped envelope; the spread columns and strategy numbers were "
            'composed for the example." style="font:500 9.5px/1 '
            "var(--font-mono);letter-spacing:0.07em;border-radius:5px;"
            "padding:3px 6px;margin-left:8px;color:var(--codemut);border:1px "
            'solid var(--codeline)">MIXED, SEE NOTE</span></div>\n'
            '          <pre style="padding:13px 16px;overflow-x:auto"><code '
            'data-lang="txt" style="font:400 12.5px/1.75 var(--font-mono);'
            "color:var(--codemut)\">{'day': 0, 'vix': 18.0}",
            '<span title="Captured from the run named beside this block." '
            'style="font:500 9.5px/1 var(--font-mono);letter-spacing:0.07em;'
            "border-radius:5px;padding:3px 6px;margin-left:8px;color:"
            'var(--tks);border:1px solid var(--tks)">MEASURED</span></div>\n'
            '          <pre style="padding:13px 16px;overflow-x:auto"><code '
            'data-lang="txt" style="font:400 12.5px/1.75 var(--font-mono);'
            "color:var(--codemut)\">{'day': 0, 'vix': 18.0}",
        ),
        (
            "{'day': 1, 'vix': 18.0}\n...\n{'day': 20, 'vix': 18.0}\n"
            "{'day': 21, 'vix': 19.03}\n{'day': 22, 'vix': 20.07}\n"
            "{'day': 23, 'vix': 21.10}\n{'day': 24, 'vix': 22.13}\n\n"
            "inside the envelope\n  - horizon 120d is within the certified "
            "252d, and no named statistic\n    meets a measured gap\n  ? "
            "annualised_vol_pct is in band at the certified horizon (24.0972 "
            "in\n    (15.0, 36.0)) -- but that is a median across 30 seeds; "
            "check\n    `intervals` for the spread before relying on one "
            "seed\n  ? cross_sectional_corr is in band at the certified "
            "horizon (0.2558 in\n    (0.08, 0.56)) -- but that is a median "
            "across 30 seeds; check\n    `intervals` for the spread before "
            "relying on one seed\n\n(82.16, 61.76)      # annualised vol %, "
            "crisis against calm\n0.6781              # cross-sectional "
            "correlation under the crisis\n\ncalm    0.1142   -0.0871\n"
            "crisis  0.0316   -0.2043\n\n6.06 11.69          # shortfall "
            "bps, calm then VIX 45",
            "{'day': 1, 'vix': 18.0}\n...\n{'day': 19, 'vix': 18.0}\n"
            "{'day': 20, 'vix': 80.0}\n{'day': 21, 'vix': 78.97}\n"
            "{'day': 22, 'vix': 77.93}\n{'day': 23, 'vix': 76.90}\n"
            "{'day': 24, 'vix': 75.87}\n\n"
            "inside the envelope\n  - horizon "
            "120d is within the certified 252d, and no named statistic\n    "
            "meets a measured gap\n  ? annualised_vol_pct is in band at the "
            "certified horizon (31.4632 in\n    (15.0, 36.0)) -- but that is "
            "a median across 30 seeds; check\n    `intervals` for the spread "
            "before relying on one seed\n  ? cross_sectional_corr is in band "
            "at the certified horizon (0.3063 in\n    (0.08, 0.56)) -- but "
            "that is a median across 30 seeds; check\n    `intervals` for the "
            "spread before relying on one seed\n\n(67.68, 39.98)      # "
            "annualised vol %, crisis against calm\n0.6235              # "
            "cross-sectional correlation under the crisis\n\n"
            "calm   -42.9347  11.9232\ncrisis -56.6869  95.0928\n\n"
            "6.06 11.69          # shortfall bps, calm then VIX 45",
        ),
        (
            "In that block the volatility pair and the two shortfall figures "
            "are measured runs, cited in the prose below. The scenario table "
            "rows and the two strategy lines are composed to show the shape "
            "of the output.",
            "Every line in that block is a measurement on pretium 0.2.0 "
            "under pt-v10, the default before 0.3.0, from the code above it "
            "and left as measured rather than relabelled. The scenario rows are "
            "elided in the middle and rounded to two decimals; nothing else "
            "is edited. Note what the path actually does: "
            "<code style=\"font-size:12.5px\">vix_shock</code> puts the peak "
            "on day <code style=\"font-size:12.5px\">at</code> and decays it "
            "over the window, rather than ramping up to it.",
        ),
        (
            "Volatility goes from 62% to 82%, and correlation climbs with "
            "it.",
            "Volatility goes from 40% to 68%, and correlation climbs with "
            "it, 0.49 to 0.62.",
        ),
        # The two "half as violent" lines. Both quoted the pt-v3-era lever
        # and both indexed into a six-gap list that no longer exists.
        #
        # Two corrections, not one. The NUMBER: the steady-state crisis
        # lever reads 6.04x on pt-v12 against real markets' 6.16x
        # (docs/realism-envelope.md:245-251, and the same pair in
        # `pretium.envelope.GAPS` under `scenario-magnitude`), so "four
        # fifths" -- itself the pt-v10 5.05x -- understates the shipped
        # preset by a factor a reader would act on. The INDEX: there are
        # five gaps now, `[g.id for g in envelope.GAPS]` gives horizon,
        # decay-shape, scenario-magnitude, macro-range,
        # roster-concentration, so this one is gap 3. trust.html's own list
        # is renumbered to match in apply_trust_fixes.
        #
        # What the gap is ABOUT changed with the number, so the prose has to
        # change with it rather than just the digits: the expected size of a
        # scenario's response is calibrated now and the dispersion around it
        # is not. "Never size losses" becomes "never size them from one run".
        (
            "And gap 5 on the realism page: scenario response is "
            "directional, not sized. A crisis here is about half as violent "
            "as a real one. Use scenarios to detect breakage, never to size "
            "losses.",
            "And gap 3 on the realism page: a scenario's size is right on "
            "average and unreliable in one run. The steady-state lever, a "
            "sustained crisis against a calm market, reads 6.04x here "
            "against 6.16x for real markets on the certified roster, which "
            "is within two percent; what is not calibrated is the spread "
            "around it, so a single run understates how much of its own "
            "move was the scenario. Use scenarios to detect breakage, and "
            "size a loss across many seeds or not at all.",
        ),
        (
            "Do not quote the sizes as real. A crisis here is roughly half "
            "as violent as a real one (gap 5). Read the direction and the "
            "ordering; leave the magnitudes alone.",
            "Do not quote one run's sizes as real. The steady-state crisis "
            "lever is calibrated on average, 6.04x against a real 6.16x, "
            "and the dispersion around it is not (gap 3); the pinned pair "
            "above is a different measurement again. Read the direction and "
            "the ordering, and take a magnitude from many seeds rather than "
            "from this one.",
        ),
    ]
    for old, new in swaps:
        if old not in doc:
            sys.exit("the design bundle reworded a scenarios-page passage "
                     f"that apply_scenarios_fixes corrects: {old[:70]!r}")
        doc = doc.replace(old, new, 1)
    return doc


def apply_internals_fixes(doc: str) -> str:
    """Cross the "Under the Hood" page over the 0.2.0 era boundary.

    The page describes how the CURRENT default behaves, and it was written
    against pt-v3: it named pt-v3 as the shipped model, quoted that preset's
    momentum coefficient, and carried a pinned-VIX response measured before
    the era boundary rewired four of the mechanisms it describes. A page that
    explains the engine while quoting a superseded preset's numbers is a
    plausible and wrong explanation of what a reader is running.

    The replacement figures are measured on the installed 0.2.0 package by
    the recipe the page already states: annualised volatility over
    `Universe.random(20, seed=11)`, correlation over
    `Universe.random(25, seed=11)`, 120 days, sim seed 3, VIX pinned through
    the scenario API. Each replacement asserts.

    Two coefficients in those replacements were themselves an era behind, and
    are corrected here rather than left to a reader to trip over. The shipped
    `sector_vix_coupling` is 1.0, not the 0.25 pt-v7 and pt-v10 carried:
    pt-v11's crisis work quadrupled it (`rust/src/params.rs:1663`), and
    `ModelParams.from_preset("pt-v12").to_dict()["sector_vix_coupling"]` reads
    1.0. And the certified annualised volatility is 32.7604, not 31.5:
    `pretium.envelope.CERTIFIED["annualised_vol_pct"]` gives 32.7604, which is
    what the trust page's certified table publishes as 32.76, so quoting 31.5
    here left two pages of one site disagreeing about one measurement.

    The RNG-streams section is the third correction. The engine has seven
    generator streams, not three (`rust/src/rng.rs` `mod stream`, ids 0 to 6),
    and a live `Engine.state_snapshot()["rng"]` is 21 numbers, three per
    stream. `Engine.draws_by_stream()` still reports only the first three, so
    the sentence that cites it says so rather than implying seven counts come
    back.
    """
    swaps = [
        (
            "the shipped <code style=\"font-size:13px\">pt-v3</code> turns the "
            "term down to 0.0742 and measures +0.0375, inside the band.",
            "the shipped <code style=\"font-size:13px\">pt-v12</code> turns the "
            "term down to 0.0186 and measures +0.0239, inside the band.",
        ),
        (
            "On top sits a shared market factor with its own conditional "
            "variance, coupled to VIX. The factor's variance reverts to a "
            "target scaling with (VIX/15)\u00b2, so a pinned VIX moves "
            "realised volatility: 49% annualised at VIX 5, 59% at 15, 107% "
            "at 45, 124% at 65. Above VIX 25.5 the cross-section blends "
            "toward the factor, and mean pairwise correlation reads +0.27 "
            "calm, +0.68 at VIX 45, +0.76 at 65. Diversification fails under "
            "stress the way real crises make it fail.",
            "On top sits a shared market factor with its own conditional "
            "variance, coupled to VIX. The factor's variance reverts to a "
            "target scaling with (VIX/15)\u00b2, so a pinned VIX moves "
            "realised volatility: 31% annualised at VIX 5, 37% at 15, 105% "
            "at 45, 126% at 65. Above VIX 25.5 the cross-section blends "
            "toward the factor, and mean pairwise correlation reads +0.20 "
            "calm, +0.62 at VIX 45, +0.68 at 65. Diversification fails under "
            "stress the way real crises make it fail. Since the 2026-08-26 "
            "era boundary the coupling is not the factor's alone: a name's "
            "own GJR-GARCH variance follows the VIX too "
            "(<code style=\"font-size:13px\">garch_vix_coupling</code> 0.3), "
            "the sector draw's variance follows the regime in full "
            "(<code style=\"font-size:13px\">sector_vix_coupling</code> 1.0, "
            "which was a quarter under pt-v10 and was quadrupled by pt-v11's "
            "crisis work), "
            "the VIX's own fear channel reads the day's index return rather "
            "than the closing minute, the volatility regimes come off the "
            "market instead of the business cycle, and the factor's variance "
            "clamp sits at 32x its baseline rather than 8x. Descriptions of "
            "these mechanics written for pt-v3 do not carry over.",
        ),
        (
            "Those levels are far above the 24.1% certified panel because a "
            "pinned VIX is not a normal market: the pin drives the factor "
            "variance directly, and these runs use a smaller roster over 120 "
            "days rather than the certified 40 names over 252.",
            "Those levels straddle the certified panel's 32.76% rather than "
            "sitting near it, the two calm pins bracketing it and the two "
            "crisis pins running three to four times higher, because a "
            "pinned VIX is not a "
            "normal market: the pin drives the factor "
            "variance directly, and these runs use a smaller roster over 120 "
            "days rather than the certified 40 names over 252. The ratio "
            "between two of them is also not the crisis lever the realism "
            "page quotes, which is measured on the certified roster.",
        ),
        # The book is a mechanism claim. It is not an empirical
        # microstructure claim, and the page was inviting the second reading.
        (
            "Matching runs against a limit order book with price-time "
            "priority. You get queue position, partial fills, and a quoted "
            "spread that widens under stress. Impact is emergent: a large "
            "order pays worse prices because it consumed levels, and there is "
            "no slippage coefficient anywhere in the code.",
            "Matching runs against a limit order book with price-time "
            "priority. You get queue position, partial fills, and a quoted "
            "spread that widens under stress. Impact is emergent: a large "
            "order pays worse prices because it consumed levels, and there is "
            "no slippage coefficient anywhere in the code. Read that as a "
            "statement about the mechanism, not about calibration: the "
            "realism envelope measures daily price, correlation and volume "
            "statistics, and nothing in it grades depth shape, cancellation "
            "intensity, queue dynamics, the intraday spread distribution, "
            "impact decay, resiliency or order-sign autocorrelation against "
            "real market data. The book is real and it is not certified.",
        ),
        # RNG streams. The page was authored against the original three-way
        # split and the engine has had seven streams since the volume and
        # news mechanisms landed. Ids and duties are read straight off
        # `rust/src/rng.rs` `mod stream`: MARKET 0, ECONOMY 1, EXTERNAL 2,
        # JUMPS 3, VOLUME 4, NEWS 5, VOLUME_IDIO 6. Three numbers per stream
        # is why a live snapshot's `rng` array is 21 long.
        (
            "One seed drives the whole simulation, through three independent "
            "substreams, so changing what one consumer draws cannot shift "
            "any other consumer's sequence.",
            "One seed drives the whole simulation, through seven independent "
            "substreams, so changing what one consumer draws cannot shift "
            "any other consumer's sequence. Four of the seven exist because "
            "of that rule rather than in spite of it: a mechanism that "
            "consumes draws could not have been added to a shared stream at "
            "all without shifting every later draw and moving every shipped "
            "preset's trajectory, so jumps, volume, news and per-name volume "
            "each got a stream of their own and each arrived inert.",
        ),
        (
            '<sc-raw-tr><sc-raw-td style="font-family:var(--font-mono);font-size:12.5px">external</sc-raw-td><sc-raw-td style="text-align:right;font-family:var(--font-mono);font-size:12.5px">2</sc-raw-td><sc-raw-td>the embedder, through Engine.draw_uniform() / draw_normal()</sc-raw-td></sc-raw-tr>',
            '<sc-raw-tr><sc-raw-td style="font-family:var(--font-mono);font-size:12.5px">external</sc-raw-td><sc-raw-td style="text-align:right;font-family:var(--font-mono);font-size:12.5px">2</sc-raw-td><sc-raw-td>the embedder, through Engine.draw_uniform() / draw_normal()</sc-raw-td></sc-raw-tr>\n            <sc-raw-tr><sc-raw-td style="font-family:var(--font-mono);font-size:12.5px">jumps</sc-raw-td><sc-raw-td style="text-align:right;font-family:var(--font-mono);font-size:12.5px">3</sc-raw-td><sc-raw-td>endogenous jumps, drawn once per name per day at the close</sc-raw-td></sc-raw-tr>\n            <sc-raw-tr><sc-raw-td style="font-family:var(--font-mono);font-size:12.5px">volume</sc-raw-td><sc-raw-td style="text-align:right;font-family:var(--font-mono);font-size:12.5px">4</sc-raw-td><sc-raw-td>the common persistent volume component, drawn once per day at the close</sc-raw-td></sc-raw-tr>\n            <sc-raw-tr><sc-raw-td style="font-family:var(--font-mono);font-size:12.5px">news</sc-raw-td><sc-raw-td style="text-align:right;font-family:var(--font-mono);font-size:12.5px">5</sc-raw-td><sc-raw-td>endogenous company news, drawn once per name per day at the open</sc-raw-td></sc-raw-tr>\n            <sc-raw-tr><sc-raw-td style="font-family:var(--font-mono);font-size:12.5px">volume_idio</sc-raw-td><sc-raw-td style="text-align:right;font-family:var(--font-mono);font-size:12.5px">6</sc-raw-td><sc-raw-td>per-name volume persistence, drawn once per name per day</sc-raw-td></sc-raw-tr>',
        ),
        (
            "The market stream's schedule is a pure function of market "
            "status, active roster and sector count, so no price, no macro "
            "value and no order flow can move its position. "
            "<code style=\"font-size:13px\">Engine.draws_by_stream()</code> "
            "reports the counts.",
            "The market stream's schedule is a pure function of market "
            "status, active roster and sector count, so no price, no macro "
            "value and no order flow can move its position. "
            "<code style=\"font-size:13px\">Engine.draws_by_stream()</code> "
            "reports the counts for the first three, which are the ones a "
            "TCA subtraction compares; the four later streams are diagnosed "
            "through the snapshot instead, where each occupies three of the "
            "21 numbers in "
            "<code style=\"font-size:13px\">state_snapshot()[\"rng\"]</code>.",
        ),
    ]
    for old, new in swaps:
        if old not in doc:
            sys.exit("the design bundle reworded an internals-page passage "
                     f"that apply_internals_fixes corrects: {old[:70]!r}")
        doc = doc.replace(old, new, 1)
    return doc


def apply_factors_fixes(doc: str) -> str:
    """Give the components page the two columns the engine gained.

    The page was authored with seven components and still listed seven after
    the title was corrected to nine, which is worse than either: a reader
    counting the table found seven under a heading that said nine. Both
    additions are real columns of `truth()` and members of `Engine.FACTORS`.

    Its worked example was the sharper problem. It summed seven columns over
    a FIVE-day run and asserted a residual around 1e-16, which held only
    because five days is too short for a jump to fire. Measured on the same
    seed over 120 days, with 8 jump ticks, the seven-column sum is off by
    0.128 while the nine-column sum reads 6.75e-17. A check that passes by
    being too short to exercise the thing it checks is worse than no check.

    Both residuals are re-measured on 0.3.0 by running the page's own snippet
    verbatim -- `Engine(seed=42, universe=Universe.random(20, seed=11))`,
    `run_days(120)`, instrument_id 0, summed in the page's own FACTORS order.
    It prints 8 jump ticks, 0.12799165416224142 across seven columns and
    6.754579534584693e-17 across nine. The nine-column figure had been
    published as 5.0e-17, which is not what the snippet beside it produces.
    """
    swaps = [
        (
            "In order. The first three are the model\u2019s own dynamics; the "
            "last four are shocks.",
            "In order. The first three are the model\u2019s own dynamics, the "
            "next four are shocks, and the last two are discrete events the "
            "engine books after the tick chain.",
        ),
        (
            "Difference <code style=\"font-size:13px\">mispricing_s</code> "
            "across two ticks, add the seven columns, and the residual should "
            "be around 1e-16.",
            "Difference <code style=\"font-size:13px\">mispricing_s</code> "
            "across two ticks, add the nine columns, and the residual should "
            "be around 1e-16.",
        ),
        (
            "engine.run_days(5)\ntruth = pl.from_arrow(engine.truth())\n\n"
            'FACTORS = ["reversion", "momentum", "crowd_lean", "company_news",\n'
            '           "order_flow_impact", "short_squeeze_effect", "random_noise"]',
            "engine.run_days(120)   # long enough for a jump to fire\n"
            "truth = pl.from_arrow(engine.truth())\n\n"
            'FACTORS = ["reversion", "momentum", "crowd_lean", "company_news",\n'
            '           "order_flow_impact", "short_squeeze_effect", "random_noise",\n'
            '           "circuit_breaker", "jump"]                 # Engine.FACTORS',
        ),
        (
            "Where the residual is not zero, the mispricing clamp or a circuit "
            "breaker bound. That is worth seeing, and it would be invisible in "
            "a summary.",
            "Run it over five days instead of 120 and the seven-column version "
            "of this check also passes, because five days is too short for a "
            "jump to fire. On this seed over 120 days, with 8 jump ticks, "
            "seven columns are off by 0.128 and nine by 6.75e-17. Where a "
            "residual does remain, the mispricing clamp bound; the circuit "
            "breaker has had its own column since 2026-08-26 and is no longer "
            "part of it.</p>\n        <p>Which is also a caveat on the older "
            "promise, and it is stated here rather than left for someone to "
            "rediscover. Before 2026-08-26 the identity held on every day "
            "neither mechanism fired and did not hold on the days they did: "
            "any day a jump landed, on any preset from "
            "<code style=\"font-size:12.5px\">pt-v4</code> onward, and any day "
            "the session circuit breaker bound, on any preset at all. So a "
            "ground-truth decomposition published from a 0.1.x release is "
            "exact except on those days, and the columns were never wrong, "
            "only incomplete. Both are columns now and the identity holds "
            "through a crisis.",
        ),
        # The last count of seven left on the page, in the Scope section, one
        # paragraph below a table this function has just made nine rows long
        # and under a heading ERA_FIXES retitles "The Nine Components".
        # `len(pretium.Engine.FACTORS)` is 9 and `truth()` carries nine
        # component columns (`rust/src/python_arrow.rs`), so a name that did
        # not tick has nine zeros, not seven.
        (
            "A name that did not tick has zeros across all seven, because "
            "nothing contributed to a move it did not make.",
            "A name that did not tick has zeros across all nine, because "
            "nothing contributed to a move it did not make.",
        ),
    ]
    for old, new in swaps:
        if old not in doc:
            sys.exit("the design bundle reworded a components-page passage "
                     f"that apply_factors_fixes corrects: {old[:70]!r}")
        doc = doc.replace(old, new, 1)

    rows = (
        '<sc-raw-tr><sc-raw-td style="font-family:var(--font-mono);'
        'font-size:12.5px">circuit_breaker</sc-raw-td><sc-raw-td>The session '
        'circuit breaker\u2019s own correction. When the model price leaves '
        'the allowed band the tick re-derives the state from the clamped '
        'price, and until 2026-08-26 that rewrite was booked to nobody, so on '
        'any day the breaker bound the columns did not reconstruct the move.'
        '</sc-raw-td></sc-raw-tr>\n            '
        '<sc-raw-tr><sc-raw-td style="font-family:var(--font-mono);'
        'font-size:12.5px">jump</sc-raw-td><sc-raw-td>The daily jump, applied '
        'after the tick loop rather than inside it, and recorded on the tick '
        'where it is first observed. Every preset from pt-v4 carries jumps, '
        'and without this column those days never reconstructed.'
        '</sc-raw-td></sc-raw-tr>'
    )
    # Anchored on a short unique phrase and the row end that follows it,
    # rather than on the whole row: the bundle uses typographic apostrophes
    # and matching them through two layers of escaping is how this function
    # failed the first time it was written.
    probe = "The idiosyncratic draw, scaled by the name"
    if probe not in doc:
        sys.exit("the components table's random_noise row was reworded; "
                 "apply_factors_fixes cannot append the two new rows")
    cut = doc.index("</sc-raw-tr>", doc.index(probe)) + len("</sc-raw-tr>")
    return doc[:cut] + "\n            " + rows + doc[cut:]

def apply_guide_fixes(doc: str) -> str:
    """Correct the three guide pages the bundle left an era behind.

    Grouped because they share one cause with the reference pages: the bundle
    was authored when pt-v3 was the default and `examples/` had different
    filenames, and the three pages each invite a reader to run something and
    compare. A guide page is the worst place for a stale identity, because
    the reader has the real answer on their own screen.

    RUNNING A MARKET. Two spots named pt-v3 as the shipped preset. The
    "limits" box quoted pt-v3's return autocorrelation, +0.0375, with a
    provenance line attaching the certification method to that preset, and
    the worked year's OUTPUT block printed `pt-v3` from an
    `Engine(seed=42, universe=universe)` that passes no `model=` and
    therefore runs the default. On 0.3.0 that engine's `model_fingerprint`
    is `pt-v12`, `ModelParams.from_preset("pt-v12").to_dict()`
    ["momentum_theta"] is 0.0185515625 against pt-v3's 0.0742062, and
    `pretium.envelope.CERTIFIED["return_acf1"]` is 0.0239. The OUTPUT block
    keeps its ILLUSTRATIVE badge, which covers composed numbers; it does not
    cover a preset name, which is an identity and the one thing the
    fingerprint mechanism exists to make checkable.

    THE AGENT HARNESS. Two corrections. The `explain()` sentence said the
    answer is one of SEVEN names while linking to a page ERA_FIXES retitles
    "The Nine Components" in the same clause, and `len(Engine.FACTORS)` is 9.

    The bigger one is the worked agent's OUTPUT block, which was composed and
    composed WRONG in shape rather than in value. The code splats
    `pt.reference_agents(seed=3)`, which is five agents --
    `['buy_and_hold', 'random', 'momentum', 'mean_reversion', 'oracle']` --
    so the loop prints six rows and the block showed five, with `random`
    missing. And it showed `errors=0` on every row directly above a paragraph
    telling the reader to read `errors` first. Run verbatim on 0.3.0 the
    ValueAgent breaches the default `max_leverage=2.0` from its first sizing
    decision: sizing at 2% of average volume across five names is far more
    than a $1,000,000 account carries, so 56 orders are rejected and 5 fill.
    The composed block was showing a healthy run of an agent that does not
    have one. Replaced with the real output, badge moved to MEASURED, and the
    paragraph beneath now teaches both failure shapes rather than one.

    AN LLM AGENT. `examples/claude_agent.py` does not exist and never did on
    any tag; the file is `examples/08-claude-agent.py`, and its own header
    documents that invocation. The path appeared twice in the bundle, in the
    subtitle and in the shell block, and the subtitle is what
    `description_for()` turns into the meta description, the Open Graph and
    Twitter descriptions and the JSON-LD, so the broken path was also what a
    search result showed. Fixing the subtitle fixes all four.
    """
    swaps = [
        # -- Running a market --------------------------------------------
        (
            "The shipped <code style=\"font-size:12px\">pt-v3</code> preset "
            "turns that dial well down: return autocorrelation at lag one "
            "reads +0.0375 against a real band of −0.08 to 0.06, so it "
            "is in band. Earlier presets did not. "
            "<code style=\"font-size:12px\">pt-v1</code> ships the same knob "
            "at 0.25 and measures +0.249, roughly a fifth of it.",
            "The shipped <code style=\"font-size:12px\">pt-v12</code> preset "
            "turns that dial well down: "
            "<code style=\"font-size:12px\">momentum_theta</code> sits at "
            "0.0186 and return autocorrelation at lag one "
            "reads +0.0239 against a real band of −0.08 to 0.06, so it "
            "is in band. Earlier presets did not. "
            "<code style=\"font-size:12px\">pt-v1</code> ships the same knob "
            "at 0.25, thirteen times higher, and measures +0.249, which is "
            "ten times the shipped figure and outside the band rather than "
            "inside it.",
        ),
        (
            "<span style=\"font:400 11px/1.5 var(--font-mono);color:var("
            "--faint)\">pt-v3: 30 seeds, 40 instruments, 252 days. pt-v1: "
            "median of six seeds, Universe.random(40, seed=111), 252 days, "
            "sim seeds 1-6.</span>",
            "<span style=\"font:400 11px/1.5 var(--font-mono);color:var("
            "--faint)\">pt-v12: the certified panel, 30 seeds, 40 "
            "instruments, 252 days. pt-v1: median of six seeds, "
            "Universe.random(40, seed=111), 252 days, sim seeds 1-6.</span>",
        ),
        (
            "# 1.\n30 4c1e9f77a3b8d502\npt-v3\n\n# 2.",
            "# 1.\n30 4c1e9f77a3b8d502\npt-v12\n\n# 2.",
        ),
        # -- The agent harness -------------------------------------------
        (
            "as one of the seven names in",
            "as one of the nine names in",
        ),
        (
            "<span title=\"Shapes and field names are real; the numeric "
            "values were composed for the example.\" style=\"font:500 "
            "9.5px/1 var(--font-mono);letter-spacing:0.07em;border-radius:"
            "5px;padding:3px 6px;margin-left:8px;color:var(--codemut);"
            "border:1px solid var(--codeline)\">ILLUSTRATIVE</span></div>\n"
            "          <pre style=\"padding:13px 16px;overflow-x:auto\">"
            "<code data-lang=\"txt\" style=\"font:400 12.5px/1.75 "
            "var(--font-mono);color:var(--codemut)\">"
            "value              +3.14%  trades=  75 errors=0 why=0.35\n"
            "buy_and_hold       +1.02%  trades=  30 errors=0 why=None\n"
            "momentum           +8.77%  trades= 240 errors=0 why=None\n"
            "mean_reversion     -1.44%  trades= 240 errors=0 why=None\n"
            "oracle            +42.10%  trades= 240 errors=0 why=1.0"
            "</code></pre>",
            "<span title=\"Captured from the run named beside this block.\" "
            "style=\"font:500 "
            "9.5px/1 var(--font-mono);letter-spacing:0.07em;border-radius:"
            "5px;padding:3px 6px;margin-left:8px;color:var(--tks);"
            "border:1px solid var(--tks)\">MEASURED</span></div>\n"
            "          <pre style=\"padding:13px 16px;overflow-x:auto\">"
            "<code data-lang=\"txt\" style=\"font:400 12.5px/1.75 "
            "var(--font-mono);color:var(--codemut)\">"
            "value             -19.99%  trades=   5 errors=56 why=0.0\n"
            "buy_and_hold       -5.01%  trades=  30 errors=0 why=None\n"
            "random             -2.70%  trades=3583 errors=0 why=None\n"
            "momentum           +0.26%  trades=1485 errors=0 why=None\n"
            "mean_reversion    +13.44%  trades=1606 errors=0 why=None\n"
            "oracle            +15.68%  trades=1124 errors=0 why=1.0"
            "</code></pre>",
        ),
        (
            "<p>Read <code style=\"font-size:13px\">errors</code> first. A "
            "silent guard bug shows up as a suspiciously low "
            "<code style=\"font-size:13px\">trades</code> count with an "
            "empty error list, which is the trap at the top of this page. "
            "Then remember the oracle reads the true mispricing, so it is a "
            "reference rather than a competitor, and one seed ranks the seed "
            "as much as the agents. Take it to "
            "<a href=\"#/simulate\">many markets</a> before you believe an "
            "ordering.</p>",
            "<p style=\"font:400 12px/1.6 var(--font-mono);color:var(--faint)"
            ";margin:10px 0 14px\">Measured: pretium 0.3.0, pt-v12, the code "
            "above it run verbatim. Nothing is edited, which is the point of "
            "the block.</p>\n        "
            "<p>Read <code style=\"font-size:13px\">errors</code> first, and "
            "both failure shapes are in front of you. The QUIET one is a "
            "guard bug: a suspiciously low "
            "<code style=\"font-size:13px\">trades</code> count with an "
            "EMPTY error list, which is the trap at the top of this page. "
            "The LOUD one is the <code style=\"font-size:13px\">value</code> "
            "row here, 5 trades against 56 errors, every one of them "
            "<code style=\"font-size:12.5px\">trade would take leverage to "
            "N above the 2.00x limit</code>. Sizing at 2% of average volume "
            "across five names asks for far more than a $1,000,000 account "
            "can carry, so the harness rejects the order instead of filling "
            "it. That agent is not a bad strategy, it is an unsized one, and "
            "its <code style=\"font-size:13px\">why=0.0</code> is the same "
            "story: it answers "
            "<code style=\"font-size:12.5px\">\"reversion\"</code> every day "
            "whatever moved. "
            "Then remember the oracle reads the true mispricing, so it is a "
            "reference rather than a competitor, and one seed ranks the seed "
            "as much as the agents. Take it to "
            "<a href=\"#/simulate\">many markets</a> before you believe an "
            "ordering.</p>",
        ),
    ]
    for old, new in swaps:
        if old not in doc:
            sys.exit("the design bundle reworded a guide-page passage "
                     f"that apply_guide_fixes corrects: {old[:70]!r}")
        doc = doc.replace(old, new, 1)

    # -- An LLM agent ----------------------------------------------------
    # Twice in the bundle, and the subtitle occurrence is also the source of
    # four head-tag descriptions, so this one replaces every occurrence
    # rather than the first.
    if "examples/claude_agent.py" not in doc:
        sys.exit("the design bundle no longer names examples/claude_agent.py; "
                 "apply_guide_fixes would silently skip the path correction")
    doc = doc.replace("examples/claude_agent.py", "examples/08-claude-agent.py")
    return doc


def apply_api_fixes(doc: str) -> str:
    """Bring the four API reference pages onto the shipped surface.

    These pages are the ones a reader consults to decide what a call
    returns, so a stale figure here is not a stale anecdote: it is a wrong
    answer about the installed wheel. The bundle was authored in the pt-v3
    era and the four pages still described that era's panel size, gap
    count, default preset, variance clamp and volume verdict.

    Every replacement below is checked against the installed package rather
    than against another page, and every one asserts: a bundle revision that
    reworded any of them fails the build instead of silently restoring the
    pt-v3 reading.
    """
    swaps = [
        # ── api-core ────────────────────────────────────────────────────
        # bars() has two grains, not four. `minutes=` is the third spelling
        # and it is an aggregation of tick grain rather than a grain of its
        # own, which is why the two cannot be passed together. Measured:
        # grain="hour"/"minute"/"5min"/"week" each raise `unknown grain
        # "...". Valid: "tick", "day", or minutes=N`, and grain= with
        # minutes= raises `pass either minutes or grain, not both`.
        # docs/schemas.html already said this; the API page did not.
        (
            'bars(grain="day")</sc-raw-td><sc-raw-td>OHLCV as Arrow. '
            "Four granularities.</sc-raw-td>",
            'bars(grain="day")</sc-raw-td><sc-raw-td>OHLCV as Arrow. Two '
            'grains, <code style="font-size:12px">"tick"</code> and '
            '<code style="font-size:12px">"day"</code>, plus '
            '<code style="font-size:12px">minutes=N</code> to bucket ticks '
            "into bars of that many minutes. Any other grain raises, and "
            "grain and minutes cannot both be passed.</sc-raw-td>",
        ),
        # Sectors are assigned round-robin over the twelve, so the per-sector
        # count is n/12 and only n=60 divides evenly. Measured:
        # Counter(i.sector for i in Universe.random(40, seed=111)) is four
        # sectors of 4 and eight of 3; at n=60 it is twelve of 5. The
        # certified roster is the 40-name one, so the wrong version of this
        # sentence described a roster nobody runs.
        (
            "Generated roster, five names per sector, plausible "
            "fundamentals. The universe seed is a separate input domain "
            "from the simulation seed.",
            "Generated roster, plausible fundamentals. Sectors are handed "
            "out round-robin over the twelve, so each holds n/12 names give "
            "or take one: the certified 40-name roster puts four in four "
            "sectors and three in the other eight, and only n=60 gives five "
            "each. The universe seed is a separate input domain from the "
            "simulation seed.",
        ),
        # ── api-realism ─────────────────────────────────────────────────
        # The panel grew to fourteen on 2026-08-25. This row is the
        # reference for the function that returns it.
        (
            "Run a market and report the ten-statistic panel. Carries model "
            "and universe fingerprints.",
            "Run a market and report the fourteen-statistic panel. Carries "
            "model and universe fingerprints.",
        ),
        # STRUCTURAL is five statistics, not one, and "unreachable" is a
        # withdrawn claim. `pretium/loss.py` states the rule the row was
        # getting backwards: membership is about the OBJECTIVE, not about
        # reachability, and two of the five are now held in band by the
        # shipped preset (volume_change_acf1 -0.2656 against -0.32 to
        # -0.20, sector_excess_corr 0.2079 against 0.11 to 0.23).
        (
            "Loss membership as data: five targets, four constraints, and "
            "the unreachable statistic reported in every result and "
            "excluded from the objective.",
            "Loss membership as data: five targets, four constraints, and "
            "the five STRUCTURAL statistics reported in every result and "
            "excluded from the objective. Membership is about the "
            "objective, not about reachability: two of those five, "
            "volume_change_acf1 and sector_excess_corr, were called "
            "structurally unreachable until 0.2.0 and the shipped preset "
            "holds both in band at the certified horizon.",
        ),
        # Five gaps since the volume-change gap retired at 0.3.0.
        (
            "The six measured gaps with what each forbids, and the whole "
            "list as a plain mapping for a manifest.",
            "The five measured gaps with what each forbids, and the whole "
            "list as a plain mapping for a manifest.",
        ),
        # envelope.PRESET is pt-v12. The row's own advice is to compare your
        # install against this value, so naming a two-boundary-old preset
        # told every correct install that it disagreed with the docs.
        (
            "252, and pt-v3. If your installed preset differs, check() says "
            "so rather than answering.",
            "252, and pt-v12. If your installed preset differs, check() "
            "says so rather than answering.",
        ),
        # ── api-params ──────────────────────────────────────────────────
        # The ceiling is 32x, not 8x. The clamp binds where the coupled
        # target reaches it: target = base * (1 - c + c*(vix/anchor)^2), so
        # with the shipped c = 0.9540 and anchor 15 it binds at VIX 86.8 on
        # pt-v12 and bound at VIX 43.3 on pt-v3's 8x. "Above about 42" was
        # the 8x number, which is why the guidance had to move with it.
        (
            "Clamps on the factor variance. The 8x ceiling is why pinning "
            "VIX above about 42 buys almost no extra volatility.",
            "Clamps on the factor variance. pt-v12 ships the ceiling at 32x "
            "baseline where pt-v3 had 8x, which is a physical number rather "
            "than a fitted one: a real record VIX of 82.7 against this "
            "model's anchor of 15 is a variance ratio of 30, and anything "
            "lower stops the market reaching the variance a real record "
            "implies. With the shipped VIX coupling the clamp binds around "
            "VIX 87, where on pt-v3's 8x it bound around 43.",
        ),
        # 5.2 at 504 days is the pt-v3 reading, and pt-v3 is the last preset
        # with both jump intensities at 0.0, so no jump fires on it.
        # (`jump_momentum_share` is 1.0 there, which is why the claim is
        # about the intensities and not about every jump coefficient.) The
        # shipped pt-v12 reads 7.7528 against the same 7.1 to 22 band, which
        # is in band -- `envelope.MEASURED_504["excess_kurtosis"]` and
        # `BANDS_504`. Stated in the present tense, the sentence claimed the
        # shipped model still fails the statistic jumps were added to fix.
        (
            "Without jumps, prices only diffuse, and nothing ever gaps. "
            "That is why 504-day kurtosis reads 5.2 against a real 7.1 to "
            "22: fat tails at that scale need surprises.",
            "Without jumps, prices only diffuse, and nothing ever gaps. "
            "That is why 504-day kurtosis read 5.2 against a real 7.1 to 22 "
            "on pt-v3, the last preset with both jump intensities at zero, "
            "so no jump ever fired: fat tails at that scale need surprises. "
            "With them live the shipped pt-v12 reads 7.7528 there, inside "
            "the band.",
        ),
        # No statistic fails structurally now. volume_change_acf1 reads
        # -0.2656 at 252 days against -0.32 to -0.20 and -0.2572 at 504
        # against -0.29 to -0.21, and the gap that named it is retired, so
        # the machinery is described by what it achieved rather than by a
        # failure the project withdrew two releases ago.
        (
            "A shared day-to-day volume process, so a busy day is followed "
            "by a busy day. This is the machinery aimed at the one "
            "structurally failed statistic: without a volume process, "
            "day-to-day volume changes are pure noise and their "
            "autocorrelation sits near −0.5 at any coefficients."
            "</sc-raw-td></sc-raw-tr>",
            "A shared day-to-day volume process, so a busy day is followed "
            "by a busy day. This is the machinery that made "
            "volume_change_acf1 reachable: without a volume process, "
            "day-to-day volume changes are pure noise and their "
            "autocorrelation sits near −0.5 at any coefficients. It was "
            "called structurally unreachable until 0.2.0 and it is not. "
            "pt-v12 reads −0.2656 at 252 days against a band of "
            "−0.32 to −0.20 and −0.2572 at 504 against "
            "−0.29 to −0.21, and the gap that named it is "
            "retired.</sc-raw-td></sc-raw-tr>\n            "
            '<sc-raw-tr><sc-raw-td style="font-family:var(--font-mono);'
            "font-size:12.5px\">volume_move_response · volume_move_cap "
            "· volume_move_floor · volume_move_noise"
            "</sc-raw-td><sc-raw-td>How much more a name trades per one "
            "percent it has moved today, where that response saturates, the "
            "baseline it sits on, and the return-unrelated noise around it. "
            "The cap is the whole of pt-v12: at the 4.0 every preset from "
            "pt-v1 to pt-v11 shipped, a name that fell twelve percent "
            "traded exactly as much as one that fell four, so every crisis "
            "session was pinned to one value and contributed no covariance "
            "at all. Raising it to 12.0, roughly where a real exchange "
            "starts halting, took the 504-day panel from 13/14 in band to "
            "14/14 and the held-out universe from 13/14 to 14/14, and cost "
            "nothing else measured.</sc-raw-td></sc-raw-tr>",
        ),
    ]
    for old, new in swaps:
        if old not in doc:
            sys.exit("the design bundle reworded an API-page passage that "
                     f"apply_api_fixes corrects: {old[:70]!r}")
        doc = doc.replace(old, new, 1)
    return api_params_subtitle(doc)


def api_params_subtitle(doc: str) -> str:
    """Stop the model-parameters page promising a completeness it lacks.

    The page's subtitle said "Every runtime-settable coefficient". It was
    true when the bundle was authored and the surface has grown past it
    twice since: `ModelParams.settable()` returns 87 names at 0.3.0, up
    from 70 at 0.2.0, and the page carries rows for a little over half of
    them. The single most conspicuous absence was `volume_move_cap`, which
    is the entire subject of the 0.3.0 release; the row above adds it, and
    this counts what is actually there afterwards rather than asserting a
    number that drifts the next time the surface moves.
    """
    from pretium import ModelParams

    start = doc.index('data-page="api-params"')
    end = doc.index('data-page="', start + 1)
    page = doc[start:end]
    names = sorted(ModelParams.settable())
    shown = sum(1 for n in names if re.search(r"\b" + re.escape(n) + r"\b", page))

    old = (
        "Every runtime-settable coefficient and what it controls. "
        '<code style="font-size:14px">pt.ModelParams.settable()</code> lists '
        "this surface from your installed wheel; this page explains it."
    )
    if old not in doc:
        sys.exit("the model-parameters subtitle was reworded; "
                 "api_params_subtitle would ship a false completeness claim")
    # Scope first, pointer second: `description_for` cuts the meta
    # description at 155 characters, and the half a reader sees in a search
    # result should be the honest one.
    new = (
        f"What each model coefficient controls. This page explains the "
        f"{shown} coefficients that most often get changed; "
        '<code style="font-size:14px">pt.ModelParams.settable()</code> is '
        f"the complete list and returns all {len(names)} from your installed "
        "wheel, up from 70 at 0.2.0."
    )
    return doc.replace(old, new, 1)


def apply_reference_fixes(doc: str) -> str:
    """Correct the four reference pages: MCP, install, schemas and wasm.

    They are grouped because they share one cause. The bundle was authored
    against three RNG streams, seven attribution columns, six named gaps,
    54 settable coefficients and a smaller wasm binary, and every one of
    those moved without the pages moving with them. Each is also a number a
    reader is invited to check against their own install, which is the kind
    that costs something when it is wrong.

    The MCP entries are the sharpest, because that page's own argument is
    that "a hardcoded caveat is how a caveat becomes false" and the box
    making the argument carried a hardcoded pt-v3 figure two eras old. The
    server composes that sentence at call time from `envelope.CERTIFIED`
    and `facts.REAL_MARKETS` (`_statistic_line` in `python/pretium/mcp.py`),
    so the sample output now carries what that expression yields on 0.3.0
    rather than a paraphrase naming a preset the tool never emits.

    The wasm sizes are measured rather than converted: `tools/wasm/build.sh`
    prints the raw and gzip figures itself, and the 0.3.0 artefact is
    204,290 bytes raw and 72,981 gzipped, with brotli -q 11 at 60,285. No
    unit convention recovers the bundle's 182 / 68 / 56 KB.
    """
    swaps = [
        # -- MCP ---------------------------------------------------------
        # `pt.model_preset()["name"]` -> pt-v12, and evaluating mcp.py's own
        # `_statistic_line("return_acf1")` against the live envelope gives
        # "return_acf1 measures 0.0239 against a real-market band of -0.08
        # to 0.06 (in band) at the certified 252-day horizon".
        #
        # The re-measured median the box also quoted, 0.0485, has no
        # recorded provenance anywhere in the repository, so it is dropped
        # rather than restated under a new preset. The box is about the
        # mechanism, and the last sentence now names the mechanism.
        (
            "The shipped <code style=\"font-size:12px\">pt-v3</code> certifies "
            "0.0375, and re-measuring across the README's own published "
            "method gives a median of 0.0485. Both sit comfortably inside "
            "the real-market band of −0.08 to 0.06, and both are about a "
            "fifth of the quoted figure.",
            "The default has moved twice since that was written, from "
            "<code style=\"font-size:12px\">pt-v3</code> to "
            "<code style=\"font-size:12px\">pt-v10</code> at 0.2.0 and on to "
            "<code style=\"font-size:12px\">pt-v12</code> at 0.3.0, which "
            "certifies 0.0239: comfortably inside the real-market band of "
            "−0.08 to 0.06, and about a tenth of either quoted figure. This "
            "server never types that number. It reads it out of "
            "<code style=\"font-size:12px\">envelope.CERTIFIED</code> on every "
            "call, so the sentence moves when the preset moves.",
        ),
        # The badge over this block warrants that "shapes and field names
        # are real". This caveat's shape was not: the server builds it from
        # `_statistic_line`, which names no preset and quotes no band edge
        # of its own. Replaced with what that expression yields on 0.3.0,
        # verbatim.
        (
            "    \"MOMENTUM SIGNAL: returns here autocorrelate at +0.0375 at "
            "lag\n     one (pt-v3, band -0.08 to 0.06) and real equities sit "
            "near\n     zero. A momentum edge measured here is partly an "
            "artefact of\n     the mispricing process and will not "
            "transfer.\",",
            "    \"This strategy trades a return-continuation or reversal "
            "signal,\n     so its edge depends on the simulator's return "
            "autocorrelation:\n     return_acf1 measures 0.0239 against a "
            "real-market band of\n     -0.08 to 0.06 (in band) at the "
            "certified 252-day horizon.\",",
        ),
        # len(pt.ModelParams.settable()) -> 87 at 0.3.0. 54 predates the
        # 0.1.4, 0.2.0 and 0.3.0 additions; CHANGELOG.md records the last
        # leg of that, 70 -> 87.
        (
            "<strong style=\"color:var(--fg)\">The 54 model coefficients."
            "</strong>",
            "<strong style=\"color:var(--fg)\">The 87 model coefficients."
            "</strong>",
        ),
        # len(pretium.envelope.GAPS) -> 5. The volume-change gap retired at
        # 0.3.0 because pt-v12 holds that row at both horizons.
        (
            "A sector-concentrated roster is one of the six named envelope "
            "gaps, and this is what makes it askable.",
            "A sector-concentrated roster is one of the five named envelope "
            "gaps, and this is what makes it askable.",
        ),
        # -- schemas -----------------------------------------------------
        # The `truth()` table promised nine factors and listed seven. The
        # live Arrow schema on 0.3.0 ends random_noise, circuit_breaker,
        # jump, and `Engine.FACTORS` is the same nine in the same order.
        # Without the last two the columns do not sum to the change in
        # mispricing_s on any day a jump lands or the breaker binds, which
        # is exactly the day a reader is checking.
        (
            "reversion · momentum · crowd_lean · company_news "
            "· order_flow_impact · short_squeeze_effect · "
            "random_noise",
            "reversion · momentum · crowd_lean · company_news "
            "· order_flow_impact · short_squeeze_effect · "
            "random_noise · circuit_breaker · jump",
        ),
        (
            "A name that did not tick has zeros across the seven and keeps "
            "its last two level values",
            "A name that did not tick has zeros across the nine and keeps "
            "its last two level values",
        ),
        # -- install -----------------------------------------------------
        # The sentence says three; the table under it has five rows:
        # package version, model preset, known-answer baseline,
        # SPEC_VERSION and RNG derivation.
        (
            "What has a stated policy is the simulated output, and it has "
            "three independent version numbers:",
            "What has a stated policy is the simulated output, and it has "
            "five independent version numbers:",
        ),
        # len(engine.state_snapshot()["rng"]) -> 21 on 0.3.0: three words
        # for each of the seven streams `set_rng_state` restores in
        # rust/src/python_engine.rs -- market, economy, external, jumps,
        # volume, news, volume_idio. The accepted lengths there are
        # 9 | 12 | 15 | 18 | 21, and only a 3 is refused.
        (
            "A checkpoint carries nine RNG numbers: state, increment and "
            "Box-Muller spare for each of the three streams. A pre-split "
            "snapshot with three numbers is refused on restore with its era "
            "named, because it froze a single-stream market this version "
            "cannot continue bit-exactly.",
            "A checkpoint carries twenty-one RNG numbers: state, increment "
            "and Box-Muller spare for each of the seven streams, which are "
            "market, economy, external, jumps, volume, news and per-name "
            "volume. It carried nine when there were three streams and grew "
            "by three each time a mechanism got its own, so restore reads "
            "the era off the length: nine, twelve, fifteen and eighteen all "
            "still restore, keeping this engine's seed-derived position for "
            "the streams the snapshot predates, which is what lets a "
            "checkpoint written before a mechanism existed replay as it did "
            "then. Only a pre-split snapshot with three numbers is refused, "
            "with its era named, because it froze a single-stream market "
            "this version cannot continue bit-exactly.",
        ),
        # pyproject.toml:14 and rust/Cargo.toml:11 both read
        # `license = "MIT OR Apache-2.0"`, and LICENSE-APACHE and
        # LICENSE-MIT both sit at the repository root. Naming one of them
        # takes away an option the reader is entitled to.
        (
            "Issues and pull requests go there. Licensed Apache-2.0.",
            "Issues and pull requests go there. Licensed MIT OR Apache-2.0, "
            "at your option.",
        ),
        (
            "<span style=\"font-size:12.5px;color:var(--faint)\">Apache-2.0"
            "</span>",
            "<span style=\"font-size:12.5px;color:var(--faint)\">MIT OR "
            "Apache-2.0</span>",
        ),
        # -- wasm size ---------------------------------------------------
        # Measured on the committed 0.3.0 artefact,
        # rust/target/wasm32-unknown-unknown/release/pretium.wasm, which
        # `strings` confirms carries 0.3.0:
        #   wc -c              -> 204290
        #   gzip -9 -c | wc -c ->  72981
        #   brotli -q 11 -c    ->  60285
        # Byte counts rather than rounded KB, because the bundle's KB
        # figures are unrecoverable under either convention and the raw and
        # gzip lines are exactly what tools/wasm/build.sh prints.
        (
            "<sc-raw-td style=\"font-family:var(--font-mono);font-size:12.5px\""
            ">182 KB</sc-raw-td>",
            "<sc-raw-td style=\"font-family:var(--font-mono);font-size:12.5px\""
            ">204,290 bytes</sc-raw-td>",
        ),
        (
            "<sc-raw-td style=\"font-family:var(--font-mono);font-size:12.5px\""
            ">68 KB</sc-raw-td>",
            "<sc-raw-td style=\"font-family:var(--font-mono);font-size:12.5px\""
            ">72,981 bytes</sc-raw-td>",
        ),
        (
            "<sc-raw-td style=\"font-family:var(--font-mono);font-size:12.5px\""
            ">56 KB</sc-raw-td>",
            "<sc-raw-td style=\"font-family:var(--font-mono);font-size:12.5px\""
            ">60,285 bytes</sc-raw-td>",
        ),
        (
            "Smaller than most JavaScript charting libraries, for a whole "
            "market simulator with an order book in it.",
            "About 200 KiB raw and 71 KiB gzipped, for a whole market "
            "simulator with an order book in it. Measured on the 0.3.0 "
            "artefact <code style=\"font-size:13px\">tools/wasm/build.sh</code> "
            "produces: the script prints the raw and gzip figures itself, "
            "and the brotli line is "
            "<code style=\"font-size:13px\">brotli -q 11</code> over the same "
            "file.",
        ),
        (
            "that build produces a digest identical to the native one: 182 "
            "KB raw, 68 KB gzipped.",
            "that build produces a digest identical to the native one, and "
            "at 0.3.0 it is 204,290 bytes raw and 72,981 gzipped.",
        ),
        (
            "A 68 KB WebAssembly build whose digest matches the native one "
            "exactly, so a permalink gives every viewer the same market.",
            "A 73 KB gzipped WebAssembly build whose digest matches the "
            "native one exactly, so a permalink gives every viewer the same "
            "market.",
        ),
    ]
    for old, new in swaps:
        if old not in doc:
            sys.exit("the design bundle reworded a reference-page passage "
                     f"that apply_reference_fixes corrects: {old[:70]!r}")
        doc = doc.replace(old, new, 1)
    return doc


def nav_html(pages: list[dict], current: str) -> str:
    items = []
    for p in pages:
        if p["slug"] == current:
            items.append(f'<li><span aria-current="page">{html.escape(p["label"])}</span></li>')
        else:
            items.append(
                f'<li><a href="{page_url(p["slug"])}">{html.escape(p["label"])}</a></li>'
            )
    return "<ul>" + "".join(items) + "</ul>"


def _og_dates(page: dict) -> str:
    published, modified = seo.page_dates(page["slug"])
    out = []
    if published:
        out.append(f'<meta property="article:published_time" content="{published}">')
    if modified:
        out.append(f'<meta property="article:modified_time" content="{modified}">')
    return "\n".join(out)


STATIC_CSS = """
.layout{max-width:1120px;margin:0 auto;padding:0 24px;display:grid;
  grid-template-columns:232px minmax(0,1fr);gap:40px}
.sidebar{padding:28px 0;border-right:1px solid var(--linesoft)}
.sidebar ul{list-style:none;padding:0;margin:0;font-size:13.5px}
.sidebar li{margin:0 0 2px}
.sidebar a,.sidebar span{display:block;padding:5px 10px;border-radius:6px;color:var(--mut)}
.sidebar a:hover{background:var(--panel);text-decoration:none;color:var(--fg)}
.sidebar span[aria-current]{background:var(--navact);color:var(--fg);font-weight:600}
main{padding:28px 0 72px;min-width:0}
main h1{font-size:30px;margin:0 0 18px}
main h2{font-size:20px;margin:34px 0 12px}
main h3{font-size:16px;margin:24px 0 8px}
.masthead{border-bottom:1px solid var(--line);background:var(--panel)}
.masthead .inner{max-width:1120px;margin:0 auto;padding:14px 24px;
  display:flex;align-items:baseline;gap:12px}
.masthead a.brand{font-weight:650;color:var(--fg);font-size:15px}
.masthead .tag{color:var(--mut);font-size:13px}
.masthead .spacer{flex:1}
.appnote{margin:0 0 26px;padding:10px 14px;border:1px solid var(--linesoft);
  border-radius:8px;background:var(--panel);font-size:13px;color:var(--mut)}
pre{background:var(--codebg);color:var(--codefg);border-radius:9px;
  padding:13px 16px;overflow-x:auto;font-size:13px}
pre code{color:inherit}
.tablewrap{overflow-x:auto}
footer.site{border-top:1px solid var(--line);margin-top:48px;padding:22px 0;
  color:var(--mut);font-size:13px}
@media(max-width:820px){.layout{grid-template-columns:1fr;gap:0}
  .sidebar{border-right:0;border-bottom:1px solid var(--linesoft)}}
"""


def build_static_page(page: dict, pages: list[dict], css: str) -> str:
    desc = description_for(page)
    title = f"{seo.title(page['slug'], page['h1'])} · {SITE_NAME}"
    content = clean_content(page["html"])
    # Give wide tables their own scroll container so the page body never
    # scrolls sideways on a phone.
    content = re.sub(r"<table", '<div class="tablewrap"><table', content)
    content = re.sub(r"</table>", "</table></div>", content)
    # Visible questions, and the matching FAQPage markup in the head. Marked-up
    # questions that do not appear on the page are a structured data
    # violation, so these are rendered as well as declared.
    content += seo.faq_html(page["slug"])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc, quote=True)}">
<link rel="canonical" href="{absolute(page['slug'])}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:title" content="{html.escape(seo.title(page['slug'], page['h1']), quote=True)}">
<meta property="og:description" content="{html.escape(desc, quote=True)}">
<meta property="og:url" content="{absolute(page['slug'])}">
{_og_dates(page)}
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{html.escape(seo.title(page['slug'], page['h1']), quote=True)}">
<meta name="twitter:description" content="{html.escape(desc, quote=True)}">
{json_ld(page, desc)}
{FAVICON_LINKS}
{FONT_LINK}
<style>{css}{STATIC_CSS}</style>
{analytics()}
</head>
<body>
<header class="masthead"><div class="inner">
<a class="brand" href="./">pretium</a>
<span class="tag">{TAGLINE}</span>
<span class="spacer"></span>
<a href="{REPO_URL}">GitHub</a>
</div></header>
<div class="layout">
<nav class="sidebar" aria-label="Documentation">{nav_html(pages, page['slug'])}</nav>
<main>
<p class="appnote">This is the plain version of the page. The
<a href="./#/{page['slug']}">full documentation app</a> adds search, dark mode
and copy buttons.</p>
{content}
</main>
</div>
<footer class="site"><div class="layout" style="display:block">
pretium {TAGLINE}. <a href="{REPO_URL}">Source on GitHub</a>.
</div></footer>
</body>
</html>
"""


def build_index(bundle: str, pages: list[dict]) -> str:
    """The app, with the head a crawler reads and a no-JS route index."""
    home = next(p for p in pages if p["slug"] == "home")
    desc = (
        "Documentation for pretium, a deterministic market simulator with a "
        "real limit order book. Rust core, Python API, published realism limits."
    )
    site_ld = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "pretium",
        "description": desc,
        "url": f"{BASE_URL}/",
        "applicationCategory": "DeveloperApplication",
        "operatingSystem": "Linux, macOS, Windows",
        "codeRepository": REPO_URL,
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
    }
    head_extra = f"""<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="{html.escape(desc, quote=True)}">
<link rel="canonical" href="{BASE_URL}/">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:title" content="{SITE_NAME}: {TAGLINE}">
<meta property="og:description" content="{html.escape(desc, quote=True)}">
<meta property="og:url" content="{BASE_URL}/">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{SITE_NAME}: {TAGLINE}">
<meta name="twitter:description" content="{html.escape(desc, quote=True)}">
<script type="application/ld+json">{json.dumps(site_ld, separators=(",", ":"))}</script>
{FAVICON_LINKS}
{spa_analytics()}
"""
    out = bundle.replace("<head>", "<head>\n" + head_extra, 1)
    out = out.replace("<html>", '<html lang="en">', 1)

    # Without JavaScript the app renders nothing. Give that reader, and any
    # crawler that does not execute the bundle, a real route index.
    links = "".join(
        f'<li><a href="{page_url(p["slug"])}">{html.escape(p["label"])}</a></li>'
        for p in pages
    )
    noscript = (
        "<noscript><div style=\"max-width:720px;margin:40px auto;padding:0 24px;"
        "font:15px/1.7 system-ui,sans-serif\">"
        f"<h1 style=\"font-size:24px\">{SITE_NAME}</h1>"
        f"<p>{html.escape(desc)}</p>"
        "<p>The documentation app needs JavaScript. Every page is also "
        "available as plain HTML:</p>"
        f"<ul>{links}</ul></div></noscript>"
    )
    return out.replace("<body>", "<body>\n" + noscript, 1)


def build_sitemap(pages: list[dict]) -> str:
    return seo.build_sitemap(pages, absolute, BASE_URL)


def build_robots() -> str:
    return seo.build_robots(BASE_URL)


def main() -> None:
    bundle = read_bundle()
    doc = inner_document(bundle)
    css = design_css(doc)
    pages = split_pages(doc)
    seo.check_descriptions(pages)

    OUT.mkdir(exist_ok=True)
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    (OUT / "index.html").write_text(build_index(bundle, pages), encoding="utf-8")
    (OUT / "sitemap.xml").write_text(build_sitemap(pages), encoding="utf-8")
    (OUT / "robots.txt").write_text(build_robots(), encoding="utf-8")
    (OUT / "envelope.html").write_text(
        seo.envelope_page(BASE_URL, REPO_URL, VERSION), encoding="utf-8"
    )
    (OUT / "llms.txt").write_text(
        seo.build_llms(pages, VERSION, BASE_URL, absolute), encoding="utf-8"
    )
    (OUT / "llms-full.txt").write_text(
        seo.build_llms_full(pages, VERSION, BASE_URL, absolute, seo.plain_text),
        encoding="utf-8",
    )

    written = 0
    for page in pages:
        if page["slug"] == "home":
            continue
        (OUT / f"{page['slug']}.html").write_text(
            build_static_page(page, pages, css), encoding="utf-8"
        )
        written += 1

    print(f"index.html          {len((OUT / 'index.html').read_bytes()):>9,} bytes")
    print(f"static pages        {written}")
    # len(pages) is 24 and the sitemap now carries 50: the pages, the
    # envelope landing page, and the 25 markdown docs that Pages was already
    # serving unlisted. Counting the pages here reported a number that was
    # true before this build and misleading after it.
    _urls = (OUT / "sitemap.xml").read_text(encoding="utf-8").count("<url>")
    print(f"sitemap entries     {_urls}  ({len(pages)} pages + envelope + "
          f"{len(seo.markdown_docs())} markdown)")
    print(f"analytics           {GA_MEASUREMENT_ID or 'not configured'}")
    print(f"markdown docs       {len(seo.markdown_docs())} listed")
    print(f"llms.txt            {len((OUT / 'llms.txt').read_bytes()):>9,} bytes")
    print(f"llms-full.txt       {len((OUT / 'llms-full.txt').read_bytes()):>9,} bytes")


if __name__ == "__main__":
    main()
