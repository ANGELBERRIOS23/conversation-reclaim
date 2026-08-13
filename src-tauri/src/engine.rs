use crate::harnesses::registered_harnesses;
use chrono::Local;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashSet;
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, BufWriter, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use walkdir::WalkDir;

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
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ApplyResult {
    freed_bytes: u64,
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

fn file_is_active(path: &Path) -> bool {
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

fn build_plan() -> Result<(Vec<Action>, Vec<String>, Vec<PathBuf>), String> {
    let user_home = home()?;
    let mut actions = Vec::new();
    let mut warnings = Vec::new();
    let harnesses = registered_harnesses();
    let allowed_roots = harnesses
        .iter()
        .flat_map(|h| h.allowed_roots(&user_home))
        .collect();
    for harness in harnesses {
        harness.plan(&user_home, &mut actions, &mut warnings);
    }
    Ok((actions, warnings, allowed_roots))
}

pub fn scan_storage() -> Result<ScanResult, String> {
    let (actions, warnings, _) = build_plan()?;
    let mut categories = Vec::new();
    for harness in registered_harnesses() {
        let metadata = harness.metadata();
        let matching: Vec<_> = actions
            .iter()
            .filter(|a| a.category == metadata.key)
            .collect();
        categories.push(Category {
            key: metadata.key.into(),
            name: metadata.name.into(),
            description_es: metadata.description_es.into(),
            description_en: metadata.description_en.into(),
            logo: metadata.logo.into(),
            bytes: matching.iter().map(|a| a.planned_bytes).sum(),
            items: matching.len() as u64,
            recommended: metadata.recommended,
            protected: metadata.protected,
            available: true,
            details: Vec::new(),
        });
    }
    let total_reclaimable = categories.iter().map(|c| c.bytes).sum();
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

pub fn apply_cleanup(request: ApplyRequest) -> Result<ApplyResult, String> {
    let chosen: HashSet<_> = request.categories.into_iter().collect();
    if chosen.is_empty() {
        return Err("No cleanup categories were selected".into());
    }
    let registered: HashSet<_> = registered_harnesses()
        .into_iter()
        .map(|h| h.metadata().key.to_string())
        .collect();
    if let Some(unknown) = chosen.iter().find(|key| !registered.contains(*key)) {
        return Err(format!("Unknown cleanup category: {unknown}"));
    }
    let (plan, mut warnings, allowed) = build_plan()?;
    let selected: Vec<_> = plan
        .into_iter()
        .filter(|a| chosen.contains(&a.category))
        .collect();
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
    writeln!(manifest, "{}", json!({"type":"plan","version":"3.0.1","createdAt":Local::now().to_rfc3339(),"actions":selected})).map_err(|e| e.to_string())?;
    manifest.sync_all().map_err(|e| e.to_string())?;

    let mut freed = 0u64;
    let mut applied = 0usize;
    let mut skipped = 0usize;
    for action in selected {
        let before = file_size(&action.path);
        let outcome = if !path_is_under(&action.path, &allowed) {
            Err("path outside allowed roots".to_string())
        } else if file_is_active(&action.path) {
            Err("file is active or its state could not be verified".to_string())
        } else if fs::symlink_metadata(&action.path)
            .map(|m| m.file_type().is_symlink())
            .unwrap_or(true)
        {
            Err("symbolic link or missing target".to_string())
        } else if action.action == "truncate" {
            atomic_trim(&action.path, action.marker_offset.unwrap_or(0))
        } else {
            fs::remove_file(&action.path)
                .map(|_| before)
                .map_err(|e| e.to_string())
        };
        match outcome {
            Ok(bytes) => {
                freed += bytes;
                applied += 1;
                writeln!(manifest, "{}", json!({"type":"outcome","status":"applied","path":action.path,"freedBytes":bytes})).map_err(|e| e.to_string())?;
            }
            Err(error) => {
                skipped += 1;
                warnings.push(format!("{}: {}", action.path.display(), error));
                writeln!(
                    manifest,
                    "{}",
                    json!({"type":"outcome","status":"skipped","path":action.path,"error":error})
                )
                .map_err(|e| e.to_string())?;
            }
        }
        manifest.flush().map_err(|e| e.to_string())?;
    }
    manifest.sync_all().map_err(|e| e.to_string())?;
    Ok(ApplyResult {
        freed_bytes: freed,
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
