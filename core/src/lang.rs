//! Nhận chiều dịch và chuẩn hoá chữ để so khớp không phân biệt hoa thường, dấu.

use serde::{Deserialize, Serialize};
use unicode_normalization::UnicodeNormalization;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Direction {
    #[serde(rename = "en-vi")]
    EnVi,
    #[serde(rename = "vi-en")]
    ViEn,
}

impl Direction {
    pub fn source(self) -> &'static str {
        match self {
            Direction::EnVi => "en",
            Direction::ViEn => "vi",
        }
    }
    pub fn target(self) -> &'static str {
        match self {
            Direction::EnVi => "vi",
            Direction::ViEn => "en",
        }
    }
}

/// Có ký tự mang dấu tiếng Việt (kể cả đ) thì coi là tiếng Việt, còn lại là tiếng Anh.
pub fn detect(text: &str) -> Direction {
    let has_vi = text.nfd().any(|c| is_vi_mark(c)) || text.chars().any(|c| matches!(c, 'đ' | 'Đ'));
    if has_vi {
        Direction::ViEn
    } else {
        Direction::EnVi
    }
}

fn is_vi_mark(c: char) -> bool {
    // Dấu thanh và dấu mũ/trăng/móc dùng trong tiếng Việt (dạng tách NFD).
    matches!(c, '\u{0300}' | '\u{0301}' | '\u{0303}' | '\u{0309}' | '\u{0323}' | '\u{0302}' | '\u{0306}' | '\u{031B}')
}

/// Chữ thường, bỏ dấu, đ -> d, gộp khoảng trắng. Dùng để tìm kiếm và so trùng.
pub fn fold(text: &str) -> String {
    let lowered = text.to_lowercase();
    let stripped: String = lowered
        .nfd()
        .filter(|c| !unicode_normalization::char::is_combining_mark(*c))
        .map(|c| if c == 'đ' { 'd' } else { c })
        .collect();
    stripped.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Chuẩn hoá để lưu: gộp khoảng trắng, NFC (tránh 2 cách mã hoá cùng một chữ có dấu).
pub fn clean(text: &str) -> String {
    text.nfc().collect::<String>().split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Số từ (tách theo khoảng trắng).
pub fn word_count(text: &str) -> usize {
    text.split_whitespace().count()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_english() {
        assert_eq!(detect("overfitting"), Direction::EnVi);
        assert_eq!(detect("distribution shift"), Direction::EnVi);
        assert_eq!(detect("café"), Direction::ViEn); // có dấu sắc: chấp nhận coi là tiếng Việt
    }

    #[test]
    fn detects_vietnamese() {
        assert_eq!(detect("học sâu"), Direction::ViEn);
        assert_eq!(detect("đa"), Direction::ViEn);
        assert_eq!(detect("Mô hình"), Direction::ViEn);
        // Chữ tiếng Việt không dấu thì không phân biệt được, mặc định Anh -> Việt.
        assert_eq!(detect("hoc sau"), Direction::EnVi);
    }

    #[test]
    fn detects_vietnamese_in_decomposed_form() {
        let nfd: String = "học sâu".nfd().collect();
        assert_eq!(detect(&nfd), Direction::ViEn);
    }

    #[test]
    fn fold_strips_marks_and_case() {
        assert_eq!(fold("Học Sâu"), "hoc sau");
        assert_eq!(fold("ĐỘ  bền   vững"), "do ben vung");
        assert_eq!(fold("  Overfitting "), "overfitting");
        assert_eq!(fold("tính nội sinh"), "tinh noi sinh");
    }

    #[test]
    fn clean_normalizes_whitespace_and_composition() {
        let nfd: String = "học sâu".nfd().collect();
        assert_eq!(clean(&format!("  {}\n ", nfd)), "học sâu");
        assert_eq!(clean(&nfd).chars().count(), "học sâu".chars().count());
    }

    #[test]
    fn direction_codes() {
        assert_eq!(Direction::EnVi.source(), "en");
        assert_eq!(Direction::EnVi.target(), "vi");
        assert_eq!(Direction::ViEn.source(), "vi");
        assert_eq!(serde_json::to_string(&Direction::ViEn).unwrap(), "\"vi-en\"");
    }
}
