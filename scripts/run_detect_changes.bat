@echo off
REM Usage: run_detect_changes.bat <repo-path>
REM Example: run_detect_changes.bat C:\JARVIS

setlocal
if "%~1"=="" (
    echo Usage: run_detect_changes.bat ^<repo-path^>
    exit /b 2
)

set "PYTHONPATH=%~dp0..\src;%PYTHONPATH%"
C:\JARVIS\.venv\Scripts\python.exe -m jarvis_graph detect_changes "%~1"
endlocal
