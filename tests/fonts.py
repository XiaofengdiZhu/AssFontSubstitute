"""测试字体工厂：用 fontBuilder 生成方块字形的最小 TTF/TTC，供各测试模块复用。"""
from __future__ import annotations

from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTCollection

DEFAULT_FAMILY = "測試體"

# zh-CN / zh-TW 别名写入 Windows 平台（platformID=3, UTF-16）的对应 langID
_EXTRA_LANGS = (("zh-CN", 0x804), ("zh-TW", 0x404))


def _box(pen: TTGlyphPen, k: float = 1.0) -> None:
    def p(x, y):
        return (300 + (x - 300) * k, 300 + (y - 300) * k)
    pen.moveTo(p(100, 0))
    pen.lineTo(p(100, 600))
    pen.lineTo(p(500, 600))
    pen.lineTo(p(500, 0))
    pen.closePath()


def _circle(pen: TTGlyphPen, scale: float = 1.0) -> None:
    # 四段二次贝塞尔近似实心圆（圆心 (300,300)，半径 280；
    # 0.9142r 为二次曲线过 45° 点的控制点系数）
    cx, cy, r = 300, 300, 280 * scale
    k = round(r * 0.9142)
    pen.moveTo((cx + r, cy))
    pen.qCurveTo((cx + k, cy + k), (cx, cy + r))
    pen.qCurveTo((cx - k, cy + k), (cx - r, cy))
    pen.qCurveTo((cx - k, cy - k), (cx, cy - r))
    pen.qCurveTo((cx + k, cy - k), (cx + r, cy))
    pen.closePath()


def _new_glyph(shape: str, scale: float = 1.0) -> object:
    pen = TTGlyphPen(None)
    if shape == "circle":
        _circle(pen, scale)
    else:
        _box(pen, scale)
    return pen.glyph()


def _make(spec: dict):
    """构建最小字体的 TTFont（未 save）；build_font/build_ttc 与测试直接复用。"""
    chars = spec.get("chars", "")
    family = spec.get("family", DEFAULT_FAMILY)
    weight = spec.get("weight", 400)
    width = spec.get("width", 5)
    italic = spec.get("italic", False)
    psname = spec.get("psname") or (family + "-" + str(weight))
    version = spec.get("version", "Version 1.000")
    extra_aliases = spec.get("extra_aliases") or {}
    shape = spec.get("shape", "box")
    scale = spec.get("scale", 1.0)

    glyph_order = [".notdef", "space"] + [f"g{i}" for i in range(len(chars))]
    cmap = {0x20: "space"}
    cmap.update({ord(d): "space" for d in "0123456789Aa"})
    for i, c in enumerate(chars):
        cmap[ord(c)] = f"g{i}"

    glyphs = {".notdef": _new_glyph(shape, scale), "space": TTGlyphPen(None).glyph()}
    for i in range(len(chars)):
        glyphs[f"g{i}"] = _new_glyph(shape, scale)

    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(dict.fromkeys(glyph_order, (500, 0)))
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWeightClass=weight,
                usWidthClass=width, fsSelection=(1 if italic else 64))
    fb.setupNameTable({
        1: family, 2: "Regular", 4: family, 6: psname, 5: version,
        3: "TestFont;" + version + ";AFS", 8: "AFS Test",
    })
    nt = fb.font["name"]
    for lang_key, lang_id in _EXTRA_LANGS:
        value = extra_aliases.get(lang_key)
        if not value:
            continue
        for nid in (1, 4):
            nt.setName(value, nid, 3, 1, lang_id)
    fb.setupPost()
    return fb.font


def build_font(path: Path, chars: str, family: str = DEFAULT_FAMILY,
               extra_aliases: dict | None = None, psname: str | None = None,
               version: str = "Version 1.000", weight: int = 400,
               width: int = 5, italic: bool = False, shape: str = "box",
               scale: float = 1.0) -> None:
    spec = {"chars": chars, "family": family, "extra_aliases": extra_aliases,
                "psname": psname, "version": version, "weight": weight, "width": width,
                "italic": italic, "shape": shape, "scale": scale}
    _make(spec).save(path)


def build_ttc(path: Path, specs: list[dict]) -> None:
    coll = TTCollection()
    coll.fonts = [_make(s) for s in specs]
    coll.save(path)


def build_vf(path: Path, chars: str, family: str = DEFAULT_FAMILY,
             axes: list[dict] | None = None,
             instances: list[dict] | None = None,
             weight: int = 400, psname: str | None = None,
             version: str = "Version 1.000",
             extra_aliases: dict | None = None) -> None:
    """最小可变字体：fvar 轴+命名实例，无 gvar（轮廓不随轴变——
    parser/index/渲染管道测试只依赖 fvar+name+cmap；轮廓级变异由
    freetype 自身保证，spec §6）。"""
    font = _make({"chars": chars, "family": family, "weight": weight,
                  "psname": psname, "version": version,
                  "extra_aliases": extra_aliases})
    # fontBuilder.addFvar 的轴只收 tuple/带属性的 designspace 对象（不收 dict），
    # 命名实例 dict 键为 stylename——这里把本工厂的 dict 口径转换过去
    fvar_axes = [(a["tag"], a["min"], a["default"], a["max"], a["name"])
                 for a in (axes or [])]
    fvar_instances = [{"stylename": i["name"], "location": i["location"]}
                      for i in (instances or [])]
    fb = FontBuilder(font=font)
    fb.setupFvar(fvar_axes, fvar_instances)
    font.save(path)
