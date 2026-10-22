<#
.SYNOPSIS
  Removes Kantam ERP from this server.

.DESCRIPTION
  By default removes the program, services, firewall rule and certificate trust, and KEEPS the
  data folder and database so a reinstall picks up where it left off.
  -RemoveData also deletes the database and the data folder (take a backup first).
  Python, Node.js and PostgreSQL themselves are left installed.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:ProgramFiles 'Kantam ERP'),
    [string]$DataDir = (Join-Path $env:ProgramData 'Kantam ERP'),
    [switch]$RemoveData
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Kantam.Common.ps1')
Assert-Administrator

$EnvFile = Get-KantamEnvFile $DataDir
$cfg = Read-KantamEnv $EnvFile
if ($RemoveData) {
    Write-Host 'This deletes the Kantam database and every data file (photos, keys, backups in the data folder).' -ForegroundColor Yellow
    if ((Read-Host 'Type DELETE to continue') -cne 'DELETE') { throw 'Cancelled.' }
}

Write-Step 'Stopping and removing services'
foreach ($s in $KantamServices) {
    Stop-KantamService $s $DataDir
    $task = Get-KantamTask $s
    if ($task) { Unregister-ScheduledTask -InputObject $task -Confirm:$false }
}
Write-Ok 'Services removed.'

Write-Step 'Removing firewall rule and certificate trust'
Get-NetFirewallRule -DisplayName 'Kantam ERP (HTTPS)' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
if ($cfg['TLS_THUMBPRINT']) {
    Remove-Item -LiteralPath "Cert:\LocalMachine\Root\$($cfg['TLS_THUMBPRINT'])" -ErrorAction SilentlyContinue
}
Write-Ok 'Done.'

if ($RemoveData -and $cfg['PG_BIN']) {
    Write-Step 'Dropping the database'
    $env:PGPASSWORD = $cfg['POSTGRES_PASSWORD']
    try {
        # The database owner may drop its own database; the login role is left for the DBA to remove.
        & (Join-Path $cfg['PG_BIN'] 'psql.exe') -h $cfg['POSTGRES_HOST'] -p $cfg['POSTGRES_PORT'] -U $cfg['POSTGRES_USER'] -d postgres -q -c 'DROP DATABASE IF EXISTS kantam'
        if ($LASTEXITCODE -ne 0) { Write-Note 'Could not drop the database; remove it manually with pgAdmin or psql.' } else { Write-Ok 'Database dropped.' }
    } finally { Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue }
}

Write-Step 'Removing program files'
# This script may run from inside the install folder: PowerShell has already loaded it, but the
# working directory must not be inside the folder being deleted.
Set-Location -LiteralPath $env:TEMP
if (Test-Path -LiteralPath $InstallDir) { Remove-Item -LiteralPath $InstallDir -Recurse -Force }
Write-Ok "Removed $InstallDir"

if ($RemoveData) {
    if (Test-Path -LiteralPath $DataDir) { Remove-Item -LiteralPath $DataDir -Recurse -Force }
    Write-Ok "Removed $DataDir"
} else {
    Write-Note "Data kept in $DataDir and the 'kantam' database. Reinstalling will reuse them."
}
