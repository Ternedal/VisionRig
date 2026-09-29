@echo off
setlocal
cd /d "%~dp0"
title VisionRig DEV

set "PYTHONPATH=%~dp0src"
set "VISIONRIG_MODELRIG_BRIDGE=1"
set "VISIONRIG_MODELRIG_WORKER_URL=http://127.0.0.1:8099"
for /f "usebackq delims=" %%S in (`git rev-parse HEAD 2^>nul`) do set "VISIONRIG_GIT_SHA=%%S"
if not defined VISIONRIG_GIT_SHA (
  echo VisionRig DEV: kunne ikke bestemme git SHA fra checkout.
  exit /b 1
)

echo.
echo   VisionRig DEV
echo   ModelRig bridge: ENABLED
echo   Target: http://127.0.0.1:8099/experimental/consciousness/visionrig-event
echo   Revision: %VISIONRIG_GIT_SHA%
echo.

python -m visionrig
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" echo VisionRig stoppede med exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
