import json
import os

import pytest

from ass_font_substitute.core.coverage.matcher import COV_THRESHOLD
from ass_font_substitute.core.similarity.search import MIN_SIM
from ass_font_substitute.core.similarity.search import SAMPLE_TEXT
from ass_font_substitute.core.similarity.search import SIZE_RANGE
from ass_font_substitute.gui.paths import appdata_dir
from ass_font_substitute.gui.paths import decisions_path
from ass_font_substitute.gui.paths import default_cache_path
from ass_font_substitute.gui.paths import settings_path
from ass_font_substitute.gui.settings_store import DEFAULTS
from ass_font_substitute.gui.settings_store import Settings


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_paths_under_data_dir(monkeypatch, tmp_path):
    # 绿色软件：数据目录随程序走（<运行目录>/Data），AFS_DATA_DIR 覆盖
    # 全部分辨（测试隔离用）
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
    assert appdata_dir() == tmp_path
    assert settings_path() == tmp_path / "settings.json"
    assert default_cache_path() == tmp_path / "cache.db"
    assert decisions_path() == tmp_path / "decisions.db"


def test_defaults_match_core_constants():
    assert DEFAULTS["cov_threshold"] == COV_THRESHOLD == 0.95
    assert DEFAULTS["min_sim"] == MIN_SIM == 0.85
    assert (DEFAULTS["size_min"], DEFAULTS["size_max"]) == SIZE_RANGE == (0.90, 1.10)
    assert DEFAULTS["sample_text"] == SAMPLE_TEXT
    assert DEFAULTS["n_pause"] == 5
    assert DEFAULTS["save_suffix"] == ".substituted.ass"
    assert DEFAULTS["overwrite_default"] is False
    assert DEFAULTS["preview_mono"] is False        # 强制等宽默认关
    assert DEFAULTS["max_threads"] >= 1
    # 界面滑杆值域：覆盖率下限 80（2026-10 从 90 放开）、大小上限 150
    from ass_font_substitute.gui.settings_store import COV_BOUNDS
    from ass_font_substitute.gui.settings_store import SIZE_BOUNDS
    assert COV_BOUNDS == (0.80, 1.00, 0.01)
    assert SIZE_BOUNDS == (0.50, 1.50, 0.01)


def test_roundtrip_and_missing_file(tmp_path):
    s = Settings(tmp_path / "s.json")
    assert s.get("n_pause") == 5                      # 无文件 → 全默认
    s.set("n_pause", 8)
    s.set("font_dirs", ["J:/fonts"])
    s2 = Settings(tmp_path / "s.json")
    assert s2.get("n_pause") == 8
    assert s2.get("font_dirs") == ["J:/fonts"]


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    f = tmp_path / "s.json"
    f.write_text("{broken", "utf-8")
    s = Settings(f)
    assert s.get("cov_threshold") == COV_THRESHOLD


def test_unknown_keys_and_bad_types_filtered(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"n_pause": "x", "future_key": 1,
                             "overwrite_default": "yes", "cov_threshold": 0.9,
                             "preview_mono": "on"}),
                 "utf-8")
    s = Settings(f)
    assert s.get("n_pause") == DEFAULTS["n_pause"]
    assert "future_key" not in json.loads(f.read_text("utf-8"))
    assert s.get("overwrite_default") is False
    assert s.get("cov_threshold") == 0.9
    assert s.get("preview_mono") is False


def test_empty_sample_text_rejected(tmp_path):
    s = Settings(tmp_path / "s.json")
    with pytest.raises(ValueError):
        s.set("sample_text", "   ")


def test_bad_suffix_rejected(tmp_path):
    s = Settings(tmp_path / "s.json")
    with pytest.raises(ValueError):
        s.set("save_suffix", "substituted")           # 必须以点开头
    s.set("save_suffix", ".fixed.ass")
    assert s.get("save_suffix") == ".fixed.ass"


def test_atomic_write_no_tmp_leftover(tmp_path):
    s = Settings(tmp_path / "s.json")
    s.set("n_pause", 3)
    assert not (tmp_path / "s.json.tmp").exists()


