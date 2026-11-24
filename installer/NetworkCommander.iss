; Inno Setup script. Compile with: iscc /DSourceDir=<pyinstaller output> NetworkCommander.iss
#ifndef SourceDir
  #define SourceDir "..\backend\dist\NetworkCommander"
#endif
#define AppVer "0.1.1"
[Setup]
AppId={{6B2A7D1E-5C0B-4D3E-9A55-4E7B0C1D2F01}
AppName=Network Commander
AppVersion={#AppVer}
DefaultDirName={autopf}\NetworkCommander
DefaultGroupName=Network Commander
OutputDir=..\out
OutputBaseFilename=NetworkCommander-Setup-{#AppVer}
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\NetworkCommander.exe
[Tasks]
Name: "desktop"; Description: "Create a desktop shortcut"
Name: "autostart"; Description: "Start Network Commander automatically when I sign in to Windows"; Flags: unchecked
[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
[Icons]
Name: "{group}\Network Commander"; Filename: "{app}\NetworkCommander.exe"
Name: "{group}\Stop Network Commander"; Filename: "{app}\NetworkCommander.exe"; Parameters: "--stop"
Name: "{autodesktop}\Network Commander"; Filename: "{app}\NetworkCommander.exe"; Tasks: desktop
[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "NetworkCommander"; ValueData: """{app}\NetworkCommander.exe"" --no-browser"; Tasks: autostart; Flags: uninsdeletevalue
[Run]
Filename: "{app}\NetworkCommander.exe"; Description: "Start Network Commander now"; Flags: nowait postinstall skipifsilent
[UninstallRun]
Filename: "{app}\NetworkCommander.exe"; Parameters: "--stop"; Flags: runhidden skipifdoesntexist
; Your data (%LOCALAPPDATA%\NetworkCommander: database, encrypted credentials, logs) is NOT removed on uninstall.
