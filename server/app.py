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
import json
import os
import re
import secrets
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

VERSION = "0.2.0"
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
            "THONGNGU_ALLOWED_ORIGINS", "https://thong-ngu.vercel.app,http://127.0.0.1:8765,http://localhost:8765"
        ).split(",")
    )
    max_mb: int = _env_int("THONGNGU_MAX_MB", 30)
    max_pages: int = _env_int("THONGNGU_MAX_PAGES", 40)
    keep_hours: int = _env_int("THONGNGU_KEEP_HOURS", 2)
    max_queue: int = _env_int("THONGNGU_MAX_QUEUE", 20)
    max_per_ip: int = _env_int("THONGNGU_MAX_PER_IP", 2)
    # Đo 10/11/2026: 12 yêu cầu song song nhanh gấp đôi mặc định (4); cao hơn không nhanh thêm.
    qps: int = _env_int("THONGNGU_QPS", 12)
    engine: str = os.environ.get("THONGNGU_ENGINE", "bing")
    job_timeout_s: int = _env_int("THONGNGU_JOB_TIMEOUT", 20 * 60)
    python: str = os.environ.get("THONGNGU_PYTHON", sys.executable)
    bridge: Path = Path(os.environ.get("THONGNGU_BRIDGE", HERE / "pdf_bridge.py"))


S = Settings()
PAGES_RE = re.compile(r"^\s*\d+\s*(-\s*\d*\s*)?(,\s*\d+\s*(-\s*\d*\s*)?)*$")
ID_RE = re.compile(r"^[0-9a-f]{32}$")


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
    total_pages: int = 0
    created: float = field(default_factory=time.time)
    status: str = "queued"  # queued | running | done | error
    percent: float = 0.0
    stage: str = ""
    error: str | None = None
    files: dict[str, str] = field(default_factory=dict)  # mono/dual -> tên file trong thư mục lượt dịch
    started: float | None = None
    finished: float | None = None

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
            "percent": round(self.percent, 1),
            "stage": self.stage,
            "step": self.status if self.status in ("queued", "done", "error") else stage_step(self.stage),
            "error": self.error,
            "pages": len(self.pages),
            "page_numbers": self.pages,  # số trang thật trong file gốc, để trình đọc ghi "Trang 7"
            "files": sorted(self.files),
            "seconds": round((self.finished or time.time()) - (self.started or time.time()), 1) if self.started else None,
            "expires_at": self.created + S.keep_hours * 3600,
        }


JOBS: dict[str, Job] = {}
# Tạo lúc máy chủ khởi động (lifespan), không tạo lúc nạp module: asyncio.Queue gắn với
# event loop đầu tiên dùng nó, khởi động lại trong cùng tiến trình sẽ treo hàng đợi.
QUEUE: asyncio.Queue[str]


def queue_position(job: Job) -> int | None:
    if job.status != "queued":
        return None
    waiting = sorted((j for j in JOBS.values() if j.status == "queued"), key=lambda j: j.created)
    return 1 + next(i for i, j in enumerate(waiting) if j.id == job.id)


def ahead_of(job: Job) -> int:
    """Máy chỉ dịch một file một lúc, ai gửi trước dịch trước: đếm file đang dịch + file chờ gửi trước."""
    if job.status == "running":
        return 0
    if job.status != "queued":
        return 0
    return sum(1 for j in JOBS.values()
               if j.status == "running" or (j.status == "queued" and j.created < job.created))


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
        QUEUE.task_done()


def cleanup_once(now: float | None = None) -> int:
    """Xoá lượt dịch quá hạn (cả trên đĩa), kể cả thư mục mồ côi sau khi khởi động lại. Trả về số thư mục đã xoá."""
    cutoff = (now or time.time()) - S.keep_hours * 3600
    removed = 0
    for jid, job in list(JOBS.items()):
        if job.created < cutoff and job.status in ("done", "error"):
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

@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    global QUEUE
    QUEUE = asyncio.Queue()
    S.data_dir.mkdir(parents=True, exist_ok=True)
    tasks = [asyncio.create_task(worker()), asyncio.create_task(janitor())]
    _app.state.tasks = tasks
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()


app = FastAPI(title="Thông Ngữ - máy chủ dịch PDF", version=VERSION, docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in S.allowed_origins if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def client_ip(request: Request) -> str:
    # Sau Caddy: lấy IP thật từ X-Forwarded-For (Caddy tự thêm), không thì IP kết nối.
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "?")


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
    if lang not in ("vi", "en"):
        raise HTTPException(400, "Ngôn ngữ chỉ có vi hoặc en")
    ip = client_ip(request)
    active = [j for j in JOBS.values() if j.status in ("queued", "running")]
    if len(active) >= S.max_queue:
        raise HTTPException(503, "Máy chủ đang bận nhiều lượt dịch, bạn thử lại sau ít phút nhé.")
    if sum(1 for j in active if j.ip == ip) >= S.max_per_ip:
        raise HTTPException(429, f"Mỗi người dịch tối đa {S.max_per_ip} file cùng lúc. Đợi file trước xong nhé.")

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
    try:
        chosen = parse_pages(pages, total)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if len(chosen) > S.max_pages:
        raise HTTPException(413, f"Bạn chọn {len(chosen)} trang, tối đa {S.max_pages} trang mỗi lần. Hãy nhập ô Trang cần dịch, ví dụ 1-{S.max_pages}.")

    job = Job(id=secrets.token_hex(16), ip=ip, name=Path(file.filename or "paper.pdf").name[:120],
              lang=lang, dual=dual, pages=chosen, total_pages=total)
    job.dir.mkdir(parents=True)
    (job.dir / "input.pdf").write_bytes(data)
    # Bản gốc chỉ gồm các trang được chọn, để web đặt cạnh bản dịch theo đúng từng trang.
    src = pymupdf.open(stream=data, filetype="pdf")
    if len(chosen) < total:
        src.select([p - 1 for p in chosen])
    src.save(job.dir / "original.pdf", garbage=3, deflate=True)
    src.close()
    JOBS[job.id] = job
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
    if kind not in job.files:
        raise HTTPException(404, "Chưa có file này.")
    stem = Path(job.name).stem
    suffix = {"dual": "song-ngu", "original": "ban-goc"}.get(kind, job.lang)
    return FileResponse(
        job.dir / job.files[kind],
        media_type="application/pdf",
        filename=f"{stem}.{suffix}.pdf",
        content_disposition_type="attachment" if download else "inline",
    )
