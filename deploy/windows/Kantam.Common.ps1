# Shared helpers for the Kantam ERP Windows scripts. Dot-source this file.
# Keep this file ASCII-only: Windows PowerShell 5.1 reads BOM-less files as ANSI.

$script:KantamSids = @{
    Administrators = 'S-1-5-32-544'
    System         = 'S-1-5-18'
    LocalService   = 'S-1-5-19'
}
$script:KantamServices = @('backend', 'worker', 'gateway')
$script:KantamTaskPath = '\Kantam ERP\'   # used only to clean up the scheduled tasks of older versions
# WinSW (MIT licence) turns each process into a real Windows service. Pinned and hash-checked.
$script:WinSwUrl = 'https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW.NET461.exe'
$script:WinSwSha256 = 'B5066B7BBDFBA1293E5D15CDA3CAAEA88FBEAB35BD5B38C41C913D492AADFC4F'

function Write-Step([string]$Message) { Write-Host ''; Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message) { Write-Host "    $Message" -ForegroundColor Green }
function Write-Note([string]$Message) { Write-Host "    $Message" -ForegroundColor Yellow }

function Assert-Administrator {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this script from an elevated PowerShell (Run as administrator).'
    }
}

function Get-AccountName([string]$Sid) {
    (New-Object Security.Principal.SecurityIdentifier($Sid)).Translate([Security.Principal.NTAccount]).Value
}

function Get-KantamDataDir {
    if ($env:KANTAM_DATA_DIR) { return $env:KANTAM_DATA_DIR }
    # The installer records a non-default data folder next to the installed scripts.
    $pointer = Join-Path $PSScriptRoot 'datadir.txt'
    if (Test-Path -LiteralPath $pointer) {
        $d = (Get-Content -LiteralPath $pointer | Select-Object -First 1).Trim()
        if ($d) { return $d }
    }
    return (Join-Path $env:ProgramData 'Kantam ERP')
}

function Get-KantamEnvFile([string]$DataDir) { Join-Path $DataDir 'config\kantam.env' }

function Read-KantamEnv([string]$Path) {
    $values = [ordered]@{}
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in Get-Content -LiteralPath $Path) {
            if ($line -match '^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$') { $values[$matches[1]] = $matches[2] }
        }
    }
    return $values
}

function Write-KantamEnv([string]$Path, $Values) {
    $lines = @('# Kantam ERP runtime configuration. Contains secrets - readable by administrators and the service account only.')
    foreach ($k in $Values.Keys) { $lines += "$k=$($Values[$k])" }
    # ASCII without BOM so Python, Node and PowerShell all read it identically.
    [IO.File]::WriteAllLines($Path, [string[]]$lines, (New-Object Text.ASCIIEncoding))
}

function Import-KantamEnv([string]$Path) {
    $values = Read-KantamEnv $Path
    if ($values.Count -eq 0) { throw "Configuration not found: $Path. Run Install-Kantam.ps1 first." }
    foreach ($k in $values.Keys) { [Environment]::SetEnvironmentVariable($k, $values[$k], 'Process') }
    return $values
}

function New-KantamSecret([int]$Bytes = 32) {
    $buf = New-Object byte[] $Bytes
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($buf) } finally { $rng.Dispose() }
    return (($buf | ForEach-Object { $_.ToString('x2') }) -join '')
}

function ConvertFrom-SecureStringPlain([Security.SecureString]$Secure) {
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Read-NewPassword([string]$Prompt, [int]$MinLength = 12) {
    while ($true) {
        $a = Read-Host -AsSecureString "$Prompt (min $MinLength characters)"
        $b = Read-Host -AsSecureString 'Repeat'
        $pa = ConvertFrom-SecureStringPlain $a
        $pb = ConvertFrom-SecureStringPlain $b
        if ($pa -ne $pb) { Write-Note 'The two entries do not match. Try again.'; continue }
        if ($pa.Length -lt $MinLength) { Write-Note "Use at least $MinLength characters."; continue }
        return $pa
    }
}

function Invoke-Checked {
    # Runs a native command, streams its output, and throws on a non-zero exit code.
    param([string]$FilePath, [string[]]$Arguments, [string]$What)
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)." }
}

function Invoke-NativeCapture {
    # Runs a native command and returns stdout+stderr as text lines. Windows PowerShell 5.1 turns
    # redirected stderr into terminating errors under ErrorActionPreference=Stop, so relax it here.
    param([string]$FilePath, [string[]]$Arguments)
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $lines = & $FilePath @Arguments 2>&1 | ForEach-Object { "$_" } }
    finally { $ErrorActionPreference = $old }
    return @{ ExitCode = $LASTEXITCODE; Output = @($lines) }
}

