"""Cầu nối giả cho test: không dịch thật, chỉ ghi lại tham số và tạo 2 file PDF nhỏ.
FAKE_MODE=error -> báo lỗi 429; FAKE_MODE=crash -> thoát không tạo file; FAKE_SLEEP=giây -> chạy chậm."""
import json, os, sys, time
import pymupdf

args = sys.argv[1:]
out = args[args.index("--output") + 1]
inp = args[0]
with open(os.path.join(os.path.dirname(inp), "args.json"), "w") as f:
    json.dump(args, f)
if os.environ.get("FAKE_RUNS"):  # đếm số lần dịch thật sự chạy
    with open(os.environ["FAKE_RUNS"], "a") as f:
        f.write("1\n")
mode = os.environ.get("FAKE_MODE", "")
for pct in (10, 5, 60):  # có lần tụt để kiểm tra tiến độ chỉ tăng
    print(json.dumps({"type": "progress", "percent": pct, "stage": "Parse Page Layout"}), flush=True)
    time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
if mode == "error":
    print(json.dumps({"type": "error", "message": "HTTPError: 429 Client Error: Too Many Requests"}), flush=True)
    sys.exit(0)
if mode == "crash":
    print("Traceback: boom", file=sys.stderr)
    sys.exit(1)
os.makedirs(out, exist_ok=True)
files = {}
for kind in ("mono", "dual"):
    if kind == "dual" and "--no-dual" in args:
        continue
    d = pymupdf.open(); d.new_page().insert_text((72, 72), f"{kind} ban dich"); p = os.path.join(out, f"x.vi.{kind}.pdf"); d.save(p); files[kind] = p
print(json.dumps({"type": "progress", "percent": 100, "stage": "Save PDF"}), flush=True)
print(json.dumps({"type": "finish", "mono": files.get("mono"), "dual": files.get("dual"), "seconds": 0.1}), flush=True)
