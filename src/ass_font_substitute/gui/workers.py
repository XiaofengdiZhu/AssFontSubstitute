from __future__ import annotations

import queue
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QThread, Signal

from ass_font_substitute.gui.settings_store import Settings


class CoreWorker(QThread):
    """串行核心工作者：独占 Cache/LibraryIndex（core Cache 单连接线程约束）。
    submit(tag, fn, *args, **kw) 入队；fn(ctx, *args, **kw) 的返回值经
    result 信号回 UI 线程，异常经 failed 信号。任务严格按提交顺序执行。"""

    result = Signal(object, object)   # (tag, payload)
    failed = Signal(object, str)      # (tag, "ExcType: msg")

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._q: queue.Queue = queue.Queue()
        self._stop = False

    def run(self):
        from ass_font_substitute.core.similarity.cache import Cache
        from ass_font_substitute.gui.paths import default_cache_path
        p = self._settings.get("cache_path") or str(default_cache_path())
        ctx = SimpleNamespace(settings=self._settings, cache=Cache(Path(p)),
                              snapshot=None, lib=None, lib_dirs=None)
        while not self._stop:
            item = self._q.get()
            if item is None:
                break
            tag, fn, args, kw = item
            try:
                self.result.emit(tag, fn(ctx, *args, **kw))
            except Exception as e:  # noqa: BLE001  单任务失败不拖垮工作者
                self.failed.emit(tag, f"{type(e).__name__}: {e}")

    def submit(self, tag, fn, *args, **kw):
        self._q.put((tag, fn, args, kw))

    def shutdown(self):
        self._stop = True
        self._q.put(None)
