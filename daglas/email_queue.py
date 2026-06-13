import base64
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import daglas.config

logger = logging.getLogger(__name__)


@dataclass
class RawEmail:
    sender: str
    subject: str
    body: str
    raw_bytes: bytes
    queued_at: str = ""


class EmailQueue:
    def __init__(self, data_dir: str | None = None):
        cfg = daglas.config.config
        if data_dir:
            self._data_dir = Path(data_dir)
        elif cfg:
            self._data_dir = Path(cfg.data_dir)
        else:
            self._data_dir = Path("data")
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._listeners: dict[str, list[Callable[[str], None]]] = {}
        self._lock = threading.Lock()

    def on_push(self, namespace: str, callback: Callable[[str], None]) -> None:
        if namespace not in self._listeners:
            self._listeners[namespace] = []
        self._listeners[namespace].append(callback)

    def _namespace_path(self, namespace: str) -> Path:
        return self._data_dir / "email_queue" / f"{namespace}.jsonl"

    @staticmethod
    def _serialize(email: RawEmail) -> str:
        data = {
            "sender": email.sender,
            "subject": email.subject,
            "body": email.body,
            "raw_bytes": base64.b64encode(email.raw_bytes).decode("ascii"),
            "queued_at": email.queued_at,
        }
        return json.dumps(data, ensure_ascii=False)

    @staticmethod
    def _deserialize(line: str) -> RawEmail:
        data = json.loads(line)
        raw_bytes = base64.b64decode(data["raw_bytes"])
        return RawEmail(
            sender=data["sender"],
            subject=data["subject"],
            body=data["body"],
            raw_bytes=raw_bytes,
            queued_at=data.get("queued_at", ""),
        )

    def push(self, namespace: str, email: RawEmail) -> None:
        if not email.queued_at:
            email.queued_at = datetime.now(timezone.utc).isoformat()
        path = self._namespace_path(namespace)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = self._serialize(email)
        with self._lock:
            with open(path, "a") as f:
                f.write(line + "\n")
        for callback in self._listeners.get(namespace, []):
            try:
                callback(namespace)
            except Exception as e:
                logger.error("Listener failed for namespace '%s': %s", namespace, e)

    def pop(self, namespace: str) -> RawEmail | None:
        path = self._namespace_path(namespace)
        with self._lock:
            if not path.is_file():
                return None
            lines = path.read_text().splitlines()
            if not lines:
                return None
            first = self._deserialize(lines[0])
            remaining = lines[1:]
            if remaining:
                path.write_text("\n".join(remaining) + "\n")
            else:
                path.unlink()
        return first

    def drain(self, namespace: str) -> list[RawEmail]:
        path = self._namespace_path(namespace)
        with self._lock:
            if not path.is_file():
                return []
            lines = path.read_text().splitlines()
            path.unlink()
        return [self._deserialize(line) for line in lines]
