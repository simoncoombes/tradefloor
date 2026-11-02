"""ptv22traded -- the pt-v22 rows read on traded accounts
(CRITERIA-pt-v22.md: MG2, MG3, XD1 to XD5 and AX2, "the ptv22traded stage";
IF4, "metaorder_curve.py pointed at the front future's book").

    python ptv22traded.py measure --arm NAME[@BASE][:dial=v,...] --seeds 201-203 \\
        [--years 21] [--parts traded,push,metaorder] [--switches phase1] \\
        [--workers 3] [--engine PATH] --out DIR
        the same as `ptv22.py measure --parts traded,push,metaorder`; the
        files land beside the ptv22 stage's free-/chain- files, and
    python ptv22.py grade DIR/NAME
        reads all of them, every row against its band.

Three parts, each a ptv22.py measure part (one JSON per seed):

- `traded`, one history of `--years` with accounts trading the listed
  futures through their books from the end of the first year (BURN), each a
  `tradefloor.Portfolio` stepped with the hooks every harness calls
  (collect_dividends and settle_open after the open, accrue before the
  close, settle_close after it): the XD1 rules on published data and the
  instruments they time held unconditionally, the XD2 basket rule and the
  basket held, and margin-stress accounts levered to their initial margin.
  MG2 is read on every one of them.
- `push` (XD3), per seed: a warmed market forked at the session before an
  index-future and a VIX-future expiry, one fork pushing the settlement
  print with the stock basket against a long position at the limit, the
  other not; the push's P&L is the pushed fork's less the twin's.
- `metaorder` (IF4), per seed: tools/calibration/metaorder_curve.py's Q6
  measurement (60 warm sessions, the session opened and run 15 ticks, a
  day TWAP forked against a twin that does not trade, implementation
  shortfall in the daily sigma) on the front index future's book instead of
  a name's.

MG3, XD4, XD5 and AX2 read option portfolios, option rules and the delivery
of shares on exercise. The engine they are written for (tradefloor dev with
PRs #272 and #273) lists futures only: `ContractKind` has no option, and
`Portfolio` holds none. Their estimators are below and tested on records of
the shape the planned API gives; the `traded` part records
`options_listed: False` and the grade prints them "not read" until phase 2
and 3 list options.
"""
from __future__ import annotations

import json
import math
import pathlib
import statistics as st
import sys
import zlib

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import ptv22 as P  # noqa: E402

YEAR, MONTH = P.YEAR, P.MONTH
CASH = 10_000_000.0
#: Rules decide on the last close's published values and trade at 09:45
#: (15 ticks into the session), market orders through the contract's book.
TRADE_TICK = 15
#: A position is re-sized only when its contract count is off its target by
#: more than this share (or the target's contract or sign changes), so a
#: unit position does not pay the spread every session for a contract or two.
REBALANCE = 0.10
#: A margin-stress account below this share of its starting cash, with no
#: futures left, is replaced by a fresh one (MG2 keeps both records).
RESPAWN = 0.10
#: Trailing window for the basis rules' mean and sd (published closes only).
BASIS_LOOKBACK = YEAR
BASIS_MIN = QUARTER = P.QUARTER

#: The phase 1 switches (PLAN-pt-v22.md, mechanisms 1 to 9) at the values the
#: engine's own tools fit (tradefloor dev: basis_dials.py, vix_futures_dials.py
#: and margin_dials.py, and tests/test_futures_accounts.py), set beneath an
#: arm's own dials by `--switches phase1`. An arm that sets any of them keeps
#: its own value.
PHASE1 = dict(index_level_listed=1.0, vix_intraday_live=1.0,
              futures_index_listed=1.0, basis_sd=3.753, basis_persistence=0.429, night_session_steps=10.0,
              futures_vix_listed=1.0, futures_vix_live_fast_share=0.713, futures_vix_live_fast_half_life=5.96,
              futures_vix_live_slow_half_life=71.7, futures_rates_listed=1.0, futures_oil_listed=1.0,
              margin_scan_coverage=0.99, margin_scan_tail=1.762)
SWITCHES = {"none": {}, "phase1": PHASE1}

#: What each part reads from the engine (ptv22.require reads this through
#: ptv22.PLANNED).
PLANNED = {"traded": ("index_level", "contracts", "quote", "settlements", "forecast", "run_night", "submit"),
           "push": ("index_level", "contracts", "quote", "settlements", "submit", "fork", "book"),
           "metaorder": ("contracts", "quote", "submit", "fork", "book"),
           "names": ("chain", "quote", "settlements", "submit", "exercise", "exercise_violations",
                     "name_option_roots", "option_expiries", "earnings_calendar", "fork", "book")}

# ---------------------------------------------------------------- the rules
#
# Each XD1 rule is a target in units of the account's net worth (a unit is
# a fully collateralised position whose notional equals net worth), decided
# from what the last close published and traded at TRADE_TICK. Its
# buy-and-hold is the instrument it times, held as a unit unconditionally
# and rolled the same way ("hold"). `bound` is the clause of XD1 it is
# judged by:
#   "C10c"   timing and levered rules: an exploit when the median annualised
#            excess over buy-and-hold is above +1.0 point OR it is ahead in
#            more than 2/3 of histories;
#   "C10a"   other rules: an exploit when above +1.0 point AND ahead in more
#            than 2/3 (XD1's words; see C10A_WORDS);
#   "VF5"    rules that earn a real premium: the median annual log return
#            over 252-session windows inside VF5's band negated.
# Every rule here goes in and out of its instrument on a published signal,
# so every one is a timing rule; none takes the "C10a" clause.
#
# legs: ((root, k), sign): the k-th listed contract of `root` counted from
# the front (the engine's `front` flag), long +1 or short -1.

XD1_RULES = {
    # the basis: long the front index future, out while it is rich (its close
    # basis above its trailing mean by more than one trailing sd)
    "idx_basis_rich_out": {"legs": ((("IDX", 0), +1),), "hold": "IDX", "bound": "C10c"},
    # the basis, levered both ways: long while cheaper than its trailing mean, short otherwise
    "idx_basis_long_short": {"legs": ((("IDX", 0), +1),), "hold": "IDX", "bound": "C10c"},
    # the VIX curve: short the front VIX future in contango (F2 > F1), out otherwise
    "vix_short_contango": {"legs": ((("VIX", 0), -1),), "hold": None, "bound": "VF5"},
    # a calendar spread on the VIX curve: short F1, long F2 in contango, out otherwise
    "vix_calendar_contango": {"legs": ((("VIX", 0), -1), (("VIX", 1), +1)), "hold": "VIX_spread", "bound": "C10c"},
    # the oil curve: long the front oil future in backwardation (F1 > F4), out otherwise
    "oil_long_backwardation": {"legs": ((("OIL", 0), +1),), "hold": "OIL", "bound": "C10c"},
    # a calendar spread on the oil curve: long F1, short F4 in backwardation, out otherwise
    "oil_calendar_backwardation": {"legs": ((("OIL", 0), +1), (("OIL", 3), -1)), "hold": "OIL_spread",
                                   "bound": "C10c"},
    # meeting days: long, or short, the front policy-rate future over a session whose close holds a meeting
    "ff_meeting_long": {"legs": ((("FF", 0), +1),), "hold": "FF", "bound": "C10c"},
    "ff_meeting_short": {"legs": ((("FF", 0), -1),), "hold": "FF", "bound": "C10c"},
}
HOLDS = {"IDX": ((("IDX", 0), +1),), "VIX_spread": ((("VIX", 0), -1), (("VIX", 1), +1)),
         "OIL": ((("OIL", 0), +1),), "OIL_spread": ((("OIL", 0), +1), (("OIL", 3), -1)),
         "FF": ((("FF", 0), +1),)}
STRESS_ROOTS = ("IDX", "VIX", "FF", "TR3", "OIL")

#: CRITERIA-pt-v22.md's XD1 says a rule is an exploit "when its median
#: annualised excess over buy-and-hold is above +1.0 point and it is ahead in
#: more than 2/3 of histories (C10a's bound)"; results/ptv20/c10.py, which
#: grades C10a, passes a rule only when both hold, so it fails a rule on
#: either. The stage implements XD1's words for the "C10a" clause and flags
#: the difference; no rule here takes that clause.
C10A_WORDS = "and"
C10_EXCESS, C10_SHARE = 1.0, 2 / 3


def rule_signal(name, sig, hist):
    """The rule's unit multiplier (0 out, 1 in as its legs say, -1 reversed)
    from `sig`, what the last close published, and `hist`, the closes before
    it (for the trailing basis)."""
    if name == "idx_basis_rich_out" or name == "idx_basis_long_short":
        b = sig.get("idx_basis_bp")
        past = [x for x in hist[-BASIS_LOOKBACK:] if x is not None]
        if b is None or len(past) < BASIS_MIN:
            return 1
        m, s = float(np.mean(past)), float(np.std(past, ddof=1))
        if name == "idx_basis_rich_out":
            return 0 if b > m + s else 1
        return 1 if b < m else -1
    if name in ("vix_short_contango", "vix_calendar_contango"):
        f1, f2 = sig.get("vix_f1"), sig.get("vix_f2")
        return 1 if (f1 is not None and f2 is not None and f2 > f1) else 0
    if name in ("oil_long_backwardation", "oil_calendar_backwardation"):
        f1, f4 = sig.get("oil_f1"), sig.get("oil_f4")
        return 1 if (f1 is not None and f4 is not None and f1 > f4) else 0
    if name in ("ff_meeting_long", "ff_meeting_short"):
        return 1 if sig.get("meeting_next") else 0
    raise KeyError(name)


# ------------------------------------------------------------ engine helpers

def _tf_errors(tf):
    return tuple(x for x in (getattr(tf, "OrderError", None), getattr(tf, "ValidationError", None)) if x) or (Exception,)


def listed(e):
    """{root: [contract dicts from the front on, in expiry order]}: the
    contracts tradable now, starting at the engine's front contract."""
    by = {}
    for c in e.contracts():
        if c.get("kind") == "future":
            by.setdefault(c["root"], []).append(c)
    out = {}
    for root, cs in by.items():
        cs = sorted(cs, key=lambda c: c["expiry"])
        i = next((j for j, c in enumerate(cs) if c.get("front")), 0)
        out[root] = cs[i:]
    return out


