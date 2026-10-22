@echo off
chcp 65001 >nul 2>nul
setlocal enabledelayedexpansion

cd /d "%~dp0"

set "VERFILE=%TEMP%\kamu-py-version.txt"
set "PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"
set "PYVER=3.13.11"

echo.
echo   ========================================
echo    KAMUCL one-click beautify
echo      (avatar + mascot + icons + plugin)
echo   ========================================
echo.

rem ---- locate a *working* python -------------------------------------------------
rem "where python" is NOT enough: the Microsoft Store alias
rem (%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe) is a 0-byte stub that
rem `where` reports as found, but executing it prints nothing / exits 9009.
rem So we actually run it and read back a version string.
set "PY="
call :try_python python
if not defined PY call :try_python python3
if not defined PY call :try_python py

if not defined PY (
    echo   [NOTE] No usable Python found on this machine.
    echo.
    echo   This can happen when only the Microsoft Store stub exists:
    echo     C:\Users\...\AppData\Local\Microsoft\WindowsApps\python.exe
    echo.
    echo   The tool can download and install Python for you.
    echo   Source: Huawei Cloud mirror ^(faster in China^) with python.org fallback.
    echo.
    echo     1^) Auto-install Python 3.13 ^(+ Pillow^)  [recommended]
    echo     2^) Open the download page and install manually
    echo     3^) Exit
    echo.
    choice /C 123 /N /M "   Choose [1/2/3]: "
    if errorlevel 3 (
        echo.
        echo   Cancelled.
        pause
        exit /b 1
    )
    if errorlevel 2 (
        echo.
        echo   Opening https://www.python.org/downloads/
        echo   Remember to tick "Add python.exe to PATH" during setup.
        echo.
        start "" "https://www.python.org/downloads/"
        pause
        exit /b 1
    )

    echo.
    echo   Downloading and installing Python ...
    echo.
    call :install_python
    if not defined PY (
        echo.
        echo   [ERROR] Python is still unavailable.
        echo.
        echo   Close this window, open a NEW cmd window, and run this file again
        echo   ^(PATH changes only apply to newly started processes^).
        echo.
        pause
        exit /b 1
    )
    echo.
    echo   Python installed: !PY!
    echo.
)

rem ---- environment check: Python version + Pillow (mirror-first install) --------
rem Only forward "don't ask" switches; other args belong to install.py.
set "ENVARGS="
for %%A in (%*) do (
    if /I "%%A"=="-y" set "ENVARGS=-y"
    if /I "%%A"=="--yes" set "ENVARGS=-y"
    if /I "%%A"=="--no-install-deps" set "ENVARGS=--no-install"
)
"%PY%" "%~dp0env_check.py" !ENVARGS!
if errorlevel 1 (
    echo.
    echo   [ERROR] Environment check failed ^(see the output above^).
    echo.
    echo   If Pillow could not be installed, try manually:
    echo     "%PY%" -m pip install -i %PIP_MIRROR% Pillow
    echo.
    pause
    exit /b 1
)
echo.

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

"%PY%" install.py %*

set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (
    echo   Finished. Press any key to exit.
) else (
    echo   Failed with code %RC%. Press any key to exit.
)
pause >nul
endlocal
exit /b 0

rem ==============================================================================
rem subroutines
rem ==============================================================================

:try_python
rem Find a *working* interpreter. `where python` alone is not enough: the
rem Microsoft Store alias is a 0-byte stub that `where` reports and that
rem hangs or exits 9009 when executed. So we run it for real and check that
rem it writes back a plausible version string.
rem
rem The version is written to a temp file instead of being captured from
rem stdout -- that avoids every nested-quote / escaping pitfall in cmd.
if defined PY goto :eof

set "CAND="
for /f "delims=" %%P in ('where %1 2^>nul') do (
    if not defined CAND set "CAND=%%P"
)
if not defined CAND goto :eof

if exist "%VERFILE%" del /f /q "%VERFILE%" >nul 2>nul
"%CAND%" -c "import sys;open(r'%VERFILE%','w').write(sys.version.split()[0])" >nul 2>nul

set "OUT="
if exist "%VERFILE%" (
    for /f "usebackq delims=" %%V in ("%VERFILE%") do (
        if not defined OUT set "OUT=%%V"
    )
)
if not defined OUT goto :eof

rem Must look like 3.13.11
echo %OUT%| findstr /R "^[0-9][0-9]*\.[0-9]" >nul
if errorlevel 1 goto :eof

set "PY=%CAND%"
goto :eof

:install_python
rem Delegate to env_check.py when any python exists; otherwise bootstrap directly.
set "INSTALLER=%TEMP%\kamu-python-setup.exe"
set "PYVER=3.13.11"
set "URL1=https://mirrors.huaweicloud.com/python/%PYVER%/python-%PYVER%-amd64.exe"
set "URL2=https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-amd64.exe"

if exist "%INSTALLER%" del /f /q "%INSTALLER%" >nul 2>nul

echo   Downloading Python %PYVER% ^(about 27 MB^) ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$u=@('%URL1%','%URL2%');" ^
  "foreach($x in $u){ try{ Invoke-WebRequest -Uri $x -OutFile '%INSTALLER%' -UseBasicParsing -TimeoutSec 600; Write-Host ('   Downloaded from ' + $x); exit 0 }catch{ Write-Host ('   Failed: ' + $x) } };" ^
  "exit 1"
if errorlevel 1 (
    echo   [ERROR] Download failed.
    goto :eof
)

if not exist "%INSTALLER%" (
    echo   [ERROR] Installer not found after download.
    goto :eof
)

echo   Installing silently ^(per-user, auto PATH^) ...
echo   This may take 1-2 minutes. Do not close this window.
echo.
"%INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_test=0 Include_launcher=1 AssociateFiles=0 Shortcuts=0
echo.

rem Re-resolve python from the refreshed PATH
set "PY="
call :try_python python
if not defined PY (
    rem The installer writes to the registry; this shell needs the new value.
    for /f "tokens=2,*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul ^| findstr /I "Path"') do set "PATH=%%B;!PATH!"
    set "PY="
    call :try_python python
)
if not defined PY (
    if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (
        set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    )
)
del /f /q "%INSTALLER%" >nul 2>nul
goto :eof
