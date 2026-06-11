mod auth;
mod commands;
mod settings;
mod updater;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            Ok(())
        })
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_store::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            commands::get_app_info,
            commands::ping,
            auth::commands::start_login,
            auth::commands::handle_deep_link,
            auth::commands::poll_auth_state,
            auth::commands::store_tokens,
            auth::commands::load_tokens,
            auth::commands::clear_tokens,
            auth::commands::logout,
            updater::check_update,
            settings::get_server_url,
            settings::set_server_url,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
