use super::{Harness, HarnessMetadata};
use crate::engine::{add_tree_actions, add_trim_actions, Action};
use serde_json::Value;
use std::path::Path;

pub struct Codex;

fn marker(value: &Value) -> bool {
    value.get("type").and_then(Value::as_str) == Some("compacted")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn only_top_level_marker_matches() {
        assert!(marker(&json!({"type":"compacted"})));
        assert!(!marker(&json!({"payload":{"type":"compacted"}})));
    }
}

impl Harness for Codex {
    fn metadata(&self) -> HarnessMetadata {
        HarnessMetadata {
            key: "codex",
            name: "Codex",
            description_es: "Cachés y sesiones compactadas inactivas",
            description_en: "Caches and inactive compacted sessions",
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
        add_trim_actions(actions, &root.join("sessions"), "codex", marker, true);
        add_trim_actions(
            actions,
            &root.join("archived_sessions"),
            "codex",
            marker,
            true,
        );
        add_tree_actions(
            actions,
            &root.join("cache"),
            "codex",
            "regenerable Codex cache",
        );
        warnings.push("Active and child Codex sessions are excluded from native cleanup".into());
    }
}
