from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from ass_font_substitute.core.assparse.encoding import BOMS
from ass_font_substitute.core.assparse.encoding import EncodingDetectError
from ass_font_substitute.core.assparse.encoding import detect_encoding


@dataclass
class RewriteResult:
    out_path: Path | None
    changed_lines: list[int]


# 行终止符只认 \r\n|\r|\n（str/bytes.splitlines 在 \v \f \x85 \u2028 等
# 上也断行——正文字段含这些字符时行被劈开、段落状态机错乱）
_LINE_RE = re.compile(r"\r\n|\r|\n")
_LINE_RE_B = re.compile(rb"\r\n|\r|\n")

# 多字节序列不含 0x0A/0x0D 的编码：可在字节级切行（big5/sjis/gb 尾字节
# 范围均不含控制符，utf-8 续字节 ≥0x80），逐行锁原字节。utf-16/32 单元
# 流里 0A/00 相邻可能是字符内容（U+xx0A 等），只能文本级整编
BYTE_LINE_CODECS = frozenset(
    {"utf-8", "utf-8-sig", "gb18030", "big5", "shift_jis"})


def _split_lines(s) -> list:
    rx = _LINE_RE_B if isinstance(s, bytes) else _LINE_RE
    out = []
    start = 0
    for mo in rx.finditer(s):
        out.append(s[start:mo.end()])
        start = mo.end()
    if start < len(s) or (not out and s):
        out.append(s[start:])
    return out


def _bom_and_codec(data: bytes, enc: str) -> tuple[bytes, str]:
    """(BOM 字节, 正体裁码)：BOM 拆出正文外统一手工补回——通用 utf-16
    编解码器恒写 LE、"utf-8-sig" 之外自管不了端序；BOM 文件的正体一律
    用显式端序 codec。"""
    for bom, codec in BOMS:
        if data.startswith(bom):
            if codec == "utf-8-sig":
                return bom, "utf-8"
            if codec == "utf-16":
                return bom, "utf-16-le"
            return bom, codec
    return b"", enc


def _expand_at(mapping: dict[str, str]) -> dict[str, str]:
    """展开 @ 变体：对称补齐竖排/横排两个方向。

    - 纯名键 K → V：补 @K → @V（既有行为）。
    - @ 键 @K → @V：补 K → V（extract 的 raw_name 以 @ 开头时只有这一个键，
      缺横排方向会导致 Style 里的纯名不被改写）。
    - 值不带 @ 的 @ 键只按原样保留（无法推知另一方向）。
    """
    m = dict(mapping)
    for k, v in mapping.items():
        if k.startswith("@"):
            if v.startswith("@"):
                m.setdefault(k[1:], v[1:])
            continue
        m.setdefault("@" + k, "@" + v)
    return m


