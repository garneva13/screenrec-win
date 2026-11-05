@echo off
rem Play a recording with the project's own Python, without a console window.
rem   ScreenRecPlayer.cmd                 the newest recording in the output folder
rem   ScreenRecPlayer.cmd "<path>"        an index, a video file, or a folder
rem PYTHONPATH rather than a working directory, so a relative path given here still
rem means what it meant in the folder it was typed in.
setlocal
set "PYTHONPATH=%~dp0app;%PYTHONPATH%"
start "" "%~dp0.venv\Scripts\pythonw.exe" -m screenrec.player %*
endlocal
