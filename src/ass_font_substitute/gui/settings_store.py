from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ass_font_substitute.core.coverage.matcher import COV_THRESHOLD
from ass_font_substitute.core.similarity.search import MIN_SIM, SAMPLE_TEXT, SIZE_RANGE

# 界面 stepper 边界（spec §10）：覆盖率 90–100 步长 1；相似率 50–100 步长 5；
# 大小区间 50–200 步长 1。存储一律小数，页面按百分数换算
COV_BOUNDS = (0.90, 1.00, 0.01)
SIM_BOUNDS = (0.50, 1.00, 0.05)
SIZE_BOUNDS = (0.50, 2.00, 0.01)

DEFAULTS: dict = {
    "font_dirs": [],
    "cov_threshold": COV_THRESHOLD,
    "min_sim": MIN_SIM,
    "size_min": SIZE_RANGE[0],
    "size_max": SIZE_RANGE[1],
    "n_pause": 5,
    "max_threads": max(1, os.cpu_count() or 4),   # 挂账：并行解析落地前仅存储
    "save_suffix": ".substituted.ass",
    "overwrite_default": False,
    "sample_text": SAMPLE_TEXT,
    "cache_path": "",                              # 空 = paths.default_cache_path()
    "last_dir": "",                                # 上次文件/目录对话框的目录
}

_BOOL_KEYS = {"overwrite_default"}
_LIST_KEYS = {"font_dirs"}
_INT_KEYS = {"n_pause", "max_threads"}
_FLOAT_KEYS = {"cov_threshold", "min_sim", "size_min", "size_max"}
_SUFFIX_RE = re.compile(r"^\..+$")


class Settings:
    """JSON 设置。get/set 同步落盘（tmp + os.replace 原子写）；
    损坏/含未知键/类型不符的存量数据按默认处理，绝不崩 GUI。"""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else None
        self.reload()

    def _file(self) -> Path:
        if self.path is not None:
            return self.path
        from ass_font_substitute.gui.paths import settings_path
        return settings_path()

    def reload(self) -> None:
        self._data = dict(DEFAULTS)
        f = self._file()
        if not f.exists():
            return
        try:
            stored = json.loads(f.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(stored, dict):    # 非映射视为损坏
            return
        dirty = False
        for k, v in stored.items():
            if k in _BOOL_KEYS and isinstance(v, bool):
                self._data[k] = v
            elif k in _LIST_KEYS and isinstance(v, list):
                self._data[k] = [str(x) for x in v]
            elif k in _INT_KEYS and isinstance(v, int) and not isinstance(v, bool):
                self._data[k] = v
            elif k in _FLOAT_KEYS and isinstance(v, (int, float)) \
                    and not isinstance(v, bool):
                self._data[k] = float(v)
            elif k == "sample_text" and isinstance(v, str) and v.strip():
                self._data[k] = v
            elif k in ("save_suffix", "cache_path", "last_dir") \
                    and isinstance(v, str):
                self._data[k] = v
            else:
                dirty = True                # 未知键或类型不符
        if dirty:
            self.save()                     # 回写净化后的存量设置

    def get(self, key: str):
        return self._data[key]

    def set(self, key: str, value) -> None:
        if key == "sample_text" and (not isinstance(value, str) or not value.strip()):
            raise ValueError("对比文字不能为空")
        if key == "save_suffix" and not (isinstance(value, str) and _SUFFIX_RE.match(value)):
            raise ValueError("另存后缀必须以点开头且非空，如 .substituted.ass")
        self._data[key] = value
        self.save()

    def save(self) -> None:
        f = self._file()
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, f)
