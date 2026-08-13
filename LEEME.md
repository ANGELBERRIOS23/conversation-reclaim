# conversation-reclaim

*[English version](README.md)*

> **Probado en macOS.** Linux debería funcionar. **Windows: aún NO está
> probado** — la herramienta es multiplataforma por diseño e incluye detección
> de rutas para Windows, pero si la corres ahí eres el pionero. Mira las
> [notas de Windows](#notas-de-windows-cómo-encontrar-qué-limpiar).

Recupera el espacio en disco que tus conversaciones de IA se comen en silencio
— **sin romperlas**.

Cada agente de IA guarda una copia completa de cada conversación en disco.
Cuando una conversación llega al límite de contexto se **compacta**: el modelo
resume todo lo anterior en unos párrafos y sigue. Pero el contenido original
**nunca se borra** — se queda en disco, inservible, esperando un resume que
solo cargará el resumen y los mensajes recientes.

```
1 → 100 (conversación completa)        compactada → resumen A
1 → 100 (conversación completa)        compactada → resumen B (incluye A)
1 → 100 (conversación completa)        compactada → resumen C (incluye B)
```

Encontramos una conversación de 776 MB de la cual **774 MB eran basura
pre-compactación**. Y no es solo la compactación: OpenCode guarda un log de
eventos donde cada actualización de streaming copia el mensaje completo (una
sola sesión llegó a **4.7 GB** de eventos redundantes), Antigravity deja
gigabytes de capturas del modo browser, y los agentes copian las mismas skills
en cuatro carpetas distintas.

Esta herramienta detecta el **último marcador de compactación** de cada
conversación y recorta todo lo anterior — conservando el resumen y lo reciente
intactos. Después limpia lo demás que es seguro: snapshots huérfanos,
tool-outputs, logs, caches, grabaciones del browser. **Siempre después de un
respaldo completo.**

## Pensada para agentes de IA — y para humanos

Dos formas de usarla:

1. **Por un agente de IA (recomendado)** — el repo incluye una skill para
   agentes (`skills/conversation-reclaim/SKILL.md`). Instálala y dile a tu
   agente *"libera espacio de mis conversaciones"* (o en inglés; la herramienta
   habla el idioma en el que le hables). La skill conoce los marcadores, las
   reglas de seguridad y el flujo respaldo-primero. Así la usa el autor.
2. **Directo desde la terminal** — un CLI de Python de un solo archivo (solo
   stdlib), sin dependencias ni instalación. Corre `scan` primero, revisa los
   números, y luego `apply` con un destino de respaldo:

```bash
python3 reclaim.py scan                    # estimación de solo lectura
python3 reclaim.py apply                   # aplica; escribe manifiesto en ~/.conversation-reclaim/
python3 reclaim.py apply --backup-dir /Volumes/DISCO/respaldos-ia   # respaldo completo opcional
python3 reclaim.py apply --only antigravity   # solo una herramienta
python3 reclaim.py apply-db --backup-dir /Volumes/DISCO   # poda opencode.db (opencode cerrado; respaldo obligatorio o --no-backup)
python3 reclaim.py skills                  # lista skills y detecta repetidas
python3 reclaim.py restore --backup-dir /ruta
```

**El respaldo es opcional, no es el comportamiento por defecto.** `apply` no
copia nada a un disco externo salvo que pases `--backup-dir`. Sin él, cada
cambio queda registrado en un manifiesto en
`~/.conversation-reclaim/manifest-<fecha>.jsonl` (archivo, bytes recortados,
offset del marcador, fecha), y los recortes JSONL son atómicos (temp + rename):
si el script se interrumpe, el original queda intacto. `apply-db` es la única
operación irreversible: **exige** un `--backup-dir` explícito (o `--no-backup`
para asumir el riesgo).

Idioma: la salida sigue `$RECLAIM_LANG`, luego `$LANG`, o usa `--lang es|en`.
Pídele en español y responde en español; en inglés, en inglés.

## Qué limpia, por herramienta

| Herramienta | Almacenamiento | Marcador de compactación | Qué hace `apply` |
|---|---|---|---|
| **Claude Code** | `~/.claude/projects/**/*.jsonl` | línea con `Conversation compacted` / `compactMetadata` | recorta todo lo anterior al último marcador |
| **Codex** | `~/.codex/sessions/**/rollout-*.jsonl` | evento `"type":"compacted"` | igual |
| **OpenCode** | `~/.local/share/opencode/opencode.db` | part `type=compaction` con `tail_start_id` | `apply-db`: eventos de streaming redundantes + mensajes pre-compactación + VACUUM |
| **OpenCode (archivos)** | `snapshot/`, `tool-output/`, `log/` | — | snapshots huérfanos, tool-outputs, logs |
| **Antigravity / Gemini** | `~/.gemini/antigravity{,,-cli,-ide}/conversations/*.db` | pasos `step_type 98` (CONVERSATION_HISTORY) | poda pasos pre-compactación + transcripts, scratch, logs, caches, `browser_recordings` |
| **Command Code** | `~/.commandcode/projects/` | ninguno detectado | solo escaneo/respaldo |

## El problema del opencode.db (opencode-db-prune, integrado)

El `opencode.db` de OpenCode usa event-sourcing: cada actualización de
streaming escribe un snapshot completo de la parte. Una sesión larga genera
cientos de miles de filas de pura duplicación — en nuestro caso 147k filas /
**4.4 GB** de una base de 4.9 GB, más del 90% redundante. `apply-db` elimina
esos eventos redundantes, luego poda los mensajes pre-compactación y hace
`VACUUM`. Es una fusión del proyecto dedicado
[opencode-db-prune](https://github.com/ANGELBERRIOS23/opencode-db-prune)
(úsalo directo si solo te interesa la base de datos).

## Seguridad

- **Respaldo opcional pero recomendado.** `apply` sin `--backup-dir` no copia
  nada a ningún lado; en su lugar escribe un manifiesto de cambios en
  `~/.conversation-reclaim/` y usa reemplazos atómicos de archivo (una corrida
  interrumpida deja los originales intactos — ver la skill para las recetas
  manuales de respaldo).
- `apply-db` exige un respaldo explícito (`--backup-dir`) o `--no-backup`.
- Refusa tocar `opencode.db` mientras opencode esté corriendo.
- Pre-flight: no borra eventos si el contenido solo vive en la tabla de
  eventos.
- Verificación de integridad (`PRAGMA integrity_check`) tras operar en DBs.
- Las DB de conversación de Antigravity se omiten si la app las tiene abiertas.
- Las skills **solo se listan, nunca se borran** (las repetidas se pueden
  deduplicar con symlinks).
- Los caches (tool-output, logs, snapshots, scratch) son seguros de borrar —
  el prompt caching vive en el servidor, no cuesta tokens.

## Notas de Windows: cómo encontrar qué limpiar

Windows **aún no está probado** — ayuda bienvenida. La herramienta ya resuelve
las rutas correctas, y aquí está dónde vive cada cosa en Windows para que
puedas verificar a mano:

| Qué | Ruta en Windows |
|---|---|
| Conversaciones de Claude Code | `%USERPROFILE%\.claude\projects\<proyecto>\<uuid>.jsonl` |
| Sesiones de Codex | `%USERPROFILE%\.codex\sessions\<año>\<mes>\<día>\rollout-*.jsonl` |
| Base de OpenCode | `%USERPROFILE%\.local\share\opencode\opencode.db` (también `%LOCALAPPDATA%\opencode\data` o `%APPDATA%\opencode`) |
| Snapshots / tool-output / logs de OpenCode | mismo directorio que la DB |
| Antigravity / Gemini | `%USERPROFILE%\.gemini\antigravity{,,-cli,-ide}\...` |
| Logs de las apps de Antigravity | `%APPDATA%\Antigravity\logs` y `%APPDATA%\Antigravity IDE\logs` |
| Command Code | `%USERPROFILE%\.commandcode\projects` |
| Skills | `%USERPROFILE%\.claude\skills`, `%USERPROFILE%\.config\opencode\skills`, `%USERPROFILE%\.agents\skills` |

Notas para usuarios de Windows:

- **`apply-db` no puede detectar una base abierta en Windows** (no hay `lsof`);
  asegúrate de que opencode esté completamente cerrado antes de correrlo.
- El recorte de JSONL funciona exactamente igual: el archivo se reescribe desde
  el último marcador de compactación.
- Las rutas de Antigravity bajo `%USERPROFILE%\.gemini` son las mismas en
  Windows.
- Si encuentras una ruta distinta, abre un issue o un PR — el proyecto quiere
  ser verdaderamente multiplataforma.

## Resultados verificados (macOS, máquina del autor)

- Claude Code: 1.8 GB → 355 MB (una conversación era de 776 MB, 774 MB de
  basura pre-compactación).
- OpenCode DB: 4.9 GB con 4.7 GB de eventos redundantes (147k filas).
- Antigravity/Gemini: 19 GB → 4.6 GB (poda de conversaciones + grabaciones de
  browser + respaldos que ya no servían).
- Skills: 12 duplicadas en 5 raíces (~17 MB, deduplicación vía symlinks).

## Licencia

MIT — ver [LICENSE](LICENSE).
