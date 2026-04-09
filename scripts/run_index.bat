@echo off
REM Usage: run_index.bat <repo-path> [--full]
REM Example: run_index.bat C:\JARVIS
REM          run_index.bat C:\JARVIS --full

setlocal
if "%~1"=="" (
    echo Usage: run_index.bat ^<repo-path^> [--full]
    exit /b 2
)

set "PYTHONPATH=%~dp0..\src;%PYTHONPATH%"
C:\JARVIS\.venv\Scripts\python.exe -m jarvis_graph index "%~1" %~2
endlocal
