@echo off
rem Start ScreenRec with the project's own Python, without a console window.
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app\run.pyw"