def test_win_geometry_roundtrip_and_validation(tmp_path):
    # None=没记过；合法存档 {x,y,w,h:int, max:bool} 落盘回读；
    # 坏形状 set 即拒；存量坏形状 reload 过滤回默认并净化重写
    f = tmp_path / "s.json"
    s = Settings(f)
    assert s.get("win_geometry") is None

    g = {"x": -8, "y": 0, "w": 1100, "h": 750, "max": False}   # 负坐标合法（副屏）
    s.set("win_geometry", g)
    assert Settings(f).get("win_geometry") == g

    for bad in ({"x": 1, "y": 2, "w": 3},                     # 缺键
                {"x": 1, "y": 2, "w": 3, "h": 4, "max": 1},   # max 非 bool
                {"x": 1.5, "y": 2, "w": 3, "h": 4, "max": True},
                {"x": 1 << 31, "y": 0, "w": 3, "h": 4, "max": True},  # 超 QRect 域
                "0,0,100x100"):
        with pytest.raises(ValueError):
            s.set("win_geometry", bad)

    f.write_text(json.dumps({"win_geometry": {"x": 0, "y": "a",
                                              "w": 100, "h": 100,
                                              "max": True}}), "utf-8")
    s2 = Settings(f)
    assert s2.get("win_geometry") is None
    assert json.loads(f.read_text("utf-8"))["win_geometry"] is None   # 净化重写


def test_last_dir_scoped_roundtrip_and_independence(tmp_path, monkeypatch, qapp):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from ass_font_substitute.gui import lastdir

    s = Settings(tmp_path / "s.json")
    assert s.get("last_dirs") == {}                 # 缺省空：不预置目录
    lib_dir, sub_dir = tmp_path / "fonts", tmp_path / "subs"
    lib_dir.mkdir()
    sub_dir.mkdir()

    seen = {}

    def fake_dir(parent, title, d):
        seen["d"] = d
        return str(lib_dir)

    monkeypatch.setattr(lastdir.QFileDialog, "getExistingDirectory",
                        lambda *a: fake_dir(*a))
    monkeypatch.setattr(lastdir.QFileDialog, "getOpenFileNames",
                        lambda parent, title, d, f: (
                            seen.__setitem__("files_d", d) or
                            ([str(sub_dir / "a.ass")], "")))

    # library scope：选定回记
    assert lastdir.pick_existing_dir(None, s, "选目录", "library") == str(lib_dir)
    assert s.get("last_dirs")["library"] == str(lib_dir)

    # subs scope：回记自己的目录，不碰 library
    assert lastdir.pick_open_files(None, s, "选文件", "f", "subs") ==         [str(sub_dir / "a.ass")]
    assert s.get("last_dirs") == {"library": str(lib_dir), "subs": str(sub_dir)}
    assert seen["files_d"] == ""                    # subs 首次：种子为空

    # 种子按 scope 取：library 再开落在 fonts，subs 落在 subs
    lastdir.pick_existing_dir(None, s, "选目录", "library")
    assert seen["d"] == str(lib_dir)
    lastdir.pick_open_files(None, s, "选文件", "f", "subs")
    assert seen["files_d"] == str(sub_dir)

    # 取消不改记忆
    monkeypatch.setattr(lastdir.QFileDialog, "getOpenFileNames",
                        lambda *a, **kw: ([], ""))
    lastdir.pick_open_files(None, s, "选文件", "f", "subs")
    assert s.get("last_dirs")["subs"] == str(sub_dir)

    # 记忆目录已删：种子回退为空，其他 scope 不受影响
    import shutil
    shutil.rmtree(sub_dir)
    monkeypatch.setattr(lastdir.QFileDialog, "getOpenFileNames",
                        lambda parent, title, d, f: (
                            seen.__setitem__("files_d2", d) or ([], "")))
    lastdir.pick_open_files(None, s, "选文件", "f", "subs")
    assert seen["files_d2"] == ""
    assert s.get("last_dirs")["library"] == str(lib_dir)


def test_last_dir_drive_root_gets_separator(tmp_path, monkeypatch, qapp):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from ass_font_substitute.gui import lastdir

    s = Settings(tmp_path / "s.json")
    drive = tmp_path.drive or "C:"
    # 真实对话框返回反斜杠路径：根目录文件的 dirname 已带分隔符
    monkeypatch.setattr(lastdir.QFileDialog, "getOpenFileNames",
                        lambda *a, **kw: ([f"{drive}\a.ass"], ""))
    lastdir.pick_open_files(None, s, "选文件", "f", "subs")
    assert s.get("last_dirs")["subs"] == drive + "\\"
    # 盘相对路径（dirname 剥成裸 "C:"）经 _remember 归一成根
    lastdir._remember(s, "library", drive)
    assert s.get("last_dirs")["library"] == drive + "\\"


