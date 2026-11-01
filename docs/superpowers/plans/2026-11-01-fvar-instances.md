# fvar 可变字体实例枚举 + 字重错标补偿 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** fontindex 枚举可变字体（fvar）命名实例为独立实体，parser 统一修复「名字带重量词而 OS/2=400」的字重错标，渲染层按实例坐标实例化。

**Architecture:** 方案 A「实例=扩展面」（spec 已定）：parser 每 face 枚举命名实例为多行 FontRecord（inst_index 维度）；store 加列升 SCHEMA_VERSION 2（.bak+全量重建）；library 去重键加实例维度；FontEntity 带 var_axes；render 经 freetype set_var_design_coords 实例化；渲染缓存 ALGO_VER+1 全量失效。

**Tech Stack:** Python 3 / fontTools（fvar 读取、fontBuilder fixture）/ freetype-py（set_var_design_coords）/ SQLite / pytest。

**Spec:** `docs/superpowers/specs/2026-11-01-fvar-instances-and-weight-fix-design.md`（计划从 spec 论证，执行者须同读）。

## Global Constraints

- core/（`src/ass_font_substitute/core/`）零 Qt，禁止 import PySide6/qfluentwidgets。
- commit 格式 `type(scope): 中文描述`，正文写原因与教训。
- 测试命令 `uv run pytest`；lint `uv run ruff check .`（isort force-single-line，一行一个 import）。
- 每任务独立提交；旧码红验证（先跑新测试确认 FAIL 再实现）。
- 测试字体一律 `tests/fonts.py` 工厂生成，不依赖系统字体。
- SCHEMA_VERSION 1→2、cache ALGO_VER "2"→"3" 与全链同批落地（spec §5 单提交语义按任务拆分执行时，中途状态不发布即可；store 升版本的提交须在 parser 之后）。

---

### Task 1: VF 测试字体工厂

**Files:**
- Modify: `tests/fonts.py`
- Test: `tests/test_fonts_factory.py`（新建；或并入 Task 2 的 parser 测试文件头部——推荐独立文件，工厂是公共依赖）

**Interfaces:**
- Produces: `build_vf(path, chars, family, axes, instances, weight=400, psname=None, version="Version 1.000", extra_aliases=None)` —— `axes: list[dict]`（fontBuilder 口径：`{"name","tag","min","default","max"}`），`instances: list[dict]`（`{"name","location"}`）。后续所有任务用此工厂造 VF fixture。

- [ ] **Step 1: 写失败测试**

```python
"""tests/test_fonts_factory.py：VF 工厂冒烟。"""
from pathlib import Path

from fontTools.ttLib import TTFont


def test_build_vf_has_named_instances(tmp_path: Path):
    from tests.fonts import build_vf
    p = tmp_path / "vf.ttf"
    build_vf(p, "永", family="变化體",
             axes=[{"name": "Weight", "tag": "wght",
                    "min": 300, "default": 400, "max": 900}],
             instances=[{"name": "Light", "location": {"wght": 300}},
                        {"name": "Black", "location": {"wght": 900}}])
    tt = TTFont(str(p))
    fvar = tt["fvar"]
    assert [a.axisTag for a in fvar.axes] == ["wght"]
    assert len(fvar.instances) == 2
    # fontBuilder 自动登记实例名 name 记录，subfamilyNameID 指向它
    sub_ids = {i.subfamilyNameID for i in fvar.instances}
    got = {tt["name"].getDebugName(nid) for nid in sub_ids}
    assert got == {"Light", "Black"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_fonts_factory.py -v`
Expected: FAIL，`ImportError: cannot import name 'build_vf'`

- [ ] **Step 3: 实现 build_vf**

在 `tests/fonts.py` 的 `build_ttc` 之后追加（复用 `_make` 的字形/OS2/name 基建）：

```python
def build_vf(path: Path, chars: str, family: str = DEFAULT_FAMILY,
             axes: list[dict] | None = None,
             instances: list[dict] | None = None,
             weight: int = 400, psname: str | None = None,
             version: str = "Version 1.000",
             extra_aliases: dict | None = None) -> None:
    """最小可变字体：fvar 轴+命名实例，无 gvar（轮廓不随轴变——
    parser/index/渲染管道测试只依赖 fvar+name+cmap；轮廓级变异由
    freetype 自身保证，spec §6）。"""
    fb = _make({"chars": chars, "family": family, "weight": weight,
                "psname": psname, "version": version,
                "extra_aliases": extra_aliases})
    fb.setupFvar(axes or [], instances or [])
    fb.font.save(path)
```

