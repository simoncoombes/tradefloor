"""pretium is now tradefloor.

Versions of pretium through 0.4.3 are the real library and replay
exactly; pin one of them to reproduce a recorded result. This 0.5.0
shim exists so `import pretium` keeps working while you migrate:
it re-exports tradefloor and registers its submodules under the old
name. New code should import tradefloor.
"""
import importlib
import sys
import warnings

import tradefloor as _tf

warnings.warn(
    "pretium is now tradefloor; `import tradefloor`. Versions <= 0.4.3 "
    "remain the real pretium and replay exactly when pinned.",
    DeprecationWarning,
    stacklevel=2,
)

globals().update({k: v for k, v in vars(_tf).items() if not k.startswith("_")})


def __getattr__(name):
    # `import pretium.envelope` and friends resolve to the tradefloor
    # module of the same name, registered under both names so a second
    # import returns the same object.
    mod = importlib.import_module(f"tradefloor.{name}")
    sys.modules[f"pretium.{name}"] = mod
    return mod
