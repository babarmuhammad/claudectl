@echo off
setlocal enabledelayedexpansion
set "CHOICE_FILE=%temp%\choice_claude.txt"

if exist "%CHOICE_FILE%" del "%CHOICE_FILE%"

rem 1) run the TUI - writes the choice file and exits
set "CLAUDECTL_BAT=1"
py "%~dp0claude-sessions.py"

if not exist "%CHOICE_FILE%" (
    echo Cancelled.
    pause
    exit /b
)

rem 2) launch claude from the choice file. All flag-building (effort, model,
rem    permission, agent, --agents JSON, worktree, add-dir, config dir, PATH)
rem    lives in Python now, so the launched claude can carry the large
rem    --agents payload the cmd choice-file could never hold.
py "%~dp0claude-sessions.py" --launch
