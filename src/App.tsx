import { useEffect, useMemo, useState } from "react";
import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import {
  Check, ChevronDown, ChevronLeft, ChevronRight, CircleHelp, ExternalLink, Eye, FolderOpen,
  FileClock, FolderSearch, Globe2, HardDrive, Info, Languages,
  LoaderCircle, LockKeyhole, RefreshCw, ShieldCheck, Sparkles, Trash2,
} from "lucide-react";
import { BrandLogo } from "./BrandLogos";

type Language = "es" | "en";
type CategoryKey = string;
type Page = "overview" | "outputs" | "activity" | "protection";

type Category = {
  key: CategoryKey;
  name: string;
  descriptionEs: string;
  descriptionEn: string;
  logo: string;
  bytes: number;
  items: number;
  recommended: boolean;
  protected: boolean;
  available: boolean;
  details: string[];
};

type ScanResult = {
  categories: Category[];
  totalReclaimable: number;
  scannedAt: string;
  warnings: string[];
};

type ApplyResult = {
  freedBytes: number;
  plannedBytes: number;
  skippedBytes: number;
  manifestPath: string;
  applied: number;
  skipped: number;
  warnings: string[];
};

type OutputItem = {
  path: string;
  name: string;
  task: string;
  bytes: number;
  modified: string;
  copyPath: string | null;
  newerVersion: string | null;
  orphan?: boolean;
};

type OrphanFolder = {
  path: string;
  name: string;
  bytes: number;
  files: number;
  modified: string;
  hasGit: boolean;
};

type LargeTab = "outputs" | "images" | "orphans";

type GeneratedImage = {
  path: string;
  name: string;
  chat: string;
  bytes: number;
  modified: string;
  copyPath: string | null;
};

type TrashResult = {
  moved: number;
  freedBytes: number;
  manifestPath: string;
  errors: string[];
};

type UpdateInfo = {
  version: string;
  currentVersion: string;
  notes?: string;
};

