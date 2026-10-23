use super::{Harness, HarnessMetadata};
use crate::engine::{age_cutoff, collect_files, file_size, is_older_than_cutoff, Action};
use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

fn version_key(name: &str) -> Vec<u64> {
    name.split(|c: char| !c.is_ascii_digit())
        .filter(|part| !part.is_empty())
        .filter_map(|part| part.parse().ok())
        .collect()
}

fn real(path: &Path) -> PathBuf {
    fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
}

/// Todas menos la más nueva y las de `keep` (comparadas por ruta real).
fn all_but_newest(mut entries: Vec<PathBuf>, keep: &[PathBuf]) -> Vec<PathBuf> {
    entries.sort_by_key(|p| version_key(&p.file_name().unwrap_or_default().to_string_lossy()));
    let mut kept: HashSet<PathBuf> = keep.iter().map(|p| real(p)).collect();
    if let Some(newest) = entries.last() {
        kept.insert(real(newest));
    }
    entries.into_iter().filter(|p| !kept.contains(&real(p))).collect()
}

fn entries(dir: &Path, want_dirs: bool) -> Vec<PathBuf> {
    let Ok(read) = fs::read_dir(dir) else {
        return Vec::new();
    };
    read.filter_map(Result::ok)
        .filter(|e| {
            e.file_type()
                .is_ok_and(|t| !t.is_symlink() && (if want_dirs { t.is_dir() } else { t.is_file() }))
        })
        .map(|e| e.path())
        .filter(|p| !version_key(&p.file_name().unwrap_or_default().to_string_lossy()).is_empty())
        .collect()
}

fn running_commands() -> Vec<String> {
    if cfg!(target_os = "windows") {
        return Vec::new();
    }
    Command::new("ps")
        .args(["-axo", "command="])
        .output()
        .map(|out| String::from_utf8_lossy(&out.stdout).lines().map(str::to_owned).collect())
        .unwrap_or_default()
}

fn claude_desktop_code() -> Option<PathBuf> {
    dirs::config_dir().map(|config| config.join("Claude").join("claude-code"))
}

/// Versiones viejas de Claude Code y Codex, y restos que ya no sirven.
/// Devuelve (ruta, es_carpeta, motivo).
pub(crate) fn candidates(home: &Path, desktop: Option<&Path>, running: &[String]) -> Vec<(PathBuf, bool, &'static str)> {
    let in_use = |p: &Path| running.iter().any(|cmd| cmd.starts_with(&*p.to_string_lossy()));
    let mut found = Vec::new();

    let versions = home.join(".local/share/claude/versions");
    let link = home.join(".local/bin/claude");
    let keep: Vec<PathBuf> = if link.exists() { vec![link] } else { Vec::new() };
    for path in all_but_newest(entries(&versions, false), &keep) {
        if !in_use(&path) {
            found.push((path, false, "old Claude Code (terminal) version"));
        }
    }

    if let Some(desktop) = desktop {
        for path in all_but_newest(entries(desktop, true), &[]) {
            if !in_use(&path) {
                found.push((path, true, "old Claude Code (desktop app) version"));
            }
        }
    }

    let releases = home.join(".codex/packages/standalone/releases");
    let current = home.join(".codex/packages/standalone/current");
    let keep: Vec<PathBuf> = if current.exists() { vec![current] } else { Vec::new() };
    for path in all_but_newest(entries(&releases, true), &keep) {
        if !in_use(&path) {
            found.push((path, true, "old Codex version"));
        }
    }

    let cutoff = age_cutoff();
    if let Ok(sessions) = fs::read_dir(home.join(".claude/file-history")) {
        for session in sessions.filter_map(Result::ok) {
            if !session.file_type().is_ok_and(|t| t.is_dir()) {
                continue;
            }
            let newest = collect_files(&session.path())
                .iter()
                .filter_map(|f| fs::metadata(f).and_then(|m| m.modified()).ok())
                .max();
            if newest.is_some_and(|time| time < cutoff) {
                found.push((session.path(), true, "old Claude Code restore point"));
            }
        }
    }

    for root in [home.join(".codex/sessions"), home.join(".codex/archived_sessions")] {
        for path in collect_files(&root) {
            if path.to_string_lossy().contains(".bak-") && is_older_than_cutoff(&path) {
                found.push((path, false, "old Codex repair backup"));
            }
        }
    }
    found
}

