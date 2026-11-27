@echo off
REM Daily inventory updater — call this from Windows Task Scheduler.
REM Logs go to logs\ folder.

cd /d "%~dp0\.."

REM If a venv exists, use it; otherwise fall back to system Python
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo [%DATE% %TIME%] Running inventory update...
%PY% -m core.update_inventory
set EXITCODE=%ERRORLEVEL%
echo [%DATE% %TIME%] Exit code: %EXITCODE%
exit /b %EXITCODE%
