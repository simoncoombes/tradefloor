"""A lossless JSON form of ``Engine.state_snapshot()``, for tests.

The snapshot holds byte buffers, and the generator array carries u64 bit
patterns as f64s, some of which are NaN with a payload. JSON's own number
syntax loses both, so this tags them: bytes as ``{"bytes": base64}``, and any
float that is not finite as ``{"f64": hex bits}``. Every finite float goes
through ``repr``, which round-trips exactly.

Self-contained and free of tradefloor imports, so the fixture writer in
``tests/fixtures/snapshots`` can run it under an installed release.
"""

from __future__ import annotations

import base64
import json
import math
import struct
from typing import Any


def encode(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return {"bytes": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return {"f64": struct.pack("<d", value).hex()}
    if isinstance(value, dict):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    raise TypeError(f"cannot encode {type(value).__name__}")


def decode(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"bytes"}:
            return base64.b64decode(value["bytes"])
        if set(value) == {"f64"}:
            return struct.unpack("<d", bytes.fromhex(value["f64"]))[0]
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


def dumps(snapshot: dict[str, Any]) -> str:
    return json.dumps(encode(snapshot), sort_keys=True)


def loads(text: str) -> dict[str, Any]:
    return decode(json.loads(text))
