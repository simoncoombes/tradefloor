---
title: Glossary
nav_order: 27
rack: reference
short: Glossary
---

# Glossary

The terms this project uses in a specific sense. Each gloss is sourced from the
page that owns the term and links to it, so a reader who meets one mid-page has
somewhere to go.

Where no page defines a term the entry reads `no owning page`, and the gloss is
written from the code instead. Those rows are collected at the foot of this
page: they are a list of things this documentation does not currently explain.


## The run

| term | what it means here | owning page |
|---|---|---|
| Universe | The roster of instruments, and its order is contractual, because the engine walks it in index order drawing random numbers as it goes, so a re-sorted universe gives a different market from the same seed. | [Core concepts](core-concepts.md) |
| Instrument | One tradable company -- ticker, sector and eight fundamentals -- whose `short_interest` is a share count, so a value strictly between 0 and 1 raises rather than being read as a fraction. | `no owning page` |
| Macro | Day-zero rates, inflation and the cycle, and the chain runs on from there endogenously, so `vix` takes 118 distinct values over a 120-day run. | [Core concepts](core-concepts.md) |
| seed | One integer, separate from the universe seed, and it does not identify a run on its own, because the market an agent trades depends on that agent's own orders. | [Reproducing a run](reproducing-a-run.md) |
| tick | One minute of game time, and a day is 390 regular-session ticks, so a hand loop that passes the same `hour, minute` 390 times simulates a different market. | [Running a simulation](running-a-simulation.md) |
| Engine | A whole market stepped through time, owning the seeded generator, the per-company price state, the economy and the central bank, and nothing beyond those four. | `no owning page` |
| order book | `engine.book(ticker)` returns a detached snapshot, so filling against it prices your execution at the levels you consume and leaves `prices()` byte-identical to the run that never traded. | [Running a simulation](running-a-simulation.md) |
| market maker | The counterparty on every book, quoting both sides and shifting both quotes toward reducing its inventory, so the book absorbs one-sided flow without running dry. | `no owning page` |
| ADV | Average daily volume, the denominator for participation, because 13.7 million shares is 0.05x a day's volume in one name and 407x in another. | `no owning page` |
| circuit breaker | When the model price leaves the session band the tick re-derives `mispricing_s` from the clamped price, so a factor sum that omits the `circuit_breaker` column misses that correction on any day the breaker binds. | [Ground truth](ground-truth.md) |
| Checkpoint and branch | Two ways to fork a run at one state: on a sixty-day, forty-instrument run `branch` copies engine state in under 1 ms and does not survive the process, `Checkpoint` replays the order log in 2.7 s and does. | [Forking a simulation](forking-a-simulation.md) |
| sweep | Streams one seed at a time, because a hundred recorded 252-day 100-instrument engines alive at once is roughly 110 GB against a little over 1 GB for one. | [Sweeps and parallelism](sweeps-and-parallelism.md) |
| order_flow | The channel that tells the market you traded, passed to `tick()` or `run_session()`, and pushing 500,000 AAA shares through it closes the name at 137.45 against 135.67 untouched. | [Running a simulation](running-a-simulation.md) |
| order log | `engine.order_log` holds every input the engine consumed, and an unknown entry raises on replay rather than being skipped, because a replay that ignored one would look like a success. | [Reproducing a run](reproducing-a-run.md) |
| close_market | The day's close bookkeeping -- momentum roll, GARCH, macro step -- and it advances the macro chain into the next day, so a row recorded after it carries the values the next day will trade under. | [Running a simulation](running-a-simulation.md) |

## Price formation

