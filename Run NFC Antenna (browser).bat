@echo off
setlocal
title NFC Spiral Antenna Generator
cd /d "%~dp0"
set "LOG=%~dp0startup_log.txt"

echo ============================================================ > "%LOG%"
echo NFC Spiral Antenna Generator launcher   %DATE% %TIME%      >> "%LOG%"
echo Folder: %~dp0                                               >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo   NFC Spiral Antenna Generator
echo   Looking for Python...
echo.

set "PY="
set "PYARGS="

call :try "py" "-3"
call :try "python" ""
call :try "python3" ""
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :try "%%~D\python.exe" ""
for /d %%D in ("%ProgramFiles%\Python3*")                 do call :try "%%~D\python.exe" ""
for /d %%D in ("C:\Python3*")                             do call :try "%%~D\python.exe" ""
call :try "%USERPROFILE%\anaconda3\python.exe" ""
call :try "%USERPROFILE%\miniconda3\python.exe" ""
call :try "%USERPROFILE%\AppData\Local\anaconda3\python.exe" ""
call :try "C:\ProgramData\anaconda3\python.exe" ""
call :try "C:\ProgramData\miniconda3\python.exe" ""
call :try "%USERPROFILE%\Anaconda3\python.exe" ""
call :try "C:\Anaconda3\python.exe" ""

if not defined PY goto nopython

echo   Using: %PY% %PYARGS%
>>"%LOG%" echo   Using: %PY% %PYARGS%

"%PY%" %PYARGS% -c "import ezdxf, shapely" >nul 2>>"%LOG%"
if not errorlevel 1 goto haveDeps
echo   Installing ezdxf and shapely (DXF writer, clearance check)...
"%PY%" %PYARGS% -m pip install "ezdxf>=1.0" "shapely>=2.0" >>"%LOG%" 2>&1
:haveDeps

echo.
echo   Starting the generator in your web browser.
echo   Leave this window open while you use the software.
echo.
"%PY%" %PYARGS% server.py 2>>"%LOG%"
>>"%LOG%" echo Exit code: %ERRORLEVEL%
echo.
echo   The application has stopped. If this was unexpected, the file
echo   startup_log.txt in this folder has the details.
echo.
pause
exit /b 0

:nopython
echo.
echo   ------------------------------------------------------------
echo   Python was not found on this computer.
echo.
echo   Install Python 3.9 or newer from
echo       https://www.python.org/downloads/
echo   and TICK "Add python.exe to PATH" during the installation.
echo.
echo   Or use the online version - nothing to install:
echo       https://emadroshandel.github.io/nfc-spiral-antenna-generator/
echo   ------------------------------------------------------------
echo.
echo No working Python interpreter found. >> "%LOG%"
pause
exit /b 1

:try
if defined PY goto :eof
set "CAND=%~1"
set "CARGS=%~2"
>>"%LOG%" echo --- trying: %CAND% %CARGS%
"%CAND%" %CARGS% -c "import sys; assert sys.version_info >= (3, 9); print(sys.version)" >>"%LOG%" 2>&1
if errorlevel 1 goto :eof
set "PY=%CAND%"
set "PYARGS=%CARGS%"
>>"%LOG%" echo *** SELECTED: %CAND% %CARGS%
goto :eof
