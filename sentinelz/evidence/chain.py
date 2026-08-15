"""Hash-chained decision records.

Each record carries `prev`, the hash of the record before it. Verification
recomputes the whole chain, so both mutation and reordering are detected and
the first bad index is named.
"""

from __future__ import annotations

from typing import Any

from sentinelz.evidence.canonical import GENESIS, record_hash


class DecisionChain:
    """Append-only chain of decision records."""

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []
        self._head: str = GENESIS

    @property
    def head(self) -> str:
        """Hash of the most recent record, or `GENESIS` when empty."""
        return self._head

    @property
    def records(self) -> list[dict[str, Any]]:
        return self._records

    def __len__(self) -> int:
        return len(self._records)

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        """Link `record` to the chain and return it.

        Mutates `record`: sets `prev` and `hash`. The hash covers `prev`, so
        every record commits to the entire history before it.
        """
        record["prev"] = self._head
        record.pop("hash", None)
        digest = record_hash(record)
        record["hash"] = digest
        self._head = digest
        self._records.append(record)
        return record


def verify(records: list[dict[str, Any]]) -> tuple[bool, int | None]:
    """Verify a chain.

    Returns `(True, None)` if intact, else `(False, i)` naming the first
    record whose stored hash or `prev` link does not check out.
    """
    prev = GENESIS
    for i, record in enumerate(records):
        if record.get("prev") != prev:
            return False, i
        stored = record.get("hash")
        body = {k: v for k, v in record.items() if k != "hash"}
        if stored != record_hash(body):
            return False, i
        prev = stored  # type: ignore[assignment]
    return True, None
