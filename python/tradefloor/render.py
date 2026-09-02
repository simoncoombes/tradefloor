"""Turn an allowlisted observation payload into the text an agent reads.

`P6-observation-invariance.md` (`docs/design/`, `feat/programme-design`)
names the question this module exists to ask: how much of what an agent
decides is the market, and how much is how the market was described to it.
Four adapters already turn `integrations.common.serialize_observation` (or
FinRobot's own copy of it, `integrations.finrobot.observe`) into text, each
its own way -- FinRobot writes prose, LangGraph and PydanticAI dump sorted
JSON, OpenAI Agents sends the same JSON as a second message. A `Renderer`
is the seam that lets one payload be shown four ways, or the four adapters
be handed one renderer, and the decisions compared.

## What a renderer never sees

A :class:`Renderer` takes the payload and nothing else. Not the
`Observation`, not the engine. `serialize_observation` already drew the
line between what an agent may see and what only the simulator knows;
handing a renderer anything beyond the payload it was given would let a
formatting choice reopen that boundary, and the allowlist test would not
see it happen, because it inspects the payload the serializer built, not
what a renderer read afterwards.

## What stays outside a renderer

A mandate, a brief, an "Objective" section: whatever an adapter tells its
framework about the TASK rather than about the MARKET. Two adapters
concatenate that text onto the rendered payload (FinRobot, LangGraph) and
two send it as a separate message (PydanticAI, OpenAI Agents), and a
renderer that took a position on which would be deciding something that is
each adapter's own choice, not this module's. A :class:`Renderer` renders
`payload`; the instructions stay where each adapter already keeps them.

## Two renderers, and why there are two

:class:`JSONRenderer` is what LangGraph, PydanticAI and OpenAI Agents send
today: `payload`, `json.dumps`-ed with sorted keys. :class:`TextRenderer`
is what FinRobot sends today, generalised over the four axes
`P6-observation-invariance.md` studies -- `detail`, `units`, `order` and
`language` -- so the same knobs that vary FinRobot's prompt can be turned
on any adapter's. Neither is privileged by the :class:`Renderer` protocol;
an adapter's default is whichever reproduces what it already sends, and
`invariance` takes any object with `render` and `key`.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, Sequence, runtime_checkable

from ._core import ValidationError

#: The values :class:`TextRenderer` accepts for `units`, `order` and
#: `language`. Read by the constructor's validation and by :meth:`key`.
UNITS = ("usd", "bps")
ORDER = ("roster", "alphabetical", "by_position")
LANGUAGE = ("en", "fr")


@runtime_checkable
class Renderer(Protocol):
    """What every adapter's `renderer=` argument, and `invariance`, accept.

    Two methods, and nothing else is assumed. `render` turns one
    allowlisted payload into the text an agent reads; `key` names the exact
    configuration that produced it, stable across calls, so a transcript
    and an `Invariance` table can say which renderer is which without
    printing every argument beside it.

    `@runtime_checkable` makes `isinstance` check method PRESENCE, not
    signature -- enough to give a caller who passes the wrong kind of
    object a clear refusal instead of an `AttributeError` three calls deep
    inside an adapter.
    """

    def render(self, payload: dict[str, Any]) -> str:
        ...

    def key(self) -> str:
        ...


class JSONRenderer:
    """`payload`, as canonical JSON. `key()` is `"json"`.

    Sorted keys and a two-space indent: what LangGraph's, PydanticAI's and
    OpenAI Agents' current prompts already send, because a replay key is a
    digest of this text and an unordered dump would give the same market
    two keys depending on how a dict happened to be built. Every value
    `serialize_observation` emits -- numbers, strings, `None`, and
    containers of those -- already round-trips through `json.dumps`, so
    this needs no `default=` hook; a payload that ever did would be an
    allowlist defect, not a rendering one.
    """

    def render(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, indent=2, sort_keys=True)

    def key(self) -> str:
        return "json"

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, JSONRenderer)

    def __hash__(self) -> int:
        return hash("JSONRenderer")

    def __repr__(self) -> str:
        return "JSONRenderer()"


# -- TextRenderer: labels ----------------------------------------------------
#
# Every EN string below is copied character for character from
# `integrations.finrobot.render`, `_asset_block` and `_universe_block` --
# not retyped from memory -- because `TextRenderer()` (its all-default
# construction) is what every adapter that concatenates its own
# instructions (FinRobot, LangGraph) falls back to, and the fixture-key
# tests replay committed recordings keyed on this exact text. FR is this
# module's own translation of the same labels; no fixture is keyed on it,
# so nothing outside this file constrains its wording.

_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "title": "SIMULATED MARKET",
        "day_step": "Day {day}, decision step {step}.",
        "macro": "Macro",
        "assets": "Assets",
        "sectors": "Sectors",
        "universe": "Universe",
        "detail": "Detail",
        "portfolio": "Portfolio",
        "positions": "Positions:",
        "none": "none",
        "shares": "shares",
        "not_available": "not available",
        "price": "  price",
        "price_bps": "  price, change vs prev close",
        "return_1d": "  return, 1 day",
        "return_5d": "  return, 5 days",
        "volatility": "  step volatility",
        "bid_ask": "  bid / ask",
        "avg_daily_volume": "  avg daily volume",
        "position": "  your position",
        "max_order_shares": "  max order this step",
        "cash": "cash",
        "net_worth": "net worth",
        "gross_exposure": "gross exposure",
        "max_leverage": "max leverage",
        "buying_power": "buying power",
        "col_symbol": "symbol",
        "col_sector": "sector",
        "col_price": "price",
        "col_price_bps": "px chg",
        "col_5d": "5d",
        "col_position": "position",
        "col_max_order": "max order",
        "col_names": "names",
        "col_held": "held",
        "col_exposure": "exposure",
        "universe_intro": ("All {n} symbols below are tradable. A dash "
                          "means the figure is not available yet."),
        "universe_note": ("Full detail for your holdings and for a "
                         "standing panel follows the table."),
        "detail_intro": ("{shown} of {n} symbols, being every name you "
                        "hold plus a standing panel."),
    },
    "fr": {
        "title": "MARCHE SIMULE",
        "day_step": "Jour {day}, etape de decision {step}.",
        "macro": "Contexte macro",
        "assets": "Actifs",
        "sectors": "Secteurs",
        "universe": "Univers",
        "detail": "Detail",
        "portfolio": "Portefeuille",
        "positions": "Positions :",
        "none": "aucune",
        "shares": "titres",
        "not_available": "non disponible",
        "price": "  prix",
        "price_bps": "  prix, variation vs cloture precedente",
        "return_1d": "  performance, 1 jour",
        "return_5d": "  performance, 5 jours",
        "volatility": "  volatilite (pas de temps)",
        "bid_ask": "  achat / vente",
        "avg_daily_volume": "  volume quotidien moyen",
        "position": "  votre position",
        "max_order_shares": "  ordre maximal ce pas",
        "cash": "liquidites",
        "net_worth": "valeur nette",
        "gross_exposure": "exposition brute",
        "max_leverage": "levier maximal",
        "buying_power": "capacite d'achat",
        "col_symbol": "symbole",
        "col_sector": "secteur",
        "col_price": "prix",
        "col_price_bps": "var prix",
        "col_5d": "5j",
        "col_position": "position",
        "col_max_order": "ordre max",
        "col_names": "noms",
        "col_held": "detenus",
        "col_exposure": "exposition",
        "universe_intro": ("Les {n} symboles ci-dessous sont tous "
                          "negociables. Un tiret signifie que la valeur "
                          "n'est pas encore disponible."),
        "universe_note": ("Le detail complet de vos positions et d'un "
                         "panneau permanent suit le tableau."),
        "detail_intro": ("{shown} symboles sur {n}, soit chaque nom "
                        "detenu et un panneau permanent."),
    },
}

#: The compact universe table's column widths, fixed rather than measured
#: off the data -- see `integrations.finrobot._ROW`, which this mirrors --
#: so the rendering of one market does not shift when another market has a
#: longer ticker.
_ROW = "{symbol:<8} {sector:<24}{price:>13}{ret:>10}{position:>16}{cap:>16}"


def _num(value: Any) -> str:
    if value is None:
        return "not available"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _money(value: Any) -> str:
    return "not available" if value is None else f"{value:,.2f}"


def _pct(value: Any) -> str:
    return "not available" if value is None else f"{value * 100:+.2f}%"


def _qty(value: Any) -> str:
    return "not available" if value is None else f"{value:,.0f}"


def _bps(value: Any) -> str:
    """`value` (a fraction, such as `return_1d`) in basis points."""
    return "not available" if value is None else f"{value * 10_000:+.2f} bps"


def _short_money(value: Any) -> str:
    return "-" if value is None else f"{value:,.2f}"


def _short_pct(value: Any) -> str:
    return "-" if value is None else f"{value * 100:+.2f}%"


def _short_qty(value: Any) -> str:
    return "-" if value is None else f"{value:,.0f}"


def _short_bps(value: Any) -> str:
    return "-" if value is None else f"{value * 10_000:+.2f}"


def _sector_rows(assets: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """A per-sector summary, computed from `assets` and nothing else.

    Byte-for-byte the arithmetic in `integrations.finrobot._sector_rows`:
    a name with no supplied sector lands in `"unclassified"`, and the
    five-day return is the equally weighted mean over the members that
    have one. Order-independent -- callers may hand this an already
    reordered asset list -- because it buckets and sums.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    for asset in assets:
        sector = asset["fundamentals"].get("sector") or "unclassified"
        buckets.setdefault(str(sector), []).append(asset)
    rows = []
    for sector in sorted(buckets):
        members = buckets[sector]
        returns = [m["return_5d"] for m in members
                  if m["return_5d"] is not None]
        rows.append({
            "sector": sector,
            "names": len(members),
            "held": sum(1 for m in members if m["position"]),
            "exposure": sum(m["position"] * m["price"] for m in members),
            "return_5d": (sum(returns) / len(returns)) if returns else None,
        })
    return rows


