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
intactos. Después limpia datos desechables: snapshots, tool-outputs, logs,
caches, grabaciones del browser y transcripts de subagentes cerrados. Antes
muestra estas categorías destructivas; el respaldo externo completo es opcional.

## Pensada para agentes de IA — y para humanos

Tres formas de usarla:

1. **Por un agente de IA (recomendado)** — el repo incluye una skill para
   agentes (`skills/conversation-reclaim/SKILL.md`). Instálala y dile a tu
   agente *"libera espacio de mis conversaciones"* (o en inglés; la herramienta
   habla el idioma en el que le hables). La skill conoce los marcadores, las
   reglas de seguridad y el flujo revisión-primero. Así la usa el autor.
2. **Interfaz visual** — una aplicación nativa Tauri 2 con interfaz React y
   motor de limpieza en Rust. Respeta el modo claro/oscuro del sistema, se
   adapta a ventanas pequeñas, recuerda Español/English, marca lo recomendado
   y confirma antes de escribir. La app portable es independiente del CLI:
   quien la descarga no instala Python, Node ni Rust.
3. **Directo desde la terminal** — un CLI de Python (solo
   stdlib), sin dependencias ni instalación. Corre `scan` primero, revisa los
   números, y luego `apply` con un destino de respaldo:

### Opciones de descarga