def test_nan_and_bad_legacy_values_filtered(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"cov_threshold": float("nan"),
                             "save_suffix": "no-dot",
                             "min_sim": float("inf")}), "utf-8")
    s = Settings(f)
    assert s.get("cov_threshold") == DEFAULTS["cov_threshold"]   # NaN 拒收
    assert s.get("min_sim") == DEFAULTS["min_sim"]               # Inf 拒收
    assert s.get("save_suffix") == DEFAULTS["save_suffix"]       # 存量后缀复验
    with pytest.raises(ValueError):
        s.set("cov_threshold", float("nan"))
    with pytest.raises(ValueError):
        s.set("cov_threshold", float("inf"))


def test_settings_concurrent_set_threadsafe(tmp_path):
    import threading
    s = Settings(tmp_path / "s.json")
    errs = []

    def worker(i):
        try:
            for _ in range(30):
                s.set("n_pause", 1 + i)
        except Exception as e:                     # tmp 撞名/句柄互斥即在此暴露
            errs.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errs
    assert (tmp_path / "s.json.tmp").exists() is False


def test_wheel_filter_blocks_only_registered(qapp):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QSlider
    from PySide6.QtWidgets import QWidget
    from qfluentwidgets import FluentIcon as FIF

    from ass_font_substitute.gui.pages.settings_page import _WheelFreeCard

    card = _WheelFreeCard(FIF.FILTER, "t", "c")
    host = QWidget()
    slider = QSlider(host)
    other = QSlider(host)
    card.slider = slider
    wheel = QEvent(QEvent.Type.Wheel)
    assert card.eventFilter(slider, wheel) is True      # 注册过的控件被拦
    assert card.eventFilter(other, wheel) is False      # 未注册的放行
    assert card.eventFilter(slider, QEvent(QEvent.Type.None_)) is False


def test_wheel_block_filter(qapp):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QComboBox

    from ass_font_substitute.gui.widgets.wheel_block import WheelBlock

    f = WheelBlock()
    assert f.eventFilter(QComboBox(), QEvent(QEvent.Type.Wheel)) is True
    assert f.eventFilter(QComboBox(), QEvent(QEvent.Type.None_)) is False


def test_slider_spin_card_init_does_not_commit(qapp):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qfluentwidgets import FluentIcon as FIF

    from ass_font_substitute.gui.pages.settings_page import _SliderSpinCard

    calls = []
    _SliderSpinCard(FIF.FILTER, "t", "c", 90, 100, 1, 96, calls.append,
                    suffix="%")
    assert calls == []                              # 构造不提交，存量值不被改写


def test_slider_spin_card_compact_spin_link(qapp):
    # 滑杆+compact 数值框卡：spin↔slider 双向联动；spin 改值即时提交，
    # 滑杆拖拽松手才提交，无回环
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent
    from PySide6.QtCore import QPointF
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtGui import QPointingDevice
    from qfluentwidgets import FluentIcon as FIF

    from ass_font_substitute.gui.pages.settings_page import _SliderSpinCard

    calls = []
    card = _SliderSpinCard(FIF.FILTER, "t", "c", 90, 100, 1, 96, calls.append,
                           suffix="%")
    assert card.spin.value() == 96
    assert card.spin.text() == "96%"
    btn = card.spin.spinButton
    assert btn is not None and not btn.isHidden()     # 常显

    # 点击按上下半区分流：上半 stepUp、下半 stepDown（鼠标事件直投；
    # 带 globalPos 的构造才没被 Qt 6.12 标 deprecated）
    dev = QPointingDevice.primaryPointingDevice()

    def _click(y):
        pos = QPointF(btn.width() / 2, y)
        press = QMouseEvent(QEvent.Type.MouseButtonPress, pos, pos,
                            Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier, dev)
        release = QMouseEvent(QEvent.Type.MouseButtonRelease, pos, pos,
                              Qt.MouseButton.LeftButton,
                              Qt.MouseButton.LeftButton,
                              Qt.KeyboardModifier.NoModifier, dev)
        qapp.sendEvent(btn, press)
        qapp.sendEvent(btn, release)

    _click(8)
    assert card.spin.value() == 97 and calls == [97]
    _click(25)
    assert card.spin.value() == 96 and calls == [97, 96]
    # spin 改值 → 滑杆跟随 + 即时提交一次（无回环）
    card.spin.setValue(98)
    assert card.slider.value() == 98
    assert calls == [97, 96, 98]
    # 键盘/程序化改滑杆（无拖拽）→ 即时提交
    card.slider.setValue(92)
    assert card.spin.value() == 92
    assert card.spin.text() == "92%"
    assert calls == [97, 96, 98, 92]
    # 真实拖拽路径（press→改→release）：拖拽中只刷框，松手统一提交
    card.slider.sliderPressed.emit()
    card.slider.setValue(90)
    assert card.spin.value() == 90
    assert calls == [97, 96, 98, 92]
    card.slider.sliderReleased.emit()
    assert calls == [97, 96, 98, 92, 90]
    # 框宽刚好容下 "100%"：比该文字宽一点，远小于旧固定 90 时代的富余
    assert card.spin.width() <= card.spin.fontMetrics().horizontalAdvance(
        "100%") + 38
    card.spin.deleteLater()


