"""Làm bố cục bản dịch giống bản gốc.

Không sửa mã BabelDOC (AGPL), chỉ bọc vài hàm của nó lúc chạy (xem install()):
  - Đo từng đoạn gốc trước khi dịch: khoảng cách chân dòng, mép phải thật, căn đều, thụt treo, căn giữa.
  - Dòng dịch nằm đúng chân dòng gốc (line_skip quy đổi từ khoảng dòng gốc), đáy khung giữ như gốc.
  - Căn đều hai lề như gốc, kể cả dòng có công thức (nét vẽ đi theo công thức).
  - Thiếu chỗ thì làm hẹp chữ (Tz) tới hs_min trước khi thu nhỏ; mỗi đoạn giữ cỡ tối ưu của riêng nó.
  - Mục danh sách "–" tách riêng, thụt treo đúng; dòng căn giữa (tên bài, ghi chú) vẫn căn giữa.
  - Giữ nguyên tài liệu tham khảo và khối tác giả ở trang 1.
  - Sửa lỗi có sẵn của BabelDOC: gạch phân số lạc chỗ, khung chữ bị nhận hẹp tách mất chữ ("incr"),
    xuống dòng giữa chữ ("c" / "ủa").
Dùng cho babeldoc 0.6.x (thuần Python), xem server/requirements.txt.
"""
from __future__ import annotations

import statistics

ORIGINAL: dict[int, dict] = {}  # id(paragraph) -> {"pitch", "size", "lines", "justified"}
STATS = {"measured": 0, "pitch_applied": 0, "justified_lines": 0, "condensed": 0}
CONDENSED: set[int] = set()
SKIP: set[int] = set()  # id(paragraph) của các mục tài liệu tham khảo: giữ nguyên bản gốc, không dịch
OPTIMAL: dict[int, float] = {}  # id(paragraph) -> cỡ tối ưu riêng của đoạn (trước khi BabelDOC ép cả tài liệu về cùng cỡ)
DEBUG = bool(__import__("os").environ.get("THONGNGU_LAYOUT_DEBUG"))


# ---------------------------------------------------------------- đo đoạn gốc
def _chars(paragraph):
    for comp in paragraph.pdf_paragraph_composition or []:
        if comp is None:
            continue
        if comp.pdf_line is not None:
            yield from comp.pdf_line.pdf_character or []
        elif comp.pdf_character is not None:
            yield comp.pdf_character
        elif comp.pdf_same_style_characters is not None:
            yield from comp.pdf_same_style_characters.pdf_character or []


