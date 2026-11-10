"""Cầu nối Thông Ngữ <-> PDFMathTranslate-next.

Nhận đúng tham số dòng lệnh của pdf2zh_next, nhưng in tiến độ ra stdout dưới dạng
một dòng JSON cho mỗi sự kiện để ứng dụng hiển thị thanh tiến độ thật:
  {"type": "progress", "percent": 42.0, "stage": "Translate Paragraphs"}
  {"type": "finish", "mono": "...pdf", "dual": "...pdf", "seconds": 19.2}
  {"type": "error", "message": "..."}
Chạy bằng python trong cùng môi trường đã cài pdf2zh-next.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

FONTS_DIR = Path(os.environ.get("THONGNGU_FONTS_DIR", Path(__file__).resolve().parent / "fonts"))


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------- font giống bản gốc
CM_RE = re.compile(r"(^|\+)(CMR|CMBX|CMTI|CMSL|LMRoman|LMRoman|CMU|SFRM|SFBX|SFTI|cmr)", re.I)
TIMES_RE = re.compile(r"Times|Nimbus.?Rom|Termes|STIX|Tinos|Liberation.?Serif|PTMR|UTMR", re.I)
SANS_RE = re.compile(r"Arial|Helvetica|Calibri|Verdana|Segoe|Roboto|Open.?Sans|DejaVu.?Sans|Noto.?Sans|Inter\b|Lato|Source.?Sans|Fira.?Sans|Nimbus.?San", re.I)


def detect_body_font(pdf_path: str, max_pages: int = 5) -> tuple[str | None, str]:
    """Font chiếm nhiều chữ nhất ở vài trang đầu -> "LMRoman" (Computer Modern), "Termes" (Times) hoặc None (không chân)."""
    import pymupdf
    counts: Counter[str] = Counter()
    with pymupdf.open(pdf_path) as doc:
        for page in list(doc)[:max_pages]:
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        n = len(span["text"].strip())
                        if n:
                            counts[span["font"]] += n
    if not counts:
        return "Termes", ""
    body = counts.most_common(1)[0][0]
    if CM_RE.search(body):
        return "LMRoman", body
    if TIMES_RE.search(body):
        return "Termes", body
    if SANS_RE.search(body):
        return None, body
    return "Termes", body  # chữ có chân khác: Times là dáng gần nhất trong các paper khoa học


CJK_FONT_RE = re.compile(r"^(SourceHan|LXGW|KleeOne|MaruBuri)")


def trim_cjk_fonts(fam: dict) -> None:
    """BabelDOC gắn mọi font Trung/Nhật/Hàn (mỗi file ~25 MB) vào danh sách dự phòng của mọi ngôn ngữ và nạp
    hết vào RAM. Dịch sang tiếng Việt không cần chúng; GoNotoKurrent vẫn còn để dự phòng ký hiệu lạ."""
    for key in ("normal", "script", "fallback"):
        kept = [x for x in fam[key] if not CJK_FONT_RE.match(x)]
        if kept:
            fam[key][:] = kept


def install_paper_fonts(pdf_path: str) -> str:
    """Đăng ký font TeX Gyre Termes / Latin Modern (đủ dấu tiếng Việt) lên đầu danh sách font của BabelDOC.
    Tên file có "Serif" vì BabelDOC chỉ coi là font có chân khi tên chứa chữ đó."""
    style, body = detect_body_font(pdf_path)
    if style is None:
        from babeldoc.assets import embedding_assets_metadata as meta
        trim_cjk_fonts(meta.EN_FONT_FAMILY)
        return f"giữ font mặc định (bản gốc dùng chữ không chân: {body})"
    import pymupdf
    from babeldoc.assets import embedding_assets_metadata as meta
    from babeldoc.const import get_cache_file_path

    added = {}
    for shape, bold, italic in (("Regular", 0, 0), ("Bold", 1, 0), ("Italic", 0, 1), ("BoldItalic", 1, 1)):
        name = f"ThongNguSerif-{style}-{shape}.ttf"
        src = FONTS_DIR / name
        data = src.read_bytes()
        dst = get_cache_file_path(name, "fonts")
        if not dst.exists() or dst.read_bytes() != data:
            shutil.copyfile(src, dst)
        f = pymupdf.Font(fontfile=str(src))
        meta.EMBEDDING_FONT_METADATA[name] = {
            "ascent": round(f.ascender * 1000), "descent": round(f.descender * 1000),
            "bold": bold, "italic": italic, "monospace": 0, "serif": 1, "encoding_length": 2,
            "file_name": name, "font_name": f.name or name[:-4],
            "sha3_256": hashlib.sha3_256(data).hexdigest(), "size": len(data),
        }
        added[shape] = name
    fam = meta.EN_FONT_FAMILY  # tiếng Việt dùng bộ font EN của BabelDOC
    for key, shapes in (("normal", ("Regular", "Bold")), ("script", ("Italic", "BoldItalic"))):
        fam[key][:] = [added[s] for s in shapes] + [x for x in fam[key] if not x.startswith("ThongNguSerif-")]
    trim_cjk_fonts(fam)
    return f"dùng {style} cho bản dịch (font chính bản gốc: {body})"


sys.path.insert(0, str(Path(__file__).resolve().parent))  # chạy với python -I nên phải tự thêm


def tighten_line_spacing() -> str:
    """Khoảng cách dòng đúng như bản gốc + căn đều hai lề (xem thongngu_layout.py)."""
    if os.environ.get("THONGNGU_LAYOUT", "1") == "0":  # tắt để so với bố cục gốc của BabelDOC
        return "bố cục mặc định của BabelDOC"
    import thongngu_layout

    default = float(os.environ.get("THONGNGU_LINE_SKIP", "1.12") or 1.12)
    justify = os.environ.get("THONGNGU_JUSTIFY", "1") != "0"
    hs_min = float(os.environ.get("THONGNGU_MIN_WIDTH", "0.78") or 0.78)  # 0.78: đo 10-11, cỡ chữ 21/21 đoạn đúng, chữ vẫn dễ đọc
    return thongngu_layout.install(line_skip_default=default, justify=justify, hs_min=hs_min)


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
    try:
        note = install_paper_fonts(str(files[0]))
    except Exception as e:  # thiếu file font thì vẫn dịch được bằng font mặc định
        note = f"không cài được font giống bản gốc: {e}"
    try:
        note += "; " + tighten_line_spacing()
    except Exception as e:
        note += f"; không chỉnh được khoảng cách dòng: {e}"
    print(f"[thongngu] {note}", file=sys.stderr, flush=True)
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