Los [Releases de GitHub](https://github.com/ANGELBERRIOS23/conversation-reclaim/releases)
ofrecen descargas nativas generadas por GitHub Actions:

- **Windows x64 Setup (recomendado):** instala la app y habilita las
  actualizaciones automáticas firmadas.
- **Windows x64 portable:** descomprime y ejecútalo sin instalar. Usa Setup si
  quieres actualizaciones transparentes.
- **macOS Apple Silicon:** descomprime el paquete `macOS-arm64` y abre la app.
- **macOS Intel:** descomprime el paquete `macOS-x64` y abre la app.

Esos paquetes contienen la aplicación React/Rust ya compilada: el usuario no
instala Python ni ninguna dependencia. Un build de desarrollo
sin certificado puede requerir **clic derecho → Abrir** en macOS o confirmar
Windows SmartScreen. La app revisa el Release más reciente de GitHub al abrir;
si hay una actualización la muestra en Protección y solo la instala cuando el
usuario elige **Instalar y reiniciar**. Cada actualización se firma y verifica
criptográficamente antes de instalarse. Cada tag `v*` crea los paquetes
nativos, el índice del actualizador y sus firmas, y los adjunta al Release.

```bash
python3 reclaim.py scan                    # estimación de solo lectura
python3 reclaim.py apply                   # aplica; escribe manifiesto en ~/.conversation-reclaim/
python3 reclaim.py apply --backup-dir /Volumes/DISCO/respaldos-ia   # respaldo completo opcional
python3 reclaim.py apply --only antigravity   # solo una herramienta
python3 reclaim.py apply-db --backup-dir /Volumes/DISCO   # poda opencode.db (opencode cerrado; respaldo obligatorio o --no-backup)
python3 reclaim.py apply-db --no-backup --close-opencode  # avisa, cierra OpenCode normalmente y poda
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
| **Claude Code** | `~/.claude/projects/**/*.jsonl` | evento estructurado de resumen/compactación | recorta antes del último marcador + elimina sidechains cerrados |
| **Codex** | `~/.codex/sessions/**/rollout-*.jsonl` | evento superior `"type":"compacted"` | igual + elimina rollouts/índices de subagentes cerrados |
| **OpenCode** | `~/.local/share/opencode/opencode.db` | part `type=compaction` con `tail_start_id` | `apply-db`: eventos de streaming redundantes + mensajes pre-compactación + VACUUM |
| **OpenCode (archivos)** | `snapshot/`, `tool-output/`, `log/` | — | todos los snapshots locales, tool-outputs y logs |
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
- `--close-opencode` avisa y solicita el cierre normal de la app en macOS. Se
  niega si OpenCode es el anfitrión/ancestro del comando actual, para no matar
  al agente que ejecuta la limpieza. Ejecutarlo desde Terminal, Codex u otro
  harness externo.
- Pre-flight: no borra eventos si el contenido solo vive en la tabla de
  eventos.
- Verificación de integridad (`PRAGMA integrity_check`) tras operar en DBs.
- Las DB de conversación de Antigravity se omiten si la app las tiene abiertas.
- Los subagentes Codex cerrados se identifican mediante `session_meta`; los
  activos se preservan si existe un writer lock o un spawn edge abierto.
- Los sidechains de Claude, incluidos `agent-acompact-*`, se validan mediante
  sus metadatos JSON y solo se eliminan cuando no están abiertos.
- Antes de borrar transcripts de subagentes o `browser_recordings`, el CLI
  muestra cantidad y tamaño. Las grabaciones son capturas ya consumidas que
  Antigravity no reutiliza.
- Se puede limpiar desde Codex: la tarea actual, rollouts bloqueados y DB de
  logs activas se omiten; los historiales cerrados sí se limpian. Una ejecución
  posterior puede recuperar la tarea actual una vez cerrada.
- Las skills **solo se listan, nunca se borran** (las repetidas se pueden
  deduplicar con symlinks).
- Los caches (tool-output, logs, snapshots, scratch) son seguros de borrar —
  el prompt caching vive en el servidor, no cuesta tokens.

## Notas de Windows: cómo encontrar qué limpiar

La app apunta a **Windows 10 de 64 bits, versión 1803 o posterior**, y Windows
11. Tauri utiliza Microsoft Edge WebView2, distribuido con esas versiones de
Windows. Las rutas de limpieza todavía necesitan más pruebas reales, así que
la experiencia de usuarios de Windows sigue siendo bienvenida:

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

- La detección usa la API nativa de archivos compartidos de Windows, así que
  omite historiales activos sin depender de `lsof`. Cierra OpenCode manualmente
  antes de `apply-db`; el cierre automático por ahora es solo para macOS.
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

## Aviso legal (disclaimer)

**Sin garantía, úsalo bajo tu propio riesgo.** Esta herramienta borra datos
(contenido pre-compactación, caches, transcripts de subagentes, eventos
redundantes). Está diseñada para que tus conversaciones sigan funcionando — se
conservan el resumen y los mensajes recientes — pero eres **totalmente
responsable de lo que le pides a tu agente que elimine**. Si borras una
conversación importante, es decisión de quien lo pidió, no de este proyecto.
Revisa siempre la salida de `scan` antes de `apply`, y usa `--backup-dir` ante
la duda.

## Contribuciones — nuevos harness bienvenidos

Este proyecto quiere cubrir **todos los harness de IA**, no solo los actuales.
Son bienvenidas las contribuciones para **Cursor, Kiro, Ghost, Trae, los
harness de DeepSeek o cualquier otro** — incluido un harness que estés
construyendo tú mismo.

La app nativa usa un registro `Harness`: cada integración mantiene en un solo
módulo sus metadatos, raíces permitidas, reglas de escaneo y plan de limpieza.
El panel y el ejecutor descubren las integraciones registradas automáticamente
y muestran un símbolo neutral si aún no existe un logo. Consulta la
[guía para añadir un harness](docs/ADDING_A_HARNESS.md).

Checklist para el PR (para que los mantenedores puedan confiar y verificar):

1. **Indica el SO donde probaste** (p. ej. "probado en Windows 11 Pro 24H2",
   "probado en macOS 15, Linux sin probar").
2. **Indica el nombre de la herramienta y una URL** — el repo del CLI/app, la
   página del marketplace o sus docs — para que los mantenedores puedan
   verificar que la herramienta existe y cómo guarda datos antes de revisar.
3. Explica **dónde viven los datos** y **cuál es el marcador de compactación**
   (un campo del JSONL, una tabla/columna de SQLite, un tipo de evento...).
   Reutiliza los mismos patrones: `scan_<tool>()` + `apply_<tool>()` + una
   entrada en `PATHS`.
4. Muestra la salida de `scan` de tu herramienta antes/después, y confirma que
   el resume funciona tras el recorte.
5. **Todo PR pasa revisión humana** antes de aprobarse. Ningún automatismo
   fusiona código externo. Mantén los cambios en un solo archivo
   (`reclaim.py`) más los docs.

Si no escribes código: abre un **issue** con el nombre de la herramienta,
dónde viven sus conversaciones y una muestra del formato de almacenamiento —
eso ya es una gran contribución.

## Verificaciones de desarrollo

El proyecto sigue usando solo la biblioteca estándar. Ejecuta las regresiones:

```bash
python3 -m unittest discover -v
```

Para construir el paquete nativo del sistema operativo actual:

```bash
python3 -m pip install -r requirements-build.txt
npm ci
npm run build
cargo test --manifest-path src-tauri/Cargo.toml
npm run tauri build -- --bundles app       # macOS .app
# Windows: npm run tauri build -- --no-bundle
```

Cubren marcadores estructurados, recortes atómicos con permisos, subagentes,
manifiestos, totales, transacciones OpenCode y códigos de salida.

## Licencia

MIT — ver [LICENSE](LICENSE).
