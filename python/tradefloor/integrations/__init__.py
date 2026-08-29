"""Adapters that let somebody else's agent framework trade a Tradefloor market.

One module per framework, and each one is a shim rather than a feature. The
division of labour is the same in all of them and it is the reason this
subpackage exists as a place rather than as a file:

    the framework      interprets, reasons, decides
    Tradefloor         market, macro, execution, accounting, checkpoints,
                       forks, interventions, comparison

A framework never touches engine state. It receives an
:class:`~tradefloor.harness.Observation`, rendered into whatever it reads, and
returns a decision that this package validates before anything is executed.

## Why these are not in the top-level namespace

Every other module under ``tradefloor/`` is first-party engine surface, and
importing the package imports all of them. An adapter is a different kind of
thing: it depends on a third party whose release cadence, Python support and
dependency tree are not ours, and it must not be able to break
``import tradefloor`` for somebody who has never heard of it. So the rules
here are narrow and worth stating.

- ``tradefloor/__init__.py`` does not import this subpackage, and this
  subpackage's ``__init__`` does not import its own modules. Reaching an
  adapter is always an explicit
  ``from tradefloor.integrations.finrobot import ...``.
- An adapter imports its framework INSIDE the function that needs it, never
  at module scope, and says which extra installs it when the import fails.
  A reader who only wants to replay a recorded run should never be asked for
  the framework at all.
- One optional extra per framework, named after it:
  ``pip install "tradefloor[finrobot]"``.

## Attribution

Each adapter targets a project this repository does not own and is not
affiliated with. The module docstrings name the upstream project, its
licence and the version tested. Nothing here is endorsed by, or produced in
partnership with, those projects.
"""

from __future__ import annotations

# Deliberately empty of imports. See the docstring: an adapter is reached by
# naming it, so that a broken or uninstalled third-party dependency can never
# be reached by `import tradefloor` alone.

__all__: list[str] = []
