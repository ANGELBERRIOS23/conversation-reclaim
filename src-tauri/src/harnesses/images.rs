use super::{Harness, HarnessMetadata};
use crate::engine::{collect_files, is_older_than_cutoff, Action};
use serde_json::{json, Map, Value};
use std::fs::{self, File, FileTimes};
use std::io::Write;
use std::path::{Path, PathBuf};

/// Texto que ocupa el lugar de una imagen que veía el modelo.
pub(crate) const PLACEHOLDER: &str = "[imagen quitada por conversation-reclaim]";
/// PNG de 1x1 para las copias que solo usa la interfaz: el registro no
/// cambia de forma.
pub(crate) const TINY_PNG_B64: &str =
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=";
const MIN_IMAGE_CHARS: usize = 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Tool {
    Claude,
    Codex,
}

fn tiny_png_url() -> String {
    format!("data:image/png;base64,{TINY_PNG_B64}")
}

fn long_str(value: Option<&Value>) -> Option<usize> {
    value
        .and_then(Value::as_str)
        .map(str::len)
        .filter(|len| *len > MIN_IMAGE_CHARS)
}

fn claude_image_block_size(block: &Map<String, Value>) -> usize {
    if block.get("type").and_then(Value::as_str) != Some("image") {
        return 0;
    }
    let Some(source) = block.get("source").and_then(Value::as_object) else {
        return 0;
    };
    if source.get("type").and_then(Value::as_str) != Some("base64") {
        return 0;
    }
    long_str(source.get("data")).unwrap_or(0)
}

/// Quita las imágenes en base64 de un registro de Claude Code.
///
/// Los bloques que ve el modelo pasan a texto; las copias de la interfaz
/// (toolUseResult, adjuntos) quedan como PNG de 1x1. Los PDF y las firmas de
/// razonamiento no se tocan. Devuelve los caracteres retirados.
pub(crate) fn strip_claude(value: &mut Value) -> usize {
    let mut removed = 0;
    match value {
        Value::Array(items) => {
            for item in items.iter_mut() {
                let size = item.as_object().map(claude_image_block_size).unwrap_or(0);
                if size > 0 {
                    removed += size;
                    *item = json!({"type": "text", "text": PLACEHOLDER});
                } else {
                    removed += strip_claude(item);
                }
            }
        }
        Value::Object(map) => {
            let keys: Vec<String> = map.keys().cloned().collect();
            for key in keys {
                if key == "source" {
                    if let Some(source) = map.get_mut("source").and_then(Value::as_object_mut) {
                        let is_image = source.get("type").and_then(Value::as_str) == Some("base64")
                            && source
                                .get("media_type")
                                .and_then(Value::as_str)
                                .is_some_and(|m| m.starts_with("image/"));
                        if let (true, Some(len)) = (is_image, long_str(source.get("data"))) {
                            removed += len - TINY_PNG_B64.len();
                            source.insert("data".into(), json!(TINY_PNG_B64));
                            source.insert("media_type".into(), json!("image/png"));
                            continue;
                        }
                    }
                }
                if key == "base64"
                    && map
                        .get("type")
                        .and_then(Value::as_str)
                        .is_some_and(|t| t.starts_with("image/"))
                {
                    if let Some(len) = long_str(map.get("base64")) {
                        removed += len - TINY_PNG_B64.len();
                        map.insert("base64".into(), json!(TINY_PNG_B64));
                        map.insert("type".into(), json!("image/png"));
                        continue;
                    }
                }
                if let Some(child) = map.get_mut(&key) {
                    removed += strip_claude(child);
                }
            }
        }
        _ => {}
    }
    removed
}

fn codex_image_item_size(item: &Map<String, Value>) -> usize {
    if item.get("type").and_then(Value::as_str) != Some("input_image") {
        return 0;
    }
    match item.get("image_url").and_then(Value::as_str) {
        Some(url) if url.starts_with("data:image") && url.len() > MIN_IMAGE_CHARS => url.len(),
        _ => 0,
    }
}

