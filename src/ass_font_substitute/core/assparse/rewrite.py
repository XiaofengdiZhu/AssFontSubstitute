from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from ass_font_substitute.core.assparse.encoding import detect_encoding


@dataclass
class RewriteResult:
    out_path: Path | None
    changed_lines: list[int]


def _expand_at(mapping: dict[str, str]) -> dict[str, str]:
    m = dict(mapping)
    for k, v in mapping.items():
        if k.startswith("@"):
            continue
        m.setdefault("@" + k, "@" + v)
    return m


def _fn_pattern(name: str) -> re.Pattern:
    return re.compile(r"(\\fn" + re.escape(name) + r")(?=[\\}])")


def rewrite_file(src: Path, mapping: dict[str, str], out: Path | None = None,
                 overwrite_src: bool = False) -> RewriteResult:
    assert not (out and overwrite_src), "out 与 overwrite_src 互斥"
    src = Path(src)
    data = src.read_bytes()
    enc = detect_encoding(data)
    text = data.decode(enc)
    lines = text.splitlines(keepends=True)
    m = _expand_at(mapping)

    section = ""
    formats: dict[str, list[str]] = {}
    changed: list[int] = []

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
            def fix_block(match: re.Match) -> str:
                block = match.group(0)
                new_block = block
                for old, new in m.items():
                    new_block = _fn_pattern(old).sub(r"\\fn" + new, new_block)
                return new_block
            new_line = re.sub(r"\{[^{}]*\}", fix_block, line)
            if new_line != line:
                lines[i] = new_line
                changed.append(i)

    result_text = "".join(lines)
    result_bytes = result_text.encode(enc)
    if overwrite_src:
        tmp = src.with_name(src.name + f".tmp{os.getpid()}")
        tmp.write_bytes(result_bytes)
        os.replace(tmp, src)
        return RewriteResult(src, changed)
    if out is not None:
        Path(out).write_bytes(result_bytes)
        return RewriteResult(Path(out), changed)
    return RewriteResult(None, changed)  # dry-run
