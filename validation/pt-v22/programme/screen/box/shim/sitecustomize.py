"""Loaded at start-up when graded_scenarios.py puts this folder on PYTHONPATH:
`tradefloor.Scenario.load(name)` reads $GRADED_SCENARIOS/NAME.yml when it exists."""
import os
import pathlib

_DIR = os.environ.get("GRADED_SCENARIOS")
if _DIR:
    try:
        import tradefloor as _tf
    except ImportError:
        _tf = None
    if _tf is not None and not getattr(_tf.Scenario, "_graded", False):
        _orig = _tf.Scenario.load

        def _load(name, _orig=_orig):
            p = pathlib.Path(_DIR) / f"{name}.yml"
            if p.exists():
                return _tf.Scenario.from_yaml(p.read_text(encoding="utf-8"))
            return _orig(name)
        _tf.Scenario.load = staticmethod(_load)
        _tf.Scenario._graded = True