def rewrite_file(src: Path, mapping: dict[str, str], out: Path | None = None,
                 overwrite_src: bool = False) -> RewriteResult:
    """按 mapping 重写字幕文件里的字体名。

    错误契约与 extract（折成 parse_error 字段）不同：本函数是落盘入口，
    编码识别失败与写回编码失败（替换值含目标编码不可表示的字符）都抛
    EncodingDetectError，由调用方（jobs.apply_to_file 宽 except）兜底
    ——有意为之，勿对齐。"""
    if out and overwrite_src:
        raise ValueError("out 与 overwrite_src 互斥")
    src = Path(src)
    data = src.read_bytes()
    enc = detect_encoding(data)
    bom_b, body_codec = _bom_and_codec(data, enc)
    body = data[len(bom_b):]
    try:
        text = body.decode(body_codec)
    except UnicodeDecodeError as e:
        # 探测只看前 64KB 样本（性能），>64KB 处才出现的非法序列在此浮出
        # ——对调用方仍是编码问题，折包对齐错误契约
        raise EncodingDetectError(f"样本通过但全文解码失败（{body_codec}）: {e}") from e
    lines = _split_lines(text)
    # 字节级行切片：未改动的行锁原字节（big5 等双编码字 decode→encode
    # 回写规范码，未改行字节也被翻转；对得上才用，行数不齐弃用）
    byte_lines = None
    if enc in BYTE_LINE_CODECS:
        bl = _split_lines(body)
        if len(bl) == len(lines):
            byte_lines = bl
    # 映射键 strip 归一（审查二轮 P1）：light_ass 不 strip Style 域，
    # extract 按原样建桶（raw_name="MS Gothic "），映射键随之带尾随空格；
    # 而本函数取样式域先 strip（cur = parts[fi].strip()）、\fn 名字的
    # 空白由正则组单独保留——键不归一则「提得到、改不掉」静默漏替。
    # 空格键剥空后丢弃（空 alt 会让 fn 正则零宽匹配全场）；同名键剥空后
    # 相撞按后者覆盖（调用方不应给同名键，与 dict 字面量语义一致）。
    m = _expand_at({k.strip(): v for k, v in mapping.items() if k.strip()})

    section = ""
    formats: dict[str, list[str]] = {}
    changed: list[int] = []
    # 单遍交替正则（查表替换）：串行逐项 sub 会在「某映射值恰是另一映射
    # 键」时链式误替（{SimSun→宋体, 宋体→思源} 把 {\fnSimSun} 写成思源）。
    # 长键优先——正则交替取先中，短键是长键前缀时会截胡。
    # 两侧空白容忍（\s→pad 原样保留）：extract 把「\fn名字 」strip 后建桶
    # 而 libass 对尾随空格 trim，不宽容则「提得到、改不掉」静默漏替。
    # lookahead 容 "}" 与 "\\" 之外还容 ")"——\t(...) 动画参数里的 \fn
    # 名字后随右括号（extract 已下钻提取，见 _transform_fns）
    alt = "|".join(re.escape(old)
                   for old in sorted(m, key=len, reverse=True))
    fn_re = (re.compile(r"\\fn([ \t]*)(" + alt + r")([ \t]*)(?=[\\})])")
             if alt else None)

    for i, line in enumerate(lines):
        stripped = line.strip()
        # 关键字统一小写比对（VSFilter 对行关键字不敏感；小写关键字的
        # 文件曾整段跳过=静默零改写）
        low = stripped.lower()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = low
            continue
        if low.startswith("format:") and "styles" in section:
            formats[section] = [f.strip().lower() for f in
                                stripped[len("Format:"):].split(",")]
            continue
        if low.startswith("style:") and "styles" in section:
            fmt = formats.get(section)
            if not fmt or "fontname" not in fmt:
                continue
            parts = stripped[len("Style:"):].split(",")
            fi = fmt.index("fontname")
            if fi < len(parts):
                cur = parts[fi].strip()
                if cur in m:
                    # 域内格式空格保留（"Style: Main, 舊字體 ," 的对齐空格
                    # 是作者的排版，整域替换曾抹掉）
                    pad_l = parts[fi][:len(parts[fi]) - len(parts[fi].lstrip())]
                    pad_r = parts[fi][len(parts[fi].rstrip()):]
                    parts[fi] = pad_l + m[cur] + pad_r
                    # 关键字原样写回（小写文件不改写成 Style:）
                    newline = stripped[:len("Style:")] + ",".join(parts)
                    lines[i] = line.replace(stripped, newline, 1)
                    changed.append(i)
            continue
        if low.startswith(("dialogue:", "comment:")) and "events" in section:
            # 只在 {...} 块内替换 \fn（Comment 行的 \fn 也改写）——外壳
            # 限定块级，正文里的字面 \fn 片段不动
            if fn_re is not None:
                def _sub_fn(mo: re.Match) -> str:
                    return (r"\fn" + mo.group(1) + m[mo.group(2)]
                            + mo.group(3))
                new_line = re.sub(
                    r"\{[^{}]*\}",
                    lambda b: fn_re.sub(_sub_fn, b.group(0)),
                    line)
            else:
                new_line = line
            if new_line != line:
                lines[i] = new_line
                changed.append(i)

    changed_set = set(changed)
    try:
        if byte_lines is not None:
            chunks = [bom_b]
            for i, tl in enumerate(lines):
                chunks.append(tl.encode(body_codec) if i in changed_set
                              else byte_lines[i])
            result_bytes = b"".join(chunks)
        else:
            result_bytes = bom_b + "".join(lines).encode(body_codec)
    except UnicodeEncodeError as e:
        # P2：替换值含目标编码不可表示的字符（如简体名写回 big5）时
        # UnicodeEncodeError 裸逃——折包对齐错误契约，gui 兜底显示的
        # 报错可读（类名沿 EncodingDetectError 略名不副实，但调用方按
        # 编码问题分支的语义一致）
        raise EncodingDetectError(
            f"替换值含编码 {body_codec} 无法表示的字符，无法写回: {e}") from e
    if overwrite_src or out is not None:
        # 两分支统一 tmp+os.replace 原子写（审查二轮 P0-2：另存分支曾
        # write_bytes 直写——盘满/崩溃截断目标文件）；finally 清残骸，
        # 替换失败（目标被播放器占用等）不留 .tmp<pid>
        dst = src if overwrite_src else Path(out)
        tmp = dst.with_name(dst.name
                            + f".tmp{os.getpid()}-{threading.get_ident()}")
        try:
            tmp.write_bytes(result_bytes)
            os.replace(tmp, dst)
        finally:
            tmp.unlink(missing_ok=True)
        return RewriteResult(dst, changed)
    return RewriteResult(None, changed)  # dry-run
