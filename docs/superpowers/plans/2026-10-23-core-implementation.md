# AssFontSubstitute Core 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 AssFontSubstitute 的 core 层（字体索引 / ASS 解析改写 / 覆盖与孪生 / 相似度引擎 / 决定记忆），全部可 pytest 验证，无 GUI。

**Architecture:** 五个 core 包（fontindex / assparse / coverage / similarity / decision）经 models.py 中的数据结构通信。索引 = 每字体目录一个 fontindex.db（sqlite），聚合视图 LibraryIndex 做版本去重；全局 cache.db 存渲染条 / 相似度对 / 决定记忆。ASS 用 light_ass 只做解析，写回自研行级手术保证未改动行字节级原样。

**Tech Stack:** Python 3.12+（uv 管理），fontTools，freetype-py，numpy，light-ass，OpenCC，xxhash，pytest。

**Spec:** `docs/superpowers/specs/2026-10-23-ass-font-substitute-design.md`（先读，本计划从 spec 论证）

## Global Constraints

- Python ≥3.12，uv 管理环境；依赖：fonttools、freetype-py、numpy、light-ass、opencc-python-reimplemented 或 opencc、xxhash、pytest
- 一切名称匹配前置**名称规范化**：strip 空白 + 剥 `@` 前缀 + casefold
- 字体指纹 = hash(全部名称别名 + cmap 区间表 + weight/width/italic + 文件内容 xxhash64)
- 版本去重身份 = PostScript 名（nameID 6），无则名称别名并集；版本 = nameID 5 解析数字；新版替换旧版、旧版跳过、相等/解析失败保留已入库
- cmap 存连续 Unicode 区间表（二分查询），非字符 set
- 探针真值表：简 = hit(Ps)≥90% 且 hit(Pt)<90%；繁反之；简繁 = 双≥90%；其余未标注
- 编码探测：BOM 表 → 全文件顺序试探 utf-8 → gb18030 → big5 → shift_jis → euc-kr，每个候选解码成功后过合理性校验（U+FFFD / CJK 占比 / 中性高频字命中），全失败报错
- 写回：Fontname 列 strip 后与旧名**全等**才整列替换；`\fn` 仅 `{...}` 块内 lookahead 完整匹配；`@舊名 → @新名`；Comment 行 `\fn` 也改写；Format 行按 section 绑定；覆盖原文件 = 写同目录临时文件后 os.replace 原子替换
- light_ass 解析必须 `strict=False`
- 相似度：tile 48×48 灰度；只按两字体**可用字交集**评分，交集空 = 无法比较；高相似阈值 70%；攒 N=5 个高相似暂停；渲染缓存总量上限 500MB LRU
- 决定记忆键 = 规范化字体名 + 方向
- sqlite 全部 WAL + busy_timeout=5000
- 测试不依赖外部下载；真实字幕样例在 `temp/`（只读，测试复制副本）
- 每任务 TDD：先写失败测试，再实现，再提交

---

### Task 1: 项目脚手架

**Files:**
- Create: `pyproject.toml`、`ass_font_substitute/__init__.py`、`ass_font_substitute/core/__init__.py`、`tests/__init__.py`、`tests/conftest.py`

**Interfaces:**
- Produces: 可运行的 pytest；后续所有任务的包路径 `ass_font_substitute.core.*`

- [ ] **Step 1: uv 初始化**

```bash
cd D:/Projects/AssFontSubstitute
uv init --package --python 3.12 --name ass-font-substitute
```

- [ ] **Step 2: pyproject 依赖与 pytest 配置**

`pyproject.toml` 合并为：

```toml
[project]
name = "ass-font-substitute"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fonttools>=4.53",
    "freetype-py>=2.4",
    "numpy>=2.0",
    "light-ass>=0.3",
    "opencc>=1.1",
    "xxhash>=3.0",
]

[dependency-groups]
dev = ["pytest>=8.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 3: 包骨架与冒烟测试**

```bash
mkdir -p ass_font_substitute/core tests
touch ass_font_substitute/__init__.py ass_font_substitute/core/__init__.py tests/__init__.py
```

`tests/conftest.py`：

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
```

`tests/test_scaffold.py`：

```python
def test_import_package():
    import ass_font_substitute.core  # noqa: F401
```

- [ ] **Step 4: 运行**

```bash
uv sync && uv run pytest -q
```

Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "chore: 项目脚手架（uv + pytest）"
```

---

### Task 2: models.py — CmapRanges 与数据结构

**Files:**
- Create: `ass_font_substitute/core/models.py`
- Test: `tests/test_ranges.py`

**Interfaces:**
- Produces:
  - `CmapRanges(starts: tuple[int,...], ends: tuple[int,...])`，方法 `contains(cp:int)->bool`、`to_bytes()->bytes`、类方法 `from_chars(chars)->CmapRanges`、`from_bytes(b)->CmapRanges`
  - `FontRecord`（dir_path:str, rel_path:str, face_index:int, psname:str|None, version:tuple[int,...]|None, aliases:list[str], weight:int, width:int, italic:bool, cmap:CmapRanges, xxh:str, size:int, mtime_ns:int, script:str, category:str）
  - `FontUsage`（raw_name:str, norm_name:str, style_count:int, inline_count:int, chars:frozenset[str]）
  - `Candidate`（fingerprint:str, display_name:str, coverage:float, similarity:float|None, size_ratio:float|None, n_chars:int|None, script:str, category:str, weight:int, source:str）

- [ ] **Step 1: 失败测试**

`tests/test_ranges.py`：

```python
from ass_font_substitute.core.models import CmapRanges


def test_contains_and_merge():
    r = CmapRanges.from_chars({0x4E00, 0x4E01, 0x4E02, 0x4E10})
    assert r.contains(0x4E01)
    assert r.contains(0x4E10)
    assert not r.contains(0x4E03)
    assert not r.contains(0x4E0F)
    assert r.starts == (0x4E00, 0x4E10)  # 0x4E00-0x4E02 合并成一段


def test_bytes_roundtrip():
    r = CmapRanges.from_chars({1, 2, 3, 100, 200, 0x10FFFF})
    assert CmapRanges.from_bytes(r.to_bytes()) == r


def test_empty():
    r = CmapRanges.from_chars(set())
    assert not r.contains(0x4E00)
    assert r.starts == ()
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_ranges.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

`ass_font_substitute/core/models.py`：

```python
from __future__ import annotations

import bisect
import struct
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class CmapRanges:
    starts: tuple[int, ...]
    ends: tuple[int, ...]  # 含端点

    def contains(self, cp: int) -> bool:
        i = bisect.bisect_right(self.starts, cp) - 1
        return i >= 0 and cp <= self.ends[i]

    def hit_ratio(self, chars) -> float:
        s = list(chars)
        if not s:
            return 0.0
        return sum(self.contains(ord(c)) for c in s) / len(s)

    def to_bytes(self) -> bytes:
        n = len(self.starts)
        return struct.pack(f"<I{n * 2}I", n, *sum(zip(self.starts, self.ends), ()))

    @classmethod
    def from_chars(cls, cps) -> "CmapRanges":
        pts = sorted(set(cps))
        if not pts:
            return cls((), ())
        starts, ends = [pts[0]], [pts[0]]
        for cp in pts[1:]:
            if cp == ends[-1] + 1:
                ends[-1] = cp
            else:
                starts.append(cp)
                ends.append(cp)
        return cls(tuple(starts), tuple(ends))

    @classmethod
    def from_cmap_subtables(cls, tables) -> "CmapRanges":
        cps: set[int] = set()
        for t in tables:
            cps.update(t.cmap.keys())
        return cls.from_chars(cps)


@dataclass
class FontRecord:
    dir_path: str
    rel_path: str
    face_index: int
    psname: Optional[str]
    version: Optional[tuple[int, ...]]
    aliases: list[str]
    weight: int
    width: int
    italic: bool
    cmap: CmapRanges
    xxh: str
    size: int
    mtime_ns: int
    script: str  # 简/繁/简繁/未标注
    category: str  # 黑/宋/圆/楷/仿/未分类


@dataclass
class FontUsage:
    raw_name: str
    norm_name: str
    style_count: int
    inline_count: int
    chars: set[str] = field(default_factory=set)


@dataclass
class Candidate:
    fingerprint: str
    display_name: str
    coverage: float
    similarity: Optional[float] = None
    size_ratio: Optional[float] = None
    n_chars: Optional[int] = None
    script: str = "未标注"
    category: str = "未分类"
    weight: int = 400
    source: str = "recommend"  # recommend/last/twin/pick
```

- [ ] **Step 4: 运行通过**

Run: `uv run pytest tests/test_ranges.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(core): CmapRanges 与核心数据结构"
```

---

### Task 3: probes.py — 简/繁探针与分类词表

**Files:**
- Create: `ass_font_substitute/core/fontindex/__init__.py`、`ass_font_substitute/core/fontindex/probes.py`
- Test: `tests/test_probes.py`

**Interfaces:**
- Produces: `PROBE_SC: frozenset[str]`、`PROBE_TC: frozenset[str]`、`classify_script(cmap: CmapRanges) -> str`（简/繁/简繁/未标注）、`classify_category(aliases: list[str]) -> str`（黑/宋/圆/楷/仿/未分类）

- [ ] **Step 1: 失败测试**

`tests/test_probes.py`：

```python
from ass_font_substitute.core.fontindex.probes import classify_category, classify_script
from ass_font_substitute.core.models import CmapRanges


def _cmap(scss, stcs):
    cps = {ord(c) for c in scss} | {ord(c) for c in "的一是我"}
    return CmapRanges.from_chars(cps)


def test_script_simple():
    assert classify_script(_cmap("这们时说", "")) == "简"


def test_script_traditional():
    assert classify_script(_cmap("", "這們時說")) == "繁"


def test_script_both():
    assert classify_script(_cmap("这们时说", "這們時說")) == "简繁"


def test_script_none():
    assert classify_script(CmapRanges.from_chars({ord(c) for c in "的一是我"})) == "未标注"


def test_category_keywords():
    assert classify_category(["華康中黑體"]) == "黑"
    assert classify_category(["仿宋 GB2312"]) == "仿"
    assert classify_category(["华康华综体 W5"]) == "未分类"
    assert classify_category(["汉仪旗圆", "HYQiYuan"]) == "圆"
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_probes.py -v`，Expected: FAIL

- [ ] **Step 3: 实现**

`ass_font_substitute/core/fontindex/probes.py`：

```python
from ass_font_substitute.core.models import CmapRanges

# 各约 60-100 常用独有字（内置常量，词表固定）
PROBE_SC = frozenset(
    "这们时说为对发经开关门长东车贝见书马鸟风雨页飞气习乡亿仅仅从仓仪们价会伟传伤优会何余作你"
    "体余佣侠侧俩俭债倾偿儬兒党兰关兴养兽内冈册写军农冯冲决况冻净凤凭凯击别刘则刚创刬剧剩势劝"
)
PROBE_TC = frozenset(
    "這們時說為對發經開關門長東車貝見書馬鳥風雨頁飛氣習鄉億僅僅從倉儀們價會偉傳傷優會何餘作你"
    "體餘傭俠側倆儉債傾償兒黨蘭關興養獸內岡冊寫軍農馮衝決況凍淨鳳憑凱擊別劉則剛創剷劇剩勢勸"
)
CATEGORY_KEYWORDS = [("黑", "黑"), ("宋", "宋"), ("明", "宋"), ("圆", "圆"), ("圓", "圆"),
                     ("楷", "楷"), ("仿", "仿")]
THRESHOLD = 0.90


def classify_script(cmap: CmapRanges) -> str:
    hs, ht = cmap.hit_ratio(PROBE_SC), cmap.hit_ratio(PROBE_TC)
    sc, tc = hs >= THRESHOLD, ht >= THRESHOLD
    if sc and tc:
        return "简繁"
    if sc:
        return "简"
    if tc:
        return "繁"
    return "未标注"


def classify_category(aliases) -> str:
    for alias in aliases:
        for kw, cat in CATEGORY_KEYWORDS:
            if kw in alias:
                return cat
    return "未分类"
```

