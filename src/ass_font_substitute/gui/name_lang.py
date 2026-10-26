"""展示名语言挑选：各界面字体名按界面语言优先链从索引的 name 表语言
记录（fonts.name_langs，SCHEMA v7）里现挑，替代入库单值 display_name。
纯函数零 Qt——VM 层与 widget 层共用。"""
from __future__ import annotations

# 界面语言 → 展示名语言优先链（langID 依次取记录集中首个命中；未知
# 设置值按简序列）。与详情页命名卡默认语言同一条链
_UI_LANG_ORDER = {
    "zh-Hans": (0x804, 0x1004, 0x404, 0xC04, 0x409, 0x411),
    "zh-Hant": (0x404, 0xC04, 0x804, 0x1004, 0x409, 0x411),
}


def ui_lang(settings) -> str:
    """设置语言 → 链键（None/未知值按简中序列）。"""
    zh = settings.get("language") if settings is not None else None
    return "zh-Hant" if zh == "zh-Hant" else "zh-Hans"


def pick_display_name(e, ui: str = "zh-Hans") -> str:
    """实体展示名：ID1 legacy family → ID16 → ID4（与入库 display_name
    同字段序），字段内按 UI 语言链取该语言记录；链全不中取该字段表序
    首条（旧 _cjk_first 同场景取首个 CJK 文本，此处统一退表序首条——
    韩文等非链 CJK 语言让位靠前记录，已拍板），全字段无记录退入库
    display_name（旧库/无平台 3 记录）。行归组仍按 display_name——按链
    挑名会因各 face 语言记录集不同而裂行。"""
    for nid in (1, 16, 4):
        recs = e.name_langs.get(nid) if e.name_langs else None
        if not recs:
            continue
        for lcid in _UI_LANG_ORDER.get(ui, _UI_LANG_ORDER["zh-Hans"]):
            for lg, text in recs:
                if lg == lcid:
                    return text
        return recs[0][1]
    return e.display_name
