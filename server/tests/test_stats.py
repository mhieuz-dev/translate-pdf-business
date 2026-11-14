"""Test sổ ghi lượt dịch + theo dõi RAM cho trang quản trị."""
import json
import os
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import stats  # noqa: E402


def _rec(path, t, ip, pages=3, sec=30.0, wait=0.0, status="done", shared=False):
    stats.record(path, t=t, ip_h=ip, pages=pages, seconds=sec, wait=wait, status=status, shared=shared)


def test_ip_hash():
    a = stats.ip_hash("1.2.3.4", "k1")
    assert a == stats.ip_hash("1.2.3.4", "k1")
    assert len(a) == 16 and "1.2.3.4" not in a
    assert a != stats.ip_hash("1.2.3.4", "k2")


def test_day_vn_boundary():
    assert stats.day_vn(datetime(2026, 11, 13, 16, 59, tzinfo=timezone.utc).timestamp()) == "2026-11-13"
    assert stats.day_vn(datetime(2026, 11, 13, 17, 0, tzinfo=timezone.utc).timestamp()) == "2026-11-14"


def test_record_fields(tmp_path):
    p = tmp_path / "stats.jsonl"
    _rec(p, time.time(), "abc")
    lines = p.read_text().splitlines()
    assert len(lines) == 1
    assert set(json.loads(lines[0])) == {"t", "ip", "pages", "sec", "wait", "status", "shared"}
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_summary_counts(tmp_path):
    p = tmp_path / "stats.jsonl"
    now = time.time()
    _rec(p, now - 40, "A", pages=3, sec=30, wait=5)
    _rec(p, now - 30, "B", pages=3, sec=0, shared=True)
    _rec(p, now - 20, "C", pages=4, status="error")
    _rec(p, now - 10, "A", pages=2, sec=50, wait=70)
    ram = stats.RamTracker(tmp_path / "ram.json")
    s = stats.summary(p, ram, now)
    t = s["today"]
    assert (t["runs"], t["people"], t["pages"], t["errors"]) == (3, 3, 5, 1)
    assert t["max_wait_s"] == 70
    assert s["days"][0]["date"] == stats.day_vn(now)
    assert s["days"][0]["avg_s"] == 40
    assert len(s["days"]) == 14


def test_summary_skips_bad_lines(tmp_path):
    p = tmp_path / "stats.jsonl"
    now = time.time()
    _rec(p, now - 5, "A")
    with p.open("a") as f:
        f.write('\n{"t": 1, \n')
    s = stats.summary(p, stats.RamTracker(tmp_path / "ram.json"), now)
    assert s["today"]["runs"] == 1


def test_summary_no_file(tmp_path):
    s = stats.summary(tmp_path / "none.jsonl", stats.RamTracker(tmp_path / "ram.json"), time.time())
    assert s["today"] == {"runs": 0, "people": 0, "pages": 0, "errors": 0, "max_wait_s": 0}
    assert s["ram"] is None
    assert all(d["runs"] == 0 and d["avg_s"] is None and d["ram_min_mb"] is None for d in s["days"])


def test_ram_tracker_min_and_reload(tmp_path):
    p = tmp_path / "ram.json"
    now = time.time()
    r = stats.RamTracker(p)
    for v in (1500, 700, 1200):
        r.sample(v, 3819, now)
    day = stats.day_vn(now)
    assert r.daily[day] == 700
    assert r.current["avail_mb"] == 1200
    assert stats.RamTracker(p).daily[day] == 700
    s = stats.summary(tmp_path / "x.jsonl", r, now)
    assert s["days"][0]["ram_min_mb"] == 700
    p.write_text("{hỏng")
    assert stats.RamTracker(p).daily == {}


def test_parse_meminfo():
    text = "MemTotal:        3910656 kB\nMemFree:  1 kB\nMemAvailable:    1651712 kB\n"
    assert stats.parse_meminfo(text) == (1613, 3819)


def test_summary_survives_garbage(tmp_path):
    p = tmp_path / "stats.jsonl"
    now = time.time()
    _rec(p, now - 30, "A")
    with p.open("ab") as f:
        f.write(b"\xff\xfe garbage\n")
        f.write(b'{"t": 1e20, "ip": "x", "pages": 1, "sec": 1, "wait": 0, "status": "done", "shared": false}\n')
        f.write(b'{"t": NaN, "ip": "x", "pages": 1, "sec": 1, "wait": 0, "status": "done", "shared": false}\n')
        f.write(b"\x00" * 50)  # ext4 mất điện để lại byte NUL, lần ghi sau nối tiếp cùng dòng
    _rec(p, now - 10, "B")
    s = stats.summary(p, stats.RamTracker(tmp_path / "ram.json"), now)
    assert s["today"]["runs"] == 2 and s["today"]["people"] == 2


def test_ip_key_random_and_kept(tmp_path):
    k1 = stats.load_ip_key(tmp_path / "ip_key")
    assert len(k1) >= 32 and k1 != "thongngu"
    assert stats.load_ip_key(tmp_path / "ip_key") == k1
    assert stat.S_IMODE(os.stat(tmp_path / "ip_key").st_mode) == 0o600
    assert stats.load_ip_key(tmp_path / "other") != k1
