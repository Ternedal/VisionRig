from pathlib import Path

import pytest

from visionrig.producer_state import (
    ProducerStateError,
    ProducerStateStore,
    producer_state_key,
)


def test_state_reservation_survives_restart_and_recovers_inflight_as_drop(
    tmp_path: Path,
) -> None:
    path = tmp_path / "producer-state.json"
    key = producer_state_key(
        gateway_url="http://100.64.0.2:8111",
        source_id="quest",
        source_type="vr",
    )

    first = ProducerStateStore(path)
    sequence, pending = first.reserve(key)
    assert sequence == 0
    assert pending == 0

    # Simulated crash: no accept/drop completion.
    second = ProducerStateStore(path)
    recovered = second.recover(key)
    assert recovered.next_sequence == 1
    assert recovered.pending_dropped == 1
    assert recovered.inflight_sequence is None

    next_sequence, next_pending = second.reserve(key)
    assert next_sequence == 1
    assert next_pending == 1
    accepted = second.mark_accepted(key, next_sequence)
    assert accepted.pending_dropped == 0
    assert accepted.next_sequence == 2


def test_mark_drop_persists_accumulator(tmp_path: Path) -> None:
    store = ProducerStateStore(tmp_path / "state.json")
    key = "producer-test"
    sequence, _ = store.reserve(key)
    after_drop = store.mark_dropped(key, sequence)
    assert after_drop.pending_dropped == 1

    next_sequence, pending = store.reserve(key)
    assert next_sequence == 1
    assert pending == 1


def test_corrupt_or_inconsistent_state_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        """{
          "schema_id": "visionrig/producer-state/v1",
          "entries": {
            "producer-test": {
              "next_sequence": 3,
              "pending_dropped": 0,
              "inflight_sequence": 3
            }
          }
        }""",
        encoding="utf-8",
    )
    with pytest.raises(ProducerStateError, match="invalid producer state"):
        ProducerStateStore(path).snapshot("producer-test")
