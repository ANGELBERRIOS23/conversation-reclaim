use super::{Harness, HarnessMetadata};
use crate::engine::{add_tree_actions, Action};
use std::path::Path;

pub struct OpenCode;

fn root(home: &Path) -> std::path::PathBuf {
    if cfg!(target_os = "windows") {
        dirs::data_local_dir()
            .unwrap_or_else(|| home.to_path_buf())
            .join("opencode")
    } else {
        home.join(".local/share/opencode")
    }
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

    fn allowed_roots(&self, home: &Path) -> Vec<std::path::PathBuf> {
        vec![root(home)]
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
