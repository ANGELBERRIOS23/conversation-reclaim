import { useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  Check, ChevronDown, CircleHelp, ExternalLink,
  FileClock, Globe2, HardDrive, Info, Languages,
  LoaderCircle, LockKeyhole, RefreshCw, ShieldCheck, Sparkles, Trash2,
} from "lucide-react";
import { BrandLogo } from "./BrandLogos";

type Language = "es" | "en";
type CategoryKey = string;
type Page = "overview" | "activity" | "protection";

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
  manifestPath: string;
  applied: number;
  skipped: number;
  warnings: string[];
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
    review: "Revisar selección",
    clean: "Liberar espacio",
    cleaning: "Liberando…",
    cancel: "Cancelar",
    confirmTitle: "¿Liberar el espacio seleccionado?",
    confirmBody: "Conversation Reclaim registrará cada cambio en un manifiesto. No cerrará aplicaciones ni tocará conversaciones activas.",
    confirmed: "Espacio liberado",
    resultBody: "La limpieza terminó correctamente.",
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
    review: "Review selection",
    clean: "Reclaim space",
    cleaning: "Reclaiming…",
    cancel: "Cancel",
    confirmTitle: "Reclaim the selected space?",
    confirmBody: "Conversation Reclaim records every change in a manifest. It will not close apps or touch active conversations.",
    confirmed: "Space reclaimed",
    resultBody: "Cleanup completed successfully.",
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
  },
};

