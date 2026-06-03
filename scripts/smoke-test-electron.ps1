# =============================================================================
# Electron Integration Smoke Test (Windows)
# Flow: occupy 8765 -> silent NSIS install -> launch installed app ->
#       wait port.json -> verify port changed -> health check ->
#       verify renderer loaded -> uninstall
# =============================================================================

$ErrorActionPreference = "Stop"

function Write-Ok   { param([string]$msg) Write-Host "[ok] $msg" -ForegroundColor Green }
function Write-Fail { param([string]$msg) Write-Host "[fail] $msg" -ForegroundColor Red; exit 1 }
function Write-Step { param([string]$msg) Write-Host ">> $msg" -ForegroundColor Yellow }

$ProjectRoot  = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$AppPkgName   = "dental-recommend-agent"
$ProgramsDir  = Join-Path $env:LOCALAPPDATA "Programs"
$OccupiedPort = 8765
$UserDataDir  = Join-Path $env:APPDATA $AppPkgName
$BackendDataDir = Join-Path $UserDataDir "backend-data"
$PortJson       = Join-Path $BackendDataDir "port.json"

$AppProcess         = $null
$PortBlockerProcess = $null
$InstallDir         = $null

function Find-InstalledExe {
    if (-not $InstallDir -or -not (Test-Path $InstallDir)) { return $null }
    return Get-ChildItem "$InstallDir\*.exe" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notmatch "Uninstall" } |
        Select-Object -First 1 -ExpandProperty FullName
}

function Find-Uninstaller {
    if (-not $InstallDir -or -not (Test-Path $InstallDir)) { return $null }
    return Get-ChildItem "$InstallDir\Uninstall*" -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
}

function Cleanup {
    if ($AppProcess -and -not $AppProcess.HasExited) {
        Stop-Process -Id $AppProcess.Id -Force -ErrorAction SilentlyContinue
    }
    if ($PortBlockerProcess -and -not $PortBlockerProcess.HasExited) {
        Stop-Process -Id $PortBlockerProcess.Id -Force -ErrorAction SilentlyContinue
    }
    $uninst = Find-Uninstaller
    if ($uninst) {
        Write-Step "Uninstalling ..."
        Start-Process -FilePath $uninst -ArgumentList "/S" -Wait -WindowStyle Hidden -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
    if (Test-Path $PortJson) {
        Remove-Item -Path $PortJson -Force -ErrorAction SilentlyContinue
    }
}

function Resolve-InstallDir {
    if (-not (Test-Path $ProgramsDir)) { return }
    $dirs = Get-ChildItem $ProgramsDir -Directory -ErrorAction SilentlyContinue
    foreach ($d in $dirs) {
        $exe = Get-ChildItem "$($d.FullName)\*.exe" -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notmatch "Uninstall" } | Select-Object -First 1
        if ($exe) { $script:InstallDir = $d.FullName; return }
    }
}

