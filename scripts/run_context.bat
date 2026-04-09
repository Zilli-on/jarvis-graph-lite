@echo off
REM Usage: run_context.bat <repo-path> <symbol-or-file>
REM Example: run_context.bat C:\JARVIS handle_voice
REM          run_context.bat C:\JARVIS agents/claude_bridge.py

setlocal
if "%~1"=="" (
    echo Usage: run_context.bat ^<repo-path^> ^<symbol-or-file^>
    exit /b 2
)
if "%~2"=="" (
    echo Usage: run_context.bat ^<repo-path^> ^<symbol-or-file^>
    exit /b 2
)

set "PYTHONPATH=%~dp0..\src;%PYTHONPATH%"
C:\JARVIS\.venv\Scripts\python.exe -m jarvis_graph context "%~1" "%~2"
endlocal
