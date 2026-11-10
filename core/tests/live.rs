//! Test gọi mạng thật. Chạy tay: cargo test -p thongngu-core --test live -- --ignored --nocapture

use thongngu_core::lookup::{fetch_online, merge};
use thongngu_core::sources::client;
use thongngu_core::Direction;

async fn run(q: &str, dir: Direction) -> thongngu_core::LookupResult {
    let c = client();
    let online = fetch_online(&c, q, dir).await;
    let r = merge(q, dir, vec![], vec![], online);
    println!("\n== {q} ({:?})", dir);
    for c in &r.candidates {
        println!("  [{}] {} | {}", c.source, c.text, c.detail);
    }
    println!("  phonetic={:?} defs={} errors={:?}", r.phonetic, r.definitions.len(), r.errors);
    r
}

#[tokio::test]
#[ignore]
async fn live_en_to_vi_term() {
    let r = run("overfitting", Direction::EnVi).await;
    assert!(r.errors.iter().all(|e| e != "wikipedia" && e != "google"), "{:?}", r.errors);
    assert!(r.candidates.iter().any(|c| c.source == "wikipedia" && c.text == "sự quá khớp"));
    assert!(r.candidates.iter().any(|c| c.source == "google" || c.also.iter().any(|a| a == "Google Dịch")));
}

#[tokio::test]
#[ignore]
async fn live_vi_to_en_term() {
    let r = run("học sâu", Direction::ViEn).await;
    assert!(r.candidates.iter().any(|c| c.text.eq_ignore_ascii_case("deep learning")));
}

#[tokio::test]
#[ignore]
async fn live_dictionary_and_sentence() {
    let r = run("robust", Direction::EnVi).await;
    assert!(!r.definitions.is_empty());
    let s = run("We evaluate the robustness of the model under distribution shift.", Direction::EnVi).await;
    assert_eq!(s.mode, "sentence");
    assert_eq!(s.candidates.len(), 1);
}
