use super::{Harness, HarnessMetadata};
use crate::engine::{add_tree_actions, Action};
use std::path::{Path, PathBuf};

/// Layout verified directly against a real ZCode installation (2026-08-15)
/// before the user uninstalled it — higher confidence than Trae, but still
/// newer and less community-verified than Claude/Codex/OpenCode, so this
/// stays beta. Conversation rollouts, the session database, workspace
/// files, credentials, and network certificates are intentionally excluded.
pub struct ZCode;

const CLI_CACHE_LEAVES: [&str; 6] = [
    "cli/image-cache",
    "cli/exec",
    "cli/log",
    "cli/plugins/cache",
    "v2/crash",
    "v2/logs",
];

const APP_CACHE_LEAVES: [&str; 6] = [
    "session/Cache",
    "session/Code Cache",
    "session/GPUCache",
    "session/DawnGraphiteCache",
    "session/DawnWebGPUCache",
    "session/blob_storage",
];

fn cli_root(home: &Path) -> PathBuf {
    home.join(".zcode")
}

fn app_root_with(home: &Path, config_dir: Option<&Path>) -> PathBuf {
    if let Some(roaming) = config_dir {
        return roaming.join("ZCode");
    }
    home.join("Library/Application Support/ZCode")
}

fn app_root(home: &Path) -> PathBuf {
    #[cfg(target_os = "windows")]
    {
        app_root_with(home, dirs::config_dir().as_deref())
    }
    #[cfg(not(target_os = "windows"))]
    {
        app_root_with(home, None)
    }
}

impl Harness for ZCode {
    fn metadata(&self) -> HarnessMetadata {
        HarnessMetadata {
            key: "zcode",
            name: "ZCode",
            description_es:
                "Beta: cachés, registros y grabaciones de shell regenerables de ZCode; conversaciones, credenciales y certificados no se tocan",
            description_en:
                "Beta: regenerable ZCode caches, logs, and shell recordings; conversations, credentials, and certificates are not touched",
            logo: "zcode",
            recommended: false,
            protected: true,
        }
    }

    fn allowed_roots(&self, home: &Path) -> Vec<PathBuf> {
        vec![cli_root(home), app_root(home)]
    }

    fn plan(&self, home: &Path, actions: &mut Vec<Action>, warnings: &mut Vec<String>) {
        let cli = cli_root(home);
        for leaf in CLI_CACHE_LEAVES {
            add_tree_actions(
                actions,
                &cli.join(leaf),
                "zcode",
                "regenerable ZCode cache, log, or shell recording",
            );
        }

        let app = app_root(home);
        for leaf in APP_CACHE_LEAVES {
            add_tree_actions(
                actions,
                &app.join(leaf),
                "zcode",
                "regenerable ZCode desktop cache",
            );
        }

        warnings.push(
            "ZCode support is in beta: only regenerable caches, logs, and shell recordings are removed. Conversation rollouts, the session database, workspace files, credentials, and network certificates are never touched by this category."
                .into(),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cli_root_matches_verified_layout() {
        let home = Path::new("/home/tester");
        assert_eq!(cli_root(home), home.join(".zcode"));
    }

    #[test]
    fn windows_app_root_uses_roaming_appdata() {
        let home = Path::new("/home/tester");
        let roaming = Path::new("/roaming");
        assert_eq!(app_root_with(home, Some(roaming)), roaming.join("ZCode"));
    }

    #[test]
    fn fallback_app_root_uses_macos_application_support() {
        let home = Path::new("/home/tester");
        assert_eq!(
            app_root_with(home, None),
            home.join("Library/Application Support/ZCode")
        );
    }
}
