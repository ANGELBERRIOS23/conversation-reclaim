use crate::harnesses::registered_harnesses;
use chrono::Local;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashSet;
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, BufWriter, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime};
use walkdir::WalkDir;

pub(crate) const DEFAULT_MIN_AGE_DAYS: u64 = 14;
static MIN_AGE_DAYS: AtomicU64 = AtomicU64::new(DEFAULT_MIN_AGE_DAYS);

/// Fija cuántos días sin cambios necesita un archivo para entrar en las
/// limpiezas que dependen de la edad (imágenes, trabajo de Codex, versiones).
fn set_min_age_days(days: Option<u64>) {
    let days = days.unwrap_or(DEFAULT_MIN_AGE_DAYS).clamp(1, 3650);
    MIN_AGE_DAYS.store(days, Ordering::Relaxed);
}

pub(crate) fn min_age_days() -> u64 {
    MIN_AGE_DAYS.load(Ordering::Relaxed)
}

/// Lo modificado antes de este momento es lo bastante viejo para limpiarse.
pub(crate) fn age_cutoff() -> SystemTime {
    SystemTime::now() - Duration::from_secs(min_age_days() * 86_400)
}

pub(crate) fn is_older_than_cutoff(path: &Path) -> bool {
    fs::symlink_metadata(path)
        .and_then(|m| m.modified())
        .map(|modified| modified < age_cutoff())
        .unwrap_or(false)
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Category {
    key: String,
    name: String,
    description_es: String,
    description_en: String,
    logo: String,
    bytes: u64,
    items: u64,
    recommended: bool,
    protected: bool,
    available: bool,
    details: Vec<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ScanResult {
    categories: Vec<Category>,
    total_reclaimable: u64,
    scanned_at: String,
    warnings: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ApplyRequest {
    categories: Vec<String>,
    #[serde(default)]
    older_than_days: Option<u64>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ApplyResult {
    freed_bytes: u64,
    planned_bytes: u64,
    skipped_bytes: u64,
    manifest_path: String,
    applied: usize,
    skipped: usize,
    warnings: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Action {
    category: String,
    action: String,
    path: PathBuf,
    planned_bytes: u64,
    marker_offset: Option<u64>,
    reason: String,
}

impl Action {
    pub(crate) fn delete(category: &str, path: PathBuf, bytes: u64, reason: &str) -> Self {
        Self {
            category: category.into(),
            action: "delete_file".into(),
            path,
            planned_bytes: bytes,
            marker_offset: None,
            reason: reason.into(),
        }
    }

    /// Reescribe una sesión sin sus imágenes; la conversación se conserva.
    pub(crate) fn strip_images(category: &str, path: PathBuf, bytes: u64, reason: &str) -> Self {
        Self {
            action: "strip_images".into(),
            ..Self::delete(category, path, bytes, reason)
        }
    }

    /// Borra una carpeta completa (versiones viejas, puntos de restauración).
    pub(crate) fn delete_tree(category: &str, path: PathBuf, reason: &str) -> Self {
        let bytes = collect_files(&path).iter().map(|p| file_size(p)).sum();
        Self {
            action: "delete_tree".into(),
            ..Self::delete(category, path, bytes, reason)
        }
    }
}

fn home() -> Result<PathBuf, String> {
    dirs::home_dir().ok_or_else(|| "Could not determine the user home folder".to_string())
}

pub(crate) fn file_size(path: &Path) -> u64 {
    fs::symlink_metadata(path)
        .ok()
        .filter(|m| m.file_type().is_file())
        .map(|m| m.len())
        .unwrap_or(0)
}

pub(crate) fn collect_files(root: &Path) -> Vec<PathBuf> {
    if !root.exists() {
        return Vec::new();
    }
    WalkDir::new(root)
        .follow_links(false)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_file() && !entry.file_type().is_symlink())
        .map(|entry| entry.into_path())
        .collect()
}

pub(crate) fn file_is_active(path: &Path) -> bool {
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::fs::OpenOptionsExt;
        return OpenOptions::new()
            .read(true)
            .write(true)
            .share_mode(0)
            .open(path)
            .is_err();
    }
    #[cfg(not(target_os = "windows"))]
    {
        let executable = if cfg!(target_os = "macos") {
            "/usr/sbin/lsof"
        } else {
            "/usr/bin/lsof"
        };
        match Command::new(executable)
            .arg("-t")
            .arg("--")
            .arg(path)
            .output()
        {
            Ok(output) if output.status.success() => !output.stdout.is_empty(),
            Ok(output) if output.status.code() == Some(1) => false,
            _ => true,
        }
    }
}

fn marker_offset(path: &Path, predicate: fn(&Value) -> bool) -> Result<Option<u64>, String> {
    let metadata = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
        return Err("not a regular file".into());
    }
    let reader = BufReader::new(File::open(path).map_err(|e| e.to_string())?);
    let mut offset = 0u64;
    let mut last = None;
    for (index, line) in reader.split(b'\n').enumerate() {
        let mut raw = line.map_err(|e| e.to_string())?;
        if raw.is_empty() {
            offset += 1;
            continue;
        }
        if raw.last() == Some(&b'\r') {
            raw.pop();
        }
        let record: Value = serde_json::from_slice(&raw)
            .map_err(|_| format!("invalid JSON at line {}", index + 1))?;
        if predicate(&record) {
            last = Some(offset);
        }
        offset += raw.len() as u64 + 1;
    }
    Ok(last)
}

fn is_codex_subagent(path: &Path) -> bool {
    let Ok(file) = File::open(path) else {
        return true;
    };
    for line in BufReader::new(file).lines().take(96).flatten() {
        let Ok(record) = serde_json::from_str::<Value>(&line) else {
            return true;
        };
        if record.get("type").and_then(Value::as_str) != Some("session_meta") {
            continue;
        }
        let Some(payload) = record.get("payload") else {
            continue;
        };
        if payload.get("thread_source").and_then(Value::as_str) == Some("subagent") {
            return true;
        }
    }
    false
}

pub(crate) fn claude_sidechain_valid(path: &Path) -> bool {
    if path
        .parent()
        .and_then(Path::file_name)
        .and_then(|s| s.to_str())
        != Some("subagents")
    {
        return false;
    }
    let Some(name) = path
        .file_stem()
        .and_then(|s| s.to_str())
        .and_then(|s| s.strip_prefix("agent-"))
    else {
        return false;
    };
    let Some(session) = path
        .parent()
        .and_then(Path::parent)
        .and_then(Path::file_name)
        .and_then(|s| s.to_str())
    else {
        return false;
    };
    let Ok(file) = File::open(path) else {
        return false;
    };
    let mut found = false;
    for line in BufReader::new(file).lines() {
        let Ok(line) = line else {
            return false;
        };
        let Ok(record) = serde_json::from_str::<Value>(&line) else {
            return false;
        };
        if record.get("isSidechain").and_then(Value::as_bool) == Some(true) {
            if record.get("agentId").and_then(Value::as_str) != Some(name)
                || record.get("sessionId").and_then(Value::as_str) != Some(session)
            {
                return false;
            }
            found = true;
        }
    }
    found
}

/// Claude Code abre su .jsonl, agrega una línea y lo cierra, así que lsof no ve
/// una conversación abierta. Se protegen las de procesos vivos y, por si acaso,
/// las que cambiaron en los últimos minutos (igual que reclaim.py).
pub(crate) const CLAUDE_RECENT_MINUTES: u64 = 60;

fn is_session_id(text: &str) -> bool {
    let bytes = text.as_bytes();
    bytes.len() == 36
        && bytes.iter().enumerate().all(|(index, byte)| match index {
            8 | 13 | 18 | 23 => *byte == b'-',
            _ => byte.is_ascii_digit() || (b'a'..=b'f').contains(byte),
        })
}

/// Procesos vivos con su línea de comando (vacía en Windows). `None` si no se pudo listar.
fn live_processes() -> Option<Vec<(u32, String)>> {
    #[cfg(target_os = "windows")]
    {
        let output = Command::new("tasklist")
            .args(["/FO", "CSV", "/NH"])
            .output()
            .ok()?;
        if !output.status.success() {
            return None;
        }
        return Some(
            String::from_utf8_lossy(&output.stdout)
                .lines()
                .filter_map(|line| line.split("\",\"").nth(1))
                .filter_map(|pid| pid.trim_matches('"').parse().ok())
                .map(|pid| (pid, String::new()))
                .collect(),
        );
    }
    #[cfg(not(target_os = "windows"))]
    {
        let output = Command::new("ps")
            .args(["axww", "-o", "pid=,args="])
            .output()
            .ok()?;
        if !output.status.success() {
            return None;
        }
        Some(
            String::from_utf8_lossy(&output.stdout)
                .lines()
                .filter_map(|line| {
                    let line = line.trim_start();
                    let (pid, args) = line.split_once(char::is_whitespace).unwrap_or((line, ""));
                    pid.parse().ok().map(|pid| (pid, args.to_string()))
                })
                .collect(),
        )
    }
}

fn collect_resume_ids(args: &str, ids: &mut HashSet<String>) {
    let words: Vec<&str> = args.split_whitespace().collect();
    for (index, word) in words.iter().enumerate() {
        for flag in ["--resume", "--session-id", "-r"] {
            if *word == flag {
                if let Some(next) = words.get(index + 1) {
                    if is_session_id(next) {
                        ids.insert((*next).to_string());
                    }
                }
            } else if let Some(value) = word.strip_prefix(flag).and_then(|rest| rest.strip_prefix('=')) {
                if is_session_id(value) {
                    ids.insert(value.to_string());
                }
            }
        }
    }
}

/// Ids de las conversaciones de Claude Code abiertas ahora: `~/.claude/sessions/<pid>.json`
/// con el proceso vivo y `--resume`/`--session-id` de los procesos. `None` si no se pudo saber.
pub(crate) fn claude_open_session_ids(home: &Path) -> Option<HashSet<String>> {
    let sessions_dir = home.join(".claude").join("sessions");
    let processes = live_processes();
    if processes.is_none() && !sessions_dir.is_dir() {
        return None;
    }
    let alive: Option<HashSet<u32>> = processes
        .as_ref()
        .map(|list| list.iter().map(|(pid, _)| *pid).collect());
    let mut ids = HashSet::new();
    if let Ok(entries) = fs::read_dir(&sessions_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|s| s.to_str()) != Some("json") {
                continue;
            }
            let Ok(text) = fs::read_to_string(&path) else {
                continue;
            };
            let Ok(info) = serde_json::from_str::<Value>(&text) else {
                continue;
            };
            let Some(session) = info.get("sessionId").and_then(Value::as_str) else {
                continue;
            };
            if !is_session_id(session) {
                continue;
            }
            let pid = info
                .get("pid")
                .and_then(Value::as_u64)
                .and_then(|pid| u32::try_from(pid).ok())
                .or_else(|| {
                    path.file_stem()
                        .and_then(|s| s.to_str())
                        .and_then(|s| s.parse().ok())
                });
            // Sin lista de procesos no se puede descartar ninguno: se protege.
            let running = match (&alive, pid) {
                (Some(set), Some(pid)) => set.contains(&pid),
                _ => true,
            };
            if running {
                ids.insert(session.to_string());
            }
        }
    }
    for (_, args) in processes.unwrap_or_default() {
        collect_resume_ids(&args, &mut ids);
    }
    Some(ids)
}