注意：`_make` 返回的是 `fb.font`（TTFont），`setupFvar` 必须在 `setupOS2`/`setupPost` 之后、`save` 之前调用——若 `_make` 内部已 save 需重构为返回 font 由调用方 save（现实现 `_make(spec).save(path)` 在 `build_font` 内，`_make` 本身返回 font，可直接复用）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_fonts_factory.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/fonts.py tests/test_fonts_factory.py
git commit -m "test(fontindex): VF 测试字体工厂 build_vf（fvar 轴+命名实例）"
```

---

### Task 2: FontRecord 扩展 + parser 枚举命名实例

**Files:**
- Modify: `src/ass_font_substitute/core/models.py:103`（FontRecord）
- Modify: `src/ass_font_substitute/core/fontindex/parser.py`
- Test: `tests/test_parser_fontindex.py`（现名以仓库实际为准，grep `parse_font_file` 的测试文件）

**Interfaces:**
- Produces: `FontRecord.inst_index: int = 0`、`FontRecord.var_axes: tuple[float, ...] = ()`（按 fvar axes 声明序的全长坐标；静态/默认行为空）。
- Produces: parser 模块级 `_WEIGHT_WORDS: dict[str, int]`（Task 3 复用）。
- Consumes: `tests/fonts.py build_vf`（Task 1）。

- [ ] **Step 1: 写失败测试**

```python
def test_parse_vf_enumerates_named_instances(tmp_path):
    from tests.fonts import build_vf
    p = tmp_path / "vf.ttf"
    build_vf(p, "永宇", family="变化體",
             axes=[{"name": "Weight", "tag": "wght",
                    "min": 300, "default": 400, "max": 900}],
             instances=[{"name": "Light", "location": {"wght": 300}},
                        {"name": "Black", "location": {"wght": 900}}])
    recs = parse_font_file(p)
    assert len(recs) == 3                      # 默认 + Light + Black
    assert [r.inst_index for r in recs] == [0, 1, 2]
    dflt, light, black = recs
    assert dflt.var_axes == ()                 # 默认行走默认态
    assert light.var_axes == (300.0,) and light.weight == 300
    assert black.var_axes == (900.0,) and black.weight == 900
    # 默认行 weight=OS/2（400），width 沿用 OS/2（审查 B1：wdth 不折算）
    assert dflt.weight == 400
    assert light.width == black.width == dflt.width
    # 限定名别名：族名+子族名同 langID 配对（fixture 族名 zh-CN/zh-TW/en？）
    # fixture 默认族名记录语言以 _EXTRA_LANGS 为准——断言限定名存在即可
    assert any("Light" in a for a in light.aliases)
    assert any("Black" in a for a in black.aliases)
    # display ∈ aliases 不变量（审查 B3）
    assert light.display_name in light.aliases
    assert black.display_name in black.aliases
    # name_langs 置空（审查 B2：GUI 展示链走 display_name 回退）
    assert light.name_langs == {} and black.name_langs == {}
    # cmap 同文件共享（同一 CmapRanges 对象）
    assert light.cmap is dflt.cmap and black.cmap is dflt.cmap
    # psname/version 文件级沿用
    assert light.psname == dflt.psname and black.version == dflt.version


def test_parse_vf_skips_default_equal_and_nameless_instances(tmp_path):
    from tests.fonts import build_vf
    p = tmp_path / "vf.ttf"
    build_vf(p, "永", family="变化體",
             axes=[{"name": "Weight", "tag": "wght",
                    "min": 400, "default": 400, "max": 900}],
             instances=[{"name": "Regular", "location": {"wght": 400}},   # 坐标=默认 → 跳过
                        {"name": "Black", "location": {"wght": 900}}])
    recs = parse_font_file(p)
    assert [r.inst_index for r in recs] == [0, 1]
    assert recs[1].weight == 900


def test_parse_static_unchanged(tmp_path):
    from tests.fonts import build_font
    p = tmp_path / "s.ttf"
    build_font(p, "永", family="靜態體")
    r, = parse_font_file(p)
    assert r.inst_index == 0 and r.var_axes == ()


