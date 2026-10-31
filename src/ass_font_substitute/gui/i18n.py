"""界面文案翻译装载：app 自身文案走 Qt tr/.qm 链路（pyside6-lupdate →
opencc s2twp → lrelease，见 scripts/gen_translations.py；源语言=简体，
故 zh-Hans 无需装载），qfw 内置组件文案走 FluentTranslator。
两路 translator 都必须在任何窗口创建前安装——main() 启动早期调用，
运行中不热切（切语言提示重启生效）。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QLocale
from PySide6.QtCore import QTranslator
from qfluentwidgets import FluentTranslator

LANG_ZH_HANS = "zh-Hans"
LANG_ZH_HANT = "zh-Hant"

# 繁体拍板：台湾繁體（与译文 opencc s2twp 同源）；简体用 China
_LOCALES = {
    LANG_ZH_HANS: QLocale(QLocale.Chinese, QLocale.China),
    LANG_ZH_HANT: QLocale(QLocale.Chinese, QLocale.Taiwan),
}

_QM_NAME = "afs_zh-Hant.qm"


def translations_dir() -> Path:
    return Path(__file__).resolve().parent / "translations"


def load_app_translator(lang: str) -> QTranslator | None:
    """app 自身文案翻译器；源语言（简体）或装载失败返回 None——
    调用方按无翻译处理（tr 回落源文案），不视为错误。"""
    if lang != LANG_ZH_HANT:
        return None
    t = QTranslator()
    if not t.load(str(translations_dir() / _QM_NAME)):
        return None
    return t


def fluent_translator(lang: str) -> FluentTranslator:
    """qfw 内置组件翻译器：跟随设置而非系统语言（简体环境跑系统繁体
    不得漂成繁体，反之亦然）。未知语言值按简体兜底。"""
    return FluentTranslator(_LOCALES.get(lang, _LOCALES[LANG_ZH_HANS]))
