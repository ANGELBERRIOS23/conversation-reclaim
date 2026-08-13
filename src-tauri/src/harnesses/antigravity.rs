use super::{Harness, HarnessMetadata};
use crate::engine::{add_tree_actions, Action};
use std::path::Path;

pub struct Antigravity;

impl Harness for Antigravity {
    fn metadata(&self) -> HarnessMetadata {
        HarnessMetadata {
            key: "antigravity",
            name: "Antigravity",
            description_es: "Grabaciones del navegador y datos temporales",
            description_en: "Browser recordings and temporary data",
            logo: "gemini",
            recommended: true,
            protected: true,
        }
    }

    fn allowed_roots(&self, home: &Path) -> Vec<std::path::PathBuf> {
        vec![home.join(".gemini")]
    }

    fn plan(&self, home: &Path, actions: &mut Vec<Action>, _warnings: &mut Vec<String>) {
        let root = home.join(".gemini");
        for leaf in [
            "antigravity/scratch",
            "antigravity-cli/scratch",
            "antigravity-ide/scratch",
            "antigravity-ide/browser_recordings",
        ] {
            add_tree_actions(
                actions,
                &root.join(leaf),
                "antigravity",
                "one-use Antigravity artifact",
            );
        }
    }
}