def measure_paragraph(paragraph) -> dict | None:
    chars = [c for c in _chars(paragraph) if c.box is not None and c.char_unicode and c.char_unicode.strip()]
    if len(chars) < 8:
        return None
    sizes = [c.pdf_style.font_size for c in chars if c.pdf_style and c.pdf_style.font_size]
    if not sizes:
        return None
    size = statistics.median(sizes)
    # Gom chữ thành dòng theo toạ độ y (PDF: y tăng lên trên), lấy trung vị đáy chữ làm chân dòng.
    lines: list[list] = []
    for c in sorted(chars, key=lambda c: -c.box.y):
        if lines and abs(lines[-1][0].box.y - c.box.y) <= 0.45 * size:
            lines[-1].append(c)
        else:
            lines.append([c])
    lines = [l for l in lines if len(l) >= 3]
    if len(lines) < 2:
        return {"pitch": None, "size": size, "lines": len(lines), "justified": False}
    base = [statistics.median(c.box.y for c in l) for l in lines]
    gaps = [a - b for a, b in zip(base, base[1:]) if 0.8 * size <= a - b <= 2.5 * size]
    pitch = statistics.median(gaps) if gaps else None
    left = min(c.box.x for c in chars)
    text_right = max(max(c.box.x2 for c in l) for l in lines)
    # Căn đều: so mép phải các dòng thân đoạn với TRUNG VỊ của chúng (không với khung đoạn: một dòng
    # thò ra lề như dấu gạch nối, dấu ngoặc làm khung rộng hơn 1-2 pt). Bỏ mảnh công thức (tử số,
    # chỉ số) vì chúng bị gom thành "dòng" ngắn.
    width = text_right - left
    body = [l for l in lines[:-1] if max(c.box.x2 for c in l) - min(c.box.x for c in l) >= 0.5 * width]
    edges = [max(c.box.x2 for c in l) for l in body]
    right = statistics.median(edges) if edges else text_right
    flush = sum(1 for e in edges if abs(e - right) <= 1.5)
    justified = bool(edges) and flush >= max(1, 0.6 * len(edges))
    if DEBUG:
        import sys
        print(f"[measure] {''.join(c.char_unicode for c in lines[0])[:30]!r} edges={[round(e,1) for e in edges]} "
              f"all={[round(max(c.box.x2 for c in l),1) for l in lines]} just={justified}", file=sys.stderr, flush=True)
    # Thụt treo (mục danh sách, mục trích dẫn): dòng 2 trở đi bắt đầu thụt vào so với dòng đầu.
    lefts = [min(c.box.x for c in l) for l in lines]
    hang = 0.0
    m = statistics.median(lefts[1:])
    if all(abs(x - m) <= 1.0 for x in lefts[1:]) and 2.0 < m - lefts[0] < 0.25 * width:
        hang = m - lefts[0]
    return {"pitch": pitch, "size": size, "lines": len(lines), "justified": justified,
            "left": left, "right": right if justified else text_right, "hang": hang,
            "spans": [(min(c.box.x for c in l), max(c.box.x2 for c in l)) for l in lines]}


def free_right(paragraph, page) -> float | None:
    """Mép phải xa nhất mà đoạn MỘT dòng được lấn sang khi bản dịch dài hơn: tới mép khung bố cục chứa nó
    (mép cột), dừng trước đoạn khác nằm bên phải cùng hàng."""
    b = paragraph.box
    if b is None:
        return None
    lay = next((l.box for l in page.page_layout or [] if l.id == paragraph.layout_id), None)
    if lay is None:
        return None
    lim = lay.x2
    for q in page.pdf_paragraph or []:
        qb = q.box
        if q is paragraph or qb is None:
            continue
        if min(qb.y2, b.y2) - max(qb.y, b.y) > 0.5 and qb.x >= b.x2 - 1:
            lim = min(lim, qb.x - 2)
    return max(lim, b.x2)


def centered_line(paragraph, page, spans=None):
    """Đoạn căn giữa trang hoặc giữa cột chữ (tên bài, tiêu đề "Abstract", lời ghi chú căn giữa)?
    spans: mép trái/phải từng dòng (None = đoạn một dòng, lấy theo khung). Mọi dòng phải có tâm trùng
    tâm vùng và có dòng hẹp hơn hẳn vùng. Trả về (tâm, mép trái, mép phải) của vùng, hoặc None."""
    b = paragraph.box
    if b is None:
        return None
    spans = spans or [(b.x, b.x2)]
    texts = [l.box for l in page.page_layout or [] if l.class_name in TEXT_LAYOUTS and l.box.x2 - l.box.x > 150]
    refs = []
    if texts:
        refs.append((min(t.x for t in texts), max(t.x2 for t in texts)))  # cả khổ chữ của trang
    refs += [(t.x, t.x2) for t in texts if t.x - 2 <= b.x and b.x2 <= t.x2 + 2]  # cột chứa nó
    limit = 0.7 if len(spans) == 1 else 0.9
    for lo, hi in refs:
        cx = (lo + hi) / 2
        if all(abs((x + x2) / 2 - cx) <= 2.0 for x, x2 in spans) and min(x2 - x for x, x2 in spans) < limit * (hi - lo):
            return (cx, lo, hi)
    return None