注：两探针集按「同字简繁对」成对扩到 ≥60 对；上面是种子，实现时补满并保持两集合一一对应（校验测试：`PROBE_SC` 与 `PROBE_TC` 去掉简繁异形后无交集、长度相等）。

- [ ] **Step 4: 补一探针对齐断言进测试**

`tests/test_probes.py` 追加：

```python
def test_probe_sets_aligned():
    from ass_font_substitute.core.fontindex.probes import PROBE_SC, PROBE_TC
    assert len(PROBE_SC) == len(PROBE_TC)
    assert not (PROBE_SC & PROBE_TC)
```

- [ ] **Step 5: 运行 + Commit**

Run: `uv run pytest tests/test_probes.py -v`，Expected: all passed

```bash
git add -A && git commit -m "feat(core): 简繁探针与分类词表"
```

---

### Task 4: 测试字体工厂

**Files:**
- Create: `tests/fonts.py`
- Test: `tests/test_fonts_factory.py`

**Interfaces:**
- Produces（测试基础设施，非 core 产物）:
  - `build_font(path, chars, family="測試體", extra_aliases=None, psname=None, version="Version 1.000", weight=400, width=5, italic=False)`
  - `build_ttc(path, specs: list[dict])`
  - 每个字符字形为方块轮廓（保证 FreeType 可渲染出非空位图）

- [ ] **Step 1: 失败测试**

`tests/test_fonts_factory.py`：

```python
from fontTools.ttLib import TTFont

from tests.fonts import build_font, build_ttc


def test_build_font_roundtrip(tmp_path):
    p = tmp_path / "f.ttf"
    build_font(p, chars="永中木水", family="華康測試體W5", psname="DFTest-W5",
               version="Version 5.001", weight=700)
    tt = TTFont(p)
    names = {r.nameID: r.toUnicode() for r in tt["name"].names if r.platformID == 3}
    assert names[1] == "華康測試體W5"
    assert names[6] == "DFTest-W5"
    assert "5.001" in names[5]
    assert ord("永") in tt.getBestCmap()
    assert tt["OS/2"].usWeightClass == 700


def test_build_ttc(tmp_path):
    p = tmp_path / "c.ttc"
    build_ttc(p, [dict(chars="永", family="A體"), dict(chars="中", family="B體")])
    assert len(TTFont(p, fontNumber=0)["name"].names) > 0
    from fontTools.ttLib import TTCollection
    assert len(TTCollection(p).fonts) == 2
```

- [ ] **Step 2: 运行确认失败**，Run: `uv run pytest tests/test_fonts_factory.py -v`

- [ ] **Step 3: 实现**

`tests/fonts.py`：

```python
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTCollection


def _box(pen: TTGlyphPen):
    pen.moveTo((100, 0))
    pen.lineTo((100, 600)); pen.lineTo((500, 600)); pen.lineTo((500, 0))
    pen.closePath()


def _make(spec: dict):
    chars = spec.get("chars", "")
    family = spec.get("family", "測試體")
    weight = spec.get("weight", 400)
    width = spec.get("width", 5)
    italic = spec.get("italic", False)
    psname = spec.get("psname", family + "-" + str(weight))
    version = spec.get("version", "Version 1.000")

    glyph_order = [".notdef", "space"] + [f"g{i}" for i in range(len(chars))]
    cmap = {0x20: "space"}
    for i, c in enumerate(chars):
        cmap[ord(c)] = f"g{i}"
    cmap.update({ord(d): "space" for d in "0123456789Aa"})

    glyphs = {".notdef": _new_box(), "space": TTGlyphPen(None).glyph()}
    for i in range(len(chars)):
        glyphs[f"g{i}"] = _new_box()

    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({g: (500, 0) for g in glyph_order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWeightClass=weight,
                usWidthClass=width, fsSelection=(1 if italic else 64))
    fb.setupNameTable({
        1: family, 2: "Regular", 4: family, 6: psname, 5: version,
        3: "TestFont;" + version + ";AFS", 8: "AFS Test",
    })
    # 繁/简别名（zh-TW / zh-CN）
    nt = fb.font["name"]
    for nid, val in ((1, family), (4, family)):
        if spec.get("extra_aliases"):
            nt.setName(spec["extra_aliases"].get("zh-CN", family), nid, 3, 1, 0x804)
            nt.setName(spec["extra_aliases"].get("zh-TW", family), nid, 3, 1, 0x404)
    fb.setupPost()
    fb.setupDSIG()
    return fb.font


def _new_box():
    pen = TTGlyphPen(None)
    _box(pen)
    return pen.glyph()


def build_font(path, **spec):
    _make(spec).save(path)


def build_ttc(path, specs):
    coll = TTCollection()
    coll.fonts = [_make(s) for s in specs]
    coll.save(path)
```

实现时以 `refs/pysubs2` 无关、`fontTools` 文档为准微调 `setupGlyf`/`setupHorizontalMetrics` 参数顺序；断言以测试通过为准。

- [ ] **Step 4: 运行通过**，Run: `uv run pytest tests/test_fonts_factory.py -v`

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "test: 测试字体工厂（fontBuilder）"
```

---

### Task 5: parser.py — 单文件解析为 FontRecord

**Files:**
- Create: `ass_font_substitute/core/fontindex/parser.py`
- Test: `tests/test_parser.py`

**Interfaces:**
- Consumes: `CmapRanges`、`classify_script/classify_category`
- Produces: `FontParseError(Exception)`、`parse_font_file(path: Path) -> list[FontRecord]`（TTC 展开多条；face_index 从 0 起）

- [ ] **Step 1: 失败测试**

`tests/test_parser.py`：

```python
import pytest

from ass_font_substitute.core.fontindex.parser import parse_font_file
from tests.fonts import build_font, build_ttc


def test_parse_single(tmp_path):
    p = tmp_path / "a.ttf"
    build_font(p, chars="永中木水", family="華康測試體W5", psname="DFTest-W5",
               version="Version 5.001", weight=700,
               extra_aliases={"zh-CN": "华康测试体 W5"})
    recs = parse_font_file(p)
    assert len(recs) == 1
    r = recs[0]
    assert r.face_index == 0
    assert r.psname == "DFTest-W5"
    assert r.version == (5, 1)
    assert r.weight == 700 and not r.italic
    assert "華康測試體W5" in r.aliases and "华康测试体 W5" in r.aliases
    assert r.cmap.contains(ord("永"))
    assert len(r.xxh) == 16


def test_parse_ttc(tmp_path):
    p = tmp_path / "a.ttc"
    build_ttc(p, [dict(chars="永", family="A體"), dict(chars="中", family="B體")])
    recs = parse_font_file(p)
    assert [r.face_index for r in recs] == [0, 1]
    assert {r.aliases[0] for r in recs} >= {"A體", "B體"}


def test_parse_bad_file(tmp_path):
    p = tmp_path / "bad.ttf"
    p.write_bytes(b"not a font")
    from ass_font_substitute.core.fontindex.parser import FontParseError
    with pytest.raises(FontParseError):
        parse_font_file(p)
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现**

`ass_font_substitute/core/fontindex/parser.py`：

```python
from __future__ import annotations

import re
from pathlib import Path

import xxhash
from fontTools.ttLib import TTCollection, TTFont

from ass_font_substitute.core.fontindex.probes import classify_category, classify_script
from ass_font_substitute.core.models import CmapRanges, FontRecord

NAME_IDS = (1, 4, 6, 16, 17)
TTC_EXTS = {".ttc", ".otc"}
_VER_RE = re.compile(r"([\d]+(?:\.\d+)*)")


class FontParseError(Exception):
    pass


def parse_version(s: str | None) -> tuple[int, ...] | None:
    if not s:
        return None
    m = _VER_RE.search(s)
    if not m:
        return None
    try:
        return tuple(int(x) for x in m.group(1).split("."))
    except ValueError:
        return None


def _load_fonts(path: Path) -> list[TTFont]:
    try:
        if path.suffix.lower() in TTC_EXTS:
            return list(TTCollection(str(path)).fonts)
        return [TTFont(str(path), lazy=True)]
    except Exception as e:  # fontTools 各类异常统一包装
        raise FontParseError(f"{path.name}: {e}") from e


def parse_font_file(path: Path) -> list[FontRecord]:
    fonts = _load_fonts(path)
    data = path.read_bytes()
    xxh = xxhash.xxh64(data).hexdigest()
    st = path.stat()
    out: list[FontRecord] = []
    for idx, tt in enumerate(fonts):
        try:
            name = tt["name"]
            aliases = sorted({r.toUnicode() for r in name.names
                              if r.nameID in NAME_IDS and r.toUnicode()})
            psname = next((r.toUnicode() for r in name.names if r.nameID == 6), None)
            version = parse_version(next((r.toUnicode() for r in name.names
                                          if r.nameID == 5), None))
            os2 = tt["OS/2"]
            cmap = CmapRanges.from_cmap_subtables(tt["cmap"].tables)
            out.append(FontRecord(
                dir_path=str(path.parent), rel_path=path.name, face_index=idx,
                psname=psname, version=version, aliases=aliases,
                weight=os2.usWeightClass, width=os2.usWidthClass,
                italic=bool(os2.fsSelection & 1),
                cmap=cmap, xxh=xxh, size=st.st_size, mtime_ns=st.st_mtime_ns,
                script=classify_script(cmap), category=classify_category(aliases),
            ))
        except Exception as e:
            raise FontParseError(f"{path.name}#{idx}: {e}") from e
    return out
```

- [ ] **Step 4: 运行通过**

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(core): 字体文件解析（含 TTC 展开与版本）"
```

---

### Task 6: store.py — fontindex.db 增量更新与扫描

**Files:**
- Create: `ass_font_substitute/core/fontindex/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `parse_font_file`、`FontRecord`
- Produces:
  - `SCHEMA_VERSION = 1`
  - `open_db(path: Path) -> sqlite3.Connection`（WAL、busy_timeout、建表、版本不符抛 `IndexSchemaError`）
  - `scan_dir(root: Path) -> ScanReport`（rel_paths 集合）
  - `update_index(dir_path: Path, conn) -> UpdateResult`（added/changed/removed/bad_retried 计数）
  - `stats(conn) -> dict`（files/fonts/support_sc/support_tc）

- [ ] **Step 1: 失败测试**

`tests/test_store.py`：

