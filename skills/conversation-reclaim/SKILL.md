---
name: conversation-reclaim
description: >-
  Escanea y libera espacio acumulado por conversaciones de Claude Code, Codex,
  OpenCode, Antigravity/Gemini y Command Code. Recorta contenido anterior a la
  última compactación, elimina caches, browser recordings y transcripts de
  subagentes cerrados, ofrece respaldo opcional y registra cambios. Usar cuando
  el usuario pida limpiar conversaciones, liberar espacio de agentes, revisar
  cuánto ocupan, eliminar compactaciones anteriores o encontrar skills duplicadas.
---

# conversation-reclaim

Usar `conversation-reclaim/reclaim.py` desde el repositorio del usuario. Es un
CLI Python sin dependencias externas.

## Flujo obligatorio

1. Ejecutar `python3 reclaim.py scan` y mostrar al usuario lo recuperable.
2. Explicar las categorías que se eliminarán. En particular:
   - `browser_recordings` son capturas ya consumidas por Antigravity; no se
     reutilizan. El CLI las elimina por defecto y avisa cantidad/tamaño.
   - Los transcripts de subagentes son artefactos de un solo uso. El CLI borra
     únicamente hijos cerrados y preserva los activos.
   - OpenCode elimina todos sus snapshots locales, además de tool-output/logs.
3. Aplicar solo después de que el usuario autorice la limpieza. Ofrecer
   `--backup-dir`; es opcional para `apply` y obligatorio para `apply-db`, salvo
   aceptación explícita mediante `--no-backup`.
4. Reportar la ruta del manifiesto y, si existe, del respaldo.
5. Verificar JSONL/SQLite y pedir al usuario reanudar una conversación reciente.

## Comandos

| Comando | Acción |
|---|---|
| `python3 reclaim.py gui` | Abre el panel visual de selección y confirmación. |
| `python3 reclaim.py scan` | Escaneo de solo lectura. Ejecutar primero. |
| `python3 reclaim.py skills` | Lista skills y duplicados; nunca los elimina. |
| `python3 reclaim.py apply` | Limpia y escribe manifiesto sin respaldo externo. |
| `python3 reclaim.py apply --backup-dir <ruta>` | Respalda todos los targets antes de limpiar. |
| `python3 reclaim.py apply --only claude\|codex\|opencode\|antigravity\|caches` | Limita la limpieza. |
| `python3 reclaim.py apply --no-antigravity-steps` | Omite la poda de DB de Antigravity. |
| `python3 reclaim.py apply-db --backup-dir <ruta>` | Poda transaccional de `opencode.db`. |
| `python3 reclaim.py apply-db --no-backup` | Poda irreversible aceptando el riesgo. |
| `python3 reclaim.py apply-db --no-backup --close-opencode` | Avisar, cerrar OpenCode normalmente y podar. |
| `python3 reclaim.py restore --backup-dir <ruta>` | Imprime la guía de restauración manual. |

## Comportamiento por herramienta

- **Claude Code:** validar marcadores JSON estructuralmente, conservar resumen y
  contenido reciente. Detectar sidechains con `isSidechain`, `agentId` y
  `sessionId`; borrar los cerrados, incluidos `agent-acompact-*`. No tocar
  `memory/` ni el transcript principal.
- **Codex:** aceptar solo un evento JSON superior `type=compacted`. Detectar
  hijos mediante `session_meta.payload.thread_source=subagent`, UUID y
  `source.subagent`. Preservar si existe
  `~/.codex/thread-writer-locks/<id>.lock` o un edge `open` en
  `state_5.sqlite`; al borrar un hijo cerrado, retirar su rollout y filas de
  índice/log asociadas. Nunca inferirlo solo por nombre o texto.
  La limpieza puede ejecutarse desde Codex: omitir la tarea actual, rollouts
  bloqueados y DB de logs abiertas; recuperar el resto y dejar lo activo para
  una ejecución posterior.
- **OpenCode archivos:** borrar snapshots, tool-output y logs. El mensaje debe
  decir “todos los snapshots”, no “huérfanos”.