def measure_document(docs) -> None:
    for page in docs.page or []:
        for paragraph in page.pdf_paragraph or []:
            info = measure_paragraph(paragraph)
            if info:
                cen = centered_line(paragraph, page, info.get("spans"))
                if cen:
                    info["center"] = cen
                elif info["lines"] <= 1:
                    info["free_x2"] = free_right(paragraph, page)
                if paragraph.box is not None:
                    info["bottom"] = paragraph.box.y
                ORIGINAL[id(paragraph)] = info
                STATS["measured"] += 1


# ---------------------------------------------------------------- giữ nguyên tài liệu tham khảo
import re as _re

REF_HEAD = _re.compile(r"^(\d+(\.\d+)*\.?\s*)?(references|bibliography|reference list|literature cited|works cited)\s*$", _re.I)
REF_ENTRY = _re.compile(
    r"^\s*(\[\d{1,3}\]"                                      # [12] Tác giả ...
    r"|\d{1,3}\.\s+[A-Z]"                                     # 12. Tác giả ...
    r"|[A-Z][\w'’\-]+,\s+(?:[A-Z]\.|[A-Z][a-z]).*\b(19|20)\d\d)"  # Họ, Tên ... năm
)


ABSTRACT_HEAD = _re.compile(r"^\s*(abstract|summary)\b", _re.I)


def mark_front_matter(docs) -> None:
    """Trang 1: tên tác giả, nơi công tác, email (giữa tên bài và "Abstract") giữ nguyên như bản gốc.
    Dịch chúng chỉ làm hỏng (Google Research -> "Nghiên cứu Google", mất chữ đậm, lệch ô)."""
    page = next((p for p in docs.page or [] if p.page_number == 0), None)
    if page is None:
        return
    # chỉ đoạn nằm ngang: dải chữ dọc "arXiv:... [cs.CL]" bên lề có cỡ chữ lớn hơn cả tên bài
    paras = [p for p in page.pdf_paragraph or [] if p.box is not None and not p.vertical
             and p.box.x2 - p.box.x > p.box.y2 - p.box.y]
    if not paras:
        return

    def size(p):
        v = [c.pdf_style.font_size for c in _chars(p) if c.pdf_style and c.pdf_style.font_size]
        return statistics.median(v) if v else 0

    title = max(paras, key=size)
    heads = [p for p in paras if ABSTRACT_HEAD.match(p.unicode or "")]
    top = max((p.box.y2 for p in heads), default=None)  # toạ độ PDF: y tăng lên trên
    for p in paras:
        text = (p.unicode or "").strip()
        if p is title or len(text) >= 200 or p in heads:
            continue
        below_title = p.box.y2 <= title.box.y + 1
        above_abstract = top is not None and p.box.y >= top - 1
        if below_title and ("@" in text or above_abstract):
            SKIP.add(id(p))


def mark_references(docs) -> None:
    """Sau tiêu đề "References", các đoạn có dạng mục trích dẫn được giữ nguyên (tên tác giả, tên bài
    báo không nên dịch). Gặp 2 đoạn liên tiếp không giống mục trích dẫn (phụ lục, hình...) thì dừng."""
    in_refs, misses = False, 0
    for page in docs.page or []:
        for paragraph in page.pdf_paragraph or []:
            text = (paragraph.unicode or "").strip()
            if REF_HEAD.match(text):
                in_refs, misses = True, 0
                continue
            if not in_refs:
                continue
            if REF_ENTRY.match(text):
                SKIP.add(id(paragraph))
                misses = 0
            else:
                misses += 1
                if misses >= 2:
                    in_refs = False


# ---------------------------------------------------------------- khung văn bản bị nhận hẹp
TEXT_LAYOUTS = {"plain text", "text", "paragraph_hybrid", "list_item_hybrid"}


