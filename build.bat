@echo off
chcp 65001 >nul
cd /d %~dp0
rem pywebview: default desktop UI host; missing import falls back to old Tk silently, so fail here
py -c "import webview" 2>nul || (
  echo ERROR: pywebview not installed - run: py -m pip install pywebview
  exit /b 1
)
rem sv-ttk: UI theme runtime dep introduced by the ttk migration; fail fast, no silent fallback
py -c "import sv_ttk" 2>nul || (
  echo ERROR: sv_ttk not installed - run: py -m pip install sv_ttk
  exit /b 1
)
rem winrt: BLE MIDI backend runtime dep (keyboard automation over Bluetooth); fail fast, no silent fallback
py -c "import winrt.runtime" 2>nul || (
  echo ERROR: winrt packages not installed - run: py -m pip install winrt-runtime winrt-Windows.Foundation winrt-Windows.Foundation.Collections winrt-Windows.Devices.Enumeration winrt-Windows.Devices.Midi winrt-Windows.Storage.Streams
  exit /b 1
)
rem snapshot runtime data (config/playlist) to _bak_dist, timestamped, never overwritten
py tools\_snapshot_bak.py || goto :err
rem dist will be wiped by pyinstaller; backup runtime data first.
rem dist\Cube Setlist Manager（缺省回退仓库根恢复源）
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
rem smoke: boot packed exe, assert /app loads+nav+no JS error (guards silent Tk fallback)
py out\_smoke_dist.py || goto :err
rem APK for /app.apk download endpoint: release-signed (0.16.3+ official channel; debug build would break upgrade for release-signature users)
if exist "mobile\app\build\outputs\apk\release\app-release.apk" (
  copy /y "mobile\app\build\outputs\apk\release\app-release.apk" ^
    "dist\Cube Setlist Manager\CubeRemote.apk" >nul
  echo APK included
) else (
  echo no APK found: run "cd mobile ^&^& gradlew assembleRelease" first
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
