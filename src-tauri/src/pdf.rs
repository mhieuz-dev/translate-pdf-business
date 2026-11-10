//! Dịch cả file PDF bằng PDFMathTranslate-next (pdf2zh_next), giữ nguyên bố cục.
//!
//! Không gọi thẳng CLI vì CLI chỉ in tiến độ khi chạy trong terminal và luôn thoát mã 0
//! kể cả khi dịch lỗi. Thay vào đó chạy `pdf_bridge.py` bằng chính python của môi trường
//! đã cài pdf2zh-next; script in mỗi sự kiện thành một dòng JSON.

use std::path::{Path, PathBuf};
use std::process::Stdio;

use serde::Serialize;
use serde_json::Value;
use tauri::{AppHandle, Emitter};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::Command;

const BRIDGE: &str = include_str!("../resources/pdf_bridge.py");
/// Số yêu cầu dịch gửi song song. Thời gian dịch PDF gần như toàn bộ là chờ dịch vụ trả lời.
const PDF_QPS: u32 = 12;

#[derive(Debug, Clone, Serialize)]
pub struct PdfStatus {
    pub found: bool,
    pub path: Option<String>,
    pub python: Option<String>,
    pub version: Option<String>,
    pub detail: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct PdfOutput {
    pub files: Vec<String>,
    pub dir: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct Progress {
    pub job_id: String,
    pub percent: Option<f64>,
    pub line: Option<String>,
}

/// Nơi nhận tiến độ: trong app là sự kiện gửi lên giao diện, trong test là một danh sách.
pub type Reporter = std::sync::Arc<dyn Fn(Progress) + Send + Sync>;

pub fn app_reporter(app: AppHandle) -> Reporter {
    std::sync::Arc::new(move |p: Progress| {
        let _ = app.emit("pdf-progress", p);
    })
}

fn exe_name(base: &str) -> String {
    if cfg!(windows) {
        format!("{base}.exe")
    } else {
        base.to_string()
    }
}

/// Các chỗ có thể đã cài pdf2zh_next, ưu tiên từ trên xuống.
fn candidates(data_dir: &Path) -> Vec<PathBuf> {
    let bin = if cfg!(windows) { "Scripts" } else { "bin" };
    let name = exe_name("pdf2zh_next");
    let mut out = Vec::new();
    if let Ok(p) = std::env::var("THONGNGU_PDF2ZH") {
        out.push(PathBuf::from(p));
    }
    out.push(data_dir.join("pdf2zh").join(".venv").join(bin).join(&name));
    // Khi chạy từ mã nguồn: <repo>/tools/pdf2zh/.venv
    if let Ok(exe) = std::env::current_exe() {
        for dir in exe.ancestors().skip(1).take(6) {
            out.push(dir.join("tools").join("pdf2zh").join(".venv").join(bin).join(&name));
        }
    }
    if let Some(home) = std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE")) {
        out.push(PathBuf::from(&home).join(".local").join("bin").join(&name));
    }
    if let Some(paths) = std::env::var_os("PATH") {
        out.extend(std::env::split_paths(&paths).map(|d| d.join(&name)));
    }
    out
}

/// Tìm python đi kèm: shebang của script (pip/uv), hoặc python nằm cạnh / ở thư mục cha (venv, Windows).
pub fn python_for(script: &Path) -> Option<PathBuf> {
    let real = std::fs::canonicalize(script).unwrap_or_else(|_| script.to_path_buf());
    if let Ok(bytes) = std::fs::read(&real) {
        if bytes.starts_with(b"#!") {
            let first = String::from_utf8_lossy(&bytes[..bytes.iter().position(|b| *b == b'\n').unwrap_or(bytes.len())]).to_string();
            let interp = first.trim_start_matches("#!").trim();
            // "#!/usr/bin/env python3" thì không dùng được trực tiếp, bỏ qua.
            let p = PathBuf::from(interp.split_whitespace().next().unwrap_or(""));
            if p.is_absolute() && p.exists() && !p.ends_with("env") {
                return Some(p);
            }
        }
    }
    let dir = real.parent()?;
    for d in [Some(dir), dir.parent()].into_iter().flatten() {
        for n in ["python", "python3", "python.exe"] {
            let p = d.join(n);
            if p.exists() {
                return Some(p);
            }
        }
    }
    None
}

pub async fn status(data_dir: &Path) -> PdfStatus {
    let Some(script) = candidates(data_dir).into_iter().find(|p| p.is_file()) else {
        return PdfStatus { found: false, path: None, python: None, version: None, detail: Some("Không thấy lệnh pdf2zh_next trên máy.".into()) };
    };
    let Some(py) = python_for(&script) else {
        return PdfStatus {
            found: false,
            path: Some(script.display().to_string()),
            python: None,
            version: None,
            detail: Some("Thấy pdf2zh_next nhưng không tìm được python đi kèm.".into()),
        };
    };
    let version = Command::new(&py)
        .args(["-I", "-c", "import importlib.metadata as m; print(m.version('pdf2zh-next'))"])
        .output()
        .await
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string());
    match version {
        Some(v) => PdfStatus { found: true, path: Some(script.display().to_string()), python: Some(py.display().to_string()), version: Some(v), detail: None },
        None => PdfStatus {
            found: false,
            path: Some(script.display().to_string()),
            python: Some(py.display().to_string()),
            version: None,
            detail: Some("Môi trường python không có gói pdf2zh-next.".into()),
        },
    }
}

