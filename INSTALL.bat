@echo off
rem Virtual Dowel - Copyright (c) 2026 Nikolai Kozak. All rights reserved. Proprietary - see LICENSE.txt.
rem Double-click this file once after unzipping, and again after every update or move of the folder.
rem It needs Python 3.8 or newer from python.org, installed with "Add python.exe to PATH" ticked.
rem It points the ShopBot files at this folder and writes the custom cut (normally C80) into ShopBot.
setlocal
set "APP=%~dp0app"
if not exist "%APP%\vd_align.bat" set "APP=%~dp0."
call "%APP%\vd_align.bat" --install
set RC=%errorlevel%
echo.
if "%RC%"=="0" (
  echo ======================================================================
  echo  Installed. In ShopBot, type the custom cut shown above - normally C80
  echo  - to start Virtual Dowel.
  echo ======================================================================
) else (
  echo The install did not finish. See the messages above and app\vd_launch.log
)
echo.
pause