def published(e, day):
    """What the close of session `day` published that the rules read: the
    front index future's close basis, the VIX curve's first two contracts and
    the oil curve's first and fourth (contracts live after this close, as
    ptv22's VF and OF rows take them), and whether the next session's close
    holds a scheduled policy meeting (forecast()["next_meeting"])."""
    out = {}
    by = {}
    for c in e.contracts():
        if c.get("kind") == "future" and c["expiry"] > day:
            by.setdefault(c["root"], []).append(c)
    for v in by.values():
        v.sort(key=lambda c: c["expiry"])
    idx = [c for c in by.get("IDX", []) if c.get("front")] or by.get("IDX", [])[:1]
    if idx:
        out["idx_basis_bp"] = float(e.quote(idx[0]["symbol"])["basis_bp"])
    def mark(c):
        m = e.quote(c["symbol"]).get("mark")
        return float(m) if m is not None else None

    vix = by.get("VIX", [])
    if len(vix) >= 2:
        out["vix_f1"], out["vix_f2"] = mark(vix[0]), mark(vix[1])
    oil = by.get("OIL", [])
    if len(oil) >= 4:
        out["oil_f1"], out["oil_f4"] = mark(oil[0]), mark(oil[3])
    f = e.forecast() if hasattr(e, "forecast") else None
    nm = f.get("next_meeting") if f else None
    out["meeting_next"] = nm is not None and int(nm) == day + 1
    return out


def displayed_depth(e, symbol, side):
    """A contract's displayed depth on one side: the quantity on the ten
    levels its book shows (the maker's ladder and agents' resting orders).
    The stage's stand-in for the position limit PLAN-pt-v22.md specifies
    ("per agent and contract, sized to the contract's displayed depth"),
    which the engine does not enforce yet."""
    try:
        return float(sum(lv.quantity for lv in e.book(symbol).price_levels(side)))
    except Exception:          # noqa: BLE001  a stand-in without a book
        return float("inf")


def leg_targets(e, legs, units, nw, lst):
    """Contracts per symbol for `units` of `legs`: each leg's notional equal
    to `units` times net worth at the front leg's price."""
    if not units or nw <= 0:
        return {}
    first = None
    out = {}
    for (root, k), sign in legs:
        cs = lst.get(root, [])
        if len(cs) <= k:
            return {}
        q = e.quote(cs[k]["symbol"])
        price = q.get("price") or q.get("mark")
        if first is None:
            first = math.floor(abs(units) * nw / (price * q["multiplier"]))
        out[cs[k]["symbol"]] = sign * (1 if units > 0 else -1) * first
    return {s: n for s, n in out.items() if n}


def trade_to(p, e, target, errs, log):
    """Bring the account's futures to `target` ({symbol: contracts}),
    leaving a position whose count is within REBALANCE of its target."""
    held = {s: f.quantity for s, f in p.futures.items()}
    for s in sorted(set(held) | set(target)):
        want, have = target.get(s, 0.0), held.get(s, 0.0)
        diff = want - have
        if abs(diff) < 1 or (want and have and want * have > 0 and abs(diff) <= REBALANCE * abs(want)):
            continue
        try:
            p.execute(e, s, diff)
        except errs as ex:
            log.append(f"{s} {diff:+g}: {type(ex).__name__}: {ex}"[:200])


def _derivs(p):
    """What a margin call liquidates: futures, and on the names stage the account's options."""
    return {**p.futures, **getattr(p, "options", {})}


class MarginWatch:
    """MG2 on one account: every margin call met or the account's futures
    (and options) liquidated by the next open, and no account below zero on a
    derivative loss without a liquidation. On the traded stage's accounts,
    which hold no options, this reads the futures alone, as before."""

    def __init__(self):
        self.calls = self.met = self.unmet = self.liquidations = self.below_zero = 0
        self.failures = []
        self._seen = 0
        self._below = None
        # The same calls read for risk left (design ptv22/optmargin, RULE.md "MG2"): a failure only where the
        # liquidation leaves a future, a short option, or a long option its book would still buy.
        self.failures_risk = []
        self.no_bid_left = []

    def _risk_left(self, p, e, day):
        risky, idle = [], []
        for s_, f in _derivs(p).items():
            long_option = s_ in getattr(p, "options", {}) and f.quantity > 0
            sellable = True
            if long_option and e is not None:
                try:
                    cost = e.book(s_).sweep_cost("sell", float(f.quantity))
                    sellable = cost is not None and cost.filled > 0
                except Exception:          # noqa: BLE001  a contract with no book takes nothing
                    sellable = False
            (risky if (not long_option or sellable) else idle).append(f"{s_} {f.quantity:g}")
        if idle:
            self.no_bid_left.append((day, ",".join(idle)))
        return risky

    def after_open(self, p, day, e=None):
        open_calls = p.margin_calls[self._seen:]
        self._seen = len(p.margin_calls)
        for c in open_calls:
            self.calls += 1
            if c.get("met") is None:
                self.failures.append((day, "a call left undecided at the open"))
            elif c["met"]:
                self.met += 1
            else:
                self.unmet += 1
                liq = [x for x in p.liquidations if x["day"] == day]
                self.liquidations += len(liq)
                if _derivs(p):
                    self.failures.append((day, "an unmet call left futures held after the open: "
                                          + ",".join(f"{s} {f.quantity:g}" for s, f in _derivs(p).items())))
                if any(abs(abs(x["filled"]) - abs(x["quantity"])) > 1e-9 for x in liq):
                    self.failures.append((day, "a liquidation not filled in full"))
                risky = self._risk_left(p, e, day) if _derivs(p) else []
                if risky:
                    self.failures_risk.append((day, "an unmet call left risk held after the open: " + ",".join(risky)))
        if self._below is not None:
            d, nw = self._below
            if not any(x["day"] == day for x in p.liquidations) and _derivs(p):
                self.failures.append((day, f"below zero at the close of {d} ({nw:.0f}) and not liquidated"))
            self._below = None

    def after_close(self, p, e, day, nw):
        made = len(p.margin_calls) > self._seen
        if nw < 0 and _derivs(p):
            self.below_zero += 1
            self._below = (day, nw)
            if not made:
                self.failures.append((day, f"below zero ({nw:.0f}) with no margin call"))

    def record(self):
        return {"calls": self.calls, "met": self.met, "unmet": self.unmet, "liquidations": self.liquidations,
                "below_zero": self.below_zero, "failures": self.failures[:50], "n_failures": len(self.failures),
                "failures_risk": self.failures_risk[:50], "n_failures_risk": len(self.failures_risk),
                "no_bid_left": self.no_bid_left[:50], "n_no_bid_left": len(self.no_bid_left)}


def merge_margin(recs):
    out = {k: sum(r[k] for r in recs) for k in ("calls", "met", "unmet", "liquidations", "below_zero", "n_failures")}
    out["failures"] = [f for r in recs for f in r["failures"]][:50]
    return out


# ------------------------------------------------------------ the traded part

def run_traded(tf, params, seed, years, engine_factory=None, portfolio_cls=None, cash=CASH):
    """One history with every account the stage reads (module note), on the
    certified roster. Records each account's net worth at every close from
    BURN, its margin record (MG2), and the rules' signals."""
    u = list(tf.Universe.random(P.ROSTER[0], seed=P.ROSTER[1]))
    e = engine_factory(seed) if engine_factory else tf.Engine(seed=seed, universe=u, model=params)
    P.require(e, "traded")
    Portfolio = portfolio_cls or tf.Portfolio
    errs = _tf_errors(tf)
    burn = P.BURN
    tickers = list(e.tickers)
    shares = np.array([float(x.shares_outstanding) for x in u])
    options = any(c.get("kind") == "option" for c in e.contracts())
    accounts = {}
    for name in XD1_RULES:
        accounts[name] = {"kind": "XD1", "rule": name}
    for h in HOLDS:
        accounts["hold_" + h] = {"kind": "hold", "hold": h}
    accounts["basket_night"] = {"kind": "XD2"}
    accounts["hold_basket"] = {"kind": "hold_basket"}
    for root in STRESS_ROOTS:
        for sign in (+1, -1):
            accounts[f"stress_{root}_{'long' if sign > 0 else 'short'}"] = {"kind": "stress", "root": root,
                                                                             "sign": sign}
    rec = {"seed": seed, "burn": burn, "options_listed": options, "accounts": {}, "night_move": [], "signals": []}
    sigs, basis_hist = [], []
    last_mark = None          # the front index future's (symbol, close mark)
    night = []
    for day in range(years * YEAR):
        live = day >= burn
        if day == burn:
            for name, a in accounts.items():
                a["p"] = Portfolio(cash=cash, owner=name)
                a["nw"], a["watch"], a["log"], a["dead"], a["past"] = [], MarginWatch(), [], False, []
            # the stress accounts start at the cash their position limit
            # needs, so their margin binds where the limit would
            lst = listed(e)
            for name, a in accounts.items():
                if a["kind"] == "stress" and lst.get(a["root"]):
                    q = e.quote(lst[a["root"]][0]["symbol"])
                    lim = displayed_depth(e, lst[a["root"]][0]["symbol"], "sell" if a["sign"] > 0 else "buy")
                    if q.get("initial_margin"):
                        a["p"].cash = a["p"].starting_cash = min(cash, lim * q["initial_margin"])
        # the night, then XD2's signal: the front index future's night move
        move = None
        if day:
            e.run_night()
            lst = listed(e)
            if lst.get("IDX") and last_mark and lst["IDX"][0]["symbol"] == last_mark[0]:
                move = math.log(float(e.quote(last_mark[0])["price"]) / last_mark[1])
        night.append(move)
        e.open_market()
        if live:
            for name, a in accounts.items():
                p = a["p"]
                p.collect_dividends(e)
                p.settle_open(e)
                a["watch"].after_open(p, day)
            # XD2: the basket bought at the open when the night move is up, sold at the open when not
            _basket(e, accounts["basket_night"], 1 if (move is not None and move > 0) else 0, tickers, shares, errs)
            _basket(e, accounts["hold_basket"], 1, tickers, shares, errs)
        e.run_session(9, 30, 3, TRADE_TICK)
        if live and sigs:
            lst = listed(e)
            sig = sigs[-1]
            for name, a in accounts.items():
                p = a["p"]
                nw = p.net_worth(e)
                if a["dead"] or a["kind"] in ("XD2", "hold_basket"):
                    continue
                if a["kind"] == "stress" and nw < RESPAWN * p.starting_cash and p.futures:
                    trade_to(p, e, {}, errs, a["log"])        # closed, and replaced at the next session
                    continue
                if a["kind"] == "stress" and nw < RESPAWN * p.starting_cash:
                    # a margin-stress account that has lost what it had is
                    # replaced by a fresh one at the same cash, so the margin
                    # is exercised over the whole history; its record is kept
                    a["past"].append(a["watch"].record())
                    a["p"] = p = Portfolio(cash=p.starting_cash, owner=f"{name}#{len(a['past'])}")
                    a["watch"] = MarginWatch()
                    nw = p.net_worth(e)
                if nw <= 0:
                    a["dead"] = True
                    trade_to(p, e, {}, errs, a["log"])
                    continue
                if a["kind"] == "XD1":
                    r = XD1_RULES[a["rule"]]
                    tgt = leg_targets(e, r["legs"], rule_signal(a["rule"], sig, basis_hist[:-1]), nw, lst)
                elif a["kind"] == "hold":
                    tgt = leg_targets(e, HOLDS[a["hold"]], 1, nw, lst)
                else:
                    tgt = _stress_target(e, a, nw, lst)
                trade_to(p, e, tgt, errs, a["log"])
        flows = {}
        if live:
            for a in accounts.values():
                for t, (b, s) in a["p"].pending_flow().items():
                    x = flows.setdefault(t, [0.0, 0.0])
                    x[0] += b; x[1] += s
                a["p"].clear_flow()
        e.run_session(9, 30 + TRADE_TICK, 3, 390 - TRADE_TICK, **({"fills": {k: tuple(v) for k, v in flows.items()}}
                                                                  if flows else {}))
        if live:
            for a in accounts.values():
                a["p"].accrue(e)
        e.close_market()
        s = published(e, day)
        sigs.append(s)
        basis_hist.append(s.get("idx_basis_bp"))
        lst = listed(e)
        if lst.get("IDX"):
            q = e.quote(lst["IDX"][0]["symbol"])
            last_mark = (lst["IDX"][0]["symbol"], float(q["mark"])) if q.get("mark") else None
        if live:
            for name, a in accounts.items():
                p = a["p"]
                p.settle_close(e)
                nw = p.net_worth(e)
                a["watch"].after_close(p, e, day, nw)
                a["nw"].append(nw)
    rec["night_move"] = night[burn:]
    rec["signals"] = sigs[burn:]
    for name, a in accounts.items():
        p = a.get("p")
        rec["accounts"][name] = {k: v for k, v in a.items() if k not in ("p", "watch", "log", "nw", "past")}
        if p is None:
            continue
        rec["accounts"][name].update({
            "nw": a["nw"], "start": p.starting_cash, "margin": merge_margin(a["past"] + [a["watch"].record()]),
            "generations": len(a["past"]) + 1,
            "settled": len(p.settled), "trades": len(p.fills), "order_errors": a["log"][:20],
            "n_order_errors": len(a["log"])})
    return rec


