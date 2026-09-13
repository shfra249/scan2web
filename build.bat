@echo off
REM Build a Windows app folder for the Scanner Bridge Service using
REM --onedir (NOT --onefile) plus --noupx, to minimize antivirus false
REM positives. See README section 7 for why --onefile gets flagged more
REM often than --onedir.
REM
REM IMPORTANT - 32-bit vs 64-bit compatibility:
REM PyInstaller bundles the interpreter it's run with, so the exe it
REM produces only runs on the SAME bitness (or wider) as that interpreter:
REM   - built with 32-bit Python  -> runs on BOTH 32-bit and 64-bit Windows
REM   - built with 64-bit Python  -> runs ONLY on 64-bit Windows
REM To make one build that works everywhere, run this venv with a 32-bit
REM Python install (download the "Windows installer (32-bit)" from
REM python.org, create the venv with it, then run this script from inside
REM that venv). Windows' WOW64 layer runs 32-bit binaries transparently on
REM 64-bit systems, so there is no downside to always building 32-bit.
REM
REM IMPORTANT - Antivirus false positives:
REM --onedir produces a folder with the exe and its libraries sitting in
REM plain view, instead of a single file that silently self-extracts to a
REM temp folder at runtime (the --onefile behavior most heuristic scanners
REM associate with malware droppers). --noupx disables UPX compression,
REM another major AV trigger. --version-file embeds real file metadata
REM (company/product name, version, description), since a file with none
REM at all looks more suspicious than one with normal-looking properties.
REM See README section 7 for the full explanation and further options
REM (code signing, false-positive submission) if still flagged after this.
REM
REM Run this ON WINDOWS, inside a venv where requirements.txt is installed.

python -c "import struct,sys; print('Building with a', struct.calcsize('P')*8, 'bit Python interpreter')"

pyinstaller --noconfirm --onedir --windowed --noupx ^
    --name "ScannerBridge" ^
    --icon "green.ico" ^
    --version-file "version_info.txt" ^
    --add-data "green.ico;." ^
    --add-data "red.ico;." ^
    --hidden-import win32timezone ^
    --hidden-import win32com.client ^
    --hidden-import pythoncom ^
    --hidden-import pystray._win32 ^
    --hidden-import PIL._tkinter_finder ^
    --hidden-import flask_cors ^
    scanner_service.py

echo.
echo Build finished. Find the app in dist\ScannerBridge\
echo The exe to run/distribute is: dist\ScannerBridge\ScannerBridge.exe
echo (Zip the whole ScannerBridge folder to share it - the exe needs the
echo  other files sitting next to it in that same folder to run.)
echo.
echo If the line above said "32 bit", this build runs on both 32-bit and 64-bit Windows.
echo If it said "64 bit", it will ONLY run on 64-bit Windows.
echo.
echo NOTE: unsigned exes can still be flagged by antivirus/SmartScreen even
echo with --onedir, though far less often than with --onefile. See README
echo section 7 for further options (code signing, false-positive report).
pause
