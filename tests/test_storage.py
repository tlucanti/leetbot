import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from leetbot.storage import Completion, TrackingStore


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.store = TrackingStore(self.path)

    def test_only_one_file_with_username_to_problem_ids_mapping(self):
        self.store.add("Alice", {"1": Completion(1000, "Easy"), "15": Completion(1100, "Medium")})
        self.assertEqual(json.loads(self.store.state_path.read_text()), {"Alice": {
            "1": {"completed_at": 1000, "difficulty": "Easy"},
            "15": {"completed_at": 1100, "difficulty": "Medium"},
        }})
        self.assertEqual([p.name for p in self.path.iterdir()], ["state.json"])
        self.assertEqual(TrackingStore(self.path).solved_ids("ALICE"), {"1", "15"})
        self.assertEqual(TrackingStore(self.path).completion_times("ALICE"), {"1": 1000, "15": 1100})
        self.assertEqual(TrackingStore(self.path).completed_problems("ALICE")["15"].difficulty, "Medium")

    def test_empty_history_still_tracks_user(self):
        self.assertTrue(self.store.add("Alice", {}))
        self.assertTrue(self.store.contains("alice"))
        self.assertEqual(json.loads(self.store.state_path.read_text()), {"Alice": {}})

    def test_case_insensitive_add_does_not_replace_existing_solves(self):
        self.store.add("Alice", {"1": Completion(1000, "Easy")})
        self.assertFalse(self.store.add("ALICE", {"2": Completion(1100, "Hard")}))
        self.assertEqual(self.store.users(), ["Alice"])
        self.assertEqual(self.store.solved_ids("alice"), {"1"})

    def test_record_solve_accumulates_problem_ids(self):
        self.store.add("Alice", {"1": Completion(1000, "Easy")})
        self.store.record_solve("ALICE", "2", 1100, "Medium")
        self.store.record_solve("alice", "2", 1200, "Hard")
        self.assertEqual(self.store.solved_ids("Alice"), {"1", "2"})
        self.assertEqual(TrackingStore(self.path).solved_ids("Alice"), {"1", "2"})
        self.assertEqual(TrackingStore(self.path).completion_times("Alice"), {"1": 1000, "2": 1100})
        self.assertEqual(TrackingStore(self.path).completed_problems("Alice")["2"].difficulty, "Medium")

    def test_remove_deletes_user_from_json(self):
        self.store.add("Alice", {"1": Completion(1000, "Easy")})
        self.store.add("Bob", {"2": Completion(1100, "Medium")})
        self.assertTrue(self.store.remove("ALICE"))
        self.assertFalse(self.store.remove("alice"))
        self.assertEqual(json.loads(self.store.state_path.read_text()), {"Bob": {
            "2": {"completed_at": 1100, "difficulty": "Medium"},
        }})
        self.assertFalse(TrackingStore(self.path).contains("Alice"))

    def test_atomic_write_failure_preserves_file_and_memory(self):
        self.store.add("Alice", {"1": Completion(1000, "Easy")})
        before = self.store.state_path.read_bytes()
        with patch("leetbot.storage.os.replace", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                self.store.record_solve("Alice", "2", 1100, "Medium")
        self.assertEqual(self.store.state_path.read_bytes(), before)
        self.assertEqual(self.store.solved_ids("Alice"), {"1"})
        self.assertEqual([p.name for p in self.path.iterdir()], ["state.json"])

    def test_remove_write_failure_preserves_tracking(self):
        self.store.add("Alice", {"1": Completion(1000, "Easy")})
        with patch("leetbot.storage.os.replace", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                self.store.remove("Alice")
        self.assertTrue(self.store.contains("Alice"))
        self.assertTrue(TrackingStore(self.path).contains("Alice"))

    def test_corrupt_or_invalid_state_is_rejected_without_overwriting(self):
        for content in ("broken json", "[]", '{"Alice": ["1"]}', '{"Alice": {"1": null}}',
                        '{"Alice": {"1": 1000}}', '{"Alice": {"1": {}}}',
                        '{"Alice": {"1": {"completed_at": true, "difficulty": "Easy"}}}',
                        '{"Alice": {"1": {"completed_at": -1, "difficulty": "Easy"}}}',
                        '{"Alice": {"1": {"completed_at": 1000, "difficulty": "invalid"}}}',
                        '{"Alice": {"1": {"completed_at": 1000, "difficulty": null}}}',
                        '{"Alice": {"": {}}}', '{"../alice": {}}', '{"Alice": {}, "ALICE": {}}'):
            with self.subTest(content=content):
                self.store.state_path.write_text(content)
                with self.assertRaisesRegex(ValueError, "Invalid state file"):
                    TrackingStore(self.path)
                self.assertEqual(self.store.state_path.read_text(), content)

    def test_solved_ids_are_returned_as_a_copy(self):
        self.store.add("Alice", {"1": Completion(1000, "Easy")})
        self.store.solved_ids("Alice").add("2")
        self.assertEqual(self.store.solved_ids("Alice"), {"1"})

    def test_completion_times_are_returned_as_a_copy(self):
        self.store.add("Alice", {"1": Completion(1000, "Easy")})
        self.store.completion_times("Alice")["1"] = 2000
        self.assertEqual(self.store.completion_times("Alice"), {"1": 1000})

    def test_unknown_historical_times_can_be_zero(self):
        self.store.add("Alice", {"1": Completion(0, None)})
        self.assertEqual(TrackingStore(self.path).completion_times("Alice"), {"1": 0})

    def test_completed_problems_are_returned_as_a_copy(self):
        self.store.add("Alice", {"1": Completion(1000, "Easy")})
        self.store.completed_problems("Alice")["1"] = Completion(2000, "Hard")
        self.assertEqual(self.store.completed_problems("Alice")["1"], Completion(1000, "Easy"))
