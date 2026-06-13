import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from daglas.email_queue import RawEmail
from daglas.subscriber_store import SubscriberStore

logger = logging.getLogger(__name__)

Actor = Callable[[str, str, str], None]


@dataclass
class ClassificationResult:
    action_counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class EmailProcessor:
    def __init__(self, queue):
        self._queue = queue
        self._actors: dict[str, Actor] = {}
        self._patterns: dict[str, list[str]] = {}
        self._running = False

        self.register("unsubscribe", self._unsubscribe_default)
        self.register("subscribe", self._subscribe_default)
        self._actors["unknown"] = self._unknown_default

        queue.on_push("incoming", self._on_notify)

    def register(
        self, action: str, actor: Actor, patterns: list[str] | None = None
    ) -> None:
        self._actors[action] = actor
        self._patterns[action] = patterns or [action]

    def _on_notify(self, namespace: str) -> None:
        if self._running:
            return
        self._running = True
        try:
            self.process(namespace)
        finally:
            self._running = False

    def process(self, namespace: str = "incoming") -> ClassificationResult:
        result = ClassificationResult()
        emails = self._queue.drain(namespace)
        for email in emails:
            action = self._classify(email.subject, email.body)
            try:
                self._dispatch(action, email.sender, email.subject, email.body)
                result.action_counts[action] = result.action_counts.get(action, 0) + 1
            except Exception as e:
                result.errors.append(f"{action}:{email.sender}: {e}")
        return result

    def _classify(self, subject: str, body: str) -> str:
        text = (subject + " " + body).lower()
        for action in self._patterns:
            for pattern in self._patterns[action]:
                if pattern.lower() in text:
                    return action
        return "unknown"

    def _dispatch(self, action: str, sender: str, subject: str, body: str) -> None:
        actor = self._actors.get(action)
        if actor:
            actor(sender, subject, body)

    def _subscribe_default(self, sender: str, subject: str, body: str) -> None:
        store = SubscriberStore()
        store.add(sender)

    def _unsubscribe_default(self, sender: str, subject: str, body: str) -> None:
        store = SubscriberStore()
        store.remove(sender)

    def _unknown_default(self, sender: str, subject: str, body: str) -> None:
        self._queue.push(
            "archive",
            RawEmail(sender=sender, subject=subject, body=body, raw_bytes=b""),
        )
