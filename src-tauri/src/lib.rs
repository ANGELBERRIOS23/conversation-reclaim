mod engine;
mod harnesses;

use engine::{ApplyRequest, ApplyResult, ScanResult};
use serde::Serialize;

#[cfg(desktop)]
use tauri_plugin_updater::UpdaterExt;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct UpdateInfo {
    version: String,
    current_version: String,
    notes: Option<String>,
}

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

#[cfg(desktop)]
#[tauri::command]
async fn check_for_update(app: tauri::AppHandle) -> Result<Option<UpdateInfo>, String> {
    let update = app
        .updater()
        .map_err(|error| error.to_string())?
        .check()
        .await
        .map_err(|error| error.to_string())?;

    Ok(update.map(|available| UpdateInfo {
        version: available.version.to_string(),
        current_version: available.current_version.to_string(),
        notes: available.body,
    }))
}

#[cfg(desktop)]
#[tauri::command]
async fn install_update(app: tauri::AppHandle) -> Result<(), String> {
    let update = app
        .updater()
        .map_err(|error| error.to_string())?
        .check()
        .await
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "No update is currently available".to_string())?;

    update
        .download_and_install(|_, _| {}, || {})
        .await
        .map_err(|error| error.to_string())?;

    app.restart();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default();

    #[cfg(desktop)]
    let builder = builder.plugin(tauri_plugin_updater::Builder::new().build());

    builder
        .invoke_handler(tauri::generate_handler![
            scan_storage,
            apply_cleanup,
            reveal_manifest,
            check_for_update,
            install_update
        ])
        .run(tauri::generate_context!())
        .expect("error while running Conversation Reclaim");
}
