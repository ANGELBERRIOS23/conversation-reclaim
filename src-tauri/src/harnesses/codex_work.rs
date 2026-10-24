use super::{Harness, HarnessMetadata};
use crate::engine::{file_size, is_older_than_cutoff, Action};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use walkdir::WalkDir;

const MEDIA: &[&str] = &[
    "png", "jpg", "jpeg", "webp", "gif", "heic", "tif", "tiff", "bmp", "mp4", "mov", "m4v", "webm",
    "mkv", "avi",
];
const SKIP_DIRS: &[&str] = &["node_modules", ".git", ".venv", "venv", "Library", ".Trash"];

const IMAGES: &[&str] = &["png", "jpg", "jpeg", "webp", "gif", "heic"];

fn is_hidden(path: &Path) -> bool {
    path.file_name().is_some_and(|name| name.to_string_lossy().starts_with('.'))
}

/// Una imagen de verdad: ni `.DS_Store` ni otros archivos ocultos que Finder
/// deja en las carpetas.
pub(crate) fn is_generated_image(path: &Path) -> bool {
    !is_hidden(path)
        && path
            .extension()
            .and_then(|e| e.to_str())
            .is_some_and(|e| IMAGES.contains(&e.to_ascii_lowercase().as_str()))
}

fn is_media(path: &Path) -> bool {
    path.extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| MEDIA.contains(&e.to_ascii_lowercase().as_str()))
}

fn has_files(dir: &Path) -> bool {
    WalkDir::new(dir)
        .into_iter()
        .filter_map(Result::ok)
        .any(|entry| entry.file_type().is_file())
}

/// `<workspace>/<fecha>/<tarea>/work/...`
pub(crate) fn is_work_dir(path: &Path, workspace: &Path) -> bool {
    path.strip_prefix(workspace)
        .map(|rest| rest.components().nth(2).is_some_and(|c| c.as_os_str() == "work"))
        .unwrap_or(false)
}

/// Imágenes y videos que Codex dejó en `work/` de tareas viejas.
///
/// Nunca toca `outputs/` (lo entregado) ni archivos dentro de un repositorio
/// git. Si la tarea no tiene nada en `outputs/`, lo entregado puede estar en
/// `work/` y la tarea no se toca.
pub(crate) fn work_media(workspace: &Path) -> Vec<PathBuf> {
    let mut found = Vec::new();
    let Ok(days) = fs::read_dir(workspace) else {
        return found;
    };
    for day in days.filter_map(Result::ok) {
        let Ok(tasks) = fs::read_dir(day.path()) else {
            continue;
        };
        for task in tasks.filter_map(Result::ok) {
            let work = task.path().join("work");
            let outputs = task.path().join("outputs");
            let is_real_dir = fs::symlink_metadata(&work).is_ok_and(|m| m.is_dir());
            if !is_real_dir || !outputs.is_dir() || !has_files(&outputs) {
                continue;
            }
            let walker = WalkDir::new(&work).follow_links(false).into_iter();
            for entry in walker
                .filter_entry(|e| {
                    !(e.file_type().is_dir()
                        && (e.file_name() == "node_modules" || e.path().join(".git").exists()))
                })
                .filter_map(Result::ok)
            {
                let path = entry.path();
                if entry.file_type().is_file() && is_media(path) && file_size(path) > 0 && is_older_than_cutoff(path) {
                    found.push(path.to_path_buf());
                }
            }
        }
    }
    found.sort();
    found
}

fn same_contents(a: &Path, b: &Path) -> bool {
    use std::io::Read;
    let (Ok(mut fa), Ok(mut fb)) = (fs::File::open(a), fs::File::open(b)) else {
        return false;
    };
    let mut ba = vec![0u8; 1 << 20];
    let mut bb = vec![0u8; 1 << 20];
    loop {
        let (Ok(na), Ok(nb)) = (fa.read(&mut ba), fb.read(&mut bb)) else {
            return false;
        };
        if na != nb || ba[..na] != bb[..nb] {
            return false;
        }
        if na == 0 {
            return true;
        }
    }
}

/// {original: copia} para los originales que tienen una copia idéntica en
/// `roots`. Solo compara archivos del mismo tamaño y no sigue enlaces (así no
/// baja archivos de la nube). `skip_dir` permite saltar carpetas enteras.
pub(crate) fn identical_copies(
    originals: &[PathBuf],
    roots: &[PathBuf],
    skip_dir: &dyn Fn(&Path) -> bool,
) -> HashMap<PathBuf, PathBuf> {
    let mut by_size: HashMap<u64, Vec<PathBuf>> = HashMap::new();
    for path in originals {
        by_size.entry(file_size(path)).or_default().push(path.clone());
    }
    let mut found: HashMap<PathBuf, PathBuf> = HashMap::new();
    for root in roots {
        let walker = WalkDir::new(root).follow_links(false).into_iter();
        for entry in walker
            .filter_entry(|e| {
                !(e.file_type().is_dir()
                    && (SKIP_DIRS.iter().any(|skip| e.file_name() == *skip) || skip_dir(e.path())))
            })
            .filter_map(Result::ok)
        {
            if !entry.file_type().is_file() || is_hidden(entry.path()) {
                continue;
            }
            let Some(candidates) = by_size.get(&file_size(entry.path())) else {
                continue;
            };
            for original in candidates {
                if original.as_path() != entry.path()
                    && !found.contains_key(original)
                    && same_contents(original, entry.path())
                {
                    found.insert(original.clone(), entry.path().to_path_buf());
                }
            }
        }
    }
    found
}

