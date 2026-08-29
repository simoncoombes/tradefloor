"""Adapters that let somebody else's agent framework trade a Tradefloor market.

One module per framework, and each one is a shim. All of them split the work
the same way, so this subpackage is a place and not a file:

    the framework      interprets, reasons, decides
    Tradefloor         market, macro, execution, accounting, checkpoints,
                       forks, interventions, comparison

A framework never touches engine state. It receives an
:class:`~tradefloor.harness.Observation`, rendered into whatever it reads, and
returns a decision that this package validates before anything is executed.

## Why these are not in the top-level namespace

Every other module under ``tradefloor/`` is first-party engine surface, and
importing the package imports all of them. An adapter depends on a third
party whose release cadence, Python support and dependency tree belong to
somebody else, and it must never break ``import tradefloor`` for a user who
has never heard of that project. So the rules here are narrow and worth
stating.

- ``tradefloor/__init__.py`` does not import this subpackage, and this
  subpackage's ``__init__`` does not import its own modules. Reaching an
  adapter is always an explicit
  ``from tradefloor.integrations.finrobot import ...``.
- An adapter imports its framework INSIDE the function that needs it, never
  at module scope, and names the extra that installs it when the import
  fails. Replaying a recorded run should never require the framework.
- One optional extra per framework, named after it:
  ``pip install "tradefloor[finrobot]"``.

## Attribution

Each adapter targets a project this repository neither owns nor is
affiliated with. The module docstrings name the upstream project, its licence
and the version tested. None of those projects endorses this work.
"""

from __future__ import annotations

# Empty of imports on purpose. See the docstring: an adapter is reached by
# naming it, so `import tradefloor` alone can never touch a broken or
# uninstalled third-party dependency.

__all__: list[str] = []