def _stress_target(e, a, nw, lst):
    """A margin-stress account: as many front contracts as its net worth
    covers at the initial margin the last close set, at most the displayed
    depth (the position limit's stand-in), long or short."""
    cs = lst.get(a["root"], [])
    if not cs:
        return {}
    sym = cs[0]["symbol"]
    im = e.quote(sym).get("initial_margin")
    if not im:
        return {}
    lim = displayed_depth(e, sym, "sell" if a["sign"] > 0 else "buy")
    n = min(math.floor(nw / im), math.floor(lim))
    return {sym: a["sign"] * n} if n > 0 else {}


def _basket(e, a, units, tickers, shares, errs):
    """The cap-weighted basket of the roster at `units` of net worth, bought
    or sold at once through the names' books; a held basket is not re-weighted."""
    p = a["p"]
    held = any(abs(p.quantity_of(t)) > 0 for t in tickers)
    if (units > 0) == held:
        return
    if units > 0:
        px = np.frombuffer(e.prices(), "<f8")
        w = shares * px / (shares * px).sum()
        nw = p.net_worth(e)
        for t, wi, pi in zip(tickers, w, px):
            n = math.floor(wi * nw / pi)
            if n > 0:
                try:
                    p.execute(e, t, n)
                except errs as ex:
                    a["log"].append(f"{t} +{n}: {ex}"[:200])
    else:
        for t in tickers:
            q = p.quantity_of(t)
            if q:
                try:
                    p.execute(e, t, -q)
                except errs as ex:
                    a["log"].append(f"{t} {-q:+g}: {ex}"[:200])


# --------------------------------------------------------------- the rows

def ann_log(nw, start=None):
    """Annualised log return of a net-worth series (one value a close), the
    first value (or `start`) the base; a series that reaches zero reads as
    the loss of everything (log of 1e-6 of the base)."""
    base = start if start is not None else nw[0]
    if len(nw) < 2 or base <= 0:
        return float("nan")
    years = len(nw) / YEAR
    return math.log(max(nw[-1], 1e-6 * base) / base) / years


def window_returns(nw, start):
    """Annual log returns over windows of 252 closes, anchored at the last
    (ptv22.windows_back), as VF5 reads its constant-maturity position."""
    x = np.maximum(np.asarray([start] + list(nw), float), 1e-6 * start)
    return [float(math.log(x[b] / x[a])) for a, b in P.windows_back(len(x) - 1, YEAR)]


def c10_exploit(median_excess, share_ahead, bound):
    """Whether a rule breaks its clause of XD1's bound."""
    if bound == "C10c" or C10A_WORDS == "or":
        return median_excess > C10_EXCESS or share_ahead > C10_SHARE
    return median_excess > C10_EXCESS and share_ahead > C10_SHARE


def vf5_band(realj=None):
    r = realj or json.load(open(P.DATA / "real.json"))
    v = r["VF"]["VF5"]
    return -v["high"], -v["low"]


def traded_rows(traded, realj=None):
    """MG2, XD1 and XD2 from the `traded` part's records (and MG3, XD4, XD5,
    AX2 where the records carry options)."""
    rows, rep = {}, {}
    if not traded:
        return rows, rep
    # MG2: failures over every account of every history
    fails, tally = 0, {"calls": 0, "met": 0, "unmet": 0, "liquidations": 0, "below_zero": 0}
    by_root = {}
    examples = []
    for h in traded:
        for name, a in h["accounts"].items():
            m = a.get("margin")
            if not m:
                continue
            fails += m["n_failures"]
            for k in tally:
                tally[k] += m[k]
            if a.get("kind") == "stress":
                r = by_root.setdefault(a["root"], {"calls": 0, "unmet": 0, "below_zero": 0})
                for k in r:
                    r[k] += m[k]
            examples += [(h["seed"], name) + tuple(f) for f in m["failures"][:3]]
    rows["MG2"] = fails
    rep["MG2"] = {**tally, "stress_by_root": by_root, "failures": examples[:10]}
    # XD1: each rule against the instrument it times, held
    lo, hi = vf5_band(realj)
    per = {}
    for name, r in XD1_RULES.items():
        ex, wins = [], []
        for h in traded:
            a = h["accounts"].get(name)
            if not a or not a.get("nw"):
                continue
            if r["bound"] == "VF5":
                wins += window_returns(a["nw"], a["start"])
            else:
                b = h["accounts"]["hold_" + r["hold"]]
                ex.append(100 * (ann_log(a["nw"], a["start"]) - ann_log(b["nw"], b["start"])))
        if r["bound"] == "VF5" and wins:
            v = st.median(wins)
            per[name] = {"bound": "VF5", "median_annual_log_return": v, "windows": len(wins), "band": [lo, hi],
                         "breaks": not (lo <= v <= hi)}
        elif ex:
            med, share = st.median(ex), sum(x > 0 for x in ex) / len(ex)
            per[name] = {"bound": r["bound"], "median_excess_pts": med, "share_ahead": share, "histories": len(ex),
                         "breaks": c10_exploit(med, share, r["bound"])}
    if per:
        rows["XD1"] = sum(x["breaks"] for x in per.values())
        rep["XD1"] = per
    # XD2: the basket bought at the open on the night futures move, against the basket held
    ex = []
    for h in traded:
        a, b = h["accounts"].get("basket_night"), h["accounts"].get("hold_basket")
        if a and b and a.get("nw") and b.get("nw"):
            ex.append(100 * (ann_log(a["nw"], a["start"]) - ann_log(b["nw"], b["start"])))
    if ex:
        med, share = st.median(ex), sum(x > 0 for x in ex) / len(ex)
        moves = [x for h in traded for x in h["night_move"] if x is not None]
        rows["XD2"] = int(c10_exploit(med, share, "C10c"))
        rep["XD2"] = {"median_excess_pts": med, "share_ahead": share, "histories": len(ex),
                      "share_nights_up": float(np.mean([x > 0 for x in moves])) if moves else float("nan")}
    # the option rows, where the records carry them
    opt = [h for h in traded if h.get("options_listed")]
    rep["options_listed"] = bool(opt)
    if opt:
        r, p = option_rows(opt, realj)
        rows.update(r); rep.update(p)
    return rows, rep


# ------------------------------------------------- MG3, XD4, XD5, AX2 (options)
#
# Records of the shape the planned API gives (PLAN-pt-v22.md, phases 2 and
# 3), one per history, under "options":
#   "portfolios": {name: [(dV, margin_prev), ...]}  each option portfolio's
#        one-session value change and the initial margin the previous close
#        set on it (Portfolio.margin_requirement), dollars
#   "rules": {name: {"kind": "ob"|"c10"|"lose", "ob": "OB1".."OB4" (kind ob),
#        "windows": {"r": [...], "v": [...], "d": [...]} (kind ob, per 252-session
#        window, against the index total return as ptv22.ob_window reads),
#        "excess": annualised excess over buy-and-hold, points (kind c10),
#        "pnl": [P&L after cost per trade] (kind lose), "family": "index"|"name"}}
#   "deliveries": [{"shares_expected", "shares_moved", "cash_expected", "cash_moved"}, ...]
# None of them exists on an engine that lists no options.

def mg3_pairs(h):
    """One history's MG3 pairs (draft item 22): (the value's change, the margin set at the close before) of each
    single-name option portfolio, one name's options as FINRA 4210(g)(8)(A) groups them (`_name_pairs`)."""
    return [(dv, m) for dv, m in h["options"].get("name_portfolios", []) if m]


def mg3_share(hist):
    """MG3 (draft item 22, restated 2026-10-27): the share of single-name option portfolio sessions whose one-session
    loss passes the portfolio's initial margin set at the close before, losses only; band 0 to 0.00112 (the forty's
    rate of one-session moves past +-15%, 2015-2025). Item 19(a)'s account-level reading is replaced."""
    past = n = 0
    for h in hist:
        for dv, m in mg3_pairs(h):
            past += -dv > m
            n += 1
    return past / n if n else float("nan")


def ax2_error(hist):
    err = [max(abs(d["shares_expected"] - d["shares_moved"]), abs(d["cash_expected"] - d["cash_moved"]))
           for h in hist for d in h["options"].get("deliveries", [])]
    return max(err) if err else float("nan")


