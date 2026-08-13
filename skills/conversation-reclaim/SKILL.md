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
| `python3 reclaim.py apply` | Aplica reducciones **sin respaldo externo** (escribe un manifiesto de cambios en `~/.conversation-reclaim/`). |
| `python3 reclaim.py apply --backup-dir <disco>` | **Opcional**: respalda TODO en el disco (externo recomendado) y luego reduce. |
| `python3 reclaim.py apply --only claude\|codex\|opencode\|antigravity\|caches` | Aplicar solo a una herramienta. |
| `python3 reclaim.py apply --no-antigravity-steps` | Antigravity sin podar las DB de conversación (solo transcripts/logs). |
| `python3 reclaim.py apply-db --backup-dir <disco>` | Poda `opencode.db` (eventos redundantes + pre-compactación + VACUUM). **Exige respaldo explícito o `--no-backup`.** **Refusa si opencode está corriendo**. |
| `python3 reclaim.py restore --backup-dir <ruta>` | Guía de restauración desde el respaldo (si se hizo uno). |

**Regla de respaldo por defecto:** el respaldo externo NO es automático. Si el
usuario no pasa `--backup-dir`, `apply` funciona igual pero solo deja un
manifiesto (qué archivo, cuántos bytes, offset del marcador, fecha). Los
recortes de Claude/Codex son atómicos (temp + replace): si el script muere a
mitad, el original queda intacto. Para `apply-db` el respaldo es obligatorio
(flag `--backup-dir` o `--no-backup`): es la única operación irreversible.

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

## Fallback: si el script falla o se hace a mano

Todo lo que la herramienta hace se puede hacer/revisar manualmente. Esto es lo
que se sabe de cada almacenamiento (usado para diagnosticar y para limpieza
manual si la herramienta no corre):

**Manifiesto de cambios** (siempre se escribe): `~/.conversation-reclaim/manifest-<fecha>.jsonl`
— cada línea: `{"tool","file","cut_bytes","old_size","marker_offset","time"}`.
Úsalo para saber exactamente qué se tocó. Los `*.reclaim-tmp` que queden tras
una interrupción se pueden borrar (el original quedó intacto si el replace no
se ejecutó).

**Verificación rápida después de tocar algo:**
```bash
# Claude/Codex: el archivo debe ser JSONL válido desde la primera línea
python3 -c "import json;[json.loads(l) for l in open('ruta.jsonl')]"
# DBs SQLite
sqlite3 ruta.db "PRAGMA integrity_check"        # → ok
```

**Claude Code** (`~/.claude/projects/<proyecto>/<uuid>.jsonl`): recorte manual:
```bash
python3 - <<'EOF'
import json, pathlib
p = pathlib.Path("ARCHIVO.jsonl"); out = []
marker = 0
for i, raw in enumerate(open(p, 'rb')):
    if b'compactMetadata' in raw or b'Conversation compacted' in raw:
        marker += len(raw)  # el ÚLTIMO marcador es el que vale
    else:
        marker += len(raw)
EOF
```
(equivalente: `grep -n 'Conversation compacted' archivo.jsonl` → el último
número de línea; conservar desde ahí con `tail -n +N archivo.jsonl > nuevo`).

**Codex** (`~/.codex/sessions/<año>/<mes>/<día>/rollout-*.jsonl`): igual, con el
evento `"type":"compacted"`.

**OpenCode** (`opencode.db`, tabla `event` con tipos `message.part.updated.1`,
`message.updated.1`, `session.updated.1` que duplican cada part en streaming):
```bash
sqlite3 opencode.db "SELECT count(*), sum(length(data)) FROM event WHERE type='message.part.updated.1'"
# limpieza manual de los redundantes (tras respaldar y con opencode cerrado):
sqlite3 opencode.db "DELETE FROM event WHERE type IN ('message.updated.1','message.part.updated.1','session.updated.1'); VACUUM;"
```
Marcador de compactación: part `type=compaction` con `tail_start_id` (todo
mensaje anterior a ese id es pre-compactación).

**Antigravity/Gemini** (`~/.gemini/antigravity{,,-cli,-ide}/`): conversaciones
en `conversations/<id>.db` (pasos `step_type 98` = compactación), transcripts
en `brain/<id>/.system_generated/logs/transcript*.jsonl`, imágenes del modo
browser en `antigravity-ide/browser_recordings/` (pesan GB, son capturas de
sesiones de browser, borrables si son viejas), y `antigravity-backup/` (ver con
el usuario si es suyo y si lo sigue usando).

**Caches 100% seguros** (borrar no cuesta tokens — el prompt caching vive en el
servidor): `opencode snapshot/` (huérfanos si `session_context_epoch` está
vacía), `tool-output/`, `log/`, `.gemini/*/{log,crashes,cache,scratch}`,
`logs` de las apps Antigravity, `~/.codex/cache` y `logs_*.sqlite`.

**Restauración** con `restore`: copiar desde el respaldo `claude-projects/`,
`codex-sessions/`, `opencode.db`, `gemini-*/` etc. a sus rutas originales.

**Windows**: rutas bajo `%USERPROFILE%\.claude`, `%USERPROFILE%\.codex`,
`%USERPROFILE%\.local\share\opencode` (o `%LOCALAPPDATA%\opencode`),
`%USERPROFILE%\.gemini`, `%USERPROFILE%\.commandcode`, `%APPDATA%\Antigravity`.
En Windows no hay `lsof`: `apply-db` no detecta la DB abierta, cerrar opencode
a mano.

## Flujo de trabajo recomendado

1. **Escanear**: `python3 reclaim.py scan` → reportar total escaneado y
   recuperable, más los top por conversación y los caches (snapshots huérfanos
   de opencode, tool-output, logs, scratch, recordings).
2. **Preguntar al usuario** qué autoriza y si quiere respaldo externo
   (`--backup-dir /Volumes/<disco>`) o ir sin respaldo (solo manifiesto).
   Regla del usuario: *si hay respaldo, backup primero; después aplicar*.
3. **Aplicar**: `apply [--backup-dir <disco>]` (o `--only`). Anotar la ruta del
   respaldo si se hizo y la del manifiesto.
4. **Verificar**: `PRAGMA integrity_check` en DBs tocadas, validar JSONL
   recortados, y pedir al usuario que pruebe reanudar una conversación de cada
   herramienta.
5. **opencode.db**: si opencode está corriendo, `apply-db` no puede ejecutarse;
   darle el comando exacto al usuario para cuando lo cierre, recordando que
   requiere `--backup-dir` o `--no-backup`.

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

## Disclaimer (decírselo al usuario cuando haga falta)

La herramienta borra datos (contenido pre-compactación, caches, transcripts de
subagentes). El diseño conserva resumen + reciente, pero **quien pide el
borrado es responsable de lo que se borra**. Antes de `apply`, mostrar siempre
la salida de `scan` y confirmar con el usuario; recomendar `--backup-dir` ante
la duda. Si el usuario pide borrar una conversación entera a propósito, es su
decisión.

## Contribuciones a la herramienta (si el usuario quiere aportar)

Bienvenidos soportes para Cursor, Kiro, Ghost, Trae, harnesses de DeepSeek o
cualquiera. El PR debe indicar: SO donde se probó, nombre + URL del CLI/app
para verificar que existe y cómo guarda datos, dónde vive el historial y cuál
es su marcador de compactación (siguiendo `scan_<tool>()` + `apply_<tool>()` +
`PATHS`), salida de scan antes/después y confirmación de que el resume
funciona. **Todo PR pasa revisión humana antes de aprobarse.**

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
