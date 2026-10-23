//! Outputs de Codex del más pesado al más liviano. Nada se borra solo: cada
//! persona elige qué mandar a la Papelera.

use crate::engine::file_size;
use crate::harnesses::{identical_copies, is_work_dir, user_search_roots};
use chrono::{DateTime, Local};
use serde::Serialize;
use serde_json::json;
use std::collections::HashMap;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
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
                if entry.file_type().is_file() && file_size(entry.path()) >= min_bytes {
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
fn trash_checked(paths: Vec<String>, manifest_dir: &Path, kind: &str, allowed: &dyn Fn(&Path) -> bool) -> Result<TrashResult, String> {
    fs::create_dir_all(manifest_dir).map_err(|e| e.to_string())?;
    let manifest_path = manifest_dir.join(format!("{kind}-{}.jsonl", Local::now().format("%Y%m%d-%H%M%S-%6f")));
    let mut manifest = OpenOptions::new().create_new(true).write(true).open(&manifest_path).map_err(|e| e.to_string())?;
    let mut result = TrashResult { moved: 0, freed_bytes: 0, manifest_path: manifest_path.display().to_string(), errors: Vec::new() };

    for raw in paths {
        let path = PathBuf::from(&raw);
        let real = fs::canonicalize(&path).ok();
        let safe = fs::symlink_metadata(&path).is_ok_and(|m| m.file_type().is_file())
            && real.as_deref().is_some_and(allowed);
        let (true, Some(real)) = (safe, real) else {
            result.errors.push(format!("{raw}: not allowed here"));
            continue;
        };
        let size = file_size(&real);
        match trash::delete(&real) {
            Ok(()) => {
                result.moved += 1;
                result.freed_bytes += size;
                writeln!(manifest, "{}", json!({"type": kind, "path": raw, "bytes": size, "time": Local::now().to_rfc3339()}))
                    .map_err(|e| e.to_string())?;
            }
            Err(error) => result.errors.push(format!("{raw}: {error}")),
        }
    }
    manifest.sync_all().map_err(|e| e.to_string())?;
    Ok(result)
}

pub(crate) fn trash_for(home: &Path, paths: Vec<String>, manifest_dir: &Path) -> Result<TrashResult, String> {
    let real_workspace = fs::canonicalize(workspace(home)).map_err(|_| "Codex workspace not found".to_string())?;
    trash_checked(paths, manifest_dir, "outputs", &|real| is_output_file(real, &real_workspace))
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
        .filter(|e| e.file_type().is_file() && file_size(e.path()) > 0)
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
    trash_checked(paths, manifest_dir, "generated-images", &|real| real.starts_with(&real_dir) && real != real_dir)
}

pub fn trash_generated_images(paths: Vec<String>) -> Result<TrashResult, String> {
    let home = home()?;
    let manifest_dir = home.join(".conversation-reclaim");
    trash_generated_for(&home, paths, &manifest_dir)
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