/// Motivo para no tocar este archivo de `~/.claude/projects`, o `None`.
pub(crate) fn claude_protection(
    path: &Path,
    projects: &Path,
    open: Option<&HashSet<String>>,
    cutoff: Option<SystemTime>,
) -> Option<&'static str> {
    let rel = path.strip_prefix(projects).ok()?;
    let parts: Vec<String> = rel
        .components()
        .map(|part| part.as_os_str().to_string_lossy().into_owned())
        .collect();
    let ids: Vec<String> = parts
        .iter()
        .skip(1)
        .map(|part| part.strip_suffix(".jsonl").unwrap_or(part).to_string())
        .filter(|part| is_session_id(part))
        .collect();
    if let Some(open) = open {
        if ids.iter().any(|id| open.contains(id)) {
            return Some("open conversation");
        }
    }
    let cutoff = cutoff?;
    let mut candidates = vec![path.to_path_buf()];
    if let Some(project) = parts.first() {
        candidates.extend(ids.iter().map(|id| projects.join(project).join(format!("{id}.jsonl"))));
    }
    let recent = candidates.iter().any(|candidate| {
        fs::metadata(candidate)
            .and_then(|m| m.modified())
            .map(|modified| modified >= cutoff)
            .unwrap_or(false)
    });
    recent.then_some("changed recently")
}

