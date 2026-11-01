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

# 字重错标补偿词表（spec §1，OS/2 usWeightClass 规范值核验无误）。仅英文
# 词——CJK「黑体/粗」是类别语义。触发条件：ID1/4/16 文本名尾词（大小写
# 不敏感）命中 ∧ 该行 weight==400（审查 S1 尾词限定：对齐 Windows「族名
# +样式尾置」惯例，封「Black Ops One」（真 400 面）类假阳；camelCase
# 尾缀（CorbelLight）无词边界不触发——假阴记账 spec §8）
_WEIGHT_WORDS = {
    "thin": 100, "extralight": 200, "ultralight": 200, "light": 300,
    "medium": 500, "semibold": 600, "demibold": 600, "bold": 700,
    "extrabold": 800, "ultrabold": 800, "black": 900, "heavy": 900,
}


def _compensate_weight(base: dict) -> int:
    """错标补偿：名字尾词命中词表且 OS/2==400 → 按词表改写。多命中取
    最大（"… Light" 与 "… Bold" 并存时取 700）。"""
    if base["weight"] != 400:
        return base["weight"]
    hits = [w for nid in (1, 4, 16)
            for _lg, s in (base["name_langs"].get(nid) or [])
            for w in ([_WEIGHT_WORDS[s.rstrip().rsplit(" ", 1)[-1].lower()]]
                      if s.rstrip() and
                      s.rstrip().rsplit(" ", 1)[-1].lower() in _WEIGHT_WORDS
                      else [])]
    return max(hits) if hits else base["weight"]


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


def _has_cjk(s: str) -> bool:
    """文本是否含 CJK（假名/汉字/谚文/兼容区等），CJK 优先判定共用。"""
    return any("\u3040" <= c <= "\u30ff"      # 假名
               or "\u3400" <= c <= "\u9fff"   # 汉字
               or "\uf900" <= c <= "\ufaff"   # 兼容汉字
               or "\uac00" <= c <= "\ud7af"   # 谚文
               or "\uff66" <= c <= "\uff9f"   # 半角假名
               or "\U00020000" <= c <= "\U0002ffff"   # CJK 扩展 B+
               for c in s)


def _cjk_first(names: list[str]) -> str | None:
    """多语言名里挑 CJK 名优先（中文环境可读），无 CJK 退首名。"""
    for s in names:
        if _has_cjk(s):
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


def _subfamily_texts(name, nid: int) -> list[tuple[int, str]]:
    """subfamilyNameID 直查 name 表（全平台），[(langID, 文本)]。按
    subfamilyNameID 直查天然覆盖其指向 ID2 记录的场景；fvar 实例的
    subfamilyNameID 也常指向自建记录，同一直查路径处理。记录缺失返回
    空。"""
    return [(r.langID, r.toUnicode()) for r in name.names
            if r.nameID == nid and r.toUnicode()]


