import argparse
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import reclaim


def write_jsonl(path, records):
    path.write_bytes(b"".join(
        json.dumps(record, separators=(",", ":")).encode() + b"\n"
        for record in records))


class MarkerTests(unittest.TestCase):
    def test_nested_codex_marker_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rollout.jsonl"
            write_jsonl(path, [
                {"type": "event", "payload": "keep"},
                {"type": "unrelated", "payload": {"type": "compacted"}},
                {"type": "event", "payload": "recent"},
            ])
            before = path.read_bytes()
            result = reclaim.truncate_file_at_marker(
                path, reclaim.is_codex_compaction, "codex")
            self.assertFalse(result[2])
            self.assertEqual(path.read_bytes(), before)

    def test_structural_marker_trims_and_preserves_mode(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rollout.jsonl"
            write_jsonl(path, [
                {"type": "event", "payload": "old"},
                {"type": "compacted", "payload": {"summary": "kept"}},
                {"type": "event", "payload": "recent"},
            ])
            path.chmod(0o600)
            cut, _size, done, _marker = reclaim.truncate_file_at_marker(
                path, reclaim.is_codex_compaction, "codex")
            self.assertTrue(done)
            self.assertGreater(cut, 0)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text().splitlines()[0])["type"],
                             "compacted")

    def test_invalid_json_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rollout.jsonl"
            path.write_bytes(b'{"type":"event"}\nnot-json\n{"type":"compacted"}\n')
            before = path.read_bytes()
            result = reclaim.truncate_file_at_marker(
                path, reclaim.is_codex_compaction, "codex")
            self.assertFalse(result[2])
            self.assertEqual(path.read_bytes(), before)


class SubagentTests(unittest.TestCase):
    def test_codex_subagent_requires_structural_metadata_and_matching_id(self):
        with tempfile.TemporaryDirectory() as td:
            thread_id = "019ffc9b-5721-78a1-af01-c72508ff6d76"
            path = Path(td) / ("rollout-2026-08-13-" + thread_id + ".jsonl")
            write_jsonl(path, [{
                "type": "session_meta",
                "payload": {
                    "id": thread_id,
                    "thread_source": "subagent",
                    "parent_thread_id": "parent",
                    "source": {"subagent": {"thread_spawn": {"depth": 1}}},
                },
            }])
            self.assertEqual(reclaim.codex_subagent_info(path)["thread_id"], thread_id)

    def test_claude_acompact_sidechain_is_recognized(self):
        with tempfile.TemporaryDirectory() as td:
            session = "session-id"
            folder = Path(td) / session / "subagents"
            folder.mkdir(parents=True)
            path = folder / "agent-acompact-123.jsonl"
            write_jsonl(path, [{
                "isSidechain": True,
                "agentId": "acompact-123",
                "sessionId": session,
            }])
            self.assertIsNotNone(reclaim.claude_subagent_info(path))

    def test_codex_writer_lock_preserves_subagent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            locks = root / "locks"
            locks.mkdir()
            thread_id = "child-id"
            (locks / (thread_id + ".lock")).touch()
            paths = dict(reclaim.PATHS)
            paths.update({"codex_locks": locks,
                          "codex_state": root / "missing.sqlite"})
            with mock.patch.object(reclaim, "PATHS", paths):
                self.assertTrue(reclaim.codex_subagent_is_active(thread_id))

    def test_closed_codex_subagent_removes_rollout_and_index_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sessions = root / "sessions"
            sessions.mkdir()
            thread_id = "019ffc9b-5721-78a1-af01-c72508ff6d76"
            rollout = sessions / ("rollout-" + thread_id + ".jsonl")
            write_jsonl(rollout, [{
                "type": "session_meta",
                "payload": {"id": thread_id, "thread_source": "subagent",
                            "parent_thread_id": "parent",
                            "source": {"subagent": {"thread_spawn": {"depth": 1}}}},
            }])
            state = root / "state.sqlite"
            con = sqlite3.connect(state)
            con.executescript("""
                CREATE TABLE threads(id TEXT PRIMARY KEY, thread_source TEXT);
                CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,
                    child_thread_id TEXT PRIMARY KEY, status TEXT);
                CREATE TABLE thread_dynamic_tools(thread_id TEXT);
            """)
            con.execute("INSERT INTO threads VALUES (?, 'subagent')", (thread_id,))
            con.execute("INSERT INTO thread_spawn_edges VALUES ('parent', ?, 'closed')",
                        (thread_id,))
            con.execute("INSERT INTO thread_dynamic_tools VALUES (?)", (thread_id,))
            con.commit()
            con.close()
            log_db = root / "logs.sqlite"
            con = sqlite3.connect(log_db)
            con.execute("CREATE TABLE logs(id INTEGER PRIMARY KEY, thread_id TEXT)")
            con.execute("INSERT INTO logs(thread_id) VALUES (?)", (thread_id,))
            con.commit()
            con.close()
            paths = dict(reclaim.PATHS)
            paths.update({"codex_sessions": sessions, "codex_state": state,
                          "codex_locks": root / "locks", "codex_logs": [log_db],
                          "codex_archived": root / "missing-archived"})
            with mock.patch.object(reclaim, "PATHS", paths), \
                 mock.patch.object(reclaim, "database_in_use", return_value=False), \
                 redirect_stdout(io.StringIO()):
                freed, entries = reclaim.apply_codex()
            self.assertGreater(freed, 0)
            self.assertFalse(rollout.exists())
            self.assertEqual(entries[0]["action"], "delete_subagent")
            con = sqlite3.connect(state)
            self.assertEqual(con.execute("SELECT count(*) FROM threads").fetchone()[0], 0)
            self.assertEqual(con.execute(
                "SELECT count(*) FROM thread_spawn_edges").fetchone()[0], 0)
            con.close()
            con = sqlite3.connect(log_db)
            self.assertEqual(con.execute("SELECT count(*) FROM logs").fetchone()[0], 0)
            con.close()