- **OpenCode DB:** ordenar compactaciones por sesión/tiempo/id, usar la última,
  bloquear con SQLite, borrar en una transacción, procesar IDs por lotes,
  ejecutar `VACUUM` e `integrity_check`. Rehusar si está abierto o si el
  contenido no está materializado fuera de `event`.
  Si está abierto, avisar y pedir autorización antes de usar
  `--close-opencode`. Cerrar solo mediante quit normal; nunca forzar. Si el PID
  que mantiene la DB es ancestro/anfitrión del CLI, rehusar cerrarlo y pedir que
  se ejecute desde Terminal, Codex u otro harness externo.
- **Antigravity:** recortar transcripts estructuralmente, podar pasos anteriores
  al último `step_type=98`, omitir DB abiertas y limpiar logs, crashes, cache,
  scratch y browser recordings con aviso previo.
- **Command Code:** solo escanear/respaldar hasta conocer un marcador seguro.

## Interfaz visual

Usar `gui.py` como capa delgada sobre las funciones auditadas de `reclaim.py`.
Mostrar estimación, recomendación, explicación y estado activo por categoría;
marcar por defecto solo elementos recomendados con tamaño mayor que cero. Dejar
que el usuario desmarque categorías, elija respaldo y confirme el conjunto
exacto antes de aplicar. Mantener toda operación de disco en el motor común para
que CLI y GUI compartan las mismas protecciones y manifiestos.

En Windows, detectar archivos abiertos mediante la API nativa de file sharing.
El cierre automático de OpenCode es solo macOS; pedir cierre manual en Windows.

## Distribución visual

Ofrecer varias rutas sin mezclar sus requisitos:

- Releases: `Conversation Reclaim.exe` portable para Windows x64 y `.app`
  autocontenida separada para macOS Apple Silicon e Intel. No requieren Python.
- Código fuente: `Conversation Reclaim.app`, `launch-gui.command` o
  `launch-gui.bat`; estos sí requieren Python con Tk.
- Agentes/desarrolladores: `python3 reclaim.py gui` o el CLI.

Construir cada binario en su propio sistema operativo mediante
`.github/workflows/portable-builds.yml`; PyInstaller no es cross-compiler.
Ejecutar `--smoke-test` sobre cada binario antes de publicar el ZIP. Avisar que
los builds sin certificado pueden activar Gatekeeper o SmartScreen y que una
distribución pública pulida necesita firma/notarización del propietario.

## Garantías y manifiesto

- Fallar cerrado ante JSON inválido, marcador ambiguo, symlink inesperado,
  archivo activo, esquema desconocido o imposibilidad de comprobar el uso.
- Realizar recortes con un temporal impredecible en el mismo directorio,
  preservar permisos, sincronizar y reemplazar atómicamente.
- Registrar cada recorte/borrado con herramienta, acción, ruta, bytes, marcador,
  estado y hora en `~/.conversation-reclaim/manifest-*.jsonl`.
- Si se pidió respaldo y alguna copia/verificación falla, abortar antes de
  mutar. El respaldo debe incluir recordings, caches, estado/logs Codex y todo
  target que vaya a eliminarse.
- Propagar códigos de salida no cero; no declarar éxito cuando hubo rechazo o
  fallo parcial.

## Rutas y marcadores

| Herramienta | Ruta | Marcador |
|---|---|---|
| Claude | `~/.claude/projects/<proyecto>/**/*.jsonl` | campos superiores de resumen/compactación |
| Codex | `~/.codex/sessions/**/rollout-*.jsonl` | evento superior `type=compacted` |
| OpenCode | `~/.local/share/opencode/opencode.db` | part compaction con `tail_start_id` |
| Antigravity | `~/.gemini/antigravity{,,-cli,-ide}/conversations/*.db` | step `step_type=98` |
| Command Code | `~/.commandcode/projects/` | ninguno conocido |

## Verificación

Ejecutar las regresiones del proyecto antes de distribuir cambios:

```bash
python3 -m unittest discover -v
python3 -m py_compile reclaim.py
```

Después de limpiar, comprobar `PRAGMA integrity_check` en las DB afectadas y
validar que los JSONL restantes se parsean desde la primera línea.

Las skills duplicadas solo se reportan. Proponer symlinks si el usuario desea
deduplicarlas y pedir confirmación específica antes de cambiar nada.