def _instance_compose(family_recs: list[tuple[int, str]],
                      sub_recs: list[tuple[int, str]]) -> tuple[list[str], str]:
    """限定名别名 + display_name 同一路径生成（spec §1，审查 B2/B3）。
    family_recs 取显示链同源族名记录（ID16 优先，回退 ID1，调用方已按
    CJK 优先排序），子族按同 langID 配对；子族缺该语言只出英文（0x409）
    限定名。返回 (限定名列表, display_name)；无任何配对返回 ([], "")。"""
    sub_by_lang = dict(sub_recs)
    sub_default = sub_by_lang.get(0x409)
    names: list[str] = []
    display = ""
    for lang, fam in family_recs:
        sub = sub_by_lang.get(lang) or sub_default
        if not sub:
            continue
        full = f"{fam} {sub}"
        if full not in names:
            names.append(full)
    if names:
        # display = 显示链语言（family_recs 首条，与 _display_name 的
        # CJK 优先链同源序）配对出的那条；保证 display ∈ aliases
        display = names[0]
    return names, display


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
                    if s.startswith("@"):
                        # 竖排变体名不进别名（审查二轮 P1）：'@'=0x40 排序
                        # 恒在最前——污染指纹/版本去重键/find_by_name，
                        # 展示名回退 aliases[0] 时必占首位（照 ID1/16/4
                        # 展示路径同款过滤；查询侧 normalize_name 剥 @，
                        # 匹配不受影响）
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
                # PS 名只认 Windows 平台（platformID==3）记录：去重键应与
                # Windows 字体系统的标识同源——corbell 实测 platform0 的
                # ID6='Corbel-Light' 而 Windows 记录='CorbelLight'，首条
                # 命中曾把去重键漂成非 Windows 形态（审查二轮 P1）。无
                # Win 记录的老字体回退首条（Mac 平台），不改键域
                psname = next((r.toUnicode() for r in name.names
                               if r.nameID == 6 and r.platformID == 3), None)
                if psname is None:
                    psname = next((r.toUnicode() for r in name.names
                                   if r.nameID == 6), None)
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
                # —— 公共属性（默认/实例行共用）——
                # 错标补偿先于 base 组装（统一作用于默认/静态行；实例行
                # weight 之后按轴值覆盖，不受补偿二次改写）
                weight = _compensate_weight({"weight": weight,
                                             "name_langs": by_lang})
                base = {
                    "dir_path": str(path.parent), "rel_path": path.name,
                    "face_index": idx, "psname": psname, "version": version,
                    "aliases": aliases, "weight": weight, "width": width,
                    "italic": italic, "cmap": cmap,
                    "script": classify_script(cmap),
                    "category": classify_category(aliases),
                    "display_name": _display_name(by_id, aliases),
                    "family_names": _family_names(by_id),
                    "name_langs": by_lang, "name_records": tuple(records)}
                fvar = tt.get("fvar")
                if fvar is None or not fvar.instances:
                    out.append(FontRecord(**base))
                    continue
                # —— VF：默认行 + 命名实例行 ——
                out.append(FontRecord(**base))     # inst_index=0, var_axes=()
                tags = [a.axisTag for a in fvar.axes]
                defaults = [float(a.defaultValue) for a in fvar.axes]
                # 限定名用的族名记录：ID16 优先回退 ID1——ID16 优先系
                # spec §1 拍板，与 _display_name 的 ID1 优先链不同源，
                # 非 @ 名，[(langID, 文本)]；按 CJK 优先排序，使
                # display 与 _display_name 的族名语言选择对齐（preflight
                # 裁定）。fam_recs 仅含平台 3 记录，纯 Mac 名 VF 的实例行
                # 将跳过（现实趋零，默认行仍带族别名）
                fam_recs = (by_lang.get(16) or by_lang.get(1)) or []
                fam_recs = sorted(fam_recs, key=lambda p: not _has_cjk(p[1]))
                for k, inst in enumerate(fvar.instances, start=1):
                    coords = tuple(float(inst.coordinates.get(t, dv))
                                   for t, dv in zip(tags, defaults, strict=True))
                    if coords == tuple(defaults):
                        continue          # 坐标=默认：默认行已覆盖（S3）
                    sub_recs = _subfamily_texts(name, inst.subfamilyNameID)
                    if not sub_recs:
                        continue          # 子族名不可解析：无名不合成（拍板）
                    qual, disp = _instance_compose(fam_recs, sub_recs)
                    if not qual:
                        continue
                    w = int(coords[tags.index("wght")]) if "wght" in tags \
                        else weight
                    it = (italic
                          or ("slnt" in tags and coords[tags.index("slnt")] != 0)
                          or ("ital" in tags and coords[tags.index("ital")] == 1))
                    out.append(FontRecord(
                        **{**base,
                           "aliases": aliases + qual,
                           "display_name": disp,
                           "name_langs": {},        # B2：GUI 走 display 回退
                           "weight": w,
                           "italic": it,
                           "inst_index": k,
                           "var_axes": coords}))
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
