@echo off
setlocal
set "VCVARS=%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
if not exist "%VCVARS%" (
    echo Microsoft Visual C++ Build Tools 2022 were not found.
    exit /b 1
)
call "%VCVARS%" >nul
if errorlevel 1 exit /b %errorlevel%
where cl.exe >nul 2>nul
if errorlevel 1 (
    echo Microsoft Visual C++ environment could not be initialized.
    exit /b 1
)
python "%~dp0setup.py" build_ext --inplace %*
exit /b %errorlevel%
