from __future__ import annotations

import json
import math
import os
import re
import threading
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtCore import Signal

from ass_font_substitute.core.coverage.matcher import COV_THRESHOLD
from ass_font_substitute.core.similarity.search import MIN_SIM
from ass_font_substitute.core.similarity.search import SAMPLE_TEXT
from ass_font_substitute.core.similarity.search import SIZE_RANGE

# 界面滑杆边界（spec §10）：覆盖率 80–100（2026-10 下限 90 收到 80）；
# 相似率 50–100；大小区间 50–150（200% 过宽无实用场景，2026-10 收到
# 150）。存储一律小数，页面按百分数换算
COV_BOUNDS = (0.80, 1.00, 0.01)
SIM_BOUNDS = (0.50, 1.00, 0.05)
SIZE_BOUNDS = (0.50, 1.50, 0.01)

DEFAULTS: dict = {
    "font_dirs": [],
    "cov_threshold": COV_THRESHOLD,
    "min_sim": MIN_SIM,
    "size_min": SIZE_RANGE[0],
    "size_max": SIZE_RANGE[1],
    "n_pause": 5,
    "max_threads": max(1, os.cpu_count() or 4),   # 建索引并行解析进程数
    "save_suffix": ".substituted.ass",
    "overwrite_default": False,
    "sample_text": SAMPLE_TEXT,
    "cache_path": "",                              # 空 = paths.default_cache_path()
    "last_dirs": {},                               # 各对话框上次目录 {scope: 路径}
    "preview_history": [],                         # 预览文字历史（新→旧，最多 10 条；预览页/字幕页共享）
    "preview_mono": False,                         # 预览强制等宽（全角 1em/半角 0.5em 统一格宽，多行逐字对位）
    "language": "zh-Hans",                         # 界面语言（国际化待接入，暂存储值）
    "theme": "auto",                               # auto/light/dark
    "theme_color": "",                             # 强调色 #rrggbb；空=跟随系统
    "bg_effect": "mica",                           # 窗口材质；Win10 下 Mica 类自动退化为实色
    "win_geometry": None,                          # 上次关闭窗口 {x,y,w,h:int, max:bool}；None=没记过
}

_BOOL_KEYS = {"overwrite_default", "preview_mono"}
_LIST_KEYS = {"font_dirs"}
_INT_KEYS = {"n_pause", "max_threads"}
_FLOAT_KEYS = {"cov_threshold", "min_sim", "size_min", "size_max"}
# 预览文字历史上限（预览页/字幕页共享组件与载入裁剪共用）
MAX_PREVIEW_HISTORY = 10
# 枚举键：取值必须落在集合内，存量越界值按默认处理
_ENUM_KEYS = {
    "language": {"zh-Hans", "zh-Hant"},
    "theme": {"auto", "light", "dark"},
    "bg_effect": {"mica", "mica_alt", "acrylic", "aero", "none"},
}
_SUFFIX_RE = re.compile(r"^\..+$")
# 强调色：空串=跟随系统；非空必须 #rrggbb（ColorDialog c.name() 出货格式）
_THEME_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _is_win_geometry(v) -> bool:
    """win_geometry 存档形状：None 或 {x,y,w,h:int, max:bool}。整数限
    ±2^31：JSON 任意精度整数越界会让 QRect 构造抛 OverflowError，ctor
    崩=每次启动都崩（违背「损坏绝不崩 GUI」），界内荒谬值由恢复分支
    钳制/弃用兜底。"""
    return v is None or (
        isinstance(v, dict)
        and isinstance(v.get("max"), bool)
        and all(isinstance(v.get(f), int) and not isinstance(v.get(f), bool)
                and -(1 << 31) < v.get(f) < (1 << 31)
                for f in ("x", "y", "w", "h")))


class Settings(QObject):
    """JSON 设置。get/set 同步落盘（tmp + os.replace 原子写）；
    损坏/含未知键/类型不符的存量数据按默认处理，绝不崩 GUI。"""

    #: (key, value) 落盘后、且值有变才发；reload/启动装载不发。
    #: worker 线程 set（cache_move）时 Qt 自动按接收者线程排队投递
    valueChanged = Signal(str, object)

    def __init__(self, path: Path | None = None):
        super().__init__()
        self.path = Path(path) if path is not None else None
        # worker 线程（cache_move 落 cache_path）与 UI 线程并发 set：
        # 互斥保住「tmp 固定名原子写」与 dumps 期间字典不被改
        self._lock = threading.RLock()          # set→save 嵌套，须可重入
        self.reload()

    def _file(self) -> Path:
        if self.path is not None:
            return self.path
        from ass_font_substitute.gui.paths import settings_path
        return settings_path()

    def reload(self) -> None:
        with self._lock:
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
                elif k in _INT_KEYS and isinstance(v, int) \
                        and not isinstance(v, bool):
                    self._data[k] = v
                elif k in _FLOAT_KEYS and isinstance(v, (int, float)) \
                        and not isinstance(v, bool) and math.isfinite(v):
                    self._data[k] = float(v)    # NaN/Inf 会崩 round()，拒收
                elif k == "sample_text" and isinstance(v, str) and v.strip():
                    self._data[k] = v
                elif k == "save_suffix" and isinstance(v, str) \
                        and _SUFFIX_RE.match(v):
                    self._data[k] = v           # 存量后缀复验，与 set 同规则
                elif k == "theme_color" and isinstance(v, str) \
                        and (v == "" or _THEME_COLOR_RE.match(v)):
                    self._data[k] = v           # 空串=跟随系统，存量复验同规则
                elif (k in ("cache_path",) and isinstance(v, str)) or (k in _ENUM_KEYS and v in _ENUM_KEYS[k]):
                    self._data[k] = v
                elif k == "last_dirs" and isinstance(v, dict) \
                        and all(isinstance(kk, str) and isinstance(vv, str)
                                for kk, vv in v.items()):
                    self._data[k] = dict(v)        # 深一层不做路径校验，种子时兜底
                elif k == "preview_history" and isinstance(v, list) \
                        and all(isinstance(x, str) and x.strip() for x in v):
                    self._data[k] = v[:MAX_PREVIEW_HISTORY]   # 新→旧，最多 10 条
                elif k == "win_geometry" and _is_win_geometry(v):
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
        if key == "theme_color" and not (value == ""
                                         or (isinstance(value, str) and _THEME_COLOR_RE.match(value))):
            raise ValueError("强调色必须是 #rrggbb 十六进制色，留空跟随系统")
        if key in _FLOAT_KEYS and isinstance(value, float) \
                and not math.isfinite(value):
            raise ValueError(f"{key} 必须是有限数值")
        if key in _ENUM_KEYS and value not in _ENUM_KEYS[key]:
            raise ValueError(f"{key} 必须是 {'/'.join(sorted(_ENUM_KEYS[key]))} 之一")
        if key == "win_geometry" and not _is_win_geometry(value):
            raise ValueError("win_geometry 必须是 None 或 "
                             "{x,y,w,h:int, max:bool} 字典")
        with self._lock:
            old = self._data.get(key)
            self._data[key] = value
            self.save()
        if old != value:                        # emit 在锁外：槽内再入 set 不死锁
            self.valueChanged.emit(key, value)

    def save(self) -> None:
        with self._lock:
            f = self._file()
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_name(f.name + ".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2),
                           "utf-8")
            os.replace(tmp, f)