/// Saca de `actions` (de esa categoría) lo que toca conversaciones de Claude abiertas
/// y, con `recent`, las que cambiaron en los últimos `CLAUDE_RECENT_MINUTES` minutos.
pub(crate) fn retain_unprotected_claude(
    actions: &mut Vec<Action>,
    home: &Path,
    category: &str,
    recent: bool,
    warnings: &mut Vec<String>,
) {
    let projects = home.join(".claude").join("projects");
    let open = claude_open_session_ids(home);
    if open.is_none() {
        warnings.push(
            "Claude Code: could not tell which conversations are open; only recently changed ones are protected"
                .into(),
        );
    }
    let cutoff = recent.then(|| SystemTime::now() - Duration::from_secs(CLAUDE_RECENT_MINUTES * 60));
    let mut skipped = 0usize;
    actions.retain(|action| {
        if action.category != category
            || claude_protection(&action.path, &projects, open.as_ref(), cutoff).is_none()
        {
            return true;
        }
        skipped += 1;
        false
    });
    if skipped > 0 {
        warnings.push(format!(
            "Claude Code: {skipped} file(s) of open or recently changed conversations were left untouched"
        ));
    }
}

pub(crate) fn add_trim_actions(
    actions: &mut Vec<Action>,
    root: &Path,
    category: &str,
    predicate: fn(&Value) -> bool,
    skip_subagents: bool,
) {
    for path in collect_files(root)
        .into_iter()
        .filter(|p| p.extension().and_then(|s| s.to_str()) == Some("jsonl"))
    {
        if category == "claude"
            && path
                .components()
                .any(|part| part.as_os_str() == "subagents")
        {
            continue;
        }
        if skip_subagents && is_codex_subagent(&path) {
            continue;
        }
        let size = file_size(&path);
        if let Ok(Some(offset)) = marker_offset(&path, predicate) {
            if offset > 0 && offset < size {
                actions.push(Action {
                    category: category.into(),
                    action: "truncate".into(),
                    path,
                    planned_bytes: offset,
                    marker_offset: Some(offset),
                    reason: "history before last structural compaction marker".into(),
                });
            }
        }
    }
}

