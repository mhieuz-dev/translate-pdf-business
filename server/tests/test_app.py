"""Test máy chủ dịch PDF. Chạy: cd server && .venv/bin/pytest -q
Mặc định dùng cầu nối giả (không mạng). Test dịch thật: THONGNGU_LIVE=1 .venv/bin/pytest -q -k live"""
import json
import os
import sys
import time
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import app as srv  # noqa: E402


def make_pdf(pages=3, text="Overfitting occurs when a model learns the training data too well.") -> bytes:
    d = pymupdf.open()
    for i in range(pages):
        d.new_page().insert_textbox(pymupdf.Rect(72, 72, 520, 700), f"Page {i + 1}. {text}", fontsize=12)
    return d.tobytes()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(srv.S, "data_dir", tmp_path / "data")
    monkeypatch.setattr(srv.S, "bridge", HERE / "fake_bridge.py")
    monkeypatch.setattr(srv.S, "max_per_ip", 2)
    monkeypatch.setattr(srv.S, "max_pages", 40)
    monkeypatch.setattr(srv.S, "max_mb", 30)
    monkeypatch.delenv("FAKE_MODE", raising=False)
    monkeypatch.delenv("FAKE_SLEEP", raising=False)
    srv.JOBS.clear()
    with TestClient(srv.app) as c:
        yield c
    srv.JOBS.clear()


def upload(c, data=None, **form):
    return c.post("/jobs", files={"file": ("bai bao.pdf", data if data is not None else make_pdf(), "application/pdf")}, data=form)