const copy = {
  es: {
    product: "Conversation Reclaim",
    navOverview: "Resumen",
    navActivity: "Actividad",
    navSafety: "Protección",
    eyebrow: "Limpieza inteligente",
    title: "Recupera espacio sin perder tus conversaciones.",
    subtitle: "Analiza datos antiguos de asistentes de IA y elimina únicamente artefactos regenerables o ya compactados.",
    ready: "Disponible para liberar",
    selected: "seleccionado",
    lastScan: "Último análisis",
    scanNow: "Analizar de nuevo",
    scanning: "Analizando…",
    choose: "Elige qué limpiar",
    chooseHelp: "Las opciones recomendadas son seguras para el uso normal. Tus conversaciones actuales quedan protegidas.",
    recommended: "Recomendado",
    optional: "Opcional",
    protected: "Datos activos protegidos",
    items: "elementos",
    noData: "No se encontraron datos",
    notDetected: "No detectado",
    review: "Revisar selección",
    clean: "Liberar espacio",
    cleaning: "Liberando…",
    cancel: "Cancelar",
    confirmTitle: "¿Liberar el espacio seleccionado?",
    confirmBody: "Conversation Reclaim registrará cada cambio en un manifiesto. No cerrará aplicaciones ni tocará conversaciones activas.",
    completeTitle: "Limpieza completada",
    partialTitle: "Limpieza parcial",
    noChangesTitle: "Sin cambios aplicados",
    completeBody: "Se completaron todas las acciones seleccionadas.",
    partialBody: "Parte de la limpieza se completó, pero algunos archivos se omitieron para mantener tus datos seguros.",
    noChangesBody: "No se modificó ningún archivo. Revisa los detalles para saber por qué se omitieron las acciones.",
    estimate: "Estimado",
    actual: "Liberado realmente",
    appliedActions: "acciones aplicadas",
    skippedActions: "acciones omitidas",
    scanNotes: "Notas del análisis",
    details: "Ver detalles",
    manifest: "Mostrar manifiesto",
    close: "Listo",
    safetyTitle: "Tus conversaciones están protegidas",
    safetyBody: "La limpieza conserva las conversaciones activas y omite automáticamente cualquier archivo que esté en uso.",
    advanced: "Limpieza avanzada",
    advancedBody: "La poda profunda de bases de datos permanece disponible en el CLI para usuarios técnicos.",
    errorTitle: "No se pudo completar",
    demo: "Vista previa — conecta el motor nativo al abrir la app de escritorio.",
    activityTitle: "Actividad",
    activityBody: "Consulta el análisis más reciente y los resultados de limpieza de esta sesión.",
    latestScan: "Análisis más reciente",
    found: "Espacio encontrado",
    lastCleanup: "Última limpieza",
    noCleanup: "Todavía no has realizado una limpieza en esta sesión.",
    protectionTitle: "Protección",
    protectionBody: "Estas reglas se aplican automáticamente antes de modificar cualquier archivo.",
    activeRule: "Archivos en uso",
    activeRuleBody: "Las conversaciones y bases de datos activas se omiten automáticamente.",
    manifestRule: "Registro verificable",
    manifestRuleBody: "Cada cambio queda documentado con su ruta, tamaño y resultado.",
    atomicRule: "Cambios seguros",
    atomicRuleBody: "Los recortes se preparan por separado y solo reemplazan el original al terminar correctamente.",
    updates: "Actualizaciones",
    updateBody: "Conversation Reclaim comprueba GitHub de forma segura y verifica la firma antes de instalar.",
    checkUpdates: "Buscar actualizaciones",
    checkingUpdates: "Buscando…",
    upToDate: "Tienes la versión más reciente",
    updateAvailable: "Nueva versión disponible",
    installUpdate: "Instalar y reiniciar",
    installingUpdate: "Instalando…",
    temporaryMedia: "Medios temporales",
    images: "Imágenes en conversaciones",
    codexWork: "Archivos de trabajo de Codex",
    oldVersions: "Versiones viejas y restos",
    ageLabel: "Solo lo que no cambió en",
    ageUnit: "días",
    ageHelp: "Aplica a imágenes, archivos de trabajo y restos. Vuelve a analizar después de cambiarlo.",
    navOutputs: "Archivos grandes",
    outputsTitle: "Outputs de Codex",
    outputsBody: "Lo que Codex te entregó, del más pesado al más liviano. Nada se borra solo: marca lo que ya no necesitas y mándalo a la Papelera.",
    findOutputs: "Buscar archivos",
    searching: "Buscando…",
    outputsHint: "Busca en ~/Documents/Codex y revisa si ya tienes una copia idéntica en tus carpetas. Puede tardar un poco.",
    outputsEmpty: "No hay outputs de 1 MB o más.",
    outputsFound: "archivos",
    withCopyCount: "con copia en otra carpeta",
    copyAt: "Copia en",
    newerVersion: "Hay versión más nueva",
    selectWithCopy: "Marcar los que tienen copia",
    clearSelection: "Quitar selección",
    selectAll: "Seleccionar todo",
    noCopy: "Sin copia en otro lado",
    tabOrphans: "Chats eliminados",
    orphansTitle: "Carpetas de chats eliminados",
    orphansBody: "Carpetas de ~/Documents/Codex cuya conversación ya no existe en Codex, ni activa ni archivada. Se recomienda borrarlas: van completas a la Papelera.",
    orphansHint: "Compara cada carpeta con las conversaciones de Codex. Solo cuenta carpetas sin cambios en los últimos 2 días.",
    orphansEmpty: "No hay carpetas de chats eliminados.",
    orphansFound: "carpetas",
    orphanBadge: "Chat eliminado",
    selectOrphans: "Marcar los de chats eliminados",
    filesLabel: "archivos",
    hasGit: "tiene un repositorio git",
    moveToTrash: "Mover a la Papelera",
    moving: "Moviendo…",
    trashConfirmTitle: "¿Mover a la Papelera?",
    trashConfirmBody: "Los archivos marcados van a la Papelera. Puedes sacarlos de ahí hasta que la vacíes.",
    trashDone: "en la Papelera",
    trashErrors: "no se pudieron mover",
    tabOutputs: "Outputs",
    tabImages: "Imágenes generadas",
    imagesTitle: "Imágenes generadas por Codex",
    imagesBody: "Todas las imágenes que Codex generó. Las recomendadas ya tienen una copia idéntica en tus carpetas, así que no pierdes nada si las quitas. Tú decides.",
    imagesHint: "Busca en ~/.codex/generated_images y revisa si ya tienes una copia idéntica en tus carpetas.",
    imagesEmpty: "Codex no tiene imágenes generadas.",
    imagesFound: "imágenes",
    recommendedCount: "recomendadas",
    selectRecommended: "Marcar las recomendadas",
    recommendedBadge: "Recomendada",
    preview: "Ver",
    openFile: "Abrir",
    revealMac: "Mostrar en Finder",
    revealOther: "Mostrar en la carpeta",
    noPreview: "No hay vista previa para este tipo de archivo. Ábrelo con su app o muéstralo en la carpeta.",
    markForTrash: "Marcar para la Papelera",
    unmark: "Quitar marca",
    previous: "Anterior",
    next: "Siguiente",
  },
  en: {
    product: "Conversation Reclaim",
    navOverview: "Overview",
    navActivity: "Activity",
    navSafety: "Protection",
    eyebrow: "Smart cleanup",
    title: "Reclaim space without losing your conversations.",
    subtitle: "Finds old AI assistant data and removes only regenerable or previously compacted artifacts.",
    ready: "Ready to reclaim",
    selected: "selected",
    lastScan: "Last scan",
    scanNow: "Scan again",
    scanning: "Scanning…",
    choose: "Choose what to clean",
    chooseHelp: "Recommended options are safe for normal use. Your current conversations stay protected.",
    recommended: "Recommended",
    optional: "Optional",
    protected: "Active data protected",
    items: "items",
    noData: "No data found",
    notDetected: "Not detected",
    review: "Review selection",
    clean: "Reclaim space",
    cleaning: "Reclaiming…",
    cancel: "Cancel",
    confirmTitle: "Reclaim the selected space?",
    confirmBody: "Conversation Reclaim records every change in a manifest. It will not close apps or touch active conversations.",
    completeTitle: "Cleanup completed",
    partialTitle: "Partial cleanup",
    noChangesTitle: "No changes applied",
    completeBody: "All selected cleanup actions were completed.",
    partialBody: "Part of the cleanup completed, but some files were skipped to keep your data safe.",
    noChangesBody: "No files were changed. Review the details to see why actions were skipped.",
    estimate: "Estimated",
    actual: "Actually reclaimed",
    appliedActions: "actions applied",
    skippedActions: "actions skipped",
    scanNotes: "Scan notes",
    details: "View details",
    manifest: "Show manifest",
    close: "Done",
    safetyTitle: "Your conversations stay protected",
    safetyBody: "Cleanup preserves active conversations and automatically skips anything currently in use.",
    advanced: "Advanced cleanup",
    advancedBody: "Deep database pruning remains available in the CLI for technical users.",
    errorTitle: "Could not complete",
    demo: "Preview mode — the native engine connects in the desktop app.",
    activityTitle: "Activity",
    activityBody: "Review the latest scan and cleanup results from this session.",
    latestScan: "Latest scan",
    found: "Space found",
    lastCleanup: "Latest cleanup",
    noCleanup: "No cleanup has been completed in this session yet.",
    protectionTitle: "Protection",
    protectionBody: "These safeguards run automatically before any file is changed.",
    activeRule: "Files in use",
    activeRuleBody: "Active conversations and databases are skipped automatically.",
    manifestRule: "Verifiable record",
    manifestRuleBody: "Every change records its path, size, and outcome.",
    atomicRule: "Safe changes",
    atomicRuleBody: "Trims are prepared separately and replace the original only after completing successfully.",
    updates: "Updates",
    updateBody: "Conversation Reclaim securely checks GitHub and verifies the signature before installing.",
    checkUpdates: "Check for updates",
    checkingUpdates: "Checking…",
    upToDate: "You have the latest version",
    updateAvailable: "New version available",
    installUpdate: "Install and restart",
    installingUpdate: "Installing…",
    temporaryMedia: "Temporary media",
    images: "Conversation images",
    codexWork: "Codex work files",
    oldVersions: "Old versions and leftovers",
    ageLabel: "Only what hasn't changed in",
    ageUnit: "days",
    ageHelp: "Applies to images, work files, and leftovers. Scan again after changing it.",
    navOutputs: "Large files",
    outputsTitle: "Codex outputs",
    outputsBody: "What Codex delivered to you, biggest first. Nothing is deleted on its own: check what you no longer need and move it to the Trash.",
    findOutputs: "Find files",
    searching: "Searching…",
    outputsHint: "Looks in ~/Documents/Codex and checks whether an identical copy already exists in your folders. It may take a moment.",
    outputsEmpty: "No outputs of 1 MB or more.",
    outputsFound: "files",
    withCopyCount: "with a copy elsewhere",
    copyAt: "Copy at",
    newerVersion: "Newer version available",
    selectWithCopy: "Select the ones with a copy",
    clearSelection: "Clear selection",
    selectAll: "Select all",
    noCopy: "No copy elsewhere",
    tabOrphans: "Deleted chats",
    orphansTitle: "Folders of deleted chats",
    orphansBody: "Folders in ~/Documents/Codex whose conversation no longer exists in Codex, active or archived. Removing them is recommended: whole folders go to the Trash.",
    orphansHint: "Compares each folder with your Codex conversations. Only folders unchanged in the last 2 days count.",
    orphansEmpty: "No folders from deleted chats.",
    orphansFound: "folders",
    orphanBadge: "Deleted chat",
    selectOrphans: "Select those from deleted chats",
    filesLabel: "files",
    hasGit: "has a git repository",
    moveToTrash: "Move to Trash",
    moving: "Moving…",
    trashConfirmTitle: "Move to Trash?",
    trashConfirmBody: "The selected files go to the Trash. You can take them back until you empty it.",
    trashDone: "in the Trash",
    trashErrors: "could not be moved",
    tabOutputs: "Outputs",
    tabImages: "Generated images",
    imagesTitle: "Images generated by Codex",
    imagesBody: "Every image Codex generated. Recommended ones already have an identical copy in your folders, so you lose nothing by removing them. You decide.",
    imagesHint: "Looks in ~/.codex/generated_images and checks whether an identical copy already exists in your folders.",
    imagesEmpty: "Codex has no generated images.",
    imagesFound: "images",
    recommendedCount: "recommended",
    selectRecommended: "Select the recommended ones",
    recommendedBadge: "Recommended",
    preview: "Preview",
    openFile: "Open",
    revealMac: "Show in Finder",
    revealOther: "Show in folder",
    noPreview: "No preview for this file type. Open it with its app or show it in its folder.",
    markForTrash: "Mark for Trash",
    unmark: "Unmark",
    previous: "Previous",
    next: "Next",
  },
};

