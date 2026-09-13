@echo off
NET SESSION >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo ERROR: try run as Admin
    pause
    exit /b
)

SET "OSF_PATH=C:\Program Files\OSFMount\osfmount.com"
SET "DRIVE_LETTER=A:"

echo Unmounting drive %DRIVE_LETTER%...
"%OSF_PATH%" -d -m %DRIVE_LETTER%

:: If standard unmount fail, force it
if %ERRORLEVEL% NEQ 0 (
    echo Drive is locked by an app. Force-dismount...
    "%OSF_PATH%" -D -m %DRIVE_LETTER%
)

echo Operation finished, data saved by osfmount (i hope)
:: timeout /t 3
