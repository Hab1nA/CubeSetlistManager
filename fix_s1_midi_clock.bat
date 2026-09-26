@echo off
rem Fix: Studio One 7 (Windows MIDI Services stack) cannot send MIDI to
rem loopMIDI/teVirtualMIDI ports until the Windows MIDI Service is restarted.
rem Double-click this file, approve the UAC prompt, then restart Studio One
rem and verify with:  py -u probe_s1.py clock "VJ Automation"
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
echo Done. Now restart Studio One and test the MIDI clock.
pause
