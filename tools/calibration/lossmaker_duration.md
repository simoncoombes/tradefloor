# A rate term for loss-making companies: design note

Written 2026-10-11, before any code, for the pt-v22 candidate. The numbers
from the real market below are computed from the files named beside them;
the calibration and the grade are appended at the end once they ran.

## The gap

`fair_value.rs` values a company with earnings at or below zero at 1.2
times its restated book value (`compute_fair_value_at`, the book path,
`fair_value.rs:343-355` on `f2e0c6e`). The rate term
$R_{i,d} = \max(0.5,\ 1 - \lambda D_i (y_d - r^{\ast}))$ that every profitable
company's multiple carries is not applied there, and the breakdown reports
`rate_adjustment` 1.0. So a rate shock moves no loss-maker's fair value. A
loss-maker's price still moves with the market factor, the VIX discount and
its own noise, but its valuation is the one valuation in the model that
does not reprice when the discount rate does.

In 2022 the stocks without earnings fell furthest. The model has them
falling least on the rate channel, because profitable growth companies
lose $\lambda D_i / R$ per unit of yield and loss-makers lose nothing.

## The real target

**Source.** Kenneth R. French Data Library, "Portfolios Formed on E/P",
built from CRSP (the file says the 202608 CRSP database), downloaded on
2026-10-11 from
`https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/Portfolios_Formed_on_E-P_CSV.zip`.
The 2022 rows are kept in `tools/calibration/data/ff-ep-2022.json`.
Portfolios are formed each June on fiscal-year earnings over December market
equity. The `<= 0` portfolio holds every firm with non-positive earnings. The
positive-E/P firms are split at NYSE breakpoints into Lo 30 (the highest
P/E, the profitable growth names), Med 40 and Hi 30. The universe is the
whole CRSP common-stock market.

**The rate move.** Moody's Baa yield (FRED DBAA, the frozen copy behind
row R6, `validation/pt-v21/programme/results/ptv20/data/driven-2022.json`)
went from 3.45% on 3 January 2022 to 5.87% on 30 December 2022, +242 bp.

**2022, January to December, total return:**

| Portfolio | Firms (Jan 2022) | Equal weight | Value weight |
|---|---|---|---|
| E/P <= 0 (loss-makers) | 1,593 | -38.14% | -16.61% |
| Lo 30 (profitable, highest P/E) | 547 | -19.47% | -27.18% |
| Med 40 | 607 | -10.62% | -12.68% |
| Hi 30 | 573 | -10.89% | -8.78% |
| all profitable, by firm count | 1,727 | -13.41% | -15.84% |

As log changes per 100 bp of Baa, equal weight: loss-makers -19.9%,
profitable growth (Lo 30) -8.9%, all profitable -6.0%. **The loss-makers'
excess over profitable growth is -10.9% per 100 bp** (log -0.480 against
-0.216, over 2.42 points), and -13.9% per 100 bp over all profitable firms.

That excess is the target. Two caveats come with it.

- It is an equal-weight figure. Value-weighted, the 2022 loss-makers fell
  *less* than profitable growth (-16.6% against -27.2%, an excess of +5.6%
  per 100 bp). Part of that is the June formation lag: from January to June
  2022 the `<= 0` portfolio was sorted on fiscal 2020 earnings, when large
  energy companies reported losses, and energy rose in 2022. But in the
  second half, sorted on fiscal 2021, the value-weighted loss-makers still
  did not trail Lo 30 (log +0.024 against -0.013). The "unprofitable fell
  most" fact is a fact about the typical loss-making firm, which is small,
  not about loss-making capitalisation.
- Not all of the gap is the discount rate. Loss-makers also lost access to
  cheap equity financing and some to their revenue outlook. Reading the
  whole gap as duration makes -10.9% per 100 bp an upper bound on what a
  rate term should carry.