def test_parse_vf_missing_subfamily_name_skipped(tmp_path):
    # subfamilyNameID 指向不存在的 name 记录 → 该实例跳过（拍板：无名不合成）
    from tests.fonts import build_vf
    from fontTools.ttLib import TTFont
    p = tmp_path / "vf.ttf"
    build_vf(p, "永", family="变化體",
             axes=[{"name": "Weight", "tag": "wght",
                    "min": 300, "default": 400, "max": 900}],
             instances=[{"name": "Black", "location": {"wght": 900}}])
    tt = TTFont(str(p))
    tt["fvar"].instances[0].subfamilyNameID = 999   # 悬空 nameID
    tt.save(str(p))
    recs = parse_font_file(p)
    assert len(recs) == 1 and recs[0].inst_index == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_parser_fontindex.py -k vf -v`
Expected: FAIL（`inst_index` 属性不存在）

- [ ] **Step 3: 实现**

`models.py` FontRecord 末尾追加：

```python
    inst_index: int = 0   # fvar 命名实例序：0=默认实例/静态，1..N=命名实例
    var_axes: tuple[float, ...] = ()   # fvar 轴坐标（axes 声明序全长）；空=默认态
```

`parser.py`：

```python
import struct  # 顶部

def _subfamily_texts(name, nid: int) -> list[tuple[int, str]]:
    """subfamilyNameID 回查 name 表（全平台），[(langID, 文本)]；含 ID2
    常规回退——fvar 实例的 subfamilyNameID 常指向自建记录，缺失时查同值
    nameID 无意义，只按 nid 直查；记录缺失返回空。"""
    return [(r.langID, r.toUnicode()) for r in name.names
            if r.nameID == nid and r.toUnicode()]


def _instance_compose(family_recs: list[tuple[int, str]],
                      sub_recs: list[tuple[int, str]]) -> tuple[list[str], str]:
    """限定名别名 + display_name 同一路径生成（spec §1，审查 B2/B3）。
    family_recs 取显示链同源族名记录（ID16 优先，回退 ID1），子族按同
    langID 配对；子族缺该语言只出英文（0x409）限定名。返回
    (限定名列表, display_name)；无任何配对返回 ([], "")。"""
    sub_by_lang = dict(sub_recs)
    sub_default = sub_by_lang.get(0x409)
    names: list[str] = []
    display = ""
    for lang, fam in family_recs:
        sub = sub_by_lang.get(lang) or sub_default
        if not sub:
            continue
        full = f"{fam} {sub}"
        if full not in names:
            names.append(full)
    if names:
        # display = 显示链语言（family_recs 首条，与 _display_name 的
        # CJK 优先链同源序）配对出的那条；保证 display ∈ aliases
        display = names[0]
    return names, display
```

`parse_font_file` face 循环内、现 `out.append(FontRecord(...))` 改造为：

```python
                # —— 公共属性（默认/实例行共用）——
                base = dict(
                    dir_path=str(path.parent), rel_path=path.name,
                    face_index=idx, psname=psname, version=version,
                    aliases=aliases, weight=weight, width=width,
                    italic=italic, cmap=cmap,
                    script=classify_script(cmap),
                    category=classify_category(aliases),
                    display_name=_display_name(by_id, aliases),
                    family_names=_family_names(by_id),
                    name_langs=by_lang, name_records=tuple(records))
                fvar = tt.get("fvar")
                if fvar is None or not fvar.instances:
                    out.append(FontRecord(**base))
                    continue
                # —— VF：默认行 + 命名实例行 ——
                out.append(FontRecord(**base))     # inst_index=0, var_axes=()
                tags = [a.axisTag for a in fvar.axes]
                defaults = [float(a.defaultValue) for a in fvar.axes]
                # 限定名用的族名记录：ID16 优先回退 ID1（与 _display_name
                # 同序），非 @ 名，[(langID, 文本)]
                fam_recs = (by_lang.get(16) or by_lang.get(1)) or []
                for k, inst in enumerate(fvar.instances, start=1):
                    coords = tuple(float(inst.coordinates.get(t, dv))
                                   for t, dv in zip(tags, defaults))
                    if coords == tuple(defaults):
                        continue          # 坐标=默认：默认行已覆盖（S3）
                    sub_recs = _subfamily_texts(name, inst.subfamilyNameID)
                    if not sub_recs:
                        continue          # 子族名不可解析：无名不合成（拍板）
                    qual, disp = _instance_compose(fam_recs, sub_recs)
                    if not qual:
                        continue
                    w = int(coords[tags.index("wght")]) if "wght" in tags \
                        else weight
                    it = (italic
                          or ("slnt" in tags and coords[tags.index("slnt")] != 0)
                          or ("ital" in tags and coords[tags.index("ital")] == 1))
                    out.append(FontRecord(
                        **{**base,
                           "aliases": aliases + qual,
                           "display_name": disp,
                           "name_langs": {},        # B2：GUI 走 display 回退
                           "weight": w,
                           "italic": it,
                           "inst_index": k,
                           "var_axes": coords}))