def widen_text_layouts(page, max_grow: float = 30.0) -> int:
    """Mô hình nhận dạng bố cục đôi khi vẽ khung đoạn văn hẹp hơn dòng chữ thật (ví dụ 126-498 trong
    khi dòng chạy 108-504). Chữ nằm ngoài khung ("incr" của "increased", số "3" của "0.3") bị tách
    thành đoạn riêng, dịch lẻ hoặc bỏ nguyên, nên bản dịch ra "incrgiảm". Nới khung theo chiều ngang cho
    phủ trọn những dòng mà nó đã chứa phần lớn, miễn là không lấn sang khung khác (cột bên cạnh, hình)."""
    from babeldoc.format.pdf.document_il.il_version_1 import Box

    layouts = page.page_layout or []
    lines = [l.box for l in layouts if l.class_name == "fallback_line"]
    others = [l for l in layouts if l.class_name != "fallback_line"]

    def hit(a, b):
        return min(a.x2, b.x2) - max(a.x, b.x) > 0.5 and min(a.y2, b.y2) - max(a.y, b.y) > 0.5

    grown = 0
    for lay in others:
        if lay.class_name not in TEXT_LAYOUTS:
            continue
        b = lay.box
        x, x2 = b.x, b.x2
        for f in lines:
            cy = (f.y + f.y2) / 2
            w = f.x2 - f.x
            if not (b.y <= cy <= b.y2) or w <= 0:
                continue
            if min(f.x2, b.x2) - max(f.x, b.x) < 0.6 * w:
                continue
            if b.x - f.x <= max_grow:
                x = min(x, f.x)
            if f.x2 - b.x2 <= max_grow:
                x2 = max(x2, f.x2)
        if x >= b.x and x2 <= b.x2:
            continue
        new = Box(x=x, y=b.y, x2=x2, y2=b.y2)
        if any(o is not lay and hit(new, o.box) and not hit(b, o.box) for o in others):
            continue
        lay.box = new
        grown += 1
    return grown


# ---------------------------------------------------------------- nét vẽ của công thức
def attach_formula_curves(document, tol: float = 2.0) -> int:
    """Lỗi có sẵn của BabelDOC: gạch phân số / nét căn của công thức nằm trong page.pdf_curve chứ không trong
    formula.pdf_curve, nên khi dịch xong công thức bị dời mà nét vẽ vẫn ở chỗ cũ (vạch ngang lạc chỗ).
    Gán các nét nằm gọn trong khung công thức vào chính công thức để BabelDOC dời chúng cùng chữ."""
    moved = 0
    for page in document.page or []:
        curves = list(page.pdf_curve or [])
        if not curves:
            continue
        formulas = []
        for paragraph in page.pdf_paragraph or []:
            for comp in paragraph.pdf_paragraph_composition or []:
                f = comp.pdf_formula if comp is not None else None
                if f is not None and f.box is not None:
                    formulas.append((f, paragraph.xobj_id))
        if not formulas:
            continue
        keep = []
        for c in curves:
            b = c.box
            owner = None
            if b is not None:
                for f, xobj in formulas:
                    fb = f.box
                    if (c.xobj_id == xobj and b.x >= fb.x - tol and b.x2 <= fb.x2 + tol
                            and b.y >= fb.y - tol and b.y2 <= fb.y2 + tol):
                        owner = f
                        break
            if owner is None:
                keep.append(c)
            else:
                owner.pdf_curve.append(c)
                moved += 1
        page.pdf_curve = keep
    return moved


# ---------------------------------------------------------------- căn đều hai lề
def _line_y(unit, prev_y):
    if unit.char is not None:
        return unit.char.box.y
    if unit.unicode is not None and unit.y is not None:
        return unit.y
    return prev_y  # công thức: thuộc dòng của chữ đứng trước


