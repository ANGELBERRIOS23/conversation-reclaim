import argparse
import io
import json
import os
import sqlite3
import tempfile
import time
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
    def test_nested_claude_marker_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rollout.jsonl"
            write_jsonl(path, [
                {"type": "event", "payload": "keep"},
                {"type": "unrelated", "payload": {"type": "summary"}},
                {"type": "event", "payload": "recent"},
            ])
            before = path.read_bytes()
            result = reclaim.truncate_file_at_marker(
                path, reclaim.is_claude_compaction, "claude")
            self.assertFalse(result[2])
            self.assertEqual(path.read_bytes(), before)

    def test_structural_marker_trims_and_preserves_mode(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rollout.jsonl"
            write_jsonl(path, [
                {"type": "event", "payload": "old"},
                {"type": "summary", "payload": {"summary": "kept"}},
                {"type": "event", "payload": "recent"},
            ])
            path.chmod(0o600)
            cut, _size, done, _marker = reclaim.truncate_file_at_marker(
                path, reclaim.is_claude_compaction, "claude")
            self.assertTrue(done)
            self.assertGreater(cut, 0)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text().splitlines()[0])["type"],
                             "summary")

    def test_invalid_json_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rollout.jsonl"
            path.write_bytes(b'{"type":"event"}\nnot-json\n{"type":"summary"}\n')
            before = path.read_bytes()
            result = reclaim.truncate_file_at_marker(
                path, reclaim.is_claude_compaction, "claude")
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

    def test_codex_sessions_and_closed_subagents_are_preserved(self):
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
            user_rollout = sessions / "rollout-user.jsonl"
            write_jsonl(user_rollout, [
                {"type": "session_meta", "payload": {"id": "user"}},
                {"type": "compacted", "payload": {"message": "summary"}},
            ])
            before = (rollout.read_bytes(), user_rollout.read_bytes())
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
            self.assertEqual((freed, entries), (0, []))
            self.assertEqual((rollout.read_bytes(), user_rollout.read_bytes()), before)
            con = sqlite3.connect(state)
            self.assertEqual(con.execute("SELECT count(*) FROM threads").fetchone()[0], 1)
            self.assertEqual(con.execute(
                "SELECT count(*) FROM thread_spawn_edges").fetchone()[0], 1)
            con.close()
            con = sqlite3.connect(log_db)
            self.assertEqual(con.execute("SELECT count(*) FROM logs").fetchone()[0], 1)
            con.close()

    def test_scan_codex_does_not_advertise_session_trimming(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sessions = root / "sessions"
            archived = root / "archived_sessions"
            sessions.mkdir()
            archived.mkdir()
            write_jsonl(sessions / "rollout-user.jsonl", [
                {"type": "session_meta", "payload": {"id": "user"}},
                {"type": "compacted", "payload": {"message": "summary"}},
            ])
            write_jsonl(archived / "rollout-old.jsonl", [
                {"type": "session_meta", "payload": {"id": "old"}},
            ])
            paths = dict(reclaim.PATHS)
            paths.update({"codex_sessions": sessions, "codex_archived": archived})
            with mock.patch.object(reclaim, "PATHS", paths):
                result = reclaim.scan_codex()
            self.assertGreater(result["total"], 0)
            self.assertGreater(result["archived"], 0)
            self.assertEqual(result["reclaim"], 0)
            self.assertEqual(result["subagents_bytes"], 0)


class MetricsTests(unittest.TestCase):
    def test_antigravity_is_included_in_total(self):
        fake = {"conv_total": 1000, "wal_total": 200, "reclaim": 300,
                "compacted": 1, "top": []}
        with mock.patch.object(reclaim, "scan_claude", return_value=None), \
             mock.patch.object(reclaim, "scan_codex", return_value=None), \
             mock.patch.object(reclaim, "scan_opencode", return_value=None), \
             mock.patch.object(reclaim, "scan_antigravity", return_value=fake), \
             mock.patch.object(reclaim, "scan_transient_media",
                               return_value={"files": [], "n": 0, "total": 0}), \
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
             mock.patch.object(reclaim, "scan_transient_media",
                               return_value={"files": [], "n": 0, "total": 0}), \
             mock.patch.object(reclaim, "dir_size", return_value=0), \
             mock.patch.object(reclaim, "scan_skills",
                               return_value={"n": 2, "total": 20,
                                             "dupes": [duplicate]}):
            output = io.StringIO()
            with redirect_stdout(output):
                reclaim.scan()
        self.assertIn("demo", output.getvalue())


class TemporaryMediaTests(unittest.TestCase):
    def test_discovers_only_old_structural_temporary_images(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            temp = root / "temp"
            temp.mkdir()
            old_codex = temp / "codex-clipboard-old.png"
            recent_codex = temp / "codex-clipboard-recent.png"
            unrelated = temp / "other.png"
            for path in (old_codex, recent_codex, unrelated):
                path.write_bytes(b"image")

            gemini = root / "gemini"
            media = gemini / "antigravity" / "brain" / "task" / ".tempmediaStorage" / "frame.webp"
            deliverable = gemini / "antigravity" / "brain" / "task" / "deliverables" / "result.png"
            media.parent.mkdir(parents=True)
            deliverable.parent.mkdir(parents=True)
            media.write_bytes(b"temp")
            deliverable.write_bytes(b"keep")

            now = 2_000_000_000
            old = now - reclaim.TRANSIENT_MEDIA_MIN_AGE - 1
            for path in (old_codex, unrelated, media, deliverable):
                os.utime(path, (old, old))
            os.utime(recent_codex, (now, now))
            paths = dict(reclaim.PATHS)
            paths["gemini"] = gemini
            with mock.patch.object(reclaim, "PATHS", paths), \
                 mock.patch.object(reclaim.tempfile, "gettempdir", return_value=str(temp)):
                found = reclaim.transient_media_candidates(reference_time=now)
            self.assertEqual(set(found), {old_codex, media})

    def test_optional_apply_removes_old_media_and_records_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old_media = root / "codex-clipboard-old.png"
            old_media.write_bytes(b"image")
            args = argparse.Namespace(backup_dir=None, only="media",
                                      include_media=False,
                                      no_antigravity_steps=False)
            with mock.patch.object(reclaim, "transient_media_candidates",
                                   return_value=[old_media]), \
                 mock.patch.object(reclaim, "database_in_use", return_value=False), \
                 mock.patch.object(reclaim, "MANIFEST_DIR", root / "manifests"), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(reclaim.apply(args), 0)
            self.assertFalse(old_media.exists())
            manifest = next((root / "manifests").glob("manifest-*.jsonl"))
            record = json.loads(manifest.read_text().splitlines()[0])
            self.assertEqual(record["action"], "delete_temporary_attachment")


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

    def test_windows_close_requests_normal_window_close(self):
        users = [{"pid": 4321, "command": "OpenCode"}]
        with mock.patch.object(reclaim, "_IS_WIN", True), \
             mock.patch.object(reclaim, "database_users", return_value=users), \
             mock.patch.object(reclaim, "process_is_current_ancestor", return_value=False), \
             mock.patch.object(reclaim, "_request_windows_process_close",
                               return_value={4321}) as request_close, \
             mock.patch.object(reclaim, "database_in_use", return_value=False), \
             redirect_stdout(io.StringIO()):
            self.assertTrue(reclaim.close_opencode_for_cleanup("C:/opencode.db"))
        request_close.assert_called_once()

    def test_windows_close_refuses_unknown_database_holder(self):
        users = [{"pid": 4321, "command": "BackupService"}]
        with mock.patch.object(reclaim, "_IS_WIN", True), \
             mock.patch.object(reclaim, "database_users", return_value=users), \
             mock.patch.object(reclaim, "process_is_current_ancestor", return_value=False), \
             mock.patch.object(reclaim, "_request_windows_process_close") as request_close, \
             redirect_stdout(io.StringIO()):
            self.assertFalse(reclaim.close_opencode_for_cleanup("C:/opencode.db"))
        request_close.assert_not_called()


class OpenCodeV2Tests(unittest.TestCase):
    """OpenCode 2 importa las sesiones de OpenCode 1 a session_v2/session_message
    y deja la copia vieja en message/part."""

    def make_db(self, path, phase="completed", with_v1=True):
        con = sqlite3.connect(path)
        con.executescript("""
            CREATE TABLE event(id INTEGER PRIMARY KEY, type TEXT, data TEXT);
            CREATE TABLE kv(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE session_v2(id TEXT PRIMARY KEY, time_created INTEGER);
            CREATE TABLE session_message(id TEXT PRIMARY KEY, session_id TEXT,
                                         type TEXT, seq INTEGER, data TEXT);
        """)
        if phase:
            con.execute("INSERT INTO kv VALUES ('migration.v1-v2', ?)",
                        (json.dumps({"phase": phase}),))
        for sid in ("s1", "s2"):
            con.execute("INSERT INTO session_v2 VALUES (?, 1)", (sid,))
            con.execute("INSERT INTO session_message VALUES (?, ?, 'user', 0, ?)",
                        (f"u-{sid}", sid, json.dumps({"text": "pregunta"})))
            con.execute("INSERT INTO session_message VALUES (?, ?, 'assistant', 1, ?)",
                        (f"a-{sid}", sid, json.dumps({"content": [
                            {"type": "text", "text": "respuesta completa"}]})))
        if with_v1:
            con.executescript("""
                CREATE TABLE session(id TEXT PRIMARY KEY, time_created INTEGER);
                CREATE TABLE message(id TEXT PRIMARY KEY, session_id TEXT, data TEXT,
                                     time_created INTEGER);
                CREATE TABLE part(id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
                                  data TEXT, time_created INTEGER);
            """)
            for sid in ("s1", "s2"):
                con.execute("INSERT INTO session VALUES (?, 1)", (sid,))
                con.execute("INSERT INTO message VALUES (?, ?, '{}', 1)", (f"m-{sid}", sid))
                con.execute("INSERT INTO part VALUES (?, ?, ?, ?, 1)",
                            (f"p-{sid}", f"m-{sid}", sid,
                             json.dumps({"type": "text", "text": "respuesta completa"})))
            # s2 tiene un razonamiento que OpenCode 2 no importó: su copia se queda.
            con.execute("INSERT INTO part VALUES ('p-extra', 'm-s2', 's2', ?, 2)",
                        (json.dumps({"type": "reasoning", "text": "solo en la copia vieja"}),))
        con.commit()
        con.close()

    def prune(self, db, root, **kwargs):
        paths = dict(reclaim.PATHS)
        paths["opencode_db"] = db
        with mock.patch.object(reclaim, "PATHS", paths), \
             mock.patch.object(reclaim, "MANIFEST_DIR", root / "manifests"), \
             mock.patch.object(reclaim, "database_in_use", return_value=False), \
             redirect_stdout(io.StringIO()):
            return reclaim.prune_opencode_db(no_backup=True, **kwargs)

    def v1_sessions(self, db):
        con = sqlite3.connect(db)
        rows = sorted(r[0] for r in con.execute("SELECT DISTINCT session_id FROM message"))
        con.close()
        return rows

    def test_drop_v1_copy_only_drops_verified_sessions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "opencode.db"
            self.make_db(db)
            self.assertEqual(self.prune(db, root, drop_v1_copy=True), 0)
            self.assertEqual(self.v1_sessions(db), ["s2"])
            con = sqlite3.connect(db)
            self.assertEqual(con.execute("SELECT count(*) FROM session_message").fetchone()[0], 4)
            self.assertEqual(con.execute("SELECT count(*) FROM session").fetchone()[0], 2)
            con.close()

    def test_without_flag_the_v1_copy_is_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "opencode.db"
            self.make_db(db)
            self.assertEqual(self.prune(db, root), 0)
            self.assertEqual(self.v1_sessions(db), ["s1", "s2"])

    def test_v1_copy_is_kept_while_import_is_pending(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "opencode.db"
            self.make_db(db, phase="sessions")
            self.assertEqual(self.prune(db, root, drop_v1_copy=True), 0)
            self.assertEqual(self.v1_sessions(db), ["s1", "s2"])

    def test_fresh_v2_database_without_v1_tables(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "opencode.db"
            self.make_db(db, phase=None, with_v1=False)
            self.assertEqual(self.prune(db, root, drop_v1_copy=True), 0)
            paths = dict(reclaim.PATHS)
            paths["opencode_db"] = db
            with mock.patch.object(reclaim, "PATHS", paths):
                result = reclaim.scan_opencode()
            self.assertEqual(result["v1_copy"], (0, 0))
            self.assertEqual(result["redundant"], (0, 0))

    def test_scan_reports_the_v1_copy(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "opencode.db"
            self.make_db(db)
            paths = dict(reclaim.PATHS)
            paths["opencode_db"] = db
            with mock.patch.object(reclaim, "PATHS", paths):
                result = reclaim.scan_opencode()
            self.assertEqual(result["v1_copy"][0], 2)
            self.assertGreater(result["v1_copy"][1], 0)
            self.assertEqual(result["compactions"], 0)

    def test_close_stops_v2_background_service_instead_of_the_app(self):
        users = [{"pid": 24337, "command": "opencode.exe"}]
        with mock.patch.object(reclaim, "database_users", return_value=users), \
             mock.patch.object(reclaim, "process_is_current_ancestor", return_value=False), \
             mock.patch.object(reclaim, "stop_opencode_service", return_value=True) as stop, \
             mock.patch.object(reclaim, "database_in_use", return_value=False), \
             mock.patch.object(reclaim.sys, "platform", "darwin"), \
             mock.patch.object(reclaim.subprocess, "run") as run, \
             redirect_stdout(io.StringIO()):
            self.assertTrue(reclaim.close_opencode_for_cleanup("/tmp/opencode.db"))
        stop.assert_called_once()
        run.assert_not_called()

    def test_service_is_not_stopped_when_cli_is_v1(self):
        with mock.patch.object(reclaim, "_opencode_cli_major", return_value=1), \
             mock.patch.object(reclaim.subprocess, "run") as run:
            self.assertFalse(reclaim.stop_opencode_service())
        run.assert_not_called()


BIG = "A" * 4000


def claude_image(data=BIG, media_type="image/png"):
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


class ExtrasImageTests(unittest.TestCase):
    def test_tiny_png_is_a_valid_one_pixel_png(self):
        import base64
        raw = base64.b64decode(reclaim.TINY_PNG_B64)
        self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(raw[16:24], (1).to_bytes(4, "big") * 2)

    def test_claude_model_images_become_text_and_ui_copies_shrink(self):
        record = {
            "message": {"role": "user", "content": [
                claude_image(),
                {"type": "tool_result", "content": [claude_image(), {"type": "text", "text": "ok"}]},
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                "data": BIG}},
                {"type": "thinking", "thinking": "x", "signature": BIG},
            ]},
            "toolUseResult": {"type": "image", "file": {"base64": BIG, "type": "image/jpeg"}},
        }
        removed = reclaim.strip_claude_images(record)
        content = record["message"]["content"]
        self.assertEqual(content[0], {"type": "text", "text": reclaim.IMAGE_PLACEHOLDER})
        self.assertEqual(content[1]["content"][0]["type"], "text")
        self.assertEqual(content[2]["source"]["data"], BIG)
        self.assertEqual(content[3]["signature"], BIG)
        self.assertEqual(record["toolUseResult"]["file"]["base64"], reclaim.TINY_PNG_B64)
        self.assertEqual(record["toolUseResult"]["file"]["type"], "image/png")
        self.assertGreater(removed, 3 * 3000)

    def test_small_images_are_left_alone(self):
        record = {"message": {"content": [claude_image("A" * 100)]}}
        self.assertEqual(reclaim.strip_claude_images(record), 0)
        self.assertEqual(record["message"]["content"][0]["type"], "image")

    def test_codex_images_become_input_text_and_meta_is_untouched(self):
        url = "data:image/png;base64," + BIG
        records = [
            {"ordinal": 0, "type": "session_meta", "payload": {"id": "x"}},
            {"ordinal": 1, "type": "response_item", "payload": {
                "type": "function_call_output",
                "output": [{"type": "input_image", "image_url": url}]}},
            {"ordinal": 2, "type": "event_msg", "payload": {
                "item": {"result": {"screenshot": {"url": url}}}}},
        ]
        meta = json.dumps(records[0])
        removed = sum(reclaim.strip_codex_images(r) for r in records)
        self.assertEqual(json.dumps(records[0]), meta)
        self.assertEqual(records[1]["payload"]["output"][0],
                         {"type": "input_text", "text": reclaim.IMAGE_PLACEHOLDER})
        self.assertEqual(records[2]["payload"]["item"]["result"]["screenshot"]["url"],
                         reclaim.TINY_PNG_URL)
        self.assertEqual(records[1]["ordinal"], 1)
        self.assertGreater(removed, 2 * 3000)

    def test_rewrite_keeps_other_lines_byte_identical_and_saves_original(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "session.jsonl"
            plain = b'{"type": "user",  "message": {"content": "hola \xc3\xb1"}}'
            image = json.dumps({"message": {"content": [claude_image()]}}).encode()
            path.write_bytes(plain + b"\n" + image + b"\n")
            os.utime(path, (1_000_000, 1_000_000))
            original = path.read_bytes()
            trash = root / "trash"
            with mock.patch.object(reclaim, "HOME", root):
                cut, done, error = reclaim.rewrite_session_without_images(path, "claude", trash)
            self.assertIsNone(error)
            self.assertTrue(done)
            lines = path.read_bytes().split(b"\n")
            self.assertEqual(lines[0], plain)
            self.assertEqual(lines[2], b"")
            self.assertEqual(json.loads(lines[1])["message"]["content"][0]["type"], "text")
            self.assertEqual(path.stat().st_mtime, 1_000_000)
            self.assertEqual(cut, len(original) - path.stat().st_size)
            self.assertEqual((trash / "session.jsonl").read_bytes(), original)

    def test_invalid_json_with_image_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "session.jsonl"
            path.write_bytes(b'{"base64": broken\n')
            before = path.read_bytes()
            cut, done, error = reclaim.rewrite_session_without_images(path, "claude")
            self.assertFalse(done)
            self.assertIn("JSON", error)
            self.assertEqual(path.read_bytes(), before)

    @unittest.skipUnless(reclaim._zstd(), "zstd no está instalado")
    def test_compressed_codex_session_round_trips(self):
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rollout.jsonl.zst"
            body = (json.dumps({"type": "session_meta", "payload": {}}) + "\n" +
                    json.dumps({"payload": {"content": [
                        {"type": "input_image", "image_url": "data:image/png;base64," + BIG}]}}) + "\n")
            path.write_bytes(subprocess.run([reclaim._zstd(), "-q", "-c", "-"],
                                            input=body.encode(), capture_output=True).stdout)
            cut, done, error = reclaim.rewrite_session_without_images(path, "codex")
            self.assertIsNone(error)
            self.assertTrue(done)
            text = reclaim._read_session(path).decode()
            self.assertEqual(text.splitlines()[0], body.splitlines()[0])
            self.assertIn(reclaim.IMAGE_PLACEHOLDER, text)
            self.assertNotIn(BIG, text)

    def codex_home(self, root, next_ordinal):
        sessions = root / ".codex" / "sessions"
        sessions.mkdir(parents=True)
        thread = "01a0aaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee"
        path = sessions / f"rollout-2026-09-01T10-00-00-{thread}.jsonl"
        lines = [json.dumps({"ordinal": 0, "type": "session_meta", "payload": {}}),
                 json.dumps({"ordinal": 1, "payload": {"content": [
                     {"type": "input_image", "image_url": "data:image/png;base64," + BIG}]}}),
                 json.dumps({"ordinal": 2, "payload": {"text": "hola"}})]
        path.write_bytes(("\n".join(lines) + "\n").encode())
        db = root / ".codex" / "thread_history_1.sqlite"
        with closing_db(db) as con:
            con.execute("CREATE TABLE thread_history_projection_state (thread_id TEXT PRIMARY KEY, "
                        "next_rollout_byte_offset INTEGER NOT NULL, next_rollout_ordinal INTEGER NOT NULL)")
            con.execute("INSERT INTO thread_history_projection_state VALUES (?, ?, ?)",
                        (thread, path.stat().st_size, next_ordinal))
        paths = dict(reclaim.PATHS)
        paths["codex_sessions"] = sessions
        return path, db, thread, paths

    def test_codex_read_position_moves_with_the_smaller_file(self):
        for next_ordinal, expect in ((3, "end"), (2, "line2")):
            with tempfile.TemporaryDirectory() as td:
                path, db, thread, paths = self.codex_home(Path(td), next_ordinal)
                with mock.patch.object(reclaim, "PATHS", paths), \
                     mock.patch.object(reclaim, "database_in_use", return_value=False):
                    cut, done, error = reclaim.rewrite_session_without_images(path, "codex")
                self.assertIsNone(error)
                new = path.read_bytes()
                offset = sqlite3.connect(str(db)).execute(
                    "select next_rollout_byte_offset from thread_history_projection_state").fetchone()[0]
                if expect == "end":
                    self.assertEqual(offset, len(new))
                else:
                    self.assertEqual(json.loads(new[offset:].split(b"\n")[0])["ordinal"], 2)
                self.assertFalse(any(p.name.endswith(".reclaim-orig") for p in path.parent.iterdir()))

    def test_codex_open_means_the_session_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            path, db, thread, paths = self.codex_home(Path(td), 3)
            before = path.read_bytes()
            with mock.patch.object(reclaim, "PATHS", paths), \
                 mock.patch.object(reclaim, "database_in_use", return_value=True):
                cut, done, error = reclaim.rewrite_session_without_images(path, "codex")
            self.assertFalse(done)
            self.assertIn("Codex", error)
            self.assertEqual(path.read_bytes(), before)

    def test_candidates_respect_age_and_skip_other_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            projects = root / "projects" / "p"
            projects.mkdir(parents=True)
            old = projects / "old.jsonl"
            new = projects / "new.jsonl"
            other = projects / "notes.txt"
            for f in (old, new, other):
                f.write_text("{}\n")
            now = 2_000_000_000
            os.utime(old, (now - 15 * 86400, now - 15 * 86400))
            os.utime(new, (now - 86400, now - 86400))
            os.utime(other, (now - 30 * 86400, now - 30 * 86400))
            paths = dict(reclaim.PATHS)
            paths.update(claude_projects=root / "projects", codex_sessions=root / "none",
                         codex_archived=root / "none2")
            with mock.patch.object(reclaim, "PATHS", paths):
                found = reclaim.conversation_image_candidates(14, reference_time=now)
            self.assertEqual(found, [("claude", old)])


class ExtrasFilesTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.now = 2_000_000_000
        self.old = self.now - 20 * 86400

    def tearDown(self):
        self.td.cleanup()

    def make(self, relative, data=b"x", old=True):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        stamp = self.old if old else self.now
        os.utime(path, (stamp, stamp))
        return path

    def test_codex_work_only_takes_old_media_outside_outputs_and_git(self):
        task = "ws/2026-09-01/tarea"
        shot = self.make(f"{task}/work/captura.png")
        video = self.make(f"{task}/work/render/borrador.mp4")
        self.make(f"{task}/work/nuevo.png", old=False)
        self.make(f"{task}/work/script.py")
        self.make(f"{task}/outputs/final.png")
        self.make(f"{task}/work/repo/.git/HEAD")
        self.make(f"{task}/work/repo/docs/foto.png")
        self.make("ws/2026-09-01/sin-entrega/work/entrega-real.mp4")
        paths = dict(reclaim.PATHS)
        paths["codex_workspace"] = self.root / "ws"
        with mock.patch.object(reclaim, "PATHS", paths):
            found = reclaim.codex_work_candidates(14, reference_time=self.now)
        self.assertEqual(sorted(found), sorted([shot, video]))

    def test_generated_images_go_only_when_an_identical_copy_exists(self):
        copied = self.make("gen/a/copied.png", b"same bytes")
        self.make("gen/a/unique.png", b"only here")
        self.make("docs/proyecto/entrega.png", b"same bytes")
        self.make("docs/proyecto/otra.png", b"different!")
        work_copy = self.make("gen/b/work-only.png", b"copied to work")
        self.make("docs/2026-09-01/tarea/work/borrador.png", b"copied to work")
        paths = dict(reclaim.PATHS)
        paths["codex_generated"] = self.root / "gen"
        paths["codex_workspace"] = self.root / "docs"
        with mock.patch.object(reclaim, "PATHS", paths):
            found = reclaim.codex_generated_duplicates(
                14, search_roots=[self.root / "docs"], reference_time=self.now)
        self.assertEqual([g for g, _c in found], [copied])
        self.assertTrue(work_copy.exists())

    def test_old_versions_keep_current_and_newest(self):
        versions = self.root / "versions"
        for name in ("2.1.280", "2.1.281", "2.1.288", "2.1.290"):
            self.make(f"versions/{name}")
        link = self.root / "bin" / "claude"
        link.parent.mkdir()
        link.symlink_to(versions / "2.1.288")
        releases = self.root / "releases"
        for name in ("0.150.1-aarch64", "0.160.0-aarch64"):
            self.make(f"releases/{name}/codex")
        current = self.root / "current"
        current.symlink_to(releases / "0.160.0-aarch64")
        self.make("history/old-session/file@v1")
        self.make("history/new-session/file@v1", old=False)
        paths = dict(reclaim.PATHS)
        paths.update(claude_cli_versions=versions, claude_cli_link=link,
                     claude_desktop_code=self.root / "none", codex_releases=releases,
                     codex_current=current, claude_file_history=self.root / "history",
                     codex_sessions=self.root / "none", codex_archived=self.root / "none")
        with mock.patch.object(reclaim, "PATHS", paths), \
             mock.patch.object(reclaim, "_running_commands", return_value=[]), \
             mock.patch.object(reclaim, "database_in_use", return_value=False):
            found = {p.name for _label, p in
                     reclaim.old_version_candidates(14, reference_time=self.now)}
        self.assertEqual(found, {"2.1.280", "2.1.281", "0.150.1-aarch64", "old-session"})

    def test_apply_extras_moves_to_trash_and_records_manifest(self):
        task = "ws/2026-09-01/tarea"
        shot = self.make(f"{task}/work/captura.png", b"png")
        self.make(f"{task}/outputs/final.pdf")
        args = argparse.Namespace(backup_dir=None, only="codex-work", include_media=False,
                                  no_antigravity_steps=False, extras=False,
                                  older_than=14, trash=True)
        paths = dict(reclaim.PATHS)
        paths.update(codex_workspace=self.root / "ws", codex_generated=self.root / "none")
        trash = self.root / "Trash" / "reclaim"
        with mock.patch.object(reclaim, "PATHS", paths), \
             mock.patch.object(reclaim, "HOME", self.root), \
             mock.patch.object(reclaim, "trash_root", return_value=trash), \
             mock.patch.object(reclaim, "_cutoff", return_value=self.now - 14 * 86400), \
             mock.patch.object(reclaim, "database_in_use", return_value=False), \
             mock.patch.object(reclaim, "MANIFEST_DIR", self.root / "manifests"), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(reclaim.apply(args), 0)
        self.assertFalse(shot.exists())
        self.assertTrue((trash / f"{task}/work/captura.png").exists())
        manifest = next((self.root / "manifests").glob("manifest-*.jsonl"))
        self.assertEqual(json.loads(manifest.read_text().splitlines()[0])["action"],
                         "delete_work_media")


class OutputsFixture(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.ws = self.root / "Documents" / "Codex"
        self.paths = dict(reclaim.PATHS)
        self.paths["codex_workspace"] = self.ws

    def tearDown(self):
        self.td.cleanup()

    def make(self, relative, size, fill=b"x"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fill * size)
        return path

    def listing(self, **kwargs):
        with mock.patch.object(reclaim, "PATHS", self.paths), \
             mock.patch.object(reclaim, "HOME", self.root), \
             mock.patch.object(reclaim, "OUTPUTS_MIN_BYTES", 10):
            return reclaim.codex_outputs(**kwargs)


class OutputsListTests(OutputsFixture):
    def test_lists_outputs_biggest_first_and_skips_work_and_small_files(self):
        big = self.make("Documents/Codex/d/t/outputs/video.mp4", 300)
        mid = self.make("Documents/Codex/d/t/outputs/sub/foto.png", 200)
        self.make("Documents/Codex/d/t/outputs/nota.txt", 5)
        self.make("Documents/Codex/d/t/work/borrador.mp4", 900)
        items = self.listing(find_copies=False)
        self.assertEqual([i["path"] for i in items], [big, mid])
        self.assertEqual(items[0]["task"], "d/t")

    def test_marks_copies_outside_codex_but_not_inside(self):
        shared = self.make("Documents/Codex/d/t/outputs/a.mp4", 100, b"a")
        only_codex = self.make("Documents/Codex/d/t/outputs/b.mp4", 100, b"b")
        copy = self.make("Downloads/a.mp4", 100, b"a")
        self.make("Documents/Codex/d/otra/outputs/b-copia.mp4", 100, b"b")
        items = {i["path"]: i for i in self.listing()}
        self.assertEqual(items[shared]["copy"], copy)
        self.assertIsNone(items[only_codex]["copy"])

    def test_only_explicit_v_numbers_count_as_versions(self):
        v1 = self.make("Documents/Codex/d/t/outputs/spot_v1.mp4", 100)
        v2 = self.make("Documents/Codex/d/t/outputs/spot_v2.mp4", 100)
        series = self.make("Documents/Codex/d/t/outputs/Audio 03.wav", 100)
        self.make("Documents/Codex/d/t/outputs/Audio 04.wav", 100)
        unnamed = self.make("Documents/Codex/d/t/outputs/File-unnamed (1).png", 100)
        self.make("Documents/Codex/d/t/outputs/File-unnamed (2).png", 100)
        items = {i["path"]: i for i in self.listing(find_copies=False)}
        self.assertEqual(items[v1]["newer"], v2)
        self.assertIsNone(items[v2]["newer"])
        self.assertIsNone(items[series]["newer"])
        self.assertIsNone(items[unnamed]["newer"])

    def test_trash_by_number_moves_only_the_chosen_file(self):
        big = self.make("Documents/Codex/d/t/outputs/video.mp4", 300)
        small = self.make("Documents/Codex/d/t/outputs/foto.png", 200)
        items = self.listing(find_copies=False)
        trash = self.root / ".Trash" / "reclaim"
        with mock.patch.object(reclaim, "PATHS", self.paths), \
             mock.patch.object(reclaim, "HOME", self.root), \
             mock.patch.object(reclaim, "trash_root", return_value=trash), \
             mock.patch.object(reclaim, "MANIFEST_DIR", self.root / "manifests"), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(reclaim.trash_outputs(items, ["1"]), 0)
            self.assertEqual(reclaim.trash_outputs(items, ["9"]), 2)
            self.assertEqual(reclaim.trash_outputs(items, [str(self.root / "otro.mp4")]), 2)
        self.assertFalse(big.exists())
        self.assertTrue(small.exists())
        self.assertTrue((trash / "Documents/Codex/d/t/outputs/video.mp4").exists())


class OpenListedTests(OutputsFixture):
    def test_open_and_reveal_use_the_listed_file_only(self):
        video = self.make("Documents/Codex/d/t/outputs/video.mp4", 300)
        items = self.listing(find_copies=False)
        with mock.patch.object(reclaim.subprocess, "run") as run, \
             mock.patch.object(reclaim.sys, "platform", "darwin"), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(reclaim.open_listed(items, "1"), 0)
            self.assertEqual(reclaim.open_listed(items, "1", reveal=True), 0)
            self.assertEqual(reclaim.open_listed(items, str(self.root / "otro.mp4")), 2)
        self.assertEqual(run.call_args_list[0].args[0], ["open", str(video)])
        self.assertEqual(run.call_args_list[1].args[0], ["open", "-R", str(video)])
        self.assertEqual(run.call_count, 2)


class OrphanTests(OutputsFixture):
    def setUp(self):
        super().setUp()
        self.paths["codex_sessions"] = self.root / ".codex" / "sessions"
        self.paths["codex_archived"] = self.root / ".codex" / "archived_sessions"

    def session(self, relative, cwd):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": str(cwd)}}) + "\n")

    def age(self, relative, days=10):
        stamp = time.time() - days * 86400
        for current, _dirs, files in os.walk(self.root / relative):
            for name in files:
                os.utime(os.path.join(current, name), (stamp, stamp))

    def orphans(self):
        with mock.patch.object(reclaim, "PATHS", self.paths), \
             mock.patch.object(reclaim, "HOME", self.root):
            return [str(p.relative_to(self.ws)) for p in reclaim.codex_orphan_dirs()]

    def test_only_old_dated_folders_without_session_are_orphans(self):
        self.make("Documents/Codex/2026-09-01/alive/outputs/a.mp4", 20)
        self.make("Documents/Codex/2026-09-01/archived/work/b.png", 20)
        self.make("Documents/Codex/2026-09-01/gone/outputs/c.mp4", 20)
        self.make("Documents/Codex/2026-09-02/gone-today/outputs/d.mp4", 20)
        self.make("Documents/Codex/integraciones/tool/x.txt", 20)
        self.session(".codex/sessions/2026/09/01/rollout-1.jsonl", self.ws / "2026-09-01/alive/work")
        self.session(".codex/archived_sessions/rollout-2.jsonl", self.ws / "2026-09-01/archived")
        for folder in ("Documents/Codex/2026-09-01", "Documents/Codex/integraciones"):
            self.age(folder)
        self.assertEqual(self.orphans(), ["2026-09-01/gone"])

    def test_nothing_is_orphan_when_a_session_cannot_be_read(self):
        self.make("Documents/Codex/2026-09-01/gone/outputs/c.mp4", 20)
        self.age("Documents/Codex/2026-09-01")
        self.assertEqual(self.orphans(), [], "no sessions at all")
        broken = self.root / ".codex/sessions/rollout-broken.jsonl"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text("not json\n")
        self.assertEqual(self.orphans(), [])

    def test_outputs_from_deleted_chats_are_marked_and_folders_go_to_trash(self):
        gone = self.make("Documents/Codex/2026-09-01/gone/outputs/c.mp4", 20)
        alive = self.make("Documents/Codex/2026-09-01/alive/outputs/a.mp4", 20)
        self.session(".codex/sessions/rollout-1.jsonl", self.ws / "2026-09-01/alive")
        self.age("Documents/Codex/2026-09-01")
        items = {i["path"]: i for i in self.listing(find_copies=False)}
        self.assertTrue(items[gone]["orphan"])
        self.assertFalse(items[alive]["orphan"])
        trash = self.root / ".Trash" / "reclaim"
        with mock.patch.object(reclaim, "PATHS", self.paths), \
             mock.patch.object(reclaim, "HOME", self.root), \
             mock.patch.object(reclaim, "trash_root", return_value=trash), \
             mock.patch.object(reclaim, "MANIFEST_DIR", self.root / "manifests"), \
             redirect_stdout(io.StringIO()):
            folders = reclaim.codex_orphan_list()
            self.assertEqual([f["task"] for f in folders], ["2026-09-01/gone"])
            self.assertEqual(reclaim.trash_outputs(folders, ["1"], action="trash_orphan_folder"), 0)
        self.assertFalse((self.ws / "2026-09-01/gone").exists())
        self.assertTrue(alive.exists())
        self.assertTrue((trash / "Documents/Codex/2026-09-01/gone/outputs/c.mp4").exists())


class GeneratedListTests(OutputsFixture):
    def setUp(self):
        super().setUp()
        self.paths["codex_generated"] = self.root / ".codex" / "generated_images"
        self.paths["codex_state"] = self.root / ".codex" / "state_5.sqlite"

    def generated(self):
        with mock.patch.object(reclaim, "PATHS", self.paths), \
             mock.patch.object(reclaim, "HOME", self.root):
            return reclaim.codex_generated_list()

    def test_recommended_first_with_chat_title(self):
        self.make(".codex/generated_images/chat-a/big.png", 300, b"a")
        self.make(".codex/generated_images/chat-a/.DS_Store", 40, b"d")
        self.make("Downloads/.DS_Store", 40, b"d")
        copied = self.make(".codex/generated_images/chat-b/copied.png", 100, b"b")
        self.make("Downloads/logo.png", 100, b"b")
        in_work = self.make(".codex/generated_images/chat-a/draft.png", 200, b"c")
        self.make("Documents/Codex/d/t/work/draft.png", 200, b"c")
        self.paths["codex_state"].parent.mkdir(parents=True, exist_ok=True)
        with closing_db(self.paths["codex_state"]) as con:
            con.execute("CREATE TABLE threads (id TEXT, title TEXT, name TEXT)")
            con.execute("INSERT INTO threads VALUES ('chat-b', 'Logo para la web', NULL)")
        items = self.generated()
        self.assertEqual(items[0]["path"], copied)
        self.assertEqual(items[0]["task"], "Logo para la web")
        self.assertEqual(items[0]["copy"], self.root / "Downloads/logo.png")
        self.assertIsNone(next(i for i in items if i["path"] == in_work)["copy"])
        self.assertFalse(any(i["path"].name == ".DS_Store" for i in items))

    def test_trash_stays_inside_generated_images(self):
        image = self.make(".codex/generated_images/chat/x.png", 50)
        items = self.generated()
        trash = self.root / ".Trash" / "reclaim"
        with mock.patch.object(reclaim, "PATHS", self.paths), \
             mock.patch.object(reclaim, "HOME", self.root), \
             mock.patch.object(reclaim, "trash_root", return_value=trash), \
             mock.patch.object(reclaim, "MANIFEST_DIR", self.root / "manifests"), \
             redirect_stdout(io.StringIO()):
            outside = str(self.make("Downloads/mine.png", 50))
            self.assertEqual(reclaim.trash_outputs(items, [outside],
                                                   root=self.paths["codex_generated"]), 2)
            self.assertEqual(reclaim.trash_outputs(items, ["1"], root=self.paths["codex_generated"],
                                                   action="trash_generated_image"), 0)
        self.assertFalse(image.exists())
        self.assertTrue((trash / ".codex/generated_images/chat/x.png").exists())


def closing_db(path):
    from contextlib import closing as _closing
    con = sqlite3.connect(str(path))
    con.isolation_level = None
    return _closing(con)


if __name__ == "__main__":
    unittest.main()