| term | what it means here | owning page |
|---|---|---|
| `fundamental_value` | What the company is worth on its fundamentals, with no mispricing in it. | [ground truth](ground-truth.md) |
| `anchor_price` | `fundamental_value` times `exp(mispricing_s)`, what the model wanted before the book touched it, so `close - anchor_price` isolates the microstructure. | [ground truth](ground-truth.md) |
| `mispricing_s` | Log deviation of price from fair value, and the nine factor columns sum to its change with a worst residual of 1.9e-16 over 39,000 rows. | [ground truth](ground-truth.md) |
| the nine factors | `reversion`, `momentum`, `crowd_lean`, `company_news`, `order_flow_impact`, `short_squeeze_effect`, `random_noise`, `circuit_breaker` and `jump` -- difference `mispricing_s`, add all nine, and you can check the label instead of trusting it. | [ground truth](ground-truth.md) |
| market factor | One normal drawn per tick that reaches every name through its beta, and without shared factors a 108-name index would have almost no aggregate volatility because independent noise cancels. | `no owning page` |
| sector factor | One normal per sector per tick that reaches each member through its sector loading, blending toward the market factor above VIX 25.5 so diversification stops working exactly when it is most wanted. | `no owning page` |
| idiosyncratic | The part of a name's return no other name shares, one normal per company per tick sized by that name's own GARCH variance; the market factor's variance share was raised out of this term's budget so total volatility held still. | `no owning page` |
| GARCH and GJR-GARCH | The per-name conditional variance recursion, `garch_alpha` on yesterday's squared return and `garch_beta` on yesterday's variance, plus a GJR `garch_gamma` that adds weight when that return was negative, because a symmetric GARCH squares the return and destroys its sign. | `no owning page` |
| `jump` | Applied to `mispricing_s` after the tick loop rather than inside it, so dropping it from the sum takes the worst residual from 1.9e-16 to 0.090 on a run carrying one jump row in 39,000. | [ground truth](ground-truth.md) |
| crowd lean | The crowd net-buys what trades below fair value and net-sells what trades above it, capped at 0.02 in log price so the crowd can never dominate a news day. | `no owning page` |
| order flow impact | The permanent, information share of order flow into `mispricing_s`, 0.35 of the total impact coefficient of 50.0, because the book already charges the temporary component when orders walk real depth. | `no owning page` |
| guards | The mispricing cap, the crowd lean cap, the session breaker and the price cap, settable and excluded from every calibration search because they are worst-case guarantees, plus `daily_shock_cap`, which is not settable at all. | [model presets](model-presets.md) |
| fair value | The level mean reversion pulls toward, repricing at the central bank's meeting calendar, so a run long enough to cross a meeting is not a stationary experiment. | [core concepts](core-concepts.md) |
| `random_noise` | The market component times the crash amplifier, plus the sector component, plus the idiosyncratic draw, summed into one column, so `truth` cannot separate the three. | `no owning page` |
| `circuit_breaker` | Books the rewrite when the model price leaves the session band and the tick re-derives `s` from the clamped price, and with no column for it the sum misses that correction on any day the breaker binds. | [ground truth](ground-truth.md) |

## Truth and provenance

