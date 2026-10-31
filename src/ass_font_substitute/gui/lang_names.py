"""语言显示名查询：CLDR 子集表（zh-Hans/zh-Hant，生成于
lang_names_data，scripts/gen_lang_names.py），替代 babel
Locale.get_display_name——打包 -30MB（拍板 2026-11-01）。

组合口径与 babel core.get_display_name 同构：语言名 + " (文字, 地区)"
（细节名逗号连接）；文字缺失时 zh 按地区推断（TW/HK/MO→Hant，其余→
Hans，仅带地区才推断——babel likely-subtags 的语言名实际歧义只有 zh）；
查无语言返回 None，调用方走静态兜底。"""
from __future__ import annotations

from ass_font_substitute.gui.lang_names_data import LANGS_ZH_HANS
from ass_font_substitute.gui.lang_names_data import LANGS_ZH_HANT
from ass_font_substitute.gui.lang_names_data import SCRIPTS_ZH_HANS
from ass_font_substitute.gui.lang_names_data import SCRIPTS_ZH_HANT
from ass_font_substitute.gui.lang_names_data import TERRS_ZH_HANS
from ass_font_substitute.gui.lang_names_data import TERRS_ZH_HANT


def display_name(tag: str, ui: str) -> str | None:
    """BCP47 tag（'en-US'/'zh-TW'/'az-Arab'，大小写/连字符随意）→
    界面语言（ui='zh_CN'|'zh_TW'）显示名。"""
    langs, scripts, terrs = (
        (LANGS_ZH_HANS, SCRIPTS_ZH_HANS, TERRS_ZH_HANS) if ui != "zh_TW"
        else (LANGS_ZH_HANT, SCRIPTS_ZH_HANT, TERRS_ZH_HANT))
    parts = tag.replace("-", "_").lower().split("_")
    lang = langs.get(parts[0])
    if lang is None:
        return None
    script = next((p for p in parts[1:] if len(p) == 4), None)
    terr = next((p for p in parts[1:] if len(p) == 2 or p.isdigit()), None)
    if script is None and terr is not None and parts[0] == "zh":
        script = "hant" if terr in ("tw", "hk", "mo") else "hans"
    details = [d for d in (scripts.get(script) if script else None,
                           terrs.get(terr) if terr else None) if d]
    return f"{lang} ({', '.join(details)})" if details else lang
