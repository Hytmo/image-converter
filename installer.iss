; Inno Setup script for Image Converter
; Compile with: ISCC.exe installer.iss
; Output:       installer_output\ImageConverter-Setup.exe

#define MyAppName       "Image Converter"
#define MyAppVersion    "1.0.0"
#define MyAppPublisher  "Hytmo"
#define MyAppURL        "https://github.com/Hytmo/image-converter"
#define MyAppExeName    "ImageConverter.exe"
#define MyAppIcoName    "icon.ico"

[Setup]
; A stable AppId (GUID) so future versions are recognised as upgrades.
AppId={{8F9C2A4B-7E31-4D6E-A5F2-1B7C3D8E9F02}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
VersionInfoVersion={#MyAppVersion}.0
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases

; Install location: 64-bit Program Files, in a per-product folder.
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes

; This is a 64-bit app (PyInstaller built against 64-bit Python).
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin

; Output
OutputDir=installer_output
OutputBaseFilename=ImageConverter-Setup
Compression=lzma2
SolidCompression=yes

; Branding
WizardStyle=modern
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Additional shortcuts:"; Flags: checkedonce

[Files]
Source: "dist\ImageConverter.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "assets\icon.ico";          DestDir: "{app}"; Flags: ignoreversion
Source: "README.md";                DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\{#MyAppName}"; \
    Filename: "{app}\{#MyAppExeName}"; \
    IconFilename: "{app}\{#MyAppIcoName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

Name: "{autodesktop}\{#MyAppName}"; \
    Filename: "{app}\{#MyAppExeName}"; \
    IconFilename: "{app}\{#MyAppIcoName}"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; \
    Description: "Launch {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent
