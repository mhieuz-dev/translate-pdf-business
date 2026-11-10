//! Phím tắt toàn cục.
//!
//! - Linux GNOME (kể cả Wayland): Wayland không cho ứng dụng tự bắt phím toàn cục, nên đăng ký
//!   một "custom shortcut" của GNOME chạy `thong-ngu --lookup`. Bản đang chạy nhận lệnh qua
//!   cơ chế một-phiên-bản (single instance) và mở cửa sổ tra nhanh.
//! - Windows/macOS: đăng ký bằng plugin global-shortcut khi khởi động (xem lib.rs).

pub const LABEL: &str = "Ctrl+Alt+E";
#[allow(dead_code)]
pub const GNOME_BINDING: &str = "<Control><Alt>e";
#[allow(dead_code)]
const SCHEMA: &str = "org.gnome.settings-daemon.plugins.media-keys";
#[allow(dead_code)]
const KEY_PATH: &str = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/thongngu/";

/// Lệnh mà phím tắt sẽ chạy. Bản AppImage phải trỏ tới file .AppImage chứ không phải thư mục giải nén tạm.
#[allow(dead_code)]
pub fn launch_command() -> Result<String, String> {
    let exe = std::env::var("APPIMAGE")
        .ok()
        .map(std::path::PathBuf::from)
        .or_else(|| std::env::current_exe().ok())
        .ok_or("Không xác định được đường dẫn ứng dụng")?;
    let exe = exe.to_string_lossy().to_string();
    // GNOME tách lệnh theo quy tắc shell; chỉ cần ngoặc kép khi đường dẫn có khoảng trắng/ký tự đặc biệt.
    if exe.chars().all(|c| c.is_alphanumeric() || "/._-+".contains(c)) {
        Ok(format!("{exe} --lookup"))
    } else {
        Ok(format!("\"{}\" --lookup", exe.replace('\\', "\\\\").replace('"', "\\\"")))
    }
}

/// gsettings đọc giá trị theo cú pháp GVariant, nên chuỗi phải bọc trong '...' và thoát ký tự.
#[allow(dead_code)]
pub fn gvariant_str(s: &str) -> String {
    format!("'{}'", s.replace('\\', "\\\\").replace('\'', "\\'"))
}

/// Ghép đường dẫn của mình vào danh sách custom-keybindings dạng GVariant: ['/a/', '/b/'] hoặc @as [].
#[allow(dead_code)]
pub fn add_to_list(current: &str, path: &str) -> String {
    let mut items = parse_list(current);
    if !items.iter().any(|p| p == path) {
        items.push(path.to_string());
    }
    format!("[{}]", items.iter().map(|p| format!("'{p}'")).collect::<Vec<_>>().join(", "))
}

#[allow(dead_code)]
pub fn parse_list(current: &str) -> Vec<String> {
    current
        .trim()
        .trim_start_matches("@as")
        .trim()
        .trim_start_matches('[')
        .trim_end_matches(']')
        .split(',')
        .map(|s| s.trim().trim_matches('\'').to_string())
        .filter(|s| !s.is_empty())
        .collect()
}

#[cfg(target_os = "linux")]
mod gnome {
    use super::*;
    use std::process::Command;

    fn gsettings(args: &[&str]) -> Result<String, String> {
        let out = Command::new("gsettings").args(args).output().map_err(|_| "Không tìm thấy gsettings (máy không dùng GNOME?)".to_string())?;
        if !out.status.success() {
            return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
        }
        Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
    }

    pub fn available() -> bool {
        gsettings(&["get", SCHEMA, "custom-keybindings"]).is_ok()
    }

    pub fn installed() -> bool {
        let Ok(list) = gsettings(&["get", SCHEMA, "custom-keybindings"]) else { return false };
        if !parse_list(&list).iter().any(|p| p == KEY_PATH) {
            return false;
        }
        let schema = format!("{SCHEMA}.custom-keybinding:{KEY_PATH}");
        gsettings(&["get", &schema, "command"]).map(|c| c.contains("--lookup")).unwrap_or(false)
    }

    pub fn install() -> Result<String, String> {
        let list = gsettings(&["get", SCHEMA, "custom-keybindings"])?;
        let schema = format!("{SCHEMA}.custom-keybinding:{KEY_PATH}");
        gsettings(&["set", &schema, "name", &gvariant_str("Thông Ngữ - tra nhanh")])?;
        gsettings(&["set", &schema, "command", &gvariant_str(&launch_command()?)])?;
        gsettings(&["set", &schema, "binding", &gvariant_str(GNOME_BINDING)])?;
        gsettings(&["set", SCHEMA, "custom-keybindings", &add_to_list(&list, KEY_PATH)])?;
        Ok(format!("Đã cài phím tắt {LABEL}. Bôi đen chữ ở bất kỳ đâu rồi bấm thử nhé."))
    }
}

#[cfg(target_os = "linux")]
pub fn installed() -> bool {
    gnome::installed()
}

#[cfg(target_os = "linux")]
pub fn install() -> Result<String, String> {
    if !gnome::available() {
        return Err(format!(
            "Máy bạn không dùng GNOME. Hãy tự gán phím tắt trong cài đặt bàn phím để chạy lệnh: {}",
            launch_command().unwrap_or_else(|_| "thong-ngu --lookup".into())
        ));
    }
    gnome::install()
}

#[cfg(not(target_os = "linux"))]
pub fn installed() -> bool {
    true // đăng ký bằng plugin mỗi lần khởi động
}

#[cfg(not(target_os = "linux"))]
pub fn install() -> Result<String, String> {
    Ok(format!("Phím tắt {LABEL} đã bật sẵn. Bôi đen chữ, bấm Ctrl+C rồi bấm {LABEL}."))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_gvariant_lists() {
        assert!(parse_list("@as []").is_empty());
        assert_eq!(parse_list("['/a/', '/b/']"), vec!["/a/", "/b/"]);
    }

    #[test]
    fn adds_path_once() {
        assert_eq!(add_to_list("@as []", "/x/"), "['/x/']");
        assert_eq!(add_to_list("['/a/']", "/x/"), "['/a/', '/x/']");
        assert_eq!(add_to_list("['/a/', '/x/']", "/x/"), "['/a/', '/x/']");
    }

    #[test]
    fn launch_command_ends_with_flag() {
        let c = launch_command().unwrap();
        assert!(c.ends_with(" --lookup"), "{c}");
        assert!(!c.contains('\''), "không được có nháy đơn (làm hỏng GVariant): {c}");
    }

    #[test]
    fn gvariant_strings_are_quoted_and_escaped() {
        assert_eq!(gvariant_str("/a/b --lookup"), "'/a/b --lookup'");
        assert_eq!(gvariant_str("it's"), "'it\\'s'");
        assert_eq!(gvariant_str("<Control><Alt>e"), "'<Control><Alt>e'");
    }
}
