; Inno Setup 6 script for PII Guard. Compiled by build.ps1 (ISCC.exe installer.iss).
; Per-user install: no administrator rights needed.

#define AppName "PII Guard"
#define AppVersion "1.3.0"
#define AppExe "PIIGuard.exe"

[Setup]
AppId={{6A3E0F7C-2B4D-4E61-9B8A-5C1D7E2F9A40}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=PII Guard
DefaultDirName={autopf}\PII Guard
DefaultGroupName=PII Guard
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=dist\installer
OutputBaseFilename=PIIGuard-Setup-{#AppVersion}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=no

[Tasks]
Name: "startup"; Description: "Start PII Guard when I sign in to Windows"; GroupDescription: "Startup:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "dist\PIIGuard\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "demo\*"; DestDir: "{app}\demo"; Flags: ignoreversion recursesubdirs
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\PII Guard"; Filename: "{app}\{#AppExe}"
Name: "{group}\PII Guard demo page"; Filename: "{app}\demo\demo.html"
Name: "{group}\PII Guard self-test"; Filename: "{app}\{#AppExe}"; Parameters: "--selftest"
Name: "{group}\Uninstall PII Guard"; Filename: "{uninstallexe}"
Name: "{autodesktop}\PII Guard"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "PIIGuard"; ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: startup
; where safe copies go (the "Safe copies folder" page); the app keeps this in step with its settings
Root: HKCU; Subkey: "Software\PIIGuard"; ValueType: string; ValueName: "SafeCopiesFolder"; ValueData: "{code:GetSafeDir}"; Flags: uninsdeletekey
; the browser link (native messaging host) the app registers itself: removed on uninstall
Root: HKCU; Subkey: "Software\Google\Chrome\NativeMessagingHosts\com.piiguard.host"; Flags: dontcreatekey uninsdeletekey
Root: HKCU; Subkey: "Software\Microsoft\Edge\NativeMessagingHosts\com.piiguard.host"; Flags: dontcreatekey uninsdeletekey
Root: HKCU; Subkey: "Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\com.piiguard.host"; Flags: dontcreatekey uninsdeletekey
Root: HKCU; Subkey: "Software\Chromium\NativeMessagingHosts\com.piiguard.host"; Flags: dontcreatekey uninsdeletekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start PII Guard now"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: WizardSilent
Filename: "{app}\demo\demo.html"; Description: "Open the demo page"; Flags: shellexec postinstall skipifsilent unchecked

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM {#AppExe} /F"; Flags: runhidden; RunOnceId: "StopPIIGuard"

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\PIIGuard"

[Code]
// PII Guard sits in the tray and doesn't answer Windows' "please close" request, so an
// upgrade stops it directly before copying files (it is started again at the end).
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM PIIGuard.exe /F', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(1000);
  Result := '';
end;

// ---- "Safe copies folder" page ---------------------------------------------------------
var
  SafePage: TInputDirWizardPage;

function DefaultSafeDir(): String;
begin
  Result := ExpandConstant('{localappdata}\PIIGuard\Safe copies');
end;

procedure InitializeWizard;
var
  Prev: String;
begin
  SafePage := CreateInputDirPage(wpSelectTasks,
    'Safe copies folder', 'Where should PII Guard save the safe copies of your files?',
    'When you choose to replace private details, PII Guard saves the safe version of the file in this folder. ' +
    'Your original files are never changed.' + #13#10#13#10 +
    'Copies in the default folder are deleted after 24 hours. Copies in a folder you choose are kept until you delete them.' + #13#10#13#10 +
    'You can change this later: right-click the PII Guard shield in the taskbar, then Safe copies folder.',
    False, '');
  SafePage.Add('');
  if not RegQueryStringValue(HKCU, 'Software\PIIGuard', 'SafeCopiesFolder', Prev) or (Prev = '') then
    Prev := DefaultSafeDir();
  SafePage.Values[0] := Prev;
end;

function GetSafeDir(Param: String): String;
begin
  Result := SafePage.Values[0];
  if Result = '' then
    Result := DefaultSafeDir();
end;

