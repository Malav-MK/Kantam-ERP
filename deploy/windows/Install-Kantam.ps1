<#
.SYNOPSIS
  Installs or upgrades Kantam ERP + CRM on a Windows factory server.

.DESCRIPTION
  One script, safe to re-run (re-running upgrades the code and keeps data and secrets):
    1. Installs Python 3.12, Node.js LTS and PostgreSQL 16 with winget if they are missing.
    2. Copies the application to Program Files and builds it.
    3. Creates the database, generates secrets and a TLS certificate.
    4. Runs migrations and, on first install, creates the owner account.
    5. Registers three auto-start tasks (backend, worker, gateway) running as LOCAL SERVICE,
       and a firewall rule that admits only the approved plant networks.

.EXAMPLE
  .\Install-Kantam.ps1
  .\Install-Kantam.ps1 -AllowedCidrs 10.10.20.0/24,10.10.30.0/24 -HostName erp.kantam.local
  .\Install-Kantam.ps1 -Demo        # also loads illustrative demo data and demo users
  .\Install-Kantam.ps1 -BaseDir "D:\Kantam ERP"   # program and data under one folder on another drive
#>
[CmdletBinding()]
param(
    # One folder for everything, e.g. "D:\Kantam ERP" -> <BaseDir>\Program and <BaseDir>\Data.
    [string]$BaseDir = '',
    [string]$InstallDir = '',
    [string]$DataDir = '',
    [ValidateRange(1, 65535)][int]$Port = 443,
    [string]$HostName = $env:COMPUTERNAME,
    [string[]]$AllowedCidrs = @(),
    [string]$PgHost = 'localhost',
    [ValidateRange(1, 65535)][int]$PgPort = 5432,
    [string]$PgSuperUser = 'postgres',
    [string]$CertPfx = '',
    [switch]$SkipPrerequisites,
    [switch]$Demo,
    [switch]$Unattended
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$bootstrapOutput = $null
$PackageDir = $PSScriptRoot
$SourceRoot = (Resolve-Path (Join-Path $PackageDir '..\..')).Path
. (Join-Path $PackageDir 'Kantam.Common.ps1')

if ($BaseDir) {
    $BaseDir = [IO.Path]::GetFullPath($BaseDir).TrimEnd('\')
    if (-not $InstallDir) { $InstallDir = Join-Path $BaseDir 'Program' }
    if (-not $DataDir) { $DataDir = Join-Path $BaseDir 'Data' }
}
if (-not $InstallDir) { $InstallDir = Join-Path $env:ProgramFiles 'Kantam ERP' }
if (-not $DataDir) { $DataDir = Join-Path $env:ProgramData 'Kantam ERP' }
$InstallDir = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$DataDir = [IO.Path]::GetFullPath($DataDir).TrimEnd('\')

Assert-Administrator
if ($PSVersionTable.PSVersion.Major -lt 5) { throw 'PowerShell 5.1 or later is required.' }
if (-not (Test-Path (Join-Path $SourceRoot 'backend\manage.py'))) { throw "Application source not found next to this script ($SourceRoot)." }
Get-ChildItem -LiteralPath $SourceRoot -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue

foreach ($dir in @($InstallDir, $DataDir)) {
    $drive = [IO.Path]::GetPathRoot($dir)
    if (-not (Test-Path -LiteralPath $drive)) { throw "Drive $drive does not exist." }
    $fs = (Get-Volume -DriveLetter $drive.Substring(0, 1) -ErrorAction SilentlyContinue).FileSystem
    if ($fs -and $fs -ne 'NTFS' -and $fs -ne 'ReFS') { throw "Drive $drive uses $fs. Kantam ERP needs an NTFS drive (file permissions protect the secrets)." }
}
if ($BaseDir) {
    # Folders outside Program Files / ProgramData inherit loose drive-root permissions
    # (any signed-in user may modify). Protect the base folder first.
    New-Item -ItemType Directory -Force -Path $BaseDir | Out-Null
    Set-StrictAcl $BaseDir @("*$($KantamSids.LocalService):(RX)") -NoRecurse
}

# Repair a data folder left unreadable by an earlier installer version, then lock it down *before*
# any secret is written (ProgramData is readable by all users by default).
Repair-KantamDataAccess $DataDir
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
Set-KantamDataAcl $DataDir
$LogDir = Join-Path $DataDir 'logs'
$transcript = Join-Path $LogDir ("install-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))
Start-Transcript -Path $transcript | Out-Null

try {
    $EnvFile = Get-KantamEnvFile $DataDir
    $FirstInstall = -not (Test-Path -LiteralPath $EnvFile)
    $existing = Read-KantamEnv $EnvFile
    Write-Host ''
    Write-Host 'Kantam ERP + CRM - Windows installer' -ForegroundColor White
    Write-Host ("  Mode       : {0}" -f ($(if ($FirstInstall) { 'new installation' } else { 'upgrade (data and secrets kept)' })))
    Write-Host "  Program    : $InstallDir"
    Write-Host "  Data       : $DataDir"

    # ------------------------------------------------------------------ prerequisites
    function Update-SessionPath {
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    }
    function Install-WithWinget([string]$Id, [string[]]$Extra) {
        if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
            throw "winget is not available. Install '$Id' manually (machine-wide), then re-run this script."
        }
        Write-Note "Installing $Id with winget (this can take a few minutes)..."
        & winget.exe install --id $Id -e --scope machine --silent --accept-package-agreements --accept-source-agreements --disable-interactivity @Extra
        if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) { throw "winget could not install $Id (exit code $LASTEXITCODE)." }
        Update-SessionPath
    }
    function Find-Python {
        foreach ($v in '312', '313') {
            $p = Join-Path $env:ProgramFiles "Python$v\python.exe"
            if (Test-Path $p) { return $p }
        }
        return $null
    }
    function Find-Node {
        $p = Join-Path $env:ProgramFiles 'nodejs\node.exe'
        if (Test-Path $p) {
            $v = (& $p --version).TrimStart('v').Split('.')
            if ([int]$v[0] -gt 20 -or ([int]$v[0] -eq 20 -and [int]$v[1] -ge 19)) { return $p }
            Write-Note "Node.js $($v -join '.') is too old (need 20.19 or later)."
        }
        return $null
    }
    function Find-PgBin {
        $dirs = Get-ChildItem -Path (Join-Path $env:ProgramFiles 'PostgreSQL') -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^\d+$' -and [int]$_.Name -ge 14 -and (Test-Path (Join-Path $_.FullName 'bin\psql.exe')) } |
            Sort-Object { [int]$_.Name } -Descending
        if ($dirs) { return (Join-Path $dirs[0].FullName 'bin') }
        return $null
    }

    Write-Step 'Checking prerequisites'
    $python = Find-Python
    $node = Find-Node
    $pgBin = Find-PgBin
    $pgInstalledNow = $false
    $pgSuperPassword = $env:KANTAM_PG_SUPERPASSWORD
    if (-not $SkipPrerequisites) {
        if (-not $python) {
            Install-WithWinget 'Python.Python.3.12' @('--override', '/quiet InstallAllUsers=1 PrependPath=1 Include_launcher=1 Include_test=0')
            $python = Find-Python
        }
        if (-not $node) {
            Install-WithWinget 'OpenJS.NodeJS.LTS' @()
            $node = Find-Node
        }
        $localPg = $PgHost -in @('localhost', '127.0.0.1', '::1')
        if (-not $pgBin -and $localPg) {
            if (-not $pgSuperPassword) { $pgSuperPassword = New-KantamSecret 18 }
            Install-WithWinget 'PostgreSQL.PostgreSQL.16' @('--override',
                "--mode unattended --unattendedmodeui none --superpassword $pgSuperPassword --serverport $PgPort --disable-components stackbuilder,pgAdmin")
            $pgBin = Find-PgBin
            $pgInstalledNow = $true
        }
    }
    if (-not $python) { throw 'Python 3.12 (installed for all users under Program Files) was not found.' }
    if (-not $node) { throw 'Node.js 20.19+ (installed under Program Files\nodejs) was not found.' }
    if (-not $pgBin) { throw 'PostgreSQL client tools (psql, pg_dump) were not found under Program Files\PostgreSQL.' }
    $psql = Join-Path $pgBin 'psql.exe'
    $npm = Join-Path (Split-Path $node) 'npm.cmd'
    Write-Ok "Python     : $python"
    Write-Ok "Node.js    : $node ($(& $node --version))"
    Write-Ok "PostgreSQL : $pgBin"

    # ------------------------------------------------------------------ settings
    Write-Step 'Preparing configuration'
    function Get-LocalCidrs {
        Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and $_.PrefixLength -ge 8 -and $_.PrefixLength -le 30 } |
            ForEach-Object {
                $ip = [Net.IPAddress]::Parse($_.IPAddress).GetAddressBytes()
                [Array]::Reverse($ip)
                $mask = [uint32]([Math]::Pow(2, 32) - [Math]::Pow(2, 32 - $_.PrefixLength))
                $net = [BitConverter]::GetBytes([uint32]([BitConverter]::ToUInt32($ip, 0) -band $mask))
                [Array]::Reverse($net)
                "{0}/{1}" -f ([Net.IPAddress]::new($net).ToString()), $_.PrefixLength
            } | Sort-Object -Unique
    }
    $cidrPattern = '^((\d{1,3}\.){3}\d{1,3}/\d{1,2}|[0-9a-fA-F:]+/\d{1,3})$'
    if ($AllowedCidrs.Count -eq 0 -and $existing['ZT_ALLOWED_CIDRS']) {
        $AllowedCidrs = @($existing['ZT_ALLOWED_CIDRS'].Split(',') | Where-Object { $_ -and $_ -notin @('127.0.0.0/8', '::1/128') })
    }
    if ($AllowedCidrs.Count -eq 0) {
        $AllowedCidrs = @(Get-LocalCidrs)
        if ($AllowedCidrs.Count -eq 0) { throw 'Could not detect the plant network. Pass -AllowedCidrs, e.g. -AllowedCidrs 10.10.20.0/24' }
        Write-Note "Detected plant network(s): $($AllowedCidrs -join ', ')"
        Write-Note 'Only these networks will reach the ERP. Exclude guest Wi-Fi and machine-control networks.'
        if (-not $Unattended) {
            $answer = Read-Host '    Use these networks? [Y]es / [N]o (enter your own)'
            if ($answer -match '^[Nn]') {
                $AllowedCidrs = @((Read-Host '    Approved networks, comma separated (e.g. 10.10.20.0/24,10.10.30.0/24)').Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
            }
        }
    }
    $AllowedCidrs = @($AllowedCidrs | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    foreach ($c in $AllowedCidrs) { if ($c -notmatch $cidrPattern) { throw "Not a valid network (CIDR): $c" } }

    # (PowerShell variable names are case-insensitive: this updates the -Port parameter itself.)
    if (-not $PSBoundParameters.ContainsKey('Port') -and $existing['PORT']) { $Port = [int]$existing['PORT'] }
    $port = $Port
    $listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) {
        $owner = (Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue).ProcessName
        if ($owner -ne 'node') { throw "Port $port is already used by '$owner'. Choose another with -Port (e.g. -Port 8443)." }
    }

    $cfg = [ordered]@{}
    foreach ($k in $existing.Keys) { $cfg[$k] = $existing[$k] }
    if (-not $cfg['DJANGO_SECRET_KEY']) { $cfg['DJANGO_SECRET_KEY'] = New-KantamSecret 48 }
    if (-not $cfg['ZT_GATEWAY_SECRET']) { $cfg['ZT_GATEWAY_SECRET'] = New-KantamSecret 32 }
    if (-not $cfg['POSTGRES_PASSWORD']) { $cfg['POSTGRES_PASSWORD'] = New-KantamSecret 24 }
    if (-not $cfg['TLS_PFX_PASSPHRASE']) { $cfg['TLS_PFX_PASSPHRASE'] = New-KantamSecret 24 }
    $cfg['DJANGO_DEBUG'] = '0'
    $cfg['DJANGO_ALLOWED_HOSTS'] = '127.0.0.1,localhost'
    $cfg['POSTGRES_DB'] = 'kantam'
    $cfg['POSTGRES_USER'] = 'kantam'
    $cfg['POSTGRES_HOST'] = $PgHost
    $cfg['POSTGRES_PORT'] = "$PgPort"
    $cfg['POSTGRES_SSLMODE'] = 'prefer'
    $cfg['ZT_ALLOWED_CIDRS'] = (@($AllowedCidrs) + @('127.0.0.0/8', '::1/128') | Select-Object -Unique) -join ','
    $cfg['ZT_REQUIRE_DEVICE'] = 'true'
    $cfg['ZT_COOKIE_SECURE'] = 'true'
    $cfg['ZT_KEY_DIR'] = Join-Path $DataDir 'keys'
    $cfg['KANTAM_MEDIA_ROOT'] = Join-Path $DataDir 'media'
    $cfg['BACKEND_URL'] = 'http://127.0.0.1:8000'
    $cfg['HOST'] = '0.0.0.0'
    $cfg['PORT'] = "$port"
    $cfg['STATIC_DIR'] = Join-Path $InstallDir 'app\frontend\dist'
    $cfg['TLS_PFX_FILE'] = Join-Path $DataDir 'tls\kantam-erp.pfx'
    $cfg['NODE_EXE'] = $node
    $cfg['PG_BIN'] = $pgBin
    $cfg['KANTAM_HOSTNAME'] = $HostName

    Write-KantamEnv $EnvFile $cfg
    Write-Ok "Approved networks: $($cfg['ZT_ALLOWED_CIDRS'])"

    # ------------------------------------------------------------------ database
    Write-Step 'Preparing the PostgreSQL database'
    if ($pgInstalledNow) {
        $secretFile = Join-Path $DataDir 'admin\postgres-superuser.txt'
        Set-Content -LiteralPath $secretFile -Value "PostgreSQL superuser '$PgSuperUser' password: $pgSuperPassword" -Encoding ASCII
        Write-Note "PostgreSQL was installed. Its superuser password is saved in $secretFile (administrators only). Move it to your password safe."
    }
    if (-not $pgSuperPassword) {
        # An earlier (possibly failed) run may have installed PostgreSQL and saved its password.
        foreach ($candidate in @((Join-Path $DataDir 'admin\postgres-superuser.txt'), (Join-Path $env:ProgramData 'Kantam ERP\admin\postgres-superuser.txt'))) {
            if (-not $pgSuperPassword -and (Test-Path -LiteralPath (Split-Path $candidate))) {
                Repair-KantamDataAccess (Split-Path (Split-Path $candidate))
                if (Test-Path -LiteralPath $candidate) {
                    $m = [regex]::Match((Get-Content -LiteralPath $candidate -Raw), 'password: (\S+)')
                    if ($m.Success) {
                        $pgSuperPassword = $m.Groups[1].Value
                        Write-Ok "Using the PostgreSQL superuser password saved by the earlier installation ($candidate)."
                        if ($candidate -ne (Join-Path $DataDir 'admin\postgres-superuser.txt')) {
                            Copy-Item -LiteralPath $candidate -Destination (Join-Path $DataDir 'admin') -Force
                        }
                    }
                }
            }
        }
    }
    if (-not $pgSuperPassword) {
        if ($Unattended) { throw 'Set KANTAM_PG_SUPERPASSWORD for an unattended install against an existing PostgreSQL server.' }
        $pgSuperPassword = ConvertFrom-SecureStringPlain (Read-Host -AsSecureString "    Password of the existing PostgreSQL superuser '$PgSuperUser'")
    }
    $env:PGPASSWORD = $pgSuperPassword
    try {
        $common = @('-h', $PgHost, '-p', "$PgPort", '-U', $PgSuperUser, '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-tAq')
        $roleExists = (& $psql @common -c "SELECT 1 FROM pg_roles WHERE rolname = 'kantam'") -eq '1'
        if ($LASTEXITCODE -ne 0) { throw 'Could not connect to PostgreSQL with the superuser password.' }
        # Password is generated hex - safe inside a SQL literal.
        $pw = $cfg['POSTGRES_PASSWORD']
        if ($roleExists) { Invoke-Checked $psql ($common + @('-c', "ALTER ROLE kantam WITH LOGIN PASSWORD '$pw'")) 'Updating database role' }
        else { Invoke-Checked $psql ($common + @('-c', "CREATE ROLE kantam WITH LOGIN PASSWORD '$pw'")) 'Creating database role' }
        $dbExists = (& $psql @common -c "SELECT 1 FROM pg_database WHERE datname = 'kantam'") -eq '1'
        if (-not $dbExists) {
            Invoke-Checked $psql ($common + @('-c', "CREATE DATABASE kantam OWNER kantam ENCODING 'UTF8' TEMPLATE template0")) 'Creating database'
            Invoke-Checked $psql ($common + @('-c', 'REVOKE CONNECT ON DATABASE kantam FROM PUBLIC')) 'Restricting database access'
        }
        if ($pgInstalledNow) {
            # The ERP is the only client: keep PostgreSQL off the network.
            Invoke-Checked $psql ($common + @('-c', "ALTER SYSTEM SET listen_addresses = 'localhost'")) 'Restricting PostgreSQL to localhost'
            Get-Service -Name 'postgresql*' | Restart-Service
            Start-Sleep -Seconds 3
        }
    } finally { Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue }
    Write-Ok 'Database kantam and role kantam are ready.'

    # ------------------------------------------------------------------ application files
    Write-Step 'Stopping running services (if any)'
    foreach ($s in $KantamServices) { Stop-KantamService $s $DataDir }

    Write-Step 'Copying application files'
    $AppDir = Join-Path $InstallDir 'app'
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    if (-not $InstallDir.StartsWith($env:ProgramFiles, [StringComparison]::OrdinalIgnoreCase)) {
        # Program Files is already writable only by administrators; any other folder must be made so.
        Set-StrictAcl $InstallDir @("*$($KantamSids.LocalService):(OI)(CI)RX")
    }
    New-Item -ItemType Directory -Force -Path $AppDir, (Join-Path $InstallDir 'bin') | Out-Null
    foreach ($part in 'backend', 'frontend', 'gateway', 'docs') {
        & robocopy.exe (Join-Path $SourceRoot $part) (Join-Path $AppDir $part) /MIR /NFL /NDL /NJH /NJS /NP `
            /XD .venv node_modules dist keys protected_media __pycache__ tests test .git /XF *.sqlite3 *.pyc .env | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "Copying $part failed (robocopy exit code $LASTEXITCODE)." }
    }
    foreach ($f in 'Kantam.Common.ps1', 'Run-KantamService.ps1', 'KantamAdmin.ps1', 'Uninstall-Kantam.ps1') {
        Copy-Item -LiteralPath (Join-Path $PackageDir $f) -Destination (Join-Path $InstallDir 'bin') -Force
    }
    # Lets KantamAdmin.ps1 / Uninstall-Kantam.ps1 find the data folder when it is not the default.
    Set-Content -LiteralPath (Join-Path $InstallDir 'bin\datadir.txt') -Value $DataDir -Encoding ASCII
    Copy-Item -LiteralPath (Join-Path $SourceRoot 'README.md') -Destination $InstallDir -Force
    Write-Ok "Installed to $AppDir"

    Write-Step 'Installing Python packages'
    $venv = Join-Path $InstallDir 'venv'
    $venvPython = Join-Path $venv 'Scripts\python.exe'
    if (-not (Test-Path $venvPython)) { Invoke-Checked $python @('-m', 'venv', $venv) 'Creating the Python environment' }
    Invoke-Checked $venvPython @('-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '--upgrade', 'pip') 'Upgrading pip'
    Invoke-Checked $venvPython @('-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '-r', (Join-Path $AppDir 'backend\requirements.txt')) 'Installing Python packages'
    Invoke-Checked $venvPython @('-m', 'compileall', '-q', (Join-Path $AppDir 'backend')) 'Pre-compiling the backend'

    Write-Step 'Building the web interface and gateway'
    Push-Location (Join-Path $AppDir 'frontend')
    try {
        Invoke-Checked $npm @('ci', '--no-audit', '--no-fund', '--loglevel=error') 'Installing frontend packages'
        Invoke-Checked $npm @('run', 'build') 'Building the frontend'
        Remove-Item -Recurse -Force node_modules -ErrorAction SilentlyContinue   # build-time only
    } finally { Pop-Location }
    Push-Location (Join-Path $AppDir 'gateway')
    try { Invoke-Checked $npm @('ci', '--omit=dev', '--no-audit', '--no-fund', '--loglevel=error') 'Installing gateway packages' } finally { Pop-Location }

    # ------------------------------------------------------------------ TLS certificate
    Write-Step 'Preparing the HTTPS certificate'
    $pfxPath = $cfg['TLS_PFX_FILE']
    $pfxPass = ConvertTo-SecureString $cfg['TLS_PFX_PASSPHRASE'] -AsPlainText -Force
    if ($CertPfx) {
        $given = Read-Host -AsSecureString '    Password of the supplied .pfx certificate'
        $imported = Import-PfxCertificate -FilePath $CertPfx -CertStoreLocation Cert:\LocalMachine\My -Password $given -Exportable
        $exportArgs = @{ Cert = $imported; FilePath = $pfxPath; Password = $pfxPass }
        if ((Get-Command Export-PfxCertificate).Parameters.ContainsKey('CryptoAlgorithmOption')) { $exportArgs['CryptoAlgorithmOption'] = 'AES256_SHA256' }
        Export-PfxCertificate @exportArgs | Out-Null
        Remove-Item -LiteralPath "Cert:\LocalMachine\My\$($imported.Thumbprint)" -DeleteKey -ErrorAction SilentlyContinue
        $cfg['TLS_THUMBPRINT'] = $imported.Thumbprint
        Write-Ok 'Using the supplied certificate.'
    } elseif (-not (Test-Path -LiteralPath $pfxPath)) {
        $dns = @($HostName, 'localhost')
        $domain = (Get-CimInstance Win32_ComputerSystem).Domain
        if ($domain -and $domain -ne 'WORKGROUP' -and $HostName -notlike '*.*') { $dns += "$HostName.$domain" }
        $ips = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '169.254.*' } | ForEach-Object { $_.IPAddress })
        $san = (($dns | Select-Object -Unique | ForEach-Object { "DNS=$_" }) + ($ips | ForEach-Object { "IPAddress=$_" })) -join '&'
        $cert = New-SelfSignedCertificate -Subject "CN=$HostName" -FriendlyName 'Kantam ERP' -CertStoreLocation Cert:\LocalMachine\My `
            -KeyAlgorithm RSA -KeyLength 2048 -HashAlgorithm SHA256 -KeyExportPolicy Exportable -NotAfter (Get-Date).AddYears(3) `
            -TextExtension @('2.5.29.37={text}1.3.6.1.5.5.7.3.1', "2.5.29.17={text}$san")
        $exportArgs = @{ Cert = $cert; FilePath = $pfxPath; Password = $pfxPass }
        if ((Get-Command Export-PfxCertificate).Parameters.ContainsKey('CryptoAlgorithmOption')) { $exportArgs['CryptoAlgorithmOption'] = 'AES256_SHA256' }
        Export-PfxCertificate @exportArgs | Out-Null
        $cerPath = Join-Path $DataDir 'tls\kantam-erp.cer'
        Export-Certificate -Cert $cert -FilePath $cerPath -Type CERT | Out-Null
        # Trust it on this server; the private key no longer needs to sit in the certificate store.
        Import-Certificate -FilePath $cerPath -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
        Remove-Item -LiteralPath "Cert:\LocalMachine\My\$($cert.Thumbprint)" -DeleteKey -ErrorAction SilentlyContinue
        $cfg['TLS_THUMBPRINT'] = $cert.Thumbprint
        Write-Ok "Self-signed certificate created for: $($dns -join ', ') $($ips -join ', ')"
        Write-Note "Install $cerPath as a Trusted Root certificate on plant PCs/tablets (or via Group Policy) to avoid browser warnings."
    } else {
        Write-Ok 'Keeping the existing certificate.'
    }
    Write-KantamEnv $EnvFile $cfg

    # ------------------------------------------------------------------ permissions
    Write-Step 'Locking down file permissions'
    # Program Files already allows administrators to write and everyone else (including LOCAL SERVICE) only to read.
    Set-KantamDataAcl $DataDir
    Write-Ok 'Service account (LOCAL SERVICE) can read the program and config, and write only keys, media, logs and run state.'

    # ------------------------------------------------------------------ migrate + first run
    Write-Step 'Updating the database schema'
    Import-KantamEnv $EnvFile | Out-Null
    $env:PYTHONUTF8 = '1'
    $manage = Join-Path $AppDir 'backend\manage.py'
    Push-Location (Join-Path $AppDir 'backend')
    try {
        Invoke-Checked $venvPython @($manage, 'migrate', '--noinput') 'Database migration'
        # Decide from the database, not from files, so a re-run after a failed first attempt still creates the owner.
        $probe = Invoke-NativeCapture $venvPython @($manage, 'shell', '-c', 'from apps.core.models import User; print(User.objects.exists())')
        if ($probe.ExitCode -ne 0) { throw ("Could not query the database:`n" + ($probe.Output -join "`n")) }
        if (($probe.Output | Select-Object -Last 1) -ne 'True') {
            $FirstInstall = $true
            Write-Step 'Creating the owner account'
            Write-Host '    The owner signs in with this password and then sets up an authenticator app (MFA).'
            for ($attempt = 1; ; $attempt++) {
                $env:KANTAM_OWNER_PASSWORD = if ($Unattended -and $env:KANTAM_OWNER_PASSWORD) { $env:KANTAM_OWNER_PASSWORD } else { Read-NewPassword '    Owner password' }
                $run = Invoke-NativeCapture $venvPython @($manage, 'bootstrap', '--owner', 'owner', '--owner-name', 'Owner', '--password-env', 'KANTAM_OWNER_PASSWORD',
                    '--device-name', "$HostName (server console)")
                Remove-Item Env:\KANTAM_OWNER_PASSWORD -ErrorAction SilentlyContinue
                if ($run.ExitCode -eq 0) { $bootstrapOutput = ($run.Output -join "`n"); break }
                Write-Note (($run.Output | Select-Object -Last 3) -join ' ')
                if ($Unattended -or $attempt -ge 3) { throw 'Owner account could not be created.' }
            }
            if ($Demo) {
                Write-Step 'Loading demo data'
                $env:KANTAM_DEMO_PASSWORD = Read-NewPassword '    Shared password for the demo users'
                try { Invoke-Checked $venvPython @($manage, 'seed_demo', '--password-env', 'KANTAM_DEMO_PASSWORD') 'Loading demo data' }
                finally { Remove-Item Env:\KANTAM_DEMO_PASSWORD -ErrorAction SilentlyContinue }
            }
        } else {
            Invoke-Checked $venvPython @($manage, 'sync_rbac') 'Refreshing roles and approval policies'
        }
    } finally { Pop-Location }
    # Files created just now (signing keys) inherit the folder rules; re-apply once more to be certain.
    Set-KantamDataAcl $DataDir

    # ------------------------------------------------------------------ services
    Write-Step 'Registering auto-start services'
    $account = Get-AccountName $KantamSids.LocalService
    $principal = New-ScheduledTaskPrincipal -UserId $account -LogonType ServiceAccount -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    $runner = Join-Path $InstallDir 'bin\Run-KantamService.ps1'
    foreach ($s in $KantamServices) {
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
            -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runner`" -Service $s -DataDir `"$DataDir`""
        Register-ScheduledTask -TaskPath $KantamTaskPath -TaskName "Kantam ERP $s" -Action $action -Principal $principal -Settings $settings `
            -Trigger (New-ScheduledTaskTrigger -AtStartup) -Description "Kantam ERP $s (supervised, restarts automatically)" -Force | Out-Null
    }
    foreach ($s in $KantamServices) { Start-KantamService $s }
    Write-Ok "Backend, worker and gateway start at boot as $account."

    Write-Step 'Configuring the Windows firewall'
    $ruleName = 'Kantam ERP (HTTPS)'
    Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port `
        -RemoteAddress $AllowedCidrs -Profile Domain, Private -Description 'Kantam ERP gateway - approved plant networks only' | Out-Null
    Write-Ok "Port $port open to: $($AllowedCidrs -join ', ')"
    if (Get-NetConnectionProfile -ErrorAction SilentlyContinue | Where-Object { $_.NetworkCategory -eq 'Public' }) {
        Write-Note 'A network on this server is set to Public; the firewall rule does not apply there. Set the plant network to Private or Domain.'
    }

    Write-Step 'Checking that the ERP responds'
    if (Test-KantamHealth $port 120) { Write-Ok 'Gateway and backend are up.' }
    else { Write-Note "No response yet. Check the logs in $LogDir, or run: KantamAdmin.ps1 status" }

    # ------------------------------------------------------------------ summary
    $url = if ($port -eq 443) { "https://$HostName" } else { "https://${HostName}:$port" }
    Write-Host ''
    Write-Host '------------------------------------------------------------------' -ForegroundColor White
    Write-Host " Kantam ERP is installed:  $url" -ForegroundColor Green
    Write-Host '------------------------------------------------------------------' -ForegroundColor White
    if ($FirstInstall -and $bootstrapOutput) {
        $code = [regex]::Match($bootstrapOutput, 'shown once\): (\S+)').Groups[1].Value
        Write-Host " Owner username       : owner"
        Write-Host " Device enrolment code: $code   (valid 24 hours, for this server's browser)"
    }
    $admin = Join-Path $InstallDir 'bin\KantamAdmin.ps1'
    Write-Host " Admin tool              : $admin"
    Write-Host '   e.g. KantamAdmin.ps1 new-device "Stores tablet" | status | logs | backup | reset'
    Write-Host " Install log             : $transcript"
    Write-Host ''
} finally {
    Stop-Transcript | Out-Null
}
