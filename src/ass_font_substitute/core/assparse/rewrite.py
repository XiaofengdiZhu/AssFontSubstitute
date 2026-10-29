from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from ass_font_substitute.core.assparse.encoding import detect_encoding


@dataclass
class RewriteResult:
    out_path: Path | None
    changed_lines: list[int]


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
    编码识别失败抛 EncodingDetectError，由调用方（jobs.apply_to_file 宽
    except）兜底——有意为之，勿对齐。"""
    if out and overwrite_src:
        raise ValueError("out 与 overwrite_src 互斥")
    src = Path(src)
    data = src.read_bytes()
    enc = detect_encoding(data)
    text = data.decode(enc)
    # "-le"/"-be" 编解码器不剥 BOM：剥出文本外，写回补回（"utf-16"/
    # "utf-8-sig" 自管 BOM，bom=False 不受影响）
    bom = text.startswith("\ufeff")
    if bom:
        text = text[1:]
    lines = text.splitlines(keepends=True)
    m = _expand_at(mapping)

    section = ""
    formats: dict[str, list[str]] = {}
    changed: list[int] = []
    # 单遍交替正则（查表替换）：串行逐项 sub 会在「某映射值恰是另一映射
    # 键」时链式误替（{SimSun→宋体, 宋体→思源} 把 {\fnSimSun} 写成思源）。
    # 长键优先——正则交替取先中，短键是长键前缀时会截胡
    alt = "|".join(re.escape(old)
                   for old in sorted(m, key=len, reverse=True))
    fn_re = re.compile(r"\\fn(" + alt + r")(?=[\\}])") if alt else None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped.lower()
            continue
        if stripped.lower().startswith("format:") and "styles" in section:
            formats[section] = [f.strip().lower() for f in
                                stripped[len("Format:"):].split(",")]
            continue
        if stripped.startswith("Style:") and "styles" in section:
            fmt = formats.get(section)
            if not fmt or "fontname" not in fmt:
                continue
            parts = stripped[len("Style:"):].split(",")
            fi = fmt.index("fontname")
            if fi < len(parts):
                cur = parts[fi].strip()
                if cur in m:
                    parts[fi] = m[cur]
                    newline = "Style:" + ",".join(parts)
                    lines[i] = line.replace(stripped, newline, 1)
                    changed.append(i)
            continue
        if stripped.startswith(("Dialogue:", "Comment:")) and "events" in section:
            # 只在 {...} 块内替换 \fn（Comment 行的 \fn 也改写）——外壳
            # 限定块级，正文里的字面 \fn 片段不动
            if fn_re is not None:
                new_line = re.sub(
                    r"\{[^{}]*\}",
                    lambda b: fn_re.sub(lambda mo: r"\fn" + m[mo.group(1)],
                                        b.group(0)),
                    line)
            else:
                new_line = line
            if new_line != line:
                lines[i] = new_line
                changed.append(i)

    result_text = ("\ufeff" if bom else "") + "".join(lines)
    result_bytes = result_text.encode(enc)
    if overwrite_src:
        tmp = src.with_name(src.name
                            + f".tmp{os.getpid()}-{threading.get_ident()}")
        tmp.write_bytes(result_bytes)
        os.replace(tmp, src)
        return RewriteResult(src, changed)
    if out is not None:
        Path(out).write_bytes(result_bytes)
        return RewriteResult(Path(out), changed)
    return RewriteResult(None, changed)  # dry-run