function Set-StrictAcl {
    # Protects ONE folder: Administrators + SYSTEM full control plus the extra grants, nothing inherited
    # from above. Everything inside is then reset to simply inherit from that folder, so no file can ever
    # end up with an empty access list (which locks out even administrators).
    param([string]$Path, [string[]]$ExtraGrants = @(), [switch]$NoRecurse)
    $grants = @("*$($KantamSids.Administrators):(OI)(CI)F", "*$($KantamSids.System):(OI)(CI)F") + $ExtraGrants
    # Grant first, then cut inheritance: the folder is never left without an administrator entry.
    $r = Invoke-NativeCapture 'icacls.exe' (@($Path, '/grant:r') + $grants + @('/C', '/Q'))
    if ($r.ExitCode -ne 0) { throw "Could not set permissions on ${Path}: $($r.Output -join ' ')" }
    $r = Invoke-NativeCapture 'icacls.exe' @($Path, '/inheritance:r', '/C', '/Q')
    if ($r.ExitCode -ne 0) { throw "Could not set permissions on ${Path}: $($r.Output -join ' ')" }
    if (-not ($ExtraGrants -match [regex]::Escape($KantamSids.LocalService))) {
        Invoke-NativeCapture 'icacls.exe' @($Path, '/remove:g', "*$($KantamSids.LocalService)", '/C', '/Q') | Out-Null
    }
    if (-not $NoRecurse -and (Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) {
        $r = Invoke-NativeCapture 'icacls.exe' @((Join-Path $Path '*'), '/reset', '/T', '/C', '/Q')
        if ($r.ExitCode -ne 0) { throw "Could not reset permissions inside ${Path}: $($r.Output -join ' ')" }
    }
}

function Set-KantamDataAcl([string]$DataDir) {
    # LOCAL SERVICE (the service account) may read config and certificates, and write keys, media,
    # logs and run state. backups and admin are for administrators only.
    $ls = $KantamSids.LocalService
    foreach ($d in 'config', 'keys', 'media', 'logs', 'run', 'tls', 'backups', 'admin') {
        New-Item -ItemType Directory -Force -Path (Join-Path $DataDir $d) | Out-Null
    }
    Set-StrictAcl $DataDir @("*${ls}:(RX)") -NoRecurse
    foreach ($d in 'keys', 'media', 'logs', 'run') { Set-StrictAcl (Join-Path $DataDir $d) @("*${ls}:(OI)(CI)M") }
    foreach ($d in 'config', 'tls') { Set-StrictAcl (Join-Path $DataDir $d) @("*${ls}:(OI)(CI)RX") }
    foreach ($d in 'backups', 'admin') { Set-StrictAcl (Join-Path $DataDir $d) @() }
}

function Test-KantamDataAccess([string]$DataDir) {
    # True when an administrator can list every folder and open every file. A file that is merely
    # in use (e.g. a log held by a running service) is not a permission problem.
    try { $items = @(Get-ChildItem -LiteralPath $DataDir -Recurse -Force -ErrorAction Stop) } catch { return $false }
    foreach ($f in ($items | Where-Object { -not $_.PSIsContainer })) {
        try { [IO.File]::Open($f.FullName, 'Open', 'Read', 'ReadWrite').Dispose() }
        catch {
            $e = $_.Exception
            while ($e.InnerException) { $e = $e.InnerException }
            # Windows sharing/lock violations (HRESULT 0x80070020 / 0x80070021) mean "in use", which is fine.
            $inUse = ($e -is [IO.IOException]) -and (($e.HResult -band 0xFFFF) -in @(32, 33))
            if (-not $inUse) { return $false }
        }
    }
    return $true
}

function Repair-KantamDataAccess([string]$DataDir) {
    # Earlier installer versions could leave files in the data folder with an empty access list.
    # Give ownership and full control back to Administrators only; Set-KantamDataAcl then tidies up.
    if (-not (Test-Path -LiteralPath $DataDir) -or (Test-KantamDataAccess $DataDir)) { return }
    Write-Note 'Repairing permissions on the data folder (left by an earlier installer version)...'
    Invoke-NativeCapture 'takeown.exe' @('/F', $DataDir, '/R', '/A', '/D', 'Y') | Out-Null
    Invoke-NativeCapture 'icacls.exe' @($DataDir, '/grant', "*$($KantamSids.Administrators):F", "*$($KantamSids.System):F", '/T', '/C', '/Q') | Out-Null
    Invoke-NativeCapture 'icacls.exe' @($DataDir, '/grant', "*$($KantamSids.Administrators):(OI)(CI)F", '/C', '/Q') | Out-Null
    if (Test-KantamDataAccess $DataDir) { Write-Ok 'Data folder permissions repaired.' }
    else { Write-Note "Some files in $DataDir are still not readable (possibly in use). Continuing." }
}

function Get-KantamServiceName([string]$Service) { "KantamERP-$Service" }

function Get-KantamService([string]$Service) {
    Get-Service -Name (Get-KantamServiceName $Service) -ErrorAction SilentlyContinue
}

function Remove-LegacyKantamTask([string]$Service, [string]$DataDir) {
    # Versions before the Windows-service switch used scheduled tasks plus a PowerShell runner.
    $task = Get-ScheduledTask -TaskPath $KantamTaskPath -TaskName "Kantam ERP $Service" -ErrorAction SilentlyContinue
    if ($task) {
        Stop-ScheduledTask -InputObject $task -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -InputObject $task -Confirm:$false -ErrorAction SilentlyContinue
    }
    $pidFile = Join-Path $DataDir "run\$Service.pid"
    if (Test-Path -LiteralPath $pidFile) {
        $procId = (Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($procId -match '^\d+$' -and (Get-Process -Id ([int]$procId) -ErrorAction SilentlyContinue)) {
            Invoke-NativeCapture 'taskkill.exe' @('/PID', $procId, '/T', '/F') | Out-Null
        }
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }
}

function Stop-KantamService([string]$Service, [string]$DataDir) {
    $svc = Get-KantamService $Service
    if ($svc -and $svc.Status -ne 'Stopped') {
        Stop-Service -Name $svc.Name -Force -ErrorAction SilentlyContinue
        try { $svc.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30)) } catch { Write-Note "$($svc.Name) did not stop within 30 seconds." }
    }
    Remove-LegacyKantamTask $Service $DataDir
}

