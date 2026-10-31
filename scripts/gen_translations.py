"""界面文案翻译管线：pyside6-lupdate 提取 → opencc s2twp 自动填译 →
pyside6-lrelease 编译 .qm。

    uv run python scripts/gen_translations.py

源语言 = 简体（代码里的中文即源文案），目标 = 台湾繁體（s2twp 带
台湾惯用词：字体→字型、文件→檔案 等）。可重复跑：lupdate 增量合并，
已有且非 unfinished 的译文保留——手工改过的条目（去掉 unfinished
标记）不会被自动译文覆盖；源文案变了的条目由 lupdate 重标 unfinished
后重填。-no-obsolete 丢弃源码里已消失的条目；-locations none 免行号
漂移造成 .ts 无谓 diff。
"""
from __future__ import annotations

import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "src" / "ass_font_substitute" / "gui"
TS = GUI / "translations" / "afs_zh-Hant.ts"
QM = TS.with_suffix(".qm")

# s2twp 人工纠偏（源文案 → 译文，强制覆盖自动译文；按整条源串匹配）：
# s2twp 按词组转，「字体系」命中「体系→體系」与独立「字体→字型」不一致；
# 「同文件」不成词漏转；其余为大陆腔改台湾惯用（自訂/取得/回報/目前/
# 應用程式/控制項）。源串从代码消失时 test_gui_i18n 守卫报红
_OVERRIDES = {
    "字体系": "字型系",
    "版式字体系": "版式字型系",
    "样式映射字体系": "樣式對映字型系",
    "同字体系其他字体": "同字型系其他字型",
    "同文件其他字体": "同檔案其他字型",
    "自定义…": "自訂…",
    "界面控件的强调色；跟随系统取 Windows 主题色，或自定义":
        "介面控制項的強調色；跟隨系統取 Windows 主題色，或自訂",
    "获取更新 / 反馈问题（GitHub）": "取得更新 / 回報問題（GitHub）",
    "跟随系统（当前 {0}）": "跟隨系統（目前 {0}）",
    "确认移除 {0} 个字幕文件？选择的字体将记忆，重新添加即恢复。":
        "確認移除 {0} 個字幕檔案？選擇的字型會被記住，重新新增即恢復。",
    "设置界面语言（重启应用后生效）": "設定介面語言（重新啟動應用程式後生效）",
    "重启应用后生效": "重新啟動應用程式後生效",
}


def _tool(name: str) -> str:
    exe = Path(sys.executable).parent / (name + (".exe" if sys.platform == "win32" else ""))
    return str(exe)


def sources() -> list[str]:
    return sorted(str(p) for p in GUI.rglob("*.py") if "__pycache__" not in p.parts)


def lupdate(ts: Path) -> str:
    """提取到 ts（已存在则增量合并）；返回工具输出。"""
    ts.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [_tool("pyside6-lupdate"), *sources(), "-no-obsolete",
         "-locations", "none", "-source-language", "zh_CN",
         "-target-language", "zh_TW", "-ts", str(ts)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise RuntimeError(f"lupdate 失败：\n{r.stdout}\n{r.stderr}")
    return r.stdout


def messages(ts: Path) -> set[tuple[str, str]]:
    """(context, source) 全集。"""
    root = ET.parse(ts).getroot()
    return {(c.findtext("name") or "", m.findtext("source") or "")
            for c in root.iter("context") for m in c.iter("message")}


def autofill(ts: Path) -> int:
    """unfinished/空译文条目填 s2twp 自动译文并去标记；_OVERRIDES 条目
    无论状态一律强制覆盖。返回改写数。"""
    import opencc
    cc = opencc.OpenCC("s2twp")
    tree = ET.parse(ts)
    n = 0
    for m in tree.getroot().iter("message"):
        src = m.findtext("source") or ""
        tr = m.find("translation")
        if tr is None:
            tr = ET.SubElement(m, "translation")
        if src in _OVERRIDES:
            if tr.text != _OVERRIDES[src] or tr.get("type"):
                tr.text = _OVERRIDES[src]
                tr.attrib.pop("type", None)
                n += 1
        elif tr.get("type") == "unfinished" or not (tr.text or "").strip():
            tr.text = cc.convert(src)
            tr.attrib.pop("type", None)
            n += 1
    ET.indent(tree, space="    ")
    with open(ts, "wb") as f:
        f.write(b'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE TS>\n')
        tree.write(f, encoding="utf-8", xml_declaration=False)
        f.write(b"\n")
    return n


def lrelease(ts: Path, qm: Path) -> str:
    r = subprocess.run([_tool("pyside6-lrelease"), str(ts), "-qm", str(qm)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode:
        raise RuntimeError(f"lrelease 失败：\n{r.stdout}\n{r.stderr}")
    return r.stdout


def main() -> int:
    print(lupdate(TS).strip())
    print(f"自动填译 {autofill(TS)} 条（opencc s2twp）")
    print(lrelease(TS, QM).strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
