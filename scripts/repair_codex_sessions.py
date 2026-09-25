#!/usr/bin/env python3
"""Repair Codex rollouts whose leading session_meta was removed.

Dry run by default. --apply requires a separate backup directory and never
deletes a rollout. If a complete earlier backup ends with the current rollout,
the entire original is restored. Otherwise only session_meta is reconstructed
from Codex's local thread index; already removed history cannot be recreated.
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.digest()


def first_record(path):
    with path.open("rb") as source:
        raw = source.readline()
    return json.loads(raw) if raw else None


def validate_compacted_rollout(path):
    """Fail closed on invalid JSONL or a metadata record already in the file."""
    with path.open("rb") as source:
        for number, raw in enumerate(source, 1):
            record = json.loads(raw)
            if not isinstance(record, dict):
                raise ValueError(f"{path}: line {number} is not an object")
            if number == 1 and record.get("type") != "compacted":
                raise ValueError(f"{path}: first record is not compacted")
            if record.get("type") == "session_meta":
                raise ValueError(f"{path}: metadata already exists inside rollout")


def thread_id_from_path(path):
    import uuid

    if not path.name.startswith("rollout-") or path.suffix != ".jsonl":
        raise ValueError(f"{path}: not a Codex rollout")
    thread_id = path.stem[-36:]
    try:
        uuid.UUID(thread_id)
    except ValueError as exc:
        raise ValueError(f"{path}: invalid thread id") from exc
    return thread_id


def metadata_from_index(path, connection):
    thread_id = thread_id_from_path(path)
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT id, rollout_path, created_at_ms, created_at, cwd, source, "
        "model_provider, cli_version, history_mode, thread_source, originator "
        "FROM threads WHERE id=?", (thread_id,)
    ).fetchone()
    if row is None or Path(row["rollout_path"]).resolve() != path.resolve():
        raise ValueError(f"{path}: no matching thread index row")
    source = row["source"]
    if source and source.startswith("{"):
        source = json.loads(source)
    created_ms = row["created_at_ms"] or row["created_at"] * 1000
    timestamp = datetime.fromtimestamp(created_ms / 1000, timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    originator = row["originator"]
    if not originator:
        originator = "codex_exec" if source == "exec" or row["thread_source"] == "guardian_review" else "Codex Desktop"
    payload = {
        "session_id": thread_id,
        "id": thread_id,
        "timestamp": timestamp,
        "cwd": row["cwd"],
        "originator": originator,
        "cli_version": row["cli_version"],
        "source": source,
        "thread_source": row["thread_source"],
        "model_provider": row["model_provider"],
        "history_mode": row["history_mode"],
    }
    if any(payload[key] is None for key in ("cwd", "cli_version", "source", "thread_source", "model_provider")):
        raise ValueError(f"{path}: incomplete thread index row")
    record = {"timestamp": timestamp, "ordinal": 0, "type": "session_meta", "payload": payload}
    return (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def original_from_backup(path, root, originals_root):
    if originals_root is None:
        return None
    folder = "codex-sessions" if root.name == "sessions" else "codex-archived"
    candidate = originals_root / folder / path.relative_to(root)
    if not candidate.is_file():
        return None
    meta = first_record(candidate)
    if (not isinstance(meta, dict) or meta.get("type") != "session_meta" or
            meta.get("payload", {}).get("id") != thread_id_from_path(path)):
        return None
    original = candidate.read_bytes()
    current = path.read_bytes()
    return original if original.endswith(current) else None


def affected_rollouts(roots):
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("rollout-*.jsonl")):
            if path.is_symlink() or not path.is_file():
                continue
            first = first_record(path)
            if isinstance(first, dict) and first.get("type") == "compacted":
                yield root, path


def repair_one(root, path, connection, backup_dir, originals_root):
    validate_compacted_rollout(path)
    original = original_from_backup(path, root, originals_root)
    method = "full restore" if original is not None else "metadata reconstruction"
    header = None if original is not None else metadata_from_index(path, connection)
    old_stat = path.stat()
    old_hash = digest(path)
    destination = backup_dir / root.name / path.relative_to(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"backup already exists: {destination}")
    shutil.copy2(path, destination)
    if digest(destination) != old_hash:
        raise IOError(f"backup verification failed: {destination}")
    with destination.open("rb") as copied:
        os.fsync(copied.fileno())
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".repair-tmp", delete=False) as temp:
        temp_path = Path(temp.name)
        try:
            if original is not None:
                temp.write(original)
            else:
                temp.write(header)
                with path.open("rb") as source:
                    shutil.copyfileobj(source, temp)
            temp.flush()
            os.fsync(temp.fileno())
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
    try:
        shutil.copystat(path, temp_path)
        os.chmod(temp_path, stat.S_IMODE(old_stat.st_mode))
        current = path.stat()
        if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (
                old_stat.st_dev, old_stat.st_ino, old_stat.st_size, old_stat.st_mtime_ns) or digest(path) != old_hash:
            raise RuntimeError(f"{path}: changed during repair")
        os.replace(temp_path, path)
        try:
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            pass
        if first_record(path).get("type") != "session_meta":
            raise IOError(f"{path}: repair verification failed")
    finally:
        temp_path.unlink(missing_ok=True)
    return method


def main(argv=None):
    home = Path.home()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions-root", type=Path, default=home / ".codex/sessions")
    parser.add_argument("--archived-root", type=Path, default=home / ".codex/archived_sessions")
    parser.add_argument("--state-db", type=Path, default=home / ".codex/state_5.sqlite")
    parser.add_argument("--originals-root", type=Path, help="earlier Conversation Reclaim backup root")
    parser.add_argument("--backup-dir", type=Path, help="required destination for copies of current broken files")
    parser.add_argument("--apply", action="store_true", help="write repairs after backing up")
    args = parser.parse_args(argv)
    if args.apply and args.backup_dir is None:
        parser.error("--apply requires --backup-dir")
    roots = [args.sessions_root.resolve(), args.archived_root.resolve()]
    if args.apply:
        destination = args.backup_dir.resolve()
        if any(destination == root or root in destination.parents for root in roots):
            parser.error("--backup-dir must be outside the session directories")
    affected = list(affected_rollouts(roots))
    if not affected:
        print("No rollouts with missing session metadata found.")
        return 0
    uri = f"file:{args.state_db.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        planned = []
        for root, path in affected:
            validate_compacted_rollout(path)
            original = original_from_backup(path, root, args.originals_root)
            if original is None:
                metadata_from_index(path, connection)
            planned.append((root, path, "full restore" if original is not None else "metadata reconstruction"))
        print(f"Found {len(planned)} affected rollouts.")
        for _, path, method in planned:
            print(f"  {path}: {method}")
        if not args.apply:
            print("Dry run. Pass --apply and --backup-dir to repair.")
            return 0
        backup_dir = args.backup_dir.resolve()
        backup_dir.mkdir(parents=True, exist_ok=False)
        backup_dir.chmod(0o700)
        completed = 0
        for root, path, _ in planned:
            method = repair_one(root, path, connection, backup_dir, args.originals_root)
            completed += 1
            print(f"Repaired {completed}/{len(planned)}: {path.name} ({method})")
        print(f"Verified backups: {backup_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