def test_slider_spin_card_integer_cards(qapp):
    # 无后缀整数卡（暂停数/线程数）：初值回读、框改值即时提交、滑杆
    # 松手提交、框宽容三位数
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qfluentwidgets import FluentIcon as FIF

    from ass_font_substitute.gui.pages.settings_page import _SliderSpinCard

    calls = []
    card = _SliderSpinCard(FIF.PAUSE, "t", "c", 1, 10, 1, 5, calls.append)
    assert (card.spin.value(), card.spin.text()) == (5, "5")
    assert (card.slider.minimum(), card.slider.maximum()) == (1, 10)
    # 框宽按三位数模板：容 "000" 且不被 "100" 顶爆
    adv = card.spin.fontMetrics().horizontalAdvance("000")
    assert card.spin.width() == adv + 38
    # spin 改值即时提交；键盘/程序化改滑杆即时提交；拖拽（press→改）不提交
    card.spin.setValue(3)
    assert card.slider.value() == 3 and calls == [3]
    card.slider.setValue(8)
    assert calls == [3, 8]
    card.slider.sliderPressed.emit()
    card.slider.setValue(2)
    assert calls == [3, 8]
    card.slider.sliderReleased.emit()
    assert calls == [3, 8, 2]
    card.spin.deleteLater()


def test_size_card_range_slider(qapp, tmp_path):
    # 大小区间卡：双滑块+双 compact 数值框——初值回读、拖动落盘+框同步、
    # 框改值即时落盘
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtCore import Signal

    from ass_font_substitute.gui.pages.settings_page import SettingsPage
    from ass_font_substitute.gui.settings_store import Settings

    class _FakeWorker(QObject):
        result = Signal(object, object)
        failed = Signal(object, str)

        def submit(self, *a):
            pass

    s = Settings(tmp_path / "settings.json")
    s.set("size_min", 0.9)
    s.set("size_max", 1.1)
    page = SettingsPage(s, _FakeWorker())
    assert page.range_slider.values() == (90, 110)
    assert (page.spin.text(), page.spin_max.text()) == ("90%", "110%")
    # 值域上限 150：越界钳回，框取钳后值（存量 size_max 越界不矛盾）
    page.range_slider.setValues(80, 999)
    assert page.range_slider.values() == (80, 150)
    assert (page.spin.text(), page.spin_max.text()) == ("80%", "150%")
    # 拖拽中只刷框不落盘（每 move 全量 JSON 写太浪费），松手统一落
    assert (s.get("size_min"), s.get("size_max")) == (0.90, 1.10)
    page.range_slider.sliderReleased.emit()
    assert (s.get("size_min"), s.get("size_max")) == (0.80, 1.50)
    # 框改值 → 滑杆钳序 + 即时落盘；交叉输入（下限>上限）被排序
    page.spin.setValue(180)
    page.spin_max.setValue(60)
    assert page.range_slider.values() == (60, 150)
    assert (s.get("size_min"), s.get("size_max")) == (0.60, 1.50)
    # 滚轮拦截：框属性补挂到 card 后 _WheelFreeCard 名单才命中
    from PySide6.QtCore import QEvent
    wheel = QEvent(QEvent.Type.Wheel)
    assert page.size_card.eventFilter(page.spin, wheel) is True
    assert page.size_card.eventFilter(page.spin_max, wheel) is True
    # 框定宽：数值位数变化不顶滑块挪位
    assert page.spin.minimumWidth() == page.spin.maximumWidth() > 0
    page.deleteLater()


