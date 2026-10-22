<#
.SYNOPSIS
  Day-to-day administration of an installed Kantam ERP (run as administrator).

.EXAMPLE
  KantamAdmin.ps1 status
  KantamAdmin.ps1 start | stop | restart [backend|worker|gateway]
  KantamAdmin.ps1 logs gateway -Tail 200
  KantamAdmin.ps1 new-device "Stores tablet" -Kind tablet
  KantamAdmin.ps1 create-user rajesh -Name "Rajesh Patel" -Role store_staff -Department STORE
  KantamAdmin.ps1 backup [-Path D:\Backups]
  KantamAdmin.ps1 restore -File D:\Backups\kantam-20261022-0900.kbak
  KantamAdmin.ps1 reset            # wipe ALL data and start again with a new owner account
  KantamAdmin.ps1 manage <django-command> [args]
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)][ValidateSet('status', 'start', 'stop', 'restart', 'logs', 'new-device', 'create-user', 'backup', 'restore', 'reset', 'manage', 'help')]
    [string]$Command = 'help',
    [Parameter(Position = 1)][string]$Target = '',
    [int]$Tail = 100,
    [ValidateSet('pc', 'tablet', 'phone', 'gate', 'tv')][string]$Kind = 'pc',
    [string]$Name = '',
    [string[]]$Role = @(),
    [string]$Department = '',
    [string]$Path = '',
    [string]$File = '',
    [switch]$NoBackup,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest = @()
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Kantam.Common.ps1')

$InstallDir = Split-Path -Parent $PSScriptRoot
$DataDir = Get-KantamDataDir
$EnvFile = Get-KantamEnvFile $DataDir
$AppBackend = Join-Path $InstallDir 'app\backend'
$VenvPython = Join-Path $InstallDir 'venv\Scripts\python.exe'

function Invoke-Manage([string[]]$Arguments) {
    Import-KantamEnv $EnvFile | Out-Null
    $env:PYTHONUTF8 = '1'
    Push-Location $AppBackend
    try { Invoke-Checked $VenvPython (@('manage.py') + $Arguments) "manage.py $($Arguments[0])" } finally { Pop-Location }
}

function Get-Targets {
    if ($Target) {
        if ($Target -notin $KantamServices) { throw "Unknown service '$Target'. Use one of: $($KantamServices -join ', ')" }
        return @($Target)
    }
    return $KantamServices
}

function Show-Status {
    $cfg = Read-KantamEnv $EnvFile
    Write-Host ''
    foreach ($s in $KantamServices) {
        $svc = Get-KantamService $s
        $state = if (-not $svc) { 'NOT INSTALLED - re-run Install-Kantam.ps1' } else { "$($svc.Status)".ToLower() + "  ($($svc.StartType) start)" }
        $color = if ($svc -and $svc.Status -eq 'Running') { 'Green' } else { 'Yellow' }
        Write-Host ("  {0,-8} {1}" -f $s, $state) -ForegroundColor $color
    }
    $port = if ($cfg['PORT']) { [int]$cfg['PORT'] } else { 443 }
    $healthy = Test-KantamHealth $port 10
    Write-Host ("  {0,-8} {1}" -f 'health', $(if ($healthy) { "OK  https://$($cfg['KANTAM_HOSTNAME']):$port" } else { 'not responding' })) -ForegroundColor $(if ($healthy) { 'Green' } else { 'Red' })
    if (-not $healthy) { Show-KantamServiceProblems $DataDir }
    Write-Host ''
}

function Invoke-Backup([string]$Destination) {
    Import-KantamEnv $EnvFile | Out-Null
    if (-not $Destination) { $Destination = Join-Path $DataDir 'backups' }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $work = Join-Path $env:TEMP "kantam-backup-$stamp"
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    try {
        Write-Step 'Dumping the database'
        $env:PGPASSWORD = $env:POSTGRES_PASSWORD
        try {
            Invoke-Checked (Join-Path $env:PG_BIN 'pg_dump.exe') @('-h', $env:POSTGRES_HOST, '-p', $env:POSTGRES_PORT, '-U', $env:POSTGRES_USER,
                '-d', $env:POSTGRES_DB, '-Fc', '--no-owner', '--no-privileges', '-f', (Join-Path $work 'database.dump')) 'pg_dump'
        } finally { Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue }
        Write-Step 'Collecting evidence files and signing keys'
        foreach ($d in 'media', 'keys') {
            $src = Join-Path $DataDir $d
            if (Test-Path $src) { Copy-Item -LiteralPath $src -Destination (Join-Path $work $d) -Recurse -Force }
        }
        Set-Content -LiteralPath (Join-Path $work 'manifest.txt') -Encoding ASCII -Value @(
            "Kantam ERP backup $stamp", "Host: $env:COMPUTERNAME", "Database: $env:POSTGRES_DB")
        $zip = Join-Path $work 'payload.zip'
        Compress-Archive -Path (Join-Path $work '*') -DestinationPath $zip -CompressionLevel Optimal
        Write-Step 'Encrypting'
        $pass = Read-NewPassword '    Backup passphrase (needed to restore - keep it in your password safe)'
        $out = Join-Path $Destination "kantam-$stamp.kbak"
        Protect-KantamFile $zip $out $pass
        Write-Ok "Backup written: $out ($([Math]::Round((Get-Item $out).Length / 1MB, 1)) MB)"
        Write-Note 'A backup counts only once a restore has been tested. Copy it off this server.'
        return $out
    } finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}

