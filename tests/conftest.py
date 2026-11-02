import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Qt 测试统一 offscreen：此前个别测试文件自建 qapp 却未设，全量跑靠别的
# 文件在导入阶段 setdefault 才碰巧生效，单独跑会起真实 windows 平台插件
# （审查三轮 P2）
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(autouse=True)
def _isolated_data(tmp_path, monkeypatch):
    # 数据目录统一隔离（AGENTS.md 约定）：绕过此隔离的测试会对真实
    # Data/cache.db 跑建库/迁移、与运行实例抢锁（test_gui_workers 两条
    # 实测中招，审查三轮 P1）；各测试仍可再 setenv 覆盖
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