```

（`struct` 若 Task 2 未用到则不留 import——打包在 store 层做。）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_parser_fontindex.py tests/test_fonts_factory.py -v`
Expected: PASS（存量静态用例不回归）

- [ ] **Step 5: 全量回归 + lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 全绿（store 尚不识别新字段——inst_index/var_axes 有默认值，写入 SQL 未带列，天然忽略）

- [ ] **Step 6: Commit**

```bash
git add src/ass_font_substitute/core/models.py src/ass_font_substitute/core/fontindex/parser.py tests/
git commit -m "feat(fontindex): parser 枚举 fvar 命名实例——限定名别名与 display 同路径生成"
```

---

### Task 3: 字重错标补偿（尾词限定）

**Files:**
- Modify: `src/ass_font_substitute/core/fontindex/parser.py`
- Test: `tests/test_parser_fontindex.py`

**Interfaces:**
- Consumes: Task 2 的 FontRecord 扩展。
- Produces: 补偿作用于所有行（含默认行/静态行）的最终 weight 值；VF 实例行豁免（weight 来自轴坐标）。

- [ ] **Step 1: 写失败测试**

```python
def test_weight_compensation_tail_word(tmp_path):
    from tests.fonts import build_font
    cases = [
        ("演示 Heavy", 400, 900),      # 错标修复
        ("演示 Bold", 400, 700),
        ("演示 Light", 400, 300),
        ("演示 Medium", 400, 500),
        ("演示 Heavy", 900, 900),      # 正确标注不动
        ("Black Ops One", 400, 400),   # 首词重量词不触发（S1 假阳）
        ("演示 Condensed Bold", 400, 700),   # 尾词命中
        ("CorbelLight", 400, 400),     # camelCase 无词边界不触发（假阴记账）
    ]
    for fam, w400, expect in cases:
        p = tmp_path / f"{fam.replace(' ', '_')}.ttf"
        build_font(p, "永", family=fam, weight=w400)
        r, = parse_font_file(p)
        assert r.weight == expect, (fam, r.weight)


def test_weight_compensation_not_for_vf_instances(tmp_path):
    from tests.fonts import build_vf
    p = tmp_path / "vf.ttf"
    # 族名带 Heavy 的 VF：默认行（OS/2 400）补偿→900；Black 实例行
    # weight=轴值 900，不受补偿二次改写
    build_vf(p, "永", family="重黑體",
             axes=[{"name": "Weight", "tag": "wght",
                    "min": 400, "default": 400, "max": 900}],
             instances=[{"name": "Black", "location": {"wght": 900}}])
    dflt, black = parse_font_file(p)
    assert dflt.weight == 900            # 默认行走补偿（统一规则）
    assert black.weight == 900 and black.inst_index == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_parser_fontindex.py -k weight_compensation -v`
Expected: FAIL（未补偿，weight==400）

- [ ] **Step 3: 实现**

`parser.py` 模块级：

```python
# 字重错标补偿词表（spec §1，OS/2 usWeightClass 规范值核验无误）。仅英文
# 词——CJK「黑体/粗」是类别语义。触发条件：ID1/4/16 文本名尾词（大小写
# 不敏感）命中 ∧ 该行 weight==400（审查 S1 尾词限定：对齐 Windows「族名
# +样式尾置」惯例，封「Black Ops One」（真 400 面）类假阳；camelCase
# 尾缀（CorbelLight）无词边界不触发——假阴记账 spec §8）
_WEIGHT_WORDS = {
    "thin": 100, "extralight": 200, "ultralight": 200, "light": 300,
    "medium": 500, "semibold": 600, "demibold": 600, "bold": 700,
    "extrabold": 800, "ultrabold": 800, "black": 900, "heavy": 900,
}


def _compensate_weight(base: dict) -> int:
    """错标补偿：名字尾词命中词表且 OS/2==400 → 按词表改写。多命中取
    最大（"… Light" 与 "… Bold" 并存时取 700）。"""
    if base["weight"] != 400:
        return base["weight"]
    hits = [w for nid in (1, 4, 16)
            for _lg, s in (base["name_langs"].get(nid) or [])
            for w in ([_WEIGHT_WORDS[s.rstrip().rsplit(" ", 1)[-1].lower()]]
                      if s.rstrip() and
                      s.rstrip().rsplit(" ", 1)[-1].lower() in _WEIGHT_WORDS
                      else [])]
    return max(hits) if hits else base["weight"]
```

