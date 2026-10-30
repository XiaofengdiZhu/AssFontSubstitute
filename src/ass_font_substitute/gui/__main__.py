import multiprocessing
import sys

from ass_font_substitute.gui.app import main

if __name__ == "__main__":
    # Windows spawn 的 frozen 子进程会重执行 exe：打包形态（pyside6-deploy/
    # PyInstaller）必须最先展开 freeze_support，否则子进程递归拉起 GUI。
    # 非冻结形态为 no-op。渲染进程池（jobs.ensure_render_pool）与索引解析
    # 池（fontindex/store.py）都依赖 multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
