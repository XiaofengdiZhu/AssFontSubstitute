"""绿色软件打包入口：pyside6-deploy 要求 .py 文件入口，包内 -m 喂不了。

freeze_support 必须最先展开——渲染进程池/索引解析池的 frozen 子进程会
重执行本 exe，不展开则递归拉起 GUI（gui/__main__.py 同款注释）。"""
import multiprocessing
import sys

from ass_font_substitute.gui.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
