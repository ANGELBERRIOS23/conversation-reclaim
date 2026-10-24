#!/usr/bin/env python3
"""conversation-reclaim — libera espacio de tus conversaciones de IA sin romperlas.

Analiza, respalda y reduce el almacenamiento de:
  - Claude Code   (~/.claude/projects)   recorta todo lo anterior a la última
                                          compactación (el resumen y lo reciente
                                          se conservan intactos)
  - Codex         (~/.codex/cache)        solo cachés; nunca rollouts de sesión
  - OpenCode      (opencode.db)          poda pre-compactación + evento-log
                                          redundante (integra opencode-db-prune)
                                          + snapshots + tool-output. Con
                                          OpenCode 2: la copia de OpenCode 1
                                          que queda tras actualizar
                                          (apply-db --drop-v1-copy)
  - Command Code  (~/.commandcode)       solo escaneo/backup (sin marcadores)

Antes de tocar muestra las categorías destructivas. Si se indica un destino,
hace y verifica un respaldo completo (disco externo recomendado).

Uso:
    python3 reclaim.py scan                   # estima (solo lee)
    python3 reclaim.py apply --backup-dir /Volumes/DOCUMENTOS/respaldo-ia
                                             # respalda y reduce
    python3 reclaim.py apply-db               # poda opencode.db (requiere opencode cerrado)
    python3 reclaim.py restore --backup-dir /ruta   # restaura desde el respaldo
    python3 reclaim.py scan --extras          # mide también los extras
    python3 reclaim.py apply --extras --trash # imágenes en conversaciones,
                                             # work/ de Codex y versiones viejas
                                             # (sin cambios en 14+ días; --older-than)

Solo requiere la librería estándar de Python 3.8+.
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import stat
import tempfile
import time
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from pathlib import Path

HOME = Path.home()
VERSION = "3.9.1"
MANIFEST_DIR = HOME / ".conversation-reclaim"
TRANSIENT_MEDIA_MIN_AGE = 7 * 24 * 60 * 60
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# ---------------------------------------------------------------------------
# i18n: elige idioma por $RECLAIM_LANG, $LANG o --lang. Inglés o español.
# ---------------------------------------------------------------------------
_LANG = os.environ.get("RECLAIM_LANG") or os.environ.get("LANG", "es").lower()
LANG = _LANG.split("_")[0].split(".")[0]
EN = LANG == "en"

_T = {
    "conversation-reclaim — escaneo (solo lectura)": "conversation-reclaim — scan (read-only)",
    "escaneo (solo lectura)": "scan (read-only)",
    "sesión": "session",
    "(sin marcadores de compactación)": "(no compaction markers found)",
    "recuperable por compactación:": "reclaimable from compaction:",
    "compactadas": "compacted",
    "recuperable:": "reclaimable:",
    "Caches/snapshots adicionales:": "Extra caches/snapshots:",
    "Skills repetidas:": "Duplicate skills:",
    "| ya symlink:": "| already symlink:",
    "Recomendación: dejar una canonical y crear symlinks en las demás.":
        "Recommendation: keep one canonical copy and symlink the rest.",
    "Sin skills repetidas.": "No duplicate skills.",
    "Backup en:": "Backup at:",
    "TOTAL backup:": "TOTAL backup:",
    "AVISO: archivos de Claude Code en uso (¿claude corriendo?). Se omiten.":
        "WARNING: Claude Code files in use (claude running?). Skipping.",
    "subagentes eliminados:": "subagents deleted:",
    "archivos,": "files,",
    "recortados": "trimmed",
    "codex cache:": "codex cache:",
    "no existe opencode.db": "opencode.db not found",
    "REFUSED: opencode.db está abierto (opencode corriendo).":
        "REFUSED: opencode.db is open (opencode running).",
    "Cierra opencode y vuelve a ejecutar:": "Close opencode and run again:",
    "REFUSED: el contenido solo existe en la tabla event. No se toca.":
        "REFUSED: content only exists in the event table. Not touching it.",
    "tamaño antes:": "size before:",
    "eventos redundantes:": "redundant events:",
    "eventos redundantes": "redundant events",
    "filas": "rows",
    "borrados": "deleted",
    "mensajes pre-compactación podados": "pre-compaction messages pruned",
    "VACUUM (puede tardar)...": "VACUUM (may take a while)...",
    "integridad:": "integrity:",
    "liberado": "freed",
    "en uso, se omite": "in use, skipping",
    "pasos pre-compactación podados": "pre-compaction steps pruned",
    "Aplicando reducciones": "Applying reductions",
    "Reducciones aplicadas:": "Reductions applied:",
    "Nota: la poda de opencode.db requiere opencode cerrado.":
        "Note: pruning opencode.db requires opencode to be closed.",
    "Respaldo completo en:": "Full backup at:",
    "Cuando cierres opencode:": "When you close opencode:",
    "Restauración manual desde:": "Manual restore from:",
    "Copia los directorios según lo que quieras recuperar:":
        "Copy the directories you want to recover:",
    "(opencode cerrado)": "(opencode closed)",
    "Skills:": "Skills:",
    "totales,": "total,",
    "Tamaño": "Size",
    "Nombre": "Name",
    "Ubicación": "Location",
    "REPETIDAS (copias reales que ocupan espacio doble):":
        "DUPLICATES (real copies taking double space):",
    "ya symlink": "already symlink",
    "→ Sugerencia: conservar una canonical y symlink desde las otras.":
        "→ Suggestion: keep one canonical copy and symlink the others.",
    "Sin respaldo externo (usa --backup-dir <disco> para uno completo).":
        "No external backup (use --backup-dir <disk> for a full one).",
    "Se escribe un manifiesto de cambios en ~/.conversation-reclaim/.":
        "A change manifest is written to ~/.conversation-reclaim/.",
    "Manifiesto:": "Manifest:",
    "cambios registrados en": "changes recorded in",
    "REFUSED: apply-db borra datos de forma irreversible (eventos + mensajes).":
        "REFUSED: apply-db deletes data irreversibly (events + messages).",
    "Pasa --backup-dir <disco> para respaldar la DB o --no-backup si aceptas el riesgo.":
        "Pass --backup-dir <disk> to back up the DB, or --no-backup if you accept the risk.",
    "en": "across",
    "raíces,": "roots,",
    "Medios temporales opcionales:": "Optional temporary media:",
    "adjuntos antiguos": "old attachments",
    "AVISO: puede quitar vistas previas de imágenes en conversaciones antiguas.":
        "WARNING: this can remove image previews from old conversations.",
    "copia de OpenCode 1:": "OpenCode 1 copy:",
    "(apply-db --drop-v1-copy)": "(apply-db --drop-v1-copy)",
    "OpenCode 2 no ha terminado de importar las sesiones de OpenCode 1; no se toca su copia.":
        "OpenCode 2 has not finished importing OpenCode 1 sessions; their copy is left alone.",
    "--drop-v1-copy: esta base no tiene copia de OpenCode 1 junto a tablas de OpenCode 2.":
        "--drop-v1-copy: this database has no OpenCode 1 copy next to OpenCode 2 tables.",
    "copia de OpenCode 1 borrada:": "OpenCode 1 copy dropped:",
    "sesiones": "sessions",
    "se conservan (tienen piezas solo en la copia vieja):":
        "kept (they have pieces only in the old copy):",
    "Servicio de fondo de OpenCode 2 detenido; la DB quedó libre.":
        "OpenCode 2 background service stopped; the DB is free.",
}


def _(s):
    if not EN:
        return s
    return _T.get(s, s)


# Los marcadores se validan sobre JSON estructurado. Buscar substrings en una
# línea podría confundir contenido citado con un evento real de compactación.
OPENCODE_REDUNDANT_EVENTS = ("message.updated.1", "message.part.updated.1", "session.updated.1")

# OpenCode 2 importa una sola vez las sesiones de OpenCode 1 a session_v2 /
# session_message, vacía `event` y anota la fase en `kv`. Con "completed" ya no
# vuelve a leer message/part, pero tampoco las borra: la conversación queda dos
# veces en la base.
OPENCODE_V1_MIGRATION_KEY = "migration.v1-v2"


def opencode_layout(cur):
    """Qué versión de OpenCode escribió la base. OpenCode 1 ya creaba una
    tabla session_message vacía, así que la marca de la 2 es session_v2."""
    tables = {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    phase = None
    if "kv" in tables:
        row = cur.execute("SELECT value FROM kv WHERE key=?",
                          (OPENCODE_V1_MIGRATION_KEY,)).fetchone()
        if row:
            try:
                phase = json.loads(row[0]).get("phase")
            except (ValueError, AttributeError):
                phase = "unreadable"
    return {"v1": {"session", "message", "part"} <= tables,
            "v2": {"session_v2", "session_message"} <= tables,
            "event": "event" in tables,
            "migration": phase}


def _collect_strings(obj, out):
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for value in obj.values():
            _collect_strings(value, out)
    elif isinstance(obj, list):
        for value in obj:
            _collect_strings(value, out)


def opencode_v1_copy_is_redundant(cur, session):
    """¿Cada texto, razonamiento y salida de herramienta de la copia de
    OpenCode 1 de esta sesión está también en session_message?"""
    if not cur.execute("SELECT 1 FROM session_v2 WHERE id=?", (session,)).fetchone():
        return False
    strings = []
    for (data,) in cur.execute(
            "SELECT data FROM session_message WHERE session_id=?", (session,)):
        try:
            _collect_strings(json.loads(data), strings)
        except ValueError:
            continue
    if not strings:
        return False
    corpus = "\x00".join(strings)
    for (content,) in cur.execute(
            "SELECT coalesce(json_extract(data,'$.text'), json_extract(data,'$.state.output')) "
            "FROM part WHERE message_id IN (SELECT id FROM message WHERE session_id=?) "
            "AND json_extract(data,'$.type') IN ('text','reasoning','tool')", (session,)):
        if isinstance(content, str) and content.strip() and content.strip() not in corpus:
            return False
    return True

# Rutas a tocar (multiplataforma: macOS/Linux usan HOME; Windows usa USERPROFILE
# y %APPDATA% para las apps de escritorio de Antigravity)
_APPDATA = os.environ.get("APPDATA") or ""
_IS_WIN = os.name == "nt"
if _IS_WIN:
    _AG_APP = Path(_APPDATA) / "Antigravity"
    _AG_IDE_APP = Path(_APPDATA) / "Antigravity IDE"
else:
    _AG_APP = HOME / "Library" / "Application Support" / "Antigravity"
    _AG_IDE_APP = HOME / "Library" / "Application Support" / "Antigravity IDE"
if _IS_WIN:
    _CLAUDE_DESKTOP = Path(_APPDATA) / "Claude"
elif sys.platform == "darwin":
    _CLAUDE_DESKTOP = HOME / "Library" / "Application Support" / "Claude"
else:
    _CLAUDE_DESKTOP = HOME / ".config" / "Claude"

def _opencode_dir():
    cand = [HOME / ".local" / "share" / "opencode"]
    if _IS_WIN:
        la = Path(os.environ.get("LOCALAPPDATA", ""))
        aa = Path(os.environ.get("APPDATA", ""))
        cand += [la / "opencode" / "data", la / "opencode", aa / "opencode"]
    for p in cand:
        if p.exists():
            return p
    return cand[0]


def _opencode_db():
    return _opencode_dir() / "opencode.db"


PATHS = {
    "claude_projects": HOME / ".claude" / "projects",
    "codex_sessions": HOME / ".codex" / "sessions",
    "codex_archived": HOME / ".codex" / "archived_sessions",
    "codex_cache": HOME / ".codex" / "cache",
    "codex_logs": [HOME / ".codex" / "logs_1.sqlite", HOME / ".codex" / "logs_2.sqlite"],
    "codex_state": HOME / ".codex" / "state_5.sqlite",
    "codex_locks": HOME / ".codex" / "thread-writer-locks",
    "opencode_dir": _opencode_dir(),
    "opencode_db": _opencode_db(),
    "commandcode": HOME / ".commandcode" / "projects",
    "gemini": HOME / ".gemini",
    "antigravity_app": _AG_APP,
    "antigravity_ide_app": _AG_IDE_APP,
    "claude_file_history": HOME / ".claude" / "file-history",
    "claude_cli_versions": HOME / ".local" / "share" / "claude" / "versions",
    "claude_cli_link": HOME / ".local" / "bin" / "claude",
    "claude_desktop_code": _CLAUDE_DESKTOP / "claude-code",
    "codex_releases": HOME / ".codex" / "packages" / "standalone" / "releases",
    "codex_current": HOME / ".codex" / "packages" / "standalone" / "current",
    "codex_generated": HOME / ".codex" / "generated_images",
    "codex_workspace": HOME / "Documents" / "Codex",
}

# Subdirectorios de Antigravity/Gemini y su rol
GEMINI_CONV_DIRS = (
    "antigravity/conversations",
    "antigravity-cli/conversations",
    "antigravity-ide/conversations",
)
GEMINI_BRAIN_DIRS = (
    "antigravity/brain",
    "antigravity-cli/brain",
    "antigravity-ide/brain",
)
# Marcador de compactación de Antigravity: paso step_type 98 (CONVERSATION_HISTORY)
ANTIGRAVITY_COMPACT_STEP = 98


def human(n):
    if n is None:
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n} B"


def now():
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def parse_json_line(raw):
    """Parsea una línea JSONL estrictamente; devuelve None si no es válida."""
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def is_claude_compaction(record):
    if not isinstance(record, dict):
        return False
    if isinstance(record.get("compactMetadata"), dict):
        return True
    if record.get("isSummary") is True or record.get("type") == "summary":
        return True
    return (record.get("type") == "system" and
            record.get("subtype") == "compact_boundary")


def is_antigravity_compaction(record):
    return (isinstance(record, dict) and
            record.get("type") == "CONVERSATION_HISTORY")


def find_last_marker(path, predicate):
    """Devuelve (offset, error). Ante JSONL inválido falla cerrado."""
    last_marker = -1
    offset = 0
    try:
        with open(path, "rb") as fh:
            for line_no, raw in enumerate(fh, 1):
                record = parse_json_line(raw)
                if record is None:
                    return -1, f"JSON inválido en línea {line_no}"
                if predicate(record):
                    last_marker = offset
                offset += len(raw)
    except OSError as exc:
        return -1, str(exc)
    return last_marker, None


def codex_session_metadata(path):
    """Extrae metadatos propios del hijo, incluso en rollouts heredados."""
    try:
        with open(path, "rb") as fh:
            for _ in range(64):
                raw = fh.readline()
                if not raw:
                    break
                record = parse_json_line(raw)
                if record is None:
                    return None
                if (record.get("type") == "session_meta" and
                        isinstance(record.get("payload"), dict) and
                        record["payload"].get("thread_source") == "subagent"):
                    payload = record.get("payload")
                    return payload if isinstance(payload, dict) else None
    except OSError:
        return None
    return None


def codex_subagent_info(path):
    meta = codex_session_metadata(path)
    if not meta or meta.get("thread_source") != "subagent":
        return None
    thread_id = meta.get("id") or meta.get("session_id")
    source = meta.get("source")
    if (not thread_id or not isinstance(source, dict) or
            not isinstance(source.get("subagent"), dict)):
        return None
    if str(thread_id) not in Path(path).name:
        return None
    return {
        "thread_id": str(thread_id),
        "parent_thread_id": meta.get("parent_thread_id"),
        "agent_path": meta.get("agent_path"),
    }


def codex_subagent_is_active(thread_id, path=None):
    if path is not None and database_in_use(path) is not False:
        return True
    lock = PATHS["codex_locks"] / f"{thread_id}.lock"
    if lock.exists():
        return True
    state = PATHS["codex_state"]
    if state.exists():
        try:
            with closing(sqlite3.connect(f"file:{state}?mode=ro", uri=True)) as con:
                row = con.execute(
                    "SELECT status FROM thread_spawn_edges WHERE child_thread_id=?",
                    (thread_id,)).fetchone()
            return bool(row and row[0] == "open")
        except sqlite3.Error:
            return True
    return False


def claude_subagent_info(path):
    """Valida un sidechain de Claude por contenido y ubicación, no solo nombre."""
    path = Path(path)
    if path.parent.name != "subagents" or not path.name.startswith("agent-"):
        return None
    expected_agent = path.stem[len("agent-"):]
    expected_session = path.parent.parent.name
    found = False
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                record = parse_json_line(raw)
                if record is None:
                    return None
                if record.get("isSidechain") is True:
                    agent = record.get("agentId")
                    session = record.get("sessionId")
                    if agent != expected_agent or session != expected_session:
                        return None
                    found = True
    except OSError:
        return None
    return {"agent_id": expected_agent, "session_id": expected_session} if found else None


# ---------------------------------------------------------------------------
# Escaneo
# ---------------------------------------------------------------------------

def scan_claude():
    base = PATHS["claude_projects"]
    total = reclaim = compacted = 0
    top = []
    if not base.exists():
        return None
    for f in sorted(base.glob("*/*.jsonl")):
        size = f.stat().st_size
        total += size
        last_marker, error = find_last_marker(f, is_claude_compaction)
        if error:
            continue
        if last_marker >= 0:
            compacted += 1
            reclaim += last_marker
            top.append((last_marker, size, str(f)))
    # Sidechains de subagentes: artefactos de un solo uso, incluidos acompact.
    sub = sub_big = 0
    for p in base.rglob("subagents/agent-*.jsonl"):
        if not claude_subagent_info(p):
            continue
        sub += p.stat().st_size
        sub_big += 1
    workflows = sum(p.stat().st_size for p in base.rglob("subagents/workflows/*"))
    return {"total": total, "reclaim": reclaim, "compacted": compacted,
            "subagents_bytes": sub, "subagents_n": sub_big,
            "workflows_bytes": workflows,
            "top": sorted(top, reverse=True)}


def scan_codex():
    base = PATHS["codex_sessions"]
    total = 0
    if not base.exists():
        return None
    for f in base.rglob("*.jsonl"):
        total += f.stat().st_size
    archived = sum(p.stat().st_size for p in PATHS["codex_archived"].rglob("*")) \
        if PATHS["codex_archived"].exists() else 0
    return {"total": total, "reclaim": 0, "compacted": 0,
            "archived": archived, "top": [],
            "subagents_bytes": 0, "subagents_n": 0,
            "active_subagents": 0}


def transient_media_candidates(reference_time=None):
    """Adjuntos inequívocamente temporales, nunca imágenes de proyecto.

    El umbral de siete días protege la tarea activa y evita borrar una imagen
    justo después de pegarla. La categoría sigue siendo opcional porque una
    conversación antigua puede conservar solo la ruta local de la miniatura.
    """
    cutoff = (time.time() if reference_time is None else reference_time) - TRANSIENT_MEDIA_MIN_AGE
    found = []

    try:
        for path in Path(tempfile.gettempdir()).glob("codex-clipboard-*"):
            if path.suffix.lower() in IMAGE_SUFFIXES:
                found.append(path)
    except OSError:
        pass

    gemini = PATHS["gemini"]
    for brain in GEMINI_BRAIN_DIRS:
        root = gemini / brain
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            if not any(part in {".tempmediaStorage", "tempmediaStorage"}
                       for part in path.parts):
                continue
            found.append(path)

    candidates = []
    seen = set()
    for path in found:
        try:
            metadata = path.lstat()
            resolved = path.resolve()
            if resolved in seen or not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
                continue
            if metadata.st_mtime > cutoff or metadata.st_size <= 0:
                continue
            seen.add(resolved)
            candidates.append(path)
        except OSError:
            continue
    return sorted(candidates, key=str)


def scan_transient_media():
    files = transient_media_candidates()
    return {"files": files, "n": len(files),
            "total": sum(file_size(path) for path in files)}


def scan_opencode():
    db = PATHS["opencode_db"]
    if not db.exists():
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cur = con.cursor()
    page_size = cur.execute("PRAGMA page_size").fetchone()[0]
    page_count = cur.execute("PRAGMA page_count").fetchone()[0]
    db_total = page_size * page_count
    layout = opencode_layout(cur)
    ev = red = (0, 0)
    if layout["event"]:
        ev = cur.execute("SELECT count(*), coalesce(sum(length(data)),0) FROM event").fetchone()
        red = cur.execute(
            f"SELECT count(*), coalesce(sum(length(data)),0) FROM event "
            f"WHERE type IN ({','.join('?'*3)})",
            OPENCODE_REDUNDANT_EVENTS).fetchone()

    # Con OpenCode 2 ya importado, message/part son una copia completa que
    # OpenCode 2 no lee: se reporta entera (hasta cuánto se libera) y no se
    # estima además la poda pre-compactación sobre esa misma copia.
    v1_copy = (0, 0)
    imported = layout["v2"] and layout["migration"] == "completed"
    if imported and layout["v1"]:
        v1_copy = (
            cur.execute("SELECT count(DISTINCT session_id) FROM message").fetchone()[0],
            cur.execute("SELECT coalesce(sum(length(data)),0) FROM message").fetchone()[0]
            + cur.execute("SELECT coalesce(sum(length(data)),0) FROM part").fetchone()[0])

    reclaim = 0
    rows = []
    if layout["v1"] and not imported:
        rows = cur.execute(
            "SELECT session_id, id, data FROM part "
            "WHERE json_extract(data,'$.type')='compaction' ORDER BY session_id, time_created"
        ).fetchall()
    comps = defaultdict(list)
    for r in rows:
        comps[r[0]].append(json.loads(r[2]))
    for sess, clist in comps.items():
        tail = clist[-1].get("tail_start_id")
        if not tail:
            continue
        msgs = cur.execute(
            "SELECT id, length(data) FROM message WHERE session_id=? ORDER BY time_created, id",
            (sess,)).fetchall()
        ids = [m[0] for m in msgs]
        try:
            idx = ids.index(tail)
        except ValueError:
            continue
        waste = ids[:idx]
        if not waste:
            continue
        mlen = sum(m[1] for m in msgs[:idx])
        plen = 0
        for batch in chunks(waste):
            q = ",".join("?" * len(batch))
            plen += cur.execute(
                f"SELECT coalesce(sum(length(data)),0) FROM part WHERE message_id IN ({q})",
                batch).fetchone()[0]
        # No inspeccionar el JSON de toda la tabla event por cada sesión: es
        # O(sesiones × eventos) y en DB grandes tarda minutos. Los eventos de
        # streaming ya se reportan por tipo en `redundant`; aquí estimamos solo
        # message + part para evitar doble conteo y mantener `scan` interactivo.
        reclaim += mlen + plen
    con.close()
    return {"db_total": db_total, "events": ev, "redundant": red,
            "compactions": len(comps), "reclaim": reclaim,
            "layout": layout, "v1_copy": v1_copy}


def file_size(p):
    try:
        return Path(p).lstat().st_size
    except OSError:
        return 0


def dir_size(p):
    if isinstance(p, list):
        return sum(dir_size(x) for x in p)
    p = Path(p)
    if not p.exists():
        return 0
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    total = 0
    for f in p.rglob("*"):
        try:
            total += f.stat().st_size
        except OSError:
            continue
    return total


def scan_antigravity():
    """Antigravity/Gemini: conversaciones en SQLite protobuf + componentes."""
    gem = PATHS["gemini"]
    if not gem.exists():
        return None
    conv_total = wal_total = 0
    nconv = 0
    compacted = 0
    reclaim = 0
    top = []
    for sub in GEMINI_CONV_DIRS:
        d = gem / sub
        if not d.exists():
            continue
        for db in d.glob("*.db"):
            if db.name.endswith((".db-shm", ".db-wal")):
                continue
            nconv += 1
            sz = db.stat().st_size
            conv_total += sz
            w = Path(str(db) + "-wal")
            if w.exists():
                wal_total += w.stat().st_size
            try:
                with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as con:
                    marks = con.execute(
                        "SELECT idx FROM steps WHERE step_type=? "
                        "ORDER BY idx DESC LIMIT 1", (ANTIGRAVITY_COMPACT_STEP,)).fetchone()
                    if marks:
                        compacted += 1
                        before = con.execute(
                            "SELECT coalesce(sum(length(step_payload)),0) FROM steps WHERE idx<?",
                            (marks[0],)).fetchone()[0]
                        reclaim += before
                        top.append((before, sz, str(db)))
            except sqlite3.Error:
                pass
    brain = sum(dir_size(gem / b) for b in GEMINI_BRAIN_DIRS)
    scratch = sum(dir_size(gem / b) for b in
                  ("antigravity/scratch", "antigravity-cli/scratch",
                   "antigravity-ide/scratch"))
    recordings = dir_size(gem / "antigravity-ide" / "browser_recordings")
    backup = dir_size(gem / "antigravity-backup")
    return {"conv_total": conv_total, "wal_total": wal_total, "nconv": nconv,
            "compacted": compacted, "reclaim": reclaim, "brain": brain,
            "scratch": scratch, "recordings": recordings, "backup": backup,
            "top": sorted(top, reverse=True)[:6]}


SKILL_ROOTS = [
    HOME / ".claude" / "skills",
    HOME / ".config" / "opencode" / "skills",
    HOME / ".agents" / "skills",
    HOME / ".gemini" / "skills",
    HOME / ".antigravity" / "skills",
]


def scan_skills():
    """Lista skills por tamaño y detecta repetidas (mismo nombre en varios roots).

    Solo reporta: la decisión de qué hacer es del usuario. La recomendación
    para duplicados es dejar una canonical y hacer symlinks desde las demás.
    """
    found = []  # (root, nombre, tamaño, desc, es_symlink)
    for root in SKILL_ROOTS:
        if not root.exists():
            continue
        for skill_dir in sorted(root.iterdir()):
            if not skill_dir.is_dir() and not skill_dir.is_symlink():
                continue
            sk = skill_dir / "SKILL.md"
            if not sk.exists():
                continue
            size = dir_size(skill_dir) if not skill_dir.is_symlink() else 0
            is_link = skill_dir.is_symlink()
            desc = ""
            try:
                for line in open(sk):
                    line = line.strip()
                    if line.startswith("description:"):
                        desc = line[len("description:"):].strip().strip(">-").strip()[:90]
                        break
            except OSError:
                pass
            found.append((str(root), skill_dir.name, size, desc, is_link))

    by_name = defaultdict(list)
    for root, name, size, desc, is_link in found:
        by_name[name].append((root, size, is_link))

    dupes = []
    for name, items in by_name.items():
        if len(items) > 1:
            copies = [i for i in items if not i[2]]
            links = [i for i in items if i[2]]
            if len(copies) > 1 or (copies and links):
                dupes.append((name, copies, links))
    return {
        "total": sum(s for _, _, s, _, _ in found),
        "n": len(found),
        "skills": sorted(found, key=lambda x: -x[2]),
        "dupes": sorted(dupes, key=lambda x: -sum(i[1] for i in x[1])),
    }


def scan():
    print("=" * 72)
    print(f" conversation-reclaim — {_('escaneo (solo lectura)')}")
    print("=" * 72)
    total = reclaim = disposable = 0
    for name, fn in (("Claude Code", scan_claude),
                     ("Codex", scan_codex),
                     ("OpenCode", scan_opencode),
                     ("Antigravity", scan_antigravity),
                     ("Command Code", None)):
        if fn is None:
            t = dir_size(PATHS["commandcode"])
            print(f"{name:<13}{human(t):>10}  {_('(sin marcadores de compactación)')}")
            total += t
            continue
        r = fn()
        if r is None:
            continue
        rec = r.get("reclaim", 0)
        tot = r.get("total", r.get("db_total", 0))
        extra = ""
        ncomp = r.get("compacted", 0)
        if name == "Codex":
            extra = (f"  | archived_sessions: {human(r.get('archived',0))}"
                     " | sesiones y subagentes protegidos")
        elif name == "Claude Code":
            extra = (f"  | subagentes: {human(r.get('subagents_bytes',0))} "
                     f"({r.get('subagents_n',0)}) + workflows "
                     f"{human(r.get('workflows_bytes',0))}")
            disposable += r.get("subagents_bytes", 0) + r.get("workflows_bytes", 0)
        if name == "OpenCode":
            extra = (f"  | {_('eventos redundantes:')} {human(r['redundant'][1])} "
                     f"({r['redundant'][0]:,} {_('filas')})")
            v1_sessions, v1_bytes = r.get("v1_copy", (0, 0))
            if v1_bytes:
                extra += (f" | {_('copia de OpenCode 1:')} {human(v1_bytes)} "
                          f"({v1_sessions} {_('sesiones')}) {_('(apply-db --drop-v1-copy)')}")
            ncomp = r.get("compactions", 0)
            disposable += (r["redundant"][1] or 0) + v1_bytes
        elif name == "Antigravity":
            extra = (f"  | WAL: {human(r.get('wal_total',0))} | brain: "
                     f"{human(r.get('brain',0))} | recordings: "
                     f"{human(r.get('recordings',0))}")
            ncomp = r.get("compacted", 0)
            tot = r.get("conv_total", 0) + r.get("wal_total", 0)
            rec = r.get("reclaim", 0)
            disposable += r.get("scratch", 0) + r.get("recordings", 0)
        total += tot
        reclaim += rec
        print(f"{name:<13}{human(tot):>10}  {_('recuperable por compactación:')} {human(rec):>9}  "
              f"({ncomp} {_('compactadas')}){extra}")
        for b, s, fpath in r.get("top", [])[:4]:
            print(f"    {human(b):>9} de {human(s):>9}  {Path(fpath).name[:50]}")

    print(f"{'='*72}")
    print(f"{'TOTAL':<13}{human(total):>10}   compactación: {human(reclaim)}")
    print(f"  {_('Caches/snapshots adicionales:')}")
    for label, path in (("opencode snapshots", PATHS["opencode_dir"] / "snapshot"),
                        ("opencode tool-output", PATHS["opencode_dir"] / "tool-output"),
                        ("opencode logs", PATHS["opencode_dir"] / "log"),
                        ("codex cache", PATHS["codex_cache"]),
                        ("codex logs sqlite", PATHS["codex_logs"])):
        size = dir_size(path)
        disposable += size
        print(f"    {label:<22}{human(size):>10}")
    media = scan_transient_media()
    disposable += media["total"]
    print(f"  {_('Medios temporales opcionales:')} {human(media['total'])} "
          f"({media['n']} {_('adjuntos antiguos')})")
    print(f"  {_('AVISO: puede quitar vistas previas de imágenes en conversaciones antiguas.')}")
    print(f"  Desechable adicional: {human(disposable)}")
    print(f"  Recuperable estimado total: {human(reclaim + disposable)}")

    sk = scan_skills()
    print()
    print(f"  {_('Skills:')} {sk['n']} {_('en')} {len(SKILL_ROOTS)} {_('raíces,')} {human(sk['total'])}")
    if sk["dupes"]:
        print(f"  {_('Skills repetidas:')}")
        for name, copies, links in sk["dupes"]:
            detail = " + ".join(f"{human(sz)} ({Path(r).parent.name}/{Path(r).name})"
                                for r, sz, _is_link in copies)
            if links:
                detail += f" {_('| ya symlink:')} {', '.join(r for r, _, _ in links)}"
            print(f"    {name:<28}{detail}")
        print(f"    {_('Recomendación: dejar una canonical y crear symlinks en las demás.')}")
    else:
        print(f"  {_('Sin skills repetidas.')}")
    return total, reclaim + disposable


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def database_in_use(path):
    users = database_users(path)
    return None if users is None else bool(users)


def _windows_file_users(path):
    """Identifica procesos que usan path mediante Windows Restart Manager."""
    import ctypes
    from ctypes import wintypes

    class RM_UNIQUE_PROCESS(ctypes.Structure):
        _fields_ = [("dwProcessId", wintypes.DWORD),
                    ("ProcessStartTime", wintypes.FILETIME)]

    class RM_PROCESS_INFO(ctypes.Structure):
        _fields_ = [
            ("Process", RM_UNIQUE_PROCESS),
            ("strAppName", wintypes.WCHAR * 256),
            ("strServiceShortName", wintypes.WCHAR * 64),
            ("ApplicationType", wintypes.DWORD),
            ("AppStatus", wintypes.ULONG),
            ("TSSessionId", wintypes.DWORD),
            ("bRestartable", wintypes.BOOL),
        ]

    manager = ctypes.WinDLL("rstrtmgr", use_last_error=True)
    manager.RmStartSession.argtypes = [ctypes.POINTER(wintypes.DWORD),
                                       wintypes.DWORD, wintypes.LPWSTR]
    manager.RmStartSession.restype = wintypes.DWORD
    manager.RmRegisterResources.argtypes = [
        wintypes.DWORD, wintypes.UINT, ctypes.POINTER(wintypes.LPCWSTR),
        wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p]
    manager.RmRegisterResources.restype = wintypes.DWORD
    manager.RmGetList.argtypes = [
        wintypes.DWORD, ctypes.POINTER(wintypes.UINT),
        ctypes.POINTER(wintypes.UINT), ctypes.POINTER(RM_PROCESS_INFO),
        ctypes.POINTER(wintypes.DWORD)]
    manager.RmGetList.restype = wintypes.DWORD
    manager.RmEndSession.argtypes = [wintypes.DWORD]
    manager.RmEndSession.restype = wintypes.DWORD
    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(33)
    if manager.RmStartSession(ctypes.byref(session), 0, key) != 0:
        return None
    try:
        resources = (wintypes.LPCWSTR * 1)(str(Path(path).resolve()))
        result = manager.RmRegisterResources(
            session, 1, resources, 0, None, 0, None)
        if result != 0:
            return None

        needed = wintypes.UINT(0)
        count = wintypes.UINT(0)
        reboot_reasons = wintypes.DWORD(0)
        result = manager.RmGetList(
            session, ctypes.byref(needed), ctypes.byref(count), None,
            ctypes.byref(reboot_reasons))
        if result == 0 and needed.value == 0:
            return []
        if result != 234 or needed.value == 0:  # ERROR_MORE_DATA
            return None

        entries = (RM_PROCESS_INFO * needed.value)()
        count.value = needed.value
        result = manager.RmGetList(
            session, ctypes.byref(needed), ctypes.byref(count), entries,
            ctypes.byref(reboot_reasons))
        if result != 0:
            return None
        return [
            {"pid": int(entry.Process.dwProcessId),
             "command": entry.strAppName or entry.strServiceShortName or "proceso de Windows"}
            for entry in entries[:count.value]
        ]
    finally:
        manager.RmEndSession(session)


def database_users(path):
    """Lista procesos que tienen abierto un archivo, o None si no es verificable."""
    if _IS_WIN:
        try:
            users = _windows_file_users(path)
            if users is not None:
                return users
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD,
                                                          wintypes.DWORD]
            kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            kernel32.Process32FirstW.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
            kernel32.Process32FirstW.restype = wintypes.BOOL
            kernel32.Process32NextW.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
            kernel32.Process32NextW.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            create_file = kernel32.CreateFileW
            create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                    wintypes.DWORD, wintypes.LPVOID,
                                    wintypes.DWORD, wintypes.DWORD,
                                    wintypes.HANDLE]
            create_file.restype = wintypes.HANDLE
            handle = create_file(str(path), 0x80000000, 0, None, 3, 0x80, None)
            invalid = ctypes.c_void_p(-1).value
            if handle == invalid:
                error = ctypes.get_last_error()
                if error in (32, 33):  # sharing/lock violation
                    return [{"pid": -1, "command": "proceso de Windows"}]
                return None
            kernel32.CloseHandle(handle)
            return []
        except (AttributeError, OSError):
            return None
    try:
        result = subprocess.run(["lsof", "-Fpc", "--", str(path)],
                                capture_output=True, text=True)
        if result.returncode not in (0, 1):
            return None
        users = []
        current = None
        for line in result.stdout.splitlines():
            if line.startswith("p") and line[1:].isdigit():
                current = {"pid": int(line[1:]), "command": ""}
                users.append(current)
            elif line.startswith("c") and current is not None:
                current["command"] = line[1:]
        return users
    except (OSError, subprocess.SubprocessError):
        return None


def process_is_current_ancestor(candidate_pid):
    """True si cerrar candidate_pid podría matar este propio proceso."""
    if not isinstance(candidate_pid, int) or candidate_pid <= 0:
        return None
    if _IS_WIN:
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESSENTRY32W(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)
            if snapshot == ctypes.c_void_p(-1).value:
                return None
            parents = {}
            try:
                entry = PROCESSENTRY32W()
                entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
                while ok:
                    parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                    ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
            finally:
                kernel32.CloseHandle(snapshot)

            pid = os.getpid()
            for _ in range(128):
                if pid == candidate_pid:
                    return True
                if pid <= 1 or pid not in parents:
                    return False
                pid = parents[pid]
            return None
        except (AttributeError, OSError):
            return None

    pid = os.getpid()
    for _ in range(64):
        if pid == candidate_pid:
            return True
        if pid <= 1:
            return False
        try:
            result = subprocess.run(
                ["ps", "-o", "ppid=", "-p", str(pid)],
                capture_output=True, text=True)
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0 or not result.stdout.strip().isdigit():
            return None
        pid = int(result.stdout.strip())
    return None


def _request_windows_process_close(pids):
    """Solicita WM_CLOSE a las ventanas principales; nunca termina procesos."""
    import ctypes
    from ctypes import wintypes

    targets = set(pids)
    found = set()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL

    @callback_type
    def visit_window(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in targets and user32.IsWindowVisible(hwnd):
            found.add(int(pid.value))
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        return True

    if not user32.EnumWindows(visit_window, 0):
        return None
    return found


def _opencode_cli_major():
    """Versión mayor del `opencode` instalado, o None si no se sabe."""
    exe = shutil.which("opencode")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True,
                             text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+)\.\d+\.\d+", out) if isinstance(out, str) else None
    return int(match.group(1)) if match else None


def stop_opencode_service():
    """OpenCode 2 deja un servicio de fondo con la DB abierta aunque no haya
    ninguna ventana. Se detiene con su propio comando; nunca se mata."""
    if (_opencode_cli_major() or 0) < 2:
        return False
    try:
        result = subprocess.run([shutil.which("opencode"), "service", "stop"],
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def close_opencode_for_cleanup(db):
    """Cierra OpenCode solo si no es el proceso anfitrión del propio CLI."""
    users = database_users(db)
    if users is None:
        print("  REFUSED: no se pudieron identificar los procesos que usan opencode.db.")
        return False
    if not users:
        return True
    for user in users:
        relation = process_is_current_ancestor(user["pid"])
        if relation is not False:
            reason = "es la aplicación anfitriona" if relation else "no se pudo verificar su relación"
            print(f"  REFUSED: no se cerrará {user['command']} PID {user['pid']}; {reason}.")
            print("  Ejecuta apply-db desde Terminal, Codex u otro agente externo a OpenCode.")
            return False
    names = ", ".join(f"{u['command']} PID {u['pid']}" for u in users)
    print(f"  AVISO: se cerrará OpenCode para liberar la DB ({names}).")
    # Un proceso de terminal (opencode, opencode.exe) y no la app "OpenCode":
    # con OpenCode 2 es su servicio de fondo, que no tiene ventana que cerrar.
    if any(u["command"] != "OpenCode" and u["command"].lower().startswith("opencode")
           for u in users) and stop_opencode_service():
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if database_in_use(db) is False:
                print(f"  {_('Servicio de fondo de OpenCode 2 detenido; la DB quedó libre.')}")
                return True
            time.sleep(0.25)
    if _IS_WIN:
        if any("opencode" not in user["command"].lower() for user in users):
            print("  REFUSED: la DB está abierta por un proceso que no se identificó como OpenCode.")
            return False
        try:
            notified = _request_windows_process_close(user["pid"] for user in users)
        except (AttributeError, OSError):
            notified = None
        if notified is None:
            print("  REFUSED: Windows no permitió solicitar el cierre normal de OpenCode.")
            return False
        missing = [user for user in users if user["pid"] not in notified]
        if missing:
            print("  REFUSED: OpenCode no expuso una ventana que pudiera cerrarse normalmente.")
            return False
    elif sys.platform == "darwin":
        try:
            subprocess.run(["osascript", "-e", 'tell application "OpenCode" to quit'],
                           capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        print("  REFUSED: --close-opencode solo está implementado de forma segura en macOS y Windows.")
        return False
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        state = database_in_use(db)
        if state is False:
            print("  OpenCode cerrado; la DB quedó libre.")
            return True
        if state is None:
            break
        time.sleep(0.25)
    print("  REFUSED: OpenCode no cerró a tiempo; no se forzó el proceso.")
    return False


def safe_copy(src, dst):
    """Copia datos + metadatos si el destino los acepta (exFAT no soporta chflags)."""
    if Path(src).is_symlink():
        os.symlink(os.readlink(src), dst)
        return
    shutil.copyfile(src, dst)
    try:
        shutil.copystat(src, dst)
    except OSError:
        pass


def backup(backup_dir, include_media=False):
    root = Path(backup_dir).expanduser().resolve()
    sources = [Path(p).resolve() for p in
               (PATHS["claude_projects"], PATHS["codex_sessions"],
                PATHS["opencode_dir"], PATHS["gemini"])]
    if any(root == src or src in root.parents for src in sources):
        raise ValueError("el destino de respaldo no puede estar dentro de una fuente")
    dest = root / f"conversation-reclaim-{now()}"
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    print(f"{_('Backup en:')} {dest}")

    # Claude Code: copia completa de ~/.claude/projects
    if PATHS["claude_projects"].exists():
        d = dest / "claude-projects"
        shutil.copytree(PATHS["claude_projects"], d, copy_function=safe_copy,
                        symlinks=True)
        copied += dir_size(d)
        print(f"  claude-projects  -> {human(dir_size(d))}")

    # Codex: sesiones + archivadas + cache + logs
    for name, p in (("codex-sessions", PATHS["codex_sessions"]),
                    ("codex-archived", PATHS["codex_archived"]),
                    ("codex-cache", PATHS["codex_cache"])):
        if p.exists():
            shutil.copytree(p, dest / name, copy_function=safe_copy, symlinks=True)
            copied += dir_size(p)
            print(f"  {name:<18}-> {human(dir_size(p))}")
    for l in PATHS["codex_logs"]:
        if l.exists():
            out = dest / f"codex-{l.name}"
            try:
                with closing(sqlite3.connect(l)) as src_con:
                    with closing(sqlite3.connect(out)) as dst_con:
                        src_con.backup(dst_con)
            except sqlite3.Error:
                safe_copy(l, out)
            copied += out.stat().st_size
    if PATHS["codex_state"].exists():
        state_backup = dest / "codex-state.sqlite"
        with closing(sqlite3.connect(PATHS["codex_state"])) as src_con:
            with closing(sqlite3.connect(state_backup)) as dst_con:
                src_con.backup(dst_con)
        copied += state_backup.stat().st_size

    # OpenCode: DB (copia consistente), snapshot, tool-output, log
    if PATHS["opencode_db"].exists():
        db_backup = dest / "opencode.db"
        with closing(sqlite3.connect(PATHS["opencode_db"])) as src_con:
            with closing(sqlite3.connect(db_backup)) as dst_con:
                src_con.backup(dst_con)
                ok = dst_con.execute("PRAGMA integrity_check").fetchone()[0]
                if ok != "ok":
                    raise sqlite3.DatabaseError(f"backup opencode inválido: {ok}")
        copied += db_backup.stat().st_size
        print(f"  opencode.db       -> {human(db_backup.stat().st_size)}")
    for name in ("snapshot", "tool-output", "log"):
        p = PATHS["opencode_dir"] / name
        if p.exists() and dir_size(p) > 0:
            shutil.copytree(p, dest / f"opencode-{name}", copy_function=safe_copy,
                            symlinks=True)
            copied += dir_size(p)
            print(f"  opencode-{name:<14}-> {human(dir_size(p))}")

    # Command Code
    if PATHS["commandcode"].exists():
        shutil.copytree(PATHS["commandcode"], dest / "commandcode",
                        copy_function=safe_copy, symlinks=True)
        copied += dir_size(PATHS["commandcode"])
        print(f"  commandcode       -> {human(dir_size(PATHS['commandcode']))}")

    # Antigravity/Gemini: conversaciones + transcripts + logs de apps
    gem = PATHS["gemini"]
    if gem.exists():
        transcripts = 0
        for sub in GEMINI_CONV_DIRS:
            p = gem / sub
            if p.exists():
                shutil.copytree(p, dest / f"gemini-{sub.replace('/', '-')}",
                                copy_function=safe_copy, symlinks=True)
                copied += dir_size(p)
                print(f"  gemini-{sub:<24}-> {human(dir_size(p))}")
        for brain in GEMINI_BRAIN_DIRS:
            p = gem / brain
            if p.exists():
                for t in p.rglob("transcript*.jsonl"):
                    rel = t.relative_to(gem)
                    d = dest / "gemini-transcripts" / rel.parent
                    d.mkdir(parents=True, exist_ok=True)
                    safe_copy(t, d / t.name)
                    transcripts += t.stat().st_size
        print(f"  gemini-transcripts -> {human(transcripts)}")
        copied += transcripts
        for sub in ("antigravity", "antigravity-cli", "antigravity-ide"):
            for junk in ("log", "crashes", "cache", "scratch"):
                p = gem / sub / junk
                if p.exists():
                    out = dest / f"gemini-{sub}-{junk}"
                    shutil.copytree(p, out, copy_function=safe_copy, symlinks=True)
                    copied += dir_size(p)
        recordings = gem / "antigravity-ide" / "browser_recordings"
        if recordings.exists():
            shutil.copytree(recordings, dest / "gemini-browser-recordings",
                            copy_function=safe_copy, symlinks=True)
            copied += dir_size(recordings)
    for app, name in ((PATHS["antigravity_app"], "antigravity-app"),
                      (PATHS["antigravity_ide_app"], "antigravity-ide-app")):
        p = app / "logs"
        if p.exists():
            shutil.copytree(p, dest / f"{name}-logs", copy_function=safe_copy,
                            symlinks=True)
            copied += dir_size(p)
            print(f"  {name}-logs      -> {human(dir_size(p))}")

    if include_media:
        media_root = dest / "temporary-media"
        for index, source in enumerate(transient_media_candidates()):
            target = media_root / f"{index:05d}-{source.name}"
            target.parent.mkdir(parents=True, exist_ok=True)
            safe_copy(source, target)
            copied += target.stat().st_size
        if media_root.exists():
            print(f"  temporary-media   -> {human(dir_size(media_root))}")

    print(f"  {_('TOTAL backup:')} {human(copied)}")
    return dest


# ---------------------------------------------------------------------------
# Aplicación de reducciones
# ---------------------------------------------------------------------------

def truncate_file_at_marker(f, predicate, label):
    """Recorta el archivo quedándonos desde el último marcador (inclusive).

    Devuelve (bytes_recortados, tamaño_original, hecho, offset_del_marcador).
    El reemplazo es atómico (tmp + replace): si el script falla a mitad,
    el original queda intacto y solo sobra un archivo *.reclaim-tmp.
    """
    f = Path(f)
    try:
        lst = f.lstat()
    except OSError:
        return 0, 0, False, -1
    if stat.S_ISLNK(lst.st_mode) or not stat.S_ISREG(lst.st_mode):
        print(f"  {f.name}: se omite porque no es un archivo regular")
        return 0, lst.st_size, False, -1
    size = lst.st_size
    last_marker, error = find_last_marker(f, predicate)
    if error:
        print(f"  {f.name}: se omite ({error})")
        return 0, size, False, -1
    if last_marker <= 0:
        return 0, size, False, -1
    kept = size - last_marker
    fd, tmp_name = tempfile.mkstemp(
        dir=str(f.parent), prefix=f".{f.name}.", suffix=".reclaim-tmp")
    tmp = Path(tmp_name)
    try:
        with open(f, "rb") as fh, os.fdopen(fd, "wb") as out:
            opened = os.fstat(fh.fileno())
            fh.seek(last_marker)
            shutil.copyfileobj(fh, out)
            out.flush()
            os.fsync(out.fileno())
            os.fchmod(out.fileno(), stat.S_IMODE(opened.st_mode))
        shutil.copystat(f, tmp, follow_symlinks=False)
        current = f.stat()
        signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        if signature(opened) != signature(current):
            raise RuntimeError(f"{f} cambió durante el recorte; no se reemplazó")
        os.replace(str(tmp), str(f))
        try:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            dir_fd = os.open(str(f.parent), flags)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        if tmp.exists():
            tmp.unlink()
    return size - kept, size, True, last_marker


def write_manifest(entries):
    """Registro de cada cambio aplicado (fallback: saber qué se tocó y cuándo)."""
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        MANIFEST_DIR.chmod(0o700)
    except OSError:
        pass
    m = MANIFEST_DIR / f"manifest-{now()}.jsonl"
    fd = os.open(str(m), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    print(f"  {_('Manifiesto:')} {m}")
    return m


def change_entry(tool, action, path, size, **extra):
    entry = {"tool": tool, "action": action, "file": str(path),
             "cut_bytes": size, "old_size": size, "marker_offset": -1,
             "status": "applied", "time": now()}
    entry.update(extra)
    return entry


def remove_path(path):
    """Elimina un target exacto sin seguir symlinks; devuelve bytes retirados."""
    path = Path(path)
    size = dir_size(path)
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    return size


def apply_claude():
    base = PATHS["claude_projects"]
    entries = []
    if not base.exists():
        return 0, entries
    if database_in_use(base):
        print(f"  {_('AVISO: archivos de Claude Code en uso (¿claude corriendo?). Se omiten.')}")
        return 0, entries
    freed = 0
    for f in sorted(base.glob("*/*.jsonl")):
        if database_in_use(f) is not False:
            print(f"  {f.name[:30]}... en uso o no verificable; se omite")
            continue
        cut, size, done, marker = truncate_file_at_marker(f, is_claude_compaction, "claude")
        if done:
            freed += cut
            entries.append({"tool": "claude", "action": "truncate", "file": str(f),
                            "cut_bytes": cut, "old_size": size,
                            "marker_offset": marker, "status": "applied", "time": now()})
            print(f"  {Path(f).name[:14]}... {human(cut)} de {human(size)} {_('recortados')}")

    # Sidechains de un solo uso; memory/ y el transcript principal no se tocan.
    sub_n = sub_bytes = 0
    subagents = [p for p in base.rglob("subagents/agent-*.jsonl")
                 if claude_subagent_info(p) and database_in_use(p) is False]
    if subagents:
        preview_bytes = sum(p.stat().st_size for p in subagents)
        print(f"  AVISO: se eliminarán {len(subagents)} transcripts de subagentes "
              f"Claude cerrados ({human(preview_bytes)}); son artefactos de un solo uso.")
    for p in subagents:
        size = p.stat().st_size
        sub_bytes += size
        sub_n += 1
        p.unlink()
        entries.append({"tool": "claude", "action": "delete_subagent",
                        "file": str(p), "cut_bytes": size, "old_size": size,
                        "marker_offset": -1, "time": now()})
        meta = p.with_name(p.name.replace(".jsonl", "") + ".meta.json")
        if meta.exists():
            meta_size = meta.stat().st_size
            meta.unlink()
            sub_bytes += meta_size
            sub_n += 1
            entries.append({"tool": "claude", "action": "delete_subagent_meta",
                            "file": str(meta), "cut_bytes": meta_size,
                            "old_size": meta_size, "marker_offset": -1,
                            "time": now()})
    for meta in base.rglob("subagents/agent-*.meta.json"):
        sibling = meta.with_name(meta.name.replace(".meta.json", ".jsonl"))
        if sibling.exists() or database_in_use(meta) is not False:
            continue
        sub_bytes += meta.stat().st_size
        sub_n += 1
        size = meta.stat().st_size
        meta.unlink()
        entries.append({"tool": "claude", "action": "delete_subagent_meta",
                        "file": str(meta), "cut_bytes": size, "old_size": size,
                        "marker_offset": -1, "time": now()})
    for wf in base.rglob("subagents/workflows"):
        if database_in_use(wf) is not False:
            print(f"  {wf}: en uso o no verificable; se omite")
            continue
        size = dir_size(wf)
        sub_bytes += size
        shutil.rmtree(wf)
        entries.append({"tool": "claude", "action": "delete_workflows",
                        "file": str(wf), "cut_bytes": size, "old_size": size,
                        "marker_offset": -1, "time": now()})
    if sub_n:
        print(f"  {_('subagentes eliminados:')} {sub_n} {_('archivos,')} {human(sub_bytes)}")
    freed += sub_bytes
    return freed, entries


def apply_codex():
    # Codex requires its initial session_meta record, and its UI reads the full
    # rollout even after a compacted event. No session or subagent is disposable.
    return 0, []


def delete_codex_subagent(path, info):
    """Elimina un subagente cerrado y mantiene coherente el índice de Codex."""
    thread_id = info["thread_id"]
    if codex_subagent_is_active(thread_id, path):
        return False, "sigue activo"
    state = PATHS["codex_state"]
    if not state.exists():
        return False, "no existe el índice state_5.sqlite"
    con = None
    try:
        con = sqlite3.connect(state, timeout=0)
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT status FROM thread_spawn_edges WHERE child_thread_id=?",
            (thread_id,)).fetchone()
        open_children = con.execute(
            "SELECT 1 FROM thread_spawn_edges WHERE parent_thread_id=? AND status='open' LIMIT 1",
            (thread_id,)).fetchone()
        if (row and row[0] == "open") or open_children:
            con.rollback()
            return False, "él o uno de sus hijos sigue activo"
        con.execute("DELETE FROM thread_dynamic_tools WHERE thread_id=?", (thread_id,))
        con.execute("DELETE FROM thread_spawn_edges WHERE child_thread_id=? OR parent_thread_id=?",
                    (thread_id, thread_id))
        con.execute("DELETE FROM threads WHERE id=? AND thread_source='subagent'", (thread_id,))
        con.commit()
    except sqlite3.Error as exc:
        if con is not None:
            con.rollback()
        return False, f"índice ocupado o incompatible ({exc})"
    finally:
        if con is not None:
            con.close()

    try:
        Path(path).unlink()
    except OSError as exc:
        return False, f"metadatos retirados, pero no se pudo borrar el rollout ({exc})"

    for log_db in PATHS["codex_logs"]:
        if not log_db.exists():
            continue
        try:
            with closing(sqlite3.connect(log_db, timeout=0)) as log_con:
                table = log_con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='logs'").fetchone()
                cols = {r[1] for r in log_con.execute("PRAGMA table_info(logs)")} if table else set()
                if "thread_id" in cols:
                    log_con.execute("DELETE FROM logs WHERE thread_id=?", (thread_id,))
                    log_con.commit()
        except sqlite3.Error:
            pass
    return True, None


def apply_opencode_files():
    """Todos los snapshots + tool-output + logs."""
    freed = 0
    entries = []
    snap = PATHS["opencode_dir"] / "snapshot"
    if snap.exists():
        print("  AVISO: se eliminarán todos los snapshots locales de OpenCode.")
        for child in snap.iterdir():
            sz = remove_path(child)
            freed += sz
            entries.append(change_entry("opencode", "delete_snapshot", child, sz))
        print(f"  snapshots eliminados: {human(freed)}")
    tool = PATHS["opencode_dir"] / "tool-output"
    if tool.exists():
        for child in tool.iterdir():
            sz = remove_path(child)
            freed += sz
            entries.append(change_entry("opencode", "delete_tool_output", child, sz))
        print(f"  tool-output eliminado")
    log = PATHS["opencode_dir"] / "log"
    if log.exists():
        sz = remove_path(log)
        freed += sz
        entries.append(change_entry("opencode", "delete_logs", log, sz))
        print(f"  logs: {human(sz)}")
    return freed, entries


def apply_codex_cache():
    freed = 0
    entries = []
    if PATHS["codex_cache"].exists():
        sz = remove_path(PATHS["codex_cache"])
        freed += sz
        entries.append(change_entry("codex", "delete_cache", PATHS["codex_cache"], sz))
        print(f"  {_('codex cache:')} {human(sz)}")
    for l in PATHS["codex_logs"]:
        if l.exists():
            if database_in_use(l) is not False:
                print(f"  {l.name}: en uso o no verificable; se omite")
                continue
            sz = l.stat().st_size
            l.unlink()
            freed += sz
            entries.append(change_entry("codex", "delete_log_db", l, sz))
            print(f"  {l.name}: {human(sz)}")
    return freed, entries


def chunks(values, size=400):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def prune_opencode_db(backup_dir=None, no_backup=False, close_opencode=False,
                      drop_v1_copy=False):
    """Poda OpenCode bajo bloqueo SQLite, una transacción y verificación.

    Con OpenCode 2 ya importado, drop_v1_copy borra la copia de OpenCode 1
    (message/part) de cada sesión cuyo contenido completo está en
    session_message; las demás se dejan como están."""
    db = PATHS["opencode_db"]
    if not db.exists():
        print(f"  {_('no existe opencode.db')}")
        return 1
    if not backup_dir and not no_backup:
        print(f"  {_('REFUSED: apply-db borra datos de forma irreversible (eventos + mensajes).')}")
        print(f"  {_('Pasa --backup-dir <disco> para respaldar la DB o --no-backup si aceptas el riesgo.')}")
        return 7
    if not _IS_WIN:
        in_use = database_in_use(db)
        if in_use and close_opencode:
            if not close_opencode_for_cleanup(db):
                return 2
            in_use = database_in_use(db)
        if in_use is not False:
            detail = "está abierto" if in_use else "no se pudo comprobar su uso"
            print(f"  REFUSED: opencode.db {detail}.")
            if in_use:
                print("  Reintenta con --close-opencode para avisar y cerrar OpenCode de forma normal.")
                print("  OpenCode 2 deja un servicio de fondo con la DB abierta: `opencode service stop`.")
            return 2
    else:
        print("  Windows: asegúrate de que opencode esté completamente cerrado.")

    backup_path = None
    if backup_dir:
        try:
            dest = Path(backup_dir).expanduser().resolve() / f"conversation-reclaim-{now()}"
            dest.mkdir(parents=True, exist_ok=False)
            backup_path = dest / "opencode.db"
            with closing(sqlite3.connect(db)) as src_con:
                with closing(sqlite3.connect(backup_path)) as dst_con:
                    src_con.backup(dst_con)
                    if dst_con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise sqlite3.DatabaseError("integridad del respaldo inválida")
            print(f"  {_('Backup en:')} {backup_path} ({human(backup_path.stat().st_size)})")
        except (OSError, sqlite3.Error) as exc:
            print(f"  REFUSED: no se pudo crear/verificar el respaldo: {exc}")
            return 4
    else:
        print("  --no-backup: no hay copia de la DB; se escribirá un manifiesto.")

    size_before = db.stat().st_size
    con = sqlite3.connect(db, timeout=0)
    con.execute("PRAGMA foreign_keys=ON")
    cur = con.cursor()
    try:
        if cur.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            print("  REFUSED: la DB ya falla integrity_check.")
            return 6

        layout = opencode_layout(cur)
        imported = layout["v2"] and layout["migration"] == "completed"

        # Antes de retirar el event log, cada sesión debe tener parts materializados.
        unsafe = layout["v1"] and cur.execute(
            "SELECT s.id FROM session s WHERE EXISTS (SELECT 1 FROM message m WHERE m.session_id=s.id) "
            "AND NOT EXISTS (SELECT 1 FROM part p JOIN message m ON m.id=p.message_id "
            "WHERE m.session_id=s.id) LIMIT 1").fetchone()
        if unsafe:
            print(f"  {_('REFUSED: el contenido solo existe en la tabla event. No se toca.')}")
            return 3

        # Con OpenCode 2 ya importado, message/part es una copia que OpenCode 2
        # no lee. No se poda por compactación: en las sesiones que no pasan la
        # verificación, lo anterior a la compactación puede ser lo único que
        # falta en la copia nueva.
        rows = []
        if layout["v1"] and not imported:
            rows = cur.execute(
                "SELECT session_id, id, data FROM part "
                "WHERE json_extract(data,'$.type')='compaction' "
                "ORDER BY session_id, time_created, id").fetchall()
        latest = {}
        for sid, _part_id, data in rows:
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                latest[sid] = parsed

        plans = []
        for sess, compaction in latest.items():
            tail = compaction.get("tail_start_id")
            ids = [r[0] for r in cur.execute(
                "SELECT id FROM message WHERE session_id=? ORDER BY time_created, id",
                (sess,)).fetchall()]
            if not tail or tail not in ids:
                print(f"  sesión {str(sess)[:14]}...: marcador inválido; se omite")
                continue
            idx = ids.index(tail)
            if idx and cur.execute(
                    "SELECT 1 FROM part WHERE message_id IN "
                    "(SELECT id FROM message WHERE session_id=?) LIMIT 1", (sess,)).fetchone():
                plans.append((sess, ids[:idx]))

        red = (0, 0)
        if layout["event"]:
            red = cur.execute(
                f"SELECT count(*), coalesce(sum(length(data)),0) FROM event "
                f"WHERE type IN ({','.join('?' * len(OPENCODE_REDUNDANT_EVENTS))})",
                OPENCODE_REDUNDANT_EVENTS).fetchone()
        print(f"  {_('tamaño antes:')} {human(size_before)}")
        print(f"  {_('eventos redundantes:')} {red[0]:,} {_('filas')}, {human(red[1])}")

        cur.execute("BEGIN IMMEDIATE")
        if layout["event"]:
            cur.execute(f"DELETE FROM event WHERE type IN "
                        f"({','.join('?' * len(OPENCODE_REDUNDANT_EVENTS))})",
                        OPENCODE_REDUNDANT_EVENTS)
        pruned_messages = 0
        for sess, waste in plans:
            pids = []
            for batch in chunks(waste):
                q = ",".join("?" * len(batch))
                pids.extend(r[0] for r in cur.execute(
                    f"SELECT id FROM part WHERE message_id IN ({q})", batch))
            for batch in chunks(pids):
                q = ",".join("?" * len(batch))
                cur.execute(f"DELETE FROM event WHERE json_extract(data,'$.part.id') IN ({q})", batch)
            for batch in chunks(waste):
                q = ",".join("?" * len(batch))
                cur.execute(f"DELETE FROM part WHERE message_id IN ({q})", batch)
                cur.execute(f"DELETE FROM message WHERE id IN ({q})", batch)
            pruned_messages += len(waste)
            print(f"  sesión {str(sess)[:14]}...: {len(waste)} mensajes pre-compactación podados")

        dropped, kept = [], []
        if drop_v1_copy and imported and layout["v1"]:
            for (sess,) in cur.execute(
                    "SELECT DISTINCT session_id FROM message").fetchall():
                if opencode_v1_copy_is_redundant(cur, sess):
                    cur.execute("DELETE FROM part WHERE message_id IN "
                                "(SELECT id FROM message WHERE session_id=?)", (sess,))
                    cur.execute("DELETE FROM message WHERE session_id=?", (sess,))
                    dropped.append(sess)
                else:
                    kept.append(sess)
            print(f"  {_('copia de OpenCode 1 borrada:')} {len(dropped)} {_('sesiones')}")
            if kept:
                print(f"  {len(kept)} {_('se conservan (tienen piezas solo en la copia vieja):')}")
                for sess in kept[:10]:
                    print(f"    {sess}")
        elif drop_v1_copy and layout["v2"] and layout["v1"] and not imported:
            print(f"  {_('OpenCode 2 no ha terminado de importar las sesiones de OpenCode 1; no se toca su copia.')}")
        elif drop_v1_copy:
            print(f"  {_('--drop-v1-copy: esta base no tiene copia de OpenCode 1 junto a tablas de OpenCode 2.')}")
        con.commit()
        print(f"  {_('VACUUM (puede tardar)...')}")
        cur.execute("VACUUM")
        ok = cur.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.Error as exc:
        con.rollback()
        print(f"  REFUSED/ROLLBACK: {exc}")
        return 2 if "locked" in str(exc).lower() else 5
    finally:
        con.close()

    size_after = db.stat().st_size
    entry = change_entry("opencode", "prune_database", db,
                         max(0, size_before - size_after),
                         redundant_events=red[0], messages=pruned_messages,
                         v1_copy_sessions=len(dropped),
                         backup_path=str(backup_path) if backup_path else None,
                         integrity=ok)
    write_manifest([entry])
    print(f"  {_('integridad:')} {ok}")
    print(f"  {_('tamaño antes:')} {human(size_before)} -> {human(size_after)} "
          f"({_('liberado')} {human(size_before - size_after)})")
    return 0 if ok == "ok" else 6


def apply_antigravity(steps=True):
    """Antigravity: recorta transcripts en el último marcador de compactación,
    poda los pasos pre-compactación de las DB de conversación (si steps=True)
    y limpia logs/crashes/cache. El respaldo externo es opcional."""
    gem = PATHS["gemini"]
    freed = 0
    entries = []

    # 1) Transcripts: quedarse desde el último CONVERSATION_HISTORY
    for brain in GEMINI_BRAIN_DIRS:
        bdir = gem / brain
        if not bdir.exists():
            continue
        for t in bdir.rglob("transcript*.jsonl"):
            if not t.is_file():
                continue
            if database_in_use(t) is not False:
                print(f"  {t.name}: en uso o no verificable; se omite")
                continue
            cut, size, done, marker = truncate_file_at_marker(
                t, is_antigravity_compaction, "antigravity")
            if done:
                freed += cut
                entries.append({"tool": "antigravity", "action": "truncate_transcript",
                                "file": str(t), "cut_bytes": cut,
                                "old_size": size, "marker_offset": marker,
                                "status": "applied", "time": now()})
                print(f"  transcript {Path(t).parent.parent.name[:8]}... "
                      f"{human(cut)} {_('recortados')}")

    # 2) DBs de conversación: podar pasos anteriores a la última compactación
    if steps:
        for sub in GEMINI_CONV_DIRS:
            d = gem / sub
            if not d.exists():
                continue
            for db in d.glob("*.db"):
                if db.name.endswith((".db-shm", "-wal")):
                    continue
                if database_in_use(db) is not False:
                    print(f"  {db.name[:12]}... {_('en uso, se omite')}")
                    continue
                con = None
                try:
                    con = sqlite3.connect(db, timeout=0)
                    con.execute("BEGIN IMMEDIATE")
                    mark = con.execute(
                        "SELECT idx FROM steps WHERE step_type=? ORDER BY idx DESC LIMIT 1",
                        (ANTIGRAVITY_COMPACT_STEP,)).fetchone()
                    if not mark or mark[0] == 0:
                        con.rollback()
                        continue
                    idx = mark[0]
                    before = con.execute(
                        "SELECT coalesce(sum(length(step_payload)),0) FROM steps WHERE idx<?",
                        (idx,)).fetchone()[0]
                    if before == 0:
                        con.rollback()
                        continue
                    for table in ("steps", "gen_metadata", "executor_metadata",
                                  "parent_references", "battle_mode_infos"):
                        columns = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
                        if "idx" in columns:
                            con.execute(f"DELETE FROM {table} WHERE idx<?", (idx,))
                    con.commit()
                    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    con.execute("VACUUM")
                    ok = con.execute("PRAGMA integrity_check").fetchone()[0]
                    entries.append(change_entry(
                        "antigravity", "prune_steps", db, before,
                        marker_offset=idx, integrity=ok,
                        status="applied" if ok == "ok" else "integrity_failed"))
                    if ok != "ok":
                        print(f"  {db.name[:12]}... integridad: {ok}")
                        continue
                    freed += before
                    print(f"  {sub}/{db.name[:12]}... {human(before)} "
                          f"({idx} {_('pasos pre-compactación podados')})")
                except sqlite3.Error as e:
                    if con is not None:
                        con.rollback()
                    print(f"  {db.name[:12]}... error: {e}")
                finally:
                    if con is not None:
                        con.close()

    # 3) Logs, crashes y caches de los componentes .gemini
    for sub in ("antigravity", "antigravity-cli", "antigravity-ide"):
        for junk in ("log", "crashes", "cache", "scratch"):
            p = gem / sub / junk
            if p.exists():
                sz = dir_size(p)
                remove_path(p)
                freed += sz
                entries.append(change_entry("antigravity", f"delete_{junk}", p, sz))
                print(f"  .gemini/{sub}/{junk}: {human(sz)}")

    # 4) browser_recordings (imágenes del modo browser, meses de antigüedad)
    rec = gem / "antigravity-ide" / "browser_recordings"
    if rec.exists():
        sz = dir_size(rec)
        files = sum(1 for p in rec.rglob("*") if p.is_file())
        print(f"  AVISO: se eliminarán {files} browser recordings ({human(sz)}). "
              "Son capturas ya usadas por Antigravity y no se reutilizan.")
        remove_path(rec)
        freed += sz
        entries.append(change_entry("antigravity", "delete_browser_recordings", rec, sz,
                                    file_count=files))
        print(f"  browser_recordings: {human(sz)}")

    # 4) Logs de las apps de escritorio
    for app, name in ((PATHS["antigravity_app"], "Antigravity"),
                      (PATHS["antigravity_ide_app"], "Antigravity IDE")):
        p = app / "logs"
        if p.exists():
            sz = dir_size(p)
            remove_path(p)
            freed += sz
            entries.append(change_entry("antigravity", "delete_app_logs", p, sz))
            print(f"  {name} (logs): {human(sz)}")
    return freed, entries


def apply_transient_media():
    freed = 0
    entries = []
    for path in transient_media_candidates():
        state = database_in_use(path)
        if state is not False:
            continue
        size = path.stat().st_size
        removed = remove_path(path)
        freed += removed
        entries.append(change_entry("media", "delete_temporary_attachment", path,
                                    size, reason="older than 7 days; optional preview cache"))
    print(f"  medios temporales: {human(freed)}")
    return freed, entries


# ---------------------------------------------------------------------------
# Extras (v3.8): imágenes dentro de conversaciones, archivos de trabajo de
# Codex y versiones viejas. Solo tocan lo que no cambió en N días.
# ---------------------------------------------------------------------------

DEFAULT_MIN_AGE_DAYS = 14
EXTRA_KINDS = ("images", "codex-work", "old-versions")
IMAGE_PLACEHOLDER = "[imagen quitada por conversation-reclaim]"
# PNG de 1x1: ocupa el lugar de una imagen en las copias que solo usa la
# interfaz, para no cambiar la forma del registro.
TINY_PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
                "YAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
TINY_PNG_URL = "data:image/png;base64," + TINY_PNG_B64
MIN_IMAGE_CHARS = 1024
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
WORK_MEDIA_SUFFIXES = IMAGE_SUFFIXES | VIDEO_SUFFIXES | {".heic", ".tif", ".tiff", ".bmp"}
DUPLICATE_SEARCH_SKIP = {"node_modules", ".git", ".venv", "venv", "Library", ".Trash"}
GENERATED_IMAGE_SUFFIXES = IMAGE_SUFFIXES | {".heic"}


def is_generated_image(path):
    """Una imagen de verdad: ni .DS_Store ni otros ocultos que deja Finder."""
    path = Path(path)
    return not path.name.startswith(".") and path.suffix.lower() in GENERATED_IMAGE_SUFFIXES


def _cutoff(min_age_days, reference_time=None):
    return (time.time() if reference_time is None else reference_time) - min_age_days * 86400


def _regular_file(path):
    try:
        return stat.S_ISREG(Path(path).lstat().st_mode)
    except OSError:
        return False


def _claude_image_block_size(block):
    """Tamaño del base64 si `block` es un bloque de imagen que ve el modelo."""
    if block.get("type") != "image":
        return 0
    source = block.get("source")
    if not isinstance(source, dict) or source.get("type") != "base64":
        return 0
    data = source.get("data")
    return len(data) if isinstance(data, str) and len(data) > MIN_IMAGE_CHARS else 0


def strip_claude_images(obj):
    """Quita las imágenes en base64 de un registro de Claude Code (en sitio).

    Los bloques que ve el modelo pasan a ser un texto corto, así la
    conversación se puede retomar. Las copias que solo usa la interfaz
    (toolUseResult, adjuntos) quedan como un PNG de 1x1 para no cambiar su
    forma. Los PDF y las firmas de razonamiento no se tocan.
    Devuelve los caracteres retirados.
    """
    removed = 0
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            size = _claude_image_block_size(item) if isinstance(item, dict) else 0
            if size:
                removed += size
                obj[i] = {"type": "text", "text": IMAGE_PLACEHOLDER}
            else:
                removed += strip_claude_images(item)
    elif isinstance(obj, dict):
        for key, value in obj.items():
            if (key == "source" and isinstance(value, dict)
                    and value.get("type") == "base64"
                    and str(value.get("media_type", "")).startswith("image/")
                    and isinstance(value.get("data"), str)
                    and len(value["data"]) > MIN_IMAGE_CHARS):
                removed += len(value["data"]) - len(TINY_PNG_B64)
                value["data"] = TINY_PNG_B64
                value["media_type"] = "image/png"
            elif (key == "base64" and isinstance(value, str)
                  and len(value) > MIN_IMAGE_CHARS
                  and str(obj.get("type", "")).startswith("image/")):
                removed += len(value) - len(TINY_PNG_B64)
                obj[key] = TINY_PNG_B64
                obj["type"] = "image/png"
            else:
                removed += strip_claude_images(value)
    return removed


def _codex_image_item_size(item):
    if item.get("type") != "input_image":
        return 0
    url = item.get("image_url")
    if isinstance(url, str) and url.startswith("data:image") and len(url) > MIN_IMAGE_CHARS:
        return len(url)
    return 0


def strip_codex_images(obj):
    """Quita las imágenes en base64 de un registro de Codex (en sitio).

    Las imágenes que ve el modelo pasan a ser `input_text`; cualquier otra
    `data:image` (capturas de la interfaz) queda como un PNG de 1x1. No toca
    session_meta ni el número de orden de cada línea.
    """
    removed = 0
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            size = _codex_image_item_size(item) if isinstance(item, dict) else 0
            if size:
                removed += size
                obj[i] = {"type": "input_text", "text": IMAGE_PLACEHOLDER}
            else:
                removed += strip_codex_images(item)
    elif isinstance(obj, dict):
        for key, value in obj.items():
            if (isinstance(value, str) and value.startswith("data:image")
                    and len(value) > MIN_IMAGE_CHARS):
                removed += len(value) - len(TINY_PNG_URL)
                obj[key] = TINY_PNG_URL
            else:
                removed += strip_codex_images(value)
    return removed


IMAGE_STRIPPERS = {
    "claude": (b"base64", strip_claude_images),
    "codex": (b"data:image", strip_codex_images),
}


def _zstd():
    found = shutil.which("zstd")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/zstd", "/usr/local/bin/zstd", "/usr/bin/zstd"):
        if Path(candidate).exists():
            return candidate
    return None


def _read_session(path, compressed=None):
    """Contenido de una sesión .jsonl o .jsonl.zst (None si no se puede leer)."""
    path = Path(path)
    if compressed is None:
        compressed = path.name.endswith(".zst")
    if compressed:
        zstd = _zstd()
        if not zstd:
            return None
        result = subprocess.run([zstd, "-dcq", "--", str(path)], capture_output=True)
        return result.stdout if result.returncode == 0 else None
    return path.read_bytes()


def strip_session_images(data, tool):
    """Devuelve (contenido_nuevo, caracteres_retirados) o (None, error).

    Solo vuelve a escribir las líneas que tienen imágenes; las demás quedan
    byte por byte iguales. Si una línea con imagen no es JSON válido, no se
    toca nada.
    """
    needle, stripper = IMAGE_STRIPPERS[tool]
    lines = data.split(b"\n")
    removed = 0
    for i, raw in enumerate(lines):
        if needle not in raw:
            continue
        body = raw[:-1] if raw.endswith(b"\r") else raw
        record = parse_json_line(body)
        if record is None:
            return None, f"JSON inválido en línea {i + 1}"
        cut = stripper(record)
        if cut:
            removed += cut
            lines[i] = json.dumps(record, ensure_ascii=False,
                                  separators=(",", ":")).encode("utf-8")
    if not removed:
        return data, 0
    return b"\n".join(lines), removed


def rewrite_session_without_images(path, tool, trash=None):
    """Reescribe una sesión sin imágenes de forma atómica.

    Devuelve (bytes_liberados_en_disco, hecho, error). Con `trash`, el
    original se guarda ahí antes de reemplazarlo.
    """
    path = Path(path)
    try:
        before = path.lstat()
    except OSError as exc:
        return 0, False, str(exc)
    if not stat.S_ISREG(before.st_mode):
        return 0, False, "no es un archivo regular"
    data = _read_session(path)
    if data is None:
        return 0, False, "no se pudo leer (¿falta zstd?)"
    new, removed = strip_session_images(data, tool)
    if new is None:
        return 0, False, removed
    if not removed:
        return 0, False, None
    compressed = path.name.endswith(".zst")
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.",
                                    suffix=".reclaim-tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            if compressed:
                result = subprocess.run([_zstd(), "-q", "-c", "-"], input=new,
                                        capture_output=True)
                if result.returncode != 0:
                    raise RuntimeError("zstd no pudo comprimir")
                out.write(result.stdout)
            else:
                out.write(new)
            out.flush()
            os.fsync(out.fileno())
        if _read_session(tmp, compressed) != new:
            raise RuntimeError("la copia nueva no coincide")
        shutil.copystat(path, tmp, follow_symlinks=False)
        current = path.lstat()
        signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        if signature(before) != signature(current):
            raise RuntimeError("cambió mientras se limpiaba")
        if trash is not None:
            keep = _trash_destination(trash, path)
            shutil.copy2(path, keep)
        after = tmp.stat().st_size
        os.replace(str(tmp), str(path))
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return 0, False, str(exc)
    finally:
        if tmp.exists():
            tmp.unlink()
    return before.st_size - after, True, None


def conversation_image_candidates(min_age_days, reference_time=None):
    """Sesiones de Claude Code y Codex sin cambios en N días.

    Cowork no entra: cada línea lleva una firma (_audit_hmac) que dejaría de
    cuadrar.
    """
    cutoff = _cutoff(min_age_days, reference_time)
    found = []
    roots = [("claude", PATHS["claude_projects"], ("*.jsonl",)),
             ("codex", PATHS["codex_sessions"], ("*.jsonl", "*.jsonl.zst")),
             ("codex", PATHS["codex_archived"], ("*.jsonl", "*.jsonl.zst"))]
    for tool, root, patterns in roots:
        if not root.exists():
            continue
        for pattern in patterns:
            for path in sorted(root.rglob(pattern)):
                try:
                    st = path.lstat()
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode) and st.st_mtime < cutoff:
                    found.append((tool, path))
    return found


def estimate_session_images(path, tool):
    data = _read_session(path)
    if data is None:
        return 0
    _new, removed = strip_session_images(data, tool)
    return removed if isinstance(removed, int) else 0


def _inside_git_tree(directory, stop):
    directory = Path(directory)
    stop = Path(stop)
    while True:
        if (directory / ".git").exists():
            return True
        if directory == stop or directory.parent == directory:
            return False
        directory = directory.parent


def codex_work_candidates(min_age_days, reference_time=None):
    """Imágenes y videos que Codex dejó en `work/` de tareas viejas.

    Nunca toca `outputs/` (lo entregado) ni archivos dentro de un repositorio
    git. Si la tarea no tiene nada en `outputs/`, lo entregado puede estar en
    `work/`, así que esa tarea no se toca.
    Estructura: ~/Documents/Codex/<fecha>/<tarea>/work/...
    """
    cutoff = _cutoff(min_age_days, reference_time)
    root = PATHS["codex_workspace"]
    found = []
    if not root.is_dir():
        return found
    for work in sorted(root.glob("*/*/work")):
        if not work.is_dir() or work.is_symlink():
            continue
        outputs = work.parent / "outputs"
        if not outputs.is_dir() or not any(p.is_file() for p in outputs.rglob("*")):
            continue
        for current, dirs, files in os.walk(work):
            if ".git" in dirs or ".git" in files:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d != "node_modules"]
            for name in files:
                path = Path(current) / name
                if path.suffix.lower() not in WORK_MEDIA_SUFFIXES:
                    continue
                try:
                    st = path.lstat()
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode) and st.st_mtime < cutoff and st.st_size > 0:
                    found.append(path)
    return found


def _is_codex_work_dir(directory, workspace):
    try:
        parts = Path(directory).relative_to(workspace).parts
    except ValueError:
        return False
    return len(parts) >= 3 and parts[2] == "work"


def _sha256(path):
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def codex_generated_duplicates(min_age_days, search_roots=None, reference_time=None):
    """Imágenes generadas por Codex que ya tienen una copia idéntica guardada.

    Devuelve [(imagen_generada, copia)]. Las que no tienen copia se quedan.
    """
    cutoff = _cutoff(min_age_days, reference_time)
    generated = PATHS["codex_generated"]
    by_size = defaultdict(list)
    if generated.is_dir():
        for path in generated.rglob("*"):
            try:
                st = path.lstat()
            except OSError:
                continue
            if is_generated_image(path) and stat.S_ISREG(st.st_mode) and st.st_mtime < cutoff and st.st_size > 0:
                by_size[st.st_size].append(path)
    if not by_size:
        return []
    if search_roots is None:
        # Todas las carpetas visibles del usuario (proyectos incluidos), sin
        # seguir enlaces: así no se bajan archivos de la nube por accidente.
        search_roots = user_search_roots()
    hashes = {}
    found = {}
    workspace = PATHS["codex_workspace"]
    for root in search_roots:
        if not Path(root).is_dir():
            continue
        for current, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in DUPLICATE_SEARCH_SKIP]
            # Una copia dentro de work/ de Codex no cuenta: también se limpia.
            if _is_codex_work_dir(Path(current), workspace):
                dirs[:] = []
                continue
            for name in files:
                if name.startswith("."):
                    continue
                copy = Path(current) / name
                try:
                    st = copy.lstat()
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode) or st.st_size not in by_size:
                    continue
                pending = [g for g in by_size[st.st_size] if g not in found]
                if not pending:
                    continue
                try:
                    copy_hash = _sha256(copy)
                    for original in pending:
                        if original not in hashes:
                            hashes[original] = _sha256(original)
                        if hashes[original] == copy_hash:
                            found[original] = copy
                except OSError:
                    continue
    return sorted(found.items())


def _version_key(name):
    return tuple(int(part) for part in re.findall(r"\d+", name))


def _running_commands():
    if _IS_WIN:
        return []
    try:
        result = subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True)
        return result.stdout.splitlines() if result.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        return []


def _keep_newest(paths, also_keep=()):
    """Todas menos la más nueva y las de `also_keep` (comparadas por ruta real)."""
    ordered = sorted(paths, key=lambda p: _version_key(p.name))
    keep = {Path(p).resolve() for p in also_keep}
    if ordered:
        keep.add(ordered[-1].resolve())
    return [p for p in ordered if p.resolve() not in keep]


def old_version_candidates(min_age_days, reference_time=None):
    """Versiones viejas de Claude Code y Codex, y restos que ya no sirven.

    Se queda siempre la versión en uso y la más nueva. Los puntos de
    restauración de Claude (file-history) y los respaldos de reparación de
    Codex solo se van si no cambiaron en N días.
    Devuelve [(etiqueta, ruta)].
    """
    cutoff = _cutoff(min_age_days, reference_time)
    running = _running_commands()
    in_use = lambda p: any(cmd.startswith(str(p)) for cmd in running)
    found = []

    versions = PATHS["claude_cli_versions"]
    if versions.is_dir():
        current = set()
        link = PATHS["claude_cli_link"]
        if link.exists():
            current.add(link.resolve())
        entries = [p for p in versions.iterdir() if not p.is_symlink()]
        for path in _keep_newest(entries, current):
            if not in_use(path) and database_in_use(path) is False:
                found.append(("Claude Code (terminal) versión vieja", path))

    desktop = PATHS["claude_desktop_code"]
    if desktop.is_dir():
        entries = [p for p in desktop.iterdir() if p.is_dir() and not p.is_symlink()
                   and _version_key(p.name)]
        for path in _keep_newest(entries):
            if not in_use(path):
                found.append(("Claude Code (app de escritorio) versión vieja", path))

    releases = PATHS["codex_releases"]
    if releases.is_dir():
        current = set()
        if PATHS["codex_current"].exists():
            current.add(PATHS["codex_current"].resolve())
        entries = [p for p in releases.iterdir() if p.is_dir() and not p.is_symlink()]
        for path in _keep_newest(entries, current):
            if not in_use(path):
                found.append(("Codex versión vieja", path))

    history = PATHS["claude_file_history"]
    if history.is_dir():
        for session in history.iterdir():
            if not session.is_dir() or session.is_symlink():
                continue
            newest = max((f.stat().st_mtime for f in session.rglob("*") if f.is_file()),
                         default=session.stat().st_mtime)
            if newest < cutoff:
                found.append(("Claude Code punto de restauración viejo", session))

    for base in (PATHS["codex_sessions"], PATHS["codex_archived"]):
        if base.is_dir():
            for path in base.rglob("*.bak-*"):
                if _regular_file(path) and path.stat().st_mtime < cutoff:
                    found.append(("Codex respaldo de reparación", path))
    return found


def trash_root():
    """Carpeta de la Papelera para esta limpieza (None si el sistema no tiene)."""
    if sys.platform == "darwin":
        base = HOME / ".Trash"
    elif not _IS_WIN:
        base = HOME / ".local" / "share" / "Trash" / "files"
    else:
        return None
    return base / f"conversation-reclaim {datetime.now().strftime('%Y-%m-%d %H.%M.%S')}"


def _trash_destination(trash, path):
    try:
        relative = Path(path).relative_to(HOME)
    except ValueError:
        relative = Path(*Path(path).parts[1:])
    destination = Path(trash) / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


def discard(path, trash=None):
    """Borra un archivo o carpeta, o lo mueve a la Papelera. Devuelve bytes."""
    path = Path(path)
    size = dir_size(path)
    if trash is None:
        remove_path(path)
    else:
        shutil.move(str(path), str(_trash_destination(trash, path)))
    return size


def scan_extras(min_age_days):
    images = defaultdict(lambda: [0, 0])
    for tool, path in conversation_image_candidates(min_age_days):
        cut = estimate_session_images(path, tool)
        if cut:
            images[tool][0] += 1
            images[tool][1] += cut
    work = codex_work_candidates(min_age_days)
    generated = codex_generated_duplicates(min_age_days)
    versions = old_version_candidates(min_age_days)
    return {
        "images": dict(images),
        "work": (len(work), sum(file_size(p) for p in work)),
        "generated": (len(generated), sum(file_size(g) for g, _copy in generated)),
        "versions": [(label, path, dir_size(path)) for label, path in versions],
    }


def print_extras(min_age_days):
    print()
    print(f"  Extras (sin cambios en {min_age_days}+ días; apply --extras o --only <tipo>):")
    r = scan_extras(min_age_days)
    for tool, label in (("claude", "Claude Code"), ("codex", "Codex")):
        n, cut = r["images"].get(tool, (0, 0))
        note = " (sin comprimir)" if tool == "codex" else ""
        print(f"    imágenes en conversaciones de {label:<12}{human(cut):>10}{note}  ({n} sesiones)")
    n, size = r["work"]
    print(f"    imágenes/videos en work/ de Codex        {human(size):>10}  ({n} archivos)")
    n, size = r["generated"]
    print(f"    imágenes generadas ya copiadas en otro lado {human(size):>7}  ({n} archivos)")
    total = sum(size for _label, _path, size in r["versions"])
    print(f"    versiones viejas y restos                {human(total):>10}  ({len(r['versions'])})")
    for label, path, size in r["versions"]:
        if size > 5 * 1024 * 1024:
            print(f"        {human(size):>9}  {label}: {path.name}")
    print("    Cowork no se toca: sus líneas llevan firma y dejarían de cuadrar.")
    return r


def open_files():
    """Rutas abiertas por cualquier proceso, con una sola llamada a lsof.

    Devuelve None si no se puede saber (Windows o lsof falló); entonces se
    pregunta archivo por archivo.
    """
    if _IS_WIN:
        return None
    try:
        result = subprocess.run(["lsof", "-w", "-Fn"], capture_output=True, text=True,
                                errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode not in (0, 1):
        return None
    return {line[1:] for line in result.stdout.splitlines() if line.startswith("n/")}


def _in_use(path, opened):
    if opened is None:
        return database_in_use(path) is not False
    return str(path) in opened


def apply_extras(kinds, min_age_days, trash=None):
    """Aplica las categorías extra elegidas. Devuelve (bytes, entradas)."""
    freed = 0
    entries = []
    opened = open_files()
    if "images" in kinds:
        done = 0
        for tool, path in conversation_image_candidates(min_age_days):
            if _in_use(path, opened):
                continue
            cut, ok, error = rewrite_session_without_images(path, tool, trash)
            if error:
                print(f"  {path.name}: se omite ({error})")
            if ok:
                freed += cut
                done += 1
                entries.append(change_entry(tool, "strip_images", path, cut,
                                            reason=f"imágenes de sesión sin cambios en {min_age_days}+ días"))
        print(f"  imágenes en conversaciones: {human(freed)} ({done} sesiones)")
    if "codex-work" in kinds:
        part = 0
        for path in codex_work_candidates(min_age_days):
            if _in_use(path, opened):
                continue
            size = discard(path, trash)
            part += size
            entries.append(change_entry("codex", "delete_work_media", path, size,
                                        reason=f"work/ sin cambios en {min_age_days}+ días"))
        for original, copy in codex_generated_duplicates(min_age_days):
            size = discard(original, trash)
            part += size
            entries.append(change_entry("codex", "delete_generated_duplicate", original, size,
                                        copy=str(copy)))
        freed += part
        print(f"  archivos de trabajo de Codex: {human(part)}")
    if "old-versions" in kinds:
        part = 0
        for label, path in old_version_candidates(min_age_days):
            size = discard(path, trash)
            part += size
            entries.append(change_entry("tools", "delete_old_version", path, size, reason=label))
        freed += part
        print(f"  versiones viejas y restos: {human(part)}")
    return freed, entries


# ---------------------------------------------------------------------------
# Outputs de Codex: lista del más pesado al más liviano. No borra nada solo;
# cada persona elige qué mandar a la Papelera.
# ---------------------------------------------------------------------------

OUTPUTS_MIN_BYTES = 1024 * 1024
# Solo una marca explícita de versión: 'video_v2' y 'video-v3' son familia.
# Números sueltos o '(2)' no cuentan: suelen ser series o archivos distintos.
_VERSION_TAIL = re.compile(r"[ _.-]*v(\d+)$", re.I)


def _version_family(path):
    """(carpeta, nombre sin la versión, extensión), o None si no dice versión."""
    if not _VERSION_TAIL.search(path.stem):
        return None
    return (path.parent, _VERSION_TAIL.sub("", path.stem).lower(), path.suffix.lower())


def find_identical_copies(originals, search_roots, skip_dir=None):
    """{original: copia} para los archivos que tienen una copia idéntica.

    Solo compara archivos del mismo tamaño y no sigue enlaces (así no baja
    archivos de la nube). `skip_dir(ruta)` permite saltar carpetas.
    """
    by_size = defaultdict(list)
    for path in originals:
        try:
            by_size[Path(path).lstat().st_size].append(Path(path))
        except OSError:
            continue
    originals_set = {p for group in by_size.values() for p in group}
    hashes = {}
    found = {}
    for root in search_roots:
        if not Path(root).is_dir():
            continue
        for current, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in DUPLICATE_SEARCH_SKIP]
            if skip_dir is not None and skip_dir(Path(current)):
                dirs[:] = []
                continue
            for name in files:
                if name.startswith("."):
                    continue
                copy = Path(current) / name
                if copy in originals_set:
                    continue
                try:
                    st = copy.lstat()
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode) or st.st_size not in by_size:
                    continue
                pending = [o for o in by_size[st.st_size] if o not in found]
                if not pending:
                    continue
                try:
                    copy_hash = _sha256(copy)
                    for original in pending:
                        if original not in hashes:
                            hashes[original] = _sha256(original)
                        if hashes[original] == copy_hash:
                            found[original] = copy
                except OSError:
                    continue
    return found


def user_search_roots(extra=()):
    roots = [p for p in HOME.iterdir()
             if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")
             and p.name not in DUPLICATE_SEARCH_SKIP | {"Applications"}]
    return roots + [Path(p) for p in extra]


def codex_outputs(min_bytes=None, find_copies=True, extra_roots=()):
    """Archivos de outputs/ de Codex, del más pesado al más liviano.

    Cada uno dice si ya hay una copia idéntica fuera de ~/Documents/Codex y
    si en la misma carpeta hay una versión más nueva del mismo archivo.
    """
    if min_bytes is None:
        min_bytes = OUTPUTS_MIN_BYTES
    workspace = PATHS["codex_workspace"]
    items = []
    for outputs in sorted(workspace.glob("*/*/outputs")):
        if not outputs.is_dir() or outputs.is_symlink():
            continue
        for current, dirs, files in os.walk(outputs):
            for name in files:
                path = Path(current) / name
                try:
                    st = path.lstat()
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode) and st.st_size >= min_bytes and not name.startswith("."):
                    items.append({"path": path, "size": st.st_size, "mtime": st.st_mtime,
                                  "task": "/".join(path.relative_to(workspace).parts[:2]),
                                  "copy": None, "newer": None})
    families = defaultdict(list)
    for item in items:
        family = _version_family(item["path"])
        if family is not None:
            families[family].append(item)
    for group in families.values():
        if len(group) > 1:
            newest = max(group, key=lambda i: _version_key(_VERSION_TAIL.search(i["path"].stem).group(1)))
            for item in group:
                if item is not newest:
                    item["newer"] = newest["path"]
    if find_copies and items:
        copies = find_identical_copies(
            [i["path"] for i in items], user_search_roots(extra_roots),
            skip_dir=lambda d: d == workspace or workspace in d.parents)
        for item in items:
            item["copy"] = copies.get(item["path"])
    items.sort(key=lambda i: (-i["size"], str(i["path"])))
    return items


def print_outputs(items, limit):
    workspace = PATHS["codex_workspace"]
    total = sum(i["size"] for i in items)
    print(f"Outputs de Codex de {human(OUTPUTS_MIN_BYTES)} o más: {len(items)} archivos, {human(total)}")
    with_copy = [i for i in items if i["copy"]]
    older = [i for i in items if i["newer"] and not i["copy"]]
    print(f"  con copia idéntica en otra carpeta: {len(with_copy)} ({human(sum(i['size'] for i in with_copy))})")
    print(f"  con una versión más nueva al lado: {len(older)} ({human(sum(i['size'] for i in older))})")
    print("  Nada se borra solo. Para mandar a la Papelera: reclaim.py outputs --to-trash <número> ...")
    print()
    for number, item in enumerate(items[:limit], 1):
        note = ""
        if item["copy"]:
            note = f"  [copia en {str(item['copy']).replace(str(HOME), '~')}]"
        elif item["newer"]:
            note = f"  [hay versión más nueva: {item['newer'].name}]"
        date = datetime.fromtimestamp(item["mtime"]).strftime("%Y-%m-%d")
        print(f"{number:>4}. {human(item['size']):>9}  {date}  "
              f"{item['path'].relative_to(workspace)}{note}")
    if len(items) > limit:
        print(f"  … y {len(items) - limit} más (usa --limit)")


def _codex_chat_titles():
    """{id de conversación: título} desde el índice de Codex (solo lectura)."""
    state = PATHS["codex_state"]
    if not state.exists():
        return {}
    try:
        uri = "file:" + str(state) + "?mode=ro&immutable=1"
        with closing(sqlite3.connect(uri, uri=True)) as con:
            rows = con.execute("SELECT id, COALESCE(NULLIF(name, ''), title) FROM threads").fetchall()
        return {thread: (title or "").strip() for thread, title in rows}
    except sqlite3.Error:
        return {}


def codex_generated_list(find_copies=True):
    """Imágenes generadas por Codex: primero las recomendadas (ya hay una
    copia idéntica fuera de work/), luego el resto, de mayor a menor."""
    generated = PATHS["codex_generated"]
    workspace = PATHS["codex_workspace"]
    titles = _codex_chat_titles()
    items = []
    if generated.is_dir():
        for path in generated.rglob("*"):
            try:
                st = path.lstat()
            except OSError:
                continue
            if is_generated_image(path) and stat.S_ISREG(st.st_mode) and st.st_size > 0:
                chat = path.parent.name
                items.append({"path": path, "size": st.st_size, "mtime": st.st_mtime,
                              "task": titles.get(chat) or chat, "copy": None, "newer": None})
    if find_copies and items:
        copies = find_identical_copies([i["path"] for i in items], user_search_roots(),
                                       skip_dir=lambda d: _is_codex_work_dir(d, workspace))
        for item in items:
            item["copy"] = copies.get(item["path"])
    items.sort(key=lambda i: (i["copy"] is None, -i["size"], str(i["path"])))
    return items


def print_generated(items, limit):
    generated = PATHS["codex_generated"]
    recommended = [i for i in items if i["copy"]]
    print(f"Imágenes generadas por Codex: {len(items)}, {human(sum(i['size'] for i in items))}")
    print(f"  recomendadas (ya tienes una copia idéntica): {len(recommended)} "
          f"({human(sum(i['size'] for i in recommended))})")
    print("  Nada se borra solo. Para mandar a la Papelera: reclaim.py generated --to-trash <número> ...")
    print()
    for number, item in enumerate(items[:limit], 1):
        mark = (f"  RECOMENDADA, copia en {str(item['copy']).replace(str(HOME), '~')}"
                if item["copy"] else "")
        date = datetime.fromtimestamp(item["mtime"]).strftime("%Y-%m-%d")
        print(f"{number:>4}. {human(item['size']):>9}  {date}  {item['task'][:48]:<48}  "
              f"{item['path'].relative_to(generated)}{mark}")
    if len(items) > limit:
        print(f"  … y {len(items) - limit} más (usa --limit)")


def trash_outputs(items, choices, root=None, action="trash_output"):
    """Manda a la Papelera los elegidos por número o por ruta (solo dentro de `root`)."""
    trash = trash_root()
    if trash is None:
        print("  REFUSED: este sistema no tiene Papelera conocida.")
        return 4
    workspace = PATHS["codex_workspace"] if root is None else root
    chosen = []
    for choice in choices:
        if choice.isdigit():
            index = int(choice) - 1
            if not 0 <= index < len(items):
                print(f"  REFUSED: el número {choice} no está en la lista.")
                return 2
            chosen.append(items[index]["path"])
        else:
            path = Path(choice).expanduser().resolve()
            if not any(path == i["path"].resolve() for i in items):
                print(f"  REFUSED: {choice} no está en la lista.")
                return 2
            chosen.append(path)
    trash.mkdir(parents=True, exist_ok=True)
    entries = []
    freed = 0
    for path in dict.fromkeys(chosen):
        if not str(path.resolve()).startswith(str(workspace.resolve())):
            continue
        size = discard(path, trash)
        freed += size
        entries.append(change_entry("codex", action, path, size, trash=str(trash)))
        print(f"  a la Papelera: {path.relative_to(workspace)} ({human(size)})")
    if entries:
        write_manifest(entries)
    print(f"  Total: {human(freed)} en {trash}")
    return 0


def open_listed(items, choice, reveal=False):
    """Abre (o muestra en su carpeta) un archivo de la lista por número o ruta."""
    if choice.isdigit() and 1 <= int(choice) <= len(items):
        path = items[int(choice) - 1]["path"]
    else:
        wanted = Path(choice).expanduser().resolve()
        match = [i["path"] for i in items if i["path"].resolve() == wanted]
        if not match:
            print(f"  REFUSED: {choice} no está en la lista.")
            return 2
        path = match[0]
    if sys.platform == "darwin":
        command = ["open", "-R", str(path)] if reveal else ["open", str(path)]
    elif _IS_WIN:
        command = ["explorer", f"/select,{path}"] if reveal else ["explorer", str(path)]
    else:
        command = ["xdg-open", str(path.parent if reveal else path)]
    subprocess.run(command)
    print(f"  {'mostrando' if reveal else 'abriendo'}: {path}")
    return 0


def apply(args):
    manifest = []
    dest = None
    if args.backup_dir:
        try:
            include_media = getattr(args, "include_media", False) or args.only == "media"
            dest = backup(args.backup_dir, include_media=include_media)
        except (OSError, ValueError, sqlite3.Error) as exc:
            print(f"  REFUSED: el respaldo falló; no se aplicó ningún cambio: {exc}")
            return 4
    else:
        print()
        print(f"  {_('Sin respaldo externo (usa --backup-dir <disco> para uno completo).')}")
        print(f"  {_('Se escribe un manifiesto de cambios en ~/.conversation-reclaim/.')}")
    print()
    print("=" * 72)
    print(f" {_('Aplicando reducciones')}")
    print("=" * 72)
    freed = 0
    only = args.only
    extras = set()
    if only in EXTRA_KINDS:
        extras.add(only)
    elif only is None and getattr(args, "extras", False):
        extras.update(EXTRA_KINDS)
    if extras:
        trash = None
        if getattr(args, "trash", False):
            trash = trash_root()
            if trash is None:
                print("  REFUSED: --trash no está disponible en este sistema.")
                return 4
            trash.mkdir(parents=True, exist_ok=True)
            print(f"  Lo que se quite va a la Papelera: {trash}")
        f, e = apply_extras(extras, getattr(args, "older_than", DEFAULT_MIN_AGE_DAYS), trash)
        freed += f
        manifest += e
    if only in (None, "claude"):
        f, e = apply_claude()
        freed += f
        manifest += e
    if only in (None, "codex"):
        f, e = apply_codex()
        freed += f
        manifest += e
    if only in (None, "opencode"):
        f, e = apply_opencode_files()
        freed += f
        manifest += e
    if only in (None, "codex"):
        f, e = apply_codex_cache()
        freed += f
        manifest += e
    if only in (None, "antigravity"):
        f, e = apply_antigravity(steps=not args.no_antigravity_steps)
        freed += f
        manifest += e
    if only == "media" or (only is None and getattr(args, "include_media", False)):
        f, e = apply_transient_media()
        freed += f
        manifest += e
    if only == "caches":
        for fn in (apply_opencode_files, apply_codex_cache):
            f, e = fn()
            freed += f
            manifest += e
        f, e = apply_antigravity(steps=False)
        freed += f
        manifest += e
    if manifest:
        m = write_manifest(manifest)
        print(f"  ({len(manifest)} {_('cambios registrados en')} {m})")
    print(f"\n  {_('Reducciones aplicadas:')} {human(freed)}")
    print(f"  {_('Nota: la poda de opencode.db requiere opencode cerrado.')}")
    print(f"        {_('Cuando cierres opencode:')}  python3 reclaim.py apply-db --backup-dir <disco>")
    if dest:
        print(f"        {_('Respaldo completo en:')} {dest}")
    return 0


def restore(backup_dir):
    print(f"{_('Restauración manual desde:')} {backup_dir}")
    print(f"{_('Copia los directorios según lo que quieras recuperar:')}")
    print("  claude-projects/  -> ~/.claude/projects")
    print("  codex-sessions/   -> ~/.codex/sessions")
    print("  codex-archived/   -> ~/.codex/archived_sessions")
    print("  codex-cache/      -> ~/.codex/cache")
    print("  codex-state.sqlite -> ~/.codex/state_5.sqlite (Codex cerrado)")
    print(f"  opencode.db       -> ~/.local/share/opencode/opencode.db {_('(opencode cerrado)')}")
    print("  opencode-snapshot/-> ~/.local/share/opencode/snapshot")
    print("  opencode-tool-output/ -> ~/.local/share/opencode/tool-output")
    print("  commandcode/      -> ~/.commandcode/projects")
    print("  gemini-*/         -> subrutas correspondientes bajo ~/.gemini")
    print("  gemini-browser-recordings/ -> ~/.gemini/antigravity-ide/browser_recordings")


def main(argv=None):
    ap = argparse.ArgumentParser(description="conversation-reclaim v" + VERSION)
    ap.add_argument("mode", nargs="?", default="scan",
                    choices=["scan", "apply", "apply-db", "restore", "skills", "outputs", "generated"])
    ap.add_argument("--limit", type=int, default=40,
                    help="outputs/generated: cuántos archivos mostrar (por defecto 40)")
    ap.add_argument("--external", action="append", default=[], metavar="CARPETA",
                    help="outputs: buscar copias también en esta carpeta (por ejemplo un disco externo)")
    ap.add_argument("--no-copies", action="store_true",
                    help="outputs/generated: no buscar copias (más rápido)")
    ap.add_argument("--open", default=None, metavar="N",
                    help="outputs/generated: abrir el número N de la lista con su app")
    ap.add_argument("--reveal", default=None, metavar="N",
                    help="outputs/generated: mostrar el número N en su carpeta (Finder/Explorador)")
    ap.add_argument("--to-trash", nargs="+", dest="trash_items", default=[], metavar="N",
                    help="outputs/generated: mandar a la Papelera estos números de la lista (o rutas)")
    ap.add_argument("--backup-dir", default=None,
                    help="destino del respaldo completo (opcional; disco externo recomendado)")
    ap.add_argument("--no-backup", action="store_true",
                    help="aplicar apply-db sin respaldo de la DB (asumir el riesgo)")
    ap.add_argument("--close-opencode", action="store_true",
                    help="avisar y cerrar OpenCode si bloquea apply-db (nunca cierra el host actual)")
    ap.add_argument("--drop-v1-copy", action="store_true",
                    help="OpenCode 2: en apply-db, borrar la copia de OpenCode 1 de las "
                         "sesiones que ya están completas en OpenCode 2")
    ap.add_argument("--no-antigravity-steps", action="store_true",
                    help="no podar los pasos pre-compactación en las DB de Antigravity")
    ap.add_argument("--only", default=None,
                    choices=["claude", "codex", "opencode", "antigravity",
                             "commandcode", "caches", "media", *EXTRA_KINDS],
                    help="aplicar reducciones solo a esta herramienta o extra")
    ap.add_argument("--include-media", action="store_true",
                    help="incluir adjuntos temporales de más de 7 días (puede quitar vistas previas antiguas)")
    ap.add_argument("--extras", action="store_true",
                    help="incluir imágenes en conversaciones, archivos de trabajo de Codex "
                         "y versiones viejas (en scan, también los mide)")
    ap.add_argument("--older-than", type=int, default=DEFAULT_MIN_AGE_DAYS, metavar="DÍAS",
                    help=f"extras: solo lo que no cambió en DÍAS (por defecto {DEFAULT_MIN_AGE_DAYS})")
    ap.add_argument("--trash", action="store_true",
                    help="extras: mandar a la Papelera en vez de borrar (y guardar ahí "
                         "el original de cada conversación que se limpia)")
    ap.add_argument("--lang", default=None, choices=["es", "en"],
                    help="idioma de salida (por defecto: $LANG, español si no se detecta)")
    args = ap.parse_args(argv)

    if args.lang:
        globals()["EN"] = args.lang == "en"

    if args.older_than < 1:
        ap.error("--older-than debe ser 1 o más")
    if args.mode == "scan":
        scan()
        if args.extras:
            print_extras(args.older_than)
        return 0
    elif args.mode == "outputs":
        items = codex_outputs(find_copies=not (args.no_copies or args.open or args.reveal),
                              extra_roots=args.external)
        if args.open or args.reveal:
            return open_listed(items, args.open or args.reveal, reveal=bool(args.reveal))
        if args.trash_items:
            return trash_outputs(items, args.trash_items)
        print_outputs(items, args.limit)
        return 0
    elif args.mode == "generated":
        items = codex_generated_list(find_copies=not (args.no_copies or args.open or args.reveal))
        if args.open or args.reveal:
            return open_listed(items, args.open or args.reveal, reveal=bool(args.reveal))
        if args.trash_items:
            return trash_outputs(items, args.trash_items, root=PATHS["codex_generated"],
                                 action="trash_generated_image")
        print_generated(items, args.limit)
        return 0
    elif args.mode == "skills":
        sk = scan_skills()
        print(f"{_('Skills:')} {sk['n']} {_('totales,')} {human(sk['total'])}")
        print(f"{_('Tamaño'):>10}  {_('Nombre'):<32}  {_('Ubicación')}")
        print("-" * 78)
        for root, name, size, desc, is_link in sk["skills"]:
            marca = " (symlink)" if is_link else ""
            print(f"{human(size):>10}  {name:<32}  {root}{marca}")
        if sk["dupes"]:
            print()
            print(f"{_('REPETIDAS (copias reales que ocupan espacio doble):')}")
            for name, copies, links in sk["dupes"]:
                print(f"  {name}:")
                for r, sz, _x in copies:
                    print(f"    {human(sz):>9}  {r}")
                if links:
                    print(f"    {_('ya symlink'):>9}  {', '.join(r for r, _x, _y in links)}")
            print(f"  {_('→ Sugerencia: conservar una canonical y symlink desde las otras.')}")
        return 0
    elif args.mode == "apply":
        return apply(args)
    elif args.mode == "apply-db":
        return prune_opencode_db(args.backup_dir, args.no_backup, args.close_opencode,
                                 args.drop_v1_copy)
    elif args.mode == "restore":
        restore(args.backup_dir or str(HOME / "respaldos-ia"))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
