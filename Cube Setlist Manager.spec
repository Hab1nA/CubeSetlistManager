# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['setlist_gui.py'],
    pathex=[],
    binaries=[],
    datas=[('app.ico', '.')],       # 窗口/任务栏图标（exe 图标见 EXE.icon）
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Cube Setlist Manager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,      # UPX 压缩 DLL 会拖慢杀软扫描/冷启动，且偶发误报损坏
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Cube Setlist Manager',
)
