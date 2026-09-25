# The tradefloor model

**From 0.8.5 the default preset is pt-v20, and this document still describes
pt-v19.** pt-v20 is pt-v19 with the dials listed in
[Coming in pt-v20](#coming-in-pt-v20) and in `ModelParams.pt_v20` in
`rust/src/params.rs`, where each one carries its value and its source.
Every equation below holds for both presets. Where pt-v20 turns on a term
that pt-v19 switches off, this document does not write it out yet. The
pt-v19 values below are pt-v19's, and `tf.ModelParams.from_preset("pt-v20")`
gives pt-v20's.

This document states the tradefloor market model as equations. It describes
**tradefloor 0.8.1** running the default preset **pt-v19**. Every equation
was read off the code at the `v0.8.1` tag, and each one names the source line
it comes from, as `file:line` under `rust/src/`. Parameter values are
pt-v19's, as `tf.ModelParams.from_preset("pt-v19").to_dict()` returns them,
rounded here to four significant figures.

A preset is a frozen set of coefficients. The equations below hold for every
preset, but many terms are switched on or off by a preset's dials, and this
document describes the terms pt-v19 runs. Terms that pt-v19 switches off are
listed once, in [Off in pt-v19](#off-in-pt-v19), and not written out.

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
| session (day) | 390 ticks | `python_engine.rs:1435` |
| macro month | 21 sessions | `economy/state.rs:65` |
| macro quarter | 63 sessions | `economy/state.rs:83` |
| year | 252 sessions | `market/tick.rs:209`, `economy/state.rs:62` |

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
  -> policy rate -> 10-year and corporate bond yields
  -> nominal output (GDP x CPI)
  -> the VIX

fair value V (every tick)            = sector P/E x earnings x rate term
mispricing s (every tick)            = mean reversion + herding + news
                                        + order flow + noise, jumps at the close
model price P* (every tick)          = V exp(s)
printed price P (every tick)         = P* traded through the market maker's book
volatility (daily, at the close)     = per-name GJR-GARCH, market-factor
                                        variance, sector variance; all read the VIX
```

**Fair value** moves slowly: with the corporate bond yield, which changes at
central-bank meetings, and with nominal output, which compounds daily.
**Mispricing** carries everything fast: the common market factor, sector
factors, each company's own noise, news, jumps and order flow. It reverts to
zero with a half-life of about 40 sessions. **The printed price** is the
model price after it has gone through a limit order book quoted by a market
maker, so trades move it and a large order pays for depth.

The VIX sits in the middle. It is computed from the index's own conditional
variance, plus a fear response to the day's return, and every variance
process in the market reads it back.

### The order of a session

1. **Open** (`engine.rs:2653-2749`). The crisis episode is stepped (start, end, epicentre), the day's company news is drawn, and each company's opening price $P^{o}$ is set to its last print.
2. **390 ticks** (`market/tick.rs:761-1493`). Each tick: draw the market factor and the sector factors; for each company, draw its own noise, update $s$, compute $V$ and $P^{\ast}$, draw tick volume, and settle the print through the book.
3. **Close** (`engine.rs:2911-3186`). Each company's GJR variance is updated from the day's noise, the momentum term rolls, jumps are drawn and applied to $s$, the market-factor and sector variances are updated, and the volume state steps.
4. **Macro step** (`engine.rs:3922-3995`, `engine.rs:3429-3640`). The index's conditional variance is computed and the VIX steps, then the economy, the business cycle and the central bank.

The next session reads the new VIX, rates and output.

**Randomness.** Each process draws from its own stream, derived from the
run's seed (`rng.rs:363-469`): market, economy, news, jumps, overnight,
volume, crisis epicentre and others. The market stream's schedule depends
only on the roster and the sectors, never on a price or a preset, so two
presets run on the same seed see the same market shocks (`params.rs:30-35`,
`market/mod.rs:15-32`). Some streams, such as the economy's, take a number
of draws that depends on the state, and that dependence stays inside the
stream.

**The opening.** Before session 1 the economy runs 755 macro steps on its
own, with the market frozen and the day's return set to zero
(`engine.rs:1430-1499`). The business cycle's opening phase and its age are
drawn from the cycle's stationary law first (`engine.rs:1343-1367`). Because
the market is frozen during this burn-in, the VIX settles near a fixed level
that depends on the roster and hardly on the seed: on
`Universe.random(40, seed=111)` it opens at 20.11 on 28 of seeds 101 to 130,
and at 20.18 and 20.24 on the other two, where inflation ended the burn-in
above 3%.

## The macro economy

The economy steps once per session, after the close (`engine.rs:3890-3900`).
Its day counter $d$ starts at 1 on the first close. Within one step the order
is: the market P/E and the day's index return are read from prices, the VIX
and the economy are updated, the business cycle may change phase, and then
the central bank meets if a meeting is due (`engine.rs:3944-3995`,
`engine.rs:3502-3631`). All macro draws come from the economy stream.

The price path reads three things from the economy: the **corporate bond
yield**, as the discount rate in fair value; **nominal output**, which scales
earnings; and **the VIX**, which every variance process reads. Everything
else in this section matters to prices only through those three.

Most constants in this section are carried over unchanged from the reference
implementation the engine was ported from, and no design note derives them.
They are **chosen**. The dials that pt-v19 moved carry their own entries.

### The calendar

The macro clock runs on trading sessions. A month starts when
$d \bmod 21 = 0$ and a quarter when $d \bmod 63 = 0$ (`economy/daily.rs:603-604`).
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
(`economy/daily.rs:1517`, `economy/cycle.rs:258-260`). Below a minimum age
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
earnings of [Fair value](#fair-value) (`engine.rs:3944-3982`).

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
ladder acting, a pt-v19 run has 1.05 recessions a decade lasting 9.1 months,
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
(`economy/daily.rs:628-650`), and then growth is updated in three steps:

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

(`economy/daily.rs:670-723`, `economy/daily.rs:686`). "Wrong sign" means
$g > 0$ in C or T, or $g < 0$ in R or E. Fiscal stimulus adds to $g$ at the
end of the monthly step, below.

### Unemployment

**Timescale:** monthly. **State:** unemployment $u$, long-term unemployment
$\ell$, and the structural rate $u^{\ast}$ (all percent).

```math
u_d = \mathrm{clip}\Big(u + 0.3\,\theta^{u}_{\mathcal{P}} + 0.2\,(2 - g) + 0.06\,(u^{\ast} - u)
 - 0.08\,g\,\mathbf{1}[\mathcal{P} \in \lbrace E, R\rbrace,\ g > 1] + 0.06\,Z;\ 2.5,\ 15\Big)
```

(`economy/daily.rs:726-745`). The phase trends $\theta^{u}$ are E -0.05,
P 0, C 0.30, T 0.04, R -0.10. The $0.2 (2 - g)$ term is Okun's law. The
structural rate carries hysteresis (`economy/daily.rs:913-925`):

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

(`economy/daily.rs:787-796`, `economy/daily.rs:756-822`). Most terms read last
month's values; the Phillips term reads this month's unemployment $u_d$,
and the terms are these:

- $-0.2 (u_d - u^{\ast})$ is the Phillips curve.
- $W = 0.08\max(0, w - 2) + 0.02 (w - 4)(\pi - 3) \cdot \mathbf{1}[w > 4, \pi > 3]$ is wage pressure (`economy/daily.rs:798-806`).
- $R^{r}$ is a real-rate drag: $-0.04 (r^{p} - \pi)$ when the policy rate is above inflation, $-0.015 (r^{p} - 3)$ when it is below inflation but above 3, else 0 (`economy/daily.rs:760-766`).
- $\Omega$ is the oil pass-through: $0.01 (o - 80)$ above USD 80 a barrel, $0.005 (o - 50)$ below USD 50 (`economy/daily.rs:768-774`).
- $e$ is the dollar index and $\tau$ the tariff rate. The tariff rate stays at 5, so its term is 0, unless a scenario changes it.
- The phase trends $\theta^{\pi}$ are E 0.015, P 0.015, C -0.02, T -0.01, R 0.01.

The price level compounds daily: $Q_d = Q_{d-1} (1 + \pi_d / (100 \cdot 252))$
(`economy/daily.rs:969`).

| Dial | Value | Kind | Source |
|---|---|---|---|
| inflation target | 2.0 | chosen | |
| `inflation_reversion` $\kappa_\pi$ | 0.55 a month | chosen | reference value; it gives too little persistence against FRED CPI (lag-1 autocorrelation 0.936 against 0.978, `params.rs:313-325`) |
| `inflation_floor` $\pi_{\min}$ | -1.0 | guard | |
| `inflation_ceiling` $\pi_{\max}$ | 6.0 | guard | real CPI inflation peaked at 9.0 in June 2022, so this binds in a 2022-like episode |
| `phillips_curve_coeff` | 0.2 | chosen | |

### Fiscal policy

**Timescale:** monthly (`economy/daily.rs:941-965`). In contraction and
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
the reference implementation. Row 13, `fed_liftoff_rule` = 1, is pt-v19's
addition: it mirrors the ladder's cut rows, and it moved the long-run mean
policy rate from 1.7% to 2.6%, against 2.9% in the US 1990 to 2025 (design
note results/macro-cycle §4).

**Quantitative easing** starts when the policy rate is at or below 0.25 in a
contraction, with purchases of USD 120bn a month, and tapers by 15 a meeting
in expansion (`economy/central_bank.rs:358-391`). On pt-v19 it reaches
prices only through a small cut in the 10-year yield; its direct channels
into valuation are switched off.

### Bond yields

**Timescale:** daily for the 10- and 2-year yields and the credit floor; at
meetings for the corporate and mortgage yields.

```math
y^{10} \leftarrow \mathrm{clip}\big(y^{10} + 0.05\,(r^{p} + \mathrm{TP} - y^{10}) + 0.03\,Z;\ 0.5,\ 12\big),
\qquad
\mathrm{TP} = 1 + \max(0,\ 0.3\,(\pi - 2)) + \max(0,\ 0.002\,(b - 100))
```

```math
y^{2} = 0.85\,r^{p} + 0.15\,y^{10}
```

(`economy/daily.rs:1435-1452`). On a day whose last tick moved the index by
more than 0.5%, the 10-year also shifts by $\mp 0.02$ times that move,
depending on whether inflation is above 4 or below 3
(`economy/daily.rs:1455-1473`).

At a meeting the 10-year is pulled halfway to $r^{p} + 1 + \max(0, 0.3(\pi - 2))$
plus the rate change, and the **corporate bond yield** is set
(`economy/central_bank.rs:303-355`):

```math
y^{c} = y^{10} + \mathrm{clip}\Big(\big(1 + 0.02\,(X - 12)\big)\,m_{\mathcal{P}};\ 0.8,\ 6\Big),
\qquad m:\ E\ 1.0,\ P\ 1.1,\ C\ 2.8,\ T\ 3.5,\ R\ 1.4
```

Between meetings the corporate yield changes only through a daily floor
(`economy/daily.rs:1558-1568`):

```math
y^{c} \leftarrow \max(y^{c},\ y^{10} + 0.8)
```

So the discount rate that fair value reads moves in steps at meetings, and
ratchets up between them if the 10-year rises. The spread multipliers, the
0.8 floor and the other constants are chosen. `daily_credit_floor_gain` = 1
turns the daily floor on; without it the spread was measured drifting to
0.42 points within 121 days (`params.rs:4338-4353`).

### Oil and the dollar

**Timescale:** daily. Oil reaches prices only through inflation; the dollar
reaches them through inflation and oil.

```math
o \leftarrow \mathrm{clip}\Big(o + 0.03\,\big[(75 + 3g)(1 + a_d) - o\big] + p^{I} - 0.08\,(e - 100) + \mathrm{opec} + 2Z;\ 35,\ 150\Big)
```

(`economy/daily.rs:1069-1102`). $a_d = 0.03 \sin(2\pi (d' - 62)/252)$ is a
seasonal term on the target, with $d'$ the day of the macro year. $p^{I}$ is
an inventory pressure that is zero while inventory is between 40 and 60
(inventory is a driftless random walk at `oil_supply_response` = 1,
derived). Every 63 sessions an OPEC decision adds $\pm(2.5 + 3U)$ with
probability 0.55 when the price is more than USD 10 from 80, or
$3(U - 0.5)$ with probability 0.2 otherwise (`economy/daily.rs:1029-1067`).

```math
e \leftarrow \mathrm{clip}\Big(e + 0.02\,\big(100 + 3\,(r^{p} - 2.5) - e\big) + 0.05\,(X - 25.5)^{+} + 0.3\,Z;\ 80,\ 130\Big)
```

(`economy/daily.rs:1158-1175`). Above a VIX of 25.5 the dollar gets a
safe-haven bid (`usd_crisis_vix_threshold`, chosen).

Gold, copper, housing, confidence, the fear and greed index and the trade
balance are computed each day and read by nothing on the price path.

### The economy's outputs

1. **The corporate bond yield** $y^{c}$, as the discount rate in fair value (`fair_value.rs:143-148`). The engine always supplies it, so the fallback to the policy rate never runs.
2. **Nominal output** $N_d = Y_d Q_d$, which scales every company's earnings and book value (`market/tick.rs:361-371`).
3. **The VIX** $X_d$, which every variance process, the jump rate, the crisis gates and the market maker's spread read.

The business cycle reaches prices only through these: through growth and
inflation into nominal output, and through the spread multiplier into the
corporate yield at the next meeting. A natural change of phase shocks growth
at once, which reaches prices slowly through nominal output; a phase pinned
by a scenario skips that shock and first moves prices at the next meeting.

## Fair value

**Timescale:** recomputed from scratch every tick for every company
(`market/tick.rs:1023-1045`). It has no state of its own. Its inputs move at
three speeds: the corporate yield and nominal output change once a session,
after the close; the last print changes every tick and enters only through
the buyback term; a company's earnings, book value, revenue growth and sector
are fixed for the run.

### The valuation

A company has trailing earnings per share $E_i$, book value per share $K_i$,
revenue growth $\gamma_i$ (a fraction a year) and a sector $k$ with anchor
P/E $\Pi_k$. Earnings are restated each tick for nominal growth and
buybacks:

```math
n_d = 1 + \eta\Big(\frac{N_d}{N_0} - 1\Big),
\qquad
B_{i,t} = \exp\!\Big(\kappa\,\frac{E_i\,n_d}{P_{i,t-1}}\cdot\frac{d}{252}\Big),
\qquad
\hat E_{i,t} = E_i\,n_d\,B_{i,t},\quad \hat K_{i,t} = K_i\,n_d\,B_{i,t}
```

(`market/tick.rs:251-264`, `market/tick.rs:361-395`). $N_0$ is nominal
output at the end of the burn-in, so $n = 1$ on the first session. $d$ is the
number of sessions already closed. $B = 1$ for a company with no earnings.

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
- **Buybacks** add about $\kappa E/P$ a year, about 1.9% at a typical earnings yield. They retire no shares.
- The design note measures nominal output growing 4.8% a year over a long run, against 4.8% in the US 1990 to 2025, and the index returning 6.1% a year (results/macro-cycle §0).

| Symbol | Dial | Value | Kind | Source |
|---|---|---|---|---|
| $r^{\ast}$ | `neutral_discount_rate` | 0.0482 | derived | the corporate yield the economy rests at after pt-v18's burn-in (`params.rs:2495-2537`); see [Known gaps](#known-gaps) |
| $\eta$ | `earnings_nominal_growth` | 1.0 | derived | holds the earnings share of nominal output constant |
| $\kappa$ | `buyback_payout_share` | 0.3333 | chosen | US large-cap net buybacks of 1.5% to 2.0% of market value, 2000 to 2025 (`params.rs:2683-2705`); no error bar |
| | rate sensitivity | 1.5 | chosen | reference implementation |
| | rate-term floor | 0.5 | guard | |
| | duration scale | 2.0 | chosen | reference implementation |
| | loss-maker price to book | 1.2 | chosen | reference implementation |
| | `market_pe_buybacks` | 1.0 | derived | the market P/E divides by the same buyback factor |

The sector anchors $\Pi_k$ are in [The sectors](#the-sectors). They are
chosen: carried from the reference implementation, with no market data named.

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

The opening mispricing is not drawn. On the first tick
$s_{i,0} = \mathrm{clip}(\ln(P_{i,0}/V_{i,0});\ -0.9,\ 0.9)$
(`market/tick.rs:1050-1055`), which works out to
$\ln u_i^{PE} - \ln R_{i,0}$ for a profitable company and $-\ln u_i^{K}$ for a
loss-maker. The cross-section of $s_0$ has a standard deviation of about 0.33,
much wider than the model's own stationary spread of about 0.07. So every run
opens with a drift back toward fair value that lasts months. pt-v20 changes
this; see [Coming in pt-v20](#coming-in-pt-v20).

Companies can also be built from SEC EDGAR filings (`python/tradefloor/edgar.py`):
diluted EPS, shares outstanding, equity over shares as book value, and
year-on-year revenue growth. By default each opens at its fair value.

## The price path

### The model price

**Timescale:** every tick. The model price is fair value times the
exponential of the mispricing (`market/tick.rs:1155`):

```math
P^{\ast}_{i,t} = \max\big(0.01,\ V_{i,t}\,e^{s_{i,t+1}}\big)
```

If $P^{\ast}$ leaves the session band $[0.75 P_{i,d}^{o},\ 1.25 P_{i,d}^{o}]$
it is clamped to the band and $s$ is re-derived from the clamped price
(`market/tick.rs:1161-1170`). The printed price is $P^{\ast}$ traded through
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

(`market/tick.rs:1137-1153`), with these terms in order:

- **Mean reversion.** $\phi_\tau = 0.5^{1/(390 H)}$, so $s$ decays by half in $H$ sessions if nothing else acts.
- **Herding.** $\theta \mu$: a share of yesterday's re-rating continues today.
- **The crowd.** $L(s, \mu) = \mathrm{clip}(-g_v s + g_m \mu;\ -\bar L,\ \bar L)$ (`mispricing.rs:174-179`). The crowd buys what trades below fair value and chases yesterday's move a little. It reads $s$ before the tick's update.
- **News** $N$, **order flow** $O$ and the **short squeeze and stop cascade** $Q$, each a daily-equivalent log shock applied at 1/390 a tick. They are defined below.
- **Noise.** $\varepsilon_{i,t}$ is the tick's shock from the factor structure, below. $u(\tau) = 1 + 0.2 (2\tau - 1)^4$ is the intraday volatility curve: 1.2 at the open and the close, 1.0 at midday (`market/hours.rs:104-106`).

The momentum term rolls at the close, before the jumps
(`market/daily.rs:240-244`): $\mu_{i,d+1} = s_i^{\mathrm{close}} - s_i^{\mathrm{ref}}$,
then $s_i^{\mathrm{ref}} \leftarrow s_i^{\mathrm{close}}$. Jumps are excluded
from the next day's momentum (`jump_momentum_share` = 0,
`engine.rs:3275-3280`).

**As a daily AR(2).** Summed over a session, with the crowd term in its
linear range and no clamp binding, the close-to-close mispricing follows

```math
s_{d+1} \approx \psi^{390}\,s_{d} + (\theta + g_m)\,\bar\psi\,(s_{d} - s_{d-1}) + \text{shocks}_d,
\qquad \psi = \phi_\tau - g_v/390
```

with $\bar\psi$ the session mean of $\psi^{k}$. At pt-v19 the two
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

### The factor structure

**Timescale:** every tick. **Draws:** one market normal $z_t$, one normal
$\zeta_{k,t}$ per sector, one normal $\eta_{i,t}$ per company, all on the
market stream, in that order.

The market factor and the sector factors (`market/tick.rs:813-859`,
`market/tick.rs:282-290`):

```math
f_t = \sqrt{v_d}\,\frac{z_t}{\sqrt{390}},
\qquad
G_{k,t} = \bar\sigma_S\,\rho_d\,\sqrt{h^{S}_{k,d}}\,\frac{\zeta_{k,t}}{\sqrt{390}}
```

$v_d$ is the market-factor variance and $h_{k,d}^{S}$ the sector variance
state, both set at the previous close; see [Volatility](#volatility). The
sector sigma scales with the VIX ratio $\rho_d$ because
`sector_vix_coupling` = 1.

A company's tick shock (`market/factors.rs:812-1064`):

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
- $I$ is the company's own noise. Its scale is the company's GJR variance $h_{i,d}$, floored, times a size step $c_i$: 0.8 above USD 50bn of market cap, 1.0 above 10bn, 1.3 above 1bn, 1.6 below (`market/factors.rs:686-697`).
- $\xi_i$ is 1 except during a crisis episode with an epicentre, when it is 2.04 for companies in the epicentre sector and 0.80 for the rest; see [Crisis regimes](#crisis-regimes).

Betas are fixed per company when the universe is built.

| Symbol | Dial | Value | Kind | Source |
|---|---|---|---|---|
| $\bar\sigma_M$ | `market_factor_sigma` | 0.007593 a day | fitted | search against the one-year statistics; the baseline of $v_d$ |
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
(`market/factors.rs:1089-1156`):

```math
Q_{i,d} = \min(0.02,\ 0.5\,\mathrm{SIR}\,r) \cdot \mathbf{1}[\mathrm{SIR} > 0.2,\ r > 0.03]
 - C(\lvert r \rvert) \cdot \mathbf{1}[r < -0.025]
 + C(r) \cdot \mathbf{1}[r > 0.025,\ \mathrm{SIR} > 0.1]
```

The cascade $C(x)$ is 0.007 above a 7% move, 0.0045 above 5%, 0.0025 above
3%, else 0.0005. `cascade_symmetry` = 1 makes the up and down ladders mirror
images, and it and the ladder constants are chosen.

### News

**Timescale:** events are drawn at the open and absorbed tick by tick.

At each open every company draws a uniform and a normal on the news stream.
With probability $\lambda_N$ it has a news event of log size
$\nu_e = \sigma_N Z$ (`engine.rs:2725-2742`). An event reaches company $i$
with weight

```math
w_{e,i} = \begin{cases}
1 & \text{the event is about } i \\
w_p\,(1 + c_p\,\chi_d) & \text{the event is about another company in the same sector}
\end{cases}
```

(`market/factors.rs:728-783`), where $\chi_d$ is the crisis spike (below).
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

(`engine.rs:1879-1889`, `market/factors.rs:512-565`). Over the session an
event moves $s$ by $w \nu_e$: 61% of that in the first minute, 89% by the
fifth and 99% by the 150th. The market
maker re-quotes by the tick's news term before any trade, so the traded price
carries the same profile (`news_quote_revision` = 1, `market/tick.rs:1350-1355`).

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
(`engine.rs:3228-3283`). A market jump hits every company with unit loading;
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
session's first ticks, through the book. At $X = A$ there are about 14
market-jump days a year, of mean -0.85%, and each company has about 1.7
idiosyncratic jumps a year, of standard deviation 7.5%.

| Symbol | Dial | Value | Kind | Source |
|---|---|---|---|---|
| $\lambda_M$ | `jump_intensity_market` | 0.05658 a day | fitted | search; for the two-year excess kurtosis |
| $\mu_M$ | `jump_mean_market` | -0.008522 | fitted | search; negative for skew |
| $\sigma_M^{J}$ | `jump_sigma_market` | 0.002460 | fitted | search |
| $\lambda_I$ | `jump_intensity_idio` | 0.006890 a day | fitted | search |
| $\sigma_I^{J}$ | `jump_sigma_idio` | 0.07521 | fitted | search |
| $c_J$ | `jump_vix_coupling` | 0.2626 | fitted | |
| | `jump_mean_compensated` | 1.0 | derived | a compensated Poisson process |

### The eleven factors

Every change in $s$ is booked to one of eleven factors, which `engine.truth()`
reports (`market/factors.rs:61-63`, `market/tick.rs:1088-1105`), each tick as follows:

| Slot | Factor | Amount |
|---|---|---|
| 0 | reversion | $(\phi_\tau - 1) s_{i,t}$ |
| 1 | momentum | $\theta \mu_{i,d}/390$ |
| 2 | crowd lean | $L(s_{i,t}, \mu_{i,d})/390$ |
| 3 | company news | $N_{i,t}/390$ |
| 4 | order flow impact | $O_{i,t}/390$ |
| 5 | short squeeze | $Q_{i,d}/390$ |
| 6 | random noise | $u(\tau_t) \varepsilon_{i,t}$ |
| 7 | circuit breaker | the change in $s$ when the breaker binds |
| 8 | jump | the jump's change in $s$, at the close |
| 9 | overnight | the overnight change in $s$ (zero on pt-v19) |
| 10 | fair value shift | $-\Delta v$: the permanent share of the name's own shocks, which leaves $s$ for its fair-value level $v$ (zero through pt-v19) |

Over a day the eleven sum to the change in $s$, to rounding, except on a tick
where the $\pm\bar s$ cap binds, which no slot records. On pt-v20 slots 3, 6
and 8 report the whole shock, which is what moved the price, and slot 10 takes
back out the part that went to fair value for good. So slots 0-9 sum to the
price's move at a fixed valuation, and all eleven to the move in $s$.

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
market, sector and own parts together (`engine.rs:2417-2420`). The
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
whose fixed point is 1 (`engine.rs:1104-1132`). $D_{k,d} = \sum_t G_{k,t}$ is
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
`engine.rs:1018-1041`):

```math
I_d = (1 + \varpi)\cdot 100\,\sqrt{252\,V_d},
\qquad
A = (1 + \varpi)\cdot 100\,\sqrt{252\,\bar V}
```

$\varpi$ is the variance risk premium, the gap between the VIX and realised
volatility. $\bar V$ is the same sum at the unconditional point: the factor
at its baseline, sector states at 1, each company's variance at its resting
level, and $\Lambda$ averaged over up and down days. $A$ is computed once, when
the engine is built, and depends on the roster: 24.0 on
`Universe.random(40, seed=111)`, 20.6 on `Universe.random(20, seed=101)`.

### The VIX step

**Timescale:** once a day, in the macro step. **Draws:** one normal and one
or two uniforms on the economy stream, and one normal on the VIX level
stream at the close. **State:** $X_d$, a slow log level $q_d$, and the
anchor's slow memory $M_d$.

A slow level wanders around the anchor (`engine.rs:3031-3044`,
`engine.rs:3840-3863`), and a slow memory tracks the read-back
(`engine.rs:3483-3499`):

```math
q_d = \phi_q\,q_{d-1} + \frac{\sigma_\ell}{G_\ell}\,Z,
\qquad
\Xi_d = \exp\Big(q_d - \frac{1}{2}\,\frac{(\sigma_\ell / G_\ell)^{2}}{1 - \phi_q^{2}}\Big),
\qquad
M_d = (1 - h_M)\,M_{d-1} + h_M \ln\frac{I_d}{A\,e^{-c_A}}
```

The target (`economy/daily.rs:1223-1318`):

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
(`economy/daily.rs:559-583`). The weight on the slow memory falls as the VIX
rises: $a(X) = 1 - (1 - a_0) X_d^{\ast} / \mathrm{clip}(X;\ X_d^{\ast},\ 2.216 X_d^{\ast})$ with
$X_d^{\ast} = A \Xi_d e^{-0.3888}$, so $a$ runs from 0.375 to 0.718
(`economy/daily.rs:510-518`).

The step (`economy/daily.rs:1360-1432`):

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

**The crisis spike** (`market/tick.rs:316-334`):

```math
\chi_d = \min\big(0.98,\ (X_d - X_c)^{+} / 1.4\big)
```

It saturates at a VIX of about 32.3. On pt-v19 it acts only on news: a
sector peer's news spreads with weight $w_p (1 + 8\chi_d)$. The crisis blend
that once raised every company's market loading in a crisis is switched off
(`crisis_blend_gain` = 0): the data showed no crisis correlation beyond
what the higher common volatility already gives.

**The crisis episode and its epicentre** (`engine.rs:2461-2535`). An episode
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
inventory $I_i$ in shares (`microstructure.rs:314-369`).

### The quote

The maker quotes around the last print, moved by the tick's news term
(`market/tick.rs:1350-1355`):

```math
\hat P_{i,t} = P_{i,t-1}\,e^{N_{i,t}/390}
```

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

The model price reaches the tape through the direction of background order
flow. The buy probability rises with the gap between the model price and the
quote, and with the crowd's lean (`microstructure.rs:597-604`):

```math
\pi^{\mathrm{buy}}_{i,t} = \mathrm{clip}\Big(0.5 + 40\,\frac{P^{\ast}_{i,t} - \hat P_{i,t}}{\hat P_{i,t}} + 10\,L(s_{i,t}, \mu_{i,d});\ 0.05,\ 0.95\Big)
```

Four market orders of $\max(1, \lfloor V_{i,t}^{\mathrm{vol}}/4 \rfloor)$
shares each, where $V^{\mathrm{vol}}$ is the tick's volume below, walk the
book: order $j$ buys if $U_j < \pi^{\mathrm{buy}}$ and sells otherwise. The
print is the price of the last fill; if nothing fills, it is $P^{\ast}$
(`microstructure.rs:606-648`). When every slice fits in the top level, the
print is the ask with probability $\pi^{\mathrm{buy}}$ and the bid otherwise.
The maker's inventory takes the other side of every fill and never decays
(`microstructure.rs:484-501`).

The print is then clamped to the same ±25% session band as the model price:
$P_{i,t} = \mathrm{clip}(P_{i,t}^{\mathrm{set}};\ 0.75 P_{i,d}^{o},\ 1.25 P_{i,d}^{o})$
(`market/tick.rs:1308-1316`). The overnight gap is not clamped, and on
pt-v19 there is no overnight move: each session opens at the last print.

| Constant | Value | Kind |
|---|---|---|
| gap pressure | 40 | chosen |
| lean tilt | 10 | chosen |
| slices a tick | 4 | chosen |
| buy probability bounds | 0.05, 0.95 | guard |
| `price_breaker_fraction` | 0.25 | guard |
| `price_hard_cap` | 50,000 | guard |

## Agent orders

**Timescale:** an agent acts between steps; its orders fill at once, and
their effect on prices is spread over the following ticks.

**Execution.** An agent's market order is priced against a copy of the
book built at the last print, the maker's inventory and the day's VIX,
with ten levels (`engine.rs:4217-4228`). A buy of $x$ shares fills
$\min(x, \text{depth on the ask side})$ at the volume-weighted price of the
levels it walks; the rest is dropped and the fill is marked partial. The
fill changes no engine state: it takes no depth from the book, adds no
volume, and does not move the maker's inventory. The shipped reference
agents cap an order at 2% of average daily volume.

**Impact.** The filled shares enter the market as order flow, buys $x^{+}$
and sells $x^{-}$ in shares, which moves $s$ through the order-flow term
(`market/factors.rs:789-795`, `market/factors.rs:1200-1213`):

```math
O_{i,t} = \frac{x^{+} - x^{-}}{x^{+} + x^{-}}\,\max\Big(0.2,\ 0.15\min\Big(\frac{x^{+} + x^{-}}{\max(\bar A_i/390,\ 100)},\ 10\Big)\Big)\cdot\frac{c_{OF}\,f_I}{\max\big(\max(\bar A_i,\ 0.005\,S_i)/390,\ 100\big)}
```

The first factor is signed participation against a minute's average volume,
floored at 0.2 and capped at 1.5. The second is a price-impact coefficient
over a minute's volume. $O$ is a daily-equivalent shock, so a tick adds
$O/390$ to $s$, and that contribution then decays with $s$.

**At 0.8.1 the flow is held for a whole step.** Every harness passes an
agent's fills to the next `run_session` as order flow, and the session
applies the same flow on every tick (`python_engine.rs:1290-1297`,
`engine.rs:3692-3703`). At six steps a day, one order is applied 65 times,
after the agent has already filled at the pre-trade book. So the agent
collects its own impact rather than paying it. This is corrected in 0.8.5;
see [Coming in pt-v20](#coming-in-pt-v20).

| Symbol | Dial | Value | Kind | Source |
|---|---|---|---|---|
| $c_{OF}$ | `order_flow_coefficient` | 50 | chosen | reference implementation, set before the book existed to cover temporary and permanent impact together |
| $f_I$ | `informed_flow_fraction` | 0.35 | chosen | the permanent share of impact; the code cites published decompositions of 0.3 to 0.5 and names none |
| | participation floor, slope, cap | 0.2, 0.15, 10 | chosen | reference implementation |

## Volume

**Timescale:** tick volume every tick; two persistent states once a day.

Tick volume is a product of multipliers on a minute's share of average
daily volume (`market/tick.rs:1177-1275`):

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
stepped at each close (`engine.rs:3357-3369`):

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
| $r_V$ | `volume_move_response` | 1.0 | fitted | |
| $c_V$ | `volume_move_cap` | 12 | fitted | a sweep found 8, 12 and 20 alike |
| $n_V$ | `volume_move_noise` | 0.2 | chosen | reference implementation |

## Scenarios

A scenario is a file of changes to the economy or the market, applied once
a session, before the open (`python/tradefloor/scenario.py:1151-1190`). It
states its shocks and the knock-on effects it assumes, separately. The model
does not decide what a war or an oil shock does to the economy; the scenario
states it, and the model carries it to prices.

**Timescale:** once a session, before the open. Scenario day $d$ counts
sessions from the start of the run, or from the fork.

### Scenario operations

A **pin** gives a field an exogenous path $x_d = P(d)$
(`python/tradefloor/scenario.py:738-800`):

```math
\text{hold: } P(d) = v,
\qquad
\text{ramp: } P(d) = x_0 + (x_1 - x_0)\,\mathrm{clip}\Big(\frac{d - b}{o};\ 0,\ 1\Big),
\qquad
\text{step: } P(d) = \begin{cases} x_0 & d < a \\ x_1 & d \ge a \end{cases}
```

An **intervention** changes a field relative to its value $\tilde x_a$ on the
day $a$ it fires, by $\mathrm{op} \in \lbrace$ set, add, multiply $\rbrace$
(`python/tradefloor/interventions.py:1146-1154`,
`python/tradefloor/scenario.py:1261-1291`). With
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
overwrites the pinned field again (`python_engine.rs:2475-2511`). When the
pin ends, the economy carries on from the last value.

### Target channels

On pt-v19 (`python/tradefloor/interventions.py:468-689`):

| Target | Reaches prices through | Delay |
|---|---|---|
| `macro.corporate_yield` | the discount rate in fair value | same session |
| `macro.vix` | every variance process, jump rates, the crisis episode, the spread | same session to next |
| `market.liquidity` | average daily volume: book depth, impact, volume | same session |
| `macro.policy_rate` | the 10-year yield, then the credit floor or the next meeting | days |
| `macro.inflation` | nominal output, the 10-year term premium, the VIX target | next session on |
| `macro.growth` | nominal output | next session on |
| `macro.cycle` | the spread multiplier and the bank, at the next meeting | next meeting |
| `commodity.oil` | inflation, monthly | a month or more |
| `macro.unemployment` | the bank and the Phillips curve | a meeting or a month |
| `policy.tariff_rate` | inflation, monthly, very weakly | a month or more |
| `macro.qe_pe_boost` | nothing on pt-v19 | |
| `macro.fear_greed` | nothing | |

A **forced VIX** (`vix_sets_variance`) goes further: on a forced close the
market-factor variance is set to its targets, with no shock term
(`market/factor_vol.rs:926-956`):

```math
v^{f}_{d+1} = \mathrm{clip}(\bar v^{f}_d),\quad v^{s}_{d+1} = \mathrm{clip}(\bar v^{s}_d),\quad v_{d+1} = \mathrm{clip}\big((1 - w_s)\,v^{f}_{d+1} + w_s\,v^{s}_{d+1}\big)
```

A pinned VIX below the anchor calms the market: at a VIX of 15 on a roster
whose anchor is 20.6, the fast target is about half the baseline variance.

Six scenarios ship: `geopolitical_conflict`, `liquidity_crisis`,
`oil_price_spike`, `policy_regime_shift`, `rate_shock` and `recession`. Each
file records the effect size measured for its interventions; those
figures were measured before pt-v19 and have not been re-measured.

## Off in pt-v19

These mechanisms exist in the code and are switched off, or cannot act, on
pt-v19. Each dial is 0 unless stated. Earlier presets use some of them.

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

## Coming in pt-v20

pt-v19 is frozen. The changes below are being built for the next preset,
pt-v20, and the release that carries it. None of them is in pt-v19, and
their details may change before they ship, so this section says only what
each one is for. When pt-v20 ships, this document will specify it in the
same way.

- **Agent order flow applied once.** At 0.8.1 an agent's fills are held as order flow for every tick of the next step, after the agent has already filled at the pre-trade book (see [Agent orders](#agent-orders)). The fix applies a fill once, on the tick after it. Runs where no agent trades do not change. It ships in 0.8.5.
- **Quotes around the model price.** The maker quotes around the last print, so the traded price can lag the model price by tens of basis points, and hour-to-hour returns reverse more than real ones do (long-run criterion C4a). A new dial centres the book on the model price every tick.
- **A share of news in fair value.** Every company-level shock now lives in the mispricing, which reverts, so all company news is temporary. A new dial sends a share of each company's own shocks to a permanent fair-value level.
- **The opening mispricing.** A random roster opens with a spread of mispricing far wider than the model's stationary spread, so every run starts with a predictable drift back to fair value. A new dial opens it at the stationary spread.
- **Herding retune.** The herding gains, $\theta$ and the crowd's momentum gain, will be re-derived once the traded price tracks the model price.
- **Price for size.** The book shows ten levels, and the part of an order beyond them is dropped rather than charged. Deeper quotes will let a large order walk further and pay for it.
- **Bonds.** A tradable bond alongside the stocks.

## Known gaps

These are places where the code does something this document can state but
not defend, or where it could not state the code's behaviour as a clean
equation. They are listed so a reader can judge them.

**Openings.**

- The neutral rate $r^{\ast}$ = 0.0482 was read off pt-v18's burn-in, which always opened in expansion. pt-v19 opens at a random point in the cycle, and its opening corporate yield ranges from 2.6% to 6.6% across seeds (median 5.65%). So a roster does not open exactly at its fair value; the rate term shifts the opening mispricing by -0.04 to +0.03.
- The opening yield also depends on the roster: 2.9% for a one-company roster, 5.6% for 12 or 40 companies at the same seed. The likely path is the roster-derived VIX anchor acting through the burn-in; it has not been traced.
- The opening VIX is close to a fixed point of the burn-in, because the market is frozen during it, so every run on a roster opens at nearly the same VIX (20.11 on 28 of 30 seeds for `Universe.random(40, seed=111)`).
- After the burn-in the macro calendar restarts at day 1, so the first monthly step of a run comes 41 sessions after the last one of the burn-in.

**Macro.**

- The growth shock on entering a phase fires on the first two sessions of the phase, not one (`economy/daily.rs:625-650`). Whether that is intended is not recorded.
- Unemployment sits on its 2.5% floor for much of an expansion. Its long-run mean is 3.6% against 5.7% in the US.
- The cycle's monthly hazard is turned into a daily probability as $h/21$, an approximation to $1 - (1 - h)^{1/21}$ that runs 4% to 15% high. The opening draw uses the hazard alone, without the ladder, so it is close to stationary but not exactly so.
- Row 6 of the central bank's ladder can never fire, because inflation is capped at 6%. While the emergency condition holds, a meeting is held at every close.
- The QE terms apply once a meeting where the code's comments say once a day. On pt-v19 QE has no price effect, so this changes nothing measurable.

**Prices.**

- A jump is written to $s$ at the close and reaches the price at the next session's first ticks. So the index return the VIX reads on the jump's day does not contain it; it contains the previous day's. A jump beyond the ±25% band is partly removed by the breaker.
- The market leg's down-tick tilt is re-centred, but the lagged down-beta multiplies the tilted term, so a small drift of about $-a a_L \beta \sigma/\sqrt{2\pi}$ a tick remains on ticks where the market is down on the trailing window. It has not been measured.
- When the $\pm 0.9$ cap on $s$ binds, the change is not booked to any of the ten factors. When the 50,000 price cap binds, $s$ is not re-derived.
- The company GJR is fed the whole noise term, market and sector parts included, while its coefficients describe a company's own returns. The floor, not $\omega$, sets the resting level in 8 of 12 sectors.
- The buyback factor is re-evaluated each tick at the current price over all elapsed sessions rather than integrated along the path, and it uses the engine's global session count, so a company listed mid-run is credited with buybacks from before it existed, and no shares are retired.

**The VIX.**

- The index variance $V_d$ leaves out the crisis epicentre's scaling, so during an episode the VIX's read-back is not quite the variance the market realises. It also reads the lagged down-beta's condition once a session, where the market samples it every tick.

**Agents and the book.**

- An agent's fill never touches the maker's inventory or the book, so the part of impact the informed-flow share leaves out is not charged anywhere. Impact divides by a minute's volume twice, so it scales with depth squared rather than depth.
- The maker's inventory never decays; only opposing flow unwinds it.
- Tick volume reads the model price, not the print. Tape volume is the modelled volume, not the traded quantity, and agent trades add none; average daily volume is fixed for the run.

**Scenarios.**

- Four of the six shipped scenarios put a permanent change on the corporate yield, which freezes it: after that, no policy, inflation or cycle move reaches the discount rate.
- A hold on the corporate yield lasts until the next central-bank meeting, not to the end of its window, because nothing but the one-way floor moves the yield between meetings.
- The effect sizes the scenario files record were measured before pt-v19, and some docstrings describe an older VIX anchor, an older crisis threshold and the switched-off crisis blend.
- A pinned change of phase does not get the growth shock a natural phase change gets.
- `World.run` builds a fresh scenario on every call, so a hold or ramp added with `World.apply` loses its anchor between calls. `pin_macro(epicentre=None)` does not clear an epicentre pin.

**Records.** The provenance ledger labels `crisis_epicentre_end_sessions` as
out of scope, but pt-v19 reads it; and it labels `market_beta_down_asym_lag`
as measured, while its own entry says 0.46 is fitted. This document uses the
corrected kinds.

## Reproducing a result

The same version, preset, seed, roster and scenario give the same market on
every platform. Each release runs one fixed simulation on five platforms and
stops if any digest differs (`tests/known_answer.json`). The engine uses its
own `exp`, `log`, `pow`, `sin` and `cos`, so no system maths library can
change a result. A `RunManifest` records every input a run needs, and
`RunManifest.reproduce()` stops at the first mismatch.

To cite the model, name the version and the preset: "tradefloor 0.8.1,
preset pt-v19". See the README's "Citing tradefloor" section.