**A cross-check, secondary.** The ARK Innovation ETF, a fund of mostly
unprofitable growth companies, was down 63% for 2022 to 9 December (TheStreet,
"A Look Back at Cathie Wood's Disastrous Year", 9 December 2022, quoted on
the Wikipedia page "ARK Invest"). Goldman Sachs' non-profitable technology
basket is widely reported to have lost about 62% in 2022, but neither page I
tried for that figure opened (etf.com refused, nasdaq.com timed out), so it
is not used here.

## The form

Mirror the profitable side. For a loss-maker,

```math
R^{L}_{i,d} = \max\big(0.5,\ 1 - \kappa_L\,D_i\,(y_d - r^{\ast})\big),
\qquad
V_{i,t} = \max\big(0.01,\ 1.2\,\hat K_{i,t}\,R^{L}_{i,d}\big)
```

with the same duration $D_i = 1 + 2\max(0, \gamma_i)$, the same corporate
yield $y_d$, the same neutral rate $r^{\ast}$ and the same 0.5 floor, and
its own sensitivity $\kappa_L$, the new dial `lossmaker_rate_sensitivity`.
At $y = r^{\ast}$ the term is exactly 1, so a generated roster still opens
at its own fair value at the neutral rate (`universe.rs`'s reconciliation
test), and the stationary opening puts any day-zero premium into $v$ as it
does today. At $\kappa_L = 0$ the code takes a branch to the line that
stands, so every existing preset is bit-identical and the dial joins
`DIGEST_SILENT_AT_ZERO`.

Its own dial rather than $\lambda$, because the target says loss-makers
need several times the profitable sensitivity: a loss-maker valued on book
has no earnings yield to absorb a discount-rate change, and its cash flows
are further out. With $\lambda$ = 3 and a typical $D$ near 1.3, profitable
growth names lose about 4% per 100 bp in the model; an excess of 10.9%
needs $\kappa_L D$ near 15, so $\kappa_L$ near 11. The validation range is
[0, 20].

What the term cannot do: the certified roster `Universe.random(40,
seed=111)` holds 3 loss-makers of 40 and 1.0% of its capitalisation
(`Universe.random(4000, seed=7)`: 10.3% of names, 11.4% of capitalisation),
against 1,593 of 3,320 CRSP firms in January 2022. So the index-level rows,
the scenario sizes among them, can move only by about that share. The term
fixes a cross-sectional fact; it is not a lever on the index's response to
a scenario.

## Should `rate_pe_sensitivity` move toward 5.2% per 100 bp?

No. MODEL.md's "4.26% per 100 bp against the S&P 500's 5.2%" is pt-v20's
reading (box ptv20g6). pt-v21 reads -6.97% per 100 bp on the same row, 90
seeds (box ptv21c1, `validation/pt-v21/programme/results/ptv21/box-c1/driven2022.json`),
already past the S&P 500's -5.2% and inside the band of -10.4 to -2.6.
Moving $\lambda$ toward 5.2 on pt-v21 would mean lowering it. Nothing here
asks for that, and the candidate leaves $\lambda$ at 3. The market P/E row
reads only profitable companies (`engine.rs`, the `market_pe` sum skips
`eps <= 0`), so the new term does not reach R6 directly.

## The measurement

The same construction on the driven 2022 path (`driven2022.py`'s path:
VIX, fed funds, Baa, 2- and 10-year pinned each morning, cycle held at
expansion): per seed, the equal-weight log price change from the first to
the last session of the loss-makers, less that of the 30% of profitable
names with the highest P/E at the start, over the 2.42-point Baa move.
Roster `Universe.random(200, seed=2022)`, which carries enough loss-makers
to read a cross-sectional mean. `tools/calibration/lossmaker_duration.py`
measures it.

- Calibration: seeds 9001-9020, pick $\kappa_L$ on a grid to put the
  measured excess at -10.9%.
- Held out: seeds 9101-9130 and a second roster, `Universe.random(200,
  seed=4022)`, read once at the chosen value.

The grade of the candidate (pt-v21 with the dial at the chosen value) is
separate, on seeds no calibration read, and is appended below.
