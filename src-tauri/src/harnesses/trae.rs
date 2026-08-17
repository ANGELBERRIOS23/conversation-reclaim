use super::{Harness, HarnessMetadata};
use crate::engine::{add_tree_actions, Action};
use std::path::{Path, PathBuf};

/// Trae's storage layout is not officially documented. Unlike the other
/// harnesses, these roots and cache leaf names come from third-party
/// reverse-engineering projects, not verified against a live installation.
/// Keep this harness read-conservative: only well-known Electron/VS
/// Code-style cache directories are ever added to the cleanup plan.
pub struct Trae;

const CACHE_LEAVES: [&str; 7] = [
    "Cache",
    "Code Cache",
    "CachedData",
    "GPUCache",
    "blob_storage",
    "logs",
    "CrashDumps",
];

fn roots_with(home: &Path, config_dir: Option<&Path>, data_local_dir: Option<&Path>) -> Vec<PathBuf> {
    let mut roots = vec![
        home.join(".trae"),
        home.join("Library/Application Support/Trae"),
    ];
    if let Some(roaming) = config_dir {
        roots.push(roaming.join("Trae"));
    }
    if let Some(local) = data_local_dir {
        roots.push(local.join("Trae"));
    }
    roots.dedup();
    roots
}

fn roots(home: &Path) -> Vec<PathBuf> {
    #[cfg(target_os = "windows")]
    {
        roots_with(
            home,
            dirs::config_dir().as_deref(),
            dirs::data_local_dir().as_deref(),
        )
    }
    #[cfg(not(target_os = "windows"))]
    {
        roots_with(home, None, None)
    }
}

impl Harness for Trae {
    fn metadata(&self) -> HarnessMetadata {
        HarnessMetadata {
            key: "trae",
            name: "Trae",
            description_es:
                "Beta: cachés regenerables de Trae; sesiones, credenciales y certificados no se tocan",
            description_en:
                "Beta: regenerable Trae caches; sessions, credentials, and certificates are not touched",
            logo: "trae",
            recommended: false,
            protected: true,
        }
    }

    fn allowed_roots(&self, home: &Path) -> Vec<PathBuf> {
        roots(home)
    }

    fn plan(&self, home: &Path, actions: &mut Vec<Action>, warnings: &mut Vec<String>) {
        for root in roots(home) {
            for leaf in CACHE_LEAVES {
                add_tree_actions(actions, &root.join(leaf), "trae", "regenerable Trae cache");
            }
        }
        warnings.push(
            "Trae support is in beta and less verified than other integrations (no official storage documentation exists): only well-known Electron/VS Code-style caches are removed. Chat sessions, credentials, and certificates are never touched by this category."
                .into(),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn windows_roots_include_roaming_and_local_appdata() {
        let home = Path::new("/home/tester");
        let roaming = Path::new("/roaming");
        let local = Path::new("/local");
        let found = roots_with(home, Some(roaming), Some(local));
        assert!(found.contains(&roaming.join("Trae")));
        assert!(found.contains(&local.join("Trae")));
        assert!(found.contains(&home.join(".trae")));
    }

    #[test]
    fn macos_fallback_includes_application_support() {
        let home = Path::new("/home/tester");
        let found = roots_with(home, None, None);
        assert!(found.contains(&home.join("Library/Application Support/Trae")));
        assert!(found.contains(&home.join(".trae")));
    }
}
