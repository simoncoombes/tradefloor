# The tradefloor model

This document states the tradefloor market model as equations. It describes
**tradefloor 0.8.5** running the default preset **pt-v20**. Every equation
was read off the code on the `release/0.8.5` branch at commit `8b7ed44`, and
each one names the source line it comes from, as `file:line` under
`rust/src/`. Parameter values are pt-v20's, as
`tf.ModelParams.from_preset("pt-v20").to_dict()` returns them, rounded here
to four significant figures. Where a table gives two values, the second is
pt-v19's.

A preset is a frozen set of coefficients. The equations below hold for every
preset, but many terms are switched on or off by a preset's dials, and this
document describes the terms pt-v20 runs. Terms that pt-v20 switches off are
listed once, in [Off in pt-v20](#off-in-pt-v20), and not written out.

pt-v20 is pt-v19 with 22 dials moved. Work published on pt-v19, the default
in 0.8.0 and 0.8.1, still replays exactly when it names its preset.
[pt-v19: reproducing earlier work](#pt-v19-reproducing-earlier-work) gives
every equation and value where pt-v19 differs.

pt-v20 was graded before it shipped on 28 registered long-run rows and the
one-year table, and passes all of them (design repository,
`programme/ptv20-registration.md` and `programme/results/ptv20/`, final
grade box `ptv20g3`). [STATISTICS.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/STATISTICS.md)
lists the rows.

The realism statistics the model is checked against are defined in
[STATISTICS.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/STATISTICS.md).
The release lines that get fixes, and for how long, are in
[SUPPORT.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/SUPPORT.md).

## Reading this document

### Parameter kinds

Every parameter table has a **kind** column. It says how the value was set.

| Kind | Meaning |
|---|---|
| measured | estimated on a named data series, with a standard error on record |
| derived | follows from an identity, from measured inputs or from the model's own structure |
| fitted | tuned against the realism statistics, one dial or a search over many; calibrated jointly, not one at a time |
| chosen | set by hand, often inherited from the reference implementation the engine was ported from |
| guard | a bound that keeps the simulation finite; not a claim about real markets |

**Measured** and **derived** values are calibrated. **Fitted** values are
calibrated jointly: a search found them against the realism statistics, and
no derivation is on record for the single dial. **Chosen** values and
**guards** are assumptions. The kinds follow the project's own ledger,
`python/tradefloor/provenance.py`, with its `unprovenanced` split into fitted
and chosen by what the code and design notes say about each value.

Where a design note or the code names a source, the table gives it. The
data series used most often:

| Short name | Series |
|---|---|
| S&P 500 | ^GSPC daily closes, 1990-01-03 to 2025-07-30; from 1928 for the index drift and tail bands |
| VIX | ^VIX daily closes, 1990 to 2025 |
| Reference roster | 40 US large caps across the sectors, daily, 2015-07 to 2025-07; 32 of them from 1987 |
| NBER and BEA | NBER recession dates and BEA real GDP (GDPC1), 1990 to 2025 |
| FRED CPI | FRED CPIAUCSL, 2015 to 2025 |

### Time

| Unit | Length | Code |
|---|---|---|
| tick | one minute of the 09:30 to 16:00 session | `market/hours.rs:22` |
| session (day) | 390 ticks | `python_engine.rs:1863` |
| macro month | 21 sessions | `economy/state.rs:65` |
| macro quarter | 63 sessions | `economy/state.rs:83` |
| year | 252 sessions | `market/tick.rs:217`, `economy/state.rs:62` |

Days are an abstract trading calendar: there are no weekends or holidays.
Within a session a per-day drift is applied as $1/390$ per tick and a per-day
standard deviation as $1/\sqrt{390}$ per tick. Tick $t = 0, \dots, 389$ sits
at session fraction $\tau_t = t/390$.

### Notation

| Symbol | Meaning | Unit |
|---|---|---|
| $i$, $k(i)$ | a company, and its sector | |
| $d$, $t$ | session, and tick within the session | |
| $V_{i,t}$ | fair value | currency per share |
| $s_{i,t}$ | mispricing, the log gap between model price and fair value | log |
| $P_{i,t}^{\ast}$ | model price, $V e^{s}$ | currency |
| $P_{i,t}$ | printed (traded) price | currency |
| $P_{i,d}^{o}$ | the session's opening price | currency |
| $X_d$ | the VIX, fixed within a session | index points |
| $A$ | the VIX anchor: the VIX at which every VIX coupling reads one | index points |
| $\rho_d$ | $X_d / A$ | |
| $v_d$ | market-factor variance | per day |
| $h_{i,d}$ | a company's own (GJR-GARCH) variance | per day |
| $y_d^{c}$ | corporate bond yield | percent a year |
| $\mathbf{1}[\cdot]$ | 1 if the condition holds, else 0 | |
| $\mathrm{clip}(x; a, b)$ | $\min(b, \max(a, x))$ | |
| $Z$, $U$ | a fresh standard normal draw, a fresh uniform draw on $[0, 1)$ | |

Macro quantities are in percent, as the engine stores them. The Python API
converts rates to fractions at its boundary (`units.rs:1-31`).

## The structure

The model has two layers that meet in one number, the model price.

```text
macro economy (daily, at the close)
  business cycle -> growth, unemployment, inflation -> central bank
  -> policy rate -> 2-year, 10-year and corporate bond yields
  -> nominal output (GDP x CPI) and the aggregate earnings cycle
  -> the VIX

fair value V (every tick)            = sector P/E x earnings x rate term
                                        x the company's own fair-value level
mispricing s (every tick)            = mean reversion + herding + market news
                                        + order flow + market noise,
                                        market jumps at the close
model price P* (every tick)          = V exp(s)
printed price P (every tick)         = P* traded through the market maker's
                                        book, quoted around P*
volatility (daily, at the close)     = per-name GJR-GARCH, market-factor
                                        variance, sector variance; all read the VIX
```

**Fair value** moves with the corporate bond yield, which moves every
session; with nominal output and an aggregate earnings cycle, which move
with the business cycle; and with each company's own fair-value level $v$,
which takes the company's and its sector's shocks for good. **Mispricing**
carries what is transient: the common market factor, market-wide news,
market jumps and order flow. It reverts to zero with a half-life of about
40 sessions. **The printed price** is the model price after it has gone
through a limit order book that the market maker quotes around the model
price, so trades move it and a large order pays for depth. The close is a
crossing at the model price.

The VIX sits in the middle. It is computed from the index's own conditional
variance, plus a fear response to the day's return, and every variance
process in the market reads it back.

### The order of a session

1. **Open** (`engine.rs:3450-3562`). The crisis episode is stepped (start, end, epicentre), the day's company news is drawn, and each company's opening price $P^{o}$ is set to its last print. On the first session only, the stationary opening splits each company's day-zero premium between $s$ and $v$ (`engine.rs:4031-4070`).
2. **390 ticks** (`market/tick.rs:797-1679`). Each tick: apply the agents' fills from the last step, once, on the first tick (`engine.rs:2114-2138`); draw the market factor and the sector factors; for each company, draw its own noise, update $s$ and $v$, compute $V$ and $P^{\ast}$, draw tick volume, and settle the print through the book. The last tick is the closing cross (`market/tick.rs:1501-1513`).
3. **Close** (`engine.rs:3730-4070`). Each company's GJR variance is updated from the day's noise, the momentum term rolls, jumps are drawn and applied to $s$, the market-factor and sector variances are updated, and the volume state steps.
4. **Macro step** (`engine.rs:4926-5007`, `engine.rs:4363-4605`). The index's conditional variance is computed and the VIX steps, then the economy and the yield curve, the business cycle, the aggregate earnings cycle and the central bank (`engine.rs:4555-4586`).

The next session reads the new VIX, rates and output.

**Randomness.** Each process draws from its own stream, derived from the
run's seed (`rng.rs:363-480`): market, economy, news, jumps, overnight,
volume, crisis epicentre and others. The market stream's schedule depends
only on the roster and the sectors, never on a price or a preset, so two
presets run on the same seed see the same market shocks (`params.rs:30-35`,
`market/mod.rs:15-32`). Some streams, such as the economy's, take a number
of draws that depends on the state, and that dependence stays inside the
stream.

**The opening.** Before session 1 the economy runs 755 macro steps on its
own, with the market frozen and the day's return set to zero
(`engine.rs:1588-1657`). The business cycle's opening phase and its age are
drawn from the cycle's stationary law first (`engine.rs:1501-1525`). Because
the market is frozen during this burn-in, the VIX settles near a fixed level
that depends on the roster and hardly on the seed: on
`Universe.random(40, seed=111)` it opens at 17.66 on 27 of seeds 101 to 130,
and at 17.68 to 17.85 on the other three. The opening corporate yield
depends on where the cycle opens, 2.5% to 6.7% across the same seeds.

## The macro economy

The economy steps once per session, after the close (`engine.rs:4894-4904`).
Its day counter $d$ starts at 1 on the first close. Within one step the order
is: the market P/E and the day's index return are read from prices, the VIX
and the economy are updated, the business cycle may change phase, and then
the central bank meets if a meeting is due (`engine.rs:4948-5007`,
`engine.rs:4437-4596`). All macro draws come from the economy stream.

The price path reads four things from the economy: the **corporate bond
yield**, as the discount rate in fair value; **nominal output** and the
**aggregate earnings cycle**, which scale earnings; and **the VIX**, which
every variance process reads. Everything else in this section matters to
prices only through those four. The bond indices read the 2- and 10-year
yields as well; see [Bonds](#bonds).

Most constants in this section are carried over unchanged from the reference
implementation the engine was ported from, and no design note derives them.
They are **chosen**. The dials that pt-v19 and pt-v20 moved carry their own entries.

### The calendar

The macro clock runs on trading sessions. A month starts when
$d \bmod 21 = 0$ and a quarter when $d \bmod 63 = 0$ (`economy/daily.rs:643-644`).
Levels compound over 252 sessions a year.

| Dial | Value | Kind | Source |
|---|---|---|---|
| `macro_calendar_days_per_year` | 252 | derived | sessions in a trading year |
| `macro_compound_days_per_year` | 252 | derived | same; at 365 the long-run index return was 3.8% a year, at 252 it is 4.9% (design note results/longrun-drift) |
| `macro_burn_in_days` | 755 | chosen | the day the slowest field, the corporate yield, enters one stationary sd of its mean |

### Business cycle

**Timescale:** a transition can happen at any close; hazards are per macro
month. **State:** the phase $\mathcal{P} \in \lbrace E, P, C, T, R \rbrace$
(expansion, peak, contraction, trough, recovery, in that cyclic order) and its
age $a$ in months.

The age advances by $1/21$ each session and resets to zero on a transition
(`economy/daily.rs:1614`, `economy/cycle.rs:258-260`). Below a minimum age
$a_{\min}(\mathcal{P})$ no transition is possible. Above it, the phase
ends with a Weibull hazard plus a state-dependent adjustment:

```math
h^{W}(a) = \min\!\Big(0.8,\ \frac{k}{\lambda}\Big(\frac{a}{\lambda}\Big)^{k-1}\Big),
\qquad
h_d = \mathrm{clip}\big(h^{W}(a_d) + \Delta_d;\ 0,\ 1\big),
\qquad
\Pr[\text{transition on day } d] = h_d / 21
```

(`economy/cycle.rs:31-65`, `economy/cycle.rs:238-262`)

The adjustment $\Delta_d$ reads the economy after the day's update
(`economy/cycle.rs:158-217`). $\pi$ is inflation, $r^{p}$ the policy rate,
$y^{2}$ and $y^{10}$ the 2- and 10-year yields, $u$ unemployment, $g$ real
growth and $\mathrm{PE}$ the market's trailing P/E:

- In expansion (E): $\Delta = 0.1 \cdot \mathbf{1}[\pi > 4] + 0.1 \cdot \mathbf{1}[r^{p} > 5] + \min(0.15, 0.08 (y^{2} - y^{10})) \cdot \mathbf{1}[y^{2} > y^{10}] + \min(0.1, 0.005 (\mathrm{PE} - 28)) \cdot \mathbf{1}[\mathrm{PE} > 28]$, and if $u > 8$ or $g < 1$ the hazard $h^{W} + \Delta$ is cut by 0.2, to no less than 0.
- In contraction (C): $\Delta = 0.1 \cdot \mathbf{1}[r^{p} < 1] + 0.05 \cdot \mathbf{1}[g < -2] + 0.05 \cdot \mathbf{1}[u > 10] + 0.05 \cdot \mathbf{1}[y^{10} - y^{2} > 1.5]$.
- In trough (T): $\Delta = 0.1 \cdot \mathbf{1}[r^{p} < 3] + 0.05 \cdot \mathbf{1}[u > 8]$.
- In recovery (R): the Weibull hazard is cut by 0.1, to no less than 0, if $u > 10$.
- In peak (P): $\Delta = 0$.

The market P/E is the cap-weighted mean of $P_i / (E_i n_d B_{i,d})$ over
profitable companies with a P/E between 0 and 200, using the restated
earnings of [Fair value](#fair-value) (`engine.rs:4948-4994`).

| Phase | Min age (months) | Weibull shape $k$ | Weibull scale $\lambda$ | Mean length (months) |
|---|---|---|---|---|
| E | 6 | 1.8 | 90.45 | 81.0 |
| P | 2 | 2.0 | 6.289 | 6.0 |
| C | 4 | 0.7 | 0.546 | 5.5 |
| T | 2 | 1.5 | 2.564 | 3.5 |
| R | 4 | 1.3 | 10.34 | 12.0 |

(`economy/state.rs:263-302`, `economy/state.rs:351-359`)

The scales are **derived**: they are solved so that each phase's mean length,
from the hazard alone, equals the NBER and BEA phase means for 1990 to 2025
(`economy/state.rs:322-331`; `cycle_us_calibration` = 1). The shapes, the
minimum ages, the 0.8 cap and the ladder constants are **chosen**. With the
ladder acting, a run has 1.05 recessions a decade lasting 9.1 months,
against 1.11 and 9.0 in the US data (design note results/macro-cycle §3-4).

At the opening the phase is drawn with probability proportional to its mean
length, and its age from the phase's survival function
(`economy/cycle.rs:350-408`, `economy/cycle.rs:447-468`;
`cycle_stationary_opening` = 1, derived).

### Growth and output

**Timescale:** a shock on entering a phase, a quarterly and a monthly step,
and a daily level update. **State:** real growth $g$ (percent a year) and
real output $Y$.

Each phase has a growth midpoint $\bar g$: E 3.0, P 1.25, C -1.5, T -0.25,
R 2.25 (`economy/state.rs:263-302`). On entering a phase, growth takes a
shock $\delta$: C $-(2 + 2U)$, T -0.5, R +1, E +0.5, P none
(`economy/daily.rs:668-690`), and then growth is updated in three steps:

```math
\text{quarterly: } g \leftarrow \mathrm{clip}\big(g + 0.25\,(\bar g - g) + 0.3\,Z;\ -10,\ 6\big)
```

```math
\text{monthly: } g \leftarrow \mathrm{clip}\big(g + 0.12\,\kappa\,(\bar g - g) + 0.1\,Z;\ -10,\ 6\big),
\quad \kappa = 2 \text{ when growth has the wrong sign for the phase, else } 1
```

```math
\text{daily: } Y_d = Y_{d-1}\Big(1 + \frac{g_d}{100 \cdot 252}\Big)
```

(`economy/daily.rs:710-763`, `economy/daily.rs:726`). "Wrong sign" means
$g > 0$ in C or T, or $g < 0$ in R or E. Fiscal stimulus adds to $g$ at the
end of the monthly step, below.

### Unemployment

**Timescale:** monthly. **State:** unemployment $u$, long-term unemployment
$\ell$, and the structural rate $u^{\ast}$ (all percent).

```math
u_d = \mathrm{clip}\Big(u + 0.3\,\theta^{u}_{\mathcal{P}} + 0.2\,(2 - g) + 0.06\,(u^{\ast} - u)
 - 0.08\,g\,\mathbf{1}[\mathcal{P} \in \lbrace E, R\rbrace,\ g > 1] + 0.06\,Z;\ 2.5,\ 15\Big)
```

(`economy/daily.rs:766-785`). The phase trends $\theta^{u}$ are E -0.05,
P 0, C 0.30, T 0.04, R -0.10. The $0.2 (2 - g)$ term is Okun's law. The
structural rate carries hysteresis (`economy/daily.rs:953-965`):

```math
\ell \leftarrow \begin{cases} \ell + 0.05\,(0.4\,u - \ell) & \mathcal{P} \in \lbrace C, T \rbrace \\ \max(0.5,\ 0.97\,\ell) & \text{otherwise} \end{cases}
\qquad u^{\ast} = 4 + 0.3\,\ell
```

All constants are chosen. Unemployment spends much of an expansion on its
2.5 floor: the design note's long-run mean is 3.6% against 5.7% in the US.

### Inflation

**Timescale:** monthly for inflation and wages; daily for the price level.
**State:** inflation $\pi$, wage growth $w$ (percent a year), the price level
$Q$ (CPI).

```math
w \leftarrow w + 0.15\,(w^{\ast} - w),\qquad
w^{\ast} = \begin{cases} \max\big(0.7\,\pi + 0.5\,(u^{\ast} - u),\ 0.8\,\pi\big) & \pi > 3 \\ 0.7\,\pi + 0.5\,(u^{\ast} - u) & \text{otherwise} \end{cases}
```

```math
\pi_d = \mathrm{clip}\Big(\pi + \kappa_\pi\,(2 - \pi) + 0.04\,\theta^{\pi}_{\mathcal{P}}
 - 0.2\,(u_d - u^{\ast}) + W + R^{r} + \Omega - 0.0003\,(e - 100) + 0.0003\,(\tau - 5) + 0.04\,Z;\ \pi_{\min},\ \pi_{\max}\Big)
```

(`economy/daily.rs:827-836`, `economy/daily.rs:796-862`). Most terms read last
month's values; the Phillips term reads this month's unemployment $u_d$,
and the terms are these:

- $-0.2 (u_d - u^{\ast})$ is the Phillips curve.
- $W = 0.08\max(0, w - 2) + 0.02 (w - 4)(\pi - 3) \cdot \mathbf{1}[w > 4, \pi > 3]$ is wage pressure (`economy/daily.rs:838-846`).
- $R^{r}$ is a real-rate drag: $-0.04 (r^{p} - \pi)$ when the policy rate is above inflation, $-0.015 (r^{p} - 3)$ when it is below inflation but above 3, else 0 (`economy/daily.rs:800-806`).
- $\Omega$ is the oil pass-through: $0.01 (o - 80)$ above USD 80 a barrel, $0.005 (o - 50)$ below USD 50 (`economy/daily.rs:808-814`).
- $e$ is the dollar index and $\tau$ the tariff rate. The tariff rate stays at 5, so its term is 0, unless a scenario changes it.
- The phase trends $\theta^{\pi}$ are E 0.015, P 0.015, C -0.02, T -0.01, R 0.01.

The price level compounds daily: $Q_d = Q_{d-1} (1 + \pi_d / (100 \cdot 252))$
(`economy/daily.rs:1009`).

| Dial | Value | Kind | Source |
|---|---|---|---|
| inflation target | 2.0 | chosen | |
| `inflation_reversion` $\kappa_\pi$ | 0.55 a month | chosen | reference value; it gives too little persistence against FRED CPI (lag-1 autocorrelation 0.936 against 0.978, `params.rs:313-325`) |
| `inflation_floor` $\pi_{\min}$ | -1.0 | guard | |
| `inflation_ceiling` $\pi_{\max}$ | 6.0 | guard | real CPI inflation peaked at 9.0 in June 2022, so this binds in a 2022-like episode |
| `phillips_curve_coeff` | 0.2 | chosen | |

### Fiscal policy

**Timescale:** monthly (`economy/daily.rs:981-1005`). In contraction and
trough the government runs a stimulus $F$ (percent of GDP a year) and debt
$b$ rises; otherwise the stimulus decays and debt falls slowly in good times:

```math
\mathcal{P} \in \lbrace C, T\rbrace:\ F = \min\big(6,\ 1 + \mathbf{1}[u > 7]\min(4,\ 0.8\,(u - 5))\big),
\quad g \leftarrow g + \frac{0.3\,F}{12},\quad b \leftarrow b + \frac{F}{12}
```

```math
\text{otherwise: } F \leftarrow \max(0,\ F - 0.2),\quad b \leftarrow \max(60,\ b - 0.05) \text{ if } g > 2
```

The fiscal multiplier 0.3 and the other constants are chosen. Debt enters
the 10-year term premium.

### Central bank

**Timescale:** at meetings, which come every 29 to 38 sessions, or 14 to 21
in an inflation crisis ($\pi > r^{p} + 2$ and $\pi > 4$). An emergency meeting
is held at any close where $\pi - r^{p} > 4$ and $\pi > 4$
(`economy/central_bank.rs:132-144`, `economy/central_bank.rs:422-434`). The
design note measures 7.6 meetings a year against the FOMC's 8.

A Taylor rate is computed, and the gap to it gates a decision ladder. The
bank never jumps to the Taylor rate (`economy/central_bank.rs:155-164`):

```math
r^{T} = 2 + 0.5\Big(\frac{\pi}{2} - 1\Big) + 0.5\,(\pi - 2) + 0.5\,(4 - u) + 0.1\,H
= 2.5 + 0.75\,\pi - 0.5\,u + 0.1\,H,
\qquad \Delta = r^{T} - r^{p}
```

$H \in [-1, 1]$ is a hawkish score the ladder moves. The first matching row
sets the change $\delta$ in the policy rate (`economy/central_bank.rs:181-285`):

| Row | Condition | Change $\delta$ (points) |
|---|---|---|
| 1 | $g < -2$ and $u > 10$ | $-(1 + 0.5U)$ |
| 2 | $g < 0$ and $u > 8$ | $-(0.5 + 0.5U)$ |
| 3 | $u > 8$ and $\pi < 3$ | -0.75 |
| 4 | C or T, $g < 0$, $u > 7$ | -0.25 if $\pi < r^{p}$, else 0 |
| 5 | C or T, $r^{p} > 5$ | -0.25 |
| 6 | $\Delta > 2$, $\pi > 6$, $r^{p} < \pi$ | +1.0 or +0.75 (unreachable: needs $\pi > 6$) |
| 7 | $\pi - r^{p} > 2$ and $\pi > 4$ | +0.75 |
| 8 | $\Delta > 1$ and $\pi > 4$ | +0.5 |
| 9 | $\Delta > 0.5$ and $\pi > 3$ | +0.25 |
| 10 | $\Delta < -0.5$, $u > 5$, $\pi < 4$ | -0.25 |
| 11 | $\Delta < -1$, C or T, $\pi < 3.5$ | -0.5 |
| 12 | $\pi > 3.5$, $r^{p} < \pi$, $u < 8$ | +0.5 if $\pi - r^{p} > 3$, else +0.25 |
| 13 | $\Delta > 0.5$, $u \le 5$, not C or T | +0.25 (lift-off) |

A rise is scaled by an urgency factor $\max(1, \lvert \pi - 2 \rvert / 2)$,
and the rate is kept in $[0, 8]$:
$r^{p} \leftarrow \mathrm{clip}(r^{p} + \delta;\ 0,\ 8)$
(`economy/central_bank.rs:168-173`, `economy/central_bank.rs:289-301`).

The Taylor coefficients, the ladder and the meeting spacing are chosen, from
the reference implementation. Row 13, `fed_liftoff_rule` = 1, was added in
pt-v19: it mirrors the ladder's cut rows, and it moved the long-run mean
policy rate from 1.7% to 2.6%, against 2.9% in the US 1990 to 2025 (design
note results/macro-cycle §4).

**Quantitative easing** starts when the policy rate is at or below 0.25 in a
contraction, with purchases of USD 120bn a month, and tapers by 15 a meeting
in expansion (`economy/central_bank.rs:358-391`). On pt-v20 it reaches
prices only through a small cut in the 10-year yield; its direct channels
into valuation are switched off.

### Bond yields

**Timescale:** daily, in the macro step after the close, for the 10-year,
the 2-year and the corporate yield; the central bank also resets the 2-year
and the corporate yield at each meeting, all in percentage points.

```math
y^{10} \leftarrow \mathrm{clip}\big(y^{10} + 0.05\,(r^{p} + \mathrm{TP} - y^{10}) + \sigma_{10}\,Z;\ 0.5,\ 12\big),
\qquad
\mathrm{TP} = 1 + \max(0,\ 0.3\,(\pi - 2)) + \max(0,\ 0.002\,(b - 100))
```

The 2-year is its own process, pulled toward the formula it used to equal,
with its own noise (`economy/daily.rs:1492-1510`):

```math
y^{2}_{d+1} = \mathrm{clip}\Big(y^{2}_d + 0.05\,\big(0.85\,r^{p} + 0.15\,y^{10} - y^{2}_d\big) + \sigma_{2}\,Z;\ 0,\ 12\Big)
```

Here $y^{10}$ is the value just stepped (`economy/daily.rs:1475-1492`). At a
meeting the 2-year is reset to $0.85\,r^{p} + 0.15\,y^{10}$
(`economy/central_bank.rs:315-316`).

**Flight to quality.** The session's cap-weighted index return $R_d$, in
percent, open to close, moves both Treasury yields the same day
(`economy/daily.rs:1512-1546`):

```math
\Delta^{Q}_d = \begin{cases} +g_Q\,R_d & \pi < 3 \\ -g_Q\,R_d & \pi > 4 \\ 0 & \text{otherwise} \end{cases},
\qquad
y^{10} \leftarrow y^{10} + \Delta^{Q}_d,\quad y^{2} \leftarrow y^{2} + \Delta^{Q}_d
```

So in the usual low-inflation regime a 1% fall in the index takes 0.8 basis
points off both yields, and Treasuries rally when stocks fall.

**The corporate yield** moves every session by the change in the 10-year
and by the meeting formula's own VIX term, and the meeting re-sets its level
(`economy/daily.rs:1548-1570`):

```math
y^{c}_{d+1} = \max\Big(y^{c}_d + \big(y^{10}_{d+1} - y^{10}_d\big) + 0.02\,m_{\mathcal{P}}\,\big(X_{d+1} - X_d\big),\ \ y^{10}_{d+1} + 0.8\Big)
```

At a meeting the 10-year is pulled halfway to $r^{p} + 1 + \max(0, 0.3(\pi - 2))$
plus the rate change, and the corporate yield is set
(`economy/central_bank.rs:303-355`):

```math
y^{c} = y^{10} + \mathrm{clip}\Big(\big(1 + 0.02\,(X - 12)\big)\,m_{\mathcal{P}};\ 0.8,\ 6\Big),
\qquad m:\ E\ 1.0,\ P\ 1.1,\ C\ 2.8,\ T\ 3.5,\ R\ 1.4
```

A daily floor, $y^{c} \ge y^{10} + 0.8$, also applies
(`economy/daily.rs:1655-1665`). So the discount rate that fair value reads
moves every session with the 10-year and the VIX, and is re-anchored at
meetings.

| Symbol | Dial | Value (pt-v19) | Kind | Source |
|---|---|---|---|---|
| $\sigma_{10}$ | `treasury_10y_noise` | 0.025 (0.03) pp a session | measured | daily sd of the 10-year's change, FRED DGS10 2015 to 2025, 5.41 bp; graded row R2 reads 4.72 bp against a band of 4.54 to 6.27 |
| $\sigma_{2}$ | `treasury_2y_noise` | 0.022 (0, formula only) | measured | FRED DGS2, 5.23 bp; row R1 reads 4.48 against 3.65 to 6.80 |
| $g_Q$ | `flight_to_quality_gain` | 0.008 (0.02, never fired) pp per % | measured | correlation of stock and Treasury returns, SPY against IEF 2015 to 2025, -0.16; row R3 reads -0.154 |
| | `flight_to_quality_day` | 1 (0) | derived | a switch: the step reads the session's own return |
| | `corporate_yield_daily` | 1 (0) | derived | a switch; stock and investment-grade bond returns, SPY against LQD, correlate +0.27, and row R4 reads +0.231 |
| | `daily_credit_floor_gain` | 1.0 | chosen | without the floor the spread drifted to 0.42 points within 121 days (`params.rs:4611-4626`) |

The 2-year's 0.05 pull, the regime thresholds, the spread multipliers and
the 0.8 floor are chosen. One limit: the model's inflation almost never
leaves the under-3% regime, so stocks and Treasuries are nearly always in
flight to quality. They match the pooled 2015-24 correlation, not the
positive one of a 2022-style inflation regime (`params.rs:6902-6905`).

### The aggregate earnings cycle

**Timescale:** one step a session, in the macro step, after the phase
check and before the central bank (`engine.rs:4555-4580`). **State:**
$\chi_d$, a log level on every company's earnings beyond what nominal
output gives them (`economy/state.rs:430-435`).

The level is pulled toward a target set by the business cycle
(`engine.rs:1116-1126`, `engine.rs:4559-4580`):

```math
\chi^{\ast}(\mathcal{P}) = \begin{cases} -\delta_E & \mathcal{P} \in \lbrace C, T \rbrace \\ \delta_E\,u_E & \mathcal{P} \in \lbrace E, P, R \rbrace \end{cases},
\qquad
\chi_{d+1} = \chi_d + \alpha_E\big(\chi^{\ast}(\mathcal{P}_{d+1}) - \chi_d\big),
\qquad \alpha_E = 1 - 2^{-1/H_E}
```

Earnings therefore fall toward $e^{-0.35} - 1 = -29.5\%$ in a contraction
and trough and recover toward $+3.2\%$ otherwise; the upside is set so the
level averages to zero over a cycle. The run opens at the target of the
phase it opens in (`engine.rs:1105-1112`). $\chi$ multiplies every
company's restated earnings and book value through $n_d$ in
[Fair value](#fair-value), so a move written at the close reaches prices on
the next session.

| Symbol | Dial | Value (pt-v19) | Kind | Source |
|---|---|---|---|---|
| $\delta_E$ | `earnings_cycle_depth` | 0.35 (0, off) | measured | picked on row B9, the spread of annual index returns (S&P 500 1990-2024, 17.4%), on 90 pooled histories (box ptv20e4); s.e. 0.14. Shiller's reported earnings fell a median 17% around NBER recessions; row E1 reads -28% against a band of -40% to -4.6% |
| $u_E$ | `earnings_cycle_upside` | 0.09 (0) | derived | $q/(1-q)$ with $q$ = 9/108, the share of months in contraction and trough in the US phase table |
| $H_E$ | `earnings_cycle_half_life` | 60 sessions | chosen | not searched |

With the cycle carrying part of the index's year-to-year variance, pt-v20
takes transient variance out by as much: the market factor's daily sigma is
0.85 of pt-v19's and market jumps come at half the rate (see the tables in
[The factor structure](#the-factor-structure) and [Jumps](#jumps)). Both
were picked on the same grid, which held the bear-market count (row B3)
and the spread of annual returns (row B9) in band.

### Oil and the dollar

**Timescale:** daily. Oil reaches prices only through inflation; the dollar
reaches them through inflation and oil.

```math
o \leftarrow \mathrm{clip}\Big(o + 0.03\,\big[(75 + 3g)(1 + a_d) - o\big] + p^{I} - 0.08\,(e - 100) + \mathrm{opec} + 2Z;\ 35,\ 150\Big)
```

(`economy/daily.rs:1109-1142`). $a_d = 0.03 \sin(2\pi (d' - 62)/252)$ is a
seasonal term on the target, with $d'$ the day of the macro year. $p^{I}$ is
an inventory pressure that is zero while inventory is between 40 and 60
(inventory is a driftless random walk at `oil_supply_response` = 1,
derived). Every 63 sessions an OPEC decision adds $\pm(2.5 + 3U)$ with
probability 0.55 when the price is more than USD 10 from 80, or
$3(U - 0.5)$ with probability 0.2 otherwise (`economy/daily.rs:1069-1107`).

```math
e \leftarrow \mathrm{clip}\Big(e + 0.02\,\big(100 + 3\,(r^{p} - 2.5) - e\big) + 0.05\,(X - 25.5)^{+} + 0.3\,Z;\ 80,\ 130\Big)
```

(`economy/daily.rs:1198-1215`). Above a VIX of 25.5 the dollar gets a
safe-haven bid (`usd_crisis_vix_threshold`, chosen).

Gold, copper, housing, confidence, the fear and greed index and the trade
balance are computed each day and read by nothing on the price path.

### True and published state

The economy's true state drives prices. An observer reads the state as
published, which can follow the true state the way the real agencies
publish it. The NBER dates a turn of the business cycle months after it
happens: it announced the December 2007 peak on 1 December 2008 and the June
2009 trough on 20 September 2010. The BEA's first estimate of a quarter's GDP
growth comes about a month after the quarter ends.

Five dials, added in 0.8.5 and 0 on every preset through pt-v19, act on
this. Two publish the phase and GDP growth late. One makes the true
unemployment rate turn over months instead of in one monthly step. One
points the fear and greed index at the published figures, and one prices a
macro step at the moment it is published. They answer an independent audit
of pt-v20, which found that timing rules on the reported macro data beat
buy-and-hold. Holding the roster and going to cash while
`macro_fields["cycle"]` read contraction or trough gained 4.39 points a year
over holding, in 30 of 30 21-year histories (design repository,
`programme/handover-2026-09-25/audit/pt-v20-audit.md`, finding 1).

| Figure | The true value is read by | The published value is read by |
|---|---|---|
| business-cycle phase | the phase hazards and phase terms of the macro step, the central bank, the spread multiplier, the earnings cycle and its anticipation, the stress intensity; a pin writes it; `state_snapshot()["economy"]["cycle_phase"]` | `macro_fields["cycle"]`, `macro_state.cycle`, a World's trace rows, the LLM adapters' observations, the fear and greed index under its switch |
| GDP growth | output, earnings, unemployment, the phase hazards, the central bank; a pin writes it; `state_snapshot()["economy"]["gdp_growth"]`, the `macro.growth` intervention's read, the Oracle's drift | `macro_fields["gdp_growth"]`, `macro_table()` and so a dataset export's `macro.arrow`, the fear and greed index under its switch |

Every other field of `macro_fields` reports the value the engine holds. The
policy rate is known from the meeting that sets it, and yields, the VIX and
oil are market prices, known as they print. Inflation and unemployment change
only at the monthly step and are reported on the close that computes them,
where the BLS publishes both a week or two after the month. A sandboxed
agent's market view serves `macro_fields` through an allowlist of published
fields and refuses `state_snapshot()`, which carries the true state.

With every dial at 0 the published value is the true one, the snapshot and
the state hash are the ones they were before 0.8.5, and every known-answer
digest is unchanged.

### The published phase

**Timescale:** at every close. **State:** the phases of the last $L_c + 1$
closes, oldest first.

With `cycle_publication_lag` $L_c$ in sessions, the phase published after
close $d$ is the true phase $L_c$ closes before:

```math
\hat{\mathcal{P}}_d = \mathcal{P}_{\max(d - L_c,\ 0)}
```

$\mathcal{P}_0$ is the opening phase, the one the run opens in after the
burn-in and the stationary opening draw, so it stays published until $L_c$
sessions have closed (`engine.rs:1340-1345`). At the end of each close's
macro step, after the cycle and the central bank, the engine appends the
phase to the history and drops the oldest (`engine.rs:1392-1401`,
`engine.rs:5047`). The burn-in runs the same step, and the construction then
refills the history with the opening phase (`engine.rs:1185`).

A scenario or a `pin_macro` that sets the phase sets the true phase at once,
so prices, the earnings cycle and the hazards react as they did before. The
new phase is published $L_c$ closes after the first close it holds for, and
a pinned phase reads back from `macro_fields["cycle"]` only then. The
packaged `recession.yml`, for example, sets contraction on day 50 and trough
on day 365, and an observer reads them about $L_c$ sessions later. A
turn announced in a World's trace rows or a hosted market log's cycle events
arrives on the same schedule.

`state_snapshot()["economy"]["cycle_phase"]` stays the true phase. While
$L_c > 0$ the economy block also carries `cycle_history`, the $L_c + 1$ phase
names oldest first, and the state hash takes the history after the phase, as
a `u32` length then each name (`engine.rs:6501-6506`,
`manifest.state_hash`). A restore refuses a history of the wrong length, or
any history on an engine whose lag is 0. A snapshot without one, restored
under the dial, refills the history with the restored phase, so that phase is
published at once.

| Dial | Value | Kind | Source |
|---|---|---|---|
| `cycle_publication_lag` $L_c$ | 0, off; a whole number of sessions up to 2520. pt-v20 sets 252 | chosen | the NBER's announcement delay, about a year for the 2007-09 recession; the owner's ruling of 2026-09-25 |

### Published GDP growth

**Timescale:** a quarterly figure, released at a close. **State:** the
figure published, the quarter being averaged (its index, the closes in it
and their sum), and the averaged quarters waiting for release.

With `gdp_publication_lag` $L_g$ in sessions, growth is reported as a
quarterly figure. Quarter $k$ is the days $kq$ to $(k+1)q - 1$ of the macro
calendar, with $q = 63$ on pt-v19 and pt-v20 (90 on the 365-day calendar
of the presets before pt-v19), and day 0, the opening, is the first day of quarter 0.
$g_d$ is the true growth after close $d$, and $g_0$ the opening growth. A
quarter's figure is its mean, released on the close $L_g$ sessions after its
last day:

```math
\bar g_k = \frac{1}{q} \sum_{d = kq}^{(k+1)q - 1} g_d,
\qquad
\hat g_d = \begin{cases} g_0 & d < q - 1 + L_g \\ \bar g_{k^{\ast}},\quad k^{\ast} = \max\lbrace k : (k+1)q - 1 + L_g \le d \rbrace & \text{otherwise} \end{cases}
```

(`engine.rs:1229-1234`, `engine.rs:1298-1325`). The step runs at the end of
each close's macro step, after the phase is recorded (`engine.rs:5051`):
the close's growth joins its quarter, the first close of a new quarter queues
the last one's mean, and every figure due by that close is released. The
construction seeds the figure with the opening growth after the burn-in
(`engine.rs:1187`). A pin on growth writes the true growth, which reaches the
published figure only through the mean of the quarter it falls in.

The daily growth steps at every change of phase: the growth shock on entering
a contraction is $-(2 + 2U)$ points (see
[Growth and output](#growth-and-output)), so a daily figure gave the turn
away on the day it happened. A quarterly mean released late dilutes and
delays that step as the real figure does.

`state_snapshot()["economy"]["gdp_growth"]` stays the true daily growth.
While $L_g > 0$ the economy block also carries `gdp_publication`, with the
keys `published`, `quarter`, `count`, `sum`, `pending_days` and
`pending_values`, in the economy's percent. The state hash takes it after the
unemployment impulse below: the published figure, the quarter, the count, the
sum, then a `u32` count of pending releases and each one's day and figure
(`engine.rs:6515-6526`). A restore refuses the block on an engine whose lag
is 0, a quarter with no close in it, a non-finite figure and releases out of
order. A snapshot without it, restored under the dial, publishes the
restored growth and averages its quarter from the restore day on.

| Dial | Value | Kind | Source |
|---|---|---|---|
| `gdp_publication_lag` $L_g$ | 0, off (growth reported daily); a whole number of sessions up to 2520. pt-v20 sets 21 | chosen | the BEA's advance estimate, about a month after the quarter; the owner's ruling of 2026-09-25 |

### Unemployment's adjustment

**Timescale:** monthly. **State:** the impulse $m$, the monthly change the
rate is making from its cyclical drivers, in points a month.

[Unemployment](#unemployment) moves at each monthly step by the NAIRU pull,
the noise and its cyclical drive in full,

```math
D = 0.3\,\theta^{u}_{\mathcal{P}} + 0.2\,(2 - g) - 0.08\,g\,\mathbf{1}[\mathcal{P} \in \lbrace E, R\rbrace,\ g > 1]
```

(`economy/daily.rs:662-672`), where $g$ is the month's growth after its
monthly step. So the first monthly step of a contraction carried a rise of
about 1.2 points, four times the spread of a monthly change otherwise, and
announced the turn within a month (desk seeds 201 to 212, 2026-09-25). With
`unemployment_adjustment_half_life` $H_u$ in sessions, the drive reaches the
rate through a partial adjustment (`economy/daily.rs:833-857`):

```math
m_d = m + a\,(D - m),
\qquad
a = 1 - 0.5^{M / H_u},
\qquad
u_d = \mathrm{clip}\big(u + m_d + 0.06\,(u^{\ast} - u) + 0.06\,Z;\ 2.5,\ 15\big)
```

$M$ is the macro month in sessions, 21 on pt-v19 and pt-v20. The NAIRU pull
and the noise are as before, and the noise draw is taken in the same place. The impulse opens at the drive of the starting economy,
before the burn-in, which then runs it (`engine.rs:1170`,
`engine.rs:1204-1213`). At $H_u = 84$ sessions, $a = 0.159$, and the first
monthly rise of a contraction is about 0.16 points on the same seeds. US
unemployment rose from 4.3% to 5.5% over the 2001 recession and from 5.0% to
9.5% from December 2007 to June 2009, by 0.1 to 0.3 points in each first
month.

This dial moves the true unemployment rate, not a published copy of it, and
so moves everything that reads the rate: inflation, confidence, the central
bank and the phase hazards. While $H_u > 0$ the snapshot's economy block
carries `unemployment_impulse`, and the state hash takes it after the phase
history and before the GDP figure (`engine.rs:6509-6511`). A restore refuses
it on an engine whose half-life is 0, and re-seeds it from the restored
economy when a snapshot has none.

| Dial | Value | Kind | Source |
|---|---|---|---|
| `unemployment_adjustment_half_life` $H_u$ | 0, off; up to 2520 sessions. pt-v20 sets 84 | fitted | FRED UNRATE over the 2001 and 2007-09 recessions; matched to their first months, with no standard error |

### The fear and greed index

**Timescale:** daily. **State:** the index $F \in [0, 100]$.

```math
F_d = \mathrm{clip}\big(F + 0.25\,(B - F) + 2\,Z;\ 0,\ 100\big),
\qquad
B = 50 + 3\,g' - 0.8\,(X - 15) + b(\mathcal{P}') + 5\,r_d
```

(`economy/daily.rs:1674-1699`). $r_d$ is the day's index return in percent,
and the phase bonus $b$ is E +15, P +5, C -25, T -20, R +10. The index feeds
consumer confidence and gold (`economy/daily.rs:952`,
`economy/daily.rs:1252`), and through confidence the housing figures and
copper. Nothing a price, the central bank, the cycle or a draw reads is
downstream of it. It is reported as `macro_fields["fear_greed_index"]` and
`macro_state.fear_greed_index`.

With `fear_greed_published_inputs` off, $\mathcal{P}'$ and $g'$ are the true
phase and growth, so the index fell about 35 points in the five sessions
after a contraction began and announced the turn to anyone reading it. With
the switch on they are the published phase and growth as of the previous
close, read before the step (`engine.rs:4973-4977`), so the index steps when
the turn is published. The switch adds no state, and with both publication
lags at 0 it changes nothing. On desk seeds 201 to 212, with the cycle lag at
252, the GDP lag at 21 and the unemployment half-life at 84, a rule that trades a five-session
fall in the index beat holding in 1 of 12 histories with the switch on,
against 9 of 12 with it off.

| Dial | Value | Kind | Source |
|---|---|---|---|
| `fear_greed_published_inputs` | 0, off; a switch, 0 or 1. pt-v20 sets 1 | derived | the real index is built from market data and dates no recession |

### Repricing at publication

**Timescale:** at the end of each close's macro step, and at each
`pin_macro`.

The macro step runs after the close, and its results are readable from then
on: the meeting's policy rate, the corporate yield it re-anchors, output and
the cycle. Fair value reaches the price only at the next session's first
tick, so an agent acting before that tick trades at the price from before the
decision. On pt-v20 with the leading dials (anticipation 126 sessions, rate
sensitivity 3, buyback share 0.75), the index fell 76 bp (se 5) in the first
65 minutes after a published hike and rose 171 bp (se 36) after a cut
(pt-v20 audit, finding 3). Event studies place the S&P 500's whole response to
an FOMC statement inside a 30-minute window (Gurkaynak, Sack and Swanson
2005; Bernanke and Kuttner 2005).

With `macro_publication_repricing` on, each public, solvent name that has
traded is re-marked as the step ends (`engine.rs:5458-5466`,
`engine.rs:5476-5573`). With $P$ its last print, $V_0$ its fair value before
the step and $V_1$ after it, both computed as the tick computes them on the
same day (`market/tick.rs:1696-1728`), the new price solves

```math
P' = \mathrm{clip}\Big(\frac{P}{V_0}\,V_1(P');\ 0.01,\ P_{\max}\Big)
```

$P_{\max}$ is the 50,000 price cap (`price_hard_cap`). $V_1$ reads the price
through the buyback term, so the engine iterates from
$P' = P V_1(P) / V_0$ until a step moves nothing, at most 16 times; with the
buyback share at 0 the first step is exact. The mispricing $s$ is left as it
was, so the next tick starts on the model price the new state implies. The
day's high, low and market cap follow the new price. A `pin_macro` re-marks
the same way, around its write (`python_engine.rs:3030`,
`python_engine.rs:3108-3109`).

The re-mark reads the true state the step leaves, as the next tick would.
The policy rate and the corporate yield are published as they are set, so
for them the two agree. For the phase it prices only what the next tick
would, and publishes nothing. It takes no draw and adds no state: the price
it writes is already in the snapshot and the state hash.

| Dial | Value | Kind | Source |
|---|---|---|---|
| `macro_publication_repricing` | 0, off; a switch, 0 or 1. pt-v20 sets 1 | derived | pt-v20 audit, finding 3; FOMC event studies |

### The economy's outputs

1. **The corporate bond yield** $y^{c}$, as the discount rate in fair value (`fair_value.rs:143-148`). The engine always supplies it, so the fallback to the policy rate never runs.
2. **Nominal output** $N_d = Y_d Q_d$ and **the earnings cycle** $\chi_d$, which scale every company's earnings and book value (`market/tick.rs:369-393`).
3. **The VIX** $X_d$, which every variance process, the jump rate, the crisis gates and the market maker's spread read.

The business cycle reaches prices through these: through growth and
inflation into nominal output, through the earnings cycle, and through the
spread multiplier into the corporate yield. A change of phase starts the
earnings cycle toward its new target at the next close.

## Fair value

**Timescale:** recomputed from scratch every tick for every company
(`market/tick.rs:1067-1101`). Its inputs move at three speeds: the
corporate yield, nominal output and the earnings cycle change once a
session, after the close; the company's fair-value level $v_i$ and the last
print change every tick; a company's published earnings, book value,
revenue growth and sector are fixed unless a scenario writes them.

### The valuation

A company has trailing earnings per share $E_i$, book value per share $K_i$,
revenue growth $\gamma_i$ (a fraction a year) and a sector $k$ with anchor
P/E $\Pi_k$. Earnings are restated each tick for nominal growth, the
earnings cycle, the company's own fair-value level and buybacks:

```math
n_d = \Big(1 + \eta\Big(\frac{N_d}{N_0} - 1\Big)\Big)\,e^{\chi_d},
\qquad
B_{i,t} = \exp\!\Big(\kappa\,\frac{E_i\,n_d\,e^{v_{i,t}}}{P_{i,t-1}}\cdot\frac{d}{252}\Big),
\qquad
\hat E_{i,t} = E_i\,n_d\,e^{v_{i,t}}\,B_{i,t},\quad \hat K_{i,t} = K_i\,n_d\,e^{v_{i,t}}\,B_{i,t}
```

(`market/tick.rs:259-272`, `market/tick.rs:369-418`, `market/tick.rs:1081-1092`).
$N_0$ is nominal output at the end of the burn-in, so the nominal factor is
1 on the first session. $\chi_d$ is the earnings cycle
([above](#the-aggregate-earnings-cycle)) and $v_{i,t}$ the company's
fair-value level ([below](#the-fair-value-level)). $d$ is the number of
sessions already closed. $B = 1$ for a company with no earnings.

The rate term shrinks the anchor P/E when the corporate yield is above its
neutral level, more for fast-growing companies:

```math
D_i = 1 + 2\max(0,\ \gamma_i),
\qquad
R_{i,d} = \max\!\Big(0.5,\ 1 - 1.5\,D_i\Big(\frac{y^{c}_d}{100} - r^{\ast}\Big)\Big)
```

(`fair_value.rs:143-148`, `fair_value.rs:184-190`), and fair value is:

```math
V_{i,t} = \begin{cases}
\max\big(0.01,\ \hat E_{i,t}\,\Pi_{k}\,R_{i,d}\big) & E_i > 0 \\
\max\big(0.01,\ 1.2\,\hat K_{i,t}\big) & E_i \le 0
\end{cases}
```

(`fair_value.rs:200`, `fair_value.rs:271-300`). A loss-making company is
valued at 1.2 times book, with no rate term.

What follows from this:

- **Rates.** $\partial \ln V / \partial r = -1.5 D_i / R$. At the neutral rate, 100 basis points on the corporate yield moves a profitable company's fair value by 1.5% to 2.7%, depending on its growth. Loss-makers do not move.
- **Earnings growth** comes only from nominal output and buybacks. Revenue growth sets duration and nothing else. There are no dividends.
- **Buybacks** add about $\kappa E/P$ a year, about 1.9% at a typical earnings yield. They retire no shares. The yield is read at the current price and applied over every elapsed session, so a company whose price falls toward the 0.01 floor reads a yield in the hundreds. Under `buyback_yield_cap` $\bar b$ the yield in $B$ is $\min(\kappa E_i n_d / P_{i,t-1},\ \bar b)$ (`market/tick.rs:270-277`). pt-v20's $\kappa$ of 0.75 is about 4.2% a year at the same earnings yield, and its cap of 0.15 binds only on a company priced under five times earnings.
- Nominal output grows 4.8% a year over a long run, against 4.8% in the US 1990 to 2025 (design note results/macro-cycle §0). On pt-v20 the index returns 5.7% a year over 21 years against a target of 6.25% (row B8), and its annual returns have a standard deviation of 16.8% against the S&P 500's 17.4% (row B9).

| Symbol | Dial | Value | Kind | Source |
|---|---|---|---|---|
| $r^{\ast}$ | `neutral_discount_rate` | 0.0482 | derived | the corporate yield the economy rests at after pt-v18's burn-in (`params.rs:2652-2694`); see [Known gaps](#known-gaps) |
| $\eta$ | `earnings_nominal_growth` | 1.0 | derived | holds the earnings share of nominal output constant |
| $\kappa$ | `buyback_payout_share` | 0.75 (0.3333) | fitted (chosen) | pt-v20's 0.75 is calibrated to the index's one-year drift, not to buybacks: about 4.2% a year at a typical earnings yield. pt-v19's 0.3333 is US large-cap net buybacks of 1.5% to 2.0% of market value, 2000 to 2025 (`params.rs`, `ModelParams::buyback_payout_share`); no error bar |
| $\bar b$ | `buyback_yield_cap` | 0.15 (0, off) | guard | keeps the buyback term finite for a company near the price floor |
| $\lambda$ | `rate_pe_sensitivity` | 3 (1.5) | fitted (chosen) | pt-v20's 3 was picked on a grid; pt-v19's 1.5 is the reference implementation's. The S&P 500's P/E fell 4.9% to 5.5% per 100 bp of Baa in 2022 |
| | rate-term floor | 0.5 | guard | |
| | duration scale | 2.0 | chosen | reference implementation |
| | loss-maker price to book | 1.2 | chosen | reference implementation |
| | `market_pe_buybacks` | 1.0 | derived | the market P/E divides by the same buyback factor |

The sector anchors $\Pi_k$ are in [The sectors](#the-sectors). They are
chosen: carried from the reference implementation, with no market data named.

### The market's permanent share

**Timescale:** every tick, and the close's jumps. **State:** a fair-value
level $v_i$ per company, 0 on every preset through pt-v19.

pt-v20 moves part of each tick's shocks out of the mispricing and into a
permanent fair-value level, so that part does not revert. The level scales a
company's restated earnings and book value by $e^{v_i}$ before the buyback
term and the valuation above (`market/tick.rs:1778-1785`). After the tick's
update of $s$ (see [Mispricing](#mispricing)), with $\psi$ =
`fair_value_news_share`:

```math
\Delta v_{i,t} = \psi\,\big(u\,(\varepsilon^{I}_{i,t} + \varepsilon^{S}_{i,t}) + N^{own}_{i,t}\big)
 + \psi_m(\sigma_t)\,\big(u\,m_{i,t} + N^{mkt}_{i,t}\big),
\qquad
s_{i,t+1} \leftarrow s_{i,t+1} - \Delta v_{i,t},
\qquad
v_i \leftarrow v_i + \Delta v_{i,t} - \tfrac{1}{2}\Delta v_{i,t}^{2}
```

(`market/tick.rs:1246-1286`). $\varepsilon^{I}$ and $\varepsilon^{S}$ are the
tick's idiosyncratic and sector noise, $N^{own}$ the news naming the company,
its peers or its sector, $N^{mkt}$ the market-wide news, and $u$ the intraday
curve (0.15 while the market is closed). The price moves by the whole shock
either way; what changes is how much of it later reverts. The
$-\tfrac{1}{2}\Delta v^{2}$ term keeps $e^{v}$ a martingale. The close's jumps
are split the same way: a company's own jump on $\psi$, the market jump on
$\psi_m$ (`engine.rs:4638-4678`).

The market's share $\psi_m$ = `fair_value_market_share` is cut above a
ceiling on the market factor's current daily sigma $\sigma_t$, with $c$ =
`fair_value_market_vol_cap` and $\sigma_F$ = `market_factor_sigma`
(`market/tick.rs:806-821`):

```math
\psi_m(\sigma) = \begin{cases} \psi_m & c = 0 \ \text{or}\ \sigma \le c\,\sigma_F \\ \psi_m\,c\,\sigma_F / \sigma & \text{otherwise} \end{cases}
```

$m_{i,t}$ is the company's market input. With `fair_value_market_linear` at
0 it is the whole input: the loading on the market draw, the down-tick tilt,
the lagged down-day wire, the crisis injection, the crash amplifier and the
recentring. At 1 it is the plain loading $\beta_i F_t$ alone
(`market/factors.rs:1226`), which has zero mean in every regime. The other
terms are not zero-mean once the VIX is high, because the amplifier fires on
a threshold in baseline sigmas; left in $s$ they are a discount that reverts
as the VIX falls, and made permanent they were a drift that ran as long as
the VIX stayed high.

Why pt-v20 takes it. With every market shock in $s$, the index reverted on
the mispricing's half-life. The ratio of its five-year variance to five
times its one-year variance read 0.42 on the leading dials, against 0.87 for
the S&P 500 over 1871-2023 (pt-v20 audit, major 5; design repository,
`programme/ptv20-registration.md`, twelfth registration, row V1). With the
plain market draw permanent up to 1.5 times the base sigma, ordinary market
news is permanent and the excess a fear regime adds reverts, the form the
evidence takes: mean reversion in index returns concentrates in turbulent
periods (Poterba and Summers 1988; Kim, Nelson and Startz 1991; Spierdijk,
Bikker and van den Hoek 2012). On held-out seeds the two-year and five-year
ratios over one year read 0.80 and 0.66, against bands of 0.75 to 1.15 and
0.55 to 1.20 (box ptv20vr9). The earnings cycle then carries less of the
index's yearly spread, and pt-v20's depth goes from 0.35 to 0.2, where the
aggregate fall in a contraction reads -0.173 against Shiller's median -0.17.
`opening_market_sigma`, the spread of the market's opening mispricing, goes
from 0.10 to 0.001, since little of the market's variance stays in $s$.

| Symbol | Dial | Value | Kind | Source |
|---|---|---|---|---|
| $\psi_m$ | `fair_value_market_share` | 0, off; pt-v20 1 | fitted | the end point; row V1 on grids ptv20vr1 to vr9 |
| | `fair_value_market_linear` | 0, off; a switch. pt-v20 1 | derived | the plain loading is the zero-mean part |
| $c$ | `fair_value_market_vol_cap` | 0, no ceiling; pt-v20 1.5 | fitted | a ceiling of 2 took the index volatility to 27.9% against 18.1% (box ptv20vr4) |

### Volatility feedback

**Timescale:** wherever fair value is read, and once a session for the
smoothed exposure. **State:** the exposure $x$, while both the gain and the
half-life are set.

Higher expected volatility raises the return investors require and lowers
the price (French, Schwert and Stambaugh 1987; Campbell and Hentschel 1992).
With `fair_value_vix_discount` $g$ and `fair_value_vix_knee` $K$, every
company's fair value is scaled by

```math
V_{i,t} \leftarrow V_{i,t}\,e^{-g\,\beta_i\,x},
\qquad
x = \begin{cases} \max\big(0,\ \ln(X/K)\big) & H_x = 0 \\ x_d & H_x > 0 \end{cases},
\qquad
x_{d} = x_{d-1} + \big(1 - 0.5^{1/H_x}\big)\big(\max(0, \ln(X_d/K)) - x_{d-1}\big)
```

(`market/tick.rs:1690-1723`, `engine.rs:5033-5043`). $X$ is the VIX and
$H_x$ = `fair_value_vix_half_life` in sessions; the smoothed exposure steps
once at each close, after the VIX has moved, and takes no draw. The discount
is applied in the tick (`market/tick.rs:1128`), the overnight opening print
(`engine.rs:4124`), the re-mark at publication and the stationary opening
(`market/tick.rs:1755`, `market/tick.rs:1796`). It has no permanent part: it
deepens a fall while fear is high and is given back as the VIX comes down.
While $g > 0$ and $H_x > 0$ the snapshot's economy block carries
`vix_feedback`, and the state hash takes it (`engine.rs:6418-6421`).

Why pt-v20 takes it. With the market's plain shocks permanent, the driven
2020 path fell 0.192 in 41 sessions against the S&P 500's 0.339 in 23 (long-run
row F1). Read unsmoothed, the discount's whole daily change landed with the
VIX's move and took the sessions under -5% from 10.6 to 18 to 25 a decade.
With a knee of 40 and a 5-session half-life the fall reads 0.266 in 35.5
sessions and the sessions under -5% 11.5 a decade, against the tape's 6.2 and
a band up to twice it (box ptv20vr9). At knees of 30 and 35 every smoothed
arm tried ran past twice the tape.

| Symbol | Dial | Value | Kind | Source |
|---|---|---|---|---|
| $g$ | `fair_value_vix_discount` | 0, off; pt-v20 0.35 | fitted | held-out grids ptv20vr6 to vr9 |
| $K$ | `fair_value_vix_knee` | 30, unread at $g = 0$; pt-v20 40 | fitted | held-out grids ptv20vr8 and vr9 |
| $H_x$ | `fair_value_vix_half_life` | 0, the VIX as it stands; pt-v20 5 | fitted | held-out grids ptv20vr6 to vr9 |

### The sectors

Twelve sectors, each with an anchor P/E, a relative volatility $v_k$ and a
daily sigma $\sigma_k$ (`sectors.rs:93-106`). All are chosen, from the
reference implementation.

| Sector | P/E | $v_k$ | $\sigma_k$ a day | Epicentre weight |
|---|---|---|---|---|
| technology | 32 | 1.2 | 0.025 | 0 |
| financial_services | 12 | 1.1 | 0.015 | 0.6 |
| healthcare | 24 | 0.9 | 0.018 | 0 |
| energy | 10 | 1.3 | 0.015 | 0 |
| consumer_discretionary | 20 | 1.0 | 0.018 | 0 |
| consumer_staples | 20 | 0.7 | 0.008 | 0 |
| industrials | 17 | 1.0 | 0.015 | 0 |
| materials | 14 | 1.2 | 0.015 | 0 |
| real_estate | 35 | 0.9 | 0.008 | 0 |
| utilities | 16 | 0.6 | 0.008 | 0 |
| telecommunications | 14 | 0.8 | 0.010 | 0 |
| transportation | 15 | 1.1 | 0.015 | 0 |

- $v_k$ centres the betas the universe generator draws, and scales the market maker's spread.
- $\sigma_k^2$ is each company's starting GJR variance and the reference for its floor and ceiling.
- The epicentre weight is the probability that a crisis starts in that sector; the remaining 0.4 is "no epicentre". It is derived from five crises on the reference roster (2000-02, 2008-09, 2011, 2020, 2022), counting a sector hit at least 1.3 times as hard as the median sector (`sectors.rs:52-78`).

The sector table has no per-sector beta or factor loading. Loadings are
global and scale with each company's own beta.

### The universe and the opening

`Universe.random(n, seed)` draws a roster from its own random stream,
independent of the market's (`universe.rs:36`, `universe.rs:197-275`).
Company $i$ is in sector $k = i \bmod 12$, and:

```math
M_i \sim \mathrm{LogU}(2 \times 10^{8},\ 2 \times 10^{12}),\quad
P_{i,0} \sim \mathrm{LogU}(3,\ 600),\quad
S_i = M_i / P_{i,0}
```

```math
E_i = \begin{cases}
-P_{i,0}\,\mathcal{U}(0.01,\ 0.09) & \text{with probability } 0.11 \\
P_{i,0} / (\Pi_k\,u^{PE}_i),\quad u^{PE}_i \sim \mathrm{LogU}(1/1.7,\ 1.7) & \text{otherwise}
\end{cases}
\qquad
K_i = \frac{P_{i,0}}{1.2}\,u^{K}_i,\quad u^{K}_i \sim \mathrm{LogU}(1/2.4,\ 2.4)
```

```math
\gamma_i = \frac{\Pi_k - 10}{100} + \mathcal{U}(-0.06,\ 0.14),\quad
\beta_i = \max\big(0.15,\ v_k\,\mathcal{U}(0.6,\ 1.35)\big),\quad
\bar A_i = \max\big(1000,\ S_i\,\mathcal{U}(0.001,\ 0.02)\big)
```

$\mathcal{U}(a, b)$ is uniform and $\mathrm{LogU}(a, b)$ log-uniform. $M$ is market cap, $S$ shares
outstanding, $\bar A$ average daily volume in shares. Short interest is
$S_i \mathrm{LogU}(0.004, 0.30)$, and all these ranges are chosen.

**The stationary opening.** A roster opens with a day-zero premium
$g_i = \ln(\max(0.01, P_{i,0})/V_{i,0})$ that has a cross-sectional
standard deviation of about 0.33 (`market/tick.rs:1654-1679`), for a
profitable company $\ln u_i^{PE} - \ln R_{i,0}$ and for a loss-maker
$-\ln u_i^{K}$. The model's own stationary spread of $s$ is about 0.016, so
putting the whole premium into $s$ would open every run with a months-long
drift back to fair value. Instead, at the first open tick, the premium is
split between $s$ and the fair-value level $v$ (`engine.rs:4031-4070`), using
$n + 1$ normals drawn once from the opening stream when the engine is built
(`engine.rs:1024-1033`):

```math
s_{i,0} = \mathrm{clip}\big(\sigma_c\,z_{n+1} + \sigma_o\,(z_i - \bar z);\ -0.9,\ 0.9\big),
\qquad
v_{i,0} = g_i - s_{i,0},
\qquad
\bar z = \frac{\sum_i w_i z_i}{\sum_i w_i}
```

with $w_i$ the opening market caps. Each company's mispricing opens at a
draw from its stationary spread, the market's common level at a draw from
its own, and the rest of the premium becomes fair value, so no price moves
at the open: $V_{i,0} e^{v_{i,0}} e^{s_{i,0}} = P_{i,0}$. A company listed
later opens with $s_0 = \mathrm{clip}(g_i;\ -0.9,\ 0.9)$
(`market/tick.rs:1103-1111`).

| Symbol | Dial | Value (pt-v19) | Kind | Source |
|---|---|---|---|---|
| $\sigma_o$ | `opening_mispricing_sigma` | 0.016 (0, off) | measured | the cross-sectional sd of $s$ over sessions 250 to 2,660 on seeds 201 to 212; 0.0155 to 0.0165 |
| $\sigma_c$ | `opening_market_sigma` | 0.10 (0, off) | measured | the time-series sd of the cap-weighted $s$ on seeds 201 to 203: 0.148, 0.105, 0.042 |

Graded: the start-up ratio in row B9, the index's volatility over the first
60 sessions against sessions 250 to 490, reads 1.19 against a band of two
thirds to 1.5.

Companies can also be built from SEC EDGAR filings (`python/tradefloor/edgar.py`):
diluted EPS, shares outstanding, equity over shares as book value, and
year-on-year revenue growth. By default each opens at its fair value.

## The price path

### The model price

**Timescale:** every tick. The model price is fair value, including the
company's fair-value level, times the exponential of the mispricing
(`market/tick.rs:1250`):

```math
P^{\ast}_{i,t} = \max\big(0.01,\ V_{i,t}\,e^{s_{i,t+1}}\big)
```

If $P^{\ast}$ leaves the session band $[0.75 P_{i,d}^{o},\ 1.25 P_{i,d}^{o}]$
it is clamped to the band and $s$ is re-derived from the clamped price
(`market/tick.rs:1256-1265`). The printed price is $P^{\ast}$ traded through
the book; see [The market maker and the book](#the-market-maker-and-the-book).

### Mispricing

**Timescale:** every tick, with a momentum term that rolls once a day.
**State:** $s_{i,t}$, and $\mu_{i,d}$, the change in $s$ over the previous
session.

```math
s_{i,t+1} = \mathrm{clip}\Big(\phi_\tau\,s_{i,t}
 + \frac{\theta\,\mu_{i,d}}{390}
 + \frac{L(s_{i,t}, \mu_{i,d})}{390}
 + \frac{N_{i,t} + O_{i,t} + Q_{i,d}}{390}
 + u(\tau_t)\,\varepsilon_{i,t};\ -\bar s,\ \bar s\Big)
```

(`market/tick.rs:1193-1246`), with these terms in order:

- **Mean reversion.** $\phi_\tau = 0.5^{1/(390 H)}$, so $s$ decays by half in $H$ sessions if nothing else acts.
- **Herding.** $\theta \mu$: a share of yesterday's re-rating continues today.
- **The crowd.** $L(s, \mu) = \mathrm{clip}(-g_v s + g_m \mu;\ -\bar L,\ \bar L)$ (`mispricing.rs:174-179`). The crowd buys what trades below fair value and chases yesterday's move a little. It reads $s$ before the tick's update.
- **News** $N$, **order flow** $O$ and the **short squeeze and stop cascade** $Q$, each a daily-equivalent log shock applied at 1/390 a tick. They are defined below. $O$ also carries the permanent impact of agents' fills; see [Agent orders](#agent-orders).
- **Noise.** $\varepsilon_{i,t}$ is the tick's shock from the factor structure, below. $u(\tau) = 1 + 0.2 (2\tau - 1)^4$ is the intraday volatility curve: 1.2 at the open and the close, 1.0 at midday (`market/hours.rs:104-106`).

This is the update before the fair-value level takes its share; the next
subsection subtracts it.

The momentum term rolls at the close, before the jumps
(`market/daily.rs:240-244`): $\mu_{i,d+1} = s_i^{\mathrm{close}} - s_i^{\mathrm{ref}}$,
then $s_i^{\mathrm{ref}} \leftarrow s_i^{\mathrm{close}}$. Jumps are excluded
from the next day's momentum (`jump_momentum_share` = 0,
`engine.rs:4170-4175`).

**As a daily AR(2).** Summed over a session, with the crowd term in its
linear range and no clamp binding, the close-to-close mispricing follows

```math
s_{d+1} \approx \psi^{390}\,s_{d} + (\theta + g_m)\,\bar\psi\,(s_{d} - s_{d-1}) + \text{shocks}_d,
\qquad \psi = \phi_\tau - g_v/390
```

with $\bar\psi$ the session mean of $\psi^{k}$. On pt-v20, as on pt-v19, the two
coefficients are 0.9826 and 0.0382, the roots are 0.9819 and 0.0389, and the
process is stationary. The effective half-life is about 40 sessions, not the
nominal 60, because the crowd's valuation lean adds its own pull. This daily
form is our derivation from the tick equation, not a formula in the code.

| Symbol | Dial | Value | Kind | Source |
|---|---|---|---|---|
| $H$ | `mispricing_half_life_days` | 60 | chosen | |
| $\phi_\tau$ | `s_phi_tick` | 0.99997 | derived | $0.5^{1/(390 \cdot 60)}$ |
| $\theta$ | `momentum_theta` | 0.01855 | fitted | cut in steps from 0.25; the last cut kept the two-year `return_acf1` under its ceiling |
| $g_v$ | `crowd_valuation_gain` | 0.006 a day | chosen | reference implementation |
| $g_m$ | `crowd_momentum_gain` | 0.02 a day | chosen | reference implementation |
| $\bar L$ | `crowd_lean_cap` | 0.02 | guard | |
| $\bar s$ | `mispricing_cap` | 0.9 | guard | $e^{\pm 0.9}$ is 0.41 to 2.46 times fair value |

### The fair-value level

**Timescale:** every tick, and at the close for jumps. **State:** $v_{i,t}$,
a log level on each company's fair value, persistent across sessions
(`market/tick.rs:161-167`).

A company's own shocks are permanent. On each tick the share $\psi$ of its
sector and own noise, and of the news that names it, its peers or its
sector, leaves $s$ for $v$ (`market/tick.rs:1209-1245`):

```math
\Delta_{i,t}^{v} = \psi\Big[u(\tau_t)\big(\xi_i\,\ell_i\,G_{k(i),t} + \xi_i\,I_{i,t}\big) + \frac{N_{i,t} - N_{i,t}^{M}}{390}\Big]
```

```math
s_{i,t+1} \leftarrow \mathrm{clip}\big(s_{i,t+1} - \Delta_{i,t}^{v};\ -\bar s,\ \bar s\big),
\qquad
v_{i,t+1} = v_{i,t} + \Delta_{i,t}^{v} - \tfrac12\big(\Delta_{i,t}^{v}\big)^{2}
```

$N^{M}$ is market-wide news, which stays in $s$. The Ito term keeps
$e^{v}$ a martingale. The price takes the whole shock on the tick either
way; what changes is that the company's part no longer reverts on the
mispricing's half-life. At the close the company's own jump goes to $v$ the
same way, and the market jump stays in $s$ (`engine.rs:4205-4245`):

```math
\Delta_{i}^{v,J} = \psi\,J_{i}^{I},
\qquad
s_i \leftarrow s_i - \Delta_{i}^{v,J},
\qquad
v_i \leftarrow v_i + \Delta_{i}^{v,J} - \tfrac12\big(\Delta_{i}^{v,J}\big)^{2}
```

and the jump is kept out of the next day's momentum. What stays in $s$: the
market leg of the factor structure, market-wide news, the market jump and
its compensator, order flow and agents' impact, the squeeze and cascade
term, reversion, herding, the crowd and the breaker.

| Symbol | Dial | Value (pt-v19) | Kind | Source |
|---|---|---|---|---|
| $\psi$ | `fair_value_news_share` | 1.0 (0, off) | derived | the end point: a company's variance ratio at 60 sessions (row C5) moves from 0.59 to 0.95 against a real 0.92, and the value and momentum signals that a transient $s$ made profitable (rows C6, C7) fall to real sizes |
| | `fair_value_market_share` | 0 | chosen | undetermined; nothing market-wide reaches $v$ |

### The factor structure

**Timescale:** every tick. **Draws:** one market normal $z_t$, one normal
$\zeta_{k,t}$ per sector, one normal $\eta_{i,t}$ per company, all on the
market stream, in that order.

The market factor and the sector factors (`market/tick.rs:850-896`,
`market/tick.rs:290-298`):

```math
f_t = \sqrt{v_d}\,\frac{z_t}{\sqrt{390}},
\qquad
G_{k,t} = \bar\sigma_S\,\rho_d\,\sqrt{h^{S}_{k,d}}\,\frac{\zeta_{k,t}}{\sqrt{390}}
```

$v_d$ is the market-factor variance and $h_{k,d}^{S}$ the sector variance
state, both set at the previous close; see [Volatility](#volatility). The
sector sigma scales with the VIX ratio $\rho_d$ because
`sector_vix_coupling` = 1.

A company's tick shock (`market/factors.rs:820-1072`):

```math
\varepsilon_{i,t} = \Gamma_t\,M_{i,t} + \upsilon_{i,d} + \xi_i\,\ell_i\,G_{k(i),t} + \xi_i\,I_{i,t}
```

```math
M_{i,t} = \beta_i\,f_t\,\big(1 + a\,\mathbf{1}[f_t < 0]\big)\big(1 + a_L\,\mathbf{1}[c_t < 0]\big),
\qquad
c_t = F_{d-1}\,(1 - \tau_t) + \sum_{t' < t} f_{t'}
```

```math
\Gamma_t = 1 + m_A \max\big(0,\ \lvert z_t \rvert - T_A\big),
\qquad
\upsilon_{i,d} = a\,\beta_i\,\frac{\sqrt{v_d/390}}{\sqrt{2\pi}}
```

```math
\ell_i = \ell_0\,\big(1 + b_\ell\,(\beta_i - 1)\big),
\qquad
I_{i,t} = \eta_{i,t}\,\sqrt{\max(h_{i,d},\ h_{\min})}\,\frac{\kappa_I\,c_i}{\sqrt{390}}
```

What each term does:

- $M$ is the market leg. A down tick of the market loads a little more ($a$), and so does any tick while the market is down on a trailing window ($a_L$, the lagged down-beta). $c_t$ blends yesterday's market move, fading through the session, with today's so far. $F_{d-1}$ is yesterday's summed market factor.
- $\Gamma$ is the crash amplifier: a market draw beyond $T_A$ standard deviations loads extra. It fires on 4.6% of ticks.
- $\upsilon$ gives back the mean the down-tick tilt adds, so the tilt changes shape and not drift.
- $\ell_i G$ is the sector leg. The loading rises with beta.
- $I$ is the company's own noise. Its scale is the company's GJR variance $h_{i,d}$, floored, times a size step $c_i$: 0.8 above USD 50bn of market cap, 1.0 above 10bn, 1.3 above 1bn, 1.6 below (`market/factors.rs:692-703`).
- $\xi_i$ is 1 except during a crisis episode with an epicentre, when it is 2.04 for companies in the epicentre sector and 0.80 for the rest; see [Crisis regimes](#crisis-regimes).

Betas are fixed per company when the universe is built.

| Symbol | Dial | Value | Kind | Source |
|---|---|---|---|---|
| $\bar\sigma_M$ | `market_factor_sigma` | 0.006454 (0.007593) a day | fitted | 0.85 of pt-v19's, picked on a grid over 90 pooled histories (box ptv20e4) to hold the bear-market count and the spread of annual returns in band once the earnings cycle carries part of the variance; the baseline of $v_d$ |
| $\bar\sigma_S$ | `sector_factor_sigma` | 0.008583 a day | fitted | a derivation exists only for another value, 0.0107 |
| | `sector_vix_coupling` | 1.0 | chosen | makes the sector sigma scale with the VIX like the market's |
| $\ell_0$ | `sector_loading` | 0.6 | measured | centres `sector_excess_corr` on the reference roster, 1987 to 2025 |
| $b_\ell$ | `sector_loading_beta_slope` | 0.7 | fitted | |
| $\kappa_I$ | `idio_sigma_scale` | 0.5126 | fitted | search |
| $h_{\min}$ | `idio_sigma_floor` | 0.0001 | guard | binds on many company-days |
| $a$ | `market_beta_down_asym` | 0.025 | chosen | no daily measurement on record |
| $a_L$ | `market_beta_down_asym_lag` | 0.46 | fitted | fitted to the lagged correlation-asymmetry statistic; a 7-point grid's best was 0.375 |
| | `market_beta_down_asym_recentre` | 1.0 | derived | $E[f \mathbf{1}(f < 0)] = -\sigma/\sqrt{2\pi}$ |
| $T_A$ | `crash_amplifier_threshold` | 2.0 | chosen | reference implementation |
| $m_A$ | `crash_amplifier_slope` | 0.2 | chosen | reference implementation |
| | `crash_amplifier_conditional_sigma` | 1.0 | derived | measures the shock in today's sigma, which keeps the VIX loop stable |

### Squeeze and cascade terms

**Timescale:** a daily-equivalent drift, set by the previous session's
return $r$ and short interest over float $\mathrm{SIR}$
(`market/factors.rs:1097-1164`):

```math
Q_{i,d} = \min(0.02,\ 0.5\,\mathrm{SIR}\,r) \cdot \mathbf{1}[\mathrm{SIR} > 0.2,\ r > 0.03]
 - C(\lvert r \rvert) \cdot \mathbf{1}[r < -0.025]
 + C(r) \cdot \mathbf{1}[r > 0.025,\ \mathrm{SIR} > 0.1]
```

The cascade $C(x)$ is 0.007 above a 7% move, 0.0045 above 5%, 0.0025 above
3%, else 0.0005. The whole term is scaled by `cascade_gain`, 0.1 on pt-v20
and 1 on pt-v19 (`market/factors.rs:1166-1174`): with the tape tracking the
model price, the ladders were the largest daily momentum left, and 0.1
brings the one-day Lo-MacKinlay contrarian profit to the reference roster's
-1.74 basis points a day (measured; estimate 0.12, s.e. 0.11; row C8 reads
-0.66 against a band of -6.4 to +2.9). `cascade_symmetry` = 1 makes the up
and down ladders mirror images, and it and the ladder constants are chosen.

### News

**Timescale:** events are drawn at the open and absorbed tick by tick.

At each open every company draws a uniform and a normal on the news stream.
With probability $\lambda_N$ it has a news event of log size
$\nu_e = \sigma_N Z$ (`engine.rs:3522-3539`). An event reaches company $i$
with weight

```math
w_{e,i} = \begin{cases}
1 & \text{the event is about } i \\
w_p\,(1 + c_p\,\chi_d) & \text{the event is about another company in the same sector}
\end{cases}
```

(`market/factors.rs:734-791`), where $\chi_d$ is the crisis spike (below).
An event is priced mostly within minutes. The share absorbed by minute $m$
of the session is

```math
\mathcal{A}(m) = (1 - \delta)\,F(m;\ h_f) + \delta\,F(m;\ h_D),
\qquad
F(m;\ h) = \frac{1 - 2^{-m/h}}{1 - 2^{-390/h}}
```

and the news term in the mispricing equation is

```math
N_{i,t} = 390 \sum_{e} w_{e,i}\,\nu_e\,\big(\mathcal{A}(t + 1) - \mathcal{A}(t)\big)
```

(`engine.rs:2044-2054`, `market/factors.rs:512-565`). Over the session an
event moves $s$ by $w \nu_e$: 61% of that in the first minute, 89% by the
fifth and 99% by the 150th. The market
maker re-quotes by the tick's news term before any trade, so the traded price
carries the same profile (`news_quote_revision` = 1, `market/tick.rs:1446-1464`).

| Symbol | Dial | Value | Kind | Source |
|---|---|---|---|---|
| $\lambda_N$ | `endogenous_news_intensity` | 0.05 a day | fitted | search |
| $\sigma_N$ | `endogenous_news_sigma` | 0.01751 | fitted | search |
| $w_p$ | `news_peer_weight`, `news_peer_weight_down` | 0.05, 0.05 | chosen | motivated by Foster (1981) and Freeman and Tse (1992) on intra-industry information transfer |
| $c_p$ | `news_peer_vix_coupling` | 8.0 | fitted | |
| $h_f$ | `news_absorption_half_life` | 0.6 ticks | derived | one-minute share of the earnings-announcement move, Christensen, Timmermann and Veliyev (arXiv 2601.08962, Table 7, 2008 to 2020) |
| $\delta$ | `news_absorption_drift_share` | 0.12 | derived | same table: 1 - 1.58/1.80 |
| $h_D$ | `news_absorption_drift_half_life` | 42 ticks | chosen | a 60-minute mean life for the drift, to fit Patell and Wolfson (1984)'s "several hours"; not measured |
| | `news_quote_revision` | 1.0 | derived | with it the tape holds 0.615 of an event after one tick against the profile's 0.605; without it 0.086 |

### Jumps

**Timescale:** once a day, at the close, on the jumps stream
(`engine.rs:4112-4178`). A market jump hits every company with unit loading;
an idiosyncratic jump hits one company. Both rates rise with the VIX, and the
expected market jump is subtracted so jumps add no drift:

```math
\lambda_{\cdot,d} = \lambda_{\cdot}\,\big((1 - c_J) + c_J\,\rho_d^{2}\big)
```

```math
s_i \leftarrow \mathrm{clip}\Big(s_i
 + \mathbf{1}[U^{M}_d < \lambda_{M,d}]\,(\mu_M + \sigma^{J}_M Z^{M}_d)
 + \mathbf{1}[U_{i,d} < \lambda_{I,d}]\,\sigma^{J}_I Z_{i,d}
 - \lambda_{M,d}\,\mu_M;\ -\bar s,\ \bar s\Big)
```

A jump changes $s$ at the close and so reaches the price at the next
session's first ticks, through the book. At $X = A$ there are about 7
market-jump days a year, of mean -0.85%, and each company has about 1.7
idiosyncratic jumps a year, of standard deviation 7.5%.

| Symbol | Dial | Value | Kind | Source |
|---|---|---|---|---|
| $\lambda_M$ | `jump_intensity_market` | 0.02829 (0.05658) a day | fitted | half pt-v19's rate, picked on the same grid as the market factor's sigma; pt-v19's was a search for the two-year excess kurtosis |
| $\mu_M$ | `jump_mean_market` | -0.008522 | fitted | search; negative for skew |
| $\sigma_M^{J}$ | `jump_sigma_market` | 0.002460 | fitted | search |
| $\lambda_I$ | `jump_intensity_idio` | 0.006890 a day | fitted | search |
| $\sigma_I^{J}$ | `jump_sigma_idio` | 0.07521 | fitted | search |
| $c_J$ | `jump_vix_coupling` | 0.2626 | fitted | |
| | `jump_mean_compensated` | 1.0 | derived | a compensated Poisson process |

### The attribution

Every change in the model price at a fixed published valuation is booked to
one of ten factors, which `engine.truth()` reports
(`market/factors.rs:61-63`, `market/tick.rs:1144-1161`). An eleventh column,
the fair-value shift, books what left $s$ for the fair-value level. It is
new in 0.8.5 (`market/factors.rs`, `python_arrow.rs`), and each tick books
as follows:

| Slot | Factor | Amount |
|---|---|---|
| 0 | reversion | $(\phi_\tau - 1) s_{i,t}$ |
| 1 | momentum | $\theta \mu_{i,d}/390$ |
| 2 | crowd lean | $L(s_{i,t}, \mu_{i,d})/390$ |
| 3 | company news | $N_{i,t}/390$, the whole shock |
| 4 | order flow impact | $O_{i,t}/390$, including agents' permanent impact |
| 5 | short squeeze | $Q_{i,d}/390$ |
| 6 | random noise | $u(\tau_t) \varepsilon_{i,t}$, the whole shock |
| 7 | circuit breaker | the change in $s$ when the breaker binds |
| 8 | jump | the jump, at the close, the whole shock |
| 9 | overnight | the overnight change (zero on pt-v20) |
| 10 | fair value shift | $-\Delta_{i,t}^{v}$, and $-\Delta_{i}^{v,J}$ at the close: the part that left $s$ for $v$ |

Slots 3, 6 and 8 keep the whole shock, because that is what moved the
price; slot 10 takes the permanent part back out of $s$. So, over any run
of rows, to rounding, except a tick where the $\pm\bar s$ cap binds, which
no slot records:

```math
\sum_{k=0}^{10} c_k = \Delta s,
\qquad
\sum_{k=0}^{9} c_k = \Delta s + \sum \Delta^{v} = \Delta s + \Delta v + \tfrac12 \sum \big(\Delta^{v}\big)^{2}
```

The first ten are the model price's log move at a fixed published
valuation, plus the Ito term. On pt-v19 slot 10 is always zero and the ten
sum to $\Delta s$. The close's share is written on the next day's first
row, beside the jump. `explain` adds two more terms, the change in
$\ln V$ (which contains $\Delta v$) and the book's $\ln(P/P^{\ast})$, so
its thirteen terms sum to $\Delta \ln P$.

## Volatility

Three variance processes set the size of the tick shocks. All three update
once a day, at the close, and none takes a random draw of its own. All three
read the VIX ratio $\rho_d = X_d / A$.

### Market-factor variance

**Timescale:** daily, at the close. **State:** a fast component $v^{f}$, a
slow component $v^{s}$, and their mixture $v$, which the next session draws
the market factor with. $F_d = \sum_t f_t$ is the day's summed market factor.
All three start at $b_m = \bar\sigma_M^{2}$.

The VIX sets a target for each component, with a steeper response above the
anchor than below it (`market/factor_vol.rs:374-391`,
`market/factor_vol.rs:721-730`, `market/factor_vol.rs:799-805`):

```math
\mathcal{R}(\rho) = \begin{cases} \rho^{e_-} & \rho < 1 \\ \rho^{e_+} & \rho \ge 1 \end{cases}
\qquad
\bar v^{f}_d = b_m\big(1 - c_m + c_m\,\mathcal{R}(\rho_d)\big),
\qquad
\bar v^{s}_d = b_m\big(1 - c'_m + c'_m\,\mathcal{R}(\rho_d)\big),\quad c'_m = c_m\,(1 - \delta_s)
```

The fast component is a GJR-GARCH(1,1) pulled toward its target; the slow
component is a plain one with a small gain on the day's shock
(`market/factor_vol.rs:325-365`, `market/factor_vol.rs:777-817`):

```math
v^{f}_{d+1} = \mathrm{clip}\Big(\big(1 - \alpha_f - \beta_f - \tfrac{\gamma_f}{2}\big)\,\bar v^{f}_d
 + \big(\alpha_f + \gamma_f\,\mathbf{1}[F_d < 0]\big)\,F_d^{2} + \beta_f\,v^{f}_d\Big)
```

```math
v^{s}_{d+1} = \mathrm{clip}\Big((1 - p_s)\,\bar v^{s}_d + g_s\,p_s\,F_d^{2} + (1 - g_s)\,p_s\,v^{s}_d\Big)
```

```math
v_{d+1} = \mathrm{clip}\big((1 - w_s)\,v^{f}_{d+1} + w_s\,v^{s}_{d+1}\big),
\qquad \mathrm{clip}(x) = \min\big(\max(x,\ 0.05\,b_m),\ 32\,b_m\big)
```

The fast component's persistence is $\alpha_f + \beta_f + \gamma_f/2 = 0.979$,
a half-life of 33 sessions; the slow one's is 0.9913, 79 sessions. The
ceiling caps the market factor at 4.3% a day.

| Symbol | Dial | Value | Kind | Source |
|---|---|---|---|---|
| $\alpha_f$ | `market_vol_alpha` | 0.0066 | measured | GJR-GARCH(1,1) QML on S&P 500 daily log returns, 1990 to 2025; s.e. 0.0082 |
| $\beta_f$ | `market_vol_beta` | 0.8946 | measured | same fit; s.e. 0.0181 |
| $\gamma_f$ | `market_vol_gamma` | 0.1556 | measured | same fit; s.e. 0.0236 |
| $p_s$ | `market_vol_slow_persistence` | 0.9913 | derived | the slow pole of the S&P 500 variance impulse response; bootstrap interval 0.975 to 1.000 |
| $g_s$ | `market_vol_slow_gain` | 0.05 | chosen | a round number |
| $w_s$ | `market_vol_slow_weight` | 0.35 | fitted | |
| $\delta_s$ | `market_vol_slow_vix_damp` | 0.374 | fitted | |
| $c_m$ | `market_vol_vix_coupling` | 0.9540 | fitted | search |
| $e_+$ | `market_vol_vix_exponent` | 4.0 | fitted | fitted to the held-VIX crisis lever: 10.3 times against 10.5 on the data |
| $e_-$ | `market_vol_vix_exponent_below` | 2.5 | chosen | inside the measured range: shared variance on the reference roster rises as the VIX to the power 2.25 (1.95 to 2.57) |
| | floor, ceiling multiples | 0.05, 32 | guard | a record VIX of 82.7 against 15 is about 30 times the variance |

### Company variance (GJR-GARCH)

**Timescale:** daily, at the close, before the jumps. **State:** $h_{i,d}$,
starting at the sector's $\sigma_k^{2}$.

The reference level follows the VIX (`market/daily.rs:205-212`), and the
recursion is a GJR-GARCH(1,1) clamped to a band around it
(`market/garch.rs:469-505`):

```math
\bar h_{i,d} = \sigma_{k(i)}^{2}\,\big(1 - c_g + c_g\,\rho_d^{2}\big)
```

```math
h_{i,d+1} = \mathrm{clip}\Big(\omega + \big(\alpha + \gamma\,\mathbf{1}[e_{i,d} < 0]\big)\,e_{i,d}^{2} + \beta\,h_{i,d};\
 f_g\,\bar h_{i,d},\ C_g\,\bar h_{i,d}\Big),
\qquad
e_{i,d} = \sum_{t} u(\tau_t)\,\varepsilon_{i,t}
```

The innovation $e_{i,d}$ is the day's whole noise term for the company:
market, sector and own parts together (`engine.rs:3214-3217`). The
persistence $\alpha + \beta + \gamma/2 = 0.9416$ is a half-life of 11.5
sessions. The recursion's own unconditional level,
$\omega / (1 - 0.9416) = 3.4 \times 10^{-5}$, sits below the floor
$0.25 \sigma_k^{2}$ in 8 of the 12 sectors, so in those sectors the floor,
not $\omega$, holds the resting level.

| Symbol | Dial | Value | Kind | Source |
|---|---|---|---|---|
| $\omega$ | `garch_omega` | 0.000002 | chosen | reference implementation |
| $\alpha$ | `garch_alpha` | 0.05951 | fitted | search |
| $\beta$ | `garch_beta` | 0.7905 | derived | $0.9416 - \alpha - \gamma/2$, where 0.9416 is the decay of the absolute-return autocorrelation on the reference roster |
| $\gamma$ | `garch_gamma` | 0.1832 | chosen | the value the same identity gives, 0.65, is not stationary |
| $c_g$ | `garch_vix_coupling` | 0.1422 | fitted | search |
| | `garch_vix_exponent` | 2.0 | chosen | |
| $f_g$, $C_g$ | `garch_floor_multiple`, `garch_ceiling_multiple` | 0.25, 5.0 | guard | the floor binds; see above |

### Sector variance

**Timescale:** daily, at the close. **State:** $h_{k,d}^{S}$, a variance ratio
whose fixed point is 1 (`engine.rs:1262-1290`). $D_{k,d} = \sum_t G_{k,t}$ is
the day's summed sector factor.

```math
h^{S}_{k,d+1} = \mathrm{clip}\Big((1 - a_s - b_s) + a_s\,\frac{D_{k,d}^{2}}{(\bar\sigma_S\,\rho_d)^{2}} + b_s\,h^{S}_{k,d};\ 0.25,\ 5\Big)
```

Its persistence is 0.904, a half-life of 7 sessions.

| Symbol | Dial | Value | Kind | Source |
|---|---|---|---|---|
| $a_s$ | `sector_vol_alpha` | 0.067 | measured | GARCH(1,1) QML on seven sectors' residuals after the market, reference roster 2015 to 2025, scaled by the VIX; s.e. 0.043 |
| $b_s$ | `sector_vol_beta` | 0.837 | measured | same fit; s.e. 0.111 |

## The VIX

The VIX is computed, not simulated on its own. Each day it moves toward the
volatility the index's own conditional variance implies, plus a fear
response to the day's return.

### The index variance and the anchor

**Timescale:** once a day, in the macro step. The index is cap-weighted,
with weights $w_i$ over listed, solvent companies. Its one-day-ahead
variance adds up the tick process's parts (`market/index_var.rs:843-1014`):

```math
V_d = K_u\Big[\,v_{d+1}\,E[z^{2}\Gamma^{2}]\,\bar\beta^{2}\,\Lambda_d\,\tfrac{1 + (1 + a)^{2}}{2}
 + \sum_k\Big(\sum_{i \in k} w_i \ell_i\Big)^{2}\bar\sigma_S^{2}\rho_d^{2}\,h^{S}_{k,d+1}
 + \sum_i w_i^{2}\,\iota_i^{2}\Big] + J^{V}_d
```

```math
J^{V}_d = \lambda_{M,d}\,\big(\mu_M^{2} + (\sigma^{J}_M)^{2}\big) - (\lambda_{M,d}\,\mu_M)^{2}
 + \lambda_{I,d}\,(\sigma^{J}_I)^{2}\sum_i w_i^{2}
 + \lambda_N\,\sigma_N^{2}\sum_i w_i^{2}
```

Here $\bar\beta = \sum_i w_i\beta_i$; $K_u = 1.0844$ is the session mean of
$u(\tau)^{2}$; $E[z^{2}\Gamma^{2}] = 1.0541$ is the crash amplifier's second
moment; $\iota_i$ is the company's own-noise scale from the factor
structure; $\Lambda_d = (1 + a_L)^{2}$ if today's summed market factor
was negative, else 1; and $J_d^{V}$ is the variance the jumps and news add.

The VIX it implies, and the anchor $A$ (`market/index_var.rs:1189-1191`,
`engine.rs:1176-1199`):

```math
I_d = (1 + \varpi)\cdot 100\,\sqrt{252\,V_d},
\qquad
A = (1 + \varpi)\cdot 100\,\sqrt{252\,\bar V}
```

$\varpi$ is the variance risk premium, the gap between the VIX and realised
volatility. $\bar V$ is the same sum at the unconditional point: the factor
at its baseline, sector states at 1, each company's variance at its resting
level, and $\Lambda$ averaged over up and down days. $A$ is computed once, when
the engine is built, and depends on the roster: on pt-v20 20.9 on
`Universe.random(40, seed=111)` and 17.9 on `Universe.random(20, seed=101)`
(pt-v19: 24.0 and 20.6). It is lower than pt-v19's because the market
factor's sigma and the market jump rate are lower.

### The VIX step

**Timescale:** once a day, in the macro step. **Draws:** one normal and one
or two uniforms on the economy stream, and one normal on the VIX level
stream at the close. **State:** $X_d$, a slow log level $q_d$, and the
anchor's slow memory $M_d$.

A slow level wanders around the anchor (`engine.rs:3850-3863`,
`engine.rs:4844-4867`), and a slow memory tracks the read-back
(`engine.rs:4418-4434`):

```math
q_d = \phi_q\,q_{d-1} + \frac{\sigma_\ell}{G_\ell}\,Z,
\qquad
\Xi_d = \exp\Big(q_d - \frac{1}{2}\,\frac{(\sigma_\ell / G_\ell)^{2}}{1 - \phi_q^{2}}\Big),
\qquad
M_d = (1 - h_M)\,M_{d-1} + h_M \ln\frac{I_d}{A\,e^{-c_A}}
```

The target (`economy/daily.rs:1263-1358`):

```math
T_d = \Xi_d\,I_d\,e^{-a(X_d)\,M_d}
 + \min\Big(C,\ \Phi(r_d, X_d) + 0.2\,(\pi - 3)^{+}\Big) - \bar\Phi_d
```

where $r_d$ is the index's open-to-close return in percent, clipped to
$\pm 15$, $\pi$ is inflation, and the fear response is

```math
\Phi(r, X) = \begin{cases}
G_{\downarrow}\,\lvert r \rvert^{e_\downarrow}\,X^{-(e_\downarrow - 1)} & r < 0 \\
-G_{\uparrow}\,r^{e_\uparrow}\,X & r > 0
\end{cases}
```

A fall raises the VIX, by less when the VIX is already high; a rise lowers
it. $\bar\Phi_d$ is the mean of $\Phi$ under a normal return with the index's
own variance, subtracted so the fear response adds no drift
(`economy/daily.rs:599-623`). The weight on the slow memory falls as the VIX
rises: $a(X) = 1 - (1 - a_0) X_d^{\ast} / \mathrm{clip}(X;\ X_d^{\ast},\ 2.216 X_d^{\ast})$ with
$X_d^{\ast} = A \Xi_d e^{-0.3888}$, so $a$ runs from 0.375 to 0.718
(`economy/daily.rs:550-558`).

The step (`economy/daily.rs:1400-1472`):

```math
X_{d+1} = \mathrm{clip}\Big(X_d + \kappa_X\,(T_d - X_d) + \sigma^{X}_d\,Z + J_d;\ 10,\ X_{\max}\Big),
\qquad
\sigma^{X}_d = c_r\,X_d\,\lvert r_d \rvert
```

```math
J_d = J_s\,\sigma^{X}_d\,E\cdot\mathbf{1}\Big[U < \frac{\lambda_J \max(0,\ -r_d)}{252}\Big],\qquad E \sim \mathrm{Exp}(1)
```

The VIX reverts with a half-life of about 2.2 sessions. On a day the index
does not move, it takes no noise and no jump.

| Symbol | Dial | Value | Kind | Source |
|---|---|---|---|---|
| $\varpi$ | `vix_variance_premium` | 0.252 | measured | median yearly VIX over realised volatility, S&P 500 and VIX 1990 to 2025; interquartile range ±0.13 |
| | `vix_level_identity` | 1.0 | measured | switches the target to the index's implied VIX; chosen on a 120-seed paired comparison |
| $\kappa_X$ | `vix_mean_reversion` | 0.27 a day | chosen | needs a joint solve with the fear gain |
| | `vix_decay_ratio` | 1.0 | measured | the same reversion up and down; 120-seed comparison |
| $G_\downarrow$ | `vix_return_gain` | 8.83 | fitted | matches the data's same-day response at $\kappa_X$ = 0.27 |
| $e_\downarrow$ | `vix_return_exponent` | 1.448 | measured | regression of the VIX change on the index move and the VIX level, 1990 to 2025; s.e. 0.12 |
| $G_\uparrow$ | `vix_return_gain_up` | 0.049 | chosen | |
| $e_\uparrow$ | `vix_return_exponent_up` | 0.5433 | measured | up-day fit; s.e. 0.04 |
| $C$ | `vix_target_shock_cap` | 158.9 | derived | the largest value $\Phi$ can take; never binds |
| $X_{\max}$ | `vix_ceiling` | 181.3 | guard | a lower bound carried from an earlier solve |
| $c_r$ | `vix_innovation_return_sigma` | 0.0175 | measured | residual fit on the VIX, 1990 to 2025; s.e. 0.0015 |
| $J_s$ | `vix_jump_level_scale` | 1.7 | measured | cumulant fit of the residual; s.e. 0.35 |
| $\lambda_J$ | `vix_jump_return_intensity` | 6.199 | derived | 2.24 fear jumps a year, spread over down days |
| $a_0$ | `vix_anchor_weight` | 0.375 | derived | from the data's level elasticity, 0.655 |
| $h_M$ | `vix_anchor_memory` | 0.0556 | fitted | fitted to the VIX persistence statistic |
| $c_A$ | `vix_anchor_centre` | 0.1515 | derived | $\ln(1.252/1.076)$ |
| $\phi_q$, $\sigma_\ell$, $G_\ell$ | `vix_level_persistence`, `vix_level_sigma`, `vix_level_loop_gain` | 0.9979, 0.0181, 1.79 | derived | yearly medians of the log VIX, 1990 to 2024 |
| | `vix_return_clamp` | 15 | guard | |

## Crisis regimes

A crisis is a state of the VIX. There is no separate regime switch: every
variance process scales with the VIX, so a high VIX is a violent market.
Three things change above a threshold $X_c$.

**The crisis spike** (`market/tick.rs:324-342`):

```math
\chi_d = \min\big(0.98,\ (X_d - X_c)^{+} / 1.4\big)
```

It saturates at a VIX of about 32.3. On pt-v20 it acts only on news: a
sector peer's news spreads with weight $w_p (1 + 8\chi_d)$. The crisis blend
that once raised every company's market loading in a crisis is switched off
(`crisis_blend_gain` = 0): the data showed no crisis correlation beyond
what the higher common volatility already gives.

**The crisis episode and its epicentre** (`engine.rs:3258-3332`). An episode
starts at the open of the first session with $X_d > X_c$. One uniform on the
epicentre stream picks the sector it starts in: financial services with
probability 0.6, no epicentre with 0.4. The episode ends after 21
consecutive sessions at or below $X_c$. While it runs, and if a company in
the epicentre sector is listed, the sector and own-noise legs of each
company's shock are scaled by $\xi_i$ (`market/factors.rs:333-351`):

```math
\xi_\uparrow^{2} = \frac{m\,(e^{2} - 1)}{1 - m} + e^{2}\,\xi_\downarrow^{2},
\qquad
\xi_\downarrow^{2} = \frac{(1 - m) - m\,w\,(e^{2} - 1)}{(1 - m)\big(1 + w\,(e^{2} - 1)\big)}
```

with $e$ = 1.93, $m$ = 0.3916 and $w$ = 0.1019. This gives
$\xi_\uparrow$ = 2.04 in the epicentre and $\xi_\downarrow$ = 0.80 elsewhere: the
epicentre's non-market volatility is $e$ times the rest's, and the roster's
mean non-market variance is unchanged. A scenario can pick the epicentre.

**Macro gates.** Above a VIX of 25.5 the dollar takes a safe-haven bid
(above).

| Symbol | Dial | Value | Kind | Source |
|---|---|---|---|---|
| $X_c$ | `crisis_vix_threshold` | 30.88 | fitted | search |
| $e$ | `crisis_epicentre_extra` | 1.93 | derived | median of the epicentre's volatility ratio in three crises on the reference roster: 2.43 (2008-09), 1.93 (2011), 1.41 (2020) |
| | epicentre weights | 0.6, 0.4 | derived | see [The sectors](#the-sectors) |
| | `crisis_epicentre_end_sessions` | 21 | chosen | a month of sessions |
| | `crisis_blend_gain` | 0 | derived | the data show no crisis correlation beyond common volatility |
| | `crisis_blend_cap`, `crisis_blend_ramp` | 0.98, 1.4 | chosen | now shape only the news spike |
| $c_p$ | `news_peer_vix_coupling` | 8.0 | fitted | |

## The market maker and the book

**Timescale:** every tick. The book is rebuilt from scratch each tick; two
things carry over between ticks, the last print $P_{i,t-1}$ and the maker's
inventory $I_i$ in shares (`microstructure.rs:325-380`).

### The quote

The maker quotes around a blend of the last print and the tick's model
price (`market/tick.rs:1446-1464`):

```math
\hat P_{i,t} = P_{i,t-1}^{\,1 - w_Q}\,\big(P_{i,t}^{\ast}\big)^{w_Q}
```

On pt-v20 $w_Q = 1$, so the maker quotes around the model price itself and
the tape tracks it; the print differs from $P^{\ast}$ by the bid-ask bounce
and the inventory skew. At $w_Q = 0$ (pt-v19) the maker quoted around the
last print moved by the tick's news term.

The full spread, in basis points, depends on market-cap tier, volatility,
the VIX and short interest (`microstructure.rs:197-258`):

```math
\sigma^{\mathrm{bps}}_{i} = b(M_i)\,\big(0.7 + 0.3\,v_k\,\beta_i\big)\Big(1 + \max\Big(0,\ \frac{X_d - 15}{30}\Big)\Big)\,m^{SI}_i
```

$b(M)$ is 3, 5, 10 or 30 basis points for a market cap above USD 50bn, above
10bn, above 1bn, or below. $m^{SI} = 1 + 2 (\mathrm{SIR} - 0.15)$ above 15%
short interest over float, else 1. The maker skews the quote against its
inventory (`market_maker.rs:120-168`):

```math
\eta_i = \hat P_{i,t}\,\frac{\sigma^{\mathrm{bps}}_{i}}{2 \cdot 10^{4}},
\qquad
q_i = \max\big(1,\ \lfloor \bar A_i / 100 \rfloor\big),
\qquad
\lambda_i = \mathrm{clip}\Big(\frac{I_i}{12\,q_i};\ -1,\ 1\Big),
\qquad
m_i = \hat P_{i,t} - \lambda_i\,\eta_i
```

The best bid and ask are $m - \eta$ and $m + \eta$, rounded to the cent and
at least a cent apart. Level $j = 0, \dots, 9$ sits $j$ half-spreads further
out and holds $\max(1, \lfloor Q_0 / (1 + 0.3j) \rfloor)$ shares
(`market_maker.rs:199-238`). The top size $Q_0$ is $q_i$, cut to
$q_i \max(0.15, 1 - \lvert \lambda_i \rvert)$ on the side the maker is already
long or short. With no inventory, the book shows about $5 q_i$ shares a
side, about 5% of a day's volume.

All the spread and ladder constants are chosen, from the reference
implementation. The ten levels are the depth the interface has always drawn.

### Settlement

**Timescale:** every tick, for every listed company. **Draws:** four
uniforms $U_1, \dots, U_4$, whether or not they are used.

Background order flow trades against the book. Its buy probability rises
with the gap between the model price and the quote, which is zero on pt-v20,
and with the crowd's lean (`microstructure.rs:720-727`):

```math
\pi^{\mathrm{buy}}_{i,t} = \mathrm{clip}\Big(0.5 + 40\,\frac{P^{\ast}_{i,t} - \hat P_{i,t}}{\hat P_{i,t}} + 10\,L(s_{i,t}, \mu_{i,d});\ 0.05,\ 0.95\Big)
```

Four market orders of $\max(1, \lfloor V_{i,t}^{\mathrm{vol}}/4 \rfloor)$
shares each, where $V^{\mathrm{vol}}$ is the tick's volume below, walk the
book: order $j$ buys if $U_j < \pi^{\mathrm{buy}}$ and sells otherwise. The
print is the price of the last fill; if nothing fills, it is $P^{\ast}$
(`microstructure.rs:729-792`). When every slice fits in the top level, the
print is the ask with probability $\pi^{\mathrm{buy}}$ and the bid otherwise.
The maker's inventory takes the other side of every fill and never decays
(`microstructure.rs:495-512`).

The print is then clamped to the same ±25% session band as the model price:
$P_{i,t} = \mathrm{clip}(P_{i,t}^{\mathrm{set}};\ 0.75 P_{i,d}^{o},\ 1.25 P_{i,d}^{o})$
(`market/tick.rs:1404-1412`). The overnight gap is not clamped, and there is
no overnight move: each session opens at the last print.

**The closing cross.** On the last tick of the session the print is the
model price, clamped to the same band, and the maker's inventory change
from that tick is dropped (`market/tick.rs:1501-1513`, `market/tick.rs:1570`):

```math
P_{i,389} = \mathrm{clip}\big(P_{i,389}^{\ast};\ 0.75 P_{i,d}^{o},\ 1.25 P_{i,d}^{o}\big)
```

So the close sits at the model price, between the bid and the ask, and a
close-to-close strategy earns no bounce.

| Constant | Value | Kind |
|---|---|---|
| gap pressure | 40 | chosen |
| lean tilt | 10 | chosen |
| slices a tick | 4 | chosen |
| buy probability bounds | 0.05, 0.95 | guard |
| `price_breaker_fraction` | 0.25 | guard |
| `price_hard_cap` | 50,000 | guard |
| `quote_model_weight` $w_Q$ | 1 (0) | derived: a switch; Roll's (1984) spread estimate from the tape was 5 times the quoted spread with $w_Q = 0$ and 1.2 times with it (row C4a), and the lag-1 autocorrelation of 65-minute returns moves from -0.19 to -0.02 |
| `closing_auction` | 1 (0) | derived: a switch; the one-day Lo-MacKinlay profit on large names moves from +13 to -1 basis points a day, against a real -1.7 |

## Agent orders

**Timescale:** an agent acts between steps; its orders fill at once
against its own book, and their effect on prices lands on the next tick.

### The agent's book

An agent trades against a book of its own, built for each order
(`agent_book.rs`). It holds three kinds of liquidity: the maker's ladder, as
above, quoted around the last print (`agent_book.rs:546-558`); latent depth;
and other agents' resting orders, at their own limits, behind the maker at
an equal price. An agent never meets its own orders
(`agent_book.rs:589-597`).

Latent depth makes size pay the square-root law. Beside the ladder, each
side holds a pool whose cumulative size $Q$ is priced at no better than
(`agent_book.rs:425-489`)

```math
p^{\mathrm{ask}}(Q) = p^{\mathrm{touch}}\Big(1 + Y\,\sigma_i\Big(\frac{Q}{\bar A_i}\Big)^{\delta}\Big),
\qquad
\sigma_i = \sqrt{h_{i,d} + \beta_i^{2}\,v_d}
```

with bids mirrored, levels on a geometric grid $Q_k = R \bar A_i 1.25^{-k}$,
rounded to the cent. The average cost of $Q$ shares is
$Y \sigma_i (Q/\bar A_i)^{\delta} / (1 + \delta)$, two thirds of the
marginal price at the square root. An order larger than $R \bar A_i$ is
cut off.

A walk takes the cheapest liquidity first. What it takes is gone for later
orders: consumed depth refills with a half-life of $H_B$ ticks
(`agent_book.rs:501-523`, `agent_book.rs:619-626`),

```math
D_{t+1} = D_t\,2^{-1/H_B}
```

and a fill against the maker moves the maker's inventory at the next
re-quote (`engine.rs:2368-2386`), and at the open everything resets.

A limit order's remainder rests (`engine.rs:2826-2843`). Each tick it is
also posted into the settlement book, where the background flow can fill it
at its limit, as a maker fill (`microstructure.rs:674-716`,
`microstructure.rs:740-755`).

### The path of a fill to the price

All agents' taker fills since the last tick are applied once, on the next
open tick (`engine.rs:2114-2138`). Each agent's net fill leaves a linear
permanent impact on $s$, Almgren's law (`engine.rs:2390-2395`):

```math
\Delta s_i = \sum_{a} \gamma\,\sigma_i\,\frac{b_{a,i} - x_{a,i}}{\bar A_i}
```

with $b$ shares bought and $x$ shares sold by agent $a$. It is booked to the
order-flow slot. A resting order's maker fill carries none. Buying 10% of a
day's volume at a daily sigma of 1.56% moves $s$ by about 4.9 basis
points. The impact lives in $s$, so it decays on the mispricing's
half-life rather than lasting for good. The model's own flow never meets
consumed or latent depth, so an agent's temporary impact reaches the tape
only through the maker's inventory.

With $\gamma \ne 0$ agents' flow no longer enters the order-flow law $O$,
which now carries only flow a caller supplies directly:

```math
O_{i,t} = \frac{x^{+} - x^{-}}{x^{+} + x^{-}}\,\max\Big(0.2,\ 0.15\min\Big(\frac{x^{+} + x^{-}}{\max(\bar A_i/390,\ 100)},\ 10\Big)\Big)\cdot\frac{c_{OF}\,f_I}{\max\big(\max(\bar A_i,\ 0.005\,S_i)/390,\ 100\big)}
```

(`market/factors.rs:797-803`, `market/factors.rs:1219-1232`).

| Symbol | Dial | Value (pt-v19) | Kind | Source |
|---|---|---|---|---|
| $Y$ | `book_depth_coefficient` | 0.75 (0, off) | measured | the cost of size fitted as 0.469 $\sigma (Q/V)^{0.495}$ (tools/calibration/impact_curve.py) inside the 0.33 to 0.67 band of Tóth et al. (2011); row C9 reads exponent 0.487 and coefficient 0.468 |
| $\delta$ | `book_depth_exponent` | 0.5 | derived | the square-root law (Tóth et al. 2011) |
| $R$ | `book_depth_reach` | 1.0 | derived | the latent book reaches one day's volume |
| | `book_shared` | 1 (0) | derived | a switch: agents consume one book |
| $H_B$ | `book_refill_half_life` | 27 ticks (0) | measured | refill after a 10% of volume order: 23.1 bp at once, 9.7 after 30 ticks, 1.6 after 130; $39 \ln 2$ at a minute's share of volume (Obizhaeva and Wang 2013) |
| | `book_resting` | 1 (0) | derived | a switch: limit orders rest with queue priority |
| $\gamma$ | `fill_impact_coefficient` | 0.314 (0) | derived | the permanent coefficient of Almgren, Thum, Hauptmann and Li (2005); linear, so no round trip profits (Huberman and Stanzl 2004) |
| $c_{OF}$ | `order_flow_coefficient` | 50 | chosen | reference implementation |
| $f_I$ | `informed_flow_fraction` | 0.35 | chosen | the permanent share of impact; published decompositions of 0.3 to 0.5, none named |

## The Oracle baseline

`baselines.Oracle` is the reference that reads state no trader can see. Its
rule is picked from the preset's own dials, never from the preset's name.

- **Cross-sectional rule** (every preset through pt-v19: `fair_value_news_share`, `fair_value_market_share` and `earnings_cycle_depth` all 0.0). Every stock-specific move is mispricing that reverts, so the spread of $s$ across names is the edge. At every step it goes long the `top_k` lowest $s$ and short the `top_k` highest, equal weight, dollar-neutral.
- **Expected-return rule** (any of those dials off zero, as on pt-v20). The cross-section of $s$ is small there: its spread falls from 0.30 to 0.015 and its rank IC against the next day's return from -0.44 to -0.03, so the old rule loses money on 3 of 8 seeds. Once a day, at the open, it forms each equity's expected log return over the session.

The expected return under the second rule has two parts: the mispricing's
reversion and herding, $(\phi_\tau^{390} - 1)s_i + \theta\mu_i$, which carry
the market-wide transient mispricing and any residual, plus the fair value's
drift, which is nominal output growth, the buyback yield and the earnings
cycle's pull toward its phase's level. The residual across names is traded
as the cross-sectional rule trades $s$. The common part is a net position
spread over every equity, long or short by its sign. The gross splits
between the two in proportion to what each earns per unit of gross.

Before pt-v20's graded arm, over 30 days on rosters `Universe.random(20,
seed=3, 42, 11)` at sim seeds 0-3, the Oracle was positive on 12 of 12
markets and ahead of every price-only reference agent on 11 of 12. Its edge
then was the market's opening mispricing (`opening_market_sigma` 0.10),
which reverts.

On the graded arm it trades close to no edge. The market's plain shocks move fair
value for good (`fair_value_market_share` 1.0, `fair_value_market_linear` 1)
and the opening mispricing is 0.001, so little that hidden state knows
predicts a return. The index's next-day return correlates with the rule's
predicted common return at 0.11, and the cross-sectional rank IC is 0.014.
The rule is net long most days and its P&L takes the sign of the market's
month:
- positive on 10 of 14 markets on those rosters (sim seeds 0-3, 0-3 and
  0-5), and on 30 of 48 over sim seeds 0-15;
- every loss is a month buy-and-hold lost more; on sim seed 3 the index
  fell about 11 per cent in log terms, 10 points of it in the names'
  permanent fair-value offsets, with the VIX below the discount's knee and
  the earnings cycle unmoved;
- adding the terms the rule leaves out (the crowd's lean on $s$, the
  anticipated earnings' drift, the volatility discount's approach to its
  target) moves the count to 26-30 of 48, with the mean P&L still near zero;
- at `opening_market_sigma` 0.10 the same seed 3 pays it +152,102 against
  buy-and-hold's -175,280.

So the Oracle is measured as a ceiling on pt-v19 (`tests/test_baselines.py`,
`CEILING_PRESET`). On pt-v20 it stays in the reference set as a reference
agent, not a ceiling, and the library reports no capture ratio there. A
fraction of the Oracle's P&L would measure the market's month, not the
agent.

`baselines.ORACLE_NOT_A_CEILING` names the presets this applies to, each
with the reason a result gives. It holds pt-v20 alone. The check reads a
scorecard's `model_fingerprint`, so a custom model (`custom-XXXXXXXX`) keeps
the ratio whatever preset it was built from. Where a preset is named:
- `capture_ratio` returns an empty mapping, whatever the Oracle earned, and
  `capture_withheld` returns the reason;
- `versus_buy_and_hold` gives each agent's P&L less buy-and-hold's in the
  same market, the comparison to quote;
- `rank` sets `Ranking.capture_withheld`, counts no seed as unmeasurable,
  leaves every capture `None` and out of `as_dict()`, and sorts the table
  on each agent's mean P&L over buy-and-hold's (`mean_excess_pnl`, with
  `seeds_ahead`);
- the MCP tools `evaluate_strategies` and `rank_strategies` send no capture
  field. They send the buy-and-hold comparison and the reason in its place.

On pt-v19 and every earlier preset each of these reports the capture ratio
as before.

## Volume

**Timescale:** tick volume every tick; two persistent states once a day.

Tick volume is a product of multipliers on a minute's share of average
daily volume (`market/tick.rs:1272-1370`):

```math
V^{\mathrm{vol}}_{i,t} = \Big\lfloor \frac{\bar A_i}{390}\,M^{\mathrm{var}}_d\,M^{\mathrm{pers}}_d\,M^{h}_{i,d}\,\Psi_{i,t}\,c^{\mathrm{vol}}(\tau_t) \Big\rfloor
```

```math
M^{\mathrm{var}}_d = \mathrm{clip}\Big(1 + g_V\Big(\frac{v_d}{\bar\sigma_M^{2}} - 1\Big);\ 0.25,\ 4\Big),
\quad
M^{\mathrm{pers}}_d = \mathrm{clip}(e^{\vartheta_d};\ 0.25,\ 4),
\quad
M^{h}_{i,d} = \mathrm{clip}\Big(1 + g_h\Big(\frac{h_{i,d}}{\sigma_k^{2}} - 1\Big);\ 0.25,\ 4\Big)
```

```math
\Psi_{i,t} = f_0 + r_V \min\Big(100\,\frac{\lvert P^{\ast}_{i,t} - P^{o}_{i,d} \rvert}{P^{o}_{i,d}},\ c_V\Big) + n_V\,U_{i,t},
\qquad
c^{\mathrm{vol}}(\tau) = 0.4 + 2.5\,(1 - \tau)^{3} + 2\,\tau^{2.5}
```

$c^{\mathrm{vol}}(\tau)$ is the intraday U-shape: 2.9 at the open, 1.07 at its lowest
around 12:50, 2.4 at the close. Two more multipliers, one for closed-market
ticks and one for news a caller supplies, are 1 in a normal run.
$\Psi$ raises volume with the day's move so far, measured on the model price. The persistent volume state is an AR(1)
stepped at each close (`engine.rs:4291-4303`):

```math
\vartheta_{d+1} = \rho_V\,\vartheta_d + \sigma_V\,Z
```

Average daily volume $\bar A_i$ is fixed for the run. Tick volume also sets
the size of the settlement slices, so a volume dial is also a price dial.

| Symbol | Dial | Value | Kind | Source |
|---|---|---|---|---|
| $g_V$ | `volume_variance_gain` | 0.02840 | fitted | search |
| $\rho_V$ | `volume_persistence` | 0.7 | fitted | a 30-seed sweep against `volume_change_acf1` and `volume_abs_return_corr` |
| $\sigma_V$ | `volume_innovation_sigma` | 0.21 | fitted | same sweep |
| $g_h$ | `volume_idio_variance_gain` | 0.2 | measured | 120-seed paired measurement on `volume_change_acf1`; flat from 0.2 to 0.3 |
| $f_0$ | `volume_move_floor` | 0.6 | chosen | reference implementation |
| $r_V$ | `volume_move_response` | 0.8 (1.0) | fitted | with less transient market noise, volume tracked a company's own move too tightly: the two-year panel's `volume_abs_return_corr` read 0.639 against a ceiling of 0.63 at 1.0 and 0.618 at 0.8 (box ptv20g2) |
| $c_V$ | `volume_move_cap` | 12 | fitted | a sweep found 8, 12 and 20 alike |
| $n_V$ | `volume_move_noise` | 0.2 | chosen | reference implementation |

## Bonds

A roster can include three bond indices: `UST2Y`, `UST10Y` and `IGCORP`
(`rates.rs:208-242`), with `Universe.random(..., bonds=True)`. They are
simulated constant-maturity indices, priced off the model's curve; they
read the curve and write nothing back to it.

**Timescale:** each index reprices at the first open after its yield
changes (`rates.rs:504-549`). UST2Y reads $y^{2}$, UST10Y reads $y^{10}$, and
IGCORP reads $y^{10}$ plus a spread re-marked to $y^{c} - y^{10}$ whenever
the corporate yield changes, which on pt-v20 is almost every session
(`rates.rs:258-273`, `rates.rs:495-509`). With the yield change $\Delta y$
as a fraction, the level moves by carry, duration and convexity
(`rates.rs:287-305`):

```math
L \leftarrow L\,\Big(1 + \frac{y^{\mathrm{prev}}}{252} - D\,\Delta y' + \tfrac12\,C\,(\Delta y')^{2}\Big),
\qquad \Delta y' = \min\Big(\Delta y,\ \frac{D}{C}\Big)
```

The carry is added once a session. The cap stops the quadratic from
pricing a large rise as a gain.

| Index | Duration $D$ (years) | Convexity $C$ | Quoted spread | Kind | Source |
|---|---|---|---|---|---|
| UST2Y | 1.9 | 4.6 | 0.6 bp | derived | a 2-year par note at 4%; depth from SHY's dollar volume, 2015 to 2025 |
| UST10Y | 8.5 | 84 | 0.6 bp | derived | a 10-year par note at 3.2%; depth from IEF |
| IGCORP | 7.0 | 100 | 0.8 bp | derived | a 40/30/15/15 mix of 3-, 7-, 20- and 30-year par bonds at 5%; depth from LQD |

Each index has its own ten-level maker ladder around its level, widened by
the VIX. An agent trades it with market orders, whose fills apply once on
the next tick; they move the maker's inventory, which decays with a
15-tick half-life, and never the level (`rates.rs:385-423`,
`rates.rs:556-607`). The agent book, limit orders and news do not apply to
the indices. Graded: rows R1 to R4 read the curve the indices price off.

## Scenarios

A scenario is a file of changes to the economy or the market, applied once
a session, before the open (`python/tradefloor/scenario.py:1162-1201`). It
states its shocks and the knock-on effects it assumes, separately. The model
does not decide what a war or an oil shock does to the economy; the scenario
states it, and the model carries it to prices.

**Timescale:** once a session, before the open. Scenario day $d$ counts
sessions from the start of the run, or from the fork.

### The packaged recession

`recession.yml` is dated on September 2007: day 50 is December 2007, the
NBER peak, and a month is 21 sessions. From 0.8.5 the recession ends and
hands back to the model's own cycle
(`python/tradefloor/scenarios/recession.yml`):

- The cycle is set to contraction on day 50 and held 315 sessions, to March 2009. On day 365 it is set to trough, and on day 428, June 2009, the NBER's trough, to recovery, and then left to the model's own cycle. Left to its own hazards from day 365, the model's cycle kept one seed in 30 in trough for more than 24 months after the onset.
- Growth is held at -2% from day 50 to day 364, then released.
- The VIX is multiplied by 3 for 60 sessions from day 50.
- The corporate yield is 150 bp wider for 378 sessions from day 50, comes back to its day-50 level over the next 252, eases 110 bp more over the 378 after that, and is released to the model's own chain on day 1058. Moody's Baa yield (FRED DBAA) was 6.65% in December 2007, 9.2% at its peak in November 2008, 6.3% in September 2009 and 5.25% in December 2011.
- Every company's earnings are multiplied down to 0.65 of their level by day 301, in two ramps (0.808 over 121 sessions from day 50, 0.805 over 131 from day 171), held there to day 490, September 2009, and restored in two ramps: to 0.96 of their pre-shock level by day 680, June 2010 (1.477 over 189 sessions from day 491), and to 1 by day 932 (1.042 over 252 from day 680). The cut stacks on pt-v20's earnings cycle, which takes about 18% off in a contraction at its depth of 0.2. Together they fall about 47% by day 365. S&P 500 four-quarter operating earnings fell 57%, from $91.47 in Q2 2007 to $39.61 in Q3 2009, and were about 0.92 of the 2007 peak over 2010 and 1.05 over 2011.

The first 120 sessions are the 0.8.5 recalibration's, to within the last
bit, and it measured -44.7% at 120 sessions, paired against the same seed
with no scenario, on the certified roster over seeds 301 to 330. That was
pt-v20 before its graded arm (box ptv20g3). The scenario sets the true phase
and growth. Under [the publication dials](#true-and-published-state) an
observer reads contraction about $L_c$ sessions after day 50 and growth as
quarterly means. The recovery was tuned on the arm's permanent market share
without the volatility feedback (the file's header gives the path), and the
grade measures it on pt-v20 as it ships (rows S1a, S1b and S2 of the
twelfth registration).

### Scenario operations

A **pin** gives a field an exogenous path $x_d = P(d)$
(`python/tradefloor/scenario.py:749-811`):

```math
\text{hold: } P(d) = v,
\qquad
\text{ramp: } P(d) = x_0 + (x_1 - x_0)\,\mathrm{clip}\Big(\frac{d - b}{o};\ 0,\ 1\Big),
\qquad
\text{step: } P(d) = \begin{cases} x_0 & d < a \\ x_1 & d \ge a \end{cases}
```

An **intervention** changes a field relative to its value $\tilde x_a$ on the
day $a$ it fires, by $\mathrm{op} \in \lbrace$ set, add, multiply $\rbrace$
(`python/tradefloor/interventions.py:1247-1255`,
`python/tradefloor/scenario.py:1272-1302`). With
$x^{\dagger} = \mathrm{op}(\tilde x_a, v)$:

```math
\text{impulse: } x_a = x^{\dagger},
\qquad
\text{hold or permanent: } x_d = x^{\dagger},
\qquad
\text{ramp: } x_d = \tilde x_a + (x^{\dagger} - \tilde x_a)\,\frac{d - a + 1}{D}
```

over the active days: $a$ alone for an impulse, $a$ to $a + D - 1$ for a hold
or ramp of length $D$, and every day from $a$ for a permanent change. A
permanent `add` therefore freezes the field at one level; it does not add to
a moving value.

**What a pin does to the economy underneath.** A pin writes the value each
morning. The macro step still runs at every close, starting from the pinned
value, and every other field follows from it; the next morning the pin
overwrites the pinned field again (`python_engine.rs:2970-3018`). When the
pin ends, the economy carries on from the last value. Two fixes on
`fix/ptv20-core` for this release (commits `cd15126` and `fe8bcef`) close
a leak the daily corporate yield opened on pt-v20: a pinned corporate yield
now holds through the close, a meeting's re-anchoring included, and a
pinned VIX adds no VIX term to the corporate yield's daily move. Before
them, holding the VIX at 45 walked the yield from 2.81% to 2.42% in five
sessions.

### Target channels

On pt-v20 (`python/tradefloor/interventions.py:476-790`):

| Target | Reaches prices through | Delay |
|---|---|---|
| `macro.corporate_yield` | the discount rate in fair value; the IGCORP bond index | same session |
| `macro.treasury_2y`, `macro.treasury_10y` | the bond indices; the 10-year also moves the corporate yield at the next close | next open |
| `market.earnings` | every company's published earnings and book value, multiplied | next tick |
| `macro.vix` | every variance process, jump rates, the crisis episode, the spread | same session to next |
| `market.liquidity` | average daily volume: book depth, impact, volume | same session |
| `macro.policy_rate` | the 2- and 10-year yields, and through the 10-year the corporate yield | days |
| `macro.inflation` | nominal output, the 10-year term premium, the VIX target | next session on |
| `macro.growth` | nominal output | next session on |
| `macro.cycle` | the earnings cycle's target at the next close; the spread multiplier and the bank | next session on |
| `commodity.oil` | inflation, monthly | a month or more |
| `macro.unemployment` | the bank and the Phillips curve | a meeting or a month |
| `policy.tariff_rate` | inflation, monthly, very weakly | a month or more |
| `macro.qe_pe_boost` | nothing on pt-v20 | |
| `macro.fear_greed` | nothing | |

A **forced VIX** (`vix_sets_variance`) goes further: on a forced close the
market-factor variance is set to its targets, with no shock term
(`market/factor_vol.rs:926-956`):

```math
v^{f}_{d+1} = \mathrm{clip}(\bar v^{f}_d),\quad v^{s}_{d+1} = \mathrm{clip}(\bar v^{s}_d),\quad v_{d+1} = \mathrm{clip}\big((1 - w_s)\,v^{f}_{d+1} + w_s\,v^{s}_{d+1}\big)
```

A pinned VIX below the anchor calms the market: at a VIX of 15 on a roster
whose anchor is 17.9, the fast target is about two thirds of the baseline
variance.

**`market.earnings`** multiplies each company's published earnings, and
scales its book value by the same factor, through `set_fundamentals`
(`python/tradefloor/interventions.py:634-691`). Only `multiply` is
accepted. It stacks on the earnings cycle and moves fair value from the
next tick; when its last window closes the scenario writes the earlier
level back. On pt-v20 a multiply by 0.6 over 60 sessions took the index
down 39.6% on one seed.

**Growth's floor.** A written growth rate may go down to -10%, where every
other rate keeps a floor of -5% (`units.rs:52-92`). US real GDP fell 7.4%
year on year to 2020Q2 and 10.0% annualised in 1958Q1 (FRED GDPC1). The
macro step's own clip was already -10.

Seven scenarios ship: `curve_shock`, `geopolitical_conflict`,
`liquidity_crisis`, `oil_price_spike`, `policy_regime_shift`, `rate_shock`
and `recession`. Two were recalibrated on pt-v20, on the certified roster
over seeds 301 to 330 (box ptv20g3):

- `recession`, to 2008's depth: contraction, growth set to -2%, the VIX times 3 for 60 sessions, credit +150 bp, and earnings times 0.6 over a year. The index falls 30.4% by session 63 and 44.7% by session 120.
- `liquidity_crisis`, to March 2020's speed: book depth times 0.4 and the VIX times 3.5 for 25 sessions, credit +50 bp, earnings times 0.85. The index falls 10.0% in 21 sessions and 14.8% by 63, then recovers.
- `curve_shock` is new: the policy rate, both Treasury yields and the corporate yield up 200 bp on day 50. On `Universe.random(20, seed=101, bonds=True)` the 10-year index falls 15.4% on the day and the median stock 3.9% by day 120.

The other four files record effect sizes measured before pt-v20.

## Off in pt-v20

These mechanisms exist in the code and are switched off, or cannot act, on
pt-v20. Each dial is 0 unless stated. Earlier presets use some of them.

- **Market shocks in fair value** (`fair_value_market_share`): only a company's own and its sector's shocks reach the fair-value level.
- **Noise in the earnings cycle** (`earnings_cycle_sigma`): the cycle follows the phase path alone.
- **Overnight move** (`overnight_variance_ratio`): the draws are taken and nothing is applied, so each session opens at the last print.
- **Crisis correlation blend** (`crisis_blend_gain`, `crisis_blend_variance_damp`): no extra market loading in a crisis.
- **Forced selling** (`forced_flow_gain` and its four partner dials): no correlated selling above a VIX threshold.
- **Remembered stress** (`universe_stress_weight`, `universe_stress_decay`, `regime_stress_points`): the crisis spike reads today's VIX only, and the business cycle has no direct path to prices.
- **QE valuation channels** (`qe_pe_gain`, `qe_pe_stock_gain`): QE reaches fair value only through the 10-year yield.
- **Book-value floor on earnings** (`fair_value_book_floor`).
- **Variance cascade and per-name persistence spread** (`garch_cascade_components`, `garch_beta_dispersion`, `garch_omega_sector_scaled`, `garch_innovation_commensurate`).
- **Market-variance extras** (`market_vol_level_sigma`, `market_vol_vix_excursion`, `market_vol_alpha_excursion`, `market_vol_vix_smooth`, `market_burn_in_sessions`).
- **Self-exciting jumps** (`jump_idio_excitation`), and jumps in the market-variance shock (`jump_market_variance_share`).
- **Smooth size and spread curves** (`size_effect_smoothness`, `spread_size_smoothness`): the step functions above are used.
- **Square-root impact** (`order_flow_impact_law`): the clamped participation law above is used.
- **Company volume state** (`volume_idio_persistence`, `volume_idio_sigma`).
- **Down-market idiosyncratic suppression** (`market_idio_down_suppress`) and a beta-dependent idiosyncratic scale (`idio_sigma_beta_exponent`).
- **VIX extras** (`vix_anchor_reversion`, `vix_innovation_sigma`, `vix_jump_intensity`, `vix_target_offset`). With `vix_level_identity` = 1, the VIX target no longer reads the business-cycle table, `vix_cycle_amplitude`, `vix_realised_vol_weight` or `market_vol_vix_anchor`, although those dials still carry values.

## pt-v19: reproducing earlier work

pt-v19 was the default in 0.8.0 and 0.8.1. It still runs, and replays
exactly, with `model="pt-v19"`. Every equation above holds for it with the
values below, except where this section gives pt-v19's own form. pt-v19
fails 10 of the 28 rows pt-v20 was graded on: B9, C4a, C4b, C5, C6, C7,
C8, R1, R4 and E1 (design repository, `programme/results/ptv20/criteria-g3.txt`).

| Dial | pt-v19 | pt-v20 |
|---|---|---|
| `quote_model_weight` | 0 | 1 |
| `closing_auction` | 0 | 1 |
| `fair_value_news_share` | 0 | 1 |
| `opening_mispricing_sigma` | 0 | 0.016 |
| `opening_market_sigma` | 0 | 0.10 |
| `cascade_gain` | 1 | 0.1 |
| `treasury_10y_noise` | 0.03 | 0.025 |
| `treasury_2y_noise` | 0 | 0.022 |
| `flight_to_quality_gain` | 0.02 | 0.008 |
| `flight_to_quality_day` | 0 | 1 |
| `corporate_yield_daily` | 0 | 1 |
| `book_depth_coefficient` | 0 | 0.75 |
| `book_depth_exponent` | 0 (read as 0.5) | 0.5 |
| `book_depth_reach` | 0 (read as 1) | 1 |
| `book_shared` | 0 | 1 |
| `book_refill_half_life` | 0 | 27 |
| `book_resting` | 0 | 1 |
| `fill_impact_coefficient` | 0 | 0.314 |
| `earnings_cycle_depth` | 0 | 0.35 |
| `earnings_cycle_upside` | 0 | 0.09 |
| `market_factor_sigma` | 0.007593 | 0.006454 |
| `jump_intensity_market` | 0.05658 | 0.02829 |
| `volume_move_response` | 1.0 | 0.8 |

Where the equations differ on pt-v19:

- **No fair-value level.** $v \equiv 0$: every shock stays in $s$ and reverts on the mispricing's half-life, and the ten attribution slots sum to $\Delta s$.
- **No earnings cycle.** $\chi \equiv 0$, so $n_d = 1 + \eta (N_d / N_0 - 1)$.
- **The opening.** The whole day-zero premium goes into $s$: $s_{i,0} = \mathrm{clip}(g_i;\ -0.9,\ 0.9)$, with a cross-sectional sd of about 0.33. Every run opens with a drift back toward fair value that lasts months.
- **The quote and the close.** The maker quotes around the last print moved by the tick's news term, $\hat P_{i,t} = P_{i,t-1} e^{N_{i,t}/390}$, and the close is the last minute's print.
- **The 2-year** is the formula at every step: $y^{2} = 0.85 r^{p} + 0.15 y^{10}$.
- **Flight to quality** reads the previous session's closing-minute index return, only when it moved more than 0.5%, at a gain of 0.02. In practice it never fires.
- **The corporate yield** is set only at meetings; between them only the one-way floor $y^{c} \ge y^{10} + 0.8$ moves it, so the discount rate moves in steps.
- **Agents' orders.** An agent's book is the maker's ten levels and nothing else; an order beyond them is cut off, and a fill takes no depth and does not move the maker's inventory. In 0.8.5 its fills reach the market once, on the next tick, through the order-flow law $O$ above. In 0.8.1 the harness held them as order flow for every tick of the next step, 65 times at six steps a day, after the agent had filled at the pre-trade book, so an agent collected its own impact.
- **The squeeze and cascade term** is not scaled, and volume responds 1.0 per 1% a company has moved.

pt-v19's values for every other dial are the ones in the tables above.

## Known gaps

These are places where the code does something this document can state but
not defend, or where it could not state the code's behaviour as a clean
equation. They are listed so a reader can judge them.

**Openings.**

- The neutral rate $r^{\ast}$ = 0.0482 was read off pt-v18's burn-in, which always opened in expansion. The opening corporate yield ranges from 2.5% to 6.7% across seeds. On pt-v20 the stationary opening books the resulting rate term into each company's fair-value level, so no run opens with a drift from it; on pt-v19 it shifts the opening mispricing by -0.04 to +0.03.
- The opening yield also depends on the roster, through the roster-derived VIX anchor acting on the burn-in; the path has not been traced.
- The opening VIX is close to a fixed point of the burn-in, because the market is frozen during it, so every run on a roster opens at nearly the same VIX.
- After the burn-in the macro calendar restarts at day 1, so the first monthly step of a run comes 41 sessions after the last one of the burn-in.
- The opening's no-price-move identity holds because the buyback factor is 1 on day zero; an opening applied later would not be exact.

**Macro.**

- The growth shock on entering a phase fires on the first two sessions of the phase, not one (`economy/daily.rs:665-690`). Whether that is intended is not recorded.
- Unemployment sits on its 2.5% floor for much of an expansion. Its long-run mean is 3.6% against 5.7% in the US.
- The cycle's monthly hazard is turned into a daily probability as $h/21$, an approximation to $1 - (1 - h)^{1/21}$ that runs 4% to 15% high. The opening draw uses the hazard alone, without the ladder, so it is close to stationary but not exactly so.
- Row 6 of the central bank's ladder can never fire, because inflation is capped at 6%. While the emergency condition holds, a meeting is held at every close.
- The QE terms apply once a meeting where the code's comments say once a day. QE has no price effect, so this changes nothing measurable.
- The daily corporate-yield increments have no ceiling; only the meeting re-anchors the level. A phase change between meetings changes the multiplier for later increments, not the level.
- The earnings cycle's pull is fixed at a 60-session half-life and was never searched, so a contraction shorter than about 60 sessions reaches well under half the depth.
- The model's inflation almost never leaves the under-3% regime, so stocks and Treasuries are nearly always in flight to quality.

**Prices.**

- A jump is written to $s$ at the close and reaches the price at the next session's first ticks. So the index return the VIX reads on the jump's day does not contain it; it contains the previous day's. A jump beyond the ±25% band is partly removed by the breaker.
- The market leg's down-tick tilt is re-centred, but the lagged down-beta multiplies the tilted term, so a small drift of about $-a a_L \beta \sigma/\sqrt{2\pi}$ a tick remains on ticks where the market is down on the trailing window. It has not been measured.
- When the $\pm 0.9$ cap on $s$ binds, the change is not booked to any attribution slot. When the 50,000 price cap binds, $s$ is not re-derived.
- The fair-value level scales fair value inside a tick as if the valuation were homogeneous in earnings and book; the 0.01 floor and the buyback factor break that slightly, and the next tick recomputes.
- `ModelParams`' doc comment says market and sector shocks stay in $s$; the code sends sector noise and sector news to $v$, as this document states.
- The company GJR is fed the whole noise term, market and sector parts included, while its coefficients describe a company's own returns. The floor, not $\omega$, sets the resting level in 8 of 12 sectors.
- The buyback factor is re-evaluated each tick at the current price over all elapsed sessions rather than integrated along the path, and it uses the engine's global session count, so a company listed mid-run is credited with buybacks from before it existed, and no shares are retired.

**The VIX.**

- With the VIX held at 65 the market is 3.6 times as volatile as with it held at 5, against 6.2 times in real markets (pt-v19: 5.2). The lower market sigma and jump rate take the lever down with them. It is reported beside the grade and does not gate it.
- The index variance $V_d$ leaves out the crisis epicentre's scaling, so during an episode the VIX's read-back is not quite the variance the market realises. It also reads the lagged down-beta's condition once a session, where the market samples it every tick.

**Agents and the book.**

- An agent's permanent impact lives in $s$, so it decays on the mispricing's half-life rather than lasting.
- The model's own flow never meets depth an agent consumed or the latent depth, so an agent's temporary impact reaches the tape only through the maker's inventory.
- On the closing tick, agents' resting-order fills are recorded at book prices while the print is the model price.
- The settlement book keeps 32 orders a side, so a far agent order can be left out of a tick's settlement.
- The maker's inventory never decays; only opposing flow unwinds it.
- Tick volume reads the model price, not the print. Tape volume is the modelled volume, not the traded quantity, and agent trades add none; average daily volume is fixed for the run.

**Scenarios.**

- A permanent change on the corporate yield freezes it: after that, no policy, inflation, VIX or cycle move reaches the discount rate.
- The effect sizes in four of the seven scenario files were measured before pt-v20, and some docstrings describe an older VIX anchor, an older crisis threshold and the switched-off crisis blend. The `macro.treasury_2y` note says the 2-year is recomputed at every close, which is pt-v19's law.
- A pinned change of phase does not get the growth shock a natural phase change gets.
- `World.run` builds a fresh scenario on every call, so a hold or ramp added with `World.apply` loses its anchor between calls. `pin_macro(epicentre=None)` does not clear an epicentre pin.

**Records.** The provenance ledger labels `crisis_epicentre_end_sessions` as
out of scope, but it is read; and it labels `market_beta_down_asym_lag`
as measured, while its own entry says 0.46 is fitted. It has no entry for
three dials pt-v20 moved, `market_factor_sigma`, `jump_intensity_market` and
`volume_move_response`, which this document calls fitted from the grading
notes. It calls `book_refill_half_life` measured where the code derives it.
This document uses the corrected kinds.

## Reproducing a result

The same version, preset, seed, roster and scenario give the same market on
every platform. Each release runs one fixed simulation on five platforms and
stops if any digest differs (`tests/known_answer.json`). The engine uses its
own `exp`, `log`, `pow`, `sin` and `cos`, so no system maths library can
change a result. A `RunManifest` records every input a run needs, and
`RunManifest.reproduce()` stops at the first mismatch.

To cite the model, name the version and the preset: "tradefloor 0.8.5,
preset pt-v20". See the README's "Citing tradefloor" section.