def test_general_group_language_theme_bg(qapp, tmp_path, monkeypatch):
    # 常规组三卡：初值、改动落盘并发信号、繁体模式选项名换正體中文
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
    from PySide6.QtCore import QObject
    from PySide6.QtCore import Signal

    from ass_font_substitute.gui.pages.settings_page import SettingsPage
    from ass_font_substitute.gui.settings_store import Settings

    class _FakeWorker(QObject):
        result = Signal(object, object)
        failed = Signal(object, str)

        def __init__(self):
            super().__init__()
            self.submits = []

        def submit(self, tag, fn, *a, **k):
            self.submits.append((tag, fn, a, k))

    s = Settings(tmp_path / "settings.json")
    page = SettingsPage(s, _FakeWorker())
    assert page.lang_combo.currentText() == "简体中文"
    assert page.theme_combo.currentText() == "跟随系统设置"
    assert page.bg_combo.currentText() == "Mica"

    themes, effects = [], []
    s.valueChanged.connect(lambda k, v: themes.append(v) if k == "theme"
                           else effects.append(v) if k == "bg_effect" else None)
    page.theme_combo.setCurrentIndex(2)           # 深色
    page.bg_combo.setCurrentIndex(4)              # 无
    assert themes == ["dark"] and effects == ["none"]
    assert s.get("theme") == "dark" and s.get("bg_effect") == "none"

    page.lang_combo.setCurrentIndex(1)
    assert s.get("language") == "zh-Hant"
    assert [page.lang_combo.itemText(i)
            for i in range(page.lang_combo.count())] == ["简体中文", "正體中文"]
    assert page.lang_combo.currentIndex() == 1
    page.lang_combo.setCurrentIndex(0)
    assert s.get("language") == "zh-Hans"
    assert [page.lang_combo.itemText(i)
            for i in range(page.lang_combo.count())] == ["简体中文", "繁体中文"]
    page.deleteLater()


def test_enum_keys_roundtrip_and_reject(tmp_path):
    # 语言/主题/窗口材质：枚举键合法值可回读，非法值 set 拒收
    s = Settings(tmp_path / "s.json")
    assert (s.get("language"), s.get("theme"),
            s.get("bg_effect")) == ("zh-Hans", "auto", "mica")
    s.set("language", "zh-Hant")
    s.set("theme", "dark")
    s.set("bg_effect", "acrylic")
    s2 = Settings(tmp_path / "s.json")
    assert (s2.get("language"), s2.get("theme"),
            s2.get("bg_effect")) == ("zh-Hant", "dark", "acrylic")
    for key, bad in (("language", "en"), ("theme", "blue"),
                     ("bg_effect", "glass")):
        with pytest.raises(ValueError):
            s.set(key, bad)


def test_enum_keys_out_of_range_fall_back(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"language": "en", "theme": "blue",
                             "bg_effect": 3}), "utf-8")
    s = Settings(f)
    assert (s.get("language"), s.get("theme"),
            s.get("bg_effect")) == ("zh-Hans", "auto", "mica")


def test_value_changed_signal_semantics(qapp, tmp_path):
    # set 新值发 (key, value) 且 emit 前已落盘；同值不发；reload 装载不发；
    # set 抛 ValueError（校验拒收）不发
    import json as _json

    s = Settings(tmp_path / "s.json")
    got = []
    s.valueChanged.connect(lambda k, v: got.append((k, v, _json.loads(
        (tmp_path / "s.json").read_text("utf-8"))["theme"])))
    s.set("theme", "dark")
    assert got == [("theme", "dark", "dark")]     # 第三元：emit 时文件已更新
    s.set("theme", "dark")
    assert got == [("theme", "dark", "dark")]     # 同值不发
    s2 = Settings(tmp_path / "s.json")            # 装载已有值不发
    s2.valueChanged.connect(lambda k, v: got.append((k, v, None)))
    assert got == [("theme", "dark", "dark")]
    with pytest.raises(ValueError):
        s.set("theme", "blue")
    assert got == [("theme", "dark", "dark")]