def _shift_formula(u, dx):
    """Dời nguyên khối công thức sang ngang dx (pt), giữ nguyên chiều đứng, kéo theo nét vẽ.
    relocate của BabelDOC đặt lại ma trận nét vẽ theo khung hiện tại (sai khi đã dời một lần),
    nên ma trận mới = ma trận cũ + đoạn dời."""
    chars = u.formular.pdf_character or []
    if not chars:
        return u
    c0 = chars[0].box
    x0, y0 = u.box.x, u.box.y
    probe = u.relocate(x0, y0, 1.0)
    p0 = probe.formular.pdf_character[0].box
    new = u.relocate(x0 + dx - (p0.x - c0.x), y0 - (p0.y - c0.y), 1.0)
    for olds, news in ((u.formular.pdf_curve, new.formular.pdf_curve), (u.formular.pdf_form, new.formular.pdf_form)):
        for o, n in zip(olds or [], news or []):
            m = [float(v) for v in o.relocation_transform] if o.relocation_transform and len(o.relocation_transform) == 6 else [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
            m[4] += dx
            n.relocation_transform = m
            if o.box is not None:
                n.box = type(o.box)(x=o.box.x + dx, y=o.box.y, x2=o.box.x2 + dx, y2=o.box.y2)
    return new


def _shift(u, dx, y):
    try:
        return _shift_formula(u, dx) if u.formular is not None else u.relocate(u.box.x + dx, y, 1.0)
    except Exception:
        return u


def _center_lines(units, cx):
    """Dời từng dòng sang ngang cho tâm dòng trùng cx."""
    lines, prev_y = [], None
    for u in units:
        y = _line_y(u, prev_y)
        if not lines or (y is not None and prev_y is not None and abs(y - prev_y) > 0.5):
            lines.append([])
        lines[-1].append((u, y))
        prev_y = y
    out = []
    for line in lines:
        vis = [u for u, _ in line if not u.is_space]
        if not vis:
            out.extend(u for u, _ in line)
            continue
        dx = cx - (min(u.box.x for u in vis) + max(u.box.x2 for u in vis)) / 2
        out.extend(_shift(u, dx, y) if abs(dx) > 0.05 else u for u, y in line)
    return out


def _move_first_line(units, dx):
    """Dời cả dòng đầu sang ngang dx (dùng cho thụt treo)."""
    out, first_y, prev_y = [], None, None
    for idx, u in enumerate(units):
        y = _line_y(u, prev_y)
        if first_y is None:
            first_y = y
        if y is not None and first_y is not None and abs(y - first_y) > 0.5:
            out.extend(units[idx:])
            return out
        prev_y = y
        try:
            u = _shift_formula(u, dx) if u.formular is not None else u.relocate(u.box.x + dx, y, 1.0)
        except Exception:
            pass
        out.append(u)
    return out


def justify_units(units, box, scale):
    """Trả về danh sách đơn vị đã giãn dấu cách để dòng chạm lề phải (trừ dòng cuối đoạn)."""
    if not units or box is None:
        return units
    # gom theo dòng
    lines, ys, prev_y = [], [], None
    for u in units:
        y = _line_y(u, prev_y)
        if not lines or (y is not None and prev_y is not None and abs(y - prev_y) > 0.5):
            lines.append([])
            ys.append(y)
        lines[-1].append(u)
        prev_y = y
    width = box.x2 - box.x
    out = []
    for li, (line, y) in enumerate(zip(lines, ys)):
        last_line = li == len(lines) - 1
        content = [i for i, u in enumerate(line) if not u.is_space]
        if last_line or y is None or len(content) < 2:
            out.extend(line)
            continue
        first, last = content[0], content[-1]
        slack = box.x2 - line[last].box.x2
        gaps = [i for i in range(first + 1, last) if line[i].is_space]
        if not gaps or slack <= 0.05 or slack > 0.2 * width:
            out.extend(line)
            continue
        step = slack / len(gaps)
        shift = 0.0
        for i, u in enumerate(line):
            if i in gaps:
                shift += step
            if shift and i > first:
                try:
                    u = _shift_formula(u, shift) if u.formular is not None else u.relocate(u.box.x + shift, y, 1.0)
                except Exception:  # đơn vị lạ: để nguyên còn hơn làm hỏng dòng
                    pass
            out.append(u)
        STATS["justified_lines"] += 1
    return out


# ---------------------------------------------------------------- làm hẹp chữ thay vì thu nhỏ
def condense_paragraph(paragraph, translation_font_ids, hs_min: float) -> None:
    """Đoạn bị thu nhỏ còn s: vẽ lại chữ dịch ở cỡ đầy đủ nhưng hẹp ngang còn s (lệnh PDF Tz).
    Toạ độ đã tính theo s nên bề ngang khớp; chữ cao đúng cỡ gốc; khoảng dòng đã cố định theo bản gốc.
    Hẹp tối đa còn hs_min, phần còn lại mới thu nhỏ để chữ không méo."""
    import copy

    from babeldoc.format.pdf.document_il.il_version_1 import GraphicState, PdfStyle

    s = paragraph.scale
    if not s or s >= 0.999 or id(paragraph) in CONDENSED:
        return
    CONDENSED.add(id(paragraph))  # hàm dàn của BabelDOC tự gọi lại chính nó: không làm hẹp hai lần
    hs = max(s, hs_min)
    for comp in paragraph.pdf_paragraph_composition or []:
        ch = comp.pdf_character if comp is not None else None
        if ch is None or ch.vertical or not ch.pdf_style or ch.pdf_style.font_id not in translation_font_ids:
            continue  # chỉ chữ dịch; công thức giữ nguyên như BabelDOC dàn
        gs = copy.copy(ch.pdf_style.graphic_state) if ch.pdf_style.graphic_state else GraphicState()
        gs.passthrough_per_char_instruction = f"{(gs.passthrough_per_char_instruction or '').strip()} {hs * 100:.2f} Tz".strip()
        ch.pdf_style = PdfStyle(font_id=ch.pdf_style.font_id, font_size=ch.pdf_style.font_size / hs, graphic_state=gs)
    STATS["condensed"] += 1


# ---------------------------------------------------------------- gắn vào BabelDOC
def install(line_skip_default: float = 1.12, justify: bool = True, hs_min: float = 0.85) -> str:
    from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
    from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting

    if getattr(Typesetting._layout_typesetting_units, "_thongngu", False):
        return "đã cài"

    orig_translate = ILTranslator.translate

    def translate(self, docs, *a, **k):
        measure_document(docs)
        mark_references(docs)
        mark_front_matter(docs)
        return orig_translate(self, docs, *a, **k)

    orig_pre_tr = ILTranslator.pre_translate_paragraph

    def pre_translate(self, paragraph, *a, **k):
        if id(paragraph) in SKIP:
            return None, None  # BabelDOC bỏ qua đoạn này, giữ nguyên chữ gốc
        return orig_pre_tr(self, paragraph, *a, **k)

    # 1) mỗi đoạn giữ cỡ tối ưu của riêng nó (không ép cả tài liệu về cỡ phổ biến)
    orig_get_scale = Typesetting._get_optimal_scale

    def get_scale(self, paragraph, *a, **k):
        v = orig_get_scale(self, paragraph, *a, **k)
        OPTIMAL[id(paragraph)] = v
        return v

    orig_pre = Typesetting.preprocess_document

    def preprocess(self, document, *a, **k):
        r = orig_pre(self, document, *a, **k)
        total = restored = 0
        for page in document.page or []:
            for paragraph in page.pdf_paragraph or []:
                total += 1
                if id(paragraph) in OPTIMAL:
                    restored += paragraph.optimal_scale != OPTIMAL[id(paragraph)]
                    paragraph.optimal_scale = OPTIMAL[id(paragraph)]
        if DEBUG:
            import sys
            print(f"[layout] preprocess: {total} đoạn, trả lại cỡ riêng cho {restored}", file=sys.stderr, flush=True)
        return r

    # Danh sách kiểu LaTeX mặc định (itemize cấp 2, gói llncs/svjour) bắt đầu bằng "–" (U+2013) mà
    # BabelDOC không coi là dấu đầu dòng, nên gộp mọi mục thành một khối liền. Chỉ xét ký tự đầu dòng.
    import babeldoc.format.pdf.document_il.midend.paragraph_finder as paragraph_finder

    orig_bullet = paragraph_finder.is_bullet_point
    if not getattr(orig_bullet, "_thongngu", False):
        def is_bullet_point(char):
            return orig_bullet(char) or char.char_unicode == "\u2013"

        is_bullet_point._thongngu = True
        paragraph_finder.is_bullet_point = is_bullet_point

    from babeldoc.format.pdf.document_il.midend.layout_parser import LayoutParser

    orig_fallback = LayoutParser.generate_fallback_line_layout_for_page

    def fallback(self, page, *a, **k):
        r = orig_fallback(self, page, *a, **k)
        try:
            n = widen_text_layouts(page)
            STATS["widened"] = STATS.get("widened", 0) + n
        except Exception as e:
            if DEBUG:
                import sys
                print(f"[layout] nới khung lỗi: {e!r}", file=sys.stderr, flush=True)
        return r

    LayoutParser.generate_fallback_line_layout_for_page = fallback

    # Lỗi xuống dòng giữa chữ của BabelDOC: trước khi đặt một ký tự, nó kiểm
    # x + rộng(ký tự) + rộng(từ ký tự đó tới hết từ) > lề phải, tức tính ký tự hiện tại HAI lần. Từ kết
    # thúc sát lề thì bị cắt đôi ("3D c" / "ủa"). Chỉ tính phần SAU ký tự hiện tại.
    orig_wb = Typesetting._get_width_before_next_break_point

    def width_before_break(self, units, scale):
        if not units or units[0].can_break_line:
            return 0
        return orig_wb(self, units[1:], scale)

    Typesetting._get_width_before_next_break_point = width_before_break

    orig_doc = Typesetting.typesetting_document

    def typesetting_document(self, document, *a, **k):
        try:
            n = attach_formula_curves(document)
            if DEBUG:
                import sys
                print(f"[layout] gán {n} nét vẽ vào công thức", file=sys.stderr, flush=True)
        except Exception as e:
            if DEBUG:
                import sys
                print(f"[layout] gán nét vẽ lỗi: {e!r}", file=sys.stderr, flush=True)
        return orig_doc(self, document, *a, **k)

    # 2) dàn xong đoạn bị thu nhỏ thì đổi sang làm hẹp chữ
    orig_find = Typesetting._find_optimal_scale_and_layout

    def find(self, paragraph, page, units, *a, **k):
        r = orig_find(self, paragraph, page, units, *a, **k)
        if k.get("apply_layout") or (len(a) >= 3 and a[2] is True):
            try:
                condense_paragraph(paragraph, set(self.font_mapper.fontid2font), hs_min)
            except Exception as e:  # không làm hỏng cả trang vì một đoạn
                if DEBUG:
                    import sys
                    print(f"[layout] condense lỗi: {e!r}", file=sys.stderr)
        return r

    orig_layout = Typesetting._layout_typesetting_units

    def layout(self, units, box, scale, line_skip, paragraph, *a, **k):
        info = ORIGINAL.get(id(paragraph))
        if info and info.get("bottom") is not None and box is not None and box.y > info["bottom"] + 0.01:
            # Trước khi vẽ, BabelDOC nâng đáy khung đoạn lên tới 3 pt để chừa khe với đoạn bên dưới. Dòng dịch
            # đã nằm đúng chân dòng gốc nên dòng cuối rơi ra ngoài khung bị nâng, BabelDOC tưởng không vừa và
            # thu nhỏ cả đoạn (đo 10-11: 0,95 -> 0,80, để lại khoảng trắng). Trả đáy về đúng đáy đoạn gốc.
            import copy
            box = copy.copy(box)
            box.y = info["bottom"]
        if not self.is_cjk:
            line_skip = line_skip_default
            if info and info["pitch"]:
                heights = [u.height for u in units if not u.is_space and u.height]
                if heights:
                    try:
                        h = statistics.mode(round(x, 2) for x in heights)
                    except statistics.StatisticsError:
                        h = statistics.median(heights)
                    if h > 0:
                        # BabelDOC xuống dòng bằng max(cao_chữ * scale * line_skip, ...). Chia thêm cho scale để
                        # khoảng dòng LUÔN bằng bản gốc, kể cả khi phải thu nhỏ chữ: mỗi dòng dịch nằm đúng chỗ
                        # dòng gốc, khoảng cách giữa các đoạn không đổi.
                        line_skip = info["pitch"] / (h * scale)
                        STATS["pitch_applied"] += 1
        if info and info.get("pitch") and box is not None and scale < 0.999 and not self.is_cjk:
            # Chân dòng đầu = đỉnh khung - cao_chữ*scale: chữ nhỏ thì dòng đầu bị đẩy lên. Hạ khung xuống
            # phần chênh để dòng đầu (và mọi dòng sau) nằm đúng chân dòng của bản gốc.
            import copy
            heights = [u.height for u in units if not u.is_space and u.height]
            if heights:
                h0 = statistics.median(heights)
                box = copy.copy(box)
                box.y2 -= h0 * (1 - scale)
                box.y -= h0 * (1 - scale)
        if info and info.get("right") and box is not None and abs(box.x2 - info["right"]) <= 6:
            # Khung đoạn của BabelDOC thường rộng hơn mép chữ gốc vài pt: dàn và căn đều theo mép chữ gốc.
            import copy
            box = copy.copy(box)
            box.x2 = info["right"]
        if info and info.get("free_x2") and box is not None and info["free_x2"] > box.x2:
            # Đoạn một dòng (mục danh sách, tiêu đề): bản dịch dài hơn thì lấn sang chỗ trống bên phải
            # trong cùng cột thay vì thu nhỏ chữ; nếu vừa khung cũ thì vị trí chữ không đổi.
            import copy
            box = copy.copy(box)
            box.x2 = info["free_x2"]
        if info and info.get("center") and box is not None:
            # Dòng căn giữa (tên bài, tác giả): dàn trong cả vùng được căn giữa rồi đặt tâm đúng tâm gốc.
            import copy
            cx, lo, hi = info["center"]
            wide = copy.copy(box)
            if info["lines"] <= 1:  # một dòng: cho dùng cả bề ngang vùng; nhiều dòng: giữ chỗ ngắt dòng theo khung gốc
                half = min(cx - lo, hi - cx)
                wide.x, wide.x2 = cx - half, cx + half
            placed, fit = orig_layout(self, units, wide, scale, line_skip, paragraph, *a, **k)
            if fit:
                placed = _center_lines(placed, cx)
                return placed, fit
        hang = info.get("hang", 0.0) if info and not paragraph.first_line_indent else 0.0
        if hang and box is not None:
            # Thụt treo: dàn trong khung hẹp (mép trái = mép dòng 2 trở đi) rồi kéo dòng đầu ra lại.
            import copy
            narrow = copy.copy(box)
            narrow.x = box.x + hang
            placed, fit = orig_layout(self, units, narrow, scale, line_skip, paragraph, *a, **k)
            if fit:
                placed = _move_first_line(placed, -hang)
        else:
            placed, fit = orig_layout(self, units, box, scale, line_skip, paragraph, *a, **k)
        if fit and justify and info and info["justified"]:
            placed = justify_units(placed, box, scale)
        if DEBUG and fit:
            import sys
            print(f"[layout] info={info and {k: info[k] for k in ('pitch','lines','justified')}} scale={scale:.2f} "
                  f"skip={line_skip:.3f} units={len(placed)} just={STATS['justified_lines']} "
                  f"box={box.x:.1f}-{box.x2:.1f} hang={hang:.1f} right={info and info.get('right')}", file=sys.stderr, flush=True)
        return placed, fit

    translate._thongngu = True
    layout._thongngu = True
    ILTranslator.translate = translate
    ILTranslator.pre_translate_paragraph = pre_translate
    Typesetting._layout_typesetting_units = layout
    Typesetting._get_optimal_scale = get_scale
    Typesetting.preprocess_document = preprocess
    Typesetting._find_optimal_scale_and_layout = find
    Typesetting.typesetting_document = typesetting_document
    return f"khoảng dòng theo bản gốc, căn đều hai lề, làm hẹp chữ tới {hs_min:.0%} thay vì thu nhỏ"