/// Tham số dòng lệnh cho pdf2zh_next.
pub fn build_args(input: &Path, out_dir: &Path, lang: &str, dual: bool, engine: &str) -> Result<Vec<String>, String> {
    let engine_flag = match engine {
        "google" => "--google",
        "bing" => "--bing",
        other => return Err(format!("Dịch vụ không hỗ trợ: {other}")),
    };
    if !matches!(lang, "vi" | "en") {
        return Err(format!("Ngôn ngữ không hỗ trợ: {lang}"));
    }
    let mut a = vec![
        input.display().to_string(),
        engine_flag.to_string(),
        "--lang-out".into(),
        lang.into(),
        "--output".into(),
        out_dir.display().to_string(),
        // Đo 10/11/2026 trên 4 trang paper, Bing: mặc định 4 yêu cầu song song mất 74,8 s,
        // 12 mất 35,6 s; 20/32/48 không nhanh hơn (33-39 s) mà dễ bị dịch vụ chặn hơn.
        "--qps".into(),
        PDF_QPS.to_string(),
    ];
    if !dual {
        a.push("--no-dual".into());
    }
    Ok(a)
}

/// Tiến độ đôi khi tụt về 0 khi sang bước mới; chỉ cho tăng.
pub fn monotonic(prev: f64, next: f64) -> f64 {
    if next.is_finite() {
        prev.max(next.clamp(0.0, 100.0))
    } else {
        prev
    }
}

