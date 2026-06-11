@echo off
setlocal enabledelayedexpansion
set "CHOICE_FILE=%temp%\choice_claude.txt"
set "CLAUDE=%USERPROFILE%\.local\bin\claude.exe"

if exist "%CHOICE_FILE%" del "%CHOICE_FILE%"

py "%~dp0claude-sessions.py"

if not exist "%CHOICE_FILE%" (
    echo Cancelled.
    pause
    exit /b
)

for /f "usebackq tokens=1,2,3,4,5 delims=|" %%A in ("%CHOICE_FILE%") do (
    set "SCELTA=%%A"
    set "ENCODED=%%B"
    set "ACTION=%%C"
    set "EFFORT=%%D"
    set "MODEL=%%E"
)

del "%CHOICE_FILE%"

cd /d "!SCELTA!"

rem -- inject per-project extra PATH entries ------------------
set "EXTRA_PATHS_FILE=%USERPROFILE%\.claude\projects\!ENCODED!\extra-paths.txt"
if exist "!EXTRA_PATHS_FILE!" (
    for /f "usebackq" %%P in ("!EXTRA_PATHS_FILE!") do set "PATH=%%P;!PATH!"
)

rem -- build optional launch flags ----------------------------
set "EFFORT_FLAG="
set "MODEL_FLAG="
set "SPFILE_FLAG="
if not "!EFFORT!"=="" set "EFFORT_FLAG=--effort !EFFORT!"
if not "!MODEL!"==""  set "MODEL_FLAG=--model !MODEL!"
set "SYS_PROMPT_FILE=%USERPROFILE%\.claude\projects\!ENCODED!\system-prompt.txt"
if exist "!SYS_PROMPT_FILE!" set SPFILE_FLAG=--system-prompt-file "!SYS_PROMPT_FILE!"

cls
echo Location: !cd!
echo Action:   !ACTION!
if not "!EFFORT!"=="" echo Effort:   !EFFORT!
if not "!MODEL!"==""  echo Model:    !MODEL!
echo ------------------------------------------

if "!ACTION!"=="new" (
    "!CLAUDE!" !EFFORT_FLAG! !MODEL_FLAG! !SPFILE_FLAG!
    if errorlevel 1 (echo. & echo [claude exited with error %ERRORLEVEL%] & pause)
) else if "!ACTION:~0,5!"=="fork:" (
    set "SID=!ACTION:~5!"
    echo Forking session: !SID!
    echo.
    "!CLAUDE!" -r !SID! --fork-session !EFFORT_FLAG! !MODEL_FLAG! !SPFILE_FLAG!
    if errorlevel 1 (echo. & echo [claude exited with error %ERRORLEVEL%] & pause)
) else if "!ACTION:~0,7!"=="resume:" (
    set "SID=!ACTION:~7!"
    echo Resuming: !SID!
    echo.
    "!CLAUDE!" -r !SID! !EFFORT_FLAG! !MODEL_FLAG! !SPFILE_FLAG!
    if errorlevel 1 (echo. & echo [claude exited with error %ERRORLEVEL%] & pause)
) else if "!ACTION:~0,14!"=="resume-named::" (
    for /f "tokens=1,2 delims=::" %%X in ("!ACTION:~14!") do (
        set "SID=%%X"
        set "SNAME=%%Y"
    )
    echo Resuming: !SID! [!SNAME!]
    echo.
    "!CLAUDE!" -r !SID! !EFFORT_FLAG! !MODEL_FLAG! !SPFILE_FLAG!
    if errorlevel 1 (echo. & echo [claude exited with error %ERRORLEVEL%] & pause)
) else (
    cmd /k
)