| term | what it means here | owning page |
|---|---|---|
| truth table | One row per instrument per tick carrying `fundamental_value`, `anchor_price`, `mispricing_s` and nine factor columns; the nine sum to the change in `mispricing_s`, so you can verify the label instead of trusting it. | [ground truth](ground-truth.md) |
| residual | The gap between differenced `mispricing_s` and the sum of the nine factor columns, 1.9e-16 at worst over the 39,000 rows of a 20-name 5-day run, so anything larger means the join is wrong -- sorting `tick` without `day` takes that same run to 0.149. | [ground truth](ground-truth.md) |
| fingerprint (universe) | A sha256 over the roster's canonical form, order included, because a re-sorted universe is a different market: AAA closes day 5 at 143.03 in roster order and 134.88 reversed. | [core concepts](core-concepts.md) |
| fingerprint (model) | The first 8 hex characters of a sha256 over the preset's canonical serialisation, so any settable change reads `custom-7f290e34` rather than `pt-v1` and a changed preset cannot present as the shipped one. | [model presets](model-presets.md) |
| fingerprint (strategy) | A sha256 over the spec's canonical serialisation, unchanged by whitespace, key order or scaling every blend weight by two, and empty for a hand-built agent because that result is reproducible only by citing code at a commit. | [strategy specs](strategy-specs.md) |
| RNG substream | One of seven generators derived from the root seed -- market, economy, external, jumps, volume, news and per-name volume -- so a new mechanism on its own stream perturbs nothing, however it draws. | [RNG streams](rng-streams.md) |
| draw schedule | Market hours, the 390-tick day, the calendar and the sector key order, which nothing settable may change, because a preset changes what the draws are multiplied into and never the schedule. | [model presets](model-presets.md) |
| draw_delta | The market stream's draw-count difference between the two runs `compare()` puts side by side, zero across all twenty-eight comparisons on `pt-v12`, and non-zero means a halt, a delisting or a roster change moved the draw schedule and the two markets are structurally different. | [scenarios](scenarios.md) |
| draws_consumed | The total draws taken from three of the seven streams, `market`, `economy` and `external`; equal `market` counts between two runs of the same tick schedule mean both saw an identical noise sequence, which is the whole point of the diagnostic. | [RNG streams](rng-streams.md) |
| draws_by_stream() | Those three counts reported separately as `{"market": n, "economy": n, "external": n}`; the four later streams are not counted there and their positions read from `state_snapshot()["rng"]` instead. | [RNG streams](rng-streams.md) |
| common random numbers | Running one seed under two presets so `draws_consumed` comes out identical, which makes every difference in the outcome a parameter effect rather than reshuffled noise. | [model presets](model-presets.md) |
| known-answer test | One fixed simulation run inside each of five wheel targets and hashed, compared target against target and against the `sha256` committed in `tests/known_answer.json`, so a disagreement stops the upload rather than merely reporting one. | [reproducing a run](reproducing-a-run.md) |
| RunManifest | The five things that identify a run as one object, embedding the roster, the macro initial conditions, the realised scenario path, the order log and the strategy; `reproduce()` raises with the disagreeing component named, because every component travels with its own fingerprint rather than one hash over the file. | [sharing a run](sharing-a-run.md) |
| order log | `engine.order_log`, every input the engine consumed as JSON-serialisable dicts and the only one of the five run identifiers it carries; an unknown operation raises on replay, because skipping one would produce a market the log does not describe and it would look like a success. | [reproducing a run](reproducing-a-run.md) |
| era boundary | A change that moves every seeded trajectory, caught by a probe digest rather than by a version string, because one calendar day brought three trajectory-changing fixes while `pt.version()` stayed 0.1.0 and the preset stayed `pt-v1`. | [sharing a run](sharing-a-run.md) |
| era_fingerprint() | The digest of a small fixed probe simulation, recomputed before every replay, so two builds that agree on the probe agree on the arithmetic it exercises and the manifest refuses by name when they do not. | [sharing a run](sharing-a-run.md) |

## Model configuration

| term | what it means here | owning page |
|---|---|---|
| `ModelParams` | The model's coefficients as one immutable value, built by `ModelParams.from_preset("pt-v1", garch_alpha=0.12)` and fingerprinted as the first 8 hex characters of a sha256 over sorted names and raw IEEE-754 bits, so a modified model reads `custom-7f290e34`, never `pt-v1`. | [model presets](model-presets.md) |
| preset | The model's complete coefficient set, frozen under one versioned name; fourteen ship, `pt-v14` is the default, and every earlier name stays selectable and bit-reproducing forever, because if every user ran a bespoke set no two published results would be comparable. | [model presets](model-presets.md) |
| settable surface | The 87 names at 0.3.0 that `ModelParams.settable()` lists and an override may change; nothing on it may change how many draws are taken or in what order, so two presets run on one seed consume identical draws and every difference in the outcome is a parameter effect. | [model presets](model-presets.md) |
| derived coefficient | `mispricing_phi` and `s_phi_tick`, the two coefficients carried as recorded bit patterns and refused as a direct override; overriding `mispricing_half_life_days` recomputes both, deterministically on a given build but not bit-identically to any recorded constant. | [model presets](model-presets.md) |
| compile-time constant | One of the 28 preset entries visible in `ModelParams.to_dict()` and covered by the fingerprint but refused as an override, because accepting an override the engine would ignore would make the fingerprint a lie. | [model presets](model-presets.md) |
| promotion | Moving a coefficient off its compiled literal onto the settable surface, the way `volume_move_cap` came off its `4.0` in `tick.rs` for `pt-v12`; `settable()` went from 70 names at 0.2.0 to 87 at 0.3.0, and every earlier preset still runs the literal, which is why they replay unchanged. | `no owning page` |
| `model_preset()` | Returns the nine-key mispricing and crowd dictionary for the preset in force, `pt-v14` by default; the GARCH parameters and the factor sigmas are live and none of them appear, so quote `pt.version()` beside the name because the version is what pins the build. | [conventions](conventions.md) |
| `ModelParams.from_preset()` | Builds a `ModelParams` from a shipped preset plus keyword overrides, `from_preset("pt-v1", garch_alpha=0.12)`; an unknown or non-settable key is refused by name, and the refusal prints the whole settable surface beside it. | [model presets](model-presets.md) |

