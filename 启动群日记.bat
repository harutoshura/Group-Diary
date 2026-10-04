@echo off
rem ===========================================================================
rem  QunRiJi ( group diary ) launcher
rem
rem  !! KEEP THIS FILE PURE ASCII AND CRLF LINE ENDINGS !!
rem
rem  Why: Chinese Windows (codepage 936) makes cmd.exe parse a .bat file as
rem  GBK.  If a UTF-8 Chinese character appears here, GBK decoding produces a
rem  stray lead byte that swallows the next character - or even the line break -
rem  and the script dies with:
rem      't' is not recognized as an internal or external command
rem      'ho.' is not recognized ...
rem  A "chcp 65001" line inside the file cannot help, because the file is
rem  parsed before that line ever runs.  So: no Chinese in this file.
rem  All Chinese messages live in app.py and README.md instead.
rem ===========================================================================

cd /d "%~dp0"

set "PY="
for %%P in (pythonw.exe) do if not defined PY set "PY=%%~$PATH:P"
if not defined PY for %%P in (pyw.exe) do if not defined PY set "PY=%%~$PATH:P"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python310\pythonw.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python310\pythonw.exe"
if not defined PY if exist "%ProgramFiles%\Python314\pythonw.exe" set "PY=%ProgramFiles%\Python314\pythonw.exe"
if not defined PY if exist "%ProgramFiles%\Python313\pythonw.exe" set "PY=%ProgramFiles%\Python313\pythonw.exe"
if not defined PY if exist "%ProgramFiles%\Python312\pythonw.exe" set "PY=%ProgramFiles%\Python312\pythonw.exe"
if not defined PY if exist "%ProgramFiles%\Python311\pythonw.exe" set "PY=%ProgramFiles%\Python311\pythonw.exe"
if not defined PY if exist "%ProgramFiles(x86)%\Python313\pythonw.exe" set "PY=%ProgramFiles(x86)%\Python313\pythonw.exe"
if not defined PY if exist "C:\Python313\pythonw.exe" set "PY=C:\Python313\pythonw.exe"
if not defined PY if exist "C:\Python312\pythonw.exe" set "PY=C:\Python312\pythonw.exe"
if not defined PY if exist "D:\Python313\pythonw.exe" set "PY=D:\Python313\pythonw.exe"

if not defined PY goto nopython

start "" "%PY%" "%~dp0app.py"
exit /b 0

:nopython
echo.
echo   Python 3 was not found on this computer.
echo.
echo   How to fix:
echo     1. Download Python 3 from  https://www.python.org/downloads/
echo     2. During setup, TICK the box "Add python.exe to PATH".
echo     3. Then double-click this file again.
echo.
echo   If Python IS installed but this still fails, open README.md for help.
echo.
start "" "%~dp0README.md"
pause
exit /b 1