`parse_font_file`：公共属性组装处（Task 2 的 `base = dict(...)`）把 `weight=weight` 换成 `weight=_compensate_weight({"weight": weight, "name_langs": by_lang})`（先算好再入 dict）；实例行 weight 覆盖不变（轴值优先，Task 2 已实现）。

- [ ] **Step 4: 跑测试确认通过 + 全量 + lint**

Run: `uv run pytest tests/test_parser_fontindex.py -v && uv run pytest -q && uv run ruff check .`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add src/ass_font_substitute/core/fontindex/parser.py tests/test_parser_fontindex.py
git commit -m "fix(fontindex): 名字尾词重量词∧OS/2=400 的字重错标补偿"
```

---

### Task 4: store 加列升 SCHEMA_VERSION 2

**Files:**
- Modify: `src/ass_font_substitute/core/fontindex/store.py`（DDL ~66-74、`_apply_result` ~235-289、`load_font_rows`、并行/串行路径指纹缓存读写、`SCHEMA_VERSION` 49）
- Test: `tests/test_fontindex_store.py`（grep `update_index` 的现有测试文件）

**Interfaces:**
- Consumes: Task 2 `FontRecord.inst_index/var_axes`。
- Produces: fonts 表列 `inst_index INTEGER NOT NULL DEFAULT 0`、`var_axes BLOB`（`struct.pack(f"<{n}f", *coords)`，空元组存 NULL）；fingerprints 主键 `(rel_path, face_index, inst_index)`；`load_font_rows` 产出行含 inst_index/var_axes（var_axes 还原为 `tuple[float,...]`，NULL→`()`）；`SCHEMA_VERSION = 2`。

- [ ] **Step 1: 写失败测试**

```python
def test_update_index_persists_instances(tmp_path):
    from tests.fonts import build_vf
    from ass_font_substitute.core.fontindex.store import (
        SCHEMA_VERSION, update_index)
    assert SCHEMA_VERSION == 2
    d = tmp_path / "lib"
    d.mkdir()
    build_vf(d / "vf.ttf", "永", family="变化體",
             axes=[{"name": "Weight", "tag": "wght",
                    "min": 300, "default": 400, "max": 900}],
             instances=[{"name": "Light", "location": {"wght": 300}},
                        {"name": "Black", "location": {"wght": 900}}])
    update_index(d, conn=None)   # 按现有测试的调用口径（注入 tmp conn 的照抄）
    rows = <SELECT fonts 全行>    # 按现有测试读法
    assert sorted(r["inst_index"] for r in rows) == [0, 1, 2]
    light = next(r for r in rows if r["inst_index"] == 1)
    assert light["weight"] == 300
    import struct
    assert struct.unpack("<1f", light["var_axes"]) == (300.0,)


def test_rescan_same_file_idempotent(tmp_path):
    # 同文件重扫：UNIQUE(rel_path, face_index, inst_index) 冲突不炸，
    # 行数不翻倍（_apply_result 先 DELETE 再插，天然幂等——回归锁）
    ...


def test_v1_db_backed_up_and_rebuilt(tmp_path):
    # 手工造 v1 结构库（CREATE TABLE 按 v1 DDL + schema_version=1），
    # open_db/装载路径触发 .bak 备份 + v2 重建
    ...
```

（测试写法照抄现有 store 测试的 conn 注入/读库口径，勿凭空造 API。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_fontindex_store.py -v -k "instance or v1"`
Expected: FAIL（无 inst_index 列 / SCHEMA_VERSION 仍为 1）

- [ ] **Step 3: 实现**

