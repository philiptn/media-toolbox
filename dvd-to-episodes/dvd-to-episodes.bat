@echo off
setlocal


:: Define paths
set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python311\python.exe"
set PYTHON_LINK=https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe

set VENV_DIR=.\.venv_win
set VENV_ACTIVATE=%VENV_DIR%\Scripts\activate.bat
set VENV_PIP=%VENV_DIR%\Scripts\pip.exe

set FFMPEG_EXE=.\.bin\ffmpeg\ffmpeg.exe
set FFMPEG_LINK=https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-7.1-full_build.7z

set MKVMERGE_EXE=.\.bin\mkvtoolnix\mkvmerge.exe
set MKVTOOLNIX_LINK=https://mkvtoolnix.download/windows/releases/89.0/mkvtoolnix-64-bit-89.0.7z


:: Ensure bin directory exists
if not exist .bin mkdir .bin

:: Check for ffmpeg executable
if not exist %FFMPEG_EXE% (
    <nul set /p="FFmpeg not found. Downloading... "
    if not exist .bin\ffmpeg mkdir .bin\ffmpeg
    curl -o .bin\ffmpeg\ffmpeg.zip %FFMPEG_LINK% >nul 2>&1
    tar -xf .bin\ffmpeg\ffmpeg.zip -C .bin\ffmpeg
    del .bin\ffmpeg\ffmpeg.zip

    :: Move all .exe files to bin\ffmpeg
    for /r .bin\ffmpeg %%F in (*.exe) do (
        move "%%F" .bin\ffmpeg >nul 2>&1
    )
    echo Done.
)

:: Check for mkvmerge executable
if not exist %MKVMERGE_EXE% (
    <nul set /p="MKVmerge not found. Downloading... "
    if not exist .bin\mkvtoolnix mkdir .bin\mkvtoolnix
    if not exist .bin\temp mkdir .bin\temp
    curl -o .bin\temp\mkvtoolnix.7z %MKVTOOLNIX_LINK% >nul 2>&1
    tar -xf .bin\temp\mkvtoolnix.7z -C .bin\mkvtoolnix
    rmdir /s /q "%cd%\.bin\temp"

    :: Move all .exe files to bin\mkvtoolnix
    for /r .bin\mkvtoolnix %%F in (*.exe) do (
        move "%%F" .bin\mkvtoolnix >nul 2>&1
    )
    echo Done.
)

:: Check for Python executable
if not exist "%PYTHON_EXE%" (
    <nul set /p="Python 3.11 not found. Installing... "
    if not exist .bin\temp mkdir .bin\temp
    curl -o .bin\temp\python_installer.exe %PYTHON_LINK% >nul 2>&1
    .bin\temp\python_installer.exe /quiet Include_launcher=0 AssociateFiles=0 Shortcuts=0 InstallLauncherAllUsers=0 InstallAllUsers=0
    rmdir /s /q "%cd%\.bin\temp"
    echo Done.
)

:: Check for virtual environment
if not exist %VENV_ACTIVATE% (
    <nul set /p="Virtual environment not found. Configuring... "
    %PYTHON_EXE% -m pip install --user virtualenv --no-warn-script-location >nul 2>&1
    %PYTHON_EXE% -m venv %VENV_DIR% >nul 2>&1
    call %VENV_ACTIVATE%
    echo Done.
) else (
    call %VENV_ACTIVATE%
)

:: Run the Python program
python dvd-to-episodes.py