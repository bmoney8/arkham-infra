// Arkham Cockpit — Tauri v2 backend core (Codeg pattern: Rust core + web UI).
// Multi-window shell: "main" cockpit HUD + "twenty" Twenty CRM wrapper window.
// System tray with Show/Hide/Quit. Window snapping/positioning is delegated to
// the OS (Win32 Aero Snap) plus tauri-plugin-window-state persistence.

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
};

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            // Twenty starts hidden; the tray or cockpit UI reveals it.
            if let Some(win) = app.get_webview_window("twenty") {
                let _ = win.hide();
            }

            let show_cockpit =
                MenuItem::with_id(app, "show_cockpit", "Show Cockpit", true, None::<&str>)?;
            let show_twenty =
                MenuItem::with_id(app, "show_twenty", "Show Twenty CRM", true, None::<&str>)?;
            let hide_twenty =
                MenuItem::with_id(app, "hide_twenty", "Hide Twenty CRM", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit Arkham Cockpit", true, None::<&str>)?;
            let menu = Menu::with_items(
                app,
                &[&show_cockpit, &show_twenty, &hide_twenty, &quit],
            )?;

            TrayIconBuilder::with_id("cockpit-tray")
                .icon(app.default_window_icon().expect("missing default icon").clone())
                .menu(&menu)
                .show_menu_on_left_click(false)
                .tooltip("Arkham Cockpit")
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "show_cockpit" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "show_twenty" => {
                        if let Some(w) = app.get_webview_window("twenty") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "hide_twenty" => {
                        if let Some(w) = app.get_webview_window("twenty") {
                            let _ = w.hide();
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app)?;

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running arkham cockpit");
}
