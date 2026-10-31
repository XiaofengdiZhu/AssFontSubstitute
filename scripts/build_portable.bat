@echo off
rem 绿色软件构建（pyside6-deploy standalone）：MSVC 环境 + 部署 + 打 zip。
rem 用法：scripts\build_portable.bat   （仓库根目录任意位置可跑）

setlocal
set ROOT=%~dp0..
cd /d "%ROOT%"

rem --- MSVC 环境（dumpbin 用于插件依赖分析，必须 vcvarsall 注入 PATH） ---
call "D:\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat" x64
if errorlevel 1 exit /b 1

uv run pyside6-deploy -f --no-install --keep-deployment-files --no-warn
if errorlevel 1 exit /b 1

rem exe 名来自入口文件名（main.py → main.exe），改名落拍板应用名
rem （standalone 独立 exe 重命名安全：DLL 按自身所在目录加载）
ren deployment\main.dist\main.exe AssFontSubstitute.exe
if errorlevel 1 exit /b 1

rem deploy 把 main.dist 再复制一份到 exec_directory（200MB 重复物）：
rem 只留 deployment\main.dist 一份，副本即删
if exist AssFontSubstitute.dist rmdir /s /q AssFontSubstitute.dist

echo.
echo === 构建完成：deployment\main.dist\AssFontSubstitute.exe ===

rem 打绿色 7z（Data/ 排除，版本号读 pyproject，输出 deployment\）
uv run python scripts/package_archive.py