function Start-KantamService([string]$Service) {
    $svc = Get-KantamService $Service
    if (-not $svc) { throw "Windows service '$(Get-KantamServiceName $Service)' not found. Re-run Install-Kantam.ps1." }
    if ($svc.Status -ne 'Running') { Start-Service -Name $svc.Name }
}

function Show-KantamServiceProblems([string]$DataDir) {
    # Printed when the ERP does not come up, so the cause is visible without hunting for logs.
    foreach ($s in $KantamServices) {
        $svc = Get-KantamService $s
        Write-Host ("    {0,-22} {1}" -f (Get-KantamServiceName $s), $(if ($svc) { $svc.Status } else { 'NOT INSTALLED' })) -ForegroundColor Yellow
    }
    foreach ($s in $KantamServices) {
        foreach ($kind in 'err', 'wrapper') {
            $f = Join-Path $DataDir "logs\$(Get-KantamServiceName $s).$kind.log"
            if ((Test-Path -LiteralPath $f) -and (Get-Item -LiteralPath $f).Length -gt 0) {
                Write-Host "    --- $(Split-Path -Leaf $f) (last lines)" -ForegroundColor Yellow
                Get-Content -LiteralPath $f -Tail 12 | ForEach-Object { Write-Host "    $_" }
            }
        }
    }
}

function Test-KantamHealth([int]$Port, [int]$TimeoutSeconds = 90) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        # The gateway answers /healthz on its own; keyLoaded=true proves it has also reached the backend.
        $r = Invoke-NativeCapture 'curl.exe' @('-k', '-s', '--max-time', '5', "https://localhost:$Port/healthz")
        if ($r.ExitCode -eq 0 -and (($r.Output -join '') -match '"keyLoaded"\s*:\s*true')) { return $true }
        Start-Sleep -Seconds 3
    }
    return $false
}

# ---- Backup encryption (AES-256-CBC + HMAC-SHA256, PBKDF2-SHA256 key derivation) ----
$script:KbakMagic = [Text.Encoding]::ASCII.GetBytes('KBAK1')
$script:KbakIterations = 600000

function Get-KbakKeys([string]$Passphrase, [byte[]]$Salt) {
    $kdf = [Security.Cryptography.Rfc2898DeriveBytes]::new($Passphrase, $Salt, $KbakIterations, [Security.Cryptography.HashAlgorithmName]::SHA256)
    try { return @{ Enc = $kdf.GetBytes(32); Mac = $kdf.GetBytes(32) } } finally { $kdf.Dispose() }
}