- DDL：fonts 表加 `inst_index INTEGER NOT NULL DEFAULT 0, var_axes BLOB` 两列，唯一约束改 `UNIQUE(rel_path, face_index, inst_index)`；fingerprints 表加 `inst_index INTEGER` 列、主键改三元组。
- `SCHEMA_VERSION = 1` → `2`（旧库经既有 .bak+全量重建路径迁移，审查 N3：`_quarantine` 覆盖既有 .bak 已在 spec §5 记账）。
- `_apply_result`：fonts INSERT 列表加 `inst_index, var_axes`，值 `(r.inst_index, struct.pack(f"<{len(r.var_axes)}f", *r.var_axes) if r.var_axes else None)`；fingerprints INSERT 加 `r.inst_index`。
- `load_font_rows`（SELECT fonts 全列）与并行路径 `_parse_task`/指纹缓存读（`SELECT rel_path, face_index, mtime_ns, fp FROM fingerprints`）相应加列；`_load_dir` 侧在 library（Task 5），此处只保证 store 侧读写口径完整。
- 指纹预计算（store.py:219 `_parse_task`）无需改：`fingerprint_of` 输入是逐 record 属性，实例行属性（别名/weight）已分化，fps 对齐 recs 自然成立。

- [ ] **Step 4: 跑测试确认通过 + 全量 + lint**

Run: `uv run pytest tests/test_fontindex_store.py -v && uv run pytest -q && uv run ruff check .`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add src/ass_font_substitute/core/fontindex/store.py tests/test_fontindex_store.py
git commit -m "feat(fontindex): 库结构升 v2——fonts/fingerprints 加实例维度"
```

---

### Task 5: library 聚合去重键 + FontEntity 扩展

**Files:**
- Modify: `src/ass_font_substitute/core/fontindex/library.py`（FontEntity ~50-70、`_load` ~105-138、`_load_dir` ~140-176）
- Test: `tests/test_fontindex_library.py`（grep `LibraryIndex` 的现有测试文件）

**Interfaces:**
- Consumes: Task 4 store 行结构（inst_index/var_axes 列）。
- Produces: `FontEntity.inst_index: int = 0`、`FontEntity.var_axes: tuple[float, ...] = ()`；去重键 `(psname 或别名并集, inst_index)`。

- [ ] **Step 1: 写失败测试**

```python
def test_same_psname_instances_survive_dedup(tmp_path):
    # 同一 VF 文件各实例共享 psname——去重键加 inst 维度后不得折叠
    from tests.fonts import build_vf
    d = tmp_path / "lib"
    d.mkdir()
    build_vf(d / "vf.ttf", "永", family="变化體",
             axes=[{"name": "Weight", "tag": "wght",
                    "min": 300, "default": 400, "max": 900}],
             instances=[{"name": "Light", "location": {"wght": 300}},
                        {"name": "Black", "location": {"wght": 900}}])
    li = LibraryIndex.open([d])
    ents = li.entities()
    assert len(ents) == 3
    assert {e.inst_index for e in ents} == {0, 1, 2}
    black = next(e for e in ents if e.inst_index == 2)
    assert black.weight == 900 and black.var_axes == (900.0,)
    # find_by_name：限定名命中实例、族名命中默认+实例全体
    assert any(e.inst_index == 2 for e in li.find_by_name("变化體 Black"))
    assert {e.inst_index for e in li.find_by_name("变化體")} >= {0, 1, 2}


def test_cross_dir_duplicate_vf_dedup_per_instance(tmp_path):
    # 两目录放同一 VF：按 (psname, inst_index) 对齐去重，保留目录序靠前
    ...
```

（`find_by_name` 的实际签名/归一化口径照抄现有测试。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_fontindex_library.py -v -k instance`
Expected: FAIL（实体被折叠成 1 个 / 无 inst_index 属性）

- [ ] **Step 3: 实现**

- `FontEntity` 加 `inst_index: int = 0`、`var_axes: tuple[float, ...] = ()`（默认值保存量测试构造点零改动）。
- `_load_dir`：SELECT/load_font_rows 行加 inst_index、var_axes（`struct.unpack(f"<{len(blob)//4}f", blob)` 还原，NULL→`()`）；指纹缓存字典键 `(rel, fi)` → `(rel, fi, inst)`；raw 元组追加两元素。
- `_load`：去重 `key = (ps if ps else "\x00".join(sorted(item[5])), inst)`；平局排序键加 inst 位；FontEntity 构造传入两新字段。
- `_by_alias` 别名索引机制不变（实例限定名经 normalize 自然成独立键）。

- [ ] **Step 4: 跑测试确认通过 + 全量 + lint**

