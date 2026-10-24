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
    diff = [i for i, (a, b) in enumerate(zip(orig_lines, new_lines, strict=False)) if a != b]
    assert diff == r.changed_lines
    assert all("REPLACED_FONT" in new_lines[i] or name in orig_lines[i]
               for i in diff)
