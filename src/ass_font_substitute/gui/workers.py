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
        try:
            cache = Cache(Path(p))
        except Exception as e:  # noqa: BLE001  启动失败可观察：线程存活，缓存类任务逐个 failed
            cache = None
            self.failed.emit("worker:init", f"{type(e).__name__}: {e}")
        ctx = SimpleNamespace(settings=self._settings, cache=cache,
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


class IndexWorker(QThread):
    """逐目录跑 update_dir；progress 回调转发为信号，cancel() 经标志
    同时作用于回调返回值（停止当前目录剩余文件）与目录间循环。"""

    progress = Signal(str, int, int, object)     # (dir, done, total, UpdateResult)
    finished_dir = Signal(str, object)           # (dir, UpdateResult)
    failed = Signal(str, str)

    def __init__(self, dirs, parent=None):
        super().__init__(parent)
        self._dirs = list(dirs)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        from ass_font_substitute.gui.jobs import update_dir
        for d in self._dirs:
            if self._cancel:
                break
            try:
                r = update_dir(
                    d, progress=lambda done, total, res, d=d:
                    self._on_progress(d, done, total, res))
                self.finished_dir.emit(d, r)
            except Exception as e:  # noqa: BLE001
                self.failed.emit(d, f"{type(e).__name__}: {e}")

    def _on_progress(self, d, done, total, res) -> bool:
        if self._cancel:
            return False
        self.progress.emit(d, done, total, res)
        return True