Run: `uv run pytest tests/test_fontindex_library.py -v && uv run pytest -q && uv run ruff check .`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add src/ass_font_substitute/core/fontindex/library.py tests/test_fontindex_library.py
git commit -m "feat(fontindex): 聚合去重键加实例维度——同 psname 多实例共存"
```

---

### Task 6: render 实例化 + 调用链 + 缓存 ALGO_VER

**Files:**
- Modify: `src/ass_font_substitute/core/similarity/render.py:216`（render_bundle）、`render.py:288`（render_text_strip）
- Modify: `src/ass_font_substitute/core/similarity/search.py:59,236`
- Modify: `src/ass_font_substitute/core/similarity/pool.py:19-35`（worker 载荷 + gather 契约 docstring）
- Modify: `src/ass_font_substitute/gui/jobs.py:333`
- Modify: `src/ass_font_substitute/core/similarity/cache.py:44`（ALGO_VER "2"→"3"）
- Test: `tests/test_render.py`、`tests/test_similarity_cache.py`（按现有命名）

**Interfaces:**
- Consumes: Task 5 `FontEntity.var_axes`。
- Produces: `render_bundle(path, face_index, chars, var_axes=())`、`render_text_strip(..., var_axes=())`；pool worker 载荷 `(path, face_index, sample_text)` → `(path, face_index, var_axes, sample_text)`（以现有实际载荷为准，docstring 契约同步）。

- [ ] **Step 1: 写失败测试**

```python
def test_render_bundle_with_var_axes(tmp_path):
    from tests.fonts import build_vf
    p = tmp_path / "vf.ttf"
    build_vf(p, "永", family="变化體",
             axes=[{"name": "Weight", "tag": "wght",
                    "min": 300, "default": 400, "max": 900}],
             instances=[{"name": "Black", "location": {"wght": 900}}])
    b1 = render_bundle(p, 0, "永")                    # 默认态
    b2 = render_bundle(p, 0, "永", var_axes=(900.0,))  # 实例态
    assert b1.bundle is not None and b2.bundle is not None
    # 无 gvar 轮廓不随轴变——此处只锁「带坐标渲染不炸、产出位图」管道
    # 口径（spec §6）；两 bundle 结构有效即可
    assert len(b2.bundle.bitmaps) == len(b1.bundle.bitmaps) > 0


def test_render_partial_axes_rejected(tmp_path):
    # freetype 对部分长度坐标静默只设首轴（审查实测）——调用方须传全长；
    # 本层对长度不等于轴数的坐标直接抛错防呆
    from tests.fonts import build_vf
    p = tmp_path / "vf.ttf"
    build_vf(p, "永", family="变化體",
             axes=[{"name": "Weight", "tag": "wght",
                    "min": 300, "default": 400, "max": 900},
                   {"name": "Width", "tag": "wdth",
                    "min": 50, "default": 100, "max": 200}],
             instances=[])
    with pytest.raises(Exception):    # 按现有渲染错误契约折包后断言具体类型
        render_bundle(p, 0, "永", var_axes=(900.0,))   # 2 轴只给 1 值
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_render.py -v -k var_axes`
Expected: FAIL（`render_bundle` 不接受 var_axes 参数）

- [ ] **Step 3: 实现**

`render.py` 两个入口加参 `var_axes: tuple[float, ...] = ()`；`freetype.Face(fh, face_index)` 打开后：

```python
        if var_axes:
            if not face.get_var_axis_count() == len(var_axes):
                raise FontRenderError(  # 按现有错误类型折包
                    f"var_axes 长度 {len(var_axes)} != 轴数 "
                    f"{face.get_var_axis_count()}")
            face.set_var_design_coords([float(a) for a in var_axes])
```

（错误类型名以 render.py 现有契约为准；不用 `set_var_named_instance`——FreeType 实例序 0 起与 inst_index 0=默认态口径不一致，spec §4 S5 记账。）

调用链：search.py:59/236 传 `e.var_axes`；pool.py worker 解包新载荷；jobs.py:333 传 `entity.var_axes`；cache.py `ALGO_VER = "2"` → `"3"`（`_purge_stale_algo` 前缀机制自动清旧行）。

- [ ] **Step 4: 跑测试确认通过 + 全量 + lint**

Run: `uv run pytest tests/test_render.py tests/test_similarity_cache.py -v && uv run pytest -q && uv run ruff check .`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add src/ass_font_substitute/core/similarity/ src/ass_font_substitute/gui/jobs.py tests/
git commit -m "feat(similarity): 渲染层 fvar 实例化——var_axes 全链传递+ALGO_VER 升 3"
```