class MetricsTests(unittest.TestCase):
    def test_antigravity_is_included_in_total(self):
        fake = {"conv_total": 1000, "wal_total": 200, "reclaim": 300,
                "compacted": 1, "top": []}
        with mock.patch.object(reclaim, "scan_claude", return_value=None), \
             mock.patch.object(reclaim, "scan_codex", return_value=None), \
             mock.patch.object(reclaim, "scan_opencode", return_value=None), \
             mock.patch.object(reclaim, "scan_antigravity", return_value=fake), \
             mock.patch.object(reclaim, "dir_size", return_value=0), \
             mock.patch.object(reclaim, "scan_skills",
                               return_value={"n": 0, "total": 0, "dupes": []}):
            with redirect_stdout(io.StringIO()):
                total, reclaimable = reclaim.scan()
        self.assertEqual((total, reclaimable), (1200, 300))

    def test_duplicate_skill_render_accepts_internal_tuple_shape(self):
        duplicate = ("demo", [("/a/skills", 10, False),
                               ("/b/skills", 10, False)], [])
        with mock.patch.object(reclaim, "scan_claude", return_value=None), \
             mock.patch.object(reclaim, "scan_codex", return_value=None), \
             mock.patch.object(reclaim, "scan_opencode", return_value=None), \
             mock.patch.object(reclaim, "scan_antigravity", return_value=None), \
             mock.patch.object(reclaim, "dir_size", return_value=0), \
             mock.patch.object(reclaim, "scan_skills",
                               return_value={"n": 2, "total": 20,
                                             "dupes": [duplicate]}):
            output = io.StringIO()
            with redirect_stdout(output):
                reclaim.scan()
        self.assertIn("demo", output.getvalue())


