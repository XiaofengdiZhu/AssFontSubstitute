from __future__ import annotations

import re
from pathlib import Path

from fontTools.ttLib import TTCollection
from fontTools.ttLib import TTFont

from ass_font_substitute.core.fontindex.probes import classify_category
from ass_font_substitute.core.fontindex.probes import classify_script
from ass_font_substitute.core.models import CmapRanges
from ass_font_substitute.core.models import FontRecord

# 1=family 4=full 6=PostScript 16=typographic family。17（typographic
# subfamily，即 FontCreator 的「样式名称」）是样式描述不是名称——
# FOTPunchello 家族它填 'Condensed Bold'、微软雅黑填 'Regular'，收进
# 别名会污染按名搜索（搜 bold 命中全库粗体）并污染展示名；2（subfamily）
# 同理从未收过
NAME_IDS = (1, 4, 6, 16)
# 详情命名卡消费的原始记录 ID 全集（name_records 入库 SCHEMA v8）：
# 全平台全语言存，2/3 只进命名卡展示、从不进别名
RECORD_IDS = (1, 2, 3, 4, 16)
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


def _cjk_first(names: list[str]) -> str | None:
    """多语言名里挑 CJK 名优先（中文环境可读），无 CJK 退首名。"""
    for s in names:
        if any("\u3040" <= c <= "\u30ff"      # 假名
               or "\u3400" <= c <= "\u9fff"   # 汉字
               or "\uf900" <= c <= "\ufaff"   # 兼容汉字
               or "\uac00" <= c <= "\ud7af"   # 谚文
               or "\uff66" <= c <= "\uff9f"   # 半角假名
               or "\U00020000" <= c <= "\U0002ffff"   # CJK 扩展 B+
               for c in s):
            return s
    return names[0] if names else None


def _display_name(by_id: dict[int, list[str]], aliases: list[str]) -> str:
    """展示名 = ID1 legacy family（Windows 字体查看器/字体列表显示的
    就是它——FOTPunchelloCondBold 的 ID1='FOTPunchello Cond Bold'；超
    出 4 样式的字重 MS 官方也拆进 ID1，如 微软雅黑 Light）。ID1 缺失
    回退 ID16 typographic family（spec：ID16 缺失时视 ID1 为其，反向
    回退同理）→ ID4 full name → 别名首；每级多语言 CJK 优先。样式名
    （ID2/17）绝不参与——它们是样式描述；@ 前缀是竖排变体名，不作
    展示名。"""
    for nid in (1, 16, 4):
        names = [s for s in by_id.get(nid, []) if not s.startswith("@")]
        pick = _cjk_first(names)
        if pick:
            return pick
    return aliases[0] if aliases else ""


def _family_names(by_id: dict[int, list[str]]) -> tuple[str, ...]:
    """字体系（FontCreator「字体系名称」栏，写进 name 表的实字段，
    非推导）：ID16 typographic family 全部 Windows 平台记录；ID16 缺失
    视 ID1 为其（spec 语义，4 样式内字体如 微软雅黑 Regular/Bold 的
    ID16 本就缺）。全语言集合入库——同族各 face 记录语言数可能不同，
    单值 CJK 优先会误裂，归组按集合交集判。样式名/@ 变体不参与。"""
    for nid in (16, 1):
        names = tuple(dict.fromkeys(
            s for s in by_id.get(nid, []) if not s.startswith("@")))
        if names:
            return names
    return ()


def _load_fonts(path: Path) -> list[TTFont]:
    try:
        if path.suffix.lower() in TTC_EXTS:
            # lazy=True：各 face 只在取 name/OS/2/cmap 时定点读表
            return list(TTCollection(str(path), lazy=True).fonts)
        return [TTFont(str(path), lazy=True)]
    except Exception as e:  # fontTools 各类异常统一包装
        raise FontParseError(f"{path.name}: {e}") from e


def parse_font_file(path: Path) -> list[FontRecord]:
    # 不读全文：lazy 加载只定点取 name/OS/2/cmap 三张小表，文件级变更
    # 检测由 update_index 的 size+mtime 门负责，解析层无需内容指纹
    fonts = _load_fonts(path)
    try:
        out: list[FontRecord] = []
        errors: list[str] = []
        for idx, tt in enumerate(fonts):
            try:
                name = tt["name"]
                by_id: dict[int, list[str]] = {}
                # 展示名语言链候选：ID1/4/16 的 Windows 平台记录带 langID
                # 入库（GUI 按界面语言链挑名用；@ 变体不参与）
                by_lang: dict[int, list[tuple[int, str]]] = {}
                # 命名卡原始记录（SCHEMA v8 入库，详情打开同步读零闪烁）
                records: list[tuple[int, int, int, str]] = []
                all_names: set[str] = set()
                for r in name.names:
                    s = r.toUnicode()
                    if not s:
                        continue
                    if r.nameID in RECORD_IDS:
                        records.append((r.nameID, r.platformID, r.langID, s))
                    if r.nameID not in NAME_IDS:
                        continue
                    all_names.add(s)
                    if r.platformID == 3 and r.nameID in (1, 4, 16) \
                            and not s.startswith("@"):
                        by_lang.setdefault(r.nameID, []).append((r.langID, s))
                    # display 只取 Windows 平台记录：Windows 字体查看器
                    # 显示的就是它；Macintosh 平台同名记录序在前会劫持
                    # 选择（FOTPunchelloCondBold 实证：Mac ID1='FOTPunchello'
                    # 排在 Win ID1='FOTPunchello Cond Bold' 前）
                    if r.platformID == 3:
                        by_id.setdefault(r.nameID, []).append(s)
                aliases = sorted(all_names)
                psname = next((r.toUnicode() for r in name.names if r.nameID == 6), None)
                version = parse_version(next((r.toUnicode() for r in name.names
                                              if r.nameID == 5), None))
                # OS/2 缺失（老字库/精简字体，恰是 v9 要救的文鼎系）回退
                # 中性默认值继续——可渲染字体不该因缺度量表被整 face 丢弃；
                # name/cmap 缺失才判坏（无名字无覆盖数据，实体无意义）
                os2 = tt.get("OS/2")
                weight = os2.usWeightClass if os2 is not None else 400
                width = os2.usWidthClass if os2 is not None else 5
                italic = bool(os2.fsSelection & 1) if os2 is not None else False
                cmap = CmapRanges.from_cmap_subtables(tt["cmap"].tables)
                out.append(FontRecord(
                    dir_path=str(path.parent), rel_path=path.name, face_index=idx,
                    psname=psname, version=version, aliases=aliases,
                    weight=weight, width=width,
                    italic=italic,
                    cmap=cmap,
                    script=classify_script(cmap), category=classify_category(aliases),
                    display_name=_display_name(by_id, aliases),
                    family_names=_family_names(by_id),
                    name_langs=by_lang, name_records=tuple(records),
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
