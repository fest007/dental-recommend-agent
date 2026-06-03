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

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$AppPkgName  = "dental-recommend-agent"
$OccupiedPort = 8765

# Find the installed app dir dynamically (productName has Chinese chars)
$ProgramsDir = Join-Path $env:LOCALAPPDATA "Programs"
$InstallDir  = Get-ChildItem $ProgramsDir -Directory | Where-Object { $_.Name -match "Agent" } | Select-Object -First 1 -ExpandProperty FullName
if (-not $InstallDir) { $InstallDir = Join-Path $ProgramsDir "dental-recommend-agent" }

# Locate NSIS installer
$InstallerExe = Get-ChildItem "$ProjectRoot\release\*Setup*.exe" | Select-Object -First 1
if (-not $InstallerExe) {
    Write-Fail "NSIS installer not found (run electron-builder --win first)"
}
Write-Ok "Installer: $($InstallerExe.FullName)"

# Paths - use wildcards to avoid Chinese char encoding issues
$InstalledExe   = Get-ChildItem "$InstallDir\*.exe" -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch "Uninstall" } | Select-Object -First 1 -ExpandProperty FullName
$UninstallExe   = Get-ChildItem "$InstallDir\Uninstall*" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
$UserDataDir    = Join-Path $env:APPDATA $AppPkgName
$BackendDataDir = Join-Path $UserDataDir "backend-data"
$PortJson       = Join-Path $BackendDataDir "port.json"

$AppProcess         = $null
$PortBlockerProcess = $null

function Cleanup {
    if ($AppProcess -and -not $AppProcess.HasExited) {
        Stop-Process -Id $AppProcess.Id -Force -ErrorAction SilentlyContinue
    }
    if ($PortBlockerProcess -and -not $PortBlockerProcess.HasExited) {
        Stop-Process -Id $PortBlockerProcess.Id -Force -ErrorAction SilentlyContinue
    }
    # Find and run uninstaller
    $uninst = Get-ChildItem "$InstallDir\Uninstall*" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
    if ($uninst) {
        Write-Step "Uninstalling ..."
        Start-Process -FilePath $uninst -ArgumentList "/S" -Wait -WindowStyle Hidden -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
    if (Test-Path $PortJson) {
        Remove-Item -Path $PortJson -Force -ErrorAction SilentlyContinue
    }
}

try {
    # 1. Occupy preferred port
    Write-Step "Occupying port $OccupiedPort ..."
    $TmpDir = Join-Path $env:TEMP "smoke-electron-$(Get-Random)"
    New-Item -ItemType Directory -Path $TmpDir -Force | Out-Null
    $blockerPy = Join-Path $TmpDir "blocker.py"
    $pyLines = @(
        "import socket, time, sys",
        "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)",
        "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)",
        "s.bind(('127.0.0.1', $OccupiedPort))",
        "s.listen(1)",
        "sys.stdout.flush()",
        "time.sleep(300)"
    )
    $pyLines | Out-File -FilePath $blockerPy -Encoding utf8
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

    # 2. Clean old port.json
    if (Test-Path $BackendDataDir) {
        Remove-Item -Path $PortJson -Force -ErrorAction SilentlyContinue
    } else {
        New-Item -ItemType Directory -Path $BackendDataDir -Force | Out-Null
    }

    # 3. Silent NSIS install
    Write-Step "Silent-installing NSIS package ..."
    # Uninstall old version if present
    $oldUninst = Get-ChildItem "$InstallDir\Uninstall*" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
    if ($oldUninst) {
        Start-Process -FilePath $oldUninst -ArgumentList "/S" -Wait -WindowStyle Hidden -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
    $installResult = Start-Process -FilePath $InstallerExe.FullName -ArgumentList "/S" -Wait -PassThru -WindowStyle Hidden
    if ($installResult.ExitCode -ne 0) {
        Write-Fail "NSIS install failed (exit $($installResult.ExitCode))"
    }
    Start-Sleep -Seconds 3
    # Refresh installed exe path
    $InstalledExe = Get-ChildItem "$InstallDir\*.exe" -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch "Uninstall" } | Select-Object -First 1 -ExpandProperty FullName
    if (-not $InstalledExe -or -not (Test-Path $InstalledExe)) {
        Write-Fail "Installed exe not found in $InstallDir"
    }
    Write-Ok "Installed: $InstalledExe"

    # 4. Launch installed app
    Write-Step "Launching installed app ..."
    $AppProcess = Start-Process -FilePath $InstalledExe -PassThru -WindowStyle Hidden
    Write-Ok "App started (PID $($AppProcess.Id))"

    # 5. Wait for port.json
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

    # 6. Verify port changed
    $PortInfo   = Get-Content $PortJson -Raw | ConvertFrom-Json
    $ActualPort = $PortInfo.port
    Write-Step "Actual port: $ActualPort"
    if ($ActualPort -eq $OccupiedPort) {
        Write-Fail "Port not changed, still $OccupiedPort"
    }
    Write-Ok "Port switched: $OccupiedPort -> $ActualPort"

    # 7. Health check
    $HealthUrl = "http://127.0.0.1:${ActualPort}/api/health"
    Write-Step "Health check: $HealthUrl ..."
    $passed = $false
    for ($i = 0; $i -lt 10; $i++) {
        try {
            $response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -eq 200) {
                $body = $response.Content | ConvertFrom-Json
                if ($body.status -eq "ok") { $passed = $true; break }
            }
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $passed) { Write-Fail "Health check failed" }
    Write-Ok "Health check passed: $($response.Content)"

    # 8. Verify renderer loaded
    $RendererMarker = Join-Path $BackendDataDir "renderer-ready"
    Write-Step "Waiting for renderer-ready marker (up to 30s) ..."
    $markerFound = $false
    for ($i = 0; $i -lt 60; $i++) {
        if (Test-Path $RendererMarker) { $markerFound = $true; break }
        if ($AppProcess.HasExited) {
            Write-Fail "App exited, renderer-ready not written"
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $markerFound) { Write-Fail "renderer-ready not found within 30s" }
    Write-Ok "Renderer loaded (marker: $(Get-Content $RendererMarker -Raw))"

    # Stop app before uninstall
    Stop-Process -Id $AppProcess.Id -Force -ErrorAction SilentlyContinue
    $AppProcess = $null
    Start-Sleep -Seconds 2

    Write-Host ""
    Write-Host "=== Electron integration smoke test PASSED ===" -ForegroundColor Green
    Write-Host "=== (NSIS install + port collision + health + renderer) ===" -ForegroundColor Green

} finally {
    Cleanup
    if (Test-Path $TmpDir) { Remove-Item -Recurse -Force $TmpDir -ErrorAction SilentlyContinue }
}