pub struct OldVersions;

impl Harness for OldVersions {
    fn metadata(&self) -> HarnessMetadata {
        HarnessMetadata {
            key: "oldVersions",
            name: "Old versions and leftovers",
            description_es: "Versiones viejas de Claude Code y Codex y puntos de restauración; la que usas se queda",
            description_en: "Old Claude Code and Codex versions and restore points; the one in use stays",
            logo: "versions",
            recommended: false,
            protected: true,
        }
    }

    fn allowed_roots(&self, home: &Path) -> Vec<PathBuf> {
        let mut roots = vec![
            home.join(".local/share/claude/versions"),
            home.join(".codex/packages/standalone/releases"),
            home.join(".claude/file-history"),
            home.join(".codex/sessions"),
            home.join(".codex/archived_sessions"),
        ];
        roots.extend(claude_desktop_code());
        roots
    }

    fn plan(&self, home: &Path, actions: &mut Vec<Action>, _warnings: &mut Vec<String>) {
        let desktop = claude_desktop_code();
        for (path, is_dir, reason) in candidates(home, desktop.as_deref(), &running_commands()) {
            if is_dir {
                actions.push(Action::delete_tree("oldVersions", path, reason));
            } else {
                let size = file_size(&path);
                actions.push(Action::delete("oldVersions", path, size, reason));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs::{File, FileTimes};
    use std::time::{Duration, SystemTime};

    fn make(root: &Path, relative: &str, old: bool) -> PathBuf {
        let path = root.join(relative);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, b"x").unwrap();
        if old {
            let stamp = SystemTime::now() - Duration::from_secs(30 * 86_400);
            File::options().write(true).open(&path).unwrap().set_times(FileTimes::new().set_modified(stamp)).unwrap();
        }
        path
    }

    #[test]
    fn keeps_current_and_newest_and_takes_old_leftovers() {
        let temp = tempfile::tempdir().unwrap();
        let home = temp.path();
        for name in ["2.1.280", "2.1.281", "2.1.288", "2.1.290"] {
            make(home, &format!(".local/share/claude/versions/{name}"), false);
        }
        fs::create_dir_all(home.join(".local/bin")).unwrap();
        #[cfg(unix)]
        std::os::unix::fs::symlink(home.join(".local/share/claude/versions/2.1.288"), home.join(".local/bin/claude")).unwrap();
        for name in ["0.150.1-aarch64", "0.160.0-aarch64"] {
            make(home, &format!(".codex/packages/standalone/releases/{name}/codex"), false);
        }
        #[cfg(unix)]
        std::os::unix::fs::symlink(
            home.join(".codex/packages/standalone/releases/0.150.1-aarch64"),
            home.join(".codex/packages/standalone/current"),
        )
        .unwrap();
        make(home, "desktop/2.1.304/x", false);
        make(home, "desktop/2.1.308/x", false);
        make(home, ".claude/file-history/old-session/a@v1", true);
        make(home, ".claude/file-history/new-session/a@v1", false);
        make(home, ".codex/sessions/2026/09/02/r.jsonl.bak-presanear", true);
        make(home, ".codex/sessions/2026/09/02/r.jsonl", true);

        let names: HashSet<String> = candidates(home, Some(&home.join("desktop")), &[])
            .into_iter()
            .map(|(p, _, _)| p.file_name().unwrap().to_string_lossy().into_owned())
            .collect();
        let mut expected: HashSet<String> = ["2.1.280", "2.1.281", "2.1.304", "old-session", "r.jsonl.bak-presanear"]
            .into_iter()
            .map(String::from)
            .collect();
        if !cfg!(unix) {
            // Sin enlaces, solo se queda la más nueva de cada una.
            expected.insert("2.1.288".into());
            expected.insert("0.150.1-aarch64".into());
        }
        assert_eq!(names, expected);
    }

    #[test]
    fn running_versions_are_kept() {
        let temp = tempfile::tempdir().unwrap();
        let home = temp.path();
        make(home, "desktop/2.1.304/claude", false);
        make(home, "desktop/2.1.308/claude", false);
        // Ruta armada por partes para que use el separador del sistema.
        let old = home.join("desktop").join("2.1.304").join("claude");
        let running = vec![format!("{} --flag", old.display())];
        assert!(candidates(home, Some(&home.join("desktop")), &running).is_empty());
    }
}