/// Quita las imágenes en base64 de un registro de Codex.
///
/// Las que ve el modelo pasan a `input_text`; cualquier otra `data:image`
/// queda como PNG de 1x1. No toca session_meta ni el orden de las líneas.
pub(crate) fn strip_codex(value: &mut Value) -> usize {
    let mut removed = 0;
    match value {
        Value::Array(items) => {
            for item in items.iter_mut() {
                let size = item.as_object().map(codex_image_item_size).unwrap_or(0);
                if size > 0 {
                    removed += size;
                    *item = json!({"type": "input_text", "text": PLACEHOLDER});
                } else {
                    removed += strip_codex(item);
                }
            }
        }
        Value::Object(map) => {
            for child in map.values_mut() {
                let long_image = child
                    .as_str()
                    .filter(|s| s.starts_with("data:image") && s.len() > MIN_IMAGE_CHARS)
                    .map(str::len);
                if let Some(len) = long_image {
                    let url = tiny_png_url();
                    removed += len - url.len();
                    *child = Value::String(url);
                } else {
                    removed += strip_codex(child);
                }
            }
        }
        _ => {}
    }
    removed
}

fn contains(haystack: &[u8], needle: &[u8]) -> bool {
    haystack.windows(needle.len()).any(|window| window == needle)
}

/// Devuelve el contenido sin imágenes y cuántos caracteres se quitaron.
///
/// Solo se vuelven a escribir las líneas con imágenes; las demás quedan
/// igual byte por byte. Una línea con imagen que no es JSON válido detiene
/// todo (no se toca el archivo).
pub(crate) fn strip_session(data: &[u8], tool: Tool) -> Result<(Vec<u8>, usize), String> {
    let needle: &[u8] = match tool {
        Tool::Claude => b"base64",
        Tool::Codex => b"data:image",
    };
    let mut out = Vec::with_capacity(data.len());
    let mut removed = 0;
    for (index, line) in data.split(|byte| *byte == b'\n').enumerate() {
        if index > 0 {
            out.push(b'\n');
        }
        if !contains(line, needle) {
            out.extend_from_slice(line);
            continue;
        }
        let body = line.strip_suffix(b"\r").unwrap_or(line);
        let mut record: Value = serde_json::from_slice(body)
            .map_err(|_| format!("invalid JSON at line {}", index + 1))?;
        let cut = match tool {
            Tool::Claude => strip_claude(&mut record),
            Tool::Codex => strip_codex(&mut record),
        };
        if cut > 0 {
            removed += cut;
            out.extend(serde_json::to_vec(&record).map_err(|e| e.to_string())?);
        } else {
            out.extend_from_slice(line);
        }
    }
    Ok((out, removed))
}

fn is_compressed(path: &Path) -> bool {
    path.to_string_lossy().ends_with(".zst")
}

fn read_session(path: &Path) -> Result<Vec<u8>, String> {
    if is_compressed(path) {
        zstd::stream::decode_all(File::open(path).map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())
    } else {
        fs::read(path).map_err(|e| e.to_string())
    }
}

fn tool_for(path: &Path) -> Option<Tool> {
    if path.components().any(|part| part.as_os_str() == ".claude") {
        Some(Tool::Claude)
    } else if path.components().any(|part| part.as_os_str() == ".codex") {
        Some(Tool::Codex)
    } else {
        None
    }
}

