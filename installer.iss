; Inno Setup 6 script for SENSIQ. Compiled by build.ps1 (ISCC.exe installer.iss).
; Per-user install: no administrator rights needed.

#define AppName "SENSIQ"
#define AppVersion "1.6.0"
#define AppExe "SENSIQ.exe"

[Setup]
AppId={{6A3E0F7C-2B4D-4E61-9B8A-5C1D7E2F9A40}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=SENSIQ
DefaultDirName={autopf}\SENSIQ
DefaultGroupName=SENSIQ
; versions up to 1.3.0 were called PII Guard and lived in ...\PII Guard: move to the new folder
UsePreviousAppDir=no
UsePreviousGroup=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=dist\installer
OutputBaseFilename=SENSIQ-Setup-{#AppVersion}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=no

[Tasks]
Name: "startup"; Description: "Start SENSIQ when I sign in to Windows"; GroupDescription: "Startup:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "dist\SENSIQ\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "demo\*"; DestDir: "{app}\demo"; Flags: ignoreversion recursesubdirs
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; leftovers of the old name (PII Guard, up to 1.3.0)
Type: filesandordirs; Name: "{autopf}\PII Guard"
Type: filesandordirs; Name: "{autoprograms}\PII Guard"
Type: files; Name: "{autodesktop}\PII Guard.lnk"

[Icons]
Name: "{group}\SENSIQ"; Filename: "{app}\{#AppExe}"
Name: "{group}\SENSIQ demo page"; Filename: "{app}\demo\demo.html"
Name: "{group}\SENSIQ self-test"; Filename: "{app}\{#AppExe}"; Parameters: "--selftest"
Name: "{group}\Uninstall SENSIQ"; Filename: "{uninstallexe}"
Name: "{autodesktop}\SENSIQ"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "PIIGuard"; Flags: deletevalue
Root: HKCU; Subkey: "Software\PIIGuard"; Flags: deletekey
Root: HKCU; Subkey: "Software\Google\Chrome\NativeMessagingHosts\com.piiguard.host"; Flags: deletekey
Root: HKCU; Subkey: "Software\Microsoft\Edge\NativeMessagingHosts\com.piiguard.host"; Flags: deletekey
Root: HKCU; Subkey: "Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\com.piiguard.host"; Flags: deletekey
Root: HKCU; Subkey: "Software\Chromium\NativeMessagingHosts\com.piiguard.host"; Flags: deletekey
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "SENSIQ"; ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: startup
; where safe copies go (the "Safe copies folder" page); the app keeps this in step with its settings
Root: HKCU; Subkey: "Software\SENSIQ"; ValueType: string; ValueName: "SafeCopiesFolder"; ValueData: "{code:GetSafeDir}"; Flags: uninsdeletekey
; the browser link (native messaging host) the app registers itself: removed on uninstall
Root: HKCU; Subkey: "Software\Google\Chrome\NativeMessagingHosts\com.sensiq.host"; Flags: dontcreatekey uninsdeletekey
Root: HKCU; Subkey: "Software\Microsoft\Edge\NativeMessagingHosts\com.sensiq.host"; Flags: dontcreatekey uninsdeletekey
Root: HKCU; Subkey: "Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\com.sensiq.host"; Flags: dontcreatekey uninsdeletekey
Root: HKCU; Subkey: "Software\Chromium\NativeMessagingHosts\com.sensiq.host"; Flags: dontcreatekey uninsdeletekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start SENSIQ now"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: WizardSilent
Filename: "{app}\demo\demo.html"; Description: "Open the demo page"; Flags: shellexec postinstall skipifsilent unchecked

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM {#AppExe} /F"; Flags: runhidden; RunOnceId: "StopSENSIQ"

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\SENSIQ"

[Code]
// SENSIQ sits in the tray and doesn't answer Windows' "please close" request, so an
// upgrade stops it directly before copying files (it is started again at the end).
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM SENSIQ.exe /IM PIIGuard.exe /F', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(1000);
  Result := '';
end;

// ---- "Safe copies folder" page ---------------------------------------------------------
var
  SafePage: TInputDirWizardPage;

function DefaultSafeDir(): String;
begin
  Result := ExpandConstant('{localappdata}\SENSIQ\Safe copies');
end;

procedure InitializeWizard;
var
  Prev: String;
begin
  SafePage := CreateInputDirPage(wpSelectTasks,
    'Safe copies folder', 'Where should SENSIQ save the safe copies of your files?',
    'When you choose to replace private details, SENSIQ saves the safe version of the file in this folder. ' +
    'Your original files are never changed.' + #13#10#13#10 +
    'Copies in the default folder are deleted after 24 hours. Copies in a folder you choose are kept until you delete them.' + #13#10#13#10 +
    'You can change this later: right-click the SENSIQ shield in the taskbar, then Safe copies folder.',
    False, '');
  SafePage.Add('');
  if not RegQueryStringValue(HKCU, 'Software\SENSIQ', 'SafeCopiesFolder', Prev) or (Prev = '') then
    if not RegQueryStringValue(HKCU, 'Software\PIIGuard', 'SafeCopiesFolder', Prev) then  { PII Guard <= 1.3.0 }
      Prev := '';
  if (Prev = '') or (CompareText(Prev, ExpandConstant('{localappdata}\PIIGuard\Safe copies')) = 0) then
    Prev := DefaultSafeDir();
  SafePage.Values[0] := Prev;
end;

function GetSafeDir(Param: String): String;
begin
  Result := SafePage.Values[0];
  if Result = '' then
    Result := DefaultSafeDir();
end;

