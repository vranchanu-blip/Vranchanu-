; Установщик Raport cazare (собирается в GitHub Actions через Inno Setup 6)
#define AppVersion GetEnv("APP_VERSION")

[Setup]
AppId={{6B1E5E2C-7C55-4C1B-9B7E-2F4A9A1C0D11}
AppName=Raport cazare
AppVersion={#AppVersion}
AppPublisher=Raport cazare
DefaultDirName={localappdata}\Programs\RaportCazare
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=RaportCazare-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\RaportCazare.exe
UninstallDisplayName=Raport cazare

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"

[Files]
Source: "..\dist\RaportCazare\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Raport cazare"; Filename: "{app}\RaportCazare.exe"
Name: "{autodesktop}\Raport cazare"; Filename: "{app}\RaportCazare.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RaportCazare.exe"; Description: "Запустить Raport cazare"; Flags: nowait postinstall skipifsilent
