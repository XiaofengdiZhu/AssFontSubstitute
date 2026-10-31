"""从 babel（CLDR）生成语言/文字/地区显示名表 → gui/lang_names_data.py。

一次性生成工具：运行环境装 babel（dev 期现成），产物自足后运行时零
babel 依赖（打包 -30MB，拍板 2026-11-01）。表 = CLDR zh_CN/zh_Hant 的
languages（仅语言级键，复合键不被 get_display_name 消费）+ scripts +
territories 全量；组合口径与 babel core.get_display_name 对拍校验。
CLDR 升级后重跑即可。

用法：uv run python scripts/gen_lang_names.py"""
from __future__ import annotations

import sys
from pathlib import Path

from babel import Locale
from babel import __version__ as babel_version

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "ass_font_substitute" / "gui" / "lang_names_data.py"


def compose(langs, scripts, terrs, tag: str, hant: bool) -> str | None:
    """与 babel core.get_display_name 同构的组合（消费方 gui/lang_names.py
    同式）：语言名 + " (文字, 地区)"；查无语言返回 None。文字缺失按 CLDR
    likely subtags 补：zh 地区定文字（TW/HK/MO→Hant，其余→Hans）——babel
    走完整 likely-subtags 表，语言名场景只有 zh 有实际歧义。"""
    lang = langs.get(tag.split("_")[0])  # 语言级键（babel 同：取基础语言）
    if lang is None:
        return None
    parts = tag.split("_")
    script = next((p for p in parts[1:] if len(p) == 4), None)
    terr = next((p for p in parts[1:] if len(p) == 2 or p.isdigit()), None)
    if script is None and terr is not None and parts[0] == "zh":
        script = "hant" if terr in ("tw", "hk", "mo") else "hans"
    details = [d for d in (scripts.get(script) if script else None,
                           terrs.get(terr) if terr else None) if d]
    return f"{lang} ({', '.join(details)})" if details else lang


def main() -> int:
    hans, hant = Locale.parse("zh_CN"), Locale.parse("zh_Hant")
    langs = {k.lower(): (hans.languages.get(k), hant.languages.get(k))
             for k in set(hans.languages) | set(hant.languages)
             if "_" not in k}
    langs = {k: v for k, v in langs.items() if v[0] or v[1]}
    scripts = {k.lower(): (hans.scripts.get(k), hant.scripts.get(k))
               for k in set(hans.scripts) | set(hant.scripts)}
    scripts = {k: v for k, v in scripts.items() if v[0] or v[1]}
    terrs = {k.lower(): (hans.territories.get(k), hant.territories.get(k))
             for k in set(hans.territories) | set(hant.territories)}
    terrs = {k: v for k, v in terrs.items() if v[0] or v[1]}

    # 对拍：组合须与 babel get_display_name 逐 tag 一致（两套 ui 各跑）
    samples = ("en", "en-US", "zh", "zh-TW", "zh-Hant", "zh-Hant-TW",
               "ja", "ko", "ru", "fil-PH", "de-AT", "sv-FI", "nb-NO",
               "az-Arab", "es-419", "pt-PT")
    for tag in samples:
        bcp = tag.replace("-", "_").lower()
        for ui, _loc in (("zh_CN", hans), ("zh_TW", hant)):
            try:
                exp = Locale.parse(bcp).get_display_name(ui)
            except Exception:
                exp = None          # babel 认不出的 tag（zh_Hans_TW 等）
            got = compose({k: v[0] if ui == "zh_CN" else v[1]
                           for k, v in langs.items()},
                          {k: v[0] if ui == "zh_CN" else v[1]
                           for k, v in scripts.items()},
                          {k: v[0] if ui == "zh_CN" else v[1]
                           for k, v in terrs.items()}, bcp, ui == "zh_TW")
            assert got == exp or exp is None, \
                f"{tag}@{ui}: {got!r} != {exp!r}"

    def fmt(d, idx):
        return "\n".join(f"    {k!r}: {v[idx]!r},"
                         for k, v in d.items() if v[idx] is not None)

    body = f'''"""CLDR 语言/文字/地区显示名表（zh-Hans + zh-Hant）——生成文件勿手改。

来源：babel {babel_version}（CLDR）经 scripts/gen_lang_names.py 抽取；
键全小写（语言/文字代码、地区码）。消费方 gui/lang_names.py。
"""

LANGS_ZH_HANS = {{
{fmt(langs, 0)}}}

LANGS_ZH_HANT = {{
{fmt(langs, 1)}}}

SCRIPTS_ZH_HANS = {{
{fmt(scripts, 0)}}}

SCRIPTS_ZH_HANT = {{
{fmt(scripts, 1)}}}

TERRS_ZH_HANS = {{
{fmt(terrs, 0)}}}

TERRS_ZH_HANT = {{
{fmt(terrs, 1)}}}
'''
    OUT.write_text(body, encoding="utf-8")
    print(f"{OUT.name}: {len(langs)} 语言 + {len(scripts)} 文字 + "
          f"{len(terrs)} 地区，对拍 {len(samples)}×2 tag 全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
