@echo off
setlocal enabledelayedexpansion
set "CHOICE_FILE=%temp%\choice_claude.txt"
set "CLAUDE=%USERPROFILE%\.local\bin\claude.exe"
if not exist "%CLAUDE%" (
    where claude.exe >nul 2>&1
    if not errorlevel 1 (
        set "CLAUDE=claude.exe"
    )
)

if exist "%CHOICE_FILE%" del "%CHOICE_FILE%"

set "CLAUDECTL_BAT=1"
py "%~dp0claude-sessions.py"

if not exist "%CHOICE_FILE%" (
    echo Cancelled.
    pause
    exit /b
)

for /f "usebackq tokens=1-10 delims=|" %%A in ("%CHOICE_FILE%") do (
    set "T1=%%A"
    set "T2=%%B"
    set "T3=%%C"
    set "T4=%%D"
    set "T5=%%E"
    set "T6=%%F"
    set "T7=%%G"
    set "T8=%%H"
    set "T9=%%I"
    set "T10=%%J"
)

set "CFGDIR="
if "!T1!"=="v3" (
    rem v3 format: v3^|path^|encoded^|action^|effort^|model^|perm^|name^|worktree^|config_dir
    rem empty fields are written as '-' so for /f cannot collapse them
    set "SCELTA=!T2!"
    set "ENCODED=!T3!"
    set "ACTION=!T4!"
    set "EFFORT=!T5!"
    set "MODEL=!T6!"
    set "PERM=!T7!"
    set "SNAME=!T8!"
    set "WTREE=!T9!"
    set "CFGDIR=!T10!"
    if "!EFFORT!"=="-" set "EFFORT="
    if "!MODEL!"=="-"  set "MODEL="
    if "!PERM!"=="-"   set "PERM="
    if "!SNAME!"=="-"  set "SNAME="
    if "!WTREE!"=="-"  set "WTREE="
    if "!ENCODED!"=="-" set "ENCODED="
    if "!CFGDIR!"=="-" set "CFGDIR="
) else if "!T1!"=="v2" (
    rem v2 format: v2^|path^|encoded^|action^|effort^|model^|perm^|name^|worktree
    set "SCELTA=!T2!"
    set "ENCODED=!T3!"
    set "ACTION=!T4!"
    set "EFFORT=!T5!"
    set "MODEL=!T6!"
    set "PERM=!T7!"
    set "SNAME=!T8!"
    set "WTREE=!T9!"
    if "!EFFORT!"=="-" set "EFFORT="
    if "!MODEL!"=="-"  set "MODEL="
    if "!PERM!"=="-"   set "PERM="
    if "!SNAME!"=="-"  set "SNAME="
    if "!WTREE!"=="-"  set "WTREE="
    if "!ENCODED!"=="-" set "ENCODED="
) else (
    rem legacy 5-field format: path^|encoded^|action^|effort^|model
    set "SCELTA=!T1!"
    set "ENCODED=!T2!"
    set "ACTION=!T3!"
    set "EFFORT=!T4!"
    set "MODEL=!T5!"
    set "PERM="
    set "SNAME="
    set "WTREE="
)

del "%CHOICE_FILE%"

cd /d "!SCELTA!"

rem -- pin account / config dir (v3+) ------------------------
rem Empty CFGDIR -> default ~/.claude; set it explicitly so it overrides
rem any ambient CLAUDE_CONFIG_DIR this console was launched under.
if "!CFGDIR!"=="" set "CFGDIR=%USERPROFILE%\.claude"
set "CLAUDE_CONFIG_DIR=!CFGDIR!"

rem -- inject per-project extra PATH entries ------------------
set "EXTRA_PATHS_FILE=!CFGDIR!\projects\!ENCODED!\extra-paths.txt"
if exist "!EXTRA_PATHS_FILE!" (
    for /f "usebackq delims=" %%P in ("!EXTRA_PATHS_FILE!") do set "PATH=%%P;!PATH!"
)

