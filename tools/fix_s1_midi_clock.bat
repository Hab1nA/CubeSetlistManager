@echo off
rem Restarts the Windows MIDI Service (midisrv). This is Microsoft's standard
rem remedy when virtual MIDI ports (loopMIDI etc.) go silent after the
rem Windows MIDI Services rollout.
rem NOTE: verified INEFFECTIVE for Studio One 7.2.3 clock output on the
rem reference machine (2026-09-27) - kept for other/ future manifestations.
rem After running this, restart Studio One so it re-opens its MIDI ports.
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
  exit /b
)
echo Restarting Windows MIDI Service (midisrv)...
net stop midisrv
net start midisrv
echo.
echo Done. Restart Studio One, then verify with:
echo   py -u probe_s1.py clock "VJ Automation"
pause
