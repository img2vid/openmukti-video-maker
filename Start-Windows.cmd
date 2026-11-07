@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 goto failure
set "SCENEVOICE_VENV_PY=.venv\Scripts\python.exe"
if exist "%SCENEVOICE_VENV_PY%" goto dependencies

echo Setting up SceneVoice Studio...
py -3 -c "import sys, tkinter; assert sys.version_info >= (3, 11)" >nul 2>&1
if not errorlevel 1 (
    py -3 -m venv .venv
    if errorlevel 1 goto failure
    goto dependencies
)
python -c "import sys, tkinter; assert sys.version_info >= (3, 11)" >nul 2>&1
if errorlevel 1 (
    echo Install 64-bit Python 3.11 or newer with Tcl/Tk support and run this launcher again.
    echo https://www.python.org/downloads/windows/
    goto failure
)
python -m venv .venv
if errorlevel 1 goto failure

:dependencies
if exist ".venv\scenevoice-requirements.txt" (
    fc /b requirements.txt ".venv\scenevoice-requirements.txt" >nul
    if not errorlevel 1 goto launch
)
echo Installing audio and image packages for this copy of SceneVoice Studio...
"%SCENEVOICE_VENV_PY%" -m pip --disable-pip-version-check install -r requirements.txt
if errorlevel 1 goto failure
copy /y requirements.txt ".venv\scenevoice-requirements.txt" >nul
if errorlevel 1 goto failure

:launch
"%SCENEVOICE_VENV_PY%" -m scenevoice %*
if errorlevel 1 goto failure
popd
exit /b 0

:failure
echo.
echo SceneVoice Studio could not start. Check the setup steps in README.md.
pause
popd
exit /b 1
