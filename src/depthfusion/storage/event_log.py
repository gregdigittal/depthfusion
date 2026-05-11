from __future__ import annotations

import fcntl
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from depthfusion.core.memory import MemoryEvent


class EventLog:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seen_ids: set[str] = set()
        self._load_seen_ids()

    def _load_seen_ids(self) -> None:
        if not self._path.exists():
            return
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        data = json.loads(line)
                        self._seen_ids.add(data["event_id"])
                    except (json.JSONDecodeError, KeyError):
                        pass

    def append(self, event: MemoryEvent) -> bool:
        with self._lock:
            if event.event_id in self._seen_ids:
                return False
            line = json.dumps(event.to_dict()) + "\n"
            with open(self._path, "a") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    f.write(line)
                    f.flush()
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
            self._seen_ids.add(event.event_id)
            return True

    def replay(
        self,
        project_id: Optional[str] = None,
        since: Optional[datetime] = None,
    ) -> Iterator[MemoryEvent]:
        if not self._path.exists():
            return
        with open(self._path) as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        event = MemoryEvent.from_dict(data)
                        if project_id and event.project_id != project_id:
                            continue
                        if since and event.timestamp < since:
                            continue
                        yield event
                    except (json.JSONDecodeError, KeyError, ValueError):
                        pass
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def count(self) -> int:
        with self._lock:
            return len(self._seen_ids)