function Confirm-Typed([string]$Word, [string]$Message) {
    Write-Host $Message -ForegroundColor Yellow
    if ((Read-Host "Type $Word to continue") -cne $Word) { throw 'Cancelled.' }
}

function Reset-DataFolders {
    foreach ($d in 'media', 'keys') {
        $p = Join-Path $DataDir $d
        if (Test-Path -LiteralPath $p) { Get-ChildItem -LiteralPath $p -Force | Remove-Item -Recurse -Force }
    }
}

function Invoke-Restore([string]$BackupFile) {
    if (-not $BackupFile -or -not (Test-Path -LiteralPath $BackupFile)) { throw 'Give the backup with -File <path to .kbak>.' }
    Confirm-Typed 'RESTORE' "This replaces ALL current ERP data with the backup $BackupFile."
    Import-KantamEnv $EnvFile | Out-Null
    $pass = ConvertFrom-SecureStringPlain (Read-Host -AsSecureString 'Backup passphrase')
    $work = Join-Path $env:TEMP ("kantam-restore-{0:yyyyMMddHHmmss}" -f (Get-Date))
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    try {
        Write-Step 'Decrypting and checking the backup'
        $zip = Join-Path $work 'payload.zip'
        Unprotect-KantamFile $BackupFile $zip $pass
        Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $work 'x')
        $x = Join-Path $work 'x'
        if (-not (Test-Path (Join-Path $x 'database.dump'))) { throw 'The backup has no database dump.' }

        Write-Step 'Stopping services'
        foreach ($s in $KantamServices) { Stop-KantamService $s $DataDir }

        Write-Step 'Restoring the database'
        $env:PGPASSWORD = $env:POSTGRES_PASSWORD
        try {
            $psql = Join-Path $env:PG_BIN 'psql.exe'
            Invoke-Checked $psql @('-h', $env:POSTGRES_HOST, '-p', $env:POSTGRES_PORT, '-U', $env:POSTGRES_USER, '-d', $env:POSTGRES_DB, '-v', 'ON_ERROR_STOP=1', '-q',
                '-c', 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;') 'Clearing the database'
            $r = Invoke-NativeCapture (Join-Path $env:PG_BIN 'pg_restore.exe') @('-h', $env:POSTGRES_HOST, '-p', $env:POSTGRES_PORT, '-U', $env:POSTGRES_USER,
                '-d', $env:POSTGRES_DB, '--no-owner', '--no-privileges', (Join-Path $x 'database.dump'))
            $r.Output | Where-Object { $_ -and $_ -notmatch 'schema "public" already exists' } | ForEach-Object { Write-Host "    $_" }
            if ($r.ExitCode -ne 0) { Write-Note 'pg_restore reported warnings (listed above). Checking the schema next.' }
        } finally { Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue }

        Write-Step 'Restoring evidence files and signing keys'
        Reset-DataFolders
        foreach ($d in 'media', 'keys') {
            $src = Join-Path $x $d
            if (Test-Path $src) { Get-ChildItem -LiteralPath $src -Force | Copy-Item -Destination (Join-Path $DataDir $d) -Recurse -Force }
        }
        Set-KantamDataAcl $DataDir
        Invoke-Manage @('migrate', '--noinput')
        Invoke-Manage @('sync_rbac')
        $chain = Invoke-NativeCapture $VenvPython @((Join-Path $AppBackend 'manage.py'), 'shell', '-c',
            'from apps.core.audit import verify_chain; print("audit chain", verify_chain())')
        Write-Host ("    " + ($chain.Output | Select-Object -Last 1))

        Write-Step 'Starting services'
        foreach ($s in $KantamServices) { Start-KantamService $s }
        Show-Status
        Write-Ok 'Restore finished. Everyone must sign in again; enrolled devices keep working.'
    } finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}

