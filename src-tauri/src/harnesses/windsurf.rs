use super::{Harness, HarnessMetadata};
use crate::engine::{add_tree_actions, Action};
use std::path::{Path, PathBuf};

/// Windsurf is a VS Code fork with the same SQLite-backed chat storage
/// convention as Cursor. This harness only touches the standard Electron/
/// VS Code-style cache directories, never the chat databases themselves.
pub struct Windsurf;

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
        return roaming.join("Windsurf");
    }
    home.join("Library/Application Support/Windsurf")
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

impl Harness for Windsurf {
    fn metadata(&self) -> HarnessMetadata {
        HarnessMetadata {
            key: "windsurf",
            name: "Windsurf",
            description_es:
                "Beta: cachés regenerables del editor Windsurf; tus chats no se tocan",
            description_en:
                "Beta: regenerable Windsurf editor caches; your chats are not touched",
            logo: "windsurf",
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
                "windsurf",
                "regenerable Windsurf editor cache",
            );
        }
        warnings.push(
            "Windsurf support is in beta: only regenerable editor caches are removed. Chat history (state.vscdb) is never touched by this category."
                .into(),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn windows_root_uses_roaming_appdata_windsurf_folder() {
        let home = Path::new("/home/tester");
        let roaming = Path::new("/roaming");
        assert_eq!(root_with(home, Some(roaming)), roaming.join("Windsurf"));
    }

    #[test]
    fn fallback_root_uses_macos_application_support() {
        let home = Path::new("/home/tester");
        assert_eq!(
            root_with(home, None),
            home.join("Library/Application Support/Windsurf")
        );
    }
}