const demoScan: ScanResult = {
  totalReclaimable: 4_472_446_976,
  scannedAt: new Date().toISOString(),
  warnings: [],
  categories: [
    { key: "claude", name: "Claude Code", descriptionEs: "Historial compactado y subagentes cerrados", descriptionEn: "Compacted history and closed subagents", logo: "claude", bytes: 894_435_328, items: 61, recommended: true, protected: true, available: true, details: [] },
    { key: "codex", name: "Codex", descriptionEs: "Solo cachés regenerables; sesiones protegidas", descriptionEn: "Regenerable caches only; sessions protected", logo: "codex", bytes: 21_835_776, items: 4, recommended: true, protected: true, available: true, details: [] },
    { key: "opencode", name: "OpenCode", descriptionEs: "Archivos temporales, registros y snapshots", descriptionEn: "Temporary files, logs, and snapshots", logo: "opencode", bytes: 2_351_480_832, items: 418, recommended: false, protected: true, available: true, details: [] },
    { key: "antigravity", name: "Antigravity", descriptionEs: "Grabaciones del navegador y datos temporales", descriptionEn: "Browser recordings and temporary data", logo: "gemini", bytes: 884_695_040, items: 182, recommended: true, protected: true, available: true, details: [] },
    { key: "media", name: "Temporary media", descriptionEs: "Adjuntos temporales antiguos; las vistas previas pueden desaparecer", descriptionEn: "Old temporary attachments; previews may disappear", logo: "media", bytes: 487_587_840, items: 3603, recommended: false, protected: true, available: true, details: [] },
    { key: "images", name: "Conversation images", descriptionEs: "Capturas guardadas en chats viejos de Claude Code y Codex; los chats se conservan", descriptionEn: "Screenshots stored in old Claude Code and Codex chats; the chats are kept", logo: "images", bytes: 716_177_408, items: 106, recommended: false, protected: true, available: true, details: [] },
    { key: "codexWork", name: "Codex work files", descriptionEs: "Capturas y borradores en work/ de tareas viejas de Codex; outputs/ no se toca", descriptionEn: "Screenshots and drafts in work/ of old Codex tasks; outputs/ is never touched", logo: "codexWork", bytes: 4_509_715_456, items: 7263, recommended: false, protected: true, available: true, details: [] },
    { key: "oldVersions", name: "Old versions and leftovers", descriptionEs: "Versiones viejas de Claude Code y Codex y puntos de restauración; la que usas se queda", descriptionEn: "Old Claude Code and Codex versions and restore points; the one in use stays", logo: "versions", bytes: 1_288_490_189, items: 26, recommended: false, protected: true, available: true, details: [] },
    { key: "cursor", name: "Cursor", descriptionEs: "Beta: cachés regenerables del editor Cursor; tus chats no se tocan", descriptionEn: "Beta: regenerable Cursor editor caches; your chats are not touched", logo: "cursor", bytes: 612_368_384, items: 27, recommended: false, protected: true, available: true, details: [] },
    { key: "copilot", name: "GitHub Copilot (VS Code)", descriptionEs: "Beta: cachés regenerables de VS Code, donde vive Copilot Chat; tus chats no se tocan", descriptionEn: "Beta: regenerable VS Code caches where Copilot Chat lives; your chats are not touched", logo: "copilot", bytes: 415_236_096, items: 19, recommended: false, protected: true, available: true, details: [] },
    { key: "trae", name: "Trae", descriptionEs: "Beta: cachés regenerables de Trae; sesiones, credenciales y certificados no se tocan", descriptionEn: "Beta: regenerable Trae caches; sessions, credentials, and certificates are not touched", logo: "trae", bytes: 0, items: 0, recommended: false, protected: true, available: false, details: [] },
    { key: "zcode", name: "ZCode", descriptionEs: "Beta: cachés, registros y grabaciones de shell regenerables de ZCode; conversaciones, credenciales y certificados no se tocan", descriptionEn: "Beta: regenerable ZCode caches, logs, and shell recordings; conversations, credentials, and certificates are not touched", logo: "zcode", bytes: 0, items: 0, recommended: false, protected: true, available: false, details: [] },
    { key: "kiro", name: "Kiro", descriptionEs: "Beta: cachés regenerables del editor Kiro; el historial de chat no se toca", descriptionEn: "Beta: regenerable Kiro editor caches; chat history is not touched", logo: "kiro", bytes: 0, items: 0, recommended: false, protected: true, available: false, details: [] },
    { key: "windsurf", name: "Windsurf", descriptionEs: "Beta: cachés regenerables del editor Windsurf; tus chats no se tocan", descriptionEn: "Beta: regenerable Windsurf editor caches; your chats are not touched", logo: "windsurf", bytes: 0, items: 0, recommended: false, protected: true, available: false, details: [] },
  ],
};

