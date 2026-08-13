#!/usr/bin/env python3
"""conversation-reclaim — libera espacio de tus conversaciones de IA sin romperlas.

Analiza, respalda y reduce el almacenamiento de:
  - Claude Code   (~/.claude/projects)   recorta todo lo anterior a la última
                                          compactación (el resumen y lo reciente
                                          se conservan intactos)
  - Codex         (~/.codex/sessions)    idem con sus eventos "compacted"
  - OpenCode      (opencode.db)          poda pre-compactación + evento-log
                                          redundante (integra opencode-db-prune)
                                          + snapshots + tool-output
  - Command Code  (~/.commandcode)       solo escaneo/backup (sin marcadores)

Antes de tocar muestra las categorías destructivas. Si se indica un destino,
hace y verifica un respaldo completo (disco externo recomendado).

Uso:
    python3 reclaim.py scan                   # estima (solo lee)
    python3 reclaim.py apply --backup-dir /Volumes/DOCUMENTOS/respaldo-ia
                                             # respalda y reduce
    python3 reclaim.py apply-db               # poda opencode.db (requiere opencode cerrado)
    python3 reclaim.py restore --backup-dir /ruta   # restaura desde el respaldo

Solo requiere la librería estándar de Python 3.8+.
"""

import argparse
import json
import os
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
VERSION = "2.4.0"
MANIFEST_DIR = HOME / ".conversation-reclaim"

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
}


def _(s):
    if not EN:
        return s
    return _T.get(s, s)


# Los marcadores se validan sobre JSON estructurado. Buscar substrings en una
# línea podría confundir contenido citado con un evento real de compactación.
OPENCODE_REDUNDANT_EVENTS = ("message.updated.1", "message.part.updated.1", "session.updated.1")

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


def is_codex_compaction(record):
    return isinstance(record, dict) and record.get("type") == "compacted"


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
    total = reclaim = compacted = 0
    top = []
    if not base.exists():
        return None
    files = sorted(base.rglob("*.jsonl"))
    subagents_bytes = subagents_n = active_subagents = 0
    for f in files:
        size = f.stat().st_size
        total += size
        sub = codex_subagent_info(f)
        if sub:
            if codex_subagent_is_active(sub["thread_id"], f):
                active_subagents += 1
            else:
                subagents_bytes += size
                subagents_n += 1
            continue
        last_marker, error = find_last_marker(f, is_codex_compaction)
        if error:
            continue
        if last_marker >= 0:
            compacted += 1
            reclaim += last_marker
            top.append((last_marker, size, str(f)))
    archived = sum(p.stat().st_size for p in PATHS["codex_archived"].rglob("*")) \
        if PATHS["codex_archived"].exists() else 0
    return {"total": total, "reclaim": reclaim, "compacted": compacted,
            "archived": archived, "top": sorted(top, reverse=True),
            "subagents_bytes": subagents_bytes, "subagents_n": subagents_n,
            "active_subagents": active_subagents}


def scan_opencode():
    db = PATHS["opencode_db"]
    if not db.exists():
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cur = con.cursor()
    page_size = cur.execute("PRAGMA page_size").fetchone()[0]
    page_count = cur.execute("PRAGMA page_count").fetchone()[0]
    db_total = page_size * page_count
    ev = cur.execute("SELECT count(*), coalesce(sum(length(data)),0) FROM event").fetchone()
    red = cur.execute(
        f"SELECT count(*), coalesce(sum(length(data)),0) FROM event "
        f"WHERE type IN ({','.join('?'*3)})",
        OPENCODE_REDUNDANT_EVENTS).fetchone()

    reclaim = 0
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
            "compactions": len(comps), "reclaim": reclaim}


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
                     f" | subagentes cerrados: {human(r.get('subagents_bytes',0))} "
                     f"({r.get('subagents_n',0)}), activos: {r.get('active_subagents',0)}")
            disposable += r.get("subagents_bytes", 0)
        elif name == "Claude Code":
            extra = (f"  | subagentes: {human(r.get('subagents_bytes',0))} "
                     f"({r.get('subagents_n',0)}) + workflows "
                     f"{human(r.get('workflows_bytes',0))}")
            disposable += r.get("subagents_bytes", 0) + r.get("workflows_bytes", 0)
        if name == "OpenCode":
            extra = (f"  | {_('eventos redundantes:')} {human(r['redundant'][1])} "
                     f"({r['redundant'][0]:,} {_('filas')})")
            ncomp = r.get("compactions", 0)
            disposable += r["redundant"][1] or 0
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