pub(crate) fn add_tree_actions(
    actions: &mut Vec<Action>,
    root: &Path,
    category: &str,
    reason: &str,
) {
    for path in collect_files(root) {
        let size = file_size(&path);
        if size > 0 {
            actions.push(Action {
                category: category.into(),
                action: "delete_file".into(),
                path,
                planned_bytes: size,
                marker_offset: None,
                reason: reason.into(),
            });
        }
    }
}

fn build_plan_for(
    chosen: Option<&HashSet<String>>,
) -> Result<(Vec<Action>, Vec<String>, Vec<PathBuf>), String> {
    let user_home = home()?;
    let mut actions = Vec::new();
    let mut warnings = Vec::new();
    let mut allowed_roots = Vec::new();

    for harness in registered_harnesses() {
        let key = harness.metadata().key;
        if let Some(selected) = chosen {
            if !selected.contains(key) {
                continue;
            }
        }
        allowed_roots.extend(harness.allowed_roots(&user_home));
        harness.plan(&user_home, &mut actions, &mut warnings);
    }

    Ok((actions, warnings, allowed_roots))
}

pub fn scan_storage(older_than_days: Option<u64>) -> Result<ScanResult, String> {
    set_min_age_days(older_than_days);
    let (actions, mut warnings, _) = build_plan_for(None)?;
    let user_home = home()?;
    let mut categories = Vec::new();

    for harness in registered_harnesses() {
        let metadata = harness.metadata();
        let mut bytes = 0u64;
        let mut items = 0u64;
        let mut active_trim_files = 0u64;

        for action in actions.iter().filter(|action| action.category == metadata.key) {
            if action.action == "truncate" && file_is_active(&action.path) {
                active_trim_files += 1;
                continue;
            }
            bytes += action.planned_bytes;
            items += 1;
        }

        if active_trim_files > 0 {
            warnings.push(format!(
                "{}: {} active or unverifiable history file(s) were excluded from the reclaimable estimate",
                metadata.name, active_trim_files
            ));
        }

        categories.push(Category {
            key: metadata.key.into(),
            name: metadata.name.into(),
            description_es: metadata.description_es.into(),
            description_en: metadata.description_en.into(),
            logo: metadata.logo.into(),
            bytes,
            items,
            recommended: metadata.recommended,
            protected: metadata.protected,
            available: harness.is_available(&user_home),
            details: Vec::new(),
        });
    }

    let total_reclaimable = categories.iter().map(|category| category.bytes).sum();
    Ok(ScanResult {
        categories,
        total_reclaimable,
        scanned_at: Local::now().to_rfc3339(),
        warnings,
    })
}

