from __future__ import annotations

import queue
from types import SimpleNamespace

from PySide6.QtCore import QThread
from PySide6.QtCore import Signal

from ass_font_substitute.gui.settings_store import Settings


class JobHandle:
    """submit 的取消句柄：置 cancelled 后任务仍在队列里，worker 弹出时
    跳过不执行。用于预览渲染这类「过期即废物」的任务——字体列表快速
    滚动会积压大量已滚出视野的渲染，不取消则可见行排在几百个废任务后。"""

    __slots__ = ("cancelled",)

    def __init__(self):
        self.cancelled = False


class CoreWorker(QThread):
    """串行核心工作者：独占 Cache/LibraryIndex（core Cache 单连接线程约束）。
    submit(tag, fn, *args, **kw) 入队并返回 JobHandle；fn(ctx, *args, **kw)
    的返回值经 result 信号回 UI 线程，异常经 failed 信号。任务按提交顺序
    执行，已取消的弹出即跳过（开始执行后不可中断）。"""

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
        cache = None
        try:
            cache = Cache(default_cache_path())
        except Exception as e:
            self.failed.emit("worker:init", f"{type(e).__name__}: {e}")
        ctx = SimpleNamespace(settings=self._settings, cache=cache,
                              snapshot=None, lib=None, lib_key=None,
                              pool=None)
        while not self._stop:
            item = self._q.get()
            if item is None:
                break
            handle, tag, fn, args, kw = item
            if handle.cancelled:
                continue
            try:
                self.result.emit(tag, fn(ctx, *args, **kw))
            except Exception as e:
                self.failed.emit(tag, f"{type(e).__name__}: {e}")
        if cache is not None:
            cache.close()   # 记账落库+干净关连接（WAL 落盘清旁车）
        from ass_font_substitute.gui.jobs import shutdown_ctx
        shutdown_ctx(ctx)   # 渲染进程池收尾

    def submit(self, tag, fn, *args, **kw) -> JobHandle:
        handle = JobHandle()
        if self._stop:
            handle.cancelled = True   # 置停后窗口仍可交互：入队即弃（防静默忙态）
            return handle
        self._q.put((handle, tag, fn, args, kw))
        return handle

    def shutdown(self):
        """置停 + 排空：先积压任务逐个置 cancelled 再放哨兵——哨兵排在
        队尾时退出要等全部积压跑完（大库下 wait 必超时 → 进程带活线程
        退出硬崩）。在途单个任务仍会跑完（开始执行后不可中断），由
        closeEvent 的 wait 兜底。幂等：closeEvent 与测试清理会各自调用，
        残留的哨兵 None 进快照会让 item[0] 下标炸。"""
        if self._stop:
            return
        self._stop = True
        for item in list(self._q.queue):
            item[0].cancelled = True
        self._q.put(None)


class IndexWorker(QThread):
    """逐目录跑 update_dir；progress 回调转发为信号，cancel() 经标志
    同时作用于回调返回值（停止当前目录剩余文件）与目录间循环。"""

    progress = Signal(str, int, int, object)     # (dir, done, total, UpdateResult)
    finished_dir = Signal(str, object)           # (dir, UpdateResult)
    failed = Signal(str, str)

    def __init__(self, dirs, max_threads: int = 1,
                 force: set[str] | None = None, parent=None):
        super().__init__(parent)
        self._dirs = list(dirs)
        self._max_threads = max(1, int(max_threads))
        self._force = set(force or ())     # 强制重建的目录（清库全量重解析）
        self._cancel = False
        self._skip: set[str] = set()

    def cancel(self):
        self._cancel = True

    def cancel_dir(self, d: str):
        """单停一个目录：在途的中断剩余文件，排队的到队即跳过；
        其余目录不受影响（工具栏「取消更新」才是整体取消）。"""
        self._skip.add(d)

    def run(self):
        from ass_font_substitute.core.fontindex.store import UpdateResult
        from ass_font_substitute.gui.jobs import update_dir
        for d in self._dirs:
            if self._cancel:
                break
            if d in self._skip:
                # 排队中被单停：回发取消结果让行按钮即刻脱「更新中」
                self.finished_dir.emit(d, UpdateResult(cancelled=True))
                continue
            try:
                r = update_dir(
                    d, progress=lambda done, total, res, d=d:
                    self._on_progress(d, done, total, res),
                    max_threads=self._max_threads,
                    force=d in self._force)
                self.finished_dir.emit(d, r)
            except Exception as e:
                self.failed.emit(d, f"{type(e).__name__}: {e}")

    def _on_progress(self, d, done, total, res) -> bool:
        if self._cancel or d in self._skip:
            return False
        self.progress.emit(d, done, total, res)
        return True