const demoScan: ScanResult = {
  totalReclaimable: 4_472_446_976,
  scannedAt: new Date().toISOString(),
  warnings: [],
  categories: [
    { key: "claude", name: "Claude Code", descriptionEs: "Historial compactado y subagentes cerrados", descriptionEn: "Compacted history and closed subagents", logo: "claude", bytes: 894_435_328, items: 61, recommended: true, protected: true, available: true, details: [] },
    { key: "codex", name: "Codex", descriptionEs: "Cachés y sesiones compactadas inactivas", descriptionEn: "Caches and inactive compacted sessions", logo: "codex", bytes: 341_835_776, items: 34, recommended: true, protected: true, available: true, details: [] },
    { key: "opencode", name: "OpenCode", descriptionEs: "Archivos temporales, registros y snapshots", descriptionEn: "Temporary files, logs, and snapshots", logo: "opencode", bytes: 2_351_480_832, items: 418, recommended: false, protected: true, available: true, details: [] },
    { key: "antigravity", name: "Antigravity", descriptionEs: "Grabaciones del navegador y datos temporales", descriptionEn: "Browser recordings and temporary data", logo: "gemini", bytes: 884_695_040, items: 182, recommended: true, protected: true, available: true, details: [] },
    { key: "media", name: "Temporary media", descriptionEs: "Adjuntos temporales antiguos; las vistas previas pueden desaparecer", descriptionEn: "Old temporary attachments; previews may disappear", logo: "media", bytes: 487_587_840, items: 3603, recommended: false, protected: true, available: true, details: [] },
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
  const t = copy[lang];
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform);

  const runScan = async () => {
    setBusy("scan");
    setError("");
    try {
      const data = isTauri() ? await invoke<ScanResult>("scan_storage") : demoScan;
      setScan(data);
      setSelected(new Set(data.categories.filter((item) => item.recommended && item.bytes > 0).map((item) => item.key)));
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

  const selectedBytes = useMemo(() => scan?.categories
    .filter((item) => selected.has(item.key))
    .reduce((sum, item) => sum + item.bytes, 0) ?? 0, [scan, selected]);

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

  const clean = async () => {
    setModal(null);
    setBusy("clean");
    try {
      const response = await invoke<ApplyResult>("apply_cleanup", { request: { categories: [...selected] } });
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
        <div className="brand"><span className="brand-mark"><Sparkles size={18} /></span><span>{t.product}</span></div>
        <nav aria-label="Primary">
          <button className={`nav-item ${page === "overview" ? "active" : ""}`} onClick={() => setPage("overview")}><HardDrive size={18} />{t.navOverview}</button>
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
        <header className="mobile-header">
          <div className="brand"><span className="brand-mark"><Sparkles size={17} /></span><span>{t.product}</span></div>
          <div className="mobile-actions">
            <button className={`mobile-nav ${page === "overview" ? "active" : ""}`} onClick={() => setPage("overview")} aria-label={t.navOverview}><HardDrive size={17} /></button>
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
              <button className="secondary-button scan-button" onClick={runScan} disabled={busy !== null}><RefreshCw size={16} className={busy === "scan" ? "spin" : ""} />{busy === "scan" ? t.scanning : t.scanNow}</button>
            </section>

            <section className="category-grid">
              {(scan?.categories ?? []).map((category) => {
                const description = lang === "es" ? category.descriptionEs : category.descriptionEn;
                const categoryName = category.key === "media" ? t.temporaryMedia : category.name;
                const checked = selected.has(category.key);
                return <button key={category.key} className={`category-card ${checked ? "selected" : ""}`} onClick={() => category.available && toggle(category.key)} disabled={!category.available} aria-pressed={checked}>
                  <div className="card-top"><span className={`tool-icon ${category.logo}`}><BrandLogo logo={category.logo} size={21} /></span><span className={`toggle ${checked ? "on" : ""}`}><span /></span></div>
                  <div className="card-title"><h3>{categoryName}</h3><span className={category.recommended ? "badge recommended" : "badge"}>{category.recommended ? t.recommended : t.optional}</span></div>
                  <p>{description}</p>
                  <div className="card-metric"><strong>{category.available ? formatBytes(category.bytes) : t.noData}</strong><span>{category.items} {t.items}</span></div>
                  {category.protected && <div className="protected"><LockKeyhole size={13} />{t.protected}</div>}
                </button>;
              })}
              {busy === "scan" && !scan && [0, 1, 2, 3].map((key) => <div className="category-card skeleton" key={key} />)}
            </section>

            <section className="safety-card"><div className="safety-icon"><ShieldCheck size={22} /></div><div><h3>{t.safetyTitle}</h3><p>{t.safetyBody}</p></div><details><summary>{t.advanced}<CircleHelp size={15} /></summary><p>{t.advancedBody}</p></details></section>
          </div>

          <footer className="action-bar"><div><span>{selected.size} {t.items}</span><strong>{formatBytes(selectedBytes)}</strong></div><button className="primary-button" disabled={!selected.size || busy !== null || !isTauri()} onClick={() => setModal("confirm")}><Sparkles size={17} />{busy === "clean" ? t.cleaning : t.clean}</button></footer>
        </>}

        {page === "activity" && <div className="content page-content">
          <section className="page-intro"><span className="eyebrow"><FileClock size={14} />{t.navActivity}</span><h1>{t.activityTitle}</h1><p>{t.activityBody}</p></section>
          <section className="status-grid">
            <article className="status-card"><div className="status-icon"><HardDrive size={20} /></div><span>{t.latestScan}</span><strong>{scan ? new Date(scan.scannedAt).toLocaleString(lang) : "—"}</strong><small>{t.found}: {formatBytes(scan?.totalReclaimable ?? 0)}</small></article>
            <article className="status-card"><div className="status-icon"><Check size={20} /></div><span>{t.lastCleanup}</span>{result ? <><strong>{formatBytes(result.freedBytes)}</strong><small>{result.applied} {t.items}</small></> : <p>{t.noCleanup}</p>}</article>
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

      {modal && <div className="modal-backdrop" role="presentation" onMouseDown={() => busy === null && setModal(null)}><section className="modal" role="dialog" aria-modal="true" onMouseDown={(event) => event.stopPropagation()}>
        <span className={`modal-icon ${modal}`}>{modal === "success" ? <Check size={28} /> : modal === "error" ? <Info size={28} /> : <Trash2 size={27} />}</span>
        <h2>{modal === "confirm" ? t.confirmTitle : modal === "success" ? t.confirmed : t.errorTitle}</h2>
        <p>{modal === "confirm" ? t.confirmBody : modal === "success" ? `${t.resultBody} ${formatBytes(result?.freedBytes ?? 0)}` : error}</p>
        {modal === "confirm" && <div className="confirm-total"><span>{t.ready}</span><strong>{formatBytes(selectedBytes)}</strong></div>}
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
