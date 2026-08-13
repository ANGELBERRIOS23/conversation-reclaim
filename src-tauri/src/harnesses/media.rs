use super::{Harness, HarnessMetadata};
use crate::engine::{collect_files, file_size, Action};
use std::fs;
use std::path::Path;
use std::time::{Duration, SystemTime};

pub struct TemporaryMedia;

const MIN_AGE: Duration = Duration::from_secs(7 * 24 * 60 * 60);

fn is_image(path: &Path) -> bool {
    matches!(
        path.extension()
            .and_then(|value| value.to_str())
            .map(str::to_ascii_lowercase)
            .as_deref(),
        Some("png" | "jpg" | "jpeg" | "webp" | "gif")
    )
}

fn is_old_regular_file(path: &Path) -> bool {
    let Ok(metadata) = fs::symlink_metadata(path) else {
        return false;
    };
    metadata.file_type().is_file()
        && !metadata.file_type().is_symlink()
        && metadata
            .modified()
            .ok()
            .and_then(|modified| SystemTime::now().duration_since(modified).ok())
            .is_some_and(|age| age >= MIN_AGE)
}

fn is_antigravity_temp_media(path: &Path) -> bool {
    is_image(path)
        && path.ancestors().any(|part| {
            matches!(
                part.file_name().and_then(|name| name.to_str()),
                Some(".tempmediaStorage" | "tempmediaStorage")
            )
        })
}

fn add_candidate(actions: &mut Vec<Action>, path: std::path::PathBuf, reason: &str) {
    if is_old_regular_file(&path) {
        let size = file_size(&path);
        if size > 0 {
            actions.push(Action::delete("media", path, size, reason));
        }
    }
}

impl Harness for TemporaryMedia {
    fn metadata(&self) -> HarnessMetadata {
        HarnessMetadata {
            key: "media",
            name: "Temporary media",
            description_es:
                "Adjuntos temporales de más de 7 días; puede quitar vistas previas antiguas",
            description_en: "Temporary attachments older than 7 days; old previews may disappear",
            logo: "media",
            recommended: false,
            protected: true,
        }
    }

    fn allowed_roots(&self, home: &Path) -> Vec<std::path::PathBuf> {
        vec![std::env::temp_dir(), home.join(".gemini")]
    }

    fn plan(&self, home: &Path, actions: &mut Vec<Action>, warnings: &mut Vec<String>) {
        let temp = std::env::temp_dir();
        if let Ok(entries) = fs::read_dir(&temp) {
            for entry in entries.flatten() {
                let path = entry.path();
                let matches_codex = path
                    .file_name()
                    .and_then(|name| name.to_str())
                    .is_some_and(|name| name.starts_with("codex-clipboard-"));
                if matches_codex && is_image(&path) {
                    add_candidate(actions, path, "old Codex clipboard attachment");
                }
            }
        }

        for root in [
            home.join(".gemini/antigravity/brain"),
            home.join(".gemini/antigravity-cli/brain"),
            home.join(".gemini/antigravity-ide/brain"),
        ] {
            for path in collect_files(&root)
                .into_iter()
                .filter(|path| is_antigravity_temp_media(path))
            {
                add_candidate(actions, path, "old Antigravity temporary media");
            }
        }
        warnings.push("Temporary media is optional: deleting it can remove image previews from old conversations".into());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_explicit_temp_media_directories_match() {
        assert!(is_antigravity_temp_media(Path::new(
            "brain/id/.tempmediaStorage/a.png"
        )));
        assert!(is_antigravity_temp_media(Path::new(
            "brain/tempmediaStorage/a.webp"
        )));
        assert!(!is_antigravity_temp_media(Path::new(
            "brain/id/deliverables/a.png"
        )));
        assert!(!is_antigravity_temp_media(Path::new(
            "brain/id/.tempmediaStorage/a.txt"
        )));
    }
}