def option_rule_exploits(hist, family, realj):
    """XD4 (family "index") or XD5 ("name"): rules inside their OB band (the
    median per-window r, v and d each inside the OB row's band), or C10a's
    bound, and the trades that must lose (parity, box, conversion, the
    expiry push) with a mean P&L after cost at most 0. Returns (count of
    rules breaking theirs, per rule)."""
    realj = realj or json.load(open(P.DATA / "real.json"))
    agg = {}
    for h in hist:
        for name, r in h["options"].get("rules", {}).items():
            if r.get("family") != family:
                continue
            g = agg.setdefault(name, {"kind": r["kind"], "ob": r.get("ob"), "r": [], "v": [], "d": [], "excess": [],
                                      "pnl": []})
            for x in "rvd":
                g[x] += r.get("windows", {}).get(x, [])
            if "excess" in r:
                g["excess"].append(r["excess"])
            g["pnl"] += r.get("pnl", [])
    out = {}
    for name, g in agg.items():
        if g["kind"] == "ob":
            vals = {x: st.median(g[x]) for x in "rvd" if g[x]}
            bands = {x: (realj["OB"][f"{g['ob']}{x}"]["low"], realj["OB"][f"{g['ob']}{x}"]["high"]) for x in vals}
            bad = any(not (bands[x][0] <= vals[x] <= bands[x][1]) for x in vals)
            out[name] = {"kind": "ob", "values": vals, "bands": bands, "breaks": bad}
        elif g["kind"] == "c10" and g["excess"]:
            med, share = st.median(g["excess"]), sum(x > 0 for x in g["excess"]) / len(g["excess"])
            out[name] = {"kind": "c10", "median_excess_pts": med, "share_ahead": share,
                         "breaks": c10_exploit(med, share, "C10c")}
        elif g["kind"] == "lose" and g["pnl"]:
            m = float(np.mean(g["pnl"]))
            out[name] = {"kind": "lose", "mean_pnl": m, "breaks": m > 0}
    return sum(x["breaks"] for x in out.values()), out


def option_rows(hist, realj=None):
    rows, rep = {}, {}
    v = mg3_share(hist)
    if v == v:
        rows["MG3"] = v
    v = ax2_error(hist)
    if v == v:
        rows["AX2"] = v
    for row, fam in (("XD4", "index"), ("XD5", "name")):
        n, per = option_rule_exploits(hist, fam, realj)
        if per:
            rows[row], rep[row] = n, per
    return rows, rep


# ------------------------------------------------------------ the push (XD3)

#: Basket participation, as a share of each name's daily volume, and the
#: window it is sent over (ticks before the close of the session before
#: expiry), then unwound over the first PUSH_UNWIND ticks after the open.
PUSH_SIZES = (0.03, 0.1, 0.3)
PUSH_WINDOWS = {"hour": 60, "day": 360}
PUSH_SLICE = 5
PUSH_UNWIND = 60
PUSH_WARM = 60


