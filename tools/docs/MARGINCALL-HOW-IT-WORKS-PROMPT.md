# Prompt: a "How it works" page for margincall.io

Hand this to the agent that manages the margincall.io site. Everything under
"Verified facts" is measured or checked in the pretium repository and can be
stated as fact. Everything else is yours to fill in or cut.

---

## The task

Add a **How it works** page, linked from the margincall.io home page. It has
two jobs, in this order:

1. Explain how margincall.io works, specifically that the market you trade
   against is a simulation with a published, checkable engine underneath it.
2. Introduce that engine, pretium, as its own thing: what it is, who else
   might want it, and where to get it.

The reader arriving from the home page is a margincall.io user asking "is
this market real, or is it made up?" The honest answer is the interesting
one: it is made up, deliberately and carefully, and here is exactly how
made up, measured against real markets and published.

A second reader arrives later: a quant, a researcher, or someone building
trading agents, who does not care about the game and wants the engine. Do
not make that person read the game pitch first. Give them a clear route out
to the repository and the documentation near the top.

## Structure

Something like this. Adjust to the site, but keep the order: what you trade
against, why it behaves like a market, what it is honest about, then where
the engine lives.

- **A short opening.** One paragraph. The market is simulated, the engine is
  open source, and the whole thing is reproducible from a seed.
- **What happens when you place an order.** The order book, price-time
  priority, partial fills, and the fact that your own order moves the price.
  This is the part users feel and do not have words for.
- **Where the prices come from.** A real factor structure, a mispricing
  process that mean-reverts toward a computable fair value, and a macro
  economy that advances daily. Not a random walk with a chart on top.
- **How real is it.** The realism envelope. Give the numbers below. This
  section earns the page its credibility, so do not soften it.
- **The engine, separately.** pretium: what it is, the install line, the
  links. Say plainly that margincall.io is one thing built on it and that
  the engine is useful on its own.
- **A closing line back to the product.** Whatever the site's usual call to
  action is.

## Verified facts about pretium

Use these numbers exactly. They are measured, and several were expensive to
establish. Do not round them, do not soften them, and do not add numbers
that are not here.

**What it is.** A market simulator with a Rust core and a Python API.
Published on PyPI as `pretium`, `pip install pretium`. Dual licensed MIT or
Apache-2.0. It simulates prices, a limit order book with price-time priority
and partial fills, and a macro economy advancing daily under a five-phase
business cycle.

**Determinism.** The same seed produces the same market. That is checked on
every release rather than asserted: five platforms (linux-x86_64,
linux-aarch64, macos-arm64, macos-x86_64, windows-x86_64) each build a
wheel, run one fixed simulation inside it and compare digests. A
disagreement fails the release. The current digest is
`5bd011be292f823ce1c360d1a12bf46de3362deee058a37283c74ab47069d0c1`.

**It runs in a browser.** There is a WebAssembly build, and it produces the
same numbers as the native one because both call the same core function
rather than reimplementing the day loop. That matters for margincall.io
specifically: the engine can run client side without becoming a second
model that quietly disagrees with the server.

**Realism, stated rather than scored.** Ten statistics are measured against
bands derived from real US large-cap data. At the certified horizon of 252
trading days the shipped `pt-v3` preset holds nine of the ten inside their
bands, and holds the same nine on random seeds and a roster of companies the
calibration never saw.

**The tenth one misses, and is published anyway.** The autocorrelation of
volume changes sits 13.7 seed-standard-deviations outside its band. It is
excluded from the calibration objective on purpose, because pointing an
optimiser at a target it cannot reach does not fail cleanly, it distorts
every other parameter chasing it. This is the single most credibility-
building fact on the page. Do not bury it.

**Six further gaps are named**, each with what it stops you concluding:
the volume-change miss above; the 252-day horizon, beyond which the model is
not certified; volatility memory that decays exponentially where real
markets decay hyperbolically; tails too thin over multi-year windows;
scenario response that is directionally right but not calibrated in
magnitude; and certification measured on a sector-balanced roster, which no
real index is. The library will refuse to certify a question that falls
outside these rather than answering it anyway.

**Ground truth you cannot get from history.** Because the simulator computed
every price, it can say why one moved: seven factor contributions per
instrument per tick that sum to the move. No historical dataset carries that
labelling. You can see that a stock fell; you can never see that 60 percent
of the fall was order-flow pressure.

**Counterfactuals.** The same seed can be run twice, once with your orders
and once without, so every fill is priced against the market where you never
traded. That is the number transaction-cost analysis vendors approximate.

**Model versions.** Coefficients ship as named, frozen presets. `pt-v3` is
the default. `pt-v1`, `pt-v2` and `pt-v4` are all still selectable and
reproduce exactly what they always did. A change to any coefficient arrives
as a new preset rather than an edit to an existing one, because a market
that runs differently from the same seed would invalidate every published
result that cited it.

**Agent access.** An optional MCP server exposes eleven read-only tools, so
a coding agent can drive the simulator through tool calls instead of Python.

## Links

- Repository: https://github.com/simoncoombes/pretium
- Documentation: https://simoncoombes.github.io/pretium/
- Package: https://pypi.org/project/pretium/

## What you must not claim

These are not stylistic preferences. Each one is a claim the project has
deliberately refused to make, and the page should not make it on their
behalf.

- **Do not say pretium predicts real markets, or that it is trained on real
  data.** It is calibrated so its statistics fall inside bands measured from
  real markets. That is a different and much weaker claim.
- **Do not imply a strategy that does well here will do well with real
  money.** The price process comes from a known model, so a strategy that
  fits that model's structure will look excellent and teach you nothing. A
  strategy that fails here is the more informative result.
- **Do not present realism as a score or a percentage.** There is no "87
  percent realistic" number and the library deliberately refuses to emit
  one, because a single number reliably becomes the thing people cite
  instead of the gaps, and the gaps decide whether a result means anything.
- **Do not describe the simulated market as real, live, or historical.**
- **Do not invent figures.** If a number is not in this document, leave it
  out or ask.

## Voice

Write it the way a person would explain it to a colleague who is smart but
has not seen it before.

- **No em dashes or en dashes.** Use a comma, a colon, a full stop or a
  rewritten sentence. This is a hard rule across the whole project.
- **No marketing register.** No "revolutionary", "powerful", "seamless",
  "unlock", "cutting-edge". No exclamation marks.
- **Avoid the "not X, but Y" construction.** It reads as generated. State
  the thing directly.
- **Short paragraphs, concrete nouns.** Prefer "your order eats the book and
  the price moves" over "sophisticated market microstructure modelling".
- **Let the honest bits be honest.** The gaps section is the most persuasive
  part of the page precisely because it does not sell. Resist the urge to
  add a reassuring sentence after it.

## What I need from you

I have not written the margincall.io side, because that is yours. Fill in or
tell me:

- What margincall.io is, in one sentence, for readers who arrived cold.
- Whether the engine runs server side, client side via WebAssembly, or both.
- Which preset the platform runs, and whether it is pinned.
- Whether users can see their own seed or run number, and whether you want
  the page to say results are reproducible on request.
- Whether to mention that pretium was extracted from margincall.io's own
  TypeScript engine and rewritten in Rust. It is a genuinely good origin
  story and it explains why the engine is unusually well specified, but only
  say it if you want that history public.
