//! Lõi của Thông Ngữ: bảng thuật ngữ cá nhân, các nguồn tra cứu miễn phí và phần gom kết quả.
//! Không phụ thuộc giao diện nên kiểm thử độc lập được.

pub mod glossary;
pub mod lang;
pub mod lookup;
pub mod sources;

pub use glossary::{Glossary, Term};
pub use lang::Direction;
pub use lookup::LookupResult;
