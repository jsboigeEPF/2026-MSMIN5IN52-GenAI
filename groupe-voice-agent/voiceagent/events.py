"""One monotonic event schema and a durable, off-loop JSONL writer."""

import json
import queue
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path


class EventBus:
    def __init__(self, root: Path, mode: str, session: str | None = None):
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.session = session or f"{timestamp}-{uuid.uuid4().hex[:8]}"
        self.directory = root / self.session
        self.directory.mkdir(parents=True, exist_ok=False)
        self.mode = mode
        self._queue: queue.Queue = queue.Queue(maxsize=10000)
        self._error: Exception | None = None
        self._closed = False
        self._listeners = []
        self._writer = threading.Thread(target=self._write, daemon=True, name="event-writer")
        self._writer.start()
        self.emit("session_start", mode=mode, wall_time=datetime.now(UTC).isoformat())

    def emit(self, event: str, turn: int = 0, *, t_ns: int | None = None, **data) -> None:
        if self._closed:
            return
        if self._error:
            raise RuntimeError("Écriture des traces impossible") from self._error
        record = {
            "t_ns": time.perf_counter_ns() if t_ns is None else int(t_ns),
            "session": self.session,
            "turn": turn,
            "event": event,
            "route": "local",
            "mode": self.mode,
            "data": data,
        }
        try:
            self._queue.put_nowait(record)
        except queue.Full as exc:
            raise RuntimeError(
                "File de traces saturée ; arrêt pour préserver les mesures"
            ) from exc
        for listener in self._listeners:
            listener(record)

    def subscribe(self, listener) -> None:
        self._listeners.append(listener)

    def _write(self) -> None:
        try:
            path = self.directory / "events.jsonl"
            with path.open("x", encoding="utf-8", buffering=1) as stream:
                while True:
                    record = self._queue.get()
                    if record is None:
                        return
                    stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        except Exception as exc:
            self._error = exc

    def close(self) -> None:
        if self._closed:
            return
        self.emit("session_end")
        self._closed = True
        while self._writer.is_alive():
            try:
                self._queue.put(None, timeout=0.1)
                break
            except queue.Full:
                continue
        self._writer.join(timeout=5)
        if self._writer.is_alive() or self._error:
            raise RuntimeError("Impossible de finaliser events.jsonl") from self._error
