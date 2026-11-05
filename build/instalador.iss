; Instalador do Windows (Inno Setup)
#define Versao GetEnv("GREGIO_VERSAO")
[Setup]
AppId={{6E2D7C4B-8B57-4C9E-9A57-6A1F2C3D9E11}
AppName=Gregio AutoEdit
AppVersion={#Versao}
AppPublisher=Gregio
DefaultDirName={localappdata}\Programs\Gregio AutoEdit
DefaultGroupName=Gregio AutoEdit
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Gregio-AutoEdit-Windows-Instalador
SetupIconFile=icone.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Gregio AutoEdit.exe

[Languages]
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "atalho"; Description: "Criar atalho na área de trabalho"; GroupDescription: "Atalhos:"

[Files]
Source: "..\dist\Gregio AutoEdit\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Gregio AutoEdit"; Filename: "{app}\Gregio AutoEdit.exe"
Name: "{autodesktop}\Gregio AutoEdit"; Filename: "{app}\Gregio AutoEdit.exe"; Tasks: atalho

[Run]
Filename: "{app}\Gregio AutoEdit.exe"; Description: "Abrir o Gregio AutoEdit"; Flags: nowait postinstall skipifsilent
