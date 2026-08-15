use super::{Harness, HarnessMetadata};
use crate::engine::{add_tree_actions, Action};
use std::path::{Path, PathBuf};

pub struct OpenCode;

fn root_candidates_with(
    home: &Path,
    data_local: Option<&Path>,
    config: Option<&Path>,
) -> Vec<PathBuf> {
    let mut candidates = vec![home.join(".local/share/opencode")];
    if let Some(local) = data_local {
        candidates.push(local.join("opencode/data"));
        candidates.push(local.join("opencode"));
    }
    if let Some(roaming) = config {
        candidates.push(roaming.join("opencode"));
    }
    candidates.dedup();
    candidates
}

fn root_candidates(home: &Path) -> Vec<PathBuf> {
    #[cfg(target_os = "windows")]
    {
        let local = dirs::data_local_dir();
        let roaming = dirs::config_dir();
        return root_candidates_with(home, local.as_deref(), roaming.as_deref());
    }
    #[cfg(not(target_os = "windows"))]
    {
        root_candidates_with(home, None, None)
    }
}

fn root(home: &Path) -> PathBuf {
    let candidates = root_candidates(home);
    candidates
        .iter()
        .find(|candidate| candidate.exists())
        .cloned()
        .unwrap_or_else(|| candidates[0].clone())
}

impl Harness for OpenCode {
    fn metadata(&self) -> HarnessMetadata {
        HarnessMetadata {
            key: "opencode",
            name: "OpenCode",
            description_es: "Archivos temporales, registros y snapshots",
            description_en: "Temporary files, logs, and snapshots",
            logo: "opencode",
            recommended: false,
            protected: true,
        }
    }

    fn allowed_roots(&self, home: &Path) -> Vec<PathBuf> {
        root_candidates(home)
    }

    fn is_available(&self, home: &Path) -> bool {
        root_candidates(home).iter().any(|candidate| candidate.exists())
    }

    fn plan(&self, home: &Path, actions: &mut Vec<Action>, warnings: &mut Vec<String>) {
        let root = root(home);
        for leaf in ["snapshot", "tool-output", "log"] {
            add_tree_actions(
                actions,
                &root.join(leaf),
                "opencode",
                "regenerable OpenCode temporary data",
            );
        }
        warnings.push("OpenCode conversation databases are protected in the native app".into());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn modern_cross_platform_location_is_always_first() {
        let home = Path::new("/home/tester");
        let local = Path::new("/local");
        let roaming = Path::new("/roaming");
        let candidates = root_candidates_with(home, Some(local), Some(roaming));

        assert_eq!(candidates[0], home.join(".local/share/opencode"));
        assert_eq!(candidates[1], local.join("opencode/data"));
        assert_eq!(candidates[2], local.join("opencode"));
        assert_eq!(candidates[3], roaming.join("opencode"));
    }
}
