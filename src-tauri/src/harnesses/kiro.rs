use super::{Harness, HarnessMetadata};
use crate::engine::{add_tree_actions, Action};
use std::path::{Path, PathBuf};

/// Kiro is a VS Code fork; a public issue on kirodotdev/Kiro confirms chat
/// history lives under `User/globalStorage/kiro.kiroagent` and can grow to
/// 13GB+ with no built-in cleanup. This harness never touches that data —
/// only the standard Electron/VS Code-style cache directories alongside it.
pub struct Kiro;

const CACHE_LEAVES: [&str; 7] = [
    "Cache",
    "Code Cache",
    "CachedData",
    "GPUCache",
    "blob_storage",
    "logs",
    "CrashDumps",
];

fn root_with(home: &Path, config_dir: Option<&Path>) -> PathBuf {
    if let Some(roaming) = config_dir {
        return roaming.join("Kiro");
    }
    home.join("Library/Application Support/kiro")
}

fn root(home: &Path) -> PathBuf {
    #[cfg(target_os = "windows")]
    {
        root_with(home, dirs::config_dir().as_deref())
    }
    #[cfg(not(target_os = "windows"))]
    {
        root_with(home, None)
    }
}

impl Harness for Kiro {
    fn metadata(&self) -> HarnessMetadata {
        HarnessMetadata {
            key: "kiro",
            name: "Kiro",
            description_es:
                "Beta: cachés regenerables del editor Kiro; el historial de chat no se toca",
            description_en:
                "Beta: regenerable Kiro editor caches; chat history is not touched",
            logo: "kiro",
            recommended: false,
            protected: true,
        }
    }

    fn allowed_roots(&self, home: &Path) -> Vec<PathBuf> {
        vec![root(home)]
    }

    fn plan(&self, home: &Path, actions: &mut Vec<Action>, warnings: &mut Vec<String>) {
        let root = root(home);
        for leaf in CACHE_LEAVES {
            add_tree_actions(
                actions,
                &root.join(leaf),
                "kiro",
                "regenerable Kiro editor cache",
            );
        }
        warnings.push(
            "Kiro support is in beta: only regenerable editor caches are removed. Chat history (User/globalStorage/kiro.kiroagent), which is usually the largest consumer of Kiro's disk usage, is never touched by this category."
                .into(),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn windows_root_uses_roaming_appdata_kiro_folder() {
        let home = Path::new("/home/tester");
        let roaming = Path::new("/roaming");
        assert_eq!(root_with(home, Some(roaming)), roaming.join("Kiro"));
    }

    #[test]
    fn fallback_root_uses_macos_application_support_lowercase() {
        let home = Path::new("/home/tester");
        assert_eq!(
            root_with(home, None),
            home.join("Library/Application Support/kiro")
        );
    }
}
