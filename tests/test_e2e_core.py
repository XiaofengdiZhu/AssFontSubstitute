"""端到端冒烟：索引 → 提取 → 分类 → 搜索 → 决定 → 写回。

样例字幕内联构造。曾用 temp/ 真实样例（glob 首个 .ass）：不入 git，
别人克隆即静默跳过测了个寂寞；且用户工作区语料漂移会打破「四类全出」
的固定分布断言（2026-10-30 实锤）。内联样例四类确定性全覆盖：
- keep：全覆蓋圓（在库全覆盖，含 \\fn 内联入桶）+ 閒置明體（样式定义
  无对白 → 零用字 keep）
- twin：繁體黑體 不在库，opencc 转简 繁体黑体 在库全覆盖（真实孪生
  路径，简体 family 无额外别名）
- replace：低覆蓋圓 在库但只录「永」，覆盖率不达标；转简不在库 → 走
  replace/搜索（否则搜索分支在本库形上不可达）
- missing：幽靈襯線 不在库、转简也无 → target=None 同一搜索器找替身
  （缺失字体找替身也是产品语义）
"""
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

_ALL = "永宇的好木水同"        # 全库字体统一用字（孪生/全覆盖/候选需覆盖对白全字）

_SAMPLE = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Def,繁體黑體,54,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1
Style: Low,低覆蓋圓,54,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1
Style: Ghost,幽靈襯線,54,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1
Style: Idle,閒置明體,54,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:02.00,Def,,0,0,0,,永宇的{\\fn全覆蓋圓}好
Dialogue: 0,0:00:02.00,0:00:03.00,Low,,0,0,0,,永宇木水
Dialogue: 0,0:00:03.00,0:00:04.00,Ghost,,0,0,0,,木水同好
"""


def test_core_e2e(tmp_path):
    raw = _SAMPLE.encode("utf-8")
    enc = detect_encoding(raw)
    src = tmp_path / "in.ass"
    src.write_bytes(raw)          # 字节直写：文本模式会把 \n 转 \r\n

    # ---- 1. 提取 ----
    ex = extract_usages(src)
    assert ex.usages
    assert not ex.parse_error
    assert any(u.style_count for u in ex.usages)          # Style 字体名入桶
    assert any(u.inline_count for u in ex.usages)         # {\fn...} 内联入桶

    # ---- 2. 索引：简体孪生 + 低覆盖受害体 + 全覆盖保留体 + 候选圆体 ----
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    build_font(lib_dir / "twin.ttf", chars=_ALL, family="繁体黑体")
    build_font(lib_dir / "lowcov.ttf", chars="永", family="低覆蓋圓")
    build_font(lib_dir / "keep.ttf", chars=_ALL, family="全覆蓋圓")
    build_font(lib_dir / "cand.ttf", chars=_ALL, family="候選圓體")
    conn = open_db(lib_dir / "AssFontSubstitute.db")
    update_index(lib_dir, conn)
    conn.close()
    lib = LibraryIndex.open([lib_dir])
    assert lib.stats()["fonts"] == 4

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
        # keep：不映射
    # 内联样例四类确定性全覆盖（曾用 temp/ 真实样例钉固定分布，语料
    # 漂移即碎）：四类各自至少一卡
    assert kinds == {"keep", "twin", "replace", "missing"}
    assert mapping  # R8：孪生/搜索映射必须非空
    for n in decided:
        d = latest_decision(tmp_path / "cache.db", normalize_name(n), "t2s")
        assert d is not None and d[0], n

    # ---- 4. 写回 + 字节级验证 ----
    out = tmp_path / "out.ass"
    r = rewrite_file(src, mapping, out=out)
    assert r.changed_lines
    assert detect_encoding(out.read_bytes()) == enc  # 编码原样保留
    orig = _SAMPLE
    new = out.read_bytes().decode(detect_encoding(out.read_bytes()))
    ol, nl = orig.splitlines(keepends=True), new.splitlines(keepends=True)
    assert len(ol) == len(nl)
    diff = [i for i, (a, b) in enumerate(zip(ol, nl, strict=False)) if a != b]
    assert diff == r.changed_lines  # 改动行集合与报告完全一致
    for i, (a, b) in enumerate(zip(ol, nl, strict=False)):
        if a != b:
            assert i in r.changed_lines  # 未报行绝不漂移（字节级）
