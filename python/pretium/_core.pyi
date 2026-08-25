"""Type stubs for the compiled extension.

PyO3 generates `__text_signature__`, so `inspect.signature` and IDE parameter
hints already work. What it cannot generate is TYPES, so without this file a
type checker treats the whole package as `Any` and a library sitting inside a
larger research codebase gets no checking at its boundary.

Two conventions are encoded here that the runtime enforces and the types make
visible:

- Columnar reads return `bytes`, not arrays. They are raw little-endian f64,
  read with `numpy.frombuffer(buf, dtype="<f8")`. Typing them as `bytes` is
  honest about what crosses the boundary; calling them `ndarray` would imply a
  numpy dependency the core does not have.
- `Optional[float]` where the core distinguishes absence from zero. That is
  not decoration: an absent bond yield falls through to the policy rate while
  a zero yield is used as given, and the two produce different markets.
"""

from typing import Any, Iterable, Literal, Sequence

__version__: str

Side = Literal["buy", "sell"]
MarketStatusName = Literal["open", "pre_market", "after_hours", "closed"]
CycleName = Literal["expansion", "peak", "contraction", "trough", "recovery"]
Grain = Literal["tick", "day"]
ColumnField = Literal[
    "price", "previous_close", "previous_tick_price", "open", "high", "low",
    "volume", "avg_volume", "market_cap", "mispricing_s",
    "mispricing_s_prev_close", "mispricing_momentum", "last_daily_return",
    "maker_inventory", "garch_variance", "beta", "short_interest",
    "float_shares",
]
FactorName = Literal[
    "reversion", "momentum", "crowd_lean",
    "company_news", "order_flow_impact", "short_squeeze_effect", "random_noise",
]

class ValidationError(ValueError):
    """Construction input rejected at the boundary."""

class OrderError(ValueError):
    """An order rejected by a market rule."""

# ---------------------------------------------------------------------------
# Layer 1
# ---------------------------------------------------------------------------

class GameRng:
    def __init__(self, seed: int, sequence: int) -> None: ...
    def next_float(self) -> float: ...
    def next_normal(self) -> float: ...
    def next_int(self, min: int, max: int) -> int: ...
    def next_bool(self, p: float) -> bool: ...

class FairValue:
    fair_value: float
    target_pe: float
    sector_anchor_pe: float
    rate_adjustment: float
    qe_adjustment: float
    book_value_path: bool

def fair_value(
    *,
    eps: float | None = ...,
    sector: str,
    revenue_growth: float | None = ...,
    federal_funds_rate: float = ...,
    corporate_bond_yield: float | None = ...,
    qe_pe_boost: float | None = ...,
    book_value_per_share: float | None = ...,
) -> FairValue: ...

class MispricingState:
    s: float
    s_prev: float
    def __init__(self, s: float = ..., s_prev: float | None = ...) -> None: ...

def step_mispricing_daily(
    state: MispricingState, *, innovation: float = ..., shock: float = ...
) -> MispricingState: ...
def apply_mispricing(fair_value: float, s: float) -> float: ...
def characteristic_root_moduli(
    phi: float | None = ..., theta: float | None = ...
) -> tuple[float, float]: ...
def crowd_adjusted_root_moduli() -> tuple[float, float]: ...
def impulse_response(
    horizon_days: int, phi: float | None = ..., theta: float | None = ...
) -> list[float]: ...
def stationary_sigma(
    innovation_sigma: float, *, phi: float | None = ..., theta: float | None = ...
) -> float | None: ...

class PriceLevel:
    price: float
    quantity: float
    orders: int

class Fill:
    price: float
    quantity: float
    maker_order_id: str
    maker_id: str
    taker_id: str
    taker_side: str

class MatchResult:
    fills: list[Fill]
    unfilled: float
    average_price: float | None
    resting_order_id: str | None

class SweepCost:
    average_price: float
    worst_price: float
    filled: float

class OrderBook:
    company_id: str
    best_bid: float | None
    best_ask: float | None
    mid_price: float | None
    spread: float | None
    def __init__(self, company_id: str, last_price: float | None = ...) -> None: ...
    def post_limit(
        self, side: Side, price: float, quantity: float, *,
        owner: str, order_id: str | None = ...,
    ) -> str: ...
    def submit(
        self, side: Side, quantity: float, *, taker: str = ...,
        limit_price: float | None = ..., post_remainder: bool = ...,
        order_id: str | None = ...,
    ) -> MatchResult: ...
    def append_maker_level(
        self, side: Side, price: float, quantity: float, *, owner: str
    ) -> str | None: ...
    def sweep_cost(self, side: Side, quantity: float) -> SweepCost | None: ...
    def price_levels(self, side: Side, max_levels: int = ...) -> list[PriceLevel]: ...
    def depth(self, side: Side) -> float: ...
    def cancel_order(self, order_id: str) -> bool: ...
    def cancel_all_for(self, owner_id: str) -> int: ...

