---
name: conversation-reclaim
description: >-
  Libera espacio en disco acumulado por las conversaciones de agentes de IA
  (Claude Code, Codex, OpenCode, Antigravity/Gemini, Command Code) recortando
  TODO lo anterior a la última compactación de cada conversación y conservando
  el resumen y lo reciente. Incluye respaldo completo previo a disco externo y
  limpieza de caches/logs/snapshots huérfanos. Úsala cuando el usuario diga
  "limpiar conversaciones", "liberar espacio de claude/codex/opencode/
  antigravity", "eliminar lo anterior a las compactaciones", "cuánto espacio
  ocupan mis conversaciones", "qué puedo borrar de mis agentes" o quiera
  revisar duplicados de conversaciones entre herramientas.
---

# conversation-reclaim

Herramienta CLI en Python (solo stdlib) que **detecta el punto de la última
compactación** de cada conversación y recorta lo anterior, conservando el
resumen y lo reciente intactos. Nunca borra sin respaldo previo. Además
escanea **skills repetidas** (para deduplicar con symlinks) y reporta los
elementos pesados no automáticos (recordings, backups).

## Ubicación

- Herramienta: `conversation-reclaim/reclaim.py` (buscar en el workspace del
  usuario o en su repo). Si no está, clonarla del repo del usuario.
- Uso: `python3 reclaim.py <comando>`.

## Comandos

| Comando | Qué hace |
|---|---|
| `python3 reclaim.py scan` | Solo lectura: cuánto se puede liberar por herramienta y por conversación, caches, y skills repetidas. **Siempre correrlo primero** y reportar números al usuario. |
| `python3 reclaim.py skills` | Lista todas las skills por tamaño y marca las repetidas (copias reales que ocupan doble) vs las que ya son symlink. |
| `python3 reclaim.py apply --backup-dir <disco>` | Respalda TODO en el disco (externo recomendado) y luego recorta/poda todo lo seguro. Pide confirmación antes si el usuario no la dio. |
| `python3 reclaim.py apply --only claude\|codex\|opencode\|antigravity\|caches` | Aplicar solo a una herramienta. |
| `python3 reclaim.py apply --no-antigravity-steps` | Antigravity sin podar las DB de conversación (solo transcripts/logs). |
| `python3 reclaim.py apply-db` | Poda `opencode.db` (eventos redundantes + pre-compactación + VACUUM). **Refusa si opencode está corriendo** — avisar al usuario que lo cierre. |
| `python3 reclaim.py restore --backup-dir <ruta>` | Guía de restauración desde el respaldo. |

## Qué limpia `apply` por herramienta

- **Claude Code / Codex**: recorta cada conversación compactada hasta su último
  marcador (resumen + reciente se conservan).
- **OpenCode (archivos)**: snapshots huérfanos, tool-output y logs.
- **OpenCode (DB)**: `apply-db` — eventos de streaming redundantes +
  pre-compactación + VACUUM (requiere opencode cerrado).
- **Antigravity**: transcripts recortados en el marcador, pasos pre-compactación
  en las DB, scratch, logs, crashes, caches y **browser_recordings**.
- **Codex**: sesiones recortadas + cache y logs sqlite.

## browser_recordings — QUÉ SON Y CUÁNDO BORRARLAS

`~/.gemini/antigravity-ide/browser_recordings/` son **capturas/imágenes que
Antigravity guarda cuando usas el modo browser** (cada sesión con el navegador
deja frames `.jpg` + metadata). Pueden pesar varios GB. Regla recomendada:

- Revisar las fechas: si son de hace >1-2 meses, son candidatas seguras.
- Borrarlas **todo** o **las más viejas** (`--only antigravity` borra todo el
  directorio). No afectan conversaciones ni código.
- Codex NO tiene estas imágenes (su `computer-use` es la app en sí, 63 MB).
  Claude Code tampoco (sus dirs de browser están vacíos). Solo Antigravity las
  acumula.
- `antigravity-backup/` (~/.gemini) es un respaldo viejo del app: verificar con
  el usuario si lo hizo él y su última fecha de uso antes de tocar; en este
  equipo ya se eliminó por decisión del usuario.

