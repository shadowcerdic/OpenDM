@echo off
rem OpenDM Build & Packaging Script for Inno Setup
echo [OpenDM] Compiling Inno Setup Installer...

set ISCC_PATH="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist %ISCC_PATH% (
    set ISCC_PATH="C:\Program Files\Inno Setup 6\ISCC.exe"
)

if not exist %ISCC_PATH% (
    echo Error: Inno Setup 6 compiler (ISCC.exe) not found.
    echo Please install Inno Setup 6 from https://jrsoftware.org/isdl.php
    exit /b 1
)

%ISCC_PATH% "installer.iss"
if %ERRORLEVEL% equ 0 (
    echo [OpenDM] Build successful! Output: dist\OpenDM-Setup.exe
) else (
    echo [OpenDM] Build failed with error code %ERRORLEVEL%
    exit /b %ERRORLEVEL%
)
