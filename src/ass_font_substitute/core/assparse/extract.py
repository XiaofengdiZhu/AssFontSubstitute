from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from light_ass import Document
from light_ass.curly.parsed_line import CommentNode
from light_ass.curly.parsed_line import TextNode
from light_ass.curly.tags import FontNameTag
from light_ass.curly.tags import ResetStyleTag
from light_ass.curly.tags import TransformTag

from ass_font_substitute.core.assparse.encoding import ALIAS_BOM
from ass_font_substitute.core.assparse.encoding import EncodingDetectError
from ass_font_substitute.core.assparse.encoding import detect_encoding
from ass_font_substitute.core.assparse.rewrite import _bom_and_codec
from ass_font_substitute.core.fontindex.library import normalize_name
from ass_font_substitute.core.models import FontUsage


@dataclass
class ExtractResult:
    usages: list[FontUsage] = field(default_factory=list)
    unknown_styles: list[str] = field(default_factory=list)
    invalid_styles: list[str] = field(default_factory=list)  # Fontname 为空的样式
    parse_error: str | None = None


def _transform_fns(tag) -> list:
    """\t(0,1000,\fnX\fscx120)：light_ass 把动画参数解析进 modifier，
    内嵌 \fn 与顶层 FontNameTag 同类，但解析失败的项回退成 RawTag
    （无 tag_name 属性）——按 tag_name=='fn' 甄别可一并滤掉；
    _raw 串在位，_fn_name 照常恢复 "(P)" 伪字体名全称。
    只下钻一层：\t 内再嵌 \t 的字体引用不支持（libass 支持存疑）。
    解析失败按无引用处理。"""
    try:
        mod = tag.get_params().get("modifier")
    except Exception:
        return []
    if mod is None:
        return []
    # modifier 是 light_ass 自定义序列（repr 像 list 但非 list 子类），
    # 只能按可迭代性归一，勿按 isinstance 包层
    mods = [mod] if isinstance(mod, (str, bytes)) or not hasattr(mod, "__iter__") \
        else list(mod)
    return [x for x in mods if getattr(x, "tag_name", None) == "fn"]


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


def _paren_tail(tag: FontNameTag, nodes: list, idx: int) -> str:
    """light_ass 截断签名：raw 以 ")" 结尾时，字体名在 ")" 之后的残余
    （到下一个 \\ 或 } 为止）被切成 CommentNode 挂在后随节点上——
    libass/rewrite 口径下这些字属于字体名，吞并恢复全名（审查二轮 P1：
    {\fnFoo(Bar)X} 提取 Foo(Bar) 幽灵卡，真名 Foo(Bar)X 永不可替）。
    CommentNode 无 _raw，text 字段即碎片；遇新标签（raw 以 \\ 开头）
    或正文节点（TextNode 等，已在 {} 外）即停；无残余返回 ""。
    只处理顶层：\t(...) 内嵌 \fn 带括号时 light_ass 连整个 TransformTag
    的 raw 一起截（更深层的截断形态），另案。"""
    raw = getattr(tag, "_raw", None)
    if raw is None or not raw.raw_str.endswith(")"):
        return ""
    tail = []
    while idx < len(nodes):
        n = nodes[idx]
        if isinstance(n, CommentNode):
            tail.append(n.text)
            idx += 1
            continue
        r = getattr(n, "_raw", None)
        if r is None or r.raw_str.startswith("\\"):
            break
        tail.append(r.raw_str)
        idx += 1
    return "".join(tail)


def _embedded_fonts(data: bytes, enc: str) -> list[str]:
    """扫 [Fonts] 段 fontname: 行（内嵌字体名，剥扩展、归一、去重）。
    只认该段内行——正文的 "fontname:" 字样不算。解码失败按无内嵌。"""
    bom_b, codec = _bom_and_codec(data, enc)
    try:
        text = data[len(bom_b):].decode(codec)
    except UnicodeDecodeError:
        return []
    section = ""
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        low = s.lower()
        if s.startswith("[") and s.endswith("]"):
            section = low
            continue
        if section != "[fonts]" or not low.startswith("fontname:"):
            continue
        name = s[len("fontname:"):].strip()
        # 只剥已知字体扩展：含点号的名字（"X 1.02" 无扩展）不得被截
        if name.lower().endswith((".ttf", ".otf", ".ttc")):
            name = name.rsplit(".", 1)[0]
        if name:
            out.append(normalize_name(name))
    return list(dict.fromkeys(out))


