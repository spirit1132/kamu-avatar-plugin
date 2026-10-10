@echo off
chcp 65001 >nul 2>nul
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo.
echo   ========================================
echo    KAMUCL one-click beautify
echo      (avatar + mascot + icons + plugin)
echo   ========================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo   [ERROR] python not found.
    echo.
    echo   Install Python 3 first: https://www.python.org/downloads/
    echo   Remember to check "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

python -c "import PIL" >nul 2>nul
if errorlevel 1 (
    echo   Pillow not found, installing...
    echo.
    python -m pip install Pillow
    if errorlevel 1 (
        echo.
        echo   [ERROR] Pillow install failed. Run manually:
        echo       pip install Pillow
        echo.
        pause
        exit /b 1
    )
    echo.
)

tasklist /FI "IMAGENAME eq KAMUCL.exe" 2>nul | find /I "KAMUCL.exe" >nul
if not errorlevel 1 (
    echo   [NOTE] KAMUCL is running.
    echo.
    echo   Patching needs to replace launcher files. Please close KAMUCL first.
    echo.
    choice /C YN /M "   Close KAMUCL automatically now"
    if errorlevel 2 (
        echo.
        echo   Please exit KAMUCL manually and run this file again.
        echo.
        pause
        exit /b 1
    )
    echo.
    echo   Closing KAMUCL...
    taskkill /F /IM KAMUCL.exe >nul 2>nul
    timeout /t 3 /nobreak >nul
    echo   Done.
    echo.
)

python install.py %*

set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (
    echo   Finished. Press any key to exit.
) else (
    echo   Failed with code %RC%. Press any key to exit.
)
pause >nul
endlocal
