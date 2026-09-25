from visionrig.contracts import SourceDescriptor
from visionrig.journal import EventJournal
from visionrig.pipeline import Frame, PerceptionPipeline


def _event(sequence: int):
    pipeline = PerceptionPipeline()
    return pipeline.process(
        Frame(
            source=SourceDescriptor(source_id="cam", source_type="camera"),
            sequence=sequence,
            payload=None,
        )
    )


def test_journal_cursor_reads_incrementally() -> None:
    journal = EventJournal(capacity=4)
    first = journal.append(_event(1))
    journal.append(_event(2))

    batch = journal.read(after_cursor=first.cursor)
    assert len(batch.entries) == 1
    assert batch.entries[0].event.frame_sequence == 2
    assert batch.next_cursor == 2
    assert batch.gap is False


def test_journal_reports_gap_after_eviction() -> None:
    journal = EventJournal(capacity=2)
    journal.append(_event(1))
    journal.append(_event(2))
    journal.append(_event(3))

    batch = journal.read(after_cursor=0)
    assert batch.gap is False
    assert [entry.cursor for entry in batch.entries] == [2, 3]

    missed = journal.read(after_cursor=0)
    assert missed.oldest_available_cursor == 2

    gap = journal.read(after_cursor=0)
    assert gap.gap is False

    # A consumer that last saw cursor 0 does not claim a gap because 0 means
    # "start from what is currently available". A stale real cursor does.
    journal.append(_event(4))
    journal.append(_event(5))
    stale = journal.read(after_cursor=1)
    assert stale.gap is True
    assert stale.entries[0].cursor == 4
