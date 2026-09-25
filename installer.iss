; Cube Setlist Manager 安装包（Inno Setup 6）
; 由 build.bat 调用：ISCC /DAppVer=0.9.5 installer.iss
; 按用户安装（免管理员）：config.json/playlist.json 写 exe 同目录，
; 装到 Program Files 会因权限写不进去，故必须 {localappdata}。

#define AppName "Cube Setlist Manager"
#define AppDirName "CubeSetlistManager"
#ifndef AppVer
#define AppVer "0.0.0"
#endif

[Setup]
AppId=C8F2EC15-371F-4C34-B8DB-9824E24E602A
AppName={#AppName}
AppVersion={#AppVer}
AppVerName={#AppName} {#AppVer}
DefaultDirName={localappdata}\Programs\{#AppDirName}
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=CubeSetlistManager-Setup-{#AppVer}
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
Source: "dist\{#AppName}\{#AppName}.exe"; \
    DestDir: "{app}\{#AppName}"; Flags: ignoreversion
Source: "dist\{#AppName}\_internal\*"; \
    DestDir: "{app}\{#AppName}\_internal"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "config.example.json"; DestDir: "{app}\{#AppName}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
; 翻谱 APP 的 APK：build.bat 已从 mobile/ 拷入，缺失时跳过该行由 Inno 自动处理
Source: "dist\{#AppName}\CubeTurn.apk"; DestDir: "{app}\{#AppName}"; \
    Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppName}\{#AppName}.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppName}\{#AppName}.exe"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\{#AppName}\{#AppName}.exe"; Description: "立即运行 {#AppName}"; \
    Flags: nowait postinstall skipifsilent
