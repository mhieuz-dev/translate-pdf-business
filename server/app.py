"""Máy chủ dịch PDF cho bản web Thông Ngữ.

Nhận file PDF, xếp hàng, dịch từng file một bằng PDFMathTranslate-next (giữ bố cục, công thức,
bảng, hình) rồi trả bản dịch để xem/tải trên web. Thiết kế để chạy chung VPS với dịch vụ khác:
  - mỗi lần chỉ dịch 1 file; mỗi lượt là một tiến trình con riêng, có hạn giờ, xong là trả RAM
  - giới hạn dung lượng, số trang, số lượt mỗi IP, độ dài hàng đợi
  - file tự xoá sau KEEP_HOURS giờ; mã lượt dịch ngẫu nhiên 128 bit nên không đoán được

Chạy thử:  .venv/bin/uvicorn app:app --port 8787
Cấu hình bằng biến môi trường (xem Settings bên dưới).
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import statistics
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import notify_mail
import stats
import pymupdf
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware

VERSION = "0.4.0"
HERE = Path(__file__).resolve().parent


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    data_dir: Path = Path(os.environ.get("THONGNGU_DATA_DIR", HERE / "data"))
    allowed_origins: list[str] = field(
        default_factory=lambda: os.environ.get(
            "THONGNGU_ALLOWED_ORIGINS", "https://translate.mhieuz.com,https://translate.document.mhieuz.com,https://thong-ngu.vercel.app,http://127.0.0.1:8765,http://localhost:8765"
        ).split(",")
    )
    allowed_hosts: list[str] = field(
        default_factory=lambda: os.environ.get(
            "THONGNGU_ALLOWED_HOSTS",
            "mhieuz.com,translate.document.mhieuz.com,translate.mhieuz.com,thong-ngu.vercel.app,127.0.0.1,localhost,testserver",
        ).split(",")
    )
    max_mb: int = _env_int("THONGNGU_MAX_MB", 30)
    max_pages: int = _env_int("THONGNGU_MAX_PAGES", 40)
    max_total_pages: int = _env_int("THONGNGU_MAX_TOTAL_PAGES", 1000)
    keep_hours: int = _env_int("THONGNGU_KEEP_HOURS", 2)
    max_queue: int = _env_int("THONGNGU_MAX_QUEUE", 20)
    max_per_ip: int = _env_int("THONGNGU_MAX_PER_IP", 2)
    # Mỗi IP chỉ được THỬ gửi file bấy nhiêu lần trong một cửa sổ thời gian; tính cả lần
    # gửi hỏng để kẻ xấu không spam file rác đốt CPU đọc PDF.
    job_rate: int = _env_int("THONGNGU_JOB_RATE", 10)
    job_rate_window: int = _env_int("THONGNGU_JOB_RATE_WINDOW", 600)
    # Đo 10/11/2026: 12 yêu cầu song song nhanh gấp đôi mặc định (4); cao hơn không nhanh thêm.
    qps: int = _env_int("THONGNGU_QPS", 12)
    engine: str = os.environ.get("THONGNGU_ENGINE", "bing")
    job_timeout_s: int = _env_int("THONGNGU_JOB_TIMEOUT", 20 * 60)
    python: str = os.environ.get("THONGNGU_PYTHON", sys.executable)
    bridge: Path = Path(os.environ.get("THONGNGU_BRIDGE", HERE / "pdf_bridge.py"))
    # Link bí mật của trang quản trị; rỗng hoặc ngắn hơn 32 ký tự thì trang quản trị tắt (404).
    admin_token: str = os.environ.get("THONGNGU_ADMIN_TOKEN", "").strip()


S = Settings()
PAGES_RE = re.compile(r"^\s*\d+\s*(-\s*\d*\s*)?(,\s*\d+\s*(-\s*\d*\s*)?)*$")
ID_RE = re.compile(r"^[0-9a-f]{32}$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
FILE_PATH_RE = re.compile(r"^/jobs/[0-9a-f]{32}/files/[a-z]+$")
ADMIN_DIR = HERE / "admin"
ADMIN_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; "
             "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
ADMIN_TYPES = {".css": "text/css", ".js": "text/javascript", ".ttf": "font/ttf", ".png": "image/png"}


def safe_filename(name: str) -> str:
    """Tên file người dùng gửi lên chỉ dùng để hiện/đặt tên tải về: bỏ đường dẫn,
    ký tự điều khiển (chống chèn header email) và giới hạn độ dài."""
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = CONTROL_RE.sub("", name).strip().strip(".")
    return (name[:120].strip() or "paper.pdf")


# ======================= Trang cần dịch =======================

def parse_pages(spec: str, total: int) -> list[int]:
    """"1-3, 8" -> [1, 2, 3, 8] (đánh số từ 1). "5-" nghĩa là từ trang 5 tới hết. Rỗng = tất cả."""
    spec = (spec or "").strip()
    if not spec:
        return list(range(1, total + 1))
    if not PAGES_RE.match(spec):
        raise ValueError("Trang cần dịch chỉ gồm số, dấu phẩy và gạch ngang, ví dụ 1-5, 8")
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            start = int(a)
            end = int(b) if b.strip() else total
        else:
            start = end = int(part)
        if start < 1 or end < start:
            raise ValueError(f"Khoảng trang không hợp lệ: {part}")
        pages.update(p for p in range(start, end + 1) if p <= total)
    if not pages:
        raise ValueError(f"File chỉ có {total} trang")
    return sorted(pages)


def pages_arg(pages: list[int]) -> str:
    """[1,2,3,8] -> "1-3,8" để truyền cho pdf2zh_next --pages."""
    out, i = [], 0
    while i < len(pages):
        j = i
        while j + 1 < len(pages) and pages[j + 1] == pages[j] + 1:
            j += 1
        out.append(str(pages[i]) if i == j else f"{pages[i]}-{pages[j]}")
        i = j + 1
    return ",".join(out)


STAGE_VI = {
    "Chuẩn bị mô hình nhận dạng bố cục": "Chuẩn bị",
    "Parse PDF and Create Intermediate Representation": "Đọc file PDF",
    "DetectScannedFile": "Kiểm tra trang scan",
    "Parse Page Layout": "Nhận dạng bố cục",
    "Parse Paragraphs": "Tách đoạn văn",
    "Parse Formulas and Styles": "Nhận dạng công thức",
    "Parse Table": "Nhận dạng bảng",
    "Automatic Term Extraction": "Trích thuật ngữ",
    "Add Debug Information": "Ghi thông tin",
    "Remove Char Descent": "Chuẩn hoá chữ",
    "Translate Paragraphs": "Dịch các đoạn văn",
    "Typesetting": "Dàn trang",
    "Add Fonts": "Nhúng font",
    "Generate drawing instructions": "Vẽ trang",
    "Subset font": "Tối ưu font",
    "Save PDF": "Lưu file",
}


# 3 bước lớn hiện trên màn hình chờ của web: chuẩn bị -> dịch văn bản -> hoàn thiện tài liệu.
FINISH_STAGES = {"Dàn trang", "Nhúng font", "Vẽ trang", "Tối ưu font", "Lưu file", "Ghi thông tin", "Xong"}


def stage_step(stage_vi: str) -> str:
    if stage_vi == "Dịch các đoạn văn":
        return "translate"
    if stage_vi in FINISH_STAGES:
        return "finish"
    return "prepare"


def friendly_error(msg: str) -> str:
    if "429" in msg or "Too Many Requests" in msg or "/sorry/" in msg:
        return "Dịch vụ dịch đang chặn tạm vì có quá nhiều yêu cầu. Bạn thử lại sau ít phút nhé."
    if any(k in msg for k in ("RetryError", "ConnectError", "ConnectionError", "Timeout")):
        return "Không kết nối được dịch vụ dịch. Bạn thử lại sau ít phút nhé."
    if "encrypt" in msg.lower() or "password" in msg.lower():
        return "File PDF đang đặt mật khẩu, hãy gỡ mật khẩu rồi tải lên lại."
    return "Dịch không thành công. Có thể file PDF bị hỏng hoặc là ảnh scan chất lượng thấp."


# ======================= Lượt dịch =======================

@dataclass
class Job:
    id: str
    ip: str
    name: str
    lang: str
    dual: bool
    pages: list[int]
    ua: str = ""
    total_pages: int = 0
    created: float = field(default_factory=time.time)
    status: str = "queued"  # queued | running | done | error
    percent: float = 0.0
    stage: str = ""
    error: str | None = None
    files: dict[str, str] = field(default_factory=dict)  # mono/dual -> tên file trong thư mục lượt dịch
    started: float | None = None
    finished: float | None = None
    key: str = ""  # mã băm file + trang + tuỳ chọn: cùng khoá thì dùng chung bản dịch
    twin: str | None = None  # đang chờ một lượt giống hệt (cùng khoá) dịch xong để dùng chung
    shared: bool = False  # nhận bản dịch của lượt giống hệt, không tự dịch

    @property
    def dir(self) -> Path:
        return S.data_dir / self.id

    def public(self, position: int | None) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "position": position,
            "ahead": ahead_of(self),  # số lượt phía trước, TÍNH CẢ file đang dịch
            "twin": self.twin is not None,
            "shared": self.shared,
            "wait_s": round(wait_start(self)) if self.status == "queued" else 0,  # còn bao lâu tới lượt
            "eta_s": round(eta_done(self)) if self.status in ("queued", "running") else 0,  # còn bao lâu xong
            "percent": round(self.percent, 1),
            "stage": self.stage,
            "step": self.status if self.status in ("queued", "done", "error") else stage_step(self.stage),
            "error": self.error,
            "pages": len(self.pages),
            "page_numbers": self.pages,  # số trang thật trong file gốc, để trình đọc ghi "Trang 7"
            "files": sorted(self.files),
            "seconds": round((self.finished or time.time()) - (self.started or time.time()), 1) if self.started else None,
            "expires_at": (self.finished or self.created) + S.keep_hours * 3600,
        }


JOBS: dict[str, Job] = {}
JOB_ATTEMPTS: dict[str, deque[float]] = {}  # IP -> các lần thử gửi file gần đây
# Tạo lúc máy chủ khởi động (lifespan), không tạo lúc nạp module: asyncio.Queue gắn với
# event loop đầu tiên dùng nó, khởi động lại trong cùng tiến trình sẽ treo hàng đợi.
QUEUE: asyncio.Queue[str]
MAIL_TASKS: set[asyncio.Task] = set()


def job_rate_limited(ip: str) -> bool:
    """Đếm mọi lần thử POST /jobs của một IP trong cửa sổ trượt (kể cả file hỏng)."""
    if S.job_rate <= 0:
        return False
    now = time.time()
    q = JOB_ATTEMPTS.setdefault(ip, deque())
    while q and q[0] < now - S.job_rate_window:
        q.popleft()
    if len(q) >= S.job_rate:
        return True
    q.append(now)
    return False


async def mail_after_done(job: Job) -> None:
    # Gửi thư trong luồng riêng để không chặn hàng đợi (file đính kèm có thể vài chục MB).
    try:
        await asyncio.to_thread(notify_mail.send_job_mail, job)
    except Exception as e:  # gửi thư hỏng không được làm chết hàng đợi
        print(f"[job {job.id}] lỗi gửi email báo: {e!r}", file=sys.stderr, flush=True)


def schedule_mail(job: Job) -> None:
    """Báo về Gmail khi máy vừa dịch xong thật (lượt dùng chung bản dịch thì không báo lại)."""
    if job.status != "done" or job.shared or not notify_mail.enabled():
        return
    t = asyncio.create_task(mail_after_done(job))
    MAIL_TASKS.add(t)
    t.add_done_callback(MAIL_TASKS.discard)


def queue_position(job: Job) -> int | None:
    if job.status != "queued":
        return None
    waiting = sorted((j for j in JOBS.values() if j.status == "queued"), key=lambda j: j.created)
    return 1 + next(i for i, j in enumerate(waiting) if j.id == job.id)


def _source(job: Job) -> Job | None:
    src = JOBS.get(job.twin) if job.twin else None
    return src if src and src.status in ("queued", "running") else None


def _ahead_jobs(job: Job) -> list[Job]:
    """Lượt phải dịch xong trước lượt này. Lượt "sinh đôi" (chờ dùng chung) không tốn thời gian máy."""
    return [j for j in JOBS.values() if j.status == "running"
            or (j.status == "queued" and j.twin is None and j.created < job.created)]


def ahead_of(job: Job) -> int:
    """Máy chỉ dịch một file một lúc, ai gửi trước dịch trước: đếm file đang dịch + file chờ gửi trước."""
    if job.status != "queued":
        return 0
    src = _source(job)
    if src:
        return ahead_of(src) + 1  # xong cùng lúc với lượt giống hệt đang dịch
    return len(_ahead_jobs(job))


# Ước tính thời gian dịch: số đo thật trên VPS 10/11/2026 (2 trang 60 s, 3 trang 73 s, 15 trang 175 s)
# ~ 45 s chuẩn bị + 9 s mỗi trang, rồi tự chỉnh theo tỉ lệ thật/ước tính của các lượt gần nhất.
EST_FIXED_S, EST_PAGE_S = 45.0, 9.0
RECENT: deque[tuple[int, float]] = deque(maxlen=20)  # (số trang, số giây) của các lượt dịch thật


def est_seconds(pages: int) -> float:
    base = EST_FIXED_S + EST_PAGE_S * pages
    if RECENT:
        ratio = statistics.median(sec / (EST_FIXED_S + EST_PAGE_S * p) for p, sec in RECENT)
        base *= min(3.0, max(0.5, ratio))
    return base


def _remaining(job: Job) -> float:
    est = est_seconds(len(job.pages))
    if job.status == "running":
        return max(5.0, est - (time.time() - (job.started or time.time())))
    return est


def wait_start(job: Job) -> float:
    """Còn bao lâu tới lượt (lượt sinh đôi: còn bao lâu lượt giống hệt dịch xong)."""
    if job.status != "queued":
        return 0.0
    src = _source(job)
    if src:
        return eta_done(src)
    return sum(_remaining(j) for j in _ahead_jobs(job))


def eta_done(job: Job) -> float:
    if job.status == "running":
        return _remaining(job)
    if job.status != "queued":
        return 0.0
    if _source(job):
        return wait_start(job)
    return wait_start(job) + _remaining(job)


def link_files(src: Job, dst: Job) -> None:
    """Dùng chung bản dịch: liên kết cứng (không tốn thêm chỗ đĩa), mỗi lượt vẫn tự hết hạn riêng."""
    for kind, name in src.files.items():
        target = dst.dir / name
        target.unlink(missing_ok=True)
        try:
            os.link(src.dir / name, target)
        except OSError:
            shutil.copy2(src.dir / name, target)
        dst.files[kind] = name
    now = time.time()
    dst.status, dst.percent, dst.stage = "done", 100.0, "Xong"
    dst.started = dst.finished = now
    dst.twin, dst.shared = None, True


def finish_twins(src: Job) -> list[Job]:
    """Lượt dịch xong: các lượt giống hệt đang chờ nhận luôn bản dịch; lỗi thì chúng tự dịch khi tới lượt."""
    linked = []
    for j in JOBS.values():
        if j.twin == src.id and j.status == "queued":
            if src.status == "done":
                link_files(src, j)
                linked.append(j)
            else:
                j.twin = None
    return linked


def log_job(job: Job) -> None:
    """Ghi 1 dòng vào sổ thống kê khi lượt kết thúc. Lỗi ghi sổ không được làm hỏng hàng đợi."""
    end = job.finished or time.time()
    start = job.started or end
    try:
        stats.record(S.data_dir / "stats.jsonl", t=end, ip_h=stats.ip_hash(job.ip, ip_key()),
                     pages=len(job.pages), seconds=end - start, wait=max(0.0, start - job.created),
                     status=job.status, shared=job.shared)
    except Exception as e:  # kể cả lỗi lạ: worker chết thì mọi lượt sau treo ở "đang chờ"
        print(f"[job {job.id}] lỗi ghi sổ thống kê: {e!r}", file=sys.stderr, flush=True)


async def run_job(job: Job) -> None:
    job.status, job.started, job.stage = "running", time.time(), "Chuẩn bị"
    args = [
        S.python, "-I", str(S.bridge), str(job.dir / "input.pdf"),
        f"--{S.engine}", "--lang-out", job.lang, "--output", str(job.dir / "out"),
        "--qps", str(S.qps), "--pages", pages_arg(job.pages),
        "--watermark-output-mode", "no_watermark",  # 2.9 mặc định chèn dòng chữ Trung vào bản dịch
    ]
    if len(job.pages) < job.total_pages:
        args.append("--only-include-translated-page")  # chỉ giữ đúng các trang người dùng chọn
    if not job.dual:
        args.append("--no-dual")
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
    )
    tail: list[str] = []

    async def drain_stderr() -> None:
        assert proc.stderr
        async for raw in proc.stderr:
            tail.append(raw.decode("utf-8", "replace").rstrip())
            del tail[:-30]

    async def read_events() -> None:
        assert proc.stdout
        async for raw in proc.stdout:
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            kind = ev.get("type")
            if kind == "progress":
                job.percent = max(job.percent, min(100.0, float(ev.get("percent") or 0)))
                st = ev.get("stage") or ""
                job.stage = STAGE_VI.get(st, st) or job.stage
            elif kind == "finish":
                for k in ("mono", "dual"):
                    p = ev.get(k)
                    if p and Path(p).is_file():
                        dest = job.dir / f"{k}.pdf"
                        shutil.move(p, dest)
                        dest.chmod(0o600)
                        job.files[k] = dest.name
            elif kind == "error":
                job.error = friendly_error(str(ev.get("message", "")))

    err_task = asyncio.create_task(drain_stderr())
    try:
        await asyncio.wait_for(read_events(), timeout=S.job_timeout_s)
        await asyncio.wait_for(proc.wait(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        job.error = f"Quá {S.job_timeout_s // 60} phút chưa xong nên đã dừng. Hãy thử chọn ít trang hơn."
    except asyncio.CancelledError:
        # Máy chủ đang tắt: dừng luôn tiến trình dịch, không để nó chạy mồ côi ăn RAM.
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    finally:
        err_task.cancel()
    shutil.rmtree(job.dir / "out", ignore_errors=True)
    job.finished = time.time()
    if job.files and not job.error:
        if (job.dir / "original.pdf").is_file():
            job.files["original"] = "original.pdf"  # bản gốc đúng các trang đã dịch, để đọc song song
        job.status, job.percent, job.stage = "done", 100.0, "Xong"
        RECENT.append((len(job.pages), job.finished - (job.started or job.finished)))
    else:
        job.status = "error"
        job.error = job.error or friendly_error("\n".join(tail))
        print(f"[job {job.id}] lỗi, stderr cuối:\n" + "\n".join(tail[-10:]), file=sys.stderr, flush=True)


async def worker() -> None:
    while True:
        job_id = await QUEUE.get()
        job = JOBS.get(job_id)
        if job and job.status == "queued":
            try:
                await run_job(job)
            except Exception as e:  # không để một file hỏng làm chết hàng đợi
                job.status, job.error, job.finished = "error", friendly_error(str(e)), time.time()
                print(f"[job {job.id}] ngoại lệ: {e!r}", file=sys.stderr, flush=True)
            for j in [job, *finish_twins(job)]:
                log_job(j)
            schedule_mail(job)
        QUEUE.task_done()


def cleanup_once(now: float | None = None) -> int:
    """Xoá lượt dịch quá hạn (cả trên đĩa), kể cả thư mục mồ côi sau khi khởi động lại. Trả về số thư mục đã xoá."""
    now = now or time.time()
    cutoff = now - S.keep_hours * 3600
    removed = 0
    attempt_cutoff = now - S.job_rate_window
    for ip, q in list(JOB_ATTEMPTS.items()):
        while q and q[0] < attempt_cutoff:
            q.popleft()
        if not q:
            JOB_ATTEMPTS.pop(ip, None)
    for jid, job in list(JOBS.items()):
        # tính từ lúc dịch xong: người chờ lâu trong hàng vẫn có đủ thời gian tải bản dịch
        if (job.finished or job.created) < cutoff and job.status in ("done", "error"):
            shutil.rmtree(job.dir, ignore_errors=True)
            JOBS.pop(jid, None)
            removed += 1
    if S.data_dir.exists():
        for d in S.data_dir.iterdir():
            if d.is_dir() and d.name not in JOBS and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
    return removed


async def janitor() -> None:
    while True:
        cleanup_once()
        await asyncio.sleep(300)


# ======================= HTTP =======================

async def ram_sampler() -> None:
    while True:
        try:
            avail, total = stats.parse_meminfo(Path("/proc/meminfo").read_text())
            RAM.sample(avail, total, time.time())
        except (OSError, KeyError, ValueError, IndexError):
            pass  # không phải Linux / không đọc được: trang quản trị chỉ thiếu số RAM
        await asyncio.sleep(30)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    global QUEUE, RAM, _IP_KEY
    QUEUE = asyncio.Queue()
    _IP_KEY = None  # nạp lại theo data_dir hiện tại
    S.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        os.chmod(S.data_dir, 0o700)  # thư mục cũ có thể còn quyền 755
    RAM = stats.RamTracker(S.data_dir / "ram_daily.json")
    tasks = [asyncio.create_task(worker()), asyncio.create_task(janitor()), asyncio.create_task(ram_sampler())]
    _app.state.tasks = tasks
    try:
        yield
    finally:
        for t in [*tasks, *MAIL_TASKS]:
            t.cancel()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Header bảo mật cho mọi phản hồi. File PDF sandbox để chặn JavaScript nhúng trong PDF."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        h = response.headers
        for name in ("server", "x-powered-by"):
            with contextlib.suppress(KeyError):
                del h[name]
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "no-referrer"
        h["X-Robots-Tag"] = "noindex, nofollow"
        h["X-Permitted-Cross-Domain-Policies"] = "none"
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        h["Cross-Origin-Resource-Policy"] = "cross-origin"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith("/admin/"):
            h["Content-Security-Policy"] = ADMIN_CSP
            h["Cache-Control"] = "no-store"
        elif FILE_PATH_RE.match(request.url.path):
            h["Content-Security-Policy"] = "default-src 'none'; sandbox"
            h["X-Download-Options"] = "noopen"
            h["Cache-Control"] = "private, no-store"
        else:
            h["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
            h["Cache-Control"] = "no-store"
        return response


app = FastAPI(title="Thông Ngữ - máy chủ dịch PDF", version=VERSION, docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in S.allowed_origins if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[h.strip() for h in S.allowed_hosts if h.strip()])
app.add_middleware(SecurityHeadersMiddleware)


def client_ip(request: Request) -> str:
    # uvicorn đã chạy với --proxy-headers + --forwarded-allow-ips 127.0.0.1 nên request.client
    # là IP thật do Caddy xác nhận. KHÔNG đọc thẳng X-Forwarded-For: client có thể tự gửi
    # header giả để vượt giới hạn mỗi IP.
    return request.client.host if request.client else "?"


@app.get("/health")
async def health() -> dict:
    waiting = sum(1 for j in JOBS.values() if j.status in ("queued", "running"))
    return {"ok": True, "version": VERSION, "waiting": waiting, "max_mb": S.max_mb,
            "max_pages": S.max_pages, "keep_hours": S.keep_hours}


@app.post("/jobs", status_code=202)
async def create_job(
    request: Request,
    file: UploadFile = File(...),
    lang: str = Form("vi"),
    dual: bool = Form(True),
    pages: str = Form(""),
) -> dict:
    ip = client_ip(request)
    if job_rate_limited(ip):
        raise HTTPException(429, f"Bạn đã gửi quá nhiều file. Thử lại sau {S.job_rate_window // 60} phút nhé.")
    if lang not in ("vi", "en"):
        raise HTTPException(400, "Ngôn ngữ chỉ có vi hoặc en")
    data = await file.read(S.max_mb * 1024 * 1024 + 1)
    if len(data) > S.max_mb * 1024 * 1024:
        raise HTTPException(413, f"File lớn hơn {S.max_mb} MB.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "File không phải PDF.")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
        if doc.needs_pass:
            raise HTTPException(400, "File PDF đang đặt mật khẩu, hãy gỡ mật khẩu rồi tải lên lại.")
        total = doc.page_count
        doc.close()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Không đọc được file PDF, có thể file bị hỏng.")
    if total > S.max_total_pages:
        raise HTTPException(413, f"File có {total} trang, tối đa {S.max_total_pages} trang.")
    try:
        chosen = parse_pages(pages, total)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if len(chosen) > S.max_pages:
        raise HTTPException(413, f"Bạn chọn {len(chosen)} trang, tối đa {S.max_pages} trang mỗi lần. Hãy nhập ô Trang cần dịch, ví dụ 1-{S.max_pages}.")

    key = f"{hashlib.sha256(data).hexdigest()}:{lang}:{int(dual)}:{pages_arg(chosen)}"
    same = sorted((j for j in JOBS.values() if j.key == key and j.status in ("done", "queued", "running")),
                  key=lambda j: j.created)
    done_same = next((j for j in reversed(same) if j.status == "done"), None)
    busy_same = next((j for j in same if j.status in ("queued", "running") and j.twin is None), None)
    active = [j for j in JOBS.values() if j.status in ("queued", "running")]
    if not done_same:
        # lượt dùng chung bản dịch có sẵn không tốn máy nên không bị chặn
        if not busy_same and len(active) >= S.max_queue:
            raise HTTPException(503, "Máy chủ đang bận nhiều lượt dịch, bạn thử lại sau ít phút nhé.")
        if sum(1 for j in active if j.ip == ip) >= S.max_per_ip:
            raise HTTPException(429, f"Mỗi người dịch tối đa {S.max_per_ip} file cùng lúc. Đợi file trước xong nhé.")

    job = Job(id=secrets.token_hex(16), ip=ip, name=safe_filename(file.filename or ""),
              lang=lang, dual=dual, pages=chosen, ua=request.headers.get("user-agent", "")[:200],
              total_pages=total, key=key)
    job.dir.mkdir(parents=True, mode=0o700)
    (job.dir / "input.pdf").write_bytes(data)
    (job.dir / "input.pdf").chmod(0o600)
    # Bản gốc chỉ gồm các trang được chọn, để web đặt cạnh bản dịch theo đúng từng trang.
    src = pymupdf.open(stream=data, filetype="pdf")
    if len(chosen) < total:
        src.select([p - 1 for p in chosen])
    # Bỏ JavaScript / file nhúng / metadata khỏi bản gốc trước khi cho trình duyệt đọc,
    # giữ nguyên liên kết và nội dung nhìn thấy.
    try:
        src.scrub(remove_links=False, redactions=False, hidden_text=False,
                  reset_fields=False, reset_responses=False, clean_pages=False)
    except (TypeError, ValueError):
        pass
    src.save(job.dir / "original.pdf", garbage=3, deflate=True)
    src.close()
    (job.dir / "original.pdf").chmod(0o600)
    JOBS[job.id] = job
    if done_same:
        link_files(done_same, job)  # đã có người dịch đúng file này: trả ngay
        log_job(job)
        return job.public(None)
    if busy_same:
        job.twin = busy_same.id  # đang dịch đúng file này: xong cùng lúc, không dịch lại
    await QUEUE.put(job.id)
    return job.public(queue_position(job))


def get_job(job_id: str) -> Job:
    job = JOBS.get(job_id) if ID_RE.match(job_id) else None
    if not job:
        raise HTTPException(404, "Không tìm thấy lượt dịch (có thể đã hết hạn và bị xoá).")
    return job


@app.get("/jobs/{job_id}")
async def job_status(job_id: str) -> dict:
    job = get_job(job_id)
    return job.public(queue_position(job))


@app.get("/jobs/{job_id}/files/{kind}")
async def job_file(job_id: str, kind: str, download: bool = False) -> FileResponse:
    job = get_job(job_id)
    if kind not in ("mono", "dual", "original") or kind not in job.files:
        raise HTTPException(404, "Chưa có file này.")
    stem = Path(safe_filename(job.name)).stem or "paper"
    suffix = {"dual": "song-ngu", "original": "ban-goc"}.get(kind, job.lang)
    return FileResponse(
        job.dir / job.files[kind],
        media_type="application/pdf",
        filename=f"{stem}.{suffix}.pdf",
        content_disposition_type="attachment" if download else "inline",
    )


# ---------- trang quản trị (link bí mật) ----------
RAM: stats.RamTracker | None = None
_IP_KEY: str | None = None


def ip_key() -> str:
    global _IP_KEY
    if _IP_KEY is None:
        _IP_KEY = stats.load_ip_key(S.data_dir / "ip_key")
    return _IP_KEY


class HideAdminAccess(logging.Filter):
    """Không ghi đường dẫn /admin/<token> vào log truy cập (journald), để dán log khi gỡ lỗi không lộ link."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args if isinstance(record.args, tuple) else ()
        return not any(isinstance(a, str) and a.startswith("/admin/") for a in args)