rem -- build optional launch flags ----------------------------
set "EFFORT_FLAG="
set "MODEL_FLAG="
set "PERM_FLAG="
set "SPFILE_FLAG="
set "NAME_FLAG="
set "WT_FLAG="
set "ADDDIR_FLAG="
set "ADDDIRS="
if not "!EFFORT!"=="" set "EFFORT_FLAG=--effort !EFFORT!"
if not "!MODEL!"==""  set "MODEL_FLAG=--model !MODEL!"
if not "!PERM!"==""   set "PERM_FLAG=--permission-mode !PERM!"
if not "!SNAME!"=="" set NAME_FLAG=-n "!SNAME!"
if "!WTREE!"=="*" (
    set "WT_FLAG=-w"
) else if not "!WTREE!"=="" (
    set WT_FLAG=-w "!WTREE!"
)
set "SYS_PROMPT_FILE=!CFGDIR!\projects\!ENCODED!\system-prompt.txt"
if exist "!SYS_PROMPT_FILE!" set SPFILE_FLAG=--system-prompt-file "!SYS_PROMPT_FILE!"
set "ADD_DIRS_FILE=!CFGDIR!\projects\!ENCODED!\add-dirs.txt"
if exist "!ADD_DIRS_FILE!" (
    for /f "usebackq delims=" %%P in ("!ADD_DIRS_FILE!") do (
        if exist "%%P\" set ADDDIRS=!ADDDIRS! "%%P"
    )
)
if defined ADDDIRS set "ADDDIR_FLAG=--add-dir!ADDDIRS!"

cls
echo Location: !cd!
echo Action:   !ACTION!
if not "!EFFORT!"=="" echo Effort:   !EFFORT!
if not "!MODEL!"==""  echo Model:    !MODEL!
if not "!PERM!"==""   echo Perms:    !PERM!
echo ------------------------------------------

if "!ACTION!"=="new" (
    "!CLAUDE!" !EFFORT_FLAG! !MODEL_FLAG! !PERM_FLAG! !NAME_FLAG! !WT_FLAG! !SPFILE_FLAG! !ADDDIR_FLAG!
    if errorlevel 1 (echo. & echo [claude exited with error %ERRORLEVEL%] & pause)
) else if "!ACTION!"=="continue" (
    echo Continuing latest session...
    echo.
    "!CLAUDE!" -c !EFFORT_FLAG! !MODEL_FLAG! !PERM_FLAG! !SPFILE_FLAG! !ADDDIR_FLAG!
    if errorlevel 1 (echo. & echo [claude exited with error %ERRORLEVEL%] & pause)
) else if "!ACTION:~0,5!"=="fork:" (
    set "SID=!ACTION:~5!"
    echo Forking session: !SID!
    echo.
    "!CLAUDE!" -r !SID! --fork-session !EFFORT_FLAG! !MODEL_FLAG! !PERM_FLAG! !SPFILE_FLAG! !ADDDIR_FLAG!
    if errorlevel 1 (echo. & echo [claude exited with error %ERRORLEVEL%] & pause)
) else if "!ACTION:~0,7!"=="resume:" (
    set "SID=!ACTION:~7!"
    echo Resuming: !SID!
    echo.
    "!CLAUDE!" -r !SID! !EFFORT_FLAG! !MODEL_FLAG! !PERM_FLAG! !SPFILE_FLAG! !ADDDIR_FLAG!
    if errorlevel 1 (echo. & echo [claude exited with error %ERRORLEVEL%] & pause)
) else if "!ACTION:~0,14!"=="resume-named::" (
    for /f "tokens=1,2 delims=::" %%X in ("!ACTION:~14!") do (
        set "SID=%%X"
        set "RNAME=%%Y"
    )
    echo Resuming: !SID! [!RNAME!]
    echo.
    "!CLAUDE!" -r !SID! !EFFORT_FLAG! !MODEL_FLAG! !PERM_FLAG! !SPFILE_FLAG! !ADDDIR_FLAG!
    if errorlevel 1 (echo. & echo [claude exited with error %ERRORLEVEL%] & pause)
) else (
    echo Unknown action: "!ACTION!" -- opening plain terminal instead.
    cmd /k
)