fn path_is_under(path: &Path, allowed: &[PathBuf]) -> bool {
    allowed.iter().any(|root| path.starts_with(root))
}

fn atomic_trim(path: &Path, offset: u64) -> Result<u64, String> {
    let original_meta = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    if !original_meta.file_type().is_file() || original_meta.file_type().is_symlink() {
        return Err("unsafe file type".into());
    }
    if offset == 0 || offset >= original_meta.len() {
        return Err("invalid marker offset".into());
    }
    let parent = path.parent().ok_or("missing parent directory")?;
    let mut source = File::open(path).map_err(|e| e.to_string())?;
    source
        .seek(SeekFrom::Start(offset))
        .map_err(|e| e.to_string())?;
    let temp = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
    let mut writer = BufWriter::new(temp.reopen().map_err(|e| e.to_string())?);
    std::io::copy(&mut source, &mut writer).map_err(|e| e.to_string())?;
    writer.flush().map_err(|e| e.to_string())?;
    writer.get_ref().sync_all().map_err(|e| e.to_string())?;
    drop(writer);
    fs::set_permissions(temp.path(), original_meta.permissions()).map_err(|e| e.to_string())?;
    let check = fs::metadata(path).map_err(|e| e.to_string())?;
    if check.len() != original_meta.len() || check.modified().ok() != original_meta.modified().ok()
    {
        return Err("file changed during cleanup".into());
    }
    temp.persist(path).map_err(|e| e.error.to_string())?;
    Ok(offset)
}

/// Rutas abiertas por cualquier proceso, con una sola llamada a lsof.
///
/// `None` si no se puede saber; entonces se pregunta archivo por archivo.
fn open_paths() -> Option<HashSet<PathBuf>> {
    if cfg!(target_os = "windows") {
        return None;
    }
    let executable = if cfg!(target_os = "macos") {
        "/usr/sbin/lsof"
    } else {
        "/usr/bin/lsof"
    };
    let output = Command::new(executable).args(["-w", "-Fn"]).output().ok()?;
    if !matches!(output.status.code(), Some(0) | Some(1)) {
        return None;
    }
    Some(
        String::from_utf8_lossy(&output.stdout)
            .lines()
            .filter_map(|line| line.strip_prefix('n'))
            .filter(|path| path.starts_with('/'))
            .map(PathBuf::from)
            .collect(),
    )
}

fn is_active(path: &Path, tree: bool, opened: Option<&HashSet<PathBuf>>) -> bool {
    match opened {
        Some(set) if tree => set.iter().any(|open| open.starts_with(path)),
        Some(set) => set.contains(path),
        None if tree => collect_files(path).iter().any(|file| file_is_active(file)),
        None => file_is_active(path),
    }
}