pub async fn translate(report: Reporter, data_dir: PathBuf, job_id: String, input: PathBuf, lang: String, dual: bool, engine: String) -> Result<PdfOutput, String> {
    let st = status(&data_dir).await;
    let py = st.python.filter(|_| st.found).ok_or_else(|| st.detail.unwrap_or_else(|| "Chưa cài PDFMathTranslate".into()))?;
    if !input.is_file() {
        return Err("Không tìm thấy file PDF".into());
    }
    let out_dir = input.parent().map(Path::to_path_buf).unwrap_or_else(|| data_dir.clone());
    let bridge = data_dir.join("pdf_bridge.py");
    std::fs::create_dir_all(&data_dir).map_err(|e| e.to_string())?;
    std::fs::write(&bridge, BRIDGE).map_err(|e| format!("Không ghi được script cầu nối: {e}"))?;

    let mut cmd = Command::new(py);
    cmd.arg("-I").arg(&bridge).args(build_args(&input, &out_dir, &lang, dual, &engine)?);
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped()).stdin(Stdio::null()).kill_on_drop(true);
    cmd.env("PYTHONIOENCODING", "utf-8").env("PYTHONUNBUFFERED", "1");
    #[cfg(windows)]
    {
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: không bật cửa sổ console
    }
    let mut child = cmd.spawn().map_err(|e| format!("Không chạy được PDFMathTranslate: {e}"))?;

    let emit = |percent: Option<f64>, line: Option<String>| report(Progress { job_id: job_id.clone(), percent, line });

    // stderr: chuyển thẳng vào nhật ký.
    let stderr = child.stderr.take().ok_or("Không đọc được stderr")?;
    let report2 = report.clone();
    let job2 = job_id.clone();
    let err_task = tokio::spawn(async move {
        let mut lines = BufReader::new(stderr).lines();
        let mut tail: Vec<String> = Vec::new();
        while let Ok(Some(l)) = lines.next_line().await {
            if l.trim().is_empty() {
                continue;
            }
            report2(Progress { job_id: job2.clone(), percent: None, line: Some(l.clone()) });
            tail.push(l);
            if tail.len() > 20 {
                tail.remove(0);
            }
        }
        tail
    });

    let stdout = child.stdout.take().ok_or("Không đọc được stdout")?;
    let mut lines = BufReader::new(stdout).lines();
    let (mut pct, mut stage) = (0.0_f64, String::new());
    let (mut files, mut error): (Vec<String>, Option<String>) = (vec![], None);
    while let Ok(Some(l)) = lines.next_line().await {
        let Ok(v) = serde_json::from_str::<Value>(&l) else {
            emit(None, Some(l));
            continue;
        };
        match v.get("type").and_then(Value::as_str) {
            Some("progress") => {
                pct = monotonic(pct, v.get("percent").and_then(Value::as_f64).unwrap_or(0.0));
                let s = v.get("stage").and_then(Value::as_str).unwrap_or("").to_string();
                let line = (s != stage && !s.is_empty()).then(|| s.clone());
                stage = s;
                emit(Some(pct), line);
            }
            Some("finish") => {
                for k in ["mono", "dual"] {
                    if let Some(p) = v.get(k).and_then(Value::as_str) {
                        files.push(p.to_string());
                    }
                }
                let secs = v.get("seconds").and_then(Value::as_f64).unwrap_or(0.0);
                emit(Some(100.0), Some(format!("Xong sau {secs:.0} giây")));
            }
            Some("error") => error = v.get("message").and_then(Value::as_str).map(String::from),
            _ => {}
        }
    }
    let status = child.wait().await.map_err(|e| e.to_string())?;
    let tail = err_task.await.unwrap_or_default();

    if let Some(e) = error {
        return Err(friendly_error(&e));
    }
    files.retain(|f| Path::new(f).is_file());
    if files.is_empty() {
        let last = tail.last().cloned().unwrap_or_default();
        return Err(if status.success() {
            "Không tạo được file dịch. Mở Nhật ký để xem chi tiết.".into()
        } else {
            friendly_error(&format!("Tiến trình dừng với mã {:?}. {last}", status.code()))
        });
    }
    Ok(PdfOutput { files, dir: out_dir.display().to_string() })
}

