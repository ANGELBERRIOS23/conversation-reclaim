mod engine;
mod harnesses;
mod outputs;

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
fn scan_storage(older_than_days: Option<u64>) -> Result<ScanResult, String> {
    engine::scan_storage(older_than_days)
}

#[tauri::command]
fn apply_cleanup(request: ApplyRequest) -> Result<ApplyResult, String> {
    engine::apply_cleanup(request)
}

#[tauri::command]
async fn list_large_outputs() -> Result<Vec<outputs::OutputItem>, String> {
    tauri::async_runtime::spawn_blocking(outputs::list_large_outputs)
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn trash_outputs(paths: Vec<String>) -> Result<outputs::TrashResult, String> {
    tauri::async_runtime::spawn_blocking(move || outputs::trash_outputs(paths))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn list_generated_images() -> Result<Vec<outputs::GeneratedImage>, String> {
    tauri::async_runtime::spawn_blocking(outputs::list_generated_images)
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn trash_generated_images(paths: Vec<String>) -> Result<outputs::TrashResult, String> {
    tauri::async_runtime::spawn_blocking(move || outputs::trash_generated_images(paths))
        .await
        .map_err(|e| e.to_string())?
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
            list_large_outputs,
            trash_outputs,
            list_generated_images,
            trash_generated_images,
            check_for_update,
            install_update
        ])
        .run(tauri::generate_context!())
        .expect("error while running Conversation Reclaim");
}
