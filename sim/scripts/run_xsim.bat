@echo off
rem ===========================================================================
rem  run_xsim.bat -- run the AMC1306 testbenches with the Vivado 2022.2 CLI
rem ---------------------------------------------------------------------------
rem  Usage:  run_xsim.bat [chain|fir|all]      (default: all)
rem            chain = tb_amc1306_sample  (AMC1306 chain + FIR, DC sweep)
rem            fir   = tb_fir_lpf         (filter response, impulse/DC/sine)
rem
rem  Needs:  xvlog / xelab / xsim on PATH (Vivado 2022.2 settings64.bat).
rem ===========================================================================
setlocal
set HERE=%~dp0
set SIM=%~dp0..
set DATA=%SIM%\data
set RTL=%SIM%\rtl
set TB=%SIM%\tb
set WORK=%SIM%\work
set WHAT=%1
if "%WHAT%"=="" set WHAT=all

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
echo [1/2] compiling ...
xvlog -i "%DATA%" -i "%RTL%" ^
      "%RTL%\amc1306_sample_stub.v" ^
      "%RTL%\fir_lpf.v" ^
      "%RTL%\amc1306_chain.v" ^
      "%TB%\tb_amc1306_sample.v" ^
      "%TB%\tb_fir_lpf.v"
if errorlevel 1 goto :fail

if /i "%WHAT%"=="fir"   goto :run_fir
if /i "%WHAT%"=="chain" goto :run_chain

:run_chain
echo.
echo [2/2] chain testbench: tb_amc1306_sample
xelab -debug typical tb_amc1306_sample -s tb_chain
if errorlevel 1 goto :fail
xsim tb_chain -runall -testplusarg "MEM=%DATA%\scenario.mem"
if errorlevel 1 goto :fail
if /i "%WHAT%"=="chain" goto :done

:run_fir
echo.
echo [2/2] FIR testbench: tb_fir_lpf
xelab -debug typical tb_fir_lpf -s tb_fir
if errorlevel 1 goto :fail
xsim tb_fir -runall
if errorlevel 1 goto :fail

:done
popd
echo.
echo Done. Waveform databases: %WORK%\xsim.dir\
exit /b 0

:fail
popd
echo.
echo FAILED -- see the messages above.
exit /b 1
