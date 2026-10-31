; Cube Setlist Manager 安装包（Inno Setup 6）
; 由 build.bat 调用：ISCC /DAppVer=X installer.iss（常规=完整版）；
; /Dauto =Cube Automator Studio One（简化版自动化集线器，独立 exe）。
; 两产品独立 AppId/安装目录/快捷方式，可并存安装；完整版首装不预置
; config.json（缺失时程序按默认值起），已装过的保留用户配置。
; 按用户安装（免管理员）：config.json/playlist.json 写 exe 同目录，
; 装到 Program Files 会因权限写不进去，故必须 {localappdata}。

#if defined(auto)
#define AppName "Cube Automator Studio One"
#define AppDirName "CubeAutomatorStudioOne"
#define AppId "6F2B9D41-8C57-4A30-9E7B-D48C2F5A31E7"
#define PkgBase "CubeAutomator-StudioOne-Setup"
#define SrcDir "Cube Automator Studio One"
#else
#define AppName "Cube Setlist Manager"
#define AppDirName "CubeSetlistManager"
#define AppId "C8F2EC15-371F-4C34-B8DB-9824E24E602A"
#define PkgBase "CubeSetlistManager-Setup"
#define SrcDir "Cube Setlist Manager"
#endif
; PyInstaller 产物目录/exe 名=AppName（两产品各一个产物目录）；完整版
; AppId 沿用旧 Cubase 版=已装用户原地升级，目录名变化仅影响新装默认路径。
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
UninstallDisplayIcon={app}\{#AppName}.exe

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; \
    GroupDescription: "附加图标："

[Files]
; 运行时真实数据（config.json/playlist.json/crash.log）不入包——
; 覆盖安装时 exe 同目录已有的用户数据原样保留
Source: "dist\{#SrcDir}\{#SrcDir}.exe"; \
    DestDir: "{app}"; DestName: "{#AppName}.exe"; \
    Flags: ignoreversion
Source: "dist\{#SrcDir}\_internal\*"; \
    DestDir: "{app}\_internal"; \
    Excludes: ".mimosa"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "config.example.json"; DestDir: "{app}"; Flags: ignoreversion
#if defined(auto)
; Automator 版首装预置 config.json（daw=studioone+三端口联动）；升级不覆盖
Source: "config.automator.json"; DestDir: "{app}"; DestName: "config.json"; \
    Flags: onlyifdoesntexist ignoreversion
#endif
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
; 翻谱 APP 的 APK：build.bat 已从 mobile/ 拷入，缺失时跳过该行由 Inno 自动处理
Source: "dist\{#SrcDir}\CubeRemote.apk"; DestDir: "{app}"; \
    Flags: ignoreversion skipifsourcedoesntexist

[InstallDelete]
; 合一版原地升级清理：AppId 沿用旧 Cubase 版保原地升级，旧 exe 名与
; 新名不同会残留成双图标/双入口——装新文件前删两类旧名
Type: files; Name: "{app}\Cube Setlist Manager Cubase.exe"
Type: files; Name: "{app}\Cube Setlist Manager S1.exe"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppName}.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\{#AppName}.exe"; Description: "立即运行 {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[Code]
// 旧布局迁移：0.11.0-prerelease.2 及之前装在 {app} 下的中性子目录
// "Cube Setlist Manager" 里（config.json/playlist.json/crash.log）。本轮起
// exe 直装 {app} 根——装文件前把旧子目录的用户数据搬上来（搬完预置
// config 的 onlyifdoesntexist 才不会盖住真实数据），装完再清掉旧子目录。
procedure CurStepChanged(CurStep: TSetupStep);
var
    oldDir: string;
begin
    if CurStep = ssInstall then begin
        oldDir := ExpandConstant('{app}\Cube Setlist Manager');
        if DirExists(oldDir) and
           (not FileExists(ExpandConstant('{app}\config.json'))) then begin
            if FileCopy(oldDir + '\config.json',
                        ExpandConstant('{app}\config.json'), False) then
                DeleteFile(oldDir + '\config.json');
            if FileCopy(oldDir + '\playlist.json',
                        ExpandConstant('{app}\playlist.json'), False) then
                DeleteFile(oldDir + '\playlist.json');
            if FileCopy(oldDir + '\crash.log',
                        ExpandConstant('{app}\crash.log'), False) then
                DeleteFile(oldDir + '\crash.log');
        end;
    end;
    if CurStep = ssPostInstall then begin
        oldDir := ExpandConstant('{app}\Cube Setlist Manager');
        { 旧子目录还在且确实是旧布局（有我们的任一旧 exe 名）才整树清除 }
        if DirExists(oldDir) and
           (FileExists(oldDir + '\Cube Setlist Manager.exe') or
            FileExists(oldDir + '\Cube Setlist Manager Cubase.exe') or
            FileExists(oldDir + '\Cube Setlist Manager Studio One.exe') or
            FileExists(oldDir + '\Cube Setlist Manager S1.exe')) then
            DelTree(oldDir, True, True, True);
    end;
end;
