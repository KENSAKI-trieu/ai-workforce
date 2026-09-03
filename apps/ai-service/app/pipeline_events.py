"""In-process progress fan-out for browser listeners.

The backend remains the only caller allowed to submit AI work.  A browser gets
an opaque, per-upload stream id and can only subscribe to the counters emitted
while that work is running.
"""

from collections import OrderedDict, deque
from threading import Condition
from time import monotonic
from typing import Any


class PipelineEventBroker:
    def __init__(self, *, max_streams: int = 512, max_events: int = 512) -> None:
        self._condition = Condition()
        self._histories: OrderedDict[str, deque[dict[str, Any]]] = OrderedDict()
        self._sequences: dict[str, int] = {}
        self._max_streams = max_streams
        self._max_events = max_events

    def publish(self, stream_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._condition:
            sequence = self._sequences.get(stream_id, 0) + 1
            self._sequences[stream_id] = sequence
            event = {
                "processing_stream_id": stream_id,
                "event_sequence": sequence,
                **payload,
            }
            history = self._histories.setdefault(
                stream_id,
                deque(maxlen=self._max_events),
            )
            history.append(event)
            self._histories.move_to_end(stream_id)
            while len(self._histories) > self._max_streams:
                expired_stream_id, _ = self._histories.popitem(last=False)
                self._sequences.pop(expired_stream_id, None)
            self._condition.notify_all()
            return event

    def wait_after(
        self,
        stream_id: str,
        sequence: int,
        *,
        timeout: float,
    ) -> list[dict[str, Any]]:
        deadline = monotonic() + timeout
        with self._condition:
            while True:
                events = [
                    event
                    for event in self._histories.get(stream_id, ())
                    if int(event["event_sequence"]) > sequence
                ]
                if events:
                    return events
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(remaining)


pipeline_events = PipelineEventBroker()
