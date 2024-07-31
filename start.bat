@echo off
setlocal enabledelayedexpansion

:: AI Process Report Startup Script
echo Starting AI Process Report...

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python is not installed or not in the system PATH.
    echo Please install Python 3.7 or higher and add it to your system PATH.
    pause
    exit /b 1
)

:: Check if venv folder exists
if not exist venv (
    echo Virtual environment not found. Creating a new one...
    python -m venv venv
    if !errorlevel! neq 0 (
        echo Failed to create virtual environment.
        echo Please ensure you have venv module installed.
        pause
        exit /b 1
    )
    set FRESH_VENV=1
) else (
    echo Virtual environment found.
    set FRESH_VENV=0
)

:: Activate the virtual environment
call venv\Scripts\activate.bat
if %errorlevel% neq 0 (
    echo Failed to activate the virtual environment.
    pause
    exit /b 1
)

:: Check if requirements.txt exists
if not exist requirements.txt (
    echo requirements.txt not found.
    echo Please ensure the file is in the same directory as this script.
    pause
    exit /b 1
)

:: Install or upgrade pip if it's a fresh venv
if %FRESH_VENV%==1 (
    echo Upgrading pip...
    python -m pip install --upgrade pip
    if %errorlevel% neq 0 (
        echo Failed to upgrade pip.
        pause
        exit /b 1
    )
)

:: Check if requirements need to be installed or updated
set INSTALL_REQS=0
if %FRESH_VENV%==1 set INSTALL_REQS=1
if %INSTALL_REQS%==0 (
    pip freeze > installed_reqs.txt
    fc /b requirements.txt installed_reqs.txt > nul
    if errorlevel 1 set INSTALL_REQS=1
    del installed_reqs.txt
)

:: Install required packages if necessary
if %INSTALL_REQS%==1 (
    echo Installing/Updating required packages...
    pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo Failed to install required packages.
        echo Please check your internet connection and try again.
        pause
        exit /b 1
    )
) else (
    echo Required packages are up to date.
)

:: Check if .env file exists
if not exist .env (
    echo .env file not found.
    echo Please create a .env file with your configuration settings.
    pause
    exit /b 1
)

:: Run the main script
echo Starting the AI Process Report script...
python main.py
if %errorlevel% neq 0 (
    echo An error occurred while running the script.
    pause
    exit /b 1
)

:: Deactivate the virtual environment
call venv\Scripts\deactivate.bat

echo Script execution completed.
pause