def _advance(e, t, n):
    """Run `n` ticks starting `t` ticks after the 09:30 open."""
    if n <= 0:
        return t
    m = 9 * 60 + 30 + t
    e.run_session(m // 60, m % 60, 3, n)
    return t + n


def _to_session_before(e, root, after, years_cap):
    """Run whole sessions until the session before the next `root` expiry
    later than `after`; returns (expiry session, symbol) with the engine
    closed after the session two before it (so the next open is expiry-1)."""
    while True:
        day = int(e.day_count)
        xs = sorted((c["expiry"], c["symbol"]) for c in e.contracts() if c["root"] == root and c["expiry"] > after)
        if xs and xs[0][0] == day + 1:
            return xs[0]
        if day > years_cap * YEAR:
            raise RuntimeError(f"no {root} expiry found")
        if day:
            e.run_night()
        e.open_market(); _advance(e, 0, 390); e.close_market()


def push_trial(e_base, tf, u, root, sign_basket, frac, window, errs):
    """From an engine opened on the session before `root`'s expiry, at tick
    0: fork a pushed and a twin market. The pushed one trades the roster's
    names at `frac` of each name's daily volume, `sign_basket` (+1 buys,
    -1 sells), in slices over the last `window` ticks of the session, holds
    through the expiry open, and unwinds over the first PUSH_UNWIND ticks.
    The futures position at the limit is the same in both; the push's P&L
    is the position's settlement in the pushed market less the twin's, plus
    the basket's P&L less what the same holdings made on the twin's prices:
    each fill priced against the twin's mid when its slice is sent
    (metaorder_curve.py's reference), and the leftover at the pushed mid
    less the twin's. So both legs are pushed fork less twin, and the
    market's own move over the night on a basket of `frac` of the cash
    market's daily volume, which the push does not cause, is left out.
    `pnl_raw` keeps the basket's cash and leftover undifferenced (the
    estimator before 2026-10-10)."""
    sym = min((c for c in e_base.contracts() if c["root"] == root), key=lambda c: c["expiry"])
    x_sym, expiry = sym["symbol"], sym["expiry"]
    mult = float(e_base.quote(x_sym)["multiplier"])
    limit = displayed_depth(e_base, x_sym, "sell")          # long at the limit
    pushed, twin = e_base.fork(2)
    tickers = list(e_base.tickers)
    adv = np.array([x.avg_volume for x in u])
    start = 390 - window
    n = window // PUSH_SLICE
    cash, held = 0.0, np.zeros(len(tickers))
    vs_twin = 0.0          # sum fill (twin mid - average), signed: the basket's cost against the twin
    t = 0
    for x in (pushed, twin):
        _advance(x, 0, start)
    t = start
    for k in range(n):
        for i, tk in enumerate(tickers):
            q = sign_basket * frac * adv[i] / n
            ref = twin.book(tk).mid_price
            r = pushed.submit("push", tk, q)
            f = r["filled"] * (1 if q > 0 else -1)
            held[i] += f
            if r["filled"]:
                cash -= f * r["average_price"]
                vs_twin += f * (ref - r["average_price"])
        for x in (pushed, twin):
            _advance(x, t, PUSH_SLICE)
        t += PUSH_SLICE
    for x in (pushed, twin):
        x.close_market()
        x.run_night()
        x.open_market()
    settle = {}
    for nm, x in (("pushed", pushed), ("twin", twin)):
        s = [z for z in x.settlements(expiry) if z["symbol"] == x_sym]
        settle[nm] = float(s[0]["value"]) if s else float("nan")
    m = PUSH_UNWIND // PUSH_SLICE
    t = 0
    for k in range(m):
        for i, tk in enumerate(tickers):
            q = -held[i] / (m - k) if k < m - 1 else -held[i]
            if abs(q) > 0:
                ref = twin.book(tk).mid_price
                r = pushed.submit("push", tk, q)
                f = r["filled"] * (1 if q > 0 else -1)
                held[i] += f
                if r["filled"]:
                    cash -= f * r["average_price"]
                    vs_twin += f * (ref - r["average_price"])
        for x in (pushed, twin):
            _advance(x, t, PUSH_SLICE)
        t += PUSH_SLICE
    mids = np.array([pushed.book(tk).mid_price for tk in tickers])
    twin_mids = np.array([twin.book(tk).mid_price for tk in tickers])
    leftover = float((held * mids).sum())
    basket = vs_twin + float((held * (mids - twin_mids)).sum())
    fut = limit * mult * (settle["pushed"] - settle["twin"])
    notional = limit * mult * settle["twin"]
    return {"root": root, "frac": frac, "window": window, "limit": limit, "settle_pushed": settle["pushed"],
            "settle_twin": settle["twin"], "futures_pnl": fut, "basket_cash": cash, "leftover": leftover,
            "basket_vs_twin": basket, "basket_market": cash + leftover - basket,
            "pnl": fut + basket, "pnl_bp_of_notional": 1e4 * (fut + basket) / notional,
            "pnl_raw": fut + cash + leftover}


def run_push(tf, params, seed, years, engine_factory=None):
    """XD3 on one seed: the index-future push (buy the basket into the close
    before expiry; the future settles on the next opening prints) and the
    VIX-future push (sell the basket through the session that sets the
    published VIX the future settles on), each at every size and window."""
    u = list(tf.Universe.random(P.ROSTER[0], seed=P.ROSTER[1]))
    e = engine_factory(seed) if engine_factory else tf.Engine(seed=seed, universe=u, model=params)
    P.require(e, "push")
    errs = _tf_errors(tf)
    trials = []
    # the VIX push only where the VIX futures are listed (they ship off in v22)
    roots = [(r, s) for r, s in (("IDX", +1), ("VIX", -1)) if r != "VIX" or getattr(params, "futures_vix_listed", 1.0)]
    for root, sign in roots:
        _to_session_before(e, root, PUSH_WARM, years)
        e.run_night()
        e.open_market()
        for w_name, w in PUSH_WINDOWS.items():
            for f in PUSH_SIZES:
                trials.append({"window_name": w_name, **push_trial(e, tf, u, root, sign, f, w, errs)})
        _advance(e, 0, 390); e.close_market()
    return {"seed": seed, "trials": trials}


def push_rows(push):
    rows, rep = {}, {}
    if not push:
        return rows, rep
    groups = {}
    for h in push:
        for t in h["trials"]:
            groups.setdefault((t["root"], t["window_name"], t["frac"]), []).append(t)
    def mean(v, k):
        xs = [x[k] for x in v if k in x]
        return float(np.mean(xs)) if xs else None

    means = {f"{r}_{w}_{f}": {"mean_pnl": mean(v, "pnl"), "mean_bp": mean(v, "pnl_bp_of_notional"),
                              "mean_futures_pnl": mean(v, "futures_pnl"),
                              "mean_basket_vs_twin": mean(v, "basket_vs_twin"),
                              "mean_basket_market": mean(v, "basket_market"),
                              "mean_pnl_raw": mean(v, "pnl_raw"),
                              "seeds_ahead": sum(x["pnl"] > 0 for x in v), "seeds": len(v)}
             for (r, w, f), v in sorted(groups.items())}
    worst = max(means, key=lambda k: means[k]["mean_pnl"])
    rows["XD3"] = means[worst]["mean_pnl"]
    rep["XD3"] = {"worst": worst, "variants": means}
    return rows, rep


# ------------------------------------------------------- the metaorder (IF4)

#: metaorder_curve.py's constants: 60 warm sessions, the order sent from
#: tick 15, the day schedule a slice every 10 ticks over 360; sizes as a
#: share of the contract's stated daily volume.
MO_WARM, MO_T0, MO_DAY, MO_GAP = 60, 15, 360, 10
MO_SIZES = (0.01, 0.03, 0.1, 0.3)


def run_metaorder(tf, params, seed, years, engine_factory=None):
    """IF4 on one seed: Q6's day TWAP on the front index future's book.
    The future's daily sigma is the sd of its close-to-close log mark change
    over the warm sessions (pairs on the same contract), as
    metaorder_curve.py reads a name's; from the session opened and run 15
    ticks, each size is sent as a day TWAP on a fork and priced against the
    twin's mid when each slice is sent: implementation shortfall
    sum fill (avg - mid) / sum fill mid, over the daily sigma."""
    u = list(tf.Universe.random(P.ROSTER[0], seed=P.ROSTER[1]))
    e = engine_factory(seed) if engine_factory else tf.Engine(seed=seed, universe=u, model=params)
    P.require(e, "metaorder")
    marks = []
    for d in range(MO_WARM):
        if d:
            e.run_night()
        e.open_market(); _advance(e, 0, 390); e.close_market()
        f = listed(e).get("IDX")
        if f:
            marks.append((f[0]["symbol"], float(e.quote(f[0]["symbol"])["mark"])))
    ch = [math.log(b[1] / a[1]) for a, b in zip(marks, marks[1:]) if a[0] == b[0]]
    sig = float(np.std(ch))
    e.run_night()
    e.open_market()
    _advance(e, 0, MO_T0)
    sym = listed(e)["IDX"][0]["symbol"]
    V = float(e.quote(sym)["daily_volume"])
    out = []
    n = MO_DAY // MO_GAP
    for f in MO_SIZES:
        x, ctl = e.fork(2)
        num = den = filled = 0.0
        t = MO_T0
        for k in range(n):
            mid = ctl.book(sym).mid_price
            r = x.submit("me", sym, f * V / n)
            if r["filled"]:
                num += r["filled"] * (r["average_price"] - mid)
                den += r["filled"] * mid
                filled += r["filled"]
            if k < n - 1:
                for y in (x, ctl):
                    _advance(y, t, MO_GAP)
                t += MO_GAP
        out.append({"f": f, "is_": num / den / sig if den else float("nan"), "filled_share": filled / (f * V),
                    "sigma": sig, "symbol": sym, "daily_volume": V})
    return {"seed": seed, "rows": out}


def metaorder_rows(mo):
    rows, rep = {}, {}
    if not mo:
        return rows, rep
    by = {}
    for h in mo:
        for r in h["rows"]:
            by.setdefault(r["f"], []).append(r["is_"])
    curve = {str(f): float(np.median([v for v in vs if v is not None and v == v])) for f, vs in sorted(by.items())}
    if "0.1" in curve:
        rows["IF4"] = curve["0.1"]
    sizes = sorted(by)
    ys = [curve[str(f)] for f in sizes]
    sel = [(f, y) for f, y in zip(sizes, ys) if y > 0]
    expo = float(np.polyfit([math.log(f) for f, _ in sel], [math.log(y) for _, y in sel], 1)[0]) if len(sel) > 1 else None
    rep["IF4"] = {"curve_day_twap": curve, "exponent": expo, "seeds": len(mo),
                  "sigma_median": float(np.median([h["rows"][0]["sigma"] for h in mo]))}
    return rows, rep


# ---------------------------------------- the single names (XD5, AX1, AX2, MG3)
#
# The `names` part runs on a model that lists single-name options (pt-v22
# phase 3, `options_names_listed`). From BURN, accounts trade the names'
# options at TRADE_TICK through the dealer, each a tf.Portfolio stepped with
# the hooks every harness calls, so exercises and assignments deliver shares
# into them at the closes:
#
#   earnings_straddle  buys the at-the-money straddle of a name's first
#                      expiry after its report at the session before the
#                      reaction session, NAME_ALLOC of net worth, and sells it
#                      at the session after (C10c against cash);
#   covered_call       holds NAME_BASKET names in round lots and writes the
#                      first call above the price at the first expiry, one
#                      contract a lot, letting assignment take the shares and
#                      buying them back (C10c against `hold_names`, the same
#                      lots held);
#   conversion         buys a lot of a name, the put and sells the call at the
#                      strike nearest the price at the first expiry, held to
#                      expiry: a package that must lose after cost once its
#                      cash is charged the rate ("lose");
#   reversal           the conversion's mirror: sells a lot short, sells the
#                      put and buys the call ("lose");
#   box                buys the call at the strike nearest the price and the
#                      put one strike above, sells the call above and the put
#                      below: K2 - K1 a share at expiry ("lose");
#   index_box, index_box_short
#                      the box on the index options, bought and sold (XD4's
#                      parity and box clause, "lose");
#   dividend_capture   on the eve of a payer's ex-date buys its deepest listed
#                      call of the first expiry after it, exercises it at the
#                      close, is paid the dividend at the open and sells the
#                      shares ("lose");
#   short_strangle     writes the call and put PIN_STEPS strikes either side of
#                      the price on the NAME_BASKET names each month, sized to
#                      half its net worth in initial margin: MG3's account,
#                      the one that holds options alone. Shares an assignment
#                      delivers are closed at the next TRADE_TICK, and MG3
#                      reads only sessions begun with no shares (before
#                      2026-10-23 the shares were kept: the account's net worth
#                      then carried their moves, unmargined by the scan, and
#                      MG3 read 0.614).
#
# and, at each monthly expiry, the pin (`expiry_pin`, "lose"): two forks of
# the market at the expiry session's TRADE_TICK, each writing the straddle at
# the strike nearest a name's price at the position limit (`pin_size`), one
# pushing the name's close toward the strike
# with PUSH_PIN_FRAC of its daily volume over the last PUSH_PIN_WINDOW ticks
# and unwinding at the next open: the push's P&L is the pushed fork's net
# worth change less the twin's.
#
# Each "lose" trade (the parity packages and the dividend capture) runs in a
# portfolio of its own, owned by the trade, so its P&L is its own legs'. (They
# once shared one account per rule, read off the account's net worth: the
# second of two conversions open together then took the first's carry
# without its financing, about +$63 a trade on the full read, which no quote
# offered.) Its cash earns, and its borrowing pays, the policy rate day by day
# (`Portfolio.accrue`), and the P&L is the net worth at the exit less the
# starting cash grown at that rate: the trade's cash flows financed at the
# rate, with nothing charged on cash an early assignment handed back.
#
# AX1 is the engine's own read (`exercise_violations`) after every close; AX2
# compares, at every close, the shares and cash `settle_close` moved in each
# account with the engine's exercise and assignment rows for it.

NAME_ALLOC = 0.05
NAME_BASKET = 5
PIN_STEPS = 3
PUSH_PIN_FRAC = 0.03
PUSH_PIN_WINDOW = 60
XD5_RULES = {"earnings_straddle": {"kind": "c10", "bound": "C10c", "hold": None},
             "covered_call": {"kind": "c10", "bound": "C10c", "hold": "hold_names"},
             "conversion": {"kind": "lose"},
             "reversal": {"kind": "lose"},
             "box": {"kind": "lose"},
             "dividend_capture": {"kind": "lose"},
             "expiry_pin": {"kind": "lose"}}
#: XD4's parity and box clause, on the index options.
XD4_PARITY = ("index_box", "index_box_short")
#: The trades each in a portfolio of their own (module note above).
OWN_ACCOUNT = ("conversion", "reversal", "box", "index_box", "index_box_short", "dividend_capture")


def _quoted(e, legs):
    """Whether every option leg is quoted on the side it trades: a bid for
    a sale, an ask for a purchase. A package with a leg the dealer shows no
    price for (a bid under a tick) is not opened."""
    for sym, q in legs:
        if sym in e.tickers:
            continue
        qt = e.quote(sym)
        if not qt.get("bid" if q < 0 else "ask"):
            return False
    return True


def _trade_open(tf, e, rule, root, day, legs, scale, errs, log, **extra):
    """A trade in a portfolio of its own (`rule:root:day`), cash `scale`
    times enough to hold the package without a margin call, earning and
    paying the policy rate; `legs` [(symbol, quantity)] executed in order.
    None when a leg is unquoted on its side or the first cannot fill."""
    if not _quoted(e, legs):
        return None
    p = tf.Portfolio(cash=scale, owner=f"{rule}:{root}:{day}", cash_interest=True, reinvest_dividends=False)
    filled = []
    for sym, q in legs:
        if _execute(p, e, sym, q, errs, log) is None:
            if not filled:
                return None
        else:
            filled.append((sym, q))
    return {"rule": rule, "root": root, "day": day, "p": p, "start": scale, "grown": 1.0, "legs": filled, **extra}


def _trade_close(e, tr, day, errs, log):
    """Close what the trade still holds (stock left by a delivery, an option
    not settled) and record its P&L: the net worth less the starting cash
    grown at the policy rate."""
    p = tr["p"]
    for t in [t for t in e.tickers if p.quantity_of(t)]:
        _execute(p, e, t, -p.quantity_of(t), errs, log)
    for sym, o in list(p.options.items()):
        if o.quantity:
            _execute(p, e, sym, -o.quantity, errs, log)
    out = {k: v for k, v in tr.items() if k not in ("p",)}
    out.update(exit_day=day, pnl=p.net_worth(e) - tr["start"] * tr["grown"], interest=p.interest, p_owner=p.owner)
    return out


def _strike_rows(e, ticker, expiry):
    """`ticker`'s chain at `expiry`: {right: [rows by strike]}."""
    out = {"C": [], "P": []}
    for r in e.chain(ticker, expiry):
        out[r["right"]].append(r)
    for v in out.values():
        v.sort(key=lambda r: r["strike"])
    return out


def _nearest_row(rows, price, offset=0):
    i = min(range(len(rows)), key=lambda j: abs(rows[j]["strike"] - price))
    return rows[max(0, min(len(rows) - 1, i + offset))]


def _first_expiry(e, ticker, after):
    """The first listed expiry of `ticker`'s options at or after session `after`."""
    xs = [x["expiry"] for x in e.option_expiries(ticker) if x["expiry"] >= after]
    return min(xs) if xs else None


def _price_of(e, ticker):
    return float(dict(zip(e.tickers, np.frombuffer(e.prices(), "<f8")))[ticker])


def _execute(p, e, symbol, q, errs, log):
    try:
        return p.execute(e, symbol, q)
    except errs as ex:
        log.append(f"{symbol} {q:+g}: {type(ex).__name__}: {ex}"[:200])
        return None


def _largest(e, u, n):
    px = np.frombuffer(e.prices(), "<f8")
    caps = [(float(x.shares_outstanding) * px[i], e.tickers[i]) for i, x in enumerate(u)]
    roots = set(e.name_option_roots())
    return [t for _, t in sorted(caps, reverse=True) if t in roots][:n]


def pin_size(e, symbol):
    """The straddle the pin writes: the engine's position limit on the
    contract (`Engine.position_limit`, `option_position_limit`), or, on an
    engine or a model without one, the dealer's displayed depth on one side
    (ten levels of its displayed size), the limit's size at a dial of 1."""
    limit = e.position_limit(symbol) if hasattr(e, "position_limit") else None
    if limit:
        return float(limit)
    return float(e.quote(symbol)["bid_size"]) * 10


def pin_trial(e_base, tf, ticker, expiry, adv, errs):
    """The expiry pin on `ticker` from an engine open at its expiry session's
    TRADE_TICK: a pushed and a twin fork each write the straddle nearest the
    price at the dealer's displayed size; the pushed one trades the stock
    toward the strike over the last PUSH_PIN_WINDOW ticks and unwinds over the
    first PUSH_UNWIND ticks of the next session. Returns the push's P&L."""
    pushed, twin = e_base.fork(2)
    books = {}
    for nm, x in (("pushed", pushed), ("twin", twin)):
        p = tf.Portfolio(cash=CASH, owner="pin")
        rows = _strike_rows(x, ticker, expiry)
        call = _nearest_row(rows["C"], _price_of(x, ticker))
        put = next(r for r in rows["P"] if r["strike"] == call["strike"])
        size = pin_size(x, call["symbol"])
        for r in (call, put):
            _execute(p, x, r["symbol"], -size, errs, [])
        books[nm] = (p, call["strike"], p.net_worth(x))
    t = TRADE_TICK
    for x in (pushed, twin):
        _advance(x, t, 390 - PUSH_PIN_WINDOW - t)
    t = 390 - PUSH_PIN_WINDOW
    p, strike, _ = books["pushed"]
    n = PUSH_PIN_WINDOW // PUSH_SLICE
    for k in range(n):
        gap = strike - _price_of(pushed, ticker)
        if abs(gap) > 0:
            _execute(p, pushed, ticker, math.copysign(PUSH_PIN_FRAC * adv / n, gap), errs, [])
        for x in (pushed, twin):
            _advance(x, t, PUSH_SLICE)
        t += PUSH_SLICE
    for nm, x in (("pushed", pushed), ("twin", twin)):
        x.close_market()
        books[nm][0].settle_close(x)
        x.open_market()
        books[nm][0].settle_open(x)
    m = PUSH_UNWIND // PUSH_SLICE
    t = 0
    for k in range(m):
        for nm, x in (("pushed", pushed), ("twin", twin)):
            pp = books[nm][0]
            q = pp.quantity_of(ticker)
            if q:
                _execute(pp, x, ticker, -q / (m - k) if k < m - 1 else -q, errs, [])
        for x in (pushed, twin):
            _advance(x, t, PUSH_SLICE)
        t += PUSH_SLICE
    change = {nm: books[nm][0].net_worth(x) - books[nm][2] for nm, x in (("pushed", pushed), ("twin", twin))}
    return {"ticker": ticker, "expiry": expiry, "strike": strike, "pushed": change["pushed"],
            "twin": change["twin"], "pnl": change["pushed"] - change["twin"]}


#: AX1's agents' side (draft item 19(b)): the tolerances, per share, a quote or an exercise is read against its
#: bound with (the tree's own rounding; Black's European is the closed form, not the tree's)
AX1_TOL, AX1_TOL_REL = 0.005, 1e-4


def _black(right, fwd, strike, vol, years, rate):
    """Black's European price on the forward, per share."""
    if not (vol > 0 and years > 0 and fwd > 0 and strike > 0):
        return max(0.0, (fwd - strike) if right == "C" else (strike - fwd)) * math.exp(-rate * max(years, 0.0))
    sd = vol * math.sqrt(years)
    d1 = (math.log(fwd / strike) + 0.5 * sd * sd) / sd
    n = lambda x: 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))  # noqa: E731
    df = math.exp(-rate * years)
    if right == "C":
        return df * (fwd * n(d1) - strike * n(d1 - sd))
    return df * (strike * n(sd - d1) - fwd * n(-d1))