/// Reescribe una sesión sin imágenes de forma atómica y conserva su fecha.
/// Devuelve los bytes liberados en disco.
pub(crate) fn rewrite_without_images(path: &Path) -> Result<u64, String> {
    let tool = tool_for(path).ok_or("unknown conversation tool")?;
    let before = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    if !before.file_type().is_file() {
        return Err("unsafe file type".into());
    }
    let data = read_session(path)?;
    let (new, removed) = strip_session(&data, tool)?;
    if removed == 0 {
        return Err("no images left to remove".into());
    }
    let bytes = if is_compressed(path) {
        zstd::stream::encode_all(&new[..], 3).map_err(|e| e.to_string())?
    } else {
        new.clone()
    };
    let check = if is_compressed(path) {
        zstd::stream::decode_all(&bytes[..]).map_err(|e| e.to_string())?
    } else {
        bytes.clone()
    };
    if check != new {
        return Err("the rewritten copy does not match".into());
    }
    let parent = path.parent().ok_or("missing parent directory")?;
    let mut temp = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
    temp.write_all(&bytes).map_err(|e| e.to_string())?;
    temp.as_file().sync_all().map_err(|e| e.to_string())?;
    fs::set_permissions(temp.path(), before.permissions()).map_err(|e| e.to_string())?;
    if let Ok(modified) = before.modified() {
        let times = FileTimes::new().set_modified(modified);
        temp.as_file().set_times(times).map_err(|e| e.to_string())?;
    }
    let current = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    if current.len() != before.len() || current.modified().ok() != before.modified().ok() {
        return Err("file changed during cleanup".into());
    }
    temp.persist(path).map_err(|e| e.error.to_string())?;
    Ok(before.len().saturating_sub(bytes.len() as u64))
}

fn candidates(home: &Path) -> Vec<(Tool, PathBuf)> {
    let mut found = Vec::new();
    for (tool, root) in [
        (Tool::Claude, home.join(".claude/projects")),
        (Tool::Codex, home.join(".codex/sessions")),
        (Tool::Codex, home.join(".codex/archived_sessions")),
    ] {
        for path in collect_files(&root) {
            let name = path.to_string_lossy();
            let session = name.ends_with(".jsonl") || (tool == Tool::Codex && name.ends_with(".jsonl.zst"));
            if session && is_older_than_cutoff(&path) {
                found.push((tool, path));
            }
        }
    }
    found
}

pub struct ConversationImages;

impl Harness for ConversationImages {
    fn metadata(&self) -> HarnessMetadata {
        HarnessMetadata {
            key: "images",
            name: "Conversation images",
            description_es: "Capturas guardadas en chats viejos de Claude Code y Codex; los chats se conservan",
            description_en: "Screenshots stored in old Claude Code and Codex chats; the chats are kept",
            logo: "images",
            recommended: false,
            protected: true,
        }
    }

    fn allowed_roots(&self, home: &Path) -> Vec<PathBuf> {
        vec![
            home.join(".claude/projects"),
            home.join(".codex/sessions"),
            home.join(".codex/archived_sessions"),
        ]
    }

