@echo off
NET SESSION >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo ERROR: try run as Admin
    pause
    exit /b
)

SET "OSF_PATH=C:\Program Files\OSFMount\osfmount.com"
SET "IMG_DIR=C:\c"
SET "IMG_PATH=%IMG_DIR%\a.img"
SET "DRIVE_LETTER=A:"
:: 200MB = 200 * 1024 * 1024
SET "DISK_BYTES=209715200"

if not exist "%IMG_DIR%" mkdir "%IMG_DIR%"

if exist "%IMG_PATH%" (
    echo Found existing image at %IMG_PATH%. Mounting to %DRIVE_LETTER%...
    "%OSF_PATH%" -a -t file -f "%IMG_PATH%" -m %DRIVE_LETTER% -o rw,logical
) else (
    echo Image not found. Pre-allocating blank file...
    fsutil file createnew "%IMG_PATH%" %DISK_BYTES%
    
    echo Mounting empty raw file to %DRIVE_LETTER%...
    "%OSF_PATH%" -a -t file -f "%IMG_PATH%" -m %DRIVE_LETTER% -o rw,logical
    
    echo Formatting mounted drive as NTFS...
    :: /q does a quick format, /y answers "Yes" to confirmation prompts
    format %DRIVE_LETTER% /fs:ntfs /v:ramdisk /q /y
)

echo Drive mounted at %DRIVE_LETTER%
:: timeout /t 3
