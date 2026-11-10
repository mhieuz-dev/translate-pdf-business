//! Gom kết quả từ các nguồn thành một danh sách gợi ý dễ chọn.

use serde::{Deserialize, Serialize};

use crate::glossary::Term;
use crate::lang::{clean, fold, word_count, Direction};
use crate::sources::{self, Definition, DictInfo, MemoryMatch, WikiInfo};

/// Dài hơn số từ này thì coi là câu/đoạn: chỉ dịch máy + dò thuật ngữ đã lưu bên trong.
pub const SENTENCE_WORDS: usize = 6;
/// Giới hạn độ dài gửi đi để tránh dán nhầm cả trang.
pub const MAX_CHARS: usize = 2000;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Candidate {
    pub text: String,
    /// "wikipedia" | "google" | "mymemory"
    pub source: String,
    pub detail: String,
    /// Các nguồn khác cũng ra đúng cách dịch này.
    pub also: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LookupResult {
    pub query: String,
    pub direction: Direction,
    /// "term" (từ/cụm từ) hoặc "sentence" (câu, đoạn).
    pub mode: String,
    /// Thuật ngữ đã lưu khớp đúng cụm vừa tra.
    pub glossary: Vec<Term>,
    /// Thuật ngữ đã lưu xuất hiện bên trong câu (chế độ câu).
    pub in_text: Vec<Term>,
    pub candidates: Vec<Candidate>,
    pub phonetic: Option<String>,
    pub definitions: Vec<Definition>,
    pub wiki: Option<WikiInfo>,
    /// Tên nguồn bị lỗi (mất mạng, hết lượt...).
    pub errors: Vec<String>,
}

/// Kết quả thô từ mạng; `Err` nghĩa là nguồn đó lỗi.
#[derive(Debug, Default)]
pub struct Online {
    pub wiki: Option<Result<Option<WikiInfo>, String>>,
    pub google: Option<Result<Option<String>, String>>,
    pub memory: Option<Result<Vec<MemoryMatch>, String>>,
    pub dict: Option<Result<DictInfo, String>>,
}

pub fn prepare_query(raw: &str) -> Result<String, String> {
    let q = clean(raw);
    if q.is_empty() {
        return Err("Chưa có chữ để tra".into());
    }
    if q.chars().count() > MAX_CHARS {
        return Err(format!("Đoạn quá dài ({} ký tự). Hãy bôi đen ít hơn {MAX_CHARS} ký tự.", q.chars().count()));
    }
    Ok(q)
}

pub fn is_sentence(q: &str) -> bool {
    word_count(q) > SENTENCE_WORDS
}

/// Gọi song song các nguồn phù hợp với loại truy vấn.
pub async fn fetch_online(client: &reqwest::Client, q: &str, dir: Direction) -> Online {
    if is_sentence(q) {
        return Online { google: Some(sources::fetch_google(client, q, dir).await), ..Default::default() };
    }
    let want_dict = dir == Direction::EnVi && word_count(q) <= 3;
    let dict_fut = async {
        if want_dict {
            Some(sources::fetch_dict(client, q).await)
        } else {
            None
        }
    };
    let (wiki, google, memory, dict) = tokio::join!(
        sources::fetch_wiki(client, q, dir),
        sources::fetch_google(client, q, dir),
        sources::fetch_mymemory(client, q, dir),
        dict_fut,
    );
    Online { wiki: Some(wiki), google: Some(google), memory: Some(memory), dict }
}

fn label(source: &str) -> &'static str {
    match source {
        "wikipedia" => "Wikipedia",
        "google" => "Google Dịch",
        "mymemory" => "Bộ nhớ dịch",
        _ => "Nguồn khác",
    }
}

/// Viết thường chữ cái đầu (tên bài Wikipedia luôn viết hoa chữ đầu), trừ khi từ đầu là viết tắt như BERT, GPU.
fn lower_first(s: &str) -> String {
    let first_word = s.split_whitespace().next().unwrap_or("");
    let is_acronym = first_word.chars().filter(|c| c.is_alphabetic()).count() >= 2
        && first_word.chars().filter(|c| c.is_alphabetic()).all(char::is_uppercase);
    if is_acronym {
        return s.to_string();
    }
    let mut chars = s.chars();
    match chars.next() {
        Some(c) => c.to_lowercase().chain(chars).collect(),
        None => String::new(),
    }
}

