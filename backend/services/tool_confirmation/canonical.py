"""Deterministic JSON-only argument snapshots for confirmation binding."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


class CanonicalArgumentsError(ValueError):
    pass


def _validate_json(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalArgumentsError("non-finite number")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalArgumentsError("object key must be a string")
            _validate_json(item)
        return
    raise CanonicalArgumentsError("arguments must contain JSON values only")


def canonical_arguments_json(arguments: Any) -> str:
    _validate_json(arguments)
    return json.dumps(
        arguments, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def canonical_arguments_hash(arguments: Any) -> str:
    snapshot = canonical_arguments_json(arguments).encode("utf-8")
    return hashlib.sha256(snapshot).hexdigest()