def _option_row(e, symbol, cache):
    """`symbol`'s chain row now (a name option: ROOT.Onnnn.Rk.kk), or None."""
    try:
        root, xs, rk = symbol.split(".", 2)
        key = (root, int(xs[1:]))
        if key not in cache:
            cache[key] = {r["symbol"]: r for r in e.chain(root, key[1])}
        return cache[key].get(symbol)
    except Exception:          # noqa: BLE001  an expiry no longer listed reads as no row
        return None


def ax1_agents(e, day, traded, closed, held, out):
    """AX1's agents' side at one close (draft item 19(b), (e): "the dealer or an agent"; the engine's
    `exercise_violations()` reads the dealer's). Called after the close, before the agents' settle_close.
    `traded`: the option symbols the agents traded in the session; `closed`: the close's settlement rows; `held`:
    each agent's options long before this close's exercises, {label: {symbol: contracts}}.

    Counted in out["violations"] (19(e): what the engine should have refused):
      exercises  an agent's exercise the contract doesn't allow: more contracts than it held long, or of an index
                 option (European)
      quotes     each row the agents traded or exercised outside the no-arbitrage bounds: the American `mid` under
                 its intrinsic value or under Black's European at the row's own volatility, less the tolerance; bid
                 over ask
    Reported in out["legal_early"], not counted (19(e): a legal exercise that holding would have beaten is the
    agent's loss): an exercise before expiry off the pricer's boundary (the price at the close less the strike, a
    put's the strike less the price, under the tree's value of holding, `fair`, by more than the tolerance), or a
    call's not on the session before an ex-date (`_next_ex`)."""
    cache = {}
    ex = [r for r in closed if r.get("event") == "exercise" and r.get("agent") in held]
    for r in ex:
        sym = r["symbol"]
        root, xs, rk = sym.split(".", 2)
        if not xs.startswith("O") or rk[:1] not in ("C", "P"):
            out["violations"].append(f"{day} {r['agent']} exercised {sym}, not an American name option")
            continue
        if r["quantity"] > held[r["agent"]].get(sym, 0.0) + 1e-9:
            out["violations"].append(f"{day} {r['agent']} exercised {r['quantity']:g} of {sym}, holding "
                                     f"{held[r['agent']].get(sym, 0.0):g} long")
        if int(xs[1:]) <= r["session"]:
            continue                                        # at expiry: the engine's in-the-money rule
        out["early_exercises"] += 1
        right, strike = rk[0], float(rk[1:])
        why = []
        if right == "C" and _next_ex(e, root, day) != day + 1:
            why.append("a call not on an ex-date's eve")
        row = _option_row(e, sym, cache)
        if row is not None and row.get("fair") is not None:
            spot = float(r.get("reference") or row["index"])
            intrinsic = (spot - strike) if right == "C" else (strike - spot)
            gap = float(row["fair"]) - intrinsic
            out["max_boundary_gap"] = max(out["max_boundary_gap"], gap)
            if gap > AX1_TOL + AX1_TOL_REL * spot:
                why.append(f"off the boundary: intrinsic {intrinsic:.4f} under the tree's {float(row['fair']):.4f}")
        if why:
            out["n_legal_early"] += 1
            if len(out["legal_early"]) < 50:
                out["legal_early"].append(f"{day} {r['agent']} exercised {sym}: " + "; ".join(why))
    for sym in sorted(set(traded) | {r["symbol"] for r in ex}):
        row = _option_row(e, sym, cache)
        if row is None or row.get("mid") is None:
            continue
        out["quotes"] += 1
        right, strike, spot = row["right"], float(row["strike"]), float(row["index"])
        tol = AX1_TOL + AX1_TOL_REL * spot
        mid = float(row["mid"])
        intrinsic = max(0.0, (spot - strike) if right == "C" else (strike - spot))
        euro = _black(right, float(row["forward"]), strike, float(row.get("iv") or 0.0),
                      float(row["sessions_to_expiry"]) / YEAR, float(row.get("rate") or 0.0))
        out["max_euro_shortfall"] = max(out["max_euro_shortfall"], euro - mid)
        if mid < intrinsic - tol:
            out["violations"].append(f"{day} {sym} mid {mid:.4f} under its intrinsic value {intrinsic:.4f}")
        if mid < euro - tol:
            out["violations"].append(f"{day} {sym} American mid {mid:.4f} under the European {euro:.4f}")
        if row.get("bid") is not None and row.get("ask") is not None and row["bid"] > row["ask"]:
            out["violations"].append(f"{day} {sym} bid {row['bid']} over ask {row['ask']}")


