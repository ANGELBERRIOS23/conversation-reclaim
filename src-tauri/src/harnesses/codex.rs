use super::{Harness, HarnessMetadata};
use crate::engine::{add_tree_actions, Action};
use std::path::Path;

pub struct Codex;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn codex_plan_never_touches_session_rollouts() {
        let temp = tempfile::tempdir().unwrap();
        let codex = temp.path().join(".codex");
        for folder in ["sessions/2026/09/23", "archived_sessions", "cache"] {
            std::fs::create_dir_all(codex.join(folder)).unwrap();
        }
        std::fs::write(
            codex.join("sessions/2026/09/23/rollout-test.jsonl"),
            b"{\"type\":\"session_meta\"}\n{\"type\":\"compacted\"}\n",
        )
        .unwrap();
        std::fs::write(
            codex.join("archived_sessions/rollout-test.jsonl"),
            b"{\"type\":\"session_meta\"}\n{\"type\":\"compacted\"}\n",
        )
        .unwrap();
        std::fs::write(codex.join("cache/rebuildable"), b"cache").unwrap();
        let mut actions = Vec::new();
        let mut warnings = Vec::new();
        Codex.plan(temp.path(), &mut actions, &mut warnings);
        assert_eq!(actions.len(), 1);
        let action = serde_json::to_value(&actions[0]).unwrap();
        let path = std::path::Path::new(action["path"].as_str().unwrap());
        assert!(path.starts_with(codex.join("cache")));
    }
}

impl Harness for Codex {
    fn metadata(&self) -> HarnessMetadata {
        HarnessMetadata {
            key: "codex",
            name: "Codex",
            description_es: "Solo cachés regenerables; sesiones protegidas",
            description_en: "Regenerable caches only; sessions protected",
            logo: "codex",
            recommended: true,
            protected: true,
        }
    }

    fn allowed_roots(&self, home: &Path) -> Vec<std::path::PathBuf> {
        vec![home.join(".codex")]
    }

    fn plan(&self, home: &Path, actions: &mut Vec<Action>, warnings: &mut Vec<String>) {
        let root = home.join(".codex");
        add_tree_actions(
            actions,
            &root.join("cache"),
            "codex",
            "regenerable Codex cache",
        );
        warnings.push("Codex session and archived rollouts are protected from cleanup".into());
    }
}