def extract_usages(path: Path) -> ExtractResult:
    path = Path(path)
    data = path.read_bytes()
    try:
        enc = detect_encoding(data)
    except EncodingDetectError as e:
        return ExtractResult(parse_error=str(e))
    embedded = _embedded_fonts(data, enc)
    try:
        # 畸形的 Style/Event 行在 strict=False 下仍可能抛错，记录而非中断（批量场景）
        doc = Document.load(str(path), encoding=ALIAS_BOM.get(enc, enc),
                            strict=False)
    except Exception as e:
        return ExtractResult(parse_error=f"{type(e).__name__}: {e}")
    return _extract(doc, embedded)


def _extract(doc: Document, embedded: list[str] | None = None) -> ExtractResult:
    buckets: dict[str, FontUsage] = {}
    style_fonts = {name: st.fontname for name, st in doc.styles.items()}
    # VSFilter/libass 对样式名不敏感匹配：`main` 引用 `Main` 样式按
    # 大小写敏感取值会整行丢字+误报 unknown_styles；精确命中优先
    style_fonts_cf: dict[str, str] = {}
    for name, fname in style_fonts.items():
        style_fonts_cf.setdefault(name.casefold(), fname)
    used_styles: set[str] = set()
    invalid_styles = [n for n, f in style_fonts.items() if not f]

    def lookup_style(s: str) -> str | None:
        return style_fonts.get(s) or style_fonts_cf.get(s.casefold())

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
        style_font = lookup_style(ev.style)
        if style_font is None:
            used_styles.add(ev.style)
            continue
        current = style_font
        is_comment = ev.comment
        tf_names: set[str] = set()    # \t(...) 动画内嵌 \fn 引用的字体
        line_chars: set[str] = set()  # 整行文本（保守归入动画字体）
        # list() 物化摊平序列：ParsedLine.__iter__ 才是把 OverrideBlock
        # 摊成节点流的入口，直接下标 parsed[ni] 走 __getitem__ 拿到的是
        # 未摊平 parts（OverrideBlock 包装），isinstance 全部落空
        parsed = list(ev.parse_tags(doc, parse_escape_nodes=True).parsed)
        ni = 0
        while ni < len(parsed):
            node = parsed[ni]
            ni += 1
            if isinstance(node, FontNameTag):
                value = _fn_name(node)
                if value:
                    tail = _paren_tail(node, parsed, ni)
                    if tail:
                        value += tail
                    current = value
                    # Comment 里的 \fn 也是引用：建桶，但不计 inline 次数与字
                    usage = bucket(current)
                    if not is_comment:
                        usage.inline_count += 1
                else:
                    current = style_font  # \fn0 / \fn 空 = 还原样式字体
            elif isinstance(node, ResetStyleTag):
                current = lookup_style(node.value) or style_font if node.value else style_font
            elif isinstance(node, TransformTag):
                # 动画参数内的 \fn 也是引用：建桶计 inline 次数；顶层字体
                # 状态不被动画切换影响（正文仍归样式/原字体）
                for it in _transform_fns(node):
                    name = _fn_name(it)
                    if name:
                        tf_names.add(name)
                        usage = bucket(name)
                        if not is_comment:
                            usage.inline_count += 1
            elif isinstance(node, TextNode) and not is_comment and current:
                bucket(current).chars.update(node.text)
                line_chars.update(node.text)
            # DrawingNode / EscapeNode / RawTag / CommentNode 不计

        # 动画进行时这些字以动画字体渲染：整行文字保守计入其用字集
        # （覆盖率低报会放行缺字候选，宁多勿少）；Comment 行不计字
        if tf_names and not is_comment:
            for name in tf_names:
                bucket(name).chars.update(line_chars)

    if embedded:
        emb = set(embedded)
        for u in buckets.values():
            if u.norm_name in emb:
                u.embedded = True

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
                                             set(u.chars), set(u.variants),
                                             u.embedded)
            else:
                m = out[u.norm_name]
                m.style_count += u.style_count
                m.inline_count += u.inline_count
                m.chars |= u.chars
                m.variants |= u.variants
                m.embedded = m.embedded or u.embedded
    return list(out.values())
