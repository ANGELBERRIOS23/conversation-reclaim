#!/usr/bin/env python3
"""conversation-reclaim — libera espacio de tus conversaciones de IA sin romperlas.

Analiza, respalda y reduce el almacenamiento de:
  - Claude Code   (~/.claude/projects)   recorta todo lo anterior a la última
                                          compactación (el resumen y lo reciente
                                          se conservan intactos)
  - Codex         (~/.codex/sessions)    idem con sus eventos "compacted"
  - OpenCode      (opencode.db)          poda pre-compactación + evento-log
                                          redundante (integra opencode-db-prune)
                                          + snapshots huérfanos + tool-output
  - Command Code  (~/.commandcode)       solo escaneo/backup (sin marcadores)

Antes de tocar NADA hace un respaldo completo en el destino indicado
(disco externo recomendado). Todo es reversible desde ese respaldo.

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
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HOME = Path.home()
VERSION = "2.1.0"
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
    "snapshots huérfanos:": "orphan snapshots:",
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


# Marcadores de compactación por herramienta
CLAUDE_MARKERS = ("compactMetadata", "Conversation compacted", '"isSummary":true', '"type":"summary"')
CODEX_MARKER = '"type":"compacted"'
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
    return datetime.now().strftime("%Y%m%d-%H%M%S")


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
        last_marker = -1
        offset = 0
        with open(f, "rb") as fh:
            for raw in fh:
                line = raw.decode("utf-8", errors="ignore")
                if any(m in line for m in CLAUDE_MARKERS):
                    last_marker = offset
                offset += len(raw)
        if last_marker >= 0:
            compacted += 1
            reclaim += last_marker
            top.append((last_marker, size, str(f)))
    # Subagentes hijos (transcripts de un solo uso) — se conservan los acompact
    sub = sub_big = 0
    for p in base.rglob("subagents/agent-*.jsonl"):
        if "acompact" in p.name:
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
    for f in files:
        size = f.stat().st_size
        total += size
        last_marker = -1
        offset = 0
        with open(f, "rb") as fh:
            for raw in fh:
                line = raw.decode("utf-8", errors="ignore")
                if CODEX_MARKER in line:
                    last_marker = offset
                offset += len(raw)
        if last_marker >= 0:
            compacted += 1
            reclaim += last_marker
            top.append((last_marker, size, str(f)))
    archived = sum(p.stat().st_size for p in PATHS["codex_archived"].rglob("*")) \
        if PATHS["codex_archived"].exists() else 0
    return {"total": total, "reclaim": reclaim, "compacted": compacted,
            "archived": archived, "top": sorted(top, reverse=True)}


def scan_opencode():
    db = PATHS["opencode_db"]
    if not db.exists():
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cur = con.cursor()
    page_size = cur.execute("PRAGMA page_size").fetchone()[0]
    page_count = cur.execute("PRAGMA page_count").fetchone()[0]
    db_total = page_size * page_count
    ev = cur.execute("SELECT count(*), sum(length(data)) FROM event").fetchone()
    red = cur.execute(
        f"SELECT count(*), sum(length(data)) FROM event WHERE type IN ({','.join('?'*3)})",
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
        q = ",".join("?" * len(waste))
        mlen = sum(m[1] for m in msgs[:idx])
        plen = cur.execute(
            f"SELECT coalesce(sum(length(data)),0) FROM part WHERE message_id IN ({q})",
            waste).fetchone()[0]
        pids = [r[0] for r in cur.execute(
            f"SELECT id FROM part WHERE message_id IN ({q})", waste).fetchall()]
        elen = 0
        if pids:
            q2 = ",".join("?" * len(pids))
            elen = cur.execute(
                f"SELECT coalesce(sum(length(data)),0) FROM event "
                f"WHERE json_extract(data,'$.part.id') IN ({q2})", pids).fetchone()[0]
        reclaim += mlen + plen + elen
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
                con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
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
                con.close()
            except sqlite3.Error:
                pass
    brain = sum(dir_size(gem / b) for b in GEMINI_BRAIN_DIRS)
    scratch = sum(dir_size(gem / b) for b in
                  ("antigravity/scratch", "antigravity-cli/scratch",
                   "antigravity-ide/scratch", "antigravity/implicit",
                   "antigravity-cli/implicit", "antigravity-ide/implicit"))
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
    total = reclaim = 0
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
        total += tot
        reclaim += rec
        extra = ""
        ncomp = r.get("compacted", 0)
        if name == "Codex":
            extra = f"  | archived_sessions: {human(r.get('archived',0))}"
        elif name == "Claude Code":
            extra = (f"  | subagentes: {human(r.get('subagents_bytes',0))} "
                     f"({r.get('subagents_n',0)}) + workflows "
                     f"{human(r.get('workflows_bytes',0))}")
        if name == "OpenCode":
            extra = (f"  | {_('eventos redundantes:')} {human(r['redundant'][1])} "
                     f"({r['redundant'][0]:,} {_('filas')})")
            ncomp = r.get("compactions", 0)
        elif name == "Antigravity":
            extra = (f"  | WAL: {human(r.get('wal_total',0))} | brain: "
                     f"{human(r.get('brain',0))} | recordings: "
                     f"{human(r.get('recordings',0))}")
            ncomp = r.get("compacted", 0)
            tot = r.get("conv_total", 0) + r.get("wal_total", 0)
            rec = r.get("reclaim", 0)
        print(f"{name:<13}{human(tot):>10}  {_('recuperable por compactación:')} {human(rec):>9}  "
              f"({ncomp} {_('compactadas')}){extra}")
        for b, s, fpath in r.get("top", [])[:4]:
            print(f"    {human(b):>9} de {human(s):>9}  {Path(fpath).name[:50]}")

    print(f"{'='*72}")
    print(f"{'TOTAL':<13}{human(total):>10}   {_('recuperable:')} {human(reclaim)}")
    print(f"  {_('Caches/snapshots adicionales:')}")
    for label, path in (("opencode snapshots", PATHS["opencode_dir"] / "snapshot"),
                        ("opencode tool-output", PATHS["opencode_dir"] / "tool-output"),
                        ("opencode logs", PATHS["opencode_dir"] / "log"),
                        ("codex cache", PATHS["codex_cache"]),
                        ("codex logs sqlite", PATHS["codex_logs"])):
        print(f"    {label:<22}{human(dir_size(path)):>10}")

    sk = scan_skills()
    print()
    print(f"  {_('Skills:')} {sk['n']} {_('en')} {len(SKILL_ROOTS)} {_('raíces,')} {human(sk['total'])}")
    if sk["dupes"]:
        print(f"  {_('Skills repetidas:')}")
        for name, copies, links in sk["dupes"]:
            detail = " + ".join(f"{human(sz)} ({Path(r).parent.name}/{Path(r).name})"
                                for r, sz in copies)
            if links:
                detail += f" {_('| ya symlink:')} {', '.join(r for r, _, _ in links)}"
            print(f"    {name:<28}{detail}")
        print(f"    {_('Recomendación: dejar una canonical y crear symlinks en las demás.')}")
    else:
        print(f"  {_('Sin skills repetidas.')}")
    return total, reclaim


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def database_in_use(path):
    try:
        result = subprocess.run(["lsof", "--", str(path)],
                                capture_output=True, text=True)
        return bool(result.stdout.strip())
    except Exception:
        return None


def safe_copy(src, dst):
    """Copia datos + metadatos si el destino los acepta (exFAT no soporta chflags)."""
    shutil.copyfile(src, dst)
    try:
        shutil.copystat(src, dst)
    except OSError:
        pass


def backup(backup_dir):
    dest = Path(backup_dir) / f"conversation-reclaim-{now()}"
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    print(f"{_('Backup en:')} {dest}")

    # Claude Code: copia completa de ~/.claude/projects
    if PATHS["claude_projects"].exists():
        d = dest / "claude-projects"
        d.mkdir(parents=True, exist_ok=True)
        for f in PATHS["claude_projects"].rglob("*"):
            rel = f.relative_to(PATHS["claude_projects"])
            if f.is_dir():
                (d / rel).mkdir(parents=True, exist_ok=True)
            else:
                (d / rel).parent.mkdir(parents=True, exist_ok=True)
                safe_copy(f, d / rel)
        copied += dir_size(d)
        print(f"  claude-projects  -> {human(dir_size(d))}")

    # Codex: sesiones + archivadas + cache + logs
    for name, p in (("codex-sessions", PATHS["codex_sessions"]),
                    ("codex-archived", PATHS["codex_archived"]),
                    ("codex-cache", PATHS["codex_cache"])):
        if p.exists():
            shutil.copytree(p, dest / name, copy_function=safe_copy)
            copied += dir_size(p)
            print(f"  {name:<18}-> {human(dir_size(p))}")
    for l in PATHS["codex_logs"]:
        if l.exists():
            safe_copy(l, dest / f"codex-{l.name}")
            copied += l.stat().st_size

    # OpenCode: DB (copia consistente), snapshot, tool-output, log
    if PATHS["opencode_db"].exists():
        db_backup = dest / "opencode.db"
        sqlite3.connect(PATHS["opencode_db"]).backup(sqlite3.connect(db_backup))
        copied += db_backup.stat().st_size
        print(f"  opencode.db       -> {human(db_backup.stat().st_size)}")
    for name in ("snapshot", "tool-output", "log"):
        p = PATHS["opencode_dir"] / name
        if p.exists() and dir_size(p) > 0:
            shutil.copytree(p, dest / f"opencode-{name}", copy_function=safe_copy)
            copied += dir_size(p)
            print(f"  opencode-{name:<14}-> {human(dir_size(p))}")

    # Command Code
    if PATHS["commandcode"].exists():
        shutil.copytree(PATHS["commandcode"], dest / "commandcode",
                        copy_function=safe_copy)
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
                                copy_function=safe_copy)
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
    for app, name in ((PATHS["antigravity_app"], "antigravity-app"),
                      (PATHS["antigravity_ide_app"], "antigravity-ide-app")):
        p = app / "logs"
        if p.exists():
            shutil.copytree(p, dest / f"{name}-logs", copy_function=safe_copy)
            copied += dir_size(p)
            print(f"  {name}-logs      -> {human(dir_size(p))}")

    print(f"  {_('TOTAL backup:')} {human(copied)}")
    return dest


# ---------------------------------------------------------------------------
# Aplicación de reducciones
# ---------------------------------------------------------------------------

def truncate_file_at_marker(f, markers, label):
    """Recorta el archivo quedándonos desde el último marcador (inclusive).

    Devuelve (bytes_recortados, tamaño_original, hecho, offset_del_marcador).
    El reemplazo es atómico (tmp + replace): si el script falla a mitad,
    el original queda intacto y solo sobra un archivo *.reclaim-tmp.
    """
    size = f.stat().st_size
    last_marker = -1
    with open(f, "rb") as fh:
        offset = 0
        for raw in fh:
            line = raw.decode("utf-8", errors="ignore")
            if any(m in line for m in markers):
                last_marker = offset
            offset += len(raw)
    if last_marker < 0:
        return 0, size, False, -1
    kept = size - last_marker
    tmp = f.with_suffix(".jsonl.reclaim-tmp")
    with open(f, "rb") as fh:
        fh.seek(last_marker)
        with open(tmp, "wb") as out:
            shutil.copyfileobj(fh, out)
    tmp.replace(f)
    return size - kept, size, True, last_marker


def write_manifest(entries):
    """Registro de cada cambio aplicado (fallback: saber qué se tocó y cuándo)."""
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    m = MANIFEST_DIR / f"manifest-{now()}.jsonl"
    with open(m, "w") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"  {_('Manifiesto:')} {m}")
    return m


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
        cut, size, done, marker = truncate_file_at_marker(f, CLAUDE_MARKERS, "claude")
        if done:
            freed += cut
            entries.append({"tool": "claude", "file": str(f),
                            "cut_bytes": cut, "old_size": size,
                            "marker_offset": marker, "time": now()})
            print(f"  {Path(f).name[:14]}... {human(cut)} de {human(size)} {_('recortados')}")

    # Subagentes hijos (transcripts de un solo uso del modo ultra/plan etc.)
    # Se conservan los agent-acompact (resúmenes de compactación) y los memory/.
    sub_n = sub_bytes = 0
    for p in base.rglob("subagents/agent-*.jsonl"):
        if "acompact" in p.name:
            continue
        sub_bytes += p.stat().st_size
        sub_n += 1
        p.unlink()
        meta = p.with_name(p.name.replace(".jsonl", "") + ".meta.json")
        if meta.exists():
            meta.unlink()
    for meta in base.rglob("subagents/agent-*.meta.json"):
        if "acompact" in meta.name:
            continue
        sub_bytes += meta.stat().st_size
        sub_n += 1
        meta.unlink()
    for wf in base.rglob("subagents/workflows"):
        sub_bytes += dir_size(wf)
        shutil.rmtree(wf)
    if sub_n:
        print(f"  {_('subagentes eliminados:')} {sub_n} {_('archivos,')} {human(sub_bytes)}")
        entries.append({"tool": "claude", "file": "subagents/*",
                        "cut_bytes": sub_bytes, "old_size": sub_bytes,
                        "marker_offset": -1, "time": now(),
                        "note": f"subagent transcripts ({sub_n} files)"})
    return freed, entries


def apply_codex():
    base = PATHS["codex_sessions"]
    entries = []
    freed = 0
    if not base.exists():
        return 0, entries
    for f in sorted(base.rglob("*.jsonl")):
        cut, size, done, marker = truncate_file_at_marker(f, (CODEX_MARKER,), "codex")
        if done:
            freed += cut
            entries.append({"tool": "codex", "file": str(f),
                            "cut_bytes": cut, "old_size": size,
                            "marker_offset": marker, "time": now()})
            print(f"  {Path(f).name[:30]}... {human(cut)} de {human(size)} {_('recortados')}")
    return freed, entries


def apply_opencode_files():
    """Snapshots huérfanos + tool-output + logs (seguro sin cerrar opencode)."""
    freed = 0
    snap = PATHS["opencode_dir"] / "snapshot"
    if snap.exists():
        sz = dir_size(snap)
        for child in snap.iterdir():
            shutil.rmtree(child)
        freed += sz
        print(f"  {_('snapshots huérfanos:')} {human(sz)}")
    tool = PATHS["opencode_dir"] / "tool-output"
    if tool.exists():
        sz = dir_size(tool)
        for child in tool.iterdir():
            if child.is_file():
                child.unlink()
        freed += sz
        print(f"  tool-output: {human(sz)}")
    log = PATHS["opencode_dir"] / "log"
    if log.exists():
        sz = dir_size(log)
        shutil.rmtree(log)
        freed += sz
        print(f"  logs: {human(sz)}")
    return freed


def apply_codex_cache():
    freed = 0
    if PATHS["codex_cache"].exists():
        sz = dir_size(PATHS["codex_cache"])
        shutil.rmtree(PATHS["codex_cache"])
        freed += sz
        print(f"  {_('codex cache:')} {human(sz)}")
    for l in PATHS["codex_logs"]:
        if l.exists():
            sz = l.stat().st_size
            l.unlink()
            freed += sz
            print(f"  {l.name}: {human(sz)}")
    return freed


def prune_opencode_db(backup_dir=None, no_backup=False):
    """Poda del opencode.db: requiere que opencode esté cerrado.

    Hace lo del proyecto opencode-db-prune (eventos redundantes) + poda
    pre-compactación. No toca la sesión de auditoría: solo recorta lo
    anterior a su última compactación.

    Es destructivo e irreversible: exige un respaldo explícito
    (--backup-dir) o aceptar el riesgo (--no-backup).
    """
    db = PATHS["opencode_db"]
    if not db.exists():
        print(f"  {_('no existe opencode.db')}")
        return 1
    if not backup_dir and not no_backup:
        print(f"  {_('REFUSED: apply-db borra datos de forma irreversible (eventos + mensajes).')}")
        print(f"  {_('Pasa --backup-dir <disco> para respaldar la DB o --no-backup si aceptas el riesgo.')}")
        return 7
    if backup_dir:
        dest = Path(backup_dir) / f"conversation-reclaim-{now()}"
        dest.mkdir(parents=True, exist_ok=True)
        db_backup = dest / "opencode.db"
        sqlite3.connect(db).backup(sqlite3.connect(db_backup))
        print(f"  {_('Backup en:')} {db_backup} ({human(db_backup.stat().st_size)})")
    else:
        print("  --no-backup: no hay copia de la DB. El manifiesto queda en ~/.conversation-reclaim/.")
    if _IS_WIN:
        print("  Windows: la detección de archivo en uso no está disponible.")
        print("  Asegúrate de que opencode esté completamente cerrado.")
    else:
        in_use = database_in_use(db)
        if in_use:
            print(f"  {_('REFUSED: opencode.db está abierto (opencode corriendo).')}")
            print(f"  {_('Cierra opencode y vuelve a ejecutar:')}  python3 reclaim.py apply-db")
            return 2
    con = sqlite3.connect(db)
    cur = con.cursor()

    # Pre-flight: el contenido final vive en part, no solo en event
    row = cur.execute("select id from session order by time_created asc limit 1").fetchone()
    if row:
        parts = cur.execute(
            "select count(*) from part where message_id in "
            "(select id from message where session_id=?)", (row[0],)).fetchone()[0]
        text = cur.execute(
            "select 1 from part where message_id in "
            "(select id from message where session_id=?) "
            "and json_extract(data,'$.type')='text' "
            "and length(json_extract(data,'$.text'))>20 limit 1", (row[0],)).fetchone()
        if parts == 0 or not text:
            print(f"  {_('REFUSED: el contenido solo existe en la tabla event. No se toca.')}")
            con.close()
            return 3

    size_before = db.stat().st_size
    print(f"  {_('tamaño antes:')} {human(size_before)}")

    # 1) Eventos redundantes (snapshots de streaming duplicados)
    red = cur.execute(
        f"SELECT count(*), coalesce(sum(length(data)),0) FROM event "
        f"WHERE type IN ({','.join('?'*3)})", OPENCODE_REDUNDANT_EVENTS).fetchone()
    print(f"  {_('eventos redundantes:')} {red[0]:,} {_('filas')}, {human(red[1])}")
    if red[0]:
        cur.execute(f"DELETE FROM event WHERE type IN ({','.join('?'*3)})",
                    OPENCODE_REDUNDANT_EVENTS)
        con.commit()
        print(f"  {_('borrados')} {red[0]:,} {_('eventos redundantes')}")

    # 2) Poda pre-compactación (mensajes/parts/eventos anteriores a la última)
    rows = cur.execute(
        "SELECT session_id, data FROM part WHERE json_extract(data,'$.type')='compaction'"
    ).fetchall()
    comps = defaultdict(list)
    for sid, data in rows:
        comps[sid].append(json.loads(data))
    for sess, clist in comps.items():
        tail = clist[-1].get("tail_start_id")
        if not tail:
            continue
        msgs = cur.execute(
            "SELECT id FROM message WHERE session_id=? ORDER BY time_created, id",
            (sess,)).fetchall()
        ids = [m[0] for m in msgs]
        try:
            idx = ids.index(tail)
        except ValueError:
            continue
        waste = ids[:idx]
        if not waste:
            continue
        q = ",".join("?" * len(waste))
        pids = [r[0] for r in cur.execute(
            f"SELECT id FROM part WHERE message_id IN ({q})", waste).fetchall()]
        if pids:
            q2 = ",".join("?" * len(pids))
            cur.execute(f"DELETE FROM event WHERE json_extract(data,'$.part.id') IN ({q2})", pids)
        cur.execute(f"DELETE FROM part WHERE message_id IN ({q})", waste)
        cur.execute(f"DELETE FROM message WHERE id IN ({q})", waste)
        con.commit()
        print(f"  {_('sesión')} {sess[:14]}...: {len(waste)} {_('mensajes pre-compactación podados')}")

    print(f"  {_('VACUUM (puede tardar)...')}")
    cur.execute("VACUUM")
    ok = cur.execute("PRAGMA integrity_check").fetchone()[0]
    con.close()
    size_after = db.stat().st_size
    print(f"  {_('integridad:')} {ok}")
    print(f"  {_('tamaño antes:')} {human(size_before)} -> {human(size_after)} "
          f"({_('liberado')} {human(size_before - size_after)})")
    return 0


def apply_antigravity(steps=True):
    """Antigravity: recorta transcripts en el último marcador de compactación,
    poda los pasos pre-compactación de las DB de conversación (si steps=True)
    y limpia logs/crashes/cache. Todo debe estar respaldado antes."""
    gem = PATHS["gemini"]
    freed = 0

    # 1) Transcripts: quedarse desde el último CONVERSATION_HISTORY
    for brain in GEMINI_BRAIN_DIRS:
        bdir = gem / brain
        if not bdir.exists():
            continue
        for t in bdir.rglob("transcript*.jsonl"):
            if not t.is_file():
                continue
            size = t.stat().st_size
            last_marker = -1
            with open(t, "rb") as fh:
                offset = 0
                for raw in fh:
                    if b'"type":"CONVERSATION_HISTORY"' in raw:
                        last_marker = offset
                    offset += len(raw)
            if last_marker > 0:
                tmp = t.with_suffix(".jsonl.reclaim-tmp")
                with open(t, "rb") as fh, open(tmp, "wb") as out:
                    fh.seek(last_marker)
                    shutil.copyfileobj(fh, out)
                tmp.replace(t)
                freed += last_marker
                print(f"  transcript {Path(t).parent.parent.name[:8]}... "
                      f"{human(last_marker)} {_('recortados')}")

    # 2) DBs de conversación: podar pasos anteriores a la última compactación
    if steps:
        for sub in GEMINI_CONV_DIRS:
            d = gem / sub
            if not d.exists():
                continue
            for db in d.glob("*.db"):
                if db.name.endswith((".db-shm", "-wal")):
                    continue
                if database_in_use(db):
                    print(f"  {db.name[:12]}... {_('en uso, se omite')}")
                    continue
                try:
                    con = sqlite3.connect(db)
                    mark = con.execute(
                        "SELECT idx FROM steps WHERE step_type=? ORDER BY idx DESC LIMIT 1",
                        (ANTIGRAVITY_COMPACT_STEP,)).fetchone()
                    if not mark or mark[0] == 0:
                        con.close()
                        continue
                    idx = mark[0]
                    before = sum(r[0] for r in con.execute(
                        "SELECT length(step_payload) FROM steps WHERE idx<?", (idx,)))
                    if before == 0:
                        con.close()
                        continue
                    for table in ("steps", "gen_metadata", "executor_metadata",
                                  "parent_references", "battle_mode_infos"):
                        con.execute(f"DELETE FROM {table} WHERE idx<?", (idx,))
                    con.commit()
                    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    con.execute("VACUUM")
                    con.close()
                    freed += before
                    print(f"  {sub}/{db.name[:12]}... {human(before)} "
                          f"({idx} {_('pasos pre-compactación podados')})")
                except sqlite3.Error as e:
                    print(f"  {db.name[:12]}... error: {e}")

    # 3) Logs, crashes y caches de los componentes .gemini
    for sub in ("antigravity", "antigravity-cli", "antigravity-ide"):
        for junk in ("log", "crashes", "cache", "scratch"):
            p = gem / sub / junk
            if p.exists():
                sz = dir_size(p)
                shutil.rmtree(p)
                freed += sz
                print(f"  .gemini/{sub}/{junk}: {human(sz)}")

    # 4) browser_recordings (imágenes del modo browser, meses de antigüedad)
    rec = gem / "antigravity-ide" / "browser_recordings"
    if rec.exists():
        sz = dir_size(rec)
        shutil.rmtree(rec)
        freed += sz
        print(f"  browser_recordings: {human(sz)}")

    # 4) Logs de las apps de escritorio
    for app, name in ((PATHS["antigravity_app"], "Antigravity"),
                      (PATHS["antigravity_ide_app"], "Antigravity IDE")):
        p = app / "logs"
        if p.exists():
            sz = dir_size(p)
            shutil.rmtree(p)
            freed += sz
            print(f"  {name} (logs): {human(sz)}")
    return freed


def apply(args):
    manifest = []
    dest = None
    if args.backup_dir:
        dest = backup(args.backup_dir)
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
        freed += apply_opencode_files()
    if only in (None, "codex"):
        freed += apply_codex_cache()
    if only in (None, "antigravity"):
        freed += apply_antigravity(steps=not args.no_antigravity_steps)
    if only == "caches":
        freed += apply_opencode_files()
        freed += apply_codex_cache()
        freed += apply_antigravity(steps=False)
    if manifest:
        m = write_manifest(manifest)
        print(f"  ({len(manifest)} {_('cambios registrados en')} {m})")
    print(f"\n  {_('Reducciones aplicadas:')} {human(freed)}")
    print(f"  {_('Nota: la poda de opencode.db requiere opencode cerrado.')}")
    print(f"        {_('Cuando cierres opencode:')}  python3 reclaim.py apply-db --backup-dir <disco>")
    if dest:
        print(f"        {_('Respaldo completo en:')} {dest}")


def restore(backup_dir):
    print(f"{_('Restauración manual desde:')} {backup_dir}")
    print(f"{_('Copia los directorios según lo que quieras recuperar:')}")
    print("  claude-projects/  -> ~/.claude/projects")
    print("  codex-sessions/   -> ~/.codex/sessions")
    print(f"  opencode.db       -> ~/.local/share/opencode/opencode.db {_('(opencode cerrado)')}")
    print("  opencode-snapshot/-> ~/.local/share/opencode/snapshot")
    print("  opencode-tool-output/ -> ~/.local/share/opencode/tool-output")
    print("  commandcode/      -> ~/.commandcode/projects")


def main():
    ap = argparse.ArgumentParser(description="conversation-reclaim v" + VERSION)
    ap.add_argument("mode", nargs="?", default="scan",
                    choices=["scan", "apply", "apply-db", "restore", "skills"])
    ap.add_argument("--backup-dir", default=None,
                    help="destino del respaldo completo (opcional; disco externo recomendado)")
    ap.add_argument("--no-backup", action="store_true",
                    help="aplicar apply-db sin respaldo de la DB (asumir el riesgo)")
    ap.add_argument("--no-antigravity-steps", action="store_true",
                    help="no podar los pasos pre-compactación en las DB de Antigravity")
    ap.add_argument("--only", default=None,
                    choices=["claude", "codex", "opencode", "antigravity",
                             "commandcode", "caches"],
                    help="aplicar reducciones solo a esta herramienta")
    ap.add_argument("--lang", default=None, choices=["es", "en"],
                    help="idioma de salida (por defecto: $LANG, español si no se detecta)")
    args = ap.parse_args()

    if args.lang:
        globals()["EN"] = args.lang == "en"

    if args.mode == "scan":
        scan()
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
    elif args.mode == "apply":
        apply(args)
    elif args.mode == "apply-db":
        prune_opencode_db(args.backup_dir, args.no_backup)
    elif args.mode == "restore":
        restore(args.backup_dir or str(HOME / "respaldos-ia"))


if __name__ == "__main__":
    main()
