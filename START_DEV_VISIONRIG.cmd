@echo off
setlocal
cd /d "%~dp0"
title VisionRig DEV

set "PYTHONPATH=%~dp0src"
set "VISIONRIG_MODELRIG_BRIDGE=1"
set "VISIONRIG_MODELRIG_WORKER_URL=http://127.0.0.1:8099"

echo.
echo   VisionRig DEV
echo   ModelRig bridge: ENABLED
echo   Target: http://127.0.0.1:8099/experimental/consciousness/visionrig-event
echo.

python -m visionrig
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" echo VisionRig stoppede med exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
