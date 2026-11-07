# AGENTS.md

ASS 字体替补：Windows 桌面 GUI 工具（PySide6 + QFluentWidgets）。简繁转换后的 ASS 字幕，分析原字体覆盖率，寻找孪生/相似替补字体，一键改写保存。界面源语言为简体中文，代码注释、文档、commit 信息均用中文。

## 目录

- `src/ass_font_substitute/core/` — 零 Qt 纯逻辑层（禁止 import PySide6/qfluentwidgets）
  - `assparse/` 编码探测、字体抽取（light_ass）、改写
  - `fontindex/` 字体解析（fontTools）+ SQLite 索引
  - `coverage/` 覆盖率匹配；`similarity/` 渲染（freetype）、度量、缓存、进程池搜索
  - `decision/` 替换记忆库
- `src/ass_font_substitute/gui/` — 界面层
  - `*_vm.py`、`jobs.py`、`drop.py`、`paths.py`、`name_lang.py` 等保持零 Qt，供测试直喂；
    对话框/翻译/控件类封装（`lastdir.py`、`vm_text.py`、`script_filter.py` 控件等）属刻意
    Qt 边界，不受此约束，勿去 Qt 化
  - `pages/` 四页（library / browse / subs / settings），`widgets/` 自绘控件
  - `translations/afs_zh-Hant.{ts,qm}` 繁体译文（两者都入库）
- `main.py` — 打包入口（pyside6-deploy 需要 .py 文件入口）
- `tests/` — pytest；`test_gui_*` 用 offscreen Qt
- `docs/` — 设计 spec、实施计划、`gui-manual-tests.md`（人工验收清单）、`wireframe.html`
- `scripts/` — 打包与翻译脚本
- 忽略目录：`temp/`（真实字幕样本）、`refs/`、`Data/`、`deployment/`

## 命令

```bash
uv sync
uv run python -m ass_font_substitute.gui          # 源码运行
uv run pytest                                     # 全量测试
uv run pytest tests/test_matcher.py -q            # 单文件
uv run ruff check .                               # lint（无 typecheck 步骤）
uv run python scripts/gen_translations.py         # 改界面文案后必跑
scripts\build_portable.bat                        # 打包（cmd 下跑，需 VS2022 C++ 工具链）
```

## 约定

- import：ruff isort `force-single-line`，一行一个 import。全角标点规则（RUF001-003）已关闭，中文文案正常写。
- commit：`type(scope): 中文描述`（feat/fix/docs/build/chore…），正文说明原因与教训。
- 数据目录：绿色软件，数据写 `<运行目录>/Data`（源码运行即仓库根 `Data/`），不写 `%APPDATA%`。测试/手测用环境变量 `AFS_DATA_DIR` 隔离，别污染真实 `Data/`。见 `gui/paths.py`。
- 多进程：渲染/解析用进程池；`main.py` 与 `gui/__main__.py` 必须最先调用 `multiprocessing.freeze_support()`，否则打包后子进程递归拉起 GUI。

## 界面翻译（易错）

- 文案写 `self.tr("简体源文")`；改动任何 tr 源文案后重跑 `gen_translations.py`（lupdate → opencc s2twp → lrelease），并提交 `.ts` 与 `.qm`。
- 人工纠偏放 `gen_translations.py` 的 `_OVERRIDES`；源串从代码消失时 `test_gui_i18n` 会报红。
- vm 层数据保持简体源值（兼作逻辑键）；只在渲染处经 `gui/vm_text.py`（context `"vm"`）翻译。动态值须用 `QT_TRANSLATE_NOOP` 登记，否则 lupdate 提取不到。
- `userData`/设置里存源值，不存译文。
- 动态拼接文案（f-string/format）的字面量必须提出模板单独过 `tr` 再拼：
  lupdate 提取不到 f-string 内嵌的 `tr()`，且 `.ts` 同步守卫对此盲区
  （fresh 与已提交两边同样看不见，恒等不报红），繁体界面会残留简体。
- `gui/lang_names_data.py` 为生成数据（生成脚本已删），勿手改结构。

## UI 约定

- tooltip 一律走 `gui/tip.py` 的 `install_tip`，不直接 `setToolTip`。
- 浅强调色反相阈值统一用 `widgets/primary_text.py` 的 `_LUMA_DARK_TEXT`（150/255），不另起常量。
- 冻结成 pixmap 的图标必须配主题切换（retheme）钩子，否则换主题/强调色后图标不可见。
- 没有父对象的子控件调 `setVisible(True)` 会变成顶层窗口（出现空白「python」窗）；先挂父再显示。
- 观感类改动 offscreen 测不全：对照 `docs/gui-manual-tests.md`，交由用户真机验收后再提交。

## 打包

- `build_portable.bat` 必须保持纯 ASCII（cmd 按 GBK 解析，UTF-8 中文会错行）。
- 产物：`deployment\main.dist\AssFontSubstitute.exe` + `deployment\AssFontSubstitute-<版本>-win64.7z`（不含 `Data/`）。
- 判定打包环境需同时检查 `sys.frozen` 与 `__compiled__`（Nuitka standalone 不设 `sys.frozen`）。

## 依赖备注

- `light_ass`：modifier 非标准 list；`ParsedLine` 索引不是惰性初始化；`\t(...)` 内嵌 `\fn` 需下钻一层。动 `assparse/` 前读现有实现与 `tests/test_extract.py`、`tests/test_rewrite*.py`。
- 许可证：整体 GPL-3.0-or-later（受 QFluentWidgets 约束）；`core/` 例外为 MIT（`core/LICENSE`），供他人抄源码。
  方向单向：core 可被 gui 使用，GPL 代码（gui、GPL 参考项目）不得流入 core；core 新增依赖须为宽松协议。
