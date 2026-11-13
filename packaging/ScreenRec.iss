; ScreenRec - the installer.
;
;   "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" packaging\ScreenRec.iss
;
; Build the programs first - `tools\build_release.py` - because this only packs what is
; already in `dist\ScreenRec`.
;
; What a person gets: two ordinary programs in the Start menu, ScreenRec and
; ScreenRec Player, an uninstaller in "Apps & features", and the player registered as
; the way to open a `.screenrec.json`.
;
; **Into Program Files, for everybody**, at the author's instruction. That costs a UAC
; prompt, which is the price of a machine-wide install; nothing in the program itself
; needs it, since it writes only to its own settings folder and to the folder chosen
; below.
;
; This file is UTF-8 **with a BOM**. Inno 6.5 compiled the Russian messages correctly
; without one - measured on 2026-11-05 by compiling the same script both ways and
; reading the wizard's own window text, which came out right either way - so the BOM
; fixes nothing today. It is here to say the encoding out loud rather than rely on a
; compiler being lenient about a file that does not state it.
;
; **The wizard asks where recordings should go**, and the answer is written to the
; registry rather than to anyone's settings file. That is not indirection for its own
; sake: an elevated installer runs as the administrator, so `{userappdata}` there is the
; administrator's profile and not necessarily the person who will use the program. So
; the installer *proposes* a folder, and `ui.app.Application._ask_where_to_save` adopts
; it into the settings of whoever runs the program first - once, per user, which is also
; what makes this right on a machine with several of them.

#define Name        "ScreenRec"
#define Version     "0.3.0"
#define Publisher   "ScreenRec"
#define RecorderExe "ScreenRec.exe"
#define PlayerExe   "ScreenRecPlayer.exe"

