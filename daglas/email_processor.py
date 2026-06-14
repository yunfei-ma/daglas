import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

Listener = Callable[[str, str, str], None]


class EmailProcessor:
    def __init__(self, queue):
        self._queue = queue
        self._listeners: list[Listener] = []
        self._running = False
        queue.on_push("incoming", self._on_notify)

    def add_listener(self, callback: Listener) -> None:
        self._listeners.append(callback)

    def _on_notify(self, namespace: str) -> None:
        if self._running:
            return
        self._running = True
        try:
            self.process(namespace)
        finally:
            self._running = False

    def process(self, namespace: str = "incoming") -> int:
        count = 0
        emails = self._queue.drain(namespace)
        for email in emails:
            logger.info("Processing: from=%s subject=%s", email.sender, email.subject)
            for listener in self._listeners:
                try:
                    listener(email.sender, email.subject, email.body)
                except Exception:
                    logger.exception("Listener failed for %s", email.sender)
            count += 1
        return count
