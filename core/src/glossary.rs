//! Bảng thuật ngữ cá nhân lưu bằng SQLite.
//!
//! Quy tắc trùng: một thuật ngữ tiếng Anh trong cùng một chuyên ngành chỉ có một cách dịch
//! (so khớp không phân biệt hoa thường/dấu). Lưu lại cùng cặp (tiếng Anh, chuyên ngành) sẽ cập nhật.

use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, OptionalExtension, Row};
use serde::{Deserialize, Serialize};

use crate::lang::{clean, fold, Direction};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Term {
    pub id: Option<i64>,
    pub en: String,
    pub vi: String,
    #[serde(default)]
    pub field: String,
    #[serde(default)]
    pub note: String,
    #[serde(default)]
    pub created_at: i64,
    #[serde(default)]
    pub updated_at: i64,
}

impl Term {
    pub fn new(en: &str, vi: &str, field: &str, note: &str) -> Self {
        Term { id: None, en: en.into(), vi: vi.into(), field: field.into(), note: note.into(), created_at: 0, updated_at: 0 }
    }
}

pub struct Glossary {
    conn: Connection,
}

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS terms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    en TEXT NOT NULL,
    vi TEXT NOT NULL,
    field TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    en_fold TEXT NOT NULL,
    vi_fold TEXT NOT NULL,
    field_fold TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS terms_en_field ON terms(en_fold, field_fold);
CREATE INDEX IF NOT EXISTS terms_vi ON terms(vi_fold);
CREATE TABLE IF NOT EXISTS history (
    query TEXT PRIMARY KEY,
    ts INTEGER NOT NULL
);
";

fn now() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as i64).unwrap_or(0)
}

fn row_to_term(r: &Row) -> rusqlite::Result<Term> {
    Ok(Term {
        id: Some(r.get(0)?),
        en: r.get(1)?,
        vi: r.get(2)?,
        field: r.get(3)?,
        note: r.get(4)?,
        created_at: r.get(5)?,
        updated_at: r.get(6)?,
    })
}

const COLS: &str = "id, en, vi, field, note, created_at, updated_at";

