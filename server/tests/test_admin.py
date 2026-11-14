"""Test trang quản trị: link bí mật, đếm lượt dịch, header, danh sách trắng asset."""
import pytest

from test_app import client, make_pdf, upload, wait_done  # noqa: F401  (fixture dùng lại, nạp sys.path)

import app as srv  # noqa: E402

TOK = "t" * 40


@pytest.fixture
def admin(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(srv.S, "admin_token", TOK)
    return client


def test_admin_disabled_without_token(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(srv.S, "admin_token", "")
    assert client.get("/admin//").status_code == 404
    assert client.get("/admin/abc/stats").status_code == 404


def test_admin_wrong_token(admin, monkeypatch):
    assert admin.get("/admin/sai/").status_code == 404
    assert admin.get(f"/admin/{'x' * 40}/stats").status_code == 404
    monkeypatch.setattr(srv.S, "admin_token", "ngan123456")
    assert admin.get("/admin/ngan123456/stats").status_code == 404


def test_admin_page_headers(admin):
    r = admin.get(f"/admin/{TOK}", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == f"{TOK}/"
    r = admin.get(f"/admin/{TOK}/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert "no-store" in r.headers["cache-control"]
    assert r.headers["referrer-policy"] == "no-referrer"


def test_admin_stats_empty(admin):
    s = admin.get(f"/admin/{TOK}/stats").json()
    assert s["today"]["runs"] == 0
    assert s["queue"] == {"running": 0, "queued": 0}


def test_admin_counts_jobs(admin):
    data = make_pdf(2)
    j1 = upload(admin, data).json()
    assert wait_done(admin, j1["id"])["status"] == "done"
    j2 = upload(admin, data).json()
    assert wait_done(admin, j2["id"])["status"] == "done"
    t = admin.get(f"/admin/{TOK}/stats").json()["today"]
    assert (t["runs"], t["people"], t["pages"], t["errors"]) == (2, 1, 2, 0)
    raw = (srv.S.data_dir / "stats.jsonl").read_text()
    assert "bai bao" not in raw and "testclient" not in raw


def test_admin_asset_whitelist(admin):
    assert admin.get(f"/admin/{TOK}/assets/admin.css").status_code == 200
    assert admin.get(f"/admin/{TOK}/assets/..%2Fapp.py").status_code == 404
    assert admin.get(f"/admin/{TOK}/assets/app.py").status_code == 404
