//! Các nguồn tra cứu miễn phí, không cần tài khoản.
//! Mỗi nguồn có một hàm `parse_*` thuần (kiểm thử bằng dữ liệu thật đã lưu) và một hàm `fetch_*`.

use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::lang::{clean, fold, Direction};

pub const USER_AGENT: &str = concat!("ThongNgu/", env!("CARGO_PKG_VERSION"), " (+https://github.com/mhieuz-dev/thong-ngu)");
const TIMEOUT: Duration = Duration::from_secs(8);

pub fn client() -> reqwest::Client {
    reqwest::Client::builder()
        .user_agent(USER_AGENT)
        .timeout(TIMEOUT)
        .build()
        .expect("không tạo được HTTP client")
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WikiInfo {
    /// Tên bài ở ngôn ngữ nguồn.
    pub title: String,
    pub url: String,
    /// 1-2 câu mở đầu bài viết.
    pub extract: String,
    /// Tên bài tương ứng ở ngôn ngữ đích (chính là gợi ý dịch), nếu có.
    pub linked_title: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Definition {
    pub pos: String,
    pub text: String,
    pub example: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
pub struct DictInfo {
    pub phonetic: Option<String>,
    pub definitions: Vec<Definition>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct MemoryMatch {
    pub text: String,
    /// Độ khớp 0..1 do MyMemory trả về.
    pub score: f64,
}

// ======================= Wikipedia =======================

pub fn wiki_url(lang: &str, title: &str) -> String {
    format!("https://{lang}.wikipedia.org/wiki/{}", title.replace(' ', "_"))
}

/// Đọc kết quả `action=query&prop=langlinks|extracts|pageprops`.
/// Bỏ qua trang không tồn tại và trang định hướng (một từ nhiều nghĩa).
pub fn parse_wiki(v: &Value, source_lang: &str) -> Option<WikiInfo> {
    let page = v.get("query")?.get("pages")?.as_array()?.first()?;
    if page.get("missing").is_some() || page.get("invalid").is_some() {
        return None;
    }
    if page.get("pageprops").and_then(|p| p.get("disambiguation")).is_some() {
        return None;
    }
    let title = page.get("title")?.as_str()?.to_string();
    let extract = page.get("extract").and_then(Value::as_str).unwrap_or("").trim().to_string();
    let linked_title = page
        .get("langlinks")
        .and_then(Value::as_array)
        .and_then(|a| a.first())
        .and_then(|l| l.get("title"))
        .and_then(Value::as_str)
        .map(|s| s.to_string());
    Some(WikiInfo { url: wiki_url(source_lang, &title), title, extract, linked_title })
}

pub async fn fetch_wiki(client: &reqwest::Client, text: &str, dir: Direction) -> Result<Option<WikiInfo>, String> {
    let lang = dir.source();
    let url = format!("https://{lang}.wikipedia.org/w/api.php");
    let v: Value = client
        .get(url)
        .query(&[
            ("action", "query"),
            ("format", "json"),
            ("formatversion", "2"),
            ("redirects", "1"),
            ("prop", "langlinks|extracts|pageprops"),
            ("ppprop", "disambiguation"),
            ("exintro", "1"),
            ("explaintext", "1"),
            ("exsentences", "2"),
            ("lllang", dir.target()),
            ("titles", text),
        ])
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
        .map_err(|e| e.to_string())?
        .json()
        .await
        .map_err(|e| e.to_string())?;
    Ok(parse_wiki(&v, lang))
}

/// Tên bài Wikipedia đôi khi kèm phần giải thích trong ngoặc, ví dụ "Cơ chế chú ý (học máy)".
pub fn strip_wiki_qualifier(title: &str) -> String {
    match title.find(" (") {
        Some(i) if title.ends_with(')') => title[..i].to_string(),
        _ => title.to_string(),
    }
}

// ======================= Google Dịch =======================

/// Endpoint `translate_a/t?client=dict-chrome-ex` trả về mảng chuỗi: ["bản dịch"]
/// hoặc [["bản dịch","mã ngôn ngữ nguồn"]] khi để tự nhận ngôn ngữ.
pub fn parse_google_t(v: &Value) -> Option<String> {
    let first = v.as_array()?.first()?;
    let s = match first {
        Value::String(s) => s.as_str(),
        Value::Array(a) => a.first()?.as_str()?,
        _ => return None,
    };
    let s = clean(s);
    (!s.is_empty()).then_some(s)
}

pub async fn fetch_google(client: &reqwest::Client, text: &str, dir: Direction) -> Result<Option<String>, String> {
    let v: Value = client
        .get("https://translate.googleapis.com/translate_a/t")
        .query(&[("client", "dict-chrome-ex"), ("sl", dir.source()), ("tl", dir.target()), ("q", text)])
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
        .map_err(|e| e.to_string())?
        .json()
        .await
        .map_err(|e| e.to_string())?;
    Ok(parse_google_t(&v))
}

// ======================= MyMemory =======================

/// Ngưỡng độ khớp: dữ liệu thật cho thấy dưới mức này toàn kết quả lạc đề
/// (ví dụ "gradient descent" -> "Dốc Màu" với độ khớp 0.67).
pub const MYMEMORY_MIN_SCORE: f64 = 0.8;

/// Lấy các bản dịch người thật từ bộ nhớ dịch MyMemory, lọc nhiễu:
/// bỏ độ khớp thấp, bỏ bản giữ nguyên chữ gốc, bỏ câu dài lạc đề, gộp trùng.
pub fn parse_mymemory(v: &Value, query: &str) -> Vec<MemoryMatch> {
    let q_fold = fold(query);
    let q_words = query.split_whitespace().count().max(1);
    let lower_query = query == query.to_lowercase();
    let mut out: Vec<MemoryMatch> = Vec::new();
    let Some(matches) = v.get("matches").and_then(Value::as_array) else { return out };
    for m in matches {
        let Some(text) = m.get("translation").and_then(Value::as_str) else { continue };
        let score = m.get("match").and_then(|x| x.as_f64().or_else(|| x.as_str().and_then(|s| s.parse().ok()))).unwrap_or(0.0);
        if score < MYMEMORY_MIN_SCORE {
            continue;
        }
        let mut text = clean(text.trim_matches(TRIM_CHARS));
        if text.is_empty() || fold(&text) == q_fold {
            continue;
        }
        // Bản dịch dài gấp nhiều lần cụm gốc thường là câu lạc đề.
        if text.split_whitespace().count() > q_words * 4 + 2 {
            continue;
        }
        if lower_query {
            text = lowercase_keep_acronyms(&text);
        }
        if out.iter().any(|o| fold(&o.text) == fold(&text)) {
            continue;
        }
        out.push(MemoryMatch { text, score });
    }
    out.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
    out
}

/// Ký tự đầu/cuối cần bỏ: gạch đầu dòng, ngoặc kép, dấu câu.
const TRIM_CHARS: &[char] = &['-', '–', '—', '•', '·', '*', '"', '\'', '“', '”', '‘', '’', '.', '。', ',', ';', ':', ' '];

/// Chuyển chữ thường nhưng giữ từ viết tắt ASCII in hoa (ML, CNN, GPU...).
fn lowercase_keep_acronyms(text: &str) -> String {
    text.split(' ')
        .map(|w| {
            let core: String = w.chars().filter(|c| c.is_alphanumeric()).collect();
            let is_acronym = (2..=6).contains(&core.chars().count())
                && core.chars().all(|c| c.is_ascii_uppercase() || c.is_ascii_digit())
                && core.chars().any(|c| c.is_ascii_uppercase());
            if is_acronym { w.to_string() } else { w.to_lowercase() }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

pub async fn fetch_mymemory(client: &reqwest::Client, text: &str, dir: Direction) -> Result<Vec<MemoryMatch>, String> {
    let pair = format!("{}|{}", dir.source(), dir.target());
    let v: Value = client
        .get("https://api.mymemory.translated.net/get")
        .query(&[("q", text), ("langpair", pair.as_str())])
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
        .map_err(|e| e.to_string())?
        .json()
        .await
        .map_err(|e| e.to_string())?;
    if v.get("quotaFinished").and_then(Value::as_bool) == Some(true) {
        return Err("MyMemory đã hết lượt miễn phí hôm nay".into());
    }
    Ok(parse_mymemory(&v, text))
}

// ======================= Free Dictionary API =======================

pub const MAX_DEFINITIONS: usize = 4;

pub fn parse_dict(v: &Value) -> DictInfo {
    let mut info = DictInfo::default();
    let Some(entries) = v.as_array() else { return info };
    for e in entries {
        if info.phonetic.is_none() {
            info.phonetic = e
                .get("phonetic")
                .and_then(Value::as_str)
                .filter(|s| !s.is_empty())
                .or_else(|| {
                    e.get("phonetics")?
                        .as_array()?
                        .iter()
                        .filter_map(|p| p.get("text").and_then(Value::as_str))
                        .find(|s| !s.is_empty())
                })
                .map(String::from);
        }
        for m in e.get("meanings").and_then(Value::as_array).into_iter().flatten() {
            let pos = m.get("partOfSpeech").and_then(Value::as_str).unwrap_or("").to_string();
            // Lấy tối đa 2 nghĩa cho mỗi từ loại để đa dạng từ loại.
            for d in m.get("definitions").and_then(Value::as_array).into_iter().flatten().take(2) {
                if info.definitions.len() >= MAX_DEFINITIONS {
                    return info;
                }
                let Some(text) = d.get("definition").and_then(Value::as_str) else { continue };
                let example = d.get("example").and_then(Value::as_str).map(String::from);
                info.definitions.push(Definition { pos: pos.clone(), text: text.to_string(), example });
            }
        }
    }
    info
}

pub async fn fetch_dict(client: &reqwest::Client, word: &str) -> Result<DictInfo, String> {
    let url = format!("https://api.dictionaryapi.dev/api/v2/entries/en/{}", urlencode_path(word));
    let resp = client.get(url).send().await.map_err(|e| e.to_string())?;
    if resp.status() == reqwest::StatusCode::NOT_FOUND {
        return Ok(DictInfo::default());
    }
    let v: Value = resp.error_for_status().map_err(|e| e.to_string())?.json().await.map_err(|e| e.to_string())?;
    Ok(parse_dict(&v))
}

fn urlencode_path(s: &str) -> String {
    s.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => (b as char).to_string(),
            _ => format!("%{b:02X}"),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(name: &str) -> Value {
        let path = format!("{}/tests/fixtures/{name}", env!("CARGO_MANIFEST_DIR"));
        let s = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{path}: {e}"));
        serde_json::from_str(&s).unwrap()
    }

    #[test]
    fn wiki_with_vietnamese_link() {
        let w = parse_wiki(&fixture("wiki_en_overfitting.json"), "en").unwrap();
        assert_eq!(w.title, "Overfitting");
        assert_eq!(w.linked_title.as_deref(), Some("Sự quá khớp"));
        assert_eq!(w.url, "https://en.wikipedia.org/wiki/Overfitting");
        assert!(w.extract.starts_with("In mathematical modeling"));
    }

    #[test]
    fn wiki_multiword_title_url() {
        let w = parse_wiki(&fixture("wiki_en_gradient_descent.json"), "en").unwrap();
        assert_eq!(w.linked_title.as_deref(), Some("Suy giảm độ dốc"));
        assert_eq!(w.url, "https://en.wikipedia.org/wiki/Gradient_descent");
    }

    #[test]
    fn wiki_without_link_still_gives_extract() {
        let w = parse_wiki(&fixture("wiki_en_robustness_nolink.json"), "en").unwrap();
        assert_eq!(w.linked_title, None);
        assert!(!w.extract.is_empty());
    }

    #[test]
    fn wiki_missing_and_disambiguation_are_skipped() {
        assert_eq!(parse_wiki(&fixture("wiki_en_missing.json"), "en"), None);
        assert_eq!(parse_wiki(&fixture("wiki_en_disambig.json"), "en"), None);
    }

    #[test]
    fn wiki_vi_to_en() {
        let w = parse_wiki(&fixture("wiki_vi_hoc_sau.json"), "vi").unwrap();
        assert_eq!(w.linked_title.as_deref(), Some("Deep learning"));
        assert_eq!(w.url, "https://vi.wikipedia.org/wiki/Học_sâu");
    }

    #[test]
    fn wiki_garbage_is_none() {
        assert_eq!(parse_wiki(&serde_json::json!({"error": "x"}), "en"), None);
    }

    #[test]
    fn strips_qualifier() {
        assert_eq!(strip_wiki_qualifier("Cơ chế chú ý (học máy)"), "Cơ chế chú ý");
        assert_eq!(strip_wiki_qualifier("Học sâu"), "Học sâu");
        assert_eq!(strip_wiki_qualifier("A (b) c"), "A (b) c");
    }

    #[test]
    fn google_t_formats() {
        assert_eq!(parse_google_t(&fixture("google_t_distribution_shift.json")).as_deref(), Some("sự thay đổi phân phối"));
        assert_eq!(parse_google_t(&serde_json::json!([["học sâu", "en"]])).as_deref(), Some("học sâu"));
        assert_eq!(parse_google_t(&serde_json::json!([""])), None);
        assert_eq!(parse_google_t(&serde_json::json!({})), None);
    }

    #[test]
    fn mymemory_filters_noise() {
        // Dữ liệu thật: "Dốc Màu"/"Dải màu" (0.67) phải bị loại, "gradient descent" trùng chữ gốc cũng loại.
        let r = parse_mymemory(&fixture("mymemory_gradient_descent.json"), "gradient descent");
        assert!(r.is_empty(), "{r:?}");
    }

    #[test]
    fn mymemory_keeps_good_match_lowercased_and_deduped() {
        // Dữ liệu thật: "Mạnh MẼ" (0.97) và "mạnh mẽ" (0.85) là một; câu "Tối ưu hóa độ bền chắc." (0.66) bị loại.
        let r = parse_mymemory(&fixture("mymemory_robustness.json"), "robustness");
        assert_eq!(r.len(), 1, "{r:?}");
        assert_eq!(r[0].text, "mạnh mẽ");
        assert!((r[0].score - 0.97).abs() < 1e-9);
    }

    #[test]
    fn mymemory_trims_bullets_and_quotes() {
        let v = serde_json::json!({"matches": [
            {"translation": "- deep learning", "match": 0.96},
            {"translation": "“Deep Learning”", "match": 0.9},
            {"translation": "• học máy (ML)", "match": 0.9}
        ]});
        let r = parse_mymemory(&v, "học sâu");
        let texts: Vec<_> = r.iter().map(|m| m.text.as_str()).collect();
        assert_eq!(texts, vec!["deep learning", "học máy (ML)"]);
    }

    #[test]
    fn mymemory_drops_long_sentences() {
        let v = serde_json::json!({"matches": [
            {"translation": "một câu rất dài không liên quan gì tới thuật ngữ được tra cứu cả", "match": 0.9},
            {"translation": "độ chệch", "match": "0.85"}
        ]});
        let r = parse_mymemory(&v, "bias");
        assert_eq!(r.len(), 1);
        assert_eq!(r[0].text, "độ chệch");
    }

    #[test]
    fn dict_parses_phonetic_and_limits_definitions() {
        let d = parse_dict(&fixture("dict_robust.json"));
        assert_eq!(d.phonetic.as_deref(), Some("/ɹəʊˈbʌst/"));
        assert!(!d.definitions.is_empty() && d.definitions.len() <= MAX_DEFINITIONS);
        assert_eq!(d.definitions[0].pos, "adjective");
        assert_eq!(d.definitions[0].text, "Evincing strength and health; strong.");
        assert_eq!(d.definitions[0].example.as_deref(), Some("A robust wall was put up."));
    }

    #[test]
    fn dict_not_found_object_is_empty() {
        assert_eq!(parse_dict(&fixture("dict_notfound.json")), DictInfo::default());
    }

    #[test]
    fn path_encoding() {
        assert_eq!(urlencode_path("state of the art"), "state%20of%20the%20art");
        assert_eq!(urlencode_path("a/b"), "a%2Fb");
    }
}