    fn plan(&self, home: &Path, actions: &mut Vec<Action>, warnings: &mut Vec<String>) {
        for (tool, path) in candidates(home) {
            let Ok(data) = read_session(&path) else {
                continue;
            };
            match strip_session(&data, tool) {
                Ok((_, removed)) if removed > 0 => actions.push(Action::strip_images(
                    "images",
                    path,
                    removed as u64,
                    "images inside a conversation that has not changed recently",
                )),
                Ok(_) => {}
                Err(error) => warnings.push(format!("{}: {}", path.display(), error)),
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn big() -> String {
        "A".repeat(4000)
    }

    #[test]
    fn claude_images_become_text_and_ui_copies_shrink() {
        let mut record = json!({
            "message": {"content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": big()}},
                {"type": "tool_result", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": big()}}]},
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": big()}},
                {"type": "thinking", "signature": big()}
            ]},
            "toolUseResult": {"type": "image", "file": {"base64": big(), "type": "image/jpeg"}}
        });
        assert!(strip_claude(&mut record) > 9000);
        let content = &record["message"]["content"];
        assert_eq!(content[0], json!({"type": "text", "text": PLACEHOLDER}));
        assert_eq!(content[1]["content"][0]["type"], "text");
        assert_eq!(content[2]["source"]["data"], big());
        assert_eq!(content[3]["signature"], big());
        assert_eq!(record["toolUseResult"]["file"]["base64"], TINY_PNG_B64);
    }

    #[test]
    fn codex_images_become_input_text_and_meta_is_kept() {
        let url = format!("data:image/png;base64,{}", big());
        let mut output = json!({"ordinal": 1, "payload": {"type": "function_call_output", "output": [{"type": "input_image", "image_url": url}]}});
        let mut screenshot = json!({"payload": {"item": {"screenshot": {"url": url}}}});
        let mut meta = json!({"ordinal": 0, "type": "session_meta", "payload": {"id": "x"}});
        assert!(strip_codex(&mut output) > 3000);
        assert!(strip_codex(&mut screenshot) > 3000);
        assert_eq!(strip_codex(&mut meta), 0);
        assert_eq!(output["payload"]["output"][0], json!({"type": "input_text", "text": PLACEHOLDER}));
        assert_eq!(output["ordinal"], 1);
        assert_eq!(screenshot["payload"]["item"]["screenshot"]["url"], tiny_png_url());
    }

    #[test]
    fn untouched_lines_stay_byte_identical() {
        let plain = "{\"type\": \"user\",  \"message\": \"hola ñ\"}";
        let image = json!({"message": {"content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": big()}}]}});
        let data = format!("{plain}\n{image}\n");
        let (out, removed) = strip_session(data.as_bytes(), Tool::Claude).unwrap();
        assert!(removed > 3000);
        let text = String::from_utf8(out).unwrap();
        let lines: Vec<_> = text.split('\n').collect();
        assert_eq!(lines[0], plain);
        assert_eq!(lines[2], "");
        assert!(lines[1].contains(PLACEHOLDER));
    }

    #[test]
    fn invalid_json_with_image_fails_closed() {
        assert!(strip_session(b"{\"base64\": broken\n", Tool::Claude).is_err());
    }

    #[test]
    fn rewrite_keeps_date_and_round_trips_compressed_sessions() {
        let temp = tempfile::tempdir().unwrap();
        let dir = temp.path().join(".codex/sessions");
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("rollout-test.jsonl.zst");
        let body = format!(
            "{}\n{}\n",
            json!({"type": "session_meta", "payload": {}}),
            json!({"payload": {"content": [{"type": "input_image", "image_url": format!("data:image/png;base64,{}", big())}]}})
        );
        fs::write(&path, zstd::stream::encode_all(body.as_bytes(), 3).unwrap()).unwrap();
        let old = std::time::SystemTime::UNIX_EPOCH + std::time::Duration::from_secs(1_000_000);
        File::options().write(true).open(&path).unwrap().set_times(FileTimes::new().set_modified(old)).unwrap();
        rewrite_without_images(&path).unwrap();
        let text = String::from_utf8(read_session(&path).unwrap()).unwrap();
        assert_eq!(text.lines().next(), body.lines().next());
        assert!(text.contains(PLACEHOLDER));
        assert!(!text.contains(&big()));
        assert_eq!(fs::metadata(&path).unwrap().modified().unwrap(), old);
    }

    #[test]
    fn tiny_png_is_a_one_pixel_png() {
        let raw = decode_base64(TINY_PNG_B64);
        assert_eq!(&raw[..8], b"\x89PNG\r\n\x1a\n");
        assert_eq!(&raw[16..24], &[0, 0, 0, 1, 0, 0, 0, 1]);
    }

    fn decode_base64(text: &str) -> Vec<u8> {
        let table = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        let mut bits = 0u32;
        let mut count = 0;
        let mut out = Vec::new();
        for byte in text.bytes().filter(|b| *b != b'=') {
            bits = (bits << 6) | table.iter().position(|t| *t == byte).unwrap() as u32;
            count += 6;
            if count >= 8 {
                count -= 8;
                out.push((bits >> count) as u8);
                bits &= (1 << count) - 1;
            }
        }
        out
    }
}
