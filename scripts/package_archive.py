"""构建产物打绿色 7z：main.dist 全量（除 Data/）→ deployment/ 下命名包。

Data/ 是构建机首启数据（缓存/索引/设置），进包会把脏数据带给用户且泄露
构建机路径；分发目标首启自建。7z 而非 zip：PySide6 体量下再省 15–20 MB
（拍板 2026-11-01）。版本号读 pyproject。用法：构建后 uv run python
scripts/package_archive.py。"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import py7zr

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "deployment" / "main.dist"


def main() -> int:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8")
                             )["project"]["version"]
    out = ROOT / "deployment" / f"AssFontSubstitute-{version}-win64.7z"
    exe = DIST / "AssFontSubstitute.exe"
    if not exe.is_file():
        print(f"缺 {exe}：先跑 scripts/build_portable.bat")
        return 1
    files = sorted(p for p in DIST.rglob("*")
                   if p.is_file()
                   and p.relative_to(DIST).parts[0] != "Data")
    with py7zr.SevenZipFile(out, "w", filters=[
            {"id": py7zr.FILTER_LZMA2,
             "preset": 9 | py7zr.PRESET_EXTREME}]) as z:
        for p in files:
            z.write(p, arcname=p.relative_to(DIST))
    mb = out.stat().st_size / 1_048_576
    print(f"{out.name}  {mb:.0f} MB  ({len(files)} 文件)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
