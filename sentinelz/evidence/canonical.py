"""Canonical JSON serialisation and record hashing.

This is the ONLY module in the repository permitted to call `json.dumps`.
`tests/test_guardrails.py` enforces that with an AST scan.

Do not change the serialisation parameters. Every hash ever written by this
project depends on them; changing one invalidates every stored chain.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: 64 zeros. The `prev` of the first record in any chain.
GENESIS = "0" * 64


def dumps(obj: Any) -> bytes:
    """Serialise `obj` to canonical UTF-8 bytes.

    Sorted keys, no whitespace, ASCII-escaped, no trailing newline. Two equal
    objects always produce byte-identical output on any platform.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_fallback,
    ).encode("utf-8")


def dumps_str(obj: Any) -> str:
    """`dumps` as `str`, for line-oriented files and HTTP payloads."""
    return dumps(obj).decode("utf-8")


def loads(raw: str | bytes) -> Any:
    """Inverse of `dumps`."""
    return json.loads(raw)


def record_hash(obj: Any) -> str:
    """SHA-256 hex digest of the canonical serialisation of `obj`."""
    return hashlib.sha256(dumps(obj)).hexdigest()


def _fallback(obj: Any) -> Any:
    """Coerce the few non-JSON types that reach us into stable primitives."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, set | frozenset):
        return sorted(obj)
    if hasattr(obj, "tolist"):  # numpy scalars and arrays
        return obj.tolist()
    if hasattr(obj, "item"):
        return obj.item()
    return str(obj)
