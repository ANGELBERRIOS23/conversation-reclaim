mod antigravity;
mod claude;
mod codex;
mod codex_work;
mod copilot;
mod cursor;
mod images;
mod kiro;
mod media;
mod old_versions;
mod opencode;
mod trae;
mod windsurf;
mod zcode;

pub(crate) use codex_work::{identical_copies, is_generated_image, is_work_dir, search_roots as user_search_roots};
pub(crate) use images::rewrite_without_images;

use crate::engine::Action;
use serde::Serialize;
use std::path::Path;

#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HarnessMetadata {
    pub key: &'static str,
    pub name: &'static str,
    pub description_es: &'static str,
    pub description_en: &'static str,
    pub logo: &'static str,
    pub recommended: bool,
    pub protected: bool,
}

/// Contract for a cleanup integration.
///
/// Adding a harness requires one new module implementing this trait and one
/// entry in `registered_harnesses`. The scanner, totals, selection UI,
/// manifests, and cleanup executor discover it automatically.
pub trait Harness: Send + Sync {
    fn metadata(&self) -> HarnessMetadata;
    fn allowed_roots(&self, home: &Path) -> Vec<std::path::PathBuf>;
    fn plan(&self, home: &Path, actions: &mut Vec<Action>, warnings: &mut Vec<String>);

    fn is_available(&self, home: &Path) -> bool {
        self.allowed_roots(home).iter().any(|root| root.exists())
    }
}

pub fn registered_harnesses() -> Vec<Box<dyn Harness>> {
    vec![
        Box::new(claude::Claude),
        Box::new(codex::Codex),
        Box::new(media::TemporaryMedia),
        Box::new(images::ConversationImages),
        Box::new(codex_work::CodexWork),
        Box::new(old_versions::OldVersions),
        Box::new(opencode::OpenCode),
        Box::new(antigravity::Antigravity),
        Box::new(cursor::Cursor),
        Box::new(copilot::Copilot),
        Box::new(trae::Trae),
        Box::new(zcode::ZCode),
        Box::new(kiro::Kiro),
        Box::new(windsurf::Windsurf),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    /// Escaneo real de la carpeta del usuario (solo lee). Se corre a mano:
    /// `cargo test real_home_scan -- --ignored --nocapture`
    #[test]
    #[ignore]
    fn real_home_scan() {
        let started = std::time::Instant::now();
        let days = std::env::var("RECLAIM_DAYS").ok().and_then(|d| d.parse().ok());
        let result = crate::engine::scan_storage(days).unwrap();
        println!("{}", serde_json::to_string_pretty(&result).unwrap());
        println!("scan took {:?}", started.elapsed());
    }

    #[test]
    fn registry_keys_are_unique_and_complete() {
        let harnesses = registered_harnesses();
        let keys: HashSet<_> = harnesses.iter().map(|h| h.metadata().key).collect();
        assert_eq!(keys.len(), harnesses.len());
        for harness in harnesses {
            let metadata = harness.metadata();
            assert!(!metadata.name.is_empty());
            assert!(!metadata.description_es.is_empty());
            assert!(!metadata.description_en.is_empty());
            assert!(!metadata.logo.is_empty());
        }
    }
}