class TextRenderer:
    """The payload as prose: macro, then assets, then the portfolio.

    The all-default construction, `TextRenderer()`, reproduces
    `integrations.finrobot.render` character for character on every
    payload that carries no `detail` -- which is every payload
    `integrations.finrobot.observe` has ever built for an existing caller,
    since `detail` is `None` there too by default. That is what lets
    FinRobot's adapter, and any other adapter, fall back to this class
    without moving a single committed fixture's key.

    ``detail`` -- ``None`` renders a full block per asset, in ``order``.
    A sequence switches to the large-universe form: a sector summary
    (`_sector_rows`, computed from `payload["assets"]`), one compact row
    per symbol, and a full block for the union of `detail` and whichever
    symbols the payload's own `position` fields say are currently held --
    a position the agent cannot see the book for is a different question,
    so a held name is detailed whether or not it is in the standing panel.
    Both the union and the sector summary come from the payload alone, so
    the caller of :meth:`render` decides nothing by choosing when to call
    it: an adapter's *standing panel* is `detail`; *what is held* is
    already in `payload["assets"][i]["position"]`.

    ``units`` -- ``"usd"`` (the default) prints each asset's dollar price.
    ``"bps"`` prints its `return_1d` -- the return since the previous
    close `_window_return` already derives -- in basis points instead,
    on the same line and in the same table column a dollar price would
    take. Every other figure (the bid, the ask, the average volume, the
    order cap, the portfolio's own dollar figures) stays in the unit it
    already carries; there is no basis-point form of a share count. Where
    `return_1d` is `None` -- day zero, before the adapter has shown the
    agent a full day -- the bps line reads `"not available"`, exactly as
    the dollar line would for a price the payload never carried.

    ``order`` -- ``"roster"`` (the default) renders assets in the order
    `payload["assets"]` lists them, which is a no-op: it is what every
    payload has always been rendered in. ``"alphabetical"`` sorts by
    symbol. ``"by_position"`` sorts by the position's absolute size,
    largest first, symbol breaking a tie. It reorders every asset listing
    -- the full blocks, the compact table and the closing holdings line --
    and never the sector summary, which is grouped by sector rather than
    listed by symbol.

    ``language`` -- ``"en"`` (the default) or ``"fr"``. It translates the
    section headings and the fixed labels this module writes -- "Macro",
    "price", "cash" and their kind -- and nothing else: a ticker, a sector
    name, a macro field name and a mandate are the payload's or the
    adapter's, not this renderer's, and travel unchanged. Number
    formatting (the decimal point, the thousands comma) does not change
    with ``language`` either, for the same reason: it is not a label.

    Raises :class:`~tradefloor._core.ValidationError` at construction on
    an argument outside :data:`UNITS`, :data:`ORDER` or :data:`LANGUAGE`,
    rather than at the first :meth:`render` -- the same choice
    :class:`~tradefloor.counterfactual.World` makes for `on_refusal`.
    """

    __slots__ = ("detail", "units", "order", "language")

    def __init__(self, *, detail: Sequence[str] | None = None,
                units: str = "usd", order: str = "roster",
                language: str = "en") -> None:
        if units not in UNITS:
            raise ValidationError(
                f"units must be one of {UNITS}, got {units!r}")
        if order not in ORDER:
            raise ValidationError(
                f"order must be one of {ORDER}, got {order!r}")
        if language not in LANGUAGE:
            raise ValidationError(
                f"language must be one of {LANGUAGE}, got {language!r}")
        #: `None` (every asset in full) or a frozen, sorted, deduplicated
        #: tuple of symbols -- the standing panel. Sorted so that two
        #: renderers built from the same set in different order compare
        #: equal and produce the same `key()`, the same reason
        #: `FinRobotAdapter.panel` freezes its argument the same way.
        self.detail = (None if detail is None
                       else tuple(sorted({str(s) for s in detail})))
        self.units = units
        self.order = order
        self.language = language

    def key(self) -> str:
        """Names this exact configuration, for a transcript or a table.

        `text/{language}/{units}/{order}/{panel}`, where `panel` is
        `"full"` for `detail=None`, `"panel:none"` for an empty sequence,
        or `"panel:"` plus the sorted symbols otherwise. Every argument
        that changes :meth:`render`'s output changes this string, and nothing
        else does: two `TextRenderer` instances built from the same
        arguments, in any order for `detail`, produce the same key.
        """
        if self.detail is None:
            panel = "full"
        elif self.detail:
            panel = "panel:" + ",".join(self.detail)
        else:
            panel = "panel:none"
        return f"text/{self.language}/{self.units}/{self.order}/{panel}"

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, TextRenderer) and self.key() == other.key()

    def __hash__(self) -> int:
        return hash(self.key())

    def __repr__(self) -> str:
        return f"TextRenderer({self.key()!r})"

    # -- ordering -----------------------------------------------------------

    def _ordered(self, assets: Sequence[dict[str, Any]],
                ) -> list[dict[str, Any]]:
        if self.order == "roster":
            return list(assets)
        if self.order == "alphabetical":
            return sorted(assets, key=lambda a: a["symbol"])
        return sorted(assets,
                     key=lambda a: (-abs(a["position"]), a["symbol"]))

    # -- rendering ------------------------------------------------------

    def render(self, payload: dict[str, Any]) -> str:
        L = _LABELS[self.language]
        assets = self._ordered(payload["assets"])
        macro = payload["macro"]

        out = [
            L["title"],
            "",
            L["day_step"].format(day=payload["day"], step=payload["step"]),
            "",
            L["macro"],
            "-" * len(L["macro"]),
        ]
        for field in macro:
            out.append(f"{field:<22} {_num(macro.get(field))}")

        if self.detail is None:
            out += ["", L["assets"], "-" * len(L["assets"])]
            for asset in assets:
                out.append("")
                out += self._asset_block(asset, L)
        else:
            out += self._universe_block(payload, assets, L)

        book = payload["portfolio"]
        out += [
            "",
            L["portfolio"],
            "-" * len(L["portfolio"]),
            f"{L['cash']:<23}{_money(book['cash'])}",
            f"{L['net_worth']:<23}{_money(book['net_worth'])}",
            f"{L['gross_exposure']:<23}{_num(book['gross_exposure'])}x",
            f"{L['max_leverage']:<23}{_num(book['max_leverage'])}"
            + ("" if book["max_leverage"] is None else "x"),
            f"{L['buying_power']:<23}{_money(book['buying_power'])}",
            "",
            L["positions"],
        ]
        held = [a for a in assets if a["position"]]
        if not held:
            out.append(f"  {L['none']}")
        for asset in held:
            out.append(f"  {asset['symbol']:<8} {_qty(asset['position'])} "
                       f"{L['shares']}  "
                       f"({_money(asset['position'] * asset['price'])})")
        return "\n".join(out)

    def _price_line(self, asset: dict[str, Any]) -> tuple[str, str]:
        """The (label, value) for an asset's headline price line."""
        L = _LABELS[self.language]
        if self.units == "bps":
            return L["price_bps"], _bps(asset["return_1d"])
        return L["price"], _money(asset["price"])

    def _asset_block(self, asset: dict[str, Any],
                     L: dict[str, str]) -> list[str]:
        """One asset in full: the nine lines a detailed name has always
        got, `units` deciding only what the price line reads."""
        price_label, price_value = self._price_line(asset)
        out = [
            asset["symbol"],
            f"{price_label:<23}{price_value}",
            f"{L['return_1d']:<23}{_pct(asset['return_1d'])}",
            f"{L['return_5d']:<23}{_pct(asset['return_5d'])}",
            f"{L['volatility']:<23}{_pct(asset['volatility'])}",
            f"{L['bid_ask']:<23}{_money(asset['best_bid'])}"
            f" / {_money(asset['best_ask'])}",
            f"{L['avg_daily_volume']:<23}{_qty(asset['avg_daily_volume'])}",
            f"{L['position']:<23}{_qty(asset['position'])} {L['shares']}",
            f"{L['max_order_shares']:<23}"
            f"{_qty(asset['max_order_shares'])} {L['shares']}",
        ]
        for key, value in sorted(asset["fundamentals"].items()):
            out.append(f"  {key:<20} {_num(value)}")
        return out

    def _universe_block(self, payload: dict[str, Any],
                        assets: Sequence[dict[str, Any]],
                        L: dict[str, str]) -> list[str]:
        """The large-universe rendering: sectors, every symbol, some in
        full. `assets` is already in `order`; the sector summary is not,
        because it groups by sector rather than listing by symbol."""
        price_col = (L["col_price_bps"] if self.units == "bps"
                    else L["col_price"])
        out = ["", L["sectors"], "-" * len(L["sectors"]),
              f"{L['col_sector']:<24}{L['col_names']:>7}{L['col_held']:>7}"
              f"{L['col_exposure']:>18}{L['col_5d']:>10}"]
        for row in _sector_rows(payload["assets"]):
            out.append(f"{row['sector']:<24}{row['names']:>7,}"
                       f"{row['held']:>7,}{_money(row['exposure']):>18}"
                       f"{_short_pct(row['return_5d']):>10}")

        out += [
            "",
            L["universe"],
            "-" * len(L["universe"]),
            L["universe_intro"].format(n=len(assets)),
            L["universe_note"],
            "",
            _ROW.format(symbol=L["col_symbol"], sector=L["col_sector"],
                       price=price_col, ret=L["col_5d"],
                       position=L["col_position"],
                       cap=L["col_max_order"]),
        ]
        for asset in assets:
            price_cell = (_short_bps(asset["return_1d"]) if self.units == "bps"
                         else _short_money(asset["price"]))
            out.append(_ROW.format(
                symbol=asset["symbol"],
                sector=str(asset["fundamentals"].get("sector")
                          or "unclassified"),
                price=price_cell,
                ret=_short_pct(asset["return_5d"]),
                position=_short_qty(asset["position"]),
                cap=_short_qty(asset["max_order_shares"]),
            ))

        listed = {a["symbol"] for a in assets}
        held = {a["symbol"] for a in assets if a["position"]}
        panel = {s for s in self.detail if s in listed}
        shown_set = panel | held
        shown = [a for a in assets if a["symbol"] in shown_set]
        out += ["", L["detail"], "-" * len(L["detail"]),
               L["detail_intro"].format(shown=len(shown), n=len(assets))]
        for asset in shown:
            out.append("")
            out += self._asset_block(asset, L)
        return out