# ---------------------------------------------------------------------------
# Layer 2
# ---------------------------------------------------------------------------

class Instrument:
    """One tradable company.

    ``short_interest`` is a SHARE COUNT, not a fraction: the squeeze rule
    divides it by the float. Values strictly between 0 and 1 are refused for a
    company with a meaningful share count, because that is what a fraction
    looks like and the mistake is otherwise silent -- three hundredths of one
    share gives a ratio of 3e-11 and a squeeze that can never fire.
    """

    ticker: str
    sector: str
    initial_price: float
    shares_outstanding: float
    eps: float | None
    book_value_per_share: float | None
    revenue_growth: float | None
    avg_volume: float
    beta: float
    short_interest: float
    market_cap: float
    def __init__(
        self, ticker: str, sector: str, *, initial_price: float,
        shares_outstanding: float, eps: float | None = ...,
        book_value_per_share: float | None = ..., revenue_growth: float | None = ...,
        avg_volume: float = ..., beta: float = ..., short_interest: float = ...,
    ) -> None: ...

class Macro:
    vix: float
    federal_funds_rate: float
    corporate_bond_yield: float | None
    inflation_rate: float
    qe_pe_boost: float
    fear_greed_index: float
    cycle: str
    def __init__(
        self, *, vix: float = ..., federal_funds_rate: float = ...,
        corporate_bond_yield: float | None = ..., inflation_rate: float = ...,
        qe_pe_boost: float = ..., fear_greed_index: float = ...,
        cycle: CycleName = ...,
    ) -> None: ...

class News:
    ticker: str | None
    sector: str | None
    price_impact: float
    def __init__(
        self, *, ticker: str | None = ..., sector: str | None = ...,
        price_impact: float = ...,
    ) -> None: ...

class NewsImpact:
    ticker: str | None
    sector: str | None
    sectors: list[str]
    remaining_impact: float
    reversal_phase: bool
    def __init__(
        self, *, ticker: str | None = ..., sector: str | None = ...,
        sectors: Sequence[str] | None = ..., remaining_impact: float = ...,
        reversal_phase: bool = ...,
    ) -> None: ...

class TickResult:
    market_status: str
    draws_consumed: int
    active: int

class ArrowStream:
    num_rows: int
    num_batches: int
    columns: list[str]
    def __arrow_c_stream__(self, requested_schema: Any = ...) -> Any: ...

class ModelParams:
    """An immutable model coefficient set: a shipped preset or a named
    deviation from one. Parameters read as attributes."""
    fingerprint: str

    @staticmethod
    def from_preset(name: str = ..., **overrides: float) -> "ModelParams": ...
    @staticmethod
    def from_dict(values: dict[str, Any]) -> "ModelParams": ...
    @staticmethod
    def settable() -> list[str]: ...
    def to_dict(self) -> dict[str, Any]: ...
    def __getattr__(self, name: str) -> float: ...