def test_value_changed_cross_thread_queued(qapp, tmp_path):
    # worker 线程 set：接收者在主线程，经 Qt 队列投递，
    # processEvents 后可见——绝不直接在发射线程执行槽
    import threading

    s = Settings(tmp_path / "s.json")
    got = []
    s.valueChanged.connect(lambda k, v: got.append((k, v, threading.current_thread())))
    t = threading.Thread(target=lambda: s.set("n_pause", 7))
    t.start()
    t.join()
    assert not got                                # 发射线程≠接收线程：未直接执行
    qapp.processEvents()
    assert len(got) == 1
    k, v, th = got[0]
    assert (k, v) == ("n_pause", 7)
    assert th is threading.main_thread()          # 槽跑在接收者线程


def test_theme_color_store_roundtrip_and_reject(tmp_path):
    # 强调色：空串=跟随系统；合法 #rrggbb 落盘回读；非法拒收；存量坏值回落
    import json as _json

    s = Settings(tmp_path / "s.json")
    assert s.get("theme_color") == ""
    s.set("theme_color", "#0065d5")
    s2 = Settings(tmp_path / "s.json")
    assert s2.get("theme_color") == "#0065d5"
    s.set("theme_color", "")                      # 回跟随系统
    assert s.get("theme_color") == ""
    for bad in ("red", "#12345", "#0065d5ff", "0065d5", None):
        with pytest.raises(ValueError):
            s.set("theme_color", bad)
    f = tmp_path / "bad.json"
    f.write_text(_json.dumps({"theme_color": "hotpink"}), "utf-8")
    assert Settings(f).get("theme_color") == ""


def test_accent_card_follow_custom_and_dialog(qapp, tmp_path, monkeypatch):
    # 强调色卡：存量自定义→选项「自定义…」+内容显色值；切跟随落空串；
    # 自定义弹 fluent 取色器，accept 才落 store（含同色确认钉住）；
    # 取消回退「跟随系统」
    from PySide6.QtCore import QObject
    from PySide6.QtCore import Signal
    from PySide6.QtGui import QColor

    import ass_font_substitute.gui.pages.settings_page as sp
    from ass_font_substitute.gui.pages.settings_page import SettingsPage

    class _FakeWorker(QObject):
        result = Signal(object, object)
        failed = Signal(object, str)

        def submit(self, *a):
            pass

    def _dlg_factory(return_code, color):
        class _Dlg(QObject):
            def __init__(self, color0, title, parent):
                super().__init__()
                self.color = QColor(color)

            def exec(self):
                return return_code
        return _Dlg

    s = Settings(tmp_path / "s.json")
    s.set("theme_color", "#0065d5")
    page = SettingsPage(s, _FakeWorker())
    assert page.accent_combo.currentIndex() == 1
    assert "#0065d5" in page.accent_card.contentLabel.text()

    # activated 驱动（同项重选照发）；程序化 setCurrentIndex 不触发，
    # 故直接调 handler 模拟用户选择（combo 视觉态由用户操作自带，不断言）
    page._on_accent(0)                            # 切跟随系统
    assert s.get("theme_color") == ""

    monkeypatch.setattr(sp, "ColorDialog", _dlg_factory(0, "#123456"))
    page._on_accent(1)                            # 取消：回退跟随系统
    assert s.get("theme_color") == ""
    assert page.accent_combo.currentIndex() == 0  # handler 主动回退

    monkeypatch.setattr(sp, "ColorDialog", _dlg_factory(1, "#123456"))
    page._on_accent(1)                            # 确认：落 store
    assert s.get("theme_color") == "#123456"
    assert "#123456" in page.accent_card.contentLabel.text()

    page._on_accent(1)                            # 回归：已停在自定义再选仍弹窗
    assert s.get("theme_color") == "#123456"

    # 同色确认钉住：跟随系统下确认与当前生效色相同的颜色也落 store
    # （qfw colorChanged 此时不发，accept 后直接读 dlg.color 才接得住）
    page._on_accent(0)
    assert s.get("theme_color") == ""
    monkeypatch.setattr(sp, "ColorDialog", _dlg_factory(1, "#009faa"))
    page._on_accent(1)
    assert s.get("theme_color") == "#009faa"