## Evaluation

| term | what it means here | owning page |
|---|---|---|
| agent | Sees prices, the order book and its own positions, and never fair value, mispricing or the attribution, because inferring those is the task and they are used for scoring on the other side of the wall. | [agents and evaluation](agents-and-evaluation.md) |
| `StrategySpec` | A strategy as data, declarative, versioned and hashable, whose sha256 fingerprint a methods section cites; a hand-written Python agent leaves `strategy_fingerprint` empty and its result is reproducible only by citing code at a commit. | [strategy specs](strategy-specs.md) |
| `Scorecard` | One agent's result -- `pnl`, `return_pct`, `trades`, `turnover`, `impact_bps`, `max_leverage`, `explanation_accuracy` -- carrying the seed and the universe, strategy and model fingerprints, because the seed alone does not identify a market and a leaderboard without them cannot be re-run. | `no owning page` |
| Oracle | Reads the true mispricing and trades it without estimation error, on the same gross exposure and 2%-of-ADV participation cap every other baseline gets, spending them on a naive equal-weight rule at `top_k=5` per side that mean-reversion out-earns on 5 of 12 markets. | [agents and evaluation](agents-and-evaluation.md) |
| capture ratio | Each agent's P&L as a fraction of the Oracle's; above 1.0 is legal and does occur, mean-reversion clearing it on 5 of 12 markets, and the Oracle's configuration and the horizon both move the denominator -- the same agent on the same market scores 0.066 at five days and 1.18 at sixty -- so quote both with any ratio. | [agents and evaluation](agents-and-evaluation.md) |
| paired sign test | Counts the seeds on which one agent beat another in the same market and sets `decisive` only on a clean sweep, so twelve paired seeds put a 9-3 win at p = 0.15 and cannot report better than p = 0.0005 however wide the gap gets. | [agents and evaluation](agents-and-evaluation.md) |
| pooling | The headline capture sums P&Ls and Oracle P&Ls across seeds rather than averaging per-seed ratios, because a $13.7k three-day denominator produced a +1.57 ratio and averaging the ten ratios instead of pooling them moves the verdict from +0.78 to +0.88. | [agents and evaluation](agents-and-evaluation.md) |
| `impact_bps` | Compares the traded run's closing prices against the same seed with nobody trading; fills feed back into the price process, so the gap compounds with the horizon -- the oracle's sign holds on 12 of 12 seeds at two days and 5 of 12 at twenty -- and the number is worth reading over a day or two and across seeds. | [an LLM agent](an-llm-agent.md) |
| `explanation_accuracy` | Scores the factor an agent's `explain(day)` names against the day's dominant factor, summed in absolute value across the roster, which `random_noise` wins on 240 of 240 scored days, so an agent that trades nothing and answers `random_noise` every day scores 100%. | [an LLM agent](an-llm-agent.md) |
| TCA and shortfall | Runs the same seed with and without your orders and prices every fill against the world where you never traded, signed so positive is a cost, and a round trip can come back negative -- between -17.72 and +2.03 bps across eight sim seeds -- because the exit sells into the impact the entry left. | [transaction cost analysis](transaction-cost-analysis.md) |
| `partial_fills()` | Lists the fills the book truncated at its displayed depth -- requests of 4,856, 9,713 and 48,563 shares all filled 483 on sim seed 2026 -- so read it before believing a low cost. | [transaction cost analysis](transaction-cost-analysis.md) |
| `TradingEnv` | The Gymnasium environment, which passes gymnasium's `env_checker`, takes actions as target weights in `[-1, 1]` so a policy does not have to learn each instrument's price range first, and pays reward as the step's P&L after the market moves, which includes the cost of the agent's own footprint. | [reinforcement learning](reinforcement-learning.md) |
| cadence | How often a strategy re-decides, `"step"` or `"daily"`, and it sits in the spec because the same one-day signal returns +37.55% at three decisions a day, +9.79% at six and -27.46% at twelve. | [strategy specs](strategy-specs.md) |
| `rank` | Scores agents over many seeds and ranks them on the pooled capture, taking a factory rather than built agents, because agents are stateful and reusing one carries a market's history into the next with no visible symptom. | [agents and evaluation](agents-and-evaluation.md) |
| `privileged` | Set `True` on an agent built from a spec naming `oracle` or from any blend containing one, on the built agent rather than on the spec, so a results table can label the row that read state no real trader has. | [strategy specs](strategy-specs.md) |
| `unmeasurable` | The seeds a ranking could not measure capture on because the Oracle lost money there, excluded from both the pooled numerator and the denominator since a negative denominator flips the sign of everything above it, and reported rather than dropped. | `no owning page` |
| rebalance cadence | How often a strategy re-decides, and part of its identity rather than a run setting: the same one-day signal returns +37.55% at three decisions a day, +9.79% at six and -27.46% at twelve, so two runs of one fingerprint could differ by the cadence alone if the spec left it out. | [Strategy specs](strategy-specs.md) |

