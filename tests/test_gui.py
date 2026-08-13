import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import gui


class GuiScanTests(unittest.TestCase):
    def test_scan_builds_selectable_recommended_categories(self):
        with mock.patch.object(gui.reclaim, "scan_claude", return_value={
                "reclaim": 100, "subagents_bytes": 20, "workflows_bytes": 0,
                "subagents_n": 2}), \
             mock.patch.object(gui.reclaim, "scan_codex", return_value={
                 "reclaim": 50, "subagents_bytes": 10, "subagents_n": 1,
                 "active_subagents": 1}), \
             mock.patch.object(gui.reclaim, "scan_opencode", return_value={
                 "reclaim": 30, "redundant": (2, 400)}), \
             mock.patch.object(gui.reclaim, "scan_antigravity", return_value={
                 "reclaim": 25, "scratch": 5, "recordings": 10,
                 "compacted": 3}), \
             mock.patch.object(gui.reclaim, "dir_size", return_value=5):
            categories = gui.scan_categories()
        self.assertEqual([c["key"] for c in categories], list(gui.CATEGORY_ORDER))
        self.assertEqual(categories[0]["bytes"], 120)
        self.assertEqual(categories[3]["bytes"], 430)
        self.assertTrue(all(c["selected"] for c in categories))


class GuiApplyTests(unittest.TestCase):
    def test_unknown_category_fails_before_mutation(self):
        with self.assertRaises(ValueError):
            gui.run_cleanup(["unknown"])

    def test_codex_selection_runs_history_and_cache(self):
        entries = [{"action": "one"}]
        with mock.patch.object(gui.reclaim, "apply_codex", return_value=(10, entries)), \
             mock.patch.object(gui.reclaim, "apply_codex_cache", return_value=(5, entries)), \
             mock.patch.object(gui.reclaim, "write_manifest", return_value=Path("manifest")) as manifest, \
             redirect_stdout(io.StringIO()):
            result = gui.run_cleanup(["codex"])
        self.assertEqual(result["freed"], 15)
        self.assertTrue(result["success"])
        manifest.assert_called_once_with(entries + entries)

    def test_opencode_close_preference_is_forwarded(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "opencode.db"
            db.write_bytes(b"1234")
            paths = dict(gui.reclaim.PATHS)
            paths["opencode_db"] = db
            with mock.patch.object(gui.reclaim, "PATHS", paths), \
                 mock.patch.object(gui.reclaim, "prune_opencode_db", return_value=0) as prune, \
                 redirect_stdout(io.StringIO()):
                result = gui.run_cleanup(["opencode_db"], close_opencode=True)
        self.assertTrue(result["success"])
        prune.assert_called_once_with(backup_dir=None, no_backup=True,
                                      close_opencode=True)


if __name__ == "__main__":
    unittest.main()
