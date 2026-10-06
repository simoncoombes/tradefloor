"""Which top-level names are API, and how far a caller may lean on each.

Every name reachable as ``tradefloor.X`` is listed here exactly once, in one
of four tiers. ``tests/test_api_surface.py`` fails when a top-level name is
added or removed without being listed, and ``tests/api_surface.txt`` is the
sorted record of the result, so a change to the surface shows up in review
as a change to that file. The documentation reads the same tuples. They are plain
literals, so ``ast.literal_eval`` reads them without importing the package.

STABLE
    The path the README and the documentation teach. Changes go through a
    deprecation first.
ADVANCED
    Real API for a narrower audience: result types, research tools,
    constants, ``version()``, and the submodules the import system binds on
    the package. Supported, with no tutorial of its own.
DEPRECATED
    Names on their way out. Each still resolves at the top level, through
    ``tradefloor.__getattr__``, and warns with a ``DeprecationWarning`` that
    names its home module, until it leaves no earlier than ``REMOVAL``.
    Empty since 0.10.0, which removed the ten engine internals that 0.9.0
    deprecated (``REMOVED``).
INTERNAL
    Reachable, but not API: the standard-library names ``__init__`` imports,
    and submodules that exist to serve the rest of the package. A submodule
    cannot warn, because importing it anywhere binds it on the package.
"""

from __future__ import annotations

#: The first release that may drop a ``DEPRECATED`` name from the top level.
#: None while nothing is deprecated.
REMOVAL: str | None = None

STABLE: tuple[str, ...] = (
    # markets
    "ArrowStream", "Engine", "Instrument", "Macro", "ModelParams", "Universe",
    "model_preset", "preset_names", "preset_record", "run_many",
    # agents and scoring
    "Agent", "Cancel", "History", "Limit", "Observation", "Portfolio",
    "Ranking", "Scorecard", "StrategySpec", "SPEC_VERSION", "baselines",
    "capture_ratio", "evaluate", "leaderboard", "rank", "reference_agents",
    "versus_buy_and_hold",
    # scenarios, forks and execution cost
    "Checkpoint", "Scenario", "ScenarioValidationError", "World", "branch",
    "compare", "counterfactual", "run_scenario", "tca",
    # realism and citing a result
    "RunManifest", "battery", "commit", "envelope", "facts", "fingerprint",
    "replay", "reveal", "sealed_battery",
    # errors and version
    "OrderError", "SandboxError", "ValidationError", "__version__",
)

ADVANCED: tuple[str, ...] = (
    # engine result and book types
    "EngineBatch", "FairValue", "Fill", "MatchResult", "News",
    "NewsImpact", "OrderBook", "PriceLevel", "SweepCost", "TickResult",
    # engine utilities
    "GameRng", "fair_value", "market_status", "sectors",
    # rate indices
    "RATE_SECTOR", "RATE_TICKERS", "bonds", "rate_specs",
    # evaluation detail
    "AgentRecord", "HiddenState", "MarketView", "PortfolioView", "Position",
    "capture_withheld", "oracle_is_ceiling", "preset_records", "sandbox",
    "spec", "sweep",
    # populated mode: background traders sharing the book
    "Population", "population",
    # counterfactual research
    "Agreement", "BoundaryMap", "Comparison", "Divergence", "Externality",
    "Flip", "Invariance", "Resample", "agree", "boundary", "externalities",
    "externality", "flip", "invariance", "map_boundaries", "resample",
    # scenarios
    "Firing", "Intervention", "TARGETS", "UNSUPPORTED_TARGETS",
    "interventions",
    # execution cost
    "Execution", "FlowImpact", "flow_impact",
    # trust, reproducibility and model study
    "BATTERY_VERSION", "Battery", "Cell", "DayLedger", "Explanation",
    "Fingerprint", "FingerprintComparison", "Node", "Verification", "atlas",
    "edgar", "explain", "loss", "manifest", "noise",
    # same value as __version__, kept for callers written before it existed
    "version",
    # rendering for language-model agents
    "JSONRenderer", "Renderer", "TextRenderer", "render",
    # submodules the import system binds on the package
    "checkpoint", "harness", "portfolio", "ranking", "records", "scenario",
)

#: Each deprecated name and the module to import it from.
DEPRECATED: dict[str, str] = {}

#: The engine internals 0.9.0 deprecated and 0.10.0 removed from the top
#: level, each with the module that still has it. Reading one through the
#: package raises an AttributeError that names that module.
REMOVED: dict[str, str] = {
    "MispricingState": "tradefloor._core",
    "apply_mispricing": "tradefloor._core",
    "characteristic_root_moduli": "tradefloor._core",
    "check_rate": "tradefloor._core",
    "crisis_epicentre_solve": "tradefloor._core",
    "crowd_adjusted_root_moduli": "tradefloor._core",
    "impulse_response": "tradefloor._core",
    "sector_daily_sigma": "tradefloor._core",
    "stationary_sigma": "tradefloor._core",
    "step_mispricing_daily": "tradefloor._core",
}

INTERNAL: tuple[str, ...] = (
    # standard-library names imported by __init__
    "Any", "Iterable", "Sequence", "annotations", "json",
    # helper submodules
    "universe_util", "yaml_subset",
)


#: Each top-level name and its tier.
#: Sorted by name. The surface test refuses a name listed in two tiers.
TIERS: dict[str, str] = dict(sorted(
    [(name, "stable") for name in STABLE]
    + [(name, "advanced") for name in ADVANCED]
    + [(name, "deprecated") for name in DEPRECATED]
    + [(name, "internal") for name in INTERNAL]
))


def deprecation_message(name: str) -> str:
    """What ``tradefloor.<name>`` says when a ``DEPRECATED`` name is read."""
    return (f"tradefloor.{name} is an engine internal and leaves the top "
            f"level no earlier than tradefloor {REMOVAL}. Import it from "
            f"{DEPRECATED[name]} instead.")


def removal_message(name: str) -> str:
    """What reading a ``REMOVED`` name through the package says."""
    return (f"tradefloor.{name} was an engine internal and left the top "
            f"level in tradefloor 0.10.0. Import it from {REMOVED[name]} "
            f"instead.")