def wait_done(c, job_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        st = c.get(f"/jobs/{job_id}").json()
        if st["status"] in ("done", "error"):
            return st
        time.sleep(0.1)
    raise AssertionError("quá thời gian chờ")


# ---------- hàm thuần ----------
def test_parse_pages():
    assert srv.parse_pages("", 3) == [1, 2, 3]
    assert srv.parse_pages("1-3, 8", 10) == [1, 2, 3, 8]
    assert srv.parse_pages("5-", 7) == [5, 6, 7]
    assert srv.parse_pages("2,2,1", 5) == [1, 2]
    assert srv.parse_pages("1-100", 3) == [1, 2, 3]
    for bad in ("a", "3-1", "0", "1;2", "-3"):
        with pytest.raises(ValueError):
            srv.parse_pages(bad, 10)
    with pytest.raises(ValueError):
        srv.parse_pages("9", 3)


def test_pages_arg():
    assert srv.pages_arg([1, 2, 3, 8]) == "1-3,8"
    assert srv.pages_arg([5]) == "5"
    assert srv.pages_arg([1, 3, 4, 6]) == "1,3-4,6"


def test_friendly_errors():
    assert "quá nhiều yêu cầu" in srv.friendly_error("HTTPError 429 Too Many Requests")
    assert "kết nối" in srv.friendly_error("RetryError[ConnectError]")


# ---------- HTTP ----------
def test_health(client):
    h = client.get("/health").json()
    assert h["ok"] and h["max_mb"] == 30 and h["max_pages"] == 40 and h["waiting"] == 0


def test_rejects_bad_uploads(client, monkeypatch):
    assert upload(client, b"hello").status_code == 400
    assert upload(client, b"%PDF-1.4 broken").status_code == 400
    assert upload(client, lang="fr").status_code == 400
    assert upload(client, pages="abc").status_code == 400
    r = upload(client, pages="9")
    assert r.status_code == 400 and "3 trang" in r.json()["detail"]
    monkeypatch.setattr(srv.S, "max_pages", 2)
    r = upload(client)
    assert r.status_code == 413 and "tối đa 2 trang" in r.json()["detail"]
    assert upload(client, pages="1-2").status_code == 202  # chọn bớt trang thì được
    monkeypatch.setattr(srv.S, "max_mb", 0)
    assert upload(client).status_code == 413


def test_full_flow_with_page_selection(client):
    r = upload(client, pages="1-2", lang="vi", dual="true")
    assert r.status_code == 202
    job = r.json()
    assert len(job["id"]) == 32 and job["pages"] == 2 and job["page_numbers"] == [1, 2]
    st = wait_done(client, job["id"])
    assert st["status"] == "done", st
    assert st["percent"] == 100 and st["files"] == ["dual", "mono", "original"]
    args = json.loads((srv.S.data_dir / job["id"] / "args.json").read_text())
    assert args[args.index("--pages") + 1] == "1-2"
    assert "--only-include-translated-page" in args  # chỉ giữ trang đã chọn
    assert args[args.index("--qps") + 1] == "12" and "--bing" in args and "--no-dual" not in args
    # không ép kiểu chữ: pdf_bridge tự chọn font giống bản gốc (Termes / Latin Modern) cho từng đoạn
    assert "--primary-font-family" not in args
    # pdf2zh 2.9 mặc định chèn dòng watermark tiếng Trung "本文档由..." vào bản dịch
    assert args[args.index("--watermark-output-mode") + 1] == "no_watermark"
    f = client.get(f"/jobs/{job['id']}/files/mono")
    assert f.status_code == 200 and f.headers["content-type"] == "application/pdf" and f.content.startswith(b"%PDF")
    assert f.headers["content-disposition"].startswith("inline")
    d = client.get(f"/jobs/{job['id']}/files/dual?download=1")
    assert d.headers["content-disposition"].startswith("attachment")
    assert "bai%20bao.song-ngu.pdf" in d.headers["content-disposition"] or "bai bao.song-ngu.pdf" in d.headers["content-disposition"]
    # bản gốc trả về đúng các trang đã chọn để đặt cạnh bản dịch
    o = client.get(f"/jobs/{job['id']}/files/original")
    orig = pymupdf.open(stream=o.content, filetype="pdf")
    assert orig.page_count == 2 and "Page 1." in orig[0].get_text() and "Page 2." in orig[1].get_text()
    # thư mục tạm của pdf2zh đã dọn, chỉ còn input + bản dịch
    assert not (srv.S.data_dir / job["id"] / "out").exists()


def test_all_pages_and_no_dual(client):
    job = upload(client, dual="false").json()
    st = wait_done(client, job["id"])
    assert st["files"] == ["mono", "original"]
    assert pymupdf.open(stream=client.get(f"/jobs/{job['id']}/files/original").content, filetype="pdf").page_count == 3
    args = json.loads((srv.S.data_dir / job["id"] / "args.json").read_text())
    assert "--no-dual" in args and "--only-include-translated-page" not in args


def test_progress_only_goes_up(client, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "0.3")
    job = upload(client).json()
    seen = []
    end = time.time() + 20
    while time.time() < end:
        st = client.get(f"/jobs/{job['id']}").json()
        seen.append(st["percent"])
        if st["status"] == "done":
            break
        time.sleep(0.05)
    assert seen == sorted(seen), seen


def test_per_ip_limit_and_queue_position(client, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "0.5")
    a = upload(client).json()
    b = upload(client).json()
    assert b["position"] == 1 or b["status"] == "queued"
    r = upload(client)
    assert r.status_code == 429
    wait_done(client, a["id"])
    wait_done(client, b["id"])
    assert upload(client).status_code == 202  # xong rồi thì gửi tiếp được


def test_error_from_translator_is_friendly(client, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "error")
    st = wait_done(client, upload(client).json()["id"])
    assert st["status"] == "error" and "quá nhiều yêu cầu" in st["error"] and st["files"] == []
    assert client.get(f"/jobs/{st['id']}/files/original").status_code == 404


def test_crash_without_output_is_error(client, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "crash")
    st = wait_done(client, upload(client).json()["id"])
    assert st["status"] == "error" and st["error"]


def test_unknown_or_malformed_ids(client):
    assert client.get("/jobs/" + "0" * 32).status_code == 404
    assert client.get("/jobs/../../etc/passwd").status_code == 404
    assert client.get("/jobs/abc/files/mono").status_code == 404
    job = upload(client).json()
    wait_done(client, job["id"])
    assert client.get(f"/jobs/{job['id']}/files/../input").status_code == 404


def test_cleanup_removes_expired(client):
    job = upload(client).json()
    wait_done(client, job["id"])
    orphan = srv.S.data_dir / "deadbeef"
    orphan.mkdir()
    os.utime(orphan, (0, 0))
    assert srv.cleanup_once(now=time.time()) == 0 + 1  # chỉ thư mục mồ côi cũ
    assert srv.cleanup_once(now=time.time() + srv.S.keep_hours * 3600 + 10) == 1
    assert client.get(f"/jobs/{job['id']}").status_code == 404
    assert not (srv.S.data_dir / job["id"]).exists()


def test_cors_allows_web_origin_only(client):
    ok = client.options("/jobs", headers={"Origin": "https://thong-ngu.vercel.app", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "https://thong-ngu.vercel.app"
    bad = client.options("/jobs", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers


@pytest.mark.skipif(os.environ.get("THONGNGU_LIVE") != "1", reason="dịch thật cần mạng, bật bằng THONGNGU_LIVE=1")
def test_live_translation(client, monkeypatch):
    monkeypatch.setattr(srv.S, "bridge", srv.HERE / "pdf_bridge.py")
    job = upload(client, data=make_pdf(2), pages="1").json()
    st = wait_done(client, job["id"], timeout=300)
    assert st["status"] == "done", st
    mono = pymupdf.open(stream=client.get(f"/jobs/{job['id']}/files/mono").content, filetype="pdf")
    assert mono.page_count == 1, "chỉ giữ đúng trang đã chọn"
    text = mono[0].get_text()
    print(text)
    assert any(w in text.lower() for w in ("mô hình", "dữ liệu", "trang")), text


def test_shutdown_kills_running_translation(tmp_path, monkeypatch):
    monkeypatch.setattr(srv.S, "data_dir", tmp_path / "data")
    monkeypatch.setattr(srv.S, "bridge", HERE / "fake_bridge.py")
    monkeypatch.setenv("FAKE_SLEEP", "5")
    srv.JOBS.clear()
    import subprocess
    with TestClient(srv.app) as c:
        job = upload(c).json()
        end = time.time() + 10
        while c.get(f"/jobs/{job['id']}").json()["status"] != "running" and time.time() < end:
            time.sleep(0.05)
        time.sleep(0.3)
    time.sleep(0.5)
    alive = subprocess.run(["pgrep", "-f", f"{job['id']}/input.pdf"], capture_output=True, text=True).stdout.strip()
    assert alive == "", f"tiến trình dịch còn sống sau khi tắt máy chủ: {alive}"
    srv.JOBS.clear()