```python
import sqlite3

from ass_font_substitute.core.fontindex.store import open_db, stats, update_index
from tests.fonts import build_font


def _mklib(d, n=2):
    for i in range(n):
        build_font(d / f"f{i}.ttf", chars="永中木", family=f"X體{i}")


def test_incremental(tmp_path):
    d = tmp_path / "fonts"
    d.mkdir()
    _mklib(d)
    conn = open_db(d / "fontindex.db")
    r1 = update_index(d, conn)
    assert r1.added == 2 and r1.fonts == 2
    s1 = stats(conn)
    assert s1["files"] == 2

    # 无变化 → 零解析
    r2 = update_index(d, conn)
    assert (r2.added, r2.changed, r2.removed) == (0, 0, 0)

    # 新增 + 删除 + 变更
    build_font(d / "new.ttf", chars="水", family="Y體")
    (d / "f0.ttf").unlink()
    (d / "f1.ttf").write_bytes((d / "f1.ttf").read_bytes() + b"\0")  # 内容变
    r3 = update_index(d, conn)
    assert r3.added == 1 and r3.removed >= 1
    assert stats(conn)["files"] == 2


def test_bad_file_retry(tmp_path):
    d = tmp_path / "fonts"
    d.mkdir()
    bad = d / "bad.ttf"
    bad.write_bytes(b"junk")
    conn = open_db(d / "fontindex.db")
    r = update_index(d, conn)
    assert r.bad == 1
    build_font(bad, chars="永", family="Fixed體")  # mtime/size 变 → 自动重试
    r2 = update_index(d, conn)
    assert r2.bad_retried == 1 and r2.fonts == 1


def test_whitelist_and_self_exclude(tmp_path):
    d = tmp_path / "fonts"
    d.mkdir()
    _mklib(d, n=1)
    (d / "note.txt").write_text("x")
    (d / "fontindex.db").write_bytes(b"")
    sub = d / "sub"
    sub.mkdir()
    build_font(sub / "nested.ttf", chars="永", family="Z體")
    conn = open_db(d / "fontindex.db")
    r = update_index(d, conn)
    assert r.added == 2  # 递归含 sub，排除 txt 与 db 自身
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现**

`ass_font_substitute/core/fontindex/store.py`：

```python
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ass_font_substitute.core.fontindex.parser import FontParseError, parse_font_file
from ass_font_substitute.core.models import CmapRanges

SCHEMA_VERSION = 1
WHITELIST = {".ttf", ".otf", ".ttc", ".otc"}

DDL = """
CREATE TABLE IF NOT EXISTS schema_version(v INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS files(
  rel_path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, xxh TEXT);
CREATE TABLE IF NOT EXISTS fonts(
  id INTEGER PRIMARY KEY, rel_path TEXT, face_index INTEGER, psname TEXT,
  version TEXT, aliases TEXT, weight INTEGER, width INTEGER, italic INTEGER,
  cmap BLOB, script TEXT, category TEXT,
  UNIQUE(rel_path, face_index));
CREATE TABLE IF NOT EXISTS bad(
  rel_path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, reason TEXT);
"""


class IndexSchemaError(Exception):
    pass


@dataclass
class UpdateResult:
    added: int = 0
    changed: int = 0
    removed: int = 0
    bad: int = 0
    bad_retried: int = 0
    fonts: int = 0


def open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(DDL)
    row = conn.execute("SELECT v FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()
    elif row[0] != SCHEMA_VERSION:
        raise IndexSchemaError(f"schema v{row[0]} != v{SCHEMA_VERSION}")
    return conn


def scan_dir(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*")
                  if p.is_file() and p.suffix.lower() in WHITELIST
                  and p.name != "fontindex.db")


def update_index(dir_path: Path, conn: sqlite3.Connection) -> UpdateResult:
    res = UpdateResult()
    known = {r[0]: (r[1], r[2]) for r in
             conn.execute("SELECT rel_path, size, mtime_ns FROM files")}
    bad = {r[0]: (r[1], r[2]) for r in
           conn.execute("SELECT rel_path, size, mtime_ns FROM bad")}
    current: dict[str, tuple[int, int]] = {}
    todo: list[Path] = []
    for p in scan_dir(dir_path):
        rel = str(p.relative_to(dir_path))
        st = p.stat()
        current[rel] = (st.st_size, st.st_mtime_ns)
        if rel in known:
            if known[rel] != current[rel]:
                todo.append(p); res.changed += 1
        elif rel in bad:
            if bad[rel] != current[rel]:
                todo.append(p); res.bad_retried += 1
        else:
            todo.append(p); res.added += 1

    with conn:
        for rel in set(known) - set(current):
            res.removed += 1
            conn.execute("DELETE FROM files WHERE rel_path=?", (rel,))
            conn.execute("DELETE FROM fonts WHERE rel_path=?", (rel,))
            conn.execute("DELETE FROM bad WHERE rel_path=?", (rel,))
        for p in todo:
            rel = str(p.relative_to(dir_path))
            try:
                recs = parse_font_file(p)
            except FontParseError as e:
                res.bad += 1
                st = p.stat()
                conn.execute("INSERT OR REPLACE INTO bad VALUES (?,?,?,?)",
                             (rel, st.st_size, st.st_mtime_ns, str(e)))
                conn.execute("DELETE FROM fonts WHERE rel_path=?", (rel,))
                continue
            st = p.stat()
            conn.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?)",
                         (rel, st.st_size, st.st_mtime_ns, recs[0].xxh))
            conn.execute("DELETE FROM bad WHERE rel_path=?", (rel,))
            conn.execute("DELETE FROM fonts WHERE rel_path=?", (rel,))
            for r in recs:
                conn.execute(
                    "INSERT INTO fonts(rel_path,face_index,psname,version,aliases,"
                    "weight,width,italic,cmap,script,category) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (rel, r.face_index, r.psname,
                     json.dumps(r.version) if r.version else None,
                     json.dumps(r.aliases, ensure_ascii=False),
                     r.weight, r.width, int(r.italic),
                     r.cmap.to_bytes(), r.script, r.category))
    res.fonts = stats(conn)["fonts"]
    return res


def stats(conn: sqlite3.Connection) -> dict:
    files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    fonts = conn.execute("SELECT COUNT(*) FROM fonts").fetchone()[0]
    sc = conn.execute("SELECT COUNT(*) FROM fonts WHERE script IN ('简','简繁')").fetchone()[0]
    tc = conn.execute("SELECT COUNT(*) FROM fonts WHERE script IN ('繁','简繁')").fetchone()[0]
    return {"files": files, "fonts": fonts, "support_sc": sc, "support_tc": tc}


def load_font_rows(conn: sqlite3.Connection):
    for row in conn.execute(
            "SELECT f.rel_path, f.face_index, f.psname, f.version, f.aliases, "
            "f.weight, f.width, f.italic, f.cmap, f.script, f.category, "
            "fi.xxh, fi.size, fi.mtime_ns FROM fonts f JOIN files fi USING(rel_path)"):
        yield row


__all__ = ["open_db", "scan_dir", "update_index", "stats", "load_font_rows",
           "SCHEMA_VERSION", "IndexSchemaError", "UpdateResult"]
```

（`load_font_rows` 供 Task 7 LibraryIndex 使用。）

- [ ] **Step 4: 运行通过**，Run: `uv run pytest tests/test_store.py -v`

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(core): fontindex.db 增量更新（三集合+bad 重试+白名单递归）"
```

---

### Task 7: library.py — 聚合视图与版本去重

**Files:**
- Create: `ass_font_substitute/core/fontindex/library.py`
- Test: `tests/test_library.py`

**Interfaces:**
- Consumes: `load_font_rows`
- Produces:
  - `FontEntity`（fingerprint, display_name, aliases, psname, version, weight, width, italic, script, category, cmap, dir_path, rel_path, face_index）
  - `fingerprint_of(record_fields...) -> str`（放本模块，`hashlib.sha1` 规范串）
  - `LibraryIndex.open(dirs: list[Path]) -> LibraryIndex`，方法 `entities() -> list[FontEntity]`（版本去重）、`find_by_name(norm: str) -> list[FontEntity]`、`stats() -> dict`（含 skipped）、`skipped() -> list[str]`

**去重规则（spec §5）**：身份 = psname（无则 sorted(aliases) 串）；同身份取 version 最大者；version 相等或无法比较 → 保留排序靠前（目录顺序、rel_path、face_index）者；落选者进 skipped（reason="旧版本"）。

- [ ] **Step 1: 失败测试**

`tests/test_library.py`：

```python
from ass_font_substitute.core.fontindex.library import LibraryIndex, normalize_name
from ass_font_substitute.core.fontindex.store import open_db, update_index
from tests.fonts import build_font


def test_version_dedup(tmp_path):
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "old.ttf", chars="永", family="X體", psname="X-Medium",
               version="Version 1.000")
    build_font(d / "new.ttf", chars="永中", family="X體", psname="X-Medium",
               version="Version 2.000")
    build_font(d / "newer_offdir.ttf", chars="永", family="Y體", psname="Y-Light",
               version="Version 1.000")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    lib = LibraryIndex.open([d])
    ents = lib.entities()
    assert len(ents) == 2  # X-Medium 去重 + Y-Light
    x = next(e for e in ents if e.psname == "X-Medium")
    assert x.version == (2, 0)
    assert any("旧版本" in s for s in lib.skipped())


def test_find_by_name_normalized(tmp_path):
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "a.ttf", chars="永", family="華康測試體W5",
               extra_aliases={"zh-CN": "华康测试体 W5"})
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    lib = LibraryIndex.open([d])
    assert lib.find_by_name(normalize_name(" 华康测试体 W5 "))
    assert lib.find_by_name(normalize_name("華康測試體W5"))
    assert not lib.find_by_name("nope")
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现**

`ass_font_substitute/core/fontindex/library.py`：

```python
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ass_font_substitute.core.fontindex.store import load_font_rows, open_db
from ass_font_substitute.core.models import CmapRanges


def normalize_name(name: str) -> str:
    return name.strip().lstrip("@").casefold()


