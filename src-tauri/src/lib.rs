//! Ứng dụng desktop Thông Ngữ (Tauri). Logic tra cứu nằm ở crate `thongngu-core`;
//! file này chỉ nối giao diện với lõi, quản lý cửa sổ, phím tắt và khay hệ thống.

mod hotkey;
mod pdf;
mod selection;

use std::path::PathBuf;
use std::sync::Mutex;

use serde::Serialize;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, State, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;
use thongngu_core::{lang, lookup as lk, Direction, Glossary, LookupResult, Term};

struct AppState {
    db: Mutex<Glossary>,
    http: thongngu_core::reqwest::Client,
    data_dir: PathBuf,
}

impl AppState {
    fn db(&self) -> Result<std::sync::MutexGuard<'_, Glossary>, String> {
        self.db.lock().map_err(|_| "Cơ sở dữ liệu đang bận, thử lại".to_string())
    }
}

// ======================= Tra cứu =======================

#[tauri::command]
async fn lookup(state: State<'_, AppState>, query: String, direction: Option<Direction>) -> Result<LookupResult, String> {
    let q = lk::prepare_query(&query)?;
    let dir = direction.unwrap_or_else(|| lang::detect(&q));
    let (exact, in_text) = {
        let db = state.db()?;
        if !lk::is_sentence(&q) {
            db.push_history(&q)?;
        }
        let in_text = if lk::is_sentence(&q) { db.find_in_text(&q, dir)? } else { vec![] };
        (db.find_exact(&q, dir)?, in_text)
    };
    let online = lk::fetch_online(&state.http, &q, dir).await;
    Ok(lk::merge(&q, dir, exact, in_text, online))
}

#[tauri::command]
fn recent_list(state: State<'_, AppState>) -> Result<Vec<String>, String> {
    state.db()?.recent(10)
}

// ======================= Bảng thuật ngữ =======================

#[tauri::command]
fn glossary_list(state: State<'_, AppState>, query: Option<String>, field: Option<String>) -> Result<Vec<Term>, String> {
    state.db()?.list(query.as_deref().unwrap_or(""), field.as_deref().unwrap_or(""))
}

#[tauri::command]
fn glossary_fields(state: State<'_, AppState>) -> Result<Vec<String>, String> {
    state.db()?.fields()
}

#[tauri::command]
fn glossary_save(state: State<'_, AppState>, entry: Term) -> Result<Term, String> {
    state.db()?.save(&entry)
}

#[tauri::command]
fn glossary_delete(state: State<'_, AppState>, id: i64) -> Result<bool, String> {
    state.db()?.delete(id)
}

// Hộp thoại chọn file chặn luồng, nên các lệnh dưới đây là async (chạy ngoài luồng giao diện).
#[tauri::command]
async fn glossary_export(app: AppHandle, state: State<'_, AppState>) -> Result<Option<String>, String> {
    let Some(fp) = app.dialog().file().add_filter("CSV", &["csv"]).set_file_name("thuat-ngu.csv").blocking_save_file() else {
        return Ok(None);
    };
    let path = fp.into_path().map_err(|e| e.to_string())?;
    let n = state.db()?.export_csv(&path)?;
    Ok(Some(format!("{} ({n} thuật ngữ)", path.display())))
}

#[tauri::command]
async fn glossary_import(app: AppHandle, state: State<'_, AppState>) -> Result<Option<usize>, String> {
    let Some(fp) = app.dialog().file().add_filter("CSV", &["csv"]).blocking_pick_file() else {
        return Ok(None);
    };
    let path = fp.into_path().map_err(|e| e.to_string())?;
    state.db()?.import_csv(&path).map(Some)
}

// ======================= Dịch PDF =======================

#[tauri::command]
async fn pdf_status(state: State<'_, AppState>) -> Result<pdf::PdfStatus, String> {
    Ok(pdf::status(&state.data_dir).await)
}

#[tauri::command]
async fn pick_pdfs(app: AppHandle) -> Result<Vec<String>, String> {
    let files = app.dialog().file().add_filter("PDF", &["pdf"]).blocking_pick_files().unwrap_or_default();
    files.into_iter().map(|f| f.into_path().map(|p| p.display().to_string()).map_err(|e| e.to_string())).collect()
}

#[tauri::command]
async fn pdf_translate(
    app: AppHandle,
    state: State<'_, AppState>,
    job_id: String,
    path: String,
    lang: String,
    dual: bool,
    engine: String,
) -> Result<pdf::PdfOutput, String> {
    pdf::translate(app, state.data_dir.clone(), job_id, PathBuf::from(path), lang, dual, engine).await
}

// ======================= Hệ thống =======================

#[derive(Serialize)]
struct AppInfo {
    version: String,
    data_dir: String,
    hotkey: String,
    hotkey_installed: bool,
    platform: String,
    pdf: pdf::PdfStatus,
}

#[tauri::command]
async fn app_info(app: AppHandle, state: State<'_, AppState>) -> Result<AppInfo, String> {
    Ok(AppInfo {
        version: app.package_info().version.to_string(),
        data_dir: state.data_dir.display().to_string(),
        hotkey: hotkey::LABEL.into(),
        hotkey_installed: hotkey::installed(),
        platform: std::env::consts::OS.into(),
        pdf: pdf::status(&state.data_dir).await,
    })
}

#[tauri::command]
async fn install_hotkey() -> Result<String, String> {
    hotkey::install()
}

#[tauri::command]
fn open_path(app: AppHandle, path: String) -> Result<(), String> {
    app.opener().open_path(path, None::<&str>).map_err(|e| e.to_string())
}

