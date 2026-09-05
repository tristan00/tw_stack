$ErrorActionPreference = 'Stop'

$PgBin   = 'C:\Program Files\PostgreSQL\17\bin'
$Root    = 'D:\pg17'
$DataDir = 'D:\pg17\data'
$Service = 'postgresql-x64-17-d'
$Port    = 55433
$Locale  = 'English_United States.1252'
$ConfSrc = Join-Path $PSScriptRoot 'tw.conf'
$PgPass  = Join-Path $env:APPDATA 'postgresql\pgpass.conf'
$NetworkServiceSid = '*S-1-5-20'

function Write-Step($msg) {
    Write-Host ("{0}  {1}" -f (Get-Date -Format 'HH:mm:ss.fff'), $msg)
}

function Invoke-Timed($name, [scriptblock]$body) {
    Write-Step "$name enter"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    & $body
    Write-Step ("{0} exit {1} ms" -f $name, $sw.ElapsedMilliseconds)
}

function Invoke-Native($exe, [string[]]$arguments) {
    $ErrorActionPreference = 'Continue'
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw ("{0} exited {1}" -f (Split-Path $exe -Leaf), $LASTEXITCODE) }
}

function Invoke-Psql([string[]]$arguments, $stdin) {
    $ErrorActionPreference = 'Continue'
    $psql = Join-Path $PgBin 'psql.exe'
    if ($null -eq $stdin) { $out = & $psql -X @arguments } else { $out = $stdin | & $psql -X @arguments }
    if ($LASTEXITCODE -ne 0) { throw ("psql exited {0}" -f $LASTEXITCODE) }
    $out
}

function Get-TwPassword {
    if (-not (Test-Path $PgPass)) { throw "pgpass not found: $PgPass" }
    $line = @(Get-Content $PgPass | Where-Object { $_ -match '^127\.0\.0\.1:55432:[^:]*:tw:' })[0]
    if (-not $line) { throw "no 127.0.0.1:55432 tw entry in $PgPass" }
    $raw = ($line -split ':', 5)[4]
    if ($raw.Contains([char]92)) { throw 'pgpass password is escaped; unescape it before reuse' }
    $raw
}

function Assert-Preconditions {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'not elevated: start this from an administrator PowerShell'
    }
    foreach ($exe in @('initdb.exe', 'pg_ctl.exe', 'psql.exe', 'pg_isready.exe')) {
        if (-not (Test-Path (Join-Path $PgBin $exe))) { throw "missing $exe in $PgBin" }
    }
    if (-not (Test-Path $ConfSrc)) { throw "missing $ConfSrc" }
    $null = Get-TwPassword
    $free = (Get-PSDrive D).Free
    if ($free -lt 100GB) { throw ("D: free {0:N1} GB is under the 100 GB gate" -f ($free / 1GB)) }
    Write-Step ("preconditions ok: elevated, pg 17 bin present, pgpass tw entry present, D: free {0:N1} GB" -f ($free / 1GB))
}

function Step-Initdb {
    if (Test-Path (Join-Path $DataDir 'PG_VERSION')) {
        Write-Step 'initdb skipped: cluster already present'
        return
    }
    New-Item -ItemType Directory -Force -Path $Root | Out-Null
    Invoke-Native (Join-Path $PgBin 'initdb.exe') @('-D', $DataDir, '-U', 'postgres', '--data-checksums', '-E', 'UTF8', "--locale=$Locale")
}

function Step-Conf {
    Copy-Item $ConfSrc (Join-Path $DataDir 'tw.conf') -Force
    $main = Join-Path $DataDir 'postgresql.conf'
    if (-not (Select-String -Path $main -Pattern "^include 'tw\.conf'" -Quiet)) {
        Add-Content -Path $main -Value "include 'tw.conf'" -Encoding ascii
    }
    Write-Step 'conf applied: tw.conf in the data directory, include line present'
}

function Step-Acl {
    Invoke-Native 'icacls.exe' @($Root, '/grant', "$($NetworkServiceSid):(OI)(CI)F", '/T', '/C')
}

function Step-Register {
    if (Get-Service -Name $Service -ErrorAction SilentlyContinue) {
        Write-Step "register skipped: service $Service already exists"
        return
    }
    Invoke-Native (Join-Path $PgBin 'pg_ctl.exe') @('register', '-N', $Service, '-D', $DataDir, '-S', 'auto')
}

function Step-Start {
    $ErrorActionPreference = 'Continue'
    $svc = Get-Service -Name $Service
    if ($svc.Status -ne 'Running') { Start-Service -Name $Service }
    $ready = Join-Path $PgBin 'pg_isready.exe'
    for ($i = 0; $i -lt 60; $i++) {
        & $ready -h 127.0.0.1 -p $Port -q
        if ($LASTEXITCODE -eq 0) { return }
        Start-Sleep -Milliseconds 500
    }
    throw "server on port $Port not ready after 30 s"
}

function Step-Role {
    $exists = Invoke-Psql @('-p', "$Port", '-U', 'postgres', '-d', 'postgres', '-Atc', "select 1 from pg_roles where rolname = 'tw'") $null
    if ($exists -eq '1') {
        Write-Step 'role skipped: tw already exists'
        return
    }
    $pw = (Get-TwPassword).Replace("'", "''")
    $null = Invoke-Psql @('-q', '-p', "$Port", '-U', 'postgres', '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-f', '-') "CREATE ROLE tw LOGIN PASSWORD '$pw' CREATEDB;"
    Write-Step 'role tw created with LOGIN CREATEDB'
}

function Step-Verify {
    $checks = @(
        @('select 1', '1'),
        @('show effective_io_concurrency', '0'),
        @('show port', "$Port"),
        @('show wal_compression', 'zstd'),
        @('show data_checksums', 'on'),
        @('show shared_buffers', '8GB'),
        @('show max_connections', '50'),
        @('select datcollate from pg_database where datname = current_database()', $Locale)
    )
    foreach ($c in $checks) {
        $got = Invoke-Psql @('-p', "$Port", '-U', 'tw', '-d', 'postgres', '-Atc', $c[0]) $null
        if ($got -ne $c[1]) { throw "check failed: $($c[0]) = '$got', expected '$($c[1])'" }
        Write-Step ("check ok: {0} = {1}" -f $c[0], $got)
    }
}

$total = [Diagnostics.Stopwatch]::StartNew()
Write-Step '0.3 create D: cluster enter'
Assert-Preconditions
Invoke-Timed 'initdb'   { Step-Initdb }
Invoke-Timed 'conf'     { Step-Conf }
Invoke-Timed 'acl'      { Step-Acl }
Invoke-Timed 'register' { Step-Register }
Invoke-Timed 'start'    { Step-Start }
Invoke-Timed 'role'     { Step-Role }
Invoke-Timed 'verify'   { Step-Verify }
Write-Step ('0.3 create D: cluster exit {0} ms -- DONE' -f $total.ElapsedMilliseconds)
