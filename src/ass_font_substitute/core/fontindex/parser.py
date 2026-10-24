from __future__ import annotations

import re
from pathlib import Path

import xxhash
from fontTools.ttLib import TTCollection
from fontTools.ttLib import TTFont

from ass_font_substitute.core.fontindex.probes import classify_category
from ass_font_substitute.core.fontindex.probes import classify_script
from ass_font_substitute.core.models import CmapRanges
from ass_font_substitute.core.models import FontRecord

NAME_IDS = (1, 4, 6, 16, 17)
TTC_EXTS = {".ttc", ".otc"}
_VER_RE = re.compile(r"([\d]+(?:\.\d+)*)")


class FontParseError(Exception):
    pass


def parse_version(s: str | None) -> tuple[int, ...] | None:
    if not s:
        return None
    m = _VER_RE.search(s)
    if not m:
        return None
    try:
        return tuple(int(x) for x in m.group(1).split("."))
    except ValueError:
        return None


def _load_fonts(path: Path) -> list[TTFont]:
    try:
        if path.suffix.lower() in TTC_EXTS:
            return list(TTCollection(str(path)).fonts)
        return [TTFont(str(path), lazy=True)]
    except Exception as e:  # fontTools 各类异常统一包装
        raise FontParseError(f"{path.name}: {e}") from e


def parse_font_file(path: Path) -> list[FontRecord]:
    fonts = _load_fonts(path)
    try:
        data = path.read_bytes()
        xxh = xxhash.xxh64(data).hexdigest()
        st = path.stat()
        out: list[FontRecord] = []
        errors: list[str] = []
        for idx, tt in enumerate(fonts):
            try:
                name = tt["name"]
                aliases = sorted({r.toUnicode() for r in name.names
                                  if r.nameID in NAME_IDS and r.toUnicode()})
                psname = next((r.toUnicode() for r in name.names if r.nameID == 6), None)
                version = parse_version(next((r.toUnicode() for r in name.names
                                              if r.nameID == 5), None))
                os2 = tt["OS/2"]
                cmap = CmapRanges.from_cmap_subtables(tt["cmap"].tables)
                out.append(FontRecord(
                    dir_path=str(path.parent), rel_path=path.name, face_index=idx,
                    psname=psname, version=version, aliases=aliases,
                    weight=os2.usWeightClass, width=os2.usWidthClass,
                    italic=bool(os2.fsSelection & 1),
                    cmap=cmap, xxh=xxh, size=st.st_size, mtime_ns=st.st_mtime_ns,
                    script=classify_script(cmap), category=classify_category(aliases),
                ))
            except Exception as e:
                # spec §11：TTC 个别子字体解析失败 → 跳过该子字体，其余照常
                # （face_index 保留原始枚举下标，允许空洞）
                errors.append(f"{path.name}#{idx}: {e}")
        if not out:
            raise FontParseError("; ".join(errors) or f"{path.name}: no face parsed")
        return out
    finally:
        # lazy 加载会持有文件句柄，Windows 上不关闭会阻塞后续删除/覆盖
        for tt in fonts:
            tt.close()