/// Ghép kết quả thành danh sách gợi ý. Hàm thuần, không gọi mạng.
pub fn merge(q: &str, dir: Direction, glossary: Vec<Term>, in_text: Vec<Term>, online: Online) -> LookupResult {
    let mut errors = Vec::new();
    let mut cands: Vec<Candidate> = Vec::new();
    let mut push = |text: &str, source: &str, detail: String| {
        let text = clean(text);
        if text.is_empty() || fold(&text) == fold(q) && dir == Direction::EnVi && source != "wikipedia" {
            return;
        }
        if let Some(c) = cands.iter_mut().find(|c| fold(&c.text) == fold(&text)) {
            let l = label(source).to_string();
            if c.source != source && !c.also.contains(&l) {
                c.also.push(l);
            }
            return;
        }
        cands.push(Candidate { text, source: source.into(), detail, also: vec![] });
    };

    let mut wiki = None;
    match online.wiki {
        Some(Ok(Some(w))) => {
            if let Some(t) = &w.linked_title {
                let lang = if dir == Direction::EnVi { "tiếng Việt" } else { "tiếng Anh" };
                let mut title = sources::strip_wiki_qualifier(t);
                if q == q.to_lowercase() {
                    title = lower_first(&title);
                }
                push(&title, "wikipedia", format!("Tên bài Wikipedia {lang}"));
            }
            wiki = Some(w);
        }
        Some(Err(_)) => errors.push("wikipedia".to_string()),
        _ => {}
    }
    match online.google {
        Some(Ok(Some(g))) => push(&g, "google", "Dịch máy".into()),
        Some(Err(_)) => errors.push("google".to_string()),
        _ => {}
    }
    match online.memory {
        Some(Ok(ms)) => {
            for m in ms {
                push(&m.text, "mymemory", format!("Bản dịch người dùng, độ khớp {:.0}%", m.score * 100.0));
            }
        }
        Some(Err(_)) => errors.push("mymemory".to_string()),
        None => {}
    }
    let mut dict = DictInfo::default();
    match online.dict {
        Some(Ok(d)) => dict = d,
        Some(Err(_)) => errors.push("dictionary".to_string()),
        None => {}
    }

    // Ghi chú sự đồng thuận vào phần mô tả để người dùng thấy ngay.
    for c in &mut cands {
        if !c.also.is_empty() {
            c.detail = format!("{} · {} cũng gợi ý", c.detail, c.also.join(", "));
        }
    }
    // Gợi ý được nhiều nguồn đồng ý xếp lên trước (giữ thứ tự gốc khi bằng nhau).
    cands.sort_by(|a, b| b.also.len().cmp(&a.also.len()));

    LookupResult {
        query: q.to_string(),
        direction: dir,
        mode: if is_sentence(q) { "sentence" } else { "term" }.into(),
        glossary,
        in_text,
        candidates: cands,
        phonetic: dict.phonetic,
        definitions: dict.definitions,
        wiki,
        errors,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn wiki(link: Option<&str>) -> WikiInfo {
        WikiInfo { title: "Overfitting".into(), url: "u".into(), extract: "e".into(), linked_title: link.map(String::from) }
    }

    #[test]
    fn prepare_rejects_empty_and_huge() {
        assert!(prepare_query("   ").is_err());
        assert!(prepare_query(&"a ".repeat(MAX_CHARS)).is_err());
        assert_eq!(prepare_query("  over\nfitting ").unwrap(), "over fitting");
    }

    #[test]
    fn sentence_detection() {
        assert!(!is_sentence("stochastic gradient descent"));
        assert!(is_sentence("We evaluate the robustness of the model under distribution shift."));
    }

    #[test]
    fn merges_and_orders_by_agreement() {
        let online = Online {
            wiki: Some(Ok(Some(wiki(Some("Sự quá khớp"))))),
            google: Some(Ok(Some("trang bị quá mức".into()))),
            memory: Some(Ok(vec![
                MemoryMatch { text: "quá khớp".into(), score: 0.9 },
                MemoryMatch { text: "Trang bị quá mức".into(), score: 0.85 },
            ])),
            dict: None,
        };
        let r = merge("overfitting", Direction::EnVi, vec![], vec![], online);
        let texts: Vec<_> = r.candidates.iter().map(|c| c.text.as_str()).collect();
        // "trang bị quá mức" được 2 nguồn đồng ý nên lên đầu; bản trùng chữ hoa bị gộp.
        assert_eq!(texts, vec!["trang bị quá mức", "sự quá khớp", "quá khớp"]);
        assert_eq!(r.candidates[0].also, vec!["Bộ nhớ dịch".to_string()]);
        assert!(r.candidates[0].detail.contains("Bộ nhớ dịch cũng gợi ý"));
        assert_eq!(r.mode, "term");
        assert!(r.errors.is_empty());
        assert!(r.wiki.is_some());
    }

    #[test]
    fn lowercases_wikipedia_title_when_query_is_lowercase() {
        // Tên bài Wikipedia luôn viết hoa chữ đầu, nhưng thuật ngữ giữa câu thì viết thường.
        let online = Online { wiki: Some(Ok(Some(wiki(Some("Sự quá khớp"))))), ..Default::default() };
        let r = merge("overfitting", Direction::EnVi, vec![], vec![], online);
        assert_eq!(r.candidates[0].text, "sự quá khớp");

        let online = Online { wiki: Some(Ok(Some(wiki(Some("Deep learning"))))), ..Default::default() };
        assert_eq!(merge("học sâu", Direction::ViEn, vec![], vec![], online).candidates[0].text, "deep learning");

        // Người dùng gõ hoa (tên riêng) thì giữ nguyên; từ viết tắt cũng giữ nguyên.
        let online = Online { wiki: Some(Ok(Some(wiki(Some("Python (ngôn ngữ lập trình)"))))), ..Default::default() };
        assert_eq!(merge("Python", Direction::EnVi, vec![], vec![], online).candidates[0].text, "Python");
        let online = Online { wiki: Some(Ok(Some(wiki(Some("BERT (mô hình ngôn ngữ)"))))), ..Default::default() };
        assert_eq!(merge("bert", Direction::EnVi, vec![], vec![], online).candidates[0].text, "BERT");
    }

    #[test]
    fn strips_wikipedia_qualifier() {
        let online = Online { wiki: Some(Ok(Some(wiki(Some("Cơ chế chú ý (học máy)"))))), ..Default::default() };
        let r = merge("attention", Direction::EnVi, vec![], vec![], online);
        assert_eq!(r.candidates[0].text, "cơ chế chú ý");
        assert_eq!(r.candidates[0].source, "wikipedia");
    }

    #[test]
    fn drops_google_echo_of_english_input() {
        let online = Online { google: Some(Ok(Some("Transformer".into()))), ..Default::default() };
        let r = merge("transformer", Direction::EnVi, vec![], vec![], online);
        assert!(r.candidates.is_empty());
    }

    #[test]
    fn keeps_wikipedia_title_identical_to_query() {
        // Bài Wikipedia tiếng Việt giữ nguyên tên tiếng Anh là thông tin hữu ích: ngành dùng nguyên từ gốc.
        let online = Online { wiki: Some(Ok(Some(wiki(Some("Transformer"))))), ..Default::default() };
        let r = merge("transformer", Direction::EnVi, vec![], vec![], online);
        assert_eq!(r.candidates.len(), 1);
    }

    #[test]
    fn collects_errors_per_source() {
        let online = Online {
            wiki: Some(Err("timeout".into())),
            google: Some(Ok(Some("học sâu".into()))),
            memory: Some(Err("quota".into())),
            dict: Some(Err("x".into())),
        };
        let r = merge("deep learning", Direction::EnVi, vec![], vec![], online);
        assert_eq!(r.errors, vec!["wikipedia", "mymemory", "dictionary"]);
        assert_eq!(r.candidates.len(), 1);
    }

    #[test]
    fn passes_dictionary_info_through() {
        let d = DictInfo { phonetic: Some("/x/".into()), definitions: vec![Definition { pos: "noun".into(), text: "t".into(), example: None }] };
        let online = Online { dict: Some(Ok(d)), ..Default::default() };
        let r = merge("robust", Direction::EnVi, vec![], vec![], online);
        assert_eq!(r.phonetic.as_deref(), Some("/x/"));
        assert_eq!(r.definitions.len(), 1);
    }

    #[test]
    fn sentence_mode_flag() {
        let q = "We evaluate the robustness of the model under distribution shift.";
        let online = Online { google: Some(Ok(Some("Chúng tôi đánh giá...".into()))), ..Default::default() };
        let r = merge(q, Direction::EnVi, vec![], vec![], online);
        assert_eq!(r.mode, "sentence");
        assert_eq!(r.candidates.len(), 1);
    }
}
