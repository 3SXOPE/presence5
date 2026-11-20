@echo off
rem Builds the Windows versions of presence5 into the dist folder:
rem   presence5-windows.exe  (the app with a window)
rem   presence5-cli.exe      (the command-line version)
rem Double-click this file, or run it from a Command Prompt in this folder.
rem It uses uv (https://docs.astral.sh/uv/), which also fetches Python, so
rem nothing else needs to be installed first.

setlocal
cd /d "%~dp0"

where uv >nul 2>nul
if %errorlevel%==0 (
    set "UV=uv"
) else if exist "%USERPROFILE%\.local\bin\uv.exe" (
    set "UV=%USERPROFILE%\.local\bin\uv.exe"
) else (
    echo uv is not installed. Installing it with its official installer...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    if errorlevel 1 goto :failed
    set "UV=%USERPROFILE%\.local\bin\uv.exe"
)

echo.
echo Setting up Python and the build tools...
"%UV%" sync
if errorlevel 1 goto :failed

echo.
echo Building presence5-windows.exe...
"%UV%" run pyinstaller --noconfirm --onefile --windowed --icon assets\presence5.ico --name presence5-windows --paths client client\gui.py
if errorlevel 1 goto :failed

echo.
echo Building presence5-cli.exe...
"%UV%" run pyinstaller --noconfirm --onefile --icon assets\presence5.ico --name presence5-cli --paths client client\main.py
if errorlevel 1 goto :failed

echo.
echo Done. The programs are in the dist folder:
dir /b dist\*.exe
echo.
pause
exit /b 0

:failed
echo.
echo The build failed. The messages above say why.
pause
exit /b 1
