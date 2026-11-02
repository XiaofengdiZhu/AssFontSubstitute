"""绿色软件打包入口：pyside6-deploy 要求 .py 文件入口，包内 -m 喂不了。

freeze_support 必须最先展开——渲染进程池/索引解析池的 frozen 子进程会
重执行本 exe，不展开则递归拉起 GUI（gui/__main__.py 同款注释）。gui.app
的 import 挪进 __main__ 块、freeze_support 之后：python main.py 形态下
spawn 子进程以 __mp_main__ 重执行本文件，顶层 import（PySide6+qfw 约
1.2s）会在每个子进程白付（审查三轮 P2）；-m 形态 multiprocessing 跳过
__main__ 重执行，本就不受影响。"""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from ass_font_substitute.gui.app import main
    sys.exit(main())