function Invoke-Reset {
    Confirm-Typed 'RESET' 'This permanently deletes ALL ERP data on this server: users, orders, stock, audit history, photos and enrolled devices.'
    if (-not $NoBackup) {
        Write-Note 'Taking a safety backup first (use -NoBackup to skip).'
        Invoke-Backup '' | Out-Null
    }
    Write-Step 'Stopping services'
    foreach ($s in $KantamServices) { Stop-KantamService $s $DataDir }
    Write-Step 'Clearing the database, evidence files and signing keys'
    Import-KantamEnv $EnvFile | Out-Null
    $env:PGPASSWORD = $env:POSTGRES_PASSWORD
    try {
        Invoke-Checked (Join-Path $env:PG_BIN 'psql.exe') @('-h', $env:POSTGRES_HOST, '-p', $env:POSTGRES_PORT, '-U', $env:POSTGRES_USER, '-d', $env:POSTGRES_DB,
            '-v', 'ON_ERROR_STOP=1', '-q', '-c', 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;') 'Clearing the database'
    } finally { Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue }
    Reset-DataFolders
    Invoke-Manage @('migrate', '--noinput')
    Write-Step 'Creating the new owner account'
    $env:KANTAM_OWNER_PASSWORD = Read-NewPassword '    New owner password'
    try { Invoke-Manage @('bootstrap', '--owner', 'owner', '--owner-name', 'Owner', '--password-env', 'KANTAM_OWNER_PASSWORD', '--device-name', "$env:COMPUTERNAME (server console)") }
    finally { Remove-Item Env:\KANTAM_OWNER_PASSWORD -ErrorAction SilentlyContinue }
    Set-KantamDataAcl $DataDir
    Write-Step 'Starting services'
    foreach ($s in $KantamServices) { Start-KantamService $s }
    Show-Status
    Write-Ok 'Reset complete. Sign in as "owner" with the enrolment code shown above. Every other device must be enrolled again.'
}

Assert-Administrator
Repair-KantamDataAccess $DataDir
switch ($Command) {
    'status' { Show-Status }
    'start' { foreach ($s in Get-Targets) { Start-KantamService $s }; Start-Sleep 3; Show-Status }
    'stop' { foreach ($s in Get-Targets) { Stop-KantamService $s $DataDir }; Show-Status }
    'restart' {
        foreach ($s in Get-Targets) { Stop-KantamService $s $DataDir }
        foreach ($s in Get-Targets) { Start-KantamService $s }
        Start-Sleep 3; Show-Status
    }
    'logs' {
        $svc = if ($Target) { $Target } else { 'gateway' }
        $files = Get-ChildItem -LiteralPath (Join-Path $DataDir 'logs') -Filter "$(Get-KantamServiceName $svc)*.log" | Sort-Object LastWriteTime -Descending | Select-Object -First 3
        foreach ($f in ($files | Sort-Object LastWriteTime)) { Write-Host "--- $($f.Name)" -ForegroundColor Cyan; Get-Content -LiteralPath $f.FullName -Tail $Tail }
    }
    'new-device' {
        if (-not $Target) { throw 'Give the device name, e.g. KantamAdmin.ps1 new-device "Gate tablet" -Kind gate' }
        Invoke-Manage @('create_device', $Target, '--kind', $Kind)
        Write-Note 'Open the ERP on that device and enter the code within 24 hours.'
    }
    'create-user' {
        if (-not $Target -or -not $Name -or $Role.Count -eq 0) { throw 'Usage: create-user <username> -Name "Full name" -Role <role>[,<role>] [-Department CODE]' }
        $a = @('create_user', $Target, '--name', $Name)
        foreach ($r in $Role) { $a += @('--role', $r) }
        if ($Department) { $a += @('--department', $Department) }
        $env:KANTAM_NEW_USER_PASSWORD = Read-NewPassword "    Temporary password for $Target (they must change it at first sign-in)"
        try { Invoke-Manage ($a + @('--password-env', 'KANTAM_NEW_USER_PASSWORD')) } finally { Remove-Item Env:\KANTAM_NEW_USER_PASSWORD -ErrorAction SilentlyContinue }
    }
    'backup' { Invoke-Backup $Path | Out-Null }
    'restore' { Invoke-Restore $File }
    'reset' { Invoke-Reset }
    'manage' {
        if (-not $Target) { throw 'Usage: KantamAdmin.ps1 manage <command> [args]' }
        Invoke-Manage (@($Target) + $Rest)
    }
    default { Get-Help $PSCommandPath -Examples }
}
