@echo off
chcp 65001 >nul
cd /d %~dp0
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
pyinstaller "VJ Automator.spec" --noconfirm || goto :err
copy /y dist\_config.bak "dist\Cube Setlist Manager\config.json" >nul
copy /y dist\_playlist.bak "dist\Cube Setlist Manager\playlist.json" >nul
copy /y dist\_config.bak "dist\VJ Automator\config.json" >nul
del dist\_config.bak dist\_playlist.bak >nul
echo 打包完成，配置已放回
exit /b 0
:err
echo 打包失败（数据备份保留在 dist\_config.bak / _playlist.bak）
exit /b 1
