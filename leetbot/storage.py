"""A single JSON mapping of tracked usernames to solved problem IDs."""

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
        self._solved: dict[str, set[str]] = {}
        if not self.state_path.exists():
            self._save({})
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Expected an object mapping usernames to problem ID lists")
            for username, problem_ids in payload.items():
                if validate_username(username) != username or self.contains(username):
                    raise ValueError("Invalid or duplicate username")
                if not isinstance(problem_ids, list) or any(
                    not isinstance(problem_id, str) or not problem_id.strip() for problem_id in problem_ids
                ):
                    raise ValueError("Problem IDs must be nonempty strings")
                self._solved[username] = set(problem_ids)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid state file: {self.state_path}. Expected username: [problem IDs].") from exc

    def _key(self, username: str) -> str:
        name = validate_username(username)
        return next((key for key in self._solved if key.casefold() == name.casefold()), name)

    def users(self) -> list[str]:
        return sorted(self._solved, key=str.casefold)

    def contains(self, username: str) -> bool:
        return self._key(username) in self._solved

    def solved_ids(self, username: str) -> set[str]:
        return self._solved[self._key(username)].copy()

    def _save(self, solved: dict[str, set[str]]) -> None:
        payload = {username: sorted(problem_ids) for username, problem_ids in sorted(solved.items())}
        atomic_write(self.state_path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        self._solved = solved

    def add(self, username: str, problem_ids: set[str]) -> bool:
        username = validate_username(username)
        if self.contains(username):
            return False
        self._save({**self._solved, username: set(problem_ids)})
        return True

    def record_solve(self, username: str, problem_id: str) -> None:
        key = self._key(username)
        self._save({**self._solved, key: self._solved[key] | {problem_id}})

    def remove(self, username: str) -> bool:
        key = self._key(username)
        if key not in self._solved:
            return False
        self._save({name: problem_ids for name, problem_ids in self._solved.items() if name != key})
        return True
