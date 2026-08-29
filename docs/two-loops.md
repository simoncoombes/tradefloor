---
title: The two loops
nav_order: 3
rack: start
short: Two loops
---

# The two loops

"Training" means two different things in this project. One trains the market
model. The other trains a strategy against a market that already exists. They
use different seeds, answer to different validation, and only one of them
produces something you cite.

Most users only need the second. [Atlas](atlas.md) says so on its own page:
you do not need it to run a simulation or test a strategy, and most users
never go further.

## Loop A, calibrating the market model

Fitting the simulator's coefficients so the synthetic market reproduces
real-market statistics.

| | |
|---|---|
| what "training" means | the calibration seeds a search or survey measures on |
| what "validation" means | held-out seeds, a held-out universe, a held-out horizon |
| what comes out | a named preset, `pt-v12` on the shipped default |
| where Atlas lives | here |

The output is frozen. A preset is a complete, named set of coefficients, and
changing any of them produces a different name, because a result recorded
under one name has to replay under that name.

## Loop B, evaluating a strategy or agent

Running a strategy, a reinforcement-learning policy or an LLM agent against a
market that already exists.

| | |
|---|---|
| what "training" means | training your policy against the Gymnasium environment, `tradefloor.gym.TradingEnv` |
| what "validation" means | ranking across seeds with a paired sign test |
| the preset | a fixed input you cite, never something you tune |

## The handover runs one way

Loop A ends at a frozen, fingerprinted preset. Loop B names that preset in its
result. Nothing travels back: a strategy result never tunes the market model,
because tuning the market on a strategy's performance bakes that strategy's
edge into the world it is then measured in.

<figure style="margin:24px 0">
<svg viewBox="0 0 880 320" role="img" width="100%"
     aria-label="Loop A calibrates the market model and ends at a frozen preset; the preset and its realism envelope are inputs to Loop B, which evaluates a strategy. No arrow returns from Loop B to Loop A."
     style="max-width:880px;color:currentColor">
  <defs>
    <marker id="tl-arrow" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor"/>
    </marker>
  </defs>
  <g fill="none" stroke="currentColor" stroke-width="1.2" opacity="0.85">
    <rect x="12" y="26" width="150" height="46" rx="6"/>
    <rect x="212" y="26" width="150" height="46" rx="6"/>
    <rect x="412" y="26" width="150" height="46" rx="6"/>
    <rect x="612" y="26" width="150" height="46" rx="6"/>
    <rect x="12" y="248" width="150" height="46" rx="6"/>
    <rect x="212" y="248" width="150" height="46" rx="6"/>
    <rect x="412" y="248" width="150" height="46" rx="6"/>
  </g>
  <g font-size="12" fill="currentColor" text-anchor="middle"
     font-family="var(--font-ui, sans-serif)">
    <text x="87" y="45">map the space</text><text x="87" y="61" opacity="0.6">Atlas survey</text>
    <text x="287" y="45">confirm</text><text x="287" y="61" opacity="0.6">disjoint seeds</text>
    <text x="487" y="45">gates</text><text x="487" y="61" opacity="0.6">thirty seeds</text>
    <text x="687" y="45">overfitting</text><text x="687" y="61" opacity="0.6">control</text>
    <text x="87" y="267">write a strategy</text><text x="87" y="283" opacity="0.6">or train a policy</text>
    <text x="287" y="267">evaluate</text><text x="287" y="283" opacity="0.6">many seeds</text>
    <text x="487" y="267">paired sign test</text><text x="487" y="283" opacity="0.6">across seeds</text>
  </g>
  <g stroke="currentColor" stroke-width="1.2" fill="none" marker-end="url(#tl-arrow)">
    <path d="M 162 49 L 206 49"/>
    <path d="M 362 49 L 406 49"/>
    <path d="M 562 49 L 606 49"/>
    <path d="M 687 72 L 687 118"/>
    <path d="M 350 160 L 350 242"/>
    <path d="M 162 271 L 206 271"/>
    <path d="M 362 271 L 406 271"/>
    <path d="M 640 160 L 640 242"/>
  </g>
  <g fill="var(--accent, currentColor)" opacity="0.14">
    <rect x="252" y="118" width="290" height="42" rx="6"/>
  </g>
  <rect x="252" y="118" width="290" height="42" rx="6" fill="none"
        stroke="var(--accent, currentColor)" stroke-width="1.6"/>
  <text x="397" y="144" font-size="13" text-anchor="middle"
        fill="var(--accent, currentColor)"
        font-family="var(--font-mono, monospace)">preset pt-v12, frozen</text>
  <g fill="none" stroke="currentColor" stroke-width="1.2" opacity="0.85">
    <rect x="572" y="118" width="180" height="42" rx="6"/>
  </g>
  <text x="662" y="144" font-size="12" text-anchor="middle" fill="currentColor"
        font-family="var(--font-ui, sans-serif)">realism envelope</text>
  <path d="M 542 139 L 566 139" stroke="currentColor" stroke-width="1.2"
        fill="none" marker-end="url(#tl-arrow)"/>
  <g font-size="10.5" fill="currentColor" opacity="0.72"
     font-family="var(--font-ui, sans-serif)">
    <text x="184" y="44">then</text>
    <text x="384" y="44">then</text>
    <text x="584" y="44">then</text>
    <text x="695" y="98">emits</text>
    <text x="358" y="205">cited by</text>
    <text x="648" y="205">bounds the claim</text>
    <text x="184" y="266">then</text>
    <text x="384" y="266">then</text>
    <text x="550" y="134">attaches to</text>
  </g>
  <text x="12" y="16" font-size="11" fill="currentColor" opacity="0.6"
        font-family="var(--font-mono, monospace)">LOOP A -- calibrate the market model</text>
  <text x="12" y="232" font-size="11" fill="currentColor" opacity="0.6"
        font-family="var(--font-mono, monospace)">LOOP B -- evaluate a strategy</text>
</svg>
<figcaption style="color:var(--mut);font-size:13px;margin-top:8px">
Loop A ends at a frozen preset. The preset and the realism envelope attached to
it are inputs to Loop B, and bound what a Loop B result may claim. There is no
arrow from Loop B back to Loop A.
</figcaption>
</figure>

## Which loop you are in decides what you read

| you want to | loop | start at |
|---|---|---|
| test a trading strategy | B | [Agents and evaluation](agents-and-evaluation.md) |
| train a reinforcement-learning policy | B | [Running a simulation](running-a-simulation.md) |
| know what your result depends on | A | [Atlas](atlas.md) |
| fit coefficients to your own data | A | [Atlas](atlas.md) |
| know what the market is measured to reproduce | neither | [The realism envelope](realism-envelope.md) |

## What the envelope adds

The preset carries a measurement with it. `pt-v12` holds all fourteen realism
statistics inside their real-market bands at the certified horizon of 252
trading days, and the envelope names five gaps where it does not hold. A Loop B
result inherits both. The certification is what lets you say the market behaved
like a real one; the gaps are what stop you saying more than that.
