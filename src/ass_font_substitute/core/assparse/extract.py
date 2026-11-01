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
    # 降级剔除的畸形行（"第 N 行无法解析已跳过：…"）；非空 = 部分提取
    skipped_lines: list[str] = field(default_factory=list)


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


# 降级剔除坏行的上限：正常字幕坏行是个位数，超限视为文件整体损坏，
# 继续二分只是烧 CPU（每轮 O(log n) 次全量解析）
_MAX_DEGRADE_REMOVALS = 50


def _reload_skip_bad_lines(text: str) -> tuple[Document | None, list[str], str]:
    """单条畸形行降级装载：light_ass 行级不容错（Event 10 域 unpack /
    AssTime / Style 域解析在 strict=False 下也照抛），一条坏行曾让
    Document.load 整文件报废（审查二轮 P1，批量场景一颗老鼠屎坏一锅粥）。

    降级：坏行存在性对行前缀单调（坏行仍在的前缀必失败）→ 二分找最小
    失败前缀（O(log n) 次全量解析），其末行即肇事者；剔除后重载，循环
    处理多条坏行。返回 (doc, 剔除行描述, 实际参与解析的文本)——第三个
    返回值供提取侧的文本级扫描与 doc 内容保持同源（Format 行被剔除时
    扫原文会全量误判失效）。彻底失败返回 (None, [], "")。
    行文本按原编码不可解时调用方直接走原错误，不进本函数。"""
    lines = text.splitlines(keepends=True)
    removed: set[int] = set()
    skipped: list[str] = []
    # N 条坏行要 N 次剔除 + 1 次成功解析 = N+1 轮：上限 50 指剔除数，
    # 循环须跑到 _MAX_DEGRADE_REMOVALS + 1（差一曾让恰好 50 条坏行的
    # 文件在最后一轮剔除后来不及终判而整文件报废）
    for _ in range(_MAX_DEGRADE_REMOVALS + 1):
        keep = [ln for i, ln in enumerate(lines) if i not in removed]
        try:
            doc = Document.from_string("".join(keep), strict=False)
        except Exception:
            lo, hi, culprit = 1, len(keep), None
            while lo <= hi:
                mid = (lo + hi) // 2
                try:
                    Document.from_string("".join(keep[:mid]), strict=False)
                    lo = mid + 1
                except Exception:
                    culprit = mid
                    hi = mid - 1
            # 全量失败已知，二分终会探到 len(keep)——culprit 理论必有值；
            # None 分支纯防御（未来 light_ass 出非行局部失败形态时兜底）
            if culprit is None:
                return None, skipped, ""
            orig = [i for i in range(len(lines)) if i not in removed][culprit - 1]
            removed.add(orig)
            snippet = lines[orig].strip()
            skipped.append(f"第 {orig + 1} 行无法解析已跳过：{snippet[:40]}")
            continue
        return doc, skipped, "".join(keep)
    return None, skipped, ""


# str.splitlines 独有断行符（\r\n|\r|\n 之外）：light_ass 按行切文用
# splitlines，在这些字符上会把一条 Dialogue 劈成两行——后半句掉进
# Unknown section 静默丢弃，用字不计数 → 覆盖率虚高放行缺字候选。
# rewrite 的 _LINE_RE 只认 \r\n|\r|\n（这些字符算正文内容），两边口径
# 不对称。解析前归一剔除（与 rewrite 对齐；分隔符本身不计入用字集），
# 降级路径共用同一段归一后文本
_SPLIT_ONLY_TABLE = dict.fromkeys((0x0B, 0x0C, 0x1C, 0x1D, 0x1E,
                                   0x85, 0x2028, 0x2029))


