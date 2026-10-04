@echo off
chcp 65001 >nul
cd /d %~dp0
rem sv-ttk: UI theme runtime dep introduced by the ttk migration; fail fast, no silent fallback
py -c "import sv_ttk" 2>nul || (
  echo ERROR: sv_ttk not installed - run: py -m pip install sv_ttk
  exit /b 1
)
rem snapshot runtime data (config/playlist) to _bak_dist, timestamped, never overwritten
py tools\_snapshot_bak.py || goto :err
rem dist will be wiped by pyinstaller; backup runtime data first.
rem 三产品各自独立产物目录，数据备份/还原也各自对应。
rem 完整版(Cubase)：dist\Cube Setlist Manager Cubase（缺省回退仓库根恢复源）
if exist "dist\Cube Setlist Manager Cubase\config.json" (
  copy /y "dist\Cube Setlist Manager Cubase\config.json" dist\_config.bak >nul
  copy /y "dist\Cube Setlist Manager Cubase\playlist.json" dist\_playlist.bak >nul
) else (
  echo dist\Cube Setlist Manager Cubase missing config, fallback to root copies
  copy /y config.json dist\_config.bak >nul
  copy /y playlist.json dist\_playlist.bak >nul
)
rem 完整版(Studio One)：dist\Cube Setlist Manager Studio One（缺省回退预置）
if exist "dist\Cube Setlist Manager Studio One\config.json" (
  copy /y "dist\Cube Setlist Manager Studio One\config.json" dist\_s1_config.bak >nul
) else (
  echo dist\Cube Setlist Manager Studio One missing config, fallback to preset copy
  copy /y config.studioone.json dist\_s1_config.bak >nul
)
rem 简化版：dist\Cube Automator Studio One（缺省回退预置）
if exist "dist\Cube Automator Studio One\config.json" (
  copy /y "dist\Cube Automator Studio One\config.json" dist\_auto_config.bak >nul
) else (
  echo dist\Cube Automator Studio One missing config, fallback to preset copy
  copy /y config.automator.json dist\_auto_config.bak >nul
)
pyinstaller "Cube Setlist Manager Cubase.spec" --noconfirm || goto :err
copy /y dist\_config.bak "dist\Cube Setlist Manager Cubase\config.json" >nul
copy /y dist\_playlist.bak "dist\Cube Setlist Manager Cubase\playlist.json" >nul
pyinstaller "Cube Setlist Manager Studio One.spec" --noconfirm || goto :err
copy /y dist\_s1_config.bak "dist\Cube Setlist Manager Studio One\config.json" >nul
pyinstaller "Cube Automator Studio One.spec" --noconfirm || goto :err
copy /y dist\_auto_config.bak "dist\Cube Automator Studio One\config.json" >nul
del dist\_config.bak dist\_playlist.bak dist\_s1_config.bak dist\_auto_config.bak >nul
rem APK for /app.apk download endpoint
if exist "mobile\app\build\outputs\apk\debug\app-debug.apk" (
  copy /y "mobile\app\build\outputs\apk\debug\app-debug.apk" ^
    "dist\Cube Setlist Manager Cubase\CubeRemote.apk" >nul
  copy /y "mobile\app\build\outputs\apk\debug\app-debug.apk" ^
    "dist\Cube Setlist Manager Studio One\CubeRemote.apk" >nul
  copy /y "mobile\app\build\outputs\apk\debug\app-debug.apk" ^
    "dist\Cube Automator Studio One\CubeRemote.apk" >nul
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
echo installer done: dist\CubeSetlistManager-Cubase-Setup-%APPVER%.exe
"%ISCC%" /DAppVer=%APPVER% /Ds1 installer.iss || goto :err
echo installer done: dist\CubeSetlistManager-StudioOne-Setup-%APPVER%.exe
rem Cube Automator Studio One（简化版自动化集线器：独立 exe，预置 automator 配置）
"%ISCC%" /DAppVer=%APPVER% /Dauto installer.iss || goto :err
echo installer done: dist\CubeAutomator-StudioOne-Setup-%APPVER%.exe
exit /b 0
:err
echo BUILD FAILED (backups kept at dist\_config.bak / _playlist.bak / _s1_config.bak / _auto_config.bak)
exit /b 1
