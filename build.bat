@echo off
chcp 65001 >nul
cd /d %~dp0
rem 运行时真实数据快照：每次打包前把 config/playlist 追加快照到 _bak_dist
rem （带时间戳永不覆盖——运行时数据一旦丢失无法重建，这是唯一可靠的恢复源）
py -c "import os,shutil,time; ts=time.strftime('%%Y%%m%%d_%%H%%M%%S'); src=os.path.join('dist','Cube Setlist Manager'); os.makedirs('_bak_dist',exist_ok=True); [shutil.copy2(os.path.join(src,f), os.path.join('_bak_dist',ts+'_'+f)) for f in ('config.json','playlist.json') if os.path.exists(os.path.join(src,f))]" || goto :err
rem 打包会清空 dist 子目录；exe 目录里的 config/playlist 是运行时真实数据，
rem 先备份（dist 根的副本可能过期，不作依据；缺失时才用根目录兜底）
if exist "dist\Cube Setlist Manager\config.json" (
  copy /y "dist\Cube Setlist Manager\config.json" dist\_config.bak >nul
  copy /y "dist\Cube Setlist Manager\playlist.json" dist\_playlist.bak >nul
) else (
  echo dist\Cube Setlist Manager 缺配置，用根目录副本兜底
  copy /y config.json dist\_config.bak >nul
  copy /y playlist.json dist\_playlist.bak >nul
)
pyinstaller "Cube Setlist Manager.spec" --noconfirm || goto :err
copy /y dist\_config.bak "dist\Cube Setlist Manager\config.json" >nul
copy /y dist\_playlist.bak "dist\Cube Setlist Manager\playlist.json" >nul
del dist\_config.bak dist\_playlist.bak >nul
rem 翻谱 APP 的 APK 进 exe 目录（网页 /app.apk 下载端点的来源）
if exist "mobile\app\build\outputs\apk\debug\app-debug.apk" (
  copy /y "mobile\app\build\outputs\apk\debug\app-debug.apk" ^
    "dist\Cube Setlist Manager\CubeTurn.apk" >nul
  echo 已带上翻谱 APK
) else (
  echo 提示：mobile\ 下没有 APK（cd mobile ^&^& gradlew assembleDebug），/app.apk 将 404
)
echo 打包完成，配置已放回

rem 安装包：版本取最近 git tag（去 v 前缀），未安装 Inno Setup 则跳过
set "APPVER=0.0.0"
for /f %%v in ('git describe --tags --abbrev^=0 2^>nul') do set "APPVER=%%v"
set "APPVER=%APPVER:v=%"
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo 未安装 Inno Setup 6，跳过安装包（winget install JRSoftware.InnoSetup^）
  exit /b 0
)
"%ISCC%" /DAppVer=%APPVER% installer.iss || goto :err
echo 安装包完成：dist\CubeSetlistManager-Setup-%APPVER%.exe
exit /b 0
:err
echo 打包失败（数据备份保留在 dist\_config.bak / _playlist.bak）
exit /b 1
