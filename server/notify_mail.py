"""Gửi email báo có người vừa dịch PDF trên Thông Ngữ.

Dùng đúng cách đã dùng ở project gift-tracker (user_tiktok/ops/watchdog.py): Gmail SMTP
cổng 587 + App Password, thư viện chuẩn (smtplib, email.message), không thêm phụ thuộc.
Thư gửi kèm bản gốc và bản dịch nếu tổng dung lượng nằm trong hạn cho phép; quá hạn thì
vẫn gửi thư kèm đường dẫn tải (đường dẫn sống tới khi lượt dịch hết hạn).

Biến môi trường:
  GMAIL_ADDRESS        tài khoản gửi (phamliem827@gmail.com)
  GMAIL_APP_PASSWORD   16 ký tự App Password của tài khoản gửi
  THONGNGU_MAIL_TO     người nhận (mặc định iamminhhieuz206@gmail.com)
  THONGNGU_PUBLIC_URL  địa chỉ máy chủ để ghép link tải
  THONGNGU_MAIL_MAX_MB tổng dung lượng file đính kèm tối đa (mặc định 18 MB)
"""
from __future__ import annotations

import os
import re
import smtplib
import sys
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_TO = "iamminhhieuz206@gmail.com"
VNTZ = ZoneInfo("Asia/Ho_Chi_Minh")