---

### Task 7: e2e + rank_pool 排序 + 真机验收清单

**Files:**
- Modify: `tests/test_gui_e2e.py`（或新建 `tests/test_fvar_e2e.py` 独立成文）
- Modify: `docs/gui-manual-tests.md`
- Test: 同上

**Interfaces:**
- Consumes: 全链（Task 1-6）。

- [ ] **Step 1: 写失败测试**

```python
def test_fvar_end_to_end_search(tmp_path):
    # 静态错标 Heavy + VF（Light/Black 实例）同库：
    # 1) 实体数与 weight 补偿正确
    # 2) find_by_name("变化體 Black") 命中实例实体
    # 3) rank_pool(target=错标静态体) 不再把 VF@900 压底
    #    （补偿后双方 weight 均 900，罚项 0）
    from tests.fonts import build_font, build_vf
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "heavy.ttf", "永宇", family="演示 Heavy", weight=400)
    build_vf(d / "vf.ttf", "永宇", family="变化體",
             axes=[{"name": "Weight", "tag": "wght",
                    "min": 300, "default": 400, "max": 900}],
             instances=[{"name": "Light", "location": {"wght": 300}},
                        {"name": "Black", "location": {"wght": 900}}])
    li = LibraryIndex.open([d])
    ents = li.entities()
    assert len(ents) == 4
    by_key = {(e.display_name, e.weight) for e in ents}
    assert ("演示 Heavy", 900) in by_key            # 补偿生效
    assert ("变化體 Black", 900) in by_key
    target = next(e for e in ents if e.display_name == "演示 Heavy")
    ranked = rank_pool(target, [e for e in ents
                                if e.fingerprint != target.fingerprint], "t2s")
    assert ranked[0].display_name == "变化體 Black"  # 同 900：探针/分类同分下不被字重罚项压底
```

（rank_pool 断言按实际探针得分微调——Black 实例与 target 的探针命中率/分类分可能不同；核心断言是「字重罚项为 0」，可改为直接断言 meta_score 差值。）

- [ ] **Step 2: 跑测试确认失败 → 实现补缺 → 通过**

Run: `uv run pytest tests/test_fvar_e2e.py -v`
Expected: 前六任务落地后应基本直接 PASS；若红，按失败点补链（多为 gui-manual-tests 或 rank 断言口径），最小修。

- [ ] **Step 3: 真机验收清单**

`docs/gui-manual-tests.md` 追加：

```markdown
## fvar 可变字体（v2 库）
- 首启库重建（v1→v2 .bak+全量）：1.3 万字体重扫耗时目测，无报错
- 库页/字体选择器：VF 实体逐实例成卡，展示名带子族（如「思源黑体 Heavy」），Light/Medium/Heavy 不再全显示族名
- 搜索：按「族名 子族名」与裸族名均可命中实例实体
- 替换预览：选 VF 实例为替补，预览条为实例字重轮廓（自渲染 set_var_design_coords）
- 须知：mpv/libass 系播放器写回 VF 实例名可能回落默认实例（spec §8 记账）；weight 被补偿改写的字体历史替换记忆失效一次，重选即重记
```

- [ ] **Step 4: 全量 + lint + Commit**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 全绿

```bash
git add tests/ docs/gui-manual-tests.md
git commit -m "test(similarity): fvar 全链 e2e——补偿排序/限定名检索/真机验收清单"
```

---

## Self-Review 记录

- Spec 覆盖：§1 枚举（Task 2）/补偿（Task 3）；§2 store（Task 4）；§3 library（Task 5）；§4 render+ALGO_VER（Task 6）；§5 迁移（Task 4 SCHEMA_VERSION，.bak 机制既有）；§6 测试 1-11 → Task 2(1)/Task 3(2,9)/Task 4(3)/Task 5(4)/Task 6(5,10)/Task 7(6,7)；§7 真机（Task 7 Step 3）；§8 限制无需任务。
- 占位符：Task 4/5 测试骨架处标注「照抄现有测试口径」——执行者先读对应测试文件再落笔，属防 API 漂移指示非占位。
- 类型一致：`inst_index: int`、`var_axes: tuple[float, ...]`（models→parser→store BLOB→library→render）全链同名同型；`build_vf` 签名唯一。
