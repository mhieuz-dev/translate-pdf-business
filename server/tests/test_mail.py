"""Test thư báo Gmail khi dịch xong. Không gửi thật: SMTP được thay bằng lớp giả."""
import sys
import time
from pathlib import Path

import pymupdf
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import app as srv  # noqa: E402
import notify_mail  # noqa: E402


def make_pdf(pages=2) -> bytes:
    d = pymupdf.open()
    for i in range(pages):
        d.new_page().insert_textbox(pymupdf.Rect(72, 72, 520, 700), f"Trang {i + 1}", fontsize=12)
    return d.tobytes()


@pytest.fixture
def mail_env(monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "sender@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    monkeypatch.setenv("THONGNGU_MAIL_TO", "owner@example.com")
    monkeypatch.setenv("THONGNGU_PUBLIC_URL", "https://mhieuz.com/thongngu-pdf")
    monkeypatch.delenv("THONGNGU_MAIL_MAX_MB", raising=False)


def make_job(tmp_path, monkeypatch, name="bai bao.pdf", pages=(1, 2), total=4, dual=True):
    monkeypatch.setattr(srv.S, "data_dir", tmp_path / "data")
    job = srv.Job(
        id="a" * 32, ip="203.0.113.7", name=name, lang="vi", dual=dual,
        pages=list(pages), total_pages=total, ua="Mozilla/5.0 (X11; Linux x86_64)",
    )
    job.dir.mkdir(parents=True)
    (job.dir / "original.pdf").write_bytes(make_pdf(2))
    (job.dir / "mono.pdf").write_bytes(make_pdf(2))
    job.files = {"original": "original.pdf", "mono": "mono.pdf"}
    job.status, job.shared = "done", False
    job.started, job.finished = time.time() - 103, time.time()
    return job


def test_disabled_without_credentials(monkeypatch):
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    assert notify_mail.enabled() is False
    job = srv.Job(id="b" * 32, ip="?", name="x.pdf", lang="vi", dual=True, pages=[1])
    assert notify_mail.send_job_mail(job) is False


def test_message_has_both_attachments_and_links(tmp_path, monkeypatch, mail_env):
    job = make_job(tmp_path, monkeypatch)
    msg, count = notify_mail.build_message(job)
    assert count == 2
    assert msg["To"] == "owner@example.com"
    assert msg["From"] == "sender@example.com"
    assert "Có người dịch" in msg["Subject"] and "bai bao" in msg["Subject"]
    body = msg.get_body(preferencelist=("plain",)).get_content()
    assert "Trang đã dịch: 2/4 trang (1, 2)" in body
    assert "1 phút 43 giây" in body
    assert "203.0.113.7" in body
    assert f"/jobs/{job.id}/files/original?download=true" in body
    assert f"/jobs/{job.id}/files/mono?download=true" in body
    names = [part.get_filename() for part in msg.iter_attachments()]
    assert names == ["bai bao.ban-dich.pdf", "bai bao.ban-goc.pdf"]


def test_oversized_attachments_become_links(tmp_path, monkeypatch, mail_env):
    monkeypatch.setenv("THONGNGU_MAIL_MAX_MB", "0")
    job = make_job(tmp_path, monkeypatch)
    msg, count = notify_mail.build_message(job)
    assert count == 0
    body = msg.get_body(preferencelist=("plain",)).get_content()
    assert "Không đính kèm được" in body
    assert "/files/mono?download=true" in body


def test_send_uses_gmail_smtp(tmp_path, monkeypatch, mail_env):
    job = make_job(tmp_path, monkeypatch)
    calls = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls["host"], calls["port"], calls["timeout"] = host, port, timeout

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            calls["tls"] = True

        def login(self, user, password):
            calls["login"] = (user, password)

        def send_message(self, msg):
            calls["sent"] = msg

    monkeypatch.setattr(notify_mail.smtplib, "SMTP", FakeSMTP)
    assert notify_mail.send_job_mail(job) is True
    assert calls["host"] == "smtp.gmail.com" and calls["port"] == 587 and calls["tls"]
    assert calls["login"] == ("sender@example.com", "abcd efgh ijkl mnop")
    assert calls["sent"]["To"] == "owner@example.com"


def test_failed_smtp_does_not_raise(tmp_path, monkeypatch, mail_env):
    job = make_job(tmp_path, monkeypatch)

    class BrokenSMTP:
        def __init__(self, *a, **k):
            raise OSError("mạng hỏng")

    monkeypatch.setattr(notify_mail.smtplib, "SMTP", BrokenSMTP)
    assert notify_mail.send_job_mail(job) is False


def test_finished_job_triggers_email_once(tmp_path, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def send_message(self, msg):
            sent.append(msg)

    monkeypatch.setattr(notify_mail.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("GMAIL_ADDRESS", "sender@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    monkeypatch.setenv("THONGNGU_MAIL_TO", "owner@example.com")
    monkeypatch.setattr(srv.S, "data_dir", tmp_path / "data")
    monkeypatch.setattr(srv.S, "bridge", HERE / "fake_bridge.py")
    monkeypatch.setattr(srv.S, "max_per_ip", 2)
    monkeypatch.delenv("FAKE_MODE", raising=False)
    monkeypatch.delenv("FAKE_SLEEP", raising=False)
    srv.JOBS.clear()

    from fastapi.testclient import TestClient
    with TestClient(srv.app) as c:
        r = c.post("/jobs", files={"file": ("paper.pdf", make_pdf(2), "application/pdf")},
                   data={"pages": "1-2"})
        job_id = r.json()["id"]
        end = time.time() + 20
        while time.time() < end:
            st = c.get(f"/jobs/{job_id}").json()
            if st["status"] == "done" and sent:
                break
            time.sleep(0.1)
        assert st["status"] == "done"
    srv.JOBS.clear()
    assert len(sent) == 1
    assert "Có người dịch" in sent[0]["Subject"]
    assert len(list(sent[0].iter_attachments())) >= 2