function formatBytes(bytes: number) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** index;
  return `${value >= 10 || index === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
}

function isTauri() {
  return "__TAURI_INTERNALS__" in window;
}

const demoOrphans: OrphanFolder[] = [
  { path: "/demo/Documents/Codex/2026-10-08/pue", name: "2026-10-08/pue", bytes: 140_652_261, files: 101, modified: "2026-10-08", hasGit: false },
  { path: "/demo/Documents/Codex/2026-08-06/h", name: "2026-08-06/h", bytes: 116_380_274, files: 2880, modified: "2026-08-06", hasGit: true },
];

const demoOutputs: OutputItem[] = [
  { path: "/demo/Documents/Codex/2026-10-06/mira/outputs/Audio 19 - cruce.wav", name: "Audio 19 - cruce.wav", task: "2026-10-06/mira", bytes: 301_056_000, modified: "2026-10-06", copyPath: null, newerVersion: null },
  { path: "/demo/Documents/Codex/2026-10-06/mira/outputs/C0027 - color y ruido.mov", name: "C0027 - color y ruido.mov", task: "2026-10-06/mira", bytes: 200_015_872, modified: "2026-10-06", copyPath: "/demo/Movies/C0027 - color y ruido.mov", newerVersion: null },
  { path: "/demo/Documents/Codex/2026-09-02/q/outputs/Marketing_v1.zip", name: "Marketing_v1.zip", task: "2026-09-02/q", bytes: 147_324_928, modified: "2026-09-02", copyPath: null, newerVersion: "Marketing_v2.zip", orphan: true },
];

const demoTile = (hue: number) => `data:image/svg+xml,${encodeURIComponent(`<svg xmlns='http://www.w3.org/2000/svg' width='1024' height='1024'><rect width='1024' height='1024' fill='hsl(${hue} 55% 82%)'/><circle cx='512' cy='512' r='260' fill='hsl(${hue} 50% 60%)'/></svg>`)}`;

const demoImages: GeneratedImage[] = [
  { path: demoTile(210), name: "exec-logo.png", chat: "Logo VXM", bytes: 2_150_000, modified: "2026-10-13", copyPath: "/demo/Downloads/Manolito/logo.png" },
  { path: demoTile(30), name: "exec-sticker.png", chat: "Stickers", bytes: 1_840_000, modified: "2026-10-12", copyPath: "/demo/Downloads/Manolito/sticker.png" },
  { path: demoTile(140), name: "exec-draft.png", chat: "Ilustración", bytes: 1_920_000, modified: "2026-10-13", copyPath: null },
];

type PreviewKind = "image" | "video" | "audio" | null;

function previewKind(name: string): PreviewKind {
  const extension = name.split(".").pop()?.toLowerCase() ?? "";
  if (["png", "jpg", "jpeg", "webp", "gif", "heic", "bmp", "svg"].includes(extension)) return "image";
  if (["mp4", "mov", "m4v", "webm"].includes(extension)) return "video";
  if (["wav", "mp3", "m4a", "aac", "ogg", "flac"].includes(extension)) return "audio";
  return null;
}

const DEFAULT_AGE_DAYS = 14;

function savedAgeDays() {
  try {
    const saved = Number(localStorage.getItem("reclaim-older-than-days"));
    return Number.isInteger(saved) && saved >= 1 ? saved : DEFAULT_AGE_DAYS;
  } catch {
    return DEFAULT_AGE_DAYS;
  }
}

const translatedNames: Record<string, "temporaryMedia" | "images" | "codexWork" | "oldVersions"> = {
  media: "temporaryMedia",
  images: "images",
  codexWork: "codexWork",
  oldVersions: "oldVersions",
};

export default function App() {
  const [lang, setLang] = useState<Language>(() => (localStorage.getItem("reclaim-language") as Language) || "es");
  const [scan, setScan] = useState<ScanResult | null>(null);
  const [selected, setSelected] = useState<Set<CategoryKey>>(new Set());
  const [busy, setBusy] = useState<"scan" | "clean" | null>(null);
  const [modal, setModal] = useState<"confirm" | "success" | "error" | null>(null);
  const [result, setResult] = useState<ApplyResult | null>(null);
  const [error, setError] = useState("");
  const [page, setPage] = useState<Page>("overview");
  const [languageOpen, setLanguageOpen] = useState(false);
  const [updateStatus, setUpdateStatus] = useState<"idle" | "checking" | "current" | "available" | "installing" | "error">("idle");
  const [availableUpdate, setAvailableUpdate] = useState<UpdateInfo | null>(null);
  const [updateError, setUpdateError] = useState("");
  const [ageDays, setAgeDays] = useState(savedAgeDays);
  const [outputs, setOutputs] = useState<OutputItem[] | null>(null);
  const [largeTab, setLargeTab] = useState<LargeTab>("outputs");
  const [images, setImages] = useState<GeneratedImage[] | null>(null);
  const [orphans, setOrphans] = useState<OrphanFolder[] | null>(null);
  const [outputsBusy, setOutputsBusy] = useState<"search" | "trash" | null>(null);
  const [pickedOutputs, setPickedOutputs] = useState<Set<string>>(new Set());
  const [trashModal, setTrashModal] = useState<"confirm" | "done" | null>(null);
  const [trashResult, setTrashResult] = useState<TrashResult | null>(null);
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const t = copy[lang];
  const nameOf = (category: Category) => translatedNames[category.key] ? t[translatedNames[category.key]] : category.name;

  const changeAgeDays = (value: number) => {
    const days = Math.min(3650, Math.max(1, Math.round(value) || DEFAULT_AGE_DAYS));
    setAgeDays(days);
    try {
      localStorage.setItem("reclaim-older-than-days", String(days));
    } catch {
      // Sin almacenamiento local el valor solo dura esta sesión.
    }
  };
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform);

  const runScan = async () => {
    setBusy("scan");
    setError("");
    try {
      const data = isTauri() ? await invoke<ScanResult>("scan_storage", { olderThanDays: ageDays }) : demoScan;
      setScan(data);
      setSelected(new Set(data.categories.filter((item) => item.available && item.recommended && item.bytes > 0).map((item) => item.key)));
    } catch (cause) {
      setError(String(cause));
      setModal("error");
    } finally {
      setBusy(null);
    }
  };

  const checkUpdates = async (showResult = true) => {
    if (!isTauri()) return;
    setUpdateStatus("checking");
    setUpdateError("");
    try {
      const update = await invoke<UpdateInfo | null>("check_for_update");
      setAvailableUpdate(update);
      setUpdateStatus(update ? "available" : "current");
    } catch (cause) {
      if (showResult) {
        setUpdateError(String(cause));
        setUpdateStatus("error");
      } else {
        setUpdateStatus("idle");
      }
    }
  };

  const installAvailableUpdate = async () => {
    setUpdateStatus("installing");
    setUpdateError("");
    try {
      await invoke("install_update");
    } catch (cause) {
      setUpdateError(String(cause));
      setUpdateStatus("error");
    }
  };

  useEffect(() => {
    void runScan();
    if (isTauri()) window.setTimeout(() => void checkUpdates(false), 1200);
  }, []);

  const selectedCategories = useMemo(() => scan?.categories
    .filter((item) => selected.has(item.key)) ?? [], [scan, selected]);

  const selectedBytes = useMemo(() => selectedCategories
    .reduce((sum, item) => sum + item.bytes, 0), [selectedCategories]);

  const resultKind = result
    ? result.applied === 0
      ? "none"
      : result.skipped > 0
        ? "partial"
        : "complete"
    : "complete";

  const resultTitle = resultKind === "none" ? t.noChangesTitle : resultKind === "partial" ? t.partialTitle : t.completeTitle;
  const resultBody = resultKind === "none" ? t.noChangesBody : resultKind === "partial" ? t.partialBody : t.completeBody;

  const changeLanguage = (next: Language) => {
    setLang(next);
    localStorage.setItem("reclaim-language", next);
    document.documentElement.lang = next;
    setLanguageOpen(false);
  };

  const toggle = (key: CategoryKey) => {
    setSelected((current) => {
      const next = new Set(current);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  };

  const findOutputs = async (tab = largeTab) => {
    setOutputsBusy("search");
    try {
      let items: { path: string }[];
      if (tab === "outputs") {
        const found = isTauri() ? await invoke<OutputItem[]>("list_large_outputs") : demoOutputs;
        setOutputs(found);
        items = found;
      } else if (tab === "images") {
        const found = isTauri() ? await invoke<GeneratedImage[]>("list_generated_images") : demoImages;
        setImages(found);
        items = found;
      } else {
        const found = isTauri() ? await invoke<OrphanFolder[]>("list_orphan_folders") : demoOrphans;
        setOrphans(found);
        items = found;
      }
      setPickedOutputs((previous) => new Set([...previous].filter((path) => items.some((item) => item.path === path))));
    } catch (cause) {
      setError(String(cause));
      setModal("error");
    } finally {
      setOutputsBusy(null);
    }
  };

  const togglePicked = (path: string) => setPickedOutputs((previous) => {
    const next = new Set(previous);
    if (next.has(path)) next.delete(path); else next.add(path);
    return next;
  });

  const activeList: { path: string; bytes: number; copyPath: string | null }[] = largeTab === "orphans"
    ? (orphans ?? []).map((item) => ({ path: item.path, bytes: item.bytes, copyPath: null }))
    : (largeTab === "outputs" ? outputs : images) ?? [];
  const pickedBytes = activeList.filter((item) => pickedOutputs.has(item.path)).reduce((sum, item) => sum + item.bytes, 0);

  const fileSource = (path: string) => isTauri() && (path.startsWith("/") || /^[A-Za-z]:\\/.test(path)) ? convertFileSrc(path) : path;
  const revealLabel = isMac ? t.revealMac : t.revealOther;

  const runFileAction = async (command: "open_large_file" | "reveal_large_file", path: string) => {
    if (!isTauri()) return;
    try {
      await invoke(command, { path });
    } catch (cause) {
      setError(String(cause));
      setModal("error");
    }
  };

  const previewItems: { path: string; name: string; bytes: number; label: string }[] = largeTab === "outputs"
    ? (outputs ?? []).map((item) => ({ path: item.path, name: item.name, bytes: item.bytes, label: item.task }))
    : largeTab === "images" ? (images ?? []).map((item) => ({ path: item.path, name: item.name, bytes: item.bytes, label: item.chat })) : [];
  const previewIndex = previewItems.findIndex((item) => item.path === previewPath);
  const previewItem = previewIndex >= 0 ? previewItems[previewIndex] : null;
  const movePreview = (delta: number) => {
    if (previewIndex < 0 || !previewItems.length) return;
    setPreviewPath(previewItems[(previewIndex + delta + previewItems.length) % previewItems.length].path);
  };

  useEffect(() => {
    if (!previewPath) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPreviewPath(null);
      if (event.key === "ArrowRight") movePreview(1);
      if (event.key === "ArrowLeft") movePreview(-1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const switchLargeTab = (tab: LargeTab) => {
    if (tab === largeTab) return;
    setLargeTab(tab);
    setPickedOutputs(new Set());
    const loaded = tab === "outputs" ? outputs : tab === "images" ? images : orphans;
    if (loaded === null) void findOutputs(tab);
  };

  const moveOutputsToTrash = async () => {
    setTrashModal(null);
    setOutputsBusy("trash");
    try {
      const command = largeTab === "outputs" ? "trash_outputs" : largeTab === "images" ? "trash_generated_images" : "trash_orphan_folders";
      const response = await invoke<TrashResult>(command, { paths: [...pickedOutputs] });
      setTrashResult(response);
      setPickedOutputs(new Set());
      setTrashModal("done");
    } catch (cause) {
      setError(String(cause));
      setModal("error");
    } finally {
      setOutputsBusy(null);
    }
    await findOutputs();
  };

  const clean = async () => {
    setModal(null);
    setBusy("clean");
    try {
      const response = await invoke<ApplyResult>("apply_cleanup", { request: { categories: [...selected], olderThanDays: ageDays } });
      setResult(response);
      setModal("success");
      await runScan();
    } catch (cause) {
      setError(String(cause));
      setModal("error");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className={`app-shell ${isMac ? "mac" : ""}`}>
      <aside className="sidebar">
        <div className="brand" data-tauri-drag-region><span className="brand-mark"><Sparkles size={18} /></span><span>{t.product}</span></div>
        <nav aria-label="Primary">
          <button className={`nav-item ${page === "overview" ? "active" : ""}`} onClick={() => setPage("overview")}><HardDrive size={18} />{t.navOverview}</button>
          <button className={`nav-item ${page === "outputs" ? "active" : ""}`} onClick={() => setPage("outputs")}><FolderSearch size={18} />{t.navOutputs}</button>
          <button className={`nav-item ${page === "activity" ? "active" : ""}`} onClick={() => setPage("activity")}><FileClock size={18} />{t.navActivity}</button>
          <button className={`nav-item ${page === "protection" ? "active" : ""}`} onClick={() => setPage("protection")}><ShieldCheck size={18} />{t.navSafety}</button>
        </nav>
        <div className="sidebar-bottom">
          {updateStatus === "available" && availableUpdate && <button className="update-chip" onClick={() => setPage("protection")}><RefreshCw size={14} /><span>{t.updateAvailable}</span><strong>v{availableUpdate.version}</strong></button>}
          <div className="language-picker">
            <button className="language-trigger" onClick={() => setLanguageOpen((open) => !open)} aria-haspopup="listbox" aria-expanded={languageOpen}>
              <Languages size={16} /><span>{lang === "es" ? "Español" : "English"}</span><ChevronDown className={languageOpen ? "open" : ""} size={14} />
            </button>
            {languageOpen && <div className="language-menu" role="listbox" aria-label="Language">
              <button role="option" aria-selected={lang === "es"} onClick={() => changeLanguage("es")}>Español{lang === "es" && <Check size={14} />}</button>
              <button role="option" aria-selected={lang === "en"} onClick={() => changeLanguage("en")}>English{lang === "en" && <Check size={14} />}</button>
            </div>}
          </div>
        </div>
      </aside>

      <main>
        <div className="titlebar-drag" data-tauri-drag-region aria-hidden="true" />
        <header className="mobile-header">
          <div className="brand" data-tauri-drag-region><span className="brand-mark"><Sparkles size={17} /></span><span>{t.product}</span></div>
          <div className="mobile-actions">
            <button className={`mobile-nav ${page === "overview" ? "active" : ""}`} onClick={() => setPage("overview")} aria-label={t.navOverview}><HardDrive size={17} /></button>
            <button className={`mobile-nav ${page === "outputs" ? "active" : ""}`} onClick={() => setPage("outputs")} aria-label={t.navOutputs}><FolderSearch size={17} /></button>
            <button className={`mobile-nav ${page === "activity" ? "active" : ""}`} onClick={() => setPage("activity")} aria-label={t.navActivity}><FileClock size={17} /></button>
            <button className={`mobile-nav ${page === "protection" ? "active" : ""}`} onClick={() => setPage("protection")} aria-label={t.navSafety}><ShieldCheck size={17} /></button>
            <button className="language-compact" onClick={() => changeLanguage(lang === "es" ? "en" : "es")}><Globe2 size={17} />{lang.toUpperCase()}</button>
          </div>
        </header>

        {page === "overview" && <>
          <div className="content">
            <section className="hero">
              <div className="hero-copy"><span className="eyebrow"><Sparkles size={14} />{t.eyebrow}</span><h1>{t.title}</h1><p>{t.subtitle}</p></div>
              <div className="space-card">
                <div className="orb" aria-hidden="true"><div className="orb-core"><Trash2 size={28} /></div></div>
                <div><span>{t.ready}</span><strong>{scan ? formatBytes(scan.totalReclaimable) : "—"}</strong><small>{formatBytes(selectedBytes)} {t.selected}</small></div>
              </div>
            </section>

            {!isTauri() && <div className="demo-banner"><Info size={16} />{t.demo}</div>}

            <section className="section-heading">
              <div><h2>{t.choose}</h2><p>{t.chooseHelp}</p></div>
              <div className="heading-actions">
                <label className="age-control" title={t.ageHelp}>
                  <span>{t.ageLabel}</span>
                  <input type="number" min={1} max={3650} value={ageDays} onChange={(event) => changeAgeDays(Number(event.target.value))} disabled={busy !== null} />
                  <span>{t.ageUnit}</span>
                </label>
                <button className="secondary-button scan-button" onClick={runScan} disabled={busy !== null}><RefreshCw size={16} className={busy === "scan" ? "spin" : ""} />{busy === "scan" ? t.scanning : t.scanNow}</button>
              </div>
            </section>

            <section className="category-grid">
              {(scan?.categories ?? []).map((category) => {
                const description = lang === "es" ? category.descriptionEs : category.descriptionEn;
                const categoryName = nameOf(category);
                const checked = selected.has(category.key);
                const selectable = category.available && category.bytes > 0;
                return <button key={category.key} className={`category-card ${checked ? "selected" : ""}`} onClick={() => selectable && toggle(category.key)} disabled={!selectable} aria-pressed={checked}>
                  <div className="card-top"><span className={`tool-icon ${category.logo}`}><BrandLogo logo={category.logo} size={21} /></span><span className={`toggle ${checked ? "on" : ""}`}><span /></span></div>
                  <div className="card-title"><h3>{categoryName}</h3><span className={category.recommended ? "badge recommended" : "badge"}>{category.recommended ? t.recommended : t.optional}</span></div>
                  <p>{description}</p>
                  <div className="card-metric"><strong>{category.available ? formatBytes(category.bytes) : t.notDetected}</strong><span>{category.available ? `${category.items} ${t.items}` : ""}</span></div>
                  {category.protected && <div className="protected"><LockKeyhole size={13} />{t.protected}</div>}
                </button>;
              })}
              {busy === "scan" && !scan && [0, 1, 2, 3].map((key) => <div className="category-card skeleton" key={key} />)}
            </section>

            {scan?.warnings.length ? <section className="safety-card">
              <div className="safety-icon"><Info size={22} /></div>
              <div><h3>{t.scanNotes}</h3><p>{scan.warnings[0]}</p></div>
              {scan.warnings.length > 1 && <details><summary>{t.details}<CircleHelp size={15} /></summary>{scan.warnings.slice(1).map((warning, index) => <p key={`${index}-${warning}`}>{warning}</p>)}</details>}
            </section> : null}

            <section className="safety-card"><div className="safety-icon"><ShieldCheck size={22} /></div><div><h3>{t.safetyTitle}</h3><p>{t.safetyBody}</p></div><details><summary>{t.advanced}<CircleHelp size={15} /></summary><p>{t.advancedBody}</p></details></section>
          </div>

          <footer className="action-bar"><div><span>{selected.size} {t.items}</span><strong>{formatBytes(selectedBytes)}</strong></div><button className="primary-button" disabled={!selected.size || busy !== null || !isTauri()} onClick={() => setModal("confirm")}><Sparkles size={17} />{busy === "clean" ? t.cleaning : t.clean}</button></footer>
        </>}

        {page === "outputs" && <>
          <div className="content page-content">
            <section className="page-intro"><span className="eyebrow"><FolderSearch size={14} />{t.navOutputs}</span><h1>{largeTab === "outputs" ? t.outputsTitle : largeTab === "images" ? t.imagesTitle : t.orphansTitle}</h1><p>{largeTab === "outputs" ? t.outputsBody : largeTab === "images" ? t.imagesBody : t.orphansBody}</p></section>
            <div className="tab-switch" role="tablist">
              <button role="tab" aria-selected={largeTab === "outputs"} className={largeTab === "outputs" ? "active" : ""} onClick={() => switchLargeTab("outputs")} disabled={outputsBusy !== null}>{t.tabOutputs}</button>
              <button role="tab" aria-selected={largeTab === "images"} className={largeTab === "images" ? "active" : ""} onClick={() => switchLargeTab("images")} disabled={outputsBusy !== null}>{t.tabImages}</button>
              <button role="tab" aria-selected={largeTab === "orphans"} className={largeTab === "orphans" ? "active" : ""} onClick={() => switchLargeTab("orphans")} disabled={outputsBusy !== null}>{t.tabOrphans}</button>
            </div>
            <section className="section-heading">
              <div><p>{largeTab === "outputs"
                ? outputs ? `${outputs.length} ${t.outputsFound} · ${formatBytes(outputs.reduce((sum, item) => sum + item.bytes, 0))} · ${outputs.filter((item) => item.copyPath).length} ${t.withCopyCount}` : t.outputsHint
                : largeTab === "images"
                  ? images ? `${images.length} ${t.imagesFound} · ${formatBytes(images.reduce((sum, item) => sum + item.bytes, 0))} · ${images.filter((item) => item.copyPath).length} ${t.recommendedCount}` : t.imagesHint
                  : orphans ? `${orphans.length} ${t.orphansFound} · ${formatBytes(orphans.reduce((sum, item) => sum + item.bytes, 0))}` : t.orphansHint}</p></div>
              <div className="heading-actions">
                {activeList.length > 0 && <button className="secondary-button" onClick={() => setPickedOutputs(new Set(activeList.map((item) => item.path)))} disabled={outputsBusy !== null || pickedOutputs.size === activeList.length}>{t.selectAll}</button>}
                {activeList.length > 0 && largeTab !== "orphans" && <button className="secondary-button" onClick={() => setPickedOutputs(new Set(activeList.filter((item) => item.copyPath).map((item) => item.path)))} disabled={outputsBusy !== null || !activeList.some((item) => item.copyPath)}>{largeTab === "outputs" ? t.selectWithCopy : t.selectRecommended}</button>}
                {largeTab === "outputs" && outputs && outputs.some((item) => item.orphan) && <button className="secondary-button" onClick={() => setPickedOutputs(new Set(outputs.filter((item) => item.orphan).map((item) => item.path)))} disabled={outputsBusy !== null}>{t.selectOrphans}</button>}
                {pickedOutputs.size > 0 && <button className="secondary-button" onClick={() => setPickedOutputs(new Set())} disabled={outputsBusy !== null}>{t.clearSelection}</button>}
                <button className="secondary-button scan-button" onClick={() => void findOutputs()} disabled={outputsBusy !== null}><RefreshCw size={16} className={outputsBusy === "search" ? "spin" : ""} />{outputsBusy === "search" ? t.searching : t.findOutputs}</button>
              </div>
            </section>
            {largeTab === "orphans" && orphans && orphans.length === 0 && <p className="empty-note">{t.orphansEmpty}</p>}
            {largeTab === "orphans" && orphans && orphans.length > 0 && <ul className="output-list">
              {orphans.map((item) => {
                const picked = pickedOutputs.has(item.path);
                return <li key={item.path}>
                  <div className={`output-row ${picked ? "picked" : ""}`}>
                    <input type="checkbox" aria-label={item.name} checked={picked} onChange={() => togglePicked(item.path)} disabled={outputsBusy !== null} />
                    <strong className="output-size">{formatBytes(item.bytes)}</strong>
                    <button className="output-main" onClick={() => togglePicked(item.path)} title={item.path}><span className="output-name">{item.name}</span><small>{item.files} {t.filesLabel} · {item.modified}{item.hasGit ? ` · ${t.hasGit}` : ""}</small></button>
                    <span className="output-badges"><span className="output-badge orphan">{t.orphanBadge}</span></span>
                    <span className="row-actions">
                      <button className="icon-button" title={revealLabel} aria-label={revealLabel} onClick={() => { if (isTauri()) invoke("reveal_orphan_folder", { path: item.path }).catch((cause) => { setError(String(cause)); setModal("error"); }); }}><FolderOpen size={15} /></button>
                    </span>
                  </div>
                </li>;
              })}
            </ul>}
            {largeTab === "images" && images && images.length === 0 && <p className="empty-note">{t.imagesEmpty}</p>}
            {largeTab === "images" && images && images.length > 0 && <ul className="image-grid">
              {images.map((item) => {
                const picked = pickedOutputs.has(item.path);
                const source = isTauri() ? convertFileSrc(item.path) : item.path;
                return <li key={item.path}>
                  <div className={`image-card ${picked ? "picked" : ""}`} title={item.path}>
                    <input type="checkbox" aria-label={item.name} checked={picked} onChange={() => togglePicked(item.path)} disabled={outputsBusy !== null} />
                    <button className="image-thumb" onClick={() => setPreviewPath(item.path)} aria-label={`${t.preview}: ${item.name}`}><img src={source} alt={item.name} loading="lazy" /></button>
                    <span className="image-meta"><strong>{formatBytes(item.bytes)}</strong><small title={item.chat}>{item.modified} · {item.chat}</small></span>
                    {item.copyPath
                      ? <span className="output-badge copy" title={item.copyPath}>{t.recommendedBadge} · {t.copyAt} {item.copyPath.split(/[\\/]/).slice(-2).join("/")}</span>
                      : <span className="output-badge none">{t.noCopy}</span>}
                    <span className="row-actions">
                      <button className="icon-button" title={t.openFile} aria-label={t.openFile} onClick={() => void runFileAction("open_large_file", item.path)}><ExternalLink size={15} /></button>
                      <button className="icon-button" title={revealLabel} aria-label={revealLabel} onClick={() => void runFileAction("reveal_large_file", item.path)}><FolderOpen size={15} /></button>
                    </span>
                  </div>
                </li>;
              })}
            </ul>}
            {largeTab === "outputs" && outputs && outputs.length === 0 && <p className="empty-note">{t.outputsEmpty}</p>}
            {largeTab === "outputs" && outputs && outputs.length > 0 && <ul className="output-list">
              {outputs.map((item) => {
                const picked = pickedOutputs.has(item.path);
                return <li key={item.path}>
                  <div className={`output-row ${picked ? "picked" : ""}`}>
                    <input type="checkbox" aria-label={item.name} checked={picked} onChange={() => togglePicked(item.path)} disabled={outputsBusy !== null} />
                    <strong className="output-size">{formatBytes(item.bytes)}</strong>
                    <button className="output-main" onClick={() => setPreviewPath(item.path)} title={item.path}><span className="output-name">{item.name}</span><small>{item.task} · {item.modified}</small></button>
                    <span className="output-badges">
                      {item.copyPath && <span className="output-badge copy" title={item.copyPath}>{t.copyAt} {item.copyPath.split(/[\\/]/).slice(-2).join("/")}</span>}
                      {!item.copyPath && item.newerVersion && <span className="output-badge newer" title={item.newerVersion}>{t.newerVersion}: {item.newerVersion}</span>}
                      {item.orphan && <span className="output-badge orphan">{t.orphanBadge}</span>}
                      {!item.copyPath && !item.newerVersion && !item.orphan && <span className="output-badge none">{t.noCopy}</span>}
                    </span>
                    <span className="row-actions">
                      {previewKind(item.name) && <button className="icon-button" title={t.preview} aria-label={t.preview} onClick={() => setPreviewPath(item.path)}><Eye size={15} /></button>}
                      <button className="icon-button" title={t.openFile} aria-label={t.openFile} onClick={() => void runFileAction("open_large_file", item.path)}><ExternalLink size={15} /></button>
                      <button className="icon-button" title={revealLabel} aria-label={revealLabel} onClick={() => void runFileAction("reveal_large_file", item.path)}><FolderOpen size={15} /></button>
                    </span>
                  </div>
                </li>;
              })}
            </ul>}
          </div>
          <footer className="action-bar"><div><span>{pickedOutputs.size} {t.items}</span><strong>{formatBytes(pickedBytes)}</strong></div><button className="primary-button" disabled={!pickedOutputs.size || outputsBusy !== null || !isTauri()} onClick={() => setTrashModal("confirm")}><Trash2 size={17} />{outputsBusy === "trash" ? t.moving : t.moveToTrash}</button></footer>
        </>}

        {page === "activity" && <div className="content page-content">
          <section className="page-intro"><span className="eyebrow"><FileClock size={14} />{t.navActivity}</span><h1>{t.activityTitle}</h1><p>{t.activityBody}</p></section>
          <section className="status-grid">
            <article className="status-card"><div className="status-icon"><HardDrive size={20} /></div><span>{t.latestScan}</span><strong>{scan ? new Date(scan.scannedAt).toLocaleString(lang) : "—"}</strong><small>{t.found}: {formatBytes(scan?.totalReclaimable ?? 0)}</small></article>
            <article className="status-card"><div className="status-icon"><Check size={20} /></div><span>{t.lastCleanup}</span>{result ? <><strong>{formatBytes(result.freedBytes)}</strong><small>{result.applied} {t.appliedActions} · {result.skipped} {t.skippedActions}</small></> : <p>{t.noCleanup}</p>}</article>
          </section>
        </div>}

        {page === "protection" && <div className="content page-content">
          <section className="page-intro"><span className="eyebrow"><ShieldCheck size={14} />{t.navSafety}</span><h1>{t.protectionTitle}</h1><p>{t.protectionBody}</p></section>
          <section className="protection-grid">
            <article className="protection-card"><span><LockKeyhole size={21} /></span><h2>{t.activeRule}</h2><p>{t.activeRuleBody}</p></article>
            <article className="protection-card"><span><FileClock size={21} /></span><h2>{t.manifestRule}</h2><p>{t.manifestRuleBody}</p></article>
            <article className="protection-card"><span><ShieldCheck size={21} /></span><h2>{t.atomicRule}</h2><p>{t.atomicRuleBody}</p></article>
          </section>
          <section className="safety-card"><div className="safety-icon"><ShieldCheck size={22} /></div><div><h3>{t.safetyTitle}</h3><p>{t.safetyBody}</p></div><details><summary>{t.advanced}<CircleHelp size={15} /></summary><p>{t.advancedBody}</p></details></section>
          <section className="update-card">
            <div className="update-copy"><span><RefreshCw size={20} /></span><div><h2>{t.updates}</h2><p>{t.updateBody}</p>{availableUpdate && <small>v{availableUpdate.currentVersion} → v{availableUpdate.version}</small>}{updateStatus === "current" && <small className="success-text"><Check size={13} />{t.upToDate}</small>}{updateStatus === "error" && <small className="error-text">{updateError}</small>}</div></div>
            {updateStatus === "available" ? <button className="primary-button" onClick={installAvailableUpdate}><RefreshCw size={16} />{t.installUpdate}</button> : <button className="secondary-button" onClick={() => void checkUpdates(true)} disabled={updateStatus === "checking" || updateStatus === "installing"}><RefreshCw className={updateStatus === "checking" ? "spin" : ""} size={16} />{updateStatus === "checking" ? t.checkingUpdates : updateStatus === "installing" ? t.installingUpdate : t.checkUpdates}</button>}
          </section>
        </div>}
      </main>

      {previewItem && <div className="modal-backdrop" role="presentation" onMouseDown={() => setPreviewPath(null)}><section className="modal preview-modal" role="dialog" aria-modal="true" aria-label={previewItem.name} onMouseDown={(event) => event.stopPropagation()}>
        <div className="preview-media">
          {previewKind(previewItem.name) === "image" && <img src={fileSource(previewItem.path)} alt={previewItem.name} />}
          {previewKind(previewItem.name) === "video" && <video key={previewItem.path} src={fileSource(previewItem.path)} controls autoPlay />}
          {previewKind(previewItem.name) === "audio" && <audio key={previewItem.path} src={fileSource(previewItem.path)} controls autoPlay />}
          {!previewKind(previewItem.name) && <p className="empty-note">{t.noPreview}</p>}
          {previewItems.length > 1 && <>
            <button className="preview-nav previous" onClick={() => movePreview(-1)} aria-label={t.previous}><ChevronLeft size={20} /></button>
            <button className="preview-nav next" onClick={() => movePreview(1)} aria-label={t.next}><ChevronRight size={20} /></button>
          </>}
        </div>
        <h2 className="preview-title" title={previewItem.path}>{previewItem.name}</h2>
        <p>{formatBytes(previewItem.bytes)} · {previewItem.label} · {previewIndex + 1}/{previewItems.length}</p>
        <div className="modal-actions preview-actions">
          <button className={pickedOutputs.has(previewItem.path) ? "secondary-button marked" : "secondary-button"} onClick={() => togglePicked(previewItem.path)} disabled={outputsBusy !== null}><Trash2 size={15} />{pickedOutputs.has(previewItem.path) ? t.unmark : t.markForTrash}</button>
          <button className="secondary-button" onClick={() => void runFileAction("open_large_file", previewItem.path)} disabled={!isTauri()}><ExternalLink size={15} />{t.openFile}</button>
          <button className="secondary-button" onClick={() => void runFileAction("reveal_large_file", previewItem.path)} disabled={!isTauri()}><FolderOpen size={15} />{revealLabel}</button>
          <button className="primary-button" onClick={() => setPreviewPath(null)}>{t.close}</button>
        </div>
      </section></div>}

      {trashModal && <div className="modal-backdrop" role="presentation" onMouseDown={() => setTrashModal(null)}><section className="modal" role="dialog" aria-modal="true" onMouseDown={(event) => event.stopPropagation()}>
        <span className={`modal-icon ${trashModal === "done" ? "success" : "confirm"}`}>{trashModal === "done" ? <Check size={28} /> : <Trash2 size={27} />}</span>
        <h2>{trashModal === "confirm" ? t.trashConfirmTitle : `${trashResult?.moved ?? 0} ${t.outputsFound} ${t.trashDone}`}</h2>
        <p>{trashModal === "confirm" ? t.trashConfirmBody : `${formatBytes(trashResult?.freedBytes ?? 0)}${trashResult?.errors.length ? ` · ${trashResult.errors.length} ${t.trashErrors}` : ""}`}</p>
        {trashModal === "confirm" && <div className="confirm-total"><span>{pickedOutputs.size} {t.items}</span><strong>{formatBytes(pickedBytes)}</strong></div>}
        <div className="modal-actions">
          {trashModal === "confirm" ? <><button className="secondary-button" onClick={() => setTrashModal(null)}>{t.cancel}</button><button className="primary-button" onClick={moveOutputsToTrash}><Trash2 size={16} />{t.moveToTrash}</button></> : <button className="primary-button" onClick={() => setTrashModal(null)}>{t.close}</button>}
        </div>
      </section></div>}

      {modal && <div className="modal-backdrop" role="presentation" onMouseDown={() => busy === null && setModal(null)}><section className="modal" role="dialog" aria-modal="true" onMouseDown={(event) => event.stopPropagation()}>
        <span className={`modal-icon ${modal}`}>{modal === "success" ? resultKind === "complete" ? <Check size={28} /> : <Info size={28} /> : modal === "error" ? <Info size={28} /> : <Trash2 size={27} />}</span>
        <h2>{modal === "confirm" ? t.confirmTitle : modal === "success" ? resultTitle : t.errorTitle}</h2>
        <p>{modal === "confirm" ? t.confirmBody : modal === "success" ? resultBody : error}</p>
        {modal === "confirm" && <>
          <div className="confirm-total"><span>{t.estimate}</span><strong>{formatBytes(selectedBytes)}</strong></div>
          {selectedCategories.length > 0 && <details><summary>{t.review}<CircleHelp size={15} /></summary>{selectedCategories.map((category) => <p key={category.key}>{nameOf(category)}:{formatBytes(category.bytes)} · {category.items} {t.items}</p>)}</details>}
        </>}
        {modal === "success" && result && <>
          <div className="confirm-total"><span>{t.estimate}</span><strong>{formatBytes(result.plannedBytes)}</strong></div>
          <div className="confirm-total"><span>{t.actual}</span><strong>{formatBytes(result.freedBytes)}</strong></div>
          <p>{result.applied} {t.appliedActions} · {result.skipped} {t.skippedActions}</p>
          {result.warnings.length > 0 && <details><summary>{t.details}<CircleHelp size={15} /></summary>{result.warnings.map((warning, index) => <p key={`${index}-${warning}`}>{warning}</p>)}</details>}
        </>}
        <div className="modal-actions">
          {modal === "confirm" ? <><button className="secondary-button" onClick={() => setModal(null)}>{t.cancel}</button><button className="primary-button" onClick={clean}>{t.clean}</button></> : <>
            {modal === "success" && result?.manifestPath && <button className="secondary-button" onClick={() => invoke("reveal_manifest", { path: result.manifestPath })}><ExternalLink size={15} />{t.manifest}</button>}
            <button className="primary-button" onClick={() => setModal(null)}>{t.close}</button>
          </>}
        </div>
      </section></div>}
      {busy === "clean" && <div className="working"><LoaderCircle className="spin" /><span>{t.cleaning}</span></div>}
    </div>
  );
}
