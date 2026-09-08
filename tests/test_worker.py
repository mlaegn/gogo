"""S5d — the fetch loop.

The loop matters for a reason that is easy to miss: each cycle stamps a
`forecast_snapshots` row with `fetched_at`, and that history cannot be reconstructed.
The archive knows what the ocean did last Tuesday; nothing knows what the forecast said
on the Monday. So a day the worker did not run is a permanent hole in the only data that
can answer "would we have called it right at the time".

Which makes "does not die quietly" the property worth testing.
"""

import threading
from datetime import date

from gogo.worker import date_chunks, run_forever


def test_a_failed_fetch_does_not_end_the_loop():
    """Open-Meteo has bad minutes. A worker that exits on the first one looks like a
    worker that ran for a while, which is worse than one that never started."""
    calls: list[int] = []
    stop = threading.Event()

    def flaky(_days: int) -> int:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("open-meteo timed out")
        if len(calls) >= 3:
            stop.set()
        return 42

    # interval 0 keeps the test instant: backoff is capped at the interval.
    cycles = run_forever(interval_s=0, stop=stop, fetch=flaky, check=lambda: [])

    assert len(calls) == 3, "should have retried after the failure"
    assert cycles == 2, "only the two successful cycles count"


def test_an_already_set_stop_means_no_fetch_at_all():
    """SIGTERM during startup must not fire off a request on the way out."""
    stop = threading.Event()
    stop.set()
    calls: list[int] = []

    cycles = run_forever(
        interval_s=0, stop=stop, fetch=lambda _d: calls.append(1) or 0, check=lambda: []
    )

    assert calls == []
    assert cycles == 0


def test_a_spot_with_no_hours_is_reported_every_cycle(caplog):
    """The Ribeira bug: a spot left every ranking for four days and nothing complained.

    A ranking quietly missing a spot looks entirely normal — there is no error, just one
    fewer row — so the check has to be loud on its own.
    """
    stop = threading.Event()

    def once(_days: int) -> int:
        stop.set()
        return 1

    with caplog.at_level("ERROR"):
        run_forever(interval_s=0, stop=stop, fetch=once, check=lambda: ["ribeira"])

    assert "ribeira" in caplog.text
    assert "no hours" in caplog.text


def test_a_healthy_cycle_logs_no_error(caplog):
    stop = threading.Event()

    def once(_days: int) -> int:
        stop.set()
        return 1

    with caplog.at_level("ERROR"):
        run_forever(interval_s=0, stop=stop, fetch=once, check=lambda: [])

    assert caplog.text == ""


def test_date_chunks_covers_the_range_exactly():
    chunks = list(date_chunks(date(2026, 1, 1), date(2026, 1, 10), 4))

    assert chunks[0] == (date(2026, 1, 1), date(2026, 1, 4))
    assert chunks[-1][1] == date(2026, 1, 10)
    # Inclusive and gapless: the next chunk starts the day after the last one ended.
    for (_, end), (start, _) in zip(chunks, chunks[1:]):
        assert (start - end).days == 1
