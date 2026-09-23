@echo off
chcp 65001 >nul
cd /d %~dp0
rem 打包会清空 dist 子目录；exe 目录里的 config/playlist 是运行时真实数据，
rem 先备份（dist 根的副本可能过期，不作依据；缺失时才用根目录兜底）
if exist dist\工程播放台\config.json (
  copy /y dist\工程播放台\config.json dist\_config.bak >nul
  copy /y dist\工程播放台\playlist.json dist\_playlist.bak >nul
) else (
  echo dist\工程播放台 缺配置，用根目录副本兜底
  copy /y config.json dist\_config.bak >nul
  copy /y playlist.json dist\_playlist.bak >nul
)
pyinstaller 工程播放台.spec --noconfirm || goto :err
pyinstaller MIDI视频桥.spec --noconfirm || goto :err
copy /y dist\_config.bak dist\工程播放台\config.json >nul
copy /y dist\_playlist.bak dist\工程播放台\playlist.json >nul
copy /y dist\_config.bak dist\MIDI视频桥\config.json >nul
del dist\_config.bak dist\_playlist.bak >nul
echo 打包完成，配置已放回
exit /b 0
:err
echo 打包失败（数据备份保留在 dist\_config.bak / _playlist.bak）
exit /b 1