## Realism

| term | what it means here | owning page |
|---|---|---|
| the panel | The fourteen statistics `pretium.facts.measure()` returns, measured at a single flat VIX on one horizon, so a preset can be perfect on all fourteen while the market's response to a crisis changes underneath it. | [realism metrics](realism-metrics.md) |
| band | The range a statistic must land in, measured with the panel's own estimators on real data: nine consecutive 252-day windows of 40 US large caps over 2015-2025 set each one, and the tenth is held out because it straddles the COVID crash. | [realism metrics](realism-metrics.md) |
| crisis_window | The tenth of those ten windows, reported beside each band rather than setting it, because the panel is a claim about a typical year and crisis behaviour is measured under pinned scenarios. | [realism metrics](realism-metrics.md) |
| SEED_SD | Each statistic's across-seed standard deviation at the shipped baseline, and the unit every band exit is priced in, so a miss on volatility measured in tens and a miss on an autocorrelation measured in hundredths are comparable. | [realism metrics](realism-metrics.md) |
| L_real | The band-distance loss a calibration search minimises, `sum over k of (d_k / s_k)^2` across nine of the fourteen, zero anywhere inside a band because there is no credit for sitting in the middle of one. | [realism metrics](realism-metrics.md) |
| live target | One of the five statistics the calibration search is trying to move into band, and the role `band_distance_loss` reports on their rows; appending a key to `LIVE_TARGETS` is the one edit that promotes a structural statistic into the loss. | `no owning page` |
| constraint | One of the four statistics already in band at the baseline, contributing zero loss where they stand and pushing back only when a candidate drives them out, because a calibration that fixed correlation by breaking kurtosis would trade a documented gap for a new one. | `no owning page` |
| structural | The five statistics banded, measured and given a verdict but contributing zero to `L_real`, because no lever has been shown to move them cleanly; membership states the calibration objective rather than reachability, and the shipped `pt-v14` holds all five in band at the certified horizon. | [realism metrics](realism-metrics.md) |
| dual_horizon_loss | `L_real` at 252 days plus `L_real` at 504 days, each against its own bands and its own noise scale, because three consecutive calibration searches bought 252-day realism by spending 504-day realism and the objective could not see the trade. | [realism metrics](realism-metrics.md) |
| sd_out | A statistic's band distance in units of its across-seed noise, which is what makes band widths comparable across the panel: `sector_excess_corr`'s 0.12-wide band is 17.6 across-seed standard deviations, the second most forgiving of the fourteen. | [realism metrics](realism-metrics.md) |
| intervals() and typical_straddles | `envelope.intervals()` reports each statistic's p10, p90 and across-seed sd beside its median, and `typical_straddles` fires when the p10-to-p90 range crosses a band edge: nine of the fourteen did so on `pt-v10` over thirty seeds, so one seed can read out of band on a median that sits inside. | [realism metrics](realism-metrics.md) |
| decay curve | The model's absolute-return autocorrelation at ten lags out to 60, fitted as a log-log slope over lags 1 to 20 because no single lag reveals a shape: real markets fit -0.436 and the model -0.953, so its volatility memory fades about 2.2 times as fast. | [realism metrics](realism-metrics.md) |
| VIX lever | The ratio of realised volatility at a high held VIX to a low one, so how much more violent a sustained crisis is than a calm market; real markets read x6.16, and it is a gate rather than a calibration target because the panel cannot see it. | [realism metrics](realism-metrics.md) |
| VIX shock | The ratio of realised volatility during a 20-day VIX spike to a flat baseline, so how fast the market reacts as distinct from where it settles: a variance process with a long half-life reaches the right level for a sustained crisis and cannot track a short spike. | [realism metrics](realism-metrics.md) |
| envelope | What pretium certifies and what it does not -- the statistics matched, the horizon, the axes the claim survives and the five measured gaps -- published instead of a single realism score, because one number hides the structure that decides whether a result means anything. | [realism envelope](realism-envelope.md) |
| certified horizon | 252 trading days, the horizon the certification was measured at on thirty seeds, and `envelope.check` refuses to certify beyond it even though `pt-v12` holds all fourteen against bands re-derived at 504 days. | [realism envelope](realism-envelope.md) |
| Gap | One measured way the model departs from real markets, each ending in a rule about what it forbids you to conclude: five ship with `pt-v12`, down from six at that boundary and eight at 0.1.4, and the three that closed are recorded rather than deleted. | [realism envelope](realism-envelope.md) |
| check() | `envelope.check()` answers whether a question falls inside the envelope and returns a boolean with its reasons attached, and it raises on an unknown statistic name because a silently dropped name is a silently granted certification. | [realism metrics](realism-metrics.md) |
| validation axes | The four axes every calibration certificate reports `L_real` on -- training seeds, held-out seeds, held-out universe, held-out horizon -- because a model fitted to thirty seeds and reported on those same thirty seeds has demonstrated nothing. | [realism metrics](realism-metrics.md) |
| skew | The skewness of the pooled standardised daily returns, returned by `measure()` on every panel and given no band, so the model could carry the wrong sign on it indefinitely and no verdict would say so. | [realism metrics](realism-metrics.md) |
| MEMORY_VALID_TO_LAG | Lag 20, the line in code past which the model's volatility memory runs wrong in sign, which puts vol-targeting and risk-parity overlays reading a one-month or longer volatility estimate outside the envelope. | [realism envelope](realism-envelope.md) |