pub fn apply_cleanup(request: ApplyRequest) -> Result<ApplyResult, String> {
    set_min_age_days(request.older_than_days);
    let chosen: HashSet<_> = request.categories.into_iter().collect();
    if chosen.is_empty() {
        return Err("No cleanup categories were selected".into());
    }

    let registered: HashSet<_> = registered_harnesses()
        .into_iter()
        .map(|harness| harness.metadata().key.to_string())
        .collect();
    if let Some(unknown) = chosen.iter().find(|key| !registered.contains(*key)) {
        return Err(format!("Unknown cleanup category: {unknown}"));
    }

    let (selected, mut warnings, allowed) = build_plan_for(Some(&chosen))?;
    let planned_bytes = selected.iter().map(|action| action.planned_bytes).sum();
    let user_home = home()?;
    let manifest_dir = user_home.join(".conversation-reclaim");
    fs::create_dir_all(&manifest_dir).map_err(|e| e.to_string())?;
    let manifest_path = manifest_dir.join(format!(
        "native-{}.jsonl",
        Local::now().format("%Y%m%d-%H%M%S-%6f")
    ));
    let mut manifest = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&manifest_path)
        .map_err(|e| e.to_string())?;
    writeln!(
        manifest,
        "{}",
        json!({"type":"plan","version":env!("CARGO_PKG_VERSION"),"createdAt":Local::now().to_rfc3339(),"minAgeDays":min_age_days(),"actions":selected})
    )
    .map_err(|e| e.to_string())?;
    manifest.sync_all().map_err(|e| e.to_string())?;

    let mut freed = 0u64;
    let mut skipped_bytes = 0u64;
    let mut applied = 0usize;
    let mut skipped = 0usize;
    let opened = open_paths();

    for action in selected {
        let before = file_size(&action.path);
        let tree = action.action == "delete_tree";
        let outcome = if !path_is_under(&action.path, &allowed) {
            Err("path outside allowed roots".to_string())
        } else if is_active(&action.path, tree, opened.as_ref()) {
            Err("file is active or its state could not be verified".to_string())
        } else if fs::symlink_metadata(&action.path)
            .map(|m| m.file_type().is_symlink())
            .unwrap_or(true)
        {
            Err("symbolic link or missing target".to_string())
        } else if action.action == "truncate" {
            atomic_trim(&action.path, action.marker_offset.unwrap_or(0))
        } else if action.action == "strip_images" {
            crate::harnesses::rewrite_without_images(&action.path)
        } else if tree {
            fs::remove_dir_all(&action.path)
                .map(|_| action.planned_bytes)
                .map_err(|e| e.to_string())
        } else {
            fs::remove_file(&action.path)
                .map(|_| before)
                .map_err(|e| e.to_string())
        };

        match outcome {
            Ok(bytes) => {
                freed += bytes;
                applied += 1;
                writeln!(
                    manifest,
                    "{}",
                    json!({"type":"outcome","status":"applied","path":action.path,"freedBytes":bytes})
                )
                .map_err(|e| e.to_string())?;
            }
            Err(error) => {
                skipped += 1;
                skipped_bytes += action.planned_bytes;
                warnings.push(format!("{}: {}", action.path.display(), error));
                writeln!(
                    manifest,
                    "{}",
                    json!({"type":"outcome","status":"skipped","path":action.path,"plannedBytes":action.planned_bytes,"error":error})
                )
                .map_err(|e| e.to_string())?;
            }
        }
        manifest.flush().map_err(|e| e.to_string())?;
    }

    manifest.sync_all().map_err(|e| e.to_string())?;
    Ok(ApplyResult {
        freed_bytes: freed,
        planned_bytes,
        skipped_bytes,
        manifest_path: manifest_path.display().to_string(),
        applied,
        skipped,
        warnings,
    })
}

pub fn reveal_manifest(path: &str) -> Result<(), String> {
    let candidate = PathBuf::from(path);
    let root = home()?.join(".conversation-reclaim");
    if !candidate.starts_with(&root) || !candidate.is_file() {
        return Err("Invalid manifest path".into());
    }
    #[cfg(target_os = "macos")]
    let result = Command::new("open").arg("-R").arg(&candidate).status();
    #[cfg(target_os = "windows")]
    let result = Command::new("explorer")
        .arg(format!("/select,{}", candidate.display()))
        .status();
    #[cfg(target_os = "linux")]
    let result = Command::new("xdg-open")
        .arg(candidate.parent().unwrap_or(&root))
        .status();
    result.map_err(|e| e.to_string()).and_then(|s| {
        if s.success() {
            Ok(())
        } else {
            Err("Could not reveal manifest".into())
        }
    })
}

