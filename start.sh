#!/bin/bash

# AI Process Report Startup Script
echo "Starting AI Process Report..."

# Check if Python is installed
if ! command -v python3 &> /dev/null
then
    echo "Python 3 is not installed or not in the system PATH."
    echo "Please install Python 3.7 or higher and add it to your system PATH."
    exit 1
fi

# Check if venv folder exists
if [ ! -d "venv" ]; then
    echo "Virtual environment not found. Creating a new one..."
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo "Failed to create virtual environment."
        echo "Please ensure you have venv module installed."
        exit 1
    fi
    FRESH_VENV=1
else
    echo "Virtual environment found."
    FRESH_VENV=0
fi

# Activate the virtual environment
source venv/bin/activate
if [ $? -ne 0 ]; then
    echo "Failed to activate the virtual environment."
    exit 1
fi

# Check if requirements.txt exists
if [ ! -f "requirements.txt" ]; then
    echo "requirements.txt not found."
    echo "Please ensure the file is in the same directory as this script."
    exit 1
fi

# Install or upgrade pip if it's a fresh venv
if [ $FRESH_VENV -eq 1 ]; then
    echo "Upgrading pip..."
    pip install --upgrade pip
    if [ $? -ne 0 ]; then
        echo "Failed to upgrade pip."
        exit 1
    fi
fi

# Check if requirements need to be installed or updated
INSTALL_REQS=0
if [ $FRESH_VENV -eq 1 ]; then
    INSTALL_REQS=1
else
    pip freeze > installed_reqs.txt
    if ! cmp -s requirements.txt installed_reqs.txt; then
        INSTALL_REQS=1
    fi
    rm installed_reqs.txt
fi

# Install required packages if necessary
if [ $INSTALL_REQS -eq 1 ]; then
    echo "Installing/Updating required packages..."
    pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "Failed to install required packages."
        echo "Please check your internet connection and try again."
        exit 1
    fi
else
    echo "Required packages are up to date."
fi

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo ".env file not found."
    echo "Please create a .env file with your configuration settings."
    exit 1
fi

# Run the main script
echo "Starting the AI Process Report script..."
python main.py
if [ $? -ne 0 ]; then
    echo "An error occurred while running the script."
    exit 1
fi

# Deactivate the virtual environment
deactivate

echo "Script execution completed."