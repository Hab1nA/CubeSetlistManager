@echo off
chcp 65001 >nul
cd /d %~dp0
rem snapshot runtime data (config/playlist) to _bak_dist, timestamped, never overwritten
py _snapshot_bak.py || goto :err
rem dist will be wiped by pyinstaller; backup runtime data first
if exist "dist\Cube Setlist Manager\config.json" (
  copy /y "dist\Cube Setlist Manager\config.json" dist\_config.bak >nul
  copy /y "dist\Cube Setlist Manager\playlist.json" dist\_playlist.bak >nul
) else (
  echo dist\Cube Setlist Manager missing config, fallback to root copies
  copy /y config.json dist\_config.bak >nul
  copy /y playlist.json dist\_playlist.bak >nul
)
pyinstaller "Cube Setlist Manager.spec" --noconfirm || goto :err
copy /y dist\_config.bak "dist\Cube Setlist Manager\config.json" >nul
copy /y dist\_playlist.bak "dist\Cube Setlist Manager\playlist.json" >nul
del dist\_config.bak dist\_playlist.bak >nul
rem APK for /app.apk download endpoint
if exist "mobile\app\build\outputs\apk\debug\app-debug.apk" (
  copy /y "mobile\app\build\outputs\apk\debug\app-debug.apk" ^
    "dist\Cube Setlist Manager\CubeRemote.apk" >nul
  echo APK included
) else (
  echo no APK found: run "cd mobile ^&^& gradlew assembleDebug" first
)
rem installer: version from latest git tag
set "APPVER=0.0.0"
for /f %%v in ('git describe --tags --abbrev^=0 2^>nul') do set "APPVER=%%v"
set "APPVER=%APPVER:v=%"
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup 6 not installed, skip installer
  exit /b 0
)
"%ISCC%" /DAppVer=%APPVER% installer.iss || goto :err
echo installer done: dist\CubeSetlistManager-Setup-%APPVER%.exe
exit /b 0
:err
echo BUILD FAILED (backups kept at dist\_config.bak / _playlist.bak)
exit /b 1
