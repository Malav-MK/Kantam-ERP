<#
  Supervises one Kantam ERP process. Started at boot by a scheduled task running as
  the low-privilege LOCAL SERVICE account; restarts the process if it exits.
  Not meant to be run by hand - use KantamAdmin.ps1 start|stop|status.
#>
param(
    [Parameter(Mandatory = $true)][ValidateSet('backend', 'worker', 'gateway')][string]$Service,
    [Parameter(Mandatory = $true)][string]$DataDir
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Kantam.Common.ps1')

$InstallDir = Split-Path -Parent $PSScriptRoot
$AppDir = Join-Path $InstallDir 'app'
$LogDir = Join-Path $DataDir 'logs'
$RunDir = Join-Path $DataDir 'run'
$cfg = Import-KantamEnv (Get-KantamEnvFile $DataDir)
$env:PYTHONUNBUFFERED = '1'
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'

switch ($Service) {
    'backend' {
        $exe = Join-Path $InstallDir 'venv\Scripts\waitress-serve.exe'
        $argList = @('--listen=127.0.0.1:8000', '--threads=16', '--channel-timeout=120', '--ident=kantam', 'kantam.wsgi:application')
        $workDir = Join-Path $AppDir 'backend'
    }
    'worker' {
        $exe = Join-Path $InstallDir 'venv\Scripts\python.exe'
        $argList = @('manage.py', 'run_worker')
        $workDir = Join-Path $AppDir 'backend'
    }
    'gateway' {
        $exe = $cfg['NODE_EXE']
        $argList = @('src/server.js')
        $workDir = Join-Path $AppDir 'gateway'
    }
}

$runnerLog = Join-Path $LogDir "$Service-runner.log"
function Write-RunnerLog([string]$Message) {
    Add-Content -LiteralPath $runnerLog -Value ("{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $Message) -Encoding ASCII
}

$delay = 5
while ($true) {
    # Keep 30 days of logs.
    Get-ChildItem -LiteralPath $LogDir -Filter "$Service-*.log" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne "$Service-runner.log" -and $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
        Remove-Item -Force -ErrorAction SilentlyContinue

    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $started = Get-Date
    Write-RunnerLog "starting $Service"
    $proc = Start-Process -FilePath $exe -ArgumentList $argList -WorkingDirectory $workDir -NoNewWindow -PassThru `
        -RedirectStandardOutput (Join-Path $LogDir "$Service-$stamp.out.log") `
        -RedirectStandardError (Join-Path $LogDir "$Service-$stamp.err.log")
    Set-Content -LiteralPath (Join-Path $RunDir "$Service.pid") -Value $proc.Id -Encoding ASCII
    $proc.WaitForExit()
    Write-RunnerLog "$Service exited with code $($proc.ExitCode)"

    # Back off if it keeps crashing; reset once it has stayed up for a while.
    if (((Get-Date) - $started).TotalMinutes -gt 5) { $delay = 5 } else { $delay = [Math]::Min($delay * 2, 120) }
    Start-Sleep -Seconds $delay
}