#[cfg(test)]
mod claude_open_tests {
    use super::*;

    const OPEN: &str = "11111111-2222-4333-8444-555555555555";
    const CLOSED: &str = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";

    #[test]
    fn session_ids_and_resume_flags() {
        assert!(is_session_id(OPEN));
        assert!(!is_session_id("11111111-2222-4333-8444-55555555555Z"));
        let mut ids = HashSet::new();
        collect_resume_ids(&format!("claude --resume {OPEN} --model x"), &mut ids);
        collect_resume_ids(&format!("claude --session-id={CLOSED}"), &mut ids);
        collect_resume_ids("claude --resume not-an-id", &mut ids);
        assert_eq!(ids.len(), 2);
    }

    #[test]
    fn open_and_recent_conversations_are_protected() {
        let dir = tempfile::tempdir().unwrap();
        let projects = dir.path().join("projects");
        let project = projects.join("proj");
        fs::create_dir_all(project.join(OPEN).join("subagents")).unwrap();
        let open_file = project.join(format!("{OPEN}.jsonl"));
        let open_sub = project.join(OPEN).join("subagents").join("agent-x.jsonl");
        let closed_file = project.join(format!("{CLOSED}.jsonl"));
        for path in [&open_file, &open_sub, &closed_file] {
            fs::write(path, b"{}\n").unwrap();
        }
        let open: HashSet<String> = [OPEN.to_string()].into_iter().collect();
        assert_eq!(
            claude_protection(&open_file, &projects, Some(&open), None),
            Some("open conversation")
        );
        assert_eq!(
            claude_protection(&open_sub, &projects, Some(&open), None),
            Some("open conversation")
        );
        assert_eq!(claude_protection(&closed_file, &projects, Some(&open), None), None);
        // Recién escrito: con la ventana de minutos también queda protegido.
        let cutoff = SystemTime::now() - Duration::from_secs(CLAUDE_RECENT_MINUTES * 60);
        assert_eq!(
            claude_protection(&closed_file, &projects, Some(&open), Some(cutoff)),
            Some("changed recently")
        );
        let future = SystemTime::now() + Duration::from_secs(60);
        assert_eq!(claude_protection(&closed_file, &projects, Some(&open), Some(future)), None);
    }

    #[test]
    fn retain_drops_only_protected_actions_of_that_category() {
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path();
        let project = home.join(".claude").join("projects").join("proj");
        fs::create_dir_all(&project).unwrap();
        let sessions = home.join(".claude").join("sessions");
        fs::create_dir_all(&sessions).unwrap();
        fs::write(
            sessions.join(format!("{}.json", std::process::id())),
            format!("{{\"pid\":{},\"sessionId\":\"{OPEN}\"}}", std::process::id()),
        )
        .unwrap();
        let open_file = project.join(format!("{OPEN}.jsonl"));
        let closed_file = project.join(format!("{CLOSED}.jsonl"));
        fs::write(&open_file, b"{}\n").unwrap();
        fs::write(&closed_file, b"{}\n").unwrap();
        let mut actions = vec![
            Action::delete("claude", open_file.clone(), 3, "test"),
            Action::delete("claude", closed_file.clone(), 3, "test"),
            Action::delete("other", open_file.clone(), 3, "test"),
        ];
        let mut warnings = Vec::new();
        retain_unprotected_claude(&mut actions, home, "claude", false, &mut warnings);
        let kept: Vec<(&str, &Path)> = actions
            .iter()
            .map(|a| (a.category.as_str(), a.path.as_path()))
            .collect();
        assert_eq!(kept, vec![("claude", closed_file.as_path()), ("other", open_file.as_path())]);
        assert!(warnings.iter().any(|w| w.contains("left untouched")));
    }
}
