@echo off
rem ===========================================================================
rem  QunRiJi - debug launcher (keeps a console window open with the log)
rem
rem  !! KEEP THIS FILE PURE ASCII AND CRLF LINE ENDINGS !!
rem  See the header of the main launcher .bat in this folder for the reason:
rem  on Chinese Windows, non-ASCII bytes in a .bat file break cmd.exe parsing.
rem  This file turns on UTF-8 *after* it has been parsed, so the Chinese log
rem  printed by app.py shows up correctly.
rem ===========================================================================

chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
title QunRiJi debug console

set "PY="
for %%P in (python.exe) do if not defined PY set "PY=%%~$PATH:P"
if not defined PY for %%P in (py.exe) do if not defined PY set "PY=%%~$PATH:P"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PY if exist "%ProgramFiles%\Python313\python.exe" set "PY=%ProgramFiles%\Python313\python.exe"
if not defined PY if exist "%ProgramFiles%\Python312\python.exe" set "PY=%ProgramFiles%\Python312\python.exe"
if not defined PY if exist "C:\Python313\python.exe" set "PY=C:\Python313\python.exe"
if not defined PY if exist "D:\Python313\python.exe" set "PY=D:\Python313\python.exe"

if not defined PY goto nopython

echo   Debug mode: this window shows the log. Closing it stops the program.
echo.
"%PY%" "%~dp0app.py"
echo.
echo   The program has exited. Press any key to close this window.
pause
exit /b 0

:nopython
echo.
echo   Python 3 was not found on this computer.
echo   See README.md for what to install.
echo.
pause
exit /b 1