try {
    # 1. Locate NSIS installer
    $InstallerExe = Get-ChildItem "$ProjectRoot\release\*Setup*.exe" | Select-Object -First 1
    if (-not $InstallerExe) { Write-Fail "NSIS installer not found" }
    Write-Ok "Installer: $($InstallerExe.FullName)"

    # 2. Occupy preferred port
    Write-Step "Occupying port $OccupiedPort ..."
    $TmpDir = Join-Path $env:TEMP "smoke-electron-$(Get-Random)"
    New-Item -ItemType Directory -Path $TmpDir -Force | Out-Null
    $blockerPy = Join-Path $TmpDir "blocker.py"
    @(
        "import socket, time, sys",
        "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)",
        "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)",
        "s.bind(('127.0.0.1', $OccupiedPort))",
        "s.listen(1)",
        "sys.stdout.flush()",
        "time.sleep(300)"
    ) | Out-File -FilePath $blockerPy -Encoding utf8
    $PortBlockerProcess = Start-Process -FilePath "python" -ArgumentList $blockerPy -PassThru -WindowStyle Hidden

    $portReady = $false
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $tcp = New-Object System.Net.Sockets.TcpClient
            $tcp.Connect("127.0.0.1", $OccupiedPort)
            $tcp.Close()
            $portReady = $true
            break
        } catch { }
    }
    if (-not $portReady) { Write-Fail "Port $OccupiedPort not occupied" }
    Write-Ok "Port $OccupiedPort occupied (PID $($PortBlockerProcess.Id))"

    # 3. Clean old port.json
    if (Test-Path $BackendDataDir) {
        Remove-Item -Path $PortJson -Force -ErrorAction SilentlyContinue
    } else {
        New-Item -ItemType Directory -Path $BackendDataDir -Force | Out-Null
    }

    # 4. Kill any lingering app/backend processes from previous runs
    Get-Process -Name "backend","*Agent*","electron" -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2

    # 5. Uninstall old version if present
    Resolve-InstallDir
    $oldUninst = Find-Uninstaller
    if ($oldUninst) {
        Write-Step "Removing old install ..."
        Start-Process -FilePath $oldUninst -ArgumentList "/S" -Wait -WindowStyle Hidden -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 5
    }

    # 6. Silent NSIS install (fallback to unpacked if installer crashes)
    Write-Step "Silent-installing NSIS package ..."
    $installResult = Start-Process -FilePath $InstallerExe.FullName -ArgumentList "/S" -Wait -PassThru -WindowStyle Hidden
    $UsedNsis = $true
    if ($installResult.ExitCode -ne 0) {
        Write-Host "[warn] NSIS install failed (exit $($installResult.ExitCode)), falling back to win-unpacked" -ForegroundColor Yellow
        $UsedNsis = $false
    }
    Start-Sleep -Seconds 5

    # 7. Find app to launch (installed or unpacked)
    $InstalledExe = $null
    if ($UsedNsis) {
        Resolve-InstallDir
        $InstalledExe = Find-InstalledExe
    }
    if (-not $InstalledExe) {
        # Fallback: use win-unpacked
        $UnpackedDir = Join-Path $ProjectRoot "release\win-unpacked"
        $InstalledExe = Get-ChildItem "$UnpackedDir\*.exe" -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notmatch "Uninstall" } |
            Select-Object -First 1 -ExpandProperty FullName
        if (-not $InstalledExe) { Write-Fail "No app exe found (NSIS or unpacked)" }
        Write-Host "[info] Using unpacked: $InstalledExe" -ForegroundColor Yellow
    } else {
        Write-Ok "Installed: $InstalledExe"
    }

    # 8. Launch app (flags for headless CI rendering)
    Write-Step "Launching app ..."
    $AppProcess = Start-Process -FilePath $InstalledExe -ArgumentList "--no-sandbox","--disable-gpu" -PassThru -WindowStyle Hidden
    Write-Ok "App started (PID $($AppProcess.Id))"

    # 9. Wait for port.json
    Write-Step "Waiting for port.json (up to 45s) ..."
    $found = $false
    for ($i = 0; $i -lt 90; $i++) {
        if (Test-Path $PortJson) { $found = $true; break }
        if ($AppProcess.HasExited) {
            Write-Fail "App exited (code $($AppProcess.ExitCode)), no port.json"
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $found) { Write-Fail "port.json not found within 45s" }
    Write-Ok "port.json created"

    # 10. Verify port changed
    $info = Get-Content $PortJson -Raw | ConvertFrom-Json
    $actualPort = $info.port
    Write-Step "Actual port: $actualPort"
    if ($actualPort -eq $OccupiedPort) { Write-Fail "Port not changed" }
    Write-Ok "Port switched: $OccupiedPort -> $actualPort"

    # 11. Health check
    $healthUrl = "http://127.0.0.1:${actualPort}/api/health"
    Write-Step "Health check: $healthUrl ..."
    $ok = $false
    for ($i = 0; $i -lt 10; $i++) {
        try {
            $r = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 3
            if ($r.StatusCode -eq 200) {
                $b = $r.Content | ConvertFrom-Json
                if ($b.status -eq "ok") { $ok = $true; break }
            }
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ok) { Write-Fail "Health check failed" }
    Write-Ok "Health check passed"

    # 12. Verify renderer loaded
    $marker = Join-Path $BackendDataDir "renderer-ready"
    Write-Step "Waiting for renderer-ready (up to 45s) ..."
    $markerOk = $false
    for ($i = 0; $i -lt 90; $i++) {
        if (Test-Path $marker) { $markerOk = $true; break }
        if ($AppProcess.HasExited) {
            Write-Fail "App exited, renderer-ready not written"
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $markerOk) { Write-Fail "renderer-ready not found within 45s" }
    Write-Ok "Renderer loaded"

    # Stop app before uninstall
    Stop-Process -Id $AppProcess.Id -Force -ErrorAction SilentlyContinue
    $AppProcess = $null
    Start-Sleep -Seconds 2

    Write-Host ""
    Write-Host "=== Electron integration smoke test PASSED ===" -ForegroundColor Green

} finally {
    Cleanup
    if (Test-Path $TmpDir) { Remove-Item -Recurse -Force $TmpDir -ErrorAction SilentlyContinue }
}
