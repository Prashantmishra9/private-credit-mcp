"""In-memory response cache, cleared whenever data_version changes."""

import json
import time
from collections.abc import Callable
from threading import Lock
from typing import Any


class ResponseCache:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = Lock()
        self._version: int | None = None
        self._entries: dict[str, tuple[Any, float | None]] = {}

    @staticmethod
    def key(tool: str, arguments: dict[str, Any]) -> str:
        """Tool name plus arguments; order and letter case do not matter."""
        return tool + ":" + json.dumps(arguments, sort_keys=True, default=str).lower()

    def get(self, key: str, version: int | None) -> Any | None:
        with self._lock:
            self._clear_if_changed(version)
            value, expires = self._entries.get(key, (None, None))
            if expires is not None and self._clock() >= expires:
                del self._entries[key]
                return None
            return value

    def put(self, key: str, version: int | None, value: Any, ttl: float | None = None) -> None:
        """Store a response that was computed under `version`."""
        with self._lock:
            self._clear_if_changed(version)
            self._entries[key] = (value, None if ttl is None else self._clock() + ttl)

    def _clear_if_changed(self, version: int | None) -> None:
        if version != self._version:
            self._entries.clear()
            self._version = version