def _missing_fontname_styles(text: str) -> set[str]:
    """styles 段 Format 缺 fontname 列或 Style 行列数不足到 fontname 位
    的样式名：light_ass 对取不到的 fontname 回退默认值 'Arial'——幻影
    桶吞正文用字、覆盖率决策错位（审查二轮 P2）。返回应判失效的样式名
    （doc.styles 的键与此处文本解析同源，直接按名对上）。

    口径备忘（审查核验）：Style 行先于 Format 行时 light_ass 回落
    DEFAULT_STYLE_FORMAT（fontname 位 1），扫描同口径；样式名只 lstrip
    不 rstrip，带尾随空格的名字此处对不上 doc.styles 键（极罕见，漏判
    方向无害）；缩进的 Format/Style 行 light_ass 整体忽略而扫描会判，
    方向同样无害。"""
    from light_ass.constants import DEFAULT_STYLE_FORMAT

    section = ""
    fmt: list[str] | None = None
    bad: set[str] = set()
    for line in text.splitlines():
        s = line.strip()
        low = s.lower()
        if s.startswith("[") and s.endswith("]"):
            section = low
            continue
        if section != "[v4+ styles]":
            continue
        if low.startswith("format:"):
            fmt = [f.strip().lower() for f in s[len("format:"):].split(",")]
            continue
        if low.startswith("style:"):
            eff = fmt if fmt is not None \
                else [f.lower() for f in DEFAULT_STYLE_FORMAT]
            fi = eff.index("fontname") if "fontname" in eff else None
            parts = s[len("style:"):].split(",")
            if fi is None or fi >= len(parts):
                name = parts[0].strip() if parts else ""
                if name:
                    bad.add(name)
    return bad


def extract_usages(path: Path) -> ExtractResult:
    path = Path(path)
    data = path.read_bytes()
    try:
        enc = detect_encoding(data)
    except EncodingDetectError as e:
        return ExtractResult(parse_error=str(e))
    embedded = _embedded_fonts(data, enc)
    # 主路径与降级路径共用同一手工解码（_bom_and_codec 剥 BOM + 显式端
    # 序正文 codec）：解析前归一劈行符必须发生在喂给 light_ass 之前
    bom_b, body_codec = _bom_and_codec(data, enc)
    try:
        text = data[len(bom_b):].decode(body_codec)
    except UnicodeDecodeError as e:
        return ExtractResult(parse_error=f"UnicodeDecodeError: {e}")
    text = text.translate(_SPLIT_ONLY_TABLE)
    skipped: list[str] = []
    scan_text = text
    try:
        doc = Document.from_string(text, strict=False)
    except Exception as e:
        # 降级：剔除肇事坏行重载（单条畸形行不拖垮整文件）；彻底失败
        # 才回原错误。文本级扫描（缺列样式判定）改扫实际参与解析的
        # 文本——Format 行本身是肇事行时扫原文会全量误判失效
        doc, skipped, scan_text = _reload_skip_bad_lines(text)
        if doc is None:
            return ExtractResult(parse_error=f"{type(e).__name__}: {e}")
    r = _extract(doc, embedded, _missing_fontname_styles(scan_text))
    r.skipped_lines = skipped
    return r


def _extract(doc: Document, embedded: list[str] | None = None,
             broken_styles: set[str] | None = None) -> ExtractResult:
    buckets: dict[str, FontUsage] = {}
    broken = broken_styles or set()
    # fontname 缺列的样式判失效（light_ass 回退默认 'Arial'，幻影桶见
    # _missing_fontname_styles）：不建桶、不计样式字体，入 invalid_styles
    style_fonts = {name: st.fontname for name, st in doc.styles.items()
                   if name not in broken}
    # VSFilter/libass 对样式名不敏感匹配：`main` 引用 `Main` 样式按
    # 大小写敏感取值会整行丢字+误报 unknown_styles；精确命中优先
    style_fonts_cf: dict[str, str] = {}
    for name, fname in style_fonts.items():
        style_fonts_cf.setdefault(name.casefold(), fname)
    used_styles: set[str] = set()
    invalid_styles = [n for n, f in style_fonts.items() if not f]
    invalid_styles += sorted(broken)
    # 事件引用失效样式常带大小写漂移（VSFilter 不敏感匹配正是
    # style_fonts_cf 存在的理由），失效判定须同口径
    broken_cf = {b.casefold() for b in broken}

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
            # 失效样式（fontname 缺列）≠ 未知样式，不进 unknown 误报；
            # Comment 行不渲染，未知样式引用不构成信号
            if ev.style.casefold() not in broken_cf and not ev.comment:
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
