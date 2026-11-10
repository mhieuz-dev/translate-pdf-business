"""Dựng font cho bản dịch PDF: chuyển OTF (CFF) của TeX Gyre Termes và Latin Modern sang TTF.

Vì sao: BabelDOC chỉ dùng font TTF và chỉ coi là font có chân khi tên chứa "serif", nên đặt tên
ThongNguSerif-<Kiểu>-<Dáng>.ttf. Hai bộ font này giống chữ LaTeX (Times và Computer Modern) mà vẫn
đủ 146/146 chữ tiếng Việt (kiểm bằng check_font_vi.py), còn font gốc trong paper thì không có dấu Việt.
Nguồn: CTAN fonts/tex-gyre/opentype, fonts/lm/fonts/opentype/public/lm (GUST Font License).
Chạy: python3 build_fonts.py <thư-mục-chứa-otf>
"""
import sys
from pathlib import Path

from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable

SOURCES = {
    "Termes": "texgyretermes-{}.otf",
    "LMRoman": "lmroman10-{}.otf",
}
STYLES = {"Regular": "regular", "Bold": "bold", "Italic": "italic", "BoldItalic": "bolditalic"}


def otf_to_ttf(src: Path, dst: Path) -> None:
    font = TTFont(src)
    order = font.getGlyphOrder()
    glyphs = font.getGlyphSet()
    font["loca"] = newTable("loca")
    font["glyf"] = glyf = newTable("glyf")
    glyf.glyphOrder = order
    glyf.glyphs = {}
    for name in order:
        pen = TTGlyphPen(glyphs)
        glyphs[name].draw(Cu2QuPen(pen, 1.0, reverse_direction=True))
        glyf[name] = pen.glyph()
    font["maxp"] = maxp = newTable("maxp")
    maxp.tableVersion = 0x00010000
    for k in ("maxZones", "maxTwilightPoints", "maxStorage", "maxFunctionDefs", "maxInstructionDefs",
              "maxStackElements", "maxSizeOfInstructions", "maxComponentElements"):
        setattr(maxp, k, 1 if k == "maxZones" else 0)
    del font["CFF "]
    if "VORG" in font:
        del font["VORG"]
    post = font["post"]
    post.formatType = 2.0
    post.extraNames = []
    post.mapping = {}
    post.glyphOrder = order
    font.sfntVersion = "\000\001\000\000"
    font.save(dst)


def main(src_dir: str) -> None:
    out = Path(__file__).resolve().parent
    for family, pattern in SOURCES.items():
        for style, key in STYLES.items():
            dst = out / f"ThongNguSerif-{family}-{style}.ttf"
            otf_to_ttf(Path(src_dir) / pattern.format(key), dst)
            print(dst.name, dst.stat().st_size)


if __name__ == "__main__":
    main(sys.argv[1])
