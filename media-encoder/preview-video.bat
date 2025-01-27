@echo off
setlocal

:: Define paths
set PYTHON_EXE=.\bin\python\python.exe
set PYTHON_LINK=https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
set VENV_DIR=.\venv_win
set VENV_ACTIVATE=%VENV_DIR%\Scripts\activate.bat
set VENV_PIP=%VENV_DIR%\Scripts\pip.exe

:: Check for Python executable
if not exist %PYTHON_EXE% (
    <nul set /p=Python 3.11 not found. Installing...
    if not exist bin\temp mkdir bin\temp
    if not exist bin\python mkdir bin\python
    curl -o bin\temp\python_installer.exe %PYTHON_LINK% >nul 2>&1
    bin\temp\python_installer.exe /quiet TargetDir="%cd%\bin\python" Include_launcher=0 AssociateFiles=0 Shortcuts=0 InstallLauncherAllUsers=0 InstallAllUsers=0
    rmdir /s /q "%cd%\bin\temp"
    echo Done.
)

:: Check for virtual environment
if not exist %VENV_ACTIVATE% (
    <nul set /p=Virtual environment not found. Configuring...
    %PYTHON_EXE% -m pip install --user virtualenv --no-warn-script-location >nul 2>&1
    %PYTHON_EXE% -m venv %VENV_DIR% >nul 2>&1
    call %VENV_ACTIVATE%
    echo Done.
    <nul set /p=Installing packages...
    %VENV_PIP% install --upgrade pip setuptools wheel vswhere >nul 2>&1
    %VENV_PIP% install -r requirements.txt >nul 2>&1
    echo Done.
) else (
    call %VENV_ACTIVATE%
)

set /p input_file="Drag & drop the video file you want to preview: "

python preview-video.py --file %input_file%

echo.
echo Press any key to exit...
pause >nul
exit