## Calibration and Atlas

| term | what it means here | owning page |
|---|---|---|
| Atlas | Samples the simulator's parameter space, measures whatever you care about at every point, and describes the response surface, because six consecutive calibration searches were rejected for selling whatever a scalar objective could not see. | [Atlas](atlas.md) |
| Axis | One parameter and the range the survey moves it over; `log=True` samples the log of the value and refuses a low of zero, because zero is infinitely far away in log space. | `no owning page` |
| box | The range an axis sweeps, by default a quarter to four times the preset's shipped value; a 96-core search once concluded there was nothing to find because the best known value sat just outside that ceiling. | [Atlas](atlas.md) |
| Latin hypercube | Stratifies each axis independently, so every parameter is sampled evenly across its range however many others vary, which is what makes a few thousand points informative in fifty dimensions rather than merely scattered. | [Atlas](atlas.md) |
| `plan()` | Returns the vectors a survey will measure without measuring them, so you can check them before spending anything, because a sampled vector can easily sit outside the region your model is stationary over. | [Atlas](atlas.md) |
| screening resolution | The few seeds per point a survey runs at, chosen so the map is affordable; it is enough to rank and describe, and anything it recommends still needs `confirm` at full resolution. | [Atlas](atlas.md) |
| `sensitivity` | Ranks each parameter by rank correlation against one output; a near-zero sensitivity is not proof of inertness, because a sensitivity is one axis at a time and a mechanism that needs two parameters is structurally invisible to it. | [Atlas](atlas.md) |
| `profile` | Bins one output against one parameter to give the shape of the effect, monotone or an interior optimum; read the bin counts, because a filter that leaves too few rows answers a noisier question. | [Atlas](atlas.md) |
| `unidentified` | The parameters that move nothing at all across the named outputs; the reading is no monotone effect over the sampled ranges at this resolution, so it deprioritises a parameter in a search and never retires a mechanism. | [Atlas](atlas.md) |
| Pareto front | Shows every available trade at the same time and makes a human choose, because whatever sits outside a scalar objective is free and the optimiser sells it silently. | [Atlas](atlas.md) |
| `attribution` | Decomposes the difference between two vectors across the parameters that differ, assuming approximate additivity; on a purely multiplicative surface it can overstate a contribution several-fold, so pass `measured=` and read the residual. | [Atlas](atlas.md) |
| `confirm()` | Re-measures a candidate at full resolution on seed blocks disjoint from the survey's and reports the paired difference in each block, because a candidate declared shippable on a +0.1297 gap read -0.0315 on fresh seeds. | [Atlas](atlas.md) |
| emptiness certificate | Earns or refuses the strong negative claim that no parameter vector in the named box reaches the named targets jointly, with the residual saying by how far, and it stays a numerical minimum over a bounded box under a named budget, not a proof. | `no owning page` |
| calibration certificate | The record a calibration run writes, carrying `L_real` on the four validation axes and the overfitting rule as text beside its verdict; the per-statistic breakdown stays because one certificate reads a training `L_real` of 0.0000 and is still rejected on two flips. | [realism metrics](realism-metrics.md) |
| the overfitting control | Rejects a candidate on either of two counts, a trained-to statistic in band on the training seeds and out of band on a validation axis, or a validation `L_real` exceeding the training `L_real` by more than twice its bootstrap spread across seeds; the first clause is the one that actually fires. | [realism metrics](realism-metrics.md) |
| gate | Everything a candidate preset has to pass in one run: thirty-seed panels at 252 and 504 days, a held VIX 45 crisis state, six held-out seeds and a held-out 60-name universe, then the response instrument against the base. | `no owning page` |
| `emit_preset` | Turns a calibration certificate into the `const fn` body for `rust/src/params.rs` and a bit-pattern assertion beside it, because the vector a search found and the vector a build ships have to be the same sixty-four bits and somebody retyping a number off a report is how that breaks. | `no owning page` |
| `survey` | The measured table `atlas.survey` returns, one row per planned vector; a vector whose measurement raises is recorded and skipped rather than fatal, because a region that breaks the model is a fact about the model. | [Atlas](atlas.md) |
| `where=` | Restricts a `sensitivity` or `profile` to a region of the other parameters; conditioning the 4,000-vector survey on `decay > 0.9` left about 380 rows whose bin medians swung three times wider than the effect, so check the counts. | [Atlas](atlas.md) |
| validation axes | The four axes every calibration certificate reports `L_real` on: training seeds, held-out seeds, held-out universe, held-out horizon; only the first is what the search optimised. | [realism metrics](realism-metrics.md) |
| trained-to | The nine statistics in the loss, not all fourteen; the overfitting control's first clause fires on one of those in band on the training seeds and out of band on a validation axis. | [realism metrics](realism-metrics.md) |