impl Glossary {
    pub fn open(path: &Path) -> Result<Self, String> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir).map_err(|e| format!("Không tạo được thư mục dữ liệu: {e}"))?;
        }
        let conn = Connection::open(path).map_err(|e| format!("Không mở được cơ sở dữ liệu: {e}"))?;
        Self::init(conn)
    }

    pub fn open_in_memory() -> Result<Self, String> {
        Self::init(Connection::open_in_memory().map_err(|e| e.to_string())?)
    }

    fn init(conn: Connection) -> Result<Self, String> {
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;").map_err(|e| e.to_string())?;
        conn.execute_batch(SCHEMA).map_err(|e| e.to_string())?;
        Ok(Glossary { conn })
    }

    /// Thêm mới hoặc cập nhật. Trả về bản ghi sau khi lưu.
    pub fn save(&self, t: &Term) -> Result<Term, String> {
        let en = clean(&t.en);
        let vi = clean(&t.vi);
        let field = clean(&t.field);
        let note = t.note.trim().to_string();
        if en.is_empty() || vi.is_empty() {
            return Err("Cần có cả tiếng Anh và tiếng Việt".into());
        }
        let (en_f, vi_f, field_f) = (fold(&en), fold(&vi), fold(&field));
        let ts = now();

        // Bản ghi khác đã chiếm cặp (tiếng Anh, chuyên ngành) này?
        let clash: Option<i64> = self
            .conn
            .query_row("SELECT id FROM terms WHERE en_fold = ?1 AND field_fold = ?2", params![en_f, field_f], |r| r.get(0))
            .optional()
            .map_err(|e| e.to_string())?;

        let id = match (t.id, clash) {
            (Some(id), Some(other)) if other != id => {
                return Err(format!("\"{en}\" đã có trong bảng{}", if field.is_empty() { String::new() } else { format!(" (chuyên ngành {field})") }));
            }
            (Some(id), _) | (None, Some(id)) => {
                let n = self
                    .conn
                    .execute(
                        "UPDATE terms SET en=?1, vi=?2, field=?3, note=?4, en_fold=?5, vi_fold=?6, field_fold=?7, updated_at=?8 WHERE id=?9",
                        params![en, vi, field, note, en_f, vi_f, field_f, ts, id],
                    )
                    .map_err(|e| e.to_string())?;
                if n == 0 {
                    return Err("Thuật ngữ không còn tồn tại".into());
                }
                id
            }
            (None, None) => {
                self.conn
                    .execute(
                        "INSERT INTO terms (en, vi, field, note, en_fold, vi_fold, field_fold, created_at, updated_at) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?8)",
                        params![en, vi, field, note, en_f, vi_f, field_f, ts],
                    )
                    .map_err(|e| e.to_string())?;
                self.conn.last_insert_rowid()
            }
        };
        self.get(id)?.ok_or_else(|| "Không đọc lại được thuật ngữ vừa lưu".into())
    }

    pub fn get(&self, id: i64) -> Result<Option<Term>, String> {
        self.conn
            .query_row(&format!("SELECT {COLS} FROM terms WHERE id = ?1"), params![id], row_to_term)
            .optional()
            .map_err(|e| e.to_string())
    }

    pub fn delete(&self, id: i64) -> Result<bool, String> {
        self.conn.execute("DELETE FROM terms WHERE id = ?1", params![id]).map(|n| n > 0).map_err(|e| e.to_string())
    }

    /// Danh sách theo bộ lọc. `query` khớp một phần tiếng Anh, tiếng Việt hoặc ghi chú (không dấu).
    /// Khớp đầu từ được xếp trước, sau đó theo thứ tự chữ cái tiếng Anh.
    pub fn list(&self, query: &str, field: &str) -> Result<Vec<Term>, String> {
        let q = fold(query);
        let f = fold(field);
        let like = format!("%{}%", escape_like(&q));
        let prefix = format!("{}%", escape_like(&q));
        let sql = format!(
            "SELECT {COLS} FROM terms
             WHERE (?1 = '' OR en_fold LIKE ?2 ESCAPE '\\' OR vi_fold LIKE ?2 ESCAPE '\\' OR lower(note) LIKE ?2 ESCAPE '\\')
               AND (?3 = '' OR field_fold = ?3)
             ORDER BY (en_fold LIKE ?4 ESCAPE '\\' OR vi_fold LIKE ?4 ESCAPE '\\') DESC, en_fold ASC"
        );
        let mut stmt = self.conn.prepare(&sql).map_err(|e| e.to_string())?;
        let rows = stmt.query_map(params![q, like, f, prefix], row_to_term).map_err(|e| e.to_string())?;
        rows.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())
    }

    pub fn count(&self) -> Result<i64, String> {
        self.conn.query_row("SELECT COUNT(*) FROM terms", [], |r| r.get(0)).map_err(|e| e.to_string())
    }

    /// Các chuyên ngành đã dùng, dùng nhiều xếp trước.
    pub fn fields(&self) -> Result<Vec<String>, String> {
        let mut stmt = self
            .conn
            .prepare("SELECT field FROM terms WHERE field != '' GROUP BY field_fold ORDER BY COUNT(*) DESC, field ASC")
            .map_err(|e| e.to_string())?;
        let rows = stmt.query_map([], |r| r.get(0)).map_err(|e| e.to_string())?;
        rows.collect::<Result<Vec<String>, _>>().map_err(|e| e.to_string())
    }

    /// Thuật ngữ khớp đúng cụm vừa tra (theo chiều dịch).
    pub fn find_exact(&self, text: &str, dir: Direction) -> Result<Vec<Term>, String> {
        let col = match dir {
            Direction::EnVi => "en_fold",
            Direction::ViEn => "vi_fold",
        };
        let mut stmt = self
            .conn
            .prepare(&format!("SELECT {COLS} FROM terms WHERE {col} = ?1 ORDER BY updated_at DESC"))
            .map_err(|e| e.to_string())?;
        let rows = stmt.query_map(params![fold(text)], row_to_term).map_err(|e| e.to_string())?;
        rows.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())
    }

    /// Thuật ngữ đã lưu xuất hiện bên trong một câu/đoạn (khớp nguyên cụm từ).
    pub fn find_in_text(&self, text: &str, dir: Direction) -> Result<Vec<Term>, String> {
        let hay = format!(" {} ", tokens(&fold(text)));
        let all = self.list("", "")?;
        let mut hits: Vec<Term> = all
            .into_iter()
            .filter(|t| {
                let needle = match dir {
                    Direction::EnVi => fold(&t.en),
                    Direction::ViEn => fold(&t.vi),
                };
                let needle = tokens(&needle);
                !needle.is_empty() && hay.contains(&format!(" {needle} "))
            })
            .collect();
        // Cụm dài trước (ví dụ "gradient descent" trước "gradient").
        hits.sort_by(|a, b| b.en.len().cmp(&a.en.len()));
        Ok(hits)
    }

    // ---------- Lịch sử tra ----------
    pub fn push_history(&self, query: &str) -> Result<(), String> {
        let q = clean(query);
        if q.is_empty() {
            return Ok(());
        }
        self.conn
            .execute("INSERT INTO history (query, ts) VALUES (?1, ?2) ON CONFLICT(query) DO UPDATE SET ts = excluded.ts", params![q, now()])
            .map_err(|e| e.to_string())?;
        self.conn
            .execute("DELETE FROM history WHERE query NOT IN (SELECT query FROM history ORDER BY ts DESC LIMIT 50)", [])
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn recent(&self, limit: usize) -> Result<Vec<String>, String> {
        let mut stmt = self.conn.prepare("SELECT query FROM history ORDER BY ts DESC LIMIT ?1").map_err(|e| e.to_string())?;
        let rows = stmt.query_map(params![limit as i64], |r| r.get(0)).map_err(|e| e.to_string())?;
        rows.collect::<Result<Vec<String>, _>>().map_err(|e| e.to_string())
    }

    // ---------- CSV ----------
    /// Xuất CSV (UTF-8 có BOM để Excel mở đúng tiếng Việt). Cột: en,vi,field,note.
    pub fn export_csv(&self, path: &Path) -> Result<usize, String> {
        let terms = self.list("", "")?;
        let mut buf: Vec<u8> = vec![0xEF, 0xBB, 0xBF];
        {
            let mut w = csv::Writer::from_writer(&mut buf);
            w.write_record(["en", "vi", "field", "note"]).map_err(|e| e.to_string())?;
            for t in &terms {
                w.write_record([&t.en, &t.vi, &t.field, &t.note]).map_err(|e| e.to_string())?;
            }
            w.flush().map_err(|e| e.to_string())?;
        }
        std::fs::write(path, buf).map_err(|e| format!("Không ghi được file: {e}"))?;
        Ok(terms.len())
    }

    /// Xuất theo định dạng glossary của BabelDOC/PDFMathTranslate (source,target,tgt_lng).
    pub fn export_babeldoc_csv(&self, path: &Path) -> Result<usize, String> {
        let terms = self.list("", "")?;
        let mut w = csv::Writer::from_path(path).map_err(|e| e.to_string())?;
        w.write_record(["source", "target", "tgt_lng"]).map_err(|e| e.to_string())?;
        for t in &terms {
            w.write_record([t.en.as_str(), t.vi.as_str(), "vi"]).map_err(|e| e.to_string())?;
        }
        w.flush().map_err(|e| e.to_string())?;
        Ok(terms.len())
    }

    /// Nhập CSV. Nhận cả tiêu đề en/vi/field/note lẫn source/target (BabelDOC).
    /// Trả về số dòng đã lưu; dòng thiếu tiếng Anh hoặc tiếng Việt bị bỏ qua.
    pub fn import_csv(&self, path: &Path) -> Result<usize, String> {
        let raw = std::fs::read(path).map_err(|e| format!("Không đọc được file: {e}"))?;
        let data = raw.strip_prefix(&[0xEF, 0xBB, 0xBF][..]).unwrap_or(&raw);
        let mut r = csv::ReaderBuilder::new().flexible(true).from_reader(data);
        let headers: Vec<String> = r.headers().map_err(|e| e.to_string())?.iter().map(|h| h.trim().to_lowercase()).collect();
        let idx = |names: &[&str]| headers.iter().position(|h| names.contains(&h.as_str()));
        let (Some(i_en), Some(i_vi)) = (idx(&["en", "english", "source", "tiếng anh"]), idx(&["vi", "vietnamese", "target", "tiếng việt"])) else {
            return Err("File CSV cần có cột en và vi (hoặc source và target)".into());
        };
        let i_field = idx(&["field", "chuyên ngành", "domain"]);
        let i_note = idx(&["note", "ghi chú", "notes"]);
        let mut n = 0;
        for rec in r.records() {
            let rec = rec.map_err(|e| e.to_string())?;
            let get = |i: Option<usize>| i.and_then(|i| rec.get(i)).unwrap_or("").to_string();
            let t = Term::new(&get(Some(i_en)), &get(Some(i_vi)), &get(i_field), &get(i_note));
            if t.en.trim().is_empty() || t.vi.trim().is_empty() {
                continue;
            }
            self.save(&t)?;
            n += 1;
        }
        Ok(n)
    }
}