function Protect-KantamFile([string]$InFile, [string]$OutFile, [string]$Passphrase) {
    $salt = New-Object byte[] 16
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create(); $rng.GetBytes($salt); $rng.Dispose()
    $keys = Get-KbakKeys $Passphrase $salt
    $aes = [Security.Cryptography.Aes]::Create()
    $aes.KeySize = 256; $aes.Mode = 'CBC'; $aes.Padding = 'PKCS7'; $aes.Key = $keys.Enc; $aes.GenerateIV()
    $out = [IO.File]::Open($OutFile, 'Create', 'ReadWrite', 'None')
    try {
        $out.Write($KbakMagic, 0, $KbakMagic.Length); $out.Write($salt, 0, 16); $out.Write($aes.IV, 0, 16)
        $crypto = [Security.Cryptography.CryptoStream]::new($out, $aes.CreateEncryptor(), [Security.Cryptography.CryptoStreamMode]::Write)
        $in = [IO.File]::OpenRead($InFile)
        try { $in.CopyTo($crypto); $crypto.FlushFinalBlock() } finally { $in.Dispose() }
        # Encrypt-then-MAC over header + ciphertext.
        $out.Position = 0
        $hmac = [Security.Cryptography.HMACSHA256]::new([byte[]]$keys.Mac)
        $tag = $hmac.ComputeHash($out)
        $out.Position = $out.Length
        $out.Write($tag, 0, $tag.Length)
    } finally { $out.Dispose(); $aes.Dispose() }
}

function Unprotect-KantamFile([string]$InFile, [string]$OutFile, [string]$Passphrase) {
    $in = [IO.File]::OpenRead($InFile)
    try {
        $header = New-Object byte[] 37
        if ($in.Length -lt 85 -or $in.Read($header, 0, 37) -ne 37 -or [Text.Encoding]::ASCII.GetString($header, 0, 5) -ne 'KBAK1') {
            throw 'Not a Kantam backup file.'
        }
        $salt = [byte[]]$header[5..20]; $iv = [byte[]]$header[21..36]
        $keys = Get-KbakKeys $Passphrase $salt
        $bodyLength = $in.Length - 32
        $buf = New-Object byte[] 1048576

        # 1. Verify the MAC before decrypting anything.
        $hmac = [Security.Cryptography.HMACSHA256]::new([byte[]]$keys.Mac)
        $in.Position = 0
        $remaining = $bodyLength
        while ($remaining -gt 0) {
            $n = $in.Read($buf, 0, [int][Math]::Min($buf.Length, $remaining))
            [void]$hmac.TransformBlock($buf, 0, $n, $null, 0); $remaining -= $n
        }
        [void]$hmac.TransformFinalBlock((New-Object byte[] 0), 0, 0)
        $tag = New-Object byte[] 32
        [void]$in.Read($tag, 0, 32)
        $diff = 0; for ($i = 0; $i -lt 32; $i++) { $diff = $diff -bor ($tag[$i] -bxor $hmac.Hash[$i]) }
        if ($diff -ne 0) { throw 'Wrong passphrase or the backup file is damaged.' }

        # 2. Stream-decrypt the ciphertext (between the header and the MAC).
        $aes = [Security.Cryptography.Aes]::Create()
        $aes.KeySize = 256; $aes.Mode = 'CBC'; $aes.Padding = 'PKCS7'; $aes.Key = $keys.Enc; $aes.IV = $iv
        $dec = $aes.CreateDecryptor()
        $out = [IO.File]::Create($OutFile)
        try {
            $in.Position = 37
            $remaining = $bodyLength - 37
            $outBuf = New-Object byte[] ($buf.Length + 16)
            while ($remaining -gt 0) {
                $n = $in.Read($buf, 0, [int][Math]::Min($buf.Length, $remaining)); $remaining -= $n
                if ($remaining -gt 0) {
                    $m = $dec.TransformBlock($buf, 0, $n, $outBuf, 0); $out.Write($outBuf, 0, $m)
                } else {
                    $last = $dec.TransformFinalBlock($buf, 0, $n); $out.Write($last, 0, $last.Length)
                }
            }
        } finally { $out.Dispose(); $dec.Dispose(); $aes.Dispose() }
    } finally { $in.Dispose() }
}