def fingerprint_of(aliases: list[str], cmap: CmapRanges, weight: int, width: int,
                   italic: bool, xxh: str) -> str:
    canon = json.dumps({
        "aliases": sorted(aliases),
        "cmap": [list(r) for r in zip(cmap.starts, cmap.ends)],
        "weight": weight, "width": width, "italic": int(italic), "xxh": xxh,
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FontEntity:
    fingerprint: str
    display_name: str
    aliases: tuple[str, ...]
    psname: Optional[str]
    version: Optional[tuple[int, ...]]
    weight: int
    width: int
    italic: bool
    script: str
    category: str
    cmap: CmapRanges
    dir_path: str
    rel_path: str
    face_index: int


class LibraryIndex:
    def __init__(self, dirs: list[Path]):
        self.dirs = dirs
        self._entities: list[FontEntity] = []
        self._skipped: list[str] = []
        self._load()

    @classmethod
    def open(cls, dirs: list[Path]) -> "LibraryIndex":
        return cls(dirs)

    def _load(self):
        raw = []
        for d in self.dirs:
            conn = open_db(d / "fontindex.db")
            for (rel, fi, ps, ver, al, w, wd, it, cmb, script, cat,
                 xxh, size, mtime) in load_font_rows(conn):
                aliases = tuple(json.loads(al))
                version = tuple(json.loads(ver)) if ver else None
                cmap = CmapRanges.from_bytes(cmb)
                fp = fingerprint_of(list(aliases), cmap, w, wd, bool(it), xxh)
                raw.append((d, rel, fi, ps, version, aliases, w, wd, bool(it),
                            script, cat, cmap, fp))
        best: dict[tuple, tuple] = {}
        for item in sorted(raw, key=lambda t: (t[0].name, t[1], t[2])):
            ps, ver = item[3], item[4]
            key = ps if ps else "\x00".join(item[5])
            cur = best.get(key)
            if cur is None:
                best[key] = item
            else:
                cv = cur[4]
                if ver is not None and (cv is None or ver > cv):
                    self._skipped.append(f"{cur[0].name}/{cur[1]}#{cur[2]} 旧版本")
                    best[key] = item
                else:
                    self._skipped.append(f"{item[0].name}/{item[1]}#{item[2]} 旧版本")
        self._entities = [
            FontEntity(fp, item[5][0], item[5], ps, ver, w, wd, it, script, cat,
                       cmap, str(item[0]), item[1], item[2])
            for (d, rel, fi, ps, ver, aliases, w, wd, it, script, cat, cmap, fp)
            in best.values()]

    def entities(self) -> list[FontEntity]:
        return list(self._entities)

    def find_by_name(self, norm: str) -> list[FontEntity]:
        n = norm.strip().casefold()
        return [e for e in self._entities
                if any(normalize_name(a) == n for a in e.aliases)]

    def stats(self) -> dict:
        sc = sum(1 for e in self._entities if e.script in ("简", "简繁"))
        tc = sum(1 for e in self._entities if e.script in ("繁", "简繁"))
        return {"dirs": len(self.dirs), "fonts": len(self._entities),
                "support_sc": sc, "support_tc": tc}

    def skipped(self) -> list[str]:
        return list(self._skipped)

    def dir_overlaps(self) -> list[str]:
        """嵌套/重叠目录告警：返回被其他目录包含的目录路径。"""
        out = []
        norm = [str(d.resolve()) for d in self.dirs]
        for i, d in enumerate(norm):
            for j, other in enumerate(norm):
                if i != j and other != d and d.startswith(other.rstrip("\\/") + "\\"):
                    out.append(f"{self.dirs[i]} 嵌套于 {self.dirs[j]}")
        return out
```

- [ ] **Step 4: 运行通过**

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(core): LibraryIndex 聚合视图与版本去重"
```

---

### Task 8: encoding.py — 编码探测

**Files:**
- Create: `ass_font_substitute/core/assparse/__init__.py`、`ass_font_substitute/core/assparse/encoding.py`
- Test: `tests/test_encoding.py`

**Interfaces:**
- Produces: `EncodingDetectError(Exception)`、`detect_encoding(data: bytes) -> str`（返回 Python codec 名，如 `utf-8-sig` / `utf-16` / `gb18030` / `big5`）

- [ ] **Step 1: 失败测试**

`tests/test_encoding.py`：

```python
import pytest

from ass_font_substitute.core.assparse.encoding import detect_encoding

SAMPLE = "[Script Info]\nTitle: 測試字幕\n; 繁體中文的一句話，包含常用字。\n\n[V4+ Styles]\n"


def test_bom_utf8():
    assert detect_encoding(SAMPLE.encode("utf-8-sig")) == "utf-8-sig"


def test_bom_utf16le():
    assert detect_encoding(SAMPLE.encode("utf-16")) == "utf-16"


def test_plain_utf8():
    assert detect_encoding(SAMPLE.encode("utf-8")) == "utf-8"


def test_gb18030():
    assert detect_encoding(SAMPLE.encode("gb18030")) == "gb18030"


def test_big5_beats_gb18030_misread():
    # 繁体高频文本：big5 解码命中高频字，gb18030 硬解必产生低频怪字
    big5_text = "這是繁體字幕的一句話，我們在這裡說話。" * 3
    assert detect_encoding(big5_text.encode("big5")) == "big5"


def test_failure():
    with pytest.raises(Exception):
        detect_encoding(bytes(range(0x80, 0xC0)) * 64)
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现**

`ass_font_substitute/core/assparse/encoding.py`：

```python
from __future__ import annotations

# 中性高频字（简繁同形），合理性校验用
FREQ_NEUTRAL = frozenset(
    "的一是我人不他在有中大小上下山川日月金木水火土田石心手足口目耳"
    "好可何你我把來去出入同天地時中文字幕的一是在"
)

BOMS = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)

TRY_ORDER = ("utf-8", "gb18030", "big5", "shift_jis", "euc-kr")


class EncodingDetectError(Exception):
    pass


def _plausible(text: str) -> bool:
    if "\ufffd" in text:
        return False
    if not text:
        return False
    cjk = sum(1 for c in text if 0x4E00 <= ord(c) <= 0x9FFF)
    freq = sum(c in FREQ_NEUTRAL for c in text)
    return freq >= 5 or cjk / max(len(text), 1) >= 0.05


def detect_encoding(data: bytes) -> str:
    for bom, codec in BOMS:
        if data.startswith(bom):
            return codec
    for codec in TRY_ORDER:
        try:
            text = data.decode(codec)
        except (UnicodeDecodeError, LookupError):
            continue
        if _plausible(text):
            return codec
    raise EncodingDetectError("无法识别编码（utf-8/gb18030/big5/shift_jis/euc-kr 均不合理）")
```

- [ ] **Step 4: 运行通过**

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(core): ASS 编码探测（BOM+全文件试探+合理性校验）"
```

---

### Task 9: extract.py — 字体引用与用字集提取

**Files:**
- Create: `ass_font_substitute/core/assparse/extract.py`
- Test: `tests/test_extract.py`

**Interfaces:**
- Consumes: `detect_encoding`、`normalize_name`、`FontUsage`
- Produces:
  - `ExtractResult`（usages: list[FontUsage]、unknown_styles: list[str]、parse_error: str|None）
  - `extract_usages(path: Path) -> ExtractResult`
  - `merge_usages(results: list[ExtractResult]) -> list[FontUsage]`（按 norm_name 合并：style_count/inline_count 求和、chars 并集、raw_name 取首个）
- 实现前先读 `refs/light_ass/light_ass/`（document.py、curly/parser.py、curly/tags/font.py、parsed_line.py）核对节点属性名（FontNameTag.value、DrawingNode、TextNode、ResetStyleTag），代码中属性名以下列模板为准校正。

- [ ] **Step 1: 失败测试**

`tests/test_extract.py`：

```python
from ass_font_substitute.core.assparse.extract import extract_usages, merge_usages

ASS = """[Script Info]
Title: t
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour
Style: Main,華康主體W5,52,&H00FFFFFF
Style: Alt,華康副體W7,48,&H00FFFFFF

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:05.00,Main,,0,0,0,,永和的中文字
Dialogue: 0,0:00:05.00,0:00:09.00,Alt,,0,0,0,,{\\fn華康行內體}行內字體{text
Comment: 0,0:00:09.00,0:00:10.00,Main,,0,0,0,,{\\fn華康註解體}註解不計字
Dialogue: 0,0:00:10.00,0:00:12.00,Ghost,,0,0,0,,幽靈樣式
"""


def _write(tmp_path, text=ASS, name="a.ass"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8-sig", newline="")
    return p


def test_extract(tmp_path):
    r = extract_usages(_write(tmp_path))
    by = {u.norm_name: u for u in r.usages}
    assert by["華康主體w5"].style_count == 1
    assert set(by["華康主體w5"].chars) >= {"永", "和", "的", "中", "文"}
    # 行内切换：行内体拿到行内的字
    assert by["華康行內體"].inline_count == 1
    assert "行" in by["華康行內體"].chars
    # Comment：引用但不计字
    assert "華康註解體" in by and len(by["華康註解體"].chars) == 0
    # 未知样式
    assert r.unknown_styles == ["Ghost"]


def test_empty_fontname_style(tmp_path):
    bad = ASS.replace("Style: Alt,華康副體W7,", "Style: Alt,,")
    r = extract_usages(_write(tmp_path, bad))
    assert "Alt" in r.invalid_styles


def test_merge(tmp_path):
    a = extract_usages(_write(tmp_path, name="a.ass"))
    b = extract_usages(_write(tmp_path, ASS.replace("永和的中文字", "另外的字"), "b.ass"))
    merged = merge_usages([a, b])
    m = {u.norm_name: u for u in merged}
    assert m["華康主體w5"].style_count == 2
    assert {"永", "另"} <= set(m["華康主體w5"].chars)
```

（测试里第二断言写松一点没关系，关键是行内体分到了字。）

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现（模板 + 对照 refs 校正属性名）**

`ass_font_substitute/core/assparse/extract.py`：

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from light_ass import Document

from ass_font_substitute.core.assparse.encoding import detect_encoding
from ass_font_substitute.core.fontindex.library import normalize_name
from ass_font_substitute.core.models import FontUsage


@dataclass
class ExtractResult:
    usages: list[FontUsage] = field(default_factory=list)
    unknown_styles: list[str] = field(default_factory=list)
    invalid_styles: list[str] = field(default_factory=list)  # Fontname 为空的样式
    parse_error: str | None = None


def extract_usages(path: Path) -> ExtractResult:
    data = Path(path).read_bytes()
    enc = detect_encoding(data)
    doc = Document.load(str(path), encoding=enc, strict=False)

    buckets: dict[str, FontUsage] = {}
    style_fonts = {name: st.fontname for name, st in doc.styles.items()}
    used_styles: set[str] = set()
    invalid_styles = [n for n, f in style_fonts.items() if not f]

    def bucket(raw: str) -> FontUsage:
        norm = normalize_name(raw)
        if norm not in buckets:
            buckets[norm] = FontUsage(raw_name=raw, norm_name=norm,
                                      style_count=0, inline_count=0, chars=set())
        return buckets[norm]

    for name, fontname in style_fonts.items():
        if fontname:
            bucket(fontname).style_count += 1

    for ev in doc.events:
        style_font = style_fonts.get(ev.style)
        if style_font is None:
            used_styles.add(ev.style)
            continue
        current = style_font
        is_comment = getattr(ev, "is_comment", False)
        for node in ev.parse_tags():  # 属性名对照 refs/light_ass 校正
            cls = type(node).__name__
            if cls == "FontNameTag":
                if node.value and node.value != "0":
                    current = node.value
                    if not is_comment:
                        bucket(current).inline_count += 1
                else:
                    current = style_font
            elif cls == "ResetStyleTag":
                current = style_fonts.get(node.value, style_font) if getattr(
                    node, "value", None) else style_font
            elif cls == "TextNode" and not is_comment:
                bucket(current).chars.update(node.value)
            # DrawingNode / EscapeNode 不计

    return ExtractResult(usages=list(buckets.values()),
                         unknown_styles=sorted(used_styles),
                         invalid_styles=invalid_styles)


def merge_usages(results: list[ExtractResult]) -> list[FontUsage]:
    out: dict[str, FontUsage] = {}
    for r in results:
        for u in r.usages:
            if u.norm_name not in out:
                out[u.norm_name] = FontUsage(u.raw_name, u.norm_name,
                                             u.style_count, u.inline_count,
                                             set(u.chars))
            else:
                m = out[u.norm_name]
                m.style_count += u.style_count
                m.inline_count += u.inline_count
                m.chars |= u.chars
    return list(out.values())
```

- [ ] **Step 4: 运行通过**（必要时按 refs 修属性名），Run: `uv run pytest tests/test_extract.py -v`

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(core): ASS 字体引用与用字集提取（light_ass）"
```

---

### Task 10: rewrite.py — 行级外科手术写回

**Files:**
- Create: `ass_font_substitute/core/assparse/rewrite.py`
- Test: `tests/test_rewrite.py`

**Interfaces:**
- Consumes: `detect_encoding`
- Produces:
  - `RewriteResult`（out_path: Path, changed_lines: list[int]）
  - `rewrite_file(src: Path, mapping: dict[str, str], out: Path | None = None, overwrite_src: bool = False) -> RewriteResult`
    - `mapping`：原样写法键值（不含 @ 前缀时自动补 `@旧 → @新`）
    - `out` 给定 = 另存（已存在则覆盖）；`overwrite_src=True` = 临时文件 + `os.replace` 原子替换；两者互斥，全空 = 不写盘仅返回计数（dry-run）

- [ ] **Step 1: 失败测试**

`tests/test_rewrite.py`：

```python
from ass_font_substitute.core.assparse.rewrite import rewrite_file

ASS = ("[Script Info]\r\n"
       "Title: t\r\n"
       "\r\n"
       "[V4+ Styles]\r\n"
       "Format: Name, Fontname, Fontsize\r\n"
       "Style: Main,華康主體W5,52\r\n"
       "Style: MainP,華康主體W5(P),52\r\n"
       "\r\n"
       "[Events]\r\n"
       "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\r\n"
       "Dialogue: 0,0:00:00.00,0:00:05.00,Main,,0,0,0,,{\\fn華康主體W5}甲{\\fn0}乙\r\n"
       "Comment: 0,0:00:05.00,0:00:06.00,Main,,0,0,0,,{\\fn華康主體W5}丙\r\n"
       "Dialogue: 0,0:00:06.00,0:00:08.00,Main,,0,0,0,,{\\fn華康主體W5X}不誤傷\r\n")


def _mk(tmp_path, enc="utf-8-sig"):
    p = tmp_path / "in.ass"
    p.write_bytes(ASS.encode(enc))
    return p


def test_exact_column_replace_no_prefix_hit(tmp_path):
    p = _mk(tmp_path)
    out = tmp_path / "out.ass"
    r = rewrite_file(p, {"華康主體W5": "思源黑体"}, out=out)
    text = out.read_text(encoding="utf-8-sig")
    assert "Style: Main,思源黑体,52" in text
    assert "華康主體W5(P)" in text          # (P) 不误伤
    assert "{\\fn思源黑体}甲" in text        # 行内替换
    assert "{\\fn華康主體W5X}" in text       # 前缀不误伤
    assert "Style: MainP,華康主體W5(P),52" in text
    assert len(r.changed_lines) == 3          # 1 Style + Dialogue + Comment


def test_encoding_preserved(tmp_path):
    for enc in ("utf-16", "gb18030"):
        p = _mk(tmp_path, enc)
        out = tmp_path / f"out_{enc}.ass"
        rewrite_file(p, {"華康主體W5": "思源黑体"}, out=out)
        data = out.read_bytes()
        assert data[:2] != b"\r\n"
        text = data.decode(enc)
        assert "Style: Main,思源黑体,52" in text
        assert "\r\n" in text


def test_at_variant(tmp_path):
    p = _mk(tmp_path)
    out = tmp_path / "out.ass"
    p2 = tmp_path / "at.ass"
    p2.write_bytes(ASS.replace("Main,華康主體W5,", "Main,@華康主體W5,").encode("utf-8-sig"))
    rewrite_file(p2, {"華康主體W5": "思源黑体"}, out=out)
    assert "Main,@思源黑体," in out.read_text(encoding="utf-8-sig")


def test_atomic_overwrite(tmp_path):
    p = _mk(tmp_path)
    before = p.read_bytes()
    rewrite_file(p, {"華康主體W5": "思源黑体"}, overwrite_src=True)
    text = p.read_text(encoding="utf-8-sig")
    assert "思源黑体" in text
    assert p.read_bytes() != before
    assert not list(tmp_path.glob("*.tmp*"))
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现**

`ass_font_substitute/core/assparse/rewrite.py`：

```python
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from ass_font_substitute.core.assparse.encoding import detect_encoding


@dataclass
class RewriteResult:
    out_path: Path | None
    changed_lines: list[int]


def _expand_at(mapping: dict[str, str]) -> dict[str, str]:
    m = dict(mapping)
    for k, v in mapping.items():
        if k.startswith("@"):
            continue
        m.setdefault("@" + k, "@" + v)
    return m


def _fn_pattern(name: str) -> re.Pattern:
    return re.compile(r"(\\fn" + re.escape(name) + r")(?=[\\}])")


def rewrite_file(src: Path, mapping: dict[str, str], out: Path | None = None,
                 overwrite_src: bool = False) -> RewriteResult:
    assert not (out and overwrite_src), "out 与 overwrite_src 互斥"
    src = Path(src)
    data = src.read_bytes()
    enc = detect_encoding(data)
    text = data.decode(enc)
    lines = text.splitlines(keepends=True)
    m = _expand_at(mapping)

    section = ""
    formats: dict[str, list[str]] = {}
    changed: list[int] = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped.lower()
            continue
        if stripped.lower().startswith("format:") and "styles" in section:
            formats[section] = [f.strip().lower() for f in
                                stripped[len("Format:"):].split(",")]
            continue
        if stripped.startswith("Style:") and "styles" in section:
            fmt = formats.get(section)
            if not fmt or "fontname" not in fmt:
                continue
            parts = stripped[len("Style:"):].split(",")
            fi = fmt.index("fontname")
            if fi < len(parts):
                cur = parts[fi].strip()
                if cur in m:
                    parts[fi] = m[cur]
                    newline = "Style:" + ",".join(parts)
                    lines[i] = line.replace(stripped, newline, 1)
                    changed.append(i)
            continue
        if stripped.startswith(("Dialogue:", "Comment:")) and "events" in section:
            # 只在 {...} 块内替换 \fn
            def fix_block(match: re.Match) -> str:
                block = match.group(0)
                new_block = block
                for old, new in m.items():
                    new_block = _fn_pattern(old).sub(r"\\fn" + new, new_block)
                return new_block
            new_line = re.sub(r"\{[^{}]*\}", fix_block, line)
            if new_line != line:
                lines[i] = new_line
                changed.append(i)

    result_text = "".join(lines)
    result_bytes = result_text.encode(enc)
    if overwrite_src:
        tmp = src.with_name(src.name + f".tmp{os.getpid()}")
        tmp.write_bytes(result_bytes)
        os.replace(tmp, src)
        return RewriteResult(src, changed)
    if out is not None:
        Path(out).write_bytes(result_bytes)
        return RewriteResult(Path(out), changed)
    return RewriteResult(None, changed)  # dry-run
```

- [ ] **Step 4: 运行通过**，Run: `uv run pytest tests/test_rewrite.py -v`

- [ ] **Step 5: 真实样例回归**

`tests/test_rewrite_real.py`：

```python
from pathlib import Path

from ass_font_substitute.core.assparse.rewrite import rewrite_file

REAL = next(Path("temp").glob("*.ass"), None)


def test_real_subtitle_surgical(tmp_path):
    if REAL is None:
        return  # 无样例环境跳过
    src = tmp_path / "real.ass"
    src.write_bytes(REAL.read_bytes())
    from ass_font_substitute.core.assparse.encoding import detect_encoding
    enc = detect_encoding(REAL.read_bytes())
    orig_lines = REAL.read_bytes().decode(enc).splitlines(keepends=True)

    # 从真实文件提一个 style 字体名做替换
    import re
    m = re.search(r"^Style:.*?,([^,]+),", "\n".join(orig_lines), re.M)
    name = m.group(1)
    r = rewrite_file(src, {name: "REPLACED_FONT"}, out=tmp_path / "o.ass")
    new_lines = (tmp_path / "o.ass").read_bytes().decode(enc).splitlines(keepends=True)
    assert len(new_lines) == len(orig_lines)
    diff = [i for i, (a, b) in enumerate(zip(orig_lines, new_lines)) if a != b]
    assert diff == r.changed_lines
    assert all("REPLACED_FONT" in new_lines[i] or name in orig_lines[i]
               for i in diff)
```

Run: `uv run pytest tests/test_rewrite_real.py -v`

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(core): 行级外科手术写回（全等替换+@映射+原子覆盖）"
```

---

### Task 11: matcher.py — 在库匹配、覆盖率、孪生检测

**Files:**
- Create: `ass_font_substitute/core/coverage/__init__.py`、`ass_font_substitute/core/coverage/matcher.py`
- Test: `tests/test_matcher.py`

**Interfaces:**
- Consumes: `LibraryIndex`、`FontEntity`、`FontUsage`
- Produces:
  - `coverage_detail(cmap: CmapRanges, chars: set[str]) -> tuple[float, list[str]]`（比例 + 缺字列表）
  - `find_fonts(lib, usage: FontUsage) -> list[FontEntity]`
  - `twin_convert(name: str, direction: str) -> str`（direction ∈ "t2s" / "s2t"）
  - `twin_candidates(lib, usage, direction) -> list[FontEntity]`
  - `classify_usage(usage, lib, threshold=0.98) -> UsageClass`（UsageClass: kind ∈ keep/twin/replace/missing、entities、coverage、missing_chars、twin_hit）

- [ ] **Step 1: 失败测试**

`tests/test_matcher.py`：

```python
from ass_font_substitute.core.coverage.matcher import classify_usage, twin_convert
from ass_font_substitute.core.fontindex.library import LibraryIndex, normalize_name
from ass_font_substitute.core.fontindex.store import open_db, update_index
from ass_font_substitute.core.models import FontUsage
from tests.fonts import build_font


def _usage(name, chars, styles=1):
    return FontUsage(name, normalize_name(name), styles, 0, set(chars))


def _lib(tmp_path):
    d = tmp_path / "lib"
    d.mkdir()
    # 简体替身：名称=华康测试体 W5，覆盖全部用字
    build_font(d / "sc.ttf", chars="永和的中文字", family="华康测试体 W5",
               extra_aliases={"zh-TW": "華康測試體W5"})
    # 繁体原字体：缺 "中"
    build_font(d / "tc.ttf", chars="永和的文字", family="華康原體W5")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    return LibraryIndex.open([d])


def test_twin_convert():
    assert twin_convert("華康測試體W5", "t2s") == "华康测试体 W5" or \
           twin_convert("華康測試體W5", "t2s").replace(" ", "") == "华康测试体W5"


def test_classify(tmp_path):
    lib = _lib(tmp_path)
    # 孪生命中：繁名转简 = 华康测试体 W5，覆盖达标
    r = classify_usage(_usage("華康測試體W5", "永和的中文字"), lib)
    assert r.kind == "twin"
    # 需替换：在库但缺字
    r2 = classify_usage(_usage("華康原體W5", "永和的中文字"), lib, threshold=0.98)
    assert r2.kind == "replace" and "中" in r2.missing_chars
    # 保留：在库且覆盖达标
    r3 = classify_usage(_usage("華康原體W5", "永和的文"), lib)
    assert r3.kind == "keep"
    # 不在库
    r4 = classify_usage(_usage("不存在體", "永"), lib)
    assert r4.kind == "missing"
    # 零用字 → 保留
    r5 = classify_usage(_usage("不存在體", ""), lib)
    assert r5.kind == "keep"
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现**

`ass_font_substitute/core/coverage/matcher.py`：

```python
from __future__ import annotations

from dataclasses import dataclass, field

import opencc

from ass_font_substitute.core.fontindex.library import FontEntity, LibraryIndex
from ass_font_substitute.core.models import CmapRanges, FontUsage

_CC = {"t2s": opencc.OpenCC("t2s"), "s2t": opencc.OpenCC("s2t")}


def twin_convert(name: str, direction: str) -> str:
    return _CC[direction].convert(name)


def coverage_detail(cmap: CmapRanges, chars: set[str]) -> tuple[float, list[str]]:
    if not chars:
        return 1.0, []
    missing = sorted(c for c in chars if not cmap.contains(ord(c)))
    return (len(chars) - len(missing)) / len(chars), missing


def find_fonts(lib: LibraryIndex, usage: FontUsage) -> list[FontEntity]:
    return lib.find_by_name(usage.norm_name)


@dataclass
class UsageClass:
    kind: str                 # keep / twin / replace / missing
    entities: list[FontEntity] = field(default_factory=list)
    coverage: float = 1.0
    missing_chars: list[str] = field(default_factory=list)
    twin_hit: FontEntity | None = None


def classify_usage(usage: FontUsage, lib: LibraryIndex, threshold: float = 0.98,
                   direction: str = "t2s") -> UsageClass:
    if not usage.chars:
        return UsageClass(kind="keep")
    entities = find_fonts(lib, usage)
    if entities:
        covs = [coverage_detail(e.cmap, usage.chars) for e in entities]
        best = max(range(len(entities)), key=lambda i: covs[i][0])
        ratio, missing = covs[best]
        if ratio >= threshold:
            return UsageClass("keep", entities, ratio, missing)
        return UsageClass("replace", entities, ratio, missing)
    # 不在库 → 孪生检测
    twin_name = twin_convert(usage.norm_name, direction)
    twins = lib.find_by_name(twin_name)
    for t in twins:
        ratio, missing = coverage_detail(t.cmap, usage.chars)
        if ratio >= threshold:
            return UsageClass("twin", [t], ratio, missing, twin_hit=t)
    return UsageClass("missing")
```

注：`FontUsage` 从 `ass_font_substitute.core.models` import（extract 里同源）。测试文件头部 import 改为：

```python
from ass_font_substitute.core.models import FontUsage
```

- [ ] **Step 4: 运行通过**，Run: `uv run pytest tests/test_matcher.py -v`

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(core): 在库匹配/覆盖率/孪生检测"
```

---

### Task 12: fingerprint + render.py — 逐字渲染与归一化

**Files:**
- Create: `ass_font_substitute/core/similarity/__init__.py`、`ass_font_substitute/core/similarity/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `fingerprint_of`（Task 7）
- Produces:
  - 常量 `TILE = 48`、`TARGET_INK = 36`
  - `RenderBundle`（tiles: dict[str, np.ndarray(uint8, 48×48)]、ink_median: float、available: frozenset[str]）
  - `render_bundle(path: Path, face_index: int, chars: str) -> RenderBundle`（两遍渲染：先 96px 测墨迹中位数，再按 `TARGET_INK/中位数` 比例逐字定尺寸渲染，pad 到 48×48；缺字跳过不渲 .notdef）

- [ ] **Step 1: 失败测试**

`tests/test_render.py`：

```python
import numpy as np

from ass_font_substitute.core.similarity.render import render_bundle
from tests.fonts import build_font


def test_render(tmp_path):
    p = tmp_path / "f.ttf"
    build_font(p, chars="永中木", family="X體")
    b = render_bundle(p, 0, "永中木缺")
    assert set(b.tiles) == {"永", "中", "木"}     # 缺字跳过
    assert b.available == frozenset("永中木")
    for t in b.tiles.values():
        assert t.shape == (48, 48) and t.dtype == np.uint8
        assert t.max() > 0                        # 方块字形非空
    assert b.ink_median > 0


def test_two_fonts_different_tiles(tmp_path):
    build_font(tmp_path / "a.ttf", chars="永", family="A")
    build_font(tmp_path / "b.ttf", chars="永", family="B")
    a = render_bundle(tmp_path / "a.ttf", 0, "永")
    b = render_bundle(tmp_path / "b.ttf", 0, "永")
    # 工厂字体同形，仅验证流水线；自比在 metrics 任务验证
    assert a.tiles["永"].shape == b.tiles["永"].shape
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现**

`ass_font_substitute/core/similarity/render.py`：

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import freetype
import numpy as np

TILE = 48
TARGET_INK = 36
BASE_PX = 96


@dataclass
class RenderBundle:
    tiles: dict[str, np.ndarray]
    ink_median: float
    available: frozenset[str]


def _render_char(face, ch: str, px: int) -> np.ndarray | None:
    face.set_pixel_sizes(0, px)
    try:
        face.load_char(ch, freetype.FT_LOAD_RENDER | freetype.FT_LOAD_TARGET_NORMAL)
    except Exception:
        return None
    bm = face.glyph.bitmap
    if bm.width == 0 or bm.rows == 0:
        return None
    arr = np.frombuffer(bytes(bm.buffer), dtype=np.uint8).reshape(bm.rows, bm.width)
    if not arr.any():
        return None
    return arr


def _ink_height(arr: np.ndarray) -> int:
    rows = np.where(arr.any(axis=1))[0]
    return int(rows[-1] - rows[0] + 1)


def _fit(arr: np.ndarray) -> np.ndarray:
    """裁墨迹 bbox，等比缩放到高度 TARGET_INK，居中 pad 到 TILE×TILE。"""
    cols = np.where(arr.any(axis=0))[0]
    rows = np.where(arr.any(axis=1))[0]
    crop = arr[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    h, w = crop.shape
    scale = TARGET_INK / h
    nw = max(1, round(w * scale))
    nh = TARGET_INK
    # 最近邻缩放（numpy 索引）
    ys = (np.arange(nh) * h // nh)
    xs = (np.arange(nw) * w // nw)
    small = crop[ys][:, xs]
    out = np.zeros((TILE, TILE), dtype=np.uint8)
    y0 = (TILE - nh) // 2
    x0 = (TILE - nw) // 2
    out[y0:y0 + nh, x0:x0 + nw] = small
    return out


def render_bundle(path: Path, face_index: int, chars: str) -> RenderBundle:
    face = freetype.Face(str(path).encode(), face_index) \
        if isinstance(str(path), str) and str(path).isascii() \
        else freetype.Face(str(path), face_index)
    # pass 1: 基准尺寸墨迹高度中位数
    heights = {}
    for ch in chars:
        arr = _render_char(face, ch, BASE_PX)
        if arr is not None:
            heights[ch] = _ink_height(arr)
    if not heights:
        return RenderBundle({}, 0.0, frozenset())
    median = float(np.median(sorted(heights.values())))
    tiles: dict[str, np.ndarray] = {}
    for ch in chars:
        px = max(8, round(BASE_PX * TARGET_INK / median)) if median else BASE_PX
        arr = _render_char(face, ch, px)
        if arr is None:
            if ch in heights:
                heights.pop(ch)
            continue
        tiles[ch] = _fit(arr)
    return RenderBundle(tiles, median, frozenset(tiles))
```

（freetype-py 的 Face 构造参数以本地版本为准微调；`render_bundle` 中 ascii 分支可简化为直接 `freetype.Face(str(path), face_index)`，Windows 路径默认支持。）

- [ ] **Step 4: 运行通过**，Run: `uv run pytest tests/test_render.py -v`

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(core): 逐字独立渲染与归一化 tile"
```

---

### Task 13: metrics.py — pHash + NCC + 交集评分

**Files:**
- Create: `ass_font_substitute/core/similarity/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: `RenderBundle`
- Produces:
  - `phash(tile: np.ndarray) -> int`（两级 Haar DWT 取 LL 8×8，对中值取 64bit）
  - `ncc(a, b) -> float`
  - `CHAR_WEIGHT(c: str) -> float`（CJK 1.0，其余 0.3）
  - `CompareResult`（score: float、size_ratio: float、n_chars: int）
  - `compare(a: RenderBundle, b: RenderBundle) -> CompareResult | None`（交集空 = None）

- [ ] **Step 1: 失败测试**

`tests/test_metrics.py`：

```python
import numpy as np

from ass_font_substitute.core.similarity.metrics import compare, ncc, phash
from ass_font_substitute.core.similarity.render import RenderBundle


def _bundle(tiles, ink=40.0):
    return RenderBundle(dict(tiles), ink, frozenset(tiles))


def test_phash_identical():
    rng = np.random.default_rng(0)
    t = rng.integers(0, 255, (48, 48)).astype(np.uint8)
    assert phash(t) == phash(t)
    assert phash(t) != phash(np.roll(t, 12, axis=1))


def test_ncc_self():
    t = np.arange(48 * 48, dtype=np.float64).reshape(48, 48)
    assert abs(ncc(t, t) - 1.0) < 1e-9


def test_compare_self_is_one():
    t = np.full((48, 48), 128, np.uint8)
    t[10:38, 10:38] = 200
    b = _bundle({"永": t, "8": t})
    r = compare(b, b)
    assert r.score > 0.99
    assert r.size_ratio == 1.0 and r.n_chars == 2


def test_compare_intersection_only():
    a = _bundle({"永": np.zeros((48, 48), np.uint8)})
    b = _bundle({"中": np.zeros((48, 48), np.uint8)})
    assert compare(a, b) is None  # 无交集 = 无法比较
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现**

`ass_font_substitute/core/similarity/metrics.py`：

```python
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ass_font_substitute.core.similarity.render import RenderBundle


def _dwt_ll(a: np.ndarray, levels: int = 2) -> np.ndarray:
    cur = a.astype(np.float64)
    for _ in range(levels):
        h, w = cur.shape
        lh, lw = h // 2, w // 2
        x = cur[: lh * 2, : lw * 2]
        ll = (x[0::2, 0::2] + x[0::2, 1::2] + x[1::2, 0::2] + x[1::2, 1::2]) / 4
        cur = ll
    return cur


def phash(tile: np.ndarray) -> int:
    ll = _dwt_ll(_resize12(tile))
    flat = ll.flatten()
    med = np.median(flat)
    bits = 0
    for v in flat[:64]:
        bits = (bits << 1) | (1 if v > med else 0)
    return bits


def _resize12(tile: np.ndarray) -> np.ndarray:
    # 48 -> 32（3/2 缩小，两级 DWT 后 8×8）
    ys = (np.arange(32) * 48 // 32)
    return tile[ys][:, ys]


def ncc(a: np.ndarray, b: np.ndarray) -> float:
    x, y = a.astype(np.float64), b.astype(np.float64)
    x, y = x - x.mean(), y - y.mean()
    denom = np.sqrt((x * x).sum() * (y * y).sum())
    if denom == 0:
        return 1.0 if np.array_equal(a, b) else 0.0
    return float((x * y).sum() / denom)


def CHAR_WEIGHT(c: str) -> float:
    return 1.0 if 0x2E80 <= ord(c) <= 0x9FFF or 0x3400 <= ord(c) else 0.3


def _hamming_sim(a: int, b: int) -> float:
    return 1 - bin(a ^ b).count("1") / 64


@dataclass
class CompareResult:
    score: float
    size_ratio: float
    n_chars: int


def compare(a: RenderBundle, b: RenderBundle) -> CompareResult | None:
    inter = set(a.tiles) & set(b.tiles)
    if not inter:
        return None
    total, wsum = 0.0, 0.0
    for ch in inter:
        ta, tb = a.tiles[ch], b.tiles[ch]
        s = 0.5 * _hamming_sim(phash(ta), phash(tb)) + 0.5 * max(ncc(ta, tb), 0.0)
        w = CHAR_WEIGHT(ch)
        total += s * w
        wsum += w
    size_ratio = b.ink_median / a.ink_median if a.ink_median else 1.0
    return CompareResult(total / wsum, size_ratio, len(inter))
```

- [ ] **Step 4: 运行通过**

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(core): pHash+NCC 交集评分"
```

---

### Task 14: cache.py — 全局缓存（渲染条 / 相似度对 / LRU / 迁移）

**Files:**
- Create: `ass_font_substitute/core/similarity/cache.py`
- Test: `tests/test_cache.py`

**Interfaces:**
- Produces:
  - `Cache(path: Path)`：`.get_render(fingerprint, text_ver, loader) -> RenderBundle`（惰性：miss 时调 `loader() -> RenderBundle` 并存）、`.get_pair(a, b, text_ver) -> CompareResult|None`、`.put_pair(a, b, text_ver, r)`、`.trim(max_bytes=500*1024*1024)`、`.clear_all()`、`.move_to(new_path, migrate=True)`、`.pair_count / render_bytes` 属性
  - 渲染条 BLOB = zlib(`np.savez_compressed` 到内存：tiles+ink+available)
  - 相似度对键归一（a<b 排序后入库）

- [ ] **Step 1: 失败测试**

`tests/test_cache.py`：

```python
import numpy as np

from ass_font_substitute.core.similarity.cache import Cache
from ass_font_substitute.core.similarity.metrics import CompareResult
from ass_font_substitute.core.similarity.render import RenderBundle


def _bundle(chars="永"):
    return RenderBundle({c: np.full((48, 48), 200, np.uint8) for c in chars},
                        40.0, frozenset(chars))


def test_render_lazy_and_hit(tmp_path):
    c = Cache(tmp_path / "cache.db")
    calls = []
    def loader():
        calls.append(1)
        return _bundle()
    b1 = c.get_render("fpA", "v1", loader)
    b2 = c.get_render("fpA", "v1", loader)
    assert len(calls) == 1 and set(b2.tiles) == set(b1.tiles)
    c.get_render("fpA", "v2", loader)  # 文字版本变 → miss
    assert len(calls) == 2


def test_pair_roundtrip_and_order(tmp_path):
    c = Cache(tmp_path / "cache.db")
    r = CompareResult(0.91, 1.08, 40)
    c.put_pair("zzz", "aaa", "v1", r)
    got = c.get_pair("aaa", "zzz", "v1")   # 键序无关
    assert got.score == 0.91 and got.size_ratio == 1.08


def test_trim_and_clear(tmp_path):
    c = Cache(tmp_path / "cache.db")
    for i in range(3):
        c.get_render(f"fp{i}", "v1", _bundle)
    assert c.render_bytes > 0
    c.trim(max_bytes=1)   # 强制淘汰
    assert c.pair_count == 0
    c.clear_all()


def test_move(tmp_path):
    p1, p2 = tmp_path / "a.db", tmp_path / "b" / "c.db"
    p2.parent.mkdir()
    c = Cache(p1)
    c.get_render("fp", "v1", _bundle)
    c.move_to(p2, migrate=True)
    assert p2.exists()
    c2 = Cache(p2)
    assert c2.get_render("fp", "v1", _bundle).tiles
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现**

`ass_font_substitute/core/similarity/cache.py`：

```python
from __future__ import annotations

import io
import sqlite3
import time
import zlib
from pathlib import Path

import numpy as np

from ass_font_substitute.core.similarity.metrics import CompareResult
from ass_font_substitute.core.similarity.render import RenderBundle

DDL = """
CREATE TABLE IF NOT EXISTS renders(
  fingerprint TEXT, text_ver TEXT, blob BLOB, nbytes INTEGER, used_at REAL,
  PRIMARY KEY(fingerprint, text_ver));
CREATE TABLE IF NOT EXISTS pairs(
  fpa TEXT, fpb TEXT, text_ver TEXT, score REAL, size_ratio REAL, n_chars INT,
  PRIMARY KEY(fpa, fpb, text_ver));
"""

DEFAULT_LIMIT = 500 * 1024 * 1024


class Cache:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=5)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(DDL)

    # ---- render ----
    def get_render(self, fingerprint: str, text_ver: str, loader) -> RenderBundle:
        row = self.conn.execute(
            "SELECT blob FROM renders WHERE fingerprint=? AND text_ver=?",
            (fingerprint, text_ver)).fetchone()
        if row:
            self.conn.execute(
                "UPDATE renders SET used_at=? WHERE fingerprint=? AND text_ver=?",
                (time.time(), fingerprint, text_ver))
            self.conn.commit()
            return _decode(row[0])
        bundle = loader()
        self._put_render(fingerprint, text_ver, bundle)
        return bundle

    def _put_render(self, fingerprint, text_ver, bundle: RenderBundle):
        blob = _encode(bundle)
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO renders VALUES (?,?,?,?,?)",
                (fingerprint, text_ver, blob, len(blob), time.time()))
        self.trim()

    # ---- pair ----
    def get_pair(self, a: str, b: str, text_ver: str) -> CompareResult | None:
        a, b = sorted((a, b))
        row = self.conn.execute(
            "SELECT score,size_ratio,n_chars FROM pairs "
            "WHERE fpa=? AND fpb=? AND text_ver=?", (a, b, text_ver)).fetchone()
        return CompareResult(*row) if row else None

    def put_pair(self, a: str, b: str, text_ver: str, r: CompareResult):
        a, b = sorted((a, b))
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO pairs VALUES (?,?,?,?,?,?)",
                              (a, b, text_ver, r.score, r.size_ratio, r.n_chars))

    # ---- 容量 ----
    @property
    def render_bytes(self) -> int:
        return self.conn.execute(
            "SELECT COALESCE(SUM(nbytes),0) FROM renders").fetchone()[0]

    @property
    def pair_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM pairs").fetchone()[0]

    def trim(self, max_bytes: int = DEFAULT_LIMIT):
        while self.render_bytes > max_bytes:
            row = self.conn.execute(
                "SELECT fingerprint, text_ver FROM renders "
                "ORDER BY used_at LIMIT 1").fetchone()
            if not row:
                return
            with self.conn:
                self.conn.execute(
                    "DELETE FROM renders WHERE fingerprint=? AND text_ver=?",
                    row)

    def clear_all(self):
        with self.conn:
            self.conn.execute("DELETE FROM renders")
            self.conn.execute("DELETE FROM pairs")

    def move_to(self, new_path: Path, migrate: bool = True):
        self.conn.close()
        if migrate:
            new_path.parent.mkdir(parents=True, exist_ok=True)
            self.path.rename(new_path)
        else:
            self.path.unlink(missing_ok=True)
        self.__init__(new_path)


def _encode(b: RenderBundle) -> bytes:
    buf = io.BytesIO()
    np.savez_compressed(buf, chars=np.array(sorted(b.tiles)),
                        **{f"t{i}": t for i, t in enumerate(b.tiles.values())})
    head = f"{b.ink_median}\x00".encode()
    return zlib.compress(head + buf.getvalue(), 6)


def _decode(blob: bytes) -> RenderBundle:
    raw = zlib.decompress(blob)
    head, body = raw.split(b"\x00", 1)
    ink = float(head)
    z = np.load(io.BytesIO(body))
    chars = [str(c) for c in z["chars"]]
    tiles = {c: z[f"t{i}"] for i, c in enumerate(chars)}
    return RenderBundle(tiles, ink, frozenset(chars))
```

注意 `_encode` 依赖 tiles 顺序稳定：`sorted(b.tiles)` 与 `b.tiles.values()` 遍历序一致（Python dict 保序），decode 侧按同序 zip——若测试抓到乱序，改为 `{c: t for c, t in zip(chars, b.tiles.values())}`。

- [ ] **Step 4: 运行通过**，Run: `uv run pytest tests/test_cache.py -v`

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(core): 全局缓存（渲染条/相似度对/LRU/迁移）"
```

---

### Task 15: decision — 决定记忆

**Files:**
- Create: `ass_font_substitute/core/decision/__init__.py`、`ass_font_substitute/core/decision/store.py`
- Test: `tests/test_decision.py`

**Interfaces:**
- Consumes: `Cache`（同一 cache.db 文件，独立表）
- Produces:
  - `record_decision(db_path: Path, norm_name: str, direction: str, fingerprint: str | None, keep: bool = False)`
  - `latest_decision(db_path: Path, norm_name: str, direction: str) -> tuple[str | None, bool] | None`（(fingerprint, keep)；无记录 None）

- [ ] **Step 1: 失败测试**

`tests/test_decision.py`：

```python
from ass_font_substitute.core.decision.store import latest_decision, record_decision


def test_record_and_latest(tmp_path):
    db = tmp_path / "cache.db"
    record_decision(db, "華康圓體w5", "t2s", "fp1")
    assert latest_decision(db, "華康圓體w5", "t2s") == ("fp1", False)
    # 只记最近一次
    record_decision(db, "華康圓體w5", "t2s", "fp2")
    assert latest_decision(db, "華康圓體w5", "t2s") == ("fp2", False)
    # 方向独立
    assert latest_decision(db, "華康圓體w5", "s2t") is None
    # 保留原字体名也可记
    record_decision(db, "新細明體", "t2s", None, keep=True)
    assert latest_decision(db, "新細明體", "t2s") == (None, True)
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现**

`ass_font_substitute/core/decision/store.py`：

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS decisions(
  norm_name TEXT, direction TEXT, fingerprint TEXT, keep INTEGER,
  updated_at REAL, PRIMARY KEY(norm_name, direction));
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(DDL)
    return conn


def record_decision(db_path: Path, norm_name: str, direction: str,
                    fingerprint: str | None, keep: bool = False):
    import time
    with _connect(db_path) as conn:
        conn.execute("INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?)",
                     (norm_name, direction, fingerprint, int(keep), time.time()))


def latest_decision(db_path: Path, norm_name: str, direction: str):
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT fingerprint, keep FROM decisions WHERE norm_name=? AND direction=?",
        (norm_name, direction)).fetchone()
    conn.close()
    return (row[0], bool(row[1])) if row else None
```

- [ ] **Step 4: 运行通过**

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(core): 替换决定记忆（名+方向键）"
```

---

### Task 16: search.py — 元数据粗排与可续搜索

**Files:**
- Create: `ass_font_substitute/core/similarity/search.py`
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: `LibraryIndex`、`Cache`、`render_bundle`、`compare`、`coverage_detail`、`fingerprint_of`
- Produces:
  - `HIGH_SIM = 0.70`
  - `SAMPLE_TEXT: str`（内置 v1 对比文字，约 40 个简繁同形字 + `8 A a 0 ?`；词表来自 spec §9，实现时从 spec 抄录同形字表）
  - `text_version(text: str) -> str`（sha1 前 8 位）
  - `rank_pool(target: FontEntity | None, pool: list[FontEntity], direction: str) -> list[FontEntity]`（分类匹配 + weight/width 差 + 目标方向独有探针命中率；target=None 时仅脚本偏好）
  - `class FontSearcher`：
    - `__init__(target: FontEntity | None, chars: set[str], lib: LibraryIndex, cache: Cache, sample_text: str, cov_threshold: float, n_pause: int = 5)`
    - `.next() -> Candidate | None`（推进一个候选；None = 搜索耗尽）
    - `.paused: bool`（攒够 n_pause 个相似度 ≥ HIGH_SIM）
    - `.high_sim_count: int`

- [ ] **Step 1: 失败测试**

`tests/test_search.py`：

```python
from ass_font_substitute.core.fontindex.library import LibraryIndex
from ass_font_substitute.core.fontindex.store import open_db, update_index
from ass_font_substitute.core.similarity.cache import Cache
from ass_font_substitute.core.similarity.search import FontSearcher, SAMPLE_TEXT
from tests.fonts import build_font


def _lib(tmp_path, n=4):
    d = tmp_path / "lib"
    d.mkdir()
    chars = "永中木水火土石田" + "8Aa0?"
    build_font(d / "target.ttf", chars=chars, family="目標圓體", psname="TGT")
    for i in range(n):
        build_font(d / f"c{i}.ttf", chars=chars, family=f"圓體{i}", psname=f"C{i}")
    build_font(d / "far.ttf", chars="8Aa0?", family="黑體遠", psname="FAR")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    return LibraryIndex.open([d])


def test_search_pauses_and_ranking(tmp_path):
    lib = _lib(tmp_path)
    cache = Cache(tmp_path / "cache.db")
    target = next(e for e in lib.entities() if e.psname == "TGT")
    s = FontSearcher(target, set("永中木水火土石田"), lib, cache,
                     sample_text="永中木水火土石田8Aa0?", cov_threshold=0.98)
    found = []
    while not s.paused:
        c = s.next()
        if c is None:
            break
        found.append(c)
    assert len(found) == 5                      # n_pause 暂停
    assert all(c.similarity is not None for c in found)
    # 同形工厂字体自高分；无 CJK 字形的远字体垫底或不出现
    assert found[0].coverage >= found[-1].coverage or \
           found[0].similarity >= found[-1].similarity


def test_resume_after_pause(tmp_path):
    lib = _lib(tmp_path, n=1)
    cache = Cache(tmp_path / "cache.db")
    target = next(e for e in lib.entities() if e.psname == "TGT")
    s = FontSearcher(target, set("永中木"), lib, cache,
                     sample_text="永中木8Aa0?", cov_threshold=0.98)
    while not s.paused:
        if s.next() is None:
            break
    s.paused = False                            # 用户点继续
    more = s.next()
    assert more is None or more.similarity is not None
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现**

`ass_font_substitute/core/similarity/search.py`：

```python
from __future__ import annotations

import hashlib
from typing import Iterator, Optional

from ass_font_substitute.core.coverage.matcher import coverage_detail
from ass_font_substitute.core.fontindex.library import FontEntity, LibraryIndex
from ass_font_substitute.core.fontindex.probes import PROBE_SC, PROBE_TC
from ass_font_substitute.core.models import Candidate
from ass_font_substitute.core.similarity.cache import Cache
from ass_font_substitute.core.similarity.metrics import compare
from ass_font_substitute.core.similarity.render import render_bundle

HIGH_SIM = 0.70
# 内置 v1：简繁同形字（spec §9 词表）+ 数字字母标点
SAMPLE_TEXT = ("永 中 木 水 火 山 田 石 目 心 手 白 的 我 你 他 它 好 行 生 "
               "同 尖 思 明 上 下 左 右 外 大 小 不 了 在 是 有 和 也 8 A a 0 ?")


def text_version(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def _probe_score(e: FontEntity, direction: str) -> float:
    probes = PROBE_SC if direction == "t2s" else PROBE_TC
    return e.cmap.hit_ratio(probes)


def rank_pool(target: Optional[FontEntity], pool: list[FontEntity],
              direction: str) -> list[FontEntity]:
    def meta_score(e: FontEntity) -> float:
        s = _probe_score(e, direction)
        if target is not None:
            if e.category == target.category:
                s += 1.0
            s -= abs(e.weight - target.weight) / 1000
            s -= abs(e.width - target.width) / 100
        return s
    return sorted(pool, key=meta_score, reverse=True)


class FontSearcher:
    def __init__(self, target: Optional[FontEntity], chars: set[str],
                 lib: LibraryIndex, cache: Cache, sample_text: str,
                 cov_threshold: float, n_pause: int = 5,
                 direction: str = "t2s"):
        self.target = target
        self.chars = chars
        self.lib = lib
        self.cache = cache
        self.sample_text = sample_text
        self.cov_threshold = cov_threshold
        self.n_pause = n_pause
        self.direction = direction
        self.paused = False
        self.high_sim_count = 0
        tver = text_version(sample_text)
        pool = [e for e in lib.entities()
                if target is None or e.fingerprint != target.fingerprint]
        pool = rank_pool(target, pool, direction)
        # 覆盖率 < 阈值的候选垫底（stable partition，不剔除）
        low = [e for e in pool if coverage_detail(e.cmap, chars)[0] < cov_threshold]
        high = [e for e in pool if e not in low]
        self._iter: Iterator[FontEntity] = iter(high + low)
        self._tver = tver
        self._tcache: dict[str, object] = {}

    def _bundle(self, e: FontEntity):
        def loader():
            return render_bundle(
                _entity_path(e), e.face_index, self.sample_text)
        return self.cache.get_render(e.fingerprint, self._tver, loader)

    def _target_bundle(self):
        if self.target is None:
            return None
        if "tgt" not in self._tcache:
            self._tcache["tgt"] = self._bundle(self.target)
        return self._tcache["tgt"]

    def next(self) -> Optional[Candidate]:
        if self.paused:
            return None
        e = next(self._iter, None)
        if e is None:
            return None
        cov, _missing = coverage_detail(e.cmap, self.chars)
        cand = Candidate(fingerprint=e.fingerprint, display_name=e.display_name,
                         coverage=cov, script=e.script, category=e.category,
                         weight=e.weight, source="recommend")
        tb = self._target_bundle()
        if tb is not None:
            cached = self.cache.get_pair(self.target.fingerprint, e.fingerprint,
                                         self._tver)
            r = cached if cached else compare(tb, self._bundle(e))
            if not cached and r is not None:
                self.cache.put_pair(self.target.fingerprint, e.fingerprint,
                                    self._tver, r)
            if r is not None:
                cand.similarity, cand.size_ratio, cand.n_chars = \
                    r.score, r.size_ratio, r.n_chars
                if r.score >= HIGH_SIM:
                    self.high_sim_count += 1
                    if self.high_sim_count >= self.n_pause:
                        self.paused = True
        return cand


def _entity_path(e: FontEntity) -> "Path":
    from pathlib import Path
    return Path(e.dir_path) / e.rel_path
```

（覆盖率 < 阈值的候选垫底已在 `__init__` 的 stable partition 中实现。）

- [ ] **Step 4: 运行通过**，Run: `uv run pytest tests/test_search.py -v`

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(core): 元数据粗排与可暂停续算搜索"
```

---

### Task 17: 端到端冒烟（真实字幕）

**Files:**
- Test: `tests/test_e2e_core.py`

**Interfaces:**
- Consumes: 全部前序模块
- Produces: 验证 core 全链路：索引 → 提取 → 分类 → 搜索 → 决定 → 写回

- [ ] **Step 1: 写测试**

`tests/test_e2e_core.py`：

```python
import re
from pathlib import Path

from ass_font_substitute.core.assparse.encoding import detect_encoding
from ass_font_substitute.core.assparse.extract import extract_usages
from ass_font_substitute.core.assparse.rewrite import rewrite_file
from ass_font_substitute.core.coverage.matcher import classify_usage
from ass_font_substitute.core.decision.store import latest_decision, record_decision
from ass_font_substitute.core.fontindex.library import LibraryIndex, normalize_name
from ass_font_substitute.core.fontindex.store import open_db, update_index
from ass_font_substitute.core.similarity.cache import Cache
from ass_font_substitute.core.similarity.search import FontSearcher
from tests.fonts import build_font

REAL = next(Path("temp").glob("*.ass"), None)


def test_core_e2e(tmp_path):
    if REAL is None:
        return
    # 1. 库：真实 Style 字体名建简体孪生 + 一个候选圆体
    text = REAL.read_bytes().decode(detect_encoding(REAL.read_bytes()))
    style_fonts = sorted(set(re.findall(r"^Style:.*?,([^,]+),", text, re.M)))
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    import opencc
    t2s = opencc.OpenCC("t2s")
    twin_chars = "".join(dict.fromkeys(re.findall(r"[\u4e00-\u9fff]", text)))[:200]
    for i, name in enumerate(style_fonts[:3]):
        sc = t2s.convert(name)
        build_font(lib_dir / f"twin{i}.ttf", chars=twin_chars, family=sc,
                   extra_aliases={"zh-TW": name})
    build_font(lib_dir / "cand.ttf", chars=twin_chars, family="候選圓體")
    conn = open_db(lib_dir / "fontindex.db")
    update_index(lib_dir, conn)
    lib = LibraryIndex.open([lib_dir])

    # 2. 提取 + 分类
    src = tmp_path / "in.ass"
    src.write_bytes(REAL.read_bytes())
    ex = extract_usages(src)
    assert ex.usages
    mapping: dict[str, str] = {}
    cache = Cache(tmp_path / "cache.db")
    for u in ex.usages:
        cls = classify_usage(u, lib)
        if cls.kind == "twin":
            mapping[u.raw_name] = cls.twin_hit.display_name
            record_decision(tmp_path / "cache.db", u.norm_name, "t2s",
                            cls.twin_hit.fingerprint)
        elif cls.kind == "replace":
            target = cls.entities[0]
            s = FontSearcher(target, u.chars, lib, cache,
                             sample_text="永中木水火同好8Aa0?",
                             cov_threshold=0.98, n_pause=3)
            cands = []
            while not s.paused:
                c = s.next()
                if c is None:
                    break
                cands.append(c)
            if cands:
                mapping[u.raw_name] = cands[0].display_name
        # keep / missing：不映射
    assert latest_decision(tmp_path / "cache.db",
                           normalize_name(style_fonts[0]), "t2s") is not None \
        or not mapping

    # 3. 写回 + 字节级验证
    if mapping:
        out = tmp_path / "out.ass"
        r = rewrite_file(src, mapping, out=out)
        assert r.changed_lines
        orig = REAL.read_bytes().decode(detect_encoding(REAL.read_bytes()))
        new = out.read_bytes().decode(detect_encoding(out.read_bytes()))
        ol, nl = orig.splitlines(keepends=True), new.splitlines(keepends=True)
        assert len(ol) == len(nl)
        for i, (a, b) in enumerate(zip(ol, nl)):
            if a != b:
                assert i in r.changed_lines
```

- [ ] **Step 2: 运行**，Run: `uv run pytest tests/test_e2e_core.py -v`，Expected: PASS

- [ ] **Step 3: 全量回归**，Run: `uv run pytest -q`，Expected: all passed

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "test: core 端到端冒烟（真实字幕）"
```

---

## 计划外说明

- GUI 另出独立计划（spec §10），依赖本计划全部接口；索引 generation 快照/陈旧提示、FontSize 建议值展示、嵌套目录告警 UI 呈现（`LibraryIndex.dir_overlaps()` 已提供数据）均归 GUI 计划。
- `refs/_smoke/poc_surgical_v2.py` 是 Task 10 的参考雏形，含实测结论，实现时对照。
- light_ass 精确属性名以 `refs/light_ass/` 源码为准（Task 9 Step 3 明示校正动作）。
