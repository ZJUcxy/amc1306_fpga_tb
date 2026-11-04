<#
    run_xsim.ps1 -- compile and run the AMC1306 testbenches with xsim.

    Usage:
        powershell -ExecutionPolicy Bypass -File run_xsim.ps1 [chain|fir|all]

    Why PowerShell and not a .bat: this has to import Vivado's settings64.bat
    into the current process, copy the bitstream into the xsim run directory
    (xsim v2022.2 rejects `-testplusarg MEM=<path>` when the path contains a
    drive letter), and report the pass/fail summary.
#>
param(
    [ValidateSet('chain', 'fir', 'all')]
    [string]$What = 'all'
)

$ErrorActionPreference = 'Stop'

$sim  = Split-Path -Parent $PSScriptRoot      # ...\sim
$data = Join-Path $sim 'data'
$rtl  = Join-Path $sim 'rtl'
$tb   = Join-Path $sim 'tb'
$work = Join-Path $sim 'work'
$logs = Join-Path $sim 'logs'

# ---------------------------------------------------------------- Vivado ----
if (-not $env:XILINX_VIVADO) {
    $candidates = @(
        'F:\FPGA\Vivado\2022.2',
        'C:\Xilinx\Vivado\2022.2',
        'D:\Xilinx\Vivado\2022.2',
        'E:\Xilinx\Vivado\2022.2',
        'C:\AMD\Vivado\2022.2'
    )
    foreach ($c in $candidates) {
        $settings = Join-Path $c 'settings64.bat'
        if (Test-Path $settings) {
            Write-Host "==> importing $settings"
            $lines = & cmd /c "call `"$settings`" >nul 2>nul && set"
            foreach ($l in $lines) {
                if ($l -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
                    [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
                }
            }
            break
        }
    }
}
if (-not (Get-Command xvlog -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: xvlog not found." -ForegroundColor Red
    Write-Host "       Set up Vivado 2022.2 first, e.g.:"
    Write-Host "         call F:\FPGA\Vivado\2022.2\settings64.bat"
    exit 1
}
Write-Host "==> $(& xvlog --version 2>&1 | Select-Object -Last 1)"

# ---------------------------------------------------------------- compile ---
if (Test-Path $work) { Remove-Item -Recurse -Force $work }
New-Item -ItemType Directory -Force -Path $work, $logs | Out-Null
Push-Location $work
try {
    Write-Host "==> xvlog"
    & xvlog -i $data -i $rtl `
        "$rtl\amc1306_sample_stub.v" "$rtl\fir_lpf.v" "$rtl\amc1306_chain.v" `
        "$tb\tb_amc1306_sample.v" "$tb\tb_fir_lpf.v" *> compile.out
    if ($LASTEXITCODE -ne 0) { Get-Content compile.out -Tail 30; throw "xvlog failed" }
    $warn = Select-String -Path compile.out -Pattern 'ERROR|WARNING'
    if ($warn) { Write-Host "   $($warn.Count) ERROR/WARNING line(s), see compile.out" }

    # xsim cannot take an absolute MEM= plusarg, the TB falls back to the
    # default file name, so put the bitstream where the simulation runs
    Copy-Item "$data\scenario.mem" "$work\scenario.mem" -Force

    function Invoke-Tb([string]$top, [string]$snap, [string]$log) {
        Write-Host "==> $top"
        & xelab -debug typical $top -s $snap *> "elab_$snap.out"
        if ($LASTEXITCODE -ne 0) { Get-Content "elab_$snap.out" -Tail 30; throw "xelab $top failed" }
        $sw = [Diagnostics.Stopwatch]::StartNew()
        & xsim $snap --runall *> $log
        $sec = [int]$sw.Elapsed.TotalSeconds
        Copy-Item $log (Join-Path $logs (Split-Path -Leaf $log)) -Force
        $res = Select-String -Path $log -Pattern 'RESULT:'
        Write-Host "    $($res.Line.Trim())   (${sec}s)"
    }

    if ($What -eq 'chain' -or $What -eq 'all') { Invoke-Tb 'tb_amc1306_sample' 'tb_chain' 'sim_chain.out' }
    if ($What -eq 'fir'   -or $What -eq 'all') { Invoke-Tb 'tb_fir_lpf'       'tb_fir'   'sim_fir.out' }

    Write-Host ""
    Write-Host "logs: $logs"
}
finally {
    Pop-Location
}
