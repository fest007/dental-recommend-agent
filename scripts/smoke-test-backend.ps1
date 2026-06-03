# =============================================================================
# 后端端口碰撞 Smoke Test (Windows)
# 验证：首选端口被占用时，后端切到新端口、写出 port.json、健康检查通过
# 用法：从项目根目录运行，需要先完成 PyInstaller 打包
# =============================================================================

$ErrorActionPreference = "Stop"

function Write-Ok   { param($msg) Write-Host "✓ $msg" -ForegroundColor Green }
function Write-Fail { param($msg) Write-Host "✗ $msg" -ForegroundColor Red; exit 1 }
function Write-Step { param($msg) Write-Host "→ $msg" -ForegroundColor Yellow }

# 定位后端可执行文件
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BackendBin  = Join-Path $ProjectRoot "backend\dist\backend\backend.exe"

if (-not (Test-Path $BackendBin)) {
    Write-Fail "后端可执行文件不存在: $BackendBin（请先运行 pyinstaller）"
}

$OccupiedPort = 8765
$TmpDir       = Join-Path $env:TEMP "smoke-test-$(Get-Random)"
New-Item -ItemType Directory -Path $TmpDir -Force | Out-Null

$BackendProcess    = $null
$PortBlockerProcess = $null

function Cleanup {
    if ($BackendProcess -and -not $BackendProcess.HasExited) {
        Stop-Process -Id $BackendProcess.Id -Force -ErrorAction SilentlyContinue
    }
    if ($PortBlockerProcess -and -not $PortBlockerProcess.HasExited) {
        Stop-Process -Id $PortBlockerProcess.Id -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -Recurse -Force $TmpDir -ErrorAction SilentlyContinue
}

try {
    # ---------- 1. 占住首选端口 ----------
    Write-Step "占用端口 $OccupiedPort ..."

    $BlockerScript = @"
import socket, sys, time
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
s.bind(('127.0.0.1', $OccupiedPort))
s.listen(1)
sys.stdout.write('ready\n')
sys.stdout.flush()
time.sleep(120)
"@
    $PortBlockerProcess = Start-Process -FilePath "python" -ArgumentList "-c", $BlockerScript `
        -PassThru -WindowStyle Hidden -RedirectStandardOutput "$TmpDir\blocker.out"

    # 等待端口占用者就绪
    $ready = $false
    for ($i = 0; $i -lt 10; $i++) {
        try {
            $tcp = New-Object System.Net.Sockets.TcpClient
            $tcp.Connect("127.0.0.1", $OccupiedPort)
            $tcp.Close()
            $ready = $true
            break
        } catch {
            Start-Sleep -Milliseconds 300
        }
    }
    if (-not $ready) { Write-Fail "无法确认端口 $OccupiedPort 已被占用" }
    Write-Ok "端口 $OccupiedPort 已被占用 (PID $($PortBlockerProcess.Id))"

    # ---------- 2. 启动后端 ----------
    Write-Step "启动后端 ..."

    $env:DENTAL_AGENT_DATA_DIR = $TmpDir
    $BackendProcess = Start-Process -FilePath $BackendBin -PassThru -WindowStyle Hidden
    Remove-Item Env:\DENTAL_AGENT_DATA_DIR
    Write-Ok "后端已启动 (PID $($BackendProcess.Id))"

    # ---------- 3. 等待 port.json ----------
    $PortJson = Join-Path $TmpDir "port.json"
    Write-Step "等待 port.json 出现 ..."

    $found = $false
    for ($i = 0; $i -lt 30; $i++) {
        if (Test-Path $PortJson) { $found = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $found) { Write-Fail "port.json 未在 15 秒内出现" }
    Write-Ok "port.json 已生成"

    # ---------- 4. 验证端口 ----------
    $PortInfo    = Get-Content $PortJson -Raw | ConvertFrom-Json
    $ActualPort  = $PortInfo.port
    Write-Step "port.json 中的端口: $ActualPort"

    if ($ActualPort -eq $OccupiedPort) {
        Write-Fail "端口未切换: port.json 仍为 $OccupiedPort，应为其他端口"
    }
    Write-Ok "端口已切换: $OccupiedPort → $ActualPort"

    # ---------- 5. 健康检查 ----------
    $HealthUrl = "http://127.0.0.1:${ActualPort}/api/health"
    Write-Step "健康检查: $HealthUrl ..."

    $passed = $false
    for ($i = 0; $i -lt 20; $i++) {
        try {
            $response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -eq 200) {
                $body = $response.Content | ConvertFrom-Json
                if ($body.status -eq "ok") {
                    $passed = $true
                    break
                }
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }

    if (-not $passed) { Write-Fail "健康检查失败: $HealthUrl" }
    Write-Ok "健康检查通过: $HealthUrl → $($response.Content)"

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host " 后端端口碰撞 smoke test 全部通过" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green

} finally {
    Cleanup
}
