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
    if out and overwrite_src:
        raise ValueError("out 与 overwrite_src 互斥")
    src = Path(src)
    data = src.read_bytes()
    enc = detect_encoding(data)
    text = data.decode(enc)
    lines = text.splitlines(keepends=True)
    m = _expand_at(mapping)

    section = ""
    formats: dict[str, list[str]] = {}
    changed: list[int] = []
    # 预编译到行循环外（大文件 × 几百映射项时每 block×每项 re.compile
    # 缓存查询百万级）
    fn_res = [(re.compile(r"(\\fn" + re.escape(old) + r")(?=[\\}])"), new)
              for old, new in m.items()]

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
            # 只在 {...} 块内替换 \fn（Comment 行的 \fn 也改写）
            def fix_block(match: re.Match, _res=fn_res) -> str:
                block = match.group(0)
                for pat, new in _res:
                    block = pat.sub(r"\\fn" + new, block)
                return block
            new_line = re.sub(r"\{[^{}]*\}", fix_block, line)
            if new_line != line:
                lines[i] = new_line
                changed.append(i)

    result_text = "".join(lines)
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
