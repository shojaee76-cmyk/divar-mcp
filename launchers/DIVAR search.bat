@echo off
chcp 65001 >nul
title DIVAR search
setlocal

rem this file lives in <project>\launchers, so the project root is one level up
pushd "%~dp0.."
set "ROOT=%CD%"
set "PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" "%ROOT%\launch\divar_search.py" %*
popd

echo.
pause
endlocal