class ManifestTests(unittest.TestCase):
    def test_cache_apply_records_browser_recordings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            opencode = root / "opencode"
            codex = root / "codex"
            gemini = root / "gemini"
            (opencode / "tool-output").mkdir(parents=True)
            (opencode / "tool-output" / "tool.txt").write_text("x")
            (codex / "cache").mkdir(parents=True)
            (codex / "cache" / "cache.txt").write_text("x")
            recordings = gemini / "antigravity-ide" / "browser_recordings"
            recordings.mkdir(parents=True)
            (recordings / "frame.jpg").write_bytes(b"frame")
            paths = dict(reclaim.PATHS)
            paths.update({
                "opencode_dir": opencode,
                "codex_cache": codex / "cache",
                "codex_logs": [],
                "gemini": gemini,
                "antigravity_app": root / "no-app",
                "antigravity_ide_app": root / "no-ide",
            })
            args = argparse.Namespace(backup_dir=None, only="caches",
                                      no_antigravity_steps=False)
            with mock.patch.object(reclaim, "PATHS", paths), \
                 mock.patch.object(reclaim, "MANIFEST_DIR", root / "manifests"), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(reclaim.apply(args), 0)
            manifests = list((root / "manifests").glob("manifest-*.jsonl"))
            self.assertEqual(len(manifests), 1)
            actions = [json.loads(line)["action"]
                       for line in manifests[0].read_text().splitlines()]
            self.assertIn("delete_browser_recordings", actions)
            self.assertIn("delete_tool_output", actions)
            self.assertEqual(manifests[0].stat().st_mode & 0o777, 0o600)

    def test_backup_failure_aborts_before_apply(self):
        args = argparse.Namespace(backup_dir="/fake", only=None,
                                  no_antigravity_steps=False)
        with mock.patch.object(reclaim, "backup", side_effect=OSError("boom")), \
             mock.patch.object(reclaim, "apply_claude") as apply_claude, \
             redirect_stdout(io.StringIO()):
            self.assertEqual(reclaim.apply(args), 4)
        apply_claude.assert_not_called()

    def test_full_backup_includes_recordings_and_codex_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sources = root / "sources"
            (sources / "claude").mkdir(parents=True)
            (sources / "codex-sessions").mkdir()
            (sources / "opencode").mkdir()
            recordings = sources / "gemini" / "antigravity-ide" / "browser_recordings"
            recordings.mkdir(parents=True)
            (recordings / "frame.jpg").write_bytes(b"frame")
            opencode_db = sources / "opencode" / "opencode.db"
            sqlite3.connect(opencode_db).close()
            state = sources / "state.sqlite"
            sqlite3.connect(state).close()
            paths = dict(reclaim.PATHS)
            paths.update({
                "claude_projects": sources / "claude",
                "codex_sessions": sources / "codex-sessions",
                "codex_archived": sources / "missing-archived",
                "codex_cache": sources / "missing-cache",
                "codex_logs": [],
                "codex_state": state,
                "opencode_dir": sources / "opencode",
                "opencode_db": opencode_db,
                "commandcode": sources / "missing-command",
                "gemini": sources / "gemini",
                "antigravity_app": sources / "missing-app",
                "antigravity_ide_app": sources / "missing-ide",
            })
            with mock.patch.object(reclaim, "PATHS", paths), redirect_stdout(io.StringIO()):
                dest = reclaim.backup(root / "backups")
            self.assertTrue((dest / "codex-state.sqlite").exists())
            self.assertEqual((dest / "gemini-browser-recordings" / "frame.jpg").read_bytes(),
                             b"frame")


