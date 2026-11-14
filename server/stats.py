"""Sổ ghi lượt dịch và theo dõi RAM cho trang quản trị.

Chỉ thư viện chuẩn. Không lưu IP thật (chỉ mã băm HMAC) và không lưu tên file.
Ngày tính theo giờ Việt Nam (UTC+7).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

VN = timezone(timedelta(hours=7))


def ip_hash(ip: str, key: str) -> str:
    return hmac.new(key.encode(), ip.encode(), hashlib.sha256).hexdigest()[:16]


def day_vn(ts: float) -> str:
    return datetime.fromtimestamp(ts, VN).strftime("%Y-%m-%d")


def record(path: Path, *, t: float, ip_h: str, pages: int, seconds: float, wait: float,
           status: str, shared: bool) -> None:
    line = json.dumps({"t": round(t, 1), "ip": ip_h, "pages": pages, "sec": round(seconds, 1),
                       "wait": round(wait, 1), "status": status, "shared": shared})
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def parse_meminfo(text: str) -> tuple[int, int]:
    vals = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        if name in ("MemTotal", "MemAvailable"):
            vals[name] = int(rest.split()[0]) // 1024
    return vals["MemAvailable"], vals["MemTotal"]


class RamTracker:
    """RAM trống hiện tại + mức thấp nhất của từng ngày (lưu file để khởi động lại không mất)."""

    def __init__(self, path: Path):
        self.path = path
        self.current: dict | None = None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.daily: dict[str, int] = {str(k): int(v) for k, v in data.items()}
        except (OSError, ValueError, AttributeError, TypeError):
            self.daily = {}

    def sample(self, avail_mb: int, total_mb: int, now: float) -> None:
        self.current = {"avail_mb": avail_mb, "total_mb": total_mb, "t": round(now)}
        day = day_vn(now)
        if day in self.daily and self.daily[day] <= avail_mb:
            return
        self.daily[day] = avail_mb
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.daily), encoding="utf-8")
        os.replace(tmp, self.path)


def _read(path: Path) -> list[dict]:
    rows = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    rows.append({"t": float(r["t"]), "ip": str(r["ip"]), "pages": int(r["pages"]),
                                 "sec": float(r["sec"]), "wait": float(r["wait"]),
                                 "status": str(r["status"]), "shared": bool(r["shared"])})
                except (ValueError, KeyError, TypeError):
                    continue  # dòng hỏng (máy tắt giữa lúc ghi) thì bỏ qua
    except OSError:
        pass
    return rows


def summary(path: Path, ram: RamTracker, now: float, days: int = 14) -> dict:
    by_day: dict[str, list[dict]] = {}
    for r in _read(path):
        by_day.setdefault(day_vn(r["t"]), []).append(r)
    today = datetime.fromtimestamp(now, VN).date()
    out = []
    for i in range(days):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        rows = by_day.get(d, [])
        done = [r for r in rows if r["status"] == "done"]
        real = [r for r in done if not r["shared"]]
        out.append({
            "date": d,
            "runs": len(done),
            "people": len({r["ip"] for r in rows}),
            "pages": sum(r["pages"] for r in real),
            "errors": sum(1 for r in rows if r["status"] != "done"),
            "avg_s": round(sum(r["sec"] for r in real) / len(real), 1) if real else None,
            "ram_min_mb": ram.daily.get(d),
            "max_wait_s": round(max((r["wait"] for r in rows), default=0)),
        })
    t = out[0]
    return {
        "today": {k: t[k] for k in ("runs", "people", "pages", "errors", "max_wait_s")},
        "days": [{k: v for k, v in d.items() if k != "max_wait_s"} for d in out],
        "ram": ram.current,
    }