def run_names(tf, params, seed, years, engine_factory=None, cash=CASH):
    """One history of the single-name option accounts and the pin trials
    (module note above): each account's net worth at every close from BURN,
    the rule trades' P&L, MG3's pairs, AX1's count and AX2's deliveries."""
    u = list(tf.Universe.random(P.ROSTER[0], seed=P.ROSTER[1]))
    e = engine_factory(seed) if engine_factory else tf.Engine(seed=seed, universe=u, model=params)
    P.require(e, "names")
    errs = _tf_errors(tf)
    burn = P.BURN
    adv = {x.ticker: float(x.avg_volume) for x in u}
    names = ("earnings_straddle", "covered_call", "hold_names", "short_strangle")
    acc = {}
    rec = {"seed": seed, "burn": burn, "options_listed": bool(e.lists_name_options),
           "options": {"rules": {}, "deliveries": [], "portfolios": {}}, "ax1": 0, "ax1_examples": [],
           "pins": [], "trades": {k: [] for k in OWN_ACCOUNT},
           "ax1_agents": {"early_exercises": 0, "quotes": 0, "max_boundary_gap": 0.0, "max_euro_shortfall": 0.0,
                          "violations": [], "n_legal_early": 0, "legal_early": []}}
    open_trades = {"own": [], "straddle": []}
    held_at_close = {}         # each agent's options at the last close: the session's trades are the change
    basket = []
    n_days = int(round(years * YEAR))
    for day in range(n_days):
        live = day > burn          # names list from the first close, so day > 0
        e.open_market()
        if live and not acc:
            for nm in names:
                acc[nm] = {"p": tf.Portfolio(cash=cash, owner=nm), "nw": [], "log": [], "margin": None,
                           "watch": MarginWatch()}
            basket = _largest(e, u, NAME_BASKET)
        if live:
            for a in acc.values():
                a["p"].collect_dividends(e)
                a["p"].settle_open(e)
                a["watch"].after_open(a["p"], day, e)
            for tr in open_trades["own"]:
                tr["p"].collect_dividends(e)
                tr["p"].settle_open(e)
        e.run_session(9, 30, 3, TRADE_TICK)
        if live:
            _name_rules(tf, e, acc, basket, day, open_trades, rec, errs)
            if calendar_monthly_expiry(day) and day + 1 < n_days:
                for t in basket[:2]:
                    x = _first_expiry(e, t, day)
                    if x == day:
                        try:
                            rec["pins"].append(pin_trial(e, tf, t, x, adv[t], errs))
                        except Exception as ex:          # noqa: BLE001  a trial that cannot run is noted
                            rec["pins"].append({"ticker": t, "expiry": x, "error": str(ex)[:200]})
        flows = {}
        for q in [a["p"] for a in acc.values()] + [tr["p"] for tr in open_trades["own"]]:
            for t, (b, s_) in q.pending_flow().items():
                x = flows.setdefault(t, [0.0, 0.0])
                x[0] += b; x[1] += s_
            q.clear_flow()
        e.run_session(9, 30 + TRADE_TICK, 3, 390 - TRADE_TICK,
                      **({"fills": {k: tuple(v) for k, v in flows.items()}} if flows else {}))
        before = {nm: (a["p"].cash, {t: a["p"].quantity_of(t) for t in e.tickers}) for nm, a in acc.items()}
        rate = float(e.macro_fields["federal_funds_rate"])
        for tr in open_trades["own"]:
            tr["p"].accrue(e)
            tr["grown"] *= 1.0 + rate / 252.0
        own_before = [(tr, tr["p"].cash, {t: tr["p"].quantity_of(t) for t in e.tickers}) for tr in open_trades["own"]]
        e.close_market()
        rec.setdefault("price_crc", []).append(zlib.crc32(e.prices()))   # each close's prices, for pairing arms
        if e.lists_name_options:
            v = e.exercise_violations()
            rec["ax1"] += len(v)
            rec["ax1_examples"] += v[:3]
            ports = [a["p"] for a in acc.values()] + [tr["p"] for tr in open_trades["own"]]
            traded = sorted({s_ for q in ports for s_, o in q.options.items()
                             if o.quantity != held_at_close.get(q.owner, {}).get(s_, 0.0)}
                            | {s_ for q in ports for s_ in held_at_close.get(q.owner, {}) if s_ not in q.options})
            ax1_agents(e, day, traded, e.settlements(int(e.day_count) - 1),
                       {q.owner: {s_: o.quantity for s_, o in q.options.items() if o.quantity > 0} for q in ports},
                       rec["ax1_agents"])
        closed = e.settlements(int(e.day_count) - 1) if own_before else []
        for tr, cash0, held0 in own_before:
            q = tr["p"]
            q.settle_close(e)
            rows = [r for r in closed if r.get("agent") == q.owner and r.get("event") in ("exercise", "assignment")]
            if rows:
                rec["options"]["deliveries"].append({
                    "account": q.owner, "day": day,
                    "shares_expected": sum(abs(sum(r["shares"] for r in rows if r["root"] == t)) for t in e.tickers),
                    "shares_moved": sum(abs(q.quantity_of(t) - held0[t]) for t in e.tickers),
                    "cash_expected": sum(r["cash"] for r in rows), "cash_moved": q.cash - cash0})
        for nm, a in acc.items():
            p = a["p"]
            p.settle_close(e)
            rows = [r for r in e.settlements(int(e.day_count) - 1)
                    if r.get("agent") == nm and r.get("event") in ("exercise", "assignment")]
            if rows:
                cash0, held0 = before[nm]
                moved_shares = sum(abs(p.quantity_of(t) - held0[t]) for t in e.tickers)
                expected_shares = sum(abs(sum(r["shares"] for r in rows if r["root"] == t)) for t in e.tickers)
                rec["options"]["deliveries"].append({
                    "account": nm, "day": day, "shares_expected": expected_shares, "shares_moved": moved_shares,
                    "cash_expected": sum(r["cash"] for r in rows), "cash_moved": p.cash - cash0})
            nw = p.net_worth(e)
            # MG3 reads the account that holds options alone, so a session's
            # value change is the options'.
            # MG3's pair is read only on a session the account began with no
            # shares (an assignment's delivery is closed at the next
            # TRADE_TICK; until then the shares' move, which the option scan
            # does not margin, would be read as the options').
            if nm == "short_strangle" and a["nw"] and a["margin"] and p.options and not a.get("shares"):
                rec["options"]["portfolios"].setdefault(nm, []).append((nw - a["nw"][-1], a["margin"]))
            if nm == "short_strangle":
                _name_pairs(rec, a, p, e)
            a["margin"] = p.margin_requirement(e) if p.options else None
            a["shares"] = any(x.quantity for x in p.positions.values())
            a["watch"].after_close(p, e, day, nw)
            a["nw"].append(nw)
        held_at_close = {q.owner: {s_: o.quantity for s_, o in q.options.items()}
                         for q in [a["p"] for a in acc.values()] + [tr["p"] for tr in open_trades["own"]]}
    rules = rec["options"]["rules"]
    for nm, r in XD5_RULES.items():
        if r["kind"] == "c10" and nm in acc:
            a = acc[nm]
            base = acc[r["hold"]] if r["hold"] else None
            ex = 100 * (ann_log(a["nw"], cash) - (ann_log(base["nw"], cash) if base else 0.0))
            rules[nm] = {"kind": "c10", "family": "name", "excess": ex}
        elif nm in OWN_ACCOUNT:
            rules[nm] = {"kind": "lose", "family": "name", "pnl": [t["pnl"] for t in rec["trades"][nm]]}
        elif nm == "expiry_pin":
            rules[nm] = {"kind": "lose", "family": "name", "pnl": [t["pnl"] for t in rec["pins"] if "pnl" in t]}
    for nm in XD4_PARITY:
        rules[nm] = {"kind": "lose", "family": "index", "pnl": [t["pnl"] for t in rec["trades"][nm]]}
    rec["accounts"] = {nm: {"nw": a["nw"], "order_errors": a["log"][:20], "n_order_errors": len(a["log"]),
                            "deliveries": len(a["p"].deliveries), "margin": a["watch"].record()}
                       for nm, a in acc.items()}
    return rec


def calendar_monthly_expiry(day):
    """Whether session `day` is a monthly option expiry (session 15 of a
    21-session month)."""
    return day % MONTH == 14


def _name_rules(tf, e, acc, basket, day, open_trades, rec, errs):
    """The single-name rules' trades at TRADE_TICK of session `day`."""
    # earnings straddles: bought the session before a reaction session, sold the session after it
    a = acc["earnings_straddle"]
    p = a["p"]
    for leg in [x for x in open_trades["straddle"] if x["sell_on"] == day]:
        for sym, q in leg["legs"]:
            if abs(p.quantity_of(sym)) > 0:
                _execute(p, e, sym, -p.quantity_of(sym), errs, a["log"])
        open_trades["straddle"].remove(leg)
    for ev in e.earnings_calendar(2):
        if ev["sessions_ahead"] != 1 or ev["ticker"] not in e.name_option_roots():
            continue
        t = ev["ticker"]
        x = _first_expiry(e, t, ev["session"] + 1)
        if x is None:
            continue
        rows = _strike_rows(e, t, x)
        call = _nearest_row(rows["C"], _price_of(e, t))
        put = next((r for r in rows["P"] if r["strike"] == call["strike"]), None)
        if put is None or not call.get("ask") or not put.get("ask"):
            continue
        n = math.floor(NAME_ALLOC * p.net_worth(e) / (100.0 * (call["ask"] + put["ask"])))
        if n < 1:
            continue
        legs = [(call["symbol"], n), (put["symbol"], n)]
        for sym, q in legs:
            _execute(p, e, sym, q, errs, a["log"])
        open_trades["straddle"].append({"legs": legs, "sell_on": ev["session"] + 1})
    # the held lots, and the covered calls written on them
    for nm in ("hold_names", "covered_call"):
        a = acc[nm]
        p = a["p"]
        for t in basket:
            lots = math.floor(p.net_worth(e) / len(basket) / (100.0 * _price_of(e, t)))
            have = p.quantity_of(t)
            if have <= 0 and lots > 0:
                _execute(p, e, t, 100.0 * lots - have, errs, a["log"])
            if nm == "covered_call":
                held = math.floor(p.quantity_of(t) / 100.0)
                written = -sum(o.quantity for s_, o in p.options.items() if s_.startswith(t + ".O"))
                if held > written:
                    x = _first_expiry(e, t, day)
                    if x is None:
                        continue
                    rows = _strike_rows(e, t, x)["C"]
                    above = [r for r in rows if r["strike"] > _price_of(e, t)]
                    if above and above[0].get("bid"):
                        _execute(p, e, above[0]["symbol"], -(held - written), errs, a["log"])
    # the trades in portfolios of their own: closed the session after their
    # expiry (the parity packages) or on their sell day (the capture)
    log = rec.setdefault("own_log", [])
    for tr in [x for x in open_trades["own"] if x["close_on"] <= day]:
        rec["trades"][tr["rule"]].append(_trade_close(e, tr, day, errs, log))
        open_trades["own"].remove(tr)
    held = {tr["rule"] for tr in open_trades["own"]}
    # the parity packages, monthly, each on names of its own so no package
    # holds the other side of another's contract (a reversal's long call would
    # otherwise be the conversion's counterparty, and never exercise it):
    # conversions on the first two basket names, reversals on the next two,
    # boxes on the fifth; and on the index
    if day % MONTH == 15:
        for i, t in enumerate(basket[:5]):
            x = _first_expiry(e, t, day + 5)
            if x is None:
                continue
            rows = _strike_rows(e, t, x)
            price = _price_of(e, t)
            call = _nearest_row(rows["C"], price)
            k = next((i for i, r in enumerate(rows["C"]) if r["strike"] == call["strike"]), None)
            put = next((r for r in rows["P"] if r["strike"] == call["strike"]), None)
            if put is None or k is None or k + 1 >= len(rows["C"]):
                continue
            call2 = rows["C"][k + 1]
            put2 = next((r for r in rows["P"] if r["strike"] == call2["strike"]), None)
            scale = 3.0 * 100.0 * price
            packages = {}
            if i < 2:
                packages["conversion"] = [(t, 100.0), (put["symbol"], 1.0), (call["symbol"], -1.0)]
            elif i < 4:
                packages["reversal"] = [(t, -100.0), (put["symbol"], -1.0), (call["symbol"], 1.0)]
            elif put2 is not None:
                packages["box"] = [(call["symbol"], 1.0), (call2["symbol"], -1.0), (put2["symbol"], 1.0),
                                   (put["symbol"], -1.0)]
            for rule, legs in packages.items():
                tr = _trade_open(tf, e, rule, t, day, legs, scale, errs, log, close_on=x + 1, expiry=x,
                                 strike=call["strike"], strike2=call2["strike"], rate=float(call["rate"]),
                                 dividends=float(call["dividends"]), price=price)
                if tr:
                    open_trades["own"].append(tr)
        xs = sorted({c["expiry"] for c in e.contracts() if c.get("root") == "IDX" and c.get("kind") == "option"
                     and c["expiry"] >= day + 5})
        if xs and "index_box" not in held:
            x = xs[0]
            ch = e.chain("IDX", x)
            level = float(e.index_level["level"]) if isinstance(e.index_level, dict) else float(e.index_level)
            C = sorted((r for r in ch if r["right"] == "C"), key=lambda r: r["strike"])
            Pt = {r["strike"]: r for r in ch if r["right"] == "P"}
            k = min(range(len(C)), key=lambda i: abs(C[i]["strike"] - level))
            if k + 1 < len(C) and C[k]["strike"] in Pt and C[k + 1]["strike"] in Pt:
                c1, c2, p1, p2 = C[k], C[k + 1], Pt[C[k]["strike"]], Pt[C[k + 1]["strike"]]
                long_box = [(c1["symbol"], 1.0), (c2["symbol"], -1.0), (p2["symbol"], 1.0), (p1["symbol"], -1.0)]
                for rule, legs in (("index_box", long_box), ("index_box_short", [(s_, -q) for s_, q in long_box])):
                    tr = _trade_open(tf, e, rule, "IDX", day, legs, 3.0 * 100.0 * level, errs, log, close_on=x + 1,
                                     expiry=x, strike=c1["strike"], strike2=c2["strike"])
                    if tr:
                        open_trades["own"].append(tr)
    # dividend capture: the deep call bought on an ex-date's eve, exercised at the close, the shares sold the next session
    for t in e.name_option_roots():
        nxt = _next_ex(e, t, day)
        if nxt != day + 1:
            continue
        x = _first_expiry(e, t, day + 2)
        if x is None:
            continue
        rows = _strike_rows(e, t, x)["C"]
        deep = rows[0]
        if not deep.get("ask") or deep["strike"] >= _price_of(e, t):
            continue
        tr = _trade_open(tf, e, "dividend_capture", t, day, [(deep["symbol"], 1.0)], 3.0 * 100.0 * _price_of(e, t),
                         errs, log, close_on=day + 1, symbol=deep["symbol"])
        if tr is None:
            continue
        try:
            tr["p"].exercise(e, deep["symbol"], 1.0)
        except errs as ex:
            log.append(f"exercise {deep['symbol']}: {ex}"[:200])
        open_trades["own"].append(tr)
    # the short strangles, written monthly on the basket, sized to half the net worth in initial margin
    a = acc["short_strangle"]
    p = a["p"]
    # the account holds options alone: shares an assignment delivered are closed here, at the next TRADE_TICK
    for t, x in list(p.positions.items()):
        if x.quantity:
            _execute(p, e, t, -x.quantity, errs, a["log"])
    if day % MONTH == 15 and not p.options:
        for t in basket:
            x = _first_expiry(e, t, day + 5)
            if x is None:
                continue
            rows = _strike_rows(e, t, x)
            price = _price_of(e, t)
            call, put = _nearest_row(rows["C"], price, PIN_STEPS), _nearest_row(rows["P"], price, -PIN_STEPS)
            m = e.option_margin([(call["symbol"], -1.0), (put["symbol"], -1.0)]) or 0.0
            if m <= 0 or not call.get("bid") or not put.get("bid"):
                continue
            n = math.floor(0.5 * p.net_worth(e) / len(basket) / m)
            for r in (call, put):
                k = min(n, math.floor(pin_size(e, r["symbol"])))   # at most the position limit
                if k > 0:
                    _execute(p, e, r["symbol"], -float(k), errs, a["log"])
        # the leverage written: the short legs' notional (contracts x 100 x the name's price) over net worth
        nw_ = p.net_worth(e)
        if p.options and nw_ > 0:
            notional = sum(abs(o.quantity) * o.multiplier * _price_of(e, s_.split(".")[0]) for s_, o in p.options.items())
            rec["options"].setdefault("strangle_writes", []).append(notional / nw_)