logging.getLogger("uvicorn.access").addFilter(HideAdminAccess())


def check_admin(token: str) -> None:
    t = S.admin_token
    if len(t) < 32 or not secrets.compare_digest(token.encode(), t.encode()):
        raise HTTPException(404, "Not Found")


def admin_assets() -> dict[str, Path]:
    """Danh sách trắng: chỉ đúng các file trong admin/ (và admin/fonts/) mới được phục vụ."""
    out = {}
    for p in [*ADMIN_DIR.glob("*"), *ADMIN_DIR.glob("fonts/*")]:
        if p.is_file() and p.suffix in ADMIN_TYPES:
            out[p.relative_to(ADMIN_DIR).as_posix()] = p
    return out


@app.get("/admin/{token}", include_in_schema=False)
async def admin_redirect(token: str) -> RedirectResponse:
    check_admin(token)
    # Location tương đối vì Caddy đã bỏ tiền tố /thongngu-pdf trước khi chuyển vào đây.
    return RedirectResponse(f"{token}/", status_code=307)


@app.get("/admin/{token}/", include_in_schema=False)
async def admin_page(token: str) -> FileResponse:
    check_admin(token)
    return FileResponse(ADMIN_DIR / "index.html", media_type="text/html")


@app.get("/admin/{token}/stats", include_in_schema=False)
async def admin_stats(token: str) -> dict:
    check_admin(token)
    ram = RAM or stats.RamTracker(S.data_dir / "ram_daily.json")
    out = await asyncio.to_thread(stats.summary, S.data_dir / "stats.jsonl", ram, time.time())
    out["queue"] = {"running": sum(1 for j in JOBS.values() if j.status == "running"),
                    "queued": sum(1 for j in JOBS.values() if j.status == "queued")}
    return out


@app.get("/admin/{token}/assets/{name:path}", include_in_schema=False)
async def admin_asset(token: str, name: str) -> FileResponse:
    check_admin(token)
    path = admin_assets().get(name)
    if not path:
        raise HTTPException(404, "Not Found")
    return FileResponse(path, media_type=ADMIN_TYPES[path.suffix])
