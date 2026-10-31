@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title AAC Tag Studio - install dependencies
echo.
echo Installing packages from requirements.txt ...
echo.

set "PY="
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo No working Python 3 found. Install from python.org first.
  pause
  exit /b 1
)

echo Using: %PY%
%PY% -c "import sys; print(sys.executable)"
echo.

%PY% -m pip install --upgrade pip setuptools wheel
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Trying with --user ...
  %PY% -m pip install --user -r requirements.txt
)

echo.
echo Done. You can now run Launch_AAC_Tag_Studio.bat or Build_EXE.bat.
echo Optional: also install pyinstaller if you want to build an EXE:
echo   %PY% -m pip install "pyinstaller>=6.0"
echo.
pause
