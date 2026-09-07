@echo off
REM Build a single-file Windows .exe for the Scanner Bridge Service.
REM Run this ON WINDOWS, inside a venv where requirements.txt is installed.

pyinstaller --noconfirm --onefile --windowed ^
    --name "ScannerBridge" ^
    --icon "green.ico" ^
    --add-data "green.ico;." ^
    --add-data "red.ico;." ^
    --hidden-import win32timezone ^
    --hidden-import win32com.client ^
    --hidden-import pythoncom ^
    --hidden-import pystray._win32 ^
    --hidden-import PIL._tkinter_finder ^
    scanner_service.py

echo.
echo Build finished. Find ScannerBridge.exe in the dist\ folder.
pause
