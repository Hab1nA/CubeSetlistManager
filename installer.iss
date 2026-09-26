; Cube Setlist Manager 安装包（Inno Setup 6）
; 由 build.bat 调用：ISCC /DAppVer=0.11.0 installer.iss
; 双底座双安装包：常规编译=Cubase 版；加 /Ds1 =Studio One 版（独立
; AppId/安装目录/快捷方式名，可与 Cubase 版并存安装；首装预置 studioone
; 配置 config.json，已装过的保留用户配置）。
; 按用户安装（免管理员）：config.json/playlist.json 写 exe 同目录，
; 装到 Program Files 会因权限写不进去，故必须 {localappdata}。

#ifdef s1
#define AppName "Cube Setlist Manager S1"
#define AppDirName "CubeSetlistManagerS1"
#define AppId "CA6676E9-9731-4F52-9DA0-939FA46DBEDF"
#define PkgBase "CubeSetlistManager-S1-Setup"
#else
#define AppName "Cube Setlist Manager"
#define AppDirName "CubeSetlistManager"
#define AppId "C8F2EC15-371F-4C34-B8DB-9824E24E602A"
#define PkgBase "CubeSetlistManager-Setup"
#endif
; PyInstaller 产物目录固定（双安装包共用同一 exe，行为差异全走预置配置）
#define SrcDir "Cube Setlist Manager"
#ifndef AppVer
#define AppVer "0.0.0"
#endif

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVer}
AppVerName={#AppName} {#AppVer}
DefaultDirName={localappdata}\Programs\{#AppDirName}
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename={#PkgBase}-{#AppVer}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#AppName}\{#AppName}.exe

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; \
    GroupDescription: "附加图标："

[Files]
; 运行时真实数据（config.json/playlist.json/crash.log）不入包——
; 覆盖安装时 exe 目录里已有的用户数据原样保留
Source: "dist\{#SrcDir}\{#SrcDir}.exe"; \
    DestDir: "{app}\{#AppName}"; DestName: "{#AppName}.exe"; \
    Flags: ignoreversion
Source: "dist\{#SrcDir}\_internal\*"; \
    DestDir: "{app}\{#AppName}\_internal"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "config.example.json"; DestDir: "{app}\{#AppName}"; Flags: ignoreversion
#ifdef s1
; S1 版首装预置 config.json（daw=studioone+S1 默认路径）；升级不覆盖用户配置
Source: "config.s1.json"; DestDir: "{app}\{#AppName}"; DestName: "config.json"; \
    Flags: onlyifdoesntexist ignoreversion
#endif
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
; 翻谱 APP 的 APK：build.bat 已从 mobile/ 拷入，缺失时跳过该行由 Inno 自动处理
Source: "dist\{#SrcDir}\CubeRemote.apk"; DestDir: "{app}\{#AppName}"; \
    Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppName}\{#AppName}.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppName}\{#AppName}.exe"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\{#AppName}\{#AppName}.exe"; Description: "立即运行 {#AppName}"; \
    Flags: nowait postinstall skipifsilent
