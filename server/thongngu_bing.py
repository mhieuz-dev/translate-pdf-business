"""Dịch đủ đoạn văn dài bằng Bing.

pdf2zh-next 2.9 cắt mọi đoạn gửi Bing còn 1000 ký tự (`text = text[:1000]`), phần sau bị bỏ
mà không báo lỗi: đoạn dài trong paper mất cả câu cuối (đo 10-11: đoạn 27 dòng của bài SLAM mất
5 dòng). Ở đây chia đoạn dài thành nhiều phần <= 1000 ký tự, cắt ở cuối câu (không cắt giữa
công thức {v3} hay thẻ <style id='1'>...</style>), dịch từng phần rồi nối lại.
"""
from __future__ import annotations

import re

LIMIT = 1000
_TOKEN = re.compile(r"<style id='\d+'>|</style>|\{v\d+\}")


def split_for_bing(text: str, limit: int = LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    blocked = [False] * len(text)  # bên trong thẻ/công thức hoặc giữa cặp <style>...</style>
    depth = 0
    pos = 0
    for m in _TOKEN.finditer(text):
        for i in range(pos, m.start()):
            blocked[i] = depth > 0
        for i in range(m.start(), m.end()):
            blocked[i] = True
        if m.group().startswith("<style"):
            depth += 1
        elif m.group() == "</style>":
            depth = max(0, depth - 1)
        pos = m.end()
    for i in range(pos, len(text)):
        blocked[i] = depth > 0

    def cut_points(lo, hi, sentence):
        for i in range(hi - 1, lo, -1):
            if text[i].isspace() and not blocked[i] and (not sentence or text[i - 1] in ".!?;:"):
                yield i

    parts, start = [], 0
    while len(text) - start > limit:
        hi = start + limit
        cut = next(cut_points(start + limit // 3, hi, True), None)
        if cut is None:
            cut = next(cut_points(start, hi, False), None)
        if cut is None:  # một khối liền 1000 ký tự không có chỗ cắt: cắt cứng, tránh giữa thẻ
            cut = hi
            while cut > start + 1 and blocked[cut - 1] and blocked[cut]:
                cut -= 1
        parts.append(text[start:cut].strip())
        start = cut
    parts.append(text[start:].strip())
    return [p for p in parts if p]


def install() -> str:
    from pdf2zh_next.translator.translator_impl.bing import BingTranslator

    if getattr(BingTranslator.do_translate, "_thongngu", False):
        return "dịch đủ đoạn dài"
    orig_do = BingTranslator.do_translate
    orig_init = BingTranslator.__init__

    def __init__(self, *a, **k):
        orig_init(self, *a, **k)
        # bộ nhớ đệm cũ chứa bản dịch đã bị cắt cụt: đổi khoá để không lấy lại chúng
        self.add_cache_impact_parameters("thongngu_full_length", 1)

    def do_translate(self, text, rate_limit_params: dict | None = None):
        parts = split_for_bing(text)
        if len(parts) == 1:
            return orig_do(self, text, rate_limit_params)
        out = []
        for i, part in enumerate(parts):
            if i:
                self.rate_limiter.wait(rate_limit_params)
            out.append(orig_do(self, part, rate_limit_params))
        return " ".join(out)

    do_translate._thongngu = True
    BingTranslator.__init__ = __init__
    BingTranslator.do_translate = do_translate
    return "dịch đủ đoạn dài"
