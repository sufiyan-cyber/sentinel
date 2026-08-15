"""Canonical JSON, the hash chain, and the verifier."""

from __future__ import annotations

import pytest

from sentinelz.evidence.canonical import GENESIS, dumps, dumps_str, loads, record_hash
from sentinelz.evidence.chain import DecisionChain, verify
from sentinelz.evidence.export import read_chain, verify_file, write_chain

# ------------------------------------------------------------- canonical


def test_dumps_is_key_order_independent():
    assert dumps({"b": 1, "a": 2}) == dumps({"a": 2, "b": 1})


def test_dumps_has_no_whitespace_and_no_newline():
    raw = dumps({"a": 1, "b": [1, 2]})
    assert b" " not in raw
    assert not raw.endswith(b"\n")
    assert raw == b'{"a":1,"b":[1,2]}'


def test_dumps_is_ascii_only():
    raw = dumps({"note": "café — naïve"})
    assert all(byte < 128 for byte in raw)


def test_round_trip():
    obj = {"z": [1, {"y": None}], "a": True, "n": 1.5}
    assert loads(dumps_str(obj)) == obj


def test_record_hash_is_stable():
    assert record_hash({"a": 1, "b": 2}) == record_hash({"b": 2, "a": 1})
    assert record_hash({"a": 1}) != record_hash({"a": 2})
    assert len(record_hash({"a": 1})) == 64


def test_sets_and_numpy_scalars_serialise():
    import numpy as np

    raw = dumps({"s": {"b", "a"}, "n": np.float64(0.5), "arr": np.array([1.0, 2.0])})
    assert loads(raw) == {"s": ["a", "b"], "n": 0.5, "arr": [1.0, 2.0]}


# ----------------------------------------------------------------- chain


def _chain_of(n: int) -> DecisionChain:
    chain = DecisionChain()
    for i in range(n):
        chain.append({"idx": i, "action": "ALLOW", "hazard": i / 100.0})
    return chain


def test_first_record_links_to_genesis():
    chain = _chain_of(1)
    assert chain.records[0]["prev"] == GENESIS


def test_hundred_record_chain_verifies():
    chain = _chain_of(100)
    assert verify(chain.records) == (True, None)
    assert len(chain) == 100


def test_mutating_record_37_is_detected_at_37():
    chain = _chain_of(100)
    records = chain.records
    records[37]["action"] = "REVOKE"
    ok, bad = verify(records)
    assert ok is False
    assert bad == 37


def test_reordering_two_records_is_detected():
    chain = _chain_of(20)
    records = chain.records
    records[5], records[6] = records[6], records[5]
    ok, bad = verify(records)
    assert ok is False
    assert bad == 5


def test_truncating_the_chain_still_verifies_as_a_prefix():
    """A prefix is internally consistent — detecting truncation needs the head
    hash, which is what `chain.head` is for."""
    chain = _chain_of(10)
    assert verify(chain.records[:5]) == (True, None)
    assert chain.head == chain.records[-1]["hash"]


def test_replaying_an_old_record_is_rejected():
    chain = _chain_of(10)
    records = chain.records
    records.append(dict(records[3]))
    ok, bad = verify(records)
    assert ok is False
    assert bad == 10


def test_deleting_a_record_is_detected():
    chain = _chain_of(10)
    records = chain.records
    del records[4]
    ok, bad = verify(records)
    assert ok is False
    assert bad == 4


def test_append_sets_prev_and_hash():
    chain = DecisionChain()
    record = chain.append({"a": 1})
    assert record["prev"] == GENESIS
    assert record["hash"] == chain.head
    second = chain.append({"a": 2})
    assert second["prev"] == record["hash"]


# ---------------------------------------------------------------- export


def test_write_and_verify_file(tmp_path, monkeypatch):
    import sentinelz.evidence.export as export

    monkeypatch.setattr(export, "RUNS_DIR", tmp_path)
    chain = _chain_of(25)
    path = write_chain("run-1", chain.records)
    assert path.exists()
    assert verify_file(path) == (True, None)
    assert len(read_chain(path)) == 25


def test_tampering_with_one_line_on_disk_names_the_record(tmp_path, monkeypatch):
    import sentinelz.evidence.export as export

    monkeypatch.setattr(export, "RUNS_DIR", tmp_path)
    chain = _chain_of(12)
    path = write_chain("run-2", chain.records)

    lines = path.read_text(encoding="utf-8").splitlines()
    lines[7] = lines[7].replace('"ALLOW"', '"REVOKE"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, bad = verify_file(path)
    assert ok is False
    assert bad == 7


def test_file_is_byte_identical_for_identical_records(tmp_path, monkeypatch):
    """Determinism requirement N5, at the file level."""
    import sentinelz.evidence.export as export

    monkeypatch.setattr(export, "RUNS_DIR", tmp_path)
    first = write_chain("a", _chain_of(30).records).read_bytes()
    second = write_chain("b", _chain_of(30).records).read_bytes()
    assert first == second


@pytest.mark.parametrize("payload", [{"a": 1}, {"nested": {"x": [1, 2, {"y": "z"}]}}, {"unicode": "é"}])
def test_hash_survives_a_round_trip_through_disk(tmp_path, payload):
    path = tmp_path / "one.jsonl"
    path.write_text(dumps_str(payload) + "\n", encoding="utf-8")
    assert record_hash(loads(path.read_text(encoding="utf-8"))) == record_hash(payload)