/// Imágenes generadas por Codex que ya tienen una copia idéntica guardada en
/// las carpetas del usuario. Las copias dentro de `work/` no cuentan porque
/// también se limpian.
pub(crate) fn generated_duplicates(generated: &Path, search_roots: &[PathBuf], workspace: &Path) -> Vec<(PathBuf, PathBuf)> {
    let originals: Vec<PathBuf> = WalkDir::new(generated)
        .follow_links(false)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_file() && is_generated_image(e.path()) && file_size(e.path()) > 0 && is_older_than_cutoff(e.path()))
        .map(|e| e.into_path())
        .collect();
    if originals.is_empty() {
        return Vec::new();
    }
    let mut pairs: Vec<_> = identical_copies(&originals, search_roots, &|dir| is_work_dir(dir, workspace))
        .into_iter()
        .collect();
    pairs.sort();
    pairs
}

pub(crate) fn search_roots(home: &Path) -> Vec<PathBuf> {
    let Ok(entries) = fs::read_dir(home) else {
        return Vec::new();
    };
    entries
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_ok_and(|t| t.is_dir()))
        .filter(|e| {
            let name = e.file_name();
            let name = name.to_string_lossy();
            !name.starts_with('.') && name != "Applications" && !SKIP_DIRS.contains(&name.as_ref())
        })
        .map(|e| e.path())
        .collect()
}

pub struct CodexWork;

impl Harness for CodexWork {
    fn metadata(&self) -> HarnessMetadata {
        HarnessMetadata {
            key: "codexWork",
            name: "Codex work files",
            description_es: "Capturas y borradores en work/ de tareas viejas de Codex; outputs/ no se toca",
            description_en: "Screenshots and drafts in work/ of old Codex tasks; outputs/ is never touched",
            logo: "codexWork",
            recommended: false,
            protected: true,
        }
    }

    fn allowed_roots(&self, home: &Path) -> Vec<PathBuf> {
        vec![home.join("Documents/Codex"), home.join(".codex/generated_images")]
    }

    fn plan(&self, home: &Path, actions: &mut Vec<Action>, _warnings: &mut Vec<String>) {
        let workspace = home.join("Documents/Codex");
        for path in work_media(&workspace) {
            let size = file_size(&path);
            actions.push(Action::delete("codexWork", path, size, "Codex work/ media in a task that already has outputs"));
        }
        let generated = home.join(".codex/generated_images");
        for (original, _copy) in generated_duplicates(&generated, &search_roots(home), &workspace) {
            let size = file_size(&original);
            actions.push(Action::delete("codexWork", original, size, "generated image already saved elsewhere"));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs::{File, FileTimes};
    use std::time::{Duration, SystemTime};

    fn make(root: &Path, relative: &str, data: &[u8], old: bool) -> PathBuf {
        let path = root.join(relative);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, data).unwrap();
        if old {
            let stamp = SystemTime::now() - Duration::from_secs(30 * 86_400);
            File::options().write(true).open(&path).unwrap().set_times(FileTimes::new().set_modified(stamp)).unwrap();
        }
        path
    }

    #[test]
    fn work_cleanup_skips_outputs_git_recent_and_tasks_without_outputs() {
        let temp = tempfile::tempdir().unwrap();
        let ws = temp.path().join("ws");
        let shot = make(&ws, "2026-09-01/t/work/shot.png", b"x", true);
        let video = make(&ws, "2026-09-01/t/work/render/draft.mp4", b"x", true);
        make(&ws, "2026-09-01/t/work/new.png", b"x", false);
        make(&ws, "2026-09-01/t/work/script.py", b"x", true);
        make(&ws, "2026-09-01/t/outputs/final.png", b"x", true);
        make(&ws, "2026-09-01/t/work/repo/.git/HEAD", b"x", true);
        make(&ws, "2026-09-01/t/work/repo/docs/photo.png", b"x", true);
        make(&ws, "2026-09-01/no-outputs/work/real-deliverable.mp4", b"x", true);
        let mut expected = vec![shot, video];
        expected.sort();
        assert_eq!(work_media(&ws), expected);
    }

    #[test]
    fn generated_images_need_an_identical_copy_outside_work() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path();
        make(root, "gen/a/.DS_Store", b"finder", true);
        make(root, "docs/.DS_Store", b"finder", false);
        let copied = make(root, "gen/a/copied.png", b"same bytes", true);
        make(root, "gen/a/unique.png", b"only here", true);
        let work_only = make(root, "gen/b/work-only.png", b"in work only", true);
        make(root, "docs/project/delivered.png", b"same bytes", false);
        make(root, "docs/2026-09-01/t/work/draft.png", b"in work only", false);
        let found = generated_duplicates(&root.join("gen"), &[root.join("docs")], &root.join("docs"));
        assert_eq!(found.iter().map(|(g, _)| g.clone()).collect::<Vec<_>>(), vec![copied]);
        assert!(work_only.exists());
    }
}