# Thứ tự ưu tiên khi phải chọn file đính kèm: bản dịch trước, bản gốc sau.
KIND_FILE = {"mono": "ban-dich", "dual": "song-ngu", "original": "ban-goc"}
KIND_LABEL = {"mono": "bản dịch", "dual": "bản song ngữ", "original": "bản gốc"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class MailSettings:
    sender: str
    password: str
    to: str
    public_url: str
    max_mb: int


def settings() -> MailSettings:
    return MailSettings(
        sender=os.environ.get("GMAIL_ADDRESS", "").strip(),
        password=os.environ.get("GMAIL_APP_PASSWORD", "").strip(),
        to=(os.environ.get("THONGNGU_MAIL_TO") or os.environ.get("ALERT_EMAIL_TO")
            or DEFAULT_TO).strip(),
        public_url=os.environ.get("THONGNGU_PUBLIC_URL", "https://mhieuz.com/thongngu-pdf").rstrip("/"),
        max_mb=_env_int("THONGNGU_MAIL_MAX_MB", 18),
    )


def enabled() -> bool:
    s = settings()
    return bool(s.sender and s.password and s.to)


def translation_kind(files: dict[str, str]) -> str | None:
    """Bản dịch để đính kèm: bản dịch thuần (mono) trước, không có thì bản song ngữ."""
    for kind in ("mono", "dual"):
        if kind in files:
            return kind
    return None


def _pages_text(job) -> str:
    pages = getattr(job, "pages", []) or []
    total = getattr(job, "total_pages", 0) or 0
    if not pages:
        return f"0/{total}"
    if len(pages) == total:
        return f"tất cả {total} trang"
    shown = ", ".join(str(p) for p in pages[:12]) + (", ..." if len(pages) > 12 else "")
    return f"{len(pages)}/{total} trang ({shown})"


def _duration_text(job) -> str:
    started = getattr(job, "started", None)
    finished = getattr(job, "finished", None)
    if not started or not finished:
        return "không rõ"
    seconds = max(0, int(round(finished - started)))
    if seconds < 60:
        return f"{seconds} giây"
    return f"{seconds // 60} phút {seconds % 60} giây"


def _time_text(ts: float | None) -> str:
    if not ts:
        return "không rõ"
    return datetime.fromtimestamp(ts, VNTZ).strftime("%H:%M ngày %d/%m/%Y (giờ Việt Nam)")


def _safe_stem(name: str) -> str:
    stem = Path(name or "paper").stem
    stem = re.sub(r"[^\w.\- ]+", "_", stem, flags=re.UNICODE).strip(" ._")
    return (stem or "paper")[:80]


def _links(job, cfg: MailSettings) -> list[str]:
    lines = []
    for kind in ("original", translation_kind(job.files)):
        if kind and kind in job.files:
            url = f"{cfg.public_url}/jobs/{job.id}/files/{kind}?download=true"
            lines.append(f"- {KIND_LABEL[kind].capitalize()}: {url}")
    return lines


def build_message(job):
    """Dựng thư + chọn file đính kèm vừa hạn Gmail. Trả về (EmailMessage, số file đính kèm)."""
    cfg = settings()
    stem = _safe_stem(job.name)
    kind_trans = translation_kind(job.files)
    candidates = []
    if kind_trans:
        candidates.append((kind_trans, Path(job.dir) / job.files[kind_trans]))
    if "original" in job.files:
        candidates.append(("original", Path(job.dir) / job.files["original"]))

    limit = cfg.max_mb * 1024 * 1024
    attached: list[tuple[str, Path]] = []
    used = 0
    for kind, path in candidates:
        if not path.is_file():
            continue
        size = path.stat().st_size
        if used + size <= limit:
            attached.append((kind, path))
            used += size

    missing = [KIND_LABEL[k] for k, _ in candidates if k not in {a[0] for a in attached}]
    subject = f"[Thông Ngữ] Có người dịch: {stem} ({len(getattr(job, 'pages', []))} trang)"
    body = [
        "Có người vừa dịch một file PDF trên Thông Ngữ.",
        "",
        f"File: {job.name}",
        f"Trang đã dịch: {_pages_text(job)}",
        f"Ngôn ngữ đích: {'tiếng Việt' if job.lang == 'vi' else 'tiếng Anh'}",
        f"Chế độ: {'song ngữ' if job.dual else 'chỉ bản dịch'}",
        f"Gửi lúc: {_time_text(getattr(job, 'finished', None) or getattr(job, 'created', None))}",
        f"Thời gian dịch: {_duration_text(job)}",
        f"Người gửi: {getattr(job, 'ip', '?')}",
    ]
    ua = (getattr(job, "ua", "") or "").strip()
    if ua:
        body.append(f"Trình duyệt: {ua[:200]}")
    body += [
        f"Mã lượt dịch: {job.id}",
        "",
        *(_links(job, cfg) or ["- (không có đường dẫn tải)"]),
    ]
    if attached:
        body += ["", f"Đính kèm: {', '.join(KIND_LABEL[k] for k, _ in attached)}."]
    if missing:
        body += ["", f"Không đính kèm được {', '.join(missing)} vì tổng dung lượng quá lớn; "
                     "bấm đường dẫn ở trên để tải."]
    body += ["", "File trên máy chủ tự xoá sau khi lượt dịch hết hạn, nên hãy tải sớm."]
    body += ["", "-- Thông Ngữ"]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.sender
    msg["To"] = cfg.to
    msg.set_content("\n".join(body))
    for kind, path in attached:
        msg.add_attachment(
            path.read_bytes(), maintype="application", subtype="pdf",
            filename=f"{stem}.{KIND_FILE.get(kind, kind)}.pdf",
        )
    return msg, len(attached)


def send_job_mail(job) -> bool:
    """Gửi thư báo cho chủ máy chủ. Lỗi gửi chỉ ghi log, không làm hỏng lượt dịch."""
    if not enabled():
        return False
    cfg = settings()
    try:
        msg, n = build_message(job)
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as smtp:
            smtp.starttls()
            smtp.login(cfg.sender, cfg.password)
            smtp.send_message(msg)
        print(f"[job {job.id}] đã gửi email báo ({n} file đính kèm) tới {cfg.to}",
              file=sys.stderr, flush=True)
        return True
    except (smtplib.SMTPException, OSError, ValueError) as e:
        print(f"[job {getattr(job, 'id', '?')}] gửi email thất bại: {e!r}",
              file=sys.stderr, flush=True)
        return False
