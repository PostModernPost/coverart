; Inno Setup script for Liner Notes (formerly Cover Art: same AppId, so it upgrades Cover Art in place). Built by .github/workflows/build.yml:
;   iscc /DAppVersion=1.2.3 packaging\installer.iss

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define AppName "Liner Notes"
#define AppExe "LinerNotes.exe"

[Setup]
AppId={{6E2B8C41-3F7A-4D2E-9B15-C0A7E1F4D8B3}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Adam Waitt
AppPublisherURL=https://github.com/PostModernPost/coverart
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=linernotes.ico
OutputDir=..\dist
OutputBaseFilename=LinerNotes-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Installs for the current user: no admin password needed
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
; Start-menu entry moves from "Cover Art" to "Liner Notes"
UsePreviousGroup=no

[Tasks]
Name: "desktopicon"; Description: "Put a Liner Notes icon on the desktop"
Name: "startup"; Description: "Start Liner Notes when Windows starts (it waits quietly by the clock)"

[Files]
Source: "..\dist\LinerNotes\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[InstallDelete]
; Leftovers from when the app was called Cover Art
Type: files; Name: "{app}\CoverArt.exe"
Type: files; Name: "{autodesktop}\Cover Art.lnk"
Type: filesandordirs; Name: "{autoprograms}\Cover Art"
Type: files; Name: "{autoprograms}\Cover Art.lnk"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "CoverArt"; Flags: deletevalue dontcreatekey
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "LinerNotes"; \
  ValueData: """{app}\{#AppExe}"" --quiet"; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "Open Liner Notes now"; Flags: nowait postinstall skipifsilent
; In-app updates run this installer silently with /updated=1: reopen the app afterwards
Filename: "{app}\{#AppExe}"; Parameters: "--updated"; Flags: nowait; Check: IsAppUpdate

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM {#AppExe} /F"; Flags: runhidden; RunOnceId: "StopLinerNotes"

[Code]
function IsAppUpdate: Boolean;
begin
  Result := ExpandConstant('{param:updated|0}') = '1';
end;