## Dónde vive cada historial y su marcador de compactación

| Herramienta | Ruta | Marcador de compactación |
|---|---|---|
| Claude Code | `~/.claude/projects/<proyecto>/<uuid>.jsonl` | línea con `Conversation compacted` / `compactMetadata` / `isSummary` |
| Codex | `~/.codex/sessions/<año>/<mes>/<día>/rollout-*.jsonl` | evento `"type":"compacted"` |
| OpenCode | `~/.local/share/opencode/opencode.db` (SQLite) | part `type=compaction` con `tail_start_id`; basura en tabla `event` (`message.part.updated.1`, etc.) |
| Antigravity | `~/.gemini/antigravity{,,-cli,-ide}/conversations/<id>.db` | pasos `step_type 98` (CONVERSATION_HISTORY); transcripts en `brain/<id>/.system_generated/logs/` |
| Command Code | `~/.commandcode/projects/` | sin marcador detectable (solo escaneo) |

## Skills repetidas

Los agentes copian la misma skill a varias raíces (`~/.claude/skills`,
`~/.config/opencode/skills`, `~/.agents/skills`, `~/.gemini/skills`,
`~/.antigravity/skills`), ocupando espacio doble/triple. La herramienta solo
**lista y recomienda** — NO borra skills. Para deduplicar (con confirmación del
usuario): dejar una canonical y reemplazar las demás por symlink
(`ln -s ../../.claude/skills/<nombre> <otra-raíz>/<nombre>`), como ya hace
`~/.gemini/skills/media-use`.

## Flujo de trabajo recomendado

1. **Escanear**: `python3 reclaim.py scan` → reportar total escaneado y
   recuperable, más los top por conversación y los caches (snapshots huérfanos
   de opencode, tool-output, logs, scratch, recordings).
2. **Preguntar al usuario** qué autoriza. Regla del usuario: *backup primero,
   aplicar después*.
3. **Backup + aplicar**: `apply --backup-dir /Volumes/<disco>` (o `--only`).
   Anotar la ruta del respaldo generado.
4. **Verificar**: `PRAGMA integrity_check` en DBs tocadas, validar JSONL
   recortados, y pedir al usuario que pruebe reanudar una conversación de cada
   herramienta.
5. **opencode.db**: si opencode está corriendo, `apply-db` no puede ejecutarse;
   darle el comando exacto al usuario para cuando lo cierre.

## Reglas de seguridad (no romperlas)

- Nunca aplicar sin respaldo previo. El respaldo va a disco externo
  (`/Volumes/DOCUMENTOS` si existe).
- No tocar `browser_recordings` sin que el usuario lo autorice (son imágenes
  del modo browser; explicarle qué son y sus fechas). Codex y Claude NO tienen
  estas imágenes.
- No tocar `antigravity-backup` sin verificar con el usuario (puede ser un
  respaldo que él hizo).
- En Antigravity, si una DB está abierta por la app se omite (check con lsof).
- En opencode, `apply-db` se niega si la DB está en uso.
- El recorte de Claude Code/Codex conserva la línea del último marcador hacia
  adelante (resumen incluido); nunca recortar si no hay marcador.
- Los caches locales (tool-output, logs, snapshots, scratch, caches de codex)
  se pueden borrar sin gastar tokens: el prompt caching es del lado del
  servidor.
- **Skills: solo listar y recomendar. Nunca borrarlas.** Si el usuario quiere
  deduplicar, proponer symlinks y pedir confirmación explícita.

## Ejemplos típicos

```bash
python3 reclaim.py scan
python3 reclaim.py apply --backup-dir /Volumes/DOCUMENTOS
python3 reclaim.py apply --only antigravity --backup-dir /Volumes/DOCUMENTOS
python3 reclaim.py apply-db   # con opencode cerrado
```

## Verificación post-aplicación

- `sqlite3 ~/.local/share/opencode/opencode.db "PRAGMA integrity_check"` → ok
- `sqlite3 <db antigravity> "PRAGMA integrity_check"` → ok
- Parsear las primeras/últimas líneas de los JSONL recortados.
- Confirmar con el usuario que las conversaciones recientes siguen visibles y
  que puede reanudarlas.
