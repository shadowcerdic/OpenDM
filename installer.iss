; OpenDM v2.0 Inno Setup Script
; Builds a complete standalone Windows installer including Python runtime,
; OpenDM source code, shortcuts, and Chrome Native Messaging Host registry keys.

#define MyAppName "OpenDM"
#define MyAppVersion "2.0.0"
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
; Register Chrome Native Messaging Host for OpenDM session token bootstrapping
Root: HKCU; Subkey: "Software\Google\Chrome\NativeMessagingHosts\org.opendm.native_host"; ValueType: string; ValueName: ""; ValueData: "{app}\app\opendm\org.opendm.native_host.json"; Flags: uninsdeletekey

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m opendm.gui"; WorkingDir: "{app}\app"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
