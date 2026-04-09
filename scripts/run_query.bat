@echo off
REM Usage: run_query.bat <repo-path> "<question>" [--limit N]
REM Example: run_query.bat C:\JARVIS "voice recognition"

setlocal
if "%~1"=="" (
    echo Usage: run_query.bat ^<repo-path^> "^<question^>" [--limit N]
    exit /b 2
)
if "%~2"=="" (
    echo Usage: run_query.bat ^<repo-path^> "^<question^>" [--limit N]
    exit /b 2
)

set "PYTHONPATH=%~dp0..\src;%PYTHONPATH%"
C:\JARVIS\.venv\Scripts\python.exe -m jarvis_graph query "%~1" "%~2" %3 %4
endlocal
