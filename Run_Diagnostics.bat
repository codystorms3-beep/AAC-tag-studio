@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title AAC Tag Studio - diagnostics
set "PY="
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo No working Python 3 found. Install it from python.org first.
  pause
  exit /b 1
)
%PY% aac_tag.py --diagnose
echo.
echo Report saved to: %~dp0logs\diagnostics.txt
pause
