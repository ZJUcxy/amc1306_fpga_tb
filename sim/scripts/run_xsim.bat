@echo off
rem ===========================================================================
rem  run_xsim.bat -- run tb_amc1306_sample with the Vivado 2022.2 command line
rem ---------------------------------------------------------------------------
rem  Usage:  double-click, or from a cmd prompt:  run_xsim.bat
rem  Needs:  xvlog / xelab / xsim on PATH (Vivado 2022.2 settings64.bat run).
rem          If they are not, this script tries the usual install locations.
rem ===========================================================================
setlocal
set HERE=%~dp0
set SIM=%~dp0..
set DATA=%SIM%\data
set RTL=%SIM%\rtl
set TB=%SIM%\tb
set WORK=%SIM%\work

rem ---- locate a Vivado 2022.2 environment if it is not already set ----------
if not defined XILINX_VIVADO (
    for %%D in (C D E F G) do (
        if exist "%%D:\Xilinx\Vivado\2022.2\settings64.bat" call "%%D:\Xilinx\Vivado\2022.2\settings64.bat" >nul 2>nul
    )
)
where xvlog >nul 2>nul
if errorlevel 1 (
    echo.
    echo ERROR: xvlog was not found on PATH.
    echo        Run this from a Vivado 2022.2 command shell, or edit the
    echo        settings64.bat path above.  Example:
    echo            call C:\Xilinx\Vivado\2022.2\settings64.bat
    echo.
    exit /b 1
)

if exist "%WORK%" rmdir /s /q "%WORK%"
mkdir "%WORK%" >nul 2>nul
pushd "%WORK%"

echo.
echo [1/3] compiling ...
xvlog -i "%DATA%" "%RTL%\amc1306_sample_stub.v" "%TB%\tb_amc1306_sample.v"
if errorlevel 1 goto :fail

echo.
echo [2/3] elaborating ...
xelab -debug typical tb_amc1306_sample -s tb_sim
if errorlevel 1 goto :fail

echo.
echo [3/3] simulating ...
xsim tb_sim -runall -testplusarg "MEM=%DATA%\scenario.mem"
if errorlevel 1 goto :fail

popd
echo.
echo Done. Waveform database: %WORK%\xsim.dir\tb_sim
exit /b 0

:fail
popd
echo.
echo FAILED -- see the messages above.
exit /b 1