fn escape_like(s: &str) -> String {
    s.replace('\\', "\\\\").replace('%', "\\%").replace('_', "\\_")
}

/// Bỏ dấu câu, chỉ giữ chữ/số, để so khớp nguyên cụm từ trong câu.
fn tokens(s: &str) -> String {
    s.split(|c: char| !c.is_alphanumeric()).filter(|w| !w.is_empty()).collect::<Vec<_>>().join(" ")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn g() -> Glossary {
        Glossary::open_in_memory().unwrap()
    }

    #[test]
    fn save_and_get() {
        let g = g();
        let t = g.save(&Term::new("  Overfitting ", "quá khớp", "Học máy", "ghi chú")).unwrap();
        assert_eq!(t.en, "Overfitting");
        assert_eq!(t.vi, "quá khớp");
        assert!(t.id.is_some());
        assert!(t.created_at > 0);
        assert_eq!(g.count().unwrap(), 1);
    }

    #[test]
    fn rejects_empty() {
        let g = g();
        assert!(g.save(&Term::new("", "x", "", "")).is_err());
        assert!(g.save(&Term::new("x", "  ", "", "")).is_err());
    }

    #[test]
    fn same_en_and_field_updates_instead_of_duplicating() {
        let g = g();
        let a = g.save(&Term::new("robustness", "tính mạnh mẽ", "Học máy", "")).unwrap();
        let b = g.save(&Term::new("Robustness", "tính bền vững", "học máy", "đổi ý")).unwrap();
        assert_eq!(a.id, b.id);
        assert_eq!(g.count().unwrap(), 1);
        assert_eq!(b.vi, "tính bền vững");
        assert_eq!(b.note, "đổi ý");
    }

    #[test]
    fn same_en_different_field_is_separate() {
        let g = g();
        g.save(&Term::new("kernel", "nhân", "Học máy", "")).unwrap();
        g.save(&Term::new("kernel", "hạt nhân", "Hệ điều hành", "")).unwrap();
        assert_eq!(g.count().unwrap(), 2);
        assert_eq!(g.find_exact("Kernel", Direction::EnVi).unwrap().len(), 2);
    }

    #[test]
    fn editing_into_existing_pair_is_rejected() {
        let g = g();
        g.save(&Term::new("bias", "độ chệch", "Thống kê", "")).unwrap();
        let mut b = g.save(&Term::new("variance", "phương sai", "Thống kê", "")).unwrap();
        b.en = "BIAS".into();
        let err = g.save(&b).unwrap_err();
        assert!(err.contains("đã có"), "{err}");
    }

    #[test]
    fn edit_by_id_changes_en() {
        let g = g();
        let mut t = g.save(&Term::new("overfiting", "quá khớp", "", "")).unwrap();
        t.en = "overfitting".into();
        let t2 = g.save(&t).unwrap();
        assert_eq!(t2.id, t.id);
        assert_eq!(t2.en, "overfitting");
        assert_eq!(g.count().unwrap(), 1);
    }

    #[test]
    fn list_search_is_accent_and_case_insensitive() {
        let g = g();
        g.save(&Term::new("deep learning", "học sâu", "Học máy", "")).unwrap();
        g.save(&Term::new("endogeneity", "tính nội sinh", "Kinh tế lượng", "")).unwrap();
        assert_eq!(g.list("hoc sau", "").unwrap().len(), 1);
        assert_eq!(g.list("HỌC", "").unwrap().len(), 1);
        assert_eq!(g.list("DEEP", "").unwrap().len(), 1);
        assert_eq!(g.list("", "kinh te luong").unwrap().len(), 1);
        assert_eq!(g.list("", "").unwrap().len(), 2);
        assert_eq!(g.list("zzz", "").unwrap().len(), 0);
    }

    #[test]
    fn list_escapes_like_wildcards() {
        let g = g();
        g.save(&Term::new("p_value", "giá trị p", "", "")).unwrap();
        g.save(&Term::new("pxvalue", "khác", "", "")).unwrap();
        let r = g.list("p_v", "").unwrap();
        assert_eq!(r.len(), 1);
        assert_eq!(r[0].en, "p_value");
    }

    #[test]
    fn list_puts_prefix_matches_first() {
        let g = g();
        g.save(&Term::new("stochastic gradient", "gradient ngẫu nhiên", "", "")).unwrap();
        g.save(&Term::new("gradient descent", "suy giảm độ dốc", "", "")).unwrap();
        let r = g.list("gradient", "").unwrap();
        assert_eq!(r[0].en, "gradient descent");
    }

    #[test]
    fn fields_ranked_by_usage() {
        let g = g();
        g.save(&Term::new("a", "a", "Kinh tế", "")).unwrap();
        g.save(&Term::new("b", "b", "Học máy", "")).unwrap();
        g.save(&Term::new("c", "c", "Học máy", "")).unwrap();
        g.save(&Term::new("d", "d", "", "")).unwrap();
        assert_eq!(g.fields().unwrap(), vec!["Học máy".to_string(), "Kinh tế".to_string()]);
    }

    #[test]
    fn find_exact_both_directions() {
        let g = g();
        g.save(&Term::new("deep learning", "học sâu", "", "")).unwrap();
        assert_eq!(g.find_exact("Deep Learning", Direction::EnVi).unwrap().len(), 1);
        assert_eq!(g.find_exact("Học sâu", Direction::ViEn).unwrap().len(), 1);
        assert_eq!(g.find_exact("deep", Direction::EnVi).unwrap().len(), 0);
    }

    #[test]
    fn find_in_text_matches_whole_phrases_only() {
        let g = g();
        g.save(&Term::new("gradient descent", "suy giảm độ dốc", "", "")).unwrap();
        g.save(&Term::new("gradient", "gradient", "", "")).unwrap();
        g.save(&Term::new("rate", "tốc độ", "", "")).unwrap();
        let hits = g.find_in_text("We use stochastic gradient descent, with a learning-rate schedule.", Direction::EnVi).unwrap();
        let ens: Vec<_> = hits.iter().map(|t| t.en.as_str()).collect();
        assert_eq!(ens, vec!["gradient descent", "gradient", "rate"]);
        let none = g.find_in_text("The gradients accelerate.", Direction::EnVi).unwrap();
        assert!(none.is_empty(), "không được khớp một phần của từ: {none:?}");
    }

    #[test]
    fn delete_works() {
        let g = g();
        let t = g.save(&Term::new("a", "b", "", "")).unwrap();
        assert!(g.delete(t.id.unwrap()).unwrap());
        assert!(!g.delete(t.id.unwrap()).unwrap());
        assert_eq!(g.count().unwrap(), 0);
    }

    #[test]
    fn history_dedupes_and_orders_newest_first() {
        let g = g();
        g.push_history("overfitting").unwrap();
        std::thread::sleep(std::time::Duration::from_millis(3));
        g.push_history("robustness").unwrap();
        std::thread::sleep(std::time::Duration::from_millis(3));
        g.push_history("overfitting").unwrap();
        g.push_history("   ").unwrap();
        assert_eq!(g.recent(10).unwrap(), vec!["overfitting".to_string(), "robustness".to_string()]);
        assert_eq!(g.recent(1).unwrap().len(), 1);
    }

    #[test]
    fn csv_roundtrip_keeps_vietnamese_and_commas() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("g.csv");
        let g1 = g();
        g1.save(&Term::new("endogeneity", "tính nội sinh", "Kinh tế lượng", "Ghi chú có dấu phẩy, và \"ngoặc\"")).unwrap();
        g1.save(&Term::new("overfitting", "quá khớp", "Học máy", "")).unwrap();
        assert_eq!(g1.export_csv(&path).unwrap(), 2);
        let bytes = std::fs::read(&path).unwrap();
        assert_eq!(&bytes[..3], &[0xEF, 0xBB, 0xBF], "phải có BOM cho Excel");

        let g2 = g();
        assert_eq!(g2.import_csv(&path).unwrap(), 2);
        let t = &g2.find_exact("endogeneity", Direction::EnVi).unwrap()[0];
        assert_eq!(t.vi, "tính nội sinh");
        assert_eq!(t.field, "Kinh tế lượng");
        assert_eq!(t.note, "Ghi chú có dấu phẩy, và \"ngoặc\"");
    }

    #[test]
    fn import_accepts_babeldoc_format_and_skips_blank_rows() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("b.csv");
        std::fs::write(&path, "source,target,tgt_lng\nattention,cơ chế chú ý,vi\n,thiếu,vi\nencoder,,vi\n").unwrap();
        let g = g();
        assert_eq!(g.import_csv(&path).unwrap(), 1);
        assert_eq!(g.list("attention", "").unwrap()[0].vi, "cơ chế chú ý");
    }

    #[test]
    fn import_rejects_unknown_headers() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("x.csv");
        std::fs::write(&path, "a,b\n1,2\n").unwrap();
        assert!(g().import_csv(&path).is_err());
    }

    #[test]
    fn babeldoc_export_has_expected_header() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("bd.csv");
        let g = g();
        g.save(&Term::new("attention", "cơ chế chú ý", "", "")).unwrap();
        g.export_babeldoc_csv(&path).unwrap();
        let s = std::fs::read_to_string(&path).unwrap();
        assert_eq!(s, "source,target,tgt_lng\nattention,cơ chế chú ý,vi\n");
    }

    #[test]
    fn persists_to_disk() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sub").join("t.db");
        {
            let g = Glossary::open(&path).unwrap();
            g.save(&Term::new("a", "b", "", "")).unwrap();
        }
        let g = Glossary::open(&path).unwrap();
        assert_eq!(g.count().unwrap(), 1);
    }
}
