use super::{Harness, HarnessMetadata};
use crate::engine::{add_tree_actions, Action};
use std::path::{Path, PathBuf};

pub struct Antigravity;

fn gemini_root(home: &Path) -> PathBuf {
    home.join(".gemini")
}

fn app_roots(home: &Path) -> Vec<PathBuf> {
    #[cfg(target_os = "windows")]
    {
        return dirs::config_dir()
            .map(|root| {
                vec![
                    root.join("Antigravity"),
                    root.join("Antigravity IDE"),
                ]
            })
            .unwrap_or_default();
    }
    #[cfg(target_os = "macos")]
    {
        let support = home.join("Library/Application Support");
        return vec![support.join("Antigravity"), support.join("Antigravity IDE")];
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        let _ = home;
        Vec::new()
    }
}

impl Harness for Antigravity {
    fn metadata(&self) -> HarnessMetadata {
        HarnessMetadata {
            key: "antigravity",
            name: "Antigravity",
            description_es: "Cachés, registros, scratch y grabaciones temporales",
            description_en: "Caches, logs, scratch data, and temporary recordings",
            logo: "gemini",
            recommended: true,
            protected: true,
        }
    }

    fn allowed_roots(&self, home: &Path) -> Vec<PathBuf> {
        let mut roots = vec![gemini_root(home)];
        roots.extend(app_roots(home));
        roots
    }

    fn is_available(&self, home: &Path) -> bool {
        let gemini = gemini_root(home);
        ["antigravity", "antigravity-cli", "antigravity-ide"]
            .iter()
            .any(|variant| gemini.join(variant).exists())
            || app_roots(home).iter().any(|root| root.exists())
    }

    fn plan(&self, home: &Path, actions: &mut Vec<Action>, warnings: &mut Vec<String>) {
        let root = gemini_root(home);
        for variant in ["antigravity", "antigravity-cli", "antigravity-ide"] {
            for leaf in ["log", "crashes", "cache", "scratch"] {
                add_tree_actions(
                    actions,
                    &root.join(variant).join(leaf),
                    "antigravity",
                    "regenerable Antigravity runtime data",
                );
            }
        }

        add_tree_actions(
            actions,
            &root.join("antigravity-ide/browser_recordings"),
            "antigravity",
            "one-use Antigravity browser recording",
        );

        for app in app_roots(home) {
            add_tree_actions(
                actions,
                &app.join("logs"),
                "antigravity",
                "regenerable Antigravity desktop logs",
            );
        }

        warnings.push(
            "Antigravity conversation databases remain protected in the native app; this category removes only regenerable runtime artifacts"
                .into(),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gemini_root_matches_cli_layout() {
        let home = Path::new("/home/tester");
        assert_eq!(gemini_root(home), home.join(".gemini"));
    }
}
