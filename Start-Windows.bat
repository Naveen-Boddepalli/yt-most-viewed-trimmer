@echo off
rem YouTube Highlight Trimmer: double-click this file to start the app (Windows).
rem
rem Nothing needs to be installed first. On the first run this downloads uv
rem (a small installer tool, no admin rights needed), a private copy of Python,
rem and the app's libraries into this folder. That takes a few minutes. Later
rem starts take a few seconds. The app then opens in your web browser.

setlocal
cd /d "%~dp0"
title YouTube Highlight Trimmer
echo === YouTube Highlight Trimmer ===
echo.

set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul
if errorlevel 1 call :install_uv
where uv >nul 2>nul
if errorlevel 1 (
    echo Could not install uv. Check your internet connection and try again.
    goto :fail
)

set "FIRST_RUN=0"
if not exist ".venv\Scripts\python.exe" set "FIRST_RUN=1"
if "%FIRST_RUN%"=="1" (
    echo First-time setup: downloading Python and the app's libraries.
    echo This takes a few minutes and only happens once.
    uv venv --quiet --python 3.12 .venv
)
if not exist ".venv\Scripts\python.exe" (
    echo Could not set up Python. Check your internet connection and try again.
    goto :fail
)

rem Install missing libraries and update the YouTube downloader every start:
rem YouTube changes often and old versions of yt-dlp stop working.
echo Checking for updates...
uv pip install --quiet --python .venv\Scripts\python.exe -r requirements.txt --upgrade-package yt-dlp --upgrade-package yt-dlp-ejs --upgrade-package pytubefix
if errorlevel 1 goto :update_failed
goto :start

:update_failed
if "%FIRST_RUN%"=="1" (
    rmdir /s /q .venv
    echo Setup failed. Check your internet connection and try again.
    goto :fail
)
echo Could not check for updates (offline?). Starting with the installed version.

:start
set "YT_TRIMMER_SINGLE_USER=1"
set "STREAMLIT_BROWSER_GATHER_USAGE_STATS=false"
echo.
echo Starting... the app will open in your web browser.
echo Keep this window open while you use the app. Close it to stop the app.
echo.
.venv\Scripts\python.exe -m streamlit run app.py --server.headless false --server.address 127.0.0.1 --server.fileWatcherType none %*
if errorlevel 1 goto :fail
exit /b 0

:install_uv
echo First-time setup: installing uv (no admin rights needed)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:UV_NO_MODIFY_PATH='1'; irm https://astral.sh/uv/install.ps1 | iex"
exit /b 0

:fail
echo.
pause
exit /b 1
