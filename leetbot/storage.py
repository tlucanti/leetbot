"""A single JSON mapping of usernames to problem IDs and completion times."""

import json
import os
from pathlib import Path
import tempfile

from .leetcode import validate_username


def atomic_write(path: Path, content: str) -> None:
    """Replace the complete file so a crash cannot leave partially written JSON."""
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temp_path = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


class TrackingStore:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.state_path = directory / "state.json"
        self._solved: dict[str, dict[str, int]] = {}
        if not self.state_path.exists():
            self._save({})
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Expected an object mapping usernames to completed problems")
            for username, completions in payload.items():
                if validate_username(username) != username or self.contains(username):
                    raise ValueError("Invalid or duplicate username")
                if not isinstance(completions, dict) or any(
                    not isinstance(problem_id, str)
                    or not problem_id.strip()
                    or type(completed_at) is not int
                    or completed_at < 0
                    for problem_id, completed_at in completions.items()
                ):
                    raise ValueError("Expected problem IDs mapped to Unix completion timestamps")
                self._solved[username] = dict(completions)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid state file: {self.state_path}. Expected username: {{problem ID: Unix timestamp}}."
            ) from exc

    def _key(self, username: str) -> str:
        name = validate_username(username)
        return next((key for key in self._solved if key.casefold() == name.casefold()), name)

    def users(self) -> list[str]:
        return sorted(self._solved, key=str.casefold)

    def contains(self, username: str) -> bool:
        return self._key(username) in self._solved

    def solved_ids(self, username: str) -> set[str]:
        return set(self._solved[self._key(username)])

    def completion_times(self, username: str) -> dict[str, int]:
        return self._solved[self._key(username)].copy()

    def _save(self, solved: dict[str, dict[str, int]]) -> None:
        payload = {username: dict(sorted(completions.items())) for username, completions in sorted(solved.items())}
        atomic_write(self.state_path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        self._solved = solved

    def add(self, username: str, completions: dict[str, int]) -> bool:
        username = validate_username(username)
        if self.contains(username):
            return False
        self._save({**self._solved, username: dict(completions)})
        return True

    def record_solve(self, username: str, problem_id: str, completed_at: int) -> None:
        key = self._key(username)
        if problem_id not in self._solved[key]:
            self._save({**self._solved, key: {**self._solved[key], problem_id: completed_at}})

    def remove(self, username: str) -> bool:
        key = self._key(username)
        if key not in self._solved:
            return False
        self._save({name: problem_ids for name, problem_ids in self._solved.items() if name != key})
        return True
