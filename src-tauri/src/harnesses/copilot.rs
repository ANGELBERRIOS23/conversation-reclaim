use super::{Harness, HarnessMetadata};
use crate::engine::{add_tree_actions, Action};
use std::path::{Path, PathBuf};

/// GitHub Copilot Chat has no storage of its own: it runs as an extension
/// inside VS Code and its sessions live under VS Code's own `User/
/// workspaceStorage/*/chatSessions` directory. This harness only ever
/// touches VS Code's regenerable editor caches, never that chat data.
pub struct Copilot;

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
        return roaming.join("Code");
    }
    home.join("Library/Application Support/Code")
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

impl Harness for Copilot {
    fn metadata(&self) -> HarnessMetadata {
        HarnessMetadata {
            key: "copilot",
            name: "GitHub Copilot (VS Code)",
            description_es:
                "Beta: cachés regenerables de VS Code, donde vive Copilot Chat; tus chats no se tocan",
            description_en:
                "Beta: regenerable VS Code caches where Copilot Chat lives; your chats are not touched",
            logo: "copilot",
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
                "copilot",
                "regenerable VS Code cache",
            );
        }
        warnings.push(
            "GitHub Copilot support is in beta: only regenerable VS Code caches are removed. Copilot Chat sessions (workspaceStorage/*/chatSessions) are never touched by this category."
                .into(),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn windows_root_uses_roaming_appdata_code_folder() {
        let home = Path::new("/home/tester");
        let roaming = Path::new("/roaming");
        assert_eq!(root_with(home, Some(roaming)), roaming.join("Code"));
    }

    #[test]
    fn fallback_root_uses_macos_application_support() {
        let home = Path::new("/home/tester");
        assert_eq!(
            root_with(home, None),
            home.join("Library/Application Support/Code")
        );
    }
}
