from pathlib import Path

import daglas.config


class SubscriberStore:
    def __init__(self, path: str | None = None):
        if path:
            self._path = Path(path)
        elif daglas.config.config is not None:
            data_dir = Path(daglas.config.config.data_dir)
            self._path = data_dir / "subscribers.txt"
        else:
            self._path = Path("data") / "subscribers.txt"

    def _read_all(self) -> list[str]:
        if not self._path.is_file():
            return []
        lines = self._path.read_text().splitlines()
        return [line.strip() for line in lines if line.strip()]

    def _write_all(self, lines: list[str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text("\n".join(lines) + "\n")

    def list(self) -> list[str]:
        return self._read_all()

    def add(self, email: str) -> None:
        email = email.strip()
        if not email:
            return
        current = self._read_all()
        if email in current:
            return
        current.append(email)
        self._write_all(current)

    def remove(self, email: str) -> None:
        email = email.strip()
        if not email:
            return
        if not self._path.is_file():
            return
        current = self._read_all()
        if email not in current:
            return
        current = [e for e in current if e != email]
        self._write_all(current)