class Engine:
    FACTORS: list[str]
    tickers: list[str]
    draws_consumed: int
    len: int
    macro_state: Macro
    model: ModelParams
    model_fingerprint: str
    model_params: dict[str, Any]
    order_log: list[dict[str, Any]]
    recorded_days: int
    recorded_book_rows: int
    session_ticks_written: int

    def __init__(
        self, *, seed: int, universe: Sequence[Instrument],
        macro_state: Macro | None = ...,
        model: str | ModelParams | None = ...,
    ) -> None: ...
    def __len__(self) -> int: ...

    def open_market(self) -> None: ...
    def close_market(self) -> None: ...
    def tick(
        self, hour: int, minute: int, day_of_week: int, *, volatility: float = ...,
        news: Sequence[News] | None = ...,
        news_impacts: Sequence[NewsImpact] | None = ...,
        order_flow: dict[str, tuple[float, float]] | None = ...,
    ) -> TickResult: ...
    def run_session(
        self, hour: int, minute: int, day_of_week: int, ticks: int, *,
        volatility: float = ..., close_at_end: bool = ...,
        news: Sequence[News] | None = ...,
        news_impacts: Sequence[NewsImpact] | None = ...,
        order_flow: dict[str, tuple[float, float]] | None = ...,
    ) -> int: ...
    def run_days(
        self, days: int, *, hour: int = ..., minute: int = ...,
        day_of_week: int = ..., ticks_per_day: int = ..., volatility: float = ...,
        record: bool = ..., first_day: int = ...,
    ) -> int: ...
    def run_until(
        self, *, ticker: str, above: float | None = ..., below: float | None = ...,
        max_ticks: int = ..., hour: int = ..., minute: int = ...,
        day_of_week: int = ..., volatility: float = ...,
    ) -> int | None: ...

    # Columnar reads. Raw little-endian f64 bytes, not arrays -- read with
    # numpy.frombuffer(buf, dtype="<f8"). Typed as bytes because that is what
    # crosses the boundary; calling it ndarray would imply a dependency the
    # core does not have.
    def prices(self) -> bytes: ...
    def column(self, field: ColumnField) -> bytes: ...
    def attribution(self, factor: FactorName) -> bytes: ...
    def session_prices(self) -> bytes: ...
    def session_volumes(self) -> bytes: ...
    def session_mispricing_s(self) -> bytes: ...

    def bars(
        self, *, day: int = ..., minutes: int | None = ..., grain: Grain | None = ...
    ) -> ArrowStream: ...
    def truth(self, *, day: int = ...) -> ArrowStream: ...
    def macro_table(self) -> ArrowStream: ...
    def book_table(self) -> ArrowStream: ...
    def state_snapshot(self) -> dict[str, Any]: ...
    def restore_state(self, snapshot: dict[str, Any]) -> None: ...
    def record(self, day: int) -> None: ...
    def clear_recording(self) -> None: ...
    def snapshot_book(
        self, *, day: int = ..., tick: int = ..., levels: int = ...
    ) -> int: ...

    def book(self, ticker: str) -> OrderBook: ...
    def list_instrument(self, instrument: Instrument) -> int: ...
    def delist(self, index: int) -> str: ...
    def index_of(self, ticker: str) -> int | None: ...
    def pin_macro(
        self, *, vix: float | None = ..., federal_funds_rate: float | None = ...,
        corporate_bond_yield: float | None = ..., inflation_rate: float | None = ...,
        qe_pe_boost: float | None = ..., fear_greed_index: float | None = ...,
        cycle: CycleName | None = ...,
    ) -> None: ...
    def draw_uniform(self) -> float: ...
    def draw_normal(self) -> float: ...
    def draws_by_stream(self) -> dict[str, int]: ...

class EngineBatch:
    seeds: list[int]
    tickers: list[str]
    draws_consumed: list[int]
    shape: tuple[int, int]
    model: ModelParams
    model_fingerprint: str
    def __init__(
        self, *, seeds: Sequence[int], universe: Sequence[Instrument],
        macro_state: Macro | None = ...,
        model: str | ModelParams | None = ...,
    ) -> None: ...
    def __len__(self) -> int: ...
    def open_market(self) -> None: ...
    def tick(
        self, hour: int, minute: int, day_of_week: int, *, volatility: float = ...
    ) -> None: ...
    def run_session(
        self, hour: int, minute: int, day_of_week: int, ticks: int, *,
        volatility: float = ...,
    ) -> None: ...
    def prices(self) -> bytes: ...
    def column(self, field: ColumnField) -> bytes: ...

# ---------------------------------------------------------------------------
# Module level
# ---------------------------------------------------------------------------

def sectors() -> list[str]: ...
def sector_volatility(sector: str) -> float: ...
def sector_daily_sigma(sector: str) -> float: ...
def random_instruments(n: int = ..., *, seed: int = ...) -> list[Instrument]: ...
def market_status(hour: int, minute: int, day_of_week: int) -> str: ...
def model_preset(name: str = ...) -> dict[str, Any]: ...
def check_rate(name: str, fraction: float) -> float: ...
def version() -> str: ...
def fixed_simulation_digest(
    *, size: int, universe_seed: int, seed: int, days: int, ticks: int,
    preset: str,
) -> str: ...
def fills_stream(
    day: Sequence[int], step: Sequence[int], tick: Sequence[int],
    instrument_id: Sequence[int],
    quantity: Sequence[float], price: Sequence[float],
    worst_price: Sequence[float], notional: Sequence[float],
) -> ArrowStream: ...
