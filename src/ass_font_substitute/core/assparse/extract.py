from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from light_ass import Document
from light_ass.curly.parsed_line import TextNode
from light_ass.curly.tags import FontNameTag
from light_ass.curly.tags import ResetStyleTag

from ass_font_substitute.core.assparse.encoding import EncodingDetectError
from ass_font_substitute.core.assparse.encoding import detect_encoding
from ass_font_substitute.core.fontindex.library import normalize_name
from ass_font_substitute.core.models import FontUsage


@dataclass
class ExtractResult:
    usages: list[FontUsage] = field(default_factory=list)
    unknown_styles: list[str] = field(default_factory=list)
    invalid_styles: list[str] = field(default_factory=list)  # Fontname 为空的样式
    parse_error: str | None = None


def _fn_name(tag: FontNameTag) -> str:
    # \fn0 / \fn 空参是还原样式字体的重置信号：value 解析为 None，与原始串写法无关，
    # 故先看 value 再做原始串恢复，避免把 "0" 误判为字体名。
    value = tag.value
    if not value:
        return ""
    # light_ass 会把 \fn名字(...) 中 "(...)" 当作标签参数，value 截断为括号内容；
    # 从原始串恢复完整写法（与 rewrite 的行级全等匹配口径一致），无 _raw 时退回 value。
    raw = getattr(tag, "_raw", None)
    if raw is not None and raw.raw_str.startswith("\\fn"):
        name = raw.raw_str[len("\\fn"):].strip(" \t")
    else:
        name = value.strip(" \t")
    return "" if name == "0" else name  # 模板原有 value != "0" 守卫


def extract_usages(path: Path) -> ExtractResult:
    path = Path(path)
    try:
        enc = detect_encoding(path.read_bytes())
    except EncodingDetectError as e:
        return ExtractResult(parse_error=str(e))
    try:
        # 畸形的 Style/Event 行在 strict=False 下仍可能抛错，记录而非中断（批量场景）
        doc = Document.load(str(path), encoding=enc, strict=False)
    except Exception as e:
        return ExtractResult(parse_error=f"{type(e).__name__}: {e}")
    return _extract(doc)


def _extract(doc: Document) -> ExtractResult:
    buckets: dict[str, FontUsage] = {}
    style_fonts = {name: st.fontname for name, st in doc.styles.items()}
    used_styles: set[str] = set()
    invalid_styles = [n for n, f in style_fonts.items() if not f]

    def bucket(raw: str) -> FontUsage:
        norm = normalize_name(raw)
        if norm not in buckets:
            buckets[norm] = FontUsage(raw_name=raw, norm_name=norm,
                                      style_count=0, inline_count=0, chars=set())
        u = buckets[norm]
        u.variants.add(raw)
        return u

    for fontname in style_fonts.values():
        if fontname:
            bucket(fontname).style_count += 1

    for ev in doc.events:
        style_font = style_fonts.get(ev.style)
        if style_font is None:
            used_styles.add(ev.style)
            continue
        current = style_font
        is_comment = ev.comment
        for node in ev.parse_tags(doc, parse_escape_nodes=True).parsed:
            if isinstance(node, FontNameTag):
                value = _fn_name(node)
                if value:
                    current = value
                    # Comment 里的 \fn 也是引用：建桶，但不计 inline 次数与字
                    usage = bucket(current)
                    if not is_comment:
                        usage.inline_count += 1
                else:
                    current = style_font  # \fn0 / \fn 空 = 还原样式字体
            elif isinstance(node, ResetStyleTag):
                current = style_fonts.get(node.value, style_font) if node.value else style_font
            elif isinstance(node, TextNode) and not is_comment and current:
                bucket(current).chars.update(node.text)
            # DrawingNode / EscapeNode / RawTag / CommentNode 不计

    return ExtractResult(usages=list(buckets.values()),
                         unknown_styles=sorted(used_styles),
                         invalid_styles=invalid_styles)


def merge_usages(results: list[ExtractResult | list[FontUsage]]) -> list[FontUsage]:
    """按规范名合并多来源用字桶。元素可为 ExtractResult，也可直接给
    FontUsage 列表（GUI 文件层按勾选文件聚合时免去再包一层）。"""
    out: dict[str, FontUsage] = {}
    for r in results:
        for u in (r.usages if isinstance(r, ExtractResult) else r):
            if u.norm_name not in out:
                out[u.norm_name] = FontUsage(u.raw_name, u.norm_name,
                                             u.style_count, u.inline_count,
                                             set(u.chars), set(u.variants))
            else:
                m = out[u.norm_name]
                m.style_count += u.style_count
                m.inline_count += u.inline_count
                m.chars |= u.chars
                m.variants |= u.variants
    return list(out.values())
