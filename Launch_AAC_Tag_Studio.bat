@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title AAC Tag Studio v14.8 - launcher
if not exist "logs" mkdir "logs"
set "LOG=%~dp0logs\launcher.log"
>>"%LOG%" echo ===== %date% %time% =====

REM --- find a REAL Python (the "py" launcher first, then "python"). Ignores the Microsoft Store stub.
set "PY="
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY goto nopython

>>"%LOG%" echo Using interpreter: %PY%
echo Using Python: %PY%
%PY% aac_tag.py %*
set "RC=%errorlevel%"
>>"%LOG%" echo Exit code: %RC%

if not "%RC%"=="0" (
  echo.
  echo ============================================================
  echo  AAC Tag Studio stopped with exit code %RC%.
  echo  Full details: %~dp0logs\aac_tag.log
  echo  Tip: double-click Run_Diagnostics.bat and send me logs\diagnostics.txt
  echo ============================================================
  pause
)
exit /b %RC%

:nopython
>>"%LOG%" echo No working Python found
echo.
echo ============================================================
echo  No working Python 3 was found.
echo  - Install Python 3.11 or 3.12 from python.org and tick "Add python.exe to PATH"
echo  - If Python IS installed: Windows Settings ^> Apps ^> Advanced app settings
echo    ^> App execution aliases, and turn OFF the "python.exe" Store alias.
echo ============================================================
pause
exit /b 1
