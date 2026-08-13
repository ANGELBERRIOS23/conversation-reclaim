mod engine;
mod harnesses;

use engine::{ApplyRequest, ApplyResult, ScanResult};

#[tauri::command]
fn scan_storage() -> Result<ScanResult, String> {
    engine::scan_storage()
}

#[tauri::command]
fn apply_cleanup(request: ApplyRequest) -> Result<ApplyResult, String> {
    engine::apply_cleanup(request)
}

#[tauri::command]
fn reveal_manifest(path: String) -> Result<(), String> {
    engine::reveal_manifest(&path)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            scan_storage,
            apply_cleanup,
            reveal_manifest
        ])
        .run(tauri::generate_context!())
        .expect("error while running Conversation Reclaim");
}
