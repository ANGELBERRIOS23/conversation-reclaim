//! Outputs de Codex del más pesado al más liviano. Nada se borra solo: cada
//! persona elige qué mandar a la Papelera.

use crate::engine::file_size;
use crate::harnesses::{identical_copies, is_generated_image, is_work_dir, user_search_roots};
use chrono::{DateTime, Local};
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};
use walkdir::WalkDir;

const MIN_BYTES: u64 = 1024 * 1024;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OutputItem {
    path: String,
    name: String,
    task: String,
    bytes: u64,
    modified: String,
    copy_path: Option<String>,
    newer_version: Option<String>,
    /// La conversación de Codex que creó esta carpeta ya no existe.
    orphan: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TrashResult {
    moved: usize,
    freed_bytes: u64,
    manifest_path: String,
    errors: Vec<String>,
}

fn home() -> Result<PathBuf, String> {
    dirs::home_dir().ok_or_else(|| "Could not determine the user home folder".to_string())
}

fn workspace(home: &Path) -> PathBuf {
    home.join("Documents").join("Codex")
}

/// `video_v2` → ("video", 2). Solo una marca explícita `v<número>` cuenta:
/// números sueltos o `(2)` suelen ser series o archivos distintos.
fn version_tail(stem: &str) -> Option<(String, u64)> {
    let digits = stem.len() - stem.trim_end_matches(|c: char| c.is_ascii_digit()).len();
    if digits == 0 {
        return None;
    }
    let (rest, number) = stem.split_at(stem.len() - digits);
    let rest = rest.strip_suffix(['v', 'V'])?;
    let base = rest.trim_end_matches([' ', '_', '.', '-']).to_lowercase();
    Some((base, number.parse().ok()?))
}

/// `<workspace>/<fecha>/<tarea>/outputs/...`
fn is_output_file(path: &Path, workspace: &Path) -> bool {
    path.strip_prefix(workspace)
        .map(|rest| rest.components().count() > 3 && rest.components().nth(2).is_some_and(|c| c.as_os_str() == "outputs"))
        .unwrap_or(false)
}

fn collect(workspace: &Path, min_bytes: u64) -> Vec<PathBuf> {
    let mut found = Vec::new();
    let Ok(days) = fs::read_dir(workspace) else {
        return found;
    };
    for day in days.filter_map(Result::ok) {
        let Ok(tasks) = fs::read_dir(day.path()) else {
            continue;
        };
        for task in tasks.filter_map(Result::ok) {
            let outputs = task.path().join("outputs");
            if !fs::symlink_metadata(&outputs).is_ok_and(|m| m.is_dir()) {
                continue;
            }
            for entry in WalkDir::new(&outputs).follow_links(false).into_iter().filter_map(Result::ok) {
                let hidden = entry.file_name().to_string_lossy().starts_with('.');
                if entry.file_type().is_file() && !hidden && file_size(entry.path()) >= min_bytes {
                    found.push(entry.into_path());
                }
            }
        }
    }
    found
}

pub(crate) fn list_for(home: &Path, min_bytes: u64, find_copies: bool) -> Vec<OutputItem> {
    let workspace = workspace(home);
    let paths = collect(&workspace, min_bytes);

    let mut newest: HashMap<(PathBuf, String, String), (u64, PathBuf)> = HashMap::new();
    for path in &paths {
        let stem = path.file_stem().unwrap_or_default().to_string_lossy();
        if let Some((base, number)) = version_tail(&stem) {
            let ext = path.extension().unwrap_or_default().to_string_lossy().to_lowercase();
            let key = (path.parent().unwrap_or(Path::new("")).to_path_buf(), base, ext);
            let entry = newest.entry(key).or_insert((number, path.clone()));
            if number > entry.0 {
                *entry = (number, path.clone());
            }
        }
    }

    let copies = if find_copies && !paths.is_empty() {
        identical_copies(&paths, &user_search_roots(home), &|dir| dir.starts_with(&workspace))
    } else {
        HashMap::new()
    };
    let orphans: HashSet<PathBuf> = orphan_dirs(home, ORPHAN_IDLE_DAYS).into_iter().collect();

    let mut items: Vec<OutputItem> = paths
        .iter()
        .map(|path| {
            let stem = path.file_stem().unwrap_or_default().to_string_lossy();
            let newer_version = version_tail(&stem).and_then(|(base, _)| {
                let ext = path.extension().unwrap_or_default().to_string_lossy().to_lowercase();
                let key = (path.parent().unwrap_or(Path::new("")).to_path_buf(), base, ext);
                newest
                    .get(&key)
                    .filter(|(_, newest_path)| newest_path != path)
                    .map(|(_, newest_path)| newest_path.file_name().unwrap_or_default().to_string_lossy().into_owned())
            });
            let relative = path.strip_prefix(&workspace).unwrap_or(path);
            let task = relative.components().take(2).map(|c| c.as_os_str().to_string_lossy()).collect::<Vec<_>>().join("/");
            let modified = fs::metadata(path)
                .and_then(|m| m.modified())
                .map(|t| DateTime::<Local>::from(t).format("%Y-%m-%d").to_string())
                .unwrap_or_default();
            OutputItem {
                path: path.display().to_string(),
                name: path.file_name().unwrap_or_default().to_string_lossy().into_owned(),
                task,
                bytes: file_size(path),
                modified,
                copy_path: copies.get(path).map(|c| c.display().to_string()),
                newer_version,
                orphan: task_dir(path, &workspace).is_some_and(|task| orphans.contains(&task)),
            }
        })
        .collect();
    items.sort_by(|a, b| b.bytes.cmp(&a.bytes).then_with(|| a.path.cmp(&b.path)));
    items
}

pub fn list_large_outputs() -> Result<Vec<OutputItem>, String> {
    Ok(list_for(&home()?, MIN_BYTES, true))
}

/// Manda a la Papelera solo los archivos cuya ruta real pasa `allowed`
/// (`outputs/../work/x` no pasa) y deja un registro.
fn trash_checked(paths: Vec<String>, manifest_dir: &Path, kind: &str, folders: bool, allowed: &dyn Fn(&Path) -> bool) -> Result<TrashResult, String> {
    fs::create_dir_all(manifest_dir).map_err(|e| e.to_string())?;
    let manifest_path = manifest_dir.join(format!("{kind}-{}.jsonl", Local::now().format("%Y%m%d-%H%M%S-%6f")));
    let mut manifest = OpenOptions::new().create_new(true).write(true).open(&manifest_path).map_err(|e| e.to_string())?;
    let mut result = TrashResult { moved: 0, freed_bytes: 0, manifest_path: manifest_path.display().to_string(), errors: Vec::new() };

    let mut accepted: Vec<(String, PathBuf, u64)> = Vec::new();
    for raw in paths {
        let path = PathBuf::from(&raw);
        let real = fs::canonicalize(&path).ok();
        let safe = fs::symlink_metadata(&path).is_ok_and(|m| if folders { m.is_dir() } else { m.file_type().is_file() })
            && real.as_deref().is_some_and(allowed);
        let (true, Some(real)) = (safe, real) else {
            result.errors.push(format!("{raw}: not allowed here"));
            continue;
        };
        let size = if folders { folder_size(&real).0 } else { file_size(&real) };
        accepted.push((raw, real, size));
    }

    // Todos juntos en una sola operación: Finder suena una vez (no una por
    // archivo) y "Devolver" sigue funcionando. Si el lote falla, se intenta
    // uno por uno para que un archivo con problema no frene a los demás.
    if !accepted.is_empty() && trash::delete_all(accepted.iter().map(|(_, real, _)| real)).is_err() {
        for (raw, real, _) in &accepted {
            if real.exists() {
                if let Err(error) = trash::delete(real) {
                    result.errors.push(format!("{raw}: {error}"));
                }
            }
        }
    }
    for (raw, real, size) in &accepted {
        if real.exists() {
            if !result.errors.iter().any(|e| e.starts_with(raw.as_str())) {
                result.errors.push(format!("{raw}: still in place"));
            }
            continue;
        }
        result.moved += 1;
        result.freed_bytes += size;
        writeln!(manifest, "{}", json!({"type": kind, "path": raw, "bytes": size, "time": Local::now().to_rfc3339()}))
            .map_err(|e| e.to_string())?;
    }
    manifest.sync_all().map_err(|e| e.to_string())?;
    Ok(result)
}

pub(crate) fn trash_for(home: &Path, paths: Vec<String>, manifest_dir: &Path) -> Result<TrashResult, String> {
    let real_workspace = fs::canonicalize(workspace(home)).map_err(|_| "Codex workspace not found".to_string())?;
    trash_checked(paths, manifest_dir, "outputs", false, &|real| is_output_file(real, &real_workspace))
}

pub fn trash_outputs(paths: Vec<String>) -> Result<TrashResult, String> {
    let home = home()?;
    let manifest_dir = home.join(".conversation-reclaim");
    trash_for(&home, paths, &manifest_dir)
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GeneratedImage {
    path: String,
    name: String,
    chat: String,
    bytes: u64,
    modified: String,
    copy_path: Option<String>,
}

fn generated_dir(home: &Path) -> PathBuf {
    home.join(".codex").join("generated_images")
}

/// Imágenes generadas por Codex: primero las recomendadas (ya hay una copia
/// idéntica fuera de `work/`), luego el resto, cada grupo de mayor a menor.
pub(crate) fn generated_for(home: &Path, find_copies: bool) -> Vec<GeneratedImage> {
    let dir = generated_dir(home);
    let paths: Vec<PathBuf> = WalkDir::new(&dir)
        .follow_links(false)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_file() && is_generated_image(e.path()) && file_size(e.path()) > 0)
        .map(|e| e.into_path())
        .collect();
    let workspace = workspace(home);
    let copies = if find_copies && !paths.is_empty() {
        identical_copies(&paths, &user_search_roots(home), &|d| is_work_dir(d, &workspace))
    } else {
        HashMap::new()
    };
    let mut items: Vec<GeneratedImage> = paths
        .iter()
        .map(|path| GeneratedImage {
            path: path.display().to_string(),
            name: path.file_name().unwrap_or_default().to_string_lossy().into_owned(),
            chat: path
                .parent()
                .and_then(|p| p.file_name())
                .unwrap_or_default()
                .to_string_lossy()
                .into_owned(),
            bytes: file_size(path),
            modified: fs::metadata(path)
                .and_then(|m| m.modified())
                .map(|t| DateTime::<Local>::from(t).format("%Y-%m-%d").to_string())
                .unwrap_or_default(),
            copy_path: copies.get(path).map(|c| c.display().to_string()),
        })
        .collect();
    items.sort_by(|a, b| {
        b.copy_path.is_some().cmp(&a.copy_path.is_some()).then(b.bytes.cmp(&a.bytes)).then_with(|| a.path.cmp(&b.path))
    });
    items
}

pub fn list_generated_images() -> Result<Vec<GeneratedImage>, String> {
    Ok(generated_for(&home()?, true))
}

pub(crate) fn trash_generated_for(home: &Path, paths: Vec<String>, manifest_dir: &Path) -> Result<TrashResult, String> {
    let real_dir = fs::canonicalize(generated_dir(home)).map_err(|_| "Codex generated images not found".to_string())?;
    trash_checked(paths, manifest_dir, "generated-images", false, &|real| real.starts_with(&real_dir) && real != real_dir)
}

pub fn trash_generated_images(paths: Vec<String>) -> Result<TrashResult, String> {
    let home = home()?;
    let manifest_dir = home.join(".conversation-reclaim");
    trash_generated_for(&home, paths, &manifest_dir)
}

/// Días sin cambios que necesita una carpeta para contar como huérfana.
const ORPHAN_IDLE_DAYS: u64 = 2;

/// `<workspace>/<fecha>/<tarea>` de una ruta dentro del espacio de Codex.
fn task_dir(path: &Path, workspace: &Path) -> Option<PathBuf> {
    let rest = path.strip_prefix(workspace).ok()?;
    let mut parts = rest.components();
    let (day, task) = (parts.next()?, parts.next()?);
    Some(workspace.join(day.as_os_str()).join(task.as_os_str()))
}

fn is_dated(name: &str) -> bool {
    let b = name.as_bytes();
    b.len() == 10
        && b.iter().enumerate().all(|(i, c)| if i == 4 || i == 7 { *c == b'-' } else { c.is_ascii_digit() })
}

fn first_line(path: &Path) -> Option<String> {
    let file = File::open(path).ok()?;
    let reader: Box<dyn Read> = if path.to_string_lossy().ends_with(".zst") {
        Box::new(zstd::stream::read::Decoder::new(file).ok()?)
    } else {
        Box::new(file)
    };
    let mut line = String::new();
    BufReader::new(reader).read_line(&mut line).ok()?;
    Some(line)
}

/// Carpetas de tareas que todavía tienen una conversación de Codex (activa o
/// archivada), según la carpeta de trabajo que guarda cada sesión.
/// `None` si alguna sesión no se pudo leer o no hay ninguna: así no se marca
/// como huérfano algo que quizá sigue vivo.
pub(crate) fn live_task_dirs(home: &Path) -> Option<HashSet<PathBuf>> {
    let workspace = workspace(home);
    let mut live = HashSet::new();
    let mut sessions = 0;
    for root in [home.join(".codex").join("sessions"), home.join(".codex").join("archived_sessions")] {
        for entry in WalkDir::new(&root).follow_links(false).into_iter().filter_map(Result::ok) {
            let name = entry.file_name().to_string_lossy();
            let is_session = name.starts_with("rollout-") && (name.ends_with(".jsonl") || name.ends_with(".jsonl.zst"));
            if !entry.file_type().is_file() || !is_session {
                continue;
            }
            let meta: Value = serde_json::from_str(first_line(entry.path())?.trim()).ok()?;
            sessions += 1;
            if let Some(task) = meta.pointer("/payload/cwd").and_then(Value::as_str).and_then(|cwd| task_dir(Path::new(cwd), &workspace)) {
                live.insert(task);
            }
        }
    }
    (sessions > 0).then_some(live)
}

/// Carpetas `<fecha>/<tarea>` sin conversación y sin cambios en `idle_days`.
pub(crate) fn orphan_dirs(home: &Path, idle_days: u64) -> Vec<PathBuf> {
    let Some(live) = live_task_dirs(home) else {
        return Vec::new();
    };
    let cutoff = SystemTime::now() - Duration::from_secs(idle_days * 86_400);
    let real_dirs = |dir: &Path| -> Vec<PathBuf> {
        fs::read_dir(dir)
            .map(|read| {
                read.filter_map(Result::ok)
                    .filter(|e| e.file_type().is_ok_and(|t| t.is_dir() && !t.is_symlink()))
                    .map(|e| e.path())
                    .collect()
            })
            .unwrap_or_default()
    };
    let mut found = Vec::new();
    for day in real_dirs(&workspace(home)) {
        if !is_dated(&day.file_name().unwrap_or_default().to_string_lossy()) {
            continue;
        }
        for task in real_dirs(&day) {
            if live.contains(&task) {
                continue;
            }
            // Solo cuenta la fecha de los archivos: la de una carpeta cambia
            // también cuando se le quitan cosas, y eso no es actividad.
            let recent = WalkDir::new(&task)
                .follow_links(false)
                .into_iter()
                .filter_map(Result::ok)
                .filter(|e| e.file_type().is_file())
                .filter_map(|e| e.metadata().ok()?.modified().ok())
                .any(|time| time >= cutoff);
            if !recent {
                found.push(task);
            }
        }
    }
    found
}

fn folder_size(dir: &Path) -> (u64, u64) {
    WalkDir::new(dir)
        .follow_links(false)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_file())
        .fold((0, 0), |(bytes, files), e| (bytes + file_size(e.path()), files + 1))
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OrphanFolder {
    path: String,
    name: String,
    bytes: u64,
    files: u64,
    modified: String,
    has_git: bool,
}

pub(crate) fn orphans_for(home: &Path, idle_days: u64) -> Vec<OrphanFolder> {
    let workspace = workspace(home);
    let mut items: Vec<OrphanFolder> = orphan_dirs(home, idle_days)
        .into_iter()
        .map(|dir| {
            let (bytes, files) = folder_size(&dir);
            OrphanFolder {
                name: dir.strip_prefix(&workspace).unwrap_or(&dir).display().to_string(),
                bytes,
                files,
                modified: fs::metadata(&dir)
                    .and_then(|m| m.modified())
                    .map(|t| DateTime::<Local>::from(t).format("%Y-%m-%d").to_string())
                    .unwrap_or_default(),
                has_git: WalkDir::new(&dir).into_iter().filter_map(Result::ok).any(|e| e.file_name() == ".git"),
                path: dir.display().to_string(),
            }
        })
        .collect();
    items.sort_by(|a, b| b.bytes.cmp(&a.bytes).then_with(|| a.path.cmp(&b.path)));
    items
}

pub fn list_orphan_folders() -> Result<Vec<OrphanFolder>, String> {
    Ok(orphans_for(&home()?, ORPHAN_IDLE_DAYS))
}

fn orphan_set(home: &Path) -> HashSet<PathBuf> {
    orphan_dirs(home, ORPHAN_IDLE_DAYS).iter().filter_map(|d| fs::canonicalize(d).ok()).collect()
}

pub(crate) fn trash_orphans_for(home: &Path, paths: Vec<String>, manifest_dir: &Path) -> Result<TrashResult, String> {
    // Se vuelve a calcular en el momento: solo pasa lo que hoy sigue huérfano.
    let allowed = orphan_set(home);
    trash_checked(paths, manifest_dir, "orphan-folders", true, &|real| allowed.contains(real))
}

pub fn trash_orphan_folders(paths: Vec<String>) -> Result<TrashResult, String> {
    let home = home()?;
    let manifest_dir = home.join(".conversation-reclaim");
    trash_orphans_for(&home, paths, &manifest_dir)
}

/// Abre en Finder una carpeta huérfana de la lista.
pub fn reveal_orphan_folder(path: String) -> Result<(), String> {
    let home = home()?;
    let real = fs::canonicalize(&path).map_err(|_| "folder not found".to_string())?;
    if !orphan_set(&home).contains(&real) {
        return Err("only folders of deleted Codex chats can be opened here".into());
    }
    #[cfg(target_os = "macos")]
    return run(std::process::Command::new("open").arg(&real));
    #[cfg(target_os = "windows")]
    return run(std::process::Command::new("explorer").arg(&real)).or(Ok(()));
    #[cfg(target_os = "linux")]
    return run(std::process::Command::new("xdg-open").arg(&real));
}

/// Ruta real del archivo si es un output de Codex o una imagen generada.
/// Solo esos se pueden abrir o mostrar desde la página de archivos grandes.
pub(crate) fn listed_file(home: &Path, path: &str) -> Result<PathBuf, String> {
    let real = fs::canonicalize(path).map_err(|_| "file not found".to_string())?;
    if !fs::symlink_metadata(&real).is_ok_and(|m| m.file_type().is_file()) {
        return Err("not a file".into());
    }
    let in_outputs = fs::canonicalize(workspace(home)).is_ok_and(|ws| is_output_file(&real, &ws));
    let in_generated = fs::canonicalize(generated_dir(home)).is_ok_and(|dir| real.starts_with(&dir) && real != dir);
    if in_outputs || in_generated {
        Ok(real)
    } else {
        Err("only Codex outputs and generated images can be opened here".into())
    }
}

fn run(command: &mut std::process::Command) -> Result<(), String> {
    command.status().map_err(|e| e.to_string()).and_then(|status| {
        if status.success() {
            Ok(())
        } else {
            Err("the system could not open it".into())
        }
    })
}

/// Abre el archivo con la app que el sistema usa para ese tipo.
pub fn open_large_file(path: String) -> Result<(), String> {
    let real = listed_file(&home()?, &path)?;
    #[cfg(target_os = "macos")]
    return run(std::process::Command::new("open").arg(&real));
    #[cfg(target_os = "windows")]
    return run(std::process::Command::new("explorer").arg(&real)).or(Ok(()));
    #[cfg(target_os = "linux")]
    return run(std::process::Command::new("xdg-open").arg(&real));
}

/// Abre la carpeta del archivo con el archivo seleccionado (Finder/Explorador).
pub fn reveal_large_file(path: String) -> Result<(), String> {
    let real = listed_file(&home()?, &path)?;
    #[cfg(target_os = "macos")]
    return run(std::process::Command::new("open").arg("-R").arg(&real));
    #[cfg(target_os = "windows")]
    return run(std::process::Command::new("explorer").arg(format!("/select,{}", real.display()))).or(Ok(()));
    #[cfg(target_os = "linux")]
    return run(std::process::Command::new("xdg-open").arg(real.parent().unwrap_or(&real)));
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make(root: &Path, relative: &str, data: &[u8]) -> PathBuf {
        let path = root.join(relative);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, data).unwrap();
        path
    }

    /// Huérfanas reales (solo lee): `cargo test real_orphans -- --ignored --nocapture`
    #[test]
    #[ignore]
    fn real_orphans() {
        let items = list_orphan_folders().unwrap();
        let total: u64 = items.iter().map(|i| i.bytes).sum();
        println!("{} orphan folders, {} bytes", items.len(), total);
        for item in items.iter().take(5) {
            println!("{} {} files git={} {}", item.bytes, item.files, item.has_git, item.name);
        }
        let outputs = list_large_outputs().unwrap();
        println!("outputs from deleted chats: {}", outputs.iter().filter(|o| o.orphan).count());
    }

    /// Lista real (solo lee): `cargo test real_outputs -- --ignored --nocapture`
    #[test]
    #[ignore]
    fn real_outputs() {
        let items = list_large_outputs().unwrap();
        let total: u64 = items.iter().map(|i| i.bytes).sum();
        let copies = items.iter().filter(|i| i.copy_path.is_some()).count();
        println!("{} outputs, {} bytes, {} with copy", items.len(), total, copies);
        for item in items.iter().take(3) {
            println!("{} {} {}", item.bytes, item.task, item.name);
        }
    }

    #[test]
    fn generated_images_put_the_ones_with_a_copy_first() {
        let temp = tempfile::tempdir().unwrap();
        let home = temp.path();
        make(home, ".codex/generated_images/chat-a/big.png", &[b'a'; 300]);
        make(home, ".codex/generated_images/chat-a/.DS_Store", b"finder");
        make(home, "Documents/Codex/d/t/outputs/.DS_Store", b"finder");
        make(home, ".codex/generated_images/chat-a/copied.png", &[b'b'; 100]);
        make(home, ".codex/generated_images/chat-b/in-work-only.png", &[b'c'; 200]);
        make(home, "Downloads/kept.png", &[b'b'; 100]);
        make(home, "Documents/Codex/d/t/work/draft.png", &[b'c'; 200]);
        let items = generated_for(home, true);
        let names: Vec<_> = items.iter().map(|i| i.name.as_str()).collect();
        assert_eq!(names, ["copied.png", "big.png", "in-work-only.png"]);
        assert!(items[0].copy_path.as_deref().is_some_and(|c| c.ends_with("kept.png")));
        assert!(items[2].copy_path.is_none(), "a copy inside work/ does not count");
        assert_eq!(items[0].chat, "chat-a");
    }

    #[test]
    fn generated_trash_only_accepts_files_inside_generated_images() {
        let temp = tempfile::tempdir().unwrap();
        let home = temp.path();
        let image = make(home, ".codex/generated_images/chat/x.png", b"img");
        let other = make(home, ".codex/sessions/rollout.jsonl", b"{}");
        let sneaky = format!("{}/.codex/generated_images/../sessions/rollout.jsonl", home.display());
        let result = trash_generated_for(home, vec![other.display().to_string(), sneaky], &home.join("m")).unwrap();
        assert_eq!(result.moved, 0);
        assert_eq!(result.errors.len(), 2);
        assert!(other.exists() && image.exists());
    }

    /// Usa la Papelera real (suena una vez). Se corre a mano:
    /// `cargo test real_trash_batch -- --ignored --nocapture`
    #[test]
    #[ignore]
    fn real_trash_batch() {
        let temp = tempfile::tempdir().unwrap();
        let home = temp.path();
        let names = ["reclaim-prueba-1.txt", "reclaim-prueba-2.txt", "reclaim-prueba-3.txt"];
        let paths: Vec<String> = names
            .iter()
            .map(|n| make(home, &format!("Documents/Codex/d/t/outputs/{n}"), b"prueba").display().to_string())
            .collect();
        let result = trash_for(home, paths, &home.join("m")).unwrap();
        println!("moved={} errors={:?}", result.moved, result.errors);
        assert_eq!(result.moved, 3);
        // Se sacan de la Papelera para que el directorio temporal los borre.
        let trash = dirs::home_dir().unwrap().join(".Trash");
        for name in names {
            let _ = fs::rename(trash.join(name), home.join(name));
        }
    }

    fn session(home: &Path, name: &str, cwd: &Path) {
        make(
            home,
            &format!(".codex/sessions/2026/10/01/{name}"),
            format!("{}\n", json!({"type": "session_meta", "payload": {"cwd": cwd}})).as_bytes(),
        );
    }

    fn age(path: &Path, days: u64) {
        let stamp = SystemTime::now() - Duration::from_secs(days * 86_400);
        for entry in WalkDir::new(path).into_iter().filter_map(Result::ok) {
            File::options().read(true).open(entry.path()).ok().map(|f| f.set_times(fs::FileTimes::new().set_modified(stamp)));
            if entry.file_type().is_file() {
                File::options().write(true).open(entry.path()).unwrap().set_times(fs::FileTimes::new().set_modified(stamp)).unwrap();
            }
        }
    }

    #[test]
    fn orphan_folders_have_no_session_and_are_not_recent() {
        let temp = tempfile::tempdir().unwrap();
        let home = temp.path();
        let ws = home.join("Documents/Codex");
        make(home, "Documents/Codex/2026-09-01/alive/outputs/a.mp4", b"a");
        make(home, "Documents/Codex/2026-09-01/archived/work/b.png", b"b");
        make(home, "Documents/Codex/2026-09-01/gone/outputs/c.mp4", b"cc");
        make(home, "Documents/Codex/2026-10-20/gone-today/outputs/d.mp4", b"d");
        make(home, "Documents/Codex/integraciones/tool/x.txt", b"x");
        session(home, "rollout-1.jsonl", &ws.join("2026-09-01/alive/work"));
        make(home, ".codex/archived_sessions/rollout-2.jsonl", format!("{}\n", json!({"type": "session_meta", "payload": {"cwd": ws.join("2026-09-01/archived")}})).as_bytes());
        for dir in ["2026-09-01/alive", "2026-09-01/archived", "2026-09-01/gone", "integraciones"] {
            age(&ws.join(dir), 10);
        }
        let names: Vec<String> = orphans_for(home, 2).into_iter().map(|o| o.name).collect();
        assert_eq!(names, [Path::new("2026-09-01").join("gone").display().to_string()]);
    }

    #[test]
    fn no_orphans_when_a_session_cannot_be_read() {
        let temp = tempfile::tempdir().unwrap();
        let home = temp.path();
        make(home, "Documents/Codex/2026-09-01/gone/outputs/c.mp4", b"c");
        age(&home.join("Documents/Codex/2026-09-01/gone"), 10);
        make(home, ".codex/sessions/rollout-broken.jsonl", b"not json\n");
        assert!(orphans_for(home, 2).is_empty());
        assert!(orphans_for(&temp.path().join("nothing"), 2).is_empty(), "no sessions at all: nothing is orphan");
    }

    #[test]
    fn orphan_trash_refuses_live_folders() {
        let temp = tempfile::tempdir().unwrap();
        let home = temp.path();
        let ws = home.join("Documents/Codex");
        let alive = make(home, "Documents/Codex/2026-09-01/alive/outputs/a.mp4", b"a");
        session(home, "rollout-1.jsonl", &ws.join("2026-09-01/alive"));
        age(&ws.join("2026-09-01"), 10);
        let result = trash_orphans_for(home, vec![ws.join("2026-09-01/alive").display().to_string()], &home.join("m")).unwrap();
        assert_eq!(result.moved, 0);
        assert!(alive.exists());
    }

    #[test]
    fn only_listed_files_can_be_opened() {
        let temp = tempfile::tempdir().unwrap();
        let home = temp.path();
        let output = make(home, "Documents/Codex/d/t/outputs/video.mp4", b"v");
        let image = make(home, ".codex/generated_images/chat/x.png", b"i");
        let work = make(home, "Documents/Codex/d/t/work/draft.mp4", b"w");
        let personal = make(home, "Downloads/mine.pdf", b"p");
        let sneaky = format!("{}/Documents/Codex/d/t/outputs/../work/draft.mp4", home.display());
        assert!(listed_file(home, &output.display().to_string()).is_ok());
        assert!(listed_file(home, &image.display().to_string()).is_ok());
        assert!(listed_file(home, &work.display().to_string()).is_err());
        assert!(listed_file(home, &personal.display().to_string()).is_err());
        assert!(listed_file(home, &sneaky).is_err());
        assert!(listed_file(home, &home.join(".codex/generated_images").display().to_string()).is_err());
    }

    #[test]
    fn version_tail_only_accepts_explicit_v_numbers() {
        assert_eq!(version_tail("spot_v2"), Some(("spot".into(), 2)));
        assert_eq!(version_tail("Spot-V10"), Some(("spot".into(), 10)));
        assert_eq!(version_tail("Audio 03"), None);
        assert_eq!(version_tail("File-unnamed (2)"), None);
        assert_eq!(version_tail("final"), None);
    }

    #[test]
    fn lists_biggest_first_with_copies_and_versions() {
        let temp = tempfile::tempdir().unwrap();
        let home = temp.path();
        make(home, "Documents/Codex/d/t/outputs/big.mp4", &[b'a'; 300]);
        make(home, "Documents/Codex/d/t/outputs/spot_v1.mp4", &[b'b'; 200]);
        make(home, "Documents/Codex/d/t/outputs/spot_v2.mp4", &[b'c'; 100]);
        make(home, "Documents/Codex/d/t/outputs/tiny.txt", b"x");
        make(home, "Documents/Codex/d/t/work/draft.mp4", &[b'z'; 900]);
        make(home, "Downloads/big-copy.mp4", &[b'a'; 300]);
        let items = list_for(home, 10, true);
        let names: Vec<_> = items.iter().map(|i| i.name.as_str()).collect();
        assert_eq!(names, ["big.mp4", "spot_v1.mp4", "spot_v2.mp4"]);
        assert!(items[0].copy_path.as_deref().is_some_and(|c| c.ends_with("big-copy.mp4")));
        assert_eq!(items[1].newer_version.as_deref(), Some("spot_v2.mp4"));
        assert_eq!(items[2].newer_version, None);
        assert_eq!(items[0].task, "d/t");
    }

    #[test]
    fn trash_refuses_anything_outside_codex_outputs() {
        let temp = tempfile::tempdir().unwrap();
        let home = temp.path();
        make(home, "Documents/Codex/d/t/outputs/keep.mp4", b"keep");
        let work = make(home, "Documents/Codex/d/t/work/draft.mp4", b"work");
        let other = make(home, "Downloads/personal.mp4", b"mine");
        let sneaky = format!("{}/Documents/Codex/d/t/outputs/../work/draft.mp4", home.display());
        let result = trash_for(
            home,
            vec![work.display().to_string(), other.display().to_string(), sneaky],
            &home.join("manifests"),
        )
        .unwrap();
        assert_eq!(result.moved, 0);
        assert_eq!(result.errors.len(), 3);
        assert!(work.exists() && other.exists());
    }
}
