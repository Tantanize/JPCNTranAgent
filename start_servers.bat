@echo off
:: start_servers.bat
:: Run from the subtitle_agent root directory.
:: Starts all four MCP servers in separate windows.

setlocal
set ROOT=%~dp0
set LOGS=%ROOT%logs
if not exist "%LOGS%" mkdir "%LOGS%"

call :start_server transcribe    8771
call :start_server ass_bilingual 8772
call :start_server ass_translate 8773
call :start_server ass_burnin    8774

echo.
echo All servers started in separate windows.
echo Close those windows or run stop_servers.bat to stop them.
goto :eof

:start_server
set NAME=%1
set PORT=%2
set VENV=%ROOT%%NAME%\.venv
set PYTHON=%VENV%\Scripts\python.exe
set PIP=%VENV%\Scripts\pip.exe

if not exist "%PYTHON%" (
    echo [setup] Creating venv for %NAME% ...
    python -m venv "%VENV%"
    "%PIP%" install -q --upgrade pip
    "%PIP%" install -q -r "%ROOT%%NAME%\requirements.txt"
)

echo [start] %NAME% on port %PORT%
start "%NAME% :%PORT%" "%PYTHON%" "%ROOT%%NAME%\server.py" --port %PORT%
goto :eof