## Scenarios

| term | what it means here | owning page |
|---|---|---|
| Scenario | A macro path an agent holds positions through: the rate walking from 2.5% to 5% over fifteen days costs buy-and-hold 2.56 points against the same market with no scenario. | [Scenarios](scenarios.md) |
| `pin_macro` | Overrides the endogenous step for the named field from the day it lands: pin `vix=30.0` once after day 3 and day 3 reads 30.0 while the four days that follow read 22.75, 23.62, 23.89 and 19.30, because the chain carries on from the pinned value rather than freezing it. | [Core concepts](core-concepts.md) |
| endogenous versus driven | The macro chain evolves on its own from the day-zero state unless a scenario drives a path through it, and endogenous inflation peaks at 4.0% on every one of thirty seeds against real CPI's 9.0% in June 2022, so an inflation regime or a policy crisis has to be driven through a scenario. | [The realism envelope](realism-envelope.md) |
| the meeting-calendar trap | A policy-only rate path moves every price by exactly 0.00% over 40 days and a median -4.00% once a 60-day run crosses the first central-bank meeting at day 45, because equities discount off the corporate bond yield and only a meeting recomputes it, so a short policy-only study sees nothing, silently. | [Scenarios](scenarios.md) |
| scenario response | The two gate metrics from `tools/calibration/scenario_response.py`, the steady-state VIX lever and the transient 20-day VIX shock, are invisible to the fourteen-statistic panel, because the panel is measured at a single flat VIX on one horizon and a vector can be perfect on all fourteen while the market's response to a crisis changes underneath it. | [The realism metrics](realism-metrics.md) |
| `draw_delta` | The market stream's draw-count difference between a scenario run and its baseline, reported by `compare()` rather than asserted zero, because a non-zero delta means the scenario changed the market's own draw schedule and the result compares two structurally different markets; measured at zero across all twenty-eight comparisons on `pt-v12`. | [Scenarios](scenarios.md) |
| `compare()` | Runs a scenario against a baseline defaulting to `Scenario().hold(**scenario.at(0))`, the scenario's own day-zero values held flat, and refuses a scenario that never moves inside the horizon because every instrument would come back at exactly 0.00% by construction rather than by measurement. | [Scenario recipes](scenario-recipes.md) |

