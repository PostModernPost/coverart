; Inno Setup script for Cover Art. Built by .github/workflows/build.yml:
;   iscc /DAppVersion=1.2.3 packaging\installer.iss

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define AppName "Cover Art"
#define AppExe "CoverArt.exe"

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
SetupIconFile=coverart.ico
OutputDir=..\dist
OutputBaseFilename=CoverArt-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Installs for the current user: no admin password needed
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force

[Tasks]
Name: "desktopicon"; Description: "Put a Cover Art icon on the desktop"
Name: "startup"; Description: "Start Cover Art when Windows starts (it waits quietly by the clock)"

[Files]
Source: "..\dist\CoverArt\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "CoverArt"; \
  ValueData: """{app}\{#AppExe}"" --quiet"; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "Open Cover Art now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM {#AppExe} /F"; Flags: runhidden; RunOnceId: "StopCoverArt"
