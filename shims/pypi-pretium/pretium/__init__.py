"""pretium is now tradefloor.

Versions of pretium through 0.4.3 are the real library and replay
exactly; pin one of them to reproduce a recorded result. This shim
exists so `import pretium` keeps working while you migrate: it
re-exports tradefloor and aliases its submodules under the old name.
New code should import tradefloor.
"""
import importlib
import pkgutil
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

# `import pretium.envelope` resolves through the import system, which
# consults sys.modules and never this module's __getattr__ -- so every
# tradefloor submodule is aliased there eagerly, and the alias IS the
# tradefloor module, one object under two names. Submodules that refuse
# to import (tradefloor.mcp without the mcp extra) are skipped; importing
# them under either name then fails with tradefloor's own message.
for _m in pkgutil.iter_modules(_tf.__path__):
    try:
        sys.modules[f"pretium.{_m.name}"] = importlib.import_module(
            f"tradefloor.{_m.name}"
        )
    except ImportError:
        pass


def __getattr__(name):
    mod = importlib.import_module(f"tradefloor.{name}")
    sys.modules[f"pretium.{name}"] = mod
    return mod