/// Đổi lỗi kỹ thuật thường gặp sang lời dễ hiểu.
pub fn friendly_error(e: &str) -> String {
    if e.contains("429") || e.contains("Too Many Requests") || e.contains("/sorry/") {
        "Dịch vụ dịch đang chặn tạm thời vì gửi quá nhiều yêu cầu (lỗi 429). Hãy chọn dịch vụ khác (ví dụ Bing) hoặc thử lại sau.".into()
    } else if e.contains("RetryError") || e.contains("ConnectError") || e.contains("ConnectionError") || e.contains("Timeout") {
        format!("Không kết nối được dịch vụ dịch. Kiểm tra mạng hoặc đổi dịch vụ khác. ({e})")
    } else {
        e.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn args_for_bing_without_dual() {
        let a = build_args(Path::new("/x/p.pdf"), Path::new("/x"), "vi", false, "bing").unwrap();
        assert_eq!(a, vec!["/x/p.pdf", "--bing", "--lang-out", "vi", "--output", "/x", "--qps", "12", "--no-dual"]);
    }

    #[test]
    fn args_reject_unknown_engine_and_lang() {
        assert!(build_args(Path::new("a.pdf"), Path::new("."), "vi", true, "deepl").is_err());
        assert!(build_args(Path::new("a.pdf"), Path::new("."), "zh", true, "google").is_err());
    }

    #[test]
    fn progress_never_goes_back() {
        let mut p = 0.0;
        for x in [14.1, 0.0, 30.0, 25.0, f64::NAN, 150.0] {
            p = monotonic(p, x);
        }
        assert_eq!(p, 100.0);
    }

    #[test]
    fn friendly_rate_limit_message() {
        assert!(friendly_error("RetryError[... raised HTTPError 429 Client Error: Too Many Requests]").contains("Bing"));
        assert_eq!(friendly_error("lỗi khác"), "lỗi khác");
    }

    #[test]
    fn python_from_shebang_and_sibling() {
        let dir = tempfile::tempdir().unwrap();
        let bin = dir.path().join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let py = bin.join("python");
        std::fs::write(&py, "").unwrap();
        // venv: python nằm cạnh, shebang trỏ đúng python đó
        let s = bin.join("pdf2zh_next");
        std::fs::write(&s, format!("#!{}\nimport x\n", py.display())).unwrap();
        assert_eq!(python_for(&s), Some(py.clone()));
        // shebang dạng env: rơi về python nằm cạnh
        std::fs::write(&s, "#!/usr/bin/env python3\n").unwrap();
        assert_eq!(python_for(&s), Some(py));
    }

    /// Dịch thật một file PDF 1 trang từ đầu tới cuối (cần đã cài pdf2zh-next và có mạng).
    /// Chạy: cargo test -p thong-ngu pdf_end_to_end -- --ignored --nocapture
    #[tokio::test]
    #[ignore]
    async fn pdf_end_to_end() {
        let dir = tempfile::tempdir().unwrap();
        let pdf = dir.path().join("mau.pdf");
        let made = std::process::Command::new("python3")
            .args(["-c", "import sys, fitz; d=fitz.open(); p=d.new_page(); p.insert_textbox(fitz.Rect(72,72,520,700), 'Overfitting occurs when a model learns the training data too well and fails to generalize. We evaluate the robustness of the model under distribution shift.', fontsize=12); d.save(sys.argv[1])", pdf.to_str().unwrap()])
            .status()
            .unwrap();
        assert!(made.success(), "cần python3 có pymupdf để tạo PDF mẫu");
        let seen = std::sync::Arc::new(std::sync::Mutex::new(Vec::<Progress>::new()));
        let seen2 = seen.clone();
        let report: Reporter = std::sync::Arc::new(move |p| seen2.lock().unwrap().push(p));
        let data = dir.path().join("data");
        let out = translate(report, data, "j1".into(), pdf.clone(), "vi".into(), true, "bing".into()).await.expect("dịch PDF");
        println!("{out:?}");
        assert_eq!(out.files.len(), 2, "phải có bản dịch và bản song ngữ");
        assert!(out.files.iter().all(|f| Path::new(f).is_file()));
        let ev = seen.lock().unwrap();
        let max = ev.iter().filter_map(|p| p.percent).fold(0.0, f64::max);
        assert_eq!(max, 100.0, "tiến độ phải tới 100%");
        let mut last = 0.0;
        for p in ev.iter().filter_map(|p| p.percent) {
            assert!(p >= last, "tiến độ không được giảm");
            last = p;
        }
    }

    #[tokio::test]
    async fn missing_file_is_reported() {
        let dir = tempfile::tempdir().unwrap();
        let report: Reporter = std::sync::Arc::new(|_| {});
        let r = translate(report, dir.path().to_path_buf(), "j".into(), dir.path().join("khong-co.pdf"), "vi".into(), true, "bing".into()).await;
        assert!(r.is_err());
    }

    #[test]
    fn bridge_script_is_embedded() {
        assert!(BRIDGE.contains("do_translate_async_stream"));
    }
}
