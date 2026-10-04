@echo off
rem Virtual Dowel - Copyright (c) 2026 Nikolai Kozak. All rights reserved. Proprietary - see LICENSE.txt.
rem Virtual Dowel - runs vd_align.py when you double-click it, and runs the installer.
rem ShopBot does not use this file: its SHELL cannot start a .bat, so vd_probe.sbp starts python.exe itself.
rem Every start is added to vd_launch.log next to this file, so a failed start can be diagnosed.
setlocal
set "VD_FROM_BAT=1"
set "HERE=%~dp0"
set "LOG=%HERE%vd_launch.log"
echo. >> "%LOG%"
echo %date% %time%  vd_align.bat %* >> "%LOG%"
call :findpython
if not defined PY goto nopython
echo using %PY% >> "%LOG%"
%PY% "%HERE%vd_align.py" %*
set RC=%errorlevel%
echo exit code %RC% >> "%LOG%"
if /i "%~1"=="--install" exit /b %RC%
if not "%RC%"=="0" if not "%RC%"=="3" (
  echo.
  echo The alignment script reported a problem - see the messages above.
  echo A copy of what happened is in %LOG%
  pause
)
exit /b %RC%

:findpython
rem Test that Python really RUNS (Windows ships a fake python.exe that only opens the Store), and
rem also try the usual launcher locations in case the PATH that ShopBot passes on is out of date.
set "PY="
for %%P in ("py" "%SystemRoot%\py.exe" "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe") do (
  if not defined PY (
    "%%~P" -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>nul && set "PY="%%~P" -3"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>nul && set "PY=python"
)
goto :eof

:nopython
echo Python 3.8 or newer not found >> "%LOG%"
echo.
echo Python 3.8 or newer was not found.
echo Install it from https://www.python.org/downloads/windows/
echo and tick "Add python.exe to PATH" in the installer. Then run vd_install.bat.
echo.
if /i not "%~1"=="--install" pause
exit /b 2