[Setup]
AppId={{8E6C9A2F-4D13-4B77-9A1E-5C2F0B7A3D61}
AppName={#Name}
AppVersion={#Version}
AppPublisher={#Publisher}
DefaultDirName={autopf}\{#Name}
DefaultGroupName={#Name}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=ScreenRec-{#Version}-setup
SetupIconFile=..\build\icons\screenrec.ico
UninstallDisplayIcon={app}\{#RecorderExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
DiskSpanning=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian";  MessagesFile: "compiler:Languages\Russian.isl"

[CustomMessages]
english.LaunchRecorder=Start ScreenRec now
english.DesktopIcons=Put shortcuts on the desktop
english.OutputCaption=Where should recordings go?
english.OutputDescription=Choose the folder ScreenRec will save its recordings in.
english.OutputSubCaption=You can change this later in the program's settings. The folder is only a starting place: nothing is written there until you record something.
russian.LaunchRecorder=Запустить ScreenRec
russian.DesktopIcons=Создать ярлыки на рабочем столе
russian.OutputCaption=Куда сохранять записи?
russian.OutputDescription=Выберите папку, в которую ScreenRec будет сохранять записи.
russian.OutputSubCaption=Это можно будет поменять в настройках программы. Папка — только отправная точка: в неё ничего не пишется, пока вы не сделаете запись.

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcons}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Everything the build produced: the two programs, Qt, Python and ffmpeg.
Source: "..\dist\ScreenRec\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#Name}";          Filename: "{app}\{#RecorderExe}"
Name: "{group}\{#Name} Player";   Filename: "{app}\{#PlayerExe}"
Name: "{group}\{cm:UninstallProgram,{#Name}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#Name}";        Filename: "{app}\{#RecorderExe}"; Tasks: desktopicon
Name: "{autodesktop}\{#Name} Player"; Filename: "{app}\{#PlayerExe}";   Tasks: desktopicon

[Registry]
; The folder the wizard asked for. Read once, by the program, into the settings of
; whoever runs it - see the note at the top of this file.
Root: HKA; Subkey: "Software\{#Name}"; ValueType: string; ValueName: "DefaultOutputDir"; ValueData: "{code:OutputFolder}"; Flags: uninsdeletekey

; A recording this program made is a `.screenrec.json`, and double-clicking one should
; open the player rather than a text editor.
Root: HKA; Subkey: "Software\Classes\.screenrec.json"; ValueType: string; ValueName: ""; ValueData: "ScreenRec.Recording"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\ScreenRec.Recording"; ValueType: string; ValueName: ""; ValueData: "ScreenRec recording"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\ScreenRec.Recording\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#PlayerExe},0"
Root: HKA; Subkey: "Software\Classes\ScreenRec.Recording\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#PlayerExe}"" ""%1"""

[Run]
Filename: "{app}\{#RecorderExe}"; Description: "{cm:LaunchRecorder}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The settings and the probe cache are the user's and are left alone on purpose: an
; uninstall that throws away where somebody keeps their recordings punishes them for
; trying a new version. Only what this installer wrote is removed.
Type: dirifempty; Name: "{app}\tools\ffmpeg"
Type: dirifempty; Name: "{app}\tools"
Type: dirifempty; Name: "{app}"

[Code]
var
  OutputPage: TInputDirWizardPage;

function RegisteredIn(Root: Integer): String;
begin
  if not RegQueryStringValue(Root, 'Software\{#Name}', 'DefaultOutputDir', Result) then
    Result := '';
  Result := Trim(Result);
end;

function FolderFromRegistry: String;
begin
  // The answer this page was given last time.
  //
  // **Both registry views, and the 64-bit one first.** Setup is a 32-bit program, so a
  // plain `HKEY_LOCAL_MACHINE` here is the `Wow6432Node` view, while the registry
  // section above writes through `HKA` with `ArchitecturesInstallIn64BitMode` in force
  // and lands in the 64-bit one. Reading the wrong view finds nothing and falls
  // silently through to the built-in suggestion, which is this function failing at the
  // one job it has - measured on 2026-11-05, with the value plainly present in
  // `HKLM\Software\ScreenRec` and the page still opening on the suggestion.
  //
  // Per-user is asked as well, for a machine that once had a per-user install.
  Result := '';
  if IsWin64 then
    Result := RegisteredIn(HKEY_LOCAL_MACHINE_64);
  if Result = '' then
    Result := RegisteredIn(HKEY_LOCAL_MACHINE_32);
  if (Result = '') and IsWin64 then
    Result := RegisteredIn(HKEY_CURRENT_USER_64);
  if Result = '' then
    Result := RegisteredIn(HKEY_CURRENT_USER_32);
end;

procedure InitializeWizard;
var
  Suggested, Source: String;
begin
  // An Inno comment is delimited by braces, so this one cannot be - every constant
  // in this file is written in braces and would close it early. That is what the
  // first attempt did, at line 113 column 16.
  //
  // **The page opens on what the last install was told**, and only falls back to the
  // built-in suggestion when there has been no install. Before this it proposed the
  // same built-in folder at every upgrade, so somebody who keeps their recordings
  // elsewhere had to say so again each time.
  //
  // **The program's own `settings.json` is deliberately not read**, although it is
  // the better answer - it is where recordings actually go. Two reasons, the second
  // decisive. It is not needed: this value is a *proposal*, and
  // `ui.app.Application._ask_where_to_save` takes it only for a user who has never
  // chosen a folder, so for anyone who has, a stale value here changes nothing. And
  // it cannot be read correctly: the file is UTF-8, Pascal Script's `Chr` takes a
  // byte, and a hand-rolled decoder therefore truncates every character above 255 -
  // measured on 2026-11-05: a path with Cyrillic in it reached the page with every
  // letter replaced by its own code point modulo 256, which is neither the path nor
  // obviously wrong to look at. A folder name on a Russian machine is exactly
  // the case that breaks, so the branch was taken out rather than left to mangle one
  // path in ten.
  Suggested := FolderFromRegistry;
  Source := 'what the last install was told';
  if Suggested = '' then
  begin
    // The profile of whoever started setup: the documents and videos constants would
    // be the administrator's under an elevated install.
    Suggested := ExpandConstant('{%USERPROFILE}') + '\Videos\{#Name}';
    Source := 'the built-in suggestion';
  end;
  Log('ScreenRec: the folder page opens on "' + Suggested + '", from ' + Source + '.');

  OutputPage := CreateInputDirPage(
    wpSelectDir,
    ExpandConstant('{cm:OutputCaption}'),
    ExpandConstant('{cm:OutputDescription}'),
    ExpandConstant('{cm:OutputSubCaption}'),
    False, '');
  OutputPage.Add('');
  OutputPage.Values[0] := Suggested;
end;

function OutputFolder(Param: String): String;
begin
  Result := Trim(OutputPage.Values[0]);
  if Result = '' then
    Result := ExpandConstant('{%USERPROFILE}') + '\Videos\{#Name}';
end;
