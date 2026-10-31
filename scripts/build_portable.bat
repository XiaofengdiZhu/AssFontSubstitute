@echo off
rem 绿色软件构建（pyside6-deploy standalone）：MSVC 环境 + 部署 + 打 zip。
rem 用法：scripts\build_portable.bat   （仓库根目录任意位置可跑）

setlocal
set ROOT=%~dp0..
cd /d "%ROOT%"

rem --- MSVC 环境（dumpbin 用于插件依赖分析，必须 vcvarsall 注入 PATH） ---
rem vswhere 定位最新 VS 安装（官方入口，装 VS 即自带），不写死盘符/版本
set VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe
if not exist "%VSWHERE%" (
    echo [build_portable] 未找到 vswhere：请安装 Visual Studio 2022（含 C++ 生成工具）1>&2
    exit /b 1
)
for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSROOT=%%i"
if not defined VSROOT (
    echo [build_portable] vswhere 未找到含 x64 C++ 工具链的 VS 实例 1>&2
    exit /b 1
)
call "%VSROOT%\VC\Auxiliary\Build\vcvarsall.bat" x64
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
