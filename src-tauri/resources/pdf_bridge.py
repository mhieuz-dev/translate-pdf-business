"""Cầu nối Thông Ngữ <-> PDFMathTranslate-next.

Nhận đúng tham số dòng lệnh của pdf2zh_next, nhưng in tiến độ ra stdout dưới dạng
một dòng JSON cho mỗi sự kiện để ứng dụng hiển thị thanh tiến độ thật:
  {"type": "progress", "percent": 42.0, "stage": "Translate Paragraphs"}
  {"type": "finish", "mono": "...pdf", "dual": "...pdf", "seconds": 19.2}
  {"type": "error", "message": "..."}
Chạy bằng python trong cùng môi trường đã cài pdf2zh-next.
"""
import asyncio
import json
import logging
import sys


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


async def main():
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    from pdf2zh_next.config import ConfigManager
    from pdf2zh_next.high_level import do_translate_async_stream
    import babeldoc.assets.assets

    settings = ConfigManager().initialize_config()
    files = list(settings.basic.input_files)
    if not files:
        emit({"type": "error", "message": "Thiếu file PDF đầu vào"})
        return 2
    emit({"type": "progress", "percent": 0, "stage": "Chuẩn bị mô hình nhận dạng bố cục"})
    babeldoc.assets.assets.warmup()
    settings.basic.input_files = set()
    failed = 0
    for file in files:
        try:
            async for ev in do_translate_async_stream(settings, file):
                kind = ev.get("type")
                if kind in ("progress_start", "progress_update", "progress_end"):
                    emit({
                        "type": "progress",
                        "percent": float(ev.get("overall_progress", 0) or 0),
                        "stage": ev.get("stage", ""),
                    })
                elif kind == "finish":
                    r = ev["translate_result"]
                    emit({
                        "type": "finish",
                        "mono": str(r.mono_pdf_path) if r.mono_pdf_path else None,
                        "dual": str(r.dual_pdf_path) if r.dual_pdf_path else None,
                        "seconds": getattr(r, "total_seconds", None),
                    })
                    break
                elif kind == "error":
                    failed += 1
                    emit({"type": "error", "message": str(ev.get("error", "Lỗi không rõ"))})
                    break
        except Exception as e:  # lỗi mạng, file hỏng...
            failed += 1
            emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
