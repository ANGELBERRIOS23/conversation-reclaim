use super::{Harness, HarnessMetadata};
use crate::engine::{add_trim_actions, claude_sidechain_valid, collect_files, file_size, Action};
use serde_json::Value;
use std::path::Path;

pub struct Claude;

fn marker(value: &Value) -> bool {
    value.get("type").and_then(Value::as_str) == Some("summary")
        || value.get("isSummary").and_then(Value::as_bool) == Some(true)
        || value.get("compactMetadata").is_some_and(Value::is_object)
        || (value.get("type").and_then(Value::as_str) == Some("system")
            && value.get("subtype").and_then(Value::as_str) == Some("compact_boundary"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn only_structural_markers_match() {
        assert!(marker(&json!({"type":"summary"})));
        assert!(marker(
            &json!({"type":"system","subtype":"compact_boundary"})
        ));
        assert!(!marker(&json!({"message":"compact_boundary"})));
    }
}

impl Harness for Claude {
    fn metadata(&self) -> HarnessMetadata {
        HarnessMetadata {
            key: "claude",
            name: "Claude Code",
            description_es: "Historial compactado y subagentes cerrados",
            description_en: "Compacted history and closed subagents",
            logo: "claude",
            recommended: true,
            protected: true,
        }
    }

    fn allowed_roots(&self, home: &Path) -> Vec<std::path::PathBuf> {
        vec![home.join(".claude")]
    }

    fn plan(&self, home: &Path, actions: &mut Vec<Action>, _warnings: &mut Vec<String>) {
        let root = home.join(".claude/projects");
        add_trim_actions(actions, &root, "claude", marker, false);
        for path in collect_files(&root) {
            if path.extension().and_then(|s| s.to_str()) == Some("jsonl")
                && claude_sidechain_valid(&path)
            {
                actions.push(Action::delete(
                    "claude",
                    path.clone(),
                    file_size(&path),
                    "validated closed Claude sidechain",
                ));
            }
        }
    }
}
