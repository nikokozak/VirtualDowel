@echo off
rem Virtual Dowel - Copyright (c) 2026 Nikolai Kozak. All rights reserved. Proprietary - see LICENSE.txt.
rem Virtual Dowel - double-click ONCE after unzipping or moving this folder.
rem It checks Python and points vd_probe.sbp and vd_selftest.sbp at the folder this file is in.
call "%~dp0vd_align.bat" --install
echo.
pause
