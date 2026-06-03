# =============================================================================
# Backend Port Collision Smoke Test (Windows)
# =============================================================================

param(
    [int]$OccupiedPort = 8765
)

$ErrorActionPreference = "Stop"

function Write-Ok   { param([string]$msg) Write-Host "[ok] $msg" -ForegroundColor Green }
function Write-Fail { param([string]$msg) Write-Host "[fail] $msg" -ForegroundColor Red; exit 1 }
function Write-Step { param([string]$msg) Write-Host ">> $msg" -ForegroundColor Yellow }

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BackendBin  = Join-Path $ProjectRoot "backend\dist\backend\backend.exe"

if (-not (Test-Path $BackendBin)) {
    Write-Fail "Backend binary not found: $BackendBin"
}

$TmpDir = Join-Path $env:TEMP "smoke-backend-$(Get-Random)"
New-Item -ItemType Directory -Path $TmpDir -Force | Out-Null

$BackendProc = $null
$BlockerProc = $null

function Cleanup {
    if ($BackendProc -and -not $BackendProc.HasExited) {
        Stop-Process -Id $BackendProc.Id -Force -ErrorAction SilentlyContinue
    }
    if ($BlockerProc -and -not $BlockerProc.HasExited) {
        Stop-Process -Id $BlockerProc.Id -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -Recurse -Force $TmpDir -ErrorAction SilentlyContinue
}

try {
    # 1. Occupy the preferred port via temp .py file (avoids quoting issues)
    Write-Step "Occupying port $OccupiedPort ..."
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

    $BlockerProc = Start-Process -FilePath "python" -ArgumentList $blockerPy -PassThru -WindowStyle Hidden -RedirectStandardOutput "$TmpDir\blocker.out"

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
    if (-not $portReady) { Write-Fail "Port $OccupiedPort not occupied after 7s" }
    Write-Ok "Port $OccupiedPort occupied (PID $($BlockerProc.Id))"

    # 2. Start backend
    Write-Step "Starting backend ..."
    $env:DENTAL_AGENT_DATA_DIR = $TmpDir
    $logFile = Join-Path $TmpDir "backend-stderr.log"
    $BackendProc = Start-Process -FilePath $BackendBin -PassThru -WindowStyle Hidden -RedirectStandardError $logFile -RedirectStandardOutput "$TmpDir\backend-stdout.log"
    Remove-Item Env:\DENTAL_AGENT_DATA_DIR -ErrorAction SilentlyContinue
    Write-Ok "Backend started (PID $($BackendProc.Id))"

    # 3. Wait for port.json
    $PortJson = Join-Path $TmpDir "port.json"
    Write-Step "Waiting for port.json (up to 30s) ..."
    $found = $false
    for ($i = 0; $i -lt 60; $i++) {
        if (Test-Path $PortJson) { $found = $true; break }
        # Check if backend crashed
        if ($BackendProc.HasExited) {
            Write-Host "[debug] Backend exited early with code $($BackendProc.ExitCode)" -ForegroundColor Red
            if (Test-Path $logFile) {
                Write-Host "[debug] stderr:" -ForegroundColor Red
                Get-Content $logFile -Tail 20 | ForEach-Object { Write-Host "  $_" }
            }
            Write-Fail "Backend process exited before writing port.json"
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $found) {
        Write-Host "[debug] Backend still running but no port.json" -ForegroundColor Yellow
        if (Test-Path $logFile) {
            Write-Host "[debug] stderr:" -ForegroundColor Yellow
            Get-Content $logFile -Tail 20 | ForEach-Object { Write-Host "  $_" }
        }
        Write-Fail "port.json not found within 30s"
    }
    Write-Ok "port.json created"

    # 4. Verify port changed
    $info = Get-Content $PortJson -Raw | ConvertFrom-Json
    $actualPort = $info.port
    Write-Step "Actual port: $actualPort"
    if ($actualPort -eq $OccupiedPort) {
        Write-Fail "Port not changed, still $OccupiedPort"
    }
    Write-Ok "Port switched: $OccupiedPort -> $actualPort"

    # 5. Health check
    $healthUrl = "http://127.0.0.1:${actualPort}/api/health"
    Write-Step "Health check: $healthUrl ..."
    $ok = $false
    for ($i = 0; $i -lt 20; $i++) {
        try {
            $r = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 3
            if ($r.StatusCode -eq 200) { $ok = $true; break }
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ok) { Write-Fail "Health check failed" }
    Write-Ok "Health check passed"

    Write-Host ""
    Write-Host "=== Backend port collision smoke test PASSED ===" -ForegroundColor Green

} finally {
    Cleanup
}