## Terms no page owns

22 of the terms above are defined nowhere in `docs/`. Each is glossed above from the code, and each is a paragraph some page is missing.

| term | where the definition actually lives |
|---|---|
| `Instrument` | no docs/*.md defines it. core-concepts.md names it once inside a code comment (`pt.Universe([pt.Instrument(...), ...])`, line 46) and reproducing-a-ru. |
| `Engine` | no docs/*.md says what an Engine is. core-concepts.md has `### Universe` and `### Macro` sections but no `### Engine` section, despite constructing on. |
| `market maker` | the phrase appears in no docs/*.md at all. |
| `ADV` | used in three docs pages as an established term and expanded in none of them. transaction-cost-analysis.md:26 and :45 quote a name's ADV as 9,713 shar. |
| `market factor` | defined only in rust/src/market/factors.rs (the `SharedFactors` doc comment: drawn once per tick, reaches every name through beta, without it a 108-na. |
| `sector factor` | defined only in rust/src/market/tick.rs lines 507-533 (one normal per sector key per tick, blended toward the market factor above `crisis_vix_threshol. |
| `idiosyncratic` | defined only in rust/src/market/factors.rs lines 372-380 (`idiosyncratic_sigma = sqrt(garch_variance) * idio_sigma_scale / sqrt(390)`, one draw per co. |
| `GARCH and GJR-GARCH` | defined only in the module header of rust/src/market/garch.rs (alpha = yesterday's surprise, beta = persistence, gamma = extra weight on a negative su. |
| `crowd lean` | defined only in rust/src/mispricing.rs lines 90-96 and 159-179 (`crowd_lean_with`, the restoring force plus a smaller herding term, clamped to `crowd_. |
| `order flow impact` | defined only in rust/src/market/factors.rs (`ORDER_FLOW_COEFFICIENT` 50.0, `INFORMED_FLOW_FRACTION` 0.35 with the argument that the book now charges t. |
| `truth table` | the exact phrase appears in NO docs/*.md file. |
| `known-answer test` | named in docs/model-presets.md:200 and docs/reading-results.md:35 but defined on neither. docs/reproducing-a-run.md describes the mechanism in full (f. |
| `era boundary` | used on eight docs pages (conventions, model-presets, scenarios, realism-envelope, rng-streams, sharing-a-run, atlas) and defined on none of them. |
| `promotion` | no docs/*.md page defines it. |
| `Scorecard` | defined only in code, at python/pretium/harness.py:222, docstring "One agent's result", with the field list in `__slots__` at line 231 and the fingerp. |
| `unmeasurable` | defined only in code, at python/pretium/ranking.py:227-231 ("Seeds where capture was not measurable because the reference did not make money") and enf. |
| `live target` | defined only in python/pretium/loss.py: the module docstring at line 31 ("the five statistics the search is trying to move into band"), the LIVE_TARGE. |
| `constraint` | defined only in python/pretium/loss.py: the module docstring at line 34 ("the four statistics in band at the baseline ... push back only when a candid. |
| `Axis` | the type is defined only at python/pretium/atlas.py:142, `class Axis: "One parameter and the range the survey moves it over."`, with the log-axis refu. |
| `emptiness certificate` | defined only at tools/calibration/falsify.py:1-40 (module docstring, CALIBRATION.md 4.4). |
| `gate` | defined only at tools/calibration/gate_pick.py:1-13 (the thirty-seed battery for one candidate) and gate_batch.py:1-34 (the same axes for many candida. |
| `emit_preset` | defined only at tools/calibration/emit_preset.py:1-20. |
