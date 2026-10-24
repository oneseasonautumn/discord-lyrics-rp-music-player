@echo off
cd /d "%~dp0"

where python >nul 2>nul
if %ERRORLEVEL%==0 (
    python deploy.py %*
    exit /b %ERRORLEVEL%
)

where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3 deploy.py %*
    exit /b %ERRORLEVEL%
)

echo Python 3.10+ is required but was not found.
exit /b 1