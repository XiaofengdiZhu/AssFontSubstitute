"""端到端冒烟：索引 → 提取 → 分类 → 搜索 → 决定 → 写回（真实字幕样例）。

temp/ 只读：一律在 tmp_path 上操作副本。样例缺失时静默跳过。
与任务书差异（见 report-H.md）：
- 孪生字体只带简体 family（无 extra_aliases），走「繁名不在库 → opencc 转简 →
  命中 → 覆盖率」的真实孪生路径（控制器 R8）；其用字取全文件去重字符，
  否则 200 字截断 + 标点缺席会让孪生覆盖率必然不达 0.98。
- 追加一个「在库但覆盖不足」的字体，否则 brief 的 replace/搜索分支在本库形
  上不可达，搜索阶段无法被冒烟。
- 分类为 missing 时以 target=None 走同一搜索器（探针粗排），缺失字体找替身
  也是产品语义。
"""
import re
from pathlib import Path

from ass_font_substitute.core.assparse.encoding import detect_encoding
from ass_font_substitute.core.assparse.extract import extract_usages
from ass_font_substitute.core.assparse.rewrite import rewrite_file
from ass_font_substitute.core.coverage.matcher import classify_usage
from ass_font_substitute.core.decision.store import latest_decision
from ass_font_substitute.core.decision.store import record_decision
from ass_font_substitute.core.fontindex.library import LibraryIndex
from ass_font_substitute.core.fontindex.library import normalize_name
from ass_font_substitute.core.fontindex.store import open_db
from ass_font_substitute.core.fontindex.store import update_index
from ass_font_substitute.core.similarity.cache import Cache
from ass_font_substitute.core.similarity.search import FontSearcher
from tests.fonts import build_font

REAL = next(Path("temp").glob("*.ass"), None)


def test_core_e2e(tmp_path):
    if REAL is None:
        return  # 无真实样例环境：跳过
    raw = REAL.read_bytes()
    enc = detect_encoding(raw)
    text = raw.decode(enc)

    # ---- 1. 提取（先于建库：需据真实用字挑 replace 受害者）----
    src = tmp_path / "in.ass"
    src.write_bytes(raw)  # temp/ 只读，副本上工作
    ex = extract_usages(src)
    assert ex.usages
    assert not ex.parse_error
    assert any(u.style_count for u in ex.usages)          # Style 字体名入桶
    assert any(u.inline_count for u in ex.usages)         # {\fn...} 内联入桶

    # ---- 2. 索引：前 3 个 Style 字体名建简体孪生（family=简体名，无额外别名）
    #         + 1 个在库但覆盖不足的繁体名（触发 replace/搜索）+ 1 个候选圆体
    style_fonts = sorted(set(re.findall(r"^Style:.*?,([^,]+),", text, re.M)))
    assert style_fonts
    import opencc
    t2s = opencc.OpenCC("t2s")
    # 孪生用字 = 全文件去重字符（文档序）：保证孪生覆盖真实用字（含标点）
    font_chars = "".join(dict.fromkeys(text))
    used_names = {u.raw_name for u in ex.usages if u.chars}
    victim = sorted(used_names - set(style_fonts[:3]))[0]

    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    for i, name in enumerate(style_fonts[:3]):
        build_font(lib_dir / f"twin{i}.ttf", chars=font_chars,
                   family=t2s.convert(name))
    build_font(lib_dir / "lowcov.ttf", chars="永", family=victim)
    build_font(lib_dir / "cand.ttf", chars=font_chars, family="候選圓體")
    conn = open_db(lib_dir / "fontindex.db")
    update_index(lib_dir, conn)
    lib = LibraryIndex.open([lib_dir])
    assert lib.stats()["fonts"] == 5

    # ---- 3. 分类 + 搜索 + 决定 ----
    mapping: dict[str, str] = {}
    decided: list[str] = []
    kinds: set[str] = set()
    cache = Cache(tmp_path / "cache.db")
    for u in ex.usages:
        cls = classify_usage(u, lib)
        kinds.add(cls.kind)
        if cls.kind == "twin":
            mapping[u.raw_name] = cls.twin_hit.display_name
            record_decision(tmp_path / "cache.db", u.norm_name, "t2s",
                            cls.twin_hit.fingerprint)
            decided.append(u.norm_name)
        elif cls.kind in ("replace", "missing"):
            target = cls.entities[0] if cls.kind == "replace" else None
            s = FontSearcher(target, u.chars, lib, cache,
                             sample_text="永中木水火同好8Aa0?",
                             cov_threshold=0.98, n_pause=3)
            cands = []
            while not s.paused:
                c = s.next()
                if c is None:
                    break
                cands.append(c)
            assert cands, f"搜索无候选：{u.raw_name}"
            mapping[u.raw_name] = cands[0].display_name
            record_decision(tmp_path / "cache.db", u.norm_name, "t2s",
                            cands[0].fingerprint)
            decided.append(u.norm_name)
            if cls.kind == "replace":
                assert any(c.similarity is not None for c in cands)
        # keep / missing（无候选时）：不映射
    # 真实样例语料随 temp/ 漂移（用户工作区，类别分布不固定，曾钉
    # 「8 keep/2 twin/1 replace/3 missing」四类全出已废）——只钉冒烟
    # 主干：replace/missing 必现（搜索+写回路径必走）、映射非空、决策
    # 可回放；twin 机制由 matcher 单测覆盖
    assert {"replace", "missing"} <= kinds
    assert mapping  # R8：孪生/搜索映射必须非空
    for n in decided:
        d = latest_decision(tmp_path / "cache.db", normalize_name(n), "t2s")
        assert d is not None and d[0], n

    # ---- 4. 写回 + 字节级验证 ----
    out = tmp_path / "out.ass"
    r = rewrite_file(src, mapping, out=out)
    assert r.changed_lines
    assert detect_encoding(out.read_bytes()) == enc  # 编码原样保留
    orig = raw.decode(enc)
    new = out.read_bytes().decode(detect_encoding(out.read_bytes()))
    ol, nl = orig.splitlines(keepends=True), new.splitlines(keepends=True)
    assert len(ol) == len(nl)
    diff = [i for i, (a, b) in enumerate(zip(ol, nl, strict=False)) if a != b]
    assert diff == r.changed_lines  # 改动行集合与报告完全一致
    for i, (a, b) in enumerate(zip(ol, nl, strict=False)):
        if a != b:
            assert i in r.changed_lines  # 未报行绝不漂移（字节级）
