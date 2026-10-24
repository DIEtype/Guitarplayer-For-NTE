@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 goto directory_failed
py -3 -c "import tkinter" >nul 2>&1
if not errorlevel 1 goto via_py
python -c "import tkinter" >nul 2>&1
if not errorlevel 1 goto via_python
set "GUITAR_BUNDLED=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%GUITAR_BUNDLED%" goto via_bundled
echo Python 3.10 or newer with Tkinter is required.
pause
exit /b 1
:via_py
py -3 "%~dp0main.py" %*
goto finished
:via_python
python "%~dp0main.py" %*
goto finished
:via_bundled
"%GUITAR_BUNDLED%" "%~dp0main.py" %*
goto finished
:directory_failed
echo Cannot open the application directory.
pause
exit /b 1
:finished
set "GUITAR_EXIT=%errorlevel%"
if "%GUITAR_EXIT%"=="0" exit /b 0
echo Startup failed. See startup_error.log in this folder.
pause
exit /b %GUITAR_EXIT%
