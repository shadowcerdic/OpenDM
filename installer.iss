; OpenDM v2.0 Inno Setup Script
; Builds a complete standalone Windows installer including Python runtime,
; OpenDM source code, shortcuts, and Chrome Native Messaging Host registry keys.

#define MyAppName "OpenDM"
#define MyAppVersion "2.0.1"
#define MyAppPublisher "OpenDM Project"
#define MyAppURL "https://github.com/shadowcerdic/OpenDM"
#define MyAppExeName "OpenDM.exe"

[Setup]
AppId={{8B2F3A41-C684-4821-B07E-E865F438D1C9}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=dist
OutputBaseFilename=OpenDM-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
CloseApplications=yes
RestartApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupicon"; Description: "Start OpenDM automatically when Windows starts"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Staged Python Embedded runtime and application payload
Source: "build_stage\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m opendm.gui"; WorkingDir: "{app}\app"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m opendm.gui"; WorkingDir: "{app}\app"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m opendm.gui"; WorkingDir: "{app}\app"; Tasks: startupicon

[Registry]
; Register Native Messaging Host for OpenDM session token bootstrapping.
; The manifest installed by [Files] is a template carrying a literal {app}
; placeholder; the [Code] section renders it with the real install path below.
; Keys are removed automatically on uninstall (uninsdeletekey).
Root: HKCU; Subkey: "Software\Google\Chrome\NativeMessagingHosts\org.opendm.native_host"; ValueType: string; ValueName: ""; ValueData: "{app}\app\opendm\org.opendm.native_host.json"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Microsoft\Edge\NativeMessagingHosts\org.opendm.native_host"; ValueType: string; ValueName: ""; ValueData: "{app}\app\opendm\org.opendm.native_host.json"; Flags: uninsdeletekey

[Code]
// Fill the {app} placeholder in org.opendm.native_host.json with the real
// install directory so the native-messaging manifest points at this install.
function EscapeJsonBackslashes(const S: AnsiString): AnsiString;
var
  i: Integer;
begin
  Result := '';
  for i := 1 to Length(S) do
  begin
    if S[i] = '\' then
      Result := Result + '\\'
    else
      Result := Result + S[i];
  end;
end;

procedure RenderNativeHostManifest();
var
  ManifestPath: String;
  Content, InstallDir: AnsiString;
  P: Integer;
begin
  ManifestPath := ExpandConstant('{app}\app\opendm\org.opendm.native_host.json');
  if not LoadStringFromFile(ManifestPath, Content) then
    RaiseException('OpenDM: could not read native host manifest template: ' + ManifestPath);
  InstallDir := EscapeJsonBackslashes(ExpandConstant('{app}'));
  // Fill every '{app}' placeholder with the JSON-escaped install dir.
  P := Pos('{app}', Content);
  while P > 0 do
  begin
    Delete(Content, P, 5);
    Insert(InstallDir, Content, P);
    P := Pos('{app}', Content);
  end;
  if not SaveStringToFile(ManifestPath, Content, False) then
    RaiseException('OpenDM: could not write native host manifest: ' + ManifestPath);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RenderNativeHostManifest();
end;

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m opendm.gui"; WorkingDir: "{app}\app"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