class OpenCodeTests(unittest.TestCase):
    def make_db(self, path):
        con = sqlite3.connect(path)
        con.executescript("""
            CREATE TABLE session(id TEXT PRIMARY KEY, time_created INTEGER);
            CREATE TABLE message(id TEXT PRIMARY KEY, session_id TEXT, data TEXT,
                                 time_created INTEGER);
            CREATE TABLE part(id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
                              data TEXT, time_created INTEGER);
            CREATE TABLE event(id INTEGER PRIMARY KEY, type TEXT, data TEXT);
        """)
        con.execute("INSERT INTO session VALUES ('s1', 1)")
        con.executemany("INSERT INTO message VALUES (?, 's1', '{}', ?)",
                        [("m1", 1), ("m2", 2), ("m3", 3)])
        con.execute("INSERT INTO part VALUES ('text', 'm3', 's1', ?, 30)",
                    (json.dumps({"type": "text", "text": "materialized content"}),))
        # Insertadas en orden inverso: la consulta debe elegir time_created=20.
        con.execute("INSERT INTO part VALUES ('new-comp', 'm3', 's1', ?, 20)",
                    (json.dumps({"type": "compaction", "tail_start_id": "m2"}),))
        con.execute("INSERT INTO part VALUES ('old-comp', 'm3', 's1', ?, 10)",
                    (json.dumps({"type": "compaction", "tail_start_id": "m1"}),))
        con.execute("INSERT INTO event(type, data) VALUES ('message.updated.1', '{}')")
        con.commit()
        con.close()

    def test_prune_uses_latest_compaction_and_returns_exit_code(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "opencode.db"
            self.make_db(db)
            paths = dict(reclaim.PATHS)
            paths["opencode_db"] = db
            with mock.patch.object(reclaim, "PATHS", paths), \
                 mock.patch.object(reclaim, "MANIFEST_DIR", root / "manifests"), \
                 mock.patch.object(reclaim, "database_in_use", return_value=False), \
                 redirect_stdout(io.StringIO()):
                code = reclaim.prune_opencode_db(no_backup=True)
            self.assertEqual(code, 0)
            con = sqlite3.connect(db)
            self.assertEqual([r[0] for r in con.execute(
                "SELECT id FROM message ORDER BY time_created")], ["m2", "m3"])
            self.assertEqual(con.execute("SELECT count(*) FROM event").fetchone()[0], 0)
            con.close()

    def test_missing_database_propagates_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            paths = dict(reclaim.PATHS)
            paths["opencode_db"] = Path(td) / "missing.db"
            with mock.patch.object(reclaim, "PATHS", paths), redirect_stdout(io.StringIO()):
                self.assertEqual(reclaim.main(["apply-db", "--no-backup"]), 1)

    def test_lsof_process_metadata_is_parsed(self):
        result = mock.Mock(returncode=0, stdout="p99998\ncOpenCode\n")
        with mock.patch.object(reclaim.subprocess, "run", return_value=result):
            users = reclaim.database_users("/tmp/opencode.db")
        self.assertEqual(users, [{"pid": 99998, "command": "OpenCode"}])

    def test_close_refuses_to_terminate_host_agent(self):
        users = [{"pid": 99998, "command": "OpenCode"}]
        with mock.patch.object(reclaim, "database_users", return_value=users), \
             mock.patch.object(reclaim, "process_is_current_ancestor", return_value=True), \
             mock.patch.object(reclaim.subprocess, "run") as run, \
             redirect_stdout(io.StringIO()):
            self.assertFalse(reclaim.close_opencode_for_cleanup("/tmp/opencode.db"))
        run.assert_not_called()

    def test_close_external_opencode_then_continues(self):
        users = [{"pid": 99998, "command": "OpenCode"}]
        with mock.patch.object(reclaim, "database_users", return_value=users), \
             mock.patch.object(reclaim, "process_is_current_ancestor", return_value=False), \
             mock.patch.object(reclaim, "database_in_use", return_value=False), \
             mock.patch.object(reclaim.sys, "platform", "darwin"), \
             mock.patch.object(reclaim.subprocess, "run") as run, \
             redirect_stdout(io.StringIO()):
            self.assertTrue(reclaim.close_opencode_for_cleanup("/tmp/opencode.db"))
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
