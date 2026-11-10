//! Đọc chữ người dùng đang bôi đen.
//!
//! Linux có "primary selection": chữ vừa bôi đen là đọc được ngay, không cần Ctrl+C.
//! Windows/macOS không có, nên đọc clipboard (người dùng bấm Ctrl+C trước khi bấm phím tắt).

use std::io::Read;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use tauri::AppHandle;
use tauri_plugin_clipboard_manager::ClipboardExt;

/// Bôi đen cả trang thì chỉ lấy phần đầu.
const MAX_CHARS: usize = 600;

pub fn read(app: &AppHandle) -> String {
    let text = read_primary().or_else(|| app.clipboard().read_text().ok()).unwrap_or_default();
    shorten(&text)
}

fn shorten(text: &str) -> String {
    let t = text.split_whitespace().collect::<Vec<_>>().join(" ");
    t.chars().take(MAX_CHARS).collect()
}

#[cfg(target_os = "linux")]
fn read_primary() -> Option<String> {
    let mut tools: Vec<&[&str]> = Vec::new();
    if std::env::var_os("WAYLAND_DISPLAY").is_some() {
        tools.push(&["wl-paste", "--primary", "--no-newline", "--type", "text"]);
    }
    tools.push(&["xclip", "-o", "-selection", "primary"]);
    tools.push(&["xsel", "--primary", "--output"]);
    tools.into_iter().find_map(|cmd| run(cmd, Duration::from_millis(1500))).filter(|s| !s.trim().is_empty())
}

#[cfg(not(target_os = "linux"))]
fn read_primary() -> Option<String> {
    None
}

/// Chạy lệnh có giới hạn thời gian (công cụ clipboard đôi khi treo khi không có chủ sở hữu selection).
#[allow(dead_code)]
fn run(cmd: &[&str], timeout: Duration) -> Option<String> {
    let mut child = Command::new(cmd[0]).args(&cmd[1..]).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().ok()?;
    let start = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                if !status.success() {
                    return None;
                }
                let mut out = String::new();
                child.stdout.take()?.read_to_string(&mut out).ok()?;
                return Some(out);
            }
            Ok(None) if start.elapsed() < timeout => std::thread::sleep(Duration::from_millis(15)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shorten_collapses_whitespace_and_caps_length() {
        assert_eq!(shorten("  distribution\n  shift \t"), "distribution shift");
        assert_eq!(shorten(&"a".repeat(MAX_CHARS + 50)).chars().count(), MAX_CHARS);
    }

    #[test]
    fn run_times_out_and_reports_failure() {
        assert_eq!(run(&["sh", "-c", "printf hello"], Duration::from_secs(2)).as_deref(), Some("hello"));
        assert_eq!(run(&["sh", "-c", "exit 1"], Duration::from_secs(2)), None);
        assert_eq!(run(&["sleep", "5"], Duration::from_millis(100)), None);
        assert_eq!(run(&["khong-co-lenh-nay"], Duration::from_millis(100)), None);
    }
}
