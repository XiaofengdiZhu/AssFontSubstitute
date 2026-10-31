@echo off
rem Portable build (pyside6-deploy standalone): MSVC env + deploy + 7z pack.
rem Keep this file pure ASCII: cmd parses .bat in ANSI codepage (GBK on zh
rem systems); UTF-8 Chinese comments desync byte pairing and merge lines.
rem Usage: scripts\build_portable.bat   (run from anywhere in the repo)

setlocal
set ROOT=%~dp0..
cd /d "%ROOT%"

rem --- MSVC env (dumpbin used by plugin dependency analysis; PATH injected
rem by vcvarsall). Locate latest VS via vswhere, no hardcoded drive letter. ---
set VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe
if not exist "%VSWHERE%" (
    echo [build_portable] vswhere not found: install VS2022 with C++ build tools 1>&2
    exit /b 1
)
for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSROOT=%%i"
if not defined VSROOT (
    echo [build_portable] vswhere found no VS instance with x64 C++ toolset 1>&2
    exit /b 1
)
call "%VSROOT%\VC\Auxiliary\Build\vcvarsall.bat" x64
if errorlevel 1 exit /b 1

uv run pyside6-deploy -f --no-install --keep-deployment-files --no-warn
if errorlevel 1 exit /b 1

rem exe name follows entry filename (main.py -> main.exe); rename to the
rem agreed app name (safe for standalone: DLLs load from their own dir)
ren deployment\main.dist\main.exe AssFontSubstitute.exe
if errorlevel 1 exit /b 1

rem deploy duplicates main.dist into exec_directory (~200MB redundant copy):
rem keep only deployment\main.dist, drop the duplicate
if exist AssFontSubstitute.dist rmdir /s /q AssFontSubstitute.dist

echo.
echo === build done: deployment\main.dist\AssFontSubstitute.exe ===

rem pack portable 7z (Data/ excluded, version from pyproject, out deployment\)
uv run python scripts/package_archive.py
