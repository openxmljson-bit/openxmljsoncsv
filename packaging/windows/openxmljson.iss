; Inno Setup script for OPENXMLJSON (Windows installer).
;
; Wraps the PyInstaller one-folder build (dist\OPENXMLJSON\, produced by
;   pyinstaller packaging\openxmljson.spec
; ) into a per-user Setup.exe.
;
; Build:
;   ISCC.exe packaging\windows\openxmljson.iss
;   ISCC.exe /DAppVersion=1.2.3 packaging\windows\openxmljson.iss   (override version)
;
; Paths below are relative to THIS script's folder (packaging\windows), so
; ..\..\dist is the repo-root dist\ where PyInstaller writes its output.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define AppName "OPENXMLJSON"
#define AppPublisher "OPENXMLJSON"
#define AppExe "OPENXMLJSON.exe"
#define DistDir "..\..\dist\OPENXMLJSON"

[Setup]
; A stable AppId keeps upgrades/uninstalls tied to the same product.
; (Generate your own with Inno's "Create GUID" if you fork this app.)
AppId={{7C1E3F2A-9B44-4E67-8B3D-2F1A6C9D0E5B}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\..\dist
OutputBaseFilename={#AppName}-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Per-user install: no admin elevation, installs under %LOCALAPPDATA%.
PrivilegesRequired=lowest
WizardStyle=modern
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
  GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; The entire PyInstaller output folder (exe + Python runtime + Qt + native
; module). recursesubdirs/createallsubdirs pulls in every nested dependency.
Source: "{#DistDir}\*"; DestDir: "{app}"; \
  Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; \
  Flags: nowait postinstall skipifsilent