def database_users(path):
    """Lista procesos que tienen abierto un archivo, o None si no es verificable."""
    if _IS_WIN:
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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
    if sys.platform != "darwin":
        print("  REFUSED: --close-opencode solo está implementado de forma segura en macOS.")
        return False
    names = ", ".join(f"{u['command']} PID {u['pid']}" for u in users)
    print(f"  AVISO: se cerrará OpenCode para liberar la DB ({names}).")
    try:
        subprocess.run(["osascript", "-e", 'tell application "OpenCode" to quit'],
                       capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        pass
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


def backup(backup_dir):
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
    base = PATHS["codex_sessions"]
    entries = []
    freed = 0
    if not base.exists():
        return 0, entries
    for f in sorted(base.rglob("*.jsonl")):
        if codex_subagent_info(f):
            continue
        if database_in_use(f) is not False:
            print(f"  {f.name[:30]}... en uso o no verificable; se omite")
            continue
        cut, size, done, marker = truncate_file_at_marker(f, is_codex_compaction, "codex")
        if done:
            freed += cut
            entries.append({"tool": "codex", "action": "truncate", "file": str(f),
                            "cut_bytes": cut, "old_size": size,
                            "marker_offset": marker, "status": "applied", "time": now()})
            print(f"  {Path(f).name[:30]}... {human(cut)} de {human(size)} {_('recortados')}")

    candidates = []
    for f in sorted(base.rglob("*.jsonl")):
        info = codex_subagent_info(f)
        if info and not codex_subagent_is_active(info["thread_id"], f):
            candidates.append((f, info, f.stat().st_size))
    if candidates:
        total = sum(item[2] for item in candidates)
        print(f"  AVISO: se eliminarán {len(candidates)} transcripts de subagentes "
              f"Codex cerrados ({human(total)}); son artefactos de un solo uso.")
    for path, info, size in candidates:
        ok, reason = delete_codex_subagent(path, info)
        if ok:
            freed += size
            entries.append(change_entry("codex", "delete_subagent", path, size,
                                        thread_id=info["thread_id"]))
        else:
            print(f"  subagente {info['thread_id'][:12]}... se omite: {reason}")
    return freed, entries


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


def prune_opencode_db(backup_dir=None, no_backup=False, close_opencode=False):
    """Poda OpenCode bajo bloqueo SQLite, una transacción y verificación."""
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

        # Antes de retirar el event log, cada sesión debe tener parts materializados.
        unsafe = cur.execute(
            "SELECT s.id FROM session s WHERE EXISTS (SELECT 1 FROM message m WHERE m.session_id=s.id) "
            "AND NOT EXISTS (SELECT 1 FROM part p JOIN message m ON m.id=p.message_id "
            "WHERE m.session_id=s.id) LIMIT 1").fetchone()
        if unsafe:
            print(f"  {_('REFUSED: el contenido solo existe en la tabla event. No se toca.')}")
            return 3

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

        red = cur.execute(
            f"SELECT count(*), coalesce(sum(length(data)),0) FROM event "
            f"WHERE type IN ({','.join('?' * len(OPENCODE_REDUNDANT_EVENTS))})",
            OPENCODE_REDUNDANT_EVENTS).fetchone()
        print(f"  {_('tamaño antes:')} {human(size_before)}")
        print(f"  {_('eventos redundantes:')} {red[0]:,} {_('filas')}, {human(red[1])}")

        cur.execute("BEGIN IMMEDIATE")
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


def apply(args):
    manifest = []
    dest = None
    if args.backup_dir:
        try:
            dest = backup(args.backup_dir)
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
                    choices=["scan", "apply", "apply-db", "restore", "skills", "gui"])
    ap.add_argument("--backup-dir", default=None,
                    help="destino del respaldo completo (opcional; disco externo recomendado)")
    ap.add_argument("--no-backup", action="store_true",
                    help="aplicar apply-db sin respaldo de la DB (asumir el riesgo)")
    ap.add_argument("--close-opencode", action="store_true",
                    help="avisar y cerrar OpenCode si bloquea apply-db (nunca cierra el host actual)")
    ap.add_argument("--no-antigravity-steps", action="store_true",
                    help="no podar los pasos pre-compactación en las DB de Antigravity")
    ap.add_argument("--only", default=None,
                    choices=["claude", "codex", "opencode", "antigravity",
                             "commandcode", "caches"],
                    help="aplicar reducciones solo a esta herramienta")
    ap.add_argument("--lang", default=None, choices=["es", "en"],
                    help="idioma de salida (por defecto: $LANG, español si no se detecta)")
    args = ap.parse_args(argv)

    if args.lang:
        globals()["EN"] = args.lang == "en"

    if args.mode == "scan":
        scan()
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
        return prune_opencode_db(args.backup_dir, args.no_backup, args.close_opencode)
    elif args.mode == "restore":
        restore(args.backup_dir or str(HOME / "respaldos-ia"))
        return 0
    elif args.mode == "gui":
        try:
            from desktop import main as gui_main
        except ModuleNotFoundError as exc:
            if exc.name != "PySide6":
                raise
            print("PySide6 no está instalado; se abrirá la interfaz clásica de respaldo.")
            from gui import main as gui_main
        return gui_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