def _name_pairs(rec, a, p, e):
    """MG3 per portfolio as FINRA 4210(g)(8)(A) groups one (one name's options): at each close, each name's
    options held, their value at the dealer's mids and their margin (`Engine.option_margin` on that name's legs);
    a pair (the value's change, the margin the close before) for each name held unchanged through a session the
    account began with no shares (design ptv22/optmargin, RULE.md section 4)."""
    by = {}
    for s_, o in p.options.items():
        if o.quantity:
            by.setdefault(s_.split(".")[0], {})[s_] = o.quantity
    now = {}
    for n_, legs in by.items():
        value = sum(p.options[s_].value(p._option_price(e, p.options[s_])) for s_ in legs)
        now[n_] = (legs, value, e.option_margin(list(legs.items())) or 0.0)
    prev = a.get("by_name")
    if prev and not a.get("shares"):
        for n_, (legs, v0, m0) in prev.items():
            if m0 and n_ in now and now[n_][0] == legs:
                rec["options"].setdefault("name_portfolios", []).append((now[n_][1] - v0, m0))
    a["by_name"] = now


def _next_ex(e, ticker, day):
    """`ticker`'s next ex-date after session `day`, as the market knows it
    then: the declared ex-dates of `Engine.distributions()` (each public
    from its declaration, 21 sessions before), else its 63-session phase."""
    try:
        import pyarrow as pa
        t = pa.table(e.distributions()).to_pylist()
        ix = {x: i for i, x in enumerate(e.tickers)}
        days = [r["day"] for r in t if r.get("instrument_id") == ix.get(ticker)
                and r["day"] > day and r.get("declared_day", day) <= day]
        if days:
            return min(days)
    except Exception:          # noqa: BLE001  no pyarrow, or an engine without the table
        pass
    return _ex_from_phase(ticker, day)


def _ex_from_phase(ticker, day):
    """The name's next ex-date after `day` from its 63-session phase: the
    dividend calendar is a hash of the ticker (FNV-1a 64 of its bytes, mod
    63; tradefloor's market::dividends::phase_for), with no draw."""
    h = 0xcbf29ce484222325
    for b in ticker.encode():
        h ^= b
        h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    phase = h % 63
    k = (day - phase) % 63
    return day + 63 - k


def names_rows(recs, realj=None):
    """XD5, MG3, AX2 (option_rows) and AX1 from the `names` part."""
    rows, rep = {}, {}
    if not recs:
        return rows, rep
    r, p = option_rows(recs, realj)
    rows.update(r); rep.update(p)
    # MG2 on the option accounts (the four standing accounts; a record from before the watch carries none)
    m = [(h["seed"], nm, a["margin"]) for h in recs for nm, a in h.get("accounts", {}).items() if a.get("margin")]
    if m:
        # draft item 23 (2026-10-27): an unmet call fails only when its liquidation leaves risk held (a future, a short
        # option, or a long option its book would still buy); a long option no book bids is reported, not counted.
        # A record from a reader before item 23 carries no such count: MG2 is then not read, never read the old way.
        if all("n_failures_risk" in x for _, _, x in m):
            rows["MG2_options"] = sum(x["n_failures_risk"] for _, _, x in m)
        rep["MG2_options"] = {**{k: sum(x[k] for _, _, x in m) for k in ("calls", "met", "unmet", "liquidations",
                                                                       "below_zero")},
                              "registered_count": sum(x["n_failures"] for _, _, x in m),
                              "no_bid_left": sum(x.get("n_no_bid_left", 0) for _, _, x in m),
                              "failures": [(sd, nm) + tuple(f) for sd, nm, x in m for f in x.get("failures_risk", [])[:3]][:10],
                              "no_bid": [(sd, nm) + tuple(f) for sd, nm, x in m for f in x.get("no_bid_left", [])[:3]][:10],
                              "read": "item 23 (no risk left)" if "MG2_options" in rows else "NOT READ: a record before item 23"}
    # AX1, "the dealer or an agent" (draft item 19(b)): the engine's dealer read plus the agents' side
    ag = [h["ax1_agents"] for h in recs if "ax1_agents" in h]
    rows["AX1"] = sum(h["ax1"] for h in recs) + sum(len(a["violations"]) for a in ag)
    rep["AX1"] = {"examples": [x for h in recs for x in h["ax1_examples"]][:10],
                  "dealer": sum(h["ax1"] for h in recs)}
    if ag:
        rep["AX1"]["agents"] = {"violations": sum(len(a["violations"]) for a in ag),
                                "early_exercises": sum(a["early_exercises"] for a in ag),
                                "quotes": sum(a["quotes"] for a in ag),
                                "max_boundary_gap": max(a["max_boundary_gap"] for a in ag),
                                "max_euro_shortfall": max(a["max_euro_shortfall"] for a in ag),
                                "examples": [x for a in ag for x in a["violations"]][:10],
                                "legal_early_reported": sum(a.get("n_legal_early", 0) for a in ag),
                                "legal_early_examples": [x for a in ag for x in a.get("legal_early", [])][:10]}
    rep["names"] = {"pins": sum(len(h["pins"]) for h in recs),
                    "conversions": sum(len(h["trades"]["conversion"]) for h in recs),
                    "dividend_captures": sum(len(h["trades"]["dividend_capture"]) for h in recs),
                    "deliveries": sum(len(h["options"]["deliveries"]) for h in recs)}
    return rows, rep


# ----------------------------------------------------------------- dispatch

RUN = {"traded": run_traded, "push": run_push, "metaorder": run_metaorder, "names": run_names}

#: Pass rules the grade applies to these rows' values: XD1, XD2, XD4 and XD5
#: are the number of rules that break their bound (C10a's or C10c's, an OB
#: band, VF5's band negated, or "loses"), so each passes at 0; MG2, MG3, XD3,
#: AX2 and IF4 are read against their stated bounds.
COUNT_ROWS = ("XD1", "XD2", "XD4", "XD5")


def part_rows(adir):
    rows, rep = {}, {}
    realj = None
    for part, fn in (("traded", traded_rows), ("push", push_rows), ("metaorder", metaorder_rows),
                     ("names", names_rows)):
        recs = P._load(adir, part)
        if recs:
            r, p = fn(recs, realj) if part in ("traded", "names") else fn(recs)
            rows.update(r); rep.update(p)
            rep[f"{part}_histories"] = len(recs)
    return rows, rep


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "measure":
        if "--parts" not in argv:
            argv += ["--parts", "traded,push,metaorder"]
        if "--switches" not in argv:
            argv += ["--switches", "phase1"]
    return P.main(argv)


if __name__ == "__main__":
    main()