#[tauri::command]
fn open_url(app: AppHandle, url: String) -> Result<(), String> {
    if !url.starts_with("https://") {
        return Err("Chỉ mở liên kết https".into());
    }
    app.opener().open_url(url, None::<&str>).map_err(|e| e.to_string())
}

#[tauri::command]
fn popup_hide(app: AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("popup") {
        w.hide().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn open_main(app: AppHandle, query: Option<String>) -> Result<(), String> {
    let _ = popup_hide(app.clone());
    show_main(&app);
    if let Some(q) = query.filter(|q| !q.trim().is_empty()) {
        app.emit_to("main", "lookup-request", q).map_err(|e| e.to_string())?;
    }
    Ok(())
}

// ======================= Cửa sổ =======================

fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

fn encode_query(s: &str) -> String {
    s.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => (b as char).to_string(),
            _ => format!("%{b:02X}"),
        })
        .collect()
}

fn show_popup(app: &AppHandle, text: &str) {
    if let Some(w) = app.get_webview_window("popup") {
        let _ = app.emit_to("popup", "popup-lookup", text);
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
        return;
    }
    let url = format!("popup.html?q={}", encode_query(text));
    let built = WebviewWindowBuilder::new(app, "popup", WebviewUrl::App(url.into()))
        .title("Thông Ngữ - Tra nhanh")
        .inner_size(470.0, 640.0)
        .min_inner_size(380.0, 440.0)
        .always_on_top(true)
        .skip_taskbar(true)
        .center()
        .focused(true)
        .build();
    if let Err(e) = built {
        eprintln!("không mở được cửa sổ tra nhanh: {e}");
    }
}

/// Xử lý tham số dòng lệnh, cả lúc khởi động lẫn khi bản thứ hai được gọi (phím tắt).
fn handle_args(app: &AppHandle, args: &[String]) {
    if args.iter().any(|a| a == "--lookup") {
        let app = app.clone();
        // Đọc selection có thể mất tới ~1 giây, không làm ở luồng giao diện.
        tauri::async_runtime::spawn_blocking(move || {
            let text = selection::read(&app);
            show_popup(&app, &text);
        });
    } else if !args.iter().any(|a| a == "--hidden") {
        show_main(app);
    }
}

fn setup_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Mở Thông Ngữ", true, None::<&str>)?;
    let quick = MenuItem::with_id(app, "quick", "Tra nhanh chữ đang bôi đen", true, None::<&str>)?;
    let sep = PredefinedMenuItem::separator(app)?;
    let quit = MenuItem::with_id(app, "quit", "Thoát", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &quick, &sep, &quit])?;
    let mut tray = TrayIconBuilder::with_id("main")
        .tooltip("Thông Ngữ")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, e| match e.id.as_ref() {
            "open" => show_main(app),
            "quick" => handle_args(app, &["--lookup".to_string()]),
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, e| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = e {
                show_main(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.build(app)?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Dùng trong script cài đặt: `thong-ngu --install-hotkey` cài phím tắt rồi thoát, không mở cửa sổ.
    if std::env::args().any(|a| a == "--install-hotkey") {
        match hotkey::install() {
            Ok(msg) => println!("{msg}"),
            Err(e) => {
                eprintln!("{e}");
                std::process::exit(1);
            }
        }
        return;
    }

    #[allow(unused_mut)] // chỉ Windows/macOS mới gắn thêm plugin phím tắt
    let mut builder = tauri::Builder::default()
        // Phải đăng ký đầu tiên: bản thứ hai (do phím tắt gọi) chuyển tham số sang bản đang chạy rồi thoát.
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| handle_args(app, &argv)))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_clipboard_manager::init());

    #[cfg(any(target_os = "windows", target_os = "macos"))]
    {
        use tauri_plugin_global_shortcut::{Code, Modifiers, Shortcut, ShortcutState};
        let sc = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::KeyE);
        builder = builder.plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_shortcuts([sc])
                .expect("đăng ký phím tắt")
                .with_handler(move |app, s, ev| {
                    if *s == sc && ev.state() == ShortcutState::Pressed {
                        handle_args(app, &["--lookup".to_string()]);
                    }
                })
                .build(),
        );
    }

    builder
        .setup(|app| {
            let data_dir = app.path().app_data_dir()?;
            let db = Glossary::open(&data_dir.join("thongngu.db"))?;
            app.manage(AppState { db: Mutex::new(db), http: thongngu_core::sources::client(), data_dir });
            if let Err(e) = setup_tray(app.handle()) {
                eprintln!("không tạo được biểu tượng khay hệ thống: {e}");
            }
            let args: Vec<String> = std::env::args().collect();
            handle_args(app.handle(), &args);
            Ok(())
        })
        .on_window_event(|window, event| {
            // Đóng cửa sổ chỉ ẩn đi: app vẫn chạy nền để phím tắt bật lên tức thì. Thoát qua khay hệ thống.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .invoke_handler(tauri::generate_handler![
            lookup,
            recent_list,
            glossary_list,
            glossary_fields,
            glossary_save,
            glossary_delete,
            glossary_export,
            glossary_import,
            pdf_status,
            pick_pdfs,
            pdf_translate,
            app_info,
            install_hotkey,
            open_path,
            open_url,
            popup_hide,
            open_main,
        ])
        .run(tauri::generate_context!())
        .expect("lỗi khi chạy Thông Ngữ");
}

#[cfg(test)]
mod tests {
    use super::encode_query;

    #[test]
    fn encodes_vietnamese_query_for_url() {
        assert_eq!(encode_query("học sâu"), "h%E1%BB%8Dc%20s%C3%A2u");
        assert_eq!(encode_query("a&b=c"), "a%26b%3Dc");
    }
}
