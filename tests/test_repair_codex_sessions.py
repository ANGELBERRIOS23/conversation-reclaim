import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts import repair_codex_sessions as repair


THREAD_ID = "01a05868-ed81-7c33-9f78-e8bf0af75caa"


class RepairCodexSessionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sessions = self.root / "sessions"
        self.archived = self.root / "archived_sessions"
        self.sessions.mkdir()
        self.archived.mkdir()
        self.path = self.sessions / f"rollout-2026-08-31T09-21-06-{THREAD_ID}.jsonl"
        self.current = (b'{"type":"compacted","payload":{"message":"kept"}}\n'
                        b'{"type":"event_msg","payload":{"type":"done"}}\n')
        self.path.write_bytes(self.current)
        self.db = self.root / "state.sqlite"
        with sqlite3.connect(self.db) as connection:
            connection.execute("""
                CREATE TABLE threads (
                    id TEXT, rollout_path TEXT, created_at_ms INTEGER,
                    created_at INTEGER, cwd TEXT, source TEXT,
                    model_provider TEXT, cli_version TEXT,
                    history_mode TEXT, thread_source TEXT, originator TEXT
                )
            """)
            connection.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (THREAD_ID, str(self.path), 1788189666690, 1788189666,
                 "/tmp/example", "vscode", "openai", "0.150.0", "paginated", "user", None),
            )

    def args(self, *extra):
        return ["--sessions-root", str(self.sessions), "--archived-root", str(self.archived),
                "--state-db", str(self.db), *extra]

    def test_dry_run_keeps_file_unchanged(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(repair.main(self.args()), 0)
        self.assertEqual(self.path.read_bytes(), self.current)

    def test_reconstructs_header_and_verifies_backup(self):
        backup = self.root / "backup"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(repair.main(self.args("--apply", "--backup-dir", str(backup))), 0)
        contents = self.path.read_bytes()
        header = json.loads(contents.splitlines()[0])
        self.assertEqual(header["type"], "session_meta")
        self.assertEqual(header["payload"]["id"], THREAD_ID)
        self.assertEqual(header["payload"]["source"], "vscode")
        self.assertTrue(contents.endswith(self.current))
        self.assertEqual((backup / "sessions" / self.path.name).read_bytes(), self.current)

    def test_restores_full_original_when_current_is_exact_suffix(self):
        originals = self.root / "originals"
        candidate = originals / "codex-sessions" / self.path.name
        candidate.parent.mkdir(parents=True)
        full = (b'{"type":"session_meta","payload":{"id":"' + THREAD_ID.encode() +
                b'"}}\n{"type":"event_msg","payload":{"type":"old"}}\n' + self.current)
        candidate.write_bytes(full)
        backup = self.root / "backup"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(repair.main(self.args("--originals-root", str(originals),
                                                    "--apply", "--backup-dir", str(backup))), 0)
        self.assertEqual(self.path.read_bytes(), full)
        self.assertEqual((backup / "sessions" / self.path.name).read_bytes(), self.current)

    def test_missing_index_row_fails_before_writing(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("DELETE FROM threads")
        backup = self.root / "backup"
        with self.assertRaisesRegex(ValueError, "no matching thread index row"):
            repair.main(self.args("--apply", "--backup-dir", str(backup)))
        self.assertFalse(backup.exists())
        self.assertEqual(self.path.read_bytes(), self.current)


if __name__ == "__main__":
    unittest.main()
